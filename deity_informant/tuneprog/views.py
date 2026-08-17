"""S6 -- group views: struct fields that are a per-copy address table.

A stride names a field when the copies are equally spaced. When they are not
(Follin's ``$62EE``/``$64DB``/``$66CA``), the copy correspondence names it
instead: one field, one address per copy, listed once in the state header.
"""

from __future__ import annotations

from .ir import Const
from .irwalk import unique_name
from .facts import Facts, MAXOPS, leaf_loads, ops, sid_name, update_role


def sid_fields(facts):
    """``{cell address: the SID field it feeds}`` -- the shadow of one register."""
    out = {}
    for hit in list(facts.sid) + [(b, v) for b, _i, v in facts.copies]:
        addr, val = hit
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

    The slots come from :mod:`.copyfold` and :mod:`.unroll`, which proved the
    copies one program modulo this table; here they only get names.
    """
    folds = list((prog.meta.get("folds") or {}).values()) + list(folds or ())
    if not folds:
        return names
    facts = facts or Facts(prog)
    sidf = sid_fields(facts)
    for f in folds:
        if not f["slots"] or f.get("named"):
            continue
        f["named"] = True
        g = unique_name(f["group"], set(names.groups))
        f["group"] = g
        if f.get("node") is not None:
            f["node"].group = g
        cells = {}
        for cell in sorted(f["slots"], key=lambda c: (c[1] is None, c[1], c[0])):
            want = cell_field(prog, facts, names, cell, sidf)
            name = unique_name(want, set(cells))
            cells[name] = list(f["slots"][cell])
            for j, other in enumerate(f["slots"][cell]):
                names.slots[tuple(other)] = (g, name, j)
        names.groups[g] = {"stride": 0, "n": f["n"], "members": [], "cells": cells}
    return names


def decorate(prog, names, folds=None):
    """Every group view the S6 passes add over the recovered names."""
    return copy_groups(prog, names, folds, Facts(prog))
