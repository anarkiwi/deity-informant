"""B7 -- the certified tick lowered into section 3.3 rows of ``sets``.

One block of the S4 IR becomes one guarded row: its guard path is the row's
``when``, its statements the row's ``sets`` in order, and every SSA temp a named
cell. Nothing is classified here; a leaf the vocabulary has no name for is
refused, and the score supplies the bytes a fetch read.
"""

from __future__ import annotations

from ..tuneprog.accguard import guardpath
from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of, rpo
from ..tuneprog.ir import Bin, Const, If, Let, Load, Store, Switch, Var, evalbin
from ..tuneprog.irwalk import addr_split
from .cells import ident
from .flow import edge as switchedge, fold, reaching, switched

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


class Lower:
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
        # the turn of an unrolled loop being lowered, and the cells its own turns read
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

    def turnsof(self, blocks):
        """``{name: (loop header, whether the head binds it)}`` for one segment.

        A name an unrolled loop binds takes one value a *turn*, so the score
        supplies one constant per turn of it and not one per name.
        """
        out = {}
        got = sorted(((len(v[0]), h) for h, v in self.loops.items() if h in blocks))
        for n, h in got:
            if n >= len(blocks):
                continue
            for lbl in self.loops[h][0]:
                for s in self.proc.blocks[lbl].stmts:
                    if type(s) is Let:
                        out.setdefault(s.n, (h, lbl == h))
        return out

    def turncell(self, n, w=1):
        """The cell one turn of an unrolled loop reads its own supplied byte in."""
        if self.turn is None:
            return None
        got = self.turns.setdefault(n, [])
        while len(got) <= self.turn:
            got.append(self.cells.declare("%s__%d" % (self.temp(n, w).lstrip("#"), len(got)), None))
        return got[self.turn]

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
                return self.tref(self.temp(e.n, e.w))
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
        if type(term) is Switch:
            return tuple((q, c, True) for c in switchedge(term, lbl))
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
    def row(self, lbl, extra=(), local=None, guard=None, turn=None):
        """One block as a guarded row, and the accumulator stores it must be split at."""
        self.lbl, self.local, self.turn = lbl, dict(local or {}), turn
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
        parts = [(self.copies(o), g) for o, g in parts]
        when = self.when(lbl, extra, guard)
        return when, parts

    def copies(self, rows):
        """Fold the copies of one per-voice cell a block writes at constant addresses.

        A value every copy takes is one write every voice makes (§3.6's ``all``);
        a copy that is neither the committing voice's nor one of a full set is no
        cell of the object.
        """
        at = {}
        for i, e in enumerate(rows):
            if type(e[0]) is tuple:
                at.setdefault(e[0][0], []).append(i)
        if not at:
            return rows
        out, drop = list(rows), set()
        for name, ks in at.items():
            vals = {rows[i][0][1]: repr(rows[i][1]) for i in ks}
            full = len(vals) == self.cells.voices and len(set(vals.values())) == 1
            for j, i in enumerate(ks):
                if full:
                    out[i] = ("*" + name, rows[i][1], rows[i][2])
                    drop |= {i} if j else set()
                elif not rows[i][0][1]:
                    out[i] = ("@" + name, rows[i][1], rows[i][2])
                else:
                    drop.add(i)
                    self.bad.add("$%04X" % rows[i][2])
        return [e for i, e in enumerate(out) if i not in drop]

    def one(self, s):
        """One statement: a temp, a cell, a register, or an accumulator's own store.

        A plain assignment carries the site it moves, which is what B7's
        recognition joins T1's accumulator records to.
        """
        t = type(s)
        self.lbl = self.lbl
        try:
            if t is Let:
                w = getattr(s.e, "w", 1)
                if s.n in self.v.subst:
                    return None
                nm = self.temp(s.n, w)
                put = nm if nm[:1] == "#" else "@" + nm
                if s.n in self.v.supplied:
                    cell = self.turncell(s.n, w)
                    if cell is None:  # one value a name: the score writes the cell itself
                        return None
                    return (put, {"cell": cell}, None)
                return (put, self.value(s.e), None)
            if t is Store:
                got = self.v.target(self, s)
                if got is None:
                    return None
                if got[0] == "acc":
                    return ("acc", got[1], self.value(s.v), s.src)
                return (got[1], self.value(s.v), s.src)  # a copy's target is a pair
        except Unlowerable as x:
            self.bad.add(s.n if t is Let else "$%04X" % s.src)
            del x
        return None

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
