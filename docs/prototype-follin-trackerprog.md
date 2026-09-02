# Prototype: Ghouls'n'Ghosts as a trackerprog — the sixth family, and the score that is a program

A **hand transliteration** of the certified Tim Follin tuneprogs
([prototype-follin.md](prototype-follin.md), anatomy [§3.6](playroutine-anatomy.md))
into trackerprogs ([prototype-trackerprog.md](prototype-trackerprog.md) §3),
rendered by the same universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
defMON ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)) and
JCH ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)), certified
against the tune's own player on the PcodeVM.

Four results. **All thirty-two subtunes render over their whole horizons, write
for write**: 111,763 ticks, **0 divergences** on §2's observable, every tick's
write list *identical*. Three are named builds, the family's three shapes and
its three terminators — song 0 (12,997 ticks, `fixed_point`), song 6 (14,337,
`loop`, period 8,064 re-verified on the render) and song 20 (20,049, `horizon`,
a sound effect that starts one voice of three). **The score is a program**: no
orderlist/pattern split and no instrument table, one byte stream per voice being
both, with `$8A` call, `$8B` return, `$82`/`$81` counted loop, `$87` jump and
`$86` stop — §3.6's `call`, `ret` and counted loop, struck for want of an
exemplar, rendered and measured here. **The fetch is a walk**, 25 rows in one
tick at its longest. **The layer invariant holds at six families**, with no
branch on `meta.family`.

```
tools/trackerprog_follin.py $HVSC/MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid \
    --song 0  --source docs/certificates/ghouls-song01.json --certify --out out/follin-tp/song0
```

`--song` is 0-based, as the player's own `init` is; a certificate's `song` field
is 1-based, so subtune 0 is `ghouls-song01.json`, and songs 6 and 20 take
`ghouls-song07.json` and `ghouls-song21.json`.

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 the nine things the family was expected to force.

---

## 1. The object

| block | what it holds |
| --- | --- |
| `meta` | three voices in order 0 1 2; the row clock is `dur`, a frame count with no tempo and no speed counter; `tick` is the six blocks of the anatomy's pseudocode with a `commit` between each; `row_ends_fetch` says the walk stops at a row with a length |
| `pitch` | 97 entries, base 0, `round($010C·2^(k/12))` — read at `note + transpose`, a byte |
| `streams` | eight, all `all: True` (guarded rows, no cursor): `blip`, `vibrato`, `pitchmod`, `pulse`, `gate` for the voice's tick; `noteon` and `rest` for its row program; `filter` for the one global channel |
| `accs` | **empty**. Not one modulator of this player is an accumulator with a bound and a policy; every one is a fixed sequence of guarded assignments, which is a stream's degenerate form |
| `instruments` | **one**, with no accs and no columns. The family has no instrument table: an instrument is the run of commands before a note, and those are the score's |
| `score` | three order programs of blocks, each block a run of rows ending in one step; the rows are the byte stream's own tokens, one event each |
| `state0` | 36 cells per voice — **27** of zero page, **6** immediates the play routine rewrites inside its own instructions, `blipfreq` from two bytes inside a handler, the order program's own position and one scratch the note-on and the portamento share — plus seven globals and the `stopped` mask the entry left |

Nothing in the object is named for Follin, and nothing in `trackerprog/` is.

---

## 2. The mapping, line by line

