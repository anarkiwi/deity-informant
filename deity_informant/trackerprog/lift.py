"""T2 -- ``tuneprog.T2.json``: pitch, cursors, streams and the materialised score."""

from __future__ import annotations

from collections import Counter

from ..tuneprog.acchist import Cells
from ..tuneprog.accshape import Ctx
from ..tuneprog.ir import Const
from . import pitch, score, streams
from .cursors import accesses, copies, strides
from .resolve import Program
from .hist import Eval
from .refuse import Refusal


def _copy_shifts(chan, stride):
    """``[(copy, env, displacement)]``: each copy of a channel's cursor cell.

    A copy is bound by an index name (``env``) or, where the callers unrolled the
    loop, by the constant displacement each arm reads the cell at.
    """
    a = chan.accs[0]
    if chan.cursor[0] != "cell":
        return [(i, env, 0) for i, env in enumerate(copies(a, stride))]
    idx = a.cursor.index
    if idx is None:
        return [(0, {}, 0)]
    if type(idx) is Const:
        ds = sorted(
            {
                acc.cursor.index.v
                for acc in chan.accs
                if acc.cursor and type(acc.cursor.index) is Const
            }
        )
        return [(i, {}, d) for i, d in enumerate(ds)]
    return [(i, env, sum(env.values())) for i, env in enumerate(copies(a, stride))]


def _score(ev, cells, chans, rgn, names, stride, P):
    order, pattern, refused = score.classify(chans, rgn, names, P)
    voices = {}
    img = cells.img
    for role, group in (("order", order), ("pattern", pattern)):
        for chan in group:
            bound = _copy_shifts(chan, stride)
            if not bound:
                refused.append(
                    Refusal(
                        "score not cursor-shaped",
                        score._cellname(chan, names),
                        chan.accs[0].site[0],
                        "unbound copy index %s" % sorted(chan.accs[0].copyvars),
                    )
                )
            if role == "pattern":
                for cell, ok, sites, _tables in score.fed(chan, P, rgn):
                    if not ok:
                        refused.append(
                            Refusal(
                                "score not cursor-shaped",
                                "%s@$%04X" % (names.of(cell[0]), cell[1]),
                                ",".join(sites),
                                "the pattern selector is filled by no table read",
                            )
                        )
            bytes_ = score.terminators(chan, P, rgn)
            byte = min(bytes_) if bytes_ else None
            for copy, env, shift in bound:
                got, term, bad = score.materialise(ev, cells, chan, env, shift, byte)
                if got is None:
                    refused.append(
                        Refusal(
                            "score not cursor-shaped",
                            score._cellname(chan, names),
                            chan.accs[0].site[0],
                            "no history for the base or the cursor",
                        )
                    )
                    continue
                v = voices.setdefault(copy, {})
                v.setdefault(role, []).append(
                    {
                        "table": names.of(chan.table),
                        "cursor": score._cellname(chan, names),
                        "depth": chan.depth,
                        "pointers": score.pointers(chan, rgn, names),
                        "terminator": term,
                        "unresolved_ticks": int(bad.sum()),
                        "events": [
                            {
                                "tick": e.tick,
                                "ticks": e.ticks,
                                "base": e.base,
                                "pos": e.pos,
                                "bytes": streams.bytes_at(img, e.base, e.pos, e.end),
                            }
                            for e in got
                        ],
                    }
                )
    out = [{"copy": k, **v} for k, v in sorted(voices.items())]
    return out, list(dict.fromkeys(refused)), order


def document(view, names, hist, cert=None):
    """The T2 document over the presentation view and the certified history."""
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    rgn = view.by_id()
    ctx = Ctx(view, names)
    cells = Cells(view, names, hist)
    ev = Eval(cells)
    P = Program(ctx)
    accs = accesses(ctx, names, P)
    stride = strides(view, names)
    chans = score.channels(accs, rgn)
    ptab = pitch.table(view, names)
    prid = set(ptab["regions"]) if ptab else set()
    strm, sel = [], []
    voices, refused, orders = _score(ev, cells, chans, rgn, names, stride, P)
    for key, group in streams.group(chans).items():
        group = [c for c in group if c.table not in prid and c not in orders]
        if not group:
            continue
        shifts = sorted({s for c in group for _i, _e, s in _copy_shifts(c, stride)})
        got = streams.table(cells, key, group, rgn, names, shifts, score.stepping(P, rgn, key[1:]))
        if got is not None:
            (strm if got["kind"] == "stream" else sel).append(got)
    return {
        "plane": "S6-view",
        "horizon": {
            "ticks": cells.ticks,
            "complete": bool(sub.get("complete")),
            "period": sub.get("period"),
        },
        "pitch": (
            None
            if ptab is None
            else {**ptab, "accessors": pitch.accessors(accs, view, names, prid)}
        ),
        "streams": strm,
        "selectors": sel,
        "score": voices,
        "refusals": [r.to_dict() for r in refused],
        "stats": {
            "accesses": len(accs),
            "channels": len(chans),
            "kinds": dict(Counter(c.kind for c in chans)),
        },
    }


def summary(doc):
    """One line per stream, selector and score channel of a T2 document."""
    out = []
    p = doc["pitch"]
    if p:
        out.append(
            "  pitch %s %d entries, %d accessors"
            % (p["layout"], len(p["entries"]), len(p["accessors"]))
        )
    for s in doc["streams"] + doc["selectors"]:
        out.append(
            "  %-8s %-22s %2d cols x %3d entries, %3d visited, step %s, terminator %s"
            % (
                s["kind"],
                s["cursor"],
                len(s["columns"]),
                s["entries"],
                len(s["visited"]),
                s["step"],
                s["terminator"],
            )
        )
    for v in doc["score"]:
        for role in ("order", "pattern"):
            for ch in v.get(role, ()):
                ev = ch["events"]
                out.append(
                    "  voice %d %-7s %-6s via %-18s depth %s, %4d events, %2d bases, "
                    "terminator %s, pointers %s"
                    % (
                        v["copy"],
                        role,
                        ch["table"],
                        ch["cursor"],
                        ch["depth"],
                        len(ev),
                        len({e["base"] for e in ev}),
                        ch["terminator"],
                        ch["pointers"]
                        and "%s[%d]"
                        % ("/".join(ch["pointers"]["tables"]), ch["pointers"]["entries"]),
                    )
                )
    return "\n".join(out)
