# tuneprog — the SID decompiler

`deity_informant/tuneprog/` turns a `.sid` file into a **certified per-tick program**:
an executable IR whose SID writes, schedule effects and state footprint match the
traced playroutine tick for tick, plus a readable pseudocode form of it.

Design: [`tuneprog-decompiler-design.md`](tuneprog-decompiler-design.md).
Exemplar write-ups: [automatas](prototype-automatas.md), [follin](prototype-follin.md),
[goattracker](prototype-goattracker.md), [sidwizard](prototype-sidwizard.md).
Independent baseline: [ghidra-highpcode-export.md](ghidra-highpcode-export.md).

## Vocabulary

| term | meaning |
| --- | --- |
| **site** | one `(pc, opcode, fixed operand bytes)` the trace executed; operand bytes the program writes drop out of the key |
| **cell** | an instruction byte some traced procedure writes — a self-modified operand or opcode |
| **region** | a connected component of the access relation: one storage object, with a base, extent, stride and initial bytes |
| **view** | a presentation copy of the certified program; S5/S6 rewrite the view and never the program |
| **sibling copies** | k static copies of one template an unrolled player wrote out (Follin's three voices, defMON's cascade blocks) |
| **closure** | giving every copy the arms its siblings ran, so k trace-closed programs become one shape (`--closure siblings`) |
| **role** | what a region is used as: `sid_image`, `freq_table`, `counter`, `timer`, `cursor`, `ptr`, `acc`, `phase`, `table` |
| **phase** | the state scalar a tick tests to pick its rate (defMON's `& 7` call counter) |
| **tick** | one call of the play entry, at the cadence S0 discovered; the horizon flag spells it `--calls`, the certificate field `ticks` |
| **certificate** | `certificate.json`: what was compared, over how many ticks, with what divergence and periodicity |

## Pipeline

| stage | does | modules |
| --- | --- | --- |
| S0 | load image, entry and cadence discovery, init runner, 6510 port + CIA models | `machine.py` |
| S1 | op-level tracing: sites, edges, calls/returns, exact per-op access sets, pinned inputs, reference write log, per-tick state hashes | `tracevm.py`, `trace.py`, `tracedata.py` |
| S2a | residualised lift: an SMC operand becomes a load of its cell | `lift.py` |
| S2b | procedures from observed edges: clone per entry, tail calls, variant and computed switches | `cfg.py`; static table closure in `jumptab.py` |
| S2c | sibling closure (presentation): the static correspondence between k copies of one template, and each copy's missing arms lifted from the copy that ran them, into a second program the same front end builds | `siblings.py`, `closure.py` |
| S3 | storage typing: regions, kinds, strides, fields, envelopes, origins | `regions.py` |
| — | front end → IR: one procedure per CFG procedure, one block per node, every memory op typed | `build.py` |
| S4 | SSA over registers/flags/uniques, DCE, copy/constant propagation, 6510 idiom peepholes | `ssa.py`, `idioms.py` |
| S5 | structuring: loops, if/else, switch, counted `for`, the phase | `structure.py` |
| S6 | presentation over a view: value inlining, machine-texture removal, stack frames, 16-bit views, struct views and roles, outlining, shared tails, copy folding | `inline.py`, `texture.py`, `frame.py`, `word.py`, `recover.py`, `facts.py`, `views.py`, `fold.py`, `tails.py`, `copyfold.py`, `unroll.py`, `live.py` |
| S7 | Python code generation, the certificate document, the `tuneprog.md` text form | `emit.py`, `pseudocode.py`, `printer.py` |
| S8 | per-call differential verification against the trace, periodicity, chunked and resumable | `verify.py` |
| — | the facts a headless Ghidra needs from the trace, and the oracles that compare the two ([`ghidra-highpcode-export.md`](ghidra-highpcode-export.md)) | `ghidra_facts.py`, `ghidra_compare.py` |

`pipeline.py` drives every stage into one output directory. The IR itself
(`ir.py`) and its reference interpreter (`interp.py`) are the semantics every
other executor is checked against; `irwalk.py` and `graph.py` are the IR and CFG
traversals every stage shares.

## Module map

```
front end     machine 243  tracevm 325  trace 257  tracedata 300  lift 227
              cfg 309  regions 226  jumptab 179  siblings 330  closure 173
program       ir 401  interp 228  irwalk 308  graph 70  build 481
              ssa 423  idioms 357  emit 337  verify 299
presentation  structure 500  inline 199  texture 475  frame 320  word 369
              fold 472  tails 165  copyfold 487  unroll 395  live 96
              facts 214  recover 316  views 153
text          pseudocode 356  printer 349
driver        pipeline 451  __init__ 114
baseline      ghidra_facts 219  ghidra_compare 182   38 modules, 11,305 lines
```

Stage entry points, which are also the module boundaries:
`machine.find_entries`, `trace.run_trace`, `lift.lift_trace`, `cfg.build_procs`,
`regions.build_regions`, `build.build_ir`, `ssa.simplify`, `emit.emit_python`,
`verify.verify`, `siblings.correspond`, `closure.close`, `structure.structure`,
`copyfold.apply`, `recover.recover`, `views.decorate`, `printer.render`.

## Use

```bash
deity-informant tuneprog TUNE.sid --out DIR \
    [--song N | --songs all] [--seconds S | --calls N | --until-period] \
    [--sid-model 6581|8580] [--closure siblings|none] \
    [--resume] [--budget S] [--no-verify] [--no-text] [--ghidra-facts]
```

`--closure siblings` (the default) is S2c: before printing, the sibling copies of
one template each get the arms their siblings ran, and the copies then print once
over a loop index. It changes nothing that is certified -- `tuneprog.S4.json`,
`tuneprog.py` and `certificate.json` are the trace-closed program either way --
and `--closure none` prints that program as it is. What the closure added is
counted in the `closure` line of `tuneprog.md` and in `tuneprog.S6.json`.

`tools/tuneprog_certify.py` is the same pipeline as a standalone driver. Both
are chunked: a long run exits 2 while work remains, so each invocation stays
inside `--budget` CPU seconds.

```bash
until python3 tools/tuneprog_certify.py TUNE.sid --out out/tune --until-period --resume
do :; done
```

`tools/tuneprog_recert.py` reproduces every certificate under
`docs/certificates/` from the run each one records and diffs it field for field
(ignoring the timestamp and the two timing fields):

```bash
until python3 tools/tuneprog_recert.py --out out/recert --resume; do :; done
```

Artefacts in `--out DIR`:

| file | stage |
| --- | --- |
| `trace.json`, `trace.npz` | S1 (structure, bulk arrays) |
| `regions.json`, `procs.json` | S3, S2b |
| `tuneprog.S4.json` | the certified program |
| `tuneprog.py` | generated Python, one function per procedure |
| `certificate.json` | S8 |
| `tuneprog.S5.json`, `tuneprog.S6.json` | the structured shape, the recovered names and group views (with the closure's own counts) |
| `tuneprog.md` | the pseudocode |
| `state.json`, `tracer.pkl`, `verify.pkl` | resume state |

Python API:

```python
from deity_informant.tuneprog import pipeline
from deity_informant.tuneprog.tracedata import Trace

trace = Trace.load("out/tune")
prog, regions, procs = pipeline.build(trace, "TUNE.sid")            # S2..S4
closed, sibs, stats = pipeline.closed(trace, prog, "TUNE.sid")      # S2c (presentation)
view, structured, names = pipeline.present(closed, sibs)            # S5/S6
```

## Certificate schema

```jsonc
{
  "tune": "Automatas.sid",             // the file's basename
  "sid_model": null,                   // "6581"/"8580" when $D41B bit 0 was pinned
  "oracle": "deity_informant.PcodeVM@0.5.0",
  "reference_validated_against": "none",
  "compared": ["init writes", "tick sid writes", "tick schedule effects"],
  "entry": {"kind": "sub", "addr": 4067, "cycles_per_tick": 2457, "source": "cia_timer"},
  "stage": "S4",                       // "S6" once S5/S6 annotated it (they never edit it)
  "divergence": null,                  // else {tick, index, compared, expected, got, site}
  "cost": {"trace_calls": 149025, "sites": 651, "regions": 102,
           "ir_procs": 8, "ir_blocks": 305, "ir_statements": 1070,
           "verify_cpu_seconds": 9.2, "calls_per_second": 16136},
  "subtunes": [{
      "song": 1, "ticks": 149025, "seconds": 371.64, "cycles_per_tick": 2457,
      "divergences": 0, "envelope_traps": 0,
      "period": 129024, "first_repeat": 149024,      // the tuneprog's own
      "trace_period": 129024, "trace_first_repeat": 149024,   // the trace's
      "complete": true,                // period found, agrees with the trace, no divergence
      "closure": "trace", "inputs_pinned": 2228, "interp_prefix": 2000
  }],
  "generated": "2026-08-16T20:12:05Z"
}
```

`complete` means the run closed: the tuneprog reached a state repeat at the same
tick and with the same period as the trace, with no divergence. Otherwise the
program is certified only to the horizon it ran.

## Certified exemplars

Numbers from `docs/certificates/`. `complete` = certified to a state repeat;
`horizon` = certified to the tick count shown.

| certificate | tune | player | ticks | music | period | procs | blocks | stmts | regions | closure |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `automatas` | Automatas.sid | defMON | 149,025 | 6m12s | 129,024 | 8 | 305 | 1,070 | 102 | complete |
| `automatas-6581` | Automatas.sid | defMON, `$D41B`=0 | 149,025 | 6m12s | 129,024 | 8 | 305 | 1,070 | 102 | complete |
| `automatas-8580` | Automatas.sid | defMON, `$D41B`=1 | 149,025 | 6m12s | 129,024 | 8 | 304 | 1,059 | 102 | complete |
| `commando-song1` | Commando.sid | Hubbard | 11,780 | 3m55s | — | 3 | 115 | 361 | 58 | horizon |
| `commando-song2` | Commando.sid | Hubbard | 11,780 | 3m55s | — | 3 | 100 | 276 | 62 | horizon |
| `ghouls-song01`…`32` | Ghouls_n_Ghosts.sid | Follin | 6…20,049 | — | 1…8,064 | 2–4 | 74–450 | 181–1,294 | 39–70 | complete (31 of 32) |
| `ghouls-songs-all` | Ghouls_n_Ghosts.sid | Follin, all 32 subtunes | 220,049 | — | per subtune | 4 | 520 | 1,567 | 75 | complete (31 of 32) |
| `gt2-je-suis-linus` | Je_suis_Linus_le_salaud.sid | GoatTracker 2 | 8,236 | 2m44s | 6,720 | 14 | 245 | 580 | 73 | complete |
| `gt2-do-it-again` | Do_It_Again.sid | GoatTracker 2 | 9,956 | 3m19s | 8,640 | 14 | 234 | 569 | 73 | complete |
| `sw-emomyst` | Emomyst.sid | SID Wizard 1.6 | 8,084 | 2m41s | 6,120 | 15 | 365 | 1,054 | 96 | complete |
| `sw-end-of-the-world` | End_of_the_World.sid | SID Wizard 1.9 | 14,465 | 4m49s | 7,688 | 16 | 361 | 1,050 | 94 | complete |

Every one has `divergences: 0` and `envelope_traps: 0`. `ghouls-song21` is the
one subtune with no state repeat inside 400 s (two voices keep a portamento and a
trill moving), so it is certified to a 20,049-tick horizon. `commando` has
Hubbard's counters running free, so it is certified to its HVSC length.

## Known gaps

- **Trace closure.** The certified product is trace-closed: a branch direction or
  a table entry the run never took becomes `trap 'untaken'` / `trap 'unverified'`,
  not a decompiled path. `jumptab` closes a patched jump statically over the
  table's observed extent, which recovers most but not all arms (14 of 16 in
  GoatTracker's tick-0 table); entries no accessor ever reached are outside the
  region and stay unlisted. `--closure siblings` recovers the arms *another copy
  of the same template* ran, which is most of them where a player unrolls its
  voices, but only for the printed form.
- **The closure is unverified code.** The arms a sibling ran are lifted into the
  copies that never reached them, under those copies' own operands. They are
  reachable only through edges that were a `trap` before, the added sites have
  count 0, and the closed program is the same front end's product from the closed
  trace -- but no execution ever took them, and the `closure` line says how many
  statements that is (Follin song 1: 44 of 283).
- **A copy is found by its static shape, so a short horizon finds less of it.**
  Discovery starts at pcs the trace executed and pairs a dispatch's arms by how
  far their targets align; over 30 s of *Automatas* one cascade run folds and over
  the whole song both do, nested. What folds is therefore a function of the
  horizon, and the certificate's horizon is what the exemplar documents report.
- **Names are role-derived.** The trace shows shapes, not words: `timer_2`,
  `cursor_1490`, `b148D`. A per-family dictionary keyed on the player signature
  would name them from the original source.
- **16-bit views need one carry chain.** `word.fold16` proves a pair from the
  carry the low byte hands the high byte; halves stored by unrelated
  instructions (Follin's frequency shadow, the pulse width) stay two bytes.
- **A callee's return value does not print.** `ir.retval` recovers the tick's own
  return; a procedure that computes a byte for its caller prints an empty body
  and the caller shows the assignment.
- **Sign extension and flag algebra print as written.** A patched branch
  dispatcher keeps `(base + off) - ((off & $80) << 1)`, and a `BVC` after `SBC`
  prints the overflow expression rather than the bit test it stands for.
- **Refusals.** A second armed interrupt source (CIA-2 timer, NMI vector), a
  recursive JSR call graph, an `init` that never returns inside its budget, and a
  play routine that runs past its instruction budget are diagnosed and refused,
  not approximated.
