"""B7 -- one run of the certified tick, recorded: what a lift cannot read statically.

The interpreter of :mod:`.interp` runs the program from the post-init image; this
subclass records the score bytes each fetch read as its own temps, the largest
number of times an inner loop turned, and the tick each row boundary landed on.
"""

from __future__ import annotations

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of
from ..tuneprog.ir import If, Let
from . import interp
from . import region as rgn


def fetch_of(prog, proc, groups):
    """A :class:`~.region.Fetch` over given block groups, each exporting every temp.

    A group is ``(entry, blocks, exits)``: every block the region's own paths
    leave by, since a visit that left another way is a visit the score loses.
    """
    F = rgn.Fetch()
    p = prog.procs[proc]
    for entry, blocks, exits in groups:
        names = tuple(s.n for l in blocks for s in p.blocks[l].stmts if type(s) is Let)
        F.regions[(proc, entry)] = rgn.Region(
            proc, frozenset(blocks), entry, exits[0], frozenset(exits), frozenset(), names
        )
    return F


class Recorder(interp.Player):
    """The tick run whole, with every inner loop's turn count kept."""

    def __init__(self, prog, fetch, inputs=None, envvars=None, track=(), marks=()):
        super().__init__(prog, fetch, inputs, envvars=envvars)
        self.track, self.resets = {}, {}
        for k, node, kind in track:
            (self.resets if kind == "reset" else self.track)[id(node)] = k
        # a name an unrolled loop binds carries the loop it turns in: one value
        # a turn, and the turn is what the loop's own count says
        self.marks = {id(e): (k, t) for k, e, t in marks}
        self.runs, self.trips = {}, {}
        self.seen, self.turns, self.seq = set(), {}, 0

    def ev(self, e, F):
        i = id(e)
        k = self.resets.get(i)
        if k is not None:
            self.runs[k] = 0
        k = self.track.get(i)
        if k is not None:
            self.runs[k] = self.runs.get(k, 0) + 1
            self.trips[k] = max(self.trips.get(k, 0), self.runs[k])
        got = self.marks.get(i)
        if got is None:
            return super().ev(e, F)
        self.seen.add(got[0])
        v = super().ev(e, F)
        if got[1] is not None and self.rec is not None:
            # the head's own test runs after its statements and before the body's
            h, inhead = got[1]
            self.turns[(got[0], self.runs.get(h, 0) - (0 if inhead else 1))] = v
        return v

    def _begin(self, key, region, F):
        self.seen, self.turns = set(), {}
        super()._begin(key, region, F)

    def _end(self, F, prev, to, rets=None):
        self.rec["seen"] = sorted(self.seen)
        self.rec["turns"] = dict(self.turns)
        self.rec["seq"], self.seq = self.seq, self.seq + 1
        super()._end(F, prev, to, rets)


def headers(prog, proc, blocks):
    """``[(header, node, kind)]``: each inner loop's own test, and what resets its count."""
    p = prog.procs[proc]
    g = cfg(p)
    loops = natural_loops(g, idoms(p, g), preds_of(p))
    preds = preds_of(p)
    out = []
    for h, (body, _latch) in loops.items():
        if not (h in blocks and len(body) < len(blocks) and type(p.blocks[h].term) is If):
            continue
        out.append((h, p.blocks[h].term.c, "track"))
        for q in preds.get(h, ()):
            if q in body:
                continue
            for s in p.blocks[q].stmts:
                for e in (getattr(s, "e", None), getattr(s, "v", None)):
                    if e is not None:
                        out.append((h, e, "reset"))
    return out


class _Seen(dict):
    """A block map that remembers the labels one run asked it for."""

    def __init__(self, d, log):
        super().__init__(d)
        self.log = log

    def __getitem__(self, k):
        self.log.append(k)
        return dict.__getitem__(self, k)


def firstonly(prog, proc, inputs=None, ticks=3):
    """The blocks the tick runs on its first call alone, where that call runs no other.

    A tune whose init only schedules runs its reset on the first call and spends
    that tick, which is what section 4.7's ``state0.prologue`` states.
    """
    p = prog.procs[proc]
    keep, log = p.blocks, []
    p.blocks = _Seen(keep, log)
    try:
        pl = interp.Player(prog, rgn.Fetch(), inputs).run_init()
        first, later = None, set()
        for _ in range(max(ticks, 2)):
            log.clear()
            pl.tick()
            if first is None:
                first = set(log)
            else:
                later |= set(log)
    finally:
        p.blocks = keep
    return frozenset(first - later)


