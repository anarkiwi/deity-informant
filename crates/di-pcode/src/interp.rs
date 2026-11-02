//! Reference interpreter over the IR, step-for-step equal to `deity_informant.vm.PcodeVM`.

use crate::ir::{
    binop, CtrlKind, Insn, Op, Opc, Operands, PenKind, Template, Vn, FILE, IMM, SLOT_BASE,
    UNIQ_BASE,
};

/// Memory as the interpreter sees it. The host decides what each address does.
pub trait Bus {
    fn read(&mut self, addr: u16) -> u8;
    fn write(&mut self, addr: u16, val: u8);
    /// Instruction-byte fetch.
    #[inline(always)]
    fn fetch(&mut self, addr: u16) -> u8 {
        self.read(addr)
    }
    /// The cycle count at the start of the instruction whose accesses follow. Drivers that
    /// model timing ([`crate::Cpu::run_clocked`], lifted programs) call it; plain stepping
    /// does not.
    #[inline(always)]
    fn now(&mut self, _cycles: u64) {}
    /// RAM at `addr`, bypassing any device decoded there (a bank map's RAM pages).
    #[inline(always)]
    fn ram_read(&mut self, addr: u16) -> u8 {
        self.read(addr)
    }
    /// Write RAM at `addr`, bypassing any device decoded there.
    #[inline(always)]
    fn ram_write(&mut self, addr: u16, val: u8) {
        self.write(addr, val)
    }
    /// Flat memory standing for this bus, for compiled code to access directly: what
    /// `read`/`write` reach at non-volatile addresses and what `ram_read`/`ram_write` reach,
    /// each 64 KiB and without side effects. `None` routes every access through the bus.
    #[inline(always)]
    fn flat(&mut self) -> Option<(*mut u8, *mut u8)> {
        None
    }
}

impl Bus for [u8; 0x10000] {
    #[inline(always)]
    fn read(&mut self, addr: u16) -> u8 {
        self[addr as usize]
    }
    #[inline(always)]
    fn write(&mut self, addr: u16, val: u8) {
        self[addr as usize] = val;
    }
    fn flat(&mut self) -> Option<(*mut u8, *mut u8)> {
        let p = self.as_mut_ptr();
        Some((p, p))
    }
}

/// `lift()` register-file indices.
pub mod reg {
    pub const A: usize = 0;
    pub const X: usize = 1;
    pub const Y: usize = 2;
    pub const SP: usize = 3;
    pub const C: usize = 8;
    pub const Z: usize = 9;
    pub const I: usize = 10;
    pub const D: usize = 11;
    pub const B: usize = 12;
    pub const V: usize = 13;
    pub const N: usize = 14;
    /// `(register, bit)` of each flag in the status byte NV-BDIZC.
    pub const STATUS_BITS: [(usize, u8); 6] = [(C, 0), (Z, 1), (I, 2), (D, 3), (V, 6), (N, 7)];
}

/// Why [`Cpu::run_clocked`] stopped.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Halt {
    /// SP rose above the threshold (the entry returned).
    Sp,
    /// The cycle budget ran out.
    Cycles,
    /// A JAM executed.
    Jam,
}

/// A JAM opcode executed at `pc`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Jam {
    pub pc: u16,
}

/// Architectural state: the `lift()` register file, PC and the cycle count. Registers live
/// in the interpreter's value file (`FILE` words: registers, operand slots, uniques), so an
/// instruction reads and writes them in place.
#[derive(Clone, Copy)]
pub struct Cpu {
    f: [u32; FILE],
    pub pc: u16,
    pub cycles: u64,
}

impl Default for Cpu {
    fn default() -> Self {
        let mut f = [0; FILE];
        f[reg::SP] = 0xff;
        Cpu {
            f,
            pc: 0,
            cycles: 0,
        }
    }
}

impl PartialEq for Cpu {
    fn eq(&self, o: &Self) -> bool {
        (self.reg(), self.pc, self.cycles) == (o.reg(), o.pc, o.cycles)
    }
}

impl Eq for Cpu {}

impl std::fmt::Debug for Cpu {
    fn fmt(&self, fm: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        fm.debug_struct("Cpu")
            .field("reg", &self.reg())
            .field("pc", &self.pc)
            .field("cycles", &self.cycles)
            .finish()
    }
}

