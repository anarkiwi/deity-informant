//! Table extents of the last-entry rule ([`crate::Opts::overrun_last`]): every table an
//! absolute-indexed instruction reads or writes is bounded to its own extent, a read past it
//! reads its last valid entry and a store there writes nothing.
//!
//! A *site* is an instruction of the entries whose constant operand plus an index register
//! forms its address. A site of one address reads a fixed entry. Its index is *constant data* where every value it may hold comes, through
//! registers and arithmetic, from immediates and bytes no store of the entries may write (a
//! lookup at an instrument byte): such a site reads fixed entries of whatever table holds them
//! and makes no base. Any other site starts a table at its operand plus its index's floor (the
//! least index the code's tests and constants prove at any site of that operand,
//! `Site::floor`), its *step* the
//! greatest common divisor of the analysis's index values' differences (a record's stride).
//! Two sites read at one index value (the register unchanged between them) one entry apart,
//! or whole entries apart where their address sets overlap and one instruction combines the
//! two values (`freq[i + 2] - freq[i]`), are one table read at two offsets: the one whose
//! base no other site reads alone is *inner* (the upper where both or neither is), reading
//! the other's table, and starts none; split parts (`lo[i]`, `hi[i]`), whose values no
//! instruction combines, stay columns. A table of step `s` at `t`
//! runs to the first start at or past `t + s`, the first byte of an instruction or the end of
//! the held bytes holding it; sites read at one index value with no offset between them inside
//! one entry are the columns of one record table, whose entries are the least of theirs.

use crate::analysis::{flow_succs, operand_bytes, Flow, Graph, Iv, Tg};
use crate::ptr::StoreKey;
use di_pcode::reg::{A, X, Y};
use di_pcode::Template;
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};

/// A site's table: its inclusive bytes `[lo, hi]` at `stride` per entry, the last entry at
/// `hi + 1 - stride`; `fixed` where no store of the entries may write the byte of an entry
/// its sites read (`lo` plus a multiple of `stride`; the other columns of a record table are
/// other tables').
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Table {
    pub lo: u32,
    pub hi: u32,
    pub stride: u32,
    pub fixed: bool,
}

/// An indexed access: its key, index register, operand, address set and whether it stores
/// (a store's table is its own whatever its index: it writes no fixed entry of another's).
struct Site {
    key: StoreKey,
    r: usize,
    base: u32,
    addrs: Vec<u32>,
    /// The addresses the analysis gives (its address set, else the operand plus its index's
    /// values): their differences are the entries'.
    own: Vec<u32>,
    store: bool,
    /// The floor a compare with an immediate and the branch it decides set on the index on
    /// the one path into the site ([`compared`]).
    test: Option<u32>,
}

impl Site {
    /// The least index the code's tests and constants prove ([`compared`]), or one entry
    /// past it where the addresses never take that, a 1-based table whose operand is its start
    /// less one entry. A higher least, or one the data alone gives, moves no start: the
    /// entries below it are the table's, never read.
    fn floor(&self) -> u32 {
        let proved = self.test.unwrap_or(0);
        let seen = self.addrs[0].saturating_sub(self.base);
        if seen == proved + self.step() {
            seen
        } else {
            proved
        }
    }
    fn step(&self) -> u32 {
        let lo = self.own[0];
        (self.own.iter().skip(1))
            .fold(0, |g, &a| gcd(g, a - lo))
            .max(1)
    }
}

fn gcd(a: u32, b: u32) -> u32 {
    if b == 0 {
        a
    } else {
        gcd(b, a % b)
    }
}

