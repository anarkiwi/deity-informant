//! Executing a lifted program over a [`Bus`], leaving for the reference [`Cpu`] at deopts.

use crate::ir::{mask, mem, other, Program, CY, E, NREG};
use crate::opt::checkpoints;
use di_pcode::{reg as R, Bus, Cpu, Halt, Opc, Space, Vn};
use fixedbitset::FixedBitSet;
use rustc_hash::FxHashMap;

const IMM: u32 = 1 << 31;
const NOCHECK: u32 = u32::MAX;
/// Longest run the 32-bit cycle register covers before the executor hands over to the `Cpu`.
const SPAN: u64 = 1 << 31;

#[derive(Clone, Copy, Debug)]
struct X {
    opc: Opc,
    osz: u8,
    asz: u8,
    out: u32,
    a: u32,
    b: u32,
    aux: u32,
}

#[derive(Clone, Copy, Debug)]
struct XBlock {
    start: u32,
    pc: u16,
    /// Cycle bound to the next check, at cycle-budget checkpoints.
    check: u32,
    /// Bytes the stack may grow by before the next check.
    depth: u32,
}

/// A lifted program compiled for execution.
#[derive(Clone, Debug)]
pub struct Exec {
    ops: Vec<X>,
    blocks: Vec<XBlock>,
    tables: Vec<Vec<(u16, u32)>>,
    fentry: Vec<u32>,
    entries: Vec<(u16, u32)>,
    width: usize,
    code: Vec<(u16, u8)>,
    constcode: FixedBitSet,
    img: Box<[u8]>,
    elide_ret: bool,
}

fn operand(v: Vn, temps: u32) -> u32 {
    match v.space() {
        Space::Const => IMM | v.off(),
        Space::Reg => v.off(),
        Space::Unique => temps + v.off(),
        Space::Ram => unreachable!("lifted programs address memory through LOAD/STORE"),
    }
}

impl Exec {
    pub fn new(p: &Program) -> Self {
        let mut x = Exec {
            ops: Vec::new(),
            blocks: Vec::new(),
            tables: Vec::new(),
            fentry: Vec::new(),
            entries: p.entries.clone(),
            width: NREG
                + p.functions
                    .iter()
                    .map(|f| f.ntemps as usize)
                    .max()
                    .unwrap_or(0),
            code: p.code.clone(),
            constcode: FixedBitSet::with_capacity(0x10000),
            img: vec![0u8; 0x10000].into_boxed_slice(),
            elide_ret: p.elide_ret,
        };
        for &(a, v) in &p.code {
            x.constcode.insert(a as usize);
            x.img[a as usize] = v;
        }
        let t0 = NREG as u32;
        for f in &p.functions {
            let base = x.blocks.len() as u32;
            let tbase = x.tables.len() as u32;
            x.fentry.push(base);
            let cps = checkpoints(f);
            let bounds = bounds(f, &cps);
            for (bi, b) in f.blocks.iter().enumerate() {
                let (check, depth) = if cps.contains(bi) {
                    bounds[bi]
                } else {
                    (NOCHECK, 0)
                };
                x.blocks.push(XBlock {
                    start: x.ops.len() as u32,
                    pc: b.pc,
                    check,
                    depth,
                });
                for op in &b.ops {
                    let aux = match op.opc {
                        Opc::Branch | Opc::CBranch => base + op.aux,
                        Opc::BranchInd => tbase + op.aux,
                        _ => op.aux,
                    };
                    let opd = |v: Vn| if v.is_none() { IMM } else { operand(v, t0) };
                    x.ops.push(X {
                        opc: op.opc,
                        osz: op.out.size() as u8,
                        asz: op.ins[0].size() as u8,
                        out: if op.out.is_none() {
                            0
                        } else {
                            operand(op.out, t0)
                        },
                        a: opd(op.ins[0]),
                        b: opd(op.ins[1]),
                        aux,
                    });
                }
            }
            for t in &f.tables {
                x.tables
                    .push(t.iter().map(|&(a, b)| (a, base + b)).collect());
            }
        }
        x
    }
}

