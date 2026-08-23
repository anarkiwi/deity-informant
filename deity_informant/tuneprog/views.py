"""S6 -- group views: struct fields that are a per-copy address table.

A stride names a field when the copies are equally spaced. When they are not
(Follin's ``$62EE``/``$64DB``/``$66CA``), the copy correspondence names it
instead: one field, one address per copy, listed once in the state header.
"""

from __future__ import annotations

from collections import namedtuple

from .facts import Facts, MAXOPS, MAXROLE, SID_VOICE, elem_count as elems, leaf_loads, ops
from .facts import per_region, sid_name, sid_stores, unclaimed, update_role
from .ir import Bin, Const, SID_REG_HI, SID_REG_LO, Var
from .irwalk import accessors, unique_name

BLOCK = ("state", "init_constant")  # the kinds of storage a record view may split


def indexed(rgn, rids, vals, k):
    """How differing addresses print: as an ``index``, as a ``table``, or ``no``.

    One index serves one region a stride view already walks; addresses in
    different regions need the per-copy table; the SID's own register file is
    indexed by voice and by nothing else.
    """
    if rids is None:
        return "index"
    d = vals[1] - vals[0]
    if all(SID_REG_LO <= v <= SID_REG_HI for v in vals):
        return "index" if not d % SID_VOICE else "no"
    if len(set(rids)) > 1:
        return "table"
    r = rgn.get(rids[0])
    if r is None or r.kind == "io":
        return "index"
    return "index" if not d % max(r.stride, 1) and k <= elems(r) <= MAXROLE else "table"


def step(var, d, v, w):
    """``v`` plus ``d`` times the copy index, as an expression."""
    out = Var(var) if abs(d) == 1 else Bin("*", Var(var), Const(abs(d), w), w)
    if not v:
        return out if d > 0 else Bin("-", Const(0, w), out, w)
    return Bin("+" if d > 0 else "-", Const(v, w), out, w)


def sid_fields(facts):
    """``{cell address: the SID field it feeds}`` -- the shadow of one register."""
    out = {}
    for addr, val in sid_stores(facts):
        if ops(val) > MAXOPS:
            continue
        leaves = leaf_loads(val)
        if leaves and type(leaves[0].a) is Const and leaves[0].r in facts.rgn:
            if facts.rgn[leaves[0].r].kind == "state":
                out.setdefault(leaves[0].a.v, sid_name(addr)[0])
    return out


def cell_field(prog, facts, names, cell, sidf):
    """The field name one ``(region, address)`` slot gets: role, register, or address."""
    rid, addr = cell
    r = facts.rgn.get(rid)
    if r is not None and rid in names.region and (r.size <= 2 or addr == r.base):
        return names.region[rid]
    if addr is None:
        return names.of(rid)
    if addr in sidf:
        return sidf[addr]
    role = update_role(facts.cellupd.get(cell, ()), cell in facts.cellplain, rid)
    if role:
        return role
    if cell in facts.cellindex:
        return "cursor"
    return "b%04X" % addr


def copy_groups(prog, names, folds=None, facts=None):
    """Name a fold's slots: ``voice[v].field`` over a per-copy address table.

    The slots come from :mod:`.copyview` and :mod:`.unroll`, which proved the
    copies one program modulo this table; here they only get names.
    """
    folds = list(prog.meta.get("copyviews") or ()) + list(folds or ())
    if not folds:
        return names
    facts = facts or Facts(prog)
    sidf = sid_fields(facts)
    for f in folds:
        if not f["slots"] or f.get("named"):
            continue
        f["named"] = True
        held = _same_view(names.groups.get(f["group"]), f)
        g = f["group"] if held else unique_name(f["group"], set(names.groups))
        f["group"] = g
        if f.get("node") is not None:
            f["node"].group = g
        cells, named = {}, {}
        # a run one relocation apart proves its mapping inside the loop it folded
        # and nowhere else; a static template's copies are that everywhere
        local = f.get("node") is not None
        for cell in sorted(f["slots"], key=lambda c: (c[1] is None, c[1], c[0])):
            want = cell_field(prog, facts, names, cell, sidf)
            name = unique_name(want, set(cells))
            cells[name] = list(f["slots"][cell])
            named[cell] = name
            for j, other in enumerate(f["slots"][cell]):
                names.slots.setdefault(tuple(other), []).append((g, name, j, local))
        for key, cell in (f.get("columns") or {}).items():
            if cell in named:
                names.column[key] = (g, named[cell], cell[0])
        names.groups[g] = {
            "stride": held["stride"] if held else 0,
            "n": f["n"],
            "members": held["members"] if held else [],
            "cells": cells,
        }
    return names


def _same_view(held, f):
    """A stride view whose every element this family's index selects: one view, not two.

    Equal strides and the copy map are two proofs of the same struct, so the
    fold's cells and the view's fields join under one name.
    """
    members = held.get("members") if held else None
    if not members or held["n"] != f["n"] or held.get("cells"):
        return None
    return held if set(members) <= set(f.get("views") or ()) else None


def decorate(prog, names, folds=None, facts=None):
    """Every group view the S6 passes add over the recovered names."""
    facts = facts or Facts(prog)
    copy_groups(prog, names, folds, facts)
    shape = shapes(prog, prog.by_id(), facts.tick)
    field_split(prog, names, facts, shape)
    return transpose_split(prog, names, facts, shape)


