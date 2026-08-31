# Prototype: trackerprog — a universal tracker representation

Design prototype, not yet a certified exemplar: the schema, the semantics, the
lift and the acceptance for a **trackerprog**, the layer above
[tuneprog-architecture.md](tuneprog-architecture.md). A tuneprog moves a tune
from opaque bytes to a certified per-tick *program*; a trackerprog moves the
music from player-specific code-plus-data to player-independent *data* — a pitch
table, instrument definitions and patterns that play pitches with instruments —
rendered by **one fixed universal player**. Effects are **bounded
accumulators**.

Empirical ground: [playroutine-anatomy.md](playroutine-anatomy.md) §2, which
shows all nine playroutines are one object (STATE, TABLES, PLAY), and the eight
certified families — [GoatTracker 2](prototype-goattracker.md), [SID
Wizard](prototype-sidwizard.md), [JCH V20](prototype-jch.md),
[Hubbard](prototype-commando-floor.md), [defMON](prototype-automatas.md),
[Follin](prototype-follin.md), [Blackbird](prototype-blackbird-trackerprog.md)
and [Walker](prototype-walker-trackerprog.md).
Galway alone is **prose-only**: no certificate covers it (architecture
§9.2) and no schema row rests on it alone. The survey fact that sizes the layer: 91.6 % of traced HVSC by weight has
≥ 50 % of its indexed play sites on a voice-like domain (architecture §9.3, line
1009; its own summary line reads "the SID stride appears in 90 % of tunes"), so
the object this schema names is the population's.

Citations: `anatomy:N` is a line of `playroutine-anatomy.md`; `jch:N`, `gt2:N`,
`sw:N`, `commando-floor:N`, `follin:N` a line of the matching `prototype-*.md`;
`gt2.md:N`, `jch.md:N`, `sw.md:N`, `commando.md:N`, `automatas.md:N` a line of
`out/recert-main/{gt2-je-suis-linus,jch-guldkorn-intro,sw-emomyst,
commando-song1,automatas}/tuneprog.md`.

Contents: 1 definition · 2 observable and certificate · 3 schema · 4 the
universal player · 5 effects as bounded accumulators · 6 the lift · 7 what
landed · 8 refusals and boundaries · 9 acceptance · 10 open.

---

## 1. Definition

| term | meaning |
| --- | --- |
| **trackerprog** | one data object, eight keys: `{meta, pitch, streams, accs, instruments, score, state0, globals}` — no code, no bytecode escape, no per-family construct. The nine hand exemplars carry this key set and no other |
| **universal player** | one fixed tick procedure (§4) shared by every trackerprog; `trackerprog/universal.py`, and the only executable in the layer |
| **certified-equivalent (T)** | for every tick of the source tuneprog's certified horizon, the universal player's observable (§2) equals the tuneprog's |
| **accumulator** | a bounded state machine `Acc(target, width, delta, bound, policy, rate, phase, links, scope)` (§5); the only per-tick mutation an effect may be |
| **stream** | a finite table of steps with holds and one terminator (§3.3); the only sequencing an instrument, a prelude or the score may be |
| **scoreprog** | what the lift emits *today*, and **not a trackerprog**: ten keys (`emit.KEYS`), the certified tick itself in a `program` key, its fetch regions cut out and its score in their place as data. It renders on an S4 interpreter, `trackerprog/interp.py`, never on §4. §6 |
| **lift (T0–T3)** | certified tuneprog + S6 naming plane → scoreprog, fail-closed: what does not fit is a `Refusal(reason, cell)`, never an approximation |

**Two artefacts, one target.** A trackerprog is data and a scoreprog is a
program with its score lifted to data, so they are two objects with two
renderers and two certificates (`attest.py` and `certify.py`). They share seven
key *names* — `meta`, `pitch`, `streams`, `accs`, `instruments`, `score`,
`globals` — at disjoint shapes under every one of them, and exactly **one
field**: `meta.commit_order`, the per-voice edge-register order. `state0` is the
trackerprog's alone; `producers`, `program` and `inputs` the scoreprog's.
Sharper still: `emit.replay` reads only `meta.horizon`, `score`, `program` and
`inputs` — a scoreprog's `pitch`, `streams`, `accs`, `instruments` and
`producers` are *readings* it recovered and prints, and its own renderer never
reads them.

The target is one object: a lift that emits what §4 renders, with no `program`
key. Nine families reach it by hand and the lift does not yet, which is
[trackerprog-backlog.md](trackerprog-backlog.md) B6 and B7. Until then no
document may say the lift produces a trackerprog, or that a trackerprog carries
a program.

Layer invariant, as a test: two trackerprogs lifted from different families must
render on the same player with no family branch; the source family survives only
as provenance in `meta`. The schema may not add a construct for one family — the
repo rule (architecture §11: two families, or one plus a survey count) applies
to schema rows exactly as to view heuristics, and the two exceptions below are
marked at their rows. Oracle chain, one link longer: `sidplayfp ⇐ PcodeVM ⇐
tuneprog ⇐ trackerprog`.

---

## 2. The observable and the certificate

The tuneprog observable — the ordered interleaved SID write list — cannot be the
trackerprog's: *which register a player writes before which* is an idiom (GT2's
ghost flush emits 25 writes **high to low**, `for r in 24..0`, `$D418` first and
`$D400` last, anatomy:766; Hubbard writes ctrl, pw, AD, SR per fetch,
commando-floor:201-205; JCH's write-out is its own order, jch:172-181), and
reproducing it would carry the player back in. The anatomy states the license
(anatomy:151-155): *write order matters only at the frame edge and for gate
edges*, a gate 1→0→1 or TEST 1→0 inside one call being a real event
(anatomy:153-154). Extending it to AD/SR is this document's claim, not the
anatomy's, whose AD/SR point is narrower — the envelope rate counter is not
reset by gate, so a note started against a running release counter has its
attack delayed (anatomy:125-133). The extension is *conservative*: it can only
make the certificate stricter, and it keeps SW 1.6's `AD,SR` and 1.9's `SR,AD`
(anatomy:1232) distinguishable rather than silently equal.

The trackerprog observable, per tick — implemented as `grid.reduce_tick`
(`grid.py:209`), whose `TickObs(edges, values)` is the whole comparison:

| rule | registers | reduction |
| --- | --- | --- |
| 1 | `ctrl`, `AD`, `SR` (`grid.EDGE`) | per voice, **every write kept in tick order**, unchanged repeats included — these may be written more than once a tick, and GT2's flush writes all three every tick whether or not they moved |
| 2 | `freq`, `pw`, `cutoff` (`grid.PAIRS`) | one value per tick, the last the tick left: the DACs are level-sensitive, and the two 8-bit-bus writes a pair takes are already a print convention (architecture §6.1). `pw` carries the SID's 12-bit projection (`grid.PW_HI`), `cutoff` its 3+8 split (`grid.PAIRS[6]`) |
| 3 | `res_route`, `mode_vol` (`grid.LEVEL`) | one value per tick, the same way — Hubbard's drum-then-arpeggio double write of `$D401` is last-wins under rule 2; a `mode_vol` carrying a sample stream is refused outright (§8) |

Precedents: the Ghidra emulate oracle already compares "the ordered sequence of
SID register changes, both sides reduced by the same rule" (architecture §5.4).
The certificate names both halves, kept and dropped:

```jsonc
{
  "source": {"tune": "...", "certificate_digest": "..."},   // binds to the tuneprog cert
  "compared": ["per-voice ctrl/AD/SR write order", "freq/pw/cutoff tick values",
               "res_route/mode_vol tick values"],
  "dropped":  ["order between registers of different classes inside a tick",
               "order between voices inside a tick", "cycle position inside a tick"],
  "ticks": 8236,                       // the whole certified horizon, never less
  "divergence": null,                  // else {tick, register, expected, got}
  "refusals": [],                      // non-empty ⇒ no trackerprog is emitted
  "loop": {"period": 6720, "first_repeat": 8235} | null,    // inherited claim, re-checked
  "end":  {"tick": 8235, "kind": "loop" | "fixed_point" | "horizon"}
}
```

Three source shapes, three ends. `complete` with `period > 1` closes: `loop`
non-null, `end.kind = loop`. `complete` with **`period = 1`** did not loop — the
state reached a fixed point and the tune *ended* (`jch-knob-at-night` period 1
at tick 8,576, architecture §9.2; Follin song 1 period 1 at call 12,996,
follin:93): `loop` null, `end.kind = fixed_point`, materialisation (§6) to
`first_repeat` — exercised, and the tune says where the end comes from: its
wrapper's own two-byte countdown closes the flush at frame 8,576
([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md) §3). A `horizon` tuneprog yields the horizon's score, `loop` null,
`end.kind = horizon`, `Order` ending in the `horizon` terminator (§3.6).

---

## 3. The schema

Serialised as tagged JSON in the S4 style (`ir.enc` vocabulary): `$trackerprog
$pitch $stream $acc $ins $pat $ord $cmd`, dicts as `{"$dict": [[k, v], …]}`. The
scoreprog of §6 is a different object with a different tag, `$scoreprog`
(`emit.to_json`); nothing below describes it.

### 3.1 meta

Cadence (`cycles_per_tick`, `source`), SID model, subtune, source tune and
family (provenance only), universal-player semantic version, and
**`commit_order`**: the permutation of `(ctrl, ad, sr)` the commit emits per
voice (§4) — one datum per tune, lifted like the pitch table, not a player
branch. Four certified families take three of the six values, one of them two
across its versions:

| source | `commit_order` | evidence |
| --- | --- | --- |
| JCH V20 | `(ad, sr, ctrl)` | `p_1616` writes `ad`, `sr`, `ctrl` in that order — jch:178-180, jch.md:656-658. Measured: any other order diverges on all 2,401 ticks of *Guldkornekspressen Intro*, and on **0** of *Knob at Night*, whose flush re-orders them ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md) §4.1) |
| GoatTracker 2 | `(sr, ad, ctrl)` | the flush runs `$D418`→`$D400`, so per voice offset 6, 5, 4 (anatomy:766) |
| SID Wizard 1.6 / 1.9 | `(ad, sr, ctrl)` / `(sr, ad, ctrl)` | anatomy:1232, the HR and note-start frames |
| Hubbard | `(ctrl, ad, sr)` | `sid[v].ctrl`, then `pw`, then `ad`, `sr` — commando-floor:201-205 |

`commit_order` orders one *act*'s edges, and **the tick is always a sequence of
acts** — one act per thing the tick did, a stream row's `sets`, an instrument's
note-on, one row command. There is no second form and no datum selecting one:
§2 rule 1 keeps every `ctrl`/`AD`/`SR` write, so a family that writes `AD` from
the instrument and again from the row's own effect on the same tick needs the
sequence (SID Wizard, diverging on 500 ticks of *Emomyst* without it —
sidwizard-trackerprog §4.5), and a family whose writes go through a shadow makes
one act of the tick and cannot tell the difference. Measured rather than
argued: rendering the acts sequence for the families that do not need it is
write-for-write identical over their whole horizons — Hubbard 11,780 ticks,
GoatTracker 2 12,000 × 2, 0 differing. The first draft's `meta.commit` ∈
{`order`, `acts`} is therefore struck: the general form costs the families
without the problem nothing, and a schema row no observation distinguishes is
not a row.

**`row_ends_fetch`: where the walk stops.** A family whose row *is* its boundary
consumes exactly one row per boundary. Follin's fetch is a walk over its own byte
stream — it takes every command it meets on the way to the note, up to 25 in one
tick — and `meta.row_ends_fetch` is the guard over §3.6's row facts that ends it
(there, a row that carries a length). Absent, every row ends the walk and the
walk is one step, which is what the other five families have; the loop flushes
its group *between* two rows and never after the last, so a one-row family is
bit-identical by construction and measures 0 over all eleven earlier builds'
whole horizons (follin-trackerprog §4.1).

### 3.2 pitch

`pitch: [u16; N]` — the tune's frequency table as the lift **materialises** it,
plus annotations it proves: tuning (`12-TET` where `recover._freq` names it),
base note, resolution. Materialised, not copied: storage is an idiom like any
other, so Blackbird's two byte arrays overlapped by 15 bytes, whose
quarter-semitone entries are the **sum of two entries of the same array at fixed
offsets** (anatomy:145-147), lift to explicit u16 rows — the values read, not
the bytes stored (prose-only family, so a projection). Hubbard's PAL table is `N
= 96`; GT2's `FREQ_LO`/`FREQ_HI` at `$14E3`/`$1543` are **96** entries each
(anatomy:742), of which a tune reaches what it reaches — JCH's `rec4[96]` prints
95 (jch:107). All nine players have such a table (anatomy:141), already the
`freq_table` role.

Every *note* elsewhere is an index into this table or a signed index offset.
Accumulator deltas are not notes: they are in the **target register's own
units**, raw frequency for a `freq` target (Hubbard's `porta & $7E` step,
commando-floor:236-238; GT2's 16-bit speed-table entry, gt2.md:683-684), and
§5's `interval(n)` is the bridge from a note interval into those units.

`interval(n)` is `pitch[n+1] - pitch[n]`, and `0` where there is no semitone
above `n` — at the top of the tuning, and over a sound that is no pitch at all.
The first draft carried a second form, `tablestep(P, n, shift)`, which is
`interval(n) >> shift` with the shift folded in and the top of the tuning left
to raise; the shift is `shr`, which the grammar already has, so the two are one.
T1's rule of the same name (§5, the classifier's table-difference recogniser)
is unchanged: it names the shape read out of the *source program*, and what it
emits is this.

**Past the top of the tuning is a producer, and its bound is the index's own.**
§6's rule that the overrun is a producer takes its first exemplar here. Follin
reads `notetab[note + transpose]` with a 97-entry table and a one-byte index, so
past entry 96 the read is what the image holds after the table — for the low
half, the high half's own start, and then the sound-effect pointers. That is the
`beyond` record §5 already had, attached to the stream whose row makes the
producer, and its `words` are bounded by the *index*, not by the notes the score
holds: 159 of them, `$61` through `$FF`, each stated. A bound derived from the
score's own note bytes and transposes was written first and two sound effects
walked straight past it (follin-trackerprog §4.6).

### 3.3 streams

The one sequencing form. A stream is a finite table of steps:

```
Step = { when:  [ guard, … ]               // the one guard shape in this schema
       , sets:  [ set(target, value), … ]  // assignments, in this order,
                                           // all inside one tick (Walker's gate 1→0→1)
       , point: [ (slot, row, keep), … ]   // re-point a stream, keeping its hold or not
       , op:    acc(acc_id) | pitch(offset | absolute) | cmd(name) | none
       , run:   [ acc(acc_id), … ]         // an acc the step runs on every tick it holds
       , hold:  k ticks (k ≥ 1)
       , next / jump: row }                // where the step goes, and where a row jumps
