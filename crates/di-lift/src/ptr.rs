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
//!
//! The same windows keep a pointer clear of the volatile ranges (`zones`, widened to whole
//! pages): an access through a protected pair reaches neither page 1 nor a device, so it
//! makes no throw-away bus read.

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

/// Page 1, the zone every window keeps a pointer out of.
pub const PAGE1: (u32, u32) = (0x100, 0x200);

/// Whether the pointer `p` is in the window of index `m`: `p + i` may be in page 1 for some
/// `i <= m`.
pub fn inside(p: u32, m: u32) -> bool {
    inside_zone(p, m, PAGE1)
}

/// Whether `p + i` may lie in the zone `[lo, hi)` for some `i <= m`.
pub fn inside_zone(p: u32, m: u32, (lo, hi): (u32, u32)) -> bool {
    p.wrapping_sub(lo.wrapping_sub(m)) & 0xffff < hi - lo + m
}

/// Page 1 and the pages of the volatile ranges.
pub fn zones(volatile: &[(u32, u32)]) -> Vec<(u32, u32)> {
    let mut z = vec![PAGE1];
    z.extend(
        volatile
            .iter()
            .map(|&(lo, hi)| (lo & 0xff00, ((hi + 0xff) & !0xff).min(0x10000)))
            .filter(|&(lo, hi)| lo < hi),
    );
    z
}

/// The high bytes a pointer in the window `t` of some zone may have.
fn high_bytes(zones: &[(u32, u32)], t: u32) -> impl Iterator<Item = Iv> + '_ {
    zones.iter().map(move |&(lo, hi)| Iv {
        lo: (lo.wrapping_sub(t) & 0xffff) >> 8,
        hi: (hi - 1) >> 8,
    })
}

/// A store of a value in `v` to the high byte keeps the window `t` whatever the low byte.
fn hi_ok(zones: &[(u32, u32)], v: Iv, t: u32) -> bool {
    high_bytes(zones, t).all(|h| !v.meets(h.lo, h.hi + 1))
}

/// A store of a value in `v` to the high byte leaves the pointer in the window `t` of some
/// zone whatever the low byte: every page it names lies wholly in one.
fn hi_certain(zones: &[(u32, u32)], v: Iv, t: u32) -> bool {
    (v.lo..=v.hi.min(0xff)).all(|h| {
        zones
            .iter()
            .any(|&(lo, hi)| h << 8 >= lo.saturating_sub(t) && h < hi >> 8)
    })
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
    fn risky(&self, z: &[(u32, u32)], q: u8, t: u32) -> bool {
        self.hits(q.wrapping_add(1)) && !hi_ok(z, self.val, t) || self.hits(q) && !self.lo_ok(t)
    }
}

struct Site {
    pc: u16,
    m: u32,
    qs: Vec<u8>,
    defd: bool,
}

