# tracker — a universal tracker layer lifted from the song model

A **derived** artifact one layer above the song-synthesis model
(`song_model.py`, docs/song-model.md) and frameprog. It re-projects the
recovered *synthesis machine* (counters, freq/pw/filter drivers, the
control automaton) into tracker vocabulary — **voice lanes of notes** over a
small, tune-independent **engine** of parametric generators. Notes are pitch
*relationships* (equal-tempered semitone indices via the recovered ET table),
not register bytes; note-events **trigger** bounded accumulators and shared
generators; sweeps and transforms bind to a **trigger source** (note-on or a
song clock). Status: implemented (M-T1/T2/T3/T3.5). Pitch recovery is audited
across **121 distinct trackers** (SIDId-identified corpus, `tools/tracker_*`):
static pitch tables detected graph-only for **97/121**, and output-side
decomposition (`pitchind.py`: peel the graph-recovered modulation, induce the
ET lattice from the residual, invert) recovers the note lane for **21** of the
remaining 24 — so a note lane is recoverable for **~118/121 (~98%)**, with 3
documented exceptions (§M-T3.5). Induced lattices agree with the static tables
per-frame where both exist (1.00/0.999 on spot-checks).

This is not the retired log-tier musical layer (deleted 2026-07-25). That was
organised register logs; this starts from *relationships* and its
verification law is designed first (§1), per the owner rule.

## 1. Verification law (Gate T) — designed first

A tracker is a pair `(engine, song)` consumed by ONE universal reference
player. The player is fixed across all tunes; a tune is a parameter vector.

- **Gate T (regenerate the layer below).** `render(engine, song)` produces a
  frame projection; it MUST equal the frameprog canonical projection
  (`framelog.canonical`, docs/frameprog.md §1.1) frame-for-frame over the
  Full-Songlengths window. frameprog already regenerates sidprog's projection
  and sidprog is cycle-exact, so a Gate-T-passing tracker is bit-exact at the
  frame-projection plane. `render` is the only semantics; there is no second
  "player."
- **Codec law.** `parse(emit(t)) ≡ t` (invertible text, as for the
  structurer/frameprog).
- **Coverage law (no silent gaps).** Every written plane in every frame is
  produced either by an *interpreted* generator (§3) or by an explicit
  `residual` byte-program generator. A tune with an opaque region (computed
  pitch base — Krakout) still passes Gate T: its opaque planes render from a
  recorded residual, marked as such. Coverage is reported, never assumed —
  same discipline as the observed-set / commit-phase checker.

Musical *interpretation* (a note is a semitone, a transform is portamento) is
**not** required for Gate T; it is required for the higher **Gate M**
(§9): re-derive the interpreted form and regenerate the raw tracker exactly.
Interpretation that cannot regenerate is a diagnostic, never the artifact.

## 2. Why "universal"

The song-model paper already proved *one vocabulary, per-tune parameters*
across Hubbard/Follin/Daglish: every counter is item-1's step+reload shape;
every freq/pw/cutoff modulator is an accumulator or a table lookup; every
control edge is a guarded gate/waveform/ADSR transition. The tracker makes
that vocabulary the **engine** and demotes each tune to its parameters. The
engine has five primitives and nothing else (§3); a "universal tracker
format" is exactly the closure of this set.

## 3. The engine (five primitives)

Each primitive is a direct re-projection of a `song_model.SongModel` field.
Every generator carries `trigger ∈ {note_on(v), clock(C), frame}` and an
output `plane ∈ {freq(v), pw(v), ctrl(v), ad(v), sr(v), cutoff, res, vol}`.

1. **Clock** ← `Counter` (item 1). `dec`+reload ⇒ a divider emitting ticks;
   `inc` free-running ⇒ an LFO phase. Fields `{reload, mask}`. Scope global
   (tempo divider, LFO) or voice (note-length). The tick edge is a Clock
   whose zero-crossing gates note advance.
2. **Accumulator** ← the `accum` set + item-2/3/6 accumulators, unified.
   `{scope, width:8|16, seed: note_on(src)|const, step: const|table[clock]|
   counter, clock, bounds:[lo,hi] × wrap|clamp|turnaround, plane}`. This one
   primitive is portamento, pitch-bend, the PW triangle, and the filter
   cutoff envelope — the "bounded accumulator" the note triggers.
3. **Table** ← a `data` array. A **pitch Table** additionally carries the ET
   inversion from `et_check` (`reference`, `octaves`): a freq word ⇒ nearest
   index ⇒ **semitone number**. Intervals are index deltas; transposition is a
   constant offset; octave is ±12. This is the relationship grounding.
4. **Lookup** ← item-2 note/arp lookups. `{table, index: note|clock|acc,
   plane}`. Index by a note ⇒ pitched note; by a Clock phase ⇒ **arpeggio /
   vibrato** (a *shared* generator, since the phase Clock is global); by an
   Accumulator ⇒ table-swept modulation.
5. **Planes** — the SID register planes in frameprog projection order; the
   render order is fixed by §1.1 of frameprog, so `render` is deterministic.

Shared vs. voice-local falls out of `scope`+`trigger`: a `note_on(v)`
generator is re-seeded per note from the instrument (voice-local); a
`clock(C)`/`frame` generator with global `C` is shared across voices (the LFO,
the filter envelope, the tempo divider).

