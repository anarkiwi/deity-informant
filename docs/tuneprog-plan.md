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
| certified | Automatas (defMON, both SID models), Commando songs 1–2 (Hubbard), Ghouls'n'Ghosts (Follin, 32 subtunes + `--songs all`), GoatTracker 2 ×2, SID Wizard ×2, JCH NewPlayer V20 ×2 — 44 certificates, 744,583 certified ticks, 0 divergences, 0 envelope traps; 40 complete via periodicity, the `--songs all` program complete on 31 of its 32 subtunes |
| certify at 15 s, not yet run to length | Blackbird (Quintessence), Galway (Comic Bakery), Walker (Chameleon) |
| refused by design | JCH Easy Does It (NMI sample mixer = second interrupt); the plain V20 engine inside it is certified twice ([prototype-jch.md](prototype-jch.md)) |
| code | `deity_informant/tuneprog/`, 46 modules, 13,781 lines, none over 500; 518 hermetic + 49 HVSC + 4 oracle tests, 96 % coverage; `tools/tuneprog_certify.py`, `tools/tuneprog_recert.py` (44/44 reproduce), `tools/tuneprog_period.py`, `tools/tuneprog_ghidra.py` |
| baseline | Ghidra high P-code export with SMC context ([ghidra-highpcode-export.md](ghidra-highpcode-export.md)) and three oracles |
| merged PRs | #225 design · #226 plan · #227 prototype · #228 fold/texture · #229 Follin · #230 GoatTracker · #231 SID Wizard · #232 Ghidra export · #233 consolidation · #234 copy folding · #235 plan v2 · #237 stack · #239 stack footprint · #241 sibling correspondence · #242/#243 the copy index · #244 the copy view · #248 fold reach · #249 plan (the P-JCH row) · #250 certificate accounting · #251 JCH NewPlayer V20 · #252 the Q-packages · Q1a structuring |

Fourteen stages, each an Opus agent with the certificate as its acceptance test:
front end → certified core → presentation → fold/texture → Follin →
GoatTracker ∥ SID Wizard ∥ Ghidra export → consolidation → copy folding →
stack elimination → the copy index (correspondence, front end, view). Every
stage merged on green CI; `tools/tuneprog_recert.py` reproduces all 42
certificates on `main`. **Both gates of §6 are met.**

## 2. What the prototypes proved

| design claim (§ in the design) | evidence |
|---|---|
| per-tick equivalence is the right observable and is checkable cheaply (§1, §7) | 42 certificates, 841,891 ticks, 0 divergences, 0 envelope traps; a full-song certificate costs seconds to a few minutes in Python |
| dynamic first: executed sites + exact access relation give the code, the storage and the CFG (§2, §5) | every mechanism the nine exemplars document came out of the generic front end without tune-specific code: SMC operand/opcode cells, pointer broadcast, patched `JMP`/`JSR`/branch dispatch, illegal opcodes incl. `NOP #imm` overlap, `(zp,X)`, init calling into play, data-dependent SID addresses, computed store operands, IRQ entries, subtunes, return values, stack frames |
| exact regions with envelope asserts (§5 S3) | envelope traps never fired in any certificate; per-voice fields appear as size-3 arrays at strides 1/7/49 exactly as predicted; the two region weaknesses found (one-loop init merges, table overrun merges) are presentation, not correctness |
| flags/registers vanish under SSA; SMC becomes loads (§5 S4) | printed forms have no `sp`, no flags, no `carry(` except genuine borrows; the certified IR is 1.0–1.6 statements per executed instruction (Automatas 651 → 1,070; GT2 437 → 580; SW 859 → 1,054; Follin 1,177 → 1,242) |
| periodicity upgrades a horizon to completeness (§1, §7) | 38 of 42 certificates are complete (plus 31/32 subtunes of the union program); the four exceptions are all aperiodic modulation, measured (P2, `tools/tuneprog_period.py`): Follin sub 21's portamento + trill never re-align, and both Commando subtunes drift a per-voice pulse-width accumulator whose full byte is the SID write — 41,898 of 48,192 tick write lists differ at song 1's 11,808-tick pattern loop |
| the design's cost model | trace 277 k instructions/s, verify 10–16 k calls/s; the whole HVSC at song length is ≈ 300 CPU-hours — a few hours on this machine, no fast tracer needed yet |
| Ghidra cannot be the core but can be a baseline (§8) | with our facts applied through a SLEIGH context register, Ghidra's decompiler abstracts SMC mechanically (79/87 of Automatas' cell bytes become globals) and its high P-code is 5.8–10.6× our S4 statements — see [ghidra-highpcode-export.md](ghidra-highpcode-export.md) |

