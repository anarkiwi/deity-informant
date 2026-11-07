//! Whole-program discovery: reachable instruction variants from the entries, code cells
//! (code bytes the program writes) by a fixpoint over static store analysis and observed
//! writes, and per-access address intervals.

use crate::bank::{Back, BankMap};
use crate::ir::mask;
use di_pcode::{CtrlKind, Op, Opc, Operands, Slot, Space, Template, Vn};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{BTreeMap, BTreeSet};

/// An entry point and optional live-in register values (`lift()` register index, values),
/// guarded at entry. A folded entry ([`crate::fold`]) first makes the volatile writes `io`
/// (address, value) in order, then the stores `pre` (byte address, value), and then assigns
/// the registers `set`.
#[derive(Clone, Debug, Default)]
pub struct Entry {
    pub pc: u16,
    pub regs: Vec<(usize, Vec<u8>)>,
    pub set: Vec<(usize, u8)>,
    pub pre: Vec<(u16, Val)>,
    pub io: Vec<(u16, u8)>,
}

/// A byte an entry stores before its first instruction: a constant, or the value a register
/// holds at entry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Val {
    K(u8),
    R(usize),
}

impl Entry {
    /// The registers at its first instruction: its declared intervals, then those it sets.
    pub fn env(&self) -> Env {
        let mut env = TOP_ENV;
        for (r, vals) in &self.regs {
            if let (Some(&lo), Some(&hi), true) =
                (vals.iter().min(), vals.iter().max(), *r < env.len())
            {
                env[*r] = Iv {
                    lo: lo as u32,
                    hi: hi as u32,
                };
            }
        }
        for &(r, v) in &self.set {
            env[r] = Iv::exact(v as u32);
        }
        env
    }

    /// The bytes `pre` stores and the values each may hold.
    pub fn stores(&self) -> impl Iterator<Item = (u16, BTreeSet<u8>)> + '_ {
        self.pre.iter().map(|&(a, v)| match v {
            Val::K(x) => (a, BTreeSet::from([x])),
            Val::R(r) => (
                a,
                self.regs
                    .iter()
                    .find(|e| e.0 == r)
                    .map_or_else(|| (0..=0xff).collect(), |e| e.1.iter().copied().collect()),
            ),
        })
    }
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
/// The least all-ones value at or above `x`: an or or exclusive or of values at most `x`
/// stays below it.
fn ones(x: u32) -> u32 {
    u32::MAX.checked_shr(x.leading_zeros()).unwrap_or(0)
}

