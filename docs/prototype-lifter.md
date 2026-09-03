# Prototype: the trackerprog as a binding of a certified tune's planes

There is one player — `deity_informant/trackerprog/universal.py` — with a fixed
tick procedure ([prototype-trackerprog.md](prototype-trackerprog.md) §4), a
fixed state vector and a fixed set of things it reads. A certified tuneprog has
those same things under other names, and the planes already name them. A
**trackerprog is the binding of the tune's named state and tables to the
player's slots**, rendered into §3's schema: no lowered row, no SSA temp, no
cell named by an address, and the tick is not inferred because it is the
player's.

| tune | schedule | score | records | certificate |
| --- | --- | --- | --- | --- |
| Commando song 1 (Hubbard) | derived (§2) | bound field for field (§3) | 3 of 3 T1 states (§4) | **0 divergences over 11,780 ticks, 133,109 writes** |
| *Guldkornekspressen Intro* (JCH V20) | derived (§2) | **no field binds** (§7) | 2 of 5 (§4) | **tick 0 diverges** (§7) |

```
tools/tuneprog_score.py --out <out>/commando-song1 --calls 11780
tools/tuneprog_trackerprog.py --out <out>/commando-song1 \
    --sid $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid --certify
tools/tuneprog_score.py --out <out>/jch-guldkorn-intro --calls 2401
tools/tuneprog_trackerprog.py --out <out>/jch-guldkorn-intro \
    --sid $HVSC/MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid --certify
```

The binding reads `tuneprog.S4/S6/T0/T1/T2.json`, `certificate.json` and the
post-init image, and nothing else; T2 is read at the horizon it was computed
over, so its score is recomputed at the certified horizon first.

**0 divergences on a lowering was not evidence of abstraction; on a binding it
is.** A lowering re-implements the tick per tune, so rendering it back is
guaranteed by construction and says only that the transliteration is faithful.
A binding carries no tick at all — the object is the planes' own data in the
player's slots — so rendering the tune's observable is a claim about the *tune*:
that it is an instance of the one player.

Contents: 1 the binding, field by field · 2 the schedule · 3 the score ·
4 the records · 5 the rows that are left · 6 against the hand object ·
7 the second family · 8 what was replaced, measured.

---

## 1. The binding, field by field

`deity_informant/trackerprog/{bind,rows,events,records,shape,sections}.py`, on
top of `read.py` (the expression and guard reader) and `schedule.py` (B6).

| object | plane | what it supplied |
| --- | --- | --- |
| `meta.voice_order`, `commit_order`, `tick`, `tempo`, `row_consumes_tick` | B6 over S4 and T0 | the voice loop, the segments, the clock (§2) |
| `meta.tempo.cell` = `rowsleft`; `dur` | the clock cell, where the fetch reloads it | the player's own row clock, reloaded by the event's own length |
| `pitch` | T2 | `base` and the entries, verbatim |
| `score.orders` | T2's order cursor, replayed | one `play` step per value the tune's own cursor took |
| `score.patterns` | the fetch's own stores, replayed | §3.6's fields (§3) |
| `instruments` | T2's selector | one record per entry the horizon selected, its columns off the image |
| `instruments[k].pitch` | §3.2's words past the tuning | where the score's note is past the tuning's top |
| `accs` | T1 | §5's record, each field read where the tune's own site reads it (§4) |
| `streams`, `meta.row` | T0's write sites and the tick's own stores | the register writes and the cell moves T1 states no record over (§5) |
| `globals.streams` | the blocks outside the voice loop | the tick-level channel |
| `state0` | the post-init image | the seed of every cell the object still reads |

The player's own eight voice cells are **bound, not declared**: `note` is the
cell that indexes the tuning (`tables.note_base`), `ins` the selector's index
cell (T2), `rowsleft` the clock (B6), `orderpos` T2's order cursor, `freq` the
per-voice pair a T1 record whose target is the frequency register moves, and
`wave` the cell an instrument column reaches a `ctrl` write through. Where the
tick reads a role off a scratch it copies a per-voice cell into, the slot is
bound to the cell it was copied from.

---

## 2. The schedule (B6)

`schedule.derive`, unchanged from #351 and stated there. Commando's, datum for
datum against `tools/trackerprog_commando.py`:

| datum | bound | hand | |
| --- | --- | --- | --- |
| `voice_order` / `commit_order` | `[2, 1, 0]` / `(ctrl, ad, sr)` | the same | same |
| segments | `prelude` 8 blocks · `row` 17 · `machine` 59 | — | — |
| `meta.tick` | `{stream: prelude0}` `{stream: prelude2}` `commit` `row` `commit` `machine` | `prelude` `commit` `row` `commit` `machine` | **differs by one datum**: the instrument's lead-in is a `{stream}` phase whose own rows carry the guard the hand puts in `tempo.early` |
| `tempo.cell` / `step` / `rate` / `phase` | `rowsleft` / `−1` / 3 / 0 | the same | **identical** |
| `tempo.boundary` | `((phase − 1) & $FF) & $80 != 0` | `rowsleft >= $80` | the same guard, over the clock's own step |
| `row_consumes_tick` | `true` | `true` | same |

