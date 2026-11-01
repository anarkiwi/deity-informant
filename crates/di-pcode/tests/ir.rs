use di_pcode::ir::{c, cs, m, r, u};
use di_pcode::{
    decode_range, instantiate, reg, Bus, Cpu, Ctrl, CtrlKind, Jam, Op, Opc, Operands, Pen, PenKind,
    Slot, Space, Template, Vn, MAX_OPS, OPS, TEMPLATES, UNIQUE_SIZE,
};

fn image(org: u16, prog: &[u8]) -> Box<[u8; 0x10000]> {
    let mut m = Box::new([0u8; 0x10000]);
    m[org as usize..org as usize + prog.len()].copy_from_slice(prog);
    m
}

fn run_at(mem: &mut [u8; 0x10000], pc: u16, cpu: &mut Cpu) -> Result<(), Jam> {
    cpu.pc = pc;
    cpu.step(mem)
}

#[test]
fn varnode_packing_round_trips() {
    let v = Vn::new(Space::Unique, 33, 2, Slot::B2Inc);
    assert_eq!(
        (v.space(), v.off(), v.size(), v.slot()),
        (Space::Unique, 33, 2, Slot::B2Inc)
    );
    assert_eq!(v.mask(), 0xffff);
    assert!(Vn::NONE.is_none() && !c(0, 1).is_none());
    let o = Operands::new(0xff, 0x12);
    assert_eq!(o.0, [0, 0xff, 0x12ff, 0, 0x13]);
    assert_eq!(
        cs(Slot::Word, 2).bound(&o),
        Vn::new(Space::Const, 0x12ff, 2, Slot::Word)
    );
    assert_eq!(cs(Slot::B1, 1).bound(&o).off(), 0xff);
    assert_eq!(r(3).bound(&o), r(3));
}

#[test]
fn templates_cover_the_op_pool() {
    let mut next = 0;
    for t in TEMPLATES.iter() {
        assert_eq!(t.start as usize, next);
        assert!((1..=3).contains(&t.len));
        next += t.n as usize;
        let bound: Vec<u8> = t
            .ops()
            .iter()
            .enumerate()
            .flat_map(|(i, op)| (0..2).map(move |j| (i, j, op.ins[j])))
            .filter(|&(_, _, v)| v.slot() != Slot::Fixed)
            .map(|(i, j, _)| (i << 1 | j) as u8)
            .collect();
        assert_eq!(bound, t.binds());
        for op in t.ops() {
            assert!(op.inputs().len() == op.opc.arity());
            for v in op.inputs().iter().chain([&op.out]) {
                if v.space() == Space::Unique {
                    assert!(v.off() as usize + v.size() as usize <= UNIQUE_SIZE);
                }
            }
        }
    }
    assert_eq!(next, OPS.len());
    assert_eq!(
        MAX_OPS,
        TEMPLATES.iter().map(|t| t.n as usize).max().unwrap()
    );
    assert_eq!(Template::of(0x20).stk(), Some(CtrlKind::Jsr));
    assert_eq!(Template::of(0xea).stk(), None);
    assert_eq!(Opc::IntLessEqual.name(), "INT_LESSEQUAL");
    assert_eq!(CtrlKind::JmpInd.name(), "jmpind");
}

#[test]
fn instantiate_binds_operand_slots() {
    // LDA $12F0,X ; JMP ($30FF) ; BNE -2 ; LDA ($80),Y
    let mem = image(
        0x1000,
        &[0xbd, 0xf0, 0x12, 0x6c, 0xff, 0x30, 0xd0, 0xfe, 0xb1, 0x80],
    );
    let mut arena = Vec::new();
    let mut insns = Vec::new();
    decode_range(&mem, 0x1000, 0x100a, &mut arena, &mut insns);
    assert_eq!(
        insns.iter().map(|i| i.pc).collect::<Vec<_>>(),
        [0x1000, 0x1003, 0x1006, 0x1008]
    );
    let lda = &insns[0];
    let words: Vec<u32> = lda
        .ops(&arena)
        .iter()
        .flat_map(|op| op.inputs().to_vec())
        .filter(|v| v.slot() == Slot::Word)
        .map(|v| v.off())
        .collect();
    assert_eq!(words, [0x12f0]);
    assert_eq!(lda.pen(), Pen::Ax(0x12f0));
    assert_eq!(insns[1].ctrl(), Ctrl::JmpInd(0x30ff));
    assert_eq!(
        insns[2].ctrl(),
        Ctrl::Br {
            flag: reg::Z as u8,
            pol: 0,
            target: 0x1006,
            fall: 0x1008
        }
    );
    assert_eq!(insns[2].pen(), Pen::Branch);
    assert!(matches!(insns[3].pen(), Pen::Iy { zp: 0x80, .. }));
    assert_eq!(insns[3].length(), 2);
    let again = instantiate(&mem, 0x1000, &mut arena);
    assert_eq!(again.ops(&arena), lda.ops(&arena));
    assert_eq!(Template::of(0xea).pen_at(0, 0), Pen::None);
    assert_eq!(Template::of(0x59).pen_at(0x34, 0x12), Pen::Ay(0x1234));
}

