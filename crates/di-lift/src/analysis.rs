//! Whole-program discovery: reachable instruction variants from the entries, code cells
//! (code bytes the program writes) by a fixpoint over static store analysis and observed
//! writes, and per-access address intervals.

use crate::bank::{Back, BankMap};
use crate::ir::mask;
use di_pcode::{CtrlKind, Op, Opc, Operands, Slot, Space, Template, Vn};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::BTreeSet;

/// An entry point and optional live-in register values (`lift()` register index, values).
#[derive(Clone, Debug, Default)]
pub struct Entry {
    pub pc: u16,
    pub regs: Vec<(usize, Vec<u8>)>,
}

/// What running the program showed: executed instruction starts, executed `(pc, next pc)`
/// transfers, the values stored to each address, the instructions whose accesses reached page
/// 1 with the lowest and highest page-1 address each reached, the stack heights each instruction start was seen at
/// (SP minus SP at its call's entry as integers: pushes negative, an SP set below a wrap far
/// negative), per entry the SP values (after its return address) it was called at, the bank
/// configurations each instruction start ran in, and per entry the port registers
/// `(dir, data)` at its calls.
#[derive(Clone, Debug, Default)]
pub struct Facts {
    pub starts: Vec<u16>,
    pub edges: Vec<(u16, u16)>,
    pub writes: Vec<(u16, Vec<u8>)>,
    pub page1: Vec<(u16, u16, u16)>,
    pub heights: Vec<(u16, Vec<i16>)>,
    pub entry_sp: Vec<(u16, Vec<u8>)>,
    pub banks: Vec<(u16, Vec<u8>)>,
    pub entry_port: Vec<(u16, Vec<(u8, u8)>)>,
}

/// An unsigned interval of a value.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Iv {
    pub lo: u32,
    pub hi: u32,
}

impl Iv {
    pub const fn exact(v: u32) -> Self {
        Iv { lo: v, hi: v }
    }
    pub const fn full(size: u32) -> Self {
        Iv {
            lo: 0,
            hi: mask(size),
        }
    }
    pub fn value(self) -> Option<u32> {
        (self.lo == self.hi).then_some(self.lo)
    }
    pub fn meets(self, lo: u32, hi: u32) -> bool {
        self.lo < hi && lo <= self.hi
    }
}

/// Interval of `opc(a, b)` with an `out`-sized result and `a` of `size0` bytes.
pub fn iv_op(opc: Opc, a: Iv, b: Iv, out: u32, size0: u32) -> Iv {
    let m = mask(out);
    if let (Some(x), Some(y)) = (a.value(), b.value()) {
        return Iv::exact(di_pcode::binop(opc, x, y, mask(size0)) & m);
    }
    let full = Iv::full(out);
    let r = match opc {
        Opc::IntAdd if a.hi as u64 + b.hi as u64 <= m as u64 => Iv {
            lo: a.lo + b.lo,
            hi: a.hi + b.hi,
        },
        Opc::IntSub if a.lo >= b.hi => Iv {
            lo: a.lo - b.hi,
            hi: a.hi - b.lo,
        },
        Opc::IntAnd => Iv {
            lo: 0,
            hi: a.hi.min(b.hi),
        },
        Opc::IntOr => match (a.value(), b.value()) {
            (Some(0), _) => b,
            (_, Some(0)) => a,
            (Some(c), None) | (None, Some(c))
                if {
                    let x = if a.value().is_some() { b } else { a };
                    c != 0 && x.hi < c.isolate_lowest_one()
                } =>
            {
                let x = if a.value().is_some() { b } else { a };
                Iv {
                    lo: x.lo | c,
                    hi: x.hi | c,
                }
            }
            _ => Iv {
                lo: a.lo.max(b.lo),
                hi: m,
            },
        },
        Opc::IntLeft => match b.value() {
            Some(s) if s < 32 && (a.hi as u64) << s <= m as u64 => Iv {
                lo: a.lo << s,
                hi: a.hi << s,
            },
            _ => full,
        },
        Opc::IntRight => match b.value() {
            Some(s) if s < 32 => Iv {
                lo: a.lo >> s,
                hi: a.hi >> s,
            },
            _ => full,
        },
        Opc::IntEqual | Opc::IntNotEqual | Opc::IntLess | Opc::IntLessEqual | Opc::IntCarry => {
            Iv { lo: 0, hi: 1 }
        }
        _ => full,
    };
    if r.hi > m {
        full
    } else {
        r
    }
}

