# eqlift — adopting equality-saturation lifting (implementation plan)

Status: PoC landed (`deity_informant/eqlift.py`, `deity_informant/eqlift_mem.py`,
`tools/eqlift_emit.py`, `tests/test_eqlift.py`, `tests/test_eqlift_mem.py`); this
doc is the normative plan for replacing frameproc's expression-level lift passes
with solver-verified rewriting over a unified value+memory e-graph. "MUST" is a
gate.

## 1. What equality saturation does and does not do

- Equality saturation canonicalizes VALUES: it merges expressions proven equal
  and extraction reads the cheapest equal term at each site. This is its whole
  contract. Every rewrite is an admitted equivalence (§4).
- It does NOT delete statements and does NOT reason about memory dataflow by
  itself. A flag/register write, a store, an assignment are CFG statements at
  fixed def sites; saturation rewrites the expression a site holds, it can NEVER
  remove the site. Dead-code elimination and store-to-load forwarding are not
  value rewrites.
- Retracted claim: an earlier version of this doc asserted that equality
  saturation "subsumes both `_Prune` (dead-definition pruning) and `_inline`
  (single-use inlining) — copies die because extraction reads the cheapest equal
  term." This is FALSE. Evidence: on Commando, per-site extraction emitted 45
  dead 6502 status-flag writes versus frameprog's 10, because a dead flag
  definition is a statement no use extracts away — canonicalizing the expression
  at that site leaves the site standing. Per-site extraction alone does not
  suffice for dead code or memory forwarding.
- The correct mechanism for both is to make MEMORY a first-class sort in the
  same e-graph and to drive deletion from ROOT EXTRACTION over observable sinks
  (§2). Statement removal is then "the value is an unreferenced subterm", not a
  bespoke pass.

## 2. The unified value+memory e-graph (the mechanism)

One e-graph, one saturation, one root extraction carry both algebras. The value
algebra is `eqlift.py` (`num`/`cell`/`loc`/`load`/arith/compare, with the
Z3-QF_BV rule set). The memory algebra is `eqlift_mem.py` and is prototyped with
passing tests (`tests/test_eqlift_mem.py`).

- Sorts: a value/address sort `T` (`num`, `loc`, `zext`, `add`, …) and a memory
  sort `Mem`. A procedure body is a `store`-chain over an opaque initial memory
  `mem0()`; every load is `sel(mem, addr)`. Cell versioning is replaced by
  position in the store chain.
- The four McCarthy array axioms, each Z3-proven valid over the array theory
  (`select`/`store` over `BV16 -> BV8`) before admission, exactly as the value
  rules are proven over QF_BV:
  - `sel(store(m,a,v),a) = v` — store-to-load forwarding.
  - `sel(store(m,a,v),b) = sel(m,b)` when `disjoint(a,b)` — disjoint read-through.
  - `store(store(m,a,u),a,v) = store(m,a,v)` — dead-store overwrite.
  - `store(m,a,sel(m,a)) = m` — redundant store / spill-reload elimination.
- Address disjointness is an e-class INTERVAL analysis, not imperative cell
  overlap: `lo`/`hi` lattice values (merge by min/max) propagate over
  `num`/`zext`/`add`; `hi(a) < lo(b)` yields the `disjoint(a,b)` relation that
  guards the diff axiom. This replaces `_addr_range` + `havoc_store` scoping.
- Root extraction from observable sinks drives deletion: the observable outputs
  are the SID hardware register writes (`$D400`–`$D41C`), procedure returns, and
  cross-procedure-live cells. A value reachable from no sink is an unreferenced
  subterm and is not emitted — dead flags and dead stores fall out of extraction,
  not liveness. Store-to-load forwarding, spill/reload elimination, disjoint
  read-through and dead-store removal fall out of saturation.

## 3. Integration shape

- Insertion point: `frameproc.procedures`, per procedure, between `_Builder`
  (pass-1 statement serialization) and `_Info` (pass-2 liveness). Build the
  store-chain + value terms into one e-graph, saturate with the admitted value
  and memory rulesets, extract from the observable-sink roots, print the surviving
  statements.
- Superseded on cutover: the pass-1 expression cleanup `_Prune` and
  `_inline`/`_find_use`. Their effect is now root extraction over the unified
  graph — copies and dead defs are unreferenced subterms — NOT per-site cheapest
  extraction, which was shown insufficient (§1).
