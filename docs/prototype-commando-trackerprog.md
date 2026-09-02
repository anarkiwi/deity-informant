# Prototype: Commando as a trackerprog — the oracle reference tune

A **hand transliteration** of the certified Commando tuneprog
([prototype-commando-floor.md](prototype-commando-floor.md) §4) into a
trackerprog ([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered
by one universal player and certified against the tune's own player on the
PcodeVM. No lift, no decompiler, no proposer. It renders: all three subtunes,
11,780 ticks each, **0 divergences** on §2's observable, the raw write lists
*permutations* of the oracle's with each register's own sequence of values
identical. The schema needed thirteen additions (§4), nine of them one datum
each; no new mechanism, and every §5 row Hubbard is cited for held as written.

```
tools/trackerprog_commando.py $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid \
    --song 0 --certify --out out/commando-tp
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 measurements.

---

## 1. The object

`tools/trackerprog_commando.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` renders it.

| section | Commando song 1 |
| --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ctrl, ad, sr)`; `tempo` a divider, `rate = speed + 1 = 3`; `cycles_per_tick 19656` |
| `pitch` | `base 16` and **80** contiguous frequencies — the tune's whole tuning, the same in every subtune. No number outside 16..95 exists anywhere in the object (§4.1) |
| what lies past the tuning | the arpeggio's `beyond` (12 words by overflow distance, 2 traps, the same in every subtune, §4.2); instruments 4 and 7's own `pitch` (§4.3) — expressions over cells, no state |
| `streams` | three: `note_on` (the note row's five sets), `note_off` (the prelude), `arp` (a two-row pitch stream `[0, 12]`) |
| `accs` | seven declared forms, 18 arms across the 9 instruments (§2) |
| `instruments` | **9** — the subtune's reach; the file carries 13 |
| `score` | 3 order programs (64 / 63 / 123 `play` steps, `jump(0)`), 31 patterns, 570 events |
| `globals` | `mode_vol $0F`, the flag `C`'s default, the init and stop write lists |

The seven accumulator ids (`vibrato`, `pulse_run`, `pulse_bounce`, `slide`,
`drum`, `skydive`, `arpeggio`) are labels in the data.

---

## 2. The mapping, line by line

Left column is [prototype-commando-floor.md](prototype-commando-floor.md) §4's
factored form — the certified program. Right column is the object.

| the tuneprog says | the trackerprog says | §5 row |
| --- | --- | --- |
| `FREQ[n]`, the u16 at `$5428 + 2n` | `pitch(n)` — the tuning, total by construction | §3.2 |
| `FREQ[n+1] - FREQ[n]` (the vibrato's interval) | `interval(n)` | §3.2 |
| `FREQ[n+12]` (the arpeggio's octave) | `pitch(n + arp[counter & 1])`, `arp = [0, 12]` | §5 arpeggio |
| a transposition past the tuning | the arpeggio's own `beyond`, by overflow distance | new (§4.2) |
| a sound that is no pitch | the instrument's own `pitch` modulator; the event carries no note | new (§4.3) |
| `INS[i]`, 8 columns | `Ins{adsr, wave, pw, prelude, accs}` | §3.5 |
| `TRACK[v]` / `PAT[p]` | `score.orders` / `score.patterns` of events | §3.6 |
| `speedctr`, `speed` | `meta.tempo` — a divider, `rate = 3` | §3.6 |
| `row & $1F` | the event's `dur`, in row ticks | §3.6 |
| `row & $20` | the event's `tie`: it disarms the prelude | new (§4.8) |
| `row & $40` | the event's `sounds` — this family's only gate token | §3.6 |
| the extra byte `< $80` | the event's `ins` | §3.6 |
| `$518B` hard cut | the instrument's **prelude**, `early = 1` row tick, rows `set(ctrl, wave & $FE) set(ad,0) set(sr,0)` | §3.5 |
| `ins.vib ≠ 0` | `Acc(freq, repeat(interval(note) >> (vib+1), fold(counter, 7)), policy reload(pitch[note]))` | §5 vibrato, stateless phase |
| `ins.fx & 8` | `Acc(pw_lo, const(pspeed) + flag(C), width 8, wrap, scope instrument)` | §5 pulse run |
| `ins.pspeed ≠ 0` | `Acc(pw, const(pspeed & $E0), width 12, reflect, amplitude [$800,$EFF] >> 8, bound [0,$FFF] projected, rate (pspeed & $1F)+1, phase cell pwdir, scope instrument)` | §5 pulse sweep |
| the extra byte `≥ $80` | `arm(slide, {delta, phase})` on the event — unpacked at build time, never at run time | §3.6 |
| `voice.porta ≠ 0` | `Acc(freq, const(<delta>), phase const(<phase>), wrap, scope voice)`, armed by the score | §5 free slide |
| `ins.fx & 1` | `Acc(freq_hi, const(-1), emit entry)` plus two ctrl rows its own guard selects | new (§4.5, §4.6) |
| `ins.fx & 4` | `Acc(freq, policy reload(pitch[note + arp[counter & 1]]))` | §5 arpeggio |
| `ins.fx & 2` | `skydive`, declared with its guard and `trap: true` | §5 (struck row, kept as data) |
| `trkptr` `$FF` / `$FE` | the order program's `jump(0)` / `stop` | §3.6 |

The three routines the print names disappear: `fetch` is the sequencer step,
`soundwork` the accumulator list in rank order, `voices`' `X = 2,1,0` loop
`meta.voice_order`. The SID register offset `$54EB`, the scratch pair
`$550A/$550B`, the `PHA`/`PLA` pulse spill and `$5504`'s saved `X` go with them:
not one byte of the object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole horizon
against the tune's own player on `deity_informant.PcodeVM`.

| subtune | ins | patterns | events | tuning | `beyond` | acc arms | ticks | SID writes | divergences |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9 | 31 | 570 | 80 | 12 | 18 | 11,780 | 133,109 | **0** |
| 2 | 4 | 10 | 118 | 80 | 12 | 8 | 11,780 | 128,953 | **0** |
| 3 | 1 | 4 | 61 | 80 | 12 | 4 | 11,780 | 7,219 | **0** |

The tuning and the `beyond` are the same object in all three rows: only the
score and the instruments change with the subtune. 133,109 is the write count
[prototype-commando-floor.md](prototype-commando-floor.md) §2.2 measures on the
trace, to the write.

**Stronger than §2, where it holds.** Every tick's two write lists have the same
multiset, and on subtunes 2 and 3 every register's own sequence of values is
identical (`same_per_register_order`); the whole difference there is where
`ctrl` sits relative to `pw` on a note-on tick, because §4's `commit` emits
producers before edge registers and Hubbard emits `ctrl, pw_lo, pw_hi, ad, sr`.
Subtune 1 is the exception, and §4.3 says why: **105 of 11,780 ticks** differ in
one write, all on `freq_lo`, each immediately superseded by another to the same
register with the tick's final value identical. Subtune 3 is not in the
certified set and earns its place anyway: its order program ends in `stop`,
which subtunes 1 and 2 never reach inside the horizon.

---

## 4. What the spec needed

Thirteen additions. Nine are one datum each; §4.1 to §4.4 are one rule made
explicit. None is a player branch and none is a new mechanism.

### 4.1 A pitch table is a pitch table, and the modulators are expressions

`"pitch": {"base": 16, "freq": [$02BD, $02E7, …, $FD2E]}` — the tune's **whole
tuning**, 80 contiguous constants, the same in every subtune, not the notes some
melody plays. No number outside 16..95 appears anywhere: not in the score, not
in a table, not as an index. The vibrato and the arpeggio keep no tables of
their own, and the two things this tune does that are not pitch are each the
modulator's own (§4.2, §4.3) over cells it names outright (§4.4).

### 4.2 The arpeggio owns what it does past the tuning

Commando's frequency table is fused with the six per-voice arrays: `$5448`'s 202
bytes tune notes 16..95 and then become state (commando-floor §5), so
transposing an octave up from the tuning's top twelve notes runs off the end.
The edge the arpeggio runs off is the **tuning's**, not its `bound`, which is
the 16-bit store the cell keeps.

```
[5] arpeggio      freq  w16  tick   scope voice   bound [0, $FFFF] projected
      beyond  past the tuning, by how far past it the transposition went
            0  u16(0, 7)
            1  u16(14, voicebase)
            2  u16(orderpos[0], orderpos[1])
            3  u16(orderpos[2], patrow[0])
            4  u16(patrow[1], patrow[2])
            5  u16(rowsleft[0], rowsleft[1])
            6  trap: the packed row byte, which the score keeps as an event's own fields
            7  trap: the packed row byte, which the score keeps as an event's own fields
            8  u16(wave[0], wave[1])
            9  u16(wave[2], note[0])
           10  u16(note[1], note[2])
           11  u16(ins[0], ins[1])
```

It is the arpeggio's because the arpeggio is the only thing that asks, measured
by poisoning each value: every one that reaches a register is an arpeggio
output, and the vibrato's two are dead because every instrument whose vibrato
could step past the tuning also arpeggiates over it. So **the tuning has no
interval above its top note**. Indexed by distance and not by note, the words
are a property of the tune's memory layout, so **the same `beyond` appears in
every subtune**; the two traps are the three bytes of the packed row byte the
score no longer keeps.

### 4.3 An instrument owns what is not a pitch at all

Instruments 4 and 7 sound a drum whose frequency is the waveforms the other
voices are sounding. That is no pitch, so the score gives those events no note
and the frequency comes from a modulator on the instrument:

```
   4  $0F  $C4   $43  $0200  drum skydive
    pitch   this instrument's sound is no pitch; it is its own
        value     u16(wave[0], wave[1])
   7  $0D  $FB   $15  $0180  vibrato(shift 2) drum arpeggio
    pitch   this instrument's sound is no pitch; it is its own
        value     u16(wave[0], wave[1])
        octave    u16(pwdir[0], pwdir[1])
```

Each carries exactly what its own modulators ask of a pitch: the frequency, and
the octave the arpeggio jumps to where the instrument arpeggiates (instrument 4
arpeggiates nothing, so it has no octave). Neither has an interval: **an
unpitched sound has no semitone above it, so a vibrato over it steps by
nothing.** Hubbard's routine does step, by whatever lies past his tuning, and
the instrument's own arpeggio overwrites the result in the same tick; the object
drops that intermediate write, which is §3's 105 ticks.

### 4.4 A modulator reads, and names the voice it reads

Such a modulator is **expressions and nothing else** — §5 nodes over §5's cells,
no private state and no event. What lies past this tuning is the engine's
per-voice state, so a word says which voice: `{"cell": [name, voice]}` beside
`{"cell": name}`, the same name, space and half, read on the voice the word
names. The region opens with the routine's own index table, `$54E8..$54EA`, and
the scalar at `$54EB` holding the entry of the voice being run — a voice cell
like any other, `voicebase`, seeded from the table and written by nothing, so
the `X = 7v` index is a byte of the tune's memory. A word depending on nothing
live folds to a number at build time.

**No packed byte survives, and a cursor over one is the row program's.** The row
byte's bit fields are separate event columns and the portamento byte is unpacked
at build time; the carry no producer leaves, off the instrument index's own
third shift, folds to `0` with its proof recorded. This tune's own **byte**
cursor into a pattern, where the trackerprog's cursor counts events, is two
`meta.row` steps over a cell of its own — `@patrow := patrow + (1 + (<sounds> +
<field>))` and `@patrow := 0 when <wraps> != 0` — so `wraps` is a fact of the
row like `sounds` and `field`, and no event carries a column for it.

### 4.5 An instrument has a note row, whether or not it has a prelude

Hubbard's prelude is `null`, and the five sets a note-on emits (`ctrl = wave &
gate`, `pw_lo`, `pw_hi`, `ad`, `sr`) are a `{stream}` step of `meta.row`
instead, reading the instrument's own columns and its **live** pw cell.

### 4.6 `Acc.emit: entry`

The drum writes `freq_hi` and *then* decrements it, so the producer sends the
value the tick came in with; the epoch is not inferable from the cell.

### 4.7 An `Acc` may carry the gate rows its own guard selects

The drum's first row tick sets `ctrl = $80` (TEST, gate off) and every later
tick `ctrl = wave & $FE`, under the guard that decides whether it steps:
`gate: {true: [...], false: [...]}`.

### 4.8 `meta.row_consumes_tick`

On the tick a voice takes a new row, its accumulators do not run. One bit.

### 4.9 `Event.tie`, and `sounds` rather than `gate`

The prelude belongs to the instrument; whether it fires belongs to the **row**,
bit 5. Bit 6 is `sounds`, and the row byte has no gate token of its own, so
every event here has `gate: none` — §4.3's drum rows then saying what they are,
`sounds: true, note: none`.

### 4.10 `Acc.when` and `Acc.delta_when`

The drum needs three guards and the vibrato's delta one (`dur >= 6`).

### 4.11 The flag's producer, not only its consumer

§5 gives the consumer a live carry, `const(Δ) + flag(C)`. The producer is the
one carry in the layer that is **not** an expression: the vibrato's `repeat`
loop leaves `C`, and it is the carry of the *last* of its `n` additions, which
no expression over the value the loop stored recovers. So `flag: {name: "C",
seed: 1}`, the seed being what the loop leaves when it does not run — the
compare's own carry — and `globals.flags.C.default` what `C` is when no vibrato
is armed at all (`bit(ins, 5)`, the residue of the instrument index's three
shifts, always 0 here and stated rather than assumed). Rendering the seed as 0
diverges on **11,747 of subtune 2's 11,780** ticks and 329 of subtune 3's, and
deleting the carry term diverges on the same 11,747: it is the write that makes
the subtunes aperiodic (architecture §5.2), exercised on 2,106 ticks.

### 4.12 `stop` is four writes and an abandoned tick

`$D404 = $D40B = $D412 = 0`, `$D418 = $0F`, on the tick *after* the one the
terminator was read on, which is abandoned mid-voice-loop. Subtune 3 exercises
it.

### 4.13 `trap`: the arm the horizon never takes

The skydive row is kept as data — its guard, and `trap: true` — and the player
raises if it is ever taken.

---

## 5. What the spec got right

Every §5 row Hubbard is cited for held **exactly as written**, and two carry
family evidence the spec does not: the vibrato's fold (`counter & 7`, `^ 7`
above 3) is the triangle, its shift count the instrument byte and its `dur >= 6`
guard the delta's rather than the producer's; and the pulse sweep's `[$800,
$EFF]` is the `amplitude` it turns at and not the interval the cell keeps — a
step of `$E0` from `$E60` lands at `$F40`, which the turn's own `>> 8` does not
see, and the step after wraps to `$020`, so 10 moves of song 1's 6,800 leave the
window, the first at tick 3,457. `tabcell` and the sign-extended table entry
(§10) are not exercised here.

---

## 6. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks of a
presentation:

| subtune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 788 | 5,037 | 748 | 7 | 40 | 748 | 3,424 |
| 2 | 267 | 1,697 | 248 | 7 | 19 | 248 | 2,368 |
| 3 | 195 | 1,255 | 182 | 7 | 13 | 182 | 2,188 |

Statements equal data rows by construction. Song 1's 788 lines against the
source print's 414 is the trade: 252 code lines become 60 lines of instruments,
accumulators and generators, and the rest is the score and the tuning printed
*as data*, which the tuneprog never printed. `xz -9e` of the object against the
tune's own PSID load band, §9's acceptance #3:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, song 1, compact | 47,313 | **3,520** |
| — its `score` half | 39,172 | 1,444 |
| — everything else (tuning, accs with their own state, instruments) | 8,132 | 2,232 |
| the whole load band | 4,039 | 2,548 |
| the tune's data floor (commando-floor §2.2) | 1,941 | 1,112 |
| `trackerprog.json`, song 2 / song 3 | 14,754 / 10,591 | 2,424 / 2,236 |

Song 1's object is **1.43×** the compressed load band; the current table over
all thirty builds is [prototype-trackerprog.md](prototype-trackerprog.md) §9.1.
The score half alone lands within 40 % of the tune's own compressed data.
Subtunes 2 and 3 carry the tune's whole tuning and the whole `beyond` rather
than the slice their own melody touches: those two sections are the same object
in all three, and the JSON's raw size is key repetition. Against the floor, the
player pseudocode [playroutine-anatomy.md](playroutine-anatomy.md) §3.1.3 is 65
lines and covers all three songs and every fx bit; the universal player covers
*no* song.
