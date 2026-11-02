//! Carry chains: multi-byte additions, subtractions, comparisons and shifts recognized in
//! optimized blocks, and fused into one wide operation where their bytes are adjacent in memory.
//!
//! A *link* is one byte of the operation: `r = x + y + c` (`ADC`, `INC`), `r = x - y - b`
//! (`SBC`, `CMP`, `DEC`), `r = x << 1 | c` (`ASL`/`ROL`) or `r = x >> 1 | c << 7` (`LSR`/`ROR`)
//! with its carry-out. Links are found from the shapes value numbering leaves (the forms of
//! Ghidra's double-precision rules: the carry-in of a link is the carry-out of the link below),
//! looking through negations of the carry (`BCC`/`BNE` skips) and the decimal flag's tag
//! ([`crate::ir::DECIMAL`]): a link emitted where the decimal flag may be set is never fused.
//!
//! A chain whose operand bytes are adjacent plain loads (or constants) is fused: wide loads and
//! the wide result at the chain's first replaced op, each result byte, each operand byte loaded
//! after that point and the top carry-out redefined from the wide values, and the result's byte
//! stores, when adjacent, replaced by one wide store. Every redefined varnode keeps its value,
//! so flags and bytes still read elsewhere stay exact; what only fed the chain dies. Loads move
//! up and stores down only past ops that cannot touch their bytes, so the rewrite is exact
//! whatever else reads the carries; the chain is kept when it leaves fewer ops after dead-code
//! elimination.
//!
//! A one-instruction block that increments or decrements a byte under a carry branch
//! (`BCC`/`BNE`/`BCS` over `INC hi`/`DEC hi`) is if-converted into its predecessor as
//! `hi ± carry` when it writes no register (the flags it sets are dead at the join) and its
//! store is plain; the conversion is kept only when it completes a fused chain.

use crate::analysis::{iv_op, Iv};
use crate::ir::{k, tmp, Block, Function, LOp, DECIMAL};
use di_pcode::{Opc, Space, Vn};
use rustc_hash::{FxHashMap, FxHashSet};

/// A value in a block: a constant, a live-in varnode, or the output of op `i`.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug, PartialOrd, Ord)]
enum R {
    K(u32),
    In(u32),
    Op(u32),
}

const K0: R = R::K(0);

/// Kind of a chain.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Kind {
    Add,
    Sub,
    Shl,
    Shr,
}

/// One byte of a chain: `x op y op cin`; for a shift `y` is 0.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
struct Spec {
    kind: Kind,
    x: R,
    y: R,
    cin: R,
}

impl Spec {
    /// Additions commute: a constant second, else the smaller operand first.
    fn norm(self) -> Spec {
        let swap = self.kind == Kind::Add
            && match (self.x, self.y) {
                (R::K(_), R::K(_)) => false,
                (R::K(_), _) => true,
                (_, R::K(_)) => false,
                (x, y) => y < x,
            };
        if swap {
            Spec {
                x: self.y,
                y: self.x,
                ..self
            }
        } else {
            self
        }
    }
}

/// Where a byte operand lives: a constant address, or `X`/`Y` plus a constant.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Place {
    Abs(u16),
    Indexed(u8, u16),
    Other,
}

/// A recognized chain, least significant byte first.
#[derive(Clone, Debug)]
pub struct Chain {
    pub kind: Kind,
    pub fused: bool,
    /// Address of the instruction the chain's block starts at.
    pub pc: u16,
    pub x: Vec<Place>,
    pub y: Vec<Place>,
    /// Places the result bytes are stored to.
    pub r: Vec<Place>,
}

struct Link {
    spec: Spec,
    r: Option<u32>,
    cout: Option<u32>,
}

/// Block ops with each input resolved to its definition.
struct View<'a> {
    ops: &'a [LOp],
    ins: Vec<[R; 2]>,
    uses: Vec<Vec<u32>>,
}

fn key(v: Vn) -> u32 {
    (v.space() as u32) << 16 | v.off()
}

