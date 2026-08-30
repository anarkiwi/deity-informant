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
shows all nine playroutines are one object (STATE, TABLES, PLAY), and the six
certified families — [GoatTracker 2](prototype-goattracker.md), [SID
Wizard](prototype-sidwizard.md), [JCH V20](prototype-jch.md),
[Hubbard](prototype-commando-floor.md), [defMON](prototype-automatas.md),
[Follin](prototype-follin.md). Galway, Walker and Blackbird are **prose-only**:
no certificate covers them (architecture §9.2) and no schema row rests on them
alone. The survey fact that sizes the layer: 91.6 % of traced HVSC by weight has
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
| **trackerprog** | one data object: `{meta, pitch, streams, accs, instruments, score, globals}` — no code, no bytecode escape, no per-family construct |
| **universal player** | one fixed tick procedure (§4) shared by every trackerprog; the only executable in the layer |
| **certified-equivalent (T)** | for every tick of the source tuneprog's certified horizon, the universal player's observable (§2) equals the tuneprog's |
| **accumulator** | a bounded state machine `Acc(target, width, delta, bound, policy, rate, phase, links, scope)` (§5); the only per-tick mutation an effect may be |
| **stream** | a finite table of steps with holds and one terminator (§3.3); the only sequencing an instrument, a prelude or the score may be |
| **lift (T0–T3)** | certified tuneprog + S6 naming plane → trackerprog, fail-closed: what does not fit is a `Refusal(reason, cell)`, never an approximation |

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
`first_repeat`. A `horizon` tuneprog yields the horizon's score, `loop` null,
`end.kind = horizon`, `Order` ending in the `horizon` terminator (§3.6).

---

## 3. The schema

Serialised as tagged JSON in the S4 style (`ir.enc` vocabulary): `$trackerprog
$pitch $stream $acc $ins $pat $ord $cmd`, dicts as `{"$dict": [[k, v], …]}`.

### 3.1 meta

Cadence (`cycles_per_tick`, `source`), SID model, subtune, source tune and
family (provenance only), universal-player semantic version, and
**`commit_order`**: the permutation of `(ctrl, ad, sr)` the commit emits per
voice (§4) — one datum per tune, lifted like the pitch table, not a player
branch. Four certified families take three of the six values, one of them two
across its versions:

| source | `commit_order` | evidence |
| --- | --- | --- |
| JCH V20 | `(ad, sr, ctrl)` | `p_1616` writes `ad`, `sr`, `ctrl` in that order — jch:178-180, jch.md:656-658 |
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
row's `jump` is the whole of it.

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
frames — jch.md:527-538) and `rec7` filter, **3** columns `[init, Δ, frames]`
(jch.md:544-546), counts at jch:82 and jch:106-107; SW tempo programs; defMON
sidTAB rows (variable-length register-column records with delay and jump,
anatomy:211 — the form at its most general); and the prose-only Galway, Walker
and Blackbird programs. The stream is what all of these already are: *rows,
holds, one terminator*.

### 3.4 accumulators

Declared once in `accs`, referenced by streams, instruments, preludes and
commands. The full object is §5.

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
| JCH V20 | `early = 2`; row `set(ad,$0F) set(sr,$00) set(ctrl, mask $FE)`, note row `set(ctrl,$09)` | jch.md:501-511, jch:150-158 |
| GoatTracker 2 | `early = gatetimer` (instrument column 7); row `set(ctrl, wave & $FE)`, note row `set(ctrl, firstwave\|TEST)` | anatomy:214, anatomy:742 |
| SID Wizard | `early = 2`; rows in the version's own AD/SR order, then `set(ctrl, TEST\|gate)` at tick 2 | anatomy:1232-1233 |
| defMON | a sidTAB row program: `WG=00 AD=0F SR=00` → `WG=09` → sound | anatomy:214 |
| Blackbird (prose-only) | `early = 2`; `set(sr,0) set(ctrl, gate off)`, note row ADSR=0000 then the real AD/SR | anatomy:133-135 |
| Hubbard, Galway, Follin | `null` — Hubbard cuts notes with SR=0, Galway pulses TEST at note-on | anatomy:137-140 |

