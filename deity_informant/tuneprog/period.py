"""Why a subtune's state does not repeat: per-cell periods, drift, the observable.

Which cells block the repeat, whether each is a counter (a period that does not
divide the music loop) or an accumulator (a constant drift per loop), and whether
the SID stream repeats at that loop -- when it does not, nothing certifies it.
"""

from __future__ import annotations

from collections import Counter

from .ir import STACK_HI, STACK_LO


def min_period(seq):
    """The smallest ``p`` with ``seq[i] == seq[i + p]`` everywhere, else ``None``.

    The KMP failure function: the smallest period of a sequence is its length
    less its longest border.
    """
    n = len(seq)
    fail = [0] * (n + 1)
    k = 0
    for i in range(1, n):
        while k and seq[i] != seq[k]:
            k = fail[k]
        if seq[i] == seq[k]:
            k += 1
        fail[i + 1] = k
    p = n - fail[n]
    return None if p == n else p


class Samples:
    """One byte per tick per footprint cell, and each tick's SID write list.

    The stack page is left out: a program :func:`~.stack.eliminate` proved
    stack-free claims periodicity on the footprint without it. ``vm`` seeds the
    write log's cursor, so ``init``'s own SID writes are nobody's tick.
    """

    __slots__ = ("cols", "n", "writes", "first", "_at")

    def __init__(self, vm=None):
        self.cols = {}
        self.n = 0
        self.writes = []
        self.first = {}
        self._at = 0 if vm is None else len(vm.sidlog[1])

    def add(self, vm):
        """Record one tick: the footprint's bytes and the writes it made."""
        for a in vm.written_play:
            if STACK_LO <= a <= STACK_HI:
                continue
            v = vm.mem[a]
            col = self.cols.get(a)
            if col is None:
                col = self.cols[a] = bytearray([v]) * self.n
                self.first[a] = self.n
            col.append(v)
        self.n += 1
        end = len(vm.sidlog[1])
        self.writes.append(
            bytes(vm.sidlog[1][self._at : end]) + bytes(vm.sidlog[2][self._at : end])
        )
        self._at = end

    def tail(self, col):
        """The second half of one per-tick series."""
        return bytes(col[-(self.n // 2) :])


def _drift(col, loop, n, start=0):
    """The per-loop deltas of one cell, from the tick it was first written."""
    return sorted({(col[i + loop] - col[i]) & 0xFF for i in range(start, n - loop, loop)})


def classify(s):
    """The obstruction to a repeat: the loop, its blockers, and the observable.

    ``loop`` is the SID stream's own period, or -- when it has none in the window
    -- the period most non-constant cells share; a blocker is a cell whose period
    does not divide it, and one with no period at all is a drifting accumulator.
    """
    half = s.n // 2
    per = {a: min_period(s.tail(c)) for a, c in s.cols.items()}
    found = Counter(p for p in per.values() if p is not None and p > 1)
    obs = min_period(s.writes[-half:]) if half else None
    loop = obs if obs is not None else (found.most_common(1)[0][0] if found else None)
    out = {
        "ticks": s.n,
        "cells": len(s.cols),
        "loop": loop,
        "observable_period": obs,
        "blockers": [
            {
                "addr": "$%04X" % a,
                "period": p,
                "drift": _drift(s.cols[a], loop, s.n, s.first[a]) if loop else [],
            }
            for a, p in sorted(per.items())
            if p is None or loop is None or loop % p
        ],
        "observable_mismatch": None,
        "verdict": "no loop" if not s.n else ("aperiodic" if obs is None else "periodic"),
    }
    if loop is not None:
        bad = [i for i in range(s.n - loop) if s.writes[i] != s.writes[i + loop]]
        out["observable_mismatch"] = {"ticks": len(bad), "of": s.n - loop, "first": bad[:1]}
    if obs is not None and out["blockers"]:
        out["verdict"] = "state only"
    return out
