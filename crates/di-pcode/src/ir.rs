//! The P-Code IR: packed varnodes, ops, per-opcode templates and instantiated instructions.

use crate::templates_gen::{BINDS, OPS, TEMPLATES};

/// Address space of a varnode.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(u8)]
pub enum Space {
    Const = 0,
    Reg = 1,
    Unique = 2,
    Ram = 3,
}

/// Instruction-operand expression a constant is bound to (`lift()`'s `prov`).
#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
#[repr(u8)]
pub enum Slot {
    /// Not operand-derived.
    Fixed = 0,
    /// Byte 1: `([1], "id")`.
    B1 = 1,
    /// Bytes 1-2 as a little-endian word: `([1, 2], "word")`.
    Word = 2,
    /// `(b1 + 1) & $FF`: `([1], "hi1")`.
    B1Inc = 3,
    /// `(b2 + 1) & $FF`: `([2], "hi1")`.
    B2Inc = 4,
}

const SLOTS: [Slot; 5] = [Slot::Fixed, Slot::B1, Slot::Word, Slot::B1Inc, Slot::B2Inc];
const SPACES: [Space; 4] = [Space::Const, Space::Reg, Space::Unique, Space::Ram];

/// Operand-slot values of one instruction, indexed by [`Slot`].
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Operands(pub [u32; 5]);

impl Operands {
    #[inline(always)]
    pub fn new(b1: u8, b2: u8) -> Self {
        let (b1, b2) = (b1 as u32, b2 as u32);
        Self([0, b1, b1 | (b2 << 8), (b1 + 1) & 0xff, (b2 + 1) & 0xff])
    }
}

/// Value-file layout the interpreter reads varnodes from: registers, operand slots, uniques.
pub const SLOT_BASE: usize = 16;
pub const UNIQ_BASE: usize = SLOT_BASE + 5;
/// Value-file words.
pub const FILE: usize = UNIQ_BASE + crate::templates_gen::UNIQUE_SIZE;
/// File index of a fixed constant (the value is the offset).
pub const IMM: usize = 0xff;
/// File index of a RAM varnode (read and written through the bus).
pub const RAM: usize = 0xfe;

/// A varnode packed into 32 bits: offset (or constant value) in bits 0-15, [`Space`] in
/// 16-17, size in bytes in 18-20 (0 for "no varnode"), [`Slot`] in 21-23, and in 24-31 its
/// value-file index ([`IMM`] or [`RAM`] for varnodes not in the file).
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default, Hash)]
#[repr(transparent)]
pub struct Vn(pub u32);

impl Vn {
    pub const NONE: Vn = Vn((IMM as u32) << 24);

    #[inline(always)]
    pub const fn new(space: Space, off: u16, size: u8, slot: Slot) -> Self {
        let idx = match (space, slot) {
            (Space::Const, Slot::Fixed) => IMM,
            (Space::Const, _) => SLOT_BASE + slot as usize,
            (Space::Reg, _) => {
                assert!(off < SLOT_BASE as u16, "register index out of range");
                off as usize
            }
            (Space::Unique, _) => {
                assert!(
                    UNIQ_BASE + (off as usize) < RAM,
                    "unique offset out of range"
                );
                UNIQ_BASE + off as usize
            }
            (Space::Ram, _) => RAM,
        };
        Vn(off as u32
            | (space as u32) << 16
            | (size as u32) << 18
            | (slot as u32) << 21
            | (idx as u32) << 24)
    }
    /// Value-file index.
    #[inline(always)]
    pub const fn index(self) -> usize {
        (self.0 >> 24) as usize
    }
    #[inline(always)]
    pub const fn off(self) -> u32 {
        self.0 & 0xffff
    }
    #[inline(always)]
    pub fn space(self) -> Space {
        SPACES[(self.0 >> 16 & 3) as usize]
    }
    #[inline(always)]
    pub const fn size(self) -> u32 {
        self.0 >> 18 & 7
    }
    #[inline(always)]
    pub const fn slot_index(self) -> usize {
        (self.0 >> 21 & 7) as usize
    }
    #[inline(always)]
    pub fn slot(self) -> Slot {
        SLOTS[self.slot_index()]
    }
    #[inline(always)]
    pub const fn is_none(self) -> bool {
        self.size() == 0
    }
    /// A varnode outside the interpreter's value file: lifted-program registers beyond the
    /// `lift()` file, temporaries of any offset and constants.
    #[inline(always)]
    pub const fn raw(space: Space, off: u16, size: u8) -> Self {
        Vn(off as u32 | (space as u32) << 16 | (size as u32) << 18 | (IMM as u32) << 24)
    }
    /// This varnode without its slot and value-file index.
    #[inline(always)]
    pub fn canon(self) -> Self {
        Vn::raw(self.space(), self.off() as u16, self.size() as u8)
    }
    /// All-ones mask of the varnode's size.
    #[inline(always)]
    pub const fn mask(self) -> u32 {
        const MASKS: [u32; 8] = [0, 0xff, 0xffff, 0xff_ffff, !0, !0, !0, !0];
        MASKS[self.size() as usize]
    }
    /// This constant with its offset replaced by its slot's value in `o`.
    #[inline(always)]
    pub const fn bound(self, o: &Operands) -> Vn {
        if self.slot_index() == 0 {
            self
        } else {
            Vn(self.0 & !0xffff | (o.0[self.slot_index()] & self.mask()))
        }
    }
}

