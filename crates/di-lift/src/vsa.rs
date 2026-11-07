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
//! `A`, `X` and `Y` also keep their known bits ([`crate::known`]). For computed jumps
//! ([`crate::dispatch`]) a byte loaded from a constant base plus an index register is tagged
//! with its load while it is in a data register, among the top three stack bytes pushed since
//! the function's entry, or in a tracked byte (the pointer bytes of `JMP (ind)` and `(zp),Y`
//! accesses, the cell operands of jumps) stored at an exact address (so is a constant there);
//! tracked bytes also keep their value intervals, which bound the addresses of the pointer
//! accesses through them. A tag is live while its index register keeps its value; where the
//! register is written, the live tags it indexed become one group (they share an index value)
//! and the group an earlier execution of that write made becomes unrelated.
//!
//! Memory contents are otherwise unknown: a load yields its full range. The result holds for
//! any machine state in which the entries are called with their declared registers, since only
//! lifted control edges are followed and every other one deopts.

use crate::analysis::{
    absint_ranges, flow_succs, operand_bytes, stack_delta, Access, Entry, Env, Flow, Iv, Var,
    TOP_ENV,
};
use crate::ir::fold;
use crate::known::{self, Kb};
use di_pcode::{CtrlKind, Op, Opc, Slot, Space, Template, Vn, UNIQUE_SIZE};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::VecDeque;

/// Flag registers with predicates: C, Z, N.
const FLAGS: [usize; 3] = [8, 9, 14];
/// Registers a predicate can constrain: A, X, Y.
pub(crate) const DATA: usize = 3;

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

/// How the index of a table byte relates to its index register: still the register's value,
/// the value the register held when the instruction at `pc` wrote it (group `pc << 2 | r`;
/// bytes of one group share their index), or unrelated.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Ix {
    Live,
    Grp(u32),
    Lost,
}

/// What a byte holds beyond its interval: a constant; the byte the load of opcode `op` at
/// `pc` read from a constant base plus index register `reg` ([`NOREG`]: none); or (a pushed
/// byte) any value.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Tab {
    K(u8),
    T { pc: u16, op: u8, reg: u8, ix: Ix },
    Any,
}

pub(crate) const NOREG: u8 = u8::MAX;

/// A set of byte values, bit `v` for `v`.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub(crate) struct Bits([u64; 4]);

impl Bits {
    fn add(&mut self, v: u32) {
        self.0[(v >> 6) as usize & 3] |= 1 << (v & 63);
    }
    pub(crate) fn has(&self, v: u32) -> bool {
        self.0[(v >> 6) as usize & 3] >> (v & 63) & 1 == 1
    }
    fn or(self, o: Bits) -> Bits {
        Bits(std::array::from_fn(|i| self.0[i] | o.0[i]))
    }
    fn values(self) -> impl Iterator<Item = u32> {
        (0..256).filter(move |&v| self.has(v))
    }
    /// The values `v + k` (mod 256).
    fn shifted(self, k: u8) -> Bits {
        let mut out = Bits::default();
        self.values().for_each(|v| out.add((v + k as u32) & 0xff));
        out
    }
    fn hull(self) -> Option<Iv> {
        let lo = self.values().next()?;
        let hi = (0..256).rev().find(|&v| self.has(v))?;
        Some(Iv { lo, hi })
    }
    fn filter(self, f: impl Fn(u32) -> bool) -> Bits {
        let mut out = Bits::default();
        self.values().filter(|&v| f(v)).for_each(|v| out.add(v));
        out
    }
}

/// What holds of a byte tagged `a` on one path and `b` on another.
fn join_tab(a: Tab, b: Tab) -> Option<Tab> {
    match (a, b) {
        _ if a == b => Some(a),
        (
            Tab::T { pc, op, reg, .. },
            Tab::T {
                pc: p2,
                op: o2,
                reg: r2,
                ..
            },
        ) if (pc, op, reg) == (p2, o2, r2) => Some(Tab::T {
            pc,
            op,
            reg,
            ix: Ix::Lost,
        }),
        _ => None,
    }
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
    pub(crate) from: u64,
    /// Known bits of `A`, `X` and `Y`.
    pub(crate) kb: [Kb; DATA],
    /// Table bytes `A`, `X` and `Y` hold.
    pub(crate) tag: [Option<Tab>; DATA],
    /// The values `A`, `X` and `Y` may hold beyond their intervals and known bits, where a
    /// load of constant bytes or a branch on one narrowed them ([`reach`]'s pass only).
    pub(crate) set: [Option<Bits>; DATA],
    /// The top three stack bytes, from the top, where pushed since the function's entry.
    pub(crate) push: [Option<Tab>; 3],
    /// The two bytes above the function's entry SP, low first (its `JSR`'s return address
    /// less one), as pulls and pushes past the entry leave them.
    pub(crate) above: [Option<Tab>; 2],
    /// Pushes less pulls since the function's entry, where known.
    pub(crate) depth: Option<i8>,
    /// Tracked bytes ([`Code::vecs`]) whose contents are known, by address: the table byte or
    /// constant they hold, and their interval.
    pub(crate) mem: Vec<(u16, Option<Tab>, Iv)>,
    /// Pointer pairs some instruction since the entry stored, by low byte, and their values
    /// ([`crate::word`], [`reach`]'s pass only).
    pub(crate) wide: Vec<(u16, Option<crate::word::Wv>)>,
    /// What `A`, `X` and `Y`, and tracked bytes, hold of 16-bit quantities.
    pub(crate) half: [Option<crate::word::Half>; DATA],
    pub(crate) mhalf: Vec<(u16, crate::word::Half)>,
    /// What bytes stored through a pointer pair at a known index hold, by pair and index,
    /// while the pair and the bytes stand.
    pub(crate) phalf: crate::word::Phalf,
}

impl St {
    fn new(env: Env) -> St {
        St {
            env,
            pred: [None; 3],
            rel: [Some((0, 0)), Some((1, 0)), Some((2, 0))],
            stored: [0; 2],
            from: 0,
            kb: std::array::from_fn(|r| Kb::of(env[r])),
            tag: [None; DATA],
            set: [None; DATA],
            push: [None; 3],
            above: [None; 2],
            depth: Some(0),
            mem: Vec::new(),
            wide: Vec::new(),
            half: [None; DATA],
            mhalf: Vec::new(),
            phalf: Vec::new(),
        }
    }
    fn has(&self, a: u32) -> bool {
        self.stored[(a >> 7) as usize] >> (a & 127) & 1 == 1
    }
    /// The instruction at `pc` writes data register `r`: bytes indexed by its old value
    /// become a group, the group an earlier execution made becomes unrelated.
    fn detach(&mut self, pc: u16, r: usize) {
        let g = (pc as u32) << 2 | r as u32;
        let f = |t: &mut Tab| {
            if let Tab::T { reg, ix, .. } = t {
                if *ix == Ix::Grp(g) {
                    *ix = Ix::Lost;
                } else if *reg as usize == r && *ix == Ix::Live {
                    *ix = Ix::Grp(g);
                }
            }
        };
        self.tag.iter_mut().flatten().for_each(f);
        self.push.iter_mut().flatten().for_each(f);
        self.mem.iter_mut().flat_map(|e| e.1.as_mut()).for_each(f);
    }
    /// The addresses a zero-page pointer access may reach given the intervals of its tracked
    /// pointer bytes: the pointer's hull plus its index, when that does not wrap.
    fn pointed(&self, p: crate::analysis::Ptr) -> Option<Iv> {
        self.reach(p).filter(|r| r.hi <= 0xffff)
    }
    /// The pointer's hull plus its index as unwrapped addresses (up to `$100FE`).
    pub(crate) fn reach(&self, p: crate::analysis::Ptr) -> Option<Iv> {
        let (q, h) = (p.lo?.value()?, p.hi.value()?);
        let iv = |a: u32| self.mem.iter().find(|e| e.0 as u32 == a).map(|e| e.2);
        let (lo, hi) = (iv(q)?, iv(h)?);
        Some(Iv {
            lo: hi.lo << 8 | lo.lo,
            hi: (hi.hi << 8 | lo.hi) + p.idx,
        })
    }
    /// The values data register `r` may hold.
    /// The value set of data register `r` where kept: its set, else (in [`reach`]'s pass) its
    /// one value.
    fn held(&self, code: &Code, r: usize) -> Option<Bits> {
        self.set[r].or_else(|| {
            let v = self.env[r].value().filter(|_| code.sets())?;
            let mut b = Bits::default();
            b.add(v);
            Some(b)
        })
    }
    pub(crate) fn vals(&self, r: usize) -> Bits {
        self.set[r].unwrap_or_else(|| {
            let mut b = Bits::default();
            self.kb[r].values(self.env[r]).for_each(|v| b.add(v));
            b
        })
    }
    /// What data register `r` holds as a tag: its table byte, else its constant.
    fn val(&self, r: usize) -> Option<Tab> {
        self.tag[r].or_else(|| self.env[r].value().map(|x| Tab::K(x as u8)))
    }
    /// The state a callee starts in after a `JSR` from this one returning to `ret`.
    fn callee(&self, ret: u16) -> St {
        let [lo, hi] = ret.wrapping_sub(1).to_le_bytes();
        St {
            above: [Some(Tab::K(lo)), Some(Tab::K(hi))],
            stored: self.stored,
            from: self.from,
            kb: self.kb,
            tag: self.tag,
            set: self.set,
            mem: self.mem.clone(),
            wide: self.wide.clone(),
            half: self.half,
            mhalf: self.mhalf.clone(),
            phalf: self.phalf.clone(),
            ..St::new(self.env)
        }
    }
}

/// A one-byte value as a symbol: `base + off`, bit 7 of it, or a condition on it.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Sym {
    Lin(u8, u8),
    Bit7(u8, u8),
    Cond(u8, u8, Rel, u8),
    /// A constant.
    K(u8),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Val {
    C(u8),
    S(Sym),
}

/// Symbols of the registers after `ops` (entry `A`, `X`, `Y` are bases 0-2, each load a new
/// base) and the mask of registers they write.
fn symbolic(ops: &[Op], b: [Option<u8>; 2], dec: Option<u8>) -> ([Option<Sym>; 16], u32) {
    let (reg, written, _) = symbolic_in(ops, b, (None, dec, [None; DATA]), None);
    (reg, written)
}

/// The base of the byte at a tracked address in [`symbolic_in`].
pub(crate) const MEM: u8 = u8::MAX;

/// A base symbolic evaluation leaves to its caller (see [`symbolic_in`]).
pub(crate) const VAR: u8 = DATA as u8;

