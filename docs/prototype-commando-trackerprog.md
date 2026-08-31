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
2. **The schema needs thirteen additions**, listed in §4. Nine are one datum
   each. The other four are one rule made explicit: **a value that is not in the
   pitch table is not a pitch, so it is not a note.** The tuning holds pitch and
   nothing else, the modulators are expressions over it, and the two things this
   tune does that are not pitch belong to whatever does them — the arpeggio owns
   what it does past the tuning, and an instrument owns a sound that has no pitch
   at all. Nothing in the tune needed a new *mechanism*: every §5 row
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
`deity_informant/trackerprog/universal.py` (452 lines, no tune, no family, no
table of its own) renders it. The object is §3's seven sections:

| section | Commando song 1 |
| --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ctrl, ad, sr)`; `tempo` a divider, `rate = speed + 1 = 3`; `cycles_per_tick 19656` |
| `pitch` | `base 16` and **80** contiguous frequencies — the tune's whole tuning, the same in every subtune. No number outside 16..95 exists anywhere in the object (§4.1) |
| modulators with private state | the arpeggio's `beyond` (12 words by overflow distance, 3 traps, the same in every subtune, §4.2); instruments 4 and 7's own `pitch` (§4.3) |
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
| a transposition past the tuning | the arpeggio's own `beyond`, by overflow distance | new (§4.2) |
| a sound that is no pitch | the instrument's own `pitch` modulator; the event carries no note | new (§4.3) |
| `INS[i]`, 8 columns | `Ins{adsr, wave, pw, prelude, accs}` | §3.5 |
| `TRACK[v]` / `PAT[p]` | `score.orders` / `score.patterns` of events | §3.6 |
| `speedctr`, `speed` | `meta.tempo` — a divider, `rate = 3` | §3.3 |
| `row & $1F` | the event's `dur`, in row ticks | §3.6 |
| `row & $20` | the event's `tie`: it disarms the prelude | new (§4.8) |
| `row & $40` | the event's `sounds` — this family's only gate token | §3.6 |
| the extra byte `< $80` | the event's `ins` | §3.6 |
| `$518B` hard cut | the instrument's **prelude**, `early = 1` row tick, rows `set(ctrl, wave & $FE) set(ad,0) set(sr,0)` | §3.5 |
| `ins.vib ≠ 0` | `Acc(freq, repeat(tablestep(pitch, note, vib+1), phase(fold(counter,7))), policy reload(pitch[note]))` | §5 vibrato, stateless phase |
| `ins.fx & 8` | `Acc(pw_lo, add(const(pspeed), flag(C)), width 8, wrap, scope instrument)` | §5 pulse run |
| `ins.pspeed ≠ 0` | `Acc(pw, const(pspeed & $E0), width 12, reflect, amplitude [$800,$EFF] >> 8, bound [0,$FFF] projected, rate (pspeed & $1F)+1, phase cell pwdir, scope instrument)` | §5 pulse sweep |
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

| subtune | ins | patterns | events | tuning | `beyond` | acc arms | ticks | SID writes | divergences |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9 | 31 | 570 | 80 | 12 | 18 | 11,780 | 133,109 | **0** |
| 2 | 4 | 10 | 118 | 80 | 12 | 8 | 11,780 | 128,953 | **0** |
| 3 | 1 | 4 | 61 | 80 | 12 | 4 | 11,780 | 7,219 | **0** |

The tuning and the arpeggio's `beyond` are the same object in all three rows:
they belong to the tune, and only the score and the instruments change with the
subtune.

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write.

**A claim stronger than §2, where it holds.** On every tick of every subtune the
two sides' write lists have the same *multiset*, and for subtunes 2 and 3 every
register's own sequence of values is identical too (`same_per_register_order`).
The whole difference there is where `ctrl` sits relative to `pw` on a note-on
tick, because §4's `commit` emits producers (step 4) before the edge registers
(step 5) and Hubbard emits `ctrl, pw_lo, pw_hi, ad, sr` — exactly the idiom §2
cites as its reason for dropping cross-class order, measured rather than
assumed.

Subtune 1 is the one exception, and §4.3 says why: an unpitched sound has no
semitone above it, so a vibrato over it steps by nothing, where Hubbard's
routine steps by whatever lies past his tuning. **105 of 11,780 ticks** differ
in one write, all on `freq_lo`, and in every one of the 105 that write is
immediately superseded by another to the same register with the tick's final
value identical. The frequency the chip holds at every tick boundary is the
same. Reproducing the superseded write would have meant putting a note number
back into the object for a value §2 drops by design; a test states the 105 and
its shape rather than the object hiding it.

Subtune 3 is not in the certified set and earns its place anyway: its order
program ends in `stop`, which subtunes 1 and 2 never reach inside the horizon.

---

## 4. What the spec needed

Thirteen additions. Nine are one datum each; §4.1 to §4.4 are one rule made
explicit. None is a player branch and none is a new mechanism.

### 4.1 A pitch table is a pitch table, and the modulators are expressions

§3.2 says `pitch: [u16; N]` and "every *note* elsewhere is an index into this
table or a signed index offset". Both halves are right, and the object says no
more than that:

```jsonc
"pitch": {"base": 16, "freq": [$02BD, $02E7, …, $FD2E]}   // notes 16..95, constants
```

That is the tune's **whole tuning** — 80 contiguous entries, the same in every
subtune — not the notes some melody plays. The vibrato and the arpeggio keep no
tables; they are expressions over it:

```
vibrato   delta   repeat(interval >> <shift>, fold(counter, 7))
arpeggio  policy  reload transpose(arp[counter & 1])        with arp = [0, 12]
```

And the rule the whole object keeps: **a value that is not in the pitch table is
not a pitch, so it is not a note.** No number outside 16..95 appears anywhere —
not in the score, not in a table, not as an index, not as a "note the tuning
does not have". Two things this tune does are not pitch, and each is private to
whatever does them.

### 4.2 The arpeggio owns what it does past the tuning

Commando's frequency table is fused with the six per-voice arrays: `$5448`'s 202
bytes tune notes 16..95 and then become state (commando-floor §5, "const is
refuted by the tune"). Transposing an octave up from the tuning's top twelve
notes runs off the end.

§5 already gives every accumulator a policy at the edge of what it reaches. The
edge the arpeggio runs off is the **tuning's** — not its `bound`, which is the
16-bit store the cell keeps, a distinction §7's second package had to make when
the renderer started asserting the one and this record was claiming the other
(the arp stream's `[0, 12]` is the transpose, and the cell holds a frequency).
Its behaviour past the tuning is **its own**, with its own private state and
subscriptions, indexed by how far past the transposition went and never by a
note:

```
[5] arpeggio      freq  w16  tick           scope voice
      policy  reload transpose(arp[counter & 1])
      phase   counter & 1
      bound   [$0000, $FFFF] projected -- the 16-bit store
      beyond  past the tuning, by how far past it the transposition went
            0  u16(0, 7)
            1  u16(14, sid_base(reader))
            2  u16(own.orderpos0, own.orderpos1)
            …
            5  trap: no event publishes rowsleft
            …
           11  u16(own.ins0, own.ins1)
          state  orderpos0=$00 … ins1=$09
          on order(voice 0): orderpos0 := pos ; …
