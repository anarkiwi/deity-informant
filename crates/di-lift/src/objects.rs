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
use crate::vsa::{hull, Bits};
use crate::walks::Mk;
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};

/// A check of pair `q`'s pointer (as `of` reads it) against object `obj` ([`Objs::objects`]):
/// with `flag` (a flag register), of the pointer plus the flag times 256.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ObjChk {
    pub q: u8,
    pub obj: u16,
    pub flag: Option<u8>,
    pub of: Of,
}

/// What a check reads: the pair's pointer, or the pointer plus `Y`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Of {
    Ptr,
    PtrY,
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
    /// Per instruction start, the checks after it of the walks it anchors ([`crate::walks`]).
    pub post: FxHashMap<u16, Vec<ObjChk>>,
    /// Per access the memory map bounds ([`machine`]), the object its address is checked to
    /// lie in before it, and the object's bytes.
    pub machine: FxHashMap<StoreKey, (u16, Iv)>,
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

/// The control flow the definitions reach along: every flow edge, and from each return whose
/// targets are not computed to every return site.
pub(crate) struct Cfg<'a> {
    pub(crate) g: &'a Graph,
    pub(crate) rets: Vec<u16>,
    preds: FxHashMap<u16, Vec<u16>>,
}

impl<'a> Cfg<'a> {
    pub(crate) fn new(g: &'a Graph) -> Self {
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

/// Whether the bytes `h` lie in the held run `[lo, end)` outside the `volatile` ranges.
pub(crate) fn serves((lo, end): (u32, u32), volatile: &[(u32, u32)], h: Iv) -> bool {
    lo <= h.lo && h.hi < end && !volatile.iter().any(|&(a, b)| h.meets(a, b))
}

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
/// volatile range. A pair of `seeds` needs no check: its loads read its words plus `Y`; the
/// loads of others an object leaves are served by walks ([`walked`]), whose data bytes are
/// those of the image, or of each of init's runs for the variables `free` it leaves. With
/// `machine` (a closed program over a fixed memory map) the accesses whose bases lie in the
/// held bytes stay in them ([`machine`]).
#[allow(clippy::too_many_arguments)]
pub fn objects(
    g: &Graph,
    roots: &[u16],
    volatile: &[(u32, u32)],
    bank: Option<&BankMap>,
    held: Option<(u32, u32)>,
    seeds: &Seeds,
    free: &FxHashMap<u16, Vec<u8>>,
    machine: bool,
) -> (Objs, Proven) {
    let mut out = Objs::default();
    let mut proven = Vec::new();
    let Some((lo0, end)) = held.filter(|_| !g.runs.is_empty() && !roots.is_empty()) else {
        return (out, proven);
    };
    let held = |h: Iv| serves((lo0, end), volatile, h);
    let mut loads = loads(g, volatile, bank);
    for (key, ws) in indexed_x(g, seeds) {
        let h = Iv {
            lo: *ws.first().unwrap() as u32,
            hi: *ws.last().unwrap() as u32,
        };
        if held(h) {
            proven.push((key, (h, h)));
        }
    }
    let stores = zp_stores(g);
    let index: FxHashMap<StoreKey, usize> =
        stores.iter().enumerate().map(|(i, s)| (s.key, i)).collect();
    let cfg = Cfg::new(g);
    let mut pairs: Vec<u8> = loads.keys().copied().collect();
    pairs.sort_unstable();
    for &q in pairs.iter().filter(|q| !seeds.contains_key(q)) {
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
        let mut up = crate::Uf::new(n);
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
                up.union(f, d);
            }
            by.push((f, l));
        }
        let mut comps: FxHashMap<usize, (Vec<usize>, Vec<&Load>)> = FxHashMap::default();
        for d in 0..n {
            comps.entry(up.find(d)).or_default().0.push(d);
        }
        for &(f, l) in &by {
            comps.entry(up.find(f)).or_default().1.push(l);
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
                            of: Of::Ptr,
                        };
                        out.pre.entry(pc).or_default().push(c);
                        continue;
                    }
                    Def::Store { s, flag, .. } => (stores[s].key, flag),
                    Def::Entry => continue,
                };
                let c = ObjChk {
                    q,
                    obj: id,
                    flag,
                    of: Of::Ptr,
                };
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
    // Loads proven inside held bytes already need no walk.
    let done: FxHashSet<StoreKey> = (proven.iter().map(|p| p.0))
        .chain(g.proven.iter().filter(|e| held(e.1 .0)).map(|e| *e.0))
        .collect();
    for l in loads.values_mut() {
        l.retain(|l| !done.contains(&l.key));
    }
    let cx = Walked {
        g,
        cfg: &cfg,
        roots,
        held: &held,
        end,
        bytes: Bytes::new(g, free),
        stored: crate::analysis::stored(g),
    };
    for &q in &pairs {
        let kill = (stores.iter())
            .filter(|s| s.addr.meets(q as u32, q as u32 + 2))
            .map(|s| s.key.0)
            .collect();
        cx.walked(q, &loads[&q], seeds.get(&q), &kill, &mut out, &mut proven);
    }
    if machine {
        let run = Iv {
            lo: lo0,
            hi: end - 1,
        };
        self::machine(g, &cx, run, &mut out, &mut proven);
    }
    (out, proven)
}

/// Per pointer pair no lifted store may write, by its low byte, the words it holds at every
/// entry, ascending: of the image's bytes, or of each of init's runs for its variables.
pub type Seeds = FxHashMap<u8, Vec<u16>>;

