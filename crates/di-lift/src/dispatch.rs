//! What the interval analysis tells about computed transfers and stores, from the code alone.
//!
//! **Index sets.** The values an index register may hold at an instruction: the union over
//! its call contexts of its interval cut by its known bits there, cut by the progression
//! `start + k * step` (`k` below the trip bound) of every loop counter it is
//! ([`crate::scev`]). An index whose interval is every byte and that no trip bound limits is
//! unbounded: known bits refine an index set, they do not bound it.
//!
//! **Stores.** A store whose address is its operand plus index registers has, as addresses,
//! the address for each value of their index sets (each evaluated exactly); any other (through
//! a pointer, a cell operand or an unbounded index) is unknown. A stored register's values are
//! those of the definitions reaching the store (each instruction writing it on a path to the
//! store with no other between: the values its interval and known bits allow after it), so
//! paths that load different constants keep them apart.
//!
//! **Computed jumps.** The analysis states tag a byte loaded from a constant base plus an
//! index register with the load, through register copies, pushes and stores to `JMP (ind)`
//! pointers, and whether its index is still the register's value; where the register is
//! written, the bytes it indexed become a group sharing one index value. A `JMP (ind)` whose
//! pointer bytes hold such tags (or constants) on every path, and an `RTS`/`RTI` whose return
//! address its function pushed so, goes to the words the loads read for each index value:
//! two bytes indexed by the register's current value, or of one group, pair up value by
//! value, a constant pairs with every byte. A `JMP (ind)` through a pointer in the code span
//! that no store may write goes to the pointer's word. The bytes read must be ones no store
//! may write; otherwise, or when a byte is not tagged on some path, an index is unbounded or
//! two bytes are indexed by unrelated values, the transfer is not resolved.

use crate::analysis::{absint, operand_bytes, Flow, Iv, Tg, Var, TOP_ENV};
use crate::scev::{Induction, Place};
use crate::vsa::{Code, Ix, Key, St, Tab, NOREG};
use di_pcode::{reg, Opc, Space, Template};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::BTreeSet;

/// A store of the template op `ai` (its access index) of opcode `op` at `pc`: its interval,
/// its addresses (`None`: unknown) and values (`None`: any).
#[derive(Clone, Debug)]
pub struct Store {
    pub pc: u16,
    pub op: u8,
    pub ai: usize,
    pub addr: Iv,
    pub addrs: Option<Vec<u16>>,
    pub vals: Option<BTreeSet<u8>>,
    /// Cells whose values the store also writes (a byte loaded from one and stored).
    pub deps: Vec<u16>,
}

/// An index guard: the instruction at `pc` deopts before it unless index register `reg` is in
/// `ok` (a table read outside its extent).
pub type Guard = (u16, u8, Iv);

/// A computed transfer's targets and the bytes they were read from, or that some path leaves
/// it unresolved.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Jump {
    Targets(Vec<u16>, Vec<u16>, Vec<Guard>),
    Fail,
}

/// The analysis results index and value sets come from: the states of every context per
/// instruction start, their join, the loops' induction variables, the predecessors of each
/// start and the entries.
pub(crate) struct Sets<'a> {
    pub(crate) guards: &'a FxHashMap<u16, (u8, Iv)>,
    pub(crate) states: FxHashMap<u16, Vec<&'a St>>,
    pub(crate) merged: &'a FxHashMap<u16, St>,
    pub(crate) ind: &'a [Induction],
    pub(crate) preds: FxHashMap<u16, Vec<u16>>,
    pub(crate) roots: &'a [u16],
}

