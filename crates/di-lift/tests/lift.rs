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
        ..Opts::default()
    };
    let p = lift_with(&img, &es, &[(0xc000, 0xc100)], &f, cyc.clone());
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
    assert_eq!((s.stack_guards, s.slot_ops, s.stack_ops), (2, 2, 5));
    let f = facts(&img, &[(pc[0], 0x18); 4], 1_000_000);
    let forced = calls(6, |i| if i % 2 == 1 { 0x01 } else { 0x18 });
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
            ..Opts::default()
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
            ..Opts::default()
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
    // `JSR sub`, the shared `BRK` tail entered from `entry` and from `sub`, and a call per
    // `BRK` of the handler at `$0000` (a `BRK` too) they all reach.
    assert_eq!(p.stats.calls, 6);
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
            ..Opts::default()
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
            ..Opts::default()
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
            ..Opts::default()
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
            ..Opts::default()
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

const SID: (u16, u16) = (0xd400, 0xd800);

/// Check a banked program over the C64-like map with facts from `seen` (exact, no deopts,
/// both cycle modes), then lifted from those facts run `forced` then `seen` exactly with
/// deopts when `forced` is not empty; the optimized functional lift.
fn banked(prog: &[(u16, &[u8])], pc: u16, seen: &[u8], forced: &[u8]) -> Program {
    let (img, m) = (c64_image(prog), c64_like());
    let p = check_in(&img, &[pc], &acc_calls(seen), SID, Some(&m));
    assert!(p.bank.is_some());
    let fc: Vec<(u16, u8)> = seen.iter().map(|&a| (pc, a)).collect();
    let f = facts_in(&img, &fc, 1_000_000, Some(&m));
    let runs: Vec<(usize, u8)> = acc_calls(forced)
        .into_iter()
        .chain(acc_calls(seen))
        .collect();
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
            ..Opts::default()
        };
        let q = di_lift::lift_banked(
            &img,
            &entries(&[pc]),
            &[(0xd400, 0xd800)],
            &f,
            opts,
            Some(&m),
        );
        assert_eq!(diff(&img, &q, &runs, SID) > 0, !forced.is_empty());
    }
    p
}

#[test]
fn bank_switch_reads_ram_under_rom_and_io() {
    let p = banked(progs::BANKRD, progs::BANKRD_ENTRY, &[0, 0], &[]);
    assert!(p.stats.bank_dispatch > 0);
    let text = p.to_string();
    assert!(
        text.contains("volatile") && !text.contains("bank"),
        "{text}"
    );
}

#[test]
fn code_in_ram_under_rom() {
    banked(progs::ROMRAM, progs::ROMRAM_ENTRY, &[0, 1, 2], &[]);
}

#[test]
fn rom_code_called_from_ram() {
    let p = banked(progs::ROMCALL, progs::ROMCALL_ENTRY, &[0, 0], &[]);
    assert!(p.stats.calls > 0 || p.stats.insns > 10);
}

#[test]
fn unseen_configuration_deopts() {
    banked(progs::BANKSEEN, progs::BANKSEEN_ENTRY, &[0, 0], &[1]);
}

const SUB: (u16, &[u8]) = (0x1120, &[0xa9, 0x07, 0x8d, 0x00, 0xc0, 0x60]);

/// Stores indexed by a register a loop bounds never reach the constant code that lies within
/// 256 bytes of their table.
#[test]
fn bounded_index_stores_need_no_code_guard() {
    let up: &[u8] = &[
        0xa2, 0x00, 0x8a, 0x9d, 0xe0, 0x10, 0xe8, 0xe0, 0x20, 0xd0, 0xf7, 0x20, 0x20, 0x11, 0x60,
    ];
    let down: &[u8] = &[
        0xa2, 0x07, 0x9d, 0xe0, 0x10, 0xca, 0x10, 0xfa, 0x20, 0x20, 0x11, 0x60,
    ];
    for code in [up, down] {
        let p = check(
            &image(&[(0x1000, code), SUB]),
            &[0x1000],
            &calls(3, |_| 0),
            IO,
        );
        assert_eq!(p.stats.code_guards, 0);
    }
}

/// A loop stepping `Y` down by 7 around a call of a routine another site also calls: the
/// call leaves `Y` alone, so `Y` is the loop's induction variable and its indexed store stays
/// off the routine's code within 256 bytes of its table.
#[test]
fn loops_around_shared_calls_bound_their_index() {
    let code: &[u8] = &[
        0xa0, 0x0e, 0x20, 0x20, 0x11, 0xa9, 0x00, 0x99, 0xe0, 0x10, 0x98, 0x38, 0xe9, 0x07, 0xa8,
        0x10, 0xf1, 0x20, 0x20, 0x11, 0x60,
    ];
    let p = check(
        &image(&[(0x1000, code), SUB]),
        &[0x1000],
        &calls(3, |_| 0),
        IO,
    );
    assert_eq!(p.stats.code_guards, 0);
}

/// A loop stepping `X` down by 7 around a call of a routine only it calls: in the routine `X`
/// holds the loop's values, so the routine's indexed store stays off the code within 256
/// bytes of its table.
#[test]
fn a_loops_index_bounds_the_routines_it_calls() {
    let code: &[u8] = &[
        0xa2, 0x0e, 0x20, 0x00, 0x11, 0x8a, 0x38, 0xe9, 0x07, 0xaa, 0x10, 0xf6, 0x60,
    ];
    let voice: &[u8] = &[0xa9, 0x00, 0x9d, 0xe0, 0x10, 0x60];
    let p = check(
        &image(&[(0x1000, code), (0x1100, voice), SUB]),
        &[0x1000],
        &calls(3, |_| 0),
        IO,
    );
    assert_eq!(p.stats.code_guards, 0);
}

/// A routine called with `X` = 0, 1, 2 that leaves `X` as it found it (its own callee too)
/// is analysed per call site.
#[test]
fn registers_survive_calls_per_call_site() {
    let entry: &[u8] = &[
        0xa2, 0x00, 0x20, 0x20, 0x10, 0xe8, 0x20, 0x20, 0x10, 0xe8, 0x20, 0x20, 0x10, 0x60,
    ];
    let voice: &[u8] = &[
        0xa9, 0x05, 0x9d, 0xe0, 0x10, 0x20, 0x40, 0x11, 0x9d, 0xe8, 0x10, 0x60,
    ];
    let img = image(&[
        (0x1000, entry),
        (0x1020, voice),
        (0x1140, &[0xa9, 0x09, 0x60]),
    ]);
    let p = check(&img, &[0x1000], &calls(3, |_| 0), IO);
    assert_eq!((p.stats.code_guards, p.stats.calls), (0, 4));
}

