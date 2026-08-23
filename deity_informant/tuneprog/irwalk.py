"""Traversal of the IR: sub-expressions, the values a node reads, names, call order.

Every pass of :mod:`.ssa` and every S5/S6 presentation module shares these walks,
so they live apart from the pass that first needed them. The S6 word view
(:class:`~.ir.R16`/:class:`~.ir.W16`) is walked like any other node.
"""

from __future__ import annotations

from collections import Counter, namedtuple

from .ir import (
    Assert,
    Bin,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    Phi,
    R16,
    REGIDX,
    copyval,
    Return,
    Store,
    Switch,
    Trap,
    Var,
    W16,
)

IMPURE = ("io", "chk")  # a load of these classes can consume a pinned input
NO_EXPRS = frozenset((Phi, Goto, Trap))  # nodes that evaluate nothing: names and labels only


# ---- expressions -------------------------------------------------------------
def walk(e):
    """Every sub-expression of ``e``, itself first."""
    yield e
    t = type(e)
    if t is Bin:
        yield from walk(e.a)
        yield from walk(e.b)
    elif t is Load or t is R16:
        yield from walk(e.a)


def loads(e):
    """Every :class:`~.ir.Load` the value of ``e`` reads."""
    return [x for x in walk(e) if type(x) is Load]


def any_load(e, pred):
    """True when some load in ``e`` satisfies ``pred``; stops at the first hit."""
    t = type(e)
    if t is Load:
        return pred(e) or any_load(e.a, pred)
    if t is Bin:
        return any_load(e.a, pred) or any_load(e.b, pred)
    return any_load(e.a, pred) if t is R16 else False


def sub_expr(e, fn):
    """Bottom-up rewrite of ``e``; ``fn`` sees each node after its children."""
    t = type(e)
    if t is Bin:
        a, b = sub_expr(e.a, fn), sub_expr(e.b, fn)
        if a is not e.a or b is not e.b:
            e = Bin(e.op, a, b, e.w)
    elif t is Load:
        a = sub_expr(e.a, fn)
        if a is not e.a:
            e = Load(e.cls, a, e.w, e.lo, e.hi, e.r)
    elif t is R16:
        a = sub_expr(e.a, fn)
        if a is not e.a:
            e = R16(e.lo, e.hi, a)
    return fn(e)


def expand(e, defs, depth):
    """``e`` with every name ``defs`` maps replaced by its value, ``depth`` deep."""
    t = type(e)
    if t is Var and depth > 0 and e.n in defs:
        return expand(defs[e.n], defs, depth - 1)
    if t is Bin:
        return Bin(e.op, expand(e.a, defs, depth), expand(e.b, defs, depth), e.w)
    if t is Load:
        return Load(e.cls, expand(e.a, defs, depth), e.w, e.lo, e.hi, e.r)
    return e


def pure(e):
    """True when evaluating ``e`` has no effect (a pinned input read has one)."""
    t = type(e)
    if t is Load:
        return e.cls not in IMPURE and pure(e.a)
    if t is Bin:
        return pure(e.a) and pure(e.b)
    return True


def loadfree(e):
    """True when ``e`` reads no memory at all: its value is register algebra."""
    t = type(e)
    if t is Load:
        return False
    return loadfree(e.a) and loadfree(e.b) if t is Bin else True


def reads_region(e, rids):
    """True when the value of ``e`` reads one of ``rids``, through a byte or a pair."""
    return any(
        (x.lo[0] in rids or x.hi[0] in rids) if type(x) is R16 else x.r in rids
        for x in walk(e)
        if type(x) in (Load, R16)
    )


def addr_split(e):
    """``(constant base, index)`` of an address expression; ``(None, e)`` if neither."""
    if type(e) is Const:
        return e.v, None
    if type(e) is Bin and e.op == "+":
        for k, i in ((e.a, e.b), (e.b, e.a)):
            if type(k) is Const:
                return k.v, i
    return None, e


# ---- statements and terminators ----------------------------------------------
def node_exprs(node):
    """The expressions one statement or terminator evaluates.

    Every node type is listed, :data:`NO_EXPRS` included: a type added to
    :mod:`.ir` and to neither raises rather than going silently invisible to
    every traversal at once.
    """
    t = type(node)
    if t is Let or t is Assert:
        return (node.e,)
    if t is Store:
        return (node.a, node.v)
    if t is W16:
        return (node.a, node.e)
    if t is Call:
        return node.args
    if t is If:
        return (node.c,)
    if t is Switch:
        return (node.e,)
    if t is Return:
        return node.vals
    if t in NO_EXPRS:
        return ()
    raise TypeError("node_exprs: unknown IR node %s" % t.__name__)


def node_loads(node):
    """Every :class:`~.ir.Load` one statement or terminator reads."""
    return (x for e in node_exprs(node) for x in walk(e) if type(x) is Load)


Acc = namedtuple("Acc", "proc rid store base idx lo hi")  # one accessor's shape