/// A fixed constant.
pub const fn c(v: u16, size: u8) -> Vn {
    Vn::new(Space::Const, v, size, Slot::Fixed)
}
/// A constant bound to an operand slot.
pub const fn cs(slot: Slot, size: u8) -> Vn {
    Vn::new(Space::Const, 0, size, slot)
}
/// A one-byte register (`lift()` register-file index).
pub const fn r(i: u16) -> Vn {
    Vn::new(Space::Reg, i, 1, Slot::Fixed)
}
/// A unique (temporary).
pub const fn u(off: u16, size: u8) -> Vn {
    Vn::new(Space::Unique, off, size, Slot::Fixed)
}
/// A memory cell read or written directly.
pub const fn m(addr: u16, size: u8) -> Vn {
    Vn::new(Space::Ram, addr, size, Slot::Fixed)
}

/// P-Code opcodes the lifter emits (and `PcodeVM` interprets).
#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
#[repr(u8)]
pub enum Opc {
    Copy,
    IntZext,
    IntAdd,
    IntSub,
    IntAnd,
    IntOr,
    IntXor,
    IntLeft,
    IntRight,
    IntEqual,
    IntNotEqual,
    IntLess,
    IntLessEqual,
    IntCarry,
    Load,
    Store,
    /// Lifted-program control: jump to the block in `aux`.
    Branch,
    /// Jump to the block in `aux` when input 0 is non-zero.
    CBranch,
    /// Jump to the address in input 0 through the block's target table.
    BranchInd,
    /// Call the function in `aux`; input 0 is the return address the call stands for.
    Call,
    /// Return from the function; at the outermost frame input 0 is the exit PC.
    Return,
    /// A host-defined operation named by `aux` (`deopt`, `codechk`).
    CallOther,
}

impl Opc {
    /// The SLEIGH mnemonic.
    pub const fn name(self) -> &'static str {
        match self {
            Opc::Copy => "COPY",
            Opc::IntZext => "INT_ZEXT",
            Opc::IntAdd => "INT_ADD",
            Opc::IntSub => "INT_SUB",
            Opc::IntAnd => "INT_AND",
            Opc::IntOr => "INT_OR",
            Opc::IntXor => "INT_XOR",
            Opc::IntLeft => "INT_LEFT",
            Opc::IntRight => "INT_RIGHT",
            Opc::IntEqual => "INT_EQUAL",
            Opc::IntNotEqual => "INT_NOTEQUAL",
            Opc::IntLess => "INT_LESS",
            Opc::IntLessEqual => "INT_LESSEQUAL",
            Opc::IntCarry => "INT_CARRY",
            Opc::Load => "LOAD",
            Opc::Store => "STORE",
            Opc::Branch => "BRANCH",
            Opc::CBranch => "CBRANCH",
            Opc::BranchInd => "BRANCHIND",
            Opc::Call => "CALL",
            Opc::Return => "RETURN",
            Opc::CallOther => "CALLOTHER",
        }
    }
    /// Number of inputs (`b` is [`Vn::NONE`] for unary ops).
    pub const fn arity(self) -> usize {
        match self {
            Opc::Branch => 0,
            Opc::Copy
            | Opc::IntZext
            | Opc::Load
            | Opc::CBranch
            | Opc::BranchInd
            | Opc::Call
            | Opc::Return => 1,
            _ => 2,
        }
    }
}