## 4. The song

- **lanes** — one per voice, plus a global lane. A voice lane is
  `orders → patterns → rows`, recovered from `streams.py`: the note/pattern
  byte stream and its `pos_*` sequence positions. For pointer-sequencer
  players (Follin) a lane is instead a **command script** (streams.py already
  decodes the 21-op grammar); rows and scripts are two encodings of the same
  lane and both render through §3.
- **note-event (row cell)** — `{ note, inst, dur, fx* }`:
  - `note` = semitone index into the voice's pitch Table (via §3.3), or `===`
    (gate-off), or `...` (hold/continue). A byte with no pitch-table
    provenance renders as `~NNNN` (raw freq word) and is a Gate-M gap, not a
    Gate-T gap.
  - `inst` = instrument id = the note-on instrument selector (Commando
    `idx_54FE[x]` → stride-8 block row).
  - `dur` = ticks the note holds = the note-length reload (Commando
    `streambyte & $1F`; Follin the note-length byte).
  - `fx*` = per-note effect columns, one per enabled transform (§6), decoded
    from the per-note flag byte (Commando `m_5523` bits `$01/$04/$08`).
- **global lane** — song-clock-driven effects: tempo changes, the filter
  envelope, master volume. Rows keyed by the global tick.

## 5. Instruments

An instrument is a named bundle of engine bindings, lifted from the
per-instrument data block the note-on selects. Commando: the stride-8 block
`m_5591[104]` row `8·k` is exactly one instrument —

```
inst k {
  waveform  = m_5593[8k]          ; item 4 ctrl byte
  ad        = m_5594[8k]          ; item 5, static per note
  sr        = m_5595[8k]
  pw.seed   = m_5591[8k], m_5592[8k]
  pw.rate   = m_5597[8k] & $1F    ; idx_5507 → Clock ctr_550D reload
  pw.step   = m_5597[8k] & $E0    ; Accumulator step (triangle)
  flags     = m_5598[8k]          ; m_5523: which transforms run
}
```

`flags` bits select which §6 transforms the instrument's notes carry —
i.e. an instrument *is* a subset of engine generators plus their seeds. The
tracker instrument table is the block, one row per instrument, decoded.

## 6. Effects and transforms (item → column)

Each is a §3 generator with an explicit trigger. Sub-classification of item-2
`slide` into portamento/vibrato/arpeggio is by sign source (song-model item-2
future work), done here because the columns require it.

| column | generator | trigger | Commando cells |
|--------|-----------|---------|----------------|
| `Pxx` portamento | Accumulator, step const `m_5520&$7E` signed by bit0, plane freq | note_on | `m_551D`/`ctr_551A` (16-bit), lines 423–446 |
| `Bxx` pitch-bend down | Accumulator dec, gated by note-length | note_on + gate | `ctr_551A`, `m_5523&$01`, lines 447–472 |
| `Vxx` vibrato | Lookup(pitch, index=phase) triangle `idx_550C` | clock(`ctr_5525`) | reflected `&$07`, lines 329–377 — **shared** |
| `Axx` arpeggio | Lookup(pitch, index=note+phase-toggle) | clock(`ctr_5525`) | `idx_54FB` toggle, `m_5523&$04`, lines 485–497 — **shared** |
| `Oxx` pulse sweep | Accumulator triangle, bounds hi-nibble `$08/$0E` | note_on, clock(`ctr_550D`) | `m_5591/2`, `m_5523&$08`, lines 384–421 |
| global filter | Accumulator dec, plane cutoff, bounds/onset | clock(voice-slot or song) | Ghouls only (§8); Commando: filter off |

Portamento/bend/pulse are voice-local accumulators the **note triggers**
(re-seeded at note-on); vibrato/arp are **shared generators** clocked by the
global LFO phase — the two cases the request names. A filter sweep is the same
Accumulator on the global lane, clocked by a voice slot or the song clock.

## 7. Lifting algorithm

One pass, reusing `song_model.analyze(model)` and `streams(model)`; no new
dataflow:

1. `tempo` ← the `Counter` with `kind=dec` and a reload resolving to a const
   or single global cell ⇒ `frames_per_tick`. Absent (Follin) ⇒ per-voice
   note-length Clocks carry tempo.
2. `clocks` ← remaining `Counter`s: per-voice note-length (dec, reload =
   streambyte); free `inc` ⇒ shared LFO with its consumed masks.
3. pitch `Table` ← `_pitch_tables`; build the semitone inversion.
4. `lanes` ← `streams(model)`: walk the stream by `pos_*`; decode each element
   into `{note = table-index(pitch path), dur = length reload, inst =
   selector, fx = flag-byte bits}`. Pointer-sequencer ⇒ command-script lane.
5. `instruments` ← the per-instrument block rows (§5).
6. `transforms` ← `FreqDriver`s (sub-classified §6) + the PWM accumulator
   (item 3) + the control `Automaton` (items 4–5 ⇒ waveform/gate/ADSR
   bindings) + the filter accumulator (item 6, global lane).
7. Any plane written but not produced by 1–6 ⇒ a `residual` byte-program
   generator over the observed writes (coverage law).