impl Sets<'_> {
    /// The values data register `r` may hold at `pc`; `None` (unbounded) when neither its
    /// interval nor a loop's trip bound limits it.
    /// The values data register `r` may hold at `pc`, bounded or not: its intervals cut by
    /// its known bits and its guard.
    pub(crate) fn any(&self, pc: u16, r: usize) -> Vec<u8> {
        let none = Vec::new();
        let mut ok = [false; 0x100];
        for s in self.states.get(&pc).unwrap_or(&none) {
            for x in s.kb[r].values(s.env[r]) {
                ok[x as usize] = true;
            }
        }
        let g = self.guards.get(&pc).filter(|g| g.0 as usize == r);
        (0..0x100)
            .filter(|&x| ok[x] && g.is_none_or(|g| (g.1.lo..=g.1.hi).contains(&(x as u32))))
            .map(|x| x as u8)
            .collect()
    }
    pub(crate) fn of(&self, pc: u16, r: usize) -> Option<Vec<u8>> {
        let none = Vec::new();
        let states = self.states.get(&pc).unwrap_or(&none);
        let mut ok = [false; 0x100];
        for s in states {
            for x in s.kb[r].values(s.env[r]) {
                ok[x as usize] = true;
            }
        }
        let mut bounded = states.iter().all(|s| s.env[r] != Iv::full(1));
        for i in self.ind.iter().filter(|i| i.var == Place::Reg(r as u8)) {
            let (Some(t), Some(&e)) = (i.trips, i.at.get(&pc)) else {
                continue;
            };
            bounded = true;
            let mut on = [false; 0x100];
            for s0 in i.start.lo..=i.start.hi {
                for k in 0..t {
                    on[((s0 + e as u32 + k * i.step as u32) & 0xff) as usize] = true;
                }
            }
            for (a, b) in ok.iter_mut().zip(on) {
                *a &= b;
            }
        }
        if let Some(&(_, g)) = self.guards.get(&pc).filter(|g| g.0 as usize == r) {
            bounded = true;
            for (x, o) in ok.iter_mut().enumerate() {
                *o &= (g.lo..=g.hi).contains(&(x as u32));
            }
        }
        bounded.then(|| (0..0x100).filter(|&x| ok[x]).map(|x| x as u8).collect())
    }
}

/// Where tables end: the least address each base the code accesses (a constant operand,
/// indexed or not) is accessed at, the instruction bytes and the end of the loaded program.
pub(crate) struct Objects {
    starts: Vec<(u32, u16)>,
    pub(crate) code: Vec<u32>,
    end: u32,
}

impl Objects {
    pub(crate) fn new(
        code: &Code,
        vars: &FxHashMap<u16, Vec<Var>>,
        merged: &FxHashMap<u16, St>,
        bytes: &FixedBitSet,
        loaded: Option<(u32, u32)>,
    ) -> Objects {
        let mut first: FxHashMap<u16, u32> = FxHashMap::default();
        let mut acc = Vec::new();
        let at = |acc: &mut Vec<_>, t: &Template, b, r: Option<usize>, x: u32| {
            let mut e = TOP_ENV;
            e[reg::X] = Iv::exact(0);
            e[reg::Y] = Iv::exact(0);
            if let Some(r) = r {
                e[r] = Iv::exact(x);
            }
            acc.clear();
            absint(t.ops(), b, &mut e, acc);
        };
        for (&pc, vs) in vars {
            let env = merged.get(&pc).map_or(TOP_ENV, |s| s.env);
            for v in vs {
                let t = Template::of(v.op);
                let b = operand_bytes(code.img, code.cells, pc, t.len);
                let r = [reg::X, reg::Y].into_iter().find(|&r| moves(t, b, r));
                let lo = r.map_or(0, |r| env[r].lo);
                at(&mut acc, t, b, r, 0);
                let base: Vec<Option<u32>> = acc
                    .iter()
                    .map(|a| a.addr.value().filter(|_| !a.dummy))
                    .collect();
                at(&mut acc, t, b, r, lo);
                for (bs, a) in base.into_iter().zip(&acc) {
                    if let (Some(bs), Some(st)) = (bs, a.addr.value()) {
                        let e = first.entry(bs as u16).or_insert(st);
                        *e = (*e).min(st);
                    }
                }
            }
        }
        let mut starts: Vec<(u32, u16)> = first.into_iter().map(|(b, s)| (s, b)).collect();
        starts.sort_unstable();
        Objects {
            starts,
            code: bytes.ones().map(|a| a as u32).collect(),
            end: loaded.map_or(0x10000, |l| l.1),
        }
    }
    /// The first address from `s` on that holds an instruction byte or lies past the loaded
    /// program, or past `s` at which the code starts accessing a base other than `bases`.
    fn end(&self, s: u32, bases: &[u16]) -> u32 {
        let i = self.starts.partition_point(|e| e.0 <= s);
        let o = self.starts[i..]
            .iter()
            .find(|e| !bases.contains(&e.1))
            .map_or(0x10000, |e| e.0);
        let c = self.code[self.code.partition_point(|&a| a < s)..]
            .first()
            .map_or(0x10000, |&a| a);
        o.min(c).min(self.end.max(s))
    }
    /// How many of the index values `xs` (ascending) read every column `bases` inside its
    /// extent: from its first byte to its end ([`Objects::end`], the other columns not
    /// counted), and no byte another column read at an earlier value. A prefix of `xs`.
    fn valid(&self, bases: &[u16], xs: &[u8]) -> usize {
        let Some(&x0) = xs.first() else { return 0 };
        let ends: Vec<u32> = bases
            .iter()
            .map(|&b| self.end(b as u32 + x0 as u32, bases))
            .collect();
        let mut claimed: FxHashMap<u32, usize> = FxHashMap::default();
        for (n, &x) in xs.iter().enumerate() {
            let at: Vec<u32> = bases.iter().map(|&b| b as u32 + x as u32).collect();
            let bad = at.iter().enumerate().any(|(j, &a)| {
                a >= ends[j] || claimed.get(&a).is_some_and(|&c| bases[c] != bases[j])
            });
            if bad {
                return n;
            }
            for (j, a) in at.into_iter().enumerate() {
                claimed.insert(a, j);
            }
        }
        xs.len()
    }
}

