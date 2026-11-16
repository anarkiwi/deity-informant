//! Carried indices: a computed transfer switching on the value an index register held before
//! an instruction of a carry class wrote it ([`Index::Carry`], keyed by the class's least
//! write; a class holds the writes whose carried values joins meet, [`Graph::carried`]) reads
//! it from a register copied from the written register before each node of a write of the
//! class.
//!
//! The register is the transfer's function's own (numbered past its stack slots, never
//! machine state) where its own copies are the last writes the value can come from: every
//! path from its entry to the transfer passes a write of the class, and no function it calls,
//! directly or not, runs one. Otherwise it is a register of the file ([`crate::ir::FCARRY`]), passed
//! through calls as the rest of the file is and copied before the class's writes in every
//! function running one, where the transfer's function is on no call cycle and calls no
//! function running a write of the class, and no function running one is on a call cycle or
//! an instance function; else the transfer switches over code addresses. An instance
//! function holds only one-write classes in its own registers.

use crate::analysis::Graph;
use crate::dispatch::Index;
use crate::func::{Key, Plan, H};
use crate::ir::NFCARRY;
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

/// The writes of the carry class keyed `(cp, r)`: node starts and registers.
fn members(g: &Graph, p: &Plan, (cp, r): (u16, u8)) -> Vec<(u16, u8)> {
    match (&p.inst, g.carried.get(&(cp, r))) {
        (None, Some(m)) => m.clone(),
        _ => vec![(cp, r)],
    }
}

/// The carried selectors of `p`'s transfers: transfer node start and class key.
fn wanted(g: &Graph, p: &Plan) -> BTreeSet<(u16, (u16, u8))> {
    let ix = |pc: u16, i: Index| match i {
        Index::Carry(cp, r) => Some((pc, (cp, r))),
        _ => None,
    };
    match &p.inst {
        Some(x) => (x.sel.iter()).filter_map(|(&pc, s)| ix(pc, s.0)).collect(),
        None => (p.pcs())
            .filter_map(|pc| ix(pc, g.sels.get(&pc)?.0))
            .collect(),
    }
}

/// Whether every path from `p`'s entries to a node at `at` passes a node at one of `cps`.
fn must(p: &Plan, cps: &[(u16, u8)], at: u16) -> bool {
    let mut seen: FxHashSet<Key> = FxHashSet::default();
    let mut work: Vec<Key> = (p.entry.iter()).map(|&c| (p.pc, H::Known(0), c)).collect();
    while let Some(k) = work.pop() {
        if cps.iter().any(|m| m.0 == k.0) || !seen.insert(k) {
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

/// The register a carried transfer reads: the `i`th of its function's, or of the file's.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reg {
    Local(u16),
    File(u16),
}

/// The carries a function holds: the writes `(node start, register)` copied into each of its
/// carry registers, the copies it makes into file carry registers (node start, register,
/// file carry), and the register each transfer it holds a carry for reads, by transfer node
/// start.
#[derive(Clone, Debug, Default)]
pub struct Carries {
    pub regs: Vec<Vec<(u16, u8)>>,
    pub file: Vec<(u16, u8, u16)>,
    pub at: FxHashMap<u16, Reg>,
}

/// Per plan, the carries it holds.
pub fn alloc(
    g: &Graph,
    plans: &[Plan],
    callees: &[Vec<u32>],
    recursive: &FixedBitSet,
) -> Vec<Carries> {
    let runs: Vec<FxHashSet<u16>> = plans.iter().map(starts).collect();
    let below = |i: usize| {
        let mut seen = FixedBitSet::with_capacity(plans.len());
        let mut work = callees[i].clone();
        let mut out: FxHashSet<u16> = FxHashSet::default();
        while let Some(c) = work.pop() {
            if !seen.put(c as usize) {
                out.extend(runs[c as usize].iter());
                work.extend(&callees[c as usize]);
            }
        }
        out
    };
    let mut out = vec![Carries::default(); plans.len()];
    let mut files: Vec<(u16, u8)> = Vec::new();
    let mut far: Vec<(usize, u16, (u16, u8))> = Vec::new();
    for (i, p) in plans.iter().enumerate() {
        let want = wanted(g, p);
        if want.is_empty() {
            continue;
        }
        let below = below(i);
        let mut local: Vec<(u16, (u16, u8))> = Vec::new();
        for (at, key) in want {
            let ms = members(g, p, key);
            let cps: Vec<u16> = ms.iter().flat_map(|m| copied(p, m.0)).collect();
            let clear = cps.iter().all(|q| !below.contains(q));
            if clear && cps.iter().any(|q| runs[i].contains(q)) && must(p, &ms, at) {
                local.push((at, key));
            } else if clear && p.inst.is_none() && !recursive.contains(i) {
                far.push((i, at, key));
            }
        }
        let keys: Vec<(u16, u8)> = (local.iter().map(|w| w.1))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let c = &mut out[i];
        c.regs = keys.iter().map(|&k| members(g, p, k)).collect();
        c.at = (local.iter())
            .map(|w| (w.0, Reg::Local(keys.binary_search(&w.1).unwrap() as u16)))
            .collect();
    }
    // A file carry per class a transfer of another function than its writes' needs, while the
    // functions running its writes are on no call cycle and are no instance functions.
    let held = |key: (u16, u8)| {
        let ms = g.carried.get(&key).cloned().unwrap_or_else(|| vec![key]);
        let by: Vec<usize> = (0..plans.len())
            .filter(|&j| ms.iter().any(|m| runs[j].contains(&m.0)))
            .collect();
        (by.iter()
            .all(|&j| plans[j].inst.is_none() && !recursive.contains(j)))
        .then_some((ms, by))
    };
    let mut bad: FxHashSet<(u16, u8)> = FxHashSet::default();
    far.sort_unstable_by_key(|w| w.2);
    for (i, at, key) in far {
        let j = match files.binary_search(&key) {
            Ok(j) => j,
            Err(_) if files.len() >= NFCARRY || bad.contains(&key) => continue,
            Err(_) => {
                let Some((ms, by)) = held(key) else {
                    bad.insert(key);
                    continue;
                };
                let f = files.len() as u16;
                for b in by {
                    (out[b].file).extend(ms.iter().map(|m| (m.0, m.1, f)));
                }
                files.push(key);
                f as usize
            }
        };
        out[i].at.insert(at, Reg::File(j as u16));
    }
    out
}
