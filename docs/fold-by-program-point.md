# The fold, corrected: fold by program point, not by path

*Status: proposal (2026-08-16). Supersedes docs/denotation-solve.md §11.2–11.5
as the design of the fold; §11.1 (the audit) and the invariants of §8.4 and
§9.1 stand unchanged. No code in this document; the plan is in §9.*

## 0. The verdict in one paragraph

§11 was right that the static campaigns (`framestack`, `procpass`, the
call/ret/sp arms of `frameval`, the balance/epoch machinery of `frameproc`)
re-derive statically what every execution states concretely, and that the
program should be **folded from its execution**. `framepath` (11.5) folded the
wrong thing: it merges *whole-frame linear paths* residualised over the frame
entry state, so (a) the number of things to merge is the number of distinct
per-frame control paths — a product over voices and over song position, 185
templates at 500 frames and 1,511 at 3,000 for Commando; (b) merging linear
paths needs *rejoin* guesses (`_anchor`, a bet with rollback); (c) residualising
over entry state forces every computed load to be *pinned* to the concrete cell
it hit, which is F9 by construction — the WIP `placein` hull guards treat the
symptom; and (d) loops the machine executes are unrolled by execution and would
have to be re-rolled by an isomorphism search. Result: Commando's fold is 2,183
lines at 500 frames, 2,835 at 3,000, still growing, with the voice loop spelled
three times, against 215 lines of static text that already holds the invariant.

The correction is one change of unit. **Fold by program point.** The node is
`(pc, bytes-at-pc, call context)`; the edge is an observed transition; the effect
is the lifter's semantics of that instruction, unresidualised; loops are back
edges; rejoins are node identity. The trace supplies exactly five kinds of
*facts* — coverage and byte variants, successor sets, call context and `sp` per
node, per-frame locality of cells and the address hulls of computed accesses,
volatile inputs — and nothing else. Stack removal is then a rename (`sp` is
constant per node — measured on nine exemplars, 0 exceptions), calls are edges,
SMC is variant nodes, scratch is a local wherever no computed access can reach
it, and every guard is a control fact. Size is bounded by executed code × call
contexts (1.0–3.0× on the exemplars), never by frames. The invariants of §8.4
and §9.1 hold by construction on every tune whose `sp` is a function of the
program point, and a tune where it is not (recursion, data `TXS`) fails loudly
with the playbook row (S11) — which the corpus does not contain.

Almost all of the machinery this needs exists: the walker (`structured.trace`),
the block builder, the expression layer, the structurer, `datadecl`,
`framelog`, Gate FP. What is added is two channels in the walker (context, `sp`),
a context-keyed block model, and a ~100-line stack rename. What is deleted is
what §11.3 named, plus `framepath`'s merge/anchor and the recorder's role in the
fold path.

## 1. Where the fold stands, measured

Commando (`MUSICIANS/H/Hubbard_Rob/Commando.sid`), this branch at f86fc7f, PAL,
subtune 0:

| | static pipeline | fold, 500 frames | fold, 1000 | fold, 3000 |
|---|---|---|---|---|
| distinct templates | — | 185 | 427 | 1,511 |
| recorder events (distinct templates) | — | 29,066 | 70,220 | 247,164 |
| `ck place` events | — | 3,920 | 9,311 | 32,249 |
| procedure text | 215 lines | 2,183 lines / 86 KB | 2,635 / 112 KB | 2,835 / 125 KB |
| voice loop | `loop { … x = x-1 … }` with `[x]` fields | three copies (`idx_5500`, `idx_54FF`, `idx_54FE` …) | same | same |
| labels / calls / stack | 2 labels, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |

Two facts in that table matter. The fold *does* reach zero calls/stack/labels —
the direction is right. And the fold's text is a function of the *window*, not
of the program: the template count roughly halves per halving of frames and the
text keeps growing at 3,000 frames. That is the definition of "scales with the
trace" (playbook SYM row 8), and it is not caused by the guard *observation* —
the F9 WIP replaces the concrete pin with a site-global membership hull and the
count of templates does not change, because the templates differ in their
*branch* facts (which voice was in which phase), not only in their place facts.

Root causes, each intrinsic to the unit:

