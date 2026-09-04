"""L6's naming: a cell called by the register its sole reader writes.

The last of the canonicalisation's four passes, and the only one that renames
rather than moves: a cell one register alone reads takes that register's name,
and a naming that leaves the object bigger is not taken.
"""

from __future__ import annotations

import copy

from ..shape import _reads
from .l6_reads import _readers, elsewhere, named, order, rowat, written
from ..sizes import compact, xz

PLAYER = ("phase", "counter", "voice_index", "tied", "note", "ins", "rowsleft", "dur", "orderpos")
# the names §5's own vocabulary answers itself: a cell may not take one of them
RESERVED = ("pw", "pw_lo", "pw_hi", "freq", "freq_lo", "freq_hi", "wave", "lastnote")


def names(obj):
    """A cell named by the register its sole reader writes, where it has one."""
    got, seq, out = {}, order(obj), elsewhere(obj)
    for name in sorted(written(obj)):
        if name in PLAYER or name in out or name.startswith(("shadow.", "ins.")):
            continue
        regs = {
            s[0]
            for k in _readers(obj, seq, name, own=False)
            for s in rowat(obj, seq[k]).get("sets", ()) or ()
            if s[0][:1] not in "@#!*" and name in _reads(s[1])
        }
        readers = len(_readers(obj, seq, name, own=False))
        if len(regs) == 1 and readers == 1:
            want = "@" + regs.pop()
            if want[1:] in RESERVED or want[1:] in PLAYER:
                continue
            if want[1:] not in got.values() and want[1:] not in written(obj):
                got[name] = want[1:]
    return got


def rename(obj, sub):
    """One object with each renamed cell read and written by its new name."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("cell", "global") and isinstance(v, str):
                out[k] = sub.get(v, v)
            else:
                out[k] = rename(v, sub)
        return out
    if isinstance(obj, list):
        return [rename(x, sub) for x in obj]
    if isinstance(obj, str):
        pre = obj[:1] if obj[:1] in "@#!*" else ""
        return pre + sub.get(obj[len(pre) :], obj[len(pre) :])
    return obj


def sweep(obj):
    """The rows this level emptied, and the streams nothing is left naming.

    A stream a cursor or a re-point names keeps its rows and their numbers, so
    only a stream ``meta.tick`` alone reaches loses one.
    """
    keep = named(obj)
    for name, st in obj["streams"].items():
        if name in keep or "rows" not in st:
            continue
        st["rows"] = [
            r
            for r in st["rows"]
            if not (isinstance(r, dict) and set(r) <= {"when", "sets"} and not r.get("sets"))
        ]
    live = {k for k, st in obj["streams"].items() if st.get("rows") or k in keep}
    obj["streams"] = {k: v for k, v in obj["streams"].items() if k in live}
    obj["meta"]["tick"] = [
        e for e in obj["meta"]["tick"] if isinstance(e, str) or e["stream"] in live
    ]
    return obj


def _named(obj):
    """The object with its cells named canonically, where the naming costs nothing.

    A name is a choice and not a value, so the one the object keeps is the one
    that does not make it bigger.
    """
    sub = names(obj)
    if not sub:
        return obj, {}
    got = rename(copy.deepcopy(obj), sub)
    cells = got["state0"].get("cells")
    if cells is not None:
        got["state0"]["cells"] = {sub.get(k, k): v for k, v in cells.items()}
    if "wide" in got["meta"]:
        got["meta"]["wide"] = [sub.get(n, n) for n in got["meta"]["wide"]]
    if xz(compact(got)) > xz(compact(obj)):
        return obj, {}
    return got, sub
