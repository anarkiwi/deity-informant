//! Whole-program lifting of 6502/6510 code to optimized, executable P-Code.
//!
//! [`lift`] takes the entry-state memory image, entry points, volatile address ranges and
//! [`Facts`] from running the program, and returns a [`Program`]: functions of basic blocks
//! of P-Code in which code bytes the program writes are memory reads, `JSR`/`RTS` pairs that
//! provably balance are `CALL`/`RETURN`, cycles are counted in a register, and every
//! assumption is guarded by a deopt to the reference interpreter. [`Runner`] executes one.

pub mod analysis;
pub mod bank;
pub mod emit;
pub mod exec;
pub mod func;
pub mod ir;
pub mod jit;
pub mod opt;
pub mod ptr;
pub mod vsa;
pub mod wide;

pub use analysis::{Entry, Facts};
pub use bank::{Back, BankMap, Banked};
pub use exec::{Backend, Exec, Outcome, Runner};
pub use ir::{Block, Function, LOp, Program, Stats};
pub use jit::Jit;

use analysis::{discover, is_stack_op, page1_check, Check, Flow, Iv};
use fixedbitset::FixedBitSet;
use func::H;
use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};

/// Lift options.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Opts {
    /// Optimize (otherwise the emitted P-Code as is).
    pub optimize: bool,
    /// Count cycles: `CY`, cycle-budget checks, volatile accesses at their cycle.
    pub cycles: bool,
}

impl Default for Opts {
    fn default() -> Self {
        Opts {
            optimize: true,
            cycles: false,
        }
    }
}

/// Lift `entries` of the program in `img` (the memory every entry starts from).
pub fn lift(
    img: &[u8; 0x10000],
    entries: &[Entry],
    volatile: &[(u32, u32)],
    facts: &Facts,
) -> Program {
    lift_with(img, entries, volatile, facts, Opts::default())
}

/// Function indices reachable from each function through one or more calls.
fn recursive(callees: &[Vec<u32>]) -> FixedBitSet {
    let mut rec = FixedBitSet::with_capacity(callees.len());
    for i in 0..callees.len() {
        let mut seen = FixedBitSet::with_capacity(callees.len());
        let mut work: Vec<u32> = callees[i].clone();
        while let Some(c) = work.pop() {
            if !seen.put(c as usize) {
                work.extend(&callees[c as usize]);
            }
        }
        rec.set(i, seen.contains(i));
    }
    rec
}

/// The addresses of the pointer bytes of the `JMP (ind)` at `pc`, and whether its operand is
/// a code cell.
fn ind_ptr(img: &[u8; 0x10000], g: &analysis::Graph, pc: u16) -> (Iv, bool) {
    if (1..3).any(|i| g.cells.contains(pc.wrapping_add(i) as usize)) {
        return (Iv::full(2), true);
    }
    let w = u16::from_le_bytes([
        img[pc.wrapping_add(1) as usize],
        img[pc.wrapping_add(2) as usize],
    ]);
    let h = (w & 0xff00) | (w.wrapping_add(1) & 0xff);
    let iv = Iv {
        lo: w.min(h) as u32,
        hi: w.max(h) as u32,
    };
    (iv, false)
}

/// Whether variant `v` at `pc` has an access other than a push or pull that may reach page 1
/// and cannot be checked at its instruction boundary, or jumps through a fixed page-1 pointer.
fn unchecked(img: &[u8; 0x10000], g: &analysis::Graph, pc: u16, v: &analysis::Var) -> bool {
    let acc = !is_stack_op(v.op)
        && v.acc
            .iter()
            .enumerate()
            .any(|(i, a)| !a.dummy && a.page1(&g.prot) && page1_check(v.op, i) == Check::No);
    let ind = matches!(v.flow, Flow::JmpInd(_)) && {
        let (iv, cell) = ind_ptr(img, g, pc);
        !cell && iv.meets(0x100, 0x200)
    };
    acc || ind
}

