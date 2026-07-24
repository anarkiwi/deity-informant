# Structured decompiler — implementation specification

This specifies how to replace the working **prototype**
(`deity_informant/structured.py`, `render.py`, `stext.py`) with a **complete,
production implementation**. The prototype proved the approach: 14 HVSC tunes
across 8 composers decompile to standalone SIDC text that replays each original's
full-length cycle-stamped `(cycle, reg, value)` SID write log bit-exact, and to a
structured view that recovers playroutine architecture (two Hubbard tunes on his
reused engine yield the same skeleton). What remains is turning
proof-of-concept passes into a system with proven soundness, complete coverage,
and a specified language. The prototype's phase/gate history is archived in
[decompiler-plan-prototype.md](decompiler-plan-prototype.md).

The contract below is normative. "MUST" is a gate; "prototype:" notes what the
current code does and why it is not yet sufficient.

## 0. What complete means

The prototype is *observationally* correct over a recorded window and *usually*
statically closed. The complete implementation MUST be:

1. **Sound by construction, not by trace.** Every value set, control target, and
   SMC closure MUST be justified by a static argument checked in code, or the
   build MUST fail loudly at the specific site. The evidence trace MAY seed and
   cross-check, but MUST NOT be the sole justification for any emitted construct.
   (Prototype: computed-dispatch sites fall back to the *evidence-observed*
   target set when static closure overflows — sound for the recorded window and
   guarded at run time, but not a static proof. This is the single largest gap.)
2. **Total over the input class.** Every tune in the declared class (§1)
   decompiles or fails with a precise, actionable diagnostic. No silent
   drop, no partial artifact, no "unsupported" carve-out inside the class.
3. **Losslessly executable and canonically textual.** The SIDC language is
   fully specified (§6), `loads`/`dumps` are exact inverses, and the standalone
   walker reproduces the cycle-stamped log without the original binary or any
   recorded values.
4. **Structurally faithful and readable.** The structured view is a total,
   goto-minimal re-nesting whose every construct is verified against the model
   (§5).

## 1. Input class and corpus

- **v1 class:** PSID/RSID images with `play != 0` (per-frame driver), NTSC or
  PAL, using any documented NMOS 6510 opcode incl. illegals. Subtune selection
  via `startsong` (0-based in A to init) MUST be honored.
- **v2 class (P-INT):** `play == 0` / RSID tunes that install their own
  interrupt scheduling (CIA/raster vectors, multi-speed, mid-tune reprogram,
  nested `CLI`). See §8.
- **Corpus MUST be ≥ 100 tunes**, selected for player/composer diversity
  (Hubbard, Galway, Follin, Daglish, Tel, JCH/Laxity NewPlayer, GoatTracker,
  SidWizard, Future Composer, Martin Walker, digi/volatile users), fetched and
  cached (never committed — copyrighted). Each tune runs its default subtune
  for its full `Songlengths.md5` duration.
- Coverage-of-record: **binary-committed synthetic corpus.** Because HVSC tunes
  cannot be committed, the test suite MUST reach its coverage and gate targets
  with the corpus *absent*, using non-copyrighted hand-assembled programs
  (`tests/_fuzzgen.py` extended) that exercise every analysis and rendering
  path. CI MUST NOT depend on HVSC.

## 2. Architecture of the complete system

Replace the three prototype modules with a layered pipeline; each layer has a
typed interface and its own test surface.

```
  image + entry ─► evidence ─► lifter/CFG ─► block IR ─► analysis ─► model
                                                                       │
                        ┌──────────────────────────────────────────────┤
                        ▼                                              ▼
                   SIDC text  ◄────────  emitter ◄─ model      structured view ◄─ structurer
                        │                                              (render)
                   parser ─► walker (standalone, cycle-exact)
```

- `evidence` (replaces the `RecVM`/trace parts of `structured.py`): concrete
  full-length run producing the oracle log, written-cell set, executed
  instruction identities, taken control edges. Seeds analysis; is the oracle.
- `lift`/`cfg`: the existing lifter, plus a CFG builder keyed on **block
  identity `(pc, opcode)`** (not pc) so self-modified variants are first-class.
- `block IR`: per-block ordered events (`ld`/`st`/`cyc`/`pen`) + terminator +
  register out-expressions, over the `expr` algebra. Keep the algebra; give the
  block a stable dataclass, not a tuple soup.
- `analysis` (the load-bearing rewrite, §4): value-set closure, SP/stack flow,
  dominators/post-dominators, SMC closure, dispatch resolution — all producing
  **proof objects**, not booleans.
- `model`: blocks + resolved edges + proof artifacts + the transition function.
- `emitter`/`parser`/`walker` (§6), `structurer`/`view` (§5).

## 3. Cycle and IO model (unchanged contract, hardened)