impl<'a> View<'a> {
    fn new(ops: &'a [LOp]) -> View<'a> {
        let mut def: FxHashMap<u32, u32> = FxHashMap::default();
        let mut ins: Vec<[R; 2]> = Vec::with_capacity(ops.len());
        let mut uses = vec![Vec::new(); ops.len()];
        // Plain one-byte loads by address since the last store that may reach them; a reload
        // is the first load's value.
        let mut avail: Vec<(R, u32)> = Vec::new();
        let mut canon: Vec<u32> = Vec::with_capacity(ops.len());
        for (i, op) in ops.iter().enumerate() {
            let mut r = [K0; 2];
            for (j, &v) in op.inputs().iter().enumerate() {
                r[j] = match v.space() {
                    Space::Const => R::K(v.off()),
                    _ => match def.get(&key(v)) {
                        Some(&d) if ops[d as usize].out.size() == v.size() => {
                            let d = canon[d as usize];
                            uses[d as usize].push(i as u32);
                            R::Op(d)
                        }
                        Some(_) => R::In(u32::MAX),
                        None => R::In(key(v) | v.size() << 24),
                    },
                };
            }
            ins.push(r);
            let mut c = i as u32;
            match op.opc {
                Opc::Load if op.aux == 0 && op.out.size() == 1 => {
                    match avail.iter().find(|e| e.0 == r[0]) {
                        Some(&(_, j)) => c = j,
                        None => avail.push((r[0], i as u32)),
                    }
                }
                Opc::Store => {
                    let at = Self::split(&ins, ops, r[0]);
                    let w = op.ins[1].size();
                    avail.retain(|e| {
                        let (b, o) = Self::split(&ins, ops, e.0);
                        b == at.0 && o.wrapping_sub(at.1) & 0xffff >= w
                    });
                }
                Opc::Call => avail.clear(),
                _ => {}
            }
            canon.push(c);
            if !op.out.is_none() {
                def.insert(key(op.out), i as u32);
            }
        }
        View { ops, ins, uses }
    }
    /// `a` as a base and offset, from the ops resolved so far.
    fn split(ins: &[[R; 2]], ops: &[LOp], a: R) -> (Option<R>, u32) {
        match a {
            R::K(c) => (None, c),
            R::Op(i) if ops[i as usize].opc == Opc::IntAdd && ops[i as usize].out.size() == 2 => {
                match ins[i as usize] {
                    [b, R::K(c)] | [R::K(c), b] => (Some(b), c),
                    _ => (Some(a), 0),
                }
            }
            _ => (Some(a), 0),
        }
    }
    fn op(&self, r: R) -> Option<(&LOp, [R; 2])> {
        match r {
            R::Op(i) => Some((&self.ops[i as usize], self.ins[i as usize])),
            _ => None,
        }
    }
    fn size(&self, r: R) -> u32 {
        match r {
            R::Op(i) => self.ops[i as usize].out.size(),
            R::In(k) => k >> 24,
            R::K(_) => 0,
        }
    }
    /// Operands of `r = opc(a, b)` in both orders when `opc` commutes.
    fn bin(&self, r: R, opc: Opc) -> impl Iterator<Item = (R, R)> {
        let (n, a, b) = match self.op(r) {
            Some((op, [a, b])) if op.opc == opc => {
                let comm = matches!(opc, Opc::IntAdd | Opc::IntOr | Opc::IntAnd | Opc::IntXor);
                (1 + (comm && a != b) as usize, a, b)
            }
            _ => (0, K0, K0),
        };
        [(a, b), (b, a)].into_iter().take(n)
    }
    fn is(&self, r: R, opc: Opc, size: u32) -> Option<[R; 2]> {
        self.op(r)
            .filter(|(op, _)| op.opc == opc && op.out.size() == size)
            .map(|x| x.1)
    }
    fn bool(&self, r: R) -> bool {
        match r {
            R::K(c) => c <= 1,
            R::In(k) => (8..15).contains(&(k & 0xffff)) && k >> 16 & 0xff == Space::Reg as u32,
            R::Op(i) => {
                let op = &self.ops[i as usize];
                op.out.size() == 1
                    && match op.opc {
                        Opc::IntEqual
                        | Opc::IntNotEqual
                        | Opc::IntLess
                        | Opc::IntLessEqual
                        | Opc::IntCarry => true,
                        Opc::IntOr | Opc::IntAnd | Opc::IntXor => {
                            let [a, b] = self.ins[i as usize];
                            self.bool(a) && self.bool(b)
                                || op.opc == Opc::IntAnd && (a == R::K(1) || b == R::K(1))
                        }
                        Opc::Copy => self.bool(self.ins[i as usize][0]),
                        Opc::IntSub => {
                            let [a, b] = self.ins[i as usize];
                            a == R::K(1) && self.bool(b)
                        }
                        _ => false,
                    }
            }
        }
    }
    /// `r` as `(core, negated)` with boolean negations stripped.
    fn strip(&self, mut r: R) -> (R, bool) {
        let mut neg = false;
        loop {
            let next = match self.op(r) {
                Some((op, [a, R::K(0)])) if op.opc == Opc::IntEqual && self.bool(a) => (a, true),
                Some((op, [a, R::K(0)])) if op.opc == Opc::IntNotEqual && self.bool(a) => {
                    (a, false)
                }
                Some((op, [a, R::K(1)])) if op.opc == Opc::IntXor && self.bool(a) => (a, true),
                Some((op, [R::K(1), a])) if op.opc == Opc::IntSub && self.bool(a) => (a, true),
                Some((op, [a, _])) if op.opc == Opc::Copy && self.bool(a) => (a, false),
                _ => return (r, neg),
            };
            r = next.0;
            neg ^= next.1;
        }
    }
    /// The links whose carry-out (no-borrow for subtraction) `c` is; with `neg`, whose
    /// carry-out `c` negates.
    fn cout(&self, c: R) -> Vec<Spec> {
        let (r, neg) = self.strip(c);
        let mut out = Vec::new();
        let add = |x, y, cin| {
            Spec {
                kind: Kind::Add,
                x,
                y,
                cin,
            }
            .norm()
        };
        let sub = |x, y, cin| Spec {
            kind: Kind::Sub,
            x,
            y,
            cin,
        };
        let Some((op, [a, b])) = self.op(r) else {
            return out;
        };
        match (op.opc, neg) {
            (Opc::IntCarry, false) if op.ins[0].size() == 1 => out.push(add(a, b, K0)),
            (Opc::IntOr, false) => {
                for (p, q) in self.bin(r, Opc::IntOr) {
                    if let (Some([x, y]), Some([s, c])) =
                        (self.is(p, Opc::IntCarry, 1), self.is(q, Opc::IntCarry, 1))
                    {
                        if self.bin(s, Opc::IntAdd).any(|e| e == (x, y)) && self.bool(c) {
                            out.push(add(x, y, c));
                        }
                    }
                }
            }
            (Opc::IntEqual, false) if b == R::K(0xff) => out.push(add(a, R::K(1), K0)),
            (Opc::IntEqual, false) if b == K0 => {
                for (x, one) in self.bin(a, Opc::IntAdd) {
                    if one == R::K(1) && self.size(a) == 1 {
                        out.push(add(x, R::K(1), K0));
                    }
                }
            }
            (Opc::IntLessEqual, false) if op.ins[0].size() == 1 => out.push(sub(b, a, K0)),
            (Opc::IntLessEqual, false) if op.ins[0].size() == 2 => {
                let z = |v: R| match v {
                    R::K(c) if c <= 0xff => Some(R::K(c)),
                    _ => self.is(v, Opc::IntZext, 2).map(|i| i[0]),
                };
                if let Some(x) = z(b) {
                    if let Some(y) = z(a) {
                        out.push(sub(x, y, K0));
                    }
                    for (p, q) in self.bin(a, Opc::IntAdd) {
                        if let (Some(y), Some(bin)) = (z(p), z(q)) {
                            if self.bool(bin) && !matches!(bin, R::K(_)) {
                                out.push(sub(x, y, bin));
                            }
                        }
                    }
                }
            }
            (Opc::IntNotEqual, false) if b == K0 => {
                if let Some((x, _)) = self.bin(a, Opc::IntAnd).find(|e| e.1 == R::K(0x80)) {
                    out.push(Spec {
                        kind: Kind::Shl,
                        x,
                        y: K0,
                        cin: K0,
                    });
                }
            }
            (Opc::IntAnd, false) if b == R::K(1) && self.size(a) == 1 => out.push(Spec {
                kind: Kind::Shr,
                x: a,
                y: K0,
                cin: K0,
            }),
            _ => {}
        }
        out
    }
    /// The subtraction links whose borrow-out `c` is.
    fn borrow(&self, c: R) -> Vec<Spec> {
        let (r, neg) = self.strip(c);
        let mut out: Vec<Spec> = if neg {
            self.cout(r)
                .into_iter()
                .filter(|s| s.kind == Kind::Sub)
                .collect()
        } else {
            Vec::new()
        };
        if let (false, Some((op, [t, R::K(0)]))) = (neg, self.op(r)) {
            if op.opc == Opc::IntEqual && self.size(t) == 1 && !self.bool(t) {
                out.push(Spec {
                    kind: Kind::Sub,
                    x: t,
                    y: R::K(1),
                    cin: K0,
                });
            }
        }
        out
    }
    /// The specs op `i` computes as a link result.
    fn results(&self, i: u32) -> Vec<Spec> {
        let r = R::Op(i);
        let op = &self.ops[i as usize];
        let mut out = Vec::new();
        if op.out.size() != 1 {
            return out;
        }
        let spec = |kind, x, y, cin| Spec { kind, x, y, cin }.norm();
        match op.opc {
            Opc::IntAdd => {
                for (p, q) in self.bin(r, Opc::IntAdd) {
                    out.push(spec(Kind::Add, p, q, K0));
                    if self.bool(q) {
                        out.push(spec(Kind::Add, p, K0, q));
                        for (x, y) in self.bin(p, Opc::IntAdd) {
                            out.push(spec(Kind::Add, x, y, q));
                        }
                    }
                    if let R::K(c) = q {
                        out.push(spec(Kind::Sub, p, R::K(c.wrapping_neg() & 0xff), K0));
                    }
                }
            }
            Opc::IntSub => {
                let [x, m] = self.ins[i as usize];
                out.push(spec(Kind::Sub, x, m, K0));
                if self.bool(m) {
                    out.push(spec(Kind::Sub, x, K0, m));
                }
                for (y, b) in self.bin(m, Opc::IntAdd) {
                    if self.bool(b) {
                        out.push(spec(Kind::Sub, x, y, b));
                    }
                }
            }
            Opc::IntLeft if self.ins[i as usize][1] == R::K(1) => {
                out.push(spec(Kind::Shl, self.ins[i as usize][0], K0, K0))
            }
            Opc::IntRight if self.ins[i as usize][1] == R::K(1) => {
                out.push(spec(Kind::Shr, self.ins[i as usize][0], K0, K0))
            }
            Opc::IntOr => {
                for (p, c) in self.bin(r, Opc::IntOr) {
                    if let (Some([x, R::K(1)]), true) = (self.is(p, Opc::IntLeft, 1), self.bool(c))
                    {
                        out.push(spec(Kind::Shl, x, K0, c));
                    }
                    if let (Some([x, R::K(1)]), Some([c, R::K(7)])) =
                        (self.is(p, Opc::IntRight, 1), self.is(c, Opc::IntLeft, 1))
                    {
                        if self.bool(c) {
                            out.push(spec(Kind::Shr, x, K0, c));
                        }
                    }
                }
            }
            _ => {}
        }
        out
    }
    /// The interval of `r` from the ops defining it (inputs from outside: their full range).
    fn iv(&self, r: R, depth: u32) -> Iv {
        match r {
            R::K(c) => Iv::exact(c),
            R::In(k) => Iv::full((k >> 24).max(1)),
            R::Op(i) => {
                let op = &self.ops[i as usize];
                let full = Iv::full(op.out.size());
                if depth == 0 || op.opc == Opc::Load {
                    return full;
                }
                let [a, b] = self.ins[i as usize];
                match op.opc {
                    Opc::IntZext | Opc::Copy if self.size(a) <= op.out.size() => {
                        self.iv(a, depth - 1)
                    }
                    Opc::IntAdd
                    | Opc::IntSub
                    | Opc::IntAnd
                    | Opc::IntOr
                    | Opc::IntLeft
                    | Opc::IntRight => iv_op(
                        op.opc,
                        self.iv(a, depth - 1),
                        self.iv(b, depth - 1),
                        op.out.size(),
                        op.ins[0].size(),
                    ),
                    _ => full,
                }
            }
        }
    }
    /// An address as a base (none: a constant) and an offset.
    fn addr(&self, a: R) -> (Option<R>, u32) {
        Self::split(&self.ins, self.ops, a)
    }
    /// The `w1` bytes at `a1` and the `w2` bytes at `a2` may overlap.
    fn alias(&self, a1: R, w1: u32, a2: R, w2: u32) -> bool {
        let ((b1, o1), (b2, o2)) = (self.addr(a1), self.addr(a2));
        if b1 == b2 {
            let d = o2.wrapping_sub(o1) & 0xffff;
            return d < w1 || 0x10000 - d < w2;
        }
        let (p, q) = (self.iv(a1, 4), self.iv(a2, 4));
        p.lo < q.hi + w2 && q.lo < p.hi + w1
    }
    /// The varnode holding `r` just before op `at`, if one still does.
    fn holds(&self, r: R, at: u32, size: u8) -> Option<Vn> {
        let (v, from) = match r {
            R::K(c) => return Some(k(c, size)),
            R::In(u32::MAX) => return None,
            R::In(kk) => (
                self.ops
                    .iter()
                    .flat_map(|o| o.inputs().iter().copied())
                    .find(|&w| w.space() != Space::Const && key(w) | w.size() << 24 == kk)?,
                0,
            ),
            R::Op(i) => (self.ops[i as usize].out, i + 1),
        };
        let kept = (from..at)
            .all(|j| self.ops[j as usize].out.is_none() || key(self.ops[j as usize].out) != key(v));
        kept.then_some(v)
    }
    /// The plain one-byte load `r` is: its address.
    fn load(&self, r: R) -> Option<R> {
        self.op(r)
            .filter(|(op, _)| op.opc == Opc::Load && op.aux == 0 && op.out.size() == 1)
            .map(|x| x.1[0])
    }
    /// Where the address `a` points, for reports.
    fn place(&self, a: R) -> Place {
        match self.addr(a) {
            (None, c) => Place::Abs(c as u16),
            (Some(b), c) => match self.is(b, Opc::IntZext, 2).map(|x| x[0]) {
                Some(R::In(k)) if k >> 16 & 0xff == Space::Reg as u32 && (k & 0xffff) < 3 => {
                    Place::Indexed((k & 0xffff) as u8, c as u16)
                }
                _ => Place::Other,
            },
        }
    }
}

/// Every maximal chain of at least two links in the block.
fn chains(v: &View) -> Vec<(Spec, Vec<Link>)> {
    let mut res: FxHashMap<Spec, u32> = FxHashMap::default();
    let mut shift: FxHashMap<(Kind, R), (u32, R)> = FxHashMap::default();
    let mut couts: FxHashMap<Spec, u32> = FxHashMap::default();
    for i in 0..v.ops.len() as u32 {
        for s in v.results(i) {
            res.entry(s).or_insert(i);
            if matches!(s.kind, Kind::Shl | Kind::Shr) {
                shift.insert((s.kind, s.x), (i, s.cin));
            }
        }
        if v.ops[i as usize].out.size() == 1 {
            let (core, neg) = v.strip(R::Op(i));
            if !neg && core == R::Op(i) {
                for s in v.cout(R::Op(i)) {
                    couts.entry(s).or_insert(i);
                }
            }
        }
    }
    // The link a carry-in comes from.
    let below = |s: &Spec| -> Option<Spec> {
        if matches!(s.cin, R::K(_)) {
            return None;
        }
        let cands = if s.kind == Kind::Sub {
            v.borrow(s.cin)
        } else {
            v.cout(s.cin)
        };
        cands
            .into_iter()
            .find(|c| c.kind == s.kind)
            .map(|c| match c.kind {
                Kind::Shl | Kind::Shr => shift
                    .get(&(c.kind, c.x))
                    .map_or(c, |&(_, cin)| Spec { cin, ..c }),
                _ => c,
            })
    };
    let mut tops: Vec<Spec> = res.keys().chain(couts.keys()).copied().collect();
    tops.sort_by_key(|s| (res.get(s).or(couts.get(s)).copied(), s.x, s.y, s.cin));
    tops.dedup();
    let lower: FxHashSet<Spec> = tops.iter().filter_map(&below).collect();
    let mut out = Vec::new();
    for top in tops {
        if lower.contains(&top) {
            continue;
        }
        let mut links = Vec::new();
        let mut s = Some(top);
        while let Some(sp) = s {
            let r = match sp.kind {
                Kind::Shl | Kind::Shr => shift
                    .get(&(sp.kind, sp.x))
                    .filter(|e| e.1 == sp.cin)
                    .map(|e| e.0),
                _ => res.get(&sp).copied(),
            };
            links.push(Link {
                spec: sp,
                r,
                cout: couts
                    .get(&Spec { cin: K0, ..sp }.norm())
                    .filter(|_| matches!(sp.kind, Kind::Shl | Kind::Shr))
                    .or(couts.get(&sp))
                    .copied(),
            });
            if links.len() > 4 {
                break;
            }
            s = below(&sp);
        }
        // Walked from the carry-out down; a right shift carries from the top byte down.
        if (2..=4).contains(&links.len()) {
            if top.kind != Kind::Shr {
                links.reverse();
            }
            let bottom = if top.kind == Kind::Shr {
                links.last()
            } else {
                links.first()
            };
            out.push((bottom.unwrap().spec, links));
        }
    }
    out
}

/// The adjacent plain loads (base address) or the wide constant `ops` are.
fn operand(v: &View, ops: &[R]) -> Option<Result<R, u32>> {
    if let Some(c) = ops.iter().rev().try_fold(0u32, |w, r| match r {
        R::K(c) => Some(w << 8 | c),
        _ => None,
    }) {
        return (c <= 0xffff).then_some(Err(c));
    }
    let a0 = v.load(ops[0])?;
    let (b, o) = v.addr(a0);
    let n = ops.len() as u32;
    let top = b.map_or(0, |b| v.iv(b, 4).hi) + o + n - 1;
    let adjacent = ops.iter().enumerate().all(|(i, &r)| {
        v.load(r)
            .is_some_and(|a| v.addr(a) == (b, (o + i as u32) & 0xffff))
    });
    (adjacent && top <= 0xffff).then_some(Ok(a0))
}

fn pos(r: R) -> Option<u32> {
    match r {
        R::Op(i) => Some(i),
        _ => None,
    }
}

/// Ops inserted before a chain, on fresh temporaries.
struct Pre<'a> {
    ops: Vec<LOp>,
    tmps: &'a mut Temps,
}

