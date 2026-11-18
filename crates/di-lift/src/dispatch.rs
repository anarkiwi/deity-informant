//! What the interval analysis tells about computed transfers and stores, from the code alone.
//!
//! **Index sets.** The values an index register may hold at an instruction: the union over
//! its call contexts of its interval cut by its known bits there, cut by the progression
//! `start + k * step` (`k` below the trip bound) of every loop counter it is
//! ([`crate::scev`]). An index whose interval is every byte and that no trip bound limits is
//! unbounded: known bits refine an index set, they do not bound it. A computed transfer's
//! table read through an unbounded index that holds a byte of the loaded program
//! ([`crate::vsa::St::src`]) reads at the values the stores the analysis reaches may leave in
//! that byte (at the addresses their index registers take, cut where they hold their
//! function's entry value by the calls entering the context, [`Sets::entered`]), and its
//! entry byte.
//!
//! **Stores.** A store whose address is its operand plus index registers has, as addresses,
//! the address for each value of their index sets (each evaluated exactly); one through a
//! `(zp),Y` pointer whose bytes hold table bytes or constants on every path has each word they
//! make plus each value of `Y`; any other (through a cell operand, an unbounded index or
//! another pointer) is unknown. A stored register's values are
//! those of the definitions reaching the store (each instruction writing it on a path to the
//! store with no other between: the values its interval and known bits allow after it), so
//! paths that load different constants keep them apart.
//!
//! **Computed jumps.** The analysis states tag a byte loaded from a constant base plus an
//! index register with the load, through register copies, pushes and stores to `JMP (ind)`
//! pointers, and whether its index is still the register's value; where the register is
//! written, the bytes it indexed become a group sharing one index value. A `JMP (ind)` whose
//! pointer bytes hold such tags (or constants) on every path, and an `RTS`/`RTI` whose return
//! address its function pushed so, goes to the words the loads read for each index value:
//! two bytes indexed by the register's current value, or of one group, pair up value by
//! value, a constant pairs with every byte. `JMP`/`JSR`s and branches through cell operands go
//! the same way, or by the values every store leaves in the cells. A `JMP (ind)` through a
//! pointer in the code span that no store may write goes to the pointer's word. The bytes read must be ones no store
//! may write; otherwise, or when a byte is not tagged on some path, an index is unbounded or
//! two bytes are indexed by unrelated values, the transfer is not resolved. A store of the byte
//! a transfer the path took ([`crate::vsa::Arm`]) is indexed by stores the index values whose
//! word is a target taken ([`arm_values`]). A transfer whose target bytes are constants or
//! tags indexed by one value selects its target by that value ([`Selector`]).

use crate::analysis::{absint, operand_bytes, Access, Flow, Iv, Tg, Var, TOP_ENV};
use crate::scev::{Induction, Place};
use crate::vsa::{Code, Ix, Key, St, Tab, NOGRP, NOREG};
use crate::why::{By, Extent, Of};
use di_pcode::{reg, CtrlKind, Opc, Space, Template, Vn};
use fixedbitset::FixedBitSet;
use petgraph::algo::dominators::{simple_fast, Dominators};
use petgraph::graphmap::DiGraphMap;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::BTreeSet;

/// A store of the template op `ai` (its access index) of opcode `op` at `pc`: its interval,
/// its addresses (`None`: unknown) and values (`None`: any).
#[derive(Clone, Debug)]
pub struct Store {
    pub pc: u16,
    pub op: u8,
    pub ai: usize,
    pub addr: Iv,
    pub addrs: Option<Vec<u16>>,
    pub vals: Option<BTreeSet<u8>>,
    /// Cells whose values the store also writes (a byte loaded from one and stored).
    pub deps: Vec<u16>,
    /// The `(address, value)` pairs it writes, when an entry's run gives them all.
    pub pairs: Option<Vec<(u16, u8)>>,
    /// The cell whose byte decides the value written, with the value each byte of it leaves
    /// (`None`: any) ([`cell_maps`]).
    pub from: Option<(u16, Vec<Option<u8>>)>,
}

/// An index guard: the instruction at `pc` deopts before it unless index register `reg` is in
/// `ok` (a table read outside its extent `ext`, which guards compare equal whatever).
#[derive(Clone, Copy, Debug)]
pub struct Guard {
    pub pc: u16,
    pub reg: u8,
    pub ok: Iv,
    pub ext: Extent,
}

impl PartialEq for Guard {
    fn eq(&self, o: &Self) -> bool {
        (self.pc, self.reg, self.ok) == (o.pc, o.reg, o.ok)
    }
}

impl Eq for Guard {}

/// `gs` as guards of tables of kind `of`.
pub(crate) fn of(gs: Vec<Guard>, of: Of) -> Vec<Guard> {
    gs.into_iter()
        .map(|g| Guard {
            ext: Extent { of, ..g.ext },
            ..g
        })
        .collect()
}

/// A computed transfer's targets and the bytes they were read from, or that some path leaves
/// it unresolved.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Jump {
    /// Targets, the bytes they were read from, the index guards, and per target read from a
    /// table the address of its low byte.
    Targets(Vec<u16>, Vec<u16>, Vec<Guard>, Vec<(u16, u16)>),
    Fail,
}

/// The analysis results index and value sets come from: the states of every context per
/// instruction start, their join, the loops' induction variables, the predecessors of each
/// start and the entries.
pub(crate) struct Sets<'a> {
    pub(crate) states: FxHashMap<u16, Vec<&'a St>>,
    /// The context of each of `states`.
    pub(crate) cx: FxHashMap<u16, Vec<u32>>,
    /// Per return site, the states of the calls returning to it, by context.
    pub(crate) calls: FxHashMap<u16, Vec<(u32, &'a St)>>,
    /// Whether a register held as its function's entry value takes the values of the calls
    /// entering the context ([`Sets::values`]).
    pub(crate) entered: bool,
    pub(crate) merged: &'a FxHashMap<u16, St>,
    pub(crate) ind: &'a [Induction],
    pub(crate) preds: FxHashMap<u16, Vec<u16>>,
    pub(crate) roots: &'a [u16],
    /// The values of the bytes stores write ([`cell_values`]) and the address intervals of
    /// the stores of unknown address, once found.
    pub(crate) mem: Option<(Vals, Vec<Iv>)>,
}

/// Per byte, the values it may hold (`None`: any).
pub(crate) type Vals = FxHashMap<u16, Option<BTreeSet<u8>>>;

impl Sets<'_> {
    /// The values data register `r` may hold in state `s` of context `c`, and whether they
    /// are bounded: its value set, else its interval cut by its known bits, and (with
    /// [`Sets::entered`]) where it holds its function's entry value of a register plus a
    /// constant, by the values that register holds in the calls entering the context
    /// (bounded where they all are).
    fn values(&self, c: u32, s: &St, r: usize, seen: &mut Vec<u32>) -> ([bool; 0x100], bool) {
        let mut ok = [false; 0x100];
        let vals = s.vals(r);
        vals.values().for_each(|x| ok[x as usize] = true);
        let mut bounded = s.env[r] != Iv::full(1) || s.set[r].is_some() && ok.iter().any(|&x| !x);
        let calls = u16::try_from(c).ok().and_then(|c| self.calls.get(&c));
        let rel = s.rel[r].filter(|_| self.entered && !seen.contains(&c));
        if let (Some((b, off)), Some(cs)) = (rel, calls) {
            seen.push(c);
            let (mut up, mut all) = ([false; 0x100], true);
            for &(c2, s2) in cs {
                let (v, bd) = self.values(c2, s2, b as usize, seen);
                all &= bd;
                (0..0x100usize)
                    .filter(|&x| v[x])
                    .for_each(|x| up[(x + off as usize) & 0xff] = true);
            }
            seen.pop();
            ok.iter_mut().zip(up).for_each(|(o, u)| *o &= u);
            bounded |= all;
        }
        (ok, bounded)
    }
    /// Per context at `pc`, the values of data register `r` ([`Sets::values`]).
    fn each(&self, pc: u16, r: usize) -> impl Iterator<Item = ([bool; 0x100], bool)> + '_ {
        let states = self.states.get(&pc).map_or(&[][..], Vec::as_slice);
        let cx = self.cx.get(&pc).map_or(&[][..], Vec::as_slice);
        states
            .iter()
            .zip(cx)
            .map(move |(s, &c)| self.values(c, s, r, &mut Vec::new()))
    }
    /// The values data register `r` may hold at `pc`, bounded or not ([`Sets::values`]).
    pub(crate) fn any(&self, pc: u16, r: usize) -> Vec<u8> {
        let mut ok = [false; 0x100];
        for (v, _) in self.each(pc, r) {
            ok.iter_mut().zip(v).for_each(|(o, x)| *o |= x);
        }
        (0..0x100).filter(|&x| ok[x]).map(|x| x as u8).collect()
    }
    /// The values data register `r` may hold at `pc` where it holds a byte of the loaded
    /// program ([`St::src`](crate::vsa::St)) in every context: those the byte may hold at each
    /// address the context gives it, its entry byte and the values of every store that may
    /// write it.
    pub(crate) fn held(&self, code: &Code, pc: u16, r: usize) -> Option<Vec<u8>> {
        let (vals, unknown) = self.mem.as_ref()?;
        let mut ok = [false; 0x100];
        let states = self.states.get(&pc)?;
        for (s, &c) in states.iter().zip(&self.cx[&pc]) {
            let (b, x) = s.src[r]?;
            let xs: Vec<u32> = match x {
                NOREG => vec![0],
                x => {
                    let v = self.values(c, s, x as usize, &mut Vec::new()).0;
                    (0..0x100).filter(|&i| v[i as usize]).collect()
                }
            };
            for a in xs.into_iter().map(|i| b.wrapping_add(i as u16)) {
                let seen = code.observed.contains(a as usize);
                if seen || unknown.iter().any(|i| i.meets(a as u32, a as u32 + 1)) {
                    return None;
                }
                match vals.get(&a) {
                    Some(v) => v.as_ref()?.iter().for_each(|&v| ok[v as usize] = true),
                    None => ok[code.img[a as usize] as usize] = true,
                }
            }
        }
        Some((0..0x100).filter(|&x| ok[x]).map(|x| x as u8).collect())
    }
    /// The values data register `r` may hold at `pc` ([`Sets::values`]) cut by the loops'
    /// trip bounds; `None` (unbounded) when neither bounds them.
    pub(crate) fn of(&self, pc: u16, r: usize) -> Option<Vec<u8>> {
        let mut ok = [false; 0x100];
        let mut bounded = true;
        for (v, b) in self.each(pc, r) {
            ok.iter_mut().zip(v).for_each(|(o, x)| *o |= x);
            bounded &= b;
        }
        for i in self.ind.iter().filter(|i| i.var == Place::Reg(r as u8)) {
            let (Some(t), Some(&e)) = (i.trips, i.at.get(&pc)) else {
                continue;
            };
            bounded = true;
            let mut on = [false; 0x100];
            for s0 in i.start.lo..=i.start.hi {
                for k in 0..t {
                    on[((s0 + e as u32 + k * i.step as u32) & 0xff) as usize] = true;
                }
            }
            for (a, b) in ok.iter_mut().zip(on) {
                *a &= b;
            }
        }
        bounded.then(|| (0..0x100).filter(|&x| ok[x]).map(|x| x as u8).collect())
    }
}

/// Where tables end: the least address each base the code accesses (a constant operand,
/// indexed or not) is accessed at, the instruction bytes and the end of the loaded program.
pub(crate) struct Objects {
    starts: Vec<(u32, u16)>,
    pub(crate) code: Vec<u32>,
    end: u32,
    /// Per table base, the bases the code also reads with the same index value: the columns
    /// of one table.
    columns: FxHashMap<u16, Vec<u16>>,
}

