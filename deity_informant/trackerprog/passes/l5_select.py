"""L5 -- instruction selection: a run of predicated rows covered by constructs.

BURS-style covering against :mod:`.expand`'s expansions with a size cost.  A run
of rows a construct expands to *and* that costs more than the construct is that
construct; the run selection does not cover stays as guarded rows -- the
residual.  Reading a construct back out of a run is the inverse of the
expansion, so the two are checked against each other over every construct of
every certified build (``tests/trackerprog/test_l5_roundtrip.py``).
"""

from __future__ import annotations

import copy
import json

from .expand import KINDS, expand, read, width_of
from .ir import Level

MAXRUN = 6  # the longest run one construct's expansion is, over the thirty builds


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
    # a record with no delta reaches no modulus, so the width the rows cannot
    # state is the byte the player's own store already holds it to
    a = {"cell": cell, "width": 8, "policy": "wrap", "produce": []}
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
    # §5's bound is the invariant the player asserts at every store, and the row
    # states it: the mask the store writes through is what the chip can see
    a["bound"] = {
        "interval": [0, (1 << a["width"]) - 1],
        "from": "projected",
        "witness": "the mask the row writes through",
    }
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


def covers(kind, rows):
    """The construct one run of rows is, where re-expanding it gives that run back."""
    got = select(kind, rows)
    if got is None:
        return None
    again = expand(kind, got)
    if again is None or canon(again) != canon(rows):
        return None
    # the cost is what selection recovered, not what the object annotates: the
    # bound is derived from the mask and the rank from the order it stands in
    return got if cost(canon_of(kind, got)) < cost(rows) else None


def cover(rows, kind="acc"):
    """``(residual rows, [(at, construct)])``: the longest run a construct covers.

    Greedy longest match with a size cost, which is what a BURS matcher does over
    a linear list: at each position the longest run a construct expands to and
    costs less than wins, and a position no construct covers keeps its row.
    """
    out, got, i = [], [], 0
    while i < len(rows):
        for n in range(min(MAXRUN, len(rows) - i), 0, -1):
            hit = covers(kind, rows[i : i + n])
            if hit is not None:
                got.append((len(out), hit))
                i += n
                break
        else:
            out.append(rows[i])
            i += 1
    return out, got


def phase_of(obj):
    """The ``{stream}`` phase selection covers: the one whose rows it can spend.

    An accumulator runs in the machine phase and a tune has one, so selection
    picks the phase whose covering is worth most and leaves the others alone.
    """
    best, at = None, None
    for i, e in enumerate(obj["meta"]["tick"]):
        if isinstance(e, str):
            continue
        rows = obj["streams"][e["stream"]]["rows"]
        left, got = cover(list(rows))
        gain = sum(cost(rows) for rows in [rows]) - cost(left) - sum(cost(c) for _a, c in got)
        if got and (best is None or gain > best):
            best, at = gain, (i, e["stream"], left, got)
    return at


def select_level(l4, kinds=("acc",)):
    """L4 to L5: the runs a construct covers become that construct, ranked in place."""
    obj = copy.deepcopy(l4.obj)
    del kinds
    at = phase_of(obj)
    picked = {}
    if at is not None:
        i, name, left, got = at
        rank = 0
        obj["meta"]["tick"][i] = "machine"
        for k, (_pos, rec) in enumerate(got):
            key = "%s_acc%d" % (name, k)
            picked[key] = {**rec, "rank": rank}
            rank += 1
        obj["accs"] = {**obj["accs"], **picked}
        if left:
            obj["streams"][name] = {"rows": left, "all": True, "rank": rank}
        else:
            del obj["streams"][name]
        arms = [{"acc": k} for k in picked]
        obj["instruments"] = {
            k: {**v, "accs": list(v.get("accs", ())) + arms} for k, v in obj["instruments"].items()
        }
        obj["meta"]["instrument"] = {
            **obj["meta"].get("instrument", {}),
            "accs": list(obj["meta"].get("instrument", {}).get("accs", ())) + arms,
        }
    return Level(
        5,
        art=l4.art,
        prog=l4.prog,
        proc=l4.proc,
        obj=obj,
        facts={**l4.facts, "selected": picked, "kinds": list(KINDS)},
    )