/// [`symbolic`] with the carry and the decimal flag known and the data registers' symbols
/// before the instruction (`known`; `None`: their own bases, [`VAR`] is free for the caller)
/// and, for a tracked address `mem` whose loads are base [`MEM`], the symbol of the last value
/// stored there (`Some(None)`: not a symbol).
pub(crate) fn symbolic_in(
    ops: &[Op],
    b: [Option<u8>; 2],
    known: (Option<u8>, Option<u8>, [Option<Sym>; DATA]),
    mem: Option<u32>,
) -> ([Option<Sym>; 16], u32, Option<Option<Sym>>) {
    let (carry, dec, regs) = known;
    let val = |s: Sym| match s {
        Sym::K(k) => Val::C(k),
        s => Val::S(s),
    };
    let mut stored = None;
    let mut reg = [None; 16];
    for (r, s) in reg.iter_mut().enumerate().take(DATA) {
        *s = Some(regs[r].unwrap_or(Sym::Lin(r as u8, 0)));
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
            Opc::IntAnd | Opc::IntOr if x.is_some() && x == y => match x {
                Some(Val::S(s)) => Some(s),
                _ => None,
            },
            Opc::IntAnd if matches!(y, Some(Val::C(0xff))) => match x {
                Some(Val::S(s)) => Some(s),
                _ => None,
            },
            Opc::IntAnd if matches!(x, Some(Val::C(0xff))) => match y {
                Some(Val::S(s)) => Some(s),
                _ => None,
            },
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

/// Whether register value `v` makes `p`'s flag `want`.
fn sat(p: &Pred, want: bool, v: u32) -> bool {
    let x = (v + 256 - p.shift as u32) & 0xff;
    let y = (x + p.off as u32) & 0xff;
    let k = p.k as u32;
    let rel = match p.rel {
        Rel::Eq => y == k,
        Rel::Ne => y != k,
        Rel::Ge => y >= k,
        Rel::Lt => y < k,
    };
    p.pre.lo <= x && x <= p.pre.hi && rel == want
}

/// Narrow data register `r` of `s` to the values `keep` allows: `None` if none is left.
fn narrow(s: &mut St, r: usize, keep: Bits) -> Option<()> {
    s.env[r] = meet(s.env[r], keep.hull()?)?;
    s.kb[r] = s.kb[r].meet(Kb::of(s.env[r]));
    s.set[r] = Some(keep);
    Some(())
}

/// `st` on the edge where flag register `flag` equals `want`, or `None` if the edge cannot be
/// taken. In [`reach`]'s pass a register's value set is cut by the predicate too, and the index
/// register of a table byte it holds to the indices whose constant byte passes it.
pub(crate) fn refine(code: &Code, st: &St, flag: usize, want: bool) -> Option<St> {
    if st.env[flag].value().is_some_and(|v| (v != 0) != want) {
        return None;
    }
    let mut s = st.clone();
    s.env[flag] = Iv::exact(want as u32);
    if flag == di_pcode::reg::C && code.sets() {
        crate::word::carry(&mut s, want);
    }
    let pred = FLAGS
        .iter()
        .position(|&f| f == flag)
        .and_then(|i| st.pred[i]);
    if let Some(p) = pred {
        let r = p.reg as usize;
        s.env[r] = restrict(&p, want).and_then(|iv| meet(iv, st.env[r]))?;
        if r < DATA {
            s.kb[r] = s.kb[r].meet(Kb::of(s.env[r]));
        }
        if r < DATA && code.sets() {
            narrow(&mut s, r, st.vals(r).filter(|v| sat(&p, want, v)))?;
            if let Some(Tab::T {
                pc,
                op,
                reg: ix,
                ix: Ix::Live,
            }) = st.tag[r].filter(|_| p.shift == 0)
            {
                let ix = ix as usize;
                if let Some(base) = code.base(pc, op, ix).filter(|_| ix < DATA && ix != r) {
                    let keep = st.vals(ix).filter(|x| {
                        code.at(pc, base, x).is_some_and(|a| {
                            code.byte(a, st)
                                .and_then(Iv::value)
                                .is_none_or(|b| sat(&p, want, b))
                        })
                    });
                    narrow(&mut s, ix, keep)?;
                }
            }
        }
    }
    Some(s)
}

/// The image and code cells.
pub(crate) struct Code<'a> {
    pub(crate) img: &'a [u8; 0x10000],
    pub(crate) cells: &'a FixedBitSet,
    /// `PLP` and `RTI` pull the decimal flag clear (guarded where emitted): nothing sets it.
    pub(crate) pull_clear: bool,
    /// The bytes whose contents states track ([`crate::dispatch::vectors`]), ascending.
    pub(crate) vecs: Vec<u16>,
    /// The bytes the facts saw written.
    pub(crate) observed: FixedBitSet,
    /// Loaded bytes taken as constant (no store with an address set writes them).
    pub(crate) constant: FixedBitSet,
    /// Per indexed load, the bytes it reads in [`reach`]'s pass (a constant table's inside
    /// its extent; empty otherwise).
    pub(crate) clip: FxHashMap<u16, Vec<u16>>,
    /// Per byte, the hull of the values it may hold ([`reach`]; empty otherwise).
    pub(crate) bound: Vec<Option<Iv>>,
    /// Per zero-page byte, the hull of the values stores write ([`reach`]; empty otherwise).
    pub(crate) stored: Vec<Option<Iv>>,
    /// The init entry (`init`, as in `St::from`).
    pub(crate) init: u64,
    /// Per pointer pair, the least value it holds at every entry ([`crate::word`]; empty
    /// otherwise).
    pub(crate) floor: FxHashMap<u16, (u32, u32)>,
    /// The runs `[lo, hi)` of data bytes (no byte of an instruction) of the loaded program,
    /// ascending: a pointer whose bytes are read from one points into it ([`reach`]'s pass
    /// that takes table loads inside their extents; empty otherwise).
    pub(crate) runs: Vec<(u32, u32)>,
    /// Word columns: per two table bases `(lo, hi)` whose bytes only stores of the two halves
    /// of one pointer at one index write, the hull of the pointers stored ([`crate::word`]).
    pub(crate) words: FxHashMap<(u16, u16), (u32, u32)>,
    /// Per tracked byte some store writes: the hull of the pointers whose bytes its stores
    /// write ([`crate::word::Half::Data`]), None when one writes a byte of no such pointer.
    pub(crate) ptrs: FxHashMap<u16, Option<(u32, u32)>>,
    /// Per exit edge of a loop that runs to its end, the pairs its iterations write
    /// ([`filled`]): past it they hold what their stores write, their entry bytes no longer.
    pub(crate) exits: FxHashMap<(u16, u16), Vec<crate::word::Filled>>,
    /// Per return site, the address intervals its callee may store into ([`mods`], the reach
    /// pass's).
    pub(crate) mods: FxHashMap<u16, Vec<(u32, u32)>>,
    /// The bytes a store with an address set or of a bounded address writes ([`reach`]'s
    /// pass; empty otherwise): a table of others holds its entry bytes.
    pub(crate) pinned: FixedBitSet,
    /// The address sets the stores they key write ([`Aimed`]).
    pub(crate) aimed: Aimed,
}

/// Per store `(pc, opcode, access index)` the addresses it writes: a store with a set
/// forgets only the tracked bytes the set holds.
pub type Aimed = FxHashMap<(u16, u8, usize), Vec<u16>>;

impl Code<'_> {
    /// The hull of the bytes a load from `a` reads when every one is constant: a table no
    /// lifted store with an address set writes (guarded like one read for a jump).
    pub(crate) fn konst(&self, a: Iv) -> Option<Iv> {
        let range = a.lo as usize..a.hi as usize + 1;
        let constant = a.hi - a.lo < 0x100
            && a.hi < 0x10000
            && range
                .clone()
                .all(|i| self.constant.contains(i) && !self.cells.contains(i));
        constant.then(|| {
            let bytes = self.img[range].iter().map(|&b| b as u32);
            Iv {
                lo: bytes.clone().min().unwrap(),
                hi: bytes.max().unwrap(),
            }
        })
    }
    /// The run of data bytes ([`Code::runs`]) holding byte `a`.
    pub(crate) fn run_of(&self, a: u32) -> Option<(u32, u32)> {
        let i = self.runs.partition_point(|r| r.1 <= a);
        self.runs.get(i).copied().filter(|r| r.0 <= a)
    }
    /// Whether data registers keep value sets ([`reach`]'s pass).
    fn sets(&self) -> bool {
        !self.bound.is_empty()
    }
    /// The interval of byte `x` a load reads beyond the tracked bytes: its constant, else its
    /// bound (of the stored values alone for a zero-page byte every path since the entry
    /// stored).
    pub(crate) fn byte(&self, x: u32, st: &St) -> Option<Iv> {
        let k = self.constant.contains(x as usize) && !self.cells.contains(x as usize);
        if k {
            Some(Iv::exact(self.img[x as usize] as u32))
        } else if x < 0x100 && st.has(x) && !self.stored.is_empty() {
            self.stored[x as usize]
        } else {
            self.bound.get(x as usize).copied().flatten()
        }
    }
    /// The operand of the table load of opcode `op` at `pc` indexed by register `r`, and
    /// whether it indexes zero page.
    fn base(&self, pc: u16, op: u8, r: usize) -> Option<(u32, bool)> {
        let a = crate::dispatch::load_addr(self, pc, op, r as u8, 0)?;
        Some((a as u32, Template::of(op).len == 2))
    }
    /// The address the table load at `pc` from `base` reads with index `x`: None outside the
    /// bytes `clip` lets it read.
    fn at(&self, pc: u16, base: (u32, bool), x: u32) -> Option<u32> {
        let a = (base.0 + x) & if base.1 { 0xff } else { 0xffff };
        match self.clip.get(&pc) {
            Some(c) if c.binary_search(&(a as u16)).is_err() => None,
            _ => Some(a),
        }
    }
    /// The values the load of opcode `op` at `pc` reads in state `st` when it reads only
    /// constant bytes from its operand plus its index register's values.
    fn load_set(&self, pc: u16, op: u8, st: &St) -> Option<Bits> {
        let r = crate::dispatch::index_reg(Template::of(op));
        let base = self.base(pc, op, r.unwrap_or(NOREG as usize))?;
        let xs = r.map_or(Bits([1, 0, 0, 0]), |r| st.vals(r));
        let mut out = Bits::default();
        for x in xs.values() {
            if let Some(a) = self.at(pc, base, x) {
                out.add(self.byte(a, st)?.value()?);
            }
        }
        out.hull().map(|_| out)
    }
    /// What the load of opcode `op` at `pc` from `a` in state `st` yields beyond the tracked
    /// bytes: the hull of constant bytes ([`Code::konst`]), else of the bytes' bounds
    /// ([`Code::byte`]); where `clip` lists the bytes an indexed load at `pc` reads, the hull
    /// of those in `a`; from a constant base plus an index register, of the bytes its values
    /// address.
    pub(crate) fn load(&self, pc: u16, op: u8, a: Iv, st: &St) -> Option<Iv> {
        let byte = |x: u32| self.byte(x, st);
        let r = crate::dispatch::index_reg(Template::of(op)).filter(|_| self.sets());
        if let Some((r, base)) = r.and_then(|r| Some((r, self.base(pc, op, r)?))) {
            let xs = (st.vals(r).values())
                .filter_map(|x| self.at(pc, base, x))
                .filter(|&x| a.meets(x, x + 1));
            return xs.map(byte).reduce(|p, q| Some(hull(p?, q?))).flatten();
        }
        match self.clip.get(&pc) {
            Some(xs) => xs
                .iter()
                .map(|&x| x as u32)
                .filter(|&x| a.meets(x, x + 1))
                .map(byte)
                .reduce(|p, q| Some(hull(p?, q?)))
                .flatten(),
            None => self.konst(a).or_else(|| {
                let ok = !self.bound.is_empty() && a.hi - a.lo < 0x100 && a.hi < 0x10000;
                (a.lo..=a.hi)
                    .filter(|_| ok)
                    .map(byte)
                    .reduce(|p, q| Some(hull(p?, q?)))
                    .flatten()
            }),
        }
    }
}

