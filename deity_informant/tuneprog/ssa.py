"""S4 -- SSA over registers, flags and uniques, then DCE and copy/constant propagation.

Memory stays in program order (no memory SSA, design section 5 S4): only the
register/flag values and the lifter's uniques are renamed. Uniques are already
single-assignment (:mod:`.build` names them per block), so phi nodes appear only
for the 6510 register file, and pruned insertion (a phi only where the variable
is live) keeps that to the handful of values that really cross a branch.

Passes, each semantics-preserving with :class:`~.ir.Interp` as the oracle:

* :func:`merge_chains` -- glue single-successor/single-predecessor runs;
* :func:`to_ssa` / :func:`from_ssa` -- dominance-frontier phi insertion and
  renaming, then phi elimination through copies on split critical edges;
* :func:`copyprop` -- forward a ``let v = w``;
* :func:`constprop` -- forward a ``let v = k`` and fold a load of a read-only
  region at a known address into its byte (``const`` everywhere, ``init_constant``
  in the procedures ``init`` never reaches -- see :func:`simplify`);
* :func:`fold_branches` -- a constant test becomes a jump and its dead arm goes;
* :func:`dce` -- drop values nobody reads (the bulk of the P-Code flag ops); a
  load that can consume a pinned input is never dropped;
* :func:`canonical` -- fix the block order the JSON records.

:func:`simplify` is the S4 driver: it runs the passes to a fixpoint, with an
optional peephole hook (:mod:`.idioms`).
"""

from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx

from .ir import (
    Assert,
    Bin,
    Block,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    Phi,
    REGIDX,
    Return,
    Store,
    Switch,
    Trap,
    Var,
    retarget,
    succs,
)

IMPURE = ("io", "chk")


# ---- expression and statement plumbing --------------------------------------
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
    return fn(e)


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


def defs_of(s):
    t = type(s)
    if t is Let or t is Phi:
        return (s.n,)
    return s.rets if t is Call else ()


def uses_of(e, out, regs_only=False):
    t = type(e)
    if t is Var:
        if not regs_only or e.n in REGIDX:
            out.add(e.n)
    elif t is Bin:
        uses_of(e.a, out, regs_only)
        uses_of(e.b, out, regs_only)
    elif t is Load:
        uses_of(e.a, out, regs_only)
    return out


def stmt_uses(s, out, regs_only=False):
    """Collect the names a statement reads (phi arguments belong to its predecessors)."""
    t = type(s)
    if t is Let or t is Assert:
        uses_of(s.e, out, regs_only)
    elif t is Store:
        uses_of(s.a, out, regs_only)
        uses_of(s.v, out, regs_only)
    elif t is Call:
        for a in s.args:
            uses_of(a, out, regs_only)
    elif t is Phi and not regs_only:
        out.update(s.args.values())
    return out


def term_uses(t, out, regs_only=False):
    k = type(t)
    if k is If:
        uses_of(t.c, out, regs_only)
    elif k is Switch:
        uses_of(t.e, out, regs_only)
    elif k is Return:
        for v in t.vals:
            uses_of(v, out, regs_only)
    return out


class _Tally:
    """A ``set``-shaped sink that counts instead of collecting."""

    __slots__ = ("c",)

    def __init__(self):
        self.c = Counter()

    def add(self, n):
        self.c[n] += 1

    def update(self, it):
        for n in it:
            self.c[n] += 1


def use_counts(proc):
    """``Counter`` of every use of every variable in ``proc`` (phi arguments included)."""
    t = _Tally()
    for b in proc.blocks.values():
        for s in b.stmts:
            stmt_uses(s, t)
        term_uses(b.term, t)
    return t.c


def pure(e):
    """True when evaluating ``e`` has no effect (a pinned input read has one)."""
    t = type(e)
    if t is Load:
        return e.cls not in IMPURE and pure(e.a)
    if t is Bin:
        return pure(e.a) and pure(e.b)
    return True


def preds_of(proc):
    preds = {lbl: [] for lbl in proc.blocks}
    for lbl, b in proc.blocks.items():
        for s in succs(b.term):
            preds[s].append(lbl)
    return preds