/// Interval of operand slot `slot` of an instruction whose operand bytes are `b` (`None`
/// for a cell).
pub fn slot_iv(slot: Slot, b: [Option<u8>; 2], size: u32) -> Iv {
    let byte = |x: Option<u8>| x.map_or(Iv::full(1), |v| Iv::exact(v as u32));
    let inc = |x: Option<u8>| x.map_or(Iv::full(1), |v| Iv::exact(v.wrapping_add(1) as u32));
    let iv = match slot {
        Slot::Fixed => unreachable!(),
        Slot::B1 => byte(b[0]),
        Slot::Word => match b {
            [Some(lo), Some(hi)] => Iv::exact(u16::from_le_bytes([lo, hi]) as u32),
            [lo, None] => Iv {
                lo: lo.unwrap_or(0) as u32,
                hi: lo.map_or(0xffff, |v| 0xff00 | v as u32),
            },
            [None, Some(hi)] => Iv {
                lo: (hi as u32) << 8,
                hi: (hi as u32) << 8 | 0xff,
            },
        },
        Slot::B1Inc => inc(b[0]),
        Slot::B2Inc => inc(b[1]),
    };
    if iv.hi > mask(size) {
        Iv::full(size)
    } else {
        iv
    }
}

/// Register intervals (the `lift()` file).
pub type Env = [Iv; 16];

pub const TOP_ENV: Env = {
    let mut e = [Iv { lo: 0, hi: 0xff }; 16];
    let mut i = 8;
    while i < 15 {
        e[i] = Iv { lo: 0, hi: 1 };
        i += 1;
    }
    e
};

/// A memory access of an instruction's template: address interval and value interval
/// (stores); `dummy` for a throw-away read, which has no effect on memory.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Access {
    pub store: bool,
    pub addr: Iv,
    pub val: Iv,
    pub dummy: bool,
    /// A store of the value just loaded from the same address.
    pub back: bool,
    /// Every byte of `hi` was stored on every path to the access.
    pub defd: bool,
    /// Zero-page addresses of the pointer high bytes the address is built from, when it is
    /// that pointer, or that pointer plus a byte: if each is at least 2 when read, the
    /// address is not in page 1.
    pub hi: Option<Iv>,
}

impl Access {
    /// Whether the access may reach page 1 given that the zero-page bytes in `prot` hold at
    /// least 2.
    pub fn page1(&self, prot: &FixedBitSet) -> bool {
        self.addr.meets(0x100, 0x200)
            && !self
                .hi
                .is_some_and(|r| (r.lo..=r.hi).all(|a| prot.contains(a as usize)))
    }
}

/// What an address temporary was built from.
#[derive(Clone, Copy, Default)]
enum Tag {
    #[default]
    No,
    /// A byte loaded from the zero-page addresses.
    Byte(Iv),
    /// That byte as a word.
    Word(Iv),
    /// That byte shifted to the high half of a word.
    High(Iv),
    /// The high half with a byte below it: the page is the byte.
    Page(Iv),
    /// A `Page` plus a byte: the page is the byte or the next.
    Near(Iv),
}

