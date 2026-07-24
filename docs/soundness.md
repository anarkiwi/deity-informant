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

## Relational vector closure (lemma 1, implemented)

Corpus-wide the remaining `evidence` sites (Bionic Commando ×3, Comic Bakery
×4, Wizball ×2) are all **self-modified `JMP` vectors**: the target is
`mem[p] | (mem[p+1] << 8)` with the two operand bytes patched in one block from
parallel tables indexed by a shared register `A`. Independent per-cell
resolution takes the Cartesian `{lo} × {hi}` product and overflows the budget.

`Analysis._relational_targets` closes these soundly: it substitutes each operand
cell's unique in-block store (`_live_expr`), then jointly enumerates the shared
index registers over their value sets and evaluates the whole target expression
per assignment (`_eval_live`) — correlated, so the set is `|A|`, not `|A|²`.
Sound because the register sets over-approximate and the substituted table reads
are immutable; it refuses (raising a precise diagnostic) on a ⊤ index, a
volatile leaf, a non-unique operand store, or a domain over 4096.

## Open lemma: the aliasing precondition (lemma 2)

Lemma 1's `_live_expr` may only substitute an operand cell `p` when no computed
store can alias it (`_unique_store` / `_store_may_hit`, via tight intervals).
In all three corpus tunes the aliasing writer is a page-incrementing buffer fill
(`STA (ptr),X` with `ptr`'s high byte `INC`-ed once per pass of a `DEY`/`BPL`
copy loop); its address widens to ⊤, so it *could* reach `p`, and lemma 1
correctly refuses — these sites stay `evidence`, precisely diagnosed.

Discharging it soundly (bounding the store pointer away from `p`) needs a stack
of composed analyses, worked out from the traces:

1. **Loop-trip bound.** The increment `H = H + 1` and a counter `Y = Y − 1` live
   in one block, so `H + Y` is loop-invariant; with the back-edge and header
   from the dominator tree and `Y`'s entry value `Y₀` from the non-loop
   predecessors, `H ∈ [c, c + Y₀ + 1]`.
2. **Definite assignment.** The interval must exclude `H`'s initial image byte:
   prove the `c` store dominates every read of `H` (else the image value is
   live-in and re-widens the bound past `p`).
3. **Interprocedural index bound.** `Y₀ = table[X]` needs `X` (the caller's song
   index) bounded to read the immutable page-count table.
4. **No-wrap.** `c + d·trips ≤ mask`, else the high byte wraps below `c` and can
   reach `p` again.

This is the spec's core value-set rewrite (§4.1–4.3); each step is
soundness-critical, so it is tracked as the missing lemma rather than
approximated from the trace.
