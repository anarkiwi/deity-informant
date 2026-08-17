# Ghidra high-P-Code export: an independent baseline for the tuneprog

Feed Ghidra the same dynamic facts the tuneprog decompiler uses -- post-init image, SMC cell set, entry
procedures, resolved computed jumps, regions -- and let its own decompiler produce high P-Code and C. Not a
second decompiler: a mature one, fed identical facts, as the yardstick for "is the tuneprog compact, exact and
cheap?", plus three automated oracles (complexity, coverage, semantics) that can fail and say why.

## 1. Mechanism: SLEIGH context constructors == `lift.py` residualisation

`tuneprog/lift.py` abstracts self-modifying code mechanically: the trace says which instruction bytes the play
routine writes (`Trace.cells`), the lifter's byte provenance says which constant varnode each instruction byte
feeds, and S2a replaces every constant whose provenance hits a cell with a `LOAD` from the cell's own address.
`ghidra/6510/smc.py` generates the same transformation as SLEIGH constructors, so Ghidra performs it while
decoding:

| context bit | applies to | semantics |
|---|---|---|
| `smc_imm` | every immediate opcode (legal + illegal) | `tmp:1 = *:1 (inst_start+1)` instead of the decoded constant |
| `smc_addr` | `zp`/`zpx`/`zpy`/`abs`/`absx`/`absy`/`(zp,X)`/`(zp),Y`, and the `JMP (ind)` pointer | the effective address is computed from `*:1`/`*:2 (inst_start+1)` |
| `smc_ctrl` | `JMP abs`, `JSR abs`, all eight relative branches | `goto [*:2 (inst_start+1)]`, `call [...]`, `inst_next + sext(*:1 (inst_start+1))` |
| `smc_var` | an opcode cell whose other variant is `RTS` | a guarded early `return` in front of the instruction's own semantics |

The constructors carry an *extra* context constraint, so they are strictly more specific than the stock ones
and win exactly where the analyst sets the bit; every field is `noflow`, so a value applies to the one address
it is set at. Instruction lengths and addresses are untouched, so state that lives inside code stays where it
is -- no relocation, no overlay, no byte patching.

Three build-time details make this work with the *stock* `6502.slaspec` (Ghidra's file, fetched at build time,
never committed):

* SLEIGH requires every context definition to precede the first constructor, so `build.py` injects `@include
  "6510_context.sinc"` into its copy of the stock spec, after the last `define register`.
* The operand subtables (`OP1`, `OP2`, `OP2LD`, `OP2ST`, our illegal-RMW `OP3`) cover every legal memory
  instruction plus SLO/RLA/SRE/RRA/DCP/ISC in one constructor per addressing mode. The illegal opcodes that
  spell out their own operand get twins by rewriting `6510_illegal.sinc`; a wrong operand size cannot slip
  through because the SLEIGH compiler rejects it.
* The stock `JSR`/`RTS` store `inst_next` and return to it -- self-consistent, but one byte off the hardware,
  and a 6510 program can read its own return address off the stack. `build.py` patches its copy to
  `inst_next-1` / `return [tmp+1]` (`tests/test_smc_sleigh.py` pins both).

### What Ghidra needs on top of the SLEIGH change

Correct raw P-Code is not enough; two Ghidra behaviours fold an SMC cell back to a constant before it can
reach high P-Code.

* `DecompileCallback.encodeFunction` declares **every address inside a function body** CONSTANT to the
  decompiler, and an operand cell is inside a function body by construction. `ExportHighPcode.freeCells`
  subtracts the cell bytes from each function's body -- they are data, not code -- which is what turns
  `screen = A` back into `*smc_100a = A`.
* The loader's block must be writable, or `MappedEntry.getMutabilityOfAddress` reports CONSTANT for the whole
  image. C64 RAM is writable, so the export sets it.

`6510.pspec` also declares `$D000-$DFFF` volatile, so VIC/SID/CIA reads are not folded from the image bytes.

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