/// Abstractly execute `ops` from `env` with operand bytes `b` (`None` for cells); the
/// memory accesses in op order.
pub fn absint(ops: &[Op], b: [Option<u8>; 2], env: &mut Env, acc: &mut Vec<Access>) {
    let mut uniq = [Iv::full(4); di_pcode::UNIQUE_SIZE];
    let mut tag = [Tag::No; di_pcode::UNIQUE_SIZE];
    let mut loaded: Vec<(Vn, Vn)> = Vec::new();
    for op in ops {
        let tg = |v: Vn| match v.space() {
            Space::Unique => tag[v.off() as usize],
            _ => Tag::No,
        };
        let get = |v: Vn, u: &[Iv], env: &Env| -> Iv {
            match v.space() {
                Space::Const if v.slot() == Slot::Fixed => Iv::exact(v.off()),
                Space::Const => slot_iv(v.slot(), b, v.size()),
                Space::Reg => env[v.off() as usize],
                Space::Unique => u[v.off() as usize],
                Space::Ram => Iv::full(v.size()),
            }
        };
        let a = get(op.ins[0], &uniq, env);
        let y = if op.ins[1].is_none() {
            Iv::full(1)
        } else {
            get(op.ins[1], &uniq, env)
        };
        let hi = match tg(op.ins[0]) {
            Tag::Page(r) | Tag::Near(r) => Some(r),
            _ => None,
        };
        let word = op.out.size() == 2;
        let t = match (op.opc, tg(op.ins[0]), tg(op.ins[1])) {
            (Opc::Load, _, _) if a.hi < 0x100 && op.out.size() == 1 => Tag::Byte(a),
            (Opc::IntZext, Tag::Byte(r), _) if word => Tag::Word(r),
            (Opc::IntLeft, Tag::Word(r), _) if word && y.value() == Some(8) => Tag::High(r),
            (Opc::IntOr, Tag::High(r), _) if word && y.hi < 0x100 => Tag::Page(r),
            (Opc::IntOr, _, Tag::High(r)) if word && a.hi < 0x100 => Tag::Page(r),
            (Opc::IntAdd, Tag::Page(r), _) if word && y.hi < 0x100 => Tag::Near(r),
            _ => Tag::No,
        };
        let r = match op.opc {
            Opc::Store => {
                acc.push(Access {
                    store: true,
                    addr: a,
                    val: y,
                    dummy: false,
                    back: loaded.contains(&(op.ins[0], op.ins[1])),
                    defd: false,
                    hi,
                });
                continue;
            }
            Opc::Load => {
                loaded.push((op.ins[0], op.out));
                acc.push(Access {
                    store: false,
                    addr: a,
                    val: Iv::full(1),
                    dummy: op.out.is_none(),
                    back: false,
                    defd: false,
                    hi,
                });
                Iv::full(op.out.size())
            }
            Opc::Copy | Opc::IntZext => a,
            o => iv_op(
                o,
                a,
                get(op.ins[1], &uniq, env),
                op.out.size(),
                op.ins[0].size(),
            ),
        };
        match op.out.space() {
            Space::Reg => env[op.out.off() as usize] = r,
            Space::Unique => {
                uniq[op.out.off() as usize] = r;
                tag[op.out.off() as usize] = t;
            }
            _ => {}
        }
    }
}

/// A transfer target: static, or computed from code cells with its candidate targets.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Tg {
    At(u16),
    Calc(Vec<u16>),
}

impl Tg {
    pub fn all(&self) -> &[u16] {
        match self {
            Tg::At(t) => std::slice::from_ref(t),
            Tg::Calc(v) => v,
        }
    }
}

/// Control of an instruction variant; computed targets carry their candidates.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Flow {
    Next(u16),
    Br { t: Tg, f: u16 },
    Jmp(Tg),
    JmpInd(Vec<u16>),
    Jsr { t: Tg, ret: u16 },
    Rts(Vec<u16>),
    Rti(Vec<u16>),
    Brk(Vec<u16>),
    Jam,
}

/// One decoded instruction variant.
#[derive(Clone, Debug)]
pub struct Var {
    pub op: u8,
    pub flow: Flow,
    /// Accesses of the template's `LOAD`/`STORE` ops in order.
    pub acc: Vec<Access>,
}

