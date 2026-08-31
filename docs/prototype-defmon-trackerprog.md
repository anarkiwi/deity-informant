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

Four results:

1. **Both builds render, on one object shape and one code path.** *Automatas*
   over its **whole 149,025-tick horizon** and *Jazzpjazz* over its whole 1,799,
   **0 divergences** on §2's observable — and *stronger*: the write lists are
   **identical**, tick for tick, value for value, register for register, on
   every one of the 150,824 ticks.
2. **The multispeed question of §10 is measured, and the answer is `rate`.**
   *Automatas*' entry runs 8×/frame (`cycles_per_tick 2457`); its cascades and
   its oscillator run at the *tick* and its row clock at `rate 8`, and nothing
   else in the object knows. *Jazzpjazz*'s entry runs once (16,422) at `rate 1`.
   One datum, one field, and it is §3.3's divider — the same `rate` a stream has.
3. **`horizon` is exercised.** *Jazzpjazz* is `complete: false`, so its `Order`
   is materialised as far as the horizon reaches — 28 of the arranger's 72 steps
   — and ends on the `horizon` terminator, with `end.kind = horizon`.
4. **The layer invariant holds at four families**, with no branch on
   `meta.family` anywhere in `trackerprog/`. Six of the ten forms below are in
   the player and four are only in the data.

Reproduce (the long one needs a budget, architecture §11):

```
tools/trackerprog_defmon.py $HVSC/MUSICIANS/G/Goto80/Jazzpjazz.sid \
    --source out/recert-main/goto80-jazzpjazz/certificate.json \
    --certify --out out/defmon-tp/goto80-jazzpjazz

until tools/trackerprog_defmon.py $HVSC/MUSICIANS/G/Goto80/Automatas.sid \
    --source out/recert-main/automatas/certificate.json --certify \
    --out out/defmon-tp/automatas --budget 45 --resume out/defmon-tp/automatas.pkl
do :; done
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 the eight things the family was expected to force.

The forms, in one line each. In the player: §4.1 a flush that names its own
registers and their order · §4.2 a global commit outside the image · §4.3 one
cell vocabulary for a read, a `sets` target and an accumulator's cell · §4.4
`xor` · §4.5 `row_consumes_tick: false` is *never* · §4.6 a gate reports the
decision the step made. In the data only: §4.7 the arranger's end is global, so
the score is materialised per step · §4.8 a stream that acts and *then* holds ·
§4.9 a tuning read below itself and past itself · §4.10 multispeed is `rate`.

---

## 1. The object

`tools/trackerprog_defmon.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` renders it. The same seven sections:

| section | Automatas | Jazzpjazz |
| --- | --- | --- |
| `meta` | 3 voices in order 0,1,2; `commit_order (sr, ad, ctrl)`; a **23-register image** flushed in the write-out's own order (`pw`, `freq`, `sr`, `ad`, `ctrl` per voice, then routing and volume); a **divider** row clock at `rate 8`, the entry running 8×/frame at 2,457 cycles; `tick: row ; machine`; the row never spends the tick | the same, `rate 1` at 16,422 cycles |
| `pitch` | one table, `base -36` and **292** entries: the tuning, the window the slide reads *below* it, and what the note column reaches *past* it (§4.9) | `base -36`, **169** |
| `streams` | **5**: `casa` and `casb` — the two sidTAB programs a voice runs, **358** rows over one row list; `pitch_out`, the oscillator's three producers; `filter`, the global cutoff channel; `voice_bit`, the routing masks | the same five, **97** sidTAB rows |
| `accs` | **5**: two slides (up, down), the pulse-width sweep's two directions and the bounce that turns it | the same five |
| `instruments` | **1** — defMON has no instrument record: this one is the voice's own machine, an `on_note` that resets the oscillator and six arms | **1** |
| `score` | 3 order programs of 168 `play` steps ending `jump 0`; **113** patterns, **1,621** events, **97** commands, every one a cascade re-point | 28 steps ending **`horizon`**; **32** patterns, **305** events, **11** commands |
| `globals` | one channel: the 16-bit cutoff accumulator, its step, its direction, its base and its floor; the routing byte; the volume byte; two flags | the same |

The player has one dispatch and it is on the *form* of a delta, a policy or a
stream row — never on the name of an effect. The five accumulator ids
(`slide_up`, `slide_down`, `pw_down`, `pw_up`, `pw_turn`) are labels in the data.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/{automatas,goto80-jazzpjazz}/tuneprog.md`) and the player's
own code. Right column is the object.

