# trackerprog — a universal tracker representation

The specification: what a trackerprog is, the observable it is certified
against, its schema, the one player that renders it, the lift that is to produce
it, and the refusals and acceptance that bound it. The layer above
[tuneprog-architecture.md](tuneprog-architecture.md): a tuneprog moves a tune
from opaque bytes to a certified per-tick *program*, a trackerprog moves the
music to player-independent *data* rendered by **one fixed universal player**,
with effects as **bounded accumulators**. All nine playroutines of
[playroutine-anatomy.md](playroutine-anatomy.md) §2 are one object (STATE,
TABLES, PLAY), all nine are certified families rendered by `universal.py` at 0
divergences over their whole horizons (§9), and 91.6 % of traced HVSC by weight
has ≥ 50 % of its indexed play sites on a voice-like domain (architecture §9.3).

`anatomy:N` cites `playroutine-anatomy.md`; `jch:N`, `gt2:N`, `sw:N`,
`commando-floor:N` the matching `prototype-*.md`; `gt2.md:N`, `jch.md:N`,
`sw.md:N`, `commando.md:N`, `automatas.md:N` a recert `tuneprog.md`. A family's
evidence is its [transliteration document](prototype-commando-trackerprog.md),
the review's outcome [trackerprog-review.md](trackerprog-review.md), the open
work [trackerprog-backlog.md](trackerprog-backlog.md). Every number here is one
a harness in `tools/` regenerates.

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
| **accumulator** | a bounded state machine over one named `cell` (§5): the only per-tick mutation an effect may be |
| **stream** | a finite table of steps with holds and one terminator (§3.3); the only sequencing an instrument, a prelude, a command or the score may be |
| **scoreprog** | what the lift emits *today*, and **not a trackerprog**: ten keys (`emit.KEYS`), the certified tick itself in a `program` key, its fetch regions cut out and its score in their place as data. It renders on an S4 interpreter, `trackerprog/interp.py`, never on §4 (§6) |
| **lift (T0–T3)** | certified tuneprog + S6 naming plane → scoreprog, fail-closed: what does not fit is a `Refusal(reason, cell)`, never an approximation |

Two artefacts, one target: two objects, two renderers, two certificates
(`attest.py`, `certify.py`), seven shared key *names* at disjoint shapes and one
shared field, `meta.commit_order`. The target is one object — a lift that emits
what §4 renders, with no `program` key (backlog B6, B7). One prototype lift
now reaches it, for one tune ([prototype-lifter.md](prototype-lifter.md));
`tools/tuneprog_scoreprog.py` still emits a scoreprog, and of it no document
may say otherwise. Layer invariant, as a test:
two trackerprogs from different families render on the same player with no
family branch, the source family surviving only as provenance in `meta`; a
schema row needs two families or a marked exception (architecture §11), each
marked one stated at its row with the poison that says what it is worth. Oracle
chain: `sidplayfp ⇐ PcodeVM ⇐ tuneprog ⇐ trackerprog`.

---

## 2. The observable and the certificate

*Which* register a player writes before which is an idiom, so the tuneprog's
ordered interleaved write list cannot be the observable here. The anatomy
licenses dropping it (anatomy:151-155): write order matters only at the frame
edge and for gate edges. Extending that to AD/SR is this document's claim, and
it is conservative — it can only make the certificate stricter, and it keeps SW
1.6's `AD,SR` and 1.9's `SR,AD` distinguishable (anatomy:1232).

| rule | registers | reduction (`grid.reduce_tick` → `TickObs(edges, values)`) |
| --- | --- | --- |
| 1 | `ctrl`, `AD`, `SR` (`grid.EDGE`) | per voice, **every write kept in tick order**, unchanged repeats included. The unit is the **act**, not the write (§3.1): a row writing `AD` twice in its own `sets` states one write, two rows that each write it state two |
| 2 | `freq`, `pw`, `cutoff` (`grid.PAIRS`) | one value per tick, the last the tick left: the DACs are level-sensitive. `pw` carries the SID's 12-bit projection (`grid.PW_HI`), `cutoff` its 3+8 split |
| 3 | `res_route`, `mode_vol` (`grid.LEVEL`) | one value per tick, the same way; a `mode_vol` carrying a sample stream is refused (§8) |

```jsonc
{
  "source": {"tune": "...", "certificate_digest": "..."},   // binds to the tuneprog cert
  "compared": ["per-voice ctrl/AD/SR write order", "freq/pw/cutoff tick values",
               "res_route/mode_vol tick values"],
  "dropped":  ["order between registers of different classes inside a tick",
               "the interleave between voices of one tick's writes",
               "cycle position inside a tick"],
  "ticks": 8236,                       // the whole certified horizon, never less
  "divergence": null,                  // else {tick, register, expected, got}
  "refusals": [],                      // non-empty ⇒ no trackerprog is emitted
  "loop": {"period": 6720, "first_repeat": 8235} | null,    // inherited claim, re-checked
  "end":  {"tick": 8235, "kind": "loop" | "fixed_point" | "horizon"}
}
```

The voice-order row drops the **interleave**, not the order: `attest` and
`certify` group a tick's edges per voice and compare each voice's own sequence,
so what neither compares is how one voice's writes interleave with another's
(Blackbird permutes them on 8,442 of 10,426 ticks), while the order the voices
*run* in is a datum that decides the render wherever two voices meet. Three
source shapes, three ends: `period > 1` gives `end.kind = loop`; `period = 1`
did not loop but reached a fixed point and *ended* (`jch-knob-at-night` at tick
8,576, Follin song 1 at call 12,996), so `end.kind = fixed_point` and §6
materialises to `first_repeat`; a `horizon` tuneprog yields the horizon's score
and an `Order` ending in the `horizon` terminator.

---

## 3. The schema

Serialised as tagged JSON in the S4 style (`ir.enc`): `$trackerprog $pitch
$stream $acc $ins $pat $ord $cmd`, dicts as `{"$dict": [[k, v], …]}`. A field
only `printer.py` reads is an **annotation**, marked as one at its row; a field
nothing reads is not a field; a form with one family is stated with that family
and with the poison that says what it is worth over that family's whole horizon
(`tools/trackerprog_poison.py`).

### 3.1 meta

| key | what it is | reader | families |
| --- | --- | --- | --- |
| `tune`, `song`, `family`, `source`, `cycles_per_tick`, `sid` | provenance and cadence | the print | all nine |
| `voices`, `horizon` | how many voices, and the certified horizon | `__init__`, the certificate | all nine |
| `voice_order` | the order the voices run in — **not** dropped by §2, and it decides the render wherever two voices meet | `tick()` | `[2, 1, 0]` in five |
| `commit_order` | the permutation of `(ctrl, ad, sr)` one **act**'s edges are emitted in | `edges()` | all nine, three of six values |
| `tick` | the voice's phase list (§4.1) | `voice()` | all nine |
| `row`, `stage` | the row program at the boundary and at the fetch (§3.6) | `row_step()` | all nine / three |
| `row_consumes_tick` | whether a row boundary spends the tick: always, never, or the row's own guards | `voice()` | all nine |
| `row_command` ∈ {`held`, `spent`} | whether a command outlives the row that gave it | `command_of()` | `held` GT2 alone, `--poison row-command-spent` **6,820 of 16,895** |
| `tempo` | the row clock, one counter form (§3.6) | `clock()` | all nine |
| `shadow.registers` | the image's registers in the order the flush writes them, each a name or a `[name, guards]` pair. Guarded, the direction is a byte of the **frame**: each fixed direction diverges (4,690 and 3,888 of 8,577) and every entry unguarded on all 8,577 | `tick()` | six; guarded JCH alone, `--poison flush-unguarded` |
| `instrument` | the record every instrument extends, field for field (§3.5) | `__init__` | six |
| `stop` ∈ {`voice`, `sequencer`} | what the score's own `stop` stops (§3.6) | `voice()` | `sequencer` Galway alone, `--poison stop-voice` **837 of 29,911** |
| `pitch_target` | where a taken pitch goes: the chip and the cell, or the cell alone | `take()` | `@freq` SW alone, `--poison pitch-target` **13,994 of 22,549** |
| `pitch_links` | what taking a pitch of the tuning zeroes; a *take* is not a `sets`, so the object has no assignment channel there | `take()` | GT2 alone, `--poison pitch-links` **2,354 of 16,895** |
| `rest_arm` | what a note-on rests the machine in (§3.5) | `note_on()` | GT2 alone, `--poison rest-arm` **152 of 16,895** |
| `row_ends_fetch` | the guard over §3.6's row facts that ends a fetch that is a *walk*; absent, every row ends it and the walk is one step | `fetch()` | Follin alone |
| `wide` | which voice cells are 16 bits | `cell()` | four |

