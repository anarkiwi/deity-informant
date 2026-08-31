# Prototype: GoatTracker 2 as a trackerprog — the second family, one player

A **hand transliteration** of the two certified GoatTracker 2 tuneprogs
([prototype-goattracker.md](prototype-goattracker.md), anatomy
[§3.3](playroutine-anatomy.md)) into trackerprogs
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)) and
certified against each tune's own player on the PcodeVM.

Three results:

1. **Both builds render, on one object shape and one code path.** 8,236 and
   8,659 ticks, **0 divergences** on §2's observable — and *stronger*: the write
   lists are **identical**, tick for tick, value for value, register for
   register. Not permutations. §2's dropped interleave does not exist for this
   family, because a ghost flush emits the whole image in one fixed order.
2. **The inherited loop claim re-verifies on the render.** Rendering past the
   first repeat, the next period is the previous period write for write: 6,720
   ticks for *Je suis Linus*, 8,640 for *Do It Again*.
3. **The layer invariant holds.** Hubbard and GoatTracker 2 lift to the same
   `Player`, with no branch on `meta.family`. Everything GT2 needs beyond
   Commando is a **form** (§4) — a shadow, a countdown clock, a stream with
   holds and an op, a global channel, a held command — and every form is stated
   as data that Commando simply does not carry.

Reproduce:

```
tools/trackerprog_goattracker.py $HVSC/MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid \
    --source out/recert-main/gt2-je-suis-linus/certificate.json \
    --certify --out out/gt2-tp/gt2-je-suis-linus
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 what the tuneprog could not settle.

---

## 1. The object

`tools/trackerprog_goattracker.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` renders it. The same seven sections:

| section | Je suis Linus | Do It Again |
| --- | --- | --- |
| `meta` | 3 voices in order 0,1,2; `commit_order (sr, ad, ctrl)`; a **shadow** of 25 registers flushed descending at the head of every tick; a **countdown** row clock; the fetch 2 ticks early | same, plus a live funktempo |
| `pitch` | `base 0` and **96** contiguous frequencies — the tune's whole tuning. No number outside 0..95 exists anywhere | 96, the same shape |
| `streams` | **8**: `wave` (100 rows), `pulse` (29), `filter` (43, global), `speed` (18 arm rows), plus `note_on`, `hard_restart`, `exit`, `funktempo` | 98 / 39 / 26 / 18 |
| `accs` | **9** declared forms — two vibrato Accs plus its delay, two free slides, a tone portamento and its degenerate snap, a pulse step, a cutoff step | the same nine |
| `instruments` | **30**, nine columns each | **20** |
| `score` | 3 order programs of `play(pattern, transpose)` ending `jump`; 33 patterns, 2,289 events, **43** distinct row commands the events name | 25 patterns, 1,315 events, 39 commands |
| `globals` | one channel: the filter stream, and the three registers it commits | same |

The player has one dispatch and it is on the *form* of a delta, a policy or a
stream row — never on the name of an effect. The nine accumulator ids
(`vib_delay`, `vib_phase`, `vibrato`, `porta_up`, `porta_down`, `toneporta`,
`toneporta_snap`, `pulse_step`, `cutoff_step`) are labels in the data.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/gt2-*/tuneprog.md`) and anatomy §3.3. Right column is the
object.

