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
  coverage. Refinement *moves emits out of RAW* into typed generators; a refined
  **write** is removed from RAW, so a register may be split across the two and the
  interleaving is fixed by node order — constructed and checked per frame, never
  assumed (§5).
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
transfer, and an `EDGE` stream is replaced by `DIV` where a divider generates it
(§4d) or by the arrangement (§7.4) where one does not. Both are explicit, and
**both are counted**: `Coverage` reports the two domains as two numbers and never
sums them, so neither can hide behind the other (§6).

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
- **freq/pw/filter `SELECT` per register** (§4b) — the last-write-wins planes,
  transliterated from the store statement: where the value expression names a
  declared table, the emit is that table's lane at the row the read cell recovers.
  $15-$18 are one global filter, so they take a register class of their own.
- **pw/cutoff `RAMP` per accumulator** (§4c) — the sweep. Where the store reads a cell
  the play code steps, the origin map is *queried* for where that step byte was copied
  from; where it was copied from a declaration the sweep is generated from that byte —
  one observed seed per run, then every further emit predicted.
- **Clocks** (`_clocks`) — cells the play code steps by one, read off the frame
  program's procedures: `dec` + reload is a divider (its reload is
  `frames_per_tick`), a free `inc` is an LFO phase.
- **`DIV` over a declared divisor** (§4d) — the trigger floor's one refinement: an
  `EDGE` stream a recovered divider's own reload generates becomes a `DIV`.
- **Instrument banks** (`_instruments`) — const table bases feeding a
  ctrl/AD/SR store.

Every `ctrl` write whose byte never reaches a declaration renders from `RAW`, and
so does every freq/pw/filter write whose store statement names none. The coverage numbers
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

freq, pw and the $15-$18 filter tail are last-write-wins, so a frame's value stands
on its own and there is no ordered section to place a stream against. What carries
the claim is the **statement tree**: `_tree_tables` reads, per register class, the
declarations the program text stores into that class, and only those are eligible.

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
- **The filter is its own register class** (`_class_of`, `_sid_class`). A voice store
  site is keyed by `reg % 7`, because one voice-generic site serves all three voices;
  $15-$18 are one global filter and that key would alias them onto freq_lo/freq_hi/
  pw_lo/pw_hi, so a table a pw store names would explain a resonance write. They take
  a class of their own instead. Nothing else about the plane differs —
  `framelog.canonical` already projects $15-$18 last-write-wins in a section of their
  own (docs/frameprog.md §1.1) — so the same `_lww_streams` reads them. The held rows
  of §5 stay per voice and skip them; the sweep of §4c does not, because $15/$16 is an
  accumulator in exactly the way pw is, and it reads them under their own class.

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

## 4c. The pulse sweep: a `RAMP` whose step the origin map names

PWM writes pulse width every frame and a swept cutoff writes $15/$16 every frame,
which is why `pw` and `filter` are the two largest residuals. Both are bounded
accumulators and `RAMP(seed, step, bound)` is the primitive for them — but these are
last-write-wins registers, so a generator that merely reproduces the end-of-frame
value passes the law without explaining anything. **The claim here is about
provenance, not about matching values**: the step must be a byte a declaration holds,
and what says which byte is the machine, not a static reading of the tree.

- **The store statement names the accumulator** (`_acc_sites`, `_accumulators`). A
  non-SID store whose value adds (or subtracts) one term to a read of *its own cell*
  is an accumulator; a pw or cutoff store whose value reaches exactly one such cell
  is that accumulator's output. Two accumulators reaching one store refuse it.
- **The step is queried, not walked** (`frameval.eval_watch`, docs/frameprog.md §1.4).
  Step, bound and rate are copied out of a table into RAM at note-on and **no SID
  store ever reads those cells**, so the origin rule of §6 — which reports origins
  alongside SID store sources — cannot name them however well the map is kept.
  `_acc_sites` hands the evaluator the statement the arithmetic happens in (the store,
  or the assignment its value resolves through, since a store of a bare local carries
  no origin at all) and `_acc_pools` reads the origin off that statement's own
  execution: the cells its byte derives from, less the cell it wrote.
- **Per execution, and that is a third of the figure** (`_acc_pools`). A staging cell
  is re-staged mid-run — a new note-on copies a new step — and one statement serves
  three voices inside a frame, so a snapshot of the map names the last row written
  rather than the row each read used: 27246 pw emits identified per execution against
  17324 per frame and 17111 at end of run.
- **The run is the observation's, the step is the declaration's** (`_runs`,
  `_acc_streams`). A maximal constant-nonzero-delta run of the register's own emits is
  the candidate, and it is kept only where **every** stepped emit's accumulator
  execution reports an origin inside a declaration, at an offset the declaration does
  not name `mut`, whose snapshot byte equals that step. A step fitted to the output
  would pass the law while explaining nothing; the declared byte is what refuses it.
- **Regenerate or refuse whole.** Given the seed and the step the whole run is
  determined, so one stepped emit whose origin no declaration names refuses the run
  entire and not that emit. A run that predicts nothing — a single emit, or a step of
  zero — is refused for the reason `DIV(1)` is (§4d).
