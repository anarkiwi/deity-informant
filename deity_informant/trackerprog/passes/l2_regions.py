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
    """One segment as a region tree: its loops kept, and its blocks in program order."""
    inside, out, heads = set(), [], loops(p, blocks, head)
    for lbl in [l for l in order if l in blocks]:
        if lbl in inside:
            continue
        got = heads.get(lbl)
        n = trip(low, p, got[0]) if got is not None else None
        if n is not None:
            body = [l for l in order if l in got[0]]
            out.append({"loop": {"trip": n, "body": rows_of(set(body), body)}})
            inside |= set(body)
            continue
        out += rows_of({lbl}, order)
    return out


def unstated(low, p, blocks, head=None):
    """The loop headers of a segment whose trip no value of the level states."""
    return sorted(h for h, (b, _l) in loops(p, blocks, head).items() if trip(low, p, b) is None)


def predicates(low, blocks):
    """One predicate cell a decision: if-conversion's own register.

    A block that decides a term and then moves a cell that term reads has no
    channel for the value it decided on, so the decision is a cell, assigned
    where the block makes it and read by every row it guards.  A block the tick
    does not reach assigns nothing, and the terms that lead to it are cells of
    the same kind, so its rows stand under a guard no path made true.
    """
    out = {}
    for lbl in blocks:
        b = low.proc.blocks[lbl]
        if type(b.term) is If and b.term.t != b.term.f:
            out[lbl] = ("p" + ident(lbl), b.term.c, _late(b, b.term.c))
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


def blockstmts(seg, lbl, order, preds):
    """One block as statements, in program order: its decision, then its stores."""
    low = seg.low
    got, up, rows = preds.get(lbl), raised(low, lbl), []
    for _l, kind, when, sets, _d in guards(
        seg, blockrows(seg, {lbl}, order, set(), {}, True), order
    ):
        if kind in ("set", "reg"):
            rows.append({"when": when, "sets": [list(x) for x in sets]})
    if got is not None:
        # a decision over a cell the block itself moved is read where the block
        # ends: read-after-write is the list's own order, not a second row
        rows.insert(len(rows) if got[2] else 0, predrow(seg, lbl, *got))
    rows += flagrows(low, lbl)
    low.pick = {}
    for r in rows:
        r["when"] = up + [t for t in (r.get("when") or []) if t not in up]
    return rows


def segrows(seg, blocks, order, preds, p=None, head=None):
    """One segment as a region tree: its loops kept, its blocks in program order."""

    def rows_of(bset, ordering):
        out = []
        for lbl in [l for l in ordering if l in bset]:
            out += blockstmts(seg, lbl, order, preds)
        return out

    if p is None:
        return rows_of(blocks, order)
    return tree(seg.low, p, blocks, order, rows_of, head)
