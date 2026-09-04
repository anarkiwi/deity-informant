# Prototype: the tuneprog-to-trackerprog pipeline as compiler passes

`deity_informant/trackerprog/universal.py` is an interpreter, a trackerprog is
its program, and a tune's certified tick is that interpreter specialised on one.
The lift is the inverse, and this states it as compiler passes: six levels, each
a representation the next pass consumes, and from L2 on each level is **itself a
trackerprog** — the guarded `all: True` row list at a `{stream}` phase is the
schema's most general construct — so it renders on the unchanged player and
carries the same certificate.  That is translation validation for every pass:
`passes/ir.py`'s `validate(before, after, horizon)` renders both levels and
compares their write lists, and no pass of this prototype is committed without it.

Contents: 1 the levels · 2 the idioms, level by level · 3 the round trip over
the thirty hand objects · 4 the synthetic program end to end · 5 Commando ·
6 what the prototype does not do.

---

## 1. The levels

| level | representation | pass | module | lines |
| --- | --- | --- | --- | --- |
| L0 | S4 IR + S6 names + the image | the tuneprog pipeline | — | — |
| L1 | structured tick: one procedure, callees inlined, runs and sibling copies rerolled, the voice loop and its indices explicit | inlining, rerolling, mem2reg | `passes/l1_structure.py` (+ `callee.py`, #351's own, restored) | 279 (+270) |
| L2 | phase-normal form: the voice body cut at the fetch regions and the edge writes into phases, each a predicated row list, plus the tick's own pre and post lists | region formation, if-conversion | `passes/l2_phases.py` | 366 |
| L3 | typed PNF: every cell typed by the slot lattice, every table by its kind | type inference over a finite lattice | `passes/l3_roles.py` | 232 |
| L4 | materialised PNF: the fetch replayed into §3.6 events with fields, the clock the player's, the walk the score's own play lists; the residual is the row program | partial evaluation | `passes/l4_specialise.py` | 262 |
| L5 | selected object: runs of predicated rows covered by construct expansions with a size cost; what no construct covers stays as rows | BURS-style covering | `passes/l5_select.py`, `passes/expand.py` | 256, 298 |
| L6 | canonical trackerprog: adjacent streams merged, cells propagated and their writes dead, implied guard terms dropped, names canonical | scalar optimisation | `passes/l6_canon.py` | 339 |

`passes/ir.py` (103) is the level object and the validation; `tools/trackerprog_passes.py`
(123) runs L4–L6 over a bound object.  New non-test code: **2,535 lines**, 2,142
of it written here and 393 restored or driving.  Hermetic coverage of all of it,
`pytest tests/trackerprog -m "not hvsc"`: **95 %**.

---

## 2. The idioms, level by level

Each is a fragment in the S4 IR, built by hand, named by the family that forced
it, and taken through the one pass with no branch on a family —
`test_no_pass_of_the_pipeline_names_a_family` greps the pass modules for all nine
family names and finds none.  Every fragment that changes the program is
validated against the level before it.

### L1 — `tests/trackerprog/test_l1_idioms.py`, 12 green

| idiom | family | |
| --- | --- | --- |
| a callee inlined where it stands | GoatTracker 2 | green |
| a run of three calls with a stepping argument rerolled into the loop it closes | Walker, Hubbard | green |
| unrolled voice copies at a stride rerolled into one pass over that stride | SID Wizard, Hubbard | green |
| the voice loop and its induction variables explicit | all nine | green |
| a per-voice array at a stride, and the record S6 splits at it | GoatTracker 2 | green |
| a fused tuning region with state past it | Hubbard | green |
| a split tuning of two byte tables | SID Wizard, GoatTracker 2 | green |
| the first call's own blocks peeled as a prologue | GoatTracker 2, SID Wizard | green |

The reroller is one rule: a chain of blocks whose statements are equal up to an
arithmetic progression of their own constants is one turn of a loop, with one
index a step and a counter that closes it.  A pass rerolled at the voice stride,
as many turns as there are voices, has a voice index like the loop it came from.

### L2 — `test_l2_idioms.py`, 8 green

| idiom | family | |
| --- | --- | --- |
| segments at the fetch region and at the edge writes, the commits placed | Hubbard | green |
| the act is the row: two rows each writing `AD` stay two acts | SID Wizard | green |
| a block's guard path is the row's own predicate | all nine | green |
| a fetch region that runs ahead of the boundary it stages for | GoatTracker 2, SID Wizard, JCH | green |
| a block before the voices and one after: the tick's own channel | Follin | green |
| the flush as the tick's own first act | GoatTracker 2, JCH, defMON | green |

If-conversion is by **predicate cell**: a block that decides a term and then
moves a cell that term reads has no channel for the value it decided on, so the
decision is a cell assigned where the block makes it, and every row it guards
reads that cell.  A block the tick does not reach assigns nothing and the terms
that lead to it are cells of the same kind, so no reset is needed.  A join no
path folds is #351's `planall` finished: each path raises a flag where it stands
and the tick clears them once.  A register a channel row names is one entry of
`globals.commit`, staged in a cell of its own where the row computed it; a
per-voice cell a channel row writes is every voice's (§3.6's `all`).