The tempo cell is the player's `rowsleft` **only where the fetch reloads it**:
that reload is what `dur` is bound from, and a tune whose clock the tick keeps
for itself has no `dur` field and keeps the counter under its own name.

---

## 3. The score, bound field for field

`events.py`. A visit of a fetch region is one row of one voice, and the fields
of §3.6 are the values it stored into the player's own cells: `dur` into the
clock's, `note` into the cell that indexes the tuning, `ins` into the selector's
index, `sounds` whether it stored a note at all. Where a guard reads a **masked
field of a byte the fetch supplied**, the field it is is decided by what the
horizon's own visits say — `dur`, `note`, `ins`, `sounds`, `newins`, `field`,
`wraps` — and the one such field no other explains is the row's `tie`, which is
what disarms an instrument's prelude. The field list is closed: a candidate none
of them explains is left unbound and the certificate names it.

Two consequences the object carries:

* **the play list is the score's own list, not the walk.** A visit belongs to the
  step of the order program the tune's cursor was on, so a second turn of one
  step is that step: Commando's lists are 64 / 63 / 123 steps, the hand's own.
* **the packed row byte is the event's fields and no cell.** It is what supplies
  `dur`, `sounds` and `tie`, so nothing carries it; the word past the tuning that
  would read one is a `trap`, exactly as the hand states it.

A cell the fetch writes that the fields do not cover — Commando's portamento
byte — is one §3.6 command a row carries, `arm.sets` over that named cell.

---

## 4. The records (T1 into §5)

`records.py`. Each field is read where the tune's own site reads it, not where
the record is stated:

| §5 field | bound from |
| --- | --- |
| `cell` | T1's own cell, as the object names it: a voice cell, `ins.pw.<half>`, or the global S6's `u16` names |
| `delta` | T1's `const`/`field`/`tabcell`/`repeat`, over a named cell or an `{"ins": column}`; a `tablestep` is `{"shr": [{"interval": null}, k]}`, where `k` is the count the shift loop's own counter enters with plus one where its test follows its body |
| `policy` | T1's, with a `reload` read at the block that reloads, its halves joined into the one word they are |
| `produce` | the T0 writes whose cells are the record's own; a 16-bit produce states both stores of the pair |
| `when` / `delta_when` | the terms every site stands under, split at the produce: a term the step stands under and the produce does not is `delta_when`, so the record still produces on a tick its delta does not apply |
| `phase` | T1's `bit(cell, k)` over the object's cell |
| `flag` | §5's carry, where another record's delta reads one |

Commando's three, against the hand's own records:

| T1 | the hand calls it | `cell` | `policy` | `produce` | |
| --- | --- | --- | --- | --- | --- |
| `acc0` | `vibrato` | `#acc_2` | `reload {transpose: 0}` | `freq_lo`/`freq_hi` | the hand's cell is `tick`, the per-tick scratch; the delta is `repeat(interval >> (ins.vib + 1), n)` either way |
| `acc1` | `slide` | `freq` | `wrap` | `freq_lo`/`freq_hi` | **identical**, `delta field(porta, $7E)`, `phase bit(porta, 0)` |
| `acc2` | `pulse_run` | `ins.pw.lo` | `wrap` | `pw_lo` | **identical**, `delta {ins: pspeed} + flag` |

