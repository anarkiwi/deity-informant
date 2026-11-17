//! Covered checks: the memory map's check of an access into the data block ([`Objs::machine`])
//! that a dominating check of the same address form already makes is left out.
//!
//! The address of an access through a pair or an absolute base plus an index register is a
//! *form*: the values of the pair's bytes and of the index's base (each named by the
//! instruction or merge that last defines it, the index's base plus a constant), and a constant
//! offset. Names are found by a must-analysis along the control flow, a call stepping over its
//! callee with the registers and bytes the callee may write redefined. Two accesses of one
//! form of which one dominates the other read the same values: their addresses differ by their
//! offsets' difference.
//!
//! A check is made where no dominating check covers the access; it tests the hull of the
//! offsets of the accesses of its form every path from it makes before a value of the form is
//! redefined or the routine returns, which the original reads there whatever the data: a run
//! the check fails on is one the original leaves its runs on. Every access its check's hull
//! covers and it dominates is then plain.

use crate::analysis::{flow_succs, Flow, Graph, Iv};
use crate::objects::{block_id, writes, Objs};
use crate::ptr::StoreKey;
use crate::vsa::Bits;
use di_pcode::reg::{X, Y};
use fixedbitset::FixedBitSet;
use petgraph::algo::dominators::{simple_fast, Dominators};
use petgraph::graphmap::DiGraphMap;
use rustc_hash::{FxHashMap, FxHashSet};

/// Where a location's value is defined: at the merge entering an instruction, or by the
/// instruction; the location's index (`Names`) keeps the values of distinct locations apart.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum Name {
    In(u16, usize),
    Out(u16, usize),
}

impl Name {
    fn at(self) -> u16 {
        match self {
            Name::In(p, _) | Name::Out(p, _) => p,
        }
    }
}

/// A location's value: not reached yet, a constant (an index register), or a name plus a
/// constant.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Val {
    Bot,
    Imm(u8),
    At(Name, i32),
}

/// What a callee may write and the return sites every one of its returns may reach (none:
/// one leaves otherwise).
#[derive(Clone, Default)]
struct Summary {
    regs: [bool; 2],
    stores: Vec<Iv>,
    rets: Option<FxHashSet<u16>>,
}

/// The program's control flow with each call stepping over its callee.
struct Flows<'a> {
    g: &'a Graph,
    succ: FxHashMap<u16, Vec<u16>>,
    calls: FxHashMap<u16, Summary>,
    roots: Vec<u16>,
}

const ROOT: u32 = 0x10000;

impl<'a> Flows<'a> {
    fn new(g: &'a Graph) -> Self {
        let mut memo: FxHashMap<u16, Option<Summary>> = FxHashMap::default();
        let mut pcs: Vec<u16> = g.vars.keys().copied().collect();
        pcs.sort_unstable();
        let mut calls = FxHashMap::default();
        let mut succ: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
        let mut targets: FxHashSet<u16> = FxHashSet::default();
        for &pc in &pcs {
            let mut out = Vec::new();
            for v in &g.vars[&pc] {
                match &v.flow {
                    Flow::Jsr { t, ret } => {
                        targets.extend(t.all());
                        let s = callee(g, t.all(), &mut memo);
                        if s.rets.as_ref().is_some_and(|r| r.contains(ret)) {
                            out.push(*ret);
                        }
                        calls.insert(pc, s);
                    }
                    Flow::Rts(_) | Flow::Rti(_) | Flow::Brk(_) | Flow::Jam => {}
                    f => out.extend(flow_succs(f)),
                }
            }
            out.retain(|t| g.vars.contains_key(t));
            out.sort_unstable();
            out.dedup();
            succ.insert(pc, out);
        }
        let mut preds: FxHashSet<u16> = FxHashSet::default();
        succ.values().flatten().for_each(|&t| {
            preds.insert(t);
        });
        let mut roots: Vec<u16> = (pcs.iter().copied())
            .filter(|p| !preds.contains(p) || targets.contains(p))
            .collect();
        let mut seen: FxHashSet<u16> = FxHashSet::default();
        let mut work = roots.clone();
        loop {
            while let Some(p) = work.pop() {
                if seen.insert(p) {
                    work.extend(&succ[&p]);
                }
            }
            match pcs.iter().find(|p| !seen.contains(p)) {
                Some(&p) => {
                    roots.push(p);
                    work.push(p);
                }
                None => break,
            }
        }
        Flows {
            g,
            succ,
            calls,
            roots,
        }
    }

