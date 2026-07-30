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
- **ctrl/AD/SR `SELECT` per voice** (§5) — the instrument lane: a declared bank
  lane read at a recovered row, fired by the voice's note-on `EDGE`. For `ctrl`
  the same lane also supplies the gate images, so the waveform is declared data
  and only bit 0 moves.
- **Clocks** (`_clocks`) — cells the play code steps by one, read off the frame
  program's procedures: `dec` + reload is a divider (its reload is
  `frames_per_tick`), a free `inc` is an LFO phase.
- **Instrument banks** (`_instruments`) — const table bases feeding a
  ctrl/AD/SR store.

`pw` is **not** interpreted: it renders from `RAW`, and so does every `ctrl`
write whose byte never reaches a declaration. The coverage numbers in §6 say so
plainly; nothing is claimed that a generator does not reproduce.

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

## 5. Instrument lanes: ctrl/AD/SR from a declared bank at a recovered row

ctrl and ADSR are written from an instrument bank: a declared const table of
stride `s`, one lane per byte offset. The generator for a lane is
`SELECT(lane, rows)` — the **declared bytes** of that lane, indexed by a
**recovered row** — fired by the voice's note-on `EDGE`.

- **Provenance, not proximity** (`frameval.eval_src`). The evaluator records, for
  every SID store, the cells the byte came from: every byte load at a *pure*
  address (consts, locals and ops — no memory read, so re-evaluating it consumes
  no volatile input and has no side effect) inside the value expression, each
  preceded by the cell that byte originated in, so a byte staged in a RAM
  register mirror arrives here as the bank cell it was copied from
  (docs/frameprog.md §1.4). A bare load reports one cell, `lane & mask` reports
  both and the declaration picks. That is the address the play code indexed, so
  the row is `(cell - base) // stride` and the lane is `(cell - base) % stride` —
  read off the machine, never guessed.
