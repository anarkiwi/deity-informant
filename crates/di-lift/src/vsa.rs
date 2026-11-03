//! Flow-sensitive interval analysis of the discovered program: per instruction start the
//! interval of every register on every path reaching it, from which each access's address
//! interval follows.
//!
//! The domain is an interval per register. The 6502's flag-setting idioms are kept
//! symbolically: a flag set by comparing `base + k` with a constant is a predicate on the
//! register holding `base + shift`, so a conditional branch narrows that register on each edge
//! before its interval is hulled. Joins are interval hulls; loop heads (targets of depth-first
//! back edges) widen to the next compared-with constant, so every chain is finite.
//!
//! Calls are analysed per call site: a node is an instruction start in the context of the
//! `JSR` return site its function was entered from, and an `RTS` returns to that site only.
//! A callee also records which registers it leaves as `entry value + offset`, so a caller
//! gets its own value back through any number of nested calls. This holds only while a return
//! address is the one a `JSR` pushed: where a function's pushes and pulls do not balance, or
//! the stack is not private (`matched` is false: other stores may rewrite return addresses),
//! an `RTS` may reach any return site and nothing is carried through it.
//!
//! Memory contents are unknown: a load yields its full range. The result holds for any machine
//! state in which the entries are called with their declared registers, since only lifted
//! control edges are followed and every other one deopts.

use crate::analysis::{
    absint, flow_succs, operand_bytes, stack_delta, Access, Entry, Env, Flow, Iv, Var, TOP_ENV,
};
use crate::ir::fold;
use di_pcode::{CtrlKind, Op, Opc, Slot, Space, Template, Vn, UNIQUE_SIZE};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::VecDeque;

/// Flag registers with predicates: C, Z, N.
const FLAGS: [usize; 3] = [8, 9, 14];
/// Registers a predicate can constrain: A, X, Y.
const DATA: usize = 3;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Rel {
    Eq,
    Ne,
    Ge,
    Lt,
}

impl Rel {
    fn not(self) -> Rel {
        match self {
            Rel::Eq => Rel::Ne,
            Rel::Ne => Rel::Eq,
            Rel::Ge => Rel::Lt,
            Rel::Lt => Rel::Ge,
        }
    }
}

/// The flag is `rel((x + off) mod 256, k)` for a value `x` bounded by `pre` that register
/// `reg` holds as `x + shift`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
struct Pred {
    reg: u8,
    off: u8,
    shift: u8,
    rel: Rel,
    k: u8,
    pre: Iv,
}

#[derive(Clone, PartialEq, Debug)]
pub(crate) struct St {
    pub(crate) env: Env,
    pred: [Option<Pred>; 3],
    /// `A`, `X` and `Y` as `base + off` of the register `base` at the function's entry.
    rel: [Option<(u8, u8)>; DATA],
    /// Zero-page bytes stored at an exact address on every path since the entry: a protected
    /// pointer pair whose high byte is one holds its window, as its stores are checked.
    stored: [u128; 2],
    /// The entries (bit `i % 64` for entry `i`) the state is reached from.
    from: u64,
}

impl St {
    fn new(env: Env) -> St {
        St {
            env,
            pred: [None; 3],
            rel: [Some((0, 0)), Some((1, 0)), Some((2, 0))],
            stored: [0; 2],
            from: 0,
        }
    }
    fn has(&self, a: u32) -> bool {
        self.stored[(a >> 7) as usize] >> (a & 127) & 1 == 1
    }
}

/// A one-byte value as a symbol: `base + off`, bit 7 of it, or a condition on it.
#[derive(Clone, Copy)]
pub(crate) enum Sym {
    Lin(u8, u8),
    Bit7(u8, u8),
    Cond(u8, u8, Rel, u8),
    /// A constant.
    K(u8),
}

#[derive(Clone, Copy)]
enum Val {
    C(u8),
    S(Sym),
}

/// Symbols of the registers after `ops` (entry `A`, `X`, `Y` are bases 0-2, each load a new
/// base) and the mask of registers they write.
fn symbolic(ops: &[Op], b: [Option<u8>; 2], dec: Option<u8>) -> ([Option<Sym>; 16], u32) {
    let (reg, written, _) = symbolic_in(ops, b, (None, dec), None);
    (reg, written)
}

