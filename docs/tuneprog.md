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
| **copy index** | the value `v` a merged family runs over: copy *j* executing a template row is that row executed with `v = j` |
| **column** | a per-copy table `T_x[v]` the merge reads an operand from where the copies name different addresses |
| **coverage** | a merged block's execution count per copy; a zero says no execution of that copy reached it, and the statement is unverified |
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
| S2b | procedures from observed edges: clone per entry, tail calls, variant and computed switches | `cfg.py`; static table closure in `jumptab.py`, over a per-copy column base and the range a branch proves for the index |
| S2c | sibling copies as one body: the *exact* static correspondence between k copies of one template -- bases from the chain the procedures carry, one gapped opcode alignment per pair, and a family only while every copy's operand map is a function -- then the fold that makes the copy index a value, so the certified program has one body under `v` with a per-copy column table | `siblings.py`, `copyrows.py`, `copymerge.py` |
| S3 | storage typing: regions, kinds, strides, fields, envelopes, origins | `regions.py` |
| — | front end → IR: one procedure per CFG procedure, one block per node, every memory op typed | `build.py` |
| S4 | SSA over registers/flags/uniques, DCE, copy/constant propagation, 6510 idiom peepholes, then stack elimination: frames are values and the machine stack goes | `ssa.py`, `idioms.py`, `frames.py`, `stack.py` |
| S5 | structuring: loops, if/else, switch, counted `for` (over a recurrence's domain or a family's copies), the phase | `structure.py`, `loops.py` |
| S6 | presentation over a view: value inlining, machine-texture removal, naming a residual program's frames, 16-bit views, the per-copy columns as the operands they stand for, struct views and roles, outlining, shared tails | `inline.py`, `texture.py`, `frame.py`, `word.py`, `copyview.py`, `recover.py`, `facts.py`, `views.py`, `fold.py`, `tails.py`, `unroll.py`, `live.py` |
| S7 | Python code generation, the certificate document, the `tuneprog.md` text form | `emit.py`, `pseudocode.py`, `printer.py` |
| S8 | per-call differential verification against the trace, periodicity, chunked and resumable | `verify.py` |
| — | the facts a headless Ghidra needs from the trace, and the oracles that compare the two ([`ghidra-highpcode-export.md`](ghidra-highpcode-export.md)) | `ghidra_facts.py`, `ghidra_compare.py` |

`pipeline.py` drives every stage into one output directory. The IR itself
(`ir.py`) and its reference interpreter (`interp.py`) are the semantics every
other executor is checked against; `irwalk.py` and `graph.py` are the IR and CFG
traversals every stage shares.

## Module map

