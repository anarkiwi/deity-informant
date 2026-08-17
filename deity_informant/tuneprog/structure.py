"""S5 -- structural analysis over the S4 IR: loops, if/else, switch, ``for``, phase.

Presentation only: :func:`view` makes a semantics-preserving copy of the certified
program and :func:`structure` returns a node tree over it -- loops from the back
edges, if/else from the immediate post-dominator, ``goto`` for the residue.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

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
    Trap,
    Var,
    evalbin,
    retexpr,
    retval,
    succs,
)
from .graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of
from .inline import loads as inline_loads
from .ssa import merge_chains, prune

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
    e: object = None  # the value a `return` hands back, when a reader wants it


# ---- the presentation copy ---------------------------------------------------
def view(prog, live=None, keep=None):
    """A copy of ``prog`` shaped for reading; with ``live`` it also drops dead values.

    Without ``live`` the copy is semantics-preserving (block merging and inlining
    only); ``{proc: live names}`` additionally removes what nothing reads, which is
    a presentation step and leaves the copy unexecutable.
    """
    out = copy.deepcopy(prog)
    for name, p in out.procs.items():
        prune(p)
        merge_chains(p)
        inline_loads(p, None if live is None else live[name], (keep or {}).get(name, ()))
    return out


def inline(prog, live, keep=None):
    """Re-run the value folding after the texture passes reshaped the blocks."""
    for name, p in prog.procs.items():
        inline_loads(p, live[name], (keep or {}).get(name, ()))
    return prog


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


def _trivial(proc, lbl, want=()):
    """The exit a statement-free block is, so a jump to it prints as that exit."""
    b = proc.blocks[lbl]
    if b.stmts:
        return None
    if type(b.term) is Return:
        return Exit("return", "", _returns(proc, b.term, want))
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


def _returns(proc, term, want):
    """What a ``return`` shows: the tick's own value, or the register a caller reads."""
    return retval(proc) if proc.kind == "tick" else retexpr(proc, term, want)


# ---- the structurer ----------------------------------------------------------
class _Structurer:
    def __init__(self, proc, want=()):
        self.want = want
        self.proc = proc
        self.g = cfg(proc)
        self.preds = preds_of(proc)
        self.idom = idoms(proc, self.g)
        self.ipdom = postdoms(self.g, proc)
        self.loops = natural_loops(self.g, self.idom, self.preds)
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
                end = _trivial(self.proc, n, self.want)
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
            out.append(Exit("return", "", _returns(self.proc, t, self.want)))
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


def structure_proc(proc, want=()):
    """The structured body of one procedure."""
    return _Structurer(proc, want).run()


def wants(prog, live):
    """``{procedure: the return registers a caller reads}`` (design section 6.2)."""
    out = {n: set() for n in prog.procs}
    for name, p in prog.procs.items():
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Call and s.proc in out:
                    q = prog.procs[s.proc]
                    out[s.proc] |= {i for i, r in zip(q.rets, s.rets) if r in live[name]}
    return out


def structure(prog, want=None):
    """``{proc name: [node]}`` over ``prog`` (run :func:`view` on it first).

    ``want`` is ``{procedure: the return registers its callers read}`` (see
    :func:`wants`), so a procedure that computes a byte for its caller prints it.
    """
    return {n: structure_proc(p, (want or {}).get(n, ())) for n, p in prog.procs.items()}


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
    """``(region, test, procs on the true arm, procs on the false arm)`` of the tick.

    The phase is a state *scalar* (design S5), so a test that reads one byte of a
    larger region -- Follin's tick opens on `active[0]` inside the 118-byte
    zero-page block -- is a test, not a phase.
    """
    rgn = {r.id: r for r in storage}
    defs = _lets(n for n in walk(body) if type(n) is Blk)
    for n in walk(body):
        if type(n) is not Cond:
            continue
        rs = set()
        if not _one_region(n.c, rs, defs) or len(rs) != 1:
            return None
        r = rgn.get(rs.pop())
        if r is None or r.kind != "state" or r.size != 1:
            return None
        return (r.id, n.c, _procs(n.then), _procs(n.els))
    return None
