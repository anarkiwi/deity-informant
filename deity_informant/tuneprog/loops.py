"""S5 -- the loop domain: what a counted loop's index runs over.

Constant folding over SSA names and byte cells (:class:`_Values`), a loop's exit
tests, and the two ways an index gets a domain: a recurrence, or a cover vector.
"""

from __future__ import annotations

from math import gcd

from .ir import (
    Bin,
    COPYVAR,
    Call,
    Const,
    If,
    Let,
    Load,
    Store,
    Switch,
    Trap,
    Var,
    copyval,
    evalbin,
    succs,
)
from .graph import preds_of

CAP = 256  # how far a recurrence is iterated before its domain is refused


class _Values:
    """Constant folding over a procedure: SSA names, and cells through their stores.

    A name folds when every definition agrees; a cell when every store reaching
    the load agrees, which carries an index through a save/restore pair.
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


def leaves(proc, body, lbl, skip=()):
    """The successors of ``lbl`` that leave the loop for a path the trace took.

    ``skip`` are labels no covered path reaches (:func:`~.closure.closed_blocks`):
    a closed arm is a leaf of the covered program, never one of its exits.
    """
    return [
        s
        for s in succs(proc.blocks[lbl].term)
        if s not in body and s not in skip and not _dead(proc, s)
    ]


def _dead(proc, lbl):
    return type(proc.blocks[lbl].term) is Trap


def _exit_tests(proc, body, skip=()):
    """``[(condition, the truth of it that exits)]`` for every edge leaving the loop."""
    out = []
    for lbl in sorted(body):
        t = proc.blocks[lbl].term
        if type(t) is not If:
            continue
        outs = leaves(proc, body, lbl, skip)
        at = (lbl, len(proc.blocks[lbl].stmts))
        if t.t in outs:
            out.append((t.c, True, at))
        elif t.f in outs:
            out.append((t.c, False, at))
    return out


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


def copies(proc, header, latches, body=(), preds=None):
    """``(index, k)`` when the loop runs a merged family's copies, else ``None``.

    The index is a value the merge made and its domain the coverage vector the
    correspondence proved; a latch steps it, or the prologues name it.
    """
    k = len(proc.blocks[header].cover or ())
    if k < 2:
        return None
    var = next(
        (
            s.n
            for l in sorted(latches)
            for s in proc.blocks[l].stmts
            if type(s) is Let and s.n.startswith(COPYVAR) and type(s.e) is Var
        ),
        None,
    )
    return (var, k) if var is not None else _chain(proc, header, latches, body, preds, k)


def _named(blk):
    """``{copy index name: value}`` for every index the block last sets to a constant."""
    return {s.n: s.e.v for s in blk.stmts if type(s) is Let and copyval(s.n) and type(s.e) is Const}


def _after(proc, body, name, val):
    """The edges a ``switch`` on the copy index takes when it holds ``val``."""
    out = set()
    for lbl in body:
        t = proc.blocks[lbl].term
        if type(t) is Switch and type(t.e) is Var and t.e.n == name:
            out |= {(lbl, w) for v, w in t.cases if v == val}
    return out


def _ordered(proc, header, body, name, latch, val):
    """True when the loop reaches ``latch`` only by a ``switch`` arm for copy ``val``.

    That is what makes the copies an order and not a set: the edge naming the
    next copy is taken where the index holds this one, and nowhere else.
    """
    cut = _after(proc, body, name, val)
    if not cut:
        return False
    seen, work = {header}, [header]
    while work:
        lbl = work.pop()
        for s in succs(proc.blocks[lbl].term):
            if (lbl, s) in cut or s not in body:
                continue
            if s == latch:
                return False
            if s not in seen:
                seen.add(s)
                work.append(s)
    return True


def _chain(proc, header, latches, body, preds, k):
    """``(index, k)`` when k prologues name the copies of one family, else ``None``.

    A prologue apiece steps the index by naming it: copy 0 from outside the
    loop, copies 1..k-1 on the back edges once each and only where the index
    holds the one before, each edge running that copy's share of the cover.
    """
    if preds is None or proc.blocks[header].count != sum(proc.blocks[header].cover):
        return None
    cover = tuple(proc.blocks[header].cover)
    outs = [p for p in preds[header] if p not in body]
    if not outs or not all(cover) or any(p not in body for l in latches for p in preds[l]):
        return None  # every copy has an entry and ran: an untaken edge is no iteration
    ins = sorted(set(latches) | set(outs))
    sets = {l: _named(proc.blocks[l]) for l in ins}
    shared = set.intersection(*(set(s) for s in sets.values())) if sets else set()
    for name in sorted(shared):
        back = {sets[l][name]: l for l in latches}
        if any(sets[l][name] for l in ins if l not in latches):
            continue
        if len(back) != len(latches) or sorted(back) != list(range(1, k)):
            continue
        if any(proc.blocks[back[j]].count != cover[j] for j in back):
            continue
        if all(_ordered(proc, header, body, name, back[j], j - 1) for j in back):
            return name, k
    return None


def stepping(proc, latches, var):
    """The stepping lets a ``for`` header states: the index and every latch's step.

    A copy index is machinery in the whole procedure -- the prologue that enters
    the family at copy 0 is the header's own ``0``.
    """
    out = {var}
    if copyval(var):
        base = var.split("#")[0]
        out |= {
            s.n
            for b in proc.blocks.values()
            for s in b.stmts
            if type(s) is Let and s.n.split("#")[0] == base
        }
    for l in latches:
        out |= {
            s.e.n
            for s in proc.blocks[l].stmts
            if type(s) is Let and s.n == var and type(s.e) is Var
        }
    return out


def induction(proc, header, body, latches, preds=None, skip=()):
    """``(var, values, scale, hide)`` when the loop is counted, else ``None``."""
    preds = preds_of(proc) if preds is None else preds
    tests = _exit_tests(proc, body, skip)
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
            if not vals or len(vals) < 2:
                continue
            if _plausible(proc, header, body, latches, preds, len(vals)):
                scale = 0
                for v in vals:
                    scale = gcd(scale, v)
                return s.n, tuple(vals), scale or 1, frozenset(stepping(proc, latches, s.n))
    return None
