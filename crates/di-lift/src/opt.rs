//! Optimization of lifted functions: CFG cleanup, register constant propagation across
//! blocks, value numbering over extended basic blocks (constant folding, copy propagation,
//! common subexpressions, store-to-load forwarding, removal of consumed pushes), and
//! liveness-based dead-code elimination.

use crate::analysis::{iv_op, Iv};
use crate::ir::{fold, k, mask, mem, other, Block, Function, LOp, CY, NREG};
use di_pcode::{Opc, Space, Vn};
use fixedbitset::FixedBitSet;
use rustc_hash::FxHashMap;

/// What the optimizer may assume about memory.
pub struct Ctx<'a> {
    pub img: &'a [u8; 0x10000],
    /// Constant code bytes.
    pub constcode: &'a FixedBitSet,
    pub volatile: &'a [(u32, u32)],
    /// Only pushes and pulls reach page 1: a push consumed by a pull in its block is dead.
    pub private: bool,
}

/// Optimize `f` to a fixpoint (bounded).
pub fn optimize(f: &mut Function, cx: &Ctx) {
    let mut last = usize::MAX;
    for _ in 0..8 {
        cleanup(f);
        rename(f);
        let consts = propagate(f);
        number(f, cx, &consts);
        cleanup(f);
        combine_adds(f);
        dce(f, &checkpoints(f));
        let n: usize = f.blocks.iter().map(|b| b.ops.len()).sum();
        if n == last {
            break;
        }
        last = n;
    }
    renumber_temps(f);
}

fn key(v: Vn) -> u32 {
    (v.space() as u32) << 16 | v.off()
}

/// Index of a register or temporary in liveness sets.
fn lidx(v: Vn) -> Option<usize> {
    match v.space() {
        Space::Reg => Some(v.off() as usize),
        Space::Unique => Some(NREG + v.off() as usize),
        _ => None,
    }
}

fn preds(f: &Function) -> Vec<u32> {
    let mut p = vec![0u32; f.blocks.len()];
    p[0] += 1;
    for b in &f.blocks {
        let mut s: Vec<u32> = b.succs(&f.tables).collect();
        s.sort_unstable();
        s.dedup();
        for x in s {
            p[x as usize] += 1;
        }
    }
    p
}

/// Blocks where all registers must be architectural: the entry, `CALL` returns and loop
/// headers (the executor's cycle-budget checks).
pub fn checkpoints(f: &Function) -> FixedBitSet {
    let n = f.blocks.len();
    let mut cp = FixedBitSet::with_capacity(n);
    cp.insert(0);
    for (i, b) in f.blocks.iter().enumerate() {
        cp.set(i, b.check);
    }
    cp.insert(0);
    for b in &f.blocks {
        if let Some(r) = b.call_return() {
            cp.insert(r as usize);
        }
    }
    // Iterative DFS: an edge to a block on the stack is a back edge.
    let mut state = vec![0u8; n];
    let mut stack: Vec<(u32, Vec<u32>)> = vec![(0, f.blocks[0].succs(&f.tables).collect())];
    state[0] = 1;
    while let Some((_, succ)) = stack.last_mut() {
        if let Some(s) = succ.pop() {
            match state[s as usize] {
                0 => {
                    state[s as usize] = 1;
                    let ss = f.blocks[s as usize].succs(&f.tables).collect();
                    stack.push((s, ss));
                }
                1 => {
                    cp.insert(s as usize);
                }
                _ => {}
            }
        } else {
            let (b, _) = stack.pop().unwrap();
            state[b as usize] = 2;
        }
    }
    cp
}

