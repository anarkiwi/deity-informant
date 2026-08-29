# Prototype: Commando as a trackerprog — the oracle reference tune

A **hand transliteration** of the certified Commando tuneprog
([prototype-commando-floor.md](prototype-commando-floor.md) §4) into a
trackerprog ([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered
by one universal player (§4, §5) and certified against the tune's own player on
the PcodeVM. No lift, no decompiler, no proposer: every structure below is the
factored form's own text restated one layer up, by hand, and the point of the
exercise is what that costs.

Two results:

1. **It renders.** All three Commando subtunes, 11,780 ticks each, **0
   divergences** on §2's observable — and the raw ordered write lists are
   *permutations* of the oracle's on every tick, with each register's own
   sequence of values identical. The only difference is the interleave §2 drops.
2. **The schema needs twelve additions**, listed in §4. Nine are one datum
   each. The other three are the layer's own discipline made explicit: a pitch
   table holds pitch and nothing else, with each modulator keeping its own table
   over the same rows; a played index that is not a note belongs to the
   instrument that plays it; and where a value is not a table read it is a
   **generator**, a self-contained machine with private state fed by the
   player's published events. Nothing in the tune needed a new *mechanism*: every §5 row
   Hubbard is cited for held exactly as written.

Reproduce:

```
tools/trackerprog_commando.py $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid \
    --song 0 --certify --out out/commando-tp
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 measurements.

---

## 1. The object

`tools/trackerprog_commando.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` (396 lines, no tune, no family, no
table of its own) renders it. The object is §3's seven sections:

| section | Commando song 1 |
| --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ctrl, ad, sr)`; `tempo` a divider, `rate = speed + 1 = 3`; `cycles_per_tick 19656` |
| `pitch` | **69 notes**: a note number and its frequency, all constants. The vibrato's `interval` and the arpeggio's `octave` are those accumulators' own tables (§4.1) |
| `generators` | **8**, each with private state, its event subscriptions and a value; subtunes 2 and 3 need **none** (§4.3) |
| seeds | instruments 4 and 7 carry one: a played index that is no note (§4.2) |
| `streams` | three: `note_on` (the note row's five sets), `note_off` (the prelude), `arp` (a two-row pitch stream `[0, 12]`) |
| `accs` | seven declared forms, 18 arms across the 9 instruments (§2) |
| `instruments` | **9** — the subtune's reach; the file carries 13 |
| `score` | 3 order programs (64 / 63 / 123 `play` steps, `jump(0)`), 31 patterns, 570 events |
| `globals` | `mode_vol $0F`, the flag `C`'s default, the init and stop write lists |

The player has one dispatch, and it is on the *form* of a delta — `const`,
`field`, `tablestep`, `repeat`, `add`, `flag` — never on the name of an effect.
The seven accumulator ids (`vibrato`, `pulse_run`, `pulse_bounce`, `slide`,
`drum`, `skydive`, `arpeggio`) are labels in the data.

---

## 2. The mapping, line by line

Left column is [prototype-commando-floor.md](prototype-commando-floor.md) §4's
factored form — the certified program. Right column is the object.

| the tuneprog says | the trackerprog says | §5 row |
| --- | --- | --- |
| `FREQ[n]`, the u16 at `$5428 + 2n` | `pitch.freq[i]`, `i` a row of the tuning | §3.2 |
| `FREQ[n+1] - FREQ[n]` (the vibrato's interval) | `vibrato.interval[i]` — the accumulator's own table | new (§4.1) |
| `FREQ[n+12]` (the arpeggio's octave) | `arpeggio.octave[i]` — the accumulator's own table | new (§4.1) |
| a played index outside the tuning | the instrument's `seed` | new (§4.2) |
| the value such an index carried | a **generator**: private state, published events, a value | new (§4.3) |
| `INS[i]`, 8 columns | `Ins{adsr, wave, pw, prelude, accs}` | §3.5 |
| `TRACK[v]` / `PAT[p]` | `score.orders` / `score.patterns` of events | §3.6 |
| `speedctr`, `speed` | `meta.tempo` — a divider, `rate = 3` | §3.3 |
| `row & $1F` | the event's `dur`, in row ticks | §3.6 |
| `row & $20` | the event's `tie`: it disarms the prelude | new (§4.8) |
| `row & $40` | the event's `gate: off` — a keyoff | §3.6 |
| the extra byte `< $80` | the event's `ins` | §3.6 |
| `$518B` hard cut | the instrument's **prelude**, `early = 1` row tick, rows `set(ctrl, wave & $FE) set(ad,0) set(sr,0)` | §3.5 |
| `ins.vib ≠ 0` | `Acc(freq, repeat(tablestep(pitch, note, vib+1), phase(fold(counter,7))), policy reload(pitch[note]))` | §5 vibrato, stateless phase |
| `ins.fx & 8` | `Acc(pw_lo, const(pspeed) + carry(C), width 8, wrap, scope instrument)` | §5 pulse run |
| `ins.pspeed ≠ 0` | `Acc(pw, const(pspeed & $E0), width 12, reflect, bound [$800,$EFF] projected, rate (pspeed & $1F)+1, phase cell pwdir, scope instrument)` | §5 pulse sweep |
| the extra byte `≥ $80` | `arm(slide, {delta, phase})` on the event — the byte is unpacked at build time, never at run time | §3.6 |
| `voice.porta ≠ 0` | `Acc(freq, const(<delta>), phase const(<phase>), wrap, scope voice)`, armed by the score | §5 free slide |
| `ins.fx & 1` | `Acc(freq_hi, const(-1), emit entry)` plus two ctrl rows its own guard selects | new (§4.5, §4.6) |
| `ins.fx & 4` | `Acc(freq, policy reload(pitch[note + arp[counter & 1]]))` | §5 arpeggio |
| `ins.fx & 2` | `skydive`, declared with its guard and `trap: true` | §5 (struck row, kept as data) |
| `trkptr` `$FF` / `$FE` | the order program's `jump(0)` / `stop` | §3.6 |

The three routines the print names disappear: `fetch` is the sequencer step,
`soundwork` is the accumulator list in rank order, and `voices`' `X = 2,1,0`
loop is `meta.voice_order`. The SID register offset `$54EB`, the scratch pair
`$550A/$550B`, the `PHA`/`PLA` pulse spill and `$5504`'s saved `X` are gone with
them entirely: not one byte of the object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole horizon,
the reference being the tune's own player on `deity_informant.PcodeVM` — the
same interpreter the tuneprog certificate is verified against.

| subtune | ins | patterns | events | notes | gens | acc arms | ticks | SID writes | divergences | identical ticks | permuted ticks |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9 | 31 | 570 | 69 | 8 | 18 | 11,780 | 133,109 | **0** | 3,410 | 8,370 |
| 2 | 4 | 10 | 118 | 30 | 0 | 8 | 11,780 | 128,953 | **0** | 2,944 | 8,836 |
| 3 | 1 | 4 | 61 | 22 | 0 | 4 | 11,780 | 7,219 | **0** | 11,561 | 219 |

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write.

**A claim stronger than §2, and free.** On every tick of every subtune the two
sides' write lists have the same *multiset*, and for every register the sequence
of values it is written is identical (`same_per_register_order`). The whole
difference is where `ctrl` sits relative to `pw` on a note-on tick, because §4's
`commit` emits producers (step 4) before the edge registers (step 5) and Hubbard
emits `ctrl, pw_lo, pw_hi, ad, sr`. That is exactly the idiom §2 cites as its
reason for dropping cross-class order — measured here, not assumed.

Subtune 3 is not in the certified set and earns its place anyway: its order
program ends in `stop`, which subtunes 1 and 2 never reach inside the horizon.

---

## 4. What the spec needed

Twelve additions. Nine are one datum each; §4.1, §4.2 and §4.3 are the layer's
own discipline made explicit. None is a player branch and none is a new mechanism.

### 4.1 A pitch table is a pitch table

§3.2 says `pitch: [u16; N]` and "every *note* elsewhere is an index into this
table or a signed index offset". The offset is the problem. Commando's table is
the frequency table **fused with the six per-voice arrays** — `$5448`'s 202
bytes tune notes 16..95 and then become state — and the tune's own arithmetic
walks off the end (commando-floor §5, "const is refuted by the tune"). Two
producers do the walking: the vibrato's `FREQ[n+1] - FREQ[n]` and the
arpeggio's `FREQ[n+12]`.

Neither belongs in the tuning. They are **transformations that sit on top of
pitch**, so each accumulator keeps its own table over the same rows:

```jsonc
"pitch": {"notes": [16, 18, …, 95],       // 69 note numbers
          "freq":  [$02BD, $0313, …],     // and their frequencies. Nothing else.
          "index": {"16": 0, …}}

"accs": {"vibrato":  {…, "interval": [$2A, $2F, …]},    // the interval above a note
         "arpeggio": {…, "octave":   [{"row": 8}, …]}}  // the note an octave up
```

Every `freq` is a constant; every `interval` is a constant, including note 95's
`$09D2`, which is the interval above the tuning's last note and therefore not
musical — a number the vibrato owns, not a magic entry in the pitch table.
`octave` holds a row of the tuning, or a generator (§4.2) where the tune's own
`+12` left it: five notes, 85 86 88 93 95. A row absent from a modulator's table
is a note that modulator never touches, which is a fact worth stating and the
table states it.

Nothing anywhere adds to a pitch index. The tuning is bounded by construction:
`freq` is subscripted by a row of the score and by nothing else.

### 4.2 A played index that is not a note is the instrument's seed

"Note 104" is no note. Instruments 4 and 7 play it twenty-five times
(commando-floor §5) and what it means is *start from the waveform the other two
voices are sounding* — the drum's own starting value. So it is not in the
tuning; it is a record on the instruments that use it, carrying exactly the
columns their armed accumulators ask of a note:

```
instrument 4  seed  no note: number 104, freq wave0_wave1
instrument 7  seed  no note: number 104, freq wave0_wave1,
                    interval wave0_wave1_wave2_note0_difference,
                    octave pwdir0_pwdir1
```

Instrument 4 has neither vibrato nor arpeggio, so its seed carries neither
column. The event says `note: seed`; every other event says a row of the tuning.
`number` is the seed's place in the tune's own note numbering, which another
voice's generator observes — the one thing about the seed that is not private
to its instrument, and it is on the instrument all the same.

### 4.3 `generators`: a self-contained source, with private state

The values those escapes carry are not tuning at all. §6 says so in prose — "a
read landing on a play-written cell is not a pitch entry at all" — and this is
that, given a home. A generator is declared once and named by a modulator's
table or an instrument's seed:

```jsonc
"wave0_wave1": {
  "state": {"lo": 0, "hi": 0},
  "on": [{"event": "sound", "voice": 0, "set": {"lo": {"payload": "wave"}}},
         {"event": "sound", "voice": 1, "set": {"hi": {"payload": "wave"}}}],
  "value": {"u16": [{"own": "lo"}, {"own": "hi"}]}
}
```

It reads **nothing** of the player's: no cell of another voice, no table, no
address, no index past the end of anything. What it needs it *mirrors*, by
subscribing to the events the player publishes, and two generators mirroring the
same fact keep two copies — deliberately cheaper than a shared namespace,
because a private copy cannot alias.

The player publishes six events, each a musical fact and none a memory
location, and every subscription is a `set`: a generator mirrors, it never
counts.

| event | when | payload |
| --- | --- | --- |
| `note` | the row latched a note | `note` — its number, or the seed's |
| `instrument` | the row carried an instrument | `ins` |
| `sound` | any fetch, once the instrument's registers are emitted | `wave` |
| `row` | any fetch, once the row is consumed | `pos` — the cursor's new position |
| `order` | the order position moved | `pos` |
| `turn` | an accumulator's phase turned | `acc`, `phase` |

Song 1's eight generators and what each mirrors:

| generator | reached as | mirrors |
| --- | --- | --- |
| `sidofs2_voice_base` | `octave` of note 85 | nothing — `u16(sid_base 2, sid_base of the reader)` |
| `orderpos0_orderpos1` | `octave` of note 86 | two `order` positions |
| `patrow1_patrow2` | `octave` of note 88 | two cursor positions, by `row` |
| `wave2_note0` | `octave` of note 93 | one `sound` wave, one `note` number |
| `ins0_ins1` | `octave` of note 95 | two `instrument` ids |
| `wave0_wave1` | instruments 4 and 7's seed `freq` | two `sound` waves |
| `…_difference` | instrument 7's seed `interval` | four bytes, two `sound` waves and a `note` |
| `pwdir0_pwdir1` | instrument 7's seed `octave` | two pulse-sweep phases, by `turn` |

`sid_base` is the chip's own register layout — the offset the player computes
for every write it emits — so even the SID stride is not a constant in the data,
and a source that depends on nothing live is folded to a number at build time
(which is why note 95's interval is `$09D2` and not a generator).

**The invariant this buys, and the player enforces it:** no expression reads
another voice's state. `{"cell": name}` is the voice being committed and nothing
else. Cross-voice dependence exists only as a generator, declared, initialised
and fed by published events. A test walks the whole object and asserts it.

**No packed byte survives.** The score's event fields are `dur`, `tie`, `gate`,
`ins`, `note` and `arm`, every one a musical fact: the row byte's bit fields are
separate columns, and a portamento byte is unpacked at build time into
`arm(slide, {delta, phase})` — §3.6's own command, the shape an instrument
already uses to arm an accumulator. Nine porta bytes become nine `(delta,
phase)` pairs and the `porta` cell leaves the player entirely. The carry no
producer leaves, which the 6502 takes off the instrument index's own third
shift, folds to `0` with its proof recorded (no declared instrument id has bit 5
set), so the object never reads an index as if it were data.

**A cursor publishes its position, not its increment.** A pattern carries a
`cursor` column — the position the voice's own cursor holds *after* each event,
0 at the pattern's end, which is the cursor's own reset — and the player
publishes that position. `Event.bytes`, the last encoding width in the score, is
gone with it, and so is the `wrap` event and the `add` form in a subscription.
The column is emitted only for the patterns a watched voice plays: 18 of song
1's 31, and none at all in subtunes 2 and 3. It is the object's one stated
residue — not derivable, because the trackerprog's cursor is the event index and
Hubbard's is a byte offset, so it is a materialised coordinate, which is what
§6's materialisation rule is for.

Two-thirds of the machinery is free where it is not needed: **subtunes 2 and 3
carry no generators and no seeds at all**, and neither would GT2, JCH, SID
Wizard, defMON or Follin.

### 4.4 An instrument has a note row, whether or not it has a prelude

§3.5's table says Hubbard's prelude is `null`, and then there is nowhere for the
five sets a note-on emits (`ctrl = wave & gate`, `pw_lo`, `pw_hi`, `ad`, `sr`).
Expressed here as `meta.note_row`, a §3.3 stream of `set` steps whose values
read the instrument's own columns and its **live** pw cell. Proposal: every
`Ins` has a note row; `prelude: null` means no *early* rows, not no note row.

### 4.5 `Acc.emit ∈ {entry, exit}`

The drum writes `freq_hi` and *then* decrements it, so the producer sees the
value the tick came in with. #297's "epoch of a cell the tick moved" rule names
both epochs and leaves the choice to inference; the object must state it.

### 4.6 An `Acc` may carry the gate rows its own guard selects

The drum's first row tick sets `ctrl = $80` (TEST, gate off) and every later
tick `ctrl = wave & $FE`, chosen by the same guard that decides whether the
accumulator steps. §5 has a `target: gate-mask` row (Walker, prose-only) but no
place for two `set` rows under one Acc. Written here as
`gate: {true: [...], false: [...]}`; the cleaner statement is a two-row §3.3
stream with a `select` on the Acc's guard.

### 4.7 `meta.row_consumes_tick`

On the tick a voice takes a new row, its accumulators do not run. §4's tick runs
the sequencer step and then the streams and accs unconditionally. One bit.

### 4.8 `Event.tie`

§3.5's prelude belongs to the instrument; whether it fires belongs to the
**row** — bit 5 of the row byte. One bit per event. (§3.6's nearest existing
spelling is `cmds: [gate(mask)]`.)

### 4.9 `Acc.when` and `Acc.delta_when`

§5's record has `bound`, `policy`, `rate`, `phase` and no guards, while §5's
*rules* table is about nothing else ("the store's transitively closed control
dependences", #296). The drum needs three guards and the vibrato's delta one
(`dur >= 6`); the accumulator needs a `when` list of comparisons over named
cells — the same `when` the transition prototype gives every entry of its
tick (`w11-producers`, unmerged).

### 4.10 The flag's producer, not only its consumer

§5 gives the consumer `Δ + carry(site, flag)`. The producer needs stating too:
the vibrato's `repeat` loop leaves `C`, the value it leaves when the loop does
not run (`seed: 1`, the compare's own carry), the value when its guard fails
(`unguarded: 0`), and the default when no vibrato is armed at all
(`globals.flags.C.default = bit(ins, 5)`, the residue of the instrument index's
three shifts — always 0 for this tune, and stated rather than assumed).

The term is load-bearing: delete `+ carry` and subtune 2 diverges on 11,747 of
11,780 ticks. It is the write that makes the subtunes aperiodic
(architecture §5.2), and it is exercised on 2,106 ticks.

### 4.11 `stop` is four writes and an abandoned tick

§3.6 has the terminator; the writes it makes are data —
`$D404 = $D40B = $D412 = 0`, `$D418 = $0F`, on the tick *after* the one the
terminator was read on, which is abandoned mid-voice-loop. Subtune 3 exercises
it.

### 4.12 `trap`: the arm the horizon never takes

§5 struck the skydive row for lack of an observation. The object keeps it as
data — the guard, and `trap: true` — and the player raises if it is ever taken,
which is the print's `trap 'untaken'` carried one layer up instead of dropped.

---

## 5. What the spec got right

Every §5 row Hubbard is cited for held **exactly as written**, with no widening:

- **stateless-phase vibrato** — `repeat(tablestep(FREQ, note, ins.vib + 1), n)`,
  `phase fn(global_counter)`, the single-family exception. The fold
  (`counter & 7`, `^ 7` above 3) is the triangle; the shift loop's count is the
  instrument byte; the `dur >= 6` guard is the delta's, not the producer's.
- **free slide is `wrap`, not `reflect`** (§5's correction 5): `porta` is set by
  the score and never turns.
- **pulse sweep** — `reflect`, `bound [$800,$EFF]` **projected** from the store's
  `& $F` (correction 4), `rate` the divider, `phase` the per-voice cell, `scope`
  **instrument** while its phase and divider are per voice (§5's `scope` rule,
  read off the cell).
- **pulse run** — `const(k) + carry(site)`, `bound` projected at 12 bits.
- **arpeggio** — an absolute `set` producer over a pitch stream, `phase
  fn(counter)`.
- **`commit_order (ctrl, ad, sr)`** — §3.1's row for Hubbard, unchanged.
- **§2's observable is the right strength.** Not one comparison was lost to the
  reduction, and §6 measures exactly what it drops.

Two §5 delta forms are not exercised by this tune: `tabcell` and the
sign-extended table entry (§10's open question). Neither is missing.

---

## 6. Measurements

The print, `trackerprog.md`, is the flattened form — one fact per line, eight
sections, no JSON — measured the way architecture §11 asks of a presentation:

| subtune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 810 | 5,313 | 769 | 8 | 41 | 769 | 3,784 |
| 2 | 238 | 1,464 | 219 | 7 | 19 | 219 | 2,004 |
| 3 | 163 | 998 | 150 | 7 | 13 | 150 | 1,764 |

Statements equal data rows by construction: the print carries one datum per
line. Song 1's 810 lines against the source `tuneprog.md`'s 414 is the trade the
layer makes — the program's 252 code lines become 60 lines of instruments,
accumulators and generators, and the rest is the score
and the tuning printed *as data*, which the tuneprog never printed at all.

`xz -9e` of the serialised object, the extra number §9's acceptance #3 asks
for:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, song 1, compact | 49,190 | **3,932** |
| — its `score` half | 40,008 | 1,564 |
| — everything else (tuning, modulator tables, generators, accs, instruments) | 9,173 | 2,536 |
| `tuneprog.md`, the source print | 21,679 | 4,644 |
| the whole load band | 4,039 | 2,548 |
| the tune's data floor (commando-floor §2.2) | 1,941 | 1,112 |
| `trackerprog.json`, song 2 / song 3 | 13,122 / 8,731 | 2,136 / 1,900 |

The layer's claim holds: the score compresses better than the program that
played it (3,932 against 4,644), and the score half alone lands within 40 % of
the tune's own compressed data. Every structural cleanup so far has cost
compressed bytes and been worth them: 3,252 for the first draft's seven
cell-valued pitch entries, 3,804 to bound the table, 3,932 to keep the tuning
pure and give each modulator its own. What the object buys with them is that no
part of it can be walked off, aliased or unpacked at run time. The JSON's raw
size is key repetition and nothing else.

Code, all new, no existing module touched:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 396 | §4 + §5, one procedure over the object; publishes the seven events |
| `deity_informant/trackerprog/attest.py` | 81 | §2's comparison |
| `deity_informant/trackerprog/printer.py` | 341 | the flattened form: one fact per line, and §6.2's numbers |
| `tools/trackerprog_commando.py` | 643 | the transliteration and the PcodeVM reference |
| `tests/trackerprog/test_commando_oracle.py` | 159 | the three certificates and seven claims |
| `tests/trackerprog/test_universal.py` | 440 | hermetic snippets, one per section 5 mechanism |

Against the floor: the player pseudocode
[playroutine-anatomy.md](playroutine-anatomy.md) §3.1.3 is 65 lines and covers
all three songs and every fx bit. The universal player is 396 lines and covers
*no* song — it has no Commando in it. That is the trade the layer is for.