### 7b. Snapshot soundness — tables must not be play-mutated

Every pitch strategy reads `model.mem0`, the **post-init, pre-play** image, so a
table entry is constant data only where the play phase never writes it;
elsewhere the snapshot is not what the running player indexes. `structured`
already enforces this below (it folds a `mem0` read to a constant only when the
cell is outside `model.written`); the tracker now honours the same set.

Candidates are **ranked by sound entries** (`entries - _unsound`), not filtered
on stability: a 96-entry table with one mutated byte beats a clean 36-entry one,
while at equal length the clean reading wins. `_extend_et` still runs past a
*declaration* — a declared size can under-size the physical table, §10 — but
never into a mutated cell.
`Tracker.unstable` names the play-written cells inside the accepted table; a
non-empty value means those notes invert through bytes the player rewrites.

Cells like these are exactly what frameprog's SMC mapping
(docs/frameprog.md §2) resolves to state variables: once the tracker consumes a
frameprog artifact rather than a raw image, the whole class disappears and the
ranking becomes unnecessary.

## 7a. Graph-normalization passes (run before lift)

`song_model`'s constant-base provenance runs on the pass-1 approximation
(`eqlift_annotate._backtrace`: constant-address scratch cells only) and stops
at two driver idioms that the **value+memory e-graph** (`eqlift_mem`) already
has the axioms to see through. The tracker lifter routes through the forwarded
graph; these two rewrites are the prerequisites for note recovery on
shadow/computed-base drivers (Krakout is the witness), and neither is
tune-specific.

**Redundant data-move pass (staging-buffer elimination).** Drivers stage a
frame's register writes in a RAM buffer and bulk-flush it to the SID at frame
end — Krakout's 25-byte shadow `m_E686` flushed to `$D400..$D418`
(`sid.v1.freq_lo[X] = m_E686[X]`), Follin's register-poke command. The flush
`SID[k] = B[idx]` is a copy of a value stored earlier as `B[idx] = v`. The
McCarthy axiom `sel(store(m,a,v,w),a,w) = v` forwards these to `SID[k] = v`,
but only intra-block; the driver separates staging from flush by loops/calls,
so `render_proc`'s conservative memory havoc at those joins leaves the copy
standing (verified: the shadow survives in the committed output,
`out/Krakout.sidprog.txt`). The pass forwards **across the frame**: a buffer
`B` is a *pure staging move* when every play-phase read of `B` is a SID flush
and every reaching def is a plain `B[idx] = v`; rewrite each flush to its
staged value over the frame's memory SSA (indices matched in the e-graph, not
guessed) and drop `B`; an unmatched index stays residual. This is the
memory-plane analogue of `frameproc`'s register→local faint-liveness inlining.

**Wrap-offset base normalization.** A "computed" pitch base is often just a
16-bit-wraparound negative displacement. Krakout reads `mem[idx - $19D7]`,
i.e. `mem[idx + $E629]` (`-$19D7 & $FFFF = $E629`), and the graph *already*
annotates the effective base `@x($E629, idx)`. `_const_base` **sums** SUB
operands (yields `$19D7`, a non-table), so the pitch table is missed; folding
`base - k` as `(base - k) & $FFFF` when it lands on a data span recovers it.
No new information — the base is in the read expression.

After both passes Krakout's freq write reads a constant pitch base indexed by
the note, exactly like Commando: the "computed-pitch boundary" was these two
idioms plus modulation (§8), not an information loss.

## 8. Generality

