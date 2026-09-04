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

from ..sizes import compact, xz
from .expand import KINDS, acc_of, expand, ishi
from .ir import Level

MAXRUN = 6  # the longest run one construct's expansion is, over the thirty builds


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
        cell = inner["shr"][0] if ishi(v) else inner
        return [cell.get("cell") or "#" + cell["global"], t, "hi" if ishi(v) else "lo"]
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


def gain(rows, left, got):
    """What covering one stream is worth: the rows it spends against what it states."""
    return cost(rows) - cost(left) - sum(cost(canon_of("acc", c)) for _a, c in got)


def worth(obj):
    """The stream selection covers: the one whose covering is worth most.

    A stream the machine phase ranks is covered in its own place; where the tick
    has no machine phase, a ``{stream}`` phase becomes one.
    """
    best, at = 0, None
    ranked = {k for k, st in obj["streams"].items() if "rank" in st}
    tick = obj["meta"]["tick"]
    phased = set() if "machine" in tick else {e["stream"] for e in tick if not isinstance(e, str)}
    for name in sorted(ranked | phased):
        st = obj["streams"].get(name)
        if st is None or not st.get("all"):
            continue
        left, got = cover(list(st["rows"]))
        if got and gain(st["rows"], left, got) > best:
            best, at = gain(st["rows"], left, got), (name, left, got)
    return at


def pieces(left, got):
    """The covering in tick order: the runs of rows, and the constructs between them."""
    out, prev = [], 0
    for pos, rec in got:
        if pos > prev:
            out.append(("rows", left[prev:pos]))
        out.append(("acc", rec))
        prev = pos
    if prev < len(left):
        out.append(("rows", left[prev:]))
    return out


def _slots(obj, name, got):
    """The machine's own rank order, with the covered stream replaced by its pieces.

    Streams and records share one rank order (§4.1), so the pieces stand where
    the stream stood and everything else keeps the place it had.
    """
    have = [(st["rank"], "stream", k) for k, st in obj["streams"].items() if "rank" in st]
    have += [(a.get("rank", 0), "acc0", (k, a)) for k, a in obj["accs"].items()]
    out = []
    for _rank, kind, key in sorted(have, key=lambda x: (x[0], x[1])):
        if kind == "stream" and key == name:
            out += list(got)
        else:
            out.append((kind, key))
    return out


def select_level(l4, kinds=("acc",)):  # noqa: C901 - one clause per placing
    """L4 to L5: the runs a construct covers become that construct, ranked in place.

    The covering is kept where the object it makes is no larger: a size cost over
    the whole object and not over the run alone, since a record the instruments
    share states once what the rows state at every arm.
    """
    was = copy.deepcopy(l4.obj)
    obj = copy.deepcopy(l4.obj)
    del kinds
    at = worth(obj)
    picked = {}
    if at is not None:
        name, left, got = at
        run = pieces(left, got)
        phase = name in {e["stream"] for e in obj["meta"]["tick"] if not isinstance(e, str)}
        if phase:
            obj["meta"]["tick"] = [
                "machine" if not isinstance(e, str) and e["stream"] == name else e
                for e in obj["meta"]["tick"]
            ]
        slots = run if phase else _slots(obj, name, run)
        # what the stream carried besides its rows is the pieces' too: the words
        # past the tuning a read of it reaches, its own guard, its divider
        base = {k: v for k, v in obj["streams"].pop(name).items() if k not in ("rows", "rank")}
        for rank, (kind, x) in enumerate(slots):
            if kind == "rows":
                obj["streams"]["%s%d" % (name, rank)] = {**base, "rows": x, "rank": rank}
            elif kind == "stream":
                obj["streams"][x]["rank"] = rank
            elif kind == "acc0":
                obj["accs"][x[0]]["rank"] = rank
            else:
                picked["%s_acc%d" % (name, rank)] = {**x, "rank": rank}
        obj["accs"] = {**obj["accs"], **picked}
        arms = [{"acc": k} for k in picked]
        # a record the accumulators are armed on: an object whose score names no
        # instrument still has the one every voice enters holding
        obj["instruments"] = obj["instruments"] or {"0": {}}
        obj["instruments"] = {
            k: {**v, "accs": list(v.get("accs", ())) + arms} for k, v in obj["instruments"].items()
        }
        obj["meta"]["instrument"] = {
            **obj["meta"].get("instrument", {}),
            "accs": list(obj["meta"].get("instrument", {}).get("accs", ())) + arms,
        }
    kept = picked and xz(compact(obj)) <= xz(compact(was))
    return Level(
        5,
        art=l4.art,
        prog=l4.prog,
        proc=l4.proc,
        obj=obj if kept else was,
        facts={
            **l4.facts,
            "covered": picked,
            "selected": picked if kept else {},
            "xz": [xz(compact(was)), xz(compact(obj))],
            "kinds": list(KINDS),
        },
    )