def liveness(proc):
    """``{label: live-in register names}`` (registers only: uniques never cross)."""
    preds = preds_of(proc)
    live = {lbl: set() for lbl in proc.blocks}
    work = list(proc.blocks)
    while work:
        lbl = work.pop()
        b = proc.blocks[lbl]
        cur = set()
        for s in succs(b.term):
            cur |= live[s]
        term_uses(b.term, cur, True)
        for s in reversed(b.stmts):
            cur.difference_update(defs_of(s))
            stmt_uses(s, cur, True)
        if cur != live[lbl]:
            live[lbl] = cur
            work.extend(preds[lbl])
    return live


# ---- CFG shaping -------------------------------------------------------------
def prune(proc):
    """Drop blocks unreachable from the entry (and phi arguments from dead edges)."""
    keep = set(proc.order())
    gone = [l for l in proc.blocks if l not in keep]
    for lbl in gone:
        del proc.blocks[lbl]
    if gone:
        for b in proc.blocks.values():
            for s in b.stmts:
                if type(s) is Phi:
                    s.args = {p: v for p, v in s.args.items() if p in keep}
    return proc


def fold_branches(proc):
    """A terminator whose test is constant becomes a jump; the arm it never takes dies."""
    n = 0
    for b in proc.blocks.values():
        t = b.term
        if type(t) is If and type(t.c) is Const:
            b.term = Goto(t.t if t.c.v else t.f)
            n += 1
        elif type(t) is Switch and type(t.e) is Const:
            hit = [l for v, l in t.cases if v == t.e.v]
            b.term = Goto(hit[0]) if hit else Trap("switch")
            n += 1
    if n:
        prune(proc)
    return n


def merge_chains(proc):
    """Glue a ``goto`` into its target when that target has this block as its only entry."""
    prune(proc)
    preds = preds_of(proc)
    for lbl in list(proc.order()):
        b = proc.blocks.get(lbl)
        while b is not None and type(b.term) is Goto:
            t = b.term.to
            nxt = proc.blocks.get(t)
            if nxt is None or t == proc.entry or preds[t] != [lbl] or t == lbl:
                break
            b.stmts.extend(nxt.stmts)
            b.term = nxt.term
            del proc.blocks[t]
            for s in succs(b.term):
                preds[s] = [lbl if p == t else p for p in preds.get(s, [])]
    return proc


def split_critical(proc):
    """Split every edge from a multi-way block into a multi-entry block."""
    preds = preds_of(proc)
    for lbl in list(proc.blocks):
        b = proc.blocks[lbl]
        for s in sorted(set(succs(b.term))):
            if len(succs(b.term)) > 1 and len(preds[s]) > 1:
                mid = "%s$%s" % (lbl, s)
                proc.blocks[mid] = Block(mid, [], Goto(s), b.src)
                b.term = retarget(b.term, s, mid)
    return proc


# ---- SSA ---------------------------------------------------------------------
def _frontiers(proc, preds):
    g = nx.DiGraph()
    g.add_nodes_from(proc.blocks)
    for lbl, b in proc.blocks.items():
        g.add_edges_from((lbl, s) for s in succs(b.term))
    idom = nx.immediate_dominators(g, proc.entry)
    df = defaultdict(set)
    for lbl, ps in preds.items():
        if len(ps) < 2:
            continue
        for p in ps:
            while p != idom[lbl] and p in idom:
                df[p].add(lbl)
                if p == idom[p]:
                    break
                p = idom[p]
    children = defaultdict(list)
    for n, d in idom.items():
        if n != d:
            children[d].append(n)
    return df, children


def to_ssa(proc):
    """Insert pruned phis on the dominance frontiers and rename to SSA names."""
    prune(proc)
    preds = preds_of(proc)
    df, children = _frontiers(proc, preds)
    live = liveness(proc)
    sites = defaultdict(set)
    for lbl, b in proc.blocks.items():
        for s in b.stmts:
            for d in defs_of(s):
                if d in REGIDX:
                    sites[d].add(lbl)
    for v, blocks in sites.items():
        work, placed = list(blocks), set()
        while work:
            x = work.pop()
            for y in df.get(x, ()):
                if y in placed or v not in live[y]:
                    continue
                placed.add(y)
                proc.blocks[y].stmts.insert(0, Phi(v, {}))
                if y not in blocks:
                    work.append(y)
    _rename(proc, children, preds)
    return proc


