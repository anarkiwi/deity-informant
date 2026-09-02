# Prototype: Ghouls'n'Ghosts as a trackerprog — the sixth family, and the score that is a program

A **hand transliteration** of the certified Tim Follin tuneprogs
([prototype-follin.md](prototype-follin.md), anatomy [§3.6](playroutine-anatomy.md))
into trackerprogs ([prototype-trackerprog.md](prototype-trackerprog.md) §3),
rendered by the same universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
defMON ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)) and
JCH ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)), and certified
against the tune's own player on the PcodeVM.

Four results:

1. **All thirty-two subtunes render, over their whole horizons, write for
   write.** 111,763 ticks, **0 divergences** on §2's observable — and stronger:
   every tick's write list is *identical*, value for value, register for
   register, in the order the chip saw them. Three are named builds because
   they are the family's three shapes and its three terminators: **song 0**
   (12,997 ticks, `fixed_point`), **song 6** (14,337, `loop`, period 8,064
   re-verified on the render) and **song 20** (20,049, `horizon`, a sound
   effect that starts one voice of three). The other twenty-nine are the sweep
   in §7.
2. **The score is a program, and §6.2's struck grammar comes back with an
   exemplar.** This family has no orderlist/pattern split and no instrument
   table: one byte stream per voice is both, and its structure is `$8A` call,
   `$8B` return, `$82`/`$81` counted loop, `$87` jump, `$86` stop. `for`,
   `call` and `ret` in `Order` were struck from §3.6 as "grammar with no
   exemplar is not grammar"; they are grammar now, they are measured, and the
   player runs them for every family through one procedure.
3. **The fetch is a walk.** Every other family consumes one row per row
   boundary. This one consumes every command it meets on the way to the note —
   25 rows in one tick at its longest — and each is its own act, because the player writes
   the chip as it goes. Emitting one act instead of six diverges on **213** of
   song 0's ticks and **326** of song 6's.
4. **The layer invariant holds at six families**, with no branch on
   `meta.family` anywhere in `trackerprog/`. The player grew by **110 lines**
   to carry this one; the other eleven builds render **0 of 236,586 ticks**
   differently.

Reproduce:

```
tools/trackerprog_follin.py $HVSC/MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid \
    --song 0  --source docs/certificates/ghouls-song01.json --certify --out out/follin-tp/song0

tools/trackerprog_follin.py $HVSC/MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid \
    --song 6  --source docs/certificates/ghouls-song07.json --certify --out out/follin-tp/song6

tools/trackerprog_follin.py $HVSC/MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid \
    --song 20 --source docs/certificates/ghouls-song21.json --certify --out out/follin-tp/song20
```

`--song` is 0-based, as the player's own `init` is; a tuneprog certificate's
`song` field is 1-based, so subtune 0 is `ghouls-song01.json`.

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 the nine things the family was expected to force.

The forms, in one line each. In the player: §4.1 the fetch is a walk · §4.2 the
order program's own steps · §4.3 a `stop` that stops one voice and not the tune
· §4.4 the global channel steps *after* the voices · §4.5 a row's own length
and note are facts of the row · §4.6 a stream carries what lies past the tuning.
In the print: §4.7 an order that is a program prints one step per line. In the
data only:
§4.8 the bounce whose turn steps back · §4.9 the two-armed branch as two rows ·
§4.10 the score's own note lengths, which make its grammar context-sensitive.

---

## 1. The object

| block | what it holds |
| --- | --- |
| `meta` | three voices in order 0 1 2; the row clock is `dur`, a frame count with no tempo and no speed counter; `tick` is the six blocks of the anatomy's pseudocode with a `commit` between each; `row_ends_fetch` says the walk stops at a row with a length |
| `pitch` | 97 entries, base 0, `round($010C·2^(k/12))` — read at `note + transpose`, a byte |
| `streams` | eight, all `all: True` (guarded rows, no cursor): `blip`, `vibrato`, `pitchmod`, `pulse`, `gate` for the voice's tick; `noteon` and `rest` for its row program; `filter` for the one global channel |
| `accs` | **empty**. Not one modulator of this player is an accumulator with a bound and a policy; every one of them is a fixed sequence of guarded assignments, which is a stream's degenerate form |
| `instruments` | **one**, with no accs and no columns. The family has no instrument table: an instrument is the run of commands before a note, and those are the score's |
| `score` | three order programs of blocks, each block a run of rows ending in one step; the rows are the byte stream's own tokens, one event each |
| `state0` | 36 cells per voice — **27** of zero page, **6** immediates the play routine rewrites inside its own instructions, `blipfreq` from two bytes inside a handler, the order program's own position and one scratch the note-on and the portamento share — plus seven globals and the `stopped` mask the entry left |

