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

## Commit phase and the uniform coverage rule (§2 step 2, §4.5)

`Model.build_all` separates ANALYSIS from program construction:

- **ANALYSIS** (`_analyze`/`_close_once`): hypothesis CFG + fixpoint closure;
  may speculate and materialize freely in the workspace block set; produces
  per-site `(kind, targets, lemma)` results and dispatch value sets only.
- **COMMIT** (`_commit`): constructs `proofs`/`dyn_targets`/`evidence_sites`/
  `dispatch_sets` and the final block set in one step — resplit at final
  entries, keep only blocks reachable from play under final edges — then the
  intra-block passes. Blocks are immutable afterwards (only `lookup` lazily
  adds static continuations).
- **Checker** (`check_commit`): one rule for every site class (opcode cells,
  vectors, computed jumps/calls/branches, rts-dispatch): observed evidence ⊆
  committed proven-or-evidence set; an empty or incomplete resolution at an
  executed site is never `proven` — it commits as the guarded evidence
  envelope with a tracked lemma; unexecuted+unprovable stays `failed`. Plus:
  every kept block final-reachable, every observed opcode variant built, no
  site record for a nonexistent site. Violations are `DecompileError`s.

First corpus-wide run of the rule downgraded seven bogus `proven` sites:
Army_Moves $E093 (vector, empty resolution vs 4 observed), Aces_High $113A,
Commando_High-Score $091A (stale $FFFF vs 7 observed), Jammer-424
$11F7/$1203/$124B, Bird_Flu $121B — all replay bit-exact under the envelope.

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

1. **Loop-trip bound.** *Implemented, including nested loops.* The increment
   `H = H + 1` and a counter `Y = Y − 1` share a block, so `H + Y = K` is a loop
   invariant; `Analysis._affine_cell_bound` anchors `K` at the loop header (the
   block overlap preserves `H + Y`), verifies every body block cancels
   `δH + δY = 0`, reads `Y`'s entry bound `Y₀` from the non-loop predecessors and
   the `BPL` sign-exit, checks definite assignment (the image byte is dead) and
   no-wrap, and pins `H ∈ [c, H₀ + (Y₀+1)]`. Nested and overlapping-block loops
   (Bionic's copy loop: an inner `DEX/BNE` copy inside an outer `DEY/BPL`) are
   recovered by **leader-split** dominance (`_split_idom` / `natural_loops`): a
   block that spans a later leader flows *into* it, and each procedure is rooted
   so `JSR`-called loops get dominators. The aliasing premise is discharged by
   assume-guarantee (pin the bound, then prove no computed store — including the
   pointer that reads `H` itself — reaches `H`).
   Verified end-to-end on single and nested synthetic loops and, post-closure,
   on Bionic (`$65A1 → [$78,$82]`, which un-aliases the JMP operand).
2. **Closure ordering (validated).** The dyn-target pass evidences a vector
   before `close(dispatch_pcs)` bounds the interprocedural index, so the affine
   bound must run again *after* full closure. A post-closure retry (refine the
   monotone pointer cells with the index now bound, then re-attempt each evidence
   site) was prototyped and confirmed to discharge the alias premise — the vector
   then advances past aliasing. Not landed yet because it exposes blocker 3.
3. **Index value-set precision (the current blocker).** With aliasing
   discharged, the relational enumeration (lemma 1) reads `table_lo[A] |
   table_hi[A]<<8`. Bionic's index `A` is over-approximated to 128 values
   `[$80,$FF]`, so some `table[A]` reads spill past the real (≈7-entry) table
   into *written* memory (`$6A23`), and the sound relational read refuses
   (`relational read hits mutable/IO cell`). Closing the corpus needs `A` bounded
   tightly enough that every `table[A]` read is immutable — a value-set precision
   improvement on the dispatch index itself.

This is the spec's core value-set rewrite (§4.1–4.3); each step is
soundness-critical, so it is tracked as the missing lemma rather than
approximated from the trace.

## Opcode-cell envelope (Athena lemma)

Unproven opcode-cell value sets take the guarded evidence envelope (observed
set only; walker faults otherwise; `--sound` fails; proof status `evidence`).
Athena's cells `$6083`/`$C325` close correctly in isolation (self-referential
EOR toggles iterate to their 2-sets), but two never-narrowing `(zp,X)`
computed stores alias everything with unresolvable values, forcing ⊤. The
missing lemma: bound those store pointers (zp pair value-set recovery under
widening), spec §4.2.

## Re-scope: the play boundary retires lemma 2, blocker 3 remains

The deliverable is the play-phase program only (spec §0.5): init executes
concretely and its writes become the snapshot. Measured under the re-based
analysis: Bionic's aliasing writer (the page-incrementing copy loop lemma 2
exists to bound) is **init-phase** — `$65A1` is constant `$82` for the whole
play run — so lemma 2 and its machinery are no longer on the critical path.
The vector sites themselves are play-phase and stay `evidence` (Bionic ×3,
Comic Bakery ×4, Wizball ×3): the residual obstruction is blocker 3 alone,
dispatch-index value-set precision. Ghouls_n_Ghosts' `$7316` opcode cell is
init-only; under the boundary the tune decompiles and replays bit-exact
(its remaining gate failure is text size — expression-sharing in emission —
a breadth item, not a soundness one).
