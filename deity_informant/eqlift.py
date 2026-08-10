"""Equality-saturation lift PoC over frameproc pass-1 statement lists.

An SSA-ified procedure is loaded into an egglog e-graph; every rewrite rule is
Z3-proven over QF_BV before admission; extraction picks the cheapest site-valid
term. Proof of concept only: no frameproc/frameprog behavior is touched.
"""

from __future__ import annotations

import ast

import z3
from egglog import pretty as _pretty
from egglog import Expr, StringLike, function, i64, i64Like, rewrite, ruleset, var
from egglog import eq as _fact_eq

from . import datadecl
from . import frameproc
from . import grammar as G
from . import sidprog


class T(Expr):
    """The single term sort: SSA locals, cell/heap loads, constants, ops."""


# ---- constructors (egg cost = base + 1 per literal arg + child terms) -----------
@function(cost=1)
def num(v: i64Like, w: i64Like) -> T: ...


@function(cost=1)
def cell(a: i64Like, w: i64Like, ver: i64Like) -> T: ...


@function(cost=4)
def loc(n: StringLike) -> T: ...


@function(cost=2)
def load(a: T, w: i64Like, h: i64Like) -> T: ...


@function
def add(x: T, y: T, w: i64Like) -> T: ...


@function
def sub(x: T, y: T, w: i64Like) -> T: ...


@function
def band(x: T, y: T, w: i64Like) -> T: ...


@function
def bor(x: T, y: T, w: i64Like) -> T: ...


@function
def bxor(x: T, y: T, w: i64Like) -> T: ...


@function
def shl(x: T, y: T, w: i64Like) -> T: ...


@function
def shr(x: T, y: T, w: i64Like) -> T: ...


@function
def zext(x: T) -> T: ...


@function
def trunc(x: T) -> T: ...


@function
def eq(x: T, y: T) -> T: ...


@function
def ne(x: T, y: T) -> T: ...


@function
def ult(x: T, y: T) -> T: ...


@function
def ule(x: T, y: T) -> T: ...


@function
def slt(x: T, y: T) -> T: ...


@function
def sge(x: T, y: T) -> T: ...


@function(cost=2)
def bnot(x: T) -> T: ...


@function(cost=12)
def carry(x: T, y: T, w: i64Like) -> T: ...


def _mask(w):
    return (1 << (8 * w)) - 1


# ---- dual rule algebra: the same builder yields the egg rewrite and the Z3 goal --
class _EggAlg:  # pylint: disable=too-many-public-methods
    """Builds egglog pattern terms; constant arithmetic rides on i64 operators.

    One method per constructor by contract (§4's single source), so the count is
    the vocabulary's and not a design choice to refactor."""

    def tvar(self, n, w):
        del w
        return var(n, T)

    def ivar(self, n, w):
        del w
        return var(n, i64)

    def num(self, v, w):
        return num(v, w)

    def add(self, x, y, w):
        return add(x, y, w)

    def sub(self, x, y, w):
        return sub(x, y, w)

    def band(self, x, y, w):
        return band(x, y, w)

    def bor(self, x, y, w):
        return bor(x, y, w)

    def bxor(self, x, y, w):
        return bxor(x, y, w)

    def shl(self, x, y, w):
        return shl(x, y, w)

    def shr(self, x, y, w):
        return shr(x, y, w)

    def zext(self, x):
        return zext(x)

    def trunc(self, x):
        return trunc(x)

    def eq(self, x, y):
        return eq(x, y)

    def ne(self, x, y):
        return ne(x, y)

    def ult(self, x, y):
        return ult(x, y)

    def ule(self, x, y):
        return ule(x, y)

    def slt(self, x, y):
        return slt(x, y)

    def sge(self, x, y):
        return sge(x, y)

    def bnot(self, x):
        return bnot(x)

    def carry(self, x, y, w):
        return carry(x, y, w)

    def fits(self, v, inner, outer):
        return _fact_eq(v & (_mask(outer) ^ _mask(inner))).to(i64(0))


def _b1(c):
    return z3.If(c, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))


class _Z3Alg:  # pylint: disable=too-many-public-methods
    """Builds Z3 QF_BV terms mirroring _EggAlg; widths 1/2 are BV8/BV16.

    Method for method with ``_EggAlg``: the proof and the rewrite are one builder."""

    def __init__(self):
        self.constraints = []
        self._n = 0

    def _fresh(self, n, bits):
        self._n += 1
        return z3.BitVec("%s_%d" % (n, self._n), bits)

    def tvar(self, n, w):
        return self._fresh(n, 8 * w)

    def ivar(self, n, w):
        v = self._fresh(n, 32)
        self.constraints.append(z3.ULE(v, _mask(w)))
        return v

    def num(self, v, w):
        if isinstance(v, int):
            return z3.BitVecVal(v & _mask(w), 8 * w)
        return z3.Extract(8 * w - 1, 0, v)

    def add(self, x, y, w):
        del w
        return x + y

    def sub(self, x, y, w):
        del w
        return x - y

    def band(self, x, y, w):
        del w
        return x & y

    def bor(self, x, y, w):
        del w
        return x | y

    def bxor(self, x, y, w):
        del w
        return x ^ y

    def shl(self, x, y, w):
        return x << self._amount(y, w)

    def shr(self, x, y, w):
        return z3.LShR(x, self._amount(y, w))

    @staticmethod
    def _amount(y, w):
        pad = 8 * w - y.size()
        return z3.ZeroExt(pad, y) if pad else y

    def zext(self, x):
        return z3.ZeroExt(8, x)

    def trunc(self, x):
        return z3.Extract(7, 0, x)

    def eq(self, x, y):
        return _b1(x == y)

    def ne(self, x, y):
        return _b1(x != y)

    def ult(self, x, y):
        return _b1(z3.ULT(x, y))

    def ule(self, x, y):
        return _b1(z3.ULE(x, y))

    def slt(self, x, y):
        return _b1(x < y)

    def sge(self, x, y):
        return _b1(x >= y)

    def bnot(self, x):
        return _b1(x == 0)

    def carry(self, x, y, w):
        return _b1(z3.UGT(z3.ZeroExt(1, x) + z3.ZeroExt(1, y), _mask(w)))

    def fits(self, v, inner, outer):
        """No goal of its own: an ``ivar`` is already constrained to its own width."""
        del self, v, inner, outer