/// A binary arithmetic or comparison op on values already masked to their sizes; `mask0`
/// is input 0's mask (for `INT_CARRY`). The result is not masked to the output size.
#[inline(always)]
pub fn binop(opc: Opc, a: u32, b: u32, mask0: u32) -> u32 {
    match opc {
        Opc::IntAdd => a.wrapping_add(b),
        Opc::IntSub => a.wrapping_sub(b),
        Opc::IntAnd => a & b,
        Opc::IntOr => a | b,
        Opc::IntXor => a ^ b,
        Opc::IntLeft => a.checked_shl(b).unwrap_or(0),
        Opc::IntRight => a.checked_shr(b).unwrap_or(0),
        Opc::IntEqual => (a == b) as u32,
        Opc::IntNotEqual => (a != b) as u32,
        Opc::IntLess => (a < b) as u32,
        Opc::IntLessEqual => (a <= b) as u32,
        Opc::IntCarry => (a as u64 + b as u64 > mask0 as u64) as u32,
        _ => unreachable!("not a binary op"),
    }
}

/// One P-Code op: `out = opc(ins[0], ins[1])`. `STORE` has no output and stores `ins[1]`
/// at address `ins[0]`; unary ops leave `ins[1]` as [`Vn::NONE`].
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Op {
    pub opc: Opc,
    pub out: Vn,
    pub ins: [Vn; 2],
}

impl Op {
    pub const fn new(opc: Opc, out: Vn, a: Vn, b: Vn) -> Self {
        Op {
            opc,
            out,
            ins: [a, b],
        }
    }
    /// The used inputs.
    #[inline(always)]
    pub fn inputs(&self) -> &[Vn] {
        &self.ins[..self.opc.arity()]
    }
}

/// Extra-cycle rule (`lift()`'s `pen`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PenKind {
    None,
    /// +1 taken, +1 more when the target is on another page than the fall-through.
    Branch,
    /// +1 when `word + X` crosses a page.
    Ax,
    /// +1 when `word + Y` crosses a page.
    Ay,
    /// +1 when `ptr + Y` crosses a page; `ptr` is the zero-page pointer, held in the unique
    /// at [`Template::iy_base`].
    Iy,
}

/// Control kind (`lift()`'s `ctrl[0]`); `Br` carries the flag register and polarity.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CtrlKind {
    Next,
    Br { flag: u8, pol: u8 },
    Jmp,
    JmpInd,
    Jsr,
    Rts,
    Rti,
    Brk,
    Jam,
}

impl CtrlKind {
    /// `lift()`'s name for the kind.
    pub const fn name(self) -> &'static str {
        match self {
            CtrlKind::Next => "next",
            CtrlKind::Br { .. } => "br",
            CtrlKind::Jmp => "jmp",
            CtrlKind::JmpInd => "jmpind",
            CtrlKind::Jsr => "jsr",
            CtrlKind::Rts => "rts",
            CtrlKind::Rti => "rti",
            CtrlKind::Brk => "brk",
            CtrlKind::Jam => "jam",
        }
    }
}

/// Control with operand-derived targets resolved.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Ctrl {
    Next,
    Br {
        flag: u8,
        pol: u8,
        target: u16,
        fall: u16,
    },
    Jmp(u16),
    /// Through the pointer at the address (page-wrapping high byte fetch).
    JmpInd(u16),
    Jsr(u16),
    Rts,
    Rti,
    Brk,
    Jam,
}

/// Penalty with operand-derived bases resolved.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Pen {
    None,
    Branch,
    Ax(u16),
    Ay(u16),
    /// Zero-page pointer address and the unique holding the loaded pointer.
    Iy {
        zp: u8,
        base: u8,
    },
}

