# Prototype: the tuneprog-to-trackerprog pipeline as compiler passes

`deity_informant/trackerprog/universal.py` is an interpreter, a trackerprog is
its program, and a tune's certified tick is that interpreter specialised on one.
The lift is the inverse, and this states it as compiler passes: six levels, each
a representation the next pass consumes, and from L2 on each level is **itself a
trackerprog** — an ordered, predicated statement list, whose flat case is the
schema's `all: True` row list — so it renders on the unchanged player and carries
the same certificate.  That is translation validation for every pass:
`passes/ir.py`'s `validate(before, after, horizon)` renders both levels and
compares their write lists, and no pass here is committed without it.

Contents: 1 the levels · 2 the region tree · 3 the idioms, level by level ·
4 the round trip over the thirty hand objects · 5 the synthetic program end to
end · 6 Commando and JCH from L0 · 7 what the prototype does not do.

---

## 1. The levels

| level | representation | pass | module | lines |
| --- | --- | --- | --- | --- |
| L0 | S4 IR + S6 names + the image | the tuneprog pipeline | — | — |
| L1 | structured tick: one procedure, callees inlined, runs and sibling copies rerolled, the voice loop and its indices explicit | inlining, rerolling, mem2reg | `passes/l1_structure.py` (+ `callee.py`) | 279 (+270) |
| L2 | phase-normal form: a **region tree** per phase — the voice body cut at the fetch regions and the edge writes, natural loops kept as loops, statements ordered so a later one reads what an earlier one wrote | region formation, if-conversion | `passes/l2_phases.py`, `passes/l2_regions.py` | 300, 232 |
| L3 | typed PNF: every cell typed by the slot lattice, every table by its kind | type inference over a finite lattice | `passes/l3_roles.py` | 232 |
| L4 | materialised PNF: the fetch replayed into §3.6 events, the clock the player's, a cursor over a table specialised into the stream it is | partial evaluation | `passes/l4_specialise.py`, `passes/l4_cursor.py` | 263, 177 |
| L5 | selected object: runs of statements covered by construct expansions with a size cost; what no construct covers stays statements | BURS-style covering | `passes/l5_select.py`, `passes/expand.py`, `accex.py`, `accof.py` | 276, 156, 216, 299 |
| L6 | canonical trackerprog: adjacent streams merged, cells propagated and their writes dead, implied guard terms dropped, names canonical | scalar optimisation | `passes/l6_canon.py`, `l6_names.py`, `l6_reads.py` | 150, 101, 116 |

`passes/rir.py` (241) is the region tree and the player that renders it,
`passes/ir.py` (103) the level object and the validation, `trackerprog/tree.py`
(38) the walk over a statement list that is a tree, and
`tools/trackerprog_passes.py` (249) runs the pipeline.  Every module of the
package is at or under 300 lines.  Hermetic coverage of the package,
`pytest tests/trackerprog -m "not hvsc"`: **90 %**.

---

## 2. The region tree

A level's statement list is **ordered**: a later statement reads what an earlier
one wrote, so a guard over a value a statement just left is a second statement of
the same list and not a second row.  Four forms stand beside §3.3's row:

| form | what it is | who needs it |
| --- | --- | --- |
| `{loop: {trip, body}}` | a natural loop; the trip is a value over the state the loop is entered with | Hubbard's `repeat`, a voice pass run several times a tick |
| `{region: [...], beyond?}` | a nested list, with the words past the tuning a read inside it reaches | Hubbard's `arpeggio` |
| `{take: value}` | the tuning taken at a value: §3.1's one named operation, no assignment | GoatTracker 2's `clamp` |
| `{trap: why}` | a statement the certified horizon never runs | Hubbard's `skydive` |

`rir.Player` is `universal.Player` with the control of these four and **none of
their leaves**: `rowplan` is the one place §3.3's guarded rows compile, the region
tree is admitted there and nowhere else, and every guard, value, set and take is
the player's own closure.  A list of plain rows therefore renders exactly as
`runstream` renders it.  `rir.flatten` gives the rows a tree is where every
statement has one — a loop whose trip the object states outright is that many
turns of its body — and `None` where a `take` or a `trap` stands.

