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
- **The `low_held_cursor` rung landing.** Its premise is exactly the deref span
  landing 1's read closure computes, but its consumer is `ptrcert` — a rung landing,
  not stage 3's.
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
per-voice unified where the isomorphism is total (else the copies stay); VM
families emit their operator sets. The witness, when wanted: re-emit minimal
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

**The position (2026-08-10), and the next landing.** Landed: **landing 1**, the
CyberTracker continuation — the corpus gate is clean for the first time since the
plan opened, the three `jsr_inline_skip` pins flipped on their own mechanism, and
the emit baseline moved on a reviewed §4 diff of exactly two tunes; and two parts of
**landing 2, adoption §8 step 4's cutover** — the narrowing `COPY` is a term
(`eqlift.trunc`), so the splice reaches the rules, and then the dialect gains the
signed compare while the unified renderer learns the layout, the dispatch headers and
the statement set it prints, `pcall` included; and then, on the canonical example,
**the cutover closes**. Both halves are landed: `frameval.gate_fp` on the spliced text is
`None`, so the unified emitter's program is the walker's projection, and the text is a
`dumps`/`loads` fixpoint, because `eqlift._Printer` now reads an address at
`frameproc._index_of`'s breadth, routes a register-file base through the `sid.reg` view,
and takes the ONE `_PAIRS` registry so a declared lo/hi pack reads its word column.

**Step 4's unconditional path is LANDED: `frameprog.dumps` renders through the unified
graph, and the `state { }` demotion the switch gave a subject is landed with it.** An analysed program (`frameprog.program`) carries `landings` and renders through
`eqlift_mem.artifact_lines`; a parsed one carries none and renders through
`frameproc.render_lines`, so `dumps(loads(t)) == t` is a gate on the unified emitter rather
than an accident, and `frameprog.render_lines(prog)` is the replaced projection kept as the
control. The import cycle took the **first** of #181's three answers, not the third: the
`state { }` emitter moved to `sidprog` and `_decl_pairs` to `datadecl`, because measurement
showed the third — retiring `emit`/`emit_mem` — cannot land here. `emit`'s one consumer is
`examples/state_machine_lift.py`, the acceptance gate, whose parser fails on the artifact's
first statement and whose fold layer is stated over byte lanes rung (d) has already fused;
re-basing it is landing 4's subject. `emit_mem` is named in code as what it is — the
prototype's pre-rung substrate, one consumer, retiring with that layer — and
`tools/eqlift_emit.py`/`eqlift_measure.py` are deleted with the 25-exemplar review, which
moves to the corpus. §5's `eqlift_mem` liveness deletions **are landed**; the saturation
schedule is **a round cap and a node bound**, so no clock reading reaches the artifact.
§5's `_Prune`/`_inline` are **not** subsumed and the reason is measured: `procedures` and
`repolish` run them before rungs (d), (d2), (f) and (g), which pattern-match the polished
statements, so their deletion is a rung-input change gated by `gate_sweep` plus a full
emit-identity diff. The `returns` set landing 1 owed is **refused, with its reason
measured**: the only procedures it can relax are the ones `slot_reader` blocks, and those
return to a pc the call site does not name, so the sound mechanism is a `framestack`
reading of each site's resume pc rather than an `_Info` relaxation.