| the player says | the trackerprog says | §5 row |
| --- | --- | --- |
| `tick(): if (call_counter & 7) != 0: sub() else: main()` | `meta.tempo.rate 8` — the row clock is a divider over the entry, and every other phase runs at the tick | §10, now measured (§4.10) |
| `writeout()`: `sid[v].pw_lo ← voice[v].pw_lo`, `pw_hi`, `freq`, `sr`, `ad`, `ctrl ^ ctrl_eor`, then `res_route` and `mode_vol \| $F` | `meta.shadow.registers` — the 23 registers the image carries, in the order it writes them; the `ctrl` write is the `xor` of two cells (§4.4) | §3.1 (§4.1) |
| `filter()` writes `sid.cutoff_hi` directly, before the row and the cascades | `globals.streams: [filter]` — an `all` stream of six guarded rows, and a `globals.commit` to register 22, which the image does not hold and so reaches the chip on its own tick | new (§4.2) |
| `filter.acc ± filter.step`, the high byte floored at `b10CE` where it goes negative, then `+ b10CA + carry`, then `CMP`/`BCS` against the same floor, then `NOP` or `ASL` | those six rows, in order: the step and its carry — `carry_out(acc + step, 16)` going up and `borrow_out(acc − (step + 1), 16)` coming down — the floor on the accumulator, the byte, the floor on the byte twice, and the build's own shift | §5 filter sweep |
| `row_advance()`: `b10D9 & $80` reloads all three voices from `T1A00`/`T1A80` through `T1B00`/`T1C00`/`T1D00` at `cursor_10EB` | the score's three `Order` programs, one column each; `cursor_10EB`'s init value is the **subtune**, the `$FF` row's jump target is the *voice-1 column* of that row | §3.6 |
| the first pattern to reach an end row ends all three, and its low nibble is every voice's next count | the score is materialised **per arranger step**, each voice's play step being its own pattern *cut where the step ends* | §6's materialisation (§4.7) |
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
that makes `sub()` a `main()` with its tail cut off; the `$FF` terminators of the
arranger and the `$80` of the delay; and every byte cursor. Not one byte of the
object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`'s comparison, inlined in the tool so it
can be chunked (architecture §11): §2's observable over the **whole** certified
horizon, the reference being the tune's own player on `deity_informant.PcodeVM`.

| tune | ins | patterns | events | tuning | sidTAB rows | accs | ticks | SID writes | divergences | identical |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Automatas | 1 | 113 | 1,621 | 292 | 358 | 5 | **149,025** | 3,576,600 | **0** | 149,025 |
| Jazzpjazz | 1 | 32 | 305 | 169 | 97 | 5 | **1,799** | 43,176 | **0** | 1,799 |

`compared` and `dropped` are §2's own three and three, and `identical_ticks` is
the tick count, which is stronger than either — per register, per value, per
position in the tick. There is no refusal to name on either tune: nothing is
emitted partially, and the four arms neither horizon takes each carry their
reason — a `trap` in the object for the routing byte written outright, for the
absolute note column one build has and for the oscillator's interval branch, and
a build-time assertion for that last one again and for a chained sidTAB jump,
which is a shape the object could not express at all (§6).

*Automatas* is the first exemplar whose certification does not fit in a script's
60 seconds — the tuneprog's own took 371 s — so the tool carries
`--budget`/`--resume`, pickling the PcodeVM, the `Player` and the running
counters between invocations. Three 45-second invocations reach the end. The
test suite renders a 9,000-tick prefix and says so; the whole-horizon
certificate above is the tool's, reproduced by the command in the header.

`end`: `{"tick": 149024, "kind": "loop"}` and `{"tick": 1798, "kind":
"horizon"}`. The inherited loop claim **re-verifies on the render**: rendering
past the first repeat, the next 129,024 ticks are the previous 129,024 write for
write. The window is GoatTracker 2's — the claim names the call the state
repeats *after*, and a write-out emits the image the call before it left, so the
replay starts one tick later. Taking the window one tick early leaves exactly
one mismatching tick, and it is voice 0's pulse width: a datum the flush emits
from the state the *previous* call left.

---

## 4. What the spec needed

Ten forms. Six are in the player and four are only in the data.

### 4.1 A flush names the registers it carries, and the order it writes them

`meta.shadow` was `{registers: N, order: descending|ascending}` — a *count* and
a direction. defMON's write-out is per voice (`pw_lo`, `pw_hi`, `freq_lo`,
`freq_hi`, `sr`, `ad`, `ctrl`) and then the two global registers, and it touches
neither `$D415` nor `$D416` — the cutoff is not the image's (§4.2). Neither
direction is that order, and no count excludes a register in the middle.

`registers` is now the ordered **list** the flush writes. Applying the general
form to the family that had the count: GoatTracker 2's value is
`range(24, -1, -1)`, and its two builds render write for write identical over
their whole 12,000-tick horizons — the count was that list, said less. Rendering
defMON's list ascending instead diverges on **149,025 of 149,025** ticks of
*Automatas* and **1,799 of 1,799** of *Jazzpjazz*: §2 rule 1 keeps every
`ctrl`/`AD`/`SR` write in tick order, and voice order inside a flush is that
order.

The same measurement retires `commit_order` for this family, and confirms §3.1's
own claim about it: swapping `ad` and `sr` diverges on **0** ticks of either
tune's whole horizon, because every edge write goes through the image and the
flush re-orders them. A family whose writes go through a shadow cannot tell the difference, and
this is the first one measured saying so.

### 4.2 A global commit outside the image reaches the chip on its own tick

defMON's `filter()` writes `$D416` with a `STA` to the chip, in the middle of
the tick; its `writeout()` emits `$D417` and `$D418` from cells, at the head of
the *next* one. Both are the global channel, and the difference is one tick.

The rule that carries both without a second datum: **the image holds the
registers the flush names, and a commit to a register the flush does not name
reaches the chip where it is made.** GoatTracker 2's three filter registers are
in its flush and still defer; defMON's cutoff is not in its flush and does not.
Deferring the cutoff through the image instead diverges on **55,260** ticks of
*Automatas* and **282** of *Jazzpjazz*.

This also settles where the global channel runs. §4's pseudocode puts
`commit_global()` after the voices, and moving the channel's *streams* there too
would have let the cutoff be computed from what the cascades left. It does not
survive contact: GoatTracker 2 diverges, because its filter stream and its
voices' commands write the same global cells and the order between them is the
tick's. The channel stays before the voices, the commit stays after, and the
cutoff the commit emits is the value the channel latched at the head of the
tick — which is exactly what the program computes there.

### 4.3 One cell vocabulary, for a read and for a `sets` target

The audit gave `Acc.cell` one vocabulary — `tick`, a voice cell, `#global`,
`ins.pw`, `shadow.<pair>`, any with `.hi`/`.lo` — and left the *expression*
reader and the assign target with a smaller one: `{"cell": name}` looked in the
voice's own cells and nothing else, and no `sets` target could name the image.