/// Per access of plan `p` other than a push or pull that may reach page 1 and reached it in
/// the facts (`seen`) or cannot be checked: its address interval (narrowed to its guarded
/// range) and node height.
fn unguarded<'a>(
    img: &'a [u8; 0x10000],
    g: &'a analysis::Graph,
    p: &'a func::Plan,
    seen: &'a FxHashMap<u16, (u16, u16)>,
) -> impl Iterator<Item = (Iv, H)> + 'a {
    p.nodes.iter().flat_map(move |(&(pc, h, _), node)| {
        g.vars[&pc].iter().zip(&node.arms).flat_map(move |(v, a)| {
            let acc = a.acc.iter().enumerate().filter(move |&(i, ac)| {
                !is_stack_op(v.op)
                    && !ac.dummy
                    && ac.page1(&g.prot)
                    && (seen.contains_key(&pc) || page1_check(v.op, i) == Check::No)
            });
            let ind = matches!(v.flow, Flow::JmpInd(_))
                .then(|| ind_ptr(img, g, pc))
                .filter(|&(iv, cell)| iv.meets(0x100, 0x200) && (!cell || seen.contains_key(&pc)));
            acc.map(move |(i, ac)| p.narrowed(seen, pc, v.op, i, ac.addr).unwrap_or(ac.addr))
                .chain(ind.map(|x| x.0))
                .map(move |iv| (iv, h))
        })
    })
}

/// Slot indices the unguarded page-1 accesses of plan `p` may hit.
fn exposed(
    img: &[u8; 0x10000],
    g: &analysis::Graph,
    p: &func::Plan,
    seen: &FxHashMap<u16, (u16, u16)>,
) -> FixedBitSet {
    let mut e = FixedBitSet::with_capacity(0x100);
    for (iv, _) in unguarded(img, g, p, seen) {
        e.insert_range(p.offsets(iv));
    }
    e
}

/// Unguarded page-1 accesses of plan `p` that may hit a stack byte kept out of memory.
fn sites(
    img: &[u8; 0x10000],
    g: &analysis::Graph,
    p: &func::Plan,
    slots: &FixedBitSet,
    seen: &FxHashMap<u16, (u16, u16)>,
) -> usize {
    unguarded(img, g, p, seen)
        .filter(|&(iv, h)| p.kept(slots, h, iv))
        .count()
}

/// Plans for `entries` of the discovered program `g`: an unexecuted instruction start in a
/// device (a volatile range, or I/O under the bank map), and with facts one whose page-1 access
/// cannot be checked, is not planned (edges to it deopt). With a bank map an entry is entered
/// in each configuration its calls were seen in.
pub fn plan(
    img: &[u8; 0x10000],
    g: &analysis::Graph,
    entries: &[u16],
    facts: &Facts,
    bank: Option<&BankMap>,
    volatile: &[(u32, u32)],
) -> Vec<func::Plan> {
    let starts: FxHashSet<u16> = facts.starts.iter().copied().collect();
    let banned: FxHashSet<u16> = g
        .vars
        .iter()
        .filter(|&(pc, vs)| {
            let device = volatile
                .iter()
                .any(|&(lo, hi)| (lo..hi).contains(&(*pc as u32)))
                || bank.is_some_and(|m| m.back(g.cfg[pc], *pc) == Back::Io);
            !starts.contains(pc)
                && !entries.contains(pc)
                && (device || !starts.is_empty() && vs.iter().any(|v| unchecked(img, g, *pc, v)))
        })
        .map(|(&pc, _)| pc)
        .collect();
    let roots: Vec<(u16, Vec<u8>)> = entries
        .iter()
        .map(|&pc| {
            let mut cs: Vec<u8> = match bank {
                None => vec![0],
                Some(m) => facts
                    .entry_port
                    .iter()
                    .filter(|e| e.0 == pc)
                    .flat_map(|e| e.1.iter().map(|&(d, v)| m.config(d, v)))
                    .collect(),
            };
            if cs.is_empty() {
                cs.push(g.cfg[&pc]);
            }
            cs.sort_unstable();
            cs.dedup();
            (pc, cs)
        })
        .collect();
    func::plan_all(g, &roots, &func::Seen::new(facts, banned, bank))
}

/// [`lift`] with options.
pub fn lift_with(
    img: &[u8; 0x10000],
    entries: &[Entry],
    volatile: &[(u32, u32)],
    facts: &Facts,
    opts: Opts,
) -> Program {
    lift_banked(img, entries, volatile, facts, opts, None)
}