/// The discovered program.
#[derive(Debug)]
pub struct Graph {
    /// Variants per instruction start (more than one only at opcode cells).
    pub vars: FxHashMap<u16, Vec<Var>>,
    /// Code bytes the program writes.
    pub cells: FixedBitSet,
    /// Every byte of a reached instruction.
    pub code: FixedBitSet,
    /// Observed transfers per site.
    pub edges: FxHashMap<u16, Vec<u16>>,
    /// The image with instruction bytes as fetched (from ROM where decoded so).
    pub img: Box<[u8; 0x10000]>,
    /// Instruction bytes fetched from ROM (constant, never cells).
    pub rom_code: FixedBitSet,
    /// The configuration each start was decoded in.
    pub cfg: FxHashMap<u16, u8>,
    /// Zero-page bytes whose value is kept at least 2 (see [`crate::ptr`]).
    pub prot: FixedBitSet,
    /// Per entry, the protected bytes some access it reaches reads before its own stores
    /// define them: the entry checks them.
    pub pre: FxHashMap<u16, FixedBitSet>,
    /// Per instruction start the entries (bit `i % 64` for entry `i`) it is reached from.
    pub from: FxHashMap<u16, u64>,
}

/// Stack-pointer effect of a `Next` instruction: pushes and pulls by count, `None` for an
/// instruction that sets SP (TXS, TAS, LAS).
pub fn stack_delta(op: u8) -> Option<i32> {
    match op {
        0x48 | 0x08 => Some(-1),
        0x68 | 0x28 => Some(1),
        0x9a | 0x9b | 0xbb => None,
        _ => Some(0),
    }
}

/// Opcodes whose template accesses are pushes and pulls.
pub fn is_stack_op(op: u8) -> bool {
    matches!(op, 0x48 | 0x08 | 0x68 | 0x28)
}

/// How a run-time page-1 check covers access `i` of `op`'s template.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Check {
    /// Checked before it: no earlier op of the instruction has an effect.
    Own,
    /// Covered by the check of an earlier access to the same address.
    Same,
    /// Not checkable at the instruction boundary.
    No,
}

pub fn page1_check(op: u8, i: usize) -> Check {
    let ops = Template::of(op).ops();
    let mut addrs: Vec<Vn> = Vec::new();
    let mut effect = false;
    for o in ops {
        if matches!(o.opc, Opc::Load | Opc::Store) {
            if addrs.len() == i {
                return match (addrs.contains(&o.ins[0]), effect) {
                    (true, _) => Check::Same,
                    (false, false) => Check::Own,
                    _ => Check::No,
                };
            }
            addrs.push(o.ins[0]);
        }
        addrs.retain(|&a| a != o.out);
        effect |= o.opc == Opc::Store || o.out.space() == Space::Reg;
    }
    Check::No
}

struct Ctx<'a> {
    img: &'a [u8; 0x10000],
    cells: &'a FixedBitSet,
    edges: &'a FxHashMap<u16, Vec<u16>>,
    values: &'a FxHashMap<u16, BTreeSet<u8>>,
    /// The `BRK` vector as the instruction's configuration maps it.
    brk: u16,
}

impl Ctx<'_> {
    fn facts(&self, pc: u16, more: impl IntoIterator<Item = u16>) -> Vec<u16> {
        let mut v: Vec<u16> = self
            .edges
            .get(&pc)
            .into_iter()
            .flatten()
            .copied()
            .chain(more)
            .collect();
        v.sort_unstable();
        v.dedup();
        v
    }
    fn tg(&self, pc: u16, cell: bool, image: u16) -> Tg {
        if cell {
            Tg::Calc(self.facts(pc, [image]))
        } else {
            Tg::At(image)
        }
    }
    fn word(&self, a: u16) -> u16 {
        u16::from_le_bytes([self.img[a as usize], self.img[a.wrapping_add(1) as usize]])
    }
    fn opcodes(&self, pc: u16) -> Vec<u8> {
        let mut v = BTreeSet::from([self.img[pc as usize]]);
        if self.cells.contains(pc as usize) {
            v.extend(self.values.get(&pc).into_iter().flatten());
        }
        v.into_iter().collect()
    }
    fn flow(&self, pc: u16, op: u8) -> Flow {
        let t = Template::of(op);
        let (a1, a2) = (pc.wrapping_add(1), pc.wrapping_add(2));
        let next = pc.wrapping_add(t.len as u16);
        let opnd = t.len > 1 && self.cells.contains(a1 as usize)
            || t.len > 2 && self.cells.contains(a2 as usize);
        let w = self.word(a1);
        match t.ctrl {
            CtrlKind::Next => Flow::Next(next),
            CtrlKind::Br { .. } => Flow::Br {
                t: self.tg(
                    pc,
                    opnd,
                    next.wrapping_add(self.img[a1 as usize] as i8 as u16),
                ),
                f: next,
            },
            CtrlKind::Jmp => Flow::Jmp(self.tg(pc, opnd, w)),
            CtrlKind::JmpInd => Flow::JmpInd(self.facts(pc, [])),
            CtrlKind::Jsr => Flow::Jsr {
                t: self.tg(pc, opnd, w),
                ret: next,
            },
            CtrlKind::Rts => Flow::Rts(self.facts(pc, [])),
            CtrlKind::Rti => Flow::Rti(self.facts(pc, [])),
            CtrlKind::Brk => Flow::Brk(self.facts(pc, [self.brk])),
            CtrlKind::Jam => Flow::Jam,
        }
    }
}