/// A routine whose branch on a known carry always skips a `BRK` (data after a `BCS` that
/// follows a compare) still returns to each call site with the caller's `X`; the `BRK` is not
/// planned.
#[test]
fn untaken_edges_leave_calls_balanced() {
    let entry: &[u8] = &[
        0xa2, 0x00, 0x20, 0x20, 0x10, 0xe8, 0x20, 0x20, 0x10, 0xe8, 0x20, 0x20, 0x10, 0x60,
    ];
    let voice: &[u8] = &[
        0xa9, 0x05, 0x9d, 0xe0, 0x10, 0xc9, 0x03, 0xb0, 0x02, 0x00, 0x00, 0x60,
    ];
    let img = image(&[
        (0x1000, entry),
        (0x1020, voice),
        (0x1140, &[0xa9, 0x09, 0x60]),
    ]);
    let opts = Opts {
        entry_sp: Some(0xfd),
        loaded: Some((0x1000, 0x1143)),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    assert!(!p.starts.contains(&0x1029));
    let x = p.regs.iter().find(|r| r.0 == 0x1022).map(|r| r.1[1]);
    assert_eq!(x, Some([7, 0, 0, 0]));
    assert_eq!(p.stats.code_guards, 0);
    assert_eq!(diff(&img, &p, &calls(3, |_| 0), IO), 0);
}

/// An index the program does not bound keeps its guard, and a store through it that reaches
/// constant code deopts exactly.
#[test]
fn unbounded_index_store_keeps_its_guard() {
    let entry: &[u8] = &[0xaa, 0xa9, 0xea, 0x9d, 0xe0, 0x10, 0x20, 0x20, 0x11, 0x60];
    let img = image(&[(0x1000, entry), SUB]);
    let f = facts(&img, &[(0x1000, 0)], 1_000_000);
    for cycles in [false, true] {
        let opts = Opts {
            optimize: true,
            cycles,
            ..Opts::default()
        };
        let p = lift_with(&img, &entries(&[0x1000]), &[(0xc000, 0xc100)], &f, opts);
        assert_eq!(p.stats.code_guards, 1);
        assert!(diff(&img, &p, &acc_calls(&[0, 0x41, 0]), IO) > 0);
    }
}

/// Jump tables (interleaved through `JMP (ind)`, split behind pushed return addresses) indexed
/// by a bounded register resolve from the code: no facts, every target, no deopt.
#[test]
fn computed_jumps_resolve_without_facts() {
    type Prog = &'static [(u16, &'static [u8])];
    let cases: [(Prog, u16, usize); 3] = [
        (progs::JMPIND, progs::JMPIND_ENTRY, 4),
        (progs::JUMPTAB, progs::JUMPTAB_ENTRY, 4),
        (progs::PUSHRTS, progs::PUSHRTS_ENTRY, 2),
    ];
    for (prog, pc, n) in cases {
        let img = image(prog);
        for cycles in [false, true] {
            let opts = Opts {
                optimize: true,
                cycles,
                ..Opts::default()
            };
            let p = lift_with(
                &img,
                &entries(&[pc]),
                &[(0xc000, 0xc100)],
                &Facts::default(),
                opts,
            );
            assert_eq!(p.jumps.len(), 1);
            assert_eq!(p.jumps[0].1.len(), n);
            let runs = acc_calls(&[0, 1, 2, 3, 3, 2, 1, 0]);
            assert_eq!(diff(&img, &p, &runs, IO), 0);
        }
    }
}

/// A table a store may write leaves the jump unresolved: it deopts.
#[test]
fn written_tables_stay_unresolved() {
    let mut written = progs::JMPIND[0].1.to_vec();
    written[..2].copy_from_slice(&[0x8d, 0x23]);
    written.insert(2, 0x10);
    let img = image(&[(0x1000, &written)]);
    let p = lift(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
    );
    assert!(p.jumps.is_empty());
    assert!(diff(&img, &p, &acc_calls(&[0]), IO) > 0);
}

