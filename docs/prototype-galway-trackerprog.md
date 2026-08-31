# Prototype: Comic Bakery as a trackerprog — the ninth family, and the loop that nests

A **hand transliteration** of Martin Galway's Comic Bakery player (anatomy
[§3.2](playroutine-anatomy.md)) into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
defMON ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)),
JCH ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)),
Follin ([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md)),
Blackbird ([prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md))
and Walker ([prototype-walker-trackerprog.md](prototype-walker-trackerprog.md)),
and certified against the tune's own player on the PcodeVM.

**This is the last of the anatomy's nine, and the last row of architecture §9.1's
open work.** Galway had no certificate at all when this began: the row said
"certify at 15 s, not run to length", and both halves are now done — a
fourteen-subtune front-end certificate
([galway-comic-bakery.json](certificates/galway-comic-bakery.json)) and this
reading of it.

Six results:

1. **Every subtune renders, and all of it.** 14 subtunes, **29,911 ticks**,
   **0 divergences** on §2's observable, and per register the two write lists
   agree value for value and in order (`same_per_register_order` on every one).
   Three are the sequenced music at 9,450 ticks each (3:09, the HVSC length of
   the main theme), three are jingles that end, and eight are three-voice sound
   effects with no sequencer at all.
2. **The layer gained a stack, and the family forced it.** §3.6 said the counted
   loops "do not nest by having one cell". Galway pushes a loop's start *and* its
   count onto the same 8-deep stack its calls use, and the main theme opens a
   loop inside a live one — from tick 3,072, six times on voice 1 and three on
   voice 2. `loopcnt`/`loopstart` become `loopstack`, and the strike is **0 of
   Follin's 111,763 ticks**: the sixth family's loops do not nest, so a stack of
   depth one is the register it had.
3. **A `stop` stops a *sequencer*, and which is one datum.** Follin's `$86`
   clears a voice's active flag and its whole per-frame block is skipped
   (`if active[v] >= 0 goto next voice`, anatomy §3.6.3). Galway's eighth `Ret`
   clears the run bit and *returns from the voice's routine* — the engine plays
   the note out and frees the chip several hundred ticks later. `meta.stop ∈
   {voice, sequencer}`, defaulted to what the eight earlier families have. The
   eight effect subtunes are that datum at its limit: all three voices stopped
   from tick 0, no score at all, and the whole certificate is the engine over
   the record `StartEffect` left.
4. **The instrument materialises and the engine does not, and one rule decides
   which.** `Moke`, `FLoad` and the three `load` commands write the S record the
   *next* note will copy, so §6 spends them: **134 distinct records over the set
   and not one load command**. `DMoke` writes the *live* D record — the
   gradients, the counters, the release timer — so it stays a §3.6 command:
   **18 of them**, each one `sets` on one engine cell.
5. **The TEST-bit pulse is a datum, because one of the three copies does not make
   it.** `$8354 STA $D404` and `$899C STA $D412` send `wave|8` to voice 0's and
   voice 2's `ctrl`; `$8678 STA $D409` sends it to voice **1's own `pw_lo`**,
   where its `ctrl` is `$D40B`. Under §2 rule 2 the stray pulse write is
   invisible — the real pulse low follows it — and under rule 1 the missing
   second `ctrl` write is not. The object carries `testpulse = [1, 0, 1]`, and
   the anatomy's §3.2.5 is corrected: the click is two of the three voices.
6. **The print is half the program it was read from.** `xz -9e` of the object's
   print against the source `tuneprog.md`'s: **4,924 against 9,948** for the main
   theme, 3,132 against 6,780 and 3,544 against 8,592 for the other two.

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 the prose-only rows, corrected · 6 finding the data · 7 measurements
· 8 boundaries.

---

## 1. The object

`tools/trackerprog_galway.py`. One subtune at a time:

```bash
tools/trackerprog_galway.py Comic_Bakery.sid --song 1 --out out/tp \
    --source docs/certificates/galway-comic-bakery.json --certify
```

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
| `score` | one byte program per voice: blocks of rows and section 3.6's `ret`/`call`/`jump`/`mark`/`loop`/`stop` |
| `instruments` | the S records the horizon's notes copy, interned; every one carries the same engine and differs only in the cells it starts it from |
| `globals.commit` | `$D418 = vol | mode` and the three filter shadows, constant here and written every frame |
| `state0` | the post-init image: `init` clears neither S nor D nor the duration table, so the residue *is* the initial state |