def _rename(proc, children, preds):
    stack = defaultdict(list)
    count = defaultdict(int)

    def fresh(n):
        count[n] += 1
        return "%s#%d" % (n, count[n])

    def top(n):
        s = stack[n]
        return s[-1] if s else n

    def ren(e):
        return Var(top(e.n), e.w) if type(e) is Var else e

    work = [(proc.entry, False)]
    while work:
        lbl, done = work.pop()
        b = proc.blocks[lbl]
        if done:
            for s in b.stmts:
                for d in defs_of(s):
                    stack[d.split("#")[0]].pop()
            continue
        work.append((lbl, True))
        for s in b.stmts:
            if type(s) is not Phi:
                apply_stmt(s, ren)
            if type(s) is Call:
                s.rets = tuple(_push(stack, fresh, n) for n in s.rets)
            else:
                for d in defs_of(s):
                    s.n = _push(stack, fresh, d)
        apply_term(b.term, ren)
        for t in succs(b.term):
            for s in proc.blocks[t].stmts:
                if type(s) is Phi:
                    s.args[lbl] = top(s.n.split("#")[0])
        work.extend((c, False) for c in children.get(lbl, ()))


def _push(stack, fresh, n):
    v = fresh(n)
    stack[n].append(v)
    return v


def from_ssa(proc):
    """Replace phis with copies at the end of each predecessor.

    The copies are sequential, so they go through temporaries only when this
    predecessor's phi sources include a phi destination (the swap case).
    """
    for lbl, b in proc.blocks.items():
        phis = [s for s in b.stmts if type(s) is Phi]
        if not phis:
            continue
        b.stmts = [s for s in b.stmts if type(s) is not Phi]
        for p in dict.fromkeys(q for s in phis for q in s.args):
            pb = proc.blocks[p]
            if {s.n for s in phis} & {s.args[p] for s in phis}:
                tmp = [("%s$t%d" % (lbl, i), s) for i, s in enumerate(phis)]
                pb.stmts.extend(Let(t, Var(s.args[p])) for t, s in tmp)
                pb.stmts.extend(Let(s.n, Var(t)) for t, s in tmp)
            else:
                pb.stmts.extend(Let(s.n, Var(s.args[p])) for s in phis)
    return proc


# ---- optimisation ------------------------------------------------------------
def _used(proc):
    out = set()
    for b in proc.blocks.values():
        for s in b.stmts:
            stmt_uses(s, out)
        term_uses(b.term, out)
    return out


def dce(proc):
    """Drop values nobody reads; returns the number of statements removed."""
    n = 0
    while True:
        used = _used(proc)
        gone = 0
        for b in proc.blocks.values():
            keep = [
                s
                for s in b.stmts
                if not (
                    (type(s) is Let and s.n not in used and pure(s.e))
                    or (type(s) is Phi and s.n not in used)
                )
            ]
            gone += len(b.stmts) - len(keep)
            b.stmts = keep
        n += gone
        if not gone:
            return n


def _forward(proc, want):
    """Replace uses of ``let v = e`` (for the ``e`` ``want`` accepts) by ``e``."""
    sub = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is Let and want(s.e):
                sub[s.n] = s.e
    if not sub:
        return 0
    changed = True
    while changed:  # chains: v = w, w = x
        changed = False
        for k, e in list(sub.items()):
            if type(e) is Var and e.n in sub and sub[e.n] is not e:
                sub[k] = sub[e.n]
                changed = True

    def fn(e):
        r = sub.get(e.n) if type(e) is Var else None
        return e if r is None else r

    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
            if type(s) is Phi:
                s.args = {
                    p: (sub[v].n if type(sub.get(v)) is Var else v) for p, v in s.args.items()
                }
        apply_term(b.term, fn)
    return len(sub)