Coverage of the anatomy: **all 9** exemplar players are certified or certify at a
short horizon (Blackbird, Galway, Walker pass 15 s unchanged). JCH's engine is
certified on two plain V20 builds; only the *sample* build of §3.5 stays refused,
its NMI mixer being a second interrupt.

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
untouched. *Outcome (PR #234).* Both closures exist: `siblings.py` recovers k copies of one template from the post-init image (opcode-stream alignment with resync over an insertion, a chain check, extension through the dispatch by order-preserving arm matching); `closure.py` lifts the arms one copy executed into its siblings under their own operands, and the same front end builds a second, *closed* program that verifies against the same trace with 0 divergences (`--closure siblings|none`, default on). `copyfold.py` proves the fold (equal alpha-renamed token streams, one per-copy address mapping, affine constants). Follin song 1's `tick()` is now one `for v in 0, 1, 2:` with the 21-arm command switch inside (1,421 → 669 printed lines); Automatas' cascades fold twice (`for v in 0, 1: for w in 0, 1, 2`, 717 → 637). **What it does not do**, stated as its boundary: (a) Follin's 19 sound-effect subtunes do not fold — an effect uses one or two voices and the silent voice never reaches its dispatch, so its arms have nothing to pair with and the other voices' handler bodies become a cross-copy edge, which the fold refuses (songs 1–11, 16, 20 fold); (b) the `--songs all` union does not fold — one voice's stream read is access class `chk` (it reaches bytes outside the written set over 32 subtunes) while the others are `ram`; (c) three of the 21 command arms stay `trap 'unverified'` because no voice ever sent them, and the two per-copy entries past the table pair with nothing; (d) **44 of Follin song 1's 283 printed statements are unverified** — closure arms lifted from a sibling voice that ran them; the printer reports this only as a header count, not per statement; (e) Automatas' three row-advance blocks (three unrelated table regions) do not fold; (f) discovery depends on the horizon (Automatas' cascades fold fully only over the whole song). So the printed program is no longer purely trace-closed; it carries code verified for a *different* voice, guarded by the same branches that were traps before. *Superseded by #241–#244* (§6 item 1, §7b): the correspondence is now exact and spent in the front end, `closure.py` and `copyfold.py` are gone, (a)–(d) are answered and (e)–(f) stand as backlog.

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
be expressed in that vocabulary or not added. *Confirmed by #244:* the copy view
adds no naming rule — an affine column prints through the stride vocabulary that
already existed and any other through the group-slot plane, and a column neither
describes keeps its address visible.

**L5 — Copy identity in the wild is structural, not byte-level.** Within a
family ~5 % of tunes share an executed opcode sequence, but 6-gram similarity is
0.2–0.7 (survey §9.6). Family knowledge therefore enters only through
*alignment* (our own n-gram/structure alignment or Ghidra Version Tracking),
never through reuse of a decompilation.

**L6 — Certificates want a periodicity proof, not just a witness — but measure
the obstruction before proving anything.** Hashing found periods for looping
songs, and the diagnosis of the four that stayed open was wrong. Measured in P2
with `tools/tuneprog_period.py` (per-cell smallest period over 60,000 ticks, the
SID stream's own period, per-loop drift): Commando song 1's patterns do loop, at
11,808 ticks, and its frame counter `$5525` is real — period 256, `+32` a loop,
read only as `& 1` and `& 7`, so a masked-residue hash would dispose of it. It
is not what blocks the repeat. Three per-voice pulse-width accumulators
(`pw += rate` a tick) come back to a *different* value each loop, their full
byte is the `$D402`/`$D409`/`$D410` write, and 41,898 of 48,192 tick write lists
differ at the loop; song 2 is the same shape and its counter's period already
divides its loop. A value that *is* an observable can never be reduced away, so
both subtunes sit where Follin sub 21 sits: aperiodic, not certifiable by any
sound argument at a practical horizon. The lcm/mask proof is therefore
unbuilt — no exemplar exercises it, and an unexercised reduction in the
certificate path is exactly the shape of a false `complete`. What ships instead
is the classifier that decides the question (`period.py`, verdicts `periodic` /
`state only` / `aperiodic`); the campaign's population data re-ranks the proof.

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
| ~~bounded static closure of untaken branch directions~~ *done (P2): `closure.py` walks each untaken direction as far as the post-init image states it and joins the instructions to the trace as zero-coverage sites, so the same front end builds them; it stops at a self-modified byte, an access the stack could see, `JMP (ind)`, a `JSR` no traced procedure answers, `BRK`/`JAM` or the edge of the image, and those paths keep their trap. Every closed statement is marked per statement and counted in the certificate's `closure` block. Measured at 30 s: `trap 'untaken'` 18/15/28/49 → 5/0/3/1 on Automatas/GT2/GNG/SW, 17-60 arms closed apiece, verified statements +4.6-16.7 %, 0 divergences, `stack: eliminated` unmoved, families ≥ before. Off by default (`--closure static`): it costs the covered program its structuring (row below), so the certified product stays trace-closed, which is the second product design §3 always named* | design §3, every prototype | correctness-neutral, presentation | cfg/build, closure |
| ~~fold the sound-effect subtunes~~ *done (#242): a silent voice is a zero in the coverage vector; 24 of Follin's 32 subtunes fold at least one family and 8 refuse on a cross-copy edge (below)* | copy fold (#234) | presentation | siblings, copymerge |
| ~~mark unverified statements per statement in the printed text~~ *done (#242)* | copy fold (#234) | presentation, honesty | printer, pseudocode |
| ~~fold the `--songs all` union~~ *done (#242): the union over `v` of a folded access is one region, so the class question the copies differed on goes away* | copy fold (#234) | presentation | copymerge, build |
| ~~*Automatas*' row-advance blocks~~ *done (P1), not by `unroll` but by the copy index: a chain does join them, and what refused it was ownership of the two instructions before copy 1's first row. One 45-row body over 22 columns; the columns whose copies name unrelated regions keep their table read* | copy fold (#234), stage C (#244) | presentation | unroll, views |
| ~~an edge that leaves one copy for another anywhere but the chain edge refuses the family~~ *done (P1): the rule was already the right one -- an edge to `bases[j+1]` is the advance -- and what refused was ownership. A copy holds only what its rows hold, from its first row on; the stream the alignment stepped over before it (copy j's own tail, then a preamble copy j+1 alone has) is the image of no row and belongs to nobody. Follin's 8 one-voice effects and* Automatas' `$112A` *fold and verify;* `$16AB` *refuses still: its skip enters the next copy at that copy's fourth row, and only a copy's own entry advances the run* | copy index (#242), stage C (#244) | fold reach | copyrows |
| ~~a merged family's patched dispatch loses `jumptab`'s static table closure~~ *done (P1): a column is read-only, so copy j's writer is its expression with each column read replaced by that copy's entry, and the same enumeration runs per copy (Follin song 1 at 30 s: 3 arms → 39, three dispatches of 21 each)* | copy index (#242) | closure | jumptab, copymerge |
| ~~a merged loop prints as `while` over an explicit index and its columns as `copies_XXXX[...]`~~ *done (#244): `copyview.py` prints every column as the operand it stands for and `loops.copies` makes the loop a `for v in 0..k-1`; a column whose copies name different offsets of a record, or whose readers name more than one region, keeps its table read* | copy index (#242) | presentation | views, structure |
| ~~`unroll`'s view-level fold substitutes copy 0's constants into the loop it makes~~ *done (P1): a constant that does not step keeps its literal already; where it equals a cell the run relocates, nothing inside the loop tells the two apart and the run does not fold* | stage C (#244) | presentation, honesty | unroll, views |
| ~~a merged family whose copies have preambles of their own has k entries, so `loops.copies` makes no `for`~~ *done (Q1a): the k prologues **are** the step. Where no latch steps the index by a recurrence, `loops._chain` proves the chain by assignment instead -- every entry from outside the loop names copy 0, the back edges name 1..k-1 once each, the header ran the cover's total and each back edge its own copy's share -- and the loop prints `for v in 0..k-1` with the index hidden. Measured on* Automatas *(whole song and 30 s): the row-advance body is one `for v in 0, 1, 2:` over three `switch v` chain edges, `t1` gone from 40 lines, printed text 3 lines shorter. The Follin half of this row was **refuted by measurement**: a one-voice subtune folds copy 0 against a copy nothing ran (`cover = [111, 0]`), so there is no chain edge and no copy loop at all -- the `while True` over `cv0 = 0` is the player's own command loop, and a `for v` there would claim an iteration the trace never took. The 6 `goto` into the prologues stay: a prologue holds the per-copy preamble, and promoting it (below) would hand the copy index back from a procedure* | P1 (#248) | presentation | loops, structure |
| folding a copy nothing ran costs more than it saves: Follin's 17-19, 25, 27, 29 grow ~6 % of statements and ~20 % of blocks, since the columns and the `switch (v)` are new and there was no second body to remove. What it buys is names for the per-voice cells and a coverage vector that says the silent voice's code is this code | P1 (#248) | presentation | copymerge |
| ~~a merged access unites its regions, so a role one copy's access carried can move with them~~ *done (Q1b), and the cause was not the union.* Two independent losses, both measured: (a) `facts.sid_image` read only the SID stores whose address is a **constant**, and a merged access indexes the register file, so every per-voice store fell out of the role plane -- the store's own base still names the register (`sid_stores` joins both lists, as `views.sid_fields` already did), and the "is this region really the image" guard now counts the elements each access **observably reached** (its envelope) instead of only the ones a literal address named, so a hundred-byte block one cell of which feeds a register is still refused. Follin 17-19 name `freq_lo`/`freq_hi`; JCH's write-out prints `sid[x].pw_lo = voice[x].pw_lo` for `sid.reg[2 + voice[x].b1740] = voice[x].acc_5`; (b) `cursor_1141` was lost to **block boundaries**, not to region typing: `Facts` expanded a value through the definitions of its own block only, and under `--closure static` the load sits one block above the table read that indexes it. What a value *reads* is now expanded through the procedure's `single_defs`; what a *store* is (a self-update, or plain) stays block-local, since that classification is a property of the statement and deeper expansion only inflates its operator count. Measured: the role plane gains `cursor` on 4 regions and `table` on 30 across the exemplars, and no role is lost anywhere | P1 (#248), Q1b | naming | facts, recover |
| an edge into another copy at a row that is not that copy's entry (*Automatas*' `$16AB` at 30 s). Lowering it as `v += 1; goto` that template row is sound and folds it, but measured: the merged body then has two entries, which costs a `goto` and the `ad`/`ctrl` field names -- refused as the narrower rule says | P1 (#248) | fold reach | copyrows |
| ~~a sibling family is discovered from whatever block boundaries the program happens to have~~ *done (Q2)*. The diagnosis stands as written: a copy base was found because a `trap 'untaken'` block carries the **branch's own pc** as its `src`, and `closure` deletes exactly those blocks, so Automatas' `p_1022` 3×44 family (entered at `$119A`/`$1222`, both `BMI`, both fallen into) was found with the closure off and a 2-copy candidate at `$1197` won with it on. The fix reads the *image*: `siblings.Code` gives each instruction its transfers (from the opcode, so falling into the next copy is an edge like a jump, and a direction no execution took leaves nothing) while its executions come from S2b's `Proc.nodes`, which is every instruction an execution of that procedure reached with its site count (the first cut inferred them by walking the image's fall-through chain and invented 716 instructions no execution reached, 20 per Ghouls subtune in `init` alone -- post-review fix, no family moved). Candidate bases are the boundaries the image itself draws: a leader (a transfer's target, either way out of a branch, an address nothing falls into) and the branch that decides. `_select` ranks by what a family explains -- widest, longest, then the instructions its copies hold that no row explains -- which pins the phase a repeating cascade leaves open, and a candidate whose operand map is not a function takes its code with it. Measured over all 44 certificates: families identical under `--closure trace` and `--closure static` (44/44; on `main`, 22 of 44 disagreed), so P2's discover-before-close workaround is deleted. The two one-liners recorded as insufficient stay insufficient | P2, Q2 | fold reach | siblings |
| the closed program is a different shape for the *covered* code. **Half done (Q1a).** A closed arm no longer owns anything: `closure.closed_blocks` names the blocks only a closed path reaches, and S5/S6 compute dominance, the loops and a tail's region on the covered subgraph (`graph.edges_of` cuts a closed block's edges back into covered code) while post-dominance stays the whole graph's, so the arm still nests in the branch that offered it and rejoins by fall-through. `Je_suis_Linus` at its certified horizon: **23 → 16 `goto`** (30 s the same), SW's `Emomyst` 2 → 1, and the trace-closed print of all 44 certificates is untouched by it. What is left is **not** dominance: a closed path that rejoins covered code at an address inside a covered block *splits* that block, and the extra predecessor stops `ssa.merge_chains` gluing the pieces, so the covered program is physically a different graph and the promotion cascade takes a different order. What blocks the remaining 16 is measured, all in one outlined procedure: 7 join regions with more than one way out, 2 whose region both leaves and returns, 4 that would promote but not pay. `cursor_1141` was still lost, which Q1b measured is neither region typing nor structuring but the block-local reach of the role plane, and fixed (row above). `--closure static` stays off; the criterion (GT2 0 `goto`) is not met | P2 | presentation | structure, closure, ssa |
| ~~periodicity proof for free-running counters (lcm argument)~~ *refuted (P2): the row's own exemplar does not have that shape. `period.py` + `tools/tuneprog_period.py` classify why a subtune has no repeat — each cell's smallest period, the SID stream's own period, per-loop drift — and put both Commando subtunes and `ghouls-song21` in the `aperiodic` class: the drifting cells are per-voice pulse-width accumulators whose full byte is the SID write, and Commando song 2's frame counter already divides its loop. Nothing certified needs the mask proof; re-rank it on the campaign's population, and keep it fail-closed (an unclassifiable read keeps the whole cell)* | Commando | certificate | verify, period |
| a copy's stream runs past a jump into code no execution reached and nothing transfers to (the Knob's `$1167 INC $1748,X`), which the fold then carries as an unverified row. Ending the stream there was **measured and rejected**: the Ghouls dispatch handlers are reached only through a patched indirect jump, which the image's transfer relation does not carry, so the rule shatters the 3×237 voice families (to 3×2) and breaks closure invariance on song 31. It needs `jumptab`'s facts, not the image alone | Q2 | fold precision | siblings, jumptab |
| the phase of a repeating cascade is pinned by `slack` and then by the lowest base; where two readings tie exactly (Automatas `p_168C` `$172C` vs `$1734`) the choice is a convention, not a proof. Refusing both was measured and rejected -- it also refuses the two real 5-copy cascades, whose `$12BE` and `$12C0` readings tie | Q2 | fold precision | siblings |
| per-call input capture in the Ghidra facts; resolve the two emulator disagreements | Ghidra export | oracle | ghidra_facts, headless |
| opcode cells whose alternative is not `RTS` in the SLEIGH export (overlay or paired constructor) | Ghidra export | baseline | ghidra/6510 |
| family name dictionaries by structural alignment | all | naming | recover |
| second interrupt schedule (NMI + IRQ sharing regions) | design §10, JCH | scope | machine/trace/verify |
| 16-bit views for halves stored by unrelated instructions (Follin freq shadow, pulse width). **Refused in Q1b with the measurement**: the row's stated cause is wrong and the work is bigger than a naming change. Follin's pulse width `$3F/$40` *is* one carry chain in one block (`pw_lo += t; pw_hi += carry`), and what refuses it is that `word._pairs` reads the two addresses with `addr_split`, while a merged body addresses both halves through per-copy **columns** (`Load(copymap, base + cv0*2)`); the frequency shadow `$75/$78` is not a chain at all -- its borrow is carried by a *branch* (`x16 = freq_hi` / `freq_hi - 1` in the two arms), which needs if-conversion, not a pair rule; and SID Wizard's pulse-width halves are two unrelated values (`pw_hi = a + b`, `pw_lo = acc`), not one 16-bit quantity. Above all, `names.u16` is keyed by `(lo region, hi region)` and Follin's zero page is **one** region, so the plane cannot name a pair of its cells at all: the fix is to key the u16 view by cell, which moves every certificate's u16 names | Follin, SW | presentation | word |
| ~~sign-extension/flag-algebra printing~~ *done (Q1b), one of the two shapes as asked and one refuted.* `(A + T) - ((T & $80) << 1)` prints `A + sext(T)` and `(A ^ B) & (A ^ (A - B))` under a sign test prints `overflow(A - B)`; both are identities over eight bits, recognised in `idioms` and rendered in `pseudocode`, and neither touches `idioms.fold` (which S4 runs, so the certified IR is untouched). SW's two branch dispatchers and the tempo test on both tunes are the only sites in the 44. `if (tempo & $80)` is **refuted**: V after `SBC` is `sign(A^M) & sign(A^R)`, which reduces to the operand's sign bit only given a range proof on both operands that no evidence supplies -- the honest print is the overflow itself | SW | presentation | pseudocode, idioms |
| ~~index range for jump-table extents (Follin 23 vs 21 arms)~~ *done (P1) intraprocedurally: a merged family's k tables are parallel, so they start at the same index (the region's own base as the lowest base names it) and each holds the gap between two bases; the branches on the one path into a dispatch then prove a range for the index (sign test, equality, compare) which cuts into that layout and never moves it. The layout is an inference like the extent rule, not a proof; the range is a proof, and is applied last so it can only remove entries -- Follin's three dispatches are 21 arms each, none displaced. Not built interprocedurally: SID Wizard's two dispatches do take their index as an argument, but what the caller's `CMP #$60` proves ([96, 256)) is already inside the extent (123..127 and 125..127), so the walk would cost code and buy nothing measurable* | SW | closure | jumptab |
| ~~`--songs all` resume state for mixed stop reasons~~ *done (P2): the trace stage kept a bare list of finished subtunes, so a subtune was skipped on resume whatever horizon the resuming invocation asked for — a run interrupted under `--calls N` and resumed under `--until-period` certified subtunes at different, unrecorded horizons in one certificate. Each subtune now carries `{calls, stop, horizon}` (`stop` = period / horizon / budget), a record from another horizon rewinds that subtune to S1, and `verify.pkl` carries the reference length it was taken against* | Follin | tool | pipeline |
| ~~`printer`/`pseudocode` memo invalidation with 16-bit views; `node_exprs` unknown-node guard~~ *done (P2): the memo (value expression → the cell that holds it) dropped an entry only when the entry's key read the written region, never when the cell itself lived there; `word.fold16`'s `W16` made it reachable because the renderer invalidated neither half, and a call invalidated nothing. Entries carry their region and `forget` drops both directions; `node_exprs` raises on a node no table lists* | consolidation | quality | irwalk, pseudocode |
| ~~under `--songs all` a subtune that stops on a period is certified at `ceil((first_repeat+1)/chunk)*chunk` ticks~~ *done (Q3)*: the horizon a subtune certifies is its witness, not where the chunk landed. One rule (`pipeline._certified`) serves both paths, reading the witness from the subtune's own trace (`Trace.witness`, the earlier of the two footprints' first repeats), and `verify_all` bounds each subtune's reference by it. The trace still runs to the chunk boundary -- trimming it would change the *union* program the certificate is about -- and only the certified length moves. `ghouls-songs-all` alone: 31 of 32 subtunes drop to `first_repeat + 1` (song 1 16,000 -> 12,997, song 12 4,000 -> 6, song 21 stays 20,049, the one horizon stop), 220,049 ticks -> 111,763, with period, `complete`, divergences, envelope traps and every program field identical, and each subtune now certified over exactly the horizon its own `ghouls-songNN` certificate uses (a hermetic test asserts all 32). What the untrimmed trace still holds past a witness adds nothing: a witness is a repeated state with no input consumed, so those ticks replay sites, edges and accesses the certified prefix carries -- stated in `_certified` and in the certificate schema, where `ticks` now means *verified*. A machine-readable "built from" count is backlog (the field would move all 44 documents) | P2 | tool | pipeline |
| ~~a block one init loop made, walked as `base + n*k + v` -- element inside, field outside -- is the *transpose* of the stride view~~ *done (Q1b)*: `views.transpose_split` is `field_split` with the two indices swapped. Such an index carries no scale, so what it carries is the **element count** of the stride-1 view it walks (`_elem_index`: JCH's three tracks), which makes every field k wide; each access then confirms the layout by its own **envelope** -- it must stay inside one k-wide field -- and only play-phase accesses decide it, since the init clear loop reaches the whole block. The printed form applies the same envelope test per access, so that clear loop keeps `b1014[v] = 0` and only the fields play walks print as fields. `$1014` prints `voice_2[v].f00..f09` and `$1748` `voice_3[v].timer .. .timer_3` on both V20 builds; a block anything reads as a table (one envelope crossing a field) keeps its flat address | P-JCH (#251) | presentation | views |
| ~~a SID register offset taken from a per-track table (`$1740,X` = 0, 7, 14) prints as `sid.reg[5 + voice[v].b1740]`, not `sid[v].ad`~~ *done (Q1b)*: `0, 7, 14` is the voice -> register-block map, the other half of the hardware fact `VOICE_REG` already states, so a read-only region whose three elements are exactly `7*i` is that map (`facts.voice_maps`) and an index taken from it **is** the voice. `Printer.voiced` accepts it beside the stride-7 forms; the table itself is named `voice_map`. Both V20 write-outs print `sid[x].pw_lo`/`freq_lo`/`ad`/`sr`/`ctrl` (Puterman's `ghost[x].ad`), and a clear loop over the register file still prints `sid.reg[v]`. Negative: `0, 7, 13` keeps the read | P-JCH (#251) | presentation | facts, views |
| ~~a loop body's joins are `goto`~~ *done (Q1a): a region several jumps reach and that leaves **one way** promotes too -- the helper returns where the edge went and each entry becomes `call; goto that edge`, sound when no path inside returns from the caller and every value the exit reads is one the region has wherever it takes that edge (those become the helper's return values, `_slots` in the other direction). A tail with a way out must pay for the call the exit becomes (strictly fewer `goto`), where an exit-free tail may break even because it is what makes the next one exit-free; and a promotion's own residue is queued, since the helper it makes was never revisited before. Both V20 tunes: **7 `goto` → 0**, +41/+51 printed lines, +4/+6 procedures. GoatTracker, SID Wizard, Commando and all 32 Follin subtunes print byte-identically* | P-JCH (#251) | presentation | tails, structure |
| ~~comparing a tick's writes against a *sampler* needs their cycles~~ *done (Q3), the row's own diagnosis refuted by measurement*: `grid.py` frames **both** sides by the interrupt period a write's cycle falls in -- the tracer's `wlog` against tick 0's cycle plus `cycles_per_tick`, a sidtrace CSV against `cycle - since_video_irq` -- and the Knob is then **0 of 3,000** frames different with *no sample point at all*. The frames the old comparison differed on (297 as measured here, 494 as #251 measured it) were not the missing cycles: they were `pysidtracker.oracle.grid_from_writes` rounding each write to the nearest frame *from the first play write*, a boundary 9,828 cycles into the tick which the wrapper's ramp crosses from tick ~2,450 on. Fitting a sample point instead reaches 1 of 3,000 at best (best at ~9,400, not 9,828), because the two clocks differ inside a frame (row below). The delta is attributable to one side and the oracle test asserts it: against the interrupt-framed oracle **both** trace rules (by cycle, by call index) are 0, and against the rounded anchor both are 297. What the trace's cycles do buy is the general rule, which the hermetic test states: a tick that outlives its frame lands its late writes in the next one | P-JCH (#251) | oracle | trace, testing |
| `grid.sidtrace_clock` takes one period as the median gap between a CSV's interrupt raises and refuses a set whose gaps are not whole multiples of it (within 1 %), which is right for a fixed clock and wrong for a *reprogrammed* one: a CIA tempo-change player that rewrites the timer latch mid-song now refuses outright where the old median framed it badly. The sweep will meet it and wants a per-segment clock -- the raises segment at each rewrite -- rather than one median over the run | Q3 | oracle | grid |
| a certificate records what it *verified*, not what the program was built from: a horizon lands on a chunk boundary, so the trace can hold up to `--chunk` ticks more per subtune (`ghouls-songs-all`: built from 220,049, verified 111,763). Sound as it stands -- past a witness those ticks replay states already seen, so they carry no site, edge or access the certified prefix lacks -- and stated in `_certified` and the schema, but the *document* says it only in prose. A `traced_calls` cost field would say it in data and move all 44 certificates | Q3 | certificate | emit, pipeline |
| the tracer counts CPU cycles where the sampler's clock also spends VIC DMA: the play entry is a constant 57-60 cycles later in `sidplayfp`'s frame than in ours, and inside one ramped tick of the Knob (its last write 9,312 cycles in) that offset drifts a further +533 -- one badline in eight raster lines. So a write's *offset inside* its frame is not certified -- only which frame it lands in, and the order. Free today (the grid is framed by the interrupt on both sides, and both agree write for write) and worth a raster model only if a comparison ever needs sub-frame time | Q3 | model | tracevm, machine |
| a write to `$D000-$DFFF` with I/O mapped also writes the RAM under it, in the tracer and in the interpreter alike (`tracevm._wr`, `interp.iostore`), where the hardware writes only the chip. Invisible until #251 made that RAM observable storage, and still unobservable in every exemplar (both sides agree, so no certificate can see it; the two new tunes match the oracle write for write). The honest model is two planes -- the chip and the RAM beneath -- which the address-keyed footprint, region relation and state hash all assume away, so it waits for a tune that discriminates | P-JCH (#251) review | model | tracevm, interp |
| Ghidra function bodies vs clone-per-entry (`ghidra_partial` rows) | Ghidra export | oracle | ghidra_compare |
| numba tracer/executor if the campaign needs it | design §11 | performance | trace, emit |
| ~~stack elimination in S4 (§7) — gate item~~ *done (#237)* | user gate | core | frames, stack |
| an `RTI` entry tune is residual: the status byte the machine pushed at the interrupt is a frame the tick never wrote, so its stack stays (model the entry frame as the tick's contract instead) | stack elimination (#237) | core | build, verify |
| a residual stack is whole-program: one unplaceable read keeps `SP` in every procedure, where an interprocedural frame layout would localise it | stack elimination (#237) | core, precision | frames, stack |
| `--until-period` stops at the earliest repeat of either footprint, so a *residual* tune may stop before the page-inclusive repeat it must certify on (re-trace with `--calls`, or trace on after S4 has decided) | stack footprint (#239) | horizon policy | pipeline, trace |

### 5b. Execution packages (2026-08-20)

The open rows above, grouped by owner-module overlap into one agent stage each;
one Opus agent per package with a read-only review before each merge, as §10.

| pkg | rows | size | note |
|---|---|---|---|
| ~~**P1 fold reach**~~ *done*: cross-copy edge (the rule held; ownership was the refusal) · dispatch closure over a per-copy column base · index range for extents (intraprocedural) · `unroll` copy-0 constant guard | medium | S4 changes for newly folded families; recert 42/42 with ticks/period/complete/divergences fixed |
| ~~**P2 certificate accounting**~~ *done*: lcm periodicity proof (refuted by measurement -- `period.py` classifies the obstruction and puts Commando where `ghouls-song21` is) · bounded static closure (`closure.py`, behind `--closure static`; the fold's discovery moved onto the trace-closed program so the closure cannot take a family away) · memo invalidation and the `node_exprs` guard · `--songs all` per-subtune stop reasons | medium | recert 42/42 unchanged: the certified product is still the trace-closed one |
| **P4 Ghidra oracle** | per-call inputs + the two disagreements · `ghidra_partial` bodies | medium | independent; interleave anytime |
| ~~**P-JCH V20 family**~~ *done (#251)*: `Puterman/I_Could_Eat_a_Knob_at_Night.sid` 8,577 ticks and `JCH/Guldkornekspressen_Intro.sid` 2,401 ticks, both **complete**, 0 divergences, 0 envelope traps, stack eliminated, no sibling family (JCH loops), 0 SMC cells in the player; five generic fixes, all in the machine model or the printer: the 6510 port's reset state, the port bytes in the program's image, the chip-vs-RAM class per access at $D000-$DFFF, the RAM under the register file as a shadow at delta 0, and a recurrence is not a busy-wait. both traces reproduce the `sidplayfp` grid write for write ([prototype-jch.md](prototype-jch.md)) | small-medium | done; recert 44/44 |
| ~~**Q1a structuring**~~ *done (2026-08-21)*: the k-entry merged body's `for` (the prologues **are** the step; the Follin half of that row refuted — a one-voice subtune has no chain edge and no copy loop) · JCH's voice-loop joins (a region with **one** way out promotes, returning what the exit reads; 7 `goto` → 0 in both V20 tunes) · closed-region structuring **half done** — a closed arm owns no dominance and no loop, GT2 under `--closure static` 23 → 16 `goto` and SW 1 → 0, but the criterion (0) is not met: the residue is the covered block a rejoining closed edge *splits*, which `merge_chains` then cannot glue, so the covered graph itself differs. `--closure static` stays off. Recert 44/44 with no field moved; 39 of the 44 printed texts byte-identical, the other 5 being the three *Automatas* certificates (the `for`) and the two JCH ones (the joins) | medium | presentation-only, certificates frozen; absorbed P6's first row |
| ~~**Q1b views & naming**~~ *done (2026-08-21)*: the role plane reaches a merged per-voice store and crosses a block edge (`cursor_1141` back under `--closure static`; Follin 17-19 name `freq_lo`/`freq_hi`) · the transpose stride view (`$1014`/`$1748` print as records over the track index) · the voice -> register-offset map (`sid[x].ad` for `sid.reg[5 + voice[x].b1740]`, on both V20 builds) · `sext(T[i])` and `overflow(a - b)`. **Two of the five were refuted by measurement** and are re-stated above: `if (tempo & $80)` is not an identity, and the 16-bit view for unrelated halves needs `names.u16` keyed by cell rather than by region before any pair rule can help. Recert **44/44 reproduced, 0 mismatched**, no certificate field moved; printed text changes on all 10 baselines, every line a name or a view (JCH +13 lines each for the two new record headers, the rest byte-for-byte substitutions) | medium-small | presentation-only; absorbs P5 and P6's role row |
| ~~**Q2 sibling discovery deep fix**~~ *done (2026-08-21)*: candidate bases and the chain relation from the image (row above), P2's discover-before-close workaround deleted, and the P2 shape plus randomized block shapes as hermetic property tests (six seeds x four shapes: horizon 6/9 ticks x closure off/on, asserting the same bases and rows and that S2b really glued a copy entry into the block before it). Also `jumptab._copy` no longer follows a name twice, which a merged body's definitions can make cyclic. **Certificates moved**, which the row's own diagnosis predicts: the families `main` reported were phase- and boundary-dependent, so 26 of the 44 change and every one of them is the same family or a wider one -- the Ghouls sound-effect subtunes all fold the one 3-voice family at `$6234`/`$6421`/`$6610` instead of a per-subtune shifted 2-copy reading, Automatas' four are unchanged plus a fifth 2x6 in `p_168C`, and JCH/SW gain one small 2-copy pair apiece that the block shape had hidden. 0 divergences everywhere, no protected field moved (ticks, period, first_repeat, complete, divergences, envelope traps, stack all identical on all 44), and over the set: families 53 -> 59, refused 4 -> 0, copies folded 158 -> 185, rows 6,412 -> 6,754, and the certified programs *smaller* -- `ir_statements` 19,707 -> 19,547, `ir_blocks` 8,397 -> 8,313. One fold is lost, `ghouls-song08`'s `$682A`/`$6830` pair: copy 0 ends on `BCC $683C`, which is outside the family and ran 16,000 times, so the copy can leave the family -- the rule `enters` always stated, which `main` could not see because the trap block carries the *block's* `src`. 16 of the 44 printed texts are byte-identical; the rest follow the fold | medium | done; discovery is boundary-free, certificates re-baselined |
| ~~**Q3 sweep prerequisites**~~ *done (2026-08-21)*: the grid comparison helper (`grid.py`, both sides framed by the interrupt; Knob 0/3,000 frames, the old comparison's 297 explained and refuted as a sample-point question) · `--songs all` certifies each subtune's witness, not the chunk it landed in (`ghouls-songs-all` 220,049 -> 111,763 ticks, nothing else moved) · one canonical tune map (`tunes.py`; recert and both test suites resolve through it, a hermetic test refuses an HVSC path written anywhere else, and the recert tool's own second copy of the tune cache is gone) | small | recert 44/44 |
| **Q4 = P3 stack residual policy** | RTI entry frame as the tick's contract · `--until-period` residual horizon | small-medium | pre-sweep (every installed-handler tune is an RTI entry); frame localisation stays deferred |
| **accepted boundaries** | an edge into another copy's non-entry row (`$16AB`: costs a `goto` and two field names, buys nothing) · folding a silent copy costs ~6 % statements (buys names and the coverage vector; `--no-merge` is the escape) -- Q2 makes this uniform, so Ghouls 17-19, 25, 27, 29 each grow 24-49 printed lines · a fold takes cells the naming plane had: the Knob's `$17B9`/`$17BC` leave the voice record for `b17B9[v + 3]`, Commando song 2's `$54F8` likewise | — | measured, recorded, not work |
| **not now / data-gated** | NMI+IRQ (§8 item 3's own prototype, scoping recorded) · two-planes chip/RAM (waits for a discriminating tune) · periodicity reduction (waits for a sound shape) · naming, numba (§8 items 4, 7) · residual-stack localisation (waits for the sweep's residual rate) | — | |

Order (2026-08-21, post print review): **Q1a → Q1b → Q2 → Q3 → Q4 → campaign
(§8 item 1) → data-ranked remainder**; P4 interleaves anytime. Historically:
**P1 → P2 → P-JCH V20 → campaign.** P1/P2 first because they change certificate claims the campaign
would otherwise measure stale; the V20 family stage precedes the sweep so the
largest family's issues close by generalization on two tunes rather than at
scale; P3 (RTI contract, residual horizon) follows it; NMI/sample deferred,
data-gated on the sweep. P1, P2, P-JCH, Q1a, Q1b, Q2 and Q3 are done; the campaign is also
what re-ranks the two rows P2 left open (the periodicity reduction and the
closed program's remaining structuring) and the three P-JCH left (below).

## 6. Gate: fold and stack before any new family

Two things must be true before another tune family is admitted, both because
they are about the *core* matching the design rather than about breadth:

1. **The copy index is an IR value and the folded program certifies.** k sibling
   copies of one template (Follin's voices, defMON's cascades) are one procedure
   body under `for v in 0..k-1` *in the certified S4 program*, every per-copy
   operand an address through a per-copy table `T[v]`, coverage recorded per
   `(template site, v)`, and the certificate reproduces on the folded program.
   *Status after stage C (#244):* **met.** The correspondence is spent before the
   IR exists: `copyrows.py` decides what folds, `copymerge.py` plans it, and
   `build.py` lays the rows once -- an operand the copies disagree on is a load
   from a per-copy column `T_x[v]`, the chain edge is `v += 1; if v < k: header`,
   an edge from outside enters through a prologue that sets `v` to the copy that
   holds its target, and a site's count is a vector over `v`. What the index
   cannot name refuses with its reason: a row whose copies do not lift to one
   shape stays k rows and a differing successor becomes a `switch (v)` over the
   copies' own successors (which is how the k parallel dispatch tables come to
   share one arm body), and a cross-copy edge anywhere but the chain refuses the
   family. `closure.py` and `--closure` are gone; `--no-merge` builds what S2b
   built. All 42 certificates reproduce with `copies` added and the statement,
   block and region counts of the folded program. Stage C (#244) spends the
   representation in the text: one S6 pass (`copyview.py`) reads each per-copy
   column once and prints it as the operand it stands for -- an affine column as
   that step in `v`, through the stride vocabulary that already existed
   (`sid[v].freq_lo`, `b640F[v]`), any other as copy 0's own operand plus a group
   slot `views.py` names `voice[v].field` and lists address by address in the
   state header -- and a family's loop prints as `for v in 0..k-1` over the
   coverage vector the correspondence proved, so the chain edge and the `v += 1`
   are the header. Measured (presentation only; `tuneprog.py`, `certificate.json`
   and every certificate byte-identical, recert 42/42): Follin's certified song 1
   is 757 printed lines with `tick()` 495 of them (794/578 before), 51 named
   per-copy fields in one `voice[3]` view, 2 of 60 columns left as table reads;
   *Automatas* 744 → 716 lines over the whole song (800 → 782 at 30 s) with both
   cascades `for v in 0..k-1` over `rec2[v]`; GoatTracker ×2, SID Wizard ×2 and
   Commando song 1 print byte-identically to what stage B produced, having no
   family, and Commando song 2's 2-copy family at `$5301` becomes a `for w in 0, 1`
   over `copy0[w].timer`. `copyfold.py` is gone, `unroll.py`
   keeps only the view-level fold with no static correspondence, and the
   presentation layer has no cross-module private imports left.
   *Review of #234 (2026-08-18):* the fold was placed at the end of a lossy
   pipeline and asked to prove a syntactic identity the pipeline had destroyed
   (trace closure drops arms, S4 merges block starts, SSA renames); the exact
   correspondence (`Copies.addrmap`) was reconstructed through ten thresholds
   (`GRAM/MINROWS/MINARM/MAXCOPIES/LOOK/CONFIRM/LIMIT/NEAR/MINSLOTS/MINSTMT`),
   two tokenisers (`unroll`, `copyfold`) and a second program build; the closed
   program's "0 divergences" is tautological (lifted arms have count 0); every
   listed boundary (SFX subtunes, the union's `chk`/`ram` class, row-advance
   blocks, horizon-dependent discovery) is one cause. That review is what §7b
   re-specified; #234's mechanism was the prototype that found the requirements.
   *P1 closes both boundaries this listed.* The 8 one-voice subtunes refused on
   ownership, not on the rule: a copy holds only what its rows hold, and the
   stream an alignment stepped over before a copy's first row is the image of no
   row, so `v` cannot name it and the run leaves the family there and re-enters at
   the row. And a merged dispatch enumerates again, per copy: a column is
   read-only, so copy *j*'s writer is that expression with each column read
   replaced by its *j*th entry. **What remains a boundary:** an edge into another
   copy anywhere but at that copy's own entry (*Automatas*' `$16AB` at 30 s), and
   a merged body whose copies have preambles of their own has k entries, which the
   structurer prints with a `goto` and no `for`.
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
   too (Blackbird, Galway, Walker at 10 s of music). *Amended by #239:* #237 also
   dropped the stack page from the state hash unconditionally, which let a
   residual program claim a periodicity its scratch byte contradicts; the tracer
   now hashes both footprints and the certificate claims on the one the program's
   proven stack status allows. What stays residual, by proof and not by guess: a
   stack scratch *area* whose pointer is not a constant offset, a `TSX`-relative
   read of another frame, an `RTI` entry frame's status byte, and the pointer
   read as data — and then the whole program keeps its stack, since such a read
   can see any byte of the page.

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
   `"stack": {"depth": n, "procs": [...]}` (#237 wrote `residual_depth`; it is
   `depth`, and `"unknown"` where an access is not a slot).
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

*Outcome (#241).* `siblings.py` (330 → 424 lines) keeps `Copies`, `align` and
`correspond` and replaces everything under them. A candidate pair of bases is two
block entries with the same opcode, executed as often as each other, the first
exiting into the second without reaching past it; the copy at the second is the
window the first embeds in whole (`difflib.SequenceMatcher` over opcode bytes,
where only a gap may separate the streams), and its end is the next base, so the
run extends itself. A copy before the first may sit inside the block before it
(defMON's clone) and is recovered by the same proof; only the copy nothing
follows may hold the template in part, and it must still hold its own exit. The
family holds while every copy's operand map is a function over *every* row --
keyed by addressing mode, since an indexed base whose index is data (Follin's
`STA $D400,X`) does not name what the same literal names under `abs` -- and an
ambiguity refuses the family whole. Arms pair by their index in the parallel
tables `jumptab`'s writer analysis reads, and an arm stream stops where control
has left and a region owns the next byte (a handler's own cells sit past its
jump). Measured at 30 s: Follin song 1 one family of three voices, 419 rows
(#234: 420) and a printed text identical field for field (627 lines, 261
statements, 89 unverified); Automatas five families `[5,5,3,2,2]` (#234: four
`[5,4,4,2]`), both cascade runs still folding, 763 → 759 printed lines; GT2 ×2
and SW ×2 none, as before; `tools/tuneprog_recert.py` 42/42. **What it does not
do**: a *two*-copy run whose second entry S2b merged into the block before it is
refused (the template then straddles the copy boundary and only the last copy may
hold part of it, which two copies cannot establish); a copy that differs from its
successor by a *replacement* rather than a gap is refused; and the selection of
non-overlapping families is still widest-then-longest, not a proof.

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

*Outcome (#242).* The plan is computed once, before `build_ir`: `copyrows.family`
folds a row when every copy holds the same instruction, no copy dispatches on its
own opcode byte, every copy's lift has one shape and what the trace lifted for a
copy that ran it is what the image says it is; `copymerge.plan` places the columns
and records the coverage vectors, and `build.py` lays the rows once. A column is a
per-copy table in a band no access, no code and no region can see (outside the
load image, the stack page and I/O, where every byte is a pinned input to the
program whatever it holds), so the address arithmetic is ordinary 16-bit and no
access class, executor or image size changed. Two families whose columns hold the
same bytes share one table, which is what keeps a procedure and its clone one
outlined helper. The columns are read once at the loop header where that header
dominates their uses -- the family nothing enters but its own entry -- and at the
use itself otherwise, where the read is part of an address expression and costs no
statement: giving every entry point its own reading block cost a table's worth of
statements per entry (Follin's subtune 11 grew to 2,122 statements from 1,141
that way; it is 644 under the dominance rule). `v` takes a phi like a register (`ir.copyval`), and `from_ssa`
now names its swap temporary for the edge, without which the stack analysis loses
`SP` at a loop header and the program keeps a stack it does not have. **What the
index cannot name refuses**: a row whose copies do not lift to one shape stays k
rows and a successor that differs across copies becomes a `switch (v)` over the
copies' own successors -- which is how the k parallel dispatch tables come to
share one arm body, since each copy's patched `JMP` holds its own target and no
key pairs them; a cross-copy edge anywhere but the chain edge refuses the family
whole (the generalisation -- any edge from copy *j* into copy *j+1* is an
increment -- was tried and is wrong: *Automatas* then wrote copy 1's addresses at
copy 0, so only the edge the chain proof established increments `v`).
Measurements are in §6 item 1 and §10 row 3.

**Stage C — consolidation.** One view pass over the table representation
replaces `copyfold.py` + `unroll.py`; `views.py` names `T_x` fields; docs
(`tuneprog.md`, the prototype records, this plan: gate closed).

*Outcome (#244, #245).* `copyview.py` (279 lines) is that pass: it collects every
column of the view, the accesses that read it and the copy index each occurrence
names, then decides once per column. Values that step affinely become that step
in `v`, so nothing new prints them -- `regcell`'s 7-byte voice block gives
`sid[v].freq_lo`, a region's own stride gives `b640F[v]`; values that do not keep
their table read and are named by the group view `views.copy_groups` builds from
the same role/SID-shadow/`b%04X` vocabulary, which the state header lists address
by address. **The named column keeps its read on purpose**: substituting copy 0's
operand was tried and is unsound -- an operand every copy agrees on can equal a
slot's address, and inside the loop nothing tells the two apart (Commando song 2
holds one of each at `$551A`), so the printed index is the copy the *access* names
and a constant stays copy *j*'s own cell. The field names still come from the
addresses, through a substituted twin of the view (`copyview.naming_facts`). Two
rules refuse rather than invent a name: a column whose copies sit at different
offsets of a record is not one field, and two columns whose copy 0 agrees but
whose copies do not are two fields and neither gets a name -- both keep the read,
address visible (2 of Follin's 60 columns, 2 of *Automatas*' five); and a
compound assignment needs the load to name the *very* cell the store writes,
which two column reads only do when they are the same expression (#245 -- two of
Follin's statements printed as `x <<= 1`/`x += 2` where the source was another
field). Where a
family's indexed columns select exactly a stride view's regions the two are one
view under one name, which is what makes Follin's certified song 1 one `voice[3]`
of 51 fields. The loop comes from `loops.copies`: a merged family's `for` runs the
coverage vector, not a recurrence the exit tests must re-derive (*Automatas*'
cascade leaves its loop through a `switch` arm, which `induction` cannot read),
and `strip` follows the branches so the chain edge is the header wherever the copy
ended its work -- only where nothing follows it, since a test mid-body guards the
statements after it. `copyfold.py` is deleted, `_step`/`indexed`/`elems` move into
`views.py` as the algebra `unroll` and `copyview` share, and `structure.py` splits
(`loops.py`) to stay under 500 lines. **What it does not do:** `unroll.py` still
finds only consecutive isomorphic runs inside one body with no static
correspondence -- it folds Follin's init copies and *Automatas*' write-out, and
still does not fold the three row-advance blocks (§5); a group slot with an index
prints from the cell, so a clear loop over a block whose first cell a family names
reads `voice[0].b0021[v]`; and `unroll`'s own groups still substitute copy 0's
constants into the loop they made, which carries the same ambiguity the family
columns no longer do -- bounded to that loop by the `local` flag, but a run whose
body holds a constant equal to one of its slots would print it with the loop
index (§5).

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
| 2 | ~~gate 1 stage A (§7b)~~ *done (#241)* | `siblings.py`, `tests/tuneprog/test_siblings.py` | met: `GRAM/MINROWS/MINARM/MAXCOPIES/LOOK/CONFIRM/LIMIT` gone; property tests over seeded random templates; Follin s1 one family of three voices (419 rows vs 420, printed text identical), Automatas' two cascade runs still fold (763 → 759 lines), GT2 ×2 and SW ×2 none; 42/42 recert |
| 3 | ~~gate 1 stage B (§7b)~~ *done (#242)* | `copyrows.py`, `copymerge.py` (new), `build.py`+`lower.py`, `regions.py`, `jumptab.py`, `ssa.py`, `ir.py`, `printer.py`, `pipeline.py`, `emit.py`; `closure.py` deleted | met: Follin song 1 is one body of 400 folded rows over 60 per-copy columns and certifies unchanged (1,229 statements in 441 blocks → 671 in 254, 68 regions → 44, 133 of 471 merged statements unverified and marked per statement); the `--songs all` union folds too (1,553 → 770, 520 → 294, 75 → 45, 5 unverified); *Automatas* folds its 6-copy cascade in both procedures that hold it (995 → 805, 305 → 241) and refuses two families with their reason; 24 of Follin's 32 subtunes fold at least one family and 8 refuse; GoatTracker ×2 and SID Wizard ×2 have no family and are byte-identical; `tools/tuneprog_recert.py` 42/42, with ticks, period, `complete` and divergences unchanged everywhere |
| 4 | ~~gate 1 stage C (§7b)~~ *done (#244)* | `copyview.py` (new), `loops.py` (new), `views.py`, `structure.py`, `printer.py`, `pseudocode.py`, `pipeline.py`, `unroll.py`; `copyfold.py` deleted; docs | met: one tokeniser (`unroll`), no cross-module private imports in the presentation layer, every module ≤ 500 lines; Follin's certified song 1 794 → 757 printed lines (`tick()` 578 → 495) as one `for v in 0, 1, 2` with the 21-arm switch inside and 51 `voice[v].` fields, *Automatas* 800 → 782 at 30 s (744 → 716 over the whole song) with both cascades `for v in 0..k-1`, GT2 ×2 / SW ×2 / Commando song 1 byte-identical; `tools/tuneprog_recert.py` 42/42 with every certificate byte-identical |
| 5 | ~~P1 — fold reach (§5b)~~ *done (#248)* | `copyrows.py`, `jumptab.py`, `unroll.py`, `recover.py`, `pseudocode.py`, tests, docs | met: the 8 one-voice Follin subtunes, their `$6941` triple in 28/30 and *Automatas*' `$112A` fold and verify (`$16AB` refuses with the narrower reason); Follin's three dispatches enumerate 21 arms each with none displaced (3 → 39 unverified arms at 30 s; the certified song 1 gains 16 arm blocks and no statement); *Automatas* 805 → 733 statements, 241 → 227 blocks, 98 → 80 regions; subtune 14 199 → 106 blocks, 255 → 212 statements, 52 → 39 regions; `tools/tuneprog_recert.py` 42/42 with ticks, period, `first_repeat`, `complete`, divergences and envelope traps unmoved everywhere |
| — | reviewer, before each merge | read-only | refutes: new tunable constants, duplicated mechanisms, tests that encode an exemplar rather than an invariant, module > 500 lines |

Every brief carries the global directives (no tuning constants — a threshold is
a proof property or is removed; black/pylint/xdist; coverage > 85 %; 60 s CPU
per script, use `--budget`/`--resume`), commits to a branch, opens a PR, watches
CI, merges on green, and ends with a "what remains" list that becomes this
plan's backlog. The campaign (§8 #1) runs after stage 4.