/// The pairs a pointer access `a` of variant `v` at `pc` reads through: a `(zp),Y` access's,
/// or a `(zp,X)` access's for each value `X` holds there; None for another access.
fn pairs_of(g: &Graph, pc: u16, v: &Var, a: &crate::analysis::Access) -> Option<Vec<u8>> {
    use di_pcode::reg::{X, Y};
    let p = a.ptr.filter(|_| !a.dummy)?;
    let t = di_pcode::Template::of(v.op);
    if t.len != 2 {
        return None;
    }
    let qs: Vec<u8> = match crate::dispatch::index_reg(t)? {
        Y => vec![p.lo.and_then(Iv::value)? as u8],
        X if p.idx == 0 && !g.cells.contains(pc.wrapping_add(1) as usize) => {
            let o = g.img[pc.wrapping_add(1) as usize];
            Bits::from_words(g.regs.get(&pc)?[X])
                .values()
                .map(|x| o.wrapping_add(x as u8))
                .collect()
        }
        _ => return None,
    };
    (!qs.is_empty() && !qs.contains(&0xff)).then_some(qs)
}

/// The values of `Y` at `pc` on every run.
fn ys(g: &Graph, pc: u16) -> Vec<u32> {
    let y = g
        .regs
        .get(&pc)
        .map_or(Bits::ALL, |r| Bits::from_words(r[di_pcode::reg::Y]));
    y.values().collect()
}

/// The address intervals an access `a` of variant `v` at `pc` may reach: through pointers of
/// `seeds`, their words plus `Y` (an address past `$FFFF` wrapping); else its interval.
fn reach_of(g: &Graph, pc: u16, v: &Var, a: &crate::analysis::Access, seeds: &Seeds) -> Vec<Iv> {
    let wide = |lo: u32, hi: u32| match hi > 0xffff {
        true => vec![
            Iv { lo, hi: 0xffff },
            Iv {
                lo: 0,
                hi: hi - 0x10000,
            },
        ],
        false => vec![Iv { lo, hi }],
    };
    let qs = pairs_of(g, pc, v, a).filter(|qs| qs.iter().all(|q| seeds.contains_key(q)));
    let Some(qs) = qs else {
        return wide(a.addr.lo, a.addr.hi);
    };
    let y = match a.ptr.is_some_and(|p| p.idx == 0) {
        true => vec![0],
        false => ys(g, pc),
    };
    let (Some(&ylo), Some(&yhi)) = (y.first(), y.last()) else {
        return Vec::new();
    };
    (qs.iter().flat_map(|q| &seeds[q]))
        .flat_map(|&w| wide(w as u32 + ylo, w as u32 + yhi))
        .collect()
}

/// The seeds of `g` ([`Seeds`]) with init's variables `free` and the bytes `known` whose
/// entry value is the image's (those init leaves one value in, and the loaded program's):
/// of the pairs of usable such bytes pointer accesses read through, those no store may write
/// while every pair of them holds one of its words, a greatest fixpoint: a store through
/// pairs of them writes their words plus `Y`.
pub fn seeds(
    g: &Graph,
    free: &FxHashMap<u16, Vec<u8>>,
    known: &FixedBitSet,
    volatile: &[(u32, u32)],
    bank: Option<&BankMap>,
) -> Seeds {
    let mut ok = usable(g, volatile, bank);
    for a in 0..0x100usize {
        ok.set(
            a,
            ok.contains(a) && (known.contains(a) || free.contains_key(&(a as u16))),
        );
    }
    let bytes = |a: u8| match free.get(&(a as u16)) {
        Some(v) if !v.is_empty() => v.clone(),
        _ => vec![g.img[a as usize]],
    };
    let mut out = Seeds::default();
    for (&pc, vs) in &g.vars {
        for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
            for q in v.acc.iter().filter_map(|a| pairs_of(g, pc, v, a)).flatten() {
                if !ok.contains(q as usize) || !ok.contains(q as usize + 1) {
                    continue;
                }
                let (l, h) = (bytes(q), bytes(q + 1));
                let mut ws: Vec<u16> = match l.len() == h.len() {
                    true => (l.iter().zip(&h))
                        .map(|(&l, &h)| u16::from_le_bytes([l, h]))
                        .collect(),
                    false => (l.iter())
                        .flat_map(|&l| h.iter().map(move |&h| u16::from_le_bytes([l, h])))
                        .collect(),
                };
                ws.sort_unstable();
                ws.dedup();
                out.insert(q, ws);
            }
        }
    }
    loop {
        let mut hit = FixedBitSet::with_capacity(0x100);
        for (&pc, vs) in &g.vars {
            for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
                for a in v.acc.iter().filter(|a| a.store && !a.dummy && !a.back) {
                    for iv in reach_of(g, pc, v, a, &out)
                        .into_iter()
                        .filter(|iv| iv.lo < 0x100)
                    {
                        hit.insert_range(iv.lo as usize..(iv.hi as usize + 1).min(0x100));
                    }
                }
            }
        }
        let n = out.len();
        out.retain(|&q, _| !hit.contains(q as usize) && !hit.contains(q as usize + 1));
        if out.len() == n {
            return out;
        }
    }
}

