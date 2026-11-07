//! Init folding: the host calls an init entry once, first, on the loaded image, and every
//! other entry after it returns. Its run is the same for every song along its decided path
//! ([`crate::dispatch::Walk`]): from its entry while each byte it reads is a loaded byte or one
//! it stored earlier, each branch is decided and each store's address is known, the decisions
//! taken without the entry's `A` (the song number) and without song data (the bytes the other
//! entries' code reads); where song data decides a branch, every path from it rejoins alike.
//! So the program starts from the memory that path leaves, and the folded entry starts where
//! it ends, at its entry height with every register it wrote known: it makes the stores of
//! registers the path never wrote (the entry's input, as a song number) and sets the registers
//! it wrote. A byte only the path writes is then a constant of the program, and code only the
//! path runs is not lifted. A volatile write of a known value is no memory: the folded entry
//! makes the path's volatile writes first, in order, and the path runs on past them.

use crate::analysis::{discover, Entry, Facts, Flow, Iv, Var};
use crate::dispatch::{stored_reg, Walk, STEPS};
use crate::vsa::Code;
use di_pcode::{reg, Space, Template};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::cell::RefCell;
use std::collections::BTreeMap;

pub use crate::analysis::Val;

/// The registers the folded entry hands the code it starts: `A`, `X`, `Y` and the flags.
const ARCH: [usize; 9] = [
    reg::A,
    reg::X,
    reg::Y,
    reg::C,
    reg::Z,
    reg::I,
    reg::D,
    reg::V,
    reg::N,
];

/// An init entry folded at the end of its decided path.
#[derive(Clone, Debug, Default)]
pub struct Fold {
    /// The entry's index and its pc.
    pub index: usize,
    pub entry: u16,
    /// Where the folded entry starts.
    pub pc: u16,
    /// The bytes the path leaves: the program's starting memory over the image.
    pub image: Vec<(u16, u8)>,
    /// The volatile writes the folded entry makes, in order, the stores it makes and the
    /// registers it sets.
    pub io: Vec<(u16, u8)>,
    pub pre: Vec<(u16, Val)>,
    pub set: Vec<(usize, u8)>,
    /// The path's instructions `(pc, length)` whose opcode it does not store: code the
    /// program ran, constant where the folded program writes none of their bytes.
    pub code: Vec<(u16, u8)>,
}

/// What every run of the path reads: the program, the entry, its height, the loaded and
/// volatile ranges, the volatile addresses whose reads have effects (none: all), the other
/// entries, the instructions they reach and the constant code.
struct Ctx<'a> {
    code: &'a Code<'a>,
    vars: &'a FxHashMap<u16, Vec<Var>>,
    sp: u8,
    loaded: (u32, u32),
    volatile: &'a [(u32, u32)],
    sensitive: Option<&'a [u16]>,
    others: &'a [u16],
    play: FxHashSet<u16>,
    constcode: FixedBitSet,
}

impl Ctx<'_> {
    /// Whether run `l` is at a return with no call open: the entry's own.
    fn returns(&self, l: &Lane) -> bool {
        l.walk.rets.is_empty()
            && (self.vars.get(&l.walk.pc).into_iter().flatten())
                .all(|v| matches!(v.flow, Flow::Rts(_)))
    }
}

/// One run of the path, with the entry's `A` (the song number) unknown. The values run
/// reads every loaded byte; the decisions run also takes as unknown the loaded bytes the code
/// the other entries run reads (song data), so what it decides is decided for every song.
#[derive(Clone)]
struct Lane {
    walk: Walk,
    decisions: bool,
    /// The volatile writes it made, in order.
    io: Vec<(u16, u8)>,
    /// Per byte stored outside the stack, its value (`None`: unknown).
    stored: BTreeMap<u16, Option<Val>>,
    /// The registers it wrote, and those it read before writing them, by `lift()` index.
    written: u32,
    used: u32,
    /// Its bytes of unknown value stored outside the stack (the decisions run's: those in
    /// constant code, which the folded program would take as song data), and whether it read
    /// a volatile address with effects or stored above the entry SP.
    unknown: usize,
    spoiled: bool,
    /// The bytes it stored, read from what it stored, never from the image.
    dirty: FxHashSet<u16>,
}

