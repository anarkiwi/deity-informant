"""S6 -- the copy view: a per-copy column read as the operand it stands for.

A column that steps affinely becomes that step in ``v``, so the stride vocabulary
prints it; one that does not becomes copy 0's operand plus a group slot
:mod:`.views` names ``voice[v].field``. Anything else keeps its table read.
"""

from __future__ import annotations

import copy

from .facts import Facts
from .ir import Bin, COLVAR, COPYVAR, Const, Let, Load, Store, Var
from .irwalk import apply_stmt, apply_term, node_exprs, use_counts, walk
from .structure import For
from .structure import walk as nodewalk
from .views import indexed, step


class Col:
    """One per-copy column: what the copies name through it, and who reads it."""

    __slots__ = ("w", "k", "vals", "vars", "targets")

    def __init__(self, e, init, base):
        self.w = e.w
        self.k = (e.hi - e.lo + 1) // e.w
        off = e.lo - base
        self.vals = [
            int.from_bytes(init[off + j * e.w : off + (j + 1) * e.w], "little")
            for j in range(self.k)
        ]
        self.vars, self.targets = set(), set()

    @property
    def target(self):
        """The one region every access through the column names, or ``None``."""
        return next(iter(self.targets)) if len(self.targets) == 1 else None


def _key(e, tabs):
    """``(region, low address)`` of the column a load reads, or ``None``."""
    if type(e) is Load and e.r in tabs and e.w and not (e.hi - e.lo + 1) % e.w:
        return (e.r, e.lo)
    return None


def _index(a):
    """The copy index an address reads, or ``''``."""
    return next((x.n for x in walk(a) if type(x) is Var and x.n.startswith(COPYVAR)), "")


def _accesses(s):
    """``(address, region)`` of every memory access one statement makes."""
    if type(s) is Store:
        yield s.a, s.r
    for e in node_exprs(s):
        for x in walk(e):
            if type(x) is Load:
                yield x.a, x.r


def _reads(a, tabs, byvar):
    """The columns an address names itself; a load inside it is its own access."""
    k = _key(a, tabs) or (byvar[a.n][0] if type(a) is Var and a.n in byvar else None)
    if k is not None:
        return {k}
    if type(a) is Bin:
        return _reads(a.a, tabs, byvar) | _reads(a.b, tabs, byvar)
    return set()


def _nodes(view):
    """Every statement and terminator of the view."""
    return (
        s for p in view.procs.values() for b in p.blocks.values() for s in list(b.stmts) + [b.term]
    )


def _collect(view, tabs):
    """Every column, the name each hoisted read binds it to, and the accesses that use it."""
    cols, byvar = {}, {}
    for s in _nodes(view):
        for x in (x for e in node_exprs(s) for x in walk(e)):
            k = _key(x, tabs)
            if k is None:
                continue
            col = cols.setdefault(k, Col(x, tabs[k[0]].init, tabs[k[0]].base))
            col.vars.add(_index(x.a))
        if type(s) is Let and _key(s.e, tabs) is not None:
            byvar[s.n] = (_key(s.e, tabs), _index(s.e.a))
    for s in _nodes(view):
        for a, rid in _accesses(s):
            for k in () if rid in tabs else _reads(a, tabs, byvar):
                cols[k].targets.add(rid)
    return cols, byvar


def _plan(col, rgn):
    """``(substitution, per-copy cells)`` a column prints as; ``(None, None)`` keeps it.

    An affine column becomes an expression, which is exact; a column a group view
    names keeps its read, so the address the printer resolves is the column's own
    and never a constant some row of the body may also hold.
    """
    vals = col.vals
    if len(set(vals)) == 1:
        return ("const", vals[0], col.w), None
    tgt = col.target
    d = vals[1] - vals[0]
    affine = all(v == vals[0] + i * d for i, v in enumerate(vals))
    how = indexed(rgn, [tgt] * col.k, vals, col.k) if affine else "table"
    if how == "index":
        return ("index", d, vals[0], col.w), None
    r = rgn.get(tgt) if how == "table" and tgt is not None and tgt >= 0 else None
    if r is not None and _field(r, vals):
        return ("read",), tuple((tgt, v) for v in vals)
    return None, None


def _field(r, vals):
    """True when every copy's cell is the same offset of a record of ``r``.

    Cells at different offsets are different fields, whatever the copies do with
    them, so no one name describes the column.
    """
    return len({(v - r.zero) % max(r.stride, 1) for v in vals}) == 1


