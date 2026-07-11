# deity-informant

A 6510 illegal-opcode toolkit: a standalone 6510 -> raw-P-Code lifter + pure-Python P-Code VM, and a `6510` Ghidra/pypcode SLEIGH processor module. All 105 documented NMOS 6510 illegal opcodes are first-class. No runtime dependency on py65 or Ghidra.

## Why

Working on C64 code requires the NMOS 6510 illegal opcodes, and no existing backend handles them correctly:

- Real playroutines/demos use illegals as load-bearing instructions (A Mind Is Born runs `SRE`; Automatas runs `SBX`/`SAX`/`LAX`/`ANC`/`ALR`).
- Stock Ghidra's 6502 SLEIGH spec (and pypcode, which vendors it verbatim) defines zero illegals and has no catch-all — every illegal is a `BadDataError`, so it cannot even disassemble such code, let alone decompile it.
- Ghidra's decompiler only consumes P-Code from a *compiled* SLEIGH spec, so Python-generated P-Code cannot be injected — a SLEIGH language extension is the only way to make the decompiler illegal-aware.
- py65 is not a valid execution reference: it stubs the RMW illegals (`SLO/RLA/SRE/RRA/DCP/ISC`, `LAS/ANE/SH*`) as `inst_not_implemented` — a 2-byte, 0-cycle skip, silently wrong on real code.
- The known community 6510 Ghidra fork adds illegal *disassembly* only, with uncertain P-Code semantics — not trustworthy for decompilation or execution.
- deity-informant supplies both missing pieces: a compiled 6510 SLEIGH module (illegal-aware Ghidra/pypcode decompilation) and a hardware-validated lifter + VM (byte-exact vs sidplayfp) executing all 105 illegals.

## Two products

- **standalone lifter + VM** — `lift` (6510 -> raw P-Code), the `PcodeVM` interpreter, and the `run_sub`/`run_irq`/`run_irq_driven` drivers. Pure Python, no Ghidra, no py65.
- **`6510` SLEIGH module** (`ghidra/6510/`) — stock 6502 legal spec + generated `6510_illegal.sinc` that makes Ghidra's disassembler *and* decompiler, and pypcode, illegal-aware. Language id `6510:LE:16:default`.

## Install

```bash
pip install deity-informant            # core, no deps
pip install deity-informant[oracle]    # + py65 (legal-opcode differential oracle)
pip install deity-informant[ghidra]    # + pypcode (SLEIGH build / lift backend)
pip install deity-informant[dev]       # test + lint tooling
```

## CLI

Console script `deity-informant`:

```bash
deity-informant disasm IMAGE [--org ADDR] [--start ADDR] [--count N]  # 6510 disassembly (illegal-aware)
deity-informant pcode  IMAGE --at ADDR [--org ADDR]                   # raw P-Code for one instruction
deity-informant run    IMAGE --init ADDR [--play ADDR --frames N]     # execute in PcodeVM, dump $D400.. grid
deity-informant emit-sleigh [-o DIR] [--magic 0xEE]                   # build/install the 6510 SLEIGH module
```

## Python API

```python
from deity_informant import lift, PcodeVM, run_sub
insn = lift(mem, pc)                 # {"ops", "len", "cyc", "pen", "ctrl"}
vm = PcodeVM(mem); run_sub(vm, entry, {}, lift)   # execute a subroutine to its RTS
```

## Use with Ghidra

```bash
python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
```

Compiles `6510.sla`, resolving the stock `6502.slaspec` + SLEIGH compiler from `$GHIDRA_INSTALL_DIR` (or a `pypcode` install). Restart Ghidra, import the C64 image as Raw Binary language `6510:LE:16:default` at base `$0000`; illegals now decode instead of BadData. Full steps: [docs/ghidra.md](docs/ghidra.md).

## Illegal opcodes

All 105 documented NMOS 6510 illegals lifted as genuine P-Code (not stubs), semantics/cycles per "No More Secrets — NMOS 6510 Unintended Opcodes" ([csdb.dk/release/?id=258111](https://csdb.dk/release/?id=258111)). Full table: [docs/illegal-opcodes.md](docs/illegal-opcodes.md).

## Docs

- [docs/design.md](docs/design.md) — architecture (lifter + VM, SLEIGH module, raw vs high P-Code, cycle layer).
- [docs/illegal-opcodes.md](docs/illegal-opcodes.md) — illegal-opcode reference.
- [docs/nms-provenance.md](docs/nms-provenance.md) — reference-source provenance.
- [docs/ghidra.md](docs/ghidra.md) — using the 6510 module with Ghidra / pypcode.
