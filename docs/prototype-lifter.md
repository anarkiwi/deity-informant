# Prototype: the lift from certified artefacts to a trackerprog

The exemplar [prototype-trackerprog.md](prototype-trackerprog.md) §9 says is
missing: a **lift** that produces a trackerprog rather than a hand reading of
one. It answers [trackerprog-backlog.md](trackerprog-backlog.md) **B6** (is the
schedule recoverable?) and **B7** (lower the tick, do not classify it, then
recognise what T1 names) on three tunes, rendered by
`deity_informant/trackerprog/universal.py` and certified against each tune's own
player on the PcodeVM:

| tune | schedule | hints | refusals | certificate |
| --- | --- | --- | --- | --- |
| Commando song 1 (Hubbard) | derived, one datum from the hand's (§2.1) | **0** | 4, named (§3) | **0 divergences over 11,780 ticks** |
| *Guldkornekspressen Intro* (JCH V20) | derived, four datums from the hand's (§2.1) | **0** | 1, named (§3) | **0 divergences over 2,401 ticks** |
| *Je suis Linus le salaud* (GoatTracker 2) | derived, five datums from the hand's (§2.1) | **0** | 13, named (§3) | **113 of 8,236 ticks; the 114th diverges (§5)** |

No object has a `program` key. **All three of Commando's T1 accumulators land as
§5 records** (§2.3), **none of JCH's five and none of GT2's four** (§4); §7
states what the pass did not change and §8 what each family cost and what is
left. §9 is the coverage of the three, one row a family.

```
tools/tuneprog_trackerprog.py --out <out>/commando-song1 \
    --sid $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid --certify
tools/tuneprog_trackerprog.py --out <out>/jch-guldkorn-intro \
    --sid $HVSC/MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid --certify
tools/tuneprog_trackerprog.py --out <out>/gt2-je-suis-linus \
    --sid $HVSC/MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid --certify
```

The lift reads `tuneprog.T2.json` at the horizon it was computed over, so an
output directory whose T2 was written for a prefix lifts that prefix. JCH's and
GT2's are recomputed at their certified horizons first, and each object is the
same one either way (`trackerprog/build.artefacts` computes the same plane in
memory):

```
tools/tuneprog_score.py --out <out>/jch-guldkorn-intro --calls 2401
tools/tuneprog_score.py --out <out>/gt2-je-suis-linus --calls 8236
```

Contents: 1 the source · 2 the method · 3 the hints · 4 coverage ·
5 the certificate · 6 against the hand objects · 7 the limits ·
8 what each family cost, and what is left · 9 the three families.

---

## 1. The source

Commando (Hubbard), song 1, from the certified artefacts alone —
`tuneprog.S4.json`, `tuneprog.S6.json`, `tuneprog.T0.json`, `tuneprog.T1.json`,
`tuneprog.T2.json`, `certificate.json`, and the post-init image the S4 program
carries. Neither the trace nor the binary is read for anything but the
horizon's own replay, so family knowledge cannot reach the output (§6).

It is the proposal's tune and it earns the place: T0, T1 and T2 all lift it with
no refusal, it has no shadow, its tick has three commit points
(`prelude commit row commit machine`), so it exercises B6's schedule question,
and the hand transliteration
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)) is a
ready oracle to diff field by field.

*Guldkornekspressen Intro* (JCH V20), song 1, is the second, and it earns its
place by having none of that shape: its row clock is a counter the **tick**
steps outside the voice loop, its instrument records are eleven column programs
read at cursors, and its chip writes go through a per-voice image the same tick
sends. Two things it was taken for are not what they looked like:

