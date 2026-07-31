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
- **freq/pw `SELECT` per register** (§4b) — the last-write-wins planes,
  transliterated from the store statement: where the value expression names a
  declared table, the emit is that table's lane at the row the read cell recovers.
- **pw `RAMP` per accumulator** (§4c) — the pulse sweep. Where the pw store reads
  a cell the play code steps by a declared byte, the sweep is generated from that
  byte: one observed seed, then every further emit predicted.
- **Clocks** (`_clocks`) — cells the play code steps by one, read off the frame
  program's procedures: `dec` + reload is a divider (its reload is
  `frames_per_tick`), a free `inc` is an LFO phase.
- **Instrument banks** (`_instruments`) — const table bases feeding a
  ctrl/AD/SR store.

Every `ctrl` write whose byte never reaches a declaration renders from `RAW`, and
so does every freq/pw write whose store statement names none. The coverage numbers
in §6 say so plainly; nothing is claimed that a generator does not reproduce.

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
only where the play phase never writes. That invariant lives in `datadecl`, not
compensated for here — which is why the tracker has no `_extend_et`, no `mem0`
scan and no per-entry stability ranking. The extent stops at the first
play-written cell above the observed run (`_sound_hi`) and the writes inside it
are named per record offset by `mut` (`_mut_offs`): a lane of a record array, a
cell of a flat region. The tracker **consumes** `mut` (`_lane_key`): a source cell
at one of those offsets is refused, because a play-written cell is not const data
and agreement with the snapshot is then coincidence rather than a const read. The
reading is per record — `(cell − base) mod stride` when strided, `(cell − base)`
otherwise — and getting it wrong silently matches nothing, so it is asserted
directly (`test_mut_offsets_are_lanes_when_strided_and_cells_when_flat`). Refusing
those srcs costs 2834 interpreted emits on the instrument planes (`sr` −1512,
`ctrl` −954, `ad` −368) and 5926 on the lww planes (`pw` −5322, `freq` −604);
§6 reports that cost apart from the gain it is paid out of.

## 4b. The last-write-wins planes: the table the store statement names

freq and pw are last-write-wins, so a frame's value stands on its own and there is
no all-or-nothing register rule to lean on. What replaces it is the **statement
tree**: `_tree_tables` reads, per register class, the declarations the program text
stores into that class, and only those are eligible.

- **The store names the table.** A store's value expression is walked with its
  locals resolved to their in-procedure definitions and a staged byte followed
  through the cell it was stored to (`_read_bases`) — the same origin hop
  `frameval` makes at runtime (docs/frameprog.md §1.4), made statically. A read the
  value only *indexes through* is not followed: an index cell is not the byte's
  origin, however its byte agrees.
- **The read cell recovers the row.** The tree names the declaration; it cannot
  evaluate the index, so `frameval.eval_src` still supplies the cell and the row is
  `(cell − base) // stride` as in §5. The emitted byte is the declared one.
- **Per frame, not per register** (`_lww_streams`). Each explained frame fires the
  lane's `EDGE`; the rest fall through to the note lane (§4) or stay in `RAW`. A
  declared lane outranks the note reading of the same byte, since the note reading
  emits the *observed* word and the lane emits a *declared* one.

On Commando `sid.v1.freq_hi[w9] = m_5429[t5]` and its `freq_lo` partner become two
`SELECT` nodes over the `+1` and `+0` lanes of the declared `m_5428[192] stride 2`
table, carrying **one shared row stream per voice** — the row is the semitone index,
so the pitch table and the note lane are finally the same object. `pw_hi` is the
`+1` lane of the `$5591` instrument bank at the instrument row; `pw_lo` is `mut`
(the play code writes the pulse accumulator back) and is refused.

Requiring the tree to name the table is stricter than a bare provenance search over
every declaration, and measurably so: over the corpus it declined 5693 emits' worth
of lane classifications a blind search would take. That is the point — a byte whose
declaration the program text never names is not explained by that declaration.

