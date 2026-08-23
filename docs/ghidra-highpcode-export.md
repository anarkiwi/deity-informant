# Ghidra high-P-Code export: an independent baseline for the tuneprog

Ghidra is fed the same dynamic facts the tuneprog decompiler uses (post-init image, SMC cell set, entry
procedures, resolved computed jumps, regions) and produces its own high P-Code and C. Three automated oracles
-- complexity, coverage, semantics -- compare the two sides and can fail with a reason; the nightly workflow
runs all three over every committed certificate.

## 1. Mechanism: SLEIGH context constructors == `lift.py` residualisation

`tuneprog/lift.py` abstracts SMC mechanically: the trace gives the written instruction bytes (`Trace.cells`),
byte provenance gives the constant varnode each byte feeds, and S2a replaces every constant whose provenance
hits a cell with a `LOAD` from the cell's address. `ghidra/6510/smc.py` generates the same transformation as
SLEIGH constructors, applied during decode.

| context bit | applies to | semantics |
|---|---|---|
| `smc_imm` | every immediate opcode (legal + illegal) | `tmp:1 = *:1 (inst_start+1)` instead of the decoded constant |
| `smc_addr` | `zp`/`zpx`/`zpy`/`abs`/`absx`/`absy`/`(zp,X)`/`(zp),Y`, and the `JMP (ind)` pointer | the effective address is computed from `*:1`/`*:2 (inst_start+1)` |
| `smc_ctrl` | `JMP abs`, `JSR abs`, all eight relative branches | `goto [*:2 (inst_start+1)]`, `call [...]`, `inst_next + sext(*:1 (inst_start+1))` |
| `smc_var` | an opcode cell whose other variant is `RTS` | a guarded early `return` in front of the instruction's own semantics |

The constructors carry an extra context constraint, so they are strictly more specific than the stock ones and
win exactly where the analyst sets the bit; every field is `noflow`, so a value applies to one address.
Instruction lengths and addresses are untouched: no relocation, no overlay, no byte patching.

Build-time changes against the stock `6502.slaspec` (fetched at build time, never committed):

* `build.py` injects `@include "6510_context.sinc"` after the last `define register`; SLEIGH requires context
  definitions before the first constructor.
* Operand subtables (`OP1`, `OP2`, `OP2LD`, `OP2ST`, illegal-RMW `OP3`) cover every legal memory instruction
  plus SLO/RLA/SRE/RRA/DCP/ISC in one constructor per addressing mode. Illegal opcodes that spell out their
  own operand get twins by rewriting `6510_illegal.sinc`; the SLEIGH compiler rejects a wrong operand size.
