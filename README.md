# deity-informant

NMOS 6510 toolkit: a 6510 -> raw-P-Code lifter + pure-Python P-Code VM (all 105 documented illegal opcodes are first-class), and a `6510` Ghidra/pypcode SLEIGH processor module. The `jennings` package supplies the opcode table, assembler and disassembler, and its py65-compatible `MPU` executes on this lifter + VM.

The C64 machine model (power-on RAM, interrupt-vector discovery, ROM-free IRQ dispatch) in [anarkiwi/vagrant-moses](https://github.com/anarkiwi/vagrant-moses).

## Components

- `deity_informant/lifter.py` — `lift(mem, pc)` -> `{"ops", "len", "cyc", "pen", "ctrl", "prov", "stk"}` raw P-Code; `CYCLETIME`/`EXTRACYCLES` tables.
- `deity_informant/vm.py` — `PcodeVM` interpreter over a flat 64 KiB image (SID/VIC/CIA volatile IO modeled); `run_sub`/`run_irq`/`run_irq_driven` drivers (handler fixed, or resolved per entry via `vector(addr)`).
- `deity_informant/cli.py` — `deity-informant` console script.
- `deity_informant/sleigh/` — SLEIGH module (`languages/6510.slaspec` = stock 6502 + `6510_illegal.sinc`), `smc.py` (SMC context constructors), `build.py`, and `pypcode_context()` (a loaded pypcode `Context` for the 6510).
- `ghidra/6510/headless/` — headless Ghidra integration test, run via `Dockerfile.ghidra`.
- `deity_informant/hello_world.py` — 33-byte C64 program using `LAX`/`ISC` + self-modifying code; the fixture for the VM and Ghidra tests.
- `crates/` — Rust P-Code IR templated from `lift()` and a reference interpreter, shipped in wheels as `deity_informant._native`; see [docs/native.md](docs/native.md).
- `deity_informant/wholeprog.py`, `deity_informant/trace.py` — whole-program lifting of entry points to optimized, executable, exact P-Code (self-modifying code as memory reads, balanced calls as `CALL`/`RETURN`, the hardware stack as registers, optional cycle counting, guarded deopts), run as Cranelift native code or interpreted, and the run-time facts it uses; see [docs/wholeprog.md](docs/wholeprog.md).
- `tools/wplift.py` — lift an image file whole and check it against the reference interpreter.
- `deity_informant/c64.py`, `tools/wpbank.py` — the C64 host of bank-aware lifting: the 6510 port/PLA bank map (ROM-less or verified real ROMs), facts and per-call reference records from vagrant-moses' cycle-exact machine, and a check of bank-aware play lifts of HVSC tunes against it.
- `tools/wpcorpus.py` — run every HVSC song once for its full length: flat or bank-aware lift, check, scenario cells, IRQ paths and speed per song, resumable; see [docs/corpus.md](docs/corpus.md).
- `tools/wpsurvey.py` — lift the play routines of a directory of PSID tunes and tabulate stack privacy, its blockers and (optionally) exactness.
- `deity_informant/smc/`, `tools/smcgen.py`, `tools/smclabel.py` — progressively generate every SMC scenario (writer, role, timing, mapping) for the lifter, label it with independent CPU (jennings) and machine (vagrant-moses) oracles, and drive lift tests against a strict-xfail expectation table; see [docs/smc-scenarios.md](docs/smc-scenarios.md).
- `tools/gen_pcode.py` — regenerates the native templates (`--check` verifies they are current).
- `tools/doclinks.py` — every `[text](target)` in `docs/` and `README.md` resolved: the file, and the `#anchor` against the target's own headings.

## Install

```bash
pip install deity-informant         # from PyPI; the SLEIGH sources ship as package data
pip install -e ".[dev]"             # from a checkout: + test/lint tooling, py65, pypcode
maturin develop --release           # + the native extension (Rust toolchain required)
```

## CLI

```bash
deity-informant disasm IMAGE [--org ADDR] [--start ADDR] [--count N]   # illegal-aware disassembly
deity-informant pcode  IMAGE --at ADDR [--org ADDR]                    # raw P-Code for one instruction
deity-informant run    IMAGE --init ADDR [--play ADDR --frames N]      # execute in PcodeVM, dump $D400.. grid
deity-informant emit-sleigh [-o DIR] [--magic 0xEE]                    # build/install the 6510 SLEIGH module
```

## Python API

```python
from deity_informant import lift, PcodeVM, run_sub
vm = PcodeVM(mem); run_sub(vm, entry, {}, lift)   # execute a subroutine to its RTS

from deity_informant import trace, wholeprog      # whole-program P-Code (native)
facts = trace.collect(mem, lambda s: [s.call(entry) for _ in range(100)])
prog = wholeprog.lift(mem, [entry], volatile=[(lo, hi)], facts=facts)
prog.functions, prog.stats, prog.call(bytearray(mem), 0)
```

## Ghidra

```bash
deity-informant emit-sleigh -o "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
```

Resolves the stock `6502.slaspec` + SLEIGH compiler from `$GHIDRA_INSTALL_DIR` or a `pypcode` install; the stock spec's `JSR`/`RTS` return address and `SBC` borrow are patched to the hardware's. Import a C64 image as Raw Binary, language `6510:LE:16:default`. `docker build -f Dockerfile.ghidra -t di-ghidra . && docker run --rm di-ghidra` runs the headless integration test.

## Tests

```bash
black --check deity_informant/ tests/ tools/ && pylint deity_informant/
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace
deity-informant emit-sleigh
pytest tests/ -n auto --cov=deity_informant --cov-fail-under=85
```

## References

Illegal-opcode semantics and cycles: "No More Secrets — NMOS 6510 Unintended Opcodes" ([csdb.dk/release/?id=258111](https://csdb.dk/release/?id=258111)).
