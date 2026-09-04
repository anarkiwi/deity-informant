"""What L6's passes read off an object: its tick order, its rows and their readers.

The four canonicalisation passes share one reading of the object, so it stands
once: the order the tick runs its statements in, the statement at a position,
what stands outside the streams, and the cells the object may not rename.
"""

from __future__ import annotations

import copy

from ..shape import _reads


def order(obj):
    """The rows one tick runs unconditionally, in that order, with their stream.

    A ``{stream}`` phase runs its guarded rows for every voice on every tick; the
    channel's own streams may sit on a cursor, so they are no part of this order.
    """
    out = []
    for e in obj["meta"]["tick"]:
        if isinstance(e, str):
            continue
        st = obj["streams"].get(e["stream"])
        if st is not None and st.get("all"):
            out += [(e["stream"], i) for i in range(len(st["rows"]))]
    return out


def rowat(obj, at):
    got = obj["streams"][at[0]]["rows"][at[1]]
    return got if isinstance(got, dict) else {}


def elsewhere(obj):
    """Every cell the object reads outside the rows one tick runs, by name.

    A cell a record, an instrument, the score or the row program reads is no
    cell this level may spend: the rows are not where it is read.
    """
    o = copy.deepcopy(obj)
    for at in order(o):
        r = o["streams"][at[0]]["rows"][at[1]]
        if isinstance(r, dict):
            r["when"], r["sets"] = [], []
    out, stack = _reads(o), [o]
    while stack:  # every assignment the object makes anywhere but those rows
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("sets", "all") and isinstance(v, list):
                    out |= {t[0].lstrip("@#!*") for t in v if isinstance(t, list) and t}
                stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def written(obj):
    """``{cell: [position]}``: where each cell the object's rows assign is written."""
    out = {}
    for k, at in enumerate(order(obj)):
        for s in rowat(obj, at).get("sets", ()) or ():
            if s[0][:1] in "@#!*":  # a register is the chip's and no cell of the object
                out.setdefault(s[0].lstrip("@#!*"), []).append(k)
    return out


def named(obj):
    """Every stream name the object reaches other than through ``meta.tick``.

    A stream a cursor sits on, a table a read indexes, a prelude, a note-on, a
    re-point or the channel names is a stream whose rows keep their numbers.
    """
    o = {k: v for k, v in obj.items() if k != "meta"}
    o["meta"] = {k: v for k, v in obj["meta"].items() if k != "tick"}
    out = set(obj.get("state0", {}).get("cursors", ())) | set(
        obj.get("state0", {}).get("gcursors", ())
    )
    for name in obj.get("globals", {}).get("streams", ()):
        out.add(name)
    for name in obj.get("globals", {}).get("after", ()):
        out.add(name)
    out |= {k for k, st in obj["streams"].items() if "rank" in st or not st.get("all")}
    stack = [o]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("stream", "prelude", "on_note") and isinstance(v, str):
                    out.add(v)
                elif k == "tabcell":
                    out.add(v[0])
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _readers(obj, seq, name, own=True):
    """The positions of the rows that read one cell.

    ``own`` counts the row that writes it, whose read is the value it moves and
    not a reader of the cell's own.
    """
    out = []
    for k, at in enumerate(seq):
        r = rowat(obj, at)
        sets = r.get("sets", ()) or []
        if not own and any(s[0].lstrip("@#!*") == name for s in sets):
            continue
        if name in _reads(r.get("when", [])) | _reads([s[1] for s in sets]):
            out.append(k)
    return out