```

Every field is optional and every one is a *step's*, not a family's: an
instrument's note-on (§3.5), a prelude, a row program's stream step (§3.6) and
a table are all this object. The first draft named a terminator, `jump(row) |
halt`, as a field of the stream; the exemplars put the jump on the row that
carries it and nothing reads a `halt` — a stream with no jump is one, so the
row's `jump` is the whole of it. #310 struck it from the grammar and four of the
five tools went on writing `term` for two more families, the print rendering it
beside the stream's name: striking a row from the schema is not striking it from
the object, and both are now done (§7).

A stream has a `rate` — **one meaning everywhere in this schema** (§3.6, §5): a
divider, the object advances once every `k` ticks, `k ≥ 1`, so a step occupies
`hold × rate` ticks. `k` is the degenerate form; where the score can *set* the
divider the rate is a cell and its reload, `{cell, reload}`, which is SID
Wizard's `ARPSCNT` against `ARPSPED & $3F` with a waveform row and two effects
also writing the cell (sidwizard-trackerprog §4.3). And a step's counter is read
either before or after its own move — #297's epochs, `epoch: entry` — which is
what says whether the tick that consumes the step also runs it: GoatTracker 2's
pulse row sweeps on all `n` of its ticks, SID Wizard's on the first `n` of `n+1`
(sidwizard-trackerprog §4.4). There is no "steps per tick": defMON's cascades run up to
8×/frame under a CIA cadence, but the tick *is* the entry ("sidTAB row = DL+1
calls", anatomy:213). Four families read that way — Hubbard `pwdelay -= 1; if <
0: pwdelay = pspeed & $1F` (commando-floor:223-225), JCH `phase -= 1; if phase <
0: phase = b1747` (jch:117-124), GT2's wavetable hold `if T16F9[1 + t1] ==
timer_4 … else timer_4 += 1` (gt2.md:564-569), defMON `DL+1` (anatomy:213).

What lands here: GT2 wavetables (`T16F9` rows of wave/note with `$FF` jump, the
`timer_4` compare the hold — gt2.md:564-569; the field name alone carries no
role, gt2:84); GT2/SW pulse, filter and speed tables; JCH's two column programs
— `rec6` pulse, **4** columns `[init/keep, Δ, dir|frames, next]` (`$FF` = keep,
else nibbles to `pw_lo`/`pw_hi`; `pw += b1894`; `& $80` direction, `& $7F`
frames — jch.md:527-538) and `rec7` filter, **4** columns `[init, Δ, frames,
next]` (jch.md:544-551) — both four, and the print's three is a region's derived
origin (backlog P1), not a fact about the tune
([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md) §6); counts at
jch:82 and jch:106-107; SW tempo programs; defMON
sidTAB rows (variable-length register-column records with delay and jump,
anatomy:211 — the form at its most general); Blackbird's pitch and wave programs,
whose loop marker is the *next* byte and whose backward jump is folded into the
row that lands on it; and the prose-only Galway and Walker programs. The stream is what all of these already are: *rows,
holds, one terminator*.

### 3.4 accumulators

Declared once in `accs`, referenced by streams, instruments, preludes and
commands. The full object is §5. Each carries **`step`**, the exact per-tick
recurrence a player computes `cell(t+1)` with:

```
step = { width   : bits of the value
       , value   : [{cell, shift, bits}, …]          # the bytes the value is made of
       , clauses : [ {site, rank, kind, when, copy, …}, … ]   # in tick order
       , inputs  : { "cell@rank": {before, clauses}, … } }
clause = step: {sign, delta, carry, comp, times} | action | opaque: {value} | half: {value, shift}
when   = [{test, truth, at}, …]                     # the branch's own condition, at its decider
term   = {const} | {index} | {self, shift, bits} | {table, region, addr}
       | {cell, addr, epoch: pre | post | mid, before?} | {pair: [term, term]}
       | {op, a, b, w} | {sel: [{when, value}, …]}
```

`rank` is the statement's position on its call chain (a tuple, compared
lexicographically), and every read's `epoch` is decided by that rank against the
writes of its cell: `pre` (last tick's value) before them all, `post` after them
all, `mid` between — the cell's own clauses up to `before`, carried in `inputs`,
which the acc's own proof covers. The replay
(`accstep.prove`) applies the clauses in rank order from `cell(t-1)` and requires
`cell(t)` at every tick; an acc it cannot state (`inexact recurrence`, with the
term and site) or reproduce (`divergent recurrence`, with the first tick) is a
refusal, never a record.

### 3.5 instruments

```
Ins = { adsr: (ad, sr)
      , prelude: (stream, early: k) | null   // ends k ticks before the next row boundary
      , on_note: [ Step, … ]                 // an inline §3.3 stream: the note-on's own
      , accs:    [ acc_id, … ] }             // the modulations armed at note-on
```

**One inline stream, three places.** An instrument's `on_note`, a prelude and a
command's `rows` are the same object — guarded §3.3 steps — and one procedure
runs all three, so a guard has one spelling everywhere in the schema and never a
positional slot beside the thing it guards. Each is **one act** (§2 rule 1): one
thing the tick did, however many guarded rows say it.

`on_note` is a stream and nothing else — guarded rows of `sets` and `point`,
where `point(slot, row, keep)` is §3.6's own re-point. The first draft gave the
note-on three fields (`sets` unconditionally, `note_sets` and `points` only
where no tie held) and the player kept two lists with a `return` between them;
the tie is a fact of the row (`when tie == 0`), so it is a guard like any other
and the three fields are one. A step's own `wave`, `pulse`, `filter` and `pitch`
streams are named §3.3 streams with a `rank`, re-pointed from here — not a slot
map on the instrument, which no exemplar carries.

Hard restart is **not** one fixed shape, and the first draft's `{early, ad, sr,
first_ctrl}` record cannot hold the families: SW 1.6 writes AD,SR and 1.9 SR,AD
(anatomy:1232); Blackbird's prelude has no TEST bit (anatomy:133-135); Walker
retriggers the gate off/on *inside one call* with no early frames
(anatomy:139-140, 214). A prelude is therefore just a stream (§3.3) of `set`
steps ending `early` ticks before the next row boundary — no new construct, and
the write order §2 compares is the stream's own step order:

| source | prelude | evidence |
| --- | --- | --- |
| JCH V20 | `early = 2`; row `set(ad,$0F) set(sr,$00) set(ctrl, mask $FE)`, note row `set(ctrl,$09)`; the flag that arms it is a **cell the note-on sets**, not a column the prelude reads, and the prelude's own frame leaves the machine out | jch.md:501-511, jch:150-158, [prototype-jch-trackerprog.md](prototype-jch-trackerprog.md) §4.9 |
| GoatTracker 2 | `early = gatetimer` (instrument column 7); row `set(ctrl, wave & $FE)`, note row `set(ctrl, firstwave\|TEST)` | anatomy:214, anatomy:742 |
| SID Wizard | `early = 2`; rows in the version's own AD/SR order, then `set(ctrl, TEST\|gate)` at tick 2 | anatomy:1232-1233 |
| defMON | `null`, and the data is right: `WG=00 AD=0F SR=00` → hold → `WG=09` is the first three rows of the sidTAB program the row starts, so nothing schedules it and there is no `early` (defmon-trackerprog §8) | anatomy:214 |
| Blackbird | `early = 2`; `set(sr, 0) set(@wavemask, $FE)` — and **no `ctrl` write at all**: the gate goes off because the engine ANDs that mask into every control byte for the next two frames. The note row is five writes in three acts, `sr $0F` · `ad 0, ctrl 1` · the instrument's `ad, sr`, so `AD` and `SR` each appear twice and `commit_order` says only that `ad` comes first ([prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md) §5). The first draft's row, written from anatomy:133-135 before a certificate existed, had the `ctrl` write and two acts |
| Hubbard, Galway, Follin | `null` — Hubbard cuts notes with SR=0, Galway pulses TEST at note-on | anatomy:137-140 |

GT2's `gatetimer` **is** `early`, not a second field ("gatetimer frames early,
firstwave with TEST", anatomy:214), so the first draft's `gate.timer` row is
deleted — single-family only because it duplicated `early`. The nine-family
"sound definition" row (anatomy:211) reduces here: Hubbard's 8-byte SID image +
fx bits = `adsr` + armed `accs`; GT2's 9 columns + pointers = `adsr`, `prelude`,
four stream refs. defMON's "the sidTAB row *is* the instrument" is **wrong**,
and the fourth exemplar says why: a sidTAB row is a *stream* row, a voice runs
two such programs at once, so no single `ins` can name them and both are §3.6
`point` commands — defMON's one `Ins` carries neither `adsr` nor `prelude`
([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md) §8).

### 3.6 score

```
Pattern = rows of Event
Event   = { sounds: bool            // the row starts a sound -- the one field that says so
          , note: index | none      // its pitch; none = the instrument's own (§3.5)
          , gate: on | off | none   // the row's own gate statement, where a family has one
          , tie:  bool              // re-target without re-triggering
          , ins:  instrument | none
          , arm:  Cmd | name | [name, …] | none
                                    // in row order; §4 emits them in that order.
                                    // a name indexes `score.commands`; four shapes,
                                    // one per family group -- see the backlog
          , dur:  rows }
Cmd     = { arms:  [ arm(acc_id, overrides), … ]   // what the row arms, and its overrides
          , links: [ acc_id, … ]                   // the accs its own events zero (§5)
          , sets:  [ set(target, value), … ]       // a shadow, a cell, a global, a flag
          , point: [ (slot, row, keep), … ]        // re-point a stream (the old set_stream)
          , all:   [ set(cell, value), … ]         // one cell of every voice: global tempo
          , flags: { name: value }                 // what a producer leaves for another
          , tie:   bool }                          // re-target without re-triggering
Order   = per-voice sequence program over
          { play(pattern, transpose, repeat, vol?, tempo?)
          | jump(step) | call(step, ret) | ret
          | mark(count, next) | loop(next)
          | stop | horizon }
