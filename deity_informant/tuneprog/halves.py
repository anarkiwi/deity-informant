"""S6 -- the two halves of a 16-bit value: the cell pair, and the byte shapes.

A cell is ``(region, constant address)``; a pair is two cells one index expression
reaches at two constant bases, in one region or two. Each shape here is a pattern
over a propagated expression, with no analysis behind it (:mod:`.word` applies them).
"""

from __future__ import annotations

from .idioms import CMP, fold, negated
from .ir import Bin, Const, IO_HI, IO_LO, Load, R16
from .irwalk import addr_split, expand, sub_expr

DEPTH = 8


def zerofold(e):
    """The identities with a literal on the left, then the ordinary algebra.

    A byte plus nothing never carries, the ``|`` of the two carries a chain leaves is
    then one of them, and ``1 - (a cmp b)`` is the negated compare -- three shapes
    :func:`~.idioms.fold` reads only on the right.
    """
    if type(e) is Bin and e.op == "carry" and _k(e.b, 0):
        return Const(0, 1)
    if type(e) is Bin and e.op in ("|", "+", "^") and _k(e.a, 0):
        return e.b
    if type(e) is Bin and e.op == "-" and _k(e.a, 1) and type(e.b) is Bin and e.b.op in CMP:
        return negated(e.b)
    return fold(e)


def _nofold(e):
    """:func:`zerofold`, and ``(x + 1) == 0`` as the carry it is."""
    if type(e) is Bin and e.op == "==" and _k(e.b, 0) and type(e.a) is Bin:
        if e.a.op == "+" and _k(e.a.b, 1) and e.a.w == 1:
            return Bin("carry", e.a.a, Const(1, 1), 1)
    return zerofold(e)


def norm(e, defs=None):
    """``e`` with single-definition names expanded, then folded."""
    return sub_expr(expand(e, defs, DEPTH) if defs else e, _nofold)


def same(a, b):
    """Structural equality of two expressions, ignoring the printed width."""
    if type(a) is not type(b):
        return False
    t = type(a)
    if t is Const:
        return a.v == b.v
    if t is Load:
        return a.cls == b.cls and a.r == b.r and same(a.a, b.a)
    if t is Bin:
        return a.op == b.op and same(a.a, b.a) and same(a.b, b.b)
    return a == b


def _k(e, v):
    return type(e) is Const and e.v == v


def cell(x):
    """``(region, constant address)`` of a one-byte access, or ``None``."""
    base = addr_split(x.a)[0]
    return None if x.r < 0 or x.w != 1 or base is None else (x.r, base)


def cells(x, y):
    """``(low cell, high cell)`` when two accesses are the halves of one word.

    A chip register is not memory, so a pair with one half in the I/O band and one
    outside it is two unrelated bytes one index reached at two offsets.
    """
    cl, ch = cell(x), cell(y)
    if cl is None or ch is None or cl[1] == ch[1]:
        return None
    if (IO_LO <= cl[1] <= IO_HI) != (IO_LO <= ch[1] <= IO_HI):
        return None
    return (cl, ch) if same(addr_split(x.a)[1], addr_split(y.a)[1]) else None


def _at(e, c):
    """True when ``e`` is the byte load of cell ``c``."""
    return type(e) is Load and cell(e) == c


def _terms(v):
    """One ``+`` tree flattened, left to right."""
    ts = [v]
    while type(ts[0]) is Bin and ts[0].op == "+":
        ts[:1] = [ts[0].a, ts[0].b]
    return ts


def _diff(v):
    """``(minuend, subtrahend, borrow in)`` of ``x - (y + (1 - b))``, or ``None``."""
    if type(v) is not Bin or v.op != "-":
        return None
    b = v.b
    if type(b) is Bin and b.op == "+":
        z = _oneminus(b.b)
        if z is not None:
            return v.a, b.a, z
        return (v.a, b.a, Const(0)) if _k(b.b, 1) else None
    z = _oneminus(b)
    return (v.a, Const(0), z) if z is not None else (v.a, b, Const(1))


def _oneminus(z):
    """``b`` where ``z`` is ``1 - b``, in either of :func:`zerofold`'s spellings."""
    if type(z) is Bin and z.op == "-" and _k(z.a, 1) and type(z.b) is not Const:
        return z.b
    return negated(z) if type(z) is Bin and z.op in CMP else None


def _carryof(x, ts):
    """The carry the 6510 leaves after adding ``ts`` to the byte ``x``."""
    out = Bin("carry", x, ts[0], 1)
    if len(ts) > 1:
        out = Bin("|", out, Bin("carry", Bin("+", x, ts[0], 1), ts[1], 1), 1)
    return out


