# Corpus status (full-length gate)

Full-corpus, full-Songlengths measurement (2026-07-25, play-boundary model +
structurer codec + `sidprog` canonical structured text): 140 cached v1 tunes.
Gates per tune: bit-exact cycle-stamped play log from the model AND from
parsed standalone sidprog text, parse/emit fixpoint, in-pipeline
`flatten(structure(model)) == CFG`, text smaller than the disasm listing.

**139/140 replay bit-exact standalone at full length; 138/140 pass every
gate.** Block re-splitting (`Model._resplit`) plus per-block let-bound CSE in
the sidprog emitter retired the expression-duplication size class (was 6
tunes up to 74 MB; e.g. Ghouls_n_Ghosts 4.2 MB -> 505 KB, Artura 74 MB ->
86 KB) and its runtime breaches (Artura 336s -> 94s, Chester_Field 242s ->
95s). Remaining:

## Class 1 — opcode-SMC value-set closure to ⊤ (1 tune)

- **Athena** (Galway): play-phase `$6083` toggles `$2C`/`$4C` (BIT/JMP skip
  idiom); the stores reaching the cell don't close. Same two-value class the
  closure already proves on Automatas — a precision bug, not a new mechanism.

## Class 2 — size-gate straggler (1 tune)

- **Agent_X_II** (T. Follin): sidprog 361,023 B vs 359,001 B disasm — 0.6%
  over an unusually small disasm baseline; expected to fall out of emitter
  polish (naming/inlining), tracked rather than chased.

Runtime budget: worst full-length single-tune tests now ~95-106s
(Gauntlet_III, Chester_Field, Artura) against the 60s single-process target;
decompile-side windowing is the tracked fix.

## Evidence-guarded dispatch (tracked, not failures)

Bionic Commando ×3, Comic Bakery ×4, Wizball ×3 — guarded, bit-exact,
blocked on dispatch-index precision alone (docs/soundness.md).
