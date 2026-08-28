"""T2 -- a resolved expression evaluated over the certified horizon.

:class:`~.tuneprog.acchist.Cells` reads a named cell as one array per tick; this
adds the :class:`~.resolve.Sel` node, whose alternatives are chosen tick by tick
by the truth of their own guards, read under both epochs of a cell the tick moved
exactly as :func:`~.tuneprog.acchist.truth` reads them.
"""

from __future__ import annotations

import numpy as np

from ..tuneprog.acchist import evalarr
from ..tuneprog.ir import Bin, Const, Load, R16, Var
from .resolve import Sel


class Eval:
    """Values over the horizon; ``bad`` marks the ticks some name had no column for."""

    def __init__(self, cells):
        self.cells = cells
        self.ticks = cells.ticks
        self.bad = np.zeros(self.ticks, bool)
        self.blind = 0

    def value(self, e, env):
        t = type(e)
        if t is Const:
            return np.full(self.ticks, e.v, np.int64)
        if t is Var:
            got = env.get(e.n)
            return None if got is None else np.full(self.ticks, int(got), np.int64)
        if t is Bin:
            a, b = self.value(e.a, env), self.value(e.b, env)
            return None if a is None or b is None else evalarr(e.op, a, b, e.w if e.w else 2)
        if t is Load:
            a = self.value(e.a, env)
            was = self.cells.bad
            self.cells.bad = np.zeros(self.ticks, bool)
            out = self.cells.load(e.r, a, e.w, e.lo, e.hi)
            self.bad |= self.cells.bad
            self.cells.bad = was
            return out
        if t is R16:
            lo, hi = self.cells.half(e.lo, e, env), self.cells.half(e.hi, e, env)
            return None if lo is None or hi is None else lo | (hi << 8)
        if t is Sel:
            if not e.alts:
                return None
            out = self.value(e.alts[0][1], env)
            for gs, x in e.alts[1:]:
                # the alternative's value, at the epoch its own guards were read in
                lag = {}
                for _c, _t, *w in gs:
                    lag.update(self.cells.lagged(w[0]) if w else {})
                was, self.cells.subst = self.cells.subst, {**self.cells.subst, **lag}
                try:
                    v = self.value(x, env)
                finally:
                    self.cells.subst = was
                m = self.truth(gs, env)
                if out is None or v is None:
                    return None
                out = np.where(m, v, out)
            return out
        return None

    def truth(self, guards, env):
        """The ticks a guard path held, over-approximated where a condition is unread.

        A cell the tick writes after the condition is read at last tick's value: the
        one the condition saw. Selecting a value needs one epoch, not T1's union.
        """
        out = np.ones(self.ticks, bool)
        for c, t, *w in guards:
            lag = self.cells.lagged(w[0]) if w else {}
            got = self._held(c, t, env, {**self.cells.subst, **lag})
            if got is None:
                self.blind += 1
                continue
            out &= got
        return out

    def _held(self, c, t, env, subst):
        was, self.cells.subst = self.cells.subst, subst
        try:
            v = self.value(c, env)
        finally:
            self.cells.subst = was
        if v is None:
            return None
        return (v != 0) if t else (v == 0)