---

## 2. The mapping

| the tuneprog says | the trackerprog says |
| --- | --- |
| `voice[v].timer` `DEC`/`BEQ` (`$860E`) | `meta.tempo`, a divider whose reload is the row's own `dur` |
| `T91B6[cursor_00F0]`, a byte at a time | the score: blocks of rows over the state `(pc, transpose, record, stack empty)` |
| `switch b8647` over `vt0`/`vt1`/`vt2` | nothing — the patched `JMP` is the lift's, and the fifteen handlers are the order grammar and the commands |
| `Ret` `Call` `Jmp` `CT` `JT` `For` `Next` | `ret` `call` `jump` `jump` `mark` `loop`, and `stop` where the stack runs out |
| `Moke` `FLoad` `load10` `load14` `load5` | nothing: the record they build *is* the instrument (§4.3) |
| `DMoke` | one command, one `sets`, one engine cell |
| `Transp`, and `CT`/`JT`'s third operand | `play`'s own `transpose` column, so a block read under two transposes is two blocks |
| `$8659` note-on | `streams.note_on`: `sr ad` · `wave|8` · `wave & $F7` · `pw` · the engine's cells |
| `$87D3` gate and release | `streams.gate`: the relative arm, the absolute arm, and the seven registers the kill writes |
| `$881B` pulse ramp | `accs.pm0`/`pm1` over `pcurr`, `delta` the sixteen-bit gradient cells |
| `$887D` frequency ramp | `accs.fm0`..`fm3` over `fcurr`, `fmbend` the gradient the delay adds |
| `$88AC` arpeggio | `streams.arp`, a pitch stream whose rows are the eight offset cells |
| `$816A` | `globals.commit`, four registers ahead of the voices |
| `$8D05`/`$8D2C`/`$8D53` residue | `state0.cells` — 35 per voice, read and never zeroed |

---

## 3. The certificate

Every subtune over its whole horizon, against the tune's own player on the
PcodeVM (`--certify`):

| song | what it is | ticks | divergences | per-register order |
| --- | --- | ---: | ---: | --- |
| 1 | main theme | 9,450 | 0 | identical |
| 2 | second loop | 9,450 | 0 | identical |
| 3 | third loop | 9,450 | 0 | identical |
| 4–6 | jingles, each ending | 112 · 138 · 577 | 0 | identical |
| 7–14 | three-voice sound effects | 26 · 21 · 101 · 356 · 31 · 31 · 121 · 47 | 0 | identical |
| | **total** | **29,911** | **0** | |

"Identical" is `same_per_register_order`: per register the two write lists agree
value for value and in order. The remaining permutation is between register
*classes* — this player writes the chip as it goes and the universal player
commits its global channel after the voices — which §2 drops and the certificate
names.

The horizons are the front-end certificate's own
([galway-comic-bakery.json](certificates/galway-comic-bakery.json)): 14
subtunes, 29,911 ticks, 0 divergences, 0 envelope traps, 11 of 14 complete by
periodicity, 20 procedures and 1,485 statements. It reproduces through
`tuneprog_recert.py` field for field.

---

## 4. What the layer needed

Two forms, both measured before they were written, and both of them the *order
program*'s rather than the sound's — which is the family's own shape: its engine
is two piecewise-linear generators the schema already had, and its score is a
byte-code interpreter with a stack.

### 4.1 The counted loop nests, so it is a stack

§3.6 read Follin and wrote: "`mark` and `loop` are two steps, not one `for` —
over one counted-loop register per voice that nothing saves or restores, so the
object says the loops do not nest by having one cell."

Galway's `For` (`$871A`) pushes three bytes — the loop's start address and its
count — onto the **same 8-deep stack** the `Call` handler pushes return
addresses on, and `Next` (`$8735`) decrements the count at `SP+1` and pops when
it reaches zero. Loops therefore nest, and interleave with calls, and this tune
does both: over the main theme's 9,450 ticks the render's own loop stack reaches
depth 2 from tick 3,072, six times on voice 1 and three on voice 2. With one
register the second `mark` overwrites the first, the outer loop then runs the
inner one's count, and the score plays the wrong block: the first divergence
lands at tick 3,840 with two voices a whole tone out.

The player's change is four lines — `loopcnt` and `loopstart` become one
`loopstack` per voice — and it is **free by construction for the sixth family**,
which is the check and not the argument: a family whose loops never nest sees a
stack of depth one, which is the register it had. The strike says so on the
render: **0 of Follin's 111,763 ticks differ** across all 32 subtunes.

