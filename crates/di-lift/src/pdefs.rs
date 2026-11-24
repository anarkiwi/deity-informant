//! Pointer definitions: where the variable part of each access's address is defined.
//!
//! An access of no one address forms it from a *location*: a word (the zero-page pair a
//! `(zp),Y` access reads, whose index lies in `[0, 255]`, or the operand bytes of an instruction
//! the program stores) or an index register added to a constant base (`X` or `Y`, also the `X`
//! choosing a `(zp,X)` pair). A *definition* of a word is the instructions that last wrote its
//! low and its high byte on a path to the access (`None`: the value an entry starts with); of a
//! register, the instruction that last wrote it. A write of one known address replaces the
//! byte; one that may miss it adds its definition to those reaching past it. Definitions reach
//! along the flow edges, through a call into its callee and from the callee's returns back to
//! the call's return site ([`Flows`]), so they come from the code alone.

use crate::analysis::{flow_succs, is_stack_op, Flow, Graph, Var};
use crate::objects::writes;
use crate::ptr::index_regs;
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{BTreeMap, BTreeSet};

/// The flow definitions reach along: a call enters its callees, and a return of no computed
/// target goes back to the return site of each call of a procedure reaching it without a
/// call (its own code, a tail call's included); an entry's returns leave the program.
pub struct Flows<'a> {
    g: &'a Graph,
    rets: FxHashMap<u16, BTreeSet<u16>>,
    /// Per procedure head, its instructions (calls stepped over to their return sites).
    body: FxHashMap<u16, FxHashSet<u16>>,
    /// Per return, the heads of the procedures it lies in.
    heads: FxHashMap<u16, BTreeSet<u16>>,
}

/// The callees and return site of a call at `pc` (`JSR`, `BRK`).
fn call(pc: u16, f: &Flow) -> Option<(&[u16], u16)> {
    match f {
        Flow::Jsr { t, ret } => Some((t.all(), *ret)),
        Flow::Brk(v) => Some((v, pc.wrapping_add(2))),
        _ => None,
    }
}

/// Whether `f` returns to its caller (an `RTS` or `RTI` of no computed target).
fn returns(f: &Flow) -> bool {
    matches!(f, Flow::Rts(t) | Flow::Rti(t) if t.is_empty())
}

impl<'a> Flows<'a> {
    pub fn new(g: &'a Graph) -> Self {
        let mut sites: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
        for (&pc, vs) in &g.vars {
            for (ts, ret) in vs.iter().filter_map(|v| call(pc, &v.flow)) {
                for &t in ts {
                    sites.entry(t).or_default().push(ret);
                }
            }
        }
        let mut rets: FxHashMap<u16, BTreeSet<u16>> = FxHashMap::default();
        let mut body: FxHashMap<u16, FxHashSet<u16>> = FxHashMap::default();
        let mut heads: FxHashMap<u16, BTreeSet<u16>> = FxHashMap::default();
        for (&head, back) in &sites {
            let mut seen: FxHashSet<u16> = FxHashSet::default();
            let mut work = vec![head];
            while let Some(pc) = work.pop() {
                if !seen.insert(pc) {
                    continue;
                }
                for v in g.vars.get(&pc).into_iter().flatten() {
                    if returns(&v.flow) {
                        rets.entry(pc).or_default().extend(back);
                        heads.entry(pc).or_default().insert(head);
                    }
                    match call(pc, &v.flow) {
                        Some((_, ret)) => work.push(ret),
                        None => work.extend(flow_succs(&v.flow)),
                    }
                }
            }
            body.insert(head, seen);
        }
        Flows {
            g,
            rets,
            body,
            heads,
        }
    }

    /// The procedure heads that write none of `writers`, in their own code or in a procedure
    /// they call.
    pub fn clean(&self, writers: &FxHashSet<u16>) -> FxHashSet<u16> {
        let mut dirty: FxHashSet<u16> = (self.body.iter())
            .filter(|(_, b)| b.iter().any(|pc| writers.contains(pc)))
            .map(|(&h, _)| h)
            .collect();
        loop {
            let more: Vec<u16> = (self.body.iter())
                .filter(|(h, b)| {
                    !dirty.contains(*h)
                        && b.iter().any(|pc| {
                            (self.g.vars.get(pc).into_iter().flatten()).any(|v| {
                                call(*pc, &v.flow)
                                    .is_some_and(|(ts, _)| ts.iter().any(|t| dirty.contains(t)))
                            })
                        })
                })
                .map(|(&h, _)| h)
                .collect();
            if more.is_empty() {
                break;
            }
            dirty.extend(more);
        }
        (self.body.keys())
            .filter(|h| !dirty.contains(h))
            .copied()
            .collect()
    }

