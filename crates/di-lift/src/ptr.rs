//! Zero-page pointer high bytes kept at least 2.
//!
//! An access through `(zp),Y` or `(zp,X)` has a full 64K address interval, but its address
//! is the pointer plus a byte, so it lies outside page 1 whenever the pointer's high byte is
//! at least 2. Guarding that byte instead of each access moves the check to where the byte
//! is defined: a store that may write it deopts after the instruction when it stores less
//! than 2, and an entry compares it before running unless every path to the accesses through
//! it stores it first (`vsa`). Between those points the byte can only be written by lifted
//! stores, whose address intervals are sound.
//!
//! A byte is protected when the accesses it frees outnumber the checks it costs (its stores
//! of values that may be below 2, and an entry check if some access reads it undefined).

use crate::analysis::{is_stack_op, Access, Facts, Graph, Iv};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};

/// The protected bytes of the program `g`. Stack accesses and accesses that reached page 1
/// in the facts are not candidates, nor are bytes that are volatile, code, or were stored
/// below 2 in the facts (their entry checks would fail).
pub fn protect(
    g: &Graph,
    facts: &Facts,
    volatile: &[(u32, u32)],
    roots: &[u16],
) -> (FixedBitSet, FxHashMap<u16, FixedBitSet>) {
    let mut usable = FixedBitSet::with_capacity(0x100);
    usable.insert_range(2..);
    for &(lo, hi) in volatile {
        usable.remove_range((lo as usize).min(0x100)..(hi as usize + 1).min(0x100));
    }
    for (a, v) in &facts.writes {
        if *a < 0x100 && v.iter().any(|&x| x < 2) {
            usable.set(*a as usize, false);
        }
    }
    for a in g.code.ones().take_while(|&a| a < 0x100) {
        usable.set(a, false);
    }
    let seen: FxHashSet<u16> = facts.page1.iter().map(|x| x.0).collect();
    let mut sites: Vec<(u16, Iv, bool)> = Vec::new();
    let mut cost = [0usize; 0x100];
    for (pc, vs) in &g.vars {
        for v in vs {
            let stack = is_stack_op(v.op);
            for a in v.acc.iter().filter(|a| !a.dummy && !stack) {
                match a.hi {
                    Some(r)
                        if a.addr.meets(0x100, 0x200)
                            && !seen.contains(pc)
                            && (r.lo..=r.hi).all(|i| usable.contains(i as usize)) =>
                    {
                        sites.push((*pc, r, a.defd))
                    }
                    _ => {}
                }
                if a.store && !a.back && a.val.lo < 2 {
                    for c in &mut cost
                        [a.addr.lo.min(0x100) as usize..(a.addr.hi + 1).min(0x100) as usize]
                    {
                        *c += 1;
                    }
                }
            }
        }
    }
    let mut prot = FixedBitSet::with_capacity(0x100);
    for (_, r, _) in &sites {
        prot.insert_range(r.lo as usize..r.hi as usize + 1);
    }
    loop {
        let (mut gain, mut open) = ([0usize; 0x100], [false; 0x100]);
        for (_, r, defd) in sites.iter().filter(|(_, r, _)| covered(r, &prot)) {
            for i in r.lo as usize..=r.hi as usize {
                gain[i] += 1;
                open[i] |= !defd;
            }
        }
        let drop: Vec<usize> = prot
            .ones()
            .filter(|&i| gain[i] <= cost[i] + open[i] as usize)
            .collect();
        if drop.is_empty() {
            let open: Vec<_> = sites
                .iter()
                .filter(|(_, r, defd)| !defd && covered(r, &prot))
                .collect();
            let pre = roots
                .iter()
                .enumerate()
                .map(|(i, &e)| {
                    let mut bytes = FixedBitSet::with_capacity(0x100);
                    for (_, r, _) in open
                        .iter()
                        .filter(|(pc, _, _)| g.from.get(pc).is_some_and(|f| f >> (i % 64) & 1 == 1))
                    {
                        bytes.insert_range(r.lo as usize..r.hi as usize + 1);
                    }
                    (e, bytes)
                })
                .collect();
            return (prot, pre);
        }
        for i in drop {
            prot.set(i, false);
        }
    }
}

fn covered(r: &Iv, prot: &FixedBitSet) -> bool {
    (r.lo..=r.hi).all(|a| prot.contains(a as usize))
}

/// The protected bytes a store with address interval `addr` may write, when the value it
/// stores may be below 2.
pub fn written(prot: &FixedBitSet, ac: &Access) -> Option<(u32, u32)> {
    if !ac.store || ac.back || ac.val.lo >= 2 {
        return None;
    }
    let mut hit = prot
        .ones()
        .filter(|&i| ac.addr.meets(i as u32, i as u32 + 1));
    let lo = hit.next()?;
    Some((lo as u32, hit.next_back().unwrap_or(lo) as u32))
}