/// Whether register `r` moves an address of template `t` (operand bytes `b`).
fn moves(t: &Template, b: [Option<u8>; 2], r: usize) -> bool {
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    let mut acc = Vec::new();
    absint(t.ops(), b, &mut env, &mut acc);
    let fixed: Vec<bool> = acc.iter().map(|a| a.addr.value().is_some()).collect();
    env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    env[r] = Iv::full(1);
    acc.clear();
    absint(t.ops(), b, &mut env, &mut acc);
    acc.iter()
        .zip(fixed)
        .any(|(a, f)| f && !a.dummy && a.addr.value().is_none())
}

/// The values data register `r` holds at `pc`, from the definitions reaching it; `None` when
/// an entry reaches it without one or a definition may leave any byte.
fn reaching(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    pc: u16,
    r: usize,
) -> Option<BTreeSet<u8>> {
    let mut out = BTreeSet::new();
    let mut seen: FxHashSet<u16> = FxHashSet::default();
    let mut work = vec![pc];
    let writes = |op: u8| {
        Template::of(op)
            .ops()
            .iter()
            .any(|o| o.out.space() == Space::Reg && o.out.off() as usize == r)
    };
    while let Some(p) = work.pop() {
        if sets.roots.contains(&p) {
            return None;
        }
        for &q in sets.preds.get(&p).into_iter().flatten() {
            if !seen.insert(q) {
                continue;
            }
            let st = sets.merged.get(&q)?;
            for v in &vars[&q] {
                if !writes(v.op) {
                    work.push(q);
                    continue;
                }
                let s = crate::vsa::step(code, q, v, st, &mut Vec::new());
                if s.env[r] == Iv::full(1) {
                    return None;
                }
                out.extend(s.kb[r].values(s.env[r]).map(|x| x as u8));
            }
        }
    }
    Some(out)
}

/// Whether index register `r` moves a store address of template `t` (operand bytes `b`).
fn addresses(t: &Template, b: [Option<u8>; 2], r: usize) -> bool {
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    env[r] = Iv::full(1);
    let mut acc = Vec::new();
    absint(t.ops(), b, &mut env, &mut acc);
    acc.iter().any(|a| a.store && a.addr.value().is_none())
}

/// The data register access `i` of template `t` stores, when it stores one as the
/// instruction found it.
fn stored_reg(t: &Template, i: usize) -> Option<usize> {
    let mut written = 0u32;
    let mut n = 0;
    for o in t.ops() {
        if matches!(o.opc, Opc::Load | Opc::Store) {
            if n == i {
                let v = o.ins[1];
                return (o.opc == Opc::Store
                    && v.space() == Space::Reg
                    && (v.off() as usize) < 3
                    && written >> v.off() & 1 == 0)
                    .then_some(v.off() as usize);
            }
            n += 1;
        }
        if o.out.space() == Space::Reg {
            written |= 1 << o.out.off();
        }
    }
    None
}