def copyprop(proc):
    """Forward ``let v = w``."""
    return _forward(proc, lambda e: type(e) is Var)


def const_tables(storage):
    """``{region id: (base, bytes)}`` of the read-only regions a known-address load folds."""
    return {r.id: (r.base, r.init) for r in storage if r.kind == "const"}


class Folds:
    """What a load at a known address folds to once ``init(song)`` has run.

    A byte folds when it is an SMC cell (an instruction byte some traced procedure
    writes -- an ordinary init-written variable keeps its load, and its name) or a
    neighbour of one inside the same access, and when no play-time store ever
    changes it. Design S2: "cells patched only by init are constants as far as the
    tick code is concerned"; :func:`simplify` applies it only outside init, where
    the value exists.
    """

    __slots__ = ("post", "cells", "mutable")

    def __init__(self, post, cells, mutable):
        self.post = post
        self.cells = frozenset(cells)
        self.mutable = frozenset(mutable)

    def at(self, addr, w):
        """The little-endian constant of the ``w`` bytes at ``addr``, or ``None``."""
        rng = range(addr, addr + w)
        if self.mutable.intersection(rng) or not self.cells.intersection(rng):
            return None
        return int.from_bytes(bytes(self.post[a] for a in rng), "little")


def init_reachable(prog):
    """Names of the procedures ``init`` can reach through the call graph."""
    start = prog.meta.get("init_proc")
    seen = set()
    work = [start] if start in prog.procs else []
    while work:
        n = work.pop()
        if n in seen:
            continue
        seen.add(n)
        work.extend(
            s.proc
            for b in prog.procs[n].blocks.values()
            for s in b.stmts
            if type(s) is Call and s.proc in prog.procs
        )
    return seen


def constprop(proc, tables=None, folds=None):
    """Fold known-address loads of ``tables``/``folds``, then forward ``let v = k``."""
    hits = [0]
    if tables or folds is not None:

        def fold(e):
            if type(e) is not Load or type(e.a) is not Const:
                return e
            hit = (tables or {}).get(e.r)
            if hit is not None:
                base, data = hit
                off = e.a.v - base
                if 0 <= off and off + e.w <= len(data):
                    hits[0] += 1
                    return Const(int.from_bytes(data[off : off + e.w], "little"), e.w)
            v = folds.at(e.a.v, e.w) if folds is not None and e.cls != "io" else None
            if v is None:
                return e
            hits[0] += 1
            return Const(v, e.w)

        for b in proc.blocks.values():
            for s in b.stmts:
                apply_stmt(s, fold)
            apply_term(b.term, fold)
    return hits[0] + _forward(proc, lambda e: type(e) is Const)


def canonical(proc):
    """Order the blocks reachable-first in reverse postorder, the rest by label.

    Block order is presentation, but it is what the emitted JSON records, so it is
    fixed here rather than left to the order the passes happened to build.
    """
    order = proc.order()
    rest = sorted(set(proc.blocks) - set(order))
    proc.blocks = {l: proc.blocks[l] for l in order + rest}
    return proc


def simplify(prog, peephole=None, rounds=8, folds=None):
    """The S4 pipeline over every procedure: SSA, passes to a fixpoint, out of SSA.

    With ``folds`` (a :class:`Folds`) the procedures ``init`` never reaches fold
    their loads of init-patched instruction bytes to the constants ``init(song)``
    left there; inside init the same loads stay, because the value is the store's.
    """
    inits = init_reachable(prog)
    tables = const_tables(prog.storage)
    for name, p in prog.procs.items():
        f = None if name in inits else folds
        merge_chains(p)
        split_critical(p)
        to_ssa(p)
        for _ in range(rounds):
            n = copyprop(p) + constprop(p, tables, f) + fold_branches(p) + dce(p)
            if peephole is not None:
                n += peephole(p)
            if not n:
                break
        from_ssa(p)
        merge_chains(p)
        canonical(p)
    prog.meta["stage"] = "S4"
    return prog
