"""Whole-program lifting to optimized, executable P-Code (native; see ``docs/wholeprog.md``).

A ``Program`` holds functions of basic blocks of ops ``[mnemonic, out, [inputs], aux]`` with
varnodes ``[space, offset, size]``; ``Program.call`` executes it.
"""

from __future__ import annotations

from . import _native
from .trace import Facts

__all__ = ["REGS", "lift", "lift_many", "plans", "reference_call", "reference_calls", "entry_spec"]

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


BACKEND = "jit"
"""The default ``Program.backend`` of :func:`lift`: ``"jit"`` or ``"interp"``."""


def lift(
    image,
    entries,
    volatile=(),
    facts=None,
    optimize=True,
    cycles=False,
    bank=None,
    backend=None,
    interrupts=False,
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Lift ``entries`` of the program in ``image`` (the memory each entry starts from);
    ``cycles=True`` counts cycles and stamps volatile accesses with them; ``bank`` is a bank
    map (e.g. ``deity_informant.c64.bank_map()``) memory is seen through; ``backend`` (default
    :data:`BACKEND`) is the engine ``Program.call`` runs it on; with ``interrupts`` the lift
    keeps no memory value across a volatile access, where ``Program.call(..., nested=)`` takes
    interrupts."""
    n = _native_or_raise()
    prog = n.lift_program(
        bytes(image),
        [entry_spec(e) for e in entries],
        _ranges(volatile),
        _facts(facts),
        optimize,
        cycles,
        bank,
        interrupts,
    )
    prog.backend = backend or BACKEND
    return prog


def plans(image, entries, facts=None, bank=None):
    """The functions the lift plans for ``entries``: ``[{"pc", "top", "balanced", "heights"}]``,
    ``heights`` per instruction start: the heights it is planned at relative to its function's
    entry, ``None`` where unknown."""
    return _native_or_raise().plan_program(
        bytes(image), [entry_spec(e) for e in entries], _facts(facts), bank
    )


def lift_many(jobs, progress=True, optimize=True, cycles=False):
    """Lift ``[(image, entries, volatile, facts)]`` in parallel with a progress bar."""
    n = _native_or_raise()
    progs = n.lift_many(
        [
            (bytes(img), [entry_spec(e) for e in ents], _ranges(vol), _facts(f))
            for img, ents, vol, f in jobs
        ],
        progress,
        optimize,
        cycles,
    )
    for p in progs:
        p.backend = BACKEND
    return progs


def reference_call(mem, pc, **kw):
    """``run_to_rts`` on the reference interpreter with ``Program.call``'s bus; ``bank`` sees
    memory through a bank map; ``reads`` replays its volatile reads as ``Program.call``."""
    if "volatile" in kw:
        kw["volatile"] = _ranges(kw["volatile"])
    return _native_or_raise().reference_call(mem, pc, **kw)


def reference_calls(mem, pc, n, **kw):
    """``n`` :func:`reference_call` s of ``pc`` with A 0, each from the registers the last
    left (the first from ``reg``), ``port`` (when given) stored to $01 before each:
    ``(calls, cycles, halt)``, stopping after the first that does not return."""
    if "volatile" in kw:
        kw["volatile"] = _ranges(kw["volatile"])
    return _native_or_raise().reference_calls(mem, pc, n, **kw)
