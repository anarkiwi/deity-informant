//! Init folding: the host calls an init entry once, first, on the loaded image, and every
//! other entry after it returns. Its run is the same on every input along its decided path
//! ([`crate::dispatch::Walk`]): from its entry while each byte it reads is a loaded byte or one
//! it stored earlier, each branch is decided and each store's address is known. So the program
//! starts from the memory that path leaves, and the folded entry starts where it ends, at its
//! entry height with every register it wrote known: it makes the stores of registers the path
//! never wrote (the entry's input, as a song number) and sets the registers it wrote. A byte
//! only the path writes is then a constant of the program, and code only the path runs is not
//! lifted. A volatile write of a known value is no memory: the folded entry makes the path's
//! volatile writes first, in order, and the path runs on past them.

use crate::analysis::{discover, Entry, Facts, Iv, Var};
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

/// One run of the path: the walk, for one value of the entry's `A` when it declares several.
struct Lane {
    walk: Walk,
    /// The volatile writes it made, in order.
    io: Vec<(u16, u8)>,
    /// Per byte stored outside the stack, its value (`None`: unknown).
    stored: BTreeMap<u16, Option<Val>>,
    /// The registers it wrote, and those it read before writing them, by `lift()` index.
    written: u32,
    used: u32,
    /// Its bytes stored outside the stack of unknown value, and whether it read a volatile
    /// address with effects or stored above the entry SP.
    unknown: usize,
    spoiled: bool,
    /// The bytes it stored, read from what it stored, never from the image.
    dirty: FxHashSet<u16>,
}

/// The path of an entry after `n` steps (or where it stops), run in lockstep once per value of
/// `A` it is declared with, and the last step count it could be folded at: every run on its
/// entry height, every register it wrote known, every byte it stored outside the stack known
/// or a register it never wrote, every write to `volatile` of a known value, no read of it but
/// a throw-away read reaching no `sensitive` address, no store above the entry SP, not at
/// another entry, and the runs joined: at one pc, with the same registers and volatile writes,
/// and each byte stored the same value in each, or each run's value of `A`.
struct Path {
    lanes: Vec<Lane>,
    /// The values of `A` the lanes run with (empty: one lane, `A` as declared).
    songs: Vec<u8>,
    /// The instructions the lanes ran, `pc: length`.
    ran: BTreeMap<u16, u8>,
    /// The bytes some lane stored whose values do not join.
    split: FxHashSet<u16>,
    best: Option<usize>,
}

impl Path {
    /// The value the lanes leave in byte `x`, joined: one constant, the entry's `A` (each
    /// lane's value of it, a function of the input on every declared value), or a register
    /// they all stored as they found it; None otherwise.
    fn joined(&self, img: &[u8; 0x10000], x: u16) -> Option<Val> {
        let vals: Vec<Option<Val>> = (self.lanes.iter())
            .map(|l| *l.stored.get(&x).unwrap_or(&Some(Val::K(img[x as usize]))))
            .collect();
        let first = vals[0]?;
        if vals.iter().all(|&v| v == Some(first)) {
            return Some(first);
        }
        let song = |(l, v): (usize, &Option<Val>)| *v == Some(Val::K(self.songs[l]));
        (!self.songs.is_empty() && vals.iter().enumerate().all(song)).then_some(Val::R(reg::A))
    }

    /// Whether the lanes may be folded where they are.
    fn foldable(&self, sp: u8, others: &[u16]) -> bool {
        let l0 = &self.lanes[0];
        let one = |l: &Lane| {
            !l.spoiled
                && l.unknown == 0
                && l.walk.rets.is_empty()
                && l.walk.env[reg::SP] == Iv::exact(sp as u32)
                && ARCH
                    .iter()
                    .all(|&r| l.written >> r & 1 == 0 || l.walk.env[r].value().is_some())
                && l.walk.pc == l0.walk.pc
                && l.io == l0.io
                && ARCH
                    .iter()
                    .all(|&r| l.written >> r & 1 == 0 || l.walk.env[r] == l0.walk.env[r])
        };
        self.split.is_empty() && self.lanes.iter().all(one) && !others.contains(&l0.walk.pc)
    }
}