**T1's plane is horizon-dependent, and that bounds the binding.** Over a
1,200-call prefix T1 states none of Commando's recurrences (`divergent
recurrence`), so the object states no record and does not render: the binding
carries what the planes state and nothing else.

---

## 5. The rows that are left

`rows.py`. A store site T1 states no record over is one row of a §3.3 stream, or
one step of §3.6's row program, over named cells alone. One block is one row and
nothing is unrolled; three rules keep an SSA temp out of the object:

| rule | what it is |
| --- | --- |
| a name two paths bind differently splits the row | one row per binding, under the guard of the block that bound it — Commando's pulse sweep is its two arms, its arpeggio its two transpositions |
| a store whose value is its own cell's is never folded | a counter is state the tune carries between ticks, so the row states it and no reader inlines it |
| a value read one statement before a store moved the cell it reads stands before that store | inside a row (`_epoch`) and across the rows of a segment (`_staged`), and where the object has since stored that value, the guard reads the cell it left it in |

What is left after liveness is the residue: Commando's object carries **26 rows
of 50 assignments over 17 streams**, and **18 cells** — 10 per-voice and 8
global, every one a cell of the tune's own state that S6 names.

---

## 6. Against the hand object

`out/bind/commando-song1/trackerprog.lift.json` against
`tools/trackerprog_commando.py`, section by section.

| section | bound | hand | |
| --- | --- | --- | --- |
| `meta.voice_order` / `commit_order` / `voices` / `cycles_per_tick` | `[2,1,0]` / `(ctrl,ad,sr)` / 3 / 19,656 | the same | **identical** |
| `meta.tempo` | `rowsleft`, step −1, rate 3, phase 0 | the same | **identical but the boundary's spelling** (§2) |
| `meta.tick` | 6 entries, the lead-in two `{stream}` phases | 5, the lead-in `prelude` | **differs by one datum** (§2) |
| `pitch` | base 16, 80 entries | the same | **identical** |
| `score.orders` | 64 / 63 / 123 `play` steps, `jump(0)` | the same | **identical** |
| `score.patterns` | 32 patterns, 572 events | 31, 570 | one pattern more: a visit whose fields differ from the stored row's is its own pattern |
| `instruments` | 10 records, 6 columns and `pw` | 9, `adsr`/`wave`/`pw` | the same set and one more; the columns are named by T2 and not by the family |
| `accs` | 3 §5 records | 7 | §4: T1 states three, and the other four are rows (§5) |
| `streams` / rows / `sets` | 17 / 26 / 50 | 3 / 4 / 11 | the residue T1 states no record over |
| `beyond` | 12 words, 2 traps | 12, 2 | **identical**, and derived |
| `state0` | 10 cells, 8 globals | 4 cells | every one a cell S6 names; six keep S6's own address-derived name where S6 gives no role name |
| largest `sets` expression | 30 nodes | 4 | the pulse sweep's own bound test |
| raw / `xz -9e` | 68,893 / **3,608** | 53,898 / 3,464 | |
| ratio to the load band (2,548) | **1.42×** | 1.36× | |

---

## 7. The second family, through the same emitter

*Guldkornekspressen Intro* runs through the binding with no family branch. B6
derives its schedule — `commit_order (ad, sr, ctrl)`, the counter the tick steps
outside the voice loop, step −1, a boundary of three terms and one `reset`
clause whose value is the tune's own speed, 3 — and T2's selector gives its 19
instrument records and its 95-entry tuning. Two of T1's five records render.
The object is 3,312 compressed bytes, 1.34× its 2,472-byte band.

**It diverges at tick 0**, and this is the field it names:

| | |
| --- | --- |
| tick | 0, voice 0 |
| register | `$D400`/`$D401`, the voice's frequency |
| expected | `00=16 01=01` — 278 |
| got | `00=5A 01=04` — 1,114 |
| T0 site | write 9, `p_1616` block `L1616_A9$r1` pc `$1639`, `sid[x].freq = voice[x].freq` |
| the cell it wrote | `voice[].freq`, which the row's own note commits |
| the field the object did not carry | §3.6's `note` — and with it `ins`, `dur` and `sounds` |

The field has a role and the planes state it: S6 names the cell that indexes the
tuning (`$1014`) and T2 names the pattern channel that feeds it. What the
binding's rule cannot reach is **where** the value is stored. JCH stages its row:
the fetch region keeps the pattern's bytes in a zero-page temp and the voice's
own staging cells, and the store into the cell that indexes the tuning stands in
block `L110C_4C` of the **machine** segment, one clock step later. The rule
"`note` is the value the fetch stored into that cell" therefore finds no store,
and all 790 events state `sounds: false, note: null, ins: null, dur: 0`.

The datum that would carry it is one §3.6/§4.1 already has and the binding does
not derive: **`meta.stage`** — the same row program run at the `fetch` phase,
one tick position earlier, over the row the clock runs ahead of. That, and the
nine joins no path folds (`jL1109_4C`, `jL1201_AD`, `jL1409_DE`, `jL14F7_0A`,
`jL14FD_18`, `jL1508_8C`, `jL1580_4C`, `jL1616_A9`, `jL1660_CA`), are what a
second family costs. Neither needs a player mechanism or a schema form §3 does
not have.

The families whose tick is several procedures — *Je suis Linus* (GoatTracker 2)
and *Emomyst* (SID Wizard) — refuse at B6 by name, `score not cursor-shaped: the
tick reaches no fetch region`: the binding does not inline a callee, and the
fetch regions stand in a procedure the tick proc does not hold.

---

## 8. What was replaced, measured

The lowering of #346–#351 turned every IR block of the tick into one guarded row
of `sets` and every SSA temp into a cell, then tried to recognise accumulators on
top. It is deleted (`lower.py`, `flow.py`, `unroll.py`, `recognise.py`,
`algebra.py`, `report.py`, `callee.py`, `assemble.py`), and this is why:

| measure | the lowering, at #351 | the binding | the hand |
| --- | --- | --- | --- |
| rows / `sets` (Commando) | 86 / 207 | 26 / 50 | 4 / 11 |
| `state0` cells | 123 | 18 | 4 |
| `xz -9e`, and the ratio | 6,280 — **2.46×** | 3,608 — **1.42×** | 3,464 — 1.36× |
| ratio, *Guldkorn* | 3.46× | 1.34× | 1.90× |
| ratio, *Je suis Linus* | 4.71× | refused at B6 | 2.18× |
| accumulators joined | 3 of 7 Commando, 0 of 7 JCH, 0 of 9 GT2 | 3, 2 | 7, 7 |

The player and the planes are untouched by this pass: `universal.py`,
`compiler.py` and everything under `deity_informant/tuneprog/` are unchanged
against #351, so there is no poison harness run and no recertification to make —
the binding needed neither a player mechanism nor a plane change.