    fn dominators(&self) -> Dominators<u32> {
        let mut dg: DiGraphMap<u32, ()> = DiGraphMap::new();
        for (&p, ts) in &self.succ {
            dg.add_node(p as u32);
            for &t in ts {
                dg.add_edge(p as u32, t as u32, ());
            }
        }
        for &r in &self.roots {
            dg.add_edge(ROOT, r as u32, ());
        }
        simple_fast(&dg, ROOT)
    }
}

/// The summary of a call of `ts`, its callees' regions found along the flow, nested calls by
/// their own summaries; a call reached again inside them leaves.
fn callee(g: &Graph, ts: &[u16], memo: &mut FxHashMap<u16, Option<Summary>>) -> Summary {
    let mut out = Summary::default();
    let mut rets: Option<FxHashSet<u16>> = None;
    for &t in ts {
        let s = match memo.get(&t) {
            Some(Some(s)) => s.clone(),
            Some(None) => return Summary::default(),
            None => {
                memo.insert(t, None);
                let s = region(g, t, memo);
                memo.insert(t, Some(s.clone()));
                s
            }
        };
        out.regs = [out.regs[0] | s.regs[0], out.regs[1] | s.regs[1]];
        out.stores.extend(s.stores);
        let Some(r) = s.rets else {
            return Summary::default();
        };
        rets = Some(match rets {
            None => r,
            Some(o) => o.intersection(&r).copied().collect(),
        });
    }
    out.rets = rets;
    out
}

/// The summary of the routine at `t` ([`callee`]).
fn region(g: &Graph, t: u16, memo: &mut FxHashMap<u16, Option<Summary>>) -> Summary {
    let mut out = Summary::default();
    let mut rets: Option<FxHashSet<u16>> = None;
    let (mut seen, mut work) = (FxHashSet::default(), vec![t]);
    while let Some(p) = work.pop() {
        if !seen.insert(p) {
            continue;
        }
        let Some(vs) = g.vars.get(&p) else {
            return Summary::default();
        };
        for v in vs {
            out.regs[0] |= writes(v.op, X);
            out.regs[1] |= writes(v.op, Y);
            (v.acc.iter())
                .filter(|a| a.store && !a.dummy)
                .for_each(|a| out.stores.push(a.addr));
            match &v.flow {
                Flow::Jsr { t, ret } => {
                    let s = callee(g, t.all(), memo);
                    out.regs = [out.regs[0] | s.regs[0], out.regs[1] | s.regs[1]];
                    out.stores.extend(s.stores);
                    if !s.rets.is_some_and(|r| r.contains(ret)) {
                        return Summary::default();
                    }
                    work.push(*ret);
                }
                Flow::Rts(ts) => {
                    let r: FxHashSet<u16> = ts.iter().copied().collect();
                    rets = Some(match rets {
                        None => r,
                        Some(o) => o.intersection(&r).copied().collect(),
                    });
                }
                Flow::Rti(_) | Flow::Brk(_) | Flow::Jam => return Summary::default(),
                Flow::JmpInd(ts) if ts.is_empty() => return Summary::default(),
                f => work.extend(flow_succs(f)),
            }
        }
    }
    out.rets = rets;
    out
}

/// The locations a form names: `X`, `Y`, then the tracked bytes.
struct Names<'a> {
    fl: &'a Flows<'a>,
    bytes: Vec<u32>,
    vals: FxHashMap<u16, Vec<Val>>,
}

impl<'a> Names<'a> {
    fn new(fl: &'a Flows<'a>, bytes: Vec<u32>) -> Self {
        let mut n = Names {
            fl,
            bytes,
            vals: FxHashMap::default(),
        };
        n.solve();
        n
    }

    fn width(&self) -> usize {
        2 + self.bytes.len()
    }

