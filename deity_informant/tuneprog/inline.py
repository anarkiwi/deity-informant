"""S6 -- value inlining over the presentation copy: a let folded into its uses.

Regions are disjoint by construction, so a load may move past stores to other
regions and past ``sidw``/``iow``; a store to its own region, a call or an input
read stops it. A use may sit in another block when only the defining block
reaches it.
"""

from __future__ import annotations

import networkx as nx

from .ir import Bin, Const, If, Let, Load, Return, Store, Switch, Trap, Var, retval, succs
from .ssa import preds_of
from .irwalk import stmt_uses, sub_expr, term_uses, use_counts


def _pure(e):
    t = type(e)
    if t is Load:
        return False
    return _pure(e.a) and _pure(e.b) if t is Bin else True


def _sub_stmt(s, fn):
    t = type(s)
    if t is Let:
        s.e = sub_expr(s.e, fn)
    elif t is Store:
        s.a = sub_expr(s.a, fn)
        s.v = sub_expr(s.v, fn)
    elif t.__name__ == "Call":
        s.args = tuple(sub_expr(a, fn) for a in s.args)
    elif t.__name__ == "Assert":
        s.e = sub_expr(s.e, fn)


def _sub_term(t, fn):
    k = type(t)
    if k is If:
        t.c = sub_expr(t.c, fn)
    elif k is Switch:
        t.e = sub_expr(t.e, fn)
    elif k is Return:
        t.vals = tuple(sub_expr(v, fn) for v in t.vals)


def _folder(avail, gone):
    def fn(e):
        if type(e) is not Var or e.n not in avail:
            return e
        gone.add(e.n)
        return avail[e.n]

    return fn


def _reads(e, pred):
    t = type(e)
    if t is Load:
        return pred(e) or _reads(e.a, pred)
    return _reads(e.a, pred) or _reads(e.b, pred) if t is Bin else False


def _clobbers(s, e):
    """True when statement ``s`` can change the value of ``e``, or reorder its input.

    Regions are disjoint by construction, so a load of region R survives every
    store to another region and every ``sidw``/``iow``; a call may write anything
    and one input read never moves past another.
    """
    t = type(s)
    if t.__name__ == "Call":
        return not _pure(e)
    if _reads(e, _input) and any(_reads(x, _input) for x in _exprs(s)):
        return True
    if t is not Store:
        return False
    if s.cls == "io":
        return _reads(e, lambda x: x.cls == "io")
    if s.cls == "raw":  # a JSR frame is memory: it clobbers the slots it covers
        return _reads(e, lambda x: x.lo <= s.hi and x.hi >= s.lo)
    return _reads(e, lambda x: x.r < 0 or x.r == s.r or s.r < 0)


def _exprs(s):
    """The expressions a statement evaluates."""
    t = type(s)
    if t is Store:
        return (s.a, s.v)
    return (s.e,) if hasattr(s, "e") else ()


def _cost(e):
    """Operator count of an expression, address arithmetic included."""
    t = type(e)
    if t is Bin:
        return 1 + _cost(e.a) + _cost(e.b)
    return _cost(e.a) if t is Load else 0


def _loads(e, single, cache, seen=()):
    """Every load the value of ``e`` reads once its single-definition names expand."""
    out = []
    for x in _walk(e):
        if type(x) is Load:
            out.append(x)
        elif type(x) is Var and x.n in single and x.n not in seen:
            key = x.n
            if key not in cache:
                cache[key] = _loads(single[key][2], single, cache, seen + (key,))
            out += cache[key]
    return out


def _walk(e):
    yield e
    t = type(e)
    if t is Bin:
        yield from _walk(e.a)
        yield from _walk(e.b)
    elif t is Load:
        yield from _walk(e.a)


def _kills(s, ls):
    return any(_clobbers(s, x) for x in ls)


def _own(proc, preds, lbl):
    """``{block: its predecessor}`` for the blocks only ``lbl`` can reach."""
    out, work = {}, [lbl]
    while work:
        cur = work.pop()
        for nxt in succs(proc.blocks[cur].term):
            if nxt in out or nxt == lbl or preds.get(nxt) != [cur]:
                continue
            out[nxt] = cur
            work.append(nxt)
    return out


def _blocked(proc, own, lbl, at, pos, ls):
    """True when a store, a call or a foreign block sits between the value and a use."""
    for ublk, uidx in pos:
        if ublk == lbl:
            if _kills_run(proc, lbl, at + 1, uidx, ls):
                return True
            continue
        if ublk not in own:
            return True
        if _kills_run(proc, ublk, 0, uidx, ls):
            return True
        cur = own[ublk]
        while cur != lbl:
            if _kills_run(proc, cur, 0, len(proc.blocks[cur].stmts), ls):
                return True
            cur = own[cur]
        if _kills_run(proc, lbl, at + 1, len(proc.blocks[lbl].stmts), ls):
            return True
    return False


