"""T2 -- a table read's address as one expression over named cells and copy indices.

A site reads ``T[ptr + cursor]`` where ``ptr`` is scratch a store filled a few
statements earlier and ``cursor`` a name two ``Let`` s define on two paths. The
resolver opens both: a name or a scratch cell is the definition that reaches the
site, and where several reach it on different paths each is one alternative
under the guards of its own block (:class:`Sel`). Callers substitute their
arguments the way :func:`~.tuneprog.accshape.arms` does.
"""

from __future__ import annotations

import networkx as nx

from ..tuneprog.accguard import _domsets, guardpath
from ..tuneprog.graph import cfg, idoms
from ..tuneprog.ir import Bin, Call, Const, Let, Load, Phi, R16, REGVAR, Return, Store, Var, W16
from ..tuneprog.nodes import At, Ret, Sel  # noqa: F401 -- re-exported

DEPTH = 6
MAXALTS = 16


Site = tuple  # (proc, label, statement index)


def _defs(proc):
    """``(lets, mem, rets)``: every definition of a name or a constant-address cell.

    A call's returns are definitions like any other, one per call site, so a name
    two calls return reaches a use as the alternative its own path made.
    """
    lets, mem, rets = {}, {}, set()
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            if type(s) is Call:
                for k, n in enumerate(s.rets):
                    lets.setdefault(n, []).append((lbl, i, Ret(lbl, i, s, k)))
                    rets.add(n)
            elif type(s) is Let:
                lets.setdefault(s.n, []).append((lbl, i, s.e))
            elif type(s) is Phi:
                lets.setdefault(s.n, []).append((lbl, i, s))
            elif type(s) is Store and s.r >= 0 and s.w == 1 and type(s.a) is Const:
                mem.setdefault((s.r, s.a.v), []).append((lbl, i, s.v))
            elif type(s) is W16 and type(s.a) is Const:
                for cell, shift in ((tuple(s.lo), 0), (tuple(s.hi), 8)):
                    half = (
                        Bin(">>", s.e, Const(shift, 1), 1)
                        if shift
                        else Bin("&", s.e, Const(255), 1)
                    )
                    mem.setdefault(cell, []).append((lbl, i, half))
    return lets, mem, rets


