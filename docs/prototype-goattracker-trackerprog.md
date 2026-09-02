# Prototype: GoatTracker 2 as a trackerprog — the second family, one player

A **hand transliteration** of the two certified GoatTracker 2 tuneprogs
([prototype-goattracker.md](prototype-goattracker.md), anatomy
[§3.3](playroutine-anatomy.md)) into trackerprogs
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)) and
certified against each tune's own player on the PcodeVM.

Three results. **Both builds render on one object shape and one code path**:
8,236 and 8,659 ticks, **0 divergences** on §2's observable, and the write lists
are **identical** tick for tick, value for value, register for register — §2's
dropped interleave does not exist for a family whose ghost flush emits the whole
image in one fixed order. **The inherited loop claim re-verifies on the
render.** **The layer invariant holds**: Hubbard and GoatTracker 2 lift to the
same `Player` with no branch on `meta.family`, and everything GT2 needs beyond
Commando is a **form** (§4) stated as data.

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
`deity_informant/trackerprog/universal.py` renders it.

| section | Je suis Linus | Do It Again |
| --- | --- | --- |
| `meta` | 3 voices in order 0,1,2; `commit_order (sr, ad, ctrl)`; a **shadow** of 25 registers flushed descending at the head of every tick; a **countdown** row clock; the fetch 2 ticks early | same, plus a live funktempo |
| `pitch` | `base 0` and **96** contiguous frequencies — the tune's whole tuning. No number outside 0..95 exists anywhere | 96, the same shape |
| `streams` | **8**: `wave` (100 rows), `pulse` (29), `filter` (43, global), `speed` (18 arm rows), plus `note_on`, `hard_restart`, `exit`, `funktempo` | 98 / 39 / 26 / 18 |
| `accs` | **9** declared forms — two vibrato Accs plus its delay, two free slides, a tone portamento and its degenerate snap, a pulse step, a cutoff step | the same nine |
| `instruments` | **30**, nine columns each | **20** |
| `score` | 3 order programs of `play(pattern, transpose)` ending `jump`; 33 patterns, 2,289 events, **43** distinct row commands the events name | 25 patterns, 1,315 events, 39 commands |
| `globals` | one channel: the filter stream, and the three registers it commits | same |

