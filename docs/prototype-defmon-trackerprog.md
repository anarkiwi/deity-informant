# Prototype: defMON as a trackerprog — the fourth family, one player

A **hand transliteration** of the two certified defMON tuneprogs
([prototype-automatas.md](prototype-automatas.md), anatomy
[§2](playroutine-anatomy.md) seventh column) into trackerprogs
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md))
and SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
and certified against each tune's own player on the PcodeVM.

Four results. **Both builds render on one object shape and one code path**:
*Automatas* over its whole **149,025-tick** horizon and *Jazzpjazz* over its
whole 1,799, **0 divergences** on §2's observable and the write lists identical
on all 150,824 ticks. **Multispeed is `rate`** (§4.10). **`horizon` is
exercised**: *Jazzpjazz* is `complete: false`, materialised 28 of the arranger's
72 steps. **The layer invariant holds at four families**; six of §4's ten forms
are in the player and four in the data only.

Reproduce; the long one needs a budget (architecture §11), three 45-second
invocations reaching the end:

```
tools/trackerprog_defmon.py $HVSC/MUSICIANS/G/Goto80/Jazzpjazz.sid \
    --source out/recert-main/goto80-jazzpjazz/certificate.json \
    --certify --out out/defmon-tp/goto80-jazzpjazz
    # Automatas: --budget 45 --resume out/defmon-tp/automatas.pkl, until it exits 0
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 the eight things the family was expected to force.

---

## 1. The object

`tools/trackerprog_defmon.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` renders it.

| section | Automatas | Jazzpjazz |
| --- | --- | --- |
| `meta` | 3 voices in order 0,1,2; `commit_order (sr, ad, ctrl)`; a **23-register image** flushed in the write-out's own order (`pw`, `freq`, `sr`, `ad`, `ctrl` per voice, then routing and volume); a **divider** row clock at `rate 8`, the entry running 8×/frame at 2,457 cycles; `tick: row ; machine`; the row never spends the tick | the same, `rate 1` at 16,422 cycles |
| `pitch` | one table, `base -36` and **292** entries: the tuning, the window the slide reads *below* it, and what the note column reaches *past* it (§4.9) | `base -36`, **169** |
| `streams` | **5**: `casa` and `casb` — the two sidTAB programs a voice runs, **358** rows over one row list; `pitch_out`, the oscillator's three producers; `filter`, the global cutoff channel; `voice_bit`, the routing masks | the same five, **97** sidTAB rows |
| `accs` | **5**: two slides (up, down), the pulse-width sweep's two directions and the bounce that turns it | the same five |
| `instruments` | **1** — defMON has no instrument record: this one is the voice's own machine, an `on_note` that resets the oscillator and six arms | **1** |
| `score` | 3 order programs of 168 `play` steps ending `jump 0`; **113** patterns, **1,621** events, **97** commands, every one a cascade re-point | 28 steps ending **`horizon`**; **32** patterns, **305** events, **11** commands |
| `globals` | one channel: the 16-bit cutoff accumulator, its step, its direction, its base and its floor; the routing byte; the volume byte; two flags | the same |

The five accumulator ids (`slide_up`, `slide_down`, `pw_down`, `pw_up`,
`pw_turn`) are labels in the data.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/{automatas,goto80-jazzpjazz}/tuneprog.md`) and the player's
own code.