| expected | what the artefacts say |
| --- | --- |
| a tick of several procedures ([§8](#8-what-each-family-cost-and-what-is-left) item 1 at #347) | the certified S4 tick is **one** procedure of 142 blocks. `p_10E9`, `p_1409`, `p_1616`, `p_14F7` and `p_14FD` are the *presentation's*; what the S4 leaves in their place is a **join** — one block reached from several sites — and that is what the lowering must state (§2.2) |
| `meta.shadow.registers`, a flush of the image the last tick left | the eleven `sid_image` regions are written **and sent inside the same tick**, by the voice's own last blocks (`p_1616`, jch.md:648-659), so the lowering's ranked `sets` rows already state it and no `shadow` is derived. §3.1's flush is the *wrapper's*, which only [*Knob at Night*](prototype-jch-trackerprog.md) §4.1 carries |

*Je suis Linus le salaud* (GoatTracker 2) is the third, and it is the one whose
tick is **several procedures**: the certified S4 has 14, and the tick's own 27
blocks call `p_11A4` (204 blocks, itself calling eight one-block procedures)
three times and `p_1130` three times — once a voice, unrolled in the source. It
is also the first with a **register file**: no write reaches the chip on the tick
that makes it, and the tick's first act copies 25 bytes of `sid_image` to
`$D400`. Its `init` only schedules, so the first `play` call runs the reset and
spends its own tick. Three things it was taken for and three it turned out to be:

| expected | what the artefacts say |
| --- | --- |
| a voice loop the tick already has | the tick has **no** loop over the voices: the run of three calls **is** the pass, and the object's loop is that run rerolled by the one constant its arguments step (§2.5) |
| the row clock told from a divider by comparing against a **cell** | GT2's clock is compared against a cell too — the `gatetimer` its fetch runs ahead by — so a divider is now the counter compared with the cell **its own reload reads**, which is what §3.6's *rate is a reload plus one* already said (§2.1) |
| `state0.prologue` as a family's own datum | it is derived: the blocks the tick runs on its **first** call alone, where that call runs no block of the voice loop, are a command every voice takes on a tick of its own (§2.5) |

## 2. The method

### 2.1 B6: the schedule, derived

`trackerprog/schedule.py`. The hypothesis of B6 holds as stated, and this is the
procedure:

| datum | how it is derived |
| --- | --- |
| the voice loop | the natural loop of the tick proc whose body holds the fetch regions `region.fetch` found |
| `voice_order` | the loop index's own start (a chain of copies to a constant in a pre-header) and its latch step |
| `commit_order` | `emit.commit_order` over T0: the `ctrl`/`ad`/`sr` write sites in pc order |
| segments | the loop body in **reverse postorder**, cut at the fetch regions: what precedes them, the regions themselves, what follows |
| `meta.tick` | one phase per segment, with a `commit` after a segment that holds an edge write |
| `tempo.cell`, `step` | the counter a guard on the fetch's own path reads and a store steps by a constant, of the candidates the one whose *own* step stands under fewest guards, and of those the one the most terms of that path read. A counter the voice loop steps is a voice cell; one the tick steps outside it is a scalar the whole tune keeps, declared as one cell per voice that every copy enters equal — which is what §3.6's per-voice `tempo.cell` needs of a tick-level clock |
| `tempo.boundary` | that path's own guard terms, over the object's cells: **a guard list**, not one term |
| `tempo.reset` | §3.6's clauses: every other store to the counter the **tick** makes outside the voice loop, each under its own guard path. A counter a *voice* steps is refilled by a row of that voice's phase, as Commando's is, and the clock states no clause |
| `tempo.rate`, `phase` | the counter a term of that path compares with the cell **its own reload reads**: that is the divider, its reload plus one, and the residue class the post-init image admits. A counter the path compares with some *other* cell — the lead a fetch runs ahead by — is the clock itself and the compare is its boundary |
| `row_consumes_tick` | whether the fetch's exit still reaches the first block of the machine segment |
| stream `rank`s | the order the segments' rows and accumulators stand in, which is program order |
| `meta.shadow` | §3.1's register file, where T0 states one: every write that does not reach the chip names the image it lands in and the `delta` from it to `$D400`, and the one write that copies the whole file is the flush. The registers, in the order the flush sends them, are the order one tick of the certified program sends them in (`trackerprog/shadow.py`) |
| `state0.prologue` | the blocks the tick runs on its **first** call alone, where that call runs no block of the voice loop: a tune whose init only schedules runs its reset then and spends the tick (§4.7 of [prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)). The guard that says *this is the first call* is the phase's own and no row of it repeats it |

**Commando's** derived schedule, datum for datum against the hand's:

| datum | derived | hand | |
| --- | --- | --- | --- |
| `voice_order` | `[2, 1, 0]` | `[2, 1, 0]` | same |
| `commit_order` | `(ctrl, ad, sr)` | `(ctrl, ad, sr)` | same |
| segments | `prelude` 8 blocks · `row` 17 · `machine` 59 | — | — |
| `meta.tick` | `{stream: prelude0}` `commit` `row` `commit` `machine` | `prelude` `commit` `row` `commit` `machine` | **differs by one datum** |
| `row_consumes_tick` | `true` | `true` | same |
| `tempo.cell` / `step` | `timer` / `−1` | `rowsleft` / `−1` | same cell, the S6 name |
| `tempo.rate` / `phase` | `3` / `0` | `3` / `0` | same |
| `tempo.boundary` | `((phase − 1) & $FF) & $80 != 0` | `rowsleft >= $80` | the same guard, over the clock's own step |
| `tempo.early` | none | `rowsleft == 0 and tied == 0` | the lift needs none (below) |

**The one named difference.** The first segment is a `{stream}` phase and not
`prelude`. `Player.phase_prelude` gates on `stepped`, and the lift does not
spend the tune's divider into `meta.tempo.rate` alone: it keeps the counter as a
cell as well, so the segment's own guards already say when it runs and the phase
must run on every tick. That also makes `tempo.early` unnecessary — the guard
the hand puts there is a term of the segment's rows. Both render the same
writes; the derived form is the more general one, because a family whose
pre-row segment is not an instrument lead-in has no `prelude` to name.

**JCH's**, datum for datum against `tools/trackerprog_jch.py`'s own `meta()`:

| datum | derived | hand | |
| --- | --- | --- | --- |
| `voice_order` | `[2, 1, 0]` | `[2, 1, 0]` | same |
| `commit_order` | `(ad, sr, ctrl)` | `(ad, sr, ctrl)` | same |
| segments | `prelude` 5 blocks · `row` 37 · `machine` 79 | — | — |
| `tempo.cell` / `step` | `c1746` / `−1` | `rowclock` / `−1` | **the same cell**, `$1746`: S6 names it `phase`, which §5 answers itself, so the object names it by its address |
| `tempo.reset` | `[{when ((phase − 1) & $FF) & $80 != 0, sets @c1746 := 3}]` | `[{when rowclock >= $80, sets @rowclock := speed}]` | the same clause, over the clock's own step; the speed is the byte the post-init image holds, which the play never writes |
| `tempo.rate` / `phase` | `1` / `0` | none | same: the clock has no divider |
| `tempo.boundary` | `c1746 != 0` · `c1746 == 2` · `timer_3 == 0` | `rowclock == 0` | **differs**: the lift's `row` phase is the **fetch** region, so its boundary is the fetch's own guard — where the hand's boundary is the commit and the fetch is its `early` |
| `tempo.early` / `fetch` | none | `early [rowclock == 2]` | the same datum, in the boundary |
| `meta.tick` | `{stream: prelude0}` `row` `machine` | `fetch` `prelude` `row` `machine` | **differs**: the lift has one row program and no `stage`, so the phases the hand splits at the fetch are its `row` and the rest |
| `row_consumes_tick` | `false` | `[[keys != 0]]` | **differs**, and both render the same writes over the horizon: the hand's row phase is the *commit* and spends the tick the note-on returns from, the lift's is the **fetch**, which the source does not return from. §8, #348's item 2 |
| `meta.shadow` | none | none (Guldkorn) | same: the write-out is the voice's own last rows, ranked last |
| `row_command` | `spent` | `spent` | same |

Four datums differ and one mechanism is behind three of them: **the lift's row
phase is the fetch region**, because the fetch is what the score materialises
(§2.4). The hand reads the row at the fetch and *commits* it two clock steps
later; the lift has no `stage`, so it consumes the row where the source read it.

**GT2's**, datum for datum against `tools/trackerprog_goattracker.py`'s own
`meta()`:

| datum | derived | hand | |
| --- | --- | --- | --- |
| `voice_order` | `[0, 1, 2]` | `[0, 1, 2]` | same, and it is the run of calls' own step: the arguments go 0, 7, 14 and a voice's copies stand 7 apart |
| `voices` / `cycles_per_tick` | 3 / 19,656 | the same | same |
| segments | `prelude` 182 blocks · `row` 37 · `machine` 1 | — | — |
| `meta.shadow` | 25 registers, `mode_vol` first and `v0.freq_lo` last | 25, descending `$D418`→`$D400` | **identical**, and derived: T0's own image and the order one tick sends it in |
| `state0.prologue` | 91 rows, the first call's own reset | 8 `sets` and 3 `point`s | **the same datum**, lowered rather than read |
| `tempo.cell` / `step` | `timer_2` / `−1` | `rowclock` / `−1` | **the same cell**, `$148E`: S6 names it by its role |
| `tempo.rate` / `phase` | `1` / `0` | none | same: the clock has no divider, and the compare against `gatetimer` is its boundary and not a rate (§1) |
| `tempo.boundary` | `b110D & $80 != 0` · `phase != 1` · `timer_2 == b14BA` | `rowclock == 0`, `early [rowclock == 2]` | **differs**: the lift's `row` phase is the fetch, so its boundary is the fetch's own guard — the play bit, the step that is not the reload's, and the lead `b14BA` (the hand's `gatetimer`) states |
| `tempo.reset` | none | 2 clauses (the funk tempo, then the plain reload) | **differs**: GT2's counter is a *voice's*, so its reload is a row of that voice's phase and the clock states no clause (§2.1's rule) |
| `commit_order` | `(ad, sr, ctrl)` | `(sr, ad, ctrl)` | **differs, and unobservable**: with a shadow every write lands in the image and only the flush reaches the chip, so `commit_order` orders nothing the certificate can see |
| `meta.tick` | `{stream: prelude0}` `commit` `row` `commit` `machine` | `row` `commit` `machine` `fetch` `prelude` `{stream: exit}` | **differs**: the lift has one row program, no `stage` and no instrument `prelude`, so the phases are the segments its own RPO cut |
| `row_consumes_tick` | `false` | `[[keys != 0]]` | **differs**, as JCH's does and for the same reason: the lift's row is the fetch, which the source does not return from |
| `row_command` | `spent` | `held` | **differs**: the lift inlines each visit's own bytes rather than naming the command a voice keeps (§2.4) |
| `pitch` | base 0, 91 entries | base 0, 96 | **differs**: T2's reach is 91 and the five entries past it are `beyond` words of the bytes the image holds (§2.3) |