/// Successor addresses of a flow (every candidate).
pub fn flow_succs(f: &Flow) -> Vec<u16> {
    match f {
        Flow::Next(n) => vec![*n],
        Flow::Br { t, f } => t.all().iter().copied().chain([*f]).collect(),
        Flow::Jmp(t) => t.all().to_vec(),
        Flow::Jsr { t, ret } => t.all().iter().copied().chain([*ret]).collect(),
        Flow::JmpInd(v) | Flow::Rts(v) | Flow::Rti(v) | Flow::Brk(v) => v.clone(),
        Flow::Jam => vec![],
    }
}

/// Operand bytes of the instruction at `pc` (`None` for cells or past its length).
pub fn operand_bytes(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    pc: u16,
    len: u8,
) -> [Option<u8>; 2] {
    std::array::from_fn(|i| {
        let a = pc.wrapping_add(i as u16 + 1);
        (len as usize > i + 1 && !cells.contains(a as usize)).then(|| img[a as usize])
    })
}

/// The configuration an entry runs in: the first its calls were seen at, else the image's.
pub fn entry_config(m: &BankMap, img: &[u8; 0x10000], facts: &Facts, pc: u16) -> u8 {
    let seen = facts.entry_port.iter().find(|e| e.0 == pc);
    let (d, v) = seen
        .and_then(|e| e.1.first().copied())
        .unwrap_or((img[m.dir as usize], img[m.data as usize]));
    m.config(d, v)
}