/// Whether template `t` reads register `r`.
fn reads(t: &Template, r: usize) -> bool {
    t.ops().iter().any(|o| {
        o.inputs()
            .iter()
            .any(|v| v.space() == Space::Reg && v.off() as usize == r)
    })
}

/// The index register (`X` before `Y`) template `t` reads, if any.
pub(crate) fn index_reg(t: &Template) -> Option<usize> {
    [reg::X, reg::Y].into_iter().find(|&r| reads(t, r))
}

/// The address the one data load of opcode `op` at `pc` reads with index register `r`
/// ([`NOREG`]: none) holding `x`, when that fixes it.
pub(crate) fn load_addr(code: &Code, pc: u16, op: u8, r: u8, x: u8) -> Option<u16> {
    let t = Template::of(op);
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    if r != NOREG {
        env[r as usize] = Iv::exact(x as u32);
    }
    let mut acc = Vec::new();
    absint(
        t.ops(),
        operand_bytes(code.img, code.cells, pc, t.len),
        &mut env,
        &mut acc,
    );
    let [l] = acc.iter().filter(|a| !a.dummy).collect::<Vec<_>>()[..] else {
        return None;
    };
    l.addr.value().map(|a| a as u16)
}

/// The pointer bytes of a `JMP (ind)` with a constant operand at `pc` (the high byte from the
/// pointer's own page).
fn pointer(code: &Code, pc: u16) -> Option<(u16, u16)> {
    let [Some(lo), Some(hi)] = operand_bytes(code.img, code.cells, pc, 3) else {
        return None;
    };
    let w = u16::from_le_bytes([lo, hi]);
    Some((w, (w & 0xff00) | (w.wrapping_add(1) & 0xff)))
}

/// Whether `f` is a `JMP`/`JSR` through a cell operand.
pub(crate) fn operand_jump(f: &Flow) -> bool {
    matches!(f, Flow::Jmp(Tg::Calc(_)) | Flow::Jsr { t: Tg::Calc(_), .. })
}

/// The bytes whose contents the analysis tracks: the pointers of `JMP (ind)`s and the
/// operands of `JMP`/`JSR`s through cells.
pub(crate) fn vectors(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    vars: &FxHashMap<u16, Vec<Var>>,
) -> Vec<u16> {
    let code = Code {
        img,
        cells,
        pull_clear: false,
        vecs: Vec::new(),
        guards: FxHashMap::default(),
        observed: FixedBitSet::new(),
    };
    let mut v: Vec<u16> = vars
        .iter()
        .filter(|(_, vs)| vs.iter().any(|v| matches!(v.flow, Flow::JmpInd(_))))
        .filter_map(|(&pc, _)| pointer(&code, pc))
        .flat_map(|(lo, hi)| [lo, hi])
        .collect();
    v.extend(
        vars.iter()
            .filter(|(_, vs)| vs.iter().any(|v| operand_jump(&v.flow)))
            .flat_map(|(&pc, _)| [pc.wrapping_add(1), pc.wrapping_add(2)]),
    );
    v.sort_unstable();
    v.dedup();
    v
}

/// A store's addresses and values so far (`None`: unknown).
type Seen = (Option<BTreeSet<u16>>, Option<BTreeSet<u8>>);

