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

## Open lemmas

Corpus-wide the remaining `evidence` sites (Bionic Commando ×3, Comic Bakery
×4, Wizball ×2) are all **self-modified `JMP` vectors**: the target is
`mem[p] | (mem[p+1] << 8)` with the two operand bytes patched in one block from
parallel tables indexed by a shared register `A`. Independent per-cell
resolution takes the Cartesian `{lo} × {hi}` product and overflows the budget.
Closing them soundly needs two composed lemmas, diagnosed from the traces:

1. **Relational vector closure.** `A`'s value set is already bounded by table
   analysis; the fix is to substitute each operand cell's unique in-block store
   and jointly enumerate over `A` (evaluating the whole target expression per
   value) instead of the per-cell product — correlated, so the pair set is
   `|A|`, not `|A|²`. Sound because the register set over-approximates and the
   table reads are immutable.
2. **Bounded-recurrence store-pointer analysis.** Lemma 1's soundness needs
   proof that no computed store aliases the operand cells `p`, `p+1`. In these
   tunes the aliasing writer is a page-incrementing buffer fill
   (`STA (ptr)` with `ptr`'s high byte `INC`-ed each pass); its address widens
   to ⊤ under the value-set fixpoint, so it *could* reach `p`. Proving its
   destination stays in its buffer range requires bounding the increment by the
   loop's trip count.

Until lemma 2 lands, lemma 1 must refuse (the immutability precondition is
unproven), so these sites stay `evidence` — precisely diagnosed, not silently
accepted.