```

**A command is a record, not an opcode.** The first draft listed nine named
commands — `set_tempo`, `set_vol`, `set`, `set_register`, `set_stream`, `arm`,
`disarm`, `porta`, `filter_set`, `gate`, `break` — and not one of the three
certified families emits any of them: a tempo is `sets` on the tempo cell, a
volume is `sets` on the global `$D418` nibble, a portamento is `arms` with its
overrides, a `set_stream` is `point`. The record above is what the exemplars
carry (GoatTracker 2's 43, SID Wizard's 44 and 60), and it is smaller *and*
more general than the list it replaces, because a family command is a
combination of fields rather than a name the schema had to have foreseen. What
a command may not do is still §8's boundary: a residue no combination of these
fields expresses is a `command residue` refusal, not a new opcode.

`for(n){…}`, `call(seq)` and `ret` were struck with them, resting as they did on
Galway and Follin, which were prose-only. **The exemplar has landed, and the
grammar gains what it shows and no more**
([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md) §4.2). Follin
has no orderlist/pattern split at all — one byte stream per voice is both, and
its structure is `$8A` call, `$8B` return, `$82`/`$81` counted loop, `$87` jump,
`$86` stop — so a `play` step may carry an `op`, and the five above are the five
that family emits: 302 calls, 126 returns, 196 marks, 195 loops, 39 jumps and 46
stops across its 32 subtunes.

Two spellings the exemplar forced rather than the draft foreseeing them. **A
call names where it comes back to**, not merely where it goes: the 6502 pushes
`ptr + 3`, an address, and the order of the block list is not the order of the
program. And **`mark` and `loop` are two steps, not one `for`**: they are two
bytes in two places with the body between them. The other five families carry no
`op` at all and take the `play` list as before.

**The order program runs at two positions, and it is one program.** A voice's
`play` cursor steps in two places — `sequencer_step`'s walk, where the row
boundary consumes the score, and `advance`, the cursor of a clock that fetches
its row ahead — and both are `order_step`. `advance` used to step `orderpos` by
one of its own and discard the `op` `play_of` returns, so a family whose clock
prefetches walked past `call`, `mark`, `loop` and `stop` as though each were
`play`. Nothing observed it: the three prefetching families have flat orders and
the two with order programs do not prefetch, so *when* the row is read and *what
shape the sequencer is* were one flag with no exemplar to separate them. They are
separate now — 0 differing of 332,358 over thirty builds against the merge base's
own renders (`trackerprog_poison.py --emit-digests`/`--against`), the change
reaching no certified tick, and three hermetic snippets with `fetch` in
`meta.tick` that mis-render on the old body kept beside them
(`tests/trackerprog/test_universal_fetch.py`).

**The counted loops nest, and the ninth family is why.** The sixth's are two
bytes over one register per voice that nothing saves or restores, so the object
said the loops do not nest by having one cell. Galway's `For` pushes the loop's
start *and* its count onto the **same 8-deep stack** its `Call` pushes return
addresses on, and the main theme opens a loop inside a live one from tick 3,072
— six times on voice 1 and three on voice 2, and with one register the outer
loop runs the inner one's count and the score plays the wrong block. A voice
therefore carries a loop *stack*, which a family whose loops do not nest sees as
the register it had: **0 of Follin's 111,763 ticks differ**
([prototype-galway-trackerprog.md](prototype-galway-trackerprog.md) §4.1).

**`stop` stops one voice, not the tune.** Every other certified score ends the
tune; this one ends each voice by itself (`$86` clears that voice's active flag
and the routine moves on), and the filter goes on writing. So the terminator is
per voice, `state0.stopped` seeds it from the entry — a sound effect starts one
to three voices and leaves the others stopped — and a stopped voice runs no
clock (follin-trackerprog §4.3).

**And what it stops is one datum, `meta.stop` ∈ {`voice`, `sequencer`}.** Follin's
per-frame block tests the active flag first and skips the whole voice — the
modulators, the gate and the write-out with it (anatomy §3.6.3). Galway's eighth
`Ret` clears the run bit and returns from the *voice routine*, so the sequencer
stops and the engine plays the note out, counts its release down and frees the
chip several hundred ticks later; the tick the score stops on is the voice's
last, and every tick after it runs every phase but the row. Its eight
sound-effect subtunes are that value with nothing else in them — three voices
stopped from tick 0, no score at all, and the certificate is the engine over the
record the entry left (galway-trackerprog §4.2).

**The note column is a token class, and the layer spends it.** The first draft
wrote `note: index | rest | hold | keyoff | keyon`, which is the *source byte's*
range table, not the music. Every family packs more than a pitch into that byte
and each packs something different: GT2 `note $60–$BC | rest $BD | keyoff $BE |
keyon $BF | packed rest $C0+` (anatomy:872); SID Wizard `note $01–$5F | set
vibrato amplitude $60–$6F | packed rest $70–$77 | porta $78 | sync on/off
$79/$7A | ring on/off $7B/$7C | gate on $7D | gate off $7E` (anatomy:1204); JCH
`dur/instr/super/note/rest/hold` (anatomy:210); Hubbard a keyoff *bit* in the row
byte. The anatomy already names the general fact — "byte ranges as token classes
… tokenizer thresholds = the `CMP` immediates" (anatomy:2833) — as a player
idiom with a generic fix, alongside `X = voice*7` and the 1-based tables.

Admitting `keyoff` as a *note value* therefore admits `sync on` as one, and the
rule collapses. The rule that survives all four is
[prototype-commando-trackerprog.md](prototype-commando-trackerprog.md) §4.1's:
**a value that is not in the pitch table is not a pitch, so it is not a note.**
Each token the byte packed becomes its own field — `sounds`, `gate`, `dur`,
`tie`, `arm` — and the note column holds a pitch or nothing:

| the byte says | the event says | Hubbard | GoatTracker 2 | SID Wizard |
| --- | --- | --- | --- | --- |
| the row starts a sound | `sounds` | row bit 6 clear | a note byte `$60–$BC` | `$01–$5F` |
| its pitch | `note` | index, or none for a drum | index | index |
| a gate statement of its own | `gate` | — (its bit 6 *is* `sounds`) | `$BE` / `$BF` | `$7D` / `$7E` |
| rows the event spans | `dur` | 1 | `$C0+n` | `$70–$77` |
| re-target, do not re-trigger | `tie` | row bit 5 | effect 3 | effect 3, or `$3F` in the instrument column |
| everything else | `arm` | the porta byte | the fx nibble | `$60–$77`, `$78–$7C`, and both effect columns |

**The row is a program, and one procedure runs it.** `meta.row` is an ordered
list of steps over the event — `{sets}` assignments, `{ins}` the instrument the
row names, `{stream}` a guarded §3.3 stream, `{note}` the sound the row keys,
`{commands}` the row's own — each with an optional `when` over the row's facts
(`sounds`, `keys`, `newins`, `field`, `gate_stmt`, `tie`, `gate`, the mask
below). Which steps a tune has, and in which order, is data:

| source | `meta.row` |
| --- | --- |
| Hubbard | `ins` · `note` when `sounds` · `sets @wave` · `stream note_on` · `commands` |
| GoatTracker 2 | `note` when `sounds` · `stream note_on` when `keys` · `commands` |
| SID Wizard | `sets @pending` · `ins` · `stream gate_row` when `gate_stmt` · `note` when `sounds` · `stream pitch_row` when `sounds` · `commands` |

The first draft gave each of these a meta key of its own — `note_row`,
`gate_row`, `pitch_row`, `row_sets`, `row_commits` — and a family whose clock
fetched ahead ran a *different procedure* from one whose clock did not, which is
the two-grammars failure §4.8 of the GoatTracker 2 exemplar names for `sounds`,
one level up. It showed: `note_row` fired at the note-on in one family and at
every row in another, under one name. Five keys and two procedures reduce to one
list and one `apply_row`, and the difference between the families is the list.

**The row program runs at two positions, and it is one program.** A family whose
clock fetches ahead commits some of the row where it *reads* it rather than where
it lands, and `meta.stage` is that list — the same steps in the same grammar as
`meta.row`, under guards over the same facts, run by the same procedure at the
`fetch` phase. Three families carry one: GoatTracker 2 stages the instrument, the
gate mask and the command the voice keeps; SID Wizard stages the row's instrument
into a cell of its own, because its tables live inside the instrument record and
moving `ins` early would move the tables; JCH stages the gate mask, the order's
transpose, the pitch and the row's whole commands. The staging's payload carries
§3.6's facts plus three values it copies rather than tests — `ins` (the row's, else
the voice's), `note` and `transpose`.

The first draft made this a seven-value enum, `meta.prefetch ∈ {ins, hrins, gate,
note, transpose, arm, cmds}`, one value accreted per family, which is the
`note_row`/`gate_row` failure of this section one level down: a name per call
site, five of the seven doing what a `sets` row already does under a guard the
grammar already has. Two survive as steps of their own because neither moves a
cell — `{"commands"}`, which §3.6 already had, and `{"hold"}`, the command the
score gives a voice to keep (§7).

`sounds` is the field §4's tick reads to decide whether a row keys a note, and
it is the *only* one: an object that answered it from `gate` in one family and
from `note` in another would be two grammars. `note: none` then means one thing
everywhere — no pitch of the row's own — and where such a row sounds, §3.5's
instrument supplies the frequency (Hubbard's drums; commando-trackerprog §4.3).
The ctrl mask a row leaves is `$FF` where it sounds and `$FE` where it does not,
overridden by an explicit `gate`. The two are a **chip** fact and not a datum: the
waveform byte carries its own gate bit, ctrl bit 0, and the row says only whether
to keep it, so the masks are the whole byte and the byte with that bit cleared.
All five families write these two, none can write another, and the player names
them once beside `REG` and `EDGE` rather than admitting a `meta` row no tune
would vary (§7).

Five changes from the first draft, each forced by a source (a sixth, `set` for a
shadow against `set_register` for the chip, is struck with the opcode list: both
are a `sets` entry, and *when* it reaches the chip is the target's own — a
shadow register defers, a producer does not):

| change | why, and the evidence |
| --- | --- |
| `play` gains `vol?`, `tempo?` | SW's orderlist columns are pattern, transpose, **volume, tempo**, stop, loop; GT2's pattern, repeat, transpose, loop; JCH's `[transpose] pattern` (all anatomy:209). Optional, `none` where a family has no column. `vol` lands on the one global `$D418` nibble (sw:109), so three voices' columns resolve by last-writer, which §2 makes exact |
| a `horizon` terminator | a source materialised only as far as the certified ticks reach, distinct from `stop` (Hubbard's `$FE`, SW's stop — anatomy:209) and from `jump`. The same fact as `end.kind = horizon`, stated twice |
| `arm(acc_id, overrides)` replaces `arm(acc_id, param)` | `Acc` has no `param` and should not: GT2's vibrato parameter selects a bound *and* a step (`b1096 = T1851[y] & $7F` is speedcmp, gt2.md:812; `T1863[y]` the depth or shift, gt2.md:653-684), so the command re-binds a subset of `{delta, bound, rate, phase}` on a declared `Acc` |
| a command's register target is a literal `0..24` | Follin's `$85` lists write `$D400+r` for an arbitrary register of any voice (anatomy:1803; `sid.reg[a75] = …`, follin:160-167) and resolve, because T2 materialises decoded score bytes exactly as it materialises pattern rows. Where the index does not resolve, the refusal is `command residue` (§8) — the 36 `index not a voice` sites T0's sweep already names one layer down (backlog §4, W4). **Rendered, and it needed no form of its own**: the target is §3.7's `reg.N`, which JCH's write-out earned first, so the hand transliteration of `$85` is a `sets` entry like any other (follin-trackerprog §5) |
| `point(slot, row, keep)` | GT2 commands 8/9/A re-point the wave, pulse and filter tables and zero the matching hold (`waveptr=A (wavetime=0)`, anatomy:876) — a re-point plus a link (§5), not two opcodes. It is a field of a §3.3 step, and a command's writes *are* a §3.3 stream, so there is one shape and one guard |

**The row clock is a counter, and there is no second form.**

```
tempo = { cell,  the counter the tick moves
        , step,  by how much: -1 counting down to a boundary, +1 counting up to one
        , boundary: [guard, …]      // which of its steps the row lands on
        , reset:    [{when, sets}]  // what it does at its end; the first that holds
        , rate, phase               // the ticks it steps on at all, a divider
        , fetch: [guard, …]         // the step the row is read at, if not `early`
        , early: [guard, …] }       // the step the next row is near enough on
```

The first draft had three, `meta.tempo.form ∈ {divider, countdown, counter}`, and
three procedures behind them; each family's is a *value* of the one above.
Hubbard's and defMON's **divider** is the rate, a step of `−1` on the row's own
length and no reset, the length being what the sequencer reloads. GoatTracker 2's
and JCH's **countdown** is a step of `−1`, a boundary at zero and a reset that
reloads when the cell goes past it — and GoatTracker 2's funk tempo, which was a
`tempo.alternate` record of its own, is one more reset clause ahead of the plain
one, taking the row's length from a stream and toggling the cell that indexes it.
SID Wizard's **counter** is a step of `+1` with two reset clauses that zero it and
move the tempo program on. `boundary`, `fetch` and `early` are guard lists like
every other guard in the schema, and the step a tick is is the virtual cell
`phase`, which any of them may read (sidwizard-trackerprog §4.1).

A command is named by **what it does**, never by the index a family's dispatch
gives it: GoatTracker 2's `T144A` nibble and SID Wizard's `BIGFXTABLE` index are
the patched jump the lift already spends (gt2:16, anatomy:2799), so a score that
named its commands `F:07` would be carrying the jump table one layer up. The
cost, measured on the family that has three dispatchers: three of SID Wizard's
effects have the same encoding in *two* columns, so a score naming them by what
they do cannot say which byte carried one — §8's "a preimage", made concrete
(sidwizard-trackerprog §4.10). And
whether a command outlives the row that gave it is one datum, `meta.row_command`
∈ {`held`, `spent`}: GT2 re-runs the last command at every row (effect memory,
anatomy:876's tick-0 dispatch running unconditionally), Hubbard spends it on its
row. It is a property of the tune, not of the row clock.

A tempo command — `sets` on the tempo cell, or on the cell a tempo stream is read
through — has two certified families: GT2's `funktempo`, loading a
two-entry alternating tempo (`funktempotbl[0..1] = speedtbl[A]`, anatomy:876),
and SW's tempo program (anatomy:213). Per-**voice** tempo likewise: GT2's
command F sets one voice when bit 7 is set and all three otherwise
(anatomy:876), its tempo per channel (anatomy:213), and SW carries a tempo
column per orderlist, one per voice (anatomy:209). So `tempo` is per voice with
a global default.

The order grammar is a sequence *program*, not only a list, because two
families' scores are programs (Galway's call/jmp/for-next with an 8-deep stack,
Follin's byte streams with call/loop — the dispatch at follin:160-175); a flat
tracker orderlist is the degenerate case. It stays data: no conditionals, no
arithmetic, statically bounded. Commands are the universal set only; a family
command not expressible as one of these plus an `arm` is a refusal, not a new
opcode.

### 3.7 globals

The filter as a global channel (cutoff streams and accumulators, resonance,
routing), master volume, per-voice and default tempo. Filter ownership — SW's
owner voice, JCH's "filter runs on track 0" (which is a **byte of the tune's own
filter table**, not a constant of the player — prototype-jch-trackerprog.md §8)
— is last-writer over the global
channel, which the observable makes exact without an ownership construct.
Keyboard tracking (SW `CKBDTRK`) is **not** an `interval` term: it adds an
*absolute* table entry, `a11 = FREQ[$E + (freq_idx + b1024[$2C + b1024_idx])]`
then `cutoff_hi = (a11 + cutoff_hi) + c6` (sw:110-116), where `interval` is a
difference of adjacent *tuning* entries. It is §5's `tabcell(T[c])` delta on the cutoff
target — the same construct defMON's oscillator uses (automatas.md:433-437),
where the table happens to be the tuning and the object spells it `tuned`, so
it earns its row on two families and needs none of its own.

**The channel steps before the voices or after them, and which is data.**
`globals.streams` runs the channel ahead of the voices, which is right for one
the voices *read*. Follin's they **write**: the owner voice's note-on reloads
`#cutoff` from `#cutreset` and the filter sweeps from there in the same frame,
so sweeping first writes the un-swept value — 383 diverging ticks of its song 0.
`globals.after` is the second list, and a tune declares which of the two it has
(follin-trackerprog §4.4).

---

## 4. The universal player

Normative semantics — anatomy §2's pseudocode made exact. One tick:

```
tick():
    for v in voices:                              # per-voice tempo, §3.6
        tempo[v].step()                           # a divider or a stream; row clock
        for phase in meta.tick: run(phase, v)     # the voice's tick, §4.1
        commit(v)
    commit_global(): cutoff one value (split 3+8), res_route, mode_vol

commit(v):                                        # one group of the tick's writes
    the voice's freq/pw producers, in declared order    # §2 rule 2 keeps the last
    then its edge writes, the tick's acts in order,
    each act's own in `meta.commit_order`               # §2 rule 1, §3.1
```

**§4.1 The voice's tick is a declared order.** `meta.tick` is a list of phases:

| phase | what it is |
| --- | --- |
| `fetch` | read the row the clock runs ahead of, and run `meta.stage` over it — §3.6's own row program, one tick position earlier (§3.5) |
| `prelude` | the instrument's early rows, where the clock says the next row is near |
| `row` | the row boundary: consume the Event and run §3.6's row program |
| `machine` | the voice's streams and armed accumulators, in `rank` order (§3.3, §5) |
| `commit` | a group boundary — what the tick has written so far, written |
| `{stream: s}` | a stream every path of the voice ends on, run whatever the row did |