/// Discover the program reachable from `entries` relative to `img`; the cell fixpoint ends as
/// cells and their value sets only grow within the 64 KiB image. With a bank map each start
/// is decoded in the configuration it first ran in (else the one it is first reached in), its
/// bytes from ROM where that configuration maps ROM.
pub fn discover(
    img: &[u8; 0x10000],
    entries: &[Entry],
    facts: &Facts,
    bank: Option<&BankMap>,
    matched: bool,
) -> Graph {
    let seen: FxHashMap<u16, u8> = match bank {
        Some(_) => facts.banks.iter().map(|(p, c)| (*p, c[0])).collect(),
        None => FxHashMap::default(),
    };
    let mut edges: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
    for &(p, q) in &facts.edges {
        edges.entry(p).or_default().push(q);
    }
    for v in edges.values_mut() {
        v.sort_unstable();
        v.dedup();
    }
    let observed: FxHashMap<u16, BTreeSet<u8>> = facts
        .writes
        .iter()
        .map(|(a, v)| (*a, v.iter().copied().collect()))
        .collect();
    let mut cells = FixedBitSet::with_capacity(0x10000);
    let mut values = observed.clone();
    loop {
        let mut cimg = Box::new(*img);
        let mut rom_code = FixedBitSet::with_capacity(0x10000);
        let mut cfg: FxHashMap<u16, u8> = FxHashMap::default();
        let mut vars: FxHashMap<u16, Vec<Var>> = FxHashMap::default();
        let mut work: Vec<(u16, u8)> = entries
            .iter()
            .map(|e| (e.pc, bank.map_or(0, |m| entry_config(m, img, facts, e.pc))))
            .collect();
        while let Some((pc, c)) = work.pop() {
            if vars.contains_key(&pc) {
                continue;
            }
            let c = seen.get(&pc).copied().unwrap_or(c);
            if let Some(m) = bank.filter(|m| m.back(c, pc) == Back::Rom) {
                for i in 0..Template::of(m.rom[pc as usize]).len as u16 {
                    let a = pc.wrapping_add(i);
                    if m.back(c, a) == Back::Rom {
                        cimg[a as usize] = m.rom[a as usize];
                        rom_code.insert(a as usize);
                    }
                }
            }
            cfg.insert(pc, c);
            let vec = |a: u16| match bank {
                Some(m) if m.back(c, a) == Back::Rom => m.rom[a as usize],
                _ => img[a as usize],
            };
            let ctx = Ctx {
                img: &cimg,
                cells: &cells,
                edges: &edges,
                values: &values,
                brk: u16::from_le_bytes([vec(0xfffe), vec(0xffff)]),
            };
            let vs: Vec<Var> = ctx
                .opcodes(pc)
                .into_iter()
                .map(|op| Var {
                    op,
                    flow: ctx.flow(pc, op),
                    acc: Vec::new(),
                })
                .collect();
            work.extend(vs.iter().flat_map(|v| flow_succs(&v.flow)).map(|t| (t, c)));
            vars.insert(pc, vs);
        }
        let mut code = FixedBitSet::with_capacity(0x10000);
        for (&pc, vs) in &vars {
            for v in vs {
                for i in 0..Template::of(v.op).len as u16 {
                    code.insert(pc.wrapping_add(i) as usize);
                }
            }
        }
        let written = static_stores(&cimg, &cells, &vars);
        let from = crate::vsa::analyze(&cimg, &cells, &mut vars, entries, matched);
        let mut next = FixedBitSet::with_capacity(0x10000);
        let mut nvalues = observed.clone();
        for a in code.ones().filter(|&a| !rom_code.contains(a)) {
            let a16 = a as u16;
            let st = written.get(&a16);
            if observed.contains_key(&a16) || st.is_some() {
                next.insert(a);
                nvalues
                    .entry(a16)
                    .or_default()
                    .extend(st.into_iter().flatten());
            }
        }
        // Monotone: cells and their values only grow, so the fixpoint terminates.
        next.union_with(&cells);
        for (a, v) in &values {
            nvalues.entry(*a).or_default().extend(v);
        }
        if next == cells && nvalues == values {
            return Graph {
                vars,
                cells,
                code,
                edges,
                img: cimg,
                rom_code,
                cfg,
                prot: FixedBitSet::with_capacity(0x100),
                pre: FxHashMap::default(),
                from,
            };
        }
        cells = next;
        values = nvalues;
    }
}

/// The statically known stores `addr -> values` (empty set: unknown value) by abstract
/// interpretation along single-predecessor chains.
fn static_stores(
    img: &[u8; 0x10000],
    cells: &FixedBitSet,
    vars: &FxHashMap<u16, Vec<Var>>,
) -> FxHashMap<u16, BTreeSet<u8>> {
    let mut preds: FxHashMap<u16, u32> = FxHashMap::default();
    for vs in vars.values() {
        let succ: FxHashSet<u16> = vs.iter().flat_map(|v| flow_succs(&v.flow)).collect();
        for s in succ {
            *preds.entry(s).or_default() += 1;
        }
    }
    let chained: FxHashSet<u16> = vars
        .values()
        .filter(|vs| vs.len() == 1)
        .filter_map(|vs| single_next(&vs[0].flow))
        .filter(|n| preds.get(n) == Some(&1))
        .collect();
    let mut order: Vec<u16> = vars.keys().copied().collect();
    order.sort_unstable();
    let roots: Vec<u16> = order
        .iter()
        .filter(|p| !chained.contains(p))
        .chain(order.iter())
        .copied()
        .collect();
    let mut seen: FxHashSet<u16> = FxHashSet::default();
    let mut written: FxHashMap<u16, BTreeSet<u8>> = FxHashMap::default();
    let mut stack = Vec::new();
    let mut acc = Vec::new();
    for root in roots {
        stack.push((root, TOP_ENV));
        while let Some((pc, env)) = stack.pop() {
            if !seen.insert(pc) {
                continue;
            }
            let vs = &vars[&pc];
            let single = vs.len() == 1;
            for v in vs {
                let t = Template::of(v.op);
                let mut e = env;
                acc.clear();
                absint(
                    t.ops(),
                    operand_bytes(img, cells, pc, t.len),
                    &mut e,
                    &mut acc,
                );
                for a in &acc {
                    if let (true, Some(addr)) = (a.store, a.addr.value()) {
                        let s = written.entry(addr as u16).or_default();
                        if let Some(x) = a.val.value() {
                            s.insert(x as u8);
                        }
                    }
                }
                match single_next(&v.flow) {
                    Some(n) if single && chained.contains(&n) => stack.push((n, e)),
                    _ => {}
                }
            }
        }
    }
    written
}