impl Pre<'_> {
    fn op(&mut self, opc: Opc, size: u32, a: Vn, b: Vn) -> Vn {
        let o = self.tmps.get(size);
        self.ops.push(LOp::new(opc, o, a, b, 0));
        o
    }
}

/// Fresh temporaries above every one in use.
struct Temps(u16);

impl Temps {
    fn of(f: &Function) -> Temps {
        let n = f
            .blocks
            .iter()
            .flat_map(|b| &b.ops)
            .flat_map(|o| o.ins.iter().chain([&o.out]))
            .filter(|v| v.space() == Space::Unique)
            .map(|v| v.off() + 1)
            .max()
            .unwrap_or(0);
        Temps(n.max(f.ntemps) as u16)
    }
    fn get(&mut self, size: u32) -> Vn {
        self.0 += 1;
        tmp(self.0 - 1, size as u8)
    }
}

/// Rewrite one chain of block `b` wide; `None` when it cannot be.
fn rewrite(blk: &Block, base: Spec, links: &[Link], tmps: &mut Temps) -> Option<(Vec<LOp>, Chain)> {
    let v = View::new(&blk.ops);
    let n = links.len() as u32;
    let kind = base.kind;
    let ops = &blk.ops;
    // The links' ops and the arithmetic feeding them, down to their operands.
    let tagged = |i: Option<u32>| {
        let mut work: Vec<u32> = i.into_iter().collect();
        while let Some(j) = work.pop() {
            let o = &ops[j as usize];
            if o.aux & DECIMAL != 0 && o.opc != Opc::Load {
                return true;
            }
            if o.opc != Opc::Load {
                work.extend(v.ins[j as usize].iter().filter_map(|&r| pos(r)));
            }
        }
        false
    };
    if links.iter().any(|l| tagged(l.r) || tagged(l.cout)) {
        return None;
    }
    let xs: Vec<R> = links.iter().map(|l| l.spec.x).collect();
    let ys: Vec<R> = links.iter().map(|l| l.spec.y).collect();
    let x = operand(&v, &xs)?;
    let y = operand(&v, &ys)?;
    if x.is_err() {
        return None;
    }
    let top = if kind == Kind::Shr {
        &links[0]
    } else {
        &links[n as usize - 1]
    };
    let top_cout = top.cout.filter(|_| matches!(kind, Kind::Add | Kind::Sub));
    let mut redefs: Vec<u32> = links.iter().filter_map(|l| l.r).collect();
    redefs.extend(top_cout);
    let p = *redefs.iter().min()?;
    let cin = base.cin;
    if pos(cin).is_some_and(|c| c >= p) {
        return None;
    }
    // Every operand byte read at `p` holds what the chain read.
    let mut moved: Vec<(u32, R, u32)> = Vec::new();
    for (opnd, list) in [(x, &xs), (y, &ys)] {
        let Ok(a0) = opnd else { continue };
        if pos(a0).is_some_and(|a| a >= p) {
            return None;
        }
        for (i, &r) in list.iter().enumerate() {
            let q = pos(r)?;
            let (lo, hi) = (q.min(p), q.max(p));
            let addr = v.load(r)?;
            let clobbered = (lo + 1..hi).any(|j| {
                let o = &ops[j as usize];
                o.opc == Opc::Call
                    || o.opc == Opc::Store
                        && v.alias(v.ins[j as usize][0], o.ins[1].size(), addr, 1)
            });
            if clobbered {
                return None;
            }
            if q > p {
                moved.push((q, r, i as u32));
            }
        }
    }
    // The result's byte stores, adjacent and sinkable to the last of them.
    let stores: Option<Vec<u32>> = links
        .iter()
        .map(|l| {
            let r = R::Op(l.r?);
            let st: Vec<u32> = v.uses[l.r? as usize]
                .iter()
                .copied()
                .filter(|&u| ops[u as usize].opc == Opc::Store && v.ins[u as usize][1] == r)
                .collect();
            match st[..] {
                [s] if ops[s as usize].aux == 0 && ops[s as usize].ins[1].size() == 1 => Some(s),
                _ => None,
            }
        })
        .collect();
    let stores = stores.filter(|st| {
        let a0 = v.ins[st[0] as usize][0];
        let (b, o) = v.addr(a0);
        let last = *st.iter().max().unwrap();
        st.iter().enumerate().all(|(i, &s)| {
            let a = v.ins[s as usize][0];
            v.addr(a) == (b, (o + i as u32) & 0xffff)
                && (s + 1..last).all(|j| {
                    let o = &ops[j as usize];
                    let acc = match o.opc {
                        Opc::Load => o.out.size().max(1),
                        Opc::Store => o.ins[1].size(),
                        _ => 0,
                    };
                    !(o.opc == Opc::CBranch || o.opc == Opc::Call || o.opc == Opc::CallOther)
                        && !(acc > 0 && v.alias(v.ins[j as usize][0], acc, a, 1))
                })
        }) && b.map_or(0, |b| v.iv(b, 4).hi) + o + n - 1 <= 0xffff
    });
    // New ops at `p`.
    let mut pre = Pre {
        ops: Vec::new(),
        tmps,
    };
    let wide = |pre: &mut Pre, o: Result<R, u32>| -> Option<Vn> {
        match o {
            Ok(a0) => {
                let a = v.holds(a0, p, 2)?;
                Some(pre.op(Opc::Load, n, a, Vn::NONE))
            }
            Err(c) => Some(k(c, n as u8)),
        }
    };
    let wx = wide(&mut pre, x)?;
    let wy = wide(&mut pre, y)?;
    let c1 = if cin == K0 {
        None
    } else {
        Some(v.holds(cin, p, 1)?)
    };
    let zc = |pre: &mut Pre, size: u32| c1.map(|c| pre.op(Opc::IntZext, size, c, Vn::NONE));
    let wr;
    let mut wc = Vn::NONE;
    match kind {
        Kind::Add => {
            let s = pre.op(Opc::IntAdd, n, wx, wy);
            let z = zc(&mut pre, n);
            wr = z.map_or(s, |z| pre.op(Opc::IntAdd, n, s, z));
            if top_cout.is_some() {
                let c = pre.op(Opc::IntCarry, 1, wx, wy);
                wc = match z {
                    Some(z) => {
                        let d = pre.op(Opc::IntCarry, 1, s, z);
                        pre.op(Opc::IntOr, 1, c, d)
                    }
                    None => c,
                };
            }
        }
        Kind::Sub => {
            let z = zc(&mut pre, n);
            let m = z.map_or(wy, |z| pre.op(Opc::IntAdd, n, wy, z));
            wr = pre.op(Opc::IntSub, n, wx, m);
            if top_cout.is_some() {
                wc = match zc(&mut pre, n + 1) {
                    Some(_) if n == 4 => return None,
                    Some(z) => {
                        let ey = pre.op(Opc::IntZext, n + 1, wy, Vn::NONE);
                        let ex = pre.op(Opc::IntZext, n + 1, wx, Vn::NONE);
                        let m = pre.op(Opc::IntAdd, n + 1, ey, z);
                        pre.op(Opc::IntLessEqual, 1, m, ex)
                    }
                    None => pre.op(Opc::IntLessEqual, 1, wy, wx),
                };
            }
        }
        Kind::Shl => {
            let s = pre.op(Opc::IntLeft, n, wx, k(1, 1));
            let z = zc(&mut pre, n);
            wr = z.map_or(s, |z| pre.op(Opc::IntOr, n, s, z));
        }
        Kind::Shr => {
            let s = pre.op(Opc::IntRight, n, wx, k(1, 1));
            wr = match zc(&mut pre, n) {
                Some(z) => {
                    let h = pre.op(Opc::IntLeft, n, z, k(8 * n - 1, 1));
                    pre.op(Opc::IntOr, n, s, h)
                }
                None => s,
            };
        }
    }
    let mut pre = pre.ops;
    let byte = |out: Vn, w: Vn, i: u32| {
        if i == 0 {
            LOp::new(Opc::Copy, out, w, Vn::NONE, 0)
        } else {
            LOp::new(Opc::IntRight, out, w, k(8 * i, 1), 0)
        }
    };
    let mut new: Vec<LOp> = ops.clone();
    for (i, l) in links.iter().enumerate() {
        if let Some(r) = l.r {
            new[r as usize] = byte(ops[r as usize].out, wr, i as u32);
        }
    }
    if let Some(c) = top_cout {
        new[c as usize] = LOp::new(Opc::Copy, ops[c as usize].out, wc, Vn::NONE, 0);
    }
    for &(q, r, i) in &moved {
        let w = if xs.contains(&r) && xs[i as usize] == r {
            wx
        } else {
            wy
        };
        new[q as usize] = byte(ops[q as usize].out, w, i);
    }
    let place = |r: R| v.load(r).map_or(Place::Other, |a| v.place(a));
    let mut chain = Chain {
        kind,
        fused: true,
        pc: blk.pc,
        x: xs.iter().map(|&r| place(r)).collect(),
        y: ys.iter().map(|&r| place(r)).collect(),
        r: Vec::new(),
    };
    let mut drop = vec![false; new.len()];
    let stores = stores.and_then(|st| {
        let last = *st.iter().max().unwrap();
        let a0 = v.holds(v.ins[st[0] as usize][0], last, 2)?;
        Some((st, last, a0))
    });
    if let Some((st, last, a0)) = &stores {
        let (last, a0) = (*last, *a0);
        chain.r = st.iter().map(|&s| v.place(v.ins[s as usize][0])).collect();
        for &s in st.iter() {
            drop[s as usize] = true;
        }
        drop[last as usize] = false;
        new[last as usize] = LOp::new(Opc::Store, Vn::NONE, a0, wr, 0);
    }
    let mut out = Vec::with_capacity(new.len() + pre.len());
    for (i, op) in new.into_iter().enumerate() {
        if i as u32 == p {
            out.append(&mut pre);
        }
        if !drop[i] {
            out.push(op);
        }
    }
    Some((out, chain))
}

