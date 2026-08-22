"""S1 -- the second schedule: the CIA #2 NMI, when it fires and what it enters.

CIA #2's interrupt line is the 6510's NMI. It is edge-triggered and the chip
holds it asserted until an ICR read releases it, so a handler that acknowledges
gets one NMI per underflow and one that does not gets exactly one.
"""

from __future__ import annotations

from .. import c64
from .cia import CIA2_BASE, ICR_TA, ICR_TB
from .machine import Entry, Refusal, kernal_mapped

NEVER = 1 << 62  # a due cycle no run reaches: the line cannot assert
STALE = -1  # the due cycle a CIA #2 access invalidates
DISPATCH = 7  # cycles the 6510 spends taking an NMI
SOURCE = {ICR_TA: "cia2_timer_a", ICR_TB: "cia2_timer_b"}


def vector(mem):
    """``(vector address, handler)`` the 6510 takes its NMI through, under this port.

    With the KERNAL mapped ``$FFFA`` is ROM and reaches ``$FE43``, whose
    ``JMP ($0318)`` is the dispatch; with it banked out the vector is the RAM at
    ``$FFFA``. Neither path saves a register, so the frame is the status byte.
    """
    vec = c64.NMI_VEC[0] if kernal_mapped(mem) else c64.HW_NMI_VEC[0]
    return vec, c64.read_vector(mem, vec)


def sources(cia2):
    """The names of the enabled CIA #2 sources, in mask order."""
    return "+".join(SOURCE[b] for b in (ICR_TA, ICR_TB) if cia2.sources() & b)


def period(cia2):
    """Cycles between two NMIs of the soonest enabled source, or ``None``."""
    steps = [g[1] for b in (ICR_TA, ICR_TB) if cia2.sources() & b for g in (cia2.grid(b),) if g]
    return min(steps) if steps else None


def check(cia2):
    """Raise where CIA #2 is armed on a source this model carries no schedule for."""
    bad = cia2.unmodelled()
    if bad:
        raise Refusal(
            "second interrupt source armed",
            "cia2 $%04X icr=$%02X unmodelled=$%02X crb=$%02X"
            % (CIA2_BASE, cia2.icr, bad, cia2.crb),
        )


def entry(cia2, mem):
    """The NMI :class:`~.machine.Entry` this machine dispatches, or ``None``.

    Refuses a source with no schedule and a line no vector answers: both are
    second schedules that would otherwise go unmodelled.
    """
    check(cia2)
    if not cia2.sources():
        return None
    vec, handler = vector(mem)
    if not handler:
        raise Refusal("nmi vector banked out", "$%04X carries no handler" % vec)
    return Entry("nmi", handler, period(cia2) or 0, sources(cia2))


def entries(trace):
    """The NMI entry addresses of a trace's schedule."""
    return [e["addr"] for e in trace.meta.get("schedule", ()) if e["kind"] == "nmi"]


def reach(trace, *starts):
    """Executed pcs one entry reaches: its own code, and the callees it shares.

    A ``JSR`` reaches its callee and continues at its return pc; an ``RTS`` or
    ``RTI`` leaves the entry, so its observed targets are the interrupted
    program's and are not followed.
    """
    succ = {}
    for f, _o, t in trace.edges:
        succ.setdefault(f, set()).add(t)
    for (pc, _o), c in trace.calls.items():
        succ.setdefault(pc, set()).update(c["targets"])
        succ[pc].add(c["ret_pc"])
    for pc, _o in trace.rets:
        succ.setdefault(pc, set())
    executed = {k[0] for k in trace.sites}
    seen, work = set(), list(starts)
    while work:
        pc = work.pop()
        if pc in seen or pc not in executed:
            continue
        seen.add(pc)
        work.extend(succ.get(pc, ()))
    return seen


def sites(trace):
    """The pcs the schedule's NMI entries reach: the second entry's own code."""
    return reach(trace, *entries(trace))
