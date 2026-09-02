# deity-informant

NMOS 6510 toolkit: a 6510 -> raw-P-Code lifter + pure-Python P-Code VM (all 105 documented illegal opcodes are first-class), and a `6510` Ghidra/pypcode SLEIGH processor module. The `jennings` package supplies the opcode table, assembler and disassembler, and its py65-compatible `MPU` executes on this lifter + VM.

## Components

- `deity_informant/lifter.py` — `lift(mem, pc)` -> `{"ops", "len", "cyc", "pen", "ctrl", "prov", "stk"}` raw P-Code; `CYCLETIME`/`EXTRACYCLES` tables.
- `deity_informant/vm.py` — `PcodeVM` interpreter over a flat 64 KiB image (SID/VIC/CIA volatile IO modeled); `run_sub`/`run_irq`/`run_irq_driven` drivers.
- `deity_informant/c64.py` — power-on RAM, PSID/RSID loader, IRQ vector discovery, ROM-free IRQ dispatch stubs.
- `deity_informant/tuneprog/` — the tuneprog decompiler: a `.sid` becomes a certified per-tick program plus pseudocode (`docs/tuneprog-architecture.md`).
- `deity_informant/cli.py` — `deity-informant` console script, including `tuneprog` (the pipeline).
- `ghidra/6510/` — SLEIGH module (`6510.slaspec` = stock 6502 + generated `6510_illegal.sinc`), `build.py`, and a headless Ghidra integration test (`headless/`, run via `Dockerfile.ghidra`).
- `examples/hello_world.py` — 33-byte C64 program using `LAX`/`ISC` + self-modifying code; the fixture for the VM and Ghidra tests.
- `docs/tuneprog-architecture.md` — **the canonical tuneprog reference**: definitions, pipeline S0-S8, the lift end to end, the IR, verification and the certificate schema, presentation, CLI and tools, the machine model and its boundaries, the certified exemplars, the module map, the process.
- `docs/playroutine-anatomy.md` — field guide: nine C64 playroutines reverse engineered to the byte (Hubbard, Galway, Follin, Walker, JCH, GoatTracker, SID Wizard, defMON, lft's Blackbird), the 6502 technique catalogue behind them, and what a decompiler must model.
- `docs/tuneprog-backlog.md` — open tuneprog work by lever (mechanism, evidence, owner, size, acceptance), the done ledger, the execution order.
- `docs/prototype-trackerprog.md` — the trackerprog specification: the object, its observable and certificate, the schema, the universal player, bounded accumulators, the T0-T3 lift, refusals and acceptance.
- `docs/trackerprog-backlog.md` — open trackerprog work (mechanism, size, acceptance command), the six settled decisions, the checks a tenth family must run, the presentation gaps.
- `docs/trackerprog-review.md` — the critical review of the player and the spec: the verdict, the per-row outcome, and what measurement refuted.
- `docs/prototype-*-trackerprog.md` — nine hand transliterations, one per family (Hubbard, GoatTracker 2, SID Wizard, defMON, JCH V20, Follin, Blackbird, Walker, Galway): ground truth, the forms the family forced, and its certificate numbers.
- `docs/prototype-automatas.md`, `-follin.md`, `-goattracker.md`, `-sidwizard.md`, `-jch.md` — the certified exemplars (defMON, Follin's *Ghouls'n'Ghosts*, GoatTracker 2, SID Wizard 1.6/1.9, JCH NewPlayer V20): ground truth, what broke, the generic fix, the evidence.
- `docs/prototype-kernal-entry.md` — the installed-handler family (PSID `play == 0`, CINV entries): entry convention, screened population, two evidence certificates.
- `docs/prototype-nmi.md` — the second interrupt (a CIA #2 NMI as the schedule's second entry): the population by handler kind, the chip model it needed, the interleaving against `sidplayfp`, the first two-entry certificate.
- `docs/prototype-lifter.md` — the lift from certified artefacts to a trackerprog: the schedule derived (B6), the tick lowered (B7), the coverage, the certificate, and the field-by-field diff against the hand object.
- `docs/prototype-commando-floor.md` — the complexity floor of one simple tune: print cost against the tune's own bytes, where its statements live, a hand-factored form, and the region-typing rule that would produce it.
- `docs/survey-tuneprog.md` — the pipeline over the stratified 7,023-tune HVSC sample: certification rate by family, failure classes, refusal reasons, stack/entry/fold distributions, cost and the fast-tracer verdict.
- `docs/ghidra-highpcode-export.md` — the independent baseline: SMC cells as SLEIGH context values, the facts export, the headless high-P-Code/C export, and the oracles comparing Ghidra with the tuneprog.
- `tools/` — `tuneprog_certify.py` (end-to-end certification driver, chunked against a CPU budget; `docs/certificates/` holds the exemplars' certificates), `tuneprog_recert.py` (reproduces every certificate and diffs it field for field), `tuneprog_period.py` (why a subtune has no state repeat: counter, drifting accumulator, or aperiodic), `tuneprog_floor.py` (load-band split, `xz` description lengths, printed statements by code range and kind, 16-bit pair check).
- `tools/trackerprog_*.py` — one hand transliteration per family, each rendered by `deity_informant/trackerprog/universal.py` and certified against the PcodeVM (`--certify --source <tuneprog certificate>`; `--budget`/`--resume` where a horizon exceeds one invocation), plus `trackerprog_sizes.py` (section 9.1's object-against-load-band table) and `trackerprog_poison.py` (the poison harness: an object, a stated mutation and a build set, rendered both ways over each build's whole horizon).
- `tools/doclinks.py` — every `[text](target)` in `docs/` and `README.md` resolved: the file, and the `#anchor` against the target's own headings.
- `tools/survey/` — HVSC survey instruments behind `docs/tuneprog-architecture.md` §9.3: `tracer.py` (dynamic per-site tracer on `PcodeVM`), `run.py` (stratified parallel driver), `headers.py` (static census), `report.py` (markdown tables), `tuneprog_sweep.py` (the whole pipeline over the same sample, resumable), `tuneprog_report.py` (its tables).

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
                       [--sid-model 6581|8580] [--no-merge] [--closure trace|static] [--resume] [--budget S] [--no-verify] [--no-text]
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
text = printer.render(view, structured, names, cert)      # S7: the tuneprog.md artefact
```

```bash
python3 tools/tuneprog_certify.py TUNE.sid --out DIR --until-period --resume   # exit 2 = run again
python3 tools/tuneprog_recert.py --out out/recert --resume                     # reproduce every certificate
python3 tools/tuneprog_period.py TUNE.sid --song 1 --out DIR --resume          # why a subtune never repeats
```

## Ghidra

```bash
python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
```

Resolves the stock `6502.slaspec` + SLEIGH compiler from `$GHIDRA_INSTALL_DIR` or a `pypcode` install; the stock spec's `JSR`/`RTS` return address and `SBC` borrow are patched to the hardware's. Import a C64 image as Raw Binary, language `6510:LE:16:default`. `docker build -f Dockerfile.ghidra -t di-ghidra . && docker run --rm di-ghidra` runs the headless integration test; `.github/workflows/nightly.yml` runs the three oracles over every certificate.

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