impl Objects {
    pub(crate) fn new(
        code: &Code,
        vars: &FxHashMap<u16, Vec<Var>>,
        merged: &FxHashMap<u16, St>,
        bytes: &FixedBitSet,
        loaded: Option<(u32, u32)>,
        states: &FxHashMap<u16, Vec<&St>>,
    ) -> Objects {
        let mut first: FxHashMap<u16, u32> = FxHashMap::default();
        let mut acc = Vec::new();
        let at = |acc: &mut Vec<_>, t: &Template, b, r: Option<usize>, x: u32| {
            *acc = indexed(t, b, r.map(|r| (r, Iv::exact(x))));
        };
        for (&pc, vs) in vars {
            let env = merged.get(&pc).map_or(TOP_ENV, |s| s.env);
            for v in vs {
                let t = Template::of(v.op);
                let b = operand_bytes(code.img, code.cells, pc, t.len);
                let r = [reg::X, reg::Y].into_iter().find(|&r| moves(t, b, r));
                let lo = r.map_or(0, |r| env[r].lo);
                at(&mut acc, t, b, r, 0);
                let base: Vec<Option<u32>> = acc
                    .iter()
                    .map(|a| a.addr.value().filter(|_| !a.dummy))
                    .collect();
                at(&mut acc, t, b, r, lo);
                for (bs, a) in base.into_iter().zip(&acc) {
                    if let (Some(bs), Some(st)) = (bs, a.addr.value()) {
                        let e = first.entry(bs as u16).or_insert(st);
                        *e = (*e).min(st);
                    }
                }
            }
        }
        let mut starts: Vec<(u32, u16)> = first.into_iter().map(|(b, s)| (s, b)).collect();
        starts.sort_unstable();
        // A table load whose register still holds the index another tag was read with reads
        // another column of the same table.
        let mut columns: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
        for (&pc, vs) in vars {
            for v in vs {
                let Some(Tab::T { reg: r, .. }) = crate::vsa::table(code, pc, v.op) else {
                    continue;
                };
                let Some(b) = load_addr(code, pc, v.op, r, 0).filter(|_| r != NOREG) else {
                    continue;
                };
                for st in states.get(&pc).into_iter().flatten() {
                    let held = st.tag.iter().chain(&st.push).flatten();
                    for t in held.chain(st.mem.iter().filter_map(|e| e.1.as_ref())) {
                        if let Tab::T {
                            reg, ix: Ix::Live, ..
                        } = *t
                        {
                            if let (true, Some(o)) = (reg == r, base(code, *t)) {
                                if o != b {
                                    columns.entry(b).or_default().push(o);
                                    columns.entry(o).or_default().push(b);
                                }
                            }
                        }
                    }
                }
            }
        }
        for c in columns.values_mut() {
            c.sort_unstable();
            c.dedup();
        }
        Objects {
            starts,
            code: bytes.ones().map(|a| a as u32).collect(),
            end: loaded.map_or(0x10000, |l| l.1),
            columns,
        }
    }
    /// Base `b` and the bases the code reads with the same index value: one table's columns.
    pub(crate) fn table_of(&self, b: u16) -> Vec<u16> {
        let mut t = vec![b];
        t.extend(self.columns.get(&b).into_iter().flatten());
        t.sort_unstable();
        t
    }
    /// The first address from `s` on that holds an instruction byte or lies past the loaded
    /// program, or past `s` at which the code starts accessing a base other than `bases`, and
    /// which of them it is.
    fn end(&self, s: u32, bases: &[u16]) -> (u32, By) {
        let i = self.starts.partition_point(|e| e.0 <= s);
        let o = self.starts[i..].iter().find(|e| !bases.contains(&e.1));
        let c = self.code[self.code.partition_point(|&a| a < s)..].first();
        [
            o.map(|e| (e.0, By::Object)),
            c.map(|&a| (a, By::Code)),
            Some((0x10000, By::Wrap)),
            Some((self.end.max(s), By::Loaded)),
        ]
        .into_iter()
        .flatten()
        .min_by_key(|e| e.0)
        .unwrap()
    }
    /// How many of the index values `xs` (ascending) read every column `bases` inside its
    /// extent: from its first byte to its end ([`Objects::end`], the table's other columns,
    /// those the code reads with the same index value too, not counted, and the first of
    /// `stops` (ascending) past its first byte), and no byte another column read at an
    /// earlier value. A prefix of `xs`; with fewer than all, the extent that ended it.
    pub(crate) fn valid(&self, bases: &[u16], xs: &[u8], stops: &[u32]) -> (usize, Option<Extent>) {
        let Some(&x0) = xs.first() else {
            return (0, None);
        };
        let mut same = bases.to_vec();
        for b in bases {
            same.extend(self.columns.get(b).into_iter().flatten());
        }
        let ends: Vec<(u32, By)> = bases
            .iter()
            .map(|&b| {
                let s = b as u32 + x0 as u32;
                let stop = stops[stops.partition_point(|&t| t <= s)..].first();
                let e = self.end(s, &same);
                match stop {
                    Some(&t) if t < e.0 => (t, By::Target),
                    _ => e,
                }
            })
            .collect();
        let mut claimed: FxHashMap<u32, usize> = FxHashMap::default();
        for (n, &x) in xs.iter().enumerate() {
            let at: Vec<u32> = bases.iter().map(|&b| b as u32 + x as u32).collect();
            let bad = at.iter().enumerate().find_map(|(j, &a)| {
                let ext = |end, by| Extent {
                    of: Of::Data,
                    base: bases[j],
                    end,
                    by,
                    store: None,
                };
                if a >= ends[j].0 {
                    return Some(ext(ends[j].0, ends[j].1));
                }
                claimed
                    .get(&a)
                    .filter(|&&c| bases[c] != bases[j])
                    .map(|_| ext(a, By::Column))
            });
            if bad.is_some() {
                return (n, bad);
            }
            for (j, a) in at.into_iter().enumerate() {
                claimed.insert(a, j);
            }
        }
        (xs.len(), None)
    }
}

/// The accesses of template `t` (operand bytes `b`) with the index registers zero but for
/// `set`'s register, which holds its interval.
pub(crate) fn indexed(t: &Template, b: [Option<u8>; 2], set: Option<(usize, Iv)>) -> Vec<Access> {
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    if let Some((r, v)) = set {
        env[r] = v;
    }
    let mut acc = Vec::new();
    absint(t.ops(), b, &mut env, &mut acc);
    acc
}

/// Whether register `r` moves an address of template `t` (operand bytes `b`).
fn moves(t: &Template, b: [Option<u8>; 2], r: usize) -> bool {
    indexed(t, b, Some((r, Iv::full(1))))
        .iter()
        .zip(indexed(t, b, None))
        .any(|(a, z)| z.addr.value().is_some() && !a.dummy && a.addr.value().is_none())
}

/// The values data register `r` holds at `pc`, from the definitions reaching it; `None` when
/// an entry reaches it without one or a definition may leave any byte.
fn reaching(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    pc: u16,
    r: usize,
) -> Option<BTreeSet<u8>> {
    let mut out = BTreeSet::new();
    let mut seen: FxHashSet<u16> = FxHashSet::default();
    let mut work = vec![pc];
    while let Some(p) = work.pop() {
        if sets.roots.contains(&p) {
            return None;
        }
        for &q in sets.preds.get(&p).into_iter().flatten() {
            if !seen.insert(q) {
                continue;
            }
            let st = sets.merged.get(&q)?;
            for v in &vars[&q] {
                if !crate::objects::writes(v.op, r) {
                    work.push(q);
                    continue;
                }
                let s = crate::vsa::step(code, q, v, st, &mut Vec::new());
                let vals = s.vals(r);
                if s.env[r] == Iv::full(1) && s.set[r].is_none() {
                    return None;
                }
                out.extend(vals.values().map(|x| x as u8));
            }
        }
    }
    Some(out)
}

/// Whether index register `r` moves a store address of template `t` (operand bytes `b`).
pub(crate) fn addresses(t: &Template, b: [Option<u8>; 2], r: usize) -> bool {
    indexed(t, b, Some((r, Iv::full(1))))
        .iter()
        .any(|a| a.store && a.addr.value().is_none())
}

/// The data register access `i` of template `t` stores, when it stores one as the
/// instruction found it.
pub(crate) fn stored_reg(t: &Template, i: usize) -> Option<usize> {
    let mut written = 0u32;
    let mut n = 0;
    for o in t.ops() {
        if matches!(o.opc, Opc::Load | Opc::Store) {
            if n == i {
                let v = o.ins[1];
                return (o.opc == Opc::Store
                    && v.space() == Space::Reg
                    && (v.off() as usize) < 3
                    && written >> v.off() & 1 == 0)
                    .then_some(v.off() as usize);
            }
            n += 1;
        }
        if o.out.space() == Space::Reg {
            written |= 1 << o.out.off();
        }
    }
    None
}

/// `base` with each register of `idx` at its digit of `k` in the mixed radix of their
/// value lists: the `k`th of every combination of their values.
fn index_env(
    mut env: crate::analysis::Env,
    idx: &[(usize, Vec<u8>)],
    mut k: usize,
) -> crate::analysis::Env {
    for (r, xs) in idx {
        env[*r] = Iv::exact(xs[k % xs.len()] as u32);
        k /= xs.len();
    }
    env
}

/// Whether template `t` reads register `r` before it writes it.
fn reads(t: &Template, r: usize) -> bool {
    let is = |v: &Vn| v.space() == Space::Reg && v.off() as usize == r;
    for o in t.ops() {
        if o.inputs().iter().any(is) {
            return true;
        }
        if is(&o.out) {
            return false;
        }
    }
    false
}

/// The index register (`X` before `Y`) template `t` reads, if any.
pub(crate) fn index_reg(t: &Template) -> Option<usize> {
    [reg::X, reg::Y].into_iter().find(|&r| reads(t, r))
}

/// The address the one data load of opcode `op` at `pc` reads with index register `r`
/// ([`NOREG`]: none) holding `x`, when that fixes it.
pub(crate) fn load_addr(code: &Code, pc: u16, op: u8, r: u8, x: u8) -> Option<u16> {
    let t = Template::of(op);
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    let set = (r != NOREG).then_some((r as usize, Iv::exact(x as u32)));
    let acc = indexed(t, b, set);
    let [l] = acc.iter().filter(|a| !a.dummy).collect::<Vec<_>>()[..] else {
        return None;
    };
    l.addr.value().map(|a| a as u16)
}

/// The pointer bytes of a `JMP (ind)` with a constant operand at `pc` (the high byte from the
/// pointer's own page).
pub(crate) fn pointer(code: &Code, pc: u16) -> Option<(u16, u16)> {
    let [Some(lo), Some(hi)] = operand_bytes(code.img, code.cells, pc, 3) else {
        return None;
    };
    let w = u16::from_le_bytes([lo, hi]);
    Some((w, di_pcode::ind_hi(w)))
}

/// The hull of the pointer bytes of a `JMP (w)`.
pub(crate) fn ind_iv(w: u16) -> Iv {
    let h = di_pcode::ind_hi(w);
    Iv {
        lo: w.min(h) as u32,
        hi: w.max(h) as u32,
    }
}

/// How many operand bytes give the target of `f` when they are cells: a `JMP`/`JSR`'s two,
/// a branch's offset, else none.
pub(crate) fn operand_len(f: &Flow) -> u16 {
    match f {
        Flow::Jmp(Tg::Calc(_)) | Flow::Jsr { t: Tg::Calc(_), .. } => 2,
        Flow::Br { t: Tg::Calc(_), .. } => 1,
        _ => 0,
    }
}

/// The operand bytes giving the target of the instruction at `pc` ([`operand_len`]).
pub(crate) fn target_operands(vs: &[Var], pc: u16) -> impl Iterator<Item = u16> {
    let n = vs.iter().map(|v| operand_len(&v.flow)).max().unwrap_or(0);
    (1..=n).map(move |i| pc.wrapping_add(i))
}

