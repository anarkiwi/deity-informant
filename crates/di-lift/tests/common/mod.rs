//! Differential harness: the lifted program against the reference `Cpu`.

use di_lift::{
    lift_banked, Back, Backend, BankMap, Banked, Entry, Exec, Facts, Opts, Program, Runner,
};
use di_pcode::{reg as R, Bus, Cpu, Halt};
use std::collections::{BTreeMap, BTreeSet};

/// Flat memory whose volatile range reads a function of the cycle count and access count and
/// logs accesses; without `clock` accesses carry no cycle. With `dev` the device accesses
/// (`read`/`write`) reach that register file and `ram_*` the RAM.
#[derive(Clone)]
pub struct TBus {
    pub mem: Box<[u8; 0x10000]>,
    pub dev: Option<Box<[u8; 0x10000]>>,
    pub vol: (u16, u16),
    pub now: u64,
    pub clock: bool,
    pub n: u64,
    pub log: Vec<(u16, u8, u64, bool)>,
    /// Observed code facts.
    pub writes: BTreeMap<u16, BTreeSet<u8>>,
    /// The lowest and highest page-1 address accessed since it was cleared.
    pub page1: Option<(u16, u16)>,
    /// Offer [`Bus::flat`] memory (bypassing `writes` and `page1` for flat accesses).
    pub flat: bool,
}