Five datums differ; `commit_order` is unobservable under a shadow, two are the
row phase being the fetch (as on JCH), one is the counter being a voice's own,
and one is the tuning's reach.

**One thing the RPO segmentation says that the object does not need.** Taking
B6's rule literally — *maximal* segments between the commit sites — cuts the
machine again at `$5333`, the drum's own gate write, giving a fourth segment.
The object does not carry it: `Player.runstream` makes each row its own **act**
(§2 rule 1), so the writes on either side of that cut are already two acts and a
`commit` between them distinguishes nothing. The rule as run here is therefore
*segments at the fetch regions, commits at the edge writes that end them*.

### 2.2 B7: the tick lowered

`trackerprog/lower.py`, `flow.py`, `vocab.py`, `cells.py`, `build.py`. Every store site of
the certified tick outside the fetch regions becomes a `sets` row of an
`all: True` stream at a `{stream}` phase (D3: the grammar already has this).
One IR block is one row: its **guard path** is the row's `when`, its statements
its `sets` in order, and every SSA temp a named cell of `state0.cells`, one copy
per voice. Nothing is classified; a leaf with no name is refused.

| the S4 IR says | the object says |
| --- | --- |
| `Let(n, e)` | `["@t<n>", <e lowered>]`, a per-voice cell |
| `Store(io, …)` | the register the **store's own address** names (`freq_lo`, `ctrl`, …), the index being the voice map: a family that writes the whole file through one indexed store has one region for all of it, so the region cannot name the column and the address can |
| `Store(ram, base + <voice index>)` | `["@name", …]`, the cell S6's `voice` group names, declared where it names none |
| `Store(ram, const)` | `["#name", …]`, a global |
| a store to the instrument-scoped pulse pair | an `Acc` with `policy: {reload: …}` and no `delta` — §5's own record is the only `sets` target the schema has for `ins.pw` |
| a load of a `const` record column at `stride × ins` | `{"ins": column}`, the record named by what T2 saw the selecting cell **hold** (`visited`), which is the record's own number in one family and the offset it already is in another |
| a load of a region the play never writes, at a cell | `{"tabcell": [T, cell, "b"]}` over a declared stream of that region's own bytes, one row a byte — the general form of a column program (§3.3), and the only form a table read at a cursor has. A read inside a fetch region is **not** one: those bytes are the score's (§2.4) |
| a pinned read S4 records (`ack`, `entry_reg`, `uninit_ram`) | the byte the post-init image holds; one of §8's four external kinds is a refusal by name |
| a load of the tuning at `2·E` | `{"transpose": k}` where `E` is the note cell plus a constant, else `{"transpose": {"sub": [E, note]}}` |
| a load of a cell | `{"cell": name}` / `{"global": name}` |
| `<<` by a constant | one `shl` node, the mirror of the `shr` §5 already has: an operand is named once and never doubled *k* times |
| `a < b`, `a <= b`, `carry(a, b)` | `carry_out` / `borrow_out` on the difference or the sum |
| `a != 0` on a masked bit | `{"bit": [a, k]}` |
| an inner loop | unrolled to the turns the horizon takes, repetition *j* under the condition one more turn is taken, and a `trap` row past the last |
| the condition one more turn is taken | the **disjunction** of the loop's back edges, folded as a join's paths are: two latches that differ in one term and its negation are the one path that term does not decide. Read on the plain cell each turn's own copy writes, it is the chain the turns are — a turn no turn reached leaves the cell the last turn that ran left, and the condition that stopped that turn stops every turn after it |
| a loop index that is a constant per turn | folded into that repetition, which is what makes the presentation's **rerolled sibling copies** lower at all |
| a name an unrolled loop binds and the score supplies | one cell a **turn**: turn *j* copies `t<n>__j`, the constant the score supplied for that turn, into the one cell every reader of the name has (§2.4) |

**A guard the phase's own gate states is the schedule's, not the row's.** Two
terms of a block's guard path are the schedule's and no row repeats them: the
`boundary` the `row` phase runs under, and a **divider's** own compare, which
`rate` and `phase` spend once for the tune (and which a row still states where
its own segment decides it). Everything else on the path is the row's.

**A join is a cell, not a guard.** Control dependence says a block runs *only
if* an edge was taken; it does not say the block is reached no other way, so the
guard path of a block a join carries is one path's, and the reaching condition
is a **disjunction** — which §3.3's one guard shape cannot state. `Lower.plan`
takes the paths: two that differ in one term and its negation are the one path
that term does not decide, so a diamond folds back to the guard path it had
(Commando: **every** join folds, and its object is unchanged to the byte), and
what does not fold the object states as a cell — every path that reaches the
block raises it where that path already stands, and the block's own guard is the
one term that reads it. JCH's tick has **8** such cells: they are what the
presentation's `p_1409` and `p_1616` are, one block reached from three call
sites, and §8's #347 item 1, "a callee is a phase; inline it", in the shape the S4
actually leaves.

**And the plan is one over the segments, not one a segment.** A join's own preds
do not stop at a segment's edge: `p_1409` is reached from the prelude's own last
block as well as from two of the machine's, and a plan taken segment by segment
states a cell the paths of *one* segment raise and leaves every other path
unreached — on JCH, a voice whose row is still held then writes nothing at all
on that tick. Each segment decides its own cells and then **every** path that
reaches one raises it where that path already stands, whichever segment holds
it; the flags are reset once, at the head of the first phase, which every such
row follows (`Lower.planall`). Commando declares no join cell either way and its
object is unchanged to the byte.

**The shift is one node, not 2^*k* copies.** `x << k` had been *k* doublings of
`{"add": [node, node]}` with the subtree copied at each, so a shift by *k* had
2^*k* leaves and the vibrato step's 16-bit rotate stood as 128 copies of one
mask: the largest `sets` expression was **1,282 nodes** on `main` at #346 and is
**26** here, against the hand object's 4 (§7's table). One expression of the 207
still names an operand twice — the two-stage carry an `ADC` leaves in the S4 IR,
3 nodes — and it is the IR's own shape, not the lowering's.

### 2.3 B7: T1's accumulators, joined

`trackerprog/recognise.py` and `algebra.py`. T1 states, per accumulator, the
**cell** it moves, the **sites** that move it, its `delta`, `bound`, `policy`
and `phase`; the lowering names every store by the cell it moves and carries
each assignment's own **site** on the row. The join is those two facts and
nothing else — no family, no expression pasted from a hand tool.

| the join asks | the answer it takes |
| --- | --- |
| which rows are the accumulator's | the rows whose `sets` target the cell `cells.py` names T1's address, in the one stream that holds a T1 site |
| which of them is the delta | the store whose site is a T1 site; any other store of the same cell under a guard the delta's own extends is the `policy`'s |
| the record's `when` | the longest guard list every store row begins with; the delta's extra terms are `delta_when`, minus any term reading a cell the join takes out |
| `delta` | T1's own form over the object's cells (`repeat(step, n)`, `field(cell, mask)`); where T1 names a cell the object cannot, the store peeled as an accumulation on its own cell |
| `produce` | the T0 write whose cells are the accumulator's own, found in the run or in the rows the run's guard still covers; its `sets` leave with the rows |
| `flag` | the one cell a repeated addition writes on every turn and a later row reads: §5's carry. Its `seed` is the value it enters the loop with, **enumerated over the byte** on the set `when` and `delta_when` admit, and refused where that is not one constant |
| a word in two named halves | one 16-bit cell, the halves read and written as §5's `.hi`/`.lo` on the low half's name, listed in `meta.wide` |

