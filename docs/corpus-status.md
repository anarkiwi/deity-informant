# Corpus status (full-length gate)

Full-corpus, full-Songlengths measurement (2026-07-25, play-boundary model +
structurer codec + named-state `sidprog` canonical text): 140 cached v1
tunes. Gates per tune: bit-exact cycle-stamped play log from the model AND
from parsed standalone sidprog text, parse/emit fixpoint, in-pipeline
`flatten(structure(model)) == CFG`, text smaller than the disasm listing.

**140/140 pass every gate** (574 tests, zero failures, zero skips). History
of the retired failure classes and the remaining tracked lemmas:

## Gate S baseline (readability metrics, no threshold gated yet)

`sidprog.metrics`: Commando 94.5% structured / 5 gotos (110 blocks); Athena
96.6% / 532 gotos (526 blocks); Agent_X_II 52.0% / 839 (1,809 blocks);
Ghouls_n_Ghosts 33.8% / 1,794 (3,103 blocks). The Follin-family players are
the Gate-S (>=95%) work: goto-minimal structuring over their reconverging
state machines.

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
