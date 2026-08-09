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
pivot: `gate_sweep` at full Songlengths **622 built / 621 clean** (standing
exclusions: `Rambo_First_Blood_Part_II` divergence; `C64_World` and
`1st_Decent_Hardcore` evaluation faults); byte-identity aggregate
`99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942` over the
624 built tunes (recipe: per tune, sha256 of
`frameprog.dumps(frameprog.program(model))` at full Songlengths; aggregate
sha256 of `"%s %s\n" % (tune_id, sha)` joined over tunes sorted by id);
census sum 30,854 — **retired as a steering metric**, kept as a diagnostic.

| label | what landed, where | role under this plan |
|---|---|---|
| Phase 0 | `tools/storage_census.py`; wide-store classes in `fuse_measure`; `sid_readback`/dyn counts in `lift_residue`; `tools/disasm_tune.py` | diagnostics; the claims discipline's instruments |
| Phase 1 | rung (d0s) in `framestack.py` — sp spills become locals; per-procedure refusal ledger | landed lift, stands |
| Phase 2a | `ptrcert.py` block-rooting certification; `frameproc.addr_floor` (must-set bits) | certification accounting; `addr_floor` feeds stage 3's intervals |
| Phase 2b (b0–b5) | `ptrextent.py` + `out/ptr_extents*.json` (observed extents); rung (g) in `ptrlift.py` (251 webs lifted, 1,000 ⊤ loads retired); extent faults in `frameval` | landed lift; the guard pattern stage 3 inherits |
| Phase 2c | `framestack._SpFlow`/`_balances` — interprocedural balance as a call-graph fixpoint; `sp_linked` on displacement | landed lift, stands |
| Phase 2.5 | `tools/value_walk.py` — strided-interval walker and **the in-edge map** (closes 6,350 of 7,278 labels, 584 of 624 tunes; a raw `call` into the calling list is an in-edge) | feeds stage 3's joins and disjointness |
| Phase 3a | frameprog total artifact (`image`/`dispatch`/`evidence`); content-keyed decompile cache in `tools/_sweep.py` | the substrate; a package-file edit costs one cold sweep |

Operational facts that bind: the suite runs `-n 24` (`-n auto` hits the
container fork limit), two passes (`-m "not oracle"`, then `-m oracle`); the
sweeps take `-j`/`--procs`; `gate_sweep --extents` MUST be given the
`--frames full` artifact, and `out/ptr_extents*.json` MUST be regenerated
before reading a `foreign`/`short` split off it; the 60s per-script budget
stands.

## The method

**Derive from the canonical players, not from 624 binaries.** Most of the
corpus is a handful of players, and their ground truth is published: the
trackers ship their own player source (GoatTracker, SID-Wizard, defMON), the
hand-coded families circulate as commented community disassemblies, and the
Follin driver's development disks are already in the reference set
(docs/follin-dispatch-study.md). The SID binaries serve two purposes only:
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

**The roles are the expected output, not a license.** Read forward, a play
routine's persistent state resolves into four roles — **cursor** (an index
into a declared block, advanced monotonically), **accumulator** (a value
stepped by a delta within a bound), **counter** (a countdown gating a step),
**parameter** (set by a cursor event, read by steppers) — plus the VM
registers of script-interpreter families. The vocabulary is closed by the
chip, not by enumeration: the SID lacks hardware for vibrato, portamento,
PWM/filter sweeps, arpeggio and fades, so that is what drivers synthesize.
After minimization the role is read off each surviving cell's update term
(`s' = s + k` feeding a deref is a cursor; a bounded `s' = s ± d` is an
accumulator). Recognition licenses nothing — the guards and the proofs
license; roles name.

## Stage 1 — the catalog (canonical sources -> idiom inventory)

Pull the canonical player for each family, disassemble/read it, and write the
idiom inventory the rule set will be tested against.

| family | corpus exemplar | canonical source |
|---|---|---|
| Hubbard (hand-coded) | `Hubbard_Rob/Commando` | community-commented driver disassemblies |
| Galway (hand-coded, per-voice code) | `Galway_Martin/Comic_Bakery` | community-commented driver disassemblies |
| goto80 / defMON-line | `Goto80/Automatas` | defMON player source |
| GoatTracker export | `Cadaver/Aces_High` | GoatTracker 2 player source |
| SID-Wizard export | `Chabee/Angry_Birds` | SID-Wizard player source |
| Follin (script interpreter) | `Follin_Tim/Ghouls_n_Ghosts`, `Agent_X_II` | Follin development disks (reference set) + docs/follin-dispatch-study.md |

The seven-tune evidence set stands as the exemplar set; a family is added
only if clustering by executed-code fingerprint (the recorder's run
signature, made relocation-tolerant: opcode shingles, not absolute addresses)
surfaces one the seven miss. Per family, the canonical source is
cross-checked against its HVSC exemplar under the oracle before any idiom is
recorded from it.

**Deliverable: `docs/idiom-catalog.md`.** One row per idiom: the
canonical-source citation (player, label/address), the `disasm_tune` cite in
a corpus exemplar, the families carrying it, and **the normal form it must
reduce to** (a dialect term) or `named-unknown`. Existing enumeration data
feeds in rather than being redone: docs/twobyte-lift.md's 610-shape pair
enumeration, the §5.4 shredder fixture family, ptrcert's definition-kind
census, the follin-dispatch-study grammar.

**Completeness is checkable and MUST be checked**: in each exemplar, every
SID-store dataflow slice and every frame-surviving cell is accounted to a
catalog row. The catalog, not a corpus census, is thereafter the claim of
coverage.

The Follin entry carries the debt: `_ARITY` is a hand transcription; the
catalog records the mechanical definition (an operator's arity is the net
`Y` delta of its dispatch arm, constant on all paths) and stage 3 recovers
it.

## Stage 2 — the vocabulary (capability before use)

The grammar and evaluator learn to spell and execute what extraction will
choose, with **no rewrite landing alongside**: wide (u16) locals and pair
cells (frameprog.md's M-FP3), the cursor forms rung (g) already ships, and
role-typed `state { }` declarations
(`cursor`/`accumulator`/`counter`/`parameter`/`vm`).

Gates: emitted text byte-identical corpus-wide (capability, zero use);
`dumps(loads(t)) == t` over every artifact; Gate FP untouched; hermetic
evaluator tests for every new form.

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
- **Retirement.** Adoption §5's transitional passes actually retire as
  subsumption lands, and extending them is forbidden — enforced from this
  document forward.
- **The debt discharged.** VM-family operator sets fall out per dispatch
  arm: arity is the net `Y` delta, the effect set is the cells written, an
  arm that rewrites the pointer is control. `follin_script._ARITY` is
  deleted when its family's recovered arities equal the transcription — the
  executable test that the mechanism is real.

## Stage 4 — gate + emit (the state machine is the artifact)

Per landing: the full suite; the full-corpus Gate FP sweep; the seven
exemplars at full Songlengths with their emitted-text diffs read by hand.

Steering metrics, replacing the census: **extracted term cost / emitted
size, falling; persistent cells role-named, rising toward all.** The census
signatures remain available as a diagnostic only.

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
