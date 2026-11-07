//! What the interval analysis tells about computed transfers and stores, from the code alone.
//!
//! **Index sets.** The values an index register may hold at an instruction: the union over
//! its call contexts of its interval cut by its known bits there, cut by the progression
//! `start + k * step` (`k` below the trip bound) of every loop counter it is
//! ([`crate::scev`]). An index whose interval is every byte and that no trip bound limits is
//! unbounded: known bits refine an index set, they do not bound it.
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
//! two bytes are indexed by unrelated values, the transfer is not resolved.

use crate::analysis::{absint, operand_bytes, Access, Flow, Iv, Tg, Var, TOP_ENV};
use crate::scev::{Induction, Place};
use crate::vsa::{Code, Ix, Key, St, Tab, NOREG};
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
}

/// An index guard: the instruction at `pc` deopts before it unless index register `reg` is in
/// `ok` (a table read outside its extent).
pub type Guard = (u16, u8, Iv);

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
    pub(crate) merged: &'a FxHashMap<u16, St>,
    pub(crate) ind: &'a [Induction],
    pub(crate) preds: FxHashMap<u16, Vec<u16>>,
    pub(crate) roots: &'a [u16],
}

impl Sets<'_> {
    /// The values data register `r` may hold at `pc`; `None` (unbounded) when neither its
    /// interval nor a loop's trip bound limits it.
    /// The values data register `r` may hold at `pc`, bounded or not: its intervals cut by
    /// its known bits.
    pub(crate) fn any(&self, pc: u16, r: usize) -> Vec<u8> {
        let none = Vec::new();
        let mut ok = [false; 0x100];
        for s in self.states.get(&pc).unwrap_or(&none) {
            for x in s.kb[r].values(s.env[r]) {
                ok[x as usize] = true;
            }
        }
        (0..0x100).filter(|&x| ok[x]).map(|x| x as u8).collect()
    }
    pub(crate) fn of(&self, pc: u16, r: usize) -> Option<Vec<u8>> {
        let none = Vec::new();
        let states = self.states.get(&pc).unwrap_or(&none);
        let mut ok = [false; 0x100];
        for s in states {
            for x in s.kb[r].values(s.env[r]) {
                ok[x as usize] = true;
            }
        }
        let mut bounded = states.iter().all(|s| s.env[r] != Iv::full(1));
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
            let mut e = TOP_ENV;
            e[reg::X] = Iv::exact(0);
            e[reg::Y] = Iv::exact(0);
            if let Some(r) = r {
                e[r] = Iv::exact(x);
            }
            acc.clear();
            absint(t.ops(), b, &mut e, acc);
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
    /// program, or past `s` at which the code starts accessing a base other than `bases`.
    fn end(&self, s: u32, bases: &[u16]) -> u32 {
        let i = self.starts.partition_point(|e| e.0 <= s);
        let o = self.starts[i..]
            .iter()
            .find(|e| !bases.contains(&e.1))
            .map_or(0x10000, |e| e.0);
        let c = self.code[self.code.partition_point(|&a| a < s)..]
            .first()
            .map_or(0x10000, |&a| a);
        o.min(c).min(self.end.max(s))
    }
    /// How many of the index values `xs` (ascending) read every column `bases` inside its
    /// extent: from its first byte to its end ([`Objects::end`], the table's other columns,
    /// those the code reads with the same index value too, not counted), and no byte another
    /// column read at an earlier value. A prefix of `xs`.
    pub(crate) fn valid(&self, bases: &[u16], xs: &[u8]) -> usize {
        let Some(&x0) = xs.first() else { return 0 };
        let mut same = bases.to_vec();
        for b in bases {
            same.extend(self.columns.get(b).into_iter().flatten());
        }
        let ends: Vec<u32> = bases
            .iter()
            .map(|&b| self.end(b as u32 + x0 as u32, &same))
            .collect();
        let mut claimed: FxHashMap<u32, usize> = FxHashMap::default();
        for (n, &x) in xs.iter().enumerate() {
            let at: Vec<u32> = bases.iter().map(|&b| b as u32 + x as u32).collect();
            let bad = at.iter().enumerate().any(|(j, &a)| {
                a >= ends[j] || claimed.get(&a).is_some_and(|&c| bases[c] != bases[j])
            });
            if bad {
                return n;
            }
            for (j, a) in at.into_iter().enumerate() {
                claimed.insert(a, j);
            }
        }
        xs.len()
    }
}