- NOT superseded as term rewrites, but re-sourced: pass 2 (interprocedural
  register liveness -> params/returns) and pass 3 (for-range recovery) are
  flow/shape analyses. Pass 3 stays a downstream classical pass over the
  extracted statements. Pass 2's boundary summaries (what a callee/return
  observes) MUST feed the root set for extraction; the intraprocedural dead-flag
  removal it once performed is subsumed by root extraction.
- Statement vocabulary MUST stay stable in form so passes 2-3 consume extracted
  lists unchanged.

## 4. Rule governance (the core contract)

- Admission gate, values: a rule exists only as a `(name, widths, builder)` entry
  in `eqlift.RULES`. `admitted_rules()` MUST call `verify_rules()` before building
  the egglog ruleset: every instance is proven an equivalence over Z3 QF_BV
  (width 1 -> BV8, width 2 -> BV16) or the ruleset refuses to build. No bypass.
- Admission gate, memory: every memory axiom MUST be proven valid over Z3's array
  theory by `eqlift_mem.verify_axioms()` before the memory ruleset is admitted,
  under the same no-bypass rule. The gate now spans QF_BV (values) and arrays
  (memory).
- Single source: each value builder runs against the dual algebra (`_EggAlg` for
  the rewrite, `_Z3Alg` for the proof) so the formula Z3 proves IS the rewrite
  egglog applies. Memory axioms are stated once as the egglog rewrite/rule and
  once as the Z3 array goal; both forms are checked to match the four names
  above. Hand-transcribing a rule so the proof and the rewrite can diverge is
  forbidden.
- Review findings become rules, not passes: a missed value simplification is a
  new `RULES` entry (with its Z3 proof); a missed memory simplification is a new
  admitted array axiom or a strengthening of the interval analysis — never a
  targeted rewrite of one tune. A finding that is genuinely flow/shape (params,
  returns, for-ranges) belongs to passes 2-3 or M-FP3 and MUST be recorded as
  such.
- Extraction cost policy: `_COSTS` orders leaves const < cell < local < load, ops
  cheap, `carry` expensive; SID-range cells penalized (outputs, never read back).
  Cost changes MUST be justified by a corpus-artifact diff
  (`tools/eqlift_emit.py`), not one tune. Egg-side constructor costs and `_COSTS`
  MUST stay order-consistent.

## 5. Migration: retire the transitional passes

The unified memory graph is the target. Several bespoke passes were added to the
value-only PoC as a scaffold and are TRANSITIONAL: they MUST be deleted once the
memory-graph emit path subsumes them, and MUST NOT be extended.

- `_RegLive` liveness DCE — replaced by root extraction from observable sinks.
- `_copy_prop` (no-op self-copy removal) — replaced by `store_redundant` /
  root extraction.
- `_prop_once`'s cell-forwarding branch — replaced by `sel_store_same`
  forwarding.
- `havoc_store` + `_addr_range` alias scoping — replaced by the interval
  disjointness analysis and the disjoint read-through axiom.
- the `_loadref` operand-order fix — subsumed once loads are `sel` terms with
  normalized `add` operands in the shared graph.

Local single-use value inlining that is purely expression canonicalization stays
until the unified extractor is on; it is scaffolding for readability, not a
semantic pass, and is deleted with the flag.

Open integration problems (these gate cutover, §7):

- Control-flow memory joins: a straight store-chain models one linear path.
  Branch joins, loop heads and call boundaries need a memory phi (a joined `Mem`
  e-class) or an opaque-reset of memory where the join cannot be reconciled.
  Modeling this soundly without discarding all forwarding is the primary open
  problem.
- Whole-procedure saturation cost: value + memory rules over a whole procedure's
  store chain is more expansive than the per-site value PoC. The per-procedure
  wall-clock MUST hold the 60s test budget; a bounded schedule is mandatory
  (extraction is sound at any cutoff because every admitted rule is an
  equivalence).

## 6. Verification laws at integration

- Rule proofs (all value instances) and axiom proofs (all four memory axioms) run
  in the fast suite on every change.
- All-rewritten-sites proofs stay on: for every emitted procedure, `verify_lift`
  MUST prove original == chosen under the SSA/memory definitional equations for
  every site extraction changed. Term equivalence in the environment, not a
  replay claim. Memory-forwarded loads are proven against the array-theory
  encoding of their store chain.
- Gate C / walker replay is untouched: eqlift reads the committed model and MUST
  NOT mutate it; emission re-asserts `Walker(model).run == ev.wlog` before
  writing text. Gate FP (frameprog evaluator, M-FP2) applies to the lifted
  dialect once the parser lands; until then emitted eqlift text is review
  material, not a verified artifact level.