Nothing in the object is named for Follin, and nothing in `trackerprog/` is.

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
| `$85 (r v)* T` | `sets` on `reg.R` — the chip by number, across voices, which §3.7 already had |
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
| `play` returns `$7B\|$7C\|$7D` | nothing. The observable is what the chip saw; a return value no caller in the horizon reads is not part of it, and this document does not claim it |

## 3. The certificate

Three named builds, each over its **whole** horizon, each against the tune's
own player on the PcodeVM under §2's comparison:

| build | ticks | writes | divergences | identical ticks | end |
| --- | --- | --- | --- | --- | --- |
| song 0 — the theme, and it stops | 12,997 | 86,258 | **0** | **12,997** | `fixed_point`, verified |
| song 6 — and it loops | 14,337 | 114,543 | **0** | **14,337** | `loop`, period 8,064, verified |
| song 20 — a sound effect, one voice of three | 20,049 | 83,811 | **0** | **20,049** | `horizon` |

`identical_ticks == ticks` on all three: not merely equal under §2's reduction,
which drops the order between register classes, but the same writes in the same
order. There is no shadow in this family to make that free — the player writes
the chip as it goes, six times a voice-tick — so the group boundaries are
carrying it (§4.1), and removing them costs 213 and 326 ticks (§7).

`fixed_point` is spelled differently here than in the one other family that has
it. JCH's *Knob at Night* ends in **silence**: its last tick writes nothing.
Song 0 ends in a **standing wave**: all three voices stop themselves with `$86`,
and the filter goes on committing its two registers every frame forever. So
what settles is the write list rather than its absence, and the check is that
the tick after the horizon writes what the horizon's last tick wrote.

## 4. What the spec needed

### 4.1 The fetch is a walk

Every family before this one consumes exactly one row at a row boundary. This
one runs its sequencer *until a note is fetched*: `$85` sets six registers,
`$8D` sets the waveform, `$8C` transposes, `$8A` calls, and then a note byte
arrives and the walk stops. The longest walk over the three named builds is
**25 rows in one tick** (song 6; song 0 reaches 22 and song 20 nine).

`sequencer_step` is that loop now, and `meta.row_ends_fetch` is where it stops —
for this family, a row that carries a length. A family with no such key takes
one row and leaves on the first pass, which is what the other five do.

The subtlety is the *group*. §2 rule 1 keeps every ctrl/AD/SR write in tick
order, and this player has no shadow: an `$85` list writes the chip where it
stands, and a note-on's gate follows it. So the loop flushes the group **between
two rows and never after the last** — a family that takes one row makes one act
and is bit-identical by construction, and a family that takes six makes six.
That is why the change measures 0 on all eleven existing builds without a flag.

The same reasoning runs the other way inside the tick: the six blocks of the
voice's own frame each write as they go, so `meta.tick` names a `commit` between
each. Collapsing them to one group a tick diverges on 213 ticks of song 0 and
326 of song 6 — the blip's gate write landing after the pulse's width, which
the chip saw the other way round.

### 4.2 The order program's own steps

§6.2 struck `for`, `call` and `ret` from `Order` with the rule "a row nothing
renders is a row nothing tests". They are rendered now. A `play` step may carry
an `op`:

```
{"jump": j}                  the score's own goto
{"call": j, "ret": k}        push k, go to j
"ret"                        pop
{"mark": n, "next": j}       the counted loop opens: n times, from step j
{"loop": true, "next": j}    spend one; back to the mark, else to j
"stop"                       this voice, and not the tune (section 4.3)
```

Two things are worth saying about the spelling. First, **a call names where it
comes back to**, rather than the player assuming the next step: the 6502 pushes
`ptr + 3`, an address, and the block list's order is not the program's. Second,
`mark` and `loop` are two steps and not one `for`, because they are two bytes
in two places with the loop's body between them, and the counter is one
register per voice that nothing saves or restores — this family's loops do not
nest, and the object says so by having one cell.

Across the 32 subtunes the steps are: 302 calls, 126 returns, 196 marks, 195
loops, 39 jumps, 46 stops.

### 4.3 A `stop` that stops one voice and not the tune

