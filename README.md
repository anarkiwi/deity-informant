# deity-informant

A 6510 illegal-opcode toolkit: a standalone 6510 -> raw-P-Code lifter + pure-Python P-Code VM, and a `6510` Ghidra/pypcode SLEIGH processor module. All 105 documented NMOS 6510 illegal opcodes are first-class. No runtime dependency on py65 or Ghidra.

## Why

Working on C64 code requires the NMOS 6510 illegal opcodes, and no existing backend handles them correctly:

- Real playroutines/demos use illegals as load-bearing instructions (A Mind Is Born runs `SRE`; Automatas runs `SBX`/`SAX`/`LAX`/`ANC`/`ALR`).
- Stock Ghidra's 6502 SLEIGH spec (and pypcode, which vendors it verbatim) defines zero illegals and has no catch-all — every illegal is a `BadDataError`, so it cannot even disassemble such code, let alone decompile it.
- Ghidra's decompiler only consumes P-Code from a *compiled* SLEIGH spec, so Python-generated P-Code cannot be injected — a SLEIGH language extension is the only way to make the decompiler illegal-aware.
- py65 is not a valid execution reference: it stubs the RMW illegals (`SLO/RLA/SRE/RRA/DCP/ISC`, `LAS/ANE/SH*`) as `inst_not_implemented` — a 2-byte, 0-cycle skip, silently wrong on real code.
- The known community 6510 Ghidra fork adds illegal *disassembly* only, with uncertain P-Code semantics — not trustworthy for decompilation or execution.
- deity-informant supplies both missing pieces: a compiled 6510 SLEIGH module (illegal-aware Ghidra/pypcode decompilation) and a hardware-validated lifter + VM (byte-exact vs sidplayfp) executing all 105 illegals.

## Products

- **standalone lifter + VM** — `lift` (6510 -> raw P-Code), the `PcodeVM` interpreter, and the `run_sub`/`run_irq`/`run_irq_driven` drivers. Pure Python, no Ghidra, no py65.
- **`6510` SLEIGH module** (`ghidra/6510/`) — stock 6502 legal spec + generated `6510_illegal.sinc` that makes Ghidra's disassembler *and* decompiler, and pypcode, illegal-aware. Language id `6510:LE:16:default`.
- **symbolic window recorder** — `record` runs a driver over repeated invocations, executing bit-identically to `PcodeVM` while residualising data flow over the entry state and recording every control-flow / placement fold as a fact; replay reproduces observable writes byte-exact. Sound under self-modifying code; a record-time assertion gates every artifact. See [docs/smc-recovery.md](docs/smc-recovery.md) (pipeline + ASCII diagrams) and [docs/symbolic-recorder.md](docs/symbolic-recorder.md) (contract).
- **structured decompiler (sidprog)** — `decompile` lifts a playroutine to standalone structured text: procedures of nested `loop`/`if`/`switch` regions over labeled blocks with folded expressions, SMC modeled (operand patches become live-image reads, opcode patches become observed per-byte dispatch variants behind runtime guards), cycle costs and page-cross penalties explicit, volatile reads computed from the cycle counter. The text parses back (re-verifying the structurer codec) and replays the original's cycle-stamped `(cycle, reg, value)` write log bit-exact for the full song; `--structured` renders a display-only readable view. Grammar: [docs/grammar.md](docs/grammar.md); laws: [docs/sidprog-language.md](docs/sidprog-language.md); see [docs/decompiler-implementation.md](docs/decompiler-implementation.md) for the specification to complete it.
- **frame-program layer (frameprog)** — a derived artifact **above** sidprog, emitted by `decompile --frameprog`. It drops cycle exactness: the only normative output is the canonical per-frame projection of the SID write stream, so cycle annotations are gone, volatile reads become declared `inputs` resolved from a pinned trace, and play-phase SMC is state (patched operand bytes are variables, opcode cells are `switch` arms with faulting defaults — no code image). sidprog remains the cycle-exact ground truth and its Gates A/C/L/S are untouched; frameprog is generated from the same committed model and verified by **Gate FP** — its reference evaluator's records must equal `framelog.canonical` of the walker's log, frame-for-frame. Both dialects share one grammar and one parser, so `dumps(loads(t)) == t` holds for each. Spec and gates: [docs/frameprog.md](docs/frameprog.md).
- **tracker layer** — the final step, consuming a frame program and nothing else. A tune becomes a graph of one primitive, the triggered generator `(transfer, trigger, route)` with `DIV`/`SELECT`/`RAMP`/`EDGE`/`RAW`; note lanes are equal-tempered semitone indices inverted from the **declared** pitch table, freq/pw and ctrl/ADSR are **declared** table lanes read at a recovered row — the table named by the store statement itself, minus any offset the play phase writes — and every write a generator does not yet produce stays in an explicit `RAW` residual. Verified by one law — the graph's canonical projection must equal frameprog's evaluator, frame-for-frame — so coverage is reported, never assumed. Coverage measures **justification, not reach**: measured against three editors' own songs, zero of a composer's writes are ones the recovery never produces, so a residual emit is one that cannot be attributed to a declaration rather than one that is missing. Spec, primitive, corrections ledger and measured coverage: [docs/tracker.md](docs/tracker.md).