/// Recognized chains of a block, not fused.
pub fn recognize(f: &Function) -> Vec<Chain> {
    let mut out = Vec::new();
    for b in &f.blocks {
        let v = View::new(&b.ops);
        let place = |r: R| v.load(r).map_or(Place::Other, |a| v.place(a));
        // One chain per top op: the reading whose operands are placed best.
        let mut best: FxHashMap<Option<u32>, (usize, Chain)> = FxHashMap::default();
        for (base, links) in chains(&v) {
            let top = if base.kind == Kind::Shr {
                &links[0]
            } else {
                links.last().unwrap()
            };
            let c = Chain {
                kind: base.kind,
                fused: false,
                pc: b.pc,
                x: links.iter().map(|l| place(l.spec.x)).collect(),
                y: links.iter().map(|l| place(l.spec.y)).collect(),
                r: Vec::new(),
            };
            let score =
                c.x.iter()
                    .chain(&c.y)
                    .filter(|p| **p != Place::Other)
                    .count();
            let e = best.entry(top.r.or(top.cout)).or_insert((score, c.clone()));
            if score > e.0 {
                *e = (score, c);
            }
        }
        let mut cs: Vec<(Option<u32>, Chain)> = best.into_iter().map(|(k, v)| (k, v.1)).collect();
        cs.sort_by_key(|c| c.0);
        out.extend(cs.into_iter().map(|c| c.1));
    }
    out
}