Which phases a tune has, and in which order, is one datum: a family whose fetch
runs ahead of the tick's own modulators is the list saying so, and a family
whose prelude commits ahead of its producers is `prelude` before `commit` before
`row`. A row that spends its tick (§3.6's `row_consumes_tick`) skips the phases
after it; a `{stream}` phase still runs, being the voice's write-out rather than
a modulation:

| source | `meta.tick` |
| --- | --- |
| Hubbard | `prelude` `commit` `row` `commit` `machine` |
| GoatTracker 2 | `row` `commit` `machine` `fetch` `prelude` `{stream: exit}` |
| SID Wizard | `fetch` `prelude` `commit` `row` `commit` `machine` `{stream: exit}` |

The first draft hard-coded this sequence and reached the three families with two
flags on top of it (`tempo.early_first`, `meta.voice_exit`) and a fixed
three-list commit whose first list existed only to put a prelude ahead of the
producers. Both flags and that list are the order, said once: GoatTracker 2's
prelude runs *after* its machine and must win the register, SID Wizard's runs
before and must lose it, and no third datum decides — the list does.

Producers and edges deposit as §2 compares them: the edge writes into the
ordered ctrl/AD/SR list, the producers as 16-bit values §2 reduces to the tick's
last. Voice order inside a tick is dropped by §2 and said so in the certificate.

**Producers, not a sum.** The first draft's `freq = pitch[note + …] + Σ accs`
cannot reproduce Hubbard: within one tick its vibrato, portamento, drum and
arpeggio each *store* `freq` independently, the arpeggio storing an absolute
`FREQ[note + $C]` (commando-floor:213-251) — a sum of deltas is the wrong
algebra. A voice carries an ordered **producer list** per 16-bit target, each
`Producer(target, mode, value)` with `mode add` meaning `pitch[note + transpose
+ offset] + Σ accs(this)` and `mode set` an absolute value — a table entry or a
live cell. `commit` evaluates them in declared order and §2 rule 2 makes only
the last observable, which is the chip's own semantics. GT2, JCH, SW and defMON
declare one `add` producer per target and degenerate to the old formula; Hubbard
declares four on `freq`.

Everything a real player does beyond this — ghost register files and flush
loops, unrolled voices, `X = 7v` double-duty indices, SMC-patched dispatch,
1-based tables, relocation, stack tricks — is compilation, already decompiled
away by S4–S6, and leaves no residue in the data. That list is the "symptoms"
tables of the tracker prototypes, each row a player idiom the tuneprog erased;
the trackerprog is the claim that what remains fits this procedure.

---

## 5. Effects as bounded accumulators

```
Acc = { target : freq | pw | cutoff | note | wave-param | gate-mask
                 | split(k, 8)                    # one value across two registers
      , width  : 8 | 11 | 12 | 16                 # the value's modulus
      , delta  : const(k)                             # signed
             | field(cell, mask)                      # a masked field of a live cell
             | tabcell(T[c], signed = k | unsigned)   # an absolute table entry at a cell
             | interval(n)                            # pitch[n+1] - pitch[n], 0 at the top
             | repeat(Δ, n)                           # n·Δ, a triangle's closed form
             | Δ + flag(name)                          # any of the above, plus a live carry
      , bound  : { interval: [lo, hi], from: proved | projected | observed
                 , witness: <guard | mask | period> }   # lo, hi constants: asserted
      , amplitude : { interval: [lo, hi], shift: k }    # reflect / reflect-complement's
                                                        # own turn, not a claim
                  | { count: n, cell: <phase counter> }  # ..or the turn counted, where
                                                        # the cell is not one Acc's
      , flag   : { name, seed?, unguarded? }      # the carry this step's own arithmetic
                                                  # leaves the next producer (below)
      , policy : wrap | reflect | reflect-complement | clamp(v) | halt | reload(v)
      , rate   : every k ticks (k ≥ 1)            # the §3.3 divider, one meaning
      , phase  : bit(self, k) | bit(cell, k) | cell != 0 | fn(global_counter) | acc(id)
      , cell   : the value's own, in one vocabulary -- `tick`, a voice cell,
                 `#global`, `ins.pw`, `shadow.<pair>`, any of them with `.hi`/`.lo`
      , scope  : read from the value cell's region index domain }
```

An `Acc` has no name of its own: it is named by the key `accs` declares it
under, which is the name a stream's `op`, an instrument's arm, a command's
`arms` and a `publish` subscription all use. Four tools wrote that name a second
time as an `id` column and the player read the column rather than the key, which
is a second spelling of one fact and is struck (§7).

**Bounded** is the invariant, not a hint: `bound × policy` makes the reachable
value set finite and statically known; the trackerprog states each interval and
the renderer asserts it — the tuneprog's envelope discipline one layer up. It
asserts it *as of §7's second package*, and until then the sentence was a claim
the code did not make: `bound.interval` was read only as the fold and turn
threshold of `reflect-complement` and `reflect`, and `from` and `witness` were
read nowhere. `Player.store` now holds every move an accumulator makes to the
interval its record declares, and five of the sixteen records did not survive
that — which is what an invariant nothing checks is worth.

Two consequences the assertion forced, both of them §5's own words taken at
face value.

*Statically known* is what `bound.interval` says, so it is **two constants**.
An interval that reads a live cell is not a statically known reachable set; it
is arithmetic the step does.

That arithmetic is **`amplitude`**, and it is not a bound. The threshold a
`reflect` turns at and a `reflect-complement` folds at is the triangle's own
swing: it decides what the step does and claims nothing about where the cell
goes, and in neither certified family are the two the same interval. GoatTracker
2's vibrato phase swings against `speedcmp` and keeps the byte, and Hubbard's
pulse sweep turns at `$800`/`$EFF` and keeps twelve bits — a step of `$E0` from
`$E60` lands at `$F40`, which the turn test does not see, and the next one wraps
to `$020`. The field the step reads and the interval the record claims were one
key, and one key cannot be both.

**A turn is a bound on the value, or a count of the steps.** Both are the
triangle's own arithmetic and `amplitude` carries either. The bound is exact
wherever the cell an `Acc` moves is that `Acc`'s alone, which is every family
but the eighth: Walker's pitch triangle and pitch bend sum into **one**
frequency offset per voice, and both have moved it on 1,140 of that horizon's
9,949 modulator steps, so the value there is neither modulator's and no
interval on it is either's swing. `count` is the period and `cell` is where the
modulator counts its own steps — in §5's cell vocabulary, so a modulator on the
global channel counts in a `#global` (walker-trackerprog §4.1).

The three `bound.from` tags differ:

| `from` | source of the interval | evidence |
| --- | --- | --- |
| `proved` | a guard on the update path | GT2 `if b14A0 < b1096` against the speedcmp cell (gt2.md:812,819); Hubbard `if ins.pw_hi == $E` … `== $8` (commando-floor:230-233) |
| `projected` | the write's own mask — the interval the chip can see | Hubbard's pw is 12-bit only because the store is `(pw_hi + carry) & $F` (commando.md:380, commando-floor:325); SW's `cutoff_lo & 7` (sw.md:873,881); `grid.PW_HI` is the same projection on the observable side |
| `observed` | `history.py` over the certified horizon, under the period witness | JCH's pulse and filter segments have no guard and no mask: `voice[x].pw += rec6[…].b1894` for `timer_4` frames (jch.md:527-538), `cutoff_hi += rec7[…].b1860` for `timer_5` (jch.md:544-546). The bound is the register width; the *stream* ends the segment, not a compare |

**The 6502 carry has one channel and one expression.** A carry is a value one
producer of the tick leaves for another, and every family has them; the object
had grown three ways of saying so. There is now one channel — a `sets` writes
`!name` and any expression reads `{"flag": name}` — and one expression form:

| form | what it is | families |
| --- | --- | --- |
| `carry_out(e, w)` | the carry an add of `w` bits leaves: bit `w` of the sum before the mask | SID Wizard's keyboard-tracking add and its pulse write-out, defMON's oscillator (8) and its slide and cutoff (16) |
| `borrow_out(e, w)` | a subtraction's own carry, the 6502's `C`: 1 where it did **not** borrow | SID Wizard's pulse high half and its vibrato phase, defMON's cutoff on the way down |
| `Acc.flag` | the carry the accumulator's *own* arithmetic leaves, where that arithmetic is a loop and not an expression | Hubbard's `repeat`; defMON's two pulse arms, for the frame the delta does not run |

`borrow_out(e, w)` is `1 − carry_out(e, w)` exactly, and stating it takes the
`+ 2^w` bias out of the object: defMON wrote its cutoff's down arm as
`bit(((acc + $10000) − (step + 1)), 16)`, a tree whose whole content is that
Python's shift on a negative number is arithmetic and the 6502's is not. The bias
is the machine's, so it belongs in the player.

`Acc.flag` stays because one carry is not an expression over any cell the object
has. `repeat(Δ, n)`'s carry is the carry of the **last** of `n` additions, and
`n`, `Δ` and the intermediate values are the arm's and the loop's; recovering it
from the value the loop stored means re-running the loop's last step, spelled out
in the object. #297's rule already says so from the other side — *a carry the
step computes in place is part of its own arithmetic and stays there*. Its two
optional fields are the two places the arithmetic makes no carry, and each is
measured: `seed`, the value at entry, survives only the frame the count is zero
and is worth **11,747 of Commando song 2's 11,780 ticks** and 329 of song 3's;
`unguarded`, the value where the delta did not run, is worth **475 of
*Jazzpjazz*'s 1,799 and 127,722 of *Automatas*' 149,025**, and **0** on all three
Commando subtunes, whose flag's own `globals.flags` default already says it —
so Hubbard writes `seed` and defMON writes `unguarded` and neither writes both
(§7).

`scope` is no enumeration the schema picks per family: it is read off the region
the value cell lives in, which S6 already recovers, and it is per **cell**, not
per Acc — Hubbard's pulse sweep keeps its value in the instrument record
(`ins.pw`, shared by two voices) while its direction and divider are per-voice
(`voice[v].pwdir`, `voice[v].pwdelay`, commando-floor:222-233). Global scope
occurs for the filter in three families (JCH and SW `cutoff_hi`, defMON
`filter.acc`).

`links` carries the cross-Acc effects a family's commands really have. GT2's
tone-portamento snap is the case: `p_1327` sets the note index *and* zeroes the
vibrato phase — `voice[x/7].freq_lo_idx_2 = a; voice[x/7].b14A0 = 0`
(gt2.md:798-801) — and the tick-0 handlers 1/2 zero `vibtime` the same way
(anatomy:876). The second family named here was JCH's re-trigger arm, which
re-points the pulse cursor and reloads the pw accumulator in one step
(jch.md:363-366) — and that is **not** a `links`: it is the instrument's
`on_note`, one inline §3.3 stream whose `point` sits beside its `sets` in one
act, which §3.5 already says an note-on is. The row keeps GoatTracker 2 and the
hermetic clamp snippet ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)
§8, row 5).

And `links` is not a field of an `Acc`. It is §3.6's `Cmd.links` — what a row
command zeroes, which is what GT2's handlers 1 and 2 do — and `meta.pitch_links`
— what taking a pitch of the tuning zeroes, which is what `p_1327` does. GT2
wrote both spellings for one fact, `Cmd.links` on the commands and an `Acc.links`
on `toneporta`, and the second reached neither the player nor the print: the
snap's reset flows through `meta.pitch_links` and always did. The `Acc` row is
struck and the two that are read stand (§7).

Every per-frame modulation in the anatomy's row (anatomy:212) lands on one line,
each with two certified families or a marked single-family exception:

| effect | Acc | evidence |
| --- | --- | --- |
| vibrato (triangle) | **two coupled Accs**: a phase Acc `delta const(+2)`, `bound [0, speedcmp] proved`, `policy reflect-complement`; and a freq Acc whose `phase` is `acc(phase_id)` bit 0 and whose `delta` is a shifted `interval` or a `const` | GT2: `voice[x/7].b14A0 = (a + 2) + c`, `t4 = b14A0 & 1`, then `ghost.freq += ptr` or `-= ptr` (gt2.md:852-862); the bound is the SMC cell `b1096 = T1851[y] & $7F` (gt2.md:812 — speedcmp, **not** the depth) and the complement is `a57 = ~b14A0` (gt2.md:835); `ptr` is either the 16-bit const `(T1851[y] << 8) \| T1863[y]` or `interval(freq_lo_idx_2) >> T1863[y]` through the variable-shift loop `p_12E5` (gt2.md:653-684). JCH: the same two-cell shape on its slide/vibrato (jch:82) |
| vibrato, stateless phase | one freq producer, `delta repeat(interval >> (ins.vib + 1), n)`, `phase fn(global_counter)` | **single-family exception (Hubbard)**: `phase = counter & 7; if phase >= 4: phase ^= 7`, then `for _ in 0..phase-1: f += step` (commando-floor:215-221). It is the closed form of the triangle every other family accumulates, not a new mechanism; admitted because Hubbard is §9's certified non-tracker exemplar and nothing else makes its `freq` exact. T1 reads the `phase` off the counter that decides the count (`accrule.fn_phase`) and verifies the producer against the register, not the cell (#298) |
| tone portamento | target freq, `policy clamp(pitch[target])` with an `edge` (where the step that lands exactly on the target either reaches it or does not — sidwizard-trackerprog §4.8), `delta const`, `links [reset(vibrato phase)]` | GT2 `p_10AB` case 3: the 16-bit compare chain against `FREQ[freq_lo_idx]`, snapping in `p_1327` (gt2.md:798-801). JCH's slide is the same shape with the compare on its own target |
| free slide | target freq, `policy halt` or `wrap` at width, `delta field(cell, mask)`, `phase bit(cell, 0)` | Hubbard: `d = voice[v].porta & $7E; freq += -d if porta & 1 else d` — a free ±step ramp with **no target**, so this row and not the portamento row (commando-floor:236-238). JCH slide acc (jch:82) |
| pulse sweep (bounce) | target pw, `policy reflect`, `bound [$8xx, $Exx] proved`, `rate` a divider, `phase cell != 0` | Hubbard: `pw += d` until `pw_hi == $E`, down until `$8`; `pwdir` the phase, `pwdelay` the divider, `ins.pw` the instrument-scoped value (commando-floor:222-233). JCH `rec6` segments, direction column `& $80` (jch.md:527) |
| pulse run (unbounded) | target pw, `delta const(k) + carry(site)`, `bound` **`projected`** at 12 bits | Hubbard: an **8-bit** add on `pw_lo` with the carry **live from the vibrato block** — `ins.pw_lo += ins.pspeed + C  # C inherited from $51FA` (commando-floor:222-224, `+ carry` at commando.md:394); the 12 bits come from the store's `& $F` (commando.md:380). defMON: `voice[v].pw_lo -= (b101E + (1 - carry_2))` with `carry_2` produced by the freq add above it (automatas.md:427-447), set on **9,144 of *Automatas*' 170,702 sweep steps** and on none of *Jazzpjazz*'s 129, so the row is two-family and it took the whole 149,025-tick horizon to say so — a 20,000-tick prefix reads 0 (defmon-trackerprog §7). These are the writes that make both Commando subtunes aperiodic (architecture §5.2), rendered exactly, aperiodicity included |
| filter sweep (**exercised**, sidwizard-trackerprog §5) | target `split(3, 8)` on cutoff, `delta tabcell(T[c], signed 11)`, `bound observed` | SW: the filter program's step byte is a signed 11-bit delta — `cutoff_lo = ((t3 & 7) + cutoff_lo) & 7` with the carry out, `cutoff_hi += (t3 >> 3) + carry`, the negative arm's shift arithmetic as `~(~t3 >> 3)` (sw.md:868-885, joined in `p_1611`). JCH `rec7` segments and defMON's `filter.acc` write the high half only, the same split with the low half pinned (jch.md:654, automatas.md:420) — and the split is the *chip's*, already `grid.PAIRS[6]`, not a family's |
| keyboard tracking (**exercised**, sidwizard-trackerprog §5) | `tabcell(T[c])` on the cutoff target | SW `CKBDTRK` (§3.7, sw:110-116); defMON's oscillator uses the same form on freq, `voice[v].acc += FREQ[$80 + (pw_hi[v] << 1)]` — the table being the *tuning*, so the object spells it `tuned(2·(osc & $3F) − 36)` rather than a `tabcell` over a stream, and the sign is `bit(cell, 6)`: bit 7 says whether there is a slide at all (defmon-trackerprog §8) |
| arpeggio / chord | target note, a `pitch` stream, or an absolute producer where the phase is stateless | Hubbard octave arp: `f = FREQ[note + ($C if counter & 1 else 0)]` — an **absolute `set` producer** (§4), `phase fn(global_counter)` (commando-floor:249-251). GT2 wavetable note column (gt2.md:564-569); SW chords |
| tremolo, LFOs (**exercised**, walker-trackerprog §5) | four copies of one triangle: `policy reflect`, the turn a **count** and not a bound; a one-shot is `delta_when` and not a policy; a gate tremolo is a stream and not an `Acc` | Walker: `mod1`/`mod2`/`mod3` per voice and the filter's copy on the global channel, `delta` the four bytes of RAM at `$AD73`, `rate` a countdown a note-on reloads. `mod1` and `mod3` sum into one frequency offset — both move it on 1,140 of the horizon's 9,949 modulator steps — so no bound on the cell is either's amplitude, which is what `amplitude.count` is for. The gate tremolo (`mod4`) moves the ctrl gate bit and not a volume: `$D418` is one global register, so `target vol, scope voice` does not exist and is removed; per-pattern volume is `play`'s `vol` column (§3.6) on the one global nibble, last-writer |

