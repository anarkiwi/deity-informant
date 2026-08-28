"""T3 -- the sound half as data: producers under events and conditions over cells.

The certified tick outside the fetch regions is inlined into one ranked list of
stores. Each store becomes a *producer* -- a register or cell written under its
control dependences -- when every guard is an event of the score (a row, or
``k`` ticks after or before one, read off the oracle's branch outcomes) or a
condition over cells the data alone sets, and its value reads only such cells,
the image's tables, the voice index or a fetch's temps. A cell every store
into which reduces is *latched*; a cell only the fetches set is a *command*
cell; a cell the tick steps from a guard the data cannot express -- a counter,
an accumulator's own recurrence -- is neither, and every producer through it is
a named refusal. Nothing here is verified against the observable.
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo
from ..tuneprog.ir import Bin, Call, Const, If, Let, Load, Phi, REGVAR, Return, Store, Switch, Var
from ..tuneprog.ir import evalbin, succs
from ..tuneprog.irwalk import walk
from .refuse import Refusal
from .region import _control

TABLE = ("const", "init_constant")
MAXK = 4
DEPTH = 24
EVENTS = ("row", "after", "before")

Guard = namedtuple("Guard", "uid edge")  # a control dependence: decider block, edge index


@dataclass
class UBlock:
    """One block of the inlined tick: its statements renamed by call path."""

    uid: int
    proc: str
    label: str
    path: str
    stmts: list
    term: object
    rank: int
    guards: tuple = ()
    preds: tuple = ()  # (uid, edge index) forward edges in
    region: object = None
    inregion: bool = False
    loops: tuple = ()  # loop ids whose body holds it
    calls: dict = field(default_factory=dict)  # stmt index -> (callee ublocks, rets)


class Unit:
    """The tick inlined: every block once per call path, in the order a tick runs them."""

    def __init__(self, prog, fetch):
        self.prog, self.fetch = prog, fetch
        self.blocks = []
        self.by = {}
        self.loops = {}  # loop id -> (header uid, latch uids, body uids)
        self.ret = {}  # renamed return name -> [(exit uid, expr)]
        self.params = {}  # renamed param name -> expr at the call site
        self.entry, _exits = self.inline(prog.meta["tick_proc"], "", ())

    def rename(self, e, path):
        t = type(e)
        if t is Var:
            return Var(path + e.n, e.w)
        if t is Bin:
            return Bin(e.op, self.rename(e.a, path), self.rename(e.b, path), e.w)
        if t is Load:
            return Load(e.cls, self.rename(e.a, path), e.w, e.lo, e.hi, e.r)
        return e

    def inline(self, name, path, outer, outer_loops=(), caller=None):
        """Add ``name``'s blocks under ``path``; returns ``(entry uid, exit uids)``.

        A callee's entry block is entered by its call: its one edge in is the
        caller block's, unconditional (edge ``-1``).
        """
        p = self.prog.procs[name]
        g = cfg(p)
        preds = preds_of(p)
        ctl = _control(p, g)
        loops = natural_loops(g, idoms(p, g), preds)
        back = {(l, h) for h, (_b, ls) in loops.items() for l in ls}
        uids = {}
        order = rpo(p, g)
        for lbl in order:
            b = p.blocks[lbl]
            u = UBlock(len(self.blocks), name, lbl, path, [], b.term, 0)
            uids[lbl] = u.uid
            self.blocks.append(u)
            self.by[(name, lbl, path)] = u.uid
        loopids = {}
        for h, (body, latches) in loops.items():
            lid = len(self.loops)
            self.loops[lid] = (
                uids[h],
                frozenset(uids[l] for l in latches),
                frozenset(uids[l] for l in body),
            )
            for l in body:
                loopids.setdefault(l, []).append(lid)
        for lbl in order:
            u = self.blocks[uids[lbl]]
            b = p.blocks[lbl]
            u.guards = tuple(outer) + tuple(Guard(uids[d], k) for d, k in sorted(ctl[lbl]))
            u.preds = tuple(
                (uids[q], succs(p.blocks[q].term).index(lbl))
                for q in preds[lbl]
                if (q, lbl) not in back
            )
            if lbl == p.entry and caller is not None:
                u.preds = ((caller, -1),)
            u.term = self.rename_term(b.term, path)
            u.loops = tuple(outer_loops) + tuple(loopids.get(lbl, ()))
            key = (name, lbl)
            if key in self.fetch.regions:
                u.region = key
            u.inregion = (caller is not None and self.blocks[caller].inregion) or any(
                lbl in r.blocks for r in self.fetch.regions.values() if r.proc == name
            )
            u.rank = len(self.blocks)  # provisional; final ranks assigned by walk()
            for i, s in enumerate(b.stmts):
                t = type(s)
                if t is Let:
                    u.stmts.append(Let(path + s.n, self.rename(s.e, path)))
                elif t is Store:
                    u.stmts.append(
                        Store(
                            s.cls,
                            self.rename(s.a, path),
                            self.rename(s.v, path),
                            s.w,
                            s.lo,
                            s.hi,
                            s.r,
                            s.src,
                        )
                    )
                elif t is Phi:
                    u.stmts.append(
                        Phi(path + s.n, {uids[q]: self.rename(x, path) for q, x in s.args.items()})
                    )
                elif t is Call:
                    sub = "%s%s@%d:" % (path, s.proc, len(self.blocks))
                    q = self.prog.procs[s.proc]
                    for k, a in zip(q.params, s.args):
                        self.params[sub + REGVAR[k]] = self.rename(a, path)
                    entry, exits = self.inline(s.proc, sub, u.guards, u.loops, u.uid)
                    for j, n in enumerate(s.rets):
                        self.ret[path + n] = [(x, vals[j]) for x, vals in exits if j < len(vals)]
                    u.calls[i] = (entry, s.rets)
                    u.stmts.append(Call(s.proc, (), tuple(path + n for n in s.rets)))
                else:
                    u.stmts.append(s)
        exits = [
            (uids[lbl], [self.rename(v, path) for v in p.blocks[lbl].term.vals])
            for lbl in order
            if type(p.blocks[lbl].term) is Return
        ]
        return uids[p.entry], exits

    def rename_term(self, t, path):
        k = type(t)
        if k is If:
            return If(self.rename(t.c, path), t.t, t.f)
        if k is Switch:
            return Switch(self.rename(t.e, path), t.cases, t.default)
        if k is Return:
            return Return(tuple(self.rename(v, path) for v in t.vals))
        return t

    def walk(self):
        """Blocks in the order one tick first runs them, a callee's inside its call.

        A rank is a tuple: a callee block's is the caller block's, the call's
        statement index, then the callee's own order, so items sort as they run.
        """
        out = []

        def visit(uid, base):
            u = self.blocks[uid]
            u.rank = base
            out.append(u)
            for i, (entry, _rets) in sorted(u.calls.items()):
                path = self.blocks[entry].path
                k = 0
                for v in self.blocks[entry:]:
                    if v.path == path:
                        visit(v.uid, base + (i, k))
                        k += 1

        k = 0
        for u in self.blocks:
            if u.path == "":
                visit(u.uid, (k,))
                k += 1
        return out


# ---- the oracle's branch outcomes -------------------------------------------------
def voices_of(log):
    """``{(proc, raw): index}``: the voice a logged value of a watched name means."""
    seen = {}
    for _t, _p, _l, v, _o in log:
        if v is not None and v != -1:
            seen.setdefault(v[0], set()).add(v[1])
    return {(p, raw): i for p, vals in seen.items() for i, raw in enumerate(sorted(vals))}


def outcomes(log, index):
    """``{(proc, label): {(tick, voice): outcome}}`` over the run."""
    out = {}
    for t, p, l, v, o in log:
        vi = index.get(v) if v not in (None, -1) else None
        out.setdefault((p, l), {})[(t, vi)] = o
    return out


def _index_names(s, rgn):
    """The names a statement's state-cell addresses read."""
    exprs = [s.e] if type(s) is Let else [s.a] if type(s) is Store else []
    for e in exprs:
        for x in walk(e):
            if type(x) is Load and x.r in rgn and rgn[x.r].kind == "state":
                yield from (y.n for y in walk(x.a) if type(y) is Var)
    if type(s) is Store:
        yield from (x.n for x in walk(s.a) if type(x) is Var)


