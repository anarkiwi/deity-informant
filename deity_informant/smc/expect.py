"""The expectation table: every feasible cell mapped to its lift outcome or gap reason.

A cell the lifter reproduces (flat CPU, or banking through the C64 bank map) carries its
exact outcome. A cell the lifter cannot model carries the reason (interrupts, including the
KERNAL's vector fetches on its interrupt path, or DMA); whether an oracle witnesses it is a
separate flag. A cell no oracle reaches is a pure gap.
"""

from __future__ import annotations

from .feasible import decide
from .space import cells

__all__ = [
    "lift_reason",
    "machine_cell",
    "gap_reason",
    "feasible_sids",
    "LIFT_REASONS",
    "GAP_REASONS",
]

LIFT_REASONS = ("lift-no-dma", "lift-no-interrupts")
GAP_REASONS = ("reu-no-oracle", "machine-not-synthesized", "placement-not-synthesized")

_BANK_ROLES = ("bank_reg", "hw_vector", "kernal_vector")


def machine_cell(cell):
    """Whether only the machine oracle can witness ``cell`` (banking, interrupts, DMA)."""
    return (
        cell.mapping != "flat"
        or cell.writer in ("cpu_port", "irq_push", "nmi_push", "reu_dma")
        or cell.role in _BANK_ROLES + ("reu_reg",)
        or cell.timing == "async"
    )


def lift_reason(cell):
    """Why the bank-aware lifter cannot reproduce ``cell``, or ``None`` if it can."""
    if cell.writer == "reu_dma" or cell.role == "reu_reg":
        return "lift-no-dma"
    if cell.writer in ("irq_push", "nmi_push") or cell.timing == "async":
        return "lift-no-interrupts"
    if cell.role == "kernal_vector":
        return "lift-no-interrupts"
    return None


def gap_reason(cell, witnessed):
    """Why a cell is unwitnessed, or ``None`` when it is witnessed by some oracle."""
    if witnessed:
        return None
    if cell.writer == "reu_dma" or cell.role == "reu_reg":
        return "reu-no-oracle"
    if machine_cell(cell):
        return "machine-not-synthesized"
    return "placement-not-synthesized"


def feasible_sids():
    """The sids of every feasible cell."""
    return {c.sid for c in cells() if decide(c).feasible}
