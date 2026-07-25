# Corpus status (full-length gate)

Full-corpus, full-Songlengths measurement (2026-07-25, play-boundary model +
structurer codec + named-state `sidprog` canonical text): 140 cached v1
tunes. Gates per tune: bit-exact cycle-stamped play log from the model AND
from parsed standalone sidprog text, parse/emit fixpoint, in-pipeline
`flatten(structure(model)) == CFG`, text smaller than the disasm listing.

**140/140 pass every gate** (574 tests, zero failures, zero skips). History
of the retired failure classes and the remaining tracked lemmas:

## Gate S status (readability)

Structural flow landed: region nesting IS the text's control semantics
(`if`/`else`/`loop`/`switch` with implicit fallthrough; labels only on
genuine dynamic landings; parsed text executes by tree, faster than the pc
walker). Computed-call handler bodies nest inside their dispatch arms.
`sidprog.metrics` after the goto-minimal pass (layout saturated to within
3 gotos of the single-emission floor; bounded <=3-store tail duplication
under the covered-with-verified-duplicates codec law): Commando 4 gotos /
3 labels; Krakout 96.7% / 112 gotos; Athena 97.0% / 509; Wizball 107
gotos. The former Ghouls/Follin ceiling (36.1% / 1,714 gotos over 3,103
blocks) was closure-materialization residue, not layout: transient
fixpoint-round dispatch target sets materialized blocks the final
envelope disowned (docs/follin-dispatch-study.md §1).
`collect_unreachable` now garbage-collects the model to the
final-closure reachable set after the last fixpoint + resplit:
Ghouls_n_Ghosts 627 blocks (556 executed + 71 static branch sides) /
99.8% / 82 gotos; Agent_X_II 344 blocks / 99.1% / 61 gotos; other tunes
unchanged. The remaining lemma is dispatch-index precision
(docs/soundness.md; 3 evidence sites per Follin tune unchanged).

Proc/ownership pass (`procpass.plan`, between the committed model and the
structurer): the call graph decides the proc structure — a sub with exactly
one call site whose entry the site dominates inlines at its call line
(`call $T ret $R { .. }`, the switch-call arm precedent generalized), dyn
single-site handlers keep the arm shape, and every block homes in the proc
whose entry dominates it over all callers (dominance-shared tails become
fragment procs with cross-refs). `sidprog.metrics` gains
proc_count/cross_proc_gotos. Athena 267 procs -> 2, cross-proc gotos -> 67,
97.0 -> 99.0% (506 total gotos remain: the two 256-wide guarded SMC-JMP
envelope case lists, bounded by dispatch-index precision, not layout);
Krakout 522 -> 4 procs, Automatas 5 -> 2, Army_Moves 16 -> 4;
Ghouls/Commando gotos unchanged (82 / 4).

## Retired model bogon (fixed by the commit-phase coverage rule)

Army_Moves `igoto` site $E093: `dyn_targets=[]` with a `proven` proof while
evidence observed 4 targets — a resolution still pending when the closure
rounds exhausted was accepted as an empty proven set. The commit-phase
uniform coverage rule (docs/soundness.md) now downgrades any such site to the
guarded evidence envelope; $E093 commits as `evidence` with the observed set,
alongside six sibling sites corpus-wide (Aces_High, Commando_High-Score,
Jammer-424 ×3, Bird_Flu) whose "proven" sets omitted observed targets.

## Class 1 — retired: guarded evidence envelope for unproven opcode cells

**Athena** (Galway) now decompiles bit-exact. Diagnosis: the closure computes
the `$6083` toggle's `{$2C,$4C}` correctly, but two `STA/SLO (zp,X)` computed
stores in the player never narrow (range `[0,FFFF]`) and statically may-hit
the cell with an unresolvable value, forcing ⊤. Unproven opcode-cell value
sets now fall back to the observed set under the same guarded envelope as
dispatch targets (walker faults on any other byte, `--sound` fails, proof
status `evidence`); the missing lemma — bounding the `(zp,X)` store pointers —
is tracked in docs/soundness.md. The envelope also surfaced a second Galway
toggle at `$C325` (`{$60,$A9}`) the hard failure had masked, and Athena was
the first tune whose SMC variants diverge in control flow, exposing (and
fixing) a first-variant-only successor bug in the CFG builder that the codec
check caught.

## Class 2 — retired: named-state canonical text

Bijective naming (`sid.vN.*`/`filter.*`/`zp_XX`/`m_XXXX`, indexed
`name[X]` arrays) shrank every tune's text; Agent_X_II (the last size
straggler, +0.6%) now emits 340,568 B vs 359,001 B disasm.

Runtime budget: worst full-length single-tune tests now ~95-106s
(Gauntlet_III, Chester_Field, Artura) against the 60s single-process target;
decompile-side windowing is the tracked fix.

## Evidence-guarded dispatch (tracked, not failures)

Bionic Commando ×3, Comic Bakery ×4, Wizball ×3 — guarded, bit-exact,
blocked on dispatch-index precision alone (docs/soundness.md).