**The tick is a sequence of acts** — one act per **row** a guard admits,
wherever the grammar puts a stream, plus one per row the walk consumes — and
`commit_order` orders one act's own edges, a register keeping its *last* value
inside an act since a permutation has one slot for it. The act is the row and
not the call site, measured: one act per inline row list differs on **2,943
ticks of seven builds** and 0 on the other twenty-three.

| source | `commit_order` | evidence |
| --- | --- | --- |
| JCH V20 | `(ad, sr, ctrl)` | jch:178-180; any other order diverges on all 2,401 ticks of *Guldkorn Intro* and on **0** of *Knob at Night*, whose flush re-orders them |
| GoatTracker 2 | `(sr, ad, ctrl)` | the flush runs `$D418`→`$D400` (anatomy:766) |
| SID Wizard 1.6 / 1.9 | `(ad, sr, ctrl)` / `(sr, ad, ctrl)` | anatomy:1232 |
| Hubbard | `(ctrl, ad, sr)` | commando-floor:201-205 |

There is no second form: a family that writes `AD` from the instrument and again
from the row's own effect needs the sequence (SW, 500 ticks of *Emomyst*), and a
family whose writes go through a shadow cannot tell the difference.

**One register naming.** A register is a *name*: the seven a voice has
(`freq_lo freq_hi pw_lo pw_hi ctrl ad sr`, `universal.REG`) and the four the
chip has one of (`cutoff_lo cutoff_hi res_route mode_vol`, `grid.py`'s own
column names). A bare per-voice name is that register of the committing voice, a
register named **outright** a global name or a voice's own (`v1.pw_lo`), and
that one spelling is what a command's `sets`, a voice's write-out,
`globals.commit`'s first column and `meta.shadow.registers` all use;
`universal.chipreg` is the only place a name becomes a number.

### 3.2 pitch

`pitch: [u16; N]` — the tune's frequency table as the lift **materialises** it,
plus the annotations it proves (tuning, base note, resolution). Storage is an
idiom: Blackbird's two arrays overlapped by 15 bytes, whose quarter-semitone
entries are the sum of two entries of the same array (anatomy:145-147), lift to
**269 explicit u16 rows, base 36**. All nine players have such a table
(anatomy:141), already the `freq_table` role. Every *note* elsewhere is an index
into it or a signed index offset; accumulator deltas are not notes but are in
the **target register's own units**, and `interval(n)` — `pitch[n+1] -
pitch[n]`, `0` where there is no semitone above `n` — is the bridge, a shift
folding into it through `shr`.

**Past the top of the tuning is a producer, and its bound is the index's own**:
Follin reads `notetab[note + transpose]` with a 97-entry table and a one-byte
index, so past entry 96 the read is what the image holds after the table — §5's
`beyond`, its 159 `words` bounded by the *index* and not by the notes the score
holds. **Where such a word names a cell it names the voice**: past Hubbard's
tuning lies the engine's own state for all three voices, so §5's vocabulary
states it as `{"cell": [name, voice]}`, and a byte the object has no cell for is
a `trap` carrying its reason.

### 3.3 streams

The one sequencing form. A stream is a finite table of steps:

```
Step = { when:  [ guard, … ]               // the one guard shape in this schema
       , sets:  [ set(target, value), … ]  // assignments, in this order,
                                           // all inside one tick (Walker's gate 1→0→1)
       , point: [ (slot, row, keep, guard?), … ]   // re-point a stream, keeping its
                                           // hold or not; null is a cursor on no row
       , op:    acc(acc_id) | pitch(offset | absolute, wrap?) | cmd(name) | none
                                           // acc, cmd: GoatTracker 2 alone;
                                           // wrap, the note column's own bits: SID Wizard
       , run:   [ acc(acc_id), … ]         // an acc the step runs on every tick it holds
       , hold:  k ticks (k ≥ 1)
       , next / jump: row | null           // where the step goes, and where a row jumps;
                                           // null in either is no row, and the stream stops
       , trap:  why }                      // a row the certified horizon never reaches:
                                           // arriving at it is an assertion, by name
```

Every field is optional and every one is a *step's*, not a family's; any other
key on a row is the family's own column, read through `tabcell` (§5) and by
nothing in the player. **A step has two readers**: a stream a **cursor** steps —
the `machine` phase's ranked streams without `all`, and the global channel's —
is `Player.stream_step`, and a stream run as a **guarded row list** is
`Player.runstream`, the one procedure for a declared stream with `all`, a
`{stream}` phase, a `{stream}` step of §3.6's row program, an instrument's
`on_note`, a prelude and a command's `rows`. Which one runs it decides which
fields it has:

| field | `stream_step` (cursor) | `runstream` (rows) | families writing it |
| --- | --- | --- | --- |
| `sets` | yes | yes | all nine (5 on a cursor, 9 in a row list) |
| `when` | — the *stream's* own guard is asked once per slot, never the row's | the **row's** | 8 — every family but Blackbird |
| `point` | — | yes | 6: GT2, SW, JCH, defMON, Blackbird, Galway |
| `hold` | yes | — | 4: GT2, SW, JCH, defMON |
| `next` | yes | — | 7 — every family but Hubbard and Follin |
| `jump` | yes | — | 2: GT2, defMON |
| `op` | yes | — | 3: GT2, SW, Galway |
| `run` | yes | — | 4: GT2, SW, Blackbird, Walker |
| `trap` | yes | — | 4: GT2, SW, JCH, Blackbird |

A stream may be reached both ways where a family names one from two places
(GT2's `note_on` and `exit`, GT2's and SW's `hard_restart`).

**A cursor is on a row, or on none, and never says so by its index**: a stream
that is not running carries `row: null`, which is what a `point` that stops one
writes and what a `next` or a `jump` of null leaves. A reserved index 0 cost
five families in data — Blackbird's `+1` on every row, cursor and pointer,
Walker's and Galway's opening `trap` row, SW's `no stream` row per instrument,
defMON's reserved cascade row and its halt target — while an engine's own
1-based tables are unaffected.

**A stream's `rate` is a divider**: one form and one procedure
(`universal.dividercode`) wherever the schema has one — a per-voice counter
**cell** the run steps down by one, firing where it passes zero and reloading
from the object's own expression, `{cell, reload}`, which is also §5's
`Acc.rate`. `rate: 1` and no `rate` are no divider; a bare `k` names no counter
and is refused. `meta.tempo.rate` is **not** this: with `phase` it selects which
ticks the tune's one clock steps on, once per tune, where a divider is per voice
and per run.

**Three of the four `op` values have one family**: a step that arms an
accumulator and a step that runs one of the score's own commands are GT2's
wavetable, and `wrap` — the note column's own bit width, the modulus a relative
note comes back inside — is SW's; `pitch` is five families'. Both arms are
struck by `--poison op-wrap`, which takes the step past the tuning and is
refused rather than rendered.

**A step that produces owns the tick where a family says so**: the row leaves a
flag in its own `sets` (`!name`, §5's producer flags) and that family's arms
read it in their `when` — one family's precedence, stated in the object and not
in the player. GT2's: removing the rule differs on 2,028 of 8,236 and 2,873 of
8,659 and on **0 of the other twenty-eight builds' 315,463**.

**A step's counter is read either before or after its own move**, which says
whether the tick that consumes the step also runs it — and that is not a field:
a step that acts and *then* holds is two rows, `hold: n-1` and the row its
landing tick holds and acts on, the second appended so an instrument's own row
numbers stand (**0 differing of 22,549**, under a hermetic snippet). There is no
"steps per tick" either: defMON's cascades run up to 8×/frame under a CIA
cadence, but the tick *is* the entry (anatomy:213).

What lands here: GT2 wavetables and GT2/SW pulse, filter and speed tables; JCH's
`rec6` pulse and `rec7` filter column programs, **4** columns each
(jch.md:527-551; the print's three is a region's derived origin, backlog P1); SW
tempo programs; defMON sidTAB rows, variable-length register-column records with
delay and jump (anatomy:211, the form at its most general); Blackbird's pitch
and wave programs; Galway's and Walker's own.

### 3.4 accumulators

Declared once in `accs`, referenced by streams, instruments, preludes and
commands; the record is §5 and the player reads it field for field. **`Acc.step`
is not one of its fields**: it is the T1 plane's, a field of a *scoreprog* (§1,
§6). No hand tool emits one and `universal.py` never reads one — a trackerprog's
accumulator states its `delta`, `bound`, `policy`, `rate` and `phase`, and §4's
fixed procedure computes the next value from those.

### 3.5 instruments

```
Ins = { prelude:   stream | [ Step, … ] | null  // the note's lead-in (see below)
      , on_note:   stream | [ Step, … ]         // the note-on's own §3.3 stream
      , accs:      [ acc_id, … ]                // the modulations armed at note-on
      , pitch:     { value, octave } | null     // where the sound is no pitch (§3.2)
      , transpose: semitones                    // SID Wizard alone
      , pw:        (lo, hi) }                   // Hubbard alone: §5's `ins.pw` space
