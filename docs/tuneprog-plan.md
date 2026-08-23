# tuneprog — plan v4

Companion to [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md) (the
design), [tuneprog.md](tuneprog.md) (what is built), the `prototype-*.md` records
and [survey-tuneprog.md](survey-tuneprog.md). Sections: 1 where we are ·
2 backlog (open rows by lever) · 3 done ledger · 4 process and execution order.

## 1. Where we are

| | |
|---|---|
| certified | 51 certificates, 759,353 ticks, 0 divergences, 0 envelope traps; 42 complete via periodicity, `--songs all` complete on 31/32 subtunes; no tune-specific code in the front end for any anatomy mechanism. Families: defMON (*Automatas*, both SID models), Hubbard (Commando 1–2), Follin (Ghouls 32 subtunes + union), GoatTracker 2 ×2, SID Wizard ×2, JCH V20 ×3 (incl. the two-entry *Easy Does It*), installed-handler ×2 (*Jodler*, *Playful Professor*), dead-NMI ×2 (*Alien_3*, *Jazzpjazz*), patched-dispatch ×2 (*Experiment Zeta* complete at period 5,184, *Deflektor* 30 s) |
| certify at 15 s, not run to length | Blackbird (Quintessence), Galway (Comic Bakery), Walker (Chameleon) |
| refused by design | a CIA #2 source with no schedule (TOD alarm, serial, FLAG, CNT timer): 6 of 7,023 |
| survey ([survey-tuneprog.md](survey-tuneprog.md)) | 7,023-tune stratified sample at 30 s: **91.2 % of HVSC by weight certifies** (76.7 % raw), 2.5 % diverges, 6.2 % refused with a diagnosis, 0.26 % crashes; `--until-period` over 1,338: 99.4 % of certified programs complete by weight. 58 CPU-h on the old tracer |
| code | `deity_informant/tuneprog/`, 59 modules, 17,059 lines, none over 500; 741 hermetic + 63 HVSC + 10 oracle tests, 96 % coverage; SSA 1.0–1.6 statements per instruction; `tools/tuneprog_certify.py`, `tuneprog_recert.py` (51/51), `tuneprog_period.py`, `tuneprog_ghidra.py`, `tuneprog_floor.py`, `tuneprog_nmi.py`, `tools/survey/` |
| baseline | Ghidra high P-code export with SMC context ([ghidra-highpcode-export.md](ghidra-highpcode-export.md)), 5.8–10.6× our S4 — baseline, not core; three Ghidra oracles; `sidplayfp` grid oracle |
| merged PRs | #225–#280, one stage each, every one on green CI with recert reproduced |

## 2. Backlog

