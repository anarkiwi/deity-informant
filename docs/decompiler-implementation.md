# Structured decompiler — implementation specification

> **Superseded as a plan; retained because it is normative.** This is the
> **landed sidprog specification**, not a stray: frameprog.md and soundness.md
> cite it normatively (v1 class scoping, handler entry), which is why the
> 2026-08-09 pivot kept it when the other pre-pivot plan docs were deleted. The
> active plan is docs/register-model-lift-impl.md. The sidprog **emit path,
> grammar dialect and tree walker are retired** under that plan's housekeeping;
> this document is their record, and nothing reads or writes sidprog text. The
> soundness contract of §0.1 — observed sets plus faulting guards, static
> analysis as optional certification — is the kernel that outlives them and is
> unchanged, held now on the model and on frameprog (docs/frameprog.md).

This specifies how to replace the working **prototype**
(`deity_informant/structured.py`, `render.py`, `stext.py`) with a **complete,
production implementation**. The prototype proved the approach: 14 HVSC tunes
across 8 composers decompile to standalone SIDC text that replays each original's
full-length cycle-stamped `(cycle, reg, value)` SID write log bit-exact, and to a
structured view that recovers playroutine architecture (two Hubbard tunes on his
reused engine yield the same skeleton). The prototype's central conceptual
error: it made the flat SIDC text the lossless artifact and the structured
program an advisory view. **The deliverable is the structured program itself —
canonical, standalone, byte-exact (§0.3)**; SIDC is scaffolding to be deleted.
What remains is inverting that split, then proven soundness, complete coverage,
and the specified structured language. The prototype's phase/gate history is
in git history (`docs/decompiler-plan-prototype.md`, retired 2026-08-09).

The contract below is normative. "MUST" is a gate; "prototype:" notes what the
current code does and why it is not yet sufficient.

## 0. What complete means

The prototype is *observationally* correct over a recorded window and *usually*
statically closed. The complete implementation MUST be:

1. **Sound by construction: observed behavior + runtime guards.** The artifact
   is the per-tune observed program: every serialized value set, control-target
   set, and SMC opcode set IS exactly the full-length trace-observed set, and
   every such site carries a runtime guard that faults loudly (with the site pc
   and the value) on anything outside it. This is sound by construction — the
   observed sets are by definition what the evidence run executes, and no
   unobserved behavior can be silently invented or silently entered. Static
   analysis is **optional certification**: it MAY mark a guard provably dead
   (its static set EQUALS the observed set) and MUST NOT widen any artifact
   set; a wider static set is recorded in the proof report for reference and
   the guard stays live. `--sound` means every control guard is certified dead.
2. **Total over the input class.** Every tune in the declared class (§1)
   decompiles or fails with a precise, actionable diagnostic. No silent
   drop, no partial artifact, no "unsupported" carve-out inside the class.
3. **One artifact: the structured program IS the deliverable.** There is a
   single output — structured text (procedures of `loop`/`if`/`else`/`switch`
   over named state) that is canonical, parses back exactly, and executes
   standalone cycle-exact. It is NOT an advisory "view" beside a flat lossless
   text; the flat SIDC block language is a prototype scaffold and is deleted
   (§6, §9). Everything execution needs — header/driver, data image, cycle and
   penalty semantics, register state, dispatch guard sets — lives in the
   structured text itself.
4. **Structurally faithful and readable.** The structuring is a total,
   goto-minimal, *invertible* re-nesting: flattening the parsed region tree
   reproduces the block model, checked at build time (§5).
5. **Play-phase scope.** The program decompiled is the **playroutine**: the
   code reachable from the play boundary on. `init` (decompression, memory
   relocation, table building) executes concretely in the evidence VM and is
   NEVER decompiled; its result is the artifact's data image (the post-init
   memory snapshot) plus a `sid-init` prologue — init's SID writes,
   order-preserved, replayed before frame 0. Cycle-exactness is normative from
   the first play-frame entry. This is a deliberate simplification: init-time
   unpackers/copy loops are the worst static-analysis subjects and their
   self-modification becomes snapshot data with zero proof obligation.

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
                                              structurer (region tree) ┤
                                                                       ▼
                                                          structured text (canonical)
                                                                       │
                                       parser ─► region tree ─► flatten ─► block model
                                                                       │
                                                        walker (standalone, cycle-exact)
