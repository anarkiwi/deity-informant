"""The expectation table: every feasible cell mapped to its lift outcome or gap reason.

A flat CPU cell the lifter reproduces carries its exact outcome. A machine cell carries the
reason the flat-RAM lifter cannot model it (banking, interrupts, DMA); whether an oracle
witnesses it is a separate flag. A cell no oracle reaches is a pure gap.
"""

from __future__ import annotations

from .feasible import decide
from .space import cells

__all__ = ["lift_reason", "gap_reason", "feasible_sids", "LIFT_REASONS", "GAP_REASONS"]

LIFT_REASONS = ("lift-no-dma", "lift-no-interrupts", "lift-no-banking")
GAP_REASONS = ("reu-no-oracle", "machine-not-synthesized", "placement-not-synthesized")

_BANK_ROLES = ("bank_reg", "hw_vector", "kernal_vector")


def lift_reason(cell):
    """Why the flat-RAM lifter cannot reproduce ``cell``, or ``None`` if it can (flat CPU)."""
    if cell.writer == "reu_dma" or cell.role == "reu_reg":
        return "lift-no-dma"
    if cell.writer in ("irq_push", "nmi_push") or cell.timing == "async":
        return "lift-no-interrupts"
    if cell.mapping != "flat" or cell.writer == "cpu_port" or cell.role in _BANK_ROLES:
        return "lift-no-banking"
    return None


def gap_reason(cell, witnessed):
    """Why a cell is unwitnessed, or ``None`` when it is witnessed by some oracle."""
    if witnessed:
        return None
    if cell.writer == "reu_dma" or cell.role == "reu_reg":
        return "reu-no-oracle"
    if lift_reason(cell) is not None:
        return "machine-not-synthesized"
    return "placement-not-synthesized"


def feasible_sids():
    """The sids of every feasible cell."""
    return {c.sid for c in cells() if decide(c).feasible}