Shape = namedtuple("Shape", "offsets spans play")  # what a region's accessors say of it


def shapes(prog, rgn, tick):
    """``{region: Shape}``: the offsets its accessors name and the spans they reach.

    Both counted from the region's zero, ``play`` being the tick's own spans. A
    16-bit access carries region ids and no envelope, and one that offers none
    never prints as a field (:meth:`~.pseudocode.Printer.one_field`).
    """
    out = {}
    for acc in accessors(prog):
        r = rgn.get(acc.rid)
        if r is None:
            continue
        sh = out.setdefault(acc.rid, Shape(set(), set(), set()))
        named = None if acc.base is None else r.extent(acc.base, acc.base)
        if named is not None:
            sh.offsets.add(named[0])
        span = r.extent(acc.lo, acc.hi)
        if span is not None:
            sh.spans.add(reach := (span[0], span[1] - span[0] + 1))
            if acc.proc in tick:
                sh.play.add(reach)
    return out


def _record(names, r, unit, n, fields, transposed):
    """Register one split view of ``r``: its group, its ``n`` elements, its fields."""
    g = unique_name("voice" if n == 3 else "rec", set(names.groups))
    names.split[r.id] = (g, unit, fields, transposed)
    names.groups[g] = {
        "stride": 1 if transposed else unit,
        "n": n,
        "members": [],
        "split": r.id,
        "fields": fields,
    }


def _named_fields(facts, r, sidf, by):
    """Name each field of a split from the role its own offsets share."""
    out = {}
    for k in sorted(by):
        want = _field_role(facts, r, by[k]) or sidf.get(r.zero + k, "")
        out[k] = unique_name(want or "f%02X" % k, set(out.values()))
    return out


def field_split(prog, names, facts, shape):
    """Split a block one init loop made one region into the fields play walks.

    ``init`` clears GoatTracker's blocks A+B and SID Wizard's VARIABLES with one
    loop, so the access relation joins them; the tick walks them at the stride of
    an index that reaches a record elsewhere, which is what names their fields.
    """
    sidf, taken = sid_fields(facts), _taken(names)
    scale = per_region(facts, names.scale)
    for r in prog.storage:
        s = scale.get(r.id, set())
        if len(s) != 1 or not _splittable(r, taken):
            continue
        s = s.pop()
        n = r.size // s
        if n < 2:
            continue
        by = {}
        for o in shape[r.id].offsets if r.id in shape else ():
            by.setdefault(o % s, []).append(o)
        _record(names, r, s, n, _named_fields(facts, r, sidf, by), False)
    return names


def _elem_index(names, facts):
    """``{index name: k}`` for an index that selects one of a stride-1 view's k elements.

    A struct-of-arrays field is k bytes at stride 1, so such an index carries no
    scale (:func:`~.facts.scales` needs a record wider than a byte); what it
    carries instead is the element count of the view it walks.
    """
    out = {}
    for n, rids in facts.idxvar.items():
        if (names.scale.get(n) or 1) > 1:
            continue
        ks = set()
        for rid in rids:
            d = names.groups.get((names.view.get(rid) or ("",))[0])
            if d and d["stride"] == 1 and d["n"] > 1:
                ks.add(d["n"])
        if len(ks) == 1:
            out[n] = ks.pop()
    return out


def transpose_split(prog, names, facts, shape):
    """Split a block walked ``base + n*k + v``: the field outside, the element inside.

    The transpose of :func:`field_split` -- the same play-time accessor rule with
    the two indices swapped, so the walking index has no scale and the *field* has
    the stride (JCH V20's state block is struct-of-arrays, anatomy 3.5).
    """
    sidf, taken = sid_fields(facts), _taken(names)
    want = per_region(facts, _elem_index(names, facts))
    for r in prog.storage:
        k = want.get(r.id, set())
        if len(k) != 1 or not _splittable(r, taken):
            continue
        k = k.pop()
        sh = shape.get(r.id)
        if r.size % k or r.size // k < 2 or sh is None or not _transposed(sh.play, k):
            continue
        # the play phase decides the layout; a field is listed wherever any access stays inside it
        by = {o - o % k: [o - o % k] for o, w in sh.spans if o // k == (o + w - 1) // k}
        _record(names, r, k, k, _named_fields(facts, r, sidf, by), True)
    return names


def _transposed(spans, k):
    """True when every play-time access stays inside one k-wide field.

    An index observed over some of the elements reaches fewer than k bytes and is
    still that field; one that crosses a field boundary, or walks the whole block,
    is not this layout.
    """
    return any(w > 1 for _o, w in spans) and all(o // k == (o + w - 1) // k for o, w in spans)


def _field_role(facts, r, offsets):
    """The role every cell of one field shares."""
    upd, plain = set(), False
    for o in offsets:
        upd |= facts.cellupd.get((r.id, r.zero + o), set())
        plain = plain or (r.id, r.zero + o) in facts.cellplain
    return update_role(upd, plain, r.id)


def _taken(names):
    """The regions some view already names, so no split may name them again."""
    return names.view.keys() | names.image.keys() | names.split.keys()


def _splittable(r, taken):
    """True when a region is a block no view has already named."""
    return unclaimed(r, taken, BLOCK) and elems(r) > MAXROLE