The nine accumulator ids (`vib_delay`, `vib_phase`, `vibrato`, `porta_up`,
`porta_down`, `toneporta`, `toneporta_snap`, `pulse_step`, `cutoff_step`) are
labels in the data.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/gt2-*/tuneprog.md`) and anatomy §3.3.

| the player says | the trackerprog says | §5 row |
| --- | --- | --- |
| `for r in 24..0: sid.reg[r] = ghost.reg[r]` | `meta.shadow.registers`, descending; every write goes to the shadow | new (§4.1) |
| `FREQ_LO[n]`/`FREQ_HI[n]`, 96 entries | `pitch(n)` — the tuning, total by construction | §3.2 |
| `(FREQ[n+1] - FREQ[n]) >> k`, the calculated speed | `interval(lastnote) >> k` | §3.2 |
| `DEC counter,X; BEQ tick0; BPL; reload tempo` | `meta.tempo` countdown: a cell, a reload cell, a boundary | new (§4.2) |
| `tempo < 2 ⇒ tempo ^= 1; funktempotbl[tempo] - 1` | one more `meta.tempo.reset` clause, ahead of the plain one | §3.6 |
| `counter == gatetimer ⇒ fetch_row` | the fetch, `early` clock steps before the row (§4.3) | §3.5 `early` |
| the row's `instr`, `newfx`, `newparam`, `gate` | `meta.stage` — the row program the fetch runs early | new (§4.3) |
| `SR=0; AD=$0F; gate=$FE` unless legato or fx 3 | the instrument's **prelude**, and the held command's `tie` | §3.5 |
| the orderlist's `$E0+t` / `$FF pos` | `play(pattern, transpose)` / `end: jump(k)` | §3.6 |
| `$C0+n`, the packed rest | the event's `dur`, in **rows** | §3.6 |
| pattern `[instr][fx param](note\|rest\|keyoff\|keyon)` | `Event{sounds, note, gate, ins, arm}` — the note byte's token class is spent, not re-encoded (§4.8) | §3.6 |
| the 15 tick-0 handlers | `score.commands`, **named by what they do** — `tempo:07`, `stream.wave:04`, `sr:A4` — never by the nibble the jump table indexed them with; `meta.row_command: held` says the voice keeps the last one (§4.4) | §3.6 |
| `INS[i]`, 9 columns | `Ins{adsr, on_note, prelude, accs}` plus the family's own `wave`, `vibparam`, `vibdelay` columns | §3.5 |
| `wavetbl`/`notetbl` rows, `$00-$0F` delay, `$FF` jump | the `wave` stream: `hold`, `sets`, `op`, `jump` | §3.3 |
| a wavetable note column | the step's `op: pitch(absolute \| relative)`, the relative one a **signed semitone count** read off the column's low seven bits; the armed accs stand down | §3.3 `op` |
| `pulsetimetbl`/`pulsespdtbl` | the `pulse` stream: a `set` row, or `hold` ticks of `run(pulse_step)` | §3.3 |
| `filttimetbl`/`filtspdtbl`, global | the `filter` stream on `globals.streams`, and the three registers `globals.commit` | §3.7 |
| `speedtbl` left/right, double duty | the `speed` stream, unpacked into `delta`, `depth`, `cmp`, `zero` | §3.6 `arm` |
| `vibtime += 2`, `EOR #$FF` above `speedcmp` | `Acc(vibtime, const(+2), reflect-complement, amplitude [0, speed[param].cmp], bound observed [0, $FF])` | §5 vibrato |
| `LSR; BCC freqadd / BCS freqsub` | the freq Acc's `phase bit(vibtime, 0)` | §5 vibrato |
| `mt_effect_0`'s `vibdelay` countdown | `Acc(vibdelay, const(-1))`, guarded by the arm | §5 |
| `fx 1/2`: `freq ± speed` | `porta_up` / `porta_down`, `phase const(0/1)` | §5 free slide |
| `fx 3`: the 16-bit compare chain and `p_1327` | `policy clamp(pitch[note])`, the vibrato phase reset through `meta.pitch_links` | §5 tone portamento |
| `fx 3` with speed index 0 | the degenerate clamp: `delta $FFFF`, a step that reaches its target from either side (§4.6) | §5 |
| `p_1327`'s `lastnote = abs; vibtime = 0` | taking a pitch of the tuning sets `lastnote` and `meta.pitch_links` | new (§4.6) |
| `mt_loadregs`: `ghost.ctrl = wave & gate` | a `{stream}` phase of `meta.tick`, run whatever the row did | new (§4.5) |
| `init`: zero blocks A+B, tempo/counter/instr | `state0.prologue`, applied on the tick the init call spends | new (§4.7) |

What disappears: the patched `JSR`/`JMP` low bytes (`$1289`, `$1295`, `$131E`,
`$1445`) and the two jump tables `T144A`/`T145A`; the `X = voice*7` double duty,
the 1-based `base-1,Y` reads, the zero-page scratch pair, `mt_execchn`'s
fall-through tail call and the eleven SMC immediates. Not one byte of the object
names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole
certified horizon against the tune's own player on `deity_informant.PcodeVM`.

| tune | ins | patterns | events | tuning | streams | accs | ticks | SID writes | divergences |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Je suis Linus | 30 | 33 | 2,289 | 96 | 8 | 9 | 8,236 | 205,900 | **0** |
| Do It Again | 20 | 25 | 1,315 | 96 | 8 | 9 | 8,659 | 216,475 | **0** |

**Stronger than §2, outright.** `identical_ticks` equals the whole horizon for
both tunes: not a permutation, not a multiset, the same list, and
`same_per_register_order` is true trivially because the flush writes all 25
registers in one fixed order and nothing else writes the chip. Where Commando
had to report 105 ticks whose intermediate write differs, GT2 has none: **a
ghost flush is exactly the thing §2's reduction was written to tolerate, so
where a family has one the reduction costs nothing.**