`rir.truth` puts a guard term in a value position (the chip's own comparisons),
which is how a decision becomes a cell; `accof.untruth` reads it back.

---

## 3. The idioms, level by level

Each is a fragment in the S4 IR, built by hand, named by the family that forced
it, and taken through the one pass with no branch on a family —
`test_no_pass_of_the_pipeline_names_a_family` greps the pass modules for all nine
family names and finds none.

### L1 — `tests/trackerprog/test_l1_idioms.py`, 12 green

A callee inlined where it stands (GT2); a run of three calls with a stepping
argument rerolled into the loop it closes (Walker, Hubbard); unrolled voice
copies at a stride rerolled (SW, Hubbard); the voice loop and its induction
variables (all nine); a per-voice array at a stride (GT2); a fused tuning region
with state past it (Hubbard); a split tuning of two byte tables (SW, GT2); the
first call's blocks peeled as a prologue (GT2, SW).

### L2 — `test_l2_idioms.py`, 12 green

| idiom | family | |
| --- | --- | --- |
| segments at the fetch region and at the edge writes, the commits placed | Hubbard | green |
| the act is the row: two rows each writing `AD` stay two acts | SID Wizard | green |
| a block's guard path is the row's own predicate | all nine | green |
| a fetch region that runs ahead of the boundary it stages for | GT2, SW, JCH | green |
| a block before the voices and one after: the tick's own channel | Follin | green |
| the flush as the tick's own first act | GT2, JCH, defMON | green |
| **an inner loop kept as a loop with the trip its own cell states** | Hubbard | green |
| **a block run several times a tick** | Hubbard | green |
| **a statement reads what the statement before it wrote** | all nine | green |
| **a guard over a value just written is the next statement of the list** | all nine | green |

Region formation: a natural loop of a segment stays a loop and its trip is a
value over the state it is entered with — the counter its own two-way test closes
it on, counting down to the bound (the counter itself) or up to it (the
difference).  The voice loop is `meta.voice_order`'s and is no region of the
pass.  A loop whose trip no value states is named in `unstated_loops` and left as
its blocks: a finding, not a fit.

If-conversion is by **predicate cell**, and where the decision stands is the
dependence: a block's branch is decided where the block *ends*, so a condition
that loads a cell the block itself stored is the **last** statement of the
block's list, and a condition over a name the block bound before the store is the
first.  A one-block fragment where the guard read the cell before the store
rendered the wrong arm until this was stated.

### L3 — `test_l3_idioms.py`, 10 green

The reserved cells typed from their uses (`note`, `ins`, `rowsleft`,
`orderpos`); a stream cursor over a table; the order's own cursor; the clock as a
countdown with a reset (GT2, JCH), as a divider, as a counter its clauses zero
(SW); a staging cell (SW); the image's halves typed `shadow`; T1's cell typed
`acc`; the tables typed by the plane that named them.

### L4 — `test_l4_idioms.py`, 11 green, 2 not prototyped

| idiom | family | |
| --- | --- | --- |
| a byte-decoding fetch specialised to the event fields it stored | Hubbard | green |
| a second and a third packing of the same byte through the same path | GT2, — | green |
| the row's own length is the clock the player steps | all nine | green |
| a store the fetch made is the event of the visit that made it | JCH | green |
| the order the horizon walked as the score's own play list | Follin, Galway | green |
| **a cursor over a table is the stream the player steps** | defMON, JCH, GT2 | green |
| **the step a cursor takes is the `next` the row carries** | defMON, JCH, GT2 | green |
| **a cursor that steps by two is the same specialisation** | — | green |
| the order's `call`, `ret`, `mark` and `loop` | Follin, Galway | **not prototyped** (§7) |
| a small decoder unrolled to its rows over a horizon | Blackbird | **not prototyped** (§7) |

`passes/l4_cursor.py` is one pass over cursor kinds and the fetch's own
specialisation generalised: the statements that read a declared table at a cursor
and the one that steps it become a §3.3 stream, one row a row of the table, whose
`sets` are the fields the read set and whose `next` is the step the cursor takes
where that step is not the row after it.  The step is **evaluated at every row of
the table** — the table is static, so this needs no horizon — the cursor's seed
becomes `state0.cursors`, and the phase it stood in becomes the machine's rank
order.  A walk under a guard, or one another statement also names, is left as the
rows that walk it and counted in `cursors`.

### L5 — `test_l5_idioms.py`, 22 green, and §4

The nine coverings of the earlier prototype, and one fragment per region form:
a `repeat` as a loop whose trip the record names; a `reflect` whose turn reads
the cell the statement before it wrote; a `clamp` whose take stops the rest of
the step; a `gate` under a `step_when` reading the decision where it is made; a
`trap`; a `beyond` as the region its reads stand in; and the flattening that has
rows only where every form has one.

### L6 — `test_l6_idioms.py`, 6 green

Adjacent streams merged; a cell whose reads are an expression over state no row
moves is spent; a guard term the clock's boundary implies is dropped; a term over
two constants is worth what it is; a cell named by the register its sole reader
writes; and, on every one of the thirty hand objects, the level leaves the object
no bigger and the certificate unchanged.

---

## 4. The round trip, over the thirty hand objects

`tests/trackerprog/test_l5_roundtrip.py`, generated from the poison registry's
own builds; the counts are written to `out/passes/roundtrip.json`.

| | acc | prelude | on_note | row | flush | reset | producer | total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| instances | 296 | 126 | 169 | 140 | 146 | 12 | 301 | **1,190** |
| expandable | 296 | 126 | 169 | 85 | 146 | 12 | 301 | **1,135** |
| `expand(select(expand(c))) == expand(c)` | 296 | 126 | 169 | 85 | 146 | 12 | 301 | **1,135** |
| `select(expand(c)) == c`, canonically | 296 | 126 | 169 | 85 | 146 | 12 | 301 | **1,135** |
| faithful under the hermetic snippet | 261 | — | — | — | — | — | — | **261 of 261 armable** |

`failed` is empty.  **Every one of the 296 §5 records expands, round trips and is
selected back**, against 261 of 296 before the region tree; 35 of them cannot be
*armed* under the one-phase snippet — the arm that binds their numbers is a row's
own command — and are counted by build (`gt2-je-suis-linus` 9, `gt2-do-it-again`
9, `commando-song2` 5, `commando-song3` 5, `commando-song1` 2, `sw-emomyst` 2,
`sw-end-of-the-world` 2, `blackbird-quintessence` 1).  All 261 armable render
identically to their own expansion under `rir.Player`.

The canonical form is stated, not assumed: an expansion carries no annotation
(`rank`, `scope`, `target`, `note`), no interval to assert (`bound`), no `rate`
of 1, no amplitude `shift` of 0 and no amplitude `witness`, no width a delta
never masks by, no `cell` for a record that only gates, no `phase` a record with
no delta or a `repeat` never reads, and a record the horizon never takes
(`trap`) states no behaviour at all.  `step_when` and `delta_when` are one guard
where the gate has no false arm, and where every channel of a record stands under
its step, its `when` and its `delta_when` are one guard.

**55 instances have no expansion**, both of one kind:

| form | count | why |
| --- | --- | --- |
| `{commands}` step | 28 | the run it enters is picked at run time by a cell, not by the step |
| `{note}` step | 27 | the same: the note-on is the record the voice's own `ins` cell selects |

This is the pipeline's measured boundary, and it is one boundary and not eleven:
**a construct whose expansion enters a run a cell picks at run time has none.**
A `{stream}` step names one stream and expands to its statements; a `{ins}` step
is one assignment over the event's own fields; a `{note}` step and a `{commands}`
step enter a stream the instrument record or the command record decides.

---

## 5. The synthetic program, end to end

`tests/trackerprog/_pipeline.py` is one tune in the S4 IR carrying, at once, a
run of unrolled sibling blocks the structuring rerolls into the pass over the
voices it is; a voice loop with two indices; a countdown clock the row reloads; a
fetch one clock step ahead of the boundary it stages for; a byte-decoding fetch
with a wrap that steps the order; a 25-register image and its flush; a cursor
over a wave table; a two-armed slide with a bounce that turns its direction cell;
and **an inner loop whose turns a per-voice cell counts**.

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
| L2 | 1,380 | 13 | 153 | 18 |
| L3 | 1,384 | 13 | 153 | 18 |
| L4 | 1,460 | 12 | 149 | 18 |
| L5 | 1,460 | 12 | 149 | 18 |
| L6 | 1,440 | 11 | 148 | 17 |

From `out/passes/pipeline.json`: L1 rerolled 1 chain and found a 5-block
prologue; L2 cut four segments (`prelude` 1 · `row` 3 · `machine` 7 · `machine`
1), kept **one loop with `trip {cell: rpt}` and left none unstated**, raised 9
predicate cells and 3 join flags and read the flush's 25 registers; L3 typed
`rowsleft`, `ins`, `orderpos`, `note`, two cursors and three shadow halves and
called the clock a divider stepping −1; L4 materialised 16 events over 4 patterns
and made the clock `meta.tempo`; L5's covering found two records and the object's
own size declined them (1,460 against 1,640); L6 merged one stream and spent the
predicate `phead`.

L4 specialised **no** cursor here: the tick has several `{stream}` phases and the
prototype ranks a cursor's stream only where there is one to become the machine.

---

## 6. Commando and JCH from L0

```
tools/trackerprog_passes.py --l0 --out out/lift-b6/commando-song1 \
                                 --out out/lift-b6/jch-guldkorn-intro
  commando-song1     L1: ok, 11780 ticks, 133109 writes
  commando-song1     L2: failed, Unlowerable: computed address
  jch-guldkorn-intro L1: ok, 2401 ticks, 63229 writes
  jch-guldkorn-intro L2: failed, Unlowerable: computed address
```

**L0 → L1 holds on both tunes over their whole horizons**: identical write
lists, no divergence, 11,780 ticks and 133,109 writes on Commando and 2,401 and
63,229 on JCH.  The structuring is proven on two real families.

**L1 → L2 fails on both, at one idiom.**  `l2_phases.unstatable` names every
decision of the tick whose condition no value of L2's vocabulary states, and the
tool writes them to `trackerprog.l0.report.json`:

| tune | decisions | unstatable | in the fetch region | in the voice's own pass |
| --- | --- | --- | --- | --- |
| commando-song1 | 43 | 7 | 4 (`computed address`) | 2 (`$5592[..]`), 1 (`$5596[..]`) |
| jch-guldkorn-intro | 55 | 12 | 10 (`computed address`) | 2 (`$185F[..]`) |

The idiom, in both tunes and in both places, is **a read whose address no
declared table names**:

1. *the fetch region's own byte reads* (Commando `L5086_BC`, `L508F_C9`,
   `L50DC_C8`, `L5133_BD`; JCH `L110F_BD`, `L113D_BC`, `L1144_4C`, `L1147_F0`,
   `L1149_C9`, `L118F_FE`, `L119B_A9`, `L122B_C9`, `L1243_68`, `L1273_C9`).  The
   fetch reads the score through a **pointer the order sets**, so the base is not
   a constant and §3.3's `tabcell` — which names one declared stream — cannot
   state it.  What the read *is* is the score at the voice's own cursor in the
   pattern the order names, which is the **two-level cursor nest L4
   materialises**.  L2 is asked to lower it three passes before the level that
   states it.