/// Fold constant branches, thread empty blocks, drop unreachable blocks and merge
/// straight-line chains.
pub fn cleanup(f: &mut Function) {
    loop {
        let mut changed = false;
        for bi in 0..f.blocks.len() {
            let ops = &mut f.blocks[bi].ops;
            let n = ops.len();
            if n >= 2 && ops[n - 2].opc == Opc::CBranch {
                let c = ops[n - 2].ins[0];
                let (t, fl) = (ops[n - 2].aux, ops[n - 1].aux);
                if c.space() == Space::Const || t == fl {
                    if c.space() == Space::Const && c.off() != 0 {
                        ops[n - 1].aux = t;
                    }
                    ops.remove(n - 2);
                    changed = true;
                }
            }
        }
        // Thread branches through blocks that only branch.
        let fwd: Vec<Option<u32>> = f
            .blocks
            .iter()
            .map(|b| match b.ops[..] {
                _ if b.check => None,
                [LOp {
                    opc: Opc::Branch,
                    aux,
                    ..
                }] => Some(aux),
                _ => None,
            })
            .collect();
        let resolve = |mut b: u32| {
            for _ in 0..fwd.len() {
                match fwd[b as usize] {
                    Some(t) if t != b => b = t,
                    _ => break,
                }
            }
            b
        };
        for b in &mut f.blocks {
            for op in &mut b.ops {
                if matches!(op.opc, Opc::Branch | Opc::CBranch) {
                    let t = resolve(op.aux);
                    changed |= t != op.aux;
                    op.aux = t;
                }
            }
        }
        for t in &mut f.tables {
            for e in t.iter_mut() {
                e.1 = resolve(e.1);
            }
        }
        // Merge `A: ... BRANCH B` with B when A is B's only predecessor.
        let p = preds(f);
        for a in 0..f.blocks.len() {
            loop {
                let ops = &f.blocks[a].ops;
                let n = ops.len();
                let Some(last) = ops.last() else { break };
                if last.opc != Opc::Branch
                    || n >= 2 && matches!(ops[n - 2].opc, Opc::CBranch | Opc::Call)
                {
                    break;
                }
                let b = last.aux as usize;
                if b == a || b == 0 || p[b] != 1 || f.blocks[b].ops.is_empty() || f.blocks[b].check
                {
                    break;
                }
                let tail = std::mem::take(&mut f.blocks[b].ops);
                let cyc = f.blocks[b].maxcyc;
                f.blocks[b].ops.push(LOp::new(
                    Opc::Branch,
                    Vn::NONE,
                    Vn::NONE,
                    Vn::NONE,
                    b as u32,
                ));
                let blk = &mut f.blocks[a];
                blk.ops.pop();
                blk.ops.extend(tail);
                blk.maxcyc += cyc;
                f.blocks[b].maxcyc = 0;
                changed = true;
            }
        }
        changed |= prune(f);
        if !changed {
            break;
        }
    }
}

/// Remove unreachable blocks and tables; true if any were removed.
fn prune(f: &mut Function) -> bool {
    let n = f.blocks.len();
    let mut seen = FixedBitSet::with_capacity(n);
    let mut work = vec![0u32];
    seen.insert(0);
    while let Some(b) = work.pop() {
        for s in f.blocks[b as usize].succs(&f.tables) {
            if !seen.put(s as usize) {
                work.push(s);
            }
        }
    }
    if seen.count_ones(..) == n {
        return false;
    }
    let mut map = vec![u32::MAX; n];
    let mut nb = Vec::with_capacity(n);
    for (i, b) in std::mem::take(&mut f.blocks).into_iter().enumerate() {
        if seen.contains(i) {
            map[i] = nb.len() as u32;
            nb.push(b);
        }
    }
    let mut tmap = vec![u32::MAX; f.tables.len()];
    let mut nt = Vec::new();
    for b in &mut nb {
        for op in &mut b.ops {
            match op.opc {
                Opc::Branch | Opc::CBranch => op.aux = map[op.aux as usize],
                Opc::BranchInd => {
                    let t = op.aux as usize;
                    if tmap[t] == u32::MAX {
                        tmap[t] = nt.len() as u32;
                        nt.push(
                            f.tables[t]
                                .iter()
                                .map(|&(a, x)| (a, map[x as usize]))
                                .collect(),
                        );
                    }
                    op.aux = tmap[t];
                }
                _ => {}
            }
        }
    }
    f.blocks = nb;
    f.tables = nt;
    true
}

/// Register lattice value.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum L {
    Top,
    K(u32),
    Bot,
}

fn meet(a: L, b: L) -> L {
    match (a, b) {
        (L::Top, x) | (x, L::Top) => x,
        (L::K(x), L::K(y)) if x == y => a,
        _ => L::Bot,
    }
}