```

- `evidence` (replaces the `RecVM`/trace parts of `structured.py`): concrete
  full-length run producing the oracle log, written-cell set, executed
  instruction identities, taken control edges. Runs init concretely to the
  play boundary — the post-init snapshot is the artifact's `image`, init's
  SID writes its prologue; only play-phase facts feed analysis. Seeds
  analysis; is the oracle.
- `lift`/`cfg`: the existing lifter, plus a CFG builder keyed on **block
  identity `(pc, opcode)`** (not pc) so self-modified variants are first-class.
- `block IR`: per-block ordered events (`ld`/`st`/`cyc`/`pen`) + terminator +
  register out-expressions, over the `expr` algebra. Keep the algebra; give the
  block a stable dataclass, not a tuple soup.
- `analysis` (the load-bearing rewrite, §4): value-set closure, SP/stack flow,
  dominators/post-dominators, SMC closure, dispatch resolution — all producing
  **proof objects**, not booleans.
- `model`: blocks + resolved edges + proof artifacts + the transition function.
- `structurer` (§5): model ⇄ region tree, an invertible codec — `flatten` is its
  exact inverse and a build-time check.
- `emitter`/`parser`/`walker` (§6): region tree ⇄ canonical structured text;
  the parsed text lowers through `flatten` to the block model, and ONE walker
  core executes it. No second interpreter, no flat sibling format.

## 3. Cycle and IO model (unchanged contract, hardened)

- The lifter's per-instruction `cyc`/`pen` and `PcodeVM._resolve` timing are
  normative; block summaries carry a **cycle-cost expression** (base + page-cross
  / branch-penalty predicates) and stamp every store at its prefix-sum offset.
- Volatile IO MUST be computed from the walker's own cycle counter using the
  identical formulas to `PcodeVM._rd` ($D011/$D012 raster, $D41B/$D41C
  osc/env, $D019 write-ack, $DC0D read-clear). No recorded values in the
  artifact. (Prototype: done; keep.)
- **Gate C:** for every corpus tune, the model walker AND the walker running
  the parsed *structured* text (the only text) reproduce the full-length
  play-phase `(cycle, reg, value)` log, end memory, and end registers
  bit-exact, after replaying the `sid-init` prologue (order-preserved) before
  frame 0.

## 4. Analysis — from observational to sound (the core work)

Rewrite `structured.py`'s `Analysis` as an abstract interpreter producing proofs.
Requirements, each with a proof obligation the build MUST discharge or fail:

### 4.1 Value-set / interval domain
- A cell/register abstract value is a **finite value set (≤ K) or a bounded
  interval or ⊤**. ⊤ that reaches a construct requiring a bound (dispatch index,
  jump vector, SMC opcode) means that site's guard **cannot be certified** —
  never a widening to "any" that is silently accepted. (Prototype: ⊤ is admitted
  as "any byte" in places, and the interval `_ivals`/widening/`_pair_targets`/
  optimistic-store logic is heuristic; replace with a specified lattice +
  transfer functions + monotone fixpoint with a proven termination bound.)
- **Termination MUST be by a well-founded widening operator**, specified and
  tested, not an iteration cap. Document the lattice height and the widening.

### 4.2 Pointer & indirect reads
- A load through a zero-page pointer pair MUST resolve to the byte-value set of
  the region the pointer provably ranges over, when that region is immutable;
  else the value is ⊤ and any construct depending on it stays uncertified.
  Specify pointer-range recovery (recurrence over the pointer's def–use, bounded
  by the immutable-data segments the tune actually addresses).
- This is the missing lemma keeping Bionic Commando / Comic Bakery / Wizball
  sites guard-live; certifying them needs the pointer bound proved.

### 4.3 SMC certification (play-phase only)
Init-time SMC (decompression, relocation, code copy) is baked into the snapshot
and carries no obligation. Operand patches: total, no obligation. Opcode
patches, vector rewrites, stack-dispatch, play-time code-copy: the artifact set
is the observed set behind a runtime guard (§0.1); value-set/region closure MAY
certify the guard dead when the static set equals the observed set.

### 4.4 Dispatch certification
- Computed jumps/calls (`jmp (ind)`, RTS-trick, self-patched `JMP`) serialize
  their observed target set; static resolution (indexed jump tables recovered
  as index domain × table, §4.1–4.2) only certifies the guard.
- A static set wider than observed keeps the guard live and is recorded in the
  proof report; it is NEVER serialized as arms.

### 4.5 Proof artifacts
Every dynamic site MUST carry a serializable certification record (site, kind,
status observed/certified, the observed set, and the static derivation or
refusal) emitted in a build report, auditable and diffable across tunes.

**Gate A (certification):** every dispatch/SMC/vector site has a certification
record; zero runtime guard firings on replay; every uncertified site carries
the exact static refusal (the tracked missing lemma) — not a silent pass.

## 5. Structuring — the canonical artifact (not a view)

The structured program is the deliverable. Replace the prototype's
dominator/post-dominator + single-entry-inline heuristic with a specified,
total, **invertible** structural analysis:

- **Invertibility MUST be a build-time assertion:** `flatten(structure(model))`
  reproduces the block model (blocks, edges, dispatch, cycle semantics) up to
  naming. Every rewrite the emitter performs — load inlining, CMP-idiom
  normalization (`(A±k)==0` ⇒ `A==c`), switch collapse, two's-complement
  decrements — MUST be exactly reversible by the parser/flattener or MUST NOT
  be performed. Readability transforms that lose information are forbidden;
  readability is achieved by naming and nesting, not by dropping semantics.
- **Completeness of content:** the structured text carries the program header
  (`play`/`subtune`, driver cadence, outputs, the `sid-init` prologue), the
  post-init data image, explicit register state (A/X/Y/SP/flags are
  language-level state — Hubbard's CLV/BVC `if V != 0` idiom is real code),
  per-block cycle costs with penalty predicates and per-store cycle stamps
  (compact annotation syntax; they parse), volatile-read semantics, and
  dispatch/SMC guard sets. ALL play-phase procedures are structured —
  everything reachable from the play boundary; init is concrete evidence, not
  program (§0.5).

- **Reducible regions MUST fully structure** to `if`/`else`/`while`/`loop` with
  `break`/`continue`; irreducible regions MUST use the minimal labelled-`goto`
  set (node splitting or controlled goto per a documented algorithm, e.g. the
  "No More Gotos" DREAM approach or Havlak intervals). Goto count MUST be a
  reported metric with a per-tune budget, not incidental.
- **Faithfulness is subsumed by invertibility**: the flatten check verifies
  every emitted leaf — primary or duplicate copy — against its block's
  terminator, in the pipeline, failing the build on any mismatch. Coverage
  law: every reachable block appears at least once, as exactly one labelled
  primary or as verified duplicate copies alone. Duplication is bounded to
  tiny reconvergence tails (single variant, ≤ 3 stores, rts or static-jump
  terminator, no loop membership) replacing gotos, plus controlled node
  splitting (Janssen & Corporaal, TOPLAS 19(6) 1997) at RC-set nodes — the
  retreating-edge heads that do not dominate their source, i.e. the entries
  that make a region irreducible. A split copies the region reachable from
  that entry, cut at the arm's join, and is kept only where it empties a
  label; the bounds are `render._SPLIT_BLOCKS` blocks per split,
  `_SPLIT_TOTAL` × the procedure's block count in total and `_SPLIT_DEPTH`
  nested splits, so an irreducible region cannot blow up exponentially. The
  copy count is the `dup_blocks` metric. (Prototype: faithfulness was only a
  pytest walk over an advisory view; the view concept is abolished.)
- **Dispatch recovery** (done in prototype, keep + extend): opcode-SMC →
  `switch code[$XXXX]`; computed jump/call → dispatch over the proven target
  set; same-subject comparison chains → `switch subject { case c: … }` with the
  `(A±k)==0` CMP-idiom normalization. Extend to nested/range dispatch.
- **Semantic naming (P7).** Beyond the mechanical `m_XXXX` / `sid.vN.*`: recover
  voice-indexed state as named arrays (`voice.note[v]`), classify cells by
  role from access patterns (sequence pointer, tempo counter, envelope index),
  and accept an optional user symbol map that overrides names and round-trips.
- The structured text MUST NOT drop cycle annotations or any other semantics:
  it is the executable. Rendering/pretty-printing options MAY elide annotations
  for *display only*; the canonical file always carries them.

**Gate S:** corpus-wide, `flatten(parse(emit(model)))` ≡ model (built-in
checker), ≥ 95% of blocks structured (nested, non-goto), goto budget met per
tune. Gotos over labelled blocks are legal language (totality never depends on
structuring quality); the budget is the readability metric.

## 6. The structured language specification (SIDC is deleted)

The flat SIDC block language (`stext.py`) was a prototype scaffold that
mistook the deliverable: it made the lossless artifact flat and demoted the
structured program to a lens. The complete implementation ships exactly ONE
language — the structured program of §5 — and deletes `stext.py`, the `.sidc`
output, and the `sidc-run` CLI. SIDC's serialization payloads (header, image
section, cycle/penalty annotation syntax, dispatch guard sets, versioning)
migrate into the structured grammar; its flat-block statement form does not.

- A written grammar (EBNF) for the structured document: header, `image`,
  `regs`, symbol map, procedures of nested regions (`loop`/`if`/`else`/
  `switch`/`goto`/labels), the expression algebra, cycle/penalty annotations,
  and dispatch/guard constructs.
- **Round-trip law:** `emit(parse(t)) ≡ t` (canonical fixpoint) and
  `flatten(parse(emit(model))) ≡ model`; MUST be a property test over generated
  models, not just corpus samples.
- **Versioned**, with a compatibility policy; unknown future-version constructs
  MUST fail cleanly.
- Execution path: parse → region tree → `flatten` → block model → the ONE
  walker core (shared with the model walker; no drift, no second interpreter).
- Dispatch constructs carry their observed (serialized, guarded) sets in the
  text; certification status lives in the proof report (§4.5).

**Gate L:** grammar published; property-based round-trip laws green; the
structured text alone replays cycle-exact corpus-wide; text smaller than the
disassembly listing.

## 7. Verification, tooling, performance

- **Gates run full-corpus, full-length, in CI-representative form.** Because
  HVSC cannot be committed, the committed synthetic corpus MUST independently
  hit every gate's code paths; a separate, opt-in job runs the real corpus from
  a cached HVSC (documented fetch), and its results (proof reports, byte-exact
  logs as content hashes) are recorded.
- **Differential oracle:** keep the existing byte-exactness fuzzer and the
  sidplayfp oracle; extend to assert the proof-report invariants.
- **CLI:** `decompile` (emits the structured program), `run` on a structured
  file, `--verify`, `--subtune`, `--report` (proof artifacts). Stable,
  documented. (`sidc-run` and `.sidc` output are deleted in §9 step 6.)
- **Performance:** any single process ≤ 60 s CPU (windowed parallel recording
  exists); whole-corpus decompile within the CI budget; the walker ≥ `PcodeVM`
  replay speed (it executes folded summaries).
- **Coverage:** > 85% with the HVSC corpus absent (synthetic corpus carries it).

## 8. Interrupt-driven tunes (v2 / P-INT)

`play == 0` / RSID tunes install their own scheduler. The complete
implementation MUST:
- Decompile the installed handler(s) (via `$0314`/`$FFFE`/NMI discovery), model
  timer/raster state as first-class, and represent the **driver cadence** (CIA
  periods, raster positions, nesting, idle) in the structured header the walker
  honors. The play boundary (§0.5) is where the installed scheduler first
  fires: installation code is init-phase, concrete, snapshot —
  the VM already has `run_irq_driven`; the language and walker MUST gain the
  declaration and scheduler.
- Gate C/L/S extend to this class unchanged (cycle-exact, faithful, specified).

### 8.1 Landed: the per-frame handler entry (one call per frame)

Handler discovery and decompilation are in, at the v1 cadence (one handler
invocation per frame). **The frame program is the handler itself.** After init,
`c64.installed_vector` reads the vector init wrote (CINV `$0314`, hardware
`$FFFE`, NMI `$0318`, in that order; else a CINV lying inside the load image)
and `play` becomes the handler that vector names.

The bytes pushed below it are the **invocation convention**, not text: `vm.irq_push`
writes the 6510's return word and P, plus the KERNAL `$FF48` prologue's A/X/Y save
when the tune drives CINV, and `structured.Evidence.play_frame` records how many.
`RTI` is then the frame boundary — `_BlockBuilder` lowers it to the `rts`
terminator that pops three, so every executor returns from the frame there and
neither the pushed word nor P is ever named. A CINV handler's own epilogue
(`PLA TAY PLA TAX PLA`, or `JMP $EA81` through the ROM-free stubs
`c64.install_kernal_irq_stubs` writes) pulls the convention's A/X/Y back, so the
frame body's stack effect is `play_frame` minus the terminator's pop: rung (d0')
is asked for that displacement rather than zero and the drop still holds.

Nothing in the image says how many bytes were pushed, so the artifact states it:
`entry-frame N` in the header, emitted only when it is not a called play routine's
two, and `frameprog.block_model` re-derives the same program from it.

Refusals are explicit: no installed vector, a vector installed as `$0000`, or a
load image that claims the KERNAL epilogue a CINV handler may exit through.

Still v2: the driver cadence (multi-speed CIA/raster ticks, nesting, idle) and
the interrupt-source state a handler polls — `$D019`/`$DC0D` read as the
constant-0 sources of the per-frame driver, so a handler that dispatches on
"who fired" sees nothing and plays silently.

## 9. Migration plan (prototype → complete)

Ordered, each step gated and independently shippable:

1. **Freeze the oracle at the play boundary.** Re-cut the reference logs to
   play-phase (`sid-init` prologue + frame-0-onward cycle-stamped log),
   content-hash them as regression fixtures; the complete implementation MUST
   reproduce them. Re-evaluate every evidence site and missing lemma under the
   boundary — init-phase SMC and init-only patched cells drop out (measured:
   Ghouls_n_Ghosts $7316 and Bionic's copy-loop aliasing writer are
   init-phase).
2. **Block IR + CFG on `(pc,opcode)` identity.** Replace tuple blocks with a
   dataclass; make SMC variants first-class nodes. No behavior change; re-green
   Gate C.
3. **Analysis rewrite (§4)** tune-by-tune, growing the certified fraction.
   Success = every corpus guard certified dead; each guard-live site carries
   its tracked missing lemma in the proof report.
4. **Structurer → invertible codec (§5).** Lift the region-tree builder out of
   `render.py` into a codec module; implement `flatten` and the build-time
   equivalence check; make every emitter rewrite reversible; structure ALL
   play-phase procedures.
5. **Structured language (§6).** Grow the grammar to carry header/image/regs/
   cycles/dispatch (migrating `stext.py`'s serializers); parser + `flatten` +
   the one walker core; move Gate C/L acceptance onto the structured text
   against the frozen oracle logs.
6. **Delete SIDC.** Remove `stext.py`, `.sidc` emission, `sidc-run`; update
   CLI/README/docs. Gate C green on the structured artifact is the
   precondition.
7. **Goto-minimal structuring + semantic naming** to the Gate S bar.
8. **v2/P-INT (§8).**
9. **Delete prototype scaffolding** (`decompiler-plan-prototype.md` retired
   2026-08-09), fold this document into the shipped `docs/`.

Each step MUST leave the tree green (Gate C at minimum) and MUST NOT regress the
committed synthetic-corpus coverage or the frozen oracle.

## 10. Definition of done

- Every v1-class corpus tune: proof-backed decompile (no evidence-only sites),
  and ONE artifact — a round-trip-canonical structured program (≥95%
  structured, flatten-verified) whose text alone plays the song: prologue +
  play-phase cycle-stamped log bit-exact — or a precise build failure naming
  the missing lemma, with that lemma on a tracked list.
- v2/P-INT class meets the same bar.
- Grammar, proof-report format, and CLI documented; synthetic corpus carries all
  gates with HVSC absent; real corpus job green and recorded.