pub fn iv_op(opc: Opc, a: Iv, b: Iv, out: u32, size0: u32) -> Iv {
    let m = mask(out);
    // A copy to fewer bytes truncates.
    if opc == Opc::Copy {
        return match a.value() {
            Some(x) => Iv::exact(x & m),
            None if a.hi <= m => a,
            None => Iv::full(out),
        };
    }
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
        // A mask of low bits keeps the values it covers, or of an interval inside one block
        // of its size their low bits.
        Opc::IntAnd => match (a.value(), b.value()) {
            (_, Some(m)) if m & m.wrapping_add(1) == 0 && a.hi <= m => a,
            (Some(m), _) if m & m.wrapping_add(1) == 0 && b.hi <= m => b,
            (_, Some(m)) | (Some(m), _)
                if m & m.wrapping_add(1) == 0 && {
                    let x = if a.value() == Some(m) { b } else { a };
                    x.lo & !m == x.hi & !m
                } =>
            {
                let x = if a.value() == Some(m) { b } else { a };
                Iv {
                    lo: x.lo & m,
                    hi: x.hi & m,
                }
            }
            _ => Iv {
                lo: 0,
                hi: a.hi.min(b.hi),
            },
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
                hi: m.min(ones(a.hi | b.hi)),
            },
        },
        Opc::IntXor => match (a.value(), b.value()) {
            (Some(0), _) => b,
            (_, Some(0)) => a,
            _ => Iv {
                lo: 0,
                hi: m.min(ones(a.hi | b.hi)),
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

/// Interval of operand slot `slot` whose operand bytes lie in `bi` (a cell's values).
pub fn slot_range(slot: Slot, bi: [Iv; 2], size: u32) -> Iv {
    let inc = |x: Iv| match (x.hi < 0xff, x.value()) {
        (true, _) => Iv {
            lo: x.lo + 1,
            hi: x.hi + 1,
        },
        (false, Some(_)) => Iv::exact(0),
        (false, None) => Iv::full(1),
    };
    let iv = match slot {
        Slot::Fixed => unreachable!(),
        Slot::B1 => bi[0],
        Slot::Word => Iv {
            lo: bi[1].lo << 8 | bi[0].lo,
            hi: bi[1].hi << 8 | bi[0].hi,
        },
        Slot::B1Inc => inc(bi[0]),
        Slot::B2Inc => inc(bi[1]),
    };
    if iv.hi > mask(size) {
        Iv::full(size)
    } else {
        iv
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

/// Per zero-page pointer pair (by the address of its low byte), the largest index `m` its
/// pointer is kept clear of page 1 for (`None`: unprotected); see [`crate::ptr`].
pub type Win = [Option<u16>; 0x100];

/// A zero-page pointer an address is built from: the address of its low byte (when the
/// address uses it), of its high byte, and the largest byte added to it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Ptr {
    pub lo: Option<Iv>,
    pub hi: Iv,
    pub idx: u32,
}

impl Ptr {
    /// The pairs whose windows keep the address out of page 1 and the window `m` each needs:
    /// the address is `p + i` (`i <= m`) for a pointer `p` outside `[$100 - m, $1FF]`.
    pub fn need(&self) -> (impl Iterator<Item = u8>, u32) {
        let pair = matches!(
            (self.lo.and_then(|l| l.value()), self.hi.value()),
            (Some(l), Some(h)) if (l + 1) & 0xff == h
        );
        let m = match self.idx {
            0 => 0,
            i if pair => i,
            _ => 0x100,
        };
        (
            (self.hi.lo..=self.hi.hi).map(|h| (h as u8).wrapping_sub(1)),
            m,
        )
    }
}

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
    /// Every high byte of `ptr` was stored on every path to the access.
    pub defd: bool,
    /// A store to a zero-page address whose next byte was stored on every path to it.
    pub above: bool,
    /// The zero-page pointer the address is, or is plus a byte.
    pub ptr: Option<Ptr>,
    /// A check before the instruction keeps the address out of every zone (page 1 and the
    /// volatile pages): a static lift's pointer access whose pair no window keeps.
    pub zone: bool,
    /// The zone check is proved: every page the address may name is outside the zones.
    pub zfree: bool,
}

impl Access {
    /// Whether the access may reach page 1 given the pointer windows `win`.
    pub fn page1(&self, win: &Win) -> bool {
        self.addr.meets(0x100, 0x200) && !self.windowed(win)
    }
    /// Whether the access is kept out of every zone: by its own check, or through pairs the
    /// windows `win` keep.
    pub fn windowed(&self, win: &Win) -> bool {
        self.zone
            || self.ptr.is_some_and(|p| {
                let (mut qs, m) = p.need();
                qs.all(|q| win[q as usize].is_some_and(|t| t as u32 >= m))
            })
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
    /// The high half with a byte below it (loaded from the given addresses, when known):
    /// the page is the high byte.
    Page(Ptr),
    /// A `Page` plus a byte: the page is the byte or the next.
    Near(Ptr),
}

/// Abstractly execute `ops` from `env` with operand bytes `b` (`None` for cells); the
/// memory accesses in op order.
pub fn absint(ops: &[Op], b: [Option<u8>; 2], env: &mut Env, acc: &mut Vec<Access>) {
    absint_known(ops, b, env, acc, &|_| None)
}

/// [`absint`] with the bytes `known` gives (by address) known.
pub fn absint_known(
    ops: &[Op],
    b: [Option<u8>; 2],
    env: &mut Env,
    acc: &mut Vec<Access>,
    known: &dyn Fn(u32) -> Option<u8>,
) {
    let bi = b.map(|x| x.map_or(Iv::full(1), |v| Iv::exact(v as u32)));
    let load = |a: Iv| a.value().and_then(known).map(|x| Iv::exact(x as u32));
    absint_ranges(ops, b, bi, env, acc, &load)
}

/// [`absint`] with cell operand bytes in the intervals `bi` and byte loads from an address
/// interval in the values `load` gives.
pub fn absint_ranges(
    ops: &[Op],
    b: [Option<u8>; 2],
    bi: [Iv; 2],
    env: &mut Env,
    acc: &mut Vec<Access>,
    load: &dyn Fn(Iv) -> Option<Iv>,
) {
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
                Space::Const if (0..2).all(|i| b[i].is_some() || bi[i] == Iv::full(1)) => {
                    slot_iv(v.slot(), b, v.size())
                }
                Space::Const => slot_range(v.slot(), bi, v.size()),
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
        let ptr = match tg(op.ins[0]) {
            Tag::Page(p) | Tag::Near(p) => Some(p),
            _ => None,
        };
        let word = op.out.size() == 2;
        let low = |t: Tag| match t {
            Tag::Word(r) => Some(r),
            _ => None,
        };
        let page = |hi, t| Ptr {
            lo: low(t),
            hi,
            idx: 0,
        };
        let t = match (op.opc, tg(op.ins[0]), tg(op.ins[1])) {
            (Opc::Load, _, _) if a.hi < 0x100 && op.out.size() == 1 => Tag::Byte(a),
            (Opc::IntZext, Tag::Byte(r), _) if word => Tag::Word(r),
            (Opc::IntLeft, Tag::Word(r), _) if word && y.value() == Some(8) => Tag::High(r),
            (Opc::IntOr, Tag::High(r), t) if word && y.hi < 0x100 => Tag::Page(page(r, t)),
            (Opc::IntOr, t, Tag::High(r)) if word && a.hi < 0x100 => Tag::Page(page(r, t)),
            (Opc::IntAdd, Tag::Page(p), _) if word && y.hi < 0x100 => {
                Tag::Near(Ptr { idx: y.hi, ..p })
            }
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
                    above: false,
                    ptr,
                    zone: false,
                    zfree: false,
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
                    above: false,
                    ptr,
                    zone: false,
                    zfree: false,
                });
                match (op.out.size() == 1).then(|| load(a)).flatten() {
                    Some(x) => x,
                    None => Iv::full(op.out.size()),
                }
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
    /// Zero-page pointer pairs kept clear of page 1 and their checks (see [`crate::ptr`]).
    pub prot: crate::ptr::Prot,
    /// The zones the pointer windows keep pointers out of (page 1 first).
    pub zones: Vec<(u32, u32)>,
    /// Address hulls of the loads through pointers and cell operands while table loads stay
    /// inside their extents ([`crate::vsa`]'s `reach`) and of their bases, by `(pc, opcode,
    /// access index)`.
    pub reach: crate::vsa::Hulls,
    /// The hulls of `reach` that hold on every run (no table load taken to stay inside its
    /// extent).
    pub proven: crate::vsa::Hulls,
    /// Per instruction start the values `A`, `X` and `Y` hold on every run.
    pub regs: crate::vsa::Regs,
    /// Stores whose index the code does not bound and the run `[lo, hi)` of data bytes each
    /// writes, by `(pc, opcode, access index)`: past it a write is out of bounds.
    pub bounded: FxHashMap<(u16, u8, usize), (u32, u32)>,
    /// The `RTS`/`RTI`s that jump through the address their code pushed.
    pub disp: FxHashSet<u16>,
    /// The bytes every run of the init entry stores before it reads them, when its runs are
    /// decided ([`crate::vsa`]'s `init_left`).
    pub fresh: Option<FixedBitSet>,
    /// Per instruction start the entries (bit `i % 64` for entry `i`) it is reached from.
    pub from: FxHashMap<u16, u64>,
    /// Instruction starts at which the decimal flag may be set.
    pub dec: FixedBitSet,
    /// `PLP` and `RTI` are guarded to pull the decimal flag clear ([`crate::vsa::pull_clear`]).
    pub pull_clear: bool,
    /// Induction variables of the loops.
    pub ind: Vec<crate::scev::Induction>,
    /// Computed transfers whose targets come from the code ([`crate::dispatch`]).
    pub stat: FxHashMap<u16, Vec<u16>>,
    /// Per computed transfer of `stat`, `(low byte address, target)` of each target read from
    /// a table, ascending.
    pub origins: FxHashMap<u16, Vec<(u16, u16)>>,
    /// Computed jumps selecting their target by an index register ([`crate::dispatch::Selector`]).
    pub sels: FxHashMap<u16, crate::dispatch::Selector>,
    /// Stores `(pc, opcode, access index)` none of whose addresses is constant code.
    pub nocode: FxHashSet<(u16, u8, usize)>,
    /// The bytes the targets in `stat` and the values of stores into cells were read from:
    /// constant like code.
    pub tables: BTreeSet<u16>,
    /// Opcode cells no store writes a value outside their variants: every store that may
    /// write one has known addresses and values.
    pub closed: FixedBitSet,
    /// The values of the closed cells ([`crate::dispatch::cell_values`]).
    pub vals: FxHashMap<u16, BTreeSet<u8>>,
    /// Index guards by instruction ([`crate::dispatch::Guard`]).
    pub guards: FxHashMap<u16, (u8, Iv)>,
    /// The bytes stores with address sets may write.
    pub written: FixedBitSet,
    /// Per return site, the addresses past it a call returns to (inline arguments).
    pub past: FxHashMap<u16, Vec<u16>>,
    /// Per `JSR` through cell operands with targets: its dispatch function (keyed by the least
    /// such site with the same targets) and the targets.
    pub dispatch: FxHashMap<u16, (u16, Vec<u16>)>,
    /// Per instruction start and kind (store or load), the address set of its data accesses
    /// through a pointer or with several addresses, where one is known.
    pub addr_sets: FxHashMap<(u16, bool), BTreeSet<u16>>,
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
    /// Targets from the code, which replace the facts'.
    stat: &'a FxHashMap<u16, BTreeSet<u16>>,
    values: &'a FxHashMap<u16, BTreeSet<u8>>,
    /// The `BRK` vector as the instruction's configuration maps it.
    brk: u16,
}

impl Ctx<'_> {
    fn computed(&self, pc: u16) -> Vec<u16> {
        match self.stat.get(&pc) {
            Some(t) => t.iter().copied().collect(),
            None => self.facts(pc, []),
        }
    }
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
            Tg::Calc(match self.stat.get(&pc) {
                Some(t) => t.iter().copied().collect(),
                None => self.facts(pc, [image]),
            })
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
            CtrlKind::JmpInd => Flow::JmpInd(self.computed(pc)),
            CtrlKind::Jsr => Flow::Jsr {
                t: self.tg(pc, opnd, w),
                ret: next,
            },
            CtrlKind::Rts => Flow::Rts(self.computed(pc)),
            CtrlKind::Rti => Flow::Rti(self.computed(pc)),
            CtrlKind::Brk => Flow::Brk(match self.stat.get(&pc) {
                Some(t) => t.iter().copied().collect(),
                None => self.facts(pc, [self.brk]),
            }),
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

/// The bytes entries store before their first instruction.
pub fn pre_stored(entries: &[Entry]) -> impl Iterator<Item = u16> + '_ {
    entries.iter().flat_map(|e| e.pre.iter().map(|p| p.0))
}

/// Discover the program reachable from `entries` relative to `img`. With a bank map each start
/// is decoded in the configuration it first ran in (else the one it is first reached in), its
/// bytes from ROM where that configuration maps ROM.
///
/// The code cells, their value sets and the targets of computed transfers found from the code
/// ([`crate::dispatch`]) are a joint least fixpoint from none: each round decodes the program
/// they give and analyses it, and they only grow. `loaded` is the `[lo, hi)` the program was
/// loaded to, where tables end at the latest. An `RTS`/`RTI` whose pulled address the code
/// gives targets for jumps through it within the same fixpoint; which computed transfers stay
/// unresolved (to their facts' targets) is decided on a converged program, and the fixpoint is
/// recomputed until that does not grow. With `reach` (the volatile ranges and the init
/// entry's index) the graph holds the address hulls of pointer loads.
#[allow(clippy::too_many_arguments)]
pub fn discover(
    img: &[u8; 0x10000],
    entries: &[Entry],
    facts: &Facts,
    bank: Option<&BankMap>,
    matched: bool,
    loaded: Option<(u32, u32)>,
    entry_sp: Option<u8>,
    reach: Option<crate::vsa::Wanted>,
) -> Graph {
    let mut edges: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
    for &(p, q) in &facts.edges {
        edges.entry(p).or_default().push(q);
    }
    for v in edges.values_mut() {
        v.sort_unstable();
        v.dedup();
    }
    let mut fix = Fixpoint {
        img,
        entries,
        facts,
        bank,
        matched,
        edges,
        failed: FxHashSet::default(),
        loaded,
        entry_sp,
        reach,
        constant: {
            let mut c = FixedBitSet::with_capacity(0x10000);
            if let Some((lo, hi)) = loaded {
                c.insert_range(lo as usize..(hi as usize).min(0x10000));
            }
            for a in facts.writes.iter().map(|w| w.0).chain(pre_stored(entries)) {
                c.set(a as usize, false);
            }
            c
        },
    };
    loop {
        let (mut g, jumps) = fix.run();
        // Bytes read as constant that a store with an address set may write are not.
        let mut w = g.written.clone();
        let bad: Vec<usize> = g
            .tables
            .iter()
            .map(|&a| a as usize)
            .filter(|&a| w.contains(a) && fix.constant.contains(a))
            .collect();
        if !bad.is_empty() {
            w.intersect_with(&fix.constant);
            fix.constant.difference_with(&w);
            continue;
        }
        let failed: Vec<u16> = jumps
            .iter()
            .filter(|(pc, j)| **j == crate::dispatch::Jump::Fail && !fix.failed.contains(pc))
            .map(|(&pc, _)| pc)
            .collect();
        if failed.is_empty() {
            fix.reach(&mut g);
            return g;
        }
        fix.failed.extend(failed);
    }
}

/// The inner fixpoint of [`discover`] for given unresolved transfers.
struct Fixpoint<'a> {
    img: &'a [u8; 0x10000],
    entries: &'a [Entry],
    facts: &'a Facts,
    bank: Option<&'a BankMap>,
    matched: bool,
    edges: FxHashMap<u16, Vec<u16>>,
    failed: FxHashSet<u16>,
    loaded: Option<(u32, u32)>,
    /// The SP every entry is called at, when declared.
    entry_sp: Option<u8>,
    /// Loaded bytes taken as constant: those no store with an address set wrote in an
    /// earlier round, nor the facts.
    constant: FixedBitSet,
    /// The volatile ranges and the init entry's index, when the address hulls of pointer
    /// loads are wanted.
    reach: Option<crate::vsa::Wanted<'a>>,
}