impl TBus {
    pub fn new(img: &[u8; 0x10000], vol: (u16, u16)) -> Self {
        TBus {
            mem: Box::new(*img),
            dev: None,
            vol,
            now: 0,
            clock: true,
            n: 0,
            log: Vec::new(),
            writes: BTreeMap::new(),
            page1: None,
            flat: false,
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
            self.dev.as_ref().unwrap_or(&self.mem)[a as usize]
        }
    }
    fn write(&mut self, a: u16, v: u8) {
        self.touch(a);
        if self.volatile(a) {
            self.n += 1;
            self.log.push((a, v, self.now, true));
        }
        self.writes.entry(a).or_default().insert(v);
        self.dev.as_mut().unwrap_or(&mut self.mem)[a as usize] = v;
    }
    fn ram_read(&mut self, a: u16) -> u8 {
        self.touch(a);
        self.mem[a as usize]
    }
    fn ram_write(&mut self, a: u16, v: u8) {
        self.touch(a);
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
    fn flat(&mut self) -> Option<(*mut u8, *mut u8)> {
        let ram = self.mem.as_mut_ptr();
        self.flat
            .then(|| (self.dev.as_mut().map_or(ram, |d| d.as_mut_ptr()), ram))
    }
}

/// Whether `op`'s page-1 accesses are all pushes and pulls.
fn stack_only(op: u8) -> bool {
    matches!(op, 0x48 | 0x08 | 0x68 | 0x28 | 0x20 | 0x60 | 0x40 | 0x00)
}

/// `run_to_rts` on the reference interpreter.
pub fn ref_call(cpu: &mut Cpu, bus: &mut TBus, pc: u16, acc: u8, max: u64) -> Halt {
    ref_call_in(cpu, bus, pc, acc, max, None)
}

/// [`ref_call`] over memory seen through `bank`.
pub fn ref_call_in(
    cpu: &mut Cpu,
    bus: &mut TBus,
    pc: u16,
    acc: u8,
    max: u64,
    bank: Option<&BankMap>,
) -> Halt {
    let start = cpu.r(R::SP);
    for v in [0u8, 1] {
        let sp = cpu.r(R::SP);
        bus.ram_write(0x100 | sp as u16, v);
        cpu.set_r(R::SP, sp.wrapping_sub(1));
    }
    cpu.set_r(R::A, acc);
    cpu.pc = pc;
    let end = cpu.cycles + max + 1;
    match bank {
        Some(m) => cpu.run_clocked(&mut Banked::new(m, bus), start as i32 - 1, end, None),
        None => cpu.run_clocked(bus, start as i32 - 1, end, None),
    }
}

/// A bus for `img`, banked (devices apart from RAM) when `bank` is given.
pub fn tbus(img: &[u8; 0x10000], vol: (u16, u16), bank: Option<&BankMap>) -> TBus {
    let mut b = TBus::new(img, vol);
    b.dev = bank.map(|_| Box::new(*img));
    b
}

/// Facts from running `calls` on the reference: starts, transfers, stored values, page-1
/// sites, heights and entry SPs.
pub fn facts(img: &[u8; 0x10000], calls: &[(u16, u8)], max: u64) -> Facts {
    facts_in(img, calls, max, None)
}

/// [`facts`] over memory seen through `bank`, with the configurations and entry ports.
pub fn facts_in(
    img: &[u8; 0x10000],
    calls: &[(u16, u8)],
    max: u64,
    bank: Option<&BankMap>,
) -> Facts {
    let mut bus = tbus(img, (0, 0), bank);
    let mut banks: BTreeMap<u16, BTreeSet<u8>> = BTreeMap::new();
    let mut entry_port: BTreeMap<u16, BTreeSet<(u8, u8)>> = BTreeMap::new();
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
        if let Some(m) = bank {
            let r = (bus.mem[m.dir as usize], bus.mem[m.data as usize]);
            entry_port.entry(pc).or_default().insert(r);
        }
        for v in [0u8, 1] {
            let sp = cpu.r(R::SP);
            bus.ram_write(0x100 | sp as u16, v);
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
            let op = match bank {
                Some(m) => {
                    let (d, v) = (bus.mem[m.dir as usize], bus.mem[m.data as usize]);
                    banks.entry(p).or_default().insert(m.config(d, v));
                    Banked::new(m, &mut bus).fetch(p)
                }
                None => bus.mem[p as usize],
            };
            bus.page1 = None;
            let stepped = match bank {
                Some(m) => cpu.step(&mut Banked::new(m, &mut bus)),
                None => cpu.step(&mut bus),
            };
            if stepped.is_err() {
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
        banks: banks
            .into_iter()
            .map(|(p, s)| (p, s.into_iter().collect()))
            .collect(),
        entry_port: entry_port
            .into_iter()
            .map(|(p, s)| (p, s.into_iter().collect()))
            .collect(),
    }
}

/// Run `calls` on both and compare everything observable; the total deopts. Without cycles a
/// call the reference does not return from in time is compared by its halt only, and ends the
/// run (the lift stops at a later instruction boundary).
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
    assert_eq!(x.jit().is_some(), !prog.cycles, "native code");
    let mut runs = [Backend::Interp, Backend::Jit].map(|backend| {
        let mut r = Runner::new(&x);
        r.backend = backend;
        r
    });
    let bank = prog.bank.as_ref();
    let mut rb = tbus(img, vol, bank);
    rb.clock = prog.cycles;
    let mut lbs = [false, true].map(|flat| {
        let mut b = tbus(img, vol, bank);
        b.flat = flat;
        b
    });
    let mut rc = Cpu::new();
    rc.set_r(R::SP, sp);
    let mut lcs = [rc, rc];
    for (n, &(e, acc)) in calls.iter().enumerate() {
        let h1 = ref_call_in(&mut rc, &mut rb, prog.entries[e].0, acc, max, bank);
        let mut stop = false;
        for ((run, lb), lc) in runs.iter_mut().zip(&mut lbs).zip(&mut lcs) {
            let o = run.call(e, acc, lc, lb, max);
            assert_eq!(h1, o.halt, "halt, call {n}, {:?}", run.backend);
            if !prog.cycles && h1 == Halt::Cycles {
                stop = true;
                continue;
            }
            if !prog.cycles {
                lc.cycles = rc.cycles;
            }
            assert_eq!(rc, *lc, "state, call {n}, {:?}", run.backend);
            assert_eq!(
                rb.log, lb.log,
                "volatile stream, call {n}, {:?}",
                run.backend
            );
            let sp = rc.r(R::SP) as usize;
            for a in 0..0x10000 {
                if !(0x100..=0x100 + sp).contains(&a) {
                    assert_eq!(
                        rb.mem[a], lb.mem[a],
                        "memory {a:04X}, call {n}, {:?}",
                        run.backend
                    );
                }
            }
        }
        assert_eq!(runs[0].deopts, runs[1].deopts, "deopts, call {n}");
        assert_eq!(runs[0].invalid, runs[1].invalid, "invalidation, call {n}");
        if stop {
            break;
        }
    }
    runs[0].deopts.values().sum()
}

pub fn entries(pcs: &[u16]) -> Vec<Entry> {
    pcs.iter().map(|&pc| Entry { pc, regs: vec![] }).collect()
}

/// Lift with facts from the reference and check `calls` with zero deopts, optimized and as
/// emitted, with and without cycles; the optimized functional lift.
pub fn check(img: &[u8; 0x10000], pcs: &[u16], calls: &[(usize, u8)], vol: (u16, u16)) -> Program {
    check_in(img, pcs, calls, vol, None)
}

/// [`check`] over memory seen through `bank`.
pub fn check_in(
    img: &[u8; 0x10000],
    pcs: &[u16],
    calls: &[(usize, u8)],
    vol: (u16, u16),
    bank: Option<&BankMap>,
) -> Program {
    let fc: Vec<(u16, u8)> = calls.iter().map(|&(e, a)| (pcs[e], a)).collect();
    let f = facts_in(img, &fc, 1_000_000, bank);
    let es = entries(pcs);
    let v = [(vol.0 as u32, vol.1 as u32)];
    let mut out = None;
    for cycles in [true, false] {
        for optimize in [false, true] {
            let prog = lift_banked(
                img,
                &es,
                &v,
                &f,
                Opts {
                    optimize,
                    cycles,
                    ..Opts::default()
                },
                bank,
            );
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

/// A C64-like bank map: port at `$00`/`$01`, configuration = the port's low three bits;
/// BASIC ($A000), CHARGEN ($D000, when I/O is out) and KERNAL ($E000) ROM windows as the PLA
/// maps them. Its synthetic ROM fills each window with a pattern, with `LDA #$42; RTS` at
/// `$E000`.
pub fn c64_like() -> BankMap {
    let configs = (0..8u8)
        .map(|bits| {
            let (lo, hi, charen) = (bits & 1 != 0, bits & 2 != 0, bits & 4 != 0);
            std::array::from_fn(|p| match p {
                0xa0..=0xbf if lo && hi => Back::Rom,
                0xd0..=0xdf if charen && (lo || hi) => Back::Io,
                0xd0..=0xdf if lo || hi => Back::Rom,
                0xe0..=0xff if hi => Back::Rom,
                _ => Back::Ram,
            })
        })
        .collect();
    let mut rom = Box::new([0u8; 0x10000]);
    for a in 0xa000..0x10000 {
        rom[a] = (a >> 8) as u8 ^ 0x5a;
    }
    rom[0xe000..0xe003].copy_from_slice(&[0xa9, 0x42, 0x60]);
    BankMap {
        dir: 0,
        data: 1,
        mask: 7,
        pull: 0x1f,
        lut: (0..8).collect(),
        configs,
        rom,
    }
}

/// `parts` as an image with the C64 port's power-on registers.
pub fn c64_image(parts: &[(u16, &[u8])]) -> Box<[u8; 0x10000]> {
    let mut m = image(parts);
    m[0] = 0x2f;
    m[1] = 0x37;
    m
}