| the player says | the trackerprog says | §5 row |
| --- | --- | --- |
| `for r in 24..0: sid.reg[r] = ghost.reg[r]` | `meta.shadow {registers 25, order descending}`; every write goes to the shadow | new (§4.1) |
| `FREQ_LO[n]`/`FREQ_HI[n]`, 96 entries | `pitch(n)` — the tuning, total by construction | §3.2 |
| `(FREQ[n+1] - FREQ[n]) >> k`, the calculated speed | `tablestep(pitch, lastnote, k)` — an expression over the tuning | §5 `tablestep` |
| `DEC counter,X; BEQ tick0; BPL; reload tempo` | `meta.tempo` countdown: a cell, a reload cell, a boundary | new (§4.2) |
| `tempo < 2 ⇒ tempo ^= 1; funktempotbl[tempo] - 1` | `tempo.alternate`, a two-row stream over two global cells, each row carrying the `- 1` | §3.6, a tempo over a stream |
| `counter == gatetimer ⇒ fetch_row` | the fetch, `early` clock steps before the row (§4.3) | §3.5 `early` |
| the row's `instr`, `newfx`, `newparam`, `gate` | `meta.stage` — the row program the fetch runs early | new (§4.3) |
| `SR=0; AD=$0F; gate=$FE` unless legato or fx 3 | the instrument's **prelude**, and the held command's `tie` | §3.5 |
| the orderlist's `$E0+t` / `$FF pos` | `play(pattern, transpose)` / `end: jump(k)` | §3.6 |
| `$C0+n`, the packed rest | the event's `dur`, in **rows** | §3.6 |
| pattern `[instr][fx param](note\|rest\|keyoff\|keyon)` | `Event{sounds, note, gate, ins, arm}` — the note byte's token class is spent, not re-encoded (§4.8) | §3.6 |
| the 15 tick-0 handlers | §3.6 `cmds`, **named by what they do** — `tempo:07`, `stream.wave:04`, `sr:A4` — never by the nibble the jump table indexed them with; `score.commands` carries each once and `meta.row_command: held` says the voice keeps the last one (§4.4) | §3.6 `cmds` |
| `INS[i]`, 9 columns | `Ins{adsr, sets, note_sets, points, prelude}` | §3.5 |
| `wavetbl`/`notetbl` rows, `$00-$0F` delay, `$FF` jump | the `wave` stream: `hold`, `sets`, `op`, `jump` | §3.3 |
| a wavetable note column | the step's `op: pitch(absolute \| relative)`, the relative one a **signed semitone count** read off the column's low seven bits; the armed accs stand down | §3.3 `op` |
| `pulsetimetbl`/`pulsespdtbl` | the `pulse` stream: a `set` row, or `hold` ticks of `run(pulse_step)` | §3.3 |
| `filttimetbl`/`filtspdtbl`, global | the `filter` stream on `globals.streams`, and the three registers `globals.commit` | §3.7 |
| `speedtbl` left/right, double duty | the `speed` stream, unpacked into `delta`, `depth`, `cmp`, `zero` | §3.6 `arm(acc, overrides)` |
| `vibtime += 2`, `EOR #$FF` above `speedcmp` | `Acc(vibtime, const(+2), reflect-complement, amplitude [0, speed[param].cmp], bound observed [0, $FF])` | §5 vibrato |
| `LSR; BCC freqadd / BCS freqsub` | the freq Acc's `phase bit(vibtime, 0)` | §5 vibrato |
| `mt_effect_0`'s `vibdelay` countdown | `Acc(vibdelay, const(-1))`, guarded by the arm | §5 (a counter is a divider) |
| `fx 1/2`: `freq ± speed` | `porta_up` / `porta_down`, `phase const(0/1)` | §5 free slide |
| `fx 3`: the 16-bit compare chain and `p_1327` | `policy clamp(pitch[note])`, the vibrato phase reset through `meta.pitch_links` | §5 tone portamento |
| `fx 3` with speed index 0 | `policy take` — the degenerate clamp | new (§4.6) |
| `p_1327`'s `lastnote = abs; vibtime = 0` | taking a pitch of the tuning sets `lastnote` and `meta.pitch_links` | new (§4.6) |
| `mt_loadregs`: `ghost.ctrl = wave & gate` | a `{stream}` phase of `meta.tick`, run whatever the row did | new (§4.5) |
| `init`: zero blocks A+B, tempo/counter/instr | `meta.prologue`, applied on the tick the init call spends | new (§4.7) |

What disappears: the patched `JSR`/`JMP` low bytes (`$1289`, `$1295`, `$131E`,
`$1445`) and the two jump tables `T144A`/`T145A` — the command is its *number*,
and the table that turns a number into an address is compilation. So are the
`X = voice*7` double duty, the 1-based `base-1,Y` reads, the zero-page scratch
pair, `mt_execchn`'s fall-through tail call and the eleven SMC immediates. Not
one byte of the object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole
certified horizon, the reference being the tune's own player on
`deity_informant.PcodeVM`.

