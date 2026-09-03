"""B7 -- the linear order the lowering emits: the blocks, each inner loop unrolled.

One turn of a loop is one copy of its body, and the condition one more turn is
taken is a cell the turn that ran leaves -- read after the latch's own copy, the
term itself is a turn behind.
"""

from __future__ import annotations

from ..tuneprog.ir import Bin, Const, If, Let, Var
from .cells import ident
from .flow import fold

FLAG, RESET = "!flag", "!reset"  # a join's own cell: the row that raises it, and its reset
TRUTH = ("==", "!=", "<", "<=", "carry")  # a comparison the lowering has a 0/1 for


def sequence(low, blocks, trips, reset=()):
    """The blocks in reverse postorder, each inner loop unrolled to its own bound."""
    inner = {h: v for h, v in low.loops.items() if h in blocks and len(v[0]) < len(blocks)}
    body = {l for _h, (b, _x) in inner.items() for l in b}
    low.turn = None
    if frozenset(blocks) <= low.planned:
        eff, flagrows, flags = low.eff, low.flagrows, list(reset)
    else:
        eff, flagrows = low.plan(blocks)
        flags = sorted({n for v in flagrows.values() for n, _c in v})
    out = [(RESET, tuple(flags), {}, (), None)] if flags else []
    for lbl in low.rpo:
        if lbl not in blocks:
            continue
        if lbl in inner:
            out += _unroll(low, lbl, inner[lbl], trips.get(lbl, 1), eff)
        elif lbl not in body:
            guard, extra = eff.get(lbl) or (low._own(lbl), ())
            out.append((lbl, extra, {}, guard, None))
        for name, (guard, extra) in flagrows.get(lbl, ()):
            out.append(((FLAG, name, lbl), extra, {}, guard, None))
    return out


def _unroll(low, head, loop, k, eff=None):
    """One inner loop, unrolled: repetition ``j`` under the edge that continues it.

    A body block a join carries takes the guard the plan gave it and not its
    own control-dependence path, exactly as a block outside the loop does.
    """
    eff = eff or {}
    body, latches = loop
    order = [l for l in low.rpo if l in body]
    seen = {(id(c), t) for _d, c, t, _w in low.guards.get(head, ())}
    # one more turn is taken where a back edge is: the *disjunction* of the
    # latches' own paths, folded as a join's is (two that differ in one term
    # and its negation are the one path that term does not decide)
    paths = []
    for lat in sorted(latches):
        g = tuple(
            (d, c, t)
            for d, c, t, _w in low.guards.get(lat, ())
            if (id(c), t) not in seen and low.onpath(d, c, t)
        )
        if g not in paths:
            paths.append(g)
    cont = [(d, c, t) for g, _x in fold([(g, ()) for g in paths]) for d, c, t in g]
    if not cont:
        t = low.proc.blocks[head].term
        cont = [
            (head, t.c, truth)
            for lbl, truth in ((t.t, True), (t.f, False))
            if type(t) is If and lbl in body
        ][:1]
    # the condition one more turn is taken, kept where the turn that ran leaves
    # it: read after the latch's own copy, the term itself is a turn behind
    name = "k" + ident(head)
    low.cells.declare(name, None)
    at = max((order.index(d) for d, _c, _t in cont if d in order), default=len(order) - 1)
    ind = induction(low, head, body, latches)
    keep, out = low.local, []
    for j in range(max(int(k), 1) + 1):
        step = {n: (c + j * d) & 0xFF for n, (c, d) in ind.items()}
        low.local = {n: (c + (j - 1) * d) & 0xFF for n, (c, d) in ind.items()} if j else {}
        terms = () if not j else (({"cell": name}, "!=", 0),)
        if j == max(int(k), 1):
            out.append((None, terms, {}, None, None))
            break
        low.local = step
        for i, l in enumerate(order):
            got = eff.get(l, (None, ()))
            out.append((l, terms + tuple(got[1]), step, got[0], j))
            if i == at:
                gd = eff.get(l, (None, ()))[0]
                out.append(((FLAG, name, l, _cont(low, l, cont)), terms, {}, gd, j))
    low.local = keep
    return out


def _cont(low, lbl, cont):
    """The condition one more turn is taken, as the value a cell holds it as."""
    low.lbl = lbl
    got = [truthvalue(low, c, t) for _d, c, t in cont]
    node = got[0]
    for x in got[1:]:
        node = {"and": [node, x]}
    return node


def truthvalue(low, c, t):
    """One condition in a value position: the 0 or 1 a cell reads it back as."""
    v = low.value(c if type(c) is Bin and c.op in TRUTH else Bin("!=", c, Const(0, 1), 1))
    return v if t else {"xor": [v, 1]}


def induction(low, head, body, latches):
    """``{name: (entry value, step)}`` for a loop index the object states outright.

    An index that counts down steps by the constant its own subtraction takes
    away, so the two forms are one datum.
    """
    out = {}
    for lat in sorted(latches):
        for s in low.proc.blocks[lat].stmts:
            if type(s) is not Let or type(s.e) is not Var:
                continue
            d = low.defs.get(s.e.n)
            if not (type(d) is Bin and d.op in ("+", "-") and type(d.b) is Const):
                continue
            if type(d.a) is not Var or d.a.n != s.n:
                continue
            c = _entry(low, s.n, body)
            if c is not None:
                out[s.n] = (c, -d.b.v if d.op == "-" else d.b.v)
    del head
    return out


def _entry(low, name, body):
    """The constant a loop index enters with, where a chain of copies gives one."""
    for lbl, b in low.proc.blocks.items():
        if lbl in body:
            continue
        for s in b.stmts:
            if type(s) is not Let or s.n != name:
                continue
            e, seen = s.e, 0
            while type(e) is Var and e.n in low.defs and seen < 8:
                e, seen = low.defs[e.n], seen + 1
            if type(e) is Const:
                return e.v
    return None