`SMC=0` in the environment exports the same program without the SMC facts (the A/B baseline). `docker run --rm
di-ghidra` with no arguments runs the four CI smoke tests: illegal-opcode decode, the hello-world SMC export,
the complexity/coverage oracle, and the semantic oracle.

## 3. The comparison

Twenty seconds of music per tune, one certified pipeline run each (no divergence), against one headless Ghidra
run each. "sites" is distinct executed instruction addresses (Ghidra's column counts only those inside a
function body); "raw ops" is `Instruction.getPcode()` summed over them against `LiftedSite.ops`; "high" is
`HighFunction.getPcodeOps()` against our S4 IR statements; "C" is decompiled C lines against `tuneprog.md`'s
program section.

| tune | sites (G/us) | raw ops (G/us) | high ops / S4 stmts | C / md lines | gotos (G/us) | unresolved | dropped blocks | decompile |
|---|---|---|---|---|---|---|---|---|
| Automatas | 582/615 | 1995/2177 | 6112 / 958 | 839 / 221 | 0/0 | 0 | 0 | 575 ms |
| Commando (song 1) | 320/353 | 1302/1501 | 3584 / 337 | 345 / 278 | 2/1 | 0 | 0 | 301 ms |
| GoatTracker (Je suis Linus) | 304/422 | 1318/1725 | 435 / 541 | 258 / 155 | 0/0 | 0 | 105 | 397 ms + 1 crash |
| Ghouls'n'Ghosts (song 1) | 664/718 | 2018/2743 | 3918 / 678 | 795 / 685 | 29/15 | 0 | 6 | 634 ms |

Pipeline wall time (trace + S2-S4 + verify + print + facts): 12 s, 3 s, 3 s, 3 s. Headless Ghidra wall time
(JVM start, import, analysis, decompile, emulate): 18-19 s per tune; the decompile column is `DecompInterface`
time only.

### A/B: the same run without the SMC facts (`SMC=0`)

| tune | high ops (with/without) | C lines | dropped blocks | decompiler warnings |
|---|---|---|---|---|
| Automatas (73 cell sites) | 6112 / 2515 | 839 / 658 | 0 / 65 | 10 / 125 |
| Commando (0 cell sites) | 3584 / 3584 | 345 / 345 | 0 / 0 | 0 / 0 |
| GoatTracker (14) | 435 / 204 | 258 / 275 | 105 / 128 | 113 / 130 |
| Ghouls'n'Ghosts (22) | 3918 / 147 | 795 / 87 | 6 / 0 | 28 / 10 |

Commando has no SMC cells and is bit-identical in both arms: the control works. On the three tunes that do
self-modify, the facts are the difference between a decompilation and a stub -- Ghouls'n'Ghosts' tick fails to
decompile at all without them (147 high P-Code ops for 664 executed sites), and Automatas goes from 65 dropped
blocks to none.

### Reading it

**Right.** With the cell context applied Ghidra decompiles all four tunes with *zero* unresolved control flow,
and every SMC cell reaching the C is a global (`smc_100a`, `smc_12ef`) read and written like any other
variable: the abstraction really is mechanical, and a mature decompiler performs it once the facts are in the
database. Patched dispatches resolve -- the four `JumpTable.writeOverride` calls on GoatTracker and
Ghouls'n'Ghosts become switches over our target sets -- and S6 region names carry over (`T1900[cursor_12CE]`).

**Bigger.** Ghidra's high P-Code is 6.4x (Automatas), 10.6x (Commando) and 5.8x (Ghouls'n'Ghosts) our S4
statement count; its C is 1.2-3.8x our printed form. That is the honest shape of the efficiency claim: on the
same executed sites, with the same facts, the certified tuneprog is several times smaller because it is
allowed to be *trace-exact* rather than sound-for-all-inputs -- untaken arms are `trap`, registers dead in the
trace are gone, the per-call schedule and periodicity collapse whole loops. Not evidence that Ghidra is bad;
evidence that the extra facts buy compression a static tool cannot take.

