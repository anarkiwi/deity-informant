# tracker — the universal tracker layer

The final step of the pipeline **6502 → P-Code → sidprog (cycle-exact) →
frameprog (frame projection) → tracker**. One law per boundary; the tracker's
boundary is frameprog ↔ tracker.

`deity_informant/tracker.py` consumes a `frameprog.FrameProgram` — nothing else.
It reads the frame program's **declared const tables** (docs/frameprog.md,
`datadecl`) and the frame projection frameprog itself produces, and re-expresses
the tune as a graph of triggered generators over **notes** (equal-tempered
semitone indices) instead of register bytes.

## 0. Corrections — read this before proposing work

Predictions in this document have repeatedly been built and measured false, and each
cost a work cycle. Every one below was stated in prose that read like a finding. **A
claim about what a change *would* buy is a hypothesis until a number sits beside it;
mark it as one, and when it is measured, correct it here rather than deleting it.**

| the document claimed | measured | verdict |
|---|---|---|
| reporting a resolved deref address makes every pattern byte declared (§6) | #105 | **false** — arrangement recovery stayed 0; `k` is live state at 366/366 sites, one address pins in 3929; even the refused observed address moves the partition by 0 |
| 112539 pw emits are parameters "filled at init" (§7.2) | #98 | **false** — 0 moved; 94% have an accumulator the *play* phase writes |
| the arrangement is `LOOKUP` nodes routed to `Fire` with a back-edge for the loop (§7.4) | #99 | **false** — a `Fire` edge carries no value, so it cannot name a pattern; and the loop was already free from `_emit`'s modulo |
| a `DIV` phase field is a per-stream parameter fitted to the output (§4d, §8) | #94, #96 | **false** — three editors: the phase belongs to the arrangement, not the stream |
| coverage measures how much of a tune the recovery can reach (§6, everywhere) | #106 | **false** — it measures *justification*; 99680 of the composer's writes are shaped differently by us and **zero** are never produced |
| the relative row index (#102) opens the 6953 emits an absolute-only index refuses (§2, §4f) | this step | **not yet** — the refusal number re-measures at exactly 6953 (GT 1690, SW 4738, DM 525), but a census of all three editors' own songs finds **zero** `SELECT[rel]` nodes: `gtoracle._patt_src` still *refuses* a shifted row rather than emitting one, and `dmoracle` computes a 0/1 flag where the row needs the shift amount. The element is expressible and unused; what it is owed is an emitter, not a measurement |
| the element is owed an emitter (the row above) | the universal-layer step | **paid** — `_patt_src` carries the shift (GT's transpose byte read signed, DefMON's `TR` amount passed instead of a flag) and `_arranged` builds the relative row. All three editors now emit `SELECT[rel]`, laws green: SW 60 nodes over 15 of 64 modules, GT 20 over 4 of 72 tunes, DM 7 over 3 of 6 |
| `DIV` fires one tick per n *input triggers* (§2, since the start) | the universal-layer step | **the spec was right and the evaluator wrong** — `_ticks` divided the frame number whatever the trigger was, so a divider could not clock a divider and the 95 product-period streams (1826 fires) of §8 were inexpressible. An event-clocked `DIV` now counts the ticks it receives; frame-clocked behaviour is byte-identical by construction |
| the 95 product-period streams become recoverable once the cascade is expressible (the row above) | the universal-layer step | **0 recovered** — the cascade rule demands machine evidence (the inner divider's dec watched executing exactly on the outer's ticks, both phases declared) and no corpus stream passes it at 200 frames: generated fires stay 304/307225 and the value partition is byte-identical. §8's 95 was priced from the *arithmetic* of two declared reloads, never from the chain — the same output-side pricing mistake as the §8 phase row, one level up |
| an accumulator wider than its plane is an editor quirk to price (§8) | the universal-layer step | **it is the primitive's missing route** — all three editors keep one, and the `Pair` route claims it: SW 1060 pair nodes over 64 modules, GT 2478 over 72 tunes, laws green corpus-wide at 200 frames. DefMON's stays refused behind its *turning* bound (the sweep clamps and reverses), which is the §8 triangle limit, not a width one |
| `LOOKUP` is a transfer of its own (§2, since the start) | this step | **false** — it is `SELECT` with identity rows; the second transfer was carrying *evidence* (`imm` vs `lane`), which `Coverage.classes` already carries. Collapsed: 5 transfers, partition byte-identical |
| building nodes per (declared region, cursor) aligns our partition with the editor's (docs/node-partition.md §4) | #112 | **false** — the pairs are one-to-one with the lane keys already built, so `graph_diff` matched stays **71/1648** and the value partition is byte-identical; keying the row on the cursor's *observed value* instead **costs 26 of the 71** (§6). The pair is a better *name* for a node, not a finer partition |
| the arrangement is refused because `_arr_rule` demands a modulus over regions a terminator bounds (this step's brief) | this step | **false — the refusal was one level up.** `_arr_rule` returns `(1, 256)` for both of Commando's cursors. What refused the tune is `_arr_sites` resolving a deref's row index through a **program-wide** local map (`_prog_env`, last definition wins), so three of the four `*ptr[y]` sites read `y` as *some other* definition and land on cell 0; one such site disqualifies the whole pointer. Resolving each site against the locals live at it recovers **both** levels — and §6's "both walked at once: zero sites" was an artefact of the same reading |
| no corpus tune carries a two-level arrangement whose orderlist *and* pattern row are both program text (§4g, §8) | this step | **false** — with the index resolved in order, Commando carries both, and the whole song comes out: 3 orderlists (64/63/123 entries) and 32 patterns (571 rows) **byte-identical to Rob Hubbard's own source**, 0 divergences. Corpus-wide the terminator-bounded region recovers **386 regions over the 649 tunes**, and the value partition is byte-identical |
| a `DIV` phase field would open at most the 4961 fires of the 363 divider-shaped streams that sit off `n-1` (§8) | #113 | **false, by 25×** — with the divisor still required to be declared, only **9 streams (196 fires)** have a period a reload declares and a phase `n-1` misses, and the declared counter seed supplies **4** of them. The phase was never the binding link |
| the trigger domain's residue is an arrangement problem, not a divider one (§4d, §6, §7.4) | #113 | **not where it was aimed** — of the 1101 strictly periodic streams, **989 (13608 fires) have a period no divider's reload declares at all**, and 95 more (1826 fires) only as a *product* of two declared divisors. Before a pattern can decide which tick carries a note, the tick itself has to be nameable, and on 90% of periodic streams it is not |
| a pattern byte explains 0 SID writes, so §4g's claim is what the corpus does not carry (§4g, §6) | #116 | **the rule was wrong, not the corpus** — §4g asks whether the declared byte *is* the byte a register took, and a note or an instrument column never is: it is the row another table is read at. Routed `Index` instead, Commando's attack/decay and sustain/release lanes on all three voices come out generated — **`arr` 0 -> 6498** — from the orderlist and the patterns alone |
| the trigger floor falls once the arrangement can decide which tick carries a note (§4d, §7.4, the two rows above) | #116 | **half true, and the other half is priced** — a `DIV` whose divisor is the row's own duration field generates **3249** of Commando's note-on fires and its tick 11751 more, 0 -> 15000. But observed fires still *rise* 81099 -> 84294, because §4c's 13780 newly generated pulse emits each need a trigger a `RAW` replay did not. Explaining a value costs a fire; the floor only falls when the sweep's own gate is generated too, and §8 names what blocks that (run fragmentation, not evidence) |
| the pulse accumulator's cell is `mut`, so no declaration reaches it and the sweep must seed from an observed byte (§4c, §6) | this step | **false as stated** — the cell is not data, and it is not undeclarable either: it is **state the graph itself produces**, whose first value is the declared post-init image at its own row and whose every later one is a declared step. Expressed as one object per row (§4l), Commando's pulse plane goes `22272/31563` -> **`31563/31563`**, its 597 runs collapse to 4 objects and 3 constants, its 1221 observed seeds retire to **0**, and voice 2 — which generated nothing at all — comes out whole |
| `trigger_share` cannot be gamed by generating less, so it is the floor that replaces the `observed_fires` ceiling (universality.json, #118) | this step | **half true, and the other half is the same mistake one level down** — it cannot be gamed by generating less, but its *denominator still moves with progress*: a `RAW`-replayed byte carries no trigger and a generated one needs one, so explaining 9291 pulse writes took Commando's fires `99294` -> `105944` while the generated count held at `15000` **exactly**, and the share fell `0.1769` -> `0.1714` on a step that replayed 5.5x less. The floor is now `trigger_fires`, the generated count itself: verbatim replay generates none, and no value the layer explains can lower it |
| a run whose seed is observed is the best a sweep can do, and the 597 runs are fragmentation the arrangement must fix (§7, §8) | this step | **the fragmentation was the node identity, not the evidence** — the runs are one object per instrument seen through a register-first partition, and merging them needed no new evidence at all: a route whose register an `Index` node offsets (`sta $d402,y`) and a transfer that emits what another generator holds. Corpus-wide `pw` `110842` -> `111356` over 649 tunes with **no tune generating less**, and `seed` `7542` -> `7479` |
| the arpeggio's residual is a provenance gap: the origin map names the *cursor* the index came from rather than the table cell the read resolved to (§7) | this step | **false, and backwards** — the origin map names exactly the cell the read resolved to. `asl` takes `(note + 12) * 2` past the declared 96-entry pitch table, and 240 of those rows land in the *next declaration* (recovered here) while **1110 land on the driver's own per-voice state** — `pos_54EF`, `ctr_5510`, `idx_54FE`, `pos_54EC`, `idx_54FB`. Those are live RAM, and no declaration reaches them at all: the "cursor" reading had the right cells and the wrong conclusion |
| the bend and the drum are one mechanism, and one generalisation reaches both (§7) | this step | **half true** — the object generalisation (§4m) reaches the drum whole, 644 writes. The bend needs a *second* thing the same rung does not supply: its step is a pattern byte, and the pattern is read through a zero-page pointer the origin map cannot name, so only §4j's chart walk reaches it. Measured against that walk the step is declared exactly (`& $7E` of the song's own `$5520` lane), so it is a build and not a wall |
| `Graph.charts` carries the song as declared data beside the generators (#115) | #116 | **a side-car scores zero** — the law passes at 0% generation, so "law PASS, corpus byte-identical" was satisfied perfectly by a change that generated nothing. Every coverage figure was byte-identical to the pre-#115 baseline. What the song is worth is what it *produces*, and §4k is where it began producing |
| the arpeggio's 1110 spilled writes have no declaration to reach, and emitting a walked cell's value is a route the layer does not have (§7, §8) | this step | **built, and it is one object rule** — a driver cell whose *every* store the text names is §4l's object with three more seeds: a program immediate, a step a guard on the cell's own value pins, and a byte the song copies in. With the read's cell taken off the machine's address bus, all 1112 come out and Commando's residual falls 1808 -> 696 |
| the bend needs a song lane keyed by the destination cell and a `RAMP` whose step is `Node(j)`, so it is a build and not a wall (§7, §8) | this step | **true as stated, and it cost one more element than the entry named** — the two halves are bound by the carry the text takes and *not* by adjacency (they are three cells apart), so §4c's `_paired_cells` does not find them. With `_carried` reading the carry alone, `RAMP`'s step taking `Node(j)`, and the pair route already in the primitive, the last 696 come out: **Commando is residual 0** |

The `SELECT[rel]` row is the one to read before adding to the primitive: a refusal count
says an element *would* be used, never that anything *does* use it. The `LOOKUP` row is
the shape that mistake takes inside the primitive — a distinction that is really about
evidence, spelled as a distinction in the transfer. The last two rows are the same
mistake once more, in the other domain: a refusal was priced from the *output* side (363
divider-shaped streams) and the provenance rule cut it to 9.

The two arrangement rows are a different failure and worth reading as a pair: **a census
is only as good as the reading that produced it.** "Both levels walked at once: zero
sites" was measured, published and acted on for two work cycles, and it was an artefact
of resolving a local against the *last* definition in a procedure rather than the one
live at the site. Nothing about the corpus changed; a lookup did. Before pricing a
refusal, check that the thing being counted is the thing the machine did.

The `coverage` row is the load-bearing correction. **Every value is already produced.** What
this document measures throughout is whether an emit can be *attributed* to a
declaration, never whether it can be reproduced. A residual emit is not a missing byte.

The second lesson is structural. `graph_diff` shows our node partition and the editor's
barely intersect — 1 to 11 nodes match out of 42 to 575 — and the mismatch runs **both**
ways (40 ours vs 575 theirs on one tune, 432 vs 49 on another). Recovery builds nodes
**register-first**: `_lane_key`, `_tree_tables`, `_acc_sites`, `_divisors` and `_walked`
are five separate per-register searches. An editor's song is **object-first**: a pattern
chain, an instrument program, an orderlist, each a declared table advanced by a cursor,
each feeding whatever registers it feeds. A register-first partition cannot converge on
an object-first one, and no amount of per-register refinement will make it.

## 1. The one law

A tracker is a `Graph` of generators consumed by ONE reference evaluator,
`eval_graph`. The law is a single frame-projection equality:

```
framelog.diff(tracker.eval_graph(graph, n), tracker.oracle(prog, trace, n)) is None
```

`oracle` is `frameval.eval_fp` — frameprog's own evaluator, already gated
against the cycle-exact walker by **Gate FP** (docs/frameprog.md §1.4), which in
turn is gated against the P-Code VM by **Gate C** (docs/sidprog-language.md).
So a passing tracker is bit-exact at the frame-projection plane, and there is
exactly one law at this boundary and one function for it: `tracker.gate`.

Two properties make the law meaningful rather than tautological:

- **Completeness floor.** `from_frames(frames)` is a graph of one `RAW` node
  replaying every write in order. It passes by construction at 0% interpreted
  coverage. Refinement *moves emits out of RAW* into typed generators; a refined
  **write** is removed from RAW, so a register may be split across the two and the
  interleaving is fixed by node order — constructed and checked per frame, never
  assumed (§5).
- **Nothing is passed through.** `render` rebuilds every record from the
  interpreted generators plus the explicit RAW residual only — never from the
  observed frame — so a PASS certifies the partition is complete.

Mutation evidence that the law can fail (tests/test_tracker.py): a wrong
table byte, a wrong `SELECT` row, a dropped ordered write, and two swapped
ordered writes are each detected.

## 2. The one primitive

Every structure in every C64 editor — song table, orderlist, pattern,
instrument program, arpeggio table, pitch table, tempo divider — is a table that
some trigger advances, emitting either values or further triggers. There is one
primitive:

```
Generator = (transfer, trigger, route)
  transfer : DIV(n, phase)           # one tick per n input triggers (a clock)
           | SELECT(table, rows)     # emit table[rows[i]]: a table at a row index
           | RAMP(seed, step, bound) # emit seed + step*count, wrapped
           |                         #  seed and step may be Node(j): read on j's emit
           | HOLD(j, seed, at)       # emit what generator j held after its at-th emit
           | EDGE(counts)            # fire counts[f] edges on frame f: the trigger floor
           | RAW(per_frame)          # replay writes verbatim: the value floor
  rows     : ()                      # none recovered: read straight through, wrapping
           | (a recovered run)       # the row indices observation yields
           | Node(j)                 # generated: the row generator j holds
           | Rel(op, delta, base)    # generated and combined: op(base, delta)
  trigger  : frame | Event(i)        # the root frame clock, or node i's edge
  route    : Plane(reg, mask, at)    # a SID register plane, or the bits of one
           | Pair(lo, hi, mask_hi, at)# one 16-bit emit: its low byte and its high byte
           | Rel(reg, mask, op, base)# the emit is a DELTA op combines with base
           | Index                   # the emit is another generator's row index
           | Fire | Raw              # a downstream trigger, or the value floor
  op       : ADD | SUB | XOR         # the store statement's own operator
  at       : none | Node(k)          # the register offset a generator holds: sta $d402,y
  base     : Prev                    # the plane's own previously emitted value
  delta    | Node(i)                 # generator i's current value
           | Const(c)                # a declared base byte
```

**A parameter a driver computes at runtime is another generator's value.** A `DIV`'s
divisor, a `SELECT`'s rows, a relative route's base and a `RAMP`'s **seed** and **step**
all take the form `Node(j)`. The seed is what an accumulator a note-on reloads needs
(§4m) — the ramp restarts from what `j` holds every time `j` emits rather than from a
constant — and the step is what one the song *bends* needs (§4o), read at that same
reload, because a tracker's bend magnitude is a pattern byte and no constant names it. A
step read anywhere but at a reload has no epoch to be read at, and `_check` refuses it.

**A `DIV` divides its input ticks, not the frame.** Clocked by the frame the two are
the same number; clocked by another generator's edge it counts what it receives, so
one divider clocks another and a period only the *product* of two declared reloads
names is `DIV -> DIV` — the shape §8 measured 95 streams (1826 fires) locked out when
the evaluator read the frame regardless of trigger (`_ticks` before `_Fires`).

**A route may name its register through a generator, and a transfer another's value.**
An accumulator a driver keeps per instrument is read into whichever voice's registers
that instrument is playing on, so a node owning one `(voice, register)` plane cannot hold
it. `at` is that third index — `Node(k)`, the offset generator `k` holds, which for a SID
voice is its own `(0, 7, 14)` — and `HOLD(j, seed, at)` is the reader: emit what
generator `j` held, from a declared first value rather than an observed byte. Together
they are what makes an object expressible as one node (§4l); `_check` refuses an offset
no earlier `Index` node settles and takes ownership over every offset that node can hold.

**A `Pair` route is the one emit wider than a register.** All three editors keep a
16-bit accumulator whose sweep carries into its high byte — GoatTracker's pulse and
portamento, SID-Wizard's pulse program, DefMON's pulse and cutoff slides — and a
byte-wide `RAMP` on the low register leaves every carry frame residual. The pair
writes both halves of one emit, the high byte under its own mask (SID-Wizard masks
`$7F`), and `_check` refuses a masked owner on either register: the pair owns both
whole bytes. That an editor which has never heard of the others needs the same
object is the §4f argument, applied one register wider.

**One relative concept, two domains.** `Rel` is a delta combined with a named base
wherever it appears: in the **value** domain the delta is the generator's own emit and
the base is a plane's settled byte, in the **index** domain both are named row sources.
`_rel_ok` is the one validator, over one notion of *field* (`_field_of`: a plane's
masked byte, or an `Index`) — a named source must be a declared `Const`, an earlier
absolute generator of the same field, or the plane's own `Prev`, which is a value and so
has no meaning where the field is a row index. One vocabulary of refusals covers both
domains: an unknown operation, a base that is not a byte, a base no earlier node
settles, and a base that drives another field.

**There is no `LOOKUP`.** It was `SELECT` with identity rows —
`LOOKUP((3,9,4,7))` and `SELECT((3,9,4,7), (0,1,2,3))` emit the same stream, and
`_emit`'s two branches computed the same function. What the second transfer actually
carried was *evidence*: `LOOKUP` meant "program constants, no index explained" and
`SELECT` "a declared table at a recovered row". Evidence has its own home in
`Coverage.classes`, so the transfer is gone and the empty `rows` production says the
same thing structurally — **no row was recovered** — which is exactly what the shallow
`imm` class reports.

A route is **absolute or relative**. An absolute route's emit *is* the byte; a
relative route's emit is a **delta**, and the byte is `op(base, delta)`. Every editor
has at least three tables whose entry offsets a value rather than replacing it —
GoatTracker's vibrato and its wavetable relative-note column, SID-Wizard's detune and
`chord_table`, DefMON's `TR` and `AF` — and none of them is a table read straight
through (the values depend on the base) or a `RAMP` (the step is not constant). §4f is
what recovers one from a binary, and the base is **named, never inferred**: `Prev` is
what the plane holds, `Node(i)` is another generator's current value, `Const(c)` a byte
of the program text. A base read off the observed output is a fitted parameter and is
refused for the reason §4c refuses a fitted `RAMP` step.

A route names a **bit mask** as well as a plane, because a SID register is not
always one generator's output: `$18` is a filter mode ORed with a master volume and
`$17` a resonance ORed with a routing mask, two independent musical objects sharing
one address. A generator supplies only the bits its mask names, several generators
may drive one register, and `_check` **refuses** any two whose masks are neither
equal nor disjoint — two owners of one bit is a malformed graph, not a race for node
order to settle. `Plane(reg)` is `Plane(reg, $FF)`: one owner of the whole byte, the
route as it stood. What a masked group emits is one write of the assembled byte, at
the position of the last of its generators to fire, so a register several generators
drive still takes exactly one write per frame in the order-preserved section and
counts as exactly one emit (§4e).

**Absolutes settle a register, relatives apply to it in node order** — the composition
rule, and `_check` enforces it (`_rel_ok`). A `Node(i)` base must be an *absolute*
generator of the same register and the same mask at a smaller index; a `Prev` base
needs some earlier node that writes that register (a plane generator of the same field,
or the `RAW` floor); a `Const` base needs nothing. A base generator whose value a
relative route consumes does **not** write — the relative route writes the combined
byte — so a relative pair is one emit at one position, exactly as a masked group is.
A graph that names a base no earlier generator settles is refused outright, and a
relative route whose base the evaluator cannot supply emits nothing, so a mis-built
stream drops a write and the law fails rather than a base being invented.

A route carries a **trigger**, a **value**, or an **index**. `Fire` says *when* a
downstream table advances; `Index` says *which row* it reads. Without the second, an
orderlist can beat a pattern forward but cannot name the pattern — which is why
structure recovery read exactly zero on both the recovered and the oracle side, and
why §7.4's original claim (orderlist and pattern as `LOOKUP` nodes routed to `Fire`)
could not have worked: a `Fire` edge carries no value. A `SELECT` whose `rows` is
`Node(j)` reads the row generator `j` currently holds, so the row a note-on selects
becomes an emit rather than observed data. The source is refused unless it is an
earlier `Index`-routed node — the value edge runs the way node order already runs, so
no cycle can form — and an `Index` route no generator reads is refused as dead. An
index past the end of its table emits nothing, so a mis-built arrangement drops a
write and the law says so rather than wrapping to a row nobody proved.

A row may be **relative**, for the same reason a plane route may be: an editor's
transpose shifts the row a pitch table is read at rather than the byte it yields, so
an absolute index cannot carry it. Measured across the three oracles, an absolute-only
index refuses **6953 emits over 23 of 141 modules** — GoatTracker's orderlist
`Transpose` 1690 (2.02% of its freq plane), SID-Wizard's `Transpose`/`octave_shift`
4738 (6.99%), DefMON's `TR` 525 (7.29%) — every one an emit whose note column *is* the
composer's datum, held back only by a declared shift. The same object combines two
index sources, which is what an orderlist entry plus a row within it needs. The
operation is the store's own (`ADD`/`SUB`/`XOR`, the `Rel` set); both halves must be
earlier `Index` nodes or a declared constant — the same `_rel_ok` rule the value domain
takes; a shifted row past the end of its table emits nothing, exactly as an absolute one
does. **It has no user yet**: 6953 is what the three oracles *refuse*, and each of them
still refuses it rather than emitting `SELECT[rel]` (§0, §8, docs/gt-oracle.md §4.5).

**The loop needs no machinery**: `SELECT` already advances modulo its length, so an
orderlist wraps to its first entry by construction. What §7.4 called a
back-edge was there all along.

**A region's extent is a terminator, not always a modulus.** A `RAMP`'s bound is a
wrap, and a 6502 driver more often ends a region with a *compare*: the cursor is reset
where the byte it just read equals an immediate the program text names. §4j reads that
directly — the declaration's own extent floors the region and the compare ends it — so
a region whose length no `AND`-immediate encodes is still program text. The song a
`Graph` carries (`Graph.charts`) is those regions and the rows the cursor's own walked
increments cut them into: **declared data at program-text offsets, with no observation
in it at all**, which is stronger evidence than any generator in the value domain has.

The phase, though, is real and is **this** layer's. `DIV(n, p)` fires at `p, p+n, …`, so
a graph whose orderlist is clocked by one writes nothing until the first tick — it
refuses to invent entry 0. That is the settled three-editor verdict of §8 seen from the
other side: **the phase belongs to the arrangement, not to the divider**, and the field
is therefore the arrangement's to supply. The recovery reads it off the *declared* byte
the post-init image leaves in the counter (§4i) and never off the fire pattern; `n-1`,
the reading before the field existed, is what a counter seeded at its own reload gives —
and what an unstaged cell's zero gives too, since `(0 − 1) mod n = n − 1`. So the field
is a generalisation of the old behaviour and not a parameter beside it.

`RAW` and `EDGE` are the two floors — the residual in the value domain and in the
trigger domain. Refinement replaces them: a value moves out of `RAW` into a typed
transfer, and an `EDGE` stream is replaced by `DIV` where a divider generates it
(§4d) or by the arrangement (§7.4) where one does not. Both are explicit, and
**both are counted**: `Coverage` reports the two domains as two numbers and never
sums them, so neither can hide behind the other (§6).

Identity is behavioural: two generators with the same triple are the same
generator, whatever editor structure they came from. A pitch table and an
arpeggio table are both `SELECT`, differing only in trigger and route — so
"typed vs raw" is a property of one node's emit, not two kinds of node, and
interpreting a node means refining its emit. A whole tune is a graph of these
nodes wired by their triggers, with two distinguished members: the pitch table
(`Graph.freq_table`, the note→freq `SELECT`) and the root frame clock
(`Graph.cadence`).

`eval_graph` propagates triggers from the root frame clock and projects through
`framelog.canonical` — the ONE projection, never a second one.

## 3. What is implemented

- **Pitch `SELECT` per voice** (§4) — the note lane. Accepted-note freq words
  are emitted by a plane-routed table read straight through; every other write stays
  in `RAW`.
- **ctrl/AD/SR `SELECT` per voice** (§5) — the instrument lane: a declared bank
  lane read at a recovered row, fired by the voice's note-on `EDGE`. For `ctrl`
  the same lane also supplies the gate images, so the waveform is declared data
  and only bit 0 moves.
- **freq/pw/filter `SELECT` per register** (§4b) — the last-write-wins planes,
  transliterated from the store statement: where the value expression names a
  declared table, the emit is that table's lane at the row the read cell recovers.
  $15-$18 are one global filter, so they take a register class of their own.
- **pw/cutoff `RAMP` per accumulator** (§4c) — the sweep. Where the store reads a cell
  the play code steps, the origin map is *queried* for where that step byte was copied
  from; where it was copied from a declaration the sweep is generated from that byte —
  one observed seed per run, then every further emit predicted.
- **The accumulator a note reloads** (§4m) — a plain-RAM cell every store either steps by
  the text's own immediate or sets from a declared byte at the row the machine's address
  bus named. One node per (cell, seed lane): a `SELECT` over the lane, a `RAMP` seeded
  from it, a `HOLD` per read.
- **Clocks** (`_clocks`) — cells the play code steps by one, read off the frame
  program's procedures: `dec` + reload is a divider (its reload is
  `frames_per_tick`), a free `inc` is an LFO phase.
- **`DIV` over a declared divisor** (§4d) — the trigger floor's one refinement: an
  `EDGE` stream a recovered divider's own reload generates becomes a `DIV`.
- **The cascade** (`_clock_node`, `_div_watch`) — a stream whose period only the
  *product* of two declared reloads names becomes `DIV -> DIV`, admitted only where
  the inner divider's own dec statement is watched executing exactly on the outer's
  ticks and the declared phases match both ways. §6 measures what it reaches.
- **A masked route per field** (§4e) — where a store statement partitions a register's
  bits, each field is its own generator: a declared byte at a recovered row, or that
  statement's own constant. `$18`'s mode and volume are the case that reaches the
  corpus, and it reaches 5 tunes of 649.
- **The relative route** (§4f) — where the store statement adds, subtracts or XORs a
  declared byte against a base the same statement names, the emit is that delta and the
  route combines it. It reaches 316 emits on 6 tunes of 649; the oracles say why that is
  the small half of the answer.
- **The arrangement** (§4g) — a pattern is the declared block a resolved deref proof
  names, read at a row an `Index`-routed `RAMP` generates from the counter's own walk.
  It reaches **0 emits on 649 tunes**, and §6's refusal chain is what that step bought.
- **The node identity** (§4h) — a declared-lane node is keyed by the **load base the
  program text indexes and the cursor cells that index it**, not by the declaration that
  contains it. A declaration tiles a whole data block, so the text's own bases are what
  resolve the composer's objects inside it. Every byte and every row is unchanged; §6
  measures what the finer identity buys, and the answer is nothing yet.
- **The sequencer chain** (§4i) — the tick clock, the row cursor it beats and the table
  that cursor rows, built *inside* the graph instead of being reported beside it. It
  reaches **1 tune of 649** for the row and **4** for the tick; §6 measures the
  attrition link by link, and the binding link is not the one §7.4 named.
- **The song** (§4j, `_charts`) — the arrangement itself: every terminator-bounded
  region a proven pointer names, cut into rows by the cursor's own walked increments and
  decoded into the fields the program text's `AND`-immediates name. It reaches **386
  regions over 649 tunes**, and on Commando it is the whole tune — 3 orderlists and 32
  patterns, byte-identical to the composer's own source (§6).
- **The accumulator as an object** (§4l, `_obj_streams`) — an accumulator cell that lies
  in a declared region at a cursor-named row is one persistent object per row: its first
  value the post-init image, its later ones the declared step, and every read of it an
  emit of what it holds. On Commando it makes the pulse plane whole; corpus-wide it
  reaches the tunes whose engine keeps one and moves `pw` 110842 -> 111356 over 649.
- **Instrument banks** (`_instruments`) — const table bases feeding a
  ctrl/AD/SR store.

Every `ctrl` write whose byte never reaches a declaration renders from `RAW`, and
so does every freq/pw/filter write whose store statement names none. The coverage numbers
in §6 say so plainly; nothing is claimed that a generator does not reproduce.

## 4. Pitch recovery from declared tables

The pitch table is an equal-tempered word table. Base, pairing and extent all
come from the **declarations** — no byte of the image outside a declaration is
read, and the ET validators only confirm.

- **Candidate windows** (`_candidates`) — every declared const table and cobase,
  read as an interleaved 16-bit table (little- and big-endian) and as a split
  lo/hi pair with every other declared base. Adjacent declarations form one
  contiguous const run (`_avail`): the boundary between them marks another read
  base, not another data class.
- **ET validators**, strongest evidence first: the whole window's median
  semitone/octave ratios (`_median_et`), the leading chromatic run
  (`_leading_run`), a gapped semitone-indexed table (`_sparse_et`), per-octave
  segments split by zero markers (`_segmented_et`), the longest interior
  chromatic run (`_longest_run`), and the monotone chromatic-lattice run
  (`_lattice_et`, which accepts period tables and diatonic subsets since a pitch
  table's values are `ref·2**(k/12)` whatever the physical quantity). Every
  reading of a window is a candidate.
- **Choice** (`_pitch`) — ranked by *explanatory power*: the share of the freq
  words the projection actually wrote that the table holds **exactly**, counted
  per frame. Exactness, not proximity: a dense decoy window is within half a
  semitone of anything, but only the real table holds the words the player
  wrote. Ties break on evidence tier, then extent.
- **Inversion** — a multi-octave table inverts by nearest word
  (`_note_direct`); a one-octave table by `words[semitone] >> octave`
  (`_note_shift`), the layout of computed-base drivers. Inversion is
  detune-tolerant (within half a semitone) but gated by a **continuity filter**:
  a detuned frame is a note only as vibrato on the current note (same index) or
  as a fresh exact anchor, so an excursion to an unrelated note stays residual.

### Snapshot soundness lives upstream

Every table is read from the post-init image, so a declaration is const data
only where the play phase never writes. That invariant lives in `datadecl`, not
compensated for here — which is why the tracker has no `_extend_et`, no `mem0`
scan and no per-entry stability ranking. The extent stops at the first
play-written cell above the observed run (`_sound_hi`) and the writes inside it
are named per record offset by `mut` (`_mut_offs`): a lane of a record array, a
cell of a flat region. The tracker **consumes** `mut` (`_lane_key`): a source cell
at one of those offsets is refused, because a play-written cell is not const data
and agreement with the snapshot is then coincidence rather than a const read. The
reading is per record — `(cell − base) mod stride` when strided, `(cell − base)`
otherwise — and getting it wrong silently matches nothing, so it is asserted
directly (`test_mut_offsets_are_lanes_when_strided_and_cells_when_flat`). Refusing
those srcs costs 2834 interpreted emits on the instrument planes (`sr` −1512,
`ctrl` −954, `ad` −368) and 5926 on the lww planes (`pw` −5322, `freq` −604);
§6 reports that cost apart from the gain it is paid out of.

## 4b. The last-write-wins planes: the table the store statement names

freq, pw and the $15-$18 filter tail are last-write-wins, so a frame's value stands
on its own and there is no ordered section to place a stream against. What carries
the claim is the **statement tree**: `_tree_tables` reads, per register class, the
declarations the program text stores into that class, and only those are eligible.

- **The store names the table.** A store's value expression is walked with its
  locals resolved to their in-procedure definitions and a staged byte followed
  through the cell it was stored to (`_read_bases`) — the same origin hop
  `frameval` makes at runtime (docs/frameprog.md §1.4), made statically. A read the
  value only *indexes through* is not followed: an index cell is not the byte's
  origin, however its byte agrees.
- **The read cell recovers the row.** The tree names the declaration; it cannot
  evaluate the index, so `frameval.eval_src` still supplies the cell and the row is
  `(cell − base) // stride` as in §5. The emitted byte is the declared one.
- **Per frame, not per register** (`_lww_streams`). Each explained frame fires the
  lane's `EDGE`; the rest fall through to the note lane (§4) or stay in `RAW`. A
  declared lane outranks the note reading of the same byte, since the note reading
  emits the *observed* word and the lane emits a *declared* one.
- **The filter is its own register class** (`_class_of`, `_sid_class`). A voice store
  site is keyed by `reg % 7`, because one voice-generic site serves all three voices;
  $15-$18 are one global filter and that key would alias them onto freq_lo/freq_hi/
  pw_lo/pw_hi, so a table a pw store names would explain a resonance write. They take
  a class of their own instead. Nothing else about the plane differs —
  `framelog.canonical` already projects $15-$18 last-write-wins in a section of their
  own (docs/frameprog.md §1.1) — so the same `_lww_streams` reads them. The held rows
  of §5 stay per voice and skip them; the sweep of §4c does not, because $15/$16 is an
  accumulator in exactly the way pw is, and it reads them under their own class.

On Commando `sid.v1.freq_hi[w9] = m_5429[t5]` and its `freq_lo` partner become two
`SELECT` nodes over the `+1` and `+0` lanes of the declared `m_5428[192] stride 2`
table, carrying **one shared row stream per voice** — the row is the semitone index,
so the pitch table and the note lane are finally the same object. `pw_hi` is the
`+1` lane of the `$5591` instrument bank at the instrument row; `pw_lo` is `mut`
(the play code writes the pulse accumulator back) and is refused.

Requiring the tree to name the table is stricter than a bare provenance search over
every declaration, and measurably so: over the corpus it declined 5693 emits' worth
of lane classifications a blind search would take. That is the point — a byte whose
declaration the program text never names is not explained by that declaration.

**A row may run past its own table's end** (`_spilled`). A 6502 index reaches 256 bytes
from the base a load names, and nothing stops a driver indexing past its declaration's
extent: Commando's arpeggio reads `m_5428[(note + 12) * 2]`, and on the top octave that
lands in the *next* declaration. The statement still names the base, the address is still
the machine's own, and the byte is still `mem0[src] == val` at a non-`mut` offset, so the
emit is that declaration's lane at that row. The window is what keeps this honest: a
declared cell no named base reaches is refused exactly as before, which is what stops an
*index* byte agreeing with a value byte by coincidence
(`test_the_lww_planes_read_the_table_the_store_names_not_the_one_it_indexes`). One
reading, `_declared_at`, serves the lane search and the sweep's own eligibility check, so
the two stages can no longer claim the same write and move `Coverage.total`.

## 4c. The pulse sweep: a `RAMP` whose step the origin map names

PWM writes pulse width every frame and a swept cutoff writes $15/$16 every frame,
which is why `pw` and `filter` are the two largest residuals. Both are bounded
accumulators and `RAMP(seed, step, bound)` is the primitive for them — but these are
last-write-wins registers, so a generator that merely reproduces the end-of-frame
value passes the law without explaining anything. **The claim here is about
provenance, not about matching values**: the step must be a byte a declaration holds,
and what says which byte is the machine, not a static reading of the tree.

- **The store statement names the accumulator** (`_acc_sites`, `_accumulators`). A
  non-SID store whose value adds (or subtracts) one term to a read of *its own cell*
  is an accumulator; a pw or cutoff store whose value reaches exactly one such cell
  is that accumulator's output. Two accumulators reaching one store refuse it.
- **The step is queried, not walked** (`frameval.eval_watch`, docs/frameprog.md §1.4).
  Step, bound and rate are copied out of a table into RAM at note-on and **no SID
  store ever reads those cells**, so the origin rule of §6 — which reports origins
  alongside SID store sources — cannot name them however well the map is kept.
  `_acc_sites` hands the evaluator the statement the arithmetic happens in (the store,
  or the assignment its value resolves through, since a store of a bare local carries
  no origin at all) and `_acc_pools` reads the origin off that statement's own
  execution: the cells its byte derives from, less the cell it wrote.
- **Per execution, and that is a third of the figure** (`_acc_pools`). A staging cell
  is re-staged mid-run — a new note-on copies a new step — and one statement serves
  three voices inside a frame, so a snapshot of the map names the last row written
  rather than the row each read used: 27246 pw emits identified per execution against
  17324 per frame and 17111 at end of run.
- **The run is the observation's, the step is the declaration's** (`_runs`,
  `_acc_streams`). A maximal constant-nonzero-delta run of the register's own emits is
  the candidate, and it is kept only where **every** stepped emit's accumulator
  execution reports an origin inside a declaration, at an offset the declaration does
  not name `mut`, whose snapshot byte equals that step. A step fitted to the output
  would pass the law while explaining nothing; the declared byte is what refuses it.
- **Regenerate or refuse whole.** Given the seed and the step the whole run is
  determined, so one stepped emit whose origin no declaration names refuses the run
  entire and not that emit. A run that predicts nothing — a single emit, or a step of
  zero — is refused for the reason `DIV(1)` is (§4d).
- **`bound` is the store width, not a fit**: a byte cell's bound is `$100`, and where the
  text stages the arithmetic in a scratch byte its own `AND`-immediate is that half's
  width — `and #$0f` on a high byte makes the pair's bound `$1000`.
- **Two byte cells are one 16-bit accumulator where the text takes the carry**
  (`_carries`, `_paired_cells`). The high byte is the byte *above* the low one and its
  store adds the carry out of the low one's own arithmetic (a 6502 borrow is the same
  statement written `INT_LESSEQUAL`); that is the program text saying the two are one
  number, not the two addresses happening to be adjacent and not the values they hold.
  Such an accumulator emits through a `Pair` route, whose high mask is that
  `AND`-immediate — §2's one route wider than a register, built by the recovery at last.
  A byte-wide `RAMP` on the low register leaves every carry frame residual, which is what
  Commando's whole `$D403` plane was.
- **A `RAMP` may turn, and both bounds are compare immediates** (`_turn_of`). `RAMP` is
  `(seed, step, bound, turn)`; `turn` is `()` for a ramp that only wraps, else
  `(low, high)` in **high-register units** — the step goes negative where the new emit's
  high register equals `high` and positive where it equals `low`, exactly the 6502's own
  `cmp`. The two immediates are the ones guarding the *direction cell's own step*: a
  compare that guards a cell the text merely **sets** names no bound, because a direction
  that cannot come back is not a turn. **A bound fitted to observed output is refused**,
  as a fitted step is.
- **A turn is history-dependent and the evaluation stays linear** (`_turned`, `_value`).
  `_emit` is a pure function of the trigger count, so a turning ramp recomputed from its
  seed each fire is O(n^2); `_run` carries `(count, value, direction)` per node and steps
  forward one fire at a time. docs/tracker-text.md records that same mistake costing
  45.8s -> 17.7s once already.
- **One store adds and another subtracts, and the sign is the store's** (`_acc_sites`).
  The signs a cell is stepped with are a *set*: `signs.setdefault` let the first store seen
  fix the sign and `_acc_streams` then computed the wrong declared step for every run in
  the other direction.
- **The arithmetic can happen in a scratch byte** (`_steps_own`). A store's value is
  followed through the locals it names *and* through the staging cells it is copied from,
  so `pwl = scratch` where `scratch = rate + pwl` is an accumulator; the statement watched
  is the one the arithmetic is in, which is what the origin map can answer for.
- **The step is a declared byte under a mask the store statement names** (`_step_mask`).
  `prate = pulse & $e0` puts the rate in the top three bits and the gate's reload in the
  bottom five, so the declared byte and the step are not equal and byte-equality alone
  refuses the run. The mask is the `AND`-immediate every store to the staging cell agrees
  on, and where they do not agree the whole byte stands.
- **The seed is observed and reported as such.** The accumulator's value is state it
  produces, not data it reads. Each run takes its seed from its own first emit and
  predicts the rest, so a re-staged step starts a new run at a new observed seed;
  `Coverage.classes` keeps `seed` apart from `ramp` so the generated figure never
  absorbs the observed byte.
- **A declared lane outranks the sweep** (§4b). An emit whose own source cell reads a
  declared lane is that lane's emit and is not a candidate for a run at all.

**The static reading is replaced, not kept beside it.** Until this step the step byte
was resolved off the tree: the accumulator's other addend had to reach exactly one
declared cell, read at the row the voice last held (the same recovered row §5 uses).
The two rules are not nested — a held row can name a lane the staged byte did not come
from — so they compose by replacement, and the cost is measured from both sides.
Allowed to fit (any declared byte at the held row equal to the observed delta) the
static reading reaches **1420 of 237610** constant-delta pw emits over 13 tunes and
**0 of 46369** cutoff emits, against the query's 27246 over 121 tunes and 3353; on two
tunes it reaches more than the query does — `MUSICIANS/D/Diamond/Butterfly_2` 137
against 0 and `MUSICIANS/A/Amaze/Foolish_Maniacs` 200 against 116. Neither was ever
shipped: the static rule as it stood generated a sweep on **one tune of 646**,
Commando's, and the query generates that one better (139 emits over 13 runs against
138 over one). So the replacement costs **no realized emit on any tune**, and what it
declines is 221 emits of a held-row reading on two. That is a selection between
*admissible* explanations rather than a fit to the output: both readings name a
declared byte, but the held row is one *another* read established while the query is
the copy the machine actually made, and where they disagree the machine is right by
construction.

Commando has **two** accumulators on one register class and the recovery keeps both.
`fx & 8` set runs `ins_pwl += pulse` and writes `$D402` alone: a byte cell, the sweep
§6 already reported. `fx & 8` clear runs the 12-bit one — `ins_pwl:ins_pwh`, step
`pulse & $e0`, gated by a counter reloading from `pulse & $1f`, turning where the masked
high byte reaches `$0e` going up and `$08` going down, writing `$D403` and `$D402`. A
frame that writes both registers is a pair emit and a frame that writes only the low one
is the byte accumulator's, which is the route's own arithmetic and not a segmentation:
one emit of a `Pair` makes two writes, so a frame with one write is not one.

On Commando `m_5591[idx_5518] = (m_5591[idx_5518] + idx_5507 + cflag)` feeding
`sid.v1.pw_lo` is the byte sweep, and the query resolves `idx_5507` to `m_5597[t1]`, the
`+6` lane of the declared `$5591` bank. Perturbing the declared byte at `$55A7` moves
the `RAMP` node's step field and the whole emitted stream with it, law green
throughout: the sweep is generated, not replayed. tests/test_tracker.py drives the
same evidence hermetically — a step staged in RAM from a declared byte, an identical
stream staged from an undeclared cell refused, a step at a `mut` offset refused, a
zero step and a single-emit run refused, a run one undeclared origin refuses whole,
and a wrong step and a wrong seed each failing the law.

## 4l. The accumulator as a persistent object the graph carries

§4c reads a sweep as a *run*: a maximal constant-delta stretch of one register's emits,
seeded by its own first observed byte. That is the right shape for an accumulator in a
scratch cell and the wrong one for the accumulator every C64 instrument table keeps,
because such a cell is not a cell at all — it is **one object per instrument row**, and
the run boundaries are where the song moved to another one.

Commando's is `ins_pwl:ins_pwh`, the `+0`/`+1` lanes of the declared `$5591` bank at
stride 8. The play phase writes them, so `datadecl` marks both offsets `mut` and the
declaration rule strips them: a const read of a play-written cell is refused, and rightly.
What the refusal misses is that the same cell read as **state** is not data at all.

- **The object is the cell, and its rows are the declaration's** (`_obj_voices`,
  `_obj_named`). A write whose origins name a cell of the accumulator's own declaration
  at the accumulator's own record offset is a *read of that object*; every other write of
  the class is a *step*. Both readings are the origin map's, not a segmentation.
