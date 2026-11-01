"""Decide each scenario cell feasible or refuted, each refusal carrying its violated rule.

Constraints: the writer's reachable addresses must meet the role's fetch region under the
mapping; a port writer changes the map, not bytes; the timing must suit the writer kind. A
refuted cell records the first rule it fails.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Verdict", "decide", "feasible_cells", "refuted_cells", "ROLE_REGION"]

_ANY = (0x0000, 0xFFFF)
_ZP = (0x0000, 0x00FF)
_STACK = (0x0100, 0x01FF)
_PORT = (0x0000, 0x0001)

#: The fetch region each role's byte lives in.
ROLE_REGION = {
    "opcode": _ANY,
    "operand": _ANY,
    "ptr_indirect": _ANY,
    "ret_addr": _STACK,
    "hw_vector": (0xFFFA, 0xFFFF),
    "kernal_vector": (0x0314, 0x0319),
    "bank_reg": _PORT,
    "reu_reg": (0xDF00, 0xDF1F),
}

_REACH = {"zp": _ZP, "zp_indexed_wrap": _ZP, "sp_rel": _STACK}

#: The windows a mapping needs the fetched byte in (none: anywhere).
WINDOWS = {
    "flat": (_ANY,),
    "ram_under_rom": ((0xA000, 0xBFFF), (0xE000, 0xFFFF)),
    "ram_under_io": ((0xD000, 0xDFFF),),
    "rom_vs_ram": ((0xA000, 0xBFFF), (0xD000, 0xDFFF), (0xE000, 0xFFFF)),
}

_PUSHERS = {"irq_push", "nmi_push", "brk_push"}


@dataclass(frozen=True)
class Verdict:
    """A cell's decision: feasible with no proof, or refuted with the failing rule."""

    feasible: bool
    rule: str = ""


def _meet(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else None


def _region(cell):
    """The role's fetch region inside the mapping's windows (``None`` if they miss)."""
    for w in WINDOWS[cell.mapping]:
        r = _meet(ROLE_REGION[cell.role], w)
        if r is not None:
            return r
    return None


def _reach(cell):
    if cell.writer in _PUSHERS:
        return _STACK
    return _REACH.get(cell.channel, _ANY)


def _place(cell):
    region = _region(cell)
    if region is None:
        return "unbankable-role"
    if cell.role == "reu_reg" and cell.writer != "cpu":
        return "device-reg-cpu-only"
    if cell.writer == "cpu_port":
        want = "flat" if cell.role == "bank_reg" else "rom_vs_ram"
        if cell.mapping != want:
            return "port-changes-map-only"
        return ""
    if cell.mapping == "rom_vs_ram":
        return "fetch-not-from-ram"
    if cell.role == "bank_reg":
        return "bank-reg-port-writer"
    if cell.writer in _PUSHERS and cell.role != "ret_addr":
        return "push-targets-return"
    return "" if _meet(_reach(cell), region) else "disjoint-address"


def _time(cell):
    if cell.writer in ("irq_push", "nmi_push"):
        return "" if cell.timing == "async" else "interrupt-is-async"
    if cell.writer == "brk_push" and cell.timing == "async":
        return "brk-is-sync"
    if cell.channel == "reset" and cell.timing != "pre_lift":
        return "reset-predates-lift"
    if cell.timing == "same_insn" and not (
        cell.writer == "cpu" and cell.role in ("opcode", "operand")
    ):
        return "same-insn-self-write-only"
    if cell.role == "bank_reg" and cell.timing == "pre_lift":
        return "bank-reg-after-lift"
    return ""


def decide(cell):
    """The :class:`Verdict` for ``cell``: feasible, or refuted with its violated rule."""
    rule = _place(cell) or _time(cell)
    return Verdict(not rule, rule)


def feasible_cells(cells):
    """The cells :func:`decide` accepts."""
    return [c for c in cells if decide(c).feasible]


def refuted_cells(cells):
    """``[(cell, rule)]`` for every refuted cell."""
    return [(c, v.rule) for c in cells for v in (decide(c),) if not v.feasible]