GT2's `gatetimer` **is** `early`, not a second field ("gatetimer frames early,
firstwave with TEST", anatomy:214), so the first draft's `gate.timer` row is
deleted — single-family only because it duplicated `early`. The nine-family
"sound definition" row (anatomy:211) reduces here: Hubbard's 8-byte SID image +
fx bits = `adsr` + armed `accs`; GT2's 9 columns + pointers = `adsr`, `prelude`,
four stream refs; defMON's "the sidTAB row *is* the instrument" = an instrument
that is only `streams`.

### 3.6 score

```
Pattern = rows of Event
Event   = { sounds: bool            // the row starts a sound -- the one field that says so
          , note: index | none      // its pitch; none = the instrument's own (§3.5)
          , gate: on | off | none   // the row's own gate statement, where a family has one
          , tie:  bool              // re-target without re-triggering
          , ins:  instrument | none
          , cmds: [Cmd, …]          // in row order; §4 emits them in that order
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
          | jump(row) | stop | horizon }
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

`for(n){…}`, `call(seq)` and `ret` are struck with them. They rest on Galway
and Follin, which are prose-only and deferred (§9), and §1's rule is that a
schema row carries two certified families or one plus a survey count. The three
certified orderlists are `play` steps and one of `jump`, `stop` or `horizon`;
when a score-as-program exemplar lands, the grammar gains what that exemplar
shows and no more.

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
`tie`, `cmds` — and the note column holds a pitch or nothing:

| the byte says | the event says | Hubbard | GoatTracker 2 | SID Wizard |
| --- | --- | --- | --- | --- |
| the row starts a sound | `sounds` | row bit 6 clear | a note byte `$60–$BC` | `$01–$5F` |
| its pitch | `note` | index, or none for a drum | index | index |
| a gate statement of its own | `gate` | — (its bit 6 *is* `sounds`) | `$BE` / `$BF` | `$7D` / `$7E` |
| rows the event spans | `dur` | 1 | `$C0+n` | `$70–$77` |
| re-target, do not re-trigger | `tie` | row bit 5 | effect 3 | effect 3, or `$3F` in the instrument column |
| everything else | `cmds` / `arm` | the porta byte | the fx nibble | `$60–$77`, `$78–$7C`, and both effect columns |

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

`sounds` is the field §4's tick reads to decide whether a row keys a note, and
it is the *only* one: an object that answered it from `gate` in one family and
from `note` in another would be two grammars. `note: none` then means one thing
everywhere — no pitch of the row's own — and where such a row sounds, §3.5's
instrument supplies the frequency (Hubbard's drums; commando-trackerprog §4.3).
The ctrl mask a row leaves is `$FF` where it sounds and `$FE` where it does not,
overridden by an explicit `gate`.

Five changes from the first draft, each forced by a source (a sixth, `set` for a
shadow against `set_register` for the chip, is struck with the opcode list: both
are a `sets` entry, and *when* it reaches the chip is the target's own — a
shadow register defers, a producer does not):

| change | why, and the evidence |
| --- | --- |
| `play` gains `vol?`, `tempo?` | SW's orderlist columns are pattern, transpose, **volume, tempo**, stop, loop; GT2's pattern, repeat, transpose, loop; JCH's `[transpose] pattern` (all anatomy:209). Optional, `none` where a family has no column. `vol` lands on the one global `$D418` nibble (sw:109), so three voices' columns resolve by last-writer, which §2 makes exact |
| a `horizon` terminator | a source materialised only as far as the certified ticks reach, distinct from `stop` (Hubbard's `$FE`, SW's stop — anatomy:209) and from `jump`. The same fact as `end.kind = horizon`, stated twice |
| `arm(acc_id, overrides)` replaces `arm(acc_id, param)` | `Acc` has no `param` and should not: GT2's vibrato parameter selects a bound *and* a step (`b1096 = T1851[y] & $7F` is speedcmp, gt2.md:812; `T1863[y]` the depth or shift, gt2.md:653-684), so the command re-binds a subset of `{delta, bound, rate, phase}` on a declared `Acc` |
| a command's register target is a literal `0..24` | Follin's `$85` lists write `$D400+r` for an arbitrary register of any voice (anatomy:1803; `sid.reg[a75] = …`, follin:160-167) and resolve, because T2 materialises decoded score bytes exactly as it materialises pattern rows. Where the index does not resolve, the refusal is `command residue` (§8) — the 36 `index not a voice` sites T0's sweep already names one layer down (backlog §4, W4) |
| `point(slot, row, keep)` | GT2 commands 8/9/A re-point the wave, pulse and filter tables and zero the matching hold (`waveptr=A (wavetime=0)`, anatomy:876) — a re-point plus a link (§5), not two opcodes. It is a field of a §3.3 step, and a command's writes *are* a §3.3 stream, so there is one shape and one guard |

**The row clock is a divider, a countdown or a counter.** `meta.tempo.form` says
which: Hubbard's `divider` (a rate and a phase), GoatTracker 2's `countdown` (a
cell the tick decrements against a `boundary` and a `reload`), SID Wizard's
`counter` — a cell the tick *increments*, with guarded `reset` clauses that say
where the row ends and what the tempo program does next, `boundary` naming the
tick the row sounds and `fetch` the tick it is read. A counter's steps are the
family's phases and the object exposes them as one virtual cell, `phase`, that
any guard may read (sidwizard-trackerprog §4.1).

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
owner voice, JCH's "filter runs on track 0" — is last-writer over the global
channel, which the observable makes exact without an ownership construct.
Keyboard tracking (SW `CKBDTRK`) is **not** an `interval` term: it adds an
*absolute* table entry, `a11 = FREQ[$E + (freq_idx + b1024[$2C + b1024_idx])]`
then `cutoff_hi = (a11 + cutoff_hi) + c6` (sw:110-116), where `interval` is a
difference of adjacent *tuning* entries. It is §5's `tabcell(T[c])` delta on the cutoff
target — the same construct defMON's oscillator uses (automatas.md:433-437), so
it earns its row on two families and needs none of its own.

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
| `fetch` | read the row the clock runs ahead of, and stage what it commits early (§3.5) |
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
             | Δ + carry(site, flag)                  # any of the above, plus a live carry
      , bound  : { interval: [lo, hi], from: proved | projected | observed
                 , witness: <guard | mask | period> }
      , policy : wrap | reflect | reflect-complement | clamp(v) | halt | reload(v)
      , rate   : every k ticks (k ≥ 1)            # the §3.3 divider, one meaning
      , phase  : bit(self, k) | bit(cell, k) | cell != 0 | fn(global_counter) | acc(id)
      , links  : [ reset(acc_id | stream_slot), … ]   # what this Acc's events zero
      , cell   : the value's own, in one vocabulary -- `tick`, a voice cell,
                 `#global`, `ins.pw`, `shadow.<pair>`, any of them with `.hi`/`.lo`
      , scope  : read from the value cell's region index domain }
