"""L6 -- canonical trackerprog: scalar optimisation over the selected object.

Four passes, each the classical one: adjacent streams merged, a cell whose reads
are an expression over state no row moves propagated and its write dead, a guard
term the clock's own invariants imply dropped, and a name made canonical -- the
slot the cell is, or the register its sole reader writes.  Nothing here moves a
value, so the level renders what the level before it rendered and the object it
leaves is no larger.
"""

from __future__ import annotations

import copy

from ..shape import _reads
from .ir import Level
from .l6_names import _named, sweep
from .l6_reads import _readers, elsewhere, named, order, rowat, written

PLAYER = ("phase", "counter", "voice_index", "tied", "note", "ins", "rowsleft", "dur", "orderpos")
# the names §5's own vocabulary answers itself: a cell may not take one of them
RESERVED = ("pw", "pw_lo", "pw_hi", "freq", "freq_lo", "freq_hi", "wave", "lastnote")


def merge(obj):
    """Adjacent ``{stream}`` phases with no phase between them are one stream.

    A stream anything else of the object names keeps its rows and their numbers:
    a cursor is on a row, and a re-point states one.
    """
    keep, out = named(obj), []
    for e in obj["meta"]["tick"]:
        got = out[-1] if out and not isinstance(e, str) and not isinstance(out[-1], str) else None
        if got is not None and _free(obj, keep, got["stream"]) and _free(obj, keep, e["stream"]):
            obj["streams"][got["stream"]]["rows"] += obj["streams"].pop(e["stream"])["rows"]
            continue
        out.append(e)
    obj["meta"]["tick"] = out
    return obj


def _free(obj, keep, name):
    """Whether one stream's rows keep no numbers anything else of the object states."""
    return name not in keep and obj["streams"][name].get("all")


def substitute(node, name, val):
    """One expression with every read of ``name`` replaced by the value it holds."""
    if isinstance(node, dict):
        if node.get("cell") == name or node.get("global") == name.lstrip("#"):
            return copy.deepcopy(val)
        return {k: substitute(v, name, val) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(x, name, val) for x in node]
    return node


def propagate(obj):
    """A cell whose one unguarded write is an expression over state no row moves.

    Copy propagation: the reads that stand after that write are the expression
    itself, and the write is then dead -- which is what takes the cells
    if-conversion raised out of the object again.
    """
    moved, out = set(), elsewhere(obj)
    for name, at in sorted(written(obj).items()):
        if len(at) != 1 or name in PLAYER or name in out:
            continue
        seq = order(obj)
        row = rowat(obj, seq[at[0]])
        sets = row.get("sets") or []
        if row.get("when") or len(sets) != 1 or sets[0][0].lstrip("@#!*") != name:
            continue
        val = sets[0][1]
        if _reads(val) & set(written(obj)):
            continue
        if any(k <= at[0] for k in _readers(obj, seq, name)):
            continue
        for k in _readers(obj, seq, name):
            r = rowat(obj, seq[k])
            r["when"] = substitute(r.get("when", []), name, val)
            r["sets"] = [[t, substitute(v, name, val)] for t, v in r.get("sets", ())]
        row["sets"] = []
        moved.add(name)
    return moved


def implied(obj):
    """Guard terms the clock's own invariants make true, dropped where they stand.

    The row phase runs where ``meta.tempo.boundary`` holds, so a term the
    boundary already states is no term of a row of that phase; and a term over
    two constants is worth what it evaluates to.
    """
    gs = [t for t in obj["meta"]["tempo"].get("boundary", ()) if isinstance(t, list)]
    at = {n for s in obj["meta"].get("row", ()) for n in ([s["stream"]] if "stream" in s else [])}
    n = 0
    for name, st in obj["streams"].items():
        for r in st.get("rows", ()) or ():
            if not isinstance(r, dict):
                continue
            when, out, dead = r.get("when") or [], [], False
            for t in when:
                got = _fold(t)
                if got is False:  # a term the object states false: the row is dead
                    n, dead = n + 1, True
                    continue
                if t in gs and name in at:
                    n += 1
                    continue
                if got is not True and got not in out:
                    out.append(got)
                elif got is True:
                    n += 1
            if dead:
                r["when"], r["sets"] = when, []
            elif out != when:
                r["when"] = out
    return n


def _fold(t):
    """A guard term over two constants, worth what it is; else the term."""
    if not isinstance(t, list) or len(t) != 3 or not isinstance(t[0], int):
        return t
    a, op, b = t
    if not isinstance(b, int):
        return t
    return {"==": a == b, "!=": a != b, "<": a < b, ">=": a >= b}.get(op, t)


def canonicalise(l5, do=("merge", "propagate", "implied", "names")):
    """L5 to L6: the object with its streams merged, its cells spent and named."""
    obj = copy.deepcopy(l5.obj)
    facts = {"merged": 0, "propagated": (), "dropped": 0, "renamed": {}}
    before = len(obj["streams"])
    if "merge" in do:
        merge(obj)
        facts["merged"] = before - len(obj["streams"])
    if "propagate" in do:
        facts["propagated"] = tuple(sorted(propagate(obj)))
    if "implied" in do:
        facts["dropped"] = implied(obj)
    if "names" in do:
        obj, facts["renamed"] = _named(obj)
    sweep(obj)
    for name in facts["propagated"]:
        obj["state0"].get("cells", {}).pop(name, None)
        obj["state0"].get("globals", {}).pop(name.lstrip("#"), None)
    return Level(6, art=l5.art, prog=l5.prog, proc=l5.proc, obj=obj, facts={**l5.facts, **facts})
