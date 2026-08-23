# tuneprog — the SID decompiler

`deity_informant/tuneprog/` turns a `.sid` file into a **certified per-tick program**:
an executable IR whose SID writes, schedule effects and state footprint match the
traced playroutine tick for tick, plus a readable pseudocode form of it.

Design: [`tuneprog-decompiler-design.md`](tuneprog-decompiler-design.md).
Exemplar write-ups: [automatas](prototype-automatas.md), [follin](prototype-follin.md),
[goattracker](prototype-goattracker.md), [sidwizard](prototype-sidwizard.md),
[jch](prototype-jch.md), [kernal-entry](prototype-kernal-entry.md),
[commando-floor](prototype-commando-floor.md), [nmi](prototype-nmi.md).
Independent baseline: [ghidra-highpcode-export.md](ghidra-highpcode-export.md).

## Vocabulary

| term | meaning |
| --- | --- |
| **site** | one `(pc, opcode, fixed operand bytes)` the trace executed; operand bytes the program writes drop out of the key |
| **cell** | an instruction byte some traced procedure writes — a self-modified operand or opcode |
| **region** | a connected component of the access relation: one storage object, with a base, extent, stride and initial bytes |
| **view** | a presentation copy of the certified program; S5/S6 rewrite the view and never the program |
| **sibling copies** | k static copies of one template an unrolled player wrote out (Follin's three voices, defMON's cascade blocks) |
| **copy index** | the value `v` a merged family runs over: copy *j* executing a template row is that row executed with `v = j` |
| **column** | a per-copy table `T_x[v]` the merge reads an operand from where the copies name different addresses |
| **coverage** | a merged block's execution count per copy; a zero says no execution of that copy reached it, and the statement is unverified |
| **role** | what a region is used as: `sid_image`, `freq_table`, `counter`, `timer`, `cursor`, `ptr`, `acc`, `phase`, `table`, `voice_map` |
| **split view** | a block one init loop made one region, cut into the fields the play phase walks: by a *record* stride (element index outside, field inside) or by its **transpose** (a struct-of-arrays) |
| **voice map** | a read-only table holding `0, 7, 14` -- the SID's voice -> register-block offsets, so an index read from it is a voice |
| **phase** | the state scalar a tick tests to pick its rate (defMON's `& 7` call counter) |
| **tick** | one call of the play entry, at the cadence S0 discovered; the horizon flag spells it `--calls`, the certificate field `ticks` |
| **header** | P/SID metadata is not ground truth: it seeds discovery, the traced machine decides, the `sidplayfp` oracle arbitrates |
| **certificate** | `certificate.json`: what was compared, over how many ticks, with what divergence and periodicity |

## Pipeline

| stage | does | modules |
| --- | --- | --- |
| S0 | load image, entry and cadence discovery, init runner, 6510 port + CIA models; a CIA #2 NMI second entry -- when its line asserts, which vector carries it | `machine.py`, `nmi.py` |
| S1 | op-level tracing: sites, edges, calls/returns, exact per-op access sets, pinned inputs, reference write log, per-tick state hashes. The site is the VM's cache key, so a site's closure, access sets, index domain, register masks and edge cells resolve once. A second entry preempts at the instruction boundary its line asserts at, and its schedule is recorded per tick | `tracevm.py` (memory attribution, step loop), `tracesite.py` (one site, resolved once), `traceflow.py` (edges, calls, shadow stack), `trace.py`, `tracedata.py` |
| S2a | residualised lift: an SMC operand becomes a load of its cell | `lift.py` |
| S2b | procedures from observed edges: clone per entry, tail calls, variant and computed switches | `cfg.py`; static table closure in `jumptab.py`, over a per-copy column base and the range a branch proves for the index |
| S2b' | the bounded static closure of untaken branch directions, as zero-coverage sites the same front end builds (`--closure static`) | `closure.py` |
| S2c | sibling copies as one body: the *exact* static correspondence between k copies of one template -- bases and chain relation from the post-init **image**, one gapped opcode alignment per pair, a family only while every copy's operand map is a function -- then the fold making the copy index a value: one body under `v` with a per-copy column table | `siblings.py`, `copyrows.py`, `copymerge.py` |
| S3 | storage typing: regions, kinds, strides, fields, envelopes, origins | `regions.py` |
| — | front end → IR: one procedure per CFG procedure, one block per node, every memory op typed | `build.py` |
| S4 | SSA over registers/flags/uniques, DCE, copy/constant propagation, 6510 idiom peepholes, then stack elimination: frames are values and the machine stack goes | `ssa.py`, `idioms.py`, `frames.py`, `stack.py` |
| S5 | structuring: loops, if/else, switch, counted `for` (over a recurrence's domain, over a family's copies where a latch steps the index or k prologues name it, or over a *repeat count* whose bound is an expression), the phase; a statically closed arm nests in its branch and owns no dominance | `structure.py`, `loops.py`, `graph.py` |
| S6 | presentation over a view: value inlining, machine-texture removal, the three readings of one storage cell (mirrors, a slot stored once, the value a read-modify-write leaves), the rewrites the certified IR's intervals prove (masks, comparisons, the borrow a two-armed branch hides), naming a residual program's frames, region typing by accessor shape, 16-bit views, the per-copy columns as the operands they stand for, struct views (record and transpose splits) and roles, outlining, shared tails, dead values and the copies a join leaves, then the SID's own 16-bit registers as one write apiece | `inline.py`, `texture.py`, `cells.py`, `gated.py`, `ranges.py`, `frame.py`, `partition.py`, `halves.py`, `word.py`, `copyview.py`, `recover.py`, `facts.py`, `views.py`, `fold.py`, `tails.py`, `unroll.py`, `live.py`, `cellref.py` |
| S7 | Python code generation, the certificate document, the `tuneprog.md` text form -- `meta`, `state`, `data` (every table's bytes, reach and accessors), `inputs`, then one procedure each | `emit.py`, `pseudocode.py`, `printer.py`, `datablock.py` |
| S8 | per-call differential verification against the trace, periodicity, chunked and resumable; a second entry replays at the traced schedule's store granularity | `verify.py` |
| — | the facts a headless Ghidra needs from the trace, and the oracles that compare the two ([`ghidra-highpcode-export.md`](ghidra-highpcode-export.md)) | `ghidra_facts.py`, `ghidra_compare.py` |

`pipeline.py` drives every stage into one output directory; `resume.py` decides
what a resumed run may keep. `ir.py` and its reference interpreter `interp.py` are
the semantics every other executor is checked against; `irwalk.py` and `graph.py`
are the IR and CFG traversals every stage shares.

## Module map

```
front end    machine 305  cia 248  nmi 204  tracevm 488  tracesite 185
             traceflow 101  trace 464  tracedata 448  lift 227  cfg 351
             regions 243  jumptab 373  siblings 476  closure 347
             copyrows 452  copymerge 165
program      ir 464  interp 288  irwalk 349  graph 88  lower 266  build 482
             wire 78  ssa 431  frames 409  stack 218  idioms 402  emit 403
             verify 423  period 113
presentation structure 413  loops 393  inline 192  texture 308  cells 275
             frame 51  partition 231  halves 240  word 259  fold 472
             tails 290  copyview 312  unroll 414  live 249  facts 302
             recover 419  views 272  gated 130  ranges 76
text         cellref 311  pseudocode 233  printer 468  datablock 246
driver       pipeline 491  resume 67  __init__ 138
oracle       grid 157  tunes 60
baseline     ghidra_facts 219  ghidra_compare 182   60 modules, 17,366 lines
```

Stage entry points, which are also the module boundaries:
`machine.find_entries`, `nmi.entry`, `trace.run_trace`, `lift.lift_trace`, `cfg.build_procs`,
`regions.build_regions`, `build.build_ir`, `ssa.simplify`, `stack.eliminate`,
`emit.emit_python`,
`verify.verify`, `siblings.correspond`, `copymerge.plan`, `structure.structure`,
`recover.recover`, `copyview.expand`, `partition.repartition`, `views.decorate`,
`printer.render`, `datablock.section`.

## Use

```bash
deity-informant tuneprog TUNE.sid --out DIR \
    [--song N | --songs all] [--seconds S | --calls N | --until-period] \
    [--sid-model 6581|8580] [--no-merge] [--closure trace|static] \
    [--resume] [--budget S] [--no-verify] [--no-text] [--ghidra-facts]
```

- **S2c fold**, on by default: the certified program (`tuneprog.S4.json`,
  `tuneprog.py`, `certificate.json`) holds one body under the copy index `v`, every
  operand the copies disagree on read from a per-copy column `T_x[v]`.
  `--no-merge` builds the unfolded S2b form. What folded, refused or ran in no
  copy: the `copies` line of `tuneprog.md`, the certificate's `copies`,
  `tuneprog.S6.json`.
- **`--closure static`** decompiles the untaken branch directions the post-init
  image states, as code no execution covers. Off by default: it removes nearly
  every `trap 'untaken'` and costs the *covered* program its structuring (below).

`tools/tuneprog_certify.py` is the same pipeline standalone. Both are chunked: a
long run exits 2 while work remains, so each invocation stays inside `--budget` CPU
seconds.

```bash
until python3 tools/tuneprog_certify.py TUNE.sid --out out/tune --until-period --resume
do :; done
until python3 tools/tuneprog_recert.py --out out/recert --resume; do :; done
until python3 tools/tuneprog_period.py TUNE.sid --song 1 --calls 60000 \
    --out out/period --resume; do :; done
```

- `tuneprog_recert.py` reproduces every certificate under `docs/certificates/` from
  the run each records and diffs it field for field, ignoring the timestamp and the
  two timing fields.
- `tuneprog_period.py` says why a subtune has no state repeat (`period.py`): each
  footprint cell's smallest period, the loop the SID stream has, and the cells whose
  period does not divide it. Verdict `periodic`, `state only` (the blockers never
  reach the SID, so a reduced state could certify the tune) or `aperiodic` (the SID
  stream itself does not repeat). The window must cover at least two loops.
- `tuneprog_floor.py OUTDIR` measures an output directory against its tune: the
  load band split into executed code, data the trace reached and neither; `xz -9e`
  of each of those, of the print, of the executable and of the SID write log;
  printed statements by code range (`--code LO-HI:NAME`) and kind; and every
  `(b, b+1)` pair the print reads at one index, with the addresses reached and
  whether any is ever written
  ([prototype-commando-floor.md](prototype-commando-floor.md)).
- `tools/survey/tuneprog_sweep.py` runs the pipeline over design §9's stratified
  7,023-tune HVSC sample (resumable, parallel); `tools/survey/tuneprog_report.py`
  renders [survey-tuneprog.md](survey-tuneprog.md).

Every tune the certificates and tests name lives once in
`deity_informant/tuneprog/tunes.py`: a certificate's `tune` field is a basename,
that map's key, and `tunes.resolve` finds the file under `$HVSC` or in the
`$DEITY_ORACLE_CACHE/hvsc` fetch cache. A hermetic test refuses an HVSC path
written anywhere else.

Artefacts in `--out DIR`:

| file | stage |
| --- | --- |
| `trace.json`, `trace.npz` | S1 (structure, bulk arrays) |
| `regions.json`, `procs.json` | S3, S2b |
| `tuneprog.S4.json` | the certified program |
| `tuneprog.py` | generated Python, one function per procedure |
| `certificate.json` | S8 |
| `tuneprog.S5.json`, `tuneprog.S6.json` | the structured shape, the recovered names and group views (with the fold's own counts) |
| `tuneprog.md` | the pseudocode |
| `state.json`, `tracer.pkl`, `verify.pkl` | resume state (a `--songs all` run records each subtune's ticks, stop reason and horizon) |

Python API:

```python
from deity_informant.tuneprog import pipeline
from deity_informant.tuneprog.tracedata import Trace

trace = Trace.load("out/tune")
prog, regions, procs = pipeline.build(trace, "TUNE.sid")            # S2..S4, copies folded
view, structured, names = pipeline.present(prog)                    # S5/S6
```

## Certificate schema

```jsonc
{
  "tune": "Automatas.sid",             // the file's basename
  "sid_model": null,                   // "6581"/"8580" when $D41B bit 0 was pinned
  "oracle": "deity_informant.PcodeVM@0.5.0",
  "reference_validated_against": "none",
  "compared": ["init writes", "tick sid writes", "tick schedule effects"],
                                       // + "nmi preemption schedule" and "nmi store
                                       // separability" with a second entry
  "entry": {"kind": "sub", "addr": 4067, "cycles_per_tick": 2457, "source": "cia_timer"},
                                       // "irq" also carries "kernal": the vector is CINV
  "schedule": [                        // only where a CIA #2 NMI is an entry too
    {"kind": "irq", "addr": 16352, "cycles_per_tick": 19656, "source": "pal_video",
     "kernal": false},
    {"kind": "nmi", "addr": 16617, "cycles_per_tick": 193, "source": "cia2_timer_a",
     "kernal": false, "replayed_registers": 1197084}],
                                       // kernal: which vector dispatched it ($0318 costs
                                       // 7 cycles more than the raw $FFFA)
                                       // replayed_registers: SP, pushed status, return pc
                                       // and A/X/Y taken from the schedule instead of
                                       // computed, 6 per NMI, beside inputs_pinned
  "stack": "eliminated",               // else {"depth": n|"unknown", "procs": [...]}
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
      "closure": "trace", "inputs_pinned": 2228, "interp_prefix": 2000,
      "nmis": 199514, "nmi_entries": ["nmi"]   // only with a second entry
  }],
  "closure": {                         // only under --closure static
    "arms": 22, "closed": 17,          // untaken branch directions found / closed
    "instructions": 39,                // instructions the image stated
    "stops": {"smc_cell": 4, "stack": 1},      // where the walk refused, by reason
    "blocks": 9, "statements": 57,     // what only a closed path reaches
    "verified_statements": 745,        // the rest of the program
    "untaken": 8, "frontier": 0        // traps left: directions, `trap 'unstated'` paths
  },
  "copies": {                          // only where a family folded or refused
    "families": [{"proc": "tick", "bases": ["$12BE", "$12EF", "$1320", "$1351", "$1382"],
                  "copies": 5, "rows": 18, "columns": 3, "table": "$0200"}],
    "refused": [{"proc": "p_1022", "base": "$112A",
                 "why": "an edge from copy 0 enters copy 1"}],
    "statements": 52,                  // statements inside a merged block
    "unverified": 30,                  // of those, in a block some copy never ran
    "coverage": {"1,1,1,1,1": 22, "1,0,0,0,0": 12, "0,1,1,1,1": 18}  // by copy pattern
  },
  "generated": "2026-08-16T20:12:05Z"
}
```

- **`ticks`** is what was *verified*, not traced: a horizon is reached on a chunk
  boundary, so the trace can hold up to `--chunk` ticks more (`cost.trace_calls`
  sums the verified counts). Those extra ticks add nothing after a state repeat,
  and on the tick horizon the two counts are equal. A machine-readable "built from"
  count is backlog.
- **`complete`** means the run closed: a state repeat at the same tick and period
  as the trace, no divergence; a subtune stopping on a repeat certifies
  `first_repeat + 1` ticks. Otherwise the program is certified to its horizon.
- **`stack`** is `"eliminated"` when no machine stack is left: every push is a value
  its pops read, a return address is the continuation the `Call` carries, no
  procedure takes or returns `SP`. It is `{"depth": n | "unknown", "procs": [...]}`
  when a procedure reads stack bytes its own frame did not write (a scratch area at
  a non-constant offset, a `TSX`-relative read of another frame, the pointer used as
  data); such a read can see any byte of the page, so the whole program keeps it.
- **`copies`**: `rows` instructions folded, `columns` operands the copies disagree
  on, each a per-copy entry at `table`; `coverage` counts merged statements by which
  copies ran them. Discovery reads the image, not the blocks a build happened to
  make, and counts are S2b's per-instruction site counts, so the families are the
  same under either `--closure`. A family the index cannot name is `refused` with
  its reason and stays k bodies.
- **`closure`** (under `--closure static`) counts the bounded static walk of the
  untaken directions: `arms`/`closed` the directions, `stops` why the rest refused,
  `blocks`/`statements` what *only* a closed path reaches — each printed
  `# unverified (static closure)` — `untaken` the traps left, a direction the
  walk refused or the arms of a folded row no copy ran, and `frontier` the paths the
  walk ended in `trap 'unstated'`, its frontier trap where the image is silent. The
  subtune's own `closure` field then reads `static`; the default `trace` is the
  trace-closed program, every statement covered by execution. Closed code is reachable
  only through edges that were traps, so the state hashes, `period`, `complete` and
  `divergences` are the same certificate's.

The committed certificates are all trace-closed. Measured at 30 s of music,
`--closure trace` → `--closure static`:

| exemplar | printed lines | `goto` | `trap 'untaken'` | closed statements |
| --- | ---: | ---: | ---: | ---: |
| Automatas | 772 → 864 | 6 → 6 | 18 → 5 | 59 |
| `gt2-je-suis-linus` | 1,170 → 1,104 | 0 → 16 | 15 → 0 | 23 |
| `ghouls-song01` | 747 → 832 | 27 → 39 | 28 → 3 | 76 |
| `sw-emomyst` | 1,316 → 1,854 | 0 → 2 | 49 → 1 | 114 |

Measured before the untaken direction became a mark: the trap column is now
`meta`'s `untaken` row, which the mark leaves unchanged, and the printed-line
column falls with the arms. The traps go and the *covered* program is structured
worse, so the default stays `trace`. A closed arm costs nothing itself (S5/S6 work on the covered subgraph
alone, `graph.edges_of`); the cost is the covered *graph*, where a closed path
rejoining inside a covered block splits it and the extra predecessor stops
`merge_chains` gluing the pieces.

### Cadence

`cycles_per_tick` and its `source` are what triggers the play interrupt:

- a tune programming an armed CIA Timer-A latch of its own keeps it (`cia_timer`,
  period `latch + 1`);
- otherwise the trigger is the host's: `sidplayfp`'s PSID driver rasters at a video
  frame (`pal_video` 19,656, `ntsc_video` 17,095) unless the header `speed` bit for
  *that subtune* selects its CIA, and an RSID runs the real KERNAL, whose default
  IRQ *is* that CIA unless the tune armed a raster compare of its own;
- either host CIA is Timer-A at the latch the KERNAL and `psiddrv` leave, `$4025`
  PAL and `$4295` NTSC: `pal_host_cia` 16,422 cycles, `ntsc_host_cia` 17,046;
- `speed` is a bitfield (bit *n* is subtune *n*, subtunes past the 32nd sharing bit
  31), so cadence is per subtune: `--songs all` refuses a tune whose subtunes
  disagree, one merged trace being one schedule.

### The entry frame

An `irq` tick is entered with the frame the machine pushed, and that frame is the
tick's **contract**, not storage: every byte is a parameter, the terminating `RTI`
consumes exactly those bytes, and the interrupt disable is the tick's first
statement (`build._irq_entry`). Which bytes they are is the entry's `kernal` field
(`machine.entry_frame`, read by the tracer, `verify._enter` and `frames.contract`):
a **raw** vector (`$FFFE`) is entered by the 6510 alone, so `SP+1` is the entry
flags packed (`lower.status_expr`); a **CINV** entry (`$0314`, `kernal: true`) comes
through the KERNAL prologue at `$FF48`, which saves A, X and Y on top of that byte,
so `SP+1..4` = entry Y, X, A, status — what `$EA31`/`$EA81` pop before their `RTI`.

Which applies is the 6510 port's word, not the tune's (`machine.vector_gate`): with
HIRAM set the CPU takes its vector from the KERNAL's own `$FFFE`, so a write to
`$FFFE` went to the RAM under the ROM; with HIRAM clear that RAM *is* the vector
and no prologue runs.

| installed | KERNAL mapped (HIRAM) | KERNAL banked out |
|---|---|---|
| CINV only | CINV, `kernal: true` | refuse `vector banked out` |
| `$FFFE` only | refuse `vector banked out` | raw, `kernal: false` |
| both | CINV, `kernal: true` | raw, `kernal: false` |

`find_entries` runs the gate on the pre-init image; `Tracer.run_init` re-runs it
once init has had the port, and that verdict is what the certificate carries. The
contract must hold at *every* tick, so a tick entered with the port on the other
side of HIRAM refuses (`port moved`). A tick that reads the pushed return address,
reaches the status by a route no slot places (`TSX`), or is also called by another
procedure gets no contract and is residual. `depth` is the deepest slot below an
entry pointer the analysis placed, `"unknown"` where an access is not a slot.

Not modelled: the rest of `$FF48`, which also leaves A = 0, X = SP and Z set
(`TSX; LDA $0104,X; AND #$10`), where the tracer hands the handler the registers
the previous tick left. Over the 37 `play == 0` PSIDs of HVSC `MUSICIANS/A`-`C`:
31 read no entry register, 2 read A and see the 0 the KERNAL would leave, 4 (Boray)
read A/X/Y live-in and would see other bytes on hardware. Modelling `X = SP` would
make every such tick residual, so it waits for a tune that discriminates against
the oracle ([prototype-kernal-entry.md](prototype-kernal-entry.md)).

### The 6510 port and the two footprints

The port decides what `$D000-$DFFF` is at every access: the pre-init image carries
what a KERNAL-initialised host leaves (`$00 = $2F`, `$01 = $37`), the program's own
machine carries those two bytes (`image_port`), and the tracer records which
`(pc, op)` pairs reached a chip. An address only ever touched with I/O banked out
is the RAM under the chip: ordinary storage, accesses `ram`/`chk`, and a region
under the SID register file takes the `sid_image` role at delta 0 (`ghost.reg[i]`).
An access that did reach the chip keeps the `io` class in the same region, so a
write with I/O mapped is still a SID write ([prototype-jch.md](prototype-jch.md)).

The tracer hashes **two** footprints per tick, since only S4 can say which one a
certificate may claim periodicity on: the whole play-written set, and that set
without the stack page. An eliminated-stack program claims on the page-exclusive
stream, a residual program on the page-inclusive one, a stack byte it reads back
being state like any other. `--until-period` stops at the earliest repeat of
either, which S4 may reject (recorded as `"stack"` in `state.json`); a program it
calls residual goes back to S1 and traces on to the page-free witness
(`pipeline._horizon_stage`). One rule (`_certified` over `Trace.witness(free)`, the
verdict from `verify.page_free`) covers the single-song path and `--songs all`
alike. Eliminating a stack therefore moves no period or divergence and can only
shorten a horizon: `gt2-do-it-again` closes at 8,659 ticks instead of 9,956, same
period (8,640), still `complete`.

## The register grid

`grid.py` frames any write stream carrying cycles — the tracer's `wlog` or a
`sidtrace` CSV — into a per-frame `$D400..$D418` grid by attributing each write to
the interrupt period its cycle falls in. A tick is not instantaneous (Puterman's
V20 wrapper spends 168 → 10,248 cycles between a tick's first SID write and its
last), so a grid keyed by call index is not the grid a sampler read. The boundary
is the interrupt, which the tracer sets (tick 0's cycle, then `cycles_per_tick`)
and the CSV states (`cycle - since_video_irq`); no sample point is chosen. Framed
that way the tracer and `sidplayfp` agree on **3,000 of 3,000** frames of the Knob
(`tests/test_oracle.py`); against the oracle framer's half-frame anchor
(`grid_from_writes`) both of the trace's rules — by cycle and by call index —
differ on the same 297 frames, which is the anchor and not the writes. `tick_grid`
is the by-call view, kept for that comparison.

## Certified exemplars

Numbers from `docs/certificates/`. `complete` = certified to a state repeat;
`horizon` = certified to the tick count shown.

| certificate | tune | player | ticks | music | period | procs | blocks | stmts | regions | certified |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `automatas` | Automatas.sid | defMON | 149,025 | 6m12s | 129,024 | 8 | 228 | 733 | 80 | complete |
| `automatas-6581` | Automatas.sid | defMON, `$D41B`=0 | 149,025 | 6m12s | 129,024 | 8 | 228 | 733 | 80 | complete |
| `automatas-8580` | Automatas.sid | defMON, `$D41B`=1 | 149,025 | 6m12s | 129,024 | 8 | 227 | 722 | 80 | complete |
| `commando-song1` | Commando.sid | Hubbard | 11,780 | 3m55s | — | 3 | 115 | 341 | 58 | horizon |
| `commando-song2` | Commando.sid | Hubbard | 11,780 | 3m55s | — | 3 | 101 | 278 | 61 | horizon |
| `ghouls-song01`…`32` | Ghouls_n_Ghosts.sid | Follin | 6…20,049 | — | 1…8,064 | 2–4 | 101–275 | 190–671 | 37–70 | complete (31 of 32) |
| `ghouls-songs-all` | Ghouls_n_Ghosts.sid | Follin, all 32 subtunes | 111,763 | — | per subtune | 4 | 299 | 770 | 45 | complete (31 of 32) |
| `gt2-je-suis-linus` | Je_suis_Linus_le_salaud.sid | GoatTracker 2 | 8,236 | 2m44s | 6,720 | 14 | 245 | 526 | 73 | complete |
| `gt2-do-it-again` | Do_It_Again.sid | GoatTracker 2 | 8,659 | 2m53s | 8,640 | 14 | 234 | 516 | 73 | complete |
| `jch-knob-at-night` | I_Could_Eat_a_Knob_at_Night.sid | JCH NewPlayer V20 + a banking wrapper | 8,577 | 2m51s | 1 | 9 | 155 | 472 | 99 | complete |
| `jch-guldkorn-intro` | Guldkornekspressen_Intro.sid | JCH NewPlayer V20 | 2,401 | 0m48s | 1,512 | 2 | 160 | 443 | 103 | complete |
| `sw-emomyst` | Emomyst.sid | SID Wizard 1.6 | 8,084 | 2m41s | 6,120 | 15 | 365 | 951 | 96 | complete |
| `sw-end-of-the-world` | End_of_the_World.sid | SID Wizard 1.9 | 14,465 | 4m49s | 7,688 | 16 | 361 | 935 | 94 | complete |
| `rodger-alien3` | Alien_3.sid | Andrew Rodger, hardware-vector entry | 1,503 | 0m30s | — | 7 | 148 | 505 | 25 | horizon |
| `goto80-jazzpjazz` | Jazzpjazz.sid | defMON | 1,799 | 0m30s | — | 4 | 190 | 629 | 95 | horizon |
| `necropolo-experiment-zeta` | Experiment_Zeta.sid | Virtuoso | 5,956 | 1m58s | 5,184 | 2 | 185 | 356 | 65 | complete |
| `daglish-deflektor` | Deflektor.sid | Ben Daglish/Gremlin | 1,503 | 0m30s | — | 4 | 180 | 630 | 78 | horizon |
| `jch-easy-does-it` | Easy_Does_It.sid | JCH NewPlayer V20 + a CIA #2 NMI sample mixer | 1,799 | 0m36s | — | 5 | 211 | 669 | 107 | horizon |

Every one has `divergences: 0` and `envelope_traps: 0`.

- `jch-easy-does-it` is the two-entry one: 199,514 NMI preemptions over 1,799
  ticks, each where the trace put it ([prototype-nmi.md](prototype-nmi.md)).
- `jch-knob-at-night`'s period of 1 is a song that *stops*: its tracks end and the
  state is a fixed point from tick 8,576 on.
- `ghouls-song21` has no state repeat inside 400 s (two voices keep a portamento
  and a trill moving): certified to a 20,049-tick horizon.
- Both `commando` subtunes are `aperiodic` too, measured over 60,000 ticks: song 1's
  patterns loop at 11,808 ticks, but its three pulse-width accumulators
  (`$5591`/`$55A1`/`$55B9`, each `pw += rate` a tick) do not return — 41,898 of
  48,192 tick write lists differ at that lag, on `$D410` and its two siblings.
  Hubbard's free-running frame counter `$5525` (period 256, `+32` a loop, read only
  as `& 1` and `& 7`) is not the blocker: masking it to its residue still leaves the
  accumulators, whose full byte *is* the SID write, and in song 2 its period already
  divides that subtune's loop.
- `rodger-alien3` and `goto80-jazzpjazz` are the dead-NMI pair: a written NMI vector
  and a written CIA #2 Timer-A latch that no armed source can dispatch, so neither
  is a second schedule. Neither repeats (both traced to the 400,000-tick cap), so
  both are certified to a 30 s horizon; `sidplayfp` confirms *Jazzpjazz*'s tick, the
  gaps between the interrupts it attributes its writes to being whole multiples of
  the host CIA's period, not of the CIA #2 latch the tune loads. Both pin inputs no
  other certificate does: *Alien_3* enters through the hardware vector, where no
  KERNAL prologue saves them, so the handler's A/X/Y are live-in and pinned per tick
  (6,013 in all), and *Jazzpjazz* polls the raster (2,226). *Alien_3* is also the
  only certificate whose entry frame is the bare `RTI` status byte.
- `necropolo-experiment-zeta` and `daglish-deflektor` are the patched-dispatch pair,
  the two commonest ways a player computes its own control flow: Virtuoso patches
  the *operand* of a `JMP (ind)` and jumps through the word that operand addresses
  (a load through a load), and Daglish's engine writes `(cmd & $60) >> 3` into the
  offset byte of an always-taken `BEQ` and lands on one of four `JMP`s two bytes
  apart, so one arm has offset zero and lands where the untaken direction would.
  Both were the campaign's largest divergence class until the front end read them
  ([survey-tuneprog.md](survey-tuneprog.md) §4). *Experiment Zeta* closes on a
  5,184-tick period; no Daglish tune does — all twelve sampled at `--until-period`
  stop on a `trap 'unreached'` in a `stack: residual` program.

## Known gaps

- **A second entry's instant, not its effect.** A CIA #2 NMI is certified but lands
  tens of cycles early, from the `$FE43` KERNAL dispatch stub (`nmi.KERNAL_STUB`,
  modelled; it was half the offset on the `$0318` path, 85 % of the class by weight)
  and unmodelled VIC DMA. Play-routine writes are unaffected (0 frames differing
  against `sidplayfp` over 1,500 frames on three tunes, in write order); `$D418`,
  written thousands of times a frame by a sample mixer, lands on the neighbouring
  sample nibble in 10-54 % of frames. The certificate claims the write list *under
  the traced interleaving* only ([prototype-nmi.md](prototype-nmi.md)).
- **Trace closure.** A branch direction or table entry the run never took becomes
  `trap 'untaken'` / `trap 'unverified'`, not a decompiled path; `--closure static`
  decompiles what the post-init image states, at the cost measured above. A branch
  whose one direction is a bare untaken trap prints as a `# untaken: <condition>`
  mark on the first line the covered direction reaches, and `meta` carries the
  count; a switch arm still prints its trap.
  `jumptab` closes a patched jump over the table's observed extent — 14 of 16 arms
  in GoatTracker's tick-0 table, entries no accessor reached lying outside the
  region. Two bounds narrow that extent without adding candidates: a merged
  family's k tables are parallel, so they start at the same index — the region's own
  base, the lowest of the k bases, names it — and the branches on the one path into
  the dispatch prove a range for the index (Follin's `BPL` puts its command table at
  128 and up). The layout is an inference, the range a proof, applied last. A folded
  writer names cell and table base through per-copy columns, so copy *j*'s writer is
  that expression with each column read replaced by its *j*th entry, and the same
  enumeration runs per copy (Follin song 1: 21 arms per voice).
- **A merged row a copy never ran is unverified code**: the statement the trace saw
  in another copy, at the address the correspondence gives this one. Coverage 0,
  counted in the certificate, marked per statement (`ghouls-song01`: 133 of 471).
- **A copy is found by its static shape, so a short horizon finds less of it**: over
  30 s of *Automatas* the two cascade runs fold and two smaller families refuse.
- **What the copy index cannot name refuses**, the reason in the certificate and the
  printed header: a cross-copy edge refuses the family whole; a row whose copies do
  not lift to one shape, an opcode cell inside a copy, or a successor named
  differently stay k rows under a `switch (v)` whose unrun copies trap. A refused
  family keeps S2b's *code* but not the region typing, which a folded access unites
  once for the whole program. A copy holds only what its rows hold, so the front end
  enters at the first row with `v` that row's copy; the k prologues *are* the loop's
  step, and `loops.copies` reads the chain off the assignments to print
  `for v in 0..k-1` where no latch steps the index. Jumps into a prologue stay
  `goto`.
- **A column prints as the operand it stands for** (`copyview.py`): an affinely
  stepping column becomes that step in `v` under the stride vocabulary
  (`sid[v].freq_lo`, `b640F[v]`), any other keeps its read and prints through the
  group view `views.py` names, `voice[v].field`. The read stays because copy 0's
  operand cannot be told from an operand every copy agrees on holding that address.
  `unroll`, which has no column to keep, refuses a run outright where a cell every
  copy names equals one the run relocates. What no rule names keeps its table read
  with the address visible (two of Follin's 60 columns, two of *Automatas*' five).
- **One accessor-shape enumeration** (`irwalk.accessors`): every load and store as
  `Acc(proc, region, store, base, idx, lo, hi)`, the address split included. Beside it
  sit `Rgn.extent` (the one containment test, in bytes from the region's zero),
  `facts.per_region` (the one reading of what the indices walking a region carry),
  `facts.unclaimed` (already named by some view), `ir.rgn_name`, `ir.overlaps` (regions
  whose extents overlap, as runs) and `copyview.remap_cells`/`fold_fields` (the one
  owner of a fold's cell keying). The partition's claims, `views.record_split`'s fields
  and `datablock`'s reach are all read from them.
- **Region typing is an S6 view, not the certified typing.** S3 unions the addresses
  one op touched, so one over-reaching accessor fuses unrelated storage into one region
  typed by the coarsest kind. `partition.repartition` re-types the *copy* the
  presentation runs on: an envelope starting at the access's own operand and spanning
  more than a byte names an array, a constant address a scalar, a reach starting inside
  the region nothing; the narrow claim wins, and the overrunning access keeps the fused
  region, which is the bound its envelope asserts. A part no store's envelope reaches is
  `const` beside a `state` neighbour; stride-1 regions of one kind sharing an origin
  whose extents overlap merge. A record view (`views.record_split`, a copy fold's
  field, the register image) already partitions what it names, and claims of one
  width at one spacing are a record, so both refuse. S4's ids and `regions.json` are
  untouched; the print gains the storage *named*, at one header row per part (2,894 →
  3,084 regions over the 51 certificates). A cut parent is never retired -- `_disagree`,
  the condition for cutting at all, is an access contained in no claim, and that access
  cannot move into a part -- so the parent keeps the fused range it asserts and its
  parts' ranges lie inside it, those bytes listed twice.
- **Data prints as data** (`datablock.py`). `## data` replaces `## const`: every run
  of storage the program reads prints its own bytes. A region's cells are its extent,
  or the columns its stride marks off; its *reach* is the union of its accessors'
  envelopes over them, and a cell some store's envelope reaches is state, not data --
  `partition._part_kind`'s rule read per byte, which is why a frequency table a 25-note
  overrun fused into a `state` region still prints (Commando's `FREQ`). Regions whose
  extents overlap, or that one frequency layout names, are one block, so three extents
  of one pattern array print once and a `lo|hi` table's two columns print as one entry
  list. The layout is the S6 names' own: a note table as 16-bit entries, equal-stride
  columns as one row per record under their field names, everything else as hex rows
  of 32 bytes; then one line per distinct *printed* accessor (`FREQ[t7 << 1]` in
  `oscillator`), which is why `render` renders the procedures before the header. Over
  the 51 certificates it carries 189,194 bytes in 15,903 rows, and no tune's program
  text moves; header rows fall 5,065 -> 4,737, one block naming what a row per region
  named. Refused: an `init_constant` region, whose value init computes and the
  *pre*-init `Tuneprog.image()` does not carry (15,249 bytes over the 51, 2,308 of them
  SID Wizard's unpacked tables); and a region no accessor reaches, whose only read S4
  folded to a literal. `datablock.reach_bytes` is that reach and
  `tools/tuneprog_floor.py` now calls it, so the tool and the print count one thing:
  Commando 1,941 bytes, against the 1,942 the region *extents* gave (the one byte is
  `$5526`, read by nothing in the S4).
- **Names are role-derived**: `timer_2`, `cursor_1490`, `b148D`. A per-family
  dictionary keyed on the player signature would name them from the original source.
- **A 16-bit view is a pair of cells.** A cell is `(region, constant address)`, and a
  pair is two cells one index expression reaches at two constant bases — so one region
  may hold both halves (Follin's zero page), and `names.u16` is keyed by the pair.
  `halves.py` is the byte vocabulary: the carry chain `lo = x + y; hi = x' + y' +
  carry(x + y)`, the borrow chain with its `1 - (a cmp b)` folded to the negated
  compare, the rotate `(lo >> 1) | ((hi & 1) << 7)`, and the read `(hi << 8) | lo`.
  `word.py` applies them to the statements of one block and to the `lo += 1;
  if lo == 0: hi += 1` diamond, where `(x + 1) == 0` is the carry it is. A half that is
  computed and not stored (its value handed to a callee that stores it) pairs through
  the load it makes of the other cell, and a stored half wins over a computed one. A
  pair with one half inside the I/O band and one outside is refused: a chip register is
  not memory. The carry the chain hands a third byte keeps the flag's name. Halves
  stored by unrelated instructions stay two bytes.
- **The SID's own 16-bit registers print as one write, under a stated order**
  (`word.fold_sid`, `halves.register`). `freq`, `pw` and `cutoff` are one register the
  chip's 8-bit bus takes two writes to set, so `sid[v].freq = f` is a *print*
  convention over what the executable does; `verify._compare` is over the executable's
  ordered byte writes and is untouched. The `meta` block states the order once —
  `sid  16-bit registers written lo then hi` — and a write in the other order carries
  `# hi then lo` on its own line. Three conditions: the value must be a word the
  program already holds (bytes the print would join with `|` and `<< 8` are two bytes
  that happen to reach one register), both cells must be in an `io` region (the RAM
  under the register file is memory — JCH's Puterman build writes `ghost[v].freq_lo`),
  and the fold runs *after* `unroll`, over the aligned rows: folding it earlier
  shortens the per-voice run the register-file loop is built from and the loop
  realigns (#277's refusal, *Automatas*' `writeout` 3 rows → 2 + 1).
- **A callee's return value does not print.** `ir.retval` recovers the tick's own
  return; a procedure computing a byte for its caller prints an empty body.
- **Sign extension and flag algebra print as written**: a patched branch dispatcher
  keeps `(base + off) - ((off & $80) << 1)`, and a `BVC` after `SBC` prints the
  overflow expression rather than the bit test.
- **Periodicity is a hash of the whole footprint, so one drifting cell hides it**,
  pushing the state period to the lcm of the cell's and the music loop's. A
  *reduced* hash would certify a tune whose observable-affecting reads of that cell
  all go through a mask (`& 7`, `& 1`), but nothing certified needs one: both
  `commando` subtunes and `ghouls-song21` are `aperiodic`, the drifting cell's full
  byte being itself a SID write. The classifier is fail-closed: a read shape it
  cannot classify keeps the whole cell.
- **Refusals** are diagnosed, never approximated: a second armed interrupt source, a
  recursive JSR call graph, an `init` that never returns inside its budget, a play
  routine past its instruction budget. *Armed* is the chip's rule, not the evidence:
  CIA #2's interrupt line is the 6510's NMI, so a second source exists iff CIA #2's
  ICR (`$DD0D`) enables one of bits 0-4 (accumulated over the writes, bit 7 saying
  whether a write enables or disables what it names) and, for a timer source, iff
  that timer is started (`$DD0E`/`$DD0F` bit 0). A `$DD04`/`$DD05` latch or a
  `$0318`/`$FFFA` vector over no such source is dead, as `vector_gate` treats a dead
  `$FFFE` write, and by the same rule a CIA #2 period is never a play cadence. The
  gate is re-checked every tick beside `port moved` (`nmi armed in play`). The one
  assumption is the oracle's: `sidplayfp` never presses RESTORE. Two refusals are
  the second entry's own checked properties, both fail-closed and checked on every
  NMI of every tick ([prototype-nmi.md](prototype-nmi.md) §4): `schedule not
  store-separable`, a play load in an open preemption window reading a cell a
  handler stamped in that window (5 of 7,023); and `nmi clobbers registers`, the
  handler's `RTI` not returning the A/X/Y it interrupted (8 of 7,023).