**Smaller, and why.** GoatTracker is the one row where Ghidra's total is below ours (435 vs 541), and the
oracle classifies it rather than crediting it: its main routine (`row_apply`, 264 executed sites) makes the
decompiler process die -- in *both* arms, so our SLEIGH spec is not the cause -- and its tick drops 105 blocks
as unreachable.

**Inexpressible.** Opcode cells beyond the `RTS` pair (Automatas' `$10B8`/`$10BF` `ADC`<->`SBC`) stay
residual: one address decodes as one instruction. Trace closure, the per-call schedule, the pinned inputs,
periodicity and the differential certificate have no representation in a Ghidra program -- they are properties
of a *run*, and the decompiler is deliberately bound to the program database.

## 4. The three oracles

All three run through the same Docker entry and write small JSON next to the export.

**Complexity** (`stats.json` + `comparison.json`). Per procedure, both sides: executed sites, raw P-Code ops,
high P-Code ops vs S4 statements, C lines vs `tuneprog.md` lines, gotos, unique temporaries, plus Ghidra's
diagnostics (`DecompileResults` errors, `halt_baddata`/`switchD`, "Removing unreachable block"). Where Ghidra
reports clean flow *and* our certificate holds, our statements-per-site and gotos-per-site must not exceed
Ghidra's by more than `--tol` (default 1.5x); a violation is `ours_bigger` and localised. The only flag on the
four exemplars is Automatas' `tick` (2.88 statements/site against 1.62 ops/site over eight sites: ours carries
the phase counter and the call marshalling). Every other procedure is `ok`, `ghidra_partial` (Ghidra's single
function body does not cover the sites our clone-per-entry procedure does) or `ghidra_incomplete`.

**Coverage** (`coverage.json`). Ghidra's static reachability from our entries and jump-table references, minus
the trace's executed sites, is the code our horizon and subtune selection never exercised -- our `trap` arms:

| tune | executed | reachable | uncovered sites | ranges | classification |
|---|---|---|---|---|---|
| Automatas | 615 | 672 | 57 | 13 | 13 untaken branch |
| Commando | 353 | 506 | 153 | 16 | 11 untaken branch, 3 unentered block, 2 block tail |
| GoatTracker | 422 | 447 | 25 | 6 | 6 untaken branch |
| Ghouls'n'Ghosts | 718 | 1149 | 433 | 47 | 46 untaken branch, 1 unentered block |

Ghouls'n'Ghosts' biggest hole is a whole function at `$7316` that Ghidra reaches but song 1 never enters --
the other subtunes do, which is the `--songs all` case the Follin prototype documents; the untaken-branch
ranges are the arms a longer horizon would need. No `table_arm` ranges remain on either patched-dispatch tune:
the S2 static closure already enumerated those targets and Ghidra reached exactly them.

**Semantics** (`emulate.json`). Ghidra's own P-Code emulator runs the post-init image for eight play calls
with the entry registers the trace pinned and the post-init CPU state, recording every executed pc and every
`$D400-$D418` write:

| tune | steps | pcs outside the trace | SID write mismatches | agree |
|---|---|---|---|---|
| Automatas | 1277 | 0 | 0 | yes |
| Commando | 2127 | 0 | 0 | yes |
| GoatTracker | 3006 | 0 | 8 (from call 2) | no |
| Ghouls'n'Ghosts | 4154 | 64 (from call 2) | 7 | no |

Automatas is the strongest: 73 SMC cell sites and illegal opcodes, eight calls, byte-identical SID output. The
two disagreements are open and localised (GoatTracker's `$D400/$D401` differ from call 2 with no pc ever
leaving the traced site set; Ghouls'n'Ghosts leaves it at `$681C` on call 2), and are not explained by the
entry registers, flags, stack pointer or pushed return address -- all four were pinned and made no difference.
What remains: per-call inputs the facts do not carry (both traces record only `entry_reg` inputs), and
Ghidra's emulator over re-decoded self-modified bytes.

