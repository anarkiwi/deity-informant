# A unifying control abstraction for C64 music editors

Survey of four independent editor models — **GoatTracker** (`pygoattracker`),
**SID-Wizard** (`pysidwizard`), **JCH NewPlayer** (`pyjch`), **DefMON**
(`pydefmon`) — over their shared substrate (`pysidtracker`), to derive **one
primitive** the deity-informant `tracker` layer can lift *into*: the triggered
generator. Motivation: the GoatTracker ground-truth check (docs/tracker.md,
memory) showed the current lift emits each **wavetable arpeggio** step as a
spurious note, because it has no first-class notion of the *instrument program*
that produces those frames. The unification (§2) is not a schema with a level per
editor feature; it is a single generator, and a whole tune — arrangement,
patterns, notes, instruments, effects, across all four editors — is one graph of
those generators wired by their triggers.

## 1. Survey — the control structures, side by side

| concern | GoatTracker | SID-Wizard | JCH NewPlayer | DefMON |
|---|---|---|---|---|
| **song top level** | `restart` loop | `Loop(position)` | orderlist restart | **song table** (`song_position_arrays`, `set_step v1/v2/v3`, `set_jump target,count`) |
| **per-voice sequencer** | `Orderlist(entries, restart)` | `sequences[v]` | opcode stream via `orderlist_ptr_table` | `arranger_v1/v2/v3` → `pattern_pointer_table` |
| **orderlist ops** | Play/Transpose/Repeat | Play/Transpose/TempoOverride/Loop/End | play/**subpattern call+return**/transpose/loop | play/jump (arranger control bytes) |
| **pattern** | `Pattern.rows: [Row]` | `Pattern.rows` (NOP-packed) | opcode-stream rows | `pattern_events` → `[PatternEvent]` |
| **note event** | `Row(note, instrument, command, data)` | `Row(note, instrument, fx, fx_value)` | note/inst/cmd opcodes | `PatternEvent(note, slot_a, slot_b, gate_a/b/n, duration)` |
| **instrument / program** | `wave/pulse/filter_ptr` → shared `Table(left,right)` | per-inst `wf/pw/filter_table` + chord/arp | wave/pulse/filter **opcode programs** in image | **sidtab**: shared table of register-write rows (`SidtabRow`), walked by `sidtab_jp`(jump)/`sidtab_dl`(delay) |
| **note→freq** | fixed `FREQ_TABLE` (ET) | tuning table | split `freq_lo/hi` (ET) | `NOTE_PITCH_LO/HI` (ET) |
| **clock** | frame; `speedtable` | `frame_speed`, `TempoOverride`, `tempo_table` | frame; funktempo | frame; per-event `duration` |
| **dedup** | pattern reuse | NOP-packing | subpattern reuse | `_dedup_patterns`, `_dedup_sidtab_rows` |

Shared base `pysidtracker` factors the substrate: `NoteFreqTable(hi, lo, addr)`,
`Cadence(cycles_per_call, source, clock_hz, latch, dynamic)`, `TriggerSource
{PAL,NTSC,CIA}`, `register_grid`/`grid_from_writes` (per-frame SID projection),
and a per-format `Player`/oracle. It provides **no** format-independent song/
control model — the gap this proposal fills.

## 2. The one invariant

The four "levels" I first drew (song table / sequencer / instrument VM) are **not
different kinds of thing**. Each is a table that some trigger advances, emitting
either values or *further triggers*. There is exactly one primitive:

> **A triggered generator**, identified by **(transfer, trigger, route)** — not
> by which editor table it came from. Two generators with the same triple are the
> same generator. The transfer is one of exactly three:

```
Generator = (transfer, trigger, route)
  transfer : DIV(n)            # emit one tick per n input triggers (a clock)
           | LOOKUP(seq)       # emit seq[i]; i advances (and may jump) per trigger
           | RAMP(seed,step,bound)   # emit seed + step*count, bounded/wrapped
  trigger  : frame | Event(g)  # the root frame clock, or another generator's edge
  route    : Plane(p)          # a SID register plane (freq/pw/ctrl/ad/sr/cutoff/res/vol)
           | Fire              # the emitted value IS a trigger into downstream generators
```

Everything reduces to this triple. Identity is behavioural: a pitch table and an
arp table are both `LOOKUP`; they differ only in trigger (note value vs
note-frame) and route (freq vs freq-offset) — so they are *different generators*,
while GoatTracker's arp and DefMON's arp share the triple and are *the same one*.

A whole tune — for **every** editor here — is a **graph of these generators wired
by their triggers**, drawn from a fixed **archetype inventory** (the closed set of
`(transfer, trigger, route)` triples that occur). `tools/study/utune.py` computes
it from a real parse: a GoatTracker tune and a DefMON tune populate the **same
9/9 archetypes**, differing only in instance counts — the same machine, different
parameters. Nothing is a level; every node is one of those archetypes:

- **tempo** — trigger `frame`, state a divider counter, emits `Fire(row-clock)`.
- **song / arranger** — trigger `Event(pattern-end)`, state the orderlist/song
  position, emits `Fire(pattern-select)` (and carries transpose). DefMON's
  `set_step`/`set_jump` and GoatTracker's `restart` are the *same node* with more
  or fewer entries — not an "optional level."
- **pattern (row) generator** — trigger `Event(row-clock)`, state the row cursor
  in the selected pattern, emits `Fire(note-on){note, program-refs, duration}`.
- **note→freq** — trigger `Event(note-on)`, emits `Plane(freq) := NoteFreqTable[
  note + transpose]`. The one pitch-carrying emit.
- **instrument programs** (wave / pulse / filter, *or* DefMON's raw sidtab) —
  trigger `Event(note-on)` to seed **and** `frame`/`Clock` to step, state a table
  with delay+jump, emit `Plane(ctrl|pw|cutoff|freq-offset)`. A note-on that fires
  **two** of these is DefMON's `slot_a`/`slot_b`; firing one is everyone else.
- **arp / vibrato** — the *same* instrument-program node whose emit is a
  freq-offset; "shared" arp = its trigger is a global `Clock` rather than
  `note-on`. This is why arpeggio is not a note: it is a generator edge, not a
  row.

Two derived facts fall straight out and need no special case:

- **Typed vs raw is a property of one node's `emit`, not two kinds of node.** A
  GoatTracker wavetable and a DefMON `SidtabRow` are the identical
  *table-stepped-by-a-trigger* generator; they differ only in whether `emit` is
  read as a typed plane function (`NoteRel(k)`, `Pulse(speed)`, …) or a literal
  `Plane(p):=byte`. Interpreting a node = refining its `emit`; that refinement is
  exactly deity-informant's honest-render interpreted/residual axis (PR#56),
  now a per-generator attribute.
- **`NoteFreqTable`** (`pysidtracker`, ET semitone→word) and **`Cadence`** (clock)
  are just the two distinguished generators every graph contains: the pitch table
  is the note→freq node's lookup, the cadence is the root `frame` clock.

Editors differ **only** in the wiring (which generators fire which) and each
node's byte encoding. The graph is the same graph.

## 3. The unifying abstraction — one generator graph

There is no `UTune` struct of parallel levels; there is a **directed graph of
`Generator` nodes over `pysidtracker` primitives**, evaluated per frame by
propagating triggers from the root clock:

```
Tune = Graph( nodes: [Generator], edges: Event triggers )
       + freq_table : NoteFreqTable      # the note→freq node's lookup
       + cadence     : Cadence            # the root frame clock

# every structure in §1 is a Generator, distinguished only by trigger+emit:
#   arrangement  : trigger Event(pattern-end)  emit Fire(pattern-select)+transpose
#   row/pattern  : trigger Event(row-clock)    emit Fire(note-on){note,refs,dur}
#   note→freq    : trigger Event(note-on)      emit Plane(freq):=NoteFreqTable[n]
#   wave/pulse/  : trigger Event(note-on)+Clock emit Plane(ctrl|pw|cutoff|Δfreq)
#     filter/arp                                 (typed emit, else Raw snapshot)
```

Each editor is a **decoder that emits this graph**; each editor player is the
**evaluator oracle** for it (§4). The deity-informant lift produces the same graph
from the SID. Because sequencing *is* generators, the reuse layer (shared pattern
nodes, shared program nodes, transpose on an arranger edge, loop = a back-edge)
is intrinsic, not a separate schema — DefMON's `_dedup_patterns`/
`_dedup_sidtab_rows` are just common subgraph sharing.

- **This is deity-informant's §3 engine, taken to its conclusion.** The tracker
  already has triggered generators (Clock/Accumulator/Table/Lookup, each with
  `trigger ∈ {note_on, clock, frame}` and a `plane`). The only change is
  extending `emit` to also `Fire` downstream generators — so patterns, orderlists
  and the song table stop being a separate sequencer and become generators too.
  One primitive, top to bottom.
- **Notes vs effects becomes topological.** A note is the payload of a `note-on`
  edge; arpeggio/vibrato/PWM/filter are downstream generator emits on that edge.
  The GoatTracker arp misread was reading generator output as new note-on edges;
  in the graph it cannot happen — those frames have no note-on edge.

## 4. Verification law (design-first, per the owner rule)

`eval(graph)` propagates triggers from the root `frame` clock and produces a
`pysidtracker` register grid; it MUST equal the editor player's grid
(`pygoattracker.iter_frames`, `pysidwizard.iter_writes`, `pyjch.iter_frames`,
`pydefmon.register_writes_from_player`) frame-for-frame over Full-Songlengths —
**Gate U**. Two tiers, each verified by regenerating the one below:

- **Gate U0 (output).** `eval(graph)` ≡ the format player's grid ≡ the SID's
  cycle-exact projection (the plane deity-informant already gates). Proves the
  generator graph is *complete* — every plane is some node's emit; the
  honest-render discipline, now "every emit accounted for," with un-refined nodes
  emitting `Raw` snapshots as the guaranteed-complete floor (DefMON's native form).
- **Gate U1 (structure).** For each editor's parser, the recovered graph matches
  the editor's parsed nodes/edges up to documented normalizations (transpose on
  edges, NOP unpacking, subgraph dedup). The "are the notes even right" check as a
  law — GoatTracker voice-2 passes (25/25); its arpeggio voice passes U0 but fails
  U1 (arp output was read as note-on edges), the exact gap the graph closes.

## 5. Next steps

1. **Landed: the primitive and Gate U0** (`deity_informant/ugraph.py`).
   `Generator(transfer, trigger, route)` with `DIV`/`LOOKUP`/`RAMP` plus a `RAW`
   transfer, `trigger ∈ {frame, Event(i)}`, `route ∈ {Plane(reg), Fire, Raw}`;
   `eval_graph` propagates triggers from the root frame clock and projects
   through `framelog.canonical` — the ONE projection, as frameprog §3 requires,
   never a second one. `gate_u0(graph, frames)` returns `framelog.diff`'s first
   divergence or `None`, matching `tracker.gate_t`'s contract.

   The `RAW` node is §4's guaranteed-complete floor made operational:
   `from_frames(frames)` yields a graph that passes Gate U0 by construction at
   0% interpreted coverage, and **refinement moves emits out of RAW** rather
   than building coverage up from nothing. Because a refined plane is removed
   from RAW, the two never contend for a register and the interleaving stays
   well defined; order-preserved sections (ctrl/AD/SR) stay whole in RAW until a
   generator can reproduce their order. `coverage()` reports the
   interpreted/RAW split per plane, exactly as `gate_t` reports
   interpreted/residual. Mutation-tested: a wrong `LOOKUP` value, a dropped
   ordered write, and two swapped ordered writes are each detected.

   Remaining for this step: wire the four editor players as Gate-U0 oracles
   (the fuzz players and hand-built frames are covered; corpus GoatTracker ×2 +
   DefMON Automatas/Goto80 next), and refine the note→freq LOOKUP from the
   **declared** frameprog `data` tables rather than a scan of `mem0`.
2. Recover the instrument-program nodes (typed wave/pulse/filter emit, `Raw`
   snapshot otherwise) from the control automaton (song-model items 4–6), seeded
   on `note-on` edges.
3. Move arpeggio/vibrato from the note lane to a downstream generator on the
   note-on edge — a note-on carries one note; the arp is an edge, not a row.
   Re-run Gate U1 on GoatTracker — voice-1 should pass.
4. Recover the arrangement/pattern/transpose subgraph (shared nodes + back-edges)
   from `streams.py`; DefMON's `_dedup_*` is the reference for subgraph sharing.

## 6. Parked proposal: theme + variation (pattern collapse)

After factoring instruments out of pattern identity (a pattern is notes, an
instrument is a shared bank reused across notes and voices), a dense tune still
shows many patterns (6581 Words: 92). Two abstractions do NOT collapse it, measured:
melodic-motif grammar (80 distinct interval-shapes; top-20 3-note motifs cover only
27% — through-composed) and rhythm×melody factoring (61 rhythms + 82 melodies = 143
> 92). The one that does: **theme + variation.**

Most "distinct" patterns are near-copies of a theme differing by a couple of note
edits. Clustering the transpose-invariant melodies by edit distance: 80 melodies →
71 themes (edit≤1) → **56 (edit≤2)** → 51 (edit≤3). So:

- a **theme** = a canonical interval-sequence (transpose-invariant phrase);
- a pattern = **(theme, transpose, patch)**, patch a short edit list; exact and
  reversible — `apply(patch, theme) == pattern`;
- collapse: 103 used → 92 note-patterns → **56 themes + variations**.

Fits the generator model: a theme is a LOOKUP (note-sequence generator); a variation
is that generator plus a tiny patch generator — the same shared-resource + reference
+ small-delta shape as instruments and transpose. Lossless (theme+patch reconstructs
the pattern), so it never over-compresses distinct music; the edit-distance threshold
(default ≤2, reported per pattern) only decides how much edit is "the same theme," and
at edit=0 it degrades to today's behaviour. Statement-and-variation is fundamental to
music, so verse/chorus tunes compress far more than this through-composed 30%.
