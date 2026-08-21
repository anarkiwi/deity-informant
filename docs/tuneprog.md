# tuneprog — the SID decompiler

`deity_informant/tuneprog/` turns a `.sid` file into a **certified per-tick program**:
an executable IR whose SID writes, schedule effects and state footprint match the
traced playroutine tick for tick, plus a readable pseudocode form of it.

Design: [`tuneprog-decompiler-design.md`](tuneprog-decompiler-design.md).
Exemplar write-ups: [automatas](prototype-automatas.md), [follin](prototype-follin.md),
[goattracker](prototype-goattracker.md), [sidwizard](prototype-sidwizard.md),
[jch](prototype-jch.md), [kernal-entry](prototype-kernal-entry.md).
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
| **split view** | a block one init loop made one region, cut into the fields the play phase walks: by a *record* stride (element index outside, field inside) or by its **transpose** (field outside, element index inside, which is a struct-of-arrays) |
| **voice map** | a read-only table holding `0, 7, 14` -- the SID's own voice -> register-block offsets, so an index read from it is a voice |
| **phase** | the state scalar a tick tests to pick its rate (defMON's `& 7` call counter) |
| **tick** | one call of the play entry, at the cadence S0 discovered; the horizon flag spells it `--calls`, the certificate field `ticks` |
| **header** | P/SID metadata is known unreliable (design: "metadata, not ground truth"); it seeds discovery, the traced machine decides, the `sidplayfp` oracle arbitrates |
| **certificate** | `certificate.json`: what was compared, over how many ticks, with what divergence and periodicity |

## Pipeline

| stage | does | modules |
| --- | --- | --- |
| S0 | load image, entry and cadence discovery, init runner, 6510 port + CIA models | `machine.py` |
| S1 | op-level tracing: sites, edges, calls/returns, exact per-op access sets, pinned inputs, reference write log, per-tick state hashes | `tracevm.py`, `trace.py`, `tracedata.py` |
| S2a | residualised lift: an SMC operand becomes a load of its cell | `lift.py` |
| S2b | procedures from observed edges: clone per entry, tail calls, variant and computed switches | `cfg.py`; static table closure in `jumptab.py`, over a per-copy column base and the range a branch proves for the index |
| S2b' | the bounded static closure of untaken branch directions, as zero-coverage sites the same front end builds (`--closure static`) | `closure.py` |
| S2c | sibling copies as one body: the *exact* static correspondence between k copies of one template -- bases and the chain relation read from the post-init **image**, one gapped opcode alignment per pair, and a family only while every copy's operand map is a function -- then the fold that makes the copy index a value, so the certified program has one body under `v` with a per-copy column table | `siblings.py`, `copyrows.py`, `copymerge.py` |
| S3 | storage typing: regions, kinds, strides, fields, envelopes, origins | `regions.py` |
| — | front end → IR: one procedure per CFG procedure, one block per node, every memory op typed | `build.py` |
| S4 | SSA over registers/flags/uniques, DCE, copy/constant propagation, 6510 idiom peepholes, then stack elimination: frames are values and the machine stack goes | `ssa.py`, `idioms.py`, `frames.py`, `stack.py` |
| S5 | structuring: loops, if/else, switch, counted `for` (over a recurrence's domain, or a family's copies where a latch steps the index or k prologues name it), the phase; a statically closed arm nests in its branch and owns no dominance | `structure.py`, `loops.py`, `graph.py` |
| S6 | presentation over a view: value inlining, machine-texture removal, naming a residual program's frames, 16-bit views, the per-copy columns as the operands they stand for, struct views (record and transpose splits) and roles, outlining, shared tails (exit-free, or with one way out) | `inline.py`, `texture.py`, `frame.py`, `word.py`, `copyview.py`, `recover.py`, `facts.py`, `views.py`, `fold.py`, `tails.py`, `unroll.py`, `live.py` |
| S7 | Python code generation, the certificate document, the `tuneprog.md` text form | `emit.py`, `pseudocode.py`, `printer.py` |
| S8 | per-call differential verification against the trace, periodicity, chunked and resumable | `verify.py` |
| — | the facts a headless Ghidra needs from the trace, and the oracles that compare the two ([`ghidra-highpcode-export.md`](ghidra-highpcode-export.md)) | `ghidra_facts.py`, `ghidra_compare.py` |

`pipeline.py` drives every stage into one output directory, and `resume.py` decides what a run resumed under other options may keep of it. The IR itself
(`ir.py`) and its reference interpreter (`interp.py`) are the semantics every
other executor is checked against; `irwalk.py` and `graph.py` are the IR and CFG
traversals every stage shares.

## Module map

```
front end    machine 322  tracevm 328  trace 337  tracedata 346  lift 227
             cfg 311  regions 243  jumptab 373  siblings 476  closure 347
             copyrows 453  copymerge 165
program      ir 440  interp 248  irwalk 319  graph 82  lower 227  build 452
             wire 78  ssa 431  frames 409  stack 218  idioms 401  emit 372
             verify 338  period 113
presentation structure 356  loops 307  inline 199  texture 475  frame 51
             word 369  fold 472  tails 290  copyview 279  unroll 399  live 96
             facts 284  recover 328  views 295
text         pseudocode 468  printer 405
driver       pipeline 506  resume 67  __init__ 134
oracle       grid 159  tunes 55
baseline     ghidra_facts 219  ghidra_compare 182   49 modules, 14,451 lines
```

Stage entry points, which are also the module boundaries:
`machine.find_entries`, `trace.run_trace`, `lift.lift_trace`, `cfg.build_procs`,
`regions.build_regions`, `build.build_ir`, `ssa.simplify`, `stack.eliminate`,
`emit.emit_python`,
`verify.verify`, `siblings.correspond`, `copymerge.plan`, `structure.structure`,
`recover.recover`, `copyview.expand`, `views.decorate`, `printer.render`.

## Use

```bash
deity-informant tuneprog TUNE.sid --out DIR \
    [--song N | --songs all] [--seconds S | --calls N | --until-period] \
    [--sid-model 6581|8580] [--no-merge] [--closure trace|static] \
    [--resume] [--budget S] [--no-verify] [--no-text] [--ghidra-facts]
```

S2c is on by default: where the front end proves k chained copies of one
template, the certified program holds one body under the copy index `v`, with
every operand the copies disagree on read from a per-copy column `T_x[v]`. It
changes what is certified -- `tuneprog.S4.json`, `tuneprog.py` and
`certificate.json` are the folded program -- and `--no-merge` builds what S2b
built before it, which is what the two differ by. What folded, what refused and
what no copy ran are in the `copies` line of `tuneprog.md`, in the certificate's
`copies` and in `tuneprog.S6.json`.

`--closure static` decompiles the untaken branch directions the post-init image
states, as code no execution covers (the `closure` block of the certificate, the
per-statement mark, and `closure: "static"` per subtune). It is off by default:
it removes nearly every `trap 'untaken'` and costs the *covered* program its
structuring (below).

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

Every tune the certificates and the tests name lives once, in
`deity_informant/tuneprog/tunes.py`: a certificate's `tune` field is a basename,
which is that map's key, and `tunes.resolve` finds the file under `$HVSC` or in
the `$DEITY_ORACLE_CACHE/hvsc` fetch cache. Adding a tune is one line there, and
a hermetic test refuses an HVSC path written anywhere else.

`tools/tuneprog_period.py` says why a subtune the certificate could not close
has no state repeat (`period.py`): it samples every footprint cell and every SID
write per tick and reports each cell's own smallest period, the loop the SID
stream has (if any), and the cells whose period does not divide it — a counter,
or an accumulator with a constant drift per loop. Its verdict is `periodic`,
`state only` (the blockers never reach the SID, so a reduced state could certify
the tune) or `aperiodic` (the SID stream itself does not repeat, so nothing can).
The window must cover at least two loops; sample well past the horizon.

```bash
until python3 tools/tuneprog_period.py TUNE.sid --song 1 --calls 60000 \
    --out out/period --resume; do :; done
```

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
  "entry": {"kind": "sub", "addr": 4067, "cycles_per_tick": 2457, "source": "cia_timer"},
                                       // "irq" also carries "kernal": the vector is CINV
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
      "closure": "trace", "inputs_pinned": 2228, "interp_prefix": 2000
  }],
  "closure": {                         // only under --closure static
    "arms": 22, "closed": 17,          // untaken branch directions found / closed
    "instructions": 39,                // instructions the image stated
    "stops": {"smc_cell": 4, "stack": 1},      // where the walk refused, by reason
    "blocks": 9, "statements": 57,     // what only a closed path reaches
    "verified_statements": 745,        // the rest of the program
    "untaken": 8, "frontier": 0        // traps left: directions, and stated-out paths
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

`ticks` is what was **verified**, not what was traced: a tick horizon is reached
on a chunk boundary, so the trace the program was built from can hold up to
`--chunk` ticks more (`cost.trace_calls` sums the verified counts). Where the
subtune stopped on a state repeat those extra ticks add nothing to the program --
a witness is a repeated state with no input consumed, so they replay sites, edges
and accesses the certified prefix already carries -- and where it stopped on the
tick horizon the two counts are equal. A machine-readable "built from" count is
backlog: adding the field would move all 44 documents.

`copies` is the fold S2c proved. A family is k chained copies of one template
that became one body under the copy index; `rows` is how many instructions
folded, `columns` how many operands the copies disagree on (each one a per-copy
table entry at `table`). Discovery reads the image, not the blocks a build
happened to make: a copy may open where a branch decides or where the image's
own control flow lands, and falling into the next copy is a chain edge like a
jump. How often an instruction ran is S2b's own per-instruction site count, not
anything read off the blocks. So the families are the same under
`--closure trace` and `--closure static`. `coverage` counts merged statements by which copies ran
them: a `0` is a statement the trace saw in another copy and the correspondence
says is this one's too, which the printed program marks per statement. A family
the index cannot name -- a cross-copy edge, an operand no table can express --
is `refused` with its reason, and its copies stay k bodies.

`closure` appears under `--closure static`, the bounded static walk of the branch
directions the trace never took. From each one it follows what the post-init
image *states* — no byte any decompiled procedure writes, no access the stack
could see, a target the image names, a `JSR` a traced procedure answers — and those
instructions join the trace as zero-coverage sites, so the same front end builds
them and the same S4 runs on them; where the image is silent the walk stops and
that path ends in `trap 'unstated'`. `arms`/`closed` count the directions,
`stops` says why the rest refused, and `blocks`/`statements` are what *only* a
closed path reaches: the marked blocks plus the split edges and prologues a
later pass made out of a closed edge. Those statements are covered by no
execution — the printed program marks every one `# unverified (static closure)`
— and the subtune's `closure` field reads `static` rather than `trace`. Closed
code is reachable only through edges that were traps, so the verified behaviour
is unchanged: the per-tick state hashes, `period`, `complete` and `divergences`
are the same certificate's. `untaken` counts the trap blocks left under that
name — a direction the walk refused, and the arms of a folded row no copy ran.

It is **not the default**, and the committed certificates are all trace-closed.
Measured at 30 s of music, `--closure trace` → `--closure static`:

| exemplar | printed lines | `goto` | `trap 'untaken'` | closed statements |
| --- | ---: | ---: | ---: | ---: |
| Automatas | 772 → 864 | 6 → 6 | 18 → 5 | 59 |
| `gt2-je-suis-linus` | 1,170 → 1,104 | 0 → 16 | 15 → 0 | 23 |
| `ghouls-song01` | 747 → 832 | 27 → 39 | 28 → 3 | 76 |
| `sw-emomyst` | 1,316 → 1,854 | 0 → 2 | 49 → 1 | 114 |

The traps go, and the *covered* program is still structured worse. A closed arm
itself no longer costs anything: S5 and S6 compute dominance, the loops and a
tail's region on the covered subgraph alone (`graph.edges_of` cuts a closed
block's edges back into covered code), keeping post-dominance whole so the arm
nests in the branch that offered it. What is left is the covered *graph*: a
closed path that rejoins at an address inside a covered block splits it, and the
extra predecessor stops `merge_chains` gluing the pieces, so the tail-promotion
cascade takes a different order. The default therefore stays `trace`.

`complete` means the run closed: the tuneprog reached a state repeat at the same
tick and with the same period as the trace, with no divergence. Otherwise the
program is certified only to the horizon it ran.

`stack` is `"eliminated"` when no machine stack is left: every push is a value
its pops read, a return address is the continuation the `Call` already carries,
and no procedure takes or returns `SP`. It is `{"depth": n | "unknown", "procs":
[...]}` when a procedure reads stack bytes its own frame did not write -- a
scratch area whose pointer is not a constant offset, a `TSX`-relative read of
another frame, the pointer used as data -- and then the whole program keeps the
stack, since such a read can see any byte of the page.

An `irq` tick is entered with the frame the machine itself pushed, and that frame
is the tick's **contract**, not storage: every byte of it is a parameter, the
terminating `RTI` consumes exactly those bytes, and the interrupt disable the
machine sets is the tick's first statement (`build._irq_entry`). Which bytes they
are is the entry's `kernal` field. A **raw** vector (`$FFFE`) is entered by the
6510 alone: the status byte at `SP+1` is the entry flags packed
(`lower.status_expr`). A **CINV** entry (`$0314`, `kernal: true`) is dispatched
through the KERNAL prologue at `$FF48`, which saves A, X and Y on top of that
byte, so the slots are `SP+1..4` = entry Y, X, A, status -- exactly what
`$EA31`/`$EA81` pop before their `RTI`. `machine.entry_frame` is the one statement
of this, and the tracer, `verify._enter` and `frames.contract` all read it.

Which of the two it is is not the tune's word but the **6510 port's**: with HIRAM
set the CPU takes its vector from the KERNAL's own `$FFFE`, so the dispatch is
`$FF48` and CINV and a write to `$FFFE` went to the RAM under the ROM; with HIRAM
clear that RAM *is* the vector and no prologue runs. `machine.vector_gate` decides
it, so a tune that armed both is not ambiguous:

| installed | KERNAL mapped (HIRAM) | KERNAL banked out |
|---|---|---|
| CINV only | CINV, `kernal: true` | refuse `vector banked out` |
| `$FFFE` only | refuse `vector banked out` | raw, `kernal: false` |
| both | CINV, `kernal: true` | raw, `kernal: false` |

`find_entries` runs the gate on the pre-init image, which is a guess about a port
init may move; `Tracer.run_init` re-runs it once init has had the port and that
verdict is what the ticks and the certificate carry. The frame is the tick's
contract, so it has to hold at *every* tick: a tick entered with the port on the
other side of HIRAM refuses (`port moved`).
Nothing names the pushed return address, so a tick that reads *it* -- or reaches
the status by a route no slot places, such as `TSX` -- is residual as any other
unplaceable read is; and a tick some other procedure also calls gets no contract
at all, since a `JSR` puts a return-address byte where the interrupt put the
status. `depth` is the deepest slot below an entry pointer the analysis placed
(reads and writes, callees included), `"unknown"` where an access is not a slot at
all.

What the model does *not* carry is the rest of `$FF48`: the real prologue also
leaves A = 0, X = SP and Z set (`TSX; LDA $0104,X; AND #$10`), where the tracer
hands the handler the registers the previous tick left. Measured over the 37
`play == 0` PSIDs of HVSC `MUSICIANS/A`-`C`: 31 read no entry register at all, 2
read A and see the 0 the KERNAL would leave, and 4 (Boray) read A/X/Y live-in and
would see other bytes on hardware. Modelling `X = SP` would make every such tick
residual, so it waits for a tune that discriminates against the oracle
([prototype-kernal-entry.md](prototype-kernal-entry.md)).

The **6510 port** decides what `$D000-$DFFF` is at every access: the pre-init
image carries the port a KERNAL-initialised host leaves (`$00 = $2F`,
`$01 = $37`), the program's own machine carries those two bytes (`image_port`),
and the tracer records which `(pc, op)` pairs reached a chip. An address the run
only ever touched with I/O banked out is the RAM under the chip: it types as
ordinary storage, its accesses are `ram`/`chk`, and a region lying under the SID
register file takes the `sid_image` role at delta 0 (`ghost.reg[i]`). An access
that did reach the chip keeps the `io` class in the same region, so a write with
I/O mapped is still a SID write. See [prototype-jch.md](prototype-jch.md).

The tracer hashes **two** footprints per tick, because which one a certificate may
claim periodicity on is not known until S4 has run: the whole play-written set,
and that set without the stack page. A program whose stack was eliminated writes
no stack page, so its `period`, `first_repeat` and `complete` come from the
page-exclusive stream; a residual program keeps its pushes and must claim on the
page-inclusive one — a stack byte it reads back is state like any other, and
hashing without it would report a period the tune does not have. `--until-period`
stops at the earliest repeat of either stream, which S4 may then reject. Its
verdict is recorded as `"stack"` in `state.json`; a program it calls residual goes
back to S1 and traces on where only the page-free witness exists
(`pipeline._horizon_stage`), and the horizon *every* run certifies is then the
witness that verdict allows -- one rule (`_certified` over `Trace.witness(free)`,
the verdict read from the program itself by `verify.page_free`) for the
single-song path and for `--songs all` alike. So the certificate claims
completeness where completeness is provable rather than reporting
`complete: false`. Eliminating a stack
therefore moves no certificate's period or divergence, and can only shorten a
horizon: `gt2-do-it-again` closes at 8,659 ticks instead of 9,956, same period
(8,640), still `complete`. A subtune that stops on a repeat certifies
`first_repeat + 1` ticks -- under `--songs all` too, where each subtune stops at
its own witness whatever `--chunk` found it in.

## The register grid

`grid.py` frames any write stream that carries cycles -- the tracer's own `wlog`,
or a `sidtrace` CSV -- into a per-frame `$D400..$D418` grid by attributing each
write to the interrupt period its cycle falls in. A tick is not instantaneous:
Puterman's V20 wrapper spends 168 -> 10,248 cycles between a tick's first SID
write and its last, so a grid keyed by call index is not the grid a sampler read,
and a tick that outlives its frame leaves its late writes in the next one. No
sample point is chosen: the boundary is the interrupt, which the tracer sets
(tick 0's cycle, then `cycles_per_tick`) and the CSV states (`cycle -
since_video_irq`). Framed that way the tracer and `sidplayfp` agree on **3,000 of
3,000** frames of the Knob (`tests/test_oracle.py`); the 297 frames the old
comparison differed on were the oracle framer's half-frame anchor
(`grid_from_writes` rounds to the nearest frame from the first play write), not
the writes. The test attributes that to a side: with the oracle interrupt-framed,
the trace's two rules -- by cycle and by call index -- agree with it and with each
other on all 3,000 frames, while against the rounded anchor **both** differ on
297. `tick_grid` is the by-call view, kept for exactly that comparison.

## Certified exemplars

Numbers from `docs/certificates/`. `complete` = certified to a state repeat;
`horizon` = certified to the tick count shown.

| certificate | tune | player | ticks | music | period | procs | blocks | stmts | regions | certified |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `automatas` | Automatas.sid | defMON | 149,025 | 6m12s | 129,024 | 8 | 227 | 733 | 80 | complete |
| `automatas-6581` | Automatas.sid | defMON, `$D41B`=0 | 149,025 | 6m12s | 129,024 | 8 | 227 | 733 | 80 | complete |
| `automatas-8580` | Automatas.sid | defMON, `$D41B`=1 | 149,025 | 6m12s | 129,024 | 8 | 226 | 722 | 80 | complete |
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

Every one has `divergences: 0` and `envelope_traps: 0`. `jch-knob-at-night`'s
period of 1 is a song that *stops*: its tracks end, the player writes nothing
more and the state is a fixed point from tick 8,576 on. `ghouls-song21` is the
one subtune with no state repeat inside 400 s (two voices keep a portamento and a
trill moving), so it is certified to a 20,049-tick horizon. Both `commando`
subtunes are certified to their HVSC length for the same reason, measured with
`tools/tuneprog_period.py` over 60,000 ticks (20 minutes of music): song 1's
patterns loop at 11,808 ticks, but its three per-voice pulse-width accumulators
(`$5591`/`$55A1`/`$55B9`, each `pw += rate` a tick) do not come back to where
they were — 41,898 of 48,192 tick write lists differ at that lag, on `$D410` and
its two siblings. Hubbard's free-running frame counter `$5525` is real (period
256, `+32` a loop, read only as `& 1` and `& 7`) but is not what blocks the
repeat: reducing it to its masked residue still leaves the accumulators, whose
full byte *is* the SID write, and in song 2 its period already divides that
subtune's loop. Verdict `aperiodic`, the same class as `ghouls-song21`.

## Known gaps

- **Trace closure.** The certified product is trace-closed: a branch direction or
  a table entry the run never took becomes `trap 'untaken'` / `trap 'unverified'`,
  not a decompiled path. `--closure static` decompiles the untaken directions the
  post-init image states (`closure.py`, the `closure` block of the certificate,
  the walk's frontier as `trap 'unstated'`), at the presentation cost measured
  above. `jumptab` closes a patched jump statically over the
  table's observed extent, which recovers most but not all arms (14 of 16 in
  GoatTracker's tick-0 table); entries no accessor ever reached are outside the
  region and stay unlisted. Two bounds narrow that extent where they apply, and
  neither can add a candidate the extent rule did not already carry: a merged
  family's k tables are parallel, so they start at the same index -- the region's
  own base as the lowest of the k bases names it -- and each holds the gap between
  two bases; and the branches on the one path into the dispatch *prove* a range
  for the index (a sign test, an equality, a compare -- Follin's `BPL` over the
  stream byte puts its command table at 128 and up), which cuts into that layout
  and never moves it. The layout is an inference like "out to the nearest
  instruction", not a proof; the range is a proof, and it is applied last.
  A folded writer names its cell *and* its table base through per-copy columns;
  since a column is read-only, copy *j*'s writer is that expression with each
  column read replaced by its *j*th entry, and the same enumeration runs on each
  -- Follin song 1's three voices show 21 arms apiece.
  Where a player unrolls its voices, a row one copy ran
  is every copy's row, which the fold makes one statement -- and marks unverified
  for the copies that never reached it.
- **A merged row a copy never ran is unverified code.** The statement is the one
  the trace saw in another copy, at the address the correspondence says this copy
  names. It is reachable only where the copy's own control flow goes there, its
  coverage entry is 0, the certificate counts it, and the printed program marks it
  per statement (`ghouls-song01`: 133 of 471 merged statements).
- **A copy is found by its static shape, so a short horizon finds less of it.**
  Discovery starts at pcs the trace executed and pairs a dispatch's arms by how
  far their targets align; over 30 s of *Automatas* the two cascade runs fold and
  two smaller families refuse. What folds is therefore a function of the horizon,
  and the certificate's horizon is what the exemplar documents report.
- **What the copy index cannot name refuses.** An edge that leaves one copy for
  anywhere in another but that copy's own entry, a row whose copies do not lift to
  one shape, an opcode cell inside a copy, a successor a copy that never ran the
  row names differently in the image: the first refuses the family whole, the others
  keep that row as k rows under a `switch (v)`, where every copy that did not run
  it traps. The reason is in the certificate and in the printed header, never a
  silent approximation. A refused family leaves its *code* as S2b built it, but
  not the program's region typing: the regions a folded access unites are united
  once for the whole program, so a tune where one family folds and another refuses
  carries the accepted family's region typing everywhere. A copy holds only what
  its rows hold, from its first row on: the stream an alignment stepped over
  before it is the image of no row -- part copy *j*'s own tail, part a preamble
  copy *j+1* alone has -- so no index names it, and the front end enters the copy
  at the row itself with `v` the copy that row belongs to. That is what folds
  Follin's one-voice effects and *Automatas*' row-advance blocks, at the cost of
  a merged body with k entries -- and those k prologues *are* the loop's step:
  where no latch steps the index by a recurrence, `loops.copies` takes the chain
  from the assignments (copy 0 from outside, 1..k-1 on the back edges, each
  edge's count its own copy's share of the cover) and prints `for v in 0..k-1`
  all the same. The jumps into a prologue stay `goto`: a prologue carries the
  preamble that copy alone has, and a helper never hands the index back.
- **A column prints as the operand it stands for.** S6 reads the per-copy table
  once (`copyview.py`): a column whose values step affinely becomes that step in
  `v`, so the existing stride vocabulary prints it (`sid[v].freq_lo` by the
  7-byte voice block, `b640F[v]` by the region's own stride); one whose values do
  not keeps its read, and the group view `views.py` names prints it
  `voice[v].field`, address by address in the state header. The read stays
  because substituting copy 0's operand cannot be told from an operand every copy
  agrees on that happens to hold the same address, so the printed index is the
  copy the *access* names; a plain constant is copy *j*'s own cell wherever it
  stands. `unroll`, which has no column to keep, refuses a run outright where a
  cell every copy names equals one the run relocates. What no rule names keeps its table read with the address visible -- two
  of Follin's 60 columns, two of *Automatas*' five -- and the field names come
  from a substituted twin of the view, since a role is a property of the address.
  The loop itself is a `for v in 0..k-1` over the coverage vector the
  correspondence proved, so the `v += 1; if v < k` chain edge is the header and
  never a statement.
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
- **Periodicity is a hash of the whole footprint, so one drifting cell hides it.**
  A cell whose own period does not divide the music loop pushes the state period
  to the lcm of the two. A *reduced* hash would certify the tune where every
  observable-affecting read of such a cell goes through a mask (`& 7`, `& 1`) --
  the residue's period then divides the loop -- but nothing in the certified set
  needs it: `tools/tuneprog_period.py` puts both `commando` subtunes and
  `ghouls-song21` in the `aperiodic` class, where the drifting cell's full byte
  is itself a SID write and no reduction is sound. The rule the classifier
  applies, and any reduction that follows it, is fail-closed: a read shape it
  cannot classify keeps the whole cell.
- **Refusals.** A second armed interrupt source (CIA-2 timer, NMI vector), a
  recursive JSR call graph, an `init` that never returns inside its budget, and a
  play routine that runs past its instruction budget are diagnosed and refused,
  not approximated.