| the player says | the trackerprog says | §5 row |
| --- | --- | --- |
| `tick(): if (call_counter & 7) != 0: sub() else: main()` | `meta.tempo.rate 8` — the row clock is a divider over the entry, every other phase running at the tick | §4.10 |
| `writeout()`: `sid[v].pw_lo ← voice[v].pw_lo`, `pw_hi`, `freq`, `sr`, `ad`, `ctrl ^ ctrl_eor`, then `res_route` and `mode_vol \| $F` | `meta.shadow.registers` — the 23 registers the image carries, in the order it writes them; the `ctrl` write is the `xor` of two cells (§4.4) | §3.1 (§4.1) |
| `filter()` writes `sid.cutoff_hi` directly, before the row and the cascades | `globals.streams: [filter]` — an `all` stream of six guarded rows, and a `globals.commit` to a register the image does not hold, which so reaches the chip on its own tick | new (§4.2) |
| `filter.acc ± filter.step`, the high byte floored at `b10CE` where it goes negative, then `+ b10CA + carry`, then `CMP`/`BCS` against the same floor, then `NOP` or `ASL` | those six rows, in order: the step and its carry — `carry_out(acc + step, 16)` going up and `borrow_out(acc − (step + 1), 16)` coming down — the floor on the accumulator, the byte, the floor on the byte twice, and the build's own shift | §5 filter sweep |
| `row_advance()`: `b10D9 & $80` reloads all three voices from `T1A00`/`T1A80` through `T1B00`/`T1C00`/`T1D00` at `cursor_10EB` | the score's three `Order` programs, one column each; `cursor_10EB`'s init value is the **subtune**, the `$FF` row's jump target is the *voice-1 column* of that row | §3.6 |
| the first pattern to reach an end row ends all three, and its low nibble is every voice's next count | the score is materialised **per arranger step**, each voice's play step being its own pattern *cut where the step ends* | §6 (§4.7) |
| `p_112A()`: `flag [A] [B] [note]`, bit 7 end · bit 6 sidcall A · bit 5 sidcall B · bit 4 note · bits 3–0 the count | `Event{sounds, note, arm, dur}` — the flag's token class is spent, `dur` is the count plus one, and a value that is not in the pitch table is not a note | §3.6 |
| the note byte: `b12CC ← byte`, `freq_idx ← b12CC`, `acc ← 0`, `pw_hi[v] ← 0` | `sounds`, `note`, and the one instrument's `on_note`: `@freq_idx := note ; @acc := 0 ; @osc := 0` | §3.5 |
| a sidcall byte: `rec2[k].cursor ← byte`, `rec2[k].timer ← 0` | a row command, `cascade.a:NN` / `cascade.b:NN`, whose whole record is one `point` | §3.6 `point` |
| `cascades()`: six copies of `if timer == 0: fire; elif timer < 0: dead; else timer -= 1`, over `T1800`/`T1900` (the row's address) and `T1E00` (its delay) | two streams, `casa` and `casb`, one cursor per voice each, over one row list; `T1900[i] == 0` is the row's **jump**, `T1E00[i] & $80` is the stream's **terminator** and its low bits the hold | §3.3 (§4.8) |
| `row_apply()`: a variable-length record of register columns behind two mask bytes | a §3.3 row's `sets`, in the record's own order — `@ctrl`, `@ctrl_eor` and the `ctrl` they xor to, `ad`, `sr`, `@freq_idx := offset + note`, `@osc`, the pulse pair, `@pwstep`, the routing byte, the volume byte, the cutoff base, and the filter's two | §3.3, the form it was written from |
| `res_route ← res_route & b1021[v]` or `(res_route & $F) \| byte \| b1020[v]` | `#res_route` and the `voice_bit` stream's `value`/`mask` columns; the third arm (the byte written outright) is a `trap` the horizon never takes | §3.7 |
| `oscillator()` with `pw_hi[v] == 0`: `freq_lo ← FREQ_LO[fi] + v`, `freq_hi ← FREQ_HI[fi]`, and the carry it leaves | `pitch_out` row 1 — two producers, no carry into the high byte, and `!C` for the sweep below | §4 producers |
| `pw_hi[v] >= $80`: `acc ± FREQ[2·(osc & $3F)]`, sign from `bit(osc, 6)`; then `freq ← acc + FREQ[fi]` | two `Acc`s on the voice's `acc` cell, `delta {"tuned": 2·(osc & $3F) − 36}`, `phase const 0/1`; `pitch_out` row 3 adds the tuning at the note | §5 `tabcell`, spelled `tuned` (§8) |
| `0 < pw_hi[v] < $80`: `freq ← FREQ[fi] + (FREQ[Y−24] − FREQ[Y−25])` | `pitch_out` row 2, a `trap`: no `osc` byte of either tune is in that range | §5 `interval` (§8) |
| `voice[v].pw_lo ∓ (b101E …)` with the bounce at `pw_hi == 0` and `== $F` | `pw_down` / `pw_up` on `shadow.pw`, each with the endpoint as `policy.reload` and `delta_when`, and `pw_turn` on the flag the skipped delta leaves | §5 reflect |
| `voice[v].pw_lo -= (b101E + (1 - carry_2))` | `delta {"sub": [{"add": [pwstep, 1]}, {"flag": "C"}]}` — the carry the frequency add of the same voice's tick left, which that row writes as `carry_out(…, 8)` | §5 a live carry (§8) |
| `p_14CB`: `$D41B` decides `b10CE` and `b10D4` | the filter channel's floor and its shift, read off the image the tick sees | §6 |
| `init()`'s `io[$DC04] = $98`, `io[$DC05] = 9` | nothing: the cadence is `meta.cycles_per_tick`, taken from the source certificate | §3.1 |

What disappears: `$FB`/`$FC` and the `(ptr),Y` walk; the six unrolled cascade
copies and the three unrolled voice copies; every SMC immediate — `$1023`,
`$1025`, `$102D`, `$1037`, `$1039`, `$103B`, `$103D`, `$10AF`, `$10B6`, `$10B9`,
`$10CA`, `$10CE`, `$10D9`, `$10EB`, `$1129`, `$1165` — is a cell, a global or a
datum of `meta`; the `LAX`/`SAX`/`ANC`/`ALR` tricks; the `b10D8` dispatch byte
that makes `sub()` a `main()` with its tail cut off; the `$FF` terminators of
the arranger and the `$80` of the delay; and every byte cursor. Not one byte of
the object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`'s comparison, inlined in the tool so it
can be chunked (architecture §11): §2's observable over the **whole** certified
horizon against the tune's own player on `deity_informant.PcodeVM`.

| tune | ins | patterns | events | tuning | sidTAB rows | accs | ticks | SID writes | divergences | identical |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Automatas | 1 | 113 | 1,621 | 292 | 358 | 5 | **149,025** | 3,576,600 | **0** | 149,025 |
| Jazzpjazz | 1 | 32 | 305 | 169 | 97 | 5 | **1,799** | 43,176 | **0** | 1,799 |

`identical_ticks` is the tick count — per register, per value, per position in
the tick. There is no refusal to name on either tune, and the four arms neither
horizon takes carry their reason: a `trap` for the routing byte written
outright, for the absolute note column one build has and for the oscillator's
interval branch, plus a build-time assertion for the last of those and for a
chained sidTAB jump, a shape the object could not express at all (§6).

*Automatas* is the first exemplar whose certification does not fit in a script's
60 seconds, so the tool carries `--budget`/`--resume`, pickling the PcodeVM, the
`Player` and the counters between invocations. `end` is `{"tick": 149024,
"kind": "loop"}` and `{"tick": 1798, "kind": "horizon"}`, and the inherited loop
claim re-verifies on the render: the next 129,024 ticks are the previous
129,024, write for write, on GoatTracker 2's window — one tick early leaves
exactly one mismatching tick, voice 0's pulse width, a datum the flush emits
from the state the *previous* call left.

---

## 4. What the spec needed

Ten forms. Six are in the player and four are only in the data.

### 4.1 A flush names the registers it carries, and the order it writes them

defMON's write-out is per voice (`pw_lo`, `pw_hi`, `freq_lo`, `freq_hi`, `sr`,
`ad`, `ctrl`) and then the two global registers, touching neither `$D415` nor
`$D416` — the cutoff is not the image's (§4.2). No count and no direction is
that order, so `meta.shadow.registers` is the ordered **list** the flush writes.
Ascending instead diverges on **149,025 of 149,025** ticks of *Automatas* and
**1,799 of 1,799** of *Jazzpjazz*: voice order inside a flush is §2 rule 1's
tick order. The same measurement retires `commit_order` here and confirms
§3.1's claim about a shadowed family — swapping `ad` and `sr` diverges on **0**
ticks of either horizon.

### 4.2 A global commit outside the image reaches the chip on its own tick

`filter()` writes `$D416` to the chip mid-tick; `writeout()` emits `$D417` and
`$D418` from cells at the head of the *next* one. The rule that carries both:
**the image holds the registers the flush names, and a commit to a register the
flush does not name reaches the chip where it is made.** Deferring the cutoff
through the image diverges on **55,260** ticks of *Automatas* and **282** of
*Jazzpjazz*. Moving the channel's *streams* after the voices instead diverges on
GoatTracker 2, whose filter stream and voices' commands write the same global
cells, so the channel stays before the voices and the commit after.

### 4.3 One cell vocabulary, for a read and for a `sets` target

The sweep's value cell **is** the write-out's own two operand bytes: a sidTAB row
stores a width into them, the sweep reads it back, steps it and stores it, and
the flush emits it. Writing that width through a producer defers it to the end
of the voice's tick, so the sweep — three ranks later in the same tick — reads
the value the *previous* tick left, which is a wrong pulse width 132 ticks into
*Jazzpjazz*. The expression reader and the assign target therefore use
`Acc.cell`'s own vocabulary and no smaller one.

### 4.4 `xor`, beside `and` and `or`

`sid[v].ctrl ← voice[v].ctrl ^ voice[v].ctrl_eor`, and a sidTAB row sets either
half; two cells and their xor at every write is the only shape that holds both.
`Acc` uses the same node to turn the sweep's direction byte, which is `EOR #$80`
in the program.

### 4.5 `row_consumes_tick: false` is *never*, not *always*

The first family to write the value, and the arm was dead: `false` reached the
guard evaluator as `None`, which reads as *always*. defMON's row shares its tick
with the cascades and the oscillator, so the tick ended at the row and every
cascade fired one tick late. Three values in one place.

### 4.6 A gate reports the decision the step made, not the cell it left

`Acc.gate` picked its arm by evaluating `step_when` again *after* the store. For
Commando's drum, whose `step_when` reads cells its own step does not move, that
is the same answer twice; for defMON's sweep, whose `step_when` is `pw >= step`,
the step that lands the width on its floor reads the floor back and reports that
it did not step. `step()` decides once, before anything moves.

### 4.7 The arranger's end is global, so the score is materialised per step

**One** arranger cursor serves all three voices and advances when the *first* of
the three patterns reaches its end row, that row's low nibble becoming every
voice's next count. No `Order` grammar expresses that and none should: a voice's
play step is **its pattern cut where the step ends**, the last event's `dur`
filled out to the step's length and patterns keyed by content. *Automatas*' 168
arranger steps cut **0** patterns, *Jazzpjazz*'s 72 cut **110** of 216, and 78
source patterns become 32.

### 4.8 A stream that acts and *then* holds

A sidTAB row acts on the tick it is reached and *then* holds for `DL` more — the
delay belongs to the row that just fired — which is §3.3's step shifted by one,
and the shift cannot be folded into the row because a jump target's predecessor
is not its own index. The transliteration is two object rows per sidTAB row: an
**act** row of `hold 1` carrying the record's `sets` and a **wait** row of `hold
DL` carrying nothing, elided where `DL & $7F` is zero. Making the wait one tick
longer diverges on 138,907 of 149,025 ticks of *Automatas* and 1,350 of 1,799 of
*Jazzpjazz*. The delay's **bit 7** is §3.3's terminator, a row whose `next` is
`null`; its low seven bits stay as the wait row's hold, so the byte round trips.

### 4.9 A tuning read below itself and past itself

One frequency table, two live windows: the note at its base, the slide's step
**36 entries below** it (`2·(osc & $3F) − 36`, negative for two thirds of the
byte's range), and a note offset wrapping in eight bits read **past** its stored
156. `base -36` and 292 entries for *Automatas*, 169 for *Jazzpjazz*. The top is
a static over-approximation — *Automatas* materialises `-36..255` and reads
`-36..168`, *Jazzpjazz* `-36..132` and `0..98` — but not slack: *Automatas*
really does read 49 entries past the 156 the table stores, and `tuned` asserts,
so a read outside what the object states fails.

### 4.10 Multispeed is `rate`, and it is the row clock's

*Automatas* is a used multispeed entry: `cycles_per_tick 2457`, 8 calls a frame,
`main()` on one and `sub()` on seven. The row clock is `rate 8` on the one clock
form while the cascades' holds, the oscillator's producers and the filter's step
are all *per tick* — the anatomy's "sidTAB row = DL+1 calls" (anatomy:213).
Shortening the row by one clock step diverges on 149,000 of 149,025 ticks. The
tool derives the rate from the certificate's cadence,
`round(19656 / cycles_per_tick)`.

---

## 5. What the spec got right

Nine rows this family exercises and does not change.

| the row | what defMON does with it |
| --- | --- |
| §3.3 **the stream at its most general** | the sidTAB is the form §3.3 was written from and it needs no field: a variable-length record of register columns is a row's `sets`, the `$1900 == 0` indirection is the row's `jump`, and the delay's bit 7 is the terminator (§4.8) |
| §3.3 **one row list, two cursors** | `casa` and `casb` are the same 358 rows under two per-voice cursors — six cascades over one table, which is what a stream's cursor already is |
| §3.5 **one inline stream, three places** | a sidcall is a command whose whole record is `point`, and the one instrument's `on_note` is an inline stream of three sets. No `adsr`, no `prelude` |
| §3.6 **a command is a record, named by what it does** | 97 commands and 11, every one `cascade.a:NN` or `cascade.b:NN` — the sidTAB row index is *data* the score names, and there is no dispatch table |
| §3.6 **the note column is a token class the layer spends** | the flag byte's four high bits become `sounds`, `arm` ×2 and nothing; its low nibble becomes `dur`; the note byte is a pitch index outright |
| §3.6 **`Order` is `play` plus `jump`, `stop` or `horizon`** | three columns of one arranger, `jump` on the `$FF` row for the complete tune and `horizon` for the one that is not |
| §4.1 **`meta.tick` is a list** | `row ; machine`, two phases and no `fetch`: the frame defMON reads its next flag byte on writes nothing, so it is part of the row's own length and not a phase |
| §5 **the bounded accumulator** | the slide is `delta tuned(c)` / `policy wrap` / `phase const`; the sweep is `delta` with a live flag, `policy reload` at either end, `delta_when` for the arm that does not step, and a second accumulator on the flag the skipped delta leaves. Five records, no new field |
| §2 **the certificate's boundary** | the write lists are identical, so nothing in the certificate rests on the reduction. `permuted_ticks` is 0 on both tunes |

---

## 6. Finding the data

defMON relocates: the two builds put the same player code at the same addresses
but their *tables* differ by 36 bytes and `init` patches operands, so the tool
runs the tune's own `init` on the PcodeVM and reads the image the *tick* sees.
On it, **17 signatures** locate every datum by the operand of the instruction
that reads or writes it, in three shapes: one site; `n` sites at the player's own
stride (`cascade` matches its six unrolled copies and `patrow` its three, the six
`LDX #imm` constants asserted to be `0, 1, 2, 0, 1, 2` — which is what makes
`casa` and `casb` two streams and not six); and alternatives exactly one of which
may match, which one being a datum — the sidTAB's note column is `ADC b12CC` in
*Automatas* and `BMI +4 / ADC b12CC / AND #$7F` in *Jazzpjazz*, so one build
reads a byte with bit 7 set as an absolute note, and no reachable row of that
build has one, so the arm is a `trap`.

