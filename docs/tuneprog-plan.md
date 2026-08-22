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
| certified | Automatas (defMON, both SID models), Commando songs 1–2 (Hubbard), Ghouls'n'Ghosts (Follin, 32 subtunes + `--songs all`), GoatTracker 2 ×2, SID Wizard ×2, JCH NewPlayer V20 ×2, the first two of the installed-handler family (Becher's *Jodler*, Baumrucker's *Playful Professor*), the first two of the dead-NMI family (Andrew Rodger's *Alien_3*, Goto80's *Jazzpjazz*, both at a 30 s horizon) and the first two of the patched-dispatch families (NecroPolo's *Experiment Zeta* on the Virtuoso engine, complete on a 5,184-tick period; Ben Daglish's *Deflektor* at 30 s) — 50 certificates, 757,554 certified ticks, 0 divergences, 0 envelope traps; 42 complete via periodicity, the `--songs all` program complete on 31 of its 32 subtunes |
| certify at 15 s, not yet run to length | Blackbird (Quintessence), Galway (Comic Bakery), Walker (Chameleon) |
| ~~refused by design~~ | *was* JCH Easy Does It (NMI sample mixer = second interrupt); **certified 2026-08-22** ([prototype-nmi.md](prototype-nmi.md)), and 134 of the 195 tunes of its class with it. What refuses now is only a CIA #2 source with no schedule (TOD alarm, serial, FLAG, a CNT timer: 6 tunes of 7,023) |
| code | `deity_informant/tuneprog/`, 56 modules, 16,328 lines, none over 500 (`pipeline.py` 510 excepted); 649 hermetic + 62 HVSC + 10 oracle tests, 95 % coverage; `tools/tuneprog_certify.py`, `tools/tuneprog_recert.py` (51/51 reproduce), `tools/tuneprog_period.py`, `tools/tuneprog_ghidra.py` |
| at survey scale | the whole pipeline over the stratified 7,023-tune sample ([survey-tuneprog.md](survey-tuneprog.md), §8 item 1): **91.2 % of HVSC by weight certifies at a 30 s horizon** (76.7 % raw), 2.5 % diverges, 6.2 % is refused with a diagnosis, 0.26 % crashes; the `--until-period` pass over 1,338 of them leaves **99.4 % of certified programs complete by weight**. 58 CPU-hours at the tracer that campaign ran on; **the tracer is now 3.0-3.5× faster** (§8 item 7, #271) and a certified tune's trace CPU falls 2.89× on a 200-tune re-run of the same sample — the part of that 58 that does not move is entry discovery, which the sweep bills to the trace stage |
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
| the design's cost model | trace 277 k instructions/s, verify 10–16 k calls/s; the whole HVSC at song length is ≈ 300 CPU-hours — a few hours on this machine, no fast tracer needed yet. **Corrected by the campaign** ([survey-tuneprog.md](survey-tuneprog.md) §11): verification matches the model (13,518 calls/s amortised) but tracing runs at 96 k instructions/s — 2.9× off, the 277 k was the prototype VM — putting the catalogue at ≈ 131 CPU-h at 30 s and ≈ 529 at song length; the fast tracer **is** warranted. **Now met** (§8 item 7, #271): the production tracer runs at **480-580 k instructions/s**, 3.0-3.5× its old rate and 1.7-2.1× the prototype VM the 277 k was taken on, with the `Trace` byte-identical over all 82 traces the 50 certificates hold |
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
| opcode cells whose alternative is not `RTS` in the SLEIGH export (overlay or paired constructor). **Sized by the campaign** ([survey-tuneprog.md](survey-tuneprog.md) §10): 263 of 7,023 tunes have an SMC opcode cell at all (3.6 % weighted) and **198 of them have one whose alternatives exclude `RTS`** (2.8 % raw, 3.4 % weighted) -- three quarters of the class, so the `RTS`-only overlay covers the minority, not the bulk | Ghidra export | baseline | ghidra/6510 |
| family name dictionaries by structural alignment | all | naming | recover |
| ~~second interrupt schedule (NMI + IRQ sharing regions)~~ *done (Q8, 2026-08-22)*: a CIA #2 NMI is the schedule's second entry and a second procedure ([prototype-nmi.md](prototype-nmi.md)). `cia.py` is the chip (Timer B on cycles or on Timer A, one-shot, the latched flags and the edge-triggered line, a latch write that lands at the *pending* underflow rather than reloading); `nmi.py` says when the line asserts and which vector carries it; the tracer takes the NMI at the instruction boundary it is due at -- inside a tick and in the host's idle time -- and records the preemption schedule as data; S8 replays it at **store** granularity, which is exact for everything either entry reads of the other, pinning only the second entry's live-in registers. Measured: **181 tunes of 7,023 (1.3 % weighted) have both entries**, in four classes, and the two entries **never share code** (0 of 181), which is what lets the pinned input stream partition by entry. 134 of the class's 195 certify at 30 s. Zero cost on a one-entry program: its emitted text is byte-identical, it runs on the plain `Machine`, recert 51/51 | design §10, JCH | scope | machine/trace/verify |
| the NMI instant is up to ~80 cycles early: the tracer has no VIC-DMA model, so a CPU a badline stalled takes the NMI later than we do. Measured against `sidplayfp`'s own `since_nmi` column on *Easy Does It*: **+17 to +25 cycles** typically, 936 of 21,149 within 8. Everything the play routine writes is 0 frames differing over 1,500 frames on three tunes of three classes; `$D418`, which only the mixer writes, differs in 10 / 21 / 54 % of frames by one sample step, because a few tens of cycles decide which nibble is a frame's last write | Q8 | oracle | trace, a raster model |
| the 43 tunes whose CIA #2 NMI is their *only* schedule (`play == 0`, no IRQ vector the port dispatches) are a **single**-entry program whose cadence is a CIA #2 timer, and `find_entries` refuses them `no entry` before the chip is consulted. 0.1 % weighted | Q8 | scope | machine |
| a moving NMI vector becomes one entry per address it took, which is right for a two-phase handler chain (103 tunes met one) and would be wrong for a genuinely computed vector: nothing bounds the entry count | Q8 | scope | trace, cfg |
| `--seconds` computes its tick target from the cadence `find_entries` guessed, not the one `Tracer._settle` leaves, so a tune whose period settles later certifies past the horizon asked for (*Easy Does It*: 1,799 ticks = 35.9 s for `--seconds 30`) | Q8 | tool | pipeline |
| the 41 divergences and 11 `JAM` crashes left in the NMI class at 30 s: `state hash` 13, `entry register` 9, `sid` 6, `io` 6, `input mismatch` 3, and 24 of the 41 are past tick 5 -- the schedule drifting from the machine's, not a first-tick modelling gap | Q8 | correctness | trace, verify |
| ~~the NMI refusal was sized on evidence, not on a schedule~~ *done (Q5, 2026-08-22).* `find_entries` refused any write to the CIA #2 Timer-A latch or the NMI vector, neither of which makes an NMI possible. The rule is the chip's: a CIA #2 source can fire iff the ICR (`$DD0D`) has been written with bit 7 and one of bits 0-4 — a mask the chip *accumulates*, so the last write does not give it — and, for a timer source, iff that timer is started (`$DD0E`/`$DD0F` bit 0); CIA #2's line is the NMI, so an armed source refuses whatever vector carries it and a vector over no armed source is dead, as `vector_gate` already treats a dead `$FFFE` write. `CIA` carries the mask and Timer B's start bit, `nmi_gate` is the one predicate, applied to the init trace's last writes in `find_entries` (the cheap refusal) and then exactly at the end of init and at **every tick**, beside `port moved`. **Measured over the campaign's 547 refusals** ([survey-tuneprog.md](survey-tuneprog.md) §5b): 311 really are armed (1.8 % weighted), **81 are dead evidence (0.8 % weighted, against §9.2's ≈ 1 % estimate)** and 154 are undecided because init never returns (one more faulted the tracer). Re-run at 30 s the 547 give 3 certified, 1 diverged, 1 crashed, 542 refused: the row falls to 273 + 9 `nmi armed in play` (2.2 % weighted, not 3.0 %) and the released tunes land almost entirely in the `no entry` / `vector banked out` refusals it had been shadowing. Two of the three are certificates (`rodger-alien3`, `goto80-jazzpjazz`). **What remains:** the 311 armed tunes are the population for the true two-schedule prototype (§8 item 3) — nothing here models an NMI, it only refuses one; the 154 whose init never returns need `init runaway` to become a diagnosis rather than a budget; the init trace is a *second* emulation, so the cheap refusal can in principle over-refuse where the two disagree (measured 0 of 547, and 9 tunes do show an ICR write the tracer's own machine never makes) | survey §5, Q5 | correctness, scope | machine, trace |
| ~~a CIA #2 latch was being handed back as the play cadence~~ *done (Q5)*: `playroutine_cadence` falls through from CIA #1 to CIA #2 and treats an unwritten ICR as the armed KERNAL default, which is right for CIA #1 and wrong for CIA #2, so a dead `$DD04` latch became the tick period. `_cadence` now takes a CIA period only when it is CIA #1's. Nothing in this class was ever traced before, which is why it had never shown. **What remains:** the same fall-through is still in `pysidtracker`, and `_cia_armed`'s KERNAL-default rule is CIA #1's alone | Q5 | correctness | machine |
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
| `grid.sidtrace_clock` takes one period as the median gap between a CSV's interrupt raises and refuses a set whose gaps are not whole multiples of it (within 1 %), which is right for a fixed clock and wrong for a *reprogrammed* one (*the second half of this row is done (Q8): it also refused a CSV in which any write is more than one period after its raise, which is exactly a second entry writing while the first is idle -- the period check stays, that clause is gone, and three tunes of the NMI class are still refused by the period check*): a CIA tempo-change player that rewrites the timer latch mid-song now refuses outright where the old median framed it badly. The sweep will meet it and wants a per-segment clock -- the raises segment at each rewrite -- rather than one median over the run. The same guard refuses a whole CSV for one stray row on the other source, which is the honest reading of "one source, covering every row that carries any": if a real sweep CSV mixes occasionally, the fix is to count each row from the source it carries and check the two clocks agree, not to relax the guard (the check is per chip, so a multi-chip CSV whose chips differ is already fine) | Q3 | oracle | grid |
| a certificate records what it *verified*, not what the program was built from: a horizon lands on a chunk boundary, so the trace can hold up to `--chunk` ticks more per subtune (`ghouls-songs-all`: built from 220,049, verified 111,763). Sound as it stands -- past a witness those ticks replay states already seen, so they carry no site, edge or access the certified prefix lacks -- and stated in `_certified` and the schema, but the *document* says it only in prose. A `traced_calls` cost field would say it in data and move all 44 certificates | Q3 | certificate | emit, pipeline |
| the tracer counts CPU cycles where the sampler's clock also spends VIC DMA: the play entry is a constant 57-60 cycles later in `sidplayfp`'s frame than in ours, and inside one ramped tick of the Knob (its last write 9,312 cycles in) that offset drifts a further +533 -- one badline in eight raster lines. So a write's *offset inside* its frame is not certified -- only which frame it lands in, and the order. Free today (the grid is framed by the interrupt on both sides, and both agree write for write) and worth a raster model only if a comparison ever needs sub-frame time | Q3 | model | tracevm, machine |
| a write to `$D000-$DFFF` with I/O mapped also writes the RAM under it, in the tracer and in the interpreter alike (`tracevm.write`, `interp.iostore`), where the hardware writes only the chip. Invisible until #251 made that RAM observable storage, and still unobservable in every exemplar (both sides agree, so no certificate can see it; the two new tunes match the oracle write for write). The honest model is two planes -- the chip and the RAM beneath -- which the address-keyed footprint, region relation and state hash all assume away, so it waits for a tune that discriminates | P-JCH (#251) review | model | tracevm, interp |
| a `cia_timer` cadence carries no video standard, so `verify.subtune` and `pipeline._target` resolve the clock by `"ntsc" in source` and put an NTSC CIA-timed tune's `seconds` in the PAL clock. `baumrucker-professor` is one (header clock NTSC, own latch 9,828): its 1,503 ticks read 14.99 s where the machine says 14.44. Self-consistent -- the horizon that traced it used the same rule -- and invisible until the speed-bit work made the standard part of the source name (`pal_host_cia`/`ntsc_host_cia`). The fix is to carry the standard on the entry rather than in a substring, which moves the `source` field of all four `cia_timer` certificates and `professor`'s `seconds` | speed flag (2026-08-21) | model | machine, verify |
| Ghidra function bodies vs clone-per-entry (`ghidra_partial` rows) | Ghidra export | oracle | ghidra_compare |
| the sweep's `cpu_trace` column bills S0 entry discovery to S1, and on a refused tune it is *all* of it. Measured on the first 200 tunes of the seed-1 sample (§5b Q7): a certified tune's trace CPU is 8.6 % `machine._traced` (`pysidtracker`'s `playroutine_cadence` + `trace_init`, 0.08 s median) and 91.4 % tracing, which is why it fell 2.89× when the tracer got 3.06× faster; a refused tune's is ~100 % `_traced` and did not move at all (775.1 → 765.4 s over 60 tunes). Forty-six of those sixty refuse **`no entry`** and spend **14.6 CPU-seconds each** deciding it -- a whole init trace on someone else's tracer to answer a question about vectors. So survey §12's "tracing is 78 % of pass 1's CPU" is 78 % of S0+S1, and the campaign's 30 s projection cannot be corrected exactly without the split. Two rows: give `tuneprog_sweep.py` a `cpu_entry` column, and find out why `no entry` costs 14.6 s | §5b Q7 | cost accounting | survey/tuneprog_sweep, machine |
| ~~numba tracer/executor if the campaign needs it~~ *refuted as stated (2026-08-22, §5b Q7)*: the 2.9× was never the 6510 semantics. The profile put the compiled P-Code closure at 5-6 % of the old tracer's self time and the bookkeeping around it at the rest; after the site-keyed rewrite the closure is 16-24 % and everything left is Python the profile can name. A numba core would have to reproduce the per-op access sets, index domains, edge counts, register masks and input pinning bit for bit to keep the certificate — that is the whole tracer, not its inner loop — and design §5 S1's three-way differential on top. **Reopen only on a measurement that puts the core above 60 % of trace CPU**; it is not | design §11, §8 item 7 | performance | trace, emit |
| ~~stack elimination in S4 (§7) — gate item~~ *done (#237)* | user gate | core | frames, stack |
| ~~an `RTI` entry tune is residual: the status byte the machine pushed at the interrupt is a frame the tick never wrote, so its stack stays~~ *done (Q4)*: the frame the machine pushed is the entry's **contract**, not storage. `frames.contract` gives an `irq` tick one pseudo-push at slot `SP+1` whose value is the entry flags packed (`lower.status_expr`, the inverse of `pop_status`); the terminating `RTI`'s status load must-reaches it like any other slot, so the group is a value, the page access goes, and the tick eliminates. The interrupt disable the machine sets at entry moves out of `verify._enter` into the tick's own first statement (`build._irq_entry`), which is what makes the entry flags *be* the pushed byte. Exactness is the existing must-def analysis, not a new pattern: slots `SP+2`/`SP+3` (the pushed return address) name no value, so a tick that reads them is residual though it puts the frame back balanced, and a `TSX`-relative read is residual because it is no slot at all; a tick some other procedure also calls gets no contract, since a `JSR` puts a return-address byte where the interrupt put the status. Round trip cost: the pack and its six bit extractions stay in the certified IR and the presentation folds them (`idioms.bit`), so the printed tick is unchanged. Differentials in the #237 style (both executors, stack kept and eliminated) plus a unit check that slots 0, 2 and 3 stay opaque; recert 44/44, no field moved (no certificate is an `irq` entry) | stack elimination (#237) | core | build, frames, stack, verify |
| a residual stack is whole-program: one unplaceable read keeps `SP` in every procedure, where an interprocedural frame layout would localise it. **Sized by the campaign**: 826 of 5,783 built programs are residual (14.3 % raw, **4.4 % of HVSC by weight**); the unplaceable read is in `tick` itself in 512 of them (62 %), in `init` in 219 (27 %) and in a helper in 365 (44 %), and 819 of 826 have no computable depth at all (`Frame.events is None`). So the win is inside the tick, not between procedures, and 'how deep' is not the question -- 'whose frame' is | stack elimination (#237) | core, precision | frames, stack |
| ~~`--until-period` stops at the earliest repeat of either footprint, so a *residual* tune may stop before the page-inclusive repeat it must certify on~~ *done (Q4)*: the trace stops on the witness the program's stack status allows. `Tracer.witness`/`Trace.witness` take `free`; S4's verdict is recorded in `state.json` (`"stack"`), and `pipeline._horizon_stage` sends a *residual* program whose trace stopped on the page-free witness alone back to S1 to trace on -- once, on the verdict *becoming* residual -- with the driver looping trace/front rather than needing a second invocation. Where both repeats fall inside one chunk no second trace is needed, but the horizon still has to move, so `stage_verify` derives it from `Trace.witness(free)` exactly as `verify_all` already did: one rule, both paths, every chunk size (a review of the first cut caught this -- the reproducer passes at `--chunk 8` by luck and fails at the default 4,000, so the test is parametrised over both). Under `--songs all` the subtunes that stopped on a free-only period are the ones re-traced, their record carrying `full` for exactly that test. The #239 reproducer (stack scratch, page-free period 1, page-inclusive period 256) under `--until-period` now certifies 257 ticks `complete: true` at period 256 where it used to stop at tick 2 and report `complete: false` | stack footprint (#239) | horizon policy | pipeline, trace |
| ~~an installed handler that chains to the KERNAL IRQ epilogue never reaches its `RTI` under the tracer~~ *done (2026-08-21)*: the entry's **vector** decides its frame. `Entry` carries `kernal` (the installed vector is CINV, so the KERNAL dispatches it), `machine.entry_frame` is the one statement of what the machine pushed -- the status byte alone on a raw vector, plus the A, X and Y that `$FF48` saves on a CINV one -- and the tracer, `verify._enter` and `frames.contract` all read it, so slots `SP+1..4` are the entry Y, X, A and status and `$EA31`/`$EA81` pop exactly the machine's own three bytes. Exactness is Q4's must-def discipline unchanged: a `TSX`-relative read of the same status is no slot and stays residual, the pushed return address (now `SP+5`/`SP+6`) names nothing, and a raw-vector entry keeps Q4's one-slot shape. Measured over the same 486 `play == 0` tunes of HVSC `MUSICIANS/A`-`C` (37 PSID, all 37 CINV): **37/37 build, 37/37 reach their `RTI` and verify** where all 23 that built diverged at tick 0 on `unreached` before, 34/37 certify at a 15 s horizon with **0 divergences and 0 envelope traps** (20 stack-eliminated, 14 residual), and the three that do not are an S6 obstruction with its own row below. Two are committed as evidence: `becher-jodler` (707 ticks, period 700, **complete**, 0 pinned inputs, stack eliminated) and `baumrucker-professor` (CIA cadence 9,829 cycles, 1,503 ticks, 0 pinned inputs, stack eliminated). Differentials in the #237/#259 style (both executors, stack kept and eliminated): a CINV handler chaining to `$EA81` eliminates, one that restores only A eliminates too (each saved byte is its own slot), one reaching its frame through `TSX` is residual, and the same handler declared raw does *not* reach its `RTI`. Recert 46/46, no field of the 44 moved -- `kernal` is in the entry dict only where there is a vector | Q4 | core | machine, trace, verify, frames |
| ~~14 of the family crash the generated code on a `Call` emitted with no arguments~~ *done (2026-08-21)*: the IR call graph can have **cycles**, and `build._wire` assumed it could not. `cfg._no_recursion` refuses a `JSR` cycle but deliberately lets a *tail* call through (it grows no frame) -- and a tail edge is still a `Call` in the IR, so a player whose routine jumps back to a `JSR`ed label is self-recursive there. One post-order pass then wires that site against params the same pass has not computed yet: `s.args = ()` where the callee ends up taking eight, and `p_p_CD45() missing 8 required positional arguments` at tick 0. Both sets only grow (own definitions, plus what the callees add), so the fix is the fixpoint they always were: a worklist in post-order rank, a caller re-queued when its callee moves, one pass on an acyclic graph. Split out as `wire.py` (`build.py` was over 500 lines). The 14 crashes go to 0 -- 10 Android tunes and the 4 Crowther examples, whose `$CD45` tail-calls itself -- and a hermetic reproducer (`JSR rec` / `rec: ... JMP rec`) raises the same `TypeError` under the old single pass | #259 screening | core | build, wire |
| ~~the `kernal` decision was written-vector precedence, not evidence~~ *done (2026-08-21, review of #261)*: which vector the machine dispatches through is the **6510 port's** word, not the tune's. With HIRAM set the CPU takes its vector from the KERNAL's own `$FFFE`, so a write to `$FFFE` went to the RAM under the ROM and CINV is live; with HIRAM clear that RAM *is* the vector and no `$FF48` prologue runs. `machine.vector_gate` decides it (`kernal_mapped` = the port's HIRAM line, factored out of `port_bank`), so a tune that armed both is not ambiguous, and it **refuses** (`vector banked out`) where the port forbids the only dispatch the tune armed. `find_entries` runs it on the pre-init image, `Tracer.run_init` re-runs it once init has had the port -- that verdict is the one the ticks and the certificate carry -- and every tick re-checks HIRAM, since the frame is the tick's contract (`port moved`). The 37 re-screened: **37/37 trace and verify, 34/37 certify, 0 divergences, 0 envelope traps**, every stable certificate field identical, all 37 still CINV (the population was clean-CINV, which is why the precedence rule had not been caught). Hermetic: both-written decided by the port either way, `$FFFE` with the KERNAL mapped refused, CINV with it banked out refused, and a raw entry's frame `{1: P}` | #261 review | core | machine, trace |
| ~~the CINV frame convention had no oracle guard~~ *done (2026-08-21, review of #261)*: `test_oracle.py` runs lft's `A_Mind_Is_Born.sid` -- a CINV entry at `$0031` that chains to `$EA31` -- through the **tuneprog tracer** and compares its interrupt-framed grid with `sidplayfp`: **0 of 3,000 frames differ**. The tune reaches its `RTI` only because the tracer pushes the three bytes `$FF48` saves, so the machine-model claim is checked the way #251 established for the port | #261 review | oracle | trace, testing |
| ~~the PSID **speed flag** is not in the cadence~~ *done (2026-08-21)*: a tune that programs no timer of its own is driven by **the host**, and which host it is the container says. `machine._cadence` keeps the tune's own armed latch first (`cia_timer`, unchanged); where there is none, a PSID whose `speed` bit is set for *that subtune* and every RSID that armed no raster compare tick on CIA #1 Timer-A at the latch the KERNAL and `psiddrv` leave -- `$4025` PAL, `$4295` NTSC, so `latch + 1` = **16,422** and **17,046** cycles (source `pal_host_cia`/`ntsc_host_cia`) -- and a PSID with the bit clear keeps the driver's raster frame. The word is a bitfield, so the cadence is now per subtune: `find_entries(..., song=)` takes it (`c64.speed_cia`, bit *n*, subtunes past the 32nd sharing bit 31) and `--songs all` **refuses** a tune whose subtunes disagree, one merged trace being one schedule. Both host periods are measured off the oracle's own raises, not the header: `Jodler` writes every 7th tick and its CSV raises are 114,954 apart = 7 x 16,422; the same tune with NTSC clock bits renders 119,322 = 7 x 17,046. Four oracle-marked cases, one per class, each checked twice -- the cadence must divide every gap between raises, and framing the oracle's writes *and* the trace's on it must agree frame for frame (a wrong cadence fails both: 19,656 does not divide 114,954, and at 19,656 Jodler differs on 741 of 796 frames): `commando` 19,656 raster, `automatas` 2,457 (its own latch outranking a set speed bit), `becher-jodler` 16,422 speed bit, `A_Mind_Is_Born` 16,422 RSID. **The RSID class was the same defect and is fixed here too**: lft's tune was ticking at 19,656 while `sidplayfp` raised every 16,422, and the existing CINV guard passed anyway because it framed each grid by *its own* clock, which makes a frame-for-frame comparison cadence-blind -- the raises are what carry the period. `becher-jodler` moves in exactly four fields (`entry.cycles_per_tick`/`source` and the subtune's `cycles_per_tick` and `seconds`, 14.1 -> 11.78); the row predicted its **ticks and period would move and they do not** (707 and 700 unchanged), the tune's state machine being cadence-free. Recert 46/46, the 44 `sub` documents untouched (only `Automatas` of the 13 mapped tunes carries a set bit, and it latches its own timer) | #261 review | model | machine, c64, pipeline |
| `grid.sidtrace_clock` takes the period from the **median gap between raises that carried a write**, which is the burst period of a sparse writer, not the frame. Measured on two of the family: Cox's *Caverns of Eriban* writes every 6th or 3rd frame, so the median is 117,936 with slip 58,968 and the guard refuses; Becher's *Jodler* writes every 7th, so the median is a clean 7 x 16,422 and the derived clock is 7 frames wide. A gcd over the gaps is the derivable lower bound and still overshoots where the spacing never varies; the honest fix is to frame the CSV by the *entry's* cadence and the CSV's own first raise, which needs the origin question (a sparse writer's first raise is not frame 0) settled first. It is why the guard above uses a tune that writes every frame | #261 review | oracle | grid |
| `fold.outline` can leave an edge to a block it deleted: S6 then dies in `graph.preds_of` with a `KeyError` on the label. **Sized by the campaign: 32 of 7,023 tunes (0.5 % raw, 0.1 % weighted), every one of them already certified** -- presentation only at scale as well. 3 of the 37 (Abbing's *Belagerung 2*, Beben's *Come What May* and *Tiger's Eye*) at a 15 s horizon; all three verify and their S4 program is clean, so it is presentation only. **Diagnosed**: `fold._emit` makes one candidate out of a *run* of consecutive single-entry/single-exit atoms and takes the union of their blocks, but `sese` was checked per atom -- where each atom's head is exempt from the predecessor test, being the head. Joined, the inner atom's head is no longer a head, and nothing rechecks it: *Belagerung 2*'s `L8A30_EA` is the join of an `If` in `L8A02_AE` and the fall-through through `L8A0B_A0$r1`, so the pair `{L8A0B_A0$r1, L8A30_EA}` is emitted with `sese` **False** and `_extract` deletes a block the `If` still names. The fix is one condition in the accumulation loop -- an atom joins the run only when every predecessor of its head is already inside it, else the run ends and a new one starts -- and it may move printed text on the 44, which is why it is a row and not this stage | this stage | presentation | fold |
| the tracer hands a CINV tick the registers the previous tick left, where the real `$FF48` also leaves A = 0, X = SP and Z set (`TSX; LDA $0104,X; AND #$10` -- the BRK test). Measured over the 37: 31 read no entry register at all, 2 read A and see the same 0, and 4 (Boray) read A/X/Y live-in and would see other bytes on hardware. Not a certificate question (the tracer and the verifier agree, and the read is a pinned `entry_reg` input either way) but a model one, and `X = SP` cannot simply be modelled: an `SP` value that survives makes the whole program residual (`stack._holds_sp`). Decide it against the `sidplayfp` grid on one Boray tune | this stage | model | machine, trace |
| **region typing by accessor-shape partition** (P-FLOOR's one mechanism, promoted to a work row): partition a region's accessors by shape — a k-byte span at index `0..k-1` is an array, a `(b, b+1)` pair at one index is a u16 row, a constant address a scalar — split the region where the partitions disagree about a byte and leave the overrunning accessor a bound assertion instead of collapsing to the coarsest kind; a byte no store writes is `const` beside a `state` neighbour. On Commando this alone is typings T1+T2+T3 of the hand-factored form (252 → 115 printed lines measured); it also splits the three-extent pattern block. Presentation-only. Companion one-liner: `voice.freq` (`$551D`/`$551A`) refuses to fold inside `word._match` though `_pairs` accepts and `_crosses` is False at all three sites — instrumented, undiagnosed, worth 7 lines | P-FLOOR ([prototype-commando-floor.md](prototype-commando-floor.md)) | presentation | regions/views, word |
| ~~**`trap switch`: the emitted program reaches a dispatch value the trace never produced.** The campaign's largest failure class -- **189 of 7,023 tunes (2.7 % raw, 0.5 % weighted)**~~ *done (Q6, 2026-08-22). It was **three** mechanisms, and none of them is `emit._term`: the trap was honest every time and the scrutinee or the case set was wrong.* **(a) A `JMP (ind)` whose own operand bytes the program patches.** `cfg._expr` tested `ls.ctrl_cell` before the `jmpind` form, so the switch dispatched on the **pointer** the operand holds while its cases were the observed **targets** -- one indirection apart, so the trap fired on the first dispatch. The rule: a `JMP (ind)` dispatches on the word its pointer holds, whether the pointer is the instruction's constant operand or the byte pair the program writes into it (`cfg._expr`, `lower._indirect`, with the 6502's page wrap and the span of the pointers the trace ran as the envelope). Virtuoso, Element114Studio, Fred Gray, Galway and Tiny/Sound Images are all this. **(b) A patched branch offset of zero.** `cfg._node` took the taken set to be *every observed successor but the address after the instruction* -- which is exactly the target a zero offset names, so a `LDA #$00; BEQ` dispatch (Ben Daglish's 4-way `STA br+1`) lost that arm and the program trapped on the one value it computes most. The rule: a computed branch's cases are the targets the **offset bytes the trace ran at that site** name, intersected with the successors it reached (`cfg._rel_targets`); no successor address can decide the direction when both arms land together, and the offset byte can. **(c) The copy index one past the last copy.** `build._next_copy` stepped `v` before deciding whether the edge advances the run, so the arm that *leaves* the family carried `v = k`; where the exit re-enters code the last copy holds (`Bitfrost.sid`'s `$10EF`, the class's own first-divergence example) the merged body then dispatched on an index no copy has. The rule: only the arm that advances takes the step. **Measured**, the same 189 at 30 s, before on `main` / after: **188 `trap switch` + 1 `trap untaken` → 177 certified**, 4 `trap switch`, 4 `io`, 2 `input exhausted`, 2 wall timeouts (the two Virtuoso tunes now trace past the divergence). Whole families: Ben Daglish/Gremlin 25/25, Element114Studio 25/25, Fred Gray 15/15, Virtuoso 27/29, Tiny/Sound Images 11/12, Galway 6/7. **The association the campaign measured is refuted as a cause**: what the class is made of is self-modification of *control*, not of code in general, and copy folding is a bystander. Classified on `main` at 30 s by the instruction that dispatches at the first divergence: **`JMP (ind)` 110, a patched branch 62, an unmatched `RTS`/`RTI` 4**, 13 unresolved by the reconstruction; reading the emitted scrutinee instead gives 98 / 77 / 4 with **9 on the copy index** and 1 `untaken`. The two readings agree on the shape: the two control mechanisms are about 170 of the 189, the copy index is under ten. Evidence certificates: `necropolo-experiment-zeta` (Virtuoso, complete, period 5,184) and `daglish-deflektor` (Ben Daglish/Gremlin, 30 s horizon). **What it costs the certified product** (§9's rule): the step is a block of its own, and it survives `ssa.merge_chains` only where the family header has more than one entry -- **3 of the 48**, `ghouls-song28`/`30`/`32`, `cost.ir_blocks` +1 each and nothing else; the other 45 reproduce field for field against `main`, `ir_statements` included. The emitted `tuneprog.py` is **not** byte-identical: where the block survives (`ghouls-song28`) it is 1,601 → 1,603 lines with 242 differing -- one `if lbl <= n:` chunk, every `lbl` number after it, and the step's temporary renamed after its own block -- while the *printed* program is byte-identical; where the step merges away (*Automatas* at 30 s) the emitted text is the same length with 72 lines differing, all of them that rename, and the printed program gains 2 bare source-comment lines. **What remains** as rows below: the 4 residual `trap switch` are all one shape (an unmatched `RTS` return), and the Daglish family does not close on a period | campaign ([survey-tuneprog.md](survey-tuneprog.md) §4) | correctness | cfg, lower, build |
| **`trap switch` over an unmatched return (the RTS trick): 4 tunes of the 189.** `cfg` makes an unmatched `rts`/`rti` a switch over `rets["loose"]`, the return addresses the trace popped there; the program pops one it did not -- `Whittaker/Exterminator` and `Clarke/Ocean_Loader_3` in init, `Haard/Blood_n_Guts` at tick 0 (popping `$0001`, so the frame itself is wrong) and `Parker_Bros/Gyruss` at tick 356. All four are `stack: residual` programs, so this is the stack model's boundary, not the dispatch's | Q6 | correctness | stack, cfg, frames |
| **a patched `JMP (ind)` gets no static table closure.** `jumptab._cell` recognises a computed target as `Load(w=2, Const)` — the operand *is* the target for a patched `JMP $xxxx`/`JSR`, and now correctly is not for `JMP (ind)`, whose term is a load through a load. So the arms of a Virtuoso/Galway pointer table that the trace never dispatched stay unlisted rather than `trap 'unverified'`. Nothing is lost that was ever right, and the argument is short: `_cell` matched the operand cell, which for a `JMP (ind)` is the *pointer*, and `_arms` keyed the entries it added by that pointer -- consistent only with the scrutinee that was wrong, which is why a patched `JMP (ind)` trapped on `main` at its first execution and no certified tune has such a site; no certificate's `ir_statements`, `ir_blocks` or `copies.unverified` falls here. The closure is worth having on its own terms: enumerate the *pointer* table and dereference each entry | Q6 | closure | jumptab |
| **the Ben Daglish/Gremlin family certifies at 30 s and never closes on a period.** All 12 sampled at `--until-period` (80,000-tick cap) diverge `trap unreached X0002` at the tick the trace stopped on, every one of them a `stack: residual` program -- 1,584 ticks (`Blasteroids`) to 10,177 (`Wizard_Warz`), three at tick -1. That is the campaign's "31 of 1,338 certified at 30 s and not at period scale" class, now with a whole family behind it and one shared symptom | Q6 | correctness | stack, verify, period |
| **the `io` write list differs, 73 tunes (1.0 % raw, 0.2 % weighted), 41 of them in init.** The program's VIC/CIA writes are not the trace's; the SID write list never differs anywhere in the sample. Concentrated in Geir_Tjelta/SIDSys18.6 (17), Heathcliff/DigitalArts (11) and Novaload (11) | campaign §4 | correctness | build, verify |
| ~~**the closure boundary shows up as a divergence: `trap unverified` 47 + `trap untaken` 31 = 78 tunes (1.1 %).** An arm lifted from a sibling copy, or a branch direction nothing executed, that the program then reaches while replaying its own trace. SidFactory_II/Laxity is 23 of the 47~~ *closed by Q6 without being worked on, and the diagnosis was wrong: the closure was never the boundary.* Re-run at 30 s, before on `main` / after: **47 + 31 → 78 certified, 0 diverged.** Both classes were the same two control mechanisms seen from the other side -- a patched `JMP (ind)` whose *pointer* value happened to match a table entry `jumptab` had closed as an `unverified` arm, and a zero branch offset whose arm `enumerate_targets` closed for the same reason (the hermetic `BGROW` fixture in `test_jumptab.py` shows exactly that: the zero arm was closure, and is now a verified case). SidFactory_II/Laxity, the campaign's largest divergence-only family, certifies whole | campaign §4, Q6 | correctness | cfg, lower, jumptab |
| **volatile-input replay: `trap input exhausted` 26 + `input mismatch` 12 = 38 tunes (0.5 %).** The program consumes pinned inputs in a different order or number than the trace recorded. Novaload and Heathcliff/DigitalArts lead | campaign §4 | correctness | verify, interp |
| **`RecursionError` out of the emitted program, 7 tunes.** A tail call `cfg._no_recursion` lets through (it grows no machine frame) is a `Call` in the IR and grows a *Python* frame per edge, so a self-recursive tail loop blows the interpreter stack; 2 of the 7 surface inside `interp.ioload` instead. The #259 fixpoint fixed the argument wiring for these, not the depth | campaign §13 | crash | build, wire, emit, interp |
| **`RuntimeError: JAM at $XXXX` escapes `vm.py:step` as a crash, 2 tunes.** Every other unsupported construct is a diagnosed `Refusal`; a JAM opcode reached while tracing is not, so it reads as a bug in us instead of a refusal of the tune. Classification only | campaign §13 | crash, honesty | trace, machine |
| **`KeyError` in `ssa._frontiers` on a block label, 1 tune** (*Green_Tea.sid*) | campaign §13 | crash | ssa |
| **`KeyError: 'expr'` in `lower.ctrl_expr`, 1 tune** (*Examples.sid*) | campaign §13 | crash | lower |
| **`TrapError` out of `ir.evalbin` during S5/S6, 2 tunes** -- the certificate stands, presentation dies evaluating an expression that traps | campaign §13 | crash, presentation | ir, views |

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
| ~~**Q4 = P3 stack residual policy**~~ *done (2026-08-21)*: the RTI entry frame is the tick's contract (`frames.contract`; an `irq` tick with a balanced pair inside eliminates, one that reads the pushed return address or reaches the status through `TSX` stays residual) · `--until-period` traces on to the page-inclusive witness once S4 calls a program residual (the #239 reproducer: `complete: false` at tick 2 -> **`complete: true`, 257 ticks, period 256**). Recert **44/44 reproduced, 0 mismatched**, no field moved -- none of the 44 is an `irq` entry, and the row above records why no HVSC tune could be added as evidence: the KERNAL-epilogue entry convention, not the contract, is what stops the family | small-medium | done; frame localisation stays deferred |
| ~~**P-EQSAT prototype**~~ *done (2026-08-21); the prints qualify, the cost does not.* `--eqsat` (default off) routes the S6 expression layer through an `egglog` e-graph. `eqrules.py` is one term language and **108 rules** (84 rewrites, 24 rules, from ~150 lines of generator): `idioms.fold`'s identities run to a fixpoint instead of one pass, the boolean shapes, an interval analysis whose lattice **is** the e-class merge (`lo` merges by max, `hi` by min, so a union intersects two proofs), the masks and comparisons that analysis decides, the V flag of a subtract, and a branch whose arms differ by one. `ranges.py` seeds it with what the certified IR proves about a cell -- the image joined with every store's value over its own asserted envelope, each store carrying its **own** procedure's definitions, iterated *down* from the whole byte so every intermediate over-approximates (3-4 rounds on all 42). `eqsat.py` lowers a procedure, unions a name with its lone definition (that is `texture.propagate`), lowers a qualifying if-diamond to a `select`, and extracts by `(marker nodes, printed tokens, structural key)` -- a total order, so no hash decides anything. **Measured** on the 42 certificates whose S4 is cached, default vs `--eqsat` on the same input: **no metric worse on any tune**, 6 byte-identical, the rest strictly smaller -- lines 30,821 -> 30,690, printed tokens 89,099 -> 87,755 (-1.5 %), blocks 9,976 -> 9,792, statements 12,005 -> 11,974. With the flag off all 42 prints are byte-identical to `main` and `present` leaves the certified S4 JSON unchanged either way. Determinism: two runs at different `PYTHONHASHSEED`, 42/42 byte-identical. **The branch-carried borrow fires**, three sites: `ghouls-song21`'s `$6650` (Follin's frequency subtract, 6 lines -> `x12 = (b0021[89] - (b0021[86] < b0021[107]))`) and both SW `init` sign extensions. **`if (tempo & $80)` still refuses, now with the number**: the gated rule exists (equal operand signs => V is 0; opposite => the difference's sign bit or its complement) with a hermetic positive and negative, but at the corpus's only two `overflow(` sites the analysis proves one operand exactly ($86 in *Emomyst*) and cannot bound the other -- the counter it compares is written `cell = cell + 1` with no mask, so any non-relational interval widens it to the whole byte, and the bound that would decide the branch **is** the branch. **Differential**: the rewritten certified program against its own trace, 300 calls, both executors, on eight programs across all six families (Follin 12 and 21, *Emomyst*, *Automatas*, JCH *Guldkornekspressen*, Puterman *Knob*, Becher *Jodler*, Baumrucker *Professor*), each with a deliberately corrupted store as the control -- and it earned its keep: the *Knob* diverged at tick 127 on an unsound seeding (`single_defs` merged across procedures, and SSA names collide between them), which nothing in the prints showed. **Cost**: 600 new lines (`eqsat` 283, `eqrules` 244, `ranges` 73) plus ~20 of glue against **116 retirable** (`texture.zerocarry` 16 + `texture.propagate` 34 + `idioms.fold`/`foldall`/`negated`/`width`/`_isbool` 66, and that last group only if S4 migrates too, which changes what is certified), and **x3.6 CPU** over the set. One difference is not a reduction and is recorded: on 21 Ghouls subtunes the simplified filter write makes `$0096` look like a `cutoff_hi` shadow, so the role plane renames it from `ptr` -- a two-role cell the plane must pick one name for, exposed by the better expression rather than caused by it. **Verdict**: the acceptance on prints is met and the passes should still not migrate on this evidence -- 5x the code and 3.6x the CPU for 1.5 % fewer tokens. What is worth keeping is the *analysis*: `ranges.py` is 73 lines, is what bought the borrow and the folded branch, and needs no e-graph to be useful to the bespoke passes | medium | done; presentation-only, certificates frozen, flag off by default |
| ~~**P-FLOOR Commando**~~ *done (2026-08-21)*: the complexity floor of one simple tune, measured on Commando song 1 alone ([prototype-commando-floor.md](prototype-commando-floor.md)); `tools/tuneprog_floor.py` is the instrument. **Verdict: the gap is storage typing, not algebra, and it is one rule wide.** Song 1's load band is 936 B of executed code against 1,942 B of data the trace reaches (48 % of the band); `xz -9e` puts the band at 2,548 B, the data at 1,116 B, the print at 2,956 B and the SID write log at 36,484 B -- the program is a 14x compression of its own output, and the print is already at the tune's own compressed size. The floor for the *player* is anatomy [3.1.3](playroutine-anatomy.md)'s 65 hand-written lines; the print's program section is **252** lines / 2,282 tokens (341 S4 statements -> 178 S6 -> 254 printed nodes), and a hand-factored form under four typings reaches **115** (126 with the 11 elided `trap 'untaken'` arms) -- 2.2x of the 3.9x closed, with 49 of the 254 nodes measured as pure machine encoding (17 halves and carries, 32 touching no storage). **`--eqsat` on this exact program moves 8 tokens of 2,282 at 4.7x the CPU**, all of it deleting two `0 +` in a borrow -- expression rewriting cannot dump a table as data, split a region or rename a derivation, which is the whole gap. The cause is one fused region: `$5448` (202 B, `state`) is the 96-entry frequency table *plus* six 3-byte per-voice arrays *plus* the SID register offset, because 26 of its 51 accessors are 3-element voice arrays and 10 are five `(b, b+1)` frequency pairs whose reach crosses them. 56 of the 252 printed lines name `FREQ[`, 23 of those are `FREQ[195]`, the register offset. **The u16 pairing is verified mechanically and the const typing is refuted -- by the tune**: song 1 plays pitch 104 twenty-five times (drum instruments 4 and 7), and `$5428 + 2*104` is `voice[0].ctrl`, so the drum's starting frequency *is* the ctrl bytes in the voice array; the arpeggio's `+12` reaches the `pwdir` cells. The mirror fault: the one pattern block prints as three regions (1290/1285/1287 B) because three accessors reached three extents. `voice.freq` (`$551D` lo, `$551A` hi) refuses to fold for neither of the two obvious reasons -- instrumented, `word._pairs` accepts the addresses and `word._crosses` returns False at all three sites; the refusal is inside `_match` and is left as a one-line open question, worth 7 printed lines. **The one mechanism**: partition a region's accessors by shape (k-byte span at index `0..k-1` = array; `(b, b+1)` at one index = u16 row; constant address = scalar), split the region where the partitions disagree about a byte and leave the overrunning accessor a bound assertion, instead of collapsing to the coarsest kind; a byte no store writes is `const` even when a neighbour is `state`. Certificates and pipeline untouched; presentation-only, hand-derived | small | done; one PR, no pipeline change |
| ~~**Q5 NMI refusal**~~ *done (2026-08-22)*: the gate is whether a CIA #2 source can fire, not whether a latch or a vector was written (§5 row above); `CIA` carries the accumulated ICR mask and Timer B's start bit, `nmi_gate` is the one predicate and is re-checked per tick (`nmi armed in play`), and a CIA #2 period is no longer a cadence. 547 refusals partitioned 311 armed / 81 dead / 154 undecided / 1 tracer fault, the row falls from 3.0 % to 2.2 % weighted, `rodger-alien3` and `goto80-jazzpjazz` are the evidence, recert 48/48 with no protected field moved on the original 46 | survey §5, §8 item 3 | small | machine, trace, tests, docs |
| ~~**Q6 `trap switch`**~~ *done (2026-08-22)*: the campaign's largest divergence class was three mechanisms in the front end's reading of computed control (§5 row above) -- a `JMP (ind)` dispatching on its patched operand instead of the word that operand points at, a patched branch offset of zero whose target the "not the fall-through address" rule discarded, and a copy index stepped before the arm that advances the run was chosen. Measured over the class's own 189 tunes at 30 s: **188 `trap switch` + 1 `untaken` → 177 certified**, 4 left, all one new shape (an unmatched `RTS`); and over the 78 `unverified` + `untaken` tunes, **78 → 78 certified**, which the same two control fixes bought without that row being worked on. Two evidence certificates from two of the families (`necropolo-experiment-zeta`, `daglish-deflektor`); recert **50/50 reproduced** with no protected field moved anywhere and **3 of the 48 re-baselined**, `cost.ir_blocks` +1 apiece (`ghouls-song28`/`30`/`32`) where the step block survives `ssa.merge_chains`; `ir_statements` is unmoved everywhere and the other 45 reproduce field for field | survey §4, §14 | small-medium | cfg, lower, build, tests, docs |
| ~~**Q7 fast tracer**~~ *done (2026-08-22)*: §8 item 7's gate, and the profile refuted its own premise -- the old tracer spent 5-6 % of its self time in the compiled P-Code and the rest on bookkeeping around it (base VM chain 27-29 %, the tracing `step` prologue 29-30 %, edges/frames/register masks 11-12 %, per-op attribution 7-9 %, C-level `set`/`dict`/`array` 17-19 %, the per-tick hash **1 %**), seven Python calls and six dict lookups an instruction, all re-deriving what a site already fixes. **One mechanism**: the site key is the VM's cache key, so a site's closure (per-op access sets, index-domain set and `(pc, op index)` pairs baked in as constants), register masks, control kind, cycle penalty and edge/call/return cells resolve once and the loop only indexes them; fetch, execute, resolve, dispatch and accounting fuse into one pass; a per-pc inline cache skips the byte re-read for a pc no store has touched (`known` carries the write mark, a first write to an executed byte drops the covering entries), which keeps an SMC-heavy tune correct without making an SMC-free loop pay; and the footprint hash is one numpy gather over the sorted write set, the page-free stream a mask over it. **3.0-3.5× on seven runs across six families (geometric mean 3.2×), 480-580 k instructions/s** against design §2's 277 k -- so the production tracer now beats the prototype VM the model was taken on by 1.7-2.1×. The `Trace` is **byte-identical**: `trace.json` plus every bulk array over all 82 traces the 50 certificates hold, and thirteen hermetic fixtures (one per recorded mechanism, including an operand cell that reverts and a chip write onto executed RAM under I/O) pin the digests against the previous tracer's. Recert **50/50 before and after, no field moved**; `tracevm.py` keeps memory attribution and the step loop, `tracesite.py` one site's resolution and code generation, `traceflow.py` the control-flow record. Two things the sweep then said that §12 could not: a certified tune's trace CPU falls **2.89×**, and a *refused* tune's does not move at all (**1.01×**) because it never reaches the tracer -- `no entry` refusals spend their seconds in `machine._traced`, which is `pysidtracker`'s cadence and init trace, S0 work the sweep bills to S1 | survey §12, §8 item 7 | medium | tracevm, tracesite, traceflow, trace, machine, tests, docs |
| ~~**Q8 NMI prototype**~~ *done (2026-08-22)*: §8 item 3's last design gap. A CIA #2 NMI is the schedule's **second entry** — one `Trace`, two procedures (`tick` and `nmi`), the preemption schedule recorded per tick and replayed by S8 ([prototype-nmi.md](prototype-nmi.md)). The population is the first deliverable and it re-ranked the rest: `tools/tuneprog_nmi.py` classifies all 7,023 by what the handler does, and **181 have both entries (2.6 % raw, 1.3 % weighted)** in four classes (`sample player, silent play` 67, `sample mixer ($D418 only)` 57 and 1.0 % weighted, `no SID write` 49, `handler writes the register file` 8), against §5b's 311/1.8 % — that count was taken with every gate bypassed and included tunes an NMI model cannot help. **The two entries never share code (0 of 181)** and **do share RAM both ways (67 % weighted each)**, which decided the model: two procedures, and a schedule placed at *store* granularity so the handler's view of shared RAM is exact rather than approximate. Three chip corrections were needed and the third mattered most — Timer B (29 % of the class), one-shot mode, and a latch write to a *running* timer landing at the pending underflow instead of reloading the counter (JCH's mixer rewrites `$DD04` every NMI; reloading made it 26 % slow and broke the grid from frame 584). Against `sidplayfp` over 1,500 frames on three tunes of three classes, **`$D400`-`$D417` is 0 frames differing on all three and the changed-write list is identical in order in 1,500 of 1,500**; `$D418` differs in 10/21/54 % of frames because the NMI instant is up to ~80 cycles early without a VIC model (§5 row). **`jch-easy-does-it`** is the certificate: 1,799 ticks, 199,514 preemptions, 0 divergences, 0 envelope traps, the schedule in the document. Class-wide at 30 s: **134 of 195 certified** (all 195 used to refuse), 41 diverged, 11 `JAM`, 9 refused. Cost on a one-entry program is **zero** — the emitted text is byte-identical (sha1 unmoved on three tunes), it runs on the plain `Machine`, verify throughput is unmoved and the tracer's is within 1-3 % — and recert is **51/51 with 0 fields moved** | §8 item 3, design §10 | medium-large | machine, cia, nmi, trace, cfg, build, emit, interp, verify, grid, tests, docs |
| **accepted boundaries** | an edge into another copy's non-entry row (`$16AB`: costs a `goto` and two field names, buys nothing) · folding a silent copy costs ~6 % statements (buys names and the coverage vector; `--no-merge` is the escape) -- Q2 makes this uniform, so Ghouls 17-19, 25, 27, 29 each grow 24-49 printed lines · a fold takes cells the naming plane had: the Knob's `$17B9`/`$17BC` leave the voice record for `b17B9[v + 3]`, Commando song 2's `$54F8` likewise | — | measured, recorded, not work |
| **data-gated: now measured** ([survey-tuneprog.md](survey-tuneprog.md) §10, 7,023 tunes) | ~~NMI+IRQ = `second interrupt source armed`, **547 tunes, 45 % of all refusals, 3.0 % of HVSC by weight**~~ **the sizing was wrong and is re-measured in §5b (Q5): 311 tunes / 1.8 % weighted are armed and are §8 item 3's population; 81 / 0.8 % weighted were dead evidence and are now admitted or refused with the diagnosis they always had** ·  two-planes chip/RAM **3 tunes** (34 read the RAM under I/O at all): the discriminating tune exists and the class is negligible, so the row stays closed · residual-stack localisation **826 tunes / 4.4 % weighted**, and the read is inside `tick` 62 % of the time · periodicity: **5,051 of 5,384 certified programs (95.7 % weighted) find no state repeat in 30 s**, so the reduction is gated on the `--until-period` pass, not on a sound shape · naming, numba (§8 items 4, 7) | — | |

Order (2026-08-21, post print review): **Q1a → Q1b → Q2 → Q3 → Q4 → campaign
(§8 item 1) → data-ranked remainder**; P4 interleaves anytime. Historically:
**P1 → P2 → P-JCH V20 → campaign.** P1/P2 first because they change certificate claims the campaign
would otherwise measure stale; the V20 family stage precedes the sweep so the
largest family's issues close by generalization on two tunes rather than at
scale; P3 (RTI contract, residual horizon) follows it; NMI/sample deferred,
data-gated on the sweep. P1, P2, P-JCH, Q1a, Q1b, Q2, Q3, Q4 and P-EQSAT are done; the campaign is also
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
   read of another frame, the return address in an `RTI` entry frame, and the
   pointer read as data — and then the whole program keeps its stack, since such
   a read can see any byte of the page. *Amended by Q4:* that entry frame's
   *status* byte is the tick's contract (the entry flags packed), so an `irq`
   tick whose `RTI` consumes exactly it eliminates like any other; and a program
   that does stay residual is traced on to the page-inclusive witness rather than
   certified `complete: false` at the page-free one.

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

1. ~~**Campaign at survey scale.**~~ **Done (2026-08-21)** —
   [survey-tuneprog.md](survey-tuneprog.md), driver
   `tools/survey/tuneprog_sweep.py` + `tools/survey/tuneprog_report.py`,
   `run.py`'s sample and seed so the two surveys join. The same 7,023 tunes,
   645 families, one subtune each, at a 30 s horizon:
   **5,384 certified (76.7 % raw, 91.2 % weighted to HVSC), 399 diverged (5.7 %
   / 2.5 %), 1,222 refused (17.4 % / 6.2 %), 11 crashed, 7 wall-timeouts**, in
   17.7 CPU-hours. The `--until-period` pass over the first three certified
   tunes per family (1,338, a stated nesting of the same stratification, 300 s
   cap) certifies 981 of which **894 are complete (91.1 % of certified, 99.4 %
   weighted; 81.2 % weighted over the whole pass-2 population)** against 118 at
   30 s, in 40.4 CPU-hours. What it decided, in order of population:
   **(a)** divergences are first-tick modelling gaps, not drift — 86 % occur in
   init or the first three ticks, so more compute is not what buys the rest;
   **(b)** `trap switch` is the largest failure class, 189 tunes, and takes
   whole families (Virtuoso, Daglish, Element114Studio, Fred Gray);
   **(c)** the second interrupt is 45 % of refusals and **3.0 % of HVSC by
   weight** — the largest addressable population anywhere, so item 3's NMI
   prototype is now first by data; **(d)** the fast tracer is warranted (item 7);
   **(e)** the raw `RTI` entry frame has no population — all 108 built interrupt
   entries are CINV; **(f)** 31 of 1,338 tunes certified at 30 s and did not at
   period scale (16 diverged, 13 `recursion`, 1 `play runaway`, 1 JAM crash), so
   the 91.2 % is a 30 s figure. Nine crash and failure classes are new §5 rows;
   the data-gated rows carry their measured class sizes.
2. **Static closure + unverified accounting** (L2): the bounded walk from
   untaken directions and unobserved switch arms, `verified`/`unverified`
   statement counts in the certificate, and the printer marking unverified
   arms; measure trap reduction on the six certified tunes. Cost: small; the
   sibling closure already exists.
3. ~~**The remaining exemplars**~~ / ~~**a JCH second-interrupt prototype**~~ —
   *the second half is **done (2026-08-22)**, §5b's Q8 row and
   [prototype-nmi.md](prototype-nmi.md).* It was **not** 2 % of HVSC: the tunes
   with both entries are **181 of 7,023, 1.3 % weighted**, and 43 more have the
   NMI as their *only* schedule. Certified at *store* granularity rather than
   call granularity, because the two entries share RAM in both directions in
   two thirds of the class by weight and call granularity would have placed the
   handler's view wrong; the concurrency model turned out to be the cheap half,
   and the chip model (Timer B, one-shot, the latch rule) the expensive one.
   `jch-easy-does-it` is the certificate; 134 of the class's 195 certify at 30 s.
   The remaining exemplars (Blackbird, Galway, Walker at length) are untouched
   and still cheap.
4. **Family naming by alignment**: assemble a symbol-bearing reference build
   (GT2 `player.s` with the tune's flags; SW `player.asm`; undefmon), align to
   the tune's procedures/regions by opcode-sequence and structure (or Ghidra
   Version Tracking through the export), and measure naming coverage on GT2 and
   SW. Cost: medium; payoff: readability for ~15 % of HVSC.
5. **Periodicity proof for counters** (L6) — small, raises the complete rate.
6. **Ghidra oracles in the nightly recert** — the complexity/coverage/emulate
   sub-modes on the certified set, so a regression in ours shows as an
   `ours_bigger` flag; resolve the two emulator disagreements first.
7. ~~**Fast tracer**~~ — *gate fired 2026-08-21, **done 2026-08-22** (#271)*.
   The gate: tracing was **78 % of the 30 s pass's CPU and 96.8 % of the
   `--until-period` pass's** at **329 ticks/s** amortised, ≈ 96 k
   instructions/s against design §2's 277 k. **The profile said it was not the
   6510 semantics.** Aggregating the per-site generated closures (cProfile keys
   them all as `<string>:1:_f`, so `pstats` keeps one and drops the rest), self
   time on five runs split: **core VM 27-29 %** (the base `step`/`run_record`/
   `_exec`/`_resolve` chain and the P-Code closure, of which the closure itself
   is 5-6 %), **the tracing `step` prologue another 29-30 %**, accounting
   (edges, shadow frames, register masks) 11-12 %, per-op attribution 7-9 %, the
   driver loop 4 %, C-level `set`/`dict`/`array` calls 17-19 %, the per-tick
   hash **1 %**. Seven Python calls and six dict lookups per instruction, all
   re-deriving what a site already fixes.

   **One mechanism**: the site key *is* the VM's cache key, so everything
   constant about a site is resolved on its first execution and the loop only
   indexes it — the P-Code closure with its per-op access sets, index-domain set
   and `(pc, op index)` pairs baked in as constants; the register masks; the
   control kind, length, cycle count and page-cross penalty; the edge, call and
   return cells. Fetch, execute, resolve, dispatch and accounting fuse into one
   pass. A per-pc inline cache skips the byte re-read for a pc no store has
   touched (`known` carries the write mark; a first write to an executed byte
   drops the covering entries), which is what makes an SMC-free hot loop cheap
   without making an SMC-heavy one wrong. The footprint hash is one numpy gather
   over the sorted write set, the page-free stream a mask over the same gather.

   Measured on the same instrument (one process, `process_time`, `Tracer` alone):

   | tune | ticks | before | after | ratio | after instr/s |
   |---|---|---|---|---|---|
   | *Automatas* (defMON, CIA) | 12,029 | 788 ticks/s | **2,503** | **3.18×** | 502 k |
   | Commando song 1 | 1,503 | 628 | **2,181** | **3.47×** | 577 k |
   | Ghouls song 1 (Follin) | 1,503 | 1,227 | **3,696** | **3.01×** | 476 k |
   | GoatTracker 2 *Do It Again* | 1,503 | 492 | **1,565** | **3.18×** | 513 k |
   | JCH V20 *Guldkornekspressen* | 1,503 | 444 | **1,438** | **3.24×** | 511 k |
   | *Experiment Zeta* `--until-period` | 6,000 | 590 | **1,972** | **3.34×** | 552 k |
   | *Automatas*, 40,000 ticks (footprint stress) | 40,000 | 757 | **2,350** | **3.10×** | 500 k |

   **3.0-3.5× (geometric mean 3.2×), 480-580 k instructions/s**, so the production tracer is now
   1.7-2.1× *faster* than the prototype VM the design's 277 k was taken on.
   `Tracer.trace()` is byte-identical: `trace.json` plus every bulk array over
   all 82 traces the 50 certificates hold, and thirteen hermetic fixtures pin the
   digests. Recert **50/50 before and after, 0 fields moved**.

   What it leaves: after the rewrite the compiled P-Code closure is 16-24 % of
   self time, the fused `step` 33-40 %, attribution 11-17 %, the driver loop
   9-11 % — no single mechanism is over 60 %, so the numba core stays a refuted
   row (§5), not a queued one.

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
