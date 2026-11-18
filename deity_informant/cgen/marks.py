"""Markers: what each printed construct of the ``.c`` comes from (*Translation cost*).

Each printed statement and check carries a marker record on its ``c_ast`` node
(:class:`cnodes.Prov`); the printer reads them back in text order, and as ``#line``
directives."""

from __future__ import annotations

from collections import defaultdict

#: ``#line`` files: an instruction's and the artifacts' (docs/cgen.md, *Translation cost*).
FILES = ("6502", "check", "trap", "access", "copy", "frame", "runtime")


class Marks:
    """The records markers name: ``("pc", fn, block, visit, ops)`` a statement printing ``ops``
    in visit ``visit`` of block ``block`` of MLIR function ``fn``, ``("check", kind, op, fn,
    trap)``, ``("arm", check)``, ``("trap", k, fn)`` and ``("tag", file)``."""

    def __init__(self):
        self.recs, self.visits = [], 0

    def add(self, rec):
        """The index of a new record ``rec``."""
        self.recs.append(rec)
        return len(self.recs) - 1

    def visit(self):
        """A new visit: one printing of a block's statements."""
        self.visits += 1
        return self.visits


def copies(recs, order, pcs_of):
    """``{pc: n}``: the times each original instruction is printed, summed over its lift nodes
    and inline call chains (:func:`_group`) of the most visits printing one of its operations
    in one block. ``pcs_of(fn, pc)``: each copy's instruction an operation stands for."""
    seen = defaultdict(set)
    for rec in (recs[j] for j in order):
        if rec[0] != "pc":
            continue
        _, fn, block, visit, ops = rec
        for o in (o for o in ops if o.pc):
            group = _group(o)
            for pc in pcs_of(fn, o.pc):
                seen[(pc, group, id(o), block)].add(visit)
    most = defaultdict(int)
    for (pc, group, _, _), visits in seen.items():
        most[(pc, group)] = max(most[(pc, group)], len(visits))
    located = {pc for pc, group in most if group[0] is not None}
    out = defaultdict(int)
    for (pc, group), n in most.items():
        if group[0] is not None or pc not in located:
            out[pc] += n
    return dict(sorted(out.items()))


def _group(o):
    """The lift node and call sites of operation ``o``'s location; one the printer or the
    optimizer made takes that of an operation of its instruction in its block, else none: an
    operation the optimizer merged out of an instruction's emissions, counted only where the
    instruction has no located one (:func:`copies`)."""
    loc = o.loc
    if not loc and o.parent is not None:
        loc = next((x.loc for x in o.parent.ops if x.loc and x.pc == o.pc), ())
    return (loc[0][1], loc[1:]) if loc else (None, o.pc)
