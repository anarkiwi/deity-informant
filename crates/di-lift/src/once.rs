//! The run of an entry called once that no input decides.
//!
//! An entry's inputs are its registers but those declared one value, the bytes it starts with
//! that are not the program's (outside `loaded`, the host init's variables, the stack page
//! above its SP) and every volatile read. The entry is run on the reference interpreter from
//! the image while the interval interpreter ([`absint_known`]) follows each instruction with
//! every input unknown: a register or byte it computes exactly is a function of the image
//! alone. When every control transfer, every stack pointer and every store address of the run
//! is exact up to the entry's return, the run is the only one the entry makes, whatever its
//! inputs: every instruction it runs reads, on every run, the operand bytes it read there.

use crate::analysis::{absint_known, Access, Entry, Iv};
use di_pcode::{reg, Cpu, Template};
use fixedbitset::FixedBitSet;
use rustc_hash::FxHashMap;

/// The opcode and operand bytes each instruction of the run executed with, when they were the
/// same image-decided bytes at each of its executions.
pub type Ran = FxHashMap<u16, [u8; 3]>;

/// The instructions the run sets no bound on: at most this many before it returns.
pub const STEPS: u64 = 1 << 22;

/// The flag a conditional branch of opcode `op` tests.
fn branch_flag(op: u8) -> Option<usize> {
    match op {
        0x10 | 0x30 => Some(reg::N),
        0x50 | 0x70 => Some(reg::V),
        0x90 | 0xb0 => Some(reg::C),
        0xd0 | 0xf0 => Some(reg::Z),
        _ => None,
    }
}

/// [`Ran`] of `entry`, called at `sp` on `img` once, where no input decides its run: `input`
/// the bytes it starts with that are inputs. `None` where an input decides a transfer, the
/// stack pointer or a store's address, or it does not return within [`STEPS`].
pub fn run(img: &[u8; 0x10000], entry: &Entry, sp: u8, input: &FixedBitSet) -> Option<Ran> {
    let mut mem = Box::new(*img);
    let mut unknown = input.clone();
    unknown.grow(0x10000);
    unknown.insert_range(0x101 + sp as usize..0x200);
    let mut env = entry.env();
    env[reg::SP] = Iv::exact(sp as u32);
    let mut cpu = Cpu::new();
    for (r, iv) in env.iter().enumerate() {
        cpu.set_r(r, iv.value().unwrap_or(0) as u8);
    }
    cpu.pc = entry.pc;
    let mut seen: FxHashMap<u16, Option<[u8; 3]>> = FxHashMap::default();
    let mut acc: Vec<Access> = Vec::new();
    let mut steps = 0;
    loop {
        steps += 1;
        if steps > STEPS {
            return None;
        }
        let pc = cpu.pc;
        let at = |a: u16| (!unknown.contains(a as usize)).then(|| mem[a as usize]);
        let op = at(pc)?;
        let t = Template::of(op);
        let b: [Option<u8>; 2] = std::array::from_fn(|i| {
            ((i + 1) < t.len as usize)
                .then(|| at(pc.wrapping_add(i as u16 + 1)))
                .flatten()
        });
        let ops = [op, b[0].unwrap_or(0), b[1].unwrap_or(0)];
        let whole = (0..t.len as usize - 1).all(|i| b[i].is_some());
        let s = env[reg::SP].value()? as u16;
        let stacked = |n: u16| (1..=n).all(|i| at(0x100 | (s + i) & 0xff).is_some());
        let decided = match op {
            0x60 if s == sp as u16 => break,
            0x4c | 0x20 => whole,
            0x6c => {
                whole && {
                    let p = u16::from_le_bytes([ops[1], ops[2]]);
                    at(p).is_some() && at(p & 0xff00 | p.wrapping_add(1) & 0xff).is_some()
                }
            }
            0x60 => stacked(2),
            0x40 => stacked(3),
            0x00 => false,
            _ => branch_flag(op).is_none_or(|f| env[f].value().is_some()),
        };
        if !decided {
            return None;
        }
        let e = seen.entry(pc).or_insert(whole.then_some(ops));
        if *e != Some(ops) || !whole {
            *e = None;
        }
        acc.clear();
        let known = |a: u32| at(a as u16);
        absint_known(t.ops(), b, &mut env, &mut acc, &known);
        cpu.step(&mut *mem).ok()?;
        // The stack pointer a transfer moves, the status `RTI` pulls (from decided bytes).
        if matches!(op, 0x20 | 0x60 | 0x40) {
            env[reg::SP] = Iv::exact(cpu.r(reg::SP) as u32);
        }
        if op == 0x20 {
            unknown.set(0x100 | s as usize, false);
            unknown.set(0x100 | s.wrapping_sub(1) as usize & 0xff, false);
        }
        if op == 0x40 {
            for (f, _) in reg::STATUS_BITS {
                env[f] = Iv::exact(cpu.r(f) as u32);
            }
        }
        for x in acc.iter().filter(|x| x.store) {
            let a = x.addr.value()? as usize & 0xffff;
            unknown.set(a, x.val.value().is_none());
        }
        let stored = acc
            .iter()
            .filter(|x| x.store)
            .map(|x| x.addr.lo as usize & 0xffff);
        let last = |a: usize| {
            acc.iter()
                .rev()
                .find(|x| x.store && x.addr.lo as usize & 0xffff == a)
        };
        if stored.clone().any(|a| {
            last(a)
                .and_then(|x| x.val.value())
                .is_some_and(|v| v as u8 != mem[a])
        }) || (0..16).any(|r| env[r].value().is_some_and(|v| v as u8 != cpu.r(r)))
        {
            return None;
        }
    }
    Some(
        seen.into_iter()
            .filter_map(|(pc, o)| Some((pc, o?)))
            .collect(),
    )
}
