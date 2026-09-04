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

import json

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


def _isprod(r):
    """A row whose every target is a register the chip has: the record's produce."""
    sets = r.get("sets") or []
    return bool(sets) and all(not s[0].startswith(("@", "#", "!", "*")) for s in sets)


def _isdelta(r):
    """A row that moves one cell by a delta: the record's own step."""
    sets = r.get("sets") or []
    if len(sets) != 1 or not sets[0][0].startswith(("@", "#")):
        return False
    v = sets[0][1]
    if not isinstance(v, dict) or "and" not in v or width_of(v) is None:
        return False
    inner = v["and"][0]
    if not isinstance(inner, dict) or next(iter(inner), "") not in ("add", "sub"):
        return False
    arg = inner[next(iter(inner))][0]
    return isinstance(arg, dict) and arg in (read(_cellof(sets[0][0])),)


def _cellof(target):
    return target[1:] if target[:1] == "@" else target


def _ishi(v):
    return (
        isinstance(v, dict)
        and "and" in v
        and isinstance(v["and"][0], dict)
        and "shr" in v["and"][0]
    )


def acc_of(rows):  # noqa: C901 - one clause per channel of the record
    """The §5 record a run of rows is the expansion of, or ``None``."""
    if rows is None:
        return None
    steps = [r for r in rows if _isdelta(r)]
    prods = [r for r in rows if _isprod(r)]
    rest = [r for r in rows if r not in steps and r not in prods]
    when = _common(rows)
    if steps or prods:
        at = min(rows.index(r) for r in steps + prods)
        reload_ = [r for r in rest if rows.index(r) < at]
        gate = [r for r in rest if rows.index(r) > at]
    else:
        # a record that neither moves a cell nor produces writes through its
        # reload or through its gate, and the rows are the same either way: the
        # reload is one assignment, the gate is the arm's own list
        one = bool(rest) and len(rest[0].get("sets") or []) == 1
        reload_, gate = (rest[:1], rest[1:]) if one else ([], rest)
    cell = _sourceof(steps, reload_, prods, gate)
    if cell is None:
        return None
    a = {"cell": cell, "policy": "wrap", "produce": []}
    if reload_:
        extra = [t for t in (reload_[0].get("when") or []) if t not in when]
        a["policy"] = {"reload": reload_[0]["sets"][0][1], **({"when": extra} if extra else {})}
    if steps:
        v = steps[0]["sets"][0][1]
        op = next(iter(v["and"][0]))
        a["width"] = width_of(v)
        a["delta"] = v["and"][0][op][1]
        w0 = list(steps[0].get("when") or [])
        w1 = list(steps[-1].get("when") or [])
        ph = None
        if len(steps) > 1:
            diff = [t for t in w0 if t not in w1]
            ph = diff[0][0] if len(diff) == 1 else None
            if ph is not None:
                a["phase"] = ph
        gs = [t for t in w1 if t not in when and (ph is None or t[0] != ph)]
        if gs:
            a["delta_when"] = gs
    if prods:
        a["produce"] = [[t, "hi" if _ishi(v) else "lo"] for t, v in prods[0]["sets"]]
        if steps and rows.index(prods[0]) < rows.index(steps[0]):
            a["emit"] = "entry"
    if gate:
        a["gate"] = {"true": [list(s) for s in gate[0]["sets"]]}
    if when:
        a["when"] = when
    return a


def _sourceof(steps, reload_, prods, gate=()):
    """The cell a run of rows moves: the step's target, the reload's, or the produce's.

    A record that only writes through its gate moves no cell of its own, and the
    name it declares is not in the rows: the gate's own target stands for it.
    """
    for got in (steps, reload_):
        if got:
            return _cellof(got[0]["sets"][0][0])
    if not prods:
        return _cellof(gate[0]["sets"][0][0]) if gate else None
    v = prods[0]["sets"][0][1]
    inner = v["and"][0] if isinstance(v, dict) and "and" in v else v
    inner = inner["shr"][0] if isinstance(inner, dict) and "shr" in inner else inner
    if not isinstance(inner, dict):
        return None
    return inner.get("cell") or ("#" + inner["global"] if "global" in inner else None)


def _reads(node, cell):
    """Whether one value reads the cell it is being stored into."""
    if isinstance(node, dict):
        return node in (read(cell),) or any(_reads(v, cell) for v in node.values())
    if isinstance(node, list):
        return any(_reads(x, cell) for x in node)
    return False


def _common(rows):
    """The guard every row of a run carries: the record's own ``when``."""
    return [t for t in (rows[0].get("when") or []) if all(t in (r.get("when") or []) for r in rows)]


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


def select(kind, rows):
    """The construct a run of rows is the expansion of, up to the canonical form."""
    if rows is None:
        return None
    if kind == "acc":
        return acc_of(rows)
    if kind in ("prelude", "on_note"):
        return [dict(r) for r in rows]
    if kind == "row":
        return dict(rows[0]) if len(rows) == 1 else None
    if kind == "reset":
        return {"when": rows[0].get("when") or [], "sets": rows[0]["sets"]}
    if kind == "flush":
        r = rows[0]
        name = r["sets"][0][0]
        return name if not r.get("when") else [name, r["when"]]
    if kind == "producer":
        t, v = rows[0]["sets"][0]
        inner = v["and"][0]
        cell = inner["shr"][0] if _ishi(v) else inner
        return [cell.get("cell") or "#" + cell["global"], t, "hi" if _ishi(v) else "lo"]
    return None


# what an expansion does not carry, and what the round trip therefore drops:
# the annotations the print alone reads, the rank the tick order gives, the
# interval the player asserts at every store, and the degenerate divider
DROPPED = ("rank", "scope", "target", "note", "bound", "id")


def canon(x):
    """One construct in the form the round trip compares: no empty channel, sorted."""
    if isinstance(x, dict):
        return {k: canon(v) for k, v in sorted(x.items()) if v not in (None, [], {})}
    if isinstance(x, (list, tuple)):
        return [canon(y) for y in x]
    return x


def canon_acc(a):
    """One §5 record in the form its expansion can state: the player's own fields.

    A row carries no annotation, no rank and no interval to assert, so the round
    trip compares the record without them; a ``rate`` of 1 is no divider; a
    produce of one byte is its own low half; a width no delta masks by is not
    observable; and ``step_when`` and ``delta_when`` are one guard on a record
    whose gate has no false arm.
    """
    got = {k: v for k, v in a.items() if k not in DROPPED}
    if got.get("rate") == 1:
        del got["rate"]
    if not got.get("delta"):
        got.pop("width", None)
        got.pop("delta", None)
    if not got.get("delta") and not got.get("produce") and not isinstance(got.get("policy"), dict):
        got.pop("cell", None)  # a record that only gates moves no cell the rows name
    got["delta_when"] = list(got.pop("step_when", None) or []) + list(got.get("delta_when") or [])
    if not got["delta_when"]:
        del got["delta_when"]
    if got.get("gate"):
        got["gate"] = {"true": got["gate"].get("true") or []}
    w = got.get("width", 8)
    got["produce"] = [
        [t, "lo" if part == "byte" and w <= 8 else part] for t, part in got.get("produce", ())
    ]
    return canon(got)


def canon_of(kind, x):
    """One construct in the canonical form the round trip compares it by."""
    if x is None:
        return None
    if kind == "acc":
        return canon_acc(x)
    if kind == "producer":
        return [x[0], x[1], "lo" if x[2] == "byte" else x[2]]
    return canon(x)


def cost(x):
    """The size a construct or a run of rows carries in the object."""
    return len(json.dumps(canon(x), separators=(",", ":")))


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
