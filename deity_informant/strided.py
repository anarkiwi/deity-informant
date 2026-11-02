"""Strided intervals (Balakrishnan & Reps' value-set analysis) with a bounded membership map.

A :class:`Strided` is ``{lo + k * stride : 0 <= k <= (hi - lo) / stride}``, the least such set
holding every value seen, plus the exact members while the lattice has at most :data:`CAP`
points. :func:`fold` adds batches of ``(key, value)`` pairs to per-key intervals in numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd

import numpy as np

__all__ = ["CAP", "Strided", "fold"]

#: Lattice points beyond which the members are dropped and only the interval is kept.
CAP = 256


@dataclass(frozen=True)
class Strided:
    """``lo``, ``hi``, ``stride`` (0 for a single value) and ``bits``: bit ``k`` set when
    ``lo + k * stride`` was seen, ``None`` past :data:`CAP` lattice points."""

    lo: int
    hi: int
    stride: int = 0
    bits: int | None = 1

    @property
    def points(self):
        """Lattice points of the interval."""
        return (self.hi - self.lo) // self.stride + 1 if self.stride else 1

    @property
    def count(self):
        """Distinct values seen, ``None`` once the members are dropped."""
        return None if self.bits is None else self.bits.bit_count()

    def values(self):
        """The members seen (``None`` once dropped)."""
        if self.bits is None:
            return None
        return [self.lo + k * self.stride for k in range(self.points) if self.bits >> k & 1]

    def __contains__(self, v):
        if not self.lo <= v <= self.hi or (self.stride and (v - self.lo) % self.stride):
            return False
        if self.stride == 0 or self.bits is None:
            return True
        return bool(self.bits >> (v - self.lo) // self.stride & 1)

    def join(self, other):
        """The least strided interval holding both, its members while both are known."""
        lo, hi = min(self.lo, other.lo), max(self.hi, other.hi)
        s = gcd(self.stride, other.stride, abs(self.lo - other.lo))
        a, b = self.values(), other.values()
        return _make(lo, hi, s, None if a is None or b is None else a + b)

    def to_list(self):
        return [self.lo, self.hi, self.stride, None if self.bits is None else "%x" % self.bits]

    @classmethod
    def from_list(cls, v):
        lo, hi, s, bits = v
        return cls(int(lo), int(hi), int(s), None if bits is None else int(bits, 16))

    @classmethod
    def of(cls, values):
        """The strided interval of an iterable of integers."""
        v = np.unique(np.asarray(list(values), np.int64))
        return _make(int(v[0]), int(v[-1]), int(np.gcd.reduce(v - v[0])), v.tolist())


def _make(lo, hi, s, members):
    if members is None or (s and (hi - lo) // s >= CAP):
        return Strided(lo, hi, s, None)
    bits = 0
    for v in members:
        bits |= 1 << ((v - lo) // s if s else 0)
    return Strided(lo, hi, s, bits)


def fold(state, keys, values):
    """Join each ``values[i]`` into ``state[keys[i]]`` (a dict of :class:`Strided`); keys and
    values are non-negative, values below 2**32."""
    if len(keys) == 0:
        return state
    u = np.unique(np.asarray(keys, np.int64) << 32 | np.asarray(values, np.int64))
    k, v = u >> 32, u & 0xFFFFFFFF
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    lo = v[starts]
    hi = v[np.r_[starts[1:], len(v)] - 1]
    rel = v - np.repeat(lo, np.diff(np.r_[starts, len(v)]))
    stride = np.gcd.reduceat(rel, starts)
    bounds = np.r_[starts, len(v)]
    for i, key in enumerate(k[starts].tolist()):
        s = _make(int(lo[i]), int(hi[i]), int(stride[i]), v[bounds[i] : bounds[i + 1]].tolist())
        old = state.get(key)
        state[key] = s if old is None else old.join(s)
    return state