`$86` is `INC $7B`: the voice's own active flag goes negative-to-zero, the
routine moves to the next voice, and the filter runs regardless. Every other
family's score ends the *tune*. So `stopped` is a per-voice list the player
carries, `state0.stopped` seeds it from the image, and a stopped voice runs no
clock at all — it does not fetch, and its `dur` does not move.

The seed is not decoration. A sound effect starts one to three voices over
whatever was playing and leaves the others where `sidclear` left them, which is
a track pointer of `$0000` and no track. Song 20 starts one voice; starting all
three instead diverges on all 20,049 of its ticks.

### 4.4 The global channel steps after the voices, not before

`globals.streams` runs the channel before the voices, which is right for a
channel the voices *read*. This one they **write**: an owner voice's note-on
reloads `#cutoff` from `#cutreset`, and the filter sweeps from there in the same
frame. Sweeping first and reloading second writes the un-swept value.

So `globals.after` is the second list, and which of the two a tune has is data.
Moving this family's filter to `globals.streams` diverges on 383 ticks of song 0.

### 4.5 A row's own length and note are facts of the row

`row_facts` built the payload a row program's guards read, and it carried
whether the row sounds, keys, carries a field, states a gate and ties — but not
its length or its note, which `stage_facts` added for the fetch alone. Both are
facts of the row wherever it is read, and both are needed here: `dur` is what
ends the walk (§4.1) and `note` is what the note-on transposes. One dictionary
now, and `stage_facts` is left with the *two* a staging copies rather than
tests — the row's instrument and its play step's transpose.

### 4.6 A stream carries what lies past the tuning

The note index is `note + transpose`, one byte, and the tables are 97 entries.
An index past 96 reads what follows them in the image — for the low table, the
high table's own first byte, and then the sound-effect pointers and lists.

§6's rule is that the overrun is a *producer* and not a tuning, and the player
already had the shape: a `beyond` record of words indexed by how far past the
top the read went. What it did not have was a way for an `all` stream's row to
reach one — `stream_step` set `self.beyond` and `rows` did not. It does now.

The bound is the index's own width, so the record is not an estimate: 159 words,
`$61` through `$FF`, each the constant the image holds there, stated word for
word. Both streams that read the tuning carry one.

### 4.7 An order that is a program prints one step per line

The print listed an order as a row of pattern numbers, 24 to a line, which is
what an orderlist is. An order that carries steps prints one to a line with its
step spelled out — `call 12, back at 4`, `mark 3 from 18`, `loop, else 20` — and
a family with no steps prints as before.

### 4.8 The bounce whose turn steps back — in the data

The pulse width and the filter cutoff sweep between two bounds, and the turn is
not the `reflect` policy §5 has. The player tests the bound on the *stepped*
value and, where it crossed, complements the direction cell and **runs the other
arm in the same frame** — so the frame a bound is met leaves the accumulator
exactly where it found it, and can meet the far bound on the way back.

That is a loop over two arms, and the object is it unrolled: three passes, each
after the first two guarded by the turn before it, and a fourth that is a
`trap`. Three is not a guess — two passes trap on song 0's pulse and on song
6's cutoff, and three diverges on nothing anywhere.

No player change: `TURNS` is a number in the tool and the rows are data.

### 4.9 The two-armed branch as two rows — in the data

`LDY #imm; BEQ` over an immediate the play routine rewrites is how this player
asks every direction question: vibrato up or down, pulse up or down, trill on
its offset or its base. A `phase` expression cannot answer it, because the cell
is a byte and the test is "is it zero" — and `EOR #$FF` on a direction seeded
with `$01` gives `$FE`, which is not zero either, so the voice adds forever.

The reading that needs nothing new is the assembly's own: a branch with two arms
is **two rows**, one guarded `== 0` and one `!= 0`, and guards already compare.
A zero-test expression node was considered and is not in the object.

### 4.10 The score's own note lengths — in the data

`$84 n` patches the immediate the fetch reads a note's length from, and a
non-zero one means notes carry no length byte at all. So the byte grammar is
not context free: the same address parses two ways under two values of that
cell, and a static parse must carry it.

A state is therefore a byte **and** the length, blocks are keyed by both, and
the walk is one procedure at a time with a summary per call target — because a
`$84` inside a called body changes what the caller comes back to. Two of the
eleven songs need it: without the specialisation, `$9862` in subtune 8 and
`$9C8C` in subtune 10 parse under two lengths at once.

