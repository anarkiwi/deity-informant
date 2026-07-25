# Musical-structure recovery — study

A NEW layer above the decompiler: recover tracker-shaped musical structure
(instruments, patterns, order lists, tempo) from a playroutine. It deliberately
weakens the byte-exact contract to **per-frame semantics** so it can simplify
aggressively; operation cost is irrelevant at this layer. This document
formalises the frame semantics and verification law, designs the recovery
pipeline, proposes the song-text format, and records the prototype experiments
(tools/study/, all numbers below measured on real HVSC tunes, full
Songlengths length unless noted). Study only — nothing here is production.

## 1. Canonical frame semantics (normative)

A **frame** is one play-boundary invocation (one `proc play` call of the
decompiled program; for v2/P-INT, one driver tick — §5). The canonical frame
log is derived mechanically from the walker's cycle-stamped `(cycle, reg,
value)` log: split at play-call boundaries, drop cycles, reorder within the
frame as:

- per voice v1, v2, v3: `freq_lo`, `freq_hi`, `pw_lo`, `pw_hi` — each the
  **last** value written that frame (last-write-wins collapse), present only
  if written;
- then that voice's `ctrl`/`attack_decay`/`sustain_release` writes with their
  **original relative order preserved in full** (order carries gating and
  hard-restart semantics);
- then `filter.cutoff_lo`, `cutoff_hi`, `resonance` ($D417), `mode_vol`
  ($D418) — last-write-wins, present only if written.

**Elision decision:** registers unwritten in a frame are elided, not carried.
The frame-domain player owns register state (init prologue seeds it), so
carrying would only duplicate state the verifier already tracks; elision keeps
the canonical log minimal and makes "player rewrote the same value" (an
idiom fingerprint) distinguishable from "no write" where the strict tier
needs it.

**Why last-write-wins is sound for freq/pw/filter:** these registers are level
latches; a mid-frame transient value is audible only for the sub-frame window
until the next write. Measured collapse (full length):

| tune | frames | raw writes | canonical | collapsed | value-changing | lossy frames |
|---|---|---|---|---|---|---|
| Commando (Hubbard) | 11750 | 132,629 | 117,031 | 15,598 | 12,367 | 7,849 |
| Aces_High (Cadaver) | 13850 | 161,348 | 161,348 | 0 | 0 | 0 |
| Consultant (Cadaver) | 8800 | 110,851 | 110,851 | 0 | 0 | 0 |
| Rambo (Galway, 3000-frame probe) | 3000 | 19,553 | 17,993 | 1,560 | — | 355 |
| Ghouls_n_Ghosts (Follin, 3000-frame probe) | 3000 | 19,186 | 18,416 | 770 | — | 378 |

Every one of Commando's 12,367 value-changing collapses is a frequency
register (per-reg counts: v2 freq_lo 6,860, v1 freq_hi 1,925, v3 freq_hi
1,914, v1 freq_lo 684, v2 freq_hi 888, v3 freq_lo 96 — zero pulse, zero
filter): Hubbard computes the note frequency, then his effect/drum path
overwrites it in the same frame. The final value is the one the frame hears.

**Why ctrl/AD/SR keep their order:** within-frame same-register double edges
are real and player-identifying (full length, value-changing writes to the
same register twice in one frame):

| tune | double edges | dominant ctrl sequence |
|---|---|---|
| Consultant | 854 (ctrl) | `08 -> 09` (GoatTracker test-bit hard restart) |
| Bionic_Commando | 183 (ctrl) | `15 -> 14` (gate on+off same frame), `10 -> 81`, `14 -> 81` |
| Wizball | 3 (ctrl) | `08 -> 41` |
| Aces_High | 6 (SR) | — |
| Commando, Monty_on_the_Run | 0 | — |

A state-only view would erase Consultant's 854 note restarts and Follin's
one-frame gate blips entirely.

## 2. Verification law (Gate F)

Two tiers, both mechanical:

- **Gate F-strict (diagnostic):** the recovered song rendered by the reference
  frame-domain player reproduces the canonical frame log of §1 verbatim,
  full-length. This is the exact analogue of the byte-exact gate one level up,
  but it forces the recovery to reproduce write *habits* (idempotent rewrites
  of unchanged values — e.g. Consultant rewrites v1 freq every frame for
  hundreds of frames while the value never changes).
- **Gate F (normative — owner-decided):** per frame, (a) the end-of-frame
  25-register state is equal, and (b) per voice, the deduplicated **edge
  sequence** — the subsequence of ctrl/AD/SR writes whose value differs from
  that register's current value — is equal. Rationale: a same-value rewrite
  of any SID register, including ctrl (no gate/test edge) and AD/SR (no
  envelope event), is hardware-inert; state + edges is precisely the
  information content of the frame semantics.

Prototype verifier (`tools/study/verify.py`): the **L0 event list** (per
frame: register-state delta + edge sequences) renders back through the
reference player and passes Gate F **full-length on all three study tunes**
(Commando 11,750 frames, Aces_High 13,850, Consultant 8,800; first-mismatch
= none). L0 sizes: Commando 109,789 events vs 132,629 raw writes, Consultant
68,794 vs 110,851 — the event tier already deduplicates player idiom.

## 3. Recovery pipeline

### 3.1 Log-driven (works alone on gate-driven players)

From the canonical log: state grid -> per-voice events (gate-edge note-on/off,
gated freq changes, wave changes) -> tempo -> row grid -> tokens -> repetition.
Measured:

| | Commando | Aces_High | Consultant |
|---|---|---|---|
| note-ons v1/v2/v3 | 1045/1087/1048 | 1401/844/661 | **1/16/3** |
| onsets on row grid | 100% (0 off-grid) | 96.1% at speed 2 | n/a |
| speed (frames/row) | 6 (deltas 6:1194, 12:305, 18:32) | ambiguous (6:822, 12:508, then 8/16/18/4…) | n/a |
| note-on freq exact in tune's own table | 99.8% / 89.9% / 99.8% | 100% / 100% / 100% | — |
| phrase cover (repeats >= 8 rows) | 69% / 89% / 92% | 95-97% | 98% |
| 16-row blocking, blocks:distinct | 122:47 / 122:22 / 122:11 | 432:80…108 | 55:2…9 |

- **Note inversion must use the tune's own table.** The generic locator
  (`tools/study/notes.py:locate_freq_table` — candidate bases from `LDA
  abs,X`/`abs,Y` operands, three layouts, 16-bit octave-ramp validation)
  found: Commando **interleaved word table at $5428** (= the sidprog text's
  `m_5428`/`m_5429`), Aces_High split hi/lo at $1340, Consultant interleaved
  at $1265. Cadaver's tables match the derived equal-tempered PAL table
  (freq = f_hz * 2^24 / 985248) in **0/96 entries**; Hubbard's in 37/96.
  Nearest-ET + cent detune is the fallback and the naming scheme only.
  Commando v2's 10% non-table onsets are drum-table frequencies — instrument
  material, not notes.
- **Where log-driven fails:** (a) Consultant holds gates for minutes — the
  gate-edge note model finds 1-16 "notes"; the real triggers are the 854
  `08 -> 09` test-bit edge sequences, plus tie-notes with no ctrl activity at
  all — freq jumps to table-exact values, indistinguishable from arpeggio
  chords without the pattern data; (b) Aces_High's onset deltas mix 6/12 with
  4/5/7/8/16/18 — GoatTracker tempo commands/funktempo defeat a single global
  speed; the row grid must be piecewise (tempo change events as first-class
  rows).

### 3.2 Program-driven (the decompiled program names the answer)

The structured program + post-init image already contain the player's own
music data model. Two mechanical probes on Commando
(`tools/study/hybrid_probe.py`, `run_study.staircase_cells`):

- Of the 59 non-SID cells written during play, exactly three are **staircase
  cells** (monotone +1 steps, changing only on row boundaries): $54EC, $54ED,
  $54EE — the per-voice **order-list position counters** (63/62/122 steps).
  The detector found them with zero tune-specific knowledge.
- Their step frames give ground-truth pattern boundaries: Commando v1
  patterns are variable-length — 30, 32, 32, 32, 129, 128, 61, 66, 64, 16 …
  rows. Fixed-16 blocking (log-driven) is exactly right for v3 (counter
  stride 96 frames = 16 rows; 122 blocks:11 distinct vs ground truth 122
  steps) and wrong for v1 (47 "distinct 16-row blocks" vs 30 real patterns).

Segmenting the v1 row stream at the $54EC boundaries yields the real song
structure (§4 excerpt). The pointer pairs feeding pattern data ($5889 area)
are recoverable the same way (paired lo/hi cells whose 16-bit value jumps at
the same boundaries).

### 3.3 Ownership (the hybrid design)

| concern | owner | reason (measured) |
|---|---|---|
| canonical frame log, Gate F | log layer | mechanical from the walker; total |
| note-freq table, note identity | program layer, log fallback | 0/96 ET match on Cadaver; table located statically in all 3 tunes |
| tempo / row grid | log layer seeds, program layer decides | global speed fails on Aces_High; the player's row counter cell is exact |
| pattern boundaries, order list | program layer | staircase counters are exact; log-driven blocking wrong on variable-length patterns |
| row contents (notes, gate, instrument) | log layer | ground truth by definition; program data formats are per-engine |
| instrument frame-programs (wavetable/pulse/filter runs) | log layer clusters, program layer names | per-note trajectories are in the log; tables give identity |
| verification | log layer | Gate F needs no engine knowledge |

The program layer is an **oracle for segmentation and naming**; the log layer
is the **semantic ground truth and the gate**. Engines where the program side
degrades (packed/exotic data formats, Follin's code-as-data) still recover at
L1/L0 because the log side never depends on engine knowledge.

## 4. Output format: the `sidsong` text

Tracker-shaped, same spirit and discipline as sidprog: canonical, parseable,
versioned, one law. Tiered so totality never depends on recovery quality:

- **L0 event list** (always available, §2): per-frame register deltas + edge
  sequences. Verified on all three tunes.
- **L1 voice streams:** per-voice note/gate/ornament events on the row grid,
  no pattern factoring.
- **L2 song:** instruments + patterns + order lists + tempo; residual
  channel-local L0 overlays for whatever the musical model cannot carry
  (digi frames, raster tricks), so L2 is never blocked on 100% coverage.

Owner decision (post-study): **no note normalization at this stage** — raw
register values are the content; a tune-table index annotation is allowed
only where table-exact and only as a comment. The note-named sketch below
predates that decision and is kept for shape; the emitted prototype texts of
§9 are the normative example outputs.

Sketch (numbers from the Commando prototype run; grammar to be specified in
the implementation phase):

```
sidsong 1
tune "Commando" clock pal frame-rate 50
speed 6                     ; frames per row; per-pattern override allowed
freqtable $5428 interleaved 96   ; provenance: program-layer, m_5428/m_5429