- **Its first value is the declared post-init image** (`_obj_seed`). That is const data at
  init, mutated only by the play phase whose steps the graph itself produces — so it is a
  `SELECT` from a declaration, not an observed seed. `Coverage.classes` reports `seed` 0.
- **Its later values are the declared step** (`_obj_step`). Each execution's own origin
  pool must name exactly one declared byte at a non-`mut` offset, under the mask the text
  applies (§4c); two arms disagreeing about an object's step refuse it whole.
- **Which voice a step belongs to is the machine's own order** (`_obj_walk`). One loop
  iteration serves one voice, so the k-th execution of the accumulator statement in a
  frame is the k-th voice whose write that frame names no object. Measured over
  Commando's 11750 frames the two counts agree on **every frame** (0/1/2/3 executions
  against 0/1/2/3 unnamed writes, 11750 of 11750).
- **The carry says which halves are one number, per arm** (`_obj_arms`). A low arm is
  16-bit where some high root takes the carry out of *this* arm's own arithmetic, its
  other terms included — so Commando's `fx & 8` byte arm and its 12-bit arm are told
  apart by the program text rather than by their masks.
- **Regenerate or refuse whole.** The object is walked from its declared image by its
  declared steps and every read is *checked* against the byte the register took; one
  contradiction refuses that object entirely, and its writes stay `RAW`.