/// Per block: the most cycles and the deepest stack growth on any path to the next
/// checkpoint (or an exit), counting the block itself.
fn bounds(f: &crate::ir::Function, cps: &FixedBitSet) -> Vec<(u32, u32)> {
    // Per block: cycles, net SP change and lowest SP change reached.
    let local: Vec<(u32, i32, i32)> = f
        .blocks
        .iter()
        .map(|b| {
            let (mut net, mut low) = (0i32, 0i32);
            for op in &b.ops {
                if op.out == crate::ir::r1(R::SP) {
                    let c = op.ins[1].off() as i32;
                    net = match op.opc {
                        _ if op.ins[0] != op.out || op.ins[1].space() != Space::Const => 0,
                        Opc::IntAdd => net + if c < 0x80 { c } else { c - 0x100 },
                        Opc::IntSub => net - if c < 0x80 { c } else { c - 0x100 },
                        _ => 0,
                    };
                    low = low.min(net);
                }
            }
            (b.maxcyc, net, low)
        })
        .collect();
    let mut memo: Vec<Option<(u32, i32)>> = vec![None; f.blocks.len()];
    fn go(
        f: &crate::ir::Function,
        cps: &FixedBitSet,
        local: &[(u32, i32, i32)],
        memo: &mut Vec<Option<(u32, i32)>>,
        b: usize,
    ) -> (u32, i32) {
        if let Some(v) = memo[b] {
            return v;
        }
        memo[b] = Some((0, 0));
        let (cyc, net, low) = local[b];
        let (mut c, mut l) = (0u32, 0i32);
        let succ: Vec<u32> = f.blocks[b].succs(&f.tables).collect();
        for s in succ {
            if !cps.contains(s as usize) {
                let (sc, sl) = go(f, cps, local, memo, s as usize);
                c = c.max(sc);
                l = l.min(sl);
            }
        }
        let v = (cyc + c, low.min(net + l));
        memo[b] = Some(v);
        v
    }
    (0..f.blocks.len())
        .map(|b| {
            let (c, l) = go(f, cps, &local, &mut memo, b);
            (c, (-l) as u32)
        })
        .collect()
}

impl Exec {
    /// Every byte the program treats as constant code holds its lifted value in `bus`.
    pub fn matches<B: Bus>(&self, bus: &mut B) -> bool {
        self.code.iter().all(|&(a, v)| bus.read(a) == v)
    }
}

/// Why a run ended, and where it deopted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Outcome {
    pub halt: Halt,
    /// The PC the lifted program handed over to the reference interpreter at.
    pub deopt: Option<u16>,
}

/// Reusable execution state for one [`Exec`]: the value file, call frames, and whether a
/// write to constant code has invalidated the program.
pub struct Runner<'a> {
    x: &'a Exec,
    f: Vec<u32>,
    /// Active `CALL`s: resume op, return address, SP before the `JSR`.
    frames: Vec<(u32, u16, u8)>,
    pub invalid: bool,
    /// Deopts per PC.
    pub deopts: FxHashMap<u16, u64>,
}

impl<'a> Runner<'a> {
    pub fn new(x: &'a Exec) -> Self {
        Runner {
            x,
            f: vec![0; x.width],
            frames: Vec::new(),
            invalid: false,
            deopts: FxHashMap::default(),
        }
    }

    /// `run_to_rts`: push the dummy return `$0001`, set A and run entry `entry` until it
    /// returns (SP back above its start), at most `max_cycles` past the start or a JAM.
    pub fn call<B: Bus>(
        &mut self,
        entry: usize,
        acc: u8,
        cpu: &mut Cpu,
        bus: &mut B,
        max_cycles: u64,
    ) -> Outcome {
        let start = cpu.r(R::SP);
        for v in [0x00, 0x01] {
            let sp = cpu.r(R::SP);
            bus.write(0x100 | sp as u16, v);
            cpu.set_r(R::SP, sp.wrapping_sub(1));
        }
        cpu.set_r(R::A, acc);
        cpu.pc = self.x.entries[entry].0;
        self.run(entry, cpu, bus, start as i32 - 1, max_cycles)
    }