/// The fall-through successor of a straight-line instruction.
fn single_next(f: &Flow) -> Option<u16> {
    match f {
        Flow::Next(n) => Some(*n),
        Flow::Jmp(Tg::At(t)) => Some(*t),
        _ => None,
    }
}

/// Operands of an instruction with constant bytes in place of cells (for slot values that
/// are fully constant).
pub fn operands(b: [Option<u8>; 2]) -> Operands {
    Operands::new(b[0].unwrap_or(0), b[1].unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The data access of opcode `op` (operand bytes `b`, `X` and `Y` as given).
    fn data_access(op: u8, b: [Option<u8>; 2], x: Iv, y: Iv) -> Access {
        let mut env = TOP_ENV;
        env[di_pcode::reg::X] = x;
        env[di_pcode::reg::Y] = y;
        let mut acc = Vec::new();
        absint(Template::of(op).ops(), b, &mut env, &mut acc);
        *acc.iter().rev().find(|a| !a.dummy).unwrap()
    }

    #[test]
    fn pointer_high_byte_is_found_through_y_and_x() {
        let b = [Some(0xfb), None];
        let a = data_access(0xb1, b, Iv::full(1), Iv::full(1));
        assert_eq!(a.hi, Some(Iv::exact(0xfc)));
        assert!(a.addr.meets(0x100, 0x200));
        let a = data_access(0xa1, b, Iv::exact(2), Iv::full(1));
        assert_eq!(a.hi, Some(Iv::exact(0xfe)));
        let a = data_access(0xa1, b, Iv { lo: 0, hi: 3 }, Iv::full(1));
        assert_eq!(a.hi, Some(Iv { lo: 0xfc, hi: 0xff }));
    }

    #[test]
    fn the_pointer_itself_and_other_modes_have_no_high_byte() {
        let b = [Some(0xfb), Some(0x20)];
        for op in [0xad, 0xbd, 0xb9, 0xa5, 0xb5] {
            assert_eq!(
                data_access(op, b, Iv::full(1), Iv::full(1)).hi,
                None,
                "{op:x}"
            );
        }
        let a = data_access(0xb1, [None, None], Iv::full(1), Iv::full(1));
        assert_eq!(a.hi, Some(Iv::full(1)));
    }

    #[test]
    fn protected_bytes_exclude_page_1() {
        let a = data_access(0xb1, [Some(0xfb), None], Iv::full(1), Iv::full(1));
        let mut prot = FixedBitSet::with_capacity(0x100);
        assert!(a.page1(&prot));
        prot.insert(0xfc);
        assert!(!a.page1(&prot));
        let wide = data_access(0xb1, [None, None], Iv::full(1), Iv::full(1));
        assert!(wide.page1(&prot));
        prot.insert_range(..);
        assert!(!wide.page1(&prot));
    }

    #[test]
    fn a_store_back_of_the_loaded_byte_is_marked() {
        let mut env = TOP_ENV;
        let mut acc = Vec::new();
        absint(
            Template::of(0xe6).ops(),
            [Some(0xfc), None],
            &mut env,
            &mut acc,
        );
        let stores: Vec<bool> = acc.iter().filter(|a| a.store).map(|a| a.back).collect();
        assert_eq!(stores, [true, false]);
    }
}
