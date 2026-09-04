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
from ..sizes import compact, xz
from .ir import Level

PLAYER = ("phase", "counter", "voice_index", "tied", "note", "ins", "rowsleft", "dur", "orderpos")
# the names §5's own vocabulary answers itself: a cell may not take one of them
RESERVED = ("pw", "pw_lo", "pw_hi", "freq", "freq_lo", "freq_hi", "wave", "lastnote")


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
    tick = obj["meta"]["tick"]
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
    del tick
    return out


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


def names(obj):
    """A cell named by the register its sole reader writes, where it has one."""
    got, seq, out = {}, order(obj), elsewhere(obj)
    for name, at in sorted(written(obj).items()):
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
        del at
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
