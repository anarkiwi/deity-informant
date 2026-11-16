//! Status lanes: a stack slot each of whose definitions packs single-bit values (the status
//! byte `PHP`, `BRK` or an interrupt frame pushes, from the flags) holds each bit in a lane
//! register of its own, copied where the slot is defined. Every op reading the slot reads the
//! byte rebuilt from its lanes and the bits all definitions agree on, so value numbering
//! takes a pulled flag (`PLP`, `RTI`) straight from its lane and a byte read as data (`PLA`)
//! packs only the bits its consumers keep. The slot keeps its packed definition for what reads
//! it as the stack byte: deopts and the memory image.

use crate::ir::{k, mask, reg, tmp, Function, LOp, NREG};
use di_pcode::{Opc, Space, Vn};
use fixedbitset::FixedBitSet;
use rustc_hash::FxHashMap;

/// One bit of a packed byte: a constant, or a one-bit register at a version (its writes so
/// far in the block).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Bit {
    K(bool),
    R(u16, u32),
}

type Bits = [Bit; 8];

/// Ops to insert at an op index (after it when set) and, before a read, the slot it reads and
/// the byte to read in its place.
type Edit = (usize, bool, Vec<LOp>, Option<(u32, Vn)>);

const ZERO: Bits = [Bit::K(false); 8];

/// Where a slot is defined (and how, when it packs single-bit values) and read.
#[derive(Default)]
struct Uses {
    defs: Vec<(usize, usize, Option<Bits>)>,
    reads: Vec<(usize, usize)>,
}

/// Whether register `r` of `f` holds 0 or 1: a flag or a lane.
fn one_bit(f: &Function, r: u32) -> bool {
    let lanes = NREG as u32 + f.nslots;
    (8..15).contains(&r) || (lanes..lanes + f.nlanes).contains(&r)
}

/// The bits of `v` in a block where `dec` decomposes temporaries and `ver` counts register
/// writes.
fn bits(
    f: &Function,
    v: Vn,
    dec: &FxHashMap<u32, Bits>,
    ver: &FxHashMap<u32, u32>,
) -> Option<Bits> {
    if v.size() != 1 {
        return None;
    }
    match v.space() {
        Space::Const => Some(std::array::from_fn(|i| Bit::K(v.off() >> i & 1 != 0))),
        Space::Reg if one_bit(f, v.off()) => {
            let mut b = ZERO;
            b[0] = Bit::R(v.off() as u16, ver.get(&v.off()).copied().unwrap_or(0));
            Some(b)
        }
        Space::Unique => dec.get(&v.off()).copied(),
        _ => None,
    }
}

/// The bits of `op`'s one-byte result: copies, shifts and masks of such bytes and ors of
/// disjoint ones.
fn eval(
    f: &Function,
    op: &LOp,
    dec: &FxHashMap<u32, Bits>,
    ver: &FxHashMap<u32, u32>,
) -> Option<Bits> {
    let a = bits(f, op.ins[0], dec, ver)?;
    let c = |v: Vn| (v.space() == Space::Const).then_some(v.off());
    match op.opc {
        Opc::Copy => Some(a),
        Opc::IntLeft => {
            let s = c(op.ins[1])? as usize;
            Some(std::array::from_fn(|i| match i.checked_sub(s) {
                Some(j) => a[j],
                None => Bit::K(false),
            }))
        }
        Opc::IntRight => {
            let s = c(op.ins[1])? as usize;
            Some(std::array::from_fn(|i| {
                a.get(i + s).copied().unwrap_or(Bit::K(false))
            }))
        }
        Opc::IntAnd => {
            let m = c(op.ins[1])?;
            Some(std::array::from_fn(|i| match m >> i & 1 {
                0 => Bit::K(false),
                _ => a[i],
            }))
        }
        Opc::IntOr => {
            let b = bits(f, op.ins[1], dec, ver)?;
            let mut r = ZERO;
            for i in 0..8 {
                r[i] = match (a[i], b[i]) {
                    (Bit::K(false), x) | (x, Bit::K(false)) => x,
                    (Bit::K(true), Bit::K(true)) => Bit::K(true),
                    _ => return None,
                };
            }
            Some(r)
        }
        _ => None,
    }
}