### L3 — `test_l3_idioms.py`, 10 green

| idiom | family | |
| --- | --- | --- |
| the reserved cells typed from their uses (`note`, `ins`, `rowsleft`, `orderpos`) | all nine | green |
| a stream cursor over a table | defMON, JCH, GoatTracker 2 | green |
| the order's own cursor | all nine | green |
| the clock as a countdown with a reset clause | GoatTracker 2, JCH | green |
| the clock as a counter its own clauses zero | SID Wizard | green |
| a cell the fetch writes and another phase reads: a staging cell | SID Wizard | green |
| the image's own halves typed `shadow` | GoatTracker 2, JCH, defMON | green |
| T1's own cell typed `acc` | every family that has one | green |
| the tables typed by the plane that named them | all nine | green |

Typing renames and states; it moves no value, so the level renders exactly what
the level before it rendered, which the tests assert as well as validate.

### L4 — `test_l4_idioms.py`, 8 green

| idiom | family | |
| --- | --- | --- |
| a byte-decoding fetch specialised to the event fields it stored | Hubbard | green |
| a second packing of the same byte through the same path | GoatTracker 2 | green |
| a third packing through the same path again | — | green |
| the row's own length is the clock the player steps | all nine | green |
| a store the fetch made is the event of the visit that made it | JCH | green |
| the order the horizon walked as the score's own play list (`play`, `jump`) | Follin, Galway | green |
| a cursor with `hold`, `next` and `jump` specialised to a stream record | defMON, JCH, GT2 | **not prototyped** (§6) |
| a small decoder unrolled to its rows over a horizon | Blackbird | **not prototyped** (§6) |

### L5 — `test_l5_idioms.py`, 15 green, and §3

| idiom | |
| --- | --- |
| a sweep run covered by the record it expands to | green |
| a slide run with a direction covered by its two arms | green |
| a reload run covered by the policy it is | green |
| a gated run covered with the arm the gate writes | green |
| a run that produces the value it came in with is `emit: entry` | green |
| a run no construct expands to stays a row | green |
| every family's record costs less than the run of rows it covers | green |
| the covering takes the longest run a construct expands to | green |
| the record and the rows it covers render the same | green |

### L6 — `test_l6_idioms.py`, 6 green

| idiom | |
| --- | --- |
| adjacent streams of one tick are one stream | green |
| a cell whose reads are an expression over state no row moves is spent | green |
| a guard term the clock's boundary implies is dropped | green |
| a guard term over two constants is worth what it is | green |
| a cell named by the register its sole reader writes | green |
| the level leaves every one of the thirty hand objects no bigger and certified | green, 30 of 30 |

---

## 3. The round trip, over the thirty hand objects

`tests/trackerprog/test_l5_roundtrip.py`, generated from the poison registry's
own builds; the counts are written to `out/passes/roundtrip.json`.