- **`bound` is the store width, not a fit**: the accumulator is a byte cell, so the
  bound is `$100` and the wrap is the register's own.
- **The seed is observed and reported as such.** The accumulator's value is state it
  produces, not data it reads. Each run takes its seed from its own first emit and
  predicts the rest, so a re-staged step starts a new run at a new observed seed;
  `Coverage.classes` keeps `seed` apart from `ramp` so the generated figure never
  absorbs the observed byte.
- **A declared lane outranks the sweep** (§4b). An emit whose own source cell reads a
  declared lane is that lane's emit and is not a candidate for a run at all.

**The static reading is replaced, not kept beside it.** Until this step the step byte
was resolved off the tree: the accumulator's other addend had to reach exactly one
declared cell, read at the row the voice last held (the same recovered row §5 uses).
The two rules are not nested — a held row can name a lane the staged byte did not come
from — so they compose by replacement, and the cost is measured from both sides.
Allowed to fit (any declared byte at the held row equal to the observed delta) the
static reading reaches **1420 of 237610** constant-delta pw emits over 13 tunes and
**0 of 46369** cutoff emits, against the query's 27246 over 121 tunes and 3353; on two
tunes it reaches more than the query does — `MUSICIANS/D/Diamond/Butterfly_2` 137
against 0 and `MUSICIANS/A/Amaze/Foolish_Maniacs` 200 against 116. Neither was ever
shipped: the static rule as it stood generated a sweep on **one tune of 646**,
Commando's, and the query generates that one better (139 emits over 13 runs against
138 over one). So the replacement costs **no realized emit on any tune**, and what it
declines is 221 emits of a held-row reading on two. That is a selection between
*admissible* explanations rather than a fit to the output: both readings name a
declared byte, but the held row is one *another* read established while the query is
the copy the machine actually made, and where they disagree the machine is right by
construction.

On Commando `m_5591[idx_5518] = (m_5591[idx_5518] + idx_5507 + cflag)` feeding
`sid.v1.pw_lo` is the sweep, and the query resolves `idx_5507` to `m_5597[t1]`, the
`+6` lane of the declared `$5591` bank. Perturbing the declared byte at `$55A7` moves
the `RAMP` node's step field and the whole emitted stream with it, law green
throughout: the sweep is generated, not replayed. tests/test_tracker.py drives the
same evidence hermetically — a step staged in RAM from a declared byte, an identical
stream staged from an undeclared cell refused, a step at a `mut` offset refused, a
zero step and a single-emit run refused, a run one undeclared origin refuses whole,
and a wrong step and a wrong seed each failing the law.

## 4d. The trigger domain: a `DIV` whose divisor is a declared reload

`EDGE` is the trigger floor and `DIV(n)` is the one transfer that can lift a stream
off it. The rule is §4c's, applied to triggers: **the divisor is program text, never
a period fitted to the fire pattern.** A period read off the output would pass the
law while claiming a structure the code does not have — the law cannot tell the two
apart, so the provenance rule must.

- **The divisor is what the play code reloads** (`_divisors`). A recovered divider
  (`_clocks`: a cell the play code steps down) is reloaded either with an immediate
  of the program text or from a declared byte at an offset the declaration does not
  name `mut`. Nothing else is a divisor: a reload out of a RAM cell is runtime state,
  and its post-init byte agreeing with an observed period is coincidence, exactly as
  in §4/#61.
- **A divisor of one is refused.** `DIV(1)` divides nothing — it is the root frame
  clock — so it would "explain" every stream that fires on consecutive frames with a
  byte that only happens to be `1`. That is the same refusal §4c makes of a `RAMP`
  whose declared step is zero: a generator that predicts nothing is not one.
- **The phase is the primitive's, not a parameter** (`_generates`). `DIV(n)` fires at
  frames `n-1, 2n-1, …` — a counter loaded with its own reload and stepped down. The
  whole stream must match in both directions: a missing tick refuses as loudly as a
  spare one. Adding a phase field would buy a per-stream parameter, and §6 measures
  that it would buy almost nothing besides.
- **The law does the verification.** A `DIV` node replaces an `EDGE` in place, so its
  downstream `SELECT`s fire on exactly the frames the divisor says; a wrong divisor
  moves every emit after the first and `tracker.gate` fails
  (`test_mutation_a_wrong_divisor_is_detected`).

§6 reports what this returns, and the answer is a measured near-zero: the recovered
clocks generate **300 of 305119 fires** over 3 tunes of 646. That number is the point
of the step — it is the input to scoping §7.4, and it is honest in a way a fitted
period would not be.

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
- **Split per write, not forfeited per register** (`_instr_streams`, `_buckets`).
  What is removed from `RAW` is a *write*, so every write `_classify` explains is a
  candidate emit and the rest stay residual: one unexplained write no longer costs
  the register. The old all-or-nothing rule and the subset search it needed are
  gone, replaced by a **bucket order** that makes the split explicit.