#[allow(clippy::too_many_arguments)]
fn path(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    e: &Entry,
    sp: u8,
    loaded: (u32, u32),
    volatile: &[(u32, u32)],
    sensitive: Option<&[u16]>,
    others: &[u16],
    n: usize,
) -> Path {
    let songs: Vec<u8> = match e.regs.iter().find(|r| r.0 == reg::A) {
        Some((_, vs)) if vs.len() > 1 => vs.clone(),
        _ => Vec::new(),
    };
    let lane = |a: Option<u8>| {
        let mut walk = Walk::new(e, Some(sp));
        if let Some(a) = a {
            walk.env[reg::A] = Iv::exact(a as u32);
        }
        Lane {
            walk,
            io: Vec::new(),
            stored: BTreeMap::new(),
            written: 0,
            used: 0,
            unknown: 0,
            spoiled: false,
            dirty: FxHashSet::default(),
        }
    };
    let lanes = match songs.is_empty() {
        true => vec![lane(None)],
        false => songs.iter().map(|&a| lane(Some(a))).collect(),
    };
    let mut p = Path {
        lanes,
        songs,
        ran: BTreeMap::new(),
        split: FxHashSet::default(),
        best: None,
    };
    let mut read = Vec::new();
    'steps: for i in 0..=n.min(STEPS) {
        if i > 0 && p.foldable(sp, others) {
            p.best = Some(i);
        }
        if i == n {
            break;
        }
        let mut touched = Vec::new();
        for l in p.lanes.iter_mut() {
            let dirty = RefCell::new(std::mem::take(&mut l.dirty));
            let konst = |a: u32| {
                (loaded.0 <= a && a < loaded.1 && !dirty.borrow().contains(&(a as u16)))
                    .then(|| code.img[a as usize])
            };
            let stepped = l.walk.step(code, vars, &konst, &mut read);
            read.clear();
            let Some((op, acc, more)) = stepped else {
                break 'steps;
            };
            if acc.iter().any(|a| a.store && a.addr.value().is_none()) {
                break 'steps;
            }
            let t = Template::of(op);
            p.ran.insert(l.walk.at, t.len);
            for (ai, a) in acc.iter().enumerate() {
                if volatile.iter().any(|&(lo, hi)| a.addr.meets(lo, hi)) {
                    let felt = sensitive
                        .is_none_or(|s| s.iter().any(|&x| a.addr.meets(x as u32, x as u32 + 1)));
                    match (a.store, a.addr.value(), a.val.value()) {
                        (true, Some(x), Some(v)) => l.io.push((x as u16, v as u8)),
                        (true, ..) => l.spoiled = true,
                        (false, ..) => l.spoiled |= !a.dummy || felt,
                    }
                    continue;
                }
                if !a.store || a.dummy {
                    continue;
                }
                let x = a.addr.lo as u16;
                dirty.borrow_mut().insert(x);
                if (0x100..0x200).contains(&x) {
                    l.spoiled |= x > 0x100 + sp as u16;
                    continue;
                }
                let v = match (a.val.value(), stored_reg(t, ai)) {
                    (Some(v), _) => Some(Val::K(v as u8)),
                    (None, Some(r)) if l.written >> r & 1 == 0 => Some(Val::R(r)),
                    _ => None,
                };
                let was = matches!(l.stored.insert(x, v), Some(None));
                l.unknown = l.unknown + usize::from(v.is_none()) - usize::from(was);
                touched.push(x);
            }
            let bit = |v: &di_pcode::Vn| 1u32.checked_shl(v.off()).unwrap_or(0);
            for o in t.ops() {
                for v in o.inputs().iter().filter(|v| v.space() == Space::Reg) {
                    l.used |= bit(v) & !l.written;
                }
                if o.out.space() == Space::Reg {
                    l.written |= bit(&o.out);
                }
            }
            l.dirty = dirty.into_inner();
            if !more {
                break 'steps;
            }
        }
        if p.lanes.len() > 1 {
            for x in touched {
                match p.joined(code.img, x) {
                    Some(_) => p.split.remove(&x),
                    None => p.split.insert(x),
                };
            }
        }
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
    };
    let others: Vec<u16> = (entries.iter().enumerate())
        .filter(|&(i, _)| i != ix)
        .map(|(_, e)| e.pc)
        .collect();
    let e = &entries[ix];
    let run = |n| {
        path(
            &code, &g.vars, e, sp, loaded, volatile, sensitive, &others, n,
        )
    };
    let p = run(run(STEPS).best?);
    let l0 = &p.lanes[0];
    let mut out = Box::new(*img);
    let mut f = Fold {
        index: ix,
        entry: e.pc,
        pc: l0.walk.pc,
        io: l0.io.clone(),
        ..Fold::default()
    };
    let stored: std::collections::BTreeSet<u16> = p
        .lanes
        .iter()
        .flat_map(|l| l.stored.keys().copied())
        .collect();
    for &a in &stored {
        match p.joined(img, a) {
            Some(Val::K(x)) => {
                out[a as usize] = x;
                f.image.push((a, x));
            }
            Some(v) => f.pre.push((a, v)),
            None => unreachable!("a fold point stores no unknown byte"),
        }
    }
    f.code = (p.ran.iter())
        .filter(|e| !stored.contains(e.0))
        .map(|(&pc, &n)| (pc, n))
        .collect();
    let written = p.lanes.iter().fold(0, |m, l| m | l.written);
    let used = p.lanes.iter().fold(0, |m, l| m | l.used);
    f.set = (ARCH.iter())
        .filter(|&&r| written >> r & 1 == 1)
        .map(|&r| (r, l0.walk.env[r].lo as u8))
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
