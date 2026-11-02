//! Random lifted programs: native code against the interpreter.

use di_lift::ir::{k, other, reg, tmp, Block, Function, LOp, Program};
use di_lift::{Backend, Exec, Runner};
use di_pcode::{Cpu, Opc, Vn};

const VALUE_OPS: [Opc; 14] = [
    Opc::Copy,
    Opc::IntZext,
    Opc::IntAdd,
    Opc::IntSub,
    Opc::IntAnd,
    Opc::IntOr,
    Opc::IntXor,
    Opc::IntLeft,
    Opc::IntRight,
    Opc::IntEqual,
    Opc::IntNotEqual,
    Opc::IntLess,
    Opc::IntLessEqual,
    Opc::IntCarry,
];

/// SplitMix64, and the temporaries defined so far in the block being generated (lifted code
/// defines a temporary before it uses it, in the same block).
struct Rng(u64, u8);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
    fn size(&mut self) -> u8 {
        [1, 1, 2, 4][self.below(4) as usize]
    }
    /// A register other than SP (3) and the exit and cycle registers, or a temporary
    /// (`out`: any, defining it; otherwise a defined one).
    fn var(&mut self, size: u8, out: bool) -> Vn {
        let t = self.below(8) as u16;
        match self.below(3) {
            0 if out || self.1 & (1 << t) != 0 => {
                self.1 |= (out as u8) << t;
                tmp(t, size)
            }
            _ => reg(
                [0, 1, 2, 4, 5, 6, 7, 8, 9, 10][self.below(10) as usize],
                size,
            ),
        }
    }
    fn input(&mut self, size: u8) -> Vn {
        if self.below(3) == 0 {
            k(self.next() as u32 & 0xffff, size.min(2))
        } else {
            self.var(size, false)
        }
    }
}

fn value_op(r: &mut Rng) -> LOp {
    let opc = VALUE_OPS[r.below(VALUE_OPS.len() as u64) as usize];
    let (osz, asz) = (r.size(), r.size());
    let b = if opc.arity() == 2 {
        if matches!(opc, Opc::IntLeft | Opc::IntRight) && r.below(2) == 0 {
            k(r.below(40) as u32, 1)
        } else {
            r.input(asz)
        }
    } else {
        Vn::NONE
    };
    let a = r.input(asz);
    LOp::new(opc, r.var(osz, true), a, b, 0)
}

fn mem_op(r: &mut Rng) -> LOp {
    let a = r.input(2);
    if r.below(2) == 0 {
        LOp::new(Opc::Load, r.var(1, true), a, Vn::NONE, 0)
    } else {
        LOp::new(Opc::Store, Vn::NONE, a, r.input(1), 0)
    }
}

/// A function of `n` blocks branching forward only; block `i`'s address is `pc + i`.
fn function(r: &mut Rng, pc: u16, n: u32, callee: Option<u32>) -> Function {
    let mut blocks = Vec::new();
    let mut tables = Vec::new();
    for i in 0..n {
        r.1 = 0;
        let mut ops: Vec<LOp> = (0..r.below(12))
            .map(|_| {
                if r.below(4) == 0 {
                    mem_op(r)
                } else {
                    value_op(r)
                }
            })
            .collect();
        let later = |r: &mut Rng| i + 1 + r.below((n - i - 1) as u64) as u32;
        if i + 1 == n {
            ops.push(LOp::new(Opc::Return, Vn::NONE, k(0x1234, 2), Vn::NONE, 0));
        } else {
            if r.below(2) == 0 {
                let c = r.var(1, false);
                ops.push(LOp::new(Opc::CBranch, Vn::NONE, c, Vn::NONE, later(r)));
            }
            match (r.below(8), callee) {
                (0, _) => {
                    let to = r.input(2);
                    ops.push(LOp::new(
                        Opc::CallOther,
                        Vn::NONE,
                        to,
                        Vn::NONE,
                        other::DEOPT,
                    ));
                }
                (1, _) => {
                    let t: Vec<(u16, u32)> = (i + 1..n).map(|b| (pc + b as u16, b)).collect();
                    let sel = reg(5, 2);
                    let pick = k((pc as u32 + 1 + i + r.below(3) as u32) & 0xffff, 2);
                    ops.push(LOp::new(Opc::Copy, sel, pick, Vn::NONE, 0));
                    ops.push(LOp::new(
                        Opc::BranchInd,
                        Vn::NONE,
                        sel,
                        Vn::NONE,
                        tables.len() as u32,
                    ));
                    tables.push(t);
                }
                (2 | 3, Some(f)) => {
                    let ra = k(pc as u32 + i, 2);
                    ops.push(LOp::new(Opc::Call, Vn::NONE, ra, Vn::NONE, f));
                    ops.push(LOp::new(Opc::Branch, Vn::NONE, Vn::NONE, Vn::NONE, i + 1));
                }
                (4, _) => {
                    let (a, v) = (r.input(2), r.input(1));
                    let out = r.var(1, true);
                    ops.push(LOp::new(Opc::CallOther, out, a, v, other::CODECHK));
                }
                _ => {}
            }
        }
        blocks.push(Block {
            pc: pc + i as u16,
            ops,
            ..Default::default()
        });
    }
    Function {
        pc,
        top: callee.is_some(),
        blocks,
        tables,
        ntemps: 8,
        ..Default::default()
    }
}

#[test]
fn random_programs_match_the_interpreter() {
    let mut r = Rng(1, 0);
    for case in 0..1000 {
        let (n0, n1) = (1 + r.below(8) as u32, 1 + r.below(4) as u32);
        let p = Program {
            functions: vec![
                function(&mut r, 0x2000, n0, Some(1)),
                function(&mut r, 0x3000, n1, None),
            ],
            entries: vec![(0x2000, 0)],
            code: (0..64).map(|i| (0x2000 + i, r.next() as u8)).collect(),
            ..Default::default()
        };
        let x = Exec::new(&p);
        assert!(x.jit().is_some());
        let mut mem = Box::new([0xeau8; 0x10000]);
        for (a, b) in mem.iter_mut().enumerate().take(0x1000) {
            *b = (a as u8).wrapping_mul(37);
        }
        mem[0x1fff] = 0x60;
        let mut cpu = Cpu::new();
        let mut reg = [0u8; 16];
        reg.iter_mut().for_each(|v| *v = r.next() as u8);
        reg[3] = 0xf0;
        cpu.set_reg(reg);
        let out = [Backend::Interp, Backend::Jit].map(|backend| {
            let (mut c, mut m) = (cpu, mem.clone());
            let mut run = Runner::new(&x);
            run.backend = backend;
            let o = run.call(0, 7, &mut c, &mut *m, 200);
            (o, c, m, run.invalid)
        });
        assert_eq!(out[0].0, out[1].0, "outcome, case {case}\n{p}");
        assert_eq!(out[0].1, out[1].1, "state, case {case}\n{p}");
        assert!(out[0].2 == out[1].2, "memory, case {case}\n{p}");
        assert_eq!(out[0].3, out[1].3, "invalidation, case {case}\n{p}");
    }
}
