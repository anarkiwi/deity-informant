"""Region formation for L2: the natural loops of one segment, kept as loops.

A block a tick runs several times is no statement stated once, so a segment's
blocks are a tree: a natural loop is a ``loop`` statement whose body is its own
blocks and whose trip is a value over the state the loop is entered with.
"""

from __future__ import annotations

from ...tuneprog.graph import cfg, idoms, natural_loops, preds_of
from ...tuneprog.ir import Bin, Const, If, Let, Load, Store, Var
from ...tuneprog.irwalk import addr_split, walk
from ..cells import ident
from ..rows import blockrows, guards
from .rir import read

STEPS = {"+": 1, "-": -1}
CMP = ("!=", "==", "<", ">=", ">", "<=")


def loops(p, blocks, head=None):
    """``{header: (body, latches)}`` for the natural loops inside one segment.

    The voice loop is the tick's own and ``meta.voice_order`` runs it, so the
    header the level named for it is no region of the pass.
    """
    g = cfg(p)
    got = natural_loops(g, idoms(p, g), preds_of(p))
    return {
        h: (b, l) for h, (b, l) in got.items() if h in blocks and h != head and b <= set(blocks)
    }


def defs(p, body):
    """The names the loop's own blocks bind, each to the value it binds."""
    return {s.n: s.e for lbl in body for s in p.blocks[lbl].stmts if type(s) is Let}


def resolve(x, seen):
    """One value with a name the loop binds read through to the value it binds."""
    while type(x) is Var and x.n in seen:
        x = seen[x.n]
    return x


def tested(p, body):
    """``[(address, bound)]``: the cells the loop's own two-way tests close it on."""
    out, seen = [], defs(p, body)
    for lbl in body:
        t = p.blocks[lbl].term
        if type(t) is not If or t.t == t.f or type(t.c) is not Bin or t.c.op not in CMP:
            continue
        for a, b in ((t.c.a, t.c.b), (t.c.b, t.c.a)):
            x, y = resolve(a, seen), resolve(b, seen)
            if type(x) is Load and type(y) is Const and addr_split(x.a)[0] is not None:
                out.append((addr_split(x.a)[0], y.v))
    return out


def stepof(p, body, addr):
    """The constant one turn of the loop moves the cell by, or ``None``."""
    got, seen = set(), defs(p, body)
    for lbl in body:
        for s in p.blocks[lbl].stmts:
            if type(s) is not Store or s.cls != "ram" or addr_split(s.a)[0] != addr:
                continue
            v = resolve(s.v, seen)
            if type(v) is not Bin or v.op not in STEPS or type(v.b) is not Const:
                return None
            a = resolve(v.a, seen)
            if type(a) is not Load or addr_split(a.a)[0] != addr:
                return None
            got.add(STEPS[v.op] * v.b.v)
    return got.pop() if len(got) == 1 else None


def trip(low, p, body):
    """The turns one loop takes, as a value over the state it is entered with.

    A counter the loop moves by a constant and tests against a constant: counting
    down to the bound is the counter itself, counting up is the difference.
    """
    for addr, lim in tested(p, body):
        step = stepof(p, body, addr)
        if not step or low.v.cells.at(addr) is None:
            continue
        cell = read(low.v.cells.voicecell(addr))
        if step < 0:
            return cell if not lim else {"sub": [cell, lim]}
        return {"sub": [lim, cell]}
    return None


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
        n = trip(low, p, got[0]) if got is not None else None
        if n is None:
            run.append(lbl)
            continue
        body = [l for l in order if l in got[0]]
        out += rows_of(set(run), run) if run else []
        out.append({"loop": {"trip": n, "body": rows_of(set(body), body)}})
        inside |= set(body)
        run = []
    return out + (rows_of(set(run), run) if run else [])


def unstated(low, p, blocks, head=None):
    """The loop headers of a segment whose trip no value of the level states."""
    return sorted(h for h, (b, _l) in loops(p, blocks, head).items() if trip(low, p, b) is None)


def predicates(low, blocks):
    """One predicate cell a decision the block's own store takes away.

    A block that decides a term and then moves a cell that term reads has no
    channel for the value it decided on, so that decision is a cell, assigned
    where the block makes it and read by every row it guards.  Every other term
    is read at the site that decides it, where the value it reads still stands:
    a cell a tick did not assign holds the tick before's, which is no predicate.
    """
    out = {}
    for lbl in blocks:
        b = low.proc.blocks[lbl]
        if type(b.term) is If and b.term.t != b.term.f and _late(b, b.term.c):
            out[lbl] = ("p" + ident(lbl), b.term.c, True)
    return out


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
            out += [(quiet[0], x) for x in closing(seg, quiet.pop(0), preds)]
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

    def rows_of(bset, ordering):
        return blockstmts(seg, bset, order, ordering, preds)

    if p is None:
        return rows_of(blocks, order)
    return tree(seg.low, p, blocks, order, rows_of, head)