#[inline(always)]
fn load<B: Bus>(bus: &mut B, addr: u32, size: u32) -> u32 {
    (0..size).fold(0, |v, i| {
        v | (bus.read((addr + i) as u16) as u32) << (8 * i)
    })
}

#[inline(always)]
fn store<B: Bus>(bus: &mut B, addr: u32, val: u32, size: u32) {
    for i in 0..size {
        bus.write((addr + i) as u16, (val >> (8 * i)) as u8);
    }
}

impl Cpu {
    pub fn new() -> Self {
        Self::default()
    }

    /// Register `i` (a [`reg`] index).
    #[inline(always)]
    pub fn r(&self, i: usize) -> u8 {
        self.f[i] as u8
    }
    #[inline(always)]
    pub fn set_r(&mut self, i: usize, v: u8) {
        self.f[i] = v as u32;
    }
    /// The 16-entry register file.
    pub fn reg(&self) -> [u8; 16] {
        std::array::from_fn(|i| self.r(i))
    }
    pub fn set_reg(&mut self, reg: [u8; 16]) {
        for (i, v) in reg.into_iter().enumerate() {
            self.set_r(i, v);
        }
    }
    /// Unique `off` as the last instruction left it.
    pub fn unique(&self, off: usize) -> u32 {
        self.f[UNIQ_BASE + off]
    }

    /// The status byte (bit 5 set; B as given).
    pub fn status(&self, brk: bool) -> u8 {
        reg::STATUS_BITS
            .iter()
            .fold(0x20 | (brk as u8) << 4, |p, &(i, s)| p | self.r(i) << s)
    }

    pub fn set_status(&mut self, p: u8) {
        for (i, s) in reg::STATUS_BITS {
            self.set_r(i, (p >> s) & 1);
        }
    }

    /// Run `ops` (an instruction's P-Code) with operand slots `o`.
    #[inline(always)]
    pub fn exec_ops<B: Bus>(&mut self, bus: &mut B, ops: &[Op], o: &Operands) {
        let f = &mut self.f;
        f[SLOT_BASE..UNIQ_BASE].copy_from_slice(&o.0);
        #[inline(always)]
        fn get<B: Bus>(f: &[u32; FILE], bus: &mut B, v: Vn) -> u32 {
            match v.index() {
                i if i < FILE => f[i],
                IMM => v.off(),
                _ => load(bus, v.off(), v.size()),
            }
        }
        for op in ops {
            let a = get(f, bus, op.ins[0]);
            let val = match op.opc {
                Opc::Copy | Opc::IntZext => a,
                Opc::Load if op.out.size() == 1 => bus.read(a as u16) as u32,
                Opc::Load => load(bus, a & 0xffff, op.out.size()),
                Opc::Store => {
                    let (vn, b) = (op.ins[1], get(f, bus, op.ins[1]));
                    if vn.size() == 1 {
                        bus.write(a as u16, b as u8);
                    } else {
                        store(bus, a & 0xffff, b, vn.size());
                    }
                    continue;
                }
                opc => binop(opc, a, get(f, bus, op.ins[1]), op.ins[0].mask()),
            };
            let (out, val) = (op.out, val & op.out.mask());
            match out.index() {
                i if i < FILE => f[i] = val,
                _ => store(bus, out.off(), val, out.size()),
            }
        }
    }

    #[inline(always)]
    fn push<B: Bus>(&mut self, bus: &mut B, v: u8) {
        let sp = self.r(reg::SP);
        bus.write(0x100 | sp as u16, v);
        self.set_r(reg::SP, sp.wrapping_sub(1));
    }

    #[inline(always)]
    fn pull<B: Bus>(&mut self, bus: &mut B) -> u8 {
        let sp = self.r(reg::SP).wrapping_add(1);
        self.set_r(reg::SP, sp);
        bus.read(0x100 | sp as u16)
    }

