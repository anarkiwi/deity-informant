"""The nodes a resolved expression adds to the IR: a guarded choice, a site, a return."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sel:
    """``alts[0]`` unless a later alternative's guards hold: ``((guards, expr), ...)``."""

    alts: tuple


@dataclass(frozen=True, slots=True)
class At:
    """``e`` read at ``site`` ``(proc, label, index)``; ``via`` the call a return came through."""

    e: object
    site: tuple
    via: tuple = None


@dataclass(frozen=True, slots=True)
class Ret:
    """A call's ``k``-th return as a definition at ``(lbl, i)``: opened through the callee."""

    lbl: str
    i: int
    call: object
    k: int