* Two hardware facts the stock 6502 spec has wrong, both patched in `smc.PATCHES` (`_in_ctor` replaces
  inside one constructor or macro). `JSR`/`RTS` store and return to `inst_next`, one byte off, and a 6510
  program can read its own return address off the stack: `inst_next-1` / `return [tmp+1]`. And
  `subtraction_flags1` sets `C` to the *borrow*, the 6510's complement
  ([#3189](https://github.com/NationalSecurityAgency/ghidra/issues/3189), open) — `SBC`, `ISC` and `SBX` are
  its three users and the flip is one `!= 0` to `== 0`. `tests/test_smc_sleigh.py` pins the stack convention
  at the P-Code shape level and the borrow by evaluating the raw P-Code against `lift.py`'s own `SBC`.

### What Ghidra needs on top of the SLEIGH change

Two Ghidra behaviours fold an SMC cell back to a constant before it reaches high P-Code:

* `DecompileCallback.encodeFunction` declares every address inside a function body CONSTANT, and an operand
  cell is inside a body by construction. `ExportHighPcode.freeCells` subtracts the cell bytes from each
  function body -- they are data, not code -- which turns `screen = A` back into `*smc_100a = A`.
* The loader's block must be writable or `MappedEntry.getMutabilityOfAddress` reports CONSTANT for the whole
  image. C64 RAM is writable, so the export sets it.

`6510.pspec` declares `$D000-$DFFF` volatile, so VIC/SID/CIA reads are not folded from the image bytes.

## 2. Running it

```bash
deity-informant tuneprog TUNE.sid --out OUT --seconds 20 --ghidra-facts
python3 tools/tuneprog_ghidra.py OUT --dst OUT/ghidra      # or, from a finished dir

docker build -f Dockerfile.ghidra -t di-ghidra .
docker run --rm -v "$PWD:/src" -e FACTS_DIR=/src/OUT/ghidra -e OUT_DIR=/src/OUT/gout \
    --entrypoint sh di-ghidra -c "sh ghidra/6510/headless/run.sh export"
docker run --rm -v "$PWD:/src" -e FACTS_DIR=/src/OUT/ghidra -e OUT_DIR=/src/OUT/gout \
    --entrypoint sh di-ghidra -c "sh ghidra/6510/headless/run.sh emulate"

python3 tools/tuneprog_ghidra.py OUT --compare OUT/gout    # the join + the flags
```

`SMC=0` exports without the SMC facts (the A/B baseline). `docker run --rm di-ghidra` with no arguments runs
the four CI smoke tests: illegal-opcode decode, hello-world SMC export, complexity/coverage oracle, semantic
oracle.

`.github/workflows/nightly.yml` runs the same three oracles over every committed certificate, four shards of
`tools/tuneprog_recert.py --shard I/4 --ghidra-dir DIR`: the replay writes the facts (`--ghidra-dir` implies
`--ghidra-facts`), the job runs `export` and `emulate` per certificate in the cached image, and the last
recert invocation joins them and exits 1 on any `ours_bigger`. One certificate's export is 13 s and its
emulate 9 s, so 51 are ~19 min of headless Ghidra, ~5 min a shard, against ~500 CPU-s for the whole replay.

## 3. The comparison

Twenty seconds of music per tune, one certified pipeline run each (no divergence), against one headless Ghidra
run each. Columns: sites = distinct executed instruction addresses (Ghidra's counts only those inside a
function body); raw ops = `Instruction.getPcode()` summed over them vs `LiftedSite.ops`; high =
`HighFunction.getPcodeOps()` vs S4 IR statements; C = decompiled C lines vs `tuneprog.md`'s program section.

| tune | sites (G/us) | raw ops (G/us) | high ops / S4 stmts | C / md lines | gotos (G/us) | unresolved | dropped blocks | decompile |
|---|---|---|---|---|---|---|---|---|
| Automatas | 582/615 | 1995/2177 | 6112 / 736 | 837 / 146 | 0/0 | 0 | 0 | 645 ms |
| Commando (song 1) | 320/353 | 1302/1501 | 3583 / 325 | 346 / 43 | 2/0 | 0 | 0 | 358 ms |
| GoatTracker (Je suis Linus) | 304/422 | 1318/1725 | 435 / 493 | 258 / 144 | 0/0 | 0 | 105 | 490 ms + 1 crash |
| Ghouls'n'Ghosts (song 1) | 664/718 | 2018/2743 | 8203 / 498 | 1054 / 481 | 76/21 | 0 | 6 | 1,089 ms |

Headless Ghidra wall time (JVM start, import, analysis, decompile): 13 s per tune, and 9 s for the emulate
pass; the decompile column is `DecompInterface` time only. The md-lines column is a 20 s horizon, not the
certificate's.

### A/B: the same run without the SMC facts (`SMC=0`)

| tune | high ops (with/without) | C lines | dropped blocks | decompiler warnings |
|---|---|---|---|---|
| Automatas (73 cell sites) | 6112 / 2515 | 837 / 656 | 0 / 65 | 10 / 125 |
| Commando (0 cell sites) | 3583 / 3583 | 346 / 346 | 0 / 0 | 0 / 0 |
| GoatTracker (14) | 435 / 204 | 258 / 275 | 105 / 128 | 113 / 130 |
| Ghouls'n'Ghosts (22) | 8203 / 147 | 1054 / 87 | 6 / 0 | 34 / 10 |

Commando has no SMC cells and is bit-identical in both arms: the control works.

### Reading it

* **Resolved.** All four decompile with zero unresolved control flow; cells become named globals (`smc_100a`,
  `smc_12ef`) read and written like variables. 79 of Automatas' 87 cell bytes and 20 of Ghouls'n'Ghosts' 29
  appear that way in the C, against 0 and 1 without the facts; GoatTracker's 3 of 14 is the crashed
  `row_apply`, where the rest live. The four `JumpTable.writeOverride` calls on GoatTracker and
  Ghouls'n'Ghosts become switches over our target sets; S6 region names carry over (`T1900[cursor_12CE]`).
* **Bigger.** Ghidra's high P-Code is 8.3x (Automatas), 11.0x (Commando), 16.5x (Ghouls'n'Ghosts) our S4
  statement count; its C is 1.8-8.0x our printed form. The tuneprog is trace-exact, not
  sound-for-all-inputs: untaken arms are `trap`, registers dead in the trace are gone, the per-call schedule
  and periodicity collapse whole loops.
* **Smaller.** GoatTracker is the one row below ours (435 vs 493), classified rather than credited:
  `row_apply` (264 executed sites) kills the decompiler process in *both* arms, so the SLEIGH spec is not the
  cause, and its tick drops 105 blocks as unreachable.
* **Inexpressible.** Opcode cells beyond the `RTS` pair (Automatas' `$10B8`/`$10BF` `ADC`<->`SBC`) stay
  residual: one address decodes as one instruction. Trace closure, the per-call schedule, the pinned inputs,
  periodicity and the differential certificate are properties of a run, and have no representation in a
  Ghidra program.

## 4. The three oracles

All three run through the same Docker entry and write JSON next to the export.

**Complexity** (`stats.json` + `comparison.json`). Per procedure, both sides: executed sites, raw P-Code ops,
high P-Code ops vs S4 statements, C lines vs `tuneprog.md` lines, gotos, unique temporaries, plus Ghidra's
diagnostics (`DecompileResults` errors, `halt_baddata`/`switchD`, "Removing unreachable block"). Where Ghidra
reports clean flow *and* our certificate holds, our statements-per-site and gotos-per-site must not exceed
Ghidra's by more than `--tol` (default 1.5x); a violation is `ours_bigger` and localised. Every other
procedure is `ok`, `ghidra_lead`, `ghidra_incomplete` or `ghidra_partial`. Clean flow means no unresolved
control flow, no dropped block, no `DecompileResults` error *and* some high P-Code: a body over executed
sites that produced none is nothing to compare, which is what "Decompiler process died" and "Low-level
Error: Overlapping input varnodes" leave behind (GoatTracker's `row_apply`, *Playful Professor*'s `p_6200`,
both SID Wizard tunes).

No flag on the four exemplars. Over the 51 certificates two survive, both standing:

* *Deflektor*'s `init`, 2 `goto` against Ghidra's 0 over the same 55 sites (51 printed lines to 35 C lines).
  They are the copy fold's cross-copy edges inside `for v in 0, 1, 2`; Ghidra does not fold and writes the
  three copies out flat. The measured refusal is [tuneprog-plan.md](tuneprog-plan.md) §2.8.
* *Alien 3*'s `tick`, 0.80 statements/site against 0.47 ops/site over 15 sites. Our twelve statements keep
  the three register saves to `$01FA`-`$01FC` that Ghidra's frame analysis folds into one `uStack0000 =
  param_1`; the tune is `stack: residual` (§2.5's row). On the like-for-like measure we are the smaller
  side, 8 printed lines to 15 C lines.

`tuneprog_recert.py --known CERT:ENTRY` names a flag that is a recorded row, so the nightly gates on a flag
beside those two rather than on their standing.

Ghidra's bodies are disjoint and our procedures are cloned per entry, so the two sides align only as address
sets: each `per_function` row carries the executed addresses its body owns (`pcs`), and `ghidra_partial` is a
body that misses one of our executed sites, named, however the counts compare — a body holding *more* sites
than ours is the merge case, not a cover. `comparison.json`'s `alignment` states both shapes: `merged` is a
Ghidra body holding sites from more than one of our procedures, `clones` our procedures sharing a site.
GoatTracker's `row_apply` (`$11A4`) merges `p_1130` and `p_11A4`, which are also its clone pair; Automatas'
`p_1006`/`p_1022` and Commando's `p_500C`/`tick` are clone pairs Ghidra keeps apart; Ghouls'n'Ghosts has
neither. Partial rows: 2 of 8 on Automatas, 2 of 3 on Commando, 3 of 13 on GoatTracker, 1 of 4 on
Ghouls'n'Ghosts.

**Coverage** (`coverage.json`). Ghidra's static reachability from our entries and jump-table references, minus
the trace's executed sites: the code the horizon and subtune selection never exercised, i.e. the `trap` arms.

| tune | executed | reachable | uncovered sites | ranges | classification |
|---|---|---|---|---|---|
| Automatas | 615 | 672 | 57 | 13 | 13 untaken branch |
| Commando | 353 | 506 | 153 | 16 | 11 untaken branch, 3 unentered block, 2 block tail |
| GoatTracker | 422 | 447 | 25 | 6 | 6 untaken branch |
| Ghouls'n'Ghosts | 718 | 1149 | 433 | 47 | 46 untaken branch, 1 unentered block |

Ghouls'n'Ghosts' largest hole is a function at `$7316` that Ghidra reaches but song 1 never enters; other
subtunes do (the `--songs all` case). No `table_arm` ranges remain on either patched-dispatch tune: the S2
static closure enumerated those targets and Ghidra reached exactly them.

**Semantics** (`emulate.json`). Ghidra's P-Code emulator runs the post-init image for eight play calls with
the entry registers the trace pinned, the post-init CPU state and the per-call input sequence the trace
recorded (`emulate.reads`: each volatile read's `(pc, address, value)`, written into memory at that pc before
the step). It records every executed pc and the ordered sequence of `$D400-$D418` register *changes*, which
is what the facts carry on our side too — both reduced by the same rule from the same post-init bytes, so a
replay reaching the same end state by a different route is a disagreement:

| tune | steps | pcs outside the trace | SID change sequence | agree |
|---|---|---|---|---|
| Automatas | 1277 | 0 | matches | yes |
| Commando | 2127 | 0 | matches | yes |
| GoatTracker | 3006 | 0 | matches | yes |
| Ghouls'n'Ghosts | 1475 | 0 | matches | yes |

The two disagreements the earlier run carried were one cause, and it was not the inputs. Over the 20 s traces
GoatTracker and Ghouls'n'Ghosts consume exactly one volatile input each, an init-phase `entry_reg` read the
post-init image already carries; Automatas' 2,227 `$D012` reads are all init's wait loop; only Commando reads
anything at play time (1,002 `entry_reg`). Our own `PcodeVM`, run back to back from the post-init image
exactly as `EmulateTrace` runs, reproduced all four change sequences, so the environment was not it either.
The first differing step, register state included: Ghouls'n'Ghosts at call 0 step 631, `$680A` `BCS $6810`
with `A = $FF` after `SEC; SBC $73` — the hardware clears `C` on a borrow, Ghidra set it, and the tick then
took a branch the trace never did (`$681C`, the 64 unknown pcs) and wrote `$D416 = $3F` for `$1F` at `$684E`;
GoatTracker at call 1 step 448, `$130C`, the second `SBC $1543,Y` of a 16-bit subtract, `A = $FF` for `$00`
with the borrow inverted. Both are §1's `subtraction_flags1` patch (ghidra#3189); with it the emulator agrees
with all four traces step for step, registers included (Ghouls'n'Ghosts' 4,154 steps become the 1,475 our own
VM runs).

Over all 51 certificates: 46 agree, 5 do not, and none of the 5 is a Ghidra defect. Four are the entry-frame
limit below — the tick of *Jodler*, *Playful Professor*, *Alien 3* and *Easy Does It* is an installed handler
whose frame is an interrupt frame, so it never returns to the sentinel a fake `JSR` pushed and walks into
`$FFFF`, `$FF00` (the KERNAL stub) or `$0100` (the stack page it just popped). The fifth,
*I Could Eat a Knob at Night*, is the oracle's model of a call: our own `PcodeVM`, run back to back from the
post-init image under the same conditions, disagrees with the trace in the same way from call 0, so the cold
call is not the machine's tick 0. Both are reported, not enforced.

Scope limits of this oracle:

* For the 105 illegal opcodes it is not independent: the SLEIGH `.sinc` and the Python lifter encode the same
  table (`tests/test_coverage.py` guards that they stay equal), and sidplayfp is the independent one. For the
  legal set it is a third oracle beside py65 and sidplayfp.
* It does not validate the SMC constructors: Ghidra's emulator decodes from live memory with the flowing
  context, so `noflow` per-address database values never reach it, and the stock constructors read the
  modified bytes anyway. `tests/test_smc_sleigh.py` pins them at the P-Code shape level against `lift.py`.
* `SBC`'s carry is no longer independent either, `smc.PATCHES` having brought it to the hardware's rule and
  so to `lift.py`'s. The patch is justified by the 6510, not by agreement: our lifter is a separate table
  (`jennings.opcodes`), the stock spec's own `CMP` uses the right rule two constructors away, and the defect
  is upstream and open.

## 5. Limits

* Opcode cells whose alternative is not `RTS` are residual; overlay address spaces are the other known route,
  at the price of manual control-flow resolution between overlays.
* Ghidra function bodies are disjoint and our procedures are cloned per entry, so per-procedure rows only
  compare where the entry addresses match and Ghidra's body covers our executed sites as a set; the oracle
  labels the rest `ghidra_partial` and names where those sites went (`alignment`) rather than scoring them.
* The export creates labels and comments, not structure data types for stride views: region bases can sit
  inside executed code, and defining data there breaks disassembly.
* The decompiler inlines thunks and tail calls, so a Ghidra function's high P-Code can include a callee's;
  totals are the safer comparison.
* `EmulateTrace` single-steps, enters the tick as a subroutine and stops at a pushed sentinel, and caps at
  400k steps per call (a call that hits the cap ends the run rather than spending the cap on each of the
  rest). A tick whose frame is an interrupt frame -- an installed CINV handler, which chains to the KERNAL
  epilogue and returns by `RTI` -- never reaches that sentinel: 4 of the 51. A second entry (the CIA #2 NMI)
  is not emulated at all, so a two-entry program's calls are the tick's alone.
* Eight back-to-back calls from the post-init image is not the machine's first ticks on every tune: one of
  the 51 diverges from call 0 under our own VM as well as Ghidra's, so the model of a call, not the
  emulator, is the limit there.

## References

**Overlay address spaces** — the other route to "one address, several contents"; ours keeps one address space,
so cross-block flow is never resolved by hand.
[`OverlayAddressSpace`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/address/OverlayAddressSpace.html) ·
[`Memory`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/mem/Memory.html) ·
[`Program`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/listing/Program.html) ·
[`space.hh`](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Decompiler/src/decompile/cpp/space.hh) ·
[Memory Map help](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Base/src/main/help/help/topics/MemoryMapPlugin/Memory_Map.htm)
("some limitations in conjunction with decompilation and analysis").

**Context registers** — the standard mode switch; those select an instruction set, ours selects where an
operand comes from. Ghidra's stock
[6502 module](https://github.com/NationalSecurityAgency/ghidra/tree/master/Ghidra/Processors/6502) has no
context register.
[SLEIGH 6.4](https://ghidra.re/ghidra_docs/languages/html/sleigh_tokens.html) (`define context`, `noflow`) ·
[SLEIGH ch.8](https://ghidra.re/ghidra_docs/languages/html/sleigh_context.html) ·
ARM `TMode` ([ARM.sinc](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Processors/ARM/data/languages/ARM.sinc),
[ARMtTHUMB.pspec](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Processors/ARM/data/languages/ARMtTHUMB.pspec)) ·
[ghidra-65816](https://github.com/achan1989/ghidra-65816) (`ctx_MF`/`ctx_XF`/`ctx_EF`) ·
[`ProgramContext`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/listing/ProgramContext.html).

**P-Code injection** — the same effect in Java; a context constructor needs no per-program Java and no
analyser hook.
[`PcodeInjectLibrary`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/lang/PcodeInjectLibrary.html) ·
[`InjectPayload`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/lang/InjectPayload.html) ·
[cspec.xml](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Decompiler/src/main/doc/cspec.xml)
(`<callfixup>`/`<callotherfixup>`) ·
[Guide to P-code Injection](https://swarm.ptsecurity.com/guide-to-p-code-injection/).

**Ghidra's own position on SMC.**
[#5871](https://github.com/NationalSecurityAgency/ghidra/issues/5871) (open: a 6502 `JSR` overwriting its own
operand fails the processor tests) ·
[#3392](https://github.com/NationalSecurityAgency/ghidra/issues/3392) (open: data inside an instruction is
"not something Ghidra can currently handle") ·
[#8888](https://github.com/NationalSecurityAgency/ghidra/issues/8888) ("Ghidra's static analysis is generally
not sensitive to time") ·
[#6799](https://github.com/NationalSecurityAgency/ghidra/issues/6799) (the decompiler "is intentionally left
bound only to the Program database ... we allow the user to manually capture dynamic data back into the
Program database" — what the facts export does) ·
[#6651](https://github.com/NationalSecurityAgency/ghidra/discussions/6651) (context register recommended for
banking) · [#4321](https://github.com/NationalSecurityAgency/ghidra/discussions/4321).

**Emulation-driven analysis** — all materialise one byte-state by patching bytes back; we never patch a byte,
the cell stays a variable.
[GhidraEmulatorUI](https://github.com/cslamber/GhidraEmulatorUI) (COMMIT writes emulator bytes into the
listing) · [GhidraEmu](https://github.com/Nalen98/GhidraEmu) (does not persist them) ·
[`EmulatorHelper`](https://ghidra.re/ghidra_docs/api/ghidra/app/emulator/EmulatorHelper.html)
(`enableMemoryWriteTracking`/`getTrackedMemoryWriteSet`, what the semantic oracle drives) ·
[`PcodeEmulator`](https://ghidra.re/ghidra_docs/api/ghidra/pcode/emu/PcodeEmulator.html) ·
[EmuX86DeobfuscateExampleScript](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Base/ghidra_scripts/EmuX86DeobfuscateExampleScript.java)
(writes results back as comments only).

**6502/C64 in Ghidra.** [c64_ghidra](https://github.com/c64cryptoboy/c64_ghidra) is the closest prior art: an
overlay-capable C64 loader plus `c64PcodeEmulation.py` ("our only reason to write emulator memory back to the
program memory is to support self-modifying code", `clearListing` then `setByte`) ·
[Disassembling Crossroads, part 2](https://dansanderson.com/mega65/crossroads-part-2/) (SMC found by
inspection, overlays for the `$D000` I/O window) ·
[GhidraNes](https://github.com/kylewlacy/GhidraNes) and [GhidraBoy](https://github.com/Gekkio/GhidraBoy)
(overlay-per-bank, both documenting the manual cross-bank flow cost).

**Academic** — none produce a trace-exact program with a per-call differential certificate.
[Certified Self-Modifying Code](https://flint.cs.yale.edu/flint/publications/smc.html) (Cai/Shao/Vaynberg,
PLDI 2007), Hoare logic where code is mutable data ·
[CoDisasm](https://doi.org/10.1145/2810103.2813627) (Bonfante et al., CCS 2015), *wave* decomposition, one
maximal segment per unmodified region (ours is the opposite move: cells as variables, no waves) ·
[Verified abstract interpretation ... self-modifying code](https://doi.org/10.1007/978-3-319-08970-6_9)
(Blazy/Laporte/Pichardie, ITP 2014), a Coq-verified sound CFG ·
[A Model for Self-Modifying Code](https://doi.org/10.1007/978-3-540-74124-4_16) (Anckaert et al., IH 2006),
state-enhanced CFG with edges conditional on the target's byte state ·
[LTL Model Checking of Self Modifying Code](https://arxiv.org/abs/1909.12635) and
[Reachability Analysis of Self Modifying Code](https://arxiv.org/abs/1909.12626) (Touili/Ye), self-modifying
pushdown systems ·
[Hybrid Analysis and Control of Malware](https://doi.org/10.1007/978-3-642-15512-3_17) (Roundy/Miller, RAID
2010), static CFG plus selective dynamic instrumentation.