2. *a table read at an index that is not the slot the vocabulary names*
   (Commando `L526B_AD`, `L5285_38` reading `$5592`, the instrument record's
   pulse-width high column, at a per-voice cursor and not at the instrument
   selector; JCH `L1479_AC`, `L13C1_BC` reading `$185F`).  The read is of a
   region the play itself writes, so it is no const table either.

This is the pipeline's own boundary on a real tune, and it is stated as a finding
and not worked around: nothing here lowers the read, fits it, refuses it by name
or branches on the family.  The order the levels are given in is what fails —
§1's L2 is asked for rows over a region whose data §1's L4 supplies — and
[prototype-lifter.md](prototype-lifter.md)'s binding is the same pipeline with
the fetch materialised **before** the lowering, which is why it reaches an object
at all.

For the record, from the binding (which is L3 with one L4 shape) through L5 and
L6, unchanged by this work:

```
tools/trackerprog_passes.py --out out/lift-b6/commando-song1 --sid <resolved> --certify
  commando-song1 L4: 17 streams, 26 rows, 3 accs, 18 cells, xz 3608, divergence None
  commando-song1 L5: 17 streams, 26 rows, 3 accs, 18 cells, xz 3608, divergence None
  commando-song1 L6: 16 streams, 26 rows, 3 accs, 18 cells, xz 3588, divergence None
```