/// Whether register `r` moves an address of template `t` (operand bytes `b`).
fn moves(t: &Template, b: [Option<u8>; 2], r: usize) -> bool {
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    let mut acc = Vec::new();
    absint(t.ops(), b, &mut env, &mut acc);
    let fixed: Vec<bool> = acc.iter().map(|a| a.addr.value().is_some()).collect();
    env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    env[r] = Iv::full(1);
    acc.clear();
    absint(t.ops(), b, &mut env, &mut acc);
    acc.iter()
        .zip(fixed)
        .any(|(a, f)| f && !a.dummy && a.addr.value().is_none())
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
    let writes = |op: u8| {
        Template::of(op)
            .ops()
            .iter()
            .any(|o| o.out.space() == Space::Reg && o.out.off() as usize == r)
    };
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
                if !writes(v.op) {
                    work.push(q);
                    continue;
                }
                let s = crate::vsa::step(code, q, v, st, &mut Vec::new());
                if s.env[r] == Iv::full(1) {
                    return None;
                }
                out.extend(s.kb[r].values(s.env[r]).map(|x| x as u8));
            }
        }
    }
    Some(out)
}

/// Whether index register `r` moves a store address of template `t` (operand bytes `b`).
fn addresses(t: &Template, b: [Option<u8>; 2], r: usize) -> bool {
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    env[r] = Iv::full(1);
    let mut acc = Vec::new();
    absint(t.ops(), b, &mut env, &mut acc);
    acc.iter().any(|a| a.store && a.addr.value().is_none())
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

/// Whether template `t` reads register `r`.
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
    let mut env = TOP_ENV;
    env[reg::X] = Iv::exact(0);
    env[reg::Y] = Iv::exact(0);
    if r != NOREG {
        env[r as usize] = Iv::exact(x as u32);
    }
    let mut acc = Vec::new();
    absint(
        t.ops(),
        operand_bytes(code.img, code.cells, pc, t.len),
        &mut env,
        &mut acc,
    );
    let [l] = acc.iter().filter(|a| !a.dummy).collect::<Vec<_>>()[..] else {
        return None;
    };
    l.addr.value().map(|a| a as u16)
}

/// The pointer bytes of a `JMP (ind)` with a constant operand at `pc` (the high byte from the
/// pointer's own page).
fn pointer(code: &Code, pc: u16) -> Option<(u16, u16)> {
    let [Some(lo), Some(hi)] = operand_bytes(code.img, code.cells, pc, 3) else {
        return None;
    };
    let w = u16::from_le_bytes([lo, hi]);
    Some((w, (w & 0xff00) | (w.wrapping_add(1) & 0xff)))
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
/// `(zp),Y` accesses, the cell operand bytes of instructions and the bytes both loaded and
/// stored at a constant address.
pub(crate) fn vectors(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    vars: &FxHashMap<u16, Vec<Var>>,
) -> Vec<u16> {
    let code = Code {
        img,
        cells,
        pull_clear: false,
        vecs: Vec::new(),
        observed: FixedBitSet::new(),
        constant: FixedBitSet::new(),
        clip: FxHashMap::default(),
        bound: Vec::new(),
        stored: Vec::new(),
        after: Vec::new(),
        init: 0,
        floor: FxHashMap::default(),
        runs: Vec::new(),
        words: FxHashMap::default(),
    };
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
                if let (Some(q), Some(h)) = (p.lo.and_then(|l| l.value()), p.hi.value()) {
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
    loads.intersect_with(&stores);
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
            let mut env = base;
            let mut q = k;
            for (r, xs) in &idx {
                env[*r] = Iv::exact(xs[q % xs.len()] as u32);
                q /= xs.len();
            }
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
        })
        .collect()
}