/// The indexed accesses of `g` whose operand is constant and lies in the held bytes `held`
/// (`[lo, hi)`), with their address sets ([`Graph::addr_sets`], else their intervals).
fn sites(g: &Graph, held: (u32, u32)) -> Vec<Site> {
    let mut out = Vec::new();
    let mut pcs: Vec<u16> = g.vars.keys().copied().collect();
    pcs.sort_unstable();
    for pc in pcs {
        for v in &g.vars[&pc] {
            let t = Template::of(v.op);
            let b = operand_bytes(&g.img, &g.cells, pc, t.len);
            let (3, [Some(l), Some(h)]) = (t.len, b) else {
                continue;
            };
            let Some(r) = [X, Y]
                .into_iter()
                .find(|&r| crate::dispatch::moves(t, b, r))
            else {
                continue;
            };
            let base = u16::from_le_bytes([l, h]) as u32;
            for (ai, a) in v.acc.iter().enumerate() {
                if a.dummy || a.ptr.is_some() || base < held.0 || base >= held.1 {
                    continue;
                }
                let addrs: Vec<u32> = match g.addr_sets.get(&(pc, a.store)) {
                    Some(s) => s.iter().map(|&x| x as u32).collect(),
                    None => (a.addr.lo..=a.addr.hi).collect(),
                };
                let hull = g.bases.get(&(pc, v.op, ai)).map_or(a.addr, |b| b.0);
                let mine = g.addr_sets.get(&(pc, a.store));
                // Without an address set, the operand plus each value the index may hold
                // (a record index shifted left: one entry per record).
                let held = g.regs.get(&pc).map(|x| crate::vsa::Bits::from_words(x[r]));
                let own: Vec<u32> = match (mine, held) {
                    (Some(s), _) => s.iter().map(|&x| x as u32).collect(),
                    (None, Some(b)) if b.values().next().is_some() => {
                        b.values().map(|v| base + v).collect()
                    }
                    (None, _) => (hull.lo..=hull.hi).collect(),
                };
                if addrs.first().is_some_and(|&x| x >= base) {
                    out.push(Site {
                        own,
                        test: None,
                        key: (pc, v.op, ai),
                        r,
                        base,
                        addrs,
                        store: a.store,
                    });
                }
            }
        }
    }
    out
}

/// The predecessors of each instruction start of `g` over its flow.
fn preds(g: &Graph) -> FxHashMap<u16, Vec<u16>> {
    let mut out: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
    for (&pc, vs) in &g.vars {
        for v in vs {
            for s in flow_succs(&v.flow) {
                out.entry(s).or_default().push(pc);
            }
        }
    }
    out
}

/// The bytes a store of `g` may write: its address set, else its interval.
fn written(g: &Graph) -> FixedBitSet {
    let mut out = FixedBitSet::with_capacity(0x10000);
    for (&pc, vs) in &g.vars {
        for a in vs
            .iter()
            .flat_map(|v| &v.acc)
            .filter(|a| a.store && !a.dummy)
        {
            match g.addr_sets.get(&(pc, true)) {
                Some(s) => out.extend(s.iter().map(|&x| x as usize)),
                None => out.insert_range(a.addr.lo as usize..(a.addr.hi as usize + 1).min(0x10000)),
            }
        }
    }
    out
}

/// Whether the value register `r` holds before `pc` is constant data: from immediates and
/// bytes outside `w` alone, through registers and arithmetic, along every definition.
fn constant_index(
    g: &Graph,
    preds: &FxHashMap<u16, Vec<u16>>,
    w: &FixedBitSet,
    roots: &[u16],
    (pc, r): (u16, usize),
) -> bool {
    let mut seen = FxHashSet::default();
    let mut work = vec![(pc, r)];
    // A pointer's read stays in the song's data, which no store writes (*Machine model*).
    let fixed = |pc: u16, a: &crate::analysis::Access| {
        a.ptr.is_some()
            || match g.addr_sets.get(&(pc, false)) {
                Some(s) => s.iter().all(|&x| !w.contains(x as usize)),
                None => w.count_ones(a.addr.lo as usize..a.addr.hi as usize + 1) == 0,
            }
    };
    while let Some((at, r)) = work.pop() {
        if !seen.insert((at, r)) {
            continue;
        }
        let Some(ps) = preds.get(&at).filter(|_| !roots.contains(&at)) else {
            return false;
        };
        for &p in ps {
            for v in &g.vars[&p] {
                if matches!(v.flow, Flow::Jsr { ret, .. } if ret == at) {
                    work.push((p, r));
                    continue;
                }
                if !crate::objects::writes(v.op, r) {
                    work.push((p, r));
                    continue;
                }
                let t = Template::of(v.op);
                let imm = operand_bytes(&g.img, &g.cells, p, t.len);
                let loads: Vec<_> = v.acc.iter().filter(|a| !a.store && !a.dummy).collect();
                match v.op {
                    0xa9 | 0xa2 | 0xa0 => {
                        if imm[0].is_none() {
                            return false;
                        }
                    }
                    0xaa | 0xa8 => work.push((p, A)),
                    0x8a => work.push((p, X)),
                    0x98 => work.push((p, Y)),
                    0xe8 | 0xca | 0xc8 | 0x88 | 0x0a | 0x2a | 0x4a | 0x6a => work.push((p, r)),
                    _ if !loads.is_empty() && loads.iter().all(|a| fixed(p, a)) => {
                        if is_alu(v.op) {
                            work.push((p, r));
                        }
                    }
                    _ if loads.is_empty() && is_alu(v.op) && imm[0].is_some() => work.push((p, r)),
                    _ => return false,
                }
            }
        }
    }
    true
}

