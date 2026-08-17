"""Traversal of the IR: rewrite every expression a statement reads, collect its names.

The passes of :mod:`.ssa` and every S5/S6 presentation module share these walks,
so they live apart from the pass that first needed them.
"""

from __future__ import annotations

from collections import Counter

from .ir import Assert, Bin, Call, If, Let, Load, Phi, REGIDX, Return, Store, Switch, Var

IMPURE = ("io", "chk")  # a load of these classes can consume a pinned input


def sub_expr(e, fn):
    """Bottom-up rewrite of ``e``; ``fn`` sees each node after its children."""
    t = type(e)
    if t is Bin:
        a, b = sub_expr(e.a, fn), sub_expr(e.b, fn)
        if a is not e.a or b is not e.b:
            e = Bin(e.op, a, b, e.w)
    elif t is Load:
        a = sub_expr(e.a, fn)
        if a is not e.a:
            e = Load(e.cls, a, e.w, e.lo, e.hi, e.r)
    return fn(e)


def apply_stmt(s, fn):
    """Rewrite every expression a statement reads (phi arguments are names)."""
    t = type(s)
    if t is Let:
        s.e = sub_expr(s.e, fn)
    elif t is Store:
        s.a = sub_expr(s.a, fn)
        s.v = sub_expr(s.v, fn)
    elif t is Call:
        s.args = tuple(sub_expr(a, fn) for a in s.args)
    elif t is not Phi:
        s.e = sub_expr(s.e, fn)


def apply_term(t, fn):
    k = type(t)
    if k is If:
        t.c = sub_expr(t.c, fn)
    elif k is Switch:
        t.e = sub_expr(t.e, fn)
    elif k is Return:
        t.vals = tuple(sub_expr(v, fn) for v in t.vals)


def defs_of(s):
    t = type(s)
    if t is Let or t is Phi:
        return (s.n,)
    return s.rets if t is Call else ()


def uses_of(e, out, regs_only=False):
    t = type(e)
    if t is Var:
        if not regs_only or e.n in REGIDX:
            out.add(e.n)
    elif t is Bin:
        uses_of(e.a, out, regs_only)
        uses_of(e.b, out, regs_only)
    elif t is Load:
        uses_of(e.a, out, regs_only)
    return out


def stmt_uses(s, out, regs_only=False):
    """Collect the names a statement reads (phi arguments belong to its predecessors)."""
    t = type(s)
    if t is Let or t is Assert:
        uses_of(s.e, out, regs_only)
    elif t is Store:
        uses_of(s.a, out, regs_only)
        uses_of(s.v, out, regs_only)
    elif t is Call:
        for a in s.args:
            uses_of(a, out, regs_only)
    elif t is Phi and not regs_only:
        out.update(s.args.values())
    return out


def term_uses(t, out, regs_only=False):
    k = type(t)
    if k is If:
        uses_of(t.c, out, regs_only)
    elif k is Switch:
        uses_of(t.e, out, regs_only)
    elif k is Return:
        for v in t.vals:
            uses_of(v, out, regs_only)
    return out


class _Tally:
    """A ``set``-shaped sink that counts instead of collecting."""

    __slots__ = ("c",)

    def __init__(self):
        self.c = Counter()

    def add(self, n):
        self.c[n] += 1

    def update(self, it):
        for n in it:
            self.c[n] += 1


def use_counts(proc):
    """``Counter`` of every use of every variable in ``proc`` (phi arguments included)."""
    t = _Tally()
    for b in proc.blocks.values():
        for s in b.stmts:
            stmt_uses(s, t)
        term_uses(b.term, t)
    return t.c


def pure(e):
    """True when evaluating ``e`` has no effect (a pinned input read has one)."""
    t = type(e)
    if t is Load:
        return e.cls not in IMPURE and pure(e.a)
    if t is Bin:
        return pure(e.a) and pure(e.b)
    return True
