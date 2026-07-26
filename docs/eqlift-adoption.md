# eqlift — adopting equality-saturation lifting (implementation plan)

Status: PoC landed (`deity_informant/eqlift.py`, `tools/eqlift_emit.py`,
`tests/test_eqlift.py`); this doc is the normative plan for replacing
frameproc's expression-level lift passes with solver-verified rewriting.
"MUST" is a gate. Scope: expression lifting only — see Non-goals.

## 1. Integration shape

- Insertion point: `frameproc.procedures`, per procedure, between `_Builder`
  (pass-1 statement serialization) and `_Info` (pass-2 liveness):
  SSA-ify the pass-1 statement list, load it into the e-graph, saturate with
  the admitted ruleset, extract per statement site, sweep dead temps.
- Superseded and deleted on cutover: the pass-1 expression cleanup —
  `_Prune` (dead-definition pruning) and `_inline`/`_find_use` (single-use
  inlining economics). Equality saturation subsumes both: copies die because
  extraction reads the cheapest equal term, not because a pass hunts uses.
- NOT superseded: pass 2 (interprocedural register liveness -> params/returns)
  and pass 3 (for-range recovery) are flow/shape analyses, not term rewrites;
  they stay downstream as standard algorithms and MUST consume the extracted
  statement lists unchanged in form (same statement vocabulary).
- The SSA layer is conservative by construction: fresh version per write,
  havoc at labels/loop heads/joins/call boundaries, cell-overlap
  invalidation, volatile loads never deduplicate. Any weakening of a havoc
  point MUST come with a soundness argument in this doc, not code comments.

## 2. Dependency policy

- `egglog` and `z3-solver` go into `pyproject.toml` under a new extra
  `eqlift = ["egglog", "z3-solver"]`; `dev` MUST include the extra so CI and
  local dev always have it. Pin `egglog` minor (`egglog>=13,<14`): the str
  form of extracted expressions is parsed and MUST stay stable.
- CI MUST `pip install -e .[dev]` (picks up the extra) before the fast suite;
  `tests/test_eqlift.py` keeps `pytest.importorskip` so downstream forks
  without the extra skip cleanly instead of erroring.
- Import guard: none. `eqlift.py` imports `egglog`/`z3` at module top and no
  production module imports `eqlift` until cutover; `pylint deity_informant/`
  runs in the dev environment where the extra is installed. At cutover,
  `frameproc` MUST import eqlift lazily inside `procedures()` so the base
  install (no extra) keeps every non-lifting entry point working.

## 3. Rule governance (the core contract)

- Admission gate: a rule exists only as a `(name, widths, builder)` entry in
  `eqlift.RULES`. `admitted_rules()` MUST call `verify_rules()` before
  constructing the egglog ruleset: every rule instance is proven an
  equivalence over Z3 QF_BV (width 1 -> BV8, width 2 -> BV16) or the whole
  ruleset refuses to build. There is no bypass path.
- Single source: each builder runs against the dual algebra
  (`_EggAlg` for the admitted rewrite, `_Z3Alg` for the proof obligation),
  so the formula Z3 proves IS the rewrite egglog applies. Hand-transcribing
  a rule twice is forbidden; a rule that cannot be expressed through the
  algebra is a missing algebra op, not an excuse for a bespoke pass.
- Review findings become rules, not passes: when review of lifted output
  finds a missed simplification, the fix is a new RULES entry (plus its Z3
  proof, obtained for free by the gate) — never a targeted rewrite of one
  tune's statements. A finding that cannot be a local equivalence (shape,
  liveness) belongs to passes 2-3 or to M-FP3, and MUST be recorded as such.
- Location and naming: rules live in `eqlift.py` next to the algebra as
  `_r_<family>_<effect>` builders (`_r_sign_ne`, `_r_not_ule`,
  `_r_carry_fuse`); RULES entries are `<family>_<effect>` with an explicit
  width tuple. Width-independent patterns dedup automatically; the Z3 proof
  still runs per declared width.
- Extraction cost policy: `_COSTS` orders leaves const < cell < local < load,
  ops cheap, `carry` expensive (mirrors frameproc's plen economics), SID-range
  cells penalized (outputs; never read back). Cost changes MUST be justified
  by a diff of the emitted corpus artifacts (`tools/eqlift_emit.py` output),
  not by a single tune. The egg-side constructor costs and `_COSTS` MUST stay
  order-consistent so the extraction pool contains the preferred terms.

## 4. Verification laws at integration

- Rule proofs (all instances) run in the fast suite on every change.
- All-rewritten-sites proofs stay on: for every emitted procedure,
  `verify_lift` MUST prove original == chosen under the SSA definitional
  equations for every site where extraction changed the term. This is term
  equivalence in the SSA environment — it is NOT a replay claim.
- Gate C / walker replay is untouched: eqlift reads the committed model and
  MUST NOT mutate it; emission tools re-assert `Walker(model).run == ev.wlog`
  before writing text. Gate FP (frameprog evaluator, M-FP2) applies to the
  lifted dialect once the parser lands; until then emitted eqlift text is
  review material, not a verified artifact level.

## 5. Migration steps (each independently gated)

1. Land the `eqlift` extra + CI install; fast suite green with the PoC tests
   running (not skipping) in CI.
2. `frameproc.procedures(engine="eqlift")` behind a flag: pass-1 output ->
   eqlift -> passes 2-3; corpus diff of frameprog artifacts reviewed; the
   flag defaults off. Gate: all-sites proofs + byte-identical output for
   procedures where no rule fires.
3. Cost/rule tuning until the corpus diff is a strict readability win
   (reviewed tune by tune); flag defaults on.
4. Delete `_Prune`/`_inline` and the flag; frameprog emission goes through
   eqlift unconditionally. Gate: full suite + Gate FP status unchanged.

## 6. Risks and mitigations

- egglog version drift: extracted-str parsing and RunReport shapes are
  version-sensitive. Mitigation: minor-version pin + `_parse_ir` round-trip
  covered by tests (including the let-lifted multi-line form).
- Extraction nondeterminism: `extract_multiple` pool membership is not
  contractually ordered. Mitigation: selection re-costs candidates with
  `_COSTS` and breaks ties lexicographically; emission tools MUST emit twice
  and compare (in-process) and the corpus artifacts are the cross-process
  witness. Any observed instability is a release blocker for step 3.
- Saturation blowup: assoc/comm rules are expansive. Mitigation: bounded
  schedule (`run(ruleset * iters)`, default 24) — saturation is not assumed;
  extraction is sound at any cutoff because every admitted rule is an
  equivalence. Per-procedure wall-clock cap MUST hold the 60s test budget.
- Stale-version leakage in variants: extraction can propose terms over old
  SSA versions. Mitigation (landed): site-validity check + leaf repair from
  e-class mates; the all-sites Z3 proofs would catch any residual hole.

## 7. Non-goals

- Passes 2-3 (params/returns liveness, for-ranges) are out of scope for the
  e-graph; they remain classical passes over the lifted statements.
- 16-bit cell fusion in emitted text: `carry_fuse` proves the ADC-pair
  equivalence and wins extraction where the 16-bit vocabulary exists, but
  printing fused byte-pair cells requires the M-FP3 pair-cell dialect first.
  M-FP3 is a prerequisite, not a rule-set problem.
- No replay/bit-exactness claim for lifted text until Gate FP's independent
  evaluator (M-FP2) can execute the dialect.