| tune | ins | patterns | events | tuning | streams | accs | ticks | SID writes | divergences |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Je suis Linus | 30 | 33 | 2,289 | 96 | 8 | 9 | 8,236 | 205,900 | **0** |
| Do It Again | 20 | 25 | 1,315 | 96 | 8 | 9 | 8,659 | 216,475 | **0** |

**Stronger than §2, and it holds outright.** `identical_ticks` equals the whole
horizon for both tunes: not a permutation, not a multiset — the same list.
`same_per_register_order` is true trivially, because the flush writes all 25
registers in one fixed order and nothing else writes the chip at all. Commando's
§3 had to measure and report 105 ticks where an intermediate write differs; GT2
has none, and the reason is structural rather than lucky: **a ghost flush is
exactly the thing §2's reduction was written to tolerate, so where a family has
one the reduction costs nothing.**

**The loop claim, re-verified.** The source certificate claims `complete` with
period 6,720 (8,640), first repeat at call 8,235 (8,658). The trackerprog
re-checks it on its *own* render: rendering `first_repeat + period` ticks, the
period after the first repeat equals the period before it, write for write.

| tune | period | first repeat | replay window | verified |
| --- | --- | --- | --- | --- |
| Je suis Linus | 6,720 | 8,235 | ticks 1,516..8,235 against 8,236..14,955 | **yes** |
| Do It Again | 8,640 | 8,658 | ticks 19..8,658 against 8,659..17,298 | **yes** |

The window starts one tick *after* the claimed first repeat, and the certificate
says so rather than hiding it: the claim names the call the state repeats after,
and a flush emits the image the call before it left, so the writes repeat one
tick later than the state does. Measured, not assumed — at the claimed offset
exactly one tick of 6,720 differs, and it is the boundary tick.

---

## 4. What the spec needed

Seven forms and one correction. None is a family branch, none is a new
mechanism, and every form is a datum Commando's object simply does not carry.
The correction (§4.8) is the opposite: a place where having two families made
one of them wrong.

### 4.1 A shadow register file, and its flush

`meta.shadow: {registers: 25, order: descending}`. Where a tune has one, no
write reaches the chip on the tick that made it: `emit` deposits into the
shadow, and the tick's *first* act is to emit the whole shadow in the stated
order. §2 already names GT2's flush as the reason cross-class order is dropped
(prototype-trackerprog:44); stating it as one datum makes the reduction
unnecessary for this family instead of merely tolerable. `commit_order (sr, ad,
ctrl)` follows from it — per voice the descending sweep passes offset 6, 5, 4 —
and §3.1's row for GoatTracker 2 is unchanged.

### 4.2 A row clock counting down, and a tempo over a stream

§3.6 already says tempo is "a divider or a tempo stream", per voice with a
global default. This family's counts **down**: a cell the tick decrements, a
boundary at zero, and the row's own length taken from a `tempo` cell when the
cell goes past it. Commando's counts down too, over a rate.

The object said that with a `meta.tempo.form` of its own — `countdown` against
Commando's `divider` and SID Wizard's `counter`, three names selecting three
procedures in the player — and §7's seventh package took the three back to one.
GT2's clock is now `step −1`, `boundary [rowclock == 0]`, and a `reset` clause
that reloads where the cell has gone past; Commando's is the same step with a
`rate` and no reset at all, because a divider's row length is the sequencer's to
reload. Nothing about this family's clock moved except where it is written.

`tempo.alternate` moved with it. §3.6's tempo over a stream was a record of its
own — a stream name and a guard, read by a `reload()` the player kept for it —
and it is **one more reset clause, ahead of the plain one**: where the tempo cell
says so, take the row's length from the funk stream and toggle the cell that
indexes it, and otherwise take the tempo cell. First match wins, which is what
the two arms always were. It is dead in *Je suis Linus* and fires 4,326 times in
*Do It Again*, on the same code; striking the clause and leaving the plain reload
diverges on **8,639 of *Do It Again*'s 8,659** ticks and 0 of *Je suis Linus*'.

