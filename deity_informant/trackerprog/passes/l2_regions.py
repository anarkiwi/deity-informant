"""Region formation for L2: the natural loops of one segment, kept as loops.

A block a tick runs several times is no statement stated once, so a segment's
blocks are a tree: a natural loop is a ``loop`` statement whose body is its own
blocks and whose trip is a value over the state the loop is entered with.
"""

from __future__ import annotations

from ...tuneprog.graph import cfg, idoms, natural_loops, preds_of
from ...tuneprog.ir import Bin, Const, If, Let, Load, Store, Var
from ...tuneprog.irwalk import addr_split
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