def _kills_run(proc, lbl, lo, hi, ls):
    return any(_kills(s, ls) for s in proc.blocks[lbl].stmts[lo:hi])


class _Tally:
    """A ``set``-shaped sink that keeps every occurrence, not every name."""

    __slots__ = ("hits",)

    def __init__(self):
        self.hits = []

    def add(self, n):
        self.hits.append(n)

    def update(self, it):
        self.hits.extend(it)


def _positions(proc):
    """``{name: [(block, index)] once per use}``; the terminator counts last."""
    where = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(list(b.stmts) + [b.term]):
            t = _Tally()
            (term_uses if i == len(b.stmts) else stmt_uses)(s, t)
            for u in t.hits:
                where.setdefault(u, []).append((lbl, i))
    return where


def loads(proc, live=None):
    """Fold a value into its uses, past statements that cannot alias it.

    A use may sit in another block when that block is reachable only through the
    definition's; regions are disjoint, so only a store to the same region, a call
    or an input read stops the move.
    """
    if live is not None:
        want = retval(proc)
        slot = proc.rets.index(0) if want is not None else None
        for b in proc.blocks.values():
            b.stmts = [s for s in b.stmts if type(s) is not Let or s.n in live]
            if type(b.term) is Return:
                # the machine's return plumbing goes; the value the host reads stays
                b.term = Return(
                    ()
                    if slot is None
                    else tuple(v if i == slot else Const(0) for i, v in enumerate(b.term.vals))
                )
    uses, where, preds = use_counts(proc), _positions(proc), preds_of(proc)
    latches = _latches(proc, preds)
    defs = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            if type(s) is Let:
                defs.setdefault(s.n, []).append((lbl, i, s.e))
    single = {n: v[0] for n, v in defs.items() if len(v) == 1}
    sub, cache, owns = {}, {}, {}
    for n, (lbl, at, e) in sorted(single.items()):
        pos = where.get(n, ())
        if not pos or len(pos) != uses[n] or type(e) is Var or _feeds_copy(proc, pos, latches):
            continue
        ls = _loads(e, single, cache)
        if len(pos) > 1 and (_cost(e) > 1 or any(_input(x) for x in ls)):
            continue
        own = owns.setdefault(lbl, _own(proc, preds, lbl))
        if not _blocked(proc, own, lbl, at, pos, ls):
            sub[n] = e
    return _apply_sub(proc, sub)


def _latches(proc, preds):
    """The blocks that close a loop: their copies are the induction variables."""
    g = _cfg(proc)
    idom = nx.immediate_dominators(g, proc.entry)
    return {l for _b, ls in _natural_loops(g, idom, preds).values() for l in ls}


def _feeds_copy(proc, pos, latches):
    """A value a loop-closing rename reads stays: it carries the induction variable."""
    for lbl, i in pos:
        stmts = proc.blocks[lbl].stmts
        if lbl in latches and i < len(stmts) and type(stmts[i]) is Let and type(stmts[i].e) is Var:
            return True
    return False


def _subber(sub):
    def fn(e):
        return sub.get(e.n, e) if type(e) is Var else e

    return fn


def _apply_sub(proc, sub):
    """Expand the chosen values into each other, then into every use."""
    for _ in range(len(sub)):
        nxt = {n: sub_expr(e, _subber(sub)) for n, e in sub.items()}
        if nxt == sub:
            break
        sub = nxt
    fn = _subber(sub)
    for b in proc.blocks.values():
        for s in b.stmts:
            _sub_stmt(s, fn)
        _sub_term(b.term, fn)
        b.stmts = [s for s in b.stmts if not (type(s) is Let and s.n in sub)]
    return proc


def _input(x):
    """A load whose value comes from the pinned input stream, not from a region."""
    return x.cls == "io" or (x.cls == "chk" and x.r < 0)


def _cfg(proc):
    g = nx.DiGraph()
    g.add_nodes_from(proc.blocks)
    for lbl, b in proc.blocks.items():
        g.add_edges_from((lbl, s) for s in succs(b.term))
    return g


def _natural_loops(g, idom, preds):
    """``{header: (body labels, latch labels)}`` from the back edges."""
    loops = {}
    for u, v in g.edges:
        d = u
        while d is not None and d != v and idom.get(d) != d:
            d = idom.get(d)
        if d != v:
            continue
        body, latches = loops.setdefault(v, (set([v]), set()))
        latches.add(u)
        stack = [u]
        while stack:
            x = stack.pop()
            if x in body:
                continue
            body.add(x)
            stack.extend(preds.get(x, ()))
    return loops


def _dead(proc, lbl):
    return type(proc.blocks[lbl].term) is Trap
