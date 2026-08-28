"""T3 -- the sound half as data: producers under events and conditions over cells.

The certified tick outside the fetch regions is inlined into one ranked list of
stores. Each store becomes a *producer* -- a register or cell written under its
control dependences -- when every guard is a condition over cells the data alone sets, and its value
reads only such cells,
the image's tables or a fetch's temps. A cell every store
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
from .refuse import Refusal
from .region import _control

DEPTH = 24

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


# ---- the reduction --------------------------------------------------------------
class Lowering:
    """Producers out of the inlined tick: what reduces, and what refuses by name.

    A block runs when one of its forward edges is taken (:mod:`.universal`
    keeps the flag per pass); a ``Let`` is a temp set at its own rank, so a
    value read before a store in the same block stays the value read; a ``Phi``
    picks the argument of the predecessor that ran last; a call's parameters and
    returns are temps at the call and at each exit; a loop is its latch edge.
    """

    def __init__(self, prog, fetch, unit):
        self.prog, self.fetch, self.unit = prog, fetch, unit
        self.refusals = []
        self.items = []
        self.bad = set()  # cell addresses a refused store writes
        self.unbound = set()  # temps a refused let defines, blocks refused
        self.spans = set()  # (lo, hi) envelopes the items read and write
        self.walk = unit.walk()

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
                addr = self.concrete(a)
                if addr is None or any(addr + k in self.bad for k in range(e.w)):
                    return "a read of a cell a refused producer steps ($%04X..$%04X)" % (e.lo, e.hi)
            self.spans.add((e.lo, e.hi))
            return ["mem", a, e.w]
        return "unsupported %s" % t.__name__

    @staticmethod
    def concrete(a):
        """The one address a constant expression reads, or ``None``."""
        try:
            return evaldata(a, {})
        except _Unevaluable:
            return None

    # ---- edges ------------------------------------------------------------------
    def edge(self, p, k):
        """The guard on edge ``k`` out of block ``p``: its branch as data, or a refusal."""
        if k < 0:
            return []
        if type(p.term) is If:
            cond, truth = self.expr(p.term.c), 1 if k == 0 else 0
            if isinstance(cond, str):
                raise Refusal(
                    "guard not in IR",
                    "%s:%s" % (p.proc, p.label),
                    "$%04X" % self.prog.procs[p.proc].blocks[p.label].src,
                    cond,
                )
            return [["cond", cond, truth]]
        if type(p.term) is Switch:
            cond = self.expr(p.term.e)
            if isinstance(cond, str) or k >= len(p.term.cases):
                return "a dispatch the data cannot decide at %s:%s" % (p.proc, p.label)
            return [["cond", ["bin", "==", cond, ["k", p.term.cases[k][0]], 1], 1]]
        return []

    def block(self, u):
        """The block item: one alternative per forward edge in, and the fetches resuming here."""
        alts = []
        for puid, k in u.preds:
            p = self.unit.blocks[puid]
            if p.inregion:
                continue
            if puid in self.unbound:
                return None, "reached only through a refused block"
            try:
                g = self.edge(p, k)
            except Refusal as r:
                return None, r
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
                try:
                    g = self.edge(p, k)
                except Refusal as r:
                    return None, r
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
        self.spans.add((s.lo, s.hi))
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
                if isinstance(why, Refusal):
                    self.refusals.append(why)
                elif type(s) is Store:
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
                try:
                    g = self.edge(p, succs(p.term).index(hl))
                except Refusal:
                    g = []
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
    if k == "byte":  # a score byte: the row's, at the cursor's position plus an offset
        return env["byte"](e[1], evaldata(e[2], env, mem, tmps) + e[3])
    if k == "sel":
        for when, v in e[1]:
            if all(holds(g, env, mem, tmps) for g in when):
                return evaldata(v, env, mem, tmps)
        raise _Unevaluable()
    raise _Unevaluable()


def holds(g, env, mem=None, tmps=None):
    """One guard: ``["cond", expr, truth]``."""
    _c, x, truth = g
    return (evaldata(x, env, mem, tmps) != 0) == bool(truth)
