# register-model-lift: implementation plan (phased, evidence-based)

Status: implementation plan for docs/register-model-lift.md, de-risked against
seven family-representative tunes and one corpus-wide sweep before any phase
starts. Each phase is mechanical (a committed instrument produces its work
list), bounded (a refusal vocabulary names what it will not do), and guarded
(gates that stop the line rather than reinterpret the goal). "MUST" is a gate.
§5 records the prototypes: the Phase 3 analysis run to zero oracle
contradictions on all seven tunes, the Phase 2 certification run against the
declared block registry, six Z3-proved worked examples, and the committed
`tests/test_shred_regmodel.py` shredder that stays xfail until the phases land.

## 0. The evidence base: one tune per driver family

Seven tunes across six families, chosen to span hand-coded drivers, tracker
exports, and the corpus's hardest control-flow shape, all building and all
**Gate FP clean** at HEAD (300-frame sweep, `out/gate_sweep.json`):

| family | tune | why it represents |
|---|---|---|
| Hubbard (hand-coded, 1985) | `MUSICIANS/H/Hubbard_Rob/Commando` | §7.10.12/13's measured tune; the showcase baseline |
| Galway (hand-coded, per-voice code) | `MUSICIANS/G/Galway_Martin/Comic_Bakery` | §7.10.2/7.10.9's problem tune; register-window sweeps |
| goto80 (scene, own/defMON-line player) | `MUSICIANS/G/Goto80/Automatas` | modern scene idioms; RAM pointer cells |
| GoatTracker (tracker export) | `MUSICIANS/C/Cadaver/Aces_High` | the suite's canonical GT tune (`tests/test_streams.py`) |
| SID-Wizard (tracker export) | `MUSICIANS/C/Chabee/Angry_Birds` | player-signature-verified SW export (pysidwizard `read_sid`) |
| Follin (script interpreter, SMC dispatch) | `MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts` | docs/follin-dispatch-study.md's subject; the dispatch worst case |
| Follin (confirmation) | `MUSICIANS/F/Follin_Tim/Agent_X_II_The_Mad_Profs_Back` | the study's second tune; same idiom, different sites |

Every number below was measured on these seven at HEAD (`6d19741`), 1500 frames
(or full length where shorter), with `tools/lift_residue.py`,
`tools/lift_triage.py`, `tools/fuse_measure.py`, a scratch storage-class probe
replicating §7.10.13's harness, and a corpus-wide store-reach sweep (both
committed in Phase 0). The probe calibrates: it reproduces §7.10.12's Commando
figure (23,357 SID-image reads) and §7.10.13's state verdicts (26 cells: 4
persistent, 3 never written) exactly.

**The family table.** Census sites are `lift_residue` signatures; storage rows
are the instrumented state image; ⊤ means a G1-resolved `addr_bits` bound
above `$01FF`.

| | Commando | Comic_Bakery | Automatas | Aces_High | Angry_Birds | Ghouls | Agent_X_II |
|---|---:|---:|---:|---:|---:|---:|---:|
| emitted lines | 432 | 1066 | 860 | 528 | 1142 | 955 | 773 |
| census sites, total | 21 | 111 | 72 | 23 | 124 | 161 | 105 |
| — `carry_val` | 1 | 50 | 20 | 6 | 38 | 20 | 18 |
| — `unnamed_addr` | 5 | 33 | 29 | 6 | 46 | 81 | 47 |
| — `word_pack` | 5 | 9 | 9 | 8 | 2 | 16 | 8 |
| — `raw_sp` | 0 | 0 | 3 | 0 | 16 | 0 | 0 |
| — `flag_bit` / `borrow` | 4 / 2 | 2 / 0 | 8 / 1 | 1 / 2 | 10 / 5 | 8 / 4 | 3 / 4 |
| lane residue (triage) | 0 | 5 `swept` | 0 | 0 | 1 `unproven` | 3 `unproven` | 2 `unproven` |
| `state { }` fields declared | 26 | 133 | 86 | 19 | 24 | 114 | 89 |
| — scratch (frame-local/RO/WO) | **22** | **52** | **15** | **11** | **9** | **41** | **30** |
| — genuinely persistent | 4 | 71 | 69 | 4 | 12 | 68 | 58 |
| — untouched at 1500 frames | 0 | 10 | 2 | 4 | 3 | 5 | 1 |
| frame-local cells, all regions | 20 | 33 | 11 | 5 | 11 | 4 | 7 |
| ⊤ **store** addresses | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| ⊤ **load** sites | 0 | 23 | 26 | 6 | 39 (+13 stack) | 52 | 42 |
| — their pointer roots | — | `zp_F0/F2/F4/F6` | `zp_FB`, RAM cells `m_11xx` | `zp_FB` | `zp_FE` | `zp_21/23/25` | `zp_02..07` |
| SID read-back sites (static) | 3 | 1 | 1 | 2 | **0** | **0** | **0** |
| `hi-first` stores | 3 | 0 | 0 | 0 | 2 | 0 | 0 |
| `switch goto` dispatch sites | 0 | 4 | 0 | 2 | 1 | 3 | 3 |
| `unobserved` markers | 10 | 38 | 16 | 6 | 23 | 44 | 32 |