/// An index the code does not bound reads the table inside its extent, which ends with the
/// loaded program: the jump goes to those words, and an index past them deopts at the load.
#[test]
fn unbounded_index_reads_inside_the_extent() {
    let mut unbounded = progs::JMPIND[0].1.to_vec();
    unbounded[..2].copy_from_slice(&[0xea, 0xea]);
    let img = image(&[(0x1000, &unbounded)]);
    let opts = Opts {
        loaded: Some((0x1000, 0x1000 + unbounded.len() as u32)),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    assert_eq!(p.jumps, [(0x1010, vec![0x1013, 0x1017, 0x101b, 0x1022])]);
    assert_eq!(diff(&img, &p, &acc_calls(&[0, 1, 2, 3, 2, 0]), IO), 0);
    assert!(diff(&img, &p, &acc_calls(&[4]), IO) > 0);
}

/// A jump through a cell operand whose low byte an unbounded index reads from a table that
/// code only the table reaches follows: the table ends at the first target it gives past its
/// first byte, so the code's bytes give no targets.
#[test]
fn jump_table_ends_at_its_target() {
    let prog: &[u8] = &[
        0xaa, 0xbd, 0x0a, 0x10, 0x8d, 0x08, 0x10, 0x4c, 0x11, 0x10, 0x0d, 0x11, 0x15, 0x8d, 0x50,
        0xc0, 0x60, 0x8e, 0x51, 0xc0, 0x60, 0x8c, 0x52, 0xc0, 0x60,
    ];
    let img = image(&[(0x1000, prog)]);
    let opts = Opts {
        loaded: Some((0x1000, 0x1000 + prog.len() as u32)),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    assert_eq!(p.jumps, [(0x1007, vec![0x100d, 0x1011, 0x1015])]);
    assert_eq!(diff(&img, &p, &acc_calls(&[0, 1, 2, 1, 0]), IO), 0);
    assert!(diff(&img, &p, &acc_calls(&[3]), IO) > 0);
}

/// A store into a table that ends the loaded program through an unbounded index stays inside
/// it: an index past it deopts at the store. One whose index range stays inside the program
/// has no guard.
#[test]
fn unbounded_stores_stay_inside_the_program() {
    let prog: &[u8] = &[
        0xa8, 0xb9, 0x0b, 0x10, 0x8d, 0x00, 0xc0, 0x99, 0x0b, 0x10, 0x60, 0x11, 0x22, 0x33, 0x44,
    ];
    let mut inside = prog.to_vec();
    inside.extend([0; 0x100]);
    for (bytes, past) in [(prog, true), (&inside[..], false)] {
        let img = image(&[(0x1000, bytes)]);
        let opts = Opts {
            loaded: Some((0x1000, 0x1000 + bytes.len() as u32)),
            ..Opts::default()
        };
        let p = lift_with(
            &img,
            &entries(&[0x1000]),
            &[(0xc000, 0xc100)],
            &Facts::default(),
            opts,
        );
        assert_eq!(diff(&img, &p, &acc_calls(&[0, 3, 1, 2, 3, 0]), IO), 0);
        assert_eq!(diff(&img, &p, &acc_calls(&[4, 5]), IO) > 0, past);
    }
}

/// A register stored to a cell, overwritten, then read past a guarded store into a table that
/// ends the loaded program: the cell's reload is forwarded from the register, whose
/// overwriting copy only the guard's deopt reads, so no load of the cell follows the store.
#[test]
fn reload_forwarded_past_a_guarded_store() {
    let (cell, tab) = (0x1018u16, 0x1019u16);
    let [cl, ch] = cell.to_le_bytes();
    let [tl, th] = tab.to_le_bytes();
    let prog: &[u8] = &[
        0xa8, 0xae, cl, ch, 0xe8, 0x8e, cl, ch, 0xae, 0x00, 0xc0, 0x99, tl, th, 0x9d, 0x01, 0xc0,
        0xae, cl, ch, 0x8e, 0x02, 0xc0, 0x60, 0x07, 0x11, 0x22, 0x33, 0x44,
    ];
    let img = image(&[(0x1000, prog)]);
    let opts = Opts {
        loaded: Some((0x1000, 0x1000 + prog.len() as u32)),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    let loads = (p.functions.iter().flat_map(|f| &f.blocks))
        .flat_map(|b| &b.ops)
        .filter(|o| o.opc == di_pcode::Opc::Load && o.ins[0] == di_lift::ir::k(cell as u32, 2))
        .count();
    assert_eq!(loads, 1);
    assert_eq!(diff(&img, &p, &acc_calls(&[0, 3, 1, 2, 3, 0]), IO), 0);
    assert!(diff(&img, &p, &acc_calls(&[4]), IO) > 0);
}

/// A table byte a store from a constant base plus an unbounded index may write is data, not
/// constant code the store is checked against.
#[test]
fn indexed_store_keeps_tables_data() {
    let put: &[u8] = &[0xaa, 0xa9, 0x07, 0x9d, 0x00, 0x11, 0x60];
    let get: &[u8] = &[0xad, 0x80, 0x11, 0x8d, 0x53, 0xc0, 0x60];
    let img = image(&[(0x1000, put), (0x1010, get), (0x1180, &[0x42])]);
    let opts = Opts {
        loaded: Some((0x1000, 0x1200)),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1000, 0x1010]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    assert!(p.code.iter().all(|e| e.0 < 0x1100));
    let calls = [(1, 0), (0, 0x80), (1, 0), (0, 0x10), (1, 0)];
    assert_eq!(diff(&img, &p, &calls, IO), 0);
}

/// A counted loop clearing operands with `STA abs,X` makes them cells (no code check), and a
/// store of known opcodes makes a closed opcode cell (no dispatch deopt); the cleared operand
/// cells are closed with their values.
#[test]
fn indexed_stores_make_cells() {
    let clear: &[u8] = &[
        0xa2, 0x0a, 0xa9, 0x00, 0x9d, 0x04, 0x11, 0x8a, 0x38, 0xe9, 0x05, 0xaa, 0x10, 0xf4, 0x60,
    ];
    let ops: &[u8] = &[
        0xee, 0x04, 0x11, 0xa9, 0x11, 0x8d, 0x60, 0xc0, 0xa9, 0x22, 0x8d, 0x61, 0xc0, 0xa9, 0x33,
        0x8d, 0x62, 0xc0, 0x60,
    ];
    let flip: &[u8] = &[
        0x4a, 0xa9, 0x69, 0x90, 0x02, 0xa9, 0xe9, 0xa2, 0x05, 0x9d, 0x23, 0x12, 0xca, 0xca, 0xca,
        0xca, 0xca, 0x10, 0xf6, 0x60,
    ];
    let run: &[u8] = &[
        0xa9, 0x10, 0x38, 0x69, 0x03, 0x8d, 0x60, 0xc0, 0x69, 0x04, 0x8d, 0x61, 0xc0, 0x60,
    ];
    let img = image(&[
        (0x1000, clear),
        (0x1100, ops),
        (0x1200, flip),
        (0x1220, run),
    ]);
    let calls = [(1, 0), (0, 0), (1, 0), (2, 1), (3, 0), (2, 0), (3, 0)];
    let p = lift(
        &img,
        &entries(&[0x1000, 0x1100, 0x1200, 0x1220]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
    );
    assert_eq!(p.stats.code_guards, 0);
    assert_eq!((p.stats.cells, p.stats.opcode_cells), (5, 2));
    let sets = vec![
        (0x1109, vec![0x00, 0x22]),
        (0x110e, vec![0x00, 0x33]),
        (0x1223, vec![0x69, 0xe9]),
        (0x1228, vec![0x69, 0xe9]),
    ];
    assert_eq!(p.sets, sets);
    assert_eq!(diff(&img, &p, &calls, IO), 0);
}

/// Three copies of a voice routine at a stride, each over its own zero-page bytes and I/O
/// register (`$C000 + 7k`), chained by `JMP`s: one instance function of the copy's index,
/// replaying exactly without facts.
#[test]
fn unrolled_copies_fold() {
    let copy = |k: u8, tail: &[u8]| -> Vec<u8> {
        let mut c = vec![
            0xc6,
            0x10 + k,
            0x10,
            0x04,
            0xa9,
            0x02,
            0x85,
            0x10 + k,
            0xa5,
            0x20 + k,
            0x8d,
            7 * k,
            0xc0,
        ];
        c.extend_from_slice(tail);
        c
    };
    let (c0, c1, c2) = (
        copy(0, &[0x4c, 0x20, 0x10]),
        copy(1, &[0x4c, 0x30, 0x10]),
        copy(2, &[0x60]),
    );
    let img = image(&[
        (
            0x1000,
            &[0x85, 0x20, 0x85, 0x21, 0x85, 0x22, 0x4c, 0x10, 0x10],
        ),
        (0x1010, &c0),
        (0x1020, &c1),
        (0x1030, &c2),
    ]);
    let v = [(IO.0 as u32, IO.1 as u32)];
    for optimize in [false, true] {
        let opts = Opts {
            optimize,
            fold: true,
            ..Opts::default()
        };
        let p = lift_with(&img, &entries(&[0x1000]), &v, &Facts::default(), opts);
        assert_eq!((p.stats.instances, p.stats.instance_copies), (1, 3));
        assert_eq!(diff(&img, &p, &calls(12, |i| i as u8), IO), 0);
    }
}

/// Ops of `p` reading register `r` (as printed).
fn reads(p: &Program, r: &str) -> usize {
    p.to_string()
        .lines()
        .filter(|l| {
            let rhs = l.split_once('=').map_or(l.trim(), |(_, x)| x);
            rhs.split([' ', ',']).skip(2).any(|t| t == r)
        })
        .count()
}

#[test]
fn status_saved_through_the_stack_in_lanes() {
    // STA $1008; CMP #$E9; PHP; LDX #0; (opcode cell) NOP/INX; STX $C001; PLP; LDA #0;
    // ROL A; STA $C002; RTS
    let img = image(&[(
        0x1000,
        &[
            0x8d, 0x08, 0x10, 0xc9, 0xe9, 0x08, 0xa2, 0x00, 0xea, 0x8e, 0x01, 0xc0, 0x28, 0xa9,
            0x00, 0x2a, 0x8d, 0x02, 0xc0, 0x60,
        ],
    )]);
    let seen = calls(4, |i| [0xea, 0xe8][i % 2]);
    let p = check(&img, &[0x1000], &seen, IO);
    assert_eq!(reads(&p, "s0"), 0, "{p}");
    // An opcode the facts never saw deopts with the status pushed: its byte is in memory.
    let f = facts(&img, &[(0x1000, 0xea), (0x1000, 0xe8)], 1_000_000);
    let p = lift(&img, &entries(&[0x1000]), &[(0xc000, 0xc100)], &f);
    assert!(diff(&img, &p, &calls(3, |i| [0xea, 0xf8, 0xe8][i]), IO) > 0);
}

/// A store indexed over its own operand bytes, of a value read from I/O: resolved with its
/// operands' entry bytes it makes them cells of unknown values, which leave it unresolved.
/// The values only rise, so the lift ends: the store's address is unknown and a run that
/// rewrites its operand deopts.
#[test]
fn store_over_its_own_operand_ends() {
    let prog: &[u8] = &[
        0xad, 0x00, 0xc0, 0x29, 0x0f, 0xaa, 0xad, 0x01, 0xc0, 0x9d, 0x09, 0x10, 0x60,
    ];
    let img = image(&[(0x1000, prog)]);
    let opts = Opts {
        loaded: Some((0x1000, 0x1000 + prog.len() as u32)),
        entry_sp: Some(0xfd),
        ..Opts::default()
    };
    let (tx, rx) = std::sync::mpsc::channel();
    let job = img.clone();
    std::thread::spawn(move || {
        let p = lift_with(
            &job,
            &entries(&[0x1000]),
            &[(0xc000, 0xc100)],
            &Facts::default(),
            opts,
        );
        tx.send(p).unwrap();
    });
    let p = rx
        .recv_timeout(std::time::Duration::from_secs(60))
        .expect("lift ends");
    assert!(diff(&img, &p, &acc_calls(&[0, 0, 0, 0]), IO) > 0);
}

#[test]
fn stored_bytes_cover_every_store() {
    // TAX; STA $1100,X; LDY #0; LDA #0; STA $FD; LDA #$20; STA $FE; TXA; STA ($FD),Y; RTS
    let known = image(&[(
        0x1000,
        &[
            0xaa, 0x9d, 0x00, 0x11, 0xa0, 0x00, 0xa9, 0x00, 0x85, 0xfd, 0xa9, 0x20, 0x85, 0xfe,
            0x8a, 0x91, 0xfd, 0x60,
        ],
    )]);
    let p = lift(
        &known,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
    );
    assert_eq!(p.stored, [(0xfd, 0xff), (0x1100, 0x1200), (0x2000, 0x2001)]);
    // STA $FD; STA $FE; LDY #0; STA ($FD),Y; RTS: a pointer of unknown value reaches any byte.
    let unknown = image(&[(
        0x1000,
        &[0x85, 0xfd, 0x85, 0xfe, 0xa0, 0x00, 0x91, 0xfd, 0x60],
    )]);
    let p = lift(
        &unknown,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
    );
    assert_eq!(p.stored, [(0, 0x10000)]);
}

/// A data operand of bytes the host's init holds is read from memory, as a cell's, with the
/// analysis taking its value; a branch's operand stays its target.
#[test]
fn held_operands_are_loaded() {
    // TAY; LDA $1100,Y; STA $C000; BNE +3; STA $C001; RTS
    let img = image(&[
        (
            0x1000,
            &[
                0xa8, 0xb9, 0x00, 0x11, 0x8d, 0x00, 0xc0, 0xd0, 0x03, 0x8d, 0x01, 0xc0, 0x60,
            ],
        ),
        (0x1100, &[1, 2, 3, 4]),
    ]);
    let run = |held: Vec<u16>| {
        let opts = Opts {
            entry_sp: Some(0xfd),
            fold: true,
            held,
            ..Opts::default()
        };
        lift_with(
            &img,
            &entries(&[0x1000]),
            &[(0xc000, 0xc100)],
            &Facts::default(),
            opts,
        )
    };
    let (plain, held) = (run(vec![]), run(vec![0x1002, 0x1003, 0x1008]));
    let (bare, text) = (plain.to_string(), held.to_string());
    assert!(!bare.contains("LOAD #1002") && text.contains("LOAD #1002"));
    assert!(!text.contains("#1008") && text.contains("#c001"));
    assert_eq!(held.stored, plain.stored);
    assert_eq!(diff(&img, &held, &acc_calls(&[0, 3]), IO), 0);
}

/// The keys of every computed jump's table.
fn table_keys(p: &Program) -> Vec<Vec<u16>> {
    (p.functions.iter())
        .flat_map(|f| f.tables.iter().map(|t| t.iter().map(|e| e.0).collect()))
        .collect()
}

/// `AND #3; TAX; LDA tlo,X; STA ptr; LDA thi,X; STA ptr+1; LDX #0; JMP (ptr)`: the index is
/// carried past `LDX`, and the jump's table is keyed by its values.
const CARRIED: &[u8] = &[
    0x29, 0x03, 0xaa, 0xbd, 0x26, 0x10, 0x8d, 0x2e, 0x10, 0xbd, 0x2a, 0x10, 0x8d, 0x2f, 0x10, 0xa2,
    0x00, 0x6c, 0x2e, 0x10, 0x8e, 0x50, 0xc0, 0x60, 0xa9, 0x11, 0x8d, 0x51, 0xc0, 0x60, 0xad, 0x52,
    0xc0, 0x8d, 0x53, 0xc0, 0x60, 0x60, 0x14, 0x18, 0x1e, 0x25, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
];

/// Two jumps through the same table, the first (in a subroutine) indexed by `X` carried past
/// the caller's `LDX #0`, the second by `Y` carried past `LDY #0`.
const TWO_CARRIED: &[u8] = &[
    0x85, 0xfb, 0x29, 0x03, 0xaa, 0xbd, 0x3f, 0x10, 0x8d, 0x47, 0x10, 0xbd, 0x43, 0x10, 0x8d, 0x48,
    0x10, 0xa2, 0x00, 0x20, 0x2d, 0x10, 0xa5, 0xfb, 0x4a, 0x29, 0x03, 0xa8, 0xb9, 0x3f, 0x10, 0x8d,
    0x47, 0x10, 0xb9, 0x43, 0x10, 0x8d, 0x48, 0x10, 0xa0, 0x00, 0x6c, 0x47, 0x10, 0x6c, 0x47, 0x10,
    0x8e, 0x50, 0xc0, 0x60, 0x8c, 0x51, 0xc0, 0x60, 0xa9, 0x52, 0x8d, 0x53, 0xc0, 0x60, 0x60, 0x30,
    0x34, 0x38, 0x3e, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
];

/// `STA save`, then per stage `i` and register `r` (`X`, then `Y`): `LDA save`, `2i` times
/// `LSR A`; `AND #3; TAr; LDA tlo,r; STA ptr; LDA thi,r; STA ptr+1; LDr #i; JMP (ptr)` to four
/// arms storing `r` and going on to the next stage.
const STAGES2: &[u8] = &[
    0x8d, 0x9e, 0x10, 0xad, 0x9e, 0x10, 0x29, 0x03, 0xaa, 0xbd, 0x8c, 0x10, 0x8d, 0x9c, 0x10, 0xbd,
    0x90, 0x10, 0x8d, 0x9d, 0x10, 0xa2, 0x00, 0x6c, 0x9c, 0x10, 0xa9, 0x00, 0x8e, 0x00, 0xc0, 0x8d,
    0x40, 0xc0, 0x4c, 0x46, 0x10, 0xa9, 0x01, 0x8e, 0x01, 0xc0, 0x8d, 0x41, 0xc0, 0x4c, 0x46, 0x10,
    0xa9, 0x02, 0x8e, 0x02, 0xc0, 0x8d, 0x42, 0xc0, 0x4c, 0x46, 0x10, 0xa9, 0x03, 0x8e, 0x03, 0xc0,
    0x8d, 0x43, 0xc0, 0x4c, 0x46, 0x10, 0xad, 0x9e, 0x10, 0x4a, 0x4a, 0x29, 0x03, 0xa8, 0xb9, 0x94,
    0x10, 0x8d, 0x9c, 0x10, 0xb9, 0x98, 0x10, 0x8d, 0x9d, 0x10, 0xa0, 0x01, 0x6c, 0x9c, 0x10, 0xa9,
    0x10, 0x8c, 0x04, 0xc0, 0x8d, 0x44, 0xc0, 0x4c, 0x8b, 0x10, 0xa9, 0x11, 0x8c, 0x05, 0xc0, 0x8d,
    0x45, 0xc0, 0x4c, 0x8b, 0x10, 0xa9, 0x12, 0x8c, 0x06, 0xc0, 0x8d, 0x46, 0xc0, 0x4c, 0x8b, 0x10,
    0xa9, 0x13, 0x8c, 0x07, 0xc0, 0x8d, 0x47, 0xc0, 0x4c, 0x8b, 0x10, 0x60, 0x1a, 0x25, 0x30, 0x3b,
    0x10, 0x10, 0x10, 0x10, 0x5f, 0x6a, 0x75, 0x80, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00, 0x00,
];
/// [`STAGES2`] with a third stage indexed by `X`.
const STAGES3: &[u8] = &[
    0x8d, 0xed, 0x10, 0xad, 0xed, 0x10, 0x29, 0x03, 0xaa, 0xbd, 0xd3, 0x10, 0x8d, 0xeb, 0x10, 0xbd,
    0xd7, 0x10, 0x8d, 0xec, 0x10, 0xa2, 0x00, 0x6c, 0xeb, 0x10, 0xa9, 0x00, 0x8e, 0x00, 0xc0, 0x8d,
    0x40, 0xc0, 0x4c, 0x46, 0x10, 0xa9, 0x01, 0x8e, 0x01, 0xc0, 0x8d, 0x41, 0xc0, 0x4c, 0x46, 0x10,
    0xa9, 0x02, 0x8e, 0x02, 0xc0, 0x8d, 0x42, 0xc0, 0x4c, 0x46, 0x10, 0xa9, 0x03, 0x8e, 0x03, 0xc0,
    0x8d, 0x43, 0xc0, 0x4c, 0x46, 0x10, 0xad, 0xed, 0x10, 0x4a, 0x4a, 0x29, 0x03, 0xa8, 0xb9, 0xdb,
    0x10, 0x8d, 0xeb, 0x10, 0xb9, 0xdf, 0x10, 0x8d, 0xec, 0x10, 0xa0, 0x01, 0x6c, 0xeb, 0x10, 0xa9,
    0x10, 0x8c, 0x04, 0xc0, 0x8d, 0x44, 0xc0, 0x4c, 0x8b, 0x10, 0xa9, 0x11, 0x8c, 0x05, 0xc0, 0x8d,
    0x45, 0xc0, 0x4c, 0x8b, 0x10, 0xa9, 0x12, 0x8c, 0x06, 0xc0, 0x8d, 0x46, 0xc0, 0x4c, 0x8b, 0x10,
    0xa9, 0x13, 0x8c, 0x07, 0xc0, 0x8d, 0x47, 0xc0, 0x4c, 0x8b, 0x10, 0xad, 0xed, 0x10, 0x4a, 0x4a,
    0x4a, 0x4a, 0x29, 0x03, 0xaa, 0xbd, 0xe3, 0x10, 0x8d, 0xeb, 0x10, 0xbd, 0xe7, 0x10, 0x8d, 0xec,
    0x10, 0xa2, 0x02, 0x6c, 0xeb, 0x10, 0xa9, 0x20, 0x8e, 0x08, 0xc0, 0x8d, 0x48, 0xc0, 0x4c, 0xd2,
    0x10, 0xa9, 0x21, 0x8e, 0x09, 0xc0, 0x8d, 0x49, 0xc0, 0x4c, 0xd2, 0x10, 0xa9, 0x22, 0x8e, 0x0a,
    0xc0, 0x8d, 0x4a, 0xc0, 0x4c, 0xd2, 0x10, 0xa9, 0x23, 0x8e, 0x0b, 0xc0, 0x8d, 0x4b, 0xc0, 0x4c,
    0xd2, 0x10, 0x60, 0x1a, 0x25, 0x30, 0x3b, 0x10, 0x10, 0x10, 0x10, 0x5f, 0x6a, 0x75, 0x80, 0x10,
    0x10, 0x10, 0x10, 0xa6, 0xb1, 0xbc, 0xc7, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00, 0x00,
];
/// `STA op` and one [`STAGES2`] stage with the opcode cell `op` (`A` as stored) between
/// `LDX #0` and `JMP (ptr)`.
const STAGE_CELL: &[u8] = &[
    0x8d, 0x1a, 0x10, 0x8d, 0x55, 0x10, 0xad, 0x55, 0x10, 0x29, 0x03, 0xaa, 0xbd, 0x4b, 0x10, 0x8d,
    0x53, 0x10, 0xbd, 0x4f, 0x10, 0x8d, 0x54, 0x10, 0xa2, 0x00, 0xea, 0x6c, 0x53, 0x10, 0xa9, 0x00,
    0x8e, 0x00, 0xc0, 0x8d, 0x40, 0xc0, 0x4c, 0x4a, 0x10, 0xa9, 0x01, 0x8e, 0x01, 0xc0, 0x8d, 0x41,
    0xc0, 0x4c, 0x4a, 0x10, 0xa9, 0x02, 0x8e, 0x02, 0xc0, 0x8d, 0x42, 0xc0, 0x4c, 0x4a, 0x10, 0xa9,
    0x03, 0x8e, 0x03, 0xc0, 0x8d, 0x43, 0xc0, 0x4c, 0x4a, 0x10, 0x60, 0x1e, 0x29, 0x34, 0x3f, 0x10,
    0x10, 0x10, 0x10, 0x00, 0x00, 0x00,
];

#[test]
fn jumps_switch_on_their_index() {
    for (prog, n) in [
        (&[(0x1000, CARRIED)][..], 4),
        (progs::JUMPTAB, 4),
        (progs::PUSHRTS, 2),
    ] {
        let p = check(&image(prog), &[0x1000], &acc_calls(&[0, 1, 2, 3, 2, 1]), IO);
        assert_eq!(table_keys(&p), [(0..n).collect::<Vec<u16>>()]);
    }
}

#[test]
fn an_index_carried_into_a_subroutine_switches_on_its_index() {
    let p = check(
        &image(&[(0x1000, TWO_CARRIED)]),
        &[0x1000],
        &acc_calls(&[0, 1, 2, 3, 6, 5, 4, 7]),
        IO,
    );
    assert_eq!(table_keys(&p), vec![(0..4).collect::<Vec<u16>>(); 2]);
}

#[test]
fn indices_carried_in_one_function_each_switch_on_their_index() {
    let accs: Vec<u8> = (0..64).collect();
    for (prog, n) in [(STAGES2, 2), (STAGES3, 3)] {
        let p = check(&image(&[(0x1000, prog)]), &[0x1000], &acc_calls(&accs), IO);
        assert_eq!(table_keys(&p), vec![(0..4).collect::<Vec<u16>>(); n]);
        assert_eq!(p.functions[0].ncarries, n as u32);
    }
}

/// An opcode the facts never saw deopts between the carry's copy and the transfer: the
/// reference runs on from the machine state.
#[test]
fn deopt_with_a_carried_index_live() {
    let img = image(&[(0x1000, STAGE_CELL)]);
    let seen = [0xea, 0xe8, 0xc8, 0xaa];
    let fc: Vec<(u16, u8)> = seen.iter().map(|&a| (0x1000, a)).collect();
    let f = facts(&img, &fc, 1_000_000);
    let runs = acc_calls(&[0xea, 0x18, 0xe8, 0xc8, 0x38, 0xaa]);
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
            ..Opts::default()
        };
        let q = lift_with(&img, &entries(&[0x1000]), &[(0xc000, 0xc100)], &f, opts);
        assert_eq!(table_keys(&q), [(0..4).collect::<Vec<u16>>()]);
        assert_eq!(q.functions[0].ncarries, 1);
        assert_eq!(diff(&img, &q, &runs, IO), 2);
    }
}

/// `Y` read from `sel,X` on one path and from a zero-page byte on the other, each path loading
/// the table pair with it and writing `Y` before the paths join at `JMP (ptr)`.
const MERGED: &[u8] = &[
    0x85, 0x02, 0x29, 0x03, 0xaa, 0xa5, 0x02, 0x30, 0x14, 0xbc, 0x4e, 0x10, 0xb9, 0x52, 0x10, 0x8d,
    0x5a, 0x10, 0xb9, 0x56, 0x10, 0x8d, 0x5b, 0x10, 0xa0, 0x00, 0x4c, 0x33, 0x10, 0x29, 0x03, 0x85,
    0x03, 0xa4, 0x03, 0xb9, 0x52, 0x10, 0x8d, 0x5a, 0x10, 0xb9, 0x56, 0x10, 0x8d, 0x5b, 0x10, 0x85,
    0x03, 0xa0, 0x01, 0x6c, 0x5a, 0x10, 0x8e, 0x50, 0xc0, 0x8c, 0x54, 0xc0, 0x60, 0xa9, 0x11, 0x8d,
    0x51, 0xc0, 0x60, 0xad, 0x52, 0xc0, 0x8d, 0x53, 0xc0, 0x60, 0x8c, 0x55, 0xc0, 0x60, 0x02, 0x00,
    0x03, 0x01, 0x36, 0x3d, 0x43, 0x4a, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
];
/// `AND #3; TAX; LDA tlo,X; STA ptr; LDA thi,X; STA ptr+1; LDX #0` and two calls of a
/// subroutine `JMP (ptr)`: the index is written in the caller.
const CALLED: &[u8] = &[
    0x29, 0x03, 0xaa, 0xbd, 0x30, 0x10, 0x8d, 0x38, 0x10, 0xbd, 0x34, 0x10, 0x8d, 0x39, 0x10, 0xa2,
    0x00, 0x20, 0x1e, 0x10, 0x8d, 0x00, 0xc0, 0x20, 0x1e, 0x10, 0x8d, 0x01, 0xc0, 0x60, 0x6c, 0x38,
    0x10, 0x8e, 0x50, 0xc0, 0x60, 0xa9, 0x11, 0x60, 0xad, 0x52, 0xc0, 0x60, 0x8e, 0x53, 0xc0, 0x60,
    0x21, 0x25, 0x28, 0x2c, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
];
/// [`CALLED`] with the subroutine calling itself once before its jump.
const RECURSIVE: &[u8] = &[
    0x29, 0x03, 0xaa, 0xbd, 0x45, 0x10, 0x8d, 0x4d, 0x10, 0xbd, 0x49, 0x10, 0x8d, 0x4e, 0x10, 0xa2,
    0x00, 0xa9, 0x01, 0x8d, 0x4f, 0x10, 0x20, 0x28, 0x10, 0x8d, 0x00, 0xc0, 0xa9, 0x01, 0x8d, 0x4f,
    0x10, 0x20, 0x28, 0x10, 0x8d, 0x01, 0xc0, 0x60, 0xad, 0x4f, 0x10, 0xf0, 0x06, 0xce, 0x4f, 0x10,
    0x20, 0x28, 0x10, 0x6c, 0x4d, 0x10, 0x8e, 0x50, 0xc0, 0x60, 0xa9, 0x11, 0x60, 0xad, 0x52, 0xc0,
    0x60, 0x8e, 0x53, 0xc0, 0x60, 0x36, 0x3a, 0x3d, 0x41, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00, 0x00,
];
/// The write `LDX #0` in a subroutine called before the jump's subroutine, which calls it
/// again from an arm of the jump.
const RERUN: &[u8] = &[
    0x29, 0x03, 0xaa, 0xbd, 0x31, 0x10, 0x8d, 0x39, 0x10, 0xbd, 0x35, 0x10, 0x8d, 0x3a, 0x10, 0x20,
    0x19, 0x10, 0x20, 0x1c, 0x10, 0x8d, 0x00, 0xc0, 0x60, 0xa2, 0x00, 0x60, 0x6c, 0x39, 0x10, 0x8e,
    0x50, 0xc0, 0x60, 0xa9, 0x11, 0x60, 0xad, 0x52, 0xc0, 0x60, 0x20, 0x19, 0x10, 0x8e, 0x53, 0xc0,
    0x60, 0x1f, 0x23, 0x26, 0x2a, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
];
/// `STA op` and [`CALLED`] with one call, the opcode cell `op` (`A` as stored) before
/// the subroutine's `JMP (ptr)`.
const CALLED_CELL: &[u8] = &[
    0x8d, 0x1b, 0x10, 0x29, 0x03, 0xaa, 0xbd, 0x2e, 0x10, 0x8d, 0x36, 0x10, 0xbd, 0x32, 0x10, 0x8d,
    0x37, 0x10, 0xa2, 0x00, 0x20, 0x1b, 0x10, 0x8d, 0x00, 0xc0, 0x60, 0xea, 0x6c, 0x36, 0x10, 0x8e,
    0x50, 0xc0, 0x60, 0xa9, 0x11, 0x60, 0xad, 0x52, 0xc0, 0x60, 0x8e, 0x53, 0xc0, 0x60, 0x1f, 0x23,
    0x26, 0x2a, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
];

#[test]
fn an_index_written_on_two_paths_switches_on_its_index() {
    let accs: Vec<u8> = (0..=255).collect();
    let p = check(
        &image(&[(0x1000, MERGED)]),
        &[0x1000],
        &acc_calls(&accs),
        IO,
    );
    assert_eq!(table_keys(&p), [(0..4).collect::<Vec<u16>>()]);
    assert_eq!(p.functions[0].ncarries, 1);
}

#[test]
fn an_index_written_in_a_caller_switches_on_its_index() {
    let p = check(
        &image(&[(0x1000, CALLED)]),
        &[0x1000],
        &acc_calls(&[0, 1, 2, 3, 6, 5, 4, 7]),
        IO,
    );
    assert_eq!(table_keys(&p), [(0..4).collect::<Vec<u16>>()]);
    assert!(p.functions.iter().all(|f| f.ncarries == 0));
}

#[test]
fn an_index_past_a_call_cycle_or_rerun_write_switches_on_addresses() {
    for prog in [RECURSIVE, RERUN] {
        let p = check(
            &image(&[(0x1000, prog)]),
            &[0x1000],
            &acc_calls(&[0, 1, 2, 3, 6, 5, 4, 7]),
            IO,
        );
        let keys = table_keys(&p);
        assert_eq!(keys.len(), 1);
        assert!(keys[0].iter().all(|&a| a >= 0x1000));
    }
}

/// An opcode the facts never saw deopts between the caller's write and the subroutine's
/// transfer: the reference runs on from the machine state.
#[test]
fn deopt_with_a_carried_index_live_across_a_call() {
    let img = image(&[(0x1000, CALLED_CELL)]);
    let fc: Vec<(u16, u8)> = [0xea, 0xe8, 0xc8, 0xaa]
        .iter()
        .map(|&a| (0x1000, a))
        .collect();
    let f = facts(&img, &fc, 1_000_000);
    let runs = acc_calls(&[0xea, 0x18, 0xe8, 0xc8, 0x38, 0xaa]);
    for cycles in [true, false] {
        let opts = Opts {
            optimize: true,
            cycles,
            ..Opts::default()
        };
        let q = lift_with(&img, &entries(&[0x1000]), &[(0xc000, 0xc100)], &f, opts);
        assert_eq!(table_keys(&q), [(0..4).collect::<Vec<u16>>()]);
        assert_eq!(diff(&img, &q, &runs, IO), 2);
    }
}

/// A device model: a latch at `$C020` (mirrored at `$C060`) whose high nibble reads 1, and
/// a register at `$C01B` reading `0` while bit 3 of `$C012` is set, any byte otherwise.
fn devices() -> di_lift::dev::Devices {
    use di_lift::dev::{Devices, Reg};
    let mut all = [0u8; 32];
    all.fill(0xff);
    let mut zero = [0u8; 32];
    zero[0] = 1;
    let bits = di_lift::vsa::Bits::from_bitmap;
    let mut d = Devices::default();
    let latch = Reg {
        latch: 0x0f,
        ones: 0xf0,
        ..Reg::default()
    };
    let osc = Reg {
        ctrl: Some(0xc012),
        cmask: 0x08,
        sets: vec![bits(&all), bits(&zero)],
        ..Reg::default()
    };
    let ctrl = Reg {
        sets: vec![bits(&all)],
        ..Reg::default()
    };
    d.regs
        .extend([(0xc020, latch), (0xc01b, osc), (0xc012, ctrl)]);
    d.canon.extend([
        (0xc020, 0xc020),
        (0xc060, 0xc020),
        (0xc01b, 0xc01b),
        (0xc012, 0xc012),
    ]);
    d
}

/// The values a register holds at `pc` in the lift's analysis.
fn held(p: &Program, pc: u16, r: usize) -> Vec<u32> {
    let w = p.regs.iter().find(|x| x.0 == pc).map(|x| x.1[r]).unwrap();
    (0..256)
        .filter(|&v| w[v as usize >> 6] >> (v & 63) & 1 == 1)
        .collect()
}

/// A read of a latch gives the value written through a mirror; a read of a derived register
/// the set its control register's written value selects; an undescribed address any byte.
#[test]
fn device_reads_take_the_models_sets() {
    let code: &[u8] = &[
        0xa9, 0x05, 0x8d, 0x60, 0xc0, // LDA #$05; STA $C060
        0xad, 0x20, 0xc0, 0xaa, // LDA $C020; TAX
        0xa9, 0x28, 0x8d, 0x12, 0xc0, // LDA #$28; STA $C012
        0xad, 0x1b, 0xc0, 0xa8, // LDA $C01B; TAY
        0xad, 0x30, 0xc0, 0x60, // LDA $C030; RTS
    ];
    let img = image(&[(0x1000, code)]);
    let lift = |devices| {
        let opts = Opts {
            entry_sp: Some(0xfd),
            loaded: Some((0x1000, 0x1016)),
            devices,
            ..Opts::default()
        };
        lift_with(
            &img,
            &entries(&[0x1000]),
            &[(0xc000, 0xc100)],
            &Facts::default(),
            opts,
        )
    };
    let p = lift(devices());
    assert_eq!(held(&p, 0x1009, 1), vec![0xf5]);
    assert_eq!(held(&p, 0x1012, 2), vec![0]);
    assert_eq!(held(&p, 0x1015, 0).len(), 256);
    assert_eq!(diff(&img, &p, &calls(2, |_| 0), IO), 0);
    let q = lift(di_lift::dev::Devices::default());
    assert_eq!(held(&q, 0x1009, 1).len(), 256);
}

/// A store to a register's mirror in a callee, and one of unknown address that may reach it,
/// leave the caller's read of the register what they may have written.
#[test]
fn device_writes_through_mirrors_and_callees() {
    let code: &[u8] = &[
        0xa9, 0x05, 0x8d, 0x20, 0xc0, // LDA #$05; STA $C020
        0x20, 0x20, 0x10, // JSR $1020
        0xad, 0x20, 0xc0, 0xaa, // LDA $C020; TAX
        0xac, 0x30, 0xc0, // LDY $C030
        0x99, 0x00, 0xc0, // STA $C000,Y
        0xad, 0x20, 0xc0, 0xaa, // LDA $C020; TAX
        0x60, // RTS
    ];
    let sub: &[u8] = &[0xa9, 0x07, 0x8d, 0x60, 0xc0, 0x60]; // LDA #$07; STA $C060; RTS
    let img = image(&[(0x1000, code), (0x1020, sub)]);
    let opts = Opts {
        entry_sp: Some(0xfd),
        loaded: Some((0x1000, 0x1026)),
        devices: devices(),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    assert_eq!(held(&p, 0x100c, 1), vec![0xf7]);
    assert_eq!(held(&p, 0x1016, 1), (0xf0..0x100).collect::<Vec<_>>());
    assert_eq!(diff(&img, &p, &calls(2, |_| 0), IO), 0);
}

/// Callees reading one- and two-byte inline arguments through the return address they pull,
/// each called from two sites, are continuation functions: the calls hand them their return
/// addresses in registers and every call continues past its arguments, no deopt.
#[test]
fn inline_arguments_are_continuations() {
    #[rustfmt::skip]
    let prog: &[u8] = &[
        0x20, 0x3e, 0x10, 0x11, 0x22, 0x8d, 0x00, 0xc0, 0x8e, 0x01, 0xc0, 0x20, 0x25, 0x10,
        0x33, 0x8d, 0x02, 0xc0, 0x20, 0x3e, 0x10, 0x44, 0x55, 0x8d, 0x03, 0xc0, 0x8e, 0x04,
        0xc0, 0x20, 0x25, 0x10, 0x66, 0x8d, 0x05, 0xc0, 0x60, 0x68, 0x85, 0xf0, 0x68, 0x85,
        0xf1, 0xa0, 0x01, 0xb1, 0xf0, 0xaa, 0xe6, 0xf0, 0xd0, 0x02, 0xe6, 0xf1, 0xa5, 0xf1,
        0x48, 0xa5, 0xf0, 0x48, 0x8a, 0x60, 0x68, 0x85, 0xf2, 0x68, 0x85, 0xf3, 0xa0, 0x02,
        0xb1, 0xf2, 0xaa, 0x88, 0xb1, 0xf2, 0xa8, 0xa5, 0xf2, 0x18, 0x69, 0x02, 0x85, 0xf2,
        0xa5, 0xf3, 0x69, 0x00, 0x48, 0xa5, 0xf2, 0x48, 0x98, 0x60,
    ];
    let p = static_lift(prog);
    assert_eq!(p.functions.iter().filter(|f| f.cont.is_some()).count(), 2);
    assert_eq!(diff(&static_image(prog), &p, &calls(3, |_| 0), IO), 0);
}

/// A deopt in a continuation function after it pushed back its advanced return address (a
/// store through an index past its table) writes that address as the frame's: the reference
/// returns past the call's argument.
#[test]
fn continuation_deopt_writes_the_address_it_holds() {
    #[rustfmt::skip]
    let prog: &[u8] = &[
        0x20, 0x0f, 0x10, 0x11, 0x8d, 0x00, 0xc0, 0x20, 0x0f, 0x10, 0x22, 0x8d, 0x01, 0xc0,
        0x60, 0x68, 0x85, 0xf0, 0x68, 0x85, 0xf1, 0xa0, 0x01, 0xb1, 0xf0, 0xaa, 0xa5, 0xf0,
        0x18, 0x69, 0x01, 0xa8, 0xa5, 0xf1, 0x69, 0x00, 0x48, 0x98, 0x48, 0xac, 0x10, 0xc0,
        0x99, 0x2f, 0x10, 0x8a, 0x60, 0x01, 0x02,
    ];
    let p = static_lift(prog);
    assert_eq!(p.functions.iter().filter(|f| f.cont.is_some()).count(), 1);
    let calls: Vec<(usize, u8)> = (0..8).map(|_| (0, 0)).collect();
    assert!(diff(&static_image(prog), &p, &calls, IO) > 0);
}

fn static_image(prog: &[u8]) -> Box<[u8; 0x10000]> {
    image(&[(0x1000, prog)])
}

/// The static lift (entry SP `$FD`) of `prog` at `$1000`.
fn static_lift(prog: &[u8]) -> Program {
    let opts = Opts {
        loaded: Some((0x1000, 0x1000 + prog.len() as u32)),
        entry_sp: Some(0xfd),
        fold: true,
        ..Opts::default()
    };
    lift_with(
        &static_image(prog),
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    )
}

/// A pointer play sets from I/O into the middle of a page of records and advances with
/// `INC; BNE; INC` reads its three fields unchecked: checks after its definition (the pair's two
/// stores) and after the advance (the pointer plus the zero flag times 256, so the low byte's
/// wrap is no transient leaving the object) keep it in the records, past whose end its fields
/// read the code that follows. Run off them by a count from I/O, the advance's check deopts;
/// both engines match the reference.
#[test]
fn pointer_objects_check_their_definitions() {
    let walk = |count: &[u8]| -> Vec<u8> {
        let mut p = vec![
            0xad, 0x00, 0xc0, 0x29, 0x3f, 0x09, 0x80, 0x85, 0xfb, 0xa9, 0x11, 0x85, 0xfc,
        ];
        p.extend(count);
        p.extend([
            0xa0, 0x00, 0xb1, 0xfb, 0x8d, 0x01, 0xc0, 0xa0, 0x01, 0xb1, 0xfb, 0x8d, 0x02, 0xc0,
            0xc8, 0xb1, 0xfb, 0x8d, 0x03, 0xc0, 0xe6, 0xfb, 0xd0, 0x02, 0xe6, 0xfc, 0xca, 0xd0,
            0xe3, 0x60,
        ]);
        p
    };
    let data: Vec<u8> = (0..0x80u32).map(|i| (i * 7) as u8).collect();
    for (count, deopts) in [
        (&[0xa2u8, 0x04][..], false),
        (&[0xae, 0x04, 0xc0][..], true),
    ] {
        let prog = walk(count);
        let img = image(&[
            (0x1150, &prog),
            (0x1180, &data),
            (0x1200, &[0xa9, 0x00, 0x60]),
        ]);
        let opts = Opts {
            entry_sp: Some(0xfd),
            loaded: Some((0x1150, 0x1203)),
            ..Opts::default()
        };
        let p = lift_with(
            &img,
            &entries(&[0x1150, 0x1200]),
            &[(0xc000, 0xc100)],
            &Facts::default(),
            opts,
        );
        assert_eq!(p.objects.len(), 1, "{count:x?}");
        assert_eq!((p.objects[0].0, p.objects[0].1), (0x1180, 0x1200));
        assert_eq!(p.objects[0].2.len(), 3);
        assert_eq!(p.stats.obj_guards, 2);
        for &pc in &p.objects[0].2 {
            let h = p.proven.iter().find(|r| r.0 == pc).unwrap();
            assert!(h.1 >= 0x1180 && h.2 <= 0x1202, "{h:x?}");
        }
        let n = diff_at(&img, &p, &calls(64, |_| 0), IO, 1_000_000, 0xff);
        assert_eq!(n > 0, deopts, "{count:x?}");
    }
}

/// A pointer set from I/O to one of four runs of records by a table of their starts, walked
/// from its start to a sign-bit marker: from the table's words the walk's load reads proven
/// inside the runs with no check; both engines match the reference.
#[test]
fn walks_read_to_their_marker() {
    let code = [
        0xad, 0x00, 0xc0, 0x29, 0x03, 0xaa, 0xbd, 0x80, 0x11, 0x85, 0xfb, 0xbd, 0x84, 0x11, 0x85,
        0xfc, 0xa0, 0x00, 0xb1, 0xfb, 0x30, 0x06, 0x8d, 0x01, 0xc0, 0xc8, 0xd0, 0xf6, 0x60,
    ];
    let mut data = vec![0x88, 0x90, 0x98, 0xa0, 0x11, 0x11, 0x11, 0x11];
    for r in 0..4u8 {
        data.extend((0..7).map(|i| (r * 7 + i) & 0x7f));
        data.push(0x80 | r);
    }
    let img = image(&[(0x1150, &code), (0x1180, &data)]);
    let opts = Opts {
        entry_sp: Some(0xfd),
        loaded: Some((0x1150, 0x11a8)),
        ..Opts::default()
    };
    let p = lift_with(
        &img,
        &entries(&[0x1150]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    );
    let h = p.proven.iter().find(|r| r.0 == 0x1162).unwrap();
    assert!(h.1 >= 0x1188 && h.2 < 0x11a8, "{h:x?}");
    assert_eq!(p.stats.obj_guards, 0);
    assert_eq!(diff_at(&img, &p, &calls(32, |_| 0), IO, 1_000_000, 0xff), 0);
}

/// A walk over a table of addresses ending in a zero high byte, each entry's second byte
/// incremented through the pointer it makes, at `$1000`; `d1` the second entry's word.
fn addr_walk(d1: u16) -> Vec<u8> {
    let [lo, hi] = d1.to_le_bytes();
    #[rustfmt::skip]
    let prog = vec![
        0xa2, 0x00, 0xbd, 0x1d, 0x10, 0xf0, 0x14, 0x85, 0xfc, 0xbd, 0x1c, 0x10, 0x85, 0xfb,
        0xa0, 0x01, 0xb1, 0xfb, 0x18, 0x69, 0x01, 0x91, 0xfb, 0xe8, 0xe8, 0xd0, 0xe7, 0x60,
        0x24, 0x10, lo, hi, 0x28, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    ];
    prog
}

/// The table the stores' pointer comes from is constant under the assumption their address
/// set then verifies: the store writes the bytes its entries name, none of the table's.
#[test]
fn table_of_addresses_resolves_its_stores() {
    let prog = addr_walk(0x1026);
    let p = static_lift(&prog);
    let set = p.addr_sets.iter().find(|s| (s.0, s.1) == (0x1015, true));
    let runs = vec![(0x1025, 0x1026), (0x1027, 0x1028), (0x1029, 0x102a)];
    assert_eq!(set.map(|s| &s.2), Some(&runs));
    let code: Vec<u16> = p.code.iter().map(|c| c.0).collect();
    assert!((0x101c..0x1024).all(|a| code.contains(&a)));
    assert_eq!(diff(&static_image(&prog), &p, &calls(3, |_| 0), IO), 0);
}

/// An entry naming a table byte makes the store meet the assumed table: it has no address
/// set, so its check leaves the lifted code where it hits the table.
#[test]
fn table_entry_naming_the_table_leaves_its_store_checked() {
    let prog = addr_walk(0x101f);
    let p = static_lift(&prog);
    assert!(!p.addr_sets.iter().any(|s| (s.0, s.1) == (0x1015, true)));
    assert!(diff(&static_image(&prog), &p, &calls(4, |_| 0), IO) > 0);
}

/// An init storing through an absolute operand it patches from a table: `$1000` onward, its
/// first three bytes `head`.
fn patched_store(head: [u8; 3]) -> Vec<u8> {
    let mut prog = vec![
        0xad, 0x12, 0x10, 0x8d, 0x0f, 0x10, 0xad, 0x13, 0x10, 0x8d, 0x10, 0x10, 0xa9, 0x5a, 0x8d,
        0xff, 0xff, 0x60, 0x14, 0x10, 0x00,
    ];
    prog[..3].copy_from_slice(&head);
    prog
}

/// The static lift of `prog` at `$1000` run once.
fn once_lift(prog: &[u8]) -> Program {
    let opts = Opts {
        loaded: Some((0x1000, 0x1000 + prog.len() as u32)),
        entry_sp: Some(0xfd),
        fold: true,
        once: true,
        ..Opts::default()
    };
    lift_with(
        &static_image(prog),
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    )
}

/// Run once, an init no input decides makes one run: the store through the operand it
/// patches writes the address the run patched in.
#[test]
fn once_run_gives_a_patched_operand_its_address() {
    let prog = patched_store([0xad, 0x12, 0x10]);
    let p = once_lift(&prog);
    let set = p.addr_sets.iter().find(|s| (s.0, s.1) == (0x100e, true));
    assert_eq!(set.map(|s| &s.2), Some(&vec![(0x1014, 0x1015)]));
    assert_eq!(diff(&static_image(&prog), &p, &calls(1, |_| 0), IO), 0);
}

/// An operand patched from the argument is decided by an input: the store has no address.
#[test]
fn once_run_an_input_decides_gives_no_address() {
    let prog = patched_store([0xea, 0xea, 0xea]);
    let p = once_lift(&prog);
    assert!(!p.addr_sets.iter().any(|s| (s.0, s.1) == (0x100e, true)));
    diff(&static_image(&prog), &p, &calls(1, |_| 0x14), IO);
}