/// The bits of slot `s` that op `o` of block `b` reads and the rest of the block may observe,
/// by demanded bits backward from the block's end, where every value is demanded whole, as is
/// every register at an op handing the machine state over.
fn demand(f: &Function, b: usize, o: usize, s: u32) -> u32 {
    let ops = &f.blocks[b].ops;
    let mut dm: FxHashMap<u32, u32> = FxHashMap::default();
    let key = |v: Vn| (v.space() as u32) << 16 | v.off();
    let get = |dm: &FxHashMap<u32, u32>, v: Vn| dm.get(&key(v)).copied().unwrap_or(mask(v.size()));
    let mut slot = 0;
    for (i, op) in ops.iter().enumerate().skip(o).rev() {
        if matches!(
            op.opc,
            Opc::Call | Opc::Return | Opc::BranchInd | Opc::CallOther
        ) {
            dm.retain(|&x, _| x >> 16 != Space::Reg as u32);
        }
        let d = match op.out.space() {
            Space::Reg | Space::Unique => {
                let d = get(&dm, op.out);
                dm.insert(key(op.out), 0);
                d
            }
            _ => 0,
        };
        let c = |v: Vn| (v.space() == Space::Const).then_some(v.off());
        for (j, &v) in op.inputs().iter().enumerate() {
            let m = mask(v.size());
            let x = match (op.opc, c(op.ins[1 - j.min(1)])) {
                _ if d == 0 && !op.effectful() => 0,
                (Opc::Copy | Opc::IntOr | Opc::IntXor | Opc::IntZext, _) => d & m,
                (Opc::IntAnd, Some(k)) => d & k,
                (Opc::IntAnd, None) => d & m,
                (Opc::IntLeft, Some(k)) if j == 0 && k < 32 => d >> k,
                (Opc::IntRight, Some(k)) if j == 0 && k < 32 => ((d as u64) << k & m as u64) as u32,
                _ => m,
            };
            if i == o && v.space() == Space::Reg && v.off() == s {
                slot |= x;
            } else if matches!(v.space(), Space::Reg | Space::Unique) {
                *dm.entry(key(v)).or_insert(0) |= x;
            }
        }
    }
    slot
}

/// The webs of a slot's definitions and reads `u`, by reaching definitions: per web the
/// indices of its definitions and reads, `None` for a web the function's entry reaches (the
/// byte before any push).
fn webs(f: &Function, u: &Uses) -> Vec<Option<(Vec<usize>, Vec<usize>)>> {
    let (n, entry) = (f.blocks.len(), u.defs.len());
    let mut last: Vec<Option<usize>> = vec![None; n];
    for (i, d) in u.defs.iter().enumerate() {
        last[d.0] = Some(i);
    }
    let mut inn = vec![FixedBitSet::with_capacity(entry + 1); n];
    inn[0].insert(entry);
    let mut work: Vec<usize> = (0..n).collect();
    while let Some(b) = work.pop() {
        let out = match last[b] {
            Some(i) => {
                let mut o = FixedBitSet::with_capacity(entry + 1);
                o.insert(i);
                o
            }
            None => inn[b].clone(),
        };
        for t in f.blocks[b].succs(&f.tables) {
            let t = t as usize;
            if !out.is_subset(&inn[t]) {
                inn[t].union_with(&out);
                work.push(t);
            }
        }
    }
    let mut up: Vec<usize> = (0..=entry).collect();
    fn root(up: &mut [usize], mut x: usize) -> usize {
        while up[x] != x {
            up[x] = up[up[x]];
            x = up[x];
        }
        x
    }
    let reach: Vec<Vec<usize>> = u
        .reads
        .iter()
        .map(|&(b, o)| {
            let local = u.defs.iter().rposition(|d| d.0 == b && d.1 < o);
            local.map_or_else(|| inn[b].ones().collect(), |i| vec![i])
        })
        .collect();
    for r in &reach {
        for w in r.windows(2) {
            let (x, y) = (root(&mut up, w[0]), root(&mut up, w[1]));
            up[x] = y;
        }
    }
    let mut by: FxHashMap<usize, (Vec<usize>, Vec<usize>)> = FxHashMap::default();
    for (j, r) in reach.iter().enumerate() {
        if let Some(&d) = r.first() {
            by.entry(root(&mut up, d)).or_default().1.push(j);
        }
    }
    for i in 0..=entry {
        let x = root(&mut up, i);
        if let Some(w) = by.get_mut(&x) {
            w.0.push(i);
        }
    }
    let mut out: Vec<_> = by.into_values().collect();
    out.sort_unstable();
    out.into_iter()
        .map(|w| (!w.0.contains(&entry)).then_some(w))
        .collect()
}