| the tuneprog says | the trackerprog says |
| --- | --- |
| `notetab[97]` at `$6D35`/`$6D96` | `pitch[n]`, base 0; past its top the read is a producer, not a pitch (§4.6) |
| one byte stream per voice | `score.orders[v].play`, a block per label, each with its own step |
| `$8A lo hi` / `$8B` | `{"call": i, "ret": j}` / `"ret"` over a per-voice return stack |
| `$82 n` / `$81` | `{"mark": n, "next": j}` / `{"loop": true, "next": j}` over one counted-loop register |
| `$87 lo hi` / `$86` | `{"jump": j}` / `"stop"` — which stops this voice and not the tune |
| a note byte, and its length | an event with `note`, `sounds` and `dur`; a length byte where `$84`'s cell is 0 |
| a command byte | an event with `dur: 0` whose `arm` is §3.6's command: the cells its bytes name |
| `$85 (r v)* T` | `sets` on the register **named outright** — any voice's, which §3.1's one register naming already gives |
| `$8D w` | `ctrl := w ; @wave := w ; @pulse_mode0, @pulse_mode := w & $40 ? $FF : 1` |
| `$80 s lo hi` | `@pwspd, @pwreset, @pw`; a reset of zero takes the handler's own default cell |
| `$88` eight bytes | `#filtspd, @filtdir0, #cutoff, #cutreset, #filtmin, #filtmax` |
| `$89` | `#owner := voice_index` — the third family's third spelling of a filter owner |
| `$8E d dp hp dir` | `@vibdelay, @vibdepth, @halfper, @vibdir0` |
| `$8F n w lo hi` | `@bliplen, @blipwave, @blipfreq` |
| `$91 off tA tB` / `$92 s` / `$90 r` / `$8C t` / `$83 g` | `@trilloff/@tA/@tB` · `@portaspd` · `@release` · `@transpose` · `@gated := 1 ; @gatelen` |
| `$93` | `@skipxpose := $FF` — one shot, and the note that spends it clears it |
| blip end (`$623B`) | stream `blip`: a countdown, then the note's own frequency and waveform |
| vibrato (`$6258`) | stream `vibrato`: a delay counter, a signed step on `freqsh`, and a half-period counter that complements the direction cell |
| trill (`$6295`) else porta (`$62BE`) | stream `pitchmod`: two modulators of the *note index* sharing one tail — the tuning, read at the note they left |
| pulse (`$62ED`) | stream `pulse`: §4.8's bounce, then the pair the frame leaves |
| duration and gate off (`$6338`) | the row clock's own step, then stream `gate`: the release point and the gate-off countdown, neither spending the other's step |
| the fetch (`$6360`) | `meta.row` over the walk: the row's commands, then `noteon` or `rest` |
| the filter (`$67FF`) | `globals.after` stream `filter` — the same bounce over `#cutoff` — and `globals.commit`, the 11 bits the chip splits 8 and 3 |
| `play` returns `$7B\|$7C\|$7D` | nothing. The observable is what the chip saw; a return value no caller in the horizon reads is not part of it |

---

## 3. The certificate

Three named builds, each over its **whole** horizon, each against the tune's
own player on the PcodeVM under §2's comparison:

| build | ticks | writes | divergences | identical ticks | end |
| --- | --- | --- | --- | --- | --- |
| song 0 — the theme, and it stops | 12,997 | 86,258 | **0** | **12,997** | `fixed_point`, verified |
| song 6 — and it loops | 14,337 | 114,543 | **0** | **14,337** | `loop`, period 8,064, verified |
| song 20 — a sound effect, one voice of three | 20,049 | 83,811 | **0** | **20,049** | `horizon` |

`identical_ticks == ticks` on all three: the same writes in the same order, with
no shadow to make that free — the player writes the chip as it goes, six times a
voice-tick — so the group boundaries are carrying it (§4.1), and removing them
costs 213 and 326 ticks (§7). `fixed_point` is spelled differently here than in
the one other family that has it: JCH's *Knob at Night* ends in **silence**, its
last tick writing nothing, where song 0 ends in a **standing wave**, all three
voices stopping themselves with `$86` while the filter commits its two registers
every frame forever. So what settles is the write list rather than its absence,
and the check is that the tick after the horizon writes what the last tick of it
wrote.

---

## 4. What the spec needed

