"""deity_informant -- a 6510 -> raw-P-Code lifter and pure-Python P-Code interpreter.

Two products from one opcode table:

* **standalone** -- ``lift`` + ``PcodeVM`` run 6510 code (including every
  documented NMOS illegal) as raw P-Code, with no Ghidra and no py65.
* **Ghidra / pypcode backend** -- the ``6510`` SLEIGH module under ``ghidra/6510``
  (stock 6502 legal set + a generated illegal ``.sinc``) so Ghidra's disassembler
  *and* decompiler, and pypcode, become illegal-aware.

Illegal-opcode semantics and cycles follow "No More Secrets - NMOS 6510
Unintended Opcodes" (v0.91); see ``docs/illegal-opcodes.md``.
"""

from __future__ import annotations

from .lifter import (
    OPS,
    MODE_LEN,
    MEM_MODES,
    ILLEGAL_OPCODES,
    MAGIC,
    lift,
    load_cycle_tables,
    CYCLETIME,
    EXTRACYCLES,
)
from .vm import (
    PcodeVM,
    run_sub,
    run_irq,
    run_irq_driven,
)

__version__ = "0.2.0"

__all__ = [
    "OPS",
    "MODE_LEN",
    "MEM_MODES",
    "ILLEGAL_OPCODES",
    "MAGIC",
    "lift",
    "load_cycle_tables",
    "CYCLETIME",
    "EXTRACYCLES",
    "PcodeVM",
    "run_sub",
    "run_irq",
    "run_irq_driven",
    "__version__",
]
