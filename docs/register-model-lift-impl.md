# register-model-lift — the minimizer plan (implementation)

Status: in execution. This document replaced the 13-phase queue at the
2026-08-09 mid-implementation review (decision log): same goal, different
engine. The goal is unchanged — **the register-model residue leaves the
artifact and the play routine emerges as a role-typed state machine**: a
per-frame transition function over named persistent state, byte fidelity owed
at the `$D400`–`$D41C` boundary and nowhere else. The engine is no longer a
ladder of per-shape rungs steered by corpus censuses; it is **derivation from
the canonical players and consolidation by equality saturation**, with the
corpus kept for the one thing only it can do — verification. "MUST" is a gate.

Why the phased plan died, in its own numbers: seven landings moved the census
sum 31,525 -> 30,854 (−2.1%) while the headline (tunes wearing zero machine
shapes) never left 0 of 624; every conclusion paid four full-corpus sweeps, so
the plan drifted toward what was cheap under that cost model — instruments,
censuses, re-measures — and kept extending the bespoke value/alias analyses
that docs/eqlift-adoption.md §5 says MUST NOT be extended, while the engine
that doc specifies sat unused after proving itself corpus-wide (682 tunes
emitted, bit-identical under two hash seeds). The one landing that ran
guard-first instead of certify-first (2b's rewrite) lifted 77% of its work
list in one step, against the 10% its certification phase managed. The record
chose the method; this document writes it down. Narratives are in git history
(`git log --grep=regmodel`).

## The law (unchanged, non-negotiable)

- **Gate FP's law.** The observable surface of a frame program is the
  canonical per-frame projection of the SID write stream; the reference
  evaluator's records equal `framelog.canonical` of the walker's log, frame
  for frame, full song.
- **Observed-primary + guards** (docs/soundness.md). Committed value sets,
  control-target sets and SMC opcode sets are exactly the trace-observed
  sets; anything outside faults loudly at evaluation. This is what makes
  few-exemplar derivation sound corpus-wide: an exemplar-derived rule that is
  wrong for a tail tune **faults**, it does not miscompile.
- **Verification never samples.** Derivation uses exemplars; every landing
  gates on the full suite plus the full-corpus Gate FP sweep. Three times a
  change passed every fixture and only the corpus caught it (frameprog.md
  §7.10.9's three hidden failures; 2c's eight divergences; 2.5's 334
  contradictions). The corpus sweep is one cached command and it stays.
- **Rule governance** (docs/eqlift-adoption.md §4). A rewrite exists only as
  a Z3-proven equivalence over QF_BV / the array theory; no bypass. Findings
  become rules or named refusals, **never per-tune rewrites**.
  `follin_script._ARITY` is the one standing exception — a hand-transcribed
  per-tune table, a named debt discharged at stage 3 — and no second table
  may join it.
- **The claims discipline.** No "unliftable" / "refuses" / "must stay" claim
  without the disassembly behind it (`tools/disasm_tune.py`) and a fixture. A
  claim read off emitted text is not a claim about the machine — `unobserved`
  arms are invisible in the text by design.
- **Public-repo rule.** Reference sources (tracker players, driver
  disassemblies, development disks) are cited, **never vendored**.

## The landed record (labels the code still cites)

The phased campaign's landings are library now; their historical labels
appear in docstrings and resolve here. Standing verification state at the
pivot: `gate_sweep` at full Songlengths **624 build; 622 evaluate / 621
clean** (standing exclusions: `Rambo_First_Blood_Part_II` divergence;
`C64_World` and `1st_Decent_Hardcore` evaluation faults); byte-identity
aggregate
`99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942` over all
624 — emission does not evaluate, so the two evaluation faults still emit
(the recipe is `tools/emit_identity.py`: per tune, sha256 of
`frameprog.dumps(frameprog.program(model))` at full Songlengths; aggregate
sha256 of `"%s %s\n" % (tune_id, sha)` joined over tunes sorted by id;
`--expect <sha>` gates it); census sum 30,854 — **retired as a steering
metric**, kept as a diagnostic. Stage 2 reproduced both: `gate_sweep` 622/621
with the same three named tunes, aggregate `99d4fdec…` unchanged over 624
(28,512,406 bytes).

| label | what landed, where | role under this plan |
|---|---|---|
| Phase 0 | `tools/storage_census.py`; wide-store classes in `fuse_measure`; `sid_readback`/dyn counts in `lift_residue`; `tools/disasm_tune.py` | diagnostics; the claims discipline's instruments |
| Phase 1 | rung (d0s) in `framestack.py` — sp spills become locals; per-procedure refusal ledger | landed lift, stands |
| Phase 2a | `ptrcert.py` block-rooting certification; `frameproc.addr_floor` (must-set bits) | certification accounting; `addr_floor` feeds stage 3's intervals |
| Phase 2b (b0–b5) | `ptrextent.py` + `out/ptr_extents*.json` (observed extents); rung (g) in `ptrlift.py` (251 webs lifted, 1,000 ⊤ loads retired); extent faults in `frameval` | landed lift; the guard pattern stage 3 inherits |
| Phase 2c | `framestack._SpFlow`/`_balances` — interprocedural balance as a call-graph fixpoint; `sp_linked` on displacement | landed lift, stands |
| Phase 2.5 | `tools/value_walk.py` — strided-interval walker and **the in-edge map** (closes 6,350 of 7,278 labels, 584 of 624 tunes; a raw `call` into the calling list is an in-edge) | feeds stage 3's joins and disjointness |
| Stage 1 | `tools/exemplars.py` (the 25-tune set), `idiom_cover.py` (the completeness gate), `source_anchor.py` + `idiom_cite.py` (the cites), `deity_informant/idioms.py` (23 recognizers); docs/idiom-catalog.md | the claim of coverage stages 2-4 are tested against |
| Stage 2 | `tests/test_vocabulary.py` (the checklist); `deity_informant/roles.py` + `tools/role_census.py` (the role reading); the `state { }` role keywords; `tools/emit_identity.py` | the vocabulary extraction may choose from, and the roles stage 4 names cells with |
| Phase 3a | frameprog total artifact (`image`/`dispatch`/`evidence`); content-keyed decompile cache in `tools/_sweep.py` | the substrate; a package-file edit costs one cold sweep |

Operational facts that bind: the suite runs `-n 24` (`-n auto` hits the
container fork limit), two passes (`-m "not oracle"`, then `-m oracle`); the
sweeps take `-j`/`--procs`; `gate_sweep --extents` MUST be given the
`--frames full` artifact, and `out/ptr_extents*.json` MUST be regenerated
before reading a `foreign`/`short` split off it; the 60s per-script budget
stands. eqlift's own bounds are `DI_EQLIFT_BUDGET_S` / `DI_EQLIFT_BUDGET_MB`
per e-graph and `DI_EQLIFT_EMIT_S` per artifact (3b): a saturation cut short
is sound, so lowering them trades minimization for time and nothing else.

## The method

**Derive from the canonical players, not from 624 binaries.** The corpus is a
heavy head over a long tail — 16 families are 57.7% of it — and the head's
ground truth is largely published: the trackers ship their own player source
(GoatTracker, SID-Wizard, defMON), Galway's sources are published by the
composer himself, Hubbard's driver circulates as a commented disassembly, and
the Follin driver's grammar is already in this repository
(docs/follin-dispatch-study.md §3, derived from the handler code and validated
against instrumented dispatch counts — there are no Follin "development disks"
in any reference set; that earlier claim was unsupported). Six head families
have no published source and are read from their exemplars alone. The SID
binaries serve two purposes only:
cross-checking a canonical source against its exports under the sidplayfp
oracle (which catches export feature-stripping and version drift), and the
full-corpus gate. Idiom derivation is a reading task over fewer than ten
programs.

**Consolidate by saturation, not by rung.** Every byte-level spelling of one
computation is equal in an e-graph under Z3-admitted rules, and extraction by
cost picks the simplest representative. The phased plan's entire middle
becomes extraction outcomes instead of phases: scratch dies because no
observable root reaches it, width recovery happens because the wide form is
the cheap representative of a carry chain (`carry_fuse`), boundary read-backs
die because the sinks are modeled write-only.

**Minimality is relative to vocabulary.** Extraction targets the wide dialect
(stage 2): a minimal *6502* program still spells carry chains — the machine
has no wide add — so minimizing into 6502 preserves the register model by
construction. Minimal 6502 re-emission is the round-trip *witness* (stage 4),
never the target.

**Whole-program saturation is tractable here because the domain cooperates.**
A play routine finishes inside a frame, so per-frame control flow is bounded
and small; tune data is immutable, so store/load forwarding through the
memory sort is mostly trivial; and the symbolic recorder already residualizes
each frame to straight-line dataflow over the entry state with SMC handled
soundly (docs/symbolic-recorder.md). The recorder is "enough analysis to feed
the saturation algorithm" — it exists and is hardware-validated.

**The canonical example, executable.** `examples/state_machine_lift.py` is the
whole method in one file, and `tests/test_state_machine_lift.py` gates it: a
hand-written 6502 playroutine (8 bars over three structurally parallel voices —
lead, bass, offbeat arpeggio — each with its own script, deferred-carry cursor,
Follin-style SMC-JMP command dispatch including a variable-arity `rawsid`
operator whose arity is the decoded length, table vibrato through an ADC carry
chain, portamento as a byte borrow chain over a bounded step, a shared note
fetch reached by `JSR` with a `PHA`/`PLA` spill, a two-frame hard restart that
zeroes ADSR and drops the gate then sets TEST, and a RAM SID shadow carrying the
envelope/control lanes flushed ADSR before gate) plus a voice-independent head
(a 24-bit accumulator whose carry-out outlives its add, a 12-bit pulse-width
pair, an indexed span store on one arm of a branch a disjoint cell crosses, and
a page-zero row whose index arithmetic wraps in 8 bits) and, on voice 3 alone,
a filter block (the `$D415`/`$D416` cutoff pair, and `(s & K) | v` field updates
on the `$D417`/`$D418` flag cells) runs through the real pipeline — VM,
decompile + walker replay, `eqlift_mem.emit` minimization, seven spelling folds,
every instance Z3-proved: the shadow store-to-load forward over the array
theory, the paired u16 SID store, the deferred-carry advance and the wide
compare whose guards are proved rather than matched, the n-lane wide update
(2 and 3 lanes, with its carry-out) whose operand is searched and proved rather
than pattern-matched, and the u16 pair reload, a structural match whose
independence check is a name scan and whose proof stage 3's rule admission owes
— role classification off the folded update shapes, `flags` included — and the
resulting role-typed state machine executes and matches the original
frame-for-frame, in order, on the VM projection, on pysidtracker's independent
engine, and on the dockerized sidplayfp/sidtrace oracle (`--sidtrace`); `--wav`
renders both sides to `out/` over the same frame span. Voices 1 and 2's
per-voice code is asserted isomorphic up to base displacement over the folded
statements, and voice 3's differs by exactly the filter block. **Every stage
below MUST keep this example green; a stage that cannot express it has diverged
from the goal.** The stages generalize exactly what it does: stage 1 catalogs
the idioms it hand-picks, stage 2 puts its fold vocabulary in the real
grammar, stage 3 moves its folds into admitted rules and its convergence
checks over the catalog, stage 4 emits its output shape for the corpus. What it
does not yet reach is pinned `xfail(strict=True)` against the stage that flips
it: the stack-spill forward (3c — landing 2 measured it as two mechanisms, the
memory spelling's cost and scratch demotion), the split lo/hi pitch row and any
byte-lane update of a declared-u16 quantity (3c), and per-voice re-rolling (3d).
The branch-join forward is green as of 3b landing 2.

**The roles are the expected output, not a license.** Read forward, a play
routine's persistent state resolves into five roles — **cursor** (an index
into a declared block, advanced monotonically or rewritten), **accumulator** (a
value stepped by a delta within a bound), **counter** (a countdown gating a
step), **flags** (a bit-packed cell each writer updates while preserving the
bits it does not write), **parameter** (set by a cursor event, read by
steppers) — plus the VM registers of script-interpreter families. The
vocabulary is closed by the chip, not by enumeration: the SID lacks hardware
for vibrato, portamento, PWM/filter sweeps, arpeggio and fades, so that is what
drivers synthesize. The role is read off each cell's update term (`s' = s + k`
whose cell an address reads is a cursor; a bounded `s' = s ± d` is an
accumulator), which stage 2 executed over the exemplars —
`flags` is the role that census added, and it covers 98.9% of the witnessed
cells with the shift residue named. Recognition licenses nothing — the guards
and the proofs license; roles name.