**What T1 recognises, and what the exemplars changed (#296).** Every rule below
cites two certified families, as §1 requires; the four rows the classifier could
not read as written are stated as changes to this section, not bent into it.

| rule | how T1 reads it | two families |
| --- | --- | --- |
| a recurrence is not one statement | the guards are the store's transitively closed **control dependences** with the callers' arguments substituted; neither `gated.diamonds` nor a dominator walk sees either | GT2 `p_109E` from four arms of `p_1082`; SID Wizard `p_1611` from the two arms of `p_15D1` |
| a counter is not an accumulator | every step ±1 ⇒ the divider `rate` names, not an `Acc` | GT2 `voice[].timer_3`, JCH `voice[].timer_4` |
| `tablestep` | the delta cell's own recurrence halves a table difference in a loop; `loops.repeats` refuses both spellings, so the count is read off the decrement | GT2 `p_12E5` (`BNE`, an equality exit), Hubbard `$51E4` (a second recurrence), JCH `acc_5` (a `for`) |
| `repeat(Δ, n)` | the step's own block is a counted loop whose bound is a cell | Hubbard `$520B`; the hermetic `for` snippet |
| `split(lo, hi)` | a masked low half plus the high half its carry-out feeds | SID Wizard cutoff `(3, 8)`, Hubbard pw `(8, 4)` |
| `reload` | an action guarded by an equality against a `$FF` sentinel | JCH `rec6[].b1893`, SID Wizard `saved4 < $FE` |
| `clamp` | an action whose value reads a table a guard on the update path compares the target against | GT2 `p_1327` against `FREQ_LO[freq_lo_idx]`; JCH's slide against its own target |
| `links` | the constants the clamp action's own block stores into another `Acc`'s cell | GT2 `p_1327` zeroing `b14A0`; the hermetic clamp snippet |
| `scope` | the copies the value cell's stride makes: 3 is a voice, more is a record a cursor selects, one is the tune's | GT2 `voice[]`/`ghost[]`, Hubbard `rec2[]` |

**What #297 added.** Six more rules, each with its two families; together they
close the T1 gap #296 left — JCH's pulse and cutoff, Hubbard's portamento and
pulse run:

| rule | how T1 reads it | two families |
| --- | --- | --- |
| **reload *then* step, in one tick** | the tick's statement order is the CFG's **reverse postorder** (`graph.rpo`), not `Proc.order`'s preorder, which puts the join a step sits in ahead of the arm that reloads into it. `acchist._sequence` then composes one tick's clauses in that order, so a segment change and the step that follows it are one move and not two readings of one | JCH `p_1409`: `pw = rec6[t2/4].b1893` unless `$FF`, then `pw ± rec6[…].b1894` in the same call, and `$1490`/`$1493` the same shape on `cutoff_hi`. Hubbard `$525B`: `timer_4` reloaded from `b5507 & $1F`, then the pulse steps |
| **a live carry is a *named* flag** | a carry another block of the tick leaves is an SSA flag with no expression of its own, so the record names its site and the flag; a carry the step computes in place is part of its own arithmetic and stays there. The object spells the read `{"flag": name}` and the write `!name`, and there is no `carry` *delta* form: a delta plus a live carry is `add(Δ, flag(name))`, which the grammar already had | Hubbard `rec2[…].b5591 = ((… + b5507) + carry)` at `$5237`, the bit live from the vibrato block `$5208` (`C#41`); defMON `voice[v].pw_lo -= (b101E + (1 - carry_2))` |
| **an external carry refuses** | §8's `external input` for one bit: a flag still a machine register after the callers' arguments are substituted, or one an `io` read makes, is a bit the tick was *given*. Fail-closed — the plane never guesses it | the two halves of one rule in the hermetic set: `PINNED` (`ADC` with no `CLC`, flag `C`) refuses, `JOINCARRY` (a `CMP` in either arm, flag `C#1`) classifies |
| **a loop's exit test is not a guard on its body** | control dependence through a **back edge** says one more iteration follows, not that this block ran. Dropped when the test does not dominate the block and both sit in one loop body; kept for a real `break`, which does dominate what follows it | JCH `p_10E9`'s `for v in 2, 1, 0` (`(X#2 - 1) & $80` was landing in every store's guards), Hubbard `oscillator`'s |
| **the epoch of a cell the tick moved** | `history` samples once a tick, so a condition beside a store read either the value the tick came in with — stepped, where a divider's own step clause ran — or the one it left with; which one depends on where the read sits, so every condition is read under **both**. A divider's step ran on the ticks its own guards hold, and where those have no history the observable decides | GT2 `voice[].timer_3`, where a reload back onto the value it had moves nothing a post-tick compare can see; Hubbard `timer_5`, read *after* its own reload and not before |
| **a copy loop's scratch is not one cell** | a cell a copy loop stores at a constant address — its body and everything it calls — holds each copy's value in turn and keeps only the last. A condition on it says *which copy*, so it is dropped like an index; a value on it is opened to the one expression that fills it, whose own reads are indexed by the copy | Hubbard `b5507`/`cursor_5518`, per voice through `p_519B`; JCH `t2`/`t3`, parked in the cursor their own read indexed |

**What #298 added.** Six rules that make a producer no cell column can carry
readable — the last T1 refusal #297 left, Hubbard's vibrato:

| rule | how T1 reads it | families |
| --- | --- | --- |
| **a scratch producer is verified against its register** | a value cell a copy loop reloads before every use carries nothing across ticks, and one column holds the last copy — so the claim is not the cell's history but the SID register T0 says the value lands in, one series per voice out of W1's `TickObs` over the same replay (`accreg`). The record's clauses are evaluated with the copy index bound per voice, and the ticks another T0 site writes any of the same bits are ticks the value the tick left is not this producer's | **single-family exception (Hubbard)**: `acc_2` (`$550A`, site `$5212`), one column three voices a tick through `oscillator`'s `for v in 2, 1, 0`; the hermetic pair is the second half of the rule — the same loop writing `$D400+7v` classifies, and writing `$D404+7v` (an edge, not a level, so no column) refuses |
| **a register is no one producer's** | the observable's value is what the tick's *last* write left, so a producer's claim holds on the ticks no other site of the same field wrote. `grid.value_index` names the field a register sits in and a pair's halves are two fields of one column, so a site that moves only the other half does not shadow it | Hubbard's freq: the arpeggio `$5386`/`$538C`, the portamento `$52C7`/`$52E2` and the `freq_hi` bounce `$5321`/`$532E` all reach voice `v`'s pair beside `$5227`; SID Wizard's `cutoff_lo`/`cutoff_hi`, one column and two fields |
| **a counted loop's passes are its bound, or one more** | `loops.repeats` reads a loop tested *after* its body, which runs `bound + 1` times. A loop whose exit test *precedes* the body has already dropped the last pass, and what says so is that test standing in the body's own guards, where #297's back-edge rule would have taken it out | Hubbard `$520B` (`(Y - 1) & $80` guards the add: `b550C` passes); the hermetic `inner:` loop (`DEY`/`BPL`, `dep[v] + 1` passes) |
| **a shift loop's count is the value it entered with** | the loop counts its cell down to the floor, so the column holds the floor and not the count; the one store that fills the cell outside the loop is what the shift is | Hubbard `$51E4` counts `timer_3` down to `$FF`, so the shift is `rec2[…].b5596`, the instrument byte that filled it; GoatTracker 2 `p_12E5` shifts by `T1863[y]`, a read its loop does not consume, and keeps it |
| **the counted loop is the innermost one** | a block of a nested loop belongs to every loop around it, and a dictionary of headers fixes no order between them: `accshape.enclosing` takes the smallest body that holds the block, which is both the counted loop and the one order a rerun cannot change | Hubbard's shift loop inside `oscillator`'s voice loop once `p_519B` inlines (the count read `voice[].timer` on some runs and `rec2[…].b5596` on others); the same shape for `$520B` |
| **an index is a copy selector whether a register or a cell carries it** | #297 read a store at a *constant base* as a copy loop's scratch, which a record a cursor picks is not: it keeps its own column. Only a store with no index at all writes the one cell every copy shares | Hubbard `rec2[…].b5591` and the `sid.reg[b54EB]` writes left the scratch set (29 cells to 21); SID Wizard's `ptr_4` (`$0001`/`$0002`, written at `ptr + 1`) left it too |

One row this section stated differently, corrected here rather than in the
classifier: **each caller's arm of a pair's half is its own clause.** `_merge`
keyed a pair's byte stores by their `(procedure, block)`, so a half written from
three call sites kept one arm and dropped two — the guards of whichever the view
enumerated last. The arm is its guard path, not its block, and #298 keys it that
way; Hubbard's `acc_2` reload is the family (three calls of `p_519B` from
`oscillator`), and it is why the record's `reload` holds on 564 of 1,200 ticks
rather than none.

Four rows this section stated differently, corrected here rather than in the
classifier:

1. **The vibrato phase cell is not bounded by `speedcmp`.** The row above reads
   `bound [0, speedcmp] proved`; `b14A0 < b1096` selects an arm, and the
   complement arm puts the cell in the *upper* half of the byte, so over 12,000
   certified ticks the cell leaves `[0, speedcmp]`. `[0, speedcmp]` is the
   triangle's amplitude, not the cell's range. T1 offers the guard's interval
   first and takes the first one the horizon keeps, which here is `observed` at
   the byte's width — the record says which, and never widens silently.
   Corrected here in 2026 and **still written the wrong way in the object** until
   §7's second package turned the assertion on: it leaves `[0, speedcmp]` on
   1,532 of *Je suis Linus*' 10,956 moves and 1,114 of *Do It Again*' 10,073,
   from tick 2 and tick 20. `amplitude` now carries the compare and `bound` says
   `observed [0, $FF]`, which is the correction as data rather than as prose.
2. **`split(k, 8)` is `split(lo, hi)`.** One rule, two families: SID Wizard's
   cutoff is `(3, 8)` and Hubbard's pw `(8, 4)`. The `8` was the cutoff case.
3. **`tabcell(T[c], signed = k)`'s `k` is the width the byte is signed *into*.**
   SID Wizard's filter step is an 8-bit table byte extended into the 11-bit
   split, which is what "signed 11" says and what T1 records.
4. **A guard on a masked projection of the target gives `projected`, not
   `proved`.** Hubbard's bounce tests `(pw_hi ± borrow) & $F`, which bounds the
   projection the chip sees, not the cell; SID Wizard's `cutoff_lo & 7` is the
   same shape. `proved` is for a guard on the value itself.
5. **A free slide's direction cell is `wrap`, not `reflect` (#297).** `reflect`
   is a bounce: the play *turns* the direction cell, either by stepping it
   (Hubbard's `FREQ[$E8 + x] ± 1` at its `$8`/`$E` ends, GT2's phase byte) or by
   setting it under a test of the accumulator's own value (GT2's portamento
   compare chain against `FREQ_LO[freq_lo_idx]`). A direction cell the **score**
   sets from a stream byte picks a direction and never turns, which is this
   section's own free-slide row — Hubbard's `voice[v].b5520` (`porta & 1`) and
   GT2's `b10AC` (the command byte, 1 up and 2 down) are its two families, and
   both are `wrap` at width. #296 read them as `reflect` because any store to the
   phase cell counted as a turn.

Two first-draft rows are struck. **Skydive** is dead in the only family that has
it — `if ins.fx & 2 and (row & $1F) >= 3: trap 'untaken'` (commando-floor:247) —
so there is no observation to fit. **Piecewise envelopes** are not a row: they
are streams of `acc` segments (§3.3), the stream sequencing and the accumulator
moving. Nothing else moves a shadow between rows — that is the discipline.

---

## 6. The lift, T0–T3

The lift emits a **scoreprog** (§1), not a trackerprog: the certified tick with
its fetch regions cut out and its score in their place as data. Its renderer is
`trackerprog/interp.py`, an S4 interpreter, and the sound half is still the tick
outside the regions, carried in `program` and run as code. Converging it onto
§4's object is [trackerprog-backlog.md](trackerprog-backlog.md) B6 and B7.

Input: a certified tuneprog — `tuneprog.S4.json` (the program),
`tuneprog.S6.json` (the naming plane: roles `freq_table`, `cursor`, `timer`,
`acc`, `sid_image`, `voice_map`; views; u16 pairs; the `index` relation),
`tuneprog.T0.json` (per-write-site provenance) and `certificate.json`. The lift
consumes the *certified* object, never the trace or the binary: family knowledge
may steer extraction but can never reach the output, which renders on §4 alone.

| stage | in | out | mechanism |
| --- | --- | --- | --- |
| **T0 channels** | S4 IR + names | per-register provenance | **landed** (§7): `provenance.document` writes `tuneprog.T0.json`, one record per SID write site — register, voices, the expression over named cells, its leaves, the site, the printed line |
| **T1 accumulators** | T0 + `history.py` | `Acc` set | a `state` cell whose update matches a §5 `delta` and whose guards, masks or history give a `bound` with its `from` tag. Not `ranges.py` and not `gated.py`: `ranges.expr_range` bails to width on any self-referential `+`/`-` (`ranges.py:44-49`) and `gated.diamonds` needs one same-name `Let` per arm (`gated.py:34-37`), which no reflect site in GT2/Commando/JCH/SW has. `facts.update_role` (`facts.py:288`) is the seed; the rest is new |
| **T2 grammars** | the S6 `index` relation + histories | streams, patterns, orderlists, pitch table | a `cursor`'s observed successor relation (step +1 runs, jump targets, the `$FF`-terminator reloads) delimits its table's rows and loop row; the two-level cursor nest (row cursor over a pattern table indexed through an orderlist cursor) is the score; `freq_table` regions are `pitch` |
| **T3 emit + certify** | all | `scoreprog.json`, `scoreprog.md`, `scoreprog.certificate.json` (`tools/tuneprog_scoreprog.py`) | render on `interp.py` tick-for-tick against `Verifier.obs` over the whole certified horizon, §2 observable; any residue → `Refusal`, nothing emitted |

T2's materialisation rule: the trackerprog represents the score the trace
played. Storage idioms — Blackbird's LZ stream and ring buffers, packed rests,
1-based columns, interleaving, Follin's `$85` byte lists — are dropped by
materialising the decoded rows over the horizon, which is `period` for a
`complete` source with `period > 1`, `first_repeat` for a `period = 1` source
(the tune ended; §2), and the certified tick count for a `horizon` source.

The note space is `0..N-1` where `N` is the trace's reach; there is no
`clamp(note)` rule. A read past a **const** table's declared size extends
`pitch` with the values read; a read landing on a **play-written** cell is not a
pitch entry at all. Commando song 1 plays pitch 104 twenty-five times, `$5428 +
2*104` is `voice[0].ctrl` / `voice[1].ctrl`, and the arpeggio's `+12` reaches
the two `pwdir` bytes (commando-floor:295-310), so that starting frequency lifts
as an absolute `set` producer over `field(cell)` (§4, §5) — named, not
materialised as pitch. The first draft's "reads two bytes past the table" was
wrong on both the distance and the kind.

---

## 7. What landed, and what remains

Nothing in the front end changed: the IR, the tracer and S8 are untouched and
the trackerprog consumes certified artefacts. The five enabling packages and T1
itself have merged.