def _notc(c):
    """``1 - c``, in the spelling :func:`zerofold` gives it."""
    return zerofold(Bin("-", Const(1), c, 1))


def operand(lo, hi):
    """The 16-bit value a pair of byte operands reads, or ``None``.

    A high half of literal zero is a byte widened, which is what ``ADC lo``/``ADC #0``
    adds to a word; the pair is then the low half alone.
    """
    if type(lo) is Const and type(hi) is Const:
        return Const(lo.v | (hi.v << 8), 2)
    if type(hi) is Const and not hi.v:
        return lo
    pair = cells(lo, hi) if type(lo) is Load and type(hi) is Load else None
    return None if pair is None else R16(pair[0], pair[1], lo.a)


def _add(_pair, vlo, vhi):
    """``lo = x + y; hi = x' + y' + carry(x + y)`` is one 16-bit add."""
    ts, hs = _terms(vlo), _terms(vhi)
    if not 2 <= len(ts) <= 3:
        return None
    want = norm(_carryof(ts[0], ts[1:]))
    at = next((i for i, t in enumerate(hs) if same(norm(t), want)), None)
    if at is None:
        return None
    rest = hs[:at] + hs[at + 1 :]
    rest = rest + [Const(0)] if len(rest) == 1 else rest
    if len(rest) != 2:
        return None
    for a, b in ((rest[0], rest[1]), (rest[1], rest[0])):
        x, y = operand(ts[0], a), operand(ts[1], b)
        if x is not None and y is not None:
            out = Bin("+", x, y, 2)
            out = out if len(ts) == 2 else Bin("+", out, ts[2], 2)
            return out, (want, norm(_carryof(hs[0], hs[1:])))
    return None


def _sub(_pair, vlo, vhi):
    """``lo = x - y - borrow; hi = x' - y' - borrow(x - y)`` is one 16-bit subtract."""
    lo, hi = _diff(vlo), _diff(vhi)
    if lo is None or hi is None:
        return None
    want = Bin("<=", Bin("+", lo[1], Bin("-", Const(1), lo[2], 1), 1), lo[0], 1)
    if not same(norm(hi[2]), norm(want)):
        return None
    x, y = operand(lo[0], hi[0]), operand(lo[1], hi[1])
    if x is None or y is None:
        return None
    out = Bin("-", x, y, 2) if _k(lo[2], 1) else Bin("-", x, Bin("+", y, _notc(lo[2]), 2), 2)
    up = Bin("<=", Bin("+", hi[1], Bin("-", Const(1), hi[2], 1), 1), hi[0], 1)
    return out, (norm(want), norm(up))


def _shift(pair, vlo, vhi):
    """``hi >>= 1; lo = (lo >> 1) | ((hi & 1) << 7)`` is one 16-bit shift right."""
    if not (type(vhi) is Bin and vhi.op == ">>" and _k(vhi.b, 1) and _at(vhi.a, pair[1])):
        return None
    if type(vlo) is not Bin or vlo.op != "|":
        return None
    for x, y in ((vlo.a, vlo.b), (vlo.b, vlo.a)):
        if not (type(x) is Bin and x.op == ">>" and _k(x.b, 1) and _at(x.a, pair[0])):
            continue
        top = y.a if type(y) is Bin and y.op == "<<" and _k(y.b, 7) else None
        if type(top) is Bin and top.op == "&" and _k(top.b, 1) and _at(top.a, pair[1]):
            return Bin(">>", R16(pair[0], pair[1], x.a.a), Const(1, 1), 2), ()
    return None


RULES = (_add, _sub, _shift)


def value(pair, vlo, vhi):
    """``(the 16-bit value assigned, the byte carries it subsumes)``, or ``None``."""
    return next((e for e in (r(pair, vlo, vhi) for r in RULES) if e is not None), None)


def bumped(pair, half, v):
    """The cell's own load when ``v`` is the byte increment of ``pair[half]``."""
    ts = _terms(v)
    ok = len(ts) == 2 and _at(ts[0], pair[half]) and _k(ts[1], 1)
    return ts[0] if ok else None


def read(e):
    """``(hi << 8) | lo`` over one cell pair is a 16-bit read."""
    if type(e) is not Bin or e.op != "|":
        return e
    for lo, x in ((e.a, e.b), (e.b, e.a)):
        hi = x.a if type(x) is Bin and x.op == "<<" and _k(x.b, 8) else None
        if type(lo) is not Load or type(hi) is not Load:
            continue
        pair = cells(lo, hi)
        if pair is not None:
            return R16(pair[0], pair[1], lo.a)
    return e