## 7. Graph-provenance semantic recovery

The lifted graph is not only cleaner text; the memory sort makes backward
reachability a first-class query, and it produces semantic labels as output.

- Because SID register addresses are known output sinks, the data table feeding
  each register is found by backward reachability from that store to a
  constant-base indexed `sel` (a `sel` whose address is `base + index`, `base`
  constant). The table backing each register is thereby identified.
- Pitch/frequency table: the constant-base table whose loaded lo/hi values reach
  `$D400`/`$D401 + 7·voice` is the pitch table. The label is confirmed
  independently by equal temperament — consecutive 16-bit entries in ratio
  ≈ 2^(1/12), octave doubling — verified on Commando's `$5428` table (96 entries
  = 8 octaves).
- The same backward-reachability query labels the pulse-width, control, AD/SR and
  filter tables from the SID register map, each by the register it reaches.

These labels are a first-class output of the lifted graph, emitted alongside the
procedure text.

## 8. Migration steps (each independently gated)

1. Land the `eqlift` extra + CI install; fast suite green with the value PoC and
   the memory PoC (`test_eqlift_mem.py`) running (not skipping) in CI. Memory
   axiom proofs run in the gate.
2. Wire the memory sort into `lift_stmts` behind a flag: build the store chain,
   saturate value+memory rules, extract from the observable-sink roots on linear
   (join-free) regions first. Corpus diff of frameprog artifacts reviewed; flag
   defaults off. Gate: all-sites proofs + byte-identical output where no rule
   fires.
3. Solve the memory-join model (phi / opaque-reset) and cost bounding; extend
   root extraction across whole procedures. Cost/rule tuning until the corpus
   diff is a strict readability win, reviewed tune by tune; flag defaults on.
4. Delete the transitional passes (§5), `_Prune`/`_inline`, and the flag;
   frameprog emission goes through the unified graph unconditionally. Gate: full
   suite + Gate FP status unchanged.

## 9. Dependency policy

- `egglog` and `z3-solver` are **core dependencies**. They began as the extra
  `eqlift = ["egglog", "z3-solver"]`, and moved on the first cutover: frameprog's
  rung (d2) reads its 16-bit lifts off the admitted rule set, so a frame program
  cannot be built without them and an optional extra would only mean a broken
  install. Pin `egglog` minor (`egglog>=13,<14`): the str form of extracted
  expressions is parsed and MUST stay stable.
- CI MUST `pip install -e .[dev]` before the fast suite; `tests/test_eqlift.py`
  and `tests/test_eqlift_mem.py` keep `pytest.importorskip` for forks that pin an
  older core.

## 10. Risks and mitigations

- Memory-join unsoundness: an under-conservative phi could forward a load across
  a branch that writes its cell. Mitigation: opaque-reset by default at every
  join/loop-head/call boundary, weakened only per-site behind an admitted
  argument in this doc; all-sites Z3 proofs catch any residual hole.
- Saturation blowup: assoc/comm plus the memory axioms are expansive over a whole
  procedure. Mitigation: bounded schedule (`run(ruleset * iters)`); saturation is
  not assumed; extraction sound at any cutoff. Per-procedure wall-clock MUST hold
  the 60s test budget.
- egglog version drift: extracted-str parsing and RunReport shapes are
  version-sensitive. Mitigation: minor-version pin + `_parse_ir` round-trip
  covered by tests (including the let-lifted multi-line form).
- Extraction nondeterminism: `extract_multiple` pool membership is not
  contractually ordered. Mitigation: re-cost with `_COSTS`, break ties
  lexicographically; emit twice and compare; corpus artifacts are the
  cross-process witness. Instability is a release blocker for step 3.
- Interval analysis too weak: a table left unbounded degrades to a full-memory
  read-through miss (a correctness-preserving readability loss, never a soundness
  loss). Mitigation: extend the `lo`/`hi` lattice by proven cases only, justified
  by corpus diff.

## 11. Non-goals

- Passes 2-3 (params/returns liveness, for-ranges) remain classical passes over
  the lifted statements; only their intraprocedural dead-flag removal is
  subsumed by root extraction.
- 16-bit cell fusion in emitted text: `carry_fuse` proves the ADC-pair
  equivalence and wins extraction where the 16-bit vocabulary exists, but printing
  fused byte-pair cells requires the M-FP3 pair-cell dialect first. M-FP3 is a
  prerequisite, not a rule-set problem.
- No replay/bit-exactness claim for lifted text until Gate FP's independent
  evaluator (M-FP2) can execute the dialect.