One instrument correction found while calibrating, owed to the record:
`frameval.run_frame` re-reads the just-stored cell to buffer a SID write
(`frameval.py:523`), so a state-image read counter sees one echo per write.
§7.10.12's "23,357 SID reads" includes that echo; the echo-free figure for
Commando is **5,440 reads over 8 registers from the same 3 statements**, and
the section's conclusions (all write-only, carried across frames, three sites)
are unchanged. Phase 0's committed instrument counts net of echo.

**The corpus-wide store-reach sweep** (all 624 tunes, 0 refusals, 250s): the
per-family ⊤-store row generalizes. **570 of 624 tunes (91.3%) contain no
store whose reach bound exceeds the stack.** The 105 wide stores over the
remaining 54 tunes are three shapes and nothing else:

| shape | stores | tunes | disposition |
|---|---:|---:|---|
| `(zext2(reg) + $00NN):2`, true bound ≤ `$01FF` | ~20 | 15 | G2's `INT_ADD` carry rule bounds them; **13 tunes go clean on G2 alone** |
| a store *through* a sequence pointer (`(zp_21:2 + zext2(y)):2`, `m_5554:2`…) | ~50 | 12 | write-through players; same certification as the ⊤ loads (Phase 2) |
| a bare `t0:2` local G1's read cannot resolve at that seat | ~31 | 30 | resolve-strengthening or per-tune refusal |

43 of the 54 carry exactly one; the two worst at 14 apiece are `C64_World` and
`1st_Decent_Hardcore` — **the same two tunes §7.10.3 names as the worst
`unnamed` carriers**, which is the framing confirming itself: their pointer
write-through is one defect wearing two counters.

## 1. Ambiguities resolved up front

These are the questions that would otherwise surface mid-phase. Each is now a
measured answer, not a judgment call.

