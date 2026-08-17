# deity-informant

NMOS 6510 toolkit: a 6510 -> raw-P-Code lifter + pure-Python P-Code VM (all 105 documented illegal opcodes are first-class), and a `6510` Ghidra/pypcode SLEIGH processor module. The `jennings` package supplies the opcode table, assembler and disassembler, and its py65-compatible `MPU` executes on this lifter + VM.

## Components

- `deity_informant/lifter.py` — `lift(mem, pc)` -> `{"ops", "len", "cyc", "pen", "ctrl"}` raw P-Code; `CYCLETIME`/`EXTRACYCLES` tables.
- `deity_informant/vm.py` — `PcodeVM` interpreter over a flat 64 KiB image (SID/VIC/CIA volatile IO modeled); `run_sub`/`run_irq`/`run_irq_driven` drivers.
- `deity_informant/c64.py` — power-on RAM, PSID/RSID loader, IRQ vector discovery, ROM-free IRQ dispatch stubs.
- `deity_informant/tuneprog/` — the tuneprog decompiler: a `.sid` becomes a certified per-tick program plus pseudocode. Module map, pipeline stages, certificate schema and the certified exemplars: `docs/tuneprog.md`.
- `deity_informant/cli.py` — `deity-informant` console script, including `tuneprog` (the pipeline).
- `ghidra/6510/` — SLEIGH module (`6510.slaspec` = stock 6502 + generated `6510_illegal.sinc`), `build.py`, and a headless Ghidra integration test (`headless/`, run via `Dockerfile.ghidra`).
- `examples/hello_world.py` — 33-byte C64 program using `LAX`/`ISC` + self-modifying code; the fixture for the VM and Ghidra tests.
- `docs/tuneprog.md` — the tuneprog decompiler: module map, pipeline S0-S8, CLI and tools, certificate schema, the certified exemplars, known gaps.
- `docs/playroutine-anatomy.md` — field guide: nine C64 playroutines reverse engineered to the byte (Hubbard, Galway, Follin, Walker, JCH, GoatTracker, SID Wizard, defMON, lft's Blackbird), the 6502 technique catalogue behind them, and what a decompiler must model.
- `docs/tuneprog-decompiler-design.md` — design of the tuneprog decompiler (SID tune -> certified per-tick-equivalent high-level program): definitions, pipeline, IR, verification, HVSC survey, plan.
- `docs/prototype-automatas.md`, `-follin.md`, `-goattracker.md`, `-sidwizard.md` — the four certified exemplars (defMON, Follin's *Ghouls'n'Ghosts*, GoatTracker 2, SID Wizard 1.6/1.9): ground truth, what broke, the generic fix, the evidence.
- `docs/ghidra-highpcode-export.md` — the independent baseline: the trace's SMC cells as SLEIGH context values, the facts export, the headless high-P-Code/C export, and the oracles that compare Ghidra with the tuneprog.
- `tools/tuneprog_certify.py` — end-to-end certification driver (`TUNE.sid --out DIR`), chunked against a CPU budget; `docs/certificates/` holds the certificates for the exemplars. `tools/tuneprog_recert.py` reproduces every one of them and diffs it field for field.
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

from deity_informant.tuneprog import find_entries, run_trace, pipeline, printer, verify
image, schedule = find_entries(open("tune.sid", "rb").read())
trace = run_trace(image, schedule[0], calls=1000)         # S0/S1: one instrumented run
prog, regions, procs = pipeline.build(trace, "tune.sid")  # S2/S3/S4: the certified program
cert = verify.certify(prog, verify.verify(prog, trace))   # S8: per-call equivalence + periodicity
view, structured, names = pipeline.present(prog)          # S5/S6 over a copy
text = printer.render(view, structured, names, cert)      # S7: tuneprog.md
```

```bash
python3 tools/tuneprog_certify.py TUNE.sid --out DIR --until-period --resume   # exit 2 = run again
python3 tools/tuneprog_recert.py --out out/recert --resume                     # reproduce every certificate
```

## Ghidra

```bash
python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
```

Resolves the stock `6502.slaspec` + SLEIGH compiler from `$GHIDRA_INSTALL_DIR` or a `pypcode` install. Import a C64 image as Raw Binary, language `6510:LE:16:default`. `docker build -f Dockerfile.ghidra -t di-ghidra . && docker run --rm di-ghidra` runs the headless integration test.

## Tests

```bash
black --check deity_informant/ tests/ examples/ ghidra/ && pylint deity_informant/ examples/ ghidra/6510/build.py
python ghidra/6510/build.py
pytest tests/ -m "not oracle and not hvsc" -n auto --cov=deity_informant --cov-fail-under=85
pytest tests/ -m oracle -n auto      # sidplayfp oracle (Docker + HVSC)
pytest tests/ -m hvsc -n auto        # tuneprog front end + certificates on HVSC exemplars
```

## References

Illegal-opcode semantics and cycles: "No More Secrets — NMOS 6510 Unintended Opcodes" ([csdb.dk/release/?id=258111](https://csdb.dk/release/?id=258111)).
