//! Zero-page pointers kept clear of page 1.
//!
//! An access through `(zp),Y` or `(zp,X)` has a full 64K address interval, but its address
//! is a zero-page pointer `p = mem[q] | mem[q + 1] << 8` plus an index `i <= m`, which is
//! outside page 1 whenever `p` is outside the window `[$100 - m, $1FF]`: below it the sum
//! stays in page 0, from `$200` up it stays at or above `$200` or wraps past `$FFFF` into
//! page 0. Guarding the pair instead of each access moves the check to where the pair is
//! defined: a store that may write either byte and may leave the pointer in its window reads
//! the pair back after its instruction and deopts, and an entry checks the pairs some access
//! it reaches reads before every path to it stores their high byte (`vsa`). Between those
//! points the pair can only be written by lifted stores, whose address intervals are sound.
//!
//! A pair defined by two stores on one straight instruction chain (`STA q; LDA hi,X; STA
//! q+1`) with nothing between them that reads through the pair or may store into it is read
//! back once, after the second: the pointer between them is transient.
//!
//! A pair is protected, with a window serving the largest index of the accesses it frees,
//! when those accesses outnumber the checks it costs.

use crate::analysis::{is_stack_op, Facts, Flow, Graph, Iv, Win};
use crate::bank::BankMap;
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};

/// The window of an index that may be any byte or a pointer whose low byte is unknown: the
/// high byte is at least 2.
pub const FULL: u32 = 0x100;

/// A store by instruction start, opcode and access index.
pub type StoreKey = (u16, u8, usize);

/// The protected pairs and the checks keeping them.
#[derive(Clone, Debug)]
pub struct Prot {
    pub win: Win,
    /// Per entry, the pairs (by low-byte address) the entry checks.
    pub pre: FxHashMap<u16, FixedBitSet>,
    /// Per store, the pairs it reads back after its instruction.
    pub chk: FxHashMap<StoreKey, Vec<u8>>,
}

impl Default for Prot {
    fn default() -> Self {
        Prot {
            win: [None; 0x100],
            pre: FxHashMap::default(),
            chk: FxHashMap::default(),
        }
    }
}

/// Whether the pointer `p` is in the window of index `m`: `p + i` may be in page 1 for some
/// `i <= m`.
pub fn inside(p: u32, m: u32) -> bool {
    p.wrapping_sub(0x100 - m) & 0xffff < 0x100 + m
}

fn has(v: Iv, x: u32) -> bool {
    v.lo <= x && x <= v.hi
}

/// A store of a value in `v` to the high byte keeps the window `t` whatever the low byte.
fn hi_ok(v: Iv, t: u32) -> bool {
    !has(v, 1) && !(has(v, 0) && t > 0)
}

struct Store {
    key: StoreKey,
    addr: Iv,
    val: Iv,
    above: bool,
}

impl Store {
    fn hits(&self, a: u8) -> bool {
        self.addr.meets(a as u32, a as u32 + 1)
    }
    /// A store of a low byte keeps the window `t` of a pair that held it.
    fn lo_ok(&self, t: u32) -> bool {
        t == 0 || t == FULL || self.above && self.addr.value().is_some() && self.val.hi + t < 0x100
    }
    /// The store may leave pair `q` in window `t`, the pair holding it before.
    fn risky(&self, q: u8, t: u32) -> bool {
        self.hits(q.wrapping_add(1)) && !hi_ok(self.val, t) || self.hits(q) && !self.lo_ok(t)
    }
}

struct Site {
    pc: u16,
    m: u32,
    qs: Vec<u8>,
    defd: bool,
}

