# dm-oracle — DefMON's song, in the universal format

The third editor. `docs/gt-oracle.md` established the shape — read a song out of a
real editor's own model, express it in `docs/tracker.md`'s one primitive, gate it
by the same law, compare it against our recovery, and report every wall — and this
extends it to **DefMON** through `pydefmon`, whose `depack_replay` reaches a packed
HVSC `.sid`. So unlike SID-Wizard, DefMON is gated on the **frameprog boundary
GoatTracker uses**, and a third independent editor is what turns a two-editor
artifact into a real gap in the primitive.

`deity_informant/dmoracle.py` is the mapper, `tools/dm_compare.py` the
measurement. The builder, the two graphs and the law are `gtoracle`'s, unchanged:
one vocabulary, one builder, three editors. `pydefmon` is an optional extra
(`pip install -e '.[nativeoracle]'`); the tests skip without it.

## 1. The sidTAB is not a table, it is fifteen lanes

DefMON's model is a **256-row sidTAB** of 15-byte bitmap-packed rows, a per-row
delay (`sidtab_dl`), a per-row jump (`sidtab_jp`), three arrangers and 128
patterns. The lazy mapping — a `SELECT` whose table is the 15-byte rows and whose
row stream is observed — is a transliteration and is **refused**: it re-labels
DefMON's own table as a universal node while explaining nothing, and the delay and
the jump, which are the actual structure, would vanish.

Every column is instead decomposed into the generic object it drives, and every
one of those objects exists in editors that never heard of DefMON:

| sidTAB column | what it drives | the generic lane | GoatTracker / SID-Wizard spell it |
|---|---|---|---|
| `WGh` | `$D404` waveform + gate | `("sidtab","wave")`, gate armed | `wavetable.left` / `wf_table` |
| `WGl` | XOR'd into `$D404` each frame | `("sidtab","xor")` — a **second** generator on one plane | `gateoff_timer` / `ptn_gate` (§4.3) |
| `AD` | `$D405` | `("ins","ad")` | `Instrument.attack_decay` / `attack`+`decay` |
| `SR` | `$D406` | `("ins","sr")` | `Instrument.sustain_release` / `sustain`+`release` |
| `PW` | `$D403` verbatim, `$D402` as `b & $F0` | `("pw","hi")` / `("pw","lo")` | `pulsetable` left/right / `pw_table` |
| `PS` | pulse-sweep depth + direction | `("pw","step")` — a `RAMP` step | the `pulsetable` sweep row |
| `RE` | `$D417` resonance + voice routing | `("filt","res")` | `filtertable` / `filter_resonance` |
| `FV` | `$D418` filter mode + volume | `("filt","vol")` = `(b & $F0) \| $0F` | GT's `$18`, SW's `filter_mode_vol` |
| `CP` | `$D416` cutoff rate | `("filt","step")` | `filtertable` cutoff step |
| `ACID` | 16-bit cutoff-slide command | `("filt","acid")` / `("filt","acidhi")` | — (§4.4) |
| `TR` | note, absolute or relative | `("note","tr")` | orderlist `Transpose` / `Transpose` |
| `AF` | portamento / slide mode | `("note","slide")` | `CMD_TONEPORTA` / `portamento` |
| `sidtab_dl` | hold the row for `dl + 1` frames | `("clock","dl")` — a **divisor** | GT `TempoOverride` / SW `speed` |
| `sidtab_jp` | the row the cascade lands on | `("clock","jp")` — a **loop back-edge** | orderlist `Loop` / sequence `Loop` |
| `arranger_v1/v2/v3` | per-song-step pattern number | `("arr","v1"/"v2"/"v3")` — the **orderlist** | `Channel.entries` / `sequences` |
| `PatternEvent` | note + which envelope to arm | `("patt","note"/"slot_a"/"slot_b"/"flag")` | `Pattern.rows` / `Pattern.rows` |
| `NOTE_PITCH_LO/HI` | the note→freq table | `("pitch","lo"/"hi")` | `FREQ_TABLE` / `NOTE_FREQ_LO/HI` |

