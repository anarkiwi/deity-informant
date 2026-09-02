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
        self.marks = {id(e): k for k, e in marks}
        self.runs, self.trips = {}, {}
        self.seen = set()

    def ev(self, e, F):
        i = id(e)
        k = self.resets.get(i)
        if k is not None:
            self.runs[k] = 0
        k = self.track.get(i)
        if k is not None:
            self.runs[k] = self.runs.get(k, 0) + 1
            self.trips[k] = max(self.trips.get(k, 0), self.runs[k])
        k = self.marks.get(i)
        if k is not None:
            self.seen.add(k)
        return super().ev(e, F)

    def _begin(self, key, region, F):
        self.seen = set()
        super()._begin(key, region, F)

    def _end(self, F, prev, to, rets=None):
        self.rec["seen"] = sorted(self.seen)
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


def run(prog, proc, groups, ticks, inputs=None, envvars=None, loops=(), marks=()):
    """``(recorder, fetches)``: one horizon recorded over the given block groups."""
    F = fetch_of(prog, proc, groups)
    R = Recorder(prog, F, inputs, envvars=envvars, track=loops, marks=marks).run_init()
    obs, trap = R.render(ticks)
    return R, R.fetches, trap, obs


def voice_name(records, names, voices):
    """The name the fetch's own environment carries the voice in.

    The voice loop leaves several names for its index and the score needs the one
    the fetch itself carries: every voice, and no value that is not one.
    """
    for n in names:
        got = [r["env"].get(n) for r in records]
        if got and len(set(got)) == voices and all(v in range(voices) for v in got):
            return n
    return None


def score_of(records, low, vvar, ordernames, tempo, voices, ordpos=None, keep=None):
    """The score the fetches read: per-voice orders of patterns of events.

    A visit ends where the fetch stepped the *order* cursor T2 named -- which is
    the pattern's own end, and the only place the score's shape comes from.
    """
    rows = {v: [] for v in range(voices)}
    for got in records:
        v = got["env"].get(vvar)
        if v is None or v not in rows:
            continue
        sets = [
            ["@" + low.temps[n], int(got["temps"][n])]
            for n in got["seen"]
            if n in low.temps and n in got["temps"] and (keep is None or low.temps[n] in keep)
        ]
        pat = next((int(got["temps"][n]) for n in got["seen"] if n in ordernames), 0)
        dur = next((c[2] for c in got["cmds"] if c[0] == "ram" and c[1] == tempo + v), 0)
        ends = ordpos is not None and any(c[0] == "ram" and c[1] == ordpos + v for c in got["cmds"])
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
