"""Whole-program lifting to optimized, executable P-Code (native; see ``docs/wholeprog.md``).

A ``Program`` holds functions of basic blocks of ops ``[mnemonic, out, [inputs], aux]`` with
varnodes ``[space, offset, size]``; ``Program.call`` executes it.
"""

from __future__ import annotations

from . import _native
from .trace import Facts

__all__ = ["REGS", "lift", "lift_many", "reference_call", "reference_calls", "entry_spec"]

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
    d = (f if isinstance(f, Facts) else Facts.from_dict(f)).to_dict()
    d.pop("ea", None)
    return d


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
    entry_sp=None,
    loaded=None,
    fold=False,
    sensitive=None,
    free=None,
    alias=(),
    once=False,
    held=(),
    devices=None,
    holds=(),
    checked=False,
    known=None,
    driven=None,
    player=False,
    overrun="trap",
    played=None,
):  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    """Lift ``entries`` of the program in ``image`` (the memory each entry starts from);
    ``cycles=True`` counts cycles and stamps volatile accesses with them; ``bank`` is a bank
    map (e.g. ``deity_informant.c64.bank_map()``) memory is seen through; ``backend`` (default
    :data:`BACKEND`) is the engine ``Program.call`` runs it on; ``entry_sp`` declares the SP
    every entry is called at (``docs/wholeprog.md``, *Stack extent*), ``loaded`` the
    ``(lo, hi)`` the program was loaded to (*Table extents*), ``fold=True`` lifts copies of
    one routine as one function of the copy's index (*Instances*) and ``sensitive`` lists the
    volatile addresses whose reads have effects (a throw-away read reaching none is not made).
    ``free`` ``{addr: values}`` are the variables an init the host ran on ``image`` left: bytes
    of unknown value, ``values`` theirs on each of its runs, in order (``docs/cgen.md``,
    *Init*); ``alias``
    ``[(lo, g, delta)]`` the operand pairs of them holding pair ``g`` plus ``delta``
    (:func:`deity_informant.postinit.groups`); ``once=True`` runs each entry once from ``image``
    (a host's init): a store into code makes cells only of the instructions that may run after
    it in the same call, and needs no code check (``docs/wholeprog.md``, *Init*); ``held`` the
    bytes such an init stored that hold one value on every run, the image's; ``devices`` a
    :class:`deity_informant.devices.Model` of what the volatile addresses answer; ``holds``
    ``[(lo, hi)]`` runs past the program the host holds as data besides those the lift's
    stores write (another lift's stores: a pointer object's loads may read them,
    ``docs/wholeprog.md``, *Pointer objects*); ``checked=True`` that the host checks the
    entries' declared ``A``, ``X`` and ``Y`` values before each call (the analysis takes them,
    no entry guard tests them); ``known`` ``{addr: values}`` every value a byte of ``free``
    may hold after the init, which that init's lift bounds it to (*Init*); ``driven``
    ``{addr: values}``, where the host calls the entries in a loop after such an init, what
    the init leaves whatever the data (``Program.exit`` of its lift): every entry starts
    from the driver head (*Driver loop*); ``player=True`` that the entries are a player's,
    lifted apart from the init that selects the song: its pointers from the song's data stay
    in the song's data block (*Machine model*); ``overrun="zero"`` that an access the memory
    map bounds reads 0 (a load) or does nothing (a store) where its address leaves its object,
    instead of trapping (``"trap"``), ``"last"`` the last entry of the run below it; ``played``
    ``{addr: values}`` the values of ``free`` on the runs of the songs the host calls init with
    alone, what a player's pointer holds at an entry (*Pointer regions*; None: ``free``'s)."""
    n = _native_or_raise()
    prog = n.lift_program(
        bytes(image),
        [entry_spec(e) for e in entries],
        _ranges(volatile),
        _facts(facts),
        optimize,
        cycles,
        bank,
        entry_sp,
        loaded,
        fold=fold,
        sensitive=None if sensitive is None else sorted(set(sensitive)),
        free=sorted((int(a), [int(x) for x in v]) for a, v in (free or {}).items()),
        alias=[tuple(a) for a in alias],
        once=once,
        held=sorted(set(held)),
        devices=None if devices is None else devices.lower(_ranges(volatile)),
        holds=sorted((int(a), int(b)) for a, b in holds),
        checked=checked,
        known=sorted((int(a), sorted(int(x) for x in v)) for a, v in (known or {}).items()),
        driven=(
            None
            if driven is None
            else sorted((int(a), sorted(int(x) for x in v)) for a, v in driven.items())
        ),
        player=player,
        overrun=overrun,
        played=sorted((int(a), [int(x) for x in v]) for a, v in (played or {}).items()),
    )
    prog.backend = backend or BACKEND
    return prog


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
    return _reference("reference_call", mem, pc, **kw)


def reference_calls(mem, pc, n, **kw):
    """``n`` :func:`reference_call` s of ``pc`` with A 0, each from the registers the last
    left (the first from ``reg``), ``port`` (when given) stored to $01 before each:
    ``(calls, cycles, halt)``, stopping after the first that does not return."""
    return _reference("reference_calls", mem, pc, n, **kw)


def _reference(name, *args, **kw):
    """The native reference interpreter's ``name`` of ``args``, ``volatile`` normalised."""
    if "volatile" in kw:
        kw["volatile"] = _ranges(kw["volatile"])
    return getattr(_native_or_raise(), name)(*args, **kw)