Eleven assertions are fail-closed — the six cascades two sets of three, the three
voice records one stride apart, the slide's two windows one table at two offsets,
the interval's two reads neighbours, the tuning's halves adjacent, and over the
reading a sidTAB jump landing on another jump, a pulse byte the chip's nibble
cannot hold, an oscillator byte in the interval range, a filter shift that is
neither `NOP` nor `ASL`, a per-voice detune that is not the voice index, and an
arranger step no pattern ends. Every one refuses; none approximates. And a test
reconstructs every reachable sidTAB record — both mask bytes, all twelve columns
in the record's own order, every row's delay with its terminator bit — out of the
object alone and diffs it against the image byte for byte.

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Automatas | 2,818 | 24,021 | 2,696 | 7 | 122 | 2,696 | 8,604 |
| Jazzpjazz | 703 | 6,838 | 662 | 7 | 41 | 662 | 3,848 |

`xz -9e` of the object against the tune's own PSID load band (§9's acceptance
number 3):

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.md`, Automatas | 140,205 | **8,604** |
| `trackerprog.json`, Automatas, compact | 216,734 | 8,228 |
| — its `score` half (3 orders, 113 patterns, 97 commands) | 161,486 | 4,240 |
| — everything else (tuning, streams, accs, instrument) | 55,239 | 4,160 |
| the whole load band | 8,162 | 4,316 |
| `trackerprog.md`, Jazzpjazz | 37,182 | **3,848** |
| `trackerprog.json`, compact / its `score` half | 54,509 / 29,082 | 3,712 / 1,024 |
| its load band | 6,065 | 2,948 |

*Jazzpjazz* is the **closest any exemplar comes** to the layer's first size
claim, 1.26×, and *Automatas* is 1.90×; the current table over all thirty builds
is [prototype-trackerprog.md](prototype-trackerprog.md) §9.1. *Automatas* is the
worse, and the object says why: `casa` and `casb` are the same 358 rows printed
twice, two cursors over one table not being something §3.3 can say. The
compressor recovers most of it; the print does not.

**What the object needs, poisoned one datum at a time**, each row the object
with one thing taken out, re-rendered against its own certified render, counted
as ticks whose write list is no longer identical, **over the whole horizon of
each tune**:

| datum taken out | Automatas / 149,025 | Jazzpjazz / 1,799 |
| --- | --- | --- |
| the flush's order, run ascending instead | 149,025 | 1,799 |
| the row one clock step shorter | 149,000 | 1,795 |
| the per-voice detune the write-out adds (`b101F`) | 146,487 | 1,798 |
| the sidTAB delay one tick longer | 138,907 | 1,350 |
| the cutoff written through the image, not the chip | 55,260 | 282 |
| the cutoff's floor | 49,758 | 0 — this build's floor is zero |
| the carry the frequency add leaves | 44,675 | 0 — 129 sweep steps, none with a carry |
| `commit_order`, `ad` and `sr` swapped | **0** — the image re-orders them | **0** |
| the oscillator's interval branch | **0** — dead in both, and a `trap` | **0** |

Every non-zero row is a §2 **divergence**, not a permutation, and the two zeros
are the section's real content: §3.1's claim about a shadowed family confirmed by
measurement for the first time, and an arm no reachable sidTAB row of either tune
can take, which the tool asserts at build time rather than discovering at tick
100,000.

**A prefix is not a horizon, and this table is where it showed.** Over
*Automatas*' first 20,000 ticks — a longer prefix than any other exemplar's whole
horizon — the carry row reads **0**, and the conclusion written from it was that
defMON could not be §5's second family for a live carry. Over the whole 149,025
it reads **44,675**: the carry is set on 9,144 of the sweep's 170,702 steps, and
the first is past tick 20,000. §9's acceptance #1 says the whole certified
horizon; this is what it is for.

---

## 8. The eight things the family was expected to force

Five held, two held in kind but not in spelling, and **one was wrong**. One of
the five held only when the measurement was taken over the *whole* horizon
rather than a 20,000-tick prefix (§7).

| # | the expectation | what the code said |
| --- | --- | --- |
| 1 | §3.3 cites defMON's sidTAB rows as the form the stream was written from, so they should need **no new field** | **held.** No field. What they needed was the delay read at the other end of the hold, which is a transliteration and not a row (§4.8), and the terminator §3.3 already has |
| 2 | §3.5's "the sidTAB row **is** the instrument" — an `Ins` with `on_note` and nothing else | **wrong, and the sharpest of the eight.** A sidTAB row is a *stream row*; a voice runs **two** at once, so no single `Event.ins` can name them and both are §3.6 `point` commands. The object has **one** instrument for the whole tune, the voice's own machine — an `on_note` that resets the oscillator, and six arms. It has no `adsr` and no `prelude`, the first `Ins` of any family with neither |
| 3 | §3.5's data-side prelude row, `WG=00 AD=0F SR=00 → WG=09 → sound` | **held, and it is not a prelude.** It is the first three rows of a sidTAB program — `ctrl_eor := 0 ; ad := $0F ; sr := 0`, `hold 3`, `ctrl_eor := 9` — so the object carries no `prelude` and no `early` at all: nothing schedules it, the stream simply starts there |
| 4 | §10's multispeed, to be **measured** on a used entry | **held (§4.10).** `rate 8` on the row clock, `cycles_per_tick 2457`, every other phase at the tick, and no new field |
| 5 | the **second family** for §5's live carry — `pw_lo -= (b101E + (1 - carry_2))` | **held, and only just.** `delta {"sub": [{"add": [pwstep, 1]}, {"flag": "C"}]}`, the carry the frequency add of the same voice's tick left. Set on **9,144 of 170,702** sweep steps of *Automatas*, dropping it diverges on 44,675 ticks — but on *Jazzpjazz*, and on *Automatas*' first 20,000, it is set on none (§7). The row is two-family; it took the whole horizon to say so |
| 6 | `tabcell(T[c])` on freq, sign from `bit(cell, 7)` | **held in kind, not in spelling.** The slide's step is an absolute table entry at a cell-derived index, which is the row. But the table is the *tuning*, so the object spells it `{"tuned": 2·(osc & $3F) − 36}` and no stream carries it; and the sign is `bit(osc, 6)`, not bit 7 — bit 7 says whether there is a slide at all |
| 7 | the `horizon` terminator, on the first `complete: false` exemplar | **held.** *Jazzpjazz*'s three orders end `horizon`, materialised 28 steps of 72, and `end.kind = horizon` |
| 8 | the arranger — `flag [A] [B] [note]` rows over three pattern columns with an `$FF` jump, subtune = start row, and the byte-range token class spent | **held, and it forced one thing more.** The flag byte is `sounds`/`arm`/`arm`/`dur` and the note byte is a pitch index; the `$FF` row's jump target is the voice-1 column of that row; the subtune is the arranger cursor's init value. What was not foreseen is that the arranger's end is **global**, so the score is materialised per arranger step (§4.7) |