/// Lanes for one web of slot `s`: its definitions `(block, op, bits)` and reads `(block, op,
/// bits observed)`, as edits to their blocks.
fn lane(
    f: &mut Function,
    s: u32,
    defs: &[(usize, usize, Bits)],
    reads: &[(usize, usize, u32)],
    edits: &mut [Vec<Edit>],
) {
    let seen = reads.iter().fold(0, |m, r| m | r.2);
    let fixed = (0..8).fold(0u32, |m, i| match defs[0].2[i] {
        Bit::K(x) if defs.iter().all(|d| d.2[i] == Bit::K(x)) => m | (x as u32) << i,
        _ => m,
    });
    let free = (0..8).filter(|&i| {
        seen >> i & 1 != 0 && defs.iter().any(|d| d.2[i] != Bit::K(fixed >> i & 1 != 0))
    });
    let lanes: Vec<(usize, Vn)> = free
        .map(|i| {
            f.nlanes += 1;
            (i, reg((NREG as u32 + f.nslots + f.nlanes - 1) as u16, 1))
        })
        .collect();
    for &(b, o, d) in defs {
        let ops = lanes
            .iter()
            .map(|&(i, l)| {
                let src = match d[i] {
                    Bit::K(x) => k(x as u32, 1),
                    Bit::R(r, _) => reg(r, 1),
                };
                LOp::new(Opc::Copy, l, src, Vn::NONE, 0)
            })
            .collect();
        edits[b].push((o, true, ops, None));
    }
    for &(b, o, d) in reads {
        let mut ops = Vec::new();
        let mut fresh = |f: &mut Function, opc, a, b| {
            f.ntemps += 1;
            let t = tmp((f.ntemps - 1) as u16, 1);
            ops.push(LOp::new(opc, t, a, b, 0));
            t
        };
        let mut acc = None;
        for &(i, l) in lanes.iter().filter(|e| d >> e.0 & 1 != 0) {
            let x = match i {
                0 => l,
                _ => fresh(f, Opc::IntLeft, l, k(i as u32, 1)),
            };
            acc = Some(acc.map_or(x, |a| fresh(f, Opc::IntOr, a, x)));
        }
        let v = match acc {
            None => k(fixed & d, 1),
            Some(a) if fixed & d == 0 => a,
            Some(a) => fresh(f, Opc::IntOr, a, k(fixed & d, 1)),
        };
        edits[b].push((o, false, ops, Some((s, v))));
    }
}

/// Split every web of a slot all of whose definitions pack single-bit values into lanes: the
/// bits its definitions agree on are constants, the others lane registers set beside each
/// definition; each read reads the byte rebuilt from them.
pub fn split(f: &mut Function) {
    let (lo, hi) = (NREG as u32, NREG as u32 + f.nslots);
    let mut slots: FxHashMap<u32, Uses> = FxHashMap::default();
    for (bi, b) in f.blocks.iter().enumerate() {
        let mut dec: FxHashMap<u32, Bits> = FxHashMap::default();
        let mut ver: FxHashMap<u32, u32> = FxHashMap::default();
        for (oi, op) in b.ops.iter().enumerate() {
            for v in op.inputs() {
                if v.space() == Space::Reg && (lo..hi).contains(&v.off()) {
                    slots.entry(v.off()).or_default().reads.push((bi, oi));
                }
            }
            let out = op.out;
            match out.space() {
                Space::Unique => match (out.size() == 1).then(|| eval(f, op, &dec, &ver)).flatten()
                {
                    Some(x) => dec.insert(out.off(), x),
                    None => dec.remove(&out.off()),
                },
                Space::Reg => {
                    if (lo..hi).contains(&out.off()) {
                        let now = |b: &Bit| match *b {
                            Bit::R(r, v) => ver.get(&(r as u32)).copied().unwrap_or(0) == v,
                            Bit::K(_) => true,
                        };
                        let d = (op.opc == Opc::Copy)
                            .then(|| bits(f, op.ins[0], &dec, &ver))
                            .flatten()
                            .filter(|d| d.iter().all(now));
                        slots.entry(out.off()).or_default().defs.push((bi, oi, d));
                    }
                    *ver.entry(out.off()).or_default() += 1;
                    None
                }
                _ => None,
            };
        }
    }
    // Per block: (op index, after it, ops to insert, the slot read and its rebuilt byte).
    let mut edits: Vec<Vec<Edit>> = vec![Vec::new(); f.blocks.len()];
    let mut order: Vec<_> = slots.into_iter().collect();
    order.sort_unstable_by_key(|e| e.0);
    for (s, mut u) in order {
        u.reads.dedup();
        for (ds, rs) in webs(f, &u).into_iter().flatten() {
            let Some(defs) = ds
                .iter()
                .map(|&i| Some((u.defs[i].0, u.defs[i].1, u.defs[i].2?)))
                .collect::<Option<Vec<_>>>()
            else {
                continue;
            };
            let reads: Vec<(usize, usize, u32)> = rs
                .iter()
                .map(|&j| {
                    (
                        u.reads[j].0,
                        u.reads[j].1,
                        demand(f, u.reads[j].0, u.reads[j].1, s),
                    )
                })
                .collect();
            lane(f, s, &defs, &reads, &mut edits);
        }
    }
    for (blk, mut ed) in f.blocks.iter_mut().zip(edits) {
        if ed.is_empty() {
            continue;
        }
        ed.sort_by_key(|e| (e.0, e.1));
        let mut ed = ed.into_iter().peekable();
        let old = std::mem::take(&mut blk.ops);
        for (oi, mut op) in old.into_iter().enumerate() {
            while let Some((_, false, ops, read)) = ed.next_if(|e| e.0 == oi && !e.1) {
                blk.ops.extend(ops);
                let (s, v) = read.unwrap();
                let n = op.arity();
                for x in op.ins[..n]
                    .iter_mut()
                    .filter(|x| x.space() == Space::Reg && x.off() == s)
                {
                    *x = v;
                }
            }
            blk.ops.push(op);
            while let Some((_, _, ops, _)) = ed.next_if(|e| e.0 == oi) {
                blk.ops.extend(ops);
            }
        }
    }
}
