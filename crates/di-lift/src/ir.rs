//! Lifted-program IR: functions of basic blocks of P-Code ops over di-pcode varnodes.

use di_pcode::{binop, Opc, Space, Vn};
use fixedbitset::FixedBitSet;

/// Exit threshold: the entry returns once `SP >= E` (2 bytes).
pub const E: u16 = 16;
/// Cycles since the entry started, at the start of the next unaccounted instruction (4 bytes).
pub const CY: u16 = 17;
/// Register varnodes: the `lift()` file (0-15), `E` and `CY`; a function's promoted stack
/// slots follow (`NREG + i` holds the byte `i` below its entry SP).
pub const NREG: usize = 18;
/// Bit mask of the registers below [`NREG`].
pub const ALL_REGS: u32 = (1 << NREG) - 1;

/// A register varnode.
pub const fn reg(i: u16, size: u8) -> Vn {
    Vn::raw(Space::Reg, i, size)
}
/// A one-byte `lift()` register.
pub const fn r1(i: usize) -> Vn {
    reg(i as u16, 1)
}
/// A temporary.
pub const fn tmp(i: u16, size: u8) -> Vn {
    Vn::raw(Space::Unique, i, size)
}
/// A constant.
pub fn k(v: u32, size: u8) -> Vn {
    let m = mask(size as u32);
    assert!(v & m <= 0xffff, "constant out of range");
    Vn::raw(Space::Const, (v & m) as u16, size)
}
/// The cycle register.
pub const fn cy() -> Vn {
    reg(CY, 4)
}

/// All-ones mask of `size` bytes.
pub const fn mask(size: u32) -> u32 {
    if size >= 4 {
        !0
    } else {
        (1u32 << (8 * size)) - 1
    }
}

/// `CALLOTHER` operations.
pub mod other {
    /// `deopt(pc)`: leave the lifted program for the reference interpreter at `pc` with the
    /// current registers.
    pub const DEOPT: u32 = 0;
    /// `codechk(addr, value)`: 1 when `addr` is a code byte the program treats as constant and
    /// `value` differs from it.
    pub const CODECHK: u32 = 1;
    pub const NAMES: [&str; 2] = ["deopt", "codechk"];
}

/// `LOAD`/`STORE` flags in `aux`.
pub mod mem {
    /// The address may be volatile: kept verbatim and in order, at the instruction's cycle.
    pub const VOLATILE: u32 = 1;
    /// A push or pull of the hardware stack.
    pub const STACK: u32 = 2;
}

/// One lifted op: `out = opc(ins[0], ins[1])`; `aux` is the target block (`BRANCH`,
/// `CBRANCH`), target table (`BRANCHIND`), function (`CALL`), operation (`CALLOTHER`) or
/// access flags (`LOAD`, `STORE`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct LOp {
    pub opc: Opc,
    pub out: Vn,
    pub ins: [Vn; 2],
    pub aux: u32,
}

impl LOp {
    pub fn new(opc: Opc, out: Vn, a: Vn, b: Vn, aux: u32) -> Self {
        LOp {
            opc,
            out,
            ins: [a, b],
            aux,
        }
    }
    /// The used inputs.
    pub fn inputs(&self) -> &[Vn] {
        &self.ins[..self.opc.arity()]
    }
    /// Ends a block (a `CALL` is followed by the `BRANCH` to its return block).
    pub fn is_term(&self) -> bool {
        matches!(self.opc, Opc::Branch | Opc::BranchInd | Opc::Return) || self.is_deopt()
    }
    pub fn is_deopt(&self) -> bool {
        self.opc == Opc::CallOther && self.aux == other::DEOPT
    }
    pub fn volatile(&self) -> bool {
        matches!(self.opc, Opc::Load | Opc::Store) && self.aux & mem::VOLATILE != 0
    }
    /// Has an effect beyond writing `out`.
    pub fn effectful(&self) -> bool {
        match self.opc {
            Opc::Store | Opc::Branch | Opc::CBranch | Opc::BranchInd | Opc::Call | Opc::Return => {
                true
            }
            Opc::Load => self.volatile(),
            Opc::CallOther => self.aux == other::DEOPT,
            _ => false,
        }
    }
}

