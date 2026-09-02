# Prototype: Comic Bakery as a trackerprog — the ninth family, and the loop that nests

A **hand transliteration** of Martin Galway's Comic Bakery player (anatomy
[§3.2](playroutine-anatomy.md)) into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders [Commando](prototype-commando-trackerprog.md),
[GoatTracker 2](prototype-goattracker-trackerprog.md),
[SID Wizard](prototype-sidwizard-trackerprog.md),
[defMON](prototype-defmon-trackerprog.md), [JCH](prototype-jch-trackerprog.md),
[Follin](prototype-follin-trackerprog.md),
[Blackbird](prototype-blackbird-trackerprog.md) and
[Walker](prototype-walker-trackerprog.md), and certified against the tune's own
player on the PcodeVM.

Five results. **Every subtune renders, and all of it**: 14 subtunes, **29,911
ticks**, **0 divergences** on §2's observable, `same_per_register_order` on every
one — three sequenced music subtunes at 9,450 ticks each (3:09, the HVSC length
of the main theme), three jingles that end, eight three-voice sound effects with
no sequencer. **The counted loop nests, so it is a stack** (§4.1), struck at **0
of Follin's 111,763 ticks**. **A `stop` stops a *sequencer***, and which is one
datum (§4.2) — the eight effect subtunes are that datum at its limit, all three
voices stopped from tick 0 and the whole certificate the engine over the record
`StartEffect` left. **The instrument materialises and the engine does not**: the
load commands write the S record the next note copies, so §6 spends them (134
distinct records, not one load command), while `DMoke` writes the live D record
and stays a §3.6 command, 18 of them (§4.3). **The TEST-bit pulse is a datum**:
`$8354 STA $D404` and `$899C STA $D412` send `wave|8` to voice 0's and voice 2's
`ctrl` while `$8678 STA $D409` sends it to voice **1's own `pw_lo`**, whose
`ctrl` is `$D40B`, so the object carries `testpulse = [1, 0, 1]` (§5).

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 the anatomy's rows, corrected · 6 finding the data · 7 measurements
· 8 boundaries.

---

## 1. The object

`tools/trackerprog_galway.py Comic_Bakery.sid --song 1 --source
docs/certificates/galway-comic-bakery.json --certify`, one subtune at a time:

| section | what this tune puts there |
| --- | --- |
| `meta.tempo` | a divider: cell `dur`, `step -1`, the row at `dur == 0`, the row's own length reloading it — the three zero-page `DEC`/`BEQ` counters at `$FA..$FC` |
| `meta.tick` | `row` · `commit` · `{stream: gate}` · `machine` — the sequencer, then the engine, both writing the chip as they go |
| `meta.row` | `commands` · `ins` · `note` when `sounds` · `stream note_on` when `sounds` |
| `meta.stop` | `sequencer` (§4.2) |
| `pitch` | the two byte tables at `$8D92`/`$8DF1`, one u16 row per note over the span the horizon asks for (95 of them for the main theme) |
| `streams.note_on` | eleven guarded rows: five register acts and six that fill the engine's cells |
| `streams.gate` | the two gate modes, the release timer and the hard kill, as guarded rows over `vadsc`/`vrc`/`vwfg` |
| `streams.arp` | eight rows, one per offset cell, read backwards with the last index a cell |
| `accs` | `pm0`/`pm1` over `pcurr` and `fm0`..`fm3` over `fcurr`, plus each generator's delay, bend, reload and no-op arms — thirteen of the fifteen taken (§7) |
| `score` | one byte program per voice: blocks of rows and §3.6's `ret`/`call`/`jump`/`mark`/`loop`/`stop` |
| `instruments` | the S records the horizon's notes copy, interned; every one carries the same engine and differs only in the cells it starts it from |
| `globals.commit` | `$D418 = vol \| mode` and the three filter shadows, constant here and written every frame |
| `state0` | the post-init image: `init` clears neither S nor D nor the duration table, so the residue *is* the initial state |

---

## 2. The mapping

| the tuneprog says | the trackerprog says |
| --- | --- |
| `voice[v].timer` `DEC`/`BEQ` (`$860E`) | `meta.tempo`, a divider whose reload is the row's own `dur` |
| `T91B6[cursor_00F0]`, a byte at a time | the score: blocks of rows over the state `(pc, transpose, record, stack empty)` |
| `Ret` `Call` `Jmp` `CT` `JT` `For` `Next` | `ret` `call` `jump` `jump` `mark` `loop`, and `stop` where the stack runs out |
| `Moke` `FLoad` `load10` `load14` `load5` | nothing: the record they build *is* the instrument (§4.3) |
| `DMoke` | one command, one `sets`, one engine cell |
| `Transp`, and `CT`/`JT`'s third operand | `play`'s own `transpose` column, so a block read under two transposes is two blocks |
| `$8659` note-on | `streams.note_on`: `sr ad` · `wave\|8` · `wave & $F7` · `pw` · the engine's cells |
| `$87D3` gate and release | `streams.gate`: the relative arm, the absolute arm, and the seven registers the kill writes |
| `$881B` pulse ramp | `accs.pm0`/`pm1` over `pcurr`, `delta` the sixteen-bit gradient cells |
| `$887D` frequency ramp | `accs.fm0`..`fm3` over `fcurr`, `fmbend` the gradient the delay adds |
| `$88AC` arpeggio | `streams.arp`, a pitch stream whose rows are the eight offset cells |
| `$8D05`/`$8D2C`/`$8D53` residue | `state0.cells` — 35 per voice, read and never zeroed |

