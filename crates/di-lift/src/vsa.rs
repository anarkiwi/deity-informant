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
    from: u64,
    /// Known bits of `A`, `X` and `Y`.
    pub(crate) kb: [Kb; DATA],
    /// Table bytes `A`, `X` and `Y` hold.
    pub(crate) tag: [Option<Tab>; DATA],
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
            push: [None; 3],
            above: [None; 2],
            depth: Some(0),
            mem: Vec::new(),
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
    fn reach(&self, p: crate::analysis::Ptr) -> Option<Iv> {
        let (q, h) = (p.lo?.value()?, p.hi.value()?);
        let iv = |a: u32| self.mem.iter().find(|e| e.0 as u32 == a).map(|e| e.2);
        let (lo, hi) = (iv(q)?, iv(h)?);
        Some(Iv {
            lo: hi.lo << 8 | lo.lo,
            hi: (hi.hi << 8 | lo.hi) + p.idx,
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
            mem: self.mem.clone(),
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
        if r < DATA {
            s.kb[r] = s.kb[r].meet(Kb::of(s.env[r]));
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
}

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
                && e.set.iter().all(|&(r, x)| r != d || x == 0)
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
    let load = |a: Iv| a.value().and_then(held).or_else(|| code.konst(a));
    absint_ranges(ops, b, bi, &mut env, acc, &load);
    for a in acc.iter_mut() {
        if let Some(iv) = a.ptr.and_then(|p| st.pointed(p)) {
            if let Some(m) = meet(a.addr, iv) {
                a.addr = m;
            }
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
        push: st.push,
        above: st.above,
        depth: st.depth,
        mem: st.mem.clone(),
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
    for a in acc.iter().filter(|a| a.store) {
        // A pointer store whose sum may wrap reaches the low addresses it wraps to.
        let reach = a.ptr.and_then(|p| st.reach(p));
        let hit = |e: &(u16, Option<Tab>, Iv)| match reach {
            Some(r) => {
                let m = e.0 as u32;
                (r.lo..=r.hi).contains(&m) || (r.lo..=r.hi).contains(&(m + 0x10000))
            }
            None => a.addr.meets(e.0 as u32, e.0 as u32 + 1),
        };
        if !code
            .vecs
            .iter()
            .any(|&m| a.addr.meets(m as u32, m as u32 + 1))
        {
            continue;
        }
        let old = st
            .mem
            .iter()
            .find(|e| Some(e.0 as u32) == a.addr.value())
            .copied();
        s.mem.retain(|e| !hit(e));
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
    let tab = |x: Option<Tab>, y: Option<Tab>| join_tab(x?, y?);
    St {
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
    /// Joined states of the `RTS`s returning through the return address advanced past the
    /// call (inline arguments), by the address they return to.
    past: FxHashMap<u16, St>,
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
        (s.push, s.above, s.depth) = (call.push, call.above, call.depth);
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
    /// An `RTS` of the call returning to site `r` returns to `t` instead, with `st`.
    fn give_past(&mut self, r: u16, t: u16, st: &St) {
        let site = self.ret.entry(r).or_default();
        let new = site
            .past
            .get(&t)
            .map_or_else(|| st.clone(), |o| join(o, st, None));
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
        let past: Vec<(u16, St)> = site
            .past
            .iter()
            .map(|(&t, p)| (t, apply(p, &site.calls[&cx], true)))
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
}

/// Each store of `stores` an entry's run decided writes the pairs it made ([`Made`]).
///
/// [`Made`]: crate::dispatch::Made
fn pair(stores: &mut [crate::dispatch::Store], made: &crate::dispatch::Made) {
    for s in stores.iter_mut() {
        if let Some(Some(p)) = made.get(&(s.pc, s.op, s.ai)) {
            let addrs: std::collections::BTreeSet<u16> = p.iter().map(|e| e.0).collect();
            s.addrs = Some(addrs.into_iter().collect());
            s.vals = Some(p.iter().map(|e| e.1).collect());
            s.pairs = Some(p.iter().copied().collect());
        }
    }
}

/// What the analysis takes beyond the program: the `[lo, hi)` the program was loaded to, the
/// bytes the facts saw written and those taken as constant (holding their entry value: trip
/// bounds may read them).
pub struct Given<'a> {
    pub loaded: Option<(u32, u32)>,
    /// The SP every entry is called at, when declared: an entry's run
    /// ([`crate::dispatch::entry_runs`]) then knows where its pushes land.
    pub entry_sp: Option<u8>,
    pub observed: &'a FixedBitSet,
    pub constant: &'a FixedBitSet,
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
    let code = Code {
        img,
        cells,
        pull_clear: pull_clear(vars, entries),
        vecs: crate::dispatch::vectors(img, cells, vars),
        observed: observed.clone(),
        constant: given.constant.clone(),
    };
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
    let mut merged: FxHashMap<u16, St> = FxHashMap::default();
    for (&(pc, _), st) in &w.state {
        let m = merged
            .get(&pc)
            .map_or_else(|| st.clone(), |o| join(o, st, None));
        merged.insert(pc, m);
    }
    let succs: FxHashMap<u16, Vec<u16>> = vars.keys().map(|&pc| (pc, succ_of(pc))).collect();
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
        &heads,
        &succs,
        &merged,
        &roots,
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
    for (&(pc, _), st) in &w.state {
        states.entry(pc).or_default().push(st);
    }
    let mut preds: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
    for (&p, ss) in &succs {
        for &s in ss {
            preds.entry(s).or_default().push(p);
        }
    }
    let sets = crate::dispatch::Sets {
        states,
        merged: &merged,
        ind: &ind,
        preds,
        roots: &roots,
    };
    let mut bytes = FixedBitSet::with_capacity(0x10000);
    for (&pc, vs) in vars.iter() {
        for v in vs {
            for i in 0..Template::of(v.op).len as u16 {
                bytes.insert(pc.wrapping_add(i) as usize);
            }
        }
    }
    let objs = crate::dispatch::Objects::new(&code, vars, &merged, &bytes, loaded, &sets.states);
    let mut stores = crate::dispatch::stores(&code, vars, &sets);
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
    // Stores an entry's run decides write the pairs it made.
    let none = FxHashSet::default();
    let (made, _) =
        crate::dispatch::entry_runs(&code, vars, entries, given.entry_sp, &konst, &none);
    pair(&mut stores, &made);
    let mut cellvals = crate::dispatch::cell_values(img, &stores);
    // An inductive invariant: with every loaded byte the entries' runs read taken as its image
    // byte, the runs' stores and every store through operand cells resolved at the values that
    // gives hold if no store of the program may write a byte the runs read. An entry whose
    // reads some store may write is not run, until the rest hold or none is left.
    if let Some((lo, hi)) = loaded {
        let image = |a: u32| {
            let i = a as usize;
            (lo <= a && a < hi && !code.cells.contains(i) && !code.observed.contains(i))
                .then(|| img[i])
        };
        let mut skip: FxHashSet<u16> = FxHashSet::default();
        while skip.len() < entries.len() {
            let (made, read) =
                crate::dispatch::entry_runs(&code, vars, entries, given.entry_sp, &image, &skip);
            let mut opt = stores.clone();
            pair(&mut opt, &made);
            let cv = crate::dispatch::cell_addressed(&code, vars, &sets, &mut opt);
            let touched: FxHashSet<u16> = read
                .iter()
                .filter(|(_, r)| {
                    opt.iter().any(|s| match &s.addrs {
                        Some(a) => a.iter().any(|x| r.contains(x)),
                        None => r.iter().any(|&x| s.addr.meets(x as u32, x as u32 + 1)),
                    })
                })
                .map(|(&e, _)| e)
                .collect();
            if touched.is_empty() {
                stores = opt;
                cellvals = cv;
                break;
            }
            skip.extend(touched);
        }
    }
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
        crate::dispatch::jumps(&code, vars, &w.state, &sets, &objs, &stores, &cellvals);
    Analysis {
        past: w
            .ret
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
    }
}