The loop claim re-verifies on the render. The source certificates claim period
6,720 with first repeat at call 8,235, and 8,640 at 8,658; replaying ticks
1,516..8,235 against 8,236..14,955 and 19..8,658 against 8,659..17,298 is write
for write identical. The window starts one tick *after* the claimed first
repeat: the claim names the call the state repeats after, and a flush emits the
image the call before it left. At the claimed offset exactly one tick of 6,720
differs, and it is the boundary tick.

---

## 4. What the spec needed

Seven forms and one correction. None is a family branch, none is a new
mechanism, and every form is a datum Commando's object simply does not carry.

### 4.1 A shadow register file, and its flush

`meta.shadow.registers`, 25 of them descending. Where a tune has one, no write
reaches the chip on the tick that made it: `emit` deposits into the shadow, and
the tick's *first* act emits the whole shadow in the stated order.
`commit_order (sr, ad, ctrl)` follows from it — per voice the descending sweep
passes offset 6, 5, 4.

### 4.2 A row clock counting down, and a tempo over a stream

This family's clock counts **down**: `step −1`, `boundary [rowclock == 0]`, and
a `reset` clause that reloads where the cell has gone past. The funk tempo is
**one more reset clause, ahead of the plain one** — where the tempo cell says
so, take the row's length from the funk stream and toggle the cell that indexes
it, otherwise take the tempo cell, first match winning. It is dead in *Je suis
Linus* and fires 4,326 times in *Do It Again*, on the same code; striking the
clause and leaving the plain reload diverges on **8,639 of *Do It Again*'s
8,659** ticks and 0 of *Je suis Linus*'.

The `- 1` is the row's, not the player's: the reload takes a *countdown* against
the boundary at 0, so a row of `n` clock steps counts `n - 1` down to it, which
is what the plain reload cell already holds and what the funk row holds too. A
funk row reloading its own length instead of its countdown diverges on **8,625
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

The rest of the event lands at the row. The instrument the prelude belongs to is
the one the fetch just staged, which makes the legato class (`instr >=
FIRSTNOHRINSTR`) a property of the instrument's own `prelude: null` rather than
a test in the player. And **a tune whose fetch runs ahead has a lead-in row**:
nothing is staged before the first fetch, so the first row boundary sounds
nothing, which the object gets by consuming what was staged.

### 4.4 A row command may be **held**

Every GT2 tick 0 runs a command, and it is the last one the score gave — a row
without an effect byte inherits — so the voice holds a command and the score
replaces it. `arms` is §3.6's `arm(acc_id, overrides)`; `point` re-points a
cursor *and* resets its hold; what commands 1 and 2 zero (the vibrato phase) is
a `sets` on the accumulator's own cell, an `Acc` having no link channel and
`meta.pitch_links` being a *take*'s alone (§4.6); `all` is the global tempo, a
**write** to every voice's cell, so the invariant that no *expression* reads
another voice's state is untouched. `tie` belongs to the command because GT2's
does: a tone portamento suppresses the hard restart and the instrument load
whether the row carried the effect byte or inherited it.

A command is an object the score **names**, and `score.commands` carries the
tune's 43 (39) distinct ones once each — GT2 re-runs the held command at every
row, so inlining would have written the same record on 2,289 rows. It is named
by what it *does* (`tempo:07`, `stream.wave:04`, `sr:A4`) and never by the
nibble `T144A` indexes it with. `meta.rest_arm` is the arm a note row leaves
before its command runs, GT2's `fx = 0`.

### 4.5 A stream step's `op`, and the voice's exit rows

Commando's streams are one row of `set`s; GT2 exercises the whole of §3.3, under
one rule: **a step's `sets` and `op` fire on the tick that consumes the step**,
the last tick of its `hold`, **and a step that has an `op` is the voice's
producer for that tick, so the accumulators the score armed do not run.** That
is the wavetable's note column — where it sets a frequency the continuous effect
is skipped, which in the object is the absence of a second producer. A step may
instead `run` an accumulator on *every* tick it holds (the pulse and filter
sweeps), the same rule with the other timing, and both occur. A `{stream}` phase
of `meta.tick` names a stream every voice path ends on: GT2's `mt_loadregs`,
`ctrl = wave & gate`, on every tick including the ones a row consumes.