/// The bytes whose contents the analysis tracks: the pointers of `JMP (ind)`s and of
/// `(zp),Y` accesses, the cell operand bytes of instructions, the bytes outside `dev` both
/// loaded and stored at a constant address, and the canonical device registers whose values
/// reads depend on that a store at a constant address writes.
pub(crate) fn vectors(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    vars: &FxHashMap<u16, Vec<Var>>,
    dev: &FixedBitSet,
    devices: &crate::dev::Devices,
) -> Vec<u16> {
    let none = crate::dev::Devices::default();
    let ran = crate::once::Ran::default();
    let known = FxHashMap::default();
    let code = Code::new(img, cells, &known, &none, &ran);
    let mut v: Vec<u16> = vars
        .iter()
        .filter(|(_, vs)| vs.iter().any(|v| matches!(v.flow, Flow::JmpInd(_))))
        .filter_map(|(&pc, _)| pointer(&code, pc))
        .flat_map(|(lo, hi)| [lo, hi])
        .collect();
    v.extend(vars.iter().flat_map(|(&pc, vs)| target_operands(vs, pc)));
    let mut acc = Vec::new();
    let (mut loads, mut stores) = (FixedBitSet::with_capacity(0x10000), FixedBitSet::new());
    stores.grow(0x10000);
    for (&pc, vs) in vars {
        for var in vs {
            let t = Template::of(var.op);
            let mut env = TOP_ENV;
            acc.clear();
            let b = operand_bytes(img, cells, pc, t.len);
            v.extend(
                (0..t.len as u16 - 1)
                    .filter(|&i| b[i as usize].is_none())
                    .map(|i| pc + i + 1),
            );
            absint(t.ops(), b, &mut env, &mut acc);
            for p in acc.iter().filter_map(|a| a.ptr) {
                if let (Some(q), Some(h)) =
                    (p.lo.and_then(super::analysis::Iv::value), p.hi.value())
                {
                    v.extend([q as u16, h as u16]);
                }
            }
            for a in &acc {
                if let Some(m) = a.addr.value().filter(|_| !a.dummy) {
                    if a.store { &mut stores } else { &mut loads }.insert(m as usize);
                }
            }
        }
    }
    let held: FxHashSet<u16> = devices.held().collect();
    v.extend(
        (stores.ones())
            .filter_map(|a| devices.canon(a as u16))
            .filter(|c| held.contains(c)),
    );
    loads.intersect_with(&stores);
    loads.difference_with(dev);
    v.extend(loads.ones().map(|a| a as u16));
    v.sort_unstable();
    v.dedup();
    v
}

/// A store's addresses and values so far (`None`: unknown).
type Seen = (Option<BTreeSet<u16>>, Option<BTreeSet<u8>>);

/// Every store of `vars`, sorted by instruction and access.
pub(crate) fn stores(code: &Code, vars: &FxHashMap<u16, Vec<Var>>, sets: &Sets) -> Vec<Store> {
    let mut out = Vec::new();
    for (&pc, vs) in vars {
        for v in vs.iter().filter(|v| v.acc.iter().any(|a| a.store)) {
            let t = Template::of(v.op);
            let b = operand_bytes(code.img, code.cells, pc, t.len);
            out.extend(var_stores(code, vars, sets, pc, v, &[b]));
        }
    }
    out.sort_unstable_by_key(|s| (s.pc, s.op, s.ai));
    out
}

/// The stores of variant `v` at `pc` over each choice `bs` of its operand bytes.
fn var_stores(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    pc: u16,
    v: &Var,
    bs: &[[Option<u8>; 2]],
) -> Vec<Store> {
    let base = sets.merged.get(&pc).map_or(TOP_ENV, |s| s.env);
    let t = Template::of(v.op);
    let mut acc = Vec::new();
    let mut found: FxHashMap<usize, Seen> = v
        .acc
        .iter()
        .enumerate()
        .filter(|a| a.1.store)
        .map(|(i, _)| (i, (Some(BTreeSet::new()), Some(BTreeSet::new()))))
        .collect();
    let mut held: FxHashMap<usize, Option<BTreeSet<u8>>> = FxHashMap::default();
    for &b in bs {
        let idx: Option<Vec<(usize, Vec<u8>)>> = [reg::X, reg::Y]
            .into_iter()
            .filter(|&r| reads(t, r) && addresses(t, b, r))
            .map(|r| sets.of(pc, r).map(|s| (r, s)))
            .collect();
        let Some(idx) = idx else {
            for f in found.values_mut() {
                *f = (None, None);
            }
            break;
        };
        let n: usize = idx.iter().map(|e| e.1.len()).product();
        for k in 0..n {
            let mut env = index_env(base, &idx, k);
            acc.clear();
            absint(t.ops(), b, &mut env, &mut acc);
            for (i, a) in acc.iter().enumerate().filter(|a| a.1.store) {
                let (addrs, vals) = found.get_mut(&i).unwrap();
                match (a.addr.value(), addrs.as_mut()) {
                    (Some(x), Some(s)) => {
                        s.insert(x as u16);
                    }
                    _ => *addrs = None,
                }
                let Some(s) = vals.as_mut() else { continue };
                if let Some(x) = a.val.value() {
                    s.insert(x as u8);
                    continue;
                }
                let range = |iv: Iv| {
                    (iv != Iv::full(1)).then(|| (iv.lo..=iv.hi).map(|x| x as u8).collect())
                };
                let from = held.entry(i).or_insert_with(|| match stored_reg(t, i) {
                    Some(r) => reaching(code, vars, sets, pc, r).or_else(|| range(base[r])),
                    None => range(a.val),
                });
                match from {
                    Some(f) => s.extend(f.iter()),
                    None => *vals = None,
                }
            }
        }
    }
    found
        .into_iter()
        .map(|(ai, (addrs, vals))| Store {
            pc,
            op: v.op,
            ai,
            addr: v.acc[ai].addr,
            addrs: addrs.map(|s| s.into_iter().collect()),
            vals,
            deps: Vec::new(),
            pairs: None,
            from: None,
        })
        .collect()
}

/// The address sets of the loads from an operand plus index registers whose index sets are
/// bounded, by `(pc, opcode, access index)`: the address for each value of their index
/// sets, each evaluated exactly (a load through a pointer or a cell operand has none).
pub(crate) fn indexed_loads(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
) -> FxHashMap<(u16, u8, usize), Vec<u16>> {
    let mut out = FxHashMap::default();
    let mut acc = Vec::new();
    for (&pc, vs) in vars {
        let base = sets.merged.get(&pc).map_or(TOP_ENV, |s| s.env);
        for v in vs {
            let t = Template::of(v.op);
            let b = operand_bytes(code.img, code.cells, pc, t.len);
            let loads = |a: &Access| !a.store && !a.dummy && a.ptr.is_none();
            if b[..t.len as usize - 1].iter().any(Option::is_none) || !v.acc.iter().any(loads) {
                continue;
            }
            let idx: Option<Vec<(usize, Vec<u8>)>> = [reg::X, reg::Y]
                .into_iter()
                .filter(|&r| reads(t, r) && moves(t, b, r))
                .map(|r| sets.of(pc, r).map(|s| (r, s)))
                .collect();
            let Some(idx) = idx.filter(|i| !i.is_empty() && i.iter().all(|e| !e.1.is_empty()))
            else {
                continue;
            };
            let mut found: FxHashMap<usize, BTreeSet<u16>> = FxHashMap::default();
            let n: usize = idx.iter().map(|e| e.1.len()).product();
            for k in 0..n {
                let mut env = index_env(base, &idx, k);
                acc.clear();
                absint(t.ops(), b, &mut env, &mut acc);
                for (i, a) in acc.iter().enumerate().filter(|a| loads(a.1)) {
                    if let Some(x) = a.addr.value() {
                        found.entry(i).or_default().insert(x as u16);
                    }
                }
            }
            for (ai, s) in found.into_iter().filter(|e| e.1.len() > 1) {
                out.insert((pc, v.op, ai), s.into_iter().collect());
            }
        }
    }
    out
}

/// The values of the cells ([`cell_values`]) with each store whose address is unknown only
/// through its own operand cells taken at their values: the stores' addresses and the cells'
/// values are found together, from none of those stores writing up to their fixpoint (a store
/// whose operand cells have no values stays unknown). Each round joins what the stores
/// resolved with the cells' values write into those values ([`join_cells`]), so the values
/// only rise in a lattice of finite height and the rounds end, at values that hold of the
/// stores resolved with them.
pub(crate) fn cell_addressed(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    stores: &mut [Store],
) -> FxHashMap<u16, Option<BTreeSet<u8>>> {
    let operand_cells = |s: &Store| {
        let t = Template::of(s.op);
        let b = operand_bytes(code.img, code.cells, s.pc, t.len);
        (0..t.len as usize - 1).any(|j| b[j].is_none())
    };
    let open: Vec<usize> = (0..stores.len())
        .filter(|&i| stores[i].addrs.is_none() && operand_cells(&stores[i]))
        .collect();
    let orig: Vec<Store> = open.iter().map(|&i| stores[i].clone()).collect();
    let rest: Vec<Store> = (0..stores.len())
        .filter(|i| !open.contains(i))
        .map(|i| stores[i].clone())
        .collect();
    let mut cv = cell_values(code, &rest);
    loop {
        for (s0, &i) in orig.iter().zip(&open) {
            stores[i] = resolve(code, vars, sets, s0, &cv).unwrap_or_else(|| s0.clone());
        }
        let new = join_cells(&cv, cell_values(code, stores));
        if new == cv {
            return cv;
        }
        cv = new;
    }
}

/// `a` joined with `b` by cell (one missing holds only its entry byte, which every set
/// holds): sets unite and unknown values absorb.
fn join_cells(
    a: &FxHashMap<u16, Option<BTreeSet<u8>>>,
    mut b: FxHashMap<u16, Option<BTreeSet<u8>>>,
) -> FxHashMap<u16, Option<BTreeSet<u8>>> {
    for (&k, v) in a {
        match (b.entry(k).or_insert_with(|| v.clone()), v) {
            (Some(x), Some(y)) => x.extend(y),
            (x, None) => *x = None,
            (None, _) => {}
        }
    }
    b
}

/// Store `s` with its operand cells taken at their values `cv`, when they all have some.
fn resolve(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    s: &Store,
    cv: &FxHashMap<u16, Option<BTreeSet<u8>>>,
) -> Option<Store> {
    let v = vars.get(&s.pc)?.iter().find(|v| v.op == s.op)?;
    let t = Template::of(v.op);
    let b = operand_bytes(code.img, code.cells, s.pc, t.len);
    let ran = code.ran.get(&s.pc).filter(|o| o[0] == v.op);
    let choice = |j: usize| -> Option<Vec<Option<u8>>> {
        match b[j] {
            Some(x) => Some(vec![Some(x)]),
            None if j + 1 < t.len as usize && ran.is_some() => Some(vec![ran.map(|o| o[j + 1])]),
            None if j + 1 < t.len as usize => {
                let a = s.pc.wrapping_add(1 + j as u16);
                let vs = match cv.get(&a) {
                    Some(v) => v.clone()?,
                    None if code.free.contains(a as usize) => return None,
                    None => [code.img[a as usize]].into(),
                };
                Some(vs.into_iter().map(Some).collect())
            }
            None => Some(vec![None]),
        }
    };
    let (lo, hi) = (choice(0)?, choice(1)?);
    let bs: Vec<[Option<u8>; 2]> = lo
        .iter()
        .flat_map(|&l| hi.iter().map(move |&h| [l, h]))
        .collect();
    var_stores(code, vars, sets, s.pc, v, &bs)
        .into_iter()
        .find(|x| x.ai == s.ai)
        .filter(|x| x.addrs.is_some())
}

/// The bytes a store of `stores` with known addresses may write, those the facts saw written
/// and the volatile bytes. A store of unknown address is checked against every byte the lift
/// takes as constant (`codechk`), tables included.
fn written(code: &Code, stores: &[Store]) -> FixedBitSet {
    let mut w = code.observed.clone();
    w.grow(0x10000);
    w.union_with(&code.dev);
    for a in stores.iter().filter_map(|s| s.addrs.as_ref()) {
        a.iter().for_each(|&x| w.insert(x as usize));
    }
    w
}