### One object, three registers: the destination is an index

The accumulator is keyed by **instrument** (`y = m_5518`), its counters by **voice**
(`x`), and its destination register by a third index (`w9 = m_54EB`, itself
`m_54E8[x]`). A node owning one `(voice, register)` plane cannot hold that, which is why
the recovery could not merge the runs. The primitive gains the missing element:

- **A route may name its register through a generator** — `Plane(reg, mask, at)` and
  `Pair(lo, hi, mask_hi, at)`, where `at` is `Node(k)` and the emit lands at `reg` plus
  the offset `k` holds. That is `sta $d402,y` written down. The offsets are the SID's own
  per-voice layout `(0, 7, 14)`, and `_check` refuses an offset no earlier `Index` node
  settles and takes register ownership over every offset that node's table can hold.
- **A transfer may be another generator's value** — `HOLD(j, seed, at)`: emit what
  generator `j` held after its `at`-th emit of this frame, or the declared `seed` before
  it has emitted at all. It is the value domain's `Node(j)`, which `rows` and the relative
  base already name; what it adds is a reader whose emit *is* that value.
- **`at` is the machine's order, not a fit.** A note-on that lands before the frame's
  steps must read what the object held then, and one that lands after must read what it
  holds after. Both are the same datum as the write order the recovery already reads.

**Measured, Commando whole tune (11750 frames, law PASS):** pulse `22272/31563` ->
**`31563/31563`**, residual `11983` -> **`2692`**, generated `89.8%` -> **`97.7%`**,
observed sweep seeds `1221` -> **`0`**, sweep runs `597` -> **4 objects and 3 constants**,
nodes `1311` -> **`121`**, trigger streams `642` -> **`32`**. Voice 2's pulse plane, which
generated nothing at all because 63.2% of its frames were note-on copies rather than
sweep steps, is now whole: the copies *are* the object, emitted rather than replayed.

**Corpus-wide the object reaches the tunes that keep one** and no other: 649 tunes at 200
frames, `pw` `110842/565009` -> `111356/565009`, `seed` `7542` -> `7479`, generated fires
unchanged at `1140`, and **no tune's generated count falls**. It is one engine's shape,
and the engine is Rob Hubbard's.

## 4m. The accumulator a note reloads

§4l's object is a cell a declaration **contains**, whose first value is the post-init
image. A driver's per-voice accumulator is usually not one: `ctr_551A[x]` is plain RAM,
and what makes it program text plus declared data all the same is that every store to it
is one of two things the text names.

- **Its value arrives from a reload** (`_reload_seed`). A store the text does not compute
  sets the cell, and that execution's own origins must name exactly one declared byte at a
  non-`mut` offset — which is §4b's rule for a lane emit, at a cell instead of a register.
  The row is the machine's own address bus, the byte is the declaration's.
- **Its later values are the walk the text names** (`_reload_walks`). A store `_walk_of`
  reads as `("step", d, wrap)` steps it, exactly as §4j reads a cursor. A store neither
  rule covers — arithmetic over a cell no declaration reaches — leaves the object
  **undefined until the next reload**, so nothing is claimed across it.
- **One object is one (cell, seed lane).** The seed is a `SELECT` over one declared lane,
  so an object whose reloads name two declarations is two objects with disjoint fire
  vectors, not one refused. Keying on the cell alone made recovery depend on which lane
  came first, and so on how many frames were run.
- **A `RAMP`'s seed may be `Node(j)`** — the last scalar parameter of the primitive to
  take the form `DIV`'s divisor and `SELECT`'s rows already have. The ramp restarts from
  what `j` holds every time `j` emits, which is what a note-on reloading an accumulator
  does; `_check` refuses a seed no *earlier* index node settles, and refuses one on a
  turning ramp, since a reload has no direction.
- **Regenerate or refuse whole.** Every read is checked against the byte the register
  took, and one contradiction refuses that object entirely — the object's row is bound to
  the read's own voice, and that binding is what the check proves.

**Measured, Commando whole tune (11750 frames, law PASS):** the drum plane comes out
whole — `freq` `51478/54170` -> `52362/54170`, residual `2692` -> **`1808`**, `ramp` on
the pitch plane `0` -> **`644`**, observed sweep seeds still **`0`**. Corpus-wide, 649
tunes at 200 frames: `law` 649/649, interpreted `776678` -> `794952` over **138 tunes**
with **none generating less** and every tune's `total` unmoved.

## 4n. The driver cell as an object, and the song lane that reloads it

§4l's object is a cell a declaration **contains**; §4m's is plain RAM a declared byte
reloads. This is the same object with **every** store the program text names, and it is
what a read that spills onto the driver's own state needs (§7, §8's retired entry).

- **Four stores name a value** (`_reload_walks`). A program immediate the text stores;
  a step under a guard that pins the cell's own value (`_pinned`) — a 6502 branches on a
  compare and the *taken* arm names the value, §4j's reading of the compare that resets a
  cursor; a byte the song copies into the cell (`_cell_lanes`); and §4m's reload whose
  origins name one declared byte. Commando's pulse direction counter is the guard case:
  `ctr_5510[x] = ctr_5510[x] + 1` under `ctr_5510[x] == $00` is a store of `$01`, so a
  cell two arms step by ±1 is one reload and one step and not two steps that disagree.
- **A song lane is keyed by the cell, not by the table it indexes** (`_cell_lanes`).
  §4k names a row byte by the table the cell it flows into indexes — a note column, an
  instrument column. `_row_walk` already carries every row byte's destination set with the
  guards resolved, so the cell itself is the prior name, and a byte the song copies into
  plain RAM is declared data at a program-text offset exactly as a note column is.
- **A cell the run never writes still holds its post-init image** (`_obj_cells`). Every
  store is watched, object or not, so that is a fact about the run rather than an
  assumption; a store whose address the run does not report withdraws the reading whole.
- **A read is a copy, and the cell is the machine's address bus** (`_reload_reads`). The
  store's value must resolve to a single load with nothing done to it — arithmetic
  combines cells and names none — and which cell is then the store's own origins, the
  cell the load addressed with its origin ahead of it, or the capture it read through
  where a staged copy collapsed them. A cell outside one index register of both an object
  base and the reading statement's own base is no object's and no read's (§4b's rule,
  applied at both ends).
- **The object has one clock: its own updates.** The seed reads its lane at the row the
  reload took and *no row* at a step, and a ramp whose seed emits nothing walks on. Machine
  order is then the graph's order rather than an ordinal it must infer, which is what makes
  a cursor the text steps *and* resets inside one frame expressible at all.
- **Regenerate or refuse whole.** Every read is checked against the byte the register
  took and one contradiction refuses that object entirely, exactly as §4l's does.

On Commando this is the arpeggio's spilled row. `m_5428[(note + 12*(frc & 1)) * 2]` runs
past the declared 96-entry table onto the driver's own per-voice state, and the origin map
names the cell exactly: `pos_54EF` the pattern row cursor (576 writes), `ctr_5510` the pulse
direction counter (330), `idx_54FE` the instrument index (96), `pos_54EC` the orderlist
cursor (72), `idx_54FB` the note index (36), `m_54F8` (2). The first three are walks, the
next two are song lanes, and the last is a cell whose post-init image the read met first.

**Measured, Commando whole tune (11750 frames, law PASS):** residual `1808` -> **`696`**,
freq `52362/54170` -> `53474/54170`, generated `98.5%` -> `99.4%`, observed sweep seeds
still `0`. Corpus-wide, 649 tunes at 200 frames: `law` 649/649, interpreted `794952` ->
`892071` over **348 tunes** with **none generating less**, every tune's `total` unmoved and
`seed` unmoved at `7479`.

## 4o. The accumulator the song bends

§4n's object is one byte and one step the text names. Commando's pitch bend is neither: it
is a 16-bit accumulator whose step is a **pattern byte**.

- **The carry binds the halves, not adjacency** (`_carried`). §4c pairs a cell with the
  byte *above* it, and a driver's halves need not be adjacent — `m_551D[x]` and
  `ctr_551A[x]` are three apart. What says the two are one number is the high store taking
  the carry out of the low one's own arithmetic, which is program text.
- **The step is the song's own byte, under the mask the text applies** (`_bend_lane`). A
  driver stages the step in a scratch byte it also uses for other things, so the store that
  names it is the one live *at* the arm. Commando's is `m_5520[x] & $7E`, and the sign is
  the arm the text's guard on bit 0 of that same byte selects.
- **A `RAMP`'s step may be `Node(j)`** — the last scalar parameter of the primitive to
  take the form the divisor, the rows and the seed already carry. The step is read where
  the seed is, at the reload, so a ramp with no reload has no epoch to read one and
  `_check` refuses it.
- **One arm walks one run.** The graph's ramp reads one step per epoch, so a run two arms
  step is claimed by neither — the recovery checks what the graph will emit, not what the
  machine did.
- **Both halves of an emit are one `pair` write**, so a read of one half alone claims
  nothing, and a SID store is a read only where its value expression *is* the arm's own —
  the text saying so, never a byte that agrees.

**Measured, Commando whole tune (11750 frames, law PASS):** residual `696` -> **`0`**, the
pitch plane `53474/54170` -> **`54170/54170`**, 501 pair emits over two arms (299 down, 202
up). The corpus is unmoved at 200 frames: Commando's bend does not enter before frame 2000,
and no other cached tune carries a 16-bit accumulator a song byte steps.

## 4d. The trigger domain: a `DIV` whose divisor is a declared reload

`EDGE` is the trigger floor and `DIV(n)` is the one transfer that can lift a stream
off it. The rule is §4c's, applied to triggers: **the divisor is program text, never
a period fitted to the fire pattern.** A period read off the output would pass the
law while claiming a structure the code does not have — the law cannot tell the two
apart, so the provenance rule must.

- **The divisor is what the play code reloads** (`_divisors`). A recovered divider
  (`_clocks`: a cell the play code steps down) is reloaded either with an immediate
  of the program text or from a declared byte at an offset the declaration does not
  name `mut`. Nothing else is a divisor: a reload out of a RAM cell is runtime state,
  and its post-init byte agreeing with an observed period is coincidence, exactly as
  in §4/#61.
- **A divisor of one is refused.** `DIV(1)` divides nothing — it is the root frame
  clock — so it would "explain" every stream that fires on consecutive frames with a
  byte that only happens to be `1`. That is the same refusal §4c makes of a `RAMP`
  whose declared step is zero: a generator that predicts nothing is not one.
- **The phase is the primitive's, and it belongs to the arrangement** (`_generates`,
  `_sequencer`). `DIV(n, p)` fires at frames `p, p+n, …`, and `p` is the *declared*
  counter: `(mem0[cell] − 1) mod n`, paired with the divisor its own reload names. The
  whole stream must match in both directions: a missing tick refuses as loudly as a
  spare one. A phase read off the fires is refused; §4i is where the field comes from
  and §6 prices it.
- **The law does the verification.** A `DIV` node replaces an `EDGE` in place, so its
  downstream `SELECT`s fire on exactly the frames the divisor says; a wrong divisor
  moves every emit after the first and `tracker.gate` fails
  (`test_mutation_a_wrong_divisor_is_detected`).

§6 reports what this returns, and the answer is a measured near-zero: the recovered
clocks generate **300 of 305119 fires** over 3 tunes of 646, and **304 of 307225** over
4 of 649 once §4i supplies the phase. That number is the point of the step — it is the
input to scoping §7.4, and it is honest in a way a fitted period would not be.

## 4e. One plane, two generators: the bit partition the store statement names

`$18` writes a filter mode and a master volume in one byte, `$17` a resonance and a
routing mask. Neither is a declared byte, so `_lane_key` refuses both (§5's
`mem0[src] == val` pair), and the whole write falls to `RAW` — 16597 `$18` emits and
7105 `$17` emits over the corpus (§6). The masked route of §2 expresses them, under
the provenance standard §4b applies to a table and §4c to a step:

- **The mask is the program text's** (`_term`, `_partition`). A store whose value is
  an `OR` of terms partitions the byte where the text names every term's bits but
  one: a constant owns the bits it sets, an `AND`-immediate owns its mask, a shift
  moves that mask, and the one term left over takes the rest. Masks that overlap, or
  that leave a bit unowned, are **not** a partition and the site is refused. A mask
  read off the observed bytes — "these bits never change, so call them a field" —
  explains nothing and is never taken.
- **Every field must be sourced** (`_decompose`). For each field the emitted bits are
  either that statement's own constant, or a declared byte at the row the read cell
  recovers — the same `_lane_key` pair as §4b, applied to `val & mask`. One field the
  declarations do not hold refuses the whole write; a lane byte that spills into
  another field's bits refuses it too, since the fields would then not be disjoint.
- **Keyed per register class, like §4b** (`_partitions`). The tree names the partition
  for a register class, not for a single write, so one voice-generic store site serves
  its whole class.
- **A group fires together** (`_mask_streams`). Every field of an explained frame
  fires, the last of them writes the assembled byte, and the masks a register's fields
  take are fixed at its first explained frame — one field has one owner. A frame whose
  decomposition names a different owner for a mask stays residual.
- **The last-write-wins planes only.** ctrl/AD/SR is a *sequence* of whole-byte writes
  (§5), not a partition of one byte, so a masked group there would have to agree with
  the section's write count and order; the recovery refuses it and §6 measures what
  that costs (23 emits on 2 tunes). The primitive itself handles the section — a group
  writes once, where its last field fires — and the test drives exactly that.

On `MUSICIANS/A/AceMan/Lostro.sid` the store is `sid.filter.modevol = (m_1056 | $0F)`:
the text gives the constant `$0F` the low nibble and the read the high one, and the
staged cell `$1056` originates in the declared table based at `$1A13` — the filter
program, whose row 1 holds the mode byte `$10`. `$18` becomes two generators, a
`SELECT` over that lane masked `$F0` and a `SELECT(($0F,), ())` masked `$0F`, and the
register is explained for the first time on that tune.

## 4f. The relative route: a declared delta over a base the statement names

Three independent editors need a generator whose value is a delta combined with a base
rather than an absolute byte — DefMON's `AF` and `TR` (2421 freq emits, 33.6% of its
plane, on 6 tunes of 6, docs/dm-oracle.md §4.2), GoatTracker's vibrato and orderlist
transpose (12744 emits on 28 tunes) and SID-Wizard's detune and chord table (38840
emits on 64 modules, docs/gt-oracle.md §4.2). That an editor which has never heard of
the others needs the same object is the argument that it belongs in the primitive, and
the route of §2 is the general form: a generator supplies the delta, the route names
the operation and the base.

The recovery holds it to the standard §4b applies to a table, §4c to a step and §4e to
a mask: **both the operation and the base come from the program text, and the delta is
a declared byte at the cell the machine actually read.**

- **The store statement is the site** (`_rel_sites`). A SID store whose value expression
  resolves to `INT_ADD`, `INT_SUB` or `INT_XOR` of exactly two terms is a relative site;
  one term must reach a declaration (the delta), and the other must *name* a base. A
  store of anything else is not a site, and that is a count, not a claim.
- **Three named bases, and nothing else** (`_term_role`, `_is_mirror`).
  `Const(c)` — the base term is a program constant. `lane − c` is normalised to
  `lane + (−c)`, which is the same byte and one route rather than two.
  `Prev` — the base term reads, directly, a cell the program text stores this register
  class's own value into (`_mirrors`: a non-SID store whose value expression is one a
  SID store of that class also writes). That cell **is** the plane's previous emit, by
  the text, not by resemblance to the output.
  `Node(i)` — the base term reaches a second declaration, so the base is that lane's byte
  at the row its own read cell recovers, and the graph carries it as a real generator.
- **The emit is predicted, never solved for** (`_relate`). Per write, the delta
  candidates are the source cells inside the delta declaration at a non-`mut` offset,
  and the byte is the **declared** one; the base is the named base's value. The write is
  claimed only where `op(base, delta)` *equals the byte the register took*. Nothing is
  ever computed as `emitted − previous`: that is the fitted-parameter failure §4c
  refuses for a `RAMP` step, and §6 prices it here the same way.
- **A delta of zero predicts nothing** and is refused, for the reason `DIV(1)` and a
  `RAMP` of step zero are (§4c, §4d): the plane simply held its value, and a byte that
  happens to be `0` explains no index.
- **`lane − prev` is refused**, not re-associated. `SUB` is `base − delta` by the text;
  a declared lane as the *minuend* over the plane's own value is not `base op delta` and
  the site is declined rather than bent into one.
- **Per emit, not per run.** Every relative emit is determined by declared data and a
  base the graph itself carries, so there is no run to keep or refuse whole and **no
  observed seed at all** — unlike §4c, a relative stream contributes nothing to the
  shallow `seed` class. A `Prev` emit whose plane has no previous value is simply not
  claimed.
- **The order-preserved section takes none.** ctrl/AD/SR is a sequence of whole-byte
  writes (§5), not a value composed on one plane, so a relative emit there would have to
  agree with the section's write count and order; the recovery refuses it and §6
  measures the 2198 emits that costs.
- **Last of the value rules.** §4f only sees writes §4b, §4c and §4e have all declined,
  so no other plane can move; §6 confirms that per tune.

On `MUSICIANS/S/SounDemoN/Arkanoid.sid` the store is `freq_lo = m_15FC[t] + $FA`: the
declared lane supplies the delta and the store's own constant is the base, and 35 freq
emits that were an *observed* word matched to an ET table become a *declared* byte over
a program constant — the same count, strictly better evidence, which is why that tune
does not appear in the improved list. On `MUSICIANS/B/Bolleman/Geisha_End_Screen.sid`
the store adds the `$190B` lane to the `$17B0` lane and the base is a second generator.
tests/test_tracker.py drives all three bases hermetically, plus every refusal: an
unnamed base, a `mut` delta, a zero delta, the order-preserved section, and an identical
emitted stream whose delta is staged in an undeclared RAM cell.

## 4g. The arrangement: a declared pattern at a row the program text walks

Rung (f) (docs/frameprog.md §4.4) proves that a base-less deref `mem[P + i]` is **row
`i` of block `T[k]`** of a declared pointer table — the two-level shape an orderlist and
its patterns have. What it proves is the *address space*, not the address: `k` and `i`
are live state. This section recovers the second of them, and §6 measures how far that
gets on a 6502 driver.

- **The site is a resolved deref proof** (`_arr_sites`). `frameptr` supplies, per pointer,
  the declared `lo`/`hi` reload table, its extent, and the block set read out of `mem0` at
  that extent. Nothing is searched: the pattern table is the one the rung proved.
- **The row must be a cell the program text walks** (`_walked`, `_walk_of`). A cell every
  play-code writer of which is `cell = cell ± c` or `cell = c` — an `AND`-immediate wrap
  included, since that is how a 6502 counter wraps — has a value the post-init byte and
  the *order the machine ran those writers* determine. That is a walk, not an observation:
  the step and the modulus are program text and the seed is the declared image. One writer
  the text does not determine disqualifies the cell, and the site with it.
- **A walk that stands still predicts nothing** and is refused, for the reason `DIV(1)`
  and a `RAMP` of step zero are (§4c, §4d): a modulus of one, or a step of zero, holds the
  row and explains no index.
- **The block comes off the machine's own address bus** (`_arr_states`). The pointer's
  reload store is watched on the same run everything else uses (`frameval.eval_watch`),
  and its own read cell inside the declared `lo` lane names the entry `k` — the provenance
  discipline docs/dm-oracle.md §2 applies to a live replay, applied here to the evaluator.
  The block is then the **declared** word at that entry.
- **The store statement names the pattern** (`_arr_classes`, `_arr_reads`). The deref
  address is impure, so `frameval._addrs` reports the pointer's own cells and never the
  target: a byte read through a proven pointer arrives at this layer with **no source cell
  at all**. What names it is therefore the statement tree, exactly as §4b names a table —
  a SID store whose value expression reaches a `mem` at a resolved deref address.
- **The emit is predicted, never solved for** (`_arr_claim`). The address is
  `block + row`, the byte is the **declared** byte there, and the write is claimed only
  where that byte *is* the byte the register took — the `mem0[src] == val` pair every
  other lane emit passes. Two states of one frame that both predict the byte refuse it
  (`arrange_ambiguous`): the watch log and the store log are separate lists, so within a
  frame the walk's states are ordered but the writes are not placed among them.
