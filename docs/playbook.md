# The playbook: every 6502 player idiom, every known failure mode

The 6502 is not complicated. Its player idioms are a closed set, enumerated
below with the mechanism that answers each. **Read this before probing
anything.** A "new" case must be written as *nearest row + exact delta*; an
empty delta means apply the named mechanism — no new name, no probe scripts,
no guessing code. A new row lands only together with its mechanism and driver.
Targets are absolute: **zero stack, zero scratch, zero calls on 624/624** — a
corpus tune in a "semantic" refusal class is a misdiagnosis (S11).

## SYM. Start here: fault symptom -> known causes, in order of prior

| symptom | check, in order |
|---|---|
| `load/store into the stack page $01xx` | (1) surviving call's machine push over a destacked spill — count the tune's call lines first; (2) a walk deleted a store whose reader used a different slot key — F6/F2; (3) plain page-one data cell mis-protected — S10/F8 |
| `ret/switch/goto target $X outside the observed set` | (1) a pc bound twice / first-wins map — F1; (2) label pruned that a transfer names — check `frameproc.entered_pcs`; (3) dispatch table under-closed — D5 |
| one-cell divergence at frame N | (1) an indexed/covering read the walk did not count — F4 (`framestack.read_reach`); (2) under-carved extent — F5/L6; (3) dead-store drop of a machine-read byte — F4 |
| `runaway frame program` | loop-carried def killed by liveness — F2 (`for` live-out; levelled exits) |
| clean tune -> `unobserved reached` | deeper placement runs a previously skipped path; expected reclassification — verify the guard, not the placement |
| value right, cells wrong | destination fusion without layout proof — A10 |
| verdict changes with pass order | F3 — the rule is order-dependent; fix the rule, not the order |
| artifact scales with the trace (template count ~ frames; folded text >> static text) | (1) a guard observation is data, not control — F9 (Commando: place guards pinning table reads to the note played, 11,750 frames -> 5,915 templates where 1,302 control shapes exist); (2) only after F9 is excluded: an unrolled loop resisting re-roll — §11.2(3) |
| `no consistent fork` / `divergence without a shared guard` | (1) two guard kinds at one control point — guard identity not site-global, F9+F6; (2) a genuinely unshared divergence — read the recorder facts at the site before naming anything new. Chain `_fork`'s swallowed errors (innermost first) to see the real head mismatch |

## S. Stack idioms — the complete real-world set

| id | shape | is | answered by |
|---|---|---|---|
| S1 | `JSR/RTS` | call linkage | splice/copy (`procpass`, `render.placed`); no call form ⇒ no push |
| S2 | `PHA / JSR / PLA` | save-around-call (defMON `$1009`) | inline first; pair forwards as plain store/load |
| S3 | `PHA .. PLA` across straight/branchy/looped flow | spill | structured must-def (`framestack._Slot/_SpSlot`) |
| S4 | `LDA zp/PHA ×2 .. PLA/STA ×2` | pointer save (Alice `$1003`) | S3, two bytes |
| S5 | entry `PHA/TXA/PHA/TYA/PHA` etc. | invocation convention | header fact `entry-frame N`; never lifted text |
| S6 | push hi/lo, `RTS` | computed goto | rung (d0r) -> `goto (word+1)` |
| S7 | `PLA/PLA` frame drop; pull-adjust-push inline params (C64_World `$4921`) | computed goto + param reads | arithmetic lifts (sources fuse); placement stays (A10) |
| S8 | `TSX/STX .. LDX/TXS` | context bracket | bracket dissolution (`_saves`, `_SpFlow` caps) |
| S9 | constant `TXS` | stack init | init fact, `(abs,v)` base |
| S10 | absolute `$01xx` access, no sp | page one as spare RAM | ordinary cell; ownership at the access (`frameval._Page`); the page is not special |
| S11 | recursion carrying value / `LAS`/`TAS` / data `TXS` | **corpus-absent** | synthetic soundness drivers only; a corpus match = misdiagnosis |

## M. Self-modification idioms (all land as declared state post-de-SMC)

| id | shape | is |
|---|---|---|
| M1 | immediate-operand patch | a variable |
| M2 | abs-operand patch | a base/pointer variable (carve rules apply after relocation) |
| M3 | vector patch | a dispatch variable (`vec`/`swd`) |
| M4 | opcode patch (`$60` sentinel, defMON `$10B8/BF/D8`) | 2-variant mode dispatch (`opsw`, guarded, faulting default) |
| M5 | branch-displacement patch | mode variant |
| M6 | `INC` on an operand cell (Automatas `$0FE4`) | a counter stored in code |

Relocated cells are ordinary data afterwards: F1 (homing) and F5 (carving)
apply to them exactly as to any cell.

## D. Dispatch and control idioms

| id | shape | answered by |
|---|---|---|
| D1 | handler table -> `jmp` operand rewrite (Follin `$6360`) | `jmpd` + declared tables (`opdispatch`) |
| D2 | RTS dispatch | = S6 |
| D3 | `JMP (vec)` | `vec`/`gdyn` |
| D4 | multi-entry routine / shared tail (defMON `$1003/$1006/$1022`) | per-site copies; fixpoint-allow labels; single-owner homing |
| D5 | `swc`/`opsw` arms | resolve through their own table before `pcmap`; may bind pcs legitimately (arm-landing) |
| D6 | patched-displacement computed branch (`dbr`) | targets must be interior to the one procedure (open class) |

