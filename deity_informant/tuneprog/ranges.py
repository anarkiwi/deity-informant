"""What the certified IR proves about the value of a byte of memory.

A cell holds its image byte or the value of a store whose envelope reaches it;
:func:`cell_ranges` iterates that step down from the whole byte, so every
intermediate over-approximates and the first fixpoint is the answer.
"""

from __future__ import annotations

import numpy as np

from .ir import Bin, Const, Load, MASK, Store, Var
from .irwalk import single_defs

BOOL = ("==", "!=", "<", "<=", "carry")


def expr_range(e, mem, defs, seen):
    """The interval of one expression under ``mem``, expanding single definitions."""
    t = type(e)
    if t is Const:
        return e.v, e.v
    if t is Var:
        d = defs.get(e.n)
        if d is None or e.n in seen:
            return 0, MASK[e.w]
        return expr_range(d, mem, defs, seen | {e.n})
    if t is Load:
        if e.w != 1 or e.cls != "ram" or not 0 <= e.lo <= e.hi <= 0xFFFF:
            return 0, MASK[e.w]
        s = slice(e.lo, e.hi + 1)
        return int(mem[0][s].min()), int(mem[1][s].max())
    if t is not Bin:
        return 0, MASK[getattr(e, "w", 2)]
    m = MASK[e.w]
    if e.op in BOOL:
        return 0, 1
    al, ah = expr_range(e.a, mem, defs, seen)
    bl, bh = expr_range(e.b, mem, defs, seen)
    if e.op == "&":
        return 0, min(ah, bh)
    if e.op in ("|", "^"):
        return 0, min(m, (1 << max(ah, bh).bit_length()) - 1)
    if e.op == "+":
        return (al + bl, ah + bh) if ah + bh <= m else (0, m)
    if e.op == "-":
        return (al - bh, ah - bl) if al >= bh else (0, m)
    if e.op == "<<" and bl == bh:
        return (al << bl, ah << bl) if ah << bl <= m else (0, m)
    if e.op == ">>" and bl == bh:
        return al >> bl, ah >> bl
    return 0, m


def cell_ranges(prog):
    """``(lo, hi)`` over the 64 KiB image: the values a byte of memory can hold."""
    img = np.frombuffer(bytes(prog.image()), dtype=np.uint8).astype(np.int32)
    stores = [
        (s, defs)
        for p in prog.procs.values()
        for defs in (single_defs(p),)
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Store and 0 <= s.lo <= min(s.hi, 0xFFFF)
    ]
    mem = (np.zeros(0x10000, np.int32), np.full(0x10000, 0xFF, np.int32))
    while True:
        nlo, nhi = img.copy(), img.copy()
        for s, defs in stores:
            a, b = expr_range(s.v, mem, defs, frozenset()) if s.w == 1 else (0, 0xFF)
            sl = slice(s.lo, min(s.hi, 0xFFFF) + 1)
            np.minimum(nlo[sl], a, out=nlo[sl])
            np.maximum(nhi[sl], b, out=nhi[sl])
        if np.array_equal(nlo, mem[0]) and np.array_equal(nhi, mem[1]):
            return mem
        mem = (nlo, nhi)