/// The pairs, windows and checks of program `g`. Stack accesses and accesses that reached
/// page 1 in the facts are not candidates, nor are pairs with a byte that is volatile, code
/// or a port register, or whose stored values in the facts would fail their checks.
pub fn protect(
    g: &Graph,
    facts: &Facts,
    volatile: &[(u32, u32)],
    roots: &[u16],
    bank: Option<&BankMap>,
) -> Prot {
    let mut usable = FixedBitSet::with_capacity(0x100);
    usable.insert_range(..);
    for &(lo, hi) in volatile {
        usable.remove_range((lo as usize).min(0x100)..(hi as usize).min(0x100));
    }
    for a in g.code.ones().take_while(|&a| a < 0x100) {
        usable.set(a, false);
    }
    if let Some(m) = bank {
        for a in [m.dir, m.data].into_iter().filter(|&a| a < 0x100) {
            usable.set(a as usize, false);
        }
    }
    let mut seen_lo = [0u32; 0x100];
    let mut seen_hi = [(false, false); 0x100];
    for (a, v) in facts.writes.iter().filter(|w| w.0 < 0x100) {
        let a = *a as usize;
        seen_lo[a] = v.iter().fold(seen_lo[a], |m, &x| m.max(x as u32));
        seen_hi[a].0 |= v.contains(&0);
        seen_hi[a].1 |= v.contains(&1);
    }
    // The largest window the facts' values keep, per pair.
    let tmax: Vec<Option<u32>> = (0..0x100usize)
        .map(|q| {
            let h = (q + 1) & 0xff;
            match seen_hi[h] {
                _ if !usable.contains(q) || !usable.contains(h) => None,
                (_, true) => None,
                (true, _) => Some(0xff - seen_lo[q]),
                _ => Some(FULL),
            }
        })
        .collect();
    let seen: FxHashSet<u16> = facts.page1.iter().map(|x| x.0).collect();
    let mut sites: Vec<Site> = Vec::new();
    let mut stores: Vec<Store> = Vec::new();
    for (&pc, vs) in &g.vars {
        for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
            for (ai, a) in v.acc.iter().enumerate().filter(|(_, a)| !a.dummy) {
                if let Some(p) = a
                    .ptr
                    .filter(|_| a.addr.meets(0x100, 0x200) && !seen.contains(&pc))
                {
                    let (qs, m) = p.need();
                    let qs: Vec<u8> = qs.collect();
                    if qs.iter().all(|&q| tmax[q as usize].is_some_and(|t| t >= m)) {
                        sites.push(Site {
                            pc,
                            m,
                            qs,
                            defd: a.defd,
                        });
                    }
                }
                if a.store && !a.back && a.addr.lo < 0x100 {
                    stores.push(Store {
                        key: (pc, v.op, ai),
                        addr: a.addr,
                        val: a.val,
                        above: a.above,
                    });
                }
            }
        }
    }
    let mut at: Vec<Vec<usize>> = vec![Vec::new(); 0x100];
    for (i, s) in sites.iter().enumerate() {
        for &q in &s.qs {
            at[q as usize].push(i);
        }
    }
    let mut sq: Vec<Vec<usize>> = vec![Vec::new(); 0x100];
    for (i, s) in stores.iter().enumerate() {
        let (lo, hi) = (s.addr.lo, s.addr.hi.min(0xff));
        let mut qs: Vec<u8> = (lo..=hi)
            .flat_map(|b| [b as u8, (b as u8).wrapping_sub(1)])
            .collect();
        qs.sort_unstable();
        qs.dedup();
        for q in qs.into_iter().filter(|&q| !at[q as usize].is_empty()) {
            sq[q as usize].push(i);
        }
    }
    let index: FxHashMap<StoreKey, usize> =
        stores.iter().enumerate().map(|(i, s)| (s.key, i)).collect();
    let mut defer: FxHashMap<(usize, u8), usize> = FxHashMap::default();
    for (q, list) in sq.iter().enumerate() {
        for &s in list {
            if let Some(c) = completer(g, &index, &stores[s], q as u8) {
                defer.insert((s, q as u8), c);
            }
        }
    }
    let needs = |q: u8, t: u32| -> Vec<usize> {
        let list = &sq[q as usize];
        let deferred: Vec<usize> = list
            .iter()
            .copied()
            .filter(|&s| defer.contains_key(&(s, q)) && stores[s].risky(q, t))
            .collect();
        let forced: FxHashSet<usize> = deferred.iter().map(|&s| defer[&(s, q)]).collect();
        list.iter()
            .copied()
            .filter(|s| !deferred.contains(s))
            .filter(|&s| {
                let st = &stores[s];
                match forced.contains(&s) {
                    true if st.addr.value() == Some(q as u32) => true,
                    true => !hi_ok(st.val, t),
                    false => st.risky(q, t),
                }
            })
            .collect()
    };
    let mut win: [Option<u32>; 0x100] = [None; 0x100];
    for (q, list) in at.iter().enumerate().filter(|(_, l)| !l.is_empty()) {
        let top = list.iter().map(|&i| sites[i].m).max();
        win[q] = tmax[q].filter(|&t| t == FULL).or(top);
    }
    loop {
        let mut next = win;
        for q in (0..0x100).filter(|&q| win[q].is_some()) {
            let cur = win[q].unwrap();
            let cands: Vec<&Site> = at[q]
                .iter()
                .map(|&i| &sites[i])
                .filter(|s| {
                    s.m <= cur
                        && s.qs
                            .iter()
                            .all(|&r| r as usize == q || win[r as usize].is_some_and(|t| t >= s.m))
                })
                .collect();
            let mut opts: Vec<u32> = cands.iter().map(|s| s.m).collect();
            opts.extend((cur == FULL).then_some(FULL));
            opts.sort_unstable();
            opts.dedup();
            let mut best: Option<(usize, u32)> = None;
            for t in opts {
                let served = cands.iter().filter(|s| s.m <= t);
                let gain = served.clone().count();
                let cost = needs(q as u8, t).len() + served.clone().any(|s| !s.defd) as usize;
                if gain > cost && best.is_none_or(|(n, _)| gain - cost >= n) {
                    best = Some((gain - cost, t));
                }
            }
            next[q] = best.map(|b| b.1);
        }
        if next == win {
            break;
        }
        win = next;
    }
    let covered = |s: &Site| {
        s.qs.iter()
            .all(|&q| win[q as usize].is_some_and(|t| t >= s.m))
    };
    let mut prot = Prot::default();
    for (i, &e) in roots.iter().enumerate() {
        let mut pairs = FixedBitSet::with_capacity(0x100);
        for s in sites.iter().filter(|s| !s.defd && covered(s)) {
            if g.from.get(&s.pc).is_some_and(|f| f >> (i % 64) & 1 == 1) {
                pairs.extend(s.qs.iter().map(|&q| q as usize));
            }
        }
        prot.pre.insert(e, pairs);
    }
    for (q, t) in win
        .iter()
        .enumerate()
        .filter_map(|(q, t)| t.map(|t| (q, t)))
    {
        prot.win[q] = Some(t as u16);
        for s in needs(q as u8, t) {
            prot.chk.entry(stores[s].key).or_default().push(q as u8);
        }
    }
    prot
}