The rows the record replaces leave the stream, the stream is cut at the record,
and every rank renumbers over streams and records together. A store the schema
has no `sets` target for is already an `Acc` (`build.acc_of`, `ins.pw`); where
T1 names it, the join restates the assignment as the record it stands for.

**On JCH the join takes nothing, by name.** T1 states five accumulators over the
whole horizon and the join refuses all five: three because **T0 names no write
of its own cells** — their `produce` is the write-out, which the join reaches by
walking the rank order under the record's own guard and the write-out's rows now
stand under a join's cell (§2.2) and not under that guard — and two because
their width is **12**, the pulse pair's own projection, which `recognise.Acc`
admits at 8 and 16 and no other value. Both are §8's #348 items 3 and 4, and
neither is an approximation.

**What lies past the tuning is derived, not read.** Commando's frequency table
is fused with the per-voice arrays (commando-trackerprog §4.2). The lift takes
T2's 80 entries as the tuning and asks `cells.py` what holds each byte after
them: **21 words, every one a named cell, no trap** — where the hand states 12
words with 2 traps.

### 2.4 The score, materialised

The fetch regions are not lowered: they are the score. `record.py` runs the
certified tick from the post-init image with the row segment recorded, and each
entry yields the bytes that fetch read. Those bytes become the row's own
constants — an inline `Cmd` on the event's `arm`, two to four `sets` a row —
and `meta.row` is `{commands}` then the lowered row segment, which reads them
back through the same cells the program used. A visit ends where the fetch steps
the **order** cursor T2 named, so a pattern is a pattern and not a run.

**One value a turn, not one a name.** A visit supplies one constant per *name*
the fetch bound where the row's bytes are the fetch's own statements — Commando's
two to four — and one per **(name, turn)** where they are turns of one loop:
`record.Recorder` keeps the value each turn of the loop bound, counted by the
loop's own header, and the unrolled turn *j* copies its own `t<n>__j` into the
cell the name is. JCH's fetch is a byte walk that turns up to **3** times a
visit (`report.trips`, `L113D_BC`), and each turn now reads the byte it read:
the `$8x` duration prefix is a duration, the `$Ax` an instrument, and the note
byte the one the walk stops on. 12 of the object's 239 cells are turns of that
loop; Commando's fetch binds no name inside a loop and its object is unchanged
to the byte.

### 2.5 A tick of several procedures, and the file its writes land in

`trackerprog/callee.py` and `shadow.py`. #347 item 1 said a callee is a phase and
inlining it is the whole change; on JCH it was not what it looked like (§8), and
on GT2 it is, with two facts beside it.

| the S4 IR says | the object says |
| --- | --- |
| a `Call` the tick reaches | the callee's blocks, spliced where the call stands: every name and label under the call site's own prefix, each parameter register bound to the argument, and each `Return` the `goto` of what follows. The pass repeats until the tick holds no call |
| a **run** of calls to one procedure whose live arguments agree but for one constant that steps | **one** copy of the callee inside the loop that constant closes. It is derived from the IR alone: over the parameters the callee reads, a column of constants that steps is the index, a column each call takes from the one before it is carried, a column every call agrees on is invariant, and any other column refuses the reroll. A name an inner call left and something outside the run reads refuses it too |
| a store whose address is a region T0 names an image of | the register that offset names — `emit` deposits it and the flush sends it — and a 16-bit one the pair `shadow.freq` / `shadow.pw` §5 reads as one cell |
| the blocks the first call runs and no later call does | `state0.prologue`, a command every voice takes on a tick of its own |
| a value **every** copy of a per-voice cell takes in one block | one `sets` target `*name`, §3.6's `all` where a row states it. A copy that is neither the committing voice's nor one of a full set is no cell of the object and is refused |

GT2's tick is 27 blocks and 274 after the pass, of which **two** are rerolled
runs (`report.inlined`): `p_11A4` three times and `p_1130` three times, each
stepping its argument by 7 — which is the stride a voice's copies stand at, so
the rerolled loop **is** the voice loop `schedule.derive` then finds. Commando's
and JCH's ticks hold no call and their objects are untouched by the pass.

**What the register file costs the rest of the object.** Nothing in `universal.py`
but one target form: a `sets` target `*name` writes every voice's copy
(`Player.everyvoice`), which is §3.6's command-level `all` written where a row
can state it. The poison harness measures it at **0 differing of 332,358 over
thirty builds, 0 sites**. `meta.shadow`, `state0.shadow` and `shadow.<pair>` are
all mechanisms the player already had and no hand object had exercised from a
lift.

**A byte no region names is still a byte.** Three vocabularies the first two
families never needed: memory the play writes that S6 names no region for is a
global by its address (`#c131E` — GT2's four self-modified `JSR`/`JMP` low bytes
are exactly that, and a jump table's own edge is then a guard term the object can
state, `flow.switched`); a byte the play also reads as a **word** is that word's
half (`#c1295.lo`); and the zero page is memory like any other, which is what
JCH's `$saved`/`$saved12` were refused for at #349 and are cells for here.

## 3. The hints

The hints file `--hints` reads is one named datum a line, `meta.commit_order =
[…]`, of the kinds §3.1 lists. **For all three tunes it is empty: 0 lines, of
every kind.** Every datum any of the three objects carries is derived from the
certified artefacts.

What the lift could not lower it refuses rather than approximates, and each
certificate carries its own refusals — 4 on Commando, 1 on JCH, 13 on GT2, all
`unclassified update`:

| tune | refusal | site | what it is |
| --- | --- | --- | --- |
| Commando | `unclassified update` | `$5023`, `$5026`, `$5029`, `$502C` | the entry tick's own reset, which zeroes four per-voice cells through a loop index that is **not** the voice index; the lift has no cell for the store and drops it. The horizon is the evidence it costs nothing: 0 divergences over 11,780 ticks with it gone |
| JCH | `unclassified update` | `V#1` | one copy of the voice index the loop's own latch binds, which is not a value of the object: `{"cell": "voice_index"}` is |
| GT2 | `unclassified update` | `$1113`, `$1132`, `$1137`, `$113A` | the first call's own reset, again through an index that is not the voice's, and its two copies past the committing voice's. The prologue runs once a voice and the copy the *committing* voice takes is lowered, so the two others state nothing the first does not (§2.5) |
| GT2 | `unclassified update` | `L1119_8D$i0$u4_L140F_BD#1`, `L1119_8D$i0$u4_L1412_3D#1` | the two reads those copies fed |
| GT2 | `unclassified update` | `L1189_A9$i1$u14_L11F5_B1#1`, `…L11FC…`, `…L11FF…`, `…L120B…` | the order list read **through the zero-page pointer the walk sets**: the address is computed and no region names it. The score materialises the pattern the walk chose, so the object states the visits and not the walk |
| GT2 | `unclassified update` | `C#1`, `V#1`, `V#5` | the 6510's own carry and overflow at the tick's entry, and one copy of the voice index; none is a value of the object |
| JCH, GT2 | (was) `$saved`, `$saved12` | — | the zero page is memory like any other and is a cell here (§2.5); #349's two JCH refusals are gone |

