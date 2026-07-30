# tracker — the universal tracker layer

The final step of the pipeline **6502 → P-Code → sidprog (cycle-exact) →
frameprog (frame projection) → tracker**. One law per boundary; the tracker's
boundary is frameprog ↔ tracker.

`deity_informant/tracker.py` consumes a `frameprog.FrameProgram` — nothing else.
It reads the frame program's **declared const tables** (docs/frameprog.md,
`datadecl`) and the frame projection frameprog itself produces, and re-expresses
the tune as a graph of triggered generators over **notes** (equal-tempered
semitone indices) instead of register bytes.

## 1. The one law

A tracker is a `Graph` of generators consumed by ONE reference evaluator,
`eval_graph`. The law is a single frame-projection equality:

```
framelog.diff(tracker.eval_graph(graph, n), tracker.oracle(prog, trace, n)) is None
```

`oracle` is `frameval.eval_fp` — frameprog's own evaluator, already gated
against the cycle-exact walker by **Gate FP** (docs/frameprog.md §1.4), which in
turn is gated against the P-Code VM by **Gate C** (docs/sidprog-language.md).
So a passing tracker is bit-exact at the frame-projection plane, and there is
exactly one law at this boundary and one function for it: `tracker.gate`.

Two properties make the law meaningful rather than tautological:

- **Completeness floor.** `from_frames(frames)` is a graph of one `RAW` node
  replaying every write in order. It passes by construction at 0% interpreted
  coverage. Refinement *moves emits out of RAW* into typed generators; because a
  refined register is removed from RAW, the two never contend for one register
  and the interleaving stays well defined.
- **Nothing is passed through.** `render` rebuilds every record from the
  interpreted generators plus the explicit RAW residual only — never from the
  observed frame — so a PASS certifies the partition is complete.

Mutation evidence that the law can fail (tests/test_tracker.py): a wrong
`LOOKUP` value, a wrong `SELECT` row, a dropped ordered write, and two swapped
ordered writes are each detected.

## 2. The one primitive

Every structure in every C64 editor — song table, orderlist, pattern,
instrument program, arpeggio table, pitch table, tempo divider — is a table that
some trigger advances, emitting either values or further triggers. There is one
primitive:

```
Generator = (transfer, trigger, route)
  transfer : DIV(n)                  # one tick per n input triggers (a clock)
           | LOOKUP(seq)             # emit seq[i]; i advances per trigger
           | SELECT(table, rows)     # emit table[rows[i]]: a table at a row index
           | RAMP(seed, step, bound) # emit seed + step*count, wrapped
           | EDGE(counts)            # fire counts[f] edges on frame f: the trigger floor
           | RAW(per_frame)          # replay writes verbatim: the value floor
  trigger  : frame | Event(i)        # the root frame clock, or node i's edge
  route    : Plane(reg) | Fire | Raw # a SID register plane, or a downstream trigger
```

`RAW` and `EDGE` are the two floors — the residual in the value domain and in the
trigger domain. Refinement replaces them: a value moves out of `RAW` into a typed
transfer, and an `EDGE` stream is replaced when the generator that produces the
edge (the arrangement, §6) is recovered. Both are explicit, so the coverage
numbers never hide what is still observed rather than explained.

Identity is behavioural: two generators with the same triple are the same
generator, whatever editor structure they came from. A pitch table and an
arpeggio table are both `LOOKUP`, differing only in trigger and route — so
"typed vs raw" is a property of one node's emit, not two kinds of node, and
interpreting a node means refining its emit. A whole tune is a graph of these
nodes wired by their triggers, with two distinguished members: the pitch table
(`Graph.freq_table`, the note→freq `LOOKUP`) and the root frame clock
(`Graph.cadence`).

`eval_graph` propagates triggers from the root frame clock and projects through
`framelog.canonical` — the ONE projection, never a second one.

## 3. What is implemented

- **Pitch `LOOKUP` per voice** (§4) — the note lane. Accepted-note freq words
  are emitted by a plane-routed `LOOKUP`; every other write stays in `RAW`.
- **AD/SR `SELECT` per voice** (§5) — the instrument lane: a declared bank lane
  read at a recovered row, fired by the voice's note-on `EDGE`.
- **Clocks** (`_clocks`) — cells the play code steps by one, read off the frame
  program's procedures: `dec` + reload is a divider (its reload is
  `frames_per_tick`), a free `inc` is an LFO phase.
- **Instrument banks** (`_instruments`) — const table bases feeding a
  ctrl/AD/SR store.