Over the six cached tunes the columns are populated as follows (rows carrying an
override, all tunes): `WGh` 206, `AD` 171, `WGl` 161, `TR` 145, `ACID` hi 136,
`AF` 133, `SR` 112, `CP` 74, `PW` 62, `ACID` lo 58, `FV` 54, `RE` 53, `PS` 38.
The arrangement they hang off is 387 patterns, 5535 non-empty pattern rows, 1476
orderlist entries over 609 song steps, and **227 jump back-edges**.

## 2. Provenance off the address bus

`pydefmon` has no Python transcription of the playroutine — `DefmonPlayer` runs
the tune's **own relocatable 6502 replay** on jennings. That is better, not worse:
the replay *is* the editor's player, so every index can be read off the address
bus instead of being modelled.

- **The row.** The cascade fetches a row by reading the per-row pointer array at
  `data_base + Y`. That read names the **sidTAB row** `Y`.
- **The note.** The pitch oscillator indexes `NOTE_PITCH_LO`. That read names the
  **note**.
- **The voice and the register.** DefMON's driver is self-modifying: three
  identical SID write bands at `signature + 0x31·v` carry the emitted bytes as
  **immediate operands**, and a write into one of them names the voice, the
  register, and the column that byte came from.
- **The value.** Every operand write is bound to the row the cascade just fetched
  **only where that row's column holds exactly the staged byte** — else to the row
  the voice still holds, else nothing. That is `tracker._lane_key`'s pair
  (`mem0[src] == val`) and §5's held reading, applied to a live machine.

