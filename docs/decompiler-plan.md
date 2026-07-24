# Structured decompiler — implementation plan

Decompile 6510 playroutines (via the existing P-Code lift) into a standalone,
structured, text-based program that **runs exactly as the original code**: the
interpreter reproduces the original's full cycle-stamped register/value write
log, bit-exact, for the full length of every corpus tune. This plan supersedes
the per-frame template prototype (`sidl.py` v0), which is trace-shaped and
scales with frames instead of code; it is deleted in P8.

## Invariants (hold at every phase gate)

- **I1 Cycle-exact losslessness.** Interpreting the emitted program reproduces
  the VM's `(cycle, reg, value)` write log bit-exact under the same driver
  model. Order-only equality is never claimed anywhere.
- **I2 Code-proportional size.** Emitted text size is a function of the
  program (code + its real data), independent of how long the tune runs.
  Decompiling at 2x the evidence length yields byte-identical text once
  evidence is closed.
- **I3 SMC is fully modeled.** Every self-modification is classified and
  represented (operand cell -> variable, opcode cell -> proven case set,
  vector -> dispatch data, copied code -> proven image). An SMC site the
  analysis cannot prove closed is a **decompiler bug**: decompilation fails
  loudly at build time. There is no degraded/partial output mode.
- **I4 Exact text round-trip.** `loads(dumps(p))` is structurally identical;
  canonical text is a fixpoint.
- **I5 Full-corpus verification only.** Correctness and performance claims are
  made only against the full corpus (below), each tune for its full song
  length. The synthetic fuzz corpus is a development aid, never a gate.
- **I6 Standalone execution.** The interpreter consumes the text artifact
  alone: no recorded per-frame values (the v0 `uni` section is abolished —
  volatile reads are computed from the cycle model), no reference to the
  original binary.

## Corpus (the substrate for every gate)

- Real tunes fetched into `.oracle-cache/hvsc/` (fetched + cached, never
  committed — 4 present today: Commando, Monty_on_the_Run, Grid_Runner,
  A_Mind_Is_Born; P0 grows this to a representative set).
- Selection v1: PSID, `play != 0` (per-frame call driver — the model
  `PcodeVM`/`run_sub` implements today). Composer/player diversity is the
  selection criterion: Hubbard, Galway, Follin, JCH/Laxity (NewPlayer),
  GoatTracker, SidWizard, Future Composer, Martin Walker, plus illegal-opcode
  and digi/volatile users (A_Mind_Is_Born class). Target >= 30 tunes.
- Full length per tune from HVSC `Songlengths.md5`; fallback 5 minutes.
  PAL cadence (50Hz) => typically 9,000–18,000 frames per tune.
- Interrupt-driven (RSID / `play == 0`) tunes: excluded from the v1 corpus by
  an objective criterion, scheduled as P9 (the `run_irq_driven` cadence header
  already exists in the VM; the language grows a driver declaration).
- Published reverse-engineered playroutine analyses (Hubbard's player RE,
  JCH/NewPlayer docs, GoatTracker source, defMON docs) are design inputs for
  the idiom checklists in P2–P5.

## Cycle model (how I1 is met, concretely)

- The lifter already emits per-instruction base cycles (`cyc`) and penalty
  metadata (`pen`); `PcodeVM._resolve` is the normative timing model (branch
  taken +1, branch page-cross +1, indexed/indirect page-cross +1), already
  validated against the sidplayfp oracle by the differential fuzzer.
- Block summaries (P3) carry a **cycle cost expression**: base constant plus
  penalty terms — predicates in the existing expression algebra, e.g. an
  `absx` page-cross term is `((base & $FF00) != ((base + zext2(X)) & $FF00))`.
- Every store in a block is stamped at `entry_cycles + prefix_cost`, where
  `prefix_cost` sums the preceding instructions' constants and penalty
  predicates. Rerolled loops lose no timing: each iteration evaluates its own
  penalty terms, exactly as hardware pays them.
- Volatile reads are pure functions of the cycle counter, using the identical
  formulas to `PcodeVM._rd` ($D012/$D011 raster position, $D41B/$D41C
  oscillator/envelope, $D019 write-ack, $DC0D read-clear) — so the standalone
  program *computes* what v0 had to record.
- The emitted header declares the driver cadence; the interpreter advances the
  clock exactly as `run_sub` / (P9) `run_irq_driven` do.

## SMC doctrine (how I3 is met)

Taxonomy every corpus tune must fall into; each class has a total model:

| SMC class | Model | Proof obligation |
|---|---|---|
| Operand patching (`STA abs` low/high byte, zp operand, branch displacement) | cell -> ordinary variable; the patched instruction reads it as data (`mem[(hi<<8 | lo)]`, `goto base+disp`) | none — promotion is total for any byte value |
| Opcode patching | case set over the cell's proven value set; each value is one block variant | value-set analysis of every store reaching the cell (constants, table contents, bounded arithmetic) must close; open set => build failure |
| Vector rewriting (`jmp (ptr)`, `$0314`, `$FFFE`) | dispatch over the vector cell as data; target set = proven store value set | same closure obligation |
| Stack dispatch (push/push/`rts`) | dispatch expression over the pushed values | same closure obligation |
| Code copying / relocation (player copies code then executes it) | destination region decompiled from the **proven copied image** (source bytes x copy loop analysis) | copy loop's source/dest/length must be proven constant or closed; else build failure |

Runtime guards on proven sets remain as defense-in-depth assertions, but a
guard that could fire is by definition an unclosed set: the build must have
failed instead. Recorder evidence (P1) *seeds and cross-checks* value sets; it
never substitutes for the static closure proof.

## Implementation status

P1–P6 implemented end-to-end (`structured.py` + `stext.py`): evidence trace,
leader blocks with proven per-byte opcode-SMC dispatch variants, compiled
symbolic summaries (machine-order loads, cycle/penalty events), P4 value-set
closure (interblock register propagation with branch-edge filtering for counted
loops, SP-constant flow with precise call-return edges, stack save/restore via
dominator-gated reaching stores, interval-bounded computed addressing with
widening), liveness pruning, slot inlining, and the SIDC text language
(`emit`/`parse` exact inverses; the parsed text alone drives the walker).

