//! Carried indices: a computed transfer switching on the value an index register held before
//! the instruction at `cp` wrote it ([`Index::Carry`]) reads it from a register of its
//! function, copied from the index register before each node at `cp`. The register is the
//! function's own (numbered past its stack slots, never machine state), so a function holds a
//! carry only where its own copies are the last writes the value can come from: every path
//! from its entry to the transfer passes a node at `cp`, and no function it calls, directly or
//! not, runs the instruction at `cp`.

use crate::analysis::Graph;
use crate::dispatch::Index;
use crate::func::{Key, Plan, H};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::BTreeSet;

/// The machine instruction starts plan `p` runs (each copy's of an instance function).
fn starts(p: &Plan) -> FxHashSet<u16> {
    match &p.inst {
        Some(x) => (x.c.nodes.iter())
            .flat_map(|n| n.pcs.iter().flatten().copied())
            .collect(),
        None => p.pcs().collect(),
    }
}

/// The machine instruction starts a carry copied at node start `cp` of `p` stands for.
fn copied(p: &Plan, cp: u16) -> Vec<u16> {
    match &p.inst {
        Some(x) => x.node.get(&cp).map_or(Vec::new(), |&i| {
            x.c.nodes[i as usize]
                .pcs
                .iter()
                .flatten()
                .copied()
                .collect()
        }),
        None => vec![cp],
    }
}

/// The carried selectors of `p`'s transfers: transfer node start, `cp` and register.
fn wanted(g: &Graph, p: &Plan) -> BTreeSet<(u16, u16, u8)> {
    let ix = |pc: u16, i: Index| match i {
        Index::Carry(cp, r) => Some((pc, cp, r)),
        _ => None,
    };
    match &p.inst {
        Some(x) => (x.sel.iter()).filter_map(|(&pc, s)| ix(pc, s.0)).collect(),
        None => (p.pcs())
            .filter_map(|pc| ix(pc, g.sels.get(&pc)?.0))
            .collect(),
    }
}

/// Whether every path from `p`'s entries to a node at `at` passes a node at `cp`.
fn must(p: &Plan, cp: u16, at: u16) -> bool {
    let mut seen: FxHashSet<Key> = FxHashSet::default();
    let mut work: Vec<Key> = (p.entry.iter()).map(|&c| (p.pc, H::Known(0), c)).collect();
    while let Some(k) = work.pop() {
        if k.0 == cp || !seen.insert(k) {
            continue;
        }
        if k.0 == at {
            return false;
        }
        match (p.nodes.get(&k), p.insts.get(&k)) {
            (Some(n), _) => work.extend(n.arms.iter().flat_map(|a| a.succ.iter().copied())),
            (None, Some(c)) => work.extend(c.exits.iter().copied()),
            _ => {}
        }
    }
    true
}

/// The carries a function holds: `(cp, index register)` per carry register, ascending, and
/// the carry register each transfer it holds a carry for reads, by transfer node start.
#[derive(Clone, Debug, Default)]
pub struct Carries {
    pub regs: Vec<(u16, u8)>,
    pub at: FxHashMap<u16, u16>,
}

/// Per plan, the carries it holds.
pub fn alloc(g: &Graph, plans: &[Plan], callees: &[Vec<u32>]) -> Vec<Carries> {
    let mut runs: Vec<Option<FxHashSet<u16>>> = vec![None; plans.len()];
    (0..plans.len())
        .map(|i| {
            let want = wanted(g, &plans[i]);
            if want.is_empty() {
                return Carries::default();
            }
            let mut seen = FixedBitSet::with_capacity(plans.len());
            let mut work = callees[i].clone();
            let mut below: FxHashSet<u16> = FxHashSet::default();
            while let Some(c) = work.pop() {
                if !seen.put(c as usize) {
                    let s = runs[c as usize].get_or_insert_with(|| starts(&plans[c as usize]));
                    below.extend(s.iter());
                    work.extend(&callees[c as usize]);
                }
            }
            let ok: Vec<(u16, u16, u8)> = (want.into_iter())
                .filter(|&(at, cp, _)| {
                    let cps = copied(&plans[i], cp);
                    !cps.is_empty()
                        && cps.iter().all(|q| !below.contains(q))
                        && must(&plans[i], cp, at)
                })
                .collect();
            let regs: Vec<(u16, u8)> = (ok.iter().map(|w| (w.1, w.2)))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            let at = (ok.iter())
                .map(|w| (w.0, regs.binary_search(&(w.1, w.2)).unwrap() as u16))
                .collect();
            Carries { regs, at }
        })
        .collect()
}
