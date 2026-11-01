//! Differential harness: the lifted program against the reference `Cpu`.

use di_lift::{lift_with, Entry, Exec, Facts, Opts, Program, Runner};
use di_pcode::{reg as R, Bus, Cpu, Halt};
use std::collections::{BTreeMap, BTreeSet};

/// Flat memory whose volatile range reads a function of the cycle count and access count and
/// logs accesses; without `clock` accesses carry no cycle.
#[derive(Clone)]
pub struct TBus {
    pub mem: Box<[u8; 0x10000]>,
    pub vol: (u16, u16),
    pub now: u64,
    pub clock: bool,
    pub n: u64,
    pub log: Vec<(u16, u8, u64, bool)>,
    /// Observed code facts.
    pub writes: BTreeMap<u16, BTreeSet<u8>>,
    /// The lowest and highest page-1 address accessed since it was cleared.
    pub page1: Option<(u16, u16)>,
}

impl TBus {
    pub fn new(img: &[u8; 0x10000], vol: (u16, u16)) -> Self {
        TBus {
            mem: Box::new(*img),
            vol,
            now: 0,
            clock: true,
            n: 0,
            log: Vec::new(),
            writes: BTreeMap::new(),
            page1: None,
        }
    }
    fn touch(&mut self, a: u16) {
        if a >> 8 == 1 {
            let (lo, hi) = self.page1.unwrap_or((a, a));
            self.page1 = Some((lo.min(a), hi.max(a)));
        }
    }
    fn volatile(&self, a: u16) -> bool {
        (self.vol.0..self.vol.1).contains(&a)
    }
}

impl Bus for TBus {
    fn read(&mut self, a: u16) -> u8 {
        self.touch(a);
        if self.volatile(a) {
            self.n += 1;
            let v = (self.now ^ self.n) as u8 ^ a as u8;
            self.log.push((a, v, self.now, false));
            v
        } else {
            self.mem[a as usize]
        }
    }
    fn write(&mut self, a: u16, v: u8) {
        self.touch(a);
        if self.volatile(a) {
            self.n += 1;
            self.log.push((a, v, self.now, true));
        }
        self.writes.entry(a).or_default().insert(v);
        self.mem[a as usize] = v;
    }
    fn fetch(&mut self, a: u16) -> u8 {
        self.mem[a as usize]
    }
    fn now(&mut self, c: u64) {
        if self.clock {
            self.now = c;
        }
    }
}

/// Whether `op`'s page-1 accesses are all pushes and pulls.
fn stack_only(op: u8) -> bool {
    matches!(op, 0x48 | 0x08 | 0x68 | 0x28 | 0x20 | 0x60 | 0x40 | 0x00)
}

/// `run_to_rts` on the reference interpreter.
pub fn ref_call(cpu: &mut Cpu, bus: &mut TBus, pc: u16, acc: u8, max: u64) -> Halt {
    let start = cpu.r(R::SP);
    for v in [0u8, 1] {
        let sp = cpu.r(R::SP);
        bus.write(0x100 | sp as u16, v);
        cpu.set_r(R::SP, sp.wrapping_sub(1));
    }
    cpu.set_r(R::A, acc);
    cpu.pc = pc;
    let end = cpu.cycles + max + 1;
    cpu.run_clocked(bus, start as i32 - 1, end)
}

/// Facts from running `calls` on the reference: starts, transfers, stored values, page-1
/// sites, heights and entry SPs.
pub fn facts(img: &[u8; 0x10000], calls: &[(u16, u8)], max: u64) -> Facts {
    let mut bus = TBus::new(img, (0, 0));
    let mut cpu = Cpu::new();
    let mut starts = BTreeSet::new();
    let mut edges = BTreeSet::new();
    let mut page1: BTreeMap<u16, (u16, u16)> = BTreeMap::new();
    let mut heights: BTreeMap<u16, BTreeSet<i16>> = BTreeMap::new();
    let mut entry_sp: BTreeMap<u16, BTreeSet<u8>> = BTreeMap::new();
    for &(pc, acc) in calls {
        let start = cpu.r(R::SP);
        entry_sp
            .entry(pc)
            .or_default()
            .insert(start.wrapping_sub(2));
        for v in [0u8, 1] {
            let sp = cpu.r(R::SP);
            bus.write(0x100 | sp as u16, v);
            cpu.set_r(R::SP, sp.wrapping_sub(1));
        }
        cpu.set_r(R::A, acc);
        cpu.pc = pc;
        let end = cpu.cycles + max;
        while cpu.r(R::SP) < start && cpu.cycles < end {
            let p = cpu.pc;
            starts.insert(p);
            let h = cpu.r(R::SP) as i16 - start.wrapping_sub(2) as i16;
            heights.entry(p).or_default().insert(h);
            let op = bus.mem[p as usize];
            bus.page1 = None;
            if cpu.step(&mut bus).is_err() {
                break;
            }
            if let (Some((lo, hi)), false) = (bus.page1, stack_only(op)) {
                let r = page1.entry(p).or_insert((lo, hi));
                *r = (r.0.min(lo), r.1.max(hi));
            }
            edges.insert((p, cpu.pc));
        }
    }
    Facts {
        starts: starts.into_iter().collect(),
        edges: edges.into_iter().collect(),
        writes: bus
            .writes
            .into_iter()
            .map(|(a, v)| (a, v.into_iter().collect()))
            .collect(),
        page1: page1.into_iter().map(|(p, (lo, hi))| (p, lo, hi)).collect(),
        heights: heights
            .into_iter()
            .map(|(p, h)| (p, h.into_iter().collect()))
            .collect(),
        entry_sp: entry_sp
            .into_iter()
            .map(|(p, s)| (p, s.into_iter().collect()))
            .collect(),
    }
}