/// If-convert the carry-propagating successor of block `b` (with the join when only the two
/// reach it): the converted block, where its update stores and the update's op.
fn ifconv(f: &Function, preds: &[u32], b: usize, tmps: &mut Temps) -> Option<(Block, Place, u32)> {
    let blk = &f.blocks[b];
    let n = blk.ops.len();
    let (cb, br) = (blk.ops.get(n.checked_sub(2)?)?, blk.ops[n - 1]);
    if cb.opc != Opc::CBranch || br.opc != Opc::Branch {
        return None;
    }
    let preds = |t: u32| preds[t as usize];
    for (side, join, taken) in [(br.aux, cb.aux, false), (cb.aux, br.aux, true)] {
        let s = &f.blocks[side as usize];
        if side as usize == b
            || preds(side) != 1
            || s.check
            || s.resume.is_some()
            || s.replay.is_some()
        {
            continue;
        }
        let m = s.ops.len();
        if s.ops[m - 1].opc != Opc::Branch || s.ops[m - 1].aux != join {
            continue;
        }
        let body = &s.ops[..m - 1];
        let v = View::new(body);
        let mut stores = (0..body.len()).filter(|&i| body[i].opc == Opc::Store);
        let (Some(st), None) = (stores.next(), stores.next()) else {
            continue;
        };
        let (sop, [sa, sv]) = (body[st], v.ins[st]);
        let Some((upd, [u, R::K(1)])) = v.op(sv) else {
            continue;
        };
        // Whatever else the block computes is dead at the join: it writes no register.
        let ok = sop.aux == 0
            && matches!(upd.opc, Opc::IntAdd | Opc::IntSub)
            && upd.out.size() == 1
            && v.load(u) == Some(sa)
            && body
                .iter()
                .enumerate()
                .all(|(i, o)| i == st || o.out.space() == Space::Unique && !o.effectful())
            && body.iter().filter(|o| o.opc == Opc::Load).count() == 1;
        if !ok {
            continue;
        }
        let mut ops = blk.ops[..n - 2].to_vec();
        let p = tmps.get(1);
        let test = if taken {
            Opc::IntNotEqual
        } else {
            Opc::IntEqual
        };
        ops.push(LOp::new(
            test,
            p,
            cb.ins[0],
            k(0, cb.ins[0].size() as u8),
            0,
        ));
        let mut map: FxHashMap<u32, Vn> = FxHashMap::default();
        let mut upd_at = 0;
        // The byte below must be stored before the join or in it.
        let at = v.place(sa);
        let below = match at {
            Place::Abs(a) => Place::Abs(a.wrapping_sub(1)),
            Place::Indexed(r, a) => Place::Indexed(r, a.wrapping_sub(1)),
            Place::Other => continue,
        };
        let stores_below = |ops: &[LOp]| {
            let w = View::new(ops);
            (0..ops.len()).any(|i| ops[i].opc == Opc::Store && w.place(w.ins[i][0]) == below)
        };
        if !stores_below(&blk.ops) && !stores_below(&f.blocks[join as usize].ops) {
            continue;
        }
        for (i, o) in body.iter().enumerate() {
            let mut o = *o;
            for x in o.ins.iter_mut().filter(|x| x.space() == Space::Unique) {
                *x = map.get(&x.off()).copied().unwrap_or(*x);
            }
            if i as u32 == pos(sv).unwrap() {
                o.ins[1] = p;
                upd_at = ops.len() as u32;
            }
            if o.out.space() == Space::Unique {
                let t = tmps.get(o.out.size());
                map.insert(o.out.off(), t);
                o.out = t;
            }
            ops.push(o);
        }
        // A join reached only from here and the side block continues the block.
        let j = &f.blocks[join as usize];
        let merge = join as usize != b
            && preds(join) == 2
            && !j.check
            && j.resume.is_none()
            && j.replay.is_none();
        // The join keeps its temporaries: the blocks it reaches read them, and it reads none of
        // the block's.
        if merge {
            ops.extend_from_slice(&j.ops);
        } else {
            ops.push(LOp::new(Opc::Branch, Vn::NONE, Vn::NONE, Vn::NONE, join));
        }
        return Some((Block { ops, ..blk.clone() }, at, upd_at));
    }
    None
}