defMON's pulse-width sweep is a counterexample in both directions at once. Its
value cell **is** the write-out's own two operand bytes: a sidTAB row stores a
width into them, the sweep reads them back, steps them and stores them, and the
flush emits them. Writing that width through a producer defers it to the end of
the voice's tick, and the sweep — three ranks later, in the same tick — then
reads the value the *previous* tick left. It is not a divergence you can find by
reading: it is a wrong pulse width 132 ticks into *Jazzpjazz*.

`Player.cell` now falls through to the same `whole`/`split_cell` pair
`Acc.load` uses, and `assign` routes a `shadow.`-prefixed target through the
same `store_cell` `Acc.store` uses. One name, one meaning, three places.

### 4.4 `xor`, beside `and` and `or`

`sid[v].ctrl ← voice[v].ctrl ^ voice[v].ctrl_eor`, and a sidTAB row sets either
half. Two cells and their xor at every write is the only shape that holds both;
`ev` had `and`, `or`, `add`, `sub`, `shr`, `field` and `bit`, and `fold` already
used `^` internally. `Acc` uses the same node to turn the sweep's direction
byte, which is `EOR #$80` in the program.

### 4.5 `row_consumes_tick: false` is *never*, not *always*

A latent bug, and the first family to write the value. `consumes()` returned
`[]` for `true`, the guard list for a guard list, and **`None` for `false`** —
and `guards(None)` iterates `gs or ()` and returns `True`. Every family so far
wrote `true` or a guard list, so the arm was dead. defMON's row shares its tick
with the cascades and the oscillator, and with the bug the tick ended at the row
and every cascade fired one tick late. It is now three values in one place:
`True`, `False`, or the guards.

