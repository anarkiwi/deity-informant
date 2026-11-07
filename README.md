# deity-informant

NMOS 6510 toolkit: a 6510 -> raw-P-Code lifter + pure-Python P-Code VM (all 105 documented illegal opcodes are first-class), and a `6510` Ghidra/pypcode SLEIGH processor module. The `jennings` package supplies the opcode table, assembler and disassembler, and its py65-compatible `MPU` executes on this lifter + VM.

The C64 machine model (power-on RAM, interrupt-vector discovery, ROM-free IRQ dispatch) in [anarkiwi/vagrant-moses](https://github.com/anarkiwi/vagrant-moses).

## C lifting

`deity_informant.cgen` translates 6502/6510 machine code to C99: a static, song-independent
lift (no run of the program needed) to goto-minimal structured C, with a `goto` only at an
edge that has no structured form. The `.c` holds the code alone and the `.h` its data, so the
same player with other data, or loaded elsewhere, gives a byte-identical `.c`. Routines a
player reaches by fall-through or `JSR` are functions, unrolled per-voice copies (whole blocks
alike up to operands) one function of the copy's index with its per-copy values in constant
tables, and a voice loop's index bounds the accesses of the routine it calls. An init the host
calls once, first, is lifted as code like the others: the operands it patches are cells read
through as pointer variables, its copies copy loops and its probes I/O reads, nothing of its
run decided at lift time. A read through a
pointer whose bases the analysis bounds (split tables a song byte indexes, constants, an
offset in `Y`, the record lengths it advances by) is one bounds-checked read of the span
holding them. Every output is
verified by replaying its I/O writes against the reference interpreter. See
[docs/cgen.md](docs/cgen.md).

## Components

- `deity_informant/lifter.py` — `lift(mem, pc)` -> `{"ops", "len", "cyc", "pen", "ctrl", "prov", "stk"}` raw P-Code; `CYCLETIME`/`EXTRACYCLES` tables.
- `deity_informant/vm.py` — `PcodeVM` interpreter over a flat 64 KiB image (SID/VIC/CIA volatile IO modeled); `run_sub`/`run_irq`/`run_irq_driven` drivers (handler fixed, or resolved per entry via `vector(addr)`). `PcodeVM.dummies` (default set) makes the throw-away reads and a read-modify-write's dummy write; a machine that places them on its own cycles (the jennings MPU under vagrant-moses' Python backend) clears it, so the bus sees each once.
- `deity_informant/cli.py` — `deity-informant` console script.
- `deity_informant/sleigh/` — SLEIGH module (`languages/6510.slaspec` = stock 6502 + `6510_illegal.sinc`), `smc.py` (SMC context constructors), `build.py`, and `pypcode_context()` (a loaded pypcode `Context` for the 6510).
- `ghidra/6510/headless/` — headless Ghidra integration tests (LAX/ISC decode, layout export), run via `Dockerfile.ghidra`.
- `deity_informant/hello_world.py` — 33-byte C64 program using `LAX`/`ISC` + self-modifying code; the fixture for the VM and Ghidra tests.
- `crates/` — Rust P-Code IR templated from `lift()` and a reference interpreter, shipped in wheels as `deity_informant._native`; see [docs/native.md](docs/native.md).
- `deity_informant/wholeprog.py`, `deity_informant/trace.py` — whole-program lifting of entry points to optimized, executable, exact P-Code (self-modifying code as memory reads, balanced calls as `CALL`/`RETURN`, the hardware stack as registers, optional cycle counting, guarded deopts), run as Cranelift native code or interpreted, and the run-time facts it uses; see [docs/wholeprog.md](docs/wholeprog.md).
- `deity_informant/layout.py`, `tools/layout.py` — memory layout recovery (words, pointers, address tables, record and parallel arrays) from carry chains, pointer arithmetic and effective-address facts; see [docs/layout.md](docs/layout.md).
- `tools/pcode2c.py`, `tools/pcode2mlir.py`, `tools/structure.py`, `tools/cslice.py`, `tools/cplay.py` — a lift as C, as MLIR and as structured C (MLIR `lift-cf-to-scf` through EmitC), Frama-C slicing, and replay of the C against the reference; see [docs/structured.md](docs/structured.md).
- `deity_informant/cgen/`, `tools/cgenplay.py`, `tools/cclones.py`, `tools/csize.py` — entry points of a program as C99 with a goto only where an edge has no structured form (static lift with tail calls and unrolled copies as one function indexing per-copy tables, MLIR `inline`/`mem2reg`, dominator-tree structuring with short-circuit conditions, provenance of each construct, a compact printer): a `.c` of the code alone and a `.h` of its data regions, identical across data sets and origins, checked against the reference, its repeated code measured and its compiled size attributed; see [docs/cgen.md](docs/cgen.md).
- `tools/ghidra_layout.py`, `ghidra/6510/headless/ApplyLayout.java` — headless Ghidra export of layout maps (struct types, typed data, labels, data references) with a read-back check; see [docs/layout.md](docs/layout.md#ghidra-export).
- `tools/wplift.py` — lift an image file whole and check it against the reference interpreter.
- `deity_informant/c64.py`, `tools/wpbank.py` — the C64 host of bank-aware lifting: the 6510 port/PLA bank map (ROM-less or verified real ROMs), facts and per-call reference records from vagrant-moses' cycle-exact machine, and a check of bank-aware play lifts of HVSC tunes against it.
- `tools/wpcorpus.py` — run every HVSC song once for its full length: flat or bank-aware lift, check, scenario cells, IRQ paths and speed per song, resumable; see [docs/corpus.md](docs/corpus.md).
- `tools/sidcmp.py` — compare the machine's SID register changes for interrupt-driven songs, booted as sidplayfp does, with sidplayfp's (`anarkiwi/sidtrace`); see [docs/corpus.md](docs/corpus.md#boot-environment).
- `tools/wpsurvey.py` — lift the play routines of a directory of PSID tunes and tabulate stack privacy, its blockers and (optionally) exactness.
- `deity_informant/smc/`, `tools/smcgen.py`, `tools/smclabel.py` — progressively generate every SMC scenario (writer, role, timing, mapping) for the lifter, label it with independent CPU (jennings) and machine (vagrant-moses) oracles, and drive lift tests against a strict-xfail expectation table; see [docs/smc-scenarios.md](docs/smc-scenarios.md).
- `tools/gen_pcode.py` — regenerates the native templates (`--check` verifies they are current).
- `tools/vtools.py`, `tools/sidid.py`, `tools/sidreloc.py`, `tools/gtruth.py` — layout-validation references on the `di-vtools` image: SIDId player names, sidreloc's address bytes, GoatTracker 2 ground-truth layouts from source; see [docs/validation.md](docs/validation.md).
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
python -m deity_informant.cgen IMAGE --org ADDR --entry PC:NAME --volatile LO-HI -o OUT.c   # C translation
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
maturin develop --release   # the coverage gate measures the native build
pytest tests/ -n auto --cov=deity_informant --cov-fail-under=85
```

## References

Illegal-opcode semantics and cycles: "No More Secrets — NMOS 6510 Unintended Opcodes" ([csdb.dk/release/?id=258111](https://csdb.dk/release/?id=258111)).