The `- 1` is the row's, not the player's. The reload takes a *countdown* against
the boundary at 0, so a row of `n` clock steps counts `n - 1` down to it — which
is what the plain reload cell already holds, and what the funk row now holds too:
the player subtracted it for this one path and for no other value in the schema
(prototype-trackerprog.md §7). It is load-bearing and the measurement says so:
a funk row reloading its own length instead of its countdown diverges on **8,625
of *Do It Again*'s 8,659** ticks, from tick 20, and on 0 of *Je suis Linus*'.

### 4.3 The fetch runs ahead of the row it stages

GT2 reads a row `gatetimer` ticks before that row's tick 0, and what it reads
takes effect *then*, not at the row. Two data:

```jsonc
"tempo": { "early": 2 },              // the fetch's lead, in clock steps
"stage": [                            // what it commits when it reads: a row program
  { "ins": true },
  { "sets": [["@gate", {"payload": "gate"}]], "when": [["gate_stmt", "!=", 0]] },
  { "hold": true }
]
```

The rest of the event — the note, the note-on, the command — lands at the row.
This is §3.5's `early` given its second job: the prelude was always "`early`
ticks before the next row boundary", and the fetch that carries the prelude
carries the row's other early fields with it. The instrument the prelude belongs
to is the one the fetch just staged, which is what makes the legato class
(`instr >= FIRSTNOHRINSTR`) a property of the instrument's own `prelude: null`
and not a test in the player.

A consequence the player must state rather than infer: **a tune whose fetch runs
ahead has a lead-in row.** Nothing is staged before the first fetch, so the
first row boundary sounds nothing. That is not an off-by-one to paper over; it
is the pipeline the family is built on, and the object gets it by consuming what
was staged rather than by reading the score at the boundary.

### 4.4 A row command may be **held**

Every GT2 tick 0 runs a command, and the command is the last one the score gave
— a row without an effect byte inherits. So the voice holds a command, and the
score replaces it. One record, unpacked into named fields:

```
{ arms: [acc arms], links: [acc ids to reset], sets: [[target, value]],
  point: [[stream slot, row]], all: [[cell, value]], tie: bool }
```

The record has no name of its own either: `score.commands` is a dict and the key
is the name. `arms` is §3.6's `arm(acc_id, overrides)`; `point` re-points a
cursor *and* resets its hold, exactly as §3.6 says;
`links` is §3.6's `Cmd.links` (commands 1 and 2 zero the vibrato phase), which
is the one place the object reads a link; `all` is §3.6's global tempo — the one place a command writes every
voice's cell, which is a **write**, not a read, so the invariant that no
*expression* reads another voice's state is untouched. `tie` belongs to the
command because GT2's does: a tone portamento suppresses the hard restart and
the instrument load whether the row carried the effect byte or inherited it.

The score does not inline these: a command is an object it **names**, and
`score.commands` carries the tune's 43 (39) distinct ones once each. GT2 re-runs
the held command at every row, so inlining would have written the same record on
2,289 rows.

**What is named, and what is not.** A command is named by what it *does* —
`tempo:07`, `stream.wave:04`, `sr:A4`, `slide.down:0E` — never by the nibble
`T144A` indexes it with. §2 claims the two jump tables disappear; keeping their
index as the command's name would have been keeping them. And whether a command
outlives its row is a datum, `meta.row_command`, not a property of the clock:
`held` here, `spent` for Hubbard, read by the one procedure that applies a
command in either family. Before it, "countdown-clock families hold their
commands" was true only by accident of which sequencer branch ran.

`meta.rest_arm` is the arm a note row leaves before its command runs — GT2's
`fx = 0`, the instrument's own vibrato — so an instrument that arms nothing
directly still rests in something.