    /// The values after instruction `pc` given those before it.
    fn transfer(&self, pc: u16, ins: &[Val]) -> Vec<Val> {
        let g = self.fl.g;
        let vs = &g.vars[&pc];
        let mut out = ins.to_vec();
        let def = |i: usize| Val::At(Name::Out(pc, i), 0);
        let cell = |i: u16| g.cells.contains(pc.wrapping_add(i) as usize);
        for (k, r) in [(0, X), (1, Y)] {
            let (inc, dec, imm) = if r == X {
                (0xe8, 0xca, 0xa2)
            } else {
                (0xc8, 0x88, 0xa0)
            };
            let hull = (g.regs.get(&pc)).and_then(|x| Bits::from_words(x[r]).hull());
            let one = (vs.len() == 1).then(|| vs[0].op);
            out[k] = match (one, ins[k]) {
                (Some(op), Val::Imm(c)) if op == inc || op == dec => Val::Imm(if op == inc {
                    c.wrapping_add(1)
                } else {
                    c.wrapping_sub(1)
                }),
                (Some(op), Val::At(n, d)) if op == inc && hull.is_some_and(|h| h.hi < 0xff) => {
                    Val::At(n, d + 1)
                }
                (Some(op), Val::At(n, d)) if op == dec && hull.is_some_and(|h| h.lo > 0) => {
                    Val::At(n, d - 1)
                }
                (Some(op), _) if op == imm && !cell(1) => Val::Imm(g.img[pc as usize + 1]),
                _ if vs.iter().any(|v| writes(v.op, r)) => def(k),
                (_, v) => v,
            };
        }
        let call = self.fl.calls.get(&pc);
        if call.is_some_and(|s| s.regs[0]) {
            out[0] = def(0);
        }
        if call.is_some_and(|s| s.regs[1]) {
            out[1] = def(1);
        }
        for (i, &b) in self.bytes.iter().enumerate() {
            let hit = |a: &Iv| a.meets(b, b + 1);
            let stored = (vs.iter().flat_map(|v| &v.acc))
                .any(|a| a.store && !a.dummy && hit(&a.addr))
                || call.is_some_and(|s| s.stores.iter().any(hit));
            if stored {
                out[2 + i] = def(2 + i);
            }
        }
        out
    }

    /// The values before each instruction: the one every reached predecessor leaves, else a
    /// name of the merge (at a root, always).
    fn solve(&mut self) {
        let n = self.width();
        let mut preds: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
        for (&p, ts) in &self.fl.succ {
            for &t in ts {
                preds.entry(t).or_default().push(p);
            }
        }
        let roots: FxHashSet<u16> = self.fl.roots.iter().copied().collect();
        let mut outs: FxHashMap<u16, Vec<Val>> = FxHashMap::default();
        let mut work: Vec<u16> = self.fl.roots.clone();
        let mut queued: FxHashSet<u16> = work.iter().copied().collect();
        while let Some(pc) = work.pop() {
            queued.remove(&pc);
            let ins: Vec<Val> = (0..n)
                .map(|i| {
                    let mut vals = (preds.get(&pc).into_iter().flatten())
                        .filter_map(|p| outs.get(p).map(|o| o[i]))
                        .filter(|v| *v != Val::Bot);
                    let merged = Val::At(Name::In(pc, i), 0);
                    match vals.next() {
                        _ if roots.contains(&pc) => merged,
                        None => Val::Bot,
                        Some(f) if vals.all(|v| v == f) => f,
                        Some(_) => merged,
                    }
                })
                .collect();
            let out = self.transfer(pc, &ins);
            self.vals.insert(pc, ins);
            if outs.get(&pc) != Some(&out) {
                outs.insert(pc, out);
                for &t in &self.fl.succ[&pc] {
                    if queued.insert(t) {
                        work.push(t);
                    }
                }
            }
        }
    }
}

/// An access's form: its runs, the names of its pair's bytes (none: an absolute base), and the
/// name of its index's base (none: a constant index).
type Form = (Vec<(u32, u32)>, Option<(Name, Name)>, Option<Name>);

/// A machine-checked load into the data block: its key, form and offset.
struct Acc {
    key: StoreKey,
    form: Form,
    off: i32,
}

/// A load's pair (low byte, none: an absolute base), index register and constant offset.
type Shape = (Option<u32>, usize, i32);