## 4. Coverage

Every number below is `trackerprog.lift.report.json`, written by the commands at
the head of this document; none is typed.

| number | Commando | *Guldkorn Intro* | *Je suis Linus* |
| --- | --- | --- | --- |
| store sites of the tick outside the fetch regions | **86** | **132** | **101** |
| lowered into `sets` rows | **86 rows over 9 streams, 207 assignments** | **124 rows over 4 streams, 419 assignments** | **257 rows over 4 streams, 496 assignments** |
| recognised into `Acc` records | **5** — T1's three joined (§2.3), and two `ins.pw` stores T1 names no accumulator for | **0** (§2.3) | **0** (§2.3) |
| refused | **4** (§3) | **1** (§3) | **13** (§3) |
| T1 accumulators recognised | **3 of 3**, none refused: `acc_2_lo` (`repeat` + `flag`), `voice[].acc` (`field` + `phase bit`), `rec2[].b5591` (`add` with the carry) | **0 of 5**: 3 `T0 names no write of its own cells`, 2 `width 12 is not a section 5 width` | **0 of 4**: 2 `its rows stand outside the machine's rank order`, 2 `T1 names no second region for the word` |
| T2 recognised | the tuning (80 entries, base 16), the instrument selector (13 records, 6 columns), the score (3 order lists, 32 patterns, 572 events) | the tuning (95 entries, base 0), the selector (19 records, 8 columns, named by the values T2 saw the cell hold), the score (3 order lists, 37 patterns, 500 events) | the tuning (91 entries, base 0, and 165 `beyond` words with no trap), the selector (**30 records, 9 columns** — the widest of T2's six, which is what makes it the instrument's), the score (3 order lists, 3 patterns, 4,640 events) |
| tables materialised as streams of their own bytes (§2.2) | 0 | **19**, 1,877 rows | **15**, 778 rows |
| leaves opened | 438 constants, 331 cells, 43 globals, 10 pitch reads, 7 instrument columns, 0 unnamed | 760 constants, 694 cells, 29 globals, 4 pitch reads, 26 `tabcell` reads, 0 unnamed | 1,213 constants, 1,045 cells, 132 globals, 11 pitch reads, 11 instrument columns, 25 `tabcell` reads, 0 unnamed |
| score bytes a row supplies | 4 to 6, the row's own bytes and no more | 5 to 10, one a name where the fetch bound one and **one a turn** where its own loop did (§2.4) | 2 to 6, the visit's own bytes |
| the tick's own callees, inlined | 0 | 0 | **2 runs rerolled** into the voice loop they are, 27 blocks to 274 (§2.5) |

The three records, as the object states them:

| T1 | cell | width | delta | policy | bound | phase / flag | produce |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `acc0` the vibrato | `#acc_2_lo` | 16 | `repeat(u16(#step_lo, #step_hi), #b550C)` under `delta_when` | `reload` the tuning's own word | `observed [0, 64814]` | `flag {C_43, seed 1}` | `freq_lo` `freq_hi` |
| `acc1` the slide | `acc` | 16 | `field(#freq_lo_add, $FF)` | `wrap` | `projected [0, 65535]` | `bit(b5520, 0)` | `freq_lo` `freq_hi` |
| `acc2` the pulse run | `ins.pw.lo` | 8 | `#b5507 + tC_6`, the carry the vibrato's loop left | `wrap` | `projected [0, 255]` | — | `pw_lo` |

**T1's `rate` is not restated, and the reason is the lowering.** T1 gives all
three a countdown on `timer_5`; the lowering has already spent that counter as
a row and a guard, so a divider on the record would step it twice. The records
carry `rate: 1` and the divider stays where the lowering put it.