- **Order is constructed and checked, not hoped for** (`_precede`, `_buckets`,
  `_refine_voice`). ctrl/AD/SR is the order-preserved section, and the section
  renders as its buckets — one per stream key, plus the residual — concatenated in
  node order. So the rendering equals the observation exactly when every frame's
  bucket sequence is non-decreasing in one global order, and two adjacent writes in
  a frame fix a precedence between their buckets. `_precede` takes the transitive
  closure of those precedences; a key on a cycle can sit neither wholly before nor
  wholly after the residual, and is **demoted into the residual**, lightest first,
  until the digraph is acyclic. The remainder is ordered by ancestor count — a
  linear extension of the closure — and cut at the residual into `pre` and `post`.
  `_refine_voice` then rebuilds the whole section, residual writes in place, and
  compares it with the observed one *values included*; a mismatch refuses the voice.
  So the law never depends on a lucky interleaving, and a split that would reorder a
  register's writes is refused rather than emitted.

The row stream is per voice, not per lane: on Commando the AD and SR `SELECT`
nodes of a voice carry the *same* rows and share one `EDGE`, which is the
instrument selector showing through. What is **not** yet explained is the note-on
timing: the `EDGE` counts are observed. Values are declared data at a recovered
index; triggers are still the floor for 99.90% of fires, and §4d's `DIV` is the only
thing that lifts any of them off it. §6 counts that domain separately and never folds
it into the interpreted-emit share.

## 6. Coverage (measured, 200 frames unless stated)

`Coverage(interp, residual, total, planes, classes, triggers)` carries **two
partitions, one per domain**, and they are never summed.

- The **value** partition: emits produced by an interpreted generator vs emits
  replayed from `RAW`, the per-plane split, and per plane the evidence behind each
  interpreted emit — `lane` and `gate` are declared bytes at a recovered index
  (**strong**), `imm` is a program constant that passes the law without explaining an
  index (**shallow**, never folded into a strong figure).
- The **trigger** partition (`triggers`): `(generated, all)` fires, counted by
  `_run` off the evaluator itself. A generated fire is a `DIV` tick over a divisor the
  play code declares (§4d) — the only strong evidence this domain has, and the only
  evidence it admits at all. Every other fire is the `EDGE` floor.

### Where the tracker stands

Whole cached corpus, PSID start subtune, 200 frames: **682 tunes cached, 646
decompile**, and of those 646 the **tracker law passes 646/646** and a pitch table
is recovered for **582**. The 36 that do not decompile never reach this layer (10
`play $0000` with no interrupt vector installed, 5 init runaways, 3 unmodelled
`brk`, 3 pinned-trace faults, and the remainder assorted). Values are **38.66%**
explained and triggers **0.098%**; the two are stated apart because they are two
domains, and the second is smaller by a factor of 390. This is the current state,
not a delta; the tables after it record how it was reached.

| plane | interpreted | of | share | strong | shallow |
|---|---|---|---|---|---|
| freq | 414066 | 602528 | 68.7% | 414066 | 0 |
| pw | 89376 | 561585 | 15.9% | 85871 | 3505 |
| ctrl | 111659 | 300573 | 37.1% | 101884 | 9775 |
| filter | 22301 | 240694 | 9.3% | 21654 | 647 |
| sr | 55945 | 115963 | 48.2% | 47905 | 8040 |
| ad | 54362 | 112534 | 48.3% | 47010 | 7352 |
| **all** | **747709** | **1933877** | **38.66%** | **718390** | **29319** |

The other domain, on the same run and reported apart from that table:

| domain | generated | of | share |
|---|---|---|---|
| values (emits) | 747709 | 1933877 | 38.66% |
| **triggers (fires)** | **300** | **305119** | **0.098%** |

**96.1% of interpreted emits rest on strong evidence** — a declared byte at a
recovered row, or generated from one. The shallow 29319 are program immediates
(`ctrl`/`ad`/`sr` releases and hard-restarts) plus the observed seed each sweep run
starts from; they pass the law without explaining an index and are never folded into
a strong figure. The freq plane splits 147002 pitch-table `LOOKUP` emits (the note
lane, §4) and 267064 declared-lane `SELECT` emits (§4b); `ctrl`'s 111659 splits 69178
lane reads and 32706 gate images of a lane byte (§5); `pw`'s 89376 splits 63093
declared-lane reads and **26283 swept by a `RAMP`** (22778 generated, 3505 observed
seeds, §4c); `filter`'s 22301 splits 19033 declared-lane reads and 3268 swept (2621
and 647). Whole planes explained, per tune: `ad` 285, `sr` 268, `freq` 122, `ctrl`
57, `pw` 20, `filter` 4.

The residual is still dominated by the two accumulator planes: `pw` (472209 emits)
and `filter` (218393), with `ctrl` (188914) behind them. `pw` and `filter` are one
problem measured twice, and §4c's sweep now takes the part of it whose step a
declaration holds; what is left of that shape is counted per refusal below.
`ctrl`, `ad` and `sr` are no longer bounded by the
partition (§5 splits a register rather than forfeiting it) but by what the
declarations name at all: they now realize 69178/72778, 47010/52300 and
47905/51702 of their declared-byte ceilings. What is left there needs a row for
the gate to ride, and a voice that never reads its waveform from a declaration has
none (§8).

