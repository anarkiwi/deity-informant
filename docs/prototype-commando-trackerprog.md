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
   table holds the tune's whole tuning and nothing else, with the modulators as
   **expressions** over it; the lookup is **total**, and past the tuning it
   resolves against one **source** indexed by position; and a source is
   self-contained, with private state fed by the player's published events. Nothing in the tune needed a new *mechanism*: every §5 row
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
`deity_informant/trackerprog/universal.py` (390 lines, no tune, no family, no
table of its own) renders it. The object is §3's seven sections:

| section | Commando song 1 |
| --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ctrl, ad, sr)`; `tempo` a divider, `rate = speed + 1 = 3`; `cycles_per_tick 19656` |
| `pitch` | `base 16` and **80** contiguous frequencies — the tune's whole tuning, the same in every subtune. The vibrato and the arpeggio are expressions over it (§4.1) |
| `generators` | **one** source, `past_tuning`, indices 96..116: 12 words and 9 stated traps, 17 private bytes, 17 subscriptions — the same in every subtune (§4.2, §4.3) |
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
| `FREQ[n]`, the u16 at `$5428 + 2n` | `pitch(n)` — the tuning, total by construction | §3.2 |
| `FREQ[n+1] - FREQ[n]` (the vibrato's interval) | `pitch(n+1) - pitch(n)` — an expression | §5 `tablestep` |
| `FREQ[n+12]` (the arpeggio's octave) | `pitch(n + arp[counter & 1])`, `arp = [0, 12]` | §5 arpeggio |
| an index past the tuning | one **source**, indexed by position, with private state | new (§4.2, §4.3) |
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

| subtune | ins | patterns | events | tuning | source | acc arms | ticks | SID writes | divergences | identical ticks | permuted ticks |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9 | 31 | 570 | 80 | 21 | 18 | 11,780 | 133,109 | **0** | 3,410 | 8,370 |
| 2 | 4 | 10 | 118 | 80 | 21 | 8 | 11,780 | 128,953 | **0** | 2,944 | 8,836 |
| 3 | 1 | 4 | 61 | 80 | 21 | 4 | 11,780 | 7,219 | **0** | 11,561 | 219 |

The tuning and the source are the same object in all three rows: they belong to
the tune, and only the score and the instruments change with the subtune.

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

### 4.1 A pitch table is a pitch table, and the modulators are expressions

§3.2 says `pitch: [u16; N]` and "every *note* elsewhere is an index into this
table or a signed index offset". Both halves are right, and the object should
say no more than that:

```jsonc
"pitch": {"base": 16, "freq": [$02BD, $02E7, …, $FD2E]}   // notes 16..95, constants
```

That is the tune's **whole tuning** — 80 contiguous entries, the same in every
subtune — not the notes some melody happens to play. The vibrato and the
arpeggio then keep no tables at all; they are expressions over it:

```
vibrato   delta   repeat((pitch(note + 1) - pitch(note)) >> <shift>, fold(counter, 7))
arpeggio  policy  reload pitch(note + arp[counter & 1])        with arp = [0, 12]
```

An earlier draft gave each modulator a table indexed by the notes this melody
plays. That was wrong twice over: it put transformation data in the tuning's
shape, and a different melody over the same tune would have shattered it —
a new note meant a new table row that did not exist. Expressions over a total
lookup have neither problem.

### 4.2 One source, indexed by position, for what lies past the tuning

`pitch(n)` has to be **total**, because Hubbard's own arithmetic walks off the
end of his tuning: `$5448`'s 202 bytes tune notes 16..95 and then become the
six per-voice arrays (commando-floor §5, "const is refuted by the tune"). The
vibrato's `n+1` and the arpeggio's `n+12` reach indices 96..116.

So past the tuning the lookup resolves against a **source**: one generator,
indexed by position and never by note.

```
past_tuning -- indices 96..116
     96  u16(0, 7)
     97  u16(14, sid_base(reader))
     98  u16(own.orderpos0, own.orderpos1)
     99  u16(own.orderpos2, own.patrow0)
    100  u16(own.patrow1, own.patrow2)
    101  trap: no event publishes rowsleft
    102  trap: no event publishes rowsleft
    103  trap: no event publishes rowbyte
    104  u16(own.wave0, own.wave1)
    105  u16(own.wave2, own.note0)
    106  u16(own.note1, own.note2)
    107  u16(own.ins0, own.ins1)
    108  trap: a cell the tick recomputes; nothing carries it between ticks
    …
    116  u16(own.pwdir0, own.pwdir1)
