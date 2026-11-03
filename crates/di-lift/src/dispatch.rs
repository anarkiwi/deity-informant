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

use crate::analysis::{absint, operand_bytes, Flow, Iv, Var, TOP_ENV};
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
}

/// A computed transfer's targets and the bytes they were read from, or that some path leaves
/// it unresolved.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Jump {
    Targets(Vec<u16>, Vec<u16>),
    Fail,
}

/// The analysis results index and value sets come from: the states of every context per
/// instruction start, their join, the loops' induction variables, the predecessors of each
/// start and the entries.
pub(crate) struct Sets<'a> {
    pub(crate) states: FxHashMap<u16, Vec<&'a St>>,
    pub(crate) merged: &'a FxHashMap<u16, St>,
    pub(crate) ind: &'a [Induction],
    pub(crate) preds: FxHashMap<u16, Vec<u16>>,
    pub(crate) roots: &'a [u16],
}

impl Sets<'_> {
    /// The values data register `r` may hold at `pc`; `None` (unbounded) when neither its
    /// interval nor a loop's trip bound limits it.
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
        bounded.then(|| (0..0x100).filter(|&x| ok[x]).map(|x| x as u8).collect())
    }
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

/// The bytes whose contents the analysis tracks: the pointers of `JMP (ind)`s.
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
    };
    let mut v: Vec<u16> = vars
        .iter()
        .filter(|(_, vs)| vs.iter().any(|v| matches!(v.flow, Flow::JmpInd(_))))
        .filter_map(|(&pc, _)| pointer(&code, pc))
        .flat_map(|(lo, hi)| [lo, hi])
        .collect();
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
                });
            }
        }
    }
    out.sort_unstable_by_key(|s| (s.pc, s.op, s.ai));
    out
}

/// The bytes some store of `stores` may write.
fn written(stores: &[Store]) -> FixedBitSet {
    let mut w = FixedBitSet::with_capacity(0x10000);
    for s in stores {
        match &s.addrs {
            Some(a) => a.iter().for_each(|&x| w.insert(x as usize)),
            None => w.insert_range(s.addr.lo as usize..(s.addr.hi as usize).min(0xffff) + 1),
        }
    }
    w
}

/// Per index value (`None`: not indexed, one byte) the byte a tag stands for, and its address
/// (`None`: a constant).
type Bytes = Vec<(Option<u8>, u8, Option<u16>)>;

fn bytes(code: &Code, sets: &Sets, t: Tab) -> Option<Bytes> {
    match t {
        Tab::K(k) => Some(vec![(None, k, None)]),
        Tab::Any => None,
        Tab::T { pc, op, reg, .. } => {
            let xs = if reg == NOREG {
                vec![None]
            } else {
                sets.of(pc, reg as usize)?.into_iter().map(Some).collect()
            };
            xs.into_iter()
                .map(|x| {
                    let a = load_addr(code, pc, op, reg, x.unwrap_or(0))?;
                    Some((x, code.img[a as usize], Some(a)))
                })
                .collect()
        }
    }
}

/// Targets and the bytes they were read from.
type Found = (BTreeSet<u16>, BTreeSet<u16>);

/// The words of the byte pair `lo`, `hi` (see the module documentation), when every byte
/// read is one no store writes.
fn pair(code: &Code, sets: &Sets, w: &FixedBitSet, lo: Tab, hi: Tab) -> Option<Found> {
    let (l, h) = (bytes(code, sets, lo)?, bytes(code, sets, hi)?);
    let ix = |t: Tab| match t {
        Tab::T { reg, ix, .. } if reg != NOREG && ix != Ix::Lost => Some((reg, ix)),
        _ => None,
    };
    let pairs: Vec<_> = match (ix(lo), ix(hi)) {
        (Some(a), Some(b)) if a == b => l
            .iter()
            .flat_map(|x| h.iter().filter(move |y| y.0 == x.0).map(move |y| (x, y)))
            .collect(),
        _ if l.len() == 1 || h.len() == 1 => l
            .iter()
            .flat_map(|x| h.iter().map(move |y| (x, y)))
            .collect(),
        _ => return None,
    };
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
    Some((t, read))
}

/// Per computed transfer resolved or failed in some state of `state`, its resolution; an
/// `RTS`/`RTI` returning through its caller's address in every state is absent.
pub(crate) fn jumps(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    state: &FxHashMap<Key, St>,
    sets: &Sets,
    stores: &[Store],
) -> FxHashMap<u16, Jump> {
    let w = written(stores);
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
        for v in &vars[&pc] {
            let r = match v.flow {
                Flow::JmpInd(_) => {
                    pointer(code, pc).and_then(|(lo, hi)| match (tag(lo), tag(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, &w, l, h).map(Some),
                        _ if constant(lo) && constant(hi) => Some(Some((
                            BTreeSet::from([u16::from_le_bytes([
                                code.img[lo as usize],
                                code.img[hi as usize],
                            ])]),
                            BTreeSet::from([lo, hi]),
                        ))),
                        _ => None,
                    })
                }
                Flow::Rts(_) => match st.push {
                    [None, None, _] => Some(None),
                    [Some(l), Some(h), _] => pair(code, sets, &w, l, h).map(|(t, b)| {
                        Some((t.into_iter().map(|x| x.wrapping_add(1)).collect(), b))
                    }),
                    _ => None,
                },
                Flow::Rti(_) => match st.push {
                    [None, None, None] => Some(None),
                    [Some(_), Some(l), Some(h)] => pair(code, sets, &w, l, h).map(Some),
                    _ => None,
                },
                _ => continue,
            };
            let e = res.entry(pc).or_insert_with(|| r.clone());
            *e = match (e.take(), r) {
                (Some(Some(mut a)), Some(Some(b))) => {
                    a.0.extend(b.0);
                    a.1.extend(b.1);
                    Some(Some(a))
                }
                (Some(None), Some(None)) => Some(None),
                _ => None,
            };
        }
    }
    res.into_iter()
        .filter_map(|(pc, r)| match r {
            Some(Some((t, b))) => Some((
                pc,
                Jump::Targets(t.into_iter().collect(), b.into_iter().collect()),
            )),
            Some(None) => None,
            None => Some((pc, Jump::Fail)),
        })
        .collect()
}