- **A block outside every declaration is not a pattern.** The block must lie in a
  `datadecl` region, at offsets the declaration does not name `mut`; a pointer into
  undeclared memory holds no const data whatever byte it agrees with (#61).
- **The nodes** (`_arr_pairs`). One block is one fed `SELECT` — the declared bytes of that
  block — shared by every song step that revisits it, and its row is an `Index`-routed
  `RAMP(row0, step, wrap)` whose step and modulus are the text's. **The pattern's loop is
  that modulus**: `_emit` already wraps, so no back-edge is built (§2). A run is maximal in
  the walk, a run of one row is refused for the reason a one-emit `RAMP` is, and a row
  stream the walk does not reproduce refuses its block whole, as a sweep run does.
- **Nothing is segmented.** A pattern boundary read off the observed row stream, or an
  orderlist read off which blocks the run happened to visit, is a fitted parameter and is
  refused outright; §6 prices what it would have taken. The order-preserved section takes
  no pattern generator either, for the reason it takes no masked group and no relative
  route (§4e, §4f), and §6 prices that too.

The layer is exercised hermetically in tests/test_tracker.py: a pointer reloaded from a
declared table and deref'd at a walked row, whose ten emits are all declared bytes at a
generated row; the wrap being the pattern's own loop; two blocks alternating, each its own
node, the first refired on the revisit; and every refusal — a row the text does not walk,
a walk that stands still, a block no declaration holds, a `mut` row, a one-row run, and the
order-preserved section. Mutation evidence: a pattern boundary moved by one row and two
song steps naming each other's block each fail the law.

## 4j. The song: terminator-bounded regions at cursors the program text steps

§4g asks what a *pattern byte* explains about a SID register and answers 0 (§6). This
section asks the prior question — **what the song is** — and answers it whole. Nothing
here is observed: the pointer table is rung (f)'s, the extent is a compare immediate's,
the row boundaries are the cursor's own walked increments, and every byte is the
post-init image at an offset the program text reaches. `Graph.charts` carries it.

- **A site's row index is resolved in order** (`_guarded`, `_cursor_of`). §4g read a
  deref's index through a program-wide `{local: last definition}` map, so a driver that
  reads `*ptr[y]` at four sites with `y` reassigned between them resolved three of them
  to some *other* definition — cell 0 — and one such site disqualified the whole pointer.
  The locals live **at** each site are what the machine used, and reading them that way
  is what §0's first new row records: it is not a new rule, it is the same rule applied
  where it was misapplied.
- **The extent is a terminator, floored by the declaration** (`_extent`, `_terminators`).
  The region's own declaration is what the machine indexed, so it is a proven *floor*;
  above it the region runs to the first byte equal to an immediate the program text
  compares the region's own bytes against. Both halves are evidence — the floor is
  frameprog's rung, the immediate is program text — and neither is read off the row
  stream. An extent taken from where the replay happened to stop is refused, as §4c
  refuses a fitted step.
- **The terminator is the compare that resets the cursor** (`_term_reads`). A region
  ends where the text sets its cursor back, guarded by a comparison of the byte just
  read; that comparison names the byte. Where the reset is unreachable in the walked text
  — an orderlist whose restart the tune never plays — the region's *first* read is what
  the text compares instead, and its immediates are the candidates.
- **The cursor steps once per walked increment** (`_row_walk`). A row is not a fixed
  number of bytes: each read is placed at the cursor's live value, and between reads the
  cursor advances by exactly the increment statements whose guards the bytes already read
  satisfy. So a row that carries a parameter byte is three cursor steps long and a tied
  row is one, and **which** it is comes from the row byte's own bits, not from where the
  next row was observed to start.
- **The fields are the masks the text tests** (`_row_fields`). An arm that steps the
  cursor no further is a **tie**; one that steps it further and reads again takes a
  **parameter**; the mask a stepped-down counter is reloaded through is the row's
  **duration**; a mask whose set arm reaches a ctrl/AD/SR store is a **release** and one
  whose clear arm does is a **sustain**. Every one of those is an `AND`-immediate
  `_unmask` peels off the walked text.
- **A byte is named by the cell it flows into** (`_read_names`, `_cursor_roles`). A
  parameter byte the text copies into the cursor of the pitch table is a **note**; into
  the cursor of an instrument bank, an **instrument**. The destinations carry their own
  guards, which is how one parameter byte reaches two cells under one bit.
- **Two levels link through the reload index** (`_chart_source`). Where one region's byte
  is the index another pointer reloads its own table at, that region's entries *name* the
  other's blocks: an orderlist over patterns, from the text alone.
- **Refusals, each counted.** A pointer whose reads do not agree on one cursor
  (`song_no_cursor`), a region no compare bounds (`song_no_terminator`), a block whose
  terminator lies beyond the deref's proven row bound (`song_block_unbounded`).

On Commando this is the tune: pointer `$005D` over three terminator-bounded orderlists at
cursor `$54EC`, pointer `$005F` over 32 patterns at cursor `$54EF`, terminator `$FF`, row
fields `$1F` duration / `$20` sustain / `$40` tie / `$80` parameter — and §6 reports the
diff against Rob Hubbard's own source.

## 4k. The song as generators: a pattern byte is an index, not a register byte

§4j recovers the song whole and §4g reports that a pattern byte explains **0** SID
writes. Both are true and the reason is one sentence: §4g asks whether the declared byte
*is* the byte a register took, and a note or instrument column never is — it is the row
some other table is read at. The primitive already carries the edge for that (`Index`,
§2); this section is where the recovery finally builds one.

- **A byte is named by the cell it flows into, and that names the table it indexes**
  (`_row_roles`). §4j already calls a parameter byte copied into the pitch table's cursor
  a **note** and one copied into an instrument bank's cursor an **instrument**. Here that
  name becomes the table the byte is the row of, so the row a note-on selects is
  *generated* rather than observed — §7.4's stated goal, reached.
- **A voice's rows are the patterns its own orderlist entries name** (`_song_lanes`).
  §4j's `_chart_source` links one region's entries to another's blocks, so a voice's row
  stream is those blocks concatenated: declared data at program-text offsets, in the order
  the program text's own cursor reaches it. Nothing is segmented and nothing is read off
  the row stream the machine produced.
- **The loop needs no machinery.** `SELECT` advances modulo its table, so a song whose
  orderlist restarts wraps by construction (§2). The table is one pass of the orderlist.
- **Two readings of one datum, both structural** (`_song_lanes`). A row that names no
  parameter either takes no emit at all or holds the one before it, which is what the
  player's own cell does — a tied row writes ctrl/AD/SR with the instrument it inherits
  and writes no pitch at all. Which reading a lane takes is settled by whether it
  *reproduces that lane's own run*, exactly as §4i settles a cursor's rows, and never by
  fitting bytes.
- **Regenerate or refuse whole** (`_song_at`, `_songed`). A stream whose recovered run the
  song does not reproduce keeps that run and is counted as `lane`; one it does reproduce
  becomes `SELECT(lane, Node(song))` and is counted as **`arr`**, so the arrangement's
  contribution can never hide inside the strong figure.
- **One lane is one node** (`_singers`), however many registers read it: a voice's
  attack/decay and its sustain/release are two reads of one instrument index, as the
  player's own cell is one cell.

**A row read must be able to be another generator's emit in the same frame** (`_Held`).
`_run` was node-major: an index node firing three times in a frame overwrote its own value
twice and every read of that frame saw the last row. A frame is now settled in dependency
order — index nodes are earlier than the reads they feed (`_index_ok`) — and an emit is
paired with the **edge** that produced it, the last value standing where a node did not
fire that edge, which is what a cell the machine did not rewrite holds. Commando's driver
cuts up to three rows inside one frame, one per voice.

**A divider's divisor may be an emit** (`_period`, `_counted`). `DIV(n, phase)` takes
`n = Node(j)`: the trigger domain's counterpart of the value domain's generated row, and
the one shape a constant cannot name — a tracker row's own duration field. The divisor is
reloaded at every tick, so the divider is a countdown rather than a modulus and a row that
lasts one tick and one that lasts sixteen are the same generator with a different reload.
The reload is the value the tick's own reader emits, which the frame settles *after* the
triggers, so a tick whose reload is not in yet takes it at the next input trigger: the
player's own order, the counter running out first and the row it then reads supplying the
next one. `_divisor_ok` refuses a divisor no `index` node supplies, and an `index` node a
divider reads is no longer dead.

On Commando this is what it buys, measured over the whole tune: the attack/decay and
sustain/release lanes of all three voices — **6498 emits** — stop being read at an observed
row and are read at the row the orderlist and the patterns generate, `arr` 0 -> 6498. §6
reports what it does not buy, and why, as a number.

## 4h. The node identity: a declared region at a cursor the program text names

docs/node-partition.md measured that the frame program's **(declared region, cursor cell)**
pairs are the editor's object set — 1415 of 1654 composer objects named by a pair, 25.6
pairs per tune against 26.7 editor groups — and recommended building nodes from them.
This section is that change, and §6 is its measurement.

- **A region is a load base the text indexes** (`_pairs`, `_objects`). Every procedure's
  statements are walked in order and each load written `base[index]` contributes its base;
  the const bases the index reads, resolved through the locals defined above it, are its
  **cursors**. That is `tools/node_partition.py`'s own pairing rule, applied here rather
  than reinvented.
- **The text's bases resolve the block a declaration only contains.** `datadecl` tiles a
  whole data block, and §2.1's `SHIFT` control showed containment resolves the block and
  not the table. A region therefore runs from its base to the **next named base**, or to
  the declaration's end, and stops at the first offset the declaration names `mut`. A
  strided declaration is a record array, so a base inside one names a **lane** of it and
  the region carries the stride: the lanes overlap and containment alone would name the
  wrong one.
- **A base the text names no cursor for is refused**, per base, with a diagnostic
  (`pair_no_cursor`), as is a base no declaration covers (`pair_base_undeclared`) and one
  whose own offset is play-written (`pair_region_mut`). A refused base builds no node; its
  reads fall back to the whole declaration exactly as before, which is a coarser node and
  never a fabricated row.
- **Nothing about the emit changes.** The row is still `(cell − base) // stride` off the
  cell `frameval.eval_src` says the machine read, and the byte is still the declared one
  checked equal to the byte the register took (§5). Only the *base* the row is counted
  from moves, from the declaration to the object, so the node's table is the composer's
  table. The value partition is byte-identical corpus-wide, which is the check that this
  is a renaming of nodes and not a new claim.
- **The cursor's own value is watched, and does not key the node** (`_cur_watch`,
  `_cur_states`, `_pair_verify`). The cursor cells ride the one `frameval.eval_watch` run
  the recovery already makes. `eval_watch` reports a watched store's **address and origin
  cells, never its byte**, so a cursor's value is reconstructed the way §4g reconstructs a
  pattern block: the text's own step/set rule where it names one, else the declared byte at
  the cell the machine copied from, else nothing at all. A read is *verified* where its
  named cursor was holding the index the machine read at. **Keying the node on that was
  built and measured, and it costs matches** (§6), so it is priced and refused: the census
  ships, the key does not.
- **A row searched over the region for the byte is refused outright**, for the reason §4c
  refuses a fitted step and §4g a segmented pattern row. §6 prices that search too.
- **The law sees the region, not the cursor, and that is stated rather than implied.** The
  row is the machine's own read index off a base the text names, so reading a region at
  another region's base fails the law (tests/test_tracker.py) while a wrong cursor *label*
  changes only what the node is called. Making the cursor load-bearing is exactly what
  keying the row on its value would have done, and §6 is why that is refused.

## 4i. The sequencer: a tick clock, a row cursor, and the table that cursor rows

Until this step the layer **recovered a sequencer and then discarded it**. `_tempo` found
`frames_per_tick`, `_clocks` the dividers, `_divisors` their reloads and `_walked` the
cursors — and every one of them went into the `Tracker` namedtuple, which is a *report*.
`_graph` received none of it and built the `Graph` from per-register write streams plus
two floors. A tracker song is a chain — `tick clock -> row cursor -> pattern row ->
note-on -> instrument program -> register` — and only the last link was built, which is
why 99.89% of fires were the observed `EDGE` floor.

`_sequencer` is that chain, recovered from program text and threaded into `_graph`. Every
object it uses already existed in the primitive; nothing is invented but the `DIV` phase
field, which §2 and §8 had already settled as belonging here.

- **Link 1, the tick** (`_reloads`, `_sequencer`, `_clock_node`). A tick is
  `DIV(n, phase)`: `n` is the divisor §4d's rule declares — what the play code reloads
  into a cell it steps down, an immediate or a declared byte at a non-`mut` offset — and
  the **phase is the declared counter**, `(mem0[cell] − 1) mod n`. The counter's post-init
  byte is what init staged; a counter left at its own reload, and one init never staged at
  all, both give `n − 1`, which is the reading before the field existed. The divisor and
  the phase are now paired **per divider**, so a phase can only come from the counter its
  own reload divides. A phase fitted to the observed fires is refused for the reason a
  fitted period is (§4d), and §6 prices it.
- **Link 2, the row cursor** (`_beats`, `_rows_at`). A cursor is a cell `_walked` proves
  the play code only steps or sets by its own text, with one step and one modulus
  (`_arr_rule`); its seed is the post-init byte, so `RAMP(seed, step, wrap)` routed `Index`
  generates its whole walk. **It is beaten by its own step statement, not by the read**:
  the cursor stores ride the one `frameval.eval_watch` run the recovery already makes, and
  the frames the text's step rule executed are the cursor's trigger stream. A cell some
  writer *reloads* is refused outright — a `RAMP` walks and never resets.
- **Link 3, the table that cursor rows** (`_chain`). A region is §4h's: a declared base
  the program text indexes, at the cursors the text names for it. Where one of those
  cursors is walked, the region's rows are **predicted** from the cursor's own beats and
  compared with the run the machine read; a stream the walk does not reproduce keeps its
  recovered run, exactly as a sweep run whose step no declaration names is refused whole
  (§4c). The `SELECT`'s `rows` then becomes `Node(cursor)` — a generated row, not observed
  data.
- **Link 4, the note-on.** Because the cursor's trigger is its own beat stream,
  `_clock_node` sees that stream like any other and lifts it to a `DIV` where a declared
  tick generates it. That is the whole chain wired: `DIV -> RAMP[Index] -> SELECT`, with
  no separate mechanism for the note-on and no back-edge for the loop — `_emit` already
  wraps (§2).
- **Refuse per link, per tune, and keep today's floor.** Each link names its own refusal
  (`chain_cursor_not_walked`, `chain_cursor_reset`, `chain_rows_unwalked`) and a tune
  missing any link keeps exactly the behaviour it had. Nothing is approximated: a cursor
  whose trigger were fitted to the read stream instead of taken from its own beats is
  refused, and §6 prices that too.

`MUSICIANS/B/Bonifacio_Robert/Delta_Man.sid` is the tick: the divider `$0079` reloads the
immediate 48 and the post-init image leaves 32 in it, so the tick is `DIV(48, 31)` — 47
ticks over the tune, where the divider's own phase 47 generates none of them.
`MUSICIANS/B/Blanchette_Francois/Bird_on_the_Run_II.sid` is the row: cursor `$C6FF`, seed
0, step +2, wrap 256, and the voice's pitch-hi lane is read at the row that `RAMP` holds.
Both render in `out/*.trackertext.txt`.

## 5. Instrument lanes: ctrl/AD/SR from a declared bank at a recovered row

ctrl and ADSR are written from an instrument bank: a declared const table of
stride `s`, one lane per byte offset. The generator for a lane is
`SELECT(lane, rows)` — the **declared bytes** of that lane, indexed by a
**recovered row** — fired by the voice's note-on `EDGE`.

- **Provenance, not proximity** (`frameval.eval_src`). The evaluator records, for
  every SID store, the cells the byte came from: every byte load at a *pure*
  address (consts, locals and ops — no memory read, so re-evaluating it consumes
  no volatile input and has no side effect) inside the value expression, each
  preceded by the cell that byte originated in, so a byte staged in a RAM
  register mirror arrives here as the bank cell it was copied from
  (docs/frameprog.md §1.4). A bare load reports one cell, `lane & mask` reports
  both and the declaration picks. That is the address the play code indexed, so
  the row is `(cell - base) // stride` and the lane is `(cell - base) % stride` —
  read off the machine, never guessed.
- **The declared byte must agree** (`_lane_key`). A write is a lane read only if
  the cell lies inside a declared table, at an offset the declaration does not
  name `mut`, *and* `mem0[cell] == value`. A cell the play phase mutated therefore
  never passes as constant data (#61, #78), and every byte a `SELECT` emits is
  verified equal to the declared byte at the moment of that emit. The check earns
  its keep: over the 60-tune sample 916 ADSR emits read a cell inside a declaration
  whose byte no longer matches the snapshot, and all 916 stay residual.
- **The tree first, the search behind it** (`_classify`). The declarations the
  store site names for this register class (§4b) are tried first; the search over
  every declared bank is the fallback for a value the tree cannot express — a byte
  staged across a procedure boundary or through the stack, which `frameval`'s
  program-wide locals carry but a per-procedure walk of the tree does not. Dropping
  the fallback would cost 15648 emits on these planes (measured before the origin
  rule of §6), so it stays and is named.
- **The gate rides the recovered waveform** (`_classify`, `_key_table`). A ctrl
  write carrying no declared cell of its own is the gate bit applied to the lane
  byte at the row the voice's last lane read established: the emitted byte is
  `lane[row]`, `lane[row] & ~gate` or `lane[row] | gate`. The ctrl `SELECT` table
  is therefore the declared lane followed by those three held readings of it, so a
  row past the lane length says both which byte and that the row came from the hold
  rather than from this emit's own provenance — which is exactly the `lane`/`gate`
  line in `classes`. Every byte emitted is still a declared byte, and a voice that
  never read its waveform from a declaration has no row to ride and stays residual.
- **The gate is one owner read three ways, not two owners of one register** — which
  is why §4e's masked route does not replace it, and the distinction is worth
  stating because the two look alike. `_SECT` is a 1-bit field enumerated over its
  three states, and a nibble cannot be enumerated that way (256 rows of nothing),
  which is what §4e generalizes. But the ctrl plane is not the shape §4e expresses:
  each ctrl **write** carries all eight bits from one source — a declared byte, or
  that byte with the gate bit forced — and the next write may be residual, whereas a
  masked group partitions *one byte* between generators that all fire on it. Splitting
  ctrl into a waveform field and a gate field would need both to fire on every write
  the other explains, so one residual write would take the register's whole span with
  it; the per-write split of this section is strictly better there. The two mechanisms
  therefore stay apart, and §6 measures the 23 emits that costs.
- **Immediates** (`_immediates`, `_const_flow`). The other half of a typical note
  lane is the release write, an immediate operand in the play code (`ad = 0`), and
  the other half of a typical ctrl lane is the hard-restart byte a branch loads
  before the store. Those emits are `SELECT((c,), ())` for a constant `c` the program
  text stores to that register class (`reg % 7`, since one voice-generic store site
  serves all three voices behind a dynamic offset), reached directly or through a
  local. The value comes from the program, not from the observation; `Coverage.classes`
  keeps `lane`/`gate` (declared bytes at a recovered index) apart from `imm`.
- **Split per write, not forfeited per register** (`_instr_streams`, `_buckets`).
  What is removed from `RAW` is a *write*, so every write `_classify` explains is a
  candidate emit and the rest stay residual: one unexplained write no longer costs
  the register. The old all-or-nothing rule and the subset search it needed are
  gone, replaced by a **bucket order** that makes the split explicit.
- **Order is constructed and checked, not hoped for** (`_precede`, `_buckets`,
  `_refine_voice`). ctrl/AD/SR is the order-preserved section, and the section
  renders as its buckets — one per stream key, plus the residual — concatenated in
  node order. So the rendering equals the observation exactly when every frame's
  bucket sequence is non-decreasing in one global order, and two adjacent writes in
  a frame fix a precedence between their buckets. `_precede` takes the transitive
  closure of those precedences; a key on a cycle can sit neither wholly before nor
  wholly after the residual, and is **demoted into the residual**, lightest first,
  until the digraph is acyclic. The remainder is ordered by ancestor count — a
  linear extension of the closure — and cut at the residual into `pre` and `post`.
  `_refine_voice` then rebuilds the whole section, residual writes in place, and
  compares it with the observed one *values included*; a mismatch refuses the voice.
  So the law never depends on a lucky interleaving, and a split that would reorder a
  register's writes is refused rather than emitted.

The row stream is per voice, not per lane: on Commando the AD and SR `SELECT`
nodes of a voice carry the *same* rows and share one `EDGE`, which is the
instrument selector showing through. What is **not** yet explained is the note-on
timing: the `EDGE` counts are observed. Values are declared data at a recovered
index; triggers are still the floor for 99.90% of fires, and §4d's `DIV` is the only
thing that lifts any of them off it. §6 counts that domain separately and never folds
it into the interpreted-emit share.

## 6. Coverage (measured, 200 frames unless stated)

**What coverage does not measure.** It measures *justification*, never *reach*.
`tools/graph_diff.py` matches our graph against the editor's own node by node — both
sides are a `Graph`, so they are directly comparable — keying each node on the writes,
edges or rows it produces, and it checks its own attribution by rebuilding each side's
`eval_graph` projection from the per-node signatures. Over nine GoatTracker tunes,
600 frames each: **99680 of the composer's writes are shaped differently by us, and
zero are never produced.** Not one byte of any song is missing from our output. The
node partitions barely agree — 1 to 11 nodes match out of 42 to 575 — and the
disagreement runs both ways: Autumnness recovers 40 nodes against the song's 575,
Big_Time_Sensuality 432 against 49. So a residual emit is one we emit but cannot
attribute to a declaration, and the gap this document measures everywhere below is a
gap in *partitioning and evidence*, not in what the recovery can reproduce.

`Coverage(interp, residual, total, planes, classes, triggers)` carries **two
partitions, one per domain**, and they are never summed.

- The **value** partition: emits produced by an interpreted generator vs emits
  replayed from `RAW`, the per-plane split, and per plane the evidence behind each
  interpreted emit — `lane` and `gate` are declared bytes at a recovered index
  (**strong**), `imm` is a program constant that passes the law without explaining an
  index (**shallow**, never folded into a strong figure), `mask` is a byte several
  generators assemble field by field (§4e), part declared and part program constant, and
  `rel` is a declared delta over a base the store statement names (§4f), part declared
  byte and part live value: neither of the last two is folded into a strong figure.
  `ramp` is an emit the accumulator's declared step generates (§4c) and `seed` the one
  observed byte a run starts from — a pair emit starts from two, one per register — and
  `seed` is shallow for that reason. `arr` is a declared byte read at a row the
  **arrangement generates** (§4k): the strongest evidence in the value domain, because
  neither the byte nor the index it is read at is observed, and it is kept apart from
  `lane` so it can never be confused with a row the observation yielded.
- The **trigger** partition (`triggers`): `(generated, all)` fires, counted by
  `_run` off the evaluator itself. A generated fire is a `DIV` tick over a divisor the
  play code declares (§4d), or over one the arrangement's own duration field emits
  (§4k) — the only strong evidence this domain has, and the only evidence it admits at
  all. Every other fire is the `EDGE` floor.

### Where the tracker stands

Whole cached corpus, PSID start subtune, 200 frames: **682 tunes cached, 646
decompile**, and of those 646 the **tracker law passes 646/646** and a pitch table
is recovered for **582**. The 36 that do not decompile never reach this layer (10
`play $0000` with no interrupt vector installed, 5 init runaways, 3 unmodelled
`brk`, 3 pinned-trace faults, and the remainder assorted). Values are **38.66%**
explained and triggers **0.098%**; the two are stated apart because they are two
domains, and the second is smaller by a factor of 390. This is the current state,
not a delta; the tables after it record how it was reached. (Current at **649**
tunes reaching the gate and after §4c's turning pair sweep, §4k's song, §4l's object and
§4m's reload, the two figures are **40.92%** — 794952 of 1942809 — and **0.339%**, 1140 of
336667. The value figure rose by 22193 emits for the first two, all `ramp` and `arr`, by a
further **514** for §4l, all of them `pw` (110842 -> 111356) with `seed` falling 7542 ->
7479, and by **18274** for §4m and the spilled row together, over **138 tunes** with
**none generating less**, every tune's `total` unmoved and `seed` unmoved at 7479:
`lane` 517258 -> 531325 and `ramp` 44533 -> 49746, `pw` 111356 -> 124436, `freq` 417498 ->
420957 and `filter` 24455 -> 26190. The trigger *share* falls throughout because
generating a value costs a fire — a byte a `RAW` node replayed needed no trigger and a
byte a generator produces does — which is why `universality.json` floors the generated
*count* and not the share, and §8 records the domain as the next thing to pay off.)

| plane | interpreted | of | share | strong | shallow |
|---|---|---|---|---|---|
| freq | 414066 | 602528 | 68.7% | 414066 | 0 |
| pw | 89376 | 561585 | 15.9% | 85871 | 3505 |
| ctrl | 111659 | 300573 | 37.1% | 101884 | 9775 |
| filter | 22301 | 240694 | 9.3% | 21654 | 647 |
| sr | 55945 | 115963 | 48.2% | 47905 | 8040 |
| ad | 54362 | 112534 | 48.3% | 47010 | 7352 |
| **all** | **747709** | **1933877** | **38.66%** | **718390** | **29319** |

The other domain, on the same run and reported apart from that table:

| domain | generated | of | share |
|---|---|---|---|
| values (emits) | 747709 | 1933877 | 38.66% |
| **triggers (fires)** | **300** | **305119** | **0.098%** |

**96.1% of interpreted emits rest on strong evidence** — a declared byte at a
recovered row, or generated from one. The shallow 29319 are program immediates
(`ctrl`/`ad`/`sr` releases and hard-restarts) plus the observed seed each sweep run
starts from; they pass the law without explaining an index and are never folded into
a strong figure. The freq plane splits 147002 pitch-table note-lane emits (the note
lane, §4) and 267064 declared-lane `SELECT` emits (§4b); `ctrl`'s 111659 splits 69178
lane reads and 32706 gate images of a lane byte (§5); `pw`'s 89376 splits 63093
declared-lane reads and **26283 swept by a `RAMP`** (22778 generated, 3505 observed
seeds, §4c); `filter`'s 22301 splits 19033 declared-lane reads and 3268 swept (2621
and 647). Whole planes explained, per tune: `ad` 285, `sr` 268, `freq` 122, `ctrl`
57, `pw` 20, `filter` 4.