| item | what landed | modules / artefacts |
| --- | --- | --- |
| the grid as a first-class comparison (**#291**) | `grid.regs`, `grid.changes`, `grid.reduce_tick` → `TickObs(edges, values)`, `grid.reduce_run`; constants `CTRL/AD/SR/EDGE/PAIRS/LEVEL/PW_HI`; `ghidra_facts._tick_writes` is now a filter plus `grid.changes`; `Verifier(obs=True)` accumulates one `TickObs` per verified tick (`verify.py:165,286,350`). `verify._compare` stays raw — mirror folding, the PW nibble and the cutoff mask do not reach it | `grid`, `verify`, `ghidra_facts` |
| cell histories without touching S1 (**#292**) | `history.cells`, `history.history`, `history.widen_u16`, `History`, over the verifier's own ticks (`Verifier._one` promoted to `tick()`), `np.frombuffer(M.m)` at a fixed index, sparse strides off `Region.addrs`; `tools/tuneprog_history.py` writes `tuneprog.history.npz`. A library and a tool, not a pipeline artefact | `history`, `verify` |
| the S6 exports T2 needs (**#293**) | `facts.idxbase`/`cellsrc`/`leaf_reads` and one cell key put record fields into `cellindex`; `facts.cursor_cells` is one cursor rule for scalars, fold slots and split fields alike; `recover.index_relation` serialises the relation as `tuneprog.S6.json`'s **`index`** block; `Names.from_dict` reads the whole document back. Score cursors now carry the role — GT2 `rec[x/7].cursor`/`.cursor_2`/`.cursor_3`, JCH `voice_3[v].cursor`, SW `rec` `+0`…`+6` | `facts`, `recover`, `views` |
| per-register provenance, T0 (**#294**) | `provenance.py` writes **`tuneprog.T0.json`** beside S6: `{plane, voice_map, image, writes}`, one record per SID write site. Roots are `io` stores whose envelope lies in `$D400..$D418` plus stores into a `sid_image` region rekeyed by the flush delta; `provenance.regvoices` reads the register off the site's base and the voices off its envelope; `expr` substitutes names stopping at every cell S6 names, serialised with `ir.enc` (`R16`/`W16` added to `_NODES`, `W16` gaining `env`); each record's `print` is the `tuneprog.md` line itself | `provenance`, `ir`, `pipeline` |
| T1, the accumulator plane (**#296**) | `accum.document` writes **`tuneprog.T1.json`** — `{plane, horizon, accs, refusals}` — from a library and `tools/tuneprog_accum.py`, no pipeline artefact moved. `accshape` reads a store's guards as its transitively closed **control dependences** (not its dominators, which a join carries either way) and joins the callers' arguments where a value's free names are its procedure's parameters; `accdelta` is §5's grammar; `accrule` the counter, bound, policy, rate, phase and scope rules; `acchist` evaluates a named-cell expression over `history.py` and runs both verifiers | `accum`, `accshape`, `accdelta`, `accrule`, `acchist` |
| T1 against the register, not the cell (**#298**) | `accreg.py`: a producer whose value cell is copy-loop **scratch** is replayed against the SID register T0 names, one series per voice out of `Verifier(obs=True)`'s `TickObs` (`grid.value_index` gives the field a register sits in), with the other T0 sites of the same field as the ticks the value is another producer's; `history.History` resolves a byte by its address, since the presentation view's region ids are not the sampled program's; `accdelta.tablestep_exprs`/`unscratch` put the table difference in place of a cell one column cannot carry; `accshape.enclosing`/`onepass` make the counted loop the innermost one and its passes the bound or one more; `accguard._consts` drops an indexed store from the scratch set; `accrule` gains `reload` for a scratch cell, `fn_phase`, and one clause per caller's arm of a pair's half | `accreg`, `acchist`, `accdelta`, `accguard`, `accrule`, `accshape`, `accum`, `grid`, `history` |
| T1's tick order and its epochs (**#297**) | `graph.rpo` makes the tick's statement order the CFG's reverse postorder, so a reload and the step that follows it compose in one tick; `accguard` splits out of `accshape` at the 500-line rule and holds `key_of`, the control dependences (now with a loop's own back edges taken out), `opened`, the copy-loop `scratch` set and the `propagate` that opens it to the one expression that fills it; `acchist.truth` reads every condition under **both** epochs of a cell the tick moved and `counter_epoch` steps a divider by its own step clauses' guards; `accum` emits `delta.carry` for a named flag and refuses one the tick was given | `accguard`, `accshape`, `acchist`, `accrule`, `accum`, `graph` |

Measured over the 51 recert programs: **849 write sites, 849 prints re-rendering
to their own line, 0 sites both unnamed and unrefused**; the 40 refusals are 36
`index not a voice` (Follin's `$85` cross-voice list, JCH's non-constant clear)
and 4 `smc target`. Replay cost from #292, ticks / cells / seconds:
`gt2-je-suis-linus` 12,000 / 120 / 5.4, `jch-guldkorn-intro` 4,000 / 146 / 1.6,
`sw-emomyst` 12,000 / 129 / 8.0, `commando-song1` 11,780 / 206 / 3.4.

Measured over the four exemplars at their certified horizons — GoatTracker 2
12,000 ticks, JCH 4,000, SID Wizard 12,000, Hubbard 11,780 — **16 accumulators,
0 replay divergences, 0 interval escapes, 9 stated refusals** after #298 (15 and
10 after #297, 10 and 15 before it), 31 s of CPU:

| tune | accs (policy / `bound.from`) | refusals |
| --- | --- | --- |
| `gt2-je-suis-linus` | vibrato phase `reflect-complement`/`observed`, vibrato freq `reflect`/`observed` and the free slide `wrap`/`observed` (both `tablestep`, `phase acc(id)` on the first), filter `wrap`/`observed` (`tabcell`) | 3 `delta` (the wavetable gate cell), 1 `replay` (`ghost.pw_lo`) |
| `jch-guldkorn-intro` | slide and vibrato freq `wrap`/`projected` (`field`, scope voice); pulse ×2 `reload`/`projected` and cutoff `reload`/`observed`, all three `tabcell` off a `cursor` with a countdown `rate` | none |
| `sw-emomyst` | filter `split(3, 8)` ×2 `wrap`/`observed` (`tabcell` signed 11), `sid.reg` `clamp`/`projected` and `wrap`/`observed` | 4 `delta`, 1 `replay` |
| `commando-song1` | the vibrato `reload`/`observed` (`repeat(tablestep(FREQ, voice[].freq_idx, timer_3), b550C)`, `phase fn(timer_6)`, scope voice, replayed against each voice's `freq` pair); portamento `wrap`/`projected` (`field(b5520 & $7E)`, `phase bit(b5520, 0)`, scope voice) and the pulse run `wrap`/`projected` (`tabcell(rec2[…].b5597) + carry($5240, C#41)`, scope instrument) | none |

**What T1 does not claim.** An `Acc` states what a producer does, not when the
tick runs it — that is §4's `commit` order — so the replay accepts a tick the
value did not move on and refuses every move the plane's own clauses cannot
make. Where the value a producer *sets* is a table read no name indexes, the
record states when the producer ran and leaves the value to §4's absolute `set`.
A producer checked against a register claims less again: the register is no one
producer's, so the record holds on the ticks no other T0 site of the same field
wrote, and where a competing site's own guard reads a cell the horizon has no
column for the record leaves that tick to it and says how often (`verify.alien`).
Hubbard's horizon now carries no refusal; GoatTracker 2's `ghost.pw_lo` and SID
Wizard's `b1024` still refuse, and their cells are not scratch.

| T2, the score as a cursor nest (**#299**) | `trackerprog/` beside `tuneprog/`: `resolve` opens a table read's address to one expression over named cells and copy indices (reaching definitions with guarded alternatives, scratch pointer stores, joins, callers' arguments), `cursors` decomposes it into base, origin, cursor and shift, `score` nests pointer bases to depth 2, names the order channels a pattern's selector reads, takes terminator bytes off the cursor's own reset stores and materialises every voice's fetch events over the horizon, `streams` tells a self-stepped cursor from a selector, `pitch` materialises the values read; `tools/tuneprog_score.py` writes **`tuneprog.T2.json`**. GT2 (33 pointers, 9 × 30 instruments, `T16F9`), JCH (26 pointers, `rec8[19]`, three `$FF`s), Commando (`T576B`, `T5889`, `rec2`) lift with no refusal; SID Wizard refuses by name at `p_17C8` | `trackerprog.resolve`, `hist`, `cursors`, `score`, `streams`, `pitch`, `refuse`, `lift` |

| the lift from data (**t3-from-data**) | `region.fetch` cuts the certified S4 tick's fetch regions out (score-byte taint, minimal single-entry regions with side doors, tainted-latch loops); `player.Player` is one interpreter over the program from the post-init image, recording each region entry as a fetch (stores, bytes, temps, resume block) and replaying fetches with the regions skipped; `emit.lift` builds the object — instruments as the table the `ad`/`sr` sites index (a selector or a pointer table), streams as T2's cursor tables with their bytes, `accs` as T1's, producers as T0's sites outside the regions under their guards, rows/patterns/order from the fetches — and never reads the observable. JCH ×2, GT2 ×2, Commando ×2, SW ×2 certify from data; six of eight prints below the source's `xz` | `trackerprog.region`, `player`, `emit`, `certify` |
| ~~lanes, deltas, `tablestep`, cycles, the period with a loop (**#304**)~~ — superseded, it encoded the observable | `emit.lift` encodes levels as deltas and vibrato as `freq_ts(m, shift)`, folds runs and short cycles into one step, splits a row's sound into lanes (edge registers apart under one `commit_order`), shares a stream between a note and a shorter one, keys patterns on note offsets with the transpose in the order, and materialises a complete source over its period with the loop's `enter` levels; `player.py` (now `interp.py`) steps cycles and lanes in commit order. Four of eight prints below the source's `xz` | `trackerprog.emit`, `interp` |
| ~~rows as streams, six tunes certified (**#302**)~~ — superseded, it encoded the observable | `emit.lift` reads T2's rows and the observable: per row a stream of steps with holds (the voice's ordered edges, `note_off`/`freq`/`pw` sets), deduplicated, the row's instrument the stream it arms; the global channel one stream; a second schedule entry refuses as `sample stream`. JCH ×2, GT2 ×2, Commando ×2 render at 0 divergences | `trackerprog.emit`, `player` |
| the scoreprog interpreter, the certificate and the print (**#300**) | `trackerprog/interp.py` (then `player.py`) runs the certified tick with the fetch regions replaced by data — it is an S4 interpreter and **not** §4, which is `universal.py`; `certify.py` is §2's comparison over the whole horizon with `compared`, `dropped`, `refusals`, `emitted`, the loop claim re-checked on the render; `emit.py` lifts T0's sites that are a constant or a pitch lookup at the row boundary or every tick and refuses the rest as `command residue`, prints `scoreprog.md` and measures §6.2's six plus `xz -9e`; `tools/tuneprog_scoreprog.py`. The hermetic tune renders at 0 divergences; GT2, JCH and Commando carry named residue (backlog §4, W8) | `trackerprog.interp`, `certify`, `emit` |

| one grammar, audited across three families (**#310**) | the three hand exemplars read together against §3: `meta.commit` struck (the tick is always a sequence of acts, and rendering it so for the families that do not need it is write-for-write identical over their whole horizons); `meta.row` replaces `note_row`, `gate_row`, `pitch_row`, `row_sets`, `row_commits` and merges `latch`/`row` into one `apply_row`; `Ins.on_note` replaces `sets`/`note_sets`/`points`; `meta.tick` replaces `tempo.early_first`, `meta.voice_exit` and the commit's `pre` list; `interval(n)` replaces `tablestep`; one cell vocabulary for `Acc.cell`, retiring `voice.freq*` and the `@`-means-two-things collision. a command's writes become an inline stream, so a guard has one spelling and never a positional slot; §3.3's terminator, §3.6's nine-command opcode list, `for`/`call`/`ret` and §3.5's stream-slot map are struck as grammar no exemplar carries. Measured: the union of `meta` keys across the three families 26 → 21, the keys the player *branches* on 15 → 10, two row procedures → one, three mechanisms for "run a stream at a point in the tick" → one, two guard spellings → one; `universal.py` 995 → 1,009 lines, which is the price of the generality and is paid once rather than per family. 62 HVSC oracle tests unchanged: Hubbard ×3, GoatTracker 2 ×2 and SID Wizard ×2 at 0 divergences over their whole horizons | `universal`, `printer`, the three `tools/trackerprog_*.py` |

| defMON, the fourth family (**#311**) | the two certified defMON tuneprogs transliterated onto the same player: *Automatas* over its whole 149,025-tick horizon and *Jazzpjazz* over its 1,799, 0 divergences and write lists **identical** on every tick, the loop claim re-verified on the render. Six forms in the player — `meta.shadow.registers` is the ordered list of registers the image carries (GoatTracker 2's value is `range(24, -1, -1)`, write for write identical), a `globals.commit` to a register outside that list reaches the chip on its own tick, `{"cell": …}` and a `sets` target now read and write §5's own cell vocabulary (`shadow.<pair>` included), `xor` beside `and`/`or`, `row_consumes_tick: false` is *never* rather than always, and a gate reports the decision the step made rather than re-reading the cell it moved. Four in the data only: the arranger's end is global so the score materialises per step, a stream that acts and *then* holds is two rows, a tuning read below itself and past itself is a signed `base`, and §10's multispeed is `rate = 8` — measured. One expectation fell: the sidTAB row is a stream row and not an instrument, so both sidcalls are `point` commands and the family's one `Ins` carries neither `adsr` nor `prelude`. `carry(site, flag)` is two-family after all, but only over the whole horizon — a 20,000-tick prefix reads 0 where 149,025 reads 44,675 | `universal`, `printer`, `tools/trackerprog_defmon.py` |

| JCH V20, the fifth family (**#312**) | the two certified JCH tuneprogs transliterated onto the same player: *Guldkornekspressen Intro* over its whole 2,401-tick horizon with the loop claim re-verified, and *I Could Eat a Knob at Night* over its whole 8,577 with the write lists **identical** on every tick, 0 divergences on both. **`end.kind = fixed_point` is taken for the first time**: period 1, `loop` null, the score materialised to `first_repeat` and the render's last tick writing nothing. Five forms in the player, every one a marked single-family data form — a flush entry may state the guard the image writes it under (one build flushes the same 25 registers in either direction, and which one is a byte of the frame: fixing either diverges on 4,689 and 3,887 of 8,577), `meta.prefetch` gains `note`, `transpose` and `cmds` (the row's pitch staged with the row, worth 8 and 397 ticks; the order's transpose too, because the vibrato reads the untransposed note, worth 240; the row's commands spent at the fetch rather than the boundary, worth 38), and `reg.N` is a register of the one global channel written by the voice whose write-out sends it. Five in the data only, including the two column programs as act-and-hold rows with the step ranked after them (reversing it diverges on 1,821 of 2,401) and the wrapper as a stream, a countdown and seven overrides. **Two expectations measured to zero and were struck**: the build byte's own effects skip (0 of 8,577 on the only build that sets it) and a staged instrument (0 on both). The first family whose two builds disagree about having a shadow, and the second measurement that a shadow hides `commit_order` — and, new, voice order with it | `universal`, `printer`, `tools/trackerprog_jch.py` |

| the object's dead surface, pruned (**P1**) | every field the five tools emit, read back against every consumer — the player, the print and the round-trip tests — and the eight nothing read struck: §3.3's `term` (#310 took it out of the grammar and left it in four tools and the print), a stream's `kind` and `scope`, an `Acc`'s `id` (the key `accs` declares it under is its name, and the player now reads that), an `Acc`'s `links` on GoatTracker 2's `toneporta` (a second spelling of `meta.pitch_links`, which is what the snap actually resets), Commando's `overflow` and `armed_by` (the arm the print announced is the `arms` of the event the print already renders), defMON's `meta.order_steps`, and the label on SID Wizard's `prologue`. Measured, the way §6.4's first check asks: rendering the pruned objects is **write-for-write identical on every tick of all eleven builds' whole horizons** — 0 differing of 236,586, Commando ×3 11,780, GoatTracker 2 8,236 and 8,659, SID Wizard 8,084 and 14,465, defMON 1,799 and 149,025, JCH 2,401 and 8,577 — and all eleven re-certify at 0 divergences against their tunes' own players. The print loses 1 line and 8–28 tokens per build where a stream carried a terminator; `universal.py` 1,063 → 1,066, `printer.py` 628 → 622, the five tools 5,666 → 5,614. The generalisable check, which is §6.4's applied to the *object* rather than to the player: **a field the object writes and no consumer reads is not a field**, and grep for the readers of every name the schema declares — a row struck from §3 stays in the tools until someone looks | `universal`, `printer`, the five `tools/trackerprog_*.py` |

| the object compiled once, not walked every tick (**P8**) | the player dispatched on the *form* of every expression node at every reading — `next(iter(e.items()))` and a thirty-way `if` chain, 7 million times over *End of the World*'s 14,465 ticks, which is 485 evaluations a tick — and `guards` built a five-entry dict of comparisons to index one of them, 1.9 million times. The object is fixed for a render, so it is now **compiled on first reading and called thereafter**: one closure per expression node, one predicate per guard list, one setter per `sets` target, and a plan per accumulator, per stream row, per inline stream, per stream column, per flush entry and per row clock. `machine`'s stream ranks are sorted once in `__init__` rather than per voice-tick, `publish`'s subscriptions are indexed by `(event, voice, acc)` rather than scanned, `slot` resolves each stream's cursor from one map, and `meta.tick`'s phase names resolve to bound methods instead of a string-compare chain. Nothing in §3, §4 or §5 changed: this is the same procedure over the same object, and the acceptance is that **the write lists are identical tick for tick** against the pre-change player on all eleven builds' whole horizons — 0 differing of 236,586 — with all eleven re-certifying at 0 divergences. Measured over those horizons, render goes **3,857 → 8,239 ticks/s, 2.14×** overall: Hubbard ×3 1.37–1.54×, GoatTracker 2 1.66× and 1.69×, SID Wizard 2.15× and 2.11×, defMON 2.39× and 2.24×, JCH 1.91× and 2.23×. The families that gain most are the ones that evaluate most — SID Wizard's guarded phases, defMON's cascades, JCH's column programs. It is **not** the 5× the package aimed at, and the profile says why: after the compile the cost is flat across `machine`, `step`, `rows` and the closures themselves, each 5–10 %, and no further factor is available without generating Python source per object and `exec`ing it — which would trade the one thing this layer exists to have, a fixed procedure a reader can hold against §4, for a replay speed only the poison sweeps want. `universal.py` 1,095 → 1,378, all of it the compiler, and a stored player drops it and reads it again (`__getstate__`), because none of it is a fact | `universal` |

| one row clock, not three (**P7**) | `meta.tempo.form ∈ {divider, countdown, counter}` selected three procedures in `clock()`, a fourth in `early_due()`, a fifth in `fetch_due()` and a sixth in `sequencer_step()`, plus a `reload()` of its own that one family's funk tempo branched inside again. **The counter is the general one** and each family's clock is a value of it: a `cell`, a signed `step`, a `boundary` guard, guarded `reset` clauses (first match wins), and the `rate`/`phase` that say which ticks it steps on at all. Hubbard's and defMON's divider is the rate with a step of −1 and no reset — the row's length is the sequencer's to reload; GoatTracker 2's and JCH's countdown is a step of −1, a boundary at zero and a reset that reloads past it; SID Wizard's counter is a step of +1 with two clauses that zero it and move its tempo program on. `tempo.alternate` — the funk tempo, a record with a stream and a guard — becomes **one more reset clause ahead of the plain one**, which is what it always was; `tempo.reload` and `tempo.form` go with it, `boundary`/`fetch`/`early` become guard lists like every other guard in the schema, and `sequencer_step` stops asking what shape the clock is and asks `meta.tick` whether the tune has a `fetch` phase, which is the datum that was actually meant. Measured: **0 differing ticks of 236,586** across all eleven builds, and all eleven re-certify. The clauses are load-bearing and the poisons say so — the clock with no reset at all diverges on 8,230 of *Je suis Linus*' 8,236, 8,653 of *Do It Again*'s 8,659, 8,077 of *Emomyst*'s 8,084, 14,451 of *End of the World*'s 14,465 and 2,395 of *Guldkorn*'s 2,401; striking only the funk clause and leaving the plain reload diverges on 8,639 of *Do It Again*'s 8,659 and 0 of *Je suis Linus*'. The keys the player *branches* on go **15 → 11** (`tempo.form`, `tempo.alternate`, `tempo.reload` and the fetch's own dispatch), the clock's three procedures → one, and `universal.py` 1,095 → 1,085 with `printer.py` 630 → 618 | `universal`, `printer`, all five `tools/trackerprog_*.py` |

| one spelling for the 6502 carry (**P6**) | a carry is a value one producer of the tick leaves another, and the object had grown three ways of saying so: §5's `carry(site, flag)`, which the object never spelled that way at all (a delta plus a live carry is `add(Δ, flag(name))`, which the grammar already had); a `{"bit": [e, 8]}` or `{"bit": [e, 16]}` written into `!C` by a `sets`, in two families and under a local helper in one of them; and defMON's `bit(((acc + $10000) − (step + 1)), 16)` for the subtraction, a tree whose whole content is that Python's shift on a negative number is arithmetic and the 6502's is not. One channel — a `sets` writes `!name`, an expression reads `{"flag": name}` — and one expression form: **`carry_out(e, w)`**, bit `w` of a sum before its mask, and **`borrow_out(e, w)`**, which is `1 − carry_out(e, w)` exactly and is the 6502's own `C`. Two certified families each; the bias goes into the player where the machine's own arithmetic belongs; `{"bit": …}` keeps only the genuine bit tests (a sign, a phase, a flag byte's own bit), which is what it was for. `Acc.flag` stays, because one carry is not an expression over any cell the object has: `repeat(Δ, n)`'s carry is the carry of the *last* of `n` additions, and recovering it from the value the loop stored means re-running the loop's last step in the object. Its two optional fields are measured rather than assumed — `seed` is worth **11,747 of Commando song 2's 11,780 ticks** and 329 of song 3's, and `unguarded` **475 of *Jazzpjazz*'s 1,799 and 127,722 of *Automatas*' 149,025** but **0** on all three Commando subtunes, whose flag's own default already says it, so Hubbard's record drops it and each family now writes only the field it needs. Measured: **0 differing ticks of 236,586** across all eleven builds, and all eleven re-certify | `universal`, `printer`, `tools/trackerprog_{commando,sidwizard,defmon}.py` |

| JCH's wave table decoded, the last unspent token class (**P5**) | §3.6's rule — *a value that is not in the pitch table is not a pitch* — is what the layer spends the note column's byte ranges for, and JCH's wave table had them unspent: the object carried the two raw byte columns and a five-row reader whose guards were the assembly's own `CMP` immediates (`== $7F` a jump, `>= $80` an absolute note, `< $80` a relative one), re-deriving each row's kind every tick from a byte that is a constant of the table. GoatTracker 2, SID Wizard and defMON all decode their wave tables at build time; this one is now decoded too, into four columns whose names are §3.3's and GoatTracker 2's — `next` the link (the jump's target on a jump row and the row itself on every other, so the reader follows it unconditionally and a note row's follow is the identity), `pitch` and `relative` the note column, `ctrl` the waveform byte. The reader is four rows and tests no byte range. The `$7E` token — step the cursor back one — appears in **neither build's table at all**, so its guarded trap row is gone and §3.3's own row `trap` carries it, refusing at the read. Guldkorn's 64 rows decode to 15 jumps, 19 absolute notes and 30 relative; Knob at Night's 19 to 5, 3 and 11. Measured: **0 differing ticks** of 2,401 and 8,577, both builds re-certify write-for-write, and the round trip is the check that the decode is lossless — the test rebuilds each row's own byte out of its columns and diffs the pair against the tune. What does *not* transfer is the mechanism: GoatTracker 2's `op: pitch(offset \| absolute)` takes a pitch of the tuning, and V20's wave row moves an **index** a separate stream turns into a frequency two ranks later, so the vocabulary aligns and the step does not | `tools/trackerprog_jch.py` |

| the prefetch enum becomes the row program (**P4**) | `meta.prefetch` was a seven-value string enum — `ins`, `hrins`, `gate`, `note`, `transpose`, `arm`, `cmds` — accreted one value per family across #309, #310 and #312, which is the `note_row`/`gate_row` failure §6.4 documents one level up: a name per call site rather than the general form the schema already had. It is now **`meta.stage`, a §3.6 row program**, run at the `fetch` phase by the same `row_step` that runs `meta.row`, over a payload that is §3.6's row facts plus the three values a staging copies rather than tests (`ins`, `note`, `transpose`). Five of the seven become ordinary rows: `ins` is `{"ins"}`, which `meta.row` already had; `hrins` is `{"sets": [["@hrins", {"payload": "ins"}]]}`, the target cell now the tune's to name rather than a second enum value; `gate` is `{"sets": [["@gate", {"payload": "gate"}]], "when": [["gate_stmt", "!=", 0]]}`, which closes P3's loose end by making the mask row data; `note` and `transpose` are the same shape. Two survive as steps, because neither moves a cell: `{"commands"}`, which `meta.row` already had, and `{"hold"}`, the command the score gives a voice to keep. A row step's `sets` now reads the row's own facts, as its `stream` and its `when` already did — one payload, everywhere the row program runs. Measured: **0 differing ticks of 236,586** across all eleven builds' whole horizons, and all eleven re-certify. `universal.py` 1,093 → 1,091 and `printer.py` 633 → 630, the enum gone from both — the shape is the saving, not the line count: one procedure and one payload where there were two of each | `universal`, `printer`, `tools/trackerprog_{goattracker,sidwizard,jch}.py` |

| the constants that were not the chip's (**P3**) | three family-shaped values in the player, each moved to where its own fact lives. `pwdir` was a *voice cell the player declares*, beside `ins`, `wave`, `orderpos`, `rowsleft`, `dur`, `freq`, `note` and `lastnote` — and it is Hubbard's pulse direction, not the player's vocabulary, so it is seeded through `state0.cells` like every other cell a tune has. The funk tempo's `- 1` was a subtraction the player did to one family's reload and to no other value in the schema; it is one clock step, the row's *countdown* against the boundary at 0 rather than its length, so the alternate stream's rows say it and `reload()` returns what the row gives exactly as it returns what the tempo cell gives. Load-bearing, measured: dropping it without folding it in diverges on **8,625 of *Do It Again*'s 8,659** ticks and on 0 of *Je suis Linus*', where the funk tempo is dead. And the gate masks `$FF`/`$FE`, written out at two sites, are one question — §6.1's "one musical question, one place that answers it" — so the fetch's staging asks `gate_mask()`, which is the place, and the pair is a named chip constant (§3.6) rather than a `meta` row: no family varies it, and by §6.4's own check a datum no observation distinguishes is not a datum. Measured: 0 differing ticks of 236,586 across all eleven builds, and all eleven re-certify | `universal`, `tools/trackerprog_{commando,goattracker}.py` |

| §5's bound asserted, and five records that did not survive it (**P2**) | `Player.store` holds every accumulator move to the interval its record declares — §5 has said the renderer does this since the first draft and it did not, `bound.interval` being read only as `reflect`'s turn and `reflect-complement`'s fold and `from`/`witness` read nowhere. Turning it on took **five of the sixteen records** out, none of them a bug in the render and every one a claim the object was making falsely: Hubbard's vibrato said `proved [0, 3]` where `[0, 3]` is the *fold*, the repeat's count, and the cell holds a frequency (8,836 / 22,488 / 1,089 escaping moves on the three subtunes, from **tick 1**); its arpeggio said `proved [0, 12]` where `[0, 12]` is the arp stream's transpose (16,341 / 13,803 / 1,089, from tick 1); its drum said `proved [1, $FF]` from the guard `freq_hi != 0`, which bounds the value the step comes *in* with and not the one it leaves after `−1` (32 and 5, from tick 173); its pulse sweep said `projected [$800, $EFF]`, which is where the bounce turns and not where the cell goes, since a step of `$E0` from `$E60` reaches `$F40` and then wraps to `$020` (10 moves, from tick 3,457); and GoatTracker 2's vibrato phase said `proved [0, speedcmp]`, which §5 correction 1 declared wrong in prose in 2026 and which the object went on saying (1,532 of 10,956 and 1,114 of 10,073, from ticks 2 and 20). The turn and the fold move to **`amplitude`**, which is the step's own arithmetic and may read a live cell; `bound.interval` is two constants, which is what §5's *statically known* means. Measured: rendering the corrected objects is **write-for-write identical on every tick of all eleven builds' whole horizons** — 0 differing of 236,586 — and all eleven re-certify at 0 divergences; the assertion costs under 4 % of render. The generalisable check: **an invariant the renderer does not assert is prose**, and the interval a *step* reads is not the interval a *record* claims — one key cannot be both | `universal`, `printer`, `tools/trackerprog_{commando,goattracker}.py` |

| Blackbird, the seventh family (**#322**) | lft's *Quintessence* transliterated onto the same player over its whole 10,426-tick horizon, 0 divergences, `end.kind = horizon` — and **the first family that cost the player nothing**: `universal.py` and `printer.py` are byte for byte as #321 left them, every form this family needs being one the six before it earned. Two things outside the player had to move. The tuneprog front end could not certify the tune at all: Blackbird's `X = voice×7` indexes the state arrays *and* `$D400,X`, so the region carrying `v_wavemask` is typed `io`, and `Machine.ioload`/`iostore` took a site's class for the address's — a RAM read pinned as a chip input, trapping `input exhausted` at tick 0. The address decides, exactly as the tracer's own read and write decide it; 51/51 recert unmoved and the tune now certifies over the whole song. And §2's *dropped* voice order became load-bearing for the first time: a tick that runs a tokenizer pass over all three voices and then its audio engine over all three permutes its writes between voices on 8,442 of 10,426 ticks, and `attest` printed "order between voices inside a tick" on its own `dropped` list while comparing the flat edge list — it now compares per voice, which is what `certify.divergence` always did. All fourteen earlier builds re-certify and not one loses an identical tick. Three schema rows written from prose while the family had no certificate: §3.2's quarter-semitone tuning lands as written, plus the low half's own carry-in (2,185 ticks); §3.3's Blackbird program is the pitch/wave stream, with a backward jump folded into the row that lands on it; §3.5's prelude row is corrected — no `ctrl` write, and five writes in three acts. The score is one LZ stream of 2,961 bytes over three ring buffers and §6 drops all of it: 6,255 rows of `dur` 1, `xz -9e` 5,860 against the source `tuneprog.md`'s 7,956 | `attest`, `interp`, `tools/trackerprog_blackbird.py` |
| Galway, the ninth family and the last of the nine | Martin Galway's *Comic Bakery* transliterated onto the same player over **all fourteen subtunes**, 29,911 ticks, 0 divergences, `same_per_register_order` on every one — and the front-end certificate it renders against is new, which retires architecture §9.1's last open row. Two forms, both in the order program and both struck against the sixth family before they were written. **The counted loops nest**: Galway pushes a loop's start and count onto the same 8-deep stack its calls use and the main theme opens one inside a live one from tick 3,072, so `loopcnt`/`loopstart` become a `loopstack` — 0 of Follin's 111,763 ticks differ, a stack of depth one being the register it had. **And what a `stop` stops is `meta.stop` ∈ {`voice`, `sequencer`}**: Follin's skips the whole voice, Galway's clears the run bit and lets the engine play the note out, so a halted voice runs no clock and every other phase, and the tick its score stops on is its last. Its eight sound-effect subtunes are that value alone — three voices stopped from tick 0 and no score at all. In the data: §6 spends `Moke`/`FLoad`/`load*` into 134 interned instrument records because they build the record the *next* note copies, while `DMoke` stays 18 commands because it pokes the live engine; a block's state carries the transpose, the record and whether its stack is empty; and `testpulse = [1, 0, 1]` is the anatomy correction that one of the three unrolled copies sends `wave|8` to its own `pw_lo` and not its `ctrl`. All fifteen earlier builds re-certify unchanged | `universal`, `tools/trackerprog_galway.py` |
| the poison harness, §7's own method (**B1**) | §7 quotes *render both forms over the whole horizon and count differing ticks* forty-odd times and no tool in the tree did it, which is why its headline horizon total was wrong six times over against its own per-build list. `trackerprog/poison.py` is the method: a mutation is a stated edit to the object (`drop PATH`, `set PATH=JSON`, `*` over a mapping's keys or a list's indices), a strike renders both forms and counts, and every row carries the **sites** the path matched and the **first** differing tick — a path that matches nothing renders 0 differing and is not evidence, and a poison the renderer *refuses* is an asserted invariant rather than a crash. A render reduces to one 16-byte digest a tick and caches on the object's own hash, so the whole set is 5 MB and a second poison over one object costs one pass. `tools/trackerprog_poison.py` carries the registry — **thirty builds, 332,358 ticks**, every horizon read from the committed certificate that records it, so no tick count in the harness is typed; the eleven builds P1–P8 were measured over are a named set totalling **236,586**, and both totals are asserted against every `differing … of N` this document and the backlog quote. The four object-level poisons §7 already states reproduce exactly: the clock with no reset diverges on **8,230** of *Je suis Linus*' 8,236, **8,653** of *Do It Again*'s 8,659, **8,077** of *Emomyst*'s 8,084, **14,451** of *End of the World*'s 14,465 and **2,395** of *Guldkorn*'s 2,401; the funk clause alone on **8,639** of 8,659 and **0** of *Je suis Linus*'; `flag.seed` on **11,747** of Commando song 2's 11,780 and **329** of song 3's; `flag.unguarded` on **475** of *Jazzpjazz*'s 1,799 and **127,722** of *Automatas*' 149,025. Three things the first sweep found: `Acc.flag.seed` is a **required** key and not a defaulted one (`universal.py:1380` reads it unguarded, so dropping it raises where setting it measures); `unguarded` now matches **no site** on any Hubbard record, so P6's "0 on all three Commando subtunes" is no longer the same measurement and the sites count is what says so; and the Galway suite's horizon table had subtunes 12 and 13 transposed against the certificate — 121 ticks certified, 31 rendered — and now reads them from it | `trackerprog.poison`, `tools/trackerprog_poison.py`, `tests/trackerprog/test_poison.py` |

Everything after this is the rest of the `trackerprog/` package, under the same
rules (≤ 500 lines per module, hermetic tests, the certificate).

## 8. Refusals and boundaries

Fail-closed, diagnosed, in the tuneprog refusal style — a trackerprog with a
residue is not emitted:

| reason | when |
| --- | --- |
| `sample stream` | a CIA #2 NMI sample mixer or `$D418` nibble stream (the *Easy Does It* mixer): digis are not a score |
| `external input` | see the rule below |
| `unclassified update` | a state cell reaching a SID register whose update T1 cannot bound — the accumulator invariant is the claim, so an unbounded or unmodelled data-dependent update refuses |
| `score not cursor-shaped` | a pattern fetch T2 cannot express as the cursor grammar (a genuinely computed score) |
| `command residue` | a pattern command not expressible as §3.6's record, a register target with a non-literal index included |

**The `external input` rule**, restated so it does not refuse the arithmetic. A
pinned input refuses only when *all three* hold: its `tracedata.input_kind`
(architecture §1, line 45) is `raster`, `cia`, `sid_readback` or `io`; its
recorded values are not constant over the horizon; and T0's provenance shows it
reaching a SID write or a score cursor. `ack`, `entry_reg` and `uninit_ram` are
never external — an ack's value is discarded, `entry_reg` is the caller's
register at the entry, and the power-on pattern is part of the image
(architecture §9.3, point 3). That distinguishes an ack from an input, and it is
why Commando's 11,780 pinned reads — one `entry_reg` read per tick at `$5015`
(commando.md:198-202) — do **not** refuse. Without the rule the effects-rich
exemplar §9 accepts seventh would be unreachable on a technicality.

Boundaries stated, not hidden: cross-class intra-tick write order and voice
order (§2, with the license and the certificate's `dropped` list); cycle
positions inside a tick (architecture §8.3); and the trackerprog is *a* preimage
— many render the same grid, so round-tripping to a source *format* (a `.sng`,
an `.swm`) is a separate family-specific exporter, out of this layer by
construction.

## 9. Acceptance

Exemplars, in order: **GT2 ×2, JCH ×2, SW ×2** (the tracker end — all six
`complete`), then **defMON ×2**, then **Commando ×2** (effects-rich,
non-tracker, aperiodic observable), then **Follin** (score-as-program), then
**Blackbird** (a score that does not exist until the player has decompressed it)
— all landed.

defMON belongs in the list, not in the deferred set: certified twice over —
`automatas` (149,025 ticks, period 129,024, `complete`) and `goto80-jazzpjazz`
(1,799 ticks, `horizon`), architecture §9.2 — with its own prototype document,
and the evidence §3.3, §3.5 and §5 lean on for the general stream form, the
data-side prelude and the second family for `carry(site)`. Two costs:
`automatas` needs `--budget`/`--resume` like every long tool (architecture §11),
and `goto80-jazzpjazz` being `horizon` exercises that terminator, not the loop
claim. Both landed; of the three citations two held and one did not — a voice
runs *two* sidTAB programs at once, so a sidTAB row is a stream row and not an
instrument, and both sidcalls are §3.6 `point` commands
([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md) §8). Per
exemplar:

| # | acceptance |
| --- | --- |
| 1 | `trackerprog.certificate.json`: 0 divergences over the whole certified horizon on the §2 observable, `compared` and `dropped` both populated, the loop claim re-verified where `end.kind = loop` |
| 2 | every refusal named with its cell — no partial emit |
| 3 | the print measured with **§6.2's six numbers** — tokens, lines, statements, blocks, header rows, data rows, which architecture §11 requires verbatim of every presentation change — plus **one extra**, `xz -9e` of the object against **the tune's own load band**. `xz` is §8.3's own unit and no substitute for the six. The first draft compared against `tuneprog.md` and claimed the score compresses *better* than the program that played it; measured against the binary, it does not — §9.1 |
| 4 | recert untouched: 51/51, no tuneprog artefact moves |

State after t3-from-data — these are **scoreprogs** (§1), certified against §2
by `certify.py`, and the rows above are the trackerprog's acceptance and not
theirs: JCH ×2, GT2 ×2, SID Wizard ×2 and Commando ×2 certify `emitted: true`
with no divergence over their whole horizons, lifted from their programs' data
alone — the score as recorded fetches replayed with the score tables never read,
the instruments as the program's own table (30, 19, 13, 11), T1's accumulators
and T2's streams named; `jch-easy-does-it` refuses as a `sample stream`. Six of
eight prints are below the source's `xz` (Hubbard's per-row SID writes keep his
two above). The sound half is still the certified tick outside the fetch
regions, carried in `program` and run by `interp.py`, not §4's fixed procedure
over instruments, streams and accumulators — that reduction is backlog B6/B7,
and the exact replay is what it must be proved against. Of the eight names a
scoreprog shares with a trackerprog, only `meta.commit_order` is the same
field.

**State of the hand exemplars.** Six families are transliterated by hand onto
§4's own procedure and certified against their tunes' players on the PcodeVM,
with no branch on `meta.family` anywhere in `trackerprog/`: Hubbard ×3 subtunes
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ×2 builds
([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ×2 builds
([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
defMON ×2 builds
([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)), JCH V20
×2 builds ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)) and
Follin ×3 named builds of a 32-subtune sweep
([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md)), each
with the inherited loop claim re-verified on the render where the source carries
one, and the write lists identical or permuted rather than merely equal under
§2's reduction. defMON is the first exemplar whose horizon does not fit a
script's 60 seconds, so its tool carries `--budget`/`--resume` (architecture
§11); the whole 149,025-tick certificate is the tool's and the suite renders a
stated prefix. JCH is the first to take `end.kind = fixed_point` — a song that
*ends* — and the first whose two builds disagree about having a shadow. Follin
is the score-as-program exemplar §3.6's `Order` grammar was waiting for, the
first whose fetch is a walk over several rows at one boundary, the first with no
instrument table and no accumulator at all, and the first certified over *every*
subtune of its tune: all 32, 111,763 ticks, write-for-write identical. One
exemplar remains: the T0–T3 lift that would produce these objects rather than a
hand reading of them — today it produces a scoreprog, which is a different
object (§1, §6).

The genericity gate: the six tracker exemplars must lift with zero
family-conditioned code in `trackerprog/` — the same modules, hermetic snippet
tests per mechanism, each schema row's two-family evidence recorded here. The
one remaining single-family row (§5's stateless-phase vibrato) is a data form,
not a code branch. §3.6's "a command's register target is a literal 0..24" is
confirmed and is no longer single-family: it is §3.7's `reg.N`, which JCH's
write-out earned first, and Follin's `$85` lists render through it unchanged.

### 9.1 The object against the load band

The claim above was measured against `tuneprog.md`, which is a pretty-printed
decompilation — a *presentation* artefact, and §3.4's own rule is that a claim
measured against one is not measured. The program that played the tune is the
binary. Measured against it by `tools/trackerprog_sizes.py`, over the poison
registry's own thirty builds — `xz -9e` of the PSID load band, header stripped,
against `xz -9e` of every certified subtune's object concatenated:

| tune | songs | certified | band `xz` | object `xz` | ratio |
| --- | --- | --- | --- | --- | --- |
| *Je suis Linus* (GT2) | 1 | 1 | 2,804 | 5,988 | **2.14×** |
| *Do It Again* (GT2) | 1 | 1 | 2,668 | 5,628 | **2.11×** |
| *End of the World* (SW) | 1 | 1 | 3,992 | 7,876 | **1.97×** |
| *Guldkorn Intro* (JCH) | 1 | 1 | 2,472 | 4,852 | **1.96×** |
| *Comic Bakery* (Galway) | 14 | **14** | 4,760 | 9,096 | **1.91×** |
| *Automatas* (defMON) | 1 | 1 | 4,316 | 8,216 | **1.90×** |
| *Emomyst* (SW) | 1 | 1 | 3,576 | 6,288 | **1.76×** |
| *Knob at Night* (JCH) | 1 | 1 | 9,600 | 16,180 | **1.69×** |
| *Quintessence* (Blackbird) | 1 | 1 | 3,772 | 6,352 | **1.68×** |
| *Chameleon* (Walker) | 1 | 1 | 3,140 | 4,916 | **1.57×** |
| *Jazzpjazz* (defMON) | 1 | 1 | 2,944 | 3,700 | **1.26×** |
| *Commando* (Hubbard) | 19 | 3 | 2,548 | 4,096 | 1.61× (3 of 19) |
| *Ghouls'n'Ghosts* (Follin) | 32 | 3 | 10,888 | 8,648 | 0.79× (3 of 32) |

**The claim does not hold, and this is the finding.** Ten of the thirteen tunes
have one subtune, so the band holds exactly the music the object covers, and the
object is **1.26× to 2.14×** the binary on every one. Galway is the eleventh and
the only multi-subtune tune certified whole — all fourteen — and it is 1.91×. The two ratios below 1 are the two tunes measured on a fraction of
their subtunes against a band that holds all of them, which is not a comparison:
Follin's three of thirty-two is 0.79× and its three objects summed separately are
already 1.36×.

Where the bytes go, and it is not where the first draft assumed:

| build | score `xz` | rest `xz` | score share |
| --- | --- | --- | --- |
| *Je suis Linus* | 3,100 | 3,192 | 49 % |
| *Automatas* | 4,240 | 4,144 | 51 % |
| *Quintessence* | 511,867 raw → 3,204 | 3,252 | 50 % |
| *Knob at Night* | 792 | 15,524 | 5 % |

The **score is not what makes the object large**. Materialised over the whole
horizon with every packed byte unpacked, every cursor spent and Blackbird's LZ
stream expanded 511,867 bytes wide, it still compresses to about what the whole
load band does — 3,100 against 2,804 for *Je suis Linus*, a band that holds the
player *and* the data. It is the sound half — instruments, streams,
accumulators, `state0`, and the schema's own key names once per record — that
doubles the total, and on *Knob at Night* it is 95 % of it.

So the layer trades size for the thing it exists to have. §6's materialisation
rule drops every storage idiom deliberately, and the object carries no player at
all where the band carries one; what it buys is that nine families render on one
procedure, which no binary does. **The honest claim is that the object is
player-independent, not that it is small** — and the sound half is where any
future saving is, which is what B7 and B8 of
[trackerprog-backlog.md](trackerprog-backlog.md) are about.

---

## 10. Open

| question | state |
| --- | --- |
| ~~multispeed scaling~~ | closed by defMON: *Automatas*' entry runs 8×/frame at `cycles_per_tick 2457` and its row clock is `rate = 8`, while its cascades, its oscillator and its filter run at the tick. `rate` carries it and nothing else knows — shortening the row by one clock step diverges on 149,000 of 149,025 ticks ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md) §4.10) |
| `sext` as a delta | `sext(k, T[c])` appears in the IR only as a jump offset (`switch ($1953 + sext(T1934[a]))`, sw.md:1205). The one accumulator delta that sign-extends is SW's filter step, and it lifts as `tabcell(T[c], signed 11)` (§5). If an exemplar shows a sign-extended table entry that is *not* an absolute table cell, `delta` gains a form; until then it does not |
| global-scope accumulators beyond the filter | a survey question, not a schema one: `scope` is read from the value cell's region (§5) |
| the second entry | a tune whose NMI is a second *musical* entry (not a mixer) has two tick clocks; the schema has one cadence with per-voice dividers over it. Refuse until an exemplar demands otherwise |
| ~~the SW orderlist fold~~ | closed by #303: the load was never folded away, the print dropped a return of one value and its flags (`ir.retexpr`); SID Wizard ×2 certify |

Settled since the first draft and dropped from this list: note-space clamping
(§6 — the note space is the trace's reach, and Commando's overrun is a producer,
not a pitch entry) and instrument-scoped accumulator sharing (§5 — `scope` is
read off the region, per cell).