## Install

```bash
pip install deity-informant            # core, no deps
pip install deity-informant[oracle]    # + py65 (legal-opcode differential oracle)
pip install deity-informant[ghidra]    # + pypcode (SLEIGH build / lift backend)
pip install deity-informant[dev]       # test + lint tooling
```

## CLI

Console script `deity-informant`:

```bash
deity-informant disasm IMAGE [--org ADDR] [--start ADDR] [--count N]  # 6510 disassembly (illegal-aware)
deity-informant pcode  IMAGE --at ADDR [--org ADDR]                   # raw P-Code for one instruction
deity-informant run    IMAGE --init ADDR [--play ADDR --frames N]     # execute in PcodeVM, dump $D400.. grid
deity-informant decompile TUNE [.sid or raw] [--frames N --verify -o FILE]  # decompile to sidprog text
deity-informant decompile TUNE --frameprog                             # frame-level dialect (Gate FP)
deity-informant prog-run FILE [--frames N]                            # run a sidprog program, dump the grid
deity-informant emit-sleigh [-o DIR] [--magic 0xEE]                   # build/install the 6510 SLEIGH module
```

## Python API

```python
from deity_informant import lift, PcodeVM, run_sub
insn = lift(mem, pc)                 # {"ops", "len", "cyc", "pen", "ctrl"}
vm = PcodeVM(mem); run_sub(vm, entry, {}, lift)   # execute a subroutine to its RTS
```

## Use with Ghidra

```bash
python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
```

Compiles `6510.sla`, resolving the stock `6502.slaspec` + SLEIGH compiler from `$GHIDRA_INSTALL_DIR` (or a `pypcode` install). Restart Ghidra, import the C64 image as Raw Binary language `6510:LE:16:default` at base `$0000`; illegals now decode instead of BadData. Full steps: [docs/ghidra.md](docs/ghidra.md).

## Example / demo

`python examples/hello_world.py` prints `HELLO, WORLD!` from a self-contained 33-byte C64 program that uses two load-bearing illegal opcodes (`LAX`, `ISC`) and genuine self-modifying code (`ISC`'s `INC` rewrites the `STA` operand each pass). CI verifies both the VM output and that the `LAX`/`ISC` bytes decode through the 6510 SLEIGH engine (where stock 6502 throws `BadDataError`). Walkthrough: [docs/hello-world.md](docs/hello-world.md).

### Decompiled to P-Code

The two illegal opcodes through Ghidra's decompiler engine (`6510:LE:16:default`,
libsla — same engine as `pypcode.Context(...).translate(...)`). Stock 6502 raises
`BadDataError` on both bytes; the 6510 module emits real P-Code:

```
$1002  BF 13 10   LAX $1013,Y      ; illegal — one instruction loads A and X and sets Z
  unique[11b00:2] = zext(Y)                 # Y widened to 16-bit index
  unique[11d00:2] = 0x1013 + unique[11b00:2]# effective address $1013 + Y
  A = *[RAM]unique[11d00:2]                  # A <- data[Y]
  X = A                                      #   ...and X too (the "LAX" fork)
  Z = A == 0x0                               # Z flag the BEQ terminator rides on
  N = A s< 0x0                               # N flag

$100C  EF 0A 10   ISC $100A        ; illegal RMW — INC $100A then SBC (discarded)
  unique[f400:1] = RAM[100a:1] + 0x1         # INC the byte at $100A ...
  RAM[100a:1]    = unique[f400:1]            #   ...= the STA operand -> self-modification
  unique[f500:1] = A - unique[f400:1]        # SBC A,mem: borrow-chain subtract ...
  unique[f800:1] = unique[f500:1] - !C       #   ...minus carry-complement
  ... V/N/Z/C flag algebra elided ...
  A = unique[f800:1]                         # SBC result -> A (overwritten by next LAX)
```