| tune | pitch table | interpreted | freq plane | pw | ctrl | ad | sr |
|---|---|---|---|---|---|---|---|
| Commando (Hubbard), 300 frames | `$5428` interleaved, 97 words | **2385/2525 = 94.5%** | 1198/1280 = 93.6% | 305/363 | 576/576 | 153/153 | 153/153 |
| Ghouls_n_Ghosts (Follin) | `$6D35`/`$6D96` split, 97 | 485/1164 = 41.7% | 485/722 = 67.2% | 0/0 | 0/29 | 0/7 | 0/4 |
| Automatas (Goto80/DefMON) | `$1578`/`$1614` split, 120 | 1594/4800 = 33.2% | 796/1200 = 66.3% | 0/1200 | 266/600 | 266/600 | 266/600 |
| Athena (Galway) | `$C517`/`$C55F` split, 72 | 426/1882 = 22.6% | 426/1200 = 35.5% | 0/600 | 0/48 | 0/17 | 0/17 |
| Krakout (Daglish) | `$E629` big-endian, 12, octave-shift | 228/5000 = 4.6% | 228/1200 = 19.0% | 0/1200 | 0/600 | 0/600 | 0/600 |

Of Commando's 576 ctrl emits, **75 are declared-lane reads** and **399 are that
lane with the gate bit cleared** — 474 strong, all from the `$5591` bank's
waveform lane at `+2`, the same row its AD (`+3`) and SR (`+4`) lanes read — and
102 are the `$80` hard-restart immediate. Its 306 ADSR emits are 154 lane reads
and 152 `ad = sr = 0` release immediates, its 305 pw emits 77 reads of the `+1`
lane of the same bank plus **209 generated by sweep `RAMP`s over the `+6` lane's
step, from 19 observed seeds** — one per re-staging (§4c) — and **all 1198 of its freq
emits are the declared
`$5428` pitch lanes at a recovered row** rather than observed words matched to an
ET table.
Automatas' 798 refined instrument emits are *all* immediates (`ctrl = ad = sr = 0`,
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
That query shipped, and "The accumulator's parameters, queried out of RAM" below is
what it returned — 29551 emits, against the 7480 ceiling a fitting oracle reached here.

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
Only Commando's sweep is a genuinely computed accumulator, and §4c's test as it then
stood (the evaluator reports no source cell) is sharper for it, not weaker.

What the change does **not** do is raise the ceiling to the plane totals. Emits
whose source tuple names a declared byte at a non-`mut` offset — the ceiling the
interpreted figure is drawn from, before any all-or-nothing rule — go freq 110833
→ 289125, pw 21085 → 87828, ctrl 20915 → 72778, sr 40098 → 51702, ad 43718 →
52300, filter 15571 → 27785. `ad` and `sr` are the gap to read: ~46% of their
emits now name a declared byte while ~16% are interpreted, so what holds them back
is the tracker's own all-or-nothing-per-register rule (§5 as it then stood) and its
order check, not provenance — which "Realizing the ceiling" below then measured and
removed. `filter` at 11.5% is the opposite reading: even named perfectly, most
filter writes are not declared-table reads.

### The filter plane, and the 8% of it that is a declared-table read

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile), against
the table above. $15-$18 join §4b's last-write-wins planes under a register class of
their own; no other rule changes.

| plane | before | after |
|---|---|---|
| interpreted | 557052/1933877 = 28.80% | **576085/1933877 = 29.79%** |
| filter | 0/240694 = 0% | **19033/240694 = 7.91%** |

Every one of the 19033 is `lane` class — a declared byte at the row the read cell
recovers — and **no other plane changes by a single emit**: freq, pw, ctrl, ad and sr
are byte-identical per tune, as is every other tune's class split. 184 tunes improve,
**none regress**, and 3 have the whole filter plane explained. The canonical fixpoint,
Gate FP and the tracker law all hold 646/646, unchanged.

The gain is exactly the ceiling this rule can reach, and the ceiling is the story.
Per register, over the plane's own denominator — the record after last-write-wins,
which is what `RAW` counts and what the 240694 above is (the 27785 in the table before
this one counts every write instead, so it is not the same basis):

| register | emits | names a declared byte | the tree names the table | a program constant |
|---|---|---|---|---|
| $15 cutoff lo | 24151 | 247 (1.0%) | 40 (0.2%) | 2361 |
| $16 cutoff hi | 87533 | 7882 (9.0%) | 7033 (8.0%) | 7907 |
| $17 resonance/routing | 54771 | 15642 (28.6%) | **10642 (19.4%)** | 2498 |
| $18 mode/volume | 74239 | 2321 (3.1%) | 1318 (1.8%) | 21411 |
| **all** | **240694** | **26092 (10.8%)** | **19033 (7.9%)** | **34177** |

Two things are refused, and both are named rather than folded away. The 7059-emit gap
between the third column and the fourth is what a blind search over every declaration
would take; on the lww planes the store statement naming the table is the only
admissible basis (§4b), so those stay residual. The last column is the shallow figure
this step does **not** claim: 34177 filter emits write a byte the program text stores
as a constant, over half of them $18, and an `imm` `LOOKUP` would pass the law for
every one — but it explains no index, and on a register that takes many program
constants it is the observation, not the program, choosing between them. The filter
plane carries strong evidence only.