/// Edges into each block of `f`.
fn edges_in(f: &Function) -> Vec<u32> {
    let mut n = vec![0u32; f.blocks.len()];
    for s in f.blocks.iter().flat_map(|b| b.succs(&f.tables)) {
        n[s as usize] += 1;
    }
    n
}

fn count(f: &Function) -> usize {
    f.blocks.iter().map(|b| b.ops.len()).sum()
}

/// The 2-byte load op `i` of `v` can be: `ZEXT(lo) | ZEXT(hi) << 8` of the plain loads of
/// two adjacent bytes that nothing between them and `i` may store into; its address there.
fn word_load(v: &View, i: u32) -> Option<(Vn, [u32; 5])> {
    let op = &v.ops[i as usize];
    if op.out.size() != 2 {
        return None;
    }
    v.bin(R::Op(i), Opc::IntOr).into_iter().find_map(|(x, y)| {
        let [zh, R::K(8)] = v.is(x, Opc::IntLeft, 2)? else {
            return None;
        };
        let [h] = v.is(zh, Opc::IntZext, 2).map(|r| [r[0]])?;
        let [l] = v.is(y, Opc::IntZext, 2).map(|r| [r[0]])?;
        let (al, ah) = (v.load(l)?, v.load(h)?);
        let (b, o) = v.addr(al);
        let adjacent =
            v.addr(ah) == (b, (o + 1) & 0xffff) && b.map_or(0, |b| v.iv(b, 4).hi) + o < 0xffff;
        let kept = [(l, al), (h, ah)].iter().all(|&(r, a)| {
            (pos(r).unwrap() + 1..i).all(|j| {
                let o = &v.ops[j as usize];
                o.opc != Opc::Call
                    && !(o.opc == Opc::Store
                        && v.alias(v.ins[j as usize][0], o.ins[1].size(), a, 1))
            })
        });
        let at = v.holds(al, i, 2).filter(|_| adjacent && kept)?;
        Some((at, [pos(x)?, pos(zh)?, pos(y)?, pos(l)?, pos(h)?]))
    })
}