/// Constant registers at each block entry, by forward dataflow over every CFG edge.
fn propagate(f: &Function) -> Vec<Vec<(u16, u32)>> {
    let n = f.blocks.len();
    let mut inn = vec![[L::Top; NREG]; n];
    inn[0] = [L::Bot; NREG];
    let mut work: Vec<u32> = (0..n as u32).rev().collect();
    let mut queued = FixedBitSet::with_capacity(n);
    queued.insert_range(..);
    let mut env: FxHashMap<u32, u32> = FxHashMap::default();
    while let Some(b) = work.pop() {
        queued.remove(b as usize);
        env.clear();
        for (i, l) in inn[b as usize].iter().enumerate() {
            if let L::K(v) = l {
                env.insert(key(crate::ir::reg(i as u16, 1)), *v);
            }
        }
        let blk = &f.blocks[b as usize];
        let mut after_call = false;
        for op in &blk.ops {
            if op.opc == Opc::Call {
                after_call = true;
            }
            if op.out.is_none() || op.out.space() == Space::Const {
                continue;
            }
            let val = |v: Vn| match v.space() {
                Space::Const => Some(v.off()),
                _ => env.get(&key(v)).copied(),
            };
            let r = match op.opc {
                Opc::Load | Opc::CallOther => None,
                _ => {
                    let a = val(op.ins[0]);
                    let b = if op.opc.arity() > 1 {
                        val(op.ins[1])
                    } else {
                        Some(0)
                    };
                    a.zip(b).map(|(a, b)| crate::ir::eval(op, a, b))
                }
            };
            match r {
                Some(v) => env.insert(key(op.out), v),
                None => env.remove(&key(op.out)),
            };
        }
        let out: [L; NREG] = std::array::from_fn(|i| {
            if after_call {
                return L::Bot;
            }
            env.get(&key(crate::ir::reg(i as u16, 1)))
                .map_or(L::Bot, |&v| L::K(v))
        });
        let mut succ: Vec<u32> = blk.succs(&f.tables).collect();
        succ.sort_unstable();
        succ.dedup();
        for s in succ {
            let cur = inn[s as usize];
            let new: [L; NREG] = std::array::from_fn(|i| meet(cur[i], out[i]));
            if new != cur {
                inn[s as usize] = new;
                if !queued.put(s as usize) {
                    work.push(s);
                }
            }
        }
    }
    inn.iter()
        .map(|r| {
            r.iter()
                .enumerate()
                .filter_map(|(i, l)| match l {
                    L::K(v) => Some((i as u16, *v)),
                    _ => None,
                })
                .collect()
        })
        .collect()
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
enum Val {
    K(u32),
    S(u32),
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
struct SKey {
    opc: Opc,
    a: Val,
    b: Val,
    size: u8,
    size0: u8,
}

#[derive(Clone, Copy, Debug)]
struct Sym {
    key: Option<SKey>,
    size: u8,
    iv: Iv,
}

impl Sym {
    fn is_bool(&self) -> bool {
        self.size == 1 && self.iv.hi <= 1
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum MemKey {
    Const(u16),
    /// `0x100 | (base + off) & 0xff`.
    Stack(u32, u8),
    Other(u32),
}

/// A known memory byte: key, value, and the tracked push (block, index) that stored it.
type Known = (MemKey, Val, Option<(u32, usize)>);

#[derive(Default)]
struct St {
    vmap: FxHashMap<u32, Val>,
    mem: Vec<Known>,
    /// Changes to undo when leaving a block of the extended basic block.
    undo: Vec<Undo>,
}

enum Undo {
    Var(u32, Option<Val>),
    MemPush,
    MemKill(usize, Known),
}

impl St {
    fn set(&mut self, k: u32, v: Val) {
        let old = self.vmap.insert(k, v);
        self.undo.push(Undo::Var(k, old));
    }
    fn remember(&mut self, e: Known) {
        self.mem.push(e);
        self.undo.push(Undo::MemPush);
    }
    fn forget(&mut self, mut dead: impl FnMut(MemKey) -> bool) {
        let mut i = 0;
        while i < self.mem.len() {
            if dead(self.mem[i].0) {
                let e = self.mem.remove(i);
                self.undo.push(Undo::MemKill(i, e));
            } else {
                i += 1;
            }
        }
    }
    fn rewind(&mut self, mark: usize) {
        while self.undo.len() > mark {
            match self.undo.pop().unwrap() {
                Undo::Var(k, Some(v)) => {
                    self.vmap.insert(k, v);
                }
                Undo::Var(k, None) => {
                    self.vmap.remove(&k);
                }
                Undo::MemPush => {
                    self.mem.pop();
                }
                Undo::MemKill(i, e) => self.mem.insert(i, e),
            }
        }
    }
}

/// A push within the current block: its op index, and whether a pull consumed it, a later
/// push overwrote it, or another access may have read it.
struct Push {
    key: MemKey,
    at: usize,
    dead: bool,
    seen: bool,
}

struct Lvn<'a> {
    cx: &'a Ctx<'a>,
    syms: Vec<Sym>,
    hc: FxHashMap<SKey, u32>,
    holders: FxHashMap<u32, Vec<Vn>>,
}

impl Lvn<'_> {
    fn leaf(&mut self, size: u8, iv: Iv) -> u32 {
        self.syms.push(Sym {
            key: None,
            size,
            iv,
        });
        (self.syms.len() - 1) as u32
    }
    fn iv(&self, v: Val) -> Iv {
        match v {
            Val::K(x) => Iv::exact(x),
            Val::S(s) => self.syms[s as usize].iv,
        }
    }
    fn is_bool(&self, v: Val) -> bool {
        match v {
            Val::K(x) => x <= 1,
            Val::S(s) => self.syms[s as usize].is_bool(),
        }
    }
    fn skey(&self, v: Val) -> Option<SKey> {
        match v {
            Val::S(s) => self.syms[s as usize].key,
            Val::K(_) => None,
        }
    }
    fn val(&mut self, st: &mut St, v: Vn) -> Val {
        if v.space() == Space::Const {
            return Val::K(v.off());
        }
        if let Some(&x) = st.vmap.get(&key(v)) {
            return x;
        }
        let flag = v.space() == Space::Reg && (8..15).contains(&v.off());
        let iv = if flag {
            Iv { lo: 0, hi: 1 }
        } else {
            Iv::full(v.size())
        };
        let s = self.leaf(v.size() as u8, iv);
        st.set(key(v), Val::S(s));
        self.holders.entry(s).or_default().push(v);
        Val::S(s)
    }
    /// A varnode holding `x` now, else `orig`.
    fn vn(&self, st: &St, x: Val, size: u32, orig: Vn) -> Vn {
        match x {
            Val::K(c) if c & mask(size) <= 0xffff => k(c, size as u8),
            Val::K(_) => orig,
            Val::S(s) => self
                .holders
                .get(&s)
                .and_then(|h| {
                    h.iter()
                        .copied()
                        .find(|&h| st.vmap.get(&key(h)) == Some(&x) && h.size() == size)
                })
                .unwrap_or(orig),
        }
    }
    /// The value of `opc(a, b)`: a constant, an existing symbol, or a new one (`true`).
    fn simplify(&mut self, opc: Opc, a: Val, b: Val, size: u8, size0: u8) -> (Val, bool) {
        let m = mask(size as u32);
        use Val::{K, S};
        if let (K(x), K(y)) = (a, b) {
            return (K(fold(opc, x, y, size as u32, size0 as u32)), false);
        }
        let commutes = matches!(
            opc,
            Opc::IntAdd | Opc::IntAnd | Opc::IntOr | Opc::IntXor | Opc::IntEqual | Opc::IntNotEqual
        );
        let (a, b) = match (a, b) {
            (K(_), S(_)) if commutes => (b, a),
            (S(x), S(y)) if commutes && y < x => (b, a),
            _ => (a, b),
        };
        let same = a == b;
        let r = match (opc, a, b) {
            (Opc::Copy, _, _) => Some(a),
            (Opc::IntZext, S(s), _) if self.syms[s as usize].size == size => Some(a),
            (Opc::IntSub, _, _) if same => Some(K(0)),
            (Opc::IntSub, _, K(c)) => {
                return self.simplify(Opc::IntAdd, a, K(c.wrapping_neg() & m), size, size0)
            }
            (Opc::IntAdd, _, K(0)) => Some(a),
            (Opc::IntAdd, S(_), K(c)) => match self.skey(a) {
                Some(SKey {
                    opc: Opc::IntAdd,
                    a: x,
                    b: K(d),
                    size: sz,
                    ..
                }) if sz == size => {
                    return self.simplify(Opc::IntAdd, x, K((c + d) & m), size, size0)
                }
                _ => None,
            },
            (Opc::IntAnd, _, K(0)) => Some(K(0)),
            (Opc::IntAnd, S(_), K(c)) if self.iv(a).hi < c.isolate_lowest_one() => Some(K(0)),
            (Opc::IntAnd | Opc::IntOr | Opc::IntXor, S(_), K(c)) if matches!(self.skey(a), Some(SKey { opc: o, b: K(_), size: sz, .. }) if o == opc && sz == size) =>
            {
                let Some(SKey { a: x, b: K(d), .. }) = self.skey(a) else {
                    unreachable!()
                };
                let e = match opc {
                    Opc::IntAnd => c & d,
                    Opc::IntOr => c | d,
                    _ => c ^ d,
                };
                return self.simplify(opc, x, K(e), size, size0);
            }
            (Opc::IntAnd, _, K(c)) if c == m => Some(a),
            (Opc::IntAnd, _, K(1)) if self.is_bool(a) => Some(a),
            (Opc::IntAnd | Opc::IntOr, _, _) if same => Some(a),
            (Opc::IntOr | Opc::IntXor, _, K(0)) => Some(a),
            (Opc::IntXor, _, _) if same => Some(K(0)),
            (Opc::IntLeft | Opc::IntRight, _, K(0)) => Some(a),
            (Opc::IntEqual | Opc::IntNotEqual, S(_), K(c)) if matches!(self.skey(a), Some(SKey { opc: Opc::IntAdd, b: K(_), size: sz, .. }) if sz == size0) =>
            {
                let Some(SKey { a: x, b: K(d), .. }) = self.skey(a) else {
                    unreachable!()
                };
                let m0 = mask(size0 as u32);
                return self.simplify(opc, x, K(c.wrapping_sub(d) & m0), size, size0);
            }
            (Opc::IntEqual, _, _) if same => Some(K(1)),
            (Opc::IntNotEqual, _, _) if same => Some(K(0)),
            (Opc::IntEqual, _, K(1)) | (Opc::IntNotEqual, _, K(0)) if self.is_bool(a) => Some(a),
            (Opc::IntEqual, _, K(0)) | (Opc::IntNotEqual, _, K(1)) if self.is_bool(a) => {
                return self.simplify(Opc::IntXor, a, K(1), 1, 1)
            }
            (Opc::IntXor, _, K(1)) if self.is_bool(a) => match self.skey(a) {
                Some(SKey {
                    opc: Opc::IntXor,
                    a: x,
                    b: K(1),
                    ..
                }) => Some(x),
                _ => None,
            },
            (Opc::IntCarry, _, K(0)) | (Opc::IntCarry, K(0), _) => Some(K(0)),
            _ => self.range_fold(opc, a, b, size0),
        };
        if let Some(v) = r {
            return (v, false);
        }
        let sk = SKey {
            opc,
            a,
            b,
            size,
            size0,
        };
        if let Some(&s) = self.hc.get(&sk) {
            return (S(s), false);
        }
        let iv = match opc {
            Opc::IntZext => self.iv(a),
            _ => iv_op(opc, self.iv(a), self.iv(b), size as u32, size0 as u32),
        };
        self.syms.push(Sym {
            key: Some(sk),
            size,
            iv,
        });
        let s = (self.syms.len() - 1) as u32;
        self.hc.insert(sk, s);
        (S(s), true)
    }
    /// Comparisons and carries decided by the operands' intervals.
    fn range_fold(&self, opc: Opc, a: Val, b: Val, size0: u8) -> Option<Val> {
        let (x, y) = (self.iv(a), self.iv(b));
        let m = mask(size0 as u32) as u64;
        let (lo, hi) = match opc {
            Opc::IntCarry => (x.lo as u64 + y.lo as u64 > m, x.hi as u64 + y.hi as u64 > m),
            Opc::IntLess => (x.hi < y.lo, x.lo < y.hi),
            Opc::IntLessEqual => (x.hi <= y.lo, x.lo <= y.hi),
            Opc::IntEqual | Opc::IntNotEqual if x.hi < y.lo || y.hi < x.lo => {
                let ne = (opc == Opc::IntNotEqual) as u32;
                return Some(Val::K(ne));
            }
            _ => return None,
        };
        (lo == hi).then_some(Val::K(lo as u32))
    }
    fn memkey(&self, a: Val) -> MemKey {
        match a {
            Val::K(x) => MemKey::Const(x as u16),
            Val::S(s) => {
                if let Some(SKey {
                    opc: Opc::IntOr,
                    a: Val::S(z),
                    b: Val::K(0x100),
                    ..
                }) = self.syms[s as usize].key
                {
                    if let Some(SKey {
                        opc: Opc::IntZext,
                        a: Val::S(w),
                        ..
                    }) = self.syms[z as usize].key
                    {
                        if self.syms[w as usize].size == 1 {
                            return match self.syms[w as usize].key {
                                Some(SKey {
                                    opc: Opc::IntAdd,
                                    a: Val::S(base),
                                    b: Val::K(c),
                                    ..
                                }) => MemKey::Stack(base, c as u8),
                                _ => MemKey::Stack(w, 0),
                            };
                        }
                    }
                }
                MemKey::Other(s)
            }
        }
    }
    fn key_iv(&self, m: MemKey) -> Iv {
        match m {
            MemKey::Const(a) => Iv::exact(a as u32),
            MemKey::Stack(..) => Iv {
                lo: 0x100,
                hi: 0x1ff,
            },
            MemKey::Other(s) => self.syms[s as usize].iv,
        }
    }
    fn alias(&self, x: MemKey, y: MemKey) -> bool {
        match (x, y) {
            (MemKey::Const(a), MemKey::Const(b)) => a == b,
            (MemKey::Stack(b1, o1), MemKey::Stack(b2, o2)) if b1 == b2 => o1 == o2,
            (MemKey::Other(a), MemKey::Other(b)) if a == b => true,
            _ => {
                let (p, q) = (self.key_iv(x), self.key_iv(y));
                p.lo <= q.hi && q.lo <= p.hi
            }
        }
    }
}

/// Extended-basic-block roots: the entry, blocks without exactly one predecessor, and
/// `CALL` returns.
fn roots(f: &Function) -> FixedBitSet {
    let p = preds(f);
    let mut root = FixedBitSet::with_capacity(f.blocks.len());
    root.insert(0);
    for (i, &c) in p.iter().enumerate() {
        if c != 1 {
            root.insert(i);
        }
    }
    for b in &f.blocks {
        if let Some(r) = b.call_return() {
            root.insert(r as usize);
        }
    }
    root
}

/// Children of `b` in its extended basic block.
fn ebb_children(f: &Function, root: &FixedBitSet, b: u32) -> Vec<u32> {
    let mut succ: Vec<u32> = f.blocks[b as usize].succs(&f.tables).collect();
    succ.sort_unstable();
    succ.dedup();
    succ.retain(|&s| !root.contains(s as usize));
    succ
}

/// Give every temporary definition its own number within its extended basic block, so value
/// numbering can keep using values the emitted code overwrote.
fn rename(f: &mut Function) {
    let root = roots(f);
    let mut most = 0u32;
    for r in root.ones() {
        // Ops of the extended basic block bound its definitions; a larger one keeps the
        // emitted numbering.
        let mut size = 0usize;
        let mut work = vec![r as u32];
        while let Some(b) = work.pop() {
            size += 2 * f.blocks[b as usize].ops.len();
            work.extend(ebb_children(f, &root, b));
        }
        if size >= 0xffff {
            most = most.max(f.ntemps);
            continue;
        }
        let mut next = 0u32;
        let mut stack = vec![(r as u32, FxHashMap::<u32, u16>::default())];
        while let Some((b, mut map)) = stack.pop() {
            for op in &mut f.blocks[b as usize].ops {
                let arity = op.opc.arity();
                for v in op.ins[..arity].iter_mut() {
                    if v.space() == Space::Unique {
                        let n = *map.entry(v.off()).or_insert_with(|| {
                            next += 1;
                            (next - 1) as u16
                        });
                        *v = crate::ir::tmp(n, v.size() as u8);
                    }
                }
                if op.out.space() == Space::Unique {
                    next += 1;
                    map.insert(op.out.off(), (next - 1) as u16);
                    op.out = crate::ir::tmp((next - 1) as u16, op.out.size() as u8);
                }
            }
            let kids = ebb_children(f, &root, b);
            for k in kids {
                stack.push((k, map.clone()));
            }
        }
        most = most.max(next);
    }
    f.ntemps = most;
}

/// Value numbering over extended basic blocks, seeded with `consts` at their roots.
fn number(f: &mut Function, cx: &Ctx, consts: &[Vec<(u16, u32)>]) {
    let root = roots(f);
    let mut lv = Lvn {
        cx,
        syms: Vec::new(),
        hc: FxHashMap::default(),
        holders: FxHashMap::default(),
    };
    let mut st = St::default();
    for r in root.ones() {
        st.vmap.clear();
        st.mem.clear();
        st.undo.clear();
        for &(i, v) in &consts[r] {
            st.vmap.insert(key(crate::ir::reg(i, 1)), Val::K(v));
        }
        // Depth-first over the tree, undoing each block's changes after its subtree.
        let mut stack: Vec<(u32, Option<usize>)> = vec![(r as u32, None)];
        while let Some((b, mark)) = stack.pop() {
            if let Some(m) = mark {
                st.rewind(m);
                continue;
            }
            stack.push((b, Some(st.undo.len())));
            number_block(&mut lv, &mut f.blocks[b as usize], b, &f.tables, &mut st);
            for s in ebb_children(f, &root, b) {
                stack.push((s, None));
            }
        }
    }
}

fn number_block(lv: &mut Lvn, blk: &mut Block, bi: u32, tables: &[Vec<(u16, u32)>], st: &mut St) {
    let cx = lv.cx;
    let ops = std::mem::take(&mut blk.ops);
    let mut out: Vec<LOp> = Vec::with_capacity(ops.len());
    let mut pushes: Vec<Push> = Vec::new();
    for mut op in ops {
        let arity = op.opc.arity();
        let a = if arity > 0 {
            lv.val(st, op.ins[0])
        } else {
            Val::K(0)
        };
        let b = if arity > 1 {
            lv.val(st, op.ins[1])
        } else {
            Val::K(0)
        };
        for i in 0..arity {
            let x = if i == 0 { a } else { b };
            op.ins[i] = lv.vn(st, x, op.ins[i].size(), op.ins[i]);
        }
        let mut result: Option<(Val, bool)> = None;
        match op.opc {
            Opc::Load => {
                if let Val::K(x) = a {
                    if !cx.volatile.iter().any(|&(lo, hi)| (lo..hi).contains(&x)) {
                        op.aux &= !mem::VOLATILE;
                    }
                }
                let mk = lv.memkey(a);
                let hit = (!op.volatile())
                    .then(|| st.mem.iter().find(|e| e.0 == mk).copied())
                    .flatten();
                if let Some((_, v, push)) = hit {
                    // Without a varnode still holding the value the load stays, reading the
                    // stored byte, so that store must stay too.
                    let held = !lv.vn(st, v, 1, Vn::NONE).is_none();
                    if let Some((_, i)) = push.filter(|p| p.0 == bi) {
                        if held {
                            pushes[i].dead = true;
                        } else {
                            pushes[i].seen = true;
                        }
                    }
                    result = Some((v, false));
                } else {
                    for pu in pushes.iter_mut() {
                        if lv.alias(pu.key, mk) {
                            pu.seen = true;
                        }
                    }
                    let s = lv.leaf(1, Iv::full(1));
                    if !op.volatile() {
                        st.remember((mk, Val::S(s), None));
                    }
                    result = Some((Val::S(s), true));
                }
            }
            Opc::Store => {
                if let Val::K(x) = a {
                    if !cx.volatile.iter().any(|&(lo, hi)| (lo..hi).contains(&x)) {
                        op.aux &= !mem::VOLATILE;
                    }
                }
                let mk = lv.memkey(a);
                st.forget(|e| lv.alias(e, mk));
                for pu in pushes.iter_mut() {
                    if pu.key == mk && !pu.seen {
                        pu.dead = true;
                    }
                }
                let push = (cx.private && op.aux & mem::STACK != 0 && !op.volatile()).then(|| {
                    pushes.push(Push {
                        key: mk,
                        at: out.len(),
                        dead: false,
                        seen: false,
                    });
                    (bi, pushes.len() - 1)
                });
                if !op.volatile() {
                    st.remember((mk, b, push));
                }
            }
            Opc::CBranch => {
                if let Val::K(c) = a {
                    if c != 0 {
                        out.push(LOp::new(Opc::Branch, Vn::NONE, Vn::NONE, Vn::NONE, op.aux));
                        break;
                    }
                    continue;
                }
            }
            Opc::BranchInd => {
                if let Val::K(t) = a {
                    let tb = &tables[op.aux as usize];
                    op = match tb.binary_search_by_key(&(t as u16), |e| e.0) {
                        Ok(i) => LOp::new(Opc::Branch, Vn::NONE, Vn::NONE, Vn::NONE, tb[i].1),
                        Err(_) => {
                            LOp::new(Opc::CallOther, Vn::NONE, k(t, 2), Vn::NONE, other::DEOPT)
                        }
                    };
                }
            }
            Opc::CallOther if op.aux == other::CODECHK => {
                let c = match (a, b) {
                    (Val::K(x), _) if !cx.constcode.contains(x as usize) => Some(0),
                    (Val::K(x), Val::K(y)) => Some((cx.img[x as usize] as u32 != y) as u32),
                    _ => None,
                };
                result = Some(match c {
                    Some(c) => (Val::K(c), false),
                    None => (Val::S(lv.leaf(1, Iv { lo: 0, hi: 1 })), true),
                });
            }
            Opc::CallOther | Opc::Branch | Opc::Return | Opc::Call => {}
            opc => {
                let size0 = if arity > 0 { op.ins[0].size() as u8 } else { 0 };
                result = Some(lv.simplify(opc, a, b, op.out.size() as u8, size0));
            }
        }
        if let Some((v, fresh)) = result {
            let dst = op.out;
            if !fresh {
                let src = lv.vn(st, v, dst.size(), Vn::NONE);
                if src == dst {
                    continue;
                }
                if !src.is_none() {
                    op = LOp::new(Opc::Copy, dst, src, Vn::NONE, 0);
                }
            }
            st.set(key(dst), v);
            if let Val::S(s) = v {
                let h = lv.holders.entry(s).or_default();
                if !h.contains(&dst) {
                    h.push(dst);
                }
            }
        }
        out.push(op);
        if op.is_term() {
            break;
        }
    }
    let mut dead: Vec<usize> = pushes
        .iter()
        .filter(|p| p.dead && !p.seen)
        .map(|p| p.at)
        .collect();
    dead.sort_unstable();
    for i in dead.into_iter().rev() {
        out.remove(i);
    }
    blk.ops = out;
}

/// Combine `R = R + c` adds of a register that nothing reads in between (adds of other
/// values commute past them).
fn combine_adds(f: &mut Function) {
    for blk in &mut f.blocks {
        let mut last: FxHashMap<u32, usize> = FxHashMap::default();
        let mut drop: Vec<usize> = Vec::new();
        for i in 0..blk.ops.len() {
            let op = blk.ops[i];
            let r = op.out;
            let selfadd = matches!(op.opc, Opc::IntAdd | Opc::IntSub)
                && r.space() == Space::Reg
                && op.ins[0] == r
                && op.ins[1] != r;
            if selfadd && op.ins[1].space() == Space::Const {
                let c = op.ins[1].off();
                let c = if op.opc == Opc::IntSub {
                    c.wrapping_neg()
                } else {
                    c
                } & mask(r.size());
                let sum = last.get(&key(r)).map(|&j| {
                    let p = &blk.ops[j];
                    let pc = if p.opc == Opc::IntSub {
                        p.ins[1].off().wrapping_neg()
                    } else {
                        p.ins[1].off()
                    };
                    (j, pc.wrapping_add(c) & mask(r.size()))
                });
                if let Some((j, v)) = sum.filter(|&(_, v)| v <= 0xffff) {
                    blk.ops[j] = LOp::new(Opc::IntAdd, r, r, k(v, r.size() as u8), 0);
                    drop.push(i);
                } else {
                    last.insert(key(r), i);
                }
                continue;
            }
            if matches!(op.opc, Opc::Call | Opc::Return | Opc::CBranch) || op.is_deopt() {
                last.clear();
                continue;
            }
            if op.volatile() {
                last.remove(&key(crate::ir::cy()));
            }
            for v in op.inputs() {
                if !(selfadd && *v == r) {
                    last.remove(&key(*v));
                }
            }
            if !selfadd && !r.is_none() {
                last.remove(&key(r));
            }
        }
        for i in drop.into_iter().rev() {
            blk.ops.remove(i);
        }
        blk.ops.retain(|op| {
            !(op.opc == Opc::IntAdd
                && op.ins[0] == op.out
                && op.ins[1].space() == Space::Const
                && op.ins[1].off() == 0)
        });
    }
}

/// Remove ops whose results are never used, by liveness over registers and temporaries.
fn dce(f: &mut Function, cps: &FixedBitSet) {
    let n = f.blocks.len();
    let width = NREG + f.ntemps as usize;
    let all = |s: &mut FixedBitSet| s.insert_range(0..NREG);
    let transfer = |blk: &Block, live: &mut FixedBitSet| -> Vec<bool> {
        let mut keep = vec![true; blk.ops.len()];
        for (i, op) in blk.ops.iter().enumerate().rev() {
            if let Some(o) = lidx(op.out) {
                if !op.effectful() && !live.contains(o) {
                    keep[i] = false;
                    continue;
                }
                live.remove(o);
            }
            for v in op.inputs() {
                if let Some(x) = lidx(*v) {
                    live.insert(x);
                }
            }
            if op.volatile() {
                live.insert(CY as usize);
            }
            // A `BRANCHIND` to an address outside its table deopts there.
            if matches!(op.opc, Opc::Call | Opc::Return | Opc::BranchInd) || op.is_deopt() {
                all(live);
            }
        }
        keep
    };
    let mut live_in = vec![FixedBitSet::with_capacity(width); n];
    loop {
        let mut changed = false;
        for b in (0..n).rev() {
            let mut live = FixedBitSet::with_capacity(width);
            for s in f.blocks[b].succs(&f.tables) {
                live.union_with(&live_in[s as usize]);
            }
            transfer(&f.blocks[b], &mut live);
            if cps.contains(b) {
                all(&mut live);
            }
            if live != live_in[b] {
                live_in[b] = live;
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    for b in 0..n {
        let mut live = FixedBitSet::with_capacity(width);
        for s in f.blocks[b].succs(&f.tables) {
            live.union_with(&live_in[s as usize]);
        }
        let keep = transfer(&f.blocks[b], &mut live);
        let mut it = keep.iter();
        f.blocks[b].ops.retain(|_| *it.next().unwrap());
    }
}

/// Renumber temporaries densely in order of definition.
fn renumber_temps(f: &mut Function) {
    let mut map: FxHashMap<u32, u16> = FxHashMap::default();
    for b in &mut f.blocks {
        for op in &mut b.ops {
            for v in op.ins.iter_mut().chain(std::iter::once(&mut op.out)) {
                if v.space() == Space::Unique {
                    let n = map.len() as u16;
                    let t = *map.entry(v.off()).or_insert(n);
                    *v = crate::ir::tmp(t, v.size() as u8);
                }
            }
        }
    }
    f.ntemps = map.len() as u32;
}
