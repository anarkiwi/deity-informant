"""The construct expansions L5 selects against, over the region tree of :mod:`.rir`.

A construct of the schema is a shorthand for a statement list, and its expansion
is that list; :mod:`.accex` writes section 5's record out and :mod:`.accof` reads
one back.  A step that enters a run the event picks at run time has none.
"""

from __future__ import annotations

from . import accex, accof
from .rir import read

WIDTHS = (8, 11, 12, 16)
KINDS = ("acc", "prelude", "on_note", "row", "flush", "reset", "producer")
RUNTIME = "the run it enters is picked at run time by a cell, not by the step"


def mask(node, w):
    """A value held to the modulus its record declares."""
    return accex.mask(node, w)


def width_of(node):
    """The modulus a masked value states, where the mask is one of section 5's widths."""
    if not isinstance(node, dict) or "and" not in node:
        return None
    m = node["and"][1]
    return next((w for w in WIDTHS if isinstance(m, int) and m == (1 << w) - 1), None)


def ishi(v):
    """Whether one produce value is the high half of the cell it reads."""
    return (
        isinstance(v, dict)
        and "and" in v
        and isinstance(v["and"][0], dict)
        and "shr" in v["and"][0]
    )


def acc_why(a):
    """Why one section 5 record has no expansion, or ``None`` where it has one."""
    return accex.why(a)


def acc_rows(a):
    """The region tree section 4's accumulator procedure is, for one record."""
    return accex.rows(a)


def acc_of(rows):
    """The section 5 record a statement list is the expansion of, or ``None``."""
    return accof.record(rows)


def rowsof(obj, x):
    """A section 3.3 stream where the grammar puts one: a declared name, or the rows."""
    if x is None:
        return []
    return obj["streams"][x]["rows"] if isinstance(x, str) else list(x)


def instances(obj):
    """Every construct instance one object states, as ``(kind, key, spec)``."""
    out = [("acc", k, a) for k, a in obj.get("accs", {}).items()]
    for k, rec in obj.get("instruments", {}).items():
        for kind in ("prelude", "on_note"):
            got = rec.get(kind, obj["meta"].get("instrument", {}).get(kind))
            if got:
                out.append((kind, k, rowsof(obj, got)))
    out += [("row", str(i), _step(obj, s)) for i, s in enumerate(obj["meta"].get("row", ()))]
    out += [("flush", str(i), e) for i, e in enumerate(_flush(obj))]
    out += [("reset", str(i), c) for i, c in enumerate(obj["meta"]["tempo"].get("reset", ()))]
    out += [
        ("producer", "%s.%d" % (k, i), [a["cell"]] + list(p))
        for k, a in obj.get("accs", {}).items()
        for i, p in enumerate(a.get("produce", ()))
    ]
    return out


def _step(obj, s):
    """One row program step with the stream it names read out: the rows are the step's."""
    return {**s, "stream": rowsof(obj, s["stream"])} if "stream" in s else s


def _flush(obj):
    return ((obj["meta"].get("shadow") or {}).get("registers")) or ()


def row_rows(spec):
    """The statement list one step of the row program is, or ``None`` where it has none."""
    if "sets" in spec:
        return [dict(spec)]
    when = list(spec.get("when") or [])
    if "stream" in spec:
        return [{**({"when": when} if when else {}), "region": list(spec["stream"])}]
    if "ins" in spec:
        got = when + [[{"payload": "newins"}, "!=", 0]]
        return [{"when": got, "sets": [["@ins", {"payload": "ins"}]]}]
    return None


def row_of(stmts):
    """The row program step a statement list is the expansion of, or ``None``."""
    if len(stmts) != 1:
        return None
    s = stmts[0]
    when = list(s.get("when") or [])
    if "region" in s:
        return {**({"when": when} if when else {}), "stream": list(s["region"])}
    got = s.get("sets") or []
    if len(got) == 1 and got[0] == ["@ins", {"payload": "ins"}]:
        rest = [t for t in when if t != [{"payload": "newins"}, "!=", 0]]
        return {**({"when": rest} if rest else {}), "ins": True}
    return dict(s)


def row_why(spec):
    """Why one step of the row program has no expansion, or ``None`` where it has one."""
    if row_rows(spec) is not None:
        return None
    for k in ("note", "commands", "hold"):
        if k in spec:
            return "{%s}: %s" % (k, RUNTIME)
    return "no expansion"


def expand(kind, spec):
    """One construct as the statement list it is, which selection covers a run with."""
    if kind == "acc":
        return accex.rows(spec)
    if kind in ("prelude", "on_note"):
        return [dict(r) for r in spec]
    if kind == "row":
        return row_rows(spec)
    if kind == "reset":
        return [{"when": list(spec.get("when") or []), "sets": [list(s) for s in spec["sets"]]}]
    if kind == "flush":
        name = spec if isinstance(spec, str) else spec[0]
        when = [] if isinstance(spec, str) else list(spec[1])
        return [{"when": when, "sets": [[name, read("shadow." + name)]]}]
    if kind == "producer":
        cell, target, part = spec[0], spec[1], "hi" if spec[2] == "hi" else "lo"
        val = mask({"shr": [read(cell), 8]}, 8) if part == "hi" else mask(read(cell), 8)
        return [{"sets": [[target, val]]}]
    return None


def why(kind, spec):
    """Why one construct has no expansion, or ``None`` where it has one."""
    if kind == "acc":
        return accex.why(spec)
    if kind == "row":
        return row_why(spec)
    return None