```
front end     machine 243  tracevm 325  trace 301  tracedata 310  lift 227
              cfg 309  regions 228  jumptab 368  siblings 395
              copyrows 453  copymerge 165
program       ir 429  interp 247  irwalk 309  graph 70  lower 204  build 448
              ssa 431  frames 371  stack 204  idioms 357  emit 367  verify 326
presentation  structure 345  loops 217  inline 199  texture 475  frame 44
              word 369  fold 472  tails 165  copyview 279  unroll 399  live 96
              facts 223  recover 323  views 213
text          pseudocode 409  printer 371
driver        pipeline 425  __init__ 121
baseline      ghidra_facts 219  ghidra_compare 182   43 modules, 12,631 lines
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
    [--sid-model 6581|8580] [--no-merge] \
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
| `tuneprog.S5.json`, `tuneprog.S6.json` | the structured shape, the recovered names and group views (with the fold's own counts) |
| `tuneprog.md` | the pseudocode |
| `state.json`, `tracer.pkl`, `verify.pkl` | resume state |

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

`copies` is the fold S2c proved. A family is k chained copies of one template
that became one body under the copy index; `rows` is how many instructions
folded, `columns` how many operands the copies disagree on (each one a per-copy
table entry at `table`). `coverage` counts merged statements by which copies ran
them: a `0` is a statement the trace saw in another copy and the correspondence
says is this one's too, which the printed program marks per statement. A family
the index cannot name -- a cross-copy edge, an operand no table can express --
is `refused` with its reason, and its copies stay k bodies.

`complete` means the run closed: the tuneprog reached a state repeat at the same
tick and with the same period as the trace, with no divergence. Otherwise the
program is certified only to the horizon it ran.

`stack` is `"eliminated"` when no machine stack is left: every push is a value
its pops read, a return address is the continuation the `Call` already carries,
and no procedure takes or returns `SP`. It is `{"depth": n | "unknown", "procs":
[...]}` when a procedure reads stack bytes its own frame did not write -- a
scratch area whose pointer is not a constant offset, a `TSX`-relative read of
another frame, an interrupt entry frame's status byte, the pointer used as data
-- and then the whole program keeps the stack, since such a read can see any byte
of the page. `depth` is the deepest slot below an entry pointer the analysis
placed (reads and writes, callees included), `"unknown"` where an access is not a
slot at all.

The tracer hashes **two** footprints per tick, because which one a certificate may
claim periodicity on is not known until S4 has run: the whole play-written set,
and that set without the stack page. A program whose stack was eliminated writes
no stack page, so its `period`, `first_repeat` and `complete` come from the
page-exclusive stream; a residual program keeps its pushes and must claim on the
page-inclusive one — a stack byte it reads back is state like any other, and
hashing without it would report a period the tune does not have. `--until-period`
stops at the earliest repeat of either stream, so a residual tune may need
`--calls` to reach the page-inclusive repeat it certifies on. Eliminating a stack
therefore moves no certificate's period or divergence, and can only shorten a
horizon: `gt2-do-it-again` closes at 8,659 ticks instead of 9,956, same period
(8,640), still `complete`.

## Certified exemplars

Numbers from `docs/certificates/`. `complete` = certified to a state repeat;
`horizon` = certified to the tick count shown.

| certificate | tune | player | ticks | music | period | procs | blocks | stmts | regions | closure |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `automatas` | Automatas.sid | defMON | 149,025 | 6m12s | 129,024 | 8 | 227 | 733 | 80 | complete |
| `automatas-6581` | Automatas.sid | defMON, `$D41B`=0 | 149,025 | 6m12s | 129,024 | 8 | 227 | 733 | 80 | complete |
| `automatas-8580` | Automatas.sid | defMON, `$D41B`=1 | 149,025 | 6m12s | 129,024 | 8 | 226 | 722 | 80 | complete |
| `commando-song1` | Commando.sid | Hubbard | 11,780 | 3m55s | — | 3 | 115 | 341 | 58 | horizon |
| `commando-song2` | Commando.sid | Hubbard | 11,780 | 3m55s | — | 3 | 101 | 278 | 61 | horizon |
| `ghouls-song01`…`32` | Ghouls_n_Ghosts.sid | Follin | 6…20,049 | — | 1…8,064 | 2–4 | 90–275 | 190–671 | 37–70 | complete (31 of 32) |
| `ghouls-songs-all` | Ghouls_n_Ghosts.sid | Follin, all 32 subtunes | 220,049 | — | per subtune | 4 | 299 | 770 | 45 | complete (31 of 32) |
| `gt2-je-suis-linus` | Je_suis_Linus_le_salaud.sid | GoatTracker 2 | 8,236 | 2m44s | 6,720 | 14 | 245 | 526 | 73 | complete |
| `gt2-do-it-again` | Do_It_Again.sid | GoatTracker 2 | 8,659 | 2m53s | 8,640 | 14 | 234 | 516 | 73 | complete |
| `sw-emomyst` | Emomyst.sid | SID Wizard 1.6 | 8,084 | 2m41s | 6,120 | 15 | 365 | 951 | 96 | complete |
| `sw-end-of-the-world` | End_of_the_World.sid | SID Wizard 1.9 | 14,465 | 4m49s | 7,688 | 16 | 361 | 935 | 94 | complete |

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
  a merged body with several entries, which the structurer prints with a `goto`
  (two in *Automatas*) and no `for`.
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
- **Refusals.** A second armed interrupt source (CIA-2 timer, NMI vector), a
  recursive JSR call graph, an `init` that never returns inside its budget, and a
  play routine that runs past its instruction budget are diagnosed and refused,
  not approximated.