/// Whether the decimal flag can only be set by a pull from the stack: no `SED` among the
/// variants and no entry that allows it set. `PLP` and `RTI` are then guarded to pull it clear.
pub fn pull_clear(vars: &FxHashMap<u16, Vec<Var>>, entries: &[Entry]) -> bool {
    let d = di_pcode::reg::D;
    !vars.values().flatten().any(|v| v.op == 0xf8)
        && entries.iter().all(|e| {
            e.regs
                .iter()
                .all(|(r, vals)| *r != d || vals.iter().all(|&x| x == 0))
        })
}

/// `st` after variant `v` at `pc`; its accesses are appended to `acc`.
pub(crate) fn step(code: &Code, pc: u16, v: &Var, st: &St, acc: &mut Vec<Access>) -> St {
    let t = Template::of(v.op);
    let ops = t.ops();
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    let mut env = st.env;
    let held = |a: u32| st.mem.iter().find(|e| e.0 as u32 == a).map(|e| e.2);
    let bi: [Iv; 2] = std::array::from_fn(|i| match b[i] {
        Some(x) => Iv::exact(x as u32),
        None => held(pc.wrapping_add(i as u16 + 1) as u32).unwrap_or(Iv::full(1)),
    });
    let load = |a: Iv| {
        a.value()
            .and_then(held)
            .or_else(|| code.load(pc, v.op, a, st))
    };
    absint_ranges(ops, b, bi, &mut env, acc, &load);
    for a in acc.iter_mut() {
        if let Some(iv) = a.ptr.and_then(|p| st.pointed(p)) {
            if let Some(m) = meet(a.addr, iv) {
                a.addr = m;
            }
        }
    }
    // A store through a cell operand pair reaches the pair's values plus its index; none
    // while the pair holds none.
    let q = pc.wrapping_add(1);
    let through = code.sets() && t.len == 3 && b.iter().any(Option::is_none);
    let through = through && acc.iter().any(|a| a.store) && crate::word::pair(code, q);
    let mut vacant = false;
    if through {
        let idx = crate::dispatch::index_reg(t).map_or(Iv::exact(0), |r| st.env[r]);
        match crate::word::reach(code, st, q, idx) {
            Some(iv) if iv.hi <= 0xffff => {
                for a in acc
                    .iter_mut()
                    .filter(|a| a.store && !a.dummy && a.ptr.is_none())
                {
                    a.addr = meet(a.addr, iv).unwrap_or(a.addr);
                }
            }
            Some(_) => {}
            None => vacant = true,
        }
    }
    if code.pull_clear && matches!(v.op, 0x28 | 0x40) {
        env[di_pcode::reg::D] = Iv::exact(0);
    }
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
    let kb = known::eval(ops, b, &st.env, &st.kb);
    let mut s = St {
        env,
        pred,
        rel,
        stored,
        from: st.from,
        kb: std::array::from_fn(|r| kb[r].unwrap_or(st.kb[r]).meet(Kb::of(env[r]))),
        tag: st.tag,
        set: st.set,
        push: st.push,
        above: st.above,
        depth: st.depth,
        mem: st.mem.clone(),
        wide: st.wide.clone(),
        half: st.half,
        mhalf: st.mhalf.clone(),
        phalf: st.phalf.clone(),
    };
    // Pushes and pulls past the entry: a pull at depth `d <= 0` reads the byte `-d` above it,
    // a push at `d < 0` writes the byte `-d - 1`.
    let mut pulled = None;
    match (v.op, st.depth) {
        (0x48 | 0x08, Some(d)) if d < 0 => {
            let top = if v.op == 0x48 { st.val(0) } else { None };
            s.above[(-d - 1) as usize] = top.or(Some(Tab::Any));
        }
        (0x68 | 0x28, Some(d)) if d <= 0 => pulled = st.above.get((-d) as usize).copied().flatten(),
        _ => {}
    }
    if let (0x68, Some(Tab::K(x))) = (v.op, pulled) {
        s.env[0] = Iv::exact(x as u32);
        s.kb[0] = Kb::of(s.env[0]);
    }
    s.depth = match stack_delta(v.op) {
        Some(x) if matches!(v.flow, Flow::Next(_)) => st.depth.and_then(|d| {
            let n = d - x as i8;
            (-2..=i8::MAX - 1).contains(&n).then_some(n)
        }),
        _ if matches!(v.flow, Flow::Next(_)) => None,
        _ => st.depth,
    };
    match (v.op, &v.flow) {
        (0x48 | 0x08, _) if st.depth.is_some_and(|d| d < 0) => {}
        (0x68 | 0x28, _) if pulled.is_some() => {}
        (0x48 | 0x08, _) => {
            let top = if v.op == 0x48 { st.val(0) } else { None };
            s.push = [top.or(Some(Tab::Any)), st.push[0], st.push[1]];
        }
        (0x68 | 0x28, _) => s.push = [st.push[1], st.push[2], None],
        (op, Flow::Next(_))
            if stack_delta(op).is_none()
                || acc.iter().any(|a| a.store && a.addr.meets(0x100, 0x200)) =>
        {
            (s.push, s.above) = ([None; 3], [None; 2])
        }
        _ => {}
    }
    for (ai, a) in acc.iter().enumerate().filter(|(_, a)| a.store && !vacant) {
        // A pointer store whose sum may wrap reaches the low addresses it wraps to; one with
        // an address set, only those.
        let reach = a.ptr.and_then(|p| st.reach(p));
        let aimed = code.aimed.get(&(pc, v.op, ai));
        let hit = |m: u16| match (aimed, reach) {
            (Some(s), _) => s.binary_search(&m).is_ok(),
            (None, Some(r)) => {
                let m = m as u32;
                (r.lo..=r.hi).contains(&m) || (r.lo..=r.hi).contains(&(m + 0x10000))
            }
            (None, None) => a.addr.meets(m as u32, m as u32 + 1),
        };
        let tracked = |m: u16| match aimed {
            Some(s) => s.binary_search(&m).is_ok(),
            None => a.addr.meets(m as u32, m as u32 + 1),
        };
        if !code.vecs.iter().any(|&m| tracked(m)) {
            continue;
        }
        let old = st
            .mem
            .iter()
            .find(|e| Some(e.0 as u32) == a.addr.value())
            .copied();
        s.mem.retain(|e| !hit(e.0));
        let Some(m) = a.addr.value() else { continue };
        let (_, _, held) = symbolic_in(ops, b, (None, None, [None; DATA]), Some(m));
        let (t, iv) = match held {
            Some(Some(Sym::Lin(r, 0))) if (r as usize) < DATA => {
                (st.val(r as usize), st.env[r as usize])
            }
            Some(Some(Sym::K(k))) => (Some(Tab::K(k)), Iv::exact(k as u32)),
            Some(Some(Sym::Lin(MEM, k))) => (None, old.map_or(Iv::full(1), |e| shift(e.2, k))),
            _ => (a.val.value().map(|x| Tab::K(x as u8)), a.val),
        };
        if t.is_some() || iv != Iv::full(1) {
            s.mem.push((m as u16, t, iv));
            s.mem.sort_unstable_by_key(|e| e.0);
        }
    }
    if code.sets() {
        crate::word::step(
            code, pc, v.op, ops, b, st, &mut s, acc, &sym, written, vacant,
        );
    }
    for r in (0..DATA).filter(|&r| written >> r & 1 == 1) {
        s.tag[r] = match sym[r] {
            Some(Sym::Lin(b0, 0)) if (b0 as usize) < DATA => st.tag[b0 as usize],
            Some(Sym::Lin(b0, 0)) if b0 >= DATA as u8 && b0 != MEM => match v.op {
                0x68 => pulled.or(st.push[0]),
                op => table(code, pc, op),
            },
            _ => None,
        };
    }
    for r in (0..DATA).filter(|&r| written >> r & 1 == 1) {
        s.set[r] = match sym[r] {
            _ if !code.sets() => None,
            Some(Sym::Lin(b0, k)) if (b0 as usize) < DATA => {
                st.set[b0 as usize].map(|x| x.shifted(k))
            }
            Some(Sym::Lin(b0, 0)) if b0 >= DATA as u8 && b0 != MEM && v.op != 0x68 => {
                code.load_set(pc, v.op, st)
            }
            _ => None,
        };
        if let Some(x) = s.set[r] {
            if narrow(&mut s, r, x).is_none() {
                s.set[r] = None;
            }
        }
    }
    for r in (0..DATA).filter(|&r| written >> r & 1 == 1) {
        s.detach(pc, r);
    }
    s
}