## 4c. The pulse sweep: a `RAMP` whose step is a declared byte

PWM writes pulse width every frame, which is why `pw` is the largest single
residual. The sweep is a bounded accumulator, and `RAMP(seed, step, bound)` is the
primitive for it — but a `pw` register is last-write-wins, so a generator that
merely reproduces the end-of-frame value passes the law without explaining
anything. **The claim here is about provenance, not about matching values**: the
step must be a byte the declarations hold and the statement tree names.

- **The store statement names the accumulator** (`_accumulators`). A non-SID store
  whose value adds (or subtracts) one term to a read of *its own cell* is an
  accumulator; a pw store whose value reaches that cell is the sweep. Two
  accumulators reaching one store refuse it.
- **The step is a declared byte** (`_step_site`). The accumulator's other addend is
  walked with locals resolved and staged bytes followed (`_read_bases`, §4b), and
  must reach exactly one declared cell at an offset the declaration does not name
  `mut` — `("fix", cell)` for a flat declaration, `("lane", decl, off)` for a
  strided one, read at the row the voice holds (`_hold_rows`, the same recovered row
  §5 uses). Ambiguity refuses; a step read at a `mut` offset refuses, because a
  play-written cell is runtime state and not a parameter.
- **`bound` is the store width, not a fit**: the accumulator is a byte cell, so the
  bound is `$100` and the wrap is the register's own.
- **The seed is observed and reported as such.** The accumulator's value is state
  it produces, not data it reads — on Commando its lane is exactly the one `mut`
  offset of the `$5591` bank. One run per step cell takes its seed from the run's
  first emit and predicts the rest; a run that predicts nothing (one emit, or a
  declared step of zero) is refused, and `Coverage.classes` keeps `seed` apart from
  `ramp` so the generated figure never absorbs the observed byte.
- **The evaluator says which writes step.** A pw write `frameval.eval_src` reports
  no source cell for is the accumulator store — its value was computed, not loaded
  — and those are the frames the `RAMP` fires on, one step each. A write that
  loads a cell is §4b's business. Nothing about the run is fitted: given the seed
  and the declared step the whole stream is determined, and a run that does not
  regenerate byte-for-byte is refused whole.