The residual is still dominated by the two accumulator planes: `pw` (472209 emits)
and `filter` (218393), with `ctrl` (188914) behind them. `pw` and `filter` are one
problem measured twice, and §4c's sweep now takes the part of it whose step a
declaration holds; what is left of that shape is counted per refusal below.
`ctrl`, `ad` and `sr` are no longer bounded by the
partition (§5 splits a register rather than forfeiting it) but by what the
declarations name at all: they now realize 69178/72778, 47010/52300 and
47905/51702 of their declared-byte ceilings. What is left there needs a row for
the gate to ride, and a voice that never reads its waveform from a declaration has
none (§8).

| tune | pitch table | interpreted | freq plane | pw | ctrl | ad | sr |
|---|---|---|---|---|---|---|---|
| Commando (Hubbard), 300 frames | `$5428` interleaved, 97 words | **2385/2525 = 94.5%** | 1198/1280 = 93.6% | 305/363 | 576/576 | 153/153 | 153/153 |
| Ghouls_n_Ghosts (Follin) | `$6D35`/`$6D96` split, 97 | 485/1164 = 41.7% | 485/722 = 67.2% | 0/0 | 0/29 | 0/7 | 0/4 |
| Automatas (Goto80/DefMON) | `$1578`/`$1614` split, 120 | 1594/4800 = 33.2% | 796/1200 = 66.3% | 0/1200 | 266/600 | 266/600 | 266/600 |
| Athena (Galway) | `$C517`/`$C55F` split, 72 | 426/1882 = 22.6% | 426/1200 = 35.5% | 0/600 | 0/48 | 0/17 | 0/17 |
| Krakout (Daglish) | `$E629` big-endian, 12, octave-shift | 228/5000 = 4.6% | 228/1200 = 19.0% | 0/1200 | 0/600 | 0/600 | 0/600 |

Of Commando's 576 ctrl emits, **75 are declared-lane reads** and **399 are that
lane with the gate bit cleared** — 474 strong, all from the `$5591` bank's
waveform lane at `+2`, the same row its AD (`+3`) and SR (`+4`) lanes read — and
102 are the `$80` hard-restart immediate. Its 306 ADSR emits are 154 lane reads
and 152 `ad = sr = 0` release immediates, its 305 pw emits 77 reads of the `+1`
lane of the same bank plus **209 generated by sweep `RAMP`s over the `+6` lane's
step, from 19 observed seeds** — one per re-staging (§4c) — and **all 1198 of its freq
emits are the declared
`$5428` pitch lanes at a recovered row** rather than observed words matched to an
ET table.
Automatas' 798 refined instrument emits are *all* immediates (`ctrl = ad = sr = 0`,
one voice held silent every frame) while its 597 new freq emits are all declared
lanes, which is why the split is reported and never folded into one number.

Over the first 60 cached HVSC tunes (57 decompile; keyed by full relpath) nothing
moves: a pitch table is recovered for **53**, the law passes for **57/57**, and
the interpreted share stays **23.1%** (39598/171586) with ctrl at 0/30025 — no
voice in that prefix reads its waveform from a declaration.

Over all 682 cached tunes (646 decompile), against the same tree before this
step: the law passes for **646/646** either way, a pitch table is recovered for
**582** either way, and the interpreted share goes 21.0% (406674/1936217) to
**21.5%** (416294/1936217). Per plane: freq 63.1% (unchanged), ctrl **0 → 7993**
of 301790 (2.6%), ad 10758 → 11719 of 112169, sr 14404 → 15070 of 116108. The
ctrl gain is **4615 strong** (2907 lane, 1708 gate) and 3378 immediates; 49 tunes
refine ctrl at all, 25 of them on strong evidence and 24 on immediates alone
(2776 emits, which is why the split is reported rather than a single figure).

Over all 682 cached tunes again, each at its PSID start subtune (623 reach the
gate; the denominators differ from the paragraph above, which starts every tune
at subtune 0), against the same tree before frameprog reported a store's
**origin** cell (docs/frameprog.md §1.4) — a change to frameprog only, with
`tracker.py` untouched:

| plane | before | after |
|---|---|---|
| interpreted | 408262/1895893 = 21.53% | **419595/1895893 = 22.13%** |
| freq | 373048/587722 | 373048/587722 (unchanged) |
| ctrl | 7796/295884 = 2.63% | **10558/295884 = 3.57%** |
| ad | 12338/109997 = 11.22% | **16887/109997 = 15.35%** |
| sr | 15080/113411 = 13.30% | **19102/113411 = 16.84%** |

Strong evidence carries the gain: declared-lane `SELECT` emits go 23303 → 34518
while program immediates barely move (11911 → 12029), and the ctrl `SELECT`
split goes 2723 → 4351 lane rows and 1683 → 2803 gate rows. 86 tunes improve and
**none regress**; 68 more tunes have their whole `ad` plane explained, 11 more
their whole `sr` plane and 3 more their whole `ctrl` plane. Gate FP holds
623/623 and the tracker law 623/623, both unchanged.

frameprog's indexed-access rendering (docs/frameprog.md §4.2, which cuts raw
`mem[expr]` by 31% corpus-wide) moves **nothing** here, and that is the record:
every plane is byte-identical before and after. Coverage at this layer is a
function of the declarations and of `frameval.eval_src`'s store provenance, not
of how an address is written; the remaining `ctrl`/`ad`/`sr` residual is 40%
writes whose value reaches the store through a local (no source cell at all) and
30% whose source cell falls outside every declaration. Naming an address better
answers neither.

### Reading the statement tree, and consuming `mut`

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile). Two
independent effects, reported apart because they pull opposite ways: transliterating
the last-write-wins planes off the store statement (§4b) **gains**, and refusing
source cells at a `mut` offset (§4) **costs**. The middle two columns are each
effect on its own; the last is the shipped combination.

| plane | before | tree only | `mut` only | shipped |
|---|---|---|---|---|
| interpreted | 425657/1933877 = 22.01% | 467904 = 24.20% | 422823 = 21.86% | **459144 = 23.74%** |
| freq | 379104/602528 | 395868 | 379104 | **395264** |
| pw | 0/561585 | 25483 | 0 | **20161** |
| ctrl | 10558/300573 | 10558 | 9604 | **9604** |
| ad | 16890/112534 | 16890 | 16522 | **16522** |
| sr | 19105/115963 | 19105 | 17593 | **17593** |

The tree gain is **+42247** emits, every one of them `lane` class — declared bytes
at a recovered row, no immediates — over 330 tunes that grow a declared lww lane.
The `mut` refusal costs **−2834** on the instrument planes (exactly the residue
#78 measured from the declaration side: `sr` −1512, `ctrl` −954, `ad` −368) and a
further **−5926** on the planes the tree gain opened (`pw` −5322, `freq` −604).
Net **+33487**, 167 tunes improve, 6 regress (all six purely from the refusal).
Whole planes explained: `pw` 0 → 6 tunes, `freq` 106 → 113; `ctrl` 28 → 25, `ad`
180 → 177, `sr` 195 → 192, the losses again the refusal's. The canonical fixpoint
holds 646/646, Gate FP 646/646 and the tracker law 646/646 — all three unchanged.

`eval_src` is **not** removed and cannot be: the tree names the declaration but
cannot evaluate the index, so the read cell is still what recovers the row. What it
replaces is *identification*. Of the corpus's 8538 SID store sites, 5457 (63.9%)
name a declared table in the tree — 3223 of the 4526 freq/pw sites (71.2%) — and on
those planes that naming is now the only admissible basis.

### The pulse sweep, and how little of it is declared

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile),
against the table above. The sweep `RAMP` (§4c) moves `pw` **20161 → 20452** of
561585 (3.59% → 3.64%) and the interpreted share 459144 → **459435** of 1933877
(23.74% → 23.76%). Every new emit is a sweep emit: `ramp` **+281**, `seed` +10, and
`pw`'s `lane` count does not move. **No other plane changes by a single emit**, no
tune loses an interpreted emit, 3 tunes improve and none regress. The canonical
fixpoint, Gate FP and the tracker law are all unchanged.

That gain is small on purpose, and the measurement says why. Of the 561585 pw
emits, **158718 (28%) sit in a constant-nonzero-delta run of two or more** — the
plane really is accumulator-driven. But the accumulator's *parameters* are almost
never declared data the tree can name:

| where the pw store's byte comes from | emits |
|---|---|
| a cell outside every declaration (a RAM accumulator) | 329792 |
| a computed value, no source cell at all | 181504 |
| a declared lane, byte agreeing (§4b) | 21083 |
| a declared cell at a `mut` offset (the accumulator's own lane) | 17728 |

183 tunes have a pw store reading a cell the play code steps, but only 3 have a
step the declarations hold: the rest step by a **RAM cell the play code copied from
a table at note-on**, and so do their bounds and their rate. Ahti_01's sweep is a
textbook 16-bit triangle — `m_6846[x] ± m_684C[x]`, turning around at `m_6852[x]`
and `m_684F[x]` — with all four parameters in per-voice RAM. An oracle that is
*allowed to fit*, taking any declared byte at the voice's held row that equals the
observed delta, still reaches only 7480 emits, because the held row itself is rare
(`ctrl` is 3.2% declared corpus-wide). So the ceiling for this step is ~1.3% of the
plane and the honest yield is 0.05%; the rest is not a `RAMP` problem.

What would move it is a frameprog change, not a tracker one: `eval_src` reports
origins for **SID** stores only (docs/frameprog.md §1.4), so a parameter staged in
RAM arrives here as an unnameable cell. Reporting the origin of a named non-SID
store site would make those steps, bounds and rates declared bytes at recovered
rows, and the accumulator would then be recoverable wherever the tree finds it.
That query shipped, and "The accumulator's parameters, queried out of RAM" below is
what it returned — 29551 emits, against the 7480 ceiling a fitting oracle reached here.

### The origin of a byte staged in a register

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile),
against the table above, and again a change to **frameprog only** with
`tracker.py` untouched: `frameval`'s cell → origin map now carries through the
**locals**, not only through the cells (docs/frameprog.md §1.4). A driver that
loads a bank byte into a register and stores it a statement later showed the old
rule no load at all inside the store's value expression, so the origin was
dropped exactly where the staging happened.

| plane | before | after |
|---|---|---|
| interpreted | 459435/1933877 = 23.76% | **557052/1933877 = 28.80%** |
| freq | 395264/602528 | **414066** |
| pw | 20452/561585 | **63232** |
| ctrl | 9604/300573 | **42789** |
| sr | 17593/115963 | **19373** |
| ad | 16522/112534 | **17592** |

**337 tunes improve and 1 regresses.** The gain is strong evidence, not
immediates: `lane` 152262 → 386688 and `gate` 2688 → 10608, while `imm` moves
11899 → 12615. Whole planes newly explained: `ad` +37 tunes, `ctrl` +27, `sr`
+21, `pw` +14, `freq` +9. The canonical fixpoint, Gate FP and the tracker law all
hold 646/646, unchanged — the rule annotates and cannot disturb a value, a write
or a record.

Two costs, both named rather than folded away. `MUSICIANS/F/Fanta/15_Years_Oxyron`
loses 42 freq emits: an added origin cell displaces the lane its lww classification
picked, the mis-bind docs/frameprog.md §4.2 measured from the other side. And the
sweep `RAMP` goes 281 emits → 138 with observed seeds 10 → 1, because two of the
three sweeps were never accumulators: with the register hop visible, Cool_Intro's
and Aha's pw writes report a declared source cell, so §4c stands aside and §4b
claims them as declared lanes — pw on those two tunes goes 80 → 239 and 72 → 348.
Only Commando's sweep is a genuinely computed accumulator, and §4c's test as it then
stood (the evaluator reports no source cell) is sharper for it, not weaker.

What the change does **not** do is raise the ceiling to the plane totals. Emits
whose source tuple names a declared byte at a non-`mut` offset — the ceiling the
interpreted figure is drawn from, before any all-or-nothing rule — go freq 110833
→ 289125, pw 21085 → 87828, ctrl 20915 → 72778, sr 40098 → 51702, ad 43718 →
52300, filter 15571 → 27785. `ad` and `sr` are the gap to read: ~46% of their
emits now name a declared byte while ~16% are interpreted, so what holds them back
is the tracker's own all-or-nothing-per-register rule (§5 as it then stood) and its
order check, not provenance — which "Realizing the ceiling" below then measured and
removed. `filter` at 11.5% is the opposite reading: even named perfectly, most
filter writes are not declared-table reads.

### The filter plane, and the 8% of it that is a declared-table read

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile), against
the table above. $15-$18 join §4b's last-write-wins planes under a register class of
their own; no other rule changes.

| plane | before | after |
|---|---|---|
| interpreted | 557052/1933877 = 28.80% | **576085/1933877 = 29.79%** |
| filter | 0/240694 = 0% | **19033/240694 = 7.91%** |

Every one of the 19033 is `lane` class — a declared byte at the row the read cell
recovers — and **no other plane changes by a single emit**: freq, pw, ctrl, ad and sr
are byte-identical per tune, as is every other tune's class split. 184 tunes improve,
**none regress**, and 3 have the whole filter plane explained. The canonical fixpoint,
Gate FP and the tracker law all hold 646/646, unchanged.

The gain is exactly the ceiling this rule can reach, and the ceiling is the story.
Per register, over the plane's own denominator — the record after last-write-wins,
which is what `RAW` counts and what the 240694 above is (the 27785 in the table before
this one counts every write instead, so it is not the same basis):

| register | emits | names a declared byte | the tree names the table | a program constant |
|---|---|---|---|---|
| $15 cutoff lo | 24151 | 247 (1.0%) | 40 (0.2%) | 2361 |
| $16 cutoff hi | 87533 | 7882 (9.0%) | 7033 (8.0%) | 7907 |
| $17 resonance/routing | 54771 | 15642 (28.6%) | **10642 (19.4%)** | 2498 |
| $18 mode/volume | 74239 | 2321 (3.1%) | 1318 (1.8%) | 21411 |
| **all** | **240694** | **26092 (10.8%)** | **19033 (7.9%)** | **34177** |

Two things are refused, and both are named rather than folded away. The 7059-emit gap
between the third column and the fourth is what a blind search over every declaration
would take; on the lww planes the store statement naming the table is the only
admissible basis (§4b), so those stay residual. The last column is the shallow figure
this step does **not** claim: 34177 filter emits write a byte the program text stores
as a constant, over half of them $18, and an `imm` read would pass the law for
every one — but it explains no index, and on a register that takes many program
constants it is the observation, not the program, choosing between them. The filter
plane carries strong evidence only.

Only **$17** is really a table plane: resonance and the routing mask are
per-instrument bytes a bank holds, and 28.6% of them name one. Cutoff is not, and the
split between its two bytes says why — the low byte that carries a sweep's precision
names a declared byte 1.0% of the time against the high byte's 9.0%.

How much of an index those 19033 rows carry is reported rather than assumed:
**11461 (60.2%) read one declared cell for the whole tune** — a filter setting in const
data, over 155 tunes — and **7572 read a lane at two or more rows**, over 43. Both are
declared bytes at a row `frameval.eval_src` recovered and neither is fitted, but a
fixed cell explains a value where a moving row also explains an index, so the two are
not one figure. Per register the single-row share is $15 100%, $17 69%, $18 80% and
$16 43%.

Where the rest of the byte comes from says what the plane actually is:

| register | computed | outside every declaration | at a `mut` offset | declared, byte moved | a declared lane |
|---|---|---|---|---|---|
| $15 cutoff lo | 2011 | 21651 | 173 | 69 | 247 |
| $16 cutoff hi | 15042 | 59268 | 1868 | 3473 | 7882 |
| $17 resonance/routing | 3787 | 28137 | 100 | 7105 | 15642 |
| $18 mode/volume | 22197 | 32824 | 300 | 16597 | 2321 |
| **all** | **43037 (17.9%)** | **141880 (58.9%)** | **2441 (1.0%)** | **27244 (11.3%)** | **26092 (10.8%)** |

Three fifths of the plane loads a cell no declaration covers — a RAM cell — which is
the answer `pw` gave and for the same reason: `eval_src` reports origins alongside SID
store sources, so a parameter staged in RAM arrives here unnameable. (This said "at
init or at note-on"; naming the init copies was built in #98 and moved **zero** of
these — the cell is written by the *play* phase in 94% of the pw cases. §0.) The shape confirms it is the same problem: **38872 emits (16%) sit in a
constant-nonzero-delta run of two or more**, and 38817 of them are cutoff — 39% of the
$16 emits and 21% of the $15 emits, against 0% of $17 and $18. Cutoff is an accumulator
sweeping; resonance, routing and mode are settings. A further 11.3% loads a cell that
*is* declared but no longer holds the byte that reached the register (#61), 16597 of
them $18, where the low nibble is the volume DAC (docs/frameprog.md §1.2) and a
declared mode nibble combined with a volume level is not a declared byte.

So the ceiling for this step was ~8% and the step returns ~8%. Raising it was §7.2's
query for RAM-staged parameters, not a filter generator: the filter plane is one more
accumulator whose step, bound and rate the play code copied out of a table. That query
has since shipped and took the plane to 9.3%, all of it cutoff (§4c, and "The
accumulator's parameters, queried out of RAM").

### Realizing the ceiling: a register split, not forfeited

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile),
against the filter table above, and a change to `tracker.py` only. §7.1's finer
partition (§5): a write, not a register, is what refinement removes from `RAW`, and
the order-preserved section is rebuilt from the typed buckets and the residual
bucket in a constructed order rather than being required to have the typed writes
at one end.

**Measured first, so the ceiling was known before a line was written.** Of the
ctrl/AD/SR writes in registers the old rule left wholly residual, `_classify`
already explained 165843 — and the distribution said the rule, not provenance, was
the binding constraint:

| unexplained writes in the register | registers | emits in them | of those, `_classify` explains |
|---|---|---|---|
| 0 (lost to the subset search or the order check alone) | 644 | 23289 | 23289 |
| 1 | 404 | 31537 | 31133 |
| 2–5 | 414 | 28544 | 27258 |
| 6–20 | 521 | 46025 | 40213 |
| 21+ | 1794 | 319921 | 43950 |

644 registers — 23289 emits — were forfeit with **nothing** unexplained in them,
and another 404 for a single write apiece. Only in the 21+ row is the residual
mostly genuine, and even there 43950 emits are explained writes held hostage by
their neighbours.

| plane | before | after | this step's ceiling |
|---|---|---|---|
| interpreted | 576085/1933877 = 29.79% | **718297/1933877 = 37.14%** | 741928 |
| ctrl | 42789/300573 = 14.24% | **111659/300573 = 37.15%** | 123979 |
| ad | 17592/112534 = 15.63% | **54362/112534 = 48.31%** | 60824 |
| sr | 19373/115963 = 16.71% | **55945/115963 = 48.24%** | 61415 |
| freq | 414066/602528 | 414066/602528 (unchanged) | — |
| pw | 63232/561585 | 63232/561585 (unchanged) | — |
| filter | 19033/240694 | 19033/240694 (unchanged) | — |

**419 tunes improve and none regresses**, and `freq`, `pw` and `filter` are
byte-identical per tune — this step touches the order-preserved section and nothing
else. The canonical fixpoint holds 646/646, Gate FP 646/646 and the tracker law
646/646, all unchanged.

The gain is strong evidence, and the split is reported rather than folded: of
+142212 emits, **129660 (91.2%) are strong** — `lane` 405721 → 513283 and `gate`
10608 → 32706 — and 12552 are program immediates (`imm` 12615 → 25167). Against the
declared-byte ceiling §6 measured for the *strong* classes, the `lane` counts now
reach 69178/72778 of `ctrl`, 47010/52300 of `ad` and 47905/51702 of `sr` — 90–95%
of what provenance names at all, from 39%/26%/28%. Whole planes newly explained:
`ad` 166 → 285 tunes, `sr` 195 → 268, `ctrl` 52 → 57.

**The cost, named.** 23631 explained emits (14.2% of the 165843 ceiling: `ctrl`
12320, `ad` 6462, `sr` 4849) are still refused, because their key straddles the
residual — the same key's writes fall on both sides of an unexplained write across
frames — and a bucket that cannot sit wholly on one side is demoted back into the
residual rather than emitted out of order. That is **258 of 1874 voices over 113
tunes**; the demoted total is exactly the gap between ceiling and gain, measured
independently from either side. The refusal is the whole guarantee: the law would
fail if it were skipped, and tests/test_tracker.py shows it failing for both
placements of a straddled key. The rebuild check behind it (`_refine_voice`
returning None, the voice back at the RAW floor) fires on **no** corpus voice — the
bucket order it verifies is constructed to satisfy it — so it is a guard, and the
test that exercises it drives it from a deliberately swapped order.

### The trigger domain, measured — and how little a clock explains

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile). Until
this step the trigger domain had no number anywhere: `Coverage` counted emits and said
nothing about fires, so the 37.14% headline was silent about the other half of the
primitive. It now has one, and the one is small.

| the trigger domain | nodes | fires | of all fires |
|---|---|---|---|
| all fire-routed nodes | 3801 | 280737 | 100% |
| strictly periodic (one distinct gap, ≥3 fires) | 836 | 76914 | 27.4% |
| … of which period 1 (consecutive frames) | 445 | 71076 | 25.3% |
| … of which period ≥ 2 — the divider-shaped ones | 391 | 5838 | **2.1%** |
| … of those, at `DIV`'s own phase (`n-1, 2n-1, …`) | 28 | 877 | 0.31% |
| **generated by a declared divisor (§4d)** | **3** | **300** | **0.107%** |
| the `EDGE` floor: the arrangement's population | 3798 | 280437 | 99.89% |