    /// Run entry `entry` from `cpu` (its PC is set to the entry's) until SP exceeds
    /// `sp_above` or at least `max_cycles` have elapsed (checked before each instruction), or a
    /// JAM. The lifted code assumes the entry returns at SP + 2.
    pub fn run<B: Bus>(
        &mut self,
        entry: usize,
        cpu: &mut Cpu,
        bus: &mut B,
        sp_above: i32,
        max_cycles: u64,
    ) -> Outcome {
        let (pc, fi) = self.x.entries[entry];
        cpu.pc = pc;
        let c0 = cpu.cycles;
        let end = c0.saturating_add(max_cycles).saturating_add(1);
        let sp = cpu.r(R::SP) as i32;
        if self.invalid {
            self.invalid = !self.x.matches(bus);
        }
        if self.invalid || sp_above != sp + 1 || sp > 0xfd || sp > sp_above {
            return self.fallback(cpu, bus, pc, sp_above, end);
        }
        let budget = (end - c0).min(SPAN) as u32;
        let f = &mut self.f;
        for (i, v) in cpu.reg().into_iter().enumerate() {
            f[i] = v as u32;
        }
        f[E as usize] = sp as u32 + 2;
        f[CY as usize] = 0;
        self.frames.clear();
        let x = self.x;
        let ops = &x.ops[..];
        let get = |f: &[u32], o: u32| {
            if o & IMM != 0 {
                o & !IMM
            } else {
                f[o as usize]
            }
        };
        macro_rules! enter {
            ($b:expr) => {{
                let blk = x.blocks[$b as usize];
                if blk.check != NOCHECK
                    && (self.f[CY as usize] as u64 + blk.check as u64 >= budget as u64
                        || self.f[R::SP] < blk.depth)
                {
                    break (blk.pc, false);
                }
                blk.start as usize
            }};
        }
        let first = x.blocks[x.fentry[fi as usize] as usize];
        let (dpc, exit) = if first.check >= budget || (sp as u32) < first.depth {
            (first.pc, false)
        } else {
            let mut ip = first.start as usize;
            loop {
                let o = ops[ip];
                ip += 1;
                let f = &mut self.f;
                match o.opc {
                    Opc::Copy | Opc::IntZext => {
                        f[o.out as usize] = get(f, o.a) & mask(o.osz as u32)
                    }
                    Opc::Load => {
                        if o.aux & mem::VOLATILE != 0 {
                            bus.now(c0 + f[CY as usize] as u64);
                        }
                        f[o.out as usize] = bus.read(get(f, o.a) as u16) as u32;
                    }
                    Opc::Store => {
                        if o.aux & mem::VOLATILE != 0 {
                            bus.now(c0 + f[CY as usize] as u64);
                        }
                        bus.write(get(f, o.a) as u16, get(f, o.b) as u8);
                    }
                    Opc::Branch => ip = enter!(o.aux),
                    Opc::CBranch => {
                        if get(f, o.a) != 0 {
                            ip = enter!(o.aux);
                        }
                    }
                    Opc::BranchInd => {
                        let t = get(f, o.a) as u16;
                        let tb = &x.tables[o.aux as usize];
                        match tb.binary_search_by_key(&t, |e| e.0) {
                            Ok(i) => ip = enter!(tb[i].1),
                            Err(_) => break (t, false),
                        }
                    }
                    Opc::Call => {
                        let sp = (f[R::SP] as u8).wrapping_add(2);
                        self.frames.push((ip as u32, get(f, o.a) as u16, sp));
                        ip = enter!(x.fentry[o.aux as usize]);
                    }
                    Opc::Return => match self.frames.pop() {
                        Some((r, _, _)) => ip = r as usize,
                        None => break (get(f, o.a) as u16, true),
                    },
                    Opc::CallOther => {
                        if o.aux == other::DEOPT {
                            break (get(f, o.a) as u16, false);
                        }
                        let a = get(f, o.a) as usize;
                        let hit = x.constcode.contains(a) && x.img[a] as u32 != get(f, o.b);
                        self.invalid |= hit;
                        f[o.out as usize] = hit as u32;
                    }
                    opc => {
                        let (a, b) = (get(f, o.a), get(f, o.b));
                        f[o.out as usize] =
                            di_pcode::binop(opc, a, b, mask(o.asz as u32)) & mask(o.osz as u32);
                    }
                }
            }
        };
        let f = &self.f;
        let mut reg = [0u8; 16];
        for (i, r) in reg.iter_mut().enumerate() {
            *r = f[i] as u8;
        }
        cpu.set_reg(reg);
        cpu.pc = dpc;
        cpu.cycles = c0 + f[CY as usize] as u64;
        if exit {
            return Outcome {
                halt: Halt::Sp,
                deopt: None,
            };
        }
        if self.x.elide_ret {
            for &(_, ra, sp) in &self.frames {
                bus.write(0x100 | sp as u16, (ra >> 8) as u8);
                bus.write(0x100 | sp.wrapping_sub(1) as u16, ra as u8);
            }
        }
        self.fallback(cpu, bus, dpc, sp_above, end)
    }

    fn fallback<B: Bus>(
        &mut self,
        cpu: &mut Cpu,
        bus: &mut B,
        pc: u16,
        sp_above: i32,
        end: u64,
    ) -> Outcome {
        *self.deopts.entry(pc).or_default() += 1;
        cpu.pc = pc;
        Outcome {
            halt: cpu.run_clocked(bus, sp_above, end),
            deopt: Some(pc),
        }
    }
}
