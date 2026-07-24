# Soundness accounting (decompiler §4)

Every computed control-transfer and self-modification site carries a **proof
record** — the migration step that turns the observational prototype into a
sound-by-construction build (docs/decompiler-implementation.md §4.5, §9.3).

## Proof records

`structured.Proof(site, kind, status, targets, lemma)`:

- `kind`: `jump` (`jmp abs` self-patched), `vector` (`jmp (ind)`), `branch`
  (computed branch), `call` (computed `jsr`), `opcode` (SMC opcode cell).
- `status`:
  - `proven` — the target/opcode set is statically bounded (value-set closure);
    `targets` is that proven set.
  - `evidence` — static closure gave up; only the trace-observed set bounds the
    site. `lemma` names the missing static argument (the cell/pointer and the
    analysis that overflowed). These are the tracked Gate-A missing lemmas.
  - `failed` — unprovable and never observed (dead site).

`model.proofs` maps each site pc to its `Proof`.

## Report and strict mode

- `structured.proof_report(model)` → serialisable `{tally, sites}`;
  `structured.format_report(model)` → text. CLI: `decompile --report`.
- `decompile(..., sound=True)` / `decompile --sound` fails the build with a
  site-specific diagnostic if any `evidence` site remains — the loud failure
  §4/Gate A require instead of a silently guarded envelope.

Without `--sound` the guarded evidence envelope is kept (the standalone walker
still faults on any unobserved target), so the default build is unchanged while
the report makes every non-proven site auditable.

## Open lemma

Corpus-wide the remaining `evidence` sites (Bionic Commando, Comic Bakery,
Wizball) are **self-modified `JMP` vectors**: the target is
`mem[p] | (mem[p+1] << 8)` with the two operand bytes patched from parallel
tables indexed by a shared register. Independent per-cell resolution takes the
Cartesian `{lo} × {hi}` product and overflows; the sound closure is
**relational evaluation over the shared index** (joint enumeration), which
proves the exact reachable pair set when the index is bounded.