```

Because it is positional, **every subtune carries the same source** — it is a
property of the tune, not of a melody — and a melody that reached index 99 or
106, which song 1 never does, would simply work. A position the object cannot
publish is a stated trap with its reason, never a hole and never silence: nine
of the twenty-one are traps, seven because the cells are tick scratch that
nothing carries between ticks, two because the object unpacked the row byte and
one because it does not publish the row countdown. Those are the boundary, said
out loud.

"Note 104" is no pitch, and nothing pretends otherwise: the tuning stops at 95,
the score plays index 104 twenty-five times, and the lookup finds it in the
source. That is what *not a note* means, said structurally rather than by
special-casing it onto an instrument.

### 4.3 A source is self-contained, with private state

```jsonc
"past_tuning": {
  "base":  96,
  "state": {"wave0": 0, "wave1": 0, …, "ins1": 9, …},
  "on":    [{"event": "sound", "voice": 0, "set": {"wave0": {"payload": "wave"}}}, …],
  "words": [ … ]
}
```

It reads **nothing** of the player's: no cell of another voice, no table, no
address. What it needs it *mirrors*, by subscribing to the events the player
publishes; seventeen private bytes and seventeen subscriptions cover the whole
tail. Mirroring is deliberately cheaper than a shared namespace, because a
private copy cannot alias.

The player publishes six events, each a musical fact and none a memory
location, and every subscription is a `set`: a source mirrors, it never counts.

| event | when | payload |
| --- | --- | --- |
| `note` | the row latched a note | `note` |
| `instrument` | the row carried an instrument | `ins` |
| `sound` | any fetch, once the instrument's registers are emitted | `wave` |
| `row` | any fetch, once the row is consumed | `pos` — the cursor's new position |
| `order` | the order position moved | `pos` |
| `turn` | an accumulator's phase turned | `acc`, `phase` |

`sid_base` is the chip's own register layout — the offset the player computes
for every write it emits — so even the SID stride is not a constant in the data,
and a word that depends on nothing live is folded to a number at build time
(which is why index 96 prints as `u16(0, 7)` and needs no state).

**The invariant this buys, and the player enforces it:** no expression reads
another voice's state. `{"cell": name}` is the voice being committed and nothing
else. Cross-voice dependence exists only in a source, declared, initialised and
fed by published events. A test walks the whole object and asserts it.

**No packed byte survives.** The score's event fields are `dur`, `tie`, `gate`,
`ins`, `note` and `arm`, every one a musical fact: the row byte's bit fields are
separate columns, and a portamento byte is unpacked at build time into
`arm(slide, {delta, phase})` — §3.6's own command, the shape an instrument
already uses to arm an accumulator. The carry no producer leaves, which the 6502
takes off the instrument index's own third shift, folds to `0` with its proof
recorded (no declared instrument id has bit 5 set).

**A cursor publishes its position, not its increment.** A pattern carries a
`cursor` column — the position the voice's own cursor holds *after* each event,
0 at the pattern's end, which is the cursor's own reset — and the player
publishes that position. `Event.bytes`, the last encoding width in the score, is
gone with it, and so is the `wrap` event and the `add` form in a subscription.
It is the object's one materialised coordinate: not derivable, because the
trackerprog's cursor is the event index and Hubbard's is a byte offset.

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
| 1 | 818 | 5,668 | 777 | 8 | 41 | 777 | 3,728 |
| 2 | 289 | 1,934 | 269 | 8 | 20 | 269 | 2,604 |
| 3 | 211 | 1,429 | 197 | 8 | 14 | 197 | 2,364 |

Statements equal data rows by construction: the print carries one datum per
line. Song 1's 818 lines against the source `tuneprog.md`'s 414 is the trade the
layer makes — the program's 252 code lines become 60 lines of instruments,
accumulators and generators, and the rest is the score
and the tuning printed *as data*, which the tuneprog never printed at all.

`xz -9e` of the serialised object, the extra number §9's acceptance #3 asks
for:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, song 1, compact | 49,035 | **3,760** |
| — its `score` half | 41,089 | 1,736 |
| — everything else (tuning, the source, accs, instruments) | 7,937 | 2,184 |
| `tuneprog.md`, the source print | 21,679 | 4,644 |
| the whole load band | 4,039 | 2,548 |
| the tune's data floor (commando-floor §2.2) | 1,941 | 1,112 |
| `trackerprog.json`, song 2 / song 3 | 15,668 / 11,298 | 2,564 / 2,312 |

The layer's claim holds: the score compresses better than the program that
played it (3,760 against 4,644), and the score half alone lands within 40 % of
the tune's own compressed data. Subtunes 2 and 3 grew, because they now carry
the tune's whole tuning and the whole source rather than the slice their own
melody touches — which is the point: those two sections are the same object in
all three, and a fourth melody would need neither changed. The JSON's raw
size is key repetition and nothing else.

Code, all new, no existing module touched:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 390 | §4 + §5, one procedure over the object; publishes the seven events |
| `deity_informant/trackerprog/attest.py` | 81 | §2's comparison |
| `deity_informant/trackerprog/printer.py` | 323 | the flattened form: one fact per line, and §6.2's numbers |
| `tools/trackerprog_commando.py` | 597 | the transliteration and the PcodeVM reference |
| `tests/trackerprog/test_commando_oracle.py` | 175 | the three certificates and six claims |
| `tests/trackerprog/test_universal.py` | 386 | hermetic snippets, one per section 5 mechanism |

Against the floor: the player pseudocode
[playroutine-anatomy.md](playroutine-anatomy.md) §3.1.3 is 65 lines and covers
all three songs and every fx bit. The universal player is 390 lines and covers
*no* song — it has no Commando in it. That is the trade the layer is for.