/// The base of the byte at a tracked address in [`symbolic_in`].
pub(crate) const MEM: u8 = u8::MAX;

/// [`symbolic`] with the carry and decimal flag known (`known`) and, for a tracked address `mem` whose loads
/// are base [`MEM`], the symbol of the last value stored there (`Some(None)`: not a symbol).
pub(crate) fn symbolic_in(
    ops: &[Op],
    b: [Option<u8>; 2],
    known: (Option<u8>, Option<u8>),
    mem: Option<u32>,
) -> ([Option<Sym>; 16], u32, Option<Option<Sym>>) {
    let (carry, dec) = known;
    let val = |s: Sym| match s {
        Sym::K(k) => Val::C(k),
        s => Val::S(s),
    };
    let mut stored = None;
    let mut reg = [None; 16];
    for (r, s) in reg.iter_mut().enumerate().take(DATA) {
        *s = Some(Sym::Lin(r as u8, 0));
    }
    let mut uniq = [None; UNIQUE_SIZE];
    let (mut written, mut fresh) = (0u32, DATA as u8);
    for op in ops {
        let get =
            |v: Vn, reg: &[Option<Sym>; 16], uniq: &[Option<Sym>; UNIQUE_SIZE]| -> Option<Val> {
                if v.size() != 1 {
                    return None;
                }
                match v.space() {
                    Space::Reg if v.off() == 8 && written >> 8 & 1 == 0 && carry.is_some() => {
                        carry.map(Val::C)
                    }
                    Space::Reg if v.off() == 11 && written >> 11 & 1 == 0 && dec.is_some() => {
                        dec.map(Val::C)
                    }
                    Space::Const => match v.slot() {
                        Slot::Fixed => u8::try_from(v.off()).ok().map(Val::C),
                        Slot::B1 => b[0].map(Val::C),
                        _ => None,
                    },
                    Space::Reg => reg[v.off() as usize].map(val),
                    Space::Unique => uniq[v.off() as usize].map(val),
                    Space::Ram => None,
                }
            };
        let (x, y) = (get(op.ins[0], &reg, &uniq), get(op.ins[1], &reg, &uniq));
        let lin = |a, c| match (a, c) {
            (Some(Val::S(Sym::Lin(b, o))), Some(Val::C(k)))
            | (Some(Val::C(k)), Some(Val::S(Sym::Lin(b, o)))) => Some((b, o, k)),
            _ => None,
        };
        let cond = |rel, k, (b, o): (u8, u8)| Some(Sym::Cond(b, o, rel, k));
        let at = |v: Vn| match v.space() {
            Space::Const if v.slot() == Slot::Fixed => Some(v.off()),
            Space::Const => crate::analysis::slot_iv(v.slot(), b, v.size()).value(),
            _ => None,
        };
        let r = match op.opc {
            Opc::Copy if matches!(x, Some(Val::C(_))) => match x {
                Some(Val::C(k)) => Some(Sym::K(k)),
                _ => None,
            },
            o if matches!(
                o,
                Opc::IntAdd
                    | Opc::IntSub
                    | Opc::IntAnd
                    | Opc::IntOr
                    | Opc::IntXor
                    | Opc::IntLeft
                    | Opc::IntRight
                    | Opc::IntEqual
                    | Opc::IntNotEqual
                    | Opc::IntLess
                    | Opc::IntLessEqual
                    | Opc::IntCarry
            ) && matches!((x, y), (Some(Val::C(_)), Some(Val::C(_)))) =>
            {
                match (x, y) {
                    (Some(Val::C(p)), Some(Val::C(q))) => {
                        Some(Sym::K(fold(o, p as u32, q as u32, 1, 1) as u8))
                    }
                    _ => None,
                }
            }
            Opc::IntAnd if matches!(x, Some(Val::C(0))) || matches!(y, Some(Val::C(0))) => {
                Some(Sym::K(0))
            }
            Opc::IntOr | Opc::IntXor if matches!(y, Some(Val::C(0))) => match x {
                Some(Val::S(s)) => Some(s),
                _ => None,
            },
            Opc::IntOr | Opc::IntXor if matches!(x, Some(Val::C(0))) => match y {
                Some(Val::S(s)) => Some(s),
                _ => None,
            },
            Opc::Load if op.out.size() == 1 && mem.is_some() && at(op.ins[0]) == mem => {
                Some(Sym::Lin(MEM, 0))
            }
            Opc::Load if op.out.size() == 1 => {
                fresh = fresh.wrapping_add(1);
                Some(Sym::Lin(fresh, 0))
            }
            Opc::Store if mem.is_some() && at(op.ins[0]) == mem => {
                stored = Some(match y {
                    Some(Val::S(s)) => Some(s),
                    _ => None,
                });
                None
            }
            Opc::Copy => match x {
                Some(Val::S(s)) => Some(s),
                _ => None,
            },
            Opc::IntAdd => lin(x, y).map(|(b, o, k)| Sym::Lin(b, o.wrapping_add(k))),
            Opc::IntSub => match (x, y) {
                (Some(Val::S(Sym::Lin(b, o))), Some(Val::C(k))) => {
                    Some(Sym::Lin(b, o.wrapping_sub(k)))
                }
                _ => None,
            },
            Opc::IntAnd => lin(x, y)
                .filter(|&(_, _, k)| k == 0x80)
                .map(|(b, o, _)| Sym::Bit7(b, o)),
            Opc::IntEqual | Opc::IntNotEqual => {
                let eq = op.opc == Opc::IntEqual;
                match (x, y) {
                    (Some(Val::S(Sym::Bit7(b, o))), Some(Val::C(0))) => {
                        let (rel, k) = if eq { (Rel::Lt, 0x80) } else { (Rel::Ge, 0x80) };
                        cond(rel, k, (b, o))
                    }
                    _ => lin(x, y)
                        .and_then(|(b, o, k)| cond(if eq { Rel::Eq } else { Rel::Ne }, k, (b, o))),
                }
            }
            Opc::IntLessEqual => match (x, y) {
                (Some(Val::C(c)), Some(Val::S(Sym::Lin(b, o)))) => cond(Rel::Ge, c, (b, o)),
                (Some(Val::S(Sym::Lin(b, o))), Some(Val::C(c))) if c < 255 => {
                    cond(Rel::Lt, c + 1, (b, o))
                }
                _ => None,
            },
            Opc::IntLess => match (x, y) {
                (Some(Val::S(Sym::Lin(b, o))), Some(Val::C(c))) => cond(Rel::Lt, c, (b, o)),
                (Some(Val::C(c)), Some(Val::S(Sym::Lin(b, o)))) if c < 255 => {
                    cond(Rel::Ge, c + 1, (b, o))
                }
                _ => None,
            },
            _ => None,
        };
        let r = r.filter(|_| op.out.size() == 1);
        match op.out.space() {
            Space::Reg if !op.out.is_none() => {
                let o = op.out.off() as usize;
                reg[o] = r;
                written |= 1 << o;
            }
            Space::Unique if !op.out.is_none() => uniq[op.out.off() as usize] = r,
            _ => {}
        }
    }
    (reg, written, stored)
}

