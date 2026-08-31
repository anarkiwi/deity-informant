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
- `docs/prototype-trackerprog.md` — the layer above the tuneprog: one player-independent data object (pitch table, streams, bounded accumulators, instruments, score) rendered by one universal player, its observable and certificate, the T0-T3 lift and the acceptance.
- `docs/trackerprog-backlog.md` — review of the trackerprog prototype against its sources, and the work packages that enable it (owner, size, acceptance, order).
- `docs/prototype-automatas.md`, `-follin.md`, `-goattracker.md`, `-sidwizard.md`, `-jch.md` — the certified exemplars (defMON, Follin's *Ghouls'n'Ghosts*, GoatTracker 2, SID Wizard 1.6/1.9, JCH NewPlayer V20): ground truth, what broke, the generic fix, the evidence.
- `docs/prototype-kernal-entry.md` — the installed-handler family (PSID `play == 0`, CINV entries): entry convention, screened population, two evidence certificates.
- `docs/prototype-nmi.md` — the second interrupt (a CIA #2 NMI as the schedule's second entry): the population by handler kind, the chip model it needed, the interleaving against `sidplayfp`, the first two-entry certificate.
- `docs/prototype-commando-floor.md` — the complexity floor of one simple tune: print cost against the tune's own bytes, where its statements live, a hand-factored form, and the region-typing rule that would produce it.
- `docs/prototype-trackerprog-transition.md` — the layer between: the certified tick as data (an ordered list of guarded assignments over a named STATE, TABLES and the SID) plus the score and its token grammar, rendered by one universal player, with the tracker vocabulary as a reading of it; the nine families written out byte by byte.
- `docs/prototype-commando-trackerprog.md` — Commando by hand as a trackerprog: the oracle reference tune for `docs/prototype-trackerprog.md`, certified at 0 divergences over three subtunes, and the ten schema additions the tune forced.
- `docs/prototype-goattracker-trackerprog.md` — GoatTracker 2 by hand as a trackerprog: two builds of one player rendered by the same universal player as Commando with no family branch, certified at 0 divergences (write-for-write identical) over 8,236 and 8,659 ticks with the inherited loop claim re-verified on the render, and the seven forms the family forced.
- `docs/prototype-sidwizard-trackerprog.md` — SID Wizard 1.6/1.9 by hand as a trackerprog: the third family on the same universal player, certified at 0 divergences (write-for-write identical, with no shadow to make it free) over 8,084 and 14,465 ticks with the inherited loop claims re-verified on the render, the seven forms the family forced, and the poison table saying what each datum is worth.
- `docs/prototype-defmon-trackerprog.md` — defMON by hand as a trackerprog: the fourth family on the same universal player, certified at 0 divergences (write-for-write identical) over the whole 149,025-tick horizon of *Automatas* and the 1,799 of *Jazzpjazz*, the first `--budget`/`--resume` certification and the first `horizon` order; the ten forms the family forced, the multispeed question closed, and the two schema citations it disproved.
- `docs/prototype-jch-trackerprog.md` — JCH NewPlayer V20 by hand as a trackerprog: the fifth family on the same universal player, certified at 0 divergences over the whole 2,401-tick horizon of *Guldkornekspressen Intro* (loop re-verified) and the whole 8,577 of *I Could Eat a Knob at Night* (write-for-write identical); the first `end.kind = fixed_point`, the first family whose two builds disagree about having a shadow, the ten forms it forced and the two it was expected to force that measured to zero.
- `docs/prototype-follin-trackerprog.md` — Tim Follin's *Ghouls'n'Ghosts* by hand as a trackerprog: the sixth family on the same universal player, certified at 0 divergences (write-for-write identical) over **all 32 subtunes**, 111,763 ticks, with three named builds taking all three terminators (`fixed_point`, `loop` re-verified, `horizon`); the score-as-program exemplar §3.6's `call`/`ret`/counted-loop grammar was struck for want of, the first fetch that is a walk over several rows at one boundary, and the first family with no instrument table and no accumulator at all.
- `docs/survey-tuneprog.md` — the pipeline over the stratified 7,023-tune HVSC sample: certification rate by family, failure classes, refusal reasons, stack/entry/fold distributions, cost and the fast-tracer verdict.
- `docs/ghidra-highpcode-export.md` — the independent baseline: SMC cells as SLEIGH context values, the facts export, the headless high-P-Code/C export, and the oracles comparing Ghidra with the tuneprog.
- `tools/` — `tuneprog_certify.py` (end-to-end certification driver, chunked against a CPU budget; `docs/certificates/` holds the exemplars' certificates), `tuneprog_recert.py` (reproduces every certificate and diffs it field for field), `tuneprog_period.py` (why a subtune has no state repeat: counter, drifting accumulator, or aperiodic), `tuneprog_floor.py` (load-band split, `xz` description lengths, printed statements by code range and kind, 16-bit pair check).
- `tools/trackerprog_commando.py` — the hand transliteration of Commando's tuneprog into a trackerprog, rendered by `deity_informant/trackerprog/universal.py` and certified against the PcodeVM (`--certify`).
- `tools/trackerprog_goattracker.py` — the same for GoatTracker 2, on the same player: it locates each tune's data by the operand of the instruction that reads it, so the two builds certify on one code path (`--certify --source <tuneprog certificate>`).
- `tools/trackerprog_sidwizard.py` — the same for SID Wizard: it runs the tune's own `init` first, because the music blob is position-independent and its table operands hold offsets until then, and reads each datum off the image the tick sees (`--certify --source <tuneprog certificate>`).
- `tools/trackerprog_defmon.py` — the same for defMON, with `--budget`/`--resume` so a 149,025-tick horizon certifies over several invocations.
- `tools/trackerprog_jch.py` — the same for JCH NewPlayer V20: one signature set for both builds (the player is a code template that differs only in its table operands), the 6510 port modelled on the oracle side because one build banks the chip out and flushes its own copy, and a named refusal for the sample builds.
- `tools/trackerprog_follin.py` — the same for Tim Follin's *Ghouls'n'Ghosts*: it reads every datum off the post-init image because the rip stub places the song blocks inside `init`, and parses one byte stream per voice as a program of blocks — a state is a byte *and* the note length `$84` left, so the grammar is not context free (`--certify --source <tuneprog certificate>`).
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