/// Narrow the pointer accesses of `g` through pairs of `seeds` to what their words reach:
/// each access's interval to its hull and, where it is a few addresses, the instruction's
/// address set where it has none ([`Graph::addr_sets`]; a store's addresses are then
/// [`Graph::written`] and, none of code, [`Graph::nocode`]).
pub fn seat(g: &mut Graph, seeds: &Seeds) {
    type Seat = ((u16, bool), Option<BTreeSet<u16>>, StoreKey, Iv);
    let mut sets: Vec<Seat> = Vec::new();
    for (&pc, vs) in &g.vars {
        for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
            for (ai, a) in v.acc.iter().enumerate() {
                let all = pairs_of(g, pc, v, a)
                    .is_some_and(|qs| qs.iter().all(|q| seeds.contains_key(q)));
                let ivs = reach_of(g, pc, v, a, seeds);
                if !all || ivs.is_empty() {
                    continue;
                }
                let n: u32 = ivs.iter().map(|iv| iv.hi - iv.lo + 1).sum();
                let set = (n as usize <= crate::vsa::CAP).then(|| {
                    (ivs.iter().flat_map(|iv| iv.lo..=iv.hi))
                        .map(|x| x as u16)
                        .collect()
                });
                let h = Iv {
                    lo: ivs.iter().map(|iv| iv.lo).min().unwrap(),
                    hi: ivs.iter().map(|iv| iv.hi).max().unwrap(),
                };
                sets.push(((pc, a.store), set, (pc, v.op, ai), h));
            }
        }
    }
    for (key, set, (pc, op, ai), h) in sets {
        for v in g
            .vars
            .get_mut(&pc)
            .into_iter()
            .flatten()
            .filter(|v| v.op == op)
        {
            let a = &mut v.acc[ai];
            a.addr = crate::vsa::meet(a.addr, h).unwrap_or(a.addr);
        }
        let Some(set) = set else {
            continue;
        };
        if key.1 {
            g.written.extend(set.iter().map(|&x| x as usize));
            if !set.iter().any(|&x| g.code.contains(x as usize)) {
                g.nocode.insert((pc, op, ai));
            }
        }
        g.addr_sets.entry(key).or_insert(set);
    }
}

/// The `(zp,X)` loads whose every pair is one of `seeds`, and the words they read, ascending.
fn indexed_x(g: &Graph, seeds: &Seeds) -> Vec<(StoreKey, Vec<u16>)> {
    let mut out = Vec::new();
    for (&pc, vs) in &g.vars {
        for v in vs.iter().filter(|v| !is_stack_op(v.op)) {
            for (ai, a) in v.acc.iter().enumerate().filter(|(_, a)| !a.store) {
                let x = crate::dispatch::index_reg(di_pcode::Template::of(v.op));
                let qs = pairs_of(g, pc, v, a).filter(|_| x == Some(di_pcode::reg::X));
                let Some(qs) = qs.filter(|qs| qs.iter().all(|q| seeds.contains_key(q))) else {
                    continue;
                };
                let mut ws: Vec<u16> = qs.iter().flat_map(|q| seeds[q].iter().copied()).collect();
                ws.sort_unstable();
                ws.dedup();
                out.push(((pc, v.op, ai), ws));
            }
        }
    }
    out.sort_unstable_by_key(|e| e.0);
    out
}

/// The data bytes walks read: the image's, and per run of init its variables'.
struct Bytes<'a> {
    img: &'a [u8; 0x10000],
    free: &'a FxHashMap<u16, Vec<u8>>,
    runs: usize,
    /// Per marker and run, the first marker at or after each byte.
    next: RefCell<FxHashMap<Mk, Vec<Vec<u32>>>>,
}

/// No marker.
const NO: u32 = u32::MAX;

impl<'a> Bytes<'a> {
    fn new(g: &'a Graph, free: &'a FxHashMap<u16, Vec<u8>>) -> Self {
        let runs = free.values().map(Vec::len).max().unwrap_or(1).max(1);
        Bytes {
            img: &g.img,
            free,
            runs,
            next: RefCell::default(),
        }
    }
    fn at(&self, x: u32, r: usize) -> u8 {
        match self.free.get(&(x as u16)) {
            Some(v) if !v.is_empty() => v[r.min(v.len() - 1)],
            _ => self.img[x as usize],
        }
    }
    /// The greatest first marker `m` at or after a byte of `[lo, hi]` over the runs, and the
    /// greatest count of bytes from such a byte to it; None where a run has none.
    fn reach(&self, m: Mk, lo: u32, hi: u32) -> Option<(u32, u32)> {
        let mut memo = self.next.borrow_mut();
        let next = memo.entry(m).or_insert_with(|| {
            (0..self.runs)
                .map(|r| {
                    let mut out = vec![NO; 0x10000];
                    let mut n = NO;
                    for x in (0..0x10000u32).rev() {
                        if m.hit(self.at(x, r)) {
                            n = x;
                        }
                        out[x as usize] = n;
                    }
                    out
                })
                .collect()
        });
        let mut out = (0, 0);
        for r in next.iter() {
            for x in lo..=hi.min(0xffff) {
                let n = r[x as usize];
                if n == NO {
                    return None;
                }
                out = (out.0.max(n), out.1.max(n - x));
            }
        }
        Some(out)
    }
}

/// What the walks of a program's pairs share: its flow, entries, held bytes (and the end of
/// those past the program), data bytes and the bytes its stores may write.
struct Walked<'a, H: Fn(Iv) -> bool> {
    g: &'a Graph,
    cfg: &'a Cfg<'a>,
    roots: &'a [u16],
    held: &'a H,
    end: u32,
    bytes: Bytes<'a>,
    stored: FixedBitSet,
}

/// A load a walk serves and its offsets from the anchor's pointer plus `Y`.
struct Step<'l> {
    l: &'l Load,
    w: crate::walks::Walk,
}

/// What the loads of an anchor's walks read from starts `[lo, hi]`: per load its hull, and
/// the least and greatest offset from the starts.
struct Reads {
    hulls: Vec<Iv>,
    dlo: i64,
    dhi: i64,
}

