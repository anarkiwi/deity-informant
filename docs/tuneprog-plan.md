# tuneprog — plan v3: what prototyping taught, the two gates, what to prototype next

Companion to [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md) (the
design), [tuneprog.md](tuneprog.md) (what is built) and the five
`prototype-*.md` records. This document is the reflection after the first
prototyping round (2026-08-16/17): which design claims held, which rules changed,
what the accumulated backlog is, which prototypes are worth doing next before
the codebase grows further, and whether we are using the Ghidra ecosystem and
the existing knowledge about self-modifying code where it pays.

Contents

1. Where we are
2. What the prototypes proved
3. Lessons that change the design or the plan
4. Ghidra, prior SMC work, and the RTS-trick family — are we leveraging them?
5. Backlog (deduplicated from every stage's "what remains")
6. Gate: fold and stack before any new family
7. Gate 2 — stack elimination in S4: the work
7b. Gate 1 — the copy index as an IR value: the work
8. Next prototypes, ranked
9. Keeping the project small
10. Execution: one agent at a time, in this order

---

## 1. Where we are

| | |
|---|---|
| certified | Automatas (defMON, both SID models), Commando songs 1–2 (Hubbard), Ghouls'n'Ghosts (Follin, 32 subtunes + `--songs all`), GoatTracker 2 ×2, SID Wizard ×2 — 42 certificates, 841,891 ticks, 0 divergences, 0 envelope traps; 38 complete via periodicity, the `--songs all` program complete on 31 of its 32 subtunes |
| certify at 15 s, not yet run to length | Blackbird (Quintessence), Galway (Comic Bakery), Walker (Chameleon) |
| refused by design | JCH Easy Does It (NMI sample mixer = second interrupt) |
| code | `deity_informant/tuneprog/`, 40 modules, ≈ 11,600 lines, none over 500; 380 hermetic + 35 HVSC tests, 94 % coverage; `tools/tuneprog_certify.py`, `tools/tuneprog_recert.py` (42/42 reproduce), `tools/tuneprog_ghidra.py` |
| baseline | Ghidra high P-code export with SMC context ([ghidra-highpcode-export.md](ghidra-highpcode-export.md)) and three oracles |
| merged PRs | #225 design · #226 plan · #227 prototype · #228 fold/texture · #229 Follin · #230 GoatTracker · #231 SID Wizard · #232 Ghidra export · #233 consolidation · #234 copy folding |

Ten stages, each an Opus agent with the certificate as its acceptance test:
front end → certified core → presentation → fold/texture → Follin →
GoatTracker ∥ SID Wizard ∥ Ghidra export → consolidation → copy folding. Every
stage merged on green CI; `tools/tuneprog_recert.py` reproduces all 42
certificates on `main`.

## 2. What the prototypes proved

| design claim (§ in the design) | evidence |
|---|---|
| per-tick equivalence is the right observable and is checkable cheaply (§1, §7) | 42 certificates, 841,891 ticks, 0 divergences, 0 envelope traps; a full-song certificate costs seconds to a few minutes in Python |
| dynamic first: executed sites + exact access relation give the code, the storage and the CFG (§2, §5) | every mechanism the nine exemplars document came out of the generic front end without tune-specific code: SMC operand/opcode cells, pointer broadcast, patched `JMP`/`JSR`/branch dispatch, illegal opcodes incl. `NOP #imm` overlap, `(zp,X)`, init calling into play, data-dependent SID addresses, computed store operands, IRQ entries, subtunes, return values, stack frames |
| exact regions with envelope asserts (§5 S3) | envelope traps never fired in any certificate; per-voice fields appear as size-3 arrays at strides 1/7/49 exactly as predicted; the two region weaknesses found (one-loop init merges, table overrun merges) are presentation, not correctness |
| flags/registers vanish under SSA; SMC becomes loads (§5 S4) | printed forms have no `sp`, no flags, no `carry(` except genuine borrows; the certified IR is 1.0–1.6 statements per executed instruction (Automatas 651 → 1,070; GT2 437 → 580; SW 859 → 1,054; Follin 1,177 → 1,242) |
| periodicity upgrades a horizon to completeness (§1, §7) | 38 of 42 certificates are complete (plus 31/32 subtunes of the union program); the exceptions are aperiodic modulation (Follin sub 21: portamento + trill never re-align) and a loop with a free-running counter (Commando: state period = lcm(loop, 256) frames, beyond the horizon) |
| the design's cost model | trace 277 k instructions/s, verify 10–16 k calls/s; the whole HVSC at song length is ≈ 300 CPU-hours — a few hours on this machine, no fast tracer needed yet |
| Ghidra cannot be the core but can be a baseline (§8) | with our facts applied through a SLEIGH context register, Ghidra's decompiler abstracts SMC mechanically (79/87 of Automatas' cell bytes become globals) and its high P-code is 5.8–10.6× our S4 statements — see [ghidra-highpcode-export.md](ghidra-highpcode-export.md) |

