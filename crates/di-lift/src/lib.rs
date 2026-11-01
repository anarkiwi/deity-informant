//! Whole-program lifting of 6502/6510 code to optimized, executable P-Code.
//!
//! [`lift`] takes a 64 KiB entry-state image, entry points, volatile address ranges and
//! [`Facts`] from running the program, and returns a [`Program`]: functions of basic blocks
//! of P-Code in which code bytes the program writes are memory reads, `JSR`/`RTS` pairs that
//! provably balance are `CALL`/`RETURN`, cycles are counted in a register, and every
//! assumption is guarded by a deopt to the reference interpreter. [`Runner`] executes one.

pub mod analysis;
pub mod emit;
pub mod exec;
pub mod func;
pub mod ir;
pub mod opt;

pub use analysis::{Entry, Facts};
pub use exec::{Exec, Outcome, Runner};
pub use ir::{Block, Function, LOp, Program, Stats};

use analysis::{discover, is_stack_op, Flow, Iv};
use fixedbitset::FixedBitSet;
use func::{Ctl, H};
use rayon::prelude::*;
use rustc_hash::FxHashMap;

/// Lift `entries` of the program in `img` (the memory every entry starts from).
pub fn lift(
    img: &[u8; 0x10000],
    entries: &[Entry],
    volatile: &[(u32, u32)],
    facts: &Facts,
) -> Program {
    lift_with(img, entries, volatile, facts, true)
}

/// [`lift`], optionally without optimization (the emitted P-Code as is).
pub fn lift_with(
    img: &[u8; 0x10000],
    entries: &[Entry],
    volatile: &[(u32, u32)],
    facts: &Facts,
    optimize: bool,
) -> Program {
    let g = discover(img, entries, facts);
    let pcs: Vec<u16> = entries.iter().map(|e| e.pc).collect();
    let plans = func::plan_all(&g, &pcs);
    let fidx: FxHashMap<u16, u32> = plans
        .iter()
        .enumerate()
        .skip(entries.len())
        .map(|(i, p)| (p.pc, i as u32))
        .collect();
    let mut constcode = g.code.clone();
    constcode.difference_with(&g.cells);
    let page1 = |iv: Iv| iv.meets(0x100, 0x200);
    // Accesses other than pushes and pulls that may reach page 1, per function.
    let touches: Vec<(bool, bool)> = plans
        .iter()
        .map(|p| {
            let (mut load, mut store) = (false, false);
            for pc in p.nodes.keys() {
                for v in &g.vars[pc] {
                    if is_stack_op(v.op) {
                        continue;
                    }
                    for a in &v.acc {
                        if page1(a.addr) {
                            *(if a.store { &mut store } else { &mut load }) = true;
                        }
                    }
                    if let Flow::JmpInd(_) = v.flow {
                        let w = u16::from_le_bytes([
                            img[pc.wrapping_add(1) as usize],
                            img[pc.wrapping_add(2) as usize],
                        ]);
                        let cell = (1..3).any(|i| g.cells.contains(pc.wrapping_add(i) as usize));
                        load |= cell || (0x100..0x200).contains(&w) || (0xff..0x1ff).contains(&w);
                    }
                }
            }
            (load, store)
        })
        .collect();
    let private = !volatile.iter().any(|&(lo, hi)| lo < 0x200 && 0x100 < hi)
        && !g.cells.ones().any(|a| (0x100..0x200).contains(&a))
        && plans
            .iter()
            .all(|p| p.nodes.values().all(|n| n.h != H::Unknown))
        && touches.iter().all(|&(l, s)| !l && !s);
    // A callee's return checks its return address when its calls may store into page 1.
    let callees: Vec<Vec<u32>> = plans
        .iter()
        .map(|p| {
            p.nodes
                .values()
                .flat_map(|n| &n.ctl)
                .filter_map(|(c, _)| match c {
                    Ctl::Call { f, .. } => Some(fidx[f]),
                    _ => None,
                })
                .collect()
        })
        .collect();
    let mut ret_guard = FixedBitSet::with_capacity(plans.len());
    if !private {
        for (i, t) in touches.iter().enumerate() {
            ret_guard.set(i, t.1);
        }
        loop {
            let mut changed = false;
            for (i, cs) in callees.iter().enumerate() {
                if !ret_guard.contains(i) && cs.iter().any(|&c| ret_guard.contains(c as usize)) {
                    ret_guard.insert(i);
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
    }
    let sh = emit::Shared {
        img,
        g: &g,
        volatile,
        constcode: emit::prefix(&constcode),
        private,
        fidx,
        ret_guard,
    };
    let cx = opt::Ctx {
        img,
        constcode: &constcode,
        volatile,
        private,
    };
    let mut stats = Stats {
        cells: g.cells.count_ones(..),
        opcode_cells: g
            .cells
            .ones()
            .filter(|&a| g.vars.contains_key(&(a as u16)))
            .count(),
        code_bytes: constcode.count_ones(..),
        stack_private: private,
        unreached: facts
            .starts
            .iter()
            .filter(|s| !g.vars.contains_key(s))
            .count(),
        ..Stats::default()
    };
    let functions = plans
        .iter()
        .enumerate()
        .map(|(i, p)| {
            let (mut f, insns, raw, guards) = emit::emit(&sh, p, entries.get(i));
            stats.insns += insns;
            stats.raw_ops += raw;
            stats.code_guards += guards;
            stats.calls += p
                .nodes
                .values()
                .flat_map(|n| &n.ctl)
                .filter(|(c, _)| matches!(c, Ctl::Call { .. }))
                .count();
            stats.emitted_ops += f.blocks.iter().map(|b| b.ops.len()).sum::<usize>();
            if optimize {
                opt::optimize(&mut f, &cx);
            }
            stats.blocks += f.blocks.len();
            stats.ops += f.blocks.iter().map(|b| b.ops.len()).sum::<usize>();
            f
        })
        .collect();
    Program {
        functions,
        entries: pcs
            .iter()
            .enumerate()
            .map(|(i, &pc)| (pc, i as u32))
            .collect(),
        code: constcode.ones().map(|a| (a as u16, img[a])).collect(),
        volatile: volatile.to_vec(),
        elide_ret: private,
        stats,
    }
}

/// One lift request.
pub struct Job {
    pub img: Box<[u8; 0x10000]>,
    pub entries: Vec<Entry>,
    pub volatile: Vec<(u32, u32)>,
    pub facts: Facts,
}

/// Lift many programs in parallel; `tick` runs after each one.
pub fn lift_many(jobs: &[Job], tick: impl Fn() + Sync) -> Vec<Program> {
    jobs.par_iter()
        .map(|j| {
            let p = lift(&j.img, &j.entries, &j.volatile, &j.facts);
            tick();
            p
        })
        .collect()
}