# ---- the rule set: each entry is Z3-proven for each width before admission ------
def _r_add_comm(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.add(x, y, w), A.add(y, x, w)


def _r_add_num_in(A, w):
    """A numeral addend floats to the inner position of a chain.

    The one associativity instance the lane fusions need: they match a two-term add
    whose partner is the carry, and a lift spells the numeral last. Directed, so no
    grouping of a chain is ever enumerated."""
    x, y, c = A.tvar("x", w), A.tvar("y", w), A.ivar("c", w)
    return A.add(A.add(x, y, w), A.num(c, w), w), A.add(A.add(x, A.num(c, w), w), y, w)


def _r_add_fold(A, w):
    a, b = A.ivar("a", w), A.ivar("b", w)
    return A.add(A.num(a, w), A.num(b, w), w), A.num((a + b) & _mask(w), w)


def _r_add_zero(A, w):
    x = A.tvar("x", w)
    return A.add(x, A.num(0, w), w), x


def _r_sub_to_add(A, w):
    x, b = A.tvar("x", w), A.ivar("b", w)
    return A.sub(x, A.num(b, w), w), A.add(x, A.num((_mask(w) + 1 - b) & _mask(w), w), w)


def _r_add_to_sub(A, w):
    """The way back: ``expr`` folds ``SBC #k`` to an add, and the borrow needs the k."""
    x, a = A.tvar("x", w), A.ivar("a", w)
    return A.add(x, A.num(a, w), w), A.sub(x, A.num((_mask(w) + 1 - a) & _mask(w), w), w)


def _r_and_comm(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.band(x, y, w), A.band(y, x, w)


def _r_or_comm(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.bor(x, y, w), A.bor(y, x, w)


def _r_or_zero(A, w):
    x = A.tvar("x", w)
    return A.bor(x, A.num(0, w), w), x


def _r_shl_zero(A, w):
    y = A.tvar("y", 1)
    return A.shl(A.num(0, w), y, w), A.num(0, w)


def _r_carry_zero(A, w):
    """Nothing carries out of adding zero; the flag is a byte whatever ``w`` is."""
    x = A.tvar("x", w)
    return A.carry(x, A.num(0, w), w), A.num(0, 1)


def _r_sub_add_cancel(A, w):
    """``(x + y) - y -> x``: the addend a known accumulator leaves behind."""
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.sub(A.add(x, y, w), y, w), x


def _r_sub_sub_cancel(A, w):
    """``x - (x - y) -> y``, the subtrahend the same way round."""
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.sub(x, A.sub(x, y, w), w), y


def _r_carry_comm(A, w):
    """A carry out does not know which addend it came from; an ADC's lane pairing does."""
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.carry(x, y, w), A.carry(y, x, w)


def _r_and_fold(A, w):
    a, b = A.ivar("a", w), A.ivar("b", w)
    return A.band(A.num(a, w), A.num(b, w), w), A.num(a & b, w)


def _r_zext_num(A, w):
    del w
    a = A.ivar("a", 1)
    return A.zext(A.num(a, 1)), A.num(a, 2)


def _r_num_narrow(A, w):
    """The way back, guarded: expr's borrow compare widens its constant, not its zext."""
    del w
    a = A.ivar("a", 1)
    return A.num(a, 2), A.zext(A.num(a, 1)), (A.fits(a, 1, 2),)


def _r_sign_ne(A, w):
    x = A.tvar("x", w)
    return A.ne(A.band(x, A.num(1 << (8 * w - 1), w), w), A.num(0, w)), A.slt(x, A.num(0, w))


def _r_sign_eq(A, w):
    x = A.tvar("x", w)
    return A.eq(A.band(x, A.num(1 << (8 * w - 1), w), w), A.num(0, w)), A.sge(x, A.num(0, w))


def _r_not_ne(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.bnot(A.ne(x, y)), A.eq(x, y)


def _r_not_eq(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.bnot(A.eq(x, y)), A.ne(x, y)


def _r_not_slt(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.bnot(A.slt(x, y)), A.sge(x, y)


def _r_not_ult(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.bnot(A.ult(x, y)), A.ule(y, x)


def _r_not_ule(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.bnot(A.ule(x, y)), A.ult(y, x)


def _r_addc_eq(A, w):
    x, a, b = A.tvar("x", w), A.ivar("a", w), A.ivar("b", w)
    lhs = A.eq(A.add(x, A.num(a, w), w), A.num(b, w))
    return lhs, A.eq(x, A.num((_mask(w) + 1 + b - a) & _mask(w), w))


def _r_addc_ne(A, w):
    x, a, b = A.tvar("x", w), A.ivar("a", w), A.ivar("b", w)
    lhs = A.ne(A.add(x, A.num(a, w), w), A.num(b, w))
    return lhs, A.ne(x, A.num((_mask(w) + 1 + b - a) & _mask(w), w))


def _r_eq_comm(A, w):
    """Equality is symmetric, so which side a compare leaves the literal on is not a fact.

    ``sub_eq0`` hands back the two compared terms in the order the subtract had
    them, and every rule that then moves a constant step across the equality wants
    it on the right; without this the same flag reads one way and not the other."""
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.eq(x, y), A.eq(y, x)


def _r_ne_comm(A, w):
    """The same for the negated relation."""
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.ne(x, y), A.ne(y, x)


def _r_sub_eq0(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.eq(A.sub(x, y, w), A.num(0, w)), A.eq(x, y)


def _r_sub_ne0(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.ne(A.sub(x, y, w), A.num(0, w)), A.ne(x, y)


def _pk(A, h, l):
    """``h<<8 | l``: the word two byte lanes make, the shape every fusion is over."""
    return A.bor(A.shl(A.zext(h), A.num(8, 1), 2), A.zext(l), 2)


def _r_carry_fuse(A, w):
    del w
    al, ah, bl, bh = (A.tvar(n, 1) for n in ("al", "ah", "bl", "bh"))
    hi = A.add(A.add(ah, bh, 1), A.carry(al, bl, 1), 1)
    return _pk(A, hi, A.add(al, bl, 1)), A.add(_pk(A, ah, al), _pk(A, bh, bl), 2)


def _r_carry_fuse0(A, w):
    """The ADC chain with no hi addend: ``(ah + carry(al,bl))<<8 | (al+bl)``."""
    del w
    al, ah, bl = (A.tvar(n, 1) for n in ("al", "ah", "bl"))
    hi = A.add(ah, A.carry(al, bl, 1), 1)
    return _pk(A, hi, A.add(al, bl, 1)), A.add(_pk(A, ah, al), A.zext(bl), 2)


def _r_borrow_fuse(A, w):
    """The SBC chain fuses: ``(ah - (al<bl))<<8 | (al-bl) -> a16 - zext(bl)``."""
    del w
    al, ah, bl = (A.tvar(n, 1) for n in ("al", "ah", "bl"))
    hi = A.sub(ah, A.ult(al, bl), 1)
    return _pk(A, hi, A.sub(al, bl, 1)), A.sub(_pk(A, ah, al), A.zext(bl), 2)


def _r_borrow_word(A, w):
    """The general borrow, hi addend and all: ``ah - (bh + (al<bl))`` is the hi lane.

    ``SEC / SBC lo / SBC hi`` writes the borrow the compare form of the flag makes,
    which is what the byte-wise chain leaves; one rule, not a pass."""
    del w
    al, ah, bl, bh = (A.tvar(n, 1) for n in ("al", "ah", "bl", "bh"))
    hi = A.sub(ah, A.add(bh, A.ult(al, bl), 1), 1)
    return _pk(A, hi, A.sub(al, bl, 1)), A.sub(_pk(A, ah, al), _pk(A, bh, bl), 2)


def _r_bit_fuse(mn):
    """A bitwise op distributes over the pack, so two lanes of it are one word op."""

    def build(A, w):
        del w
        al, ah, bl, bh = (A.tvar(n, 1) for n in ("al", "ah", "bl", "bh"))
        f = getattr(A, mn)
        return _pk(A, f(ah, bh, 1), f(al, bl, 1)), f(_pk(A, ah, al), _pk(A, bh, bl), 2)

    return build


def _r_shl_fuse(rot):
    """``ASL/ROL lo`` then ``ROL hi``: the bit leaves the lo lane's top for the hi.

    ``rot`` adds the bit the lo lane takes in, which is the carry the program held
    and which the word takes in the same place."""

    def build(A, w):
        del w
        al, ah, c = (A.tvar(n, 1) for n in ("al", "ah", "c"))
        bit = A.ne(A.band(al, A.num(0x80, 1), 1), A.num(0, 1))
        one, word = A.num(1, 1), A.shl(_pk(A, ah, al), A.num(1, 2), 2)
        lo = A.bor(A.shl(al, one, 1), c, 1) if rot else A.shl(al, one, 1)
        hi = A.bor(A.shl(ah, one, 1), bit, 1)
        return _pk(A, hi, lo), A.bor(word, A.zext(c), 2) if rot else word

    return build


def _r_shr_fuse(rot):
    """``LSR/ROR hi`` then ``ROR lo``: the same bit rightwards, out of the hi lane."""

    def build(A, w):
        del w
        al, ah, c = (A.tvar(n, 1) for n in ("al", "ah", "c"))
        one, word = A.num(1, 1), A.shr(_pk(A, ah, al), A.num(1, 2), 2)
        bit = A.shl(A.band(ah, one, 1), A.num(7, 1), 1)
        hi = A.bor(A.shr(ah, one, 1), A.shl(c, A.num(7, 1), 1), 1) if rot else A.shr(ah, one, 1)
        lo = A.bor(A.shr(al, one, 1), bit, 1)
        out = A.bor(word, A.shl(A.zext(c), A.num(15, 2), 2), 2) if rot else word
        return _pk(A, hi, lo), out

    return build


def _r_carry_ult(A, w):
    """A sum below its own addend is the carry out, which is how a wrap test reads."""
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.carry(x, y, w), A.ult(A.add(x, y, w), y)


def _r_eq_zero(A, w):
    """``x == 0`` is ``x < 1``, which is the borrow out of ``x - 1``."""
    x = A.tvar("x", w)
    return A.eq(x, A.num(0, w)), A.ult(x, A.num(1, w))


def _r_carry_ones(A, w):
    """All ones is the one value a further count carries out of, which ``BNE`` tests."""
    x = A.tvar("x", w)
    return A.eq(x, A.num(_mask(w), w)), A.carry(x, A.num(1, w), w)


def _r_mask_hoist(A, w):
    """A masked hi half is the word masked: the 12-bit register's ``AND #$0F``."""
    del w
    h, l, m = A.tvar("h", 1), A.tvar("l", 1), A.ivar("m", 1)
    lhs = _pk(A, A.band(h, A.num(m, 1), 1), l)
    return lhs, A.band(_pk(A, h, l), A.num((m << 8) | 0xFF, 2), 2)


def _r_pack_add(A, w):
    """The pack built with ADC rather than ORA: the lanes are disjoint, so ``|`` is ``+``.

    Admitted at 3d landing 2 with the §4 cost change that names the OR-built pack the
    normal form (``_packed``), so the merge cannot leave the tie-break to spell it."""
    del w
    h, l = A.tvar("h", 1), A.tvar("l", 1)
    return A.add(A.shl(A.zext(h), A.num(8, 1), 2), A.zext(l), 2), _pk(A, h, l)


def _r_pack_hi(A, w):
    """A word read as its high byte is the pack's own hi lane."""
    del w
    h, l = A.tvar("h", 1), A.tvar("l", 1)
    return A.shr(_pk(A, h, l), A.num(8, 1), 2), A.zext(h)


def _r_pack_lo(A, w):
    """And its low byte is the lo lane: the mask the 6502 spells with ``AND #$FF``."""
    del w
    h, l = A.tvar("h", 1), A.tvar("l", 1)
    return A.band(_pk(A, h, l), A.num(0xFF, 2), 2), A.zext(l)


def _r_zext_mask(A, w):
    """A widened byte is already masked to its own width."""
    del w
    x = A.tvar("x", 1)
    return A.band(A.zext(x), A.num(0xFF, 2), 2), A.zext(x)


def _r_sbc_borrow(A, w):
    """SBC borrow ``$01 - (zext(x) <= zext(y)) -> (y < x)`` (bytes)."""
    del w
    x, y = A.tvar("x", 1), A.tvar("y", 1)
    return A.sub(A.num(1, 1), A.ule(A.zext(x), A.zext(y)), 1), A.ult(y, x)


def _r_shl_fold(a, b):
    """Concrete-amount left-shift fold ``(x << a) << b -> x << (a+b)``."""

    def build(A, w):
        x = A.tvar("x", w)
        return A.shl(A.shl(x, A.num(a, w), w), A.num(b, w), w), A.shl(x, A.num(a + b, w), w)

    return build


def _r_shr_fold(a, b):
    """Concrete-amount right-shift fold ``(x >> a) >> b -> x >> (a+b)``."""

    def build(A, w):
        x = A.tvar("x", w)
        return A.shr(A.shr(x, A.num(a, w), w), A.num(b, w), w), A.shr(x, A.num(a + b, w), w)

    return build


_SHIFT_FOLDS = tuple(
    ("%s_fold_%d_%d" % (nm, a, b), (1, 2), fac(a, b))
    for nm, fac in (("shl", _r_shl_fold), ("shr", _r_shr_fold))
    for a, b in ((1, 1), (2, 1), (3, 1))
)


RULES = (
    (
        ("add_comm", (1, 2), _r_add_comm),
        ("add_num_in", (1, 2), _r_add_num_in),
        ("add_fold", (1, 2), _r_add_fold),
        ("add_zero", (1, 2), _r_add_zero),
        ("sub_to_add", (1, 2), _r_sub_to_add),
        ("add_to_sub", (1, 2), _r_add_to_sub),
        ("sub_add_cancel", (1, 2), _r_sub_add_cancel),
        ("sub_sub_cancel", (1, 2), _r_sub_sub_cancel),
        ("carry_comm", (1, 2), _r_carry_comm),
        ("eq_comm", (1, 2), _r_eq_comm),
        ("ne_comm", (1, 2), _r_ne_comm),
        ("and_comm", (1, 2), _r_and_comm),
        ("and_fold", (1, 2), _r_and_fold),
        ("or_comm", (1, 2), _r_or_comm),
        ("or_zero", (1, 2), _r_or_zero),
        ("shl_zero", (1, 2), _r_shl_zero),
        ("carry_zero", (1, 2), _r_carry_zero),
        ("zext_num", (1,), _r_zext_num),
        ("num_narrow", (1,), _r_num_narrow),
        ("sign_ne", (1, 2), _r_sign_ne),
        ("sign_eq", (1, 2), _r_sign_eq),
        ("not_ne", (1, 2), _r_not_ne),
        ("not_eq", (1, 2), _r_not_eq),
        ("not_slt", (1, 2), _r_not_slt),
        ("not_ult", (1, 2), _r_not_ult),
        ("not_ule", (1, 2), _r_not_ule),
        ("addc_eq", (1, 2), _r_addc_eq),
        ("addc_ne", (1, 2), _r_addc_ne),
        ("sub_eq0", (1, 2), _r_sub_eq0),
        ("sub_ne0", (1, 2), _r_sub_ne0),
        ("carry_fuse", (2,), _r_carry_fuse),
        ("carry_fuse0", (2,), _r_carry_fuse0),
        ("borrow_fuse", (2,), _r_borrow_fuse),
        ("borrow_word", (2,), _r_borrow_word),
        ("shl_fuse", (2,), _r_shl_fuse(False)),
        ("rol_fuse", (2,), _r_shl_fuse(True)),
        ("shr_fuse", (2,), _r_shr_fuse(False)),
        ("ror_fuse", (2,), _r_shr_fuse(True)),
        ("carry_ult", (1,), _r_carry_ult),
        ("eq_zero", (1,), _r_eq_zero),
        ("carry_ones", (1,), _r_carry_ones),
        ("mask_hoist", (2,), _r_mask_hoist),
        ("sbc_borrow", (1,), _r_sbc_borrow),
        ("pack_add", (2,), _r_pack_add),
        ("pack_hi", (2,), _r_pack_hi),
        ("pack_lo", (2,), _r_pack_lo),
        ("zext_mask", (2,), _r_zext_mask),
    )
    + tuple(("%s_fuse" % mn, (2,), _r_bit_fuse(mn)) for mn in ("band", "bor", "bxor"))
    + _SHIFT_FOLDS
)


def _built(build, alg, w):
    """``(lhs, rhs, guards)`` of a rule instance; guards are the algebra's own."""
    got = build(alg, w)
    return got[0], got[1], tuple(g for g in (got[2] if len(got) > 2 else ()) if g is not None)


def verify_rules():
    """Z3-prove every rule instance equivalent over QF_BV; returns the list.

    A guarded rule's premise rides on ``ivar``'s width constraint, so the goal
    below is already the guarded one."""
    proved = []
    for name, widths, build in RULES:
        for w in widths:
            alg = _Z3Alg()
            lhs, rhs, _g = _built(build, alg, w)
            s = z3.Solver()
            s.add(*alg.constraints)
            s.add(lhs != rhs)
            if s.check() != z3.unsat:
                raise AssertionError("rule %s (width %d) is not an equivalence" % (name, w))
            proved.append((name, w))
    return proved


_RULESET = None
_RULE_NAMES = None


def admitted_rules():
    """(ruleset, {rewrite str: rule name}); verifies all rules, then caches.

    Width-independent patterns (compare/not rules) dedup to one instance."""
    global _RULESET, _RULE_NAMES  # pylint: disable=global-statement
    if _RULESET is None:
        verify_rules()
        alg = _EggAlg()
        rewrites, names = [], {}
        for name, widths, build in RULES:
            for w in widths:
                lhs, rhs, guards = _built(build, alg, w)
                rw = rewrite(lhs).to(rhs, *guards)
                if rw.decl in names:
                    continue
                rewrites.append(rw)
                names[rw.decl] = "%s/w%d" % (name, w)
        _RULESET, _RULE_NAMES = ruleset(*rewrites), names
    return _RULESET, _RULE_NAMES


# ---- tuple IR mirroring the constructors (parse target for extracted reprs) -----
_COSTS = {"num": 1, "cell": 1, "loc": 4, "load": 2, "bnot": 2, "carry": 12}
_PACK_ADD = 2  # the ADC-built pack: equal to the OR one, and not the normal form


def _packed(ir):
    """True where an ``add`` spells the pack ``bor`` spells (``hi << 8`` plus ``zext lo``).

    ``idioms.pack`` is the OR form, so naming it the normal form is the catalog's own
    reading; the price says so instead of leaving it to the ``repr`` tie-break."""
    if ir[0] != "add":
        return False
    for a, b in ((ir[1], ir[2]), (ir[2], ir[1])):
        if a[0] == "shl" and a[2][0] == "num" and a[2][1] == 8 and a[1][0] == "zext":
            return b[0] == "zext"
    return False


_OPS = {
    "INT_ADD": "add",
    "INT_SUB": "sub",
    "INT_AND": "band",
    "INT_OR": "bor",
    "INT_XOR": "bxor",
    "INT_LEFT": "shl",
    "INT_RIGHT": "shr",
    "INT_EQUAL": "eq",
    "INT_NOTEQUAL": "ne",
    "INT_LESS": "ult",
    "INT_LESSEQUAL": "ule",
    "INT_CARRY": "carry",
}

_CMP_TAGS = frozenset(("eq", "ne", "ult", "ule", "slt", "sge"))

_EGG_FNS = {
    "num": num,
    "cell": cell,
    "loc": loc,
    "load": load,
    "add": add,
    "sub": sub,
    "band": band,
    "bor": bor,
    "bxor": bxor,
    "shl": shl,
    "shr": shr,
    "zext": zext,
    "trunc": trunc,
    "eq": eq,
    "ne": ne,
    "ult": ult,
    "ule": ule,
    "slt": slt,
    "sge": sge,
    "bnot": bnot,
    "carry": carry,
}


def _egg_of(ir, memo):
    r = memo.get(ir)
    if r is None:
        args = [_egg_of(a, memo) if isinstance(a, tuple) else a for a in ir[1:]]
        r = _EGG_FNS[ir[0]](*args)
        memo[ir] = r
    return r


# ---- pass-1 IR <-> value-graph IR ------------------------------------------------
_UNOPS = {v: k for k, v in _OPS.items()}


class _ToEgg:
    """Pass-1 to value-graph translation, expanding locals where they were written.

    ``env(name, at)`` answers with the definition in force at ``at`` and the point
    it was made, which becomes the bound for its own reads. ``prov`` collects every
    naming of a term as ``{term: {node: (depth, point)}}``, shallowest per node."""

    def __init__(self, env, prov, limit):
        self.env, self.prov, self.left = env, prov, limit

    def of(self, ir, at, d=0):
        if self.left <= 0:
            return None
        self.left -= 1
        e = self._of(ir, at, d)
        if e is not None and self.prov is not None:
            named = self.prov.setdefault(e, {})
            if d < named.get(ir, (d + 1,))[0]:
                named[ir] = (d, at)
        return e

    def _of(self, ir, at, d):
        k = ir[0]
        if k == "const":
            return ("num", ir[1] & _mask(ir[2]), ir[2])
        if k == "loc":
            got = None if self.env is None else self.env(ir[1], at)
            return ("loc", ir[1]) if got is None else self.of(got[1], got[0], d + 1)
        if k == "mem":
            if ir[1][0] == "const":
                return ("cell", ir[1][1], ir[2], 0)
            a = self.of(ir[1], at, d + 1)
            return None if a is None else ("load", a, ir[2], 0)
        return self._op(ir, at, d) if k == "op" else None

    def _op(self, ir, at, d):
        kids = [self.of(c, at, d + 1) for c in ir[2]]
        if any(c is None for c in kids):
            return None
        if ir[1] == "INT_ZEXT":
            return ("zext", kids[0])
        fn = _OPS.get(ir[1])
        if fn is None:
            return None
        if fn in _CMP_TAGS:
            return (fn, kids[0], kids[1])
        w = carry_lane(ir[2][0]) if fn == "carry" else ir[3]
        r = kids[0]
        for c in kids[1:]:
            r = (fn, r, c, w)
        return r


def to_egg(ir, env=None, prov=None, at=0, limit=1 << 20):
    """Value-graph IR for a pass-1 expression, None where it has no counterpart.

    Expansion follows a local's definition at every distinct point it is read, so
    ``limit`` bounds the nodes it may produce."""
    return _ToEgg(env, prov, limit).of(ir, at)


def carry_lane(val):
    """The lane width a carry out of ``val`` is taken at (``expr._apply``'s reading).

    A carry's own width is its one-bit result; the width that decides it is the
    operands', so a word lane's carry may not wear a byte lane's tag."""
    return G.store_width(val)


def pass1_node(ir, kids):
    """Pass-1 node for one term, its children already pass-1; None where it has none."""
    k = ir[0]
    if k == "carry":
        return ("op", "INT_CARRY", tuple(kids), 1)
    if k == "num":
        return ("const", ir[1], ir[2])
    if k == "loc":
        return ("loc", ir[1])
    if k == "cell":
        return ("mem", ("const", ir[1], 2), ir[2])
    if k == "load":
        return ("mem", kids[0], ir[2])
    if k == "zext":
        return ("op", "INT_ZEXT", (kids[0],), 2)
    if k == "trunc":
        return ("op", "COPY", (kids[0],), 1)
    mn = _UNOPS.get(k)
    return None if mn is None else ("op", mn, tuple(kids), 1 if k in _CMP_TAGS else ir[-1])


def from_egg(ir):
    """Pass-1 expression for a term, None where it has none; a local comes back a byte.

    ``to_egg`` does not carry a local's width, so a word local only round trips
    through the provenance its translation recorded."""
    kids = [from_egg(a) for a in ir[1:] if isinstance(a, tuple)]
    return None if any(c is None for c in kids) else pass1_node(ir, kids)


def _parse_call(node, env):
    if isinstance(node, ast.Name):
        return env[node.id]
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise ValueError("unexpected extracted syntax: %s" % ast.dump(node))
    out = [node.func.id]
    for a in node.args:
        if isinstance(a, (ast.Call, ast.Name)):
            out.append(_parse_call(a, env))
        else:
            out.append(ast.literal_eval(a))
    return tuple(out)


def _unformat():
    """Stop egglog's printer formatting a term we only ever parse back.

    ``str()`` pretty-prints through Black and ``_parse_ir`` hands that to ``ast``,
    which does not care: the pass was 60% of rung (d2)'s time. Skipped if egglog
    stops printing that way, since its own note there proposes exactly that."""
    black = getattr(_pretty, "black", None)
    if black is None or not hasattr(black, "format_str"):
        return
    _pretty.black = type(
        "_Unformatted",
        (),
        {"parsing": black.parsing, "format_str": staticmethod(lambda program, mode=None: program)},
    )


_unformat()


def _parse_ir(text):
    """Parse an extracted egglog str form (optionally let-lifted) into IR."""
    env, result = {}, None
    for node in ast.parse(text, mode="exec").body:
        if isinstance(node, ast.Assign):
            env[node.targets[0].id] = _parse_call(node.value, env)
        else:
            result = _parse_call(node.value, env)
    return result


def canon(ir):
    """One representative of an extracted term wherever two spell one function.

    ``x - K`` and ``x + (2**w - K)`` are the same term in two's complement and
    which one extraction hands back is not contractual; pass-1 writes an indexed
    address as an add, so that is the spelling a provenance lookup can name."""
    if not isinstance(ir, tuple):
        return ir
    out = tuple(canon(a) for a in ir)
    if out[0] == "sub" and out[2][0] == "num":
        return ("add", out[1], ("num", -out[2][1] & _mask(out[3]), out[2][2]), out[3])
    return out


_SID_LO, _SID_HI = 0xD400, 0xD41C


def _cost(ir):
    c = _COSTS.get(ir[0], 1)
    if ir[0] == "cell" and _SID_LO <= ir[1] <= _SID_HI:
        c = 9  # SID cells are outputs; never prefer reading one back
    elif _packed(ir):
        c = _PACK_ADD  # the catalog's word-pack is the OR one; the ADC spelling costs more
    for a in ir[1:]:
        c += _cost(a) if isinstance(a, tuple) else 1
    return c


def _ir_width(ir, locw):
    k = ir[0]
    if k in ("num", "cell", "load"):
        return ir[2]
    if k == "loc":
        return locw.get(ir[1], 1)
    if k == "zext":
        return 2
    if k == "trunc":
        return 1
    if k in _CMP_TAGS or k in ("bnot", "carry"):
        return 1
    return ir[-1]


# ---- printing --------------------------------------------------------------------
_CHAINS = {"band": "&", "bor": "|", "bxor": "^"}
_CMP_TEXT = {"eq": "==", "ne": "!=", "ult": "<", "ule": "<=", "slt": "<s", "sge": "<=s"}
_CMP_SWAP = frozenset(("sge",))  # x >=s y is INT_SLESSEQUAL(y, x): the dialect's <=s, swapped
_SHIFTS = {"shl": "<<", "shr": ">>"}


class _Printer:
    """Skeleton + chosen IR terms to frameprog-style text lines.

    ``pairs`` is the ONE lo/hi table registry (``frameprog._decl_pairs``): with it a
    declared pair's pack spells the word column, without it it stays the OR.
    ``locw`` gives a local's width, which only the SID view's offset fold needs."""

    def __init__(self, aliases, pairs=None, locw=None, derefs=()):
        self.aliases = aliases or {}
        self.pairs = pairs or {}
        self.locw = locw or {}
        self.derefs = frozenset(derefs)
        self.out = []

    def name(self, a):
        return self.aliases.get(a) or sidprog._addr_name(a)

    def fmt(self, ir):
        got = self._pair_pack(ir)
        if got is not None:
            return "%s[%s]:2" % (self.name(got[0]), self.fmt(got[1]))
        k = ir[0]
        if k == "num":
            return sidprog._hex(ir[1], ir[2])
        if k == "loc":
            base = ir[1].rpartition(".")[0]
            return base + sidprog._wsuf(self.locw.get(base, 1))
        if k == "cell":
            return self.name(ir[1]) + sidprog._wsuf(ir[2])
        if k == "load":
            return self._loadref(ir)
        if k == "zext":
            return "zext2(%s)" % self.fmt(ir[1])
        if k == "trunc":
            return "trunc1(%s)" % self.fmt(ir[1])
        if k == "carry":
            return "carry(%s, %s)" % (self.fmt(ir[1]), self.fmt(ir[2]))
        if k == "bnot":
            return "!%s" % self.fmt(ir[1])
        if k == "add":
            return self._addref(ir)
        if k == "sub":
            if ir[2][0] == "num":  # the dialect folds a constant subtrahend into the add
                m = _mask(ir[3])
                return self._addref(("add", ir[1], ("num", (-ir[2][1]) & m, ir[3]), ir[3]))
            return "(%s - %s)%s" % (self.fmt(ir[1]), self.fmt(ir[2]), sidprog._wsuf(ir[3]))
        if k in _CHAINS:
            body = (" %s " % _CHAINS[k]).join(self.fmt(p) for p in self._chain(ir, k))
            return "(%s)%s" % (body, sidprog._wsuf(ir[3]))
        if k in _CMP_TEXT:
            under, over = (ir[2], ir[1]) if k in _CMP_SWAP else (ir[1], ir[2])
            return "(%s %s %s)" % (self.fmt(under), _CMP_TEXT[k], self.fmt(over))
        if k in _SHIFTS:
            body = "%s %s %s" % (self.fmt(ir[1]), _SHIFTS[k], self.fmt(ir[2]))
            return "(%s)%s" % (body, sidprog._wsuf(ir[3]))
        raise ValueError("unprintable IR %r" % (k,))

    def _chain(self, ir, k):
        parts, stack = [], [ir]
        while stack:
            x = stack.pop()
            if x[0] == k and x[-1] == ir[-1]:
                stack.append(x[2])
                stack.append(x[1])
            else:
                parts.append(x)
        return parts

    def _addref(self, ir):
        w = ir[3]
        half, m = 1 << (8 * w - 1), _mask(w)
        parts = self._chain(ir, "add")
        body = [self.fmt(parts[0])]
        for p in parts[1:]:
            if p[0] == "num" and p[1] >= half:
                body.append("- " + sidprog._hex((m + 1 - p[1]) & m, w))
            else:
                body.append("+ " + self.fmt(p))
        return "(%s)%s" % (" ".join(body), sidprog._wsuf(w))

    def _split(self, addr):
        """``(const base, index)`` of a ``base + index`` address, else None.

        ``frameproc._index_of``'s breadth: the index is whatever the address adds,
        and ``zext2`` is the reader's own widening (grammar ``_index_addr``).
        ``sub_to_add``/``add_to_sub`` are admitted rules, so the same address is
        equally an ``idx - $EAA1`` and an ``idx + $155F``; an address is read mod
        $10000 and the reading may not depend on which representative extraction
        returned (adoption §10)."""
        if addr[0] not in ("add", "sub") or addr[3] != 2:
            return None
        if addr[0] == "sub" and addr[2][0] == "num" and addr[2][2] == 2:
            base, idx = (-addr[2][1]) & 0xFFFF, addr[1]
            if base < 0x100:
                return None
        elif addr[0] == "add":
            at = [
                i for i in (1, 2) if addr[i][0] == "num" and addr[i][2] == 2 and addr[i][1] >= 0x100
            ]
            if len(at) != 1:
                return None
            base, idx = addr[at[0]][1], addr[3 - at[0]]
        else:
            return None
        if idx[0] == "num":
            return None
        return base, idx[1] if idx[0] == "zext" else idx

    def _ptr_cell(self, ir):
        """The pointer cell ``ir`` is the word of, else None (``frameptr._cell_of``).

        The fused word is one two-byte cell; the unfused one is the little-endian
        pack of two adjacent byte cells, which is the shape rung (d) did not fuse."""
        if ir[0] == "cell" and ir[2] == 2:
            return ir[1]
        if ir[0] != "bor" or ir[3] != 2:
            return None
        for hi, lo in ((ir[1], ir[2]), (ir[2], ir[1])):
            if hi[0] != "shl" or hi[3] != 2 or hi[2][0] != "num" or hi[2][1] != 8:
                continue
            hc, lc = self._half(hi[1]), self._half(lo)
            if hc and lc and hc[1] is None and lc[1] is None and hc[0] == lc[0] + 1:
                return lc[0]
        return None

    def _deref(self, addr):
        """``(pointer cell, index or None)`` of a rung-(f) resolved deref, else None."""
        if not self.derefs:
            return None
        ptr = self._ptr_cell(addr)
        if ptr is not None:
            return (ptr, None) if ptr in self.derefs else None
        if addr[0] != "add" or addr[3] != 2:
            return None
        for a, b in ((addr[1], addr[2]), (addr[2], addr[1])):
            ptr = self._ptr_cell(a)
            if ptr is not None and ptr in self.derefs:
                return ptr, b[1] if b[0] == "zext" else b
        return None

    def _loadref(self, ir):
        """``frameproc._memref``'s spelling of an access: a resolved deref, a row of a
        named base, the register-file view, or ``mem[..]``, at the access's own width."""
        addr, w = ir[1], ir[2]
        got = self._deref(addr)
        if got is not None:
            row = "" if got[1] is None else "[%s]" % self.fmt(got[1])
            return "*%s%s%s" % (self.name(got[0]), row, sidprog._wsuf(w))
        got = self._split(addr)
        if got is None:
            return "mem[%s]%s" % (self.fmt(addr), sidprog._wsuf(w))
        base, idx = got
        if w != 1 or G.sid_base(base) is None:
            return "%s[%s]%s" % (self.name(base), self.fmt(idx), sidprog._wsuf(w))
        off = base - 0xD400  # rung (d)'s residue: the byte is the register file's index
        if off:
            wide = idx if _ir_width(idx, self.locw) == 2 else ("zext", idx)
            idx = ("add", wide, ("num", off, 2), 2)
        return "%s[%s]" % (G.VIEW, self.fmt(idx))

    def _half(self, ir):
        """``(base, index or None)`` where ``ir`` reads one byte of a named cell."""
        if ir[0] == "zext":
            ir = ir[1]
        if ir[0] == "cell" and ir[2] == 1:
            return ir[1], None
        return self._split(ir[1]) if ir[0] == "load" and ir[2] == 1 else None

    def _pair_columns(self, bl, bh, idx):
        """``(lo base, index)`` where two byte cells are a declared pair's columns."""
        got = frameproc.pair_site(self.pairs, bl, idx)
        if got is None or got[0] != bh:
            return None
        i = got[2]
        return got[1], (("num", i[1], i[2]) if i[0] == "const" else i)

    def _pair_pack(self, ir):
        """``(lo base, index)`` where ``ir`` packs a declared pair's two columns."""
        if not self.pairs or not isinstance(ir, tuple) or ir[0] != "bor" or ir[3] != 2:
            return None
        for hi, lo in ((ir[1], ir[2]), (ir[2], ir[1])):
            if hi[0] != "shl" or hi[3] != 2 or hi[2][0] != "num" or hi[2][1] != 8:
                continue
            sl, sh = self._half(lo), self._half(hi[1])
            if sl is None or sh is None or sl[1] != sh[1]:
                continue
            got = self._pair_columns(sl[0], sh[0], sl[1])
            if got is not None:
                return got
        return None

    def line(self, text, d):
        self.out.append(" " * d + text)

    def seq(self, nodes, d):
        for nd in nodes:
            self.node(nd, d)

    def node(self, nd, d):
        k = nd[0]
        if k == "asg":
            if not nd[2].dropped:
                self.line("%s = %s" % (nd[1], self.fmt(nd[2].chosen)), d + 1)
        elif k == "st":
            a, w = nd[1]
            self.line("%s = %s" % (self.name(a) + sidprog._wsuf(w), self.fmt(nd[2].chosen)), d + 1)
        elif k == "stx":
            ref = self._loadref(("load", nd[1].chosen, 1, 0))
            self.line("%s = %s" % (ref, self.fmt(nd[2].chosen)), d + 1)
        elif k == "if":
            cond = nd[1].chosen
            word, inner = ("ifnot", cond[1]) if cond[0] == "bnot" else ("if", cond)
            self.line("%s %s {" % (word, self.fmt(inner)), d)
            self.seq(nd[2], d + 1)
            if nd[3]:
                self.line("} else {", d)
                self.seq(nd[3], d + 1)
            self.line("}", d)
        elif k == "loop":
            self.line("loop {", d)
            self.seq(nd[1], d + 1)
            self.line("}", d)
        elif k == "label":
            self.line("$%04X:" % nd[1], d)
        elif k == "goto":
            self.line("goto $%04X" % nd[1], d)
        elif k == "unobs":
            self.line("unobserved $%04X" % nd[1], d)
        elif k == "cont":
            self.line("continue", d)
        elif k == "brk":
            self.line("break", d)
        elif k == "ret":
            self.line("ret", d + 1)
        elif k == "call":
            self.line("call $%04X ret $%04X" % (nd[1], nd[2]), d + 1)
        elif k == "callb":
            self.line("call $%04X ret $%04X {" % (nd[1], nd[2]), d + 1)
            self.seq(nd[3], d + 2)
            self.line("}", d + 1)
        elif k == "dcallx":
            self.line("call (%s) ret $%04X" % (self.fmt(nd[1].chosen), nd[2]), d + 1)
        elif k == "dbr":
            text = "%s %s goto (%s) else $%04X"
            self.line(text % (nd[1], self.fmt(nd[2].chosen), self.fmt(nd[3].chosen), nd[4]), d + 1)
        elif k == "dgotox":
            self.line("goto (%s)" % self.fmt(nd[1].chosen), d + 1)
        elif k == "igotox":
            ptr = "(%s)" % self.fmt(nd[2].chosen) if nd[2] is not None else "$%04X" % nd[1]
            self.line("igoto %s" % ptr, d + 1)
        elif k == "swg":
            self._cases("switch goto {", nd[1], d)
        elif k == "opsw":
            self._cases("switch %s {" % self.name(nd[1]), nd[2], d)
        elif k == "swc":
            self._swc(nd, d)
        else:
            raise ValueError("unprintable node %r" % (k,))

    def _cases(self, head, cases, d):
        self.line(head, d)
        self._cases_tail(cases, d)

    def _swc(self, nd, d):
        if not nd[2]:
            body = " ".join(nd[1])
            self.line("switch call { %s }" % body if body else "switch call { }", d)
            return
        self.line("switch call {", d)
        if nd[1]:
            self.line(" ".join(nd[1]), d + 1)
        self._cases_tail(nd[2], d)

    def _cases_tail(self, cases, d):
        for lbl, body in cases:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(body, d + 2)
            self.line("}", d + 1)
        self.line("}", d)


def pass1(model, entry=None):
    """(pass-1 statement list, aliases, entry) for one committed-model procedure."""
    aliases = getattr(model, "symbols", None)
    if aliases is None:
        _decls, aliases = datadecl.declarations(model)
    trees, labels, view = sidprog._model_trees(model)
    conv = frameproc._Conv(frameproc._Names(aliases))
    builder = frameproc._Builder(labels, set(model.dispatch_sets), view, conv)
    entry = model.play if entry is None else entry
    return builder.proc(dict(trees)[entry]), aliases, entry


_EMIT_NOTES = (
    "; eqlift PoC: solver-lifted procedure bodies over the committed sidprog model",
    "; header sections (state/data/symbols) reuse the frameprog emitter verbatim;",
    ";   procedure statements are equality-saturation extraction output",
)