### 4.6 A gate reports the decision the step made, not the cell it left

`Acc.gate` picked its arm by evaluating `step_when` a second time, *after* the
store. For Commando's drum, whose `step_when` reads two cells its own step does
not move, that is the same answer twice. For defMON's pulse-width sweep, whose
`step_when` is `pw >= step` and whose step moves `pw`, it is the opposite
answer: the step that lands the width on its floor reads the floor back and
reports that it did not step.

`step()` now decides once, before anything moves, and hands the decision to
`apply` and to the gate. Commando, GoatTracker 2 and SID Wizard are unchanged.

### 4.7 The arranger's end is global, so the score is materialised per step

Every certified family so far has one order cursor per voice, and each voice
wraps when its own pattern runs out. defMON has **one** arranger cursor for all
three, and it advances when the *first* of the three patterns reaches its end
row — whatever the other two were in the middle of, and with that row's own low
nibble becoming every voice's next count.

No `Order` grammar expresses that, and none should: §6's materialisation rule
already says the trackerprog represents the score the trace played. A voice's
play step is therefore **its pattern cut where the step ends**, with the last
event's `dur` filled out to the step's own length — patterns keyed by content,
so the ones no cut touches are shared. Measured: *Automatas*' 168 arranger steps
cut **0** patterns (its three columns are always the same length), *Jazzpjazz*'s
72 cut **110** of 216, and 78 source patterns become 32 materialised ones.

That defMON needed this and three tracker families did not is the finding; that
it needed no schema row is the point.

### 4.8 A stream that acts and *then* holds

§3.3's step holds for `hold` ticks and acts on the last of them. A sidTAB row
acts on the tick it is reached and *then* holds for `DL` more — the delay
belongs to the row that just fired, not to the row about to. The two are the
same shape shifted by one, and the shift cannot be folded into the row, because
a jump target's predecessor is not its own index.

The transliteration is two object rows per sidTAB row: an **act** row of
`hold 1` carrying the record's `sets`, and a **wait** row of `hold DL` carrying
nothing, elided where `DL & $7F` is zero. That is not a schema row; it is the
same stream, cut where the family cuts it. Making the wait one tick longer
diverges on 138,907 of 149,025 ticks of *Automatas* and 1,350 of 1,799 of
*Jazzpjazz*.

The delay's **bit 7** is §3.3's terminator: the row acts and the cascade stops.
A stream whose row's `next` is 0 is exactly that, because a cursor on row 0 is
a cursor that does not run — which is the form the schema already had, and the
low seven bits stay in the object as the wait row's own hold, so the byte round
trips.

### 4.9 A tuning read below itself and past itself

