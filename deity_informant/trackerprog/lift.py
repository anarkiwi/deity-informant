"""T2 -- ``tuneprog.T2.json``: pitch, cursors, streams and the materialised score."""

from __future__ import annotations

from collections import Counter

from ..tuneprog.acchist import Cells
from ..tuneprog.accshape import Ctx
from ..tuneprog.ir import Bin, Const, Var, evalbin
from . import pitch, score, streams
from .cursors import accesses, copies, strides
from .resolve import Program
from .hist import Eval
from .refuse import Refusal


def _stride_of(names, rid):
    """The copy stride of a cell's region: its group's, or its own."""
    for g in (names.groups or {}).values():
        if rid in g.get("members", ()) or g.get("split") == rid:
            return max(int(g.get("stride", 1)), 1)
    return 1


def _static(e, env):
    """A guard over the copy index alone, evaluated; ``None`` where it reads more."""
    t = type(e)
    if t is Const:
        return e.v
    if t is Var:
        return env.get(e.n)
    if t is Bin:
        a, b = _static(e.a, env), _static(e.b, env)
        return None if a is None or b is None else evalbin(e.op, a, b, e.w or 1)
    return None


def _holds(acc, env):
    """False when one of the access's guards over the copy index alone is false."""
    for c, t, *_w in acc.guards:
        v = _static(c, env)
        if v is not None and bool(v) != bool(t):
            return False
    return True


def _copy_shifts(chan, stride, names):
    """``[(copy, env, displacement)]``: each copy of a channel's cursor cell.

    A copy is bound by an index name (``env``), kept where the access's own guards
    admit it, or, where the callers unrolled the loop, by the constant displacement
    each arm reads the cell at.
    """
    if chan.cursor[0] != "cell":
        return [(i, env, 0) for i, env in enumerate(copies(chan.accs[0], stride))]
    consts = sorted(
        {
            a.cursor.index.v
            for a in chan.accs
            if a.cursor and type(a.cursor.index) is Const and _holds(a, {})
        }
    )
    if consts:
        k = _stride_of(names, chan.cursor[1])
        return [(d // k, {}, d) for d in consts]
    if any(a.cursor and a.cursor.index is None for a in chan.accs):
        return [(0, {}, 0)]
    out = {}
    for a in chan.accs:
        for i, env in enumerate(copies(a, stride)):
            if _holds(a, env):
                out.setdefault(i, (i, env, sum(env.values())))
    return [out[i] for i in sorted(out)]


def _score(ev, cells, chans, rgn, names, stride, P, prid=frozenset()):
    order, pattern, refused = score.classify(chans, rgn, names, P, prid)
    voices = {}
    img = cells.img
    for role, group in (("order", order), ("pattern", pattern)):
        for chan in group:
            bound = _copy_shifts(chan, stride, names)
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
    chans = score.channels(accs, rgn, frozenset(stride))
    ptab = pitch.table(view, names)
    prid = set(ptab["regions"]) if ptab else set()
    strm, sel = [], []
    voices, refused, orders = _score(ev, cells, chans, rgn, names, stride, P, prid)
    for key, group in streams.group(chans).items():
        group = [c for c in group if c.table not in prid and c not in orders]
        if not group:
            continue
        shifts = sorted({s for c in group for _i, _e, s in _copy_shifts(c, stride, names)})
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