/// `opc(a, b)` over inputs masked to their sizes, into `size` bytes; `size0` is input 0's.
pub fn fold(opc: Opc, a: u32, b: u32, size: u32, size0: u32) -> u32 {
    let m = mask(size);
    match opc {
        Opc::Copy | Opc::IntZext => a & m,
        o => binop(o, a, b, mask(size0)) & m,
    }
}

/// Evaluate a value op over known inputs.
pub fn eval(op: &LOp, a: u32, b: u32) -> u32 {
    fold(op.opc, a, b, op.out.size(), op.ins[0].size())
}

/// A basic block: ops ending in a terminator (`CBRANCH` falls through to the next op).
#[derive(Clone, Debug, Default)]
pub struct Block {
    /// A side exit's continuation: the block that runs in place of its deopt, whose
    /// liveness in the emitted code the deopt inherits when cycles are not counted.
    pub resume: Option<u32>,
    /// The registers (bits below `NREG`) the emitted continuation of a side exit reads,
    /// replacing `resume` once optimization starts.
    pub resume_live: Option<u32>,
    /// The address of the instruction the block starts at (the deopt PC of a cycle-budget
    /// check).
    pub pc: u16,
    /// Upper bound on the cycles the block accounts.
    pub maxcyc: u32,
    /// Starts with a cycle-budget and stack-depth check (after an instruction that sets SP).
    pub check: bool,
    pub ops: Vec<LOp>,
}

impl Block {
    /// Successor blocks of the terminator and any `CBRANCH`es.
    pub fn succs<'a>(&'a self, tables: &'a [Vec<(u16, u32)>]) -> impl Iterator<Item = u32> + 'a {
        self.ops.iter().flat_map(move |op| {
            let (one, many): (Option<u32>, &[(u16, u32)]) = match op.opc {
                Opc::Branch | Opc::CBranch => (Some(op.aux), &[]),
                Opc::BranchInd => (None, &tables[op.aux as usize]),
                _ => (None, &[]),
            };
            one.into_iter().chain(many.iter().map(|&(_, b)| b))
        })
    }
    /// The block after a `CALL` terminator.
    pub fn call_return(&self) -> Option<u32> {
        let n = self.ops.len();
        (n >= 2 && self.ops[n - 2].opc == Opc::Call).then(|| self.ops[n - 1].aux)
    }
}

/// A function: block 0 is the entry.
#[derive(Clone, Debug, Default)]
pub struct Function {
    pub pc: u16,
    /// Exits the program on its entry's return (an entry) rather than returning to a caller.
    pub top: bool,
    pub blocks: Vec<Block>,
    /// `BRANCHIND` tables: `(address, block)` sorted by address; an address not in the table
    /// deopts there.
    pub tables: Vec<Vec<(u16, u32)>>,
    pub ntemps: u32,
    /// Promoted stack slots (registers `NREG..NREG + nslots`).
    pub nslots: u32,
    /// The slot indices whose stack bytes the function keeps in slot registers (the bytes of
    /// the others stay in memory).
    pub slotted: FixedBitSet,
    /// In a call-graph cycle: checks its own stack depth at entry.
    pub recursive: bool,
}

impl Function {
    /// Registers including promoted stack slots.
    pub fn nregs(&self) -> usize {
        NREG + self.nslots as usize
    }
}