In the player: §4.1 the fetch is a walk · §4.2 the order program's own steps ·
§4.3 a `stop` that stops one voice · §4.4 the global channel steps *after* the
voices · §4.5 a row's own length and note are facts of the row · §4.6 a stream
carries what lies past the tuning. In the print: §4.7. In the data only: §4.8
the bounce whose turn steps back · §4.9 the two-armed branch as two rows · §4.10
the score's own note lengths.

### 4.1 The fetch is a walk

Every family before this one consumes exactly one row at a row boundary. This
one runs its sequencer *until a note is fetched* — `$85` sets six registers,
`$8D` the waveform, `$8C` the transpose, `$8A` calls, and then a note byte
arrives — and `meta.row_ends_fetch` is where it stops, here a row that carries a
length. The longest walk over the three named builds is **25 rows in one tick**
(song 6; song 0 reaches 22 and song 20 nine). The subtlety is the *group*: §2
rule 1 keeps every ctrl/AD/SR write in tick order and this player has no shadow,
so the loop flushes the group **between two rows and never after the last**, and
the same reasoning gives `meta.tick` a `commit` between each of the voice's six
blocks. Collapsing them to one group a tick diverges on 213 ticks of song 0 and
326 of song 6, the blip's gate write landing after the pulse's width.

### 4.2 The order program's own steps

§3.6 struck `for`, `call` and `ret` from `Order` for want of an exemplar. They
are rendered now, as a `play` step's own `op`:

```
{"jump": j}                  the score's own goto
{"call": j, "ret": k}        push k, go to j
"ret"                        pop
{"mark": n, "next": j}       the counted loop opens: n times, from step j
{"loop": true, "next": j}    spend one; back to the mark, else to j
"stop"                       this voice, and not the tune (§4.3)
```

Two things about the spelling. **A call names where it comes back to**: the 6502
pushes `ptr + 3`, an address, and the block list's order is not the program's.
And **`mark` and `loop` are two steps, not one `for`**, two bytes in two places
with the body between them, the counter one register per voice that nothing
saves or restores. Across the 32 subtunes: 302 calls, 126 returns, 196 marks,
195 loops, 39 jumps, 46 stops.

### 4.3 A `stop` that stops one voice and not the tune

`$86` is `INC $7B`: the voice's active flag goes negative-to-zero, the routine
moves to the next voice, and the filter runs regardless. So `stopped` is a
per-voice list, `state0.stopped` seeds it from the image, and a stopped voice
runs no clock at all. The seed is not decoration — a sound effect starts one to
three voices over whatever was playing and leaves the others where `sidclear`
left them — and song 20 starts one voice, where starting all three diverges on
all 20,049 of its ticks.

### 4.4 The global channel steps after the voices, not before

`globals.streams` runs the channel before the voices, which is right for a
channel the voices *read*. This one they **write**: an owner voice's note-on
reloads `#cutoff` from `#cutreset` and the filter sweeps from there in the same
frame, so sweeping first writes the un-swept value. `globals.after` is the
second list, and which of the two a tune has is data; moving this family's
filter to `globals.streams` diverges on 383 ticks of song 0.

### 4.5 A row's own length and note are facts of the row

`dur` is what ends the walk (§4.1) and `note` is what the note-on transposes, so
both belong to the payload a row program's guards read wherever the row is read
— not to the fetch alone. One dictionary, and a staging family's own list keeps
only the *two* values it copies rather than tests, the row's instrument and its
play step's transpose.

### 4.6 A stream carries what lies past the tuning

The note index is `note + transpose`, one byte, and the tables are 97 entries,
so an index past 96 reads what follows them in the image — the high table's own
first byte, then the sound-effect pointers and lists. That overrun is a
*producer*, so it is a `beyond` record of words indexed by how far past the top
the read went, reached from an `all` stream's row. The bound is the index's own
width, so the record is not an estimate: 159 words, `$61` through `$FF`, each
the constant the image holds there. Both streams that read the tuning carry one.