## Stage 1 — the catalog (canonical sources -> idiom inventory) — CLOSED

Pull the canonical player for each family, disassemble/read it, and write the
idiom inventory the rule set will be tested against.

The family table, the exemplar set and the fetched sources live in
**docs/idiom-catalog.md**; `tools/exemplars.py` is the one declaration of the
set, `tools/fetch_players.py` caches and pins the sources, `tools/player_id.py`
names a tune's player from the SIDId signatures, `tools/family_cluster.py`
clusters the corpus by executed-code fingerprint, `tools/source_anchor.py`
binds source labels to exemplar addresses and `tools/idiom_cite.py` joins the
gate's witnessed seats to those labels.

The exemplar set is **25 tunes over 24 clusters**, covering 417 of 624 cached
tunes (66.8%) — the plan opened with seven (49, 7.9%) and stage 1's first
landing measured sixteen (360, 57.7%). Per family the canonical source is
cross-checked against its exemplar before any idiom is recorded from it; a
family with no published source is marked as such, carries the weaker warrant,
and carries no canonical cite.

**Deliverable: `docs/idiom-catalog.md`.** One row per idiom: the
canonical-source citation (player, label/address), the `disasm_tune` cite in
a corpus exemplar, the families carrying it, and **the normal form it must
reduce to** (a dialect term) or `named-unknown`. Existing enumeration data
feeds in rather than being redone: docs/twobyte-lift.md's 610-shape pair
enumeration, the §5.4 shredder fixture family, ptrcert's definition-kind
census, the follin-dispatch-study grammar.

**Landed**: 23 rows (20 normal forms, 3 named-unknowns), every one witnessed
over the 25 exemplars; the completeness gate (`tools/idiom_cover.py`) runs 0
unaccounted over 2,205 obligations / 6,257 nodes; 18 of the 23 rows carry a
canonical cite computed through the anchors, and the five that do not name why
(no published source for the families that spell them, or seats outside the
anchored runs). The doc's Rows table, its family table and the recognizer set
are gated equal to the code by `tests/test_idiom_catalog.py`. Growing the set
from sixteen to 25 is what forced the 23rd row: nine new exemplars reported
eight unaccounted nodes of one shape (`zp-row`), which is the gate doing the
job it exists for.

**Completeness is checkable and MUST be checked**: in each exemplar, every
SID-store dataflow slice and every frame-surviving cell is accounted to a
catalog row. The catalog, not a corpus census, is thereafter the claim of
coverage. Any exemplar added later MUST re-run the gate over the grown set.

The Follin entry carries the debt: `_ARITY` is a hand transcription; the
catalog records the mechanical definition (an operator's arity is the net
`Y` delta of its dispatch arm, constant on all paths) and stage 3 recovers
it. The study's own §3 grammar already corroborates all 20 constant arities
op for op — and shows the definition is incomplete: `$85` (`rawsid`) is
variable-arity, so it needs a decoded-length escape or a named refusal.

## Stage 2 — the vocabulary (capability before use) — CLOSED

The grammar and evaluator learn to spell and execute what extraction will
choose, with **no rewrite landing alongside**: wide (u16) locals and pair
cells (frameprog.md's M-FP3), the cursor forms rung (g) already ships, and
role-typed `state { }` declarations.

**The capability checklist is `idioms.FORMS`, not the sentence above.** The
catalog's normal-form column also demands u16 lane update, u16 high/low byte
reads, u16 shift, flag, field select/set, and the page-zero row whose index
arithmetic wraps in 8 bits — some already spellable, none allowed to be assumed
so. `tests/test_vocabulary.py` is that checklist, enumerated from
`idioms.FORMS` so it cannot undercount: **all 20 non-unknown normal forms** are
emitted as frameprog text, parsed back, accounted by `idioms.cover` to the row
they claim with zero gaps, and executed to a checked SID write. Named-unknowns
add no vocabulary by definition, and the test asserts the three of them
(`stack-slot`, `carry-value`, `compare-value`) carry no spelling obligation.

**Landed, and the finding is a negative one**: none of the 20 needed new
grammar — the dialect could already spell every one. What did not exist was any
test that it could, which is the point of running the checklist rather than
reading the sentence: the stage's cost was one census and one keyword set, not
a vocabulary build-out. The four cases the checklist had to be written
carefully to reach at all are where the spelling is not the obvious one —
`word-pack` (a pack of two *terms*; `pair-row` owns it the moment both sides
are byte cells), `zp-row` (`mem[zext2((x + $80))]`, byte-domain arithmetic
under the widening), `deref-row` (`*ptr_00FB[y]`, which needs rung (f)'s
`resolved` binding to be spelled as a deref at all) and `pair-row` (which needs
the `lo`/`hi` roles declared on the two columns). The other half of the
sentence was already gated and is not restated: `tests/test_locwidth.py` spells,
round-trips and *executes* the u16 local and the width-2 cell store (low byte
first), and rung (g)'s deref is `deref-row`'s own case.

**Role precondition — the census, and the sixth role it forced.** The role
keywords come from the plan's read-forward argument, not from stage 1: the
catalog accounted dataflow shapes, never update shapes, so nothing yet
witnessed that the roles cover the exemplars' persistent cells.
`deity_informant/roles.py` reads a cell's role off its own update terms plus
where the program reads it back, and `tools/role_census.py` runs it over the 25
exemplars at full Songlengths (`out/role_census.json`, ~15s cached): of stage
1's **1,730 witnessed cell updates**, 1,715 attribute to a named cell (15 land
on a base the address form does not name) over **963 persistent cells**. By
update: 1,327 `set`, 174 `dec`, 162 `step-up`, 24 `field`, 15 `step-down`, 13
unshaped. By cell:

| role | cells | share | what the update is |
|---|--:|--:|---|
| parameter | 597 | 62.0% | set from a term that does not read the cell |
| counter | 160 | 16.6% | a countdown, `s' = s - 1` |
| cursor | 113 | 11.7% | stepped or rewritten, and read inside an address |
| accumulator | 62 | 6.4% | stepped by a delta, the bound spelled as a mask |
| **flags** | 15 | 1.6% | **bitwise recombination of the cell with itself** |
| vm | 5 | 0.5% | the cell an operator dispatch switches on |
| un-roled | 11 | 1.1% | the residue below |

**`flags` is the missing role the census named**, and it landed before the
keywords froze, which is what the precondition was for: a cell whose update is
`s & K`, `s | K`, `s ^ K`, `s & table[i]` or `(s & $F0) | v` — a
read-modify-write that *preserves the bits it does not write*. The `field`
shape is witnessed on 18 cells over 10 exemplars (24 updates); 15 of those
cells carry no stronger evidence and take the role. Without it, 29 cells over
14 exemplars are un-roled and the residue is two families; with it, 11 over 9
and one. The catalog already spelled this at dataflow level
(`mask-const`, `set-const`, `flag-bit` → `field select` / `field set` /
`flag`); what it never said is that a *cell* can be one.

**The residue is one shape and it is deliberately not a role.** 11 cells (1.1%)
over 9 exemplars, 13 updates over 7 skeletons, every one a **one-place shift
of the cell**:
`s << 1`, `(s:2 >> 1):2`, `(s >> 1) | ((a & $01) << $07)`,
`(s << 1) | ((a & $80) != 0)` — ASL/LSR/ROL/ROR clocked once per frame. It
splits mechanically in two, which is why one role would not cover it: a bare
one-place shift, where the cell's value is scaled and its top or bottom bit is
dropped (`ptr_00FB` in `Frantic_3_tune_5`, `zp_FB:2` in `21_G4_demo_tune_2`,
`m_CDF0:2`/`m_CDF2:2` in `Before_I_Forget`), and a **rotate**, where a bit is
shifted *in* from a flag and the cell is a queue rather than a number
(`ptr_00F8` in `Dynasty_8_tune_2`, `m_217F` in `Discmonsters_Intro`, `m_7948`
in `Down_Under`). Naming either one alone would leave the other un-roled, and
naming both together would say the same thing about a scaled value and a bit
queue; 1.1% over 9 tunes is not the "common residual shape" the precondition
asks to act on. Whichever of the two the disassembly turns out to justify, the
sites are on the record with their tunes and seats in `out/role_census.json`,
which is what a later stage needs to act. Un-roled `u8`/`u16` fields stay
legal — roles name, they never license — so this costs stage 4's role-named
metric and nothing else.

**Two classifier defects the census forced, both reproduced from `out/`:**

- **A `DEC` lifts to `x + $FF`.** The first run reported `counter` on *zero*
  cells over 25 exemplars, which is not a fact about SID drivers. A countdown is
  a modular step up until the delta is read signed at the width the step is
  taken at (`roles._signed`); reading it so put 165 cells on `dec` and moved
  `counter` from 0 to 160.
- **A cell read inside an *address* is not a self-reference.** `s' = mem[s + y]`
  walks a block through the cell; it does not step it. Counting it as one put
  the Follin script pointers in the residue as unshaped. Excluding addresses
  makes them `set` — and that same read is what puts the cell in the address
  set, which is a cursor's own evidence, so the correction and the
  classification are one fact read twice.

