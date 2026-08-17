# deity-informant

NMOS 6510 toolkit: a 6510 -> raw-P-Code lifter + pure-Python P-Code VM (all 105 documented illegal opcodes are first-class), and a `6510` Ghidra/pypcode SLEIGH processor module. The `jennings` package supplies the opcode table, assembler and disassembler, and its py65-compatible `MPU` executes on this lifter + VM.

## Components

- `deity_informant/lifter.py` — `lift(mem, pc)` -> `{"ops", "len", "cyc", "pen", "ctrl"}` raw P-Code; `CYCLETIME`/`EXTRACYCLES` tables.
- `deity_informant/vm.py` — `PcodeVM` interpreter over a flat 64 KiB image (SID/VIC/CIA volatile IO modeled); `run_sub`/`run_irq`/`run_irq_driven` drivers.
- `deity_informant/c64.py` — power-on RAM, PSID/RSID loader, IRQ vector discovery, ROM-free IRQ dispatch stubs.
- `deity_informant/tuneprog/` — tuneprog decompiler. Front end: `machine.py` (S0 image, entries/cadence, init runner, 6510 port + CIA models), `trace.py`/`tracedata.py` (S1 op-level tracer, logs, pinned inputs, state hashes, resume), `lift.py` (S2a residualised lift: SMC cells become loads), `cfg.py` (S2b procedures, clone-per-entry, computed switches), `regions.py` (S3 storage typing). Program: `ir.py` (executable IR + reference interpreter), `build.py` (front end -> IR), `irwalk.py` (IR traversal), `ssa.py`/`idioms.py` (S4 SSA, DCE, propagation, peepholes), `jumptab.py` (S2 static closure of a patched `JMP`), `emit.py` (S7 Python codegen + certificate), `verify.py` (S8 per-call differential verification, periodicity, chunked). Presentation over the certified program, which it never edits: `structure.py` (S5 loops/if/switch/`for`/phase), `frame.py` (S6 the machine stack as procedure frames), `recover.py` (S6 struct views, roles, names), `tails.py` (S6 shared tails as procedures), `printer.py` (S7 pseudocode). `pipeline.py` drives every stage into one output directory.
- `deity_informant/cli.py` — `deity-informant` console script, including `tuneprog` (the pipeline).
- `ghidra/6510/` — SLEIGH module (`6510.slaspec` = stock 6502 + generated `6510_illegal.sinc`), `build.py`, and a headless Ghidra integration test (`headless/`, run via `Dockerfile.ghidra`).
- `examples/hello_world.py` — 33-byte C64 program using `LAX`/`ISC` + self-modifying code; the fixture for the VM and Ghidra tests.
- `docs/playroutine-anatomy.md` — field guide: nine C64 playroutines reverse engineered to the byte (Hubbard, Galway, Follin, Walker, JCH, GoatTracker, SID Wizard, defMON, lft's Blackbird), the 6502 technique catalogue behind them, and what a decompiler must model.
- `docs/tuneprog-decompiler-design.md` — design of the tuneprog decompiler (SID tune -> certified per-tick-equivalent high-level program): definitions, pipeline, IR, verification, HVSC survey, plan.
- `docs/prototype-automatas.md` — implementation plan for the end-to-end prototype on defMON's *Automatas* (the hardest exemplar): ground truth, package layout, per-stage spec, evidence table, work plan.
- `docs/prototype-follin.md` — the second exemplar, Tim Follin's *Ghouls'n'Ghosts* (32 subtunes, three unrolled voices, patched-`JMP` dispatch, an `init` that patches its own compare): what broke, the generic fixes, the evidence, and the certificates for every subtune (`docs/certificates/ghouls-*.json`).
- `docs/prototype-goattracker.md` — the third exemplar, GoatTracker 2 (HVSC's largest family): what its ghost image, patched low-byte dispatch, 1-based tables and shared tails needed, and the complete certificates for two Linus tunes.
- `docs/prototype-sidwizard.md` — the fourth exemplar, Hermit's SID Wizard 1.6/1.9: an `init` that relocates 30 table operands, three dispatchers in three encodings, the stack as procedure frames, and the complete certificates for *Emomyst* and *End of the World*.
- `docs/ghidra-highpcode-export.md` — the independent baseline: the trace's SMC cells applied to Ghidra as SLEIGH context values (`ghidra/6510/smc.py`), the facts export (`tuneprog/ghidra_facts.py`, `tools/tuneprog_ghidra.py`), the headless high-P-Code/C export, and the complexity, coverage and semantic oracles that compare Ghidra with the tuneprog on the exemplars.
- `tools/tuneprog_certify.py` — end-to-end certification driver (`TUNE.sid --out DIR`), a wrapper on `tuneprog.pipeline`, chunked so each invocation stays inside a CPU budget; `docs/certificates/` holds the certificates it produced for the exemplars (Automatas, Commando, all 32 Ghouls'n'Ghosts subtunes, the two GoatTracker tunes and the two SID Wizard tunes).
- `tools/survey/` — HVSC survey instruments behind that design: `tracer.py` (dynamic per-site tracer on `PcodeVM`), `run.py` (stratified parallel driver), `headers.py` (static census), `report.py` (markdown tables).

## Install

```bash
pip install -e ".[dev]"          # test/lint tooling, py65, pypcode
pip install -e ".[oracle]"       # + pysidtracker (sidplayfp oracle test, needs Docker + HVSC)
```

## CLI

```bash
deity-informant disasm IMAGE [--org ADDR] [--start ADDR] [--count N]   # illegal-aware disassembly
deity-informant pcode  IMAGE --at ADDR [--org ADDR]                    # raw P-Code for one instruction
deity-informant run    IMAGE --init ADDR [--play ADDR --frames N]      # execute in PcodeVM, dump $D400.. grid
deity-informant tuneprog TUNE.sid --out DIR [--song N | --songs all] [--seconds S | --calls N | --until-period] \
                       [--sid-model 6581|8580] [--resume] [--budget S] [--no-verify] [--no-text]
deity-informant emit-sleigh [-o DIR] [--magic 0xEE]                    # build/install the 6510 SLEIGH module
```

## Python API

```python
from deity_informant import lift, PcodeVM, run_sub
vm = PcodeVM(mem); run_sub(vm, entry, {}, lift)   # execute a subroutine to its RTS

from deity_informant.tuneprog import find_entries, run_trace, lift_trace, build_regions, build_procs
image, schedule = find_entries(open("tune.sid", "rb").read())
trace = run_trace(image, schedule[0], calls=1000)         # S0/S1: one instrumented run
lifted = lift_trace(trace)                                # S2a: SMC operand cells -> loads
regions, procs = build_regions(trace, lifted), build_procs(trace, lifted)   # S3 / S2b

from deity_informant.tuneprog import build, ssa, idioms, emit, verify
prog = build.build_ir(trace, lifted, regions, procs)      # IR: design section 4
ssa.simplify(prog, idioms.rewrite)                        # S4: SSA, DCE, propagation, idioms
src = emit.emit_python(prog)                              # S7: one Python function per procedure
cert = verify.certify(prog, verify.verify(prog, trace))   # S8: per-call equivalence + periodicity
```

```bash
python3 tools/tuneprog_certify.py TUNE.sid --out DIR --until-period --resume   # exit 2 = run again
```

## Ghidra

```bash
python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
```

Resolves the stock `6502.slaspec` + SLEIGH compiler from `$GHIDRA_INSTALL_DIR` or a `pypcode` install. Import a C64 image as Raw Binary, language `6510:LE:16:default`. `docker build -f Dockerfile.ghidra -t di-ghidra . && docker run --rm di-ghidra` runs the headless integration test.

## Tests

```bash
black --check deity_informant/ tests/ examples/ ghidra/ && pylint deity_informant/
python ghidra/6510/build.py
pytest tests/ -m "not oracle and not hvsc" -n auto --cov=deity_informant --cov-fail-under=85
pytest tests/ -m oracle -n auto      # sidplayfp oracle (Docker + HVSC)
pytest tests/ -m hvsc -n auto        # tuneprog front end + certificates on HVSC exemplars
```

## References

Illegal-opcode semantics and cycles: "No More Secrets — NMOS 6510 Unintended Opcodes" ([csdb.dk/release/?id=258111](https://csdb.dk/release/?id=258111)).