### 4.5 A stream step's `op`, and the voice's exit rows

§3.3 gives a step `sets`, an `op` and a `hold`, and the jump the row that
carries it carries, and Commando exercised none of it: its streams are one row of `set`s. GT2 exercises all of
it, and the rule the player keeps is one line:

> **a step's `sets` and `op` fire on the tick that consumes the step** — the
> last tick of its `hold` — **and a step that has an `op` is the voice's
> producer for that tick, so the accumulators the score armed do not run.**

That is the wavetable's note column: where it sets a frequency, the continuous
effect is skipped, which in the player is `goto done` and in the object is the
absence of a second producer. A step may also `run` an accumulator on *every*
tick it holds — the pulse and filter sweeps — which is the same rule with the
other timing, and both are needed because both occur.

A `{stream}` phase of `meta.tick` (§4.1) names a stream every voice path ends
on: GT2's
`mt_loadregs`, `ctrl = wave & gate`, on every tick including the ones a row
consumes.

### 4.6 `clamp(target)`, its degenerate case, and what taking a pitch does

§5's tone portamento row says `policy clamp(pitch[target])`, `delta const`,
`links [reset(vibrato phase)]`, and cites GT2 for it. Rendered:

```
toneporta   freq  w16  voice.freq        scope voice
  policy  clamp pitch
  delta   speed[param].delta
```

Two things it needed. First, **taking a pitch of the tuning is one named
operation**, not three assignments: it writes the frequency, records the note
sounded (`lastnote`, which `tablestep` reads) and resets the phases
`meta.pitch_links` names. The wavetable's note op and the portamento's snap are
the same operation, so it is stated once — and the reset is `meta.pitch_links`
and nothing else. The object also carried it a second time as an `Acc.links` on
this record, which is where §5 had put it; no reader ever read that field, not
the player and not the print, and the block above is what the print always
emitted. It is struck (prototype-trackerprog.md §5, §7): `links` is a §3.6
command's field — commands 1 and 2 below — and `meta.pitch_links`, and an `Acc`
has neither. Second, `speed index 0 = tie/instant`
is `policy take` — the degenerate clamp, a step that cannot fall short because
there is no step. It is a policy, not a special case in the clamp's arithmetic.

The clamp itself is the clean form — *snap where the step would reach or cross
the target* — and the machine's version carries a carry out of its own 16-bit
compare into the add. That carry is provably 0 for every speed this family can
hold (`speed <= $7FFF` forces the sign test and the carry apart), and the
certificate measures the simplification rather than asserting it: 0 divergences
over both horizons.

### 4.7 `meta.prologue`, and a value the object says is not there

GT2's `init` only *schedules*; the first `play` call flushes the image the file
carries, runs the reset, and spends its tick. `meta.prologue` is that reset as a
list of assignments; the tick emits the flush and nothing else. Commando's
object carries `init_writes` as data the player never runs, because Commando's
init happens before the horizon; GT2's is inside it.

