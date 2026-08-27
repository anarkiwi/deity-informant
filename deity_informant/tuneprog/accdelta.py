"""T1 -- the section 5 delta grammar, and how a cell spells in a record.

One additive term of a recurrence as ``const``, ``field``, ``tabcell`` or
``tablestep``; a cell as the JSON-plain reference the ``Acc`` schema carries.
"""

from __future__ import annotations

from .accshape import cellof, maskof, reads, selfread, shift_loop
from .facts import elem_count
from .ir import Bin, Const, Load, MASK, R16
from .irwalk import addr_split, walk


# ---- the section 5 delta grammar ----------------------------------------------
def _cellref(ctx, cells, e, elem=0):
    """A JSON-plain reference to the cell a value reads, or ``None``."""
    if type(e) is R16:
        lo = _cellref(ctx, cells, Load("ram", e.a, 1, 0, 0xFFFF, e.lo[0]))
        return None if lo is None else dict(lo, name=ctx.names.of(e.lo[0]), width=2)
    rid = e.r if type(e) is Load else None
    r = ctx.rgn.get(rid) if rid is not None else None
    key = cellof(e) or ((rid, r.base) if r is not None else None)
    if key is None:
        return None
    rid, addr = key
    return {
        "region": rid,
        "name": cellname(ctx, cells, rid),
        "addr": "$%04X" % addr,
        "role": ctx.names.role.get(rid, ""),
        "offset": None if r is None else addr - r.base,
        "width": 1,
    }


def cellname(ctx, cells, rid):
    """How a cell spells: its group's field where a view names one, else its own."""
    g = cells.group.get(rid)
    return "%s[].%s" % (g[0], ctx.names.of(rid)) if g else ctx.names.of(rid)


def delta_of(ctx, cells, e, sources):
    """One additive term as a section 5 ``delta``, or ``None``.

    ``sources`` maps a cell whose own recurrence is a right shift of a table
    difference to that difference, which is what makes a term a ``tablestep``.
    """
    v, mask = maskof(e)
    key = (tuple(v.lo), tuple(v.hi)) if type(v) is R16 else cellof(v)
    if type(e) is Const:
        return {"kind": "const", "value": e.v}
    if key in sources:
        return dict(sources[key], kind="tablestep", cell=_cellref(ctx, cells, v))
    if type(v) is R16 and tuple(v.lo) in sources:
        return dict(sources[tuple(v.lo)], kind="tablestep", cell=_cellref(ctx, cells, v))
    ref = _cellref(ctx, cells, v)
    if ref is None:
        return None
    if _table(ctx, v):
        return {"kind": "tabcell", "cell": ref, "index": _indexref(ctx, cells, v), "signed": None}
    wide = MASK[2] if type(v) is R16 else MASK[1]
    return {"kind": "field", "cell": ref, "mask": wide if mask is None else mask}


def _table(ctx, e):
    """True when a value reads a region an index walks rather than one cell."""
    if type(e) is not Load:
        return False
    r = ctx.rgn.get(e.r)
    return r is not None and addr_split(e.a)[1] is not None and elem_count(r) > 1


def _indexref(ctx, cells, e):
    """The cell a table read takes its index from, where one does."""
    idx = addr_split(e.a)[1]
    for x in () if idx is None else walk(idx):
        got = _cellref(ctx, cells, x) if type(x) is Load else None
        if got is not None:
            return got
    return None


def tablestep_sources(ctx, cells, byname):
    """``{cell: the table difference its own recurrence shifts down}``.

    A cell a loop halves, initialised to the difference of two adjacent entries of
    one table, is ``(P[n+1] - P[n]) >> shift``: the interpolated step three
    families spell the same way (GoatTracker 2 ``p_12E5``, Hubbard ``$51E4``,
    JCH's ``acc_5``).
    """
    out = {}
    for tgt, cs in sorted(byname.items()):
        diff = next((c for c in cs if c.kind == "action" and _difference(c.value)), None)
        shifts = [c for c in cs if c.kind == "opaque" and _halved(c.value, selfread(tgt))]
        if diff is None or not shifts:
            continue
        a, b = _difference(diff.value)
        got = {
            "table": ctx.names.of(a.r),
            "index": _indexref(ctx, cells, a),
            "shift": _shiftref(ctx, cells, shifts[0]),
            "span": abs(addr_split(a.a)[0] - addr_split(b.a)[0]) or 1,
        }
        for k in ((tgt.cells, tgt.cells[0]) if tgt.kind == "pair" else (tgt.cells[0],)):
            out[k] = got
    return out


def _difference(e):
    """``(high entry, low entry)`` when a value subtracts two reads of one region."""
    xs = reads(e)
    if type(e) is not Bin or e.op != "-" or len(xs) < 2:
        return None
    a, b = xs[0], xs[1]
    if type(a) is not Load or type(b) is not Load or a.r != b.r:
        return None
    return (a, b) if addr_split(a.a)[0] is not None and addr_split(b.a)[0] is not None else None


def _halved(e, base):
    """True when a value is its own cell shifted one bit right."""
    for x in walk(e):
        if type(x) is Bin and x.op == ">>" and base(x.a):
            return True
    return False


def _shiftref(ctx, cells, clause):
    """The cell a shift loop counts down, spelled as a name."""
    got = shift_loop(ctx, clause.proc, clause.block)
    ref = None if got is None else _cellref(ctx, cells, got)
    return ref and ref["name"]