/// Every store of `vars`, sorted by instruction and access.
pub(crate) fn stores(code: &Code, vars: &FxHashMap<u16, Vec<Var>>, sets: &Sets) -> Vec<Store> {
    let mut out = Vec::new();
    let mut acc = Vec::new();
    for (&pc, vs) in vars {
        let base = sets.merged.get(&pc).map_or(TOP_ENV, |s| s.env);
        for v in vs.iter().filter(|v| v.acc.iter().any(|a| a.store)) {
            let t = Template::of(v.op);
            let b = operand_bytes(code.img, code.cells, pc, t.len);
            let idx: Option<Vec<(usize, Vec<u8>)>> = [reg::X, reg::Y]
                .into_iter()
                .filter(|&r| reads(t, r) && addresses(t, b, r))
                .map(|r| sets.of(pc, r).map(|s| (r, s)))
                .collect();
            let mut found: FxHashMap<usize, Seen> = v
                .acc
                .iter()
                .enumerate()
                .filter(|a| a.1.store)
                .map(|(i, _)| (i, (Some(BTreeSet::new()), Some(BTreeSet::new()))))
                .collect();
            let Some(idx) = idx else {
                out.extend(found.into_keys().map(|ai| Store {
                    pc,
                    op: v.op,
                    ai,
                    addr: v.acc[ai].addr,
                    addrs: None,
                    vals: None,
                    deps: Vec::new(),
                }));
                continue;
            };
            let n: usize = idx.iter().map(|e| e.1.len()).product();
            let mut held: FxHashMap<usize, Option<BTreeSet<u8>>> = FxHashMap::default();
            for k in 0..n {
                let mut env = base;
                let mut q = k;
                for (r, xs) in &idx {
                    env[*r] = Iv::exact(xs[q % xs.len()] as u32);
                    q /= xs.len();
                }
                acc.clear();
                absint(t.ops(), b, &mut env, &mut acc);
                for (i, a) in acc.iter().enumerate().filter(|a| a.1.store) {
                    let (addrs, vals) = found.get_mut(&i).unwrap();
                    match (a.addr.value(), addrs.as_mut()) {
                        (Some(x), Some(s)) => {
                            s.insert(x as u16);
                        }
                        _ => *addrs = None,
                    }
                    let Some(s) = vals.as_mut() else { continue };
                    if let Some(x) = a.val.value() {
                        s.insert(x as u8);
                        continue;
                    }
                    let from = held.entry(i).or_insert_with(|| match stored_reg(t, i) {
                        Some(r) => reaching(code, vars, sets, pc, r),
                        None => (a.val != Iv::full(1))
                            .then(|| (a.val.lo..=a.val.hi).map(|x| x as u8).collect()),
                    });
                    match from {
                        Some(f) => s.extend(f.iter()),
                        None => *vals = None,
                    }
                }
            }
            for (ai, (addrs, vals)) in found {
                out.push(Store {
                    pc,
                    op: v.op,
                    ai,
                    addr: v.acc[ai].addr,
                    addrs: addrs.map(|s| s.into_iter().collect()),
                    vals,
                    deps: Vec::new(),
                });
            }
        }
    }
    out.sort_unstable_by_key(|s| (s.pc, s.op, s.ai));
    out
}

/// The bytes a store of `stores` with known addresses may write and those the facts saw
/// written. A store of unknown address is checked against every byte the lift takes as
/// constant (`codechk`), tables included.
fn written(code: &Code, stores: &[Store]) -> FixedBitSet {
    let mut w = code.observed.clone();
    w.grow(0x10000);
    for a in stores.iter().filter_map(|s| s.addrs.as_ref()) {
        a.iter().for_each(|&x| w.insert(x as usize));
    }
    w
}

/// Per index value (`None`: not indexed, one byte) the byte a tag stands for, and its address
/// (`None`: a constant).
type Bytes = Vec<(Option<u8>, u8, Option<u16>)>;

/// Whether tags `ts` are indexed by one value: one register, still its value or one group.
fn joint(ts: &[Tab]) -> bool {
    let ix = |t: &Tab| match *t {
        Tab::T { reg, ix, .. } if reg != NOREG && ix != Ix::Lost => Some((reg, ix)),
        _ => None,
    };
    ts.len() > 1 && ts.iter().all(|t| ix(t).is_some() && ix(t) == ix(&ts[0]))
}

/// The table base a tag reads at index 0.
fn base(code: &Code, t: Tab) -> Option<u16> {
    match t {
        Tab::T { pc, op, reg, .. } => load_addr(code, pc, op, reg, 0),
        _ => None,
    }
}