### 4.2 A stop stops a sequencer, or a voice, and which is data

`voice()` began "a voice its own score stopped runs no clock" and returned
before every phase. That is Follin's, exactly: `$86` clears `$7B+v` and the
per-frame block tests it first, `if active[v] >= 0 goto next voice`
(anatomy §3.6.3) — the modulators, the gate and the filter all skipped.

Galway's is not. The eighth `Ret` (`$86FE` → `$8713`) clears the run bit in `$F9`
and returns from the *voice routine*, so the sequencer stops and the engine goes
on: the note releases over its `vadsc` frames, counts `vrc` more, writes its
seven registers to zero and frees the chip. In song 4 the last voice stops at
tick 102 and the certificate runs to 112; in song 6 voice 1 stops at 522 of 577.

So `meta.stop ∈ {voice, sequencer}`, defaulted to `voice`, and the eight earlier
families are untouched by construction. Two consequences the object states
rather than the player assuming:

- a stopped voice **runs no clock** — the source freezes `CLOCK[v]` because the
  `DEC` is inside the run-bit test — and every other phase runs;
- the tick a voice's sequencer ends on is that voice's *last* — the source's own
  `RTS` — so the phases after the row do not run on it. Without that the engine
  gets one extra tick and songs 4, 5 and 6 diverge at 98, 83 and 522.

**The eight effect subtunes are this datum with nothing else in them.** `init`
calls `StartEffect` three times, which writes the chip and fills D directly; no
run bit is ever set, no note is ever read, and the object is `state0` plus the
engine. They certify at 26 to 356 ticks with an empty score.

### 4.3 A load is storage and a poke is not

The family's own words for it (anatomy:613-615) are that the instrument *is*
score data. Both halves of that are true and they need opposite treatment, which
is the rule §6 gives and this is its cleanest exemplar:

- `Moke dst, val`, `FLoad dst, len, src` and `load10`/`load14`/`load5` write the
  **S record**, which nothing reads until a note copies it into D. The value at
  the note is a fact of the walk, so the walk interns it: 62 records for the main
  theme, 134 distinct over the set, and **no load survives as a command**.
- `DMoke off, val` writes the **D record**, which the engine is reading now. It
  cannot be materialised into anything, and it does not need to be: every cell it
  names is a cell §5 already has, so it is a `sets` and the command is named by
  the cell — `vrc:FF`, `fmc:07`, `g6:E2`, `fcurr.lo:72`.

The 18 commands the set keeps are all `DMoke`s and they reach eleven cells: the
four gradient bytes of FMG0 and FMG3, the control byte, the delay, two segment
counters, both halves of the live frequency, and the two envelope timers.

### 4.4 The state a block is read under carries three things

