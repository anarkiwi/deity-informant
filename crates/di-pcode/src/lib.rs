//! Compact 6502/6510 P-Code IR templated from `deity_informant.lifter.lift`, and a
//! reference interpreter over it.
//!
//! [`Template::of`] gives an opcode's operand-independent ops, length, cycles, penalty
//! and control; [`instantiate`] binds an instruction's operand bytes into a caller-owned
//! arena; [`Cpu::step`] executes one instruction on any [`Bus`].

pub mod decode;
pub mod interp;
pub mod ir;
#[rustfmt::skip]
mod templates_gen;

pub use decode::{decode_range, instantiate, instantiate_bytes, MAX_OPS};
pub use interp::{reg, Bus, Cpu, Halt, Jam};
pub use ir::{
    binop, Ctrl, CtrlKind, Insn, Op, Opc, Operands, Pen, PenKind, Slot, Space, Template, Vn, FILE,
    UNIQ_BASE,
};
pub use templates_gen::{BINDS, OPS, TEMPLATES, UNIQUE_SIZE};
