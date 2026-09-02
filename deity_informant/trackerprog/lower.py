"""B7 -- the certified tick lowered into section 3.3 rows of ``sets``.

One block of the S4 IR becomes one guarded row: its guard path is the row's
``when``, its statements the row's ``sets`` in order, and every SSA temp a named
cell. Nothing is classified here; a leaf the vocabulary has no name for is
refused, and the score supplies the bytes a fetch read.
"""

from __future__ import annotations

from ..tuneprog.accguard import guardpath
from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo, succs
from ..tuneprog.ir import Bin, Const, If, Let, Load, Store, Var, evalbin
from ..tuneprog.irwalk import addr_split
from .cells import ident

FLAG, RESET = "!flag", "!reset"  # a join's own cell: the row that raises it, and its reset
MASK = {1: 0xFF, 2: 0xFFFF}
BINOP = {"&": "and", "|": "or", "^": "xor", "+": "add", "-": "sub", ">>": "shr"}
CMP = {"==": "==", "!=": "!=", "<": "<"}
NEG = {"==": "!=", "!=": "==", "<": ">="}
DEPTH = 8


class Unlowerable(Exception):
    """A leaf the object's own vocabulary has no name for."""


def masked(node, w):
    """A value held to the width the machine's own arithmetic gives it."""
    m = MASK.get(w, 0xFFFF)
    return node & m if isinstance(node, int) else {"and": [node, m]}


def _bitof(m):
    return m.bit_length() - 1 if m and not m & (m - 1) else None


def _truth(a, b, op, w, e):
    """A comparison in a value position: the chip's own zero test, or one bit of a mask."""
    if b == 0 and type(e.a) is Bin and e.a.op == "&" and type(e.a.b) is Const:
        k = _bitof(e.a.b.v)
        if k is not None and isinstance(a, dict) and "and" in a:
            got = {"bit": [a["and"][0], k]}
            return got if op == "!=" else {"xor": [got, 1]}
    d = {"sub": [0, masked({"sub": [a, b]}, w)]}
    return {"carry_out": [d, 8 * w]} if op == "!=" else {"borrow_out": [d, 8 * w]}


def _shl(node, k, w):
    """``x << k`` as one node: the operand is named once, never doubled ``k`` times."""
    k = int(k)
    if not k:
        return node
    return masked(node << k if isinstance(node, int) else {"shl": [node, k]}, w)


def _fold(ctxs):
    """Two guards that differ in one term and its negation are the guard without it."""
    out = list(ctxs)
    for _ in range(len(out) * len(out) + 1):
        for i, (g, x) in enumerate(out):
            got = next(
                (j for j in range(i + 1, len(out)) if x == out[j][1] and _pair(g, out[j][0])), None
            )
            if got is None:
                continue
            drop = set(g) - set(out[got][0])
            out = [y for k, y in enumerate(out) if k not in (i, got)]
            out.append((tuple(t for t in g if t not in drop), x))
            break
        else:
            return out
    return out


def _pair(a, b):
    """Whether two guards differ in exactly one term, and it is the same condition."""
    x, y = set(a) - set(b), set(b) - set(a)
    if len(x) != 1 or len(y) != 1:
        return False
    (_d1, c1, t1), (_d2, c2, t2) = next(iter(x)), next(iter(y))
    return c1 is c2 and t1 != t2


def reaching(p, order, vidx=frozenset()):
    """``{label: {address: {expressions}}}``: the ram stores one base address has."""
    gen = {}
    for lbl, b in p.blocks.items():
        d = {}
        for s in b.stmts:
            if type(s) is Store and s.cls == "ram":
                base, idx = addr_split(s.a)
                if base is not None and (idx is None or (type(idx) is Var and idx.n in vidx)):
                    d[base] = {s.v}
        gen[lbl] = d
    inn = {lbl: {} for lbl in p.blocks}
    for _ in range(len(p.blocks) + 1):
        moved = False
        for lbl in order:
            out = {}
            for pr, b in p.blocks.items():
                if lbl not in succs(b.term):
                    continue
                d = dict(inn[pr])
                d.update(gen[pr])
                for a, vs in d.items():
                    out[a] = out.get(a, set()) | vs
            if out != inn[lbl]:
                inn[lbl], moved = out, True
        if not moved:
            break
    return inn