```

**Those six names are the whole of what the player knows about an instrument.**
Everything else on the record is the family's own column, read through `{"ins":
path}` and `{"insrec": [cell, path]}`; a column no expression, no player line
and no print line reads is not a column, which a hermetic test holds over all
thirty cached objects. `adsr` is not a player name — no line of `universal.py`
reads one — and all seven families that carry the pair read it as `["ad",
{"ins": "adsr.0"}]`: a player whose note-on emitted it in `commit_order`'s place
reproduces **SW alone** (0 of 8,084 and 0 of 14,465), while JCH's note-on puts
`ad`, `sr` and `ctrl` in **one act** (257 of 2,401), Walker's puts them beside
sixteen other cells (346 of 8,052), and Hubbard, Blackbird and Galway write
theirs from a *stream* elsewhere in the tick (1,543 of 11,780, 1,428 of 10,426,
1,327 of 9,450). `adsr` and `wave` are the two family columns the **print**
knows by name, which makes them annotations and not fields.

| datum | what it says | families |
| --- | --- | --- |
| `insrec` | `ins` reads the record the voice is playing, `insrec` the record a cell names — what a family whose tables live *inside* the instrument needs, staging the row's instrument into a cell while `ins` still names the old one | SW alone, `--poison insrec-voice` **853 of 22,549** |
| `meta.instrument` | what all of a family's instruments carry is the family's: the record each extends, its own entries winning field for field. With a row list several instruments carry stated once as a declared stream they name, the instrument half falls 35,321 → 14,480 raw bytes on Galway, 25,334 → 12,876 on JCH, 25,262 → 11,355 on SW, 12,527 → 2,758 on Walker | six |
| `pitch: {value, octave}` | where a family keys a sound the tuning has no note for, the score gives the row no note and the instrument answers; each is one §5 expression, read only where `note` is none. An unpitched sound has no semitone above it, so a vibrato over one steps by nothing | Hubbard's two drums (which read two *other* voices' cells and name them, §3.2), Galway's silence |
| `meta.rest_arm` | a note-on re-arms the instrument vibrato and *replaces* what the score's last command armed. The same arms in `meta.instrument.accs` differ on **2,714 of 8,236** and are refused on the second build: an instrument's arms are the voice's for as long as the instrument is | GT2 alone, `--poison rest-arm` **152 of 16,895** |

**One grammar for a stream in four places, one spelling, one procedure.** A
declared stream, a `prelude`, an `on_note` and a command's `rows` are all
guarded §3.3 steps, each written either as the name of a declared stream or as
the rows themselves, and `Player.rowsource` resolves the two into one plan that
`Player.runstream` runs. A guard therefore has one spelling everywhere and never
a positional slot beside the thing it guards; a note-on's tie is a fact of the
row (`when tie == 0`) like any other guard; and a step's own `wave`, `pulse`,
`filter` and `pitch` streams are named §3.3 streams with a `rank`, re-pointed
from here rather than a slot map on the instrument.

**Hard restart is not one fixed shape** — SW 1.6 writes AD,SR and 1.9 SR,AD
(anatomy:1232), Blackbird's prelude has no TEST bit (anatomy:133-135), Walker
retriggers the gate off/on *inside one call* (anatomy:139-140) — so a prelude is
just a stream of `set` steps ending `early` ticks before the next row boundary,
and the write order §2 compares is the stream's own step order:

| source | prelude |
| --- | --- |
| JCH V20 | `early = 2`; `set(ad,$0F) set(sr,$00) set(ctrl, mask $FE)`, note row `set(ctrl,$09)`; the flag that arms it is a **cell the note-on sets**, not a column the prelude reads |
| GoatTracker 2 | `early = gatetimer` (instrument column 7); `set(ctrl, wave & $FE)`, note row `set(ctrl, firstwave\|TEST)` (anatomy:214, 742) |
| SID Wizard | `early = 2`; rows in the version's own AD/SR order, then `set(ctrl, TEST\|gate)` at tick 2 |
| defMON | `null`, and the data is right: `WG=00 AD=0F SR=00` → hold → `WG=09` is the first three rows of the sidTAB program the row starts, so nothing schedules it |
| Blackbird | `early = 2`; `set(sr, 0) set(@wavemask, $FE)` and **no `ctrl` write** — the engine ANDs that mask into every control byte for two frames. The note row is five writes in three acts |
| Hubbard, Galway, Follin | `null` — Hubbard cuts notes with SR=0, Galway pulses TEST at note-on (anatomy:137-140) |

`early` is not an instrument field: it is `meta.tempo.early`, a guard over the
row clock and one number for the tune, GT2's `gatetimer` being where its *value*
comes from (anatomy:214). The nine-family "sound definition" row (anatomy:211)
reduces here — Hubbard's 8-byte SID image plus fx bits is `adsr` plus armed
`accs`, GT2's 9 columns plus pointers is `adsr`, `prelude` and four stream refs
— and defMON's "the sidTAB row *is* the instrument" is **wrong**: a sidTAB row
is a *stream* row, a voice runs two such programs at once, so no single `Ins`
can name them and both are §3.6 `point` commands.

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
          , end: jump(step) | stop | {stop: cmd}   // what the end of the list does
```