#[test]
fn loads_set_flags_and_cycles() {
    let mut mem = image(0x1000, &[0xa9, 0x80, 0xa9, 0x00]);
    let mut cpu = Cpu::new();
    run_at(&mut mem, 0x1000, &mut cpu).unwrap();
    assert_eq!((cpu.r(reg::A), cpu.r(reg::N), cpu.r(reg::Z)), (0x80, 1, 0));
    cpu.step(&mut *mem).unwrap();
    assert_eq!(
        (cpu.r(reg::A), cpu.r(reg::Z), cpu.pc, cpu.cycles),
        (0, 1, 0x1004, 4)
    );
}

#[test]
fn adc_carry_and_overflow() {
    let mut mem = image(0x1000, &[0x69, 0x50]);
    let mut cpu = Cpu::new();
    cpu.set_r(reg::A, 0x50);
    cpu.set_r(reg::C, 1);
    run_at(&mut mem, 0x1000, &mut cpu).unwrap();
    assert_eq!((cpu.r(reg::A), cpu.r(reg::V), cpu.r(reg::C)), (0xa1, 1, 0));
    cpu.set_r(reg::A, 0xff);
    run_at(&mut mem, 0x1000, &mut cpu).unwrap();
    assert_eq!((cpu.r(reg::A), cpu.r(reg::C), cpu.r(reg::V)), (0x4f, 1, 0));
}

#[test]
fn page_cross_penalties() {
    // LDA $10F0,X ; LDA ($80),Y ; BEQ +$10 (from $10FC)
    let mut mem = image(0x1000, &[0xbd, 0xf0, 0x10, 0xb1, 0x80]);
    mem[0x80] = 0xf8;
    mem[0x81] = 0x20;
    mem[0x10fc] = 0xf0;
    mem[0x10fd] = 0x10;
    let mut cpu = Cpu::new();
    cpu.set_r(reg::X, 0x0f);
    run_at(&mut mem, 0x1000, &mut cpu).unwrap();
    assert_eq!(cpu.cycles, 4);
    cpu.set_r(reg::X, 0x10);
    run_at(&mut mem, 0x1000, &mut cpu).unwrap();
    assert_eq!(cpu.cycles, 4 + 5);
    cpu.set_r(reg::Y, 0x08);
    cpu.step(&mut *mem).unwrap();
    assert_eq!(cpu.cycles, 9 + 6);
    cpu.set_r(reg::Z, 0);
    run_at(&mut mem, 0x10fc, &mut cpu).unwrap();
    assert_eq!((cpu.pc, cpu.cycles), (0x10fe, 15 + 2));
    cpu.set_r(reg::Z, 1);
    run_at(&mut mem, 0x10fc, &mut cpu).unwrap();
    assert_eq!((cpu.pc, cpu.cycles), (0x110e, 17 + 4));
}

#[test]
fn subroutines_interrupts_and_indirect_jumps() {
    // $1000 JSR $2000 ; $1003 BRK ; $2000 PHP ; PLA ; RTS ; $3000 RTI ; $1100 JMP ($10FF)
    let mut mem = image(0x1000, &[0x20, 0x00, 0x20, 0x00]);
    mem[0x2000..0x2003].copy_from_slice(&[0x08, 0x68, 0x60]);
    mem[0x3000] = 0x40;
    mem[0xfffe] = 0x00;
    mem[0xffff] = 0x30;
    mem[0x1100..0x1103].copy_from_slice(&[0x6c, 0xff, 0x10]);
    mem[0x10ff] = 0x34;
    mem[0x1000] = 0x20;
    let mut cpu = Cpu::new();
    cpu.pc = 0x1000;
    cpu.set_r(reg::C, 1);
    cpu.step(&mut *mem).unwrap();
    assert_eq!(
        (cpu.pc, cpu.r(reg::SP), mem[0x1ff], mem[0x1fe]),
        (0x2000, 0xfd, 0x10, 0x02)
    );
    cpu.run(&mut *mem, 3);
    assert_eq!(
        (cpu.pc, cpu.r(reg::A), cpu.r(reg::SP)),
        (0x1003, 0x31, 0xff)
    );
    cpu.step(&mut *mem).unwrap();
    assert_eq!((cpu.pc, cpu.r(reg::I), mem[0x1fd]), (0x3000, 1, 0x31));
    assert_eq!(cpu.status(false), 0x25);
    cpu.set_r(reg::C, 0);
    cpu.step(&mut *mem).unwrap();
    assert_eq!((cpu.pc, cpu.r(reg::C), cpu.r(reg::I)), (0x1005, 1, 0));
    run_at(&mut mem, 0x1100, &mut cpu).unwrap();
    assert_eq!(cpu.pc, 0x0034 | (mem[0x1000] as u16) << 8);
}

