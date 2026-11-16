//! Pointer objects: the bounds checks of the loads through a zero-page pointer pair hoisted to
//! the definitions of the pair.
//!
//! A load through `(q),Y` reads `p + y` for the pointer `p = mem[q] | mem[q + 1] << 8`. The
//! pointer reach pass bounds `p` (the load's base hull) only while table loads stay inside
//! their extents, which a run need not keep. The definitions of a pair are the stores that may
//! write either byte and, where a path from an entry reaches a load before any store or other
//! load, that load (the pointer the host leaves). Definitions sharing a load, and the loads
//! they reach, are one *object*: from the least base of the loads' hulls (a data byte) to the
//! greatest, up to the end of the last run of data bytes between them that the host holds. A
//! store definition reads the pair back after its instruction, a load definition before it,
//! and deopts when the pointer lies outside the object. Between those points only lifted
//! stores, whose address intervals are sound, may write the pair, so a load of the object
//! reads the object plus its index on every run: its proven hull, kept for the loads within
//! the object's fields (the largest index a load adds as one value; one reading until its data
//! says stop keeps its own check) whose hull lies in the bytes the host holds ([`held`]).
//!
//! Two stores of a pair on one straight instruction chain read it back once, after the second,
//! as pointer windows do ([`crate::ptr`]). A store of the low byte that leaves the high byte
//! to a flag ([`settled`]: `STA q; BCC l; INC q + 1` after an add, `INC q; BNE l; INC q + 1`)
//! checks the pointer plus the flag times 256: what the pair holds once the increment settles
//! it, so the increment needs no check.
//!
//! An object is made when the loads it serves outnumber its checks.

use crate::analysis::{flow_succs, is_stack_op, Flow, Graph, Iv, Var};
use crate::bank::BankMap;
use crate::ptr::{completer, usable, zp_stores, StoreKey};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};

/// A check of pair `q`'s pointer against object `obj` ([`Objs::objects`]): with `flag` (a
/// flag register), of the pointer plus the flag times 256.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ObjChk {
    pub q: u8,
    pub obj: u16,
    pub flag: Option<u8>,
}

/// The pointer objects of a program and the checks keeping them.
#[derive(Clone, Debug, Default)]
pub struct Objs {
    /// The objects `[lo, hi]` a checked pointer lies in, and the instruction starts of the
    /// loads each serves.
    pub objects: Vec<(u16, u16, Vec<u16>)>,
    /// Per store, the checks after its instruction.
    pub chk: FxHashMap<StoreKey, Vec<ObjChk>>,
    /// Per instruction start, the checks before it of the pairs its loads read through.
    pub pre: FxHashMap<u16, Vec<ObjChk>>,
}

/// A load through a pair: its instruction, access, the values of `Y` it adds and its base
/// hull (none without one).
struct Load {
    pc: u16,
    key: StoreKey,
    y: Iv,
    base: Option<Iv>,
}

/// A definition of a pair: a store (by index into the zero-page stores) that replaces both
/// bytes for the loads after it (`kill`) or may, the loads at an instruction start checking
/// the pair before it, or the entries.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Def {
    Entry,
    Load(u16),
    Store {
        s: usize,
        kill: bool,
        flag: Option<u8>,
    },
}

/// Union-find over definitions.
fn find(up: &mut [usize], mut i: usize) -> usize {
    while up[i] != i {
        up[i] = up[up[i]];
        i = up[i];
    }
    i
}

/// The control flow the definitions reach along: every flow edge, and from each return whose
/// targets are not computed to every return site.
struct Cfg<'a> {
    g: &'a Graph,
    rets: Vec<u16>,
    preds: FxHashMap<u16, Vec<u16>>,
}