    /// The instruction starts the definitions of a location reach past `pc`, the procedures
    /// `clean` writing none of it: a call of clean procedures also reaches its return site, and
    /// a return of clean procedures only reaches back to their callers through those calls.
    pub fn succs_of(&self, pc: u16, clean: &FxHashSet<u16>) -> Vec<u16> {
        let mut s = Vec::new();
        for v in self.g.vars.get(&pc).into_iter().flatten() {
            match call(pc, &v.flow) {
                Some(([], ret)) => s.push(ret),
                Some((ts, ret)) => {
                    s.extend(ts);
                    if ts.iter().all(|t| clean.contains(t)) {
                        s.push(ret);
                    }
                }
                None if returns(&v.flow) => {
                    let hs = self.heads.get(&pc);
                    if !hs.is_some_and(|hs| hs.iter().all(|h| clean.contains(h))) {
                        s.extend(self.rets.get(&pc).into_iter().flatten());
                    }
                }
                None => s.extend(flow_succs(&v.flow)),
            }
        }
        s
    }

    /// The returns of the procedures rooted at `roots` (each call stepped over to its return
    /// site): where an entry leaves the program.
    pub fn exits(&self, roots: &[u16]) -> Vec<u16> {
        let (mut seen, mut out): (FxHashSet<u16>, Vec<u16>) = Default::default();
        let mut work = roots.to_vec();
        while let Some(pc) = work.pop() {
            if !seen.insert(pc) {
                continue;
            }
            for v in self.g.vars.get(&pc).into_iter().flatten() {
                if returns(&v.flow) {
                    out.push(pc);
                }
                match call(pc, &v.flow) {
                    Some((_, ret)) => work.push(ret),
                    None => work.extend(flow_succs(&v.flow)),
                }
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }

    /// The instruction starts definitions reach past `pc`.
    pub fn succs(&self, pc: u16) -> Vec<u16> {
        let mut s = Vec::new();
        for v in self.g.vars.get(&pc).into_iter().flatten() {
            match call(pc, &v.flow) {
                Some(([], ret)) => s.push(ret),
                Some((ts, _)) => s.extend(ts),
                None if returns(&v.flow) => s.extend(self.rets.get(&pc).into_iter().flatten()),
                None => s.extend(flow_succs(&v.flow)),
            }
        }
        s
    }
}

/// A location an address is formed from: the word at a byte and the next, or a register
/// (its register-file index) indexing the run of data bytes holding the base (its first byte;
/// the base where no run holds it): one bound of the register keeps every base of a run in it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Loc {
    Word(u16),
    Reg(u8, u32),
}

/// The first byte of the run of data bytes holding the base of access `ai` of variant `op`
/// at `pc`, the base itself where none does.
fn run_of(g: &Graph, key: (u16, u8, usize), addr: u32) -> u32 {
    let base = g.bases.get(&key).map_or(addr, |h| h.1.lo);
    let i = g.runs.partition_point(|r| r.1 <= base);
    g.runs.get(i).filter(|r| r.0 <= base).map_or(base, |r| r.0)
}

/// A definition: its location, its byte (0 the low byte or the register, 1 the high byte)
/// and the instruction writing it (`None`: the value an entry starts with).
pub type Def = (Loc, u8, Option<u16>);

/// The definitions of a program and where they reach.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PDefs {
    pub defs: Vec<Def>,
    /// Per instruction start of an access, the definitions reaching it of the locations its
    /// addresses are formed from.
    pub at: Vec<(u16, Vec<u32>)>,
    /// Per instruction start writing a word, and per anchor of a walk's check, the
    /// definitions of the word reaching past it.
    pub past: Vec<(u16, Vec<u32>)>,
    /// Per instruction start, the definitions reaching it of `A`, `X` and `Y` as values
    /// (locations `Reg(r, VALUE)`).
    pub values: Vec<(u16, Vec<u32>)>,
}

/// The run of a register location that is the register's value, not an index.
pub const VALUE: u32 = u32::MAX;

/// Whether variant `v` moves the stack or the program counter through it.
fn stack(v: &Var) -> bool {
    is_stack_op(v.op)
        || matches!(
            v.flow,
            Flow::Jsr { .. } | Flow::Rts(_) | Flow::Rti(_) | Flow::Brk(_)
        )
}