1. **The template is a whole-frame path.** A frame is three voice iterations, and
   each iteration takes one of a dozen sequencer/effect paths; distinct frame
   paths are the product. Nothing in a per-frame linear path says "these three
   segments are the same code at X = 2, 1, 0" — that is a loop, and a loop is a
   property of *program points*, invisible in an unrolled path.
2. **The merge is on linear sequences.** Two paths that diverge at a branch and
   reconverge later must be re-joined; `_anchor` guesses the rejoin by the
   m-th-from-last occurrence of a shared guard key and rolls back if the bet
   fails. A CFG has no rejoin problem: two edges into one node *are* the
   rejoin.
3. **Residualisation over frame entry forces pinning.** To express "the value
   loaded here" as an expression over entry state, the recorder must know *which
   cell* was loaded — so it pins the address, and the pin becomes a guard the
   merge partitions on. That is F9, and no choice of guard observation removes it,
   because the recorder's data-flow needs the concrete cell whether or not the
   guard prints it. In a program-point fold nothing is residualised over entry:
   the effect of a node is the instruction's own semantics, `a = mem[base + x]`,
   and the evaluator resolves the address at run time exactly as the machine did.
4. **`e_XXXX` snapshot locals, occurrence-keyed load locals, `cvals` constant
   folding, the sweep** are all consequences of 3: machinery to keep the
   entry-state view coherent across a template.

Conclusion: the linear-path fold cannot be repaired into a compact one by better
guards or better anchors; its size and its bets are properties of the unit.

## 2. Fold by program point — the definition

**Trace.** As today: run init concretely to the post-init image `mem0`; run
`play` `N` invocations under the invocation convention (the entry frame of
`play_frame` bytes at `sp = $FF`; IRQ/kernal variants as `structured.irq_entry`;
the walker's cadence for multispeed tunes, one invocation being what
`framelog` cuts on). The trace is sequential — one invocation runs to its
balancing return before the next starts, as `structured.trace` runs it; nested
interrupts are a Gate-C (cycle) concern the frame projection abstracts by
contract (frameprog §1.1). Per executed instruction record:

- `pc`, the instruction bytes at `pc` (opcode and operands, as `structured.trace`
  already keys the lift cache),
- the **call context**: the sequence of `JSR` sites whose return bytes are
  still on the stack — maintained exactly by the concrete `sp`: push
  `(jsr_pc, sp_after_push)` on `JSR`, drop every entry whose `sp_after_push` is
  above the current `sp` before each step (a return, a `PLA/PLA` frame drop and
  a `TXS` bracket all pop it the same way, because the *bytes* left the stack),
- `sp` at the instruction,
- the successor `pc`,
- data addresses read and written (the walker has these), and per frame the
  read-before-write set (recorder has this as `rbw`; the walker can compute it),
- reads of volatile cells,
- for a computed transfer (`JMP (ind)`, `RTS`, patched `JMP/JSR`, `dbr`), the
  observed target set — already `Evidence.targets`.

**Node** = `(pc, bytes, ctx, sp)`. Including `sp` costs nothing where it is a
function of `(pc, ctx)` — every corpus tune but one — and bounds the one that is
not (Lft's coroutine stack, §7): a program point reached at two stack depths is
two nodes, as a routine reached in two contexts is. **Edge** = observed
`(node → node')`. The control-flow graph `G` is the union over the trace.
Nothing in `G` is a function of frames: `|G| ≤ Σ_ctx,sp |executed code|`, and
the only unbounded case is recursion, refused by a depth cap.

**Effect of a node** = the lifter's P-Code for `bytes` at `pc` (`lift(mem, pc)`
with those bytes) — the block builder already turns runs of these into
expression form over registers/flags/memory. No entry-state residualisation.

**Facts** the trace contributes, and only these:

| fact | from | used for |
|---|---|---|
| F-cov: executed nodes and edges | trace | code/data boundary, `unobserved` on every unexecuted branch direction / dispatch target |
| F-var: byte variants per pc | trace | SMC variant nodes (opcode/displacement patches); an operand patch is a *read* of the patched cell (§4) |
| F-ctx: call context + `sp` per node | trace | stack removal (§3): `sp` constant per node ⇒ stack cells are locals; calls are edges |
| F-loc: per-frame read-before-write set, computed-store hulls, per-site address hulls of computed loads/stores | trace | scratch → locals (§5); declared table extents (`datadecl`) |
| F-vol: cells read that are volatile, and the observed values of patched cells / dispatch words | trace | `inputs`, `dispatch` header lines, `switch` arms |