class Lower:
    """The lowering of one procedure's blocks into guarded rows of ``sets``."""

    def __init__(self, prog, proc, cells, vocab):
        self.prog, self.proc, self.cells, self.v = prog, prog.procs[proc], cells, vocab
        self.g = cfg(self.proc)
        self.loops = natural_loops(self.g, idoms(self.proc, self.g), preds_of(self.proc))
        self.guards = guardpath(self.proc, sites=True)
        self.rpo = list(rpo(self.proc, self.g))
        seen = {}
        for b in self.proc.blocks.values():
            for s in b.stmts:
                if type(s) is Let:
                    seen[s.n] = None if s.n in seen else s.e
        self.assigned = frozenset(seen)
        self.defs = {n: e for n, e in seen.items() if e is not None}
        self.reach = reaching(self.proc, self.rpo, vocab.vidx)
        self.preds = preds_of(self.proc)
        self.temps, self.wide, self.bad = {}, set(), set()
        self.lbl, self.gate, self.local, self.scope = None, frozenset(), {}, frozenset()
        # the conditions the schedule states outright: a divider's own compare,
        # which ``meta.tempo``'s rate and phase say once for the whole tune
        self.stated = frozenset()
        self.written = self._written()

    def _written(self):
        """Every address the play stores to: what the image cannot be folded at."""
        out = []
        for b in self.proc.blocks.values():
            for s in b.stmts:
                if type(s) is Store and s.cls in ("ram", "raw"):
                    out.append((s.lo, s.hi + s.w))
        return tuple(out)

    def frozen(self, addr, w):
        """Whether the play never writes a word: the image states it once and for all."""
        return not any(lo < addr + w and addr < hi for lo, hi in self.written)

    def temp(self, n, w=1):
        """One SSA name as a cell of the object: one copy per voice."""
        c = self.temps.get(n)
        if c is None:
            c = self.temps[n] = "t" + ident(n)
            self.cells.declare(c, None)
        if w == 2:
            self.wide.add(c)
        return c

    def expand(self, e, depth=DEPTH):
        """One expression with its SSA names and single reaching stores substituted."""
        if depth <= 0:
            return e
        t = type(e)
        if t is Var and e.n in self.local:
            return Const(self.local[e.n], e.w)
        if t is Var and (e.n in self.v.supplied or e.n in self.v.subst):
            return e
        if t is Var and e.n not in self.v.vidx and e.n in self.defs:
            return self.expand(self.defs[e.n], depth - 1)
        if t is Load and e.cls == "ram":
            base, idx = addr_split(e.a)
            ok = idx is None or (type(idx) is Var and idx.n in self.v.vidx)
            if base is not None and ok:
                vs = self.reach.get(self.lbl, {}).get(base)
                if vs and len(vs) == 1:
                    return self.expand(next(iter(vs)), depth - 1)
        if t is Bin:
            a, b = self.expand(e.a, depth - 1), self.expand(e.b, depth - 1)
            if type(a) is Const and type(b) is Const:
                return Const(evalbin(e.op, a.v, b.v, e.w or 1), e.w)
            return Bin(e.op, a, b, e.w)
        if t is Load and e.cls == "ram":
            a = self.expand(e.a, depth - 1)
            if type(a) is Const and self.frozen(a.v, e.w):
                return Const(int.from_bytes(self.v.img[a.v : a.v + e.w], "little"), e.w)
        return e

    def isvoice(self, e):
        """Whether an index expression is the voice the tick is committing."""
        x = self.expand(e)
        return type(x) is Var and x.n in self.v.vidx

    # ---- expressions -------------------------------------------------------------
    def value(self, e):
        t = type(e)
        if t is Const:
            return e.v
        if t is Var:
            if e.n in self.local:
                return self.local[e.n]
            got = self.v.subst.get(e.n)
            if got is not None:
                return got
            if e.n in self.v.vidx:
                return {"cell": "voice_index"}
            if e.n in self.v.supplied or e.n in self.assigned:
                return {"cell": self.temp(e.n, e.w)}
            raise Unlowerable(e.n)
        if t is Load:
            return self.v.load(self, e)
        if t is Bin:
            return self.binop(e)
        raise Unlowerable(repr(e))

    def binop(self, e):
        op, w = e.op, e.w
        if op == "<<":
            k = self.expand(e.b)
            if type(k) is not Const:
                raise Unlowerable("variable shift left")
            return _shl(self.value(e.a), k.v, w)
        a, b = self.value(e.a), self.value(e.b)
        if op == "carry":
            return {"carry_out": [{"add": [a, b]}, 8 * w]}
        if op == "<":
            return {"carry_out": [{"sub": [a, b]}, 8 * w]}
        if op == "<=":
            return {"borrow_out": [{"sub": [b, a]}, 8 * w]}
        if op in ("==", "!="):
            return _truth(a, b, op, w, e)
        if op in BINOP:
            node = {BINOP[op]: [a, b]}
            return node if op in ("&", "|", "^", ">>") else masked(node, w)
        raise Unlowerable(op)

    # ---- guards -------------------------------------------------------------------
    def term(self, c, truth):
        """One guard term: a comparison of the object's own, or a value against zero."""
        if type(c) is Bin and c.op in CMP:
            op = CMP[c.op] if truth else NEG[CMP[c.op]]
            return [self.value(c.a), op, self.value(c.b)]
        if type(c) is Bin and c.op == "<=":
            return [self.value(c.b), ">=" if truth else "<", self.value(c.a)]
        return [self.value(c), "!=" if truth else "==", 0]

    def guard(self, c, t):
        """One term of the *schedule's* own guard: the cells it reads, not the temps.

        A row's guard is read where the row runs and may name the temp a block
        left; the clock's is read before any phase of the tick has run, so every
        name in it is expanded to the cell it reads.
        """
        self.lbl, self.local = None, {}
        return self.term(self.expand(c), t)

    def guard_value(self, e):
        """One value the schedule states, read the same way as its guards."""
        self.lbl, self.local = None, {}
        return self.value(self.expand(e))

    def onpath(self, d, c, t):
        """Whether the row states this term, or the schedule already does (B6).

        Control dependence is sound where dominance is not, so a term decided in
        another phase is still the row's -- except two the schedule states: the
        guard the phase itself runs under (``meta.tempo.boundary``, the row's),
        and the divider's own compare, which ``rate`` and ``phase`` spend.
        """
        if (id(c), t) in self.gate:
            return False
        return d in self.scope or id(c) not in self.stated

    def when(self, lbl, extra=(), guard=None):
        out = []
        got = guard if guard is not None else [x[:3] for x in self.guards.get(lbl, ())]
        for d, c, t in got:
            if not self.onpath(d, c, t):
                continue
            out.append(self.term(c, t))
        return out + [list(x) for x in extra]

    # ---- the guard a join stands under ----------------------------------------------
    def _edge(self, q, lbl):
        """The term the edge from ``q`` to ``lbl`` decides, where it decides one."""
        term = self.proc.blocks[q].term
        if type(term) is not If or term.t == term.f:
            return ()
        return ((q, term.c, lbl == term.t),)

    def _own(self, lbl):
        """One block's own guard path, as the terms a row states."""
        return tuple((d, c, t) for d, c, t, _w in self.guards.get(lbl, ()))

    def plan(self, blocks):
        """``({block: (guard, extra)}, {block: [(flag, guard, extra)]})`` (B7).

        Control dependence says a block runs *only if* an edge was taken; it does
        not say the block is reached no other way, so the guard path of a block a
        join carries is one path's.  The reaching condition is a **disjunction**,
        which the one guard shape of section 3.3 cannot state, and two paths that
        differ in one term and its negation are the one path that term does not
        decide -- a diamond folds, a join a path *leaves* does not.  What does not
        fold the object states as a cell: every path that reaches the block raises
        it where that path already stands, and the block's own guard reads it.
        """
        eff, rows = {}, {}
        for lbl in self.rpo:
            if lbl not in blocks:
                continue
            ps = [q for q in sorted(self.preds.get(lbl, ())) if q in eff]
            got, ctx = [], {}
            for q in ps:
                ctx[q] = (tuple(dict.fromkeys(eff[q][0] + self._edge(q, lbl))), eff[q][1])
                if ctx[q] not in got:
                    got.append(ctx[q])
            got = _fold(got)
            if len(got) < 2:  # one path, and the guard path it carries is the row's
                eff[lbl] = (self._own(lbl), got[0][1] if got else ())
                continue
            name = "j" + ident(lbl)
            self.cells.declare(name, None)
            for q in ps:
                rows.setdefault(q, []).append((name, ctx[q]))
            eff[lbl] = ((), (({"cell": name}, "!=", 0),))
        return eff, rows

    # ---- statements ----------------------------------------------------------------
    def row(self, lbl, extra=(), local=None, guard=None):
        """One block as a guarded row, and the accumulator stores it must be split at."""
        self.lbl, self.local = lbl, dict(local or {})
        out, parts = [], []
        for s in self.proc.blocks[lbl].stmts:
            got = self.one(s)
            if got is None:
                continue
            if len(got) == 4:  # an accumulator's own store: the row is split at it
                parts.append((out, got))
                out = []
            else:
                out.append(got)
        parts.append((out, None))
        when = self.when(lbl, extra, guard)
        return when, parts

    def one(self, s):
        """One statement: a temp, a cell, a register, or an accumulator's own store.

        A plain assignment carries the site it moves, which is what B7's
        recognition joins T1's accumulator records to.
        """
        t = type(s)
        self.lbl = self.lbl
        try:
            if t is Let:
                if s.n in self.v.supplied or s.n in self.v.subst:
                    return None
                return ("@" + self.temp(s.n, getattr(s.e, "w", 1)), self.value(s.e), None)
            if t is Store:
                got = self.v.target(self, s)
                if got is None:
                    return None
                if got[0] == "acc":
                    return ("acc", got[1], self.value(s.v), s.src)
                return (got[1], self.value(s.v), s.src)
        except Unlowerable as x:
            self.bad.add(s.n if t is Let else "$%04X" % s.src)
            del x
        return None

    # ---- the linear order ------------------------------------------------------------
    def sequence(self, blocks, trips):
        """The blocks in reverse postorder, each inner loop unrolled to its own bound."""
        inner = {h: v for h, v in self.loops.items() if h in blocks and len(v[0]) < len(blocks)}
        body = {l for _h, (b, _x) in inner.items() for l in b}
        eff, flagrows = self.plan(blocks)
        flags = sorted({n for v in flagrows.values() for n, _c in v})
        out = [(RESET, tuple(flags), {}, ())] if flags else []
        for lbl in self.rpo:
            if lbl not in blocks:
                continue
            if lbl in inner:
                out += self._unroll(lbl, inner[lbl], trips.get(lbl, 1))
            elif lbl not in body:
                guard, extra = eff.get(lbl) or (self._own(lbl), ())
                out.append((lbl, extra, {}, guard))
            for name, (guard, extra) in flagrows.get(lbl, ()):
                out.append(((FLAG, name, lbl), extra, {}, guard))
        return out

    def _unroll(self, head, loop, k):
        """One inner loop, unrolled: repetition ``j`` under the edge that continues it."""
        body, latches = loop
        order = [l for l in self.rpo if l in body]
        seen = {(id(c), t) for _d, c, t, _w in self.guards.get(head, ())}
        cont = [
            (c, t)
            for lat in sorted(latches)
            for d, c, t, _w in self.guards.get(lat, ())
            if (id(c), t) not in seen and self.onpath(d, c, t)
        ]
        if not cont:
            t = self.proc.blocks[head].term
            cont = [
                (t.c, truth)
                for lbl, truth in ((t.t, True), (t.f, False))
                if type(t) is If and lbl in body
            ][:1]
        ind = self.induction(head, body, latches)
        keep, out = self.local, []
        for j in range(max(int(k), 1) + 1):
            step = {n: (c + j * d) & 0xFF for n, (c, d) in ind.items()}
            self.local = {n: (c + (j - 1) * d) & 0xFF for n, (c, d) in ind.items()} if j else {}
            terms = () if not j else tuple(tuple(self.term(c, t)) for c, t in cont)
            if j == max(int(k), 1):
                out.append((None, terms, {}, None))
                break
            out += [(l, terms, step, None) for l in order]
        self.local = keep
        return out

    def induction(self, head, body, latches):
        """``{name: (entry value, step)}`` for a loop index the object states outright."""
        out = {}
        for lat in sorted(latches):
            for s in self.proc.blocks[lat].stmts:
                if type(s) is not Let or type(s.e) is not Var:
                    continue
                d = self.defs.get(s.e.n)
                if not (type(d) is Bin and d.op == "+" and type(d.b) is Const):
                    continue
                if type(d.a) is not Var or d.a.n != s.n:
                    continue
                c = self._entry(s.n, body)
                if c is not None:
                    out[s.n] = (c, d.b.v)
        del head
        return out

    def _entry(self, name, body):
        """The constant a loop index enters with, where a chain of copies gives one."""
        for lbl, b in self.proc.blocks.items():
            if lbl in body:
                continue
            for s in b.stmts:
                if type(s) is not Let or s.n != name:
                    continue
                e, seen = s.e, 0
                while type(e) is Var and e.n in self.defs and seen < 8:
                    e, seen = self.defs[e.n], seen + 1
                if type(e) is Const:
                    return e.v
        return None

    def refusals(self):
        return sorted(self.bad)