/// The floor of index register `r` at `pc` the code's tests and constants prove: the least,
/// over the paths into `pc` back through `TAX`/`TAY` copies of `A`, of the immediate a path
/// loads it with, the compare deciding the branch arm it runs (`CMP`/`CPX`/`CPY #k` just
/// before it; `BCS` or `BPL` taken, `BCC` or `BMI` not: at least `k`) or the zero test of an
/// instruction just writing it (`BNE` taken, `BEQ` not: at least 1), one more past an
/// increment where it never holds `$FF`, one less past a decrement; none where a path
/// proves nothing (another write, a call, an entry, a cycle).
fn compared(g: &Graph, preds: &FxHashMap<u16, Vec<u16>>, pc: u16, r: usize) -> Option<u32> {
    floor_at(g, preds, pc, r, &mut FxHashSet::default())
}

/// The floor of register `q` entering `at`: the least over its predecessors of what each
/// edge proves (:func:`compared`), none where one proves nothing or the walk cycles.
fn floor_at(
    g: &Graph,
    preds: &FxHashMap<u16, Vec<u16>>,
    at: u16,
    q: usize,
    seen: &mut FxHashSet<(u16, usize)>,
) -> Option<u32> {
    if !seen.insert((at, q)) {
        return None;
    }
    let ps = preds.get(&at).filter(|ps| !ps.is_empty())?;
    let mut least = u32::MAX;
    for &p in ps {
        least = least.min(edge_floor(g, preds, (p, at), q, seen)?);
    }
    Some(least)
}