A procedure that *returned* under two lengths would need two return addresses
for one pushed one, and is a named refusal. None of the 33 tracks is one.

## 5. What the spec got right

- **A named register is `set_register`.** I6 settled §3.6's command list with a
  `set_register` form for exactly this family's `$85`. It needed no new form:
  the named-register target, added for JCH's global channel, writes any voice's
  register already. The expectation was right about the need
  and wrong about the spelling.
- **The instrument is the note-on.** §3.5 says an instrument is the cells a
  note-on writes plus the streams it re-points. This family has no instrument
  *table*, and the form still fits: `instruments` has one entry, the note-on is
  its `on_note`-shaped stream, and the commands that fill the cells it reads are
  the score's — which is what "an instrument is the run of commands before a
  note" means when it is written down.
- **`row_command: "spent"`** and the command-as-`arm` shape carried 21 command
  classes without a new field, including one of variable length.
- **The filter owner is a datum.** SID Wizard guards on a constant voice index,
  JCH on a byte of its own filter table, this family on a cell `$89` writes.
  Three families, three values, one guard: `voice_index == #owner`.

## 6. Finding the data

Every datum is read off the image **the tick sees**, not the load band: `init`
tail-jumps into a rip stub that copies a subtune's two song blocks over
themselves to their run addresses, so before it has run the tracks are not
there. The tool runs the tune's own `init` and reads afterwards, which is SID
Wizard's method for the same reason.

The three track pointers are then simply zero page `$21`, `$23`, `$25` — the
song-setup routine has already loaded them from `$730E+X`, so the tool never
touches the rip loader's own tables. The same is true of every cell in §1: the
object's `state0` is the image at those addresses, including the six immediates
that live inside instructions and the blip's frequency, which lives inside a
handler.

Two corrections to the anatomy's §3.6 fell out of doing this:

- **`$93` is not unused.** The anatomy's census says `$93/$94 0` and calls the
  skip-transpose cell unused. Subtune 7 uses `$93` three times, at `$9CE9`,
  `$9CEE` and `$9CFE`. Rendering it as a no-op diverges on 2,575 ticks of song 0
  and 4,155 of song 6 — it is load-bearing in tunes the census did not reach.
- **`$8D` sets the running pulse mode too.** The anatomy has it setting the mode
  "at next note". `$694F`/`$6952` store the same byte to both cells, so it takes
  effect on the frame the command runs. Leaving the running mode alone diverges
  on 2,260 ticks of song 0.

And one detail the byte grammar needs that the table does not state: the `$85`
handler consumes its own terminator (`$6913`–`$6917`), so a list of *k* pairs is
`2k + 2` bytes and not `2k + 1`.

## 7. Measurements

**The sweep.** All 32 subtunes, each over the whole horizon its tuneprog
certificate claims:

| | subtunes | ticks | divergences | identical ticks |
| --- | --- | --- | --- | --- |
| songs (0–10) | 11 | 88,486 | **0** | **88,486** |
| sound effects (11–31) | 21 | 23,277 | **0** | **23,277** |
| all | 32 | **111,763** | **0** | **111,763** |

**The strike.** Every change to the player, measured over the *whole* certified
horizon of all eleven earlier builds:

```
commando-song1  commando-song2  commando-song3  gt2-linus  gt2-do-it-again
sw-emomyst  sw-end-of-the-world  defmon-jazzpjazz  defmon-automatas
jch-guldkorn  jch-knob
```

**0 of 236,586 ticks differ.** The fetch loop leaves on its first pass where no
`row_ends_fetch` is declared and flushes nothing after the last row it takes;
`globals.after` is an empty list; `stopped` is all false; the two new payload
facts are keys no other family's guard names.

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
this family**, and saying so is §6.4's ordering check run before a field is
claimed rather than after: every edge write this player makes is a `ctrl`, its
`AD` and `SR` go through `$85` as absolute registers, so no permutation of the
three is observable. The object carries `(ctrl, ad, sr)` and the claim is that
it is unexercised, not that it is right. The blip's own `gated` guard is in the
anatomy's pseudocode and in the object, and no tick of these three builds runs
a blip on an ungated instrument. And **no subtune contains a single rest**: the
`$00` class is in the grammar, is parsed, is materialised, and across all 32
subtunes the count of rest events is **0** — which is what the anatomy's
"never used" says, measured.

**The print**, §6.2's six numbers against the source tuneprog's, for song 0:

| | lines | tokens | statements | blocks | header rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- |
| `tuneprog.md` | 1,277 | 18,310 | 1,271 | 5 | 6 | 11,648 |
| `trackerprog.md` | 1,723 | 13,356 | 1,633 | 7 | 90 | 6,024 |

Longer in statements and shorter in everything that measures information: the
score is materialised into rows, which adds lines, and the tick stops being
code, which removes tokens. `xz` is 52% of the source.

**The code.**

| | lines | what it is |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 1,488 (was 1,378) | §4 + §5, one procedure over the object |
| `deity_informant/trackerprog/printer.py` | 638 (was 618) | the flattened form and §6.2's numbers |
| `tools/trackerprog_follin.py` | 1,040 | the transliteration and the three builds |
| `tests/trackerprog/test_follin_oracle.py` | 158 | the three certificates and the family's own forms |
| `tests/trackerprog/test_universal_order.py` | 216 | hermetic snippets, one per form of §4.2 |

The player grew by **110 lines** to carry a sixth family. Most of it is the
order program — one procedure with one `elif` per step, plus the walk that runs
it — and the rest is three small openings: a stopped voice, a channel that steps
after the voices, and a stream that carries what lies past the tuning. It still
has neither Commando, nor GoatTracker, nor SID Wizard, nor defMON, nor JCH, nor
Follin in it.

## 8. The nine things the family was expected to force

Nine expectations came with this exemplar. Five held (1, 3, 4, 6, 8), two held
in kind but not in spelling (2, 5), and **two were wrong** (7, 9) — and saying
which is which is the point of writing it down.

| # | the expectation | what the code said |
| --- | --- | --- |
| 1 | the score is a stack machine, not an order→pattern nest, and it restores §6.2's struck `call`/`ret`/`for` | **held.** Six steps, one per control byte, 904 of them across 32 subtunes. What was not foreseen is that a call must name its **return** rather than take the next step (§4.2), because the 6502 pushes an address and the block list's order is not the program's |
| 2 | `set_register`, settled by I6 and never rendered, finally renders | **held in kind, not in spelling.** `$85` needed no new form: the named-register target, added for JCH's global channel, is a register of any voice written from a voice, and that is exactly what a raw list is. The expectation was right that the family needs it and wrong that the layer lacked it |
| 3 | no instrument table: the instrument is a run of commands, and §3.5's `Ins` is tested hardest | **held.** `instruments` has one entry with no columns and no accs, the note-on is its inline stream, and the cells it reads are filled by the score's own commands. §3.5 did not have to change — what changed is that an instrument can be *empty*, and the family is still complete |
| 4 | STATE that includes code bytes the tick rewrites | **held, and it cost nothing.** Six immediates and a handler's two data bytes are cells like any other; `state0` reads them at their addresses. The tuneprog layer had already done the work (`trace.py`'s `cells = code & (written_init \| written_play)`), and the trackerprog inherits it without a word |
| 5 | the blip is a note-on phase *after* the note, the mirror of `early` | **held in kind, not in spelling.** It is not a prelude at all and not an `Acc`: it is a countdown cell the note-on loads and two guarded rows that fire when it runs out. And its own `gated` guard measures **0** on all three builds, which §7 records rather than hides |
| 6 | a third spelling of the filter owner | **held.** A cell `$89` writes, against SID Wizard's constant and JCH's table byte — and the guard is the same one in all three. Removing it costs 318 ticks |
| 7 | the note-table overrun, §6's own case, which no exemplar had taken | **held, and it corrected the plan.** It is a `beyond` producer as §6 says. But the bound is not the score's: the index is one byte and every producer of it is an eight-bit add, so the record is 159 words and not a set computed from the note bytes the score holds. A score-derived bound was written first and two sound effects walked straight past it |
| 8 | `play`'s return value is a tick output no family has had | **wrong, and it is not an output.** `A = $7B\|$7C\|$7D` is read by the *game*, not by the schedule, and the observable is what the chip saw. It is not in the object, it is not in the certificate, and this document says so instead of claiming it |
| 9 | `commit_order` gets a sixth measurement | **wrong: it gets none.** Every edge write this player makes is a `ctrl`; `AD` and `SR` reach the chip through `$85` as absolute registers, in `prod`. Both other orders diverge on **0** of all three builds, so the family measures the field at zero and the object's value is a spelling, not a claim |

Two more the exemplar produced without being asked: the two corrections to the
anatomy in §6, both of which are load-bearing in ticks and neither of which any
static census would have found.
