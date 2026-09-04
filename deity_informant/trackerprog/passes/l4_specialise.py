"""L4 -- materialised PNF: the fetch, the cursors and the order specialised.

Partial evaluation against the tune's own static tables.  The fetch region is
replayed over the certified horizon and its visits become §3.6's events, whose
fields are the values the visit stored into the cells L3 typed -- ``dur`` into
the clock's, ``note`` into the tuning's index, ``ins`` into the selector's --
and the walk the horizon took becomes the score's own play lists.  The counter
the rows stepped becomes ``meta.tempo``, which the player steps, so the reads
that stood before that step read the step the tick is (``phase``).  A cursor a
row walks over a declared table becomes a §3.3 cursor stream with ``next`` and
``jump``, and what no construct covers stays a row: the residual.

The termination policy is the spec's own materialisation rule -- the horizon,
and no further.
"""

from __future__ import annotations

import copy

from .. import build, record, tables
from ..shape import _instruments
from ..events import Score
from .ir import Level

MOVED = ("rowsleft", "dur", "note", "ins", "orderpos", "lastnote")


def visits(l3, ticks):
    """The horizon recorded over the fetch region: one visit a row of one voice."""
    prog, proc = l3.prog, l3.proc
    p = prog.procs[proc]
    rowblocks = [l for n, g in l3.facts["segments"] if n == "row" for l in g]
    if not rowblocks:
        return [], None, {}
    exits = sorted({s for l in rowblocks for s in _succs(p.blocks[l].term) if s not in rowblocks})
    exits = [e for e in exits if type(p.blocks[e].term).__name__ != "Trap"]
    img = record.interp.Player(prog, _fetch()).run_init().m
    inputs, _bad = build.pinned_inputs(prog, img)
    vnames = sorted(l3.facts["vidx"])
    R, fetches, _trap, _obs = record.run(
        prog,
        proc,
        [(rowblocks[0], rowblocks, exits)],
        ticks,
        inputs=inputs,
        envvars={(proc, rowblocks[0]): vnames},
    )
    recs = fetches[(proc, rowblocks[0])]
    cells = l3.facts["cells"]
    vvar = record.voice_name(recs, vnames, cells.voices, cells.stride)
    return recs, vvar, dict(R.trips)


def _succs(term):
    from ...tuneprog.graph import succs  # pylint: disable=import-outside-toplevel

    return succs(term)


def _fetch():
    from .. import region  # pylint: disable=import-outside-toplevel

    return region.Fetch()


def score_of(l3, recs, vvar, addrs, ticks):
    """§3.6's own score: the visits as events with fields, and the play lists."""
    cells, pit = l3.facts["cells"], l3.facts["pitch"]
    top = (pit.base + pit.n) if pit is not None else 0x100
    img = l3.prog.reads()
    ordpos = addrs.get("orderpos")
    seed = [int(img[ordpos + v * cells.stride]) for v in range(cells.voices)] if ordpos else None
    own = {a for k, a in addrs.items() if a is not None and (cells.at(a) or (None,))[0] == "voice"}
    roles = {
        "dur": addrs.get("rowsleft") if addrs.get("rowsleft") is not None else -1,
        "note": addrs.get("note"),
        "ins": addrs.get("ins"),
    }
    if roles["note"] is None or roles["ins"] is None:
        return None
    sc = Score(recs, vvar, roles, cells.voices, cells.stride, ordpos, top, seed, own)
    del ticks
    return sc


def clockreads(obj, cell, upto):
    """Reads of the clock's cell that stand before its own step read the step it is.

    The player moves the counter once, before any phase; the value a row read
    before the rows moved it is that step, which §3.6 calls ``phase``.
    """
    seen = [False]

    def walk(node, before):
        if isinstance(node, dict):
            if node.get("cell") == cell and before:
                return {"cell": "phase"}
            return {k: walk(v, before) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x, before) for x in node]
        return node

    for name, st in obj["streams"].items():
        out = []
        for i, r in enumerate(st.get("rows", ())):
            before = not seen[0]
            if (name, i) == upto:
                seen[0] = True
            out.append(walk(r, before))
        if "rows" in st:
            st["rows"] = out
    return obj


def clockrow(obj, cell):
    """``(stream, index)`` of the row that steps the clock, where one does."""
    for name, st in obj["streams"].items():
        for i, r in enumerate(st.get("rows", ())):
            got = [s for s in r.get("sets", ()) if s[0].lstrip("@#!*") == cell]
            if got and _reads(got[0][1], cell):
                return (name, i)
    return None


def _reads(node, cell):
    if isinstance(node, dict):
        return node.get("cell") == cell or any(_reads(v, cell) for v in node.values())
    if isinstance(node, list):
        return any(_reads(x, cell) for x in node)
    return False