Follin's blocks are states `(pc, note-length)`, and the sixth family needed a
fixpoint because a procedure returning under two lengths would want two return
addresses for one pushed one. Galway's are `(pc, transpose, record, stack
empty)`, for the same reason and three times over:

- **transpose**, because `Transp`/`CT`/`JT` set `TR[v]` and a block called from
  two places inherits it. It is §3.6's own `play` column, so the block carries
  the notes it reads and the step carries the transpose;
- **record**, because a block's notes copy whatever S holds (§4.3). One block
  of voice 2's is reached under two wave bytes, `$29` and `$49`, from two
  `Moke`s — the instrument column the score does not have, spelled as a poke;
- **whether the stack is empty**, because that is what says a `Ret` is a `ret`
  and not a `stop`. The same byte at `$9D2D` is both, in one subtune.

`Sequencer.pop` refuses a call site that returns under two states, and none does.

### 4.5 An arm that steps ends its generator, and a reload does not

One shape, three times: a guard that reads a cell an earlier arm of the same
tick has moved is not the guard the arm was chosen on. The source leaves each
generator by `JMP`, so a segment that spends its counter does **not** let the next
segment run on that tick; a reload continues the loop and the segment after it
does run. The object says both with one flag per generator (`pmdone`, `fmdone`)
that the ending arms set and the reload does not, and the same shape a third
time in the gate: the relative release clears bit 3 of the wave byte, which puts
the *next* tick on the absolute path and not this one.

Without them the segments cascade: the frequency ramp runs two segments on the
tick one spends its counter, and the release counter runs down early enough
that the main theme's voice 0 is silenced at tick 2,038 of 9,450.

---

## 5. The prose-only rows, corrected

The anatomy describes this family byte by byte and no certificate covered it, so
this reading is the first thing that could check it. Four corrections, each one
found by the render and confirmed in the disassembly:

| the anatomy says (§3.2) | the tune says |
| --- | --- |
| §3.2.5, "the TEST-bit pulse at note start (Galway's click) still resets the oscillator phase" | two of the three copies. `$8678` writes `wave\|8` to `$D409`, which is voice 1's *pulse low*, where the other two copies write their own `ctrl`. Voice 1 has no click and one `ctrl` write where the others have two |
| §3.2.3, the engine's `4-segment version of the PM loop over FMG0..3` | four segments over `FMD0C..3C`, and `FMG3` is also the *bend* the delay adds when `FMC & 2` — `$8906` enters the fourth segment's adder without touching its counter. The fourth segment is reached, 13 times over the set, and only in a sound effect |
| §3.2.1, "FM four-segment ramp or the arp list" | and the same eight bytes are both: `D[$00..$07]` are the four sixteen-bit gradients with `FMC & 8` clear and the eight arpeggio offsets with it set, and `D[$0A]`/`D[$0B]` are `FMD2`/`FMD3` or the base note and the last index. The object keeps eight byte cells and reads them either way |
| §3.2.4, the loop policy `bit7 loop (reload FCURR too)` | reached, and only by a sound effect: `fmreload_all` fires 13 times over the whole set and `pmreload_all` never |

One the anatomy already had right and the render is glad of: **`init` clears
neither S nor D nor the duration table**, so the residue is the initial state
(§3.2.1's "a decompiler must treat D/S as initialised from the image, not
zero"). The main theme's `dmoke $1C,$FF` is in the object's command list, and
zeroing `state0.cells` changes the render inside 64 ticks.

---

## 6. Finding the data

Everything the tool reads it reads off the post-init image or off its own walk
of the score; nothing is a constant of the family. Three places that took care:

**The duration table is voice 2's stack.** `IDRT` lives at `$8CF4`, which is
`S2 + $34`, and the song loads it with `fload $44` — so a note's length is a
byte of an instrument record, and `IDRT[0]` is literally `ST2C[7]`, the count
slot of voice 2's outermost `For`. The walk therefore simulates the whole
`$8C56..$8D04` band as one flat array rather than three records, which is what
the player does.

**A raw duration of 0 is 256 frames, and the clock is a byte.** Nine rows of the
set carry one. The object states the length — 256, which is what the row is —
and the gate's relative compare reads `dur & $FF`, which is what the player's own
counter holds. The two differ on exactly the tick the row is read, and the mask
is the honest read rather than a horizon that happens not to notice.

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
source's own `AND #$81` and both would be a divergence rather than a silence if
a subtune reached them.

The score, over all fourteen subtunes: **325 blocks, 1,012 rows** — 918 notes,
63 rests and 31 pokes — and an order program of 126 calls, 69 returns, 38 jumps,
36 marks, 35 loops and 21 stops.

The print, against the `tuneprog.md` each object was read from (`xz -9e`):

| subtune | trackerprog | tuneprog | rows | instruments |
| --- | ---: | ---: | ---: | ---: |
| main theme | 4,924 | 9,948 | 406 | 62 |
| song 2 | 3,132 | 6,780 | 183 | 17 |
| song 3 | 3,544 | 8,592 | 287 | 43 |

---

## 8. Boundaries

- **The free bits are not modelled.** `$F9` bits 3..5 say the chip is not held by
  a sound effect, and the note-on tests them. Every subtune here either owns all
  three voices for its whole horizon or reads no note at all, so the bit is a
  constant the tool asserts rather than a cell the object carries. A build that
  started an effect *over* running music would need it, and Comic Bakery's own
  API can do that — the game does it and the PSID entry points cannot.
- **`Code` (`$DA`) is refused by name.** It pushes a return address and jumps
  through an operand into the game's own code; no subtune reaches it, and there
  is nothing to render if one did.
- **The order program ends where the horizon does.** A block the certified ticks
  never reach is an empty pattern with a `stop`, and a call the horizon never
  returns from names no return step. Both are the `horizon` terminator said in
  the score rather than in `end`.
- **The three music subtunes are `horizon` and not `complete`.** Their ramps
  drift — the same aperiodicity Commando has (architecture §5.2) — so 188.53
  seconds is a horizon and not a period. It is past the HVSC length of all three.
