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

## Paired-index closure (cross-block single-writer-pair lemma, implemented)

For a computed transfer whose target is `m[C_lo] | m[C_hi] << 8` over a
written operand/vector cell pair (SMC'd `JMP` operands, `jmp (ind)` vector
cells — per resolved pointer), `Analysis._paired_cell_targets` proves the
target set from four premises, tried before per-cell resolution:

1. **No alias**: no computed store's interval set may reach either cell
   (`_cell_aliased`); stack-page cells refuse outright (jsr/rts traffic
   bypasses store events).
2. **Cross-block single-writer pair**: every block that stores one cell stores
   the other in the same block (the last store per block is the pair live at
   exit; blocks are straight-line, so no transfer observes a half-updated
   pair). A cell play never writes contributes its post-init constant.
3. **Same-index pairing**: per writer, the lo/hi stored values in writer-live
   form (loads of written cells stay symbolic; repeated loads of an unstored
   cell unify) must share one index variable set — writer-entry registers
   and/or written-cell loads — or be constants. Enumeration is joint over the
   guard-refined value sets (`R`/`S`), so the set is the zip
   `{T_lo[i] | T_hi[i]<<8 : i ∈ D}` per writer plus the post-init pair —
   never the per-cell Cartesian product.
4. **Table immutability**: every table read under `D` must land outside
   `model.written`; a domain spilling into a mutable cell refuses (the domain
   is never clipped without a static guard).

Proven sites carry the derivation (cells, writer blocks, |D|) in the Proof
lemma. Unobserved members of a paired-proven envelope are **not**
materialized: they emit as faulting `unobserved` frontier edges. This is what
makes the closure a stable fixpoint — materializing over-approximate envelope
members (index over-approximation lifts junk words as blocks) plants wild
computed stores in the workspace that would correctly refute premise 1 for
the very site that created them on the next round. An unswept writer index
register retries via re-closure (bounded) rather than refusing, which also
prevents the transient Cartesian junk sets the retry replaces. Backstop: a
sole-pair enumeration that omits observed targets refuses — a premise the
trace refutes is never trusted (Wizball's naive pairing is exactly that
case).

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
Ghouls_n_Ghosts' `$7316` opcode cell is init-only; under the boundary the
tune decompiles and replays bit-exact.

## Corpus state under the paired-index lemma (full Songlengths)

Proof tally moved from proven=94/evidence=60 (110 of 138 tunes `--sound`) to
proven=126/evidence=29 (123 tunes `--sound`): 34 sites converted, all
replays bit-exact. Converted: the operand-SMC `jmp`/`jsr` family — Ghouls ×3,
Agent_X_II v2, Aiginas ×3, Chester_Field ×3, Cosmic_Storm ×3, Gauntlet_III
×3, Baby_Blues ×3, Comic Bakery ×4, Commando_High-Score ×3 via the paired
zip; 424 ×3, All_Points_Bulletin, Amulet_of_Yendor, Soccer_Skills,
ATV_Simulator, Amaurote recovered from committed stale per-cell sets. One
tune regressed (Attitune ×3: a formerly stale-but-covering Cartesian pair
now commits as the guarded envelope with a precise spill refusal).

Remaining refusal taxonomy (each site keeps the guarded envelope with the
refined per-site reason):

- **Mutable-table spill** (Bionic ×3, Agent_X_II v1/v3, Bird_Flu, Attitune
  ×3): `table read hits mutable/IO cell $XXXX (writer $YYYY)` — the index
  domain (e.g. `[$80,$FF]` from the `BPL` guard) over-approximates the real
  table extent and some `T[i]` lands in a play-written cell; blocker 3
  (dispatch-index precision) is the remaining lemma.
- **Index never closes** (Athena ×3): the writer's index registers sit
  behind the never-narrowing `(zp,X)` store family (§4.2, the same blocker
  as its opcode cells); pairing defers and the per-cell fallback keeps the
  256-wide proven Cartesian case lists.
- **Unpaired writers / vector alias** (Wizball `$492C` / `$4EAC`,`$4FFB`):
  `writer block $4D8D stores $0013 but not $0014` — the zp vector cells are
  genuinely written independently, so zip enumeration would be unsound —
  and `a computed store may reach cell $4680`.
- **Observation backstop** (Aces_High `$113A`): `enumeration omits N observed
  target(s)` — the pairing premise held syntactically but the trace refutes
  the enumeration, so an unmodelled writer exists; refused.
- **Stack-page vector** (Army_Moves `$E093`): `operand cell $0107 is a stack
  cell` — jsr/rts traffic bypasses store events, so the writer census cannot
  be complete there.