impl<'a> Cfg<'a> {
    fn new(g: &'a Graph) -> Self {
        let mut preds: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
        let mut rets = Vec::new();
        for (&pc, vs) in &g.vars {
            for v in vs {
                for t in flow_succs(&v.flow) {
                    preds.entry(t).or_default().push(pc);
                }
                match &v.flow {
                    Flow::Jsr { ret, .. } => rets.push(*ret),
                    Flow::Brk(_) => rets.push(pc.wrapping_add(2)),
                    _ => {}
                }
            }
        }
        rets.sort_unstable();
        rets.dedup();
        Cfg { g, rets, preds }
    }
    fn succs(&self, pc: u16) -> Vec<u16> {
        let mut s = Vec::new();
        for v in self.g.vars.get(&pc).into_iter().flatten() {
            s.extend(flow_succs(&v.flow));
            if matches!(&v.flow, Flow::Rts(t) | Flow::Rti(t) if t.is_empty()) {
                s.extend(&self.rets);
            }
        }
        s
    }
    /// The definitions reaching each instruction start (before it) from `roots`, each start's
    /// definitions `at` (a load's before its instruction's stores).
    fn reaching(
        &self,
        roots: &[u16],
        defs: &[Def],
        at: &FxHashMap<u16, Vec<usize>>,
    ) -> FxHashMap<u16, FixedBitSet> {
        let empty = FixedBitSet::with_capacity(defs.len());
        let mut ins: FxHashMap<u16, FixedBitSet> = FxHashMap::default();
        let mut work: Vec<u16> = roots.to_vec();
        for &r in roots {
            ins.entry(r).or_insert_with(|| empty.clone()).insert(0);
        }
        let mut queued: FxHashSet<u16> = work.iter().copied().collect();
        while let Some(pc) = work.pop() {
            queued.remove(&pc);
            let mut o = ins.get(&pc).unwrap_or(&empty).clone();
            transfer(defs, at.get(&pc), &mut o, false);
            for t in self.succs(pc) {
                if !self.g.vars.contains_key(&t) {
                    continue;
                }
                let e = ins.entry(t).or_insert_with(|| empty.clone());
                if !o.is_subset(e) {
                    e.union_with(&o);
                    if queued.insert(t) {
                        work.push(t);
                    }
                }
            }
        }
        ins
    }
}

/// Apply the definitions `ds` of an instruction start to the set `o` reaching it: its loads'
/// alone with `loads`, else all of them.
fn transfer(defs: &[Def], ds: Option<&Vec<usize>>, o: &mut FixedBitSet, loads: bool) {
    for &d in ds.into_iter().flatten() {
        match defs[d] {
            Def::Load(_) => {
                o.clear();
                o.insert(d);
            }
            Def::Store { kill, .. } if !loads => {
                if kill {
                    o.clear();
                }
                o.insert(d);
            }
            _ => {}
        }
    }
}

/// The proven hulls of loads by access, with their bases' (as [`crate::vsa::Hulls`]).
pub type Proven = Vec<(StoreKey, (Iv, Iv))>;

/// The bytes `[lo, hi)` the host holds of a program `loaded`: it, and the bytes past it below
/// the volatile ranges that the lift's stores (`written`) or the host (`holds`) write.
pub fn held(
    loaded: Option<(u32, u32)>,
    written: &FixedBitSet,
    holds: &[(u32, u32)],
    volatile: &[(u32, u32)],
) -> Option<(u32, u32)> {
    let (lo, hi) = loaded?;
    let below = (volatile.iter().map(|v| v.0))
        .filter(|&a| a >= hi)
        .min()
        .unwrap_or(0x10000);
    let past = (written.ones().map(|a| (a as u32, a as u32 + 1)))
        .chain(holds.iter().copied())
        .filter(|&(a, b)| a >= hi && b <= below)
        .map(|r| r.1);
    Some((lo, past.fold(hi, u32::max)))
}

