"""Whole-program lifting to optimized, executable P-Code (native; see ``docs/wholeprog.md``).

A ``Program`` holds functions of basic blocks of ops ``[mnemonic, out, [inputs], aux]`` with
varnodes ``[space, offset, size]``; ``Program.call`` executes it.
"""

from __future__ import annotations

from . import _native
from .trace import Facts

__all__ = ["REGS", "lift", "lift_many", "reference_call", "entry_spec"]

#: Register-file indices of ``lift()`` registers by name.
REGS = {"a": 0, "x": 1, "y": 2, "sp": 3, "c": 8, "z": 9, "i": 10, "d": 11, "v": 13, "n": 14}


def _native_or_raise():
    if _native is None:  # pragma: no cover - sdist install
        raise ImportError("whole-program lifting needs the native extension")
    return _native


def entry_spec(e):
    """``pc`` or ``(pc, {register: values})`` as the native ``(pc, [(index, values)])``."""
    if isinstance(e, int):
        return (e, [])
    pc, regs = e
    return (pc, [(REGS[r] if isinstance(r, str) else r, sorted(v)) for r, v in regs.items()])


def _ranges(r):
    return [(int(lo), int(hi)) for lo, hi in r]


def _facts(f):
    if f is None:
        return None
    return (f if isinstance(f, Facts) else Facts.from_dict(f)).to_dict()


def lift(image, entries, volatile=(), facts=None, optimize=True):
    """Lift ``entries`` of the program in ``image`` (the memory each entry starts from)."""
    n = _native_or_raise()
    return n.lift_program(
        bytes(image),
        [entry_spec(e) for e in entries],
        _ranges(volatile),
        _facts(facts),
        optimize,
    )


def lift_many(jobs, progress=True):
    """Lift ``[(image, entries, volatile, facts)]`` in parallel with a progress bar."""
    n = _native_or_raise()
    return n.lift_many(
        [
            (bytes(img), [entry_spec(e) for e in ents], _ranges(vol), _facts(f))
            for img, ents, vol, f in jobs
        ],
        progress,
    )


def reference_call(mem, pc, **kw):
    """``run_to_rts`` on the reference interpreter with ``Program.call``'s bus."""
    if "volatile" in kw:
        kw["volatile"] = _ranges(kw["volatile"])
    return _native_or_raise().reference_call(mem, pc, **kw)
