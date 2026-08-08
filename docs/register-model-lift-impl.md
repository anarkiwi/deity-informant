# register-model-lift: implementation plan (phased, evidence-based)

Status: in execution. Phases 0, 1, 2a and **2b's analysis half (b0, b1, b5)**
are DONE — each carries its landed record and before/after table in its own
section; 2b's rewrite half (b2–b4), 2c and 3 are the next candidates (2c and 3
are dependents of 2a's bounds only, so they need not wait on rung (g)). 2b's
measured work list is **308 webs / 1,782 ⊤ loads over 158 tunes**, and its
largest blocker is not the soundness premise but the declared registry's
coverage of what the pointers walk (§2 2b). The plan was
de-risked against seven family-representative tunes and one corpus-wide sweep
before any phase started. Each phase is mechanical (a committed instrument
produces its work list), bounded (a refusal vocabulary names what it will not
do), and guarded (gates that stop the line rather than reinterpret the goal).
"MUST" is a gate. §5 records the prototypes: the Phase 3 analysis run to zero
oracle contradictions on all seven tunes, the Phase 2 certification run
against the declared block registry, six Z3-proved worked examples, and the
committed `tests/test_shred_regmodel.py` shredder whose per-phase asserts
stay xfail until their phase lands (Phase 1's `sp_spill` has flipped and
passes hard).

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
(`frameval.py:535`), so a state-image read counter sees one echo per write.
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
pointer roots (the row abbreviates: Automatas' ten `m_11xx` RAM pairs are one
entry, and Phase 2a's committed instrument counts **23** roots over the seven —
the 188 loads are exact), every one already in `streams.classify`'s `pointer` vocabulary
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
MUST NOT move the summed census up per tune (Phase 1 corrected the original
"no other class rises" wording: a phase that turns memory into values moves
residue between classes — the byte-lane classes are where a promoted word's
halves land — so the per-tune sum is the law, §3). Post-Phase-1 correction:
while 298 tunes wear refused `sp` fabric the headline is **capped at 326 of
624** regardless of Phases 2–5; Phase 2c owns releasing the cap, and any
headline reported before it lands is read against that ceiling.

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
   too, not just Follin) handled as an ordinary join. (All three figures
   reproduce at HEAD from the committed counter — the seven-tune row is 1 dyn
   form and `switch goto` 0/4/0/2/1/3/3, and 2b's sweep re-measures the corpus
   at 42/31 and 195/141. What does *not* reproduce is the tree they were first
   taken on: a pre-Phase-1 `lift_residue` artifact matching the Phase 1
   before-column bit for bit on every census signature reports **44 / 32** and
   **198 / 142**, so **Phase 1's destacking moved the wall class down by 2
   statements over 1 tune, and `switch goto` by 3 sites over 1 tune** —
   movement its before/after table does not carry because neither is a census
   signature. Small, and in the right direction, but the wall class is Phase
   3's input: that phase re-measures it rather than quoting this line.)
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
built. (Resolved 2026-08-08: the split did its job — 2a's measurement chose
the annotation spelling over the four constructs, and the license for the
extent claim is the evaluator fault under observed extents, never the static
certification; §2 2b.)

## 2. The phases

Ordering is by dependency: 1 clears the stack's spills, 2a certifies the
pointer traffic (producing the bounds two dependents need — the *bound*, not
the rewrite, so **Phase 3 and Phase 2c depend on 2a only** and 2b can land
on its own schedule, §6), 2c finishes the stack (the fabric Phase 1's
destacking cannot remove, provable only with 2a's bounds), 3 promotes
frame-local scratch, 4 coalesces the remaining byte columns, 5 retires the
boundary read-back, 6 re-measures. Every phase ends with the same sweeps
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
  cell: reads/writes (net of the `frameval.py:535` write echo), first-access
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

### Phase 1 — the stack becomes locals (`raw_sp` -> 0-or-refused) — DONE, headline rescoped

The heading's original promise was `raw_sp -> 0`; the gate it shipped under
was "monotone to 0-or-refused", and that is what landed. The post-landing
review (below, and §2 Phase 2c) found the corpus refutes the stronger
headline for destacking alone, so the phase is renamed to what it achieved
and the fabric's removal is scheduled (Phase 2c), not parked.

**Landed**: rung (d0s) in `framestack` — the spill named through `sp` rather
than through its cell — plus two soundness fixes the phase turned up on its way
in, and a per-procedure refusal ledger carried by `lift_residue`.

*What chose the rule.* A corpus classification of every surviving `sp` site
(624 tunes) says the residue is **three shapes and nothing else**: the pull
address `(zext2(sp [+ k]) | $0100)` 1,278 sites / 263 tunes; the update
`sp = sp ± k` 841 / 218; and the `pcall` threading argument 533 / 172 (plus 3
strays). Only the first is a *spill*; the other two are fabric that no rewrite
removes — they leave with `drop_sp` or not at all. So the phase is one rule and
one ledger, and the corpus lead in §0 (Angry_Birds' 16 sites) is representative
of the shape but not of the disposition.

*The rule.* `concretize_stack` folds a push to a constant cell only where one
entry `sp` flowed there; a subroutine reached at two call depths joins to bot
(`structured.sp_flow`) and its spills keep the machine spelling. Rung (d0s)
names such a slot **relatively**: a `_Marks` pass gives every statement an
`(epoch, displacement, aliases)` where an epoch is a run control neither leaves
nor enters, so inside one `sp` is the entry value plus the displacements
written between and a slot is `(epoch, displacement + k)`. `_SpSlot` then
re-asks rung (d0)'s own premises against that relative cell — a store dominates
every read, no control transfer between, no other access may touch it — plus
two of its own: the store must claim free stack space (`k <= 0`; a store above
the live top is the caller's return address, not a spill), and **the procedure
must balance**. The aliases matter as much as the rule: a polished procedure
spells the pull address once into a local (`t0 = (zext2(sp) | $0100)`), and
without following that the rule sees a third of the sites.

*The balance premise is not bookkeeping — it cost eleven divergences.* The
first landing without it moved the gate to 611/623: `frameval`'s `ret` reads
page one for its target where the program moved `sp`, so a promoted slot
deletes the byte the machine returns through (`Boles_Howard/Amazon`,
`Doxx/Absolutely_Fabulous`). Requiring `_sp_state == ("entry", 0)` per
procedure — the plan's own `sp_unbalanced` class — restores it, and
`_sp_state` gained the missing case that a `pcall` handing `sp` back leaves the
walk unable to say where it stands.

*Two soundness fixes, both found by this phase, both pre-existing:*

- **`720_Degrees` (Class B, §7.10.14) is named and closed.** Rung (d0)'s
  `_count` walks into a `mem` node's *address*, so a slot read nested in
  another access's address (`m_CA02[(m_01FA & $07)]`) scored as a read and the
  slot was named — but `_rewrite_expr` did not walk there, so the store became
  a local, `drop_state` deleted the cell under it, and the load read a cell
  nothing writes. One slot, `$01FA`, one bit of `v1.ctrl` at frame 225. The
  counter and the rewriter now walk the same tree. **Gate FP 621 -> 622 clean
  of 623**, and this is the movement §2's gate reserved for `720_Degrees`.
- **`_factor_ifs` could rename two locals into one.** `_pair_names` accepted a
  bijection target the *other* arm also binds, so factoring arms that both
  assign a rung-(d0) slot renamed a carry operand onto the sum it fed
  (`Ames_John/Basket_Case` and eight more). The premise is now stated: a
  renamed arm local may not take a name that arm binds.

**The before/after table** (full 624-tune cache; census at full Songlengths
length, `fuse_measure`/`storage_census` at 1500 frames):

| | before (`c109a13`) | after |
|---|---:|---:|
| **Gate FP, 300 frames** | 621 clean / 623 built | **622 clean / 623 built** |
| — diverged | `720_Degrees` (B), `Rambo` (C) | **`Rambo` (C) only** |
| — refused | `C64_World` (`unobserved $4ED7`) | `C64_World` (unchanged) |
| **Gate FP, review seven, full length** | 7/7 clean | **7/7 clean** |
| census `raw_sp` | 2,653 / 328 tunes | **2,379 / 298 tunes** |
| census `unnamed_addr` | 9,373 / 611 | **9,129 / 611** |
| census `sid_readback` | 1,123 / 466 | 1,049 / 466 |
| census `narrow_sink` | 249 / 139 | 233 / 138 |
| census `hi_byte` / `lo_byte` | 2,254 / 2,172 | 2,328 / 2,248 |
| census `word_pack` / `flag_bit` / `carry_val` | 4,617 / 1,664 / 5,608 | 4,651 / 1,673 / 5,610 |
| census `borrow` / `mod_addr` / `shift_pair` | 902 / 741 / 169 | unchanged |
| **census sum** | **31,525** | **31,112 (-413)** |
| — tunes whose sum fell / rose | — | **92 / 33** (worst riser +4) |
| `storage_census` stack loads / stores | 974 / 889 | **753 / 661** |
| `fuse_measure` `unproven` | 217 | **201** |
| `fuse_measure` `provably_complete` | 487 | **488** |
| `fuse_measure` `looks_complete` / `unnamed` / wide stores | 498 / 105 / 105 | 499 / 105 / 105 |

**The refusal ledger** (`lift_residue --sig raw_sp` now prints it; per tune in
each row's `stack_refusals`). Every program that still carries `sp` carries a
named per-procedure refusal, so the phase's "0-or-refused" holds literally —
but the classes are broader and far more numerous than the plan expected.
Reading the table: `stack:`/`spslot:` rows are per-slot rung refusals (rung
(d0) and (d0s) re-asking their premises), while bare `sp_*` rows are
`drop_sp`'s per-procedure verdicts — `spslot: the procedure's stack effect is
not zero` and `sp_unbalanced` share a premise at two granularities, not one
class counted twice. The ledger also carries refusals on 252 tunes whose
final text has `raw_sp` 0 (e.g. `sp_linked` where a raw call keeps the
machine stack alive but no `sp` spelling survives) — conservative rows, kept
because §3's shrinking-ledger rule needs the baseline to include them:

| class | refusals | tunes |
|---|---:|---:|
| `spslot`: the procedure's stack effect is not zero | 372 | 128 |
| `sp_linked` (a raw call keeps the machine stack alive) | 366 | 307 |
| `sp_unbalanced` (the procedure's stack effect is not zero) | 288 | 201 |
| `stack`: a read is not dominated by a store of the slot | 102 | 52 |
| `spslot`: an unresolvable address may alias the live slot | 101 | 67 |
| `stack`: the slot is not both stored and read in the procedure | 78 | 43 |
| `stack`: an unresolvable address may alias the live slot | 71 | 57 |
| `sp_read` (an access rung (d0) could not destack reads sp) | 58 | 58 |
| `sp_returned` (the procedure returns sp to its caller) | 47 | 47 |
| `spslot`: another resolvable access may touch the slot | 14 | 5 |
| `spslot`: the slot is not both stored and read in the procedure | 10 | 8 |
| `stack`: another procedure may touch the slot | 8 | 6 |
| `sp_callee` | 2 | 2 |
| `spslot`: a read is not dominated by a store of the slot | 1 | 1 |
| `stack`: another resolvable access may touch the slot | 1 | 1 |

**Where the plan and the corpus disagree, the corpus wins, and it says
`raw_sp -> 0` is not reachable by destacking.** Two thirds of the class is
`sp = sp ± k` and the `pcall` threading, which only `drop_sp` removes, and
`drop_sp` is all-or-nothing per *program*: one keeper keeps `sp` everywhere.
307 tunes keep it because a raw `call` keeps the machine stack alive
(`frameval`'s `call`/`ret` move the same register the program reads back), and
201 because some procedure's stack effect is unproven. Measured: if
`sp_linked` were relaxed outright, only **71 tunes / 217 sites** would clear —
so linkage is the widest blocker but not the largest prize, and the real
ceiling is the balance analysis. What would move the number is a stronger
`_sp_state` (it demands the *entry* displacement at every label, `goto`, `ret`
and loop edge, rather than a fixpoint over them) and a rule for the
`sp_linked` case with a checkable "no surviving page-one access" premise. The
review moved both out of "Phase 6 candidates" and into **Phase 2c**, because
the `sp_linked` premise is not free-standing: proving a program's other
accesses stay off page one is exactly the bound Phase 2a's block-rooting
certifies (an access inside a declared block's extent is away from the stack
by that extent), so the sp endgame is a dependent of 2a the same way Phase 3
is — scheduling it before 2a would just re-measure today's 611-tune
unprovability. The doc's
committed bound also drifted honestly: `raw_sp` is **2,653 over 328 tunes** at
`c109a13`, not the 2,604 / 323 recorded above (which predates `#130`).

**Three classes rose, and the rise is the residue moving, not being traded.**
`hi_byte` +74, `lo_byte` +76, `word_pack` +34, `flag_bit` +9, `carry_val` +2
against `raw_sp` -274, `unnamed_addr` -244, `sid_readback` -74,
`narrow_sink` -16. The shape is uniform and readable per tune: a destacked
lo/hi spill pair becomes one 16-bit local, rung (d) fuses it, and its halves
are then read as `trunc1(q:2)` and `trunc1((q:2 >> $08):2)` — two Phase 4
sites replacing two machine-shape ones (`6581_Words_per_Minute`: `raw_sp` 1->0,
`unnamed_addr` 9->8, `hi_byte`/`lo_byte` 2->4 each). 33 tunes rise, by at most
4 sites; 92 fall, by up to 10; the sum falls 413. Under §3's law — the sum is
the metric — that is a pass, and the §2 wording "no other signature rises" is
recorded here as too strong for a phase that turns memory into values: the
byte-lane classes are exactly where a promoted word's halves land, and Phase 4
is where they leave.

**Shredder**: `sp_spill` (a subroutine two call depths reach, spilling
sp-relative) is the Phase 1 fixture — it built, gated and failed its
canonicality assert at `c109a13`, XPASSes now, and its `xfail` marker is
removed in this change. `sp_unbalanced` is the standing invariant: the same
player with the spill spanning a loop, whose stack effect is unproven, keeps
`sp` and its stack-page identity.

**Not done, and why.** `raw_sp` is 2,379, not 0 (above). The `sp_linked`
relaxation was designed and dropped: its sound form needs "the program makes no
page-one access", and 611 of 624 tunes carry an unresolvable address whose
reach the current analysis cannot bound below `$0100`, so the premise is
unprovable for almost every tune that would benefit. `Rambo_First_Blood_Part_II`
(Class C) is untouched, as §4 says.

**Two consequences of the partial outcome, found in review and now owned by
their phases.** First, while 298 tunes wear refused `sp` fabric, R7's
census-zero headline is **capped at 326 of 624** no matter what Phases 2–5
deliver — the cap is stated at R7 and is Phase 2c's number to release.
Second, the surviving stack traffic (202 tunes, 661 page-one stores, and 201
of those tunes declaring 3,292 scratch fields — 35% of Phase 3's corpus-wide
prize) is a spurious wall for Phase 3 as originally specified: the kept
spelling `(zext2(sp [+ k]) | $0100)` resolves to no base/idx form, so
`store_reach` falls back to `(0, UNRES, addr_bits, w, 0)` — an interval
**from zero**, because `addr_bits` keeps only the may-set upper bits and
discards the OR's guaranteed bit 8 — and `overlaps` then says every such
push threatens every zero-page cell (verified against the committed code:
`store_reach` on the push spelling returns reach `$0000..$01FF` and
intersects `zp_A0`). The fix is a floor, specified in Phase 3 (ii).

The phase specification, for the record: `framestack` finishes. Balanced
push/pop and call linkage become locals/params; the work list is the unbalanced residue — **16 of the seven
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

The keystone phase, split so analysis lands before dialect — and grown a
third half in review: 2c spends 2a's bounds on the stack fabric Phase 1
could not remove.

#### 2a — certification (analysis only, no text change) — DONE

**Landed**: `deity_informant/ptrcert.py` — the authority on block-rooting — plus
`frameproc.addr_floor` (the Phase 3 (ii) must-set-bits floor, arriving early
because 2a needed it), the certification folded into `storage_census` as a row
and a totals block, `fuse_measure.root_cells` delegating to `ptrcert` so the two
instruments cannot drift on what a pointer root *is*, and 24 hermetic tests in
`tests/test_ptrcert.py` over the shredder's own fixtures. Per §6's third review
decision this is a mode of the standing instruments, not a third counter
population; the analysis earns its own module because Phase 2b, 2c and 3 all
consume it.

*What chose the rule.* The certification is stated over the **emitted program**,
not the model, because that is where the ⊤ residue lives and what 2b would
rewrite: a root is a cell the emitted text still derefs through an address
naming no datum — exactly `storage_census`'s `ptr_roots`, now derived by
`ptrcert.sites` and cross-checked per tune (`cert_agree`, 0 disagreements over
624). Each definition is read through `Defs._lookup`'s own chase, which a label,
a call and a cyclic body already stop. Three premises, each re-asked in
sufficient form rather than read off `streams.classify`'s union (the §7.10.11
`_counter_range` discipline), each recorded per root in the proof record:

- **definitions closed** — every store that may reach the pair is a table-row
  reload (rung (f)'s premise re-asked per leg against the *declared* registry:
  a table `declarations` already gave the matching `lo`/`hi` role, a declared
  partner and no play-written offset, the row index bounding the block set), an
  in-block advance (one term of the add *is* the cell at that width, every other
  a constant, a carry the lo lane produced, or a value bounded inside a byte),
  or a save/restore closed transitively over held locations. Else
  `ptr_uncertified`.
- **reads closed** — no read of the pair's bytes observed outside its own web.
  §6's obligation, and it bites: a counter role, an end-of-block byte compare or
  a page-alignment test reads a byte a block+offset cursor does not maintain.
  Else `role_entangled`; where only a local the chase could not read through may
  carry the pair, the weaker `role_opaque`.
- **extent declared** — every row the root may reload, and its post-init value,
  lands inside a declared datum, under a bounded advance. Else
  `ptr_extent_open`.

*Two things the analysis needed before it could measure anything.* First,
`store_reach`'s interval **from zero** — the Phase 1 review finding that Phase 3
(ii) schedules a floor for — made every page-one push an alias of every
zero-page pointer pair, so the first run refused every Follin and SID-Wizard
root on aliasing alone. `frameproc.addr_floor` is that floor, landed here and
unused by any rewrite: must-set bits, mirroring `addr_bits`' may-set ones, so
`(zext2(sp) | $0100)` reaches `[$0100, $01FF]` and not `[$0000, $01FF]`. Second,
a store *through* a certified root is bounded by that root's own blocks — the
certification spending itself — without which the 12 write-through players are
each other's aliases and none of them certifies.

**The coverage, corpus-wide** (`storage_census --frames 1500`, 624 tunes, 0
refusals; roots are pairs, loads and stores are ⊤ access sites):

| | roots | ⊤ loads | ⊤ stores | tunes |
|---|---:|---:|---:|---:|
| pointer roots the emitted text still derefs | **1,044** | 6,653 | 38 | 511 |
| — **block-rooted** (definitions closed) | **441 (42.2%)** | 1,939 (29.1%) | 8 | 145 all-rooted |
| — block-rooted *and* reads closed | 307 (29.4%) | 1,142 (17.2%) | 8 | 91 |
| — **cursor-ready** (every premise) | **107 (10.2%)** | 157 (2.4%) | 1 | 9 |
| ⊤ accesses carrying no pointer root at all | — | 59 | 67 | 50 |

The 38 rooted ⊤ stores are exactly Phase 0's `ptr_writethrough` count, from a
different walk — the two instruments agree store for store on the write-through
class as well as on the roots.

**Definition kinds** over the 1,044 roots — 4,372 definitions: `reload` 1,776,
`save_restore` 1,034, `advance` 504, `other` 1,058. The plan's three cursor
shapes cover **76%** of the reaching definitions, which is the R9 framing
confirmed; the `other` quarter is five shapes and nothing else, and naming them
is 2a's real work list:

| shape | defs | what it is |
|---|---:|---|
| `computed` | 480 | a pointer built from arithmetic no cursor spells (zero-page reuse as scratch: `zp_FB = (a & $7F)`) |
| `held_open` | 261 | a save location the closure could not admit: something other than a cursor also writes it |
| `block_read` | 133 | **a cursor loaded from the block a cursor walks** — the script interpreter's own jump/call operand (Follin, Galway) |
| `low_held` | 96 | the cursor saved and restored through page one — Phase 1's surviving `sp` fabric holding a cursor |
| `opaque` | 88 | the definition is a procedure parameter or a call's return; the chase is intraprocedural |

**The refusal ledger** (per root; a root may wear a reads class and a defs class
at once):

| class | roots | tunes |
|---|---:|---:|
| `ptr_uncertified` | 603 | 366 |
| `role_entangled` | 418 | 302 |
| `ptr_extent_open` | 311 | 151 |
| `role_opaque` | 7 | 7 |

`ptr_uncertified` splits 316 roots refused on `other` definitions alone, 193 on
an alias store alone (2,751 stores the reach analysis cannot keep out of a pair,
even with the floor), 94 on both. The 1,296 foreign read sites over 418 roots
are the `role_entangled` population; only 7 roots refuse on the opaque path, so
the position-blind taint fallback costs almost nothing.

**What each premise costs, measured** — roots that would be cursor-ready if that
one premise were discharged and nothing else changed:

| premise | roots holding | roots blocked by it alone |
|---|---:|---:|
| advance bounded | 1,014 | 2 |
| rows declared | 642 | 17 |
| reads closed | 619 | 23 |
| definitions closed | 441 | 50 |
| **post-init value declared** | **328** | **107** |

The single largest blocker is the *post-init* premise, and it is a
conservatism rather than a fact about the corpus: a pair whose post-init image
is `$0000` or `$FFFF` (a cell init zeroed, written before it is ever read) has
no declared block for a value it never derefs. Discharging it needs precisely
Phase 3's written-before-read analysis at the frame boundary — so **Phase 3
returns 2a's favour**: cursor-ready would go 107 -> 214 on that premise alone.

**The seven-tune table, and the plan corrected.** The review set was expected to
be 15/15. It is **23 roots, 12 block-rooted, 0 cursor-ready**:

| | Commando | Comic_Bakery | Automatas | Aces_High | Angry_Birds | Ghouls | Agent_X_II |
|---|---:|---:|---:|---:|---:|---:|---:|
| pointer roots | 0 | 4 | 11 | 1 | 1 | 3 | 3 |
| block-rooted | 0 | 0 | **10** | 0 | 0 | **2** | 0 |
| cursor-ready | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ⊤ loads | 0 | 23 | 26 | 6 | 39 | 52 | 42 |
| defs: reload/advance/save/other | — | 0/6/9/15 | 6/3/8/4 | 2/0/1/4 | 4/0/8/2 | 0/12/5/2 | 0/6/3/6 |
| refusals | — | unc 4, ent 4 | unc 1, ext 10, ent 3 | unc 1, ent 1 | unc 1, ent 1 | unc 1, ent 3 | unc 3, ent 3 |

Three corrections the corpus forces on the plan, all folded into §0/R2/§5.2
above:

1. **"15 pointer roots" was the §0 table's row, not a count.** The §0 row
   collapses Automatas' ten `m_11xx` RAM pointer pairs into one entry
   (`RAM cells m_11xx`); the emitted programs carry 23. The 188 ⊤ loads
   reproduce exactly, so it is the root count that was abbreviated, not the
   traffic.
2. **§5.2's "GT, SW, Galway and goto80 certify structurally" is measured
   false for GT, SW and Galway.** Aces_High's `zp_FB` is not only a cursor: the
   player spills a register into it (`ptr_00FB_lo = x` … `x = ptr_00FB_lo`) and
   builds a masked value in it, so it is Galway's own multiplexing appearing in
   a tracker export. Angry_Birds' pair is saved and restored **through page
   one**. Only goto80 certifies structurally, and its ten RAM pairs are the
   corpus's cleanest cursors.
3. **§5.2's "Follin's roots classify as `other` on exactly two def shapes" is
   one shape short.** The loop save cells (`zp_30/32`) and the 3-deep return
   stack (`m_6B25[zp_6A]`) *do* close under the transitive save closure — that
   half of R9's design point arrives as predicted, and it is why Ghouls' `zp_21`
   and `zp_25` are block-rooted. The third shape is `block_read`: `zp_23 =
   mem[(zp_23:2 + zext2(y)):2]`, the interpreter reading its **next cursor out
   of the script it is walking**. That is a script-language jump, and no
   declared-registry premise bounds it — 133 definitions over the corpus.

**Gates.** All five held, and the phase is read-only so the first is the point:

| gate | result |
|---|---|
| emitted text byte-identical corpus-wide (sha256 per tune, branch vs `40b33d7`) | **624 / 624 identical, 0 build errors either side** |
| full suite | **2,357 passed, 495 skipped, 8 xfailed**; the four Phase 2 shredder fixtures stay xfail |
| `gate_sweep --frames 300` | **622 clean / 623 built**, diverged `Rambo` (C) only, refused `C64_World` |
| `lift_residue` | census sum **31,112**, `raw_sp` **2,379 / 298**, every signature unchanged |
| `fuse_measure` / `storage_census` | `unproven` **201**, `provably_complete` **488**, wide stores 105/54; every pre-existing census total identical row for row |

**What the number says about 2b.** The corpus pays for the cursor dialect at
**42% of roots and 29% of ⊤ loads today**, and for the *fault-outside-extent*
semantics at 10% of roots. That is below the level at which four new grammar
constructs pay for themselves, and it is direct evidence for §6's first review
decision: **build the block-extent annotation on the existing `u16` spelling
(`ptr_0002: u16 in m_1500`) plus an evaluator range check, not 2b's four
constructs.** The measured reason is specific and repeats across families —
**418 of 1,044 roots have their bytes read outside the deref**, so a cursor that
cannot spell `lo(seq)`/`hi(seq)` forces the fallback on 40% of the population
regardless of how well its definitions certify. A one-token annotation on the
existing spelling keeps the byte lanes and buys the extent fault; that is the
2b design the measurement supports, and the shredder's `pointer_walk` fixture is
the type specimen (it certifies on definitions, and refuses on the `(lo & $18)`
alignment test that decides its reload).

**Superseded in part (2026-08-08, §6's reframe entry).** The paragraph above
prices the dialect by static-certification coverage, and that was never the
license — R9 puts the extent claim in the evaluator fault, and the control
layer's observed-primary discipline (docs/corpus-status.md: artifacts carry
the trace-observed sets, guards fault outside them, static analysis demoted
to certification accounting) is the standing precedent. What stands is the
annotation-over-constructs conclusion, which is now 2b's dialect; what does
not is the deferral. 42.2% is the certification tally's opening row, and the
respecified 2b ships the spelling corpus-wide, guard-live where uncertified.

**What it supplies to 2c and 3.** 2c's `sp_linked` relaxation needs "an access
certified into a declared block's extent is bounded away from page one by that
extent". 2a supplies it in two pieces, and the second is the bigger one: the
per-root block list (1,939 ⊤ loads on block-rooted roots now carry a declared
extent, none of it in page one) *and* `frameproc.addr_floor`, which by itself
gives the surviving `(zext2(sp [+ k]) | $0100)` push the interval
`[$0100, $01FF]` instead of `[$0000, $01FF]`. Phase 1's measurement was that the
`sp_linked` premise is unprovable for 611 of 624 tunes because "any unresolvable
address may reach `$0100..$01FF`" — half of that unprovability was the missing
floor, not the missing certification. The `low_held` shape (96 definitions) is
the traffic in the other direction and 2c owns it: a cursor that lives on the
machine stack keeps `sp` alive and refuses block-rooting at once. Phase 3 gets
the reach bounds for its `aliased` condition (a certified write extent is a
declared block span, which is what §2 Phase 3 (ii) asks for), gets
`addr_floor` as the floor that section makes mandatory, and is **owed** the
post-init premise in return.

**Not done, and why.** No text moved and none was meant to; the shredder's four
Phase 2 fixtures stay `xfail(strict=True)` because 2b did not land. The
certification is intraprocedural (88 `opaque` definitions are a call's or a
parameter's), the reads closure falls back to a position-blind taint where the
chase cannot read a local (7 roots), and the `block_read` shape — a cursor read
out of the block a cursor walks — is named and counted but not certified: doing
so means enumerating a const block's words as a target set, which is a rule 2b
should decide it wants before 2a builds it.

#### 2b — the unified spelling under observed extents (respecified 2026-08-08)

The four-construct cursor dialect and the certified-roots-only rewrite are
superseded (§6's reframe entry records why). The license for the extent claim
was never the static proof: R9 places it in the evaluator fault, and the
control layer already runs exactly this discipline corpus-wide
(docs/corpus-status.md's observed-primary flip: committed artifacts are the
trace-observed sets, runtime guards fault outside them, static analysis is
demoted to certification accounting, and run-to-recurrence closure certifies
guards for the infinite run). 2a's 42.2% priced the dialect by the accounting
column; under the correct criterion the spelling ships corpus-wide, guard-live
where uncertified. One constructor set covers every family because every
measured vocabulary closes single-digit — 3 `sp` shapes, 3 wide-store shapes,
5 `other`-def shapes, 2 pack skeletons, 3 carry roles, 1 raw dyn form on the
seven — the families compose the same constructors at different depths, and no
per-family (let alone per-tune) knowledge appears anywhere below.

**The dialect: two grammar productions, no new statement kinds.** A
declaration clause — `ptr_0021: u16 in m_7338, m_7401` (the extent list, per
web, per program) — and indexed access through the annotated variable:
`ptr_0021[y]`, `ptr_0021[y]:2`, `ptr_0021[y] = v`, replacing the
`mem[(ptr:2 + zext2(y)):2]` spelling. Everything else already spells: advance
`ptr:2 += k` and reload `ptr = T[i]` emit today, byte reads of the pointer
are the existing trunc extracts, a `block_read` is `ptr = ptr[k]:2`, and a
save/restore is a u16 move. `frameval` checks every access through an
annotated variable against its extent and faults outside it (fault kind
`extent`, the `unobserved` machinery). At-rest junk in a multiplexed cell is
harmless by construction because the check is at the use (§5.2 M2).

**The pipeline (b0–b5).** Each step is a committed instrument or a rung with
its inputs, rule, refusal classes and gate; nothing below waits on a design
decision.

- **(b0) Observed extents become a committed artifact.** *(LANDED; the
  parenthetical below is measured-false — see the b0/b1/b5 record.)* The
  census sweep already resolves every deref concretely; per web it now records
  the set of declared data those derefs touched, attributing addresses outside the
  registry through `data_decls`' existing `via:` discovery (the registry
  already declares blocks a pointer walks). An address neither declared nor
  via-attributable ledgers `extent_unmappable` and the web keeps today's
  spelling. The artifact is keyed like the census (cache-relative identity,
  per subtune) and records its horizon. MUST: every gate run's horizon ≤ the
  artifact's recorded horizon, checked per tune — so an `extent` fault inside
  gating is an instrument defect and stops the line, while a fault past the
  recorded horizon is the claim boundary working, exactly `unobserved`'s own
  discipline.
- **(b1) Lift eligibility is a `ptrcert` column, separate from
  certification.** *(LANDED; the `held_open` and `opaque` counts below are
  corrected in the b0/b1/b5 record — `web_alias` 287 is exact.)* A web lifts
  iff: (i) **web closed** — no unresolvable store may reach the pair
  (`store_reach` with `addr_floor`); refusal
  `web_alias`, 287 roots today (2a's 193 alias-alone + 94 both). This is the
  only premise doing soundness work — the rename is semantics-preserving
  exactly when every def is known; everything else in this phase is
  spelling, and Gate FP replays every build behind it. (ii) **defs
  expressible** — every def is a reload, an advance, a save/restore closing
  through holds whose every writer carries an admitted pair value (2a's
  transitive closure, relaxed from "no other writer" to shared holds, which
  is where `held_open`'s 261 defs either admit or refuse), a `block_read`
  (`ptr = ptr[k]:2`), or a `computed` def whose lo/hi legs fuse at one seat
  (rung (d)'s premise, emitted as one word expression); anything else
  refuses `def_unliftable` — today's 88 `opaque` defs land here until the
  params/returns vocabulary admits them, a ledgered precision item, not a
  design question. (iii) **uses expressible** — deref, store-through, byte
  extract, word compare; a web reachable only through a local the chase
  cannot read refuses `web_opaque` (the 7 `role_opaque` roots). (iv) **holds
  off the machine stack** — `low_held` webs (96 defs) keep today's spelling,
  refusal `low_held`, and re-enter after 2c removes the fabric. Everything
  2a called a *certification* refusal stops blocking: `role_entangled` byte
  reads spell as extracts (those sites move from `unnamed_addr` to
  `hi/lo_byte` in the census — the sum still falls, derefs outnumber foreign
  reads 6,653 to 1,296, and the pack forms among them are Phase 4's);
  unbounded advance is what the guard is for (`ptr_extent_open` survives
  only as accounting); and the post-init premise — 2a's largest single
  blocker, 107 roots — dissolves at lift, because the guard checks at the
  deref and a value never deref'd faults nothing.
- **(b2) One rewrite rung** (rung (g), in `frameproc`): per eligible web,
  rename the pair to one u16 variable, rewrite each def by (ii)'s table and
  each use by (iii)'s, attach (b0)'s extent to the declaration, and rewrite
  write-through stores identically (the 38 sites over 12 tunes). The cell
  leaves `state { }` iff no other web keeps it — another role in the cell is
  another variable (M2), which is the whole of "multiplexing" once webs are
  named.
- **(b3) Certification stays 2a's machinery, as the accounting tally.**
  `ptrcert` gains the static extent enumeration: reload targets from the
  declared row-index bound, and `block_read` targets as a least fixpoint
  over the finite registry — E₀ = the web's reload and post-init blocks;
  each round adds every declared datum containing any 16-bit little-endian
  value readable at any byte offset of the constant bytes of blocks already
  in E; monotone over a finite registry, so it terminates. A block some
  store reaches is not constant: refuse `extent_mutable` (certification
  only — the lift and its guard stand). A root is **extent-certified** when
  the fixpoint equals the observed extent, or when run-to-recurrence closure
  covers the infinite run (`--close`; at the control layer the same
  discipline took 20 certified sites to 133). The per-tune tally
  (certified / guard-live) is the `--sound` pattern. The any-word rule is
  deliberately coarse — an over-wide fixpoint leaves a root guard-live,
  never wrong — and offset-aware refinement is a ledgered precision item.
- **(b4) Evaluator cost is measured, not feared.** The extent check is one
  membership test per annotated access against a per-web block list (short,
  per 2a's records). The 300-frame corpus sweep and the seven full-length
  sweeps run before and after; the wall-clock delta lands in this section's
  before/after table and MUST stay under 2× (docs/cycle-times.md the
  reference) — discharging §6's budget obligation with numbers.
- **(b5) The work list precedes the rewrite.** *(LANDED: 308 webs / 1,782 ⊤
  loads over 158 tunes; the bound below stood, the binding constraint did
  not — see the record.)* b1's column prints, per tune,
  the eligible webs and their site counts *before* rung (g) runs; the phase
  commits to that measured target, not a hope. Quotable today only as
  bounds: ≤ 1,044 roots, of which ≥ 287 refuse `web_alias` and parts of the
  `low_held`/`opaque` populations refuse (iv)/(ii). The `web_alias` ledger
  (2,751 unresolvable stores) is simultaneously the direct pricing input for
  Phase 6's value-set walker, whose deferral test now runs in reverse.

Gates: full `gate_sweep` (300f corpus + seven full-length), movement only on
tunes whose text changed, gating same or better; **zero `extent` faults in
any gate run** (impossible-by-construction under b0's horizon rule; one is
stop-the-line); census `unnamed_addr` down by the rewritten deref count and
`carry_val` down (the page-cross role (ii) collapses, §5.3's proof), per-tune
sum down (§3's law); triage `unproven` MUST NOT rise; canonical fixpoint and
`dumps(loads(t)) == t` over the two new productions; the refusal ledger
(`web_alias`, `def_unliftable`, `web_opaque`, `low_held`,
`extent_unmappable`, `extent_mutable`) counted per tune, a shrink without a
rule change a finding (§3).

Shredder: the four standing Phase 2 fixtures are this phase's flips
(`pointer_walk`, `mux_pair`, `cursor_save`, `writethrough` — markers removed
in the landing change), and 2b owes three more with it: `block_read` (a
script-jump cursor lifts; its enumeration fixpoint certifies it or the guard
carries it), `extent_guard` invariant (an access past the recorded extent
faults at evaluation), `web_alias` invariant (an unresolvable store into the
pair refuses the lift and the machine spelling survives).

What 2b hands its dependents: Phase 3 (ii)'s certified write extents now
exist for every lifted write-through, not only the cursor-ready 10%; 2c's
`sp_linked` premise gains every lifted web (an access through an annotated
variable is bounded off page one by its extent); Phase 4 inherits the
extract sites the entangled reads produce, as counted above.

##### 2b analysis half (b0, b1, b5) — DONE, no text change

**Landed**: `deity_informant/ptrextent.py` (b0) — per web, the declared data its
derefs were observed to touch, with the horizon rule as a runnable check — plus
b1's lift-eligibility column inside `ptrcert` (`LIFT_REFUSALS`, per-root
`eligible`/`lift_refusals`/`lift_defs`, and a shape on every definition record),
b5's work list in `storage_census` (printed per tune, and written as
`out/ptr_extents.json`, keyed like the census and carrying each row's horizon),
`gate_sweep --extents` (b0's MUST as a gate that stops the line), and an optional
read-only address `probe` in `frameval` so a deref's concrete address is charged
to the web whose text spelled it. 39 hermetic tests (`tests/test_ptrextent.py`
plus b1's in `tests/test_ptrcert.py`). Per §6's third review decision the new
counters are modes of `storage_census`, not a third population, and `ptrcert`
stays the single authority on what a pointer web *is*: 2a's coverage, def-kind,
shape and refusal totals reproduce from this run row for row.

**Split on purpose, one level down from 2a.** b5's own rule is that the column
prints the measured target *before* rung (g) runs, so no emitted text moved and
the four Phase 2 shredder fixtures stay `xfail(strict=True)`. §R9 records that the
2a/2b split "did its job"; this repeats it inside 2b.

*What chose the rule — b0.* Attribution was the only real choice, and it was
between guessing and knowing. A pair's value at rest is junk between roles
(§5.2 M2), so reading an extent off the cell is a guess; the address a deref
*computes* is not. `frameval` therefore gained a compile-time probe: it sees each
computed address node once, asks `ptrcert.root_cells` which web spelled it, and —
only where a web did — wraps that address closure to record what it resolves to.
A site carrying no root gets its own closure back, so the default path builds the
identical program it did before, and the two full censuses — run concurrently
under the same load — differ by 0.9% of wall (411.8s -> 415.4s). A write-through
store is a deref too, so the store address is probed at the store's width.

*What chose the rule — b1 (ii).* Rung (d) has already run by the time a frame
program exists, so a lo/hi pair that *could* fuse at one seat already did: a
surviving byte-lane `computed` def is precisely one rung (d)'s own premise
refused. "A computed def whose lo/hi legs fuse at one seat" is therefore decidable
by reading the definition's role — `word` spells as one word expression, a lone
lane does not — with no second fusion analysis to write or to keep in step. And a
hold keeps its memory identity under the lift (`ptr:2 = m_H:2` spells whatever
wrote `m_H`), so the plan's relaxation "to shared holds" lands as: a hold's writer
refuses only where the chase cannot read it at all (`opaque`, so the analysis
cannot say the hold is written as a pair) or it comes off page one (`low_held`,
premise (iv)); a value read out of a walked block or computed whole admits.

**The coverage, corpus-wide** (`storage_census --frames 1500`, 624 tunes, 0
refusals; the 2a rows are from the same run and match §2 2a exactly):

| | webs | ⊤ loads | ⊤ stores | tunes carrying ≥ 1 |
|---|---:|---:|---:|---:|
| pointer webs the emitted text derefs | **1,044** | 6,653 | 38 | 511 |
| — 2a block-rooted / cursor-ready, for contrast | 441 / 107 | 1,939 / 157 | 8 / 1 | 182 / 63 |
| — **b1 eligible** (premises (i)–(iv)) | **524 (50.2%)** | **2,654 (39.9%)** | 8 | 207 |
| — **b0 extent-mapped** (observed, all in the registry) | **645 (61.8%)** | — | — | 414 |
| — **b5 work list** (eligible ∧ mapped) | **308 (29.5%)** | **1,782 (26.8%)** | **0** | **158** |

**b1: what each premise costs, measured** — the 2a table's shape, over the lift's
own four premises. 520 webs refuse; 392 refuse under exactly one class, so 128
wear two or more:

| premise | webs holding | webs blocked by it alone | class |
|---|---:|---:|---|
| (i) web closed | 757 | **241** | `web_alias` |
| (ii) defs expressible | 873 | 62 | `def_unliftable` |
| (iii) uses expressible | 892 | 54 | `web_opaque` |
| (iv) holds off the machine stack | 979 | 35 | `low_held` |

**The refusal ledger**, per class, per tune (a web may wear several):

| class | webs | tunes |
|---|---:|---:|
| `extent_unmappable` (b0) | **399** | 188 |
| `web_alias` (b1 i) | **287** | 132 |
| `def_unliftable` (b1 ii) | 171 | 153 |
| `web_opaque` (b1 iii) | 152 | 135 |
| `low_held` (b1 iv) | 65 | 54 |

`web_alias` is **287, exactly the plan's predicted 287** (2a's 193 alias-alone +
94 both) — the one premise doing soundness work costs precisely what §2 2b said
it would. Per definition, over 2a's 4,372: **3,876 admit (88.7%)**, 390 refuse
`def_unliftable`, 106 refuse `low_held`. The `def_unliftable` 390 decompose as
272 `computed` lone byte lanes, 88 direct `opaque` definitions, and 30 hold
writers the chase could not read; the `low_held` 106 as 96 direct and 10 hold
writers. Of the 480 `computed` definitions **208 are the word role rung (d)
already fused**, which is the (ii) rule paying for itself.

**b0: the observed extents.** 1,044 webs, 1,036 of them deref'd inside the
1,500-frame horizon, **282,776 distinct addresses over 4,893 declared blocks**;
726 webs carry at least one block the registry itself attributes to them through
`via:`. 78,510 addresses (27.8%) land in no declared datum, and they split almost
evenly into two different repairs: **39,442 (50.2%) are `short`** — the deref ran
off the end of a block that same web walks, by up to 6,979 bytes — and the rest
are `foreign`, landing where the registry declares nothing at all. Only 58 of the
399 refusing webs refuse on short overruns alone.

**Four corrections the corpus forces on the plan**, all marked in the b0/b1/b5
bullets above:

1. **b0's parenthetical "the registry already declares blocks a pointer walks" is
   measured-false as a covering claim.** `datadecl`'s `via:` discovery declares the
   *anchor* blocks — the pair's post-init word and its reload-table rows — not the
   extent the pointer walks. It is the right mechanism (726 of 1,044 webs get a
   via-attributed block from it) and it is why GT's `zp_FB`, SW's `zp_FE` and
   Follin's script cursors map (5 of the 6 at 1,500 frames, 6 of 6 at full
   length); but a web whose rows are computed rather than reloaded gets one anchor
   and nothing else, which is Galway and goto80 exactly (4 and 10 refusing webs).
   `extent_unmappable` is not a rare residue class: **it is the largest single
   blocker in the pipeline at 399 webs**, ahead of `web_alias`'s 287.
2. **b5's binding constraint is b0, not b1.** The bound "≤ 1,044 roots, of which
   ≥ 287 refuse `web_alias`" holds exactly, but b1 alone leaves 524 webs and the
   extent map removes **216 of them**. The measured target is 308 webs / 1,782 ⊤
   loads over 158 tunes, and 110 tunes lift every web they carry.
3. **b1 (ii)'s "today's 88 `opaque` defs land here" is a third of the class.**
   `def_unliftable` is 390 definitions; the `computed` lone byte lane (272) is the
   larger population and the one with a rule attached — it is rung (d)'s refusal
   read back, so strengthening rung (d) is what moves it, not a params/returns
   vocabulary. Likewise `held_open`'s 261 defs do not split evenly: **221 admit,
   40 refuse** (30 `opaque` writers, 10 `low_held`).
4. **The write-through class reaches the work list at zero sites.** 22 webs carry
   the 38 rooted ⊤ stores; 2 of them are b1-eligible (`Counterforce` `$1E57`,
   `Archon` `$00FD`, 8 stores between them) and **both refuse
   `extent_unmappable`**, so b2's "rewrite write-through stores identically (the
   38 sites over 12 tunes)" has no targets until the extent gap closes. Phase 3
   (ii) should not expect certified write extents from 2b's first rewrite.

**The seven-tune table** (1,500 frames; the full-length column is the sensitivity
check §3 asks for, and the registry is floored by the same observation, so the
extents *widen* with the horizon):

| | Commando | Comic_Bakery | Automatas | Aces_High | Angry_Birds | Ghouls | Agent_X_II |
|---|---:|---:|---:|---:|---:|---:|---:|
| webs | 0 | 4 | 11 | 1 | 1 | 3 | 3 |
| b1 eligible | 0 | 1 | **7** | 0 | 0 | 3 | 3 |
| b0 extent-mapped | 0 | 0 | 1 | 1 | 1 | 2 | 3 |
| **b5 work list** | 0 | 0 | 0 | 0 | 0 | **2** | **3** |
| — its ⊤ loads / blocks | — | — | — | — | — | 28 / 2 | 42 / 3 |
| b1 refusals | — | unl 3, opq 3 | unl 1, opq 3 | unl 1, opq 1 | low 1 | — | — |
| b0 refusals (`extent_unmappable`) | — | 4 | 10 | — | — | 1 | — |
| at `--frames full`: webs / work | 0 / 0 | 4 / 0 | 13 / 0 | 1 / 0 | 1 / 0 | 3 / **3** | 3 / **3** |

Aces_High and Angry_Birds are the exact mirror of Automatas: their single web maps
perfectly and refuses b1 — GT's `zp_FB` under (ii) and (iii) (2a's register spill
is a `computed` lone lane, and a local the chase cannot read carries the pair),
SW's `zp_FE` under (iv), saved through page one — while goto80 has seven of its
eleven webs eligible and ten of them unmappable. The two halves of the pipeline
are independent by construction and the corpus proves it tune by tune, which is
why both had to be measured before rung (g) was written.

**Gates.** All seven held, and the phase is read-only so the first is the point:

| gate | result |
|---|---|
| emitted text byte-identical corpus-wide (sha256 per tune, branch vs `f720d3f`) | **624 / 624 identical, 0 build errors either side** |
| full suite | **2,399 passed, 492 skipped, 8 xfailed**; the four Phase 2 shredder fixtures stay xfail |
| `gate_sweep --frames 300` | **622 clean / 623 built**, diverged `Rambo` (C) only, refused `C64_World` |
| Gate FP, review seven, full Songlengths length | **7 / 7 clean** |
| `lift_residue` | census sum **31,112**, `raw_sp` **2,379 / 298**, every signature and every stack-refusal row unchanged |
| `fuse_measure` / `storage_census` | `unproven` **201**, `provably_complete` **488**, wide stores 105 / 54 in the same four shapes; **every pre-existing census total and every one of 624 rows identical field for field** |
| b0 horizon MUST (`gate_sweep --frames 300 --extents`) | **0 of 623 tunes outran the artifact**; the deliberate violation (the seven at full length against a 1,500-frame artifact) exits non-zero, naming all 7 |

**Not done, and why.** Rung (g) is not written and no emitted text moved; b2, b3
and b4 are untouched, and the four Phase 2 fixtures stay `xfail(strict=True)`
because that is what "the work list precedes the rewrite" means. `extent_mutable`
is b3's class and is unimplemented — b0 records observation, not the static
fixpoint. The extent artifact is recorded at 1,500 frames, which covers the
300-frame corpus gate for all 623 built tunes but **not** §3's full-length review
gate (the seven run 3,450–16,150 frames): b2 must record the artifact at
`--frames full` before it lands, and the seven's own full-length artifact, run
here, passes that gate cleanly. Finally, the analysis is 2a's — intraprocedural,
with the position-blind taint fallback and the same `_stores_into` width filter —
so `web_opaque`'s 152 webs and the 88 direct `opaque` definitions remain ledgered
precision items rather than design questions.

#### 2c — the stack fabric leaves (`raw_sp` -> 0, scheduled)

Phase 1's review finding made a phase: two thirds of the surviving `raw_sp` class is
`sp = sp ± k` and the `pcall` threading, removable only by `drop_sp`, which
is all-or-nothing per program and blocked by `sp_linked` (307 tunes) and
`sp_unbalanced` (201). Two rules, both with premises 2a supplies or Phase 1
named:

- **The `sp_linked` relaxation.** Sound form: a raw `call`'s linkage may be
  dropped where the program makes no surviving page-one access other than
  through `sp` itself. Unprovable today for 611 of 624 tunes (any
  unresolvable address may reach `$0100..$01FF`); after 2a, an access
  certified into a declared block's extent is bounded away from page one by
  that extent, so the premise is computed per tune from 2a's records plus
  the Phase 3 (ii) reach vocabulary. Refusal class stays `sp_linked` for
  tunes where an uncertified access survives. Phase 1's measurement bounds
  the direct prize (71 tunes / 217 sites clear on relaxation alone) — the
  real yield is the compound with the second rule, re-measured when 2a's
  certification coverage is known.
- **The balance fixpoint.** `_sp_state` currently demands the entry
  displacement at every label, `goto`, `ret` and loop edge; the rule becomes
  a worklist fixpoint over those edges (the same discipline as Phase 3's
  forward analysis), so a procedure whose displacement provably returns to
  entry on every path balances even where an interior edge holds a nonzero
  displacement. `sp_unbalanced` remains the refusal for genuine imbalance.

Gates: full `gate_sweep`, no verdict regression; census `raw_sp` monotone
down, sum down; the refusal ledger MUST NOT grow without a rule change (§3);
R7's census-zero cap (326, stated there) is this phase's number to release —
re-measured at landing. The `sp_spill`/`sp_unbalanced` shredder fixtures
stand; 2c owes one more: a balanced-by-fixpoint procedure (interior nonzero
displacement, entry-balanced on every path) whose spill destacks.

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

  **The vocabulary needs a floor first (Phase 1 review finding).** The
  surviving stack traffic — 202 tunes, 661 page-one stores, 201 of those
  tunes declaring 3,292 scratch fields, 35% of this phase's corpus prize —
  is spelled `(zext2(sp [+ k]) | $0100)`, which `store_ref` cannot name, so
  `store_reach` falls back to an interval from **zero** (`addr_bits` keeps
  may-set bits only) and `overlaps` reports every such push threatening
  every zero-page cell. That threat is physically impossible: the OR
  guarantees bit 8, so the true reach is `$0100..$01FF`. The `aliased`
  condition therefore MUST compute reach with a lower bound — must-set bits
  from the same `addr_bits` walk (an `INT_OR` constant's bits are guaranteed;
  zext/copy/and propagate them), giving the stack store the interval
  `[$0100, $01FF]` — before this phase lands. **Landed in 2a as
  `frameproc.addr_floor`**, which needed it for the same reason (the push
  aliased every zero-page pointer pair); this phase's job is now to *use* it in
  `aliased` rather than to build it. The shredder still owes the
  fixture with it: a program that keeps `sp` (an `sp_linked`/`sp_unbalanced`
  refusal) alongside zero-page scratch, whose scratch still promotes. Without
  the floor this phase silently forfeits a third of its yield to Phase 1's
  ledgered residue.

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
longer worth building (revisited 2026-08-08: 2b's `web_alias` ledger — 287
roots, 2,751 unresolvable stores — is now the walker's direct pricing input,
and §6's reframe entry expects this decision to reverse). G2 (old item 3) is partly consumed by Phase 0's
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
design point arriving as a measurement.

**Both sentences are corrected by the landed 2a (see §2 Phase 2a): only goto80
certifies structurally — GT, SW and Galway all multiplex or spill through their
pointer cells — and Follin carries a third `other` shape, the cursor read out of
the block it walks. The transitive save closure does hold, and is what makes
Ghouls' `zp_21`/`zp_25` block-rooted.** Two further findings shaped the rule:

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
`test_shred16.py` discipline pointed at this plan: fifteen synthesized
players pinned to the phase promises, each **building and gating today**
(hard asserts), each canonicality assert `xfail(strict=True)` so a phase that
lands flips its test to XPASS and forces the marker's deliberate removal —
the suite cannot drift past the plan silently, and the plan cannot claim what
the suite disproves. Phase 1 exercised the discipline end to end: `sp_spill`
XPASSed at landing, its marker was removed in the same change, and it now
pins its phase's outcome as a hard pass.

The suite has two kinds of fixture, and the second is what moves failure
detection out of corpus sweeps: **xfail targets** (what a phase must achieve;
a landed phase's target stays in the suite as a hard pass) and **standing
invariants** (what no phase may break — soundness facts the plan's own gates
otherwise only catch at 624-tune cost).

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
| `sp_spill` | **landed P1** | two-depth sp-relative spill destacks, no `sp` survives — marker removed |
| `sweep_blit` | **invariant** | a covering blit stays byte-wide — the §7.7 `$CA6E` ord break |
| `hi_first_pair` | **invariant** | the `hi-first` order flag survives every rebuilder |
| `path_persist` | **invariant** | a path-dependent persistent cell (`pos_54EC` shape) stays state |
| `alias_state` | **invariant** | a cell a write-through store can clobber stays memory (M1) |
| `init_livein` | **invariant** | frame 0 reads init's value: the init/state0 coupling holds |
| `sp_unbalanced` | **invariant** | an unproven stack effect keeps `sp` and its stack-page identity |

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

- **Defer Phase 2b behind its own census payback.** *(Superseded 2026-08-08:
  the reframe entry below reverses the deferral and respecifies 2b; the
  annotation simplification in this bullet stands and is 2b's dialect.)*
  Phase 3 needs 2a's bounds,
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

**Post-Phase-1 review (2026-08-08).** A second review, run against the landed
phase and its artifacts, verified the before/after table reproduces row for
row (gate 622/623, ledger, census sums, the 92/33 movement) and that
"0-or-refused" holds literally (zero tunes with `raw_sp > 0` lack a ledger
entry; the shredder's `sp_spill` passes hard with its marker removed). It
found three defects in the *plan*, all folded into the sections above: the
Phase 1 heading promised `raw_sp -> 0` while its gate asked 0-or-refused
(heading rescoped; the fabric's removal scheduled as Phase 2c with its
2a-dependency stated, not parked in Phase 6); R7's census-zero headline was
uncapped while 298 tunes wear refused `sp` fabric (cap 326/624 stated at R7);
and Phase 3's `aliased` condition, as specified, would have spuriously
refused every zero-page cell in the 201 scratch-bearing tunes that keep
page-one stores — 3,292 fields, 35% of the phase's prize — because
`store_reach`'s fallback interval starts at zero (demonstrated against the
committed code). The floor requirement in Phase 3 (ii) and its shredder
fixture are that finding's fix.

Two obligations the review added, one since discharged: Phase 2's
certification must be proven closed under **reads** as well as writes (a
counter role reading bytes a cursor rewrite no longer maintains is a
divergence) — **landed in 2a** as the reads-closed premise, with the refusals
the review asked for (`role_entangled` 418 roots / 302 tunes, `role_opaque`
7 / 7); the `mux_pair` fixture stays xfail against 2b's rewrite half, which
is what it pins. Still open in its phase: the 2b extent check inherits
Phase 3's full-length differential discipline, since a cursor faulting at
frame 9,000 is invisible to the 300-frame sweep — with the corpus-wide
`--frames full` cost budgeted before either lands (§7.8's environment
numbers are the reference).

**Post-Phase-2a course correction (2026-08-08).** 2a's three corpus-forced
corrections were folded at landing (§0's root-count abbreviation noted at R2,
§5.2's two measured-false sentences marked, 2b rewritten around the coverage
number); this pass propagated the residue the landing commit did not: the
status header now records Phases 0/1/2a as DONE rather than describing a plan
awaiting its first phase, §5.4's fixture table carries the committed suite's
full fifteen (`sp_spill` as a landed hard pass, `sp_unbalanced` as the Phase 1
invariant), and the reads-closure obligation above is marked discharged by
2a's `role_entangled`/`role_opaque` ledger instead of standing open.

**Post-2a reframe (2026-08-08): the 2b deferral is superseded, and 2b is
respecified as a mechanical pipeline (§2 2b).** The first bullet above priced
the cursor dialect by static-certification coverage; that criterion was wrong
by R9's own text — the extent claim's license is the evaluator fault, and the
project already runs this discipline at the control layer (the
observed-primary flip, docs/corpus-status.md: committed artifacts are the
trace-observed sets, runtime guards fault outside them, static analysis is
demoted to certification accounting, and recurrence closure certifies guards
for the infinite run). The dialect question is closed by the corpus's own
censuses: every measured vocabulary is single-digit — three `sp` shapes,
three wide-store shapes, five `other`-def shapes, two pack skeletons, three
carry roles, one raw dyn form on the seven (R8's Angry_Birds statement) — so
one constructor set
(bounded accumulators, cursors with extents, arrays of both, dispatch,
triggers) covers all six families at different composition depths, and no
per-family knowledge is required anywhere in the pipeline. Consequences
recorded in 2b: the annotation-over-constructs simplification stands and the
deferral does not; lift eligibility (web closure, the one soundness premise)
is separated from certification (accounting); the post-init premise
dissolves at lift; `role_entangled` blocks nothing; and the `web_alias`
ledger (287 roots, 2,751 unresolvable stores) becomes the direct pricing
input for Phase 6's value-set walker, whose "no longer worth building"
expectation is now expected to reverse.

**Post-2b-analysis finding (2026-08-08): the reframe was right about the
license and wrong about where the cost sits.** b0/b1/b5 landed and confirm the
reframe's own predictions where they were checkable — `web_alias` costs exactly
the 287 roots it predicted, the post-init premise and `role_entangled` block
nothing, and eligibility separates cleanly from certification (50.2% of webs
lift against 10.2% cursor-ready). What the reframe did not price is the
*registry*: 399 webs touch an address `data_decls` declares nothing for, which is
the largest single blocker in the pipeline and removes 216 of b1's 524 eligible
webs. So the guard-live-where-uncertified argument stands, but "guard-live"
needs a block list to be live *against*, and the corpus supplies one for 61.8%
of webs. Two consequences, both owned by their step and stated in §2 2b: b3's
static extent fixpoint stops being accounting-only — it is the only proposal on
the table that widens the annotation past what one run observed — and Phase 6's
value-set walker now has a second customer, since the `foreign` half of the
unmappable addresses (39,068 of 78,510) is exactly "where can this pointer
point" asked at the block level.