/// The objects of `g` with entries `roots`, and the proven hulls of the loads they serve. A
/// load is served where its proven hull lies in the bytes `held` ([`held`]) and meets no
/// volatile range.
pub fn objects(
    g: &Graph,
    roots: &[u16],
    volatile: &[(u32, u32)],
    bank: Option<&BankMap>,
    held: Option<(u32, u32)>,
) -> (Objs, Proven) {
    let mut out = Objs::default();
    let mut proven = Vec::new();
    let Some((lo0, end)) = held.filter(|_| !g.runs.is_empty() && !roots.is_empty()) else {
        return (out, proven);
    };
    let held = |h: Iv| lo0 <= h.lo && h.hi < end && !volatile.iter().any(|&(a, b)| h.meets(a, b));
    let loads = loads(g, volatile, bank);
    if loads.is_empty() {
        return (out, proven);
    }
    let stores = zp_stores(g);
    let index: FxHashMap<StoreKey, usize> =
        stores.iter().enumerate().map(|(i, s)| (s.key, i)).collect();
    let cfg = Cfg::new(g);
    let mut pairs: Vec<u8> = loads.keys().copied().collect();
    pairs.sort_unstable();
    for q in pairs {
        let reads = &loads[&q];
        let hits: Vec<usize> = (0..stores.len())
            .filter(|&i| stores[i].addr.meets(q as u32, q as u32 + 2))
            .collect();
        let mut quiet = FxHashSet::default();
        let mut flags = FxHashMap::default();
        for &s in &hits {
            if completer(g, &index, &stores[s], q).is_some() {
                quiet.insert(s);
            } else if let Some((i, f)) = settled(g, &index, &cfg.preds, roots, &stores[s].key, q) {
                flags.insert(s, f);
                quiet.insert(i);
            }
        }
        let mut defs = vec![Def::Entry];
        let mut at: FxHashMap<u16, Vec<usize>> = FxHashMap::default();
        for &s in hits.iter().filter(|s| !quiet.contains(s)) {
            let st = &stores[s];
            let one = g.vars.get(&st.key.0).is_some_and(|vs| vs.len() == 1);
            at.entry(st.key.0).or_default().push(defs.len());
            defs.push(Def::Store {
                s,
                kill: one && st.addr.value().is_some(),
                flag: flags.get(&s).copied(),
            });
        }
        // The loads an entry reaches before any store or other load check the pair.
        let mut pcs: Vec<u16> = reads.iter().map(|l| l.pc).collect();
        pcs.sort_unstable();
        pcs.dedup();
        let every = |defs: &mut Vec<Def>, at: &mut FxHashMap<u16, Vec<usize>>, pcs: &[u16]| {
            for &pc in pcs {
                at.entry(pc).or_default().insert(0, defs.len());
                defs.push(Def::Load(pc));
            }
        };
        let (mut d1, mut a1) = (defs.clone(), at.clone());
        every(&mut d1, &mut a1, &pcs);
        let ins = cfg.reaching(roots, &d1, &a1);
        pcs.retain(|pc| ins.get(pc).is_some_and(|i| i.contains(0)));
        every(&mut defs, &mut at, &pcs);
        let ins = cfg.reaching(roots, &defs, &at);
        let seen = |pc: u16| {
            let mut i = ins.get(&pc)?.clone();
            transfer(&defs, at.get(&pc), &mut i, true);
            Some(i)
        };
        let n = defs.len();
        let mut up: Vec<usize> = (0..n).collect();
        let mut by: Vec<(usize, &Load)> = Vec::new();
        for l in reads {
            let Some(i) = seen(l.pc) else {
                continue;
            };
            let mut it = i.ones();
            let Some(f) = it.next() else {
                continue;
            };
            for d in it {
                let (a, b) = (find(&mut up, f), find(&mut up, d));
                up[a] = b;
            }
            by.push((f, l));
        }
        let mut comps: FxHashMap<usize, (Vec<usize>, Vec<&Load>)> = FxHashMap::default();
        for d in 0..n {
            let r = find(&mut up, d);
            comps.entry(r).or_default().0.push(d);
        }
        for &(f, l) in &by {
            let r = find(&mut up, f);
            comps.entry(r).or_default().1.push(l);
        }
        let mut keys: Vec<usize> = comps.keys().copied().collect();
        keys.sort_unstable();
        for r in keys {
            let (ds, ls) = &comps[&r];
            if ds.iter().any(|&d| defs[d] == Def::Entry) {
                continue;
            }
            let Some((obj, m)) = object(g, ls, end) else {
                continue;
            };
            let hull = |l: &Load| Iv {
                lo: obj.0 as u32 + l.y.lo,
                hi: obj.1 as u32 + l.y.hi,
            };
            let served: Vec<&&Load> = (ls.iter())
                .filter(|l| l.y.hi <= m && held(hull(l)))
                .collect();
            if served.len() <= ds.len() {
                continue;
            }
            let id = match out.objects.iter().position(|o| (o.0, o.1) == obj) {
                Some(i) => i,
                None => {
                    out.objects.push((obj.0, obj.1, Vec::new()));
                    out.objects.len() - 1
                }
            };
            let pcs = &mut out.objects[id].2;
            pcs.extend(served.iter().map(|l| l.pc));
            pcs.sort_unstable();
            pcs.dedup();
            let id = id as u16;
            for &d in ds {
                let (key, flag) = match defs[d] {
                    Def::Load(pc) => {
                        let c = ObjChk {
                            q,
                            obj: id,
                            flag: None,
                        };
                        out.pre.entry(pc).or_default().push(c);
                        continue;
                    }
                    Def::Store { s, flag, .. } => (stores[s].key, flag),
                    Def::Entry => continue,
                };
                let c = ObjChk { q, obj: id, flag };
                out.chk.entry(key).or_default().push(c);
            }
            let base = Iv {
                lo: obj.0 as u32,
                hi: obj.1 as u32,
            };
            for l in served {
                proven.push((l.key, (hull(l), base)));
            }
        }
    }
    (out, proven)
}