**A command is a record, not an opcode**: a tempo is `sets` on the tempo cell, a
volume `sets` on the `$D418` nibble, a portamento `arms` with overrides, a
re-point `point`; a residue no combination expresses is a `command residue`
refusal (§8). A command is named by **what it does**, never by its dispatch
index — GT2's `T144A` nibble and SW's `BIGFXTABLE` are the patched jump the lift
already spends — which costs the round trip its *shape*, three of SW's effects
sharing an encoding across two columns (§8's preimage).

**The order is a sequence program** — a flat orderlist is the degenerate case —
and it stays data: no conditionals, no arithmetic, statically bounded. Follin
writes all five steps (302 calls, 126 returns, 196 marks, 195 loops, 39 jumps,
46 stops across 32 subtunes), and two spellings it forced: a **call names where
it comes back to**, the 6502 pushing `ptr + 3`, and **`mark` and `loop` are two
steps, not one `for`**. Galway's `mark` pushes the loop's start *and* count onto
the **same 8-deep stack** its `call` uses and opens a loop inside a live one, so
a voice carries a loop *stack* — which a family whose loops do not nest sees as
the register it had (0 of Follin's 111,763 ticks differ). A voice's `play`
cursor steps in `sequencer_step`'s walk and in `advance`, the cursor of a clock
that fetches ahead, and both are `order_step`.

| datum | what it says | measured |
| --- | --- | --- |
| `stop`, `Order.end` | `stop` ends one **voice**, not the tune, and an `end` that is not a `jump` does the same to the voice whose list ran out; it may name a command the tune runs after its last row. Hubbard's `$FE` is that command's four `sets` on `v0.ctrl`/`v1.ctrl`/`v2.ctrl`/`mode_vol`, and its three lists end together, so the voice stop reproduces the tune's | **0 differing of 292,914** over the twenty-six builds whose lists can run out |
| `meta.stop` | Follin's block tests the active flag and skips the whole voice; Galway's clears the run bit and returns from the *voice routine*, so the sequencer stops and the engine plays the note out. `sequencer` has nowhere else to live — an order's `stop` is a step of the score and no step of a score writes a cell | Galway alone, **837 of 29,911** |
| `state0.stopped` | which voices the entry left stopped — a sound effect starts one to three | Follin, Galway |
| `state0.prologue` | the init call: a `score.commands` entry every voice runs on a tick of its own before the first, by the procedure the `end` command uses | GT2, SW; 0 of 39,444 |
| `meta.row_command`, `state0.held`, `{hold}` | effect memory: whether a command outlives its row, what a voice carries into the first row, and where it takes one | GT2 alone; **6,820** and **13,947 of 16,895** |
| `Cmd.tie` | a held command ties the rows *after* the one that gave it, which no field of a row states | GT2 alone, **6,237 of 16,895** |

**The note column is a token class, and the layer spends it.** Every family
packs more than a pitch into that byte and each packs something different (GT2
`$BD` rest, `$BE` keyoff, `$C0+` packed rest, anatomy:872; SW's eight ranges,
anatomy:1204; Hubbard a keyoff *bit*). Admitting `keyoff` as a note value admits
`sync on` as one and the rule collapses, so: **a value that is not in the pitch
table is not a pitch, so it is not a note**, and each token becomes its own
field of the Event — `sounds`, `note`, `gate`, `dur`, `tie`, `arm` — and which
byte range each family packs into which field is that family's own document.
`sounds` is the field §4's tick reads to decide whether a row keys a note, and
it is the *only* one; the ctrl mask a row leaves is `$FF` where it sounds and
`$FE` where it does not, overridden by an explicit `gate` — a chip fact, the
waveform byte carrying its own gate bit.

**The row is a program, and one procedure runs it.** `meta.row` is an ordered
list of steps over the event, each with an optional `when` over the row's facts
(`sounds`, `keys`, `newins`, `field`, `gate_stmt`, `tie`, `dur`, `note`,
`wraps`, and `gate`); `Player.row_step` is the one procedure over all six:

| step | what it does |
| --- | --- |
| `{sets}` | assignments, over the row's own facts as the payload |
| `{ins}` | the instrument the row names, where it names one |
| `{stream}` | a guarded §3.3 stream — a declared one by name, or the rows |
| `{note}` | the sound the row keys: the pitch, and what the note-on arms (§3.5) |
| `{commands}` | the row's own commands, in row order |
| `{hold}` | the command the score gives the voice to keep, and the tie it carries |

`meta.stage` is the same list in the same grammar, run by the same procedure at
the `fetch` phase, for a clock that commits some of the row where it *reads* it:
GT2 stages the instrument, the gate mask and the held command; SW the row's
instrument into a cell of its own, its tables living inside the record so moving
`ins` early would move them; JCH the gate mask, the order's transpose, the pitch
and the row's whole commands. Its payload is those facts plus three values it
copies rather than tests (`ins`, `note`, `transpose`), and a fetch that stages
**no** row runs the same program over `row_facts(null)`, every fact at zero,
with `dur != 0` the guard for a step that must not run then: **0 differing of
60,848** over the four staging families' seven builds.

| source | `meta.row` |
| --- | --- |
| Hubbard | `ins` · `note` when `sounds` · `sets @wave` · `stream note_on` · `commands` |
| GoatTracker 2 | `note` when `sounds` · `stream note_on` when `keys` · `commands` |
| SID Wizard | `sets @pending` · `ins` · `stream gate_row` when `gate_stmt` · `note` when `sounds` · `stream pitch_row` when `sounds` · `commands` |

Four forms the exemplars settled: `play` carries `vol?` and `tempo?` (SW's
orderlist columns are pattern, transpose, **volume, tempo**, stop, loop,
anatomy:209, and `vol` lands on the one global nibble, last-writer); a `horizon`
terminator for a source materialised only as far as the certified ticks reach;
`arm(acc_id, overrides)`, since GT2's vibrato parameter selects a bound *and* a
step, so the command re-binds a subset of `{delta, bound, rate, phase}`; and a
command's register target is the register's **name** (§3.1).

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

Each family's clock is a *value* of it: Hubbard's and defMON's **divider** is
the rate, a step of `−1` on the row's own length and no reset; GT2's and JCH's
**countdown** a step of `−1`, a boundary at zero and a reset that reloads past
it, GT2's funk tempo one more reset clause ahead of the plain one; SW's
**counter** a step of `+1` with two clauses that zero it and move its tempo
program on. `boundary`, `fetch` and `early` are guard lists like every other,
and the step a tick is is the virtual cell `phase`. The clauses are
load-bearing: no reset at all diverges on 8,230 of *Je suis Linus*' 8,236, and
striking only the funk clause on 8,639 of *Do It Again*'s 8,659 and 0 of *Je
suis Linus*'. A tempo command is `sets` on the tempo cell or on the cell a tempo
stream is read through, per voice with a global default (anatomy:209, 213).

### 3.7 globals

The filter as a global channel (cutoff streams and accumulators, resonance,
routing), master volume, per-voice and default tempo, the producer `flags` and
their defaults (§5), and `commit`. Filter ownership — SW's owner voice, JCH's
"filter runs on track 0", which is a **byte of the tune's own filter table** —
is last-writer over the channel, which the observable makes exact without an
ownership construct; so is a voice's write-out sending one of the channel's
registers itself, naming it outright in its own `sets` (§3.1). Keyboard tracking
(SW `CKBDTRK`) is **not** an `interval` term: it adds an *absolute* table entry,
which is §5's `tabcell(T[c])` on the cutoff target — the construct defMON's
oscillator uses, where the table happens to be the tuning.

| datum | what it says | families |
| --- | --- | --- |
| `commit` | the registers the channel commits, `[name, value]` or `[name, value, guards]`; guarded, two entries name one register under opposite guards (SW's tracked cutoff against the tune's) | eight write two columns; guarded SW alone, `--poison commit-guard` **22,548 of 22,549** |
| `streams` / `after` | the channel runs ahead of the voices where they *read* it and after them where they **write** it — Follin's owner voice reloads `#cutoff` at its note-on and the filter sweeps from there in the same frame | `after`: Follin, 383 ticks of song 0 |

---

## 4. The universal player

Normative semantics — anatomy §2's pseudocode made exact. This is
`Player.tick`/`voice`/`commit` line for line, and there is no other entry:

```
tick():                                            # `Player.tick`
    if meta.shadow: emit the image the last tick left, entry by entry in
        meta.shadow.registers' order, each under its own guards      # §3.1
    if a command is due on a tick of its own:       # `state0.prologue`, or the one
        for v in 0..voices-1: run it; commit(v)     #   an order's `end: {stop}` named
        return the tick's writes                    # nothing else runs on that tick
    channel(globals.streams)                        # the channel the voices read
    for v in meta.voice_order: voice(v)             # §3.1
    channel(globals.after)                          # the channel the voices write
    channel_commit()                                # §3.7

voice(v):                                          # `Player.voice`
    if v is stopped and meta.stop == voice: nothing runs at all      # §3.6
    boundary = false if v is stopped else clock(v)
    spent = false
    for phase in meta.tick:                         # §4.1, in the order given
        if meta.stop == sequencer and v has been stopped this tick: break
        if phase is {stream: s}: run it             # the write-out: spent or not
        elif not spent: run it                      # `row` sets spent from
    commit(v)                                       #   `row_consumes_tick`

clock(v):                                          # `Player.clock`, §3.6
    stepped = tick_no % tempo.rate == tempo.phase   # `fetch` and `prelude` read this
    if not stepped: return false
    phase = tempo.cell[v]; tempo.cell[v] += tempo.step   # `phase` is the step it was
    hit = tempo.boundary holds
    the first tempo.reset clause whose guard holds, and no more
    return hit

channel(key):     globals[key]'s streams, each by its own reader (§3.3)
channel_commit(): globals.commit, guard by guard -- into the image, or to the chip
                  on this tick for a register meta.shadow.registers does not name

commit(v):                                         # one group of the tick's writes
    the voice's freq/pw producers, in declared order    # §2 rule 2 keeps the last
    then its edge writes, the tick's acts in order,
    each act's own in `meta.commit_order`, one slot per register   # §2 rule 1, §3.1
```

Nothing abandons a tick, and no phase, `voice` or `tick` can: a voice the score
stopped runs no phase (or, where `meta.stop` is `sequencer`, every phase up to
the one that stopped it), and the tune's own end is that voice stopped like any
other.

**§4.1 The voice's tick is a declared order.** `meta.tick` is a list of phases:

| phase | what it is |
| --- | --- |
| `fetch` | on a tick the clock stepped and `tempo.fetch` (else `tempo.early`) admits: read the row the clock runs ahead of and run `meta.stage` over it — §3.6's own row program, one tick position earlier |
| `prelude` | on a tick the clock stepped and `tempo.early` admits: the instrument's early rows, where the next row is near |
| `row` | the row boundary, where the clock said so: consume the Event, run §3.6's row program, and set `spent` from `row_consumes_tick` |
| `machine` | the voice's streams and armed accumulators, in the `rank` order the object gives (§3.3, §5) |
| `commit` | a group boundary — what the tick has written so far, written |
| `{stream: s}` | a stream every path of the voice ends on, run whatever the row did, and the one phase a spent tick still runs |

Which phases a tune has, and in which order, is one datum — and it is what says
which of two preludes wins the register, GT2's running *after* its machine and
SW's before, with no third datum deciding.

| source | `meta.tick` |
| --- | --- |
| Hubbard | `prelude` `commit` `row` `commit` `machine` |
| GoatTracker 2 | `row` `commit` `machine` `fetch` `prelude` `{stream: exit}` |
| SID Wizard | `fetch` `prelude` `commit` `row` `commit` `machine` `{stream: exit}` |

**Producers, not a sum.** `freq = pitch[note + …] + Σ accs` cannot reproduce
Hubbard: within one tick its vibrato, portamento, drum and arpeggio each *store*
`freq` independently, the arpeggio storing an absolute `FREQ[note + $C]`
(commando-floor:213-251). A voice carries an ordered **producer list** per
16-bit target, each `Producer(target, mode, value)` with `mode add` meaning
`pitch[note + transpose + offset] + Σ accs(this)` and `mode set` an absolute
value; `commit` evaluates them in declared order and §2 rule 2 makes only the
last observable, which is the chip's own semantics. Four families declare one
`add` producer per target and degenerate to the old formula; Hubbard declares
four on `freq`.

Everything a real player does beyond this — ghost register files and flush
loops, unrolled voices, `X = 7v` double-duty indices, SMC-patched dispatch,
1-based tables, relocation, stack tricks — is compilation, already decompiled
away by S4–S6, and leaves no residue in the data. The one residue `X = 7v` left
is a *marked defect*: SW 1.6's first-frame `LDA freqtbh,X` is `{"bug":
"voice_base"}`, whose one value is named as the defect and whose every other
name refuses to compile (`--poison no-bug`, 1,038 ticks), and Hubbard's `$54EB`
is the voice cell `voicebase`, seeded from the tune's own three-byte table and
written by nothing.

---

## 5. Effects as bounded accumulators

An accumulator is a *reading*, not the layer's centre: Follin has none at all
across 32 subtunes and 111,763 ticks, Galway's effects are its order program,
Blackbird's are streams. Where a family has them the record is this, as
`universal.py` reads it — every field has a reader in the player, and the three
the print alone reads are marked as the annotations they are:

```
Acc = { cell   : the value's own, in one vocabulary -- `tick`, a voice cell,
                 `#global`, `ins.pw`, `shadow.<pair>`, any with `.hi`/`.lo`
      , width  : 8 | 11 | 12 | 16                 # the value's modulus
      , produce: [ [register, part], … ]          # where it goes: lo | hi | byte
      , delta  : const(k)                             # signed
             | field(cell, mask)                      # a masked field of a live cell
             | tabcell(T[c], signed = k | unsigned)   # an absolute table entry at a cell
             | interval(n)                            # pitch[n+1] - pitch[n], 0 at the top
             | repeat(Δ, n)                           # n·Δ, a triangle's closed form
             | Δ + flag(name)                         # any of the above, plus a live carry
      , bound  : { interval: [lo, hi], from: proved | projected | observed
                 , witness: <guard | mask | period> }  # two constants, asserted at every
                                                       # store; witness is a note
      , policy : wrap | reflect | reflect-complement
             | { clamp: v, edge?: b } | { reload: v, when?: guard }
      , rate   : 1 | { cell, reload }             # §3.3's divider, one form and one
                                                  # procedure.  A bare k: refused
      , phase  : bit(self, k) | bit(cell, k) | cell != 0 | fn(global_counter) | acc(id)
      , amplitude : { interval: [lo, hi], shift: k, witness? }   # the turn a reflect or a
                  | { count: n, cell: <phase counter> }          # reflect-complement makes:
                                                  # a swing, or the steps counted -- never
                                                  # a claim about where the cell goes
      , flag   : { name, seed? }                  # the carry this step's own arithmetic
                                                  # leaves the next producer
      , rank   : the order the tick runs it in, against the streams (§4)

      # the three guard channels, each a separate question
      , when       : guard   # whether the accumulator runs at all this tick
      , step_when  : guard   # whether it counts as a *step* -- what `gate` reports
      , delta_when : guard   # whether the delta applies; a one-shot is this, not a policy

      , gate   : { true: [sets], false: [sets] }  # what the step writes either way,
                                                  # masked to 8 bits: edges and counters
      , emit   : "entry"                          # produce the value it had, not the one
                                                  # it leaves -- the epoch of the read
      , beyond : { index, words: [ expr | {trap: why}, … ] }   # what the value means past
                                                  # the tuning, by how far past (§3.2)
      , trap   : true                             # an arm the certified horizon never
                                                  # takes; reaching it is an assertion

      # annotations: written by the tools, read by `printer.py`, never by the player
      , target : freq | pw | cutoff | note | split(k, 8)   # what `produce` routes to
      , scope  : read from the value cell's region index domain
      , note   : why a `trap` is dead, in words }
```

Twenty-one fields, eighteen of them the player's. An `Acc` has no name of its
own: it is named by the key `accs` declares it under, which is the name a
stream's `op`, an instrument's arm and a command's `arms` all use. Five fields
and two policy values have one family, each kept with its family, its reader and
its poison:

| field | family | reader | struck |
| --- | --- | --- | --- |
| `emit: "entry"` | Hubbard | `step()` — the produce sends the value the tick came in with, which the drum's countdown needs and no `sets` can say, the gate running after the store | `emit-entry`, **11,755 of 35,340** |
| `beyond` | Hubbard | `past()` — the arpeggio's own words past the tuning (§3.2) | `acc-beyond`, **120 of 11,780** |
| `flag: {name, seed}` | Hubbard | `apply()` — the carry of the last of `n` additions | `flag-seed`, **11,747 of 11,780** |
| `trap` | Hubbard | `step()` — an arm the certified horizon never takes | `acc-trap`, **0 of 332,358**: the 0 *is* the claim |
| `amplitude: {count, cell}` | Walker | `turned()` — the turn a counter decides, where the cell is two modulators' | `amplitude-count`, **7,790 of 8,052** |
| `policy.edge` | SID Wizard | `toward()` — which side of the target the step that lands on it is on | `clamp-edge`, **1,129 of 22,549** |
| `policy: reflect-complement` | GoatTracker 2 | `apply()` — the phase byte's complement arm | `reflect-complement`, **3,593 of 16,895** |

**One cell vocabulary, and the voice is part of it.** `cell` names the value's
own on the committing voice, `{"cell": [name, voice]}` the same name, space and
half on the voice it states; only `beyond` and an instrument's `pitch` use the
second, because only they are memory models (§3.2). A modulator carries no state
beyond its own expressions: there is no private state, no subscription and no
event. A cell is one of three things, and `Player.cell` decides in that order:

| what | names |
| --- | --- |
| the eight voice cells the player declares (`Player.__init__`) | `ins` `wave` `orderpos` `rowsleft` `dur` `freq` `note` `lastnote` |
| the names `cell()` answers itself, from the tick rather than the vector | `voice_index` `counter` `phase` `tied` `freq_hi` `freq_lo` `pw` `pw_lo` `pw_hi` |
| everything else | the tune's own, declared and seeded by `state0.cells`, read on the committing voice — `#global`, `ins.pw`, `shadow.<pair>`, and a `.hi`/`.lo` half with them |

The eight are the player's state vector and no more — a constant in the player
is a family in the player, so Hubbard's `pwdir` and `pwdelay` are seeded like
every other cell a tune has. Two of the eight are also a tune's (defMON's row
clock counts in `rowsleft`, Follin's in `dur`, and `state0.cells` fills the same
slot); the reserved names win over the vector, and `pw`/`pw_lo`/`pw_hi` read the
instrument's own space only where the tune declares no cell of that name. One
form reads a row of a stream as a value, `tabcell(T[c], column)`, which is what
every table read in the object is.

