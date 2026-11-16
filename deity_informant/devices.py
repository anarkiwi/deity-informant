"""Device models: what a machine's device registers answer (``docs/cgen.md``, *Devices*).

A :class:`Model` describes registers by address (mirrors are one register): a read answers a
value set over the abstract device state (:class:`State`) their writes leave; an address it
does not describe answers any byte. A set holds every value the hardware may answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

__all__ = [
    "ALL",
    "Reg",
    "Model",
    "State",
    "submasks",
    "latch",
    "write_only",
    "counter",
    "status",
    "derived",
    "bitmap",
]

#: Every byte.
ALL = frozenset(range(256))


def submasks(mask):
    """The values whose bits all lie in ``mask``, ascending."""
    return [x for x in range(256) if x & ~mask == 0]


@dataclass(frozen=True)
class Reg:  # pylint: disable=too-many-instance-attributes
    """One register of ``kind``: bits ``latch`` read the value held, ``ones`` 1, ``live`` any.

    ``fold(prev, value)`` is what a write holds; ``derive(state, at)`` a derived or status set
    (``at`` None: time unknown), decided by bits ``cmask`` of ``ctrl`` alone once time is
    unknown; ``recur(state, addr)`` the sets a read eventually hits; ``ack`` a read's effect.
    """

    kind: str
    latch: int = 0
    ones: int = 0
    live: int = 0
    ack: bool = False
    fold: Optional[Callable] = None
    derive: Optional[Callable] = None
    ctrl: Optional[int] = None
    cmask: int = 0
    recur: Optional[Callable] = None

    def answers(self, state, addr, at=None):
        """The values a read of the register at ``addr`` answers in ``state`` at ``at``."""
        if self.derive is not None:
            return frozenset(self.derive(state, at))
        w = state.held(addr) if self.latch else 0
        base = submasks(self.latch) if w is None else [w & self.latch]
        live = submasks(self.live)
        return frozenset(b | self.ones | x for b in base for x in live)


def latch(mask=0xFF, ones=0, live=0, fold=None):
    """A register whose bits ``mask`` read the last value written."""
    return Reg("latch", latch=mask, ones=ones, live=live, fold=fold)


def write_only(live=0xFF, ones=0):
    """A register whose reads are unspecified in the bits ``live`` and 1 in ``ones``."""
    return Reg("write-only", ones=ones, live=live)


def counter(live=0xFF, mask=0, ones=0):
    """A register counting through every value of bits ``live`` (bits ``mask`` a latch):
    each value of its live bits recurs, with the latched bits it holds."""

    def recur(state, addr):
        w = state.held(addr) if mask else 0
        base = submasks(mask) if w is None else [w & mask]
        return [frozenset(b | ones | x for b in base) for x in submasks(live)]

    return Reg("counter", latch=mask, ones=ones, live=live, recur=recur)


def status(values, recur=None, ack=False, fold=None):
    """A register answering ``values(state, at)``; ``recur(state, addr)`` the sets a read
    eventually hits; ``ack``: a read changes the device; ``fold`` as :class:`Reg`'s."""
    return Reg("status", ack=ack, fold=fold, derive=values, recur=recur)


def derived(values, ctrl=None, cmask=0):
    """A register answering ``values(state, at)``, decided by bits ``cmask`` of register
    ``ctrl`` alone once time is unknown."""
    return Reg("derived", derive=values, ctrl=ctrl, cmask=cmask)


def bitmap(values):
    """``values`` as 32 bytes: bit ``v & 7`` of byte ``v >> 3`` for each ``v``."""
    bits = np.zeros(256, dtype=bool)
    bits[list(values)] = True
    return np.packbits(bits, bitorder="little").tobytes()