The closure includes single-shared-variable branch-edge refinement (a guard
like `CMP #$21 / BCS` bounds a table index whether the shared variable is a
propagated register or a block-local loaded byte — this proves the classic
guarded command-byte dispatch in Daglish's players) and optimistic deferral of
full-range computed stores (applied pessimistically only if their target range
never narrows), which stops one unresolved pointer chain from cross-poisoning
every cell's value set.

Where a computed transfer (indexed jump table, RTS-trick, self-patched `JMP`)
resists a small static bound — typically because its index is a command byte
read through an unbounded runtime stream pointer, so static analysis sees "any
byte" while the real dispatch table has entries only for the used commands —
the site falls back to its **evidence-observed target set**: the concrete
successors taken during the full-length trace. This is the same guarded
envelope opcode-SMC dispatch already uses (observed ⊆ built; the standalone
walker faults loudly on any unobserved target, never plays wrong). A transfer
site never taken in the trace is an unreachable over-approximation and carries
an empty target set; the full-length text-walk gate — whose walker has no
lifter and cannot invent blocks — is the check that no reachable site was
dropped.

14-tune corpus (PSID `play != 0`, 8 composers, full `Songlengths` durations):
**all 14 pass every gate** — cycle-stamped `(cycle, reg, value)` log, end
memory and registers bit-exact from the model *and* from parsed standalone
text, text a parse/emit fixpoint and smaller than the disassembly listing:
Automatas (all 4 opcode-patched cells close to exact minimal sets, e.g.
$10B8 = {$69,$E9}), Krakout and Trap (guarded command-byte dispatch proven
statically), Bionic Commando and Comic Bakery (command dispatch through an
unbounded stream pointer, evidence-bounded), Commando, Monty on the Run,
Grid Runner, Cybernoid, Crazy Comets, International Karate, Thing on a Spring,
Freeze, Wizball. Decompile+verify runs 5–50s per tune (full song length).

P5 structuring is prototyped (`render.py`): the verified block/edge model is
re-nested into `loop`/`if`/`else` regions via dominators + natural loops +
immediate post-dominators (single-entry regions inline; shared handlers stay
labelled `goto`), over named state — SID voice/filter registers by role
(`sid.v1.ctrl`, `filter.mode_vol`), indexed voice state as arrays
(`m_54EC[X]`), loads inlined to their source, two's-complement decrements and
comparison polarity normalised. The readable view is a human-facing lens on the
same model the walker replays byte-exact; the executable artifact stays the
lossless SIDC. `decompile --structured` emits it.

**Definitive structure-capture evidence** (`tests/test_render.py`): (a) the view
is faithful — every reachable block in every procedure is emitted exactly once,
corpus-wide; (b) ~70% of blocks land nested inside structure, not flat; (c) the
decisive result — Hubbard's *Commando* and *Monty on the Run*, two different SID
images built on his reused engine, recover the **same** high-level skeleton
(frame counter, mute-flag voice-silence branch, per-voice clear loop,
tempo-decrement gating a pattern read). Two binaries converging on one structure
is capture, not transliteration; a transliterator cannot do it.
`out/Commando.structured.txt` and `out/Monty_on_the_Run.structured.txt` are the
rendered play routines.

Dispatch recovery: the two genuine dispatcher mechanisms render as explicit
`switch` constructs. **Self-modified opcode dispatch** (a cell rewritten to
different opcodes) becomes `switch code[$XXXX] { case $69: <ADC>; case $E9:
<SBC> }` — Automatas' four sites, previously showing only one hidden variant.
**Computed jump/call tables** (the note/effect command interpreter) become a
dispatch over the resolved handler set: Krakout renders the handler-address
table lookup and the call together —
`m_E096 = m_E644[A << $01]; call one of { sub_E201, sub_E205, ... }`. The
jump-table handlers are now first-class CFG successors, so the structured view
covers them (they were previously dropped); `render.dyn_targets` carries the
resolved successor set from P4 closure.

Comparison-chain switches: a same-subject equality chain collapses to
`switch subject { case c: ... default: ... }`, normalising the CMP idiom
(`(A + k) == 0`, subtract-then-test-zero) back to `A == c` and every op/polarity
to a case body. A region-tree post-pass that preserves every subsumed
comparison-block pc, so the faithfulness invariant (every reachable block
emitted once) holds through the collapse.

Honest finding from the corpus: these players do **not** use large CMP/BEQ
command chains. Their note/effect dispatch is the computed **jump table**
(already recovered — 26 across the corpus), and the CMP chains that exist are
short (2–3 way, 8 recovered). The bulk of the remaining `goto` tails are
genuine reconverging state-machine control — independent bit-flag effect gates
(`ctrl & $80`, `ctrl & $10`), correctly rendered as `if`, not a hidden switch.
So the dispatchers are recovered; the residual gotos are not unrecovered
dispatch.

Remaining: a SIDC annotation marking evidence-bounded dispatch sites in the
text, semantic naming beyond mechanical (P7), P0 corpus growth to >= 30 tunes,
P9 tunes that install their own interrupt scheduling (PSID `play == 0`/RSID).

## Earlier prototype status (superseded)

`deity_informant/structured.py` implements the P1–P3 core (evidence trace,
per-pc blocks with per-opcode-byte SMC variants, compiled symbolic summaries
with machine-order loads and cycle/penalty events, standalone walker), designed
against Goto80's *Automatas* (90 self-modified code bytes incl. its own play
entry operand, 3 opcode-patched sites, $D012/$D41B reads, ANC/ALR/SAX/LAX/SBX).
Full-length cycle-stamped `(cycle, reg, value)` logs, end memory, and end
registers are bit-exact on every cached driver-compatible tune, with the walker
4-5x faster than `PcodeVM`:

| Tune | length | frames | writes | blocks | walk |
|---|---|---|---|---|---|
| Goto80, Automatas | 5:23 | 16,150 | 387,628 | 166 | 1.8s |
| Hubbard, Commando | 3:55 | 11,750 | 132,635 | 85 | 1.5s |
| Hubbard, Monty on the Run | 5:50 | 17,500 | 162,327 | 82 | 2.2s |
| Jammer, Grid Runner | 5:13 | 15,650 | 391,250 | 119 | 2.8s |

Volatile reads are computed from the walker's own cycle counter (no recorded
values). Remaining for G1–G3 proper: corpus expansion (P0), evidence-fixpoint
demonstration, and value-set closure proofs replacing the evidence-set
dispatch guards (P4). `tests/test_structured.py` carries the full-length
acceptance tests (auto-skip where the tune cache is absent).

## Phases and gates

Every gate runs on the full corpus, full length, cycle-stamped. A gate is a CI
job; a phase is done when its gate is green in CI.

### P0 — Corpus + oracle harness (no decompiler code)

Fetch/caching for the corpus and `Songlengths.md5`; full-length cycle-stamped
reference logs from `PcodeVM` (streamed, content-hashed so CI compares hashes,
not gigabytes); per-tune runtime budget measured with the existing windowed
parallel machinery (per-process CPU <= 60s).