**Two forms the lift alone writes.** `shl` is `shr`'s own mirror — `x << k` as
one node, where a lowering otherwise spells it as *k* doublings of a copied
subtree and pays 2^*k* leaves for it — and a `sets` target may name the
`.hi`/`.lo` half this vocabulary already has, which `cellput` implemented and
`putcode` reached for `shadow.` alone. No hand object writes either, so the
harness measures what that is worth and no more: **0 differing of 332,358** over
the thirty builds (`--emit-digests` then `--against`). Both are exercised by the
binding's own certificate ([prototype-lifter.md](prototype-lifter.md) §4, §5).

**Bounded** is the invariant, not a hint: `bound × policy` makes the reachable
value set finite and statically known, and `Player.store` asserts the interval
at every move — turning that assertion on took **five of sixteen** records out,
every one a false claim. *Statically known* means `bound.interval` is **two
constants**; an interval that reads a live cell is arithmetic the step does, and
that arithmetic is **`amplitude`**, the triangle's own swing, which in neither
certified family is the bound's interval. A turn is a bound on the value **or** a
count of the steps: the bound is exact wherever the cell an `Acc` moves is that
`Acc`'s alone, which is every family but Walker, whose two modulators sum into
one frequency offset and have both moved it on 1,140 of 9,949 steps — so `count`
is the period and `cell` where the modulator counts its own, no bound being able
to say it since Walker's period is a byte of the *instrument*.