def accessors(prog, procs=None):
    """Every load and store of ``procs`` (all of them by default) as an :data:`Acc`.

    ``base``/``idx`` are the address split, ``lo``/``hi`` the envelope it stays
    inside: the one enumeration every region-shape question is asked of (the
    partition's claims, the record views' fields, the data section's reach).
    """
    for name, p in prog.procs.items():
        if procs is not None and name not in procs:
            continue
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                for x in node_loads(s):
                    yield Acc(name, x.r, False, *addr_split(x.a), x.lo, x.hi)
                if type(s) is Store and s.r >= 0:
                    yield Acc(name, s.r, True, *addr_split(s.a), s.lo, s.hi)


def apply_stmt(s, fn):
    """Rewrite every expression a statement reads (phi arguments are names)."""
    t = type(s)
    if t is Let:
        s.e = sub_expr(s.e, fn)
    elif t is Store:
        s.a = sub_expr(s.a, fn)
        s.v = sub_expr(s.v, fn)
    elif t is Call:
        s.args = tuple(sub_expr(a, fn) for a in s.args)
    elif t is W16:
        s.a = sub_expr(s.a, fn)
        s.e = sub_expr(s.e, fn)
    elif t is not Phi:
        s.e = sub_expr(s.e, fn)


def apply_term(t, fn):
    k = type(t)
    if k is If:
        t.c = sub_expr(t.c, fn)
    elif k is Switch:
        t.e = sub_expr(t.e, fn)
    elif k is Return:
        t.vals = tuple(sub_expr(v, fn) for v in t.vals)


# ---- names -------------------------------------------------------------------
def defs_of(s):
    t = type(s)
    if t is Let or t is Phi:
        return (s.n,)
    return s.rets if t is Call else ()


def uses_of(e, out, regs_only=False):
    t = type(e)
    if t is Var:
        if not regs_only or e.n in REGIDX or copyval(e.n):
            out.add(e.n)
    elif t is Bin:
        uses_of(e.a, out, regs_only)
        uses_of(e.b, out, regs_only)
    elif t is Load or t is R16:
        uses_of(e.a, out, regs_only)
    return out


def stmt_uses(s, out, regs_only=False):
    """Collect the names a statement reads (phi arguments belong to its predecessors)."""
    if type(s) is Phi:
        if not regs_only:
            out.update(s.args.values())
        return out
    for e in node_exprs(s):
        uses_of(e, out, regs_only)
    return out


def term_uses(t, out, regs_only=False):
    for e in node_exprs(t):
        uses_of(e, out, regs_only)
    return out


class Uses:
    """A ``set``-shaped sink that keeps every occurrence, not every name."""

    __slots__ = ("hits",)

    def __init__(self):
        self.hits = []

    def add(self, n):
        self.hits.append(n)

    def update(self, it):
        self.hits.extend(it)


def use_counts(proc):
    """``Counter`` of every use of every variable in ``proc`` (phi arguments included)."""
    t = Uses()
    for b in proc.blocks.values():
        for s in b.stmts:
            stmt_uses(s, t)
        term_uses(b.term, t)
    return Counter(t.hits)


def renamer(sub):
    """A :func:`sub_expr` function that replaces every ``Var`` name ``sub`` maps."""

    def fn(e):
        return sub.get(e.n, e) if type(e) is Var else e

    return fn


def single_defs(proc):
    """``{name: expression}`` for every name one ``Let`` and nothing else defines.

    Counted over :func:`defs_of`, which is the tree's definition relation: a name a
    ``Call`` also returns, or a ``Phi`` also joins, is defined twice.
    """
    out = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            for n in defs_of(s):
                out.setdefault(n, []).append(s)
    return {n: v[0].e for n, v in out.items() if len(v) == 1 and type(v[0]) is Let}


def unique_name(want, taken, sep="_"):
    """``want``, suffixed until it is not in ``taken``."""
    out, i = want, 2
    while out in taken:
        out, i = "%s%s%d" % (want, sep, i), i + 1
    return out


# ---- procedures and the call graph -------------------------------------------
def callees(proc):
    """The procedures one procedure calls directly, in program order."""
    return list(
        dict.fromkeys(s.proc for b in proc.blocks.values() for s in b.stmts if type(s) is Call)
    )


def call_order(prog):
    """Procedure names, callees before callers (the call graph is acyclic)."""
    out, seen = [], set()

    def visit(n):
        if n in seen or n not in prog.procs:
            return
        seen.add(n)
        for c in callees(prog.procs[n]):
            visit(c)
        out.append(n)

    for n in prog.procs:
        visit(n)
    return out


def reachable(prog, root):
    """The procedures ``root`` reaches through calls, itself included."""
    seen, work = set(), [root] if root in prog.procs else []
    while work:
        n = work.pop()
        if n in seen:
            continue
        seen.add(n)
        work.extend(c for c in callees(prog.procs[n]) if c in prog.procs)
    return seen


def forwarder(proc):
    """The procedure ``proc`` exists only to call, or ``None``."""
    stmts = [s for b in proc.blocks.values() for s in b.stmts]
    if len(proc.blocks) == 1 and len(stmts) == 1 and type(stmts[0]) is Call:
        return stmts[0].proc
    return None