#[test]
fn jam_stops_without_moving() {
    let mut mem = image(0x1000, &[0xea, 0x02]);
    let mut cpu = Cpu::new();
    cpu.pc = 0x1000;
    assert_eq!(cpu.run(&mut *mem, 10), (2, Some(Jam { pc: 0x1001 })));
    assert_eq!((cpu.pc, cpu.cycles), (0x1001, 2));
    assert_eq!(cpu.run(&mut *image(0, &[0xea; 4]), 3), (3, None));
}

#[test]
fn instantiated_insn_executes_like_step() {
    let mut mem = image(0x1000, &[0x7d, 0xf8, 0x10]);
    let mut arena = Vec::new();
    let insn = instantiate(&mem, 0x1000, &mut arena);
    let mut a = Cpu::new();
    a.set_r(reg::A, 0x7f);
    a.set_r(reg::X, 0x0c);
    a.pc = 0x1000;
    let mut b = a;
    let mut mem2 = mem.clone();
    a.step(&mut *mem).unwrap();
    b.exec(&mut *mem2, &insn, &arena).unwrap();
    assert_eq!(a, b);
}

struct Log(Vec<(u16, u8)>, [u8; 0x10000]);

impl Bus for Log {
    fn read(&mut self, addr: u16) -> u8 {
        self.1[addr as usize]
    }
    fn write(&mut self, addr: u16, val: u8) {
        self.0.push((addr, val));
        self.1[addr as usize] = val;
    }
}

#[test]
fn ram_varnodes_and_wide_accesses_go_through_the_bus() {
    let mut bus = Log(Vec::new(), [0; 0x10000]);
    bus.1[0x10] = 0x34;
    bus.1[0x11] = 0x12;
    let ops = [
        Op::new(Opc::IntAdd, u(0, 2), m(0x10, 2), c(1, 2)),
        Op::new(Opc::Copy, m(0xfffe, 2), u(0, 2), Vn::NONE),
        Op::new(Opc::Load, u(2, 2), c(0xffff, 2), Vn::NONE),
        Op::new(Opc::Store, Vn::NONE, c(0x20, 2), u(2, 2)),
        Op::new(Opc::IntCarry, u(4, 1), c(0xff, 1), c(1, 1)),
        Op::new(Opc::IntLess, u(5, 1), c(1, 1), c(2, 1)),
        Op::new(Opc::IntLeft, u(6, 1), c(1, 1), c(40, 1)),
        Op::new(Opc::IntRight, u(7, 1), c(0x80, 1), c(40, 1)),
    ];
    let mut cpu = Cpu::new();
    cpu.exec_ops(&mut bus, &ops, &Operands::new(0, 0));
    let uq: Vec<u32> = (0..8).map(|i| cpu.unique(i)).collect();
    assert_eq!(
        bus.0,
        [(0xfffe, 0x35), (0xffff, 0x12), (0x20, 0x12), (0x21, 0x00)]
    );
    assert_eq!(
        (uq[0], uq[2], uq[4], uq[5], uq[6], uq[7]),
        (0x1235, 0x0012, 1, 1, 0, 0)
    );
}

#[test]
fn penalty_kinds_match_addressing_modes() {
    for (op, t) in TEMPLATES.iter().enumerate() {
        match t.pen {
            PenKind::Iy => assert_eq!(op & 0x1f, 0x11 | (op & 0x02)),
            PenKind::Branch => assert!(matches!(t.ctrl, CtrlKind::Br { .. })),
            _ => assert_eq!(t.iy_base, 0),
        }
    }
}
