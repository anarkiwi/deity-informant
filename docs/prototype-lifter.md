# Prototype: the lift from certified artefacts to a trackerprog

The exemplar [prototype-trackerprog.md](prototype-trackerprog.md) §9 says is
missing: a **lift** that produces a trackerprog rather than a hand reading of
one. It answers [trackerprog-backlog.md](trackerprog-backlog.md) **B6** (is the
schedule recoverable?) and **B7** (lower the tick, do not classify it, then
recognise what T1 names) on one tune, Commando song 1 — 11,780 ticks,
**0 divergences**, no `program` key, rendered by
`deity_informant/trackerprog/universal.py` and certified against the tune's own
player on the PcodeVM. The hints file is **empty**, and **all three of T1's
accumulators land as §5 records** (§2.3); §7 states what the pass did not
change.

```
tools/tuneprog_trackerprog.py --out out/recert-main/commando-song1 \
    --sid $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid --certify
```

Contents: 1 the source · 2 the method · 3 the hints · 4 coverage ·
5 the certificate · 6 against the hand object · 7 the limits ·
8 what the next family needs.

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
| `tempo.cell`, `step`, `boundary` | the per-voice counter a block steps by −1 and whose sign decides entry to the fetch, and that block's own branch condition |
| `tempo.rate`, `phase` | the tick-level counter a reload gates: its reload plus one, and the residue class its own countdown admits from the post-init image |
| `row_consumes_tick` | whether the fetch's exit still reaches the first block of the machine segment |
| stream `rank`s | the order the segments' rows and accumulators stand in, which is program order |

The derived schedule, datum for datum against the hand's:

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
| `Store(io, …)` | the register the chip's own column names it (`freq_lo`, `ctrl`, …) |
| `Store(ram, base + <voice index>)` | `["@name", …]`, the cell S6's `voice` group names, declared where it names none |
| `Store(ram, const)` | `["#name", …]`, a global |
| a store to the instrument-scoped pulse pair | an `Acc` with `policy: {reload: …}` and no `delta` — §5's own record is the only `sets` target the schema has for `ins.pw` |
| a load of a `const` record column at `stride × ins` | `{"ins": column}` |
| a load of the tuning at `2·E` | `{"transpose": k}` where `E` is the note cell plus a constant, else `{"transpose": {"sub": [E, note]}}` |
| a load of a cell | `{"cell": name}` / `{"global": name}` |
| `<<` by a constant | one `shl` node, the mirror of the `shr` §5 already has: an operand is named once and never doubled *k* times |
| `a < b`, `a <= b`, `carry(a, b)` | `carry_out` / `borrow_out` on the difference or the sum |
| `a != 0` on a masked bit | `{"bit": [a, k]}` |
| an inner loop | unrolled to the turns the horizon takes, repetition *j* under the edge that continues it, and a `trap` row past the last |
| a loop index that is a constant per turn | folded into that repetition, which is what makes the presentation's **rerolled sibling copies** lower at all |

**A guard decided outside the phase is the schedule's, not the row's.** Control
dependence is not a path condition: a block a join carries is reached several
ways, and the guard `guardpath` gives is one edge's. The lift keeps only the
terms a branch *inside the same segment* decides — the rest is what `meta.tick`
already says, which is the same claim B6 makes.

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

## 3. The hints

The hints file `--hints` reads is one named datum a line, `meta.commit_order =
[…]`, of the kinds §3.1 lists. **For this tune it is empty: 0 lines.** Every
datum the object carries is derived from the certified artefacts.

What the lift could not lower it refuses rather than approximates, and the
certificate carries the refusals:

| refusal | site | what it is |
| --- | --- | --- |
| `unclassified update` | `$5023`, `$5026`, `$5029`, `$502C` | the entry tick's own reset, which zeroes four per-voice cells through a loop index that is **not** the voice index; the lift has no cell for the store and drops it. The horizon is the evidence it costs nothing: 0 divergences over 11,780 ticks with it gone |

## 4. Coverage

Every number below is `trackerprog.lift.report.json`, written by the command at
the head of this document; none is typed.

| number | value |
| --- | --- |
| store sites of the tick outside the fetch regions | **86** |
| lowered into `sets` rows | **80 rows over 9 streams, 196 assignments** |
| recognised into `Acc` records | **5** — T1's three accumulators joined (§2.3), and two stores of `ins.pw` T1 names no accumulator for |
| refused | **4** (§3) |
| T1 accumulators recognised | **3 of 3**, none refused: `acc_2_lo` (`repeat` + `flag`), `voice[].acc` (`field` + `phase bit`), `rec2[].b5591` (`add` with the carry) |
| T2 recognised | the tuning (80 entries, base 16), the instrument selector (13 records, 6 columns), the score (3 order lists, 32 patterns, 572 events) |
| leaves opened | 437 constants, 324 cells, 32 globals, 10 pitch reads, 7 instrument columns, 0 unnamed |
| score bytes a row supplies | 2 to 4, the row's own bytes and no more |

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

| subtune | ticks | SID writes | divergences | permuted | identical | per-register order |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **11,780** | **133,109** | **0** | 8,370 | 3,410 | identical |

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write.

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

