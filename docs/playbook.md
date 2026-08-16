# The playbook: every 6502 player idiom, and the fact that answers it

The 6502 player idioms are a closed set. What each one *is* — with the binary
evidence, per family — is docs/playroutine-anatomy.md; this page is the
decompiler's side of that table: for each idiom, the **execution fact** that
resolves it under the program-point fold (docs/fold-by-program-point.md) and
the **construct** it becomes. Read this before probing anything. A "new" case
is written as *nearest row + exact delta*; an empty delta means apply the row.
A new row lands only with its driver. Targets are absolute and by construction:
**zero calls, zero stack, zero scratch, zero SMC on every tune that builds**; a
tune that cannot is refused with a row named here, never built worse.

Vocabulary: **node** = `(pc, bytes, ctx, sp)`; **ctx** = the JSR sites whose return
bytes are on the stack; **`sp(n)`** = the stack pointer at node `n` (part of the key); **F-cov / F-var / F-ctx / F-loc / F-vol** = the five trace facts
(fold doc §2).

## SYM. Fault symptom → known causes, in order of prior

| symptom | check, in order |
|---|---|
| refused: `context/sp depth over the cap` | (1) genuine recursion — S11, corpus-absent, so first suspect (2) the walker's context maintenance (a frame drop the `sp` rule did not pop, an interrupt entry not modelled as its own base). A coroutine stack (Lft: `TXS` from a saved value, `RTS` over a page-one word the program stores) is *not* this: `sp` is in the node key, so it is bounded and builds (S6 over S10) |
| refused: `stack cell is also a data cell` | S10: a `$01xx` absolute access lands on a `stk_XX` cell — the tune uses page one as RAM *and* as stack in the same span; model page one as memory for the tune |
| one-cell divergence at frame N | (1) a scratch cell inside a computed access hull that was elided anyway — the hull clause (§5) missed a site (F-loc) (2) an operand cell written in play that the lifter's `prov` did not mark (F-var) |
| `unobserved` reached on a clean tune | the window grew and a new edge ran — reclassification, verify the guard |
| text grows with the window | impossible by construction (`|G|` saturates); if seen, a node key carries something per-execution — F9 |
| text ≫ static text | (1) context inflation on a tiny helper — measured, bounded, unify (L3) only if the gate asks (2) structurer duplication on an irreducible region — a structurer issue, not a fold issue |
| verdict changes with pass order | F3 — fix the rule, not the order |
| value right, cells wrong | destination fusion without layout proof — A10 |

## S. Stack idioms — the complete real-world set

| id | shape | is | fact | construct |
|---|---|---|---|---|
| S1 | `JSR/RTS` | call linkage | F-ctx: edge into `ctx+[site]`, `RTS` edge back to `site+3` in the parent | nothing — edges; the callee's nodes are inlined once per ctx |
| S2 | `PHA / JSR / PLA` | save-around-call (defMON `$1009`) | `sp(n)` | `stk_XX = a … a = stk_XX`; copy-propagates away |
| S3 | `PHA .. PLA` across straight/branchy/looped flow | spill | `sp(n)` | the same local; liveness like any register local |
| S4 | `LDA zp/PHA ×2 .. PLA/STA ×2` | pointer save | `sp(n)` | two locals |
| S5 | entry `PHA/TXA/PHA/TYA/PHA` | invocation convention | header `entry-frame N`; entry `sp = $FF−N` | never lifted |
| S6 | push hi/lo, `RTS` | computed goto | `RTS` whose observed successors are not the ctx's return | `switch goto ((zext2(stk_hi)<<8 \| stk_lo)+1)` over the observed targets; the locals carry the pushed table reads |
| S7 | `PLA/PLA` frame drop; pull-adjust-push inline params | computed goto + param reads | the return-byte locals are bound constants; the ctx pops by `sp` | constants fold; params become `mem[const+k]` |
| S8 | `TSX/STX .. LDX/TXS` | context bracket | `sp(n)` constant on both sides | `x = const`; `TXS` emits nothing |
| S9 | constant `TXS` | stack init | init fact | nothing |
| S10 | absolute `$01xx`, no `sp` | page one as RAM | F-loc; refused if it aliases a `stk_XX` | ordinary cell |
| S11 | recursion carrying depth | **corpus-absent** | depth cap | refused, row named |
| S12 | coroutine / switched stack: `TSX/STX save … LDX save/TXS … RTS` (Lft `$13E4–$13F0`) | a second stack the program keeps in page one | `sp` is in the node key; the `RTS` pops S10 cells | S6 `switch goto` over the stored word; bounded copies per depth |

## M. Self-modification idioms (no code image exists)