impl<H: Fn(Iv) -> bool> Walked<'_, H> {
    /// Serve the loads `ls` of pair `q` an object leaves (its words `ws` where it is a seed;
    /// its stores at `kill`): those an anchor's walk reaches read the anchor's pointer plus `Y`
    /// and their offsets; a seed's others read its words plus `Y`.
    fn walked(
        &self,
        q: u8,
        ls: &[Load],
        ws: Option<&Vec<u16>>,
        kill: &FxHashSet<u16>,
        out: &mut Objs,
        proven: &mut Proven,
    ) {
        let rest: Vec<&Load> = ls.iter().collect();
        let mut pcs: Vec<u16> = rest.iter().map(|l| l.pc).collect();
        pcs.sort_unstable();
        pcs.dedup();
        let walks = crate::walks::walks(self.cfg, self.roots, q, kill, &pcs);
        let mut by: BTreeMap<u16, Vec<Step>> = BTreeMap::new();
        for l in rest {
            if let Some(&w) = walks.get(&l.pc) {
                by.entry(w.at).or_default().push(Step { l, w });
            }
        }
        for (at, steps) in by {
            if let Some([v]) = self.g.vars.get(&at).map(|v| &v[..]) {
                if let Flow::Next(n) = v.flow {
                    // `Y` after the anchor: an immediate's, else the values at its successor.
                    const LDY: u8 = 0xa0;
                    let imm = !self.g.cells.contains(at.wrapping_add(1) as usize);
                    let yv = match v.op == LDY && imm {
                        true => vec![self.g.img[at.wrapping_add(1) as usize] as u32],
                        false => ys(self.g, n),
                    };
                    // The words: the seed's, else those the pair holds at every load on every run.
                    let held: Option<Vec<u16>> = match ws {
                        Some(ws) => Some(ws.clone()),
                        None => steps
                            .iter()
                            .try_fold(BTreeSet::new(), |mut u, s| {
                                u.extend(self.g.ptr_sets.get(&s.l.pc)?);
                                Some(u)
                            })
                            .map(|u| u.into_iter().collect()),
                    };
                    self.anchor(q, at, &yv, &steps, held.as_ref(), out, proven);
                }
            }
        }
        // A seed's loads no walk served read its words plus `Y`.
        let Some(ws) = ws else {
            return;
        };
        let done: FxHashSet<StoreKey> = proven.iter().map(|p| p.0).collect();
        let b = Iv {
            lo: ws[0] as u32,
            hi: *ws.last().unwrap() as u32,
        };
        for l in ls.iter().filter(|l| !done.contains(&l.key)) {
            let h = Iv {
                lo: b.lo + l.y.lo,
                hi: b.hi + l.y.hi,
            };
            if (self.held)(h) {
                proven.push((l.key, (h, b)));
            }
        }
    }

    /// What the loads `steps` read from starts `[lo, hi]` with data bytes up to `stop`: a
    /// load at offsets `d` reads `[lo + d.lo, hi + d.hi]`, one walking to a marker from its
    /// start to the greatest first marker after it, the walk's bytes written by no store;
    /// None where a load may read past `stop` or below 0.
    fn reads(&self, steps: &[Step], lo: i64, hi: i64, stop: u32) -> Option<Reads> {
        let mut r = Reads {
            hulls: Vec::new(),
            dlo: i64::MAX,
            dhi: i64::MIN,
        };
        for s in steps {
            let (a, b, d) = match (s.w.d, s.w.run) {
                (Some(d), _) => (lo + d.0 as i64, hi + d.1 as i64, (d.0 as i64, d.1 as i64)),
                (None, Some((st, m))) => {
                    let (x0, x1) = (lo + st.0 as i64, hi + st.1 as i64);
                    if x0 < 0 || x1 >= stop as i64 {
                        return None;
                    }
                    let (top, run) = self.bytes.reach(m, x0 as u32, x1 as u32)?;
                    if self
                        .stored
                        .contains_any_in_range(x0 as usize..top as usize + 1)
                    {
                        return None;
                    }
                    (x0, top as i64, (st.0 as i64, run as i64 + st.1 as i64))
                }
                (None, None) => return None,
            };
            if a < 0 || b >= stop as i64 {
                return None;
            }
            (r.dlo, r.dhi) = (r.dlo.min(d.0), r.dhi.max(d.1));
            r.hulls.push(Iv {
                lo: a as u32,
                hi: b as u32,
            });
        }
        Some(r)
    }

    /// The greatest start from which every walk of `steps` ends at a marker before `stop`:
    /// the last byte every run holds its marker at, less its starts' greatest offset.
    fn shrink(&self, steps: &[Step], lo: i64, stop: u32) -> i64 {
        let over = steps.iter().map(|s| match s.w.run {
            Some((st, m)) => (lo..stop as i64)
                .rev()
                .find(|&x| (0..self.bytes.runs).all(|r| m.hit(self.bytes.at(x as u32, r))))
                .map_or(i64::MIN, |x| x - st.1 as i64),
            None => i64::MIN,
        });
        over.min().unwrap_or(i64::MIN)
    }

    /// Serve the loads `steps` of the walks from anchor `at` of pair `q`, after which `Y`
    /// holds `yv`: without a check where the pair's words `ws` are known and the walks from
    /// each word plus each value of `Y` read held bytes; else with a check after the anchor
    /// of its pointer plus `Y` (and of `Y`, where an offset may carry it past `$FF`) against
    /// an object of the data bytes the walks start in, where it serves more than one load or
    /// a walk.
    #[allow(clippy::too_many_arguments)]
    fn anchor(
        &self,
        q: u8,
        at: u16,
        yv: &[u32],
        steps: &[Step],
        ws: Option<&Vec<u16>>,
        out: &mut Objs,
        proven: &mut Proven,
    ) {
        let (Some(&ylo), Some(&yhi)) = (yv.first(), yv.last()) else {
            return;
        };
        let carry =
            |r: &Reads, ylo: u32, yhi: u32| ylo as i64 + r.dlo < 0 || yhi as i64 + r.dhi > 0xff;
        if let Some(ws) = ws {
            let mut hulls: Vec<Option<Iv>> = vec![None; steps.len()];
            let fits = ws.iter().all(|&w| {
                yv.iter().all(|&y| {
                    let a = w as i64 + y as i64;
                    let Some(r) = self
                        .reads(steps, a, a, self.end)
                        .filter(|r| !carry(r, y, y))
                    else {
                        return false;
                    };
                    for (h, x) in hulls.iter_mut().zip(r.hulls) {
                        *h = Some(h.map_or(x, |h| hull(h, x)));
                    }
                    true
                })
            });
            if fits && hulls.iter().all(|h| h.is_some_and(self.held)) {
                let b = Iv {
                    lo: ws[0] as u32,
                    hi: *ws.last().unwrap() as u32,
                };
                for (s, h) in steps.iter().zip(hulls) {
                    proven.push((s.l.key, (h.unwrap(), b)));
                }
                return;
            }
        }
        if steps.len() < 2 && steps.iter().all(|s| s.w.d.is_some()) {
            return;
        }
        let base = match ws {
            Some(ws) => Some(Some(Iv {
                lo: ws[0] as u32,
                hi: *ws.last().unwrap() as u32,
            })),
            None => steps.iter().try_fold(None, |h: Option<Iv>, s| {
                let b = s.l.base?;
                Some(Some(h.map_or(b, |h| hull(h, b))))
            }),
        }
        .flatten();
        let Some(base) = base else {
            return;
        };
        // The starts lie in the data bytes from the run holding the least to the end of the
        // last run starting by the greatest that the host holds.
        let (slo, shi) = (base.lo as i64 + ylo as i64, base.hi as i64 + yhi as i64);
        let runs = &self.g.runs;
        let i = runs.partition_point(|r| (r.1 as i64) <= slo);
        let Some(first) = runs.get(i).filter(|r| r.0 as i64 <= slo) else {
            return;
        };
        let Some(last) = (runs[i..].iter())
            .take_while(|r| (r.0 as i64) <= shi && r.1 <= self.end)
            .last()
        else {
            return;
        };
        let (lo, stop) = (slo.max(first.0 as i64), last.1);
        // Only walks' starts shrink below the reach hull's, to their last marker: valid runs
        // end their walks there.
        let walk = steps.iter().all(|s| s.w.d.is_none());
        let mut hi = match walk {
            true => shi.min(stop as i64 - 1),
            false => shi,
        };
        let mut r = self.reads(steps, lo, hi, stop);
        if r.is_none() && walk {
            hi = hi.min(self.shrink(steps, lo, stop));
            r = (hi >= lo)
                .then(|| self.reads(steps, lo, hi, stop))
                .flatten();
        }
        let Some(r) = r.filter(|r| r.hulls.iter().all(|&h| (self.held)(h))) else {
            return;
        };
        if carry(&r, ylo, yhi) {
            return;
        }
        let obj = object_id(out, lo as u16, hi as u16, steps.iter().map(|s| s.l.pc));
        let chk = ObjChk {
            q,
            obj,
            flag: None,
            of: Of::PtrY,
        };
        out.post.entry(at).or_default().push(chk);
        let b = Iv {
            lo: (lo - yhi as i64).max(0) as u32,
            hi: (hi - ylo as i64).max(0) as u32,
        };
        for (s, h) in steps.iter().zip(r.hulls) {
            proven.push((s.l.key, (h, b)));
        }
    }
}