/// Whether `[lo, hi]` shifted by `d` modulo 256, as an interval (full when it straddles the wrap).
pub(crate) fn shift(iv: Iv, d: u8) -> Iv {
    let (lo, hi) = (iv.lo + d as u32, iv.hi + d as u32);
    match (lo > 255, hi > 255) {
        (true, _) => Iv {
            lo: lo - 256,
            hi: hi - 256,
        },
        (false, true) => Iv::full(1),
        _ => Iv { lo, hi },
    }
}

pub(crate) fn meet(a: Iv, b: Iv) -> Option<Iv> {
    let (lo, hi) = (a.lo.max(b.lo), a.hi.min(b.hi));
    (lo <= hi).then_some(Iv { lo, hi })
}

pub(crate) fn hull(a: Iv, b: Iv) -> Iv {
    Iv {
        lo: a.lo.min(b.lo),
        hi: a.hi.max(b.hi),
    }
}

/// The values of the register `p` constrains when its flag is `want`; `None` if none.
fn restrict(p: &Pred, want: bool) -> Option<Iv> {
    let rel = if want { p.rel } else { p.rel.not() };
    let k = p.k as i32;
    let pieces: &[(i32, i32)] = match rel {
        Rel::Eq => &[(k, k), (1, 0)],
        Rel::Ne => &[(0, k - 1), (k + 1, 255)],
        Rel::Ge => &[(k, 255), (1, 0)],
        Rel::Lt => &[(0, k - 1), (1, 0)],
    };
    let mut out: Option<Iv> = None;
    for &(a, b) in pieces.iter().filter(|&&(a, b)| a <= b) {
        let s = (a - p.off as i32).rem_euclid(256);
        let end = s + (b - a);
        let xs = [(s, end.min(255)), (0, end - 256)];
        for &(lo, hi) in xs.iter().filter(|&&(lo, hi)| lo <= hi) {
            let Some(m) = meet(
                Iv {
                    lo: lo as u32,
                    hi: hi as u32,
                },
                p.pre,
            ) else {
                continue;
            };
            let m = shift(m, p.shift);
            out = Some(out.map_or(m, |o| hull(o, m)));
        }
    }
    out
}