/// Per index value (`None`: not indexed, one byte) the byte a tag stands for, and its address
/// (`None`: a constant).
type Bytes = Vec<Byte>;

/// One entry of [`Bytes`]: index value, byte, address.
type Byte = (Option<u8>, u8, Option<u16>);

/// Whether tags `ts` are indexed by one value: one register, still its value or one group.
fn joint(ts: &[Tab]) -> bool {
    let ix = |t: &Tab| match *t {
        Tab::T { reg, ix, .. } if reg != NOREG && ix != Ix::Lost => Some((reg, ix.rel())),
        _ => None,
    };
    ts.len() > 1 && ts.iter().all(|t| ix(t).is_some() && ix(t) == ix(&ts[0]))
}

/// The table base a tag reads at index 0.
fn base(code: &Code, t: Tab) -> Option<u16> {
    match t {
        Tab::T { pc, op, reg, .. } => load_addr(code, pc, op, reg, 0),
        _ => None,
    }
}

/// The loads a tag may have been read by: its own, or for bytes different loads of one table
/// read ([`Ix::Mrg`]) each load of that table whose byte an instruction of the class carries
/// the index of.
fn loads_of(code: &Code, sets: &Sets, t: Tab) -> Vec<u16> {
    let Tab::T { pc, op, reg, ix } = t else {
        return Vec::new();
    };
    let Ix::Mrg(g) = ix else {
        return vec![pc];
    };
    let at = base(code, t);
    let members = (code.classes.iter())
        .filter(|e| *e.1 == g)
        .map(|e| *e.0)
        .chain([g])
        .filter(|&m| (m & 3) as u8 == reg);
    let mut out: BTreeSet<u16> = BTreeSet::new();
    for w in members.map(|m| (m >> 2) as u16) {
        for st in sets.states.get(&w).into_iter().flatten() {
            out.extend(st.tags().filter_map(|t| match *t {
                Tab::T {
                    pc: l,
                    op: o,
                    reg: r,
                    ix: Ix::Live,
                } if (o, r) == (op, reg) && base(code, *t) == at => Some(l),
                _ => None,
            }));
        }
    }
    out.into_iter().collect()
}

/// The bytes tags `ts` stand for, per index value inside their tables' extent
/// ([`Objects::valid`], ending at the first of `stops` too): tags indexed by one value (the
/// same register and group) are columns of one table and pair up value by value, others are
/// read alone. Index guards keep each load inside the extent where the index values every
/// tag of its group reads with reach past it.
fn bytes(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    ts: &[Tab],
    stops: &[u32],
) -> Option<(Vec<Bytes>, Vec<Guard>)> {
    let mut out = Vec::new();
    let mut guards = Vec::new();
    let groups: Vec<&[Tab]> = if joint(ts) {
        vec![ts]
    } else {
        ts.chunks(1).collect()
    };
    let mut per: Vec<Bytes> = Vec::new();
    for g in groups {
        let mut xs: Option<BTreeSet<u8>> = None;
        let mut bases = Vec::new();
        let idx = |pc: u16, reg: u8| {
            let r = reg as usize;
            (sets.of(pc, r).or_else(|| sets.held(code, pc, r))).unwrap_or_else(|| sets.any(pc, r))
        };
        let tag_idx = |t: Tab, reg: u8| -> BTreeSet<u8> {
            (loads_of(code, sets, t).into_iter())
                .flat_map(|l| idx(l, reg))
                .collect()
        };
        for t in g {
            match *t {
                Tab::K(_) => {}
                Tab::Any => return None,
                Tab::T { reg, .. } => {
                    bases.push(base(code, *t)?);
                    if reg != NOREG {
                        let v = tag_idx(*t, reg);
                        xs = Some(match xs {
                            None => v,
                            Some(o) => o.intersection(&v).copied().collect(),
                        });
                    }
                }
            }
        }
        let xs: Vec<Option<u8>> = match xs {
            None => vec![None],
            Some(v) => {
                let v: Vec<u8> = v.into_iter().collect();
                let (n, ext) = objs.valid(&bases, &v, stops);
                if n == 0 {
                    return None;
                }
                let ok = Iv {
                    lo: v[0] as u32,
                    hi: v[n - 1] as u32,
                };
                // An index value one load reads with that another of the group never does
                // pairs with nothing: only one all of them read past the extent is guarded.
                for t in g.iter().filter(|_| n < v.len()) {
                    if let Tab::T { reg, .. } = *t {
                        for pc in loads_of(code, sets, *t) {
                            if idx(pc, reg)
                                .iter()
                                .any(|&x| !(ok.lo..=ok.hi).contains(&(x as u32)))
                            {
                                guards.push(Guard {
                                    pc,
                                    reg,
                                    ok,
                                    ext: ext.unwrap(),
                                });
                            }
                        }
                    }
                }
                v[..n].iter().map(|&x| Some(x)).collect()
            }
        };
        for t in g {
            per.push(match *t {
                Tab::K(k) => vec![(None, k, None)],
                Tab::Any => return None,
                Tab::T { pc, op, reg, .. } => xs
                    .iter()
                    .map(|&x| {
                        let a = load_addr(code, pc, op, reg, x.unwrap_or(0))?;
                        Some((x, code.img[a as usize], Some(a)))
                    })
                    .collect::<Option<Bytes>>()?,
            });
        }
    }
    out.extend(per);
    Some((out, guards))
}

/// Targets, the bytes they were read from, the guards and `(low byte address, target)` of the
/// targets read from tables.
type Found = (
    BTreeSet<u16>,
    BTreeSet<u16>,
    Vec<Guard>,
    BTreeSet<(u16, u16)>,
);

/// The byte pairs of the per index value bytes `l`, `h`: value by value when `joint`, else
/// each with each.
fn pairs<'b>(l: &'b Bytes, h: &'b Bytes, joint: bool) -> Vec<(&'b Byte, &'b Byte)> {
    l.iter()
        .flat_map(|x| {
            h.iter()
                .filter(move |y| !joint || y.0 == x.0)
                .map(move |y| (x, y))
        })
        .collect()
}

/// The bytes of the pair `lo`, `hi` ([`bytes`]); for a transfer to their words plus `to`, a
/// table they index ends at the first target those words give past its first byte: what it
/// goes to is code.
fn word_bytes(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    lo: Tab,
    hi: Tab,
    to: Option<u16>,
) -> Option<(Vec<Bytes>, Vec<Guard>)> {
    let b = bytes(code, sets, objs, &[lo, hi], &[])?;
    let Some(d) = to else { return Some(b) };
    let [l, h] = &b.0[..] else { return None };
    let mut stops: Vec<u32> = pairs(l, h, joint(&[lo, hi]))
        .iter()
        .map(|(x, y)| u16::from_le_bytes([x.1, y.1]).wrapping_add(d) as u32)
        .collect();
    stops.sort_unstable();
    stops.dedup();
    bytes(code, sets, objs, &[lo, hi], &stops)
}

/// The words of the byte pair `lo`, `hi` (see the module documentation), when every byte
/// read is one no store writes; `to` as in [`word_bytes`].
fn pair(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    w: &FixedBitSet,
    (lo, hi): (Tab, Tab),
    to: Option<u16>,
) -> Option<Found> {
    let (b, guards) = word_bytes(code, sets, objs, lo, hi, to)?;
    let [l, h] = &b[..] else { return None };
    let joint = joint(&[lo, hi]);
    if !joint && l.len() > 1 && h.len() > 1 {
        return None;
    }
    let pairs = pairs(l, h, joint);
    let read: BTreeSet<u16> = pairs
        .iter()
        .flat_map(|(x, y)| [x.2, y.2])
        .flatten()
        .collect();
    if read.iter().any(|&a| w.contains(a as usize)) {
        return None;
    }
    let t = pairs
        .iter()
        .map(|(x, y)| u16::from_le_bytes([x.1, y.1]))
        .collect();
    let origin = pairs
        .iter()
        .filter_map(|(x, y)| Some((x.2?, u16::from_le_bytes([x.1, y.1]))))
        .collect();
    Some((t, read, guards, origin))
}

/// What a computed transfer switches on: an index register still holding the index; the byte
/// of the loaded program at `base` plus register `reg` ([`NOREG`]: none) it was read from,
/// read again (its addresses within `[lo, hi]`); or the value index register `r` held before
/// the instruction at `pc` wrote it, carried in a register of the function ([`crate::carry`]).
#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash, PartialOrd, Ord)]
pub enum Index {
    Reg(u8),
    Byte {
        base: u16,
        reg: u8,
        lo: u32,
        hi: u32,
    },
    Carry(u16, u8),
}

/// The index two contexts of a transfer agree on: the same, or the same byte at addresses
/// within either's.
fn meet_ix(a: Index, b: Index) -> Option<Index> {
    match (a, b) {
        _ if a == b => Some(a),
        (
            Index::Byte { base, reg, lo, hi },
            Index::Byte {
                base: c,
                reg: r,
                lo: l,
                hi: h,
            },
        ) if (base, reg) == (c, r) => Some(Index::Byte {
            base,
            reg,
            lo: lo.min(l),
            hi: hi.max(h),
        }),
        _ => None,
    }
}

/// A transfer's selector: its [`Index`] and, per index value, the target, when every byte its
/// target is made of is a constant or a column of one table indexed by that value.
pub type Selector = (Index, Vec<(u8, u16)>);

/// The [`Index`] tags `ts` are read with in state `st`: the one register and index relation
/// every table tag shares (still the register's value, a byte read again, or one group), none
/// when no tag is indexed.
fn index_of(ts: &[Tab], st: &St) -> Option<Index> {
    let mut one = None;
    for t in ts {
        match *t {
            Tab::K(_) | Tab::T { reg: NOREG, .. } => {}
            Tab::T { reg, ix, .. } => {
                let ix = ix.rel();
                if one.replace((reg, ix)).is_some_and(|o| o != (reg, ix)) {
                    return None;
                }
            }
            Tab::Any => return None,
        }
    }
    match one? {
        (r, Ix::Live) => Some(Index::Reg(r)),
        (_, Ix::Byte(base, reg, _)) => {
            let i = match reg {
                NOREG => Iv::exact(0),
                r => st.env[r as usize],
            };
            Some(Index::Byte {
                base,
                reg,
                lo: base as u32 + i.lo,
                hi: base as u32 + i.hi,
            })
        }
        (_, Ix::Grp(g)) if g != NOGRP => Some(Index::Carry((g >> 2) as u16, (g & 3) as u8)),
        _ => None,
    }
}

/// The [`Selector`] of a transfer to `word` of the bytes tagged `ts` (`to` as in
/// [`word_bytes`] for a pair), when every byte read is one no store writes and each index
/// value selects one target.
fn selector(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    w: &FixedBitSet,
    (ts, to, st): (&[Tab], Option<u16>, &St),
    word: impl Fn(&[u8]) -> u16,
) -> Option<Selector> {
    let ix = index_of(ts, st)?;
    let (b, _) = match ts {
        &[lo, hi] => word_bytes(code, sets, objs, lo, hi, to)?,
        _ => bytes(code, sets, objs, ts, &[])?,
    };
    if b.iter()
        .flatten()
        .any(|x| x.2.is_some_and(|a| w.contains(a as usize)))
    {
        return None;
    }
    let n = b.iter().map(Vec::len).max()?;
    let mut out = (0..n)
        .map(|j| {
            let row: Vec<&Byte> = (b.iter())
                .map(|c| c.get(j).or_else(|| c.first().filter(|_| c.len() == 1)))
                .collect::<Option<_>>()?;
            let x = row.iter().find_map(|e| e.0)?;
            Some((x, word(&row.iter().map(|e| e.1).collect::<Vec<_>>())))
        })
        .collect::<Option<Vec<_>>>()?;
    out.sort_unstable();
    out.dedup();
    out.windows(2)
        .all(|p| p[0].0 != p[1].0)
        .then_some((ix, out))
}