Also corrected on the way: the high lane of a wide step (`trunc1(v >> 8)`) is
that step's shape and not one of its own, and a bare `s' = s` is an assignment
rather than a shape.

**The keywords.** `statedef` gains an optional role between the colon and the
type — `ptr_0021: cursor u16 in m_7338 observed $01` — one of `cursor`,
`accumulator`, `counter`, `flags`, `parameter`, `vm`, carried on
`FrameProgram.roles` exactly as 2b's extents are carried, and gated equal to
`roles.ROLES` by `tests/test_vocabulary.py`. **Zero use**: the emitter assigns
no role, so every artifact is byte-identical and stage 4 is what turns them on.

Gates: emitted text byte-identical corpus-wide (capability, zero use);
`dumps(loads(t)) == t` over every artifact; Gate FP untouched; hermetic
evaluator tests for every form. `tools/emit_identity.py` is the byte-identity
aggregate as a command (`--expect <sha>` gates it), which the plan cited but
nothing ran.

## Stage 3 — the minimizer (one e-graph, root extraction, convergence tests)

The engine of docs/eqlift-adoption.md §2, executed: per procedure/region of
the committed model, one unified value+memory e-graph; saturate with the
admitted value rules plus the four memory axioms guarded by interval
disjointness; **extract from observable roots only** — SID sinks, the frame
boundary, params/returns. What no root reaches is not emitted: that is
scratch elimination, dead flags, and spill removal as one mechanism instead
of three phases.

- **Joins.** Opaque-reset by default at branch joins, loop heads and call
  boundaries, weakened only by admitted argument (adoption §10). An
  unreconciled cell stays guarded memory — a readability loss, never a
  soundness loss. The in-edge map is the join structure; a raw `call` into
  the calling list is an in-edge.
- **Cost.** Wide/simple vocabulary cheap; byte-lane spellings and `carry`
  expensive; SID-range cells write-only (a read-back can never win
  extraction). Cost changes are justified by a corpus-artifact diff, never
  one tune (adoption §4).
- **Convergence tests — where the generative fuzzing sits.** Per catalog
  idiom, a parametric generator (the differential-fuzz / shredder harness)
  produces the spelling variants, and the test asserts they merge into **one
  e-class** with the catalog's normal form extractable from it. The §5.4
  shredder fixtures are the seed corpus: each pending xfail either
  normalizes here (the marker is removed in the same change) or is re-pinned
  as a guarded refusal with its reason on the record. A variant that fails
  to merge names a missing rule, and the rule lands only with its Z3 proof.
- **Not equational, kept as small classical passes.** Per-voice re-rolling
  (anti-unify the unrolled voice slices; total isomorphism or keep the
  copies), for-range recovery, and SMC opcode dispatch, which stays the
  observed-variant `switch` behind guards.
- **Budgets.** Bounded schedules — extraction is sound at any cutoff because
  every admitted rule is an equivalence; 60s per tune; a tune over budget
  ships less-minimized and never blocks a landing.
- **The canonical example stays green** (`tests/test_state_machine_lift.py`):
  its folds and its oracle equalities are this stage's definition of done in
  miniature.
- **Retirement.** Adoption §5's transitional passes actually retire as
  subsumption lands, and extending them is forbidden — enforced from this
  document forward.
- **The debt discharged.** VM-family operator sets fall out per dispatch
  arm: arity is the net `Y` delta, the effect set is the cells written, an
  arm that rewrites the pointer is control. `follin_script._ARITY` is
  deleted when its family's recovered arities equal the transcription — the
  executable test that the mechanism is real.

**The position (2026-08-10), and the next landing.** Landed: 3a (root
extraction behind `eqlift_mem.ROOT_EXTRACT`, default off), 3b part one (the
interval bridge, the bounded schedule), 3b landing 1 (the name-collision and
chain-doubling defects, the extraction budget, the 25-of-25 ON/OFF review),
3b landing 2 (the span join, the complemented join encoding, the call/goto
closure, and the egg-side memory cost the consumer's filter always implied),
3b landing 3 (the in-edge map at labels, and `ROOT_EXTRACT` **on** by default
on a clean 25-of-25 review). **Next is 3c**, and what stage 3b hands it is
named: the join carries 1,628 of 2,528 walls and the remaining 900 are
enumerated by kind in the decision log; a label with real in-edges still
resets, and closing it needs a join over the in-edge memories rather than a
map lookup. After it, 3c's rules in the shape the eight-extension
landing recorded — search the operand, prove the instance, never enumerate
spellings — with the per-idiom convergence harness and the two mechanisms
landing 2 named (a memory spelling's cost with its position-correctness
argument, in place of `pick_ir`'s filter; scratch demotion, which is what a
dead stack slot needs), then 3d (the `_ARITY` discharge, guard-aware
re-rolling).

## Stage 4 — gate + emit (the state machine is the artifact)

Per landing: the full suite; the full-corpus Gate FP sweep; the 25
exemplars at full Songlengths with their emitted-text diffs read by hand.

Steering metrics, replacing the census: **extracted term cost / emitted
size, falling; persistent cells role-named, rising toward all.** The census
signatures remain available as a diagnostic only.

The properties are pinned before the work: `tests/test_state_machine_lift.py`
carries the goal itself as strict xfails on the canonical prototype — zero
architectural registers in the role-typed text, no SMC dispatch operand and no
scratch cell in `state { }`, the role evidence clauses, declared initial values,
VM operator sets, and the round-trip witness — plus two green guards (hash-seed
determinism of the rendered text, and a size ratchet on emitted lines and
extracted term cost).

The artifact: role-typed `state { }` plus the per-frame transition function,
per-voice unified where the isomorphism is total (else the copies stay); VM
families emit their operator sets. The witness, when wanted: re-emit minimal
6502 from the minimized program and replay it under the VM against the
oracle — an end-to-end check with no evaluator in the trust chain.

## Independent housekeeping (blocks nothing)

- **sidprog retirement.** Inventory the sidprog-only laws
  (`tests/test_soundness.py:403`'s closure round-trip is the named one), port
  what still binds to frameprog equivalents, retire the emit path and the
  README surface. Timebox ~one PR or re-queue it. The cycle-exact anchor is
  the model, the walker replay and the VM/recorder against sidplayfp — the
  text was never the anchor.
- **`_declare_cells` double declaration** (3a's finding: one cell declared in
  both `state { }` and `data { }`, Agent_X_II `$6923`/`$6925`): discharged by
  stage 3's first text-moving landing.
- **The song-model modules.** `song_model.py`, `generators.py`, `movefwd.py`,
  `eqlift_annotate.py` (and `eqlift_mem`'s annotate hook) were the role
  reading on the wrong substrate; their docs and stale artifacts are deleted.
  The modules stay tested until stage 4's role-typed artifact replaces their
  function, then leave.

## Risks, against the record

- **Memory-join unsoundness**: opaque-reset default, weakenings only by
  admitted argument, all-sites proofs, Gate FP behind everything. Degradation
  is per-cell readability, not correctness.
- **Saturation cost**: bounded schedule, cutoff-sound; per-tune budget with
  graceful degradation.
- **Extraction nondeterminism**: solved once and recorded (adoption §10) —
  no consumer may depend on which representative returns; remaining choices
  are made off the program, not the extraction order.
- **Exemplar bias**: the guards fault on anything unobserved and the corpus
  gate runs every landing; a tail tune faulting is the claim boundary
  working, not a soundness event.
- **Rule-closure completeness**: not assumed — the convergence tests make it
  an empirical, per-idiom property, and the catalog's completeness check
  bounds where an unknown idiom can hide.

## Decision log

Adopted decisions, newest last. Pre-pivot narratives: git history
(`git log --grep=regmodel`, PRs #131–#135).

- **2026-08-09 — the pivot.** The 13-phase queue is deleted after the
  mid-implementation review, on its own record (header). Method replaced by
  the four stages above: canonical-source catalog, wide vocabulary, e-graph
  minimizer with root extraction and per-idiom convergence tests, corpus
  gate + role-typed emission. The soundness kernel (Gate FP, observed-primary
  guards, Z3 admission, claims discipline, full-corpus verification) is
  unchanged. Docs deleted as superseded: `register-model-lift.md` (framing
  folded here), `decompiler-plan-prototype.md`, `song-model.md`,
  `corpus-status.md`, and the stale `out/*.eqlift.txt` artifacts (unowned
  since PR #51). `decompiler-implementation.md` stays: frameprog.md and
  soundness.md cite it normatively (v1 class scoping, handler entry), so it
  is the landed sidprog spec, not a stray. Shredder xfail
  reasons and package docstrings re-pointed at the stages (one cold
  sweep-cache pass accepted). The census is retired as a steering metric.
  eqlift-adoption.md's §8 step list is superseded by the stages; its §4/§5/§6
  contracts are enforced unchanged — §5's no-extension rule now includes the
  interval/alias family that grew five members before it.
- **2026-08-09 — stage 1 opens; the exemplar set goes from seven to sixteen.**
  The canonical sources are fetched and pinned for six families
  (`tools/fetch_players.py`; Galway turns out to be **author-published**, not a
  community disassembly, and his README splits the family into a 1st-generation
  player and the 2nd generation `Athena` introduced). Two instruments then
  measured what the seven actually cover: `tools/player_id.py` names a tune's
  player from the SIDId signature database (unused in the cache until now) and
  `tools/family_cluster.py` clusters executed-opcode 5-grams by **containment**
  — Jaccard scores the size gap between two songs on one driver as difference,
  which is the wrong question. The two agree cluster for cluster. The seven fall
  in 6 clusters covering **49 of 624 tunes**; the corpus's largest single-player
  family (`GoatTracker_V2.x`, 75 tunes) had no exemplar, and the plan's
  GoatTracker exemplar `Aces_High` is `GoatTracker_V1.x` — the wrong major
  version for the fetched source. Adopted: the exemplar set is sixteen tunes,
  one per family (**360 of 624, 57.7%**), `Jammer/Grid_Runner` anchors
  GoatTracker 2, `Aces_High` stays as the V1 exemplar, and the families with no
  published source (DMC, Music Assembler, FutureComposer, Soundmonitor, JCH
  NewPlayer, Master Composer) are read from their exemplars and marked as
  carrying the weaker warrant.
- **2026-08-09 — the canonical example, and the two defects it forced.**
  `examples/state_machine_lift.py` landed the same day as the pivot: the full
  method on a hand-written Follin-flavored playroutine, gated by
  `tests/test_state_machine_lift.py` (VM projection, independent-engine grid,
  sidplayfp/sidtrace change stream — 136 changes, minimized side). Making the
  minimized text *executable* — the adoption doc's own §6 said it had only
  ever been review material — surfaced two real renderer defects in
  `eqlift_mem.render_proc`, both fixed with regression tests:
  (1) **availability was path-insensitive** — `avail` accumulated across
  sibling arms and label copies, so extraction could spell a value over
  another path's local (rendered as `w0 = w0` self-assignments that deleted
  the definition, or as reads of temps whose defs never execute on the
  path). `avail` is now scoped like `env` (restored at branches and cases,
  cleared at labels), havoc/join names — which have no rendered definition —
  are never available to spell over, and the `.0` entry-version bypass is
  restricted to genuine CPU-register locals.
  (2) **memory spellings erased chain position** — a `sel` extracted at one
  store-chain position could print as `mem[addr]`/cell at a statement where
  memory differs (the dispatch-hi line read its SMC cell pre-store).
  Extraction candidates are now pure value spellings only; a surviving
  memory read always renders from the site's own term, which is
  position-correct by construction, and ties break deterministically by
  (cost, repr) — adoption §10's discipline extended to the memory renderer.
  Standing lesson, recorded where the plan can see it: **emitted text that is
  never executed is not verified** — stage 4's evaluator-precondition exists
  because two soundness-grade defects sat invisible in review-only output.
- **2026-08-09 — the stage-1 review: four corrections, two stage-2
  preconditions.** Every catalog number reproduced exactly from
  `out/idiom_cover.json` and `out/family_cluster.json`; what did not hold:
  (1) the doc claimed a doc↔code gate no test enforced —
  `test_idiom_catalog.py` now parses the Rows table and gates ids, normal
  forms and match order against `idioms.ROWS`; (2) the family table's Galway
  1st-gen count said 3 where its exemplar's cluster holds 1 (the column is
  now defined as cluster size and sums to the 360 covered — SIDId's other
  three Galway tunes sit in uncovered clusters); (3) "622 built" and "624
  built" named different things — now 624 build, 622 evaluate, 621 clean;
  (4) stage 4 still said seven exemplars. Adopted for stage 1's close: the
  cite/families columns are the named remainder (the rows' warrant is the
  exemplars' lifted dataflow until each canonical cite lands), and the seven
  uncovered clusters the instruments already surface are queued in the
  catalog (a second GoatTracker V1 build, a third DMC build, RoMuzak,
  DefleMask, Electrosound, CheeseCutter, Laxity NewPlayer; 360 → 415 of 624)
  — families fragment **by build**, so a family's claim ends at its
  exemplar's cluster. Adopted for stage 2's open: the capability checklist is
  enumerated from `idioms.FORMS`, and the role keywords freeze only after a
  census of the witnessed cell-update shapes (both in stage 2 above).
- **2026-08-09 — stage 1 closes: the cites are computed, and the exemplar set
  is 25.** The named remainder is discharged. The seven queued clusters were
  added, plus `Wizball` and `Rambo_First_Blood_Part_II` — the two tunes the
  published Galway sources are the source *of*, so those rows' two cites name
  one code rather than two builds (Rambo carries the corpus's one standing Gate
  FP divergence; what the catalog reads from it is lifted dataflow and image
  addresses, which the frame-level divergence does not bear on). Coverage 360 →
  **417 of 624 (66.8%)** over 24 clusters, and the set is declared once in
  `tools/exemplars.py` instead of twice in the sweeps that consume it — the
  doc's family table is now gated against that table. The cite and families
  columns are **computed, not transcribed**: `idiom_cover` records the seat each
  row is witnessed at, `source_anchor` binds labels to addresses, `idiom_cite`
  joins them by strongest-anchored family then tightest label, and a cite is
  refused unless the seat sits between two anchors of a run the family's trust
  tier allows. 18 of 23 rows carry one; the five that do not say why. Findings
  the close forced, all reproduced from `out/`: (1) the grown set reported
  **eight unaccounted nodes over one shape** — `mem[zext(b ± k)]`, page-zero
  indexed addressing whose arithmetic wraps in 8 bits — so the catalog gained
  its 23rd row (`zp-row`) and the gate returned to 0; (2) an anchor binds one
  source to one *build*, so a family's second exemplar carries no cite —
  Follin's `Agent_X_II` seats cannot cite the study's `Ghouls_n_Ghosts` handler
  addresses, and the tool enforces it; (3) the earlier claim "25 stores do not
  resolve, two of which can reach `$D400`–`$D41C`" was wrong on the second half
  — over the 25 exemplars 42 stores do not resolve and **27** are conservatively
  attributed to the SID because their address is wholly open (`addr_bits` leaves
  the high byte unconstrained), which is what that attribution means; (4)
  defMON's exemplar is a **later build** than the disassembly — the control's
  four constant-displacement classes (`+$21` on 294 instructions, `+$28` on 86,
  `+$24` on 80, `0` on 10) are what a correct cross-build alignment looks like,
  so a defMON cite prints the source's line and the exemplar's own address.
- **2026-08-09 — stage 2 closes: the checklist found no gap, the census found a
  role.** The capability checklist ran and every one of the 20 non-unknown
  normal forms already spelled, round-tripped, accounted to its own catalog row
  and executed — the stage bought a *test* that the dialect can spell what
  extraction will choose, not new grammar, and `tests/test_vocabulary.py`
  enumerates it from `idioms.FORMS` so a row added to the catalog fails until it
  has a case. The role precondition is where the work was. `roles.py` reads a
  cell's role off its update terms plus where the program reads it back, and the
  census over the 25 exemplars (963 persistent cells, 1,730 updates) reported a
  common residual shape the five roles had no form for: a read-modify-write that
  preserves the bits it does not write (`s & K`, `s | K`, `s ^ K`,
  `(s & $F0) | v`), on 18 cells over 10 exemplars. Adopted: **`flags` is a
  sixth role**, landing before the keywords froze, which is what the
  precondition existed to buy. The residue is 11 cells (1.1%) over 9 exemplars,
  all one-place shifts (ASL/LSR/ROL/ROR clocked per frame), and is deliberately
  left un-roled: the sites are an index scaled to a 2-byte row stride and a
  bit-serial pattern register wearing one shape, so a seventh role would
  misdescribe half of them. Two classifier defects the census forced: a 6502
  `DEC` lifts to `x + $FF`, so the first run put `counter` on **zero** cells
  over 25 exemplars until the delta was read signed at the step's width (165
  cells on `dec`, counter 0 → 160); and a cell read inside an *address* is not a
  self-reference — `s' = mem[s + y]` walks a block through the cell rather than
  stepping it, and that same read is the cursor evidence, so the correction and
  the classification are one fact read twice. The `state { }` role keyword sits
  between the colon and the type (`ptr_0021: cursor u16 in m_7338`), rides on
  `FrameProgram.roles` as 2b's extents do, and is **zero use**: the emitter
  assigns none, so the corpus artifact is byte-identical. `tools/emit_identity.py`
  makes the byte-identity aggregate a command the gate can run, which the plan
  had cited without one.
- **2026-08-10 — stage 3a: root extraction replaces the liveness pass, behind a
  flag.** The emit path's back half is one mechanism instead of three.
  `eqlift_mem.roots()` names adoption §2's observable sinks per procedure — every
  surviving memory store (`Roots.sid` is the write-only `$D400`–`$D41C` subset;
  memory persists across the frame, so a store is observable unless an axiom
  retires it), every control statement, and the register locals pass 2's boundary
  summary says a consumer reads — and `_root_keep` closes over them: **a statement
  is emitted only if a root reaches it**, transitively through the names
  extraction chose to spell with. That is dead flags, scratch and spill removal as
  one reachability, so `_dce` and `_temp_sweep` do not run on the root path;
  `_dce`'s fixpoint survives as `_liveness`, feeding the root set instead of a
  deletion pass. The flag is `eqlift_mem.ROOT_EXTRACT` (env
  `DI_EQLIFT_ROOT_EXTRACT=0` selects the liveness path since 3b landing 3 flipped
  the default), **default off at 3a**, and off the corpus aggregate is
  `99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942` over 624
  tunes (28,512,406 bytes, 0 refused), unmoved. What the landing found:
  (1) **Store deletion is the part liveness could not do.** A store drops when the
  admitted axioms prove the chain unchanged — `store(m,a,sel(m,a,w),w) = m`, or an
  in-place overwrite — read as e-class equality of the store's pre and post
  memory, not as a pass. Krakout's play routine loses one duplicated
  `m_E686[x] = a`; Commando loses none and its 350 lines are identical on both
  paths, which is what "the root path replaces the mechanism, not the rendering"
  looks like.
  (2) **Single-use inlining is re-derived, not yet retired.** adoption §5 retires
  it with the flag, and the flag is still here; what changed is its warrant. The
  root path needs the same rule for its own reason — `render_roots` names a
  subterm only where more than one root reads it — so it is now `_share_once`,
  stated as that rule and called by both paths. Without it extraction keeps every
  name the cost model preferred to its expression (a `loc` costs 4), three extra
  lines on Commando alone.
  (3) **The all-sites proofs are executable now, and they are not vacuous.**
  `verify_sites` is adoption §6's law as a function: a Z3 reading of the extracted
  IR (values BV16 masked to their own width, memory an array `BV16 -> BV8` whose
  `mem0`/`memk` leaves are opaque) proves each emitted site's chosen term equal to
  the term the statement holds, under the SSA/memory definitional equations, with
  the assumption set checked satisfiable first so an over-constrained environment
  cannot prove everything. Commando: 291 sites, 64 changed by saturation, all
  proved.
  Not in 3a, by scope: the join model is untouched (opaque-reset at branch, loop
  head and call stands), so a store's redundancy can only be proved inside the
  region its chain spans — the join-free-region-first order stage 3 asks for,
  arrived at through the axioms rather than through a region split. Whole-chain
  extraction over a region, the region splice and the flag default are 3b.
- **2026-08-10 — stage 3b, part one: the interval bridge, and the bound the
  schedule never had.** `addr_floor` was written for this and nothing read it.
  `eqlift_mem.addr_interval` now reads it with `addr_bits` as one interval — the
  must-set bits are a floor on every address the expression names, the may-set
  bits a ceiling — and seeds it on the e-class the address converts to, **only
  where `mem_rules` states no bound of its own**, so a seed can never be widened
  by a derived one and the two committed bit analyses are consumed rather than
  extended (adoption §5). What the lattice could not say and the bridge can: a
  byte-wide address is page zero, and the stack push `zext2(sp) | $0100` is
  `[$0100,$01FF]`. A spill now reads through a push instead of stopping at it —
  `zp_41 = zp_40` becomes `zp_41 = x` — and on the exemplars the three stores the
  root path retires are two of exactly that push and one indexed store.
  A **soundness correction** the bridge forced: `add`/`shl` carried the interval
  with no width guard, and a wrapped sum is *below* both operands, so
  `$F0 + (y & $1F)` at one byte claimed a floor of `$F0` for a value that can be
  `$10`. Both rules are now guarded by `hi(a) + hi(b) <= mask(w)`; nothing was
  relying on the unguarded form, and the guard is what makes seeding byte-wide
  leaves safe at all.
  **The bounded schedule is real now.** `run(ruleset * 30)` is not a bound:
  `Dynasty_8_tune_2` (one procedure, 488 statement nodes) costs 0.0s per round
  through round 8, 0.5s at round 9, 3.1s at round 10, and asks for 1.6GB in one
  allocation at round 11 — the process died before extraction, on this branch and
  on main alike, which is why 3a's exemplar measurement never closed.
  `eqlift_mem.saturate` runs the ruleset a round at a time, stops at a fixpoint,
  and refuses the next round when the last round's own growth ratio outruns the
  remaining seconds (`DI_EQLIFT_BUDGET_S`, 5s per e-graph) or when resident growth
  passes `DI_EQLIFT_BUDGET_MB` (128); `emit_mem` divides `DI_EQLIFT_EMIT_S` (60)
  across the procedures so the artifact has a budget and not only each graph.
  Priced on the tune that forced it: 128MB gives 630 lines in 2.0s, 256MB gives
  629 lines in 39.1s — one line of minimization for 37 seconds — and Commando
  (350) and `Ghouls_n_Ghosts` (1,335) do not move a line at any bound because the
  cap does not bind there. Cutting a schedule short is sound at any point: every
  admitted rule is an equivalence.
  **The §6 proof was exponential, and per artifact instead of per procedure.**
  `_Z3Env.of` rebuilt each shared extracted subterm once per occurrence — the
  extracted IR is a DAG — so `Dynasty` reached 37GB resident and was killed;
  memoised on the IR node it proves in 40s. And `emit_mem` now hands each
  procedure its own §6 record: SSA names are procedure-local, so one merged
  `defs` would have proved equalities between different values.
  **The measurement, one lift per tune per mode, full Songlengths, 20 of the 25
  exemplars.** OFF 22,002 lines / 1,688 stores; ON 21,999 / 1,685. Eighteen tunes
  are byte-identical on the two paths; `Deek/4_Tunes` loses 2 statements and
  `Gray_Matt/Atmosphere_II` 1, all three of them stores the axioms prove
  redundant, and **no exemplar regresses**. 9,980 emitted sites, 1,712 changed by
  saturation, every one Z3-proved. Slowest emit 119.5s (`Angry_Birds`).
  **The flag default stays off**, because the review is not complete rather than
  because it failed: four exemplars (`Down_Under`, `Wizball`, `Automatas`,
  `Before_I_Forget`) exceed the measurement wall-clock on both paths — the bound
  holds per procedure, but extraction over a many-procedure artifact is not
  bounded by it — and `Daf/Alioth` refuses its own §6 proof with "site
  environment is unsatisfiable", which is the anti-vacuity guard firing on an
  inconsistency in the definitional encoding that must be diagnosed before any
  flip. Those five are 3b's next work, with the join model and whole-chain region
  extraction.
- **2026-08-10 — the canonical example grows shadowing, three voices and hard
  restart; four findings 3c/3d owe.** The prototype is now three structurally
  parallel voices (lead, bass, offbeat arpeggio), each with its own script,
  cursor, SMC dispatch and hard restart, composing the envelope/control lanes
  in a RAM SID shadow that the frame flushes ADSR-before-gate. What it surfaced:
  (1) **The memory axioms forward; the printer refuses the result.** The
  saturation does union `sel(shadow-chain, a)` with the composed value —
  constants come out forwarded (`sid.v1.pw_lo = $00`) — but `pick_ir` keeps only
  candidates with no `cell`/`load` anywhere and, when none survives, falls back
  to the *raw unsaturated* term, which is the shadow read-back. So any shadow
  value that itself reads a state cell is spelled from the shadow, never from the
  cell. The fix 3c owes is a cost, not a filter: shadow/SID read-backs priced
  expensive, after which root extraction drops the now-unread shadow stores on
  its own. The example does it as a fourth fold, `forward_shadow`, each instance
  proved over the array theory with intervening writes as concrete-address
  `Store`s and locals version-keyed, then prunes the unread stores.
  (2) **Forwarding a carry chain through a shadow read-back is where the e-graph
  explodes.** One voice, measured: 3 read-backs 4.6s/0.8GB, 5 read-backs
  53s/3.0GB, 7 read-backs OOM at 4GB — but 7 read-backs cost 3.3s when no wide
  ADC term is in the block. That is why the example shadows three lanes per voice
  and writes frequency straight to the chip. Stage 3's "bounded schedules" is not
  optional tuning; `rs * 30` to fixpoint has no bound on this shape.
  (3) **The extractor spells one idiom two ways in one routine.** The
  deferred-carry advance now emits as `p=lo; t=p+k; lo=t; cflag=(t<k); if !cflag`
  at some sites and as `p=lo; lo=p+k; if (k <= p+k)` at others. A syntactic
  matcher catches one of the two. The example's rule now inlines the window's
  temporaries and hands the guard to Z3 instead of comparing it — proving the
  guard's meaning is what makes the rule spelling-independent, and that is the
  shape the convergence tests should assert.
  (4) **Isomorphic voices, non-isomorphic guards.** The three voices are the same
  code at shifted bases, yet voice 1's advance folds `wide` and voices 2 and 3
  fold `nocarry`, purely because of where each script landed relative to a page
  boundary. Per-voice re-rolling (3d) must treat an observed-guard difference as
  unifiable under a guard, not as a structure difference, or the isomorphism it
  looks for will never be total. Noted in passing, unexplained: the minimized
  text still carries two dead `cflag` defs — the vibrato ADC carry at the voice-1
  and voice-2 tail boundaries, read by no statement, absent for voice 3 — so
  root extraction is keeping something at a region boundary that no root reaches.
  Both orderings hard restart depends on —
  ADSR-before-gate within a frame, and zero-ADSR then TEST then waveform+gate
  across frames — are reproduced by the minimized program exactly, and the test
  asserts them on both sides rather than trusting last-write-wins.
- **2026-08-10 — stage 3b, landing 1: the two blockers were name-and-shape defects,
  and the review is 25 of 25.** Every number below is `tools/eqlift_measure.py`'s
  output over the exemplar set at full Songlengths, one lift per tune per path.
  (1) **Alioth's §6 refusal was an encoding defect, and a lift defect with it.**
  `render_proc` drew def versions from one counter and havoc/join versions from
  another into one `<base>.<n>` namespace; where the counters collided a havoc name
  *was* a def name, so the e-graph unioned a register's post-call value with its
  pre-call definition and the §6 encoding read the collision as an equation — on
  Alioth `x.3 = x.3 + 1`, the unsatisfiable environment the anti-vacuity guard
  refused. The visible half is the worse half: a `call` between `a = $05` and
  `a = a + 1` printed `zp_40 = $06`, a value folded across a boundary the join model
  opaque-resets. One counter for every fresh name. Alioth now proves 481 emitted
  sites, 168 changed, no refusal — the guard was right, and there is nothing left to
  record as a named refusal.
  (2) **The four unmeasured tunes were not slow, they were exponential.** `m[a] =
  m[b]` embeds the memory chain in its own stored value, so an unnamed chain doubles
  per such store: `Down_Under`'s first procedure is a 340-node DAG whose tree
  expansion is 2.5e11 nodes, and the egglog build asked for 49GB and was killed
  before saturation ever ran — on main as on the branch, so extraction time was the
  symptom and not the cause. `render_proc` names each memory version (`memk(n)`
  unioned with the store over the previous name) exactly as a def names a value. The
  e-graph is unchanged, so every axiom still fires through the same e-class; the
  emitted text is unchanged, because `_to_ir` drops a `sel`'s memory argument; and
  the §6 record carries the version definitions, so a forwarded load is still proved
  against its own store chain and not against an opaque array. `Down_Under`: killed
  at 49GB, to 0.6s at 0.10GB.
  (3) **Extraction has a bound now.** `DI_EQLIFT_EMIT_S` is divided over the
  procedures still to render, so slack from one funds the next, and the share funds
  saturation (capped at `DI_EQLIFT_BUDGET_S`) and then extraction; a site past the
  share renders from its own term, which the renderer discipline already makes
  position-correct and which extraction's cutoff-soundness makes free. It is never
  silent: `emit`/`emit_mem`/`render_proc` take a `stats` dict carrying the
  extraction-site and fallback counts. At the default 60s three exemplars take it —
  `Angry_Birds` 470 sites, `Athena` 344, `4_Tunes` 60 — and at
  `DI_EQLIFT_EMIT_S=600` none do. A binding budget makes the artifact a function of
  the clock exactly as 3b's saturation bound already does, so ON/OFF is compared at a
  budget that does not bind: at 60s an earlier run had `Frantic_3_tune_5` one line
  longer ON, and at 600s that tune is byte-identical on the two paths.
  (4) **The two dead `cflag` defs the canonical example carried are a liveness
  defect, not a root-extraction one.** They survive on the liveness path too, so they
  predate 3a, and neither `roots()` nor `_root_keep` is at fault — `ret_live` is
  empty and the closure never names them. `eqlift_mem._liveness` is
  `frameproc._Flow` transcribed onto the render tree, and the transcription dropped
  `_Flow`'s successor-aware cases: a `dgoto`/`igoto` the next statement's `swg`
  enumerates, and a `dcall` an `swc` enumerates, land in one of those arms —
  `frameproc._open_flow` is that same invariant — so they read what the arms read
  and not `info.G`, every register the program reads anywhere. Each voice's loop
  holds `goto (ptr)` before its `switch`, so from the voice-1 and voice-2 tails a
  computed transfer was reachable with no intervening redefinition of the flag;
  voice 3 has no nested voice after it and its definition already died. `swg`/`opsw`
  arms now take the switch's own live-out instead of the empty set — an arm that
  falls off its end continues after the switch, which is what the blanket `info.G`
  was covering for — and `swc` stays conservative, its bare labels being called with
  no inline body. The example loses 5 lines (276 to 271); the exemplars lose 94 on
  both paths.
  (5) **The review, 25 of 25, at full Songlengths, both paths, every site proved.**
  OFF 27,769 lines, ON 27,767; stores −3; 13,909 extraction sites, 12,449 emitted
  sites proved, 1,971 changed by saturation and every one of them Z3-proved. Zero
  faults, zero refusals, zero regressions; 21 tunes byte-identical on the two paths,
  `Gray_Matt/Atmosphere_II` −1 line and −1 store, `Tel_Kees/Before_I_Forget` −1
  line, `Deek/4_Tunes` −2 stores. Slowest emit 60.4s, which is the budget.
  **The flag default stays off.** The review passed; the join model is landing 2's
  work, and a landing that measured the flag does not flip it. The harness is
  `tools/eqlift_measure.py` — `dump` writes the exemplars' frameprog texts once,
  `run` lifts both paths off those texts, `report` is the rollup that is this gate.
  (The drafted call/goto closure this entry parked in `docs/join-model-footprints.md`
  landed as `eqlift_mem.Footprints` in landing 2; the draft is deleted with it.)
- **2026-08-10 — the prototype pins the goal, and the pins measure the gap.**
  `tests/test_state_machine_lift.py` gains seven goal-level properties as strict
  xfails, each enumerated from the artifact (the rendered state block, the roles
  map, the folded AST) rather than from a hand-written cell list, so a voice or
  feature added to the example is covered by construction. What they measure
  today: the role-typed text still names two architectural registers (`a`, `y` —
  `x`, `sp` and the four flag cells are already gone, the last of them with 3b
  landing 1's liveness fix); the six SMC JMP-operand cells (`m_103F`/`m_1040`,
  `m_111D`/`m_111E`, `m_11FB`/`m_11FC`) are declared `parameter` state although
  their only reads are the computed transfer, and the same six are the only
  declared cells written before every read in every frame — read-before-write is
  measured by instrumenting the minimized machine's own RAM over the run, so the
  "no scratch" and "no anonymous SMC state" pins are one defect counted two ways;
  no operator set is declared for the script interpreter and the scripts print as
  bytes; no `cursor` names its block and no cell carries an initializer, the two
  emission shapes stage 4 owes; and the round-trip witness fails on the missing
  re-emission capability, which is where it must fail until stage 4 builds one.
  Two green guards ride with them: the rendered text is byte-identical under two
  `PYTHONHASHSEED` values (adoption §10's closed defect, kept closed), and a size
  ratchet pins 243 rendered lines / 679 extracted term nodes as ceilings.
- **2026-08-10 — the canonical example grows eight extensions; one fold rule
  replaces five, and the residue is pinned.** The prototype now carries an
  indexed span store on one arm of a branch a disjoint pair crosses, a page-zero
  row whose index arithmetic wraps in 8 bits, a shared note fetch reached by
  `JSR` with a `PHA`/`PLA` spill, portamento as a byte borrow chain over a
  bounded step, a 24-bit accumulator whose carry-out outlives its add, a filter
  block on voice 3 (the cutoff lane pair and two `(s & K) | v` flag cells), a
  variable-arity `rawsid` operator, and an isomorphism check over the voices.
  Everything is green end to end — VM projection, write-application grid,
  pysidtracker's engine, the sidplayfp/sidtrace change stream — the pipeline in
  **2.9s** and the whole example, oracle included, in **16s**; what is not yet
  reached is `xfail(strict=True)` against its stage. What it found:
  (1) **One proved rule subsumes five matchers.** `prove_wide` takes a run of
  adjacent byte-lane stores (locals inlined), *searches* the operand off the
  cells those terms read — the pair itself, an adjacent pair, a byte, or the
  constant the terms reduce to at zero — and hands the candidate to Z3 at width
  `8n+8`. The same rule reads the portamento step (`pitch:u16 += slide:u8`), its
  borrow chain (`diff:u16 = note:u16 - pitch:u16`), the snap (`pitch:u16 =
  note:u16`), the 12-bit pulse-width and 16-bit cutoff accumulators, and the
  3-lane phase accumulator with its carry-out (`phase:u24 += $5E2B91 ; carry ->
  tick`). `prove_wcmp` does the same for the guard: the branch the borrow chain
  feeds becomes `if (note:u16 < pitch:u16)`, proved rather than matched, so every
  spelling folds. This is the shape stage 3c's rules should take — search the
  operand, prove the instance; do not enumerate shapes.
  (2) **A lane-by-lane copy is not evidence of a wide quantity.** `zp_49 = $00;
  zp_4A = $00` (AD and SR, two independent SID bytes) proves as one u16 copy,
  because two independent byte moves always do. The rule now admits a copy only
  onto a lane group some carry-linked update already targets, which is a two-pass
  fold: evidence first, copies second. Without it the artifact declares AD:SR a
  16-bit cell — sound, and wrong.
  (3) **#144's finding (3) recurs for pair stores, and the fix is the same
  lookback.** The extractor forwards a cell store into the following SID lane
  store where nothing intervenes (`filter.cutoff_lo = (ctr5 - $80)`) and re-reads
  the cell where a branch join does (`sid.v1.pw_lo = ctr_0034`) — one idiom, two
  spellings, in one routine. The pair rule now resolves a lane term to the cell
  whose last store holds that same term with its locals undisturbed, after which
  all seven multi-byte registers are written wide.
  (4) **The join is where forwarding stops, and it is measurable.** The value the
  arm stores into the indexed row *is* forwarded into the arm; the same value
  read after the join is spelled from the cells. That is the 3b-landing-2
  obligation as a one-line assertion on the emitted text
  (`test_join_forwards_the_crossing_cell`). The `PHA`/`PLA` spill likewise
  survives as the state cell `m_01FB` with `ROOT_EXTRACT` off.
  (5) **The wrap guard holds, end to end.** The zp row stores at `mem[zext2((c +
  $20))]` with `c` cycling `$F0..$0F` and reads back at a second wrapping cursor
  in the same frame; the read is not forwarded, both halves of the row are
  written, and the frame streams match — the 3b wrap guard's witness, and the one
  place where "the analysis could not look through it" is the right answer.
  (6) **#144's unread `cflag` definitions are closed, and the closure is now
  measured.** `dead_local_defs` runs a CFG liveness over the flattened emitted
  statements. Against #144's emitter it reported exactly three dead
  `cflag = carry(...)` definitions — the two voice-tail boundaries #144 noticed,
  plus one in the voice-independent head; against 3b landing 1's (015a2e3) it
  reports **none**, so the name-collision fix was the whole defect and the test
  is green rather than pinned. The liveness is worth keeping: an unread
  definition is not structure, which is what makes the voice isomorphism of (7)
  read as structure.
  (7) **Isomorphism was made guard-aware, not script-placed.** Voices 1 and 2
  come out equal at **63 normalized statements** and voice 3's core equals them
  with a **13-statement** filter block spliced in. The normalization that had to
  be there is #144's finding (4): the observed-carry guard is dropped from the
  advance, since voice 1 folds `wide` and voices 2-3 `nocarry` purely by script
  page position. Placing the scripts so the guards agree was the alternative and
  was rejected — it is a layout accident that any edit to the example moves,
  while a guard-aware check is what 3d's re-rolling needs anyway.
  (8) **The emit cost this example was sized against is gone.** On #144's
  emitter the grown program cost 14–28s to emit at 800 frames and hit the
  `DI_EQLIFT_EMIT_S` 60s wall at 200 — a shorter trace leaves more `unobserved`
  arms and more distinct spellings, so it cost *more*. On 3b landing 1's it
  emits in **1.2s at either length**, so the same fix that closed (6) removed the
  cliff; #144's shadow-lane budget (three lanes per voice, frequency written
  direct) is kept unchanged rather than re-priced on one measurement. Saturation
  output is stable across `PYTHONHASHSEED` 1/7/12345 (same proof set, same
  frames), and `test_render_is_hash_seed_independent` gates it.
  (9) **The role reading needs the same local inlining the folds do.** `$D418`'s
  update arrives as `a6 = zp_95; a = ((a6 & $F0) | $0F); zp_95 = a`, so a shape
  read off the raw statement misses `flags` entirely; inlining per straight-line
  run recovers it, and `flags` then lands on the three voice control bytes and
  the two filter cells. `flags` is also the *weakest* evidence — a masked index
  (`log_idx = log_idx & $0F`) is an accumulator, not a flag word.
  (10) **The variable-arity operator needs no table.** `rawsid` decompiles to a
  `loop` with a data-dependent trip count and a `ptr += Y+1` advance, and its
  indexed SID store renders as `sid.v1.freq_lo[a] = w` — a span store over the
  register file, which a width law must not read as a lane write. The minimized
  program executes it, which is `_ARITY`'s escape mechanism proven in miniature.
  (11) **Frame equality is not a correctness oracle for the program.** A helper
  returning its high byte in `Y` clobbered the caller's `(ptr),Y` cursor; VM and
  minimized program agreed frame for frame on music that had derailed. The
  invariant that caught it was the script's own consumption (each voice's cursor
  advancing one command per event), not the write stream.
- **2026-08-10 — the reconcile: five landings crossed, and the board is one
  record.** PRs #144–#148 landed from five concurrent agents; this entry is the
  cross-check. Facts that bind: (1) #145 merged via `--auto` on the three
  required checks while the non-required corpus job still ran; the post-merge
  main run (`31353472314`) completed green, and merge discipline from here is
  every check including corpus, no `--auto`. (2) The size ratchet's pins are
  the test's, not the log's: #147 pinned 243 lines / 679 term nodes, #148
  re-pinned 461 / 1192 when the prototype grew eight extensions — a feature
  landing re-pins, a stage-3 landing holds or lowers. (3) Two pins flipped
  green before their stage: the four flag registers left the residue with
  landing 1's liveness fix (test 1's surviving set is `a`, `y`), and #144's
  dead `cflag` finding closed measured (3 → 0 unread defs) by the same fix.
  (4) The catalog's `stack-slot` row, the prototype's landing-2 xfails and the
  shredder join fixtures now name one mechanism from three directions, which
  is what a stage landing should walk into. The next landing is named in the
  stage 3 position block above: **3b landing 2, the span join.**
- **2026-08-10 — stage 3b, landing 2: the span join, and the stale local it exposed.**
  Every number is `tools/eqlift_measure.py` over the 25 exemplars at full Songlengths,
  both paths, `DI_EQLIFT_EMIT_S=600` so the budget does not bind (landing 1's
  precedent), beside the same run on `622ced8`.
  (1) **A store no enumeration can list can still bound a join, if the join enumerates
  what it keeps.** `_mem_writes` returns a write span `(lo, hi, width)` per non-const
  store — the tighter of the `mem_rules` lattice and the `addr_floor`/`addr_bits`
  bridge, read with no env because a local's reaching definition at the join is not
  the definition it carried inside the arm — and `_join_mem` is complemented: a fresh
  opaque memory carrying exactly the chain-held const cells proved disjoint from every
  span. Listing the row's cells to forget is impossible; listing the cells outside it
  to keep is not. Each disjointness is a Z3 QF_BV proof over *every* address in the
  span, cached, never a structural match — a store width reaching past `hi` refuses,
  and so does a cell inside the row. `Proc` and `render_proc` call the one helper, so
  branch, loop head, switch and call are one mechanism, and an unbounded address, a
  `label` and any dynamic transfer keep ⊤.
  (2) **The drafted call/goto closure landed as `Footprints`.** What entering at a pc
  may write over the enumerated call graph, as its least fixpoint; a pc no procedure
  owns and a procedure holding a transfer the map cannot follow are ⊤, so nothing
  rests on the dispatch guards. `docs/join-model-footprints.md` is deleted with it.
  (3) **egg priced at 1 the spelling the consumer refuses.** `pick_ir` has always
  dropped every candidate that mentions memory (#144's position defect), while `sel`
  carried egglog's default cost — the cheapest constructor there is. As the join
  unioned memory versions into value classes, `extract_multiple`'s variants filled
  with spellings the consumer must throw away and sites fell back to their own raw
  terms: the prototype's deferred-carry temp regressed from `((ctr1 + $40) < $40)` to
  `carry(ctr1, $40) | carry((ctr1 + $40), $00)` (`carry` is `_COSTS` 12). Adoption §4
  already required the two cost models to be order-consistent; `sel` now carries the
  consumer's price and the residue goes the other way.
  (4) **A local renders as its base name, so availability was never the question.**
  `_defined_at` accepted any version defined on the path, and a base redefined since
  still prints as the base — so a site could spell a version the base no longer holds.
  Minimal case, and it is a miscompile, not a readability loss: `a = m_1000; b = a;
  a = m_1001; sid.ctrl = b` emitted `a = m_1000; a = m_1001; sid.v1.ctrl = a`, the
  wrong byte at the chip, with `b`'s definition deleted as unread. §6's proof cannot
  see it — it proves the SSA terms equal while the printer renders the base — so the
  check belongs where the version is known: a site now carries the versions **live**
  there, not the versions available there. On the exemplars this removes the visible
  half of the defect (`x = x` self-assignments 13 → 11, `Wizball`'s `cflag` and
  `Automatas`' `a` among them) and costs 16 lines. The 11 that remain are live
  self-copies of architectural registers — sound no-ops `_share_once` skips by rule
  and `_dce` keeps because the register is live; 3c/3d's residue, enumerated in
  `out/eqlift-texts-l2b-600`.
  (5) **The review: 25 of 25, clean.** OFF 27,787 lines / ON 27,783, `d_lines` −4,
  `d_stores` −3, 22 tunes byte-identical on the two paths, 13,909 extraction sites,
  2,124 changed by saturation and every one Z3-proved (12,465 proved sites); zero
  faults, zero refusals, zero regressions, zero extraction fallbacks. Slowest emit
  84.6s against main's 87.3s. Against main the artifact is **+28 lines over 27,759
  (+0.10%)**, 13 tunes moved, +1 to +4 each: +12 of it is the span join spelling a
  crossing value instead of a machine flag (`Down_Under`'s `if zflag` becomes
  `a = m_7944; if (a == $00)` — a line for an architectural register, which is the
  direction stage 4's metric asks for) and +16 is (4)'s correctness. Emit identity is
  untouched by construction (it is the frameprog artifact): `624 tunes, 0 refused,
  28,512,406 bytes`, aggregate `99d4fdec…` unmoved; `gate_sweep` at full Songlengths
  622 evaluate / 621 clean with the same three named tunes; suite 2,777 passed / 35
  xfailed, oracle 16 passed.
  (6) **The two landing-2 pins, one flipped and one re-pinned with its measurement.**
  `test_join_forwards_the_crossing_cell` is green: the pw pair crosses the join as the
  values the head computed (`sid.v1.pw_lo = (ctr1 + $40)`, `sid.v1.pw_hi = t2`), which
  is where the property lives — the *folded* artifact cannot show it, because #148's
  pair rule resolves a forwarded lane back to the cell holding it, and that rule is
  what writes all seven multi-byte registers wide. The pin's `$0140` proxy was
  measuring the fold, so the assertion now reads the emitter's own text and the folded
  text is byte-identical to #148's (461 lines, both ratchets held).
  `test_stack_spill_forwards` is **re-pinned at 3c**, and the refusal is executable
  (`tests/test_eqlift_mem.py`): `sel_store_same` *does* forward the pull to the pushed
  value in the e-graph, and the slot survives the text for two reasons that are not
  the join's — `pick_ir` admits no memory spelling (3c owes the cost with a
  position-correctness argument, not a filter) and every surviving store is a root
  until scratch demotion, which `test_state_block_holds_no_scratch` already pins.
  (7) **The shredder join fixtures cannot flip here, and the reason is structural.**
  `tests/test_shred_regmodel.py`'s `_lift` runs `frameprog.program`, not
  `eqlift_mem.emit`: those xfails are pinned to the *frameprog* emitter and flip when
  adoption §8's step 4 makes the unified graph unconditional, not when a join model
  lands behind the flag. They stay pinned, and the join mechanism is gated by
  `tests/test_eqlift_mem.py`'s own join cases instead.
  (8) **The prototype's fold window is the same fact from the other side.** A lookback
  reset at every branch cannot read a cell the join carries, so `_cross_window` keeps
  the entries no body of the branch can disturb and drops every cell it stores, every
  span it stores through, and every term naming a local it assigns. `_base_split` also
  refused a local as a pair base, which the forwarded lanes reached for the first time.
- **2026-08-10 — stage 3b, landing 3: the in-edge map at labels, and `ROOT_EXTRACT`
  goes on.** The review is `tools/eqlift_measure.py` over the 25 exemplars at full
  Songlengths, both paths, `DI_EQLIFT_EMIT_S=600`.
  (1) **A label the map shows no edge entering is not a join.** `Footprints.joins(pc)`
  reads the same call/goto closure as an in-edge question — the walk's own
  fall-through is the only edge — so a `label` no `goto`, `call` or `swc` names, that
  is no procedure entry and no RTS-trick landing (`framefuse._landings`), keeps the
  chain instead of havocing it; `_Info.open_flow`, a transfer no map enumerates, makes
  every label in that artifact a join again. Over the exemplars **149 of 557 label
  havocs retire** and the emitted text does not move a byte. That is the measurement
  the mechanism owed and it says what the map is worth: **144 of the 149 are the first
  statement of a body**, where the walk already enters with the state the label is
  entered with, and 5 follow a `loop`. The blanket havoc was unjustified rather than
  load-bearing; the labels that still reset are the ones with real in-edges, and
  closing those needs a join over the in-edge *memories*, not a lookup.
  (2) **Whole-chain region extraction is what landing 2 already bought, and the region
  now ends in enumerable places.** Extraction has always run over the whole procedure
  (one e-graph, `_root_keep` over the whole render tree); what 3a lacked was a chain
  that crosses a join. Instrumented over the 25 exemplars, `_join_mem` is reached
  2,528 times and **carries cells at 1,628 of them (64.4%)**: `if` 1,342 carry / 585
  ⊤, `loop` 204 / 124, `call` 76 / 162, `swg`+`swc`+`opsw` 6 / 29. Every ⊤ is one of
  four named causes — a store address with no interval, a dynamic transfer, a call
  the map cannot follow, or a label with in-edges.
  (3) **`whole()` would have bought nothing, which is why ⊤ is the right answer for
  what the map cannot follow.** The draft bounded an unenumerable transfer by the
  union of every procedure's footprint. Measured: **0 of 25 exemplars have a bounded
  whole-program footprint** — each has at least one store whose address is ⊤ — so the
  union is ⊤ on every one of them, and taking ⊤ directly costs nothing and rests on
  no argument about dispatch guards.
  (4) **The `ROOT_EXTRACT` default decision: on.** The review is clean — OFF 27,787
  lines / ON 27,783, `d_lines` −4, `d_stores` −3, 22 of 25 byte-identical, 13,909
  extraction sites, 2,109 changed by saturation and every one Z3-proved (12,465 proved
  sites), **zero faults, zero refusals, zero regressions, zero extraction fallbacks**;
  slowest emit 86.0s at a budget that does not bind. ON is never worse than OFF on any
  tune and better on three (`Deek/4_Tunes` −2 lines/−2 stores,
  `Gray_Matt/Atmosphere_II` −1/−1, `Tel_Kees/Before_I_Forget` −1). `ROOT_EXTRACT` is
  therefore **on by default**, `DI_EQLIFT_ROOT_EXTRACT=0` selects the liveness path,
  and `tests/test_eqlift_mem.py` keeps both paths gated. The byte-identity aggregate
  is unmoved **by construction and by measurement**: `emit_identity` is the frameprog
  artifact, which does not read this flag — 624 tunes, 0 refused, 28,512,406 bytes,
  `99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942` with the flag on.
  `gate_sweep` at full Songlengths 622 evaluate / 621 clean with the same three named
  tunes; suite 2,778 passed / 35 xfailed; oracle 16 passed. The canonical example is
  byte-identical on both paths (461 rendered lines, 677 emitted), so the flip does not
  re-pin either ratchet.
  (5) **What the flip does not do.** Adoption §8's step 4 — deleting `_dce`,
  `_temp_sweep`, `_liveness`'s deletion role and the flag itself, and routing frameprog
  emission through the unified graph — is not this landing: the flag still selects, and
  the shredder's stage-3 xfails are pinned to the *frameprog* emitter, so they flip
  there and not here.
