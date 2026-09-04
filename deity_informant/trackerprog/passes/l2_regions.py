"""Region formation for L2: the natural loops of one segment, kept as loops.

A block a tick runs several times is no statement stated once, so a segment's
blocks are a tree: a natural loop is a ``loop`` statement whose body is its own
blocks and whose trip is a value over the state the loop is entered with.
"""

from __future__ import annotations

from types import SimpleNamespace

from ...tuneprog.ir import If, Load, Store
from ...tuneprog.irwalk import addr_split, walk
from ..cells import ident
from ..read import Unlowerable
from ..rows import blockrows, guards
from ..shape import _reads
from .l2_loops import carried, closes, exits, loops, seeds, trip


def tree(low, p, blocks, order, rows_of, head=None):
    """One segment as a region tree: its loops kept, and its blocks in program order.

    The blocks between two loops are read as one run, so a guard that reads what
    another block's row takes away is staged against it (:func:`..rows.guards`).
    """
    inside, out, run, heads = set(), [], [], loops(p, blocks, head)
    for lbl in [l for l in order if l in blocks]:
        if lbl in inside:
            continue
        got = heads.get(lbl)
        n = trip(low, p, got[0], lbl, order) if got is not None else None
        if n is None:
            run.append(lbl)
            continue
        body = [l for l in order if l in got[0]]
        out += rows_of(set(run), run) if run else []
        # the turn's own test is what ``trip`` states: no row of the body carries it
        gate = low.gate
        low.gate = gate | {(id(c), t) for _l, c, t in exits(p, got[0])}
        keep = carried(low, p, got[0], got[1])
        out += _carry(low, seeds(low, p, got[0], lbl, keep))
        out.append(
            {"loop": {"trip": n, "body": rows_of(set(body), body) + _carry(low, closes(low, keep))}}
        )
        low.gate = gate
        inside |= set(body)
        run = []
    return out + (rows_of(set(run), run) if run else [])


def predicates(low, blocks):
    """One predicate cell a decision no row can read back, and no cell for any other.

    A block that decides a term and then moves a cell that term reads, or decides
    it over a name that is no cell, has no channel for the value it decided on:
    the decision is a cell.  Every other term is read at the site that decides it,
    where what it reads still stands -- a cell a tick did not assign holds the
    tick before's, which is no predicate.
    """
    out = {}
    for lbl in blocks:
        b = low.proc.blocks[lbl]
        if type(b.term) is not If or b.term.t == b.term.f:
            continue
        if _late(b, b.term.c) or _temped(low, lbl, b.term.c):
            out[lbl] = ("p" + ident(lbl), b.term.c, True)
    return out


def _temped(low, lbl, cond):
    """Whether a decision reads a value no cell of the object holds where a row reads it.

    A name one block binds is no cell, so a term over it is read at the block that
    decides it and nowhere else: the decision itself is the cell.
    """
    low.lbl, low.local, low.pick, low.sub = lbl, {}, {}, {}
    try:
        got = low.term(low.expand(cond), True)
    except Unlowerable:
        return True
    return bool(_reads(got) & {c.lstrip("#") for c in low.temps.values()})


def _late(blk, cond):
    """Whether a condition reads a cell of the block at the terminator, past its store.

    A name the block bound is the value it had where it was bound; a load the
    condition itself makes is the value the block leaves.
    """
    put = {addr_split(s.a)[0] for s in blk.stmts if type(s) is Store and s.cls == "ram"}
    return any(type(x) is Load and addr_split(x.a)[0] in put for x in walk(cond))


def guardof(low, terms):
    """One guard list read where it stands, each term the cell its decision left."""
    when = []
    for d, c, t in terms:
        if not low.onpath(d, c, t):
            continue
        low.lbl = d
        fact = low.v.terms.get(repr(c))
        term = [fact, "!=" if t else "==", 0] if fact is not None else low.term(low.expand(c), t)
        if term not in when:
            when.append(term)
    return when


def picks(amb, lbl, path):
    """A name several blocks bind takes the definition of the block on this path."""
    out = {}
    for n, d in amb.items():
        for q in [lbl] + list(path):
            if q in d:
                out[n] = d[q]
                break
    return out


