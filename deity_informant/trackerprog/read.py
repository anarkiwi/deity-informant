"""B7 -- the certified tick read at its own sites: expressions, guards and flow.

The half of the binding that reads: one value or one guard term at the site the
program makes it, expressed over a named cell, an instrument column or a row
fact; the guard path a block stands under, with a join folded back to the path
it had; and the flow facts both need -- which ram stores reach a block, when two
guard paths are one path with a term left out, and the terms a jump table's own
edges decide.  Nothing here lowers a block into rows.
"""

from __future__ import annotations

from ..tuneprog.accguard import _domsets, guardpath
from ..tuneprog.graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of, rpo, succs
from ..tuneprog.ir import Bin, Const, If, Let, Load, Store, Switch, Var, evalbin
from ..tuneprog.irwalk import addr_split, walk
from .cells import ident

MASK = {1: 0xFF, 2: 0xFFFF}
BINOP = {"&": "and", "|": "or", "^": "xor", "+": "add", "-": "sub", ">>": "shr"}
CMP = {"==": "==", "!=": "!=", "<": "<"}
NEG = {"==": "!=", "!=": "==", "<": ">="}
DEPTH = 16


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


def switched(proc, guards):
    """``guards`` with the term each edge of a jump table decides.

    Control dependence over a ``Switch`` is one case: a block one case alone
    reaches stands under the term that case is, and one several reach under none.
    """
    g = cfg(proc)
    ipd = postdoms(g, proc, EXIT)
    pd = _domsets(ipd, [n for n in ipd if n in proc.blocks])
    out = {lbl: list(gs) for lbl, gs in guards.items()}
    for d, b in proc.blocks.items():
        if type(b.term) is not Switch:
            continue
        for s, c in _cases(b.term):
            term = (d, Bin("==", b.term.e, Const(c, 2), 1), True, ())
            for lbl in proc.blocks:
                if s in pd and lbl in pd[s] and lbl not in pd.get(d, ()):
                    out[lbl].append(term)
    return _closed(proc, out)


def _cases(t):
    """``(label, value)`` for each case of a table that reaches its label alone."""
    got = {}
    for c, s in t.cases:
        got.setdefault(s, []).append(c)
    return [(s, cs[0]) for s, cs in sorted(got.items()) if len(cs) == 1]


def edge(term, lbl):
    """The term the edge of a jump table to one label decides, where it decides one."""
    got = [c for s, c in _cases(term) if s == lbl]
    return (Bin("==", term.e, Const(got[0], 2), 1),) if got else ()


def _closed(proc, out):
    """A guard map closed under its own deciders' guards."""
    for _ in range(len(proc.blocks)):
        moved = False
        for lbl, gs in out.items():
            got = list(gs) + [x for d, _c, _v, _w in gs for x in out.get(d, ()) if x not in gs]
            moved = moved or len(got) != len(gs)
            out[lbl] = list(dict.fromkeys(got))
        if not moved:
            break
    return {lbl: tuple(gs) for lbl, gs in out.items()}