On Commando `m_5591[idx_5518] = (m_5591[idx_5518] + idx_5507 + cflag)` feeding
`sid.v1.pw_lo` is the sweep; `idx_5507` stages `m_5597[t1]`, the `+6` lane of the
declared `$5591` bank, so the step is that lane at the instrument row. Perturbing
the declared byte at `$55A7` from `$16` to `$07` to `$21` moves the `RAMP` node's
step field and the whole emitted stream with it (`80 96 AC C2 …` → `80 87 8E 95 …`
→ `80 A1 C2 E3 …`), law green throughout: the sweep is generated, not replayed.

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
- **The declared byte must agree** (`_lane_key`). A write is a lane read only if
  the cell lies inside a declared table, at an offset the declaration does not
  name `mut`, *and* `mem0[cell] == value`. A cell the play phase mutated therefore
  never passes as constant data (#61, #78), and every byte a `SELECT` emits is
  verified equal to the declared byte at the moment of that emit. The check earns
  its keep: over the 60-tune sample 916 ADSR emits read a cell inside a declaration
  whose byte no longer matches the snapshot, and all 916 stay residual.
- **The tree first, the search behind it** (`_classify`). The declarations the
  store site names for this register class (§4b) are tried first; the search over
  every declared bank is the fallback for a value the tree cannot express — a byte
  staged across a procedure boundary or through the stack, which `frameval`'s
  program-wide locals carry but a per-procedure walk of the tree does not. Dropping
  the fallback would cost 15648 emits on these planes (measured before the origin
  rule of §6), so it stays and is named.
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

### Where the tracker stands

Whole cached corpus, PSID start subtune, 200 frames: **682 tunes cached, 646
decompile**, and of those 646 the **tracker law passes 646/646** and a pitch table
is recovered for **582**. The 36 that do not decompile never reach this layer (10
`play $0000` with no interrupt vector installed, 5 init runaways, 3 unmodelled
`brk`, 3 pinned-trace faults, and the remainder assorted). This is the current
state, not a delta; the tables after it record how it was reached.

| plane | interpreted | of | share | strong | shallow |
|---|---|---|---|---|---|
| freq | 414066 | 602528 | 68.7% | 414066 | 0 |
| pw | 63232 | 561585 | 11.3% | 63231 | 1 |
| ctrl | 42789 | 300573 | 14.2% | 38943 | 3846 |
| sr | 19373 | 115963 | 16.7% | 14554 | 4819 |
| ad | 17592 | 112534 | 15.6% | 13642 | 3950 |
| filter | 0 | 240694 | 0% | 0 | 0 |
| **all** | **557052** | **1933877** | **28.80%** | **544436** | **12616** |

**97.7% of interpreted emits rest on strong evidence** — a declared byte at a
recovered row, or generated from one. The shallow 12616 are program immediates
(`ctrl`/`ad`/`sr` releases and hard-restarts) plus the one observed seed the
surviving pw sweep starts from; they pass the law without explaining an index and
are never folded into a strong figure. The freq plane splits 147002 pitch-table
`LOOKUP` emits (the note lane, §4) and 267064 declared-lane `SELECT` emits (§4b);
`pw`'s 63232 splits 63093 declared-lane reads and **138 generated by a sweep
`RAMP`** (§4c). `filter` is untouched at this layer — every filter write is still
`RAW`.

The three planes that dominate the residual are `pw` (498353 emits), `ctrl`
(257784) and `filter` (240694). They are no longer one problem: `pw` and `ctrl`
are now bounded by what the declarations can name at all, while `filter`'s
declared-byte ceiling is 27785 of 240694 (11.5%) — most of a filter write's byte
is not a declared-table read, so no provenance step reaches it and §7.1 is not
the change for it. `ctrl` still needs a row for the gate to ride, and a voice
that never reads its waveform from a declaration has none (§8).

| tune | pitch table | interpreted | freq plane | pw | ctrl | ad | sr |
|---|---|---|---|---|---|---|---|
| Commando (Hubbard), 300 frames | `$5428` interleaved, 97 words | **2366/2525 = 93.7%** | 1198/1280 = 93.6% | 286/363 | 576/576 | 153/153 | 153/153 |
| Ghouls_n_Ghosts (Follin) | `$6D35`/`$6D96` split, 97 | 485/1164 = 41.7% | 485/722 = 67.2% | 0/0 | 0/29 | 0/7 | 0/4 |
| Automatas (Goto80/DefMON) | `$1578`/`$1614` split, 120 | 1396/4800 = 29.1% | 796/1200 = 66.3% | 0/1200 | 200/600 | 200/600 | 200/600 |
| Athena (Galway) | `$C517`/`$C55F` split, 72 | 426/1882 = 22.6% | 426/1200 = 35.5% | 0/600 | 0/48 | 0/17 | 0/17 |
| Krakout (Daglish) | `$E629` big-endian, 12, octave-shift | 228/5000 = 4.6% | 228/1200 = 19.0% | 0/1200 | 0/600 | 0/600 | 0/600 |

Of Commando's 576 ctrl emits, **75 are declared-lane reads** and **399 are that
lane with the gate bit cleared** — 474 strong, all from the `$5591` bank's
waveform lane at `+2`, the same row its AD (`+3`) and SR (`+4`) lanes read — and
102 are the `$80` hard-restart immediate. Its 306 ADSR emits are 154 lane reads
and 152 `ad = sr = 0` release immediates, its 286 pw emits 77 reads of the `+1`
lane of the same bank plus **208 generated by the sweep `RAMP` from one seed and
the `+6` lane's step** (§4c), and **all 1198 of its freq emits are the declared
`$5428` pitch lanes at a recovered row** rather than observed words matched to an
ET table.
Automatas' 600 refined instrument emits are *all* immediates (`ctrl = ad = sr = 0`,
one voice held silent every frame) while its 597 new freq emits are all declared
lanes, which is why the split is reported and never folded into one number.

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