/// Counts describing a lift.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    /// Instruction variants lifted (summed over functions).
    pub insns: usize,
    /// Template ops of those instructions: the raw per-instruction P-Code.
    pub raw_ops: usize,
    /// Ops as emitted: the program's full semantics (cycles, stack, control, guards) before
    /// optimization.
    pub emitted_ops: usize,
    /// Ops after optimization.
    pub ops: usize,
    pub blocks: usize,
    /// Code bytes read from memory at run time (written by the program).
    pub cells: usize,
    /// Of which opcode bytes.
    pub opcode_cells: usize,
    /// Code bytes the program treats as constant.
    pub code_bytes: usize,
    /// Stores checked against constant code.
    pub code_guards: usize,
    /// `CALL`s emitted (one per `JSR` node).
    pub calls: usize,
    /// Only pushes and pulls reach page 1, given the page-1 guards: stack traffic is removed.
    pub stack_private: bool,
    /// Accesses checked at run time not to reach page 1.
    pub stack_guards: usize,
    /// Accesses that reached page 1 in the facts or cannot be checked (each keeps the stack
    /// in memory).
    pub page1_sites: usize,
    /// Pushes and pulls lifted as stack-slot registers.
    pub slot_ops: usize,
    /// Pushes and pulls left as memory accesses after optimization.
    pub stack_ops: usize,
    /// Instruction starts the facts saw that the lift did not reach.
    pub unreached: usize,
    /// Plan nodes whose stack height relative to their function's entry is unknown.
    pub unknown_heights: usize,
    /// Functions in a call-graph cycle.
    pub recursive: usize,
    /// Plan nodes beyond the first per instruction start (one per further stack height).
    pub split_nodes: usize,
    /// Dispatches on SP after SP-setting instructions.
    pub sp_dispatch: usize,
    /// Entries specialized to (and guarded on) the SP they were called at.
    pub sp_guards: usize,
    /// Stack bytes outermost functions push that stay in memory rather than in slots.
    pub kept_bytes: usize,
}

/// A lifted program: functions plus what the executor needs to check and leave it.
#[derive(Clone, Debug, Default)]
pub struct Program {
    pub functions: Vec<Function>,
    /// `(pc, function)` per requested entry.
    pub entries: Vec<(u16, u32)>,
    /// Constant code bytes `(addr, value)`.
    pub code: Vec<(u16, u8)>,
    /// Volatile ranges `[lo, hi)`.
    pub volatile: Vec<(u32, u32)>,
    /// `CALL` pushes no return address; a deopt writes those of the active frames.
    pub elide_ret: bool,
    /// Cycles are counted (`CY`, cycle-budget checks, volatile accesses at their cycle).
    pub cycles: bool,
    pub stats: Stats,
}

impl std::fmt::Display for Program {
    fn fmt(&self, w: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let vn = |v: Vn| -> String {
            match v.space() {
                _ if v.is_none() => "-".into(),
                Space::Const => format!("#{:x}", v.off()),
                Space::Reg => match v.off() {
                    0 => "A".into(),
                    1 => "X".into(),
                    2 => "Y".into(),
                    3 => "SP".into(),
                    i @ 8..=14 => ["C", "Z", "I", "D", "B", "V", "N"][i as usize - 8].into(),
                    16 => "E".into(),
                    17 => "CY".into(),
                    i if i >= NREG as u32 => format!("s{}", i - NREG as u32),
                    i => format!("r{i}"),
                },
                Space::Unique => format!("t{}:{}", v.off(), v.size()),
                Space::Ram => format!("m{:x}", v.off()),
            }
        };
        for (fi, f) in self.functions.iter().enumerate() {
            writeln!(
                w,
                "fn {fi} @{:04x}{}",
                f.pc,
                if f.top { " (entry)" } else { "" }
            )?;
            for (bi, b) in f.blocks.iter().enumerate() {
                writeln!(w, "  b{bi} @{:04x}:", b.pc)?;
                for op in &b.ops {
                    let ins: Vec<String> = op.inputs().iter().map(|&v| vn(v)).collect();
                    let name = if op.opc == Opc::CallOther {
                        other::NAMES[op.aux as usize]
                    } else {
                        op.opc.name()
                    };
                    let aux = match op.opc {
                        Opc::Branch | Opc::CBranch => format!(" ->b{}", op.aux),
                        Opc::BranchInd => format!(" {:x?}", f.tables[op.aux as usize]),
                        Opc::Call => format!(" fn{}", op.aux),
                        Opc::Load | Opc::Store if op.aux & mem::VOLATILE != 0 => " volatile".into(),
                        _ => String::new(),
                    };
                    let out = if op.out.is_none() {
                        String::new()
                    } else {
                        format!("{} = ", vn(op.out))
                    };
                    writeln!(w, "    {out}{name} {}{aux}", ins.join(", "))?;
                }
            }
        }
        Ok(())
    }
}
