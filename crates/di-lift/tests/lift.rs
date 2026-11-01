mod common;
#[allow(dead_code)]
mod progs;
use common::*;
use di_lift::{lift, lift_many, lift_with, Entry, Facts, Job, Opts, Program};

const IO: (u16, u16) = (0xc000, 0xc100);

fn calls(n: usize, acc: impl Fn(usize) -> u8) -> Vec<(usize, u8)> {
    (0..n).map(|i| (0, acc(i))).collect()
}

#[test]
fn copy_loop() {
    let img = image(&[
        (
            0x1000,
            &[
                0xa2, 0x00, 0xbd, 0x00, 0x11, 0x9d, 0x00, 0xc0, 0xe8, 0xe0, 0x05, 0xd0, 0xf5, 0x60,
            ],
        ),
        (0x1100, &[1, 2, 3, 4, 5]),
    ]);
    let p = check(&img, &[0x1000], &calls(3, |_| 0), IO);
    assert!(p.stats.stack_private);
    assert!(p.to_string().contains("volatile"));
}

#[test]
fn operand_cells() {
    let p = check(
        &image(progs::PATCH),
        &[progs::PATCH_ENTRY],
        &calls(20, |_| 0),
        IO,
    );
    assert_eq!((p.stats.cells, p.stats.opcode_cells), (3, 0));
}

#[test]
fn opcode_cells() {
    let p = check(
        &image(progs::TOGGLE),
        &[progs::TOGGLE_ENTRY],
        &calls(6, |_| 0),
        IO,
    );
    assert_eq!(p.stats.opcode_cells, 2);
    assert!(p.elide_ret);
}

#[test]
fn stack_jump_table_and_calls() {
    let p = check(
        &image(progs::JUMPTAB),
        &[progs::JUMPTAB_ENTRY],
        &calls(8, |i| i as u8),
        IO,
    );
    assert_eq!(p.stats.calls, 1);
    check(
        &image(progs::PUSHPULL),
        &[progs::PUSHPULL_ENTRY],
        &calls(4, |i| i as u8),
        IO,
    );
    let p = check(
        &image(progs::TSXPROG),
        &[progs::TSXPROG_ENTRY],
        &calls(3, |_| 0),
        IO,
    );
    assert!(!p.stats.stack_private);
}

#[test]
fn indirect_and_interrupt_frames() {
    check(
        &image(progs::JMPIND),
        &[progs::JMPIND_ENTRY],
        &calls(8, |i| i as u8),
        IO,
    );
    check(
        &image(progs::BRKRTI),
        &[progs::BRKRTI_ENTRY],
        &calls(3, |_| 0),
        IO,
    );
}

#[test]
fn generated_code() {
    let e = [progs::GEN_GEN, progs::GEN_RUN];
    let c = [(0, 3), (1, 0), (0, 4), (1, 0), (0, 3), (1, 0)];
    check(&image(progs::GEN), &e, &c, IO);
}

#[test]
fn cycles_budget_and_stack_wrap() {
    let img = image(progs::TIMING);
    let f = facts(&img, &[(progs::TIMING_ENTRY, 0); 10], 1_000_000);
    let es = [Entry {
        pc: progs::TIMING_ENTRY,
        regs: vec![],
    }];
    let cyc = Opts {
        optimize: true,
        cycles: true,
    };
    let p = lift_with(&img, &es, &[(0xc000, 0xc100)], &f, cyc);
    assert!(diff_capped(&img, &p, &calls(10, |_| 0), IO, 150) > 0);
    let img = image(progs::WRAP);
    let es = [Entry {
        pc: progs::WRAP_ENTRY,
        regs: vec![],
    }];
    let f = facts(&img, &[(progs::WRAP_ENTRY, 0); 4], 1_000_000);
    for opts in [cyc, Opts::default()] {
        let p = lift_with(&img, &es, &[(0xc000, 0xc100)], &f, opts);
        diff(&img, &p, &calls(6, |_| 0), IO);
    }
}