    /// Execute one instruction at `pc` given its template, ops and operand bytes.
    #[inline(always)]
    fn execute<B: Bus>(
        &mut self,
        bus: &mut B,
        pc: u16,
        t: &Template,
        ops: &[Op],
        b1: u8,
        b2: u8,
    ) -> Result<(), Jam> {
        let o = Operands::new(b1, b2);
        self.exec_ops(bus, ops, &o);
        let w = o.0[2];
        let mut cyc = t.cyc as u64;
        let cross = |base: u32, idx: u8| ((base ^ (base + idx as u32)) & 0xff00 != 0) as u64;
        match t.pen {
            PenKind::Ax => cyc += cross(w, self.r(reg::X)),
            PenKind::Ay => cyc += cross(w, self.r(reg::Y)),
            PenKind::Iy => cyc += cross(self.unique(t.iy_base as usize), self.r(reg::Y)),
            PenKind::None | PenKind::Branch => {}
        }
        let len = t.len as u16;
        let next = pc.wrapping_add(len);
        self.pc = match t.ctrl {
            CtrlKind::Next => next,
            CtrlKind::Br { flag, pol } => {
                if self.r(flag as usize) == pol {
                    let target = next.wrapping_add(b1 as i8 as u16);
                    cyc += 1 + ((next ^ target) & 0xff00 != 0) as u64;
                    target
                } else {
                    next
                }
            }
            CtrlKind::Jmp => w as u16,
            CtrlKind::JmpInd => {
                let lo = bus.read(w as u16);
                let hi = bus.read((w as u16 & 0xff00) | (w as u16).wrapping_add(1) & 0xff);
                u16::from_le_bytes([lo, hi])
            }
            CtrlKind::Jsr => {
                let ret = next.wrapping_sub(1);
                self.push(bus, (ret >> 8) as u8);
                self.push(bus, ret as u8);
                w as u16
            }
            CtrlKind::Rts => {
                let lo = self.pull(bus);
                let hi = self.pull(bus);
                u16::from_le_bytes([lo, hi]).wrapping_add(1)
            }
            CtrlKind::Rti => {
                let p = self.pull(bus);
                self.set_status(p);
                let lo = self.pull(bus);
                let hi = self.pull(bus);
                u16::from_le_bytes([lo, hi])
            }
            CtrlKind::Brk => {
                let ret = pc.wrapping_add(2);
                self.push(bus, (ret >> 8) as u8);
                self.push(bus, ret as u8);
                let p = self.status(true);
                self.push(bus, p);
                self.set_r(reg::I, 1);
                u16::from_le_bytes([bus.read(0xfffe), bus.read(0xffff)])
            }
            CtrlKind::Jam => {
                self.cycles += cyc;
                return Err(Jam { pc });
            }
        };
        self.cycles += cyc;
        Ok(())
    }

    /// Fetch and execute the instruction at `self.pc` straight from its template.
    #[inline]
    pub fn step<B: Bus>(&mut self, bus: &mut B) -> Result<(), Jam> {
        let pc = self.pc;
        let t = Template::of(bus.fetch(pc));
        let b1 = if t.len > 1 {
            bus.fetch(pc.wrapping_add(1))
        } else {
            0
        };
        let b2 = if t.len > 2 {
            bus.fetch(pc.wrapping_add(2))
        } else {
            0
        };
        self.execute(bus, pc, t, t.ops(), b1, b2)
    }

    /// Execute an instantiated instruction (at `insn.pc`) whose ops are in `arena`.
    pub fn exec<B: Bus>(&mut self, bus: &mut B, insn: &Insn, arena: &[Op]) -> Result<(), Jam> {
        self.execute(
            bus,
            insn.pc,
            insn.template(),
            insn.ops(arena),
            insn.b1,
            insn.b2,
        )
    }

    /// Step until SP rises above `sp_above` or at least `max_cycles` have elapsed (both
    /// checked before each instruction) or a JAM executes, telling the bus each
    /// instruction's start cycle: `run_to_rts`'s loop.
    pub fn run_clocked<B: Bus>(&mut self, bus: &mut B, sp_above: i32, end: u64) -> Halt {
        loop {
            if self.r(reg::SP) as i32 > sp_above {
                return Halt::Sp;
            }
            if self.cycles >= end {
                return Halt::Cycles;
            }
            bus.now(self.cycles);
            if self.step(bus).is_err() {
                return Halt::Jam;
            }
        }
    }

    /// Step up to `max_steps` instructions; the count executed (a JAM counts) and the JAM.
    pub fn run<B: Bus>(&mut self, bus: &mut B, max_steps: u64) -> (u64, Option<Jam>) {
        for n in 0..max_steps {
            if let Err(j) = self.step(bus) {
                return (n + 1, Some(j));
            }
        }
        (max_steps, None)
    }
}