Everything else — expressions, widths, webs, roles, structuring — is computed
from `G` and the image, as the static pipeline computes it today from its block
model. The recorder (`recorder.py`) is *not* on this path; it remains a
verification/RE asset.

**Emission.** `G` is structured by the existing structurer into one procedure
(`play`), loops from back edges, `if` from two-way branch nodes with the untaken
direction as `unobserved`, `switch goto` from computed transfers over their
observed target sets, `switch` over an opcode-patch cell's observed values for
variant nodes. `JSR`/`RTS` are ordinary edges (see §3), so no `call` form is
ever produced. Text size is `|G|` up to structuring duplication for
irreducible regions (§6).

Why this is "the fold of the execution" and not "the static pipeline again":
every fact that the static campaigns tried to *prove* — where `sp` stands, which
copy of a shared routine a block belongs to, whether a push and a pull pair,
which cell a computed access can hit, which byte a `JMP` operand holds — is
here *read off the trace* as a concrete, checked fact. The static text was never
the problem; the static *theories* about calls, stack and aliasing were. §11.1's
audit stands; the answer to it is a program-point fold, because the program's
points are what the execution repeats.

## 3. Stack removal by construction

**The dynamic stack model.** `sp` is part of the node, so at node `n` the
stack pointer *is* `sp(n)`. Measured: over the nine exemplars `sp` is already a
function of `(pc, ctx)` (zero exceptions; depth ≤ 6; inflation 1.00–2.97), and
over 666 corpus tunes it is a function of `(pc, ctx)` in 665 — the exception
(Lft) is what the `sp` component of the key is for (§7). The premise is
therefore not "players don't do stack tricks" but "a player's stack depth at a
program point is bounded" — recursion is the only violation, refused by a depth
cap (row S11), and the corpus has none.

Given `sp(n)`, every stack access at `n` names a constant page-one cell:

| machine form | at node `n` | emitted as |
|---|---|---|
| `PHA/PHP` (`PHX/PHY` are not 6502) | store to `$0100 + sp(n)` | `stk_XX = a` where `XX = sp(n)` — a procedure local |
| `PLA/PLP` | load from `$0100 + sp(n) + 1` | `a = stk_XX` |
| `JSR t` | pushes `pc+2` hi, lo | **an edge** `n → (t, bytes(t), ctx + [pc])`; the two return bytes become locals `stk_XX`, `stk_XX-1` bound to the constants `hi(pc+2)`, `lo(pc+2)` — bound, not elided, so a routine that *reads* them (S7) reads constants |
| `RTS` in `ctx = […, j]` whose observed successor is `j+3` | pops the return | **an edge** `n → (j+3, bytes, ctx[:-1])`; nothing emitted |
| `RTS` whose observed successors are not the context's return (S6, RTS dispatch) | pops a computed word | `switch goto ((zext2(stk_XX) << 8 \| zext2(stk_XX-1)) + 1)` over the observed targets, each an edge to `(target, bytes, ctx')` where `ctx'` is the context the trace shows at the target (the pushed word came from a table or an `LDA #`, so the locals carry that expression and the switch subject folds to the table read the static pipeline's rung (d0r) named) |
| `PLA/PLA` frame drop (S7) | reads the return-address locals of `ctx[-1]` | the reads are the constants; the drop of the context happens in the *trace* (the entry leaves `ctx`), so the successor nodes are already in the caller's context |
| `TSX` | `x = sp(n)` | the constant |
| `TXS` with constant observed effect (S8, S9) | `sp` changes to a constant | nothing; the following nodes have their own `sp(n)` |
| `TXS` with data (S11) | `sp(n')` not constant | refused, loud |
| absolute `$01xx` access with no `sp` involvement (S10) | ordinary cell | ordinary cell — and if the same cell is a `stk_XX` of some node (Lft's coroutine stack: the program writes the word its `RTS` pops), that cell is *memory* for the tune, the push is a store and the pull a load, and the `RTS` is S6 over it; no theory, no refusal |
| entry frame `PHA/TXA/PHA/TYA/PHA` (S5) | convention | header `entry-frame N` as today; the entry `sp` is `$FF − N` and the invocation's own pushes are the frame's, never emitted |
| `RTI` at the frame's end | pops P and the sentinel return | the procedure's `ret` |

Locals `stk_XX` are ordinary procedure locals: they get the same liveness,
renaming and dead-store treatment every register local gets (`frameproc` pass
1); a `PHA … PLA` spill of A becomes `stk_FC = a; … a = stk_FC` and then, by
copy propagation, disappears where A was not clobbered between. No `_Slot`,
no `_SpSlot`, no balance walk, no epoch: `sp(n)` is the trace's number.

**Why calls need no `call` form.** A `JSR` is an edge into a context; the callee's
nodes carry that context; the `RTS` edge returns to the caller's continuation in
the caller's context. The callee is therefore *inlined once per context* — the
copy the static `procpass` tried to plan (`Carry`) is here the trace's own
enumeration, and it cannot be wrong about which pc belongs to which copy because
each node knows its context. A callee reached from three sites is three regions
of `G`; nested calls multiply contexts as call strings do; the inflation is
measured (§7). This is the invariant of §8.4 — one procedure, no call, no return,
no stack access — as a *shape of the graph*, not a campaign over text.

**Where the copies are unwanted** (a tiny helper called from 20 sites, Chameleon's
`voffs`, ratio 1.56 there): the copies are ≤ the helper's size × sites, exactly
the bound §8.4 named ("a helper whose inlined copies exceed the observed call
sites, which is a bound, not a blow-up"). If a later reading wants
non-duplication, the option is *exact context unification* (§6), not a
procedure: the invariant stays.

## 4. SMC by construction

The artifact carries no code image (frameprog §2). In the program-point fold this
is literal: `G` holds instructions, not bytes at addresses.

| idiom (playbook M) | in `G` | emitted |
|---|---|---|
| M1 immediate-operand patch | the lifter's operand byte comes from a cell the trace shows written in play (`Evidence.written`); the block builder already residualises such an operand as a read of that cell (`_operand_expr` with `prov`) | `mem[$cell]` / the state field of that cell — a variable read |
| M2 abs-operand patch (pointer in an operand) | same: the operand word is two written cells | `mem[(zext2(hi) << 8 \| lo) + index]` — then `datadecl` carve / rung (f) as today |
| M3 vector patch (`JMP (vec)`, `JMP $xxxx` operand rewritten) | node with a computed transfer; observed targets = F-vol dispatch set | `switch goto (word)` over the observed set with `unobserved` default (`dispatch` header line as today) |
| M4 opcode patch (`RTS`↔`LDA #`, `DEC`↔`INC`, `ADC`↔`SBC`, `NOP`↔`ASL`) | the pc has ≥ 2 byte variants ⇒ ≥ 2 nodes `(pc, bytes_i, ctx)`; the predecessor edge fans out on the cell's observed value | `switch m_cell { case v_i: … }` with faulting default — the `opsw` form the grammar has |
| M5 branch-displacement patch (`dbr`) | variant nodes on the branch's operand byte; targets are interior | same `switch` on the operand cell |
| M6 `INC` on an operand cell (call counter in code) | a store to a cell that is also a lifter operand | the cell is a state field; the store is a store; the operand read is a read |
| relocation / init-time patching (SID Wizard's 30–36 fixups; multi-player packs' `JMP $xx00`) | happens in init, before `mem0`; the play-phase bytes are constant | nothing: `mem0` is the image; a play pc has one variant |
| code cells read as data (Automatas reads its operand bytes as the voice array) | data reads of cells that are also lifter operands | reads of the same variables — the frameprog §2 rule, total by definition |

`desmc.py`'s relocation half (building a de-SMC'd code image for the static
lifter) has no subject: there is no code image. Its declaration half (patched
cells become state fields with `observed` values) is F-vol.

## 5. Frame-locality, computed accesses, and why the only guards are control guards

**Scratch (§9.1).** A cell is scratch iff every frame that reads it wrote it
first (F-loc: `written − rbw`), and it is not observable (`$D400..$D41C`), and
**no computed access can reach it**. The last clause is where the fold's F9
pins came from, and it is decided by *hulls*, not by pins:

- for every computed store site, the observed address hull (F-loc, plus the
  declared extent of the datum it walks, from `datadecl`) — a cell inside any
  such hull stays memory (`comp_st` today);
- for every computed **load** site, likewise: a scratch cell inside a load
  site's hull is *not* elided into a local — it stays a memory cell (`mem[$xx]`
  store and load), so the computed load reads it through memory as the machine
  did. No forwarding, no `placein` guard, no staleness envelope: the value is
  in the cell because the store was not elided.