One frequency table, two live windows. The note is read at its base; the slide's
step is read **36 entries below** it (`2·(osc & $3F) − 36`, which is negative
for two thirds of the byte's range); and a note offset that wraps in eight bits
is read **past** its stored 156. §3.2 already says both — "the values read, not
the bytes stored", and "a read past a const table's declared size extends
`pitch` with the values read" — and `pitch.base` is already signed. It had never
been anything but zero.

`base -36` and 292 entries for *Automatas*, 169 for *Jazzpjazz*: the union of
what the note column can reach, what the slide window reads, and the tuning
between them. The top is a static over-approximation — every note byte against
every offset byte, with the wrap — so it materialises entries the horizon does
not read: *Automatas* materialises `-36..255` and reads `-36..168`, *Jazzpjazz*
materialises `-36..132` and reads `0..98`. Pairing note and offset per **voice**
narrows 255 to 252 and is not worth the row. The extension is not slack, though:
*Automatas* really does read 49 entries past the 156 the table stores, which is
§3.2's rule being used rather than quoted. The check that matters is the one the
renderer makes — `tuned` asserts, so a read outside what the object states is a
failure and not a guess.

### 4.10 Multispeed is `rate`, and it is the row clock's

§10 asked whether "a sequencer running at frame rate under an n× entry is
`rate = n` on that voice's tempo", and noted it had never been measured on a
used multispeed entry. *Automatas* is one: `cycles_per_tick 2457`, 8 calls a
frame, `main()` on one of them and `sub()` on seven.

`rate` carries it, and nothing else has to know. The row clock is
`rate 8` on the one clock form -- a step of -1 over `rowsleft`, on one tick in eight; the cascades' holds, the
oscillator's producers and the filter's step are all *per tick*, which is what
the anatomy's "sidTAB row = DL+1 calls" says (anatomy:213). Shortening the row
by one clock step diverges on 149,000 of 149,025 ticks, so the divider is load
bearing and not an alignment that happens to work. The tool derives it from the
certificate's cadence, `round(19656 / cycles_per_tick)`.

---

## 5. What the spec got right

Nine rows this family exercises and does not change.

| the row | what defMON does with it |
| --- | --- |
| §3.3 **the stream at its most general** | the sidTAB is the form §3.3 was written from, and it needs no field: a variable-length record of register columns is a row's `sets`, the `$1900 == 0` indirection is the row's `jump`, and the delay's bit 7 is the terminator. What it needed was said differently, not added (§4.8) |
| §3.3 **one row list, two cursors** | `casa` and `casb` are the same 358 rows under two per-voice cursors — six cascades over one table, which is what a stream's cursor already is |
| §3.5 **one inline stream, three places** | a sidcall is a command whose whole record is `point`, and the one instrument's `on_note` is an inline stream of three sets. No `sets`/`note_sets`/`points` split, no `adsr`, no `prelude` |
| §3.6 **a command is a record, named by what it does** | 97 commands and 11, every one `cascade.a:NN` or `cascade.b:NN` — the sidTAB row index is *data* the score names, not an index a dispatch table gave it, and there is no dispatch table |
| §3.6 **the note column is a token class the layer spends** | the flag byte's four high bits become `sounds`, `arm` ×2 and nothing; its low nibble becomes `dur`; the note byte is a pitch index outright, and `note is None` is the only thing that says a row does not sound |
| §3.6 **`Order` is `play` plus `jump`, `stop` or `horizon`** | three columns of one arranger, `jump` on the `$FF` row for the complete tune and `horizon` for the one that is not |
| §4.1 **`meta.tick` is a list** | `row ; machine`, two phases and no `fetch`: the frame defMON reads its next flag byte on writes nothing, so it is part of the row's own length and not a phase |
| §5 **the bounded accumulator** | the slide is `delta tuned(c)` / `policy wrap` / `phase const`; the sweep is `delta` with a live flag, `policy reload` at either end, `delta_when` for the arm that does not step, and a second accumulator on the flag the skipped delta leaves. Five records, no new field |
| §2 **the certificate's boundary** | the write lists are identical, so nothing in the certificate rests on the reduction. `permuted_ticks` is 0 on both tunes |

---

## 6. Finding the data

defMON relocates: the two builds put the same player code at the same addresses
but their *tables* differ by 36 bytes, and `init` patches operands. The tool
therefore runs the tune's own `init` on the PcodeVM and reads the image the
*tick* sees, as the SID Wizard tool does.

On that image, **17 signatures** locate every datum by the operand of the
instruction that reads or writes it. Three shapes of signature:

* **one site** — the write-out's four blocks (pulse, frequency, the three edge
  registers, routing and volume), the filter's whole chain, the arranger's
  reload and the three column tables it indexes, the oscillator's four branches,
  and `row_apply`'s four pieces;
* **`n` sites at the player's own stride** — `cascade` matches its six unrolled
  copies and `patrow` its three, and the copy stride and the voice base come out
  of the match rather than out of a constant. The six cascades' `LDX #imm`
  constants are asserted to be `0, 1, 2, 0, 1, 2` voices, which is what makes
  `casa` and `casb` two streams and not six;
* **alternatives, exactly one of which may match, and which one is a datum** —
  one signature has them: the sidTAB's note column is `ADC b12CC` in *Automatas*
  and `BMI +4 / ADC b12CC / AND #$7F` in *Jazzpjazz*, so one build masks the sum
  into seven bits and reads a byte with bit 7 set as an absolute note. No
  reachable row of that build has one, so the arm is a `trap`.

Five assertions in `layout` are the tool's own fail-closed checks: the six
cascades are two sets of three, the three voice records are one stride apart,
the slide's two windows are one table read at two offsets, the interval's two
reads are neighbours, and the tuning's high half starts exactly where its low
half ends. Six more are spread over the reading: a sidTAB jump landing on another
jump, a pulse byte the chip's own nibble cannot hold, an oscillator byte in the
interval range, a filter shift that is neither `NOP` nor `ASL`, a per-voice
detune that is not the voice index, and an arranger step no pattern ends. Every
one of them refuses; none of them approximates.