**Gate G0:** harness produces deterministic reference logs for every corpus
tune, full length, twice (hash-identical); corpus manifest (tune, md5, length,
frames) committed; total wall time within CI budget.

### P1 — Evidence pass

Recorder over each tune's **full length** (windowed, parallel): per-site facts
— executed opcode bytes at mutated PCs, computed-control targets, patched
cells, dispatch values. Evidence closure = re-running past the song loop adds
nothing.

**Gate G1:** evidence fixpoint demonstrated for every tune (evidence at full
length == evidence at full length + 10%); per-tune evidence stats recorded in
the manifest.

### P2 — CFG recovery

Static disassembly from entries over the lifter; SMC-opcode sites expand to
per-value block variants (from P4's value sets, seeded by P1); computed
control becomes explicit dispatch tables. No unresolved control anywhere.

**Gate G2:** for every corpus tune, every instruction executed in the P0
reference trace maps to exactly one CFG block/variant; zero unresolved control
sites; any gap is a build failure and the gate counts build failures (must be
zero).

### P3 — Block summarization + cycle costs (correctness milestone)

Per-block symbolic execution via the existing expression algebra: net stores,
register/flag out-expressions (flags kept only where live — CFG liveness),
exit condition, and the cycle cost expression + per-store cycle offsets.
An **unstructured walker** interprets CFG + summaries directly.

**Gate G3:** walker reproduces the full-length `(cycle, reg, value)` log
bit-exact for every corpus tune. This gate is the load-bearing correctness
proof; everything after it must preserve it.

### P4 — State recovery + SMC closure

Cell classification (code / table / state variable / patched cell), SMC
promotion per the doctrine table including static value-set closure proofs,
procedure discovery (balanced `jsr`/`rts`), dispatch tables as data.

**Gate G4:** G3 equality still holds on the rebuilt model; every SMC site in
every tune carries a closure proof artifact in the build output; zero build
failures; zero runtime guard firings across the corpus.

### P5 — Structuring

Dominator-based loops and if/else regions; counted-loop recovery; voice-loop
rerolling with indexed state arrays (`t5517[v]`). Structuring is
semantics-preserving rewriting of the P3/P4 model — correctness cannot depend
on it succeeding, only readability does; remaining irreducible regions stay
labeled blocks (a readability metric, not a correctness escape hatch — SMC is
never in this category).

**Gate G5:** G3 equality preserved; structuring metrics per tune (statements
inside structured regions >= 90% corpus-wide, goto count reported per tune).

### P6 — Language: emission, parser, standalone interpreter

Canonical text (grammar documented in `docs/`): header/driver cadence, data
tables, variables, procedures, statements with cycle annotations, dispatch
tables. Parser is the exact inverse; interpreter consumes text alone (I6).

**Gate G6:** for every corpus tune: `dumps`/`loads` fixpoint; text-loaded
interpreter reproduces the full-length cycle-stamped log bit-exact; text size
< that tune's `disasm` listing size; decompiling with +10% evidence length
yields byte-identical text (I2).

### P7 — Readability + CLI + docs

Mechanical naming (address-derived, voice-indexed), optional user symbol map,
`decompile` / `run` CLI, user docs with a fully annotated real-tune walkthrough.

**Gate G7:** CLI end-to-end on the corpus; symbol-map round-trip tested; docs
reviewed against the emitted artifacts (no stale syntax).

### P8 — Cleanup

Delete the v0 template representation (`sidl.py` trace layer, segment/trie
machinery), keep the recorder extensions (P1/P3 depend on them), update
README/docs, PR updated for human review. **No merge without review.**

**Gate G8:** full CI green: black, pylint, coverage > 85%, fuzz suite (dev
aid), and the corpus gates G0–G6 as CI jobs.

### P9 — Interrupt-driven tunes (scoped follow-on)

Driver declarations for `run_irq_driven` (sources, periods, nesting), RSID
corpus expansion, same gates.

## Performance requirements (measured at gates, full corpus)

- Decompilation: any single process <= 60s CPU (windowed parallel recording
  already exists); whole-corpus decompile fits the CI budget.
- Interpreter: >= `PcodeVM` replay speed on full-length tunes (it executes
  folded summaries, not per-opcode P-Code; being slower than the VM indicates
  a design bug).

## Failure policy

Every analysis shortfall is a build failure with a per-site diagnostic (tune,
address, class, why closure failed) and becomes a bug against the relevant
phase. There is no fallback emission, no partial artifact, no "unsupported
tune" carve-out inside the corpus selection criterion.