Two honesty notes. For the 105 illegal opcodes this is **not** an independent oracle -- our SLEIGH `.sinc` and
the Python lifter encode the same table, `tests/test_coverage.py` guards that they stay equal, and sidplayfp
is the independent one; for the legal set it is a third oracle beside py65 and sidplayfp. And it does not
validate the SMC constructors: Ghidra's emulator decodes from live memory with the flowing context, so
`noflow` per-address database values never reach it, and the stock constructors read the modified bytes
anyway. Validating those is what Ghidra's processor `PCodeTest`/emulator test framework is for;
`tests/test_smc_sleigh.py` pins them at the P-Code shape level against `lift.py`.

## 5. Limits

* Opcode cells whose alternative is not `RTS` are residual; overlay address spaces are the other known route,
  at the price of manual control-flow resolution between overlays.
* Ghidra function bodies are disjoint and our procedures are cloned per entry, so per-procedure rows only
  compare where the entry addresses match and Ghidra's body covers our sites; the oracle labels the rest
  `ghidra_partial` rather than scoring it.
* The export creates labels and comments, not structure data types for stride views: region bases can sit
  inside executed code, and defining data there breaks disassembly.
* The decompiler inlines thunks and tail calls, so a Ghidra function's high P-Code can include a callee's;
  totals are the safer comparison.
* `EmulateTrace` single-steps and caps at 400k steps per call.

## References

Every link was fetched and checked. One paragraph per topic; each ends with how ours differs.

**Overlay address spaces** -- the other route to "one address, several contents", and the one every 8-bit banking module uses: [`OverlayAddressSpace`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/address/OverlayAddressSpace.html), [`Memory.createInitializedBlock(..., overlay)`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/mem/Memory.html), [`Program.createOverlaySpace`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/listing/Program.html). The decompiler's [`space.hh`](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Decompiler/src/decompile/cpp/space.hh) says an overlay lets "the same physical location contain different code ... depending on context"; the [Memory Map help](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Base/src/main/help/help/topics/MemoryMapPlugin/Memory_Map.htm) warns they "have some limitations in conjunction with decompilation and analysis". Ours keeps one address space, so cross-block flow is never resolved by hand.