class Resolver:
    """Reaching definitions of one procedure, with guarded alternatives."""

    def __init__(self, ctx, proc, mark=False):
        self.ctx, self.name, self.mark = ctx, proc, mark
        self.proc = ctx.prog.procs[proc]
        self.g = cfg(self.proc)
        self.dom = _domsets(idoms(self.proc, self.g), self.proc.blocks)
        self.sites = guardpath(self.proc, sites=True)
        self.inloop = ctx.inloop(proc)
        self.lets, self.mem, self.rets = _defs(self.proc)
        self.program = None
        fwd = self.g.copy()  # a definition reaches a use forward, never round a back edge
        fwd.remove_edges_from((l, h) for h, (_b, ls) in ctx.loops(proc).items() for l in ls)
        self.reach = {lbl: set(nx.descendants(fwd, lbl)) for lbl in self.proc.blocks}

    def alts(self, defs, lbl, idx):
        """The definitions reaching ``(lbl, idx)``: ``[(guards, expr)]``, default first."""
        here = [(i, e) for l, i, e in defs if l == lbl and i < idx]
        if here:
            return [((), max(here)[1])]
        chain = self.dom[lbl]
        by = {}
        for l, i, e in defs:
            if l != lbl and lbl in self.reach.get(l, ()):
                by.setdefault(l, []).append((i, e))
        dominating = [l for l in chain[1:] if l in by]
        out = []
        if dominating:
            top = dominating[0]
            out.append(((), max(by.pop(top))[1]))
            by = {l: v for l, v in by.items() if top in self.dom[l]}
        elif lbl in by and self.inloop.get(lbl):
            by.pop(lbl)
        rest = sorted(by, key=lambda l: len(self.dom[l]))
        out += [(self.sites.get(l, ()), max(by[l])[1]) for l in rest]
        return out

    def guard(self, d, c, t, w, depth, seen=frozenset()):
        """One condition opened where its branch decided it: the end of block ``d``.

        The deciding block's own stores are opened into the condition, so the cells
        they read are last tick's too: those regions join the ones written after.
        The fourth element names the decider, for a reader that ranks it in the tick.
        """
        b = self.proc.blocks[d]
        here = frozenset(
            r
            for s in b.stmts
            for r in ((s.lo[0], s.hi[0]) if type(s) is W16 else (s.r,) if type(s) is Store else ())
            if r >= 0
        )
        return self.open(c, d, len(b.stmts), depth, seen), t, frozenset(w) | here, (self.name, d)

    def guards(self, lbl, depth=DEPTH):
        """The site's own guard path, each condition opened at its deciding block."""
        return tuple(self.guard(d, c, t, w, depth) for d, c, t, w in self.sites.get(lbl, ()))

    def loopvar(self, n, lbl, idx):
        """True for a name a loop carries round to the use: the copy index, left free.

        A definition reaching the use only round a back edge is carried unless a
        definition inside the same loop body dominates the use, which kills it.
        """
        here = self.inloop.get(lbl, frozenset())
        defs = self.lets.get(n) or ()
        if any(l == lbl and i < idx for l, i, _e in defs):
            return False
        chain = self.dom[lbl]
        for l, _i, _e in defs:
            if l != lbl and l in chain and here & self.inloop.get(l, frozenset()):
                return False
        for l, i, _e in defs:
            back = lbl not in self.reach.get(l, ()) if l != lbl else i >= idx
            if back and here & self.inloop.get(l, frozenset()):
                return True
        return False

    def entry(self, e, lbl, depth, seen):
        """A loop-carried name as the value it entered the loop with, or the name.

        A counter whose entry value is a constant is the loop's own index and stays
        free, to be bound per copy; a value the loop refines from a cell's reading
        (a cursor stepped past skipped entries) is that reading.
        """
        here = self.inloop.get(lbl, frozenset())
        for l in self.dom[lbl]:
            if here & self.inloop.get(l, frozenset()):
                continue
            got = [(i, x) for d, i, x in self.lets[e.n] if d == l and type(x) is not Phi]
            if got:
                i, x = max(got)
                if type(x) is Const or x is None:
                    return e
                return self.open(x, l, i, depth - 1, seen | {(e.n, (l, i))})
        return e

    def open(self, e, lbl, idx, depth=DEPTH, seen=frozenset()):
        """``e`` with every reaching name and scratch cell substituted, ``depth`` deep."""
        t = type(e)
        if t is Ret:
            if self.program is None:
                return Var(e.call.rets[e.k])
            return self.program.returned(self, e.lbl, e.i, e.call, e.k, depth - 1, seen)
        if depth <= 0:
            return e
        if t is Var:
            if e.n not in self.lets:
                return e
            if self.loopvar(e.n, lbl, idx):
                return self.entry(e, lbl, depth, seen)
            return self.select(self.lets[e.n], lbl, idx, depth, seen, e.n, e)
        if t is Bin:
            return Bin(
                e.op,
                self.open(e.a, lbl, idx, depth, seen),
                self.open(e.b, lbl, idx, depth, seen),
                e.w,
            )
        if t is Load:
            cell = (e.r, e.a.v) if type(e.a) is Const else None
            if e.w == 1 and cell in self.mem:
                return self.select(self.mem[cell], lbl, idx, depth, seen, cell, e)
            return Load(e.cls, self.open(e.a, lbl, idx, depth, seen), e.w, e.lo, e.hi, e.r)
        if t is R16:
            lo, hi = tuple(e.lo), tuple(e.hi)
            if lo in self.mem and hi in self.mem:
                l = self.select(self.mem[lo], lbl, idx, depth, seen, lo, _byte(lo))
                h = self.select(self.mem[hi], lbl, idx, depth, seen, hi, _byte(hi))
                return Bin("|", l, Bin("<<", h, Const(8, 1), 2), 2)
            return R16(e.lo, e.hi, self.open(e.a, lbl, idx, depth, seen))
        if t is Sel:
            return Sel(tuple((gs, self.open(x, lbl, idx, depth, seen)) for gs, x in e.alts))
        if t is At:
            return At(self.open(e.e, e.site[1], e.site[2], depth, seen), e.site, e.via)
        return e

    def at(self, x, lbl, idx):
        """``x`` marked with the site it was opened at, when the resolver marks sites."""
        return At(x, (self.name, lbl, idx)) if self.mark else x

    def select(self, defs, lbl, idx, depth, seen, key, orig):
        """One name's reaching definitions, each opened at its own block.

        ``seen`` holds the definitions on the path being opened: a definition met
        again is a cycle and stays a name, while the same cell read at another site
        opens to whatever reaches that site. With no definition reaching the site
        the value is ``orig`` -- the tick came in with it -- and where only guarded
        ones reach, ``orig`` is the default they override.
        """
        got = self.alts(defs, lbl, idx)
        if not got or len(got) > MAXALTS:
            return orig
        if got[0][0] and type(got[0][1]) is not Phi:
            # a name's definitions are exhaustive (SSA); a cell's need not be, and the
            # value the tick came in with is then the default
            got = [((), got[0][1])] + got[1:] if isinstance(key, str) else [((), None)] + got
        opened = []
        for gs, e in got:
            if e is None:
                opened.append(((), orig))
                continue
            at = self._where(defs, e)
            if (key, at) in seen:
                return orig
            inner = seen | {(key, at)}
            if type(e) is Phi:
                opened += self.phi(e, at, gs, depth, inner)
                continue
            x = self.at(self.open(e, at[0], at[1], depth - 1, inner), *at)
            gs = tuple(self.guard(d, c, t, w, depth - 2, inner) for d, c, t, w in gs)
            opened.append((gs, x))
        return opened[0][1] if len(opened) == 1 else Sel(tuple(opened))

    def phi(self, e, at, gs, depth, seen):
        """A join's alternatives: each argument opened at the end of its own predecessor."""
        out = []
        for pred, arg in e.args.items():
            b = self.proc.blocks.get(pred)
            if b is None:
                return [((), Var(e.n))]
            n = len(b.stmts)
            x = self.at(self.open(arg, pred, n, depth - 1, seen), pred, n)
            pg = () if not out else gs + tuple(self.sites.get(pred, ()))
            pg = tuple(self.guard(d, c, t, w, depth - 2, seen) for d, c, t, w in pg)
            out.append((pg, x))
        return out

    @staticmethod
    def _where(defs, e):
        return next((l, i) for l, i, x in defs if x is e)