/// The pairs, windows and checks of program `g` for the `zones`. Stack accesses and
/// accesses that reached page 1 in the facts are not candidates, nor are pairs with a byte
/// that is volatile, code or a port register, or whose stored values in the facts would fail
/// their checks, or (with no facts) a store to whose high byte would always fail them.
pub fn protect(
    g: &Graph,
    facts: &Facts,
    volatile: &[(u32, u32)],
    zones: &[(u32, u32)],
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
    let mut tmax: Vec<Option<u32>> = (0..0x100usize)
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
    if facts.writes.is_empty() {
        // With no facts, a pair also read as data (not to dereference it, nor to step it in
        // place) may hold any value between its uses as a pointer: it is not protected.
        for v in g.vars.values().flatten().filter(|v| !is_stack_op(v.op)) {
            if v.acc.iter().any(|a| a.ptr.is_some()) {
                continue;
            }
            let rmw = |a: &crate::analysis::Access| {
                v.acc.iter().any(|b| b.store && b.back && b.addr == a.addr)
            };
            for a in v
                .acc
                .iter()
                .filter(|a| !a.store && !a.dummy && a.addr.lo < 0x100)
            {
                if !rmw(a) {
                    for b in a.addr.lo..=a.addr.hi.min(0xff) {
                        tmax[b as usize] = None;
                        tmax[(b as u8).wrapping_sub(1) as usize] = None;
                    }
                }
            }
        }
        // A window that a stored high byte would always fail is narrowed to the largest
        // index of a known pair, else given up.
        let high = g.vars.values().flatten().flat_map(|v| &v.acc);
        for a in high.filter(|a| a.store && a.addr.lo < 0x100) {
            for b in a.addr.lo..=a.addr.hi.min(0xff) {
                let q = (b as u8).wrapping_sub(1) as usize;
                let fit = |t: &u32| !hi_certain(zones, a.val, *t);
                tmax[q] = tmax[q].filter(fit).or(tmax[q].map(|_| 0xff).filter(fit));
            }
        }
    }
    let seen: FxHashSet<u16> = facts.page1.iter().map(|x| x.0).collect();
    let mut sites: Vec<Site> = Vec::new();
    let mut stores: Vec<Store> = Vec::new();
    let reach = |a: &crate::analysis::Access| zones.iter().any(|&(lo, hi)| a.addr.meets(lo, hi));
    for (&pc, vs) in &g.vars {
        for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
            for (ai, a) in v.acc.iter().enumerate().filter(|(_, a)| !a.dummy) {
                if let Some(p) = a.ptr.filter(|_| reach(a) && !seen.contains(&pc)) {
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
            .filter(|&s| defer.contains_key(&(s, q)) && stores[s].risky(zones, q, t))
            .collect();
        let forced: FxHashSet<usize> = deferred.iter().map(|&s| defer[&(s, q)]).collect();
        list.iter()
            .copied()
            .filter(|s| !deferred.contains(s))
            .filter(|&s| {
                let st = &stores[s];
                match forced.contains(&s) {
                    true if st.addr.value() == Some(q as u32) => true,
                    true => !hi_ok(zones, st.val, t),
                    false => st.risky(zones, q, t),
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
///
/// An indexed store is completed by one whose address is its own plus one through the same,
/// unchanged index register (`STA $21,X; ...; STA $22,X`).
fn completer(g: &Graph, index: &FxHashMap<StoreKey, usize>, s: &Store, q: u8) -> Option<usize> {
    let idx = index_regs(s.key.1);
    let other = match s.addr.value() {
        Some(e) if e == q as u32 => Some(q.wrapping_add(1)),
        Some(_) => Some(q),
        None if idx != 0 && s.addr.hi < 0xff => None,
        None => return None,
    };
    let next = Iv {
        lo: s.addr.lo + 1,
        hi: s.addr.hi + 1,
    };
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
                [i] if other.is_some() && v.acc[i].addr.value() == other.map(u32::from) => {
                    return index.get(&(pc, v.op, i)).copied();
                }
                [i] if other.is_none() && v.acc[i].addr == next && index_regs(v.op) == idx => {
                    return index.get(&(pc, v.op, i)).copied();
                }
                _ => return None,
            }
            if other.is_none() && writes_regs(v.op) & idx != 0 {
                return None;
            }
        }
        match v.flow {
            Flow::Next(n) => pc = n,
            _ => return None,
        }
    }
}

/// The index registers (bit 0 `X`, bit 1 `Y`) opcode `op`'s template reads.
fn index_regs(op: u8) -> u8 {
    regs(op, |o| o.ins.to_vec())
}

/// The index registers opcode `op`'s template writes.
fn writes_regs(op: u8) -> u8 {
    regs(op, |o| vec![o.out])
}

fn regs(op: u8, f: impl Fn(&di_pcode::ir::Op) -> Vec<di_pcode::ir::Vn>) -> u8 {
    use di_pcode::ir::Space;
    let ops = di_pcode::Template::of(op).ops();
    let vs = ops.iter().flat_map(f).filter(|v| v.space() == Space::Reg);
    vs.fold(0, |m, v| match v.off() as usize {
        di_pcode::reg::X => m | 1,
        di_pcode::reg::Y => m | 2,
        _ => m,
    })
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
        let io = (0xd000, 0xe000);
        for m in [0, 7, 0xff, FULL] {
            for p in 0..0x10000u32 {
                let reach =
                    (0..=m.min(0xff)).any(|i| (0xd000..0xe000).contains(&((p + i) & 0xffff)));
                assert!(inside_zone(p, m, io) || !reach, "{p:x} {m:x}");
            }
        }
    }

    #[test]
    fn stored_high_bytes_keep_windows_by_value() {
        let v = |lo, hi| Iv { lo, hi };
        let z = &[PAGE1];
        assert!(hi_ok(z, v(2, 0xff), FULL) && hi_ok(z, v(0, 0), 0));
        assert!(!hi_ok(z, v(0, 0), 1) && !hi_ok(z, v(1, 1), 0) && !hi_ok(z, v(0, 5), 0));
        let io = &zones(&[(0xd400, 0xd41d)]);
        assert_eq!(io, &[PAGE1, (0xd400, 0xd500)]);
        assert!(!hi_ok(io, v(0xd3, 0xd3), 1) && hi_ok(io, v(0xd3, 0xd3), 0));
        assert!(hi_ok(io, v(0xd5, 0xff), FULL) && !hi_ok(io, v(2, 0xff), FULL));
        assert!(hi_certain(io, v(0xd4, 0xd4), 0) && !hi_certain(io, v(0xd4, 0xd5), 0));
        assert!(hi_certain(z, v(0, 1), FULL) && !hi_certain(z, v(0, 0), 0xff));
        let st = |above, val| Store {
            key: (0, 0, 0),
            addr: Iv::exact(0x10),
            val,
            above,
        };
        assert!(st(false, Iv::full(1)).lo_ok(0) && st(false, Iv::full(1)).lo_ok(FULL));
        assert!(!st(false, v(0, 0x0f)).lo_ok(0x10));
        assert!(st(true, v(0, 0x0f)).lo_ok(0xf0) && !st(true, v(0, 0x0f)).lo_ok(0xf1));
        assert!(st(true, v(0, 0x0f)).risky(z, 0x0f, 0) && !st(true, v(2, 3)).risky(z, 0x0f, 0x80));
    }
}