- The lifter's per-instruction `cyc`/`pen` and `PcodeVM._resolve` timing are
  normative; block summaries carry a **cycle-cost expression** (base + page-cross
  / branch-penalty predicates) and stamp every store at its prefix-sum offset.
- Volatile IO MUST be computed from the walker's own cycle counter using the
  identical formulas to `PcodeVM._rd` ($D011/$D012 raster, $D41B/$D41C
  osc/env, $D019 write-ack, $DC0D read-clear). No recorded values in the
  artifact. (Prototype: done; keep.)
- **Gate C:** for every corpus tune, the model walker AND the parsed-text walker
  reproduce the full-length `(cycle, reg, value)` log, end memory, and end
  registers bit-exact.

## 4. Analysis — from observational to sound (the core work)

Rewrite `structured.py`'s `Analysis` as an abstract interpreter producing proofs.
Requirements, each with a proof obligation the build MUST discharge or fail:

### 4.1 Value-set / interval domain
- A cell/register abstract value is a **finite value set (≤ K) or a bounded
  interval or ⊤**. ⊤ that reaches a construct requiring a bound (dispatch index,
  jump vector, SMC opcode) is a **build failure at that site**, never a widening
  to "any" that is silently accepted. (Prototype: ⊤ is admitted as "any byte" in
  places, and the interval `_ivals`/widening/`_pair_targets`/optimistic-store
  logic is heuristic; replace with a specified lattice + transfer functions +
  monotone fixpoint with a proven termination bound.)
- **Termination MUST be by a well-founded widening operator**, specified and
  tested, not an iteration cap. Document the lattice height and the widening.

### 4.2 Pointer & indirect reads
- A load through a zero-page pointer pair MUST resolve to the byte-value set of
  the region the pointer provably ranges over, when that region is immutable;
  else the value is ⊤ and any construct depending on it fails. Specify
  pointer-range recovery (recurrence over the pointer's def–use, bounded by the
  immutable-data segments the tune actually addresses).
- This is what the prototype's evidence fallback stands in for on Bionic
  Commando / Comic Bakery / Wizball. The complete implementation MUST either
  prove the bound or fail — not fall back to observed targets.

### 4.3 SMC closure (the doctrine, enforced)
Each self-modification class MUST close with a proof (see the doctrine table in
the prototype plan). Operand patches: total, no obligation. Opcode patches,
vector rewrites, stack-dispatch, code-copy: value-set/region closure MUST prove
the exact reachable set; observed ⊆ proven is a **check**, never the definition.
Runtime guards remain as defense-in-depth; a guard that *could* fire means the
proof was incomplete and the build MUST have failed instead.

### 4.4 Dispatch resolution
- Computed jumps/calls (`jmp (ind)`, RTS-trick, self-patched `JMP`) MUST resolve
  to a **statically proven** target set. Indexed jump tables MUST be recovered
  as (index domain × table) with the index domain proven bounded (§4.1–4.2).
- **No evidence-bounded dispatch in the complete implementation.** Remove
  `evidence_sites`; a site that does not statically close is a failure with a
  diagnostic naming the unresolved cell/pointer and the analysis that gave up.

### 4.5 Proof artifacts
Every resolved site MUST carry a serializable proof record (site, kind, the
value sets and the derivation) emitted in a build report, so soundness is
auditable and diffable across tunes and code changes.

**Gate A (soundness):** across the corpus, zero evidence-only justifications;
every dispatch/SMC/vector site has a proof record; zero runtime guard firings;
every build failure has a site-specific diagnostic. A tune that the prototype
only closed via evidence MUST either close statically or appear on a tracked
**"needs analysis" list with the exact missing lemma** — not silently pass.

## 5. Structuring & the readable view (P5, completed)

Replace the prototype's dominator/post-dominator + single-entry-inline heuristic
with a specified, total structural analysis:

- **Reducible regions MUST fully structure** to `if`/`else`/`while`/`loop` with
  `break`/`continue`; irreducible regions MUST use the minimal labelled-`goto`
  set (node splitting or controlled goto per a documented algorithm, e.g. the
  "No More Gotos" DREAM approach or Havlak intervals). Goto count MUST be a
  reported metric with a per-tune budget, not incidental.
- **Faithfulness MUST be a build-time assertion**, not a test-only walk: the
  region tree emits each reachable block exactly once; a checker runs in the
  pipeline and fails the build otherwise. (Prototype: faithfulness is only a
  pytest walk; promote it into the structurer.)
- **Dispatch recovery** (done in prototype, keep + extend): opcode-SMC →
  `switch code[$XXXX]`; computed jump/call → dispatch over the proven target
  set; same-subject comparison chains → `switch subject { case c: … }` with the
  `(A±k)==0` CMP-idiom normalization. Extend to nested/range dispatch.