Only **$17** is really a table plane: resonance and the routing mask are
per-instrument bytes a bank holds, and 28.6% of them name one. Cutoff is not, and the
split between its two bytes says why — the low byte that carries a sweep's precision
names a declared byte 1.0% of the time against the high byte's 9.0%.

How much of an index those 19033 rows carry is reported rather than assumed:
**11461 (60.2%) read one declared cell for the whole tune** — a filter setting in const
data, over 155 tunes — and **7572 read a lane at two or more rows**, over 43. Both are
declared bytes at a row `frameval.eval_src` recovered and neither is fitted, but a
fixed cell explains a value where a moving row also explains an index, so the two are
not one figure. Per register the single-row share is $15 100%, $17 69%, $18 80% and
$16 43%.

Where the rest of the byte comes from says what the plane actually is:

| register | computed | outside every declaration | at a `mut` offset | declared, byte moved | a declared lane |
|---|---|---|---|---|---|
| $15 cutoff lo | 2011 | 21651 | 173 | 69 | 247 |
| $16 cutoff hi | 15042 | 59268 | 1868 | 3473 | 7882 |
| $17 resonance/routing | 3787 | 28137 | 100 | 7105 | 15642 |
| $18 mode/volume | 22197 | 32824 | 300 | 16597 | 2321 |
| **all** | **43037 (17.9%)** | **141880 (58.9%)** | **2441 (1.0%)** | **27244 (11.3%)** | **26092 (10.8%)** |

Three fifths of the plane loads a cell no declaration covers — a RAM cell — which is
the answer `pw` gave and for the same reason: `eval_src` reports origins alongside SID
store sources, so a parameter staged in RAM at init or at note-on arrives here
unnameable. The shape confirms it is the same problem: **38872 emits (16%) sit in a
constant-nonzero-delta run of two or more**, and 38817 of them are cutoff — 39% of the
$16 emits and 21% of the $15 emits, against 0% of $17 and $18. Cutoff is an accumulator
sweeping; resonance, routing and mode are settings. A further 11.3% loads a cell that
*is* declared but no longer holds the byte that reached the register (#61), 16597 of
them $18, where the low nibble is the volume DAC (docs/frameprog.md §1.2) and a
declared mode nibble combined with a volume level is not a declared byte.

So the ceiling for this step was ~8% and the step returns ~8%. Raising it was §7.2's
query for RAM-staged parameters, not a filter generator: the filter plane is one more
accumulator whose step, bound and rate the play code copied out of a table. That query
has since shipped and took the plane to 9.3%, all of it cutoff (§4c, and "The
accumulator's parameters, queried out of RAM").

### Realizing the ceiling: a register split, not forfeited

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile),
against the filter table above, and a change to `tracker.py` only. §7.1's finer
partition (§5): a write, not a register, is what refinement removes from `RAW`, and
the order-preserved section is rebuilt from the typed buckets and the residual
bucket in a constructed order rather than being required to have the typed writes
at one end.

**Measured first, so the ceiling was known before a line was written.** Of the
ctrl/AD/SR writes in registers the old rule left wholly residual, `_classify`
already explained 165843 — and the distribution said the rule, not provenance, was
the binding constraint:

| unexplained writes in the register | registers | emits in them | of those, `_classify` explains |
|---|---|---|---|
| 0 (lost to the subset search or the order check alone) | 644 | 23289 | 23289 |
| 1 | 404 | 31537 | 31133 |
| 2–5 | 414 | 28544 | 27258 |
| 6–20 | 521 | 46025 | 40213 |
| 21+ | 1794 | 319921 | 43950 |

644 registers — 23289 emits — were forfeit with **nothing** unexplained in them,
and another 404 for a single write apiece. Only in the 21+ row is the residual
mostly genuine, and even there 43950 emits are explained writes held hostage by
their neighbours.

| plane | before | after | this step's ceiling |
|---|---|---|---|
| interpreted | 576085/1933877 = 29.79% | **718297/1933877 = 37.14%** | 741928 |
| ctrl | 42789/300573 = 14.24% | **111659/300573 = 37.15%** | 123979 |
| ad | 17592/112534 = 15.63% | **54362/112534 = 48.31%** | 60824 |
| sr | 19373/115963 = 16.71% | **55945/115963 = 48.24%** | 61415 |
| freq | 414066/602528 | 414066/602528 (unchanged) | — |
| pw | 63232/561585 | 63232/561585 (unchanged) | — |
| filter | 19033/240694 | 19033/240694 (unchanged) | — |

**419 tunes improve and none regresses**, and `freq`, `pw` and `filter` are
byte-identical per tune — this step touches the order-preserved section and nothing
else. The canonical fixpoint holds 646/646, Gate FP 646/646 and the tracker law
646/646, all unchanged.

The gain is strong evidence, and the split is reported rather than folded: of
+142212 emits, **129660 (91.2%) are strong** — `lane` 405721 → 513283 and `gate`
10608 → 32706 — and 12552 are program immediates (`imm` 12615 → 25167). Against the
declared-byte ceiling §6 measured for the *strong* classes, the `lane` counts now
reach 69178/72778 of `ctrl`, 47010/52300 of `ad` and 47905/51702 of `sr` — 90–95%
of what provenance names at all, from 39%/26%/28%. Whole planes newly explained:
`ad` 166 → 285 tunes, `sr` 195 → 268, `ctrl` 52 → 57.

**The cost, named.** 23631 explained emits (14.2% of the 165843 ceiling: `ctrl`
12320, `ad` 6462, `sr` 4849) are still refused, because their key straddles the
residual — the same key's writes fall on both sides of an unexplained write across
frames — and a bucket that cannot sit wholly on one side is demoted back into the
residual rather than emitted out of order. That is **258 of 1874 voices over 113
tunes**; the demoted total is exactly the gap between ceiling and gain, measured
independently from either side. The refusal is the whole guarantee: the law would
fail if it were skipped, and tests/test_tracker.py shows it failing for both
placements of a straddled key. The rebuild check behind it (`_refine_voice`
returning None, the voice back at the RAW floor) fires on **no** corpus voice — the
bucket order it verifies is constructed to satisfy it — so it is a guard, and the
test that exercises it drives it from a deliberately swapped order.

### The trigger domain, measured — and how little a clock explains

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile). Until
this step the trigger domain had no number anywhere: `Coverage` counted emits and said
nothing about fires, so the 37.14% headline was silent about the other half of the
primitive. It now has one, and the one is small.