#[test]
fn deopts_without_facts_and_guards() {
    let img = image(progs::TOGGLE);
    let es = [Entry {
        pc: progs::TOGGLE_ENTRY,
        regs: vec![(0, vec![0])],
    }];
    let p = lift(&img, &es, &[(0xc000, 0xc100)], &Facts::default());
    assert!(diff(&img, &p, &calls(4, |i| (i == 3) as u8), IO) > 0);
}

#[test]
fn batch() {
    let img = image(progs::JUMPTAB);
    let es = vec![Entry {
        pc: progs::JUMPTAB_ENTRY,
        regs: vec![],
    }];
    let f = facts(&img, &[(progs::JUMPTAB_ENTRY, 1)], 1_000_000);
    let jobs: Vec<Job> = (0..4)
        .map(|_| Job {
            img: img.clone(),
            entries: es.clone(),
            volatile: vec![(0xc000, 0xc100)],
            facts: f.clone(),
        })
        .collect();
    let n = std::sync::atomic::AtomicUsize::new(0);
    let ps = lift_many(&jobs, Opts::default(), || {
        n.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    });
    assert_eq!(n.into_inner(), 4);
    assert!(ps.iter().all(|p| p.stats == ps[0].stats));
}

#[test]
fn guarded_stack_and_forced_page1_access() {
    let img = image(progs::GUARDED);
    let pc = [progs::GUARDED_ENTRY];
    let p = check(&img, &pc, &calls(4, |_| 0x18), IO);
    let s = &p.stats;
    assert!(s.stack_private && s.page1_sites == 0);
    assert_eq!((s.stack_guards, s.slot_ops, s.stack_ops), (2, 2, 2));
    let f = facts(&img, &[(pc[0], 0x18); 4], 1_000_000);
    let forced = calls(6, |i| if i % 2 == 1 { 0x01 } else { 0x18 });
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
        };
        let p = lift_with(&img, &entries(&pc), &[(0xc000, 0xc100)], &f, opts);
        assert_eq!(diff(&img, &p, &forced, IO), 3);
    }
}

#[test]
fn recursion() {
    let p = check(
        &image(progs::RECURSE),
        &[progs::RECURSE_ENTRY],
        &calls(3, |_| 0),
        IO,
    );
    assert!(p.stats.stack_private && p.functions[1].recursive && !p.functions[0].recursive);
    assert_eq!(p.functions[1].nslots, 0);
}

fn acc_calls(accs: &[u8]) -> Vec<(usize, u8)> {
    accs.iter().map(|&a| (0, a)).collect()
}

/// Lift with facts from `seen` (exact without deopts, stack private, every height known),
/// then with facts from `fseen` run `forced` and `seen` exactly with deopts in both cycle
/// modes; the optimized functional lift from `seen`.
fn private_heights(
    prog: &[(u16, &[u8])],
    pc: u16,
    seen: &[u8],
    fseen: &[u8],
    forced: &[u8],
) -> Program {
    let img = image(prog);
    let p = check(&img, &[pc], &acc_calls(seen), IO);
    assert!(p.stats.stack_private && p.stats.unknown_heights == 0);
    let fc: Vec<(u16, u8)> = fseen.iter().map(|&a| (pc, a)).collect();
    let f = facts(&img, &fc, 1_000_000);
    let runs: Vec<(usize, u8)> = acc_calls(forced)
        .into_iter()
        .chain(acc_calls(seen))
        .collect();
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
        };
        let q = lift_with(&img, &entries(&[pc]), &[(0xc000, 0xc100)], &f, opts);
        assert!(q.stats.stack_private);
        assert!(diff(&img, &q, &runs, IO) > 0);
    }
    p
}