---

## 3. The certificate

Every subtune over its whole horizon, against the tune's own player on the
PcodeVM:

| song | what it is | ticks | divergences | per-register order |
| --- | --- | ---: | ---: | --- |
| 1 | main theme | 9,450 | 0 | identical |
| 2 | second loop | 9,450 | 0 | identical |
| 3 | third loop | 9,450 | 0 | identical |
| 4–6 | jingles, each ending | 112 · 138 · 577 | 0 | identical |
| 7–14 | three-voice sound effects | 26 · 21 · 101 · 356 · 31 · 31 · 121 · 47 | 0 | identical |
| | **total** | **29,911** | **0** | |

"Identical" is `same_per_register_order`. The remaining permutation is between
register *classes* — this player writes the chip as it goes, the universal player
commits its global channel after the voices — which §2 drops. The horizons are
the front-end certificate's own
([galway-comic-bakery.json](certificates/galway-comic-bakery.json)): 29,911
ticks, 0 divergences, 0 envelope traps, 11 of 14 complete by periodicity.

---

## 4. What the layer needed

Two forms, both the *order program*'s rather than the sound's: the engine is two
piecewise-linear generators the schema already had, and the score is a byte-code
interpreter with a stack.

### 4.1 The counted loop nests, so it is a stack

`For` (`$871A`) pushes three bytes — the loop's start address and its count —
onto the **same 8-deep stack** the `Call` handler pushes return addresses on, and
`Next` (`$8735`) decrements the count at `SP+1` and pops at zero, so loops nest
and interleave with calls: over the main theme's 9,450 ticks the loop stack
reaches depth 2 from tick 3,072, six times on voice 1 and three on voice 2. With
one register the second `mark` overwrites the first and the score plays the wrong
block, first diverging at tick 3,840 with two voices a whole tone out.

### 4.2 A stop stops a sequencer, or a voice, and which is data

Galway's eighth `Ret` (`$86FE` → `$8713`) clears the run bit in `$F9` and returns
from the *voice routine*, so the sequencer stops and the engine goes on: the note
releases over its `vadsc` frames, counts `vrc` more, writes its seven registers to
zero and frees the chip. Song 4's last voice stops at tick 102 against a horizon
of 112, song 6's voice 1 at 522 of 577. Two consequences the object states: a
stopped voice **runs no clock**, the source freezing `CLOCK[v]` inside the run-bit
test while every other phase runs; and the tick its sequencer ends on is that
voice's *last*, the source's own `RTS`, so the phases after the row do not run on
it — without which songs 4, 5 and 6 diverge at 98, 83 and 522.

### 4.3 A load is storage and a poke is not

`Moke dst, val`, `FLoad dst, len, src` and `load10`/`load14`/`load5` write the
**S record**, which nothing reads until a note copies it into D, so the walk
interns the value at the note: 62 records for the main theme, 134 over the set.
`DMoke off, val` writes the **D record** the engine is reading now, and every
cell it names is a cell §5 already has, so it is a `sets` named by the cell —
`vrc:FF`, `fmc:07`, `g6:E2`, `fcurr.lo:72`. The 18 the set keeps reach eleven
cells: the four gradient bytes of FMG0 and FMG3, the control byte, the delay, two
segment counters, both halves of the live frequency, the two envelope timers.

### 4.4 The state a block is read under carries three things

`(pc, transpose, record, stack empty)`. **Transpose**, because `Transp`/`CT`/`JT`
set `TR[v]` and a block called from two places inherits it — §3.6's `play`
column. **Record**, because a block's notes copy whatever S holds (§4.3): one
block of voice 2's is reached under two wave bytes, `$29` and `$49`, from two
`Moke`s. **Whether the stack is empty**, because that says whether a `Ret` is a
`ret` or a `stop`; the same byte at `$9D2D` is both, in one subtune.

### 4.5 An arm that steps ends its generator, and a reload does not

The source leaves each generator by `JMP`, so a segment that spends its counter
does **not** let the next run on that tick, while a reload continues the loop and
the segment after it does run. One flag per generator (`pmdone`, `fmdone`) that
the ending arms set and the reload does not says both, and the gate says it a
third time: the relative release clears bit 3 of the wave byte, putting the
*next* tick on the absolute path. Without them the frequency ramp runs two
segments on the tick one spends its counter and the main theme's voice 0 is
silenced at tick 2,038.

---

## 5. The anatomy's rows, corrected

Four corrections, each found by the render and confirmed in the disassembly:

| the anatomy says (§3.2) | the tune says |
| --- | --- |
| §3.2.5, "the TEST-bit pulse at note start (Galway's click) still resets the oscillator phase" | two of the three copies. `$8678` writes `wave\|8` to `$D409`, which is voice 1's *pulse low*, where the other two copies write their own `ctrl`. Voice 1 has no click and one `ctrl` write where the others have two |
| §3.2.3, the engine's `4-segment version of the PM loop over FMG0..3` | four segments over `FMD0C..3C`, and `FMG3` is also the *bend* the delay adds when `FMC & 2` — `$8906` enters the fourth segment's adder without touching its counter. The fourth segment is reached, 13 times over the set, and only in a sound effect |
| §3.2.1, "FM four-segment ramp or the arp list" | and the same eight bytes are both: `D[$00..$07]` are the four sixteen-bit gradients with `FMC & 8` clear and the eight arpeggio offsets with it set, and `D[$0A]`/`D[$0B]` are `FMD2`/`FMD3` or the base note and the last index. The object keeps eight byte cells and reads them either way |
| §3.2.4, the loop policy `bit7 loop (reload FCURR too)` | reached, and only by a sound effect: `fmreload_all` fires 13 times over the whole set and `pmreload_all` never |

One the anatomy already had right: **`init` clears neither S nor D nor the
duration table**, so the residue is the initial state (§3.2.1). Zeroing
`state0.cells` changes the render inside 64 ticks.

---

## 6. Finding the data

Everything the tool reads it reads off the post-init image or off its own walk of
the score; nothing is a constant of the family. Three places that took care:

**The duration table is voice 2's stack.** `IDRT` lives at `$8CF4` = `S2 + $34`
and the song loads it with `fload $44`, so a note's length is a byte of an
instrument record and `IDRT[0]` is literally `ST2C[7]`, the count slot of voice
2's outermost `For`. The walk simulates `$8C56..$8D04` as one flat array rather
than three records, which is what the player does.

**A raw duration of 0 is 256 frames, and the clock is a byte.** Nine rows of the
set carry one: the object states 256, the gate's relative compare reads `dur &
$FF`, and the two differ on exactly the tick the row is read.

**The arpeggio's base is a cell, not the note.** `$86C0` builds `D[$0A]` from the
*raw* note byte plus the transpose without the `$5E` exception the pitched path
has, so the object reads `fmd2` rather than the row's note — which also means a
`DMoke` on `$0A` would move it, and the arm is there for one.

---

## 7. Measurements

Fifteen accumulator arms, and what the certified set takes:

| arm | what it is | taken |
| --- | --- | ---: |
| `pm0` / `pm1` | the pulse ramp's two segments | 30,911 / 12,178 |
| `pmdelay` | its delay, spending `pmdly` and writing nothing | 10,594 |
| `pmreload` | the counters reloaded, the value kept (`PMC & $01`) | 910 |
| `pmnoop` | the ramp finished, the value written every frame regardless | 3 |
| `pmreload_all` | the value reloaded from `PINIT` too (`PMC & $80`) | **0** |
| `fm0` / `fm1` / `fm2` / `fm3` | the frequency ramp's four segments | 16,392 / 21,405 / 6,931 / **13** |
| `fmbend` | `FMG3` added during the delay (`FMC & $02`) | 2,040 |
| `fmdelay` | the delay without it | 23,376 |
| `fmreload` | the counters reloaded (`FMC & $01`) | 2,719 |
| `fmreload_all` | `FCURR` reloaded from the note too (`FMC & $80`) | 13 |
| `fmnoop` | the ramp finished, the frequency written regardless | **0** |

The two the set never takes are stated and not trimmed: both are arms of the
source's own `AND #$81`, and reaching one would be a divergence, not a silence.

The score, over all fourteen subtunes: **325 blocks, 1,012 rows** — 918 notes, 63
rests, 31 pokes — and an order program of 126 calls, 69 returns, 38 jumps, 36
marks, 35 loops and 21 stops. The three music subtunes print at 4,924, 3,132 and
3,544 `xz -9e` over 406, 183 and 287 rows and 62, 17 and 43 instruments; the
object against the tune's own load band is §9.1 of
[prototype-trackerprog.md](prototype-trackerprog.md).

---

## 8. Boundaries

- **The free bits are not modelled.** `$F9` bits 3..5 say the chip is not held by
  a sound effect and the note-on tests them, but every subtune here either owns
  all three voices for its whole horizon or reads no note at all, so the bit is a
  constant the tool asserts. A build that started an effect *over* running music
  would need it, and Comic Bakery's own API can do that.
- **`Code` (`$DA`) is refused by name.** It pushes a return address and jumps
  through an operand into the game's own code; no subtune reaches it.
- **The order program ends where the horizon does.** A block the certified ticks
  never reach is an empty pattern with a `stop`, and a call the horizon never
  returns from names no return step — the `horizon` terminator said in the score
  rather than in `end`.
- **The three music subtunes are `horizon` and not `complete`.** Their ramps
  drift — the same aperiodicity Commando has (architecture §5.2) — so 188.53
  seconds is a horizon and not a period, past the HVSC length of all three.
