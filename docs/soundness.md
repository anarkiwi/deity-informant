# Soundness accounting (observed-primary + certification)

The artifact is the per-tune **observed program**: every committed site set —
opcode-SMC dispatch sets, computed jump/call/branch/vector target sets, switch
case lists, dynamic-landing labels — is exactly the **trace-observed set** over
the tune's full Songlengths duration. Every such site carries a runtime guard
that faults loudly (with the site pc and the value) on anything outside its
set: the model walker's dispatch lookup and dynamic-transfer guard, the
tree-walker's unmatched-case/`resolve_pc` faults, and `unobserved` frontier
markers in the text. This is sound by construction — the observed sets are by
definition what the evidence run executes, and nothing outside them can be
silently entered.

Static analysis is **optional certification** and can never widen an artifact
set (docs/decompiler-implementation.md §0.1).

## Certification records

`structured.Proof(site, kind, status, targets, lemma)`:

- `kind`: `jump` (`jmp abs` self-patched), `vector` (`jmp (ind)`), `branch`
  (computed branch), `call` (computed `jsr`), `opcode` (SMC opcode cell).
- `targets`: always the serialized (observed) set.
- `status`:
  - `certified` — the static set EQUALS the observed set: the runtime guard is
    provably dead. `lemma` carries the static derivation (e.g. paired-index).
  - `observed` — the guard stays live. `lemma` records why static did not
    certify: a refusal diagnostic, or a static set wider than observed (which
    is reported for reference, never serialized as arms).

`model.proofs` maps each site pc to its `Proof`; `model.dyn_targets` and
`model.dispatch_sets` are the committed (observed) sets themselves.

## Report and strict mode

- `structured.proof_report(model)` → serialisable `{tally, sites}`;
  `structured.format_report(model)` → text (`[SOUND]` when no guard is live).
  CLI: `decompile --report`.
- `decompile(..., sound=True)` / `decompile --sound` fails the build with a
  site-specific diagnostic unless **every control guard is certified dead**.

Without `--sound` the build always succeeds with live guards where static
analysis fell short; the report makes every live guard auditable.

## Commit phase and the commit checker

`Model.build_all` separates ANALYSIS from program construction:

- **ANALYSIS** (`_analyze`/`_close_once`): hypothesis CFG + fixpoint closure in
  the workspace; produces per-site static `(kind, targets, lemma)` results and
  static dispatch value sets — certification input only.
- **COMMIT** (`_commit`): builds `proofs`/`dyn_targets`/`dispatch_sets` from
  the observed sets, resplits at final entries, keeps only blocks reachable
  from play under the committed edges, then runs the intra-block passes.
  Blocks are immutable afterwards (only `lookup` lazily adds static
  continuations). Unobserved-but-statically-derived arms are never
  materialized or serialized.
- **Checker** (`check_commit`): every committed set IS the observed set, every
  kept block is final-reachable from play, every observed opcode variant
  built, no site record for a nonexistent site. Violations are
  `DecompileError`s.

## Certification machinery

The value-set closure, SP flow, dominators, and the lemmas below are retained
solely to certify guards dead; their outputs never reach the artifact.

### Relational vector closure (lemma 1)

For a self-modified `JMP` vector whose operand bytes are patched in one block
from parallel tables indexed by a shared register, independent per-cell
resolution takes the Cartesian `{lo} × {hi}` product. `_relational_targets`
substitutes each operand cell's unique in-block store and jointly enumerates
the shared index registers, evaluating the whole target expression per
assignment — the set is `|A|`, not `|A|²`. Refuses (precise diagnostic) on a
⊤ index, a volatile leaf, a non-unique operand store, or a domain over 4096.

### Paired-index closure (cross-block single-writer-pair lemma)

For a target `m[C_lo] | m[C_hi] << 8` over a written cell pair,
`_paired_cell_targets` enumerates the zip `{T_lo[i] | T_hi[i]<<8 : i ∈ D}` per
writer block plus the post-init pair, under four premises: no aliasing
computed store, every writer stores both cells, one shared index variable set
per writer, and every table read immutable. Certified sites carry the
derivation (cells, writer blocks, |D|) in the Proof lemma. Backstop: an
enumeration that omits observed targets refuses — a premise the trace refutes
is never trusted.

### Affine trip bound (lemma 2, nested loops)

`_affine_cell_bound` pins a monotone-increment cell to `[c, H₀ + (Y₀+1)·d]`
via the loop invariant `H + Y = K` over leader-split natural loops
(`_split_idom`/`natural_loops`), with definite-assignment and no-wrap checks;
the aliasing premise is discharged assume-guarantee. Under the play boundary
this machinery is off the critical path (Bionic's aliasing copy-loop writer is
init-phase) but remains for certification of play-phase counted loops.

## Guard-live taxonomy (full Songlengths corpus)

Measured 2026-07-25 over 140 tunes: 20 sites certified, 135 guard-live,
79/140 tunes fully certified (`--sound` passes); all 140 replay bit-exact
from model and standalone text. Certification (equality) is strictly harder
than the old `proven ⊇ observed` rule; each guard-live site keeps its precise
static reason in the report:

- **Static set wider than observed** (113 of 135): the static closure is
  sound but not tight — envelope arms the tune never plays (e.g. Athena's
  256-wide per-cell Cartesian sets, partially-driven jump tables). The
  observed arms serialize; the static envelope is report-only.

- **Mutable-table spill** (Bionic ×3, Agent_X_II, Bird_Flu, Attitune):
  `table read hits mutable/IO cell` — the index domain over-approximates the
  real table extent (dispatch-index precision, spec §4.1–4.2).
- **Index never closes** (Athena): the writer's index registers sit behind
  the never-narrowing `(zp,X)` store family (§4.2), which also forces the
  opcode-cell TOP.
- **Unpaired writers / vector alias** (Wizball): the zp vector cells are
  written independently, so zip enumeration would be unsound.
- **Stack-page vector** (Army_Moves `$E093`): jsr/rts traffic bypasses store
  events, so the writer census cannot be complete there.
- **Unclosed/pending resolution**: closure rounds exhausted with the control
  cell still unresolved.