instrument i00 wave 4 adsr 295F   ; + frame-program: wavetable/pulse rows
instrument i01 wave 1 adsr 0DFB
instrument i02 wave 8 adsr 0A09

voice v1 order 00 00 00 01 02 03 04 05 06 07 07 07 08 09 10 10 11 12 13 13
  13 13 13 13 13 14 07 07 07 15 13 13 13 14 07 15 13 16 17 18 18 18 18 19
  20 20 20 21 22 22 22 23 24 13 25 26 07 15 27 24 28 12 29

pattern 01 rows 129 {       ; v1 slot 3; recovered rows 0-19 shown
  00 A-6 i01 | 01 --- | 02 D-4 i02 | 03 A-4 i00 | 04 A-4 i00 | 05 off
  06 A-4 i00 | 07 off | 08 A-4 i00 | 09 off | 10 A-4 i00 | 11 --- | 12 ---
  13 off | 14 A-4 i00 | 15 --- | 16 off | 17 A-4 i00 | 18 off | 19 E-5 i00
}
```

Real recovered v1 structure: **63 order slots over 30 distinct patterns**,
pattern lengths 30/32/129/128/61/66/64/16… rows, all 1,045 note-ons exactly
on the 6-frame grid, 99.8% of onset frequencies exact entries of the tune's
own table.

**Laws:** `dumps(loads(dumps(s))) == dumps(s)` (canonical fixpoint, as
sidprog); render-through-reference-player passes Gate F full-length; L2
lowers to L1 lowers to L0 with Gate F preserved at every tier. The reference
frame player is deliberately trivial (a few hundred lines: order/pattern
walker + instrument frame-programs + L0 overlay replay) — it is the spec of
the format, not an engine emulator.

## 5. Risk register

| idiom | detection (mechanical) | policy |
|---|---|---|
| mid-frame multi-writes beyond ctrl/AD/SR | collapse counters (§1: 12,367 on Commando, all freq) | accept by law: last-write-wins is the definition of frame semantics; counters reported per tune |
| digi via $D418 volume pumping | `d418_multiwrite_frames > 0` (0 on all probes — volume digi lives mostly in the v2/P-INT class) | mark affected frame ranges frame-lossy; ship L2/L1 with an explicit `digi` overlay region at L0 granularity; never silently average |
| raster-position-dependent writes (values derived from $D011/$D012 reads) | walker knows volatile reads; taint frames whose written values depend on them | values are still exact in the frame log (the walker replays them); the *recovered* song carries them as literal L0 overlay — no musical claim |
| multi-speed / v2 P-INT drivers | driver cadence in the sidprog header | frame := driver tick; tempo expressed in ticks; NTSC/PAL rate from header. Not in the v1 study class |
| gate-held / legato players (Consultant) | note-ons << freq-change events | note triggers from edge sequences (test-bit, restart patterns); tie-notes need program-layer pattern data; else degrade voice to L1 |
| tempo commands / funktempo (Aces_High) | onset-delta residue at best global speed (3.9%) | piecewise tempo tracking; tempo-change rows first-class in L2; program-layer row counter preferred |
| arpeggio/chord tables vs real notes | per-frame table-exact freq jumps at frame (not row) rate | classify as ornament (instrument frame-program), never pattern rows; threshold = row grid |
| variable-length patterns (Hubbard) | program-layer staircase boundaries vs fixed blocking mismatch (47 vs 30 on v1) | pattern length is per-pattern data, never a global; log-only fallback uses phrase segmentation, flagged lower-confidence |
| engines with no recoverable tables (code-as-data, packed) | table locator / staircase scan return nothing | L1/L0 tiers, diagnostics naming what was not found; Gate F still green |

## 6. Implementation plan (staged, gated)

- **M1 — canonical frame log + Gate F verifier + L0.** Derive frame logs from
  the decompiler's walker (play-call boundaries; not a new VM run). Emit +
  parse + render L0; Gate F and Gate F-strict both implemented, strict
  reported. Corpus target: **140/140** tunes L0-green full-length (it is
  mechanical; any failure is a bug). This is the frozen oracle of this layer.
- **M2 — notes and events.** Table locator (three layouts) + ET fallback with
  cent detune; per-voice event streams; per-tune classification report
  (gate-driven / legato / restart idiom; loss counters of §1). Gate: table
  found or ET-fallback declared, per tune; event streams render Gate F-green
  as L1. Target: >= 120/140 tunes with located tables; 100% L1-green.
- **M3 — rows, patterns, order lists (log-driven).** Tempo (incl. piecewise),
  row quantisation, phrase/blocking segmentation, instrument fingerprint
  clustering; `sidsong` v1 grammar + reference player; L2 with residual
  overlays. Gate: L2 Gate F-green full-length; report % rows carried
  musically vs overlay. Early-value target: Hubbard + GoatTracker families
  (measured here: 69-97% phrase cover).
- **M4 — program-layer binding.** Staircase/sawtooth sequencer-state
  detection generalised (GT row counters reset per pattern — extend the
  Commando-proven monotone detector); freq/pattern/instrument table naming
  joined to sidprog `m_XXXX` cells; segmentation switched to program-layer
  boundaries where found, with cross-layer consistency check (boundaries must
  be a subset of log-quiet rows). Gate: on tunes with found counters,
  order/pattern structure matches the player's own (Commando: 63 slots, v3
  16-row stride — already demonstrated).
- **M5 — corpus hardening.** Per-tune structure report (patterns, cover,
  overlay fraction, loss counters) recorded like the decompiler's proof
  reports; risk-register detectors wired as diagnostics; synthetic frame-log
  fixtures so CI needs no HVSC (same doctrine as the decompiler).

## 7. Open questions (owner)

1. ~~Gate F vs Gate F-strict~~ — decided: Gate F (state + edge sequences) is
   the normative law; strict write-log identity is a diagnostic.
2. Frame-log provenance: derive from the decompiled walker's log (M1 plan) or
   re-trace with the evidence VM? Walker derivation keeps this layer
   downstream of Gate C with zero new soundness surface.
3. Should `sidsong` carry provenance links into the sidprog text (cell names
   for tables/counters) normatively, or as comments?
4. ~~Note-name convention~~ — decided: raw register values are the content;
   table-index annotation only where table-exact, only as a comment.
5. Is the v1 study class (per-frame `play`) the right first target for M1-M4,
   deferring all multi-speed/digi to the v2 layer as assumed?

## 8. Prototype inventory (tools/study/, experimental)

| file | purpose | runtime (full length) |
|---|---|---|
| `framelog.py` | frame-sliced concrete trace, canonicalizer, loss stats, state grid | 2-6 s/tune |
| `verify.py` | Gate F reference/verifier, L0 event tier + reference render | < 1 s |
| `notes.py` | ET inversion + generic freq-table locator (3 layouts) | < 1 s |
| `recover.py` | events, speed inference, rows, phrase cover, blocking | < 1 s |
| `hybrid_probe.py` | Commando sequencer-state trajectories | ~ 12 s |
| `edge_scan.py` | within-frame same-register double-edge census | ~ 30 s / 6 tunes |
| `run_study.py` | driver: all numbers above + pattern excerpt (`study_report.json`) | ~ 60 s / 3 tunes |
| `sidsong_proto.py` | §9 tier codecs: L0/L1/L2 emit + parse + expand-to-events | < 1 s |
| `tiers.py` | §9 driver: full-length tier texts to `out/study/`, Gate F per tier, `tiers.json` | ~ 15 s / tune |
| `dedup_levels.py` | §9 pattern-dedup vs abstraction level (A/B/C/T) | ~ 15 s |
| `ghouls_probe.py` | Ghouls_n_Ghosts characterization (loss, edges, onsets, staircase) | ~ 15 s |

## 9. Addendum — tier prototypes, full length (owner decisions applied)

Emitted, parsed and Gate-F-verified `sidsong-proto` texts (raw register
values, no note normalization) for Commando and the corpus's densest player,
Ghouls_n_Ghosts (Follin, default subtune 4:19 = 12,950 frames). Outputs in
`out/study/` (HVSC-derived, gitignored); every tier that exists passes Gate F
full-length **from the parsed text** through the reference frame player.

### 9.1 Does it simplify — Commando (sidprog text: 26,168 B)

| representation | elements | text bytes | Gate F |
|---|---|---|---|
| raw play-phase writes | 132,629 writes | — | — |
| canonical frame-log dump | 117,031 writes | 1,239,090 | — |
| L0 event list | 109,789 events | 579,186 | pass |
| L1 voice streams (delta-runs) | 45,064 items | 512,049 | pass |
| L2 raw-stream patterns + orders | 220 patterns / 250 slots | 444,068 | pass |

L2 recovered structure: order slots 64/63/123 per voice (counters
$54EC/ED/EE), but raw-stream dedup keeps 55/42/123 distinct patterns — **the
representation stops shrinking at L1 -> L2** (13% bytes), and no flat tier
approaches the 26 KB sidprog text: for a compact player, the program + its
data IS the generative model. `dedup_levels.py` isolates why (distinct
patterns per voice at increasing abstraction):

| content level | v1 (64 slots) | v2 (63) | v3 (123) |
|---|---|---|---|
| A: bitwise L1 slices | 55 | 42 | 123 |
| B: rhythm/gating skeleton (freq dropped) | 27 | 11 | 8 |
| C: rows model (freq only at gate-on) | 31 | 26 | 30 |
| T: C with table-index transpose-relative onsets | 31 | 24 | 30 |

Bitwise reuse fails because per-frame ornament trajectories are
context-dependent (Hubbard drum curves start from state left by the previous
note: two token-identical v3 patterns first differ as `f=0751` vs `f=07C1`
classes; also the counter step frames jitter 90/96/102 — fetch-ahead — which
splits identical patterns by length). Level C is the honest tracker-shaped
factorization (123 -> 30 on v3); level B shows the generative headroom (8
rhythm skeletons) reachable only by moving per-frame curves into shared
instrument frame-programs. Transposition alone adds almost nothing.

Excerpt — `out/study/Commando.l2.txt`, pattern 1.01 (v1, 192 frames; `+n` =
frame gap, `f`=freq16, `p`=pulse16, `e`=ordered ctrl/AD/SR edges):

```
pattern 1.01 frames 192 {
+0 f=7518
+0 e c=15 a=0D s=FB
+1 f=EA30
+0 e c=80
+1 f=7518
+1 f=EA30
+0 e c=14
+1 f=7518
+1 f=EA30
+1 f=7518
+1 f=EA30
+1 f=7518
+1 f=EA30
+0 e a=00 s=00
+1 f=7518
+1 f=EA30
+1 f=AF58
+0 e c=15 a=0D s=FB
+1 f=0328
+0 e c=80
+1 f=AF58
+1 f=0328
+0 e c=14
+1 f=AF58
+1 f=0328
+1 f=AF58
+1 f=032A
...
}
```

(the octave-alternating `7518`/`EA30` lead, the `c=15 -> c=80 -> c=14`
noise-drum attack, and the mid-pattern `0328` vs `032A` drum-curve drift that
defeats bitwise dedup are all directly visible).

### 9.2 Does it simplify — Ghouls_n_Ghosts (sidprog text: ~452,000 B)

| representation | elements | text bytes | Gate F |
|---|---|---|---|
| raw play-phase writes | 86,024 writes | — | — |
| canonical frame-log dump | 84,345 writes | 887,553 | — |
| L0 event list | 32,566 events | 216,918 | pass |
| L1 voice streams (delta-runs) | 11,200 items | 115,790 | pass |
| L2 | not reached | — | — |

The inversion of the Commando result: for the corpus's least-structured
*program* (452 KB sidprog, 1,794 gotos), the frame layer simplifies
enormously — L1 is **7.7x smaller than the raw frame-log dump and 3.9x
smaller than the tune's own sidprog text**, because Follin's dense output is
highly ramp-structured (constant-delta vibrato/slide runs collapse: 84,345
writes -> 11,200 items, e.g. `f~+90x4` / `f~-90x3` alternating vibrato).

What broke, measured (`ghouls_probe.py`): no per-frame-driver assumption
broke — the canonicalization and Gate F held unmodified (1,679 collapsed
writes = 2% of raw; 38 double-ctrl frames, sequences `10 -> 11`, `80 -> 81`
wave-then-gate, carried exactly by the edge-sequence clause; zero $D418
multi-writes). What fails is **L2 recovery**, for three measured reasons:
(1) no sequencer staircase cells exist among the play-written cells (the
Follin player's position state is pointer/script-shaped, not a monotone
counter), so there is no program-layer segmentation signal; (2) the onset
grid is non-uniform — deltas mode 5 frames (536 of ~1,300) with 10/20/40
harmonics but a 2/3/4-frame residue — no global speed; (3) v1 is legato (255
gate-ons vs 5,113 gated freq changes), so gate-driven row detection is
ill-posed. Ghouls ships honestly at L1 until sawtooth/pointer sequencer-state
detection (M4) provides boundaries.

Excerpt — `out/study/Ghouls_n_Ghosts.l1.txt`, voice 2 (vibrato as delta-runs,
ordered ctrl-off/ADSR/ctrl-on edges in one frame):

```
+5 f=430F
+5 f=151F
+0 e c=40 a=BF s=0F c=41
+1 f=14C5
+0 p=00A0
+1 f=146B
+1 f~+90x4
+4 f=1579
+1 f~-90x3
+3 f=14C5
+1 f~+90x3
+3 f=1579
+1 f~-90x3
+3 f=14C5
+1 f~+90x3
+3 f=1579
+0 e c=40
+1 f=6701
+0 e a=0A c=41
+1 f=66A7
+1 f=664D
+1 f~+90x4
```

### 9.3 Conclusions

- Dump -> L0 -> L1 shrinks monotonically on both tunes; Gate F holds at every
  tier from parsed text. The tiered design is sound.
- Raw-stream L2 does not simplify: pattern factoring must operate on the rows
  model (level C) with per-frame curves owned by instrument frame-programs
  (level B headroom), exactly the §4 L2 design — now with numbers.
- "Does it simplify" has two regimes: compact players (Commando) are already
  their own best compressed form — the musical layer's value there is
  *legibility and editability*, not bytes; dense players (Ghouls) shrink 3.9x
  below their own program text at L1 already — the value is both.