impl Lane {
    fn new(e: &Entry, sp: u8, decisions: bool) -> Self {
        let mut walk = Walk::new(e, Some(sp));
        walk.env[reg::A] = crate::analysis::TOP_ENV[reg::A];
        Lane {
            walk,
            decisions,
            io: Vec::new(),
            stored: BTreeMap::new(),
            written: 0,
            used: 0,
            unknown: 0,
            spoiled: false,
            dirty: FxHashSet::default(),
        }
    }

    /// Whether the run counts byte `x` stored with an unknown value against folding.
    fn counts(&self, c: &Ctx, x: u16) -> bool {
        !self.decisions || c.constcode.contains(x as usize)
    }

    /// Run one instruction, adding it to `ran`: whether its successor is decided, None where
    /// the path stops (no single variant, or a store of unknown address).
    fn advance(&mut self, c: &Ctx, ran: &mut BTreeMap<u16, u8>) -> Option<bool> {
        let song = self.decisions && c.play.contains(&self.walk.pc);
        let dirty = RefCell::new(std::mem::take(&mut self.dirty));
        let konst = |a: u32| {
            (c.loaded.0 <= a
                && a < c.loaded.1
                && !dirty.borrow().contains(&(a as u16))
                && (!song || c.constcode.contains(a as usize)))
            .then(|| c.code.img[a as usize])
        };
        let mut read = Vec::new();
        let stepped = self.walk.step(c.code, c.vars, &konst, &mut read);
        let (op, acc, more) = stepped?;
        if acc.iter().any(|a| a.store && a.addr.value().is_none()) {
            return None;
        }
        let t = Template::of(op);
        ran.insert(self.walk.at, t.len);
        for (ai, a) in acc.iter().enumerate() {
            if c.volatile.iter().any(|&(lo, hi)| a.addr.meets(lo, hi)) {
                let felt = (c.sensitive)
                    .is_none_or(|s| s.iter().any(|&x| a.addr.meets(x as u32, x as u32 + 1)));
                match (a.store, a.addr.value(), a.val.value()) {
                    (true, Some(x), Some(v)) => self.io.push((x as u16, v as u8)),
                    (true, ..) => self.spoiled = true,
                    (false, ..) => self.spoiled |= !a.dummy || felt,
                }
                continue;
            }
            if !a.store || a.dummy {
                continue;
            }
            let x = a.addr.lo as u16;
            dirty.borrow_mut().insert(x);
            if (0x100..0x200).contains(&x) {
                self.spoiled |= x > 0x100 + c.sp as u16;
                continue;
            }
            let v = match (a.val.value(), stored_reg(t, ai)) {
                (Some(v), _) => Some(Val::K(v as u8)),
                (None, Some(r)) if self.written >> r & 1 == 0 => Some(Val::R(r)),
                _ => None,
            };
            let counted = self.counts(c, x);
            let was = matches!(self.stored.insert(x, v), Some(None)) && counted;
            self.unknown = self.unknown + usize::from(v.is_none() && counted) - usize::from(was);
        }
        let bit = |v: &di_pcode::Vn| 1u32.checked_shl(v.off()).unwrap_or(0);
        for o in t.ops() {
            for v in o.inputs().iter().filter(|v| v.space() == Space::Reg) {
                self.used |= bit(v) & !self.written;
            }
            if o.out.space() == Space::Reg {
                self.written |= bit(&o.out);
            }
        }
        self.dirty = dirty.into_inner();
        Some(more)
    }

    /// Join run `o` at the same instruction into this one: None where their open calls or
    /// volatile writes differ, else whether this run changed. A byte one stored and the other
    /// did not, or stored otherwise, is unknown.
    fn join(&mut self, o: &Lane, c: &Ctx) -> Option<bool> {
        if self.io != o.io {
            return None;
        }
        let mut changed = self.walk.join(&o.walk)?;
        let keys: Vec<u16> = self.stored.keys().chain(o.stored.keys()).copied().collect();
        for x in keys {
            let (a, b) = (self.stored.get(&x).copied(), o.stored.get(&x).copied());
            if a != b && a != Some(None) {
                self.stored.insert(x, None);
                self.unknown += usize::from(self.counts(c, x));
                changed = true;
            }
        }
        let (d, w, u, s) = (self.dirty.len(), self.written, self.used, self.spoiled);
        self.dirty.extend(&o.dirty);
        self.written |= o.written;
        self.used |= o.used;
        self.spoiled |= o.spoiled;
        let more = (d, w, u, s) != (self.dirty.len(), self.written, self.used, self.spoiled);
        Some(changed || more)
    }
}

