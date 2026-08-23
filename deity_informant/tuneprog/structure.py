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
from .closure import closed_blocks
from .graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of
from .loops import copies, induction, leaves, stepping
from .inline import values as inline_values
from .ssa import merge_chains, prune


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


def inline(prog, live, keep=None, dup=True):
    """Re-run the value folding after the texture passes reshaped the blocks."""
    for name, p in prog.procs.items():
        inline_values(p, live[name], (keep or {}).get(name, ()), dup)
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
    """The covered program's shape, with each closed arm structured under its branch.

    Dominance and the loops are the covered subgraph's (:func:`~.graph.edges_of`),
    so a statically closed arm owns nothing; post-dominance is the whole graph's,
    which is where the arm rejoins, so it nests in the branch that offered it.
    """

    def __init__(self, proc, want=()):
        self.want = want
        self.proc = proc
        self.shut = closed_blocks(proc)
        self.g = cfg(proc, shut=self.shut)
        self.preds = preds_of(proc, shut=self.shut)
        self.idom = idoms(proc, self.g)
        self.ipdom = postdoms(cfg(proc) if self.shut else self.g, proc)
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

    def seq(self, n, follow, ctx, shut=False):
        out = []
        while n is not None and n != follow:
            if any(c[0] == n for c in ctx):
                out.append(Jump("continue", n))
                break
            hit = next((c for c in ctx if c[1] == n), None)
            if hit is not None:
                out.append(Jump("break", hit[0]))
                break
            if n in self.done or (shut and n not in self.shut):
                end = _trivial(self.proc, n, self.want)
                out.append(end if end is not None else Jump("goto", n))
                if end is None:
                    self.labels.add(n)
                break
            if n in self.loops and not any(c[0] == n for c in ctx):
                shut = shut or n in self.shut
                node, n = self.loop(n, ctx, shut)
                out.append(node)
                continue
            self.done.add(n)
            blk = self.proc.blocks[n]
            out.append(Blk(n, blk.stmts, blk.src, blk.count))
            shut = shut or n in self.shut
            n = self.term(blk, out, ctx, shut)
        return out

    def head(self, h, ctx, shut=False):
        """The loop body: the header itself, then the sequence it flows into."""
        self.done.add(h)
        blk = self.proc.blocks[h]
        out = [Blk(h, blk.stmts, blk.src, blk.count)]
        shut = shut or h in self.shut
        out.extend(self.seq(self.term(blk, out, ctx, shut), None, ctx, shut))
        return out

    def term(self, blk, out, ctx, shut=False):
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
            out.append(Cond(t.c, self.seq(t.t, f, ctx, shut), self.seq(t.f, f, ctx, shut)))
        else:
            arms = tuple((v, self.seq(l, f, ctx, shut)) for v, l in t.cases)
            out.append(Case(t.e, arms, blk.src))
        return f

    def loop(self, h, ctx, shut=False):
        body, latches = self.loops[h]
        outs = sorted({s for l in body for s in leaves(self.proc, body, l, self.shut)})
        exit_lbl = max(outs, key=lambda l: (self.proc.blocks[l].count, l)) if outs else None
        hit = copies(self.proc, h, latches, body, self.preds)
        ind = induction(self.proc, h, body, latches, self.preds, self.shut) if hit is None else None
        inner = self.head(h, ctx + ((h, exit_lbl),), shut)
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


def strip(body, label, hide, top=True, tail=True):
    """Drop the induction test and the back edge a ``for`` header already states.

    A family's chain edge sits wherever the copy ended its own work, so the test
    is looked for down the branches -- but only where nothing follows it, since a
    test mid-body guards the statements after it. Only the outermost back edge is
    implied.
    """
    out = []
    for n in reversed(body):
        t = type(n)
        if t is Jump and n.label == label and top and tail:
            continue
        if t is Cond and tail and jumps_only(n.then + n.els, hide, label):
            continue
        if t is Cond:
            arms = (strip(x, label, hide, False, tail) for x in (n.then, n.els))
            out.append(Cond(n.c, *arms))
        elif t is Case:
            arms = tuple((v, strip(b, label, hide, False, tail)) for v, b in n.cases)
            out.append(Case(n.e, arms, n.src))
        else:
            out.append(n)
        tail = False
    return out[::-1]


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


def _called_procs(body):
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
        return (r.id, n.c, _called_procs(n.then), _called_procs(n.els))
    return None


def structure_json(structured, names):
    """The S5 annotation: the structured shape of every procedure."""
    return {
        "procs": {n: [_json(x) for x in body] for n, body in structured.items()},
        "phase": None if names.phase is None else {"region": names.phase[0]},
    }


def _json(n):
    """One structured node as data (children included, expressions elided)."""
    k = type(n).__name__.lower()
    d = {"kind": k}
    for f in ("label", "src", "count", "var", "kind", "values", "scale"):
        if hasattr(n, f) and f != "kind":
            d[f] = list(n.values) if f == "values" else getattr(n, f)
    if k == "jump" or k == "exit":
        d["kind"] = "%s:%s" % (k, n.kind)
    for f in ("then", "els", "body"):
        if hasattr(n, f):
            d[f] = [_json(x) for x in getattr(n, f)]
    if k == "case":
        d["cases"] = [[v, [_json(x) for x in b]] for v, b in n.cases]
    if k == "blk":
        d["stmts"] = len(n.stmts)
    return d