**The two `Acc` records that are still assignments** are the note-on's own
writes to `ins.pw.hi`/`ins.pw.lo`: T1 states no accumulator over them, so they
stay `policy: {reload: …}` — §5's record is the only `sets` target the schema
has for an instrument-scoped cell (§8's #347 item 3), not a reading of anything.

## 5. The certificate

`trackerprog/attest.py`, §2's comparison over the whole certified horizon
against the tune's own player on `deity_informant.PcodeVM`.

| tune | ticks | SID writes | divergences | permuted | identical | per-register order |
| --- | --- | --- | --- | --- | --- | --- |
| Commando song 1 | **11,780** | **133,109** | **0** | 8,370 | 3,410 | identical |
| *Guldkorn Intro* | **2,401** | **63,229** | **0** | 2,282 | 119 | identical |
| *Je suis Linus* | 8,236 | 205,900 | **1**, at tick **114** | 0 | 1,391 | — |

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write. JCH's 2,401 is the horizon
`docs/certificates/jch-guldkorn-intro.json` states: complete, period 1,512,
first repeat 2,400; GT2's 8,236 is `docs/certificates/gt2-je-suis-linus.json`'s:
complete, period 6,720, first repeat 8,235.

**GT2's first divergence, named.** Ticks 0 to 113 are write for write the
source's — the whole flush of all 25 registers, every tick. On tick **114** one
register pair differs:

```
register  voice 1's freq   expected $2BDD (11,229)   got $2AD8 (10,968)
```

The rest of that tick's 25 writes are identical, and so is every register of
every tick before it. What it is: the continuous slide of §5's `porta_up` /
`porta_down`, `ghost[v].freq ±= ptr` with the borrow the 6510's own `SBC`
leaves, run over the zero-page pair the speed table filled. The object states
both arms and both carries (`prelude0` rows 149 and 153, `@shadow.freq.lo` /
`.hi` with `{"sub": [1, {"cell": "tC_22"}]}` for the borrow); the 261 the two
differ by is one step of that slide, so what is wrong is *which* arm ran on one
tick and not the arithmetic of either. Diagnosing further is §8's residue 1: the
carry the tick **entered** with (`C#1`) is one of GT2's thirteen refusals, and no
cell of the object holds it.

**What the 0 is worth, and what it is not.** A lowering renders the program it
was lowered from, so 0 divergences over the horizon is close to guaranteed by
construction and is *not* evidence that anything was abstracted. What it is
evidence of is the recognition: every store the join took out of a stream and
restated as a §5 record renders the same writes as the rows it replaced, and
the horizon is where that is checked. `same_per_register_order` is a **symptom
and not a merit** — it holds because the lift keeps intermediate stores the
hand's producer list folds away, so the two write lists differ only by the
interleave; a reading that folded them would lose the property and be the
better object.

The prints and the objects against each tune's own load band (§9's acceptance
#3), `tools/trackerprog_sizes.py --object`:

| measure | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Commando `trackerprog.lift.md` | 1,166 | 19,557 | 1,125 | 7 | 41 | 1,125 | 6,164 |
| JCH `trackerprog.lift.md` | 2,920 | 26,694 | 2,874 | 7 | 46 | 2,874 | 7,936 |
| GT2 `trackerprog.lift.md` | 6,755 | 109,241 | 6,743 | 7 | 12 | 6,743 | 12,508 |

| artefact | raw | `xz -9e` | ratio to the band |
| --- | --- | --- | --- |
| Commando, lifted | 164,951 | **6,280** | **2.46×** |
| — its `score` half | 128,707 | 2,404 | |
| — everything else | 36,235 | 4,004 | |
| Commando, the hand object | 53,898 | 3,464 | 1.36× |
| — its load band | 4,039 | 2,548 | |
| *Guldkorn*, lifted | 181,385 | **8,552** | **3.46×** |
| — its `score` half | 120,224 | 2,756 | |
| — everything else | 61,152 | 5,992 | |
| *Guldkorn*, the hand object | 87,010 | 4,696 | 1.90× |
| — its load band | 3,343 | 2,472 | |
| *Je suis Linus*, lifted | 1,225,775 | **13,208** | **4.71×** |
| — its `score` half | 1,110,618 | 5,020 | |
| — everything else | 115,148 | 8,300 | |
| *Je suis Linus*, the hand object | 228,045 | 6,112 | 2.18× |
| — its load band | 4,845 | 2,804 | |

Commando is **2.46×** against the hand's 1.36× and §9.1's 1.25×–2.18× band
(`main` at #346 was 2.31×, at #349 2.16×); *Guldkorn* **3.46×** against 1.90×
(#348 3.03×, #349 3.19×); *Je suis Linus* **4.71×** against 2.18×. All three
grew this round, and by two named things, neither of them the third family's:

- **the zero page is memory** (§2.5). Commando's fetch writes it and reads it
  back, so the bytes are cells now and the score supplies each visit's own —
  4 to 6 constants an event against 2 to 4, and 2,404 compressed score bytes
  against 1,776. JCH's `$saved`/`$saved12` stop being refusals for the same
  reason and its score half goes 2,280 to 2,756.
- **the condition one more turn is taken is a cell** (§8). Each unrolled loop
  writes it once a turn, which is 6 rows on Commando, 9 on JCH.

GT2's 4.71× is the score half: **4,640 events** against the hand's 2,289,
because the lift's row is the fetch's own **visit** and GT2 fetches its row
`gatetimer` steps early — every visit is an event, where the hand's `stage`
holds one row and commits it later. Its sound half is 8,300 against 3,240.
**A lowering is bigger than a reading, and that is the trade B7 names.**

## 6. Against the hand objects

### 6.1 Commando

| section | lifted | hand | verdict |
| --- | --- | --- | --- |
| `pitch` | base 16, 80 entries | base 16, 80 entries | **identical** |
| `score.orders` | 64 / 63 / 123 `play` steps, `jump(0)` | 64 / 63 / 123, `jump(0)` | **identical** |
| `score.patterns` | 32 patterns, 572 events | 31, 570 | one extra: the last visit the horizon cuts |
| `meta.voice_order` / `commit_order` / `voices` / `cycles_per_tick` | `[2,1,0]` / `(ctrl,ad,sr)` / 3 / 19,656 | the same | **identical** |
| `meta.row_consumes_tick` / `row_command` | `true` / `spent` | `true` / `spent` | **identical** |
| `meta.tick` | `{stream}` `commit` `row` `commit` `machine` | `prelude` `commit` `row` `commit` `machine` | one datum, §2.1 |
| `meta.tempo` | a divider, rate 3, phase 0 | a divider, rate 3, phase 0 | same clock, no `early` |
| `meta.row` | `{commands}` `{stream rowprog0}` | five steps over the event's own fields | the lift keeps the row's **bytes** where the hand keeps its **fields** |
| `instruments` | 13 records, the six S6 columns plus `pw` | 9 reached, `adsr`/`wave`/`pw` named | the lift carries the file's table; the hand carries the subtune's reach, named |
| `streams` | 9, 86 rows of lowered `sets` | 3, 4 rows (`note_on`, `note_off`, `arp`) | the lowering, against the reading |
| `accs` | 5: 3 §5 records joined from T1, 2 reload assignments on `ins.pw` | 7 §5 records | **the three T1 states are records; the four the hand reads and T1 does not are still rows** |
| `beyond` | 21 words, 0 traps | 12 words, 2 traps | **the lift states more than the hand**: the two traps are the packed row byte, which the lift keeps as a cell (`b54F5`), so it has a word for them |
| `globals` | the tick-level stream, and `flag C_43` | `flags.C` and its proof | **the same mechanism**: the vibrato's loop leaves §5's carry and the pulse run reads it |
| `state0` | 123 cells, 28 globals | 4 cells | every SSA temp the object still reads is a cell |

Where the lift's statement is now the hand's: the carry (§5's `flag`, seeded and
read as a flag by the producer that consumes it), the vibrato's `repeat`, the
slide's `field` with a `phase bit`, the pulse run's `delta` with the carry. Where
it is better: the `beyond` words (no trap). Everywhere else the hand's is the
better statement, and §7 says what is left.

### 6.2 *Guldkornekspressen Intro*

| section | lifted | hand | verdict |
| --- | --- | --- | --- |
| `pitch` | base 0, 95 entries | base 0, 96 | one short: the hand asserts the stored table's own length, the lift takes T2's reach |
| `meta.voice_order` / `commit_order` / `voices` / `cycles_per_tick` | `[2,1,0]` / `(ad,sr,ctrl)` / 3 / 19,656 | the same | **identical** |
| `meta.tempo` | the counter `c1746`, step −1, one `reset` clause, boundary of three terms | the counter `rowclock`, step −1, one `reset` clause, boundary `== 0` and `early == 2` | **the same counter and the same clause**; the boundary is the fetch's, §2.1 |
| `meta.tick` / `row_consumes_tick` / `stage` | `{stream}` `row` `machine` / `false` / none | `fetch` `prelude` `row` `machine` / `[[keys != 0]]` / 5 rows | **differs**, §2.1 and §8's #348 item 2, and renders the same writes |
| `meta.shadow` | none | none | **identical**, and derived: the write-out is the voice's own rows, ranked last |
| `score.orders` | 26 / 18 / 18 `play` steps, `jump(0)` | 15 / 8 / 8, `jump(0)` | the lift's visits are its own: one per fetch the horizon took, the hand's one per stored order byte |
| `score.patterns` | 37 patterns, 500 events | 27, 723 | the lift's row is the fetch's own **visit**, carrying every byte the walk read as 5 to 10 constants; the hand's is one byte a row, so a held row is an event of its own |
| `instruments` | 19 records, 8 columns, named `0, 8, …, 144` | 19, 8 columns | **identical set**, named by what the cell holds (§2.2) |
| `streams` | 4 lowered (124 rows) and 19 tables (1,877 rows) | 10 (`pulse` 20, `filter` 23, `wavetab` 64, `wave`, `pitch`, `writeout`, `prelude`, `notestage`, `voicebits`, `channel`) | the lowering, against the reading: the hand's `pulse` 20, `filter` 23 and `wavetab` 64 are the same bytes the lift's nineteen tables hold, read the same way (`tabcell`) — records with a `next` link and a hold, against one row a byte |
| `accs` | 0 | 7 §5 records | §2.3: T1 states five and the join refuses all five, by name |
| `state0` | 247 cells, 11 globals | 41 cells | every SSA temp the object still reads is a cell; 8 of them are joins (§2.2), 12 turns of the fetch's own byte loop (§2.4) and 2 the loops' own continue conditions (§8) |

### 6.3 *Je suis Linus le salaud*

| section | lifted | hand | verdict |
| --- | --- | --- | --- |
| `meta.shadow` | 25 registers, `mode_vol` first, `v0.freq_lo` last | 25, `$D418` down to `$D400` | **identical**, and derived from T0 and one tick of the program (§2.5) |
| `state0.shadow` | the post-init image's own 25 bytes | the same | **identical** |
| `state0.prologue` | 91 rows, the first call's reset lowered | 8 `sets` and 3 `point`s, read | **the same tick**: the object spends it either way, and the lift states the reset's arithmetic where the hand states its effect |
| `meta.voice_order` / `voices` / `cycles_per_tick` | `[0,1,2]` / 3 / 19,656 | the same | **identical** |
| `meta.commit_order` | `(ad, sr, ctrl)` | `(sr, ad, ctrl)` | **differs and is unobservable**: under a shadow every write lands in the image and only the flush reaches the chip |
| `meta.tempo` | `timer_2`, step −1, boundary of three terms, no `reset`, no divider | `rowclock`, step −1, boundary `== 0`, `early` 2, two `reset` clauses | **the same counter**; the boundary is the fetch's own guard and the reload is a row of the voice's phase (§2.1) |
| `meta.tick` / `row_consumes_tick` / `stage` | `{stream}` `commit` `row` `commit` `machine` / `false` / none | six phases / `[[keys != 0]]` / 4 steps | **differs**, as on JCH: the lift's row is the fetch and it has no `stage` |
| `pitch` | base 0, 91 entries, 165 `beyond` words, 0 traps | base 0, 96 entries | the lift takes T2's reach and states the rest as the bytes the image holds |
| `instruments` | 30 records, 9 columns | 30, nine columns | **identical set and shape** |
| `score.orders` | 1 `play` step each, `jump(0)` | 3 order programs of `play(pattern, transpose)` | **differs**: the order list is read through a zero-page pointer the object cannot address (§3), so every visit is the one pattern and the walk is not stated |
| `score.patterns` | 3 patterns, 4,640 events | 33, 2,289 | the lift's row is the fetch's own **visit** and GT2 fetches `gatetimer` steps early, so a visit is an event; 43 distinct row commands the hand names are inlined bytes here |
| `streams` | 4 lowered (257 rows) and 15 tables (778 rows) | 8 (`wave` 100, `pulse` 29, `filter` 43, `speed` 18, `note_on`, `hard_restart`, `exit`, `funktempo`) | the lowering, against the reading: the same bytes, read the same way (`tabcell`), one row a byte |
| `accs` | 0 | 9 §5 records | §2.3: T1 states four and the join refuses all four, by name |
| `globals` | the tick-level stream, and 2 `commit` registers | the filter stream and three registers | **the same mechanism**, §3.7 |
| `state0` | 282 cells, 38 globals | 11 cells | every SSA temp the object still reads is a cell: 208 SSA, 7 register, 26 joins, 19 turns, 3 continue conditions, 19 the tune's |
| `row_command` | `spent` | `held` | **differs**: the lift inlines each visit's bytes rather than naming a command the voice keeps |

## 7. The limits

The recognition changes what T1 names and nothing else. **The unit of the
lowering is unchanged**: one IR block is one row, one SSA temp is one cell, and
the object still carries the tick's arithmetic rather than a reading of it.

| measure | Commando, this lift | Commando by hand | *Guldkorn*, this lift | *Guldkorn* by hand | *Je suis Linus*, this lift | *Je suis Linus* by hand |
| --- | --- | --- | --- | --- | --- | --- |
| streams / rows that carry `sets` | 9 / 86 | 3 / 4 | 23 / 2,001 | 10 / 124 | 19 / 1,035 | 8 / 202 |
| `sets` assignments | 207 | 8 | 419 | 80 | 496 | 209 |
| accumulators | 3 §5 records + 2 stand-ins | 7 §5 records | 0 | 7 §5 records | 0 | 9 §5 records |
| `state0` cells | 123 (89 SSA · 18 register · 3 continue · 13 the tune's) | 4 | 247 (157 SSA · 20 register · **8 join** · **12 turn** · 2 continue · 48 the tune's) | 41 | 282 (208 SSA · 7 register · **26 join** · **19 turn** · 3 continue · 19 the tune's) | 11 |
| largest `sets` expression | 26 nodes | 4 | 29 | 10 | 30 | 5 |
| raw / `xz -9e` | 164,951 / 6,280 | 53,898 / 3,464 | 181,385 / 8,552 | 87,010 / 4,696 | 1,225,775 / 13,208 | 228,045 / 6,112 |
| ratio to the load band | **2.46×** | 1.36× | **3.46×** | 1.90× | **4.71×** | 2.18× |

All three objects grew against #349, and by mechanisms none of the three needed
for itself: the zero page as memory (Commando +784 compressed bytes, JCH +676)
and the continue condition as a cell (§8). Neither is the third family's; both
are what the third family showed the first two were missing.

What remains, and what it would take:

| residue | what it is |
| --- | --- |
| 86, 124 and 257 rows of lowered arithmetic | the tick outside the fetch regions that T1 names no accumulator over: Commando's drum, skydive, arpeggio, pulse bounce and note-on; all of JCH's; all of GT2's. A second recognition pass would need a plane that states them, and T1 does not |
| 123, 247 and 282 declared cells | one per SSA temp the rows still read, and one per join (§2.2), per turn of a fetch's byte loop (§2.4) and per loop's own continue condition (§8). The lowering's own unit; nothing in the recognition changes it |
| 2 reload stand-ins | §8's #347 item 3: `ins.pw` is the only instrument-scoped cell the schema can be assigned |
| `rate: 1` on all three records | T1's countdown is already a row and a guard (§4) |
| 19 and 15 tables, 1,877 and 778 rows | JCH's and GT2's column programs as bytes: the lowering states a table read at a cursor as `tabcell` over the region's own bytes, where the hand states records with `next` links and holds. The rows are the same data; the reading is what the lift does not have |
| GT2's 4,640 events against the hand's 2,289 | the lift's row is the fetch's own visit and GT2 fetches early, so a visit is an event. `meta.stage` and `row_command: held` are the two data that would fold them, and the lift derives neither |

**T1's plane is horizon-dependent, and that bounds the pass.** Over a 1,200-call
prefix T1 refuses both of Commando's recurrences as `divergent recurrence` and
states no accumulator at all, so the join has nothing to join and every store
stays a row. `tests/trackerprog/test_hvsc_lift.py` asserts exactly that at that
horizon; the join itself is exercised on hand-built rows and T1 records in
`tests/trackerprog/test_recognise.py`, and over the whole horizon by the command
at the head of this document.

## 8. What each family cost, and what is left

The four things #347 said stood between this and a second family, and what each
turned out to be:

| # | at #347 | now |
| --- | --- | --- |
| 1 | **a tick of several procedures** — a callee is a phase, and inlining it is the whole change | **not what it is.** The certified S4 tick is one procedure; the several the *print* names are one block reached from several sites, and what that costs is a guard the schema cannot state. A join is a cell (§2.2), Commando's every join folds back to the guard path it had, and JCH's eight do not |
| 2 | **a row clock that is not a per-voice countdown into the fetch** | **done.** §3.6's counter is derived whole — cell, step, boundary as a guard *list*, `reset` clauses, and the divider separated from the clock by what its guard compares against (§2.1). Both families' clocks are values of it and Commando's object is unchanged to the byte |
| 3 | **`ins.pw` is the only instrument-scoped cell the schema can be assigned** | unchanged, and JCH does not need it: its pulse pair is a per-voice image cell |
| 4 | **a plane that names the effects T1 does not** | unchanged, and wider than it looked: on JCH the join takes **none** of the five T1 does state (§2.3) |

What the second family added instead, each family-free and each on both tunes:
the general clock, the join's own cell, a table read at a cursor as `tabcell`
over the region's own bytes, a register named by the store's own address, a
record split one copy a voice as per-voice cells, a pinned read as the image's
byte, and instrument records named by what T2 saw the selecting cell hold.

**The residue #348 named, and what it was.** #348 listed four things the lift
could not state, in the order JCH met them. The first was the tick-4 divergence
and is the only one that was one:

| # | at #348 | now |
| --- | --- | --- |
| 1 | **one score byte a *turn*, not a name** | **done** (§2.4). The score supplies one constant per (name, turn) and the unrolled turn copies its own into the cell the name is; a second defect the fix uncovered is done with it — a join's preds do not stop at a segment's edge (§2.2), and until they were all raised a voice whose row was still held wrote nothing at all on that tick. **JCH renders its 2,401 ticks with 0 divergences** |
| 2 | **a row that spends the tick on a fact of the row** | **not a divergence.** The hand's row phase is the commit and spends the tick its note-on returns from; the lift's is the fetch, which the source does not return from, so `false` is the derived datum and the two render the same 63,229 writes. It stays a difference of statement (§2.1, §6.2), not of behaviour |
| 3 | **a `produce` the join can reach past a cell** | unchanged: three of T1's five refuse with `T0 names no write of its own cells`, the write-out they produce through standing under a join's cell while the join walks the rank order under the record's own guard (§2.3) |
| 4 | **`width 12`** | unchanged: §5 admits 8, 11, 12 and 16, `recognise.Acc` admits 8 and 16, and JCH's pulse pair is refused by name |

3 and 4 are what keeps JCH's `accs` at 0 against the hand's 7 and its ratio at
3.46× against 1.90× (§5, §7). Neither is a divergence and neither needs a player
mechanism or a schema form §3 does not have; both are the recognition.

**What the third family cost.** GT2 met eleven things the first two did not, and
ten of them landed; each is family-free and each is on all three tunes:

| # | what it is | where |
| --- | --- | --- |
| 1 | a callee inlined where it stands, and a **run** of calls rerolled into the loop its own stepping argument closes — which is how a tick with no voice loop gets one | §2.5 |
| 2 | §3.1's register file: T0's own image, its flush, the order one tick sends it in, and `state0.shadow` | §2.5 |
| 3 | `state0.prologue` derived — the blocks the first call runs and no later call does, where that call runs no block of the voice loop | §2.5 |
| 4 | a divider told from the clock by comparing against the cell **its own reload reads**, not merely against a cell | §2.1 |
| 5 | a per-voice array at a **stride**: `_grouped` off the stride is another array's byte, and a record S6 splits at the voice stride is per-voice by copy and field | §2.5 |
| 6 | a jump table's own edges as guard terms (`flow.switched`), and the self-modified bytes they read as globals by address | §2.5 |
| 7 | a byte the play also reads as a **word** is that word's half; the zero page is memory like any other | §2.5 |
| 8 | a split tuning: two byte tables, the halves tried nearest-origin first, and the words past them the bytes the image holds | §2.1, §2.3 |
| 9 | the instrument selector is the **widest** of T2's, and a record stands where the selecting cell's own value puts it — its number in one family, the offset it already is in another | §2.1 |
| 10 | a value every copy of a per-voice cell takes is one `sets` target `*name`: §3.6's `all`, where a row states it. The one player change this round, **0 differing of 332,358** | §2.5 |
| 11 | **the condition one more turn is taken, as a cell the turn that ran leaves.** The term is read after the latch's own copy, so reading it as a term is a turn behind and stops the unrolled loop one turn early — 41 turns of a 42-turn reset. A row a turn writes the condition into a cell and the next turn's rows read that | §2.2 |

**What stopped GT2 at tick 114, in the order it meets them:**

| # | residue | state |
| --- | --- | --- |
| 1 | **the carry the tick entered with.** `C#1` and `V#1`/`V#5` are the 6510's own flags at the tick's entry; no cell of the object holds them, and the slide's borrow is what the tick-114 divergence is one step of (§5) | refused by name, and the first thing to take |
| 2 | **the order list read through a zero-page pointer.** Four refusals: the address is computed, so the object states the visits the score materialised and not the walk that chose them. `score.orders` is one `play` a voice as a result | refused by name |
| 3 | **a `produce` past a join's cell** and **`width 12`** — #348's residues 3 and 4, unchanged, and GT2 adds two of its own: `its rows stand outside the machine's rank order` (2 of its 4 T1 records) and `T1 names no second region for the word` (the other 2) | recognition only |
| 4 | **`meta.stage` and `row_command: held`** — the two data that would make GT2's 4,640 visits the hand's 2,289 rows. Neither is derived; both are schema forms §3 already has | not attempted |

None of the four needs a player mechanism or a schema form §3 does not have.
Residue 1 is the one the certificate names, and it is where a fourth pass over
GT2 starts.

## 9. The three families

Every number is `trackerprog.lift.report.json` and
`tools/trackerprog_sizes.py --object`; a fourth family extends the table by one
row.

| family | tune | store sites | rows / `sets` | `Acc` records | T1 joined | refused | hints | `xz -9e` | ratio | hand's ratio | certificate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Hubbard | Commando song 1 | 86 | 86 / 207 | 5 | **3 of 3** | 4 | **0** | 6,280 | 2.46× | 1.36× | **0 divergences over 11,780 ticks, 133,109 writes** |
| JCH V20 | *Guldkornekspressen Intro* | 132 | 124 / 419 | 0 | 0 of 5 | 1 | **0** | 8,552 | 3.46× | 1.90× | **0 divergences over 2,401 ticks, 63,229 writes** |
| GoatTracker 2 | *Je suis Linus le salaud* | 101 | 257 / 496 | 0 | 0 of 4 | 13 | **0** | 13,208 | 4.71× | 2.18× | 113 of 8,236 ticks; tick **114** diverges by one register pair (§5) |

Six families of the nine are unattempted; the two whose planes are on disk
(SID Wizard's *Emomyst*, and the registry builds with no T1/T2) refuse by name
(`tests/trackerprog/test_hvsc_lift.py`).

The mechanisms themselves are family-free: `tests/trackerprog/test_assemble.py`
and `tests/trackerprog/test_recognise.py` exercise them on hand-built IR, rows
and T1 records with no tune at all, and `tests/trackerprog/test_hvsc_lift.py`
lifts and certifies Commando and JCH — the schedule, the turn's own cells and
0 divergences — and asserts the two remaining refusals (GT2 and SW, both
`score not cursor-shaped`, `the tick reaches no fetch region`: the regions
`region.fetch` finds stand in a procedure the tick proc does not hold).
