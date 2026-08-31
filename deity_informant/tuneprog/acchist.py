"""T1 -- one named-cell expression evaluated over the certified horizon.

:mod:`.history` gives the per-tick value of every named cell; a table the program
never writes is its own image byte. :class:`Cells` reads an S6 expression as one
array per tick -- what an accumulator's interval is checked on, and what
:mod:`.accstep` reads each clause's terms from at their own epochs.
"""

from __future__ import annotations

import numpy as np

from .facts import Facts, scales
from .ir import Bin, Const, Load, MASK, R16, Var


def _cmp(op, a, b):
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return a < b if op == "<" else a <= b


def evalarr(op, a, b, w):
    """One IR binary op over whole-horizon arrays, byte-exactly as :func:`~.ir.evalbin`."""
    m = MASK[w]
    if op == "+":
        return (a + b) & m
    if op == "-":
        return (a - b) & m
    if op == "&":
        return a & b
    if op == "|":
        return a | b
    if op == "^":
        return a ^ b
    if op == "<<":
        return (a << b) & m
    if op == ">>":
        return a >> b
    if op == "carry":
        return ((a + b) > m).astype(np.int64)
    if op in ("or", "and"):  # the structurer's merged conditions, not lowered ops
        got = (a != 0) | (b != 0) if op == "or" else (a != 0) & (b != 0)
        return got.astype(np.int64)
    return _cmp(op, a, b).astype(np.int64) if op in ("==", "!=", "<", "<=") else None


class Cells:
    """Every named cell of one program as a column, and expressions over them."""

    def __init__(self, prog, names, hist, facts=None):
        self.prog = prog
        self.names = names
        self.hist = hist
        self.rgn = prog.by_id()
        self.scale = scales(facts or Facts(prog))
        self.img = np.frombuffer(bytes(prog.reads()), np.uint8).astype(np.int64)
        self.ticks = min((a.shape[0] for a in hist.values()), default=0)
        self.group = {}
        for g, v in (names.groups or {}).items():
            for rid in v["members"]:
                self.group[rid] = (g, max(int(v["stride"]), 1), int(v["n"]))
        self._cols = {}
        self.zeros = np.zeros(self.ticks, np.int64)
        self.bad = np.zeros(self.ticks, bool)
        self.subst = {}
        self.counters = {}
        self.scratch = frozenset()
        self.tabstep = {}
        self.obs = None
        self._lag = {}

    def lagged(self, rids):
        """Every sampled byte of ``rids`` one tick back, for :class:`~.trackerprog.hist.Eval`."""
        out = {}
        for rid in rids:
            if rid not in self._lag:
                r = self.rgn.get(rid)
                span = range(r.base, r.base + r.size) if r is not None else ()
                self._lag[rid] = {
                    (rid, a): np.concatenate(([c[0]], c[:-1]))
                    for a in span
                    for c in (self.col(rid, a),)
                    if c is not None
                }
            out.update(self._lag[rid])
        return out

    def col(self, rid, addr):
        """The per-tick values of one byte, where :mod:`.history` sampled it."""
        if (rid, addr) not in self._cols:
            got = self.hist.cell(rid, addr)
            self._cols[(rid, addr)] = None if got is None else got[: self.ticks].astype(np.int64)
        return self._cols[(rid, addr)]

    def byte(self, rid, addr, lo, hi):
        """One byte of state or of the image, inside the region or the access's envelope."""
        got = self.subst.get((rid, addr))
        got = self.col(rid, addr) if got is None else got
        if got is not None:
            return got
        r = self.rgn.get(rid)
        here = r is not None and r.base <= addr < r.base + r.size
        if r is None or r.kind == "state" or not (here or (lo is not None and lo <= addr <= hi)):
            return None
        return np.full(self.ticks, int(self.img[addr]), np.int64)

    def load(self, rid, addr, w, lo, hi):
        """A byte read at a per-tick address array, gathered over the addresses it took.

        An address no name covers is not a failure of the whole value: it is marked
        in :attr:`bad`, which a clause's own guard must exclude.
        """
        if addr is None or w != 1:
            return None
        out = np.zeros(self.ticks, np.int64)
        for a in np.unique(addr):
            c, here = self.byte(rid, int(a), lo, hi), addr == a
            if c is None:
                self.bad |= here
                continue
            out = np.where(here, c, out)
        return out

    def index(self, e, env):
        """An address expression; a copy index is this copy's own displacement."""
        t = type(e)
        if t is Const:
            return np.full(self.ticks, e.v, np.int64)
        if t is Var:
            got = env.get(e.n)
            return None if got is None else np.full(self.ticks, got, np.int64)
        if t is Bin:
            a, b = self.index(e.a, env), self.index(e.b, env)
            return None if a is None or b is None else evalarr(e.op, a, b, max(e.w, 2))
        return self.value(e, env)

    def value(self, e, env):
        """One value over the horizon, or ``None`` where a name it reads has none."""
        t = type(e)
        if t is Const:
            return np.full(self.ticks, e.v, np.int64)
        if t is Var:
            return self.index(e, env)
        if t is Load:
            return self.load(e.r, self.index(e.a, env), e.w, e.lo, e.hi)
        if t is R16:
            lo, hi = self.half(e.lo, e, env), self.half(e.hi, e, env)
            return None if lo is None or hi is None else lo | (hi << 8)
        if t is Bin:
            a, b = self.value(e.a, env), self.value(e.b, env)
            return None if a is None or b is None else evalarr(e.op, a, b, e.w)
        return None

    def half(self, cell, e, env):
        """One half of a 16-bit view: its own cell's address, indexed as the low one is.

        The halves share neither region nor displacement (JCH's shift accumulator is
        two unrelated bytes), so the low half's index is rebased on each.
        """
        a = self.index(e.a, env)
        return None if a is None else self.load(cell[0], a + (cell[1] - e.lo[1]), 1, None, None)


def _lag(a):
    """``a`` one tick back: the value the same expression had over last tick's cells."""
    return np.concatenate(([a[0]], a[:-1]))


def interval(cells, bound, env, elem):
    """``(lo, hi)`` of a bound as arrays, or ``None`` where a cell of it has none."""
    out = []
    for v in bound["interval"]:
        if isinstance(v, int):
            out.append(np.full(cells.ticks, v, np.int64))
            continue
        got = cells.byte(v["region"], int(v["addr"][1:], 16) + elem * v.get("scale", 0), None, None)
        if got is None:
            return None
        out.append(got)
    return tuple(out)