/// What the edge `p` to `at` proves of register `q` at `at` ([`floor_at`]).
fn edge_floor(
    g: &Graph,
    preds: &FxHashMap<u16, Vec<u16>>,
    (p, at): (u16, u16),
    q: usize,
    seen: &mut FxHashSet<(u16, usize)>,
) -> Option<u32> {
    let v = g.vars.get(&p)?.first()?;
    let imm = || operand_bytes(&g.img, &g.cells, p, 2)[0].map(u32::from);
    match (&v.flow, v.op) {
        (Flow::Jsr { .. }, _) => None,
        (Flow::Br { t: Tg::At(t), f }, op) => {
            let tested = match preds.get(&p).map(Vec::as_slice) {
                Some(&[c]) => g.vars.get(&c).and_then(|vs| vs.first()).map(|w| (c, w.op)),
                _ => None,
            };
            let proved = tested.and_then(|(c, cop)| match (op, at == *t, at == *f) {
                // A carry or sign the compare left: the register is at least its operand.
                (0xb0 | 0x10, true, _) | (0x90 | 0x30, _, true) => {
                    let cmp = [(A, 0xc9), (X, 0xe0), (Y, 0xc0)];
                    let k = operand_bytes(&g.img, &g.cells, c, 2)[0]?;
                    cmp.contains(&(q, cop)).then_some(k as u32)
                }
                // A zero test of the register just written or compared with 0: at least 1.
                (0xd0, true, _) | (0xf0, _, true) => {
                    let zero = [(A, 0xc9), (X, 0xe0), (Y, 0xc0)].contains(&(q, cop))
                        && operand_bytes(&g.img, &g.cells, c, 2)[0] == Some(0);
                    (zero || crate::objects::writes(cop, q)).then_some(1)
                }
                _ => None,
            });
            proved.or_else(|| floor_at(g, preds, p, q, seen))
        }
        // An increment where the register never holds `$FF` (the analysis's values there).
        (_, 0xc8 | 0xe8) if q == if v.op == 0xc8 { Y } else { X } => {
            let hull = g
                .regs
                .get(&p)
                .and_then(|x| crate::vsa::Bits::from_words(x[q]).hull());
            let f = floor_at(g, preds, p, q, seen)?;
            hull.filter(|h| h.hi < 0xff).map(|_| f + 1)
        }
        (_, 0x88 | 0xca) if q == if v.op == 0x88 { Y } else { X } => {
            floor_at(g, preds, p, q, seen)?.checked_sub(1)
        }
        (_, 0xa8) if q == Y => floor_at(g, preds, p, A, seen),
        (_, 0xaa) if q == X => floor_at(g, preds, p, A, seen),
        (_, 0xa9) if q == A => imm(),
        (_, 0xa2) if q == X => imm(),
        (_, 0xa0) if q == Y => imm(),
        (_, op) if crate::objects::writes(op, q) => None,
        _ => floor_at(g, preds, p, q, seen),
    }
}

/// Whether opcode `op` combines `A` with its operand into `A` (`ORA`, `AND`, `EOR`, `ADC`,
/// `SBC`).
fn is_alu(op: u8) -> bool {
    op & 3 == 1 && matches!(op >> 5, 0 | 1 | 2 | 3 | 7)
}

/// Whether ascending `a` and `b` share an address.
fn overlap(a: &[u32], b: &[u32]) -> bool {
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => return true,
        }
    }
    false
}

/// Whether the value the instruction at `i` loads into `A` reaches the instruction at `j`, an
/// `ORA`, `AND`, `EOR`, `ADC`, `SBC` or `CMP` of `A` with its own load, along the flow with no
/// instruction between writing `A`: the two reads' values are one quantity's.
fn combined(g: &Graph, i: u16, j: u16) -> bool {
    let writes_a =
        |pc: &u16| (g.vars.get(pc).into_iter().flatten()).any(|v| crate::objects::writes(v.op, A));
    let alu = |v: &crate::analysis::Var| v.op & 3 == 1 && !matches!(v.op >> 5, 4 | 5);
    if !writes_a(&i) || !g.vars.get(&j).is_some_and(|vs| vs.iter().any(alu)) {
        return false;
    }
    let mut seen = FxHashSet::default();
    let mut work: Vec<u16> = g.vars[&i].iter().flat_map(|v| next_of(&v.flow)).collect();
    while let Some(pc) = work.pop() {
        if pc == j {
            return true;
        }
        if !seen.insert(pc) || writes_a(&pc) {
            continue;
        }
        work.extend((g.vars.get(&pc).into_iter().flatten()).flat_map(|v| next_of(&v.flow)));
    }
    false
}

/// The sites reached from each site with its index value plus a constant `(i, j, delta)`: along
/// the flow from it while no instruction but an increment or decrement writes its register,
/// up to a call or return.
fn same_index(g: &Graph, sites: &[Site]) -> Vec<(usize, usize, i32)> {
    let mut at: FxHashMap<u16, Vec<usize>> = FxHashMap::default();
    for (i, s) in sites.iter().enumerate() {
        at.entry(s.key.0).or_default().push(i);
    }
    let mut out = Vec::new();
    for (i, s) in sites.iter().enumerate() {
        let mut seen = FxHashSet::default();
        let mut work: Vec<(u16, i32)> = (g.vars[&s.key.0].iter())
            .flat_map(|v| next_of(&v.flow))
            .map(|pc| (pc, 0))
            .collect();
        while let Some((pc, delta)) = work.pop() {
            if !seen.insert(pc) {
                continue;
            }
            out.extend(
                (at.get(&pc).into_iter().flatten())
                    .filter(|&&j| sites[j].r == s.r && j != i)
                    .map(|&j| (i, j, delta)),
            );
            let Some(vs) = g.vars.get(&pc) else {
                continue;
            };
            // An increment or decrement of the register moves the index by one.
            let step = match vs
                .iter()
                .map(|v| v.op)
                .find(|&op| crate::objects::writes(op, s.r))
            {
                None => 0,
                Some(0xe8 | 0xc8) => 1,
                Some(0xca | 0x88) => -1,
                Some(_) => continue,
            };
            work.extend(
                vs.iter()
                    .flat_map(|v| next_of(&v.flow))
                    .map(|n| (n, delta + step)),
            );
        }
    }
    out
}

