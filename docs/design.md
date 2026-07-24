# Architecture

deity-informant is one 6510 opcode model exposed as four products: a standalone
lifter + VM, the `6510` SLEIGH module, the symbolic window recorder, and the
state-machine kernel lift.

## Product 1 — standalone lifter + P-Code VM

Pure Python; no Ghidra, no py65 at runtime.

- **`deity_informant/lifter.py`** — `lift(mem, pc)` decodes the 6510 instruction
  at `pc` and lowers it to a list of raw P-Code micro-ops over varnodes
  `[space, offset, size]` (`c` const, `r` register/flags, `u` unique temp). It
  returns `{"ops", "len", "cyc", "pen", "ctrl"}`: the op list, byte length, base
  cycle cost, page-cross/branch penalty tag, and control-flow class. This is a
  standalone stand-in for Ghidra's `Instruction.getPcode()`.
  - Every documented NMOS illegal is genuine P-Code, not a stub: the RMW combos
    decompose to their two micro-ops (`SLO`=ASL+ORA, `RLA`=ROL+AND, `SRE`=LSR+EOR,
    `RRA`=ROR+ADC, `DCP`=DEC+CMP, `ISC`=INC+SBC) and the ALU/store/load/`SH*`
    forms are lowered directly. `JAM` halts; an unimplemented opcode is a hard
    error, never a silent skip.
  - Cycle tables (`CYCLETIME`/`EXTRACYCLES`) are frozen constants cross-checked
    against the No More Secrets chart — no runtime py65 dependency.
- **`deity_informant/vm.py`** — `PcodeVM` executes those op lists against a flat
  64 KiB memory model, capturing `$D400..` SID writes. Each record is compiled
  once (P-Code op -> a line of Python, `exec`'d into a closure, cached by
  instruction) then replayed. `run_sub` runs a subroutine to its `RTS`;
  `run_irq` / `run_irq_driven` drive frame-cadence and interrupt-driven play.

### Why volatile SID/CIA registers matter

A playroutine's entire output is its stream of writes to the SID (`$D400..`) and
its polling of CIA/VIC timer/raster registers. Nothing ever reads `$D400` back,
so any optimizer treating these as ordinary RAM would dead-store-eliminate every
SID write and drop the music. The VM and the SLEIGH memory map both treat the
SID/CIA/VIC bands as volatile IO, preserving each store and modelling the reads a
raster/CIA-polling driver branches on.

### The per-instruction cycle layer

Raw P-Code has no notion of time, but raster/multispeed and CIA-polling
playroutines branch on cycle-derived reads. So each lifted record carries a cycle
cost (`cyc` base + `pen` page-cross/branch-taken penalty). Carrying this layer is
what makes VM execution byte-exact against the hardware oracle on timing-sensitive
tunes; opcode fidelity alone is insufficient.

## Product 2 — the `6510` SLEIGH processor module

`ghidra/6510/` is a Ghidra/pypcode language extension.

- `data/languages/6510_illegal.sinc` defines the illegal opcodes as SLEIGH
  constructors; `6510.slaspec` includes the stock 6502 legal set plus that
  `.sinc`. `6510.ldefs`/`.pspec`/`.cspec` declare language `6510:LE:16:default`.
- `build.py` resolves the stock `6502.slaspec` and the SLEIGH compiler from
  `$GHIDRA_INSTALL_DIR` (or a `pypcode` install), then compiles `6510.sla`.
  The base spec is Ghidra's (Apache-2.0) and is a build artifact — fetched, not
  committed.

### SLEIGH is the only path to an illegal-aware decompiler

Ghidra's decompiler consumes only P-Code produced by a *compiled* SLEIGH spec —
Python-generated P-Code cannot be injected into it. Stock 6502 SLEIGH defines no
illegals and has no catch-all, so every illegal is `BadDataError`. Therefore a
compiled SLEIGH language extension is the sole way to make Ghidra's disassembler
*and* decompiler (and pypcode) illegal-aware. That is exactly this module.

## Product 3 — the symbolic window recorder

`deity_informant/recorder.py` (`record`) and `deity_informant/expr.py` add a
*recording decompiler*: a per-invocation partial evaluation of the interpreter.
`RecVM` subclasses `PcodeVM`, so a driver runs unchanged; each op executes
concretely (bit-identically) while a parallel pass residualises data flow over
the invocation-entry state and records every control-flow / placement fold as a
fact. A concrete pre-pass fixes the exact mutable-cell set the recording pass
residualises against.

The output is sound under self-modifying code: any concrete byte folded from a
mutable cell is either residualised into an entry-pure expression or pinned by a
recorded case fact, and evolved-state (`cur`) templates keep artifact size flat
across advancing data. A record-time assertion re-evaluates every fact and store
against the entry snapshot before the artifacts are returned. `lift` gains inert
`prov`/`stk` provenance keys that drive residualisation without changing its
existing output. See [smc-recovery.md](smc-recovery.md) for the two-pass pipeline
walk-through (ASCII diagrams) and [symbolic-recorder.md](symbolic-recorder.md) for
the full node/evaluation contract.

## Product 4 — the state-machine kernel lift

`deity_informant/kernel.py` (`lift_kernel`) lifts a `Recording` one altitude
higher: from `N` per-frame, entry-relative templates to one canonical
`(tables, S0, variants)` model of the tune. It consumes only the recorder's public
artifacts.

- **Partition.** `mem` leaves are the cross-frame reads; intersected with the
  written set they give persistent state `S`, and the complement gives the constant
  seed tables `T`.
- **Dedup.** Frames are regrouped by structural expression equality (abstracting
  only state-dependent placement addresses), collapsing `N` frames to the `K`
  distinct control paths; a variant's `guard` is its true branch structure.
- **Closed-loop proof.** `verify` re-iterates from `S0`, carrying the image forward
  with no per-frame snapshot, and asserts byte-exact outputs plus closure — the
  same soundness discipline as the record-time assertion, one level up. See
  [kernel.md](kernel.md).

## Raw P-Code vs high P-Code

- **Raw** P-Code is the literal per-instruction lowering (Product 1's `lift`, or
  Ghidra's `Instruction.getPcode()`): one small op list per instruction, no
  optimization. It is what you *execute* — the VM consumes it.
- **High** P-Code is the decompiler's SSA-form, register-allocated, optimized IR:
  great to *read* (readable C), wrong to *execute* (dead stores dropped,
  temporaries coalesced, control flow restructured).

The two products cover both: the SLEIGH module feeds Ghidra's decompiler (high
P-Code / C, for reading illegal-aware code), while the standalone lifter + VM
produce and run raw P-Code (for byte-exact execution).

## Validation

See [nms-provenance.md](nms-provenance.md). Semantics follow No More Secrets;
the VM is validated differentially against py65 (legal opcodes) and byte-exact
against the sidplayfp hardware oracle over 60 s of real playback (illegals
load-bearing). py65 is not a valid reference for the RMW illegals — it stubs them
as a 2-byte, 0-cycle skip.