/// The pair (low byte) or absolute base and the index register of load `key`'s address: an
/// absolute operand is a pair where the program writes it or the host's init leaves it
/// (`init`, [`Graph::free`]), its value then no code's.
fn shape(g: &Graph, init: &FixedBitSet, key: StoreKey) -> Option<Shape> {
    let (pc, op, ai) = key;
    let vs = g.vars.get(&pc)?;
    let v = vs.iter().find(|v| v.op == op).filter(|_| vs.len() == 1)?;
    if v.acc[ai].store || v.acc[ai].dummy {
        return None;
    }
    let cells = (1..3)
        .map(|i| pc.wrapping_add(i) as usize)
        .any(|a| g.cells.contains(a) || g.free.contains(a) || init.contains(a));
    let base = || {
        let lo = g.img[pc.wrapping_add(1) as usize] as i32;
        lo | (g.img[pc.wrapping_add(2) as usize] as i32) << 8
    };
    match (op & 0x1f, op) {
        (0x11, _) => {
            let q = v.acc[ai].ptr.and_then(|p| p.lo).and_then(|l| l.value())?;
            (q < 0xff).then_some((Some(q), Y, 0))
        }
        (0x19, _) | (_, 0xbe) if cells => Some((Some(pc as u32 + 1), Y, 0)),
        (0x19, _) | (_, 0xbe) => Some((None, Y, base())),
        (0x1c..=0x1e, _) if cells => Some((Some(pc as u32 + 1), X, 0)),
        (0x1c..=0x1e, _) => Some((None, X, base())),
        _ => None,
    }
}