/// The selectors two contexts of one jump agree on: the same index, and one target per
/// index value.
pub(crate) fn meet_sel(a: Option<Selector>, b: Option<Selector>) -> Option<Selector> {
    let ((r, mut x), (q, y)) = (a?, b?);
    let r = meet_ix(r, q)?;
    x.extend(y);
    x.sort_unstable();
    x.dedup();
    x.windows(2).all(|w| w[0].0 != w[1].0).then_some((r, x))
}

/// The words the `BRK` vector `$FFFE`/`$FFFF` may hold when `BRK` runs: of a byte stores
/// with known addresses write, the values they store (a vector the program sets before it
/// runs `BRK`; any other deopts at the `BRK`), else its constant byte; none when a store of
/// unknown address or values may write it.
fn vector(code: &Code, stores: &[Store]) -> Option<Found> {
    let mut read = BTreeSet::new();
    let byte = |a: u16, read: &mut BTreeSet<u16>| -> Option<BTreeSet<u8>> {
        let mut out = BTreeSet::new();
        for s in stores
            .iter()
            .filter(|s| s.addr.meets(a as u32, a as u32 + 1))
        {
            match (&s.addrs, &s.vals) {
                (Some(x), Some(v)) if x.contains(&a) => out.extend(v),
                (Some(_), _) => {}
                _ => return None,
            }
        }
        if out.is_empty() {
            if code.free.contains(a as usize) {
                return None;
            }
            read.insert(a);
            out.insert(code.img[a as usize]);
        }
        Some(out)
    };
    let (lo, hi) = (byte(0xfffe, &mut read)?, byte(0xffff, &mut read)?);
    let targets = lo
        .iter()
        .flat_map(|&l| hi.iter().map(move |&h| u16::from_le_bytes([l, h])))
        .collect();
    Some((targets, read, Vec::new(), BTreeSet::new()))
}

/// Per computed transfer resolved or failed in some state of `state`, its resolution; an
/// `RTS`/`RTI` returning through its caller's address in every state is absent. A `JMP`/`JSR`
/// through cell operands goes to the words its operand bytes hold where they are tagged on
/// every path, else to every combination of the values of its cells ([`cell_values`]), when
/// each is known.
pub(crate) fn jumps(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    state: &FxHashMap<Key, St>,
    sets: &Sets,
    objs: &Objects,
    stores: &[Store],
    cellvals: &FxHashMap<u16, Option<BTreeSet<u8>>>,
) -> (FxHashMap<u16, Jump>, FxHashMap<u16, Selector>) {
    let w = written(code, stores);
    let mut span = (u16::MAX, 0u16);
    for (&pc, vs) in vars {
        for v in vs {
            let end = pc.wrapping_add(Template::of(v.op).len as u16 - 1);
            span = (span.0.min(pc), span.1.max(end));
        }
    }
    let constant = |a: u16| !w.contains(a as usize) && (span.0..=span.1).contains(&a);
    // An `RTS` at its function's entry depth in a call returning to `cx` whose return
    // address bytes are not those the `JSR` pushed: inline arguments it returns past.
    let past = |cx: u32, st: &St| {
        let Ok(r) = u16::try_from(cx) else {
            return false;
        };
        let [lo, hi] = r.wrapping_sub(1).to_le_bytes();
        st.depth == Some(0)
            && st.above.iter().all(Option::is_some)
            && st.above != [Some(Tab::K(lo)), Some(Tab::K(hi))]
    };
    // Per site: `Some(Some(_))` jumps, `Some(None)` returns, `None` failed.
    let mut res: FxHashMap<u16, Option<Option<Found>>> = FxHashMap::default();
    let mut sels: FxHashMap<u16, Option<Selector>> = FxHashMap::default();
    let mut keys: Vec<&Key> = state.keys().collect();
    keys.sort_unstable();
    for key in keys {
        let (pc, st) = (key.0, &state[key]);
        let tag = |a: u16| st.mem.iter().find(|e| e.0 == a).and_then(|e| e.1);
        let opnd = |a: u16| {
            if code.cells.contains(a as usize) {
                tag(a)
            } else {
                Some(Tab::K(code.img[a as usize]))
            }
        };
        for v in &vars[&pc] {
            let le = |b: &[u8]| u16::from_le_bytes([b[0], b[1]]);
            let sel = |ts: &[Option<Tab>], to: u16, word: &dyn Fn(&[u8]) -> u16| {
                let ts: Vec<Tab> = ts.iter().copied().collect::<Option<_>>()?;
                selector(code, sets, objs, &w, (&ts, Some(to), st), word)
            };
            let sel = match v.flow {
                Flow::JmpInd(_) => {
                    pointer(code, pc).and_then(|(lo, hi)| sel(&[tag(lo), tag(hi)], 0, &le))
                }
                Flow::Br { t: Tg::Calc(_), f } => sel(&[opnd(pc.wrapping_add(1))], 0, &|b| {
                    f.wrapping_add(b[0] as i8 as u16)
                }),
                ref f if operand_len(f) == 2 => sel(
                    &[opnd(pc.wrapping_add(1)), opnd(pc.wrapping_add(2))],
                    0,
                    &le,
                ),
                Flow::Rts(_) => match st.push {
                    [Some(l), Some(h), _] => {
                        sel(&[Some(l), Some(h)], 1, &|b| le(b).wrapping_add(1))
                    }
                    _ => None,
                },
                Flow::Rti(_) => match st.push {
                    [Some(_), Some(l), Some(h)] => sel(&[Some(l), Some(h)], 0, &le),
                    _ => None,
                },
                _ => None,
            };
            let e = sels.entry(pc).or_insert_with(|| sel.clone());
            *e = meet_sel(e.take(), sel);
            let r = match v.flow {
                Flow::JmpInd(_) => {
                    pointer(code, pc).and_then(|(lo, hi)| match (tag(lo), tag(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, objs, &w, (l, h), Some(0)).map(Some),
                        _ if constant(lo) && constant(hi) => Some(Some((
                            BTreeSet::from([u16::from_le_bytes([
                                code.img[lo as usize],
                                code.img[hi as usize],
                            ])]),
                            BTreeSet::from([lo, hi]),
                            Vec::new(),
                            BTreeSet::new(),
                        ))),
                        _ => None,
                    })
                }
                Flow::Br { t: Tg::Calc(_), f } => opnd(pc.wrapping_add(1)).and_then(|t| {
                    let (b, g) = bytes(code, sets, objs, &[t], &[])?;
                    let read: BTreeSet<u16> = b[0].iter().filter_map(|x| x.2).collect();
                    if read.iter().any(|&a| w.contains(a as usize)) {
                        return None;
                    }
                    let to = b[0].iter().map(|x| f.wrapping_add(x.1 as i8 as u16));
                    let origin = b[0]
                        .iter()
                        .filter_map(|x| Some((x.2?, f.wrapping_add(x.1 as i8 as u16))))
                        .collect();
                    Some(Some((to.collect(), read, of(g, Of::Branch), origin)))
                }),
                ref f if operand_len(f) == 2 => {
                    let (lo, hi) = (pc.wrapping_add(1), pc.wrapping_add(2));
                    match (opnd(lo), opnd(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, objs, &w, (l, h), Some(0)).map(Some),
                        _ => None,
                    }
                }
                Flow::Rts(_) => match st.push {
                    [None, None, _] if past(key.1, st) => match st.above {
                        [Some(l), Some(h)] => {
                            pair(code, sets, objs, &w, (l, h), Some(1)).map(|(t, b, g, o)| {
                                let o =
                                    o.into_iter().map(|(a, x)| (a, x.wrapping_add(1))).collect();
                                Some((t.into_iter().map(|x| x.wrapping_add(1)).collect(), b, g, o))
                            })
                        }
                        _ => None,
                    },
                    [None, None, _] => Some(None),
                    [Some(l), Some(h), _] => {
                        pair(code, sets, objs, &w, (l, h), Some(1)).map(|(t, b, g, o)| {
                            let o = o.into_iter().map(|(a, x)| (a, x.wrapping_add(1))).collect();
                            Some((t.into_iter().map(|x| x.wrapping_add(1)).collect(), b, g, o))
                        })
                    }
                    _ => None,
                },
                Flow::Brk(_) => vector(code, stores).map(Some),
                Flow::Rti(_) => match st.push {
                    [None, None, None] => Some(None),
                    [Some(_), Some(l), Some(h)] => {
                        pair(code, sets, objs, &w, (l, h), Some(0)).map(Some)
                    }
                    _ => None,
                },
                _ => continue,
            };
            let e = res.entry(pc).or_insert_with(|| r.clone());
            *e = match (e.take(), r) {
                (Some(Some(mut a)), Some(Some(b))) => {
                    a.0.extend(b.0);
                    a.1.extend(b.1);
                    a.2.extend(b.2);
                    a.3.extend(b.3);
                    Some(Some(a))
                }
                (Some(None), Some(None)) => Some(None),
                _ => None,
            };
        }
    }
    // A `JMP`/`JSR` or branch whose operand cells are not tagged on every path: their value
    // sets.
    let vals = |a: u16| -> Option<BTreeSet<u8>> {
        if code.cells.contains(a as usize) {
            cellvals.get(&a).cloned().flatten()
        } else {
            Some(BTreeSet::from([code.img[a as usize]]))
        }
    };
    for (&pc, r) in res.iter_mut().filter(|(_, r)| r.is_none()) {
        let fall = vars[&pc].iter().find_map(|v| match v.flow {
            Flow::Br { t: Tg::Calc(_), f } => Some(f),
            _ => None,
        });
        if let (Some(f), Some(o)) = (fall, vals(pc.wrapping_add(1))) {
            let t = o.iter().map(|&x| f.wrapping_add(x as i8 as u16)).collect();
            *r = Some(Some((t, BTreeSet::new(), Vec::new(), BTreeSet::new())));
            continue;
        }
        if !vars[&pc].iter().any(|v| operand_len(&v.flow) == 2) {
            continue;
        }
        if let (Some(l), Some(h)) = (vals(pc.wrapping_add(1)), vals(pc.wrapping_add(2))) {
            let t = l
                .iter()
                .flat_map(|&x| h.iter().map(move |&y| u16::from_le_bytes([x, y])))
                .collect();
            *r = Some(Some((t, BTreeSet::new(), Vec::new(), BTreeSet::new())));
        }
    }
    let sels: FxHashMap<u16, Selector> = sels
        .into_iter()
        .filter(|(pc, _)| matches!(res.get(pc), Some(Some(Some(_)))))
        .filter_map(|(pc, x)| Some((pc, x?)))
        .collect();
    let jumps = res
        .into_iter()
        .filter_map(|(pc, r)| match r {
            Some(Some((t, b, g, o))) => Some((
                pc,
                Jump::Targets(
                    t.into_iter().collect(),
                    b.into_iter().collect(),
                    (g.into_iter())
                        .map(|x| match x.ext.of {
                            Of::Data => Guard {
                                ext: Extent {
                                    of: Of::Jump,
                                    ..x.ext
                                },
                                ..x
                            },
                            _ => x,
                        })
                        .collect(),
                    o.into_iter().collect(),
                ),
            )),
            Some(None) => None,
            None => Some((pc, Jump::Fail)),
        })
        .collect();
    (jumps, sels)
}

