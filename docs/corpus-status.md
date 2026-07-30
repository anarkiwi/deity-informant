# Corpus status (full-length gate)

Full-corpus, full-Songlengths measurement (2026-07-25, observed-primary
commit + structurer codec + named-state `sidprog` canonical text): 140 cached
v1 tunes. Gates per tune: bit-exact cycle-stamped play log from the model AND
from parsed standalone sidprog text, parse/emit fixpoint, in-pipeline
`flatten(structure(model)) == CFG`, text smaller than the disasm listing.

**140/140 pass every gate.**

## Observed-primary flip (this milestone)

Every committed artifact set — dispatch opcode sets, computed-transfer target
sets, switch case lists, dynamic-landing labels — is now exactly the
trace-observed set; runtime guards fault on anything else, and static analysis
is demoted to certification accounting (docs/soundness.md). Serialized
static-envelope arms (256-wide SMC-JMP case lists, proven-but-unobserved
frontier arms of paired envelopes) are gone from the artifact.

Measured effects (before -> after, full Songlengths):

- **Athena** (the flip's motivating case): its guarded SMC-JMP envelope
  switches collapse from 256-wide case lists to the observed widths (e.g.
  `$C26D`: 256 static arms -> 4 observed). Gotos 437 -> 14, labels -> 19,
  frontier 71 -> 6, text 129,228 -> 56,020 B, structured 99.0 -> 94.2%
  (the trivial envelope arms had inflated the nested-block count).
- **Ghouls_n_Ghosts**: gotos 109 -> 51, frontier 318 -> 28, text -17.2 KB.
- **Krakout**: gotos 18 -> 10, text -6.0 KB; **Wizball**: gotos 44 -> 30,
  frontier 72 -> 35, text -8.7 KB; **Trap**: text -38 B.
- **Commando, Monty_on_the_Run, Automatas, Freeze, Bionic_Commando**:
  byte-identical text (no static-envelope serialization to lose).
- Trace recording fix surfaced by the flip: a dynamic transfer landing
  exactly on its own fallthrough (patched `JMP $+3`, branch displacement 0)
  was never recorded as an observed edge — masked before by proven-set
  serialization, a guard fault under observed sets (9 tunes). Transfers are
  now recorded by lifted control kind.

Certification tally (140 tunes): **20 sites certified** (static set equals
observed; guard provably dead), **135 sites guard-live**, **79/140 tunes
fully certified** (pass `--sound`). Certification is strictly harder than the
old `proven ⊇ observed` rule (126 "proven" sites before), because equality is
required: 113 of the 135 live guards are "static set wider than observed" —
sound static envelopes whose extra arms the tune never plays; the rest are
the tracked precision refusals (mutable-table spill, unresolvable vectors,
unpaired writers; docs/soundness.md taxonomy).

## Run-to-recurrence closure (opt-in, 2026-07-26)

`decompile --close` extends the trace until the play state recurs; recurrence
certifies every guard for the infinite run (docs/soundness.md). Measured over
the same 140 tunes: 111 recur (horizon median 9216 frames, 1.8× the window);
certified sites 20 → 133 of 155, `--sound` tunes 79 → 129/140. The 29
cap-outs have no unbounded counters — incommensurate periodic components
(song loop × free-running modulation counters), plus the 3 osc3 readers.
Default build cost unchanged (closure off by default).

## Gate S status (readability)

Region nesting IS the text's control semantics (`if`/`else`/`loop`/`switch`
with implicit fallthrough; labels only on genuine dynamic landings; parsed
text executes by tree). Proc/ownership pass (`procpass.plan`) and the
evidence-frontier form (`unobserved $XXXX`) as before; `sidprog.metrics`
reports blocks/structured_pct/gotos/labels/frontier/dups/procs. Current
showcase numbers: Commando 2 gotos, Athena 14, Krakout 10, Wizball 30,
Ghouls_n_Ghosts 51 (556-block Follin program).

