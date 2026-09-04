"""The construct expansions L5 selects against, and the hermetic snippet that checks them.

A construct of the schema is a shorthand for a run of predicated statements, and
its **expansion** is that run written out as §3.3 rows.  Selection is the inverse:
a run of rows an expansion covers is the construct it expands to.  Three of the
schema's forms have no row expansion at all, and each is named where it is met --
the row language has no loop, so ``repeat``'s ``n`` additions are not rows; a
``clamp`` takes a pitch, which is one named operation and no assignment (§3.1);
and a ``reflect`` turns a direction cell on a bound the rows cannot ask about
without reading the value the same row writes.
"""

from __future__ import annotations

WIDTHS = (8, 11, 12, 16)
KINDS = ("acc", "prelude", "on_note", "row", "flush", "reset", "producer")


def mask(node, w):
    """A value held to the modulus its record declares."""
    return {"and": [node, (1 << w) - 1]}


def read(cell):
    """One §5 cell in a value position."""
    return {"global": cell[1:]} if cell.startswith("#") else {"cell": cell}


def put(cell):
    """One §5 cell as a ``sets`` target."""
    return cell if cell.startswith("#") else "@" + cell


def width_of(node):
    """The modulus a masked value states, where the mask is one of §5's widths."""
    if not isinstance(node, dict) or "and" not in node:
        return None
    m = node["and"][1]
    return next((w for w in WIDTHS if isinstance(m, int) and m == (1 << w) - 1), None)


def acc_why(a):
    """Why one §5 record has no expansion into rows, or ``None`` where it has one."""
    d, pol = a.get("delta"), a["policy"]
    if isinstance(d, dict) and "repeat" in d:
        return "repeat: the row language has no loop"
    if isinstance(pol, dict) and "clamp" in pol:
        return "clamp: taking a pitch is no assignment"
    if pol in ("reflect", "reflect-complement"):
        return "%s: the turn reads the value the row writes" % pol
    if a.get("rate", 1) != 1:
        return "rate: a divider is a counter of its own"
    for key in ("beyond", "trap", "amplitude", "flag"):
        if a.get(key):
            return "%s: no row states it" % key
    if a["cell"].split(".")[0] == "tick":
        return "tick: the per-tick scratch is no declared cell"
    if a.get("gate") and a.get("step_when"):
        return "gate under a step_when: a row cannot state the negation"
    return None


def acc_rows(a):  # noqa: C901 - one clause per channel of the record
    """The rows §4's accumulator procedure is, for a record that has an expansion."""
    if acc_why(a) is not None:
        return None
    cell, w = a["cell"], a.get("width", 8)
    base, pol = read(cell), a["policy"]
    when = list(a.get("when") or [])
    out = []
    if isinstance(pol, dict) and "reload" in pol:
        out.append(
            {"when": when + list(pol.get("when") or []), "sets": [[put(cell), pol["reload"]]]}
        )
    prod = [
        [t, mask({"shr": [base, 8]}, 8) if part == "hi" else mask(base, 8)]
        for t, part in a.get("produce", ())
    ]
    if a.get("emit") == "entry" and prod:
        out.append({"when": when, "sets": prod})
    if a.get("delta") is not None:
        gs = when + list(a.get("step_when") or []) + list(a.get("delta_when") or [])
        ph = a.get("phase")
        arms = [("add", [])] if ph is None else [("sub", [[ph, "!=", 0]]), ("add", [[ph, "==", 0]])]
        for op, extra in arms:
            out.append(
                {"when": gs + extra, "sets": [[put(cell), mask({op: [base, a["delta"]]}, w)]]}
            )
    if a.get("emit") != "entry" and prod:
        out.append({"when": when, "sets": prod})
    got = (a.get("gate") or {}).get("true") or ()
    if got:
        out.append({"when": when, "sets": [list(x) for x in got]})
    return out


def rowsof(obj, x):
    """A §3.3 stream where the grammar puts one: a declared name, or the rows."""
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
    out += [("row", str(i), s) for i, s in enumerate(obj["meta"].get("row", ()))]
    out += [("flush", str(i), e) for i, e in enumerate(_flush(obj))]
    out += [("reset", str(i), c) for i, c in enumerate(obj["meta"]["tempo"].get("reset", ()))]
    out += [
        ("producer", "%s.%d" % (k, i), [a["cell"]] + list(p))
        for k, a in obj.get("accs", {}).items()
        for i, p in enumerate(a.get("produce", ()))
    ]
    return out


def _flush(obj):
    return ((obj["meta"].get("shadow") or {}).get("registers")) or ()


def expand(kind, spec):
    """One construct as the rows it is: the expansion selection covers a run with."""
    if kind == "acc":
        return acc_rows(spec)
    if kind in ("prelude", "on_note"):
        return [dict(r) for r in spec]
    if kind == "row":
        if "sets" in spec:
            return [dict(spec)]
        if "stream" in spec:
            return None  # the rows are the stream's own; the step names it
        return None
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


def row_why(spec):
    """Why one step of the row program has no expansion into rows, or ``None``."""
    if "sets" in spec:
        return None
    for k in ("note", "ins", "commands", "hold"):
        if k in spec:
            return "{%s}: the row language has no channel for the event" % k
    if "stream" in spec:
        return "{stream}: the rows are the stream's own and the step names it"
    return "no expansion"


def why(kind, spec):
    """Why one construct has no expansion into rows, or ``None`` where it has one."""
    if kind == "acc":
        return acc_why(spec)
    if kind == "row":
        return row_why(spec)
    return None