0 divergences over 11,780 ticks at every level, and `trackerprog_sizes.py
--object` puts the L6 object at **3,588 — 1.41×** the tune's 2,548-byte load
band, against the hand transliteration's 3,464 and 1.36×.  JCH's binding
diverges at tick 0 on main and does so still (prototype-lifter.md §7, `no field
binds`); this work did not touch it.

**Commando's seven, and which of them a covering could now reach**
(`out/passes/commando-records.json`, from `expand.acc_why` over the hand object):
`vibrato`, `pulse_run`, `pulse_bounce`, `slide`, `drum`, `skydive` and
`arpeggio` — **all seven**, where before the region tree only `pulse_run` and
`slide` had an expansion at all.  What stands between the bound object and the
hand's is no longer a missing row form.

---

## 7. What the prototype does not do, and the bounds it hit

Stated as findings, not as work in progress.

| | |
| --- | --- |
| L2 on a real tune | **fails**, on both Commando and JCH, at the one idiom of §6: a read whose address no declared table names.  7 of Commando's 43 decisions and 12 of JCH's 55 |
| L4: the order's `call`, `ret`, `mark` and `loop` (Follin, Galway) | not prototyped.  The walk becomes `play` steps and a `jump` end; recognising which opcode a step is means replaying the tune's own order interpreter and reading its stack, which this pass does not do |
| L4: a small decoder unrolled to its rows over a horizon (Blackbird) | not prototyped.  The cursor specialisation evaluates a step at every row of a static table; a decoder has no per-row cursor to evaluate at |
| L4: a cursor's `hold` and `jump` | the specialisation states `next`; a `hold` counted by a cell of the tune's own, and a landing stated on the target rather than the source, are not reached |
| L5: `{note}` and `{commands}` | no expansion: the run they enter is picked at run time by a cell (§4) |
| bound: new non-test lines | 1,708 added and 522 deleted against `deity_informant/` and `tools/`, a net **1,186** of the 1,500 given; about 420 of the additions are lines the module splits moved |
| bound: module size | every module of the package at or under 300; none in the repo over 500 |
| bound: hermetic coverage | 90 % of the passes package |

Two levels change no value at all and are validated as such: L3 renames and
states, and L6's four passes are each conservative.