/// Where a value comes from ([`Sources`]): the program's bytes (a table the code indexes in the
/// held run, by its base; or its held bytes or init's runs at the entries), a constant (of the code, or
/// of the machine's state outside the program at the entries), a register at an entry, or
/// anything else.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Src {
    Prog(Option<u16>),
    Imm(u8),
    Entry,
    Other,
}

/// The value of register `r` (`Reg`) or of byte `x` (`Mem`) before an instruction start.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Val {
    Reg(usize, u16),
    Mem(u32, u16),
}

/// The sources of the values the program's registers and bytes hold, from its code alone:
/// along the definitions reaching each use, through copies (and, for an index, arithmetic),
/// to code constants, the program's bytes and the rest. Song data never decides a source.
struct Sources<'a, H: Fn(Iv) -> bool> {
    cx: &'a Walked<'a, H>,
    run: Iv,
    /// Per target (a byte; `0x10000 + r` for register `r`), its definitions (the entries,
    /// else the instruction start defining it) and those reaching each start.
    defs: FxHashMap<u32, Reaching>,
    /// Per access the sources of its address's high and low bytes (None: in progress, or a
    /// byte the code does not form).
    addr: FxHashMap<StoreKey, Option<AddrSrc>>,
}

/// A target's definitions and those reaching each instruction start ([`Sources::defs`]).
type Reaching = (Vec<Option<u16>>, FxHashMap<u16, FixedBitSet>);

/// The sources of an address's high and low bytes.
type AddrSrc = (FxHashSet<Src>, FxHashSet<Src>);

/// Where a register's value after an instruction writing it comes from: its immediate, what
/// it reads, another register, itself and what it reads, itself alone, or anything else.
enum RegSrc {
    Imm,
    Load,
    Copy(usize),
    Alu,
    Same,
    Other,
}

/// The source kind of a register an instruction of opcode `op` writes.
fn reg_src(op: u8) -> RegSrc {
    use di_pcode::reg::{A, X, Y};
    match op {
        0xa9 | 0xa2 | 0xa0 => RegSrc::Imm,
        0xaa | 0xa8 => RegSrc::Copy(A),
        0x8a => RegSrc::Copy(X),
        0x98 => RegSrc::Copy(Y),
        0xe8 | 0xca | 0xc8 | 0x88 | 0x0a | 0x2a | 0x4a | 0x6a => RegSrc::Same,
        0xa1 | 0xa5 | 0xad | 0xb1 | 0xb5 | 0xb9 | 0xbd => RegSrc::Load,
        0xa6 | 0xae | 0xb6 | 0xbe | 0xa4 | 0xac | 0xb4 | 0xbc => RegSrc::Load,
        _ if op & 3 == 1 && matches!(op >> 5, 0 | 1 | 2 | 3 | 7) => RegSrc::Alu,
        _ => RegSrc::Other,
    }
}