`ctrl` and `pw` are **not** interpreted yet: they render from `RAW`. The coverage
numbers in §6 say so plainly; nothing is claimed that a generator does not
reproduce.

## 4. Pitch recovery from declared tables

The pitch table is an equal-tempered word table. Base, pairing and extent all
come from the **declarations** — no byte of the image outside a declaration is
read, and the ET validators only confirm.

- **Candidate windows** (`_candidates`) — every declared const table and cobase,
  read as an interleaved 16-bit table (little- and big-endian) and as a split
  lo/hi pair with every other declared base. Adjacent declarations form one
  contiguous const run (`_avail`): the boundary between them marks another read
  base, not another data class.
- **ET validators**, strongest evidence first: the whole window's median
  semitone/octave ratios (`_median_et`), the leading chromatic run
  (`_leading_run`), a gapped semitone-indexed table (`_sparse_et`), per-octave
  segments split by zero markers (`_segmented_et`), the longest interior
  chromatic run (`_longest_run`), and the monotone chromatic-lattice run
  (`_lattice_et`, which accepts period tables and diatonic subsets since a pitch
  table's values are `ref·2**(k/12)` whatever the physical quantity). Every
  reading of a window is a candidate.
- **Choice** (`_pitch`) — ranked by *explanatory power*: the share of the freq
  words the projection actually wrote that the table holds **exactly**, counted
  per frame. Exactness, not proximity: a dense decoy window is within half a
  semitone of anything, but only the real table holds the words the player
  wrote. Ties break on evidence tier, then extent.
- **Inversion** — a multi-octave table inverts by nearest word
  (`_note_direct`); a one-octave table by `words[semitone] >> octave`
  (`_note_shift`), the layout of computed-base drivers. Inversion is
  detune-tolerant (within half a semitone) but gated by a **continuity filter**:
  a detuned frame is a note only as vibrato on the current note (same index) or
  as a fresh exact anchor, so an excursion to an unrelated note stays residual.

### Snapshot soundness lives upstream

Every table is read from the post-init image, so a declaration is const data
only where the play phase never writes. That invariant is enforced in
`datadecl` (`_sound_hi`: a region's extent stops at the first play-written
cell), not compensated for here — which is why the tracker has no `_extend_et`,
no `mem0` scan and no per-entry stability ranking.

## 5. Instrument lanes: AD/SR from a declared bank at a recovered row

ADSR is written once per note-on from an instrument bank: a declared const table
of stride `s`, one lane per byte offset. The generator for a lane is
`SELECT(lane, rows)` — the **declared bytes** of that lane, indexed by a
**recovered row** — fired by the voice's note-on `EDGE`.

- **Provenance, not proximity** (`frameval.eval_src`). The evaluator records, for
  every SID store, the cell the byte came from, when the value is one byte load
  at a *pure* address (consts, locals and ops — no memory read, so re-evaluating
  it consumes no volatile input and has no side effect). That is the address the
  play code indexed, so the row is `(cell - base) // stride` and the lane is
  `(cell - base) % stride` — read off the machine, never guessed from the value.
- **The declared byte must agree** (`_classify`). A write is a lane read only if
  the cell lies inside a declared table *and* `mem0[cell] == value`. A cell the
  play phase mutated therefore never passes as constant data (#61), and every
  byte a `SELECT` emits is verified equal to the declared byte at the moment of
  that emit. The check earns its keep: over the 60-tune sample 916 ADSR emits
  read a cell inside a declaration whose byte no longer matches the snapshot, and
  all 916 stay residual.
- **Immediates** (`_immediates`). The other half of a typical note lane is the
  release write, an immediate operand in the play code (`ad = 0`). Those emits
  are `LOOKUP((c,))` for a constant `c` that the program text stores to that
  register class (`reg % 7`, since one voice-generic store site serves all three
  voices behind a dynamic offset). The value comes from the program, not from the
  observation; the coverage report keeps the two counts apart.
- **All or nothing per register** (`_instr_streams`). A refined register is
  removed from `RAW`, so *every* write to it must be explained or the register
  stays residual whole. Per voice the widest explainable set of `{ad, sr}` wins.
- **Order is checked, not hoped for** (`_refine_voice`). ctrl/AD/SR is the
  order-preserved section. The refined writes must sit at one end of the section
  in every frame — so the voice's streams can be placed before (`pre`) or after
  (`post`) the residual node — and the node order by mean position must
  reproduce the observed order in every frame. Anything else is refused, so the
  law never depends on a lucky interleaving.

The row stream is per voice, not per lane: on Commando the AD and SR `SELECT`
nodes of a voice carry the *same* rows and share one `EDGE`, which is the
instrument selector showing through. What is **not** yet explained is the note-on
timing: the `EDGE` counts are observed. Values are declared data at a recovered
index; triggers are still the floor.

## 6. Coverage (measured, 200 frames unless stated)

`Coverage(interp, residual, total, planes)` is the one partition type: emits
produced by an interpreted generator vs emits replayed from `RAW`, plus the
per-plane split.

| tune | pitch table | interpreted | freq plane | ad | sr |
|---|---|---|---|---|---|
| Commando (Hubbard), 300 frames | `$5428` interleaved, 97 words | **1504/2525 = 59.6%** | 1198/1280 = 93.6% | 153/153 | 153/153 |
| Ghouls_n_Ghosts (Follin) | `$6D35`/`$6D96` split, 97 | 444/1164 = 38.1% | 444/722 = 61.5% | 0/7 | 0/4 |
| Automatas (Goto80/DefMON) | `$1578`/`$1614` split, 120 | 798/4800 = 16.6% | 398/1200 = 33.2% | 200/600 | 200/600 |
| Athena (Galway) | `$C517`/`$C55F` split, 72 | 426/1882 = 22.6% | 426/1200 = 35.5% | 0/17 | 0/17 |
| Krakout (Daglish) | `$E629` big-endian, 12, octave-shift | 228/5000 = 4.6% | 228/1200 = 19.0% | 0/600 | 0/600 |

`ctrl` and `pw` are 0% interpreted on every tune above. Of Commando's 306 ADSR
emits, **154 are declared-lane `SELECT` reads** (the `$5591` bank, stride 8, AD at
`+3` and SR at `+4`, rows 1-7 per voice) and 152 are the `ad = sr = 0` release
immediate. Automatas' 400 are all immediates — one voice held silent every frame —
which is why the split is reported and not folded into one number.

Over the first 60 cached HVSC tunes (57 decompile; keyed by full relpath): a
pitch table is recovered for **53**, the law passes for **57/57**, and the
interpreted share is **23.1%** (39598/171586), from 22.1% (37890) before the
instrument lanes. Per plane: freq **70.1%** (37890/54067, unchanged), ad **10.3%**
(723/7019), sr **11.8%** (985/8325), ctrl and pw 0%. Of the 1708 ADSR emits,
**1473 are declared-lane reads** and 235 are program immediates.

## 7. Where the residual goes next

Refinement, in the order that shrinks the residual fastest — each step must keep
the law green and must move emits out of `RAW`, never widen a declaration:

1. **ctrl/gate** — the control automaton on the note-on edge the ADSR lanes
   already carry (the biggest residual plane on Commando).
2. **pw** — the pulse-width accumulator as a `RAMP` with bounds, and the seed
   lane of the instrument bank, which needs per-lane declaration soundness.
3. **Arpeggio and vibrato as generators, not notes** — a note-on carries one
   note; an arp step is a downstream generator emit on that edge, so it must
   never appear as a fresh row.
4. **Arrangement** — orderlist/pattern/transpose as `LOOKUP` nodes routed to
   `Fire`, with shared subgraphs for reuse and a back-edge for the loop. This is
   what replaces the `EDGE` floors and the recovered row streams: the row a
   note-on selects becomes an emit of the pattern generator, not observed data.
5. **Codec** — `parse(emit(t)) ≡ t`, as for the structurer and frameprog.

## 8. Known limits

- A pitch table whose lo/hi block is never read at a constant base cannot be
  declared, so it cannot be recovered here — the one tune in the 60-tune sample
  that loses its table, `MUSICIANS/A/Aegis/2008.sid`, has no read site at its hi
  block `$0E96` at all.
- Where a table's declared extent is bounded by the next read base or the index
  cap, an over-long reading of it is not available — the declaration is what the
  play code can index, and the tracker does not run past it.
- For tunes whose freq is streamed or glide-computed with no static table at
  all, `pitchind.py` induces the ET lattice from the *observed* freq stream and
  reports a note lane with its fit; that is a diagnostic, not part of this law,
  and it never feeds a generator.
- ADSR that reaches the register through RAM stays residual. Over the 60-tune
  sample the two refusal classes are the byte arriving from a cell outside every
  declaration (5324 emits — a play-written shadow register, or a const region the
  declarations do not cover) and the value arriving from an expression rather than
  a load (7643 emits). Following a shadow cell back to the bank is a dataflow
  step, not this one; reading it as constant anyway would break #61. Only 74
  emits are refused for write order.