/// Run `calls` on both and compare everything observable; the total deopts.
pub fn diff(img: &[u8; 0x10000], prog: &Program, calls: &[(usize, u8)], vol: (u16, u16)) -> u64 {
    diff_capped(img, prog, calls, vol, 1_000_000)
}

/// [`diff`] with a cycle cap per call.
pub fn diff_capped(
    img: &[u8; 0x10000],
    prog: &Program,
    calls: &[(usize, u8)],
    vol: (u16, u16),
    max: u64,
) -> u64 {
    diff_at(img, prog, calls, vol, max, Cpu::new().r(R::SP))
}

/// [`diff_capped`] with both starting at SP `sp`.
pub fn diff_at(
    img: &[u8; 0x10000],
    prog: &Program,
    calls: &[(usize, u8)],
    vol: (u16, u16),
    max: u64,
    sp: u8,
) -> u64 {
    let x = Exec::new(prog);
    let mut run = Runner::new(&x);
    let (mut rb, mut lb) = (TBus::new(img, vol), TBus::new(img, vol));
    rb.clock = prog.cycles;
    let (mut rc, mut lc) = (Cpu::new(), Cpu::new());
    rc.set_r(R::SP, sp);
    lc.set_r(R::SP, sp);
    for (n, &(e, acc)) in calls.iter().enumerate() {
        let h1 = ref_call(&mut rc, &mut rb, prog.entries[e].0, acc, max);
        let o = run.call(e, acc, &mut lc, &mut lb, max);
        assert_eq!(h1, o.halt, "halt, call {n}");
        if !prog.cycles {
            lc.cycles = rc.cycles;
        }
        assert_eq!(rc, lc, "state, call {n}");
        assert_eq!(rb.log, lb.log, "volatile stream, call {n}");
        let sp = rc.r(R::SP) as usize;
        for a in 0..0x10000 {
            if !(0x100..=0x100 + sp).contains(&a) {
                assert_eq!(rb.mem[a], lb.mem[a], "memory {a:04X}, call {n}");
            }
        }
    }
    run.deopts.values().sum()
}

pub fn entries(pcs: &[u16]) -> Vec<Entry> {
    pcs.iter().map(|&pc| Entry { pc, regs: vec![] }).collect()
}

/// Lift with facts from the reference and check `calls` with zero deopts, optimized and as
/// emitted, with and without cycles; the optimized functional lift.
pub fn check(img: &[u8; 0x10000], pcs: &[u16], calls: &[(usize, u8)], vol: (u16, u16)) -> Program {
    let fc: Vec<(u16, u8)> = calls.iter().map(|&(e, a)| (pcs[e], a)).collect();
    let f = facts(img, &fc, 1_000_000);
    let es = entries(pcs);
    let v = [(vol.0 as u32, vol.1 as u32)];
    let mut out = None;
    for cycles in [true, false] {
        for optimize in [false, true] {
            let prog = lift_with(img, &es, &v, &f, Opts { optimize, cycles });
            assert_eq!(
                diff(img, &prog, calls, vol),
                0,
                "deopts {cycles} {optimize}"
            );
            if optimize {
                assert!(prog.stats.ops < prog.stats.emitted_ops);
            }
            out = Some(prog);
        }
    }
    out.unwrap()
}

pub fn image(parts: &[(u16, &[u8])]) -> Box<[u8; 0x10000]> {
    let mut m = Box::new([0u8; 0x10000]);
    for (a, b) in parts {
        m[*a as usize..*a as usize + b.len()].copy_from_slice(b);
    }
    m
}
