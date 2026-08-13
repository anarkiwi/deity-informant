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
  `follin_script._ARITY` was the one standing exception — a hand-transcribed
  per-tune table, a named debt — and stage 3d discharged it by recovering the
  same arities from the dispatch arms (housekeeping below); no table takes its
  place.
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

**Moved since, and it is the only movement:** the three standing exclusions are
diagnosed to root cause (docs/frameprog.md §7.10.16 and the §5 risk register).
`Rambo_First_Blood_Part_II`'s divergence was a `for` counter read as the constant
in force *before* its loop — Galway's own `rambload.asm` (`NOTE1`/`n1sl2`) is the
ground truth, and the walker was right — and is **closed**: `gate_sweep` at full
Songlengths is now **624 build; 622 evaluate / 622 clean**, no standing Gate FP
divergence left, no other verdict moved. The two evaluation faults were
**one cause, not two**: `C64_World` (frame 189) and `1st_Decent_Hardcore`
(frame 508) are the same `CyberTracker_exe` image faulting at the same
inline-parameter `JSR` return, and it is a **lift defect, not the claim
boundary** — the machine never executes `$4ED7`, so the guard is right and the
control flow that reaches it is wrong. The fix moves one tune's emitted text and
623 of 624 are byte-identical, so the aggregate moved to
`946f0dcb082fc4df0814505b5eb42a8dd677f70bcfe94deeb245c2132f1c6ec0` over the same
624 (28,512,265 bytes). It moved once more, on a **reviewed §4 cost diff** (3d
landing 2): admitting `pack_add` beside the price that names the OR-built pack the
normal form respells one line in 49 tunes and nothing else, taking it to
`434f0bab009a2543da69f7997a5c279af4f9e390fc894f601bce262e515c7c72`
(28,512,657 bytes). **Stage 4 landing 1 closed the two evaluation faults** on the
return-slot continuation, so the corpus is now **624 build; 624 evaluate / 624
clean, zero divergences and zero refusals** and the standing baseline is
`37b871408ea4344dd60e562f44825730748528a49fc247d47828eeb7aae2ce23` over 624
(28,513,156 bytes), 2 tunes moved and 622 byte-identical.

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
decompile + walker replay, `eqlift_mem.emit` minimization, six spelling folds
(the shadow store-to-load forward retired at 3c landing 1, subsumed by the
emitter's memory price), every instance Z3-proved:
the paired u16 SID store, the deferred-carry advance and the wide
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
it: any byte-lane update of a declared-u16 quantity (3c). The branch-join forward
is green as of 3b landing 2, the shadow read-back as of 3c landing 1, the
stack-spill forward as of 3d landing 1, and the declared lo/hi pitch row and
per-voice re-rolling as of 3d landing 3.

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
  idiom, a parametric generator produces the spelling variants, and the test
  asserts they merge into **one e-class** with the catalog's normal form
  extractable from it. **Landed, 3c landing 2**:
  `tests/test_eqlift_converge.py`, enumerated from `idioms.FORMS` so a catalog
  row added later fails until it has a case — 20 rows carry a generator, the
  three that spell one way name why. It named four rules on its first run and
  they are admitted with their proofs (`pack_hi`, `pack_lo`, `zext_mask` over
  QF_BV; `sel_pair` over the array theory); one more is proved and pinned
  rather than admitted, because its cost is a §4 decision. The shredder's own
  stage-3 xfails stay pinned to the *frameprog* emitter (3b landing 2 (7)),
  which is where they flip. A variant that fails to merge names a missing
  rule, and the rule lands only with its Z3 proof.
- **Not equational, kept as small classical passes.** Per-voice re-rolling
  (anti-unify the unrolled voice slices; total isomorphism or keep the
  copies), for-range recovery, and SMC opcode dispatch, which stays the
  observed-variant `switch` behind guards. **Re-rolling landed at 3d landing 3**
  on the prototype, with the leaf-callee resolution the declared row read needed:
  a voice's slice is a context whose hole is the next voice's, so a total
  anti-unification of two adjacent contexts is a loop over them, and the one
  difference that is not structure — an observed page cross — unifies under a Z3
  proof. The cutover carries the same three parts onto the unified emitter.
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
on a clean 25-of-25 review), 3c landing 1 (the memory spelling's price with its
position-correctness walk, in place of `pick_ir`'s filter — −326 lines over the
exemplars, and the spelling-independent advance rule that re-spelling forced),
3c landing 2 (the per-idiom convergence harness and the four rules its first run
named, one more proved and pinned on a cost decision), and **stage 3d, in four
landings**: 1 — the `Footprints` read closure whose deref spans are 2b's observed
extents, scratch demotion off the artifact-wide reader set, and the in-edge memory
join at a label the walk has passed every edge of; 2 — `pack_add` admitted with the
§4 cost change that names the OR-built pack the normal form; 3 — guard-aware
re-rolling and the declared lo/hi pair row read, with the leaf-callee resolution that
gives the row read its site; 4 — general associativity retired for the one directed
instance the lane fusions need, and the emit budget divided by work.
What 3b handed 3c stands unchanged: the join carries 1,628 of 2,528 walls and
the remaining 900 are enumerated by kind in the decision log. **Stage 3c is closed**
and its four named items are all discharged by 3d — the read closure, the row read,
`pack_add`'s cost change, and the join over the in-edge memories — as is the
`_ARITY` debt (housekeeping below).

**Stage 3d is closed (2026-08-10).** Every property it was pinned against flipped
on its own mechanism, none by a schedule: `test_stack_spill_forwards` (landing 1),
`test_the_adc_built_pack_converges_on_the_ora_built_one` (landing 2),
`test_rerolling_unifies_the_isomorphic_voices` and
`test_note_fetch_is_one_u16_row_read` (landing 3). The prototype's ratchets fell
455 → **339** rendered lines and 1149 → **836** extracted term nodes across the
stage; the standing emit-identity aggregate moved exactly once, on landing 2's
reviewed §4 diff, and is `434f0bab…` over 624 tunes (28,512,657 bytes); the
25-exemplar review is clean at every landing, most recently OFF 27,461 / ON 27,445
with 13,909 extraction sites, 3,309 changed and every one Z3-proved, zero faults,
refusals, regressions and fallbacks. The one pin 3d re-pinned rather than flipped is
the shredder's `dispatch_scratch_promotes`, and it names its extension below.

**What 3d hands step 4**, each with its mechanism and its owner:
- **The `swc`-label extension of the in-edge join — WITHDRAWN (2026-08-11).** The
  arm's label was never a join (`Footprints.joins` is already False for it); what reset
  the memory was the computed transfer standing before the arm table, and the pairing law
  `frameval.seq` already states is what moved the pin. See the decision log.
- **The `low_held_cursor` rung landing — LANDED (2026-08-11), on another premise.** The
  deref span was never it: the deref is a *read*, and what a read refuses is dropping the
  slot's store, not the slot's identity. See the decision log.
- **The `state { }` declaration of a demoted cell.** `frameprog._state_lines` derives
  the block from `_cells(view)` and not from the stores extraction kept, so a cell the
  read closure retired from the body still declares; the fix is a
  `framestack.drop_state` analogue keyed on demotion.
