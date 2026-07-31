# gt-oracle — a real editor's song, in the universal format

`docs/tracker.md` recovers a tune from a frame program and verifies it by one
projection equality. That law says the recovery **reproduces** the tune; it says
nothing about whether the structure recovered is the structure the composer
wrote. This layer supplies the missing half: it reads a song out of a real
editor's own model and expresses it in the **same primitive**, so the mapped
graph is checkable by the **same law**.

`deity_informant/gtoracle.py` is that mapper, `tools/gt_compare.py` the measurement.
Two editors are mapped: **GoatTracker 2** through `pygoattracker`, whose
`sid2sng` decompiles a GT-packed `.sid` straight out of HVSC, and **SID-Wizard**
through `pysidwizard`. Both are optional extras (`pip install -e '.[nativeoracle]'`);
the tests skip without them and the hermetic job stays hermetic.

## 1. The rule: express, never transliterate

Every native structure is stated in `(transfer, trigger, route)` over
`DIV`/`LOOKUP`/`SELECT`/`RAMP`/`EDGE`/`RAW` — docs/tracker.md §2 exactly as it
stands. **Editor structures are translated to generic ones before anything is
compared**, and the test applied to every field translated is: *would this
structure exist in an editor that had never heard of this one?*

| generic structure | GoatTracker spells it | SID-Wizard spells it | the tracker recovers it as |
|---|---|---|---|
| pitch table | `FREQ_TABLE` (or the packed image's) | `NOTE_FREQ_LO/HI` | `Pitch`, §4/§4b |
| instrument bank, ADSR lanes | `Instrument.attack_decay`/`sustain_release` | `attack`/`decay`/`sustain`/`release` nibbles | ctrl/AD/SR `SELECT`, §5 |
| **ADSR preamble on the note-on edge** | one global `adparam`, gated per instrument by `gateoff_timer` bits `$80`/`$40` | four per-instrument fields `hr_attack`/`hr_decay`/`hr_sustain`/`hr_release` | the `$80` ctrl immediate, `imm` class (Commando, 102 emits) |
| waveform program + gate images | `wavetable.left`, `wave & gate` | `wf_table`, `waveform & ptn_gate` | the ctrl lane and its three held readings, §5 |
| pulse program: a set row, then a sweep | `pulsetable` left/right | per-instrument `pw_table` | `SELECT` + `RAMP`, §4b/§4c |
| filter program | `filtertable` left/right | per-instrument `filter_table` | `SELECT` + `RAMP`, §4b/§4c |
| arpeggio / chord table | wavetable relative-note column | `default_chord` + `chord_table` + `arp_speed` | — (§7.3, not implemented) |
| transpose | orderlist `Transpose` | sequence `Transpose`, `octave_shift` | — (§7.4) |
| gate-off images | `gateoff_timer` | `gateoff_wf`/`gateoff_pw`/`gateoff_filt` | the gate images of the same lanes, §5 |

Three different spellings of the hard restart become **one** node shape: a
`LOOKUP` of ADSR bytes fired by the note-on `EDGE`, ahead of the instrument's own
`SELECT`. Nothing editor-specific enters the format. Where a structure has no
generic reading, that is §4 below — a deficiency, never a new node type.

## 2. Two graphs, two purposes

- **admitted** (`graph`) — a native generator's byte is emitted only where it
  equals the byte the projection actually wrote at that register in that frame,
  *and* the composer's table still holds that byte at the recovered row. That is
  the pair `tracker._lane_key` applies (`mem0[src] == val`). Every other write
  stays in `RAW`. It passes the law by construction, and its `Coverage` measures
  **how much of the tune the composer's own data explains**.
- **strict** (`strict`) — every byte comes from the native model, never from the
  observation. `RAW` here replays what the native model *predicts*. The law can
  and does fail, and `Report.matched` is how many frames the song data alone
  reproduces.

Both take the per-frame write **schedule** — which registers a frame writes, in
what order — from the driver, exactly where `tracker` takes its `EDGE` counts
(docs/tracker.md §5: "the `EDGE` counts are observed"). Every *value* is the
song's. The driver's frame offset is searched over four frames and reported
(`align`), because a packed driver's first play call emits its init tail rather
than a song frame.

The order-preserved ctrl/AD/SR section is typed whole or replayed whole per voice
per frame, against a per-voice register order every frame's section is a
subsequence of — the same guarantee `tracker._buckets` gives, constructed rather
than hoped for, and checked by the law.

## 3. What it measures

200 frames, PSID start subtune, `tools/gt_compare.py`. 682 cached HVSC tunes,
**72 decompile as GoatTracker**, 71 also lift to a frame program; 64 cached
SID-Wizard `.swm` modules.

### 3.1 The law

| | admitted law | strict, full window | strict ≥ 20 frames |
|---|---|---|---|
| GoatTracker, 71 tunes, vs **frameprog** | **71/71** | **4** | 7 |
| SID-Wizard, 64 modules, vs **its own player** | **64/64** | **64/64** | 64 |

The two rows are not the same test and the table says so. The SID-Wizard row is
one boundary short: `pysidwizard` reads `.swm` only — it has no packed-`.sid`
decompiler — so its reference is the editor's own player rather than a frame
program lifted from 6502. On **that** boundary the result is total: **every one
of 64 real SID-Wizard modules is reproduced frame for frame, for the whole
window, from a graph built out of nothing but the module and the universal
primitive.** The universal format demonstrably expresses a real editor's song end
to end.

The GoatTracker row crosses the 6502 boundary and is where the interesting number
is. Four tunes — `Benedens_Oliver/Dear_Enemy` (199/199),
`Chiummo_Aldo/25_Years_tune_1` (199/199), `Cubaxd/An_Old_Era` (199/199),
`Dlx/86400` (198/198) — reproduce the *packed player's own SID writes* for the
whole window from the decompiled song. The rest diverge earlier, and **the cause
is measured separately rather than charged to the mapping**: comparing the native
player's forward-filled register grid against the projection's, only **11 of 71
tunes** agree on ≥150 of 200 frames at any offset. `pygoattracker`'s decompiler
and playroutine, not the mapping, are what the other 60 are limited by; the
admitted graph passes the law on all 71 regardless, because a byte the two
disagree on is refused rather than emitted.

One player-fidelity fix is carried in the mapper and is stated as such: the
packed driver (`gt2reloc`) keeps the **whole** set-pulse byte in `$D403`, where
the editor player masks it to the SID's 12 bits. Correcting that in the probe
subclass takes the ≥150-frame count from **5 to 11 tunes** and is the difference
between one tune reproducing and four.

### 3.2 The three axes, against ground truth

Over the 4 tunes whose strict oracle reproduces the whole window (and, in
brackets, the 7 that reach ≥20 frames):

| axis | measured | result |
|---|---|---|
| **Pitch** | our note-lane `SELECT` row on `freq_lo` vs the pitch-table row the editor's player indexed | **1.0000 over 1847 emits, offset 0** (0.9896 over 3178) |
| **Instruments** | our `SELECT` rows on the `ad` plane vs the native instrument number | **1.0000 over 1273 emits, a bijection on 4/4 tunes** (1.0000 over 1601, 7/7) |
| **Structure** | native patterns / orderlist entries the recovery represents at all | **0 of 118 patterns, 0 of 228 orderlist entries, 0 of 7672 rows** |

The pitch axis compares like with like and says which: the native side is
`chan.lastnote`, the row the player actually handed the frequency table, **not**
the pattern note — a wavetable arpeggio changes the former and not the latter.
Offset 0 means our recovered row *is* the composer's note index, not merely
proportional to it.

The instrument axis is a bijection, which is the thing to look for: our recovered
rows are 1, 3 and 5 distinct values on those tunes and each maps to exactly one
native instrument number and back.

The structure axis is §7.4's prize, and the answer is zero — measured, not
asserted. `our_index_nodes`, the count of nodes whose route is `Fire` and whose
transfer is not `EDGE`/`DIV`, is **0 on every tune**: no generator addresses
another generator's index, so no orderlist and no pattern is represented. Of the
native note-ons, only **2 of 75** (562 of 1785 corpus-wide) coincide with a fire
of one of our instrument-plane `EDGE` streams.

### 3.3 Coverage, side by side

The same 4 tunes, oracle against recovery, on identical windows:

| plane | oracle (the composer's data) | tracker recovery |
|---|---|---|
| freq | 3694/4742 = 78% | 3694/4760 = 78% |
| ctrl | 1868/2385 = 78% | 0/2400 = 0% |
| ad | 1362/1816 = 75% | 1287/1825 = 71% |
| sr | 1362/1816 = 75% | 1460/1825 = 80% |
| pw | 1001/3576 = 28% | 48/3594 = 1% |
| filter | 448/2782 = 16% | 0/2796 = 0% |
| **all** | **9735/17117 = 56.9%** | **6489/17200 = 37.7%** |

**The freq plane agrees emit for emit** — 3694 either way. The tracker recovers
exactly the note-lane emits the composer's own table accounts for, which is the
strongest single piece of evidence here that the recovery is structurally right
and not merely projection-equal.

Over all 71 GT tunes the oracle reaches 36.9% and the recovery 42.2%; the
recovery is ahead there because the oracle is limited by decompiler fidelity on
the other 60 tunes, not because it explains less.

The trigger domain is **0 generated of 57531 fires** for the oracle and 0 of
49660 for the recovery. Mapping a real editor's song does not lift one trigger
off the floor, and §4.1 says why.

## 4. The deficiency report

Every wall actually hit, with its weight. A wall **both** editors hit is a real
gap in the primitive; one only a single editor hits is something to normalize
away instead, and none of the entries below is of that kind.

### 4.1 A `DIV` has no phase, and note-on streams never sit at its phase

| | GoatTracker, 71 tunes |
|---|---|
| voices with ≥3 note-ons | 120 |
| … strictly periodic (one distinct gap) | 31 (467 onsets) |
| … **of those, at `DIV(n)`'s own phase `n-1, 2n-1, …`** | **0** |

`DIV` is the only transfer that lifts a stream off the `EDGE` floor, and against
ground truth it generates **nothing**. The reason is not the divisor — the tempo
*is* declared song data, in the orderlist (`TempoOverride`) or in a `SETTEMPO`
command — it is the phase. GoatTracker's `mt_initchn` primes the tick counter to
1, so onsets land on frames `0, n, 2n, …` while `DIV(n)` fires on `n-1, 2n-1, …`.
SID-Wizard's `speed_counter` is pre-warmed to 2 for the same editor reason, with
the same effect. docs/tracker.md §8 estimated a phase field would open ~1.8% of
the trigger domain from the recovery side; from the composer's side it is the
difference between 0 and every periodic note-on stream in the corpus.

**The general extension**: `DIV` carries a phase (equivalently, a `RAMP`-like
seed in the trigger domain), because every editor's row counter is loaded once at
init and reloaded at zero, and no editor arranges for that load to coincide with
frame `n-1`. Justified by two independent editors and by the 363 divider-shaped
streams docs/tracker.md §6 already measures at a non-`DIV` phase.

### 4.2 A transfer's emit cannot be relative to another's

| | GoatTracker | SID-Wizard |
|---|---|---|
| freq emits the mapping cannot express | 12744 vibrato (4.5%, 28 tunes) + 500 portamento carry (9 tunes) | 38840 detune/vibrato (17.0%, 64 modules) |

Vibrato is a *bipolar offset applied to the note the pitch table holds*: neither
`LOOKUP` (the values depend on the base note) nor `RAMP` (the direction reverses
at a bound). The same shape covers the arpeggio (`wavetable` relative-note column;
SID-Wizard's `default_chord` + `chord_table`), the orderlist `Transpose`, and
SID-Wizard's `octave_shift` and `detune` — every one of which is "a table whose
emit is added to another generator's emit". docs/tracker.md §7.3 names this from
the recovery side ("an arp step is a downstream generator emit on that edge, so
it must never appear as a fresh row") and §8 names the triangle case ("a triangle
sweep that turns around at a declared bound needs a transfer this primitive does
not have").

**The general extension**: a route that **composes** with the value already on a
plane — an additive route, and a `RAMP` bound that turns rather than wraps —
because every editor has at least three tables that offset a note rather than
replace it. Both editors, and our own recovery's §7.3/§8.

### 4.3 One register plane, two generators

| | emits | share | tunes |
|---|---|---|---|
| GoatTracker `$18` = filter mode `|` master volume | 14117 | 5.0% | 71/71 |
| SID-Wizard `$18` = mode `|` volume | 12800 | 5.6% | 64/64 |
| SID-Wizard `$17` = resonance `|` per-voice routing | 4714 | 2.1% | 64/64 |
| our own recovery, `$18` "a declared mode nibble combined with a volume level is not a declared byte" (docs/tracker.md §6) | 16597 | — | — |

`$18` is a mode nibble ORed with a volume level, and `$17` a resonance nibble
ORed with a routing mask assembled from three voices' flags. Both are two
independent generators writing one plane, and the primitive routes one generator
to one plane. The ctrl plane is the same shape and is only survivable because the
gate has three states, so the lane can be **multiplied out** into gate images
(`_ctrl_table`, and `tracker._key_table` before it); with two varying nibbles the
product table is 256 rows of nothing.

**The general extension**: a masked route — a plane fed by more than one
generator, each with a bit mask. Two editors plus our own recovery's measured
16597 emits.

### 4.4 A byte-plane `RAMP` cannot express a wider accumulator

| | emits | tunes |
|---|---|---|
| GoatTracker `pw_hi` during a pulse sweep (a 12-bit accumulator's carry) | 6904 | 33 |
| GoatTracker `freq_hi` during a portamento (16-bit) | 500 | 9 |
| SID-Wizard pw runs the mapping refuses whole | 7642 | 64 |

`RAMP(seed, step, bound)` emits into one register plane, so the *low* byte of an
accumulator is exact — `lo` advances by `step mod 256` — and the high byte is
not: it moves only on carry. The mapping takes the low byte as a `RAMP` and the
high byte as the set row still standing (a declared byte at a recovered row);
where a carry has moved it, it is residual. docs/tracker.md §4c has the same
limit from the other side — the sweep is implemented on `pw` and `cutoff` as byte
accumulators only.

**The general extension**: an accumulator wider than its plane, with a route that
names a byte lane of it. GoatTracker's pulse (12-bit) and frequency (16-bit),
SID-Wizard's pulse (12-bit, an explicit `(PWHIGHO, PWLOGHO)` 16-bit pair in the
player), and our own recovery's `pw`/`cutoff`.

### 4.5 What is not a deficiency, and is reported anyway

**The driver's ghost.** 93030 GoatTracker emits (68% of the strict graph's `RAW`)
and 74229 SID-Wizard emits (53%) — `ghost:pw` alone is 39440 on 58 GT tunes — are
a register the song **never programs**, written every frame from a ghost register
holding its power-on value.
It is not a `RAW` the primitive forces: `LOOKUP((0,))` would take every one of
them and pass the law. It is refused for the reason docs/tracker.md §6 refuses
the filter plane's 34177 program constants — it explains no index, and on a
register that takes a constant it is the observation, not the song, choosing.
Counted, named, and left residual.

**The ADSR preamble's bytes.** `select:('hr','ad')`/`('hr','sr')` stay residual
on 65 of 71 GT tunes (675 emits) because `gt2reloc` bakes the hard-restart AD/SR
into player *code* and `pygoattracker.sid.PackedInfo` does not recover them. The
primitive expresses this fine — it is a `LOOKUP` on the note-on edge, and
SID-Wizard's per-instrument version maps cleanly. This is a decompiler gap, not a
format gap, and **our own recovery does get these bytes**: `tracker._immediates`
reads program constants stored to a SID register class (docs/tracker.md §5),
which is exactly where they live.

It is also the single most common cause of an early strict divergence, and the
counterfactual is measured rather than guessed. Feeding `gt_native` the driver's
pair — read off the projection at the first frame the song stages it, two observed
bytes per tune, a measurement and not a shipped rule — takes the strict full-window
count **4 → 5** with **10 tunes improving and none regressing**;
`Fegolhuzz/15_minuter_fraan_Esloev` alone goes **4 → 199 frames**. So the datum is
worth about one tune in seventy, and the rest of the gap is elsewhere.

### 4.6 How much of the mapped graph is `RAW`

Interpreted share is the admitted graph's; the `RAW` breakdown is the strict
graph's own residual, whose denominator is that graph's write count.

| | admitted interpreted | strict `RAW` | of it: the primitive cannot express (§4.2–§4.4) | of it: driver ghost (§4.5) | rest |
|---|---|---|---|---|---|
| GoatTracker, 71 tunes | 104368/282516 = 36.9% | 136378 | **34265 = 25.1%** | 93030 = 68.2% | 9083 |
| GoatTracker, strict-full 4 | 9735/17117 = 56.9% | 7382 | **1836 = 24.9%** | 5350 = 72.5% | 196 |
| SID-Wizard, 64 modules | 89348/228702 = 39.1% | 139354 | **63996 = 45.9%** | 74229 = 53.3% | 1129 |

No `RAW` in either graph is padding: the strict graph's residual carries what the
*native model predicts*, so a `RAW` write is still a claim about the song and the
law still tests it. Evidence classes are `tracker`'s own and are never summed:
GoatTracker `lane` 95306 / `gate` 3166 / `ramp` 4620 / `seed` 1276 / `imm` 0;
SID-Wizard `lane` 72581 / `gate` 7212 / `ramp` 8372 / `seed` 1183 / `imm` 0.
**No emit in either oracle rests on the shallow `imm` class** — every interpreted
byte is a byte of the composer's table at a row the editor's own player reached.

## 5. Known limits

- `pysidwizard` parses `.swm` modules only. A SID-Wizard tune packed into an HVSC
  `.sid` cannot be reached, so no SID-Wizard graph is gated against frameprog and
  the three-axis comparison — which needs a frame program on the other side — is
  GoatTracker-only. Grepping the 682 cached `.sid` files for the `SWM1` magic
  finds none, as expected: the packer emits a driver-specific layout.
- The GoatTracker oracle's ceiling is `pygoattracker`'s decompiler and player, not
  the mapping (§3.1). 60 of 71 tunes diverge from the packed player's own writes
  within 200 frames.
- `sw_native` maps SID-Wizard's first subtune only, since `SWMPlayer` reads
  sequences 0-2. Seven of the 64 cached modules have more.
- The filter plane is unmapped for SID-Wizard (0/51200): its cutoff walk is owned
  by whichever voice currently holds `filter_controller_voice`, and the mapping
  names no row for it. That is a mapping gap, not a format one.
- Row provenance is taken from the editor's own player — from an instrumented
  subclass for GoatTracker, from the public pointer state for SID-Wizard — and
  every emit is re-checked against the table byte, so a mis-named row costs
  coverage and can never cost correctness.