/// Address sets for the stores through a `(zp),Y` pointer whose two bytes hold, on every
/// path, table bytes or constants ([`bytes`], inside the tables' extent: the guards that keeps
/// are appended to `guards`, the bytes read to `reads`): each word whose bytes lie in the
/// pointer bytes' intervals plus each value of `Y`; where a loop advances a pointer byte,
/// the sets [`advanced`] gives (`konst` the bytes taken as constant). The tables read must
/// stay unwritten with the new address sets, else the stores stay of unknown address.
#[allow(clippy::too_many_arguments)]
pub(crate) fn pointer_stores(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    stores: &mut [Store],
    guards: &mut Vec<Guard>,
    reads: &mut BTreeSet<u16>,
    konst: &dyn Fn(u32) -> Option<u8>,
) -> Vec<usize> {
    let w = written(code, stores);
    // Per store: its index, addresses, the table bytes read and the guards they need.
    type Hit = (usize, Vec<u16>, BTreeSet<u16>, Vec<Guard>);
    let mut found: Vec<Hit> = Vec::new();
    let mut lp: Option<Loop> = None;
    for (i, s) in stores.iter().enumerate().filter(|(_, s)| s.addrs.is_none()) {
        let Some(var) = vars[&s.pc].iter().find(|v| v.op == s.op) else {
            continue;
        };
        let g = lp.get_or_insert_with(|| Loop::new(code, vars, sets, objs, konst));
        let Some(p) = var.acc.get(s.ai).and_then(|a| a.ptr) else {
            if let Some((a, r, gs)) = operand_set(g, &w, s.pc, var.op) {
                found.push((i, a, r, gs));
            }
            continue;
        };
        if let Some((a, r, gs)) = pointer_set(g, &w, s.pc, p) {
            found.push((i, a, r, gs));
        }
    }
    let mut trial: Vec<Store> = stores.to_vec();
    for (i, a, _, _) in &found {
        trial[*i].addrs = Some(a.clone());
    }
    let w = written(code, &trial);
    let mut done = Vec::new();
    for (i, a, r, g) in found {
        if r.iter().all(|&x| !w.contains(x as usize)) {
            stores[i].addrs = Some(a);
            reads.extend(r);
            guards.extend(g);
            done.push(i);
        }
    }
    done
}

/// The address set of an access at `pc` through the `(zp),Y` pointer `p`, the table bytes it
/// was read from and the guards they need: words of the pointer bytes' tagged table bytes
/// on every path, else the words a loop advancing them gives ([`advanced`]), plus each `Y`.
fn pointer_set(
    g: &Loop,
    w: &FixedBitSet,
    pc: u16,
    p: crate::analysis::Ptr,
) -> Option<(Vec<u16>, BTreeSet<u16>, Vec<Guard>)> {
    let (q, h) = (p.lo.and_then(super::analysis::Iv::value)?, p.hi.value()?);
    let ys = g
        .sets
        .of(pc, reg::Y)
        .unwrap_or_else(|| g.sets.any(pc, reg::Y));
    tagged_in(g, w, pc, (q, h), [None; 2], &ys).or_else(|| {
        advanced(g, pc, [q as u16, h as u16], &ys).map(|(a, r, g)| (a.into_iter().collect(), r, g))
    })
}

/// The address set of the store of opcode `op` at `pc` through its cell operand bytes, where
/// they hold tagged table bytes (a byte that is not a cell, its constant) on every path into
/// it ([`tagged_in`]), plus each value of its index register.
fn operand_set(
    g: &Loop,
    w: &FixedBitSet,
    pc: u16,
    op: u8,
) -> Option<(Vec<u16>, BTreeSet<u16>, Vec<Guard>)> {
    let t = Template::of(op);
    let b = operand_bytes(g.code.img, g.code.cells, pc, t.len);
    if t.len != 3 || b.iter().all(Option::is_some) {
        return None;
    }
    let ys = match index_reg(t) {
        Some(r) => g.sets.of(pc, r)?,
        None => vec![0],
    };
    let (q, h) = (pc.wrapping_add(1) as u32, pc.wrapping_add(2) as u32);
    tagged_in(g, w, pc, (q, h), b, &ys).filter(|x| read_only(g.code, &x.1))
}

/// Whether every byte of `bytes` is one the lift takes as constant and no cell.
fn read_only(code: &Code, bytes: &BTreeSet<u16>) -> bool {
    (bytes.iter()).all(|&a| code.constant.contains(a as usize) && !code.cells.contains(a as usize))
}

/// The words of the bytes `q, h` where they hold tagged table bytes on every path into `pc`
/// (the bytes `fixed` gives constant), plus each of `ys`; the table bytes read and the guards
/// they need.
fn tagged_in(
    g: &Loop,
    w: &FixedBitSet,
    pc: u16,
    (q, h): (u32, u32),
    fixed: [Option<u8>; 2],
    ys: &[u8],
) -> Option<(Vec<u16>, BTreeSet<u16>, Vec<Guard>)> {
    let (code, sets, objs) = (g.code, g.sets, g.objs);
    let states = sets.states.get(&pc)?;
    let mut addrs = BTreeSet::new();
    let mut read = BTreeSet::new();
    let mut gs = Vec::new();
    let tagged = states.iter().all(|st| {
        let at = |a: u32| match fixed[(a == h) as usize] {
            Some(k) => Some((a as u16, Some(Tab::K(k)), Iv::exact(k as u32))),
            None => st.mem.iter().find(|e| e.0 as u32 == a).copied(),
        };
        let (Some((_, Some(l), li)), Some((_, Some(hb), hv))) = (at(q), at(h)) else {
            return false;
        };
        let Some((words, r, g, _)) = pair(code, sets, objs, w, (l, hb), None) else {
            return false;
        };
        let inside = |x: u32, iv: Iv| (iv.lo..=iv.hi).contains(&x);
        for word in words {
            let [lo, hi] = word.to_le_bytes();
            if inside(lo as u32, li) && inside(hi as u32, hv) {
                addrs.extend(ys.iter().map(|&y| word.wrapping_add(y as u16)));
            }
        }
        read.extend(r);
        gs.extend(g);
        true
    });
    tagged.then(|| (addrs.into_iter().collect(), read, gs))
}

/// The table bytes and guards the words of a pointer pair's seeds were read with, by the
/// pair's low byte.
pub(crate) type SeedReads = FxHashMap<u8, (BTreeSet<u16>, Vec<Guard>)>;

/// The seeds of the `(zp),Y` pointer pairs ([`crate::vsa::Code::seeds`]): per store at an
/// exact address into a byte of a pair after which, in every context, both bytes hold tagged
/// table bytes or constants, the words they make ([`pair`]: inside the tables' extents, read
/// from constant bytes no store with an address set writes) inside the bytes' intervals, at most
/// [`crate::vsa::CAP`]; and per pair whose tables hold at most that many entries, the bytes
/// read and the guards they need, each of an index the code does not bound (one it bounds
/// reads the words it reads), whatever words the bytes make.
pub(crate) fn pointer_seeds(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    stores: &[Store],
) -> (crate::vsa::Seeds, SeedReads) {
    let w = written(code, stores);
    let pairs: BTreeSet<u8> = (vars.iter())
        .flat_map(|(&pc, vs)| vs.iter().filter_map(move |v| code.zpy(pc, v.op)))
        .collect();
    let mut seeds = crate::vsa::Seeds::default();
    let mut reads = SeedReads::default();
    if pairs.is_empty() {
        return (seeds, reads);
    }
    let mut acc = Vec::new();
    for (&pc, vs) in vars {
        let Some(states) = sets.states.get(&pc) else {
            continue;
        };
        for v in vs {
            let at = (v.acc.iter())
                .filter(|a| a.store && !a.dummy)
                .filter_map(|a| a.addr.value().filter(|&m| m < 0x100));
            let qs: BTreeSet<u8> = at
                .flat_map(|m| [m as u8, (m as u8).wrapping_sub(1)])
                .filter(|q| pairs.contains(q))
                .collect();
            for q in qs {
                let mut words = BTreeSet::new();
                let (mut read, mut gs) = (BTreeSet::new(), Vec::new());
                let ok = states.iter().all(|st| {
                    acc.clear();
                    let s = crate::vsa::step(code, pc, v, st, &mut acc);
                    let at = |a: u8| s.mem.iter().find(|e| e.0 == a as u16).copied();
                    let (Some((_, Some(l), li)), Some((_, Some(h), hv))) =
                        (at(q), at(q.wrapping_add(1)))
                    else {
                        return false;
                    };
                    let Some((ws, r, g, _)) = pair(code, sets, objs, &w, (l, h), None) else {
                        return false;
                    };
                    if !read_only(code, &r)
                        || g.iter().any(|x| sets.of(x.pc, x.reg as usize).is_some())
                    {
                        return false;
                    }
                    let inside = |x: u8, iv: Iv| (iv.lo..=iv.hi).contains(&(x as u32));
                    words.extend(ws.into_iter().filter(|w| {
                        let [lo, hi] = w.to_le_bytes();
                        inside(lo, li) && inside(hi, hv)
                    }));
                    read.extend(r);
                    gs.extend(g);
                    true
                });
                if !ok || read.len() > 2 * crate::vsa::CAP {
                    continue;
                }
                let e = reads.entry(q).or_default();
                e.0.extend(read);
                e.1.extend(gs);
                if !words.is_empty() && words.len() <= crate::vsa::CAP {
                    seeds.insert((pc, v.op), (q, words.into_iter().collect()));
                }
            }
        }
    }
    (seeds, reads)
}

/// Address sets of the loads through `(zp),Y` pointers ([`pointer_set`]) whose tables no
/// store writes and that need no index guard beyond `guards`, by `(pc, opcode, access
/// index)`; the tables join `reads`.
#[allow(clippy::too_many_arguments)]
pub(crate) fn pointer_loads(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    stores: &[Store],
    guards: &[Guard],
    reads: &mut BTreeSet<u16>,
    konst: &dyn Fn(u32) -> Option<u8>,
) -> FxHashMap<(u16, u8, usize), Vec<u16>> {
    let w = written(code, stores);
    let g = Loop::new(code, vars, sets, objs, konst);
    let mut out = FxHashMap::default();
    for (&pc, vs) in vars {
        for v in vs {
            for (ai, a) in v.acc.iter().enumerate() {
                let Some(p) = a.ptr.filter(|_| !a.store && !a.dummy) else {
                    continue;
                };
                // A load's set needs no guard of its own: one needing an index guard the
                // stores' sets do not already take (a table read past its extent) is not
                // taken.
                if let Some((s, r, gs)) = pointer_set(&g, &w, pc, p) {
                    let guarded = gs.iter().all(|x| guards.contains(x));
                    if !s.is_empty() && guarded && r.iter().all(|&x| !w.contains(x as usize)) {
                        reads.extend(r);
                        out.insert((pc, v.op, ai), s);
                    }
                }
            }
        }
    }
    out
}

/// Bytes known exactly while evaluating a straight chain of instructions, by address.
type Known = FxHashMap<u16, u8>;

/// Run the one variant at `pc` on `env` and the bytes `mem` holds (else those `konst` gives,
/// each appended to `read`): a store at an exact address sets its byte, any other forgets
/// those it may reach.
fn run(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    pc: u16,
    env: &mut crate::analysis::Env,
    mem: &mut Known,
    konst: &dyn Fn(u32) -> Option<u8>,
    read: &mut Vec<u16>,
) -> Vec<Access> {
    let t = Template::of(vars[&pc][0].op);
    let b = operand_bytes(code.img, code.cells, pc, t.len);

    let bi: [Iv; 2] = std::array::from_fn(|i| {
        let a = pc.wrapping_add(i as u16 + 1) as u32;
        b[i].or_else(|| mem.get(&(a as u16)).copied())
            .map_or(Iv::full(1), |x| Iv::exact(x as u32))
    });
    let seen = std::cell::RefCell::new(Vec::new());
    let load = |a: Iv| {
        let x = a.value()?;
        if let Some(&v) = mem.get(&(x as u16)) {
            return Some(Iv::exact(v as u32));
        }
        let v = konst(x)?;
        seen.borrow_mut().push(x as u16);
        Some(Iv::exact(v as u32))
    };
    let mut acc = Vec::new();
    crate::analysis::absint_ranges(t.ops(), b, bi, env, &mut acc, &load);
    read.extend(seen.into_inner());
    for a in acc.iter().filter(|a| a.store) {
        match (a.addr.value(), a.val.value()) {
            (Some(x), Some(v)) => {
                mem.insert(x as u16, v as u8);
            }
            _ => mem.retain(|&m, _| !a.addr.meets(m as u32, m as u32 + 1)),
        }
    }
    acc
}