/// The tag of the byte the instruction `op` at `pc` loads when its address is its operand
/// plus at most one index register (no memory pointer, no cell operand).
pub(crate) fn table(code: &Code, pc: u16, op: u8) -> Option<Tab> {
    let reg = crate::dispatch::index_reg(Template::of(op)).map_or(NOREG, |r| r as u8);
    crate::dispatch::load_addr(code, pc, op, reg, 0)?;
    Some(Tab::T {
        pc,
        op,
        reg,
        ix: Ix::Live,
    })
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

/// The hull of `a` and `b`, widened when `widen` is given.
fn join(code: &Code, a: &St, b: &St, widen: Option<&Thresholds>) -> St {
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
    let tab = |x: Option<Tab>, y: Option<Tab>| join_tab(x?, y?);
    let (half, mhalf, phalf) = crate::word::join_halves(a, b);
    let wide = crate::word::join(code, a, b, widen.is_some());
    St {
        wide,
        half,
        mhalf,
        phalf,
        env,
        pred,
        rel,
        stored,
        from: a.from | b.from,
        kb: std::array::from_fn(|r| {
            let k = |s: &St| s.kb[r].meet(Kb::of(s.env[r]));
            k(a).join(k(b))
        }),
        tag: std::array::from_fn(|r| tab(a.tag[r], b.tag[r])),
        set: std::array::from_fn(|r| Some(a.held(code, r)?.or(b.held(code, r)?))),
        push: std::array::from_fn(|i| match (a.push[i], b.push[i]) {
            (Some(x), Some(y)) => Some(join_tab(x, y).unwrap_or(Tab::Any)),
            _ => None,
        }),
        above: std::array::from_fn(|i| tab(a.above[i], b.above[i])),
        depth: if a.depth == b.depth { a.depth } else { None },
        mem: a
            .mem
            .iter()
            .filter_map(|&(m, x, i)| {
                let (_, y, j) = *b.mem.iter().find(|e| e.0 == m)?;
                let t = x.zip(y).and_then(|(x, y)| join_tab(x, y));
                let iv = w(i, hull(i, j));
                (t.is_some() || iv != Iv::full(1)).then_some((m, t, iv))
            })
            .collect(),
    }
}

/// What a call target's body does: its `RTS`s (reached without passing through nested calls,
/// which are summarized by their return sites), whether the stack height at every one is
/// the entry's and the nested calls' targets. A `BRK` calls its handlers, whose `RTI` at
/// their entry height returns past its signature byte; `rti` analyses such a handler, its
/// `RTI`s its returns.
struct Frame {
    rts: Vec<u16>,
    balanced: bool,
    callees: Vec<u16>,
}

fn frame(vars: &FxHashMap<u16, Vec<Var>>, t: u16, disp: &FxHashSet<u16>, rti: bool) -> Frame {
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
                Flow::Rti(_) if rti && h == 0 => f.rts.push(pc),
                Flow::Rts(ts) if disp.contains(&pc) && h != 0 => {
                    work.extend(ts.iter().map(|&n| (n, h + 2)))
                }
                Flow::Rti(ts) if disp.contains(&pc) => work.extend(ts.iter().map(|&n| (n, h + 3))),
                Flow::Rts(_) if rti => f.balanced = false,
                Flow::Rts(_) => {
                    f.rts.push(pc);
                    f.balanced &= h == 0;
                }
                Flow::Jsr { t, ret } => {
                    f.callees.extend(t.all());
                    work.push((*ret, h));
                }
                Flow::Brk(ts)
                    if !rti && !ts.is_empty() && vars.contains_key(&pc.wrapping_add(2)) =>
                {
                    for &t in ts {
                        let g = frame(vars, t, disp, true);
                        f.balanced &= g.balanced && !g.rts.is_empty();
                        f.callees.extend(g.callees);
                    }
                    work.push((pc.wrapping_add(2), h));
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

fn returns(vars: &FxHashMap<u16, Vec<Var>>, matched: bool, disp: &FxHashSet<u16>) -> Returns {
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
    let frames: FxHashMap<u16, Frame> = calls
        .iter()
        .map(|&(t, _)| (t, frame(vars, t, disp, false)))
        .collect();
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
    for (&pc, vs) in vars.iter().filter(|e| !disp.contains(e.0)) {
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
pub(crate) type Key = (u16, u32);
const NONE: u32 = u32::MAX;
const ANY: u32 = u32::MAX - 1;

/// `st` with nothing known of how its registers relate to a function's entry.
fn unlinked(st: &St) -> St {
    St {
        pred: [None; 3],
        rel: [None; DATA],
        push: [None; 3],
        above: [None; 2],
        depth: None,
        ..st.clone()
    }
}

struct Work<'a> {
    code: &'a Code<'a>,
    state: FxHashMap<Key, St>,
    queue: VecDeque<Key>,
    queued: FxHashSet<Key>,
    heads: &'a FxHashSet<u16>,
    thr: &'a Thresholds,
    /// Per return site: the states its function's `RTS`s return with and the states of the
    /// `JSR`s that call it, by context.
    ret: FxHashMap<u16, Site>,
    /// Per return site, the addresses its callee may store into, where they are bounded
    /// ([`mods`]).
    mods: FxHashMap<u16, Vec<(u32, u32)>>,
}

#[derive(Default)]
struct Site {
    /// Joined states of the `RTS`s returning to the caller, and of those that may return
    /// anywhere.
    out: Option<St>,
    wild: Option<St>,
    calls: FxHashMap<u32, St>,
    /// Joined states of the `RTS`s returning through the return address advanced past the
    /// call (inline arguments), by the address they return to.
    past: FxHashMap<u16, St>,
}

/// The caller's state after a call that left `ret`, given the state `call` it made the call in:
/// a register the callee left at its entry value plus an offset is the caller's plus that. A
/// flag's predicate on a register holds of the value the callee returns it with.
fn apply(ret: &St, call: &St, linked: bool, kept: Option<&[(u32, u32)]>) -> St {
    let mut s = ret.clone();
    if linked {
        s.stored = [
            call.stored[0] | ret.stored[0],
            call.stored[1] | ret.stored[1],
        ];
        (s.push, s.above, s.depth) = (call.push, call.above, call.depth);
        // The tracked bytes the callee stores into none of keep what they held at the call.
        if let Some(m) = kept {
            let free = |x: u16| !m.iter().any(|&(lo, hi)| (lo..=hi).contains(&(x as u32)));
            let pair = |q: u16| free(q) && free(q.wrapping_add(1));
            s.mem.retain(|e| !free(e.0));
            s.mem.extend(call.mem.iter().filter(|e| free(e.0)));
            s.mem.sort_unstable_by_key(|e| e.0);
            s.mhalf.retain(|e| !free(e.0));
            s.mhalf.extend(call.mhalf.iter().filter(|e| free(e.0)));
            s.mhalf.sort_unstable_by_key(|e| e.0);
            s.wide.retain(|e| !pair(e.0));
            s.wide.extend(call.wide.iter().filter(|e| pair(e.0)));
            s.wide.sort_unstable_by_key(|e| e.0);
        }
    } else {
        (s.push, s.above, s.depth) = ([None; 3], [None; 2], None);
    }
    for r in 0..DATA {
        s.rel[r] = None;
        if let Some((b, off)) = ret.rel[r] {
            s.env[r] = shift(call.env[b as usize], off);
            s.kb[r] = s.kb[r].meet(Kb::of(s.env[r]));
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
            Some(old) => join(
                self.code,
                old,
                &st,
                self.heads.contains(&key.0).then_some(self.thr),
            ),
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
            .map_or_else(|| st.clone(), |o| join(self.code, o, st, None));
        if slot.as_ref() != Some(&new) {
            *slot = Some(new);
            for cx in site.calls.keys().copied().collect::<Vec<_>>() {
                self.deliver(r, cx);
            }
        }
    }
    /// An `RTS` of the call returning to site `r` returns to `t` instead, with `st`.
    fn give_past(&mut self, r: u16, t: u16, st: &St) {
        let site = self.ret.entry(r).or_default();
        let new = site
            .past
            .get(&t)
            .map_or_else(|| st.clone(), |o| join(self.code, o, st, None));
        if site.past.get(&t) != Some(&new) {
            site.past.insert(t, new);
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
            .map_or_else(|| st.clone(), |o| join(self.code, o, st, None));
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
            .filter_map(|(p, linked)| {
                p.as_ref().map(|p| {
                    apply(
                        p,
                        &site.calls[&cx],
                        linked,
                        self.mods.get(&r).map(Vec::as_slice),
                    )
                })
            })
            .reduce(|a, b| join(self.code, &a, &b, None));
        let past: Vec<(u16, St)> = site
            .past
            .iter()
            .map(|(&t, p)| (t, apply(p, &site.calls[&cx], true, None)))
            .collect();
        if let Some(res) = res {
            self.flow((r, cx), res);
        }
        for (t, st) in past {
            self.flow((t, cx), st);
        }
    }
}

/// What [`analyze`] finds.
pub struct Analysis {
    /// The entries (as in `St::from`) that reach each start.
    pub from: FxHashMap<u16, u64>,
    /// The starts at which the decimal flag may be set.
    pub dec: FixedBitSet,
    pub ind: Vec<crate::scev::Induction>,
    /// The targets of computed transfers found from the code ([`crate::dispatch`]).
    pub jumps: FxHashMap<u16, crate::dispatch::Jump>,
    /// The jumps among them that select their target by an index register's value.
    pub sels: FxHashMap<u16, crate::dispatch::Selector>,
    /// Every store's addresses and values.
    pub stores: Vec<crate::dispatch::Store>,
    /// Index guards the resolved transfers and stored table bytes need.
    pub guards: Vec<crate::dispatch::Guard>,
    /// The table bytes stored values, pointer stores' addresses and trip bounds were read
    /// from.
    pub reads: std::collections::BTreeSet<u16>,
    /// Per return site, the addresses past it a call's `RTS` returns to (inline arguments).
    pub past: FxHashMap<u16, Vec<u16>>,
    /// The values of each byte some store writes ([`crate::dispatch::cell_values`]).
    pub cellvals: FxHashMap<u16, Option<std::collections::BTreeSet<u8>>>,
    /// Address sets of loads through pointers, by `(pc, opcode, access index)`.
    pub loads: FxHashMap<(u16, u8, usize), Vec<u16>>,
    /// Address hulls of loads through pointers and cell operands, and of their bases (the
    /// index registers zero) ([`reach`]), while table loads stay inside their extents and
    /// on every run.
    pub reach: Hulls,
    pub proven: Hulls,
    /// Per instruction start the values `A`, `X` and `Y` hold on every run ([`reach`]'s proof
    /// pass).
    pub regs: Regs,
    /// Stores whose index the code does not bound, by `(pc, opcode, access index)`: the run
    /// `[lo, hi)` of data bytes they write ([`bounded_stores`]).
    pub bounded: FxHashMap<(u16, u8, usize), (u32, u32)>,
    /// Every start's successors, a return's being its calls' return sites.
    pub succs: FxHashMap<u16, Vec<u16>>,
}

/// What the fixpoint walks besides the states: the returns, the roots, the loop heads, the
/// return sites of `BRK`s and every start's successors.
struct Shape {
    rets: Returns,
    roots: Vec<u16>,
    heads: FxHashSet<u16>,
    brk_rets: FxHashSet<u16>,
    succs: FxHashMap<u16, Vec<u16>>,
}

fn shape(
    vars: &FxHashMap<u16, Vec<Var>>,
    entries: &[Entry],
    matched: bool,
    disp: &FxHashSet<u16>,
) -> Shape {
    let rets = returns(vars, matched, disp);
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
    let brk_rets: FxHashSet<u16> = vars
        .iter()
        .filter(|(_, vs)| vs.iter().any(|v| matches!(v.flow, Flow::Brk(_))))
        .map(|(&pc, _)| pc.wrapping_add(2))
        .collect();
    let succs = vars.keys().map(|&pc| (pc, succ_of(pc))).collect();
    Shape {
        rets,
        roots,
        heads,
        brk_rets,
        succs,
    }
}

/// Per return site of a `JSR`, the address intervals the instructions its callee runs until
/// it returns (its nested calls' included) may store into (each store's from `at` where it
/// has them, else from its access): none for a callee that runs a `BRK` or an `RTI`.
fn mods(
    vars: &FxHashMap<u16, Vec<Var>>,
    at: &FxHashMap<u16, Vec<(u32, u32)>>,
) -> FxHashMap<u16, Vec<(u32, u32)>> {
    let mut memo: FxHashMap<u16, Option<Vec<(u32, u32)>>> = FxHashMap::default();
    let mut out = FxHashMap::default();
    for v in vars.values().flatten() {
        let Flow::Jsr {
            t: crate::analysis::Tg::At(t),
            ret,
        } = v.flow
        else {
            continue;
        };
        let m = memo.entry(t).or_insert_with(|| {
            let mut seen = FxHashSet::default();
            let (mut work, mut ivs) = (vec![t], Vec::new());
            while let Some(pc) = work.pop() {
                if !seen.insert(pc) {
                    continue;
                }
                let own = || {
                    let accs = vars[&pc].iter().flat_map(|v| &v.acc);
                    accs.filter(|a| a.store && !a.dummy)
                        .map(|a| (a.addr.lo, a.addr.hi))
                        .collect::<Vec<_>>()
                };
                ivs.extend(at.get(&pc).cloned().unwrap_or_else(own));
                for v in vars.get(&pc)? {
                    match &v.flow {
                        Flow::Rts(_) => {}
                        Flow::Brk(_) | Flow::Rti(_) => return None,
                        f => work.extend(flow_succs(f)),
                    }
                }
            }
            ivs.sort_unstable();
            Some(ivs)
        });
        if let Some(m) = m {
            out.insert(ret, m.clone());
        }
    }
    out
}

/// Per start and call context, the state on every path reaching it; per return site, what
/// its calls and returns carry.
fn fixpoint(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    entries: &[Entry],
    sh: &Shape,
    disp: &FxHashSet<u16>,
    thr: &Thresholds,
) -> (FxHashMap<Key, St>, FxHashMap<u16, Site>) {
    let (rets, heads, brk_rets) = (&sh.rets, &sh.heads, &sh.brk_rets);
    let mut w = Work {
        code,
        state: FxHashMap::default(),
        queue: VecDeque::new(),
        queued: FxHashSet::default(),
        heads,
        thr,
        ret: FxHashMap::default(),
        mods: code.mods.clone(),
    };
    for (i, e) in entries.iter().enumerate() {
        let env = e.env();
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
            let out = step(code, pc, v, &st, &mut acc);
            match (&v.flow, Template::of(v.op).ctrl) {
                (Flow::Br { t: tg, f }, CtrlKind::Br { flag, pol }) => {
                    let taken = refine(code, &out, flag as usize, pol != 0);
                    let fall = refine(code, &out, flag as usize, pol == 0);
                    for (s, e) in tg.all().iter().map(|&x| (x, &taken)).chain([(*f, &fall)]) {
                        if let Some(e) = e {
                            let mut e = e.clone();
                            if let Some(qs) = code.exits.get(&(pc, s)) {
                                crate::word::fill(code, &mut e, qs);
                            }
                            w.flow((s, cx), e);
                        }
                    }
                }
                (Flow::Jsr { t: tg, ret }, _) => {
                    w.caller(*ret, cx, &out);
                    for &s in tg.all() {
                        w.flow((s, *ret as u32), out.callee(*ret));
                    }
                }
                // An `RTS` through its call's return address advanced past the call returns.
                (Flow::Rts(ts), _) if disp.contains(&pc) && st.depth == Some(0) && cx < ANY => {
                    // The address it pulls, where its bytes are constants.
                    let own = match st.above {
                        [Some(Tab::K(l)), Some(Tab::K(h))] => {
                            Some(u16::from_le_bytes([l, h]).wrapping_add(1))
                        }
                        _ => None,
                    };
                    for &t in ts.iter().filter(|&&t| own.is_none_or(|o| o == t)) {
                        w.give_past(cx as u16, t, &out);
                    }
                }
                (Flow::Rti(ts), _)
                    if disp.contains(&pc) && cx < ANY && brk_rets.contains(&(cx as u16)) =>
                {
                    for &t in ts.iter().filter(|&&t| t as u32 == cx) {
                        w.give_past(cx as u16, t, &out);
                    }
                }
                (Flow::Rts(ts) | Flow::Rti(ts), _) if disp.contains(&pc) => {
                    let mut o = out;
                    for _ in 0..if matches!(v.flow, Flow::Rts(_)) { 2 } else { 3 } {
                        o.push = [o.push[1], o.push[2], None];
                    }
                    for &s in ts {
                        w.flow((s, cx), o.clone());
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
                // `BRK` calls its handler in the context of its address plus two, which the
                // handler's `RTI` returns to through the bytes `BRK` pushed.
                (Flow::Brk(ts), _) => {
                    let ret = pc.wrapping_add(2);
                    if vars.contains_key(&ret) {
                        w.caller(ret, cx, &out);
                    }
                    let [lo, hi] = ret.to_le_bytes();
                    let h = St {
                        push: [Some(Tab::Any), Some(Tab::K(lo)), Some(Tab::K(hi))],
                        ..unlinked(&out)
                    };
                    for &t in ts {
                        w.flow((t, ret as u32), h.clone());
                    }
                }
                (f, _) => {
                    for s in flow_succs(f) {
                        w.flow((s, cx), out.clone());
                    }
                }
            }
        }
    }
    (w.state, w.ret)
}

/// Each start's states joined over its contexts.
fn merge(code: &Code, state: &FxHashMap<Key, St>) -> FxHashMap<u16, St> {
    let mut merged: FxHashMap<u16, St> = FxHashMap::default();
    for (&(pc, _), st) in state {
        let m = merged
            .get(&pc)
            .map_or_else(|| st.clone(), |o| join(code, o, st, None));
        merged.insert(pc, m);
    }
    merged
}

/// What a fixpoint over the program needs, and each start's state from the joined ones
/// with the loops' index bounds.
struct Pass<'a> {
    code: &'a Code<'a>,
    vars: &'a FxHashMap<u16, Vec<Var>>,
    entries: &'a [Entry],
    sh: &'a Shape,
    disp: &'a FxHashSet<u16>,
    thr: &'a Thresholds,
    /// The loops' bounds of `X` and `Y` per start.
    xy: [&'a FxHashMap<u16, Iv>; 2],
    /// The bytes of the instructions.
    insn: &'a FixedBitSet,
}

impl Pass<'_> {
    /// The state at `pc` of the joined states `m`, cut by the loops' index bounds.
    fn at(&self, m: &FxHashMap<u16, St>, pc: u16) -> Option<St> {
        let mut st = m.get(&pc)?.clone();
        for (r, b) in [di_pcode::reg::X, di_pcode::reg::Y]
            .into_iter()
            .zip(self.xy)
        {
            if let Some(x) = b.get(&pc).and_then(|&b| meet(b, st.env[r])) {
                st.env[r] = x;
            }
        }
        Some(st)
    }
}

/// Address hulls of loads and of their bases by `(pc, opcode, access index)` ([`reach`]).
pub type Hulls = FxHashMap<(u16, u8, usize), (Iv, Iv)>;

/// Per instruction start the value sets of `A`, `X` and `Y`, bit `v` for `v`.
pub type Regs = FxHashMap<u16, [[u64; 4]; DATA]>;

/// An indexed load of [`reach`]'s pass: the bytes its index set reaches and those inside its
/// table's extent; `free` when the code does not bound its index.
struct Indexed {
    pc: u16,
    free: bool,
    all: Vec<u16>,
    ext: Vec<u16>,
}

/// Whether every byte of `bytes` holds its entry byte alone (no store writes it).
fn unwritten(img: &[u8; 0x10000], bound: &[Option<Iv>], bytes: &[u16]) -> bool {
    bytes
        .iter()
        .all(|&a| bound[a as usize] == Some(Iv::exact(img[a as usize] as u32)))
}

/// What each indexed load reads in a round: inside its table's extent where its index is
/// unbounded or its bytes are unwritten, else all its index set reaches.
fn clips(img: &[u8; 0x10000], idx: &[Indexed], bound: &[Option<Iv>]) -> FxHashMap<u16, Vec<u16>> {
    idx.iter()
        .map(|x| {
            let ext = x.free || unwritten(img, bound, &x.all);
            let mut c = if ext { &x.ext } else { &x.all }.clone();
            c.sort_unstable();
            (x.pc, c)
        })
        .collect()
}

/// Address hulls of the loads through pointers and cell operands and of their bases (the
/// index registers zero), by `(pc, opcode, access index)`, from a pass in which an indexed
/// access reaches the bytes its index set gives, only those inside the table's extent
/// ([`crate::dispatch::Objects::valid`], its columns read alike) for a load of unwritten
/// bytes or an index the code does not bound, and every byte that only stores with bounded
/// addresses write (each store's address set where it has one, `stores`) holds the hull of
/// its entry byte and the values they store (of those values alone for a zero-page byte every
/// path since the entry stored). Starting from no bounds,
/// each round of the pass gives bounds that hold of every run, met with the last round's,
/// until a round changes none. The hulls hold for every run whose indexed accesses stay
/// inside those extents. With `proof` no access is taken to stay inside an extent: an indexed
/// access reaches every byte its index set gives, so the hulls hold for every run.
fn reach(
    p: &Pass,
    sets: &crate::dispatch::Sets,
    objs: &crate::dispatch::Objects,
    stores: &[crate::dispatch::Store],
    given: &Given,
    proof: bool,
) -> (Hulls, Regs, Runs) {
    let code = p.code;
    let addrs: FxHashMap<(u16, u8, usize), &[u16]> = stores
        .iter()
        .filter_map(|s| Some(((s.pc, s.op, s.ai), s.addrs.as_deref()?)))
        .collect();
    let mut idx = Vec::new();
    let mut wclip: FxHashMap<(u16, u8), Vec<u16>> = FxHashMap::default();
    for (&pc, vs) in p.vars {
        for v in vs {
            let Some(Tab::T { reg, .. }) = table(code, pc, v.op) else {
                continue;
            };
            let at = |x: u8| crate::dispatch::load_addr(code, pc, v.op, reg, x);
            let (true, Some(base)) = (reg != NOREG, at(0)) else {
                continue;
            };
            let bounded = sets.of(pc, reg as usize);
            let free = bounded.is_none();
            let xs = bounded.unwrap_or_else(|| sets.any(pc, reg as usize));
            let all: Vec<u16> = xs.iter().filter_map(|&x| at(x)).collect();
            // A table one value of a bounded index fills is no table the index walks: its
            // columns overlap.
            let n = objs.valid(&objs.table_of(base), &xs).max(1);
            let ext = all[..if !proof && (n > 1 || free) {
                n
            } else {
                all.len()
            }]
                .to_vec();
            match (v.acc.iter().any(|a| a.store), free && !proof) {
                (true, true) => {
                    wclip.insert((pc, v.op), ext);
                }
                (true, false) => {}
                (false, _) => idx.push(Indexed { pc, free, all, ext }),
            }
        }
    }
    // The bytes' entry values: none for page 1, the volatile ranges, the bytes the facts saw
    // written and those an entry stores from a register.
    let mut entry: Vec<Option<Iv>> = code
        .img
        .iter()
        .map(|&b| Some(Iv::exact(b as u32)))
        .collect();
    entry[0x100..0x200].fill(None);
    for &(lo, hi) in given.volatile {
        entry[lo as usize..hi.min(0x10000) as usize].fill(None);
    }
    for a in code.observed.ones() {
        entry[a] = None;
    }
    let top = vec![None; 0x10000];
    // A pair's floor starts at its image value where the first call may be any entry's, else
    // holding none: the init entry's returns give it its first values.
    let image: FxHashMap<u16, u32> = code
        .vecs
        .iter()
        .copied()
        .filter(|&q| crate::word::pair(code, q))
        .map(|q| {
            let at = |a: u16| entry[a as usize].and_then(Iv::value);
            (q, at(q).zip(at(q + 1)).map_or(0, |(l, h)| h << 8 | l))
        })
        .collect();
    let none = |v: u32| match given.init {
        Some(_) => crate::word::EMPTY,
        None => (v, v),
    };
    let floor = image.iter().map(|(&q, &v)| (q, none(v))).collect();
    let mut c = Code {
        img: code.img,
        cells: code.cells,
        pull_clear: code.pull_clear,
        vecs: code.vecs.clone(),
        observed: code.observed.clone(),
        constant: FixedBitSet::new(),
        clip: clips(code.img, &idx, &top),
        bound: top,
        stored: vec![Some(Iv::full(1)); 0x100],
        init: given.init.map_or(0, |i| 1 << (i % 64)),
        floor,
        runs: match given.loaded.filter(|_| !proof) {
            Some((lo, hi)) => {
                // Data bytes: the program's bytes of no instruction, and the bytes from the
                // program's start up stores with address sets write (a block copy's
                // destination).
                let mut data = given.written.clone();
                data.grow(0x10000);
                data.set_range(..lo as usize, false);
                data.insert_range(lo as usize..hi as usize);
                data.difference_with(p.insn);
                let mut runs: Vec<(u32, u32)> = Vec::new();
                for a in data.ones().map(|a| a as u32) {
                    match runs.last_mut() {
                        Some(r) if r.1 == a => r.1 += 1,
                        _ => runs.push((a, a + 1)),
                    }
                }
                runs
            }
            None => Vec::new(),
        },
        words: FxHashMap::default(),
        ptrs: FxHashMap::default(),
        exits: FxHashMap::default(),
        mods: FxHashMap::default(),
        pinned: FixedBitSet::new(),
        aimed: code.aimed.clone(),
    };
    // The pairs a loop running to its end writes both bytes of.
    c.exits = filled(code, p.vars, sets)
        .into_iter()
        .map(|(e, (bytes, last))| {
            let val = |a: u16| {
                last.binary_search_by_key(&a, |l| l.0)
                    .ok()
                    .map(|i| last[i].1)
            };
            let qs = (bytes.iter().chain(last.iter().map(|l| &l.0)).copied())
                .filter(|&q| crate::word::pair(&c, q))
                .filter_map(|q| {
                    let k = val(q).zip(val(q.wrapping_add(1)));
                    let w = k.map(|(l, h)| (h as u16) << 8 | l as u16);
                    let both = bytes.binary_search(&q.wrapping_add(1)).is_ok();
                    (w.is_some() || both && bytes.binary_search(&q).is_ok()).then_some((q, w))
                });
            let mut qs: Vec<(u16, Option<u16>)> = qs.collect();
            qs.sort_unstable();
            qs.dedup();
            (e, qs)
        })
        .collect();
    c.mods = mods(p.vars, &FxHashMap::default());
    let mut acc = Vec::new();
    let merged = loop {
        let (state, _) = fixpoint(&c, p.vars, p.entries, p.sh, p.disp, p.thr);
        // Each pair's floor widens to the hull of the values an entry's return leaves in it.
        let mut floor = c.floor.clone();
        for (&(pc, cx), st) in &state {
            let exit = p.vars[&pc]
                .iter()
                .any(|v| matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)));
            if !exit || cx < ANY && !p.sh.rets.wild.contains(&pc) {
                continue;
            }
            for (q, l) in floor.iter_mut() {
                let r = match st.wide.binary_search_by_key(q, |e| e.0) {
                    Ok(_) => crate::word::value(&c, st, *q).range(),
                    // A pair no store of the init entry's run reached holds its image value.
                    Err(_) if st.from & c.init != 0 => Some((image[q], image[q])),
                    Err(_) => None,
                };
                if let Some((lo, hi)) = r {
                    *l = (l.0.min(lo), l.1.max(hi));
                }
            }
        }
        let merged = merge(&c, &state);
        // Per byte the hull of the values stores write (every value for a store of unknown
        // address or value), none where no store writes it.
        let mut wrote: Vec<Option<Iv>> = vec![None; 0x10000];
        // Stores of `A` from a table base plus an index register: what they store of a
        // pointer, at which index; the bytes other stores write.
        let mut halves = Vec::new();
        let mut other = FixedBitSet::with_capacity(0x10000);
        let mut ptrs = FxHashMap::default();
        let mut stored_at: FxHashMap<u16, Vec<(u32, u32)>> = FxHashMap::default();
        let mut pinned = c.observed.clone();
        pinned.grow(0x10000);
        for (&pc, vs) in p.vars {
            let Some(st) = p.at(&merged, pc) else {
                continue;
            };
            for v in vs {
                acc.clear();
                step(&c, pc, v, &st, &mut acc);
                for (ai, a) in acc.iter().enumerate().filter(|(_, a)| a.store) {
                    let half = (matches!(v.op, 0x99 | 0x9d) && !c.runs.is_empty())
                        .then(|| {
                            let r = crate::dispatch::index_reg(Template::of(v.op))?;
                            let b = crate::dispatch::load_addr(&c, pc, v.op, r as u8, 0)?;
                            Some((b, st.half[0]?))
                        })
                        .flatten();
                    let cand = half.is_some();
                    halves.extend(half.map(|(b, h)| (pc, b, crate::word::whole(&c, &st, h))));
                    let mut put = |x: usize, v: Iv| {
                        wrote[x] = Some(wrote[x].map_or(v, |h| hull(h, v)));
                        other.set(x, other.contains(x) || !cand && st.from & !c.init != 0);
                    };
                    let set = wclip.get(&(pc, v.op)).map(Vec::as_slice);
                    let sset = set.or_else(|| addrs.get(&(pc, v.op, ai)).copied());
                    let aset = sset.or_else(|| c.aimed.get(&(pc, v.op, ai)).map(Vec::as_slice));
                    crate::word::stored_ptrs(&c, &st, v.op, a, aset, &mut ptrs);
                    stored_at.entry(pc).or_default().push(match aset {
                        Some([x, .., y]) => (*x as u32, *y as u32),
                        Some([x]) => (*x as u32, *x as u32),
                        _ => (a.addr.lo, a.addr.hi),
                    });
                    if let Some(set) = sset {
                        let at = indexed(&c, pc, v.op, &st);
                        let set = set.iter().filter(|&&x| {
                            a.addr.meets(x as u32, x as u32 + 1)
                                && at.as_ref().is_none_or(|at| at.binary_search(&x).is_ok())
                        });
                        set.for_each(|&x| {
                            pinned.insert(x as usize);
                            put(x as usize, a.val)
                        });
                    } else {
                        if a.addr.hi - a.addr.lo < 0x100 {
                            pinned.insert_range(
                                a.addr.lo as usize..a.addr.hi.min(0xffff) as usize + 1,
                            );
                        }
                        let v = if a.addr.hi - a.addr.lo < 0x100 {
                            a.val
                        } else {
                            Iv::full(1)
                        };
                        (a.addr.lo as usize..=a.addr.hi.min(0xffff) as usize)
                            .for_each(|x| put(x, v));
                    }
                }
            }
        }
        let words = crate::word::columns(&c, &halves, &other, |a, b| {
            straight(p.vars, a, b, code) || straight(p.vars, b, a, code)
        });
        let with = |e: Option<Iv>, w: Option<Iv>| match (e, w) {
            (Some(e), Some(w)) => Some(hull(e, w)),
            (e, None) => e,
            (None, _) => None,
        };
        let bound: Vec<Option<Iv>> = entry
            .iter()
            .zip(&wrote)
            .map(|(&e, &w)| with(e, w))
            .collect();
        // Each round's values hold of every run (it starts from every value), so their meet
        // with the last round's does: the iteration descends to where a round changes none.
        let meet_top = |o: &[Option<Iv>], n: Vec<Option<Iv>>| -> Vec<Option<Iv>> {
            if o.is_empty() {
                return n;
            }
            o.iter()
                .zip(n)
                .map(|(&o, n)| match (o, n) {
                    (Some(o), Some(n)) => Some(meet(o, n).unwrap_or(n)),
                    (o, None) => o,
                    (None, n) => n,
                })
                .collect()
        };
        let bound = meet_top(&c.bound, bound);
        let stored: Vec<Option<Iv>> = c
            .stored
            .iter()
            .zip(&wrote)
            .map(|(&o, &n)| o.zip(n).map(|(o, n)| meet(o, n).unwrap_or(n)))
            .collect();
        // The floors are a least fixpoint over runs whose bytes keep their bounds: they fall
        // once the bounds hold.
        if bound == c.bound && stored == c.stored {
            // The word columns' stored pointers are a least fixpoint: a column found holds
            // none, then rises to the pointers its stores write; floors fall once they hold.
            // A column's bounds widen to the run of data bytes they move in, else to the
            // extremes, so the rise ends.
            let rose: FxHashMap<(u16, u16), (u32, u32)> = (c.words.iter())
                .map(|(k, &(lo, hi))| {
                    let (l, h) = words.get(k).copied().unwrap_or(crate::word::EMPTY);
                    let run = |x: u32| c.run_of(x).filter(|_| lo <= hi);
                    let l = match l < lo && lo <= hi {
                        true => run(l).map_or(0, |r| r.0),
                        false => lo.min(l),
                    };
                    let h = match h > hi && lo <= hi {
                        true => run(h).map_or(crate::word::MAX, |r| r.1 - 1),
                        false => hi.max(h),
                    };
                    (*k, (l, h))
                })
                .collect();
            let mods = mods(p.vars, &stored_at);
            // What stores write of pointers only rises: a byte some round found written with
            // no pointer's byte keeps none.
            for (k, v) in &c.ptrs {
                let e = ptrs.entry(*k).or_insert(*v);
                *e = e.zip(*v).map(|(a, b)| (a.0.min(b.0), a.1.max(b.1)));
            }
            if ptrs != c.ptrs || mods != c.mods {
                (c.ptrs, c.mods) = (ptrs, mods);
            } else if words.keys().any(|k| !c.words.contains_key(k)) {
                for &k in words.keys() {
                    c.words.entry(k).or_insert(crate::word::EMPTY);
                }
            } else if rose != c.words {
                c.words = rose;
            } else if floor == c.floor {
                break merged;
            } else {
                c.floor = floor;
            }
        }
        c.clip = clips(code.img, &idx, &bound);
        c.pinned = pinned;
        (c.bound, c.stored) = (bound, stored);
    };
    let mut out = FxHashMap::default();
    let mut regs = Regs::default();
    let mut runs = Runs::default();
    for (&pc, vs) in p.vars {
        let Some(st) = p.at(&merged, pc) else {
            continue;
        };
        regs.insert(pc, std::array::from_fn(|r| st.vals(r).0));
        let mut zero = st.clone();
        zero.env[di_pcode::reg::X] = Iv::exact(0);
        zero.env[di_pcode::reg::Y] = Iv::exact(0);
        for v in vs {
            let t = Template::of(v.op);
            let b = operand_bytes(code.img, code.cells, pc, t.len);
            let cell = b[..t.len as usize - 1].iter().any(Option::is_none);
            let (mut acc, mut base) = (Vec::new(), Vec::new());
            step(&c, pc, v, &st, &mut acc);
            step(&c, pc, v, &zero, &mut base);
            let ix = crate::dispatch::index_reg(t);
            let idx = ix.map_or(Iv::exact(0), |r| st.env[r]);
            let narrow = |a: Iv, w: Option<Iv>| match w {
                Some(w) if a.hi <= 0xffff => meet(a, w).unwrap_or(a),
                Some(w) => w,
                None => a,
            };
            // A store through a cell operand pair whose every store writes a pointer into one
            // run of data bytes ([`Code::ptrs`]) writes that run.
            for (ai, a) in acc.iter().enumerate().filter(|(_, a)| a.store && !a.dummy) {
                let q = (cell && t.len == 3 && a.ptr.is_none()).then_some(pc.wrapping_add(1));
                let ptr = |x: u16| matches!(c.ptrs.get(&x), Some(Some(_)));
                let pair = q.filter(|&q| crate::word::pair(&c, q) && ptr(q) && ptr(q + 1));
                let at = |x: u32| c.run_of(x);
                if let (false, None, Some(q)) = (proof, c.aimed.get(&(pc, v.op, ai)), pair) {
                    if let Some((lo, hi)) = crate::word::value(&c, &st, q).range() {
                        if let (Some(r), true) = (at(lo), at(lo) == at(hi)) {
                            runs.insert((pc, v.op, ai), r);
                        }
                    }
                }
            }
            for (ai, (a, b)) in acc.iter().zip(&base).enumerate() {
                // With `proof`, a load from a constant base and an index register too: the
                // bounds of the bytes its index comes from hold for every run.
                let indexed = proof
                    && ix.is_some()
                    && a.addr.hi <= 0xffff
                    && acc.iter().all(|x| x.ptr.is_none());
                if !a.store && !a.dummy && (a.ptr.is_some() || cell || indexed) {
                    // The pair the address is formed from: a zero-page pointer, or the cell
                    // operand.
                    let q = match a.ptr {
                        Some(crate::analysis::Ptr {
                            lo: Some(l), hi, ..
                        }) => l
                            .value()
                            .filter(|&l| hi.value() == Some(l + 1))
                            .map(|l| l as u16),
                        Some(_) => None,
                        None => (t.len == 3).then_some(pc.wrapping_add(1)),
                    }
                    .filter(|&q| crate::word::pair(&c, q));
                    let w = |i: Iv| q.and_then(|q| crate::word::reach(&c, &st, q, i));
                    let (a, b) = (narrow(a.addr, w(idx)), narrow(b.addr, w(Iv::exact(0))));
                    out.insert((pc, v.op, ai), (a, b));
                }
            }
        }
    }
    (out, regs, runs)
}

/// Stores writing one run of data bytes `[lo, hi)`, by `(pc, opcode, access index)`.
type Runs = FxHashMap<(u16, u8, usize), (u32, u32)>;

/// Per exit edge of a loop that runs every iteration to its end
/// ([`crate::scev::Induction::exit`]): the bytes the stores every iteration runs write over
/// its iterations, indexed by the loop's register, directly or through a pointer whose bytes
/// hold table bytes it indexes (every context at the store) plus one value of `Y`; and the
/// byte each such store at a fixed address no other store of the loop writes leaves, where
/// it stores a constant or a table byte the register indexes: its last iteration's.
fn filled(code: &Code, vars: &FxHashMap<u16, Vec<Var>>, sets: &crate::dispatch::Sets) -> Exits {
    let mut out: Exits = FxHashMap::default();
    let konst = |a: u16| {
        (code.constant.contains(a as usize) && !code.cells.contains(a as usize))
            .then(|| code.img[a as usize])
    };
    let tag = |s: &St, a: u32| s.mem.iter().find(|e| e.0 as u32 == a).and_then(|e| e.1);
    for iv in sets.ind {
        let (Some(edge), crate::scev::Place::Reg(r), Some(x0)) =
            (iv.exit, iv.var, iv.start.value())
        else {
            continue;
        };
        let x_at = |d: u8, k: u32| ((x0 + d as u32 + k * iv.step as u32) & 0xff) as u8;
        // The value a table byte tagged `t` holds at index value `x`.
        let at = |t: Option<Tab>, x: u8| match t {
            Some(Tab::K(k)) => Some(k),
            Some(Tab::T {
                pc,
                op,
                reg,
                ix: Ix::Live,
            }) if reg == r => konst(crate::dispatch::load_addr(code, pc, op, r, x)?),
            _ => None,
        };
        let stores_into = |m: u16| {
            let hits = |p: u16, v: &Var| {
                v.acc.iter().enumerate().any(|(ai, a)| {
                    a.store
                        && !a.dummy
                        && match code.aimed.get(&(p, v.op, ai)) {
                            Some(set) => set.binary_search(&m).is_ok(),
                            None => a.addr.meets(m as u32, m as u32 + 1),
                        }
                })
            };
            (iv.at
                .keys()
                .flat_map(|p| vars[p].iter().map(move |v| (*p, v))))
            .filter(|&(p, v)| hits(p, v))
            .count()
        };
        let (mut bytes, mut last) = (Vec::new(), Vec::new());
        for &(pc, n) in &iv.every {
            let Some(&d) = iv.at.get(&pc) else { continue };
            let xs = (0..n).map(|k| x_at(d, k));
            let states = sets.states.get(&pc).map_or(&[][..], Vec::as_slice);
            let Some(&s0) = states.first() else { continue };
            for v in &vars[&pc] {
                let Some(a) = v.acc.iter().find(|a| a.store && !a.dummy) else {
                    continue;
                };
                let ir = crate::dispatch::index_reg(Template::of(v.op));
                let Some(p) = a.ptr else {
                    if ir == Some(r as usize) {
                        let at = |x| crate::dispatch::load_addr(code, pc, v.op, r, x);
                        bytes.extend(xs.clone().filter_map(at));
                    }
                    let fixed = crate::dispatch::load_addr(code, pc, v.op, NOREG, 0);
                    let held = |s: &St| match v.op {
                        0x85 | 0x8d => s.tag[0],
                        0x86 | 0x8e => s.tag[1],
                        0x84 | 0x8c => s.tag[2],
                        _ => None,
                    };
                    let one = states.iter().all(|s| held(s) == held(s0));
                    if let (Some(m), true, Some(b)) = (fixed, one, at(held(s0), x_at(d, n - 1))) {
                        if n > 0 && stores_into(m) == 1 {
                            last.push((m, b));
                        }
                    }
                    continue;
                };
                let (Some(q), Some(h)) = (p.lo.and_then(|l| l.value()), p.hi.value()) else {
                    continue;
                };
                let y = |s: &St| match ir {
                    Some(di_pcode::reg::Y) => s.env[di_pcode::reg::Y].value(),
                    _ => None,
                };
                let key = |s: &St| (tag(s, q), tag(s, h), y(s));
                let (Some(t0 @ Tab::T { .. }), Some(t1 @ Tab::T { .. }), Some(k)) = key(s0) else {
                    continue;
                };
                if !states.iter().all(|s| key(s) == key(s0)) {
                    continue;
                }
                bytes.extend(xs.clone().filter_map(|x| {
                    let w = (at(Some(t1), x)? as u16) << 8 | at(Some(t0), x)? as u16;
                    Some(w.wrapping_add(k as u16))
                }));
            }
        }
        bytes.sort_unstable();
        bytes.dedup();
        last.sort_unstable();
        let e = out.entry(edge).or_default();
        e.0.extend(bytes);
        e.1.extend(last);
    }
    out
}

/// Per exit edge of a loop ([`filled`]): the bytes written over its iterations, and those
/// whose value past it is known.
type Exits = FxHashMap<(u16, u16), (Vec<u16>, Vec<(u16, u8)>)>;

/// Whether the instruction at `a` runs on to the one at `b` by fall-through alone, writing
/// neither index register on the way: indexed stores at both index one value.
fn straight(vars: &FxHashMap<u16, Vec<Var>>, a: u16, b: u16, code: &Code) -> bool {
    let mut pc = a;
    loop {
        let [v] = vars.get(&pc).map_or(&[][..], Vec::as_slice) else {
            return false;
        };
        let Flow::Next(n) = v.flow else {
            return false;
        };
        if n <= pc || n > b {
            return false;
        }
        if n == b {
            return true;
        }
        let Some(w) = vars.get(&n).and_then(|vs| vs.first()) else {
            return false;
        };
        let t = Template::of(w.op);
        let bs = operand_bytes(code.img, code.cells, n, t.len);
        let (_, written) = symbolic(t.ops(), bs, None);
        if written & (1 << di_pcode::reg::X | 1 << di_pcode::reg::Y) != 0 {
            return false;
        }
        pc = n;
    }
}

/// The addresses the access of opcode `op` at `pc` reaches from a constant operand with each
/// value its index register may hold in state `st`, ascending; None without an index register
/// or when a value does not fix the address.
fn indexed(code: &Code, pc: u16, op: u8, st: &St) -> Option<Vec<u16>> {
    let r = crate::dispatch::index_reg(Template::of(op))?;
    let mut out = (st.vals(r).values())
        .map(|x| crate::dispatch::load_addr(code, pc, op, r as u8, x as u8))
        .collect::<Option<Vec<u16>>>()?;
    out.sort_unstable();
    Some(out)
}

/// The stores of `vars` from a constant base plus an index register that may hold any byte
/// on some run (`regs`, [`reach`]'s proof pass) whose base is a data byte of the `loaded`
/// program and that may reach an instruction byte (`code`) past it, by `(pc, opcode, access
/// index)`: each writes the run `[lo, hi)` of data bytes holding its base, the array it
/// indexes, and a write past it is out of bounds.
fn bounded_stores(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    regs: &Regs,
    bytes: &FixedBitSet,
    loaded: Option<(u32, u32)>,
) -> FxHashMap<(u16, u8, usize), (u32, u32)> {
    let mut out = FxHashMap::default();
    let Some((lo, hi)) = loaded else {
        return out;
    };
    for (&pc, vs) in vars {
        for v in vs {
            let t = Template::of(v.op);
            let Some(r) = crate::dispatch::index_reg(t).filter(|_| t.len == 3) else {
                continue;
            };
            let Some(ai) = v.acc.iter().position(|a| a.store && !a.dummy) else {
                continue;
            };
            let b = crate::dispatch::load_addr(code, pc, v.op, r as u8, 0).map(|b| b as u32);
            let Some(b) = b.filter(|&b| (lo..hi).contains(&b) && !bytes.contains(b as usize))
            else {
                continue;
            };
            if regs.get(&pc).is_none_or(|x| x[r] != [u64::MAX; 4]) {
                continue;
            }
            let start = (lo..b)
                .rev()
                .find(|&a| bytes.contains(a as usize))
                .map_or(lo, |a| a + 1);
            let end = (b..hi).find(|&a| bytes.contains(a as usize)).unwrap_or(hi);
            // Only a store that may reach an instruction: one past the program writes data.
            if end < hi && end < b + 0x100 {
                out.insert((pc, v.op, ai), (start, end));
            }
        }
    }
    out
}

/// What [`reach`] takes: the volatile ranges and the index of the entry the host calls once,
/// before the others.
#[derive(Clone, Copy)]
pub struct Wanted<'a> {
    pub volatile: &'a [(u32, u32)],
    pub init: Option<usize>,
}

/// What the analysis takes beyond the program: the `[lo, hi)` the program was loaded to, the
/// bytes the facts saw written and those taken as constant (holding their entry value: trip
/// bounds may read them).
pub struct Given<'a> {
    pub loaded: Option<(u32, u32)>,
    pub observed: &'a FixedBitSet,
    pub constant: &'a FixedBitSet,
    /// Volatile ranges `[lo, hi)`: their bytes have no bounds.
    pub volatile: &'a [(u32, u32)],
    /// Find the address hulls of the loads through pointers ([`reach`]).
    pub reach: bool,
    /// The index of the entry the host calls once, before the others.
    pub init: Option<usize>,
    /// The bytes stores with address sets write (a block copy's destination): data.
    pub written: &'a FixedBitSet,
    /// The address sets stores write, taken as given ([`Aimed`]).
    pub aimed: &'a Aimed,
    /// Instruction bytes stores with address sets write that no instruction runs at after
    /// them (code the init entry runs before a block copy overwrites it): data.
    pub dead: &'a FixedBitSet,
}

/// Fill each variant's accesses from the interval states at its start. With `matched`, an
/// `RTS` whose callee leaves the stack as it found it returns to its caller; otherwise any
/// return may reach any return site. An `RTS`/`RTI` in `disp` jumps to its targets through
/// the return address its function pushed.
pub fn analyze(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    vars: &mut FxHashMap<u16, Vec<Var>>,
    entries: &[Entry],
    matched: bool,
    disp: &FxHashSet<u16>,
    given: &Given,
) -> Analysis {
    let (loaded, observed) = (given.loaded, given.observed);
    let sh = shape(vars, entries, matched, disp);
    let (roots, heads) = (&sh.roots, &sh.heads);
    let code = Code {
        img,
        cells,
        pull_clear: pull_clear(vars, entries),
        vecs: crate::dispatch::vectors(img, cells, vars),
        observed: observed.clone(),
        constant: given.constant.clone(),
        clip: FxHashMap::default(),
        bound: Vec::new(),
        stored: Vec::new(),
        init: 0,
        floor: FxHashMap::default(),
        runs: Vec::new(),
        words: FxHashMap::default(),
        ptrs: FxHashMap::default(),
        exits: FxHashMap::default(),
        mods: FxHashMap::default(),
        pinned: FixedBitSet::new(),
        aimed: given.aimed.clone(),
    };
    let thr = Thresholds::new(vars, &code);
    let (state, ret) = fixpoint(&code, vars, entries, &sh, disp, &thr);
    let merged = merge(&code, &state);
    let succs = &sh.succs;
    let mut dec = FixedBitSet::with_capacity(0x10000);
    let mut fill = |vars: &mut FxHashMap<u16, Vec<Var>>, pc: u16, bs: [Option<Iv>; 2]| {
        let mut st = merged
            .get(&pc)
            .map_or_else(|| St::new(TOP_ENV), Clone::clone);
        dec.set(
            pc as usize,
            !code.pull_clear && st.env[di_pcode::reg::D].hi != 0,
        );
        for (r, b) in [di_pcode::reg::X, di_pcode::reg::Y].into_iter().zip(bs) {
            if let Some(b) = b.and_then(|b| meet(b, st.env[r])) {
                st.env[r] = b;
            }
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
        fill(vars, pc, [None, None]);
    }
    // Index bounds from the loops' trip counts tighten the accesses they reach.
    let konst = |a: u32| {
        (given.constant.contains(a as usize) && !cells.contains(a as usize))
            .then(|| img[a as usize])
    };
    let mut kreads = Vec::new();
    let ind = crate::scev::find(
        &code,
        vars,
        heads,
        succs,
        &merged,
        roots,
        &konst,
        &mut kreads,
    );
    let (xb, yb) = (
        crate::scev::bounds(&ind, di_pcode::reg::X),
        crate::scev::bounds(&ind, di_pcode::reg::Y),
    );
    let mut bounded: Vec<u16> = xb.keys().chain(yb.keys()).copied().collect();
    bounded.sort_unstable();
    bounded.dedup();
    for pc in bounded {
        fill(vars, pc, [xb.get(&pc).copied(), yb.get(&pc).copied()]);
    }
    let mut states: FxHashMap<u16, Vec<&St>> = FxHashMap::default();
    for (&(pc, _), st) in &state {
        states.entry(pc).or_default().push(st);
    }
    let mut preds: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
    for (&p, ss) in succs {
        for &s in ss {
            preds.entry(s).or_default().push(p);
        }
    }
    let sets = crate::dispatch::Sets {
        states,
        merged: &merged,
        ind: &ind,
        preds,
        roots,
    };
    let mut bytes = FixedBitSet::with_capacity(0x10000);
    for (&pc, vs) in vars.iter() {
        for v in vs {
            for i in 0..Template::of(v.op).len as u16 {
                bytes.insert(pc.wrapping_add(i) as usize);
            }
        }
    }
    bytes.difference_with(given.dead);
    let objs = crate::dispatch::Objects::new(&code, vars, &merged, &bytes, loaded, &sets.states);
    let mut stores = crate::dispatch::stores(&code, vars, &sets);
    let (reach, proven, regs, runs) = if given.reach {
        let pass = Pass {
            code: &code,
            vars,
            entries,
            sh: &sh,
            disp,
            thr: &thr,
            xy: [&xb, &yb],
            insn: &bytes,
        };
        let (assumed, _, runs) = reach(&pass, &sets, &objs, &stores, given, false);
        let (proven, regs, _) = reach(&pass, &sets, &objs, &stores, given, true);
        (assumed, proven, regs, runs)
    } else {
        Default::default()
    };
    let mut bounded = bounded_stores(&code, vars, &regs, &bytes, loaded);
    bounded.extend(runs);
    for (&(pc, op, ai), &(lo, hi)) in &bounded {
        for v in vars
            .get_mut(&pc)
            .into_iter()
            .flatten()
            .filter(|v| v.op == op)
        {
            let a = &mut v.acc[ai];
            a.addr = meet(a.addr, Iv { lo, hi: hi - 1 }).unwrap_or(a.addr);
        }
    }
    let mut guards = Vec::new();
    let mut reads: std::collections::BTreeSet<u16> = kreads.into_iter().collect();
    // Tables whose bytes gave loads their values are constant, guarded like code.
    for a in vars.values().flatten().flat_map(|v| &v.acc) {
        let table = !a.store && !a.dummy && a.addr.value().is_none_or(|x| x >= 0x100);
        if table && code.konst(a.addr).is_some() {
            reads.extend((a.addr.lo..=a.addr.hi).map(|x| x as u16));
        }
    }
    let through = crate::dispatch::pointer_stores(
        &code,
        vars,
        &sets,
        &objs,
        &mut stores,
        &mut guards,
        &mut reads,
        &konst,
    );
    crate::dispatch::tag_values(
        &code,
        vars,
        &sets,
        &objs,
        &mut stores,
        &mut guards,
        &mut reads,
    );
    // A store whose address is unknown only through its own operand cells writes where the
    // values those cells may hold address.
    let cellvals = match loaded {
        Some(_) => crate::dispatch::cell_addressed(&code, vars, &sets, &mut stores),
        None => crate::dispatch::cell_values(img, &stores),
    };
    // A store through a pointer with an address set reaches the hull of its set; its
    // throw-away read, the base's page with the indexed low byte, the pages from the least
    // address less a page to the greatest.
    for &i in &through {
        let st = &stores[i];
        let Some((&lo, &hi)) = st
            .addrs
            .as_deref()
            .and_then(|a| Some((a.first()?, a.last()?)))
        else {
            continue;
        };
        let (lo, hi) = (lo as u32, hi as u32);
        let page = Iv {
            lo: lo.saturating_sub(0xff) & 0xff00,
            hi: hi | 0xff,
        };
        for v in vars
            .get_mut(&st.pc)
            .into_iter()
            .flatten()
            .filter(|v| v.op == st.op)
        {
            v.acc[st.ai].addr = Iv { lo, hi };
            for a in v.acc.iter_mut().filter(|a| a.dummy && !a.store) {
                a.addr = meet(a.addr, page).unwrap_or(page);
            }
        }
    }
    let loads = crate::dispatch::pointer_loads(
        &code, vars, &sets, &objs, &stores, &guards, &mut reads, &konst,
    );
    // A load through a pointer with an address set reaches the hull of its set.
    for (&(pc, op, ai), a) in &loads {
        let (lo, hi) = (a[0] as u32, a[a.len() - 1] as u32);
        for v in vars
            .get_mut(&pc)
            .into_iter()
            .flatten()
            .filter(|v| v.op == op)
        {
            v.acc[ai].addr = Iv { lo, hi };
        }
    }
    let (jumps, sels) =
        crate::dispatch::jumps(&code, vars, &state, &sets, &objs, &stores, &cellvals);
    Analysis {
        past: ret
            .iter()
            .filter(|(_, s)| !s.past.is_empty())
            .map(|(&r, s)| {
                let mut t: Vec<u16> = s.past.keys().copied().collect();
                t.sort_unstable();
                (r, t)
            })
            .collect(),
        from: merged.into_iter().map(|(pc, st)| (pc, st.from)).collect(),
        dec,
        ind,
        jumps,
        sels,
        stores,
        guards,
        cellvals,
        reads,
        loads,
        reach,
        proven,
        regs,
        bounded,
        succs: sh.succs.clone(),
    }
}