def rowsegment(l3):
    """The names of the streams the row segment made: what the score replaces."""
    at = [i for i, (n, _g) in enumerate(l3.facts["segments"]) if n == "row"]
    got, k = set(), 0
    for i, (name, _g) in enumerate(l3.facts["segments"]):
        got |= {"%s%d" % (name, i)} if i in at else set()
        k += 1
    return got


def cursor_streams(obj, types):
    """``{cell: table}``: the cursors this level did not specialise into records.

    A cursor a row walks over a declared table is §3.3's own stream, stepped by
    the player with ``hold``, ``next`` and ``jump``; this prototype states which
    cursors are there and leaves the walk as the rows that walk it.
    """
    return {
        cell: role.split(":", 1)[1]
        for cell, role in types.items()
        if role.startswith("cursor:") and role.split(":", 1)[1] in obj["streams"]
    }


def drop_rows(obj, dead):
    """Every row the specialisation replaced, and every stream left with none."""
    for name, st in obj["streams"].items():
        if "rows" not in st:
            continue
        st["rows"] = [r for i, r in enumerate(st["rows"]) if (name, i) not in dead]
    live = {k for k, st in obj["streams"].items() if st.get("rows")}
    obj["streams"] = {k: v for k, v in obj["streams"].items() if k in live}
    obj["meta"]["tick"] = [
        e for e in obj["meta"]["tick"] if isinstance(e, str) or e["stream"] in live
    ]
    for key in ("streams", "after"):
        if key in obj["globals"]:
            obj["globals"][key] = [k for k in obj["globals"][key] if k in live]
    return obj


def specialise(l3, ticks=None):  # noqa: C901 - one clause a construct
    """L3 to L4: the score materialised, the clock the player's, the cursors stepped."""
    obj = copy.deepcopy(l3.obj)
    ticks = ticks or obj["meta"]["horizon"]
    sch, types = l3.facts["schedule"], l3.facts["types"]
    addrs = _addrs(l3)
    recs, vvar, trips = visits(l3, ticks)
    sc = score_of(l3, recs, vvar, addrs, ticks) if recs else None
    got = rowsegment(l3)
    dead = set()
    if sc is not None:
        orders, pats = sc.events(lambda _r: False)
        obj["score"] = {"patterns": pats, "orders": orders}
        obj["instruments"] = _records(l3)
        # a row that keys no sound names no note: the step reads the one field
        # of §3.6 that says whether it does
        obj["meta"]["row"] = [{"note": True, "when": [["sounds", "!=", 0]]}] + (
            [{"ins": True}] if obj["instruments"] else []
        )
        obj["meta"]["row_consumes_tick"] = sch.row_consumes_tick
        obj["meta"]["tick"] = [
            "row" if not isinstance(e, str) and e["stream"] in got else e
            for e in obj["meta"]["tick"]
        ]
        obj["meta"]["tick"] = _once(obj["meta"]["tick"])
        obj["streams"] = {k: v for k, v in obj["streams"].items() if k not in got}
    cell = l3.facts["clock"]["cell"]
    step = clockrow(obj, cell) if cell else None
    if step is not None and sch.clock:
        low = l3.facts["reader"]
        low.v.subst = {sch.clock[1].n: {"cell": "phase"}}
        obj["meta"]["tempo"] = {
            "cell": cell,
            "step": sch.step,
            "rate": 1,
            "phase": 0,
            "boundary": [low.guard(c, t) for c, t in sch.boundary],
        }
        clockreads(obj, cell, step)
        dead.add(step)
    drop_rows(obj, dead)
    return Level(
        4,
        art=l3.art,
        prog=l3.prog,
        proc=l3.proc,
        obj=obj,
        facts={
            **l3.facts,
            "events": sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            "patterns": len(obj["score"]["patterns"]),
            "cursors": cursor_streams(obj, types),
            "trips": trips,
            "materialised": sc is not None,
        },
    )


def _records(l3):
    """The instrument table T2 named, one record an entry the horizon selected."""
    art = l3.art
    ins = tables.instrument_table(art, art["view"], art["names"])
    if not ins:
        return {}
    img = l3.prog.reads()
    return _instruments(art, art["view"], art["names"], ins, {}, img, {})


def _once(tick):
    """``meta.tick`` with the run of phases the score replaced stated once."""
    out = []
    for e in tick:
        if e == "row" and out and out[-1] == "row":
            continue
        out.append(e)
    return out


def _addrs(l3):
    """The address behind each slot the typing settled, for the recorder to read."""
    cells, out = l3.facts["cells"], {}
    back = {new: old for old, new in l3.facts["renamed"].items()}
    for name, role in l3.facts["types"].items():
        if role not in MOVED:
            continue
        own = back.get(name, name)
        out[role] = cells.vcells.get(own) or cells.baseof(own)
    return out