impl Fixpoint<'_> {
    /// The bytes the facts saw written and those the entries store.
    fn observed(&self) -> FxHashMap<u16, BTreeSet<u8>> {
        let mut observed: FxHashMap<u16, BTreeSet<u8>> = (self.facts.writes.iter())
            .map(|(a, v)| (*a, v.iter().copied().collect()))
            .collect();
        for (a, v) in self.entries.iter().flat_map(Entry::stores) {
            observed.entry(a).or_default().extend(v);
        }
        observed
    }

    /// The pointer reach of the program `g` the rounds settled on, when wanted.
    fn reach(&self, g: &mut Graph) {
        let Some(r) = self.reach else {
            return;
        };
        let mut seen_writes = FixedBitSet::with_capacity(0x10000);
        seen_writes.extend(self.observed().keys().map(|&a| a as usize));
        let written = g.written.clone();
        let given = crate::vsa::Given {
            loaded: self.loaded,
            entry_sp: self.entry_sp,
            observed: &seen_writes,
            constant: &self.constant,
            volatile: r.volatile,
            reach: true,
            init: r.init,
            written: &written,
        };
        let an = crate::vsa::analyze(
            &g.img,
            &g.cells,
            &mut g.vars,
            self.entries,
            self.matched,
            &g.disp,
            &given,
        );
        (g.reach, g.proven, g.regs, g.fresh) = (an.reach, an.proven, an.regs, an.fresh);
        g.nocode.extend(an.bounded.keys().copied());
        g.bounded = an.bounded;
    }

    fn run(&self) -> (Graph, FxHashMap<u16, crate::dispatch::Jump>) {
        let (img, entries, facts, bank) = (self.img, self.entries, self.facts, self.bank);
        let seen: FxHashMap<u16, u8> = match bank {
            Some(_) => facts.banks.iter().map(|(p, c)| (*p, c[0])).collect(),
            None => FxHashMap::default(),
        };
        let observed = self.observed();
        let mut seen_writes = FixedBitSet::with_capacity(0x10000);
        seen_writes.extend(observed.keys().map(|&a| a as usize));
        let mut cells = FixedBitSet::with_capacity(0x10000);
        let mut values = observed.clone();
        let mut stat: FxHashMap<u16, BTreeSet<u16>> = FxHashMap::default();
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
                    edges: &self.edges,
                    stat: &stat,
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
            // An `RTS`/`RTI` jumps through the address its code pushed where the code gives its
            // targets: the return sites ∪ those targets grow with the fixpoint.
            let disp: FxHashSet<u16> = stat
                .keys()
                .copied()
                .filter(|pc| {
                    vars.get(pc).is_some_and(|vs| {
                        vs.iter()
                            .any(|v| matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)))
                    })
                })
                .collect();
            let none = FixedBitSet::new();
            let given = crate::vsa::Given {
                loaded: self.loaded,
                entry_sp: self.entry_sp,
                observed: &seen_writes,
                constant: &self.constant,
                volatile: self.reach.map_or(&[], |r| r.volatile),
                reach: false,
                init: self.reach.and_then(|r| r.init),
                written: &none,
            };
            let an = crate::vsa::analyze(
                &cimg,
                &cells,
                &mut vars,
                entries,
                self.matched,
                &disp,
                &given,
            );
            let mut written: FxHashMap<u16, BTreeSet<u8>> = FxHashMap::default();
            for st in &an.stores {
                for &a in st.addrs.iter().flatten() {
                    let s = written.entry(a).or_default();
                    if let Some(Some(v)) = an.cellvals.get(&a) {
                        s.extend(v);
                    }
                }
            }
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
            // Monotone: cells, their values and targets only grow, so the fixpoint terminates.
            next.union_with(&cells);
            for (a, v) in &values {
                nvalues.entry(*a).or_default().extend(v);
            }
            let mut nstat = stat.clone();
            for (&pc, j) in &an.jumps {
                if let (crate::dispatch::Jump::Targets(t, _, _, _), false) =
                    (j, self.failed.contains(&pc))
                {
                    nstat.entry(pc).or_default().extend(t);
                }
            }
            if next == cells && nvalues == values && nstat == stat {
                let pull_clear = crate::vsa::pull_clear(&vars, entries);
                let tables: BTreeSet<u16> = an
                    .jumps
                    .iter()
                    .filter(|(pc, _)| !self.failed.contains(pc))
                    .flat_map(|(_, j)| match j {
                        crate::dispatch::Jump::Targets(_, b, _, _) => b.clone(),
                        crate::dispatch::Jump::Fail => Vec::new(),
                    })
                    .chain(an.reads.iter().copied())
                    .collect();
                let mut constcode = code.clone();
                constcode.difference_with(&cells);
                constcode.difference_with(&rom_code);
                constcode.extend(tables.iter().map(|&a| a as usize));
                let mut closed = FixedBitSet::with_capacity(0x10000);
                let mut vals = FxHashMap::default();
                for a in cells.ones() {
                    if let Some(Some(v)) = an.cellvals.get(&(a as u16)) {
                        closed.insert(a);
                        vals.insert(a as u16, v.clone());
                    }
                }
                let nocode = an
                    .stores
                    .iter()
                    .filter(|s| {
                        s.addrs
                            .as_ref()
                            .is_some_and(|a| a.iter().all(|&x| !constcode.contains(x as usize)))
                    })
                    .map(|s| (s.pc, s.op, s.ai))
                    .collect();
                let dispatch: FxHashMap<u16, (u16, Vec<u16>)> = {
                    let mut by: BTreeMap<Vec<u16>, Vec<u16>> = BTreeMap::new();
                    for (&pc, vs) in &vars {
                        if let [Var {
                            flow:
                                Flow::Jsr {
                                    t: Tg::Calc(fs), ..
                                },
                            ..
                        }] = &vs[..]
                        {
                            if !fs.is_empty() && fs.iter().all(|f| vars.contains_key(f)) {
                                by.entry(fs.clone()).or_default().push(pc);
                            }
                        }
                    }
                    // Keyed by a site no call or entry enters (its key names a function).
                    let entered: FxHashSet<u16> = vars
                        .values()
                        .flatten()
                        .flat_map(|v| match &v.flow {
                            Flow::Jsr { t, .. } => t.all().to_vec(),
                            _ => Vec::new(),
                        })
                        .chain(entries.iter().map(|e| e.pc))
                        .collect();
                    by.into_iter()
                        .filter_map(|(fs, sites)| {
                            let key = *sites.iter().filter(|s| !entered.contains(s)).min()?;
                            Some(sites.into_iter().map(move |s| (s, (key, fs.clone()))))
                        })
                        .flatten()
                        .collect()
                };
                let g = Graph {
                    reach: FxHashMap::default(),
                    proven: FxHashMap::default(),
                    regs: FxHashMap::default(),
                    bounded: FxHashMap::default(),
                    fresh: None,
                    disp,
                    vars,
                    cells,
                    code,
                    edges: self.edges.clone(),
                    img: cimg,
                    rom_code,
                    cfg,
                    prot: crate::ptr::Prot::default(),
                    zones: vec![crate::ptr::PAGE1],
                    from: an.from,
                    dec: an.dec,
                    pull_clear,
                    ind: an.ind,
                    stat: stat
                        .into_iter()
                        .map(|(pc, t)| (pc, t.into_iter().collect()))
                        .collect(),
                    origins: an
                        .jumps
                        .iter()
                        .filter(|(pc, _)| !self.failed.contains(pc))
                        .filter_map(|(&pc, j)| match j {
                            crate::dispatch::Jump::Targets(_, _, _, o) => Some((pc, o.clone())),
                            crate::dispatch::Jump::Fail => None,
                        })
                        .collect(),
                    sels: an
                        .sels
                        .iter()
                        .filter(|(pc, _)| !self.failed.contains(pc))
                        .map(|(&pc, x)| (pc, x.clone()))
                        .collect(),
                    nocode,
                    closed,
                    vals,
                    guards: {
                        // Index guards: what the program reads past its tables' extents.
                        let mut gs: FxHashMap<u16, (u8, Iv)> = FxHashMap::default();
                        let jumped = an.jumps.iter().filter(|(pc, _)| !self.failed.contains(pc));
                        let from_jumps = jumped.flat_map(|(_, j)| match j {
                            crate::dispatch::Jump::Targets(_, _, g, _) => g.clone(),
                            crate::dispatch::Jump::Fail => Vec::new(),
                        });
                        for (pc, r, ok) in an.guards.iter().copied().chain(from_jumps) {
                            let e = gs.entry(pc).or_insert((r, ok));
                            if let (true, Some(m)) = (e.0 == r, crate::vsa::meet(e.1, ok)) {
                                e.1 = m;
                            }
                        }
                        gs
                    },
                    past: an.past.clone(),
                    dispatch,
                    addr_sets: {
                        let mut m: FxHashMap<(u16, bool), BTreeSet<u16>> = FxHashMap::default();
                        for ((pc, _, _), a) in &an.loads {
                            m.entry((*pc, false)).or_default().extend(a);
                        }
                        for s in &an.stores {
                            if let Some(a) = s.addrs.as_ref().filter(|a| a.len() > 1) {
                                m.entry((s.pc, true)).or_default().extend(a);
                            }
                        }
                        m
                    },
                    written: {
                        let mut w = FixedBitSet::with_capacity(0x10000);
                        for s in &an.stores {
                            w.extend(s.addrs.iter().flatten().map(|&a| a as usize));
                        }
                        w
                    },
                    tables,
                };
                return (g, an.jumps);
            }
            cells = next;
            values = nvalues;
            stat = nstat;
        }
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
    fn pointer_pair_and_index_are_found_through_y_and_x() {
        let b = [Some(0xfb), None];
        let y = Iv { lo: 0, hi: 7 };
        let a = data_access(0xb1, b, Iv::full(1), y);
        let p = a.ptr.unwrap();
        assert_eq!(
            (p.lo, p.hi, p.idx),
            (Some(Iv::exact(0xfb)), Iv::exact(0xfc), 7)
        );
        assert_eq!(p.need().1, 7);
        assert!(a.addr.meets(0x100, 0x200));
        let a = data_access(0xb1, [Some(0xff), None], Iv::full(1), Iv::full(1));
        let p = a.ptr.unwrap();
        assert_eq!(
            (p.hi, p.need().0.collect::<Vec<_>>()),
            (Iv::exact(0), vec![0xff])
        );
        let a = data_access(0xa1, b, Iv::exact(2), Iv::full(1));
        assert_eq!(a.ptr.unwrap().hi, Iv::exact(0xfe));
        assert_eq!(a.ptr.unwrap().need().1, 0);
        let a = data_access(0xa1, b, Iv { lo: 0, hi: 3 }, Iv::full(1));
        let (qs, m) = a.ptr.unwrap().need();
        assert_eq!(
            (qs.collect::<Vec<_>>(), m),
            (vec![0xfb, 0xfc, 0xfd, 0xfe], 0)
        );
    }

    #[test]
    fn the_pointer_itself_and_other_modes_have_no_pointer() {
        let b = [Some(0xfb), Some(0x20)];
        for op in [0xad, 0xbd, 0xb9, 0xa5, 0xb5] {
            assert_eq!(
                data_access(op, b, Iv::full(1), Iv::full(1)).ptr,
                None,
                "{op:x}"
            );
        }
        let a = data_access(0xb1, [None, None], Iv::full(1), Iv::exact(3));
        assert_eq!(a.ptr.unwrap().hi, Iv::full(1));
        assert_eq!(a.ptr.unwrap().need().1, crate::ptr::FULL);
    }

    #[test]
    fn windows_exclude_page_1() {
        let a = data_access(
            0xb1,
            [Some(0xfb), None],
            Iv::full(1),
            Iv { lo: 0, hi: 0x10 },
        );
        let mut win: Win = [None; 0x100];
        assert!(a.page1(&win));
        win[0xfb] = Some(0x0f);
        assert!(a.page1(&win));
        win[0xfb] = Some(0x10);
        assert!(!a.page1(&win));
        let wide = data_access(0xb1, [None, None], Iv::full(1), Iv::full(1));
        win = [Some(0xff); 0x100];
        assert!(wide.page1(&win));
        win = [Some(crate::ptr::FULL as u16); 0x100];
        assert!(!wide.page1(&win));
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
