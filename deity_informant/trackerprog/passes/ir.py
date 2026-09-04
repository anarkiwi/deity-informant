"""The level object the passes hand each other, and the validation between them.

L0 and L1 are the S4 IR with the facts the structuring derived; L2 to L6 are
trackerprogs, which :mod:`..universal` renders.  ``validate`` renders the level
before a pass and the level after it and asserts the write lists agree -- raw,
where both are the player's, and by the certificate's own reduction where the
comparison crosses from the interpreter to the player (section 2 drops the
interleave between voices of one tick's writes, and only that).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...tuneprog import grid
from ...tuneprog.ir import TrapError
from .. import interp, region
from ..attest import attest
from .rir import render as _render

LEVELS = {
    1: "structured tick",
    2: "phase-normal form",
    3: "typed PNF",
    4: "materialised PNF",
    5: "selected object",
    6: "canonical trackerprog",
}


class Diverged(AssertionError):
    """A pass that changed the observable: the level after it is not the level before."""


@dataclass
class Level:
    """One representation, and what the pass into it derived."""

    n: int
    art: dict | None = None  # the planes (S4, S6, T0, T1, T2, the image)
    prog: object = None  # the IR, at L0 and L1
    proc: str = ""
    obj: dict | None = None  # the trackerprog, from L2 on
    facts: dict = field(default_factory=dict)

    @property
    def name(self):
        return LEVELS.get(self.n, "L%d" % self.n)


def writes(level, ticks):
    """One level's observable: a per-tick list of ``(register, value)``."""
    if level.obj is not None:
        return _render(level.obj, ticks)
    return irwrites(level.prog, ticks)


def irwrites(prog, ticks):
    """The interpreter's own write list, tick by tick, from the post-init image."""
    p = interp.Player(prog, region.Fetch())
    p.run_init()
    out = []
    for _ in range(ticks):
        try:
            p.tick()
        except TrapError:
            break
        out.append(_sid(p.sid))
    return out


def _sid(sid):
    regs = grid.regs([a for a, _v in sid])
    return [(int(r), v) for r, (_a, v) in zip(regs, sid) if r >= 0]


def validate(before, after, horizon):
    """Render both levels and compare: the one check every pass answers to."""
    want = writes(before, horizon)
    got = writes(after, horizon)
    cert = attest(after.obj, want, horizon, _render) if after.obj is not None else None
    out = {
        "from": before.n,
        "to": after.n,
        "ticks": min(len(want), len(got)),
        "writes": sum(len(w) for w in got),
        "identical": want == got,
        "divergence": cert["divergence"] if cert else (None if want == got else _first(want, got)),
        "same_per_register_order": cert["same_per_register_order"] if cert else want == got,
    }
    if out["divergence"] is not None:
        raise Diverged("L%d -> L%d: %s" % (before.n, after.n, out["divergence"]))
    return out


def _first(want, got):
    """The first tick two write lists differ on, where neither side is an object."""
    for t in range(max(len(want), len(got))):
        a = want[t] if t < len(want) else None
        b = got[t] if t < len(got) else None
        if a != b:
            return {"tick": t, "expected": a, "got": b}
    return None