**Context registers, the standard mode switch** -- [SLEIGH 6.4](https://ghidra.re/ghidra_docs/languages/html/sleigh_tokens.html) (`define context`, `noflow`), [SLEIGH ch.8](https://ghidra.re/ghidra_docs/languages/html/sleigh_context.html), ARM's `TMode` ([ARM.sinc](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Processors/ARM/data/languages/ARM.sinc), [ARMtTHUMB.pspec](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Processors/ARM/data/languages/ARMtTHUMB.pspec)), the 8-bit case ([ghidra-65816](https://github.com/achan1989/ghidra-65816): `ctx_MF`/`ctx_XF`/`ctx_EF`), and [`ProgramContext.setValue`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/listing/ProgramContext.html) for per-address values. Those select an *instruction set*; ours selects where an operand comes from. Ghidra's stock [6502 module](https://github.com/NationalSecurityAgency/ghidra/tree/master/Ghidra/Processors/6502) has no context register at all.

**P-Code injection** -- the same effect written in Java rather than in the language: [`PcodeInjectLibrary`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/lang/PcodeInjectLibrary.html), [`InjectPayload`](https://ghidra.re/ghidra_docs/api/ghidra/program/model/lang/InjectPayload.html), `<callfixup>`/`<callotherfixup>` in [cspec.xml](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Decompiler/src/main/doc/cspec.xml), worked through in [Guide to P-code Injection](https://swarm.ptsecurity.com/guide-to-p-code-injection/). A context constructor needs no per-program Java and no analyser hook.

**Ghidra's own position on SMC** -- [#5871](https://github.com/NationalSecurityAgency/ghidra/issues/5871) (open: a 6502 `JSR` overwriting its own operand fails the processor tests; "self-modifying instructions aren't really something that occurs regularly"), [#3392](https://github.com/NationalSecurityAgency/ghidra/issues/3392) (open: data inside an instruction is "not something Ghidra can currently handle"), [#8888](https://github.com/NationalSecurityAgency/ghidra/issues/8888) (overlays are for "the same address used for different purposes ... at different times"; "Ghidra's static analysis is generally not sensitive to time"), [#6799](https://github.com/NationalSecurityAgency/ghidra/issues/6799) (the decompiler "is intentionally left bound only to the Program database ... we allow the user to manually capture dynamic data back into the Program database" -- which is exactly what our facts export does), [discussion #6651](https://github.com/NationalSecurityAgency/ghidra/discussions/6651) (context register recommended for banking), [discussion #4321](https://github.com/NationalSecurityAgency/ghidra/discussions/4321).

**Emulation-driven analysis** -- all of these materialise *one* byte-state by patching bytes back; we never patch a byte, the cell stays a variable. [GhidraEmulatorUI](https://github.com/cslamber/GhidraEmulatorUI) (a memory-changes table whose COMMIT writes emulator bytes into the listing, "mostly useful in dealing with self-modifying code"), [GhidraEmu](https://github.com/Nalen98/GhidraEmu) (does not persist them), [EmulatorHelper](https://ghidra.re/ghidra_docs/api/ghidra/app/emulator/EmulatorHelper.html) (`enableMemoryWriteTracking`/`getTrackedMemoryWriteSet`, what our semantic oracle drives), the modern [PcodeEmulator](https://ghidra.re/ghidra_docs/api/ghidra/pcode/emu/PcodeEmulator.html), and Ghidra's own [EmuX86DeobfuscateExampleScript](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Features/Base/ghidra_scripts/EmuX86DeobfuscateExampleScript.java) (writes results back as comments only).

**6502/C64 in Ghidra** -- [c64_ghidra](https://github.com/c64cryptoboy/c64_ghidra) is the closest prior art: an overlay-capable C64 loader plus `c64PcodeEmulation.py`, whose comment is explicit -- "our only reason to write emulator memory back to the program memory is to support self-modifying code" (`clearListing` then `setByte`). [Disassembling Crossroads, part 2](https://dansanderson.com/mega65/crossroads-part-2/) reverses a C64 game in Ghidra with a dedicated self-modifying-code section (found by inspection; the SMC target shows as function+offset) and uses overlays for the `$D000` I/O window. [GhidraNes](https://github.com/kylewlacy/GhidraNes) and [GhidraBoy](https://github.com/Gekkio/GhidraBoy) are the overlay-per-bank precedents, both documenting the manual cross-bank flow cost.

**Academic** -- [Certified Self-Modifying Code](https://flint.cs.yale.edu/flint/publications/smc.html) (Cai/Shao/Vaynberg, PLDI 2007), Hoare logic where code is mutable data; [CoDisasm](https://doi.org/10.1145/2810103.2813627) (Bonfante et al., CCS 2015), *wave* decomposition -- disassemble each maximal segment in which no already-executed code is modified (ours is the opposite move: one program, cells as variables, no waves); [Verified abstract interpretation techniques for disassembling low-level self-modifying code](https://doi.org/10.1007/978-3-319-08970-6_9) (Blazy/Laporte/Pichardie, ITP 2014), a Coq-verified sound CFG; [A Model for Self-Modifying Code](https://doi.org/10.1007/978-3-540-74124-4_16) (Anckaert et al., IH 2006), the state-enhanced CFG whose edges are conditional on the target's byte state; [LTL Model Checking of Self Modifying Code](https://arxiv.org/abs/1909.12635) and [Reachability Analysis of Self Modifying Code](https://arxiv.org/abs/1909.12626) (Touili/Ye), self-modifying pushdown systems; [Hybrid Analysis and Control of Malware](https://doi.org/10.1007/978-3-642-15512-3_17) (Roundy/Miller, RAID 2010), static CFG plus selective dynamic instrumentation. Ours produces neither a sound over-approximation nor a per-wave listing, but one trace-exact program carrying a per-call differential certificate.
