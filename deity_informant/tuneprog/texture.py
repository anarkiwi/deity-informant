"""S6 -- machine texture removal over the presentation copy of the S4 IR.

Each pass rewrites the *view* only and is justified structurally (see each
function): empty-block threading, short-circuit conditions, pinned addresses,
single-definition values, switch merging. :mod:`.cells` holds the cell readings.
"""

from __future__ import annotations

from .cells import mirrors, stack_temps
from .frame import fresh, frames
from .gated import ranged
from .graph import preds_of
from .halves import zerofold
from .idioms import CMP, negated, bitfields
from .ir import (
    Bin,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    STACK_HI,
    STACK_LO,
    Store,
    Switch,
    Var,
    retarget,
    succs,
)
from .irwalk import (
    apply_stmt,
    apply_term,
    defs_of,
    loads,
    pure,
    renamer,
    sub_expr,
    use_counts,
)
from .ranges import cell_ranges
from .ssa import merge_chains, prune


# ---- empty-block threading ---------------------------------------------------
def thread_empty(proc):
    """Retarget every edge to a statement-free ``goto`` block at its destination."""
    changed, n = True, 0
    while changed:
        changed = False
        for lbl, b in list(proc.blocks.items()):
            if lbl == proc.entry or b.stmts or type(b.term) is not Goto or b.term.to == lbl:
                continue
            for other in proc.blocks.values():
                if lbl in succs(other.term):
                    other.term = retarget(other.term, lbl, b.term.to)
            del proc.blocks[lbl]
            changed, n = True, n + 1
    prune(proc)
    return n


# ---- short-circuit conditions ------------------------------------------------
def shortcircuit(proc, rounds=32):
    """``if a: X else: if b: X`` becomes ``if a or b: X`` (``and`` symmetrically).

    The merged test evaluates the second condition exactly where the CFG reached
    it, so the rewrite is the definition of the short-circuit operators.
    """
    n = 0
    for _ in range(rounds):
        preds, uses = preds_of(proc), use_counts(proc)
        hit = next(
            (
                h
                for lbl, b in proc.blocks.items()
                if type(b.term) is If
                for h in (_sc_pair(proc, lbl, b, preds, uses),)
                if h is not None
            ),
            None,
        )
        if hit is None:
            return n
        lbl, term, gone = hit
        proc.blocks[lbl].term = term
        del proc.blocks[gone]
        prune(proc)
        n += 1
    return n


def _sc_pair(proc, lbl, b, preds, uses):
    """``(label, merged terminator, dead block)`` when an arm is a shared test."""
    for arm, other, op in ((b.term.f, b.term.t, "or"), (b.term.t, b.term.f, "and")):
        c2 = _testonly(proc, arm, preds, uses) if arm != lbl and arm in proc.blocks else None
        if c2 is None:
            continue
        t2 = proc.blocks[arm].term
        for keep, drop, neg in ((t2.t, t2.f, False), (t2.f, t2.t, True)):
            if keep != other or drop in (arm, lbl):
                continue
            c = Bin(op, b.term.c, _not(c2) if neg else c2, 1)
            return lbl, (If(c, other, drop) if op == "or" else If(c, drop, other)), arm
    return None


def _testonly(proc, lbl, preds, uses):
    """The condition of a block that computes nothing but its own test."""
    b = proc.blocks[lbl]
    if type(b.term) is not If or len(preds[lbl]) != 1:
        return None
    vals = {}
    for s in b.stmts:
        if type(s) is not Let or not pure(s.e) or uses[s.n] != 1:
            return None
        vals[s.n] = s.e
    return sub_expr(b.term.c, lambda e: vals.get(e.n, e) if type(e) is Var else e)


def _not(c):
    return negated(c) if type(c) is Bin and c.op in CMP else Bin("==", c, Const(0), 1)


def zerocarry(prog):
    """:func:`~.halves.zerofold` over the view: the carries a chain leaves that are zero."""
    n = [0]

    def fn(e):
        out = zerofold(e)
        n[0] += out is not e
        return out

    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                apply_stmt(s, fn)
            apply_term(b.term, fn)
    return n[0]


# ---- single-definition values ------------------------------------------------
def propagate(proc, rounds=8):
    """Forward a name that has one definition in the procedure (copy/constant).

    Phi elimination leaves copies behind that S4's propagation, which runs before
    it, never sees; a single definition dominates every use, so forwarding it is
    the same rewrite one stage later.
    """
    n = 0
    for _ in range(rounds):
        defs = {}
        for b in proc.blocks.values():
            for s in b.stmts:
                for name in defs_of(s):
                    defs.setdefault(name, []).append(s.e if type(s) is Let else None)
        one = {k: v[0] for k, v in defs.items() if len(v) == 1 and v[0] is not None}
        sub = {
            k: e
            for k, e in one.items()
            if type(e) is Const or (type(e) is Var and e.n in one and type(one[e.n]) is not Load)
        }
        if not sub:
            return n
        fn = renamer(sub)
        hit = 0
        for b in proc.blocks.values():
            for s in b.stmts:
                before = repr(s)
                apply_stmt(s, fn)
                hit += repr(s) != before
            apply_term(b.term, fn)
        n += hit
        if not hit:
            return n
    return n