/// The instruction starts the entries `roots` reach in `vars`, through calls and returns.
fn reached(vars: &FxHashMap<u16, Vec<Var>>, roots: &[u16]) -> FxHashSet<u16> {
    let mut seen = FxHashSet::default();
    let mut work = roots.to_vec();
    while let Some(pc) = work.pop() {
        if seen.insert(pc) {
            for v in vars.get(&pc).into_iter().flatten() {
                work.extend(crate::analysis::flow_succs(&v.flow));
            }
        }
    }
    seen
}

/// The path of an entry after `n` steps (or where it stops), its values and decisions runs
/// in lockstep, and the last step count it could be folded at: both runs on its entry height,
/// every register they wrote known (but at the entry's return, where they are its result), every
/// byte stored outside the stack known (or a register never written, or song data outside
/// constant code), every write to `volatile` of a known value, no read of it but a throw-away
/// read reaching no `sensitive` address, no store above the entry SP, not at another entry,
/// and the runs at one pc with the same volatile writes.
struct Path {
    lanes: [Lane; 2],
    /// The instructions the decisions run ran, every path it took.
    ran: BTreeMap<u16, u8>,
    best: Option<usize>,
}

/// Whether runs `lanes` may be folded where they are.
fn foldable(c: &Ctx, lanes: &[Lane; 2]) -> bool {
    let l0 = &lanes[0];
    let done = c.returns(l0);
    let one = |l: &Lane| {
        !l.spoiled
            && l.unknown == 0
            && l.walk.rets.is_empty()
            && l.walk.env[reg::SP] == Iv::exact(c.sp as u32)
            && ARCH.iter().all(|&r| {
                l.written >> r & 1 == 0
                    || l.walk.env[r].value().is_some() && l.walk.env[r] == l0.walk.env[r]
                    || done
            })
            && l.walk.pc == l0.walk.pc
            && l.io == l0.io
    };
    lanes.iter().all(one) && !c.others.contains(&l0.walk.pc)
}

