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
| L2 | phase-normal form: the fetch region excised at the boundary and replayed — its visits §3.6's events, its other statements `meta.row` — and a **region tree** per phase over the residual: the voice body cut at the fetch regions and the edge writes, natural loops kept as loops, statements ordered so a later one reads what an earlier one wrote | specialisation of the fetch, region formation, if-conversion | `passes/l2_phases.py`, `l2_fetch.py`, `l2_regions.py`, `l2_loops.py` | 405, 387, 276, 241 |
| L3 | typed PNF: every cell typed by the slot lattice, every table by its kind | type inference over a finite lattice | `passes/l3_roles.py` | 232 |
| L4 | materialised PNF: the clock the player's, a cursor over a table specialised into the stream it is, and the score materialised where the fetch region did not already state it | partial evaluation | `passes/l4_specialise.py`, `passes/l4_cursor.py` | 264, 177 |
| L5 | selected object: runs of statements covered by construct expansions with a size cost; what no construct covers stays statements | BURS-style covering | `passes/l5_select.py`, `passes/expand.py`, `accex.py`, `accof.py` | 276, 156, 216, 309 |
| L6 | canonical trackerprog: adjacent streams merged, cells propagated and their writes dead, implied guard terms dropped, names canonical | scalar optimisation | `passes/l6_canon.py`, `l6_names.py`, `l6_reads.py` | 150, 101, 116 |

`passes/rir.py` (241) is the region tree and the player that renders it,
`passes/ir.py` (103) the level object and the validation, `trackerprog/tree.py`
(38) the walk over a statement list that is a tree, and
`tools/trackerprog_passes.py` (249) runs the pipeline.  Three modules of the
package are over 300 lines — `l2_phases.py` 405, `l2_fetch.py` 387 and
`accof.py` 309 — and the rest are at or under it.  Hermetic coverage of the
package, `pytest tests/trackerprog -m "not hvsc"`, 330 green: **85 %**.

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
| L2 | 1,300 | 5 | 28 | 10 |
| L3 | 1,300 | 5 | 28 | 10 |
| L4 | 1,300 | 5 | 28 | 10 |
| L5 | 1,300 | 5 | 28 | 10 |
| L6 | 1,300 | 5 | 28 | 10 |

From `out/passes/pipeline.json`: L1 rerolled 1 chain and found a 5-block
prologue; L2 cut four segments (`prelude` 1 · `row` 3 · `machine` 7 · `machine`
1), kept **one loop with `trip {cell: rpt}` and left none unstated**, raised 5
predicate cells and 3 join flags, read the flush's 25 registers and materialised
the fetch — 16 events over 4 patterns, and the clock `meta.tempo`; L3 typed
`rowsleft`, `ins`, `orderpos`, `note`, one cursor and three shadow halves and
called the clock a divider stepping −1; L4 left the object as it stood; L5's
covering found two records and the object's own size declined them (1,300
against 1,464); L6 merged no stream and spent no cell — no cell the predication
raised here holds state no row of the tick moves, and no rule of the level may
spend one that does.  The object L2 states is the object L6 emits: the four
levels after it are validated and change nothing on this tune.

L4 specialised **no** cursor here: the tick has several `{stream}` phases and the
prototype ranks a cursor's stream only where there is one to become the machine.

---
## 6. The nine families from L0

`tools/trackerprog_surface.py` takes every certified exemplar through the
pipeline and writes `out/surface/surface.json`.  A tune stops at its first red
level, since every level after it would read an object that level did not
produce, and each red cell carries the cause the level itself raised.

The table below is of the output directories named in it, at the horizons they
were certified over.  **Planes rebuilt from the same tune do not always give the
same cell**: at 1,200 calls Commando parts at tick 3 rather than tick 1, and
*Quintessence* stops at L1 on `$1301` — a read its own trace records under no
input kind — where the directory reaches L1.  What CI holds to is therefore the
level each family reaches from planes it builds itself
(`tests/trackerprog/test_surface.py`), which is L2 for seven of the nine;
*Comic Bakery*, whose levels take minutes where the rest take seconds, and
*Quintessence* are left to the tool until each is understood.

| tune | ticks | L1 | L2 |
| --- | --- | --- | --- |
| commando-song1 | 11,780 | **ok** | diverges at tick 1 |
| commando-base | 11,780 | **ok** | diverges at tick 1 |
| follin-song0 | 12,997 | **ok** | `computed address` (voice) |
| galway-comic-bakery | 9,450 | **ok** | `computed address` (voice) |
| gt2-je-suis-linus | 8,236 | **ok** | `computed address` (voice) |
| jch-guldkorn-intro | 2,401 | **ok** | diverges at tick 0 |
| sw-emomyst | 1,200 | **ok** | `computed address` (voice) |
| bb-quintessence-full | 10,426 | **ok** | `$12EF[..]` (voice) |
| defmon-automatas | 1,200 | **ok** | `computed address` (voice) |
| walker-chameleon | 1,200 | **ok** | `maximum recursion depth exceeded` |

**L1 holds on all ten subjects over their whole horizons.  L2 holds on none of
them, and no level above L2 has ever run on a real tune**: §1's L3 to L6 are
stated of the synthetic program (§5) and of the binding (below), and of nothing
else.  The columns for them are left out of the table because every cell in them
is unreached rather than red.

The ten red cells are three causes:

| the cause | tunes | |
| --- | --- | --- |
| a read of the **voice's own pass** no declared table names | Follin, Galway, GoatTracker 2, SID Wizard, defMON, Blackbird | `computed address`, and `$12EF[..]` on Blackbird |
| the level renders and **parts from L1** | Commando (both subtunes), JCH | tick 1, tick 1, tick 0 |
| the level's own recursion | Walker | an implementation limit, not an idiom |

**The chip's own reads are the run the oracle made.**  A raster line or an
oscillator readback is no value of the tune's own: `PcodeVM` answers `$D012` with
`(cycles // 63) % 312` and `$D41B` with `(cycles >> 3) & 0xFF`, over *executed*
cycles, which a per-tick object does not count and cannot recover.  So the levels
replay what the oracle read, in order and per address (`build.observed_inputs`,
`interp.Player(observed=...)`), and a read past the certified run traps by name.
That is what carries defMON, whose init busy-waits 2,227 times on `$D012`, and
Walker, whose play reads `$D41B` once a tick on eight of its ticks; both reach L1
where both stopped before it.

**defMON's model detect is pinned to one arm.**  `JSR $14CB` reads `$D41B` once
at init and takes bit 0 of it to choose the SID model's cutoff scaling
(`prototype-automatas.md` §2): the run's own value is `$C4`, bit 0 is 0, and the
arm it takes writes `$02` to `$10CE` and `$EA` (`NOP`) to `$10D4`.  The other arm
is **not in these planes** — S4 residualises it `untaken` at `$14E7`, because the
trace never ran it — so `--pin D41B=C5` reaches the trap and not the other model.
Certifying both, which `prototype-automatas.md` §3 lists as a should-have, is a
re-trace under the tracer's own `override` policy and not a pin downstream of it.

**What excising the fetch closed, and what it did not.**  The fetch region is the
specialiser's input and not code to predicate, so `passes/l2_fetch.py` excises it
at the L1 → L2 boundary and replays it — its visits §3.6's events, its other
statements `meta.row` — and L2 predicates the residual.  That closed the *fetch*
region's own idiom, and Commando and JCH, whose unstatable decisions all lay
there, moved from a refusal to a render.  The five families of the first class
carry theirs in the **voice** region, which is the residual L2 must state, and
that idiom is untouched.  The distinction was invisible while two tunes were
measured, and the measurement is the finding: one idiom of the boundary is
closed and the other is open on five families.

`l2_phases.unstatable` is the census of the tick as written, by block and by the
read it makes.  On the two tunes that render it names only the fetch region's own
byte reads — 4 of Commando's 43 decisions and 10 of JCH's 55 — which the
specialisation is not asked to lower.  On the five it names the voice region's,
which it is.

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
| L2 on a real tune | **holds on none of the ten subjects** (§6).  The fetch region's own idiom is closed and Commando and JCH render; the voice region's is open on Follin, Galway, GoatTracker 2, SID Wizard and Blackbird |
| L2's render where it does render | not L1 tick for tick: Commando parts at tick 1 and JCH at tick 0 (§6) |
| L3, L4, L5 and L6 on a real tune | **never run**.  No tune reaches L3, so every claim §1 makes above L2 is stated of the synthetic program (§5) and of the binding, and of nothing else |
| L1 on a real tune | holds on **all ten** over their whole horizons.  The chip's own reads are replayed from the run the oracle made, since `$D012` and `$D41B` are functions of executed cycles a per-tick object cannot count (§6) |
| the surface CI holds to | the level each of seven families reaches from planes the test builds itself (`tests/trackerprog/test_surface.py`), so a cell cannot move without the expectation and §6 moving with it.  *Comic Bakery* and *Quintessence* are not among them (§6) |
| L2's own robustness | at a 400-call horizon Commando's L2 score names an instrument its record table does not carry (`KeyError: '7'`), where at 1,200 it renders and parts.  The object should be self-consistent at every horizon |
| defMON under the other SID model | not reached.  The planes carry only the arm the trace ran; the other is `untaken` at `$14E7`, so both models want a re-trace under the tracer's `override` policy, not a pin below it (§6) |
| L4: the order's `call`, `ret`, `mark` and `loop` (Follin, Galway) | not prototyped.  The walk becomes `play` steps and a `jump` end; recognising which opcode a step is means replaying the tune's own order interpreter and reading its stack, which this pass does not do |
| L4: a small decoder unrolled to its rows over a horizon (Blackbird) | not prototyped.  The cursor specialisation evaluates a step at every row of a static table; a decoder has no per-row cursor to evaluate at |
| L4: a cursor's `hold` and `jump` | the specialisation states `next`; a `hold` counted by a cell of the tune's own, and a landing stated on the target rather than the source, are not reached |
| L5: `{note}` and `{commands}` | no expansion: the run they enter is picked at run time by a cell (§4) |
| bound: new non-test lines | 1,708 added and 522 deleted against `deity_informant/` and `tools/`, a net **1,186** of the 1,500 given; about 420 of the additions are lines the module splits moved.  The level order's own change adds 1,025 more and deletes 170, a net **855** |
| bound: module size | the L2 work is four modules — `l2_phases` 405, `l2_fetch` 387, `l2_regions` 276, `l2_loops` 241 — and three modules of the package (those two and `accof` 309) are over the 300 the earlier level order held to |
| bound: hermetic coverage | 85 % of the passes package, 330 green |

Two levels change no value at all and are validated as such: L3 renames and
states, and L6's four passes are each conservative.