| | acc | prelude | on_note | row | flush | reset | producer | total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| instances | 296 | 126 | 169 | 140 | 146 | 12 | 301 | **1,190** |
| expandable | 261 | 126 | 169 | 20 | 146 | 12 | 301 | **1,035** |
| `expand(select(expand(c))) == expand(c)` | 261 | 126 | 169 | 20 | 146 | 12 | 301 | **1,035** |
| `select(expand(c)) == c`, canonically | 261 | 126 | 169 | 20 | 146 | 12 | 301 | **1,035** |
| faithful under the hermetic snippet | 240 | — | — | — | — | — | — | **240 of 240 armable** |

Nothing failed: `failed` is empty.  21 of the 261 expandable records could not be
*armed* under the snippet — the arm that binds their numbers is a row's own
command, whose payload a one-phase snippet has no row for — and they are counted
by build (`gt2-je-suis-linus` 6, `gt2-do-it-again` 6, `commando-song2` 2,
`commando-song3` 2, `sw-emomyst` 2, `sw-end-of-the-world` 2,
`blackbird-quintessence` 1).

The canonical form is stated, not assumed: an expansion is a run of rows, so it
carries no annotation (`rank`, `scope`, `target`, `note`), no interval to assert
(`bound`, which the selection derives from the mask the store writes through),
no `rate` of 1, no width a delta never masks by, no `cell` for a record that
only gates, and `step_when` and `delta_when` are one guard where the gate has no
false arm.  A produce of one byte is its own low half.

**155 instances have no expansion into rows at all**, each named by its own form:

| form | count | why |
| --- | --- | --- |
| `{stream}` step | 43 | the rows are the stream's own and the step names it |
| `{commands}` step | 28 | the row language has no channel for the event |
| `{note}` step | 27 | the same |
| `{ins}` step | 22 | the same |
| `gate` under a `step_when` | 11 | a row cannot state the negation of a guard |
| `reflect` | 7 | the turn reads the value the row writes |
| `clamp` | 6 | taking a pitch is one named operation and no assignment (§3.1) |
| `beyond` | 3 | no row states what lies past the tuning |
| `repeat` | 3 | the row language has no loop |
| `trap` | 3 | no row states an arm the horizon never takes |
| `reflect-complement` | 2 | the turn reads the value the row writes |

This is the pipeline's own boundary, measured: **a covering can never select a
construct whose expansion is not a run of rows**, and eleven of the schema's
forms are of that kind.

---

## 4. The synthetic program, end to end

`tests/trackerprog/_pipeline.py` is one tune in the S4 IR carrying, at once, a
run of unrolled sibling blocks the structuring rerolls into the pass over the
voices it is; a voice loop with two indices (its own number and its base in the
register file); a countdown clock the row reloads; a fetch one clock step ahead
of the boundary it stages for; a byte-decoding fetch with a wrap that steps the
order; a 25-register image and its flush; a cursor over a wave table; and a
two-armed slide with a bounce that turns its direction cell.

`tests/trackerprog/test_pipeline.py`, 300 ticks, 7,500 writes:

| pass | ticks | writes | identical | divergence |
| --- | --- | --- | --- | --- |
| L0 → L1 | 300 | 7,500 | yes | none |
| L1 → L2 | 300 | 7,500 | yes | none |
| L2 → L3 | 300 | 7,500 | yes | none |
| L3 → L4 | 300 | 7,500 | yes | none |
| L4 → L5 | 300 | 7,500 | yes | none |
| L5 → L6 | 300 | 7,500 | yes | none |

| level | xz | streams | rows | cells |
| --- | --- | --- | --- | --- |
| L2 | 1,328 | 13 | 151 | 17 |
| L3 | 1,332 | 13 | 151 | 17 |
| L4 | 1,412 | 12 | 147 | 17 |
| L5 | 1,412 | 12 | 147 | 17 |
| L6 | 1,388 | 11 | 146 | 16 |

What each level derived, from `out/passes/pipeline.json`: L1 rerolled 1 chain and
found a 5-block prologue; L2 cut four segments (`prelude` 1 · `row` 3 ·
`machine` 5 · `machine` 1), raised 8 predicate cells and 3 join flags and read
the flush's 25 registers; L3 typed `rowsleft`, `ins`, `orderpos`, `note`, two
cursors and three shadow halves, and called the clock a divider stepping −1; L4
materialised 16 events over 4 patterns and made the clock `meta.tempo`; L5's
covering found two records and the object's own size declined them (1,412 against
1,584); L6 merged one stream and spent the predicate `phead`.