/// The nearest instruction every path from branch `b` reaches in its function, calls run
/// through to their return sites: its immediate postdominator, None for the function's return.
fn rejoin(vars: &FxHashMap<u16, Vec<Var>>, b: u16) -> Option<u16> {
    let succs = |pc: u16| -> Vec<Option<u16>> {
        let mut out = Vec::new();
        for v in vars.get(&pc).into_iter().flatten() {
            match &v.flow {
                Flow::Next(n) => out.push(Some(*n)),
                Flow::Br { t, f } => out.extend(t.all().iter().chain([f]).map(|&x| Some(x))),
                Flow::Jmp(t) => out.extend(t.all().iter().map(|&x| Some(x))),
                Flow::Jsr { ret, .. } => out.push(Some(*ret)),
                _ => out.push(None),
            }
        }
        out
    };
    let mut nodes = vec![b];
    let mut index: FxHashMap<u16, usize> = FxHashMap::from_iter([(b, 0)]);
    let mut edges: Vec<Vec<usize>> = Vec::new();
    while edges.len() < nodes.len() {
        let pc = nodes[edges.len()];
        let mut out = Vec::new();
        for s in succs(pc) {
            let Some(s) = s else {
                out.push(usize::MAX);
                continue;
            };
            let k = *index.entry(s).or_insert_with(|| {
                nodes.push(s);
                nodes.len() - 1
            });
            out.push(k);
        }
        edges.push(out);
    }
    // Postdominator sets, the function's return as node `n`, iterated down from all nodes.
    let n = nodes.len();
    let mut all = FixedBitSet::with_capacity(n + 1);
    all.insert_range(..);
    let mut pdom = vec![all; n];
    let mut exit = FixedBitSet::with_capacity(n + 1);
    exit.insert(n);
    loop {
        let mut changed = false;
        for i in (0..n).rev() {
            let mut m = FixedBitSet::with_capacity(n + 1);
            m.insert_range(..);
            for &s in &edges[i] {
                m.intersect_with(if s == usize::MAX { &exit } else { &pdom[s] });
            }
            if edges[i].is_empty() {
                m.clear();
            }
            m.insert(i);
            if m != pdom[i] {
                pdom[i] = m;
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    (pdom[0].ones())
        .filter(|&d| d != 0 && d < n)
        .max_by_key(|&d| pdom[d].count_ones(..))
        .map(|d| nodes[d])
}

/// The decisions run `start` past a branch it cannot decide: every path from it, joined per
/// instruction and open calls, to where they rejoin ([`rejoin`]; for the function's return,
/// the return site, or at the entry's own return), the instructions it runs added to `ran`.
/// The joined run there when every path reaches it and none stops the path (no single
/// variant, a store of unknown address, a jump other than a branch it cannot decide, open
/// calls or volatile writes that differ where paths meet).
fn explore(c: &Ctx, start: &Lane, ran: &mut BTreeMap<u16, u8>) -> Option<Lane> {
    type Key = (u16, Vec<(u16, u32)>);
    let rets = &start.walk.rets;
    let end: Option<Key> = match (rejoin(c.vars, start.walk.pc), rets.split_last()) {
        (Some(pc), _) => Some((pc, rets.clone())),
        (None, Some((r, up))) => Some((r.0, up.to_vec())),
        (None, None) => None,
    };
    let at_end = |l: &Lane| match &end {
        Some((pc, r)) => l.walk.pc == *pc && l.walk.rets == *r,
        None => c.returns(l),
    };
    let mut at: FxHashMap<Key, Lane> = FxHashMap::default();
    let first: Key = (start.walk.pc, rets.clone());
    at.insert(first.clone(), start.clone());
    let (mut work, mut exit, mut steps) = (vec![first], None::<Lane>, 0);
    while let Some(key) = work.pop() {
        steps += 1;
        if steps > STEPS {
            return None;
        }
        let mut l = at[&key].clone();
        if steps > 1 && at_end(&l) {
            match exit.as_mut() {
                Some(e) => {
                    e.join(&l, c)?;
                }
                None => exit = Some(l),
            }
            continue;
        }
        if c.returns(&l) {
            return None;
        }
        let succ = match l.advance(c, ran)? {
            true => vec![l.walk.pc],
            false => match &c.vars[&l.walk.at][0].flow {
                Flow::Br { t, f } if l.walk.pc == l.walk.at => match t.all() {
                    [t] => vec![*t, *f],
                    _ => return None,
                },
                _ => return None,
            },
        };
        for s in succ {
            let mut n = l.clone();
            n.walk.pc = s;
            let k: Key = (s, n.walk.rets.clone());
            let grew = match at.get_mut(&k) {
                Some(o) => o.join(&n, c)?,
                None => at.insert(k.clone(), n).is_none(),
            };
            if grew {
                work.push(k);
            }
        }
    }
    exit
}

/// The path of the init entry `e`, its values run's first `n` steps at most ([`Path`]).
/// Where the decisions run cannot decide a branch the values run decides, the path goes on
/// where every path from it rejoins alike ([`explore`]): the values run runs there alone.
fn path(c: &Ctx, e: &Entry, n: usize) -> Path {
    let mut p = Path {
        lanes: [Lane::new(e, c.sp, false), Lane::new(e, c.sp, true)],
        ran: BTreeMap::new(),
        best: None,
    };
    let (mut spare, mut i, n) = (BTreeMap::new(), 0, n.min(STEPS));
    loop {
        if i > 0 && foldable(c, &p.lanes) {
            p.best = Some(i);
        }
        if i == n {
            break;
        }
        let [v, d] = &mut p.lanes;
        let start = d.clone();
        let (Some(vm), Some(dm)) = (v.advance(c, &mut spare), d.advance(c, &mut p.ran)) else {
            break;
        };
        i += 1;
        if vm && dm {
            continue;
        }
        let branch = vm && !dm && matches!(c.vars[&d.walk.at][0].flow, Flow::Br { .. });
        let Some(end) = branch.then(|| explore(c, &start, &mut p.ran)).flatten() else {
            break;
        };
        while (v.walk.pc, &v.walk.rets) != (end.walk.pc, &end.walk.rets) && i < n {
            if v.advance(c, &mut spare) != Some(true) {
                return p;
            }
            i += 1;
        }
        if (v.walk.pc, &v.walk.rets) != (end.walk.pc, &end.walk.rets) {
            break;
        }
        *d = end;
    }
    p
}

/// The folded program for init entry `ix` of `entries` in a static lift (`loaded`, entry SP
/// `sp`, the volatile addresses whose reads have effects `sensitive`, none: all): its
/// starting memory, its entries and the fold, or `None` when the path folds nothing.
pub fn prepare(
    img: &[u8; 0x10000],
    entries: &[Entry],
    ix: usize,
    volatile: &[(u32, u32)],
    sensitive: Option<&[u16]>,
    loaded: (u32, u32),
    sp: u8,
) -> Option<(Box<[u8; 0x10000]>, Vec<Entry>, Fold)> {
    let g = discover(
        img,
        entries,
        &Facts::default(),
        None,
        true,
        Some(loaded),
        Some(sp),
        None,
    );
    let code = Code {
        img: &g.img,
        cells: &g.cells,
        pull_clear: g.pull_clear,
        vecs: Vec::new(),
        observed: FixedBitSet::with_capacity(0x10000),
        constant: FixedBitSet::with_capacity(0x10000),
        clip: FxHashMap::default(),
        bound: Vec::new(),
        stored: Vec::new(),
        after: Vec::new(),
        init: 0,
        floor: FxHashMap::default(),
        runs: Vec::new(),
        words: FxHashMap::default(),
    };
    let others: Vec<u16> = (entries.iter().enumerate())
        .filter(|&(i, _)| i != ix)
        .map(|(_, e)| e.pc)
        .collect();
    let e = &entries[ix];
    let mut constcode = g.code.clone();
    constcode.difference_with(&g.cells);
    let c = Ctx {
        code: &code,
        vars: &g.vars,
        sp,
        loaded,
        volatile,
        sensitive,
        others: &others,
        play: reached(&g.vars, &others),
        constcode,
    };
    let p = path(&c, e, path(&c, e, STEPS).best?);
    let (l0, l1) = (&p.lanes[0], &p.lanes[1]);
    let mut out = Box::new(*img);
    let mut f = Fold {
        index: ix,
        entry: e.pc,
        pc: l0.walk.pc,
        io: l0.io.clone(),
        ..Fold::default()
    };
    // The bytes some path stores, with the values the path the program takes leaves.
    for &a in l1.stored.keys() {
        match l0
            .stored
            .get(&a)
            .copied()
            .unwrap_or(Some(Val::K(img[a as usize])))
        {
            Some(Val::K(x)) => {
                out[a as usize] = x;
                f.image.push((a, x));
            }
            Some(v) => f.pre.push((a, v)),
            None => unreachable!("a fold point stores no unknown byte"),
        }
    }
    let stored = &l1.stored;
    f.code = (p.ran.iter())
        .filter(|e| !stored.contains_key(e.0))
        .map(|(&pc, &n)| (pc, n))
        .collect();
    let written = p.lanes.iter().fold(0, |m, l| m | l.written);
    let used = p.lanes.iter().fold(0, |m, l| m | l.used);
    f.set = (ARCH.iter())
        .filter_map(|&r| {
            Some((
                r,
                l0.walk.env[r].value().filter(|_| written >> r & 1 == 1)? as u8,
            ))
        })
        .collect();
    let mut es = entries.to_vec();
    es[ix] = Entry {
        pc: f.pc,
        regs: (e.regs.iter())
            .filter(|(r, _)| (used | !written) >> r & 1 == 1)
            .cloned()
            .collect(),
        set: f.set.clone(),
        pre: f.pre.clone(),
        io: f.io.clone(),
    };
    Some((out, es, f))
}

impl Fold {
    /// Keep the path's instructions no byte of which the folded program `g` (entries
    /// `entries`) writes, and give their bytes.
    pub(crate) fn settle(&mut self, g: &crate::analysis::Graph, entries: &[Entry]) -> FixedBitSet {
        let mut moved = g.cells.clone();
        moved.union_with(&g.written);
        moved.extend(crate::analysis::pre_stored(entries).map(|a| a as usize));
        let bytes = |&(pc, n): &(u16, u8)| (0..n as u16).map(move |i| pc.wrapping_add(i) as usize);
        self.code.retain(|c| bytes(c).all(|a| !moved.contains(a)));
        let mut out = FixedBitSet::with_capacity(0x10000);
        out.extend(self.code.iter().flat_map(bytes));
        out
    }
}