/// `st` on the edge where flag register `flag` equals `want`, or `None` if the edge cannot be taken.
pub(crate) fn refine(st: &St, flag: usize, want: bool) -> Option<St> {
    if st.env[flag].value().is_some_and(|v| (v != 0) != want) {
        return None;
    }
    let mut s = st.clone();
    s.env[flag] = Iv::exact(want as u32);
    let pred = FLAGS
        .iter()
        .position(|&f| f == flag)
        .and_then(|i| st.pred[i]);
    if let Some(p) = pred {
        let r = p.reg as usize;
        s.env[r] = restrict(&p, want).and_then(|iv| meet(iv, st.env[r]))?;
    }
    Some(s)
}

/// The image and code cells.
pub(crate) struct Code<'a> {
    pub(crate) img: &'a [u8; 0x10000],
    pub(crate) cells: &'a FixedBitSet,
}

/// `st` after variant `v` at `pc`; its accesses are appended to `acc`.
pub(crate) fn step(code: &Code, pc: u16, v: &Var, st: &St, acc: &mut Vec<Access>) -> St {
    let t = Template::of(v.op);
    let ops = t.ops();
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    let mut env = st.env;
    absint(ops, b, &mut env, acc);
    let mut stored = st.stored;
    for a in acc.iter_mut() {
        a.defd = a
            .ptr
            .is_some_and(|p| (p.hi.lo..=p.hi.hi).all(|i| st.has(i)));
        a.above = a.store
            && a.addr
                .value()
                .is_some_and(|i| i < 0x100 && st.has((i + 1) & 0xff));
        if let (true, false, Some(i @ 0..0x100)) = (a.store, a.back, a.addr.value()) {
            stored[(i >> 7) as usize] |= 1u128 << (i & 127);
        }
    }
    let (sym, written) = symbolic(ops, b, st.env[di_pcode::reg::D].value().map(|d| d as u8));
    let mut pred = [None; 3];
    for (i, &f) in FLAGS.iter().enumerate() {
        pred[i] = if written >> f & 1 == 0 {
            st.pred[i].and_then(|p| {
                let r = p.reg as usize;
                if written >> r & 1 == 0 {
                    return Some(p);
                }
                match sym[r] {
                    Some(Sym::Lin(base, o)) if base as usize == r => Some(Pred {
                        shift: p.shift.wrapping_add(o),
                        ..p
                    }),
                    _ => None,
                }
            })
        } else if let Some(Sym::Cond(base, off, rel, k)) = sym[f] {
            (0..DATA).find_map(|r| match sym[r] {
                Some(Sym::Lin(b2, o)) if b2 == base => Some(Pred {
                    reg: r as u8,
                    off,
                    shift: o,
                    rel,
                    k,
                    pre: if (base as usize) < DATA {
                        st.env[base as usize]
                    } else {
                        Iv::full(1)
                    },
                }),
                _ => None,
            })
        } else {
            None
        };
    }
    let rel = std::array::from_fn(|r| match sym[r] {
        Some(Sym::Lin(b, o)) if (b as usize) < DATA => {
            st.rel[b as usize].map(|(b0, o0)| (b0, o0.wrapping_add(o)))
        }
        _ => None,
    });
    St {
        env,
        pred,
        rel,
        stored,
        from: st.from,
    }
}

/// Program constants and their neighbours: the bounds a widened register stops at.
struct Thresholds {
    up: [u8; 256],
    down: [u8; 256],
}