Every site is **verified against the opcode that must be there** (`dm_sites`): the
three bands' seven stores each, the three global filter stores, the `LDA acc / ASL
/ STA $D416` cutoff emit, and the note tables by content. A driver variant is
refused rather than mis-read, and a mis-named row costs coverage and can never
cost correctness, because the declared byte is re-checked at every emit.

The observers return `None`, so the emulation is untouched.

## 3. What it measures

200 frames, PSID start subtune, `tools/dm_compare.py`. Of the **682 cached HVSC
tunes, 6 carry DefMON's replay signature**; all 6 depack, all 6 map, and all 6
lift to a frame program.

### 3.1 The law

| | admitted law | strict, full window | strict vs the replay's own writes |
|---|---|---|---|
| DefMON, 6 tunes, vs **frameprog** | **6/6** | **6/6 (200/200 frames each)** | **6/6** |

**Every one of the six reproduces the decompiled frame program's SID writes for
the whole window, from a graph built out of nothing but the song and the universal
primitive.** GoatTracker reaches that on 4 of 71 (docs/gt-oracle.md §3.1) and is
limited there by `pygoattracker`'s decompiler and playroutine; DefMON has no such
gap because the reference player is the tune's own 6502 code, so the oracle and
frameprog are two independent executions of the same driver and they agree
bit-for-bit. The strict result is therefore a statement about the *format*: the
universal primitive plus a `RAW` residual carrying what the song predicts
reproduces a real editor's tune across the 6502 boundary.

### 3.2 The three axes, against ground truth

| axis | measured | result |
|---|---|---|
| **Pitch** | our note-lane `SELECT` row on `freq_hi` vs the note the player indexed | **0.9588 over 2746 emits, offset 0, on 6/6 tunes** |
| **Instruments** | our `SELECT` rows on `ad`/`sr`/`ctrl` vs the sidTAB row | **0 emits — the recovery carries no row on any instrument plane** |
| **Structure** | native patterns / orderlist entries / rows each side represents | **format 20 of 387 patterns, 31 of 1476 orderlist entries, 103 of 5535 rows; recovery 0, 0, 0** |

Per tune the pitch share is 0.9966 (Automatas), 0.9849, 0.9785, 0.9752, 0.9180,
0.8706, and **the offset is 0 on every one**: our recovered row *is* the
composer's note index, not merely proportional to it. The axis reads `freq_hi`
rather than GoatTracker's `freq_lo` because on these tunes the recovery declares
the hi lane and not the lo one; the register is a parameter and is reported.

The instrument axis is zero **for a reason worth stating**: our recovery does
interpret `ctrl`/`ad`/`sr` on four of the six tunes, but entirely through the
shallow `imm` class — program constants — never through a declared lane at a row.
That is exactly what DefMON's driver forces: the instrument bytes reach the SID as
**self-modifying immediate operands**, so `tracker._immediates` explains the value
and no declaration explains the index. The oracle's own classes say the same from
the other side: `lane` 11725, `gate` 831, **`imm` 0** — no emit in the DefMON
oracle rests on the shallow class — against the recovery's `lane` 2746 / `imm`
3301 on the same windows.

The structure axis is §7.4's prize and it now carries **two numbers that must not be
summed**, one per side, on the same six windows:

| | the format (DefMON's own model) | the tracker's recovery |
|---|---|---|
| `Index` nodes | **82** | **0** |
| `SELECT`s read at a generated row | **62** | **0** |
| emits at a generated row | **4199** | **0** |
| patterns represented | **20** of 22 walked, of 387 in the songs | **0** |
| pattern rows represented | **103** of 160 walked, of 5535 | **0** |
| orderlist entries represented | **31** of 39 walked, of 1476 | **0** |

The node shapes are docs/gt-oracle.md §3.2b's, unchanged — one chain per (voice,
pattern), the row counter then the pattern's own column then the table — and the
DefMON spelling of each is §1's table read one link further:

- the **orderlist** is `arranger_v1/v2/v3`, read at `sig + $ADE + $100·v + step`. That
  read names the voice and the song step, so the pattern number is off the address bus
  exactly as §2 takes every other index.
- the **pattern** is a `PatternEvent` column: `note` names a row of `NOTE_PITCH_LO/HI`,
  and `slot_a`/`slot_b` name a **sidTAB row**, which is the row every instrument lane
  already reads. So one index link serves the pitch table and the instrument bank both.
- the **row counter** is the walk of that pattern's events. On DefMON it is a `RAMP` on
  **0** of 82 chains: the packed event stream is variable-length, so successive events
  are not an even stride and every counter is the unrolled walk (docs/gt-oracle.md
  §3.2b's unrolled-walk form).

Reaching the events at all needed the **packed pattern stream decoded**, because the
packer does not store 32 fixed four-byte events: it stores each event's flag byte
followed by only the columns its gates arm, patterns end-to-end, stopping at the last
one carrying an end event. `dm_pattern_map` rebuilds that rule and **verifies it byte
for byte against the mounted image**, ending the map where it diverges rather than
guessing — the same discipline `dm_sites` applies to the opcodes. It maps 20, 30, 90,
2, 40 and 118 patterns on the six tunes. Where the map ends, no pattern read is
observed and no arrangement is claimed: 1756 emits, counted as `no_pattern_row`.

25 of 158 native note-ons still coincide with a fire of one of our instrument-plane
`EDGE` streams; the arrangement moves the **index** domain, not the trigger domain
(§4.1) and not the value domain (§4.7).

### 3.3 Coverage, side by side

The same 6 tunes, oracle against recovery, on identical windows:

| plane | oracle (the composer's data) | tracker recovery |
|---|---|---|
| freq | 4743/7200 = 66% | 4281/7200 = 59% |
| pw | 1021/7200 = 14% | 0/7200 = 0% |
| ctrl | 1887/3600 = 52% | 1091/3600 = 30% |
| filter | 1131/3600 = 31% | 0/3600 = 0% |
| sr | 1887/3600 = 52% | 1123/3600 = 31% |
| ad | 1887/3600 = 52% | 1087/3600 = 30% |
| **all** | **12556/28800 = 43.6%** | **7582/28800 = 26.3%** |

Per tune the oracle reaches 58.3% (`NGC1277_tune_1`), 55.3% (`4_Red_Calx_slo`),
44.9% (`20_Years_Is_Nothing`), 37.6% (`Automatas`), 33.1% (`2Manu3L`) and 32.4%
(`Acidburger`).

The trigger domain is **0 generated of 3192 fires** for the oracle and 0 of 2444
for the recovery. Mapping a third real editor's song still does not lift one
trigger off the floor, and §4.1 says why — this time with the divisor in hand.

## 4. The deficiency report

Every wall actually hit, with its weight, and for each of docs/gt-oracle.md §4's
entries an explicit verdict: **does a third, independent editor hit it too?**

**A third editor settles whether a gap is universal — it says nothing about
recovery.** Each entry below was closed in the primitive and moved the format by
thousands of emits and the recovery from binaries by hundreds or none (the arrangement:
73766 against **0**). `tools/graph_diff.py` measured the reason: **zero** of a
composer's writes are ones the recovery never produces. This report is evidence about
the *format*, never a work queue for the recovery side — see docs/gt-oracle.md §4's
preamble and docs/tracker.md §0.

### 4.1 A `DIV` has no phase — confirmed, and DefMON supplies the divisor

DefMON is the first editor whose clock is **unambiguously declared song data**.
`sidtab_dl[y]` holds row `y` for `dl + 1` frames and then the cascade advances to
`sidtab_jp[y+1]`; that is a `DIV` in the trigger domain by construction, and no
period has to be fitted to anything.

| | DefMON, 6 tunes |
|---|---|
| cascade fetches observed | 1413 |
| … whose row STops (`dl ≥ $80`) | 69 |
| advances the delay byte predicts | 1329 |
| … **landing exactly where `dl + 1` and `jp` say** | **817 (61.5%)** |
| strictly periodic chains (≥3 fetches, one gap) | 40 |
| … of those, divisor 1 — `DIV(1)` explains nothing and is refused | 36 |
| … of those, a declared divisor ≥ 2 | 4 |
| … **of those, at `DIV(n)`'s own phase `n-1, 2n-1, …`** | **2** |

Read down it. The divisor is *not* the problem here — it is a byte of the song,
and it predicts three fifths of every cascade advance in the corpus. What breaks
is everything after: nine tenths of the periodic chains run at one row per frame,
which `DIV(1)` refuses for the reason docs/tracker.md §4d refuses it; and of the
four chains left, only **2** happen to sit where `DIV(n)` fires.

That 2 is not a repair of the gap, it is the size of the coincidence. DefMON arms
a cascade from a `PatternEvent`'s `GATE_A`/`GATE_B` at an **arbitrary frame**, so
the walk's phase is the arrangement's, not the divisor's — the same reason
GoatTracker's `mt_initchn` and SID-Wizard's `speed_counter` put their streams off
`DIV`'s phase (docs/gt-oracle.md §4.1), reached from the opposite direction. Two
editors prime a counter at init; the third arms it from a pattern. All three land
off `n-1 mod n`.

**Verdict: confirmed, sharpened, and settled.** The general extension is unchanged —
`DIV` carries a phase — but DefMON says the phase is not an init detail: it is where the
*arrangement* started the clock. docs/tracker.md §8 now records that as the three-editor
verdict and takes the field off its own list: a phase fitted per stream is the refusal
every other domain makes, and a phase supplied by the arrangement is not a separate
step. **§7.4 and this field are one problem, and the field is not worth adding before
the arrangement is recovered.**

The other 512 unpredicted advances are the same thing again: a pattern event
re-arming the slot before its delay expires. The clock is declared; when it starts
is not.

### 4.2 A transfer's emit cannot be relative to another — **route closed, DefMON gains nothing**

| | emits | share of the plane | tunes |
|---|---|---|---|
| DefMON freq the mapping cannot express | **2421** | 33.6% of `freq` | 6/6 |
| … `AF` portamento toward `current_note + AF` | 133 rows carry it | — | — |
| … `TR` with bit 7 clear: **added** to the voice's transpose buffer | 145 rows carry it | — | — |
| DefMON pulse-sweep runs refused whole (`PS`, counted in §4.4) | 188 | — | 38 rows carry `PS` |

`TR` is the arpeggio/transpose shape in its purest form — the docstring says it
outright: "bit 7 clear = relative, added to the voice's transpose buffer" — and
`AF` is portamento with the target expressed as an offset from the current note.
Neither is a table read straight through (the values depend on the base note) nor a `RAMP` (the
direction reverses). GoatTracker spells this as vibrato + the wavetable
relative-note column, SID-Wizard as `detune` + `chord_table` + `octave_shift`,
DefMON as `TR` + `AF`. Three editors, one missing route. **The index-domain half
landed** (the universal-layer step): `_dm_src` passes `TR`'s own amount instead of a
0/1 flag, so a bit-7-clear `TR` over the pattern's note column emits `SELECT[rel]` —
**7 nodes over 3 of 6 tunes**, laws green (docs/gt-oracle.md §4.5). `AF` stays: it is
the value-domain slide whose rate lives in the replay's own code.

**And DefMON reaches the second half of the extension that no tune had reached
before.** docs/tracker.md §8 records "a triangle sweep that turns around at a
declared bound needs a transfer this primitive does not have, and no tune in the
corpus reaches that limit before the step blocks it." DefMON's `PS` is exactly
that transfer: the pulse sweep "modulates `pulse_lo` each frame until clamped,
then **auto-reverses**". The step is declared, the run is real, and the mapping
refuses all 188 emits because `RAMP`'s bound wraps and this one turns.

**Verdict: the route shipped and DefMON recovers zero of it, measured.**
docs/tracker.md §2 and §4f answer the first half — a relative route whose generator
supplies a delta that a named base combines with — and GoatTracker and SID-Wizard take
4307 and 343 emits out of `RAW` with it (docs/gt-oracle.md §4.2). DefMON's six tunes are
**byte-identical before and after**: interpreted 12556/28800 either way, `rel` 0, all
2421 `('slide','detune')` emits still at the floor, law 6/6 and strict 6/6 unchanged.

The reason is worth more than the number, because it says what the route requires. `TR`
is relative in the **note-index** domain, not the value domain — it shifts
`current_note`, and §2 reads the final index straight off the replay's address bus, so
the route has nothing to add and nothing to lose there. `AF` is a slide **mode** byte:
`$01..$7F` names a portamento *target* (`current_note + AF`) and `$80..$FF` selects a
rate from a lookup table **inside the replay's own 6502 code**. That LUT is player code,
not the composer's song, so there is no declared delta to route — and a delta fitted to
the emitted stream would take all 2421 while explaining none of them, which is the
refusal docs/tracker.md §4f prices at 29559 emits on our own corpus. Counted, named,
left residual.

The second half stands open: **a `RAMP` bound that turns rather than wraps**, which
DefMON's `PS` still reaches (188 emits) and no other editor does. That is the transfer
domain, not the route domain.

### 4.3 One register plane, two generators — confirmed three times, and bounded

DefMON is the strongest evidence of the four, because it spells the composition
three different ways on three different registers — including with **XOR**, which
neither earlier editor used.

| | emits | share | tunes |
|---|---|---|---|
| DefMON `$D404` = `WGh ^ WGl` — a waveform lane and an XOR-mask lane | **546** | 22.4% of the 2433 `ctrl` emits with a bound row | 3/6 |
| DefMON `$D417` = `RE` resonance nibble `\|` per-voice routing mask | **1131** | **every `$17` write with a bound row** | 6/6 |
| DefMON `$D418` = `FV` mode nibble `\|` volume — **expressible** | 1131 | every `$18` write with a bound row | 6/6 |
| GoatTracker `$18`, SID-Wizard `$18`/`$17` (docs/gt-oracle.md §4.3) | 31631 | — | 135/135 |

Three readings, and the difference between them is the whole finding.

- **`$D417` is the pure case and it is total.** Resonance is a per-instrument byte
  a table holds, but the low nibble is a routing mask the driver assembles from
  three voices' flags, so **not one `$17` write in the corpus is a pure lane
  read** — all 1131 with a bound row refused, on 6 tunes of 6 (the other 69 of
  the plane's 1200 writes precede any staging and are ghost). docs/tracker.md §6 measures
  `$17` from the recovery side as "really a table plane", 28.6% naming a declared
  byte; from the composer's side the table is right there and the mask is what
  stands between them.
- **`$D404` is survivable only by multiplying out, and DefMON says how far that
  goes.** The gate is bit 0 of `WGl`, so `WGh ^ 1` is the lane and `WGh & ~1` its
  gate image — exactly `tracker._key_table`'s three readings, and 1887 emits map
  that way. Any other mask bit is waveform flicker, and 546 emits need one. The
  masks a tune uses number 1 to 7 distinct values, so the product table
  docs/gt-oracle.md §4.3 warns about would be up to 256 × 7 = **1792 rows** here.
- **`$D418` is the counter-example, and it is the useful one.** `FV` is a mode
  nibble ORed with a volume level — the same shape that costs GoatTracker 14117
  emits — but DefMON's driver supplies the volume as its own `ORA #$0F`
  **constant**. A constant second generator is not a second generator: the lane
  `(FV & $F0) | $0F` is still declared data, and all 1131 emits map.

