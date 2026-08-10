# eqlift — adopting equality-saturation lifting (implementation plan)

Status: PoC landed (`deity_informant/eqlift.py`, `deity_informant/eqlift_mem.py`,
`tools/eqlift_emit.py`, `tests/test_eqlift.py`, `tests/test_eqlift_mem.py`); this
doc is the normative contract for solver-verified rewriting over a unified
value+memory e-graph. "MUST" is a gate. 2026-08-09: the register-model plan
(docs/register-model-lift-impl.md) adopted this engine as its stage 3; §8's
step list is superseded by that plan's stages, while §4 (rule governance), §5
(transitional passes — the no-extension rule is now enforced), §6 (verification
laws) and §9 (dependency policy) bind unchanged. The passes §5 names are being
**deleted**, not kept compatible with: their removal is the plan landing, and
nothing in this document may be read as a reason to preserve one.

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
- The McCarthy array axioms, each Z3-proven valid over the array theory
  (`select`/`store` over `BV16 -> BV8`) before admission, exactly as the value
  rules are proven over QF_BV:
  - `sel(store(m,a,v),a) = v` — store-to-load forwarding.
  - `sel(store(m,a,v),b) = sel(m,b)` when `disjoint(a,b)` — disjoint read-through.
  - `store(store(m,a,u),a,v) = store(m,a,v)` — dead-store overwrite.
  - `store(m,a,sel(m,a)) = m` — redundant store / spill-reload elimination.
  - `sel(m,a,2) = sel(m,a+1,1)<<8 | sel(m,a,1)` — the pair read (stage 3c landing 2),
    admitted so the catalog's `pair-row` converges: two adjacent byte columns read at
    one index are the word the columns spell. The egglog rule is that axiom instantiated
    at `a = q + b`, the shape the corpus spells, so a word read does not spawn its lanes.
- Address disjointness is an e-class INTERVAL analysis, not imperative cell
  overlap: `lo`/`hi` lattice values (merge by min/max) propagate over
  `num`/`zext`/`band`/`add`/`shl`; `hi(a) < lo(b)` yields the `disjoint(a,b)`
  relation that guards the diff axiom, in place of any imperative per-site
  alias scoping. `add`/`shl` carry an interval only where the result
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
- Deleted on cutover: the pass-1 expression cleanup `_Prune` and
  `_inline`/`_find_use`. Their effect is root extraction over the unified
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
- **Convergence names the missing rule** (stage 3c landing 2).
  `tests/test_eqlift_converge.py` is the per-idiom gate: enumerated from `idioms.FORMS`,
  each row carries a generator of the spellings a 6502 lift produces for it, and the test
  asserts they merge into one e-class whose extracted representative is the row's normal
  form. A row that does not merge names a rule; a rule proved but not admitted names the
  cost decision that holds it back, with its measurement, and keeps a strict xfail.
  `pack_add` is the worked example: proved at 3c landing 2, admitted at 3d landing 2 with
  the price that names the OR-built pack the normal form (`eqlift._packed`, the catalog's
  own `word-pack`), on a corpus-artifact diff of 49 of 624 tunes at +8 bytes and one
  respelled line each. A cost change that makes a merge safe lands *with* the rule.
- Single source: each value builder runs against the dual algebra (`_EggAlg` for
  the rewrite, `_Z3Alg` for the proof) so the formula Z3 proves IS the rewrite
  egglog applies. Memory axioms are stated once as the egglog rewrite/rule and
  once as the Z3 array goal; both forms are checked to match the names in §2.
  Hand-transcribing a rule so the proof and the rewrite can diverge is forbidden.
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
  MUST stay order-consistent. **The memory sort is part of that order** (stage 3b
  landing 2): `pick_ir` spells a site from memory only as a last resort, so `sel`
  carries `_SEL_COST` rather than egglog's default 1 — at the default, a value class
  holding several memory versions returned only spellings the consumer discards and
  the site fell back to its own raw term.
