//! Differential harness: the lifted program against the reference `Cpu`.

use di_lift::{lift, lift_with, Entry, Exec, Facts, Program, Runner};
use di_pcode::{reg as R, Bus, Cpu, Halt};
use std::collections::{BTreeMap, BTreeSet};

/// Flat memory whose volatile range reads a function of the cycle count and logs accesses.
#[derive(Clone)]
pub struct TBus {
    pub mem: Box<[u8; 0x10000]>,
    pub vol: (u16, u16),
    pub now: u64,
    pub log: Vec<(u16, u8, u64, bool)>,
    /// Observed code facts.
    pub writes: BTreeMap<u16, BTreeSet<u8>>,
}

impl TBus {
    pub fn new(img: &[u8; 0x10000], vol: (u16, u16)) -> Self {
        TBus {
            mem: Box::new(*img),
            vol,
            now: 0,
            log: Vec::new(),
            writes: BTreeMap::new(),
        }
    }
    fn volatile(&self, a: u16) -> bool {
        (self.vol.0..self.vol.1).contains(&a)
    }
}

impl Bus for TBus {
    fn read(&mut self, a: u16) -> u8 {
        if self.volatile(a) {
            let v = (self.now as u8) ^ (a as u8);
            self.log.push((a, v, self.now, false));
            v
        } else {
            self.mem[a as usize]
        }
    }
    fn write(&mut self, a: u16, v: u8) {
        if self.volatile(a) {
            self.log.push((a, v, self.now, true));
        }
        self.writes.entry(a).or_default().insert(v);
        self.mem[a as usize] = v;
    }
    fn fetch(&mut self, a: u16) -> u8 {
        self.mem[a as usize]
    }
    fn now(&mut self, c: u64) {
        self.now = c;
    }
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

/// Facts from running `calls` on the reference: starts, transfers, writes to code bytes.
pub fn facts(img: &[u8; 0x10000], calls: &[(u16, u8)], max: u64) -> Facts {
    let mut bus = TBus::new(img, (0, 0));
    let mut cpu = Cpu::new();
    let mut starts = BTreeSet::new();
    let mut edges = BTreeSet::new();
    for &(pc, acc) in calls {
        let start = cpu.r(R::SP);
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
            if cpu.step(&mut bus).is_err() {
                break;
            }
            edges.insert((p, cpu.pc));
        }
    }
    let code: BTreeSet<u16> = starts
        .iter()
        .flat_map(|&p| [p, p.wrapping_add(1), p.wrapping_add(2)])
        .collect();
    Facts {
        starts: starts.into_iter().collect(),
        edges: edges.into_iter().collect(),
        writes: bus
            .writes
            .into_iter()
            .filter(|(a, _)| code.contains(a))
            .map(|(a, v)| (a, v.into_iter().collect()))
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
    let x = Exec::new(prog);
    let mut run = Runner::new(&x);
    let (mut rb, mut lb) = (TBus::new(img, vol), TBus::new(img, vol));
    let (mut rc, mut lc) = (Cpu::new(), Cpu::new());
    for (n, &(e, acc)) in calls.iter().enumerate() {
        let h1 = ref_call(&mut rc, &mut rb, prog.entries[e].0, acc, max);
        let o = run.call(e, acc, &mut lc, &mut lb, max);
        assert_eq!(h1, o.halt, "halt, call {n}");
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

/// Lift with facts from the reference and check `calls` with zero deopts, optimized and as
/// emitted.
pub fn check(
    img: &[u8; 0x10000],
    entries: &[u16],
    calls: &[(usize, u8)],
    vol: (u16, u16),
) -> Program {
    let fc: Vec<(u16, u8)> = calls.iter().map(|&(e, a)| (entries[e], a)).collect();
    let f = facts(img, &fc, 1_000_000);
    let es: Vec<Entry> = entries
        .iter()
        .map(|&pc| Entry { pc, regs: vec![] })
        .collect();
    let v = [(vol.0 as u32, vol.1 as u32)];
    let raw = lift_with(img, &es, &v, &f, false);
    assert_eq!(diff(img, &raw, calls, vol), 0, "deopts (emitted)");
    let prog = lift(img, &es, &v, &f);
    assert_eq!(diff(img, &prog, calls, vol), 0, "deopts");
    assert!(prog.stats.ops < prog.stats.emitted_ops);
    prog
}

pub fn image(parts: &[(u16, &[u8])]) -> Box<[u8; 0x10000]> {
    let mut m = Box::new([0u8; 0x10000]);
    for (a, b) in parts {
        m[*a as usize..*a as usize + b.len()].copy_from_slice(b);
    }
    m
}