/// Everything about one opcode that does not depend on its operands.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Template {
    /// First op in [`OPS`].
    pub start: u16,
    /// Number of ops.
    pub n: u8,
    /// First operand-bound input position in [`BINDS`].
    pub bind: u16,
    /// Number of operand-bound inputs.
    pub nbind: u8,
    /// Instruction length in bytes.
    pub len: u8,
    /// Base cycles.
    pub cyc: u8,
    pub pen: PenKind,
    pub ctrl: CtrlKind,
    /// Unique offset of the `(zp),Y` pointer for [`PenKind::Iy`], else 0.
    pub iy_base: u8,
}

impl Template {
    /// The template for `opcode`.
    #[inline(always)]
    pub fn of(opcode: u8) -> &'static Template {
        &TEMPLATES[opcode as usize]
    }
    /// The op templates; operand-bound constants carry their [`Slot`] and offset 0.
    #[inline(always)]
    pub fn ops(&self) -> &'static [Op] {
        &OPS[self.start as usize..self.start as usize + self.n as usize]
    }
    /// Operand-bound input positions as `op index << 1 | input index`.
    #[inline(always)]
    pub fn binds(&self) -> &'static [u8] {
        &BINDS[self.bind as usize..self.bind as usize + self.nbind as usize]
    }
    /// The stack-frame kind (`lift()`'s `stk`): the control kinds that push or pull a frame.
    pub fn stk(&self) -> Option<CtrlKind> {
        matches!(
            self.ctrl,
            CtrlKind::Jsr | CtrlKind::Brk | CtrlKind::Rts | CtrlKind::Rti
        )
        .then_some(self.ctrl)
    }
    /// Control resolved for an instruction at `pc` with operand bytes `b1`, `b2`.
    pub fn ctrl_at(&self, pc: u16, b1: u8, b2: u8) -> Ctrl {
        let w = u16::from_le_bytes([b1, b2]);
        match self.ctrl {
            CtrlKind::Next => Ctrl::Next,
            CtrlKind::Br { flag, pol } => {
                let fall = pc.wrapping_add(self.len as u16);
                Ctrl::Br {
                    flag,
                    pol,
                    target: fall.wrapping_add(b1 as i8 as u16),
                    fall,
                }
            }
            CtrlKind::Jmp => Ctrl::Jmp(w),
            CtrlKind::JmpInd => Ctrl::JmpInd(w),
            CtrlKind::Jsr => Ctrl::Jsr(w),
            CtrlKind::Rts => Ctrl::Rts,
            CtrlKind::Rti => Ctrl::Rti,
            CtrlKind::Brk => Ctrl::Brk,
            CtrlKind::Jam => Ctrl::Jam,
        }
    }
    /// Penalty resolved for operand bytes `b1`, `b2`.
    pub fn pen_at(&self, b1: u8, b2: u8) -> Pen {
        match self.pen {
            PenKind::None => Pen::None,
            PenKind::Branch => Pen::Branch,
            PenKind::Ax => Pen::Ax(u16::from_le_bytes([b1, b2])),
            PenKind::Ay => Pen::Ay(u16::from_le_bytes([b1, b2])),
            PenKind::Iy => Pen::Iy {
                zp: b1,
                base: self.iy_base,
            },
        }
    }
}

/// One instantiated instruction; its ops live in a caller-owned arena.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Insn {
    pub pc: u16,
    pub opcode: u8,
    pub b1: u8,
    pub b2: u8,
    /// First op in the arena.
    pub start: u32,
}

impl Insn {
    #[inline(always)]
    pub fn template(&self) -> &'static Template {
        Template::of(self.opcode)
    }
    /// The instruction's ops in `arena`: constants hold their values and keep their [`Slot`].
    #[inline(always)]
    pub fn ops<'a>(&self, arena: &'a [Op]) -> &'a [Op] {
        &arena[self.start as usize..self.start as usize + self.template().n as usize]
    }
    #[inline(always)]
    pub fn operands(&self) -> Operands {
        Operands::new(self.b1, self.b2)
    }
    /// Length in bytes.
    pub fn length(&self) -> u8 {
        self.template().len
    }
    pub fn ctrl(&self) -> Ctrl {
        self.template().ctrl_at(self.pc, self.b1, self.b2)
    }
    pub fn pen(&self) -> Pen {
        self.template().pen_at(self.b1, self.b2)
    }
}