def _rewrite(view, tabs, byvar, subs, reads):
    """Substitute every planned column read, then drop the reads nothing needs.

    The index is the one that occurrence reads, so a procedure and its clone --
    which share one column table -- each keep their own.
    """

    def fn(e):
        k = _key(e, tabs)
        hit = byvar.get(e.n) if type(e) is Var else (k, _index(e.a)) if k else None
        plan = subs.get(hit[0]) if hit else None
        if plan is None:
            return e
        if plan[0] == "read":
            return reads[hit[0]] if type(e) is Var else e
        if plan[0] == "const":
            return Const(plan[1], plan[2])
        return step(hit[1], plan[1], plan[2], plan[3]) if hit[1] else e

    for p in view.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                apply_stmt(s, fn)
            apply_term(b.term, fn)
        used = use_counts(p)
        for b in p.blocks.values():
            b.stmts = [
                s
                for s in b.stmts
                if not (type(s) is Let and s.n.startswith(COLVAR) and not used[s.n])
            ]


def _split(subs, slots):
    """Drop the columns two different fields would share a cell name.

    Copy 0 agreeing does not make two columns one field; where their later copies
    differ, no cell names either, so both keep their read.
    """
    by = {}
    for cells in slots.values():
        by.setdefault(cells[0], set()).add(cells)
    for k, cells in list(slots.items()):
        if len(by[cells[0]]) > 1:
            del slots[k], subs[k]


def _hoisted(view, tabs):
    """``{column: the load a hoisted name binds it to}``, so a use inlines the read."""
    return {
        _key(s.e, tabs): s.e for s in _nodes(view) if type(s) is Let and _key(s.e, tabs) is not None
    }


def _folds(cols, slots, indexed_rgn):
    """One record per family, clones sharing one table sharing one group view."""
    fams = {}
    for k, col in cols.items():
        for name in (n.split("#")[0] for n in col.vars if n):
            f = fams.setdefault(name, {"n": col.k, "slots": {}, "views": set(), "columns": {}})
            f["views"] |= indexed_rgn.get(k, set())
            if k in slots:
                f["slots"][slots[k][0]] = list(slots[k])
                f["columns"][k] = slots[k][0]
    out, seen = [], {}
    for base in sorted(fams):
        f = fams[base]
        sig = (f["n"], tuple(sorted((c, tuple(f["slots"][c])) for c in f["slots"])))
        if sig[1] and sig in seen:
            seen[sig]["vars"] += (base,)
            seen[sig]["views"] |= f["views"]
            seen[sig]["columns"].update(f["columns"])
            continue
        rec = {
            "vars": (base,),
            "n": f["n"],
            "slots": f["slots"],
            "views": f["views"],
            "columns": f["columns"],
            "group": "voice" if f["n"] == 3 else "copy%d" % len(out),
        }
        out.append(rec)
        seen[sig] = rec
    return out


def expand(view):
    """Rewrite every namable column read into the operand it stands for.

    Records one fold per family in ``view.meta['copyviews']``: the per-copy cells
    :func:`~.views.copy_groups` names, over the index the ``for`` runs.
    """
    tabs = {r.id: r for r in view.storage if r.kind == "copymap"}
    if not tabs:
        return []
    cols, byvar = _collect(view, tabs)
    subs, slots, byrgn, rgn = {}, {}, {}, view.by_id()
    for k, col in cols.items():
        plan, cells = _plan(col, rgn)
        if plan is None:
            continue
        subs[k] = plan
        if cells is not None:
            slots[k] = cells
        elif plan[0] == "index" and col.target is not None and col.target >= 0:
            byrgn[k] = {col.target}
    _split(subs, slots)
    if not subs:
        return []
    _rewrite(view, tabs, byvar, subs, _hoisted(view, tabs))
    out = _folds(cols, slots, byrgn)
    view.meta["copyviews"] = out
    return out


def naming_facts(view):
    """The :class:`~.facts.Facts` the field names come from: every column substituted.

    A role and a SID shadow are properties of the address, which only the
    substituted form shows; the printed program keeps the column read so no
    constant of the merged body can be mistaken for one.
    """
    tabs = {r.id: r for r in view.storage if r.kind == "copymap"}
    if not tabs:
        return Facts(view)
    twin = copy.deepcopy(view)
    cols, byvar = _collect(twin, tabs)
    rgn, subs = twin.by_id(), {}
    for k, col in cols.items():
        plan, _cells = _plan(col, rgn)
        if plan is not None:
            subs[k] = ("const", col.vals[0], col.w) if plan[0] == "read" else plan
    _rewrite(twin, tabs, byvar, subs, {})
    return Facts(twin)


def mark(structured, groups):
    """Name each ``for`` over a family's copies with the group its columns made."""
    by = {v: f["group"] for f in groups if f["slots"] for v in f["vars"]}
    for body in structured.values():
        for n in nodewalk(body):
            if type(n) is For:
                n.group = by.get(n.var.split("#")[0], n.group)
    return structured