frameprog's indexed-access rendering (docs/frameprog.md §4.2, which cuts raw
`mem[expr]` by 31% corpus-wide) moves **nothing** here, and that is the record:
every plane is byte-identical before and after. Coverage at this layer is a
function of the declarations and of `frameval.eval_src`'s store provenance, not
of how an address is written; the remaining `ctrl`/`ad`/`sr` residual is 40%
writes whose value reaches the store through a local (no source cell at all) and
30% whose source cell falls outside every declaration. Naming an address better
answers neither.

### Reading the statement tree, and consuming `mut`

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile). Two
independent effects, reported apart because they pull opposite ways: transliterating
the last-write-wins planes off the store statement (§4b) **gains**, and refusing
source cells at a `mut` offset (§4) **costs**. The middle two columns are each
effect on its own; the last is the shipped combination.

| plane | before | tree only | `mut` only | shipped |
|---|---|---|---|---|
| interpreted | 425657/1933877 = 22.01% | 467904 = 24.20% | 422823 = 21.86% | **459144 = 23.74%** |
| freq | 379104/602528 | 395868 | 379104 | **395264** |
| pw | 0/561585 | 25483 | 0 | **20161** |
| ctrl | 10558/300573 | 10558 | 9604 | **9604** |
| ad | 16890/112534 | 16890 | 16522 | **16522** |
| sr | 19105/115963 | 19105 | 17593 | **17593** |

The tree gain is **+42247** emits, every one of them `lane` class — declared bytes
at a recovered row, no immediates — over 330 tunes that grow a declared lww lane.
The `mut` refusal costs **−2834** on the instrument planes (exactly the residue
#78 measured from the declaration side: `sr` −1512, `ctrl` −954, `ad` −368) and a
further **−5926** on the planes the tree gain opened (`pw` −5322, `freq` −604).
Net **+33487**, 167 tunes improve, 6 regress (all six purely from the refusal).
Whole planes explained: `pw` 0 → 6 tunes, `freq` 106 → 113; `ctrl` 28 → 25, `ad`
180 → 177, `sr` 195 → 192, the losses again the refusal's. The canonical fixpoint
holds 646/646, Gate FP 646/646 and the tracker law 646/646 — all three unchanged.

`eval_src` is **not** removed and cannot be: the tree names the declaration but
cannot evaluate the index, so the read cell is still what recovers the row. What it
replaces is *identification*. Of the corpus's 8538 SID store sites, 5457 (63.9%)
name a declared table in the tree — 3223 of the 4526 freq/pw sites (71.2%) — and on
those planes that naming is now the only admissible basis.

### The pulse sweep, and how little of it is declared

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile),
against the table above. The sweep `RAMP` (§4c) moves `pw` **20161 → 20452** of
561585 (3.59% → 3.64%) and the interpreted share 459144 → **459435** of 1933877
(23.74% → 23.76%). Every new emit is a sweep emit: `ramp` **+281**, `seed` +10, and
`pw`'s `lane` count does not move. **No other plane changes by a single emit**, no
tune loses an interpreted emit, 3 tunes improve and none regress. The canonical
fixpoint, Gate FP and the tracker law are all unchanged.

That gain is small on purpose, and the measurement says why. Of the 561585 pw
emits, **158718 (28%) sit in a constant-nonzero-delta run of two or more** — the
plane really is accumulator-driven. But the accumulator's *parameters* are almost
never declared data the tree can name:

| where the pw store's byte comes from | emits |
|---|---|
| a cell outside every declaration (a RAM accumulator) | 329792 |
| a computed value, no source cell at all | 181504 |
| a declared lane, byte agreeing (§4b) | 21083 |
| a declared cell at a `mut` offset (the accumulator's own lane) | 17728 |

183 tunes have a pw store reading a cell the play code steps, but only 3 have a
step the declarations hold: the rest step by a **RAM cell the play code copied from
a table at note-on**, and so do their bounds and their rate. Ahti_01's sweep is a
textbook 16-bit triangle — `m_6846[x] ± m_684C[x]`, turning around at `m_6852[x]`
and `m_684F[x]` — with all four parameters in per-voice RAM. An oracle that is
*allowed to fit*, taking any declared byte at the voice's held row that equals the
observed delta, still reaches only 7480 emits, because the held row itself is rare
(`ctrl` is 3.2% declared corpus-wide). So the ceiling for this step is ~1.3% of the
plane and the honest yield is 0.05%; the rest is not a `RAMP` problem.

What would move it is a frameprog change, not a tracker one: `eval_src` reports
origins for **SID** stores only (docs/frameprog.md §1.4), so a parameter staged in
RAM arrives here as an unnameable cell. Reporting the origin of a named non-SID
store site would make those steps, bounds and rates declared bytes at recovered
rows, and the accumulator would then be recoverable wherever the tree finds it.

### The origin of a byte staged in a register

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile),
against the table above, and again a change to **frameprog only** with
`tracker.py` untouched: `frameval`'s cell → origin map now carries through the
**locals**, not only through the cells (docs/frameprog.md §1.4). A driver that
loads a bank byte into a register and stores it a statement later showed the old
rule no load at all inside the store's value expression, so the origin was
dropped exactly where the staging happened.

| plane | before | after |
|---|---|---|
| interpreted | 459435/1933877 = 23.76% | **557052/1933877 = 28.80%** |
| freq | 395264/602528 | **414066** |
| pw | 20452/561585 | **63232** |
| ctrl | 9604/300573 | **42789** |
| sr | 17593/115963 | **19373** |
| ad | 16522/112534 | **17592** |

**337 tunes improve and 1 regresses.** The gain is strong evidence, not
immediates: `lane` 152262 → 386688 and `gate` 2688 → 10608, while `imm` moves
11899 → 12615. Whole planes newly explained: `ad` +37 tunes, `ctrl` +27, `sr`
+21, `pw` +14, `freq` +9. The canonical fixpoint, Gate FP and the tracker law all
hold 646/646, unchanged — the rule annotates and cannot disturb a value, a write
or a record.

Two costs, both named rather than folded away. `MUSICIANS/F/Fanta/15_Years_Oxyron`
loses 42 freq emits: an added origin cell displaces the lane its lww classification
picked, the mis-bind docs/frameprog.md §4.2 measured from the other side. And the
sweep `RAMP` goes 281 emits → 138 with observed seeds 10 → 1, because two of the
three sweeps were never accumulators: with the register hop visible, Cool_Intro's
and Aha's pw writes report a declared source cell, so §4c stands aside and §4b
claims them as declared lanes — pw on those two tunes goes 80 → 239 and 72 → 348.
Only Commando's sweep is a genuinely computed accumulator, and §4c's test (the
evaluator reports no source cell) is sharper for it, not weaker.

What the change does **not** do is raise the ceiling to the plane totals. Emits
whose source tuple names a declared byte at a non-`mut` offset — the ceiling the
interpreted figure is drawn from, before any all-or-nothing rule — go freq 110833
→ 289125, pw 21085 → 87828, ctrl 20915 → 72778, sr 40098 → 51702, ad 43718 →
52300, filter 15571 → 27785. `ad` and `sr` are the gap to read: ~46% of their
emits now name a declared byte while ~16% are interpreted, so what holds them back
is the tracker's own all-or-nothing-per-register rule (§5) and its order check,
not provenance. `filter` at 11.5% is the opposite reading: even named perfectly,
most filter writes are not declared-table reads.

## 7. Where the residual goes next