| the trigger domain | nodes | fires | of all fires |
|---|---|---|---|
| all fire-routed nodes | 3801 | 280737 | 100% |
| strictly periodic (one distinct gap, ≥3 fires) | 836 | 76914 | 27.4% |
| … of which period 1 (consecutive frames) | 445 | 71076 | 25.3% |
| … of which period ≥ 2 — the divider-shaped ones | 391 | 5838 | **2.1%** |
| … of those, at `DIV`'s own phase (`n-1, 2n-1, …`) | 28 | 877 | 0.31% |
| **generated by a declared divisor (§4d)** | **3** | **300** | **0.107%** |
| the `EDGE` floor: the arrangement's population | 3798 | 280437 | 99.89% |

Read down that table: the "27% of fires are periodic" figure the output side offers
collapses at every step where a claim has to be earned. **Nine tenths of the periodic
fires have period 1** — a stream firing on consecutive frames is not a divider at all,
it is a store site that runs unconditionally, and 121 nodes (24200 fires) fire on every
one of the 200 frames. That leaves 2.1% of fires divider-shaped. Of those, only 28
streams sit at the phase `DIV(n)` fixes, so a *fitted* divisor — a period read straight
off the fire pattern — would still reach only 0.31% without a phase parameter as well.
And of those 28, **3** have a divisor the play code actually declares. Each step
divides the previous by roughly an order of magnitude, and the last one is provenance.

**420 of the 646 tunes declare a divisor and 3 of them generate an edge stream** —
`MUSICIANS/D/DaFunk/3-Speed.sid`, `MUSICIANS/D/Dr_Piotr/Agonia.sid` and
`MUSICIANS/D/Dune/Beach.sid`, one node each, 100 fires each. All three are `DIV(2)`,
the smallest admissible divisor, so the evidence is thin even where it holds: what
carries it is that `2` is a byte the play code reloads into a cell it steps down, and
that the law checks all 200 frames of the stream. That is the whole claim, and it is
0.107% of the domain.

What is refused is named, and each refusal was measured before it was made. A divisor
fitted to the fire pattern — the obvious way to "explain" 27% — is refused outright.
A reload out of a RAM cell is refused: its post-init byte is runtime state, and across
the corpus it adds no stream that the immediates do not (the one period-hit above is
an immediate).

`DIV(1)` is refused, and that refusal is the expensive one. 121 nodes fire on every
one of the 200 frames, **217 of the 646 tunes reload a `1` into a divider somewhere**,
and lifting the refusal would take **36 nodes and 7200 fires** across 36 tunes — 2.6%
of the domain, twenty-five times the shipped figure. It would be bought with a byte
that has nothing to do with why those streams fire every frame: they fire every frame
because their store site is unconditional, which the root frame clock already says.

The residue — **280437 fires over 3798 nodes, 99.89% of the domain** — is precisely
the population §7.4 has to explain. It is not a divider problem: 2965 of those nodes
(203823 fires) are not periodic at all, which is what an orderlist and a pattern
look like from the outside. Per tune the domain is small enough to work with directly:
the median tune has 412 fires and the largest 1716.

The value partition is **byte-identical** across this step, as it must be — a `DIV`
that replaces an `EDGE` fires on the same frames and so emits the same bytes. 718297
of 1933877 interpreted, `freq` 414066, `pw` 63232, `ctrl` 111659, `filter` 19033, `sr`
55945, `ad` 54362, and the class split `lane` 513283 / `gate` 32706 / `imm` 25167 /
`ramp` 138 / `seed` 1 — every figure unmoved. The canonical fixpoint holds 646/646,
Gate FP 646/646 and the tracker law 646/646.

### The accumulator's parameters, queried out of RAM