/// Pointer pairs and other adjacent byte pairs combined into a word become one 2-byte load,
/// where that leaves an op it was built from dead; the number replaced.
pub fn words(f: &mut Function) -> usize {
    let mut n = 0;
    for blk in &mut f.blocks {
        let v = View::new(&blk.ops);
        let mut new: Vec<(usize, LOp)> = Vec::new();
        for i in 0..blk.ops.len() as u32 {
            let Some((at, parts)) = word_load(&v, i) else {
                continue;
            };
            let mut dead: Vec<u32> = vec![i];
            while let Some(&d) = parts.iter().find(|&&p| {
                !dead.contains(&p)
                    && v.ops[p as usize].out.space() == Space::Unique
                    && v.uses[p as usize].iter().all(|u| dead.contains(u))
            }) {
                dead.push(d);
            }
            if dead.len() > 1 {
                new.push((
                    i as usize,
                    LOp::new(Opc::Load, v.ops[i as usize].out, at, Vn::NONE, 0),
                ));
            }
        }
        n += new.len();
        for (i, op) in new {
            blk.ops[i] = op;
        }
    }
    n
}

/// The ops dead-code elimination would keep of each block `b` of a function holding `ops`.
pub type Costs<'c> = dyn Fn(&Function, &[(usize, Vec<LOp>)]) -> Vec<usize> + 'c;

/// Fuse the chains of `f` (only in the blocks starting at `only`, if given) whose wide form
/// leaves fewer ops in their block.
fn straight(f: &mut Function, costs: &Costs, only: Option<u16>, tmps: &mut Temps) -> Vec<Chain> {
    let mut done = Vec::new();
    loop {
        // Each block's own ops, then its rewrites with the index of its own.
        let mut cands: Vec<(usize, Vec<LOp>)> = Vec::new();
        let mut info: Vec<Option<(usize, Chain)>> = Vec::new();
        for (b, blk) in f.blocks.iter().enumerate() {
            if only.is_some_and(|pc| blk.pc != pc) {
                continue;
            }
            let own = cands.len();
            for (spec, links) in chains(&View::new(&blk.ops)) {
                if let Some((ops, chain)) = rewrite(blk, spec, &links, tmps) {
                    if cands.len() == own {
                        cands.push((b, blk.ops.clone()));
                        info.push(None);
                    }
                    cands.push((b, ops));
                    info.push(Some((own, chain)));
                }
            }
        }
        if cands.is_empty() {
            return done;
        }
        f.ntemps = f.ntemps.max(tmps.0 as u32);
        let cost = costs(f, &cands);
        let best =
            (0..cands.len()).find(|&i| info[i].as_ref().is_some_and(|x| cost[i] < cost[x.0]));
        let Some(i) = best else {
            return done;
        };
        let (b, ops) = cands.swap_remove(i);
        f.blocks[b].ops = ops;
        done.extend(info.swap_remove(i).map(|x| x.1));
    }
}