/// [`lift_with`] over memory seen through `bank` (flat when `None`).
pub fn lift_banked(
    img: &[u8; 0x10000],
    entries: &[Entry],
    volatile: &[(u32, u32)],
    facts: &Facts,
    opts: Opts,
    bank: Option<&BankMap>,
) -> Program {
    // Entries are called with the decimal flag clear unless they declare it: guarded at entry,
    // so the arithmetic of code that does not set it is binary (`Graph::dec`).
    let declared: Vec<Entry> = entries
        .iter()
        .map(|e| {
            let mut e = e.clone();
            if !e.regs.iter().any(|r| r.0 == di_pcode::reg::D) {
                e.regs.push((di_pcode::reg::D, vec![0]));
            }
            e
        })
        .collect();
    let entries = &declared[..];
    let mut matched = true;
    let (g, plans, fidx, callees, recursive, slots, constcode, touches, seen, sites, private) = loop {
        let mut g = discover(img, entries, facts, bank, matched);
        let roots: Vec<u16> = entries.iter().map(|e| e.pc).collect();
        (g.prot, g.pre) = ptr::protect(&g, facts, volatile, &roots);
        let img: &[u8; 0x10000] = &g.img;
        let pcs: Vec<u16> = entries.iter().map(|e| e.pc).collect();
        let mut plans = plan(img, &g, &pcs, facts, bank, volatile);
        let fidx: FxHashMap<(u16, u8), u32> = plans
            .iter()
            .enumerate()
            .skip(entries.len())
            .map(|(i, p)| ((p.pc, p.entry[0]), i as u32))
            .collect();
        let callees: Vec<Vec<u32>> = plans
            .iter()
            .map(|p| p.callees().map(|k| fidx[&k]).collect())
            .collect();
        let recursive = recursive(&callees);
        let seen: FxHashMap<u16, (u16, u16)> = facts
            .page1
            .iter()
            .map(|&(p, lo, hi)| (p, (lo, hi)))
            .collect();
        // An entry called at one SP is specialized to it when that keeps more of its pushes out
        // of memory.
        for p in plans.iter_mut().take(entries.len()) {
            let sp = facts.entry_sp.iter().find(|e| e.0 == p.pc);
            if let Some(&[s]) = sp.map(|e| &e.1[..]) {
                let pushed = p.pushed(&g);
                let mut r = p.clone();
                r.refine(&g, img, s);
                let kept = |q: &func::Plan| {
                    let mut k = pushed.clone();
                    k.difference_with(&exposed(img, &g, q, &seen));
                    k.count_ones(..)
                };
                if kept(&r) > kept(p) {
                    *p = r;
                }
            }
        }
        // Slots: the bytes a function pushes, except in an outermost plan those an access other
        // than a push or pull may hit and those a pull may read before the call pushed them.
        let slots: Vec<FixedBitSet> = plans
            .iter()
            .enumerate()
            .map(|(i, p)| {
                let mut s = FixedBitSet::with_capacity(0x100);
                if !recursive.contains(i) {
                    s = p.pushed(&g);
                    if p.top {
                        s.difference_with(&exposed(img, &g, p, &seen));
                        s.difference_with(&p.pulled_unpushed(&g));
                    }
                }
                s
            })
            .collect();
        let mut constcode = g.code.clone();
        constcode.difference_with(&g.cells);
        constcode.difference_with(&g.rom_code);
        let page1 = |iv: Iv| iv.meets(0x100, 0x200);
        // Per function whether a load or store other than a push or pull may reach page 1.
        let touches: Vec<(bool, bool)> = plans
            .iter()
            .map(|p| {
                let (mut load, mut store) = (false, false);
                for pc in p.pcs() {
                    for v in &g.vars[&pc] {
                        if !is_stack_op(v.op) {
                            for a in v.acc.iter().filter(|a| !a.dummy && a.page1(&g.prot)) {
                                *(if a.store { &mut store } else { &mut load }) = true;
                            }
                        }
                        load |= matches!(v.flow, Flow::JmpInd(_)) && page1(ind_ptr(img, &g, pc).0);
                    }
                }
                (load, store)
            })
            .collect();
        let sites: usize = plans
            .iter()
            .zip(&slots)
            .map(|(p, s)| sites(img, &g, p, s, &seen))
            .sum();
        let private = !volatile.iter().any(|&(lo, hi)| lo < 0x200 && 0x100 < hi)
            && !g.code.ones().any(|a| (0x100..0x200).contains(&a))
            && plans
                .iter()
                .all(|p| p.nodes.keys().all(|k| k.1 != H::Unknown))
            && sites == 0;
        if private || !matched {
            break (
                g, plans, fidx, callees, recursive, slots, constcode, touches, seen, sites, private,
            );
        }
        matched = false;
    };
    let img: &[u8; 0x10000] = &g.img;
    let pcs: Vec<u16> = entries.iter().map(|e| e.pc).collect();
    let mut promote = FixedBitSet::with_capacity(plans.len());
    if private {
        promote.insert_range(..);
        promote.difference_with(&recursive);
    }
    let kept_bytes = plans
        .iter()
        .zip(&slots)
        .enumerate()
        .take(entries.len())
        .map(|(i, (p, s))| {
            let mut k = p.pushed(&g);
            if promote.contains(i) {
                k.difference_with(s);
            }
            k.count_ones(..)
        })
        .sum();
    // A callee's return checks its return address when its calls may store into page 1.
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
        promote,
        recursive,
        cycles: opts.cycles,
        fidx,
        bank,
        ret_guard,
        slots,
        seen,
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
        page1_sites: sites,
        sp_guards: plans.iter().filter(|p| p.sp.is_some()).count(),
        kept_bytes,
        unknown_heights: plans
            .iter()
            .map(|p| p.nodes.keys().filter(|k| k.1 == H::Unknown).count())
            .sum(),
        split_nodes: plans.iter().map(|p| p.nodes.len() - p.pcs().count()).sum(),
        recursive: sh.recursive.count_ones(..),
        unreached: facts
            .starts
            .iter()
            .filter(|s| !g.vars.contains_key(s))
            .count(),
        ..Stats::default()
    };
    let mut functions: Vec<Function> = plans
        .iter()
        .enumerate()
        .map(|(i, p)| {
            let port = facts
                .entry_port
                .iter()
                .find(|e| e.0 == p.pc && p.top)
                .and_then(|e| match e.1[..] {
                    [r] => Some(r),
                    _ => None,
                });
            let (f, n) = emit::emit(&sh, p, entries.get(i), port, i);
            stats.insns += n.insns;
            stats.raw_ops += n.raw;
            stats.code_guards += n.code_guards;
            stats.stack_guards += n.stack_guards;
            stats.ptr_guards += n.ptr_guards;
            stats.slot_ops += n.slot_ops;
            stats.sp_dispatch += n.sp_dispatch;
            stats.bank_dispatch += n.bank_dispatch;
            stats.calls += callees[i].len();
            stats.emitted_ops += f.blocks.iter().map(|b| b.ops.len()).sum::<usize>();
            f
        })
        .collect();
    if opts.optimize {
        let mut cx = opt::Ctx {
            img,
            constcode: &constcode,
            volatile,
            cycles: opts.cycles,
            sums: None,
        };
        let run = |fs: &mut Vec<Function>, cx: &opt::Ctx| {
            fs.iter_mut()
                .enumerate()
                .for_each(|(i, f)| opt::optimize(f, cx, i))
        };
        run(&mut functions, &cx);
        // Callee summaries matter only where deopts may leave registers dead: no cycles.
        if !opts.cycles && functions.len() > 1 {
            let sums = opt::summarize(&functions, &cx);
            cx.sums = Some(&sums);
            run(&mut functions, &cx);
        }
    }
    let ops = || {
        functions
            .iter()
            .flat_map(|f| &f.blocks)
            .flat_map(|b| &b.ops)
    };
    stats.blocks = functions.iter().map(|f| f.blocks.len()).sum();
    if !opts.cycles {
        stats.budget_checks = functions
            .iter()
            .map(|f| opt::budget_points(f).count_ones(..))
            .sum();
    }
    stats.ops = ops().count();
    let chains: Vec<(u32, wide::Chain)> = functions
        .iter()
        .enumerate()
        .flat_map(|(i, f)| {
            f.chains
                .iter()
                .cloned()
                .chain(wide::recognize(f))
                .map(move |c| (i as u32, c))
        })
        .collect();
    stats.chains = chains.len();
    stats.fused_chains = chains.iter().filter(|c| c.1.fused).count();
    stats.stack_ops = ops()
        .filter(|o| matches!(o.opc, di_pcode::Opc::Load | di_pcode::Opc::Store))
        .filter(|o| o.aux & ir::mem::STACK != 0)
        .count();
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
        bank: bank.cloned(),
        cycles: opts.cycles,
        stats,
        chains,
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
pub fn lift_many(jobs: &[Job], opts: Opts, tick: impl Fn() + Sync) -> Vec<Program> {
    jobs.par_iter()
        .map(|j| {
            let p = lift_with(&j.img, &j.entries, &j.volatile, &j.facts, opts);
            tick();
            p
        })
        .collect()
}