/// The register a store of opcode `op` writes, `Some(None)` for a read-modify-write of its
/// byte, None for another write.
fn mem_src(op: u8) -> Option<Option<usize>> {
    use di_pcode::reg::{A, X, Y};
    match op {
        0x81 | 0x85 | 0x8d | 0x91 | 0x95 | 0x99 | 0x9d => Some(Some(A)),
        0x86 | 0x8e | 0x96 => Some(Some(X)),
        0x84 | 0x8c | 0x94 => Some(Some(Y)),
        _ if op & 3 == 2
            && matches!(op >> 5, 0 | 1 | 2 | 3 | 6 | 7)
            && matches!((op >> 2) & 7, 1 | 3 | 5 | 7) =>
        {
            Some(None)
        }
        _ => None,
    }
}

impl<'a, H: Fn(Iv) -> bool> Sources<'a, H> {
    /// The definitions of target `t` reaching `pc`.
    fn reaching(&mut self, t: u32, pc: u16) -> Vec<Option<u16>> {
        let (g, cx) = (self.cx.g, self.cx);
        let (ds, ins) = self.defs.entry(t).or_insert_with(|| {
            let mut defs = vec![Def::Entry];
            let mut pcs = vec![None];
            let mut at: FxHashMap<u16, Vec<usize>> = FxHashMap::default();
            for (&pc, vs) in &g.vars {
                let hit = |v: &Var| match t {
                    0x10000.. => writes(v.op, (t - 0x10000) as usize),
                    x => (v.acc.iter()).any(|a| a.store && !a.dummy && a.addr.meets(x, x + 1)),
                };
                if !vs.iter().any(hit) {
                    continue;
                }
                let kill = match t {
                    0x10000.. => vs.iter().all(hit),
                    x => {
                        vs.len() == 1
                            && (vs[0].acc.iter()).any(|a| a.store && a.addr.value() == Some(x))
                    }
                };
                at.entry(pc).or_default().push(defs.len());
                defs.push(Def::Store {
                    s: 0,
                    kill,
                    flag: None,
                });
                pcs.push(Some(pc));
            }
            (pcs, cx.cfg.reaching(cx.roots, &defs, &at))
        });
        ins.get(&pc)
            .map_or(Vec::new(), |i| i.ones().map(|d| ds[d]).collect())
    }

    /// The sources of the values `work` holds; with `derive`, also through arithmetic on them
    /// (an index's induction), else such a value is none of a word's ([`Src::Other`]).
    fn leaves(&mut self, mut work: Vec<Val>, derive: bool) -> FxHashSet<Src> {
        let g = self.cx.g;
        let mut out = FxHashSet::default();
        let mut seen: FxHashSet<Val> = FxHashSet::default();
        while let Some(val) = work.pop() {
            if !seen.insert(val) {
                continue;
            }
            let (t, pc) = match val {
                Val::Reg(r, pc) => (0x10000 + r as u32, pc),
                Val::Mem(x, pc) => (x, pc),
            };
            // At an entry a byte holds what any store left, or else init's value, the program's
            // (song data), or the machine's state outside both, a constant of the entry.
            let mut ds = self.reaching(t, pc);
            if matches!(val, Val::Mem(..)) && ds.contains(&None) {
                ds.extend(self.defs[&t].0.iter().flatten().map(|&s| Some(s)));
            }
            for d in ds {
                let Some(s) = d else {
                    match val {
                        Val::Reg(..) => {
                            out.insert(Src::Entry);
                        }
                        Val::Mem(x, _) if (self.cx.held)(Iv::exact(x)) => {
                            out.insert(Src::Prog(None));
                        }
                        Val::Mem(x, _)
                            if self
                                .cx
                                .bytes
                                .free
                                .get(&(x as u16))
                                .is_some_and(|v| !v.is_empty()) =>
                        {
                            out.insert(Src::Prog(None));
                        }
                        Val::Mem(x, _) => {
                            out.insert(Src::Imm(g.img[x as usize]));
                        }
                    }
                    continue;
                };
                for v in g.vars.get(&s).into_iter().flatten() {
                    let cell = |i: u16| {
                        let a = s.wrapping_add(i);
                        g.cells
                            .contains(a as usize)
                            .then_some(Val::Mem(a as u32, s))
                    };
                    match val {
                        Val::Reg(r, _) if writes(v.op, r) => match reg_src(v.op) {
                            RegSrc::Imm => match cell(1) {
                                Some(c) => work.push(c),
                                None => {
                                    out.insert(Src::Imm(g.img[s.wrapping_add(1) as usize]));
                                }
                            },
                            RegSrc::Copy(q) => work.push(Val::Reg(q, s)),
                            RegSrc::Same | RegSrc::Alu if !derive => {
                                out.insert(Src::Other);
                            }
                            RegSrc::Same => work.push(Val::Reg(r, s)),
                            RegSrc::Load => self.read(s, v, &mut work, &mut out),
                            RegSrc::Alu => {
                                work.push(Val::Reg(r, s));
                                work.extend(cell(1).filter(|_| (v.op >> 2) & 7 == 2));
                                self.read(s, v, &mut work, &mut out);
                            }
                            RegSrc::Other => {
                                out.insert(Src::Other);
                            }
                        },
                        Val::Mem(x, _) => {
                            let w = |a: &crate::analysis::Access| {
                                a.store && !a.dummy && a.addr.meets(x, x + 1)
                            };
                            if !v.acc.iter().any(w) {
                                continue;
                            }
                            match mem_src(v.op) {
                                Some(Some(r)) => work.push(Val::Reg(r, s)),
                                Some(None) if derive => work.push(Val::Mem(x, s)),
                                Some(None) => {
                                    out.insert(Src::Other);
                                }
                                None => {
                                    out.insert(Src::Other);
                                }
                            }
                        }
                        Val::Reg(..) => {}
                    }
                }
            }
        }
        out
    }

