"""T1 -- the guards a store stands under, and the cells a copy loop rewrites.

Control dependence with the back edges taken out, the epoch a condition read and
the scratch a tick writes once per copy: what :mod:`.accshape` builds a clause's
guard path and its value out of.
"""

from __future__ import annotations

from collections import namedtuple

from .graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of
from .ir import Bin, Call, If, Load, R16, Store, Var, W16, succs
from .irwalk import addr_split, single_defs, walk
from .nodes import At

DEPTH = 3  # call frames a value is chased through before it is left as it stands
EMPTY = frozenset()
Tgt = namedtuple("Tgt", "kind cells")


def key_of(s):
    """The cell a store writes: one byte, or the two of a 16-bit assignment."""
    if type(s) is W16:
        return Tgt("pair", (tuple(s.lo), tuple(s.hi)))
    base = addr_split(s.a)[0]
    return None if base is None else Tgt("byte", ((s.r, base),))


# ---- dominating guards --------------------------------------------------------
def _domsets(idom, blocks):
    """``{label: its dominators, nearest first}``, from the immediate-dominator tree."""
    out = {}
    for lbl in blocks:
        chain, cur = [], lbl
        while cur is not None and cur not in chain:
            chain.append(cur)
            nxt = idom.get(cur)
            cur = None if nxt == cur else nxt
        out[lbl] = chain
    return out


def afterwrites(proc):
    """``{label: the regions the blocks a branch leads to write}``.

    What a condition read is what those writes had not yet changed, which is the
    only epoch :mod:`.history` keeps of a cell its own tick moves.
    """
    here = {
        lbl: frozenset(
            r
            for s in b.stmts
            for r in ((s.lo[0], s.hi[0]) if type(s) is W16 else (s.r,) if type(s) is Store else ())
            if r >= 0
        )
        for lbl, b in proc.blocks.items()
    }
    out = {lbl: here[lbl] for lbl in proc.blocks}
    for _ in range(len(proc.blocks)):
        moved = False
        for lbl, b in proc.blocks.items():
            got = out[lbl].union(*[out[s] for s in succs(b.term) if s in out] or [frozenset()])
            moved = moved or got != out[lbl]
            out[lbl] = got
        if not moved:
            break
    return {
        lbl: frozenset().union(*[out[s] for s in succs(b.term) if s in out] or [frozenset()])
        - here[lbl]
        for lbl, b in proc.blocks.items()
    }


def guardpath(proc, sites=False):
    """``{label: ((condition, its truth here, the regions written after it), ...)}``.

    With ``sites`` each entry also carries the label of the block whose branch
    decides it, first.

    Control dependence, not dominance: a block a join carries is reached either
    way, however the join itself is dominated. Outermost condition first.

    A loop's exit test is not a guard on the body that precedes it: that block ran
    before the test, and its dependence on it runs through the back edge -- one
    more iteration, not this one. Keeping it puts the last iteration's own index in
    the guards of every store a rerolled loop makes. A test that leaves the
    procedure outright (``if c: return``) guards every block after it.
    """
    g = cfg(proc)
    after = afterwrites(proc)
    ipd = postdoms(g, proc, EXIT)
    pd = _domsets(ipd, [n for n in ipd if n in proc.blocks])
    pd[EXIT] = [EXIT]  # a branch straight out of the procedure guards all that follows it
    idom = idoms(proc, g)
    inloop = _inloop(natural_loops(g, idom, preds_of(proc)))
    dom, out = _domsets(idom, proc.blocks), {lbl: [] for lbl in proc.blocks}
    for d, b in proc.blocks.items():
        t = b.term
        if type(t) is not If or t.t == t.f or t.t not in pd or t.f not in pd:
            continue
        for lbl in proc.blocks:
            if lbl in pd.get(d, ()) or _viaback(d, lbl, inloop, dom):
                continue
            if lbl in pd[t.t]:
                out[lbl].append((d, t.c, True))
            elif lbl in pd[t.f]:
                out[lbl].append((d, t.c, False))
    for _ in range(len(proc.blocks)):
        moved = False
        for lbl, gs in out.items():
            got = list(gs) + [x for d, _c, _v in gs for x in out[d] if x not in gs]
            moved = moved or len(got) != len(gs)
            out[lbl] = list(dict.fromkeys(got))
        if not moved:
            break
    depth = {lbl: len(dom[lbl]) for lbl in proc.blocks}
    return {
        lbl: tuple(
            ((d, c, v, after[d]) if sites else (c, v, after[d]))
            for d, c, v in sorted(gs, key=lambda x: depth.get(x[0], 0))
        )
        for lbl, gs in out.items()
    }


def _inloop(loops):
    """``{label: the headers whose body holds it}``."""
    out = {}
    for h, (body, _back) in loops.items():
        for lbl in body:
            out.setdefault(lbl, set()).add(h)
    return out


def _viaback(d, lbl, inloop, dom):
    """True when the only path from a test to a block of its own loop is the back edge."""
    return bool(inloop.get(d, EMPTY) & inloop.get(lbl, EMPTY)) and d not in dom.get(lbl, ())


def cellof(e):
    """``(region, constant address)`` of a byte read, or ``None``."""
    if type(e) is Load and e.w == 1:
        base = addr_split(e.a)[0]
        return None if base is None else (e.r, base)
    return None