/// The store completing pair `q` after store `s` on its straight instruction chain, when
/// nothing between them reads through the pair or may store into it.
fn completer(g: &Graph, index: &FxHashMap<StoreKey, usize>, s: &Store, q: u8) -> Option<usize> {
    let e = s.addr.value()?;
    let other = if e == q as u32 { q.wrapping_add(1) } else { q };
    let mut pc = s.key.0;
    let mut visited = FxHashSet::default();
    loop {
        let [v] = &g.vars.get(&pc)?[..] else {
            return None;
        };
        if g.cells.contains(pc as usize) || !visited.insert(pc) {
            return None;
        }
        let through = v
            .acc
            .iter()
            .any(|a| !a.dummy && a.ptr.is_some_and(|p| p.need().0.any(|r| r == q)));
        let hits: Vec<usize> = v
            .acc
            .iter()
            .enumerate()
            .filter(|(_, a)| {
                a.store
                    && !a.back
                    && !is_stack_op(v.op)
                    && [q, q.wrapping_add(1)]
                        .iter()
                        .any(|&b| a.addr.meets(b as u32, b as u32 + 1))
            })
            .map(|(i, _)| i)
            .collect();
        if through {
            return None;
        }
        if pc == s.key.0 {
            if hits != [s.key.2] {
                return None;
            }
        } else {
            match hits[..] {
                [] => {}
                [i] if v.acc[i].addr.value() == Some(other as u32) => {
                    return index.get(&(pc, v.op, i)).copied();
                }
                _ => return None,
            }
        }
        match v.flow {
            Flow::Next(n) => pc = n,
            _ => return None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outside_the_window_every_index_misses_page_1() {
        for m in (0..=FULL).step_by(17).chain([1, 0xff, FULL]) {
            for p in 0..0x10000u32 {
                let reach = (0..=m.min(0xff)).any(|i| ((p + i) & 0xffff) >> 8 == 1)
                    || m == FULL && p >> 8 == 0;
                assert!(inside(p, m) || !reach, "{p:x} {m:x}");
            }
        }
        assert!(inside(0x1ff, 0) && !inside(0xff, 0) && !inside(0, 0));
        assert!(inside(0xf0, 0x10) && !inside(0xef, 0x10) && !inside(0x200, 0xff));
        assert!(inside(0, FULL) && !inside(0x200, FULL));
    }

    #[test]
    fn stored_high_bytes_keep_windows_by_value() {
        let v = |lo, hi| Iv { lo, hi };
        assert!(hi_ok(v(2, 0xff), FULL) && hi_ok(v(0, 0), 0));
        assert!(!hi_ok(v(0, 0), 1) && !hi_ok(v(1, 1), 0) && !hi_ok(v(0, 5), 0));
        let st = |above, val| Store {
            key: (0, 0, 0),
            addr: Iv::exact(0x10),
            val,
            above,
        };
        assert!(st(false, Iv::full(1)).lo_ok(0) && st(false, Iv::full(1)).lo_ok(FULL));
        assert!(!st(false, v(0, 0x0f)).lo_ok(0x10));
        assert!(st(true, v(0, 0x0f)).lo_ok(0xf0) && !st(true, v(0, 0x0f)).lo_ok(0xf1));
        assert!(st(true, v(0, 0x0f)).risky(0x0f, 0) && !st(true, v(2, 3)).risky(0x0f, 0x80));
    }
}