Read down that table: the "27% of fires are periodic" figure the output side offers
collapses at every step where a claim has to be earned. **Nine tenths of the periodic
fires have period 1** — a stream firing on consecutive frames is not a divider at all,
it is a store site that runs unconditionally, and 121 nodes (24200 fires) fire on every
one of the 200 frames. That leaves 2.1% of fires divider-shaped. Of those, only 28
streams sit at the phase `DIV(n)` fixes, so a *fitted* divisor — a period read straight
off the fire pattern — would still reach only 0.31% without a phase parameter as well.
And of those 28, **3** have a divisor the play code actually declares. Each step
divides the previous by roughly an order of magnitude, and the last one is provenance.

**420 of the 646 tunes declare a divisor and 3 of them generate an edge stream** —
`MUSICIANS/D/DaFunk/3-Speed.sid`, `MUSICIANS/D/Dr_Piotr/Agonia.sid` and
`MUSICIANS/D/Dune/Beach.sid`, one node each, 100 fires each. All three are `DIV(2)`,
the smallest admissible divisor, so the evidence is thin even where it holds: what
carries it is that `2` is a byte the play code reloads into a cell it steps down, and
that the law checks all 200 frames of the stream. That is the whole claim, and it is
0.107% of the domain. (§4i adds a fourth, `MUSICIANS/B/Bonifacio_Robert/Delta_Man.sid`,
whose divider reloads 48 and whose counter the post-init image seeds at 32: `DIV(48, 31)`,
where the divider's own phase 47 generates nothing.)

What is refused is named, and each refusal was measured before it was made. A divisor
fitted to the fire pattern — the obvious way to "explain" 27% — is refused outright.
A reload out of a RAM cell is refused: its post-init byte is runtime state, and across
the corpus it adds no stream that the immediates do not (the one period-hit above is
an immediate).

`DIV(1)` is refused, and that refusal is the expensive one. 121 nodes fire on every
one of the 200 frames, **217 of the 646 tunes reload a `1` into a divider somewhere**,
and lifting the refusal would take **36 nodes and 7200 fires** across 36 tunes — 2.6%
of the domain, twenty-five times the shipped figure. It would be bought with a byte
that has nothing to do with why those streams fire every frame: they fire every frame
because their store site is unconditional, which the root frame clock already says.

The residue — **280437 fires over 3798 nodes, 99.89% of the domain** — is precisely
the population §7.4 has to explain. It is not a divider problem: 2965 of those nodes
(203823 fires) are not periodic at all, which is what an orderlist and a pattern
look like from the outside. Per tune the domain is small enough to work with directly:
the median tune has 412 fires and the largest 1716.

The value partition is **byte-identical** across this step, as it must be — a `DIV`
that replaces an `EDGE` fires on the same frames and so emits the same bytes. 718297
of 1933877 interpreted, `freq` 414066, `pw` 63232, `ctrl` 111659, `filter` 19033, `sr`
55945, `ad` 54362, and the class split `lane` 513283 / `gate` 32706 / `imm` 25167 /
`ramp` 138 / `seed` 1 — every figure unmoved. The canonical fixpoint holds 646/646,
Gate FP 646/646 and the tracker law 646/646.

### The accumulator's parameters, queried out of RAM

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile), against
the table above, and a change to `tracker.py` only: §4c's step is no longer walked out
of the statement tree but **queried** from frameprog's origin map at the accumulator
statement's own execution (`frameval.eval_watch`, docs/frameprog.md §1.4).

| plane | before | after |
|---|---|---|
| interpreted | 718297/1933877 = 37.14% | **747709/1933877 = 38.66%** |
| pw | 63232/561585 = 11.26% | **89376/561585 = 15.92%** |
| filter | 19033/240694 = 7.91% | **22301/240694 = 9.27%** |
| freq | 414066/602528 | 414066/602528 (unchanged) |
| ctrl | 111659/300573 | 111659/300573 (unchanged) |
| ad | 54362/112534 | 54362/112534 (unchanged) |
| sr | 55945/115963 | 55945/115963 (unchanged) |

**134 tunes improve and none regresses** — 120 on `pw`, 36 on `filter` — and `freq`,
`ctrl`, `ad` and `sr` are byte-identical *per tune*, class split included. The whole
gain is the sweep: `ramp` 138 → **25399** and `seed` 1 → **4152**, while `lane`, `gate`
and `imm` do not move by a single emit. One more tune has its whole filter plane
explained (3 → 4) and no other whole-plane count moves. The canonical fixpoint holds
646/646, Gate FP 646/646 and the tracker law 646/646.

**The seed rose with it and stays shallow.** 4152 of the 29551 sweep emits are the
observed byte a run starts from — one per run, and runs are per re-staging, so a tune
whose step is re-copied at every note-on pays one observed byte per note. `ramp` is
strong and `seed` is not, and `Coverage.classes` reports them apart precisely so this
gain cannot be read as 29551 generated emits.

The trigger domain's **generated** figure does not move: 300 fires, the same three
`DIV` nodes. Its denominator does — 280737 → **305119** — because every new `RAMP` is
a node fired by its own `EDGE` stream, so the same 300 reads 0.107% → 0.098%. That is
the domain getting bigger, not the floor getting worse, and it is the price of counting
triggers over the graph the tracker actually builds.

**Every refusal, and what it costs**, over each plane's own last-write-wins denominator
(the `cutoff` column is $15/$16 only — $17/$18 are settings, not accumulators, and no
sweep is attempted on them):

| the emit is | pw | cutoff |
|---|---|---|
| a declared lane read — §4b claims it first | 63093 | 7073 |
| in a register no store links to a stepped cell | 228054 | 50227 |
| inside a zero-delta run: the step would be zero | 131589 | 23118 |
| in no run at all: a single emit | 27 | 59 |
| in a constant-delta run whose step no declaration names | 112539 | 27939 |
| … of those, refused only for a `mut` offset | 14 | 0 |
| **generated: `ramp` + `seed`** | **26283** | **3268** |
| **all** | **561585** | **111684** |

Read down it: the binding constraint is no longer the generator but provenance again.
**228054 pw emits (41%)** are written by a store whose byte reaches no cell the play
code steps — computed some other way, or accumulated behind a store the tree cannot
follow — and **112539 (20%)** do sit in a genuine constant-delta run whose step the map
traces to no declaration. (§7.2 recorded the reason as "filled at init"; #98 built that
and moved **zero** — 94% have an accumulator the play phase writes.) The zero-delta row
is the third of the plane that simply
holds its value; a `RAMP` of step zero would take all 131589 with a byte that predicts
nothing, which is the refusal `DIV(1)` makes in the other domain (§4d). The `mut`
refusal is measured rather than assumed and is nearly free here: **14 emits on one
tune**. The run-level refusal is all-or-nothing by construction — one stepped emit
whose origin no declaration names refuses its whole run — and it fires on 303 tunes for
`pw` and 251 for `cutoff`.

### One plane, two generators — and how little of it the program text names

Same 682 cached tunes at the PSID start subtune, 200 frames (649 reach the gate here),
against the table above: §4e's masked route, a change to `tracker.py` only. **The
population was measured before a line was written**, because the point of the step is
the size of the answer. Of every SID write the rules above leave unexplained, bucketed
by whether its register class's store sites name a bit partition at all and whether
every field of that partition is then sourced:

| the write is | all | $18 | $17 | ctrl | rest |
|---|---|---|---|---|---|
| explained already (§4b/§4c/§5) | 576445 | 2321 | 15642 | 73598 | 484884 |
| the text names a partition and **every field is sourced** | **699** | **676** | **0** | **21** | **2** |
| the text names a partition, a field is not sourced | 10353 | 8100 | 411 | 1588 | 254 |
| the text names a partition, no declared byte at all | 17312 | 11081 | 1059 | 2719 | 2453 |
| no store site of the class names a partition, a declared byte moved | 131102 | 7821 | 6694 | 42430 | 74157 |
| no partition, and no declared byte either | 1206898 | 44240 | 30965 | 182003 | 949690 |
| **all** | **1942809** | **74239** | **54771** | **302359** | **1511440** |

The population the extension is *for* — a register written with only some of its bits
changed, i.e. a declared byte at a non-`mut` offset that is not the byte the register
took — is **142154** emits (`$18` 16597, `$17` 7105, `ctrl` 44039, the rest 74413), of
which **35384** hold a declared byte that is a submask of the write, the shape an `OR`
of two fields leaves.

**The recoverable population is 699 emits — 0.036% of the corpus, and 4.1% of the
16597 `$18` emits the oracle counted (docs/gt-oracle.md §4.3).** It is far below that
16597 and the rule was **not** widened to reach it. The rows below the gain are what
widening would have to take: 27665 emits whose partition the text does name but whose
fields no declaration holds — the mode nibble was staged in RAM or built by arithmetic,
not copied out of a table at play time; **which** phase staged it is unmeasured on this
plane, and #98 showed the init reading is false on `pw`/cutoff (§0), so do not assume it
here — and 131102 whose store site is a
bare load or an `OR` of two variable terms and names no mask at all. Taking the mask
off the observed bytes instead (any declared byte that is a submask of the write) would
reach 35384; that is the fit §4b, §4c and §4d each refuse in their own domain, and it
is refused here.

`$17` is the sharper result: **zero** of its 7105 are recoverable. Resonance is ORed
with a routing mask assembled from three voices' flags, and no store site in the corpus
writes that as a partition the text names — the routing byte is built in RAM.

| plane | before | after |
|---|---|---|
| interpreted | 752598/1942809 = 38.74% | **753274/1942809 = 38.77%** |
| filter | 22301/240844 = 9.26% | **22977/240844 = 9.54%** |
| freq | 417490/605952 | 417490/605952 (unchanged) |
| pw | 89850/565009 | 89850/565009 (unchanged) |
| ctrl | 112502/302359 | 112502/302359 (unchanged) |
| ad | 54436/112608 | 54436/112608 (unchanged) |
| sr | 56019/116037 | 56019/116037 (unchanged) |

**5 tunes improve and none regresses**; every other plane is byte-identical per tune,
class split included, because a masked group is the last rule tried and only sees a
write §4b and §4c have both declined. The class split moves by exactly the gain —
`lane` 516682, `gate` 32708, `imm` 25188, `ramp` 25399, `seed` 4152 all unmoved, and
`mask` **0 → 676**. The canonical fixpoint holds 649/649, Gate FP 649/649 and the
tracker law 649/649. The trigger domain's generated figure does not move (300 fires,
the same three `DIV` nodes); its denominator goes 306277 → 306953, one edge stream per
masked group, exactly as §4c's `RAMP`s moved it.

`mask` is its own class and is never folded into the strong figure. All 676 are a
declared mode nibble plus the store statement's own volume constant: half the byte is
a declared byte at a recovered row and half is a program constant, so the write is
neither `lane` nor `imm` and is counted as neither. What it is **not** is the blanket
`imm` reading §6 refuses above — the 34177 filter emits whose whole byte is some
program constant stay refused, because there the observation picks which constant,
while here the store statement's own text says which bits the constant owns.

**Every refusal, and what it costs.** A mask fitted to the observed bytes: 35384 emits,
refused outright. A partition whose fields reach no declaration: 10353. A store site
that names no partition: 131102. And the order-preserved section: 23 emits on 2 tunes
(`ctrl` 21 on `MUSICIANS/G/Galway_Martin/Commando_High-Score.sid`, whose ctrl store is
`(m_12EB | $08)`, and `sr` 2 on `MUSICIANS/A/Abynx/Are_Friends_Electric.sid`) decompose
cleanly but are refused because ctrl/AD/SR is a sequence of whole-byte writes rather
than a partition of one (§5).

### The relative route, and the gap between what it expresses and what it recovers

Same 682 cached tunes at the PSID start subtune, 200 frames (649 reach the gate),
against the table above: §4f's relative route, a change to `tracker.py` only.

| plane | before | after |
|---|---|---|
| interpreted | 753274/1942809 = 38.77% | **753555/1942809 = 38.79%** |
| pw | 89850/565009 | **90000** |
| filter | 22977/240844 | **23104** |
| freq | 417490/605952 | **417494** |
| ctrl | 112502/302359 | 112502 (unchanged) |
| ad | 54436/112608 | 54436 (unchanged) |
| sr | 56019/116037 | 56019 (unchanged) |

**5 tunes improve and none regresses.** The class split moves by exactly the route's own
class — `lane` 516682, `gate` 32708, `imm` 25188, `ramp` 25399, `seed` 4152 and `mask`
676 are all unmoved, and `rel` goes **0 → 316**. The canonical fixpoint holds 649/649,
Gate FP 649/649 and the tracker law 649/649. The trigger domain's generated figure does
not move (300 fires, the same three `DIV` nodes); its denominator goes 306953 → 307269,
one edge stream per relative pair.

316 emits and +281 interpreted are not the same number, and the difference is the
interesting one: on `MUSICIANS/S/SounDemoN/Arkanoid.sid` 35 freq emits the note lane had
been matching as *observed* words become a *declared* delta over a program constant, so
that tune's count does not move while its evidence improves. By base kind the 316 split
**`Prev` 277, `Const` 35, `Gen` 4** — all three named bases reach the corpus, and the
plane's own previous value is where nearly all of it is.

**Every refusal, and what it costs.** Sites first (a site is a store statement, so the
count is of program text, not of emits):

| the store statement | sites |
|---|---|
| names a binary op over a declared term and a named base — **the rule** | 316 emits, over 6 tunes |
| an `ADD`/`SUB`/`XOR` of more than two terms | 315 |
| an `ADD`/`SUB`/`XOR` whose other term names no base | 177 |
| an `ADD`/`SUB`/`XOR` with no declared term at all | 520 |
| a declared lane as the minuend over the plane's own value | 1 |

Then per emit, over the writes a relative class leaves unexplained:

| the emit is | emits |
|---|---|
| refused because the named base has no value here (`Gen` names no read cell, or `Prev` has no previous emit) | 35242 |
| in a relative class, but the declared delta and the named base do not predict the byte | 30143 |
| refused for a `mut` offset on the delta cell | 3388 |
| refused because the declared delta is zero, which predicts nothing | 389 |
| refused because ctrl/AD/SR is a sequence of whole-byte writes, not a composed value | 2198 |
| **taken: a declared delta over a named base** | **316** |

And the refusal the whole rule rests on, priced: **a delta back-computed as
`emitted − previous` would take 29559 further emits** — every unexplained write in a
relative class whose plane moved at all — for 93× the shipped figure. It is refused
outright. Such a delta is not data: it is the output written back as a parameter, and
the law cannot tell the two apart, which is exactly why the provenance rule has to. The
same reasoning refuses a fitted `RAMP` step (§4c), a fitted `DIV` period (§4d) and a
fitted mask (§4e), and this is the fourth domain to be measured under it.

**A mask on the delta path was tried and returned nothing.** Following the program text's
`AND`-immediates from the declaration to the store — the reading §4e takes of a field, so
the delta would be `lane[row] & m` — adds **no emit at all** and only more refused
candidates, so a narrowed byte is not what stands between the sites and the emits. The
rule ships without it rather than carrying the machinery for a zero.

The oracles are where this construct is known to exist, and they say the same thing from
the other side: it is expressible, and a decompiled driver rarely names the delta.

| oracle | admitted interpreted | strict `RAW` | law | strict, full window |
|---|---|---|---|---|
| GoatTracker, 71 tunes | 112988 → **113585** of 282516 | 126554 → **122247** | 71/71 → 71/71 | 4 → 4 |
| SID-Wizard, 64 modules | 105691 → **106034** of 228702 | 123011 → **122668** | 64/64 → 64/64 | 64/64 → 64/64 |
| DefMON, 6 tunes | 12556 → 12556 of 28800 | 16244 → 16244 | 6/6 → 6/6 | 6/6 → 6/6 |

**4307 GoatTracker emits and 343 SID-Wizard emits leave `RAW`**; 14 GT tunes and 4 SW
modules improve and none regresses on either. GoatTracker's vibrato is the relative route
in its purest form — `freq = freq ± speedtable.right[row]`, a `Prev` base and a declared
delta — and its 12744 refused emits now decompose completely: 4307 interpreted, 6284 the
*high* byte moving on carry (the wider-accumulator limit of docs/gt-oracle.md §4.4, not
this one), 1977 a relative cell the composer's own table no longer predicts, and 176 the
editor's bit-7 escape, where the declared byte is a shift count applied to a note interval
rather than a step. SID-Wizard's detune is the `Node(i)` base — the pitch lane plus the
instrument program's own detune column — and it takes 343 of the 38840 its freq plane
refuses, because the rest carry a vibrato accumulator the module does not declare.

**DefMON recovers zero, and that is the finding to state plainly.** All 2421 of its
`('slide','detune')` emits stay `RAW`, byte for byte. The route expresses the shape —
`AF` is documented as "portamento toward `current_note + AF`" and `TR` bit 7 clear as
"relative, added to the voice's transpose buffer" — but neither yields a *declared delta
in the value domain*. `TR` shifts the note **index**, and the oracle already reads the
final index off the replay's address bus, so it costs nothing and gains nothing. `AF`
selects a slide **mode**, and the per-frame rate comes from a lookup table inside the
replay's own 6502 code, which is player code and not the composer's song. A delta fitted
to the emitted stream would take all 2421 and explain none of them, so they stay at the
floor. What DefMON needs is not this route but the note-index domain (§7.4) and a
turning `RAMP` bound (§8).

So the construct recovers **316 emits from binaries** and **4650 from two editors' own
models**, and the two numbers are reported apart because they measure different things:
the first is what a 6502 driver's program text names, the second is what the format can
express when the song is in hand. The wall is provenance, as it was for §4e — 35242
emits sit in a class whose site is named but whose base the write's own read cells do
not supply.

### The arrangement, and what rung (f) actually bought

Same 682 cached tunes at the PSID start subtune, 200 frames (649 reach the gate), against
the table above: §4g's pattern generator, a change to `tracker.py` only. **The population
was measured before a line was written**, and the measurement is the result.

| plane | before | after |
|---|---|---|
| interpreted | 753555/1942809 = 38.79% | 753555/1942809 = 38.79% |
| every plane | freq 417494, pw 90000, ctrl 112502, filter 23104, sr 56019, ad 54436 | **byte-identical** |

**The recovery is zero: `arr` 0 over 649 tunes, every plane and every class unmoved,
and no tune moves in either direction.** The canonical fixpoint holds 649/649, Gate FP
649/649 and the tracker law 649/649. The trigger domain does not move either — 300 of
307269, the same three `DIV` nodes — and its denominator does not move, because no
arrangement node was built to fire one.

That is the honest answer to "what did rung (f) buy the tracker", and the refusal chain
says exactly where it stops. Of the **366 resolved deref sites over 162 tunes** the rung
proves, with each site's row index resolved through the locals to the cell it loads:

| the row index of a resolved deref is | sites |
|---|---|
| computed — no cell at all | 228 |
| a cell some writer the program text does not name also writes | 94 |
| **a cell the play code only steps or sets by its own text** | **42** |
| absent (a bare `*ptr`) | 2 |

and the orderlist position, the same reading applied to the pointer's own reload index:

| the reload index is | sites |
|---|---|
| computed | 233 |
| a cell, not walked | 83 (+35 mixed) |
| **a cell the play code only walks** | **15** |

**Both walked at once: zero sites.** So no tune in the corpus carries a two-level
arrangement whose orderlist *and* pattern row are both program text, and the layer ships
as a pattern generator only, with the orderlist position taken off the machine's own
address bus.

Then per emit, over the 42 sites whose row does walk:

| the write is | emits |
|---|---|
| in a class no store site names a proven deref for | — (the rule never sees it) |
| refused: the target block lies outside every `datadecl` region | **794**, on 2 tunes |
| refused: ctrl/AD/SR is a sequence of whole-byte writes, not a composed value | 71128 |
| **taken: a declared pattern byte at a generated row** | **0** |

Only **2 of 649 tunes** — `MUSICIANS/C/Crabtree_Ian/Angel_Meadows.sid` (646 emits) and
`MUSICIANS/H/Hubbard_Rob/Thing_on_a_Spring.sid` (148) — have a last-write-wins store whose
text points at a proven deref *and* a row the text walks. On both, **every** target block
falls outside every declaration: `datadecl` carved no region there, so there is no declared
pattern to read and the byte agreement would be coincidence. The refusal is #61's, reached
from a third direction.

**The refusal the whole rule rests on, priced: a row read off the observed stream would
take 54557 emits.** That is every write in a class whose store names a proven deref and
which no walk explains — the population a segmentation of the observed row stream, or an
orderlist recovered from the blocks the run happened to visit, would claim. It is 69× the
whole predicted population and it is refused outright, for the reason §4c refuses a fitted
`RAMP` step, §4d a fitted `DIV` period, §4e a fitted mask and §4f a back-computed delta.
This is the fifth domain measured under that rule and the first where it leaves nothing at
all.

**The other side of the same axis, reported apart.** The oracles now express the
arrangement in this primitive, so the two numbers finally exist together and they are not
the same number: the three editors' own models carry **73766 emits at a generated row** —
GoatTracker 41424 over 71 tunes (185 of 2407 patterns, 997 of 123941 rows, 213 of 6579
orderlist entries), SID-Wizard 28143 over 64 modules (220 / 1836 / 241), DefMON 4199 over
6 tunes (20 of 387 patterns, 103 of 5535 rows, 31 of 1476 orderlist entries) — against
**0** here. That is the largest format-versus-recovery gap this project has measured, and
it is the third of its shape: §4e's masked route bought 26167 emits from the editors and
676 from binaries, §4f's relative route 4650 and 316. The format expresses an arrangement;
a 6502 driver's program text does not name one. See docs/gt-oracle.md §3.2 and
docs/dm-oracle.md §3.2.

