//! Whole-program lifting of 6502/6510 code to optimized, executable P-Code.
//!
//! [`lift`] takes the entry-state memory image, entry points, volatile address ranges and
//! [`Facts`] from running the program, and returns a [`Program`]: functions of basic blocks
//! of P-Code in which code bytes the program writes are memory reads, `JSR`/`RTS` pairs that
//! provably balance are `CALL`/`RETURN`, cycles are counted in a register, and every
//! assumption is guarded by a deopt to the reference interpreter. [`Runner`] executes one.

pub mod analysis;
pub mod bank;
pub mod dispatch;
pub mod emit;
pub mod exec;
pub mod fold;
pub mod func;
pub mod inst;
pub mod ir;
pub mod jit;
pub mod known;
pub mod opt;
pub mod ptr;
pub mod scev;
pub mod vsa;
pub mod wide;
pub mod word;

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
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Opts {
    /// Optimize (otherwise the emitted P-Code as is).
    pub optimize: bool,
    /// Count cycles: `CY`, cycle-budget checks, volatile accesses at their cycle.
    pub cycles: bool,
    /// The SP every entry is called at (after its return address), as a calling convention
    /// rather than an observation: outermost plans are specialized to it, pointer windows keep
    /// pointers out of the volatile pages too, and while the stack is private only a store
    /// that may hit the stack bytes the program can hold is checked, against those bytes
    /// (*Stack extent*).
    pub entry_sp: Option<u8>,
    /// The volatile addresses whose reads have effects (none: every volatile address): a
    /// throw-away read that may reach none of them is not made.
    pub sensitive: Option<Vec<u16>>,
    /// The `[lo, hi)` the program was loaded to: tables end at its end at the latest
    /// (*Static proofs*, *Table extents*).
    pub loaded: Option<(u32, u32)>,
    /// Lift copies of one routine that differ only in their operands as one function taking
    /// the copy's index ([`inst`]); without facts and a bank map only.
    pub fold: bool,
    /// The entry (index) the host calls once, before any other, on the image: its decided
    /// path is folded into the program's starting memory ([`fold`]); a static lift
    /// (`entry_sp`, `loaded`, no facts, bank map nor cycle counting) only.
    pub init: Option<usize>,
}

