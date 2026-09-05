"""The natural loops of one segment, and the turns each takes.

A loop stays a loop at L2 and its trip is a value over the state the loop is
entered with: the counter the loop's own two-way test closes it on, read where
the loop is entered.  Two spellings of the same countdown, the compare against
zero and the sign bit a decrement past zero leaves.
"""

from __future__ import annotations

from ...tuneprog.graph import cfg, idoms, natural_loops, preds_of, succs
from ...tuneprog.ir import Bin, Const, Let, Load, Store, Var
from ...tuneprog.irwalk import addr_split
from ..read import Unlowerable
from .rir import read

STEPS = {"+": 1, "-": -1}
CMP = ("!=", "==", "<", ">=", ">", "<=")
SIGN = 0x80


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


def exits(p, body):
    """``[(block, condition, the truth that stays)]``: the tests that leave a loop."""
    out = []
    for lbl in body:
        t = p.blocks[lbl].term
        if type(t).__name__ != "If" or t.t == t.f:
            continue
        got = (t.t in body, t.f in body)
        if got == (True, False):
            out.append((lbl, t.c, True))
        elif got == (False, True):
            out.append((lbl, t.c, False))
    return out


def counted(c, stay, seen):
    """``(the value the turn steps, its step, the test)`` of a loop's own countdown."""
    if type(c) is not Bin or c.op not in ("!=", "==") or type(c.b) is not Const or c.b.v:
        return None, None, None
    a = resolve(c.a, seen)
    kind = "zero" if (c.op == "!=") == stay else None
    if type(a) is Bin and a.op == "&" and type(a.b) is Const and a.b.v == SIGN:
        a, kind = resolve(a.a, seen), "sign" if (c.op == "==") == stay else None
    if kind is None or type(a) is not Bin or a.op != "-" or type(a.b) is not Const or not a.b.v:
        return None, None, None
    return a.a, a.b.v, kind


def headers(p, body, head):
    """The blocks outside a loop that reach its header: where its counter is seeded."""
    return [l for l, b in p.blocks.items() if head in succs(b.term) and l not in body]


def entry(low, p, body, head, x):
    """The value the loop is entered with, read where the entering path bound it.

    A counter the entering path binds is read there; one the body itself loads
    from a cell is that cell, which the body's own step reads and no fold takes.
    """
    got = None
    if type(x) is Var:
        for lbl in headers(p, body, head):
            for s in p.blocks[lbl].stmts:
                if type(s) is Let and s.n == x.n:
                    got = low.chase(s.e)
    if got is None:
        got = resolve(x, defs(p, body))
    # the cell itself, not what a store since put in it: the loop reads it at entry
    low.lbl, low.local, low.pick, low.sub = None, {}, {}, {}
    try:
        return low.value(got if type(got) is Load else low.expand(got))
    except Unlowerable:
        return None


def _offset(node, k):
    """One value moved by a constant, folded where the value is one."""
    if not k:
        return node
    if isinstance(node, int):
        return node + k
    return {"add": [node, k]} if k > 0 else {"sub": [node, -k]}


def counter_trip(low, p, body, head, order):
    """The turns a countdown loop takes, as a value over the state it is entered with.

    A block up to the exiting test runs once more than a block past it, so the
    trip is the count of whichever the body's own stores stand in, and a body
    whose stores stand in both is left unstated.
    """
    got = exits(p, body)
    if len(got) != 1:
        return None
    lbl, c, stay = got[0]
    inside = [l for l in order if l in body]
    at = inside.index(lbl)
    puts = {i for i, l in enumerate(inside) if any(type(s) is Store for s in p.blocks[l].stmts)}
    early = {i for i in puts if i <= at}
    if early and puts - early:
        return None
    x, step, kind = counted(c, stay, defs(p, body))
    if x is None or step != 1:
        return None
    n = entry(low, p, body, head, x)
    if n is None or (isinstance(n, dict) and not n):
        return None
    return _offset(n, (1 if kind == "sign" else 0) - (0 if early else 1))


def tested(p, body):
    """``[(address, bound)]``: the cells the loop's own two-way tests close it on."""
    out, seen = [], defs(p, body)
    for lbl in body:
        t = p.blocks[lbl].term
        if type(t).__name__ != "If" or t.t == t.f or type(t.c) is not Bin or t.c.op not in CMP:
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


def cell_trip(low, p, body):
    """A counter the loop moves by a constant and tests against a constant."""
    for addr, lim in tested(p, body):
        step = stepof(p, body, addr)
        if not step or low.v.cells.at(addr) is None:
            continue
        cell = read(low.v.cells.voicecell(addr))
        if step < 0:
            return cell if not lim else {"sub": [cell, lim]}
        return {"sub": [lim, cell]}
    return None


def trip(low, p, body, head=None, order=None):
    """The turns one loop takes, as a value over the state it is entered with."""
    if head is not None and order is not None:
        got = counter_trip(low, p, body, head, order)
        if got is not None:
            return got
    return cell_trip(low, p, body)


def carried(low, p, body, latches):
    """``{name: (the latch that rebinds it, its value)}``: what a loop carries in a name.

    A name is no cell, so a loop that carries one has no channel for the turn it
    is on; the level gives it the cell the reader already names it by.
    """
    seen = set()
    for lbl in body:
        b = p.blocks[lbl]
        for x in [getattr(s, "e", None) for s in b.stmts] + [getattr(b.term, "c", None)]:
            seen |= {y.n for y in _walk(x) if type(y) is Var}
    out = {}
    for lbl in latches:
        for s in p.blocks[lbl].stmts:
            if type(s) is Let and s.n in seen and s.n not in low.defs:
                out[s.n] = (lbl, s.e)
    return out


def seeds(low, p, body, head, got):
    """The rows an entering path assigns a carried name in, before the loop runs."""
    out = []
    for lbl in headers(p, body, head):
        for s in p.blocks[lbl].stmts:
            if type(s) is Let and s.n in got:
                low.lbl, low.local, low.pick, low.sub = lbl, {}, {}, {}
                out.append((s.n, low.value(low.expand(s.e)), s.e.w, lbl))
    return out


def closes(low, got):
    """The rows the turn ends with: each carried name moved to the turn's own value."""
    out = []
    for n, (lbl, e) in sorted(got.items()):
        low.lbl, low.local, low.pick, low.sub = lbl, {}, {}, {}
        out.append((n, low.value(low.expand(e)), e.w, lbl))
    return out


def _walk(x):
    from ...tuneprog.irwalk import walk  # pylint: disable=import-outside-toplevel

    return walk(x) if x is not None else ()


def unstated(low, p, blocks, head=None, order=None):
    """The loop headers of a segment whose trip no value of the level states."""
    return sorted(
        h
        for h, (b, _l) in loops(p, blocks, head).items()
        if trip(low, p, b, h, order or sorted(b)) is None
    )