**Verdict: confirmed, and the boundary is now measured.** A masked route is needed
exactly when both generators vary; where one is a program constant the plane can
be folded into a derived lane, and where one is a three-state gate it can be
multiplied out. `$17` is neither, and it is 100% refused. (Another agent is
implementing the masked route; this is the count, not a proposal.)

### 4.4 A byte-plane `RAMP` cannot express a wider accumulator — confirmed, totally

| | emits | tunes |
|---|---|---|
| DefMON `pw_hi` during a `PS` sweep (a 12-bit accumulator's carry) | **812** | 6/6 |
| DefMON `pw_lo` swept past what a wrapping `RAMP` predicts | **921** | 6/6 |
| DefMON `$D416` = `acc << 1` — a byte *view* of a wider accumulator | **1200** | 6/6 |
| DefMON `PS` runs the mapping refuses whole | 188 | — |
| **`RAMP` emits the DefMON oracle generates** | **0** | **0/6** |

The last row is the headline. `Coverage.classes` reports `ramp` 0 and `seed` 0
across all six tunes: **`RAMP` is the one transfer the primitive offers that
DefMON cannot use at all.** Its two accumulators are the two shapes `RAMP` was not
built for — a pulse sweep that turns at a clamp (§4.2) and a cutoff slide the
driver emits as `acc << 1`, where `ACID` is an explicit 16-bit command ("low byte
= step magnitude, high byte = direction + control").

GoatTracker's pulse (12-bit) and frequency (16-bit), SID-Wizard's explicit
`(PWHIGHO, PWLOGHO)` pair, our own recovery's `pw`/`cutoff`, and now DefMON's
`PS`/`ACID`. **Verdict: confirmed, and it is total rather than partial here.**

### 4.5 A new wall: an accumulator whose step is itself accumulated

Not one of the four, and reported the same way.

| | emits | tunes |
|---|---|---|
| DefMON `$D416` cutoff, driven by `CP` / `ACID` | **1200** | 6/6 |

`CP` is documented as "cutoff-hi delta — added to the cutoff accumulator's
**saturation step** each frame", and `ACID` as a slide *command*. Measured, that
is what they are: on `Automatas` the `CP` bytes in play are `$E0…$FC` (−32 to −4)
while the emitted `$D416` moves by ±1 per frame. The declared byte is the **rate**
of the step, not the step. A `RAMP`'s `step` is a constant field of the node, and
no route feeds one generator's emit into another generator's parameter, so the
shape has no expression at all — and a step fitted to the observed ±1 would pass
the law while explaining nothing (docs/tracker.md §4c), so all 1200 emits stay
residual.

**The general extension**: a `RAMP` whose `step` is a *port* another generator can
drive, not a constant. It is §4.2's composition moved from the value domain to the
parameter domain, and it is what a second-order envelope needs. One editor so far,
so it is reported and not proposed.

### 4.5b An index route is absolute — confirmed, and DefMON is the third editor

docs/gt-oracle.md §4.5's wall, hit from a third direction. The index route carries a
row from one generator to another, which is what §3.2 recovers; what it cannot carry is
a row that is **one index plus another**, and DefMON needs that in both places the other
two editors need it.

| | emits refused | tunes | share of the DefMON freq plane |
|---|---|---|---|
| `TR` with bit 7 clear — added to the voice's transpose buffer | **525** | 3/6 | 525/7200 = **7.29%** |
| the arpeggio shape: the pattern's note no longer names the index | 1141 | 6/6 | — |
| `slot_a`/`slot_b` no longer naming the fetched sidTAB row | 3439 | 6/6 | — |
| GoatTracker 1690 + SID-Wizard 4738 (docs/gt-oracle.md §4.5) | 6428 | 20/135 | — |

`TR` is the entry docs/dm-oracle.md §4.2 already refused as "relative in the
**note-index** domain, not the value domain", and it said the cost was zero because
"§2 reads the final index straight off the replay's address bus, so the route has
nothing to add and nothing to lose there". **That is no longer true and the number
says so.** Now that the pattern's own note column supplies the index (§3.2), a `TR`
between it and the pitch table refuses the emit outright: 525 emits on 3 of 6 tunes,
7.29% of the plane — the *highest* share of the three editors, on the smallest corpus.

The orderlist half is the same object again. A pattern's absolute event index is
`base(the arranger's entry) + the event counter`; a generated row names **one** source,
so the arranger cannot be a generator of its own and an orderlist entry counts as
represented where its pattern's chain exists and fires (31 of 1476). The
`sidtab_jp` back-edge is the sharpest case in the corpus: **0 of 227 jump targets are
row 0**, so the wrap `_emit` already has is never DefMON's loop point, and every walk
is carried unrolled instead (docs/gt-oracle.md §3.2b).

**Verdict: confirmed on a third independent editor, and it is one object.** `Rel` in
the index domain — a row that is a declared delta over a named base index — closes the
transpose and the orderlist together, on all three editors, for **6953** freq-plane
emits and 1476 + 7248 + 6579 orderlist entries. It is not §4f with another register:
§4f combines two values into a byte, this combines two rows into an index.

**Status, re-measured: still owed, not banked.** The object landed in the primitive
(docs/tracker.md §2, #102) and this mapper still refuses. `_dm_src` computes
`shift = 1 if tr and not tr & 0x80 else 0` — a *flag*, not `TR`'s amount — and hands it
to `gtoracle._patt_src`, which prices any nonzero shift as `refused_transpose` and
returns nothing. A census of the mapped graphs by `(transfer, route)` over 6 DefMON
tunes (and 12 GoatTracker, 12 SID-Wizard) finds **zero** `SELECT[rel]` nodes. The 525
is a refusal count, and emitting it needs `_dm_src` to compute the shift `TR` actually
applies, on top of docs/gt-oracle.md §4.5's four changes to the shared mapper.

### 4.6 What is not a deficiency, and is reported anyway

**The driver's ghost.** 7911 emits — 48.7% of the strict graph's `RAW` — are a
register the song never programs, written every frame from a band operand still
holding its power-on value (6744 `ghost:held`, 1167 `ghost:ctrl` before the first
note arms a voice). DefMON's driver writes **all 25 registers every frame** in a
fixed order, so the ghost is structural here rather than incidental. It is not a
`RAW` the primitive forces — `SELECT((0,), ())` would take every one and pass the law
— and it is refused for the reason docs/tracker.md §6 refuses the filter plane's
program constants: it explains no index. Counted, named, left residual.

**The write-order refusal.** 1114 emits (`select:('ins','ad')` 557,
`select:('ins','sr')` 557) are writes the mapping *did* explain, refused because
the order-preserved ctrl/AD/SR section is typed whole or replayed whole per voice
per frame (docs/gt-oracle.md §2). That is the same guarantee `tracker._buckets`
gives and the same cost docs/tracker.md §6 measures at 23631 emits from the
recovery side. It is a partition rule, not a format gap.

### 4.7 How much of the mapped graph is `RAW`

Interpreted share is the admitted graph's; the `RAW` breakdown is the strict
graph's own residual, whose denominator is that graph's write count.

| | admitted interpreted | strict `RAW` | of it: the primitive cannot express (§4.2–§4.5) | of it: driver ghost (§4.6) | of it: write order (§4.6) | rest |
|---|---|---|---|---|---|---|
| DefMON, 6 tunes | 12556/28800 = 43.6% | 16244 | **7219 = 44.4%** | 7911 = 48.7% | 1114 = 6.9% | **0** |

The `rest` column is zero: **every residual emit in the DefMON oracle is
attributed to a named cause.** Evidence classes are `tracker`'s own and are never
summed: `lane` 11725 / `gate` 831 / `ramp` 0 / `seed` 0 / **`imm` 0**. No emit
rests on the shallow `imm` class — every interpreted byte is a byte of the
composer's sidTAB at a row the replay's own address bus named.

The arrangement (§3.2, §4.5b) moves **nothing** in that table and is not in it: it is
the index domain, so it changes where a `SELECT`'s row comes from and not which emits
are interpreted. Interpreted 12556/28800, every plane and every class byte-identical
before and after it — the check that it added structure and not coverage.

**The format-vs-recovery gap, stated in its sharpest form.** §4.3's masked route and
§4.2's relative route bought two editors 26167 and 4650 emits out of their own models
against 676 and 316 from decompiled binaries, and DefMON gained nothing from either.
The arrangement is where the pattern is total: **73766 emits at a generated row across
three editors' own models — 41424 GoatTracker, 28143 SID-Wizard, 4199 DefMON — against
0 from binaries** (docs/tracker.md §7.4: 324 of 366 resolved deref sites refuse because
the row index is not a cell the program text walks, and the 42 that remain point at
blocks no `datadecl` region covers). DefMON is the cleanest instance of it, because here
the oracle and frameprog are two executions of the *same* 6502 driver and they agree
bit for bit (§3.1) — so the gap is not fidelity and is not the tune. The format
expresses an arrangement; a driver's program text does not name one.

## 5. Known limits

- Six tunes is the whole DefMON population of the 682-tune cache. Every number
  here is over those six; they are the corpus, not a sample of it.
- The mapping now reads the **pattern walk** as well as the cascade: the arranger
  reads and the packed event reads name the song step, the pattern and the event
  (§3.2). What it still does not model is the *timing* — a `PatternEvent`'s
  `GATE_A`/`GATE_B` re-arms a slot at a frame the walk's own duration nibbles would
  predict, and the mapping takes the re-arm from the bus rather than predicting it.
  That is the 512 unpredicted advances of §4.1, still open.
- The event index is charged to the voice the arranger last gave that pattern to.
  Two voices playing one pattern at different positions would be ambiguous; every
  emit is re-checked against the declared column, so a mis-named event costs
  coverage and can never cost correctness — 1141 emits are refused that way.
- `dm_pattern_map` ends where the packed stream stops matching the rule it replays,
  so patterns past that point are unreachable (20, 30, 90, 2, 40 and 118 of 128 are
  mapped on the six tunes). It is a mapping gap, not a format one: 1756 emits, named
  `no_pattern_row`.
- `dm_sites` recognises one driver layout, verified opcode by opcode: the three
  `$0x31`-strided write bands and the three global filter stores at fixed offsets
  from the signature. All 6 cached tunes carry it; a variant is refused rather
  than mis-read, and would report as unmapped.
- The `$D415` cutoff-lo register is never written by this driver, so it never
  appears in a frame's shape and contributes nothing to either denominator.
- Row provenance is the replay's own address bus and every emit is re-checked
  against the declared byte, so a mis-named row costs coverage and can never cost
  correctness — the same guarantee docs/gt-oracle.md §5 states for the other two
  editors.
- `tools/dm_compare.py` imports `tools/gt_compare.py`'s harness (`_lifted`,
  `_ours`, `_rows`, `_bijection`, `_cov`, `_struct_axis`) rather than duplicating
  it; only the two axes that need a register parameter are re-stated locally,
  because `gt_compare`'s versions hard-code `freq_lo` and `ad`.