| `bound.from` | source of the interval | evidence |
| --- | --- | --- |
| `proved` | a guard on the value itself, on the update path | GT2 `if b14A0 < b1096`; Hubbard `if ins.pw_hi == $E` … `== $8` |
| `projected` | the write's own mask — the interval the chip can see, a guard on a masked projection of the target included | Hubbard's pw is 12-bit only because the store is `(pw_hi + carry) & $F`; SW's `cutoff_lo & 7`; `grid.PW_HI` is the same projection on the observable side |
| `observed` | `history.py` over the certified horizon, under the period witness | JCH's pulse and filter segments have no guard and no mask: the bound is the register width and the *stream* ends the segment |

A free slide's direction cell is `wrap`, not `reflect`: `reflect` is a bounce,
where the play *turns* the direction cell, and a cell the **score** sets from a
stream byte picks a direction and never turns.

**The 6502 carry has one channel and one expression**: a `sets` writes `!name`,
any expression reads `{"flag": name}`.

| form | what it is | families |
| --- | --- | --- |
| `carry_out(e, w)` | the carry an add of `w` bits leaves: bit `w` of the sum before the mask | SW's keyboard-tracking add and pulse write-out, defMON's oscillator (8) and its slide and cutoff (16) |
| `borrow_out(e, w)` | a subtraction's own carry, the 6502's `C`: 1 where it did **not** borrow. Exactly `1 − carry_out(e, w)`, which takes the `+ 2^w` bias out of the object and into the player, where the machine's own arithmetic belongs | SW's pulse high half and vibrato phase, defMON's cutoff on the way down |
| `Acc.flag` | the carry the accumulator's *own* arithmetic leaves, where that arithmetic is a loop and not an expression: `repeat(Δ, n)`'s carry is the carry of the **last** of `n` additions, and `n`, `Δ` and the intermediate values are the arm's and the loop's | Hubbard alone; `seed` is the value at entry, surviving only the frame the count is zero |

`scope` is read off the region the value cell lives in and is per **cell**, not
per `Acc`: Hubbard's pulse sweep keeps its value in the instrument record
(`ins.pw`, shared by two voices) while its direction and divider are per voice.
What a row *command* zeroes is a `sets` on the accumulator's own cell, in the
same act and under §5's bound — `meta.pitch_links` (§3.1) exists only because a
*take* has no assignment channel.

Every per-frame modulation in the anatomy's row (anatomy:212) lands on one line,
each with two certified families or a marked single-family exception:

| effect | Acc | evidence |
| --- | --- | --- |
| vibrato (triangle) | **two coupled Accs**: a phase Acc `delta const(+2)`, `bound [0, speedcmp] proved`, `policy reflect-complement`; and a freq Acc whose `phase` is `acc(phase_id)` bit 0 and whose `delta` is a shifted `interval` or a `const` | GT2: `voice[x/7].b14A0 = (a + 2) + c`, `t4 = b14A0 & 1`, then `ghost.freq += ptr` or `-= ptr` (gt2.md:852-862); the bound is the SMC cell `b1096 = T1851[y] & $7F` (gt2.md:812 — speedcmp, **not** the depth) and the complement is `a57 = ~b14A0` (gt2.md:835); `ptr` is either the 16-bit const `(T1851[y] << 8) \| T1863[y]` or `interval(freq_lo_idx_2) >> T1863[y]` through the variable-shift loop `p_12E5` (gt2.md:653-684). JCH: the same two-cell shape on its slide/vibrato (jch:82) |
| vibrato, stateless phase | one freq producer, `delta repeat(interval >> (ins.vib + 1), n)`, `phase fn(global_counter)` | **single-family exception (Hubbard)**: `phase = counter & 7; if phase >= 4: phase ^= 7`, then `for _ in 0..phase-1: f += step` (commando-floor:215-221). It is the closed form of the triangle every other family accumulates, not a new mechanism; admitted because Hubbard is §9's certified non-tracker exemplar and nothing else makes its `freq` exact. T1 reads the `phase` off the counter that decides the count (`accrule.fn_phase`) and verifies the producer against the register, not the cell |
| tone portamento | target freq, `policy clamp(pitch[target])` with an `edge` (where the step that lands exactly on the target either reaches it or does not — SID Wizard's alone, sidwizard-trackerprog §4.8, and worth 1,129 of its two builds' 22,549 ticks: `--poison clamp-edge`), `delta const` | GT2 `p_10AB` case 3: the 16-bit compare chain against `FREQ[freq_lo_idx]`, snapping in `p_1327` (gt2.md:798-801). JCH's slide is the same shape with the compare on its own target. **A snap is this row too**: GT2's parameter 0 is the clamp whose step reaches its target from either side, `delta $FFFF` |
| free slide | target freq, `policy halt` or `wrap` at width, `delta field(cell, mask)`, `phase bit(cell, 0)` | Hubbard: `d = voice[v].porta & $7E; freq += -d if porta & 1 else d` — a free ±step ramp with **no target**, so this row and not the portamento row (commando-floor:236-238). JCH slide acc (jch:82) |
| pulse sweep (bounce) | target pw, `policy reflect`, `bound [$8xx, $Exx] proved`, `rate` a divider, `phase cell != 0` | Hubbard: `pw += d` until `pw_hi == $E`, down until `$8`; `pwdir` the phase, `pwdelay` the divider, `ins.pw` the instrument-scoped value (commando-floor:222-233). JCH `rec6` segments, direction column `& $80` (jch.md:527) |
| pulse run (unbounded) | target pw, `delta const(k) + carry(site)`, `bound` **`projected`** at 12 bits | Hubbard: an **8-bit** add on `pw_lo` with the carry **live from the vibrato block** — `ins.pw_lo += ins.pspeed + C  # C inherited from $51FA` (commando-floor:222-224, `+ carry` at commando.md:394); the 12 bits come from the store's `& $F` (commando.md:380). defMON: `voice[v].pw_lo -= (b101E + (1 - carry_2))` with `carry_2` produced by the freq add above it (automatas.md:427-447), set on **9,144 of *Automatas*' 170,702 sweep steps** and on none of *Jazzpjazz*'s 129, so the row is two-family and it took the whole 149,025-tick horizon to say so — a 20,000-tick prefix reads 0 (defmon-trackerprog §7). These are the writes that make both Commando subtunes aperiodic (architecture §5.2), rendered exactly, aperiodicity included |
| filter sweep (**exercised**, sidwizard-trackerprog §5) | target `split(3, 8)` on cutoff, `delta tabcell(T[c], signed 11)`, `bound observed` | SW: the filter program's step byte is a signed 11-bit delta — `cutoff_lo = ((t3 & 7) + cutoff_lo) & 7` with the carry out, `cutoff_hi += (t3 >> 3) + carry`, the negative arm's shift arithmetic as `~(~t3 >> 3)` (sw.md:868-885, joined in `p_1611`). JCH `rec7` segments and defMON's `filter.acc` write the high half only, the same split with the low half pinned (jch.md:654, automatas.md:420) — and the split is the *chip's*, already `grid.PAIRS[6]`, not a family's |
| keyboard tracking (**exercised**, sidwizard-trackerprog §5) | `tabcell(T[c])` on the cutoff target | SW `CKBDTRK` (§3.7, sw:110-116); defMON's oscillator uses the same form on freq, `voice[v].acc += FREQ[$80 + (pw_hi[v] << 1)]` — the table being the *tuning*, so the object spells it `tuned(2·(osc & $3F) − 36)` rather than a `tabcell` over a stream, and the sign is `bit(cell, 6)`: bit 7 says whether there is a slide at all (defmon-trackerprog §8) |
| arpeggio / chord | target note, a `pitch` stream, or an absolute producer where the phase is stateless | Hubbard octave arp: `f = FREQ[note + ($C if counter & 1 else 0)]` — an **absolute `set` producer** (§4), `phase fn(global_counter)` (commando-floor:249-251). GT2 wavetable note column (gt2.md:564-569); SW chords |
| tremolo, LFOs (**exercised**, walker-trackerprog §5) | four copies of one triangle: `policy reflect`, the turn a **count** and not a bound; a one-shot is `delta_when` and not a policy; a gate tremolo is a stream and not an `Acc` | Walker: `mod1`/`mod2`/`mod3` per voice and the filter's copy on the global channel, `delta` the four bytes of RAM at `$AD73`, `rate` a countdown a note-on reloads. `mod1` and `mod3` sum into one frequency offset — both move it on 1,140 of the horizon's 9,949 modulator steps — so no bound on the cell is either's amplitude, which is what `amplitude.count` is for. The gate tremolo (`mod4`) moves the ctrl gate bit and not a volume: `$D418` is one global register, so there is no per-voice volume target, and per-pattern volume is `play`'s `vol` column (§3.6) on the one global nibble, last-writer |