@dataclass(frozen=True)
class Model:
    """A machine's devices: ``regs`` by canonical address, ``mirrors`` ``[(lo, hi, size)]``
    (``a`` in ``[lo, hi)`` is ``lo + (a - lo) % size``), ``elapsed(n)`` the ``(lo, hi)``
    machine cycles of ``n`` CPU cycles between two accesses (DMA stalls)."""

    regs: dict
    mirrors: tuple = ()
    elapsed: Callable = field(default=lambda n: (n, n))

    def canon(self, addr):
        """The canonical address of ``addr``."""
        for lo, hi, size in self.mirrors:
            if lo <= addr < hi:
                return lo + (addr - lo) % size
        return addr

    def reg(self, addr):
        """The register at ``addr``, or None."""
        return self.regs.get(self.canon(addr))

    def described(self, volatile):
        """``{addr: Reg}`` of the described addresses in the ``volatile`` ``[(lo, hi)]``."""
        out = {}
        for lo, hi in volatile:
            for a in range(lo, min(hi, 0x10000)):
                r = self.reg(a)
                if r is not None:
                    out[a] = r
        return out

    def sensitive(self, volatile):
        """The addresses in ``volatile`` whose read changes the device, ascending."""
        return sorted(a for a, r in self.described(volatile).items() if r.ack)

    def lower(self, volatile):
        """``(regs, addrs)`` for the native lifter: ``regs`` ``[(canon, latch, ones, live,
        ctrl, cmask, sets)]`` with ``sets`` bitmaps per value of bits ``cmask`` of ``ctrl``
        (ascending; -1: none, one set), none for a latch; ``addrs`` ``[(addr, canon)]``."""
        described = self.described(volatile)
        addrs = sorted((a, self.canon(a)) for a in described)
        regs = []
        for c in sorted({c for _, c in addrs}):
            r, sets = self.regs[c], []
            if r.derive is not None:
                for v in submasks(r.cmask) if r.ctrl is not None else [None]:
                    st = State(self)
                    if v is not None:
                        st.write(r.ctrl, v, None)
                    sets.append(bitmap(r.answers(st, c)))
            elif not r.latch:
                sets.append(bitmap(r.answers(State(self), c)))
            ctrl = -1 if r.ctrl is None else self.canon(r.ctrl)
            regs.append((c, r.latch, r.ones, r.live, ctrl, r.cmask, sets))
        return regs, addrs


class State:
    """The abstract state of a model's devices: per canonical register the value its writes
    hold (None unknown), the one before the last write and that write's cycle (None after a
    wait of unknown length, :meth:`wait`)."""

    def __init__(self, model):
        self.model = model
        self.vals, self.prev, self.when = {}, {}, {}

    def held(self, addr):
        """The value register ``addr``'s writes hold, or None."""
        return self.vals.get(self.model.canon(addr))

    def last(self, addr):
        """``(value, previous value, cycle)`` of register ``addr``'s last write, or None."""
        c = self.model.canon(addr)
        if c not in self.vals:
            return None
        return self.vals[c], self.prev.get(c), self.when.get(c)

    def write(self, addr, value, at):
        """A write of ``value`` to ``addr`` at cycle ``at``."""
        c = self.model.canon(addr)
        r = self.model.regs.get(c)
        old = self.vals.get(c)
        self.prev[c] = old
        self.vals[c] = value if r is None or r.fold is None else r.fold(old, value)
        self.when[c] = at

    def wait(self):
        """A wait of unknown length: the times of the writes so far are unknown."""
        self.when = dict.fromkeys(self.when)

    def since(self, addr, at):
        """The machine cycles ``(lo, hi)`` from register ``addr``'s last write to CPU cycle
        ``at``, or None where either time is unknown."""
        w = self.last(addr)
        if w is None or w[2] is None or at is None:
            return None
        return self.model.elapsed(at - w[2])

    def read(self, addr, at=None):
        """The values a read of ``addr`` at ``at`` answers, or None where undescribed."""
        r = self.model.reg(addr)
        return None if r is None else r.answers(self, addr, at)

    def recur(self, addr):
        """The value sets a read of ``addr`` eventually hits (none where undescribed)."""
        r = self.model.reg(addr)
        return [] if r is None or r.recur is None else r.recur(self, addr)