/// The bytes tags `ts` stand for, per index value inside their tables' extent
/// ([`Objects::valid`]): tags indexed by one value (the same register and group) are columns
/// of one table and pair up value by value, others are read alone. Index guards keep each
/// load inside the extent where its index set reaches past it.
fn bytes(code: &Code, sets: &Sets, objs: &Objects, ts: &[Tab]) -> Option<(Vec<Bytes>, Vec<Guard>)> {
    let mut out = Vec::new();
    let mut guards = Vec::new();
    let groups: Vec<&[Tab]> = if joint(ts) {
        vec![ts]
    } else {
        ts.chunks(1).collect()
    };
    let mut per: Vec<Bytes> = Vec::new();
    for g in groups {
        let mut xs: Option<BTreeSet<u8>> = None;
        let mut bases = Vec::new();
        for t in g {
            match *t {
                Tab::K(_) => {}
                Tab::Any => return None,
                Tab::T { pc, reg, .. } => {
                    bases.push(base(code, *t)?);
                    if reg != NOREG {
                        let v = sets.of(pc, reg as usize);
                        let v: BTreeSet<u8> = v
                            .unwrap_or_else(|| sets.any(pc, reg as usize))
                            .into_iter()
                            .collect();
                        xs = Some(match xs {
                            None => v,
                            Some(o) => o.intersection(&v).copied().collect(),
                        });
                    }
                }
            }
        }
        let xs: Vec<Option<u8>> = match xs {
            None => vec![None],
            Some(v) => {
                let v: Vec<u8> = v.into_iter().collect();
                let n = objs.valid(&bases, &v);
                if n == 0 {
                    return None;
                }
                let ok = Iv {
                    lo: v[0] as u32,
                    hi: v[n - 1] as u32,
                };
                for t in g {
                    if let Tab::T { pc, reg, .. } = *t {
                        if sets
                            .any(pc, reg as usize)
                            .iter()
                            .any(|&x| !(ok.lo..=ok.hi).contains(&(x as u32)))
                        {
                            guards.push((pc, reg, ok));
                        }
                    }
                }
                v[..n].iter().map(|&x| Some(x)).collect()
            }
        };
        for t in g {
            per.push(match *t {
                Tab::K(k) => vec![(None, k, None)],
                Tab::Any => return None,
                Tab::T { pc, op, reg, .. } => xs
                    .iter()
                    .map(|&x| {
                        let a = load_addr(code, pc, op, reg, x.unwrap_or(0))?;
                        Some((x, code.img[a as usize], Some(a)))
                    })
                    .collect::<Option<Bytes>>()?,
            });
        }
    }
    out.extend(per);
    Some((out, guards))
}

/// Targets and the bytes they were read from.
type Found = (BTreeSet<u16>, BTreeSet<u16>, Vec<Guard>);

/// The words of the byte pair `lo`, `hi` (see the module documentation), when every byte
/// read is one no store writes.
fn pair(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    w: &FixedBitSet,
    lo: Tab,
    hi: Tab,
) -> Option<Found> {
    let (b, guards) = bytes(code, sets, objs, &[lo, hi])?;
    let [l, h] = &b[..] else { return None };
    let joint = joint(&[lo, hi]);
    if !joint && l.len() > 1 && h.len() > 1 {
        return None;
    }
    let pairs: Vec<_> = l
        .iter()
        .flat_map(|x| {
            h.iter()
                .filter(move |y| !joint || y.0 == x.0)
                .map(move |y| (x, y))
        })
        .collect();
    let read: BTreeSet<u16> = pairs
        .iter()
        .flat_map(|(x, y)| [x.2, y.2])
        .flatten()
        .collect();
    if read.iter().any(|&a| w.contains(a as usize)) {
        return None;
    }
    let t = pairs
        .iter()
        .map(|(x, y)| u16::from_le_bytes([x.1, y.1]))
        .collect();
    Some((t, read, guards))
}