def fold(ctxs):
    """Two guards that differ in one term and its negation are the guard without it."""
    out = list(ctxs)
    for _ in range(len(out) * len(out) + 1):
        for i, (g, x) in enumerate(out):
            got = next(
                (j for j in range(i + 1, len(out)) if x == out[j][1] and pair(g, out[j][0])), None
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


def pair(a, b):
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


class Reader:
    """The lowering of one procedure's blocks into guarded rows of ``sets``."""

    def __init__(self, prog, proc, cells, vocab):
        self.prog, self.proc, self.cells, self.v = prog, prog.procs[proc], cells, vocab
        self.g = cfg(self.proc)
        self.loops = natural_loops(self.g, idoms(self.proc, self.g), preds_of(self.proc))
        self.guards = switched(self.proc, guardpath(self.proc, sites=True))
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
        self.scalars = frozenset()  # the names bound outside the voice loop
        self.lbl, self.gate, self.local, self.scope = None, frozenset(), {}, frozenset()
        # the definition a name two blocks bind takes on the path being read
        self.pick = {}
        # a value a block has since stored: read at the row, it is that cell
        self.sub = {}
        self.turn, self.turns = None, {}
        # one plan over several segments: a join's own preds may stand in another
        self.eff, self.flagrows, self.planned = {}, {}, frozenset()
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
        """One SSA name as a cell of the object: one copy per voice.

        A name bound outside the voice loop is one value the whole tune keeps,
        so its cell is a ``#global`` and not a copy each voice reads its own of.
        """
        c = self.temps.get(n)
        if c is None:
            c = self.temps[n] = ("#" if n in self.scalars else "") + "t" + ident(n)
            self.cells.declare(c, None)
        if w == 2:
            self.wide.add(c.lstrip("#"))
        return c

    @staticmethod
    def tref(c):
        """One temp read where it lives: a global's own name, or the voice's cell."""
        return {"global": c[1:]} if c[:1] == "#" else {"cell": c}

    def expand(self, e, depth=DEPTH):
        """One expression with its SSA names and single reaching stores substituted."""
        if depth <= 0:
            return e
        t = type(e)
        if t is Var and e.n in self.local:
            return Const(self.local[e.n], e.w)
        if t is Var and (e.n in self.v.supplied or e.n in self.v.subst):
            return e
        if t is Var and e.n in self.pick:
            return self.expand(self.pick[e.n], depth - 1)
        if t is Var and e.n not in self.v.vidx and e.n in self.defs:
            return self.expand(self.defs[e.n], depth - 1)
        if t is Load and e.cls == "ram":
            base, idx = addr_split(e.a)
            ok = idx is None or (type(idx) is Var and idx.n in self.v.vidx)
            if base is not None and ok:
                vs = self.reach.get(self.lbl, {}).get(base)
                if vs and len(vs) == 1 and not self.selfread(next(iter(vs)), base):
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

    def selfread(self, v, base, depth=DEPTH):
        """Whether a store's own value reads the cell it lands in: a counter, not a copy.

        A cell whose value is its own is state the tune carries between ticks, so
        the object states the store as a row and no reader folds it away.
        """
        seen, stack = set(), [v]
        while stack and depth:
            x = stack.pop()
            for y in walk(x):
                if type(y) is Load and addr_split(y.a)[0] == base:
                    return True
                if type(y) is Var and y.n in self.defs and y.n not in seen:
                    seen.add(y.n)
                    stack.append(self.defs[y.n])
            depth -= 1
        return False

    def chase(self, e, depth=DEPTH):
        """One name followed through its copies alone: no store is folded into it."""
        while type(e) is Var and depth and e.n in self.defs:
            e, depth = self.defs[e.n], depth - 1
        return e

    def isvoice(self, e):
        """Whether an index expression is the voice the tick is committing."""
        x = self.expand(e)
        return type(x) is Var and x.n in self.v.vidx

    # ---- expressions -------------------------------------------------------------
    def value(self, e):
        got = self.sub.get(repr(e)) if self.sub else None
        if got is not None:
            return got
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
            got = self.v.fields.get((e.n, None))
            got = got if self.v.payload or isinstance(got, dict) else None
            if got is not None:
                return got
            if e.n in self.v.supplied or e.n in self.assigned:
                return self.tref(self.temp(e.n, e.w))
            raise Unlowerable(e.n)
        if t is Load:
            return self.v.load(self, e)
        if t is Bin:
            return self.binop(e)
        raise Unlowerable(repr(e))

    def field(self, a, m):
        """A masked field of a byte the score supplied: the event field it is (§3.6).

        A fact the row program carries in its payload is read only where a payload
        stands; everywhere else the field is the player's own cell or nothing.
        """
        x = self.expand(a)
        got = self.v.fields.get((x.n, m)) if type(x) is Var else None
        return got if self.v.payload or isinstance(got, dict) else None

    def binop(self, e):
        op, w = e.op, e.w
        if op == "&" and type(e.b) is Const:
            got = self.field(e.a, e.b.v)
            if got is not None:
                return got
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

    def _edge(self, q, lbl):
        """The term the edge from ``q`` to ``lbl`` decides, where it decides one."""
        term = self.proc.blocks[q].term
        if type(term) is Switch:
            return tuple((q, c, True) for c in edge(term, lbl))
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
            got = fold(got)
            if len(got) < 2:  # one path, and the guard path it carries is the row's
                # the one path's own terms: a block a join carries states the edge
                # it took and none of the terms the join replaced with its cell
                keep = got[0][0] if got else ()
                own = tuple(t for t in self._own(lbl) if t in keep) if ps else self._own(lbl)
                eff[lbl] = (own, got[0][1] if got else ())
                continue
            name = "j" + ident(lbl)
            self.cells.declare(name, None)
            for q in ps:
                rows.setdefault(q, []).append((name, ctx[q]))
            eff[lbl] = ((), (({"cell": name}, "!=", 0),))
        return eff, rows

    # ---- statements ----------------------------------------------------------------
    def planall(self, groups):
        """One plan a segment, and the flags every other segment must raise.

        A join's own preds do not stop at a segment's edge, so a plan taken
        segment by segment leaves a cell the paths of *one* segment raise and
        every other path unreached.  Each segment decides its own cells and then
        every path that reaches one raises it where that path stands, whichever
        segment holds it; the flags are reset once, at the head of the first
        phase, which every path's own row follows.
        """
        eff, flagrows = {}, {}
        for g in groups:
            e, rows = self.plan(frozenset(g))
            eff.update(e)
            for q, v in rows.items():
                flagrows.setdefault(q, []).extend(v)
        names = {n for v in flagrows.values() for n, _c in v}
        for lbl in sorted(eff):
            name = "j" + ident(lbl)  # the cell ``plan`` declares a join under
            if name not in names:
                continue
            have = {q for q, v in flagrows.items() for n, _c in v if n == name}
            for q in sorted(self.preds.get(lbl, ())):
                if q in have or q not in eff:
                    continue
                ctx = (tuple(dict.fromkeys(eff[q][0] + self._edge(q, lbl))), eff[q][1])
                flagrows.setdefault(q, []).append((name, ctx))
        self.eff, self.flagrows = eff, flagrows
        self.planned = frozenset(eff)
        return sorted({n for v in flagrows.values() for n, _c in v})

    def refusals(self):
        return sorted(self.bad)