impl Default for Opts {
    fn default() -> Self {
        Opts {
            optimize: true,
            cycles: false,
            entry_sp: None,
            sensitive: None,
            loaded: None,
            fold: false,
            init: None,
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

/// The plan index of each instance function by class.
fn inst_index(plans: &[func::Plan]) -> Vec<u32> {
    let mut out: Vec<(u32, u32)> = plans
        .iter()
        .enumerate()
        .filter_map(|(i, p)| p.inst.as_ref().map(|x| (x.class, i as u32)))
        .collect();
    out.sort_unstable();
    out.into_iter().map(|x| x.1).collect()
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

/// Marks the accesses a static lift keeps out of the zones (page 1 and the volatile pages):
/// those through a pointer (a zero-page pair no window keeps, or operand cells) that may
/// reach a zone. Each is checked before its instruction (when every such access of the
/// instruction can be), unless its operand names only pages outside the zones (`zfree`).
/// A pointer whose high byte some store sets to a zone's page means the device or stack.
fn zone_checks(g: &mut analysis::Graph, entries: &[Entry]) {
    let (win, zones, cells, addr_sets) = (&g.prot.win, &g.zones, &g.cells, &g.addr_sets);
    let mut meant = FixedBitSet::with_capacity(0x10000);
    for a in g.vars.iter().filter(|(_, vs)| !is_stack_op(vs[0].op)) {
        for ac in a.1.iter().flat_map(|v| &v.acc).filter(|ac| ac.store) {
            let into = |&(lo, hi): &(u32, u32)| lo >> 8 <= ac.val.lo && ac.val.hi < hi >> 8;
            if ac.addr.hi - ac.addr.lo < 0x100 && zones.iter().any(into) {
                meant.insert_range(ac.addr.lo as usize..ac.addr.hi as usize + 1);
            }
        }
    }
    let held = byte_bounds(g, entries);
    let in_zone = |page: u32| {
        zones
            .iter()
            .any(|&(lo, hi)| (lo >> 8..hi >> 8).contains(&page))
    };
    // The pages an absolute operand may name: its high byte a constant or a closed cell.
    let pages = |pc: u16| -> Option<Vec<u32>> {
        let a = pc.wrapping_add(2);
        match (cells.contains(a as usize), g.vals.get(&a)) {
            (false, _) => Some(vec![g.img[a as usize] as u32]),
            (true, Some(vs)) if g.closed.contains(a as usize) => {
                Some(vs.iter().map(|&v| v as u32).collect())
            }
            _ => None,
        }
    };
    for (&pc, vs) in g.vars.iter_mut() {
        let operand = (1..3).any(|i| cells.contains(pc.wrapping_add(i) as usize));
        let high = |a: &analysis::Access| match a.ptr {
            Some(p) => !meant.contains_any_in_range(p.hi.lo as usize..p.hi.hi as usize + 1),
            None => {
                let hi = pc.wrapping_add(2);
                operand && !meant.contains(hi as usize) && !in_zone(g.img[hi as usize] as u32)
            }
        };
        let named = if operand { pages(pc) } else { None };
        for v in vs.iter_mut().filter(|v| !is_stack_op(v.op)) {
            let pointer = |a: &analysis::Access| {
                a.addr.hi - a.addr.lo >= 0x1ff
                    && (a.ptr.is_some() || operand)
                    && high(a)
                    && !a.windowed(win)
                    && zones.iter().any(|&(lo, hi)| a.addr.meets(lo, hi))
            };
            let step = index_reads(v.op);
            let clear = |p: u32| (p..=p + step).all(|q| !in_zone(q & 0xff));
            // An address set no page of which, nor the page before (its throw-away read's),
            // is a zone's.
            let set_free = [false, true].iter().any(|&st| {
                v.acc
                    .iter()
                    .any(|a| a.store == st && a.ptr.is_some() && !a.dummy)
                    && addr_sets.get(&(pc, st)).is_some_and(|set| {
                        set.iter().all(|&x| {
                            let p = x as u32 >> 8;
                            !in_zone(p) && !in_zone(p.wrapping_sub(1) & 0xff)
                        })
                    })
            });
            let free = set_free
                || match v.acc.iter().find_map(|a| a.ptr) {
                    Some(p) => (p.hi.lo..=p.hi.hi.min(0xff))
                        .all(|b| held[b as usize].is_some_and(|h| (h.lo..=h.hi).all(clear))),
                    None => named
                        .as_ref()
                        .is_some_and(|ps| ps.iter().all(|&p| clear(p))),
                };
            let mut hit = v
                .acc
                .iter()
                .enumerate()
                .filter(|(_, a)| pointer(a))
                .peekable();
            if hit.peek().is_some() && (free || hit.all(|(i, _)| page1_check(v.op, i) != Check::No))
            {
                for a in v.acc.iter_mut().filter(|a| pointer(a)) {
                    (a.zone, a.zfree) = (true, free);
                }
            }
        }
    }
}

/// Per byte, the values it may hold when every store that may write it has an exact address
/// and a value interval narrower than a byte: its entry byte and the stored values (`None`:
/// unbounded, as for page 1 where pushes land).
fn byte_bounds(g: &analysis::Graph, entries: &[Entry]) -> Vec<Option<Iv>> {
    let mut held: Vec<Option<Iv>> = (0..0x10000)
        .map(|a| Some(Iv::exact(g.img[a] as u32)))
        .collect();
    held[0x100..0x200].fill(None);
    let stores = g.vars.values().flatten().flat_map(|v| &v.acc);
    for ac in stores.filter(|a| a.store) {
        if ac.addr.value().is_none() || ac.val.hi - ac.val.lo >= 0xff {
            held[ac.addr.lo as usize..=ac.addr.hi.min(0xffff) as usize].fill(None);
        } else if let Some(h) = held[ac.addr.lo as usize].as_mut() {
            *h = Iv {
                lo: h.lo.min(ac.val.lo),
                hi: h.hi.max(ac.val.hi),
            };
        }
    }
    for a in g.cells.ones() {
        held[a] = None;
    }
    for e in entries {
        for &(a, v) in &e.pre {
            if let analysis::Val::R(_) = v {
                held[a as usize] = None;
            }
        }
    }
    held
}

/// 1 when opcode `op`'s template reads `X` or `Y` (an indexed access may cross a page).
fn index_reads(op: u8) -> u32 {
    let ops = di_pcode::Template::of(op).ops();
    u32::from(ops.iter().flat_map(|o| o.ins).any(|v| {
        v.space() == di_pcode::Space::Reg
            && matches!(v.off() as usize, di_pcode::reg::X | di_pcode::reg::Y)
    }))
}

/// Whether variant `v` at `pc` has an access other than a push or pull that may reach page 1
/// and cannot be checked at its instruction boundary, or jumps through a fixed page-1 pointer.
fn unchecked(img: &[u8; 0x10000], g: &analysis::Graph, pc: u16, v: &analysis::Var) -> bool {
    let acc = !is_stack_op(v.op)
        && v.acc
            .iter()
            .enumerate()
            .any(|(i, a)| !a.dummy && a.page1(&g.prot.win) && page1_check(v.op, i) == Check::No);
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
                    && ac.page1(&g.prot.win)
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

/// The page-1 addresses `[lo, $1FF]` the stack may hold below and at the host's return
/// address when every entry is called at SP `s` (*Stack extent*): from the deepest byte a
/// push or an elided `CALL`'s return address reaches through the plans' call chains, all of
/// page 1 when a plan is recursive or a height unknown.
fn extent(
    plans: &[func::Plan],
    fidx: &FxHashMap<(u16, u8), u32>,
    g: &analysis::Graph,
    s: u8,
) -> Iv {
    fn depth(
        i: usize,
        plans: &[func::Plan],
        fidx: &FxHashMap<(u16, u8), u32>,
        g: &analysis::Graph,
        memo: &mut [Option<Option<u32>>],
    ) -> Option<u32> {
        if let Some(d) = memo[i] {
            return d;
        }
        memo[i] = Some(None);
        let p = &plans[i];
        let mut d = p.pushed(g).ones().next_back().map_or(0, |x| x as u32 + 1);
        let mut ok = p.nodes.keys().all(|k| k.1 != H::Unknown);
        // A tail call runs its callee at the entry SP, over this function's return address;
        // an instance function at the caller's SP.
        let ifidx = inst_index(plans);
        let inline = p
            .tails
            .iter()
            .map(|&(t, _, c)| (fidx[&(t, c)], H::Known(0)))
            .chain(p.insts.iter().map(|(k, c)| (ifidx[c.class as usize], k.1)));
        for (f, h) in inline.collect::<Vec<_>>() {
            match (depth(f as usize, plans, fidx, g, memo), h) {
                (Some(e), H::Known(h)) => d = d.max((-h) as u32 + e),
                _ => ok = false,
            }
        }
        for (&(_, h, _), n) in &p.nodes {
            for a in &n.arms {
                let fs: Vec<(u16, u8)> = match &a.ctl {
                    func::Ctl::Call { f, c, .. } => vec![(*f, *c)],
                    func::Ctl::CallInd { fs, c, .. } => fs.iter().map(|&f| (f, *c)).collect(),
                    func::Ctl::CallOp { f, c, .. } => vec![(*f, *c)],
                    func::Ctl::BrkCall { f, .. } => vec![(*f, g.cfg[f])],
                    _ => continue,
                };
                // A `BRK` call pushes three bytes.
                let n = if matches!(a.ctl, func::Ctl::BrkCall { .. }) {
                    3
                } else {
                    2
                };
                for k in fs {
                    match (depth(fidx[&k] as usize, plans, fidx, g, memo), h) {
                        (Some(e), H::Known(h)) => d = d.max((n - h) as u32 + e),
                        _ => ok = false,
                    }
                }
            }
        }
        let r = ok.then_some(d);
        memo[i] = Some(r);
        r
    }
    let mut memo = vec![None; plans.len()];
    let tops = plans.iter().enumerate().filter(|(_, p)| p.top);
    let d: Option<u32> = tops
        .map(|(i, _)| depth(i, plans, fidx, g, &mut memo))
        .try_fold(0, |m, d| d.map(|d| m.max(d)));
    let top = 0x100 + s as u32 + 1;
    Iv {
        lo: d
            .and_then(|d| top.checked_sub(d))
            .filter(|&lo| lo >= 0x100)
            .unwrap_or(0x100),
        hi: 0x1ff,
    }
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
    fold: bool,
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
    func::plan_all(g, &roots, &func::Seen::new(facts, banned, bank), fold)
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

/// Entries are called with the decimal flag clear unless they declare or set it: guarded at
/// entry, so the arithmetic of code that does not set it is binary (`Graph::dec`).
fn declared(entries: &[Entry]) -> Vec<Entry> {
    entries
        .iter()
        .map(|e| {
            let mut e = e.clone();
            let d = di_pcode::reg::D;
            if !e.regs.iter().any(|r| r.0 == d) && !e.set.iter().any(|r| r.0 == d) {
                e.regs.push((d, vec![0]));
            }
            e
        })
        .collect()
}

/// Runs `[lo, hi)` of the bytes of `b`.
fn runs(b: &FixedBitSet) -> Vec<(u32, u32)> {
    let mut runs: Vec<(u32, u32)> = Vec::new();
    for a in b.ones().map(|a| a as u32) {
        match runs.last_mut() {
            Some(r) if r.1 == a => r.1 = a + 1,
            _ => runs.push((a, a + 1)),
        }
    }
    runs
}

/// Pointer-load hulls by `(pc, opcode, access index)` as sorted [`ir::Reach`]es.
fn reaches(r: &vsa::Hulls) -> Vec<ir::Reach> {
    let mut v: Vec<ir::Reach> = (r.iter())
        .map(|(&(pc, _, _), (a, b))| (pc, a.lo, a.hi, b.lo, b.hi))
        .collect();
    v.sort_unstable();
    v
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
    let plain = Opts {
        init: None,
        ..opts.clone()
    };
    let entries = declared(entries);
    if let (Some(ix), Some(sp), Some(loaded), None, true, false) = (
        opts.init,
        opts.entry_sp,
        opts.loaded,
        bank,
        facts.starts.is_empty() && facts.writes.is_empty(),
        opts.cycles,
    ) {
        if let Some((img, es, f)) = fold::prepare(
            img,
            &entries,
            ix,
            volatile,
            opts.sensitive.as_deref(),
            loaded,
            sp,
        ) {
            let ix = f.index;
            return lift_core(&img, &es, volatile, facts, plain, bank, Some(f), Some(ix));
        }
        return lift_core(img, &entries, volatile, facts, plain, bank, None, Some(ix));
    }
    lift_core(img, &entries, volatile, facts, plain, bank, None, None)
}

/// The lift of `entries` (declared, [`declared`]), `fold` the init entry folded into `img`,
/// `init` the index of the entry the host calls once, first.
#[allow(clippy::too_many_arguments)]
fn lift_core(
    img: &[u8; 0x10000],
    entries: &[Entry],
    volatile: &[(u32, u32)],
    facts: &Facts,
    opts: Opts,
    bank: Option<&BankMap>,
    fold: Option<fold::Fold>,
    init: Option<usize>,
) -> Program {
    let mut matched = true;
    let (g, plans, fidx, callees, recursive, slots, constcode, touches, seen, sites, private) = loop {
        let mut g = discover(
            img,
            entries,
            facts,
            bank,
            matched,
            opts.loaded,
            opts.entry_sp,
            opts.entry_sp.map(|_| vsa::Wanted { volatile, init }),
        );
        let roots: Vec<u16> = entries.iter().map(|e| e.pc).collect();
        // Declared entry SP: the windows also keep pointers out of the volatile pages.
        let zones = match opts.entry_sp {
            Some(_) => ptr::zones(volatile),
            None => vec![ptr::PAGE1],
        };
        g.prot = ptr::protect(&g, facts, volatile, &zones, &roots, bank);
        g.zones = zones;
        if opts.entry_sp.is_some() {
            zone_checks(&mut g, entries);
        }
        let img: &[u8; 0x10000] = &g.img;
        let pcs: Vec<u16> = entries.iter().map(|e| e.pc).collect();
        let fold = opts.fold && !opts.cycles && facts.starts.is_empty();
        let mut plans = plan(img, &g, &pcs, facts, bank, volatile, fold);
        let fidx: FxHashMap<(u16, u8), u32> = plans
            .iter()
            .enumerate()
            .skip(entries.len())
            .filter(|(_, p)| p.inst.is_none())
            .map(|(i, p)| ((p.pc, p.entry[0]), i as u32))
            .collect();
        let ifidx = inst_index(&plans);
        let callees: Vec<Vec<u32>> = plans
            .iter()
            .map(|p| {
                p.callees()
                    .map(|k| fidx[&k])
                    .chain(p.insts.values().map(|c| ifidx[c.class as usize]))
                    .collect()
            })
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
            if let Some(s) = opts.entry_sp {
                p.refine(&g, img, s);
                continue;
            }
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
                            for a in v.acc.iter().filter(|a| !a.dummy && a.page1(&g.prot.win)) {
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
    // Stack bytes below SP are not in memory while the stack is private and a deopt cannot
    // restore those of returned frames, so pushes also store where a load, whatever the
    // facts saw and the pointer windows assume, may reach page 1: a read of them is then
    // exact. A static lift (declared entry SP) relies on its windows and zone checks.
    let page1 = |iv: Iv| iv.meets(0x100, 0x200);
    let reads_page1 = |a: &analysis::Access| match opts.entry_sp {
        Some(_) => a.page1(&g.prot.win),
        None => page1(a.addr),
    };
    let page1 = |iv: Iv| iv.meets(0x100, 0x200);
    let shadow = private
        && plans.iter().any(|p| {
            p.pcs().any(|pc| {
                g.vars[&pc].iter().any(|v| {
                    !is_stack_op(v.op)
                        && (v.acc.iter().any(|a| !a.dummy && !a.store && reads_page1(a))
                            || matches!(v.flow, Flow::JmpInd(_)) && page1(ind_ptr(img, &g, pc).0))
                })
            })
        });
    let stack = opts
        .entry_sp
        .filter(|_| private)
        .map(|s| extent(&plans, &fidx, &g, s));
    let sh = emit::Shared {
        stack,
        img,
        g: &g,
        volatile,
        constcode: emit::prefix(&{
            let mut c = constcode.clone();
            c.extend(g.tables.iter().map(|&a| a as usize));
            c
        }),
        private,
        shadow,
        promote,
        recursive,
        cycles: opts.cycles,
        ifidx: inst_index(&plans),
        fidx,
        bank,
        ret_guard,
        above: plans
            .iter()
            .enumerate()
            .filter(|p| p.1.above)
            .map(|p| p.0)
            .collect(),
        slots,
        seen,
        sensitive: opts.sensitive.as_deref(),
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
        instances: plans.iter().filter(|p| p.inst.is_some()).count(),
        instance_copies: plans
            .iter()
            .filter_map(|p| p.inst.as_ref())
            .map(|x| x.c.copies())
            .sum(),
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
            stats.zone_guards += n.zone_guards;
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
    // The instruction starts the functions plan (every copy's of an instance function):
    // constant code is their bytes the program does not write.
    let starts: Vec<u16> = {
        let mut s: std::collections::BTreeSet<u16> = plans
            .iter()
            .flat_map(|p| p.nodes.keys().map(|k| k.0))
            .collect();
        for x in plans.iter().filter_map(|p| p.inst.as_ref()) {
            s.extend(x.c.nodes.iter().flat_map(|n| n.pcs.iter().flatten()));
        }
        s.into_iter().collect()
    };
    let mut fold = fold;
    let initcode = match fold.as_mut() {
        Some(f) => f.settle(&g, entries),
        None => FixedBitSet::with_capacity(0x10000),
    };
    let mut planned = FixedBitSet::with_capacity(0x10000);
    for &pc in &starts {
        for v in &g.vars[&pc] {
            for i in 0..di_pcode::Template::of(v.op).len as u16 {
                planned.insert(pc.wrapping_add(i) as usize);
            }
        }
    }
    Program {
        init: fold,
        functions,
        entries: pcs
            .iter()
            .enumerate()
            .map(|(i, &pc)| (pc, i as u32))
            .collect(),
        code: {
            let mut c = constcode.clone();
            c.intersect_with(&planned);
            c.union_with(&initcode);
            c.extend(g.tables.iter().map(|&a| a as usize));
            c.ones().map(|a| (a as u16, img[a])).collect()
        },
        volatile: volatile.to_vec(),
        written: runs(&g.written),
        fresh: g.fresh.as_ref().map_or_else(Vec::new, runs),
        starts,
        elide_ret: private,
        bank: bank.cloned(),
        cycles: opts.cycles,
        stats,
        chains,
        induction: g.ind.clone(),
        reach: reaches(&g.reach),
        proven: reaches(&g.proven),
        bounded: {
            let mut v: Vec<(u16, u32, u32)> =
                g.bounded.iter().map(|(k, r)| (k.0, r.0, r.1)).collect();
            v.sort_unstable();
            v.dedup();
            v
        },
        regs: {
            let mut v: Vec<(u16, [[u64; 4]; 3])> = g.regs.iter().map(|(&pc, r)| (pc, *r)).collect();
            v.sort_unstable();
            v
        },
        addr_sets: {
            let mut v: Vec<ir::AddrSet> = g
                .addr_sets
                .iter()
                .map(|(&(pc, st), a)| {
                    let mut runs: Vec<(u32, u32)> = Vec::new();
                    for &x in a {
                        match runs.last_mut() {
                            Some(r) if r.1 == x as u32 => r.1 += 1,
                            _ => runs.push((x as u32, x as u32 + 1)),
                        }
                    }
                    (pc, st, runs)
                })
                .collect();
            v.sort_unstable();
            v
        },
        bounds: {
            let held = byte_bounds(&g, entries);
            let stores = g.vars.values().flatten().flat_map(|v| &v.acc);
            let mut b: Vec<(u16, u8, u8)> = stores
                .filter(|a| a.store)
                .filter_map(|a| a.addr.value())
                .filter_map(|a| held[a as usize].map(|h| (a as u16, h.lo as u8, h.hi as u8)))
                .collect();
            b.sort_unstable();
            b.dedup();
            b
        },
        sets: {
            let mut s: Vec<(u16, Vec<u8>)> = g
                .closed
                .ones()
                .filter_map(|a| {
                    let vs = g.vars.get(&(a as u16))?;
                    Some((a as u16, vs.iter().map(|v| v.op).collect()))
                })
                .collect();
            // Every other closed cell (an operand byte) holds one of its values.
            let ops: FxHashSet<u16> = s.iter().map(|e| e.0).collect();
            for a in g
                .closed
                .ones()
                .map(|a| a as u16)
                .filter(|a| !ops.contains(a))
            {
                if let Some(v) = g.vals.get(&a) {
                    s.push((a, v.iter().copied().collect()));
                }
            }
            s.sort_unstable();
            s
        },
        jumps: {
            let mut j: Vec<(u16, Vec<u16>)> =
                g.stat.iter().map(|(&pc, t)| (pc, t.clone())).collect();
            j.sort_unstable();
            j
        },
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
            let p = lift_with(&j.img, &j.entries, &j.volatile, &j.facts, opts.clone());
            tick();
            p
        })
        .collect()
}
