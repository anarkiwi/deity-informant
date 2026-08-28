"""T2 -- what a table read is made of, and what its cursor did over the horizon.

An access is ``T[base + origin + (cursor << shift)]``: the cursor a state cell,
the base a constant, a 16-bit state pair, or a pointer table's entry at a
selector. The cursor's own history is its successor relation -- steps of one
size, jumps, holds -- which is all a stream or a score needs (prototype section
6, T2).
"""

from __future__ import annotations

from collections import Counter, namedtuple

import numpy as np

from ..tuneprog.accshape import terms
from ..tuneprog.facts import elem_count
from ..tuneprog.ir import Bin, Const, Load, R16, Var
from ..tuneprog.irwalk import addr_split, node_exprs, reachable, walk
from .resolve import Program, free, walkx

TABLE = ("const", "init_constant")
Access = namedtuple("Access", "site table base origin cursor shift guards expr copyvars")
Cursor = namedtuple("Cursor", "region addr index")  # index: the copy displacement expression


def _shifted(e):
    """``(inner, shift)`` for ``inner << k``, else ``(e, 0)``."""
    if type(e) is Bin and e.op == "<<" and type(e.b) is Const:
        return e.a, e.b.v
    return e, 0


def _cursor(e, rgn):
    """The state cell one term reads, or ``None``."""
    x, k = _shifted(e)
    if type(x) is Load and x.w == 1 and x.r in rgn and rgn[x.r].kind == "state":
        base, idx = addr_split(x.a)
        if base is not None:
            return Cursor(x.r, base, idx), k
    return None, 0


def decompose(addr, rgn):
    """``(base, origin, cursor, shift)`` of an opened address, or ``None``."""
    origin, cursor, shift, rest = 0, None, 0, []
    for sign, t in terms(addr):
        if type(t) is Const:
            origin += sign * t.v
            continue
        c, k = _cursor(t, rgn)
        if c is not None and sign > 0 and cursor is None:
            cursor, shift = c, k
        else:
            rest.append((sign, t))
    if any(s < 0 for s, _t in rest):
        return None
    base = None
    for _s, t in rest:
        base = t if base is None else Bin("+", base, t, 2)
    return base, origin, cursor, shift


def leaf_loads(e):
    """The reads of ``e`` that are values, not parts of another read's address."""
    xs = [x for x in walkx(e) if type(x) in (Load, R16)]
    inner = {id(y) for x in xs for y in walkx(x.a)}
    return [x for x in xs if id(x) not in inner]


def basekind(base, rgn, bound=frozenset()):
    """``const``, ``pair`` (state pointers only), ``ptrtab`` (a table entry among them), ``other``.

    A base is any expression over table entries and state pairs -- a pointer-table
    entry plus a relocation base, with its carry spelt out, is still a ``ptrtab``.
    """
    if base is None:
        return "const"
    if any(type(x) is Var and x.n not in bound for x in walkx(base, False)):
        return "other"
    return "ptrtab" if any(istable(x, rgn) for x in leaf_loads(base)) else "pair"


def istable(x, rgn):
    """True for a byte, word or pair read of a table region."""
    r = x.lo[0] if type(x) is R16 else x.r if type(x) is Load else None
    return r in rgn and rgn[r].kind in TABLE


def _halves(base):
    """``(lo, hi)`` of ``lo | (hi << 8)`` (either order), or ``None``."""
    if type(base) is not Bin or base.op != "|":
        return None
    for a, b in ((base.a, base.b), (base.b, base.a)):
        if type(b) is Bin and b.op == "<<" and type(b.b) is Const and b.b.v == 8:
            return (a.a if type(a) is Bin and a.op == "+" and type(a.b) is Const else a), b.a
    return None


def selector(base, rgn):
    """The index expression the base's first table entry is read at, or ``None``."""
    for x in leaf_loads(base):
        if istable(x, rgn) and addr_split(x.a)[1] is not None:
            return addr_split(x.a)[1]
    return None


def table_reads(prog, procs):
    """``(proc, label, index, load)`` for every distinct table read of ``procs``."""
    rgn = prog.by_id()
    seen = set()
    for pn in sorted(procs):
        for lbl, b in prog.procs[pn].blocks.items():
            for i, s in enumerate(list(b.stmts) + [b.term]):
                for x in (y for e in node_exprs(s) for y in walk(e)):
                    if type(x) is not Load or x.r not in rgn or rgn[x.r].kind not in TABLE:
                        continue
                    if (pn, lbl, repr(x)) not in seen:
                        seen.add((pn, lbl, repr(x)))
                        yield pn, lbl, i, x


def accesses(ctx, names, P=None):
    """Every table read the tick reaches, resolved and decomposed."""
    prog = ctx.prog
    rgn = prog.by_id()
    P = P or Program(ctx)
    tick = reachable(prog, prog.meta.get("tick_proc")) or set(prog.procs)
    out = []
    for pn, lbl, i, x in table_reads(prog, tick):
        for gs, a in P.resolve(pn, lbl, i, x):
            got = decompose(a.a, rgn)
            if got is not None:
                base, origin, cursor, shift = got
                out.append(
                    Access((pn, lbl, i), x.r, base, origin, cursor, shift, gs, a, free(a, False))
                )
    return out


def strides(prog, names):
    """``{copy index name: stride}`` for names indexing a voice-count region.

    A region of three elements, or a member of a group the view names with three
    copies (a split record's fields included), is one copy per voice.
    """
    out = {}
    rgn = prog.by_id()
    voiced = {}
    for g in (names.groups or {}).values():
        if int(g.get("n", 0)) == 3:
            for rid in list(g.get("members", ())) + (
                [g["split"]] if g.get("split") is not None else []
            ):
                voiced[rid] = max(int(g.get("stride", 1)), 1)
    loads = (
        x
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in list(b.stmts) + [b.term]
        for e in node_exprs(s)
        for x in walk(e)
        if type(x) is Load
    )
    for x in loads:
        r = rgn.get(x.r)
        if r is None or r.id < 0:
            continue
        k = voiced.get(r.id) or (r.stride if r.kind == "state" and elem_count(r) == 3 else None)
        if k is not None:
            for n in {y.n for y in walk(x.a) if type(y) is Var}:
                out[n] = max(out.get(n, 0), k, 1)
    return out


def copies(acc, stride, voices=3):
    """The ``env`` bindings one access runs under: one per copy index value."""
    names = sorted(acc.copyvars)
    if not names:
        return [{}]
    if any(n not in stride for n in names):
        return []
    return [{n: v * stride[n] for n in names} for v in range(voices)]


# ---- the successor relation ----------------------------------------------------
Edges = namedtuple("Edges", "step jumps holds visited")


def successors(h):
    """One cursor column's steps, jumps, holds and visited values.

    ``step`` is the one positive difference most transitions take (``None`` with
    none); ``jumps`` are the other transitions as ``Counter{(from, to)}``; ``holds``
    the run lengths between transitions; ``visited`` the sorted value set.
    """
    h = np.asarray(h, np.int64)
    if h.size == 0:
        return Edges(None, Counter(), [], [])
    moved = np.nonzero(h[1:] != h[:-1])[0] + 1
    d = h[moved] - h[moved - 1]
    pos = Counter(int(x) for x in d if x > 0)
    step = pos.most_common(1)[0][0] if pos else None
    jumps = Counter((int(h[t - 1]), int(h[t])) for t, x in zip(moved, d) if x != step)
    holds = np.diff(np.concatenate(([0], moved, [h.size]))).tolist()
    return Edges(step, jumps, holds, sorted(set(h.tolist())))
