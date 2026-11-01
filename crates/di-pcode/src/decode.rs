//! Instantiating templates into a caller-owned op arena.

use crate::ir::{Insn, Op, Operands, Template};

/// The largest op count of any opcode.
pub const MAX_OPS: usize = {
    let mut i = 0;
    let mut m = 0;
    while i < 256 {
        let n = crate::templates_gen::TEMPLATES[i].n as usize;
        if n > m {
            m = n;
        }
        i += 1;
    }
    m
};

/// Instantiate `opcode b1 b2` at `pc`, appending its ops (constants bound) to `arena`.
#[inline]
pub fn instantiate_bytes(pc: u16, opcode: u8, b1: u8, b2: u8, arena: &mut Vec<Op>) -> Insn {
    let t = Template::of(opcode);
    let o = Operands::new(b1, b2);
    let start = arena.len() as u32;
    arena.extend_from_slice(t.ops());
    let ops = &mut arena[start as usize..];
    for &b in t.binds() {
        let v = &mut ops[(b >> 1) as usize].ins[(b & 1) as usize];
        *v = v.bound(&o);
    }
    Insn {
        pc,
        opcode,
        b1,
        b2,
        start,
    }
}

/// Instantiate the instruction at `pc` in a 64 KiB image (operand bytes wrap at $FFFF).
#[inline]
pub fn instantiate(mem: &[u8; 0x10000], pc: u16, arena: &mut Vec<Op>) -> Insn {
    instantiate_bytes(
        pc,
        mem[pc as usize],
        mem[pc.wrapping_add(1) as usize],
        mem[pc.wrapping_add(2) as usize],
        arena,
    )
}

/// Linear-sweep decode of every instruction starting in `[start, end)`, appending to
/// `out` and their ops to `arena`.
pub fn decode_range(
    mem: &[u8; 0x10000],
    start: u16,
    end: u32,
    arena: &mut Vec<Op>,
    out: &mut Vec<Insn>,
) {
    let mut pc = start as u32;
    while pc < end {
        let insn = instantiate(mem, pc as u16, arena);
        pc += insn.length() as u32;
        out.push(insn);
    }
}
