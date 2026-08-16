"""S5 -- structural analysis over the S4 IR: loops, if/else, switch, ``for``, phase.

Presentation only: :func:`view` makes a semantics-preserving copy of the certified
program and :func:`structure` returns a node tree over it -- loops from the back
edges, if/else from the immediate post-dominator, ``goto`` for the residue.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import networkx as nx

from .ir import (
    Bin,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    Return,
    Store,
    Switch,
    Trap,
    Var,
    evalbin,
    succs,
)
from .ssa import merge_chains, preds_of, prune, stmt_uses, sub_expr, term_uses, use_counts

EXIT = "$exit"
CAP = 256


@dataclass
class Blk:
    """The statements of one IR block, in order."""

    label: str
    stmts: list
    src: int = 0
    count: int = 0


@dataclass
class Cond:
    c: object
    then: list
    els: list = field(default_factory=list)


@dataclass
class Loop:
    """A loop with ``break``/``continue`` inside; the printer picks while/do."""

    body: list
    label: str = ""
    count: int = 0


@dataclass
class For:
    """A counted loop: ``var`` takes ``values``; ``hide`` are its stepping lets."""

    var: str
    values: tuple
    body: list
    scale: int = 1
    hide: frozenset = frozenset()
    label: str = ""
    count: int = 0


@dataclass
class Case:
    e: object
    cases: tuple = ()
    src: int = 0


@dataclass
class Jump:
    kind: str  # break | continue | goto
    label: str = ""


@dataclass
class Exit:
    kind: str  # return | trap
    why: str = ""


# ---- the presentation copy ---------------------------------------------------
def _pure(e):
    t = type(e)
    if t is Load:
        return False
    return _pure(e.a) and _pure(e.b) if t is Bin else True


def _sub_stmt(s, fn):
    t = type(s)
    if t is Let:
        s.e = sub_expr(s.e, fn)
    elif t is Store:
        s.a = sub_expr(s.a, fn)
        s.v = sub_expr(s.v, fn)
    elif t.__name__ == "Call":
        s.args = tuple(sub_expr(a, fn) for a in s.args)
    elif t.__name__ == "Assert":
        s.e = sub_expr(s.e, fn)


def _sub_term(t, fn):
    k = type(t)
    if k is If:
        t.c = sub_expr(t.c, fn)
    elif k is Switch:
        t.e = sub_expr(t.e, fn)
    elif k is Return:
        t.vals = tuple(sub_expr(v, fn) for v in t.vals)


def _folder(avail, gone):
    def fn(e):
        if type(e) is not Var or e.n not in avail:
            return e
        gone.add(e.n)
        return avail[e.n]

    return fn


def _reads(e, pred):
    t = type(e)
    if t is Load:
        return pred(e) or _reads(e.a, pred)
    return _reads(e.a, pred) or _reads(e.b, pred) if t is Bin else False


def _clobbers(s, e):  # noqa: D401
    """True when statement ``s`` can change the value of ``e``.

    Regions are disjoint by construction, so a load of region R survives every
    store to another region and every ``sidw``/``iow``; a call may write anything.
    """
    t = type(s)
    if t.__name__ == "Call":
        return not _pure(e)
    if t is not Store:
        return False
    if s.cls == "io":
        return _reads(e, lambda x: x.cls == "io")
    if s.cls == "raw":
        return _reads(e, lambda x: x.cls == "raw")
    return _reads(e, lambda x: x.r < 0 or x.r == s.r or s.r < 0)


def _cost(e):
    """Operator count of an expression, address arithmetic included."""
    t = type(e)
    if t is Bin:
        return 1 + _cost(e.a) + _cost(e.b)
    return _cost(e.a) if t is Load else 0


def _loads(e, single, cache, seen=()):
    """Every load the value of ``e`` reads once its single-definition names expand."""
    out = []
    for x in _walk(e):
        if type(x) is Load:
            out.append(x)
        elif type(x) is Var and x.n in single and x.n not in seen:
            key = x.n
            if key not in cache:
                cache[key] = _loads(single[key][2], single, cache, seen + (key,))
            out += cache[key]
    return out


def _walk(e):
    yield e
    t = type(e)
    if t is Bin:
        yield from _walk(e.a)
        yield from _walk(e.b)
    elif t is Load:
        yield from _walk(e.a)


def _kills(s, ls):
    return any(_clobbers(s, x) for x in ls)


def _own(proc, preds, lbl):
    """``{block: its predecessor}`` for the blocks only ``lbl`` can reach."""
    out, work = {}, [lbl]
    while work:
        cur = work.pop()
        for nxt in succs(proc.blocks[cur].term):
            if nxt in out or nxt == lbl or preds.get(nxt) != [cur]:
                continue
            out[nxt] = cur
            work.append(nxt)
    return out


def _blocked(proc, own, lbl, at, pos, ls):
    """True when a store, a call or a foreign block sits between the value and a use."""
    for ublk, uidx in pos:
        if ublk == lbl:
            if _kills_run(proc, lbl, at + 1, uidx, ls):
                return True
            continue
        if ublk not in own:
            return True
        if _kills_run(proc, ublk, 0, uidx, ls):
            return True
        cur = own[ublk]
        while cur != lbl:
            if _kills_run(proc, cur, 0, len(proc.blocks[cur].stmts), ls):
                return True
            cur = own[cur]
        if _kills_run(proc, lbl, at + 1, len(proc.blocks[lbl].stmts), ls):
            return True
    return False


def _kills_run(proc, lbl, lo, hi, ls):
    return any(_kills(s, ls) for s in proc.blocks[lbl].stmts[lo:hi])


def _positions(proc):
    """``{name: [(block, statement index)]}`` of every use; the terminator is last."""
    where = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            for u in stmt_uses(s, set()):
                where.setdefault(u, []).append((lbl, i))
        for u in term_uses(b.term, set()):
            where.setdefault(u, []).append((lbl, len(b.stmts)))
    return where


def _inline_loads(proc, live=None):
    """Fold a value into its uses, past statements that cannot alias it.

    A use may sit in another block when that block is reachable only through the
    definition's; regions are disjoint, so only a store to the same region, a call
    or an input read stops the move.
    """
    if live is not None:
        for b in proc.blocks.values():
            b.stmts = [s for s in b.stmts if type(s) is not Let or s.n in live]
            if type(b.term) is Return:
                b.term = Return()
    uses, where, preds = use_counts(proc), _positions(proc), preds_of(proc)
    latches = _latches(proc, preds)
    defs = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            if type(s) is Let:
                defs.setdefault(s.n, []).append((lbl, i, s.e))
    single = {n: v[0] for n, v in defs.items() if len(v) == 1}
    sub, cache, owns = {}, {}, {}
    for n, (lbl, at, e) in sorted(single.items()):
        pos = where.get(n, ())
        if not pos or len(pos) != uses[n] or type(e) is Var or _feeds_copy(proc, pos, latches):
            continue
        ls = _loads(e, single, cache)
        if len(pos) > 1 and (_cost(e) > 1 or any(_input(x) for x in ls)):
            continue
        own = owns.setdefault(lbl, _own(proc, preds, lbl))
        if not _blocked(proc, own, lbl, at, pos, ls):
            sub[n] = e
    return _apply_sub(proc, sub)


def _latches(proc, preds):
    """The blocks that close a loop: their copies are the induction variables."""
    g = _cfg(proc)
    idom = nx.immediate_dominators(g, proc.entry)
    return {l for _b, ls in _natural_loops(g, idom, preds).values() for l in ls}


def _feeds_copy(proc, pos, latches):
    """A value a loop-closing rename reads stays: it carries the induction variable."""
    for lbl, i in pos:
        stmts = proc.blocks[lbl].stmts
        if lbl in latches and i < len(stmts) and type(stmts[i]) is Let and type(stmts[i].e) is Var:
            return True
    return False


def _subber(sub):
    def fn(e):
        return sub.get(e.n, e) if type(e) is Var else e

    return fn


def _apply_sub(proc, sub):
    """Expand the chosen values into each other, then into every use."""
    for _ in range(len(sub)):
        nxt = {n: sub_expr(e, _subber(sub)) for n, e in sub.items()}
        if nxt == sub:
            break
        sub = nxt
    fn = _subber(sub)
    for b in proc.blocks.values():
        for s in b.stmts:
            _sub_stmt(s, fn)
        _sub_term(b.term, fn)
        b.stmts = [s for s in b.stmts if not (type(s) is Let and s.n in sub)]
    return proc


def _input(x):
    """A load whose value comes from the pinned input stream, not from a region."""
    return x.cls == "io" or (x.cls == "chk" and x.r < 0)


def view(prog, live=None):
    """A copy of ``prog`` shaped for reading; with ``live`` it also drops dead values.

    Without ``live`` the copy is semantics-preserving (block merging and inlining
    only); ``{proc: live names}`` additionally removes what nothing reads, which is
    a presentation step and leaves the copy unexecutable.
    """
    out = copy.deepcopy(prog)
    for name, p in out.procs.items():
        prune(p)
        merge_chains(p)
        _inline_loads(p, None if live is None else live[name])
    return out


def inline(prog, live):
    """Re-run the single-use folding after the texture passes reshaped the blocks."""
    for name, p in prog.procs.items():
        _inline_loads(p, live[name])
    return prog


# ---- graphs ------------------------------------------------------------------
def _cfg(proc):
    g = nx.DiGraph()
    g.add_nodes_from(proc.blocks)
    for lbl, b in proc.blocks.items():
        g.add_edges_from((lbl, s) for s in succs(b.term))
    return g


def _postdoms(g, proc):
    """Immediate post-dominators through a virtual exit; a trap never reaches it."""
    r = nx.DiGraph()
    r.add_nodes_from(g)
    r.add_edges_from((b, a) for a, b in g.edges)
    r.add_edges_from((EXIT, n) for n in g if type(proc.blocks[n].term) is Return)
    return nx.immediate_dominators(r, EXIT) if EXIT in r else {}


def _natural_loops(g, idom, preds):
    """``{header: (body labels, latch labels)}`` from the back edges."""
    loops = {}
    for u, v in g.edges:
        d = u
        while d is not None and d != v and idom.get(d) != d:
            d = idom.get(d)
        if d != v:
            continue
        body, latches = loops.setdefault(v, (set([v]), set()))
        latches.add(u)
        stack = [u]
        while stack:
            x = stack.pop()
            if x in body:
                continue
            body.add(x)
            stack.extend(preds.get(x, ()))
    return loops


# ---- counted loops -----------------------------------------------------------
class _Values:
    """Constant folding over a procedure: SSA names, and cells through their stores.

    A name folds when every definition of it agrees; a byte cell folds when every
    store that reaches the load agrees, which is what carries an index register
    through the save/restore pair the 6510 uses when it runs out of registers.
    """

    def __init__(self, proc, labels=None, budget=20000):
        self.proc = proc
        self.preds = preds_of(proc)
        self.defs, self.budget, self.rcache = {}, budget, {}
        for lbl in proc.blocks if labels is None else labels:
            for i, st in enumerate(proc.blocks[lbl].stmts):
                if type(st) is Let:
                    self.defs.setdefault(st.n, []).append((lbl, i, st.e))

    def val(self, e, env, seen=frozenset(), at=None):
        """The constant value of ``e`` at position ``at`` under ``env``, or ``None``."""
        self.budget -= 1
        if self.budget < 0:
            return None
        t = type(e)
        if t is Const:
            return e.v
        if t is Var:
            if e.n in env:
                return env[e.n]
            return None if e.n in seen else self.agree(self.defs.get(e.n, ()), env, seen | {e.n})
        if t is Bin:
            a, b = self.val(e.a, env, seen, at), self.val(e.b, env, seen, at)
            return None if a is None or b is None else evalbin(e.op, a, b, e.w)
        if t is Load and type(e.a) is Const and e.w == 1 and e.cls != "io" and at is not None:
            k = ("mem", at, e.a.v)
            src = None if k in seen else self.reaching(at, e.a.v)
            return None if src is None else self.agree(src, env, seen | {k})
        return None

    def agree(self, defs, env, seen):
        vals = {self.val(x, env, seen, (lbl, i)) for lbl, i, x in defs}
        return vals.pop() if len(vals) == 1 else None

    def reaching(self, at, addr):
        """The stores of ``addr`` that reach ``at``; ``None`` if any path has none."""
        if (at, addr) in self.rcache:
            return self.rcache[(at, addr)]
        self.rcache[(at, addr)] = None
        out, seen, work = [], set(), [at]
        while work:
            self.budget -= 1
            lbl, i = work.pop()
            hit = self.last_store(lbl, i, addr)
            if hit is False or self.budget < 0:
                return None
            if hit is not None:
                out.append(hit)
                continue
            if not self.preds.get(lbl):
                return None
            for q in self.preds[lbl]:
                pos = (q, len(self.proc.blocks[q].stmts))
                if pos not in seen:
                    seen.add(pos)
                    work.append(pos)
        self.rcache[(at, addr)] = out
        return out

    def last_store(self, lbl, i, addr):
        """``(lbl, j, value)`` of the last store to ``addr`` before ``i``, False if opaque."""
        for j in range(i - 1, -1, -1):
            s = self.proc.blocks[lbl].stmts[j]
            if type(s) is Call:
                return False
            if type(s) is not Store or s.cls == "raw":
                continue
            if type(s.a) is Const:
                if s.a.v <= addr < s.a.v + s.w:
                    return (lbl, j, s.v) if s.w == 1 else False
            elif s.lo <= addr <= s.hi:
                return False
        return None


def _trivial(proc, lbl):
    """The exit a statement-free block is, so a jump to it prints as that exit."""
    b = proc.blocks[lbl]
    if b.stmts:
        return None
    if type(b.term) is Return:
        return Exit("return")
    return Exit("trap", b.term.why) if type(b.term) is Trap else None


def _leaves(proc, body, lbl):
    """The successors of ``lbl`` that leave the loop for a path the trace took."""
    return [s for s in succs(proc.blocks[lbl].term) if s not in body and not _dead(proc, s)]


def _dead(proc, lbl):
    return type(proc.blocks[lbl].term) is Trap


def _exit_tests(proc, body):
    """``[(condition, the truth of it that exits)]`` for every edge leaving the loop."""
    out = []
    for lbl in sorted(body):
        t = proc.blocks[lbl].term
        if type(t) is not If:
            continue
        leaves = _leaves(proc, body, lbl)
        at = (lbl, len(proc.blocks[lbl].stmts))
        if t.t in leaves:
            out.append((t.c, True, at))
        elif t.f in leaves:
            out.append((t.c, False, at))
    return out


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def _domain(k, var, step, vals, tests):
    """The values the loop header runs with, by iterating the recurrence to its exit."""
    out = []
    while len(out) < CAP:
        out.append(k)
        for cond, when, at in tests:
            v = vals.val(cond, {var: k}, at=at)
            if v is None:
                return None
            if bool(v) == when:
                return out
        k = vals.val(step[2], {var: k}, at=(step[0], step[1]))
        if k is None or k in out:
            return None
    return None


def _plausible(proc, header, body, latches, preds, n):
    """The trace agrees: every entry to the loop ran the header ``n`` times."""
    hits = proc.blocks[header].count
    if not hits:
        return True
    outside = sum(proc.blocks[p].count for p in preds[header] if p not in body)
    back = hits - sum(proc.blocks[l].count for l in latches)
    return any(e > 0 and hits == e * n for e in (outside, back))


def induction(proc, header, body, latches, preds=None):
    """``(var, values, scale, hide)`` when the loop is counted, else ``None``."""
    preds = preds_of(proc) if preds is None else preds
    tests = _exit_tests(proc, body)
    if not tests:
        return None
    inner = _Values(proc)
    outer = _Values(proc, [l for l in proc.blocks if l not in body])
    for latch in sorted(latches):
        for i, s in enumerate(proc.blocks[latch].stmts):
            if type(s) is not Let or type(s.e) is not Var:
                continue
            k = outer.val(Var(s.n), {})
            vals = None if k is None else _domain(k, s.n, (latch, i, s.e), inner, tests)
            if vals and len(vals) > 1 and _plausible(proc, header, body, latches, preds, len(vals)):
                scale = 0
                for v in vals:
                    scale = _gcd(scale, v)
                return s.n, tuple(vals), scale or 1, frozenset({s.n, s.e.n})
    return None


# ---- the structurer ----------------------------------------------------------
class _Structurer:
    def __init__(self, proc):
        self.proc = proc
        self.g = _cfg(proc)
        self.preds = preds_of(proc)
        self.idom = nx.immediate_dominators(self.g, proc.entry)
        self.ipdom = _postdoms(self.g, proc)
        self.loops = _natural_loops(self.g, self.idom, self.preds)
        self.done = set()
        self.labels = set()

    def run(self):
        body = self.seq(self.proc.entry, None, ())
        while True:
            left = [l for l in self.proc.order() if l not in self.done]
            if not left:
                return body
            self.labels.add(left[0])
            body.extend(self.seq(left[0], None, ()))

    def seq(self, n, follow, ctx):
        out = []
        while n is not None and n != follow:
            if any(c[0] == n for c in ctx):
                out.append(Jump("continue", n))
                break
            hit = next((c for c in ctx if c[1] == n), None)
            if hit is not None:
                out.append(Jump("break", hit[0]))
                break
            if n in self.done:
                end = _trivial(self.proc, n)
                out.append(end if end is not None else Jump("goto", n))
                if end is None:
                    self.labels.add(n)
                break
            if n in self.loops and not any(c[0] == n for c in ctx):
                node, n = self.loop(n, ctx)
                out.append(node)
                continue
            self.done.add(n)
            blk = self.proc.blocks[n]
            out.append(Blk(n, blk.stmts, blk.src, blk.count))
            n = self.term(blk, out, ctx)
        return out

    def head(self, h, ctx):
        """The loop body: the header itself, then the sequence it flows into."""
        self.done.add(h)
        blk = self.proc.blocks[h]
        out = [Blk(h, blk.stmts, blk.src, blk.count)]
        out.extend(self.seq(self.term(blk, out, ctx), None, ctx))
        return out

    def term(self, blk, out, ctx):
        """Emit ``blk``'s terminator; returns the label the sequence continues at."""
        t = blk.term
        k = type(t)
        if k is Goto:
            return t.to
        if k is Return:
            out.append(Exit("return"))
            return None
        if k is Trap:
            out.append(Exit("trap", t.why))
            return None
        f = self.ipdom.get(blk.label)
        if f in (blk.label, EXIT):
            f = None
        if k is If:
            out.append(Cond(t.c, self.seq(t.t, f, ctx), self.seq(t.f, f, ctx)))
        else:
            out.append(Case(t.e, tuple((v, self.seq(l, f, ctx)) for v, l in t.cases), blk.src))
        return f

    def loop(self, h, ctx):
        body, latches = self.loops[h]
        outs = sorted({s for l in body for s in _leaves(self.proc, body, l)})
        exit_lbl = max(outs, key=lambda l: (self.proc.blocks[l].count, l)) if outs else None
        ind = induction(self.proc, h, body, latches, self.preds)
        inner = self.head(h, ctx + ((h, exit_lbl),))
        count = self.proc.blocks[h].count
        if ind is None:
            return Loop(inner, h, count), exit_lbl
        var, vals, scale, hide = ind
        return For(var, vals, inner, scale, hide, h, count), exit_lbl