def _byte(cell):
    """One half of a pair as its own byte read."""
    return Load("ram", Const(cell[1], 2), 1, cell[1], cell[1], cell[0])


def walkx(e, guards=True):
    """:func:`~.tuneprog.irwalk.walk` through :class:`Sel` nodes, guards included."""
    stack = [e]
    while stack:
        x = stack.pop()
        yield x
        t = type(x)
        if t is Sel:
            for gs, y in x.alts:
                stack.append(y)
                if guards:
                    stack.extend(c for c, *_r in gs)
        elif t is Bin:
            stack += [x.a, x.b]
        elif t is Load or t is R16:
            stack.append(x.a)
        elif t is At:
            stack.append(x.e)


def free(e, guards=True):
    """The names an opened expression still reads: copy indices and callers' registers.

    ``guards=False`` leaves the alternatives' conditions out: a name only a guard
    reads does not bind a copy, it makes the guard unread.
    """
    return {x.n for x in walkx(e, guards) if type(x) is Var}


def _subst(e, fn):
    """:func:`~.tuneprog.irwalk.sub_expr` through :class:`Sel` nodes."""
    if type(e) is Sel:
        return Sel(
            tuple((tuple((_subst(c, fn), *r) for c, *r in gs), _subst(x, fn)) for gs, x in e.alts)
        )
    if type(e) is At:
        return At(_subst(e.e, fn), e.site, e.via)
    if type(e) is Bin:
        return fn(Bin(e.op, _subst(e.a, fn), _subst(e.b, fn), e.w))
    if type(e) is Load:
        return fn(Load(e.cls, _subst(e.a, fn), e.w, e.lo, e.hi, e.r))
    if type(e) is R16:
        return fn(R16(e.lo, e.hi, _subst(e.a, fn)))
    return fn(e)