---

## 5. Commando song 1, from the binding to L6

The binding of [prototype-lifter.md](prototype-lifter.md) is L3 with one L4
shape — the score already materialised — so `tools/trackerprog_passes.py` takes
its object through L5 and L6.

```
tools/tuneprog_trackerprog.py --out out/lift-b6/commando-song1 --sid <resolved> --certify
  commando-song1: 11780 ticks, 26 rows, 3 accs, 32 patterns, 572 events,
                  0 hints, 5 refusals, xz 3608, divergence None, 9.2 s
tools/trackerprog_passes.py   --out out/lift-b6/commando-song1 --sid <resolved> --certify
  commando-song1 L4: 17 streams, 26 rows, 3 accs, 18 cells, xz 3608, divergence None
  commando-song1 L5: 17 streams, 26 rows, 3 accs, 18 cells, xz 3608, divergence None
  commando-song1 L6: 16 streams, 26 rows, 3 accs, 18 cells, xz 3588, divergence None
  commando-song1: covered ['machine1_acc4'], selected none,
                  covering xz [3608, 3688], merged 1, propagated none, 0.6 s
```

**0 divergences over 11,780 ticks at every level.**  `tools/trackerprog_sizes.py
--object` puts the L6 object at **3,588 — 1.41×** the tune's 2,548-byte load
band, against the hand transliteration's 3,464 and 1.36×: **3.6 % over the
hand**, inside the 10 % the acceptance asked for.

Selection found one record in the machine's rows — a counter the pulse sweep
steps — and the object's own size declined it: the record repeats a four-term
guard that the one row it covers states once, and stating it costs 80 bytes of
`xz`.  The size cost is the object's, not the run's.

**The hand's seven, and which of them a covering could ever reach**
(`out/passes/commando-records.json`, from `expand.acc_why` over the hand object):

| record | reachable | the form the catalogue lacks a row for |
| --- | --- | --- |
| `pulse_run` | yes | — (and the binding already states it, from T1) |
| `slide` | yes | — (and the binding already states it, from T1) |
| `vibrato` | no | `repeat`: the row language has no loop |
| `pulse_bounce` | no | `reflect`: the turn reads the value the row writes |
| `drum` | no | `gate` under a `step_when`: a row cannot state the negation |
| `skydive` | no | `trap`: no row states an arm the horizon never takes |
| `arpeggio` | no | `beyond`: no row states what lies past the tuning |

So the four the binding leaves as rows are exactly four whose §5 form has no
expansion into rows.  **No covering, however good, can select them**; what stands
between this object and the hand's is not the selector but four missing row
forms, each named above.

---

## 6. What the prototype does not do, and the bounds it hit

Stated as findings, not as work in progress.

| | |
| --- | --- |
| L4: a cursor with `hold`, `next` and `jump` specialised into a §3.3 stream record (defMON, JCH, GT2) | not prototyped. The cursors are named in the level's own facts (`out/passes/pipeline.json`, `cursors`) and left as the rows that walk them |
| L4: a small decoder unrolled to its rows over a horizon (Blackbird) | not prototyped, for the same reason: it is the cursor specialisation |
| L4: the order's `call`, `ret`, `mark` and `loop` (Follin, Galway) | not prototyped. The walk becomes `play` steps and a `jump` end; which opcode a step is, is a recognition the level does not make |
| L2: an inner loop of the voice's own pass | one block is one row, so a block a tick runs several times is stated once. The synthetic program and the fragments have none |
| bound: `l2_phases.py` 366, `l6_canon.py` 339 | over the 300 a module was given, by 66 and 39 |
| bound: 2,535 lines of new non-test code | over the 2,000 given, by 535; 270 of it is #351's `callee.py` restored verbatim and 123 the tool |

Two levels change no value at all and are validated as such: L3 renames and
states, and L6's four passes are each conservative — a stream a cursor or a
re-point names keeps its rows and their numbers, a cell read anywhere but the
rows one tick runs is not propagated, and a naming that makes the object bigger
is not taken.  That conservatism is what carries all thirty hand objects through
L6 with the render identical and the size non-increasing.