## A. Arithmetic idioms (width is denotational — §9)

| id | shape | is |
|---|---|---|
| A1 | `ADC/SBC` column chain | wide add/sub (SID-Wizard `player.asm:1747`, GT `mt_effect_3`) |
| A2 | `CMP` lo / `SBC` hi | wide compare |
| A3 | `ADC lo / BCC / INC hi` | wide add, carry spelled in control |
| A4 | `ROR/ROL` threading lanes (Wizball FILTER) | wide shift |
| A5 | shift loop | variable-shift divide; pure-loop closed form |
| A6 | add loop | multiply; same |
| A7 | table transform (`EXPTAB`, speed tables) | edit-time multiply/divide, already frame-level |
| A8 | `AND #$0F/#$F0` packing | two values in one byte (defMON flag/dur) |
| A9 | the carry def-use edge decides | edge threads ⇒ one wide value; broken/absent ⇒ genuinely two bytes |
| A10 | one wide value, halves to unrelated places | sources fuse unconditionally; a discarded half is `trunc`; store fusion needs layout proof |

## L. Data-layout idioms

| id | shape | note |
|---|---|---|
| L1 | struct-of-arrays voice fields (`tbl,x` ×3) | record by index web |
| L2 | channel structs, stride N (Grid_Runner: 7) | mut-index pattern `0,N,2N` |
| L3 | unrolled per-voice code copies | one field spelled const + indexed; unify per web (isomorphism license) |
| L4 | lo/hi pair tables: adjacent, interleaved (`+partner`), split (+21 Follin) | pair-row family |
| L5 | shadow block + blit (`sid.reg[x] = tbl[x]`) | covering read of every latch — count via `read_reach` |
| L6 | index overruns the carve | contiguous traversed run ⇒ one datum (absorb, `datadecl`); sparse map ⇒ NOT an extent (Puke `$171F`) |
| L7 | zp as register file (Wizball, 138 cells) | scratch = frame-locality (§9.1), per web |

## F. Known failure modes of THIS machinery (check before naming anything new)

| id | mode | rule |
|---|---|---|
| F1 | first-wins pc maps (`setdefault`) | a pc has one owner or none; two copies home nothing; placement/label beats copy |
| F2 | opaque-edge conservatism | a fault edge is lattice bottom (`unobs` joins nothing); a levelled exit lands past the loop it counts out of; a `for` leaves by its own bottom |
| F3 | order-dependent verdicts | every verdict a fixpoint or order-free; if reordering passes changes output, the rule is wrong, not the order |
| F4 | paired-analysis coverage gaps | reads must use the same span/extent machinery as hazards; both ends of a spill on one sp basis; arm liveness = loop's, not arm's |
| F5 | under-carved extents | a carve must cover the observed read map; absorption guarded by full coverage |
| F6 | wrong unit | the web, not the cell: slot keys must match across push/pull; overlaid cells split per web |
| F7 | post-inline coarsening | per-procedure premises re-checked after flattening |
| F8 | guard at the wrong layer | ownership at the access, not the page; a held slot's guard sits where the cell is made |
| F9 | data pinned as control | a guard's observation must be a control fact (a branch direction, a variant set), never a data value; pinning a computed read to its one concrete address turns song data into fork arms and the artifact scales with the trace, not the code. The address fact is membership: obs = the site's image-read set minus scratch, or the one scratch cell a forward names (singleton). The guard's identity (site, kind, expr) must itself be site-global — a per-execution kind choice recreates the fault as a fork-family mismatch (Commando $5380, mixed scratch-hit/image-read site); per-execution variation lives only in the observation, where a fork is the honest divergence (the scratch arm forwards, the image arm walks). The tell: an obs that varies with song position. The invariant, generally: every emitted item — guard, name, key — is a deterministic function of the translated prefix plus site-global facts, never of the concrete cell an access landed on (the pin has hidden in a guard obs, in a guard kind, and in a load local keyed by the landed cell's store version; a path-accumulated counter is the same fault at a rejoin). A data-read guard is a staleness envelope, not an observed-primary claim: its one job is excluding the scratch cells whose stores were elided, so it renders as the observed hull split at interior scratch cells — an exact sparse set spelled term-by-term is the trace in the text again (Commando: 5,997 terms for two note columns). Control guards (branches, dispatch, opcode variants) keep exact observed sets |

## P. Protocol

1. SYM table first; then the idiom sections. Cite rows by id in findings.
2. "New" = nearest row + delta. Empty delta ⇒ apply the row's mechanism.
3. New rows land with mechanism + driver in the same change, or not at all.
4. No probabilistic probing. Claims come from drivers and the named readers
   (`tools/dump_tune.py`, `tools/call_residue.py`, `tools/inv_probe.py`,
   `tools/gate_sweep.py`).
5. Gates: drivers first; both corpus gates once per mechanism; zero
   clean→worse; depth movement explained. Doctrine details: docs/frameprog.md
   §7.10, docs/denotation-solve.md §9–§10.