- **Semantic naming (P7).** Beyond the mechanical `m_XXXX` / `sid.vN.*`: recover
  voice-indexed state as named arrays (`voice.note[v]`), classify cells by
  role from access patterns (sequence pointer, tempo counter, envelope index),
  and accept an optional user symbol map that overrides names and round-trips.
- The structured view MAY drop cycle annotations for readability but MUST remain
  derivable from — and consistent with — the exact model; a `--verify-view`
  mode MUST check that the view's control/data flow matches the model.

**Gate S:** corpus-wide, the view is faithful (built-in checker), ≥ 95% of
blocks structured (nested, non-goto), goto budget met per tune, and every
emitted construct verified against the model.

## 6. SIDC language specification (P6, completed)

The prototype's `stext.py` is an ad-hoc emit/parse pair. The complete
implementation MUST ship a **specified language**:

- A written grammar (EBNF) for the document: header (`init`/`play`/`subtune`/
  `outputs`/`dispatch`), `image`, `regs`, procedures, blocks, the expression
  algebra, and dispatch/switch constructs.
- **Round-trip law:** `loads(dumps(m)) ≡ m` and `dumps` is a canonical fixpoint;
  MUST be a property test over generated models, not just corpus samples.
- **Versioned:** `sidc <major>` with a compatibility policy; unknown
  future-version constructs MUST fail cleanly.
- The walker MUST be the single execution semantics for the text; the model
  walker and text walker MUST share one interpreter core (no drift).
- Evidence-bounded sites are removed (§4.4); any remaining dispatch is annotated
  with its proven set in the text.

**Gate L:** grammar published; property-based round-trip law green; text-only
walker cycle-exact corpus-wide; text smaller than the disassembly listing.

## 7. Verification, tooling, performance

- **Gates run full-corpus, full-length, in CI-representative form.** Because
  HVSC cannot be committed, the committed synthetic corpus MUST independently
  hit every gate's code paths; a separate, opt-in job runs the real corpus from
  a cached HVSC (documented fetch), and its results (proof reports, byte-exact
  logs as content hashes) are recorded.
- **Differential oracle:** keep the existing byte-exactness fuzzer and the
  sidplayfp oracle; extend to assert the proof-report invariants.
- **CLI:** `decompile` (SIDC or `--structured`), `sidc-run`, `--verify`,
  `--subtune`, `--report` (proof artifacts). Stable, documented.
- **Performance:** any single process ≤ 60 s CPU (windowed parallel recording
  exists); whole-corpus decompile within the CI budget; the walker ≥ `PcodeVM`
  replay speed (it executes folded summaries).
- **Coverage:** > 85% with the HVSC corpus absent (synthetic corpus carries it).

## 8. Interrupt-driven tunes (v2 / P-INT)

`play == 0` / RSID tunes install their own scheduler. The complete
implementation MUST:
- Decompile the installed handler(s) (via `$0314`/`$FFFE`/NMI discovery), model
  timer/raster state as first-class, and represent the **driver cadence** (CIA
  periods, raster positions, nesting, idle) in a SIDC header the walker honors —
  the VM already has `run_irq_driven`; the language and walker MUST gain the
  declaration and scheduler.
- Gate C/L/S extend to this class unchanged (cycle-exact, faithful, specified).

## 9. Migration plan (prototype → complete)

Ordered, each step gated and independently shippable:

1. **Freeze the oracle.** Lock the prototype's byte-exact full-length logs
   (content-hashed) for the current corpus as regression fixtures; the complete
   implementation MUST reproduce them.
2. **Block IR + CFG on `(pc,opcode)` identity.** Replace tuple blocks with a
   dataclass; make SMC variants first-class nodes. No behavior change; re-green
   Gate C.
3. **Analysis rewrite (§4)** behind a flag, tune-by-tune, keeping the evidence
   fallback available but *counting* every use. Success = zero evidence uses on
   the corpus; each remaining use is a tracked missing lemma. Then delete the
   fallback and `evidence_sites`.
4. **Structurer rewrite (§5)** with the built-in faithfulness checker and the
   goto-minimal algorithm; promote naming to semantic.
5. **SIDC spec (§6):** write the grammar, add the property-based round-trip law,
   unify the two walkers.
6. **v2/P-INT (§8).**
7. **Delete prototype scaffolding**, retire `decompiler-plan-prototype.md`,
   fold this document into the shipped `docs/`.

Each step MUST leave the tree green (Gate C at minimum) and MUST NOT regress the
committed synthetic-corpus coverage or the frozen oracle.

## 10. Definition of done

- Every v1-class corpus tune: proof-backed decompile (no evidence-only sites),
  cycle-exact standalone replay from text, faithful ≥95%-structured view,
  round-trip-canonical SIDC — or a precise build failure naming the missing
  lemma, with that lemma on a tracked list.
- v2/P-INT class meets the same bar.
- Grammar, proof-report format, and CLI documented; synthetic corpus carries all
  gates with HVSC absent; real corpus job green and recorded.