def run(prog, proc, groups, ticks, inputs=None, envvars=None, loops=(), marks=()):
    """``(recorder, fetches)``: one horizon recorded over the given block groups."""
    F = fetch_of(prog, proc, groups)
    R = Recorder(prog, F, inputs, envvars=envvars, track=loops, marks=marks).run_init()
    obs, trap = R.render(ticks)
    return R, R.fetches, trap, obs


def voice_name(records, names, voices, stride=1):
    """The name the fetch's own environment carries the voice in.

    The voice loop leaves several names for its index and the score needs the one
    the fetch itself carries: every voice, and no value that is not one. A voice's
    copies stand ``stride`` apart, so the index is the voice number times it.
    """
    want = {k * stride for k in range(voices)}
    for n in names:
        got = [r["env"].get(n) for r in records]
        if got and set(got) == want:
            return n
    return None


def bytes_of(low, name, got):
    """``[(cell, value)]`` one name the fetch bound supplies: one a *turn* where it turns.

    A name an unrolled loop binds takes one value a turn of it, so the visit
    supplies the constant each turn read into that turn's own cell; a name the
    fetch binds once supplies one constant into the one cell it is.
    """
    cells = low.turns.get(name)
    if cells:
        return [
            (cells[j], int(v))
            for (n, j), v in sorted(got.get("turns", {}).items())
            if n == name and 0 <= j < len(cells)
        ]
    if name in low.temps and name in got["temps"]:
        return [(low.temps[name], int(got["temps"][name]))]
    return []


def score_of(records, low, vvar, ordernames, tempo, voices, ordpos=None, keep=None, stride=1):
    """The score the fetches read: per-voice orders of patterns of events.

    A visit ends where the fetch stepped the *order* cursor T2 named -- which is
    the pattern's own end, and the only place the score's shape comes from.
    """
    rows = {v: [] for v in range(voices)}
    for got in sorted(records, key=lambda r: r.get("seq", 0)):
        v = got["env"].get(vvar)
        v = None if v is None or v % stride else v // stride
        if v is None or v not in rows:
            continue
        sets = [
            ["@" + cell, val]
            for n in got["seen"]
            for cell, val in bytes_of(low, n, got)
            if keep is None or cell in keep
        ]
        pat = next((int(got["temps"][n]) for n in got["seen"] if n in ordernames), 0)
        at = v * stride
        dur = next((c[2] for c in got["cmds"] if c[0] == "ram" and c[1] == tempo + at), 0)
        ends = ordpos is not None and any(
            c[0] == "ram" and c[1] == ordpos + at for c in got["cmds"]
        )
        rows[v].append((pat, dur, sets, ends))
    orders, pats = [], {}
    for v in range(voices):
        play, cur, last = [], [], None
        for pat, dur, sets, ends in rows[v]:
            if last is not None and (pat != last or cur and cur[-1][2]):
                _visit(play, pats, last, cur)
                cur = []
            cur.append((dur, sets, ends))
            last = pat
        if cur:
            _visit(play, pats, last, cur)
        orders.append({"play": play, "end": {"jump": 0}})
    return orders, pats


def _visit(play, pats, pat, rows):
    """One visit of one pattern: its events, kept once and named by what they decode to."""
    key = (pat, tuple((d, tuple(tuple(s) for s in ss)) for d, ss, _e in rows))
    name = pats.get(key)
    if name is None:
        name = pats[key] = len(pats)
    play.append(name)


def patterns_of(pats):
    return {
        str(name): {
            "events": [
                {
                    "dur": d,
                    "sounds": False,
                    "note": None,
                    "gate": None,
                    "tie": False,
                    "ins": None,
                    "arm": {"rows": [{"sets": [list(s) for s in ss]}]},
                }
                for d, ss in rows
            ]
        }
        for (_p, rows), name in pats.items()
    }