`RAM[100a:1] = ...` is Ghidra rendering a **store into code space** — the self-modification made visible in P-Code. The full instruction-by-instruction dump (and how CI asserts it under headless Ghidra) is in [docs/hello-world.md](docs/hello-world.md).

## Illegal opcodes

All 105 documented NMOS 6510 illegals lifted as genuine P-Code (not stubs), semantics/cycles per "No More Secrets — NMOS 6510 Unintended Opcodes" (V1.0, 24 Dec 2025) ([csdb.dk/release/?id=258111](https://csdb.dk/release/?id=258111)), independently validated byte-exact against the sidplayfp hardware oracle. Full table: [docs/illegal-opcodes.md](docs/illegal-opcodes.md).

## Docs

- [docs/design.md](docs/design.md) — architecture (lifter + VM, SLEIGH module, raw vs high P-Code, cycle layer).
- [docs/decompiler-implementation.md](docs/decompiler-implementation.md) — specification for completing the structured decompiler (soundness, structuring, the structured language, corpus gates).
- [docs/grammar.md](docs/grammar.md) — the ONE grammar (`deity_informant/sidprog.lark`, LALR(1)) of the sidprog language and its frameprog dialect, generated from the implementation.
- [docs/sidprog-language.md](docs/sidprog-language.md) — sidprog language: normative semantics, version policy, and property-tested round-trip laws.
- [docs/frameprog.md](docs/frameprog.md) — frameprog: the canonical frame projection, the digi input class, the pinned volatile trace, Gate FP, and the lift ladder.
- [docs/tracker.md](docs/tracker.md) - tracker: the one generator primitive, the one law (frameprog to tracker), declared-table pitch recovery, measured coverage, and §0's ledger of predictions this document made and later measured false.
- [docs/tracker-text.md](docs/tracker-text.md) - tracker-text: the recovered graph rendered for human review (`tools/tracker_text.py`), side by side with the native-editor oracle's, and what the view deliberately does not claim.
- [docs/gt-oracle.md](docs/gt-oracle.md) - the native-editor oracle: a GoatTracker 2 or SID-Wizard song read out of the editor's own model, mapped onto the same primitive and checked by the same law, with the three-axis comparison against the recovery and the deficiency report.
- [docs/dm-oracle.md](docs/dm-oracle.md) - the DefMON oracle: the sidTAB decomposed into generic lanes, the per-row delay as a clock and the jump as a loop, gated on the frameprog boundary, with a third editor's verdict on every deficiency the report carries.
- `tools/graph_diff.py` - node-level diff of the recovered graph against the editor's own, keyed on what each node produces and self-checked by rebuilding both projections; the instrument that separates what the recovery cannot reach from what it cannot attribute.
- [docs/song-model.md](docs/song-model.md) - song-synthesis model recovery (counters, freq drivers, control automaton) the tracker engine lift builds on.
- [docs/soundness.md](docs/soundness.md) — observed-primary doctrine, per-site certification records, the report, and strict (`--sound`) mode.
- [docs/decompiler-plan-prototype.md](docs/decompiler-plan-prototype.md) — the prototype's phase/gate history (superseded).
- [docs/illegal-opcodes.md](docs/illegal-opcodes.md) — illegal-opcode reference.
- [docs/nms-provenance.md](docs/nms-provenance.md) — reference-source provenance.
- [docs/cycle-times.md](docs/cycle-times.md) — `CYCLETIME`/`EXTRACYCLES`: references, the 512-entry audit, and the family invariant the tests assert.
- [docs/ghidra.md](docs/ghidra.md) — using the 6510 module with Ghidra / pypcode.
- [docs/differential-fuzz.md](docs/differential-fuzz.md) — byte-exactness differential fuzzer (VM / recorder / sidtrace oracle over synthetic-player idiom classes).