/// The locations the addresses of the accesses at `pc` are formed from.
pub fn reads(g: &Graph, pc: u16) -> BTreeSet<Loc> {
    let mut out = BTreeSet::new();
    for v in g.vars.get(&pc).into_iter().flatten().filter(|v| !stack(v)) {
        let len = di_pcode::Template::of(v.op).len;
        let accs = v.acc.iter().enumerate();
        for (ai, a) in accs.filter(|(_, a)| !a.dummy && a.addr.value().is_none()) {
            let run = run_of(g, (pc, v.op, ai), a.addr.lo);
            match a.ptr {
                Some(p) => match (p.lo.and_then(super::analysis::Iv::value), p.hi.value()) {
                    (Some(l), Some(h)) if h == l + 1 => out.insert(Loc::Word(l as u16)),
                    _ => out.insert(Loc::Reg(di_pcode::reg::X as u8, run)),
                },
                None => {
                    let q = pc.wrapping_add(1);
                    if len == 3
                        && (g.cells.contains(q as usize) || g.cells.contains(q as usize + 1))
                    {
                        out.insert(Loc::Word(q));
                    }
                    let ix = index_regs(v.op);
                    for (bit, r) in [(1, di_pcode::reg::X), (2, di_pcode::reg::Y)] {
                        if ix & bit != 0 {
                            out.insert(Loc::Reg(r as u8, run));
                        }
                    }
                    false
                }
            };
        }
    }
    out
}

/// How the instruction at `pc` writes each part of `loc` (the bytes of a word, a register):
/// `Some(true)` replacing it, `Some(false)` where it may.
fn writer(g: &Graph, pc: u16, loc: Loc, skip: Skip) -> [Option<bool>; 2] {
    let vs = g.vars.get(&pc).map_or(&[][..], |v| &v[..]);
    let one = vs.len() == 1;
    let mut out = [None, None];
    for v in vs {
        match loc {
            Loc::Reg(r, _) if writes(v.op, r as usize) => {
                out[0] = Some(one || out[0] == Some(true))
            }
            Loc::Reg(..) => {}
            Loc::Word(q) if !is_stack_op(v.op) => {
                for a in
                    (v.acc.iter()).filter(|a| a.store && !a.dummy && !a.back && !skip(pc, v, a))
                {
                    for (i, b) in [q as u32, q.wrapping_add(1) as u32].into_iter().enumerate() {
                        if a.addr.meets(b, b + 1) {
                            let exact = one && a.addr.value() == Some(b);
                            out[i] = Some(exact || out[i] == Some(true));
                        }
                    }
                }
            }
            Loc::Word(_) => {}
        }
    }
    out
}

/// Stores a location's definitions leave out: `(pc, variant, access)`.
pub(crate) type Skip<'s> = &'s dyn Fn(u16, &Var, &crate::analysis::Access) -> bool;

/// The definitions of one location: their bytes, and per instruction start writing it the
/// definition it makes and whether it writes the whole byte.
pub(crate) struct Local {
    pub(crate) defs: Vec<Def>,
    bytes: [FixedBitSet; 2],
    pub(crate) writes: FxHashMap<u16, Vec<(usize, bool)>>,
}

impl Local {
    fn new(g: &Graph, pcs: &[u16], loc: Loc) -> Self {
        Self::with(g, pcs, loc, &|_, _, _| false)
    }

    /// The definitions of `loc` by the instructions `pcs`, the stores `skip` names left out.
    pub(crate) fn with(g: &Graph, pcs: &[u16], loc: Loc, skip: Skip) -> Self {
        let n = if matches!(loc, Loc::Word(_)) { 2 } else { 1 };
        let mut defs: Vec<Def> = (0..n).map(|i| (loc, i, None)).collect();
        let mut writes: FxHashMap<u16, Vec<(usize, bool)>> = FxHashMap::default();
        for &pc in pcs {
            for (i, how) in writer(g, pc, loc, skip).into_iter().enumerate() {
                if let Some(exact) = how {
                    writes.entry(pc).or_default().push((defs.len(), exact));
                    defs.push((loc, i as u8, Some(pc)));
                }
            }
        }
        let bytes = std::array::from_fn(|i| {
            let mut m = FixedBitSet::with_capacity(defs.len());
            m.extend((0..defs.len()).filter(|&d| defs[d].1 as usize == i));
            m
        });
        Local {
            defs,
            bytes,
            writes,
        }
    }

