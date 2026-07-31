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
| transpose | orderlist `Transpose` | sequence `Transpose`, `octave_shift` | — (§4.5, a relative *index*) |
| **orderlist, pattern, row counter** | `Channel.entries`, `Pattern.rows`, `pattbase + pattptr/4` | `sequences`, `Pattern.rows`, `pattern_row` | an `Index` chain: `RAMP`/`LOOKUP` → the pattern's column → the table, §3.2b |
| gate-off images | `gateoff_timer` | `gateoff_wf`/`gateoff_pw`/`gateoff_filt` | the gate images of the same lanes, §5 |
| **two fields in one register** | `$18` = `filtertype & $70` \| `masterfader` | `$18` = mode \| volume; `$17` = resonance \| three voices' routing flags | masked `SELECT`s over disjoint bit fields, §4e |
| **a value offset rather than replaced** | `_vibrato`: `freq ± speedtable.right[row]` | `WRPITCH`: the pitch lane `+` the WF program's detune column | a relative route over `Prev` or `Node(i)`, §4f |

Three different spellings of the hard restart become **one** node shape: a
`LOOKUP` of ADSR bytes fired by the note-on `EDGE`, ahead of the instrument's own
`SELECT`. Nothing editor-specific enters the format. Where a structure has no
generic reading, that is §4 below — a deficiency, never a new node type.

The last two rows are the deficiencies this report has closed. §4.3 measured the
first, docs/tracker.md §2 and §4e answered it with a **masked route**, and the mapping
now expresses both editors' shared registers with it: GoatTracker's `$18` as the
filter program's set row masked `$70` beside the master volume masked `$0F`, and
SID-Wizard's `$17` as the resonance nibble beside **three** routing generators, one
per voice, each that voice's own instrument flag. §4.2 measured the second, and §2 and
§4f answered it with a **relative route**: the generator supplies a delta and the route
names the operation and the base. Nothing editor-specific was added for either — the
route kinds are the ones `tracker` uses on its own recovery.

The last row is the third such answer and the first in the **index** domain: an `Index`
route carries a row from one generator to another, so all three editors' orderlist,
pattern and row counter become the same three nodes (§3.2b). What it still cannot carry
is a row that composes two indices, which is §4.5 and is priced there.

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
| **Structure** | native patterns / orderlist entries / rows each side represents | **format 10 of 118 patterns, 10 of 228 orderlist entries, 63 of 7672 rows; recovery 0, 0, 0** |

The pitch axis compares like with like and says which: the native side is
`chan.lastnote`, the row the player actually handed the frequency table, **not**
the pattern note — a wavetable arpeggio changes the former and not the latter.
Offset 0 means our recovered row *is* the composer's note index, not merely
proportional to it.

The instrument axis is a bijection, which is the thing to look for: our recovered
rows are 1, 3 and 5 distinct values on those tunes and each maps to exactly one
native instrument number and back.

The structure axis is §7.4's prize, and it now carries **two numbers that must not
be summed**. `index_nodes` — nodes whose route is `Index`, and `SELECT`s whose rows
are `("node", j)` rather than a recovered run — is computed on **both** graphs, the
mapped one and the recovery's:

| the four strict-full tunes | the format (the composer's own model) | the tracker's recovery |
|---|---|---|
| `Index` nodes | **51** | **0** |
| `SELECT`s read at a generated row | **46** | **0** |
| emits at a generated row | **4811** | **0** |
| patterns represented | **10** of 12 walked, of 118 in the songs | **0** |
| pattern rows represented | **63** of 400 walked, of 7672 | **0** |
| orderlist entries represented | **10** of 12 walked, of 228 | **0** |

Per tune, patterns / rows / orderlist entries the mapped graph represents:
`Dear_Enemy` 3/12/3, `25_Years_tune_1` 3/17/3, `An_Old_Era` 2/18/2, `86400` 2/16/2.
The right-hand column is the expected result and not a failure: docs/tracker.md §7.4
measures the recovery side at **0 emits over 649 tunes** because 324 of 366 resolved
deref sites refuse — the row index is not a cell the program text walks — and the 42
left name no declared pattern block. Nothing was loosened to move it.

Corpus-wide the format side reaches **799 `Index` nodes, 737 generated-row `SELECT`s
and 41424 emits over 71 tunes** — 185 of 2407 patterns, 997 of 123941 rows and 213 of
6579 orderlist entries — against **0, 0, 0** for the recovery on the same windows. Of
the native note-ons, **2 of 75** (562 of 1785 corpus-wide) still coincide with a fire
of one of our instrument-plane `EDGE` streams; the arrangement moves neither the value
domain (§4.7) nor the trigger domain (§4.1) — it moves the **index** domain, which had
no measurement at all before this.

**The format-vs-recovery gap is this project's central fact, and this axis is its
sharpest form.** §4.3's masked route and §4.2's relative route bought 26167 and 4650
emits out of the editors' own models against 676 and 316 from decompiled binaries. The
arrangement is the limit of that pattern: **73766 emits at a generated row across three
editors' own models, against 0 from binaries.** The format expresses an arrangement; a
6502 driver's program text does not name one, and docs/tracker.md §7.4 prices exactly
why — 324 of 366 resolved deref sites refuse because the row index is not a cell the
text walks, and the 42 that remain point at blocks no `datadecl` region covers. The
right reading is not that the recovery failed: it is that the *format* was the wall for
the editors and *provenance* is the wall for the recovery, for the third time and by the
largest margin yet.

### 3.2b What the arrangement is, as node shapes

The same three nodes in all three editors, and none of them is editor-specific:

```
row counter   Index  RAMP(row0, step, 0)  or  LOOKUP(the walk)   # which row
pattern       Index  SELECT(the pattern's own column, ("node", counter))
plane         Plane  SELECT(the pitch table or the bank, ("node", pattern))
```

One chain per **(voice, pattern)**, shared by every song step that plays that
pattern and by every register that reads it — that is §7.4's "shared subgraphs for
reuse", and it is why `SELECT`s read at a generated row (737) outnumber `Index`
nodes' chains. What moves out of observation is the **note index and the instrument
number**: the row a voice's pitch `SELECT` reads is now a byte of the composer's own
pattern, not a row read off the player. The row *of the pattern* is still the walk.

The row counter is a real `RAMP` — seed, step, no wrap — on **9** GoatTracker chains
and **38** SID-Wizard ones, and the unrolled walk as a `LOOKUP` on the rest. `RAMP`
wraps `mod bound`, i.e. always back to **0**, so it is the right back-edge only for a
walk that starts at row 0; a pattern whose base row is not 0 cannot express its own
repeat with the wrap, and is carried unrolled instead.

**The wrap and the editor's loop point, measured rather than assumed.** `_emit` wraps
a `LOOKUP` at `% len(seq)`, which is the back-edge only where the editor's own restart
is the start of the table:

| | wrap IS the loop point | it is not |
|---|---|---|
| GoatTracker channel orderlists (`Orderlist.restart`) | **155** of 213 | 58 |
| SID-Wizard sequences (`Loop.position`) | **114** of 171 | 57 |
| DefMON cascade jumps (`sidtab_jp`, docs/dm-oracle.md §3.2) | **0** of 227 | 227 |

Where it is not, the walk is carried **unrolled to the loop point** by the `LOOKUP`
form rather than by a wrong back-edge, so no chain is built on a back-edge the editor
does not have. No back-edge machinery was added; the wrap `_emit` already has is the
only one used.

### 3.3 Coverage, side by side

The same 4 tunes, oracle against recovery, on identical windows:

| plane | oracle (the composer's data) | tracker recovery |
|---|---|---|
| freq | 3749/4742 = 79% | 3694/4760 = 78% |
| ctrl | 1868/2385 = 78% | 0/2400 = 0% |
| ad | 1362/1816 = 75% | 1287/1825 = 71% |
| sr | 1362/1816 = 75% | 1460/1825 = 80% |
| pw | 1001/3576 = 28% | 48/3594 = 1% |
| filter | 639/2782 = 23% | 0/2796 = 0% |
| **all** | **9981/17117 = 58.3%** | **6489/17200 = 37.7%** |

**The freq plane agreed emit for emit at 3694 either way** — the tracker recovers
exactly the note-lane emits the composer's own table accounts for, which is the
strongest single piece of evidence here that the recovery is structurally right
and not merely projection-equal. §4.2's relative route then adds **55** vibrato emits
on the oracle side and none on ours, which is that deficiency's gap in miniature.

The filter row is §4.3's masked route arriving: 448 → 639 on these four, all of it
`$18` read as two fields. Over all 71 GT tunes the oracle reaches **40.0%** (36.9%
before that route) and the recovery 42.2%; the recovery is still ahead because the
oracle is limited by decompiler fidelity on the other 60 tunes, not because it
explains less.

The trigger domain is **0 generated of 57531 fires** for the oracle and 0 of
49660 for the recovery. Mapping a real editor's song does not lift one trigger
off the floor, and §4.1 says why.

## 4. The deficiency report

Every wall actually hit, with its weight. A wall **both** editors hit is a real
gap in the primitive; one only a single editor hits is something to normalize
away instead, and none of the entries below is of that kind. **§4.3 is closed**: the
masked route shipped and the numbers below are before-and-after, which is what this
layer is for — a deficiency measured against two editors' own songs, answered in the
universal primitive, and re-measured on the same tunes.

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

### 4.2 A transfer's emit cannot be relative to another's — **closed**

| the strict graph's RAW | measured | after the relative route | tunes |
|---|---|---|---|
| GoatTracker vibrato: `freq ± speedtable.right[row]` | 12744 | **4307** interpreted | 28/71 |
| … of the rest: `freq_hi` moving on carry — a 16-bit accumulator (§4.4) | — | 6284 | — |
| … of the rest: a relative cell the composer's table no longer predicts | — | 1977 | — |
| … of the rest: the editor's bit-7 escape, a shift count and not a step | — | 176 | — |
| SID-Wizard detune: the pitch lane plus the WF program's detune column | 38840 | **343** interpreted, 612 refused | 4/64 |
| GoatTracker portamento carry (16-bit, §4.4) | 500 | 500 (unchanged) | 9/71 |

Vibrato is a *bipolar offset applied to the value the plane already holds*: neither
`LOOKUP` (the values depend on the base note) nor `RAMP` (the direction reverses
at a bound). The same shape covers the arpeggio (`wavetable` relative-note column;
SID-Wizard's `default_chord` + `chord_table`), the orderlist `Transpose`, and
SID-Wizard's `octave_shift` and `detune` — every one of which is "a table whose
emit is combined with another generator's".

**The extension shipped**: `route: Rel(reg, mask, op, base)` (docs/tracker.md §2, §4f).
A generator supplies a **delta**, the route names the operation, and the base is one of
three **named** things — the plane's own previous emit (`Prev`), another generator's
current value (`Node(i)`), or a declared byte (`Const(c)`). Both spellings map onto it
with nothing editor-specific added:

- GoatTracker's `_vibrato` is `chan.freq ± speedtable.right[idx-1]`: `Prev` plus a
  declared delta, exact on the low byte, and the high byte moves only on carry — §4.4's
  limit, not this one. Where `speedtable.left` has bit 7 set the editor *computes* the
  step from the current note's interval, so the declared byte is a shift count rather
  than a step and those 176 emits are refused.
- SID-Wizard's `WRPITCH` is an 8-bit `ADC` of the WF program's detune column onto the
  pitch lane: `Node(i)`, whose base is a real generator over `NOTE_FREQ_LO`. The route
  **consumes** the base generator's value instead of letting it write, so the pair is
  still one emit at one position — the same discipline §4.3's masked group follows.

| | admitted interpreted | strict `RAW` | of it, §4.2's shape |
|---|---|---|---|
| GoatTracker, 71 tunes | 112988 → **113585** of 282516 | 126554 → **122247** | 12744 → 8437 |
| SID-Wizard, 64 modules | 105691 → **106034** of 228702 | 123011 → **122668** | 38840 → 38497 |

**4307 GoatTracker emits and 343 SID-Wizard emits leave `RAW`**, every one a byte of the
composer's own table at a row the editor's own player reached, combined with a base that
player's own state supplies. The admitted law still passes 71/71 and 64/64, the strict
graph still reproduces the whole window on 4 GT tunes and 64/64 SID-Wizard modules, and
**no tune regresses on either editor** (14 of 71 and 4 of 64 improve). The evidence class
is `rel`, counted apart from `lane`/`gate`/`mask` and from `imm`, because a relative emit
is part declared byte and part live value.

Our own recovery closes **316** emits of it, on 6 tunes of 649, and docs/tracker.md §6
measures why: the base has to come out of the program text, and a 6502 driver that adds
one RAM-staged byte to another names none. That gap between two editors' own models
(4650 emits) and what a decompiled driver yields (316) is the honest reading of this
deficiency, and it is §4.3's reading again: the *format* was the wall for the editors,
*provenance* is the wall for the recovery.

Two halves of the old entry stay open and are not this route's. The **note-index**
domain — `chord_table`, `octave_shift`, the orderlist `Transpose` — shifts the row a
pitch `SELECT` reads rather than the byte it emits, and it is no longer free: now that
the arrangement supplies that row (§3.2), a declared shift between the pattern's column
and the pitch table's index refuses the emit outright, at **6953** emits over three
editors. That is **§4.5**, measured. And the **triangle `RAMP`** bound is a transfer,
not a route (docs/dm-oracle.md §4.2 is where a tune first reaches it).

### 4.3 One register plane, two generators — **closed**

| the strict graph's RAW | measured | after the masked route | tunes |
|---|---|---|---|
| GoatTracker `$18` = filter mode `|` master volume | 14117 | **4122** + 171 | 71/71 |
| SID-Wizard `$18` = mode `|` volume | 12800 | **4556** + 138 | 64/64 |
| SID-Wizard `$17` = resonance `|` per-voice routing | 4714 (+8086 held) | **4556** + 7 | 64/64 |
| our own recovery, `$18` "a declared mode nibble combined with a volume level is not a declared byte" (docs/tracker.md §6) | 16597 | 15921 | — |

`$18` is a mode nibble ORed with a volume level, and `$17` a resonance nibble
ORed with a routing mask assembled from three voices' flags. Both are two
independent generators writing one plane, and the primitive routed one generator
to one plane. The ctrl plane is the same shape and was only survivable because the
gate has three states, so the lane can be **multiplied out** into gate images
(`_ctrl_table`, and `tracker._key_table` before it); with two varying nibbles the
product table is 256 rows of nothing.

**The extension shipped**: `route: Plane(reg, mask)` (docs/tracker.md §2, §4e). A
generator supplies only the bits its mask names, a register may be driven by several
whose masks are disjoint — `tracker._check` refuses any overlap — and the last of them
to fire writes the assembled byte, so a masked group is still one emit at one
position. What it returns here is the closed loop:

| | admitted interpreted | strict `RAW` | of it, §4.3's shape |
|---|---|---|---|
| GoatTracker, 71 tunes | 104368 → **112988** of 282516 (36.9% → **40.0%**) | 136378 → **126554** | 14117 → **4293** |
| SID-Wizard, 64 modules | 89348 → **105691** of 228702 (39.1% → **46.2%**) | 139354 → **123011** | 25600 → **9257** |

**9824 GoatTracker emits and 16343 SID-Wizard emits leave `RAW`**, every one of them
a byte of the composer's own table at a row the editor's own player reached, and
every one under the same law: the admitted graph still passes 71/71 and 64/64, the
strict graph still reproduces the whole window on 4 GT tunes and 64/64 SID-Wizard
modules, and **no tune regresses on either editor** (51 of 71 and 43 of 64 improve).
SID-Wizard's filter plane goes 0/51200 to **16343/51200**; GoatTracker's 11470/42157
to **20090/42157**.

What is left is named rather than folded away. **13234 emits are the driver's ghost**
(§4.6): the song never runs a filter program, so the mode nibble is the editor's
power-on 0 and the volume its power-on `$0F`, and a field no song datum reaches is
refused — a masked group is admitted only where at least one field is a real table
row. **316 emits** (171 GT, 145 SW) do form a group whose fields the composer's tables
no longer reproduce, and stay `RAW` for the reason every other byte does. The
evidence class is `mask`, counted apart from `lane`/`gate` and from `imm`, because a
masked write is part declared byte at a row and part editor constant.

Our own recovery closes almost none of its 16597 — **676**, on 5 tunes — and
docs/tracker.md §6 measures why: the mask has to come out of the program text, and a
6502 driver that ORs two RAM-staged bytes names none. That gap between two editors'
own models (26167 emits) and what a decompiled driver yields (676) is the honest
reading of this deficiency: the *format* was the wall for the editors, and
*provenance* is the wall for the recovery.

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

### 4.5 An index route is absolute, and every arrangement composes two indices

The index route shipped (docs/tracker.md §2: `indexer(transfer, trigger)`, and a
`SELECT` whose rows are `("node", j)`), and §3.2 is what it bought. What it cannot
carry is a row that is **one index plus another**, and both halves of every editor's
arrangement are exactly that.

**The transpose, priced.** A transpose shifts the *index* the pitch table is read at,
not the byte it emits, so §4.2's relative route — which is in the value domain — does
not reach it. Every emit under a nonzero declared shift is refused rather than fitted:

| the shift the editor declares | emits refused | tunes/modules | share of that editor's freq plane |
|---|---|---|---|
| GoatTracker orderlist `Transpose` | **1690** | 5 of 71 | 1690/83488 = **2.02%** |
| SID-Wizard `Transpose` + instrument `octave_shift` | **4738** | 15 of 64 | 4738/67755 = **6.99%** |
| DefMON `TR` with bit 7 clear (docs/dm-oracle.md §4.2) | **525** | 3 of 6 | 525/7200 = **7.29%** |
| **three editors** | **6953** | **23 of 141** | — |

On the strict-full four the same refusal is **386** emits on one tune. Every one is a
byte of the composer's own pattern at a row the editor's own player walked, with one
declared byte between it and the pitch table's row.

**The orderlist, for the same reason.** A pattern's absolute row is
`base(the orderlist entry) + the row counter`. `("node", j)` names **one** source, so
the orderlist cannot be a generator of its own: what ships instead is one chain per
(voice, pattern), whose row counter's seed *is* the entry's base row, and an orderlist
entry counts as represented where its pattern's chain exists and fires (§3.2, 213 of
6579 for GoatTracker). The entry is in the graph as a shared subgraph and its **arrival**
is a trigger, not a generated index.

**The general extension**: `Rel` in the **index** domain — a row that is a declared
delta over a named base index, the same three bases §4f already names. One object
closes both halves, on three independent editors: the orderlist becomes
`base + counter` and the note becomes `pattern column + transpose`. It is not this
route with a wider target, and it is not §4f with a different register: §4f combines
two *values* into a byte, and this combines two *rows* into an index. The arpeggio is
the third use — GoatTracker's wavetable relative-note column costs **10142** emits
here, SID-Wizard's `chord_table` **2621** and DefMON's **1141**, all counted as
`arpeggio` and all the same shape.

### 4.6 What is not a deficiency, and is reported anyway

**The driver's ghost.** 97152 GoatTracker emits (77% of the strict graph's `RAW`)
and 75255 SID-Wizard emits (61%) — `ghost:pw` alone is 39440 on 58 GT tunes — are
a register the song **never programs**, written every frame from a ghost register
holding its power-on value. §4.3's closure grew that share rather than shrinking it,
and added 13234 of its own: a `$18` whose mode nibble no filter program ever sets is
two power-on defaults ORed, which is this category exactly.
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

### 4.7 How much of the mapped graph is `RAW`

Interpreted share is the admitted graph's; the `RAW` breakdown is the strict
graph's own residual, whose denominator is that graph's write count.

The arrangement (§3.2, §4.5) moves **nothing** in this table and is not in it: it is the
index domain, so it changes where a `SELECT`'s row comes from and not which emits are
interpreted. Interpreted counts, per-plane splits and every evidence class are
byte-identical before and after it, on all three editors — which is the check that it
added structure and not coverage.

| | admitted interpreted | strict `RAW` | of it: the primitive cannot express (§4.2, §4.4) | of it: driver ghost (§4.6, §4.3) | rest |
|---|---|---|---|---|---|
| GoatTracker, 71 tunes | 112988/282516 = 40.0% | 126554 | **20148 = 15.9%** | 97152 = 76.8% | 9254 |
| GoatTracker, strict-full 4 | 9926/17117 = 58.0% | 7191 | **1041 = 14.5%** | 5954 = 82.8% | 196 |
| SID-Wizard, 64 modules | 105691/228702 = 46.2% | 123011 | **46482 = 37.8%** | 75255 = 61.2% | 1274 |

§4.3's closure and then §4.2's are the whole movement in that table: what the primitive
cannot express falls 34265 → 20148 → **15841** and 63996 → 46482 → **46139**, and on the
strict-full four 1041 → **986**; the emits it stops accounting for are now either
interpreted or the ghost. The 316 masked groups and the 1977 relative cells whose bytes
the composer's tables no longer reproduce sit in `rest`.

No `RAW` in either graph is padding: the strict graph's residual carries what the
*native model predicts*, so a `RAW` write is still a claim about the song and the
law still tests it. Evidence classes are `tracker`'s own and are never summed:
GoatTracker `lane` 95306 / `gate` 3166 / `mask` 8620 / `rel` 597 / `ramp` 4620 / `seed`
1276 / `imm` 0; SID-Wizard `lane` 72581 / `gate` 7212 / `mask` 16343 / `rel` 343 / `ramp`
8372 / `seed` 1183 / `imm` 0. **No emit in either oracle rests on the shallow `imm`
class** — every
interpreted byte is a byte of the composer's table at a row the editor's own player
reached, and a `mask` emit is one such byte per field, with the editor's own default
volume as a one-entry table where the song never sets it (the `hr` lane's precedent).

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
- SID-Wizard's filter plane is mapped for `$17`/`$18` only (16343/51200): the two
  settings registers come off the filter program's set row through masked routes
  (§4.3), while the **cutoff** walk at `$15`/`$16` still names no row — it is owned by
  whichever voice holds `filter_controller_voice`, and the mapping does not follow the
  sweep. That is a mapping gap, not a format one.
- A masked group is admitted only where at least one field is a real table row, so a
  register the song never programs stays `RAW` even though its fields are known
  constants: 13234 emits, counted with the driver ghost (§4.6). The set row itself is
  **held** across the frames it still stands for — the editor's own pointer, not a
  search — and a held row whose byte no longer agrees is refused by the same
  `mem0[src] == val` pair every other emit passes.
- Row provenance is taken from the editor's own player — from an instrumented
  subclass for GoatTracker, from the public pointer state for SID-Wizard — and
  every emit is re-checked against the table byte, so a mis-named row costs
  coverage and can never cost correctness.
- The arrangement is measured over the 200-frame window, so its denominators are
  three: the whole song (2407 GoatTracker patterns), the part the editor's own player
  **walked** in the window (252), and the part the graph **represents** (185). Only the
  middle one is reachable at all in 200 frames, and all three are reported (§3.2).
- A pattern chain is built only where the walk has one emit per frame: a frame that
  writes a register twice would be two readers of one index value, and is refused
  rather than ordered. It costs nothing on these three editors and is counted.
- The instrument index link needs the pattern row whose instrument column set the
  voice's current instrument, held across the rows that leave it empty. Where no row
  has set it yet — the driver's own power-on instrument — there is no pattern row to
  name: 11826 GoatTracker emits and 3165 SID-Wizard emits, counted as
  `no_instrument_row` and left with an observed row.