/// Leaves out the machine checks of `out` a dominating check covers and widens each check left
/// to the offsets of its form the original reads past it on every path ([module docs](self)).
pub(crate) fn cover(g: &Graph, init: &FixedBitSet, out: &mut Objs) {
    let mut keys: Vec<StoreKey> = out.machine.keys().copied().collect();
    keys.sort_unstable();
    let dbg = std::env::var("DI_COVER").is_ok();
    if dbg {
        for k in &keys {
            eprintln!(
                "cover key {:04X} {:02X} {} shape {:?}",
                k.0,
                k.1,
                k.2,
                shape(g, init, *k)
            );
        }
    }
    let shapes: Vec<(StoreKey, Shape)> = (keys.into_iter())
        .filter_map(|k| shape(g, init, k).map(|s| (k, s)))
        .collect();
    if shapes.is_empty() {
        return;
    }
    let mut bytes: Vec<u32> = (shapes.iter())
        .filter_map(|s| s.1 .0)
        .flat_map(|q| [q, q + 1])
        .collect();
    bytes.sort_unstable();
    bytes.dedup();
    let fl = Flows::new(g);
    let names = Names::new(&fl, bytes);
    let at = |b: u32| 2 + names.bytes.binary_search(&b).unwrap();
    let mut accs: Vec<Acc> = Vec::new();
    for (key, (q, r, base)) in shapes {
        let pc = key.0;
        let Some(vals) = names.vals.get(&pc) else {
            continue;
        };
        let hull = (g.regs.get(&pc)).and_then(|x| Bits::from_words(x[r]).hull());
        let ix = match (hull, vals[(r == Y) as usize]) {
            (Some(h), _) if h.lo == h.hi => Val::Imm(h.lo as u8),
            (_, v) => v,
        };
        let pair = match q {
            Some(q) => match (vals[at(q)], vals[at(q + 1)]) {
                (Val::At(a, 0), Val::At(b, 0)) => Some((a, b)),
                _ => continue,
            },
            None => None,
        };
        let (idx, off) = match ix {
            Val::Imm(c) if pair.is_some() => (None, c as i32),
            Val::At(n, d) => (Some(n), base + d),
            _ => continue,
        };
        let runs = match out.data.get(&key) {
            Some(rs) => rs.clone(),
            None => {
                let b = out.machine[&key].1;
                vec![(b.lo, b.hi + 1)]
            }
        };
        let form = (runs, pair, idx);
        if dbg {
            eprintln!("cover acc {:04X} {:?} {:?} off {}", pc, pair, idx, off);
        }
        accs.push(Acc { key, form, off });
    }
    let doms = fl.dominators();
    let dominates = |a: u16, b: u16| {
        a != b
            && doms
                .dominators(b as u32)
                .is_some_and(|mut ds| ds.any(|d| d == a as u32))
    };
    let depth = |p: u16| doms.dominators(p as u32).map_or(0, Iterator::count);
    let mut forms: FxHashMap<Form, Vec<usize>> = FxHashMap::default();
    for (i, a) in accs.iter().enumerate() {
        forms.entry(a.form.clone()).or_default().push(i);
    }
    let mut groups: Vec<(Form, Vec<usize>)> = forms.into_iter().collect();
    groups.sort_unstable();
    for (form, mut ids) in groups {
        if ids.len() < 2 {
            continue;
        }
        ids.sort_by_key(|&i| (depth(accs[i].key.0), accs[i].key));
        let kills: FxHashSet<u16> = (form.1.iter().flat_map(|&(a, b)| [a, b]))
            .chain(form.2)
            .map(Name::at)
            .collect();
        // Whether every path on from `a` reaches `b` before a name of the form is defined
        // again or the routine leaves.
        let inevitable = |a: u16, b: u16| {
            let (mut seen, mut work) = (FxHashSet::default(), fl.succ[&a].clone());
            while let Some(p) = work.pop() {
                if p == b || !seen.insert(p) {
                    continue;
                }
                if kills.contains(&p) || fl.succ[&p].is_empty() {
                    return false;
                }
                work.extend(&fl.succ[&p]);
            }
            true
        };
        // The checks made, each with the hull its paths read and the accesses it covers.
        let mut made: Vec<(usize, (i32, i32), Vec<usize>)> = Vec::new();
        for &i in &ids {
            let (pc, off) = (accs[i].key.0, accs[i].off);
            let by = (made.iter_mut().rev())
                .find(|(d, w, _)| dominates(accs[*d].key.0, pc) && w.0 <= off && off <= w.1);
            if dbg {
                eprintln!("cover {:04X} off {} covered {}", pc, off, by.is_some());
            }
            if let Some((_, _, cov)) = by {
                cov.push(i);
                continue;
            }
            let (mut lo, mut hi) = (off, off);
            for &j in ids.iter().filter(|&&j| j != i) {
                if accs[j].key.0 != pc && inevitable(pc, accs[j].key.0) {
                    lo = lo.min(accs[j].off);
                    hi = hi.max(accs[j].off);
                }
            }
            made.push((i, (lo, hi), Vec::new()));
        }
        // Each check tests the hull of the offsets it covers.
        for (i, _, cov) in made {
            let off = accs[i].off;
            let lo = cov.iter().map(|&j| accs[j].off).fold(off, i32::min);
            let hi = cov.iter().map(|&j| accs[j].off).fold(off, i32::max);
            if lo < hi && !widen(out, accs[i].key, &form.0, lo - off, hi - lo) {
                continue;
            }
            for j in cov {
                let (_, b) = out.machine.remove(&accs[j].key).unwrap();
                out.covered.insert(accs[j].key, b);
            }
        }
    }
}

/// Load `key`'s check made of its address plus `lo` lying in one of the runs `rs` with `k`
/// bytes past it; false where no run holds so many.
fn widen(out: &mut Objs, key: StoreKey, rs: &[(u32, u32)], lo: i32, k: i32) -> bool {
    let runs: Vec<(u32, u32)> = (rs.iter())
        .filter_map(|&(a, b)| {
            let (a, b) = (a as i64 - lo as i64, b as i64 - lo as i64 - k as i64);
            (a < b && a >= 0).then_some((a as u32, b as u32))
        })
        .collect();
    let Some((first, last)) = runs.first().zip(runs.last()) else {
        return false;
    };
    let b = block_id(out, rs);
    let slack = (b, lo as u16, k as u16);
    let obj = match out.slack.iter().find(|e| *e.1 == slack) {
        Some(e) => *e.0,
        None => {
            out.objects
                .push((first.0 as u16, (last.1 - 1) as u16, Vec::new()));
            let id = out.objects.len() as u16 - 1;
            if runs.len() > 1 {
                out.parts.insert(id, runs.clone());
            }
            out.slack.insert(id, slack);
            id
        }
    };
    out.objects[obj as usize].2.push(key.0);
    let hull = Iv {
        lo: first.0,
        hi: last.1 - 1,
    };
    out.machine.insert(key, (obj, hull));
    true
}
