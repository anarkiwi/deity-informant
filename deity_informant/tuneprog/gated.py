"""S6 -- the expression rewrites the certified IR's intervals prove over the view.

:mod:`.ranges` is the one interval domain: it decides a mask and a comparison, and
reads a two-armed branch whose arms differ by one as the borrow it is. The pass runs
after :func:`~.texture.propagate` and folds what it decides with
:func:`~.halves.zerofold`.
"""

from __future__ import annotations

from .graph import preds_of
from .halves import zerofold
from .idioms import CMP
from .ir import Bin, Const, Goto, If, Let
from .irwalk import apply_stmt, apply_term, single_defs
from .ranges import expr_range
from .ssa import prune


def diamonds(proc):
    """``[(label, arm, arm, join, name)]`` for each two-armed one-value branch."""
    preds, out = preds_of(proc), []
    for lbl, b in proc.blocks.items():
        if type(b.term) is not If:
            continue
        arms = (b.term.t, b.term.f)
        if any(a not in proc.blocks or a == lbl for a in arms) or arms[0] == arms[1]:
            continue
        blks = [proc.blocks[a] for a in arms]
        if any(type(x.term) is not Goto or len(preds[a]) != 1 for x, a in zip(blks, arms)):
            continue
        if blks[0].term.to != blks[1].term.to or blks[0].term.to in arms:
            continue
        if any(len(x.stmts) != 1 or type(x.stmts[0]) is not Let for x in blks):
            continue
        if blks[0].stmts[0].n == blks[1].stmts[0].n:
            out.append((lbl, arms[0], arms[1], blks[0].term.to, blks[0].stmts[0].n))
    return out


def _decide(op, lo, hi, j):
    """The constant a comparison against ``j`` takes over ``[lo, hi]``, or ``None``."""
    if op == "<":
        return 1 if hi < j else 0 if lo >= j else None
    if op == "<=":
        return 1 if hi <= j else 0 if lo > j else None
    return None if lo <= j <= hi else (0 if op == "==" else 1)


def _rule(e, rng):
    """One node under the interval, else the ordinary algebra: a decided term folds here."""
    if type(e) is not Bin or type(e.b) is not Const:
        return zerofold(e)
    if e.op == "&":
        hi, j = rng(e.a)[1], e.b.v
        if hi <= j and not j & (j + 1):
            return e.a
        return Const(0, e.w) if j and hi < (j & -j) else zerofold(e)
    if e.op not in CMP:
        return zerofold(e)
    got = _decide(e.op, *rng(e.a), e.b.v)
    return Const(got, 1) if got is not None else zerofold(e)


def _one(e):
    return type(e) is Const and e.v == 1


def _step(a, b):
    """``+1``/``-1`` when ``a`` is ``b`` bumped by one, else ``None``."""
    if type(a) is not Bin or a.op not in ("+", "-") or not _one(a.b) or a.a != b:
        return None
    return 1 if a.op == "+" else -1


def _borrow(cond, arm_t, arm_f, rng):
    """Two arms differing by one, as the base plus or minus the test, or ``None``.

    ``If`` takes its true arm on any non-zero value, so the order that reads the
    condition as the borrow itself needs the interval to prove it is one bit.
    """
    for base, other, neg in ((arm_t, arm_f, True), (arm_f, arm_t, False)):
        d = _step(other, base)
        if d is None or (not neg and rng(cond)[1] > 1):
            continue
        c = zerofold(Bin("==", cond, Const(0), 1)) if neg else cond
        return zerofold(Bin("+" if d > 0 else "-", base, c, other.w))
    return None


def _fold_diamonds(proc, rng):
    """Every diamond whose arms are a borrow becomes one assignment in its head."""
    n = 0
    for lbl, t, f, join, name in diamonds(proc):
        if t not in proc.blocks or f not in proc.blocks:
            continue
        b = proc.blocks[lbl]
        got = _borrow(b.term.c, proc.blocks[t].stmts[0].e, proc.blocks[f].stmts[0].e, rng)
        if got is None:
            continue
        b.stmts.append(Let(name, got))
        b.term = Goto(join)
        del proc.blocks[t], proc.blocks[f]
        n += 1
    return n


def ranged(proc, mem):
    """Every interval-proved rewrite over one procedure; returns the number of sites."""
    defs = single_defs(proc)

    def rng(e):
        return expr_range(e, mem, defs, frozenset())

    n, dead = [0], False

    def fn(e):
        out = _rule(e, rng)
        n[0] += out is not e
        return out

    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
        apply_term(b.term, fn)
        if type(b.term) is If and type(b.term.c) is Const:
            b.term, dead = Goto(b.term.t if b.term.c.v else b.term.f), True
            n[0] += 1
    if dead:
        prune(proc)
    got = _fold_diamonds(proc, rng)
    if got:
        prune(proc)
    return n[0] + got