**R1 — Can a store clobber a promoted cell through an alias? Corpus-measured:
no for 91.3% of tunes, and the rest are three named shapes.** The seven-tune
zero generalized: 570 of 624 tunes have no ⊤ store at all, ~20 of the 105
exceptions are G2's three-line rule away from bounded, and the remainder are
write-through-pointer players (Phase 2's machinery, load and store alike) plus
31 unresolved bare locals. R1 is therefore **not an assumption anywhere in the
plan**: Phase 0 commits the sweep as a tool, and the promotion pass recomputes
it per build — a tune with a surviving wide store gets promotion refused for
every cell the store cannot be excluded from, refusal class `wide_store`,
ledgered per tune. Promotion never runs on trust; the check that licenses it
is the same walk that produced this table.

**R2 — What are the unbounded (⊤) accesses? Sequence-pointer traffic,
uniformly, in every family including Follin.** All 188 ⊤ loads on the seven
are the tracker/interpreter's pattern-walk (`(ptr + zext2(y+k))`) through 15
pointer roots, every one already in `streams.classify`'s `pointer` vocabulary
(GT's `$FB/$FC` and Follin's `$21..$26` are literally documented test/study
cases). The corpus adds one twist the five-tune draft missed: 12 tunes also
*store* through such pointers. So the pointer question is the whole ⊤
question, load and store — which is what makes Phase 2 the keystone phase, and
motivates lifting the pointers away entirely (R9) rather than guarding around
them.

**R3 — Is the `state { }` scratch finding family-specific? No.** Scratch
fraction of declared state: Hubbard 85%, GT 58%, Galway 39%, SW 38%, Follin
36%/34%, goto80 17%. Universal but 3×-variable: Galway, goto80 and Follin
genuinely persist most of their declared state (71/69/68 fields), so Phase 3's
win is real everywhere but must not be over-promised. The dynamic verdicts are
an **upper** bound on what the static analysis may promote (§7.10.13's
path-dependence argument stands).

**R4 — Do the byte-pair idioms reduce to few shapes? Yes, across all six
families.** `word_pack` is two skeletons (`OR(ZEXT(lo), LEFT(hi,8))`, both
operand orders); `carry_val` is three roles: (i) the ADC-chain hi column —
Angry_Birds' freq compute and Ghouls' `ptr_0021_hi + (carry(ptr0,$02) | …)`
pointer advance are the type specimens; (ii) a page-cross guard feeding
`unobserved`; (iii) `cflag` stored and consumed by the next chain. All three
collapse under the already-admitted QF_BV rules once the operands are wide —
and role (ii) disappears entirely under R9's cursor lift, since a cursor
advance has no page to cross.

**R5 — Is the write-only read-back universal? Five families no, three yes —
and the corpus says it is nearly everywhere.** Static sites on the seven:
Commando 3, Comic_Bakery 1, Automatas 1, Aces_High 2; Angry_Birds, Ghouls and
Agent_X_II **0** (7 sites; an earlier draft said 8 from a mis-attributed grep
— the committed instrument is the authority). Every site is `_widen`'s RMW
emission, and the zeros are the existence proof of the target shape. Phase
0's census then sized the class the seven could not: **1,123 `sid_readback`
sites over 466 tunes** (full length; 1,069 over 458 at 1500 frames) — the
seven evidence tunes were unrepresentative on exactly this row, and Phase 5
is a corpus-majority fix, not a cleanup.

**R6 — Does the gate hold on the evidence set? Yes.** All seven gate clean at
HEAD; any verdict movement on them is attributable to the phase that moved it.

**R7 — What metric arbitrates? The census.** Per-tune machine-shape sites: 21
/ 111 / 72 / 23 / 124 / 161 / 105 on the seven. Headline: **tunes wearing zero
machine shapes**. The word-store rate is retired (§7.10.10 proved it can rise
while the census worsens). Every phase MUST move its named classes down and
MUST NOT move any other class up, summed per tune.

**R8 — Does the Follin dispatch break the plan? No — it is already structured
in this dialect, and the wall is in the walker, not the program.** Ghouls'
emitted frame program contains **zero** `dgoto`/`igoto`/`pcall`/`callb`
statements. The three per-voice SMC dispatchers (docs/follin-dispatch-study.md
§1) arrive as three `switch goto { case $6858: … }` statements over the
*observed* handler set, with `unobserved` markers at the closure boundary and
a runtime fault outside the observed map (`frameval`'s `swd`/`gdyn` +
`_resolve` discipline). Two consequences the plan now states explicitly:

1. **Phase 3's liveness analysis MUST be a forward analysis over the emitted
   dialect, not a reuse of the backward `Defs` walker.** §7.10.5's
   whole-procedure kill switch (`_verified`: one computed jump refuses every
   label join) is a property of the backward unique-definition walk. A forward
   written-before-read analysis sees the dispatch as an n-way join over its
   spelled-out cases, treats `unobserved` as a terminator (semantics past it
   do not exist — evaluation faults there, which is the program's own claim
   boundary), and follows `goto`/label edges through a worklist. On this
   dialect the "computed jump wall" class is **one statement on all seven
   tunes** (a single raw dyn form in Angry_Birds; Phase 0's census); the
   `wall` refusal is reserved for programs that emit such raw forms, and the
   corpus count is now known: **42 statements over 31 tunes**, with
   `switch goto` dispatch (195 sites over 141 tunes — Galway and GT emit them
   too, not just Follin) handled as an ordinary join.
2. **Follin's residue is ordinary.** Zero ⊤ stores, zero `raw_sp`, zero
   read-backs; its ⊤ loads are the three script pointers; its `word_pack`
   sites include the handler-table pack `(m_6C76[a]<<8 | m_6C37[a])` feeding
   the switch — Phase 2/4 material like everything else. What is genuinely
   Follin-shaped is only the *scale* of the pointer machinery (a 3-deep
   per-voice cursor call stack, loop save/restore cells), which R9 addresses
   head-on. The old §4 caveat ("Follin refuses everything") is withdrawn as
   measured-false; what remains true is that its scratch yield is smaller
   (4–7 frame-local cells) because the interpreter's state genuinely persists.

**R9 — Are pointer variables needed at all, or can the lift go straight to
tables? Straight to table cursors, with u16 pointers as the spelled fallback.**
The measured facts: every ⊤ access in every family is a walk of the form
`*(ptr + k)` where `ptr`'s reaching definitions are (a) a row loaded from a
declared lo/hi pointer table, (b) an in-block advance (`ptr += n`, the batch
`TYA/ADC` fold), or (c) a save/restore of another such value (Follin's loop
cells `zp_30`, its 3-deep call stack). That is not an address computation — it
is a **cursor into a table's block**, and the IR should say so:

    seq_v1 : cursor(m_7338)          // block row + offset, no address
    w0 = seq_v1[y]                   // read at offset — bounded by the block
    seq_v1 += 2                      // advance — no carry, no page
    seq_v1 = m_730E[x]               // reload — row assignment
    stk[d] = seq_v1 ; seq_v1 = …     // Follin call/ret — cursor values are data

What this buys, in the plan's own currency: the ⊤ class is **deleted, not
guarded** — a cursor read is inside its block by construction, so R2's
certification obligation becomes a type fact, the 12 write-through tunes'
stores become bounded table writes, and the page-cross `carry_val` role (ii)
vanishes because offsets don't carry. The block-extent premise moves into the
evaluator: a cursor read outside its block's declared extent **faults**,
exactly the `unobserved`/`rmap` discipline — the claim is observation-closed
and self-checking under the gate. And it is the semantically honest object:
the layer above (docs/song-model.md, sidprog) already models pattern
*positions*; rung (f) already proves "this deref is a row of that table's
blocks" for the reload-only case — the cursor is that rung generalized to
advancing reads. The u16 pointer variable remains the fallback spelling for a
root that fails block-rooting (class `ptr_uncertified`), so the phase never
blocks on a hard root; but on the evidence of seven families and the corpus
sweep, the cursor is the common case and the pointer the exception, not the
reverse. Cost, stated: the cursor is a new IR/grammar construct with evaluator
support — the largest dialect addition in the plan, which is why Phase 2 is
split so its analysis half lands and is measured before the dialect half is
built.

## 2. The phases

Ordering is by dependency: 1 clears the stack, 2a certifies the pointer
traffic (producing the bounds Phase 3's guard needs — the guard needs the
*bound*, not the rewrite, so **Phase 3 depends on 2a only** and 2b can land
on its own schedule, §6), 3 promotes frame-local scratch, 4 coalesces the
remaining byte columns, 5 retires the boundary read-back, 6 re-measures. Every phase ends with the same sweeps
(`gate_sweep`, `lift_residue`, `fuse_measure`, `storage_census`) over the full
corpus, and its before/after table appended to this doc.

### Phase 0 — instruments, baselines, and the metric (no text change) — DONE

**Landed**: `tools/storage_census.py` (new; 15 hermetic tests in
`tests/test_storage_census.py`), wide-store classification folded into
`fuse_measure`'s existing walk, and `sid_readback` + dyn-control counts added
to `lift_residue` — per §6, modes of the standing instruments, not new
counter populations. Verified: every pre-existing `fuse_measure` total is
unchanged (`unproven` 217 … `provably_complete` 487), and the extended
`lift_residue` is **bit-identical to a pristine-HEAD sweep on every existing
signature, per tune, all 624 rows**. The seven-tune table above reproduces,
with two cells corrected *by* the instruments (Comic_Bakery read-back 1, and
the `switch goto` row — the instrument is the authority, which is this
phase's point). One committed number drifted honestly: `carry_val` is 5,608
at HEAD (the doc's 5,594 predates commits `f66f61a..6d19741`).

**The baselines** (`out/fuse_measure.json`, `out/lift_residue.json`,
`out/storage_census.json` at 1500 frames; out/ is local, the numbers are the
record): census-zero headline **0 of 624** (Commando closest at 21 sites);
`state { }` corpus-wide **17,664 fields, 9,360 (53%) scratch**, 7,627
persistent, 677 untouched, 5 tunes with zero declared scratch; frame-local
cells 9,436 of which **668 (7%) crossproc**; ⊤ loads 6,715, ⊤ stores 105
over 54 tunes ({g2_boundable 32, ptr_writethrough 38, loc_unresolved 33,
other 2} — `storage_census` and `fuse_measure` agree store for store);
`sid_readback` **1,123 / 466 tunes** (Phase 5's size); dyn-control 42 / 31,
`switch goto` 195 / 141 (Phase 3's wall class). One new fault:
`1st_Decent_Hardcore` **faults under evaluation at 1500 frames** (invisible
to the 300-frame gate sweep; it is also one of the two 14-wide-store tunes)
— it joins `C64_World` on the excluded list with its cause unchased.

The phase specification, for the record:

- Commit `tools/storage_census.py`: the §7.10.13 harness productionized — per
  cell: reads/writes (net of the `frameval.py:523` write echo), first-access
  kind per frame, storage-class verdict, region; per tune: `state { }`
  verdict table, ⊤-load/⊤-store site lists with pointer roots, SID read-back
  sites, cross-procedure use counts per frame-local cell, and a
  `--frames full` mode. Keyed by cache-relative identity.
- Commit the store-reach sweep (`tools/reach_sweep.py`, the R1 instrument):
  per tune, every store with `addr_bits > $01FF` and its shape class
  (`g2_boundable` / `ptr_writethrough` / `loc_unresolved`). Baseline recorded:
  105 / 54 / 624 as above.
- Count programs emitting raw `dgoto`/`gdyn`/`swd` forms (R8's `wall` class),
  and `switch goto` sites per tune, corpus-wide.
- Extend `lift_residue` with a `sid_readback` signature so Phase 5 has a
  census column (R5's corpus size stops being unsized).
- Record the census baseline and the headline: tunes at census-zero, at HEAD.

Gate: instruments reproduce the seven-tune table bit-for-bit; no emitted text
changes anywhere (hash-checked).

### Phase 1 — the stack becomes locals (`raw_sp` -> 0)

Scope: `framestack` finishes. Balanced push/pop and call linkage become
locals/params; the work list is the unbalanced residue — **16 of the seven
tunes' 19 `raw_sp` sites are Angry_Birds'** (SID-Wizard's dispatch), 3
Automatas'; corpus bound 2,604 sites over 323 tunes. Follin needs nothing
here: the study's rts census found zero pushed-target rts, and both tunes
carry `raw_sp` 0.

Mechanical rule: per-procedure stack effect; balanced -> locals; unbalanced ->
the idiom is named (SW's dispatch first) and handled by a tested rule or
refused per procedure, class `sp_unbalanced`.

Gates: full `gate_sweep`, **no verdict regression tolerated, with one named
exception**: `720_Degrees` (the standing Class B divergence localised to
framestack, §7.10.14) may only improve, and that movement is expected — the
phase owes it a look while it is in `framestack`, and "zero movement" and
"fix the standing divergence" cannot both be the gate. Census `raw_sp`
monotone to 0-or-refused; `unnamed_addr` falls by its stack component; no
other signature rises.

### Phase 2 — sequence traffic becomes table cursors

The keystone phase, split so analysis lands before dialect.

**2a — certification (analysis only, no text change).** For every pointer
root (Phase 0's list): classify each reaching definition as table-row reload /
in-block advance / save-restore / other. A root with zero `other` is
**block-rooted**; emit the per-root proof record (the §7.10.11
`_counter_range` pattern: the rule re-asks the question in sufficient form and
records which premise held). Corpus-measured coverage of block-rooting is 2a's
deliverable — the number that decides how much of 2b's dialect work the
corpus pays for. `streams.classify` is the finder; the seven-tune expectation
is 15/15 roots certify.

**2b — the cursor dialect and rewrite.** Grammar/IR: `cursor(table)` state
fields; indexed read `cur[k]`; advance; reload from a pointer-table row;
cursor values as data (Follin's call stack and loop cells). Evaluator: cursor
reads fault outside the block extent (observation-closed, gate-visible).
Certified roots rewrite to cursors — loads *and* the 12 write-through tunes'
stores; uncertified roots fall back to coalesced u16 pointer variables
(existing spelling, `ptr_005D: u16`), class `ptr_uncertified`; certified roots
with unbounded advance keep the fallback too, class `ptr_extent_open`. The
page-cross carry guards collapse with the addresses that carried them.

Gates: 2a — none needed (read-only), its output reviewed as numbers. 2b —
full `gate_sweep` with movement only on tunes whose text changed, gating same
or better; census `unnamed_addr` + `carry_val` down, sum down; triage
`unproven` MUST NOT rise; canonical fixpoint over the new construct;
`dumps(loads(t)) == t`.

### Phase 3 — frame-local promotion (the scratch elimination)

Mechanical rule, both conditions computed by committed analyses:

- **(i) not live-in at the frame boundary**: a **forward** written-before-read
  analysis over the emitted dialect from the `play` entry — across procedure
  calls, joining over `if`/`loop`/`for`/`switch goto` arms and label targets
  via worklist, `unobserved` as a terminator (R8). It runs read-only on the
  final program. The residual `wall` class covers only raw `dgoto`/`gdyn`
  forms, sized by Phase 0 before this phase commits to a yield.
- **(ii) unthreatened**, and this is a per-cell interval test, not the R1
  sweep re-run: a cell is threatened by any store whose reach interval it
  intersects — an uncertified wide store (`wide_store`, R1's guard), an
  **in-bounds computed store** (`zp,X` modular stores are bounded `$00FF` and
  still clobber zero-page scratch — census `mod_addr` is 741 sites over 91
  tunes — and a G2-bounded `(zext2(y)+$NN)` store is bounded ≤ `$01FF` and
  still clobbers low RAM; a bound below the SID is not a bound away from the
  cell), and a **certified cursor write extent** (a write-through store lands
  inside its block by construction, so a cell inside that block is clobbered
  with the evaluator never faulting). The refusal class for all three is
  `aliased`, computed from the same `addr_bits`/extent facts conditions use
  elsewhere (`frameproc.store_reach`/`overlaps` are the existing vocabulary),
  and pinned by the shredder's `alias_state` fixture.

A cell passing both leaves `state { }` and becomes a local; cross-procedure
frame-locals follow the params/returns vocabulary or refuse as `crossproc`
(sized by Phase 0). Refusal vocabulary: `livein`, `aliased`, `crossproc`,
`wall`, `wide_store`.

**The cross-frame obligation, stated fully.** Promotion is licensed by the
static proof alone; the dynamic verdicts are ceilings and oracle, never
license. Three specific hazards and their answers:

- *Init coupling.* `state0` is the post-init image, so a cell's init-written
  value is live exactly when frame 0 reads it before writing. The static rule
  quantifies over every frame uniformly (written-before-read on every path
  from `play` entry), so frame 0 is not a special case: a promoted cell's
  init value is dead by the same proof, and init emission is untouched.
- *Late persistence.* A dynamically frame-local cell can persist on a path
  first taken at frame 9,000; `pos_54EC` (130/1500 frames) is the standing
  example. This cannot make promotion unsound — the license is the
  every-path proof — but it makes the *gate blind*: `gate_sweep` runs 300
  frames, so an analysis defect diverges past the sweep's horizon. Two
  mitigations, both mandatory: the seven review tunes gate at **full
  Songlengths length** every phase (Comic_Bakery 9,450, Ghouls 12,950 — the
  §7.10.9 lesson priced in), and the **differential guard** below.
- *The differential guard (static vs dynamic, corpus-wide).* For every tune,
  `storage_census --frames full` is the oracle for the analysis: a cell the
  static analysis calls frame-local that the dynamic record shows carrying a
  value across any frame boundary is an **analysis bug, found before
  emission** — the sweep fails, the phase stops. This is the §7.10.9
  precedent (`_Flow`'s `for`-exit hole survived because nothing checked
  liveness against an execution) built into the phase as a standing gate
  rather than learned again. The converse direction is expected and reported,
  not failed: static refuses what dynamic says was local (conservatism has a
  count, per tune).
- *Subtune scope.* A frame program is per-subtune (`_sweep`'s pinned subtune);
  promotion verdicts — and Phase 2a's certification records — are per-program
  and claim nothing across subtunes.
- *The `for` premise, stated.* The dialect's `for` is do-while: `frameval`
  emits the body before `fortest`, so the body runs at least once and a
  must-write inside it holds at the exit. The analysis leans on that; §7.10.9
  is the precedent for such a premise rotting silently, so it is stated here
  and owed a shredder fixture (a cell written only inside a `for`, read after
  it) when the analysis lands.

Gates: full `gate_sweep` (300f corpus + full-length review seven); the
differential guard; census `unnamed_addr` down, sum down; `storage_census`
scratch-declared-as-state monotone toward 0; `fuse_measure` `unnamed` down.
Five-tune-era bound restated for seven: candidate cells 20/33/11/5/11/4/7;
declared-scratch 22/52/15/11/9/41/30 fields.

### Phase 4 — column coalescing (the wide-value lift, M-FP3)

Scope: transient (lo,hi) locals and paired table columns still packed by hand
— R4 classes (i)/(iii), `word_pack`'s two skeletons, `hi/lo_byte`,
`shift_pair`, `borrow`. Seven-tune bound: ~300 sites; corpus bound: the census
majority. Mechanical rule: a candidate is a def-use-linked (lo,hi) pair in one
of the enumerated adjacency shapes; the rewrite happens in the unified e-graph
and every instance is Z3-proven per the existing admission gate
(eqlift-adoption §4). The pair-cell dialect (M-FP3) is this phase's grammar
deliverable. Follin's handler-table pack (`m_6C76[a]<<8 | m_6C37[a]` feeding
its `switch goto`) is in scope as a pack like any other; keying the switch on
the command byte instead of the packed target is recorded as a Phase 6
readability candidate, not done here.

Gates: **evaluator support for the dialect is a precondition of the rewrite
landing, not a follow-up** — a corpus-wide text the gate cannot execute
contradicts §3's own laws and eqlift-adoption §6's no-replay-claim rule, so
the Z3-proofs-only window covers development, never a landing (Phase 2b
states the same rule for cursors and Phase 4 owes it equally). Then: full
`gate_sweep`; the census block
`carry_val`+`word_pack`+`hi_byte`+`lo_byte`+`shift_pair`+`borrow` falls
**as a sum** per tune (the item-1 lesson: no trading among classes);
saturation holds the 60s per-procedure budget.

### Phase 5 — the boundary keeps a shadow, not a read-back

Scope: exactly `_widen`'s RMW form; 8 static sites on the seven, corpus-sized
by Phase 0's `sid_readback` column. Rule, in preference order: (a) where
Phases 2–4 made the full word available, emit it and delete the read (item 1
proved merged pairs owe no read-back); (b) otherwise declare a shadow variable
for the held lane — a new declaration kind (a shadow has no RAM address),
written alongside every store of its register. `framelog`'s `held` semantics
are untouched. Three families already prove the target shape at 0 sites.

Gates: `sid_readback` census column to **0** (Gate FP cannot see this defect,
§7.10.12, so the census is the gate, stated up front); full `gate_sweep`
otherwise unchanged; canonical fixpoint over the new declaration.

### Phase 6 — re-measure, then decide about the walker

Not a rewrite phase. With 1–5 landed: re-run triage and census, re-derive
§7.10.7's ranked list. The value-set fixpoint (old item 5) is built only if
the residue still pays for it — over promoted variables and cursors it is a
textbook forward analysis, and the expectation recorded here is that it is no
longer worth building. G2 (old item 3) is partly consumed by Phase 0's
`g2_boundable` class; computed-jump scoping (old item 4) is expected to
dissolve into R8's forward-analysis design. Readability candidates parked
here: switch-on-command-byte for interpreter dispatches.

## 3. Divergence guards (cross-cutting, every phase)

- **The sum is the metric.** A phase that moves its own class down and the
  census sum up is rejected as bookkeeping.
- **Gate verdicts stop the line.** Any `gate_sweep` movement not predicted by
  the phase's rule is stop-and-localize (§7.10.9/§7.10.14 method).
- **The differential guard runs wherever a phase claims a static property an
  execution can witness** (Phase 3's liveness against `storage_census`;
  Phase 2b's cursor extents against evaluation faults). Dynamic contradicts
  static -> the phase stops; static merely over-refuses -> counted, reported.
- **Refusals are ledgered.** Every refusal class is counted per tune in the
  phase's sweep; a ledger that shrinks without a rule change is a finding.
- **The shredder is the executable spec** (`tests/test_shred_regmodel.py`,
  §5.4): each phase's canonicality assert is `xfail(strict=True)`, so landing
  a phase flips its test to XPASS and the marker must be removed in the same
  change — the suite and the plan cannot drift apart silently.
- **The seven tunes are the standing review set** — emitted-text diffs read by
  hand, and full-Songlengths Gate FP per phase, before any corpus sweep is
  trusted.
- **No per-tune rewrites** (eqlift-adoption §4): every finding becomes a rule
  with its proof, an analysis strengthening, or a named refusal.
- **Sampled verification is not verification** (§7.10.9): every gate is the
  full 624-tune sweep; the seven are for reading, not proving.

## 4. What the evidence set still does not cover, stated

- **The 54 wide-store tunes** are ledgered, not solved: ~13 clear on G2, ~12
  ride Phase 2's cursors, and the ~30 bare-local tunes need a resolve
  strengthening that is designed when Phase 3's ledger shows what it is worth.
  `C64_World` is doubly excluded (14 wide stores *and* the standing
  evaluation fault `unobserved $4ED7`).
- **Digi/sample players** are outside Gate FP's input class (§1.2) and this
  plan.
- **`Rambo_First_Blood_Part_II`** (Class C divergence, beneath every rung,
  §7.10.14) predates and is untouched by this plan; it stays on the record.
- **Dynamic ceilings are 1500-frame** except where `--frames full` is noted;
  every promotion claim is static, so this bounds reported yields, not
  soundness — and the Phase 3 differential guard runs full-length.
- **The Follin caveat is withdrawn** (R8): both study tunes are in the
  evidence base, structured, zero-⊤-store, gate-clean. What Follin still
  contributes is the smallest scratch yield (its state genuinely persists)
  and the deepest cursor machinery (call stacks), both now in-plan rather
  than out.

## 5. Prototypes and worked examples

Every mechanism the phases rely on was prototyped against the seven tunes
before this plan was frozen. The prototypes are scratch instruments (they
become the committed tools of Phase 0); the numbers and the caught defects are
the record. The worked examples are real emitted text, and each rewrite shown
is Z3-proved as an equivalence over all inputs, in the eqlift admission style.

### 5.1 The Phase 3 analysis, run to zero contradictions

A ~200-line forward written-before-read analysis over the emitted dialect
(intersection-of-must-written joins over `if`/`switch goto`/labels-by-worklist,
loop fixpoints, `unobserved` as terminator, `callb`-aware call summaries) was
run on all seven tunes and checked against the instrumented-execution oracle:

| | fields | promotable (static) | dynamic ceiling | contradictions |
|---|---:|---:|---:|---:|
| Commando | 26 | **19** | 22 | 0 |
| Aces_High | 21 | 6 | 15 | 0 |
| Angry_Birds | 26 | 3 | 12 | 0 |
| Agent_X_II | 95 | 10 | 31 | 0 |
| Ghouls_n_Ghosts | 120 | 15 | 46 | 0 |
| Comic_Bakery | 141 | 1 | 62 | 0 |
| Automatas | 94 | 1 | 17 | 0 |

Units, stated once: "fields" here is `prog.state` entries (hidden fields
included), where §0's "fields declared" counts the rendered `state { }` block,
so the two columns differ by the hidden entries (Aces_High 21 vs 19). The
ceiling is the oracle's scratch + untouched count in §0's units. Phase 0's
committed instrument reports both in one row so the units cannot drift again.

**The differential guard caught three unsound analysis bugs before any
rewrite existed**, which is the guard earning its Phase 3 seat: a table-extent
misread (`size * stride` where size is bytes — Commando's freq table then
"covered" the state block and everything looked live-in), a `callb` body's
`ret` treated as a procedure exit (everything after voice 1 unreachable, so
Comic_Bakery scored 136 promotable of which 71 were dynamically persistent),
and unknown `call` targets treated as no-ops (Agent_X_II: 58 contradictions).
With the fixes the verdict is sound on all seven, and the remaining precision
losses are ledgered, not hidden: the unknown-callee rule now assumes a ⊤ read
set, which collapses Comic_Bakery and Automatas to 1 promotable each — their
6 + 2 unresolved mid-procedure `call` targets are the `crossproc` class, and
resolving them is a named precision item, not a soundness one. Commando's
19-of-26 against a ceiling of 22 is the expected shape of the win.

### 5.2 The Phase 2 certification, run against the block registry

The def-classification (reload / advance / save-restore / other) certifies
GT, SW, Galway and goto80 roots structurally. Follin's roots classify as
`other` on exactly two def shapes — the loop-start save cells (`zp_30/32`)
and the 3-deep return stack (`m_6B25[zp_6A]`) — i.e. **cursor values stored
as data**, certifiable by transitive closure over save cells, which is R9's
design point arriving as a measurement. Two further findings shaped the rule:

- **The block registry already exists.** `data_decls` records pointer tables
  with `targets` spans and — decisively — blocks with `via: $FB`: the model
  already declares which blocks a pointer walks. Phase 2a certifies against
  that registry, not against a new analysis.
- **At-rest values lie; at-use values are the premise.** Galway multiplexes
  `zp_F6/F7` (observed holding `$051B` — two counters, not an address), so a
  pair's between-role content is junk. This is harmless by construction —
  the cursor faults **at the read**, and a non-pointer role never derefs —
  but it means certification is per value-web (role), not per cell, and the
  dynamic extent check must sample at the deref, not at frame end.

### 5.3 The worked examples, each Z3-proved

All six theorems below are proved over all inputs (`scratchpad` prototype;
they become admitted rules or rule instances when their phase lands).

**Phase 2 — the cursor advance (Ghouls, `case $6858`).** Emitted today:

    ptr0 = ptr_0021_lo ; t0 = (ptr0 + $02)
    t2 = (ptr_0021_hi + (carry(ptr0, $02) | carry(t0, $00)))
    ptr_0021_lo = t0 ; ptr_0021_hi = t2

PROVED `Concat(t2,t0) == Concat(hi,lo) + 2`, so the target spelling is
`seq_v1 += 2` — one cursor advance, no carry, no page. PROVED also that the
page-cross guard (`carry(lo, a)` feeding `unobserved` — all of Galway's
pointer guards) equals "the wide add changed its hi byte": after the lift the
guard is an ordinary bound check on the cursor, or vanishes into the block
extent fault.

**Phase 4 — the carry-chain pack (Angry_Birds, freq).** Emitted today:

    sid.v1.freq_lo[x]:2 = (zext2((w11 + w12 + cflag))
      | (zext2((m_091F[x] + (carry(w11, w12) | carry((w11 + w12), cflag)))) << $08):2):2

PROVED equal (for `cflag <= 1`) to the one wide add
`(zext2(m_091F[x]) << 8) + zext2(w11) + zext2(w12) + zext2(cflag)` — the
byte lanes and both carry terms are the 8-bit ALU's spelling of a single
16-bit `+`. PROVED also `pack(lo(x), hi(x)) == x`, the cancellation that
deletes a `word_pack` whose operands are one word's own halves.

**Phase 5 — the read-back becomes a shadow (Aces_High / Comic_Bakery).**
Emitted today (Aces_High, both cutoff lanes; Commando's freq form identical):

    filter.cutoff_lo:2 = ((filter.cutoff_lo:2 & $00FF):2 | (zext2(h) << $08):2):2

PROVED `((s & $00FF) | zext2(h) << 8) == Concat(h, lo(s))` and the lo-lane
mirror: with the held word in a shadow variable `s`, each RMW is a plain
byte update of `s` followed by a whole-word store — no load of a write-only
register survives.

### 5.4 The shredder: the plan as an executable, failing spec

`tests/test_shred_regmodel.py` (committed with this doc) is the
`test_shred16.py` discipline pointed at this plan: four synthesized players,
one per phase promise, each **building and gating today** (hard asserts), each
canonicality assert `xfail(strict=True)` so a phase that lands flips its test
to XPASS and forces the marker's deliberate removal — the suite cannot drift
past the plan silently, and the plan cannot claim what the suite disproves.

The suite has two kinds of fixture, and the second is what moves failure
detection out of corpus sweeps: **xfail targets** (what a phase must achieve)
and **standing invariants** (what no phase may break — soundness facts the
plan's own gates otherwise only catch at 624-tune cost).

| fixture | kind | pins |
|---|---|---|
| `scratch` | xfail P3 | scratch cell becomes a local, not state |
| `pointer_walk` | xfail P2 | mixed reload+advance deref names a datum, no `mem[..]` |
| `borrow_chain` | xfail P4 | SBC-lane compare becomes one wide compare |
| `lone_lane` | xfail P5 | lone-lane widening owes no register read-back |
| `mux_pair` | xfail P2 | Galway's multiplexed zp pair certifies per role (M2) |
| `cursor_save` | xfail P2 | Follin's save/restore — cursor values as data |
| `writethrough` | xfail P2 | write-through pointer store becomes a bounded table write |
| `g2_store` | xfail G2 | `(zext2(y)+$NN)` store bound, asserted via `addr_bits` |
| `sweep_blit` | **invariant** | a covering blit stays byte-wide — the §7.7 `$CA6E` ord break |
| `hi_first_pair` | **invariant** | the `hi-first` order flag survives every rebuilder |
| `path_persist` | **invariant** | a path-dependent persistent cell (`pos_54EC` shape) stays state |
| `alias_state` | **invariant** | a cell a write-through store can clobber stays memory (M1) |
| `init_livein` | **invariant** | frame 0 reads init's value: the init/state0 coupling holds |

Writing it measured the ladder once more, and honestly in its favour: the
first drafts of `pointer_walk` (pure advance) and `borrow_chain` (lane-paired
SBC) **XPASSed immediately** — framemath already coalesces a pure-advance
pointer pair to `ptr:2 += 3` and already lifts a lane-paired 16-bit subtract —
so both fixtures were hardened to the corpus's actual residue shapes (mixed
defs, borrow-into-different-cells). Two details of the final fixtures are
evidence in themselves: mixing one reload into the advancing pointer splits
the coalesced pair back into `ptr_0002_lo`/`ptr_0002_hi` byte halves, which is
§7.10.5's multi-definition wall in miniature; and the borrow survives spelled
`($01 - (zext2(m_1464) <= zext2(ctr_1462)))` — the census `borrow` class
reproduced from eleven synthesized instructions.

## 6. Review outcomes: adopted corrections and open simplifications

An adversarial review of this plan (complexity-first) produced eight findings.
Five were defects and are folded into the sections above: the Phase 3
`aliased` condition now exists and covers in-bounds computed stores and
certified write extents, not just wide stores (§2 Phase 3 (ii)); Phase 4's
gate makes evaluator support a landing precondition (§2 Phase 4); Phase 1's
gate names its `720_Degrees` exception; the `for`-is-do-while premise is
stated; and §5.1's units and the Automatas ceiling (17, not 23) are corrected.
Three are design simplifications recorded here as decisions to take, not
silently adopted:

- **Defer Phase 2b behind its own census payback.** Phase 3 needs 2a's bounds,
  not the cursor rewrite, so the plan's largest dialect addition moves off the
  critical path — the same deferral test Phase 6 applies to the value-set
  walker. Sharper: the fallback spelling already reads like a cursor
  (`ptr_0002: u16`, `ptr:2 += 3` emit today), so a **block-extent annotation
  on the existing u16 declaration** (`ptr_0002: u16 in m_1500`) plus an
  evaluator range check may deliver the fault-outside-extent semantics for
  one grammar token, against 2b's four new constructs. 2b is built only if
  the census after 3+4 still pays for it.
- **Phase 5 may need no new declaration kind at all.** After Phase 4, the
  held lane of a driver with its own work variables is an ordinary wide state
  field (form (a) takes most of the population); for the remainder, the
  zero-grammar alternative — delete `_widen`'s RMW form and emit the honest
  byte store, paying census `narrow_sink` instead of minting a construct —
  must be costed before a shadow declaration is committed. §7.10.12's own
  framing (the widen "bought rate with a construct no 6502 can execute")
  argues the byte store is the honest spelling. One further obligation either
  way: a register also written through an unresolved `sid.reg[i]` index
  cannot keep a shadow coherent — named refusal, and the shadow's initial
  value must be defined against `framelog`'s `held0` seeding.
- **Fold Phase 0's new instruments into the standing ones.** The store-reach
  sweep is ~20 lines inside the walk `fuse_measure` already does, and the
  `wall`/dispatch count is one more `lift_residue` signature — keeping the
  "two instruments agree store for store" discipline instead of minting a
  third counter population.

Two obligations the review added that stay open in their phases: Phase 2's
certification must be proven closed under **reads** as well as writes (a
counter role reading bytes a cursor rewrite no longer maintains is a
divergence — the `mux_pair` fixture is built to catch exactly that), with a
`role_entangled` refusal for pairs it cannot split; and the 2b extent check
inherits Phase 3's full-length differential discipline, since a cursor
faulting at frame 9,000 is invisible to the 300-frame sweep — with the
corpus-wide `--frames full` cost budgeted before either lands (§7.8's
environment numbers are the reference).