That the object *is* the tune's data and not a reading of it is checked rather
than asserted: `test_every_byte_of_the_tune_s_data_is_in_the_object`
reconstructs every reachable sidTAB record — both mask bytes and all twelve
columns in the record's own order — and every row's delay including its
terminator bit, out of the object alone, and diffs them against the image byte
for byte; the arranger's three columns and every pattern's flag, sidcall and
note bytes are compared as the events they decode to, as GoatTracker 2's
orderlist is.

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Automatas | 2,818 | 24,021 | 2,696 | 7 | 122 | 2,696 | 8,604 |
| Jazzpjazz | 703 | 6,838 | 662 | 7 | 41 | 662 | 3,848 |

`xz -9e` against the program that played it, §9's acceptance #3:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.md`, Automatas | 140,205 | **8,604** |
| `trackerprog.json`, Automatas, compact | 216,734 | 8,228 |
| — its `score` half (3 orders, 113 patterns, 97 commands) | 161,486 | 4,240 |
| — everything else (tuning, streams, accs, instrument) | 55,239 | 4,160 |
| `tuneprog.md`, the source print | 42,372 | 8,900 |
| the whole load band | 8,162 | 4,316 |
| `trackerprog.md`, Jazzpjazz | 37,182 | **3,848** |
| `trackerprog.json`, compact / its `score` half | 54,509 / 29,082 | 3,712 / 1,024 |
| its `tuneprog.md` / load band | 31,690 / 6,065 | 6,648 / 2,948 |

The layer's claim holds on both tunes — 8,604 against 8,900, and 3,848 against
6,648 — but *Automatas*' margin is the narrowest of the nine hand exemplars,
and the object says why: `casa` and `casb` are the same 358 rows printed twice,
because a stream's rows are its own and two cursors over one table is not
something §3.3 can say. The compressor recovers most of it; the print does not.

**What the object needs, poisoned one datum at a time.** Each row is the object
with one thing taken out, re-rendered against the object's own certified render;
the count is the ticks whose write list is no longer identical. **Over the whole
horizon of each tune**, which is not a detail — see below.

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

Every non-zero row is a §2 **divergence**, not a permutation. The two zeros are
the section's real content: one is §3.1's own claim about a shadowed family,
confirmed by measurement for the first time, and the other is an arm no
reachable sidTAB row of either tune can take, which the tool asserts at build
time rather than discovering at tick 100,000.

**The carry, and the bias that was not the tune's.** Both spellings of it moved
in §7's sixth package: the cutoff's up arm was `bit(acc + step, 16)` and its down
arm `bit(((acc + $10000) − (step + 1)), 16)`, a tree whose whole content is that
the 6502's subtraction leaves a carry and Python's shift on a negative number is
arithmetic. They are `carry_out(e, 16)` and `borrow_out(e, 16)` now, the bias
lives in the player where the machine's own arithmetic belongs, and `bit` keeps
only the genuine bit tests this object has plenty of — the accumulator's sign,
the byte's sign, the oscillator's direction bit, the sweep's. The `bounce` flag
that the two pulse arms leave stays an `Acc.flag`, and its `unguarded: 1` is the
one thing in the layer that field is still needed for: it is worth 475 of
*Jazzpjazz*'s 1,799 ticks and 127,722 of *Automatas*' 149,025, against 0 on all
three Commando subtunes, whose own default already says what theirs did
([prototype-trackerprog.md](prototype-trackerprog.md) §5, §7).

**A prefix is not a horizon, and this table is where it showed.** Run over
*Automatas*' first 20,000 ticks — a longer prefix than any other exemplar's
whole certified horizon — the carry row reads **0**, and the conclusion written
from it was that defMON could not be §5's second family for a live carry. Over the whole 149,025 it reads **44,675**: the carry is set on 9,144 of
the sweep's 170,702 steps, and the first of them is past tick 20,000. §9's
acceptance #1 says the whole certified horizon; this is what it is for.