#[test]
fn correlated_pushes_split_heights() {
    let all: Vec<u8> = (0..16).collect();
    let p = private_heights(progs::CORRELATED, progs::CORRELATED_ENTRY, &all, &[0], &[5]);
    let s = &p.stats;
    assert!(s.split_nodes > 0 && s.calls > 0 && s.slot_ops > 0);
    assert_eq!((s.stack_ops, p.functions.len()), (2, 2));
}

#[test]
fn brk_chain_on_untaken_path() {
    let p = private_heights(
        progs::BRKZERO,
        progs::BRKZERO_ENTRY,
        &[0, 1, 2],
        &[0, 1, 2],
        &[0x40, 0x80],
    );
    assert_eq!(p.stats.calls, 1);
}

#[test]
fn sp_restore_dispatch() {
    let p = private_heights(
        progs::SPRESTORE,
        progs::SPRESTORE_ENTRY,
        &[0, 1, 2],
        &[0, 1, 2],
        &[0x80],
    );
    assert_eq!(p.stats.sp_dispatch, 1);
}

#[test]
fn dropped_return_address() {
    private_heights(
        progs::DROPRET,
        progs::DROPRET_ENTRY,
        &[0, 1, 2],
        &[0, 1, 2],
        &[0x80],
    );
}

#[test]
fn pushed_address_jump_table() {
    private_heights(
        progs::PUSHRTS,
        progs::PUSHRTS_ENTRY,
        &[0, 1, 2, 3],
        &[0, 1, 2, 3],
        &[0x81],
    );
}

#[test]
fn sp_set_below_wrap() {
    let p = private_heights(
        progs::SPTABLE,
        progs::SPTABLE_ENTRY,
        &[0, 1, 2, 3],
        &[0],
        &[1],
    );
    assert_eq!(p.stats.sp_dispatch, 3);
}

#[test]
fn soft_deopt_resumes_with_folded_register() {
    let img = image(progs::CODEHIT);
    let pc = progs::CODEHIT_ENTRY;
    let f = facts(&img, &[(pc, 0)], 1_000_000);
    for cycles in [false, true] {
        let opts = Opts {
            optimize: true,
            cycles,
        };
        let p = lift_with(&img, &entries(&[pc]), &[(0xc000, 0xc100)], &f, opts);
        assert!(p.stats.code_guards > 0);
        assert!(diff(&img, &p, &acc_calls(&[0, 1, 0]), IO) > 0);
    }
}

/// A store that makes its own cell decode a jump back to it: discovery still terminates.
#[test]
fn cell_fixpoint_terminates() {
    let mut img = Box::new([0u8; 0x10000]);
    img[0x1000..0x100a]
        .copy_from_slice(&[0xa9, 0x4c, 0x8d, 0x05, 0x10, 0xa9, 0x02, 0x10, 0x00, 0x60]);
    let e = [Entry {
        pc: 0x1000,
        regs: vec![],
    }];
    let p = lift(&img, &e, &[], &Facts::default());
    assert_eq!(p.stats.opcode_cells, 1);
}

/// Lift with facts from `seen` and run `seen` from SP `sp` (an entry SP the facts never
/// saw) exactly with deopts in both cycle modes.
fn forced_entry_sp(prog: &[(u16, &[u8])], pc: u16, seen: &[u8], sp: u8) {
    let img = image(prog);
    let fc: Vec<(u16, u8)> = seen.iter().map(|&a| (pc, a)).collect();
    let f = facts(&img, &fc, 1_000_000);
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
        };
        let q = lift_with(&img, &entries(&[pc]), &[(0xc000, 0xc100)], &f, opts);
        assert!(diff_at(&img, &q, &acc_calls(seen), IO, 1_000_000, sp) > 0);
    }
}

#[test]
fn unexecuted_uncheckable_sites_deopt() {
    let p = private_heights(
        progs::UNEXEC,
        progs::UNEXEC_ENTRY,
        &[0, 1, 0x40],
        &[0, 1, 0x40],
        &[0x80],
    );
    assert!(p.stats.slot_ops > 0 && p.stats.page1_sites == 0);
}

