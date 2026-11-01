mod common;
#[allow(dead_code)]
mod progs;
use common::*;
use di_lift::{lift, lift_many, lift_with, Entry, Facts, Job, Opts};

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