Refinement, in the order that shrinks the residual fastest — each step must keep
the law green and must move emits out of `RAW`, never widen a declaration:

The order has changed with the origin rule of §6: the largest measured gains
available now are **tracker-side**, realizing a ceiling provenance already
supplies, rather than raising that ceiling further.

1. **Realize the ceiling on the instrument planes** — `ad` and `sr` name a
   declared byte for 52300/112534 and 51702/115963 emits and interpret 17592 and
   19373 of them; `ctrl` names 72778/300573 and interprets 42789. The ~67000-emit
   `ad`/`sr` gap is not provenance: it is the all-or-nothing-per-register rule
   (§5, a refined register is removed from `RAW`, so one unexplained write costs
   the whole register) and the order check on top of it. Both exist so the law
   cannot rest on a lucky interleaving, and neither may be relaxed — what is
   available is a **finer partition**: a register split across a typed stream and
   a residual stream that between them still reproduce every write in order. The
   floor is explicit either way, so nothing is claimed that a generator does not
   emit; the measurement to make first is how many of those ~67000 sit in
   registers that are whole but for a handful of writes.
2. **Parameters staged in RAM, queried rather than reported** — the sweep is a
   `RAMP` wherever its step is declared (§4c), and Commando's is the only one in
   the corpus. Step, bounds and rate are copied out of a table into RAM at
   note-on, and no SID store ever reads those cells, so the origin rule of §6 —
   which reports origins *alongside SID store sources* — cannot name them however
   well the map is maintained. What is missing is a **query**: exposing the map
   for an arbitrary cell lets the tracker ask what an accumulator's step cell
   traces back to, and makes RAM-staged steps, bounds and rates declared bytes at
   recovered rows.

   **Measure before building.** The step that shipped §4c returned 291 emits
   against a fitting ceiling of 7480, and §6 has now moved the baseline it would
   be measured against — pw's declared-byte ceiling went 21085 → 87828 of 561585
   while its interpreted figure went 20452 → 63232, so the accumulator population
   must be re-counted before any generator work. `filter` is **not** part of this
   step: its ceiling is 27785 of 240694 even with origins carried (§6), so most
   filter writes are not declared-table reads and no provenance change reaches
   them; a filter generator is its own step, with its own measurement.
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
  no cell to name. Over the 60-tune sample that was 7643 ADSR emits and 15957 of
  30025 ctrl emits, measured before the origin rule of §6. Neither staging in a
  RAM register mirror nor staging in a register is a refusal class any more —
  following the copy back to the bank is a dataflow step and it is frameprog's,
  so a staged bank read arrives here already declared (docs/frameprog.md §1.4).
  What still refuses is a byte whose origin is outside every declaration, or
  inside one but no longer equal to the snapshot (#61), plus the emits refused
  for write order.
- The gate arm needs a row: a voice that never reads its waveform from a
  declaration has nothing for the gate to ride and stays residual whole.
- A sweep whose step is a RAM cell stays residual, and that is most of them (§6):
  the `RAMP` is refused rather than seeded from an observed delta, since a step
  fitted to the output would pass the law while explaining nothing. A `RAMP` whose
  run predicts no emit — one write, or a declared step of zero — is refused for the
  same reason. Only the wrapping bound is implemented; a triangle sweep that turns
  around at a declared bound needs a transfer this primitive does not have, and no
  tune in the corpus reaches that limit before the step blocks it.
- On the lww planes the *table* is transliterated but the *index* is not: the tree
  names `m_5429[t5]`, and `t5` is a live state value no static reading yields, so
  `frameval.eval_src` still recovers it. That index becomes explained when the
  arrangement does (§7.3), not before.
- The tree walk is per procedure (locals) plus a program-wide staging hop
  (`origins`), so a byte staged across a procedure boundary or through the stack is
  named by no store site. On ctrl/AD/SR the provenance search covers it — 15648
  emits' worth; on freq/pw there is no such fallback and those writes stay residual,
  which is 5693 emits a blind search would have taken.