# ---- pinned addresses --------------------------------------------------------
def pin(prog):
    """An access whose observed envelope is exactly its width addresses one cell.

    The certified program asserts ``lo <= a`` and ``a + w - 1 <= hi`` on every
    access, so a ``w``-wide envelope pins the address to ``lo`` -- which is what
    takes the stack pointer out of a push and a pop.
    """
    n = [0]

    def fn(e):
        if type(e) is Load and type(e.a) is not Const and _pinned(e):
            n[0] += 1
            return Load(e.cls, Const(e.lo, 2), e.w, e.lo, e.hi, e.r)
        return e

    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Store and type(s.a) is not Const and _pinned(s):
                    s.a = Const(s.lo, 2)
                    n[0] += 1
                apply_stmt(s, fn)
            apply_term(b.term, fn)
    return n[0]


def _pinned(x):
    """A stack access whose envelope is one slot: the frame pointer is not data."""
    return x.hi - x.lo + 1 == x.w and STACK_LO <= x.lo <= STACK_HI


# ---- switch merging ----------------------------------------------------------
def merge_switches(proc):
    """Two switches on one value with a straight line between them become one.

    The line is copied into each arm, which is the case-split of a sequence when
    no arm and no copied statement writes the selector.
    """
    n = 0
    for lbl in list(proc.blocks):
        b = proc.blocks.get(lbl)
        if b is None or type(b.term) is not Switch:
            continue
        mid = _joint(proc, b.term)
        if mid is None or not _stable(proc, b.term, mid):
            continue
        pairs = dict(proc.blocks[mid].term.cases)
        for v, arm in b.term.cases:
            proc.blocks[arm].stmts.extend(_copy_stmt(s) for s in proc.blocks[mid].stmts)
            proc.blocks[arm].term = Goto(pairs[v])
        del proc.blocks[mid]
        prune(proc)
        merge_chains(proc)
        n += 1
    return n


def _joint(proc, term):
    """The switch block every arm of ``term`` flows straight into."""
    outs = set()
    for _v, arm in term.cases:
        b = proc.blocks.get(arm)
        if b is None or type(b.term) is not Goto:
            return None
        outs.add(b.term.to)
    if len(outs) != 1 or term.default:
        return None
    mid = outs.pop()
    m = proc.blocks.get(mid)
    if m is None or type(m.term) is not Switch or m.term.default:
        return None
    if {v for v, _ in term.cases} != {v for v, _ in m.term.cases}:
        return None
    return mid if set(preds_of(proc)[mid]) == {a for _v, a in term.cases} else None


def _stable(proc, term, mid):
    """The two selectors are the same value and nothing between them writes it."""
    if proc.blocks[mid].term.e != term.e:
        return False
    rs = {x.r for x in loads(term.e)}
    for name in [a for _v, a in term.cases] + [mid]:
        for s in proc.blocks[name].stmts:
            if type(s) is Call or (type(s) is Store and (s.r in rs or s.r < 0)):
                return False
    return True


def _copy_stmt(s):
    t = type(s)
    if t is Let:
        return Let(s.n, s.e)
    if t is Store:
        return Store(s.cls, s.a, s.v, s.w, s.lo, s.hi, s.r, s.src)
    return Call(s.proc, s.args, s.rets) if t is Call else s


def tidy(prog):
    """Drop the jump blocks the later passes emptied."""
    for p in prog.procs.values():
        thread_empty(p)
    return prog


def clean(prog, frameinfo=None):
    """Every texture pass over a presentation copy of ``prog``.

    ``frameinfo`` is :func:`~.frame.deltas` of the certified program, whose stack
    arithmetic the view has already dropped.
    """
    zerocarry(prog)
    make = fresh(prog)
    frames(prog, frameinfo, make)
    for p in prog.procs.values():
        bitfields(p)
    pin(prog)
    mirrors(prog)
    stack_temps(prog, make)
    mem = cell_ranges(prog)
    for p in prog.procs.values():
        propagate(p)
        ranged(p, mem)
        thread_empty(p)
        merge_switches(p)
        shortcircuit(p)
        merge_chains(p)
        thread_empty(p)
    return prog