/// The values of the cells ([`cell_values`]) with each store whose address is unknown only
/// through its own operand cells taken at their values: the stores' addresses and the cells'
/// values are found together, from none of those stores writing up to their fixpoint (both
/// only grow; a store whose operand cells have no values stays unknown).
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
    let mut cv = cell_values(code.img, &rest);
    loop {
        for (s0, &i) in orig.iter().zip(&open) {
            stores[i] = resolve(code, vars, sets, s0, &cv).unwrap_or_else(|| s0.clone());
        }
        let new = cell_values(code.img, stores);
        if new == cv {
            return cv;
        }
        cv = new;
    }
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
    let choice = |j: usize| -> Option<Vec<Option<u8>>> {
        match b[j] {
            Some(x) => Some(vec![Some(x)]),
            None if j + 1 < t.len as usize => {
                let a = s.pc.wrapping_add(1 + j as u16);
                let vs = match cv.get(&a) {
                    Some(v) => v.clone()?,
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

/// The bytes a store of `stores` with known addresses may write and those the facts saw
/// written. A store of unknown address is checked against every byte the lift takes as
/// constant (`codechk`), tables included.
fn written(code: &Code, stores: &[Store]) -> FixedBitSet {
    let mut w = code.observed.clone();
    w.grow(0x10000);
    for a in stores.iter().filter_map(|s| s.addrs.as_ref()) {
        a.iter().for_each(|&x| w.insert(x as usize));
    }
    w
}

/// Per index value (`None`: not indexed, one byte) the byte a tag stands for, and its address
/// (`None`: a constant).
type Bytes = Vec<(Option<u8>, u8, Option<u16>)>;

/// Whether tags `ts` are indexed by one value: one register, still its value or one group.
fn joint(ts: &[Tab]) -> bool {
    let ix = |t: &Tab| match *t {
        Tab::T { reg, ix, .. } if reg != NOREG && ix != Ix::Lost => Some((reg, ix)),
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

/// The bytes tags `ts` stand for, per index value inside their tables' extent
/// ([`Objects::valid`]): tags indexed by one value (the same register and group) are columns
/// of one table and pair up value by value, others are read alone. Index guards keep each
/// load inside the extent where its index set reaches past it.
fn bytes(code: &Code, sets: &Sets, objs: &Objects, ts: &[Tab]) -> Option<(Vec<Bytes>, Vec<Guard>)> {
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
            sets.of(pc, reg as usize)
                .unwrap_or_else(|| sets.any(pc, reg as usize))
        };
        for t in g {
            match *t {
                Tab::K(_) => {}
                Tab::Any => return None,
                Tab::T { pc, reg, .. } => {
                    bases.push(base(code, *t)?);
                    if reg != NOREG {
                        let v: BTreeSet<u8> = idx(pc, reg).into_iter().collect();
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
                let n = objs.valid(&bases, &v);
                if n == 0 {
                    return None;
                }
                let ok = Iv {
                    lo: v[0] as u32,
                    hi: v[n - 1] as u32,
                };
                for t in g {
                    if let Tab::T { pc, reg, .. } = *t {
                        if idx(pc, reg)
                            .iter()
                            .any(|&x| !(ok.lo..=ok.hi).contains(&(x as u32)))
                        {
                            guards.push((pc, reg, ok));
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

/// The words of the byte pair `lo`, `hi` (see the module documentation), when every byte
/// read is one no store writes.
fn pair(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    w: &FixedBitSet,
    lo: Tab,
    hi: Tab,
) -> Option<Found> {
    let (b, guards) = bytes(code, sets, objs, &[lo, hi])?;
    let [l, h] = &b[..] else { return None };
    let joint = joint(&[lo, hi]);
    if !joint && l.len() > 1 && h.len() > 1 {
        return None;
    }
    let pairs: Vec<_> = l
        .iter()
        .flat_map(|x| {
            h.iter()
                .filter(move |y| !joint || y.0 == x.0)
                .map(move |y| (x, y))
        })
        .collect();
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

/// A jump's selector: the index register and, per index value, the target, when its low and
/// high target bytes are columns of one table both indexed by the register's current value.
pub type Selector = (u8, Vec<(u8, u16)>);

/// The [`Selector`] of target bytes `lo`, `hi` when every byte read is one no store writes
/// (see [`pair`]).
fn selector(
    code: &Code,
    sets: &Sets,
    objs: &Objects,
    w: &FixedBitSet,
    lo: Tab,
    hi: Tab,
) -> Option<Selector> {
    let Tab::T {
        reg, ix: Ix::Live, ..
    } = lo
    else {
        return None;
    };
    if reg == NOREG || !joint(&[lo, hi]) {
        return None;
    }
    let (b, _) = bytes(code, sets, objs, &[lo, hi])?;
    if b.iter()
        .flatten()
        .any(|x| x.2.is_some_and(|a| w.contains(a as usize)))
    {
        return None;
    }
    let words = b[0]
        .iter()
        .zip(&b[1])
        .map(|(l, h)| Some((l.0?, u16::from_le_bytes([l.1, h.1]))))
        .collect::<Option<_>>()?;
    Some((reg, words))
}

/// The selectors two contexts of one jump agree on: the same register, and one target per
/// index value.
fn meet_sel(a: Option<Selector>, b: Option<Selector>) -> Option<Selector> {
    let ((r, mut x), (q, y)) = (a?, b?);
    if r != q {
        return None;
    }
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
            let sel = match v.flow {
                Flow::JmpInd(_) => {
                    pointer(code, pc).and_then(|(lo, hi)| Some((tag(lo)?, tag(hi)?)))
                }
                Flow::Jmp(Tg::Calc(_)) => opnd(pc.wrapping_add(1)).zip(opnd(pc.wrapping_add(2))),
                _ => None,
            }
            .and_then(|(l, h)| selector(code, sets, objs, &w, l, h));
            let e = sels.entry(pc).or_insert_with(|| sel.clone());
            *e = meet_sel(e.take(), sel);
            let r = match v.flow {
                Flow::JmpInd(_) => {
                    pointer(code, pc).and_then(|(lo, hi)| match (tag(lo), tag(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, objs, &w, l, h).map(Some),
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
                    let (b, g) = bytes(code, sets, objs, &[t])?;
                    let read: BTreeSet<u16> = b[0].iter().filter_map(|x| x.2).collect();
                    if read.iter().any(|&a| w.contains(a as usize)) {
                        return None;
                    }
                    let to = b[0].iter().map(|x| f.wrapping_add(x.1 as i8 as u16));
                    let origin = b[0]
                        .iter()
                        .filter_map(|x| Some((x.2?, f.wrapping_add(x.1 as i8 as u16))))
                        .collect();
                    Some(Some((to.collect(), read, g, origin)))
                }),
                ref f if operand_len(f) == 2 => {
                    let (lo, hi) = (pc.wrapping_add(1), pc.wrapping_add(2));
                    match (opnd(lo), opnd(hi)) {
                        (Some(l), Some(h)) => pair(code, sets, objs, &w, l, h).map(Some),
                        _ => None,
                    }
                }
                Flow::Rts(_) => match st.push {
                    [None, None, _] if past(key.1, st) => match st.above {
                        [Some(l), Some(h)] => {
                            pair(code, sets, objs, &w, l, h).map(|(t, b, g, o)| {
                                let o =
                                    o.into_iter().map(|(a, x)| (a, x.wrapping_add(1))).collect();
                                Some((t.into_iter().map(|x| x.wrapping_add(1)).collect(), b, g, o))
                            })
                        }
                        _ => None,
                    },
                    [None, None, _] => Some(None),
                    [Some(l), Some(h), _] => {
                        pair(code, sets, objs, &w, l, h).map(|(t, b, g, o)| {
                            let o = o.into_iter().map(|(a, x)| (a, x.wrapping_add(1))).collect();
                            Some((t.into_iter().map(|x| x.wrapping_add(1)).collect(), b, g, o))
                        })
                    }
                    _ => None,
                },
                Flow::Brk(_) => vector(code, stores).map(Some),
                Flow::Rti(_) => match st.push {
                    [None, None, None] => Some(None),
                    [Some(_), Some(l), Some(h)] => pair(code, sets, objs, &w, l, h).map(Some),
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
    let sels = sels
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
                    g,
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
        let Some(p) = var.acc.get(s.ai).and_then(|a| a.ptr) else {
            continue;
        };
        let g = lp.get_or_insert_with(|| Loop::new(code, vars, sets, objs, konst));
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
    let (q, h) = (p.lo.and_then(|l| l.value())?, p.hi.value()?);
    let (code, sets, objs) = (g.code, g.sets, g.objs);
    let states = sets.states.get(&pc)?;
    let ys = sets.of(pc, reg::Y).unwrap_or_else(|| sets.any(pc, reg::Y));
    let mut addrs = BTreeSet::new();
    let mut read = BTreeSet::new();
    let mut gs = Vec::new();
    let tagged = states.iter().all(|st| {
        let at = |a: u32| st.mem.iter().find(|e| e.0 as u32 == a).copied();
        let (Some((_, Some(l), li)), Some((_, Some(hb), hv))) = (at(q), at(h)) else {
            return false;
        };
        let Some((words, r, g, _)) = pair(code, sets, objs, w, l, hb) else {
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
    if tagged {
        return Some((addrs.into_iter().collect(), read, gs));
    }
    advanced(g, pc, [q as u16, h as u16], &ys).map(|(a, r, g)| (a.into_iter().collect(), r, g))
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

/// The work a run may take: a run stopped early only leaves its stores out.
pub(crate) const STEPS: usize = 1 << 20;

/// An entry's run along its decided path ([`entry_runs`]): the next instruction, the
/// registers, the bytes it stored exactly and the return sites of its open calls.
#[derive(Clone)]
pub(crate) struct Walk {
    pub(crate) pc: u16,
    /// The instruction the last [`Walk::step`] ran.
    pub(crate) at: u16,
    pub(crate) env: crate::analysis::Env,
    mem: Known,
    /// `(return site, SP its JSR ran at)` per open call.
    pub(crate) rets: Vec<(u16, u32)>,
}

impl Walk {
    /// The bytes the run stored at exact addresses and their values.
    pub(crate) fn known(&self) -> FxHashMap<u16, u8> {
        self.mem.clone()
    }

    /// Join run `o`, at the same instruction with the same open calls (else None), into this
    /// one: each register the hull of both, a stored byte known where both know it alike.
    /// Whether this run changed.
    pub(crate) fn join(&mut self, o: &Walk) -> Option<bool> {
        if self.rets != o.rets {
            return None;
        }
        let mut changed = false;
        for (a, b) in self.env.iter_mut().zip(&o.env) {
            let h = crate::vsa::hull(*a, *b);
            changed |= h != *a;
            *a = h;
        }
        let n = self.mem.len();
        self.mem.retain(|m, v| o.mem.get(m) == Some(v));
        Some(changed || self.mem.len() != n)
    }

    pub(crate) fn new(e: &crate::analysis::Entry, entry_sp: Option<u8>) -> Self {
        let mut env = TOP_ENV;
        for (r, vs) in &e.regs {
            if let [x] = vs[..] {
                env[*r] = Iv::exact(x as u32);
            }
        }
        for &(r, x) in &e.set {
            env[r] = Iv::exact(x as u32);
        }
        if let Some(sp) = entry_sp {
            env[di_pcode::reg::SP] = Iv::exact(sp as u32);
        }
        Walk {
            pc: e.pc,
            at: e.pc,
            env,
            mem: Known::default(),
            rets: Vec::new(),
        }
    }

    /// Run the instruction at `pc` ([`run`]) when it has one variant and is no cell: its
    /// opcode, its accesses and whether its successor is decided (then `pc` moves there).
    pub(crate) fn step(
        &mut self,
        code: &Code,
        vars: &FxHashMap<u16, Vec<Var>>,
        konst: &dyn Fn(u32) -> Option<u8>,
        read: &mut Vec<u16>,
    ) -> Option<(u8, Vec<Access>, bool)> {
        let pc = self.pc;
        let v = match vars.get(&pc).map(|vs| &vs[..]) {
            Some([v]) if !code.cells.contains(pc as usize) => v,
            _ => return None,
        };
        // A call's return: its `RTS` at the SP its `JSR` ran at (calls and returns move SP
        // in neither instruction's template), nothing below that SP left pushed.
        let sp = self.env[di_pcode::reg::SP].value();
        let back = self
            .rets
            .last()
            .copied()
            .filter(|&(_, s): &(u16, u32)| sp == Some(s));
        let acc = run(code, vars, pc, &mut self.env, &mut self.mem, konst, read);
        self.at = pc;
        let next = match (&v.flow, Template::of(v.op).ctrl) {
            (Flow::Next(n), _) => Some(*n),
            (Flow::Br { t, f }, CtrlKind::Br { flag, pol }) => {
                match (self.env[flag as usize].value(), t.all()) {
                    (Some(x), [t]) => Some(if (x != 0) == (pol != 0) { *t } else { *f }),
                    _ => None,
                }
            }
            (Flow::Jmp(t), _) => match t.all() {
                [t] => Some(*t),
                _ => None,
            },
            (Flow::Jsr { t, ret }, _) => match (t.all(), sp) {
                ([t], Some(s)) => {
                    self.rets.push((*ret, s));
                    Some(*t)
                }
                _ => None,
            },
            (Flow::Rts(_), _) => back.map(|(r, _)| {
                self.rets.pop();
                r
            }),
            _ => None,
        };
        if let Some(n) = next {
            self.pc = n;
        }
        Some((v.op, acc, next.is_some()))
    }
}

/// Per store `(pc, opcode, access index)`, the `(address, value)` pairs entries' runs made
/// (`None`: a value a run could not know).
pub(crate) type Made = FxHashMap<(u16, u8, usize), Option<BTreeSet<(u16, u8)>>>;

/// The stores each entry makes on the part of its run that every call repeats: from the
/// entry, while every byte it reads is constant (`konst`) or stored earlier in the run, every
/// branch it takes is decided and every store's address is known, the run is the same on
/// every call. Per store `(pc, opcode, access index)` the `(address, value)` pairs it wrote,
/// a value it could not know making it `None`; a store the code can also reach from where a
/// run stops, or that another entry's run does not decide, is left out; and per entry the
/// bytes its run read through `konst`. An entry in `skip` is not run: all its code may run.
pub(crate) fn entry_runs(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    entries: &[crate::analysis::Entry],
    entry_sp: Option<u8>,
    konst: &dyn Fn(u32) -> Option<u8>,
    skip: &FxHashSet<u16>,
) -> (Made, FxHashMap<u16, BTreeSet<u16>>) {
    let mut made: Made = FxHashMap::default();
    let (mut stops, mut reads) = (Vec::new(), FxHashMap::default());
    for e in entries {
        if skip.contains(&e.pc) {
            stops.push(e.pc);
            continue;
        }
        let mut read = Vec::new();
        let mut w = Walk::new(e, entry_sp);
        for _ in 0..STEPS {
            let Some((op, acc, more)) = w.step(code, vars, konst, &mut read) else {
                break;
            };
            if acc.iter().any(|a| a.store && a.addr.value().is_none()) {
                w.pc = w.at;
                break;
            }
            for (ai, a) in acc.iter().enumerate().filter(|(_, a)| a.store) {
                let slot = made.entry((w.at, op, ai)).or_insert(Some(BTreeSet::new()));
                match (slot.as_mut(), a.val.value()) {
                    (Some(s), Some(x)) => {
                        s.insert((a.addr.lo as u16, x as u8));
                    }
                    _ => *slot = None,
                }
            }
            if !more {
                break;
            }
        }
        stops.push(w.pc);
        reads.insert(e.pc, read.into_iter().collect::<BTreeSet<u16>>());
    }
    // What the code can reach from where a run stopped may store again, with other values.
    let mut seen: FxHashSet<u16> = FxHashSet::default();
    let mut work = stops;
    while let Some(p) = work.pop() {
        if seen.insert(p) {
            for v in vars.get(&p).into_iter().flatten() {
                work.extend(crate::analysis::flow_succs(&v.flow));
                if let Flow::Jsr { ret, .. } = v.flow {
                    work.push(ret);
                }
            }
        }
    }
    made.retain(|k, _| !seen.contains(&k.0));
    (made, reads)
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
                p.lo.and_then(|l| l.value()) == Some(ptr[0] as u32)
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
        let (rows, guards) = bytes(g.code, sets, g.objs, &tags).filter(|r| !r.0.is_empty())?;
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
/// store of unknown address or values may write it.
pub(crate) fn cell_values(
    img: &[u8; 0x10000],
    stores: &[Store],
) -> FxHashMap<u16, Option<BTreeSet<u8>>> {
    let mut out: FxHashMap<u16, Option<BTreeSet<u8>>> = FxHashMap::default();
    let init = |a: u16| Some(BTreeSet::from([img[a as usize]]));
    for s in stores {
        for &a in s.addrs.iter().flatten().chain(&s.deps) {
            out.entry(a).or_insert_with(|| init(a));
        }
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
            let mut v = s.vals.clone();
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
            let Some((b, gs)) = bytes(code, sets, objs, &[t]) else {
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
            guards.extend(g);
        }
    }
}
