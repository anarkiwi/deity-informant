"""T3 -- the rows: per copy, per tick a fetch happens on, the bytes each channel read.

A row is the fetches one copy made in one tick at one pattern base; its bytes are
each channel's span from the lowest position read to the highest, ``at`` the
span's offset from the cursor at entry. Rows group into the visits of a pattern.
"""

from __future__ import annotations

from .refuse import Refusal


def voices(fetches, chans, img, ticks):
    """``([voice], refusals)``: every copy's rows, order and patterns off the recording."""
    recs = sorted((f for got in fetches.values() for f in got), key=lambda f: f["n"])
    by, bad = {}, []
    for f in recs:
        entry = {t: e for t, e in f["entry"].items() if e is not None}
        if len(entry) < len(f["entry"]):
            missing = sorted(t for t, e in f["entry"].items() if e is None)
            bad.append(
                Refusal(
                    "score not cursor-shaped",
                    "%s:%s" % f["key"],
                    "",
                    "no entry value for %s" % missing,
                )
            )
            continue
        c0s = {t: c for t, (c, _a, _b) in entry.items()}
        bases = {t: b for t, (_c, _a, b) in entry.items()}
        t0, (_c, a, _b) = next(iter(entry.items()))
        copy = (a - chans[t0]["addr"]) // chans[t0]["stride"]
        rows = by.setdefault(copy, [])
        row = rows[-1] if rows else None
        if (
            row is None
            or row["tick"] != f["tick"]
            or any(t in row["base"] and row["base"][t] != b for t, b in bases.items())
        ):
            row = {"tick": f["tick"], "c0": {}, "base": {}, "pos": {}}
            rows.append(row)
        for t in entry:
            row["c0"].setdefault(t, c0s[t])
            row["base"].setdefault(t, bases[t])
        for a, t in f["reads"]:
            if t in row["base"]:
                row["pos"].setdefault(t, []).append(a - row["base"][t])
    out = []
    for copy in sorted(by):
        rows = by[copy]
        for r, nxt in zip(rows, rows[1:] + [None]):
            r["dur"] = (nxt["tick"] if nxt else ticks) - r["tick"]
            r["bytes"], r["at"] = {}, {}
            for t, ps in r["pos"].items():
                lo, hi = min(ps), max(ps)
                r["bytes"][t] = [int(img[(r["base"][t] + p) & 0xFFFF]) for p in range(lo, hi + 1)]
                if lo != r["c0"][t]:
                    r["at"][t] = lo - r["c0"][t]
        out.append({"copy": copy, "start": rows[0]["tick"], "rows": rows})
    return out, bad


def _key(r):
    return (
        r["dur"],
        tuple(sorted((t, tuple(b)) for t, b in r["bytes"].items())),
        tuple(sorted(r["at"].items())),
    )


def patterns(voice, chan, pointers):
    """Rows into the visits of one pattern: ``(order, patterns)``.

    A visit ends where the pattern channel's base changes or its cursor moves back;
    a pattern is named by its base's index in the pointer table and keyed on its rows.
    """
    rows = voice["rows"]
    visits, cur = [], []
    for r in rows:
        base, c0 = r["base"].get(chan), r["c0"].get(chan)
        if cur and (base != cur[-1]["base"].get(chan) or c0 < cur[-1]["c0"].get(chan, -1)):
            visits.append(cur)
            cur = []
        cur.append(r)
    if cur:
        visits.append(cur)
    names, pats, order = {}, {}, []
    for visit in visits:
        base = visit[0]["base"].get(chan)
        pid = (
            "p%d" % pointers.index(base)
            if base in pointers
            else "q%d" % len({v[0]["base"].get(chan) for v in visits[: visits.index(visit) + 1]})
        )
        key = tuple(_key(r) for r in visit)
        if key not in names:
            taken = [n for n in names.values() if n == pid or n.startswith(pid + ".")]
            names[key] = pid if not taken else "%s.%d" % (pid, len(taken))
            pats[names[key]] = [
                {"dur": r["dur"], "bytes": r["bytes"], "at": r["at"]} for r in visit
            ]
        order.append({"pattern": names[key], "tick": visit[0]["tick"], "rows": len(visit)})
    return order, pats


def emitted(voice):
    """The voice as the trackerprog carries it: rows of ``(dur, bytes)`` and nothing else."""
    return {
        "copy": voice["copy"],
        "start": voice["start"],
        "rows": [{"dur": r["dur"], "bytes": r["bytes"], "at": r["at"]} for r in voice["rows"]],
        "order": voice["order"],
        "patterns": voice["patterns"],
    }


def pointer_table(t2, view, names, img):
    """``{pattern table: [base per pointer index]}`` off T2's pointer tables and the image."""
    regs = {names.of(r.id): r for r in view.storage if r.id >= 0}
    out = {}
    for v in t2["score"]:
        for ch in v.get("pattern", ()):
            p = ch.get("pointers")
            if ch["table"] in out or not p:
                continue
            lo, hi = (regs.get(t) for t in p["tables"][:2])
            if lo is None or hi is None:
                continue
            got = []
            for i in range(p["entries"] + 1):
                a, b = lo.zero + i * max(lo.stride, 1), hi.zero + i * max(hi.stride, 1)
                inside = lo.base <= a < lo.base + lo.size and hi.base <= b < hi.base + hi.size
                got.append(int(img[a]) | int(img[b]) << 8 if inside else None)
            out[ch["table"]] = got
    return out
