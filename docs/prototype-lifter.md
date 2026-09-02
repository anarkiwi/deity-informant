# Prototype: the lift from certified artefacts to a trackerprog

The exemplar [prototype-trackerprog.md](prototype-trackerprog.md) §9 says is
missing: a **lift** that produces a trackerprog rather than a hand reading of
one. It answers [trackerprog-backlog.md](trackerprog-backlog.md) **B6** (is the
schedule recoverable?) and attempts **B7** (lower the tick, do not classify it)
on one tune, Commando song 1 — 11,780 ticks, **0 divergences**, no `program`
key, rendered by `deity_informant/trackerprog/universal.py` and certified
against the tune's own player on the PcodeVM. The hints file is **empty**.

```
tools/tuneprog_trackerprog.py --out out/recert-main/commando-song1 \
    --sid $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid --certify
```

Contents: 1 the source · 2 the method · 3 the hints · 4 coverage ·
5 the certificate · 6 against the hand object · 7 what the next family needs.

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
| `<<` by a constant | the adds §5 has |
| `a < b`, `a <= b`, `carry(a, b)` | `carry_out` / `borrow_out` on the difference or the sum |
| `a != 0` on a masked bit | `{"bit": [a, k]}` |
| an inner loop | unrolled to the turns the horizon takes, repetition *j* under the edge that continues it, and a `trap` row past the last |
| a loop index that is a constant per turn | folded into that repetition, which is what makes the presentation's **rerolled sibling copies** lower at all |

**A guard decided outside the phase is the schedule's, not the row's.** Control
dependence is not a path condition: a block a join carries is reached several
ways, and the guard `guardpath` gives is one edge's. The lift keeps only the
terms a branch *inside the same segment* decides — the rest is what `meta.tick`
already says, which is the same claim B6 makes.

**What lies past the tuning is derived, not read.** Commando's frequency table
is fused with the per-voice arrays (commando-trackerprog §4.2). The lift takes
T2's 80 entries as the tuning and asks `cells.py` what holds each byte after
them: **21 words, every one a named cell, no trap** — where the hand states 12
words with 2 traps.

### 2.3 The score, materialised

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

| number | value |
| --- | --- |
| store sites of the tick outside the fetch regions | **86** |
| lowered into `sets` rows | **79** (84 rows over 6 streams, 257 assignments) |
| recognised into `Acc` records | **3** (the instrument-scoped pulse pair) |
| refused | **4** (§3) |
| T1 accumulators, by where their store landed | 1 `acc` (`rec2[].b5591`, T1's `acc2`), 2 `sets` (`acc_2_lo`, `voice[].acc`) |
| T2 recognised | the tuning (80 entries, base 16), the instrument selector (13 records, 6 columns), the score (3 order lists, 32 patterns, 572 events) |
| leaves opened | 1,262 constants, 811 cells, 49 globals, 10 pitch reads, 7 instrument columns, 0 unnamed |
| score bytes a row supplies | 2 to 4, the row's own bytes and no more |

**Two of T1's three accumulators stay as lowered `sets`.** That is the honest
half of B7: the lowering is complete and the *recognition* pass is not. The one
that lands as an `Acc` does so because the schema has no `sets` target for an
instrument-scoped cell, not because the pass recognised a pulse run — it is
stated as `policy: {reload: …}`, an assignment, and carries none of §5's
`delta`, `bound.from: proved` or `rate`.

## 5. The certificate

`trackerprog/attest.py`, §2's comparison over the whole certified horizon
against the tune's own player on `deity_informant.PcodeVM`.

| subtune | ticks | SID writes | divergences | permuted | identical | per-register order |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **11,780** | **133,109** | **0** | 8,370 | 3,410 | **identical** |

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write. `same_per_register_order` holds, which
is **stronger than the hand object on this subtune**: the hand's 105 intermediate
`freq_lo` writes (commando-trackerprog §3) are not dropped here, because the lift
keeps the store the hand's `pitch` modulator folds away.

The print and the object against the tune's own load band (§9's acceptance #3):

| measure | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `trackerprog.lift.md` | 1,177 | 17,845 | 1,136 | 7 | 41 | 1,136 | 5,696 |

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| the lifted object, compact | 162,570 | **5,892** |
| — its `score` half | 101,990 | 1,776 |
| — everything else | 60,580 | 4,268 |
| the hand object, song 1 | 47,313 | 3,464 |
| the whole load band | 4,039 | 2,548 |

**2.31× the compressed load band**, against the hand's 1.36× and §9.1's
1.25×–2.18× band. The two score halves are within a quarter of each other
(1,776 against the hand's 1,444, over the same 572 events); the whole difference
is the sound half, 4,268 against 2,232, which holds 206 declared cells and 84
rows of lowered arithmetic where the hand holds seven accumulators. **A lowering
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
| `streams` | 6, 84 rows of lowered `sets` | 3, 4 rows (`note_on`, `note_off`, `arp`) | the lowering, against the reading |
| `accs` | 3 reload assignments | 7 §5 records with `delta`, `bound`, `policy`, `rate`, `phase` | **B7's residue** |
| `beyond` | 21 words, 0 traps | 12 words, 2 traps | **the lift states more than the hand**: the two traps are the packed row byte, which the lift keeps as a cell (`b54F5`), so it has a word for them |
| `globals` | the tick-level stream | `flags.C` and its proof | different mechanisms: the lift keeps the carry as a cell of the lowered arithmetic, so no producer flag is needed |
| `state0` | 206 cells, 20 globals | 4 cells | every SSA temp is a cell |

Two places where the lift's statement is the better one: the `beyond` words (no
trap), and the write list (`same_per_register_order` on the subtune where the
hand permutes). Everywhere else the hand's is the better statement, and the gap
is exactly the recognition B7 asks for.

## 7. What the next family would need

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
   accumulator this lift uses for the pulse pair is a stand-in, not a reading.
4. **The recognition pass.** T1 states each accumulator's `delta`, `bound`,
   `rate` and `phase`; joining them to the store sites the lowering already
   named would replace whole runs of `sets` rows with §5 records, and is what
   would bring the object back toward the hand's size.

The mechanisms themselves are family-free: `tests/trackerprog/test_assemble.py`
exercises them on hand-built IR with no tune at all, and
`tests/trackerprog/test_hvsc_lift.py` lifts and certifies Commando and asserts
the three refusals.
