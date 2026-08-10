# eqlift — adopting equality-saturation lifting (implementation plan)

Status: PoC landed (`deity_informant/eqlift.py`, `deity_informant/eqlift_mem.py`,
`tools/eqlift_emit.py`, `tests/test_eqlift.py`, `tests/test_eqlift_mem.py`); this
doc is the normative contract for solver-verified rewriting over a unified
value+memory e-graph. "MUST" is a gate. 2026-08-09: the register-model plan
(docs/register-model-lift-impl.md) adopted this engine as its stage 3; §8's
step list is superseded by that plan's stages, while §4 (rule governance), §5
(transitional passes — the no-extension rule is now enforced), §6 (verification
laws) and §9 (dependency policy) bind unchanged.

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
  `num`/`zext`/`band`/`add`/`shl`; `hi(a) < lo(b)` yields the `disjoint(a,b)`
  relation that guards the diff axiom. This replaces `_addr_range` +
  `havoc_store` scoping. `add`/`shl` carry an interval only where the result
  cannot wrap its own width — a wrapped sum is *below* both operands, so the
  unguarded floor was a claim the value breaks (§10).
- The interval BRIDGE (stage 3b) is the second source of those bounds:
  `eqlift_mem.addr_interval` reads `frameproc.addr_floor` (must-set bits, a lower
  bound) and `frameproc.addr_bits` (may-set bits, an upper one) off the pass-1
  address expression and seeds the e-class the address converts to. It seeds only
  where the lattice states nothing, so no seeded bound can be widened by a
  derived one, and it consumes those two committed analyses rather than
  extending them (§5's no-extension rule).
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

## 8. Migration steps (superseded as a schedule by docs/register-model-lift-impl.md stages 2–4; the per-step gates below remain the engine's own)

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
  procedure. Mitigation: bounded schedule; saturation is not assumed; extraction
  sound at any cutoff. Per-procedure wall-clock MUST hold the 60s test budget.
  **Measured and closed 2026-08-10** — `run(ruleset * 30)` is not a bound: on
  `Dynasty_8_tune_2` (one procedure, 488 statement nodes) rounds 0–8 cost 0.0s,
  round 9 0.5s, round 10 3.1s and round 11 asked for 1.6GB in one allocation, so
  the process died before extraction. `eqlift_mem.saturate` runs the ruleset a
  round at a time and refuses the next one when the last round's own growth ratio
  says it will not fit the remaining budget (`DI_EQLIFT_BUDGET_S`, default 5s) or
  when resident growth passes `DI_EQLIFT_BUDGET_MB` (default 128), and stops at a
  fixpoint. Priced on the tune that forced it: 128MB gives 630 lines in 2.0s,
  256MB gives 629 lines in 39.1s — one line of minimization for 37 seconds — and
  neither Commando (350) nor `Ghouls_n_Ghosts` (1,335) moves a line at any bound,
  because the cap does not bind there.
- Proof cost is a DAG problem, not a solver problem: `_Z3Env.of` rebuilt shared
  extracted subterms once per occurrence, which is exponential on a DAG. It
  memoizes on the IR node; the same tune goes from unbounded (37GB resident,
  killed) to 40s. One §6 record per PROCEDURE, never one merged record per
  artifact — SSA names are procedure-local, so a merged `defs` would prove
  equalities between different values.
- egglog version drift: extracted-str parsing and RunReport shapes are
  version-sensitive. Mitigation: minor-version pin + `_parse_ir` round-trip
  covered by tests (including the let-lifted multi-line form).
- Extraction nondeterminism: **observed, diagnosed and closed.**
  `extract_multiple` returns *a* representative of an e-class and which one is
  not contractual; re-costing with `_COSTS` plus a lexicographic tie-break does
  not help, because the tie-break orders a pool whose membership itself varies.
  Measured 2026-08-02 with `tools/lifttrace.py repeat Andy_Capp-The_Game
  --runs 8` — a split Gate FP verdict on identical source, one fresh process per
  run. `framemath._FUSED` warmth was tested and is not the cause (cold and warm
  agree in-process). The verdict proved to be a *pure function* of
  `PYTHONHASHSEED`, reproducible per seed, so the fix was not a total order over
  the pool: **the consumer must not depend on which representative came back**.
  The variation that mattered was `x - K` against `x + (2**w - K)`, one function
  spelled two ways, of which only one had a provenance naming `framemath._back`
  could use. `canon` collapses that pair onto the `add` spelling pass-1 uses for
  an indexed address; where a *choice* between forms remains, `framemath._site`
  makes it off the program (the statements' own cells) rather than off the
  extraction order. Evidence: the 682-tune corpus run under two hash seeds is
  bit-identical, 672 records, 0 differ. The residual case — where the wanted form
  is never extracted at all, so no choice among returned forms can recover it —
  is closed in `docs/frameprog.md` §7.3: `framemath._pairs` enumerates the lane
  pairs the program names and `_fuse` *queries* the e-class for each, admitting
  only those the cancellation rules reduce. Extraction still decides how the step
  is spelled; it no longer decides which grouping the site is.
- Interval analysis too weak: a table left unbounded degrades to a full-memory
  read-through miss (a correctness-preserving readability loss, never a soundness
  loss). Mitigation: extend the `lo`/`hi` lattice by proven cases only, justified
  by corpus diff. **The admitted extensions, stage 3b:**
  - *The bridge* (§2). `addr_floor(e) <= e <= addr_bits(e)` holds for every
    address the expression names — every set bit of the value is in the may-set
    mask, so the mask is an upper bound; every must-set bit is in the value, so
    the floor is a lower one. Seeded only where the lattice states nothing. What
    it buys that the lattice cannot: a byte-wide address is page zero
    (`[0,$FF]`), and the stack push `zext2(sp) | $0100` is `[$0100,$01FF]`, so a
    spill/reload reads through a push instead of stopping at it.
  - *The wrap guard* (§2), a narrowing, not a widening: `add`/`shl` state an
    interval only under `hi(a) + hi(b) <= mask(w)`. Without it `$F0 + (y & $1F)`
    at one byte claims a floor of `$F0` while the value can be `$10`, which would
    license a disjointness that is false. Nothing in the corpus was relying on
    the unguarded form; the guard is what makes seeding byte-wide leaves safe.

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