Same 682 cached tunes at the PSID start subtune, 200 frames (646 decompile), against
the table above, and a change to `tracker.py` only: §4c's step is no longer walked out
of the statement tree but **queried** from frameprog's origin map at the accumulator
statement's own execution (`frameval.eval_watch`, docs/frameprog.md §1.4).

| plane | before | after |
|---|---|---|
| interpreted | 718297/1933877 = 37.14% | **747709/1933877 = 38.66%** |
| pw | 63232/561585 = 11.26% | **89376/561585 = 15.92%** |
| filter | 19033/240694 = 7.91% | **22301/240694 = 9.27%** |
| freq | 414066/602528 | 414066/602528 (unchanged) |
| ctrl | 111659/300573 | 111659/300573 (unchanged) |
| ad | 54362/112534 | 54362/112534 (unchanged) |
| sr | 55945/115963 | 55945/115963 (unchanged) |

**134 tunes improve and none regresses** — 120 on `pw`, 36 on `filter` — and `freq`,
`ctrl`, `ad` and `sr` are byte-identical *per tune*, class split included. The whole
gain is the sweep: `ramp` 138 → **25399** and `seed` 1 → **4152**, while `lane`, `gate`
and `imm` do not move by a single emit. One more tune has its whole filter plane
explained (3 → 4) and no other whole-plane count moves. The canonical fixpoint holds
646/646, Gate FP 646/646 and the tracker law 646/646.

**The seed rose with it and stays shallow.** 4152 of the 29551 sweep emits are the
observed byte a run starts from — one per run, and runs are per re-staging, so a tune
whose step is re-copied at every note-on pays one observed byte per note. `ramp` is
strong and `seed` is not, and `Coverage.classes` reports them apart precisely so this
gain cannot be read as 29551 generated emits.

The trigger domain's **generated** figure does not move: 300 fires, the same three
`DIV` nodes. Its denominator does — 280737 → **305119** — because every new `RAMP` is
a node fired by its own `EDGE` stream, so the same 300 reads 0.107% → 0.098%. That is
the domain getting bigger, not the floor getting worse, and it is the price of counting
triggers over the graph the tracker actually builds.

**Every refusal, and what it costs**, over each plane's own last-write-wins denominator
(the `cutoff` column is $15/$16 only — $17/$18 are settings, not accumulators, and no
sweep is attempted on them):

| the emit is | pw | cutoff |
|---|---|---|
| a declared lane read — §4b claims it first | 63093 | 7073 |
| in a register no store links to a stepped cell | 228054 | 50227 |
| inside a zero-delta run: the step would be zero | 131589 | 23118 |
| in no run at all: a single emit | 27 | 59 |
| in a constant-delta run whose step no declaration names | 112539 | 27939 |
| … of those, refused only for a `mut` offset | 14 | 0 |
| **generated: `ramp` + `seed`** | **26283** | **3268** |
| **all** | **561585** | **111684** |

Read down it: the binding constraint is no longer the generator but provenance again.
**228054 pw emits (41%)** are written by a store whose byte reaches no cell the play
code steps — computed some other way, or accumulated behind a store the tree cannot
follow — and **112539 (20%)** do sit in a genuine constant-delta run whose step the map
traces to no declaration, because the parameter was filled at init or computed rather
than copied out of a table. The zero-delta row is the third of the plane that simply
holds its value; a `RAMP` of step zero would take all 131589 with a byte that predicts
nothing, which is the refusal `DIV(1)` makes in the other domain (§4d). The `mut`
refusal is measured rather than assumed and is nearly free here: **14 emits on one
tune**. The run-level refusal is all-or-nothing by construction — one stepped emit
whose origin no declaration names refuses its whole run — and it fires on 303 tunes for
`pw` and 251 for `cutoff`.

## 7. Where the residual goes next

Refinement, in the order that shrinks the residual fastest — each step must keep
the law green and must move emits out of `RAW`, never widen a declaration. The
order has changed twice. The origin rule of §6 moved the largest measured gains
**tracker-side**, to realizing a ceiling provenance already supplied; the finer
partition of §5 has now taken them. What is left is provenance-bound again.

1. **The instrument planes are now bounded by provenance again** — the finer
   partition shipped (§5, §6): `ctrl`/`ad`/`sr` interpret 37%/48%/48% and their
   `lane` counts reach 90–95% of the declared-byte ceiling, so the tracker's own
   rules are no longer what holds them back. What remains splits three ways, and
   only the first is a tracker problem: 23631 explained emits refused because the
   key straddles the residual (a note-on lane read and its gate-off image on either
   side of an unexplained write — an arrangement generator, §7.4, would place both);
   writes whose byte reaches the store computed, with no source cell at all; and
   writes whose source cell falls outside every declaration. The last two are
   frameprog's, not this layer's.