Open work only, grouped by lever; §2.8 holds measured boundaries that are not
work. `owner` = the modules that change. Size: small ≤ 1 day of one agent,
medium ≤ 1 stage, large = a stage with a prototype. Measurements marked *12-run*
are over 12 certificate tunes, one per family, at a 5 s `--no-verify` horizon
(2026-08-22); *P-EQSAT*/*P-FLOOR* are §3's rows.

### 2.1 Storage typing

| item | mechanism | evidence | owner | size | acceptance |
|---|---|---|---|---|---|
| an init-written table prints no bytes | `Rgn.init` is the *pre*-init image, so an `init_constant` region's rows would be the packed form, not what the tick reads; the post-init image the trace holds is what does, carried on the region or read from `trace.image_post_init` | #280: 15,249 of the 219,658 bytes the 51 certificates reach are `init_constant` — `sw-emomyst` 2,308 of its 4,281 (SID Wizard unpacks its tables in init), `ghouls-song01` 10,625 | datablock, build, regions | small | the 15,249 print, and no byte init writes at play time is among them |
| family name dictionaries by structural alignment | align a tune's procedures/regions to a symbol-bearing reference build (GT2 `player.s`, SW `player.asm`, undefmon) by opcode-sequence/structure or Ghidra Version Tracking; names only | L5: within a family ~5 % of tunes share an executed opcode sequence and 6-gram similarity is 0.2–0.7, so alignment not reuse | recover, ghidra | medium | named-field count on the GT2 and SW exemplars stated before and after; after > before on both |

### 2.2 16-bit

The keying landed (#277: the pair is two cells, `halves.py` holds the byte shapes).
What is left is the two shapes the pair alone does not reach.

| item | mechanism | evidence | owner | size | acceptance |
|---|---|---|---|---|---|
| SID lo/hi register pairs (`freq`, `pw`, `cutoff`) as one statement | one assignment under a write-order convention stated once in the header, exceptions marked per statement — the certificate compares the *executable's* ordered byte writes (`verify._compare`), which a print convention preserves | #277 built it and refused it, measured: folding the two writes shortens the per-voice run the register-file loop is built from, and `unroll` realigns the loop — *Automatas*' `writeout` 3 rows → 2 + 1 (+2 printed lines, +1 block), *Jodler* +2 blocks — against tokens −5,777 → −5,819 and lines −11 → −57 over the 51. P-FLOOR §6: ~9 of Commando's 21 SID-write lines | word, unroll | small; needs the row alignment below | the rule is on and no tune worse on any metric |
| two halves separated by a control join | the low half in each arm, the high half's store in the join reading a value the arms define; #277 folds this only where the value is *computed* in the arm, through the load it makes of the pair's other cell | *12-run*: the shape is Hubbard's and GT2's portamento; #277 reproduces `p_AD10`/`p_AD27` on GT2 and the switch arms of the patched-opcode snippet | word, structure | small | the join case folds without a dominance walk; no metric worse |
| nested-borrow compares `u16 < u16` | a borrow chain whose result only a branch reads is a 16-bit compare | *12-run*: 5 compare chains | halves | small | the 5 sites print as one compare |

### 2.3 Expression layer

Struck by #278: the two identity folds, the range-gated rewrites and the retirement of
`--eqsat` all landed; `gated.py` is the pass and `ranges.py` the one interval domain.
The one rule the plan named that the domain cannot decide is recorded there -- the
post-increment compare's 161 live sites are all the merged loop's copy index, whose
0..k-1 domain is `copyview`'s fact about an index, not `ranges`' fact about a cell.

### 2.4 Control shape

Struck by #279: dead values, the join's copies, the loop header's own start, the
untaken direction as a mark and the carry's name all landed. What is left is the
one loop shape the existing proof cannot reach.

| item | mechanism | evidence | owner | size | acceptance |
|---|---|---|---|---|---|
| a counted loop whose domain is an *expression* | a `For` node whose bound is a value, not a tuple: an index stepping by ±1 with the exit test on the stepped value, started from something the folder cannot read | #279 measured the 169 loops `loops.induction` leaves: 42 start unknown, 72 start known with no domain (the test does not fold, or the trip count passes `CAP`), 54 carry the index by something other than a latch copy, 1 has no exit test. Accepting a direct latch self-step moves 1 loop and 1 line over the 51 and was reverted. Commando's two `while True` loops count down from `b550C` and from an instrument byte | structure, loops, printer | medium | Commando §4's `for _ in 0..phase-1` prints; no tune worse on any metric |
| the closed program (`--closure static`) is a different shape for the *covered* code — **half done (Q1a)**; the GT2 0 `goto` criterion is not met and `--closure static` stays off. What is left is not dominance: a closed path rejoining inside a covered block splits it and the extra predecessor stops `ssa.merge_chains` | make the covered subgraph's block boundaries independent of closed rejoins | Q1a: closed arms own no dominance, `Je_suis_Linus` 23 → 16 `goto`, `Emomyst` 2 → 1; 13 of the remaining 16 are measured, all in one outlined procedure — 7 join regions with more than one way out, 2 that both leave and return, 4 that would promote but not pay | structure, closure, ssa | medium | GT2 0 `goto` under `--closure static`; trace-closed prints untouched |
| `fold.outline` can leave an edge to a block it deleted (S6 `KeyError` in `graph.preds_of`) | `fold._emit` joins a run of SESE atoms and checks `sese` per atom; an atom may join the run only when every predecessor of its head is already inside it | 32 of 7,023 (0.5 % raw, 0.1 % weighted), all certified; *Belagerung 2*'s `L8A30_EA` is the reproducer | fold | small | the 3 reproducers print; recert; printed text that moves is listed |
| static closure + unverified accounting | the bounded walk from untaken directions and unobserved switch arms, per-statement marks, trap reduction measured | exists behind `--closure static`; blocked on the row above for structuring | closure, printer | small | trap count before/after on the certified set |

### 2.5 Certification and coverage

| item | mechanism | evidence | owner | size | acceptance |
|---|---|---|---|---|---|
| the NMI instant is early without a VIC-DMA model | a raster/badline model of the cycles the CPU is stalled | after the `$FE43` stub fix half the offset remains: Iisibiisi 4,119 of 64,664 instants within 2 cycles, 12,356 within 8; `$D400`–`$D417` 0 frames differing on three tunes; `$D418` differs in 10/21/54 % of frames by one sample step | trace, tracevm | large | `$D418` frame agreement on the three tunes |
| periodicity proof for counters (L6): a cell read only through a mask contributes its residue to the state hash, fail-closed — an unclassifiable read keeps the whole cell | a masked-residue witness in `period.py` (Commando's `$5525`: period 256, `+32`, read only as `& 1` and `& 7`, so it is not what blocks the repeat) | 5,051 of 5,384 certified programs (95.7 % weighted) find no state repeat in 30 s; gated on the `--until-period` pass. Distinct from the lcm proof, refuted (§3) | period, verify | small | recert 51/51 field-for-field; the count of the 5,051 that newly complete stated, and 0 of them a full-state hash refutes |
| `frames.contract` names the frame only for the tick entry, so a two-entry program stays `stack: residual` (`jch-easy-does-it`, depth 8 held by `nmi`) | pin the recorded status into the flag registers for the NMI entry too | Q8 | frames, verify | small | JCH `stack: eliminated` |
| the second entry's live-in state is replayed, not computed: SP, status, return pc, A/X/Y from the schedule row (`replayed_registers` 6 × 199,514 on JCH) | pass them as the `nmi` entry's arguments from the preempted procedure's live values, as `frames.contract` passes the status | Q8; settles the row above and `nmi clobbers registers` below | verify, frames, build | medium | `replayed_registers` 0; JCH reproduces |
| `schedule not store-separable`: a load in an open preemption window reads a cell the handler stored in it | a preemption point per *load* of a stamped cell | 6 of the 195 class at 30 s, 5 of 7,023, 7 in the released banked-out class | nmi, interp, verify | medium | the 6 certify |
| `nmi clobbers registers`: a handler whose `RTI` does not return the interrupted A/X/Y | built by the arguments row above | 12 of the 195 class, 8 of 7,023, 4 in the banked-out class | nmi, verify | with the arguments row | the 12 certify |
| 43 tunes whose CIA #2 NMI is their *only* schedule refuse `no entry` before the chip is consulted | a single-entry program whose cadence is a CIA #2 timer | 0.1 % weighted | machine | small | the class traces |
| a moving NMI vector becomes one entry per address it took | right for a two-phase handler chain (103 tunes), wrong for a computed vector; nothing bounds the entry count | Q8 | trace, cfg | — | a bound, or a refusal past it |
| the 30 divergences and 8 `JAM` crashes left in the NMI class at 30 s | `state hash` 11, `sid` 6, `io` 5, `entry register` 3, `input mismatch` 2, one each `switch`/`input exhausted`/`brk`; 18 of 30 past tick 5 — the schedule drifting, not a first-tick gap | Q8 | trace, verify | medium | each of the 38 moved to a named refusal or a fix, the count per class stated |
| `trap switch` over an unmatched return (the RTS trick) | the program pops a return address the trace did not; all four are `stack: residual` — the stack model's boundary | 4 of the 189: *Exterminator* and *Ocean_Loader_3* (init), *Blood_n_Guts* (tick 0, pops `$0001`), *Gyruss* (tick 356) | stack, cfg, frames | medium | the four certify or refuse with the frame diagnosis |
| a patched `JMP (ind)` gets no static table closure | enumerate the *pointer* table and dereference each entry (`jumptab._cell` matches the operand cell, which for `JMP (ind)` is the pointer) | no certificate field falls here; untaken arms stay unlisted rather than `trap 'unverified'` | jumptab | small | Virtuoso/Galway arms listed |
| the Ben Daglish/Gremlin family certifies at 30 s and never closes on a period | all 12 sampled diverge `trap unreached X0002` at the tick the trace stopped on, every one `stack: residual` (1,584 to 10,177 ticks, three at tick −1) | the campaign's "31 certified at 30 s and not at period" class with one shared symptom | stack, verify, period | medium | one Daglish tune complete |
| a residual stack is whole-program: one unplaceable read keeps `SP` everywhere | an interprocedural frame layout localising the read — inside the tick in 62 % of cases, so 'whose frame' is the question | 826 of 5,783 built programs residual (4.4 % weighted); 819 have no computable depth | frames, stack | large | the residual share falls, measured on the sample |
| the `io` write list differs, 73 tunes (1.0 % raw, 0.2 % weighted), 41 in init | VIC/CIA writes are not the trace's; SID never differs | SIDSys18.6 17, DigitalArts 11, Novaload 11 | build, verify | medium | all 73 moved to a named refusal or a fix, the count per family stated (17 / 11 / 11 / 34 other) |
| volatile-input replay: `trap input exhausted` 26 + `input mismatch` 12 | pinned inputs consumed in a different order or number | 38 tunes (0.5 %); Novaload and DigitalArts lead | verify, interp | medium | all 38 moved to a named refusal or a fix, the 26 and the 12 counted separately |
| `RecursionError` out of the emitted program, 7 tunes | a tail call is a `Call` in the IR and grows a Python frame per edge; 2 surface inside `interp.ioload` | campaign §13 | build, wire, emit, interp | small | a self-recursive tail loop emits as a loop |
| `RuntimeError: JAM at $XXXX` escapes as a crash, 2 tunes | classify as a `Refusal` | campaign §13 | trace, machine | small | refusal, not crash |
| `KeyError` in `ssa._frontiers` (*Green_Tea.sid*); `KeyError: 'expr'` in `lower.ctrl_expr` (*Examples.sid*); `TrapError` out of `ir.evalbin` in S5/S6, 2 tunes | — | campaign §13 | ssa, lower, ir, views | small | the reproducers build |
| the tracer hands a CINV tick the registers the previous tick left; `$FF48` leaves A = 0, X = SP, Z set | decide against the `sidplayfp` grid on one Boray tune; `X = SP` cannot simply be modelled (an `SP` value that survives makes the program residual) | 4 of 37 (Boray) read A/X/Y live-in | machine, trace | small | the grid decides |
| a `cia_timer` cadence carries no video standard (`"ntsc" in source`) | carry the standard on the entry | `baumrucker-professor`: 1,503 ticks read 14.99 s where the machine says 14.44; moves `source` on four certificates | machine, verify | small | the four certificates move as listed |
| a certificate records what it verified, not what it was built from (`ghouls-songs-all`: built 220,049, verified 111,763) | a `traced_calls` cost field | sound as it stands; stated in prose only | emit, pipeline | small | field added, all certificates move once |
| `grid.sidtrace_clock` takes one period as the median raise gap and refuses a reprogrammed clock; and takes the median over raises that carried a write, the burst period of a sparse writer (*Caverns of Eriban* every 6th/3rd frame refused; *Jodler* every 7th gives a 7-frame clock) | per-segment clock at each latch rewrite; frame the CSV by the entry's cadence and its own first raise (needs the origin question settled) | three tunes of the NMI class still refused by the period check | grid | medium | the sparse and reprogrammed cases frame |
| `--seconds` computes its tick target from the cadence `find_entries` guessed, not the settled one (*Easy Does It* 1,799 ticks = 35.9 s for 30) | target from `Tracer._settle` | Q8 | pipeline | small | horizon honoured |

### 2.6 Tooling and cost

| item | mechanism | evidence | owner | size | acceptance |
|---|---|---|---|---|---|
| `tuneprog_recert.py --resume` replays a previous tree's verdicts from a reused `--out` | stamp tree identity (HEAD or module digests) in `recert.json` | Q8 | tuneprog_recert | small | a stale state file is refused |
| the sweep's `cpu_trace` bills S0 entry discovery to S1; a `no entry` refusal costs 14.6 CPU-s each (46 of 60 refused tunes) | a `cpu_entry` column; find out why `no entry` runs a whole init trace on `pysidtracker` | a certified tune's trace CPU is 8.6 % `_traced`; a refused tune's ~100 % and did not move with the 3× tracer (775 → 765 s over 60) | survey/tuneprog_sweep, machine | small | `cpu_entry` reported beside `cpu_trace`; a `no entry` refusal ≤ 1.5 CPU-s, the 60-tune sweep ≤ 200 CPU-s from 765 |
| **P4 Ghidra oracle** | per-call input capture in the facts export; resolve the two emulator disagreements (GoatTracker, Follin from call 2); Ghidra function bodies vs clone-per-entry (`ghidra_partial`); the complexity/coverage/emulate oracles in the nightly recert (`ours_bigger` flag) | Ghidra's emulator agreed byte-for-byte on Automatas and Commando; the per-call inputs the facts export does not carry are the open explanation | ghidra_facts, ghidra_compare, headless | medium | both disagreements explained or classified, the count stated; the three oracles run in the nightly recert on 51/51 with `ours_bigger` 0 |
| opcode cells whose alternative is not `RTS` in the SLEIGH export | overlay or paired constructor | 263 of 7,023 have an SMC opcode cell; 198 of them (3.4 % weighted) have one whose alternatives exclude `RTS`, so the `RTS`-only overlay covers the minority | ghidra/6510 | medium | the 198 decode to their non-`RTS` alternative in the export; certificates unmoved (recert 51/51) |

### 2.7 Complexity and duplication

| item | mechanism | evidence | owner | size | acceptance |
|---|---|---|---|---|---|
| one split mechanism | consolidate `views.field_split`, `views.transpose_split` and `partition.py` into one partition over accessor shapes -- #274 left three refusals that exist only because the three splits do not know about each other | three presentation splits of one region model. #274's duplicates, named: `partition._refs` ≈ `views._accesses` plus the proc name and is-store; `_cover`'s bounds math ≈ `views._offsets`/`_spans`; `_refused` ≈ `views._splittable` over `facts` instead of `names`, because it runs before `recover`; `_named` re-derives `field_split`'s scale loop; `"%s_%04X" % (kind, base)` duplicates `regions.py:191`; `_recell`/`_fields` is a second module that knows `f["slots"]` is keyed by `slots[k][0]`, which a `copyview.remap_cells(prog, moves)` would own. ≈ 35-40 lines removable. `field_split`/`transpose_split` emit names, `partition` emits regions: one model, two representations | views | small; #274 landed the third | one function, the three tests pass |
| private helpers sharing a name across modules | bounded census: `_accesses` ×3 (copyview, regions, views), `_split` ×3, `_match` ×3, `_kind` ×3, `_node` ×4 — each set either one mechanism (merge) or renamed to say how they differ | 2026-08-22 census of `deity_informant/tuneprog/` | copyview, regions, views | small | no two private helpers share a name unless they are one |

### 2.8 Boundaries (measured, not work)

| boundary | measurement |
|---|---|
| folding a copy nothing ran costs more than it saves: a silent copy adds the columns and the `switch (v)` with no second body to remove | Follin 17-19, 25, 27, 29 grow ~6 % statements, ~20 % blocks, 24–49 printed lines each; buys per-voice names and the coverage vector; `--no-merge` is the escape (`copymerge`) |
| an edge into another copy at a row that is not that copy's entry (*Automatas* `$16AB`) | lowering as `v += 1; goto` the template row is sound and folds it, but the merged body gets two entries, costing a `goto` and the `ad`/`ctrl` names — refused as the narrower rule says (`copyrows`) |
| a copy's stream runs past a jump into unreached code (the Knob's `$1167`), carried as an unverified row | rejected on the image: it shatters Ghouls' 3×237 families to 3×2 and breaks closure invariance on song 31; ending the stream there needs `jumptab`'s transfer facts, not the image alone (`siblings`, `jumptab`) |
| the phase of a repeating cascade is a convention where two readings tie exactly (*Automatas* `p_168C` `$172C` vs `$1734`) | pinned by `slack` then lowest base; refusing ties also refuses the two real 5-copy cascades (`siblings`) |
| a fold takes cells the naming plane had (the Knob's `$17B9`/`$17BC` → `b17B9[v + 3]`, Commando 2's `$54F8`) | measured in P1: the merged access's region does not keep the field names of the cells it unites (`copymerge`, `views`) |
| a hex row costs about 1.4x, compressed, the bytes it prints | Commando's data section carries 1,867 B (1,100 B `xz`) and grows the print's `xz -9e` by 1,568; `gt2-je-suis-linus` 5,174 B / 1,896 → +3,136, `sw-emomyst` 1,858 / 1,080 → +1,436. Three ASCII characters a byte recover to ~1.4× the binary's compressed size; the accessor lines and the block headers are the rest. A denser encoding would not be a listing (`datablock`) |
| a record block prints one row per record, whatever the stride | `jch-knob-at-night`'s stride-4 block is 8,576 records, 8,675 of the section's 15,900 rows over the 51. Packing records to a fixed row width was rejected: the column header then labels one group of several on the row (`datablock`) |
| the stack page is a second inexact direction of the store-granularity replay: an IR store wholly inside `$0100`–`$01FF` between the last counted store and the NMI instant replays the handler before it | needs the instruction index the trace records (the emitted program cannot count) or a shared stack store count (stack elimination removes); the no-hook converse was measured and rejected (JCH diverges at tick 6); narrow, undiagnosed in population (`nmi`, `interp`, `verify`) |
| a write to `$D000`–`$DFFF` with I/O mapped also writes the RAM under it (`tracevm.write`, `interp.iostore`) where the hardware writes only the chip | the honest model is two planes, chip and RAM beneath; unobservable in every exemplar, 3 tunes of 7,023 discriminate |
| the tracer counts CPU cycles where the sampler's clock also spends VIC DMA | 57–60 cycles per frame, +533 inside one Knob tick; free today, both sides framed by the interrupt, so a raster model is needed only if a comparison ever needs sub-frame time (`tracevm`, `machine`) |

## 3. Done

One line per struck row: title · PR/date · headline · record.

- copy folding by siblings · #234, 2026-08-17 · closure by siblings, group and per-phase views; Follin's voices fold
- the copy index as an IR value · #241/#242/#244, 2026-08-20 · Follin song 1 1,229 statements / 441 blocks → 671 / 254 as one `for v in 0, 1, 2`
- a copy that never ran a row keeps the target its own image names · #243, 2026-08-20
- bounded static closure of untaken directions · P2, #250, 2026-08-20 · `trap 'untaken'` 18/15/28/49 → 5/0/3/1 on four exemplars, off by default (`--closure static`) · design §3
- fold the sound-effect subtunes · #242 · 24 of Follin's 32 fold, 8 refuse on a cross-copy edge
- unverified statements marked per statement · #242
- fold the `--songs all` union · #242 · the union of a folded access is one region
- *Automatas*' row-advance blocks · P1, #248, 2026-08-20 · one 45-row body over 22 columns, by the copy index
- an edge leaving one copy for another refuses the family · P1, #248, 2026-08-20 · the rule held, ownership was the refusal; Follin's 8 one-voice effects and `$112A` fold
- a merged family's patched dispatch loses the static table closure · P1, #248, 2026-08-20 · per-copy enumeration, 3 → 39 arms on Follin song 1
- a merged loop prints as `while` over `copies_XXXX[...]` · #244, 2026-08-20 · `copyview.py`, `loops.copies` → `for v in 0..k-1`
- `unroll` substitutes copy 0's constants · P1, #248, 2026-08-20 · a constant that does not step keeps its literal
- k prologues make no `for` · Q1a, #253, 2026-08-21 · the prologues are the step (`loops._chain` proves the chain by assignment)
- a merged access unites its regions, roles lost · Q1b, #255, 2026-08-21 · two measured causes (`facts.sid_image` on constant addresses; block-local reach), +4 `cursor`, +30 `table` roles, none lost
- sibling discovery from block boundaries · Q2, #256, 2026-08-21 · candidate bases and the chain relation from the image; property tests over six seeds × four shapes
- periodicity proof by lcm · P2, #250, 2026-08-20, refuted · `period.py` classifies `periodic`/`state only`/`aperiodic`; Commando's pulse-width accumulators are the observable (41,898 of 48,192 tick write lists differ at the loop)
- second interrupt schedule (NMI + IRQ) · Q8, #272, 2026-08-22 · the CIA #2 NMI is the schedule's second entry; `cia.py`, `nmi.py`; 130 of the 195 class certify at 30 s · [prototype-nmi.md](prototype-nmi.md)
- the NMI refusal sized on evidence · Q5, #269, 2026-08-22 · a CIA #2 source can fire iff the accumulated ICR enables it and the timer runs; 547 refusals → 311 armed / 81 dead / 154 undecided / 1 tracer fault
- a CIA #2 latch as the play cadence · Q5, #269, 2026-08-22 · `_cadence` takes a CIA period only when it is CIA #1's
- sign-extension and flag-algebra printing · Q1b, #255, 2026-08-21 · `A + sext(T)`, `overflow(A - B)`
- index range for jump-table extents · P1, #248, 2026-08-20 · intraprocedural (Follin 23 vs 21 arms)
- `--songs all` resume state · P2, #250, 2026-08-20 · each subtune carries `{calls, stop, horizon}`
- printer memo invalidation with 16-bit views; `node_exprs` guard · P2, #250, 2026-08-20
- a subtune stopping on a period certified at the chunk boundary · Q3, #258, 2026-08-21 · `pipeline._certified` reads the witness (`ghouls-songs-all` 220,049 → 111,763)
- the transpose stride view · Q1b, #255, 2026-08-21 · `views.transpose_split` (JCH's three tracks as records)
- SID register offset from a per-track table · Q1b, #255, 2026-08-21 · `facts.voice_maps`; `sid[v].ad` for `sid.reg[5 + voice[v].b1740]`
- a loop body's joins are `goto` · Q1a, #253, 2026-08-21 · a region with one way out promotes; 7 → 0 `goto` on both V20 tunes
- comparing a tick's writes against a sampler · Q3, #258, 2026-08-21 · `grid.py` frames both sides by the interrupt; Knob 0 of 3,000 frames
- numba tracer · refuted 2026-08-22 · the compiled P-Code was 5–6 % of self time; see fast tracer
- stack elimination in S4 · #237, 2026-08-18 · `SP` absent from all exemplar `tuneprog.py`; the page outside the footprint both sides
- periodicity claims the footprint the program's stack status proves · #239, 2026-08-18
- an `RTI` entry tune is residual · Q4, #259, 2026-08-21 · the pushed frame is the entry's contract (`frames.contract`)
- `--until-period` stops before the page-inclusive repeat · Q4, #259, 2026-08-21 · the trace stops on the witness the stack status allows
- an installed handler chaining to the KERNAL epilogue never reaches `RTI` · #261, 2026-08-21 · the entry's vector decides its frame; 37/37 trace and verify, 34/37 certify · [prototype-kernal-entry.md](prototype-kernal-entry.md)
- 14 of the family crash on a `Call` with no arguments · #261, 2026-08-21 · the IR call graph has cycles; `wire.wire` is a fixpoint
- the `kernal` decision was written-vector precedence · #262, 2026-08-21 · the 6510 port's HIRAM decides; `machine.vector_gate` refuses `vector banked out`
- the CINV frame convention had no oracle guard · #262, 2026-08-21 · `test_oracle.py`: *A Mind Is Born* 0 of 3,000 frames differ
- the PSID speed flag is not in the cadence · #264, 2026-08-21 · host CIA at `$4025`/`$4295`, per subtune
- `trap switch`, 189 tunes · Q6, #270, 2026-08-22 · three front-end mechanisms (patched `JMP (ind)` operand, zero branch offset, copy-index step); 177 of 189 certify
- the closure boundary as a divergence, 78 tunes · Q6, #270, 2026-08-22 · both classes are the patched `JMP (ind)` pointer and the zero branch offset seen from the other side; `trap unverified` 47 + `trap untaken` 31 → 78 certified, 0 diverged
- P1 fold reach · #248, 2026-08-20
- the P-JCH V20 family package · #249, 2026-08-20 · the plan row that opened it
- P2 certificate accounting · #250, 2026-08-20
- P-JCH V20 family · #251, 2026-08-20 · both tunes complete, 0 divergences, five generic machine-model fixes
- the Q-packages · #252, 2026-08-21 · the remaining backlog grouped Q1a–Q4, order against the sweep
- Q1a structuring · #253, 2026-08-21
- Q1b views & naming · #255, 2026-08-21
- Q2 sibling discovery deep fix · #256, 2026-08-21
- Q3 sweep prerequisites · #258, 2026-08-21 · `grid.py`, witness-based horizons, one tune map
- Q4 = P3 stack residual policy · #259, 2026-08-21
- P-EQSAT prototype · #265, 2026-08-21 · the prints qualify (no metric worse, −1.5 % tokens), the cost does not (600 lines, ×3.6 CPU); keep `ranges.py` · §2.3 rows
- P-FLOOR Commando · #266, 2026-08-21 · the gap is storage typing, not algebra: 252 → 115 lines under four typings, `--eqsat` moves 8 tokens · [prototype-commando-floor.md](prototype-commando-floor.md)
- **u16 by cell pair** · #277, 2026-08-23 · the 16-bit view is keyed by two *cells* `(region, constant address)` that one index expression reaches at two constant bases, so one region may hold both halves; `word.py`'s store-keyed dataflow search (379 lines: `_parses`/`_sites`/`_plan`/`_match`/`_before`/`_rewrite`/`_subst`) is gone, replaced by `halves.py` (213, the byte shapes) and `word.py` (197, the pass and the flag naming). Rules: `(hi << 8) | lo` → one read, the carry chain `lo = x + y; hi = x' + y' + carry(x + y)`, the borrow chain with `1 - (a cmp b)` folded to the negated compare, the rotate `(lo >> 1) | ((hi & 1) << 7)`, and the `lo += 1; if lo == 0: hi += 1` diamond via `(x + 1) == 0` = `carry(x, 1)`. A half that is computed and not stored pairs through its read of the other cell; a stored half wins over a computed one; a pair straddling the I/O band is refused. Over the 51 certificates: tokens 178,576 → 172,757, program lines 19,824 → 19,813, statements 10,582 → 10,560, blocks 8,442 → 8,441, no tune worse on any of them; header rows 4,987 → 5,016 (a same-region pair now has a row of its own); `<< 8)` 865 → 422 and `carry(` 206 → 204 in the rendered prints. `pseudocode._recname` and the `(lo | hi << 8)` fallback are gone. Refused, measured: the SID register pair as one statement (§2.2) and `eqrules._select`, whose three diamond sites the cell-pair rule does not reach (§2.3). Recert 51/51, 0 mismatched
- **the expression layer without an e-graph** · #278, 2026-08-23 · three steps. The two identity folds: `x ^ $FF` prints as `~x` from `pseudocode` (`ir` has no unary, so the fold has nowhere to land), and `1 - (a cmp b)` is `halves.zerofold`'s third left-literal identity beside `carry(x, 0)` and `0 | x`, with `halves._notc` reduced to `zerofold(1 - c)` so one function knows the spelling; it stays out of `idioms.fold`, which S4 runs, because the certified IR has 56 of the shape. `halves._diff` gained `_oneminus`, which reads either spelling of a borrow, and `_sub` now returns its carries normalised like `_add`'s, without which Automatas' chains and `word._flags` stop matching. `gated.py` (134 lines) is the range-gated pass: one `ranges.cell_ranges` per `present()`, `expr_range` at each node, run after `texture.propagate` -- `x & j` is `x` (81 sites) or 0 (72) where the interval says so, a comparison against a literal is decided (50), a constant condition becomes a `goto`, and the if-diamond whose arms are `y` and `y ∓ 1` is the borrow it is (3 sites), `eqsat._diamonds` moved over as `gated.diamonds`. The three diamond sites #277 could not reach print exactly as `--eqsat` printed them (`sw-emomyst`, `sw-end-of-the-world`, `ghouls-song21` -- the last but for the cell's name), and no diamond of that shape is left on any of the 51. Then `--eqsat` retired: `eqsat.py`, `eqrules.py`, `test_eqsat.py`, the flag, `texture.clean`'s branch, the `eqsat` extra and `egglog`; `pipeline.structure_json`/`_node` moved to `structure.py` as `structure_json`/`_json`, which puts `pipeline.py` at 484. Net −400 source and −58 test lines over the three commits. Over the 51 certificates: tokens 172,757 → 171,405, program lines 19,813 → 19,769, statements 10,560 → 10,542, blocks 8,441 → 8,358, header rows 5,016 → 5,015, no tune worse on any of them; `present()` CPU 21.5 → 21.1 s, so `cell_ranges`' 3–4 fixpoint rounds are free at this scale; every print byte-identical across the retirement commit. Two refusals, measured: the post-increment compare `(v + c) cmp k` fires 0 times over the 51 -- 63 of its 224 sites are `(v + 1) == 0`, which is the carry itself, and all 161 others are the merged loop's copy index, whose domain belongs to `copyview` and whose advance test `loops`/`unroll` read -- and `x - x` is one site on one tune (−39 tokens). Recert 51/51, 0 mismatched
- **control shape** · #279, 2026-08-23 · three commits. `live.dead` is the plain backward question over the view (51 statements on its own); the values the plan named are not dead in the IR, so `cells.forward` reads the value a cell's *own update* stores as that cell, which is what `Printer.mem` already prints and what lets liveness see a `DEC`/`INC` pre-value die -- restricted to an update because a general store-to-load forward makes one cell depend on another and unfolds Ghouls 4's `copy_2[2]` view, and placed after `word.fold16` because before it `carry(x + y)` under `Store(lo, x + y)` becomes `carry(lo)` and Ghouls 17's `w0032` pair unfolds. `texture.py` 566 → 308 with `cells.py` (275) taking the three readings of one cell. `live.coalesce` retires the copies phi elimination leaves at a join, by live-range interference (`live.live_out`), refusing the induction chain a latch copy joins, running after `fold.outline` (which refuses a candidate by the values that cross it) and taking its candidates in sorted block order -- `Proc.blocks` is hash-order dependent and a greedy choice over it made the print differ between processes. A `for` header states where its index starts, so `hide` is every `For`'s stepping lets. A branch whose one direction is a bare `trap 'untaken'` prints as a `# untaken: <condition>` mark on the covered line, with the count in `meta`: 1,182 over the 51, exactly the traps before, of which 1,014 were one-line arms. And every version of the C register prints `carry` (`carry_2`.. where two are live), 199 `cN` → 0. Over the 51 certificates: tokens 171,405 → 166,300, program lines 19,769 → 18,339, statements 10,542 → 10,211, blocks 8,358 unchanged, header rows 5,015 → 5,065 (the `untaken` row), no tune worse on any of them; `present()` CPU 21.0 → 21.9 s; `goto` 651 and procedures 456 unchanged. Three refusals, measured: the DEC/BNE counted `for` (§2.4, the 169 loops counted by why), `carry`/`borrow` by the defining op (no C temporary over the 51 is defined by the borrow spelling `a < b`), and the single-consumer phi *sunk* into the arms, which coalescing subsumes without duplicating the consumer. Recert 51/51, 0 mismatched, every `tuneprog.S4.json` byte-identical
- **data printed as data, with its reach** · #280, 2026-08-23 · `## data` replaces `## const` (`datablock.py`, 240 lines): every run of storage the program reads prints its own bytes. A region's cells are its extent, or the columns its stride marks off; its reach is the union of its accessors' envelopes over them, and a cell some store's envelope reaches is state, not data -- `partition._kind`'s rule read per byte, which is why Commando's `FREQ` prints although the pitch-104 overrun (P-FLOOR §5) fuses it into a `state` region. A store reaches only *its own* region's cells inside its envelope, so a column's store does not blank the columns beside it. Regions whose extents overlap, or that one frequency layout names, are one block: T5889/T588B print once, a `lo|hi` table's two columns are one entry list. Layouts come from the S6 names alone -- `recover._freq_layout` now returns `(columns, entries, cut)` and `names.freq` keys it by the regions it named -- a note table as 16-bit entries, equal-stride columns as one row per record under their field names, everything else as 32-byte hex rows; then one line per distinct *printed* accessor with its procedures, which is why `printer.render` renders the procedures first (`Printer.site` collects the form `Printer.load`/`store` chose). Commando: 1,867 of the 1,941 bytes its envelopes reach are in the block (74 left are cells a store reaches), `FREQ` as 80 u16 entries, `rec2` as 13 instrument records, the pattern block as 41 hex rows. `tools/tuneprog_floor.py` now calls `datablock.reach_bytes` for its `data` row, so tool and print count one thing: 1,942 → 1,941, the byte being `const_5526`, which no S4 accessor reads. Over the 51 certificates: tokens 166,300, program lines 18,339, statements 10,211, blocks 8,358 all unchanged on every tune; header rows 5,065 → 4,737, 15,900 data rows carrying 189,194 bytes; `present()` CPU 22.2 → 21.9 s. `xz -9e` of `tuneprog.md`: Commando 2,944 → 4,512 against the load band's 2,548 and the data's own 1,100; `gt2-je-suis-linus` 5,156 → 8,292; `sw-emomyst` 7,072 → 8,508. Two refusals, measured: an `init_constant` region (15,249 bytes over the 51 -- the pre-init image is not what the tick reads, §2.1) and a region no accessor reaches. `partition._refs` is now `partition.refs`, which `datablock` shares. Recert 51/51, 0 mismatched, every `tuneprog.S4.json` byte-identical
- region typing by accessor-shape partition · #274, 2026-08-22 · `partition.py` re-types the S6 copy: the narrow claim wins, the overrunner keeps the fused region and its asserted bound, a part no store reaches is `const`, and stride-1 regions of one origin merge. Tokens 178,354 → 175,876, program lines 18,970 → 18,924, statements 10,612 → 10,582 over the 51 certificates, no tune worse on any of them, header rows 4,134 → 4,324 (the storage is now named; a cut parent keeps the fused range it asserts, so its row and its parts' rows overlap). Metrics over `printer.render(..., pcs=False)`: program-section tokens `\$?\w+|\S`, non-blank program lines, `sum(len(b.stmts))` and `sum(len(p.blocks))` over the view, and the meta+state+const+inputs rows. Commando `FREQ[` 55 printed lines → 13, `FREQ[195]` gone; *Deflektor*'s 173-byte overlap fusion is `voice[3]` with 25 fields. Four refusals, each added after a print it made worse. The `word._match` companion diagnosed and fixed: an 8-bit operand added to a word has an implicit zero high half, which neither `_parses` nor `_operand` read. Recert 51/51, no field moved
- Q5 NMI refusal · #269, 2026-08-22
- Q6 `trap switch` · #270, 2026-08-22
- Q7 fast tracer · #271, 2026-08-22 · the site is the VM's cache key; 3.0–3.5× (480–580 k instr/s), `Trace` byte-identical over 82 traces, recert 50/50
- Q8 NMI prototype · #272, 2026-08-22 · 181 of 7,023 have a dispatching NMI beside a play entry (1.3 % weighted); `jch-easy-does-it` 1,799 ticks, 199,514 preemptions, 0 divergences
- campaign at survey scale · #267, 2026-08-21 · [survey-tuneprog.md](survey-tuneprog.md)

Design deltas the design doc does not state (v3 §3):

- regions need per-phase views: one-loop init clears merge every field into one region; the view is built from play-phase accessors (`views.py`, #234), and the accessor-shape partition (#274) re-types the S6 copy over the same rule.
- a value that is an observable cannot be reduced away: Commando's per-voice pulse-width accumulators make both subtunes aperiodic at any practical horizon; `period.py` classifies rather than proves.

## 4. Process

- One Opus agent per stage in its own worktree (`PYTHONPATH` pinned), the certificate as acceptance, a read-only reviewer between stage and merge; the reviewer refutes new tunable constants, duplicated mechanisms, tests that encode an exemplar rather than an invariant, modules over 500 lines.
- Every module ≤ 500 lines; a new mechanism names the view/pass vocabulary it belongs to (views over regions, the naming plane, structural passes proven by alpha-equivalence) or is not added.
- No new role/view heuristic without a hermetic snippet test and two families that need it, or one family plus a survey count.
- The certificate is the only acceptance test; a presentation change leaves `tuneprog.py` byte-identical or explains the change; `tools/tuneprog_recert.py` green before and after; a consolidation pass after every three stages.
- Prototype docs are records; the living documents are the design, this plan and `tuneprog.md`.
- Every brief carries the global directives (no tuning constants; black/pylint/xdist; coverage > 85 %; 60 s CPU per script, `--budget`/`--resume`); each stage ends with a "what remains" list that becomes §2.

Execution order of the open packages, each a dependency of the next:

1. **`unroll`'s row alignment** — §2.2's two open rows and §2.4's expression-domain `for` are all the same lever: a loop the printer states by its header rather than its body.
2. **complexity consolidation** (§2.7) — after the three view stages exist.
3. **the init-written table** (§2.1) — needs the post-init image on the region, which is a front-end change.
4. **P4 Ghidra oracle** (§2.6) — independent; interleave anytime.

Deliberately not now: 2SID/3SID (0.6 %), ROM-dependent tunes, audio rendering, BASIC programs.
