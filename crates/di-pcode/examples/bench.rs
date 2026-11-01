//! Decode+instantiate throughput and interpreter instructions/s over a 64 KiB image.
//!
//! `cargo run --release -p di-pcode --example bench -- [WORKLOAD] [STEPS] [PC]`. `WORKLOAD`
//! is `sort` (default: `tests/fixtures/bsort.hex` from $1040), `random` (a seeded random
//! image with every JAM opcode replaced by NOP) or a 64 KiB image file. A JAM (e.g. one the
//! program stores) is stepped over.

use std::time::Instant;

use di_pcode::{decode_range, Cpu, CtrlKind, Template, MAX_OPS};

fn random_image(mut s: u64) -> Box<[u8; 0x10000]> {
    let mut m = Box::new([0u8; 0x10000]);
    for b in m.iter_mut() {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        let v = (s >> 32) as u8;
        *b = if Template::of(v).ctrl == CtrlKind::Jam {
            0xea
        } else {
            v
        };
    }
    m
}

fn hex_image(src: &str) -> Box<[u8; 0x10000]> {
    let mut m = Box::new([0u8; 0x10000]);
    for line in src.lines() {
        let line = line.split(';').next().unwrap();
        if let Some((addr, bytes)) = line.split_once(':') {
            let a = usize::from_str_radix(addr.trim(), 16).expect("hex address");
            for (a, b) in (a..).zip(bytes.split_whitespace()) {
                m[a] = u8::from_str_radix(b, 16).expect("hex byte");
            }
        }
    }
    m
}

fn progress(label: &str, done: u64, total: u64, t0: Instant, unit: &str) {
    let dt = t0.elapsed().as_secs_f64();
    println!(
        "{label}: {:3}% {done}/{total} {unit} {:.1}s {:.2} M{unit}/s",
        done * 100 / total,
        dt,
        done as f64 / dt / 1e6
    );
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let workload = args.get(1).map_or("sort", |s| s.as_str());
    let (mut mem, entry) = match workload {
        "sort" => (
            hex_image(include_str!("../../../tests/fixtures/bsort.hex")),
            0x1040,
        ),
        "random" => (random_image(0x9e37_79b9_7f4a_7c15), 0x1000),
        p => {
            let img = std::fs::read(p).expect("read IMAGE");
            let img = <[u8; 0x10000]>::try_from(img.as_slice()).expect("image must be 64 KiB");
            (Box::new(img), 0x1000)
        }
    };
    let steps: u64 = args
        .get(2)
        .map_or(100_000_000, |s| s.parse().expect("STEPS"));
    let pc = args.get(3).map_or(entry, |s| {
        u16::from_str_radix(s.trim_start_matches("0x"), 16).expect("PC in hex")
    });

    let rounds = 200u64;
    let mut arena = Vec::with_capacity(0x10000 * MAX_OPS);
    let mut insns = Vec::with_capacity(0x10000);
    let t0 = Instant::now();
    let mut decoded = 0;
    for i in 1..=rounds {
        arena.clear();
        insns.clear();
        decode_range(&mem, 0, 0x10000, &mut arena, &mut insns);
        decoded += insns.len() as u64;
        if i % (rounds / 10) == 0 {
            progress("decode", decoded, decoded * rounds / i, t0, "insn");
        }
    }
    println!(
        "decode: {:.1} MB/s of code, {} ops/image",
        (rounds * 0x10000) as f64 / t0.elapsed().as_secs_f64() / 1e6,
        arena.len()
    );

    let mut cpu = Cpu::new();
    cpu.pc = pc;
    let chunk = (steps / 10).max(1);
    let t0 = Instant::now();
    let (mut done, mut jams, mut mark) = (0, 0, chunk);
    while done < steps {
        let (n, jam) = cpu.run(&mut *mem, mark - done);
        done += n;
        if let Some(j) = jam {
            jams += 1;
            cpu.pc = j.pc.wrapping_add(1);
        }
        if done == mark {
            progress("interp", done, steps, t0, "insn");
            mark = (mark + chunk).min(steps);
        }
    }
    let dt = t0.elapsed().as_secs_f64();
    println!(
        "interp: {done} instructions ({jams} JAMs skipped), {} cycles, {:.2} Minsn/s, {:.2} Mcycles/s, {:.2} ns/insn",
        cpu.cycles,
        done as f64 / dt / 1e6,
        cpu.cycles as f64 / dt / 1e6,
        dt * 1e9 / done as f64
    );
}