def watch_vars(prog):
    """Per proc, the name most of its state-cell addresses read: the copy index."""
    rgn = prog.by_id()
    out = {}
    for name, p in prog.procs.items():
        count = {}
        for b in p.blocks.values():
            for s in b.stmts:
                for n in _index_names(s, rgn):
                    count[n] = count.get(n, 0) + 1
        if count:
            out[name] = max(count, key=count.get)
    return out


def event_series(rows, kind, k):
    """The ticks a predicate holds for each voice: ``{voice: set}``."""
    if kind == "row":
        return {v: set(ts) for v, ts in rows.items()}
    if kind == "after":
        return {v: {t + k for t in ts} for v, ts in rows.items()}
    return {v: {t - k for t in ts} for v, ts in rows.items()}


def classify(series, rows):
    """The event whose truth equals a decider's outcomes on the ticks it ran, or ``None``.

    ``series`` is ``{(tick, voice): outcome}``; a decider outside the voice loop
    is checked against any voice's rows.
    """
    if all(o in (0, 1) for o in series.values()):
        for kind in EVENTS:
            for k in range(1, MAXK + 1) if kind != "row" else (0,):
                ev = event_series(rows, kind, k)
                any_ = set().union(*ev.values()) if ev else set()
                if all(
                    o == int(t in (ev.get(v, ()) if v is not None else any_))
                    for (t, v), o in series.items()
                ):
                    return ["ev", kind, k]
        if all(o == 1 for o in series.values()):
            return ["k", 1]
        if all(o == 0 for o in series.values()):
            return ["k", 0]
    return None


