# Prototype: the lift from certified artefacts to a trackerprog

The exemplar [prototype-trackerprog.md](prototype-trackerprog.md) §9 says is
missing: a **lift** that produces a trackerprog rather than a hand reading of
one. It answers [trackerprog-backlog.md](trackerprog-backlog.md) **B6** (is the
schedule recoverable?) and **B7** (lower the tick, do not classify it, then
recognise what T1 names) on two tunes, rendered by
`deity_informant/trackerprog/universal.py` and certified against each tune's own
player on the PcodeVM:

| tune | schedule | hints | refusals | certificate |
| --- | --- | --- | --- | --- |
| Commando song 1 (Hubbard) | derived, one datum from the hand's (§2.1) | **0** | 4, named (§3) | **0 divergences over 11,780 ticks** |
| *Guldkornekspressen Intro* (JCH V20) | derived, four datums from the hand's (§2.1) | **0** | 3, named (§3) | **the first divergence named**: tick 4 of 2,401 (§5, §8) |

Neither object has a `program` key. **All three of Commando's T1 accumulators
land as §5 records** (§2.3) and **none of JCH's five** (§4); §7 states what the
pass did not change and §8 what the second family costs.

```
tools/tuneprog_trackerprog.py --out <out>/commando-song1 \
    --sid $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid --certify
tools/tuneprog_trackerprog.py --out <out>/jch-guldkorn-intro \
    --sid $HVSC/MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid --certify
```

The lift reads `tuneprog.T2.json` at the horizon it was computed over, so an
output directory whose T2 was written for a prefix lifts that prefix. JCH's is
recomputed at its certified 2,401 first, and the object is the same one either
way (`trackerprog/build.artefacts` computes the same plane in memory):

```
tools/tuneprog_score.py --out <out>/jch-guldkorn-intro --calls 2401
```