impl Thresholds {
    /// The sign boundary, the extremes and the compared-with immediates of the program and
    /// their neighbours.
    fn new(vars: &FxHashMap<u16, Vec<Var>>, code: &Code) -> Self {
        let mut marks = FixedBitSet::with_capacity(256);
        marks.extend([0, 0x7f, 0x80, 0xff]);
        for (&pc, vs) in vars {
            if vs.iter().any(|v| matches!(v.op, 0xc9 | 0xe0 | 0xc0)) {
                if let Some(k) = operand_bytes(code.img, code.cells, pc, 2)[0] {
                    marks.extend([
                        k.saturating_sub(1) as usize,
                        k as usize,
                        (k as usize + 1).min(255),
                    ]);
                }
            }
        }
        Thresholds::from_marks(&marks)
    }
    fn from_marks(marks: &FixedBitSet) -> Self {
        let (mut up, mut down) = ([0u8; 256], [0u8; 256]);
        let mut next = 255u8;
        for (x, u) in up.iter_mut().enumerate().rev() {
            if marks.contains(x) {
                next = x as u8;
            }
            *u = next;
        }
        let mut prev = 0u8;
        for (x, d) in down.iter_mut().enumerate() {
            if marks.contains(x) {
                prev = x as u8;
            }
            *d = prev;
        }
        Thresholds { up, down }
    }
    fn widen(&self, old: Iv, new: Iv) -> Iv {
        Iv {
            lo: if new.lo < old.lo {
                self.down[new.lo as usize] as u32
            } else {
                new.lo
            },
            hi: if new.hi > old.hi {
                self.up[new.hi as usize] as u32
            } else {
                new.hi
            },
        }
    }
}

fn join(a: &St, b: &St, widen: Option<&Thresholds>) -> St {
    let w = |old: Iv, new: Iv| widen.map_or(new, |t| t.widen(old, new));
    let mut env = a.env;
    for (e, &y) in env.iter_mut().zip(&b.env) {
        *e = w(*e, hull(*e, y));
    }
    let mut pred = [None; 3];
    for (p, (x, y)) in pred.iter_mut().zip(a.pred.iter().zip(&b.pred)) {
        *p = match (x, y) {
            (Some(x), Some(y))
                if (x.reg, x.off, x.shift, x.rel, x.k) == (y.reg, y.off, y.shift, y.rel, y.k) =>
            {
                Some(Pred {
                    pre: w(x.pre, hull(x.pre, y.pre)),
                    ..*x
                })
            }
            _ => None,
        };
    }
    let rel = std::array::from_fn(|r| if a.rel[r] == b.rel[r] { a.rel[r] } else { None });
    let stored = [a.stored[0] & b.stored[0], a.stored[1] & b.stored[1]];
    St {
        env,
        pred,
        rel,
        stored,
        from: a.from | b.from,
    }
}

/// What a call target's body does: its `RTS`s (reached without passing through nested calls,
/// which are summarized by their return sites), whether the stack height at every one is
/// the entry's and the nested calls' targets.
struct Frame {
    rts: Vec<u16>,
    balanced: bool,
    callees: Vec<u16>,
}

fn frame(vars: &FxHashMap<u16, Vec<Var>>, t: u16) -> Frame {
    let mut f = Frame {
        rts: Vec::new(),
        balanced: true,
        callees: Vec::new(),
    };
    let mut height: FxHashMap<u16, i32> = FxHashMap::default();
    let mut work = vec![(t, 0i32)];
    while let Some((pc, h)) = work.pop() {
        if let Some(&old) = height.get(&pc) {
            f.balanced &= old == h;
            continue;
        }
        height.insert(pc, h);
        for v in &vars[&pc] {
            match &v.flow {
                Flow::Next(n) => match stack_delta(v.op) {
                    Some(d) => work.push((*n, h + d)),
                    None => f.balanced = false,
                },
                Flow::Rts(_) => {
                    f.rts.push(pc);
                    f.balanced &= h == 0;
                }
                Flow::Jsr { t, ret } => {
                    f.callees.extend(t.all());
                    work.push((*ret, h));
                }
                Flow::Rti(_) | Flow::Brk(_) => f.balanced = false,
                fl => work.extend(flow_succs(fl).into_iter().map(|n| (n, h))),
            }
        }
    }
    f
}