The print and the object against the tune's own load band (§9's acceptance #3):

| measure | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `trackerprog.lift.md` | 1,145 | 14,862 | 1,104 | 7 | 41 | 1,104 | 5,316 |

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| the lifted object, compact | 138,225 | **5,496** |
| — its `score` half | 101,990 | 1,776 |
| — everything else | 36,226 | 3,860 |
| the hand object, song 1 | 53,898 | 3,464 |
| the whole load band | 4,039 | 2,548 |

**2.16× the compressed load band** (`tools/trackerprog_sizes.py --object`),
against the hand's 1.36× and §9.1's 1.25×–2.18× band; `main` at #346 was 2.31×.
The two score halves are within a seventh of each other (1,776 against the
hand's 1,568, over the same 572 events); the whole difference is the sound half,
3,860 against 2,076, which holds 117 declared cells and 80 rows of lowered
arithmetic where the hand holds seven accumulators and four cells. **A lowering
is bigger than a reading, and that is the trade B7 names.**

## 6. Against the hand object

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

## 7. The limits

The recognition changes what T1 names and nothing else. **The unit of the
lowering is unchanged**: one IR block is one row, one SSA temp is one cell, and
the object still carries the tick's arithmetic rather than a reading of it.

| measure | `main` at #346 | this lift | the hand object |
| --- | --- | --- | --- |
| streams / rows | 7 / 84 | 9 / 80 | 3 / 4 |
| `sets` assignments | 257 | 196 | 5 |
| accumulators | 3 reload stand-ins | 3 §5 records + 2 stand-ins | 7 §5 records |
| `state0` cells | 206 (142 SSA · 27 register temps · 23 carry · 14 the tune's) | 117 (85 · 17 · 4 · 11) | 4 |
| largest `sets` expression | 1,282 nodes | 36 | 5 |
| raw / `xz -9e` | 162,579 / 5,892 | 138,225 / 5,496 | 53,898 / 3,464 |
| ratio to the load band | 2.31× | **2.16×** | 1.36× |

What the pass removed: the vibrato's unrolled loop, the slide's two arms, the
pulse run's assignment and the register stores the three fed — 19 of the 23
carry cells and 57 of the 142 SSA temps with them. What the linear shift removed:
the largest expression, 1,282 nodes down to 36. Together, 24,354 raw bytes and
396 compressed ones.

What remains, and what it would take:

| residue | what it is |
| --- | --- |
| 80 rows of lowered arithmetic | the tick outside the fetch regions that T1 names no accumulator over: the drum, the skydive, the arpeggio, the pulse bounce and the note-on. A second recognition pass would need a plane that states them, and T1 does not |
| 117 declared cells | one per SSA temp the rows still read. The lowering's own unit; nothing in the join changes it |
| 2 reload stand-ins | §8 item 3: `ins.pw` is the only instrument-scoped cell the schema can be assigned |
| `rate: 1` on all three records | T1's countdown is already a row and a guard (§4) |

**T1's plane is horizon-dependent, and that bounds the pass.** Over a 1,200-call
prefix T1 refuses both of Commando's recurrences as `divergent recurrence` and
states no accumulator at all, so the join has nothing to join and every store
stays a row. `tests/trackerprog/test_hvsc_lift.py` asserts exactly that at that
horizon; the join itself is exercised on hand-built rows and T1 records in
`tests/trackerprog/test_recognise.py`, and over the whole horizon by the command
at the head of this document.

## 8. What the next family would need

The lift is fail-closed and the other three exemplars with a T0 plane refuse
with a named datum rather than approximating:

| tune | refusal |
| --- | --- |
| *Je suis Linus* (GT2) | `score not cursor-shaped` — the tick proc reaches no fetch region: the regions `region.fetch` finds are a callee's, and the lift lowers one procedure |
| *End of the World* / *Emomyst* (SW) | the same |
| *Guldkorn Intro* (JCH) | `unclassified update` — no row clock steps the voice loop: the per-voice counter this looks for is not the shape JCH's clock has |

Four things stand between this and a second family:

1. **A tick of several procedures.** The lift lowers the one proc `meta.tick_proc`
   names. A callee is a phase like any other, and inlining it is the whole change.
2. **A row clock that is not a per-voice countdown into the fetch.** §3.6 states
   one counter with a `step`, a `boundary` and `reset` clauses; the lift derives
   only the divider-and-countdown value of it, and refuses the rest.
3. **`ins.pw` is the only instrument-scoped cell the schema can be assigned.**
   Every other per-record mutable cell has no `sets` target, and the reload
   accumulator this lift uses for the note-on's pulse writes is a stand-in, not
   a reading.
4. **A plane that names the effects T1 does not.** The recognition (§2.3) joins
   what T1 states, and on this tune that is three of the hand's seven; the other
   four — the drum, the skydive, the arpeggio and the pulse bounce — are §7's
   residue, and no certified artefact names them as accumulators.

The mechanisms themselves are family-free: `tests/trackerprog/test_assemble.py`
and `tests/trackerprog/test_recognise.py` exercise them on hand-built IR, rows
and T1 records with no tune at all, and `tests/trackerprog/test_hvsc_lift.py`
lifts and certifies Commando and asserts the three refusals.