```

It is the arpeggio's because the arpeggio is the only thing that asks. That is
measured, not assumed: poisoning each value one at a time, every one that
reaches a register is an arpeggio output, and the vibrato's two are dead —
every instrument whose vibrato could ask for a step past the tuning also
arpeggiates over it, and the arpeggio's `freq` write lands later in the tick.
So **the tuning simply has no interval above its top note**, which is the true
statement, and nothing carries a value for one.

Being indexed by distance rather than by note, the twelve words are a property
of the tune's memory layout, not of a melody: **the same `beyond` appears in
every subtune**, and a melody reaching distance 3 or 10 — which song 1 never
does — would work unchanged. A distance the object cannot publish is a trap
carrying its reason: three are cells the tick recomputes, two are the row
countdown nothing publishes, one is the row byte the score no longer packs.

### 4.3 An instrument owns what is not a pitch at all

Instruments 4 and 7 sound a drum whose frequency is taken from the waveforms the
other voices are sounding. That is no pitch, so the score gives those events no
note, and the frequency comes from a modulator on the instrument — inline,
self-contained, one copy each:

```
   4  $0F  $C4   $43  $0200  drum skydive
    pitch   this instrument's sound is no pitch; it is its own
        value     u16(own.wave0, own.wave1)
        state  wave0=$00 wave1=$00
        on sound(voice 0): wave0 := wave
        on sound(voice 1): wave1 := wave
   7  $0D  $FB   $15  $0180  vibrato(shift 2) drum arpeggio
    pitch   this instrument's sound is no pitch; it is its own
        value     u16(own.wave0, own.wave1)
        octave    u16(own.pwdir0, own.pwdir1)
        state  wave0=$00 wave1=$00 pwdir0=$00 pwdir1=$00