/// Where `RTS`s and `RTI`s may return besides their facts' targets.
#[derive(Default)]
struct Returns {
    /// Every return site each one may reach, for loop detection.
    edges: FxHashMap<u16, Vec<u16>>,
    /// Those that may pop more than their callee pushed: they reach every return site.
    wild: FxHashSet<u16>,
    /// The return sites of the calls whose callee reaches an `RTS` with its entry's stack
    /// height: an `RTS` in the context of one returns to that site only.
    covers: FxHashMap<u16, FxHashSet<u16>>,
    all: Vec<u16>,
}

fn returns(vars: &FxHashMap<u16, Vec<Var>>, matched: bool) -> Returns {
    let mut calls: Vec<(u16, u16)> = Vec::new();
    for v in vars.values().flatten() {
        if let Flow::Jsr { t, ret } = &v.flow {
            calls.extend(t.all().iter().map(|&t| (t, *ret)));
        }
    }
    let mut r = Returns {
        all: calls.iter().map(|c| c.1).collect(),
        ..Returns::default()
    };
    r.all.sort_unstable();
    r.all.dedup();
    let frames: FxHashMap<u16, Frame> = calls.iter().map(|&(t, _)| (t, frame(vars, t))).collect();
    let mut ok: FxHashMap<u16, bool> = frames.iter().map(|(&t, f)| (t, f.balanced)).collect();
    while let Some(t) = frames
        .iter()
        .find(|&(t, f)| ok[t] && f.callees.iter().any(|c| !ok[c]))
        .map(|(&t, _)| t)
    {
        ok.insert(t, false);
    }
    for &(t, ret) in &calls {
        for &p in &frames[&t].rts {
            if ok[&t] && matched {
                r.covers.entry(p).or_default().insert(ret);
                r.edges.entry(p).or_default().push(ret);
            } else {
                r.wild.insert(p);
            }
        }
    }
    for (&pc, vs) in vars {
        if vs
            .iter()
            .any(|v| matches!(v.flow, Flow::Rti(_)) || !matched && matches!(v.flow, Flow::Rts(_)))
        {
            r.wild.insert(pc);
        }
    }
    for &p in &r.wild {
        r.edges.insert(p, r.all.clone());
    }
    r
}

/// Targets of depth-first back edges from `roots` over `succ`.
fn loop_heads(roots: &[u16], succ: &dyn Fn(u16) -> Vec<u16>) -> FxHashSet<u16> {
    let (mut heads, mut state) = (FxHashSet::default(), FxHashMap::<u16, bool>::default());
    for &r in roots {
        if state.contains_key(&r) {
            continue;
        }
        state.insert(r, true);
        let mut stack = vec![(r, succ(r), 0usize)];
        while let Some((pc, out, i)) = stack.last_mut() {
            if let Some(&n) = out.get(*i) {
                *i += 1;
                match state.get(&n) {
                    Some(true) => {
                        heads.insert(n);
                    }
                    Some(false) => {}
                    None => {
                        state.insert(n, true);
                        stack.push((n, succ(n), 0));
                    }
                }
            } else {
                state.insert(*pc, false);
                stack.pop();
            }
        }
    }
    heads
}

/// A node of the analysis: an instruction start in the context of the `JSR` return site its
/// function was called from (`NONE`: not in a called function; `ANY`: reached by a return
/// that goes somewhere other than its call's return site, so in no known call).
type Key = (u16, u32);
const NONE: u32 = u32::MAX;
const ANY: u32 = u32::MAX - 1;

/// `st` with nothing known of how its registers relate to a function's entry.
fn unlinked(st: &St) -> St {
    St {
        pred: [None; 3],
        rel: [None; DATA],
        ..st.clone()
    }
}

struct Work<'a> {
    state: FxHashMap<Key, St>,
    queue: VecDeque<Key>,
    queued: FxHashSet<Key>,
    heads: &'a FxHashSet<u16>,
    thr: &'a Thresholds,
    /// Per return site: the states its function's `RTS`s return with and the states of the
    /// `JSR`s that call it, by context.
    ret: FxHashMap<u16, Site>,
}

#[derive(Default)]
struct Site {
    /// Joined states of the `RTS`s returning to the caller, and of those that may return
    /// anywhere.
    out: Option<St>,
    wild: Option<St>,
    calls: FxHashMap<u32, St>,
}