    /// The definitions reaching past `pc` from those reaching it.
    pub(crate) fn transfer(&self, pc: u16, ins: &FixedBitSet) -> FixedBitSet {
        let mut out = ins.clone();
        for &(d, exact) in self.writes.get(&pc).into_iter().flatten() {
            if exact {
                out.difference_with(&self.bytes[self.defs[d].1 as usize]);
            }
            out.insert(d);
        }
        out
    }

    /// The definitions reaching each instruction start from `roots`.
    pub(crate) fn reaching(&self, cfg: &Flows, roots: &[u16]) -> FxHashMap<u16, FixedBitSet> {
        let empty = FixedBitSet::with_capacity(self.defs.len());
        let mut ins: FxHashMap<u16, FixedBitSet> = FxHashMap::default();
        let mut work: Vec<u16> = Vec::new();
        for &r in roots.iter().filter(|r| cfg.g.vars.contains_key(r)) {
            ins.entry(r)
                .or_insert_with(|| empty.clone())
                .union_with(&self.entries());
            work.push(r);
        }
        let mut queued: FxHashSet<u16> = work.iter().copied().collect();
        let clean = cfg.clean(&self.writes.keys().copied().collect());
        while let Some(pc) = work.pop() {
            queued.remove(&pc);
            let o = self.transfer(pc, &ins[&pc]);
            for s in cfg
                .succs_of(pc, &clean)
                .into_iter()
                .filter(|s| cfg.g.vars.contains_key(s))
            {
                let e = ins.entry(s).or_insert_with(|| empty.clone());
                if !o.is_subset(e) {
                    e.union_with(&o);
                    if queued.insert(s) {
                        work.push(s);
                    }
                }
            }
        }
        ins
    }

    /// The entry definitions.
    fn entries(&self) -> FixedBitSet {
        let mut m = FixedBitSet::with_capacity(self.defs.len());
        m.extend((0..self.defs.len()).filter(|&d| self.defs[d].2.is_none()));
        m
    }
}

/// The pointer definitions of `g` with entries `roots`.
pub fn defs(g: &Graph, roots: &[u16]) -> PDefs {
    let cfg = Flows::new(g);
    let mut pcs: Vec<u16> = g.vars.keys().copied().collect();
    pcs.sort_unstable();
    let mut wants: BTreeMap<Loc, (Vec<u16>, Vec<u16>)> = BTreeMap::new();
    for &pc in &pcs {
        for l in reads(g, pc) {
            wants.entry(l).or_default().0.push(pc);
        }
    }
    let anchors = (g.objs.post.iter()).flat_map(|(&pc, cs)| cs.iter().map(move |c| (pc, c.q)));
    for (pc, q) in anchors {
        wants.entry(Loc::Word(q)).or_default().1.push(pc);
    }
    let mut all: Vec<Def> = Vec::new();
    let mut at: BTreeMap<u16, BTreeSet<u32>> = BTreeMap::new();
    let mut past: BTreeMap<u16, BTreeSet<u32>> = BTreeMap::new();
    for (&loc, (users, walks)) in &wants {
        let local = Local::new(g, &pcs, loc);
        let ins = local.reaching(&cfg, roots);
        let base = all.len() as u32;
        let ids = |m: &FixedBitSet| m.ones().map(|d| base + d as u32).collect::<Vec<_>>();
        for pc in users {
            if let Some(i) = ins.get(pc) {
                at.entry(*pc).or_default().extend(ids(i));
            }
        }
        if let Loc::Word(_) = loc {
            let writers = local.writes.keys().copied();
            for pc in writers.chain(walks.iter().copied()) {
                if let Some(i) = ins.get(&pc) {
                    past.entry(pc)
                        .or_default()
                        .extend(ids(&local.transfer(pc, i)));
                }
            }
        }
        all.extend(local.defs);
    }
    let mut values: BTreeMap<u16, BTreeSet<u32>> = BTreeMap::new();
    for r in [di_pcode::reg::A, di_pcode::reg::X, di_pcode::reg::Y] {
        let local = Local::new(g, &pcs, Loc::Reg(r as u8, VALUE));
        let ins = local.reaching(&cfg, roots);
        let base = all.len() as u32;
        for (&pc, m) in &ins {
            values
                .entry(pc)
                .or_default()
                .extend(m.ones().map(|d| base + d as u32));
        }
        all.extend(local.defs);
    }
    let flat = |m: BTreeMap<u16, BTreeSet<u32>>| {
        (m.into_iter())
            .map(|(k, v)| (k, v.into_iter().collect()))
            .collect()
    };
    PDefs {
        defs: all,
        at: flat(at),
        past: flat(past),
        values: flat(values),
    }
}