- **Ghouls (Follin).** No global divider ⇒ tempo per voice (note-length
  Clocks). Jump-table command interpreter ⇒ command-script lanes (§4).
  Inline vibrato ⇒ the same Accumulator primitive. Filter is global from the
  voice-3 slot ⇒ the global-lane filter Accumulator, clocked by that slot.
  *Note values recovered:* the pitch table is Follin-style **separate lo/hi
  byte blocks** (`m_6D35`/`m_6D96`), so `_direct_pitch` accepts only the
  `et_check`-confirmed lo/hi pairing (not the interleaved `lo+1` guess); with
  detune-tolerant inversion the inline vibrato resolves to a note plus a
  coherent detune ramp (Gate T bit-exact). *Command-script lane (landed,
  `follin_script.py`):* the per-voice script is decoded from the graph-found
  zp pointers (`$21/22`, `$23/24`, `$25/26`) with the validated 21-op grammar
  (docs/follin-dispatch-study.md), following call/jump control flow. It is an
  **observed-set** decode — the SMC dispatch is already resolved below (the
  paired-index closure), op boundaries and pointer walks are read from the
  fetch/consume trace, so every consumed byte is **certified** to lie in
  `model.reads`. This also *is* the discriminator I could not get from a
  `command`-count threshold: a tune is command-script-driven iff its zp
  streams decode as a certified non-trivial script — Ghouls yields 3 (v1
  reproduces the study's worked example byte-for-byte), Commando and Krakout
  yield 0. *Remaining:* a script-shaped reference player (tick sim + effect
  engine over the resolved config) to regenerate the frame projection (Gate T
  for the whole lane), and following the top-level continuation past the
  observed window for full-Songlengths coverage.
- **Krakout (Daglish).** The notes are under four driver layers, all
  graph-visible, none an information loss: (1) the **shadow buffer** (§7a
  redundant data-move), (2) the **wrap-offset base** `−$19D7 = $E629` (§7a,
  already annotated `@x($E629, idx)`), (3) an **octave shift** — the `$E536`
  loop emits `pitch >> octave`, so the written freq is `pitch_table[semitone]
  >> octave` — and (4) a **±detune** (observed freqs cluster in `+30` vibrato
  triplets `1021/1051/1081`, …). So the note is `(semitone, octave)` and the
  detune is a §6 vibrato transform; both are peeled before the semitone is
  exact. Until §7a + the octave/detune transforms land, Krakout's freq planes
  render `residual` (Gate T bit-exact, Gate M pending) — a staged milestone,
  not the wall song_model's `other` verdict suggested.
- **Commando (Hubbard).** Fully interpreted; §5–§6 above. Filter unused
  (`$18=$0F` once in sid-init).

## 9. Worked example — Commando (schematic)

Engine (all values are recovered cells, not invented):

```
clock tempo   = dec ctr_5513 reload m_5517          ; frames_per_tick
clock lfo     = inc ctr_5525 mask $07               ; shared phase
clock len[v]  = dec ctr_54F2[v] reload stream&$1F    ; per-voice note length
table pitch   = m_5428[192] stride2 ET ref=$0116 oct=16
```

A voice lane row (columns present only when the instrument's flags enable
them; `note` is a semitone name derived from the pitch-Table index, marked
illustrative):

```
        note inst dur  fx
row 00   C-4  01  08   ....      ; plain note: Lookup(pitch, note) → freq
row 01   ...  ..  ..   V20       ; hold; vibrato (shared lfo) running
row 02   E-4  01  04   P41       ; new note + portamento (note_on re-seeds acc)
row 03   ===  ..  ..   ....      ; gate-off (automaton edge, m_54F5&$20)
```

`inst 01` binds waveform/AD/SR/pw from `m_5591[8]`; `P41` is the porta
Accumulator seeded at row 02's note-on; `V20` is the shared vibrato Lookup on
`ctr_5525`; the gate-off at row 03 is the control automaton's note-off edge.
`render` walks lanes at the tick rate, seeds note_on generators from `inst`,
steps clock/frame generators every frame, and writes planes in frameprog
order — reproducing `Commando.frameprog.txt` frame-for-frame (Gate T).

## 10. Milestones

- **M-T1** *(prototype landed, `tracker.py`)* engine lift (clocks, ET table,
  tempo) + note lane by value-inversion + `render` + Gate T on Commando:
  bit-exact, **388/414 freq-pairs (93.7%) interpreted as notes**, the 26
  residual being portamento/vibrato frames.
- **M-T2** *(landed)* the §7a passes: `_const_base` is SUB-aware/16-bit
  wrapped (`idx-$19D7 → $E629`, full suite green), and `movefwd.py` relabels
  SID-shadow-buffer stores onto the SID (detected by a parallel `sid[i]=B[i]`
  flush over a *writable* buffer, so read-only pitch tables and scalar
  per-voice cells are excluded — Commando stays shadow-free). Witness: Krakout
  `$E686→$D400` lifts freq provenance `other → slide` (now a real accumulator
  cell `$E588`), through the copy the const-base backtrace could not cross.
- **M-T3** *(octave-shift + detune landed)* one-octave tables invert by
  `words[semitone] >> octave + detune`. The base is **discovered from the
  graph**: `tracker._octave_pitch` collects every constant base a memory read
  indexes across `model_procs` (`$E629` appears there in normalized
  `mem[$E629+idx]` form once `_const_base` is wrap-aware) and keeps the one
  that confirms one-octave ET in either endianness — Krakout resolves to
  **`$E629` big-endian** (endianness matching the `$E536` shift loop, where
  `m_E613` is the high byte), uniquely; no hardcode, no memory scan. A note is
  taken when the residual is within half a semitone (unambiguous). Result:
  Krakout `other → notes`, **46.2% of freq-pairs (277/600) recovered, Gate T
  bit-exact**; the melodic voices are clean (`C3`/`B2`, **detune ∈ {0, ±29,
  ±30}** = the vibrato triplets), voice 3 carries large detunes (not
  pitch-tracking, honestly flagged). Inversion is **detune-tolerant** (nearest
  note within half a semitone) but gated by a **continuity filter**: a detuned
  frame is a note only as vibrato on the current note (same index) or a fresh
  *exact* anchor — an excursion to an unrelated note stays residual. This
  cleanly separates vibrato-around-a-note from mid-transition excursions:
  Commando stays clean (94%, detune 0 — the melody, modulation residual),
  Krakout keeps only its melodic notes (`{0, ±30}` vibrato; voice-3 excursions
  now residual), Ghouls' inline vibrato resolves against its multi-octave
  table as note + coherent detune ramp (61%). Gate T bit-exact throughout.
  *Split-block tables (landed):* `_pitch` has a third discovery path after
  direct-provenance and one-octave-shift — **paired lo/hi blocks at two
  distinct graph read bases**. DefMON (Automatas) stages freq into per-voice
  scalar cells fed by separate freq-lo/freq-hi byte tables at unrelated
  addresses (`$1578`/`$1614`); neither gets a freq role and movefwd's
  *indexed* copy rule misses the *scalar* staging, so provenance stalls. The
  path pairs the two tables **read with the same index** (the graph's own
  pairing — co-indexed reads, not a byte search) and takes the extent from
  their **declared table size**; `et_check` only confirms. For Automatas that
  is `$1578`/`$1614` (co-indexed, decl size 86); nothing is scanned or
  hardcoded — base, pairing, and extent all come from the graph.
  **Athena (Galway)** wears every disguise at once and still resolves:
  `freq_lo=m_C517[note]`, `freq_hi=m_C55F[note]` — a split lo/hi table reached
  through **wrap-offset** bases (`−$3AE9=$C517`, `−$3AA1=$C55F`) and **scalar
  shadow** staging into `$5E0F/$5E10`; the pairing path recovers the 40-note ET
  table (glide-based, so freq is mostly mid-slide — the table is the
  slide-target map, few on-target frames). All 10 showcase tunes now recover a
  pitch table. *Remaining:* fold the detune/slide trajectory into explicit §6
  vibrato/portamento transforms; decode Follin command-script lane structure (§8).
- **M-T3.5** *(pitch-detection audit, landed)* the corpus is tracker-diverse
  by construction: `tools/tracker_corpus.py` enumerates HVSC (56k tunes),
  identifies each player by **SIDId** signature (`cadaver/sidid`; custom
  players keyed by a relocation-invariant play-opcode hash), and dedups to
  **144 distinct trackers (128 SIDId-named)**; `tools/tracker_audit.py` runs
  production `_pitch` on the 121 v1-auditable ones (23 are v2/CIA, out of
  scope). This exposed detection at **66%** across real tracker diversity (vs
  a misleading 81% on the composer-biased set — composers reuse one tracker).
  Graph-only paths landed iteratively: split extent = **max** decl (freq-hi
  under-declared when low notes have `hi=0`), a **multi-octave interleaved**
  declared-table path, and a decl-bounded **leading-ET-run** validator
  (`_et_words`: skips a leading rest, trims the over-declared garbage tail,
  falls back to `et_check` so nothing regresses). Recovered
  SidWizard/Chris_Cox/Modulator/Ariston/Michael_Delaney, 0 regressions.
  **"Computed pitch" retracted:** characterizing the misses showed every one
  examined has a real static table in a representation detection doesn't yet
  parse — Novaload is a **segmented** interleaved ET table (per-octave
  sub-tables with `0` markers, octaves restarting), Games_Creator a
  non-co-indexed split, MCS/NinjaTracker **scalar shadow-staging** (`$9FC0`,
  `$7CD0` = zeros), K-Byte a constant-hi/period layout. Truly generative pitch
  (freq = arithmetic on the note, no table) is unconfirmed in the corpus and
  rare on 6502; a "no table" verdict must be proven from the freq code, not a
  narrow ET scan. *Remaining:* segmented/period-table layouts, scalar-shadow
  `movefwd`, and per-representation handling for the long tail.
  *(four graph-only extent/validator handlers landed, 80→86/121)* the misses
  had ET tables detection couldn't *bound* or *confirm*; four graph-anchored
  additions to `_et_words`/`_paired_pitch`/`_interleaved_pitch` — base still from
  a graph read/co-indexed/freq-role, extent from the confirmed ET run:
  - **Gapped semitone-indexed** (`_sparse_et`): a table whose unused notes read
    `0`; the interior zeros defeat both the leading-run and the `et_check`
    median. Confirms by regressing `12·log2(word/ref)` against the **array
    index** (the index IS the semitone) — ≥90% of non-zero entries within 0.3
    semitone over a ≥24 span; every octave (`w[i+12]=2·w[i]`) falls out.
    Recovered **Mjoosic_Mejker** (`$432E`, 41 gapped notes).
  - **Segmented per-octave** (`_segmented_et`): a chromatic scale chunked into
    `0`-separated blocks that restart each octave (breaking the global index
    law). Each zero-bounded run must be chromatic; ≥3 runs, ≥36 notes.
    Recovered **Novaload** (`$E2B1`, 5 segments, 96%-covered).
  - **Undeclared co-indexed split** (`_paired_pitch`, `bounded=False`): a
    co-indexed lo/hi pair with **no decl** takes its extent from the ET run in a
    capped window; guarded by disjoint blocks (`|lo−hi| ≥ len`), ≤108 notes, and
    the run itself. Recovered **X-Ample** (`$196A/$190B`), **Companion**
    (`$CA80`, 100%), **Companion/Jay_Derrett** (`$1C4B`, 100%).
  - **Undeclared interleaved at a freq-role base** (`_interleaved_pitch`,
    `bounded=False`): a 16-bit interleaved ET table whose base carries a
    freq_lo/hi provenance role but no decl; extent from `_longest_run` (the
    longest interior chromatic run — a leading near-anchor or garbage tail no
    longer truncates it), ≥48 and ≤108 notes. Recovered **NinjaTracker_V2.x**
    (`$7CE8`, 83 notes, 100%-covered) — its freq is glide-computed in zero page,
    but the slide *target* table is this undeclared interleaved read.
  All confirmed by full-Songlengths re-audit (0 regressions) and, for each
  recovery, ≥65% note coverage (100% for four of six). **Still unhandled, with
  the blocking graph fact:** MCS `$9E00` yields no ET at any record stride
  (2/3/4-word, every offset) — it is an instrument/waveform block, not a
  semitone-indexed freq table, and the note→freq path does not pass a static ET
  table reachable from the freq store. Neil_Brennan `$80A2/$80A5` are a 3-byte
  **per-voice current-freq scratch** record (lo/hi 3 apart, indexed by voice),
  not the note table. These need note→record decoding, not more provenance; no
  scan or "computed pitch" claim is warranted.
  *(ET-lattice generalization landed, 91→96/121)* the remaining
  `table-unrecognized-layout` misses had ET tables whose values do **not** ascend
  one chromatic step per index — periods (∝1/freq, descending), diatonic/modal
  subsets, and sparse note sets — so every ascending-semitone-ratio check misses
  them. Generalized to the pitch law itself: a pitch table's values are
  `ref·2**(k/12)` for integer k regardless of physical quantity, so `12·log2` of
  each value rounds to a note index. **`_lattice_et`** (new `_et_words` fallback,
  base/pairing/extent still graph-anchored) takes the leading monotone run of the
  graph window and confirms ≥90% of it lands on the chromatic lattice within 0.15
  semitone, over a ≥24-semitone span with ≥12 distinct notes; monotonicity
  rejects arpeggio/pattern streams, whole-window purity rejects a coincidental
  interior run inside noise. Inversion is nearest-value `_note_direct` (sign of
  slope is irrelevant to a value match). Recovered **Loadstar_SongSmith**
  (`$CEBB`, descending period, 100%), **Games_Creator** (`$2E80/$2EC0`, major
  scale, 100%), **Andy_Brown** (`$0F2D`, Lydian, 98%), **Music_Construction_Set**
  (`$0C08`, 100%), **Der_Baer** (`$6635/$6695`, 13-note sparse chromatic, 40% —
  slide-heavy tune, exact where it anchors). Full-Songlengths re-audit, **0
  regressions**; a decoy that passed the loose ratio test but had **0% note
  coverage** (CyberTracker `$59ED`, an off-lattice interior run) is rejected by
  the whole-window purity guard, not accepted. **Still unhandled, with the
  blocking graph fact:** Jason_Briggs/Anter-Planter/Bird_on_the_Run are
  non-monotone note **streams** (song/pattern data feeding freq, mono≈0.55), not
  a reusable indexed table; K-Byte/Mark_Cooksey expose no on-lattice window at
  any freq-role base (constant-hi / stride-3 scratch record); JammicroV1's sz23
  co-indexed pair is not ET in any pairing. These need note→record decoding, not
  more provenance.
  *(freq-role tiny-decl window landed, 96→97/121)* Ed_Bogas/Accolade stages
  freq through a per-voice scalar shadow, and the declarer split its interleaved
  16-bit ET table into byte-sized decls (`$3946` declared size 1), so the
  declared-table path computed a sub-24-word window and skipped it. Fix: a
  **freq-role** base whose decl is too small to be the table (`<48` bytes) takes
  the undeclared capped window instead of its decl size (`decl = b in sizes and
  (sizes[b] >= 48 or b not in freq)`); `_lattice_et` confirms the descending
  period run. Recovered **Ed_Bogas/Accolade** (`$3946`, 8-octave period table,
  Gate-T bit-exact, 96% note coverage). Full-Songlengths re-audit, **0
  regressions**; surgical to freq-role bases (provenance-typed, low
  false-positive risk), non-freq small decls unchanged. **Remaining bucket
  characterization (graph-only, no recovery yet — precise blocking fact each):**
  the observed SID freqs across nearly every remaining miss ARE equal-tempered
  (distinct-set on-lattice ≈1.0), so real pitch content exists — but no graph
  read-base carries an ET table in any layout (interleaved LE/BE, co-indexed
  split, lattice), and none reads through a zp-pointer indirection (the
  decompiler resolves pointers to constant bases, so const-base provenance
  already sees every table). The freq is staged through **per-voice scalar
  shadow banks** written from values that are not one-hop table reads:
  DefleMask_v2 `$CFE9` (shadow flushed to SID via a register-offset remap
  `$CFD3`) is written from a subroutine-return value (`call [12,4313]` via the
  stack cell `$1FB`); Synth_Executor `$ECD5/$ED19` is a multi-hop shadow copy
  chain (`$EDD1→$ECD5→SID`) terminating at reset constants, and its freqs are
  glide-dominated (only 23% on-lattice — smooth portamento, low invertible
  coverage even if a base table were found); Antony_Crowther `$772C` is a
  zero-page accumulator/slide engine (`freq = w1+w2`, seeds from `$AA/$AB`, no
  table ≥`$100` in the write chain). These are deep-shadow-behind-subroutine or
  accumulator engines, needing note→record / cross-call value decoding, not more
  static provenance. No generative verdict is claimed: a "no table" proof needs
  the freq code to show note-arithmetic with no table read, which the
  subroutine-return and accumulator-seed chains do not yet expose.
  *(interprocedural value-flow investigation, no recovery, 97/121 unchanged)*
  built cross-call freq provenance (env + a **global** cross-proc const-cell
  reaching-defs map + copy-chain fixpoint) starting from every SID freq store,
  and ran the **real** detectors (`_et_words`, `_lattice_et`, split, single-byte)
  over every graph read-base and co-indexed pair for all 24 misses. Result:
  **no const-base ET table is reachable from any miss's freq store** — not
  16-bit interleaved, not co-indexed/arbitrary split, not single-byte (the coarse
  octave-doubling layout). The blocker is **indirection, not the call boundary**:
  the freq value chain terminates at a `mem[zp_ptr]` read into per-voice song
  data, at a `mem[$0100+sp]` stack pop, or at a moving stream pointer — never at
  a constant table base. 10 of 24 reach **zero** constant addresses at all
  (pure pointer/stack flow). Confirmed structure per pattern:
  - **Antony_Crowther_V2** — `freq_hi = mem[ptr_00AA]`, `ptr_00AA` (`$AA/$AB`)
    loaded per voice from banks `ctr_775A[x]`/`m_7757[x]`, advanced `+3` per row;
    the freq words are inline per-voice sequence data behind an indirect pointer,
    not a note→freq table. Observed distinct freqs 0.99 on-lattice (real ET).
  - **DefleMask_v2** — a register-dump/stream player: `sub_000C` fetches the next
    byte from the moving song pointer `ptr_0013` (`$13/$14`), the SID register
    offset comes from remap table `m_CFB5`/`m_CFD3`, the value is streamed raw
    into shadow `m_CFE9` (the `$1FB` cell is a save/restore temp, not a table
    return). No note-indexed table; freqs 1.00 on-lattice.
  - **Ultima_III-Exodus** — register-dump/stack player: `sid[a+$D400] = a0` with
    `a0 = mem[$0100+sp]` (stack pop) and register offset `a = m_9DD6[m_9DD4]`
    (remap). A genuine coarse ET byte table exists in memory (`~$9F09`, values
    doubling per octave) but is **not read at any constant base** (reached only
    via the pointer/stack), so no sound graph path ties it to the freq store.
    Freqs 1.00 on-lattice.
  - **Synth_Executor** — `freq = m_ED19[x] + m_ECD5[x]` (base + glide), base
    `m_ED19[x] ← m_ED92[x] ← m_EE32/m_EE33 ← mem[ptr_009E+2]` (song pointer),
    glide `m_ECD5[x] ← m_EDD1[x]` (reset to constants). Every hop is a per-voice
    scalar bank indexed by **voice**, not note; the chain ends at a pointer, and
    freqs are only **0.17** on-lattice (smooth portamento), so uninvertible even
    if a base table existed.
  The two const candidates a permissive scan surfaced are **decoys**: C64_World
  `$4306/$4680` gives garbage words at 12% coverage; Ultima's `$9E30/$9E31`
  co-indexed "ET run" is a stride-1 overlapping-byte artifact of the coarse byte
  table, not a table read. No production change landed: the sound interprocedural
  cell/copy pass reaches no new ET table and would only risk decoy freq-roles on
  non-ET shadow banks. These misses need **note→record / stream decoding**
  (resolve the per-voice pointer's data region and record stride), not more
  value provenance.

  ### The 24 no-static-table exceptions

  `tracker._pitch` (source-side, static-table lookup) recovers **97/121**. The
  remaining 24 have **no reachable static ET table** — freq is streamed, glide-
  computed, or read inline through moving per-voice pointers. Two prior
  interprocedural investigations proved this: the freq value chain terminates at
  a `mem[zp_ptr]` read into song data, a `mem[$0100+sp]` stack pop, or a moving
  stream pointer — never a constant table base. The mechanism of each (from
  `out/pitch_triage.jsonl` graph facts + the M-T3.5 interproc notes above):

  | mechanism (freq source) | count | tunes |
  |---|---|---|
  | **stream / arithmetic** — no freq-role base; pointer/stack/register-dump flow | 12 | 1k, An_Attempt_Was_Made, Archon, Block_n_Bubble, Blueprint, C64_World, Cliff_Hanger, Cohens_Towers, Crazy_Painter, Delta_Man, Dill_Pickles, Ultima_III-Exodus |
  | **freq-role base, non-ET layout** — a freq_lo/hi read whose bytes confirm no ET table | 9 | 1942, 1_Byte_Under_512, Ace_of_Aces, Alien, Anter-Planter, Asterix_and_the_Magic_Cauldron, Bert_the_Bug_Bites_Back, Bird_on_the_Run_II, Donkey_Kong |
  | **scalar-shadow** — freq staged through a per-voice cell that reads zeros (deep shadow / glide) | 3 | 7_of_4, A_Spaceman_Came_Travelling, Another_One_Bites_the_Dust |

  Interproc-confirmed representatives: **Antony_Crowther_V2** (Another_One…) —
  `freq_hi = mem[ptr_00AA]`, banks advanced `+3`/row (pointer-inline sequence
  data); **DefleMask_v2/v12** (7_of_4, An_Attempt…) — moving song pointer +
  register remap streamed raw into a shadow (register-dump); **Ultima_III** —
  `sid[a] = mem[$0100+sp]` (stack-pop register-dump); **Synth_Executor**
  (A_Spaceman…) — `freq = base[x] + glide[x]`, base from a song pointer
  (portamento-dominated). Observed SID freqs across nearly all 24 are equal-
  tempered, so real note content exists — recover it from the *output*.

  *(output-side pitch recovery — induce the lattice from observed freqs, landed)*
  New module `deity_informant/pitchind.py` recovers the per-voice note lane from
  the **observed** SID freq stream, independent of how freq was computed (reading
  the final register value discards all shadow staging). `tracker._pitch` is
  untouched. The pipeline, per voice:
  - **Observe** the canonical per-frame freq word (`framelog` projection, full
    Songlengths).
  - **Peel** the graph-recovered modulation by dwell segmentation: a run staying
    within ¼ semitone of its running mean for ≥3 frames is a held note or slide
    *target*; its mean is a base freq, mid-slide/vibrato transients drop out.
    Voice freq kinds (note/slide/other) come from the graph `song_model`.
  - **Induce** the ET lattice from the base set: on a lattice `ref·2**(k/12)`
    every `12·log2(base)` shares one fractional part `phi`; `phi` is their
    circular mean, `fit` the share within 0.15 semitone of it. This is the
    tune's own induced pitch table — observed-set, not a byte scan.
  - **Invert** each freq frame to `k = round(12·log2(word) − phi)`, the note-lane
    index; coverage is the share of freq frames landing within 0.34 semitone.
  - **Honesty guard:** a voice counts as recovered only at `fit ≥ 0.9`,
    `coverage ≥ 0.25`, **and** ≥5 distinct notes over a ≥7-semitone span (a drone
    or 1–2-pitch percussion voice trivially fits a lattice — the span/distinct
    floor rejects it). A base set that does not fit is refused with its fit
    number, never forced onto a lattice.

  **Result (full Songlengths, `tools/pitchind_measure.py`):** **21/24 (88%)**
  yield a note lane. Typical melodic/harmonic voices recover at fit 1.00, coverage
  ≈1.00, 8–49 distinct notes. Glide-dominated voices honestly report low fit/
  coverage (Synth_Executor slide voices fit 0.44/0.43 — smooth portamento passes
  through few on-lattice frames; only its held voice recovers). **The 3 refused,
  each with the precise blocking fact:**
  - **Dill_Pickles** — the model emits **zero** SID writes across 7100 frames (a
    silent/uncovered decompile), so there is no output to invert. Not a pitch
    problem — a decompile-coverage gap.
  - **1k** — a 1 KB intro; the only pitched voices carry **2–3 distinct freqs**
    total (below the 5-note floor). Trivially on-lattice but not a note lane.
  - **Bird_on_the_Run_II** — a **non-monotone note stream** (song/pattern data
    fed straight to freq); its base set fits the lattice at only **0.76** (< 0.9,
    span 119 semitones is implausible), matching the interproc `mono≈0.55`
    finding. Refused, not forced.

  **Regression spot-check:** on 8 static-table hits from the 97, the induced
  lattice agrees with `tracker._pitch`'s static table per-frame at **1.00** (six
  tunes) / **0.999** (two) — output-side induction reproduces the static tables
  where they exist. Tests: synthetic glide+vibrato peels to a clean lattice
  (fit 1.0, coverage 0.95); an atonal random series is refused (fit < 0.9);
  slope-invariance (descending period tables share the phase); a Commando
  integration test asserts induced↔static agreement. **This is the "recover the
  notes from the other direction" deliverable — decompose modulation, and what
  remains is the per-note base frequency, however it was calculated.**
- **M-T4** lane/instrument codec (Gate codec) + residual generators ⇒ Gate T
  over the full corpus.
  *(honest render landed)* `render` no longer passes the observed frame through:
  it partitions every canonical entry into an **interpreted** generator (accepted-
  note freq words) or an **explicit residual** byte-program, and rebuilds each
  record from those two sources ONLY. A `gate_t` PASS therefore certifies the
  partition is *complete* — every plane is interpreted or residual, together
  bit-exact — rather than trivially copying the output. `Coverage` now reports
  `interp/residual/total` writes and a per-plane breakdown over the whole
  projection; the prior "notes interpreted %" measured freq alone. Hubbard's
  Commando: **47% of all writes interpreted** (freq 93%; ctrl/AD/SR/pw 100%
  residual). Next M-T4 slices shrink the residual by interpreting the control
  automaton (ctrl/gate), ADSR, and the pulse-width accumulator, then the
  emit/parse codec.
- **M-T5** Gate M: interpreted↔raw regeneration; note-name/interval/transpose
  relationships, motif detection under transform — the musical layer proper,
  each step verified by exact regeneration of the tracker below it.

## Consolidated schema

```
tracker = {
  engine: { clocks:[Clock], tables:[Table],
            generators:[Accumulator|Lookup],   # each with trigger+plane
            instruments:[Instrument] },
  song:   { lanes:[ Lane(orders, patterns|script) ],   # per voice
            global_lane: [row],                          # song-clock effects
            tempo: {frames_per_tick | per_voice} },
  cover:  { interpreted:[plane], residual:[plane] }      # coverage law
}
```