def predrow(seg, lbl, name, cond, late=False):
    """The row one decision is: the block's own guard, and the cell it leaves it in."""
    low = seg.low
    path = [d for d, _c, _t, _w in low.guards.get(lbl, ())]
    low.lbl, low.local, low.sub, low.turn = lbl, {}, {}, None
    low.pick = picks(seg.amb, lbl, path)
    when = guardof(low, [(d, c, t) for d, c, t, _w in low.guards.get(lbl, ())])
    low.lbl = lbl
    got = {"sets": [["@" + name, low.value(low.expand(cond))]]}
    del late
    return {**({"when": when} if when else {}), **got}


def flagrows(low, lbl):
    """The rows one block raises for a join no path of the tick folds (B7's ``planall``).

    The reaching condition of a block two paths carry is a disjunction, which the
    one guard shape of §3.3 cannot state, so every path that reaches it raises a
    cell where that path already stands and the block's own guard reads it.  The
    cells are cleared once, at the head of the tick.
    """
    out = []
    for name, ctx in low.flagrows.get(lbl, ()):
        low.lbl, low.local, low.pick, low.sub, low.turn = lbl, {}, {}, {}, None
        out.append({"when": guardof(low, ctx[0]), "sets": [["@" + name, 1]]})
    return out


def raised(low, lbl):
    """The terms a block's own guard carries for a join no path of the tick folds."""
    return [list(t) for t in low.eff.get(lbl, ((), ()))[1]]


def _carry(low, got):
    """One row an assignment of a carried name is, in the cell its readers name."""
    out = []
    for n, val, w in got:
        c = low.temp(n, w)
        out.append({"sets": [[c if c[:1] == "#" else "@" + c, val]]})
    return out


def closing(seg, lbl, preds):
    """What one block leaves after its stores: the decision it made, and the flags."""
    got = preds.get(lbl)
    # a decision over a cell the block itself moved is read where the block ends:
    # read-after-write is the list's own order, not a second row
    return ([predrow(seg, lbl, *got)] if got is not None else []) + flagrows(seg.low, lbl)


def blockstmts(seg, blocks, order, ordering, preds):
    """One set of blocks as rows, their stores staged across the whole set.

    A guard that reads what another block's row takes away is read before it, so
    the staging is over the segment and not over one block: the row order is the
    one ``rows.guards`` puts them in, with each block's own decision after its
    last store and a block that stores nothing where program order puts it.
    """
    low = seg.low
    steps = [
        (lbl, {"when": when, "sets": [list(x) for x in sets]})
        for lbl, _kind, when, sets, _d in guards(
            seg, blockrows(seg, set(blocks), order, set(), {}), order
        )
        if sets
    ]
    last = {lbl: i for i, (lbl, _r) in enumerate(steps)}
    at = {l: i for i, l in enumerate(ordering)}
    quiet = [l for l in ordering if l in blocks and l not in last]
    out = []
    for i, (lbl, r) in enumerate(steps):
        while quiet and at.get(quiet[0], 0) < at.get(lbl, 0):
            q = quiet.pop(0)
            out += [(q, x) for x in closing(seg, q, preds)]
        out.append((lbl, r))
        if last[lbl] == i:
            out += [(lbl, x) for x in closing(seg, lbl, preds)]
    for lbl in quiet:
        out += [(lbl, x) for x in closing(seg, lbl, preds)]
    low.pick = {}
    got = []
    for lbl, r in out:
        up = raised(low, lbl)
        r["when"] = up + [t for t in (r.get("when") or []) if t not in up]
        got.append(r)
    return got


def segrows(seg, blocks, order, preds, p=None, head=None):
    """One segment as a region tree: its loops kept, its blocks in program order."""
    if p is not None:
        keep = {}
        for h, (b, lat) in loops(p, blocks, head).items():
            keep.update(carried(seg.low, p, b, lat))
        if keep:  # a name the level gives a cell is read there, not split per path
            seg = SimpleNamespace(
                low=seg.low, amb={n: d for n, d in seg.amb.items() if n not in keep}
            )

    def rows_of(bset, ordering):
        return blockstmts(seg, bset, order, ordering, preds)

    if p is None:
        return rows_of(blocks, order)
    return tree(seg.low, p, blocks, order, rows_of, head)