/// The caller's state after a call that left `ret`, given the state `call` it made the call in:
/// a register the callee left at its entry value plus an offset is the caller's plus that.
fn apply(ret: &St, call: &St, linked: bool) -> St {
    let mut s = St {
        pred: [None; 3],
        ..ret.clone()
    };
    if linked {
        s.stored = [
            call.stored[0] | ret.stored[0],
            call.stored[1] | ret.stored[1],
        ];
    }
    for r in 0..DATA {
        s.rel[r] = None;
        if let Some((b, off)) = ret.rel[r] {
            s.env[r] = shift(call.env[b as usize], off);
            s.rel[r] = call.rel[b as usize].map(|(b0, o0)| (b0, o0.wrapping_add(off)));
        }
    }
    s
}

impl Work<'_> {
    /// Merge `st` into the state at `key` (widening at loop heads), queueing it if it grew.
    fn flow(&mut self, key: Key, st: St) {
        let new = match self.state.get(&key) {
            None => st,
            Some(old) => join(old, &st, self.heads.contains(&key.0).then_some(self.thr)),
        };
        if self.state.get(&key) != Some(&new) {
            self.state.insert(key, new);
            if self.queued.insert(key) {
                self.queue.push_back(key);
            }
        }
    }
    /// An `RTS` returns to site `r` with `st`: to the caller, or (unless `matched`) anywhere.
    fn give(&mut self, r: u16, st: &St, matched: bool) {
        let st = &if matched { st.clone() } else { unlinked(st) };
        let site = self.ret.entry(r).or_default();
        let slot = if matched {
            &mut site.out
        } else {
            &mut site.wild
        };
        let new = slot
            .as_ref()
            .map_or_else(|| st.clone(), |o| join(o, st, None));
        if slot.as_ref() != Some(&new) {
            *slot = Some(new);
            for cx in site.calls.keys().copied().collect::<Vec<_>>() {
                self.deliver(r, cx);
            }
        }
    }
    /// A `JSR` in context `cx` in state `st` is followed by site `r`.
    fn caller(&mut self, r: u16, cx: u32, st: &St) {
        let site = self.ret.entry(r).or_default();
        let new = site
            .calls
            .get(&cx)
            .map_or_else(|| st.clone(), |o| join(o, st, None));
        if site.calls.get(&cx) != Some(&new) {
            site.calls.insert(cx, new);
            self.deliver(r, cx);
        }
    }
    /// Flow what returned to site `r` into the context `cx` that called it.
    fn deliver(&mut self, r: u16, cx: u32) {
        let site = &self.ret[&r];
        let res = [(&site.out, true), (&site.wild, false)]
            .into_iter()
            .filter_map(|(p, linked)| p.as_ref().map(|p| apply(p, &site.calls[&cx], linked)))
            .reduce(|a, b| join(&a, &b, None));
        if let Some(res) = res {
            self.flow((r, cx), res);
        }
    }
}