**Piecewise envelopes** are not a row: they are streams of `acc` segments
(§3.3), the stream sequencing and the accumulator moving. Nothing else moves a
shadow between rows — that is the discipline. How a classifier *recognises*
these shapes in a certified program is the lift's, not the schema's (§6).

---

## 6. The lift, T0–T3

The lift emits a **scoreprog** (§1), not a trackerprog: the certified tick with
its fetch regions cut out and its score in their place as data, rendered by
`trackerprog/interp.py`, an S4 interpreter, with the sound half still the tick
outside the regions carried in `program` and run as code. Converging it onto
§4's object is backlog B6 and B7. It consumes the *certified* artefacts —
`tuneprog.S4.json`, `tuneprog.S6.json` (roles `freq_table`, `cursor`, `timer`,
`acc`, `sid_image`, `voice_map`; views; u16 pairs; the `index` relation),
`tuneprog.T0.json`, `certificate.json` — never the trace or the binary, so
family knowledge may steer extraction but can never reach the output.

| stage | out | mechanism |
| --- | --- | --- |
| **T0 channels** | `tuneprog.T0.json` | `provenance.document`: one record per SID write site — register, voices, the expression over named cells, its leaves, the site, the printed line |
| **T1 accumulators** | `tuneprog.T1.json` | a `state` cell whose update matches a §5 `delta` and whose guards, masks or history give a `bound` with its `from` tag, plus the exact per-tick recurrence `accstep.prove` verifies (`accum`, `accshape`, `accdelta`, `accrule`, `acchist`, `accreg`) |
| **T2 grammars** | `tuneprog.T2.json` | a `cursor`'s observed successor relation delimits its table's rows and loop row; the two-level cursor nest is the score; `freq_table` regions are `pitch` |
| **T3 emit + certify** | `scoreprog.{json,md,certificate.json}` | render on `interp.py` tick for tick against `Verifier.obs` over the whole certified horizon; any residue → `Refusal`, nothing emitted |

T2's materialisation rule: the object represents the score the trace played.
Storage idioms — Blackbird's LZ stream and ring buffers, packed rests, 1-based
columns, interleaving, Follin's `$85` byte lists — are dropped by materialising
the decoded rows over the horizon, which is `period` for a `complete` source
with `period > 1`, `first_repeat` for a `period = 1` source, and the certified
tick count for a `horizon` source. The note space is `0..N-1` where `N` is the
trace's reach, and there is no `clamp(note)` rule: a read past a **const**
table's declared size extends `pitch` with the values read, and a read landing
on a **play-written** cell is not a pitch entry at all but an absolute `set`
producer over `field(cell)`.

---

## 7. What landed

The git log is the record and [trackerprog-review.md](trackerprog-review.md) the
outcome table; this is the shape of the work.

| package | what it is | PRs | measurement |
| --- | --- | --- | --- |
| the enabling planes | the grid as a comparison, cell histories, the S6 exports, T0 provenance, T1 and its tick order | #291–#298 | 849 write sites, 849 prints re-rendering to their own line, 0 unnamed and unrefused; 16 accumulators, 0 replay divergences, 0 interval escapes |
| T2 and the lift from data | the score as a cursor nest; the fetch regions cut out and replayed as data | #299–#305 | JCH ×2, GT2 ×2, Commando ×2, SW ×2 certify from data; `jch-easy-does-it` refuses as a `sample stream` |
| the nine families, by hand | one universal player, no `meta.family` branch anywhere | #308–#325 | **thirty builds, 332,358 ticks, 0 divergences** |
| the schema hardened | the dead surface pruned, one row clock, one carry spelling, the bound asserted, the object compiled once | #313–#320 | 0 differing of 236,586 over the eleven builds then in the registry |
| the layer checked against itself | the poison harness — a stated mutation over a named build set, rendered both ways over each build's whole horizon — then the scoreprog named, the order program joined, §9 re-measured against the load band and the doc audited | #329–#333 | every horizon read from the certificate that records it, and both totals asserted against every number these documents quote |
| the review's ten items, and the one-family census | one dispatch, one act, one divider, one register naming, the compile finished, the instrument record, the spec; 300 forms counted by a script, 105 with one family, eight struck and the rest stated with their family and a poison | #335–#344 | 0 differing of 332,358 at every step; render 7,699 → 10,811 ticks/s over the nine families |

## 8. Refusals and boundaries

Fail-closed and diagnosed, in the tuneprog refusal style — an object with a
residue is not emitted:

| reason | when |
| --- | --- |
| `sample stream` | a CIA #2 NMI sample mixer or `$D418` nibble stream: digis are not a score |
| `external input` | see below |
| `unclassified update` | a state cell reaching a SID register whose update T1 cannot bound — the accumulator invariant is the claim |
| `score not cursor-shaped` | a pattern fetch T2 cannot express as the cursor grammar |
| `command residue` | a pattern command not expressible as §3.6's record, a register target with a non-literal index included |

**The `external input` rule.** A pinned input refuses only when *all three*
hold: its `tracedata.input_kind` is `raster`, `cia`, `sid_readback` or `io`; its
recorded values are not constant over the horizon; and T0's provenance shows it
reaching a SID write or a score cursor. `ack`, `entry_reg` and `uninit_ram` are
never external, which is why Commando's 11,780 pinned reads — one `entry_reg`
read per tick at `$5015` — do not refuse. Boundaries stated and not hidden:
cross-class intra-tick write order and the interleave between voices (§2's
`dropped` list), cycle positions inside a tick, and that a trackerprog is *a*
preimage, so round-tripping to a source *format* is a separate exporter.

