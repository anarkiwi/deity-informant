# Corpus status (full-length gate)

First full-corpus, full-Songlengths measurement (2026-07-24, play-boundary
model + structurer codec): 140 cached v1 tunes, acceptance (bit-exact
cycle-stamped play log from model AND parsed standalone text) + structurer
faithfulness (in-pipeline `flatten(structure(model)) == CFG`).

**139/140 decompile and replay bit-exact at full length. 133/140 pass every
gate.** The failures, by class:

## Class 1 — opcode-SMC value-set closure to ⊤ (1 tune)

- **Athena** (Galway): play-phase `$6083` toggles `$2C`/`$4C` (BIT/JMP skip
  idiom); the stores reaching the cell don't close. Same two-value class the
  closure already proves on Automatas — a precision bug, not a new mechanism.

## Class 2 — text-size gate: expression-DAG duplication in emission (6 tunes)

Bit-exact, but emitted text exceeds the disassembly listing because shared
subexpressions print as duplicated trees (no let-binding/CSE in the emitter):

| Tune | text | disasm |
|---|---|---|
| Agent_X_II (T. Follin) | 1.4 MB | 359 KB |
| Aiginas_Prophecy (G. Follin) | 515 KB | 283 KB |
| Cosmic_Storm (G. Follin) | 864 KB | 667 KB |
| Ghouls_n_Ghosts (T. Follin) | 4.2 MB | 523 KB |
| Chester_Field (G. Follin) | 63 MB | 316 KB |
| Artura (Daglish) | 74 MB | 960 KB |

Fix lands with the structured-language emitter (load-slot retention /
let-bound shared subexpressions). The same blowup drives the two
runtime-budget breaches (Artura 336s, Chester_Field 242s vs the 60s
single-process budget).

## Evidence-guarded dispatch (tracked, not failures)

Bionic Commando ×3, Comic Bakery ×4, Wizball ×3 — guarded, bit-exact,
blocked on dispatch-index precision alone (docs/soundness.md).
