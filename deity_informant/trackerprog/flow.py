"""B7 -- the flow facts the lowering reads off one procedure.

Three of them, and none knows anything of the object: which ram stores reach a
block, when two guard paths are one path with a term left out -- the fold a
join's reaching condition and a loop's own back edges are both answered by --
and the terms a jump table's own edges decide.
"""

from __future__ import annotations

from ..tuneprog.accguard import _domsets
from ..tuneprog.graph import EXIT, cfg, postdoms, succs
from ..tuneprog.ir import Bin, Const, Store, Switch, Var
from ..tuneprog.irwalk import addr_split


def switched(proc, guards):
    """``guards`` with the term each edge of a jump table decides.

    Control dependence over a ``Switch`` is one case: a block one case alone
    reaches stands under the term that case is, and one several reach under none.
    """
    g = cfg(proc)
    ipd = postdoms(g, proc, EXIT)
    pd = _domsets(ipd, [n for n in ipd if n in proc.blocks])
    out = {lbl: list(gs) for lbl, gs in guards.items()}
    for d, b in proc.blocks.items():
        if type(b.term) is not Switch:
            continue
        for s, c in _cases(b.term):
            term = (d, Bin("==", b.term.e, Const(c, 2), 1), True, ())
            for lbl in proc.blocks:
                if s in pd and lbl in pd[s] and lbl not in pd.get(d, ()):
                    out[lbl].append(term)
    return _closed(proc, out)


def _cases(t):
    """``(label, value)`` for each case of a table that reaches its label alone."""
    got = {}
    for c, s in t.cases:
        got.setdefault(s, []).append(c)
    return [(s, cs[0]) for s, cs in sorted(got.items()) if len(cs) == 1]


def edge(term, lbl):
    """The term the edge of a jump table to one label decides, where it decides one."""
    got = [c for s, c in _cases(term) if s == lbl]
    return (Bin("==", term.e, Const(got[0], 2), 1),) if got else ()


def _closed(proc, out):
    """A guard map closed under its own deciders' guards."""
    for _ in range(len(proc.blocks)):
        moved = False
        for lbl, gs in out.items():
            got = list(gs) + [x for d, _c, _v, _w in gs for x in out.get(d, ()) if x not in gs]
            moved = moved or len(got) != len(gs)
            out[lbl] = list(dict.fromkeys(got))
        if not moved:
            break
    return {lbl: tuple(gs) for lbl, gs in out.items()}


def fold(ctxs):
    """Two guards that differ in one term and its negation are the guard without it."""
    out = list(ctxs)
    for _ in range(len(out) * len(out) + 1):
        for i, (g, x) in enumerate(out):
            got = next(
                (j for j in range(i + 1, len(out)) if x == out[j][1] and pair(g, out[j][0])), None
            )
            if got is None:
                continue
            drop = set(g) - set(out[got][0])
            out = [y for k, y in enumerate(out) if k not in (i, got)]
            out.append((tuple(t for t in g if t not in drop), x))
            break
        else:
            return out
    return out


def pair(a, b):
    """Whether two guards differ in exactly one term, and it is the same condition."""
    x, y = set(a) - set(b), set(b) - set(a)
    if len(x) != 1 or len(y) != 1:
        return False
    (_d1, c1, t1), (_d2, c2, t2) = next(iter(x)), next(iter(y))
    return c1 is c2 and t1 != t2


def reaching(p, order, vidx=frozenset()):
    """``{label: {address: {expressions}}}``: the ram stores one base address has."""
    gen = {}
    for lbl, b in p.blocks.items():
        d = {}
        for s in b.stmts:
            if type(s) is Store and s.cls == "ram":
                base, idx = addr_split(s.a)
                if base is not None and (idx is None or (type(idx) is Var and idx.n in vidx)):
                    d[base] = {s.v}
        gen[lbl] = d
    inn = {lbl: {} for lbl in p.blocks}
    for _ in range(len(p.blocks) + 1):
        moved = False
        for lbl in order:
            out = {}
            for pr, b in p.blocks.items():
                if lbl not in succs(b.term):
                    continue
                d = dict(inn[pr])
                d.update(gen[pr])
                for a, vs in d.items():
                    out[a] = out.get(a, set()) | vs
            if out != inn[lbl]:
                inn[lbl], moved = out, True
        if not moved:
            break
    return inn