```

**Bounded** is the invariant, not a hint: `bound × policy` makes the reachable
value set finite and statically known; the trackerprog states each interval and
the renderer asserts it — the tuneprog's envelope discipline one layer up. The
three `bound.from` tags differ:

| `from` | source of the interval | evidence |
| --- | --- | --- |
| `proved` | a guard on the update path | GT2 `if b14A0 < b1096` against the speedcmp cell (gt2.md:812,819); Hubbard `if ins.pw_hi == $E` … `== $8` (commando-floor:230-233) |
| `projected` | the write's own mask — the interval the chip can see | Hubbard's pw is 12-bit only because the store is `(pw_hi + carry) & $F` (commando.md:380, commando-floor:325); SW's `cutoff_lo & 7` (sw.md:873,881); `grid.PW_HI` is the same projection on the observable side |
| `observed` | `history.py` over the certified horizon, under the period witness | JCH's pulse and filter segments have no guard and no mask: `voice[x].pw += rec6[…].b1894` for `timer_4` frames (jch.md:527-538), `cutoff_hi += rec7[…].b1860` for `timer_5` (jch.md:544-546). The bound is the register width; the *stream* ends the segment, not a compare |

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
(anatomy:876). Second family: JCH's re-trigger arm re-points the pulse cursor
and reloads the pw accumulator from the stream row in one step (jch.md:363-366).

Every per-frame modulation in the anatomy's row (anatomy:212) lands on one line,
each with two certified families or a marked single-family exception:

| effect | Acc | evidence |
| --- | --- | --- |
| vibrato (triangle) | **two coupled Accs**: a phase Acc `delta const(+2)`, `bound [0, speedcmp] proved`, `policy reflect-complement`; and a freq Acc whose `phase` is `acc(phase_id)` bit 0 and whose `delta` is a shifted `interval` or a `const` | GT2: `voice[x/7].b14A0 = (a + 2) + c`, `t4 = b14A0 & 1`, then `ghost.freq += ptr` or `-= ptr` (gt2.md:852-862); the bound is the SMC cell `b1096 = T1851[y] & $7F` (gt2.md:812 — speedcmp, **not** the depth) and the complement is `a57 = ~b14A0` (gt2.md:835); `ptr` is either the 16-bit const `(T1851[y] << 8) \| T1863[y]` or `interval(freq_lo_idx_2) >> T1863[y]` through the variable-shift loop `p_12E5` (gt2.md:653-684). JCH: the same two-cell shape on its slide/vibrato (jch:82) |
| vibrato, stateless phase | one freq producer, `delta repeat(interval >> (ins.vib + 1), n)`, `phase fn(global_counter)` | **single-family exception (Hubbard)**: `phase = counter & 7; if phase >= 4: phase ^= 7`, then `for _ in 0..phase-1: f += step` (commando-floor:215-221). It is the closed form of the triangle every other family accumulates, not a new mechanism; admitted because Hubbard is §9's certified non-tracker exemplar and nothing else makes its `freq` exact. T1 reads the `phase` off the counter that decides the count (`accrule.fn_phase`) and verifies the producer against the register, not the cell (#298) |
| tone portamento | target freq, `policy clamp(pitch[target])` with an `edge` (where the step that lands exactly on the target either reaches it or does not — sidwizard-trackerprog §4.8), `delta const`, `links [reset(vibrato phase)]` | GT2 `p_10AB` case 3: the 16-bit compare chain against `FREQ[freq_lo_idx]`, snapping in `p_1327` (gt2.md:798-801). JCH's slide is the same shape with the compare on its own target |
| free slide | target freq, `policy halt` or `wrap` at width, `delta field(cell, mask)`, `phase bit(cell, 0)` | Hubbard: `d = voice[v].porta & $7E; freq += -d if porta & 1 else d` — a free ±step ramp with **no target**, so this row and not the portamento row (commando-floor:236-238). JCH slide acc (jch:82) |
| pulse sweep (bounce) | target pw, `policy reflect`, `bound [$8xx, $Exx] proved`, `rate` a divider, `phase cell != 0` | Hubbard: `pw += d` until `pw_hi == $E`, down until `$8`; `pwdir` the phase, `pwdelay` the divider, `ins.pw` the instrument-scoped value (commando-floor:222-233). JCH `rec6` segments, direction column `& $80` (jch.md:527) |
| pulse run (unbounded) | target pw, `delta const(k) + carry(site)`, `bound` **`projected`** at 12 bits | Hubbard: an **8-bit** add on `pw_lo` with the carry **live from the vibrato block** — `ins.pw_lo += ins.pspeed + C  # C inherited from $51FA` (commando-floor:222-224, `+ carry` at commando.md:394); the 12 bits come from the store's `& $F` (commando.md:380). defMON: `voice[v].pw_lo -= (b101E + (1 - carry_2))` with `carry_2` produced by the freq add above it (automatas.md:427-447). These are the writes that make both Commando subtunes aperiodic (architecture §5.2), rendered exactly, aperiodicity included |
| filter sweep (**exercised**, sidwizard-trackerprog §5) | target `split(3, 8)` on cutoff, `delta tabcell(T[c], signed 11)`, `bound observed` | SW: the filter program's step byte is a signed 11-bit delta — `cutoff_lo = ((t3 & 7) + cutoff_lo) & 7` with the carry out, `cutoff_hi += (t3 >> 3) + carry`, the negative arm's shift arithmetic as `~(~t3 >> 3)` (sw.md:868-885, joined in `p_1611`). JCH `rec7` segments and defMON's `filter.acc` write the high half only, the same split with the low half pinned (jch.md:654, automatas.md:420) — and the split is the *chip's*, already `grid.PAIRS[6]`, not a family's |
| keyboard tracking (**exercised**, sidwizard-trackerprog §5) | `tabcell(T[c])` on the cutoff target | SW `CKBDTRK` (§3.7, sw:110-116); defMON's oscillator uses the same form on freq, `voice[v].acc += FREQ[$80 + (pw_hi[v] << 1)]` with the sign from `bit(cell, 7)` (automatas.md:433-437) |
| arpeggio / chord | target note, a `pitch` stream, or an absolute producer where the phase is stateless | Hubbard octave arp: `f = FREQ[note + ($C if counter & 1 else 0)]` — an **absolute `set` producer** (§4), `phase fn(global_counter)` (commando-floor:249-251). GT2 wavetable note column (gt2.md:564-569); SW chords |
| tremolo, LFOs | target **gate-mask**, `policy reflect` (triangle) or `halt` (one-shot), or a stream | Walker's gate-toggle tremolo and its four identical modulators per voice (anatomy:212) move the ctrl gate bit, not a volume. `$D418` is one global register, so `target vol, scope voice` does not exist and is removed; per-pattern volume is `play`'s `vol` column (§3.6) on the one global nibble,
last-writer. Prose-only family, so both are projections |

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
| **`+ carry(site, flag)` is a *named* flag** | a carry another block of the tick leaves is an SSA flag with no expression of its own, so the record names its site and the flag; a carry the step computes in place is part of its own arithmetic and stays there | Hubbard `rec2[…].b5591 = ((… + b5507) + carry)` at `$5237`, the bit live from the vibrato block `$5208` (`C#41`); defMON `voice[v].pw_lo -= (b101E + (1 - carry_2))` |
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
| **T3 emit + certify** | all | `trackerprog.json`, `trackerprog.md`, `trackerprog.certificate.json` | render on the universal player tick-for-tick against `Verifier.obs` over the whole certified horizon, §2 observable; any residue → `Refusal`, nothing emitted |

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
| ~~lanes, deltas, `tablestep`, cycles, the period with a loop (**#304**)~~ — superseded, it encoded the observable | `emit.lift` encodes levels as deltas and vibrato as `freq_ts(m, shift)`, folds runs and short cycles into one step, splits a row's sound into lanes (edge registers apart under one `commit_order`), shares a stream between a note and a shorter one, keys patterns on note offsets with the transpose in the order, and materialises a complete source over its period with the loop's `enter` levels; `player.py` steps cycles and lanes in commit order. Four of eight prints below the source's `xz` | `trackerprog.emit`, `player` |
| ~~rows as streams, six tunes certified (**#302**)~~ — superseded, it encoded the observable | `emit.lift` reads T2's rows and the observable: per row a stream of steps with holds (the voice's ordered edges, `note_off`/`freq`/`pw` sets), deduplicated, the row's instrument the stream it arms; the global channel one stream; a second schedule entry refuses as `sample stream`. JCH ×2, GT2 ×2, Commando ×2 render at 0 divergences | `trackerprog.emit`, `player` |
| the universal player, the certificate and the print (**#300**) | `trackerprog/player.py` is §4 tick for tick (row clock, sequencer step, armed accumulators, `commit` in `meta.commit_order`, `grid.reduce_tick`); `certify.py` is §2's comparison over the whole horizon with `compared`, `dropped`, `refusals`, `emitted`, the loop claim re-checked on the render; `emit.py` lifts T0's sites that are a constant or a pitch lookup at the row boundary or every tick and refuses the rest as `command residue`, prints `trackerprog.md` and measures §6.2's six plus `xz -9e`; `tools/tuneprog_trackerprog.py`. The hermetic tune renders at 0 divergences; GT2, JCH and Commando carry named residue (backlog §4, W8) | `trackerprog.player`, `certify`, `emit` |

| one grammar, audited across three families (**#310**) | the three hand exemplars read together against §3: `meta.commit` struck (the tick is always a sequence of acts, and rendering it so for the families that do not need it is write-for-write identical over their whole horizons); `meta.row` replaces `note_row`, `gate_row`, `pitch_row`, `row_sets`, `row_commits` and merges `latch`/`row` into one `apply_row`; `Ins.on_note` replaces `sets`/`note_sets`/`points`; `meta.tick` replaces `tempo.early_first`, `meta.voice_exit` and the commit's `pre` list; `interval(n)` replaces `tablestep`; one cell vocabulary for `Acc.cell`, retiring `voice.freq*` and the `@`-means-two-things collision. a command's writes become an inline stream, so a guard has one spelling and never a positional slot; §3.3's terminator, §3.6's nine-command opcode list, `for`/`call`/`ret` and §3.5's stream-slot map are struck as grammar no exemplar carries. Measured: the union of `meta` keys across the three families 26 → 21, the keys the player *branches* on 15 → 10, two row procedures → one, three mechanisms for "run a stream at a point in the tick" → one, two guard spellings → one; `universal.py` 995 → 1,009 lines, which is the price of the generality and is paid once rather than per family. 62 HVSC oracle tests unchanged: Hubbard ×3, GoatTracker 2 ×2 and SID Wizard ×2 at 0 divergences over their whole horizons | `universal`, `printer`, the three `tools/trackerprog_*.py` |

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
non-tracker, aperiodic observable), then **Follin** (score-as-program).

defMON belongs in the list, not in the deferred set: certified twice over —
`automatas` (149,025 ticks, period 129,024, `complete`) and `goto80-jazzpjazz`
(1,799 ticks, `horizon`), architecture §9.2 — with its own prototype document,
and the evidence §3.3, §3.5 and §5 lean on for the general stream form, the
data-side prelude and the second family for `carry(site)`. Two costs:
`automatas` needs `--budget`/`--resume` like every long tool (architecture §11),
and `goto80-jazzpjazz` being `horizon` exercises that terminator, not the loop
claim. Per exemplar:

| # | acceptance |
| --- | --- |
| 1 | `trackerprog.certificate.json`: 0 divergences over the whole certified horizon on the §2 observable, `compared` and `dropped` both populated, the loop claim re-verified where `end.kind = loop` |
| 2 | every refusal named with its cell — no partial emit |
| 3 | the print measured with **§6.2's six numbers** — tokens, lines, statements, blocks, header rows, data rows, which architecture §11 requires verbatim of every presentation change — plus **one extra**, `xz -9e` of `trackerprog.md` against the source `tuneprog.md`'s. `xz` is §8.3's own unit and no substitute for the six. The layer's claim is that the score compresses *better* than the program that played it |
| 4 | recert untouched: 51/51, no tuneprog artefact moves |

State after t3-from-data: JCH ×2, GT2 ×2, SID Wizard ×2 and Commando ×2
certify `emitted: true` with no divergence over their whole horizons, lifted
from their programs' data alone — the score as recorded fetches replayed with
the score tables never read, the instruments as the program's own table (30,
19, 13, 11), T1's accumulators and T2's streams named; `jch-easy-does-it`
refuses as a `sample stream`. Six of eight prints are below the source's
`xz` (Hubbard's per-row SID writes keep his two above). The sound half is still
the certified tick outside the fetch regions, carried as the program and run by
the interpreter, not §4's fixed procedure over instruments, streams and
accumulators — that reduction is backlog W11, and the exact replay is what it
must be proved against.

**State of the hand exemplars.** Three families are transliterated by hand onto
§4's own procedure and certified against their tunes' players on the PcodeVM,
with no branch on `meta.family` anywhere in `trackerprog/`: Hubbard ×3 subtunes
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ×2 builds
([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md))
and SID Wizard ×2 builds
([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)), the
last two with the inherited loop claim re-verified on the render and the write
lists identical rather than permuted. Seven exemplars remain: JCH ×2, defMON ×2,
Follin, and the T0–T3 lift that would produce these objects rather than a hand
reading of them.

The genericity gate: the six tracker exemplars must lift with zero
family-conditioned code in `trackerprog/` — the same modules, hermetic snippet
tests per mechanism, each schema row's two-family evidence recorded here. The
two single-family rows (§5's stateless-phase vibrato, §3.6's Follin register
target) are data forms, not code branches.

## 10. Open

| question | state |
| --- | --- |
| multispeed scaling | `rate` is now one thing, a divider (§3.3), so a sequencer running at frame rate under an n× entry is `rate = n` on that voice's tempo. Still to be *measured* on a used multispeed entry; SW 1.9 carries an unused one |
| `sext` as a delta | `sext(k, T[c])` appears in the IR only as a jump offset (`switch ($1953 + sext(T1934[a]))`, sw.md:1205). The one accumulator delta that sign-extends is SW's filter step, and it lifts as `tabcell(T[c], signed 11)` (§5). If an exemplar shows a sign-extended table entry that is *not* an absolute table cell, `delta` gains a form; until then it does not |
| global-scope accumulators beyond the filter | a survey question, not a schema one: `scope` is read from the value cell's region (§5) |
| the second entry | a tune whose NMI is a second *musical* entry (not a mixer) has two tick clocks; the schema has one cadence with per-voice dividers over it. Refuse until an exemplar demands otherwise |
| ~~the SW orderlist fold~~ | closed by #303: the load was never folded away, the print dropped a return of one value and its flags (`ir.retexpr`); SID Wizard ×2 certify |

Settled since the first draft and dropped from this list: note-space clamping
(§6 — the note space is the trace's reach, and Commando's overrun is a producer,
not a pitch entry) and instrument-scoped accumulator sharing (§5 — `scope` is
read off the region, per cell).