/// The loads through `(zp),Y` pairs of usable bytes, by the pair's low byte.
fn loads(g: &Graph, volatile: &[(u32, u32)], bank: Option<&BankMap>) -> FxHashMap<u8, Vec<Load>> {
    let ok = usable(g, volatile, bank);
    let mut out: FxHashMap<u8, Vec<Load>> = FxHashMap::default();
    for (&pc, vs) in &g.vars {
        for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
            for (ai, a) in v.acc.iter().enumerate() {
                let Some(p) = a.ptr.filter(|_| !a.store && !a.dummy) else {
                    continue;
                };
                let Some(q) = p.lo.and_then(Iv::value).filter(|&q| q < 0xff) else {
                    continue;
                };
                let pair = p.hi.value() == Some(q + 1);
                if !pair || !ok.contains(q as usize) || !ok.contains(q as usize + 1) {
                    continue;
                }
                let key = (pc, v.op, ai);
                let ys = g.regs.get(&pc).map(|r| r[di_pcode::reg::Y]);
                let has = |b: &[u64; 4], x: u32| b[x as usize >> 6] >> (x & 63) & 1 == 1;
                let y = ys
                    .and_then(|b| {
                        let lo = (0..=p.idx).find(|&x| has(&b, x))?;
                        let hi = (lo..=p.idx).rev().find(|&x| has(&b, x))?;
                        Some(Iv { lo, hi })
                    })
                    .unwrap_or(Iv { lo: 0, hi: p.idx });
                let base = g.reach.get(&key).map(|h| h.1);
                out.entry(q as u8)
                    .or_default()
                    .push(Load { pc, key, y, base });
            }
        }
    }
    out
}

/// The object of loads `ls` and the largest index of its fields (a load adding one value: a
/// field at a constant offset): from the least base of their hulls, a byte of a run of data
/// bytes, to the greatest, up to the end of the last run starting between them that ends in
/// the bytes the host holds (`end`, exclusive; the pointer may stand past its last byte).
fn object(g: &Graph, ls: &[&Load], end: u32) -> Option<((u16, u16), u32)> {
    let mut hull: Option<Iv> = None;
    let mut m = 0;
    for l in ls {
        let b = l.base?;
        hull = Some(hull.map_or(b, |h| Iv {
            lo: h.lo.min(b.lo),
            hi: h.hi.max(b.hi),
        }));
        if l.y.lo == l.y.hi {
            m = m.max(l.y.hi);
        }
    }
    let h = hull?;
    let i = g.runs.partition_point(|r| r.1 <= h.lo);
    g.runs.get(i).filter(|r| r.0 <= h.lo)?;
    let last = (g.runs[i..].iter())
        .take_while(|r| r.0 <= h.hi && r.1 <= end)
        .last()?;
    let hi = h.hi.min(last.1).min(0xffff);
    (h.lo <= hi).then_some(((h.lo as u16, hi as u16), m))
}