/// Fuse the chains of `f` whose wide form leaves fewer ops, if-converting a carry propagation
/// (and re-optimizing with `round`) when that completes a fused chain storing the propagated
/// byte; the fused chains.
pub fn fuse(f: &mut Function, costs: &Costs, round: &mut dyn FnMut(&mut Function)) -> Vec<Chain> {
    let mut tmps = Temps::of(f);
    let mut done = straight(f, costs, None, &mut tmps);
    let mut tried: FxHashSet<u16> = FxHashSet::default();
    let mut preds = edges_in(f);
    let mut b = 0;
    while b < f.blocks.len() {
        let pc = f.blocks[b].pc;
        let conv = ifconv(f, &preds, b, &mut tmps).filter(|_| tried.insert(pc));
        // The converted block itself must hold a chain through the update, rewritable with a
        // wide store, before the function is re-optimized to cost it.
        let conv = conv.filter(|(blk, _, upd)| {
            chains(&View::new(&blk.ops))
                .into_iter()
                .any(|(spec, links)| {
                    links.iter().any(|l| l.r == Some(*upd))
                        && rewrite(blk, spec, &links, &mut tmps).is_some_and(|c| !c.1.r.is_empty())
                })
        });
        if let Some((blk, at, _)) = conv {
            let mut g = f.clone();
            g.blocks[b] = blk;
            g.ntemps = g.ntemps.max(tmps.0 as u32);
            round(&mut g);
            let mut t = Temps::of(&g);
            let fused = straight(&mut g, costs, Some(pc), &mut t);
            round(&mut g);
            if fused.iter().any(|c| c.r.contains(&at)) && count(&g) < count(f) {
                *f = g;
                preds = edges_in(f);
                tmps = t;
                done.extend(fused);
                b = 0;
                continue;
            }
        }
        b += 1;
    }
    done
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ir::{r1, reg};

    fn ld(out: u16, a: u32) -> LOp {
        LOp::new(Opc::Load, tmp(out, 1), k(a, 2), Vn::NONE, 0)
    }
    fn op(opc: Opc, out: Vn, a: Vn, b: Vn) -> LOp {
        LOp::new(opc, out, a, b, 0)
    }

    /// `$20/$21 += $30/$31` as value numbering leaves `CLC; LDA; ADC; STA` twice.
    fn add16() -> Vec<LOp> {
        let t = |i| tmp(i, 1);
        vec![
            ld(0, 0x20),
            ld(1, 0x30),
            op(Opc::IntAdd, t(2), t(0), t(1)),
            op(Opc::IntCarry, t(3), t(0), t(1)),
            op(Opc::Store, Vn::NONE, k(0x20, 2), t(2)),
            ld(4, 0x21),
            ld(5, 0x31),
            op(Opc::IntAdd, t(6), t(4), t(5)),
            op(Opc::IntAdd, t(7), t(6), t(3)),
            op(Opc::Store, Vn::NONE, k(0x21, 2), t(7)),
            LOp::new(Opc::Return, Vn::NONE, k(0, 2), Vn::NONE, 0),
        ]
    }

    #[test]
    fn finds_and_fuses_an_add() {
        let ops = add16();
        let v = View::new(&ops);
        let c = chains(&v);
        assert!(c.iter().all(|c| c.1.len() == 2));
        let blk = Block {
            ops,
            ..Block::default()
        };
        let mut tm = Temps(100);
        let (out, ch) = c
            .iter()
            .find_map(|c| rewrite(&blk, c.0, &c.1, &mut tm))
            .unwrap();
        assert_eq!(ch.x, vec![Place::Abs(0x20), Place::Abs(0x21)]);
        assert_eq!(ch.r, vec![Place::Abs(0x20), Place::Abs(0x21)]);
        let wide: Vec<_> = out
            .iter()
            .filter(|o| {
                matches!(o.opc, Opc::Load | Opc::Store)
                    && (o.out.size() == 2 || o.ins[1].size() == 2)
            })
            .collect();
        assert_eq!(wide.len(), 3);
    }

    #[test]
    fn a_clobbering_store_blocks_the_wide_load() {
        let mut ops = add16();
        ops[4] = op(Opc::Store, Vn::NONE, k(0x31, 2), tmp(2, 1));
        let v = View::new(&ops);
        let c = chains(&v);
        let blk = Block {
            ops: ops.clone(),
            ..Block::default()
        };
        assert!(!c.is_empty());
        assert!(c
            .iter()
            .all(|c| rewrite(&blk, c.0, &c.1, &mut Temps(100)).is_none()));
    }

    /// `($FB),Y` as the template builds the pointer: the high byte loaded at `hi`, then a
    /// store of `st` to `sa` when given.
    fn ptr(hi: u32, st: Option<u32>) -> Function {
        let (t, w) = (|i| tmp(i, 1), |i| tmp(i, 2));
        let mut ops = vec![ld(0, 0xfb), ld(1, hi)];
        ops.extend(st.map(|a| op(Opc::Store, Vn::NONE, k(a, 2), r1(di_pcode::reg::A))));
        ops.extend([
            op(Opc::IntZext, w(4), t(1), Vn::NONE),
            op(Opc::IntLeft, w(2), w(4), k(8, 1)),
            op(Opc::IntZext, w(8), t(0), Vn::NONE),
            op(Opc::IntOr, w(6), w(8), w(2)),
            op(Opc::Load, r1(di_pcode::reg::A), w(6), Vn::NONE),
            LOp::new(Opc::Return, Vn::NONE, k(0, 2), Vn::NONE, 0),
        ]);
        Function {
            blocks: vec![Block {
                ops,
                ..Block::default()
            }],
            ..Function::default()
        }
    }

    #[test]
    fn a_pointer_pair_is_one_wide_load() {
        let mut f = ptr(0xfc, Some(0x20));
        assert_eq!(words(&mut f), 1);
        let or = &f.blocks[0].ops[6];
        assert_eq!(
            (or.opc, or.out.size(), or.ins[0]),
            (Opc::Load, 2, k(0xfb, 2))
        );
        for (hi, st) in [(0xfd, None), (0xfc, Some(0xfc)), (0xfc, Some(0xfb))] {
            assert_eq!(words(&mut ptr(hi, st)), 0, "{hi:x} {st:?}");
        }
        let mut f = ptr(0xfc, None);
        f.blocks[0].ops[0] = ld(0, 0xffff);
        f.blocks[0].ops[1] = ld(1, 0);
        assert_eq!(words(&mut f), 0);
    }

    #[test]
    fn decimal_links_stay() {
        let mut ops = add16();
        ops[7].aux = DECIMAL;
        let v = View::new(&ops);
        let c = chains(&v);
        let blk = Block {
            ops: ops.clone(),
            ..Block::default()
        };
        assert!(!c.is_empty());
        assert!(c
            .iter()
            .all(|c| rewrite(&blk, c.0, &c.1, &mut Temps(100)).is_none()));
    }

    #[test]
    fn registers_are_recognized_not_fused() {
        let a = r1(0);
        let ops = vec![
            op(Opc::IntAdd, tmp(0, 1), a, r1(1)),
            op(Opc::IntCarry, tmp(1, 1), a, r1(1)),
            op(Opc::IntAdd, tmp(2, 1), r1(2), tmp(1, 1)),
            op(Opc::Copy, reg(0, 1), tmp(2, 1), Vn::NONE),
        ];
        let v = View::new(&ops);
        let c = chains(&v);
        assert_eq!(c.len(), 1);
        let blk = Block {
            ops: ops.clone(),
            ..Block::default()
        };
        assert!(rewrite(&blk, c[0].0, &c[0].1, &mut Temps(100)).is_none());
    }

    #[test]
    fn shift_left_fuses() {
        let t = |i| tmp(i, 1);
        let c = reg(8, 1);
        let ops = vec![
            ld(2, 0x60),
            op(Opc::IntAnd, t(3), t(2), k(0x80, 1)),
            op(Opc::IntNotEqual, c, t(3), k(0, 1)),
            op(Opc::IntLeft, t(4), t(2), k(1, 1)),
            op(Opc::Store, Vn::NONE, k(0x60, 2), t(4)),
            ld(5, 0x61),
            op(Opc::IntLeft, t(6), t(5), k(1, 1)),
            op(Opc::IntOr, t(7), t(6), c),
            op(Opc::Store, Vn::NONE, k(0x61, 2), t(7)),
            LOp::new(Opc::Return, Vn::NONE, k(0, 2), Vn::NONE, 0),
        ];
        let v = View::new(&ops);
        let cs = chains(&v);
        assert!(!cs.is_empty(), "no chain");
        let blk = Block {
            ops: ops.clone(),
            ..Block::default()
        };
        assert!(cs
            .iter()
            .any(|c| rewrite(&blk, c.0, &c.1, &mut Temps(100)).is_some()));
    }
}