### 4.7 An order that is a program prints one step per line

An order that carries steps prints one to a line with its step spelled out —
`call 12, back at 4`, `mark 3 from 18`, `loop, else 20` — where a family with no
steps prints its pattern numbers 24 to a line as before.

### 4.8 The bounce whose turn steps back — in the data

The pulse width and the filter cutoff sweep between two bounds, and the turn is
not §5's `reflect`: the player tests the bound on the *stepped* value and, where
it crossed, complements the direction cell and **runs the other arm in the same
frame**, so the frame a bound is met leaves the accumulator where it found it
and can meet the far bound on the way back. The object is that loop unrolled:
three passes, the second and third guarded by the turn before them, and a fourth
that is a `trap`. Two passes trap on song 0's pulse and song 6's cutoff, and
three diverges on nothing anywhere.

### 4.9 The two-armed branch as two rows — in the data

`LDY #imm; BEQ` over an immediate the play routine rewrites is how this player
asks every direction question: vibrato up or down, pulse up or down, trill on
its offset or its base. A `phase` expression cannot answer it — the cell is a
byte and the test is "is it zero", and `EOR #$FF` on a direction seeded with
`$01` gives `$FE`, which is not zero either, so the voice adds forever. A branch
with two arms is **two rows**, one guarded `== 0` and one `!= 0`.

### 4.10 The score's own note lengths — in the data

`$84 n` patches the immediate the fetch reads a note's length from, and a
non-zero one means notes carry no length byte at all, so the byte grammar is not
context free: the same address parses two ways under two values of that cell. A
state is therefore a byte **and** the length, blocks are keyed by both, and the
walk is one procedure at a time with a summary per call target, because a `$84`
inside a called body changes what the caller comes back to. Two of the eleven
songs need it, and a procedure that *returned* under two lengths would be a
named refusal; none of the 33 tracks is one.

---

## 5. What the spec got right

`$85` needed no new command form: §3.1's register naming writes any voice's
register from a voice, which is exactly what a raw list is. §3.5's `Ins` fits a
family with no instrument table — one entry, the note-on its `on_note`-shaped
stream, the cells it reads filled by the score's own commands, which is what "an
instrument is the run of commands before a note" means written down.
`row_command: "spent"` and the command-as-`arm` shape carried 21 command classes
without a new field. And the filter owner is a datum: SID Wizard guards on a
constant voice index, JCH on a byte of its own filter table, this family on a
cell `$89` writes — one guard, `voice_index == #owner`.

---

## 6. Finding the data

Every datum is read off the image **the tick sees**, not the load band: `init`
tail-jumps into a rip stub that copies a subtune's two song blocks over
themselves to their run addresses, so before it has run the tracks are not
there. The three track pointers are then simply zero page `$21`, `$23`, `$25`,
the song-setup routine having loaded them from `$730E+X`, and `state0` is the
image at those addresses — the six immediates inside instructions and the blip's
frequency inside a handler included.

Three corrections to the anatomy's §3.6 fell out. **`$93` is not unused**: the
census calls the skip-transpose cell unused, where subtune 7 uses it at `$9CE9`,
`$9CEE` and `$9CFE`, and rendering it as a no-op diverges on 2,575 ticks of song
0 and 4,155 of song 6. **`$8D` sets the running pulse mode too**, not only the
mode at next note: `$694F`/`$6952` store the same byte to both cells, so leaving
the running mode alone diverges on 2,260 ticks of song 0. And the `$85` handler
**consumes its own terminator** (`$6913`–`$6917`), so a list of *k* pairs is
`2k + 2` bytes and not `2k + 1`.

---

## 7. Measurements

**The sweep.** All 32 subtunes, each over the whole horizon its tuneprog
certificate claims:

| | subtunes | ticks | divergences | identical ticks |
| --- | --- | --- | --- | --- |
| songs (0–10) | 11 | 88,486 | **0** | **88,486** |
| sound effects (11–31) | 21 | 23,277 | **0** | **23,277** |
| all | 32 | **111,763** | **0** | **111,763** |

**The strike.** Every change to the player, measured over the whole certified
horizon of all eleven earlier builds (three Commando subtunes, both GT2, both
SW, both defMON, both JCH), is **0 of 236,586 ticks**: the fetch loop leaves on
its first pass where no `row_ends_fetch` is declared, `globals.after` is empty,
`stopped` is all false, and the two new payload facts are keys no other family's
guard names.

**The poison table** — what each datum is worth, as ticks whose write lists
differ when it is taken away, over the three named builds:

| the object says | song 0 | song 6 | song 20 |
| --- | --- | --- | --- |
| the fetch is a walk | **12,997** | **14,337** | **19,307** |
| every voice starts, whatever the entry said | 0 | 0 | **20,049** |
| the voices run in the other order | **7,412** | **12,322** | **372** |
| `$93` never skips the transpose | **2,575** | **4,155** | 0 |
| `$8D` leaves the running pulse mode alone | **2,260** | 0 | 0 |
| the gate-off countdown steps on every tick | **5,398** | **750** | 0 |
| the filter steps before the voices | **383** | 0 | 0 |
| any voice may reload the filter at a note | **318** | 0 | 0 |
| one group a tick, not one a block | **213** | **326** | 0 |
| the pulse or the cutoff turns at most twice a frame | trap | trap | 0 |
| the pulse or the cutoff turns at most three times | 0 | 0 | 0 |
| `commit_order` is `(ad, sr, ctrl)` or `(sr, ad, ctrl)` | 0 | 0 | 0 |
| the blip end gates whatever the note did | 0 | 0 | 0 |
| a rest keys a note of the tuning | 0 | 0 | 0 |

The last three rows are the honest ones. **`commit_order` is worth nothing to
this family**: every edge write this player makes is a `ctrl`, its `AD` and `SR`
going through `$85` as registers named outright, so the object's `(ctrl, ad,
sr)` is a spelling rather than a claim. No tick of these three builds runs a
blip on an ungated instrument. And **no subtune contains a single rest**: the
`$00` class is in the grammar, is parsed, is materialised, and across all 32
subtunes the count of rest events is **0**.

**The print**, architecture §6.2's six numbers against the source tuneprog's,
for song 0:

| | lines | tokens | statements | blocks | header rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- |
| `tuneprog.md` | 1,277 | 18,310 | 1,271 | 5 | 6 | 11,648 |
| `trackerprog.md` | 1,723 | 13,356 | 1,633 | 7 | 90 | 6,024 |

Longer in statements and shorter in everything that measures information: the
score is materialised into rows, which adds lines, and the tick stops being
code, which removes tokens. Against the tune's own load band — the only
comparison that measures — three of thirty-two subtunes is no comparison at all,
and the three objects summed separately are 1.35×
([prototype-trackerprog.md](prototype-trackerprog.md) §9.1).

---

## 8. The nine things the family was expected to force

Five held (1 the stack-machine score, 3 no instrument table, 4 STATE that
includes code bytes the tick rewrites, 6 a third filter owner, 8 the note-table
overrun) and two held in kind but not in spelling: `$85` needed no
`set_register` command form (§5), and the blip is neither a prelude nor an `Acc`
but a countdown cell with two guarded rows (§2). Two were wrong. **`play`'s
return value is not a tick output**: `A = $7B|$7C|$7D` is read by the game, not
by the schedule, and the observable is what the chip saw, so it is in neither
the object nor the certificate. And **`commit_order` gets no sixth
measurement**: both other orders diverge on 0 of all three builds (§7), so the
object's value is a spelling and not a claim.

Two more the exemplar produced without being asked are the anatomy corrections
in §6, both load-bearing in ticks and neither findable by a static census.