/// Address sets for a store at `pc` through the `(zp),Y` pointer bytes `ptr` that a loop
/// advances, evaluated per value of the index its tables are read with ([`advanced_at`]):
/// the first loop (by head) whose head dominates `pc` that gives one.
fn advanced(
    g: &Loop,
    pc: u16,
    ptr: [u16; 2],
    ys: &[u8],
) -> Option<(BTreeSet<u16>, BTreeSet<u16>, Vec<Guard>)> {
    let doms: FxHashSet<u32> = g.dom.dominators(pc as u32)?.collect();
    g.heads
        .iter()
        .filter(|&&h| doms.contains(&(h as u32)))
        .find_map(|&h| advanced_at(g, h, pc, ptr, ys))
}

/// What [`advanced_at`] reads: the program, its successors, dominators (from a root above the
/// entries) and loop heads.
struct Loop<'a> {
    code: &'a Code<'a>,
    vars: &'a FxHashMap<u16, Vec<Var>>,
    sets: &'a Sets<'a>,
    objs: &'a Objects,
    konst: &'a dyn Fn(u32) -> Option<u8>,
    succs: FxHashMap<u16, Vec<u16>>,
    dom: Dominators<u32>,
    heads: Vec<u16>,
}

impl<'a> Loop<'a> {
    fn new(
        code: &'a Code<'a>,
        vars: &'a FxHashMap<u16, Vec<Var>>,
        sets: &'a Sets<'a>,
        objs: &'a Objects,
        konst: &'a dyn Fn(u32) -> Option<u8>,
    ) -> Self {
        const ROOT: u32 = 0x10000;
        let mut succs: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
        let mut graph: DiGraphMap<u32, ()> = DiGraphMap::new();
        for &r in sets.roots {
            graph.add_edge(ROOT, r as u32, ());
        }
        for (&s, ps) in &sets.preds {
            for &p in ps {
                succs.entry(p).or_default().push(s);
                graph.add_edge(p as u32, s as u32, ());
            }
        }
        let mut heads: Vec<u16> = sets.ind.iter().map(|i| i.head).collect();
        heads.sort_unstable();
        heads.dedup();
        Loop {
            code,
            vars,
            sets,
            objs,
            konst,
            succs,
            dom: simple_fast(&graph, ROOT),
            heads,
        }
    }
}

/// The starts reachable from `from` along `next` without passing `avoid`.
fn reach(from: &[u16], next: &FxHashMap<u16, Vec<u16>>, avoid: u16) -> FxHashSet<u16> {
    let mut seen: FxHashSet<u16> = from.iter().copied().filter(|&n| n != avoid).collect();
    let mut work: Vec<u16> = seen.iter().copied().collect();
    while let Some(n) = work.pop() {
        for &m in next.get(&n).into_iter().flatten() {
            if m != avoid && seen.insert(m) {
                work.push(m);
            }
        }
    }
    seen
}

/// Address sets for a store at `pc` through the pointer bytes `ptr`, given the loop at `h`:
/// its head dominates `pc`, it is entered by one edge at the end of a straight chain, and no
/// instruction on a path from the head to `pc` outside the loop writes the pointer bytes.
/// On the chain the pointer bytes (and the loop's counter) are table bytes of one index value,
/// or constants; per value of that index the chain runs exactly to the head, the loop's
/// induction variables then start at known values ([`crate::scev`], their trip bound with
/// them), and each pointer byte the loop advances takes its start plus the loop's changes to
/// it over the trips, the others their value at the head. Stores through the pointer are taken
/// not to write its bytes, as the set found misses zero page and the stack.
fn advanced_at(
    g: &Loop,
    h: u16,
    pc: u16,
    ptr: [u16; 2],
    ys: &[u8],
) -> Option<(BTreeSet<u16>, BTreeSet<u16>, Vec<Guard>)> {
    let (sets, succs) = (g.sets, &g.succs);
    let through = |a: &crate::analysis::Access| {
        a.store
            && a.ptr.is_some_and(|p| {
                p.lo.and_then(super::analysis::Iv::value) == Some(ptr[0] as u32)
                    && p.hi.value() == Some(ptr[1] as u32)
            })
    };
    let body: FxHashSet<u16> = sets
        .ind
        .iter()
        .filter(|i| i.head == h)
        .flat_map(|i| i.with.keys().copied())
        .chain([h])
        .collect();
    let [p] = sets.preds[&h]
        .iter()
        .copied()
        .filter(|p| !body.contains(p))
        .collect::<Vec<_>>()[..]
    else {
        return None;
    };
    let writes = |n: u16| {
        g.vars[&n].iter().flat_map(|v| &v.acc).any(|a| {
            a.store && !through(a) && ptr.iter().any(|&b| a.addr.meets(b as u32, b as u32 + 1))
        })
    };
    let between = &reach(&[h], succs, p) & &reach(&[pc], &sets.preds, p);
    if between.iter().any(|&n| !body.contains(&n) && writes(n)) {
        return None;
    }
    let mut chain = vec![p];
    while let [m] = sets.preds.get(chain.last()?).map_or(&[][..], |v| &v[..]) {
        if succs.get(m).map_or(0, Vec::len) != 1
            || g.vars[m].len() != 1
            || sets.roots.contains(m)
            || body.contains(m)
            || chain.contains(m)
        {
            break;
        }
        chain.push(*m);
    }
    chain.reverse();
    // The loop alone, entered from `p`, with stores through the pointer kept off its bytes.
    let mut local: FxHashMap<u16, Vec<u16>> = body
        .iter()
        .map(|&n| (n, succs.get(&n).cloned().unwrap_or_default()))
        .collect();
    local.insert(p, vec![h]);
    let mut vars: FxHashMap<u16, Vec<Var>> = local
        .iter()
        .flat_map(|(&n, ss)| ss.iter().copied().chain([n]))
        .filter_map(|n| Some((n, g.vars.get(&n)?.clone())))
        .collect();
    for a in vars.values_mut().flatten().flat_map(|v| v.acc.iter_mut()) {
        if through(a) {
            a.addr = Iv {
                lo: 0x200,
                hi: 0xffff,
            };
        }
    }
    let rel = |t: &Tab| match *t {
        Tab::T { ix: Ix::Mrg(_), .. } => None,
        Tab::T { reg, ix, .. } => Some((reg, ix)),
        _ => None,
    };
    let at_p = sets.merged.get(&p)?;
    let want = ptr.iter().find_map(|&b| {
        at_p.mem
            .iter()
            .find(|e| e.0 == b)
            .and_then(|e| e.1.as_ref())
            .and_then(rel)
    })?;
    (0..chain.len()).rev().find_map(|d| {
        let st = sets.merged.get(&chain[d])?;
        let mut places: Vec<(Option<usize>, u16, Tab)> = Vec::new();
        for (r, t) in st.tag.iter().enumerate() {
            if let Some(t) = t.filter(|t| rel(t) == Some(want)) {
                places.push((Some(r), 0, t));
            }
        }
        for &(a, t, _) in &st.mem {
            if let Some(t) = t.filter(|t| rel(t) == Some(want)) {
                places.push((None, a, t));
            }
        }
        let tags: Vec<Tab> = places.iter().map(|e| e.2).collect();
        let (rows, guards) = bytes(g.code, sets, g.objs, &tags, &[]).filter(|r| !r.0.is_empty())?;
        let mut addrs = BTreeSet::new();
        let mut read: Vec<u16> = rows.iter().flatten().filter_map(|x| x.2).collect();
        for i in 0..rows[0].len() {
            let mut env = st.env;
            let mut mem: Known = st
                .mem
                .iter()
                .filter_map(|e| Some((e.0, e.2.value()? as u8)))
                .collect();
            for ((r, a, _), col) in places.iter().zip(&rows) {
                match r {
                    Some(r) => env[*r] = Iv::exact(col[i].1 as u32),
                    None => {
                        mem.insert(*a, col[i].1);
                    }
                }
            }
            for &n in &chain[d..chain.len() - 1] {
                run(g.code, g.vars, n, &mut env, &mut mem, g.konst, &mut read);
            }
            let mut st2: FxHashMap<u16, St> = body
                .iter()
                .filter_map(|n| Some((*n, sets.merged.get(n)?.clone())))
                .collect();
            let mut entry = at_p.clone();
            entry.env = env;
            st2.insert(p, entry);
            run(g.code, g.vars, p, &mut env, &mut mem, g.konst, &mut read);
            let heads = [h].into_iter().collect();
            let ivs = crate::scev::find(
                g.code,
                &vars,
                &heads,
                &local,
                &st2,
                &[p],
                g.konst,
                &mut read,
            );
            let exact = ivs
                .iter()
                .all(|i| matches!(i.var, Place::Mem(_)) || i.start.value().is_some());
            let t = ivs.first().and_then(|iv| iv.trips).filter(|_| exact)?;
            let vals = |b: u16| -> Option<BTreeSet<u8>> {
                let s0 = *mem.get(&b)?;
                let Some(iv) = ivs.iter().find(|i| i.var == Place::Mem(b)) else {
                    return Some(BTreeSet::from([s0]));
                };
                let steps = (0..t).map(|j| (j * iv.step as u32) as u8);
                let offs: BTreeSet<u8> = steps
                    .flat_map(|j| iv.at.values().chain(&[0]).map(move |&a| a.wrapping_add(j)))
                    .collect();
                Some(offs.into_iter().map(|o| s0.wrapping_add(o)).collect())
            };
            let (lo, hi) = (vals(ptr[0])?, vals(ptr[1])?);
            for &l in &lo {
                for &hb in &hi {
                    let word = u16::from_le_bytes([l, hb]);
                    addrs.extend(ys.iter().map(|&y| word.wrapping_add(y as u16)));
                }
            }
        }
        if addrs.iter().any(|&a| a < 0x200) {
            return None;
        }
        Some((addrs, read.into_iter().collect(), guards))
    })
}

/// The values each byte some store writes may hold, from its entry byte and the stores that
/// may write it (with the values of the cells they copy, a least fixpoint); `None` where a
/// store of unknown address or values may write it, or its entry byte is not known
/// ([`Code::free`], [`Code::dev`]).
pub(crate) fn cell_values(code: &Code, stores: &[Store]) -> FxHashMap<u16, Option<BTreeSet<u8>>> {
    let mut out: FxHashMap<u16, Option<BTreeSet<u8>>> = FxHashMap::default();
    let init = |a: u16| match code.known.get(&a) {
        Some(v) => Some(v.iter().copied().collect()),
        None => {
            let known = !code.free.contains(a as usize) && !code.dev.contains(a as usize);
            known.then(|| BTreeSet::from([code.img[a as usize]]))
        }
    };
    for s in stores {
        for &a in (s.addrs.iter().flatten())
            .chain(&s.deps)
            .chain(s.from.iter().map(|f| &f.0))
        {
            out.entry(a).or_insert_with(|| init(a));
        }
    }
    // A cell the host's init's lift bounds holds those values where no store writes it.
    for &a in code
        .known
        .keys()
        .filter(|&&a| code.cells.contains(a as usize))
    {
        out.entry(a).or_insert_with(|| init(a));
    }
    let unknown: Vec<&Store> = stores.iter().filter(|s| s.addrs.is_none()).collect();
    for (a, v) in out.iter_mut() {
        if unknown
            .iter()
            .any(|s| s.addr.meets(*a as u32, *a as u32 + 1))
        {
            *v = None;
        }
    }
    for s in stores {
        for &(a, v) in s.pairs.iter().flatten() {
            if let Some(Some(cur)) = out.get_mut(&a) {
                cur.insert(v);
            }
        }
    }
    loop {
        let mut changed = false;
        for s in stores.iter().filter(|s| s.pairs.is_none()) {
            let Some(addrs) = &s.addrs else { continue };
            let mut v = match &s.from {
                Some((c, f)) => (out[c].iter().flatten())
                    .map(|&x| f[x as usize])
                    .collect::<Option<BTreeSet<u8>>>()
                    .filter(|v| {
                        addrs.iter().all(|a| match &out[a] {
                            Some(cur) => aligned(cur.iter().chain(v)),
                            None => true,
                        })
                    }),
                None => s.vals.clone(),
            };
            for d in &s.deps {
                v = match (v, &out[d]) {
                    (Some(mut x), Some(y)) => {
                        x.extend(y);
                        Some(x)
                    }
                    _ => None,
                };
            }
            for a in addrs {
                let cur = out.get_mut(a).unwrap();
                let new = match (cur.clone(), &v) {
                    (Some(mut c), Some(x)) => {
                        c.extend(x);
                        Some(c)
                    }
                    _ => None,
                };
                if new != *cur {
                    *cur = new;
                    changed = true;
                }
            }
        }
        if !changed {
            break;
        }
    }
    out
}