Coverage of the anatomy: 8 of the 9 exemplar players are certified or certify at
a short horizon (Blackbird, Galway, Walker pass 15 s unchanged; JCH is refused
by design — its NMI sample mixer is a second interrupt).

## 3. Lessons that change the design or the plan

**L1 — SMC cells are defined by their writers, not by the phase.** The design
said "operand cells written by any traced op in any phase after init"; the first
implementation keyed on play-written cells and folded init-written ones to their
post-init value. Follin's rip loader patches its own `CPY #` between two block
copies and consumes it inside init. Rule now: a cell is any instruction byte a
decompiled procedure writes; constant folding of init-only cells happens only in
procedures init never reaches. (Design §5 S2 wording tightened; SID Wizard's
relocation loop still folds to constants in the tick — the right outcome.)

**L2 — Trace-closure is exact but not enough for presentation.** Copies of one
template that executed different arms are different trace-closed programs, so
Follin's three voices would not fold; and 12–51 `trap 'untaken'` arms per tune
are honest noise. Two closures earn their place: *closure by siblings* (lift the
arms one copy ran into its isomorphic copies, marked unverified) and the
bounded static walk the design already named. Both add unverified statements
that the certificate must count separately; both leave verified behaviour
untouched. *Outcome (PR #234).* Both closures exist: `siblings.py` recovers k copies of one template from the post-init image (opcode-stream alignment with resync over an insertion, a chain check, extension through the dispatch by order-preserving arm matching); `closure.py` lifts the arms one copy executed into its siblings under their own operands, and the same front end builds a second, *closed* program that verifies against the same trace with 0 divergences (`--closure siblings|none`, default on). `copyfold.py` proves the fold (equal alpha-renamed token streams, one per-copy address mapping, affine constants). Follin song 1's `tick()` is now one `for v in 0, 1, 2:` with the 21-arm command switch inside (1,421 → 669 printed lines); Automatas' cascades fold twice (`for v in 0, 1: for w in 0, 1, 2`, 717 → 637). **What it does not do**, stated as its boundary: (a) Follin's 19 sound-effect subtunes do not fold — an effect uses one or two voices and the silent voice never reaches its dispatch, so its arms have nothing to pair with and the other voices' handler bodies become a cross-copy edge, which the fold refuses (songs 1–11, 16, 20 fold); (b) the `--songs all` union does not fold — one voice's stream read is access class `chk` (it reaches bytes outside the written set over 32 subtunes) while the others are `ram`; (c) three of the 21 command arms stay `trap 'unverified'` because no voice ever sent them, and the two per-copy entries past the table pair with nothing; (d) **44 of Follin song 1's 283 printed statements are unverified** — closure arms lifted from a sibling voice that ran them; the printer reports this only as a header count, not per statement; (e) Automatas' three row-advance blocks (three unrelated table regions) do not fold; (f) discovery depends on the horizon (Automatas' cascades fold fully only over the whole song). So the printed program is no longer purely trace-closed; it carries code verified for a *different* voice, guarded by the same branches that were traps before.

**L3 — Regions need per-phase views.** One-loop init clears (`STA $21,X` over
`$21–$97`, GT2's blocks A+B, SID Wizard's 105-byte VARIABLES) merge every field
into one region; the play-time accessors carry the strides. The region model
stays exact; the *view* is built from play-phase accessors. *Outcome (PR #234).* `views.py` splits a region one init loop made into the records the play-time stride names, per access, and names per-copy address tables once in the state header (`voice[v].pulse_mode` over `$62EE/$64DB/$66CA`); GoatTracker's blocks A+B and SID Wizard's VARIABLES print as records (`rec[x/7 + 3].timer_2`), with fields named only where a role reaches them (`f00`-style otherwise: one named field on GT2, five on SW).

**L4 — Presentation wants an algebra, not a pile of roles.** Five stages added
stride views, origins, gcd scales, group views, image-copy roles, tails,
frames, 16-bit views, tokens; the consolidation pass merged 69 duplicated
helpers. The stable core is: views over regions (stride, group table, origin,
element width, per-phase), a naming plane (role → name, family dictionary →
name), and structural passes proven by alpha-equivalence. New mechanisms should
be expressed in that vocabulary or not added.

**L5 — Copy identity in the wild is structural, not byte-level.** Within a
family ~5 % of tunes share an executed opcode sequence, but 6-gram similarity is
0.2–0.7 (survey §9.6). Family knowledge therefore enters only through
*alignment* (our own n-gram/structure alignment or Ghidra Version Tracking),
never through reuse of a decompilation.

**L6 — Certificates want a periodicity proof, not just a witness.** Hashing
found periods for looping songs; Commando's free-running frame counter pushes
the state period to lcm(loop, 256), which a hash cannot reach at song length.
A structural argument (the counter is read only as `& 7`/`& 1`, or the loop
length divides the counter period) would certify it complete. Small item, high
value for the campaign's completeness rate.

**L7 — The tick model is right; the inputs are the residual risk.** Ghidra's
emulator agreed byte-for-byte with our trace on Automatas and Commando and
disagreed from call 2 on GoatTracker and Follin; the open explanation is
per-call inputs the facts export does not carry. Every disagreement is
localised (call, pc, register); it is a bounded investigation, not a design
hole.

**L8 — Process.** One agent per stage with the certificate as the acceptance
oracle worked; parallel agents in worktrees worked (PYTHONPATH pinning); a
consolidation pass every three or four stages is necessary — the presentation
layer doubled its helper count in two stages before it. Every agent's "what
remains" list is the real backlog (§5).

## 4. Ghidra, prior SMC work, and the RTS-trick family

**What we use.** The 6510 SLEIGH module (lifter ↔ SLEIGH agreement tested through
pypcode); the SMC-context export that makes Ghidra's decompiler a trace-informed
baseline; three Ghidra-side oracles (differential complexity, static coverage
against executed sites, P-code emulator semantics); Ghidra's headless Docker in CI.

**What we deliberately do not use.** Ghidra's decompiler as the pipeline core:
it is bound to the program database, cannot express opcode-cell variants
without overlays, trace closure, the per-call schedule, inputs, periodicity or
the certificate, and its high P-code is 6–10× larger than the trace-exact
program on the same facts. The maintainers say the same thing about dynamic
data ("we allow the user to manually capture dynamic data back into the
Program database") — which is exactly what the facts export does.

**How our approach sits against prior SMC work.** Overlay address spaces
(GhidraNes/GhidraBoy, c64_ghidra) and emulation-driven write-back
(GhidraEmulatorUI, c64_ghidra's `c64PcodeEmulation.py`, `EmuX86Deobfuscate…`)
materialise *one* byte-state per address; CoDisasm's *waves* re-disassemble
after every self-modification; Anckaert's state-enhanced CFG conditions edges
on the target's byte state; Cai/Shao/Vaynberg reason about code as mutable data.
Ours never patches a byte and never splits into waves: an operand cell is a
variable, an opcode cell is a `switch` on its own byte (Anckaert's edge
condition, but on one program), and the trace supplies the domain. The RTS
trick, `JMP (ind)`, patched `JMP`/`JSR`/branch operands and jump tables all
collapse to one mechanism — a switch over observed targets, closed statically
from the table when the writers copy from one — which is also what Ghidra's
`JumpTable.writeOverride` consumes when we hand it the targets.

**What is still worth taking from the ecosystem** (in §6): Version Tracking or
BSim to align a tune's procedures/regions to a symbol-bearing reference build
of its family (GT2 `player.s`, SW `player.asm`, undefmon) for names; the
processor `PCodeTest` framework to validate the SMC constructors themselves;
structure data types in the export once region bases outside code are
distinguished; overlays for the non-`RTS` opcode cells if anyone needs Ghidra's
C for those (2 % of tunes).

## 5. Backlog

Deduplicated from every stage's report; owner = the module that would change.

| item | source | kind | owner |
|---|---|---|---|
| bounded static closure of untaken branch directions (marks unverified; removes most `trap 'untaken'`) | design §3, every prototype | correctness-neutral, presentation | cfg/build |
| fold the sound-effect subtunes: apply the sibling closure one level up (a silent copy adopts the dispatch its siblings ran, all arms unverified for it) — or accept as the boundary | copy fold (#234) | presentation | siblings, closure |
| mark unverified closure statements per statement in the printed text (today: a header count only) | copy fold (#234) | presentation, honesty | printer, pseudocode |
| fold the `--songs all` union (one voice's `chk` stream read vs `ram`) | copy fold (#234) | presentation | closure, build |
| Automatas' row-advance blocks (three unrelated table regions) | copy fold (#234) | presentation | unroll, views |
| periodicity proof for free-running counters (lcm argument) | Commando | certificate | verify |
| per-call input capture in the Ghidra facts; resolve the two emulator disagreements | Ghidra export | oracle | ghidra_facts, headless |
| opcode cells whose alternative is not `RTS` in the SLEIGH export (overlay or paired constructor) | Ghidra export | baseline | ghidra/6510 |
| family name dictionaries by structural alignment | all | naming | recover |
| second interrupt schedule (NMI + IRQ sharing regions) | design §10, JCH | scope | machine/trace/verify |
| 16-bit views for halves stored by unrelated instructions (Follin freq shadow, pulse width) | Follin, SW | presentation | word |
| sign-extension/flag-algebra printing (`sext(table[i])`, `if (tempo & $80)`) | SW | presentation | pseudocode |
| interprocedural index range for jump-table extents (Follin 23 vs 21 arms) | SW | closure | jumptab |
| `--songs all` resume state for mixed stop reasons | Follin | tool | pipeline |
| `printer`/`pseudocode` memo invalidation with 16-bit views; `node_exprs` unknown-node guard | consolidation | quality | irwalk, pseudocode |
| Ghidra function bodies vs clone-per-entry (`ghidra_partial` rows) | Ghidra export | oracle | ghidra_compare |
| numba tracer/executor if the campaign needs it | design §11 | performance | trace, emit |
| ~~stack elimination in S4 (§7) — gate item~~ *done (#237)* | user gate | core | frames, stack |
| an `RTI` entry tune is residual: the status byte the machine pushed at the interrupt is a frame the tick never wrote, so its stack stays (model the entry frame as the tick's contract instead) | stack elimination (#237) | core | build, verify |
| a residual stack is whole-program: one unplaceable read keeps `SP` in every procedure, where an interprocedural frame layout would localise it | stack elimination (#237) | core, precision | frames, stack |

## 6. Gate: fold and stack before any new family

Two things must be true before another tune family is admitted, both because
they are about the *core* matching the design rather than about breadth:

1. **The copy index is an IR value and the folded program certifies.** k sibling
   copies of one template (Follin's voices, defMON's cascades) are one procedure
   body under `for v in 0..k-1` *in the certified S4 program*, every per-copy
   operand an address through a per-copy table `T[v]`, coverage recorded per
   `(template site, v)`, and the certificate reproduces on the folded program.
   *Review of #234 (2026-08-18):* the fold was placed at the end of a lossy
   pipeline and asked to prove a syntactic identity the pipeline had destroyed
   (trace closure drops arms, S4 merges block starts, SSA renames); the exact
   correspondence (`Copies.addrmap`) was reconstructed through ten thresholds
   (`GRAM/MINROWS/MINARM/MAXCOPIES/LOOK/CONFIRM/LIMIT/NEAR/MINSLOTS/MINSTMT`),
   two tokenisers (`unroll`, `copyfold`) and a second program build; the closed
   program's "0 divergences" is tautological (lifted arms have count 0); every
   listed boundary (SFX subtunes, the union's `chk`/`ram` class, row-advance
   blocks, horizon-dependent discovery) is one cause. The gate is therefore
   **not met** and is re-specified in §7b; #234's mechanism is the prototype
   that found the requirements.
2. **The certified executable is stack-free** — the S4 program has no `SP`
   value, no return-address pushes and no stack-page stores for balanced
   `PHA/PLA/PHP/PLP`; only a genuinely unbalanced push (RTS trick, stack scratch
   read by another frame) may keep an explicit residual stack, and the
   certificate says so. §7 is the specification. *Status after #237:* **met**.
   `frames.py` proves the slots (a symbolic `SP` offset per value with a constant
   required at every join, must-def reaching over the slots, groups by union-find)
   and `stack.py` rewrites what that proves; all 42 certificates reproduce with
   `"stack": "eliminated"`, and every `tuneprog.py` has 0 occurrences of `SP` and
   no stack-page access left. S4 statements fall 0.3–11 % (Automatas 1,070 → 995,
   Follin song 1 1,242 → 1,229, the union 1,567 → 1,553, GT2 580 → 526, SW 1,054 →
   951 and 1,050 → 935); ticks, period, `complete` and divergence are unchanged
   except that `gt2-do-it-again`'s state repeat, which its stack scratch had been
   delaying, is now found at 8,658 instead of 9,955 — same period, still complete,
   a shorter horizon. Printed forms differ only in the header statement count,
   `saved` numbering, a copy the fold now removes, and the spare register a
   promoted tail's argument takes. The three uncertified exemplars are stack-free
   too (Blackbird, Galway, Walker at 10 s of music). What stays residual, by proof
   and not by guess: a stack scratch *area* whose pointer is not a constant
   offset, a `TSX`-relative read of another frame, an `RTI` entry frame's status
   byte, and the pointer read as data — and then the whole program keeps its
   stack, since such a read can see any byte of the page.

Until both hold, the queue in §8 waits; the only allowed work is on these two
items and on measurement (the campaign driver may be *written* but not used to
admit families). §10 is the execution order.

## 7. Gate 2 — stack elimination in S4: the work

*Done in #237; §6 item 2 records what it measured. One premise below was wrong:
a `PHA` is an ordinary store, so the tracer did log its address in the play
footprint and the state hash did cover the stack page. The page is now outside
the footprint on both sides — machine texture, like the JSR frames the `raw`
class already keeps out of the write log — which is what lets the eliminated
program hash the same state as the trace.*

**Where things stand.** `build.py`/`ir.py` model calls with explicit frame
pushes (`Store(raw, 256|SP, ret)`), `SP` is register index 3 in every
procedure's params/rets, and `PHA/PLA/PHP/PLP` are stores/loads on the stack page
with `SP ± 1`. The presentation already proves the frames away — `frame.py`
computes each procedure's stack offsets relative to its entry `SP`, pairs pushes
with the pops that read them, and hides them in the view; `texture.stack_temps`
forwards single push/pop pairs — but the executable and the S4 JSON still carry
the machine stack. The certificate does not observe the stack page (it is not in
the write list or the state hash), so eliminating it cannot change a certificate;
it can only break one, which is what the tests below are for.

**Work items** (one agent, one branch, ~300–400 lines + tests):

1. `frames.py` (new; move the analysis out of `frame.py`, which then only
   names): per procedure, a symbolic `SP` offset per block relative to entry,
   required constant at every join (a procedure where offsets disagree at a join
   is *residual*, see 4); classify every stack access:
   - **return-address pushes at call sites** → removed (the `Call` already
     carries the continuation; the callee's `Return` already returns);
   - **balanced push/pop within the procedure** (`PHA/PLA`, `PHP/PLP`, `TXA/PHA … PLA/TAX` shapes) → the push becomes a `Let` of a slot value, the pop a use of it (SSA); `PHP/PLP` pack/unpack through the existing bit-wise flag idioms; two pushes one pop can read become a phi;
   - **RTS-trick continuations** (`PHA PHA RTS` and `JMP (ind)` after pushes): the trace already made the unmatched `RTS` a `switch(stack word)`; forward the pushed values into the selector so the switch reads `hi:lo` values and no stack remains;
   - **entry into the middle of another procedure through a patched `RTS`**
     (Blackbird's init `JSR $1009` with `$12EB` patched to `RTS`): a matched
     JSR/RTS pair per the trace → an ordinary call after elimination.
2. **Residual stack**: a push read by another frame (stack scratch, `TSX`-relative
   reads, unbalanced pushes across calls) keeps an explicit `stack` region of the
   proven depth and an `SP` value in the procedures that touch it — nowhere
   else. The certificate records `"stack": "eliminated"` or
   `"stack": {"residual_depth": n, "procs": [...]}`.
3. `emit.py`/`interp.py`: drop `SP` from params/rets when eliminated; the
   `raw` access class stays only for the residual case; `ir.py` gains nothing
   new (slot values are `Let`s).
4. Tests: differential (interpreter before/after) on assembled snippets —
   balanced `PHA/PLA`, `PHP/PLP` across a branch, push in a loop, push across a
   call, RTS trick, `TSX` scratch (must be residual), a foreign-frame read
   (residual), JSR depth 3 with `PLA` after return; hermetic. Acceptance on the
   certified exemplars: `grep -c "SP" tuneprog.py == 0` and no `m[256|…]`
   stores for Automatas, Commando, GoatTracker ×2, SID Wizard ×2, Follin song 1
   (`--songs all` too), with `tools/tuneprog_recert.py` 42/42 reproduced (the
   `tuneprog.py` bytes change; ticks/period/complete/divergence must not); the
   printed forms unchanged except where the view had been carrying a `saved`
   temporary that is now a plain value.
5. Docs: `tuneprog.md` (module map, certificate field), the design's S4 wording
   ("registers, flags and the stack do not exist in the IR" becomes true for the
   executable), this plan (§6 gate closed).

## 7b. Gate 1 — the copy index as an IR value: the work

**Principle.** Copy identity is a property of the post-init image and the
access relation, not of the decompiled output. The correspondence is computed
exactly once, and copy *j* executing site *s* is the template site executed with
`v = j`. Nothing is "lifted"; coverage is a vector over `v`.

**Stage A — exact correspondence (`siblings.py` only).**
- Bases come from the CFG: a chain is a run of blocks where copy *j*'s exit edge
  enters copy *j+1*'s entry; the parallel dispatch tables are found through
  their writers (`jumptab`).
- Each pair of copies is aligned by a gapped sequence alignment of the
  `(opcode, mode)` streams (`difflib.SequenceMatcher`, one algorithm, no resync
  window); rows are the matched instructions.
- A family is accepted iff the per-copy operand map (`Copies.addrmap`) is
  consistent over every row — an ambiguity is a refusal, never a deletion — and
  every copy is chained. Threshold constants are removed; anything that survives
  must be a proof property (a chain edge exists, a map is a function).
- Tests are property tests: k copies of a random template under a random
  operand map with random per-copy arm coverage → one family with that map;
  a random unrelated stream sharing a prefix → none. Follin s1, Automatas (30 s
  and full song) find the same families as #234; GT2/SW find none.

**Stage B — copy index in the front end.**
- `cfg.py`: a *merge* pass after procedures are built and before `build_ir`:
  the k copies' sites collapse onto the template's under `Copies.pcmap`; the
  chain edge from copy *j* to *j+1* becomes `v += 1; if v < k: entry`, the
  entry is preceded by `v = 0`; per-site execution counts become a vector over
  `v` in the trace summary (`tracedata`).
- `build.py`: an operand that differs across copies is an address
  `T_x[v]` where `T_x` is a synthetic per-copy table region (`kind:
  "copymap"`, k entries); an affine map is the same table and prints as a
  stride later. Patched-`JMP` dispatch is keyed by the table *index* the
  writers read (`jumptab`), so the k parallel tables are one `T[v][cmd]` and
  arms pair by equal case value.
- `regions.py`: the region of a `T_x[v]` access is the union over `v` (Follin's
  `$62EE/$64DB/$66CA` become one 3-element region — the group view is then a
  region, not a naming pass).
- Verification is unchanged: `v` is an ordinary `Let`; the certificate gains
  `"copies": {families, per-statement coverage}` and `unverified` = statements
  whose coverage vector has a zero — per statement, printed as such.
- Deleted: `closure.py`, the second-program build in `pipeline.closed`, the
  `--closure` flag. `copyfold`/`unroll` are no longer needed for chained copies
  (the S4 program is already folded); they are consolidated in stage C.
- Acceptance: Follin song 1 folds *in S4* and certifies with 0 divergences;
  SFX subtunes and `--songs all` fold (the silent voice is a `v` with zero
  counts); `tools/tuneprog_recert.py` 42/42 (bytes change; ticks/period/
  complete/divergence do not); every module ≤ 500 lines.

**Stage C — consolidation.** One view pass over the table representation
replaces `copyfold.py` + `unroll.py`; `views.py` names `T_x` fields; docs
(`tuneprog.md`, the prototype records, this plan: gate closed).

## 8. Next prototypes, ranked

The question is which few prototypes buy the most information before the
project grows. Ordered by information per effort:

1. **Campaign at survey scale.** Run the pipeline over the stratified 7,023-tune
   sample (30 s horizon, then `--until-period` for the ones that pass) with a
   parallel driver like `tools/survey/run.py`: certification rate by family,
   failure classes with first-divergence sites, refusal reasons, cost. This
   turns nine exemplars into a distribution and picks the next mechanisms by
   frequency; it also decides whether a fast tracer is needed. Cost: a day of
   tooling, hours of compute. Risk: none — it only measures.
2. **Static closure + unverified accounting** (L2): the bounded walk from
   untaken directions and unobserved switch arms, `verified`/`unverified`
   statement counts in the certificate, and the printer marking unverified
   arms; measure trap reduction on the six certified tunes. Cost: small; the
   sibling closure already exists.
3. **The remaining exemplars**: complete certificates for Blackbird, Galway and
   Walker (they pass at 15 s — cheap 8/9), then a **JCH second-interrupt
   prototype**: a two-entry schedule (IRQ play + NMI mixer) with the NMI's
   `$D418` writes and shared cells, certified at call granularity. It is the
   last design gap and 2 % of HVSC. Cost: medium; risk: the concurrency model
   is new.
4. **Family naming by alignment**: assemble a symbol-bearing reference build
   (GT2 `player.s` with the tune's flags; SW `player.asm`; undefmon), align to
   the tune's procedures/regions by opcode-sequence and structure (or Ghidra
   Version Tracking through the export), and measure naming coverage on GT2 and
   SW. Cost: medium; payoff: readability for ~15 % of HVSC.
5. **Periodicity proof for counters** (L6) — small, raises the complete rate.
6. **Ghidra oracles in the nightly recert** — the complexity/coverage/emulate
   sub-modes on the certified set, so a regression in ours shows as an
   `ours_bigger` flag; resolve the two emulator disagreements first.
7. **Fast tracer** — only if (1) shows tracing dominates the campaign wall time.

Deliberately not now: 2SID/3SID (0.6 %), ROM-dependent tunes, audio rendering,
BASIC programs.

## 9. Keeping the project small

- Every module ≲ 500 lines; a mechanism that needs a new module must name the
  view/pass vocabulary of §3 L4 it belongs to.
- No new role/view heuristic without (a) a hermetic snippet test and (b) at
  least two families that need it, or one family plus a survey count.
- A consolidation pass after every three stages; `tools/tuneprog_recert.py`
  green before and after.
- The certificate stays the only acceptance test; presentation changes must
  leave `tuneprog.py` byte-identical or explain the change.
- Prototype docs are records; the living documents are the design, this plan
  and `tuneprog.md`.

## 10. Execution: one agent at a time, in this order

One Opus agent per stage in its own worktree (`PYTHONPATH` pinned), the
certificate as acceptance, a read-only reviewer between stage and merge. Order:

| # | stage | owns | acceptance |
|---|---|---|---|
| 1 | ~~gate 2 — stack (§7)~~ *done (#237)* | `frames.py`, `stack.py`, `frame.py`, `interp.py`, `trace.py`, `emit.py`, `pipeline.py`, tests | met: `SP` absent from all 42 exemplar `tuneprog.py`, 42/42 recert, §6 item 2 for the measurements |
| 2 | gate 1 stage A (§7b) | `siblings.py`, `tests/tuneprog/test_siblings.py` | thresholds gone; property tests; same families on Follin/Automatas, none on GT2/SW |
| 3 | gate 1 stage B (§7b) | `cfg.py`, `build.py`, `regions.py`, `tracedata.py`, `jumptab.py`, `pipeline.py`, `verify.py`; delete `closure.py` | Follin s1 folds in S4, 0 divergences; per-statement coverage in the certificate; SFX and union fold; 42/42 recert |
| 4 | gate 1 stage C (§7b) | `copyfold.py`+`unroll.py`→one pass, `views.py`, docs | recert green; one tokeniser; plan v3 gates closed |
| — | reviewer, before each merge | read-only | refutes: new tunable constants, duplicated mechanisms, tests that encode an exemplar rather than an invariant, module > 500 lines |

Every brief carries the global directives (no tuning constants — a threshold is
a proof property or is removed; black/pylint/xdist; coverage > 85 %; 60 s CPU
per script, use `--budget`/`--resume`), commits to a branch, opens a PR, watches
CI, merges on green, and ends with a "what remains" list that becomes this
plan's backlog. The campaign (§8 #1) runs after stage 4.
