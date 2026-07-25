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

## Gate S status (readability)

Region nesting IS the text's control semantics (`if`/`else`/`loop`/`switch`
with implicit fallthrough; labels only on genuine dynamic landings; parsed
text executes by tree). Proc/ownership pass (`procpass.plan`) and the
evidence-frontier form (`unobserved $XXXX`) as before; `sidprog.metrics`
reports blocks/structured_pct/gotos/labels/frontier/dups/procs. Current
showcase numbers: Commando 2 gotos, Athena 14, Krakout 10, Wizball 30,
Ghouls_n_Ghosts 51 (556-block Follin program).

## Retired failure classes (history)

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
