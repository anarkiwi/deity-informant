"""S1 -- the second schedule: the CIA #2 NMI, when it fires and what it enters.

CIA #2's interrupt line is the 6510's NMI. It is edge-triggered and the chip
holds it asserted until an ICR read releases it, so a handler that acknowledges
gets one NMI per underflow and one that does not gets exactly one.
"""

from __future__ import annotations

import numpy as np

from .. import c64
from .cia import CIA2_BASE, ICR_TA, ICR_TB
from .ir import IO_HI, IO_LO, STACK_HI, STACK_LO
from .machine import Entry, Refusal, kernal_mapped

NEVER = 1 << 62  # a due cycle no run reaches: the line cannot assert
STALE = -1  # the due cycle a CIA #2 access invalidates
DISPATCH = 7  # cycles the 6510 spends taking an NMI
KERNAL_STUB = 7  # $FE43's own SEI (2) + JMP ($0318) (5), ahead of the handler
SOURCE = {ICR_TA: "cia2_timer_a", ICR_TB: "cia2_timer_b"}
# the interrupted state a replayed NMI takes from its schedule row rather than
# computing: the stack pointer, the pushed status, the return pc and A/X/Y
REPLAYED = ("sp", "status", "pc", "a", "x", "y")


def vector(mem):
    """``(vector address, handler)`` the 6510 takes its NMI through, under this port.

    With the KERNAL mapped ``$FFFA`` is ROM and reaches ``$FE43``, whose
    ``JMP ($0318)`` is the dispatch; with it banked out the vector is the RAM at
    ``$FFFA``. Neither path saves a register, so the frame is the status byte.
    """
    vec = c64.NMI_VEC[0] if kernal_mapped(mem) else c64.HW_NMI_VEC[0]
    return vec, c64.read_vector(mem, vec)


def dispatch_cycles(mem):
    """Cycles from the underflow to the handler's first instruction, under this port.

    The 6510 spends :data:`DISPATCH`; the KERNAL path spends
    :data:`KERNAL_STUB` more in ``$FE43`` before the vector it dispatches on.
    The port decides both, so this is the entry's ``kernal`` flag by construction.
    """
    return DISPATCH + (KERNAL_STUB if kernal_mapped(mem) else 0)


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
    return Entry("nmi", handler, period(cia2) or 0, sources(cia2), kernal_mapped(mem))


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


class Separable:
    """Checks the one direction a store-granularity replay does not make exact.

    :class:`~.verify.Verifier` defers a queued NMI to just before the interrupted
    routine's next store, so the loads that routine makes between the NMI's real
    instant and that store move from after the handler to before it. Nothing else
    moves: the interrupted routine makes no store inside that window, so the
    handler's own view of shared RAM is the same in both orders, and every
    ``$D000-$DFFF`` read is a pinned input replayed in its own entry's order.

    The schedule is *store-separable* when no such load reads a cell the handler
    wrote. Cells carry the inter-store epoch a handler stamped them in, so a load
    compares one stamp; a load that matches the live epoch is the property failing
    and the tune is refused rather than certified on a replay that reorders it.

    It also checks the register half of the same contract: the replay restores the
    interrupted A/X/Y after a handler and the emitted play routine holds them as
    SSA values no handler can reach, so a handler that returns them moved is
    refused here rather than mis-replayed. An NMI the host's idle time took
    interrupts no routine of the tune, and the next tick re-reads its own entry
    registers, so only an in-tick NMI carries the property.
    """

    __slots__ = ("mark", "epoch", "hot", "dirty", "sp", "inside", "handler", "regs", "in_tick")

    def __init__(self):
        self.mark = np.zeros(0x10000, np.int64)
        self.epoch, self.hot, self.dirty = 1, 0, 0
        self.inside, self.sp, self.handler = False, 0, 0
        self.regs, self.in_tick = (), False

    def begin(self):
        """A new tick: the window the previous one left open ended when it returned."""
        self.epoch += 1
        self.hot = self.dirty = 0

    def enter(self, reg, handler, in_tick=True):
        """Take an NMI on registers ``reg``; a handler's own loads are in no window."""
        if not self.inside:
            self.inside, self.sp = True, reg[3]
            self.regs, self.in_tick = tuple(reg[:3]), in_tick
        self.hot, self.handler = 0, handler

    def leave(self, reg, pc=0):
        """The outermost ``RTI``: the reordered window opens, and A/X/Y must be back."""
        if not (self.inside and reg[3] >= self.sp):
            return
        self.inside = False
        self.hot = self.epoch if self.dirty else 0
        if self.in_tick and tuple(reg[:3]) != self.regs:
            raise Refusal(
                "nmi clobbers registers",
                "nmi $%04X returns at $%04X with A/X/Y %s, not the %s it interrupted"
                % (self.handler, pc, tuple(reg[:3]), self.regs),
            )

    def stored(self, addr, sz):
        """A handler's store stamps the cell; the interrupted routine's closes the window.

        The stack page is no state either entry reads of the other, and an
        ``$D000-$DFFF`` read is a pinned input rather than a load of shared RAM,
        so neither can be reordered into a different value.
        """
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            if STACK_LO <= a <= STACK_HI:
                continue
            if not self.inside:
                self.epoch += 1
                self.hot = self.dirty = 0
            elif not IO_LO <= a <= IO_HI:
                self.mark[a] = self.epoch
                self.dirty = 1

    def load(self, addr, sz, pc):
        """Raise where this load is one the replay would move ahead of the handler."""
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            if self.mark[a] == self.hot:
                raise Refusal(
                    "schedule not store-separable",
                    "$%04X at $%04X reads what nmi $%04X wrote before the next store"
                    % (a, pc, self.handler),
                )