def structure_proc(proc):
    """The structured body of one procedure."""
    return _Structurer(proc).run()


def structure(prog):
    """``{proc name: [node]}`` over ``prog`` (run :func:`view` on it first)."""
    return {n: structure_proc(p) for n, p in prog.procs.items()}


# ---- phase recognition -------------------------------------------------------
def walk(body):
    """Every node of a structured body, outermost first."""
    for n in body:
        yield n
        t = type(n)
        if t is Cond:
            yield from walk(n.then)
            yield from walk(n.els)
        elif t is Loop or t is For:
            yield from walk(n.body)
        elif t is Case:
            for _v, b in n.cases:
                yield from walk(b)


def _one_region(e, out, defs, seen=()):
    """Collect the regions ``e`` loads from; False when it reads anything else."""
    t = type(e)
    if t is Const:
        return True
    if t is Bin:
        return _one_region(e.a, out, defs, seen) and _one_region(e.b, out, defs, seen)
    if t is Var:
        return e.n in defs and e.n not in seen and _one_region(defs[e.n], out, defs, seen + (e.n,))
    if t is Load and type(e.a) is Const:
        out.add(e.r)
        return True
    return False


def _lets(blocks):
    return {s.n: s.e for b in blocks for s in b.stmts if type(s) is Let}


def _procs(body):
    return [s.proc for n in walk(body) if type(n) is Blk for s in n.stmts if hasattr(s, "proc")]


def phase(body, storage):
    """``(region, test, procs on the true arm, procs on the false arm)`` of the tick."""
    kinds = {r.id: r.kind for r in storage}
    defs = _lets(n for n in walk(body) if type(n) is Blk)
    for n in walk(body):
        if type(n) is not Cond:
            continue
        rs = set()
        if not _one_region(n.c, rs, defs) or len(rs) != 1:
            return None
        r = rs.pop()
        return None if kinds.get(r) != "state" else (r, n.c, _procs(n.then), _procs(n.els))
    return None