2. **Parameters staged in RAM — shipped, and what it left.** The step is now queried
   from the origin map at the accumulator's own execution (§4c), which took `pw`
   11.3% → 15.9% and `filter` 7.9% → 9.3% over 134 tunes with none regressing. What
   remains on those planes is provenance, not generator shape, and §6's refusal table
   sizes each part: **228054 pw emits** are written by a store that links to no stepped
   cell at all, **131589** hold a constant value (a step of zero explains nothing), and
   **112539** sweep with a constant delta whose step the map traces to no declaration
   — a parameter filled at init or computed rather than copied out of a table. The
   cutoff plane repeats the shape at a quarter of the size. Only the third of those is
   a query problem, and it needs the *init* phase's copies named, not the play phase's;
   the first is a frameprog dataflow question and the second is not an accumulator.
   A triangle sweep that turns at a declared bound is a further transfer this
   primitive does not have (§8), and no corpus tune reaches that limit before the step
   blocks it.
3. **Arpeggio and vibrato as generators, not notes** — a note-on carries one
   note; an arp step is a downstream generator emit on that edge, so it must
   never appear as a fresh row.
4. **Arrangement** — orderlist/pattern/transpose as `LOOKUP` nodes routed to
   `Fire`, with shared subgraphs for reuse and a back-edge for the loop. This is
   what replaces the `EDGE` floors and the recovered row streams: the row a
   note-on selects becomes an emit of the pattern generator, not observed data.

   **The population is now measured, and it is almost all of the domain.** §6's
   trigger census leaves **280437 fires over 3798 nodes, 99.89%**, and says what
   shape they are: 2965 of those nodes (203823 fires) are not periodic at all, and
   the 391 that are divider-shaped carry only 5838 fires between them. So a clock is
   not what is missing — §4d shipped one and it reached 3 tunes. What is missing is
   the table that decides *which* tick carries a note-on, which is the orderlist and
   the pattern. This is now the largest single unexplained thing at this layer, and
   it is the only step that moves the trigger figure at all.

   It also feeds back into the value domain: the 23631 emits §5 refuses because
   their stream key straddles the residual are exactly a note-on lane read and its
   gate-off image placed on either side of an unexplained write, and an arrangement
   generator would place both. Per tune the domain is small — median 412 fires — so
   the work is per-driver structure recovery, not scale.
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
- ctrl/ADSR that reaches the register through a **computed** value stays
  residual: the byte arrives from an expression rather than a load, so there is
  no cell to name. Over the 60-tune sample that was 7643 ADSR emits and 15957 of
  30025 ctrl emits, measured before the origin rule of §6. Neither staging in a
  RAM register mirror nor staging in a register is a refusal class any more —
  following the copy back to the bank is a dataflow step and it is frameprog's,
  so a staged bank read arrives here already declared (docs/frameprog.md §1.4).
  What still refuses is a byte whose origin is outside every declaration, or
  inside one but no longer equal to the snapshot (#61), plus the emits refused
  for write order — 23631 of them, whose stream key straddles the residual (§5, §6).
- The gate arm needs a row: a voice that never reads its waveform from a
  declaration has nothing for the gate to ride and stays residual — now for those
  writes only, not for the whole register.
- A sweep whose step the origin map traces to no declaration stays residual, and that
  is still most of them (§6): the `RAMP` is refused rather than seeded from an observed
  delta, since a step fitted to the output would pass the law while explaining nothing.
  A `RAMP` whose run predicts no emit — one write, or a step of zero — is refused for
  the same reason, and one stepped emit without a declared origin refuses its whole
  run rather than being dropped from it. The seed is the observed byte and is counted
  shallow. Only the wrapping bound is implemented; a triangle sweep that turns around
  at a declared bound needs a transfer this primitive does not have, and no tune in the
  corpus reaches that limit before the step blocks it.
- The step is queried at the *play* phase's copies. A parameter a table supplies at
  **init** and the play code only reads back arrives as a RAM cell with no origin, so
  it names no declaration; that is a large part of what §6's refusal table leaves.
- The filter plane is read by §4b and by nothing else: 77% of it loads a RAM cell or
  computes the byte outright, so no declaration names it (§6). It is refused rather
  than reached by an `imm` `LOOKUP` over the program's constants — that would take
  34177 emits without explaining an index, and $18 in particular takes many program
  constants, so it would be the observation choosing between them.
- On the lww planes the *table* is transliterated but the *index* is not: the tree
  names `m_5429[t5]`, and `t5` is a live state value no static reading yields, so
  `frameval.eval_src` still recovers it. That index becomes explained when the
  arrangement does (§7.4), not before.
- A divisor is refused unless the play code declares it (§4d): a period fitted to
  the observed fires, a reload out of a RAM cell whose post-init byte merely agrees,
  and a divisor of one are all refused, and §6 measures each refusal's cost. The
  primitive has no phase field either, so a divider whose counter starts anywhere but
  at its own reload stays at the floor — 363 of the 391 divider-shaped streams do.
  Adding the field would open at most their 4961 fires (1.8% of the domain), and only
  where the divisor is declared as well; the price would be a per-stream parameter
  fitted to the output.
- The tree walk is per procedure (locals) plus a program-wide staging hop
  (`origins`), so a byte staged across a procedure boundary or through the stack is
  named by no store site. On ctrl/AD/SR the provenance search covers it — 15648
  emits' worth; on freq/pw there is no such fallback and those writes stay residual,
  which is 5693 emits a blind search would have taken.