| id | shape | fact | construct |
|---|---|---|---|
| M1 | immediate-operand patch | operand cell ∈ `written` (F-var) | read of the state field (`_residual`) |
| M2 | abs-operand patch | same, word | `mem[word + index]`; carve/deref rungs after |
| M3 | vector patch / `JMP` operand rewrite | F-vol observed target set | `switch goto` with `unobserved` default; `dispatch` header |
| M4 | opcode patch (`$60` sentinel, defMON `$10B8/BF/D8`, Hubbard `$53DE`) | ≥2 byte variants at one pc ⇒ variant nodes | `switch cell { case v: … }` faulting default (`opsw`) |
| M5 | branch-displacement patch (`dbr`) | variant nodes on the operand | same `switch`; targets interior |
| M6 | `INC` on an operand cell (Automatas `$0FE4`) | store to a lifter operand cell | store to the field; the operand read is a read |
| M7 | init-time relocation / block copy (SID Wizard fixups, rip loaders, pack `JMP $xx00`) | before `mem0` | nothing — one variant in play |
| M8 | code cells read as data (Automatas voice array) | data reads of operand cells | reads of the same fields |

## D. Dispatch and control idioms

| id | shape | fact | construct |
|---|---|---|---|
| D1 | handler table → `jmp` operand rewrite (Follin `$6360`, Galway `$8323`) | M3 | `switch goto` |
| D2 | RTS dispatch | S6 | `switch goto` |
| D3 | `JMP (vec)` | F-vol | `switch goto` |
| D4 | multi-entry routine / shared tail (defMON `$1003/$1006/$1022`, GT `mt_execchn` fall-through) | nodes shared; ctx distinguishes call entries | structurer duplicates irreducible entries; no ownership rule |
| D5 | `JSR`/`JMP` low-byte patch (GoatTracker `$1289/$1295/$131E`) | the transfer's target is a variant (F-var); ctx keyed by the resolved callee | edges per observed target; `switch goto` if >1 |
| D6 | `BCC *+2` with patched offset (SID Wizard `$1951/$1A13`) | M5 | `switch` on the operand cell |
| D7 | opcode-patched `RTS` around a `JSR` (defMON `$10D8`) | M4 inside ctx `[$100F]` | `switch cell { RTS: ret-edge; LDA #: … }` |

## A. Arithmetic idioms (width is denotational — denotation-solve §9)

Unchanged: A1 `ADC/SBC` column chain = wide add; A2 `CMP` lo / `SBC` hi = wide
compare; A3 `ADC lo / BCC / INC hi` = wide add with carry as control; A4
`ROR/ROL` lane threading; A5 shift loop; A6 add loop; A7 table transform; A8
`AND #$0F/#$F0` packing; A9 the carry def-use edge decides width; A10 one wide
value, halves to unrelated places (sources fuse; store fusion needs layout
proof). Two facts the anatomy added, both about *flags as data* (anatomy §5.3):
a carry consumed tens of instructions after its `CMP` (GoatTracker), and a carry
*inherited* into `ADC` with no `CLC` (Hubbard `$523D`, a data-dependent +1) —
model C as a value with a definition site, never as "the last compare".

## L. Data-layout idioms

Unchanged rows L1–L7 (struct-of-arrays voice fields; stride-N channel structs;
author-unrolled per-voice copies; lo/hi pair tables; shadow block + blit; index
overruns the carve; zp as register file). Two additions from the anatomy: **L8
struct-of-code** — per-voice blocks all $31 bytes so `abs,X` indexes cells
inside code (defMON): fields = operand − block base, stride = block size, all
F-var; **L9 register image as immediates** (defMON write band): the SID image is
`LDX #/LDA #` operands = fields, the write-out is 25 loads-of-fields.

## F. Failure modes of the machinery — what survives the fold

| id | mode | rule |
|---|---|---|
| F3 | order-dependent verdicts | every verdict a fixpoint or order-free |
| F6 | wrong unit | the web, not the cell; overlaid cells split per web |
| F8 | guard at the wrong layer | a guard is a fact about a control point (branch, dispatch, variant), placed at that node |
| F9 | data pinned as control | a guard's observation is a control fact, never a data value; an emitted item is a function of the node and site-global facts, never of the concrete cell an access hit. Under the program-point fold there is no place to pin: computed accesses are `mem[expr]`; scratch inside a computed hull stays memory |
| F10 | the unit was the path | a fold over linear paths scales with the trace and needs rejoin bets; the unit is the program point (fold doc §1) |

Retired with their subjects: F1 (pc ownership — no copies to home), F2
(opaque-edge conservatism — no epochs), F4 (paired-analysis coverage — no spans),
F5 (under-carved extents as *soundness* — hulls are trace facts; carving is
declaration quality), F7 (post-inline coarsening — nothing is inlined by a pass).

## P. Protocol

1. SYM table first; then the idiom sections; cite rows by id.
2. "New" = nearest row + delta; empty delta ⇒ apply the row.
3. New rows land with a driver in the same change.
4. Claims come from drivers and readers (`tools/dump_tune.py`,
   `tools/inv_probe.py`, `tools/gate_sweep.py`); no probabilistic probing.
5. Gates: drivers first; corpus gate once per mechanism; zero clean→worse.