- **A memory spelling is priced, not banned** (stage 3c landing 1). `pick_ir` used to
  drop every candidate mentioning a `cell` or a `load`, which is most of a play
  routine's vocabulary, so a site reading any cell fell back to its own raw term and
  saturation reached it only through the terms it did not name. The order is now
  three keys: a spelling with no memory read beats every spelling with one; among
  memory spellings the **deepest** read wins — the source rather than a copy of it, so
  the copies go unread and root extraction retires them; then `_COSTS` and `repr`
  (§10's determinism). A memory spelling is admitted only where the chain walk
  proves it position-correct (§10), which is the argument the filter stood in for.

## 5. Migration: delete the transitional passes

The unified memory graph is the target. Several bespoke passes were added to the
value-only PoC as a scaffold and are TRANSITIONAL: they MUST be deleted once the
memory-graph emit path subsumes them, and they MUST NOT be extended,
generalized, re-parameterized or given a new caller in the meantime. A finding
that would require any of that is a rule or an axiom (§4), never a change to one
of these. Deleting one is the engine landing, not a regression to weigh: no
consumer may depend on one, and nothing downstream may be designed for
compatibility with one.

(Re-grounded 2026-08-10: this list once named `_RegLive`, `_copy_prop`,
`_prop_once` and `havoc_store` — prospective names that never existed in the
code, verified by `git log -S` over the package history. The inventory below
names what is real, grep-verified at #156.)

- `eqlift_mem`'s liveness path — `_dce`, `_temp_sweep`, `_liveness`'s deletion
  role, the `ROOT_EXTRACT` flag and every `root_extract` parameter — **DELETED**
  (stage 4, landing 2). Root extraction is the only path; what survives is
  `_liveness` in its rooting role and `_share_once` as the rule. The measurement
  compares against a recorded baseline artifact, not a second code path.
- `frameproc._Prune`/`_prune` and `_inline`/`_inline_list`, with the repolish
  fixpoints that drive them — replaced by root extraction and `_share_once` on
  the unified path.
- `framemath`'s per-site value e-graphs (rung (d2)'s scaffolding) — subsumed
  when the same admitted rules fire once in the per-procedure unified graph.
- the `_loadref` operand-order fix — subsumed once loads are `sel` terms with
  normalized `add` operands in the shared graph.

Single-use value inlining is the one entry with a second warrant, established at
stage 3a: `render_roots` names a subterm only where more than one root reads it,
so the root path needs the same rule for its own reason. It is stated as that
rule (`_share_once`) and called by both paths; the transitional pass is deleted
with the flag, and what survives is the rule, not the scaffold.

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
  writing text. **Minimized text is executed, not reviewed.** The clause that
  once stood here — emitted eqlift text is review material until the parser
  lands — is retracted: `examples/state_machine_lift.py` parses and runs the
  minimized program under the frameprog evaluator, the VM projection and the
  sidplayfp oracle, and the two soundness-grade renderer defects that landing
  found had sat invisible in review-only output. Emitted text that is never
  executed is not verified.

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
   **Landed, stage 3b landing 3**: the span join carries what it proves disjoint,
   the in-edge map frees the labels no edge enters, and the 25-exemplar review at
   full Songlengths (`tools/eqlift_measure.py`, `DI_EQLIFT_EMIT_S=600`) flipped
   `ROOT_EXTRACT` on — ON never emits more lines or stores than OFF, three tunes
   emit fewer, 22 of 25 are byte-identical, every changed site Z3-proved, zero
   faults and zero refusals. `DI_EQLIFT_ROOT_EXTRACT=0` selected the liveness path
   until step 4 deleted it (stage 4, landing 2).
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
  **The admitted weakenings, stage 3b landing 2:**
  - *The span join.* `_mem_writes` reads a non-const store's write span off
    `addr_interval` — a `(lo, hi, width)` interval — instead of returning ⊤, and the
    join is **complemented**: `_join_mem` builds a fresh opaque memory and re-stores
    exactly the chain-held const cells, each proved disjoint from every span the
    joined statements can write. Enumerating what the join *keeps* is what makes a
    span usable at all: a bounded-but-unenumerable write (a row, a push) cannot be
    listed as cells to forget, but every cell outside it can be listed as kept. The
    disjointness is a Z3 QF_BV proof over *every* address in the span
    (`_disjoint_span`, cached), never a structural match, so a store width that
    reaches past `hi` and a cell inside the row both refuse. An unbounded store
    address, a label and any dynamic transfer keep ⊤; an unkept cell is guarded
    memory, a readability loss and never a soundness one.
  - *The chain walk*, stage 3c landing 1 — position-correctness as a proof rather than
    a filter. A printed `mem[a]`/cell reads memory **at the statement it prints on**, so
    a spelling extracted from an earlier memory version is valid there only if the chain
    between writes nothing the read names. `_Chain` records what each version wrote (a
    store's span, a join's kept-cell set, ⊤ for a havoc) and walks back from the site:
    each store step is crossed only under a Z3 QF_BV proof over *every* address of both
    spans (`_disjoint_spans`, cached), each join step only for a cell it proved kept, and
    a havoc, an unbounded step, an address the IR cannot bound and a memory term that is
    not a named version all refuse. The walk's length is also the rank the price uses, so
    which representative extraction returned changes nothing (§10). §6's all-sites proof
    is the independent check: it expands the store chain and re-derives the disjointness
    from the address terms themselves.
  - *The read closure and scratch demotion*, stage 3d landing 1. `Footprints` carries a
    per-procedure **read** span set beside its write footprint: every memory read walked
    to an interval, the address resolved through its reaching definitions first, and where
    the bit analyses state nothing and the address is a pointer deref, the declared blocks
    2b's `ptrextent` observed the web's derefs inside (consumed, never extended — §5). A
    store is then a root only where a reader can observe it: bounded, Z3-proved disjoint
    from every reader interval and outside the device window is scratch. The reader set is
    artifact-wide because memory persists across the frame, and it is every *other*
    procedure's statements plus the rendering one's extracted spellings, since extraction
    is what retires a read. A label and an enumerated dynamic transfer contribute nothing:
    entering anywhere in the artifact reads no more than the union.
  - *The in-edge memory join at a label*, stage 3d landing 1. A label the walk has already
    passed every in-edge of — all of them `goto`s of this procedure, outside any cyclic
    body, none a call, an `swc` label, an RTS-trick landing or a procedure entry — joins
    instead of resetting: a cell every in-edge memory and the fall-through read at one
    common chain version keeps that value, and `_Chain` records the version per cell so a
    later read lands there and stops. A back edge is not weakened, and the reason is proved
    rather than argued: its region is not delimitable below the whole procedure, whose
    write footprint contains every cell the chain holds.
  - *The call/goto closure* (`Footprints`). What entering at a pc may write, over the
    enumerated call/goto graph, as that graph's least fixpoint — a caller writes what
    its callees write. A pc no procedure owns is ⊤, and so is a procedure holding a
    transfer the map cannot follow, so nothing rests on the dispatch guards: what a
    call boundary keeps is bounded by code the map actually reads.
- Saturation blowup: assoc/comm plus the memory axioms are expansive over a whole
  procedure. Mitigation: bounded schedule; saturation is not assumed; extraction
  sound at any cutoff. Per-procedure wall-clock MUST hold the 60s test budget.
  **General associativity is retired, stage 3d landing 4.** Measured, the cost is at
  width **1** (the lane arithmetic) and not at the packs: dropping `add_assoc` took
  `Angry_Birds` 50.6s -> 2.2s while restricting it to either width alone changed
  nothing. What it was buying is one instance -- a lane sum of three terms whose
  numeral addend sits outermost, where the fusion rules match a two-term add whose
  partner is the carry -- so `add_num_in` (`(x + y) + c -> (x + c) + y`, numeral `c`)
  replaces it, directed so no grouping of a chain is enumerated. The corpus artifact
  does not move (aggregate `434f0bab...` over 624) and the exemplar review is clean.
  A chain whose regrouping no admitted rule names extracts as spelled; the standing
  answer to that is flat n-ary associative nodes, an encoding change and not a rule.
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
- Graph BUILD cost is a DAG problem too, and it dominated the proof one: a store of
  a load (`m[a] = m[b]`) embeds the memory chain in its own stored value, so an
  unnamed chain DOUBLES per such store. `Down_Under`'s first procedure is a 340-node
  DAG whose tree expansion is 2.5e11 nodes; the egglog build asked for 49GB and was
  killed before saturation ran. Mitigation, stage 3b: `render_proc` names each memory
  version — `memk(n)` unioned with the store over the previous name — exactly as a
  def names a value, so every term is linear in the statements. The e-graph is
  unchanged (the store e-node is still there, so every axiom fires through the same
  e-class) and so is the emitted text (`_to_ir` drops a `sel`'s memory argument); the
  §6 record carries the version definitions, so a forwarded load is still proved
  against its own store chain and not against an opaque array. 49GB/killed → 0.6s at
  0.10GB.
- Extraction cost: `extract_multiple` per term is not bounded by the saturation
  schedule. Mitigation, stage 3b: `DI_EQLIFT_EMIT_S` is divided over the procedures
  still to render (so slack from one funds the next) **by work, stage 3d landing 4** --
  `remaining × weight[i]/sum(weights[i:])` over the recursive statement-node count,
  because dividing by procedure COUNT gave the largest procedure a one-site trailer's
  share and the trailer returned its own unspent -- and the share funds saturation,
  capped at `DI_EQLIFT_BUDGET_S`, and then extraction. A site past the share renders
  from its own term — position-correct by the renderer discipline, and sound because
  extraction is sound at any cutoff. Never silent: `emit`/`emit_mem`/`render_proc`
  take a `stats` dict carrying the extraction-site and fallback counts. Like the
  saturation bound, a binding budget makes the artifact a function of the clock, so
  an ON/OFF comparison is read at a budget that does not bind.
- Boundary liveness is `frameproc._Flow`, and a transcription of it is a place to
  lose precision: `eqlift_mem._liveness` dropped `_Flow`'s successor-aware cases, so
  every computed transfer read `info.G` — every register the program reads anywhere.
  A `dgoto`/`igoto` the next statement's `swg` enumerates, and a `dcall` an `swc`
  enumerates, land in one of those arms (`frameproc._open_flow` is the same
  invariant), so they read what the arms read; `swg`/`opsw` arms take the switch's
  own live-out, since an arm that falls off its end continues after it. `swc` stays
  conservative — its bare labels are called with no inline body.
- Proof cost is a DAG problem, not a solver problem: `_Z3Env.of` rebuilt shared
  extracted subterms once per occurrence, which is exponential on a DAG. It
  memoizes on the IR node; the same tune goes from unbounded (37GB resident,
  killed) to 40s. One §6 record per PROCEDURE, never one merged record per
  artifact — SSA names are procedure-local, so a merged `defs` would prove
  equalities between different values.
- egglog version drift: extracted-str parsing and RunReport shapes are
  version-sensitive. Mitigation: minor-version pin + `_parse_ir` round-trip
  covered by tests (including the let-lifted multi-line form).
- A local renders as its **base name**, so a spelling is valid only where the base
  still holds that version. `_defined_at` read availability — the versions defined on
  the path — which never drops a name when the base is redefined, so a site could
  spell a stale version and the printed program read the new one. Measured minimal
  case (stage 3b landing 2): `a = m_1000; b = a; a = m_1001; sid.ctrl = b` emitted
  `sid.v1.ctrl = a` after `a` was redefined, with `b`'s definition deleted as unread —
  a wrong byte at the chip. §6's all-sites proof cannot catch it: it proves the SSA
  terms equal while the printer renders the base. Mitigation: a site carries the
  versions **live** there, not the versions available there. This is the memory
  renderer's position-correctness (2026-08-09) stated for locals.
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
  equivalence and wins extraction where the 16-bit vocabulary exists. Printing
  fused byte-pair cells was a dialect prerequisite, not a rule-set problem, and
  the dialect ships it — stage 2's checklist emitted, parsed and executed all 20
  non-unknown normal forms, `pair-row` among them.