### 4.6 `clamp(target)`, its degenerate case, and what taking a pitch does

```
toneporta   freq  w16  voice.freq        scope voice
  policy  clamp pitch
  delta   speed[param].delta
```

**Taking a pitch of the tuning is one named operation**, not three assignments:
it writes the frequency, records the note sounded (`lastnote`, which `interval`
reads) and resets the phases `meta.pitch_links` names. The wavetable's note op
and the portamento's snap are the same operation, so it is stated once. Speed
index 0 — tie/instant — is the degenerate clamp, `delta $FFFF`, a step that
cannot fall short because it reaches the target from either side. The machine's
carry out of its own 16-bit compare into the add is provably 0 for every speed
this family can hold (`speed <= $7FFF` forces the sign test and the carry
apart), which the certificate measures rather than asserts.

### 4.7 `state0.prologue`, and a value the object says is not there

GT2's `init` only *schedules*; the first `play` call flushes the image the file
carries, runs the reset, and spends its tick. `state0.prologue` is that reset as
a list of assignments; the tick emits the flush and nothing else. Commando's
init happens before the horizon and is no part of its object; GT2's is inside
it.

And `{"trap": reason}` as an *expression*, a value the object states is not
there: the speed table is 1-based, so index 0 is its null. The row exists,
carries `zero: 1` (which the instrument vibrato's guard reads, and which is a
real fact) and traps on `delta` and `depth`. Over both horizons index 0 is asked
for `zero` 21,135 and 14,340 times and for a step **never**.

### 4.8 The note column is a token class, and the layer spends it

