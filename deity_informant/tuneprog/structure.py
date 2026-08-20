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
    Const,
    Goto,
    If,
    Let,
    Load,
    Return,
    Trap,
    Var,
    retexpr,
    retval,
)
from .graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of
from .loops import copies, induction, leaves, stepping
from .inline import values as inline_values
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
    """A counted loop: ``var`` takes ``values``; ``hide`` are its stepping lets.

    ``group`` names the struct view the index selects, when the loop runs over
    sibling copies (:mod:`.copyview`).
    """

    var: str
    values: tuple
    body: list
    scale: int = 1
    hide: frozenset = frozenset()
    label: str = ""
    count: int = 0
    group: str = ""


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
        inline_values(p, None if live is None else live[name], (keep or {}).get(name, ()))
    return out


def inline(prog, live, keep=None):
    """Re-run the value folding after the texture passes reshaped the blocks."""
    for name, p in prog.procs.items():
        inline_values(p, live[name], (keep or {}).get(name, ()))
    return prog


# ---- counted loops -----------------------------------------------------------
def _trivial(proc, lbl, want=()):
    """The exit a statement-free block is, so a jump to it prints as that exit."""
    b = proc.blocks[lbl]
    if b.stmts:
        return None
    if type(b.term) is Return:
        return Exit("return", "", _returns(proc, b.term, want))
    return Exit("trap", b.term.why) if type(b.term) is Trap else None


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
        outs = sorted({s for l in body for s in leaves(self.proc, body, l)})
        exit_lbl = max(outs, key=lambda l: (self.proc.blocks[l].count, l)) if outs else None
        hit = copies(self.proc, h, latches)
        ind = induction(self.proc, h, body, latches, self.preds) if hit is None else None
        inner = self.head(h, ctx + ((h, exit_lbl),))
        count = self.proc.blocks[h].count
        if hit is not None:
            hide = frozenset(stepping(self.proc, latches, hit[0]))
            return For(hit[0], tuple(range(hit[1])), inner, 1, hide, h, count), exit_lbl
        if ind is None:
            return Loop(inner, h, count), exit_lbl
        var, vals, scale, hide = ind
        return For(var, vals, inner, scale, hide, h, count), exit_lbl


def structure_proc(proc, want=()):
    """The structured body of one procedure."""
    return _Structurer(proc, want).run()


def structure(prog, want=None):
    """``{proc name: [node]}`` over ``prog`` (run :func:`view` on it first).

    ``want`` is ``{procedure: the return registers its callers read}`` (see
    :func:`wants`), so a procedure that computes a byte for its caller prints it.
    """
    return {n: structure_proc(p, (want or {}).get(n, ())) for n, p in prog.procs.items()}


def hidden(s, hide):
    """True when a statement is a ``for`` header's own stepping let."""
    return type(s) is Let and s.n in hide


def strip(body, label, hide, top=True):
    """Drop the induction test and the back edge a ``for`` header already states.

    A family's chain edge sits wherever the copy ended its own work, so the test
    is looked for down the branches; only the outermost back edge is implied.
    """
    out = []
    for n in body:
        t = type(n)
        if t is Cond and jumps_only(n.then + n.els, hide, label):
            continue
        if t is Jump and n.label == label and top:
            continue
        if t is Cond:
            out.append(
                Cond(n.c, strip(n.then, label, hide, False), strip(n.els, label, hide, False))
            )
        elif t is Case:
            out.append(
                Case(n.e, tuple((v, strip(b, label, hide, False)) for v, b in n.cases), n.src)
            )
        else:
            out.append(n)
    return out


def jumps_only(nodes, hide, label=""):
    """True when a branch arm only jumps to ``label`` (its blocks are empty or hidden)."""
    for n in nodes:
        if type(n) is Jump and (not label or n.label == label):
            continue
        if type(n) is not Blk or any(not hidden(s, hide) for s in n.stmts):
            return False
    return True


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