Contents: 1 the source · 2 the method · 3 the hints · 4 coverage ·
5 the certificate · 6 against the hand objects · 7 the limits ·
8 what the second family costs.

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
| a tick of several procedures ([§8](#8-what-the-second-family-costs) item 1 at #347) | the certified S4 tick is **one** procedure of 142 blocks. `p_10E9`, `p_1409`, `p_1616`, `p_14F7` and `p_14FD` are the *presentation's*; what the S4 leaves in their place is a **join** — one block reached from several sites — and that is what the lowering must state (§2.2) |
| `meta.shadow.registers`, a flush of the image the last tick left | the eleven `sid_image` regions are written **and sent inside the same tick**, by the voice's own last blocks (`p_1616`, jch.md:648-659), so the lowering's ranked `sets` rows already state it and no `shadow` is derived. §3.1's flush is the *wrapper's*, which only [*Knob at Night*](prototype-jch-trackerprog.md) §4.1 carries |

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
| `tempo.cell`, `step` | the counter a guard on the fetch's own path reads and a store steps by a constant, of the candidates the one whose *own* step stands under fewest guards. A counter the voice loop steps is a voice cell; one the tick steps outside it is a scalar the whole tune keeps, declared as one cell per voice that every copy enters equal — which is what §3.6's per-voice `tempo.cell` needs of a tick-level clock |
| `tempo.boundary` | that path's own guard terms, over the object's cells: **a guard list**, not one term |
| `tempo.reset` | §3.6's clauses: every other store to the counter the **tick** makes outside the voice loop, each under its own guard path. A counter a *voice* steps is refilled by a row of that voice's phase, as Commando's is, and the clock states no clause |
| `tempo.rate`, `phase` | the counter a term of that path compares with a second **cell** rather than a constant: that is the divider, its reload plus one, and the residue class the post-init image admits |
| `row_consumes_tick` | whether the fetch's exit still reaches the first block of the machine segment |
| stream `rank`s | the order the segments' rows and accumulators stand in, which is program order |

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
| `row_consumes_tick` | `false` | `[[keys != 0]]` | **differs**, and it is §8's residue: the note-on returns from the voice's pass and the lift has no fact of the row to say so |
| `meta.shadow` | none | none (Guldkorn) | same: the write-out is the voice's own last rows, ranked last |
| `row_command` | `spent` | `spent` | same |

Four datums differ and one mechanism is behind three of them: **the lift's row
phase is the fetch region**, because the fetch is what the score materialises
(§2.4). The hand reads the row at the fetch and *commits* it two clock steps
later; the lift has no `stage`, so it consumes the row where the source read it.

**One thing the RPO segmentation says that the object does not need.** Taking
B6's rule literally — *maximal* segments between the commit sites — cuts the
machine again at `$5333`, the drum's own gate write, giving a fourth segment.
The object does not carry it: `Player.runstream` makes each row its own **act**
(§2 rule 1), so the writes on either side of that cut are already two acts and a
`commit` between them distinguishes nothing. The rule as run here is therefore
*segments at the fetch regions, commits at the edge writes that end them*.

### 2.2 B7: the tick lowered

`trackerprog/lower.py`, `vocab.py`, `cells.py`, `build.py`. Every store site of
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
| an inner loop | unrolled to the turns the horizon takes, repetition *j* under the edge that continues it, and a `trap` row past the last |
| a loop index that is a constant per turn | folded into that repetition, which is what makes the presentation's **rerolled sibling copies** lower at all |

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
sites, and §8 item 1's "a callee is a phase; inline it" in the shape the S4
actually leaves.

**The shift is one node, not 2^*k* copies.** `x << k` had been *k* doublings of
`{"add": [node, node]}` with the subtree copied at each, so a shift by *k* had
2^*k* leaves and the vibrato step's 16-bit rotate stood as 128 copies of one
mask: the largest `sets` expression was **1,282 nodes** on `main` and is **36**
here, against the hand object's 5 (§7's table). One expression of the 196 still
names an operand twice — the two-stage carry an `ADC` leaves in the S4 IR, 3
nodes at `machine2` row 3 — and it is the IR's own shape, not the lowering's.

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
admits at 8 and 16 and no other value. Both are §8's residue and neither is an
approximation.

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

**One value a name, and that is the limit.** A visit supplies one constant per
*name* the fetch bound, which is right where the row's bytes are the fetch's own
statements — Commando's two to four — and wrong where they are **turns of one
loop**: the lowering unrolls the fetch's loop to its recorded trip count and
names each SSA temp once, so all three of JCH's turns read the one cell
`t_saved8` and every turn decodes the byte the last one left. It is where JCH's
certificate diverges (§5) and the first thing a third family would need (§8).

## 3. The hints

The hints file `--hints` reads is one named datum a line, `meta.commit_order =
[…]`, of the kinds §3.1 lists. **For both tunes it is empty: 0 lines, of every
kind.** Every datum either object carries is derived from the certified
artefacts.

What the lift could not lower it refuses rather than approximates, and each
certificate carries its own refusals — 4 on Commando, 3 on JCH, all
`unclassified update`:

| tune | refusal | site | what it is |
| --- | --- | --- | --- |
| Commando | `unclassified update` | `$5023`, `$5026`, `$5029`, `$502C` | the entry tick's own reset, which zeroes four per-voice cells through a loop index that is **not** the voice index; the lift has no cell for the store and drops it. The horizon is the evidence it costs nothing: 0 divergences over 11,780 ticks with it gone |
| JCH | `unclassified update` | `$saved`, `$saved12` | the two zero-page bytes the tick saves at entry and restores at exit (`$FB`/`$FC`): the object has no cell for the 6510's own pointer, and the play reads them nowhere else |
| JCH | `unclassified update` | `V#1` | one copy of the voice index the loop's own latch binds, which is not a value of the object: `{"cell": "voice_index"}` is |

## 4. Coverage

Every number below is `trackerprog.lift.report.json`, written by the commands at
the head of this document; none is typed.

| number | Commando | *Guldkorn Intro* |
| --- | --- | --- |
| store sites of the tick outside the fetch regions | **86** | **132** |
| lowered into `sets` rows | **80 rows over 9 streams, 196 assignments** | **114 rows over 4 streams, 388 assignments** |
| recognised into `Acc` records | **5** — T1's three joined (§2.3), and two `ins.pw` stores T1 names no accumulator for | **0** (§2.3) |
| refused | **4** (§3) | **3** (§3) |
| T1 accumulators recognised | **3 of 3**, none refused: `acc_2_lo` (`repeat` + `flag`), `voice[].acc` (`field` + `phase bit`), `rec2[].b5591` (`add` with the carry) | **0 of 5**: 3 `T0 names no write of its own cells`, 2 `width 12 is not a section 5 width` |
| T2 recognised | the tuning (80 entries, base 16), the instrument selector (13 records, 6 columns), the score (3 order lists, 32 patterns, 572 events) | the tuning (95 entries, base 0), the selector (19 records, 8 columns, named by the values T2 saw the cell hold), the score (3 order lists, 36 patterns, 499 events) |
| tables materialised as streams of their own bytes (§2.2) | 0 | **19**, 1,877 rows |
| leaves opened | 437 constants, 324 cells, 32 globals, 10 pitch reads, 7 instrument columns, 0 unnamed | 841 constants, 697 cells, 25 globals, 4 pitch reads, 26 `tabcell` reads, 0 unnamed |
| score bytes a row supplies | 2 to 4, the row's own bytes and no more | 3 to 6, one a name and **not** one a turn (§2.4) |

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
has for an instrument-scoped cell (§8 item 3), not a reading of anything.

## 5. The certificate

`trackerprog/attest.py`, §2's comparison over the whole certified horizon
against the tune's own player on `deity_informant.PcodeVM`.

| tune | ticks | SID writes | divergences | permuted | identical | per-register order |
| --- | --- | --- | --- | --- | --- | --- |
| Commando song 1 | **11,780** | **133,109** | **0** | 8,370 | 3,410 | identical |
| *Guldkorn Intro* | **2,401** | 61,430 | **1**, at tick **4** | 4 | 15 | — |

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write.

**JCH's first divergence, named and diagnosed.** Tick 4, voice 1: the object
writes `ad`/`sr` = `$0F`/`$00` twice — the hard restart and the write-out that
follows it — where the tune writes `$62`/`$48` and `ctrl $09`, the note-on's own
envelope; `freq` is 864 against 960. The object's row keys a note where the
tune's holds, and §2.4 says why: the fetch's own byte loop turns up to **3**
times a visit (`report.trips`, `L113D_BC`) and the score supplies **one**
`@t_saved8` an event, so the object decodes a `$8x` duration prefix as the note
byte that follows it. The 2,401-tick horizon is rendered whole — the divergence
is a wrong value, not a refusal to run — and the certificate names it rather
than the lift approximating past it.

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
| Commando `trackerprog.lift.md` | 1,145 | 14,862 | 1,104 | 7 | 41 | 1,104 | 5,316 |
| JCH `trackerprog.lift.md` | 2,879 | 20,809 | 2,834 | 7 | 45 | 2,834 | 6,880 |

| artefact | raw | `xz -9e` | ratio to the band |
| --- | --- | --- | --- |
| Commando, lifted | 138,225 | **5,496** | **2.16×** |
| — its `score` half | 101,990 | 1,776 | |
| — everything else | 36,226 | 3,860 | |
| Commando, the hand object | 53,898 | 3,464 | 1.36× |
| — its load band | 4,039 | 2,548 | |
| *Guldkorn*, lifted | 146,320 | **7,484** | **3.03×** |
| — its `score` half | 85,470 | 1,940 | |
| — everything else | 60,841 | 5,720 | |
| *Guldkorn*, the hand object | 87,010 | 4,696 | 1.90× |
| — its load band | 3,343 | 2,472 | |

Commando is **2.16×** against the hand's 1.36× and §9.1's 1.25×–2.18× band
(`main` at #346 was 2.31×); its two score halves are within a seventh of each
other (1,776 against 1,568 over the same 572 events) and the whole difference is
the sound half, 3,860 against 2,076. *Guldkorn* is **3.03×** against the hand's
1.90×, and its score half is *smaller* than the hand's (1,940 against a hand
object whose score is a third of 4,696): the whole excess is again the sound
half, 5,720, of which the 19 materialised tables are the new part. **A lowering
is bigger than a reading, and that is the trade B7 names.**

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
| `streams` | 9, 80 rows of lowered `sets` | 3, 4 rows (`note_on`, `note_off`, `arp`) | the lowering, against the reading |
| `accs` | 5: 3 §5 records joined from T1, 2 reload assignments on `ins.pw` | 7 §5 records | **the three T1 states are records; the four the hand reads and T1 does not are still rows** |
| `beyond` | 21 words, 0 traps | 12 words, 2 traps | **the lift states more than the hand**: the two traps are the packed row byte, which the lift keeps as a cell (`b54F5`), so it has a word for them |
| `globals` | the tick-level stream, and `flag C_43` | `flags.C` and its proof | **the same mechanism**: the vibrato's loop leaves §5's carry and the pulse run reads it |
| `state0` | 117 cells, 19 globals | 4 cells | every SSA temp the object still reads is a cell |

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
| `meta.tick` / `row_consumes_tick` / `stage` | `{stream}` `row` `machine` / `false` / none | `fetch` `prelude` `row` `machine` / `[[keys != 0]]` / 5 rows | **differs**, §2.1 and §8 |
| `meta.shadow` | none | none | **identical**, and derived: the write-out is the voice's own rows, ranked last |
| `score.orders` | 26 / 18 / 18 `play` steps, `jump(0)` | 15 / 8 / 8, `jump(0)` | the lift's visits are its own: one per fetch the horizon took, the hand's one per stored order byte |
| `score.patterns` | 36 patterns, 499 events | 27, 723 | the lift's row is the fetch's own visit and carries no `dur`, so a held row is not an event (§8) |
| `instruments` | 19 records, 8 columns, named `0, 8, …, 144` | 19, 8 columns | **identical set**, named by what the cell holds (§2.2) |
| `streams` | 4 lowered (114 rows) and 19 tables (1,877 rows) | 10 (`pulse` 20, `filter` 23, `wavetab` 64, `wave`, `pitch`, `writeout`, `prelude`, `notestage`, `voicebits`, `channel`) | the lowering, against the reading: the hand's `pulse` 20, `filter` 23 and `wavetab` 64 are the same bytes the lift's nineteen tables hold, read the same way (`tabcell`) — records with a `next` link and a hold, against one row a byte |
| `accs` | 0 | 7 §5 records | §2.3: T1 states five and the join refuses all five, by name |
| `state0` | 227 cells, 7 globals | 41 cells | every SSA temp the object still reads is a cell, and 8 of them are joins (§2.2) |

## 7. The limits

The recognition changes what T1 names and nothing else. **The unit of the
lowering is unchanged**: one IR block is one row, one SSA temp is one cell, and
the object still carries the tick's arithmetic rather than a reading of it.

| measure | Commando `main` #346 | Commando, this lift | Commando by hand | *Guldkorn*, this lift | *Guldkorn* by hand |
| --- | --- | --- | --- | --- | --- |
| streams / rows that carry `sets` | 7 / 84 | 9 / 80 | 3 / 4 | 23 / 114 | 10 / 38 |
| `sets` assignments | 257 | 196 | 5 | 388 | 80 |
| accumulators | 3 reload stand-ins | 3 §5 records + 2 stand-ins | 7 §5 records | 0 | 7 §5 records |
| `state0` cells | 206 (142 SSA · 27 register temps · 23 carry · 14 the tune's) | 117 (85 · 17 · 4 · 11) | 4 | 227 (152 SSA · 17 register · 2 carry · **8 join** · 48 the tune's) | 41 |
| largest `sets` expression | 1,282 nodes | 36 | 5 | 40 | 13 |
| raw / `xz -9e` | 162,579 / 5,892 | 138,225 / 5,496 | 53,898 / 3,464 | 146,320 / 7,484 | 87,010 / 4,696 |
| ratio to the load band | 2.31× | **2.16×** | 1.36× | **3.03×** | 1.90× |

What the pass removed: the vibrato's unrolled loop, the slide's two arms, the
pulse run's assignment and the register stores the three fed — 19 of the 23
carry cells and 57 of the 142 SSA temps with them. What the linear shift removed:
the largest expression, 1,282 nodes down to 36. Together, 24,354 raw bytes and
396 compressed ones.

What remains, and what it would take:

| residue | what it is |
| --- | --- |
| 80 and 114 rows of lowered arithmetic | the tick outside the fetch regions that T1 names no accumulator over: Commando's drum, skydive, arpeggio, pulse bounce and note-on; all of JCH's. A second recognition pass would need a plane that states them, and T1 does not |
| 117 and 227 declared cells | one per SSA temp the rows still read, and on JCH one per join (§2.2). The lowering's own unit; nothing in the join changes it |
| 2 reload stand-ins | §8 item 3: `ins.pw` is the only instrument-scoped cell the schema can be assigned |
| `rate: 1` on all three records | T1's countdown is already a row and a guard (§4) |
| 19 tables, 1,877 rows | JCH's column programs as bytes: the lowering states a table read at a cursor as `tabcell` over the region's own bytes, where the hand states 4-column records with `next` links and holds (jch-trackerprog §4.6). The rows are the same data; the reading is what the lift does not have |

**T1's plane is horizon-dependent, and that bounds the pass.** Over a 1,200-call
prefix T1 refuses both of Commando's recurrences as `divergent recurrence` and
states no accumulator at all, so the join has nothing to join and every store
stays a row. `tests/trackerprog/test_hvsc_lift.py` asserts exactly that at that
horizon; the join itself is exercised on hand-built rows and T1 records in
`tests/trackerprog/test_recognise.py`, and over the whole horizon by the command
at the head of this document.

## 8. What the second family costs

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

**What the lift still cannot state, in the order JCH meets it:**

1. **One score byte a *turn*, not a name.** §2.4: the fetch's own loop reads a
   byte a turn, the score supplies one constant per name, and JCH's row is up to
   three turns of one loop. This is the tick-4 divergence (§5) and nothing else
   about the object is wrong at that tick.
2. **A row that spends the tick on a fact of the row.** JCH's note-on returns
   from the voice's pass, which the hand states as `row_consumes_tick: [[keys !=
   0]]`; the lift derives `row_consumes_tick` from the fetch's own exit and has
   no fact of the row to guard it with (§2.1).
3. **A `produce` the join can reach past a cell.** §2.3: three of T1's five
   refuse because the write-out they produce through now stands under a join's
   cell, and the join walks the rank order under the record's own guard.
4. **`width 12`.** §5 admits 8, 11, 12 and 16; `recognise.Acc` admits 8 and 16,
   which refuses JCH's pulse pair by name.

The mechanisms themselves are family-free: `tests/trackerprog/test_assemble.py`
and `tests/trackerprog/test_recognise.py` exercise them on hand-built IR, rows
and T1 records with no tune at all, and `tests/trackerprog/test_hvsc_lift.py`
lifts and certifies Commando, lifts JCH and asserts its schedule and its first
divergence, and asserts the two remaining refusals (GT2 and SW, both
`score not cursor-shaped`, `the tick reaches no fetch region`: the regions
`region.fetch` finds stand in a procedure the tick proc does not hold).