/// Per computed transfer resolved or failed in some state of `state`, its resolution; an
/// `RTS`/`RTI` returning through its caller's address in every state is absent. A `JMP`/`JSR`
/// through cell operands goes to the words its operand bytes hold where they are tagged on
/// every path, else to every combination of the values of its cells ([`cell_values`]), when
/// each is known.
pub(crate) fn jumps(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    state: &FxHashMap<Key, St>,
    sets: &Sets,
    objs: &Objects,
    stores: &[Store],
    cellvals: &FxHashMap<u16, Option<BTreeSet<u8>>>,
) -> FxHashMap<u16, Jump> {
    let w = written(code, stores);
    let mut span = (u16::MAX, 0u16);
    for (&pc, vs) in vars {
        for v in vs {
            let end = pc.wrapping_add(Template::of(v.op).len as u16 - 1);
            span = (span.0.min(pc), span.1.max(end));
        }
    }
    let constant = |a: u16| !w.contains(a as usize) && (span.0..=span.1).contains(&a);
    // Per site: `Some(Some(_))` jumps, `Some(None)` returns, `None` failed.
    let mut res: FxHashMap<u16, Option<Option<Found>>> = FxHashMap::default();
    let mut keys: Vec<&Key> = state.keys().collect();
    keys.sort_unstable();
    for key in keys {
        let (pc, st) = (key.0, &state[key]);
        let tag = |a: u16| st.mem.iter().find(|e| e.0 == a).map(|e| e.1);
        let opnd = |a: u16| {
            if code.cells.contains(a as usize) {
                tag(a)
            } else {
                Some(Tab::K(code.img[a as usize]))
            }
        };
        for v in &vars[&pc] {
            let r = match v.flow {
                Flow::JmpInd(_) => {
                    pointer(code, pc).and_then(|(lo, hi)| match (tag(lo), tag(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, objs, &w, l, h).map(Some),
                        _ if constant(lo) && constant(hi) => Some(Some((
                            BTreeSet::from([u16::from_le_bytes([
                                code.img[lo as usize],
                                code.img[hi as usize],
                            ])]),
                            BTreeSet::from([lo, hi]),
                            Vec::new(),
                        ))),
                        _ => None,
                    })
                }
                ref f if operand_jump(f) => {
                    let (lo, hi) = (pc.wrapping_add(1), pc.wrapping_add(2));
                    match (opnd(lo), opnd(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, objs, &w, l, h).map(Some),
                        _ => None,
                    }
                }
                Flow::Rts(_) => match st.push {
                    [None, None, _] => Some(None),
                    [Some(l), Some(h), _] => pair(code, sets, objs, &w, l, h).map(|(t, b, g)| {
                        Some((t.into_iter().map(|x| x.wrapping_add(1)).collect(), b, g))
                    }),
                    _ => None,
                },
                Flow::Rti(_) => match st.push {
                    [None, None, None] => Some(None),
                    [Some(_), Some(l), Some(h)] => pair(code, sets, objs, &w, l, h).map(Some),
                    _ => None,
                },
                _ => continue,
            };
            let e = res.entry(pc).or_insert_with(|| r.clone());
            *e = match (e.take(), r) {
                (Some(Some(mut a)), Some(Some(b))) => {
                    a.0.extend(b.0);
                    a.1.extend(b.1);
                    a.2.extend(b.2);
                    Some(Some(a))
                }
                (Some(None), Some(None)) => Some(None),
                _ => None,
            };
        }
    }
    // A `JMP`/`JSR` whose operand cells are not tagged on every path: their value sets.
    let vals = |a: u16| -> Option<BTreeSet<u8>> {
        if code.cells.contains(a as usize) {
            cellvals.get(&a).cloned().flatten()
        } else {
            Some(BTreeSet::from([code.img[a as usize]]))
        }
    };
    for (&pc, r) in res.iter_mut().filter(|(_, r)| r.is_none()) {
        if !vars[&pc].iter().any(|v| operand_jump(&v.flow)) {
            continue;
        }
        if let (Some(l), Some(h)) = (vals(pc.wrapping_add(1)), vals(pc.wrapping_add(2))) {
            let t = l
                .iter()
                .flat_map(|&x| h.iter().map(move |&y| u16::from_le_bytes([x, y])))
                .collect();
            *r = Some(Some((t, BTreeSet::new(), Vec::new())));
        }
    }
    res.into_iter()
        .filter_map(|(pc, r)| match r {
            Some(Some((t, b, g))) => Some((
                pc,
                Jump::Targets(t.into_iter().collect(), b.into_iter().collect(), g),
            )),
            Some(None) => None,
            None => Some((pc, Jump::Fail)),
        })
        .collect()
}