class Program:
    """Every procedure's :class:`Resolver`, and the callers' substitution across them."""

    def __init__(self, ctx, mark=False):
        self.ctx, self.mark = ctx, mark
        self.res = {}
        self.callers = {}
        for n, p in ctx.prog.procs.items():
            for lbl, b in p.blocks.items():
                for i, s in enumerate(b.stmts):
                    if type(s) is Call:
                        self.callers.setdefault(s.proc, []).append((n, lbl, i, s))

    def of(self, proc):
        if proc not in self.res:
            self.res[proc] = Resolver(self.ctx, proc, self.mark)
            self.res[proc].program = self
        return self.res[proc]

    def returned(self, r, lbl, idx, call, k, depth, seen):
        """A call's ``k``-th return: the callee's return values, one alternative per exit.

        The callee's parameters are the call's arguments, opened at the call site;
        each exit's value is opened at its own block under that block's guards.
        """
        q = self.ctx.prog.procs.get(call.proc)
        if q is None or depth <= 0 or k >= len(q.rets):
            return Var(call.rets[k])
        i = q.rets[k]
        args = {REGVAR[p]: r.open(a, lbl, idx, depth, seen) for p, a in zip(q.params, call.args)}
        fn = _renamer(args)
        qr, out = self.of(call.proc), []
        for qlbl, b in q.blocks.items():
            if type(b.term) is not Return or len(b.term.vals) <= q.rets.index(i):
                continue
            n = len(b.stmts)
            x = qr.open(b.term.vals[q.rets.index(i)], qlbl, n, depth - 1, seen)
            if self.mark:
                x = At(x, (call.proc, qlbl, n), (r.name, lbl, idx))
            gs = () if not out else qr.guards(qlbl, depth - 2)
            out.append((tuple((_subst(c, fn), t, w, *d) for c, t, w, *d in gs), _subst(x, fn)))
        if not out:
            return Var(call.rets[k])
        return out[0][1] if len(out) == 1 else Sel(tuple(out))

    def resolve(self, proc, lbl, idx, e, depth=3):
        """``[(guards, expr)]``: ``e`` at a site, once per caller path that binds its parameters."""
        r = self.of(proc)
        x = r.open(e, lbl, idx)
        gs = r.guards(lbl)
        params = {REGVAR[i] for i in r.proc.params}
        want = free(x) & params
        calls = self.callers.get(proc) or ()
        if not want or not depth or not calls:
            return [(gs, x)]
        out = []
        for cproc, clbl, cidx, call in calls:
            args = dict(zip([REGVAR[i] for i in r.proc.params], call.args))
            fn = _renamer(args)
            for cgs, cx in self.resolve(cproc, clbl, cidx, _subst(x, fn), depth - 1):
                out.append(
                    (
                        cgs
                        + tuple(
                            (self._open_g(cproc, clbl, cidx, _subst(c, fn)), t, w, *d)
                            for c, t, w, *d in gs
                        ),
                        cx,
                    )
                )
        return out or [(gs, x)]

    def _open_g(self, proc, lbl, idx, c):
        return self.of(proc).open(c, lbl, idx)


def _renamer(sub):
    def fn(e):
        return sub.get(e.n, e) if type(e) is Var else e

    return fn