    /// The sources of what variant `v` at `pc` reads: of a byte of one address, its value; of
    /// several, the program's bytes where the code selects the held run for the access.
    fn read(&mut self, pc: u16, v: &Var, work: &mut Vec<Val>, out: &mut FxHashSet<Src>) {
        for (ai, a) in v.acc.iter().enumerate() {
            if a.store || a.dummy {
                continue;
            }
            match a.addr.value() {
                Some(x) => work.push(Val::Mem(x, pc)),
                None => {
                    let run = self.run;
                    let ok = (self.access(pc, v, ai)).is_some_and(|(h, l)| {
                        Self::selects(&h, &l, run, &self.cx.g.cells).is_some()
                    });
                    let t = di_pcode::Template::of(v.op);
                    let b =
                        crate::analysis::operand_bytes(&self.cx.g.img, &self.cx.g.cells, pc, t.len);
                    let table = match (a.ptr, b) {
                        (None, [Some(l), Some(h)]) if t.len == 3 => {
                            Some(u16::from_le_bytes([l, h]))
                        }
                        _ => None,
                    };
                    out.insert(if ok { Src::Prog(table) } else { Src::Other });
                }
            }
        }
    }

    /// The sources of the high and low bytes of the address access `ai` of variant `v` at
    /// `pc` is formed from: its pointer pairs', or its operand's.
    fn access(&mut self, pc: u16, v: &Var, ai: usize) -> Option<AddrSrc> {
        let key = (pc, v.op, ai);
        if let Some(r) = self.addr.get(&key) {
            return r.clone();
        }
        self.addr.insert(key, None);
        let g = self.cx.g;
        let t = di_pcode::Template::of(v.op);
        let byte = |i: u16| {
            let a = pc.wrapping_add(i);
            match (i < t.len as u16, g.cells.contains(a as usize)) {
                (false, _) => Ok(0),
                (true, true) => Err(Val::Mem(a as u32, pc)),
                (true, false) => Ok(g.img[a as usize]),
            }
        };
        let (mut his, mut los) = (FxHashSet::default(), FxHashSet::default());
        let (mut hv, mut lv) = (Vec::new(), Vec::new());
        match v.acc[ai].ptr {
            Some(p) => {
                let l = p.lo?;
                for q in l.lo..=l.hi.min(0xff) {
                    lv.push(Val::Mem(q, pc));
                    hv.push(Val::Mem((q + 1) & 0xff, pc));
                }
            }
            None => {
                for (i, (vs, ss)) in [(&mut lv, &mut los), (&mut hv, &mut his)]
                    .into_iter()
                    .enumerate()
                {
                    match byte(i as u16 + 1) {
                        Ok(b) => {
                            ss.insert(Src::Imm(b));
                        }
                        Err(c) => vs.push(c),
                    }
                }
            }
        }
        if !hv.is_empty() {
            his.extend(self.leaves(hv, false));
        }
        if !lv.is_empty() {
            los.extend(self.leaves(lv, false));
        }
        let r = (!his.is_empty() && !los.is_empty()).then_some((his, los));
        self.addr.insert(key, r.clone());
        r
    }

    /// Whether the code alone selects `block` for an address of high and low bytes from `hi`
    /// and `lo`: from the program's bytes, or constants making words in `block` (a high byte
    /// with the program's: its page, holding none of the `cells` the program writes); and
    /// whether the code's constants alone form it.
    fn selects(
        hi: &FxHashSet<Src>,
        lo: &FxHashSet<Src>,
        block: Iv,
        cells: &FixedBitSet,
    ) -> Option<bool> {
        let bad = |s: &FxHashSet<Src>| s.contains(&Src::Other) || s.contains(&Src::Entry);
        if bad(hi) || bad(lo) {
            return None;
        }
        let imm = |s: &FxHashSet<Src>| -> Vec<u32> {
            (s.iter())
                .filter_map(|s| match s {
                    Src::Imm(b) => Some(*b as u32),
                    _ => None,
                })
                .collect()
        };
        let prog = |s: &FxHashSet<Src>| s.iter().any(|s| matches!(s, Src::Prog(_)));
        let code = !prog(hi) && !prog(lo);
        let ok = match code {
            true => (imm(hi).iter()).all(|h| {
                imm(lo)
                    .iter()
                    .all(|l| block.meets(h << 8 | l, (h << 8 | l) + 1))
            }),
            false => imm(hi).iter().all(|h| {
                let p = (h << 8) as usize;
                block.meets(h << 8, (h << 8) + 0x100) && cells.count_ones(p..p + 0x100) == 0
            }),
        };
        ok.then_some(code)
    }
}