- **The splice blocker, pinned.** Rung (d2) mints a width-one narrowing `COPY` that
  `eqlift_mem._OP` maps to nothing, so splicing frameprog's statements into
  `render_proc` raises `KeyError('COPY')` before any rule fires (#161, now executable
  as `tests/test_step4_splice.py`'s strict xfail on the canonical example).
- **Re-rolling on the unified emitter.** Landing 3's pass is the prototype's, which is
  where the plan puts its classical passes and where the pins are; the cutover carries
  the same three parts — context cut, anti-unification, guard proof — onto
  `eqlift_mem`'s render tree.
- **The schedule's own §4 order, assigned here: step 4's cutover owns it.** The
  saturation and extraction bounds are wall-clock and memory-growth, which makes
  minimization non-monotonic in the budget — measured at 3d landing 4, one
  configuration's 60s artifact was *smaller* than its own 600s artifact — where a
  deterministic round cap would not be. It is a §10 determinism question dressed as a
  cost question, and it is the cutover's because the cutover is what makes the budget
  bind on the shipped artifact.
- **Flat n-ary associative nodes.** Landing 4 retired general associativity for one
  directed instance; a chain whose regrouping no admitted rule names still extracts as
  spelled, and the standing answer is the encoding `docs/symbolic-recorder.md` already
  uses for `INT_ADD` — not a rule.
- **Stage 4's own runway** is below: the CyberTracker `jsr`-continuation fix that
  clears the two evaluation faults with its shredder fixture family, and the witness's
  two remaining refusals (the raw machine call and the static image vector).

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
per-voice unified where the isomorphism is total (else the copies stay); a play
routine that dispatches through an SMC operand emits the operator set that shape
carries, whatever family wrote it. The witness, when wanted: re-emit minimal
6502 from the minimized program and replay it under the VM against the
oracle — an end-to-end check with no evaluator in the trust chain. (Landed:
`deity_informant/witness6502.py`, the whole dialect and every dispatch form
witnessed, the canonical example replayed frame for frame off the machine; the
raw machine call and the static image vector are the named refusals left.)

**The runway the diagnosis left it** (#155) is **landed, landing 1**: the two
remaining evaluation faults (`C64_World`, `1st_Decent_Hardcore` — one
CyberTracker build) clear when the `jsr` continuation is taken from the return
slot the callee wrote, and the two halves the diagnosis measured are both
required (frameprog.md §5) — `frameproc.slot_reader` refuses the `pcall`
promotion for a callee that consumes the slot, and `frameval`'s `ret` reads the
slot rather than the call's textual successor. `gate_sweep` at full Songlengths
is **624 build; 624 evaluate / 624 clean**, zero divergences and zero refusals,
and the shredder's four-fixture family pins the trick (one site, two sites, two
depths, per-site skip length) rather than the two corpus tunes alone.

**The position (2026-08-11): the stage-4 record, and the ledger's schedule to zero.**
Landings 1, 2, 3 and 5 are closed, landing 4 is half landed, and landing 6 is the close.
**Landing 1** took the CyberTracker `jsr` continuation from the return slot the callee wrote,
and the corpus gate went clean for the first time since the plan opened (#169). **Landing 2**
is adoption §8 step 4's cutover, built part by part: the narrowing `COPY` becomes a term so
the splice reaches the rules (#170); the dialect gains the signed compare while the unified
renderer learns the layout, the dispatch headers and the statement set it prints, `pcall`
included (#171); `eqlift._Printer` reads an address at `frameproc._index_of`'s breadth,
routes a register-file base through the `sid.reg` view and takes the ONE `_PAIRS` registry
(#173); §5's liveness scaffold is deleted (#174); the saturation schedule becomes a round cap
and a node bound, so no clock reading reaches the artifact (#175); a cell the data section
declares stops being declared twice (#176); and the stage-3 pins are re-measured against the
cutover's own emitter (#177), with landing 1's owed `returns` set **refused on a measured
reason** — the only procedures it can relax are the ones `slot_reader` blocks, and those
return to a pc the call site does not name (#178). **Landing 3** made the unified emitter
corpus-worthy against a control, sixteen faults fixed (#179), gave every pin a live owner and
named the switch's blocker (#180, #181), then switched: `frameprog.dumps` renders through the
unified graph (#182) and the `state { }` demotion rides out of root extraction's own
`_scratch` spans (#183). **Landing 5** completed the witness — its last three refusals close
and the three `Asm` copies become one (#185), the arm table is read as the transfer's
successor set (#184), and the 25-exemplar witness sweep runs with the refusal ledger at one
(#188). **Landing 4** is half landed: the role keywords are ON and the steering metric has a
number (#187). Beside them, stage 4's own findings — the variable-stride cursor advance folds
(#186), and the shredder's standing pins carry live owners rather than emitter verdicts
(#189). The landing numbering is #178's, unchanged: **3** is the unconditional path (adoption
§8 step 4), **4** role-typed emission plus the steering metrics, **5** the witness completed,
**6** the housekeeping and the stage close. Landing 5 closed with exactly one refusal and it
is a claim boundary, not an owed landing: `Atmosphere_II` declares the volatile input `osc3`,
and pinning `$D41B` in the witness would put the evaluator's trace back into the trust chain
the witness exists to keep out.

**The metrics, measured fresh at this record** (one run each, off this commit). Three of the
numbers this replaces were carried forward rather than re-run and did not describe main; the
corrections are stated where they land, and nothing regressed — the record was behind the
code, not the other way round.
- `gate_sweep` at full Songlengths: **624 build / 624 evaluate / 624 clean**, zero divergences
  and zero refusals (`out/gate_s4l4c.json`; held across the denotation landing).
- `tools/emit_identity.py`: **624 tunes, 0 refused, 28,310,783 bytes**, aggregate
  `05c3a08ab518e6600bb39791d09e8680090ffa190d2990c7b336bf5a4d747352` (`out/emit_s4l4c.json`).
  The denotation landing moved it on purpose: **594 tunes — 572 smaller, 19 larger, 3 the
  same size, −74,509 against +1,224, net −73,285 bytes** against the prior
  `cfab6cff…`, reviewed by shape in the decision log. The 19 growths are §4's own price
  order, and are the multi-reader memory forward's first number.
- Suite: **2,783 passed / 490 skipped / 27 xfailed** (oracle included; 2,780 without it). The ledger is
  **27 strict xfails** — 21 shredder pins under five owners
  (rung (d) 13, rung (f) 4, `frameproc` 2, `framestack` 1, `datadecl` 1) and 6 the prototype's,
  every one of them landing 4's. The witness pin flipped at #191, below.
  *(Items 5, 6, 3, 4, 7 and 8 landed after this record: the shredder ledger stands at **0**
  pins — every owner has landed — and the prototype's last one,
  `no_architectural_register_survives_as_a_value`, flipped with the naming pass. The
  ledger is **0** strict xfails and the suite carries none. Decision log, below.)*
- `tools/splice_sweep.py` against its control (`out/splice_s4l4c.json` against
  `out/splice_s4pr1.json`): **71 bad, zero new and thirteen fixed** — the thirteen all
  divergences (50 → 37) — parse and fixpoint **624 of 624**, **207,184 rewritten sites
  proved, zero unproved**, emitted size **−7,379 lines with no tune larger** (585 smaller).
  The remaining gap is **25 evaluation faults, 9 lint, 37 divergences**.
- The role metric: **13,796 of 18,637 persistent cells role-named (74.0%)**, unmoved since
  #187 turned the keywords on.
- **The headline has a number, and it is measured on the artifact.**
  `splice_sweep` now reports `arch`/`zero_arch` — the prototype pin's own predicate read over
  the corpus text, architectural registers named as values with hex and comments stripped.
  **2 of 624 tunes wear zero machine shapes**, 184,367 register tokens in all (183,648
  before the denotation landing, which trades an unrewritten tree for the register holding
  its value: fewer lines, one more token, and the headline unmoved). The phased plan
  died with this headline at 0 of 624 and it was never measured against the artifact after
  that; the diagnostic that owned it, `tools/lift_residue.py`, reads `prog.procs` — the
  walker's projection, upstream of extraction — so it cannot see the emitted text at all. It
  stands at **0 of 624 zero-residue tunes and a census sum of 30,910** (the pivot's 30,854,
  drifted by the rung landings), which is the same reading it gave before the switch and is a
  statement about a different object. Landing 4 is what moves `zero_arch`, because the five
  role pins and the two extraction-order items are exactly what leaves a register spelled.
  *(The predicate this records was too narrow: it missed the emitter's versioned copies and
  three register-file names, and it reported no number at all for the temporaries a value
  also flows through. Widened and re-baselined in the final wave's first landing — decision
  log, below — `arch` reads 195,409 and `temps` 55,120 with `zero_arch` unmoved at 2.)*

**The zero-ledger plan, in order.** Every entry is `xfail(strict=True)`, so the landing that
reaches its property flips it and cannot pass silently; the bracket is what the landing takes
off the ledger, and the reasons in the tests carry the same mechanism words.
1. **Landing 4 — the prototype re-based onto the artifact [6 pins left, 27 → 21].** The
   re-basing is one change — `examples/state_machine_lift.py`'s fold layer produces a
   `frameprog.FrameProgram` and its render layer *is* `frameprog.dumps`, which retires
   `eqlift_mem.emit`/`emit_mem`, the second projection #181 named and #182 could not reach —
   and the path is already proved on this exact image: `tests/test_witness6502.py`'s
   `test_the_example_artifact_replays_frame_for_frame` runs `sml.build_image()` →
   `structured.decompile` → `frameprog.program` → `witness6502.emit(p).frames(n)` and is
   **green**. But **the re-basing alone flips only two of the seven**, and the scoping that
   measured this is what says so; the other five owe the engine something and their entries
   below name it. Per pin:
   `no_architectural_register_survives_as_a_value` — **LANDED**, below, and the scoping was
   right that the re-basing is not what flips it. What flips it is the naming pass the
   residue always named: a register is a machine location, so what earns a name is the
   **web** — the definitions that reach a common read — and the prototype had no such pass
   at all. The engine's own `arch` is untouched by it: measured on `Hubbard_Rob/Commando`
   the artifact still carries 151 register tokens, corpus-wide `zero_arch` is still 2 of
   624, and the pin was always the prototype's projection. This is the suite's **last**
   pin: it holds **zero** `xfail`s.
   `smc_dispatch_cells_are_not_data_state` — **LANDED (#202)**, below: a cell every read of
   which decides where the machine jumps is the transfer, and the transfer is already spelled,
   so the state row beside it goes.
   `vm_family_operator_set_is_emitted` — **LANDED (#200)**, below: the `operators { }`
   production, the name off the handler table, the arity off the cursor advance and the
   writes off the arm's own blocks.
   `state_block_holds_no_scratch` — **LANDED (#203)**, below, on the prototype: the second
   demotion notion is frame-boundary liveness-in, a backward liveness whose exits flow to the
   frame's own entry because the frame repeats. Nine cells are dead there and go. The engine
   owes the same notion over `frameproc`'s statement graph, which is a landing of its own.
   `roles_carry_their_evidence` — **not the re-basing**. The `in` clause is
   `ptrlift.apply_rung`'s and appears only when an extents artifact row is passed, so a bare
   `frameprog.program(model)` emits none; `observed` is the dispatch opcode byte set, not an
   accumulator bound; and `mask`/`bound` are in no grammar production, with `roles._mask_bound`
   returning the inner term and discarding the constant. It owes a clause and a carrier.
   `init_lifts_to_declared_initial_values` — **not the re-basing**. `sidprog.lark`'s `statedef`
   has no `= HEX` alternative at all, and `prov0` carries a cell's origin *address*, not its
   value (empty on both tunes measured; 185 of 600 cached artifacts carry any origins). It owes
   a grammar production and a value.
   `round_trip_witness_is_frame_identical` — **LANDED (#191)**, and it needed less than this
   entry predicted: not the fold/render re-basing at all, only the artifact *program* carried
   in `pipeline()` plus the entry hop — `sml.reemit_6502` wraps `witness6502.emit` and writes
   a three-byte `JMP` at `sml.PLAY` to `Witness.entry`, which is a fresh label in a free span
   where `sml.run_vm` hard-codes `INIT`/`PLAY`. The program refuses nothing (`inputs == []`,
   `extents == {}`), and the pin now reads `art["prog"]` rather than the fold tree, which
   strengthens it: the subject is the artifact.
   So the trunk flips **no pins at all**: the witness closed on the artifact program alone,
   and each of the six left owes the engine a named capability rather than a re-basing. That is
   the landing's real shape, and it is worth more than the schedule it replaces — the re-basing
   is still owed (it is what retires `emit`/`emit_mem`), but it is a cleanup, not a pin-flipper.
   **And it is not a small cleanup: the trunk was measured** (2026-08-11). Reading the artifact
   costs four small parser items — the signature (`sub_1000()`, `sub_1485(x) -> a, x`), a width
   suffix after a bare name (`ctr_0030:2`), the promoted call (`a, x = sub_1485(a)`) and the
   `trunc1` operator — and rung (d) does **not** take the fold layer's subject away: 69 of the
   prototype's 95 byte-lane spellings survive in the artifact and it carries **zero** fused
   `ptr_XXXX` pairs. What it does carry is width. The prototype's AST and `Machine` are
   byte-typed, the artifact is width-typed, and **21 names wear both a `:2` spelling and a bare
   byte use** (`ptr_0040_lo` nine times), so the width is the **site's** and not the name's — a
   name-keyed registry was built and measured and diverges at frame 0. Carrying it per site
   touches every fold rule, their Z3 proofs, `render`, `classify_roles` and `reroll`, whose
   voice unification already refuses the artifact's shape ("block of 6 against 8"). So the
   re-basing is its own landing, and the fold layer it ends with is smaller than the one it
   starts from: on the artifact only three of the eight `FOLDS` still fire (`pair_set`,
   `row_read`, `wide_cmp`), because the engine now does the rest.
2. **Landing 6 — the stage close [0 pins, two items gate it].** The song-model retirement is
   **taken** (`song_model.py`, `generators.py`, `movefwd.py` and their tests deleted;
   `eqlift_annotate` stays with `emit_mem`, which still calls it); §5's `_Prune`/`_inline` deletion; and
   the parse-and-evaluate gap, taken or refused by name. The close records items 3-8 as
   scheduled, not as waiting.
3. **rung (d), the pair premise [12 pins, 19 → 7] — LANDED.** The premise is per access
   site: a lone half is spelled through the word, a store as the lane update
   `(ptr & $FF00) | zext2(row)` and a read as that lane's trunc, so nothing about one site
   refuses another's pairing. What refuses is an indexed half store the pair cannot place, a
   **page-fixed** pair (a hi lane no path changes under a lo lane advanced in place — the
   `inpage_advance` fact, proved rather than assumed) and a pair with no word access. The
   nine-`u8` width gap below is three-ninths of the way closed with it, and the rest is a
   candidate-evidence question the entry below states. Decision log, 2026-08-11.
4. **rung (d), the widening guard [1 pin, 6 → 5] — LANDED.** `lone_lane` was the rung
   *widening* a lone half into a read-modify-write of a write-only register.
   `framefuse.write_only` refuses that inside `$D400`–`$D416`, which is every lane there is,
   so a SID word store is now always two stores the driver made and a lone half stays the
   byte it wrote. Decision log, 2026-08-12.
5. **`framestack`, the slot identity [1 pin, 8 → 7] — LANDED.** `low_held_cursor` needed an
   sp-relative slot identity — push and pull at one entry-relative offset, a call provably
   below it — because a page-one interval hold is unsound. It is the `(epoch, offset)` key
   `sp_flow`'s join to bot cannot destroy, plus the reader/writer split: a read refuses only
   the *removal* of the store (the slot is held), a writer refuses the slot, and a call's own
   return push is priced at the call. `ret_live` did **not** fall out of it — `_below_sp`
   refuses every slot at `k > 0` precisely because that is the return address — so #177's
   per-site resume pc (`call site + inline-data length`) stands as the owed item.
6. **`frameproc`, the reach reading [1 pin of 2, 7 → 6] — LANDED, and the second pin is
   re-owned.** `g2_store`: `eqlift_mem._lattice` moved to `frameproc.lattice`,
   `frameproc.addr_reach` is the min of it and `addr_floor`/`addr_bits`, and `store_reach` takes
   it; `addr_bits` still may not, its `INT_OR` recursion composing masks under which a
   magnitude bound is unsound. `sp_scratch_floor` was **measured, and neither the chain nor
   the floor nor the resume-pc reading holds it**: `_join_mem` already keeps the cell across
   all three `pcall`s (the callee's whole footprint is page one) and `slot_reader` refuses
   nothing there. What holds it is `eqlift_mem.render_block`'s wall retiring *every* local at
   a call, so the value spelling names a version no longer available and extraction falls back
   to the cell. The reading is `frameproc._Info.may`, the callee's may-define set; the consumer
   is the wall, so the pin is `eqlift_mem`'s [1 pin, 6 → 5].
7. **rung (f), the writer set [4 pins, 5 → 1] — LANDED.** Premise 1 takes the web's own
   maintenance — a value whose every memory read is a plain read of a web cell — beside the
   declared row and the constant word, and the target set **opens** where it does, so the
   name is given and no block claim is. Premise 4 is over the web (the pair plus the save
   cells `_close` admits) and a deref store is bounded off the registry, the declared const
   `lo`/`hi` table's own word set out of `mem0`. `frameproc._fold_stmt` is what made the
   write-through store nameable: a destination is an address, and folding it as a value had
   replaced the pointer word with the columns it was loaded from. Decision log, 2026-08-12.
8. **`datadecl`, the `via:` discovery [1 pin, 1 → 0] — LANDED.** The anchor set reads the
   pair's **own lanes**: the constant a lane is reset to, or the bits an `INT_OR` row must
   set (`expr.floor`, the sound half of the interval — `lo(a|b) >= max(lo a, lo b)`). The
   run's own reads still bound the extent, so the rule is the input and observation is the
   guard. Landed beside it: the lo/hi partnership becomes a **co-index** claim
   (`datadecl._co_indexed`) instead of a zip by sorted base address. Decision log, 2026-08-12.

Items 3-8 are engine work — rungs (d) and (f), `framestack`, `frameproc`, `datadecl` — not
emission work, which is why they sit after the stage close rather than inside it. The one
recorded ordering constraint, (6) depending on (5), was **refuted by measurement** when both
landed: `sp_scratch_floor` never wanted the resume-pc reading, and nothing in the ledger now
orders anything. All of them may land in any order and in parallel.

**The still-open items, each with its owner and its mechanism.**
- **Landing 4's two extraction-order items** (owner: landing 4, whose headline metric is
  emitted size). Both were re-read against the code for this record, and both were recorded
  imprecisely before.
  **The multi-reader memory forward — and it is not "`_share_once` across roots".**
  `_share_once`'s scan is *already* over the whole procedure tree, so root scope is not the
  limit. The limit is that `by_name` is built from `asg` nodes alone: a **store has no name**,
  so the PLP status word `m_01FD` — stored once and read three times in `Cuomo_Jim/Cage_Match`
  — is re-extracted per reading site, and the artifact literally repeats a 156-character
  rebuild four times in one arm (1,092 duplicated characters in a 22,298-byte text; where the
  same rebuild *is* named, it is because the source program had an `asg` there, not because
  extraction shared it). Sharing it needs a **synthesized definition**, and three things
  currently refuse one: the render tree is immutable after `walk` (it is only ever pruned),
  `terms`/`chosen`/`id(nd)` are a closed parallel structure fixed before extraction, and the
  §6 proof channel pairs every kept node's term with its pick — `_node_terms`' own invariant
  is that a term missing there "is invisible to rooting, sharing and the §6 proofs". A
  synthesized def has no original term to be proved equal to. The two validity predicates it
  needs already exist (`_defined_at` and `_Chain.ok`); what does not exist is a place to put
  the node.
  **`pick_ir`'s price/fallback asymmetry — CLOSED, and it was `live()`'s, not the
  fallback's** (decision log, 2026-08-11). The fallback's missing `_defined_at` was measured
  over all 624 artifacts and admits **0** stale leaves in 89,920 firings: every leaf of the
  own term came from `conv` reading the site's own `env`, so the base denotes it by
  construction. The substitution this entry proposed is withdrawn as an empty rewrite, as is
  the price change #187 rejected. What the same instrumentation found is the asymmetry that
  was there: `live()` admitted a version only when an `asg` rendered it, so **181,878 of the
  200,939** candidates it refused named nothing but versions the base still denoted — a
  boundary's havoc rather than a def. Spellability is denotation, and the change is that
  predicate; `avail` was its only reader and leaves with it. Fallbacks 89,920 → 14,650,
  emitted text −2,754 lines over 527 tunes.
- **§5's `_Prune`/`_inline` deletion** (owner: landing 6). Not a rendering change but a
  rung-input change: `procedures` and `repolish` run them before rungs (d), (d2), (f) and (g),
  which pattern-match the polished statements. Its gate is `gate_sweep` plus a §4-reviewed
  emit-identity diff, and rung (d2)'s per-site e-graphs go with it only where the same
  admitted rules fire in the per-procedure graph.
- **The nine-`u8` width gap** (owner: rung (d), plan item 3) — **three of nine closed, and
  the other six are a different question.** `zp_41`, `zp_61` and `zp_81` are gone: their pairs
  are `ptr_0040`/`ptr_0060`/`ptr_0080` and item 3's per-site premise is what fused them. The
  remaining six (`zp_31`, `zp_4C`, `zp_6A`, `zp_6C`, `zp_8C`, `zp_93`) carry **no pair proof at
  all** — `framefuse.candidates` names pointer pairs, dispatch operand words and SID registers,
  and a counter pair the text already reads as one word (`ctr_0030:2`, `ctr_0092:2`, `zp_4B:2`,
  `zp_69:2`, `zp_6B:2`, `zp_8B:2`) is none of the three. So this is a **candidate-evidence**
  gap, not a premise one, and its mechanism is named: rung (d2)'s own fused word is evidence
  the pair is 16-bit, so `candidates` should read `framemath`'s lifted sites as a fourth
  source. Owner: rung (d), scheduled with plan item 4 (the widening guard), which is the next
  landing to touch this rung.
- **The parse-and-evaluate gap — CLOSED (2026-08-13), and what follows is what it was.**
  `splice_sweep` reports **0 bad of 624**: parse, lint, fixpoint, gate and sites all zero. The
  faults and the divergences went with rung (d)'s per-site premise and the denotation landing,
  the eight opaque-call lint tunes with the promotion, and the last one —
  `International_Karate` — with the signature refresh (decision log, below). The zero-new law
  now guards an **empty set**, so a tune that emits text it cannot parse and evaluate is a
  regression with a name rather than one more of a standing population. The record as it stood
  (owner: landing 6, and it is three mechanism
  families, not one). *(Mostly closed 2026-08-11 by rung (d)'s landing: the aliased
  partner-table parse defect was its carrier. Decision log, below.)* **71** of 624 tunes emit text that faults or diverges when parsed back
  and evaluated, where the analysed program does not — **25 evaluation faults, 9 lint, 37
  divergences**, the three sets disjoint. The denotation landing (decision log, 2026-08-11)
  took it from 84, thirteen divergences fixed and none new.
  (1) **lint, 9 tunes**: every one is `local 'a'/'nflag'/'zflag' used before definition`, and
  **it is not emission's** — the `--baseline` control (`out/splice_base.json`, which renders
  `frameproc.render_lines`' text and reaches no e-graph) carries the same nine, and
  `frameprog.check_locals(prog.procs)` fails on the walker's own projection, cold as well as
  cached. Two mechanisms, both `frameproc` signature truth: **eight** read a register after an
  opaque `call` whose callee declares no returns (`callable_` gates `info.rets`, and no
  measured tune has a procedure with returns) though `frameval._Code` carries locals
  program-wide across the call; **one** (`International_Karate`, `sub_AE0C(sp)`) reads `a`
  with no call on the path at all — a live-in `_Info.livein` omits. The owner is the
  promotion, plan items 5 and 6, not landing 4.
  (2) **faults, 25 tunes**: all 25 are `FrameFault`, 24 `unobserved $XXXX reached`
  and 1 a switch call target outside the observed set — the text's dispatch spelling
  losing a guard's observed set, so the owner is the `swg`/`swc` arm-table headers.
  (3) **divergences, 37 tunes**: **15 of them are one shape** — frame 0, section `filter`,
  position 1, `($16, $08)` against `($16, $10)`, the cutoff *hi* lane off by a single shift —
  which is the declared-pair spelling of items 3 and 1 (`init_lifts_to_declared_initial_values`)
  meeting at `$D415`/`$D416`, and it is the cheapest first bisection in the gap. The remaining
  22 are one tune each — the thirteen the denotation landing fixed all came out of that
  remainder, and the fifteen did not move. 3a's totality claim is about the *cache* round
  trip, which
  `emit_identity` exercises and which holds; this is a different claim and it does not.
- **The `_cell_decl` extent/`mut` defect** (owner: `datadecl`, plan item 8; #178 (3)).
  `table X[1] mut 0` on a cell the text writes as `X[x]`, because `_cell_decl` reads
  `model.written` at the base alone. A declaration-truth defect of `_declare_cells` that owes
  its own measurement.

**Landings 4 and 6 are what remain of stage 4**, and their sections above state them. The
`swc` in-edge join extension is **withdrawn**: it was measured against the artifact and is not
what `dispatch_scratch_promotes` waited on (decision log, 2026-08-11).

## Independent housekeeping (blocks nothing)

- **The Follin arity table — discharged** (2026-08-10, stage 3d, one PR).
  `follin_script._ARITY` is deleted and `deity_informant/opdispatch.py`
  recovers what it held, per dispatch arm, off the lifted blocks: the stream is
  the pointer the dispatch's own fetch uses, the operator range is
  `[floor, floor + extent)` with the floor read off the guard that dominates
  the dispatch and the extent the tightest spacing of the paired handler tables
  (`Ghouls_n_Ghosts` 21 slots `$80`–`$94`; `Agent_X_II` 17 slots `$80`–`$90`),
  the arms come from the paired table image, and an operator's arity is the
  arm's consumption footprint — the stream offsets it fetches, walked at each
  block's least `Y` so the reading does not depend on where the lifter cut. On
  Ghouls that reproduces **all 20** transcribed arities op for op;
  `tests/test_opdispatch.py` holds the transcription as the discharge
  witness, and the decoder now takes its lengths from the model it is decoding.
  Three findings. (1) The catalog's definition — net `Y` delta, constant on all
  paths — holds on 18 of the 20 and is **one short on `$87`/`$8A`**, the arms
  that rewrite the pointer instead of folding `Y` into it and so never count
  the 16-bit operand's second byte; the footprint covers both and the delta is
  recorded beside it. (2) `$85` (`rawsid`) came back as the **decoded-length
  escape** the catalog owed, not a refusal: the counted loop reads as first
  guarded offset 3, stride 2, trailer 1, continue while the byte is under
  `$80`, and the decoder consumes exactly that. (3) The table was per-tune in
  fact and not only in form — `Agent_X_II` is a second build whose `$84` takes
  no operand where Ghouls' takes one, whose `$88` takes four where Ghouls'
  takes eight, and whose `rawsid` run ends at `$FF` rather than at the command
  bit; the deleted table spoke for that build too. Its `$87` is a genuine
  refusal, named rather than decoded: `$6AE0` is `DEC $2F; JMP $69AB`, into the
  shared note path, so its net delta is 2 or 3 by the sticky-duration state.
  Reading that needed `tools/disasm_tune.py --post-init`, added here — the
  claims discipline wants the disassembly behind a refusal, and this driver is
  copied into place by init, so the load image does not hold it. Emit identity
  and the Gate FP sweep unmoved: the lane has no artifact consumer.
- **sidprog retirement — done** (2026-08-10, one PR). The emit path (`emit`,
  `metrics`, the writer), the parser, `TextModel`'s tree half and `TreeWalker`
  are deleted, with the grammar's `sidprog` dialect, `docs/sidprog-language.md`,
  the `prog-run` subcommand and `decompile --frameprog` (frameprog is now the
  default output; `--verify` is fixpoint + block-model rebuild + Gate FP).
  What stayed is the model machinery `frameprog.program` consumes —
  `_model_trees`, `_stmt_view`, the `_*_lines` header helpers — in a
  `sidprog.py` that keeps its name (`sidprog.lark` and the artifact's own
  header comment cite it, and moving either would move the emit identity);
  `TextModel` is now `BlockModel`. Ported: the closure round-trip
  (`test_soundness.py`, through `frameprog.block_model`), the pending-vector
  guard, the naming bijection and expression laws (`test_grammar.py`), the
  statement-cleanup, frontier, call-inlining and declaration-truth laws
  (`test_frameprog.py`). Retired as no longer binding: the sidprog-form
  rejections (nothing to reject from), `metrics` (reporting-only, no
  threshold), the multi-use load line (frameproc inlines further) and the 3a
  lossy-projection inequality (there is no second projection left). Emit
  identity unmoved.
- **`_declare_cells` double declaration** (3a's finding: one cell declared in
  both `state { }` and `data { }`, Agent_X_II `$6923`/`$6925`): **discharged at
  stage 4, landing 2** by `frameprog._drop_declared`, not by stage 3 as this
  entry claimed — the claim was checked, found false, and the defect was still
  in 18 cells of the 25 exemplars when it was measured.
- **The song-model modules — RETIRED (2026-08-12), less one.** `song_model.py`,
  `generators.py`, `movefwd.py`, `eqlift_annotate.py` (and `eqlift_mem`'s annotate
  hook) were the role reading on the wrong substrate; their docs and stale
  artifacts were deleted then. The condition on the rest was stage 4's role-typed
  artifact replacing their function, and it holds: the roles are ON (#187), the
  artifact carries them, and the import graph is closed — `song_model`,
  `generators` and `movefwd` are imported by each other and by their own two test
  files and by nothing else, production or tool. Those three and
  `tests/test_song_model.py`/`tests/test_movefwd.py` are deleted. `eqlift_annotate`
  is **not** retired with them: `eqlift_mem.emit_mem` still calls its `aggregate`
  and `annotate_lines` to label the header, so it leaves with that substrate —
  `emit`/`emit_mem` and the prototype example — and not before.

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
- **2026-08-10 — stage 3c, landing 1: the memory spelling is priced, and the price
  un-blinds extraction.** Every number is `tools/eqlift_measure.py` over the 25
  exemplars at full Songlengths, both paths, `DI_EQLIFT_EMIT_S=600`, beside the same
  run on `b83559f`.
  (1) **The filter was never about shadows; it dropped every spelling that mentions a
  cell.** #144's finding read `pick_ir`'s refusal as a shadow problem. `_has_mem` is
  true of any `cell` or `load`, which is most of a play routine's vocabulary, so *any*
  site reading a state cell kept nothing from `extract_multiple` and fell back to its
  own raw term — saturation reached those sites and the consumer threw the result away.
  The visible half, on the prototype alone: `if !(ctr_0037 == $10)` for
  `(ctr_0037 != $10)`; `zp_4E = (zp_4E - ($01 - (zext2(zp_4F) <= zext2(w9))))` for
  `zp_4E = (zp_4E - (w9 < zp_4F))`; `if !(carry(zp_50, zp_4F) | carry((zp_50 + zp_4F),
  $00))` for `if (zp_4F <= (zp_50 + zp_4F))`; and the SMC dispatch reading its own
  operand cell, `goto ((zext2(m_10AD) | (zext2(m_10AE) << $08)))`, for the local the
  store had just written. **−326 lines over 27,783 (−1.17%)** is what the filter cost.
  (2) **Position-correctness is a walk, and it is the proof the filter stood in for.**
  A printed `mem[a]`/cell reads memory *at the statement it prints on*. `_Chain` records
  what every memory version wrote — a store's span, a join's kept-cell set, ⊤ for a
  havoc — and walks back from the site to the version a candidate read at: a store step
  is crossed only under a Z3 QF_BV proof over every address of both spans, a join step
  only for a cell it proved kept, and a havoc, an unbounded step, an address the IR
  cannot bound and a memory term that is not a named version all refuse. §6's all-sites
  proof is the independent check and it holds: 3,309 sites changed by saturation
  (2,109 on main), every one Z3-proved, zero refusals.
  (3) **The price is the mechanism #144 asked for.** Three keys: no-memory beats memory;
  among memory spellings the **deepest** read wins — the source rather than a copy of it
  — then `_COSTS` and `repr`. The depth is the walk's own length, read off the site, so
  no consumer sees which representative extraction returned (§10). The shadow read-backs
  are what this retires: `sid.v1.attack_decay = m_0345` becomes `= zp_49`, and
  `a = m_01FB` becomes `a = m_14A7[x]`. `_share_once` inherits the same test, so a
  memory value now inlines into the use it moves to.
  (4) **A re-spelled idiom needs a spelling-independent rule.** The un-blinded extractor
  spells the deferred-carry advance two ways in one artifact — with the copy `p = lo`
  and with the cell read in place — and the prototype's `_match_advance` matched only the
  first, losing 3 of 9 advances and putting 12 lines back. Re-stated in #148's shape:
  inline the window's temporaries, take the cell read in place as the operand, hand the
  guard to Z3; a read of the cell after its own store and a temporary that outlives the
  window refuse. All 9 fold again.
  (5) **`forward_shadow` retires, subsumed.** The example's fourth fold and its
  array-theory `prove_forward` are deleted: the emitter forwards the shadow into the
  sinks itself. `FOLDS` is six rules, and the property the fold bought is asserted on the
  emitter's own text instead. The prototype's ratchets fall rather than hold —
  **461 → 455 rendered lines, 1192 → 1149 extracted term nodes**, 677 → 667 emitted.
  (6) **The review: 25 of 25, clean.** OFF 27,462 / ON 27,457, `d_lines` −5,
  `d_stores` −3, 22 of 25 byte-identical on the two paths, 13,909 extraction sites,
  12,139 proved sites, zero faults, zero refusals, zero regressions, zero extraction
  fallbacks. Against main **every tune shrinks or holds**: 23 of 25 smaller (best
  `Gray_Matt/Atmosphere_II` −51, `From_Beyond_main` −27, `Athena` −24), two unchanged,
  none larger. Slowest emit 120.5s against main's 86.0s at a budget that does not bind —
  the walk and the wider candidate pool are what that buys, and at the default
  `DI_EQLIFT_EMIT_S=60` a site past the share still renders own-term, which is sound.
  Byte identity is untouched by construction and by measurement (`emit_identity` is the
  frameprog artifact): 624 tunes, 0 refused, 28,512,406 bytes,
  `99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942`. Suite 2,782
  passed / 35 xfailed, oracle 16 passed.
- **2026-08-10 — stage 3c, landing 2: the convergence harness, and the four rules it
  named on its first run.** `tests/test_eqlift_converge.py` is stage 3's per-idiom
  convergence gate, executed: enumerated from `idioms.FORMS` so a catalog row added
  later fails until it has a case, each row carrying a generator of the spellings a 6502
  lift produces for it, and asserting they merge into **one e-class** whose extracted
  representative — read at `pick_ir`'s own price — is the row's normal form. 20 rows
  carry a generator; the three that spell one way (`const-literal`, `local-read`,
  `shift-pair`) name why instead of being absent.
  (1) **The first run named five gaps, and four are admitted rules.** `pack_hi`
  (`(h<<8|l) >> 8 = zext(h)`), `pack_lo` (`(h<<8|l) & $FF = zext(l)`) and `zext_mask`
  (`zext(x) & $00FF = zext(x)`) are QF_BV entries in `eqlift.RULES`, proved by
  `verify_rules` like every other; `sel_pair` (`sel(m,a,2) = sel(m,a+1,1)<<8 |
  sel(m,a,1)`) is a fifth memory axiom proved over the array theory by `verify_axioms`,
  and it is what makes the catalog's `pair-row` converge — two adjacent byte columns
  read at one index and the word read of the same row are now one class.
  (2) **The axiom is applied at the shape the corpus spells, and the direction is the
  measurement.** Stated as `sel(m,a,2) -> pack(...)` it fires on every word read and
  spawns its two lanes: the memory suite went 23s to 104s. Stated over the pack of two
  indexed byte reads sharing an index (`p = q + 1`, guarded), it fires only where a
  program actually spells the pack. The egglog rule is the proved axiom instantiated at
  `a = q + b`, never a second statement of it.
  (3) **`pack_add` is proved and NOT admitted, and the reason is a cost decision with
  its number.** The ADC-built pack (`(h<<8) + zext(l)`) is equal to the ORA-built one
  and `tests/test_eqlift_converge.py` proves it there rather than asserting it. Admitting
  it prices the two spellings the same, so the `repr` tie-break moves the canonical pack
  corpus-wide — `framemath`'s provenance reads the OR form (adoption §10's closed
  nondeterminism defect is exactly this shape) — and it costs **2.8x** the memory suite's
  saturation time, because a pack is the pointer vocabulary and the rule adds an `add`
  e-node to every one. It lands with the §4 cost change that names the pack as the normal
  form, on that change's own corpus diff; the gate is `xfail(strict)` at 3d until then.
  (4) **The split pitch row is not this axiom's shape, and the pin says so.** The
  prototype's `pitch` table lifts as two 22-byte columns at unrelated bases (`m_14A7` lo,
  `m_14BD` hi) that `frameprog._pair_tables` declares a lo/hi pair; no address arithmetic
  relates them, so no memory axiom can. `test_note_fetch_is_one_u16_row_read` is re-pinned
  at **3d** with the mechanism named: enumerate the declared pair at the site
  (`framemath._pairs`' shape) and query the class for the row read, so extraction still
  decides how the row is spelled and not which grouping the site is.
  (5) **The rules cost the artifact nothing, which is the point of admitting on the
  convergence gate.** The 25-exemplar review is **byte-identical to landing 1** —
  OFF 27,462 / ON 27,457, `d_lines` −5, `d_stores` −3, 22 identical, 13,909 extraction
  sites, 3,309 changed and every one Z3-proved, zero faults, refusals, regressions and
  fallbacks — and the frameprog artifact is unmoved even though rung (d2) reads the
  admitted rule set: 624 tunes, 0 refused, 28,512,406 bytes,
  `99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942`. The canonical
  example is unchanged at 455 rendered lines / 1149 term nodes. Suite 2,804 passed /
  36 xfailed (the 36th is (3)'s pin), oracle 16 passed; `gate_sweep` at full Songlengths
  624 build, 622 evaluate, 621 clean with the same three named tunes.
  (6) **Scratch demotion is measured, and what blocks it is named — 3c closes here.**
  Landing 1 discharged the first half of `test_stack_spill_forwards` (the pull is spelled
  from the pushed value), so only the store survives, a root until demotion. A store is
  demotable when no reader can observe it, and the reader set is artifact-wide: the
  prototype's helper `sub_1485` is clean (one stack read, one stack store, no unbounded
  read), but `sub_1000` reads its scripts through a zp pointer pair whose address is
  **⊤ with no env**, so the artifact-wide reader set is the whole space and the slot is
  not provably unread. The stack-discipline shortcut is not available either, and the
  reason is on the record: the stack slots eqlift still sees are the ones rung (d0s)'s
  `framestack._balances` refused to lift, so a balance argument would contradict the
  analysis that refused them. The mechanism 3d carries is therefore a `Footprints` **read**
  closure — the dual of `_mem_writes` — whose deref spans come from the committed observed
  extents (2b's `ptrextent`), consumed exactly as the join consumes
  `addr_floor`/`addr_bits`. `test_stack_spill_forwards` is re-pinned at 3d with that
  reason, and the executable half stays in `tests/test_eqlift_mem.py`.
- **2026-08-10 — stage 3d, landing 1: the read closure, and the store that answers to
  nobody.** Every number is `tools/eqlift_measure.py` over the 25 exemplars at full
  Songlengths, both paths, `DI_EQLIFT_EMIT_S=600`, `--extents out/ptr_extents_full.json`,
  beside the same run on `fb74318`.
  (1) **The reader set is the dual of `_mem_writes`, and 2b's extents are what bound it.**
  `_mem_reads` walks every memory read of a statement list to a span — the address's own
  lattice and bit bounds first, over the address *resolved through its reaching
  definitions* (`addr_bits` already reads one level of that; an `add` needs the leaves,
  since the lattice states nothing about a bare local), and where those state nothing and
  the address is a pointer deref, the interval of the declared blocks 2b observed the web's
  derefs inside. `Footprints` carries it per procedure beside the write footprint, so it is
  one traversal. The measurement is what the extents are worth: over the 25 exemplars
  **35 of 78 procedures have a ⊤ reader set without them and 16 with**, and the two
  exemplars whose reader set is ⊤ for another reason are the two that are `open_flow`
  (`Angry_Birds`, `Rambo_First_Blood_Part_II`). A label costs nothing here and an
  enumerated dynamic transfer costs nothing either — entering anywhere reads no more than
  the union — which is why the read closure needs no fixpoint where the write footprint
  does.
  (2) **Scratch demotion is that set asked once per store.** `roots()` no longer makes
  every surviving store a sink: a store whose span is bounded, is Z3-proved disjoint from
  every reader interval and cannot reach the device window is not observable, so it is not
  a root and `_root_keep` retires it with whatever fed it. The reader set is every *other*
  procedure's statements plus this one's **extracted** spellings, because extraction is
  what retires a read — the prototype's `PHA`/`PLA` pull is spelled from the pushed value,
  so the statement that reads `m_01FB` names it nowhere in the emitted text. The intervals
  are merged before they are proved (`_cover`), so a store answers a handful of Z3
  questions and not one per cell.
  (3) **The gate flips, and the exemplars move by one line.**
  `test_stack_spill_forwards` is green: `m_01FB` leaves the prototype's text entirely, and
  both ratchets **fall rather than hold — 455 → 453 rendered lines, 1149 → 1146 extracted
  term nodes** (677 → 666 emitted). Over the exemplars exactly one store demotes
  (`Gray_Matt/Atmosphere_II`), which is the honest number for real drivers: a play routine
  is usually one procedure and every cell it stores it also reads.
  (4) **The in-edge join fires, and the labels that still reset are back edges.** A label
  the walk has already passed every in-edge of — all of them `goto`s of this procedure,
  outside any cyclic body, none of them a call, an `swc` label, an RTS-trick landing or a
  procedure entry — is joined instead of havoced: a cell every in-edge memory and the
  fall-through read at one common chain version keeps that value, and `_Chain` records the
  version per cell so a later read lands there and stops. Over the exemplars it fires **10
  times over 5 tunes** and **398 labels still reset**. The residue is one shape and it is
  resolved rather than deferred: a back edge's in-edge memory is *behind* the walk, and the
  loop-head bound that would replace it (what the code between the label and the `goto` may
  write) is not delimitable below the whole procedure, because a label inside that region is
  entered from outside it — and the whole procedure's write footprint contains every cell
  the chain holds, since the chain holds only cells this procedure stored. The complemented
  join therefore keeps ∅ **by construction**, which `tests/test_eqlift_mem.py` asserts on a
  minimal case rather than leaving to argument.
  (5) **The review: 25 of 25, clean.** OFF 27,462 / ON **27,456**, `d_lines` −6,
  `d_stores` −3, 22 of 25 byte-identical on the two paths, 13,909 extraction sites, 3,309
  changed by saturation and every one Z3-proved (12,138 proved sites), zero faults, zero
  refusals, zero regressions, zero extraction fallbacks. Against 3c landing 2's 27,457
  **no tune is larger and one is smaller** (`Gray_Matt/Atmosphere_II` −1). Slowest emit
  91.1s at a budget that does not bind. Emit identity is untouched by construction (eqlift
  is not in the frameprog path) and by measurement: 624 tunes, 0 refused, 28,512,265 bytes,
  aggregate `946f0dcb082fc4df0814505b5eb42a8dd677f70bcfe94deeb245c2132f1c6ec0`.
  (6) **What the landing does not do.** The extents reach the lifter as data
  (`emit(model, extents=...)`), read from the committed 2b artifact by
  `tools/eqlift_measure.py --extents` and observed in-line by the prototype's own probe
  run; nothing derives one, and `ptrextent.mapped_blocks` is the single reading rung (g)
  and the read closure share. Store spans are still read without the resolution the read
  side got: tightening them would move the join and the artifact, and this landing's gate
  is the reader set.
- **2026-08-10 — the shredder's stage-3 pins are disposed against the unified emitter,
  and "they flip at the cutover" is true of two of twenty-four.** Adoption §8 step 4 moves
  frameprog *emission* onto the unified graph. Each stage-3 pin's goal property is now
  evaluated against `eqlift_mem.emit` on the same fixture model (`_emit` in
  `tests/test_shred_regmodel.py`) instead of assumed, on top of landing 1, and every
  stage-3 reason carries one of three measured verdicts (`_MEASURED`), enforced by
  `test_every_stage_three_pin_carries_its_measured_disposition` so a pin added later
  cannot skip the measurement.
  (1) **Two pre-verified flips.** `borrow_chain`: the unified graph extracts
  `(ctr0 + $37) < m_1464` where frameprog spells `$01 - (zext2(..) <= zext2(..))`, so both
  of the pin's assertions hold there today. `lone_lane`: the write-only price emits
  `sid.v1.freq_hi = t0` outright where frameprog widens the lone half into a
  read-modify-write of the u16 register. Each gains a parallel green test on the eqlift
  text; the frameprog xfail stays, and step 4 XPASSes it.
  (2) **Nineteen are not emission properties at all, which is the finding.** They read a
  verdict `frameprog.program` computes on the statements *before* any emitter runs, so the
  cutover cannot move them. The twelve `_fused_cursor` pins read rung (d)/`framefuse`'s
  tune-wide pair declaration, and `emit` reuses `frameprog._state_lines`' unfused block —
  even the green `plain_advance` control reads byte-wise there, which is pinned. The four
  `mem[` pins read rung (f)/(g)'s `*ptr[i]`, which `emit` never mints; each fixture's own
  premise is on the record ("another store may write the pointer" for `pointer_walk` and
  `mux_pair`, "a definition is not a lo/hi partner-table entry read" for `cursor_save`,
  "a store at an unproven address may write the pointer" for `writethrough`).
  `low_held_cursor` and `computed_rows` read `ptrcert`/`ptrextent`; `g2_store` reads
  `frameproc.addr_bits`.
  (3) **`g2_store`'s bound already exists — in the other analysis.** `mem_rules`' lattice
  states `($00A5, $01A4)` for the address `addr_bits` calls ⊤. `addr_interval` seeds the
  graph *from* the bit analysis and never back, so that pin flips when the bit analysis
  reads the lattice and not when emission moves. `computed_rows`' own alternative is
  measured beside it: the memory sort states nothing for `((ctr & $01) << $03) | $80`
  because `INT_OR` is in no interval rule, though every shift below it is bounded — so the
  guarded refusal is the branch its reason takes, and Phase 2.5's walker stays its
  mechanism.
  (4) **The scratch trio splits three ways, and landing 1 moved one of them.** `scratch`:
  the read closure retires the store, so `m_1460` is gone from the emitted body and all
  that stands is the `state { }` declaration `frameprog._state_lines` derives from
  `_cells(view)` rather than from the stores extraction kept — a `framestack.drop_state`
  analogue keyed on demotion, named here for the first time. `dispatch_scratch`: the
  handlers are `swc` labels, which landing 1's in-edge join excludes by design, so both
  arms read the cell where the straight-line fixture now reads the value; extending the
  join to the dispatch's own arms is the whole of what that pin waits on.
  `sp_scratch_floor`: the cell survives beside a raw `call` where frameprog threads a
  procedure with a register interface — the substrate, not `addr_floor`, is what holds it.
  (5) **Three cutover-order facts the splice plan owes, all pinned green.** Rung (d2) mints
  a *narrowing* `COPY` — the lo-lane save copy of a fused u16 local at width one — that
  `eqlift_mem._OP` maps to nothing, so splicing frameprog's own statements into
  `render_proc` raises `KeyError('COPY')` before any rule fires. `emit` builds raw
  `_Builder` procedures, so it has neither the `pcall` promotion (4)'s third case needs nor
  any structural rung. And the unified emitter spells the SMC dispatch header `switch {`
  where frameprog spells `switch goto {`, with the arms and the operand `goto` intact on
  both paths; the green R8 control matches the frameprog spelling, so step 4 keeps the
  keyword or moves that assertion with it.
  Tests only, so no artifact movement. Suite 2,686 passed / 490 skipped / 42 xfailed,
  oracle 16 passed.
- **2026-08-10 — stage 3d, landing 2: `pack_add` is admitted, and the cost names the
  normal form.** The §4 process run end to end on the one rule 3c landing 2 proved and
  refused to admit.
  (1) **The cost change is the decision, and the rule rides on it.** With both pack
  spellings priced 1 the `repr` tie-break decides which one the artifact carries, and it
  decides for `add`. `eqlift._packed` prices an `add` that spells the pack (`zext(hi) << 8`
  plus `zext(lo)`) one above the `bor` that spells it, which is the catalog's own reading —
  `idioms.pack` **is** the OR form, so `word-pack`'s normal form was already named and the
  price now says so. `pack_add` is admitted in `RULES` beside it and proved by
  `verify_rules` like every other entry.
  (2) **The corpus-artifact diff, read tune by tune.** `tools/emit_identity.py`: 624 tunes,
  0 refused, **49 of 624 moved, every one by exactly +8 bytes**, 28,512,265 →
  **28,512,657** bytes, aggregate `946f0dcb…` → **`434f0bab009a2543da69f7997a5c279af4f9e390fc894f601bce262e515c7c72`**.
  Five of the 49 were diffed by hand and the diff is **one line each, one shape**: the
  ×257 index scale `((zext2(i) << $08):2 + zext2(i)):2` becomes
  `((zext2(idx_00FC) << $08):2 | zext2(idx_00FC)):2` — the ADC-built pack respelled as the
  catalog's `word-pack`, with the operand spelled from its own cell instead of a local copy
  (a cell costs 1 and a local 4, so the OR spelling's leaves win once the pack itself is
  priced). The bytes grow because the cell's name is longer than the temp's; no line is
  added or removed. Rung (d2) is what carries it: `framemath` reads the admitted rule set.
  (3) **The 2.8× does not reproduce, and the reason is the cost change.** 3c landing 2
  measured the memory suite at 2.8× with the rule admitted and the tie-break loose.
  Measured now over `tests/test_eqlift_mem.py` + `tests/test_eqlift_converge.py`, three
  runs each: **24.9s admitted, 25.0s not**. The rule adds one `add` e-node per pack; what
  cost 2.8× was the churn of a tie that no longer exists.
  (4) **The decision: admit.** The rule is a proved equivalence, the merge is what the
  convergence gate exists to assert, the price removes a §10 nondeterminism hazard (a tie
  decided by spelling), the artifact moves by +0.0014% of its bytes and by zero lines, and
  every moved line moves *toward* the catalog's normal form.
  `test_the_adc_built_pack_converges_on_the_ora_built_one` **flips**;
  `test_the_cost_names_the_or_built_pack_the_normal_form` replaces the deferral's proof
  test with the price it was waiting for. The new emit-identity baseline is recorded above
  and `--expect` takes it from here.
  (5) **Nothing else moves.** `gate_sweep` at full Songlengths 624 build, 622 evaluate,
  **622 clean**, the two CyberTracker faults standing. The 25-exemplar eqlift review is
  **byte-identical to landing 1** — OFF 27,462 / ON 27,456, `d_lines` −6, `d_stores` −3,
  22 identical, 13,909 extraction sites, 3,309 changed and every one Z3-proved (12,138
  proved sites), zero faults, refusals, regressions and fallbacks — because the pack
  respelling lands in rung (d2)'s artifact and eqlift's own extraction never preferred the
  ADC form at any exemplar site.
- **2026-08-10 — stage 3d, landing 3 (part): the architectural-register self-copies
  retire, and 3d's residue is named.** 3b landing 2 left 11 live self-copies of
  architectural registers over the 25 exemplars — `a = a`, `x = x`, `y = y`, `sp = sp`,
  `zflag = zflag` — as "sound no-ops `_share_once` skips by rule and `_dce` keeps because
  the register is live". Resolved by a named mechanism rather than by their death at §8
  step 4: **a register assignment whose extracted spelling is the version the register
  already holds is a no-op**, because a local renders as its base name and a root reaches
  the register, not the statement. `render_proc` records the version in force at each
  `asg` (the entry version `<base>.0` where the walk has made none, which is what `conv`
  reads too) and `_self_copies` names the ones extraction spelled back. Over the exemplars
  **11 → 1**; the survivor is `Angry_Birds`' `sp = sp`, where the spelling is a different
  live version of `sp` and the statement is a real move rather than a copy. ON 27,456 →
  **27,446** (−10), five tunes smaller and none larger, 13,909 extraction sites, 3,299
  changed and every one Z3-proved (12,128 proved sites), zero faults, refusals,
  regressions and fallbacks.
  **What 3d has not closed, each with its owner.** (a) Guard-aware re-rolling and the
  declared lo/hi pair row read stay pinned at 3d
  (`test_rerolling_unifies_the_isomorphic_voices`,
  `test_note_fetch_is_one_u16_row_read`): the row read has no site until the callee's two
  returns are resolved into the caller, which is what anti-unification supplies, so the
  two are one landing and not two. (b) The shredder's `dispatch_scratch_promotes` waits on
  extending landing 1's in-edge memory join to `swc` (dispatch) labels, which landing 1
  excluded by design — an `swc` label arrives with a memory the walk does not name, so the
  extension needs the dispatch's own in-edge set and is the same mechanism one step
  further out; it is re-pinned on that extension. (c) The `low_held_cursor` family's
  premise is exactly the deref span landing 1's read closure computes, but its consumer is
  `ptrcert`, a rung — that is a rung landing and not stage 3's, and it is handed on rather
  than left floating.
- **2026-08-10 — stage 3d, landing 3: the voices re-roll under a proved guard, and the
  declared pair gets its site.** 3d's committed scope closes on the prototype, which is
  where the plan puts the two passes that are not equational; the numbers are
  `examples/state_machine_lift.py`'s own artifact at full frames.
  (1) **A voice's slice is a context whose hole is the next voice's.** The structurer
  weaves the three voices into one another — voice 2's region is a suffix of the list
  inside voice 1's dispatch loop — so "three sibling regions" was never the shape. What
  is true is stronger: *every* path through voice `v`'s region ends where voice `v+1`'s
  begins (the break handler and the kill/test arm both `goto` the tail, and the tail
  falls into the next voice), so the region is a context `C[v]` with one hole, and a
  total anti-unification of `C[1]` against `C[2]` **is** a loop over them. `reroll` cuts
  the contexts (`_find_first`/`_cut`, the voice read off `stmt_names`), anti-unifies
  node for node (`_Unify`), and emits `for voice in v1, v2 { … next voice }` with the
  hole as the loop's own back edge. Any residual mismatch refuses the whole slice.
  (2) **The observed-guard difference is proved, not matched.** #144 finding 4 measured
  the binding requirement: voice 1's cursor advance folds `wide` and voices 2-3 fold
  `nocarry`, purely from where each script landed relative to a page boundary. `_Unify`
  meets exactly one such difference and hands it to Z3 (`prove_guard_unify`): where the
  `nocarry` guard holds the guarded spelling equals the unguarded wide add, so both
  statements denote `pos:u16 += 2` and the unified statement is the unconditionally
  proved one. It is recorded as `reroll_guard(ptr_0040,+2)` beside the folds. Nothing
  structural is guarded into the loop: voice 3 refuses on its own filter block —
  `block of 13 against 21` — and keeps its copy, which is the §4(e) near-miss refusal
  running rather than being asserted, and `test_rerolling_unifies_the_isomorphic_voices`
  asserts no synthesized per-voice guard appears in the text.
  (3) **The declared pair row read needed the callee resolved, not the voices unified.**
  A correction to what landing 2 handed forward: the site is created by resolving
  `sub_1485`'s returns into its callers, which is a leaf-callee resolution
  (`resolve_calls`: straight-line register arithmetic, so the callee's post-state is the
  composition of its own assignments) and is independent of re-rolling — the two land
  together because they are one landing's scope, not because one supplies the other.
  With the lanes in one place `row_reads` matches `r1 = T_lo[e]; r2 = T_hi[e]; c_lo = r1;
  c_hi = r2`, enumerates the destination pair at the site and proves the grouping over
  the **array theory** (`prove_row`: storing the two columns' bytes at the pair's cells
  leaves it holding `Concat(hi, lo)`; a destination pair that aliases refuses), then
  emits `voice_note = pitch[x]` — the table spelled by the image's own `pitchlo`/`pitchhi`
  labels (`pair_tables`). Three instances, `row_read(m_14A7,m_14BD)` each. The pair is
  now the artifact's declaration and not the renderer's: `wide_cells` carries it, the
  hand-written `v%d_note: parameter u16` lines are deleted, and the two lanes leave the
  state block. `test_note_fetch_is_one_u16_row_read` flips.
  (4) **The loop is executed, not reviewed** (adoption §6). `expand` is the loop's own
  meaning — the body once per voice binding, the hole plugged with the following slice —
  and the pipeline runs *that* expansion through `Machine`, so Gate FP, the write grid,
  the hard-restart orderings and the pysidtracker oracle all gate the re-rolled program.
  `test_the_loop_expands_to_the_program_it_rolled` proves the round trip exactly:
  expanding the loop reproduces the folded program leaf for leaf with **one** difference,
  the `adv16` guard flag, and it carries its Z3 proof.
  (5) **The record, and the numbers.** 47 leaf bindings; 36 are systematic (a cell or
  sink the voice owns, spelled `voice_pos`/`sid.voice.freq`) or a per-voice temporary
  (alpha-renamed `voice_t<k>`), and the **11** that are neither — the two SMC operand
  cells, the two paired handler tables, six handler labels and the tail label — are
  declared in a `voices v1, v2 { … }` record, which is §4(e)'s per-voice parameter
  record arriving as output. `test_the_voice_record_declares_every_binding_no_name_covers`
  enumerates it from the artifact so a binding cannot go unspelled. Both ratchets fall
  hard: **453 → 339 rendered lines** and **1146 → 836 extracted term nodes**, re-pinned
  there. The unification rate is `2 of 3 voices over 11 bindings`, printed by the
  example's own `main`. Suite 2,707 passed / 490 skipped / **39** xfailed, oracle 16; the
  frameprog artifact is untouched by construction — the landing edits `examples/` and its
  tests and nothing in the package — which `tools/emit_identity.py` confirms: 624 tunes,
  0 refused, 28,512,657 bytes, aggregate `434f0bab…` unmoved.
  (6) **What it does not do.** The pass lives in the prototype, which is where the plan
  puts its classical passes and where the pins are; step 4's cutover carries the same
  three parts — context cut, anti-unification, guard proof — onto `eqlift_mem`'s render
  tree, and it is named in stage 4's runway rather than left floating. `resolve_calls`
  resolves a *leaf* callee only; a callee with control flow keeps its call.
- **2026-08-10 — stage 3d, landing 4: associativity is one directed instance, and the
  emit budget is split by work.** The two measured performance findings the slow-tune
  diagnosis handed forward, landed with the §4/§6 gates the diagnosis owed (it ran
  `proofs=None`). Every number is `tools/eqlift_measure.py` over the 25 exemplars plus
  `A_Chipful_of_Love_for_You`, `--extents out/ptr_extents_full.json`; the per-tune
  seconds are single-job so the pool does not colour them.
  (1) **The cost is at width 1, and the diagnosis's "widths 1-2" reading is corrected.**
  `add_assoc` restricted to width **2** alone changes nothing measurable and restricted
  to width **1** alone changes nothing measurable *either way*: `Angry_Birds` 50.6s with
  both widths, 50.7s at width 1, **2.2s** with the rule gone. The blowup is the lane
  arithmetic, not the packs.
  (2) **A plain drop is not free, which the diagnosis could not see.** It loses a lift
  `tests/test_framemath.py` pins (`STA $08,X`'s zero-page store case stops fusing) and it
  un-triggers #164's step-4 splice pin — the example stops minting the width-one
  narrowing `COPY` at all — and corpus-wide it moves **15 of 624** tunes in *both*
  directions, net +710 bytes: `1942` loses `ptr_00FA:u16` to two byte lanes and
  `Crazy_Dance` loses a row cursor's u16 advance, while `Agent_X_II` (−142 bytes) and
  `Acid_at_Night` gain one. "Equal-or-smaller 14 of 15" does not survive a tune-by-tune
  read.
  (3) **What associativity was buying is one instance, and it is directed.** Every lost
  site has the same shape: a lane sum of three terms whose numeral addend sits at the
  outer position (`(y + w3) + $01`), where the fusion rules match a two-term add whose
  partner is the carry. `add_num_in` is exactly that — `(x + y) + c -> (x + c) + y` for a
  numeral `c`, widths 1 and 2, Z3-proved by `verify_rules` like every other entry, and
  **directed**, so no grouping of a chain is ever enumerated. With it every one of those
  tunes' frameprog artifacts is byte-identical to the general-associativity baseline, and
  so is the corpus: `tools/emit_identity.py --expect 434f0bab…` **passes**, 624 tunes,
  0 refused, 28,512,657 bytes. **The standing baseline does not move.** `_r_add_assoc` is
  deleted; an intermediate that admitted two extra `carry_fuse` groupings instead was
  measured and dropped as subsumed.
  (4) **The price, and the §6 review.** Single-job at `DI_EQLIFT_EMIT_S=600`:
  `Angry_Birds` 50.6 → **18.4s**, `Frantic_3_tune_5` 29.5 → **16.0s**, `Dynasty_8_tune_2`
  27.9 → **13.6s**, `21_G4_demo_tune_2` 26.8 → **13.0s**; the exemplar review's 50 lifts
  1,455.9 → **901.2s**; the fast suite 210s → **137s**. The review is **25 of 25, clean**:
  OFF 27,461 / ON 27,445, `d_lines` −16, `d_stores` −3, 13,909 extraction sites, 3,309
  changed by saturation and every one Z3-proved (12,127 proved sites), zero faults,
  refusals, regressions and fallbacks, 18 byte-identical between the paths. Against
  landing 3's texts **no tune is larger**, `Deek/4_Tunes` is one line smaller on both
  paths, and 18 of 25 are respelled — mostly the operand order of a commutative sum the
  tie-break now returns differently, and in six tunes a real simplification the smaller
  graph reaches: `Athena`'s `(carry(ptr5,idx1) | carry((ptr5+idx1),$00))` becomes
  `((ptr5+idx1) < idx1)`, `BubbleBobble`'s `((a & $80) != $00)` becomes `(a <s $00)`,
  `Dynasty_8`'s `($01 - (zext2(a3) <= zext2(a2)))` becomes `(a2 < a3)`, and
  `Atmosphere_II`'s expanded `filter.resonance` term collapses to
  `((t0 << $01) + zp_12 + (t0 <s $00))`.
  (5) **The emit budget is divided by work, and the budget stops moving the text.**
  `emit_mem` divided the remaining seconds by procedure COUNT, so the largest procedure —
  index 0 in almost every multi-procedure tune — got the same share as a one-site trailer
  that then returned it unspent. The share is now `remaining × weight[i]/sum(weights[i:])`
  with `weight` the procedure's recursive statement-node count (`_work`). Measured over
  the 26 tunes at the **default** `DI_EQLIFT_EMIT_S=60`, three ways: **1,515 fallbacks
  over 5 tunes** (main) → **1,129 over 4** (the rule alone, which moves where the time
  goes but not how it is shared) → **0 over 0** (both). And the point of the fix, stated
  as an identity rather than a count: with both changes every one of the 26 artifacts at
  60s is **byte-identical to its own 600s artifact**, so on this set the default budget no
  longer makes the text a function of the clock.
  (6) **What it does not do.** `add_num_in` is the instance the *lane fusions* need; a
  chain whose regrouping no admitted rule names still extracts as it is spelled, and the
  standing answer to that is flat n-ary associative nodes (what `docs/symbolic-recorder.md`
  already does for `INT_ADD`), which is an encoding change and not a rule. The optional
  second lever the diagnosis named — `sub_to_add`/`add_to_sub` at 55s on a byte-identical
  artifact — is not taken here: with `add_num_in` landed the same tune is 13.0s, so the
  lever's measurement no longer describes this graph and re-taking it would be a decision
  on stale numbers.
- **2026-08-10 — the step-4 cutover splice is pinned, and stage 4's witness carries the
  whole dispatch family.** Two landings from the parallel checkout (#164, #165), folded
  into this record at 3d's close because the plan document is single-writer.
  (1) **The splice is an executable gate now, not an argument** (#164).
  `tests/test_step4_splice.py` carries adoption §8 step 4's move — the rung-built
  statements `frameprog.program` produces, carried by `eqlift_mem.render_proc` in place
  of `frameproc.render_lines` — as one strict xfail on the canonical example, read
  through the same public pipeline the witness test uses: the spliced emission succeeds,
  its text is a `dumps`/`loads` fixpoint, and the program it parses back to reproduces
  the walker's per-frame projection under Gate FP. Three green controls stand beside it,
  so the xfail isolates the splice rather than the harness. The measured first blocker is
  #161's, now on the example rather than the shredder fixture: `KeyError('COPY')` out of
  `render_proc`'s converter before any rule fires, because rung (d2) mints a width-one
  narrowing `COPY` of a fused u16 local in the phase accumulator's carry chain and
  `eqlift_mem._OP` maps no `COPY` at all. Tests only, no artifact movement.
  (2) **The witness compiles every dispatch form, and the example replays on the
  machine** (#165). `dgoto`, `swg`, `opsw`, `swc`, `dcall`, `dbr` and `igoto` through a
  computed pointer, on one shared mechanism: a computed target is a **pc of the
  serialized program**, not an address in the emitted image, so the machine resolves the
  pc to the label the program compiled it under — `frameval`'s rmap, on the machine. An
  observed arm is tested arm-first with the resolver as the fallback, and a variant
  outside the observed set lands on the fault, so observed-primary holds on the machine
  exactly as it holds in the evaluator; `igoto` reads its vector with the 6502's own page
  wrap. `test_the_example_artifact_replays_frame_for_frame` **flips green**: the canonical
  example's frame program, re-emitted as 6502 and replayed under the VM, matches the
  original routine's per-frame projection frame for frame with **no evaluator in the
  trust chain**. The refusals left each name their mechanism — the raw machine call
  (`call`/`callb`, the `framestack` RTS-trick family), the static image vector whose
  target body follows the transfer inline under no label, and an arm table with no
  computed transfer before it — and the module's own refusal table is still held to the
  checklist by test.
- **2026-08-10 — stage 4, landing 1: the continuation comes from the return slot, and
  the corpus gate is clean.** #155's runway, landed as the two halves its own
  measurement said were each necessary and neither sufficient.
  (1) **A callee that consumes its return slot is not a register-interface procedure.**
  `frameproc.slot_reader` walks a procedure's straight-line prefix with `sp` read
  entry-relative — which is where a callee must take its own return address, before a
  transfer can lose it — and refuses the `pcall` promotion where the stack pointer rises
  above its entry value or an access names page one at displacement `+1` or above.
  Those bytes are what the caller pushed, and the `pcall` surface drops `ret $R`: the
  evaluator pushes a stand-in, so the callee steps a stand-in, reads its inline data
  from nowhere and returns to a pc no map holds. The refusal is one entry in `_Info`'s
  own `blocked` set, so the raw `call $4921 ret $4ED6` carries the address again.
  (2) **`ret` reads the slot, not the call's successor.** The evaluator's shadow stack
  recorded where a call came from and returned there unconditionally, so a callee that
  rewrote the return word was ignored. It now records the word it pushed beside the
  frame, and a `ret` takes the shadow continuation **only where the slot still holds
  that word**; otherwise the slot's own word resolves through the same `rmap` the
  RTS-trick `dgoto` reads. Where `sp` concretizes, `framestack.lift_rts_trick` still
  turns the constant push pair into that `dgoto` and no `ret` runs; where it does not —
  the corpus spelling, one callee at two depths — the `ret` is the whole mechanism.
  (3) **Three pins flip on their own mechanism, and the fixture family is the claim.**
  `test_a_shared_inline_data_callee_evaluates_through_the_skip` (two sites of one
  callee), `test_a_two_depth_inline_data_callee_evaluates_through_the_skip` (the corpus
  spelling exactly) and `test_a_per_site_inline_data_length_evaluates_through_the_skip`
  (the skip length in the first inline byte, so no per-callee answer exists) are green,
  as is `test_fixture_builds_and_gates` for all four; the one-site control was already.
  Each flipped pin also asserts the mechanism in the text — no promoted call, the raw
  call with its return slot, and the slot rewrite or the `dgoto` read off it — so a gate
  that passes for another reason fails.
  (4) **The corpus, and the §4 diff read tune by tune.** `gate_sweep` at full
  Songlengths: **624 build, 624 evaluate, 624 clean, zero divergences, zero refusals** —
  the first clean corpus of this plan, and the standing `C64_World`/`1st_Decent_Hardcore`
  exclusions are gone. `tools/emit_identity.py`: 624 tunes, 0 refused, 28,512,657 →
  **28,513,156 bytes**, aggregate `434f0bab…` →
  **`37b871408ea4344dd60e562f44825730748528a49fc247d47828eeb7aae2ce23`**, **2 of 624
  moved** and 622 byte-identical. Both moved tunes were diffed in full and carry one
  shape: the two `sub_4921(x, y, sp)` call statements become
  `call $4921 ret $4ED6` / `ret $4D7C`, and `sub_4921` gains the boundary a raw call
  makes conservative — `x = m_4963`, `y = m_4965` and the machine flags the callee
  defines (`cflag`/`zflag`, plus `vflag`/`nflag` on `1st_Decent_Hardcore`), +7 and +10
  lines, +196 and +303 bytes. That price is the honest one: with the register interface
  gone, `ret_live` is `_Info.G` and the callee's own restores must survive, which is
  exactly what makes the raw call correct. It is also stage 4's metric moving the wrong
  way on two tunes, and the mechanism that would take it back is a precise `returns` set
  for a procedure every entry of which is a `call` — named here, owned by landing 2's
  cutover, which is where the boundary summary is re-sourced anyway.
  (5) **Nothing else moves.** The 25-exemplar eqlift review is **byte-identical to 3d
  landing 4** — OFF 27,461 / ON 27,445, `d_lines` −16, `d_stores` −3, 13,909 extraction
  sites, 3,309 changed and every one Z3-proved (12,127 proved sites), 18 identical
  between the paths, zero faults, refusals, regressions and extraction fallbacks — which
  is what the mechanism predicts: no exemplar's frameprog text moved, and `slot_reader`
  refuses nothing on the render trees `eqlift_mem`'s own `_Info` sees. Suite 2,713
  passed / 490 skipped / **33** xfailed (39 before, six flipped), oracle 16.
  (6) **One reading of the stack, not three.** `framestack._mems`/`_accesses`,
  `_sp_disp` and `_sp_delta` moved to `frameproc` as `accesses`/`sp_disp`/`sp_delta` and
  `framestack` binds the same objects, so rung (d0s) and the promotion refusal read a
  stack address one way.
- **2026-08-10 — stage 4, landing 2 (part): the narrowing `COPY` is a term, and the next
  splice blocker is named.** #164's measured first cutover blocker, cleared without moving
  the artifact.
  (1) **`trunc` is `zext`'s dual, and one constructor is the whole fix.** Rung (d2) mints a
  width-one `COPY` of a fused u16 local — the lo-lane save copy — and `eqlift_mem`'s
  converters mapped no `COPY`, so splicing frameprog's own statements into `render_proc`
  raised `KeyError('COPY')` before any rule fired. `eqlift.trunc` lands across the egg
  algebra, the Z3 algebra, `_EGG_FNS`, `pass1_node`, `_ir_width` and the printer, and the
  three converters route `INT_ZEXT` and `COPY` through one `_rewidth`.
  (2) **No rule is admitted over it, and that is a measurement rather than an omission.**
  `trunc_zext`, `trunc_num` and `trunc_pack` were written and Z3-proved (93 instances) and
  then dropped: `eqlift.RULES` is what rung (d2) reads, so admitting a rule is a corpus-diff
  decision (§4) and these have no artifact to show for themselves. `eqlift.to_egg` likewise
  still maps no `COPY` — mapping it there would grow rung (d2)'s per-site graphs — so the
  frameprog artifact cannot move by construction. The convergence gate is where a `trunc`
  rule lands, with its own price.
  (3) **The next blocker is executable, and it is a vocabulary one.** The unified emitter
  spells `<s`: `eqlift.slt`, which 3d landing 4 measured the smaller graph reaching for
  `((a & $80) != $00)`. `sidprog.lark`'s `op` production has no signed comparison, so `loads`
  refuses the spliced text — stage 2's capability precondition unmet for the unified
  emitter's own operator set. Either the dialect gains the signed compare (grammar,
  evaluator, an `idioms.FORMS` case) or the price never prefers it;
  `test_the_splice_now_blocks_on_the_signed_compare_the_dialect_cannot_spell` holds it.
  (4) **The pins and the gates.** The shredder's `dual_store_lo_only` `COPY` pin and
  `test_step4_splice`'s blocker test become landed controls; the cutover gate stays
  `xfail(strict)`. `gate_sweep` at full Songlengths 624 build / 624 evaluate / 624 clean,
  zero divergences and zero refusals; `tools/emit_identity.py --expect 37b87140…` passes —
  624 tunes, 0 refused, 28,513,156 bytes, **unmoved**.
- **2026-08-10 — stage 4, landing 2 (part): the signed compare is a dialect operator, the
  unified renderer learns the dialect it prints, and the splice's frame oracle goes clean.**
  #170's named blocker taken as capability, and then #161's substrate facts measured on the
  canonical example rather than argued.
  (1) **The dialect gains the operator, because no price prefers the unsigned spelling.**
  `sign_ne`/`sign_eq` are admitted, Z3-proved rules (3d landing 4), so the smaller graph
  reaches `(a <s $00)` for `((a & $80) != $00)` — the example spells it at three sites and
  its complement at three more; pricing it out would retract a proved rule to spare the
  grammar, which is the wrong way round. `sidprog.lark`'s `op` production gains `<s` and
  `<=s`, `grammar._CMPOPS` and `sidprog._CMPS` carry `INT_SLESS`/`INT_SLESSEQUAL`, so the
  dialect stays **one operator per
  p-code mnemonic in operand order**; `expr._apply` is the hermetic evaluator case, each side
  read at its own width; `eqlift_mem._OP`/`_CMP` is the way back into the graph and
  `framemath._FLAGS` the 1-bit classification. `sge` is the one graph tag with no mnemonic —
  `x >=s y` is `INT_SLESSEQUAL(y, x)` — so it prints as the swapped `<=s` and the map stays a
  bijection. `eqlift._OPS`/`pass1_node` are deliberately **not** given the mnemonics: that
  pair is rung (d2)'s own reader, and admitting them there would move the corpus artifact —
  #170's reasoning for `trunc`, unchanged. `idioms._CMP` already named both, so the catalog
  wanted the operator before the dialect could spell it and `compare-value` accounts it with
  no gap; `tests/test_vocabulary.py` carries the capability case (spelled, parsed, fixpoint,
  accounted, executed) and the instance that separates the signed reading from the unsigned.
  (2) **The witness owed it, and the refusal's own stated reason is what fell.**
  `witness6502` refused `INT_SLESS`/`INT_SLESSEQUAL` because "the reference evaluator spells
  none either" — it does now, so the compare is the borrow chain the unsigned one already
  emits plus a `BVC`/`EOR #$80` correction of the top byte's sign, read by `BPL`/`BMI`, and it
  agrees with the evaluator on every operand pair across the overflow boundary at both widths.
  One **new named refusal**: a signed compare over operands of unequal width, because the
  evaluator reads each side at its own width and the machine copy zero-extends. `INT_SCARRY`
  is the signed refusal that remains.
  (3) **#161's substrate facts, and one nobody had named.** With the operator in, the splice
  reached five more, four of them the unified renderer not knowing the dialect it prints:
  the arm table's header was `switch {` for every kind (now `switch goto {` /
  `switch call {` with its bare-label line / `switch <cell> {`); an `asg` dropped its local's
  width suffix (`q0` for `q0:2`); an `if` over a lone `unobserved` arm did not collapse to the
  one-liner and an `ifnot` printed as `if`; and simple statements sat one level left of where
  `frameproc._Printer` puts them. The fifth is not a spelling: **`walk` silently dropped
  every statement kind it did not know**, `pcall` among them, so `a, x = sub_1485(a)` left the
  text and the reads after it took stale registers — invisible in the text and visible only to
  the frame oracle. A `pcall` now converts its arguments, walls memory over the callee's own
  footprint (`_mem_writes` takes a `pcall`'s entry, which it did not) and renders; an unknown
  kind **raises**. `emit_mem` never saw any of this because the raw `_Builder` procedures it
  renders carry no `pcall`, no promoted call and no `unobserved` one-liner — which is exactly
  what #161 asked to be verified rather than assumed.
  (4) **The cutover's semantic half is landed; what is left is the text.** The strict xfail
  stands with five landed controls beside it: the signed spelling parses, the dispatch header
  and the promoted call survive, an unliftable statement raises, and **`frameval.gate_fp` on
  the spliced text is `None`** — the spliced program reproduces the walker's per-frame
  projection. What fails is the `dumps`/`loads` fixpoint alone, and it is three spellings with
  **two** causes, separated by measurement rather than assumed to be one. Two are the unified
  printer being narrower than `frameproc._membody` and owe nothing to the declarations: a
  declared table read at an index *expression* stays `mem[…]` because `eqlift._Printer`'s
  `_loadref` splits a base only where the index is a `zext` of a bare local or cell, where
  `frameproc._index_of` takes whatever the address adds; and the indexed SID register block
  spells a register name because the unified printer has no `sid.reg[i]` view. `_memref` emits
  both off a bare parsed tree with `res` empty and no `pairs`, which is what says the cause is
  printer breadth. The third is the declarations: a declared lo/hi column pair stays the
  OR-pack because the pack recognizer reads `frameproc._PAIRS`, which `render_lines` is handed
  and `render_proc` is not. Each round-trips to the same program; the next landing widens the
  printer and passes the pairs, and §5's deletions ride with it. Two smaller divergences the diff also named are closed here rather than left
  to it — a valueless `ret` names the procedure's declared returns (`render_proc` takes them),
  and a wide store carries its `hi-first` order, which was dropped and is a fact about the
  store rather than its address (frameprog.md §7.10.4).
  (5) **The prototype is the acceptance gate, so the prototype's parser moved too.**
  `examples/state_machine_lift.py` read exactly `switch {`, `if … {` and bare `ret`, so the
  new headers, the `ifnot`, the `unobserved` one-liners, `ret <names>` and the `hi-first`
  prefix all had to be parsed rather than tolerated: `ifnot C` is `("not", C)` and not an arm
  swap, which is the difference between holding the size ratchet and inflating it by nine
  empty-armed lines. The ratchets **hold exactly** at 339 lines / 836 nodes.
  (6) **The gates and the steering metric.** `tools/emit_identity.py --expect 37b87140…`
  passes — 624 tunes, 0 refused, 28,513,156 bytes, **unmoved**: nothing here is on frameprog's
  own emit path, which is what capability with zero use means. `gate_sweep` at full
  Songlengths: **624 build / 624 evaluate / 624 clean**, zero divergences, zero refusals.
  Suite 2,724 passed / 490 skipped / 33 xfailed (nine new cases, no pin flipped), oracle 16.
  The 25-exemplar review at `DI_EQLIFT_EMIT_S=600` is clean — zero faults, refusals,
  regressions, unproved sites and extraction fallbacks — and **stage 4's own steering metric
  falls**: emitted size OFF 27,461 → **26,826** and ON 27,445 → **26,811**, −634 lines on the
  ON path, entirely from the renderer learning the dialect's one-liner forms. The graph itself
  is untouched and says so: 13,909 extraction sites and 3,309 changed, both unmoved, 12,128
  proved (one more), 18 of 25 identical between the paths, `d_lines` −16 → −15 and `d_stores`
  −3. The shredder's `test_the_unified_emitter_spells_the_dispatch_header_without_the_goto`
  was a control written to move with the cutover and it moved with it; no stage-3 pin flipped
  — the ~16 substrate-dependent ones want the declarations, so they are named for the next
  landing.
- **2026-08-10 — stage 4, landing 2 (part): the unified printer reads an address the way the
  dialect does, and the cutover gate goes green.** #172's three spellings, closed on the two
  mechanisms its measurement separated.
  (1) **The address reading is `frameproc._index_of`'s, and it is read once.**
  `eqlift._Printer._split` takes a `base + index` at width 2 with exactly one constant
  `>= $0100` and the index whatever the address adds — `zext2` stripped, because that widening
  is the reader's own (`grammar._index_addr`) — so a declared table read at an index
  *expression* spells `m_14D3[(ctr_0043 & zp_46)]` instead of `mem[…]`. `_loadref` then routes
  a base that `grammar.sid_base` names to the `sid.reg` view with the register's own offset
  folded into the index, which is the one spelling `frameproc._membody` prints back — the
  register-named indexed form was the same address under a name the dialect does not index.
  (2) **The pack wants the registry, and nothing else does.** `render_proc` takes `pairs`
  (`frameprog._decl_pairs`, the ONE lo/hi registry) and hands it to the printer, where
  `_pair_pack` reads a byte-column OR through `frameproc.pair_site` itself rather than a second
  copy of it, so a declared pair reads `m_148F[t3]:2`. With no registry the two breadth
  spellings still land and the pack stays the OR: #172's separation, now asserted on the
  printer rather than on `frameproc._memref` standing in for it.
  (3) **The cutover gate flips.**
  `test_the_spliced_emitter_carries_the_rung_built_statements` is green — the spliced text is a
  `dumps`/`loads` fixpoint at 922 lines with zero divergent spellings, and `frameval.gate_fp`
  on it is `None`. §8 step 4's **text** half is landed on the canonical example. What step 4
  still owes is the unconditional path: frameprog emission itself, and with it §5's
  `_Prune`/`_inline` deletions and rung (d2)'s per-site graphs, which are subsumed only where
  the unified graph is what emits.
  (4) **The prototype is the acceptance gate, so the prototype moved.** `sid.reg` is a store
  row, so `state_machine_lift._store_addr` names the view (`grammar.VIEW` -> `$D400`) and
  `test_variable_arity_dispatch_operator` reads `sid.reg[a] =`. `LINE_PIN` **holds exactly at
  339**; `COST_PIN` **falls 836 -> 820**, because an indexed read parses to fewer nodes than
  the add it was spelled as.
  (5) **The gates, and a steering metric that correctly does not move.**
  `tools/emit_identity.py --expect 37b87140…` passes — 624 tunes, 0 refused, 28,513,156 bytes,
  **unmoved**: nothing here is on frameprog's own emit path. `gate_sweep` at full Songlengths:
  **624 build / 624 evaluate / 624 clean**, zero divergences, zero refusals. Suite 2,726 passed
  / 490 skipped / **32** xfailed (one flipped), oracle 16. The 25-exemplar review at
  `DI_EQLIFT_EMIT_S=600` is clean — zero faults, refusals, regressions, unproved sites and
  extraction fallbacks — and its rollup is **byte-identical to #171's**: OFF 26,826 / ON 26,811,
  `d_lines` −15, `d_stores` −3, 13,909 extraction sites, 3,309 changed, 12,128 proved, 18 of 25
  identical. A respelling is not a line, so the rollup cannot see this landing; the texts can,
  and they moved on **24 of 25 tunes on both paths** (`Goto80/Automatas` alone unmoved), **313
  lines each path, every one at an unchanged line count** — 163 indexed reads that named their
  row, 150 register-file accesses that took the view.
- **2026-08-10 — stage 4, landing 2 (part): §5's liveness scaffold is deleted, and the
  review's second path becomes a recorded baseline.** Adoption §5's first inventory entry,
  taken as the deletion it is stated to be.
  (1) **The selection goes, not just its default.** `eqlift_mem._dce`, `_temp_sweep`, the
  `ROOT_EXTRACT` flag with its `DI_EQLIFT_ROOT_EXTRACT` env, and every `root_extract`
  parameter on `render_proc`/`emit_mem`/`emit` are gone; the two `if root_extract:` guards
  become the path. What survives is `_liveness` in its **rooting** role — which registers
  `roots` roots — and `_share_once` as the rule §5 says has a second warrant, called by the
  one path that is left. `test_the_liveness_deletion_path_is_gone` asserts the absence by
  name, so the scaffold cannot come back as a parameter.
  (2) **A deleted path is not a second opinion, so the baseline is recorded.**
  `tools/eqlift_measure.py` measured ON against OFF; with OFF deleted it measures one run
  against `--baseline`, a previously recorded artifact. The gate keeps its shape — no fault,
  no refusal, no unproved change, and **no tune emitting more lines or stores than the
  baseline** — and `identical` now counts the tunes whose sha did not move, which is what the
  §4 review reads. A run with no baseline reports zero deltas and gates on faults alone.
  (3) **The deletion is measurably a deletion.** The 25-exemplar review at
  `DI_EQLIFT_EMIT_S=600` against #173's recorded ON run is **25 of 25 byte-identical**:
  26,811 lines, 1,988 stores, `d_lines` 0, `d_stores` 0, 13,909 extraction sites, 3,309
  changed, 12,128 proved, zero faults, refusals, regressions and extraction fallbacks. Wall
  time halves, because there is one path to run.
  (4) **The gates.** `tools/emit_identity.py --expect 37b87140…` passes — 624 tunes, 0
  refused, 28,513,156 bytes, unmoved. `gate_sweep` at full Songlengths: **624 build / 624
  evaluate / 624 clean**, zero divergences, zero refusals. Suite 2,724 passed / 490 skipped /
  32 xfailed (two fewer cases: the ON/OFF parametrizations collapse), oracle 16.
  (5) **What §5 still holds.** `frameproc._Prune`/`_prune`, `_inline`/`_inline_list` with the
  repolish fixpoints, and rung (d2)'s per-site e-graphs are subsumed by the unified path
  *where the unified path emits*, which frameprog does not do yet. They land with that
  switch, on its corpus diff — deleting them before it would not be a deletion but a
  degradation, which is a different claim than the one §5 makes.
- **2026-08-10 — stage 4, landing 2 (part): the saturation schedule stops reading the
  clock.** §10's determinism clause, applied to the one place in the lifter that broke it.
  (1) **Two bounds, both functions of the program.** `eqlift_mem.saturate` cut a round when
  the last one's growth ratio said the next would not fit `DI_EQLIFT_BUDGET_S` seconds, or
  when resident growth passed `DI_EQLIFT_BUDGET_MB` — a wall clock and an RSS reading, so
  the emitted text was a function of the machine and its load. It is now a **round cap**
  (`DI_EQLIFT_ROUNDS`, default 6) and a **node bound** (`DI_EQLIFT_NODES`, default 30,000 —
  the bound `framemath._saturate` already runs rung (d2) under, which is why rung (d2) never
  had this defect). `BUDGET_S`, `BUDGET_MB` and `_rss_mb` are deleted; `render_proc`'s
  `budget` now funds extraction alone, which is the one wall-clock cut left in the lifter
  and is loud (`stats["extract_fallback"]`, zero on every exemplar).
  (2) **The cap is measured, and the measurement is the finding.** Emitted total over the
  25 exemplars by round cap: 26,814 at 4, 26,812 at 5, **26,811 at 6, 7, 8, 10 and 12** —
  and 26,811 is exactly what the clock-cut schedule reached. So size converges at 6. Past
  6 the size is fixed and the **spelling keeps moving**: byte-identity with cap 6 is 24/25
  at 7, 23 at 8, 22 at 10, 20 at 12. And the size is not monotone the other way either —
  `Tel_Kees/Before_I_Forget` is 1,553 lines at 4 rounds and 1,554 from 5 up, so a round
  *added* a line. More rounds is not better; a cap has to be chosen, fixed and recorded,
  which is the whole argument for a cap over a budget.
  (3) **The §4 review: 6 tunes, 50 lines, no size anywhere.** 19 of 25 byte-identical to
  #174; every moved tune's line and store counts are **unchanged**. `Tel_Kees` (38 lines):
  four sites where `(((x << $01) << $01) << $01) << $01` fuses to `x << $04`, two where the
  signed compare replaces the `& $80` mask test, one pack commuted, and a run of loads that
  respells a shared index through the local the loop holds; one line grows, an address
  recomputed from its source terms rather than read back out of the cell just written, which
  is the memory price working. `Rambo` two carries collapse into one comparison; `Wizball`
  a branch reads `cflag` instead of respelling its compare; `Atmosphere_II` an index takes
  the local instead of the cell; `Grid_Runner` a pack takes `le_pack`'s own operand order;
  `Deek/4_Tunes` one copy gives way to the deepest read. Rules that the clock had cut off
  now fire, and some it had let run no longer do — which is (2) restated as text.
  (4) **The gates.** The exemplar review is clean — zero faults, refusals, regressions,
  unproved sites, extraction fallbacks — at 26,811 lines / 1,988 stores, `d_lines` 0 and
  `d_stores` 0 against #174, 13,909 sites, 3,322 changed (13 more reach a rule), 12,128
  proved. **Determinism, measured twice**: a second run is 25/25 byte-identical, and a run
  at the *default* `DI_EQLIFT_EMIT_S=60` rather than 600 is also 25/25 byte-identical. Wall
  time per tune falls roughly threefold (`Deek` 18.4s -> 2.4s). `tools/emit_identity.py
  --expect 37b87140…` passes — 624 tunes, 28,513,156 bytes, unmoved. `gate_sweep` at full
  Songlengths **624 / 624 / 624 clean**, zero divergences and refusals. Suite 2,724 passed /
  490 skipped / 32 xfailed, oracle 16. The prototype holds exactly: 339 lines / 820 nodes.
- **2026-08-10 — stage 4, landing 2 (part): a cell the data section declares stops being
  declared twice.** 3a's own finding (`Agent_X_II` `$6923`/`$6925`), discharged — the
  housekeeping list called it discharged by stage 3's first text-moving landing and it was
  not; it is measured here before it is fixed.
  (1) **The rule already existed; only its input moved.** `_state_fields.hidden` refuses a
  cell that sits inside a declared span, and `_pair_tables`/`_declare_cells` carve new spans
  in the four-pass fixpoint that runs *after* it. `frameprog._drop_declared` applies the same
  rule once more where its input changed, so the loose lo/hi pair the pack witness carves
  leaves `state { }` and is declared in `data { }` alone, with its role. It is stated as the
  span, not as the pair: any cell a declaration covers.
  (2) **The corpus diff: one shape, 124 tunes, every one smaller.**
  `tools/emit_identity.py`: 624 tunes, 0 refused, 28,513,156 -> **28,506,888 bytes**,
  aggregate `37b87140…` -> **`bc256138777fb033fc2b3d49d8b54c21218d5c87c3402a1e838eae04a59e3f5b`**,
  **124 of 624 moved and every one shrank** (−24 bytes on 73 of them, −168 at the largest,
  −6,268 total). Twelve tunes across every size class were diffed in full: **68 deleted
  lines, zero added lines**, and every deleted line is a `state { }` field whose name the
  data section declares. `gate_sweep` at full Songlengths is **624 / 624 / 624 clean**, zero
  divergences and refusals, so the removed declarations were carrying nothing.
  (3) **What the diff also exposed, named not fixed.** Two of the twelve drop an *array*
  state field (`m_1090: u8[]`, `m_F6B6: u8[]`) whose data declaration is `table X[1] mut 0`
  while the text writes `X[x]`. Carving the pair is right — it is what renders the word
  column instead of the OR-pack, and refusing an indexed cell was tried and measured: it
  costs `Akira_K/Data_Data_Data_Data` four packs and more lines than it saves. What is wrong
  is the `[1]` extent and the `mut 0` on a cell the emitted text indexes and writes;
  `_cell_decl` reads `model.written` at the base alone. That is a declaration-truth defect
  of `_declare_cells`, not of this rule, and it is owed its own measurement.
  (4) **The gates.** Suite 2,726 passed / 490 skipped / 32 xfailed (two new cases), oracle
  16. The 25-exemplar review re-dumped against the moved artifact is **25 of 25
  byte-identical** at 26,811 lines / 1,988 stores — `emit_mem` renders no state section, so
  the frameprog header cannot reach it, which is the check that says the move is where it
  is claimed to be.
- **2026-08-10 — stage 4, landing 2 (part): the stage-3 pins are re-measured against the
  cutover's own emitter, and `_emit` was the wrong stand-in.** #161 measured every stage-3
  pin's goal property against `eqlift_mem.emit`; `emit` renders raw `_Builder` procedures,
  and the cutover renders `frameprog.program`'s **rung-built** statements. A rung that
  rewrites a statement before emission is invisible to the first and decisive for the second.
  (1) **The measurement is now the cutover's.** `_spliced(name)` in the shredder is
  `test_step4_splice`'s renderer over the fixture's own frame program — `render_proc` with
  the declarations, the footprints and the `_PAIRS` registry, spliced in place of
  `render_lines` — and `test_the_stage_three_pins_are_measured_against_the_cutover_emitter`
  runs **all 24 pins' bodies** against it and asserts exactly which pass. The disposition is
  therefore held by test rather than asserted in a reason string.
  (2) **One of 24 flips, and it is the one that was predicted to.**
  `test_borrow_chain_is_one_wide_compare` passes on the spliced text: the borrow chain is
  neither `carry(` nor `$01 - (zext2(..) <= zext2(..))`. Its `unified path: holds today`
  verdict is now measured on the emitter that decides it. The other 23 hold their recorded
  verdicts, so "they flip at the cutover" remains true of one pin, not of the family.
  (3) **One recorded verdict was wrong, and the correction names its owner.**
  `test_lone_lane_half_owes_no_register_load` was recorded as `holds today` because `emit`'s
  raw procedure carries the bare `sid.v1.freq_hi = t0`. The cutover's emitter is handed
  frameprog's own rung output — `sid.v1.freq_lo:2 = ((sid.v1.freq_lo:2 & $00FF):2 | (zext2(t0)
  << $08):2):2`, the lone half widened to a read-modify-write of the u16 register — and no
  admitted rule removes a read of a write-only sink, so the pin does **not** flip. Its verdict
  becomes `refuses, and the mechanism is named`, and the owner is the widening rung, not the
  graph. Both control tests now assert the two unified texts and the disagreement between
  them, so the correction cannot rot.
  (4) **The gates.** Tests only: `tools/emit_identity.py --expect bc256138…` passes (624
  tunes, 28,506,888 bytes, unmoved) and `gate_sweep` at full Songlengths is **624 / 624 / 624
  clean**. Suite 2,727 passed / 490 skipped / 32 xfailed (one new case, no pin flipped
  against `_lift` — the cutover is still a test, not the artifact), oracle 16.
- **2026-08-10 — stage 4, landing 2: the owed `returns` set is measured and REFUSED, and
  the refusal is the finding.** #169 named "a precise `returns` set for a procedure every
  entry of which is a `call`" as what would take back `sub_4921`'s conservative boundary. It
  was built (`_Info.call_only`, `returns` accumulated at raw `call` sites, `ret_live` taking
  it instead of `_Info.G`) and it is **wrong**, by the corpus gate rather than by argument.
  (1) **What it bought.** Exactly the two tunes #169 priced: `C64_World` **−192 bytes / −7
  lines** and `1st_Decent_Hardcore` **−299 bytes / −10 lines** against +196 and +303 — the
  whole price back, line for line (`x = m_4963`, `y = m_4965`, `cflag`, `zflag`, plus
  `vflag`/`nflag`, and the temporaries that fed them). Nothing else in 624 moved.
  (2) **What it broke, and why the premise is false.** `gate_sweep` at full Songlengths went
  624 clean -> **622 clean, 2 refused**: both tunes raise `FrameFault: unobserved $4F16
  reached`. "Every entry is a `call`, so every exit returns to that call's successor" is false
  for exactly the procedures the rule can reach. The only procedures it relaxes are the ones
  `frameproc.slot_reader` blocks — and `slot_reader` blocks them *because they rewrite their
  return slot and return somewhere else*. `sub_4921` returns past the inline data, not to
  `$4ED6`, so the live-out at its `ret` is not the union over the call sites at all. Restated:
  the rule is empty where it is sound and unsound where it is not empty.
  (3) **The mechanism that would work, named.** The live-out of a slot-rewriting callee is
  the live-in at the pcs its slot may name. Those are derivable per site by the same reading
  `framestack.lift_rts_trick` uses where `sp` concretizes — call site plus inline-data length
  — and the shredder's four-fixture family already carries every spelling (one site, two
  sites, two depths, per-site length). A `ret_live` that unions the live-in at each site's
  resume pc is sound; `returns` accumulated at the call statement is not. That is the landing
  this owes, and it is a `framestack` reading, not a `_Info` relaxation.
- **2026-08-10 — stage 4, landing 3 (part): the unified emitter is measured on the corpus,
  and sixteen faults it found are fixed.** The cutover's text was green on the canonical
  example alone; §4's review discipline is the corpus, and the example hid every fault
  below.
  (1) **The measurement is a tool, and it has a control.** `tools/splice_sweep.py` asks of
  each of 624 tunes what the switch will ask — the text parses, it is a `dumps`/`loads`
  fixpoint, every local it reads has a definition (`frameprog.lint`), and the program it
  parses back to reproduces the walker's projection under Gate FP. `--baseline` runs the
  same four over `frameproc.render_lines`' own text, and **that is the finding the control
  produced**: 87 of 624 tunes fail them on the emitter that ships today (27 evaluation
  faults, 9 lint, 52 divergences), so the parse-and-evaluate path is not a corpus property
  and a landing may only be measured as a difference against it. Gate FP as `gate_sweep`
  runs it evaluates the *analysed* program and never reads the text, which is why this was
  never visible.
  (2) **The address reading, at the dialect's breadth.** `_split` reads a `sub` by a
  constant as the add it equals mod $10000 — `sub_to_add`/`add_to_sub` are both admitted,
  so §10 forbids depending on which representative extraction returns — and `fmt` spells a
  constant subtrahend the way `_addref` does, so the two representatives print one text.
  `_loadref` splits at the access's own width (a word row was `mem[..]:2`), the `sid.reg`
  view stays the byte's, and a rung-(f) resolved deref names its pointer cell: `derefs` is
  the resolved cell set, threaded `frameprog.render_ctx` -> `render_proc` -> `_Printer`,
  and it reads the fused word cell and the unfused `hi<<8|lo` pack alike. The canonical
  example cannot see this: `test_step4_splice` passed no `resolved` and the example's own
  deref prints the same either way.
  (3) **Widths, statements and shapes the walk had never met.** A local reads at the width
  its definition states (`m_1155[x]:2 = d3` is a byte value in a word store, which the
  parser refuses) and `_ew` takes `grammar.store_width`'s rule instead of "a local is a
  byte". `for` — which `frameproc._forloops` mints and the walk raised on — is walked as a
  loop whose counter havocs with the body. A declared pair's two half stores fuse to the
  word column, compared on the printed form (a version the base still holds prints the
  same) and across the nodes root extraction dropped, because otherwise the re-dump fuses
  them and the text is not a fixpoint. An else arm whose every node is dead prints no
  `} else {`.
  (4) **One dialect gap, taken as capability.** A folded word literal had no spelling:
  `grammar.store_width` read every `const` as one byte, so `filter.cutoff_lo:2 = $0000` was
  unparseable — and `framefuse._pack` says so in a comment, keeping two constant halves
  apart to work around it. A literal's width is its digit count (`grammar._const`), which
  is the rule every other value already states; `tests/test_vocabulary.py` carries the case
  (spelled, parsed, fixpoint, executed). 23 tunes were unparseable on this alone.
  (5) **A rule fired outside the width it was proved at.** `INT_CARRY`'s node width is its
  one-bit result, and both converters passed it as the carry's lane, so `carry_ult` —
  Z3-proved at width 1 — matched a carry over two u16 loads and rewrote it to an 8-bit
  wrap test. `eqlift.carry_lane` reads the lane off the operands (`expr._apply`'s own
  reading), `pass1_node` maps a carry back at width 1, and the §6 site proof that had
  **refused** on `Akira_K/Data_Data_Data_Data` passes. Emit identity is unmoved, so rung
  (d2) never saw a word-lane carry; the fault was reachable only after rung (d) fuses.
  (6) **Five rooting faults, each a local the text reads and no definition defines.**
  `_node_terms` knew no `pcall`, so an argument's spelling rooted nothing and the call
  passed an undefined local. `_liveness` ran over the registers alone, so a value local a
  join renames — read by base name at a version no def carries — looked dead in both arms;
  it now runs over every local, and `_share_once` refuses to inline away a base some
  surviving read names at a version no def carries. `sp` is machine state and never faint,
  and an asg whose operand reads a volatile is rooted for its input order, both as
  `frameproc._Prune` has them. `_liveness` gets `_Flow`'s remaining cases: the call-body
  rule (a body some `call` enters carries the machine set at its exit), `armret` (an
  inlined callee's `ret` continues after the call, not at the procedure's), the loop-head
  fixpoint's shape, and `info.labmap`'s seed.
  (7) **Two stale-version faults, and they are the reason the machine disagreed.**
  `wall`/`havoc_all` havocked only the names already in `env`, so a register the procedure
  had not yet assigned kept its block input spellable across a call and a store forwarded
  over it — `Amazing_Spider-Man`'s dispatch target came back as `$60B0`. And a `callb`
  body (and a `swc` arm) runs with the stack pointer the machine call moved, so `sp` havocs
  before the body: `Cool_Air`'s `ret` read `$0046`. `live()`'s block-input test was
  `endswith(".0")`, which matches `x.10` and `x.20`.
  (8) **The gates.** `tools/emit_identity.py --expect bc256138…` passes — 624 tunes, 0
  refused, 28,506,888 bytes, **unmoved**. `gate_sweep` at full Songlengths **624 / 624 /
  624 clean**, zero divergences and refusals. Suite **2,738 passed / 490 skipped / 32
  xfailed** (eleven new cases, no pin flipped), oracle 16. `tools/splice_sweep.py` at full
  Songlengths: **zero new failures against the control, three fixed**, the fixpoint holds
  on **624 of 624**, and emitted size falls **−4,603 lines with no tune larger** (575
  smaller).
  (9) **The exemplar review moves, and the direction is the honest one.** The 25-exemplar
  measure at `DI_EQLIFT_EMIT_S=600` is clean — zero faults, refusals, unproved sites and
  extraction fallbacks — at **26,909 lines / 1,984 stores** against 26,811 / 1,988: **+98
  lines on 15 tunes**, `Atmosphere_II` −25, 2 identical. Sites changed fall 3,322 ->
  **2,884** and sites proved rise 12,128 -> **12,226**, which is (6) and (7) restated: the
  added lines are register and value definitions the text reads, and the retired rewrites
  are the ones that were spelling a version the base no longer held. A size ratchet is a
  steering metric and not a soundness law, so the new run is recorded as the baseline
  (`out/eqlift_measure_s4l3.json`) with this review rather than treated as a regression.
  (10) **One context, not three.** `eqlift_mem.render_ctx`/`artifact_lines` are the emitter
  context the switch will call, and `tests/test_step4_splice.py`, `test_shred_regmodel`'s
  `_spliced` and `tools/splice_sweep.py` all read it, so the cutover's emitter cannot drift
  from the one the pins measure. It sits with the renderer rather than in `frameprog`
  because `eqlift_mem` reads `frameprog._state_lines`, and the switch owes that cycle its
  own answer.
- **2026-08-10 — stage 4, landing 3 (part): every pin's reason names a live owner, and the
  switch's blocker is named.** An xfail audit found four prototype pins deferring to a
  CLOSED stage, which the deferral discipline forbids.
  (1) **Three have owners.** `test_state_block_holds_no_scratch` ("stage 3b/3c") is landing
  3's `state { }` demotion; the two role pins ("3c/4") and the VM operator-set pin ("3d/4")
  are landing 4's role-typed emission. The shredder's `_S3` prefix is where a pin was
  *raised*, not who owns it — each carries a `_MEASURED` disposition against the cutover's
  own emitter — and the stage close owes that family the same re-pointing.
  (2) **One has none, and says so.** `test_no_byte_lane_update_of_any_declared_u16` needs a
  variable-stride cursor advance folded, and no admitted rule folds one; admitting a rule
  is a corpus-diff decision (adoption §4) nobody has priced. The reason states that rather
  than naming a stage that would not flip it.
  (3) **The switch's blocker, measured rather than assumed.** `frameprog` cannot call
  `eqlift_mem` while `eqlift_mem` reads `frameprog._state_lines`: pylint's R0401 fails CI,
  which is why `render_ctx`/`artifact_lines` sit with the renderer. The three answers are
  in the position paragraph; the third — retiring `emit`/`emit_mem` as the second
  projection — is the one the plan's own precedent points at.
- **2026-08-11 — stage 4, landing 3: the switch. `frameprog.dumps` renders through the
  unified graph, the corpus artifact moves, and the two faults the switch found are fixed.**
  (1) **The recorded answer to the cycle is overturned by measurement, and the measurement
  is the prototype.** #181 pointed at retiring `emit`/`emit_mem` as the second projection.
  `emit`'s one consumer is `examples/state_machine_lift.py`, the plan's acceptance gate, and
  it refuses the artifact twice over. Its dialect parser fails on the artifact's **first**
  statement (`SyntaxError: trailing ':' in 'ctr_0030:2'`; behind the width suffix stand
  `pcall`, `carry(`, `trunc1(` and `*ptr[i]`). And its fold layer — `FOLDS`: `pair_set`,
  `pair_store`, `advance`, `wide_cmp`, `wide16`, `wide24`, `row_read` — is *stated over the
  byte lanes rung (d)/(d2) has already fused*: where `emit` gives the three-byte ADC chain
  the artifact gives `q0:2 = ctr_0030:2; ctr_0030:2 = (q0:2 + $2B91):2`, so those proofs have
  nothing to fire on and fifteen green pins go red. Re-basing the fold layer onto the
  artifact is landing 4's own subject (role-typed emission moving into the engine), not a
  line in the switch. So the switch takes the **first** recorded answer for the cycle and
  says what `emit_mem` now is.
  (2) **The cycle, closed by moving the reading and not the emitter.** The `state { }`
  emitter — `_scan`, `_cells`, `_state_fields`, `_drop_declared`, `_field_line`,
  `_extent_names`, `_state_lines` and the `_INPUTS`/`_ZERO`/`_SID_*` constants — moves to
  `sidprog`, which already holds `_data_lines`, `_symbol_lines`, `_image_lines`,
  `_alias_sub` and `_addr_name`; `frameprog._decl_pairs` becomes `datadecl.decl_pairs`. With
  both readings where their inputs live, `eqlift_mem` imports neither and `frameprog`
  imports `eqlift_mem` at module scope: no R0401, `pylint deity_informant/` 10.00.
  `emit_mem`'s docstring now states what it is — the prototype's **pre-rung substrate**, not
  the artifact and never a second projection of it, one consumer, retiring with the fold
  layer at landing 4. `tools/eqlift_emit.py` and `tools/eqlift_measure.py` are deleted and
  the 25-exemplar review with them: it moves to the corpus, as #179 recommended.
  (3) **What `dumps` renders, and why the fixpoint is still the gate.** `FrameProgram`
  carries `landings` (`framefuse._landings(model)`, the one model fact `render_ctx` read) and
  `lines()` renders once — through `eqlift_mem.artifact_lines` when the program is analysed,
  through `frameproc.render_lines` when it is parsed (`landings is None`). So
  `dumps(loads(t)) == t` asks whether the unified emitter's text is what `render_lines`
  prints for the program it parses back to, which is what makes the fixpoint a gate on the
  emitter rather than an accident, and it holds on **624 of 624**. `frameprog.render_lines(prog)`
  is the projection step 4 replaced, kept as the control: patched over `FrameProgram.lines`
  it gives the pre-switch text, and `splice_sweep --baseline` and `test_step4_splice`'s
  control are the same one call. The block-model rebuild is unaffected by construction —
  `block_model` reads the evidence channels and the image, never the statements — so the
  emit identity is the same cold or warm off the sweep cache.
  (4) **A soundness fault: a store forwarded into a volatile read.** `m_D019 = $81` followed
  by a read of `$D019` came back `$81`. A volatile load is not the last store's value —
  `$D012` counts, `$D019` and `$DC0D` read zero (spec 1.3) — and two such loads are not one
  value. `eqlift_mem._may_read_vol` reads the load's address: a constant in
  `structured._VOL | _VOL0`, or a span (`_rd_span`, 2b's extents included) that may cover
  one, or ⊤. Such a load is served from a **fresh opaque memory**, so neither the store
  chain nor the e-graph's own sharing can reach it. `frameproc` had this refusal
  (`sidprog._ld_safe`) and the unified chain did not; the fuzz fixture built for exactly
  this rule (`test_frameval.py::test_constant_zero_sources_read_as_the_walker_reads_them`)
  is what caught it, and it was invisible before the switch because nothing evaluated the
  unified text.
  (5) **A rule the word-store forward needs, admitted and proved.** A word stored and then
  read lane by lane came back as its own repack —
  `sid.v1.freq_lo:2 = ((zext2(trunc1((d0:2 >> $08):2)) << $08):2 | zext2(trunc1(d0:2))):2` —
  because no rule stated the dual of `pack_hi`/`pack_lo`. `pack_split`
  (`(hi<<8)|lo == x` where the lanes are `trunc(x>>8)`/`trunc(x)`) is admitted at width 2,
  Z3-proved with the other 90, and the six `test_shred16` byte-shadow cases it broke go
  green. Every 16-bit datum a 6502 writes wide and reads lane by lane meets it.
  (6) **§6's all-sites law now runs where the text ships.** `artifact_lines(prog, proofs)`
  collects one site record per procedure and `tools/splice_sweep.py` verifies them as a
  check beside `parse`/`lint`/`fixpoint`/`gate`; `eqlift_measure --prove` had been proving
  `emit`'s sites, which are no longer the artifact's.
  (7) **One pin flips and one law weakens, both on the record.**
  `test_borrow_chain_is_one_wide_compare` XPASSed the moment the artifact became the unified
  text and its xfail is gone — the wide compare is an artifact fact, the family is 23 pins,
  and the artifact re-measurement (`_spliced` retired onto `_lift`) expects no further
  XPASS. M-FP2's `prog.procs == src.procs` **weakens**: the text is the minimized program,
  so the trees it parses back to are the emitted ones. What stands is the entry/parameter/
  return identity, the text fixpoint and Gate FP.
  (8) **Five green tests were restated, none deleted, and each restatement is the emitter
  working.** Single-use values spell at their use (`sid.v1.ctrl = (m_1400 + $10)`, not
  `s0 = ...`); a store whose cell no surviving read names retires (`zp_FB`, `m_1500[x]`,
  `zp_16`), which is root extraction's scratch rule and is what finally gives the `state { }`
  demotion a subject; the byte-lane carry prints as the unsigned compare it is
  (`((ctr0 + $37) < $37)`), so `test_framemath` asks for the carry in either spelling.
  (9) **§5's `_Prune`/`_inline` are NOT subsumed by this switch, and the reason is their
  consumer.** They were listed as replaced by root extraction and `_share_once` "on the
  unified path", and for the *rendering* that is true. But `frameproc.procedures` and
  `repolish` run them **before** rungs (d), (d2), (f) and (g), which pattern-match the
  polished statements — `framemath`'s carry chain, `framefuse`'s pairs, `frameptr`'s derefs.
  Deleting them is a rung-input change measured by `gate_sweep` plus a full emit-identity
  diff, not a rendering change, and it is the next part's, with that as its mechanism.
  (10) **The §4 review, at the sampling the diff justifies.** 623 of 624 tunes moved (one,
  `Blanchette_Francois/Bird_on_the_Run_II`, is byte-identical); the aggregate is `f9f025b1…` at
  **28,258,654 bytes**, −248,234. Read by hand: `Akira_K/Data_Data_Data_Data` (−3,097, the
  largest) is a strict readability win — `nflag = (a <s $00)` for the sign-bit mask,
  `if (a < $20)` for `ifnot ($20 <= a)`, `ptr_2B99:2 = ptr_2B91:2` where the same pack was
  printed twice, and single-use loads spelled at their sink. Five tunes grew and
  `Cuomo_Jim/Cage_Match` (+831 bytes on **five fewer lines**) is why: the PLP status word
  `m_01FD` is stored once and read three times, and each read re-spells the whole
  seven-term rebuild instead of naming it. **That is the named finding this review leaves**
  — a memory forward with more than one reader is re-extracted per site, where `_share_once`
  names a multi-read local — and its owner is landing 4's steering metrics, whose headline
  is emitted size.
  (11) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624
  clean**, zero divergences and zero refusals. Suite **2,746 passed / 490 skipped / 31
  xfailed** (one pin flipped; the retired tool took its nine cases), oracle 16, coverage
  **89.92%**. `tools/emit_identity.py` records the new baseline
  `f9f025b13d8b4f3ac98aeb884ce194c142de729b834fe843be5b4eedfdb951b4`, 624 tunes, 0 refused,
  28,258,654 bytes. `tools/splice_sweep.py` at full Songlengths against its own control:
  **zero new failures, three fixed** (87 -> 84 bad),
  parse and fixpoint on **624 of 624**, **210,037 rewritten sites proved and zero
  unproved**, and −4,528 lines with **no tune larger** (575 smaller). `black --check` and
  `pylint` clean.
- **2026-08-11 — stage 4, landing 3: the `state { }` demotion, off root extraction's own
  scratch spans.** The switch gave the demotion its subject (the artifact retires a store no
  reader can observe) and left the declaration behind it, which is a declaration-truth break
  the emitter itself opened. It closes here.
  (1) **The mechanism is the extractor's, not the text's.** `eqlift_mem._scratch` already
  names the store nodes no reader in the artifact can observe — every other procedure's
  reads plus this one's extracted spellings, over the footprint map — and `wrspan` gives each
  one its address span. `render_proc` takes a `demoted` out-parameter, `artifact_lines`
  threads it, `FrameProgram.lines` fills `prog.demoted`, and `dumps` drops a state field
  whose cell a demoted span covers **and** which no emitted line names. Both conditions:
  the first keeps a declaration the text still uses, the second keeps a field the extractor
  never touched, and a parsed program demotes nothing, so `dumps(loads(t)) == t` holds by
  construction.
  (2) **The textual rule alone was measured first and rejected.** Dropping every field the
  emitted text does not name takes nine more fields on the canonical example and breaks
  twenty tests, `test_vocabulary`'s role and extent laws among them — a field's role, extent
  and observed set are the declaration's own content, so "the body does not mention it" is
  not a demotion. Worse, the nine are not scratch at all: they are the *hi* halves of pairs
  the unified graph fuses (`ctr_0030:2`) and rung (d) did not, so the state block declares
  two `u8`s where the text reads one `u16`. That is a **second** declaration-truth gap, it
  is not this one, and it is named in the open items with rung (d) as its owner.
  (3) **The pin flips.** `test_scratch_cell_is_a_local_not_state` — pinned at stage 3, its
  disposition measured as "refuses, and the mechanism is named" against the cutover's
  emitter — XPASSes and its xfail is gone; the shredder family is 22.
  `test_the_scratch_store_demotes_..._and_its_declaration_stays` retires into it, because
  nothing is left standing. `tools/storage_census.py` now reports **zero** declared scratch
  on the fixture that was raised to show one.
  (4) **The gates.** `gate_sweep` at full Songlengths **624 / 624 / 624 clean**. Suite
  **2,746 passed / 490 skipped / 30 xfailed** (the pin flipped), oracle 16. Emit identity
  moves on a two-tune §4 review — `Gray_Fred/Batman_the_Caped_Crusader` drops
  `idx_1182: u8` and `Gray_Fred/A_New_Kind` drops `m_C06C: u16`, one field each and nothing
  else — to `4b35ed0fd6969cde4a055963ae7fe3b94bdba241a89b566d21e8384fda583a65`, 624 tunes, 0
  refused, **28,258,627 bytes**. The splice sweep is byte-for-byte the switch's: zero new,
  three fixed, parse and fixpoint 624/624, 210,037 sites proved, −4,528 lines.
- **2026-08-11 — stage 4, landing 5 (part): the witness's last three refusals close, and the
  three `Asm` copies collapse onto one.** The ledger goal is zero, so each refusal was taken
  as capability rather than re-worded.
  (1) **The raw machine call is the evaluator's stack, on the machine.** `call`/`callb` were
  refused as "a JSR at a pc, not a serialized procedure call". The mechanism is the one
  `framestack.lift_rts_trick` reads: the pushed word is a **pc of the serialized program**,
  so the site pushes `ret` itself (`LDA #hi : PHA : LDA #lo : PHA`, byte for byte the
  evaluator's `push(ret)`) and transfers with `JMP`, and a raw callee's `ret` pulls that word
  back instead of `RTS`-ing on it. The pulled word takes the site table first — the
  evaluator's shadow stack, keyed by the word, which is unique per site — and otherwise
  resolves `w + 1` through the whole compiled label table, which is the evaluator's `rmap`
  arm for a callee that rewrote its slot. `callb`'s body is emitted inline under the pc the
  call names and no other label, as `frameval._s_callb` marks it. Two named refusals remain
  in that family and both are structural, not mechanical: a raw call to a pc no procedure of
  the program carries, and a callee the program also enters by `pcall` (one return
  convention per procedure).
  (2) **The stack depths already agreed, which is why the trick is witnessable at all.**
  `vm.run_sub`'s own dummy return leaves the frame at sp `$FD`, and `frameval.run_frame`'s
  `push(0x0001)` leaves it at `$FD` too, so a page-one cell a destacked program names is the
  same byte on both sides — a top-level call's word is `$01FC`/`$01FD` either way. The
  landed tests read the slot back and rewrite it, which is the RTS trick's own shape.
  (3) **The static image vector marks the body that follows it.** `igoto $P` with no
  computed pointer reads its word from the image *at run time* (the cell the program may
  have rewritten, with the 6502's own page wrap) and its target's body follows the transfer
  inline; `frameval.seq` marks that next statement under the word `mem0` holds, so the
  witness labels it the same way and the resolver reaches it. Paired with an arm table the
  mark does not happen, in the evaluator or here.
  (4) **The signed compare over unequal widths is sign extension, not a refusal.**
  `expr._apply` reads each side at its **own** width (`_signed(a, szs[0])`), so the machine
  copy must fill with the top byte's sign (`LDA #0 : BIT top : BPL : LDA #$FF`) and not with
  zero; the borrow chain and #171's `BVC`/`EOR #$80` correction are then already right. It
  is differentially checked against the reference evaluator over every boundary pair at both
  operand orders. `INT_SCARRY` and the unequal-width `INT_CARRY` stay refused on their own
  stated mechanisms.
  (5) **The `Asm` merge moves nothing, and that is a measurement.** #178 recorded the copies
  as semantically different — `_fuzzgen` admits illegal opcodes and resolves duplicate legal
  `(mn, mode)` pairs to the highest byte where `asm6502` takes the lowest — so the tables
  were compared before being merged: **`lifter.OPS` maps no `(mn, mode)` pair to two legal
  opcodes**, so highest-wins and lowest-wins are the same table, and the two pairs that carry
  both a legal and an illegal byte take the legal one either way. `asm6502.ENC_ILLEGAL` adds
  the undocumented opcodes for the pairs no legal one spells (lowest byte, as `_fuzzgen` had
  it) and `AsmIllegal` is the three-line subclass the fuzz corpus imports; the merged dict is
  `==` to `_fuzzgen._ENC` and **no fixture byte moves**. `examples/state_machine_lift.py`
  loses its copy for the import and nothing else. What the merge does change for the two
  adopters is `asm6502`'s two guards — a duplicate label and an out-of-range branch raise
  rather than pass — and no consumer trips either.
  (6) **The gates.** Suite **2,754 passed / 490 skipped / 30 xfailed** (eight new tests),
  `witness6502` coverage 100%, `black --check` and `pylint` clean, emit identity unmoved at
  `4b35ed0f…` (624 tunes, 0 refused) since no emitter source is touched.
- **2026-08-11 — stage 4: the variable-stride cursor advance folds, and the pin #180 left
  ownerless flips.** `test_no_byte_lane_update_of_any_declared_u16` was the one prototype
  pin whose reason named no landing: "a variable-stride cursor advance stays byte-lane
  because no admitted rule folds one".
  (1) **An eqlift rule is not what flips it, and the measurement says why.** In the artifact
  the shape is `ptr2 = ptr_0040_lo; t5 = (y + ptr2 + $01); ptr_0040_lo = t5;
  cflag = (carry(y, ptr2) | carry((y + ptr2), $01)); ifnot cflag { } else unobserved $10FE`.
  There is **no pack to fuse**: the hi lane is never written, and the carry feeds an
  *unobserved* guard. A value rule cannot take "unobserved" as a premise, so the fold is the
  guarded one — the prototype's own `advance` rule, which carries its Z3 obligation.
  (2) **The fold generalizes, obligation unchanged.** `_add_const` reads the stride off an
  add chain that reads the cell exactly once, whatever the rest of the chain is (refusing a
  chain that reads another declared cell), so it returns a term as readily as a constant;
  `prove_advance` gives every free name of the stride and the guard its own byte-bounded
  bitvector and discharges the same goal. The three voices now prove
  `advance(ptr_0040,+($01 + y),nocarry)` beside `advance(ptr_0040,+2,nocarry)`, and
  `lane_updates` is **empty**: `test_no_byte_lane_update_of_any_declared_u16` flips, and
  `test_declared_u16_state_is_updated_wide_except_the_cursors` loses its exception and its
  name. The ratchets fall **339 -> 324 lines / 820 -> 773 nodes**.
  (3) **The rule is admitted anyway, because the idiom is real.** `SEC; ADC <stride>` is a
  wide add the catalog's `adc-chain` row had no spelling for: `carry_fuse_in` states
  `(ah + (carry(al,bl) | carry(al+bl, 1)))<<8 | (al+bl+1) == pk(ah,al) + zext(bl) + 1`, Z3-proved
  with the other 91 (a carry-in of one bounds the sum at `$1FF`, so the OR is the whole
  carry). `tests/test_eqlift_converge.py` carries it as the row's **second spelling group** —
  two values cannot be one class, so a catalog row now takes a list of groups — and the row
  gate stays "every case id is a catalog row".
  (4) **The gates.** `gate_sweep` at full Songlengths **624 / 624 / 624 clean**. Suite
  **2,732 passed / 490 skipped / 29 xfailed**, oracle 16. Emit identity moves on a §4 review
  of **three** tunes (−88 bytes) to
  `64f763d93ebf3b1edcc11310b3ef6be6a3818dad517026b8f1874125827a7b2b`, 624 tunes, 0 refused,
  **28,258,539 bytes**: `ATOO/American` and `Butler_Paul/Ace_of_Aces` are strict wins — the
  byte-lane advance becomes `d3:2 = (ptr_00FB_lo:2 + $0001 + zext2(pos1)):2` and in
  `Ace_of_Aces` the two half stores fuse to one word column — and
  `Feil_Georg/A_Spaceman_Came_Travelling` is −1 byte where the lanes go to table rows rather
  than to a pair, so the word add stands beside them. Splice sweep against its control:
  **zero new, three fixed**, parse and fixpoint 624/624, 210,034 sites proved, −4,529 lines
  with no tune larger.
- **2026-08-11 — stage 4, landing 5: the 25-exemplar witness sweep, and the refusal ledger
  goes to one.** #185 closed the three refusals the plan had named; running the witness
  against the exemplar set is what found the rest, and every one of those but the last turned
  out to be a capability the witness owed rather than a limit of the machine.
  (1) **The measurement is a tool with its own artifact.** `tools/witness_sweep.py` asks of
  each tune what the round-trip witness claims — the frame program re-emitted as 6502,
  replayed under `PcodeVM`, its per-frame projection differenced against the **walker's**, so
  no evaluator stands anywhere in the chain (`frameprog.iota`'s walker is the reference,
  `framelog.diff` the verdict). `--exemplars` runs the set `tools/exemplars.py` declares. The
  first run witnessed **9 of 25**. The per-landing gate is
  `test_an_exemplar_replays_frame_for_frame_off_the_machine`, which runs the exemplars a
  corpus run resolves at a bounded 200 frames and carries the refused set as a **closed
  ledger**: an exemplar not named in it must witness, and one named in it must refuse for
  exactly that reason, so neither direction drifts silently.
  (2) **`sp` is not a slot the witness needs: it is the machine's own SP** (12 tunes). The
  refusal read "the machine's own SP is not a slot" and had the relation backwards —
  `frameval.Evaluator` takes `self.sp = self.code.slot("sp")`, so the evaluator's stack
  pointer *is* the program's `sp` local and `push`/`ret` move that same register. Reading the
  local is therefore `TSX` and writing it `TXS`, and the two agree byte for byte because
  `vm.run_sub`'s dummy return and `frameval.run_frame`'s `push(0x0001)` leave the frame at
  the same depth. Still named: a **wide** `sp`, and a `for` range over it.
  (3) **A bare local reads unmasked, so the slot's width is every site's width** (1 tune).
  `frameval._expr`'s `loc` case is `r[i]`, so the machine must read the slot and not the
  node; the local widths reach a fixpoint, since a slot widened at one site widens the
  assignments that copy it. `expr._apply` reads `szs` — the *node* width — in `INT_SLESS`,
  `INT_SLESSEQUAL` and `INT_CARRY` alone, so the residual refusal is exactly those.
  (4) **The carry's threshold is the first operand's own width, and that closes two
  refusals at once** (1 tune). `INT_CARRY` was emitted as the C flag out of a `width`-byte
  add, which is `(a + b) > mask(width)` — right only when the operands are equal-width, hence
  the old "carry over operands of unequal width". `_apply` says `(a + b) > mask(szs[0])`, so
  the sum is now kept **one byte wider** than the operands and the verdict is whether any
  byte above `mask(szs[0])` survived. That is width-agnostic, so the unequal-width refusal
  and the narrowing-read-under-`INT_CARRY` refusal both retire on the same mechanism.
  (5) **The emission lays itself across the image's free runs** (3 tunes + one crash).
  `free_span` took the longest run of *zeroed* unowned bytes and refused where the code
  outgrew it — at full Songlengths three exemplars did, and one (`Ghouls_n_Ghosts`) did not
  even refuse: `asm6502` computed a branch displacement against a pc past `$FFFF` while
  `_resolve` had already wrapped the label, so a 3-byte branch measured −65533. The
  displacement is now taken **mod $10000**, which is the machine's own arithmetic and turns
  that crash into the refusal it always was. Then `Asm.cut()` marks a boundary no `rel`
  displacement crosses (statement boundaries, resolver-chain entries, procedure heads), and
  `assemble(spans)` packs the chunks across `free_spans(prog)` — the zeroed run first, then
  every disjoint unowned run it leaves — bridging each hop with one `JMP` and reporting
  `blocks` as `(base, offset, length)`. `_reserved` is what makes a non-zero run admissible:
  a byte outside it is named by no observed read, write, target, leader or declaration.
  (6) **One return convention, which also fixed two divergences the unit tests could not
  see.** #185's raw call left two structural refusals — a callee that is not a procedure
  entry, and one the program also enters by `pcall` — and the reason they existed is that
  `pcall`, `dcall` and `swc` still used the machine's `JSR`, so a callee reached both ways
  had two return conventions. `frameval` has one: **every** call site pushes a pc of the
  program (`synth()`'s stand-in for a `pcall`, the site's own `ret` otherwise) and every
  `ret` reads that word back. `retsolve` now takes the frame's own exit where `TSX` equals
  the saved depth (`frameval`'s `q >= start`), then the site the word names (its shadow
  stack, keyed by a word unique per site), then `rmap` at `w + 1`. `Grid_Runner` and `Athena`
  were **faulting** on the mixed convention; the exemplar sweep, not the unit tests, is what
  saw it.
  (7) **The one refusal left, and why it is final.** `Atmosphere_II` (Electrosound) declares
  the volatile input `osc3`: the evaluator pins `$D41B` through `iota`'s recorded trace, and a
  machine replay has no oscillator to read. Pinning it in the witness would put the
  evaluator's trace back into the trust chain, which is the one thing the witness exists to
  keep out. It is the boundary of the claim rather than an owed landing, and the test states
  it as such. `INT_SCARRY` and a narrowing local read under a signed compare stay refused on
  their own stated mechanisms; no exemplar reaches either.
  (8) **The numbers.** `tools/witness_sweep.py --exemplars` at full Songlengths:
  **24 witnessed / 1 refused, zero diverged**, 525s
  (`out/witness_sweep_s4l5.json`). Suite **2,776 passed / 490 skipped / 29 xfailed**,
  `witness6502` and `asm6502` coverage 100%, `black --check` and `pylint` clean, emit
  identity unmoved at `64f763d9…` (624 tunes, 0 refused, 28,258,539 bytes) since no emitter
  source is touched.
  (9) **What the prototype's round-trip pin still needs.**
  `test_round_trip_witness_is_frame_identical` is **not** flipped here — it looks for
  `reemit_6502`/`emit_6502`/`assemble_6502` on `sml` or `eqlift_mem` taking the prototype's
  *folded node map* and its label map, where `witness6502.emit` takes a
  `frameprog.FrameProgram` and returns a `Witness` whose entry is its own label. The flip
  therefore needs landing 4's fold-layer re-basing to produce the program, and an image whose
  `sml.PLAY` reaches the witness entry, since the pin replays through `sml.run_vm`. It is the
  stage-close agent's, not this landing's.
- **2026-08-11 — the shredder's dispatch pin flips on the pairing law, not on the join it
  was pinned to.** `dispatch_scratch_promotes` was re-pinned by 3d on "the `swc`-label
  extension of the in-edge join". Measured against the artifact the switch now emits, that
  is not its blocker and the extension would not move it.
  (1) **The predicted mechanism is refuted by measurement.** `Footprints.joins` is already
  **False** at the arm's label — no `goto`, `call` or bare `swc` label names it and the
  procedure does not own it — so the label never resets and there is nothing for an in-edge
  join to join. What resets is the `dgoto` **before** the arm table: a computed transfer
  havocs, and the arms are walked from the memory it left.
  (2) **The mechanism that does move it is a law the artifact already states three times.**
  `frameval.seq`, `witness6502._paired` and `frameproc`'s own `nxt != "swg"` readings all
  say the same thing: *an arm table belongs to the computed transfer immediately before it
  and to no other*. So the arms are that transfer's whole successor set, nothing runs
  between the transfer and an arm, and the memory the arms are entered with is the memory
  at the transfer. `render_proc`'s walk reads one statement of lookahead (`_armed`) and
  skips the havoc for a `dgoto`/`igoto` armed by `swg` and a `dcall` armed by `swc`. An
  unarmed computed transfer still havocs, which `test_an_unarmed_computed_transfer_still_havocs`
  holds.
  (3) **A second cause, and it is a bound the reader set threw away.** With the memory
  carried, the arms spelled the value and the store still declared: the row index
  `zext(ctr & $01)` gave `_ir_span` the interval `(0, $FF)` for **every** zero extension,
  so the dispatch table read covered `$1400..$1501` and swallowed the scratch cell at
  `$1460`. A zero extension is the identity on the value, so it carries its operand's own
  interval where that interval is inside the byte this dialect extends; the e-graph's
  `lo`/`hi` merge by join and cannot state this, but a reader interval only has to be
  **sound**, tightening one only proves more disjointness, and the replacement is never
  wider than the width bound it replaces.
  Z3 discharges the weakening it replaces in
  `test_a_zero_extension_carries_its_operand_s_interval`.
  (4) **The pin flips and the family is 21.** `test_dispatch_scratch_promotes` XPASSes and
  its xfail is gone; `test_the_dispatch_arms_do_not_join_the_scratch_write` is re-stated as
  `test_the_dispatch_arms_read_the_value_and_not_the_cell`, which reads the artifact rather
  than the retired second projection.
  (5) **The §4 review: 57 tunes move, 39 smaller, 14 larger, 4 the same size.** −4,353
  against +913, net **−3,440 bytes**. The reductions are one family and one shape: every
  Follin VM player (`Ghouls_n_Ghosts` −400, `Gauntlet_III` −404, `Cosmic_Storm` −368,
  `Chester_Field` −362, ten more at −171..−321) is a dispatch loop whose handlers now read
  the value the dispatcher wrote instead of reading it back out of the cell.
  (6) **The growth is one named shape, and it is a defect this landing exposes rather than
  makes.** `Batman_the_Caped_Crusader` (+468, the largest) is **three lines**: `pcall`
  register arguments that read `cflag, zflag, nflag` now read the flags' own definitions
  spelled out. `pick_ir` prices the extracted candidates but reaches the site's *own* term
  only as a fallback, when nothing else survives `_defined_at` — so any surviving candidate
  wins on no comparison at all, and the havoc the dispatch used to perform was what made
  "nothing else survives" true. Making the own term compete on price was built and
  **measured and rejected**: it prints a base name for a version the site has already
  redefined (`x = (x + $01)` where the artifact says `x = (x0 + $01)`) and re-spells two
  flags through `carry(`. The open item is `pick_ir`'s price/fallback asymmetry and its
  owner is landing 4's extraction order, not this landing.
  (7) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624
  clean**, zero divergences and zero refusals. Suite **2,764 passed / 490 skipped / 28
  xfailed** against the base commit's own 2,760 / 490 / 29 in the same environment — three
  new cases and the flipped pin. `tools/emit_identity.py` records the new baseline
  `0497fa3536b93672c4110cb3e3015d9120740a0081ec7dd090f5d7890966a83c`, 624 tunes, 0 refused,
  **28,255,099 bytes** off `64f763d9…`'s 28,258,539; the review below was re-run after the
  rebase onto #186 and is byte-for-byte the same, so the two landings are orthogonal. `tools/splice_sweep.py` at full Songlengths: **zero new failures**
  (84 bad, unmoved), parse and fixpoint **624 of 624**, **209,938 rewritten sites proved and
  zero unproved**, −4,625 lines with **no tune larger** (575 smaller). `black --check` and
  `pylint` (10.00/10) clean.
- **2026-08-11 — stage 4, landing 4 (part): the role keywords are ON, and the steering
  metric has a number.** Stage 2 built the update-shape reading and stage 4 owed it a
  consumer; `frameprog.program` is now that consumer.
  (1) **The mechanism is one call and one map.** `frameprog._roles` takes
  `roles.census(prog)`'s cell verdicts and `idioms.state_cells(prog)`'s address-to-field
  map, and fills `FrameProgram.roles`, which `sidprog._field_line` has printed since stage
  2 and `sidprog.lark`'s `statedef` has parsed since then. Recognition licenses nothing: a
  cell with no witnessed update, or one carrying an unshaped update, is simply absent and
  its field stays a legal `uN`. `parse` reads `doc.roles` back rather than recomputing, so
  the fixpoint is the text's and holds on **624 of 624**.
  (2) **The metric, recorded by the standing gate.** `tools/splice_sweep.py` now reports
  `fields` and `roled` per tune and in the rollup, so "persistent cells role-named, rising"
  is measured every landing rather than asserted: **13,796 of 18,637 = 74.0%** across the
  corpus. Emitted size is the same run's `d_lines`, unmoved at **−4,529 with no tune
  larger**.
  (3) **The §4 review is the whole diff, and it is one shape.** Every tune moved and every
  moved line is a `state { }` field gaining its keyword — `ptr_005D: cursor u16`,
  `ctr_5501: counter u8`, `idx_550A: accumulator u8` — with **zero** lines changed outside
  the state block (checked as a diff predicate on `Hubbard_Rob/Commando`, and the corpus
  `d_lines` is unmoved, which is the same statement over 624 tunes).
  (4a) **Every pin landing 4 owns now names its mechanism, not a stage.** The five reasons
  were re-pointed in the same change: each says what re-basing does for it — `role_text` is
  `sml.render`'s and still names `a`/`x`/`y` off the pre-rung dialect; `classify_roles`
  reads the folded program where the engine already separates the dispatch subject;
  `follin_arity` recovers the operator set and nothing spells it; the artifact's own
  `state { }` demotion landed at #183 but this block is the prototype's; the extent and
  observed clauses are already in the artifact and the accumulator bound is the one clause
  the engine still owes; and the post-init values are carried as `prov0`/`init_census`
  evidence but not spelled as declaration initializers.
  (4) **What landing 4 still owes, and it is the prototype's half.** The five pins the plan
  names — no architectural register as a value, the typed handler switch, the VM operator
  set, roles carrying their evidence, and declared initial values — are asserted on
  `examples/state_machine_lift.py`'s **own** `render`, not on the artifact, so the engine
  turning role keywords on cannot flip them. They flip when the prototype's fold and render
  layers are re-based onto the artifact, which is the same work the switch named
  (#182 (1)): the prototype's dialect parser and its byte-lane fold layer both predate the
  rung-fused text. That re-basing is landing 4's remaining part and it owns all five.
  (5) **The gates.** `gate_sweep` at full Songlengths **624 / 624 / 624 clean**. Suite
  **2,780 passed / 490 skipped / 28 xfailed** (oracle included), `black`/`pylint` clean. Emit
  identity moves on the review above to
  `018ce8f4e4f1623c8970cef39c6db88ba735fc606abe9820bd4eb891ca956376`, 624 tunes, 0 refused,
  **28,387,498 bytes** (+128,959: the keywords are emitted bytes). Splice sweep against its
  control: **zero new, three fixed**, parse and fixpoint 624/624, 210,034 sites proved.
  *(Corrected 2026-08-11: this run was not rebased onto #186, so neither the aggregate nor the
  splice numbers describe main. Main is `cfab6cff…` / 28,384,068 bytes, −4,625 lines and
  209,938 sites proved — see the record entry at the end of this log.)*
- **2026-08-11 — the shredder's re-measure is empty by construction, and the 21 standing
  pins get owners instead of verdicts.** #177 gave every stage-3 pin one of three
  dispositions measured against `_emit`, then `_spliced`, then the cutover's emitter. The
  switch (#182) made `frameprog.dumps` the unified graph, and that retires the whole
  vocabulary.
  (1) **The re-measurement is structural, so its flip list is empty and cannot be otherwise.**
  `_lift` *is* the artifact, every stage-3 pin is `xfail(strict=True)` on it, and a strict
  mark fails the suite the moment its goal property holds. So
  `test_the_stage_three_pins_are_measured_against_the_artifact` asserted nothing the marks
  did not — it re-ran each pin body under a mock that was a no-op for every pin, since none
  of them reads `_emit` — and it retires. `_MEASURED`'s three verdicts distinguished `emit`
  from the cutover's emitter, a distinction that died with the switch, and they retire with
  it.
  (2) **What a reason still owes is the refusal it was measured at and a live owner.**
  `_OWNERS` is five: rung (f), rung (d), `framestack`, `frameproc`, `datadecl`.
  `test_every_stage_three_pin_names_a_live_owner` keeps the one-owner rule and
  `test_the_owners_partition_the_family_as_the_ledger_records_it` pins the partition, so
  this ledger and the marks cannot drift apart.
  (3) **rung (d) owns thirteen.** Twelve are `framefuse.refusal()` reading **one** surviving
  byte-lane read of the pair as refusing the whole *tune-wide* `u16` declaration
  (`dual_store_*` ×6, `stack_spill_cursor`, `deferred_carry_cursor`, `table_spill_cursor`,
  `unpaired_half_store`, `phase_split_reload`, `shift_divide`); either the declaration
  becomes per-seat or the lane-update spelling `(ptr & $FF00) | zext2(row)` is admitted so a
  lane store is a word store. The thirteenth is `lone_lane`, where the same rung *widens* a
  lone half into a read-modify-write of a write-only register and must not.
  (4) **rung (f) owns four, and the measurement corrects the recorded premise.** `_Writes`
  over each fixture says: `pointer_walk` and `mux_pair` carry **no wild store and no third
  writer** — the only spans that hit the pair are the pair's own byte-lane reload stores at
  `$0002`/`$0003`, which `_hit` does not except and `_writers` does not record as
  definitions. `writethrough` is the one the deref bound reaches: its store address is
  `m_1400[x]:2 + $0002`, a row of a **declared const** lo/hi table whose word set `mem0`
  states, so `frameptr._span` can bound it off the registry — computed, not observed.
  `cursor_save`'s defs are a save-cell word read and a constant, which wants rung (f) to
  take a constant word definition and the held-value closure `ptrcert` already runs.
  (5) **`low_held_cursor` is `framestack`'s, and 3d's prediction is refuted.** The handoff
  named "exactly the deref span landing 1's read closure computes". It is not. A stack hold
  keyed on the page-one interval its own address bits give was **built**, and `closure()`
  admits it — every `st` that may reach the interval writes a cursor value, and `eligible`
  goes True. It is **unsound as stated**: the machine's own return-address push writes page
  one and is no `st`, so the enumerated writer set is incomplete, and the program does hold
  raw calls. The premise it needs is an sp-relative **slot identity** — push and pull at one
  entry-relative offset, a call provably below it — which is exactly what
  `structured.sp_flow`'s join to bot destroys here (`_sp_classes`: `sp_callee`, `sp_read`).
  Not landed, and the owner moves from `ptrcert` to `framestack`.
  (6) **`computed_rows_map` is `datadecl`'s, and the interval rule is its input.**
  `extent_unmappable` fires because the rows the run observes are in no declared datum. The
  `INT_OR` interval is sound and stateable —
  `hi(a|b) <= 2^ceil(lg(max(hi a, hi b) + 1)) - 1`, `lo(a|b) >= max(lo a, lo b)` — but the
  e-graph's `lo`/`hi` merge by **join**, so a rule can only widen and the tightening has to
  sit where `_lattice`/`_ir_span` do. Either way the consumer that turns a span into a
  *declaration* is `datadecl`'s `via:` discovery, so the rule is the input and discovery is
  the mechanism.
  (7) **`frameproc` owns two.** `g2_store`: `_lattice` already states `($00A5, $01A4)` where
  `addr_bits` states ⊤, and `eqlift_mem._wr_span` already records that each is sound alone
  so the tighter of the two is. `_lattice` is pure over pass-1 expressions, so it moves to
  `frameproc` and a **reach** reading takes the min — `addr_bits` itself may not, because
  its `INT_OR` recursion needs masks and a magnitude bound is unsound there.
  `sp_scratch_floor`: the cell survives beside a raw `call` the chain havocs at, so what
  holds it is the promotion `frameproc.slot_reader` refuses, not `addr_floor`.
  (8) **The gates.** Tests and documentation only -- no emitter source is touched, so no
  artifact can move -- and the suite is unchanged at the base commit's own counts, with
  `black --check` and `pylint` (10.00/10) clean.
- **2026-08-11 — the stage-4 record: the metrics are re-measured, the headline gets a number,
  and every standing pin gets a landing.** #189 gave the shredder's family owners instead of
  verdicts; this closes the record around them. A record is a measurement, so it was measured
  rather than carried forward — and three of the recorded numbers did not survive that.
  (1) **The standing emit-identity baseline never described main.**
  `--expect 018ce8f4…` **fails** on this commit, which emits
  `cfab6cfffb137904a1be106d5b2608cd9c19fe682b3f43369b9c9f49c41fb672` over 624 tunes, 0
  refused, **28,384,068 bytes**. `018ce8f4…` is `out/emit_roles3.json`'s: #187 measured it on
  a tree that had not been rebased onto #186 and recorded it as the baseline. The two differ
  on **57 tunes — 39 smaller, 14 larger, 4 the same size, −4,353 against +923, net −3,430
  bytes** — which is #186's own §4 review (57 tunes, 39/14/4, −4,353 against +913) composed
  with #187's keyword diff, the +10 being the keywords a moved state block spells differently.
  So every moving byte was reviewed when it moved; what was never reviewed was the arithmetic,
  and `--expect` is the gate that should have caught it at #187 and was not run.
  (2) **Two splice metrics were two landings stale.** The position quoted **−4,529** emitted
  lines and **210,034** proved sites as landing 4's; both are `out/splice_s4l3b.json`'s, from
  before #186. A fresh run says **−4,625 lines with no tune larger** (575 smaller) and
  **209,938 sites proved, zero unproved** — #186's own numbers, so nothing regressed and the
  record was simply behind. 84 bad against the control, **zero new and zero fixed**; parse and
  fixpoint **624 of 624**; `fields`/`roled` unmoved at **13,796 of 18,637 (74.0%)**.
  (3) **The headline is measurable and is now measured, on the artifact.** "Tunes wearing zero
  machine shapes" died with the phased plan at 0 of 624 and has been asserted ever since. The
  reading it needs is the prototype pin's own predicate — architectural registers named as
  values, hex and comments stripped — so `splice_sweep` takes it over the emitted text and
  reports `arch`/`zero_arch` per tune and in the rollup, the way #187 made `roled` a measured
  quantity instead of a claim. It stands at **2 of 624**, 183,648 register tokens in all.
  `tools/lift_residue.py`, which owned the metric before, cannot answer it: it walks
  `prog.procs`, the walker's projection upstream of extraction, so it has not read the
  artifact since the switch. It stands at **0 of 624** zero-residue tunes and a census sum of
  **30,910** against the pivot's 30,854 — the same reading it always gave, about a different
  object.
  (4) **The parse-and-evaluate gap gets an owner, and the census splits it into three
  mechanism families rather than one.** 84 of 624 is **25 evaluation faults, 9 lint, 50
  divergences**, and the three sets are **disjoint**. The lint nine are one thing —
  `local 'a'` ×7, `'nflag'`, `'zflag'`, all "used before definition" — which is root
  extraction's rooting law and the same mechanism as `_share_once` across roots, so landing 4
  is where its bisection starts. The faults are all `FrameFault`: 24 `unobserved $XXXX
  reached` and one switch call target outside the observed set, so the owner is the
  `swg`/`swc` arm-table headers, where the text can lose a guard's observed set. The
  divergences are not 50 findings: **15 are one shape** — frame 0, section `filter`, position
  1, `($16, $08)` against `($16, $10)`, the cutoff hi lane off by exactly one shift — which is
  the declared-pair spelling meeting the missing declaration initializer at `$D415`/`$D416`,
  and the cheapest bisection in the gap; the other 35 are one tune each. The gap's owner is
  the stage close, where it is taken or refused by name.
  (5) **#180's law is executable on both families now, and it found one straggler.**
  `test_round_trip_witness_is_frame_identical`'s reason was "stage 4: the round-trip witness"
  — a live stage, but no owner and no mechanism. It names both now, off #188 (9)'s recorded
  requirements: `witness6502.emit` takes a `frameprog.FrameProgram` and returns a `Witness`,
  so the pin waits on a fold layer that hands it one plus an image whose `sml.PLAY` reaches
  the witness entry, since the replay is `sml.run_vm`'s.
  `test_every_pin_names_the_landing_that_flips_it` pins all seven prototype reasons to landing
  4 the way `test_the_owners_partition_the_family_as_the_ledger_records_it` pins the
  shredder's twenty-one, so neither ledger can drift from this document.
  (6) **Landing 4's trunk flips two pins, not seven, and the scoping is what says so.**
  The re-basing path is already proved on the prototype's own image —
  `tests/test_witness6502.py::test_the_example_artifact_replays_frame_for_frame` runs
  `sml.build_image()` -> `structured.decompile` -> `frameprog.program` ->
  `witness6502.emit(p).frames(n)` and is green — so the scratch-demotion pin and the witness
  pin ride on it. The other five do not.
  `no_architectural_register_survives_as_a_value` is the clearest: measured on
  `Hubbard_Rob/Commando` the artifact carries **151 register tokens** (`x` 73, `a` 38, `y` 31,
  `cflag` 7, `vflag` 2), of which exactly one is a `for` header, so re-basing makes the pin
  read the artifact and the residue is what flips it. `roles_carry_their_evidence` wants a
  clause the grammar has not got (`mask`/`bound`; `roles._mask_bound` returns the inner term
  and discards the constant) and an `in` clause `ptrlift.apply_rung` only emits when an extents
  row is passed. `init_lifts_to_declared_initial_values` wants a `statedef` initializer
  production that does not exist and a value `prov0` does not carry (it holds the origin
  address). `vm_family_operator_set_is_emitted` wants an `operators { }` production, a symbolic
  name per opcode, and a `writes` set `follin_arity` does not compute. Recording this before
  the landing is the point of the record: the ledger arithmetic is unchanged, the schedule is
  not.
  (7) **The two extraction-order items were both recorded imprecisely, and the code says so.**
  "`_share_once` across roots" is not the defect: its scan is already over the whole procedure
  tree. The limit is that `by_name` holds `asg` nodes alone, so a **store has no name** and the
  `Cage_Match` PLP word is re-extracted per reading site — the artifact repeats one
  156-character rebuild four times in a single arm, 1,092 duplicated characters in 22,298
  bytes. Sharing it needs a synthesized definition, which the immutable render tree, the closed
  `terms`/`chosen`/`id(nd)` structure and the §6 proof channel all refuse; the two validity
  predicates it would need already exist. And `pick_ir`'s fallback is worse than "wins on no
  comparison": it is admitted with **no `_defined_at` and no chain check**, which is precisely
  the 9-tune lint family in (4) — so that family is `pick_ir`'s, not `_share_once`'s, and it is
  landing 4's cheapest close. The fix remains a substitution over the own term's stale leaves,
  not a price change.
  (8) **The plan is ordered and it ends at zero.** Landing 4 takes seven (28 → 21) across the
  trunk and the four parallel parts (6) separates; the stage close takes none and gates on
  three named items; then the engine ledger
  in six landings — rung (d)'s pair premise twelve (21 → 9), its widening guard one (9 → 8),
  `framestack`'s slot identity one (8 → 7), `frameproc`'s bit analyses two (7 → 5), rung (f)'s
  writer set four (5 → 1), `datadecl`'s `via:` discovery one (1 → 0). The only measured
  ordering constraint is `sp_scratch_floor` depending on `framestack`'s resume-pc reading; the
  rest are disjoint in files and may land in parallel.
  (9) **The gates.** Documentation, one tool metric and two test edits — no emitter source is
  touched, so no artifact can move, and the corpus numbers above are the measurement of that.
  `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**, zero divergences
  and zero refusals. Suite **2,781 passed / 490 skipped / 28 xfailed** (oracle included),
  one new case and no pin flipped. `black --check` and `pylint` (10.00/10) clean.
- **2026-08-11 — stage 4, landing 4 (part): the round-trip witness closes, and it needed less
  than the record predicted.** The plan had this pin waiting on the prototype's fold and
  render layers; measuring it first showed it waits on neither.
  (1) **What it actually needed.** `witness6502.emit` takes a `frameprog.FrameProgram`, so the
  only thing missing was one: `pipeline()` now carries `art["prog"] = frameprog.program(model)`
  beside its own fold tree. The program refuses nothing on this image — `inputs == []` and
  `extents == {}`, so neither of `emit`'s program-level refusals fires, and the extents
  `pipeline` computes go to `eqlift_mem.emit`, a different consumer taking a different shape.
  (2) **The entry hop, which is the whole of the rest.** `Witness.entry` is a fresh label in a
  free span and `sml.run_vm` enters at `PLAY`, so `sml.reemit_6502` writes a three-byte `JMP`
  at `PLAY` and returns the image under the name `REEMIT` already looked for. Nothing else in
  the image moves.
  (3) **The pin is strengthened, not weakened, by the re-statement.** It read the prototype's
  own folded node map; it now reads `art["prog"]`, so the thing assembled to 6502 and replayed
  is the **artifact** rather than a second projection of it. No evaluator is in the trust
  chain either way — the comparison is `framelog.canonical` against the original routine's own
  VM projection.
  (4) **The ledger.** 28 → **27**: the prototype's family is six, all landing 4's, and
  `test_every_pin_names_the_landing_that_flips_it` moves to six with it, so the count cannot
  drift from this document.
  (5) **A correction to this record, one commit old: the scratch pin is not the re-basing's
  either, so the trunk flips no pins at all.** The entry above had
  `test_state_block_holds_no_scratch` riding on the re-basing because #183's demotion is
  wired end to end. Measuring it says otherwise: `prog.demoted` is **empty** on the
  prototype's program, and empty *by construction* — `eqlift_mem._unread` demotes a store only
  when no read anywhere in the artifact names the byte, and a cell written before it is read
  has readers by definition. The pin's criterion is frame-boundary liveness-in (order-sensitive,
  per-frame); #183's is unobservability (order-insensitive, artifact-wide). They are different
  questions and no plumbing joins them; what the pin owes is a **second demotion notion**, a
  per-frame kill nothing computes today. The measurement also dated the pin: thirteen cells
  fail, not the six its docstring named — the image layout moved those — and the thirteen are
  the three SMC operand pairs (`m_10AD`, `m_11F8`, `m_1343`, fused `u16` by rung (d)) plus
  seven zero-page cells the artifact declares `parameter`, which are a callee's per-frame
  arguments. Both the reason and the docstring now say this. So landing 4's trunk — the
  re-basing that retires `emit`/`emit_mem` — flips **nothing**; it is a cleanup that removes
  the second projection, and all six remaining pins are engine capabilities.
  (6) **The gates.** `deity_informant/` is untouched — the diff is `examples/` and `tests/`
  only — so no artifact can move and the corpus sweeps are unchanged by construction. Suite
  **2,782 passed / 490 skipped / 27 xfailed** (oracle included) against the base commit's
  2,781 / 490 / 28. `black --check` and `pylint` (10.00/10) clean.
- **2026-08-11 — stage 4, landing 4 (part): spellability is denotation, not definition —
  and the nine-tune lint family was never `pick_ir`'s.** #190 (7) recorded the extraction
  fallback as printing "a base name for a version the site has already redefined", to be
  repaired by a substitution over the own term's stale leaves, and (4) attributed the lint
  nine to it. Both were measured before either was built, and neither survived; what the
  measurement found instead is a bigger item, and it is landing 4's own headline.
  (1) **The own term has no stale leaf to substitute — corpus-wide, not on a sample.**
  Instrumented over all 624 cached artifacts: **214,584** extraction sites, **89,920**
  own-term admissions (41.9% of sites) carrying **100,272** local leaves, **84,909** of them
  outside the site's `live()` — and **0** that the site's own `env` does not still denote.
  That is structural, not lucky: every leaf of the own term came from `conv` reading that
  site's `env`, so the base denotes it by construction and the missing `_defined_at` cannot
  admit a stale name. The other two unguarded paths measure the same way: the last-resort
  `min(cands)` fires **0** times, and the **8,268** own-term admissions whose chain price is
  `None` are the site's own volatile and `mem0` reads, which `_Chain` never steps — not a
  chain gap. The proposed substitution is therefore **withdrawn as an empty rewrite**.
  (2) **The nine lint tunes are inherited from the walker's projection, and the control
  already said so.** `out/splice_base.json` and `out/splice_base_s4l3b.json` — the
  `--baseline` runs, which render `frameproc.render_lines`' own text and reach neither the
  e-graph nor `pick_ir` — carry **the same nine tunes with the same nine messages**. And
  `frameprog.check_locals(prog.procs)` fails on the walker's projection before any rendering,
  cached and cold (`DI_SWEEP_CACHE=0`) alike. Their two mechanisms, read per tune: **eight**
  read a register after an opaque `call` whose callee declares no returns —
  `info.rets` is `_by_reg(returns[e]) if callable_[e] else []` and every measured tune has
  **zero** procedures with returns, while `frameval._Code` is explicit that "locals are
  program-wide" across a call, so the text is sound and the *signature* is what is silent;
  **one** (`International_Karate`, `sub_AE0C(sp)`) reads `a` with no call anywhere on the
  path — a live-in the parameter list omits (`_Info.livein` → `_by_reg`). Both are
  `frameproc` signature-truth defects, so the family belongs with the promotion items
  (plan 5/6), not with extraction, and the ledger's "84 → ~75" is withdrawn with it.
  (3) **The asymmetry that is real is `live()`'s, and it is what the 42% was.** `live()`
  admitted a version only when an `asg` had rendered its definition, so every version a
  *boundary* produced — a call's havoc, a join's, a label's — was unspellable and every
  candidate naming one was refused. Measured at the fallback sites: of **264,572** candidates,
  **49,937** are refused for naming the site's own name and **200,939** by `_defined_at` —
  and **181,878 of those 200,939 (90.5%)** name only versions the base still denotes, with
  **175,068** of them also priced. The e-graph was being thrown away at nearly half the sites
  for spellings that were correct. The predicate is **denotation**: a local renders as its
  base name, so the base spells whatever version it holds, whether an `asg` rendered it or a
  boundary produced it. `avail` was `live()`'s only reader, so the whole availability
  bookkeeping — the adds, the arm and loop intersections, the label clear — leaves with it.
  (4) **What it moved.** Fallbacks **89,920 → 14,650** (−84%). Emitted text **802,035 →
  799,281 lines, −2,754**, across **527 tunes**. §6 re-proves every site: **207,184 proved,
  zero unproved** (fewer sites because fewer nodes survive). The lint set is **the same nine,
  none new and none fixed**, which is the check that the boundary versions the change makes
  spellable do not become locals a reader cannot follow. The vocabulary case is the second
  direction of `test_a_stale_local_version_never_spells_a_site`: after a `call` havocs `a`,
  `(a + $01 - $01)` was the artifact's own spelling and is now `a`.
  (5) **The §4 review of the corpus diff.** `emit_identity` moves to
  `05c3a08ab518e6600bb39791d09e8680090ffa190d2990c7b336bf5a4d747352`, **28,310,783 bytes**,
  **594 tunes moved — 572 smaller, 19 larger, 3 the same size, −74,509 against +1,224, net
  −73,285**. Every moving line is an admitted rule reaching a site that used to fall back,
  and the shapes are five: the sign test folds (`((x & $80) != $00)` → `(x <s $00)`), a
  comparison takes its normal form (`ifnot ($90 <= a)` → `if (a < $90)`, `a == ctr[x]` →
  `ctr[x] == a`), shifts compose (`((x << $01) << $01)` → `(x << $02)`), an add or an or
  takes the representative operand order, and a copy the site can now name forwards so its
  definition line goes (which is where the 572 shrinks are). **The 19 growths are one shape
  and it is §4's own price order**: with the value spelling admitted, a memory read costs
  more than it, so `w2 = m_01FD` becomes the status-word rebuild spelled at the reading site
  — `Cage_Match` (+293) is the largest and is exactly the multi-reader memory forward the
  position paragraph already owns, which needs a synthesized definition to collapse. The
  standing item gets a number from this: it now costs +1,224 bytes over 19 tunes.
  (6) **It closes thirteen of the parse-and-evaluate gap, and the family is not the one
  the ledger predicted.** `splice_sweep` against `out/splice_s4pr1.json`: **84 bad → 71,
  zero new and thirteen fixed**, and all thirteen are **divergences** (50 → 37), not the nine
  lint. A site that fell back spelled its own term, and where that term read a cell the
  parsed text re-read the cell at a position the evaluator disagreed about; spelling the
  value the site holds removes the re-read. Emitted size against the control moves
  **−4,625 → −7,379 lines**, 585 tunes smaller and **none larger**; parse and fixpoint stay
  **624 of 624**; **207,184 sites proved, zero unproved**. `fields`/`roled` are unmoved at
  **13,796 of 18,637 (74.0%)**. The headline is honest in both directions: `zero_arch` stays
  at **2 of 624** and `arch` rises **183,648 → 184,367**, because a site that used to print
  an unrewritten tree now prints the register the value is in — fewer lines, one more token.
  `zero_arch` moves with the role pins, not with this.
  (7) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624
  clean**, zero divergences and zero refusals (`out/gate_s4l4c.json`). `emit_identity`
  **624 tunes, 0 refused**, the new baseline in (5) (`out/emit_s4l4c.json`); the recorded
  `--expect cfab6cff…` fails on purpose and every moving byte is reviewed above.
  `splice_sweep` as in (6) (`out/splice_s4l4c.json`). Suite **2,780 passed / 490 skipped /
  27 xfailed** (oracle excluded; one new case, no pin flipped). `black --check` and `pylint`
  clean at the tree's standing 9.95.
- **2026-08-11 — `framestack`, the slot identity: a reader holds the slot and a writer
  refuses it.** #189 refuted 3d's prediction and caught its own hold unsound; this is the
  premise the record demanded in its place, and it is sp-relative throughout.
  (1) **What survives the join.** A slot is `(epoch, entry-relative offset)`. `_Marks` and
  `_slot_at` name it without ever concretizing `sp`, which is exactly what
  `structured.sp_flow`'s join to bot destroys: a callee entered at two depths keeps its
  slot identity where `concretize_stack` can fold no address to a cell.
  (2) **The reader/writer split, which is the whole flip.** `_SpSlot._probe` refused *any*
  access that may touch a live slot. A read moves no value, so it may not refuse the
  identity — only the removal of the store. The walk now records `impure` for a reader and
  `why` for a writer, and a slot with readers is **held**: the store stays and the pulls
  read the local it defines. `low_held_cursor`'s `(ptr),y` deref between push and pull is
  exactly such a reader, so `ptrcert._classify` reads the restore as `save_restore` off
  cursor `$0002` instead of `low_held`, and the web is eligible.
  (3) **The machine's own push is in the writer set by construction — by refusing to span
  a call, and the exact pricing is REFUTED.** #189's unsoundness was an enumeration of `st`
  statements alone. The rung answers it structurally: a call closes the epoch, so no slot is
  ever live across one and no return-address push of one can reach a slot. The record's
  "a call provably below it" was then **built and measured**: an epoch spans a call whose
  callee is stack-silent (its whole call tree names no page-one cell, and it balances) where
  the live slot stands strictly above the call's displacement. It moved 2 of 624 tunes wrong
  and `gate_sweep` caught both.
  `Ultima_III-Exodus` (v2.lww, frame 0, `[15, 10]` against `[15, 12]`) was the silence
  premise itself: a callee may be entered or left by a `goto`/`label` edge the walk does not
  carry, so its accesses are not all the accesses that run. Widening the open set to
  `goto`/`label` made that tune clean.
  `Allt_under_himmelens_faeste` (`FrameFault: unobserved $0D0F reached`) survived that fix
  and is **not framestack's**: with three `y` spills around `sub_0B65` promoted away, the
  artifact loses `y = (y + $01)` inside **`sub_09A0`** — a procedure the rung never touched,
  whose own header still reads `sub_09A0(y) -> y`. The rendered body drops a definition its
  header returns, so a register the caller's spill used to carry through memory is now
  carried in a register whose defining statement root extraction retires. That is the two
  `_Info`s disagreeing (`frameproc.repolish`'s for the header, `eqlift_mem.render_ctx`'s for
  the body), and it is a live emitter defect the spanning merely exposed. So the spanning is
  not landed and the pricing stays structural; whoever takes it must fix that first.
  (4) **The corpus, §4-reviewed.** `tools/emit_identity.py`: 624 tunes, 0 refused,
  28,310,783 → **28,306,161 bytes**, `05c3a08a…` →
  **`f4d5958e0a18ab4c981634879c3e2e85bb4c959fc7c6951624e5ec94a0793635`** (the new baseline),
  **64 of 624 moved** — 47 smaller (−4,866), 17 larger (+244), none the same size.
  `Cyberbrain/Arpeggio` (−294) is the shape end to end: the address temporary and the copy
  go (`t18:2 = (zext2(sp) | $0100):2` / `mem[t18:2] = w39` / `w41 = w39` become
  `s0 = …` / `mem[(zext2(sp) | $0100):2] = s0` / `ptr_00F4_lo = s0`), and with no page-one
  definition left on the web `ptr_00EC_lo: cursor u8` + `ptr_00EC_hi: parameter u8` become
  **one `ptr_00EC: cursor u16`** whose reload is one word row.
  The larger side is not a price either: on `Amaze/Foolish_Maniacs` (+28) the held value lets
  rung (d) pair two SID writes into one `hi-first sid.v1.freq_lo[y]:2 = …` word store and the
  two tables gain their declared `lo`/`hi` roles — 28 bytes for a word store and a declared
  pair.
  (5) **What did not fall out: `ret_live`.** The plan expected the resume-pc reading from
  this analysis. It does not come from it, and the reason is structural: `_below_sp` refuses
  every slot at `k > 0` *because* that is the caller's live stack — the return address — so
  this reading never names it. #177's mechanism stands unchanged as the owed item: the
  live-out of a slot-rewriting callee is the live-in at the pcs its slot may name, per site,
  `call site + inline-data length`.
  (6) **The ledger, and the gates.** 21 → **20**, and `framestack` owns none;
  `test_the_owners_partition_the_family_as_the_ledger_records_it` moves with it.
  `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**, zero divergences
  and zero refusals. `splice_sweep --against` the recorded artifact: **71 bad, zero new and
  zero fixed**, −7,268 lines with no tune larger (585 smaller), **207,073 sites proved, zero
  unproved**, `fields`/`roled` 13,793 of 18,634. Suite **2,786 passed / 490 skipped / 26
  xfailed** (oracle included) against the base commit's 2,782 / 490 / 27. `black --check` and
  `pylint` (10.00/10) clean.
- **2026-08-11 — `frameproc`, the reach reading: the tighter of the lattice and the bits,
  and the lint eight are a call's own definitions.** Two readings of the same question stood
  apart; this joins them where each is sound, and re-owns the pin the join does not flip.
  (1) **The reach reading.** `eqlift_mem._lattice` is pure over pass-1 expressions and states
  a magnitude bound where `addr_bits` states ⊤ — `(zext2(y) + $A5)` is `($00A5, $01A4)` where
  the bits are every bit the width has. `_wr_span` already recorded that each is sound alone,
  so the tighter is; only the store chain read them together. The lattice moves to
  `frameproc.lattice`, `frameproc.addr_reach` is the min, and `store_reach` takes it, so a store
  no base/index form names carries the bound wherever it is asked about — rungs (d) and (f),
  the disturbance checks and the emitter's own chain. `addr_bits` still may not take the min
  itself: its `INT_OR` recursion composes masks, under which a magnitude bound is unsound.
  `eqlift_mem._lattice` and `_wr_span` are that reading rebound, and `g2_store` flips.
  (2) **`sp_scratch_floor` is measured and re-owned, and the recorded ordering is refuted.**
  The plan had it waiting on (5)'s resume-pc reading. It waits on neither that nor
  `addr_floor` nor the memory chain: `_join_mem` **already keeps** the cell across all three
  `pcall`s (the callee's whole footprint is page one, measured), and `slot_reader` refuses
  nothing there. What holds it is `eqlift_mem.render_block`'s wall retiring *every* local at
  a call, so the value spelling names a version no longer available and extraction falls back
  to the cell. Bounding the havoc to the callee's may-set was built and **measured to flip
  the pin**; the reading is `frameproc._Info.may` and the consumer is the wall, so the pin is
  `eqlift_mem`'s, its landing the emitter's headline metric and not this one's.
  (3) **The lint nine split eight and one, and the eight are free.** #193 handed this over as
  frameproc signature truth. Measured, it is two mechanisms.
  The **eight** read a register after a raw `call` whose callee declares no returns — and the
  callee **must**-defines it (`50_Shades_of_Gradius`: `call $EDE0` before `sid.reg[x] = a`,
  with `a` in `must[$EDE0]`). The artifact is right and the *checker* was not: a call that
  declares no returns still defines what its callee must define, because the machine runs it.
  `frameproc.must_defines` is that reading and `frameprog.check_locals` takes it, so no
  emitted byte moves and the family closes.
  The **one** is not a live-in the analysis misses: a fresh `_Info` over the *finished*
  program computes `livein[$AE0C] = {a, sp}` and `params = [a, sp]`, while the program's own
  header reads `sub_AE0C(sp)`. `repolish` freezes signatures by design ("already spelled into
  every `pcall`"), so a rung that makes a register read appear after the build leaves the
  header stale. Its landing is a signature refresh that moves every `pcall`'s arguments with
  the headers, and it is scheduled with `eqlift_mem`'s wall, not taken here.
  (4) **The ledger and the gates.** 20 → **19**, `frameproc` owns none, and `_OWNERS` grows a
  sixth entry so `sp_scratch_floor`'s reason names the live owner the measurement gave it.
  `emit_identity` is **byte-identical** to the base commit — 624 tunes, 0 refused,
  **28,306,161 bytes**, `f4d5958e…` unmoved, **0 of 624 moved** — which is the measurement of
  (1) and (3) together: the tighter bound changes no emitted decision the chain's own
  `_wr_span` did not already take, and a checker moves no bytes by construction.
  `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**, zero divergences
  and zero refusals. `splice_sweep --against` the recorded artifact: **63 bad against 71,
  eight fixed and zero new**, the lint family **9 → 1**, −7,268 lines with no tune larger
  (585 smaller), **207,073 sites proved, zero unproved**. Suite **2,788 passed / 490 skipped /
  25 xfailed** (oracle included). `black --check` and `pylint` (10.00/10, exit 0) clean.
- **2026-08-11 — rung (d)'s pair premise becomes per access site, and twelve pins land.**
  `framefuse.refusal()` read **one** surviving byte-lane access of a pair as refusing that
  pair's whole tune-wide `u16` declaration. The ledger's item 3 offered two ways out — a
  per-seat declaration, or admitting the lane-update spelling — and the landing takes the
  second, which makes the first unnecessary: the declaration stays tune-wide and it is the
  *access* that moves.
  (1) **The spelling, and the obligation it carries.** A lone half store becomes the lane
  update `(w & $FF00) | zext2(v)` — `framefuse._widen`, the rewrite the SID half has run
  since §7.2 — and a lone half read becomes that lane's own trunc
  (`frameproc.trunc_lo`/`trunc_hi`, rung (d2)'s spelling). Both are identities on the pair's
  encoding, and the obligation is §4(d2)'s own law stated over the update: `trunc(lane) = v`
  and `hi(lane) = hi(w)` with the two duals, **Z3-proved over QF_BV** on `eqlift._Z3Alg` —
  the algebra `verify_rules` proves the admitted rules on — in
  `test_the_lane_spelling_is_the_concatenated_value_law`. No idiom is named and no e-graph
  rule is admitted: the rung writes the identity, the law says it is one.
  (2) **The write-order hazard stops refusing too, on the SID half's own reading.** Where the
  second value may read the first cell the halves do not *pack*; each lane widens on its own,
  which writes them in the program's order and reads the lo the first one wrote. That was
  already the SID branch; it is now the only branch.
  (3) **What replaces the proxy is the fact it stood in for: `_page_fixed`.** The one shape
  the old rule was right about is `inpage_advance` — `lo = (lo+k) mod 256` with `hi` a page
  selector, which wraps where a word would carry. Its docstring **assumed** "hi is a constant
  page selector"; the rung now proves it: every store that may reach the hi lane writes the
  byte `mem0` holds, and no `call`/`dcall`/`swc`/`unobs` wall may hide one. Under such a hi
  lane a lo lane advanced *in place* (`_lane_advance`, the value reading its own lane through
  the locals staging it) refuses, and nothing else does. That parts `inpage_advance` from
  `deferred_carry_cursor`, whose unobserved carry arm is exactly the wall, and from
  `phase_split_reload`, whose hi lane is fixed but whose lo lane is replaced from a table
  rather than advanced. The two other refusals are an indexed half store the pair cannot
  place and a pair with no word access at all. **Every refusal left is strictly narrower than
  the rule it replaces, so no pair that fused before can refuse now** — the corpus movement is
  one-way by construction.
  (4) **The rung publishes the readers for what it spells, and three consumers use them.**
  `framefuse.lane_of` is `_widen`'s dual and `framefuse.unlane` takes a lane trunc back to the
  cell it names. `ptrcert._classify` asks both before classifying a definition, which keeps
  `low_held` (a cursor held through page one) and the byte-lane `block_read` the facts they
  were — without it the lane update reads as an opaque computed word and `low_held_cursor`
  goes *eligible*, which would be unsound. `framestack._push_val` reads a lane trunc as the
  pure push it is, and the RTS trick then lifts **better** than before: the two truncs of one
  word repack to the word, so `jsr_inline_skip_two_sites` emits
  `goto ((ptr_0004:2 + $0001):2)` where it emitted a rebuilt pack of two lane locals.
  (5) **The twelve pins, and two others that moved with them.** Flipped: `dual_store_advance`,
  `dual_store_pair_first`, `dual_store_via_regs`, `dual_store_hi_first`, `dual_store_computed`,
  `dual_store_lo_only`, `stack_spill_cursor`, `deferred_carry_cursor`, `table_spill_cursor`,
  `unpaired_half_store`, `phase_split_reload`, `shift_divide` — the shredder ledger goes
  **19 → 7** (rung (f) 4, rung (d) 1, `datadecl` 1, `eqlift_mem` 1) and rung (d) owns one,
  `lone_lane`. Beside them the landing pays part of rung
  (f)'s recorded premise: #189 (4) said `pointer_walk` and `mux_pair` refuse because the pair's
  own byte-lane reload row reads as a third writer, and that row is a width-2 lane update now,
  so both moved off the writer-set refusal onto premise 1 (a definition that is not a
  partner-table entry read). Neither pin flips — the reasons say the new premise — and
  `ptrcert` reports one word reload where it reported two byte ones.
  (6) **Four rules are admitted with it, and they are the law itself.** `lane_lo`, `lane_hi`,
  `lane_lo_keeps_hi` and `lane_hi_keeps_lo` in `eqlift.RULES` state exactly (1)'s four
  equalities as rewrites, so `verify_rules` proves them where every other rule is proved and
  the graph can read a lane update back. They fire only on the spelling this landing writes:
  measured, they take **1,308 bytes** off the corpus and, where a lane advances twice, they
  retire the intermediate local the fold otherwise mints (`ptr2 = trunc1(ptr:2)` before
  `ptr:2 = (… | zext2((ptr2 + $01)))` becomes one statement). The residual growth wants
  store-to-load forwarding of the word store into the following lane read, which
  `eqlift_mem`'s memory analysis does not do here; that is named, not owed by this landing.
  (7) **A pre-existing round-trip defect surfaced and is fixed: an aliased partner table.**
  `grammar._pair_hi` matched a declared lo/hi partner table by comparing the name the text
  writes against the name the declaration writes, which are different names whenever the
  table carries an alias — so `ctr_172E[x]:2` parsed back as **two adjacent bytes** instead of
  the two declared columns. The emitter has printed that spelling since #173's `_PAIRS`
  registry; the landing's new packs made 23 tunes emit it over aliased tables, and every one
  of them failed `splice_sweep`'s parse-and-evaluate gate. Both sides now resolve the alias
  before they are compared. Ghouls-style unaliased tables were always right, which is why the
  defect stood — and it was carrying most of the parse-and-evaluate gap: **`splice_sweep`
  goes 63 bad → 2, zero new and 61 fixed**, the 25 evaluation faults all clearing and the 37
  divergences going to 1, leaving one lint and one divergence. Parse, fixpoint, sites and
  larger stay at zero.
  (8) **The role metric moves with the fusion, and `roles` reads the new spelling.**
  `roles.updates` reads its term back through `framefuse.unlane`, so a lane step is the step
  it always was rather than an unshaped update; without it 153 more fields go un-roled.
  Measured: **18,634 fields → 18,109 and 13,793 role-named → 13,234, 74.0% → 73.1%**. The
  movement is the fusion itself — 525 of the 559 lost role entries are fields that no longer
  exist, because a fused pair is one field and not two, and a ratio above half falls when a
  named field leaves both sides of it. **At most 34 fields lose a role they had**, and that
  residue is rung (d)'s own: a lane update whose shape `roles.shape` still cannot read.
  The headline is unmoved — **`zero_arch` 2 of 624**, as landing 4's entry predicts, since
  what leaves a register spelled is the role pins and extraction order — but the census under
  it falls, **184,265 architectural register tokens → 183,682**.
  (9) **The corpus, §4-reviewed.** `emit_identity`: **624 tunes, 0 refused, 28,271,645 bytes**,
  aggregate `8410d058e6b844b04cbd4cafa0c5afc3f8c79d50558ea41dd54e49bc810f7c78`, against
  `f4d5958e…`/28,306,161 at the base commit — **−34,516 bytes**. Every tune moves because the
  artifact's own rung-(d) note moves with the premise (+4 bytes each, +2,496 in all); net of
  that the rung is **−37,012 bytes over 385 tunes — 286 smaller against 99 larger
  (−49,032 / +12,020) and 239 unchanged in size**. `gate_sweep` at full Songlengths holds
  **624 build / 624 evaluate / 624 clean**, zero divergences and zero refusals; emitted size
  is **−7,277 lines with no tune larger** (585 smaller) and **205,796 rewritten sites proved,
  zero unproved**. The declaration studies are the shape of it: Aces_High's 52 streams read
  `via ptr_00FB` instead of `via ptr_00FB_lo` and the pair's table gains the extent
  `-> $15C4..$2581`; Commando gains `m_5711[32] lo m_573E -> $5887..$5D7D`; Ghouls's three
  cursors lose their `_lo` suffix. Growth is the lane spelling where a lane genuinely survives
  — a lone store costs the mask and the `zext2` — and shrinkage is the pair stores that now
  meet at one word store and the declarations that lose a field.
  (10) **The gates.** Suite **2,805 passed / 490 skipped / 13 xfailed** (oracle included),
  `black --check` and `pylint` (10.00/10) clean.
- **2026-08-12 — the two declaration clauses #190 (6) scoped: the bound a step is taken
  under, and the value init left in the cell.** Two of the three capability pins, each owing
  "a grammar production and a carrier that does not exist"; both productions are `statedef`'s
  and both carriers were already in the program, unread.
  (1) **The evidence clause.** `statbnd` is `mask $K` — the constant the cell's own steps are
  taken under — or `bound $lo..$hi`, the extent its values are witnessed in. It is a scalar
  field's, refused wider than its field, and it rides after `in`/`observed`. Emission drops a
  bound the field cannot hold rather than spelling it, so the text stays parseable by
  construction.
  (2) **Its carrier is the census, which computed the constant and discarded it.**
  `roles._mask_bound` returned the inner term alone; it returns `(x, K)`, `roles.reading`
  reports a shape with the mask its step was taken under, and `census` names a cell's bound
  only where its masked steps agree on **one** constant. It rides on `FrameProgram.bounds`
  beside `roles`, exactly as 2b's extents ride.
  (3) **The initializer clause, and the value `prov0` could not carry.** `statedef` had no
  `= HEX` alternative at all. The value is the flat image's own: `decompile` keeps init's
  image and not its trace, so `mem0[cell]` **is** what init left there, while `prov0` names
  the origin *address* a byte was copied from and never what it is. Measured on the
  prototype, `prog.mem0` and a `run_vm(mem, 0)` agree on every declared cell, and `prov0` is
  empty there — which is why the pin could not be read off it. Because the clause is a
  reading of the image rather than a fact beside it, `parse` **checks** it: a declared value
  that is not `mem0`'s is refused, as is one wider than its field or one on an array field.
  (4) **The corpus moves twice, and both diffs are one shape each.** Against a base run of
  this branch's parent (`cbf029b`), reproduced rather than carried forward — **624 tunes, 0
  refused, 28,271,645 bytes**, aggregate
  `8410d058e6b844b04cbd4cafa0c5afc3f8c79d50558ea41dd54e49bc810f7c78`.
  The bound clause alone moves **13 of 624 tunes, +144 bytes**, every one larger — 16 clauses
  of nine characters (`Advanced_Pinball_Simulator` 3, `Ark_Pandora` 2, eleven tunes 1 each),
  and every moving line the same idiom read twice: `ctr_B49D = ((ctr_B49D + $01) & $0F)`
  becomes `ctr_B49D: accumulator u8 mask $0F`, `Ark_Pandora`'s two `mask $03` — the wrapping
  table index stage 2's census called "the bound spelled as a mask". The initializer moves
  every tune that declares a scalar cell, one clause per declaration and nothing else per
  line: **624 of 624 tunes, all larger, +100,768 bytes (+0.36%)**, aggregate
  `703af3a820d6b548b47b507db6d8eea57127e7a74dd6218f5c5be171fdcefa8b` over **28,372,413
  bytes**; the bound clause alone stands at
  `a284e4f074182a9a3c44fcee63fddb54927781437cb12b274e594ac5ca4ee406`. `Hubbard_Rob/Commando`
  is the shape read by hand: five state lines change and no other line in the file --
  `ptr_00FB: cursor u16` becomes `ptr_00FB: cursor u16 = $15E2`, the address in the song
  data its walk starts at.
  (5) **The prototype prints through the engine's own field line now.** `sml.render` spells
  its state block with `sidprog._field_line` itself, so the example cannot spell a clause the
  grammar has not got, and its 59 declarations all carry an initial value:
  `v1_pos: cursor u16 = $14EB in script0` (the cursor's own script, named by the image's
  labels), `log_idx: accumulator u8 = $00 mask $0F`,
  `v1_pitch: accumulator u16 = $15ED bound $1167..$1A13` (the note range, measured).
  `render` gained `frames` — the evidence run's length, which is the fold's own: rendered
  over more frames than it was folded over, a program leaves the dispatch domain it declares.
  (6) **Both pins are restated against the artifact where they read past it.**
  `init_lifts_to_declared_initial_values` compared at one byte per cell and over cells no
  declaration names; it reads at the **declared width** over the **declared cells**, so a
  lane a `u16` spans is one value with its pair rather than a field of its own — and it now
  also asserts that every declared cell carries one, which the old form did not.
  (7) **The third pin is measured, not taken.** `vm_family_operator_set_is_emitted` owes an
  `operators { }` production, a symbolic name per opcode, an arity and a `writes` set, and
  the reading is now known on both sides. In the prototype the dispatch arms are already
  emitted (`dispatch { op $10AF: ... }`) and the image's labels name them (`v0_c_vib` →
  `vib`, `off`, `loop`, `raw`); an arm's arity is its cursor advance (`pos += 2` → 1,
  `set16` from the stream → 2, the variable advance `pos += (y + 1)` → the decoded length
  whose run ends at the first byte with bit 7 set), and its `writes` set is what its own
  statements assign — **but the arm's statements are not all in the arm**: `c_off` folds to
  a bare `break` whose work sits at the block its label heads, so the reading is the union of
  the arm body and its label's block. On the engine side `follin_arity.operators(model)`
  gives arity and escape per opcode and **no** name and no writes; the names are the handler
  table's (`cmdlo`/`cmdhi` at opcode index) and the writes are the arm's own statements, the
  same union. It is the next landing, and it is landing 4's remaining part.
  (8) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624
  clean**, zero divergences and zero refusals. `splice_sweep --against` the recorded
  artifact (`splice_s4l4c.json`, which predates #192-#195): **2 bad, zero new and 69 fixed**
  -- one lint and one divergence left -- **parse and fixpoint 624 of 624** over both new
  productions, **205,796 sites proved, zero unproved**, −7,277 lines with no tune larger
  (585 smaller), `fields`/`roled` **13,234 of 18,109**, `zero_arch` **2 of 624**: every
  splice number is #195's, unmoved. Suite (hermetic, `-m "not oracle"`) **2,801 passed / 490
  skipped / 11 xfailed**, the
  ledger's 13 less the two pins this flips. `black --check` and `pylint` (10.00/10) clean.
  The prototype ledger goes **six → four**, and
  `test_every_pin_names_the_landing_that_flips_it` moves with it.
- **2026-08-12 — `eqlift_mem`, the call wall: a call retires what its callee may define, and
  the scratch pin flips.** #194 measured the mechanism and re-owned the pin to this file; this
  is that landing, bounded soundly rather than as measured.
  (1) **The bound.** `render_proc`'s wall retired *every* local at every call, so a value the
  caller held died at the boundary and extraction fell back to the cell it was stored in. The
  wall now retires `frameproc._Info.may` — the callee's may-define set, plus a `pcall`'s
  declared returns — intersected with the locals the walk holds. Nothing else moves: the
  memory join is untouched, and a switch's wall and every non-call boundary stay whole.
  (2) **What `may` is not a bound on, and the refusal that follows.** `_Info._may` reads a
  computed transfer and a `callb`'s writes past its inlined body as `G`, the program's
  register *read* set — no bound on a local no expression names, and locals are program-wide
  across a call — and it follows no `goto` leaving its own procedure. `_wall_may` therefore
  admits an entry only where its whole tree is `asg`/`for`/`call`/`pcall`/`swc` over admitted
  entries with every `goto` internal, refuses the map entirely under `open_flow`, and
  **re-proves the closure it reads** rather than trusting it: `summarize` gives up after 24
  rounds, so an entry whose `may` does not contain its own definitions and its callees' is
  dropped. A refused callee keeps today's wall exactly — observed-primary, never past it.
  (3) **The pin, and why its control still holds.** `test_scratch_beside_kept_sp_fabric_promotes`
  XPASSed and is landed: `$0030`'s only reader was the value spelling, which now crosses all
  three `pcall`s. Its control is unmoved — the same fixture's raw `call` path still holds the
  cell, because there the callee is not an admitted entry and its wall stays whole. The ledger
  is 19 → **18** and `eqlift_mem` owns none; `_OWNERS` keeps its sixth entry with an empty
  partition, as `framestack`'s and `frameproc`'s.
  (4) **The corpus diff, §4-reviewed.** `emit_identity`: 624 tunes, 0 refused, 28,372,413 →
  **28,371,517 bytes**, `703af3a8…` →
  **`73e80047254ea6997e4a17909db9fdf3c94d4bd46b737579086e4fc936746bdf`** (the new baseline),
  **79 of 624 moved** — 64 smaller (−1,092), 12 larger (+196), 3 the same size, net **−896**.
  One mechanism in three directions. **The shrink**: a value the call used to kill is now the
  constant it holds, and the arithmetic folds around it — `1942` reads `y = (y + $01)` and
  `… | zext2((y + $01)) …` and now reads `y = $01` and `… | $0002 …`. **The growth**: an
  argument or an index prints the value or the local it holds instead of the register —
  `Catacombs` (+46, the largest) turns `sub_1610(y)` into `sub_1610($00)` and `Krakout` (+6)
  `m_E588[y]` into `m_E588[idx0]`. That is bytes bought with machine shapes, and the headline
  reads it: `arch` **183,682 → 183,493** (−189), `zero_arch` unmoved at 2. **Size-neutral**:
  the OR in a page-one address takes the other representative operand order
  (`($0100 | zext2(sp))` → `(zext2(sp) | $0100)`), §4's own recorded shape.
  (5) **#192's defect shape did not surface, and it was looked for.** The lint family is the
  **same one** tune, none new and none fixed, and `Allt_under_himmelens_faeste` — the tune the
  spanning broke — is one of the three size-neutral moves, its diff the operand order alone:
  `sub_09A0(y) -> y` still renders `y = (y + $01)`. So the header/body `_Info` disagreement is
  neither created nor cleared here. #194 (3)'s signature refresh is not taken with this landing
  after all: its one instance (`International_Karate`, `sub_AE0C(sp)`) is a live-in `repolish`
  froze before the rung that revealed it, and moving it means re-deriving signatures and
  rewriting every `pcall`'s arguments in `frameproc`/`frameprog` — the wall bound neither adds
  nor removes an instance, measured, so it stays that landing's own work.
  (6) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**,
  zero divergences and zero refusals (`out/gate_w3.json`). `splice_sweep` against the base
  commit's run: **2 bad, the same two, zero new and zero fixed** (`Emax_01`'s one Gate FP
  divergence and `International_Karate`'s one lint, byte for byte the same messages), −7,280
  lines against −7,277 with no tune larger (585 smaller), **205,793 sites proved, zero
  unproved**, `fields`/`roled` **13,234 of 18,109** unmoved (`out/splice_w3.json`). Suite
  **2,790 passed / 490 skipped / 12 xfailed** on the #195 rebase (oracle excluded, corpus
  parameters unbounded), coverage **88.51%**, and **2,773 / 490 / 24** against #194 before it;
  on this rebase the shredder file is **117 passed / 6 xfailed** and CI carries the rest.
  `black --check` and `pylint` (10.00/10) clean.
  (7) **What this entry replaces.** Everything above is re-measured on the rebase onto #196.
  The two earlier measurements — against #194, and against #195 — gave the same 79 tunes, the
  same 12 growths and the same three shapes (−929 and −896 bytes), with `splice_sweep` at 63
  bad and at 2 bad, zero new and zero fixed each time. This landing is orthogonal to all three.
- **2026-08-12 — the song-model modules leave, and `eqlift_annotate` does not.** The
  housekeeping item's condition was stage 4's role-typed artifact replacing their function;
  #187 turned the roles ON and the artifact carries them, so the item is taken.
  (1) **The claim, measured before the deletion.** An AST scan of every package module and a
  grep over `tools/`, `examples/` and the tests: `song_model`, `generators` and `movefwd` are
  imported by each other, by `tests/test_song_model.py` and `tests/test_movefwd.py`, and by
  nothing else. The subgraph is closed, so **1,187 lines** go — `song_model.py` (398),
  `generators.py` (138), `movefwd.py` (135) and the two test files (365, 151).
  `docs/frameprog.md` §4.1's refused SID-shadow rung keeps its measurement and now records
  that its detector is retired.
  (2) **`eqlift_annotate` stays, and the reason is a live consumer, not a schedule.**
  `eqlift_mem.emit_mem` calls `aggregate` and `annotate_lines` on every header it renders, and
  `emit`/`emit_mem` are `examples/state_machine_lift.py`'s substrate. It leaves with them; the
  module's own docstring now says so, so the next reader does not have to re-measure it.
  (3) **No emitted byte can move, and the gate says so.** Nothing production imports what was
  deleted, so `emit_identity --expect` reproduces the base commit exactly: **624 tunes, 0
  refused, 28,371,517 bytes**, aggregate
  `73e80047254ea6997e4a17909db9fdf3c94d4bd46b737579086e4fc936746bdf` unmoved, **0 of 624
  moved**. (Measured twice: byte-identical against #196 before the rebase onto #197, and
  against #197 after it.) Suite **2,772 passed / 490 skipped / 10 xfailed** (oracle excluded,
  corpus parameters unbounded), coverage **90.08%** — deleting tested code moves the
  denominator, so it is measured, not assumed. `black --check` and `pylint` (10.00/10) clean.
- **2026-08-12 — rung (d)'s widening guard: the SID window is write-only, so the rung invents
  no write at all.** `lone_lane` was the last shredder pin rung (d) owned. `framefuse._widen`
  turned a lone lane store into `(reg & K) | v` — a **read** of `$D400`–`$D416`, which the
  machine cannot read and no admitted rule removes. `framefuse.write_only` refuses the
  widening inside the window, and the half stays the byte the driver wrote: at `sid.reg[i]`
  where its index is symbolic, at its own register name where it is not.
  (1) **The guard is the window, not the fixture.** The pin's fixture is an unindexed store to
  `$D401`, but every lane the widening reached is inside `$D400`–`$D416` — freq, pulse and
  cutoff are the only 16-bit registers and their eight lanes are all of it — so the guard
  retires the *whole* widening, indexed and not. What survives is `_pair_at`: a SID word store
  is now always **two stores the driver itself made**, brought together. That is a better law
  than the one it replaces, and it is the one §4.3 and §7.2 now state.
  (2) **The placement proof stays, because it is the residue's metric.** `_lane_aligned`,
  `_lane_sweep` and `_consts` still run and still fill `indexed`/`unproven`/`notaligned`/
  `swept` on the pair's proof record — §7.10's lane column, which `tools/fuse_measure.py`
  reads. What changed is what the rung *does* with a proved placement: nothing, because
  placing a byte is not a licence to write a word.
  (3) **The price, stated.** §7.10's headline was "Commando is 16-clean — zero byte-wide SID
  lane accesses, named or viewed". It is not any more: three lane stores whose partner never
  meets them stay bytes at the view, and `test_real_tune_frameprog_commando_gate` says so
  rather than asserting a claim the guard withdrew. `storage_census`' `readback_sites` and
  `lift_residue`'s `sid_readback` signature go to **zero** on the fixture that raised the pin —
  they exist to count exactly this — and `frameval`'s canonical-record test loses the two
  hi-lane writes the widening used to put on the wire, which is the machine's own record.
  (4) **The corpus, and the price is negative.** `emit_identity`: **624 tunes, 0 refused,
  28,322,337 bytes**, aggregate
  `a403a8aab3a6630ab2ef28fdc9a5fe369a0bc8fd5654f0884fd0e33387c63ff6`, against
  `703af3a8…`/28,372,413 at the base commit — **−50,076 bytes**. Net of the artifact note the guard adds (+6 bytes a tune) it is
  **−53,820 over all 624 tunes, every one smaller and none larger**: a byte at the register
  file is shorter than the read-modify-write it replaces, so the honest form is also the
  small one. `gate_sweep` at full Songlengths holds **624 build / 624 evaluate / 624 clean**,
  zero divergences and zero refusals; `splice_sweep` is **2 bad, zero new and zero fixed**
  with 205,796 sites proved and zero unproved, and the architectural-register census falls
  **183,682 → 183,534** with `zero_arch` unmoved at 2 of 624. Field and role counts do not
  move at all — this landing touches no state declaration.
  (5) **The ledger.** rung (d) owns **no** shredder pin: the family is **6 → 5**, and with
  `eqlift_mem`'s wall landed beside it the five are rung (f) 4 and `datadecl` 1. Suite
  **2,789 passed / 490 skipped / 9 xfailed** (oracle included), `black --check` and `pylint`
  (10.00/10) clean.
- **2026-08-12 — rung (f)'s writer set: a web that maintains itself is no third writer, and
  the target set pays for the name.** Item 7's four pins were the last rung (f) owned. #189
  measured the premise they refuse under; #195 moved two of them off the writer set onto
  premise 1, and this closes all four on one reading — **what a pointer web's own cells
  explain is the web's, and what it cannot claim is the block set, not the name**.
  (1) **Premise 1 takes the web's own maintenance.** A definition is admitted where it is a
  declared `lo`/`hi` partner-table row (today's rule), a **constant word**, or a value whose
  every memory read is a plain read of a **web cell** (`frameptr._web_value`). An advance
  `P = P + n`, `mux_pair`'s lane-wise `INC`/`INC` and `cursor_save`'s restore all wear that
  shape; a computed pointer and a row from outside the web still refuse. `ptrcert._const`
  moves down to `frameptr.const_word`, where the layer that owns the shape is.
  (1a) **The lane reload is the same reading, and it is the residue's own shape.** Rung (d)
  spells a lone half store as the lane update `(w & $FF00) | zext2(v)`, so a pair whose lanes
  are reloaded *apart* never packs one entry and `_entry` cannot see it. `_Ptr._lane` asks
  `framefuse.lane_of` what the update replaced and admits it where the surviving lane is the
  web's and the replacement is the web's own row — a constant, a web read, or a **declared
  const** table row (`_row_declared`; no bound is asked of its index, since an open target
  set has no claim for one to hold up). Measured on the three editor families the survey
  read, this is the residue: `Grid_Runner`'s eight deref sites resolve on it, and it is worth
  −4,086 bytes over 29 more tunes than the advance alone.
  (2) **The web is the pair plus the save cells it closes over.** `_close` takes the one hop
  2a's held-value closure takes: a definition the pair's own cells do not explain may still
  be the web's if the cell it reads answers for its own word stores. Premise 4 is over that
  web — a span reaching any root refuses, each root's own word store excepted — so a byte
  store, an indexed store or a wild store anywhere in the web still voids the whole thing.
  (3) **The price is the target set, and it is the observed-primary guard.** A web with a
  maintenance definition is **open**: `targets` claims nothing, the lemma says
  "target set open", §4.6's provenance rule refuses it by name (`pinned` cannot move, and
  did not), and the site stays in 2b's ⊤ population so `ptrlift` still gives it the
  **observed** extent and the `in` clause. `FrameProgram.proved` is that split — rung (f)'s
  block-proved subset — and `ptrcert._top`/`storage_census.top_sites` read it, so the two
  instruments still name one population.
  (4) **`writethrough` wanted a rung input, not a premise.** Its store address was
  `m_1400[x]:2 + $0002` — the columns the pointer word was *loaded* from, not the pointer —
  because `_fold_words` folded a store's **destination** as a value: `_fold_word`'s spill
  trace (`_side_addr` → `Defs.cell`) replaced `zp_02`/`zp_03` with the table reads standing
  in them. A destination is an address, so `frameproc._fold_stmt` folds it in the address
  position, where only the plain adjacent shape folds — and the machine's own
  `mem[(ptr_0002:2 + $0002):2]` survives to be named `*ptr_0002[$0002]`. With it, the store
  and the read are one deref site, and `frameptr._span` bounds a deref off the registry: the
  declared const table's word set out of `mem0`, plus the row bound — computed, never
  observed. The word sets are **assumed and then checked** (`_Writes`): a cell some other
  store may still reach loses its set and every bound that rested on it, which the corpus
  never exercises (the aggregate is byte-identical with and without the check).
  (5) **The corpus, §4-reviewed.** `emit_identity`: **624 tunes, 0 refused, 28,347,787
  bytes**, aggregate `e0daf5c46c1ae46c804d22b6ba874986eeaae3ded1fbc0518740522001d0e8f3`.
  Every tune moves because the artifact's rung-(f) note moves with the premise (+83 bytes
  each, +51,792 in all); net of that the rung is **−25,446 bytes over 137 tunes, every one
  smaller and none larger**, 487 unchanged in size. The movement is one shape and only one:
  every changed body line is `mem[(ptr:2 + i)]` becoming `*ptr[i]`, which is what a naming
  rung must look like — Battle_of_the_Village's 88 changed lines are 88 derefs and nothing
  else.
  (6) **The base commit's recorded aggregate does not describe main.** #199 recorded
  `a403a8aa…`/28,322,337 as CURRENT. Measured on this session's cache at `1e002d7`, main
  emits `ae41682ce7dd3c1a88d37e674db380447f95f34b9059658c092cc213f8ca173f` /
  **28,321,441** — 896 bytes apart — so the delta above is against a measurement of main and
  not against the ledger's number. The ledger's number is superseded, as #190 (1) had to
  supersede `018ce8f4…`.
  (7) **The gates.** `gate_sweep` at full Songlengths holds **624 build / 624 evaluate /
  624 clean**, zero divergences and zero refusals. `splice_sweep` is **2 bad — the two
  standing ones and no others**: `International_Karate`'s `local 'a' used before definition`
  and `Emax_01`'s one divergence, with parse, fixpoint and sites at zero; emitted size is
  **−7,280 lines with no tune larger** (586 smaller) and **205,793 rewritten sites proved,
  zero unproved** (205,796 before: three sites the naming takes out of the rewrite
  population). The architectural-register census falls **183,534 → 183,345** with
  `zero_arch` unmoved at 2 of 624; `fields`/`roled` do not move (18,109 / 13,234), since the
  rung touches no declaration. Suite **2,799 passed / 490 skipped / 5 xfailed**, twice;
  `black --check` and `pylint` (10.00/10) clean. No e-graph rule is admitted and no value
  moves, so there is nothing here for `verify_rules` to prove: the rung names and Gate FP
  cannot move by construction.
  (8) **The ledger.** rung (f) owns **no** shredder pin: the family is **5 → 1**, and the
  one left is `datadecl`'s.
  (9) **Two items the survey of three editor families raised, both re-measured here.** The
  claim that a false `lo`/`hi` partnership *poisons* premise 1 is **refuted**: `_entry_words`
  takes `(lob, hib)` from the **code's own** pack and then asks the registry only to agree,
  so a wrong declaration can starve the rung, never feed it. What it does cost is measured —
  `Angry_Birds` refuses at `m_202A/m_2035 is not a declared lo/hi partner pair` on a
  partnership the code reads at one index — and that is `datadecl`'s, taken with item 8.
  The proof body also read `len(tables)` as its definition count, so a first-definition
  refusal said "0 definition(s)" on a pointer with five; it names both now (`ndefs` and
  "table row(s)").
- **2026-08-12 — stage 4, landing 4 (part): the VM operator set is emitted, and every field of
  it is read off the dispatch.** The third capability pin. `sidprog.lark` gains
  `operators_sec`/`opdef`/`oprep`, `FrameProgram.operators` carries it, and
  `follin_arity.operator_set` fills it — which makes that module production code and closes
  #193's orphan note, since until now nothing but its own tests read it.
  (1) **The four readings, each off the arm.** The **name** is the handler the paired
  `cmdlo`/`cmdhi` tables select at that opcode: the image's own label for it where a caller
  supplies one (the prototype's `v0_c_vib` → `vib`), else `h_XXXX`, the address. The **arity**
  is the arm's cursor advance in operand bytes. A decoded-length arm has no constant arity, so
  it spells its **run** instead — `repeat $00..$7F at 1 tail 1`, the byte span that continues
  the operand groups, the offset the first guard byte sits at and the bytes consumed past it —
  and its arity is one group. The **writes** are the cells the arm's own blocks assign, which
  is the arm body *together with* the block its label heads: a fold leaves `c_off` a bare
  `break` whose work sits at that label, and the walk covers both because it stops at the
  block that moves the cursor, not at the case arm. Voice copies are one operator set at
  several seats, so the name is the first seat's and the writes are the union over them; an
  operator the seats disagree on is refused, exactly as `operators` already refused it.
  (2) **Two capabilities the readings needed, both guarded.** The escape's `trailer` was
  inferred from the counter's net `Y` delta, which is right only where the arm's advance *is*
  `Y`; the prototype's `TYA; SEC; ADC ptr` carries in and advances one further, so `walk` now
  reads the advance off the arm's own cursor store (`_pair_live` + `_eval_pair`, the cursor
  lane bound to two probes so a move that depends on it is no advance) and the trailer is that
  minus the guard offset. Follin's `(3, 2, 1)` is unmoved on both exemplars — the reading
  reproduces what was right and fixes what was not. And an arm that **calls** was refused
  outright (`arm terminator jsr`); a callee that neither fetches the arm's stream nor moves
  the counter cannot change what the arm consumes, so the arm now reads through it and any
  other callee still refuses. That is what lets the datum arm below the operator floor be read
  at all: `default_arm` walks the guard's other edge, and the prototype's note form is
  `arity 1` off its own `pos += 2`, not a transcribed 2.
  (3) **The prototype prints its scripts decoded.** `sml.render` spells the block through
  `sidprog.op_line` itself, so the example cannot spell a clause the grammar has not got, and
  each script decodes through the recovered grammar at the image's own labels:
  `script1 $1540: vib $00, raw $09 $00 $0A $04 $FF, $00 $30, …, off $08, loop $40 $15` — the
  `loop` operand is the script's own base, and the walk lands exactly on the next label.
  A byte below the floor is spelled as itself with its operands, because the datum form is
  the guard's else-arm and not an entry of the operator table the pin reads.
  (4) **The corpus, and it is one shape.** Control reproduced at the base commit rather than
  carried forward: **624 tunes, 0 refused, 28,347,787 bytes**, aggregate `e0daf5c4…`. With the
  block: **624 tunes, 0 refused, 28,373,593 bytes**, aggregate
  `54c58a3dffd4575d1f4eb07de476267d3dd055bc3896da324c08fbe4a1c18230`. **14 of 624 tunes move,
  every one larger, +25,806 bytes and none smaller** — and all fourteen are the Follin family,
  the corpus's one script-VM driver: `Ghouls_n_Ghosts` +2,563 for 21 declarations,
  `Gauntlet_III` +2,565, `Cosmic_Storm` +2,545. Nothing else in the corpus dispatches through
  an SMC operand, so nothing else declares an operator, which is capability with zero use
  everywhere it has no reading to make.
  (5) **The control is reproduced, not carried forward, and it has to be.** Measured against
  #199 the same way, the control came out **896 bytes under** its recorded `a403a8aa…`/
  28,322,337 -- before any of this landing, so not this landing's. The one difference in
  conditions is a `.sweep-cache` shared with a second branch's concurrent runs, which is
  exactly why every number here is a fresh pair measured under one condition. The gap is
  handed on as a measurement item, not a diagnosis.
  (6) **The ledger.** The prototype's family goes **four → three** and
  `test_every_pin_names_the_landing_that_flips_it` moves with it.
  (7) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**,
  zero divergences and zero refusals. `splice_sweep`: **parse and fixpoint 624 of 624** over
  the new production -- the block round-trips through `dumps(loads(t)) == t` like every other
  section -- **2 bad** (the two known), **205,793 sites proved, zero unproved**, `fields`/
  `roled` **13,234 of 18,109** and `zero_arch` **2 of 624**, all unmoved: the block declares,
  it does not rewrite. Suite (hermetic) **2,780 passed / 490 skipped / 8 xfailed**.
  `black --check` and `pylint` (10.00/10) clean.
- **2026-08-12 — stage 4, landing 4 (part): an SMC dispatch cell is the transfer, so it stops
  being declared beside it.** The fourth capability pin,
  `smc_dispatch_cells_are_not_data_state`. The reading is one law on both sides: **a cell every
  read of which decides where the machine jumps holds no datum another statement observes.**
  (1) **What the artifact already spelled twice.** An SMC dispatch's opcode cell rides the
  `dispatch $107A: $69 $E9` header *and* the state block as `m_107A: vm u8 observed $69 $E9`;
  a computed transfer's operand cell rode the state block as a `parameter` nothing but the
  `goto (…)` reads. `frameprog._drop_transfer_operands` reads the procedures once, splits every
  memory read into the transfer-target positions (`dgoto`/`dcall`/`igoto`/`dbr`, and an
  `opsw` subject) and everything else, and drops the state row of a field that appears only in
  the first. A cell the driver *also* reads as data keeps its row — the tune that toggles its
  own `ADC`/`SBC` opcode (`a = (m_107A ^ $80)`) still declares `m_107A`, because that read is
  a datum and the rule is about reads, not about addresses.
  (2) **The prototype, same law in its own dialect.** `sml.transfer_operands` splits reads by
  `dgoto` exactly as the pin's own predicate does, and `classify_roles` drops what it names:
  `m_10AD`, `m_10AE`, `m_11F8`, `m_11F9`, `m_1343`, `m_1344` — the three voice copies' JMP
  operand pairs — leave the state block, 323 lines → 317. The pin flips.
  (3) **The corpus moves one way.** Against #201: **169 of 624 tunes move, every one smaller,
  −12,653 bytes and none larger**, 0 refused — **28,360,940 bytes**, aggregate
  `b6c3367c7aba17e6de2239662c82d96fb33904d4a46f9952bed67b5fab968470`. `splice_sweep`'s field
  count falls **18,109 → 17,663** and `roled` **13,234 → 12,825**, which is the measurement of
  the claim: 446 declarations were machinery and 409 of them carried a role. `1K_Tune` is the
  shape read by hand — exactly one line goes, ` m_105E: parameter u8 = $B8`, and no other line
  in the file changes.
  (4) **The gates.** `gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**,
  zero divergences and zero refusals: a declaration the evaluator never read cannot move a
  frame. `splice_sweep` **parse and fixpoint 624 of 624**, **2 bad** (the two known),
  **205,793 sites proved, zero unproved**, `zero_arch` **2 of 624** unmoved. Suite
  **2,791 passed / 490 skipped / 3 xfailed** hermetic — the two `test_frameprog` cases that
  pinned the double declaration now pin its absence and the header that carries the domain.
  `black --check` and `pylint` (10.00/10) clean.
  (5) **The ledger.** The prototype's family goes **three → two**:
  `no_architectural_register_survives_as_a_value` and `state_block_holds_no_scratch` are left,
  and the second's remainder is now **seven** cells, not thirteen — this landing took the three
  SMC operand pairs off it, and what is left is the zero-page `parameter` cells that are a
  callee's per-frame arguments.
- **2026-08-12 — stage 4, landing 4 (part): the second demotion notion is frame-boundary
  liveness, and the instrument that measured the pin was blind to a wide access.** The fifth
  capability pin, `state_block_holds_no_scratch`, and it took two things: a corrected reading
  of what fails, and the per-frame kill nothing computed.
  (1) **The instrument first.** The pin's own `_TracedRam` recorded a first read or a first
  write per address and ignored **slices** — and every wide access in the machine is a slice
  (`ram[a:a+n]` for a `w16` read and its store). So the `u24` PWM phase, read as one span
  before it is written, looked written-first; and the three `u16` porta diffs, written as one
  span, looked read-first. Marking every byte a slice spans moves the failing set from the
  record's thirteen to **nine**, and they are different cells: `tick`, `row_src`, `row_val`
  and the three `v*_diff` pairs. The record's `phase` and `v*_note` were never scratch; its
  three SMC operand pairs left with #202, one landing earlier.
  (2) **The notion.** `frame_live_in` is backward liveness over the flattened program whose
  **exits flow to its own entry**, because the next frame is where they go; the fixpoint over
  that loop is the frame boundary's live-in set, and a cell outside it carries nothing across
  the boundary however many readers it has inside one frame. That is the order-sensitive,
  per-frame question #183's `_unread` cannot ask: `_unread` demotes a store no read anywhere
  names, and every one of these nine has readers.
  (3) **What the proof cannot see, the run bounds.** An indexed read's landing is not in the
  statement, so the evidence run records every address one reached (`Machine.rows`) and a cell
  some indexed read touched is left alone — observed-primary, and the guard is what makes the
  zero-page reading exact rather than optimistic. Measured on the prototype the proof and the
  observation **agree cell for cell**: nine proved dead, nine observed written-before-read,
  neither set larger than the other.
  (4) **The prototype.** `classify_roles` drops what `frame_scratch` names, so the nine leave
  the state block and the role map together, and `classify_roles` now takes the evidence run's
  length for the same reason `render` does — rendered over more frames than it was folded
  over, a program leaves the dispatch domain it declares.
  (5) **The engine still declares them, and the mechanism is named.** `deity_informant/` is
  untouched by this landing: the artifact's own second demotion is the same liveness over
  `frameproc`'s statement graph — `if`/`loop`/`for`/`opsw`/`swg`/`swc` bodies, `goto` to a
  label, `break`/`continue`, and a `call` resolved through the callee's own summary — with the
  indexed reads bounded by 2b's observed extents rather than by a machine run. `frameproc` has
  no flattener, which is the whole of the work; it is landing 4's next part and it moves the
  corpus, where this one cannot.
  (6) **The ledger.** The prototype's family goes **two → one**. What is left is
  `no_architectural_register_survives_as_a_value`, whose owner is the residue itself and whose
  metric is `zero_arch` — the final wave's, not this one's.
- **2026-08-12 — `datadecl`'s `via:` discovery reads the pair's own lanes, a partnership
  becomes a co-index claim, and the shredder family reaches zero.** `computed_rows_map` was
  the last stage-3 pin. `extent_unmappable` fired because the rows the run observes are in no
  declared datum: the pair is reloaded from a row the play code *computes*, so it is in no
  reload table and `_anchors` had nothing to anchor on.
  (1) **The interval is the input and the discovery is the mechanism, exactly as #189 (6)
  scoped it.** `expr.floor` states the sound half — `floor(a | b) = floor(a) | floor(b)`,
  `floor(a & b) = floor(a) & floor(b)`, a widening keeps its operand's — which is the bits a
  value *must* set and therefore a lower bound on it. It is a reading over the model algebra,
  not an e-graph rule: the `lo`/`hi` lattice merges by join and can only widen, so nothing is
  admitted to `RULES` and `verify_rules` has nothing new to prove.
  (2) **The anchor is the pair's own two lanes.** `streams._update_summary` records a
  computed lane's floor beside the constants a lane is reset to (`reset_floors`), and
  `datadecl._lane_words` composes the two sides into candidate words, capped at `_LANES`.
  `computed_rows`' hi lane resets to `$10` and its lo lane's `((ctr & 1) << 3) | $80` floors
  at `$80`, so the anchor is `$1080` — and the run's own reads still bound the extent through
  `_obs_hi`, so a candidate the pair never walks declares nothing. Observation is the guard,
  which is why the interval may be the input.
  (3) **A second block stops being swallowed.** `cursor_save`'s constant reset is an anchor
  now, so its two blocks are declared apart — `stream m_1500[5]` and `stream m_1560[1]` where
  one `stream m_1500[97]` used to span the gap between them — and rung (g)'s `in` clause
  names both (`ptr_0002: cursor u16 = $1500 in m_1500, m_1560`).
  (4) **The lo/hi partnership was asserted from nothing, and is now a co-index claim.** A
  survey of three editor families found `datadecl` zipping the lo cell's reload tables with
  the hi cell's **in sorted base order** — `zip(sorted(lts), sorted(hts))` — which publishes a
  partnership with no evidence that the two columns are ever read together. Two columns of one
  datum are read at one row or at no row at all, so `_co_indexed` pairs on the index
  expression the two reload reads share (`reset_rows`). Measured on `Grid_Runner`: the bogus
  `m_1675`/`m_1678` pairing goes (their reload reads carry different indices) and the two
  genuine ones, `m_1493`/`m_1496` and `m_1499`/`m_14CB`, stay.
  (5) **What the false partnership cost, measured — and what it did not.** It does **not**
  poison rung (f): `frameptr._entry_words` takes `(lob, hib)` from the *code's own* pack and
  asks the registry only to agree, so a wrong declaration can starve the rung, never feed it.
  What it corrupts is the declaration — the `->` target range, the co-extensive size and the
  `_PAIRS` render registry.
  (6) **One finding this landing does not close, with its mechanism named.**
  `Angry_Birds` still refuses at `m_202A/m_2035 is not a declared lo/hi partner pair` *after*
  the co-index fix, and the cause is not the partnership: `m_2035` is a **cobase** of
  `m_202A` (`table m_202A[21] +m_2035`), so `_regions`/`_groups` carved the two columns as one
  group and only the group base gets a declaration — `tables.get(0x2035)` is None and the
  roles are never written. That is a group-carving/pair-role interaction: a pair whose two
  columns fall inside one carved group can hold no `lo`/`hi` role, and it is the remaining
  blocker on that family. Owner: `datadecl`, unscheduled.
  (7) **The corpus, §4-reviewed.** `emit_identity`: **624 tunes, 0 refused, 28,379,441
  bytes**, aggregate `101669face8476fab8c4e1e998030370dc3c13bad3e17dbc378e00aa83dac841`,
  against a base measured on the merge commit `982b8df` — `b6c3367c…`/28,360,940 — and the
  landing's own movement is **+18,501 bytes over 136 tunes** (85 larger
  +22,091, 51 smaller −3,590, none the same size). The growth is the discovery's own price
  and it is one shape: a newly declared block prints its bytes, and its base is a new
  **bound**, so the datum it splits stops at it and the remainder falls back to `image { }`,
  which prints less densely than `data { }`. Amazing_Spider-Man is the largest mover
  (+3,734) and is exactly that: three `stream … via ptr_006E` blocks discovered off the
  pair's lanes, 37 declarations → 39, and no pair table's role moved. Measured once against
  #200 and again after the rebase across #201–#203, the movement is the **same 136 tunes and
  the same +18,501**, so the discovery is orthogonal to the operator set and the SMC
  demotion: three landings touching one artifact, and their prices add.
  (8) **The bookkeeping law, stated by a drift and then obeyed.** #199 recorded
  `a403a8aa…`/28,322,337 as CURRENT; measured on this session's cache at `1e002d7` main
  emitted `ae41682c…`/**28,321,441**, 896 bytes apart, so #200's delta was taken against a
  measurement rather than against the ledger — the same failure #190 (1) found in
  `018ce8f4…`, a number recorded from a run never re-measured on the merge commit. **The
  law**: CURRENT is only ever written from an `emit_identity --expect` that passed *on the
  merge commit*, and a landing quotes the baseline it measured, never the one it inherited.
  This landing is the first to follow it, and the base held — `982b8df` re-measured to
  `b6c3367c…`/28,360,940, reproducing #202's recorded aggregate exactly, which is also the
  evidence that #203 moved no byte, as its own note claimed.
  (9) **The gates.** `gate_sweep` at full Songlengths holds **624 build / 624 evaluate / 624
  clean**, zero divergences and zero refusals: a block declared apart from its neighbour is
  still the same bytes to the evaluator. `splice_sweep` is **1 bad**, not the standing two —
  parse, fixpoint, gate and sites all zero, and what is left is `International_Karate`'s
  `local 'a' used before definition`. `Emax_01`'s divergence, bad since it was first
  recorded, is clean here; splice was run once and on the branch only, so the credit belongs
  to the `982b8df`..HEAD span and this landing cannot claim it alone. Emitted size is
  **−7,280 lines with no tune larger** (586 smaller) and **205,793 rewritten sites proved,
  zero unproved**. `fields` **17,663 → 17,662** and `arch` **183,345 → 183,344** against
  #202, one declaration each, with `roled` unmoved at 12,825 and `zero_arch` at 2 of 624.
  Suite **2,796 passed / 490 skipped / 1 xfailed** hermetic plus **16 oracle**; `black
  --check` and `pylint` (10.00/10) clean. No e-graph rule is admitted, so `verify_rules` has
  nothing new to prove.
  (10) **The ledger.** `datadecl` owns **no** shredder pin, and the family is **1 → 0**:
  `test_every_stage_three_pin_names_a_live_owner` asserts the set is empty rather than
  counting it down, so the rule now guards a re-opening rather than a backlog. The suite's
  one remaining `xfail` is the prototype's
  `no_architectural_register_survives_as_a_value`, whose metric is `zero_arch` and whose
  owner is the residue — the final wave's, not stage 3's.

- **2026-08-12 — the operator recovery is named for the shape it gates on, and two recorded
  defects land beside it (#206).** The question was why a generic mechanism carried a family's
  name, and the reading was checked before anything moved: the module gates on a `jmpd` whose
  SMC operand cells one paired lo/hi handler table writes, takes its stream from the guard's
  own fetch, its operator range from that guard's floor plus the tables' spacing, and its
  arities from the arms — every reading off the model, not one transcribed constant. The
  **mechanism** was already family-free; the **name** was not, and a name is what a reader
  believes the premise is.
  (1) **`follin_arity` is `deity_informant/opdispatch.py`** (`git mv`, so the history reads
  through), with `tests/test_follin_arity.py` → `tests/test_opdispatch.py` and the importers
  (`frameprog`, `follin_script`, `examples/state_machine_lift.py`) rewired. The docstrings say
  the shape: the module opens on the `jmpd` it gates on rather than on a script VM in general,
  and the seats that tile one handler region stop being "voice copies" — a refusal now reads
  `dispatch seats recover arities [...]`, because what disagrees is the copies of one
  dispatch, whatever put them there. Nothing about the recovery changed: the arms, the tables
  and the escape are read exactly as they were.
  (2) **`follin_script` now says what it is.** No production path imports it — `frameprog`
  reaches `opdispatch` directly — so it is the test-only witness that the hand `_ARITY` table
  is discharged, and its docstring opens with that role. The family study is cited
  (docs/follin-dispatch-study.md) and not embedded: what survives in the module is `_NAME`'s
  operator labels and no length at all.
  (3) **D2 — `base[index]:2` is not always two adjacent bytes, and docs/grammar.md said it
  was.** Where the base is a table declared `lo T`, the emitter renders the **pair row**
  `(base[i], T[i])` — `frameproc._pair_pack` off the ONE `datadecl.decl_pairs` registry — and
  the reader takes it back the same way (`grammar._pair_addrs`), so the round trips already
  pinned the denotation while the document contradicted it. It is load-bearing on real data:
  `Grid_Runner` declares `table m_1493[3] lo m_1496`, whose row 0 is the pattern pointer
  `$167B`, and the adjacent reading of the same row gives `$0D7B`. The grammar doc now states
  the pair row, names the `lo`/`hi` attributes as the disambiguator, and says that a base
  without a `lo` attribute carries the adjacent word rung (d) fuses.
  (4) **D4 — a proof named a cell the document does not declare, and the fix is the emitter's
  own substitution.** Rung (f)'s records read `*zp_FE[y]` where `state { }` declares
  `ptr_00FE`, so `FrameProgram.proofs` joined to no row by name. The first cut threaded an
  alias map into `frameptr`'s proof methods and was **withdrawn on its own measurement**: it
  named the pointer cell and left the index (`m_1441` where the text says `pos_1441`), because
  an index is an expression and not a cell. What is total is `frameprog._aliased`, one call at
  the point the program is built, running `sidprog._alias_sub` — the emitter's own
  bijection-checked body substitution — over every rung's lemmas at once. So the proofs are
  spelled by the same rule the body is, `framemath` and `framestack` included, and no rung
  gained a parameter.
  (5) **The doc sweep, and what it did not find.** `docs/idiom-catalog.md` and the plan's
  housekeeping entry cite the module by path and now cite `opdispatch`; stage 4's artifact
  paragraph said "VM families emit their operator sets" and now says a play routine that
  dispatches through an SMC operand emits the set that shape carries. `docs/frameprog.md` was
  swept and needed nothing — its Follin mentions are the study's §4 paired-index zip closure
  (a normative citation) and two named exemplars (the `+$0F`/`+$1E` mirror handlers, the
  register-poke command), which are examples, not premises. Decision-log entries are left
  verbatim: they are what was decided when it was decided, and a path they name is read
  through this entry.
  (6) **The gates, and the point of running them on a rename.** `emit_identity` reproduces
  **`101669face8476fab8c4e1e998030370dc3c13bad3e17dbc378e00aa83dac841`, 624 tunes, 0 refused,
  28,379,441 bytes** — `--expect` passed, so not one artifact byte moved, which is what a
  landing that claims to move only names owes. `gate_sweep` at full Songlengths is **624 build
  / 624 evaluate / 624 clean**, zero divergences and zero refusals; `splice_sweep` is **1 bad**
  (`International_Karate`'s lint, the next landing's), with parse, fixpoint, gate and sites
  zero, **205,793 sites proved**, `fields` 17,662, `roled` 12,825, `arch` 183,344 and
  `zero_arch` 2 of 624 — every one of them #204's number unmoved. Suite **2,796 passed / 490
  skipped / 1 xfailed**, coverage 90.07%, `black --check` and `pylint` 10.00/10 clean. D4 is
  the reason the proofs could move while the identity did not: `FrameProgram.proofs` is not in
  the emitted text, so the join it fixes is for a consumer of the program, not of the document.
- **2026-08-13 — the signature refresh: a header is re-read off the bodies once the passes
  settle, and the parse-and-evaluate gap closes (#207).** `repolish` freezes `params`/`rets` by
  design — the `pcall`s already spell them — while its prune, inline, word fold and `if`
  factoring move the bodies underneath. On `International_Karate` that left `a` live-in at the
  play entry under a header reading `sub_AE0C(sp)`: a fresh `_Info` over the finished program
  computes `livein[$AE0C] = {a, sp}`, the text reads a local it never defines, and
  `frameprog.emit` — which lints — could not emit the tune at all. **The emitter already had
  the right answer**: `eqlift_mem.render_ctx` builds exactly that fresh summary and renders
  the *body* against it, while the *header* came from the frozen tuple. So this is not new
  analysis; it is the header being the same reading the body already is.
  (1) **`frameproc.resign`, after the last pass that moves a statement** (`frameprog.program`,
  below rung (g)). It re-derives `params`/`rets` at the build's own fixpoint — that
  summarize-until-stable loop is now `frameproc.summaries`, one function with two callers
  instead of two copies — and writes them back into the procedure tuples.
  (2) **The arguments move with the header, and only where they must.** The first cut respelled
  every `pcall` argument as `("loc", param)` and the first test that saw it said no:
  `test_parameter_and_return_inference` reads `a = sub_2000($05)`, a constant the inline pass
  folded into the argument, and a blanket respelling puts the local back — text quality lost,
  and a definition the pruner removed could leave the local undefined. So `_respell_calls`
  keys the old arguments by the callee's *old* parameter names, keeps every surviving one and
  writes `("loc", p)` only for a parameter the refresh adds. A call whose callee's signature
  is unmoved is textually unmoved, which is why the corpus diff is the size it is.
  (3) **Nothing is promoted.** A raw `call` stays a raw call: callability is the build's
  reading, and re-promoting on a fresh one would change *how* a call is made rather than what
  it passes. `test_a_raw_call_is_not_promoted_by_the_signature_refresh` pins that.
  (4) **The corpus moved, and it is two shapes read off the diffs.** `emit_identity`:
  **624 tunes, 0 refused, 28,381,180 bytes**, aggregate
  `73824f53941b1d6494e93c6bb1ec5393821096ea99b59ad6fd47915553398d8e`, against #206's
  `101669fa…`/28,379,441 — **208 tunes moved, 130 larger, 78 smaller, none the same size, net
  +1,739 bytes**. The shrinks are a signature that **over-declared**: `Antics`'s `sub_C380`
  was `-> a, cflag, zflag, nflag` where only `cflag` survives its callers, so three names
  leave the header and every call site with them (`3_Tunes`, `Cool_Air` the same shape). The
  growths are a signature that **under-declared**: `Densetsu_no_Stafy-Coral_Reef` called
  `sub_1B58()` with an empty interface where the callee both needs and defines five registers,
  and it now reads `a, x, y, cflag, zflag = sub_1B58(a, x, y, cflag, zflag)`. `A_New_Kind` is
  the argument rule working: `sub_C3CB(m_C111[y])` keeps the folded expression at its
  parameter and gains `x` beside it, rather than being respelled back to a local.
  (5) **The gates, and the milestone.** `splice_sweep` is **0 bad of 624** — error, parse,
  lint, fixpoint, gate and sites all zero, `new: []`, `fixed: 1` — so the parse-and-evaluate
  gap **closes completely** and the zero-new law now guards an empty set. `gate_sweep` at full
  Songlengths is **624 build / 624 evaluate / 624 clean**, zero divergences and zero refusals:
  a truer header changes no register the machine writes. Suite **2,799 passed / 490 skipped /
  1 xfailed**, coverage 90.10%, `black --check` and `pylint` 10.00/10. `proved` 205,793 →
  **205,836** and `arch` 183,344 → **184,037**: the headline metric moves *up* by 693 tokens
  and that is the honest direction — a register interface the text left unspelled is now
  spelled, and `zero_arch` is unmoved at 2 of 624, `fields` at 17,662, `roled` at 12,825.
  (6) **`International_Karate` is pinned, on a cost that was measured rather than feared.**
  The tune is 645 s, 2.5x the longest pin before it, and every corpus-parametrized file runs
  every pin — the reason to hesitate. Measured, the whole hermetic suite goes **523 s → 577 s**
  with the pin and three new tests, and #206's corpus job ran 11m22s, so the guard costs about
  a minute and buys the exact tune the landing fixed as a CI assertion
  (`test_real_tune_international_karate_header_takes_its_live_in`, which calls `frameprog.emit`
  and so lints).
- **2026-08-12 — the headline metric measures what the pin is about: a version of a
  register is the register, and the emitter's own locals are a second residue.** The final
  wave's first landing is the instrument, not the lift. `splice_sweep.arch_shapes` counted
  eight bare names against a hand-kept `frozenset`, and the emitter has moved past it twice
  over.
  (1) **Two things went uncounted, and both are the pin's own subject.** The emitter copies
  a register into a version before it overwrites it (`frameproc._Names.fresh` on a register
  prefix), so `a0` and `cflag0` are the same machine value under a name the predicate did
  not spell; and `frameproc._REG_LOCAL` carries `iflag`, `dflag`, `bflag` and the unnamed
  register-file slots `g4`..`g7`/`g15`, none of which the `frozenset` had. The fix is to
  stop restating the alphabet: `_arch_re()` builds `(?:register)(version)?` from
  `frameproc._ALL_REG_LOCALS` itself, so a register the emitter gains cannot go uncounted.
  (2) **The temporaries are a residue too, and they are counted apart.** `t`/`w`/`q`
  (`frameproc`), `s` (`framestack`), `d` (rung (d2)) and the role prefixes `ptr`/`pos`/`ctr`/
  `idx` (`datadecl._aliases`) are the second thing a value flows through that is neither
  declared state nor a width-typed field. `temps` counts them — every bare `[a-z]+\d+` NAME
  that is not a register and not the grammar's own vocabulary (`u8`/`u16`/`u24`,
  `zext1`/`zext2`/`trunc1`/`trunc2`) — and the two counters stay separate because the rules
  that steer them are different: summing them would hide either behind the other. Read over
  the corpus the classification is total: **zero** bare prefix-and-counter names fall outside
  it, which is the evidence the alphabet is closed and not a sample.
  (3) **The tokenizer was wrong in the same direction.** The old `[A-Za-z_]\w*` splits the
  grammar's dotted `NAME`, so `sid.v1.freq` read as three words. The predicate now uses the
  grammar's own token, `[A-Za-z_]\w*(?:\.\w+)*`.
  (4) **The honest new numbers, corpus-wide, and the survey's estimate corrected.**
  Measured over all 624 cached artifacts **on the merge commit** (`3467a97`, after #206 and
  #207): `arch` **184,037 → 195,409** (+11,372, **+6.2%**), of which **10,724** are versioned
  copies and **648** are the three unmodelled flags and the unnamed slots; `temps` is
  **55,120**, a residue the headline never had a number for. **537 of 624 tunes move**; the
  median moving tune gains **5.2%** (`March`), the largest relative is `Invention_13` at
  **+47.4%** and the largest absolute is `50_Shades_of_Gradius`, **747 → 937** (+190). The
  final-wave survey's estimate of **20–40% per tune** was measured on three hand-picked tunes
  and does not hold over the corpus: the honest aggregate is 6.2% and only five tunes exceed
  26%. Measured first at `f9b2fbf` the same predicate read 183,344 → 194,716, reproducing
  #204's recorded `arch` exactly; the +693 between the two readings is #206/#207's own text
  movement, and the number recorded here is the merge commit's, per #204 (8).
  (5) **The headline itself does not move, and that is the finding.** `zero_arch` stays **2
  of 624**: the two tunes that wear no register wear no version of one either. Neither of
  them is at zero `temps`, so on the widened reading **no tune in the corpus is free of
  machine shape** — the pin's remaining distance is larger than the old number said, and it
  is now measured. `fields` 17,662, `roled` 12,825, `proved` 205,836, size −7,280 lines with
  no tune larger (586 smaller) and `bad` **0** — `International_Karate`'s standing lint left
  with #206/#207, not with this. This landing emits no byte: `emit_identity` on the merge
  commit holds at `73824f53…`/28,381,180.
- **2026-08-13 — a branch is the carry's own evidence: a flag it splits on is a constant in
  its arms, and the borrow chain beneath it becomes one word compare.** The final wave's
  second landing. The survey put the residue at `framemath._pairs` nominating destinations
  only; the measurement said otherwise, and the measurement is what landed.
  (1) **What was actually blocking, measured before anything moved.** The exemplar
  (`Grid_Runner`, the `$104A` arm) carries a 200-char `cflag` chain the survey said should
  spell `ptr_00FE:2 <= ((zext2(y) << $08) | zext2(a))`, and every lemma that spelling needs
  is already admitted — `borrow_fuse`, `borrow_word`, `sbc_borrow`, `carry_ult`,
  `carry_comm`. Instrumented over three tunes' artifacts, **every** SBC borrow chain carries
  a *symbolic* carry-in (`($01 - cflag)`) and the constant-carry-in form those lemmas match
  occurs **zero** times; `cflag = $01` is never emitted at all. The site was not
  un-nominated, it was un-matchable, and no nomination rule would have changed that.
  (2) **The carry is constant, and the branch is what says so.** The exemplar's own text:
  `cflag = ($02 <= w12)` then `ifnot cflag { … + cflag … } else { … ($01 - cflag) … }`. The
  ADC arm runs at carry **clear** and the SBC arm at carry **set** — the program's `BCC`
  decided the carry-in and the emitter re-read the flag as an unknown in both arms. A branch
  tests *nonzero*, so a guard that is its own truth value is a **constant** in each arm.
  (3) **The mechanism, and its soundness argument.** `eqlift_mem.render_proc` keeps
  `stt["bit"]`, a map from **SSA version** to the value an enclosing branch fixed, pushed for
  the `then` arm and inverted for the `else`; `conv` returns `num` instead of `loc` for a
  version the map holds. Keying on the version *is* the argument: a redefinition allocates a
  new version and every boundary havocs into one, so a fact cannot outlive the value it is
  about, and a label inside an arm — the one way another path could enter it — havocs every
  local before the fact could be read. No e-graph equality is asserted, which would be
  unsound: the graph is one graph for the whole procedure and the other arm reads it. A plain
  `x = y` is an alias, so `copy` carries the fact to `cflag0` as well as `cflag`. The guard's
  0/1-ness is `eqlift.bit_valued`, whose alphabet `framemath._bit` now shares rather than
  restates, and whose obligation is discharged in QF_BV rather than asserted: every compare,
  `carry` and `bnot` in `_Z3Alg` is proved `<= 1`.
  (4) **The rule, and its honest weight.** `wide_cmp` is one rewrite, Z3-proved over QF_BV
  like every other: at a carry-in of one the two byte borrows concatenate to
  `ule(pk(bh,bl), pk(ah,al))` — what the chain *tests*, where `borrow_word` states what it
  computes. Extraction takes it only where the pack is cheaper than the chain, which is where
  the operands really are words, and on the exemplar it produces the survey's spelling
  verbatim. Its measured weight is **near zero**: A/B'd against the path condition alone it
  moves `arch` by **+1** on `Grid_Runner` and by nothing on `Angry_Birds`, trading a shared
  temporary and a line for one more register token. It is kept because it states the relation
  the branch is asking, not because it moves the metric. **The reducer is the path
  condition**, and the two are recorded apart so neither borrows the other's credit.
  (5) **The equality generalization is measured and refused.** A guard whose flag is
  `INT_EQUAL(loc, const)` also fixes the compared local on the arm the equality holds. Built,
  and it moved **zero of 63** sampled tunes — byte-identical to the bit fact alone — while
  costing a sense obligation that had already produced one wrong answer in development: the
  fact belongs to the arm the *flag* is set on, which for `ifnot` is the `else`, and `gate_fp`
  caught the inversion on two tunes before it left the working tree. Withdrawn as
  measured-empty. It was never on main, so nothing regresses with it.
  (6) **The corpus, §4-reviewed.** `emit_identity`: **624 tunes, 0 refused, 28,365,174
  bytes**, aggregate `7a63a89f37370af29ad7b541ff11ef21529cd5b9b7a1ec26c2aced39bbd71e1d`,
  against the merge commit `978f31a`'s `73824f53…`/28,381,180 — the landing's own movement is
  **−16,006 bytes**. Measured once at `49a300a` and again after the rebase across #205/#207,
  the movement is the **same −16,006 bytes, the same `arch` −1,430 and the same `temps`
  −134**, so the path condition is orthogonal to the operator renaming and the header
  re-read: three landings touching one artifact, and their prices add.
  (7) **The gates.** `gate_sweep` **624 build / 624 evaluate / 624 clean**, zero divergences
  and zero refusals — a value the branch already decided is the same value. `splice_sweep` is
  **0 bad**: parse, lint, fixpoint, gate and sites all zero, **205,743 rewritten sites proved,
  zero unproved** (205,836 before — 93 fewer sites, because a folded carry-in is a site that
  no longer exists). Emitted size **−7,373 lines with no tune larger** (586 smaller) against
  the projection, `fields` 17,662 and `roled` 12,825 unmoved. `arch` **195,409 → 193,979**
  (**−1,430**, −0.73%) and `temps` **55,120 → 54,986** (−134). Suite **2,804 passed / 490
  skipped / 1 xfailed** hermetic; `black --check` and `pylint` (10.00/10 on `deity_informant/`
  and on `tools/`) clean. One rule is admitted, and `verify_rules` proves it.
  (8) **The pin does not flip, and its remaining owners are named rather than forced.**
  `zero_arch` stays **2 of 624**, and on the prototype's own `role_text` — the object the pin
  reads — **53** register tokens remain (`y` 26, `a` 23, `x` 4) with **25** temporaries beside
  them. They are one mechanism in four shapes, and none of them is a carry: **23** are a
  register *as the destination* of a load the fold left unnamed
  (`a = mem[((zext2(voice_pos_hi) << $08) | zext2(voice_pos_lo))]`), **18** are that same
  unnamed value read back on the right of an assignment (`voice_note = pitch[x]`, where `x`
  is a table index with no declared role in this projection), **8** are the branch predicate
  over it (`if ($00 <=s a)`), and **4** are the VM's own computed SID index
  (`sid.reg[a] = …`). The owner is **the prototype's render layer, still un-re-based**: it
  names a value only where `classify_roles` gives it a role, so an intermediate with no role
  keeps the register the fold left it in — where `frameproc._Names` would allocate a `t`/`w`/
  role-prefixed local. That is stage 4 landing 4's remaining part, not a rung and not the
  residue, and landing 1's `temps` counter is what makes it legible: the two projections
  spell **the same residue in two alphabets**, the engine's artifact for this image wearing
  `arch` 89 / `temps` 65 where the prototype wears `arch` 53 / `temps` 25. Forcing the pin
  would require naming those values, which is the re-basing, so the `xfail(strict)` stands
  and its owner is recorded here.
- **2026-08-13 — the trunk re-basing, part 1: the prototype's parser reads the artifact,
  and a width belongs to the site.** #193 measured the trunk's price as four parser items
  plus one blocker; this takes the four and answers the blocker, with the pipeline still
  reading the pre-rung text so the two dialects can be differenced rather than swapped.
  (1) **The four items, and what was already there.** `_suffix` and the statement regexes
  already tolerated `:2` after a parenthesis, an index and an assignment target, and
  `trunc1` already parsed as a generic call — so the gap was narrower than the survey said
  and narrower in a specific place. What was missing is the **signature** (`sub_1000(x) {`,
  `sub_1485(x) -> a, x {`: `proc_entries` matched `sub_XXXX {` alone, so the artifact
  presented *zero* procedures), the width suffix after a **bare name** (`ctr_0030:2`, the
  one position `_suffix` was never called from — `SyntaxError: trailing ':'`), the
  **promoted call** (`a, x = sub_1485(a)`, whose left side is not a single target) and
  `ret a, x`. `trunc1`/`trunc2` are now the dialect's own truncation in all three
  interpreters — `_z3_expr`, `_z3_eval` and `Machine._val` — rather than an unmodelled call.
  (2) **The blocker's answer: the width is recorded on the term, not on the name.** #193's
  name-keyed registry diverged at frame 0 because the artifact does not type names. Measured
  on this image's own artifact: **6** names are *read* at two widths (`zp_44`, `zp_4D`,
  `zp_64`, `zp_6D`, `zp_84`, `zp_8D`) and **15** wear two widths once write sites are counted
  with read sites (`m_0345`, `m_034C`, `m_0353`, `zp_49`, `zp_50`, `zp_69`, `zp_70`, `zp_89`,
  `zp_90` beside the six). So every expression node carries the width its **site** spells,
  the parser takes it from the suffix where there is one and from the dialect's own rule
  where there is not, and `_wid` is a read of the term rather than a re-derivation. The
  evaluator follows: a `name` or an `index` at width `w` is `w` little-endian bytes of RAM,
  and an indexed read marks every byte it spans as a row the run reached.
  (3) **The store's width is its own value's, at every assignment — measured, not assumed.**
  Over all **163** assignments of the artifact, the target's suffix equals the width of the
  term on the right in **163**, so the statement shape needs no second width and the
  invariant is a test rather than a convention.
  (4) **What it does not move.** The pipeline still folds the `eqlift_mem.emit` text: its
  emitted text, the artifact it now also carries (`art["artifact"]`, `frameprog.dumps` of the
  same program) and `role_text` are byte-identical to the base commit, and every proof kind
  still fires. `observed_extents` returns b0's rows rather than one consumer's reading of
  them, so `frameprog.program` gets rung (g)'s own input and `ptrextent.mapped_blocks` is
  taken at the eqlift call site; the round-trip witness is frame-identical over the lifted
  program. One correction rides along: `_wid` read a *signed* compare as its operands' width
  where `Machine._val` had always returned a byte, and the two now agree.
  (5) **The gates.** `emit_identity` **624 tunes, 0 refused, 28,365,174 bytes**,
  `7a63a89f…` — `--expect` reproduced, so the corpus does not move and cannot: no
  `deity_informant/` module is touched. Suite **2,807 passed / 490 skipped / 1 xfailed**
  hermetic (2,804 before; three new cases, no pin flipped). `black --check` and `pylint`
  clean. The pin stands with #208's owner: naming the residue is the next part.
- **2026-08-13 — the trunk re-basing, part 2: a register is a machine location, so what
  earns a name is the web — and the suite's last pin flips.** #208 named the owner of the
  53 remaining register tokens as "the prototype's render layer, still un-re-based", and
  named the mechanism as a local per definition where `frameproc._Names` would allocate
  one. Built and measured, the mechanism is that and one thing more, and the one thing
  more is what makes it an analysis rather than a rename.
  (1) **What a name is for.** A register is shared by every value that passes through it,
  so renaming `a` to `t9` would say nothing. What is one value is the **web**: the
  definitions that reach a common read. `name_locals` computes reaching definitions over
  `Flat`'s own CFG — the one `dead_local_defs` already uses — unions the defs at each read,
  and gives each web one local. On the prototype's `PLAY` before the folds there are **31**
  webs over 7 register names, so the pass *separates* far more than it renames: two
  independent loads of `a` are two values and get two names, while a loop-carried counter's
  `y = $00` and `y = (y + $01)` are one web and get one, because the back edge makes each
  reach the other's read.
  (2) **What it refuses, which is the reason a green assertion means something.** Three
  kinds of web get no name: one live where the procedure begins (a live-in the ABI owns,
  not the body), one an opaque `call` defines (the callee's, spelled by no statement here),
  and one whose sites do not agree on a width — the last resting directly on part 1, since
  the width is the site's and a web read as a byte at one site and as a word at another is
  not one width-typed quantity. On this artifact **none** of the three fires
  (`art["unnamed"] == []`), and the pin asserts that alongside its own predicate, so the
  text cannot go clean by a web being quietly left alone.
  (3) **The flip, on the engine's own instrument.** `splice_sweep.arch_shapes` — the
  widened predicate the final wave's first landing built — reads the prototype's `role_text`
  at `arch` **53 → 0** and `temps` **25 → 53**, reproducing #208's recorded 53/25 exactly
  before the change. The four shapes close together because they were one mechanism: the
  unnamed load destination, its read-back, the branch predicate over it and the VM's
  computed SID index are all reads of one web
  (`voice_t6 = mem[…]` / `if (voice_t6 <s $00)` / `sid.reg[voice_t6] = …`). The trade is
  honest and is the same one landing 1 measured on the engine: the residue moves alphabet.
  It is not the same *claim*, though — a temporary is a definition a reader can follow to
  its one value, and a register is a location, which is what the pin has said since #180.
  (4) **The pin flips, and the suite reaches ZERO xfails.** `2,804 / 490 / 1` becomes
  **2,809 passed / 490 skipped / 0 xfailed** hermetic. `test_every_pin_names_the_landing_
  that_flips_it` now asserts the enumeration is *empty* and keeps #180's law for anything
  re-pinned later. Nothing was forced: `art["unnamed"]` is empty, the frame projection,
  the write-application grid, the independent engine, the sidplayfp/sidtrace oracle and the
  round-trip 6502 witness all hold unchanged over the renamed program — which is what makes
  the renaming semantics-preserving rather than asserted.
  (5) **The ratchets go down, not up.** `LINE_PIN` **324 → 311** and `COST_PIN` **773 →
  759**, both re-pinned at the measured value. The rendered line count does not move at all
  (311 either way): the pass renames, it does not add statements. Re-rolling is unmoved —
  voices 1 and 2 still unify, voice 3 still refuses by its filter block, and the per-voice
  webs alpha-rename through `_voice_name`'s existing local branch, so the loop's declared
  parameters are what they were.
  (6) **What is still owed — measured here, so part 3 starts from numbers.** The pipeline
  still folds `eqlift_mem.emit`'s text, so `emit`/`emit_mem` and `eqlift_annotate` do not
  retire yet. Two measurements taken on this commit say what part 3 actually costs, and
  both say it is a **rewrite of what the example claims**, not a port.
  *The fold layer.* Handing `fold()` the artifact's own procedures fires **`wide_cmp`
  alone, 3 proofs**, against the 48 the eqlift path produces across all eight kinds — #193
  predicted three of eight and the honest number on this image is one of eight, because
  rung (d) has already fused every pair and every wide update the prototype was proving.
  So `FOLDS` shrinks to what the engine leaves, and `test_folds_all_proved`,
  `test_carry_outlives_its_add`, `test_portamento_is_one_wide_compare` and
  `test_note_fetch_is_one_u16_row_read` stop being about proofs the prototype discharges
  and become about statements the artifact already carries.
  *The roles.* Resolving the artifact's `symbols` aliases, its declared roles and
  `classify_roles`'s derivation share **35** cells and **agree on 23**; the **12**
  disagreements are one shape — the engine reads `zp_36`/`zp_37`/`zp_38`, the `_43`/`_63`/
  `_83` counters and the `_40`/`_60`/`_80` pointer lanes as `cursor` where the prototype
  derives `accumulator` or `parameter`. So "read the artifact's roles instead of
  re-deriving" is a decision about **which reading is right**, and whichever wins moves
  `test_roles_are_the_plan_s_own_and_the_field_line_is_the_dialect_s`'s per-voice
  assertions. That is the trunk's part 3, and it is scheduled with the fold rewrite because
  the same tests move with both. The pin does not wait on either and no longer names them.
  (7) **The gates.** `emit_identity` **624 tunes, 0 refused, 28,365,174 bytes**,
  `7a63a89f…` reproduced under `--expect`: no `deity_informant/` module is touched and the
  corpus does not move. `gate_sweep` at full Songlengths **624 of 624 clean**, zero
  divergences and zero refusals. `splice_sweep` **0 bad**. `black --check` and `pylint`
  clean.
- **2026-08-13 — the trunk re-basing, part 3 (part): the header is the binding, and a
  store is as wide as the value it stores.** Two of part 3's own mechanisms, taken apart
  from the fold rewrite because neither depends on it and both are measured on the
  artifact today. Neither moves an emitted byte on either path.
  (1) **`resolve_calls` resolves the promotion.** A leaf callee reached by
  `a, x = sub_1485(a)` was silently *retired* while its site stayed — the walk matched
  `("call", entry)` alone, so feeding it the artifact produced a dangling call. The site
  is now substituted the way its header reads: the parameter copies
  (`sub_1485(x)` against the argument `a` is `x = a`), the callee's own statements, then
  the returns read back where the targets differ from them. Two things refuse rather than
  guess: a parameter copy that would overwrite a register a later argument still reads,
  and a header that does not agree with the callee's own `ret`. And a callee any site
  still reaches **stays**, so a refusal can no longer leave a call with no body — which is
  what the negative case asserts.
  (2) **The store takes the site's width.** `Machine` wrote one byte for every `asg`,
  whatever the value's width, and truncated every local to a byte. It now writes
  `_wid(rhs)` little-endian bytes to a cell, the same number to a SID sink (so
  `sid.v1.freq_lo:2 = …` is the two-register write it says it is), and keeps a local at
  its own width. On the eqlift path this changes **nothing** and cannot: measured, that
  text has **zero** cell or sink stores at a width other than one. On the artifact there
  are **32**, over 20 distinct destinations, so the evaluator could not have executed it
  at all before this.
  (3) **The gates.** Suite **2,811 passed / 490 skipped / 0 xfailed** hermetic (two new
  cases). `emit_identity` **624 tunes, 0 refused, 28,365,174 bytes**, `7a63a89f…`
  reproduced under `--expect`; `gate_sweep` **624 of 624 clean**, zero divergences and
  zero refusals; `splice_sweep` **0 bad**, `arch` 193,979 and `temps` 54,986 unmoved.
  `black --check` and `pylint` clean. What part 3 still owes is the fold rewrite and the
  role decision, both priced in the entry above.