# ---- the reduction --------------------------------------------------------------
class Lowering:
    """Producers out of the inlined tick: what reduces, and what refuses by name.

    A block runs when one of its forward edges is taken (:func:`~.universal`
    keeps the flag per pass); a ``Let`` is a temp set at its own rank, so a
    value read before a store in the same block stays the value read; a ``Phi``
    picks the argument of the predecessor that ran last; a call's parameters and
    returns are temps at the call and at each exit; a loop is its latch edge.
    """

    def __init__(self, prog, fetch, unit, log, rows, order):
        self.prog, self.fetch, self.unit = prog, fetch, unit
        self.index = voices_of(log)
        self.outcomes = outcomes(log, self.index)
        self.rows = rows
        self.voice_loop, self.order = order
        self.voicevars = self._voicevars()
        self.refusals = []
        self.items = []
        self.bad = set()  # cell addresses a refused store writes
        self.unbound = set()  # temps a refused let defines, blocks refused
        self.walk = unit.walk()

    def _voicevars(self):
        if self.voice_loop is None:
            return set()
        header = self.unit.loops[self.voice_loop][0]
        return {s.n for s in self.unit.blocks[header].stmts if type(s) is Phi}

    # ---- expressions ----------------------------------------------------------
    def expr(self, e, depth=DEPTH):
        """``e`` as data, or a string saying why not."""
        t = type(e)
        if t is Const:
            return ["k", e.v]
        if depth <= 0:
            return "expression too deep"
        if t is Var:
            if e.n in self.unbound:
                return "temp %s is set by no producer" % e.n
            return ["tmp", e.n]
        if t is Bin:
            a, b = self.expr(e.a, depth - 1), self.expr(e.b, depth - 1)
            if isinstance(a, str):
                return a
            if isinstance(b, str):
                return b
            return ["bin", e.op, a, b, e.w or 1]
        if t is Load:
            if e.cls == "io":
                return "a read of $%04X..$%04X: an input" % (e.lo, e.hi)
            a = self.expr(e.a, depth - 1)
            if isinstance(a, str):
                return a
            if any(e.lo <= x <= e.hi for x in self.bad):
                addrs = self.concrete(a)
                if addrs is None or any(x + k in self.bad for x in addrs for k in range(e.w)):
                    return "a read of a cell a refused producer steps ($%04X..$%04X)" % (e.lo, e.hi)
            return ["mem", a, e.w]
        return "unsupported %s" % t.__name__

    def concrete(self, a):
        """The addresses an expression takes over the voices, or ``None`` if it reads more."""
        try:
            return sorted({evaldata(a, {"voice": v}) for v in (self.order or [0])})
        except _Unevaluable:
            return None

    # ---- edges ------------------------------------------------------------------
    def edge(self, p, k):
        """The guard on edge ``k`` out of block ``p``: its branch, as data or an event."""
        if k < 0:
            return []
        if type(p.term) is If:
            cond, truth = self.expr(p.term.c), 1 if k == 0 else 0
            if isinstance(cond, str):
                return self.event(p, truth, cond)
            return [["cond", cond, truth]]
        if type(p.term) is Switch:
            cond = self.expr(p.term.e)
            if isinstance(cond, str) or k >= len(p.term.cases):
                return "a dispatch the data cannot decide at %s:%s" % (p.proc, p.label)
            return [["cond", ["bin", "==", cond, ["k", p.term.cases[k][0]], 1], 1]]
        return []

    def event(self, d, truth, why):
        """A branch the data cannot express, as the score event its outcomes equal."""
        series = self.outcomes.get((d.proc, d.label))
        multi = sum((b.proc, b.label) == (d.proc, d.label) for b in self.unit.blocks) > 1
        ev = classify(series, self.rows) if series and not multi else None
        if ev is None:
            return "branch at %s:%s reduces to no event (%s)" % (d.proc, d.label, why)
        return [["cond", ev, truth]]

    def block(self, u):
        """The block item: one alternative per forward edge in, and the fetches resuming here."""
        alts = []
        for puid, k in u.preds:
            p = self.unit.blocks[puid]
            if p.inregion:
                continue
            if puid in self.unbound:
                return None, "reached only through a refused block"
            g = self.edge(p, k)
            if isinstance(g, str):
                return None, g
            alts.append([puid, g])
        resumes = self.resumes(u)
        if not alts and not resumes and u.uid != self.unit.entry:
            return None, "no edge in"
        return {
            "kind": "block",
            "uid": u.uid,
            "exec": alts,
            "entry": u.uid == self.unit.entry,
        }, None

    def resumes(self, u):
        """The regions a fetch of which resumes at this block."""
        return [
            "%s:%s" % (r.proc, r.entry)
            for r in self.fetch.regions.values()
            if u.proc == r.proc
            and u.label in r.exits
            and any(b.region == (r.proc, r.entry) and b.path == u.path for b in self.unit.blocks)
        ]

    # ---- the pass -----------------------------------------------------------------
    def candidates(self):
        """Every item the tick outside the regions makes, in rank order, unreduced."""
        out = []
        for u in self.walk:
            if u.inregion:
                if u.region is not None:
                    out.append((u, -1, "fetch", None))
                continue
            out.append((u, -1, "block", None))
            for i, s in enumerate(u.stmts):
                t = type(s)
                if t in (Let, Phi, Store):
                    out.append((u, i, "stmt", s))
                elif t is Call:
                    out += [(u, i, "param", na) for na in self.params_of(u.calls[i][0])]
            if type(u.term) is Return:
                out += [(u, len(u.stmts), "ret", nv) for nv in self.returns_of(u)]
        return out

    def returns_of(self, u):
        """``(name, value)`` a return block hands back: the caller's temps, or the tick's."""
        if not u.path:
            return [("$ret%d" % j, val) for j, val in enumerate(u.term.vals)]
        return [
            (n, val) for n, exits in self.unit.ret.items() for xuid, val in exits if xuid == u.uid
        ]

    def params_of(self, entry):
        """``(name, argument)`` of the callee entered at ``entry``."""
        sub = self.unit.blocks[entry].path
        return [
            (n, a)
            for n, a in self.unit.params.items()
            if n.startswith(sub) and n[len(sub) :].count(":") == 0
        ]

    def lower(self, u, i, kind, s):
        """One item as data, or ``(None, why)``."""
        if kind == "block":
            return self.block(u)
        if kind == "fetch":
            r = self.fetch.regions[u.region]
            entry = u
            alts = []
            for puid, k in entry.preds:
                p = self.unit.blocks[puid]
                if puid in self.unbound:
                    return None, "reached only through a refused block"
                g = self.edge(p, k)
                if isinstance(g, str):
                    return None, g
                alts.append([puid, g])
            labels = {
                b.label: b.uid for b in self.unit.blocks if b.path == u.path and b.proc == u.proc
            }
            froms = {l: uid for l, uid in labels.items() if l in r.blocks}
            rets = []
            for b in self.unit.blocks:
                for entry, names in b.calls.values():
                    if self.unit.blocks[entry].path == u.path:
                        rets = [b.path + n for n in names]
            return {
                "kind": "fetch",
                "uid": u.uid,
                "exec": alts,
                "tos": labels,
                "region": "%s:%s" % u.region,
                "tmps": {n: u.path + n for n in r.liveout},
                "froms": froms,
                "rets": rets,
            }, None
        if u.uid in self.unbound:
            return None, "inside a refused block"
        if kind in ("param", "ret"):
            n, a = s
            v = self.expr(a)
            return (
                (None, v) if isinstance(v, str) else ({"kind": "let", "name": n, "value": v}, None)
            )
        if type(s) is Let:
            v = self.expr(s.e)
            return (
                (None, v)
                if isinstance(v, str)
                else ({"kind": "let", "name": s.n, "value": v}, None)
            )
        if type(s) is Phi:
            alts = []
            for puid, arg in s.args.items():
                v = self.expr(arg)
                if isinstance(v, str):
                    return None, v
                alts.append([puid, v])
            return {"kind": "phi", "name": s.n, "alts": alts}, None
        a, v = self.expr(s.a), self.expr(s.v)
        if isinstance(a, str):
            return None, a
        if isinstance(v, str):
            return None, v
        return {
            "kind": "store",
            "pc": "$%04X" % s.src,
            "cls": s.cls,
            "w": s.w,
            "lo": s.lo,
            "hi": s.hi,
            "addr": a,
            "value": v,
        }, None

    def run(self):
        """Fixpoint: a refused item's cells, temps and blocks refuse what reads them."""
        cands = self.candidates()
        while True:
            got = []
            bad, unbound = set(), set()
            for u, i, kind, s in cands:
                item, why = self.lower(u, i, kind, s)
                got.append((u, i, kind, s, item, why))
                if why is None:
                    continue
                if kind in ("block", "fetch"):
                    unbound.add(u.uid)
                elif kind == "stmt" and type(s) is Store:
                    if s.cls != "io":
                        bad |= set(range(s.lo, s.hi + 1))
                elif kind in ("param", "ret"):
                    unbound.add(s[0])
                else:
                    unbound.add(s.n)
            if bad <= self.bad and unbound <= self.unbound:
                break
            self.bad |= bad
            self.unbound |= unbound
        for u, i, kind, s, item, why in got:
            if item is None:
                if type(s) is Store:
                    what = "sid[$%04X]" % s.lo if s.cls == "io" else "$%04X" % s.lo
                    self.refusals.append(
                        Refusal(
                            "command residue" if s.cls == "io" else "unclassified update",
                            what,
                            "$%04X" % s.src,
                            why,
                        )
                    )
                elif kind in ("block", "fetch"):
                    self.refusals.append(
                        Refusal(
                            "unclassified update",
                            "%s:%s" % (u.proc, u.label),
                            "$%04X" % self.prog.procs[u.proc].blocks[u.label].src,
                            why,
                        )
                    )
                continue
            item["rank"] = list(u.rank) + [i]
            item["block"] = u.uid
            item["path"] = u.path
            self.items.append(item)
        self.items.sort(key=lambda x: tuple(x["rank"]))
        return self.items, self.refusals

    def loops(self):
        """``[{header, latches: [[uid, edge]], end}]`` for the player, innermost last."""
        pos = {it["block"]: n for n, it in enumerate(self.items)}
        out = []
        for _lid, (h, latches, body) in self.unit.loops.items():
            ends = [pos[b] for b in body if b in pos]
            if not ends or self.unit.blocks[h].inregion:
                continue  # a loop inside a fetch region is the fetches it recorded
            hl = self.unit.blocks[h].label
            edges = []
            for l in latches:
                p = self.unit.blocks[l]
                g = self.edge(p, succs(p.term).index(hl))
                edges.append([l, [] if isinstance(g, str) else g])
            out.append(
                {
                    "header": h,
                    "latches": edges,
                    "end": max(ends),
                    "body": sorted(body),
                    "size": len(body),
                }
            )
        return sorted(out, key=lambda x: x["size"])