SID Wizard's note column packs, in the same byte: `note $01–$5F`, `set vibrato
amplitude $60–$6F`, `packed rest $70–$77`, `porta $78`, `sync on/off $79/$7A`,
`ring on/off $7B/$7C`, `gate on $7D`, `gate off $7E` (anatomy:1204). Admitting
`keyoff` as a note *value* because GT2's `$BE` sits in the note range admits
`sync on` as one because SW's does, and the anatomy already classes the
construct as a player idiom to be spent (anatomy:2833). So each token the byte
packed becomes its own field:

| the byte says | the event says | Hubbard | GoatTracker 2 | SID Wizard |
| --- | --- | --- | --- | --- |
| the row starts a sound | `sounds` | row bit 6 clear | a note byte `$60–$BC` | `$01–$5F` |
| its pitch | `note` | index, or none for a drum | index | index |
| a gate statement of its own | `gate` | — (its bit 6 *is* `sounds`) | `$BE` / `$BF` | `$7D` / `$7E` |
| rows the event spans | `dur` | 1 | `$C0+n` | `$70–$77` |
| re-target, do not re-trigger | `tie` | row bit 5 | effect 3 | — |
| everything else | `arm` | the porta byte | the fx nibble | `$60–$6F`, `$78–$7C` |

The field the comparison forced is `sounds`: without it the player answers "does
this row key a note?" from `gate == on` for Hubbard and `note is not None` for
GT2, and it leaves `note: none` ambiguous — *rest* here, *sounds, with the
instrument's own pitch* in Commando. GT2 carries a `gate` on 25 rows of 2,289
(39 of 1,315) and never on a row that also sounds, carries an instrument or
carries a note: a keyoff is a row with nothing else on it.

---

## 5. What the spec got right

Every §5 row GT2 is cited for held. One carries evidence the spec states only as
a result: **the coupled vibrato pair** swings against the speed byte's `& $7F`
and *not* the depth, and `bound [0, speedcmp] proved` is where the phase
**turns** rather than the interval the cell keeps — the complement arm exists to
put the cell above it, and 1,532 of *Je suis Linus*' 10,956 moves and 1,114 of
*Do It Again*'s 10,073 leave it, the first at tick 2 and tick 20. `tabcell` is
exercised here on a stream's named column; keyboard tracking and the
sign-extended table entry are not.

---

## 6. Finding the data

The two tunes are the same `player.s` with different flags, so **no address is a
constant**: *Do It Again* loads at `$AC00`, carries author text and has one more
jump-table entry, which moves every routine and every table. The tool locates
each datum by the *operand of the instruction that reads it*, by wildcarded
opcode pattern — 17 signatures, each matching exactly one site in both builds —
and derives the rest from the layout `player.s` fixes: the ghost's base comes
from the flush loop and the five 7-field blocks A–E are the 105 bytes before it,
so one anchor gives all 26 per-voice cells; the tuning follows the ghost, and a
parallel lo/hi pair gives its own length because the two columns are adjacent
and equal. That the object *is* the tune's data is checked rather than asserted:
a test reconstructs the wave, pulse, filter and speed tables, the nine
instrument columns and every pattern from the object and diffs them against the
image byte for byte on both builds. The orderlist alone does not come back byte
for byte, deliberately — `play(pattern, transpose)` spends the `$Ex` TRANS
bytes, so it is compared as the steps it decodes to.

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Je suis Linus | 2,865 | 18,267 | 2,823 | 7 | 42 | 2,823 | 5,512 |
| Do It Again | 1,816 | 11,853 | 1,782 | 7 | 34 | 1,782 | 5,180 |

`xz -9e` of the serialised object against the tune's own PSID load band, §9's
acceptance #3:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, Je suis Linus, compact | 180,184 | **5,608** |
| — its `score` half (orders, patterns, 43 commands) | 157,711 | 2,856 |
| — everything else (tuning, streams, accs, instruments) | 22,464 | 3,032 |
| the whole load band | 6,409 | 2,804 |
| `trackerprog.json`, Do It Again | 113,027 | **5,296** |
| — its `score` half | 93,611 | 2,676 |
| — everything else | 19,407 | 2,908 |
| its load band | 4,439 | 2,668 |

These two are the layer's **worst** ratios against the binary — **2.14×** and
**2.11×**, the current table being
[prototype-trackerprog.md](prototype-trackerprog.md) §9.1. The score half alone
holds up: 3,100 against a 2,804-byte compressed load band that contains the
player *and* the data, so the music alone, materialised with every packed byte
unpacked and every cursor spent, costs about what the whole cartridge does
compressed. It is the sound half that doubles the total.

The raw size is key repetition and one deliberate choice: the row commands are
**interned**, which takes the object from 402,928 raw and 5,916 compressed to
180,184 and 5,608, and the print from 36,307 tokens to 18,267. Against the
floor, anatomy §3.3.7's "player in ~30 lines" covers GT2 alone; the universal
player has neither Commando nor GoatTracker in it.

---

## 8. What the tuneprog could not settle

Four facts the printed `tuneprog.md` could not settle needed the disassembly.
Each is tracked in [trackerprog-backlog.md](trackerprog-backlog.md).

| # | the fact | ground truth | the generic fix |
| --- | --- | --- | --- |
| 1 | a table's base and basedness — one array under several names with several derived origins (`T16F9[1 + t1]`, `T16F9[2 + r4]`, `T16F9[y]`) | `$129C LDA $16F8,Y` ⇒ base `$16F9`, 1-based | print **one** canonical `origin` and `basedness` per region and normalise every index expression to it |
| 2 | a carry live into an add, printed as a predicate over the reaching compare | `$12CD CMP #$E0 / $12CF BCS` leaves C = 0 on the fall-through | constant-fold a `carry(site)` term where the reaching compare proves it, and keep the named form only where it is live |
| 3 | what an untaken arm does: `p_1082` printed `# untaken`, and the two instructions the arm holds (`LDY #$00 ; STY $FD`, which makes the vibrato depth 8-bit) not printed at all | `$1087–$1089` | print an untaken arm's **body**, marked, rather than eliding it: the second build of the same player may take it |
| 4 | which register a store is, and in what order — `commit_order` is nowhere stated | `$134B STA $14CD,X` then `$134E..$1351 STA $14CC,X` | state the per-tune `commit_order` (§3.1) as a certificate field |

A fifth: the tick-0 and continuous dispatches print as a `switch` over the
*patched address*, which is the compiled form, where the command's number is
the index into `T144A` the same block computes one line above, and printing the
switch over that index would make the arms comparable between builds.
