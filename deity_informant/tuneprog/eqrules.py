"""The rule set of :mod:`.eqsat`: the 6510 value algebra as ``egglog`` rewrites.

Six groups -- :func:`~.idioms.fold`'s identities, the boolean shapes, the
interval analysis over e-classes, the masks and comparisons that analysis
decides, the V flag of a subtract, and a branch whose arms differ by one.
"""

from __future__ import annotations

from egglog import Expr, String, StringLike, eq, function, i64, i64Like, ne, rewrite, rule
from egglog import ruleset, set_, union

from .ranges import BOOL

WIDE = 16  # the widest value, in bits: past it a shift is outside the algebra
SIGN = 0x80


class E(Expr):
    """One 6510 value; ``m`` is the mask of the width the node is taken modulo."""

    @classmethod
    def k(cls, v: i64Like, m: i64Like) -> E: ...

    @classmethod
    def v(cls, n: StringLike, m: i64Like) -> E: ...

    @classmethod
    def cv(cls, n: StringLike, m: i64Like) -> E: ...

    @classmethod
    def ld(cls, key: i64Like, m: i64Like, a: E) -> E: ...

    @classmethod
    def op(cls, name: StringLike, m: i64Like, a: E, b: E) -> E: ...

    @classmethod
    def sel(cls, c: E, a: E, b: E) -> E: ...

    @classmethod
    def ovf(cls, a: E, b: E) -> E: ...

    @classmethod
    def blob(cls, key: i64Like, m: i64Like) -> E: ...


@function(merge=lambda old, new: old.max(new))
def lo(x: E) -> i64:
    """The greatest lower bound the IR proves for an e-class."""


@function(merge=lambda old, new: old.min(new))
def hi(x: E) -> i64:
    """The least upper bound the IR proves for an e-class."""


WEIGHT = {"E.k": 1, "E.v": 2, "E.cv": 3, "E.ld": 2, "E.op": 1, "E.blob": 1}
MARK = {"E.sel": 1, "E.ovf": 1}
ETYPE = "%s.E" % __name__


@function
def pick(i: i64Like) -> E:
    """A handle on the e-class of the ``i``-th expression the pass asked about."""


def _fold(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s):
    """:func:`~.idioms.fold`'s identities, run to saturation instead of one pass."""
    for name, val in (("+", (a + b) & m), ("-", (a - b) & m), ("&", a & b), ("|", a | b)):
        yield rewrite(E.op(name, m, E.k(a, m3), E.k(b, m4))).to(E.k(val, m))
    yield rewrite(E.op("^", m, E.k(a, m3), E.k(b, m4))).to(E.k(a ^ b, m))
    for name, val in (("<<", (a << b) & m), (">>", a >> b)):
        yield rewrite(E.op(name, m, E.k(a, m3), E.k(b, m4))).to(E.k(val, m), b >= 0, b <= WIDE)
    for name, same, other in (("==", 1, 0), ("!=", 0, 1)):
        yield rewrite(E.op(name, m, E.k(a, m3), E.k(a, m4))).to(E.k(same, 255))
        yield rewrite(E.op(name, m, E.k(a, m3), E.k(b, m4))).to(E.k(other, 255), ne(a).to(b))
    for name, out, cond in (("<", 1, a < b), ("<", 0, a >= b), ("<=", 1, a <= b), ("<=", 0, a > b)):
        yield rewrite(E.op(name, m, E.k(a, m3), E.k(b, m4))).to(E.k(out, 255), cond)
    for out, cond in ((1, (a + b) > m), (0, (a + b) <= m)):
        yield rewrite(E.op("carry", m, E.k(a, m3), E.k(b, m4))).to(E.k(out, 255), cond)
    for name in ("+", "-", "|", "^", "<<", ">>"):
        yield rewrite(E.op(name, m, x, E.k(0, m3))).to(x)
    for name in ("+", "|", "^"):
        yield rewrite(E.op(name, m, E.k(0, m3), x)).to(x)
    for side in (0, 1):
        yield rewrite(E.op("&", m, x, E.k(0, m3)) if side else E.op("&", m, E.k(0, m3), x)).to(
            E.k(0, m)
        )
    yield rewrite(E.op("&", m, x, E.k(m, m3))).to(x, eq(hi(x)).to(a), a <= m)
    yield rewrite(E.op("&", m, E.op("&", m2, x, E.k(a, m3)), E.k(b, m4))).to(
        E.op("&", m, x, E.k(a & b, m))
    )
    for name in ("<<", ">>"):
        yield rewrite(E.op(name, m, E.op(name, m, x, E.k(a, m3)), E.k(b, m4))).to(
            E.op(name, m, x, E.k(a + b, m)), a >= 0, b >= 0, (a + b) <= WIDE
        )
    for name, val in (("|", a | b), ("^", a ^ b)):
        yield rewrite(E.op(name, m, E.op(name, m, x, E.k(a, m3)), E.k(b, m4))).to(
            E.op(name, m, x, E.k(val, m))
        )
    for outer, sa in (("+", 1), ("-", -1)):
        for inner, sb in (("+", 1), ("-", -1)):
            yield rewrite(E.op(outer, m, E.op(inner, m, x, E.k(a, m3)), E.k(b, m4))).to(
                E.op("+", m, x, E.k((sb * a + sa * b) & m, m))
            )
    yield rewrite(E.op("-", m, x, x)).to(E.k(0, m))
    yield rewrite(E.op("^", m, x, x)).to(E.k(0, m))
    for name in ("&", "|"):
        yield rewrite(E.op(name, m, x, x)).to(x)