```

It carries exactly what its instrument's modulators ask of a pitch: the
frequency, and — where the instrument arpeggiates — the octave that arpeggio
jumps to. Instrument 4 arpeggiates nothing, so it has no octave. Neither has an
interval: **an unpitched sound has no semitone above it, so a vibrato over it
steps by nothing.**

That last line costs something, and §6 measures it rather than hiding it.
Hubbard's routine does step, by whatever lies past his tuning, and writes the
result — which the same instrument's arpeggio then overwrites in the same tick.
Reproducing that intermediate write would have meant putting a note number back
into the object for a value §2 drops by design. It is out, and the certificate
says exactly what that leaves.

### 4.4 A modulator is self-contained, with private state

Whether it belongs to an accumulator (`beyond`) or an instrument (`pitch`), such
a modulator reads **nothing** of the player's: no cell of another voice, no
table, no address. What it needs it *mirrors*, by subscribing to the events the
player publishes, and two modulators mirroring the same fact keep two copies —
deliberately cheaper than a shared namespace, because a private copy cannot
alias.

The player publishes seven events, each a musical fact and none a memory
location. A modulator mirrors what it is told with `set`, and counts for itself
with `add` what the tune counts.

| event | when | payload |
| --- | --- | --- |
| `note` | the row latched a note | `note` |
| `instrument` | the row carried an instrument | `ins` |
| `sound` | any fetch, once the instrument's registers are emitted | `wave` |
| `row` | any fetch, once the row is consumed | `sounds`, `field` — what the row is |
| `wrap` | the pattern restarted | — |
| `order` | the order position moved | `pos` |
| `turn` | an accumulator's phase turned | `acc`, `phase` |

`sid_base` is the chip's own register layout — the offset the player computes
for every write it emits — so even the SID stride is not a constant in the data,
and a word that depends on nothing live is folded to a number at build time.

**The invariant this buys, and the player enforces it:** no expression reads
another voice's state. `{"cell": name}` is the voice being committed and nothing
else. Cross-voice dependence exists only inside a modulator's own private state.
A test walks the whole object and asserts it.

**No packed byte survives.** The score's event fields are `dur`, `tie`, `gate`,
`ins`, `note` and `arm`, every one a musical fact: the row byte's bit fields are
separate columns, and a portamento byte is unpacked at build time into
`arm(slide, {delta, phase})` — §3.6's own command. The carry no producer leaves,
which the 6502 takes off the instrument index's own third shift, folds to `0`
with its proof recorded (no declared instrument id has bit 5 set).

**A modulator that mirrors a counter counts for itself.** The arpeggio's
`beyond` watches two voices' pattern cursors, and this tune's cursor counts
*bytes* where the trackerprog's counts events. That is the modulator's business,
not the score's: it subscribes to `row` and advances by its own model of the
cell it mirrors —

```
on row(voice 1): patrow1 += 1 + (sounds + field)
on wrap(voice 1): patrow1 := 0
```

— one byte for the row, one more where it sounds, one more where it carries an
instrument or an arm. A pattern is its events and nothing else. An earlier draft
put the byte offset in the score as a per-event `cursor` column: 570 integers
that were a function of the events beside them, on all 31 patterns, of which
pattern 31's were never read. That is what a modelling artefact looks like when
it escapes into the score, and it is gone.

### 4.5 An instrument has a note row, whether or not it has a prelude

§3.5's table says Hubbard's prelude is `null`, and then there is nowhere for the
five sets a note-on emits (`ctrl = wave & gate`, `pw_lo`, `pw_hi`, `ad`, `sr`).
Expressed here as a `{stream}` step of §3.6's `meta.row` program, a §3.3 stream
of `set` steps whose values read the instrument's own columns and its **live**
pw cell. Proposal: every `Ins` has a note row; `prelude: null` means no *early*
rows, not no note row. (Written first as `meta.note_row`, a key of its own; it
turned out to fire at the note-on in GoatTracker 2 and at *every* row here,
under one name, which is why the row is a program and not a set of hooks.)

### 4.6 `Acc.emit ∈ {entry, exit}`

The drum writes `freq_hi` and *then* decrements it, so the producer sees the
value the tick came in with. #297's "epoch of a cell the tick moved" rule names
both epochs and leaves the choice to inference; the object must state it.

### 4.7 An `Acc` may carry the gate rows its own guard selects

The drum's first row tick sets `ctrl = $80` (TEST, gate off) and every later
tick `ctrl = wave & $FE`, chosen by the same guard that decides whether the
accumulator steps. §5 has a `target: gate-mask` row (Walker, prose-only) but no
place for two `set` rows under one Acc. Written here as
`gate: {true: [...], false: [...]}`; the cleaner statement is a two-row §3.3
stream with a `select` on the Acc's guard.

### 4.8 `meta.row_consumes_tick`

On the tick a voice takes a new row, its accumulators do not run. §4's tick runs
the sequencer step and then the streams and accs unconditionally. One bit.

### 4.9 `Event.tie`, and `sounds` rather than `gate`

§3.5's prelude belongs to the instrument; whether it fires belongs to the
**row** — bit 5 of the row byte. One bit per event. (§3.6's nearest existing
spelling is `cmds: [gate(mask)]`.)

Bit 6 is the row's other flag, and the first draft of this document called it
`gate: on | off`. §3.6 now calls it `sounds`, because it is the field *every*
family answers "does this row key a note?" with, and Hubbard's row byte simply
has no gate token of its own: an event here always has `gate: none`. The
distinction is not academic — GoatTracker 2 has three tokens where Hubbard has
one bit (`$BD` rest, `$BE` keyoff, `$BF` keyon), so a rest and a keyoff both
have `sounds: false` and only the keyoff carries a `gate`. The consequence for
this tune is that §4.3's drum rows say what they are: `sounds: true, note: none`
— the row sounds, and its pitch is the instrument's own — where before, `note:
none` had to be read against `gate: on` to mean anything.

### 4.10 `Acc.when` and `Acc.delta_when`

§5's record has `bound`, `policy`, `rate`, `phase` and no guards, while §5's
*rules* table is about nothing else ("the store's transitively closed control
dependences", #296). The drum needs three guards and the vibrato's delta one
(`dur >= 6`); the accumulator needs a `when` list of comparisons over named
cells — the same `when`
[prototype-trackerprog-transition.md](prototype-trackerprog-transition.md) §1
gives every entry of its tick.

### 4.11 The flag's producer, not only its consumer

§5 gives the consumer a live carry, `add(Δ, flag(C))`. The producer needs
stating too, and this is the one carry in the layer that is **not** an
expression: the vibrato's `repeat` loop leaves `C`, and the carry it leaves is
the carry of the *last* of its `n` additions, so no expression over the value
the loop stored recovers it without re-running that step in the object. So the
record states it — `flag: {name: "C", seed: 1}`, the seed being what the loop
leaves when it does not run, which is the compare's own carry — and
`globals.flags.C.default` states what `C` is when no vibrato is armed at all
(`bit(ins, 5)`, the residue of the instrument index's three shifts, always 0 for
this tune and stated rather than assumed).

The record also carried `unguarded: 0`, the value where the guard fails, and
that is the flag's own default said twice: measured, it is worth **0 ticks on
all three subtunes**, and it is struck. defMON keeps the field, where its two
pulse arms want 1 against a default of 0 and it is worth 475 of *Jazzpjazz*'s
1,799 ticks and 127,722 of *Automatas*' 149,025
([prototype-trackerprog.md](prototype-trackerprog.md) §5, §7).

The seed is load-bearing on its own: rendering it as 0 diverges on **11,747 of
subtune 2's 11,780** ticks and 329 of subtune 3's. And the term is load-bearing:
delete the carry and subtune 2 diverges on 11,747 of
11,780 ticks. It is the write that makes the subtunes aperiodic
(architecture §5.2), and it is exercised on 2,106 ticks.

### 4.12 `stop` is four writes and an abandoned tick

§3.6 has the terminator; the writes it makes are data —
`$D404 = $D40B = $D412 = 0`, `$D418 = $0F`, on the tick *after* the one the
terminator was read on, which is abandoned mid-voice-loop. Subtune 3 exercises
it.

### 4.13 `trap`: the arm the horizon never takes

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
- **pulse sweep** — `reflect`, turning at `[$800, $EFF]` **projected** from the
  store's `& $F` (correction 4), `rate` the divider, `phase` the per-voice cell,
  `scope` **instrument** while its phase and divider are per voice (§5's `scope`
  rule, read off the cell). Where `[$800, $EFF]` *lives* moved: it is the
  `amplitude` the bounce turns at and not the interval the cell keeps, because a
  step of `$E0` from `$E60` lands at `$F40`, which the turn's own `>> 8` does not
  see, and the step after it wraps to `$020` — 10 moves of song 1's 6,800 leave
  the window, the first at tick 3,457. The `bound` is the 12-bit store
  ([prototype-trackerprog.md](prototype-trackerprog.md) §5, §7).
- **pulse run** — `add(const(k), flag(C))`, `bound` projected at 12 bits.
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
| 1 | 788 | 5,037 | 748 | 7 | 40 | 748 | 3,424 |
| 2 | 267 | 1,697 | 248 | 7 | 19 | 248 | 2,368 |
| 3 | 195 | 1,255 | 182 | 7 | 13 | 182 | 2,188 |

Statements equal data rows by construction: the print carries one datum per
line. Song 1's 788 lines against the source `tuneprog.md`'s 414 is the trade the
layer makes — the program's 252 code lines become 60 lines of instruments,
accumulators and generators, and the rest is the score
and the tuning printed *as data*, which the tuneprog never printed at all.

`xz -9e` of the serialised object, the extra number §9's acceptance #3 asks
for:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, song 1, compact | 47,313 | **3,520** |
| — its `score` half | 39,172 | 1,444 |
| — everything else (tuning, accs with their own state, instruments) | 8,132 | 2,232 |
| `tuneprog.md`, the source print | 21,679 | 4,644 |
| the whole load band | 4,039 | 2,548 |
| the tune's data floor (commando-floor §2.2) | 1,941 | 1,112 |
| `trackerprog.json`, song 2 / song 3 | 14,754 / 10,591 | 2,424 / 2,236 |

The layer's claim holds: the score compresses better than the program that
played it (3,520 against 4,644), and the score half alone lands within 40 % of
the tune's own compressed data. Subtunes 2 and 3 grew, because they now carry
the tune's whole tuning and the whole source rather than the slice their own
melody touches — which is the point: those two sections are the same object in
all three, and a fourth melody would need neither changed. The JSON's raw
size is key repetition and nothing else.

Code, all new, no existing module touched:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 452 | §4 + §5, one procedure over the object; publishes the seven events |
| `deity_informant/trackerprog/attest.py` | 81 | §2's comparison |
| `deity_informant/trackerprog/printer.py` | 328 | the flattened form: one fact per line, and §6.2's numbers |
| `tools/trackerprog_commando.py` | 611 | the transliteration and the PcodeVM reference |
| `tests/trackerprog/test_commando_oracle.py` | 207 | the three certificates and the rules of §4.1-4.3 |
| `tests/trackerprog/test_universal.py` | 425 | hermetic snippets, one per section 5 mechanism |

Against the floor: the player pseudocode
[playroutine-anatomy.md](playroutine-anatomy.md) §3.1.3 is 65 lines and covers
all three songs and every fx bit. The universal player is 452 lines and covers
*no* song — it has no Commando in it. That is the trade the layer is for.