- **The declared byte must agree** (`_classify`). A write is a lane read only if
  the cell lies inside a declared table *and* `mem0[cell] == value`. A cell the
  play phase mutated therefore never passes as constant data (#61), and every
  byte a `SELECT` emits is verified equal to the declared byte at the moment of
  that emit. The check earns its keep: over the 60-tune sample 916 ADSR emits
  read a cell inside a declaration whose byte no longer matches the snapshot, and
  all 916 stay residual.
- **The gate rides the recovered waveform** (`_classify`, `_key_table`). A ctrl
  write carrying no declared cell of its own is the gate bit applied to the lane
  byte at the row the voice's last lane read established: the emitted byte is
  `lane[row]`, `lane[row] & ~gate` or `lane[row] | gate`. The ctrl `SELECT` table
  is therefore the declared lane followed by those three held readings of it, so a
  row past the lane length says both which byte and that the row came from the hold
  rather than from this emit's own provenance — which is exactly the `lane`/`gate`
  line in `classes`. Every byte emitted is still a declared byte, and a voice that
  never read its waveform from a declaration has no row to ride and stays residual.
- **Immediates** (`_immediates`, `_const_flow`). The other half of a typical note
  lane is the release write, an immediate operand in the play code (`ad = 0`), and
  the other half of a typical ctrl lane is the hard-restart byte a branch loads
  before the store. Those emits are `LOOKUP((c,))` for a constant `c` the program
  text stores to that register class (`reg % 7`, since one voice-generic store site
  serves all three voices behind a dynamic offset), reached directly or through a
  local. The value comes from the program, not from the observation; `Coverage.classes`
  keeps `lane`/`gate` (declared bytes at a recovered index) apart from `imm`.
- **All or nothing per register** (`_instr_streams`). A refined register is
  removed from `RAW`, so *every* write to it must be explained or the register
  stays residual whole. Per voice the explainable subset of `{ctrl, ad, sr}`
  covering the most emits wins.
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

`Coverage(interp, residual, total, planes, classes)` is the one partition type:
emits produced by an interpreted generator vs emits replayed from `RAW`, the
per-plane split, and per plane the evidence behind each interpreted emit —
`lane` and `gate` are declared bytes at a recovered index (**strong**), `imm` is
a program constant that passes the law without explaining an index (**shallow**,
never folded into a strong figure).

| tune | pitch table | interpreted | freq plane | ctrl | ad | sr |
|---|---|---|---|---|---|---|
| Commando (Hubbard), 300 frames | `$5428` interleaved, 97 words | **2080/2525 = 82.4%** | 1198/1280 = 93.6% | 576/576 | 153/153 | 153/153 |
| Ghouls_n_Ghosts (Follin) | `$6D35`/`$6D96` split, 97 | 444/1164 = 38.1% | 444/722 = 61.5% | 0/29 | 0/7 | 0/4 |
| Automatas (Goto80/DefMON) | `$1578`/`$1614` split, 120 | 998/4800 = 20.8% | 398/1200 = 33.2% | 200/600 | 200/600 | 200/600 |
| Athena (Galway) | `$C517`/`$C55F` split, 72 | 426/1882 = 22.6% | 426/1200 = 35.5% | 0/48 | 0/17 | 0/17 |
| Krakout (Daglish) | `$E629` big-endian, 12, octave-shift | 228/5000 = 4.6% | 228/1200 = 19.0% | 0/600 | 0/600 | 0/600 |

Of Commando's 576 ctrl emits, **75 are declared-lane reads** and **399 are that
lane with the gate bit cleared** — 474 strong, all from the `$5591` bank's
waveform lane at `+2`, the same row its AD (`+3`) and SR (`+4`) lanes read — and
102 are the `$80` hard-restart immediate. Its 306 ADSR emits are 154 lane reads
and 152 `ad = sr = 0` release immediates. Automatas' 600 refined emits are *all*
immediates (`ctrl = ad = sr = 0`, one voice held silent every frame), which is
why the split is reported and never folded into one number.

Over the first 60 cached HVSC tunes (57 decompile; keyed by full relpath) nothing
moves: a pitch table is recovered for **53**, the law passes for **57/57**, and
the interpreted share stays **23.1%** (39598/171586) with ctrl at 0/30025 — no
voice in that prefix reads its waveform from a declaration.

Over all 682 cached tunes (646 decompile), against the same tree before this
step: the law passes for **646/646** either way, a pitch table is recovered for
**582** either way, and the interpreted share goes 21.0% (406674/1936217) to
**21.5%** (416294/1936217). Per plane: freq 63.1% (unchanged), ctrl **0 → 7993**
of 301790 (2.6%), ad 10758 → 11719 of 112169, sr 14404 → 15070 of 116108. The
ctrl gain is **4615 strong** (2907 lane, 1708 gate) and 3378 immediates; 49 tunes
refine ctrl at all, 25 of them on strong evidence and 24 on immediates alone
(2776 emits, which is why the split is reported rather than a single figure).

Over all 682 cached tunes again, each at its PSID start subtune (623 reach the
gate; the denominators differ from the paragraph above, which starts every tune
at subtune 0), against the same tree before frameprog reported a store's
**origin** cell (docs/frameprog.md §1.4) — a change to frameprog only, with
`tracker.py` untouched:

| plane | before | after |
|---|---|---|
| interpreted | 408262/1895893 = 21.53% | **419595/1895893 = 22.13%** |
| freq | 373048/587722 | 373048/587722 (unchanged) |
| ctrl | 7796/295884 = 2.63% | **10558/295884 = 3.57%** |
| ad | 12338/109997 = 11.22% | **16887/109997 = 15.35%** |
| sr | 15080/113411 = 13.30% | **19102/113411 = 16.84%** |

Strong evidence carries the gain: declared-lane `SELECT` emits go 23303 → 34518
while program immediates barely move (11911 → 12029), and the ctrl `SELECT`
split goes 2723 → 4351 lane rows and 1683 → 2803 gate rows. 86 tunes improve and
**none regress**; 68 more tunes have their whole `ad` plane explained, 11 more
their whole `sr` plane and 3 more their whole `ctrl` plane. Gate FP holds
623/623 and the tracker law 623/623, both unchanged.

## 7. Where the residual goes next

Refinement, in the order that shrinks the residual fastest — each step must keep
the law green and must move emits out of `RAW`, never widen a declaration:

1. **pw** — the pulse-width accumulator as a `RAMP` with bounds, and the seed
   lane of the instrument bank, which needs per-lane declaration soundness.
2. **Arpeggio and vibrato as generators, not notes** — a note-on carries one
   note; an arp step is a downstream generator emit on that edge, so it must
   never appear as a fresh row.
3. **Arrangement** — orderlist/pattern/transpose as `LOOKUP` nodes routed to
   `Fire`, with shared subgraphs for reuse and a back-edge for the loop. This is
   what replaces the `EDGE` floors and the recovered row streams: the row a
   note-on selects becomes an emit of the pattern generator, not observed data.
4. **Codec** — `parse(emit(t)) ≡ t`, as for the structurer and frameprog.

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
- ctrl/ADSR that reaches the register through a **computed** value stays
  residual: the byte arrives from an expression rather than a load, so there is
  no cell to name. Over the 60-tune sample that is 7643 ADSR emits, and over the
  same sample 15957 of the 30025 ctrl emits. Staging in a RAM register mirror is
  no longer one of the refusal classes — following the copy back to the bank is a
  dataflow step and it is frameprog's, so a mirror-staged bank read arrives here
  already declared (docs/frameprog.md §1.4). What still refuses is a byte whose
  origin is outside every declaration, or inside one but no longer equal to the
  snapshot (#61), plus 74 emits refused for write order.
- The gate arm needs a row: a voice that never reads its waveform from a
  declaration has nothing for the gate to ride and stays residual whole.