def _relational(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s):
    """A compare of a compare, and the 6510's subtract-against-zero."""
    for name, flip in (("==", False), ("!=", True)):
        for inner, neg in (("==", "!="), ("!=", "==")):
            for lit in (0, 1):
                same = (lit == 1) != flip
                yield rewrite(E.op(name, m, E.op(inner, m2, x, y), E.k(lit, m3))).to(
                    E.op(inner if same else neg, m2, x, y)
                )
        for cmp_, opp in (("<", "<="), ("<=", "<")):
            for lit in (0, 1):
                same = (lit == 1) != flip
                yield rewrite(E.op(name, m, E.op(cmp_, m2, x, y), E.k(lit, m3))).to(
                    E.op(cmp_, m2, x, y) if same else E.op(opp, m2, y, x)
                )
        yield rewrite(E.op(name, m, E.op("-", 255, x, y), E.k(0, m3))).to(
            E.op(name, 255, x, y), eq(hi(x)).to(a), eq(hi(y)).to(b), a <= 255, b <= 255
        )
        yield rewrite(E.op(name, m, E.op("|", m2, x, E.k(a, m3)), E.k(0, m4))).to(
            E.k(0 if name == "==" else 1, 255), a > 0
        )


def _analysis(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s):
    """The interval of an e-class; merge intersects, so every bound keeps holding."""
    yield rule(eq(x).to(E.k(a, m))).then(set_(lo(x)).to(a), set_(hi(x)).to(a))
    for ctor in (E.v, E.cv):
        yield rule(eq(x).to(ctor(s, m))).then(set_(hi(x)).to(m))
    yield rule(eq(x).to(E.ld(j, m, y))).then(set_(hi(x)).to(m))
    yield rule(eq(x).to(E.blob(j, m))).then(set_(hi(x)).to(m))
    for name in ("+", "-", "<<"):
        yield rule(eq(x).to(E.op(name, m, y, z))).then(set_(hi(x)).to(m))
    for name in BOOL:
        yield rule(eq(x).to(E.op(name, m, y, z))).then(set_(hi(x)).to(1))
    for side in (0, 1):
        yield rule(eq(x).to(E.op("&", m, y, z)), eq(hi(z if side else y)).to(a)).then(
            set_(hi(x)).to(a)
        )
    yield rule(eq(x).to(E.op(">>", m, y, E.k(j, m2))), eq(hi(y)).to(a), j >= 0, j <= WIDE).then(
        set_(hi(x)).to(a >> j)
    )
    for name in ("|", "^"):
        yield rule(
            eq(x).to(E.op(name, m, y, z)), eq(hi(y)).to(a), eq(hi(z)).to(b), a <= m, b <= m
        ).then(set_(hi(x)).to(m))
    yield rule(eq(x).to(E.sel(y, z, w1)), eq(hi(z)).to(a), eq(hi(w1)).to(b)).then(
        set_(hi(x)).to(a.max(b))
    )