/// Fill each variant's accesses from the interval states at its start; the entries (as in
/// `St::from`) that reach each start, and the starts at which the decimal flag may be set. With `matched`, an
/// `RTS` whose callee leaves the stack as it found it returns to its caller; otherwise any
/// return may reach any return site.
pub fn analyze(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    vars: &mut FxHashMap<u16, Vec<Var>>,
    entries: &[Entry],
    matched: bool,
) -> (
    FxHashMap<u16, u64>,
    FixedBitSet,
    Vec<crate::scev::Induction>,
) {
    let rets = returns(vars, matched);
    let succ_of = |pc: u16| -> Vec<u16> {
        let mut s: Vec<u16> = vars[&pc]
            .iter()
            .flat_map(|v| match &v.flow {
                Flow::Jsr { t, .. } => t.all().to_vec(),
                f => flow_succs(f),
            })
            .collect();
        s.extend(rets.edges.get(&pc).into_iter().flatten());
        s
    };
    let roots: Vec<u16> = entries.iter().map(|e| e.pc).collect();
    let heads = loop_heads(&roots, &succ_of);
    let code = Code { img, cells };
    let thr = Thresholds::new(vars, &code);
    let mut w = Work {
        state: FxHashMap::default(),
        queue: VecDeque::new(),
        queued: FxHashSet::default(),
        heads: &heads,
        thr: &thr,
        ret: FxHashMap::default(),
    };
    for (i, e) in entries.iter().enumerate() {
        let mut env = TOP_ENV;
        for (r, vals) in &e.regs {
            if let (Some(&lo), Some(&hi), true) =
                (vals.iter().min(), vals.iter().max(), *r < env.len())
            {
                env[*r] = Iv {
                    lo: lo as u32,
                    hi: hi as u32,
                };
            }
        }
        w.flow(
            (e.pc, NONE),
            St {
                from: 1 << (i % 64),
                ..St::new(env)
            },
        );
    }
    let mut acc = Vec::new();
    while let Some(key @ (pc, cx)) = w.queue.pop_front() {
        w.queued.remove(&key);
        let st = w.state[&key].clone();
        for v in &vars[&pc] {
            acc.clear();
            let out = step(&code, pc, v, &st, &mut acc);
            match (&v.flow, Template::of(v.op).ctrl) {
                (Flow::Br { t: tg, f }, CtrlKind::Br { flag, pol }) => {
                    let taken = refine(&out, flag as usize, pol != 0);
                    let fall = refine(&out, flag as usize, pol == 0);
                    for (s, e) in tg.all().iter().map(|&x| (x, &taken)).chain([(*f, &fall)]) {
                        if let Some(e) = e {
                            w.flow((s, cx), e.clone());
                        }
                    }
                }
                (Flow::Jsr { t: tg, ret }, _) => {
                    w.caller(*ret, cx, &out);
                    for &s in tg.all() {
                        w.flow(
                            (s, *ret as u32),
                            St {
                                stored: out.stored,
                                from: out.from,
                                ..St::new(out.env)
                            },
                        );
                    }
                }
                (f @ (Flow::Rts(_) | Flow::Rti(_)), _) => {
                    let wild = rets.wild.contains(&pc) || cx == ANY;
                    if wild {
                        for &r in &rets.all {
                            w.give(r, &out, false);
                        }
                    } else if cx != NONE {
                        w.give(cx as u16, &out, true);
                    }
                    let covered = rets.covers.get(&pc).filter(|_| !wild && cx != NONE);
                    let (to, out) = (if cx == NONE { NONE } else { ANY }, unlinked(&out));
                    for s in flow_succs(f) {
                        if !covered.is_some_and(|c| c.contains(&s)) {
                            w.flow((s, to), out.clone());
                        }
                    }
                }
                (f, _) => {
                    let out = if matches!(f, Flow::Brk(_)) {
                        unlinked(&out)
                    } else {
                        out
                    };
                    for s in flow_succs(f) {
                        w.flow((s, cx), out.clone());
                    }
                }
            }
        }
    }
    let mut merged: FxHashMap<u16, St> = FxHashMap::default();
    for (&(pc, _), st) in &w.state {
        let m = merged
            .get(&pc)
            .map_or_else(|| st.clone(), |o| join(o, st, None));
        merged.insert(pc, m);
    }
    let succs: FxHashMap<u16, Vec<u16>> = vars.keys().map(|&pc| (pc, succ_of(pc))).collect();
    let mut dec = FixedBitSet::with_capacity(0x10000);
    let mut fill = |vars: &mut FxHashMap<u16, Vec<Var>>, pc: u16, yb: Option<Iv>| {
        let mut st = merged
            .get(&pc)
            .map_or_else(|| St::new(TOP_ENV), Clone::clone);
        dec.set(pc as usize, st.env[di_pcode::reg::D].hi != 0);
        let y = di_pcode::reg::Y;
        if let Some(b) = yb.and_then(|b| meet(b, st.env[y])) {
            st.env[y] = b;
        }
        for v in vars.get_mut(&pc).unwrap() {
            let mut acc = std::mem::take(&mut v.acc);
            acc.clear();
            step(&code, pc, v, &st, &mut acc);
            v.acc = acc;
        }
    };
    let pcs: Vec<u16> = vars.keys().copied().collect();
    for &pc in &pcs {
        fill(vars, pc, None);
    }
    // Index bounds from the loops' trip counts tighten the accesses they reach.
    let ind = crate::scev::find(&code, vars, &heads, &succs, &merged, &roots);
    for (pc, b) in crate::scev::y_bounds(&ind) {
        fill(vars, pc, Some(b));
    }
    (
        merged.into_iter().map(|(pc, st)| (pc, st.from)).collect(),
        dec,
        ind,
    )
}
