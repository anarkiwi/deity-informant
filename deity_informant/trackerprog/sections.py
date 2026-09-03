"""B7 -- the sections of the bound object, each the plane that supplied it.

``meta`` from B6's schedule, ``pitch`` and ``instruments`` from T2, ``accs`` from
T1, ``streams`` from the tick's own store sites, ``score`` from the fetch's own
visits and ``state0`` from the post-init image.
"""

from __future__ import annotations

from ..tuneprog.ir import Store
from . import build, tables
from .rows import blockrows, guards
from .shape import _Out, _dce, _flags, _instruments, _latches, _merge_halves
from .shape import _reads, _resets, _rows_of, _transposed


def assemble(b, order, drop, roles):  # noqa: C901
    """The object: the phases, the row program, the score and the records."""
    segs, out = b.segs, _Out()
    low, sch, pre = b.low, b.sch, []
    low.gate, low.scope, low.v.payload = frozenset(), set(segs.get("prelude", [])), False
    for i, r in enumerate(
        _rows_of(
            guards(b, blockrows(b, set(segs.get("prelude", [])), order, drop, roles), order),
            ("set", "reg"),
        )
    ):
        pre.append(out.stream("prelude%d" % i, [r]))
    low.gate = frozenset((id(c), t) for c, t in sch.boundary)
    low.scope, low.v.payload = set(segs["row"]), True
    rowprog, ncmd, nst = [], 0, 0
    for _lbl, kind, when, sets, _d in guards(
        b, blockrows(b, set(segs["row"]), order, drop, roles, True), order
    ):
        if kind == "note":
            rowprog.append({"note": True, **({"when": when} if when else {})})
        elif kind == "ins":
            rowprog.append({"ins": True})
        elif kind == "arm":
            if not ncmd:
                rowprog.append({"commands": True})
            ncmd += 1
        elif kind == "reg":
            nm = out.stream("note_on%d" % nst, [{"when": when, "sets": [list(x) for x in sets]}])
            rowprog.append({"stream": nm})
            nst += 1
        else:
            rowprog.append({"sets": [list(x) for x in sets], **({"when": when} if when else {})})
    body = set(b.sch.body)
    glob = [l for l in order if l not in body and l not in b.pro]
    low.gate, low.scope, low.v.payload = frozenset(), set(glob), False
    low.gate, low.scope, low.v.payload = frozenset(), set(b.pro), False
    prol = _rows_of(guards(b, blockrows(b, set(b.pro), order, drop, roles), order), ("set", "reg"))
    low.gate, low.scope, low.v.payload = frozenset(), set(glob), False
    gl = [
        out.stream("global%d" % i, [r])
        for i, r in enumerate(
            _rows_of(
                guards(b, blockrows(b, set(glob), order, drop, roles), order),
                ("set", "reg"),
            )
        )
    ]
    low.gate, low.scope = frozenset(), set(segs.get("machine", []))
    low.v.payload = False
    rows = []
    for lbl, kind, when, sets, _d in guards(
        b, blockrows(b, set(segs.get("machine", [])), order, drop, roles), order
    ):
        if kind in ("set", "reg"):
            rows.append((order.index(lbl), {"when": when, "sets": [list(x) for x in sets]}))
    accat = sorted(b.accat.items(), key=lambda kv: kv[1])
    rank, run, nm = 0, [], 0
    for at, row in rows:
        while accat and accat[0][1] <= at:
            if run:
                out.stream("machine%d" % nm, run, rank)
                nm, rank, run = nm + 1, rank + 1, []
            b.accs[accat[0][0]]["rank"] = rank
            rank, accat = rank + 1, accat[1:]
        run.append(row)
    if run:
        out.stream("machine%d" % nm, run, rank)
        rank += 1
    for key, _at in accat:
        b.accs[key]["rank"] = rank
        rank += 1
    return sections(b, out, pre, rowprog, gl, prol)