/// The machine model: the memory map is fixed (the program, other RAM, the I/O pages, the
/// ROMs), so an access whose every base lies in the bytes `run` the host holds of the program
/// (its image and the bytes past it it writes) stays in them on every valid run of a closed
/// program: outside them it would take I/O, ROM or RAM the program never held for its own,
/// and the program, which works, would not behave as it does. Over every access of no one
/// address ([`Graph::bases`]), the code alone ([`Sources`]) selects the block it stays in:
/// where its address's bytes are copies of the program's bytes, whatever they hold, `run` (where
/// cells lie in it, the run between them holding the tables its high byte is copied from);
/// where constants of the code form them, making bases in `run`, `run` (a store the lift
/// bounds to a run of data bytes, that run). Such an access is checked before it against the
/// block (`Objs::machine`) and proven there, with no check only where the code's constants
/// form its base and its index (or any index its guard passes) keeps it in the block. Song
/// data decides neither. Other accesses are left as they were.
fn machine<H: Fn(Iv) -> bool>(
    g: &Graph,
    cx: &Walked<'_, H>,
    run: Iv,
    out: &mut Objs,
    proven: &mut Proven,
) {
    let done: FxHashSet<StoreKey> = (proven.iter().map(|p| p.0))
        .chain(g.proven.iter().filter(|e| (cx.held)(e.1 .0)).map(|e| *e.0))
        .collect();
    let mut src = Sources {
        cx,
        run,
        defs: FxHashMap::default(),
        addr: FxHashMap::default(),
    };
    let mut keys: Vec<&StoreKey> = g.bases.keys().collect();
    keys.sort_unstable();
    for &key in keys {
        let (pc, op, ai) = key;
        let base = g.bases[&key].1;
        let Some(v) = g
            .vars
            .get(&pc)
            .and_then(|vs| vs.iter().find(|v| v.op == op))
        else {
            continue;
        };
        let a = v.acc[ai];
        if is_stack_op(op) || a.dummy || a.addr.value().is_some() || done.contains(&key) {
            continue;
        }
        // A store the lift bounds to the run of data bytes holding its base, taking it to
        // change no constant code ([`Graph::bounded`]), stays in that run; any other store's
        // code checks keep the code it may write.
        let bounded = (g.bounded.get(&key)).filter(|_| a.store);
        let mut block = bounded.map_or(run, |r| Iv {
            lo: r.0,
            hi: r.1 - 1,
        });
        let Some((hi, lo)) = src.access(pc, v, ai) else {
            continue;
        };
        let Some(code) = Sources::<H>::selects(&hi, &lo, block, &g.cells) else {
            continue;
        };
        // A load from an address the program's bytes form, whatever they hold, reads the run
        // of data bytes holding every table its high byte is copied from; one copied from no
        // table, the held bytes where no cell (a scalar in the C) lies in them. A store so
        // formed is bounded by `run` alone, its stores' checks keeping the code it may write.
        if !code {
            block = run;
        }
        if !code && !a.store {
            let ts: Option<Vec<u32>> = (hi.iter())
                .filter_map(|s| match s {
                    Src::Prog(t) => Some(t.map(u32::from)),
                    _ => None,
                })
                .collect();
            let celled = g.cells.count_ones(run.lo as usize..run.hi as usize + 1) > 0;
            let of = |t: u32| g.runs.iter().find(|r| r.0 <= t && t < r.1);
            let one = ts.as_ref().and_then(|ts| {
                let r = of(*ts.first()?)?;
                ts.iter().all(|&t| of(t) == Some(r)).then_some(Iv {
                    lo: r.0,
                    hi: r.1 - 1,
                })
            });
            match one {
                Some(r) if Sources::<H>::selects(&hi, &lo, r, &g.cells).is_some() => block = r,
                None if ts.as_ref().is_none_or(Vec::is_empty) && !celled => {}
                _ => continue,
            }
        }
        if code {
            // A store bounded so is taken to stay in its run, which a run past it does not but
            // where an index guard keeps its base plus the index there.
            let kept = |&(_, ok): &(u8, Iv)| base.hi + ok.hi <= block.hi;
            let sure = bounded.is_none() || g.guards.get(&pc).is_some_and(kept);
            let ix = crate::dispatch::index_reg(di_pcode::Template::of(op));
            // An index whose values come from the program's bytes takes any value its index
            // guard, if any, lets pass.
            let fixed = ix.is_none_or(|r| {
                let s = src.leaves(vec![Val::Reg(r, pc)], true);
                s.iter().all(|s| matches!(s, Src::Imm(_) | Src::Entry))
            });
            let ok = g.guards.get(&pc).map_or(Iv { lo: 0, hi: 0xff }, |e| e.1);
            let addr = match fixed {
                true => a.addr,
                false => Iv {
                    lo: base.lo + ok.lo,
                    hi: base.hi + ok.hi,
                },
            };
            if sure && block.lo <= addr.lo && addr.hi <= block.hi {
                proven.push((key, (addr, base)));
                continue;
            }
        }
        let obj = object_id(out, block.lo as u16, block.hi as u16, [pc].into_iter());
        out.machine.insert(key, (obj, block));
        // The block bounds a load from an address the program's bytes form, none of their
        // values; a store so formed is left its stores' checks.
        match (code, a.store) {
            (true, _) => proven.extend(crate::vsa::meet(a.addr, block).map(|m| (key, (m, base)))),
            (false, false) => proven.push((key, (block, block))),
            (false, true) => {}
        }
    }
}

/// The index of object `[lo, hi]` among `out`'s, made where new, serving the loads `pcs`.
fn object_id(out: &mut Objs, lo: u16, hi: u16, pcs: impl Iterator<Item = u16>) -> u16 {
    let id = match out.objects.iter().position(|o| (o.0, o.1) == (lo, hi)) {
        Some(i) => i,
        None => {
            out.objects.push((lo, hi, Vec::new()));
            out.objects.len() - 1
        }
    };
    let ps = &mut out.objects[id].2;
    ps.extend(pcs);
    ps.sort_unstable();
    ps.dedup();
    id as u16
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
                let upto = Bits::range(Iv { lo: 0, hi: p.idx });
                let y = ys
                    .and_then(|b| Bits::from_words(b).and(upto).hull())
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
    let mut span: Option<Iv> = None;
    let mut m = 0;
    for l in ls {
        let b = l.base?;
        span = Some(span.map_or(b, |h| hull(h, b)));
        if l.y.lo == l.y.hi {
            m = m.max(l.y.hi);
        }
    }
    let h = span?;
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
pub(crate) fn writes(op: u8, r: usize) -> bool {
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