def valnames(e):
    """Every name an expression reads, its addresses included."""
    return {x.n for x in walk(e) if type(x) is Var}


def reads(e):
    """Every byte or pair a value reads, addresses excluded."""
    xs = [x for x in walk(e) if type(x) is Load or type(x) is R16]
    inner = {id(y) for x in xs for y in walk(x.a)}
    return [x for x in xs if id(x) not in inner]


def scratch(prog):
    """``{cell}`` a copy loop rewrites: one column, one value per iteration.

    A cell a loop body stores at a constant address holds each iteration's own
    value in turn, and :mod:`.history` keeps only the last -- so a condition on it
    says which copy the block ran for, not whether the tick ran it at all. The
    body includes what it calls: a per-voice procedure's own scratch is rewritten
    once a copy however deep the call sits.
    """
    out, seeds = set(), []
    for p in prog.procs.values():
        g = cfg(p)
        body = {lbl for b, _l in natural_loops(g, idoms(p, g), preds_of(p)).values() for lbl in b}
        for lbl in body:
            out |= _consts(p.blocks[lbl])
            seeds += [st.proc for st in p.blocks[lbl].stmts if type(st) is Call]
    seen = set()
    while seeds:
        name = seeds.pop()
        if name in seen or name not in prog.procs:
            continue
        seen.add(name)
        for b in prog.procs[name].blocks.values():
            out |= _consts(b)
            seeds += [st.proc for st in b.stmts if type(st) is Call]
    return out


def _consts(blk):
    """The cells one block stores at an address nothing indexes: one column, every copy.

    An index is a copy selector whether a register or a cell carries it -- a record
    a cursor picks is a copy of its own and keeps its own column, so only a store
    with no index at all writes the one cell every copy shares.
    """
    out = set()
    for st in blk.stmts:
        k = key_of(st) if type(st) is Store and st.r >= 0 else None
        if k is not None and not valnames(st.a) and addr_split(st.a)[1] is None:
            out.add(k.cells[0])
    return out


def propagate(prog, cells):
    """``{cell: the expression its one store gives it}`` for the scratch of ``cells``.

    A value a tick parks in a scratch cell to carry it across a call has no column
    of its own -- :mod:`.history` keeps the last copy's -- so T1 reads it as the
    expression that put it there, whose own reads are indexed by the copy.
    """
    hits, out = {}, {}
    for p in prog.procs.values():
        defs = single_defs(p)
        for b in p.blocks.values():
            for s in b.stmts:
                k = key_of(s) if type(s) is Store and s.r >= 0 else None
                if k is not None and k.cells[0] in cells:
                    hits.setdefault(k.cells[0], []).append((s.v, defs))
    for key, got in hits.items():
        if len(got) != 1:
            continue
        e = opened(got[0][0], got[0][1])
        if not any(cellof(x) == key for x in walk(e)):
            out[key] = e
    return out


def opened(e, defs, depth=DEPTH, prop=EMPTY, sites=None):
    """``e`` with every name ``defs`` maps substituted, addresses included.

    Not :func:`~.provenance.expand`, which stops at a named cell and so leaves a
    table read's own index a register: T1 evaluates that index over the horizon.
    ``prop`` are the scratch cells :func:`~.accguard.propagate` reads as the one
    expression that fills them, which is the only reading their column supports.
    ``sites`` maps a name to its definition's site, and pins every read of the
    substituted definition to it (:class:`~.nodes.At`): the epoch an exact reader
    gives the read is the definition's, not the use's.
    """
    t = type(e)
    if t is Var and depth > 0 and e.n in defs:
        got = opened(defs[e.n], defs, depth - 1, prop, sites)
        return pin(got, sites[e.n]) if sites and e.n in sites else got
    if t is Bin:
        a, b = opened(e.a, defs, depth, prop, sites), opened(e.b, defs, depth, prop, sites)
        return Bin(e.op, a, b, e.w)
    if t is Load:
        got = prop.get(cellof(e)) if prop and depth > 0 else None
        if got is not None:
            return opened(got, defs, depth - 1, prop, sites)
        return Load(e.cls, opened(e.a, defs, depth, prop, sites), e.w, e.lo, e.hi, e.r)
    if t is At:
        return At(opened(e.e, defs, depth, prop, sites), e.site, e.via)
    return R16(e.lo, e.hi, opened(e.a, defs, depth, prop, sites)) if t is R16 else e


def pin(e, site):
    """Every memory read of ``e`` marked as read at ``site``, reads already placed kept."""
    t = type(e)
    if t is Load or t is R16:
        return At(e, site)
    if t is Bin:
        return Bin(e.op, pin(e.a, site), pin(e.b, site), e.w)
    return e


def unpin(e, keep):
    """``e`` with the placed reads ``keep`` admits unplaced: the target's own, for :func:`~.accshape.step`."""
    t = type(e)
    if t is At:
        return e.e if keep(e.e) else At(unpin(e.e, keep), e.site, e.via)
    if t is Bin:
        return Bin(e.op, unpin(e.a, keep), unpin(e.b, keep), e.w)
    if t is Load:
        return Load(e.cls, unpin(e.a, keep), e.w, e.lo, e.hi, e.r)
    return R16(e.lo, e.hi, unpin(e.a, keep)) if t is R16 else e