/// The values each byte some store writes may hold, from its entry byte and the stores that
/// may write it (with the values of the cells they copy, a least fixpoint); `None` where a
/// store of unknown address or values may write it.
pub(crate) fn cell_values(
    img: &[u8; 0x10000],
    stores: &[Store],
) -> FxHashMap<u16, Option<BTreeSet<u8>>> {
    let mut out: FxHashMap<u16, Option<BTreeSet<u8>>> = FxHashMap::default();
    let init = |a: u16| Some(BTreeSet::from([img[a as usize]]));
    for s in stores {
        for &a in s.addrs.iter().flatten().chain(&s.deps) {
            out.entry(a).or_insert_with(|| init(a));
        }
    }
    let unknown: Vec<&Store> = stores.iter().filter(|s| s.addrs.is_none()).collect();
    for (a, v) in out.iter_mut() {
        if unknown
            .iter()
            .any(|s| s.addr.meets(*a as u32, *a as u32 + 1))
        {
            *v = None;
        }
    }
    loop {
        let mut changed = false;
        for s in stores {
            let Some(addrs) = &s.addrs else { continue };
            let mut v = s.vals.clone();
            for d in &s.deps {
                v = match (v, &out[d]) {
                    (Some(mut x), Some(y)) => {
                        x.extend(y);
                        Some(x)
                    }
                    _ => None,
                };
            }
            for a in addrs {
                let cur = out.get_mut(a).unwrap();
                let new = match (cur.clone(), &v) {
                    (Some(mut c), Some(x)) => {
                        c.extend(x);
                        Some(c)
                    }
                    _ => None,
                };
                if new != *cur {
                    *cur = new;
                    changed = true;
                }
            }
        }
        if !changed {
            break;
        }
    }
    out
}

/// Fill the values of stores into opcodes and the operands of `JMP`/`JSR`s through cells of
/// a register holding a table byte or a cell's byte on every path ([`bytes`], read inside the
/// table's extent: the guards that keeps are appended to `guards`), where the definitions
/// reaching them give none.
pub(crate) fn tag_values(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    stores: &mut [Store],
    guards: &mut Vec<Guard>,
    reads: &mut BTreeSet<u16>,
) {
    let w = written(code, stores);
    let mut control: FxHashSet<u16> = vars.keys().copied().collect();
    for (&pc, vs) in vars {
        if vs.iter().any(|v| operand_jump(&v.flow)) {
            control.extend([pc.wrapping_add(1), pc.wrapping_add(2)]);
        }
    }
    let steers = |s: &Store| {
        s.addrs
            .as_ref()
            .is_some_and(|a| a.iter().any(|x| control.contains(x)))
    };
    for s in stores.iter_mut().filter(|s| s.vals.is_none() && steers(s)) {
        let Some(r) = stored_reg(Template::of(s.op), s.ai) else {
            continue;
        };
        let Some(states) = sets.states.get(&s.pc) else {
            continue;
        };
        let mut vals = BTreeSet::new();
        let mut deps = Vec::new();
        let mut g = Vec::new();
        let mut read = Vec::new();
        let ok = states.iter().all(|st| {
            if let Some(x) = st.env[r].value() {
                vals.insert(x as u8);
                return true;
            }
            let Some(t) = st.tag[r] else { return false };
            let Some((b, gs)) = bytes(code, sets, objs, &[t]) else {
                return false;
            };
            g.extend(gs);
            b[0].iter().all(|&(_, x, a)| match a {
                Some(a) if code.cells.contains(a as usize) => {
                    deps.push(a);
                    true
                }
                Some(a) if w.contains(a as usize) => false,
                _ => {
                    vals.insert(x);
                    read.extend(a);
                    true
                }
            })
        });
        if ok {
            reads.extend(read);
            s.vals = Some(vals);
            deps.sort_unstable();
            deps.dedup();
            s.deps = deps;
            guards.extend(g);
        }
    }
}