def _masks(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s):
    """Masks and comparisons the interval decides; a carry only where it cannot happen.

    The carry rule stays at :func:`~.texture.zerocarry`'s reach: proving a carry
    away over a wider interval also erases the 16-bit chain :mod:`.word` pairs.
    """
    yield rewrite(E.op("&", m, x, E.k(j, m2))).to(x, eq(hi(x)).to(a), a <= j, (j & (j + 1)) <= 0)
    yield rewrite(E.op("&", m, x, E.k(j, m2))).to(
        E.k(0, m), eq(hi(x)).to(a), j > 0, a < (j & (0 - j))
    )
    yield rewrite(E.op("<", m, x, E.k(j, m2))).to(E.k(1, 255), eq(hi(x)).to(a), a < j)
    yield rewrite(E.op("<", m, x, E.k(j, m2))).to(E.k(0, 255), eq(lo(x)).to(a), a >= j)
    yield rewrite(E.op("<=", m, x, E.k(j, m2))).to(E.k(1, 255), eq(hi(x)).to(a), a <= j)
    yield rewrite(E.op("<=", m, x, E.k(j, m2))).to(E.k(0, 255), eq(lo(x)).to(a), a > j)
    for name, out in (("==", 0), ("!=", 1)):
        yield rewrite(E.op(name, m, x, E.k(j, m2))).to(E.k(out, 255), eq(hi(x)).to(a), a < j)
        yield rewrite(E.op(name, m, x, E.k(j, m2))).to(E.k(out, 255), eq(lo(x)).to(a), a > j)
    for side in (0, 1):
        lhs = E.op("carry", m, x, E.k(0, m3)) if side else E.op("carry", m, E.k(0, m3), x)
        yield rewrite(lhs).to(E.k(0, 255))


def _flags(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s):
    """V after a subtract is ``(A ^ B) & (A ^ (A - B))``; its sign needs both signs."""
    for left, right in ((x, y), (y, x)):
        for first in (E.op("^", 255, z, w1), E.op("^", 255, w1, z)):
            yield rule(
                eq(left).to(first),
                eq(right).to(E.op("^", 255, z, E.op("-", 255, z, w1))),
                eq(w2).to(E.op("&", 255, x, y)),
            ).then(union(w2).with_(E.ovf(z, w1)))
    sign = E.op("&", 255, E.ovf(z, w1), E.k(SIGN, 255))
    diff = E.op("&", 255, E.op("-", 255, z, w1), E.k(SIGN, 255))
    yield rewrite(sign).to(E.k(0, 255), eq(hi(z)).to(a), eq(hi(w1)).to(b), a < SIGN, b < SIGN)
    yield rewrite(sign).to(E.k(0, 255), eq(lo(z)).to(a), eq(lo(w1)).to(b), a >= SIGN, b >= SIGN)
    yield rewrite(sign).to(diff, eq(hi(z)).to(a), eq(lo(w1)).to(b), a < SIGN, b >= SIGN)
    yield rewrite(sign).to(
        E.op("^", 255, diff, E.k(SIGN, 255)),
        eq(lo(z)).to(a),
        eq(hi(w1)).to(b),
        a >= SIGN,
        b < SIGN,
    )


def _select(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s):
    """A branch whose arms differ by one is a borrow: both arms evaluate ``y``.

    ``If`` takes its true arm on any non-zero value, so the order that reads the
    condition as the borrow itself needs the analysis to prove it is one bit.
    """
    yield rewrite(E.sel(x, y, E.op("-", m, y, E.k(1, m)))).to(
        E.op("-", m, y, E.op("==", 255, x, E.k(0, 255)))
    )
    yield rewrite(E.sel(x, E.op("-", m, y, E.k(1, m)), y)).to(
        E.op("-", m, y, x), eq(hi(x)).to(a), a <= 1
    )
    yield rewrite(E.sel(x, y, E.op("+", m, y, E.k(1, m)))).to(
        E.op("+", m, y, E.op("==", 255, x, E.k(0, 255)))
    )
    yield rewrite(E.sel(x, E.op("+", m, y, E.k(1, m)), y)).to(
        E.op("+", m, y, x), eq(hi(x)).to(a), a <= 1
    )


def _ruleset(*parts):
    def body(
        x: E,
        y: E,
        z: E,
        w1: E,
        w2: E,
        a: i64,
        b: i64,
        j: i64,
        m: i64,
        m2: i64,
        m3: i64,
        m4: i64,
        s: String,
    ):
        for part in parts:
            yield from part(x, y, z, w1, w2, a, b, j, m, m2, m3, m4, s)

    body.__name__ = "eqsat_rules"
    return ruleset(body)


RULES = _ruleset(_fold, _relational, _analysis, _masks, _flags, _select)