## 9. Acceptance

| # | acceptance |
| --- | --- |
| 1 | `trackerprog.certificate.json`: 0 divergences over the whole certified horizon on the §2 observable, `compared` and `dropped` both populated, the loop claim re-verified where `end.kind = loop` |
| 2 | every refusal named with its cell — no partial emit |
| 3 | the print measured with **architecture §6.2's six numbers** — tokens, lines, statements, blocks, header rows, data rows — plus `xz -9e` of the object against the tune's own load band (§9.1) |
| 4 | recert untouched: 51/51, no tuneprog artefact moves |

Nine families are transliterated by hand onto §4's procedure and certified
against their tunes' players on the PcodeVM, with no branch on `meta.family`
anywhere in `trackerprog/` — **thirty builds, 332,358 ticks**, the registry
`tools/trackerprog_poison.py` reads its horizons from. Each carries the
inherited loop claim re-verified on the render where the source carries one, and
write lists identical or permuted rather than merely equal under §2.

| family | builds | ticks | the first of |
| --- | --- | --- | --- |
| [Hubbard](prototype-commando-trackerprog.md) | 3 subtunes | 35,340 | the effects-rich non-tracker, aperiodic observable; four producers on one `freq` |
| [GoatTracker 2](prototype-goattracker-trackerprog.md) | 2 builds | 16,895 | effect memory, and the wavetable `op` |
| [SID Wizard](prototype-sidwizard-trackerprog.md) | 2 builds | 22,549 | no shadow, so `commit_order` and the act sequence are observable |
| [defMON](prototype-defmon-trackerprog.md) | 2 builds | 150,824 | the general stream row; multispeed, closed; the first `--budget`/`--resume` |
| [JCH V20](prototype-jch-trackerprog.md) | 2 builds | 10,978 | `end.kind = fixed_point`, a song that *ends*; two builds disagreeing about having a shadow |
| [Follin](prototype-follin-trackerprog.md) | 3 named of 32 | 47,383 (111,763 over all 32) | the score as a program; a fetch that is a walk; no instrument table and no accumulator at all |
| [Blackbird](prototype-blackbird-trackerprog.md) | 1 | 10,426 | a score that does not exist until the player has decompressed it; the first that cost the player no line |
| [Walker](prototype-walker-trackerprog.md) | 1 | 8,052 | four modulators unrolled by modulator, two summing into one offset (`amplitude.count`) |
| [Galway](prototype-galway-trackerprog.md) | 14 subtunes | 29,911 | counted loops that **nest**; a `stop` that ends a voice's sequencer |

One exemplar remains: the T0–T3 lift that would produce these objects rather
than a hand reading of them. `tools/tuneprog_scoreprog.py` produces a scoreprog
(§1, §6), and eight of them certify `emitted: true` with no divergence over
their whole horizons; `tools/tuneprog_trackerprog.py` produces a **trackerprog**
as the *binding* of those planes to the player, with no `program` key and no
hints — Commando song 1 at 11,780 ticks and 0 divergences, and *Guldkorn Intro*
through the same emitter, whose certificate **names its first divergence** at
tick 0 and the field it wants rather than approximating past it — and refuses
the families whose tick is several procedures with a named datum
([prototype-lifter.md](prototype-lifter.md), backlog B6/B7).

### 9.1 The object against the load band

The program that played the tune is the binary, not `tuneprog.md` — a
pretty-printed decompilation, and a claim measured against a presentation
artefact is not measured. `tools/trackerprog_sizes.py` measures `xz -9e` of the
PSID load band, header stripped, against `xz -9e` of every certified subtune's
object concatenated, over the poison registry's own thirty builds:

| tune | songs | certified | band `xz` | object `xz` | ratio |
| --- | --- | --- | --- | --- | --- |
| *Je suis Linus* (GT2) | 1 | 1 | 2,804 | 6,116 | **2.18×** |
| *Do It Again* (GT2) | 1 | 1 | 2,668 | 5,756 | **2.16×** |
| *End of the World* (SW) | 1 | 1 | 3,992 | 7,648 | **1.92×** |
| *Guldkorn Intro* (JCH) | 1 | 1 | 2,472 | 4,696 | **1.90×** |
| *Automatas* (defMON) | 1 | 1 | 4,316 | 8,152 | **1.89×** |
| *Comic Bakery* (Galway) | 14 | **14** | 4,760 | 8,656 | **1.82×** |
| *Emomyst* (SW) | 1 | 1 | 3,576 | 6,136 | **1.72×** |
| *Knob at Night* (JCH) | 1 | 1 | 9,600 | 16,252 | **1.69×** |
| *Quintessence* (Blackbird) | 1 | 1 | 3,772 | 6,156 | **1.63×** |
| *Commando* (Hubbard) | 19 | 3 | 2,548 | 3,916 | 1.54× (3 of 19) |
| *Chameleon* (Walker) | 1 | 1 | 3,140 | 4,720 | **1.50×** |
| *Jazzpjazz* (defMON) | 1 | 1 | 2,944 | 3,672 | **1.25×** |
| *Ghouls'n'Ghosts* (Follin) | 32 | 3 | 10,888 | 8,528 | 0.78× (3 of 32) |

**The claim does not hold, and this is the finding.** Ten of the thirteen tunes
have one subtune, so the band holds exactly the music the object covers, and the
object is **1.25× to 2.18×** the binary on every one; Galway is the eleventh and
the only multi-subtune tune certified whole, at 1.82×. The one ratio below 1 is
a tune measured on a fraction of its subtunes against a band holding all of
them, which is not a comparison — Follin's three of thirty-two is 0.78× and its
three objects summed separately are already 1.35×.

**The score is not what makes the object large.** Materialised over the whole
horizon with every packed byte unpacked, every cursor spent and Blackbird's LZ
stream expanded 511,866 bytes wide, it still compresses to about what the whole
load band does — 3,208 against 2,804 for *Je suis Linus*, a band that holds the
player *and* the data — and it is 50 %, 51 % and 51 % of the object on *Je suis
Linus*, *Automatas* and *Quintessence*, where the other half (instruments,
streams, accumulators, `state0`, and the schema's own key names once per record)
doubles the total. ***Knob at Night*'s score share of 5 % is not that**: its
non-score half is 616,634 raw bytes of which **598,626 are one stream,
`wrapdata`** — the rip wrapper's own per-frame record, four bytes a frame over
8,577 frames, read through the `dptr` cursor the wrapper walks
(`trackerprog_jch.py:1179–1211`) — which is **12,636 of the object's 16,252 `xz`
bytes**, 78 % of the whole. That is tune data the wrapper carries, not the sound
vocabulary; with it taken out the half measures **2,936**.

So the layer trades size for the thing it exists to have: §6 drops every storage
idiom deliberately and the object carries no player where the band carries one.
**The honest claim is that the object is player-independent, not that it is
small.** The per-family `xz` figures in the nine transliteration documents were
measured against `tuneprog.md` and are a few per cent behind the objects the
tools build today; the table above is current and the tool regenerates it.

---

## 10. Open

| question | state |
| --- | --- |
| `sext` as a delta | `sext(k, T[c])` appears in the IR only as a jump offset. The one accumulator delta that sign-extends is SW's filter step, which lifts as `tabcell(T[c], signed 11)`. If an exemplar shows a sign-extended table entry that is *not* an absolute table cell, `delta` gains a form; until then it does not |
| global-scope accumulators beyond the filter | a survey question, not a schema one: `scope` is read from the value cell's region (§5) |
| the second entry | a tune whose NMI is a second *musical* entry (not a mixer) has two tick clocks; the schema has one cadence with per-voice dividers over it. Refuse until an exemplar demands otherwise |

Closed and dropped from this list: multispeed scaling (defMON's row clock is
`rate = 8` and nothing else knows — shortening the row by one clock step
diverges on 149,000 of 149,025 ticks), the SW orderlist fold, note-space
clamping (§6) and instrument-scoped accumulator sharing (`scope` is read off the
region, per cell).