Code, against SID Wizard's:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 1,033 (was 1,009) | §4 + §5, one procedure over the object |
| `deity_informant/trackerprog/printer.py` | 618 (was 608) | the flattened form and §6.2's numbers |
| `deity_informant/trackerprog/attest.py` | 81 (unchanged) | §2's comparison |
| `tools/trackerprog_defmon.py` | 1,058 | the transliteration, 17 signatures, the chunked reference |
| `tests/trackerprog/test_defmon_oracle.py` | 229 | the two certificates and the byte-for-byte round trip |
| `tests/trackerprog/test_universal_image.py` | 198 | hermetic snippets, one per form of §4 |

The player grew by 24 lines to carry a fourth family — 40 added against 16
taken out, and a third of what is added is comment. One of the six forms is a
bug fix (§4.5) and one deletes a duplicated read (§4.3). It still has neither Commando, nor
GoatTracker, nor SID Wizard, nor defMON in it.

---

## 8. The eight things the family was expected to force

Eight expectations came with this exemplar. Five held, two held in kind but not
in spelling, and **one was wrong** — and saying which is which is the point of
writing it down. One of the five held only when the measurement was taken over
the *whole* horizon rather than a 20,000-tick prefix (§7).

| # | the expectation | what the code said |
| --- | --- | --- |
| 1 | §3.3 cites defMON's sidTAB rows as the form the stream was written from, so they should need **no new field** | **held.** No field. What they needed was the delay read at the other end of the hold, which is a transliteration and not a row (§4.8), and the terminator §3.3 already has |
| 2 | §3.5's "the sidTAB row **is** the instrument" — an `Ins` with `on_note` and nothing else | **wrong, and the sharpest of the eight.** A sidTAB row is a *stream row*; a voice runs **two** of them at once, so no single `Event.ins` can name them and both are §3.6 `point` commands. The object has **one** instrument for the whole tune, and it is the voice's own machine — an `on_note` that resets the oscillator and six arms. It has no `adsr` and no `prelude`, which is the first `Ins` of any family to have neither |
| 3 | §3.5's data-side prelude row, `WG=00 AD=0F SR=00 → WG=09 → sound` | **held, and it is not a prelude.** It is the first three rows of a sidTAB program — `ctrl_eor := 0 ; ad := $0F ; sr := 0`, `hold 3`, `ctrl_eor := 9` — so the object carries no `prelude` and no `early` for this family at all. §3.5's table row is right about the *data* and wrong to call it a prelude: nothing schedules it, the stream simply starts there |
| 4 | §10's multispeed, to be **measured** on a used entry | **held (§4.10).** `rate 8` on the row clock, `cycles_per_tick 2457`, and every other phase at the tick. The first measurement of §10's answer, and it needed no new field |
| 5 | the **second family** for §5's live carry — `pw_lo -= (b101E + (1 - carry_2))` | **held, and only just.** `delta {"sub": [{"add": [pwstep, 1]}, {"flag": "C"}]}`, the carry the frequency add of the same voice's tick left. It is set on **9,144 of 170,702** sweep steps of *Automatas* and dropping it diverges on 44,675 ticks — but on *Jazzpjazz*, and on *Automatas*' first 20,000, it is set on none, so the first reading of this row said the expectation was wrong (§7). The row is two-family; it took the whole horizon to say so |
| 6 | `tabcell(T[c])` on freq, sign from `bit(cell, 7)` | **held in kind, not in spelling.** The slide's step is an absolute table entry at a cell-derived index, which is the row. But the table is the *tuning*, so the object spells it `{"tuned": 2·(osc & $3F) − 36}` — §5's own "the tuning read as a table by something that is not a note" — and no stream carries it. The sign is `bit(osc, 6)`, not bit 7: bit 7 says whether there is a slide at all |
| 7 | the `horizon` terminator, on the first `complete: false` exemplar | **held.** *Jazzpjazz*'s three orders end `horizon`, materialised 28 steps of 72, and `end.kind = horizon`. `stop` is still unexercised by any family |
| 8 | the arranger — `flag [A] [B] [note]` rows over three pattern columns with an `$FF` jump, subtune = start row, and the byte-range token class spent | **held, and it forced one thing more.** The flag byte is `sounds`/`arm`/`arm`/`dur` and the note byte is a pitch index; the `$FF` row's jump target is the voice-1 column of that row; the subtune is the arranger cursor's init value. What was not foreseen is that the arranger's end is **global** — one cursor for three voices, advanced by whichever pattern ends first — so the score is materialised per arranger step (§4.7), which §6's own rule already licenses |