/// The increment of the high byte of pair `q` that a store `key` of its low byte leaves to a
/// flag, and the flag: after a store that writes no flag, the carry and a `BCC` (an `ADC`'s
/// carry out); after `INC q`, the zero flag and a `BNE` (the low byte wrapped). The
/// instructions between write neither the flag nor the pair, nor read through it, and the
/// branch's fall-through, entered from it alone, is `INC q + 1`: the pair's value once it
/// settles is the pointer plus the flag times 256.
fn settled(
    g: &Graph,
    index: &FxHashMap<StoreKey, usize>,
    preds: &FxHashMap<u16, Vec<u16>>,
    roots: &[u16],
    key: &StoreKey,
    q: u8,
) -> Option<(usize, u8)> {
    const BCC: u8 = 0x90;
    const BNE: u8 = 0xd0;
    const INC: u8 = 0xe6;
    let (c, z) = (di_pcode::reg::C, di_pcode::reg::Z);
    let one = |pc: u16| match &g.vars.get(&pc)?[..] {
        [v] if !g.cells.contains(pc as usize) => Some(v),
        _ => None,
    };
    let v = one(key.0)?;
    let (flag, br) = match v.op {
        INC => (z, BNE),
        op if !writes(op, c) && !writes(op, z) => (c, BCC),
        _ => return None,
    };
    if v.acc[key.2].addr.value() != Some(q as u32) {
        return None;
    }
    let touches = |v: &Var| {
        v.acc.iter().any(|a| {
            !a.dummy
                && (a.addr.meets(q as u32, q as u32 + 2)
                    || a.ptr
                        .is_some_and(|p| p.lo.and_then(Iv::value) == Some(q as u32)))
        })
    };
    let mut pc = match v.flow {
        Flow::Next(n) => n,
        _ => return None,
    };
    let mut seen = FxHashSet::default();
    loop {
        let v = one(pc)?;
        if !seen.insert(pc) {
            return None;
        }
        if v.op == br {
            let Flow::Br { f, .. } = v.flow else {
                return None;
            };
            let inc = one(f).filter(|i| i.op == INC)?;
            let ai = (inc.acc.iter()).position(|a| a.store && !a.back && !a.dummy)?;
            let sole = preds.get(&f).is_some_and(|p| p == &[pc]) && !roots.contains(&f);
            if !sole || inc.acc[ai].addr.value() != Some(q as u32 + 1) {
                return None;
            }
            return index.get(&(f, INC, ai)).map(|&i| (i, flag as u8));
        }
        if writes(v.op, flag) || touches(v) {
            return None;
        }
        pc = match v.flow {
            Flow::Next(n) => n,
            _ => return None,
        };
    }
}

/// Whether opcode `op`'s template writes register `r`.
fn writes(op: u8, r: usize) -> bool {
    use di_pcode::ir::Space;
    di_pcode::Template::of(op)
        .ops()
        .iter()
        .any(|o| o.out.space() == Space::Reg && o.out.off() as usize == r)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn held_bytes_end_below_the_volatile_ranges() {
        let mut w = FixedBitSet::with_capacity(0x10000);
        w.insert_range(0x3000..0x3100);
        w.insert_range(0xd500..0xd600);
        let io = [(0xd400, 0xd420)];
        assert_eq!(
            held(Some((0x1000, 0x2000)), &w, &[], &io),
            Some((0x1000, 0x3100))
        );
        let more = [(0x3100, 0x3180), (0xe000, 0xe100)];
        assert_eq!(
            held(Some((0x1000, 0x2000)), &w, &more, &io),
            Some((0x1000, 0x3180))
        );
        let below = FixedBitSet::with_capacity(0x10000);
        assert_eq!(
            held(Some((0x1000, 0x2000)), &below, &[], &io),
            Some((0x1000, 0x2000))
        );
        assert_eq!(held(None, &w, &more, &io), None);
    }

    #[test]
    fn flags_written_by_their_templates() {
        let (c, z) = (di_pcode::reg::C, di_pcode::reg::Z);
        assert!(writes(0x69, c) && writes(0xe6, z) && !writes(0xe6, c));
        assert!(!writes(0x85, c) && !writes(0x85, z) && writes(0x18, c));
    }
}