#[test]
fn frame_pointer_locals() {
    let seen: Vec<u8> = (0..8).collect();
    let p = check(
        &image(progs::FRAME),
        &[progs::FRAME_ENTRY],
        &acc_calls(&seen),
        IO,
    );
    let s = &p.stats;
    assert!(s.stack_private && s.page1_sites == 0 && s.slot_ops > 0);
    assert_eq!(s.sp_guards, 1);
    forced_entry_sp(progs::FRAME, progs::FRAME_ENTRY, &seen, 0xf0);
}

#[test]
fn page1_resume_table() {
    let seen = vec![0; 12];
    let p = check(
        &image(progs::PTABLE),
        &[progs::PTABLE_ENTRY],
        &acc_calls(&seen),
        IO,
    );
    let s = &p.stats;
    assert!(s.stack_private && s.page1_sites == 0 && s.slot_ops > 0);
    assert_eq!(s.sp_guards, 1);
    forced_entry_sp(progs::PTABLE, progs::PTABLE_ENTRY, &seen, 0xf0);
}

#[test]
fn indexed_page1_table_range_guard() {
    let seen: Vec<u8> = (0..12).map(|i| i & 1).collect();
    let p = private_heights(progs::PXTABLE, progs::PXTABLE_ENTRY, &seen, &seen, &[2]);
    let s = &p.stats;
    assert!(s.page1_sites == 0 && s.slot_ops > 0 && s.stack_guards > 0);
    assert_eq!((s.kept_bytes, s.sp_guards), (2, 1));
}

/// Lift `prog` with facts from `n` calls capped at `max` cycles and run them with that cap in
/// both cycle modes, each ending with `halt` like the reference; the total deopts.
fn capped(prog: &[(u16, &[u8])], pc: u16, n: usize, max: u64, halt: di_pcode::Halt) -> u64 {
    let img = image(prog);
    let f = facts(&img, &vec![(pc, 0); n], max);
    let mut deopts = 0;
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
        };
        let p = lift_with(&img, &entries(&[pc]), &[(0xc000, 0xc100)], &f, opts);
        assert_eq!(p.stats.budget_checks > 0, !cycles);
        let start = std::time::Instant::now();
        deopts += diff_at(&img, &p, &calls(n, |_| 0), IO, max, 0xff);
        assert!(start.elapsed() < std::time::Duration::from_secs(10));
        let mut cpu = di_pcode::Cpu::new();
        let o = di_lift::Runner::new(&di_lift::Exec::new(&p)).call(
            0,
            0,
            &mut cpu,
            &mut TBus::new(&img, IO),
            max,
        );
        assert_eq!(o.halt, halt);
    }
    deopts
}

#[test]
fn infinite_loops_end_with_the_budget() {
    let cycles = di_pcode::Halt::Cycles;
    capped(progs::SPIN, progs::SPIN_ENTRY, 3, 100_000, cycles);
    capped(progs::INDSPIN, progs::INDSPIN_ENTRY, 3, 100_000, cycles);
    capped(progs::POPREC, progs::POPREC_ENTRY, 3, 100_000, cycles);
}

#[test]
fn finite_loop_under_a_budget() {
    let (pc, sp) = (progs::COUNT_ENTRY, di_pcode::Halt::Sp);
    assert_eq!(capped(progs::COUNT, pc, 3, 1_000_000, sp), 0);
    capped(progs::COUNT, pc, 3, 1_000, di_pcode::Halt::Cycles);
    let img = image(progs::COUNT);
    let mut cpu = di_pcode::Cpu::new();
    ref_call(&mut cpu, &mut TBus::new(&img, IO), pc, 0, 1_000_000);
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
        };
        let p = lift_with(
            &img,
            &entries(&[pc]),
            &[(0xc000, 0xc100)],
            &Facts::default(),
            opts,
        );
        assert!(diff_at(&img, &p, &calls(1, |_| 0), IO, cpu.cycles, 0xff) > 0);
    }
}