**This paragraph used to predict what would move it. The prediction was built and
measured false.** It said `frameval._addrs` reports a deref's pointer cells rather than
its target, so reporting the **resolved** address — proved by rung (f) to lie in
`{T[k]} + [0, bound]` — would make every pattern byte a declared byte at a recovered
`(block, row)`. It was built (#105) and arrangement recovery stayed at **0**:

- The proof supplies the address **space**, not the address: the entry `k` is live state
  at **366 of 366** resolved sites, so a single target block is named exactly **once** in
  3929 deref addresses.
- Handed the address the run itself read — the fitted version this document refuses
  everywhere — recovery is **still zero** and the partition is byte-identical. 3751
  writes gain an address; 3008 land in a `datadecl` `kind == "stream"` which `_banks`
  does not admit, 683 more are refused by #61's byte check, and the 60 that pass are
  already interpreted through another cell.

So the address was never what the consumer lacked. `docs/frameprog.md` §4.6 carries the
census. **Do not re-propose this change without new evidence**; two walls stand behind
it, both nameable — `datadecl` carving deref targets as `stream`, and `_banks` admitting
`table` only, whose measured ceiling is 1585 emits (0.08%) and only under a refused
address.

### The node identity, and what the cursor's own value would cost

§4h's change, measured against the same tree before it on the same 682 cached tunes at the
PSID start subtune, 200 frames (649 reach the gate), and on `tools/graph_diff.py` over 15
GoatTracker tunes at 600 frames (14 map; one is an init runaway).

| | before | after |
|---|---|---|
| interpreted | 753971/1942809 = 38.81% | **byte-identical**, every plane and every class |
| `graph_diff` matched nodes | **71** of 1648 theirs, 1801 ours | **71** of 1648, 1801 ours |
| only ours / only theirs | 1730 / 1577 | 1730 / 1577 |
| tracker law, Gate FP, canonical fixpoint | 649/649 | 649/649 |
| triggers | 300/307060 | 300/307225 |

**The matched count does not move, and that is the finding.** Per tune it is identical
node for node (9, 11, 2, 1, 2, 1, 8, 2, 7, 1, 18, 4, 0, 5). The cause is not subtle: over
the corpus the pairs are **one to one with the lane keys already built**, because a stream
key already carries the register, and a register almost never reads two of a declaration's
objects. The pair is a better *name* for a node — its table is the composer's table rather
than the whole block, and 5232 of 6645 declared-lane nodes now carry one — but it is not a
finer partition. The only trace at corpus scale is 165 more fires in the trigger
denominator, where a register that does read two objects now drives two streams.

**Keying the row on the cursor's observed value was built, and it is worse.** Nodes split
by which cursor was holding the index (and by where that cursor's own byte came from) take
`graph_diff` matched from **71 to 45** while adding 99 nodes: the split breaks 26 nodes
that matched exactly and creates none that do. The census says why — over the corpus only
**39305 of 432225 pair emits (9.1%)** have a cursor whose value the machine's own map can
name at all, the other 392920 being live state the origin map cannot reach, which is the
same 65-of-1763 shape docs/node-partition.md §3(c) reports from the declaration side. A
split on "could we name this cursor's source" is a split on our own reach, not on the
composer's objects. It is refused, and the census is what it left.

**The ceiling this metric has, measured before any of it.** Of the editor's 1637 nodes
that write anything over those tunes, **795** have a write set inside a single *interpreted*
node of ours — the most any repartition of what we already attribute could match. **523 of those
795 are `EDGE` trigger nodes**, 7 are row indices and about 265 are plane generators. So
node correspondence on the value planes is bounded at roughly 265 above today's 71, and
the larger half of the gap is the trigger domain — the arrangement again (§7.4), not the
value partition. Counting our `RAW` floor as a candidate raises the figure to 1294, which
is the same statement as §6's opening: the writes are all produced, in one undifferentiated
node.

The refusal histogram, corpus-wide (649 tunes):

| the pair is | count |
|---|---|
| a load base no declaration covers | 13085 |
| a declared base the text names no cursor for | **5166** |
| a declared base whose own offset is play-written | 89 |
| **a region built: base, extent and cursors all program text** | **7699** |
| …of which the text names two or more cursors for | 1489 |
| region nodes / their emits | 5232 / 451314 |
| declaration fallback nodes / their emits | 1413 / 98586 |
| a read whose named cursor held the index the machine read | 39305 |
| a read whose cursor's value nothing names | 392920 |

**And the refusal the identity rests on, priced: a row chosen to fit the byte would have
taken 413759 emits.** That is every unclaimed write in a class the store statement names a
declaration for, whose byte some named region holds *somewhere* — a search over the
region's rows would claim it, and the machine's own read index is what refuses it. It is
the sixth domain measured under that rule (§4c 1420, §4d, §4e, §4f 29559, §4g 54557), and
the largest.

### The sequencer chain, and which link actually binds

§4i's change, measured against the same tree before it on the same 682 cached tunes at the
PSID start subtune, 200 frames (649 reach the gate), and on `tools/graph_diff.py` over 15
GoatTracker tunes at 600 frames (14 map). A change to `tracker.py`, `trackertext.py` and
`tools/tracker_arrange.py` only.

| | before | after |
|---|---|---|
| interpreted | 753971/1942809 = 38.81% | **byte-identical**, every plane and every class |
| **triggers (fires)** | **300/307225 = 0.0977%** | **304/307225 = 0.0990%** |
| `graph_diff` matched nodes | 71 of 1648 theirs, 1801 ours | 71 of 1648, 1801 ours |
| only ours / only theirs | 1730 / 1577 | 1730 / 1577 |
| tracker law, Gate FP, canonical fixpoint | 649/649 | 649/649 |
| GoatTracker law / strict-full | 71/71, 4 | 71/71, 4 (admitted 113585/282516, strict `RAW` 122247) |
| SID-Wizard law / strict-full | 64/64, 64/64 | 64/64, 64/64 (admitted 106034/228702, strict `RAW` 122668) |
| DefMON law / strict-full | 6/6, 6/6 | 6/6, 6/6 (admitted 12556/28800, strict `RAW` 16244) |

Every plane, every evidence class and every tune's value partition is unmoved — `lane`
516986, `gate` 32914, `imm` 25188, `ramp` 25399, `seed` 4152, `mask` 676, `rel` 317,
`arr` 0 — as it must be, since a `DIV` that replaces an `EDGE` fires on the same frames
and a generated row emits the same declared byte the recovered run did. What moves is the
*evidence*: **15 emits on one tune are now read at a row the program text generates**
rather than at a row observation yielded, and **4 more fires on one tune are generated**
rather than replayed.

**The per-link attrition, which is the result.** The static links come from
`tools/tracker_arrange.py` over 650 decompiling tunes; the realized ones from the refusal
histogram over the 649 that reach the gate.

| the chain has | tunes | objects |
|---|---|---|
| link 1 — a tick: a declared divisor with the phase its own counter seeds | **424** | 604 ticks |
| link 2 — a cursor: a cell the text only walks, with a step and a modulus | **593** | 2198 cursors |
| links 1–2 | **401** | — |
| link 3 — a declared region the text indexes at one of those cursors | **203** | 710 of 7712 regions |
| links 1–3 | **159** | — |
| link 3 reaching a *lane stream* the recovery actually builds | 87 | — |
| link 1 realized: a `DIV` node in the graph | **4** | 4 nodes |
| link 4 realized: a table read at a generated row | **1** | 1 cursor node, 15 emits |
| the whole chain: a `DIV` beating a cursor | **0** | — |

Read down it and the collapse is between "the text names all three links" (159 tunes) and
"the machine's own streams agree with them" (1). Two refusals do all of that work, and
both are counted rather than argued:

| the refusal | count |
|---|---|
| a lane stream whose region the text names no *walked* cursor for | 6205 streams |
| a cursor some writer reloads — a `RAMP` walks and never resets | 217 |
| a row run the cursor's own beats do not reproduce | 58436 rows |
| **taken: a row the cursor's declared seed, step and modulus generate** | **15** |

**And the trigger domain, where the headline was supposed to move.** Of the 8051
fire-routed lane streams, **1101 are strictly periodic** with a period of two or more
(16071 fires), and the period is where it stops:

| the periodic stream's period is | streams | fires |
|---|---|---|
| a byte some divider's reload declares | 17 | — |
| a **product** of two declared divisors — a cascade `DIV -> DIV` since the universal-layer step, and 0 pass its chain evidence (§0) | 95 | 1826 |
| neither | **989** | **13608** |
| …of the 17, generated by the shipped rule (period *and* phase) | **5** | **404** |
| …of the 17, a phase no counter seed supplies | 9 | 196 |
| …of the 17, a stream that starts later than its own period allows | 3 | — |

**So the phase was never the binding link, and §8's estimate of it was 25× too large.**
The refused phases are worth **196 fires over 9 streams**, not 4961, and the declared
counter seed recovers 4 of them. The binding link is one level up: **90% of periodic
streams have a period no reload declares at all**, so there is no tick to hang a cursor or
a pattern on. That is the correction §0 records, and it re-aims §7.4: before a pattern can
decide *which* tick carries a note, the tick has to be nameable.

**Every refusal, priced.** A phase read off the first observed fire would take the 196
above and, without the divisor rule beside it, the whole 16071 — it is refused for the
reason §4c refuses a fitted step, §4d a fitted period, §4e a fitted mask, §4f a
back-computed delta, §4g a segmented row and §4h a searched row; this is the seventh
domain measured under it. A **cursor triggered by the stream that reads it** rather than
by its own step statement — the reading that assumes one step per read — would take **21
rows over 3 nodes on 2 tunes** against the shipped 15 over 1 on 1: it is refused because
the beats are what the machine ran and the read stream is only what agreed. And a tick
taken from a **walked counter's own modulus** — the obvious way to reach the periods 6, 8,
12, 16, 24, 48, 64 and 96 that dominate the refused set — was measured before it was
built and has **no population at all**: **zero** of the corpus's 2198 walked cursors step
exactly once per frame, so no counter's `AND`-modulus is a frame divider.

### The universal layer completed: the primitive's two missing elements, and three whole oracles

The universal-layer step (§0's last five rows) closed the primitive against what the three
editors' own songs need — the input-tick `DIV`, the `Pair` route, the `SELECT[rel]`
emitters, one write-captured SID-Wizard player, one comparison harness — and this is its
measurement. 682 cached tunes, PSID start subtune, 200-frame windows (649 reach the gate);
oracles at 200 frames; `graph_diff` over the 15-tune set at 600 (14 map).

| | before | after |
|---|---|---|
| interpreted | 753971/1942809 = 38.81% | **byte-identical**, every plane, class and tune |
| triggers (fires) | 304/307225 | **304/307225** — the cascade recovers 0 (§0) |
| tracker law, Gate FP, canonical fixpoint | 649/649 | 649/649 |
| GoatTracker law / admitted | 71/71, 113585/282516 | **71/71, 117074/282516 = 41.4%** (+3489: transpose and 16-bit sweeps) |
| GT `SELECT[rel]` / `Pair` nodes | 0 / — | **20 over 4 tunes / 2478 over 72** |
| SID-Wizard modules / law | 64, 64/64 | **95, 95/95** — the fetched tarball holds 95, the old cache was partial |
| SW `SELECT[rel]` / `Pair` nodes | 0 / — | **60 over 15 modules / 1060** |
| DefMON law / `SELECT[rel]` | 6/6, 0 | **6/6, 7 nodes over 3 tunes** |
| `graph_diff` matched | 71 of 1648 theirs, 1801 ours | **60 of 1484 theirs, 1801 ours** |

The SID-Wizard row changes basis as well as size: `sw_native` now records the writes the
editor's driver makes — WRPULS/WRWFGHO write freq, pw and ctrl every frame — instead of
`play_frame`'s nibble-masked snapshot diff, so its records are what a decompiled
SID-Wizard binary would give frameprog, and its admitted figure (99654/339269 = 29.4%
over the 95) is a new baseline, not a move on the old one.

**The matched count *fell*, and that is the structural finding restated from the other
side.** The oracle graphs got closer to the editors' objects — one pair node where two
byte streams stood, one shifted `SELECT[rel]` where per-transpose copies stood — so their
node count dropped 1648 → 1484, and 11 of the 71 matches died with the per-register
shapes they matched on. A register-first partition loses correspondence every time the
other side becomes more object-first (§0); the recovery's own partition did not move by
one byte. The gap is now measured for two editors instead of one — DefMON's first
number is 2 of 47 on Automatas — and it is the recovery's to close, object-first,
not the harness's.

**The objective in this section is the wrong one, and the list below is kept for its
measurements rather than its ordering.** It ranks work by how fast it shrinks the
residual. The residual is a property of a **register-first** partition — every item
here refines what drives one SID register — and §0 records the measurement that makes
that objective misleading: **zero** of a composer's writes are ones the recovery never
produces, and our node partition barely intersects the editor's (1–11 of 42–575, wrong
in *both* directions). Shrinking the residual therefore improves the justification of a
partition that is not the one being recovered.

The measured objective is **node correspondence** — `tools/graph_diff.py`'s matched-node
count against the editor's own graph — because that is what "universal" means here: the
same song expressed as the same graph. Every item below has a measured cost and those
numbers stand; read them as evidence about attribution, not as a queue.

**What repartitioning alone can reach is now measured, and it is not where the objective
sends you.** 795 of the editor's 1637 nodes have a write set inside a single interpreted
node of ours; **523 of them are `EDGE` trigger nodes** and only about 265 are plane
generators (§6). Naming a value node better is bounded at 265; the trigger domain is twice
that, and it is item 4.

Each step must still keep the law green and must never widen a declaration.

§4e's masked route is off this list because it has been measured out of it: a register
several generators drive is now expressible, two editors' own songs use it heavily
(docs/gt-oracle.md §4.3), and on our own recovery it reaches 699 emits. The mask must
come from the store statement, and 27665 emits whose statement does name a partition
still have a field no declaration holds — the same wall item 2 hit, measured on a second
plane. (This read "a parameter staged in RAM at init"; #98 built that and moved zero —
see §0.) It is not a generator-shape problem.

§4f's relative route is off this list for the same reason and with the same shape of
answer: three editors need it, the primitive now carries it, two of the three use it
(4650 emits leave their `RAW`), and our own recovery reaches 316. What stops it is
again provenance — 35242 emits sit in a class whose site the text names but whose base
the write's own read cells do not supply, and 30143 more where the declared delta and
the named base simply do not predict the byte. A delta read back off the output would
take 29559 of them and is refused (§6).

1. **The instrument planes are now bounded by provenance again** — the finer
   partition shipped (§5, §6): `ctrl`/`ad`/`sr` interpret 37%/48%/48% and their
   `lane` counts reach 90–95% of the declared-byte ceiling, so the tracker's own
   rules are no longer what holds them back. What remains splits three ways, and
   only the first is a tracker problem: 23631 explained emits refused because the
   key straddles the residual (a note-on lane read and its gate-off image on either
   side of an unexplained write — an arrangement generator, §7.4, would place both);
   writes whose byte reaches the store computed, with no source cell at all; and
   writes whose source cell falls outside every declaration. The last two are
   frameprog's, not this layer's.
2. **Parameters staged in RAM — shipped, and what it left.** The step is now queried
   from the origin map at the accumulator's own execution (§4c), which took `pw`
   11.3% → 15.9% and `filter` 7.9% → 9.3% over 134 tunes with none regressing. What
   remains on those planes is provenance, not generator shape, and §6's refusal table
   sizes each part: **228054 pw emits** are written by a store that links to no stepped
   cell at all, **131589** hold a constant value (a step of zero explains nothing), and
   **112539** sweep with a constant delta whose step the map traces to no declaration.
   The cutoff plane repeats the shape at a quarter of the size.

   **This item used to attribute that 112539 to "a parameter filled at init", and that
   was measured false.** Naming the init phase's copies was built (#98) and **zero** of
   those emits moved: **107785 of 114082 (94%) have an accumulator the *play* phase
   writes**, against 3775 staged at init. The cause is structural — `datadecl` carves
   declarations from **play** reads, so a table the driver relocates at init is read
   exactly once, by init, and 122782 init-written cells have no declared source region.
   Extending `datadecl` to those regions has a measured ceiling of **825 emits** and is
   recorded as a recommendation *against*. The first row is a frameprog dataflow
   question and the second is not an accumulator.
   A triangle sweep that turns at a declared bound is a further transfer this
   primitive does not have (§8). **This read "and no corpus tune reaches that limit
   before the step blocks it", and that is false**: Commando's `fx & 8`-clear arm is a
   12-bit accumulator whose step is the *declared* byte `$E0` (the `+6` lane of the
   `$5591` bank, masked `& $E0` by the text) and which turns where the masked high
   register reaches `$0E` going up and `$08` going down — both compare immediates in the
   walked text, `out/Commando.frameprog.txt:373-421`. The limit is reached, on the
   showcase tune, and the residue it leaves is **23071 of Commando's 25763 replayed
   writes**. The transfer is unbuilt, not unreachable.
3. **Arpeggio and vibrato as generators, not notes** — a note-on carries one
   note; an arp step is a downstream generator emit on that edge, so it must
   never appear as a fresh row.
4. **Arrangement** — orderlist and pattern as an `Index` route (§2), transpose as
   `Rel`. This is what replaces the `EDGE` floors and the recovered row streams: the
   row a note-on selects becomes an emit of the pattern generator, not observed data.

   **Corrected.** This item previously read "`LOOKUP` nodes routed to `Fire`, with
   shared subgraphs for reuse and a back-edge for the loop". That shape cannot
   express an arrangement: a `Fire` edge carries a trigger and no value, so it
   advances a pattern without naming it, and `SELECT`'s row index was a recovered
   tuple nothing could supply from outside. The primitive was missing one route, not
   a layer — and the back-edge was never needed, because `SELECT` already
   wrap modulo their length. The arrangement's own **phase** is what the divider
   lacks (§8): a `DIV(n)`-clocked orderlist emits nothing before frame `n-1` rather
   than inventing its first entry.

   **The population is now measured, and it is almost all of the domain.** §6's
   trigger census leaves **280437 fires over 3798 nodes, 99.89%**, and says what
   shape they are: 2965 of those nodes (203823 fires) are not periodic at all, and
   the 391 that are divider-shaped carry only 5838 fires between them. So a clock is
   not what is missing — §4d shipped one and it reached 3 tunes. What is missing is
   the table that decides *which* tick carries a note-on, which is the orderlist and
   the pattern. This is now the largest single unexplained thing at this layer, and
   it is the only step that moves the trigger figure at all.

   It also feeds back into the value domain: the 23631 emits §5 refuses because
   their stream key straddles the residual are exactly a note-on lane read and its
   gate-off image placed on either side of an unexplained write, and an arrangement
   generator would place both. Per tune the domain is small — median 412 fires — so
   the work is per-driver structure recovery, not scale.

   **Shipped, and it recovers nothing — measured.** §4g builds the pattern generator on
   rung (f)'s resolved-deref proofs and §6 reports the result: **0 emits over 649 tunes,
   the value partition byte-identical and the trigger domain unmoved at 300/307269.** The
   chain of refusals is the finding. Of 366 resolved deref sites, **324 have a row index
   the program text does not walk**; of the 42 that do, only 2 tunes have a last-write-wins
   store whose text points at the deref, and on both **every target block lies outside
   every `datadecl` region** (794 emits). Both levels walked at once: **zero sites**, so no
   corpus tune carries a two-level arrangement in program text at all. A row segmented off
   the observed stream would have taken **54557** emits and is refused outright.

   So the trigger domain is *not* what rung (f) unlocked, and the item stays on this list
   with its wall named rather than being closed. The wall is a one-hop provenance question
   in frameprog, not a generator shape: a deref address is impure, so `frameval` reports
   the pointer's cells and never the target, and every byte a driver reads through a proven
   pointer arrives here with no source cell at all. Reporting the **resolved** deref
   address as that store's source — the address rung (f) has already proved lies in
   `{T[k]} + [0, bound]` — is the change that would move it, and §6 sizes it.

   **The whole chain was then built, and the binding link is not this one.** §4i wires
   `DIV -> RAMP[Index] -> SELECT` inside the graph — the tick's phase off the declared
   counter, the cursor's beats off its own step statement, the row generated rather than
   observed — and §6 measures it link by link: **424 tunes have a tick, 593 a cursor, 203
   a region one of those cursors indexes, 159 all three — and 1 tune reads a table at a
   generated row while 0 have a `DIV` beating a cursor.** The trigger domain goes 300 to
   **304** fires and the value partition is byte-identical. What the census then names is
   a link nobody had costed: **989 of the 1101 strictly periodic streams (13608 fires)
   have a period no divider's reload declares**, and 95 more only as a product of two.
   So the next question at this layer is not which tick carries a note but **where the
   driver's tempo byte is** — the same provenance wall as items 1 and 2, one domain over.

   **The song itself is now recovered, and it says the wall is the evaluator** (§4j, §6).
   Commando's whole arrangement comes out of the program text — 3 orderlists, 32 patterns,
   571 rows, **0 divergences from the composer's own source** — so "the recovery cannot
   name the arrangement" is no longer true. What still cannot be *generated* is the row a
   note-on selects, for three named reasons: the pattern bytes are indices into other
   tables rather than register bytes (so §4g's byte-equality rule never sees them); a
   driver reads several pattern bytes and steps its cursor several times **inside one
   frame**, which `_run`'s node-major loop cannot interleave with a reader of `cur`; and
   the note-on divider's divisor is the row's own duration byte, which `DIV(n)`'s constant
   `n` cannot take. Two of those are the primitive's and one is the evaluator's; none is
   provenance, and that is the change of shape this step makes to the item.
5. **Codec** — `parse(emit(t)) ≡ t`, as for the structurer and frameprog.

### The song, recovered whole — and checked against the composer's own source

§4j's change, measured against the same tree before it on the same 682 cached tunes at the
PSID start subtune, 200 frames (649 reach the gate), plus Commando at its full Songlengths
duration (11750 frames).

| | before | after |
|---|---|---|
| interpreted | 753971/1942809 = 38.81% | **byte-identical**, every plane, class and tune |
| triggers (fires) | 304/307225 | 304/307225 |
| tracker law, Gate FP, canonical fixpoint | 649/649 | 649/649 |
| terminator-bounded regions recovered | — | **386** |
| tunes worse / better | — | **0 / 0** |

The value partition cannot move and does not: the song is carried beside the generators,
not as one, so nothing it names is claimed as an emit. What it adds is a second kind of
evidence at this layer — declared data at program-text offsets, with no observation in it.

**Commando, against Rob Hubbard's own source.** The `.asm` is the answer key, never an
input: everything below comes from the walked frame program, and the diff is run against
the source afterwards.

| what | recovered | divergences from the source |
|---|---|---|
| orderlists | 3, of 64 / 63 / 123 entries | **0 bytes** |
| patterns | 32, of 2 to 144 bytes | **0 bytes** |
| pattern rows | **571**, cut by the cursor's own increments | **0 boundaries, 0 bytes** |
| row fields | duration `$1F`, sustain `$20`, tie `$40`, parameter `$80` | — |

Every extent is right including the two that a declaration alone gets wrong: the three
orderlists, whose `$FF` the tune never reaches and whose declaration therefore stops one
byte short, and `p00`, a **one-row** pattern (`5f ff`) the song never plays and no
declaration covers at all. Both come out of the compare immediate.

**What it does not buy, measured.** `arr` stays **0** and the trigger floor stays
**81099 observed fires on Commando**, and the reason is a property of the primitive
rather than of the recovery:

- Commando's pattern bytes are **note and instrument indices**, not register bytes, so
  §4g's rule — a SID write whose byte *is* the declared pattern byte — never sees them.
  What they index is the pitch table and the instrument bank, whose rows §4b already
  recovers off the machine's own read cell.
- Chaining those rows to the pattern needs the row a `SELECT` reads to be another
  generator's emit **within one frame**, and `_run` is node-major: a node emits all of a
  frame's values before any later node reads `cur`. A driver that reads three pattern
  bytes and steps its cursor three times inside one frame cannot be interleaved that way,
  so a pattern-fed row generator would read the cursor's *end-of-frame* value. That is a
  property of the evaluator, and it is the wall — not the arrangement's evidence.
- The note-on trigger is a divider whose divisor is the row's **duration byte**, which
  changes per row. `DIV(n)` takes a constant `n`, so the 81099 fires are not expressible
  as a `DIV` at all; a variable-divisor transfer is what that would need, and it is not
  in the primitive.

Those three are the residue this step leaves, stated as what they are rather than as work
this step did.

**The lane claim's coincidence, diagnosed and priced.** On Commando **782 of 48004**
`lane` pitch emits (480 hi, 302 lo) read the instrument bank's `+2` ctrl lane rather than
the pitch table — which is why the rendered `table 0` showed `pitch hi`/`pitch lo` columns
holding the waveform byte. The cause is **not** the row: it is `_tree_tables` admitting
`$5591` for the freq class at all. `_read_bases` resolves a local against **every**
definition of that name in the procedure, and Commando's play code is one procedure that
reuses `a`, `y` and `w` throughout, so the freq class reaches every table any of them ever
read; `eval_src` then reports an unrelated origin cell among the write's sources and
`mem0[cell] == val` agrees by chance. Resolving each store against the definition **live at
it** removes exactly those 782 — and it was built and measured: corpus-wide it costs
**21014 emits over 120 tunes** (`lane` 516986 → 483627, `pw` −14106, `freq` −3444, `filter`
−3464) and improves **none**. So the narrowing is refused and the coincidence is reported
as a number instead: it is a provenance question in `_read_bases`, not in §4b's rule, and
the fix that is available today is 27× more expensive than the thing it fixes.

The reported row count for `table 0` (33 rather than the 13 instruments the block holds)
has the same shape and a different cause: the declaration `$5591` is **263** bytes at the
full duration, not 104, because `datadecl`'s extent runs to the next boundary above the
highest observed read. 263 at stride 8 is 33 records. That is a declaration extent, not a
tracker claim, and the rendering states the lane it was given.

### Commando, whole tune: the accumulator as an object

11750 frames, law PASS throughout, against the same tune before this step:

| figure | before (#118) | after |
|---|---|---|
| values generated | 105048 / 117031 = 89.8% | **114339 / 117031 = 97.7%** |
| residual (replayed) | 11983 | **2692** |
| pulse plane | 22272 / 31563 = 70.6% | **31563 / 31563 = 100%** |
| voice 2 pulse (lo, hi) | 0 / 2655, 0 / 1116 | **2655 / 2655, 1116 / 1116** |
| observed sweep seeds (`seed`) | 1221 | **0** |
| sweep runs / objects | 597 runs | **4 objects, 3 constants** |
| nodes | 1311 | **121** |
| trigger streams | 642 | **32** |
| fires generated | 15000 / 99294 | **15000 / 105944** |

**Every pulse write is now produced and none is replayed**, and the whole of that move is
§4l's. The three readings it replaces were each a *register-first* reading of one
object: a note-on copying `ins_pwl:ins_pwh` to `$D402/$D403` was a fresh run at an
observed byte, a sweep step on another voice was a different run, and an instrument
whose declared rate byte is zero — so whose accumulator provably never steps — was
neither. Read as one object per instrument row, the three are one node's emits, and the
1221 seeds are one declared post-init image apiece.

**Observed fires rise 99294 -> 105944 and the generated count does not move.** That is
the arithmetic §0's `trigger_share` row is about: every write the layer takes out of
`RAW` needs a trigger it did not need before, so a *share* of the fires falls exactly
where the value domain improves. `universality.json` therefore floors
`Coverage.triggers[0]` — the generated count — and records the share as context.

**The song is unchanged and re-verified byte for byte**: 3 orderlists (65/64/124 bytes
including their `$FF` terminators), 32 patterns, 571 rows, **0 divergences** against Rob
Hubbard's own source. §4l touches the value domain only.

### Commando, whole tune: the accumulator a note reloads

11750 frames, law PASS throughout, against the same tune after §4l:

| figure | after §4l | after §4m |
|---|---|---|
| values generated | 114339 / 117031 = 97.7% | **115223 / 117031 = 98.5%** |
| residual (replayed) | 2692 | **1808** |
| pitch plane | 51478 / 54170 = 95.0% | **52362 / 54170 = 96.7%** |
| observed sweep seeds (`seed`) | 0 | **0** |
| whole-tune recovery | 173s | **11s** |

Two readings move it, and the whole of the move is in the pitch plane.

- **§4m, the drum (644 writes).** `ctr_551A[x]` is plain per-voice RAM, so §4l's
  containment rule cannot see it; every store to it is either the text's own
  `dec` or a reload from the declared pitch table's high lane at the note's row, which is
  what makes it program text plus declared data all the same. One node per (cell, seed
  lane) — a `SELECT` over the lane, a `RAMP` seeded from it, a `HOLD` per read — carries
  the whole tune. Voice 2's object reloads from *two* declarations over the run, and
  keying the object on the lane rather than on the cell is what keeps both.
- **The spilled row (240 writes).** The arpeggio reads `m_5428[(note + 12) * 2]`, and on
  the top octave that index runs past the declared 96-entry table into the declaration
  beyond it. The store statement names the base, one 6502 index reaches 256 bytes from
  it, and the byte is the declaration the address landed in — the same
  `mem0[src] == val` rule every other lane emit passes.

**The song is unchanged and re-verified byte for byte**: 3 orderlists (65/64/124 bytes
including their `$FF` terminators), 32 patterns, 571 rows, **0 divergences** against Rob
Hubbard's own source, including the one pattern that source ships commented out and whose
bytes its pointer table still names. §4m touches the value domain only.

**What is left, and the mechanism of each write.** The residual is 1808 writes, every one
of them freq, and each is one of three program-text sites (attributed by watching each
SID store's own execution, `frameval.eval_watch`, at the full 11750 frames):

| site | what the 6502 does | residual |
|---|---|---|
| the arpeggio, spilled onto live state | `m_5428[(note + 12) * 2]` where `asl` has taken the row past every declaration | 1110 |
| the pitch bend | `nfq = m_551D[x] ± (m_5520[x] & $7E)`, its high byte `ctr_551A[x]` taking the carry | 696 |
| the note-on, spilled | `m_5428[note * 2]` at a row past every declaration, frames 0 and 1 | 2 |

- **The arpeggio's 1110 are not a provenance gap** — the earlier reading of them (§0) was
  wrong. The origin map names exactly the cell the read resolved to, and that cell is the
  driver's *own per-voice state*: `pos_54EF` the pattern row cursor (576 writes),
  `ctr_5510` the pulse direction counter (330), `idx_54FE` the instrument index (96),
  `pos_54EC` the orderlist cursor (72), `idx_54FB` the note index (36). Every one is live
  RAM whose post-init image is `00` and whose value at the read is not it, so **no
  declaration reaches them**: what would reach them is a generator per walked cell, which
  the layer does not have. The note-on's 2 are the same site one table over (`m_54F8`).
- **The bend's 696** are §4m's object at a step the object cannot name. The cells are the
  16-bit pair `m_551D[x]`:`ctr_551A[x]` — paired by the carry the text takes, not by
  adjacency — and the step is `m_5520[x] & $7E`, a **pattern byte**: the origin map does
  not reach it (the pattern is read through a zero-page pointer, so `frameval._addrs`
  cannot name the address and `prog.pinned` is empty here), and only §4j's chart walk
  does. Measured against that walk, the step *is* declared: voice 1's bend magnitudes
  `78, 62, 80, 40, 48` are exactly `& $7E` of its song's own `$5520` lane
  (`CF BF D1 A8 B1`), and the sign is bit 0 of the same byte. What §4m is owed to reach
  it is a lane keyed by the cell a pattern byte flows into (`_row_walk` already carries
  the destination set) and a `RAMP` whose *step* is a node, exactly as its seed now is.

### Commando, whole tune: residual 0

11750 frames, law PASS throughout, against the same tune after §4m:

| figure | after §4m | after §4n | after §4o |
|---|---|---|---|
| values generated | 115223 / 117031 = 98.5% | 116335 / 117031 = 99.4% | **117031 / 117031 = 100%** |
| residual (replayed) | 1808 | 696 | **0** |
| pitch plane | 52362 / 54170 = 96.7% | 53474 / 54170 | **54170 / 54170 = 100%** |
| observed sweep seeds (`seed`) | 0 | 0 | **0** |
| nodes | 142 | 176 | **194** |
| fires generated | 15000 / 117031 | 15000 / 125585 | **15000 / 128837** |

**Every write of the tune is now produced and none is replayed.** The two readings that
close it are one object rule apiece. §4n's is that a driver cell whose *every* store the
program text names is §4l's object one step further out — the three seeds it adds are a
program immediate, a step a guard on the cell's own value pins, and a byte the song copies
in — and that the cell a read landed on is the machine's own address bus. §4o's is that a
16-bit accumulator the song *bends* needs its step read where its seed is read, which is
`RAMP`'s last scalar parameter taking the form the divisor, the rows and the seed already
had.

Both are checked the same way every object here is: the walk is regenerated from the
declaration and the text, every read is compared with the byte the register took, and one
contradiction refuses that object whole. **Nothing is seeded from an observed byte**:
`Coverage.classes` reports `seed` 0, and the shallow classes are unmoved at 10108 program
constants — the same waveform and ADSR immediates §4e and §5 already reported.

**The song is unchanged and re-verified byte for byte**: 3 orderlists (65/64/124 bytes
including their `$FF` terminators), 32 patterns, 571 rows, **0 divergences** against Rob
Hubbard's own source, including the one pattern that source ships commented out. §4n and
§4o touch the value domain only, and the recovered `Graph.charts` is byte-identical to the
one #120 shipped.

**Corpus-wide** (649 tunes at 200 frames): `law` 649/649, interpreted `794952` ->
`892071` over **348 tunes** with **none generating less**, every tune's `total` unmoved,
`seed` unmoved at `7479`, and generated fires `1140` -> `1191`.

## 8. Known limits

- The arrangement (§4g) needs **two** things the program text must supply, and this
  entry used to read "no corpus tune supplies both": a pattern row that is a cell the
  play code only walks (42 of 366 resolved deref sites) and an orderlist position that is
  another (15 of 366), both at once **zero sites**. **That census was taken through a
  program-wide local map and is wrong** (§0, §4j): resolved against the locals live at
  each site, Commando supplies both and its whole song comes out. The counts below stand
  as what the old reading measured, so the orderlist position is taken off the machine's own address
  bus rather than generated, and the layer ships as a pattern generator only. A row read
  off the observed stream — worth 54557 emits — and an orderlist read off the blocks the
  run visited are both refused outright. What the route does **not** carry is a *relative*
  index: an orderlist transpose shifting a pattern's note index is `op(row, delta)` in the
  index domain, which is `Rel` moved one domain over. It was not added on a prior — the
  oracles measured what an absolute-only index refuses first, and the answer is **6953
  emits over 23 modules of 141**: GoatTracker's `Transpose` 1690 (5 of 71, 2.02% of its
  freq plane), SID-Wizard's `Transpose` + `octave_shift` 4738 (15 of 64, 6.99%) and
  DefMON's `TR` with bit 7 clear 525 (3 of 6, 7.29%). The orderlist is the same object
  seen once more: `base(entry) + counter` is two index sources where `Node(j)` names one,
  which is why the three editors' orderlist entries are represented structurally and not
  as a generator. One extension closes both, and it has landed (#102): a row may now be
  `Rel(op, delta, base)`. **The recovery still reaches zero with it** — a relative row
  composes an orderlist entry with a pattern row, and **zero** corpus sites have both of
  those in program text — and so, measured since, **do the editors' own graphs**: a
  census by `(transfer, route)` over 12 GoatTracker tunes, 12 SID-Wizard modules and 6
  DefMON tunes finds **0** `SELECT[rel]` nodes on any side. The 6953 is a *refusal*
  count, not a use: `gtoracle._patt_src` prices a shifted row as `refused_transpose` and
  returns nothing, and `dmoracle._dm_src` passes it a 0/1 flag rather than DefMON's `TR`
  amount. Emitting it needs the shift carried into the stream key (one stream, one
  shift), `_feeder` checking `column[s] + shift == row`, `_arranged` building
  `Rel("ADD", Node(counter), Const(shift))`, and DefMON computing an amount it does not
  compute today — three mappings, not a rename, and it moves `graph_diff` by
  construction. **Those three mappings have landed** (§0, §6's last table): all three
  oracles emit `SELECT[rel]` — SW 60 nodes over 15 modules, GT 20 over 4 tunes, DM 7
  over 3 — laws green. **The recovery still emits zero**, for the reason above: no
  corpus site carries both index sources in program text.
- **The transfers we generate and the transfers the editors need are not the same set**,
  and the census says so in both directions. `DIV -> fire` is used by **no editor's own
  song** (0 nodes in GT, SW or DM) and is the one transfer the recovery generates that
  nothing else does — 4 nodes, 304 fires over 4 tunes of 649 (§4d, §4i); the editors'
  tempo chains are real dividers, but their oracle graphs carry the trigger domain as
  observed `EDGE` floors, so building their sequencer chains as `DIV -> RAMP -> SELECT`
  is the oracle-side half of §7.4. The relative route's `Const` and `Node` bases are the
  same inversion one level down: 0 nodes on any editor's side, 36 and 4 emits on ours.
  `SELECT -> plane+mask` runs the other way — 68 nodes theirs against 676 emits ours —
  and so, since the universal-layer step, do `SELECT[rel]` (87 editor nodes, 0 ours) and
  the `Pair` route (3538 editor nodes over GT and SW, 0 ours: the recovery's §4c sweeps
  are byte-wide and its 16-bit candidates are a provenance question first).
- A pitch table whose lo/hi block is never read at a constant base cannot be
  declared, so it cannot be recovered here — the one tune in the 60-tune sample
  that loses its table, `MUSICIANS/A/Aegis/2008.sid`, has no read site at its hi
  block `$0E96` at all.
- Where a table's declared extent is bounded by the next read base or the index
  cap, an over-long reading of it is not available — the declaration is what the
  play code can index, and the tracker does not run past it.
- For tunes whose freq is streamed or glide-computed with no static table at
  all, `pitchind.py` induces the ET lattice from the *observed* freq stream and
  reports a note lane with its fit; that is a diagnostic, not part of this law,
  and it never feeds a generator.
- ctrl/ADSR that reaches the register through a **computed** value stays
  residual: the byte arrives from an expression rather than a load, so there is
  no cell to name. Over the 60-tune sample that was 7643 ADSR emits and 15957 of
  30025 ctrl emits, measured before the origin rule of §6. Neither staging in a
  RAM register mirror nor staging in a register is a refusal class any more —
  following the copy back to the bank is a dataflow step and it is frameprog's,
  so a staged bank read arrives here already declared (docs/frameprog.md §1.4).
  What still refuses is a byte whose origin is outside every declaration, or
  inside one but no longer equal to the snapshot (#61), plus the emits refused
  for write order — 23631 of them, whose stream key straddles the residual (§5, §6).
- The gate arm needs a row: a voice that never reads its waveform from a
  declaration has nothing for the gate to ride and stays residual — now for those
  writes only, not for the whole register.
- A sweep whose step the origin map traces to no declaration stays residual, and that
  is still most of them (§6): the `RAMP` is refused rather than seeded from an observed
  delta, since a step fitted to the output would pass the law while explaining nothing.
  A `RAMP` whose run predicts no emit — one write, or a step of zero — is refused for
  the same reason, and one stepped emit without a declared origin refuses its whole
  run rather than being dropped from it. The seed is the observed byte and is counted
  shallow. Only the wrapping bound is implemented; a triangle sweep that turns around
  at a declared bound needs a transfer this primitive does not have. **This read "and no
  tune in the corpus reaches that limit before the step blocks it", and that is false** —
  Commando does, with a declared step (`$E0`) and two declared turning bounds (`$0E` up,
  `$08` down) in its own walked text (`out/Commando.frameprog.txt:373-421`), over a
  12-bit accumulator whose halves are the `$5591` bank's `+0`/`+1` lanes. It is worth
  **23071 of that tune's 25763 replayed writes** and it is not built here.
- **§4m's object named one step and one seed lane, and this entry has been paid** (§4o).
  An arm whose delta is a *pattern* byte left the object undefined until the next reload;
  a song lane keyed by the destination cell and a `RAMP` whose step is `Node(j)` walk it
  through, and Commando's 696 bend writes come out. What the entry did **not** name is
  the pairing: the two halves are three cells apart, so the carry the text takes is the
  only thing that binds them (`_carried`). What is still owed here is a *turning* step —
  a bend that reverses at a bound rather than being reloaded — which no cached tune has.
- **A read that spills onto live state had no declaration to reach, and this entry has
  been paid** (§4n). The arpeggio's `(note + 12) * 2` runs past the pitch table on the top
  octave; 240 of those rows land in the next declaration and **1110 land on the driver's
  own per-voice cells**. A driver cell whose every store the text names is an object the
  graph walks, and `HOLD` then reads what that object holds. What is still owed is a cell
  some writer the text does *not* name touches: the object is undefined from there to its
  next reload, and nothing is claimed across it.
- The step is queried at the *play* phase's copies. A parameter a table supplies at
  **init** and the play code only reads back arrives as a RAM cell with no origin, so
  it names no declaration; that is a large part of what §6's refusal table leaves.
- The filter plane is read by §4b and by nothing else: 77% of it loads a RAM cell or
  computes the byte outright, so no declaration names it (§6). It is refused rather
  than reached by an `imm` read over the program's constants — that would take
  34177 emits without explaining an index, and $18 in particular takes many program
  constants, so it would be the observation choosing between them.
- On the lww planes the *table* is transliterated but the *index* is not: the tree
  names `m_5429[t5]`, and `t5` is a live state value no static reading yields, so
  `frameval.eval_src` still recovers it. That index becomes explained when the
  arrangement does (§7.4), not before.
- A divisor is refused unless the play code declares it (§4d): a period fitted to
  the observed fires, a reload out of a RAM cell whose post-init byte merely agrees,
  and a divisor of one are all refused, and §6 measures each refusal's cost. The
  primitive **now has a phase field and the arrangement supplies it** (§4i): the phase
  is `(mem0[counter] − 1) mod n`, the declared byte init left in the divider's own
  counter, and `n-1` is what that reading gives for a counter seeded at its reload or
  never staged at all. A phase read off the first observed fire is still refused.

  **This entry used to price the field at 4961 fires and that was 25× too high** (§0).
  Measured with the divisor rule still in force: of the 1101 strictly periodic streams
  only **17** have a period some reload declares, **9 of those (196 fires)** need a phase
  other than `n-1`, and the declared counter recovers **4**. The domain's real limit is
  one level up — **989 streams (13608 fires) have a period no reload declares**, and 95
  more only as the product of two declared divisors. The cascade `DIV -> DIV` is
  expressible since the universal-layer step (an event-clocked `DIV` divides its input
  ticks), and **0 of the 95 pass its evidence rule** — the inner dec watched executing
  exactly on the outer's ticks, both phases declared (§0). A counter's own `AND`-modulus
  would be the other source and it has **no population**: zero of 2198 walked cursors
  step once per frame.

  **The three-editor verdict on that field, settled.** This entry used to price it as
  "a per-stream parameter fitted to the output". DefMON refines that and the refinement
  is the useful part: DefMON is the first editor whose divisor is unambiguously
  *declared song data* — `sidtab_dl[y]` holds a cascade row for `dl + 1` frames, and
  that byte predicts **817 of 1329** advances across its whole corpus
  (docs/dm-oracle.md §4.1). So the divisor is not the problem in any of the three
  editors: GoatTracker's tempo is in the orderlist, SID-Wizard's in `speed`, DefMON's
  in a table. What breaks is everything after it. **36 of DefMON's 40 periodic chains
  run at divisor 1**, which `DIV(1)` refuses for the reason above, and of the four left
  only **2** sit where `DIV(n)` fires — and they sit there by coincidence, because
  DefMON arms a cascade from a `PatternEvent`'s gate flag at an **arbitrary frame**.
  GoatTracker primes `mt_initchn` to 1 and SID-Wizard pre-warms `speed_counter` to 2;
  two editors prime a counter at init, the third arms it from a pattern, and all three
  land off `n-1 mod n`. The phase is therefore **the arrangement's**, not a per-stream
  parameter: it is *where the song started the clock*. A phase field fitted per stream
  would be exactly the refusal §4c, §4d, §4e and §4f each make in their own domain, and
  a phase field supplied by the arrangement is not a separate step at all. **§7.4 and
  this field are one problem**, which is why §4i builds them together — and having built
  them, the field is worth 4 fires and the arrangement 15 emits.
- A cursor is refused unless the play code only walks it (§4i): a cell some writer
  reloads is not a `RAMP`, and 217 candidate cursors are refused for that. A row run the
  cursor's own beats do not reproduce is refused whole, which is 58436 rows against 15
  taken; and the cursor's trigger is its **own step statement**, never the stream that
  reads it — that reading would take 21 rows over 3 nodes and is refused as a trigger
  fitted to the row run.
- The tree walk is per procedure (locals) plus a program-wide staging hop
  (`origins`), so a byte staged across a procedure boundary or through the stack is
  named by no store site. On ctrl/AD/SR the provenance search covers it — 15648
  emits' worth; on freq/pw there is no such fallback and those writes stay residual,
  which is 5693 emits a blind search would have taken.
- A masked route needs a mask the program text names (§4e), and almost no store site
  names one: 699 emits of 142154 written with only some bits changed. The mask is
  refused where the store `OR`s two variable terms — the shape a routing byte built in
  RAM takes, and the reason `$17` recovers **zero** — and a partition whose fields no
  declaration holds is refused whole. The bits no generator of a register owns are
  emitted as zero, so a byte with a bit nobody owns cannot pass the law and stays
  residual; `_check` refuses overlapping masks outright. The order-preserved section
  takes no masked group at all, which costs 23 emits on 2 tunes.
- A relative route needs a base the program text names (§4f), and a 6502 driver rarely
  names one: 316 emits, on 6 tunes of 649, against 4650 the same route takes from two
  editors' own models. `Prev` needs a cell the text stores the register's own value
  into, `Node(i)` needs the write's own read cells to reach the base declaration
  (35242 emits fail exactly there), and `Const(c)` needs the base in the statement.
  A delta of zero, a delta at a `mut` offset, and a declared lane used as the minuend
  over the plane's own value are each refused, and a delta back-computed as
  `emitted − previous` — worth 29559 emits — is refused outright. The order-preserved
  section takes no relative route at all, which costs 2198 emits. What the route does
  **not** carry is a base in the *index* domain: DefMON's `TR` shifts a note index
  rather than a byte, and that is §7.4's, not this route's.