And `{"trap": reason}` as an *expression*: a value the object states is not
there. The speed table is 1-based, so index 0 is its null — `waveptr == 0` means
"no stream", `param == 0` means "no speed". The row exists, carries `zero: 1`
(which the instrument vibrato's guard reads and which is a real fact) and traps
on `delta` and `depth`. Measured: over both horizons, index 0 is asked for
`zero` 21,135 and 14,340 times and for a step **never**. §4.13's `trap` one layer
down; the same discipline on a value rather than an arm.

### 4.8 The note column is a token class, and the layer spends it

§3.6's first draft wrote `Event.note: index | rest | hold | keyoff | keyon`, and
GT2's `$BD` rest / `$BE` keyoff / `$BF` keyon sit in exactly that byte range, so
the temptation is to take the enum literally. Comparing the two families says
not to.

SID Wizard's note column packs, in the same byte: `note $01–$5F`, `set vibrato
amplitude $60–$6F`, `packed rest $70–$77`, `porta $78`, `sync on/off $79/$7A`,
`ring on/off $7B/$7C`, `gate on $7D`, `gate off $7E` (anatomy:1204). Admitting
`keyoff` as a note *value* because GT2's byte sits in the note range admits
`sync on` as one because SW's does. And the anatomy already classes the whole
construct as a player idiom to be spent — "byte ranges as token classes …
tokenizer thresholds = the `CMP` immediates" (anatomy:2833) — next to `X =
voice*7` and the 1-based tables.

So the rule is §4.1's, applied to the score: **a value that is not in the pitch
table is not a pitch, so it is not a note.** Each token the byte packed becomes
its own field, and §3.6 now says so. The field the comparison actually forced is
`sounds`: before it, the player answered "does this row key a note?" from
`gate == "on"` for Hubbard and from `note is not None` for GT2 — two
computations of one fact, in one procedure, which is two grammars wearing one
player. It also left `note: none` ambiguous, meaning *rest* here and *sounds,
with the instrument's own pitch* in Commando (measured: all 25 of Commando's
`gate: on, note: none` rows are instruments 4 and 7, the drums of §4.3).

| the byte says | the event says | Hubbard | GoatTracker 2 | SID Wizard |
| --- | --- | --- | --- | --- |
| the row starts a sound | `sounds` | row bit 6 clear | a note byte `$60–$BC` | `$01–$5F` |
| its pitch | `note` | index, or none for a drum | index | index |
| a gate statement of its own | `gate` | — (its bit 6 *is* `sounds`) | `$BE` / `$BF` | `$7D` / `$7E` |
| rows the event spans | `dur` | 1 | `$C0+n` | `$70–$77` |
| re-target, do not re-trigger | `tie` | row bit 5 | effect 3 | — |
| everything else | `arm` / `cmds` | the porta byte | the fx nibble | `$60–$6F`, `$78–$7C` |

Hubbard's `gate` is `none` on every event — its row byte has one bit where GT2
has three tokens — and the ctrl mask a row leaves is `$FF` where it sounds and
`$FE` where it does not, overridden by an explicit `gate`. GT2 carries a `gate`
on 25 rows of 2,289 (39 of 1,315), and never on a row that also sounds, carries
an instrument, or carries a note: a keyoff is a row with nothing else on it.
`test_the_event_is_the_canonical_one` asserts that in both families.

---

## 5. What the spec got right

- **§2's observable is the right strength, and here it costs nothing.** The
  reduction exists because write order between register classes is an idiom;
  GT2's idiom is a fixed flush, so the two sides agree without any reduction at
  all. The rule was written from GT2's ghost flush and GT2 is the case where it
  is free.
- **`commit_order` is one datum per tune, not a player branch** — §3.1's row for
  GoatTracker 2, `(sr, ad, ctrl)`, unchanged.
- **the coupled vibrato pair** — a phase Acc `delta const(+2)`,
  `policy reflect-complement`, swinging against `speedcmp`; a freq Acc whose
  `phase` is bit 0 of the phase Acc's cell and whose `delta` is a `tablestep` or
  a constant. §5 wrote this row from GT2 and it held **exactly as written**,
  including that the speed byte's `& $7F` and *not* the depth is what the
  triangle swings against. What §5 called that interval was wrong and this
  object repeated it: `bound [0, speedcmp] proved` is where the phase *turns*,
  and the complement arm exists precisely to put the cell above it — 1,532 of
  *Je suis Linus*' 10,956 moves and 1,114 of *Do It Again*' 10,073 leave it,
  the first at tick 2 and tick 20. §5's own correction 1 said so in prose in
  2026; the record now says it as data, `amplitude` carrying the compare and
  `bound observed [0, $FF]` carrying the byte
  ([prototype-trackerprog.md](prototype-trackerprog.md) §5, §7).
- **tone portamento** — `clamp(pitch[note])`, §5's own row, whose reset of the
  vibrato phase is §5's `links` rule ("the constants the clamp action's own block
  stores into another Acc's cell") exactly. Where the object *carries* it moved:
  taking a pitch is one operation and `meta.pitch_links` is what it resets, so
  the record's own `links` column was a second spelling nothing read (§4.6).
- **free slide is `wrap`, not `reflect`** (§5's correction 5) — GT2's
  portamento up/down has no target, and the object says so.
- **`arm(acc_id, overrides)` rather than `Acc.param`** (§3.6's third change,
  written from GT2's vibrato) is exactly the shape needed: a speed row re-binds
  `delta`, `bound` and — the small widening §4.4 names — `when`.
- **a `point` re-points *and* resets the hold** (§3.6's
  sixth change): commands 8/9/A, verbatim.
- **`rate` has one meaning** — a divider. GT2's `vibdelay` is a counter, so §5's
  own rule ("a counter is not an accumulator") makes it a guard and a −1 step,
  not a second `rate`.

Two §5 delta forms remained unexercised by either family: `tabcell` on the
*cutoff* target (keyboard tracking, SW and defMON) and the sign-extended table
entry. `tabcell` itself is exercised here, on a stream's named column; the
cutoff one is exercised by the third family
([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md) §5),
which leaves the sign-extended entry alone.

---

## 6. Finding the data

The two tunes are the same `player.s` with different flags, so **no address is a
constant**: *Do It Again* loads at `$AC00`, carries author text, and has one
more jump-table entry, which moves every routine and every table. The tool
locates each datum by the *operand of the instruction that reads it*, found by a
wildcarded opcode pattern — 17 signatures, each matching exactly one site in
both builds — and then derives the rest from the layout `player.s` fixes:

- the ghost's base comes from the flush loop, and the five 7-field blocks A–E
  are the 105 bytes before it, so **one anchor gives all 26 per-voice cells**;
- the tuning follows the ghost; a parallel pair (lo/hi, left/right) gives its
  own length, because the two columns are adjacent and equal;
- four derivations are checked against a second anchor — the tuning follows the
  image, the nine instrument columns share one stride, the columns follow the
  pattern table, the wavetable follows the columns — and all four hold on both
  builds.

None of this reaches the object: it is how the bytes are found, not what they
mean. That the object *is* the tune's data and not a reading of it is checked
rather than asserted: `test_every_byte_of_the_tune_s_data_is_in_the_object`
reconstructs the wave, pulse, filter and speed tables, the nine instrument
columns and every pattern from the object and diffs them against the image, byte
for byte, on both builds. The one section that does not come back byte for byte
is the orderlist, deliberately: `play(pattern, transpose)` spends the `$Ex` TRANS
bytes, so it is compared as the steps it decodes to. *Je suis Linus* opens with
an explicit `F0` (transpose 0) and *Do It Again* has no TRANS byte at all — the
same order program either way, which is the point. The alternative — hard-coding one build's addresses, as Commando's tool
does for its single tune — would have made "the same code with no changes" false
for the second tune, which is the claim this exemplar exists to make.

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Je suis Linus | 2,865 | 18,267 | 2,823 | 7 | 42 | 2,823 | 5,512 |
| Do It Again | 1,816 | 11,853 | 1,782 | 7 | 34 | 1,782 | 5,180 |

`xz -9e` of the serialised object, §9's acceptance #3:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, Je suis Linus, compact | 180,184 | **5,608** |
| — its `score` half (orders, patterns, 43 commands) | 157,711 | 2,856 |
| — everything else (tuning, streams, accs, instruments) | 22,464 | 3,032 |
| `tuneprog.md`, the source print | 49,979 | 8,356 |
| the whole load band | 6,409 | 2,804 |
| `trackerprog.json`, Do It Again | 113,027 | **5,296** |
| — its `score` half | 93,611 | 2,676 |
| — everything else | 19,407 | 2,908 |
| its `tuneprog.md` / load band | 42,300 / 4,439 | 7,688 / 2,668 |

The layer's claim holds again, and by a wider margin than Commando's: the score
compresses better than the program that played it (5,608 against 8,356; 5,296
against 7,688). The score half alone lands at 2,856 against a 2,804-byte
compressed load band that contains the player *and* the data — so the music
alone, materialised with every packed byte unpacked and every cursor spent,
costs about what the whole cartridge does compressed.

The raw size is key repetition and one deliberate choice: the fifteen row
commands are **interned**. GT2 re-runs its held command at every row, so an
inlined command would print on 2,289 rows; the score names 43 distinct ones
instead and carries each once. That is the same move §3.6 makes with
`arm(acc_id, ...)` — a command is an object the score refers to — and it takes
the object from 402,928 raw and 5,916 compressed to 180,184 and 5,608, and the
print from 36,307 tokens to 18,267.

Code, against Commando's:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 801 (was 452) | §4 + §5, one procedure over the object |
| `deity_informant/trackerprog/printer.py` | 495 (was 328) | the flattened form and §6.2's numbers |
| `deity_informant/trackerprog/attest.py` | 81 (unchanged) | §2's comparison |
| `tools/trackerprog_goattracker.py` | 853 | the transliteration, the anchors and the PcodeVM reference |
| `tests/trackerprog/test_goattracker_oracle.py` | 240 | the two certificates, the loop, and the byte-for-byte round trip |
| `tests/trackerprog/test_universal_streams.py` | 279 | hermetic snippets, one per form of §4 |

The player grew by 349 lines to carry a second family and covers *no* tune: it
still has neither Commando nor GoatTracker in it. Against the floor, anatomy
§3.3.7's "player in ~30 lines" covers GT2 alone.

---

## 8. What the tuneprog could not settle

The printed `tuneprog.md` was the primary source and carried the whole program
and every table's bytes. Four facts it could not settle needed the disassembly,
and each is a gap worth closing one layer down. See
[trackerprog-backlog.md](trackerprog-backlog.md) for the tracked items.

| # | the fact | what the print said | ground truth | the generic fix |
| --- | --- | --- | --- | --- |
| 1 | a table's base and basedness | one array under several names with several derived origins — `T16F9[1 + t1]`, `T16F9[2 + r4]`, `T16F9[y]`; `T175D`/`T1761`; `T1875`/`T1876`/`T188A`; `T17FB`/`T17FC`; `T1826`/`T1839`; and headers like "2-based, read at `$16F7,i`" | `$129C LDA $16F8,Y` ⇒ base `$16F9`, 1-based | print **one** canonical `origin` and `basedness` per region and normalise every index expression to it, so a materialiser can read `T[y]` without recovering the operand |
| 2 | a carry live into an add | `a38 = ((T175D[y] + freq_lo_idx) + (T16F9[y] >= $E0)) & $7F` — the carry re-derived as a predicate over the reaching compare | `$12CD CMP #$E0 / $12CF BCS` leaves C = 0 on the fall-through | constant-fold a `carry(site)` term where the reaching compare proves it, and keep the named form only where it is live (§4.11's producer/consumer rule, applied to the print) |
| 3 | what an untaken arm does | `p_1082` printed from `# $108B` with `# untaken: T1851[y] >= 0`; the two instructions the arm holds (`LDY #$00 ; STY $FD`, which makes the vibrato depth 8-bit) are not printed at all | `$1087–$1089` | print an untaken arm's **body**, marked, rather than eliding it: the second build of the same player may take it, and a hand transliteration that must render both needs the semantics either way |
| 4 | which register a store is, and in what order | `ghost[x/7].pw_hi` / `.pw_lo` — correct, but the store order within one routine is what `commit_order` has to reproduce, and it is nowhere stated | `$134B STA $14CD,X` then `$134E..$1351 STA $14CC,X` | state the per-tune `commit_order` (§3.1's own datum) as a certificate field: the family's per-voice edge-register order is already recovered, and printing it would remove the last reason to open a disassembler |

A fifth, weaker one: the tick-0 and continuous dispatches print as a `switch`
over the *patched address* (`switch b1295: case $1006: ...`), which is the
compiled form. The command's **number** is the index into `T144A` that the same
block computes one line above, and printing the switch over that index would
make the arms comparable between builds — as it stands, the two builds' arms are
labelled with different addresses for the same command.
