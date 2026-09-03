"""B7 -- the flow facts the lowering reads off one procedure.

Two of them, and neither knows anything of the object: which ram stores reach a
block, and when two guard paths are one path with a term left out -- the fold a
join's reaching condition and a loop's own back edges are both answered by.
"""

from __future__ import annotations

from ..tuneprog.graph import succs
from ..tuneprog.ir import Store, Var
from ..tuneprog.irwalk import addr_split


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