def sections(b, out, pre, rowprog, gl, prol):  # noqa: C901
    """The sections of the object, each the plane that supplied it."""
    low, sch, art, tick = b.low, b.sch, b.art, []
    rate = build.divider_rate(sch.divider[1], low, b.img) if sch.divider else 1
    phase = (
        build.divider_phase(b.img, sch.divider[0], rate - 1, rate)
        if sch.divider and rate > 1
        else 0
    )
    for name in sch.tick:
        tick += [{"stream": nm} for nm in pre] if name == "prelude" else [name]
    orders, pats = b.score.events(b.tie)
    armsets(b, pats, out)
    # the words a transposition of the object's own can reach, and the whole
    # region an instrument whose sound is no pitch reads its own from (§3.2)
    whole = trapped(
        b, tables.beyond_words(b.cells, low, b.pit, tables.beyond_limit(b.cells, low, b.pit))
    )
    words = whole[: max(_transposed(out.streams), 1)]
    for st in out.streams.values():
        st["beyond"] = {"id": "the fused tuning", "words": words}
    instruments = _instruments(art, b.view, b.names, b.ins, b.pwcols, b.img, b.accs)
    # a record no cell of the tune ever selects is no record of the object; a
    # score whose row states no instrument selects them all
    got = {r["ins"] for v in range(b.cells.voices) for r in b.score.rows[v]}
    if got - {None}:
        got |= {int(x) for x in b.img[b.voc.insbase : b.voc.insbase + b.cells.voices]}
        instruments = {k: v for k, v in instruments.items() if int(k) in got}
    pitched(b, instruments, whole)
    cellseed, globseed = b.cells.seed(b.img)
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": b.prog.meta.get("name"),
            "song": b.prog.meta.get("song"),
            "family": "bound",
            "cycles_per_tick": b.prog.meta["entry"]["cycles_per_tick"],
            "voices": b.cells.voices,
            "horizon": b.ticks,
            "voice_order": build.voice_order(
                b.p,
                sch.head,
                _latches(b.prog, b.proc, sch),
                sch.vidx,
                b.cells.voices,
                b.cells.stride,
            ),
            "commit_order": list(sch.commit_order),
            "instrument": {},
            "tempo": {
                "cell": b.clockcell,
                "step": sch.step,
                "rate": rate,
                "phase": phase,
                "boundary": [low.guard(c, t) for c, t in sch.boundary],
                **_resets(low, b.clockcell, sch),
            },
            "tick": tick,
            "row_consumes_tick": sch.row_consumes_tick,
            "row_command": "spent",
            "row": rowprog,
            "wide": sorted(set(low.wide) | wide(b)),
        },
        "pitch": {"base": b.pit.base, "freq": list(art["t2"]["pitch"]["entries"])},
        "streams": {**out.streams, **build.table_streams(b.voc, b.img)},
        "accs": dict(sorted(b.accs.items(), key=lambda kv: kv[1]["rank"])),
        "instruments": instruments,
        "score": {"patterns": pats, "orders": orders},
        "globals": {**flags(b), **({"streams": gl} if gl else {})},
        "state0": {
            "cells": cellseed,
            "globals": globseed,
            **({"prologue": {"rows": prol}} if prol else {}),
        },
    }
    _merge_halves(obj)
    _dce(obj)
    build.prune(obj)
    return obj


def coverage(b, obj):
    """What each plane supplied, counted from the object the binding emitted."""
    rows = [r for st in obj["streams"].values() for r in st["rows"] if "sets" in r]
    sets = sum(len(r["sets"]) for r in rows)
    sets += sum(len(s.get("sets", ())) for s in obj["meta"]["row"])
    t1 = b.art["t1"].get("accs") or []
    return {
        "store_sites": sum(1 for b in b.p.blocks.values() for x in b.stmts if type(x) is Store),
        "streams": len(obj["streams"]),
        "rows": len(rows) + len(obj["meta"]["row"]),
        "sets": sets,
        "accs": len(obj["accs"]),
        "t1_accumulators": len(t1),
        "t1_recognised": len(obj["accs"]),
        "cells": len(obj["state0"]["cells"]) + len(obj["state0"].get("globals", {})),
        "patterns": len(obj["score"]["patterns"]),
        "events": sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
        "instruments": len(obj["instruments"]),
        "refused": sorted(b.low.bad),
    }


def wide(b):
    """The voice cells the object reads as sixteen bits."""
    got = {a["cell"].lstrip("#") for a in b.accs.values() if a["width"] == 16}
    return {n for n in got if not n.startswith("ins.")}


def flags(b):
    """Section 5's producer flags: the carry a repeated addition leaves."""
    out = {}
    for a in b.accs.values():
        for x in _flags(a.get("delta")):
            out[x] = {"default": {"const": 0}}
    for a in b.accs.values():
        if out and isinstance(a.get("delta"), dict) and "repeat" in a["delta"]:
            a["flag"] = {"name": sorted(out)[0], "seed": 0}
    return {"flags": out} if out else {}


def armsets(b, pats, out):
    """The cells the score's own bytes reach: one command a row, over named cells."""
    live = set()
    for st in out.streams.values():
        for r in st["rows"]:
            live |= _reads(r.get("when", [])) | _reads([x[1] for x in r["sets"]])
    for a in b.accs.values():
        live |= _reads(list(a.values()))
    for pat in pats.values():
        for e in pat["events"]:
            if e["arm"] is None:
                continue
            got = [s for s in e["arm"]["rows"][0]["sets"] if s[0].lstrip("@#") in live]
            e["arm"] = {"rows": [{"sets": got}]} if got else None


def trapped(b, words):
    """A word past the tuning the score's own byte holds: no cell of the object.

    The packed row byte is the event's own fields (§3.6), so the object has no
    cell for it and the word that would read one is a ``trap``.
    """
    names = {v[0].lstrip("@#") for v in b.sc.values() if v[2] in b.packed}
    out = []
    for w in words:
        hit = [
            h
            for h in w.get("u16", ())
            if isinstance(h, dict) and (h.get("cell") or [""])[0] in names
        ]
        out.append(
            {"trap": "the packed row byte, which the score keeps as an " "event's own fields"}
            if hit
            else w
        )
    return out


def pitched(b, instruments, words):
    """An instrument whose sound the tuning has no note for: its own pitch (§3.5)."""
    top = b.pit.base + b.pit.n
    cur, want = {}, set()
    for v in range(b.cells.voices):
        for r in b.score.rows[v]:
            if r["ins"] is not None:
                cur[v] = r["ins"]
            if r["note"] is not None and r["note"] >= top and v in cur:
                want.add((cur[v], r["note"] - top))
    for key, d in sorted(want):
        rec = instruments.get(str(key))
        if rec is None or d >= len(words) or "trap" in words[d]:
            continue
        rec["pitch"] = {"value": words[d]}
        if d + 12 < len(words) and "trap" not in words[d + 12]:
            rec["pitch"]["octave"] = words[d + 12]