## Handler-driven tunes (`play == 0`, 2026-07-29)

The v1 manifest above is `play != 0` only. Measured separately over the first
140 cached `MUSICIANS/*/*/*.sid` at a 300-frame window (decompile + Gate FP):
**123/140 -> 132/140**, and **133/140** with the analysis edge model below. All
16 `play == 0` tunes in that sample previously failed as
`control 'brk' at 0000 not modeled` (the header play address *is*
`$0000`) or as an init runaway; 9 now decompile, replay bit-exact from the
model and from parsed text, and pass Gate FP. Every discovered vector in the
sample is CINV `$0314`; the hardware `$FFFE` and NMI `$0318` paths are covered
by the synthetic corpus. The 7 that remain fail honestly: 5 inits never return
(`runaway in init at $XXXX: init never returned`) and 2 BASIC tunes install no
vector at all. The non-returning inits are the next slice, not a ROM problem:
they idle until their own handler fires (`Demolix` spins at `$9F89` on a flag
its IRQ sets; `Cielos_Esfumados` pages both ROMs out, installs `$FFFE`/`$FFFA`
and loops at `$08E4`), so their frame is a main-loop iteration plus interrupts
— the v2/P-INT driver cadence, not one handler call per frame.

## Retired failure classes (history)

- **Static-only analysis edge model** (`Bangkok` `$CA55`, 2026-07-30): the
  forward analyses walked successors from `term_targets` alone, reading a
  static refusal (and every `rts`) as "no successors". A routine reached both
  by `JMP` one call deep and — through a refusing self-modified `JMP` — by
  `JSR` two deep therefore looked single-depth to `sp_flow`, so
  `concretize_stack` folded its `PHA` cells at the wrong depth, overwrote a
  return address, and the walker left the model at frame 3 while the evidence
  trace ran clean. The analyses now walk the relation COMMIT installs
  (`Analysis.succ_targets`: static set ∪ observed set, which also absorbs an
  under-approximating resolution — Attitune `$119A` resolves to `{$1006}` where
  `{$1006, $1021}` was observed), and `sp_flow` follows observed `rts`
  continuations at `sp+2` so an rts-dispatch is an edge too.
  Both holes are pinned hermetically
  (`test_shared_routine_entered_at_two_stack_depths`), and `check_commit` now
  refuses any kept block the edge model cannot reach. Extent, 300-frame
  windows: 301 blocks over 12 of the 140 v1 manifest tunes were unreachable in
  the old edge model (now 0); over all 682 cached tunes the fix takes
  **642 -> 646** with no regressions — 3 walker faults (`Bangkok`, `Archer`,
  `Blades_of_Mystics_preview`) and 1 Gate FP divergence (`Amazon`, frame 98,
  `v0.lww` 31 vs 36) were the same root cause. The class was invisible to
  Gate C twice over: `Bangkok` is outside `_corpus.CORPUS`'s composer
  round-robin, and no CI job fetches `Songlengths.md5`, so every
  corpus-parametrized test is empty in CI by construction.
- **Empty-"proven" commits** (Army_Moves `$E093` and six siblings): a
  resolution pending when closure rounds exhausted was accepted as a proven
  empty set. Retired first by the commit coverage rule, now structurally
  impossible: committed sets are the observed sets.
- **Opcode-cell hard failures** (Athena `$6083`/`$C325` toggles forced to ⊤
  by `(zp,X)` computed stores): retired by the guarded observed set.
- **Text size** (Agent_X_II): bijective naming; all tunes emit smaller than
  the disasm listing.
- **Closure-materialization residue** (Ghouls 36.1%/1,714 gotos era):
  retired by final-closure garbage collection; the flip further removes
  speculative materialization of unobserved envelope members.

Runtime budget: worst full-length single-tune build ~78 s (Trap); the 60 s
single-process target is tracked via decompile-side windowing.