class _Unevaluable(Exception):
    pass


def evaldata(e, env, mem=None, tmps=None):
    """Evaluate a data expression; memory reads need ``mem``."""
    k = e[0]
    if k == "k":
        return e[1]
    if k == "voice":
        return env["voice"]
    if k == "bin":
        return evalbin(e[1], evaldata(e[2], env, mem, tmps), evaldata(e[3], env, mem, tmps), e[4])
    if k == "mem":
        if mem is None:
            raise _Unevaluable()
        a = evaldata(e[1], env, mem, tmps)
        return sum(mem[(a + i) & 0xFFFF] << (8 * i) for i in range(e[2]))
    if k == "tmp":
        if tmps is None or e[1] not in tmps:
            raise _Unevaluable(e[1])
        return tmps[e[1]]
    if k == "sel":
        for when, v in e[1]:
            if all(holds(g, env, mem, tmps) for g in when):
                return evaldata(v, env, mem, tmps)
        raise _Unevaluable()
    raise _Unevaluable()


def holds(g, env, mem=None, tmps=None):
    """One guard: ``["cond", expr | event, truth]``."""
    _c, x, truth = g
    if x[0] == "ev":
        rows = env.get("rows")
        v = env.get("vi")
        ts = rows.get(v, ()) if v is not None else set().union(*rows.values())
        t = env["tick"] - (x[2] if x[1] == "after" else -x[2] if x[1] == "before" else 0)
        got = t in ts
    else:
        got = evaldata(x, env, mem, tmps) != 0
    return got == bool(truth)


def voice_loop(unit, log, index):
    """``(loop id, raw index values in iteration order)`` of the loop that runs the voices."""
    per = {}
    for t, p, l, v, _o in log:
        if t != 0:
            break
        per.setdefault((p, l), []).append(v)
    for lid, (h, _latches, _body) in unit.loops.items():
        u = unit.blocks[h]
        vals = per.get((u.proc, u.label), [])
        raws = [v[1] for v in vals if v not in (None, -1)]
        if len(vals) == 3 and len(set(raws)) == 3:
            return lid, raws
    return None, []


def rows_of(fetches, copyvar):
    """``{voice index: fetch ticks}`` from the recorded fetches."""
    out = {}
    for key, got in fetches.items():
        var, vals = copyvar.get(key, (None, []))
        for f in got:
            v = vals.index(f["env"][var]) if var else None
            out.setdefault(v, set()).add(f["tick"])
    return out