/// Cut the values of each store of a register holding, in every context, the byte a computed
/// transfer the path took read its target from a table at ([`crate::vsa::Arm`]): to the index
/// values inside the table's extent whose word is a target the path took (the guards that
/// keeps are appended to `guards`).
pub(crate) fn arm_values(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    stores: &mut [Store],
    guards: &mut Vec<Guard>,
) {
    let targets = |j: u16| -> Option<Vec<u16>> {
        let [v] = &vars.get(&j)?[..] else { return None };
        Some(match &v.flow {
            Flow::Jsr { t, .. } | Flow::Jmp(t) => t.all().to_vec(),
            Flow::JmpInd(t) => t.clone(),
            _ => return None,
        })
    };
    for s in stores.iter_mut() {
        let Some(r) = stored_reg(Template::of(s.op), s.ai) else {
            continue;
        };
        let mut vals = BTreeSet::new();
        let mut g = Vec::new();
        let ok = sets.states.get(&s.pc).is_some_and(|states| {
            states.iter().all(|st| {
                let (Some(src), Some(a)) = (st.src[r], st.arm) else {
                    return false;
                };
                let by =
                    |t: Tab| matches!(t, Tab::T { ix: Ix::Byte(m, x, _), .. } if (m, x) == src);
                let (Some(ts), Some((b, gs))) =
                    (targets(a.j), bytes(code, sets, objs, &[a.lo, a.hi], &[]))
                else {
                    return false;
                };
                let arms: Vec<u16> = (ts.iter().enumerate())
                    .filter(|(k, _)| a.mask >> k & 1 == 1)
                    .map(|e| *e.1)
                    .collect();
                let j = joint(&[a.lo, a.hi]);
                for x in &b[0] {
                    for y in b[1].iter().filter(|y| !j || y.0 == x.0) {
                        if arms.contains(&u16::from_le_bytes([x.1, y.1])) {
                            let ix = [(a.lo, x.0), (a.hi, y.0)].into_iter().filter(|e| by(e.0));
                            vals.extend(ix.filter_map(|e| e.1));
                        }
                    }
                }
                g.extend(gs);
                (by(a.lo) || by(a.hi)) && (j || b[0].len() == 1 || b[1].len() == 1)
            })
        });
        if ok {
            s.vals = Some(match s.vals.take() {
                Some(v) => v.intersection(&vals).copied().collect(),
                None => vals,
            });
            guards.extend(of(g, Of::Jump));
        }
    }
}

/// The index register and base of an access from a constant base plus an index register:
/// every data access of opcode `op` at `pc` (a read-modify-write's load and store alike)
/// addresses the base with the register zero.
fn indexed_base(code: &Code, pc: u16, op: u8) -> Option<(u8, u16)> {
    let t = Template::of(op);
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    let r = [reg::X, reg::Y].into_iter().find(|&r| moves(t, b, r))?;
    let acc = indexed(t, b, None);
    let mut bases = acc.iter().filter(|a| !a.dummy).map(|a| a.addr.value());
    let b = bases.next()??;
    bases.all(|x| x == Some(b)).then_some((r as u8, b as u16))
}

/// The index guards of the stores (read-modify-writes included) to a constant base plus an
/// index register the code does not bound (neither its index set nor the byte it holds,
/// [`Sets::held`]) whose table's columns ([`Objects::table_of`]) start inside the `loaded`
/// program, at instructions of one variant: the index values written are those before the
/// first at which some column's byte lies past the program where no store with an address set
/// writes (`written`), and a store at a value past them deopts. A load past the program reads
/// out of bounds instead, and a store into code that follows its table inside the program
/// writes out of bounds (`Program.bounded`).
pub(crate) fn access_guards(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    loaded: Option<(u32, u32)>,
    written: &FixedBitSet,
) -> Vec<Guard> {
    let Some((lo, hi)) = loaded else {
        return Vec::new();
    };
    let closes = |a: u32| a > 0xffff || a >= hi && !written.contains(a as usize);
    let mut out = Vec::new();
    for (&pc, vs) in vars
        .iter()
        .filter(|(_, vs)| vs.len() == 1 && vs[0].acc.iter().any(|a| a.store))
    {
        let Some((reg, b)) = indexed_base(code, pc, vs[0].op) else {
            continue;
        };
        let r = reg as usize;
        if sets.of(pc, r).or_else(|| sets.held(code, pc, r)).is_some() {
            continue;
        }
        let xs = sets.any(pc, r);
        let bases = objs.table_of(b);
        let Some(&x0) = xs.first() else { continue };
        if !bases
            .iter()
            .all(|&c| (lo..hi).contains(&(c as u32 + x0 as u32)))
        {
            continue;
        }
        let n = xs
            .iter()
            .position(|&x| bases.iter().any(|&c| closes(c as u32 + x as u32)));
        if let Some(n) = n.filter(|&n| n > 0) {
            let (c, end) = (bases.iter())
                .map(|&c| (c, c as u32 + xs[n] as u32))
                .find(|e| closes(e.1))
                .unwrap();
            out.push(Guard {
                pc,
                reg,
                ok: Iv {
                    lo: x0 as u32,
                    hi: xs[n - 1] as u32,
                },
                ext: Extent {
                    of: Of::Store,
                    base: c,
                    end: end.min(0x10000),
                    by: if end > 0xffff { By::Wrap } else { By::Loaded },
                    store: Some(pc),
                },
            });
        }
    }
    out
}

/// Fill the values of stores into opcodes and the operands of `JMP`/`JSR`s through cells of
/// a register holding a table byte or a cell's byte on every path ([`bytes`], read inside the
/// table's extent: the guards that keeps are appended to `guards`).
pub(crate) fn tag_values(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    objs: &Objects,
    stores: &mut [Store],
    guards: &mut Vec<Guard>,
    reads: &mut BTreeSet<u16>,
) {
    let w = written(code, stores);
    let mut control: FxHashSet<u16> = vars.keys().copied().collect();
    for (&pc, vs) in vars {
        control.extend(target_operands(vs, pc));
    }
    let steers = |s: &Store| {
        s.addrs
            .as_ref()
            .is_some_and(|a| a.iter().any(|x| control.contains(x)))
    };
    // A store steering control with values from an interval (a table's hull) takes its table
    // bytes too: they are exact.
    for s in stores.iter_mut().filter(|s| steers(s)) {
        let Some(r) = stored_reg(Template::of(s.op), s.ai) else {
            continue;
        };
        let Some(states) = sets.states.get(&s.pc) else {
            continue;
        };
        let mut vals = BTreeSet::new();
        let mut deps = Vec::new();
        let mut g = Vec::new();
        let mut read = Vec::new();
        let ok = states.iter().all(|st| {
            if let Some(x) = st.env[r].value() {
                vals.insert(x as u8);
                return true;
            }
            let Some(t) = st.tag[r] else { return false };
            let Some((b, gs)) = bytes(code, sets, objs, &[t], &[]) else {
                return false;
            };
            g.extend(gs);
            b[0].iter().all(|&(_, x, a)| match a {
                Some(a) if code.cells.contains(a as usize) => {
                    deps.push(a);
                    true
                }
                Some(a) if w.contains(a as usize) => false,
                _ => {
                    vals.insert(x);
                    read.extend(a);
                    true
                }
            })
        });
        if ok {
            reads.extend(read);
            s.vals = Some(vals);
            deps.sort_unstable();
            deps.dedup();
            s.deps = deps;
            guards.extend(g.into_iter().map(|x| Guard {
                ext: Extent {
                    of: Of::Cell,
                    store: Some(s.pc),
                    ..x.ext
                },
                ..x
            }));
        }
    }
}

/// Whether the instructions opcodes `ops` make at one address that run on past it all end at
/// one byte.
fn aligned<'a>(ops: impl Iterator<Item = &'a u8>) -> bool {
    let mut ends = ops.filter_map(|&op| {
        let t = Template::of(op);
        match t.ctrl {
            CtrlKind::Jmp | CtrlKind::JmpInd | CtrlKind::Rts | CtrlKind::Rti | CtrlKind::Jam => {
                None
            }
            _ => Some(t.len),
        }
    });
    ends.next().is_none_or(|n| ends.all(|m| m == n))
}

/// Give each store into opcode cells whose value no path bounds the map from one cell's byte
/// to the value it writes, where the straight chain of instructions ending at the store (each
/// the one predecessor of the next, run from the join of the states at the first) computes it
/// from that byte alone: [`cell_values`] then closes the cells' values under the stores' maps.
pub(crate) fn cell_maps(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    sets: &Sets,
    stores: &mut [Store],
    konst: &dyn Fn(u32) -> Option<u8>,
) {
    let opcode = |a: &u16| vars.contains_key(a) && code.cells.contains(*a as usize);
    let open = |s: &Store| {
        s.vals.is_none()
            && s.deps.is_empty()
            && s.pairs.is_none()
            && vars[&s.pc].len() == 1
            && s.addrs.as_ref().is_some_and(|a| a.iter().all(opcode))
    };
    for s in stores.iter_mut().filter(|s| open(s)) {
        let mut chain = vec![s.pc];
        while let [m] = sets
            .preds
            .get(chain.last().unwrap())
            .map_or(&[][..], |v| &v[..])
        {
            if !matches!(
                vars[m][..],
                [Var {
                    flow: Flow::Next(_),
                    ..
                }]
            ) || sets.roots.contains(m)
                || chain.contains(m)
            {
                break;
            }
            chain.push(*m);
        }
        chain.reverse();
        let Some(st) = sets.merged.get(&chain[0]) else {
            continue;
        };
        let eval = |seed: Option<(u16, u8)>, cells: &mut BTreeSet<u16>| {
            let mut env = st.env;
            let mut mem: Known = (st.mem.iter())
                .filter_map(|e| Some((e.0, e.2.value()? as u8)))
                .chain(seed)
                .collect();
            let mut acc = Vec::new();
            for &n in &chain {
                acc = run(code, vars, n, &mut env, &mut mem, konst, &mut Vec::new());
                cells.extend(
                    (acc.iter().filter(|a| !a.store && !a.dummy))
                        .filter_map(|a| a.addr.value())
                        .filter(|&a| code.cells.contains(a as usize))
                        .map(|a| a as u16),
                );
            }
            acc.get(s.ai).and_then(|a| a.val.value()).map(|x| x as u8)
        };
        let mut cells = BTreeSet::new();
        if eval(None, &mut cells).is_some() {
            continue;
        }
        let [c] = cells.into_iter().collect::<Vec<_>>()[..] else {
            continue;
        };
        let f: Vec<Option<u8>> = (0..=255u8)
            .map(|x| eval(Some((c, x)), &mut BTreeSet::new()))
            .collect();
        if f.iter().any(Option::is_some) {
            s.from = Some((c, f));
        }
    }
}