Everything else that is scratch becomes a procedure local `s_XXXX` (then a
register-shaped local by renaming). The set of cells this keeps in memory is
small (a page-one spill inside a `$0100,X`-walked region; a zero-page cell inside
an `(zp),Y` walk's hull) and, being memory, needs no proof.

**Guards.** With no pinned data, the artifact's guards are exactly the contract's
control guards:

- an unexecuted branch direction at an executed node → `unobserved`,
- an unobserved dispatch target / opcode variant → the `switch` default,
- an unobserved index value is **not** a guard: a table read is `T[x]` over the
  declared datum; the declaration's `observed` extent and `datadecl`'s carve
  state what was seen, and Gate FP is unaffected because the evaluator reads
  the same image the machine read.

Soundness against Gate FP, stated once: the folded program executes, on the
same `mem0`, the same instruction semantics along the same edges the machine
took (edges are exact by F-cov; effects are the lifter's, the same P-Code the VM
ran; addresses are computed by the same expressions), differing only in (a)
stores to elided scratch cells, which nothing observable reads (by F-loc and
the hull clause), and (b) unexecuted edges, which fault. Hence the per-frame
canonical SID write log is byte-identical over the traced window — the same
argument the static pipeline's Gate FP relies on, without the aliasing/extent
proofs, because the trace's hulls replace them.

## 6. Structure: loops, contexts, labels

- **Loops** are back edges of `G` (`natural_loops` in `structured.Analysis`);
  the voice loop `for x in $02..$00` is the back edge `$53A2 → $505F` and its
  induction is `x`, exactly as the static text renders it. No re-roll pass.
- **Author-unrolled voices** (Galway, Follin: three copies of one routine at
  different pcs) are three regions of `G`, as they are three regions of the
  binary. Unifying them is the L3 isomorphism license — an *optional* later
  pass with an exact check (mnemonic-stream equality modulo a base
  displacement, the diff the anatomy doc §3.2/§3.6 shows is exact for both), not
  a fold concern.
- **Context copies** (a `JSR`-called routine reached from three sites with X = 0/7/14 constant per site — GoatTracker `mt_execchn`, SID Wizard `DOTRACK`) are three regions of `G` that differ only in a constant. The same exact-unification pass can turn them into `for x in {0,7,14}` — again optional; the fold emits the copies.
- **Irreducible regions** (a shared tail entered from two contexts, Follin's
  handlers jumping back into their own voice's fetch loop) are handled by the
  structurer's node duplication (already: "an irreducible region's second entry
  is a copy") — bounded by `|G|`.
- **Labels/`goto`.** The static text keeps a few (Commando: 2) at forward merges
  the structurer cannot express with `break n`; unchanged by this proposal. Zero
  labels is a structurer target, not a fold target.

## 7. Size and non-redundancy, bounded and measured

`|text| ≤ c · Σ_ctx |code executed in ctx|` (+ structurer duplication). Measured
node/pc inflation over the nine exemplars (1,500 frames):

| tune | executed pcs | nodes (pc, ctx) | ratio | max depth |
|---|---|---|---|---|
| Commando | 314 | 314 | 1.00 | 0 |
| Comic Bakery | 762 | 776 | 1.02 | 3 |
| Ghouls'n'Ghosts | 682 | 682 | 1.00 | 0 |
| Chameleon | 992 | 1,545 | 1.56 | 6 |
| Easy Does It (JCH) | 613 | 613 | 1.00 | 1 |
| Je suis Linus (GT2) | 422 | 1,088 | 2.58 | 2 |
| Emomyst (SW) | 740 | 2,198 | 2.97 | 4 |
| Automatas (defMON) | 534 | 1,151 | 2.16 | 2 |
| Grid Runner | 326 | 768 | 2.36 | 2 |

Corpus (the 682 cached tunes, 200 invocations each, same probe; 666 measured,
16 exceeded the probe's instruction budget — init idle loops the walker's driver
handles and the probe does not):

| | value |
|---|---|
| node/pc inflation | median 1.48, p90 2.71, max 4.26 (`D_V/3SID_Test_3SID`) |
| max context depth | 6 (7 tunes); 0–2 for 563 of 666 |
| `sp` not a function of `(pc, ctx)` | 1 tune: `Lft/A_Chipful_of_Love_for_You` — a coroutine stack: `TSX/STX save; LDX #0; …; TXS; RTS` switches to a stack the program keeps in page one; with `sp` in the node key it is 2,315 nodes against 2,093 (bounded, builds; the `RTS` at `$13F0` is a 4-target `switch goto` over a page-one word the program stores — S6 over S10 cells) |
| `RTS` with several observed successors (S6) | 2 tunes (`Brooke_Jason/Andy_Capp`, the Lft tune) |
| 35 tunes with `play = 0` whose handler is installed through `$0314` were not resolved by the probe (walker does: `irq_entry`) | — |

Text growth with the window: none by construction; the static text of Commando
(215 lines) is the shape, and the fold's is the same graph.

Non-redundancy: each `(pc, bytes, ctx)` is emitted once; a frame path never
appears in the text; the text does not change when the window grows beyond
coverage (the F-cov set saturates — Commando's executed set is complete by
frame 3000 minus one dead skydive arm). The size gate of §11.4 (folded text ≤
static text per tune) becomes provable rather than hoped: both texts are
structurings of the same executed graph, and the fold's has strictly fewer
constructs (no `call`, no `sp`, no stack cells, no scratch fields).

## 8. What the artifact looks like

Exactly the static Commando text of today (`sub_5012` with `for x in $02..$00`,
`[x]` fields, `unobserved` guards, `hi-first` word stores), for every tune —
minus the two `goto`s if the structurer improves, minus every `call`, `sp`,
`stack_XX` token and every scratch `state` field corpus-wide. The header keeps
`play`, `init`, `entry-frame`, `sid-init`, `inputs`, `dispatch`, `data`,
`state`, `symbols`, `evidence` (F-cov/F-var/F-vol channels, so
`block_model(loads(text))` rebuilds `G` — totality gets easier: the evidence
*is* `G`).

## 9. The plan

### 9.1 Modules

**Change — `structured.py`.**
- `trace`: add the two channels — per executed instruction, the context and
  `sp` — and cap the context depth / `sp` range (F-ctx); refuse with S11 on
  overflow. Keep every existing channel. Context maintenance is ~15 lines
  (`ctx.append((pc, sp_after)) on JSR; while ctx and ctx[-1][1] < sp: ctx.pop()`).
  Record per-site address sets for computed loads and stores (F-loc hulls) and
  the per-frame `rbw` set (the recorder computes it; the walker can with one
  dict per frame).
- The block model: blocks are keyed by `(entry pc, opcode byte)` today
  (`Block(entry, op0, …)`, so M4 opcode variants are already distinct blocks);
  extend the key with `ctx`, and take successors from the observed edges per
  node (`targets` keyed by node). `JSR` terminates a block with an edge to the
  callee node; `RTS` with the observed return edge(s). `_BlockBuilder` already
  residualises a patched operand as a read of the written cell (`_residual`
  over the lifter's `prov`) and a patched transfer operand as an expression
  (`_ctrl_target_expr`) — M1/M2/M3/M5 need no new code.
- `sp` in the node key: free where it is redundant, and the bound for
  coroutine stacks (Lft) — no theory of `TXS`.
- Byte variants: a pc with several byte tuples yields several nodes; the
  predecessor's edge selects by the observed value of the patched cell(s)
  (F-var/F-vol) — this is what `desmc`'s declaration half already computes;
  move it here.

**Add — `framestack.py` replaced by ~100 lines** (`stackmap`, or inline in the
block builder): for a node with a stack access, `sp(n)` → the local name;
`JSR`/`RTS` → edges (+ the constant return-byte locals). Nothing else.

**Change — `frameproc.py`.** Pass 1 (registers/temporaries → locals) applies
to `stk_XX` locals unchanged. Delete: `sp_addr`, `sp_kept`, `sp_disp`,
`sp_delta`, `sp_balanced`, `calls_in`, `Calls`, `slot_reader`, `machine_reads`
(page-one), the `_ANYCALL` forms, `entered_pcs`/`bound_pcs`' call cases, the
epoch machinery. Keep: `Defs`, `envs`, `hoist`, width, `for`-range inference,
the renderer of statements.

**Change — `frameval.py`.** Delete the call/ret/`sp` arms, frame matching,
stack-page protection. The evaluator evaluates one procedure with locals,
`mem[]`, tables, `switch`, `unobserved` — the terminal shape.

**Change — `procpass.py`.** Delete copy/splice planning (`plan`, `Carry`,
homing). Keep `_dyn_targets`, `_idoms`, `_succs` if the structurer uses them.

**Change — `desmc.py`.** Delete the relocation half; keep the declaration
half (patched cell → state field with observed values), fed by F-var/F-vol.

**Change — `render.py` (structurer).** Input is `G` keyed by node; the CNS /
scoped-merge / allow-fixpoint / rank machinery whose purpose was label
minimisation *across procedures and copies* is deleted (one procedure, copies
are nodes). Keep region formation, loops, `break n`, `switch goto`, `opsw`.

**Delete — `framepath.py`** (the linear-path merge, `_anchor`, `_Xl`,
`_lsets`, `placein`), and its tests. **Delete — `framestack.py`**,
`test_framestack.py`, `test_call_lift.py`, `test_dup_dispatch.py`,
`test_stack_residue.py`; re-point `_callgen.violations()` to assert the shape
(no call/ret/sp/stack tokens) en masse. **Recorder** stays (RE asset,
`smc-recovery.md`), off the fold path.

**Keep unchanged.** `lifter.py`, `vm.py`, `framelog.py`, `frameprog.py`
(program assembly, `dumps/loads`), `grammar`, `datadecl`, `framefuse` (width),
`frameptr` (deref rung, over `G`'s expressions), `roles`, the gates.

### 9.2 Algorithms, in the order they run

1. `trace(mem, init, play, frames)` → `Evidence` + `nodes`, `edges`, `sp`,
   `ctx`, `rbw`, `hulls`, `variants`, `vol`. Cap depth (F-ctx). Detect S10
   stack aliasing (a `stk` cell equals an absolute-access cell): such a cell
   is memory for the tune (the Lft case), no refusal.
2. Build blocks over nodes: leaders = edge targets ∪ `play` ∪ variant nodes;
   a block = maximal straight run of nodes in one context with one byte
   variant each; terminator = the last node's transfer with its observed
   successor set (a `JSR` is a `goto` to the callee's first node; an `RTS`
   with one observed successor is a `goto`; with several, a `switch goto`).
3. Lift each block: the lifter's P-Code per node → the block's expression form
   over registers/flags/`mem[...]`; stack accesses through `sp(n)` → `stk_XX`
   locals; patched operands → cell reads. (This is `_BlockBuilder` with the
   stack rule and the operand-cell rule; both are local to a node.)
4. Scratch: `written − rbw − outputs − ⋃hulls` → locals; the rest memory.
   Volatile reads → `inputs`.
5. Structure `G` (loops, ifs with `unobserved`, `switch`) — the structurer.
6. Passes over one procedure as today: register locals, `for` ranges, width
   (rung d), deref (rung f), roles, `datadecl` declarations, `state` fields =
   cells read at frame entry (state) minus scratch, `evidence`.
7. `dumps` → text; `loads` → `G` again (totality); Gate FP.

### 9.3 Drivers and gates

- **Drivers first** (synthetic 6502 players, `tests/_stackgen.py` and
  `_callgen.py` shapes): S1–S10 each as a driver that must fold to zero
  call/stack tokens and pass the reference evaluator; S11 as a driver that must
  be refused with the row named. M1–M6, D1–D6 likewise. These are the playbook's
  rows made executable; the playbook (rewritten alongside, docs/playbook.md)
  cites them.
- **Witnesses**: Commando, Grid Runner, Automatas (the three of 11.6) plus the
  eight anatomy exemplars, which between them exercise every idiom class:
  Follin (patched-`JMP` dispatch, no calls, three unrolled voices), Comic
  Bakery (patched-`JMP` dispatch, RTS-trick `Code` command reachable, 8-deep
  data stack in a record — S3 across `PHA/PLA`), JCH (zero JSR, NMI excluded by
  the digi rule), GoatTracker (`JSR` low-byte patch = a `JSR` whose *target*
  varies: variant nodes at the `JSR`, contexts keyed by the resolved callee),
  SID Wizard (init relocation, `BCC` patched offset dispatch, `JSR DOTRACK` ×3),
  defMON (opcode-patched `RTS` gate around a `JSR`, 8× multispeed, `SBX/SAX/LAX/
  ANC/ALR`), Chameleon (2× speed, depth-6 contexts, `$D41B` input).
  Gate: byte-identical logs at full length; text ≤ static text; zero
  call/ret/sp/stack/scratch tokens; `block_model(loads(text))` rebuilds `G`.
- **Corpus**: `gate_sweep --fold` behind the switch as planned; flip the
  default when 624/624 build clean or refuse with a row; then mass-delete.
- **Metrics that cannot be gamed**: (i) Gate FP pass/fail per tune; (ii)
  `inv_probe` counts, all zero by construction — the probe becomes a shape
  assertion; (iii) text size vs static text; (iv) node/pc inflation per tune
  (report, not gate); (v) count of refused tunes with their rows.

### 9.4 Refusal classes, named in advance

| class | detection | row |
|---|---|---|
| context depth or `sp` range unbounded (recursion) | depth cap in the walker | S11 |
| machine stack cell also an absolute data cell | `stk_XX ∈ Evidence.written − stack pushes` | S10 — the cell is memory, not a local; builds |
| computed transfer with no observed target (never taken) | F-cov | already `unobserved` |
| a byte variant at a pc that is neither an opcode nor a branch operand nor a lifter operand cell (i.e. code overwritten by data at play time) | F-var | M-class, new row if ever seen — the anatomy corpus has none |

Everything else builds.

### 9.5 Order of work

1. Walker channels + assertions (F-ctx, hulls, `rbw`), reported by
   `tools/inv_probe.py --graph` (nodes, ratio, depth, violations) on the corpus
   — one day, no artifact change, tells us the exact refusal set before any
   emission changes.
2. Node-keyed block model + stack rename + scratch rule, emitting through the
   existing structurer, behind `--fold`; gate on the eleven witnesses.
3. Corpus gate behind the switch; triage refusals by row.
4. Flip; delete (§9.1); suite; docs (§11 marked superseded here; playbook
   rewritten).

## 10. Risks and open questions

- **The structurer's quality on `G` with contexts.** Copies of a callee inside
  a loop body are fine; a callee shared between two loop bodies is two regions.
  Irreducible shapes duplicate. Bound: `|G|`. Not a correctness risk.
- **`sp` on interrupt-driven tunes.** The entry frame differs by convention
  (`entry-frame`), not per execution; measured 0 violations of `sp(pc, ctx)`
  outside the one coroutine tune. A tune
  that nests interrupts (defMON's `CLI` mid-play, JCH's NMI) runs the nested
  handler on the same stack: the walker's driver models the nesting today
  (`run_irq_driven`); the nested handler is either excluded (digi rule) or a
  separate entry with its own `sp` base. Name it when met.
- **Trace coverage.** As today: the window is the song's full length; an edge
  never taken faults. Nothing new.
- **Exact unification (L3, contexts).** Optional; the fold emits copies. Do it
  only if the size gate asks.
- **`framepath`'s one real contribution** — the observation that frame-locality
  forwards through locals *by construction* — is kept as the scratch rule of
  §5, which is the same rule stated over program points.