/// The successors along which an index value stays: not through calls, returns or computed
/// jumps.
fn next_of(f: &Flow) -> Vec<u16> {
    match f {
        Flow::Next(_) | Flow::Br { .. } | Flow::Jmp(_) => flow_succs(f),
        _ => Vec::new(),
    }
}

/// The table of each load site of `g` (entries `roots`) whose index is not constant data, in
/// the held bytes `held` (`[lo, hi)`).
pub fn tables(g: &Graph, roots: &[u16], held: (u32, u32)) -> FxHashMap<StoreKey, Table> {
    let _span = crate::prof::span("tables");
    let preds = preds(g);
    let mut sites = sites(g, held);
    for s in &mut sites {
        s.test = compared(g, &preds, s.key.0, s.r);
    }
    let w = written(g);
    let live: Vec<bool> = (sites.iter())
        .map(|s| {
            s.addrs.len() > 1 && (s.store || !constant_index(g, &preds, &w, roots, (s.key.0, s.r)))
        })
        .collect();
    // A pair's bases as one index value reads them: the second's moved by the index's delta.
    let pairs: Vec<(usize, usize, i64, i32)> = same_index(g, &sites)
        .into_iter()
        .filter(|&(i, j, _)| live[i] && live[j])
        .map(|(i, j, d)| {
            (
                i,
                j,
                sites[j].base as i64 + d as i64 - sites[i].base as i64,
                d,
            )
        })
        .collect();
    // Two sites reading one address at one index value read one table, the later the
    // earlier's. So do two whole entries apart where one entry apart, or where their address
    // sets overlap and one instruction combines the two values: the base no other site reads
    // alone is the inner one, reading the other's table (the upper is, where both or neither
    // is).
    let adjacent = |i: usize, j: usize, d: i64| {
        let s = sites[i].step() as u64;
        let n = d.unsigned_abs();
        sites[j].step() as u64 == s
            && n.is_multiple_of(s)
            && (n == s
                || n > 0
                    && overlap(&sites[i].addrs, &sites[j].addrs)
                    && combined(g, sites[i].key.0, sites[j].key.0))
    };
    let paired: FxHashSet<usize> = (pairs.iter())
        .filter(|&&(i, j, d, _)| d == 0 || adjacent(i, j, d))
        .flat_map(|&(i, j, _, _)| [i, j])
        .collect();
    let alone: FxHashSet<u32> = (0..sites.len())
        .filter(|i| live[*i] && !paired.contains(i))
        .map(|i| sites[i].base)
        .collect();
    let mut of: Vec<usize> = (0..sites.len()).collect();
    for &(i, j, d, _) in &pairs {
        if d == 0 {
            if of[j] == j && of[i] != j {
                of[j] = i;
            }
            continue;
        }
        if !adjacent(i, j, d) {
            continue;
        }
        let (lo, hi) = if d > 0 { (i, j) } else { (j, i) };
        match (
            alone.contains(&sites[lo].base),
            alone.contains(&sites[hi].base),
        ) {
            (false, true) if of[lo] == lo => of[lo] = hi,
            _ if of[hi] == hi => of[hi] = lo,
            _ => {}
        }
    }
    // Each site reads the table of the site its chain of those ends at.
    for i in 0..of.len() {
        let mut k = i;
        for _ in 0..of.len() {
            if of[k] == k {
                break;
            }
            k = of[k];
        }
        of[i] = k;
    }
    // One operand is one table: its floor is the least of its sites'.
    let mut floor: FxHashMap<u32, u32> = FxHashMap::default();
    for (_, x) in (sites.iter().enumerate()).filter(|&(i, _)| live[i] && of[i] == i) {
        let f = floor.entry(x.base).or_insert(u32::MAX);
        *f = (*f).min(x.floor());
    }
    let start = |i: usize| {
        let x = &sites[of[i]];
        x.base + floor.get(&x.base).copied().unwrap_or(x.floor())
    };
    let mut starts: Vec<u32> = (0..sites.len())
        .filter(|&i| live[i] && of[i] == i)
        .map(start)
        .collect();
    starts.sort_unstable();
    starts.dedup();
    let end = |t: u32, s: u32| {
        let next = starts
            .iter()
            .copied()
            .find(|&x| x >= t + s)
            .unwrap_or(held.1);
        let code = (t as usize..held.1 as usize)
            .find(|&x| g.code.contains(x))
            .unwrap_or(held.1 as usize);
        next.min(code as u32).min(held.1)
    };
    let mut count: Vec<u32> = (0..sites.len())
        .map(|i| {
            let (t, s) = (start(i), sites[of[i]].step());
            (end(t, s).saturating_sub(t)).div_ceil(s)
        })
        .collect();
    // Columns of one record: the least entries of those read at one index value.
    // Columns of one record are read at one index value itself.
    let cols: Vec<(usize, usize)> = (pairs.iter())
        .filter(|&&(i, j, d, delta)| {
            let s = sites[i].step().max(sites[j].step());
            delta == 0 && of[i] != of[j] && d.unsigned_abs() >= s as u64
        })
        .map(|&(i, j, _, _)| (of[i], of[j]))
        .collect();
    let mut changed = true;
    while changed {
        changed = false;
        for &(a, b) in &cols {
            let n = count[a].min(count[b]);
            for k in [a, b] {
                changed |= count[k] != n;
                count[k] = n;
            }
        }
    }
    let mut out: FxHashMap<StoreKey, Table> = (0..sites.len())
        .filter(|&i| live[i] && count[of[i]] > 0)
        .map(|i| {
            let (t, s, n) = (start(i), sites[of[i]].step(), count[of[i]]);
            let hi = t + n * s - 1;
            let table = Table {
                lo: t,
                hi,
                stride: s,
                fixed: false,
            };
            (sites[i].key, table)
        })
        .collect();
    // A store with a table writes its entries alone (past it nothing); another store its
    // address set or interval.
    let mut ws = FixedBitSet::with_capacity(0x10000);
    for (&pc, vs) in &g.vars {
        for (v, ai, a) in (vs.iter())
            .flat_map(|v| v.acc.iter().enumerate().map(move |(ai, a)| (v, ai, a)))
            .filter(|(_, _, a)| a.store && !a.dummy)
        {
            match (out.get(&(pc, v.op, ai)), g.addr_sets.get(&(pc, true))) {
                (Some(t), _) => ws.extend(t.entries().map(|x| x as usize)),
                (None, Some(s)) => ws.extend(s.iter().map(|&x| x as usize)),
                (None, None) => {
                    ws.insert_range(a.addr.lo as usize..(a.addr.hi as usize + 1).min(0x10000))
                }
            }
        }
    }
    for t in out.values_mut() {
        t.fixed = t.entries().all(|a| !ws.contains(a as usize));
    }
    out
}

impl Table {
    /// Whether the addresses `a` lie in the table.
    pub fn holds(&self, a: Iv) -> bool {
        self.lo <= a.lo && a.hi <= self.hi
    }
    /// The first byte of the last entry.
    pub fn last(&self) -> u32 {
        self.hi + 1 - self.stride
    }
    /// The byte of each entry its sites read: `lo` plus a multiple of the stride.
    pub fn entries(&self) -> impl Iterator<Item = u32> {
        (self.lo..=self.hi).step_by(self.stride as usize)
    }
}