**Landing 2 is closed (#173-#177); landing 3's emitter was made corpus-worthy (#179) and
then switched on (#182).** The landings are renumbered from here: **3** is the
unconditional path (adoption §8 step 4), **4** role-typed emission + steering metrics,
**5** the witness completed, **6** the housekeeping and the stage close.

The switch found two faults, both fixed with the landing. A store forwarded into a
**volatile** read (`m_D019 = $81` then `$D019` read back `$81`, where it reads zero):
`eqlift_mem._may_read_vol` now serves any load whose address may be a volatile cell from a
fresh opaque memory, so neither the chain nor the graph's sharing reaches it. And a word
stored then read lane by lane came back as its own repack, because no rule stated the dual
of `pack_hi`/`pack_lo`: `pack_split` is admitted at width 2 and Z3-proved with the other 90.
One prototype-family pin flipped (`test_borrow_chain_is_one_wide_compare` — the wide compare
is an artifact fact; the shredder family is 23), and one law weakened on the record: M-FP2's
`prog.procs == src.procs` is now the entry/parameter/return identity, because the text is
the minimized program.

The baselines a successor starts from: emit identity **624 tunes, 0 refused, 28,258,539
bytes**, aggregate `64f763d93ebf3b1edcc11310b3ef6be6a3818dad517026b8f1874125827a7b2b`;
`gate_sweep` at full Songlengths **624 build / 624 evaluate / 624 clean**, zero divergences
and zero refusals; suite **2,748 passed / 490 skipped / 29 xfailed**, oracle 16; the corpus
text gate `tools/splice_sweep.py` (`out/splice_s4l3b.json` against its control
`out/splice_base_s4l3b.json`) **84 bad against the control's 87 — zero new, three fixed**,
parse and fixpoint **624 of 624**, **210,034 rewritten sites proved, zero unproved**,
−4,529 lines with no tune larger; prototype ratchets **324 lines / 773 nodes**. The
25-exemplar review and `out/eqlift_measure_s4l3.json` are retired with the tool that made
them. Stage 4's headline metric — tunes wearing zero machine shapes — has **not** moved and
cannot until landing 4 turns the role keywords on; what the switch moved is which emitter
ships.

**The open items, each with its mechanism, as the switch leaves them.**
- **§5's `_Prune`/`_inline` deletion** (landing 3, next part): not a rendering change but a
  rung-input change, since rungs (d), (d2), (f) and (g) pattern-match the polished
  statements. Its gate is `gate_sweep` plus a §4-reviewed emit-identity diff, and rung
  (d2)'s per-site e-graphs go with it only where the same admitted rules fire in the
  per-procedure graph.
- **The `state { }` demotion is LANDED**: root extraction's `_scratch` spans are threaded
  out of `artifact_lines` and `dumps` drops a field a demoted span covers that no emitted
  line names. `test_scratch_cell_is_a_local_not_state` flipped (the shredder family is 22);
  the prototype's `test_state_block_holds_no_scratch` does **not** flip, because its subject
  is `sml.render`'s own state block and not the artifact's — it re-points at landing 4 with
  the prototype's fold layer.
- **A second declaration-truth gap: the state block and the emitter disagree about width.**
  On the canonical example nine `u8` fields (`zp_31`, `zp_41`, `zp_4C`, `zp_61`, `zp_6A`,
  `zp_6C`, `zp_81`, `zp_8C`, `zp_93`) are the *hi* halves of pairs the unified graph fuses
  (`ctr_0030:2`) and rung (d) did not, so the block declares two bytes where the text reads
  one word. The mechanism is rung (d)'s pair premise, which is stricter than the graph's;
  the reading is `framefuse.apply_rung`'s `state` result against the fused stores the
  artifact emits, and it is the demotion's neighbour, not the demotion.
- **A multi-reader memory forward is re-spelled per site.** `Cuomo_Jim/Cage_Match` grew 831
  bytes on five fewer lines because the PLP status word `m_01FD` is stored once, read three
  times, and each read re-spells the whole seven-term rebuild. `_share_once` states the rule
  for a multi-read local; the forward needs it across roots. Owner: landing 4, whose
  headline metric is emitted size.
- **The parse-and-evaluate gap, measured and still unowned.** 87 of 624 tunes fail on the
  replaced projection and **84 on the artifact** — the emitted text, parsed back and
  evaluated, faults or diverges where the analysed program does not (25 errors, 9 `lint`, 50
  divergences; zero new against the control, three fixed). 3a's totality claim is about the
  *cache* round trip, which `emit_identity` exercises and which holds; this is a different
  claim and it does not. With the switch, text validity is gate-critical and
  `tools/splice_sweep.py` is the standing per-landing gate that sees it; the diagnosis is a
  per-tune bisection of the kind #179 ran, and **no landing owns it yet** — it must be
  taken or refused by name at the stage close.
- **`ret_live` for a slot-rewriting callee** (landing 1's owed, #177's refusal): a
  `framestack` reading of each call site's resume pc — call site plus inline-data length,
  where `lift_rts_trick` concretizes `sp` — unioned as the live-in. Not an `_Info`
  relaxation; the shredder's four-fixture family already carries every spelling.
- **The `_cell_decl` extent/`mut` defect** (#178 (3)): `table X[1] mut 0` on a cell the
  text writes as `X[x]`, because `_cell_decl` reads `model.written` at the base alone. It
  is a declaration-truth defect of `_declare_cells` and owes its own measurement.
- **Landings 4-6** are as their sections state them, none begun: role-typed emission and
  the steering metrics, with the prototype's fold layer re-based onto the artifact (landing
  4); the witness completed — the raw `call`/`callb` and static-image-vector refusals, the
  signed compare over unequal operand widths, the three `Asm` copies onto `asm6502.py`
  (whose tables differ semantically: `_fuzzgen` admits illegal opcodes and resolves
  duplicate legal `(mn, mode)` pairs to the highest byte where `asm6502` takes the lowest,
  so the merge moves fixture bytes), the 25-exemplar VM sweep (landing 5), and the
  song-model retirement, the `low_held_cursor` ptrcert rung and the stage close (landing 6).
  The `swc` in-edge join extension is **withdrawn**: it was measured against the artifact
  and is not what `dispatch_scratch_promotes` waited on (decision log, 2026-08-11).

## Independent housekeeping (blocks nothing)

- **The Follin arity table — discharged** (2026-08-10, stage 3d, one PR).
  `follin_script._ARITY` is deleted and `deity_informant/follin_arity.py`
  recovers what it held, per dispatch arm, off the lifted blocks: the stream is
  the pointer the dispatch's own fetch uses, the operator range is
  `[floor, floor + extent)` with the floor read off the guard that dominates
  the dispatch and the extent the tightest spacing of the paired handler tables
  (`Ghouls_n_Ghosts` 21 slots `$80`–`$94`; `Agent_X_II` 17 slots `$80`–`$90`),
  the arms come from the paired table image, and an operator's arity is the
  arm's consumption footprint — the stream offsets it fetches, walked at each
  block's least `Y` so the reading does not depend on where the lifter cut. On
  Ghouls that reproduces **all 20** transcribed arities op for op;
  `tests/test_follin_arity.py` holds the transcription as the discharge
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
