"""Equality-saturation lift PoC over frameproc pass-1 statement lists.

An SSA-ified procedure is loaded into an egglog e-graph; every rewrite rule is
Z3-proven over QF_BV before admission; extraction picks the cheapest site-valid
term. Proof of concept only: no frameproc/frameprog behavior is touched.
"""

from __future__ import annotations

import ast
import time

import z3
from egglog import EGraph, Expr, StringLike, function, i64, i64Like, rewrite, ruleset, union, var

from . import datadecl
from . import frameproc
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
class _EggAlg:
    """Builds egglog pattern terms; constant arithmetic rides on i64 operators."""

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

    def zext(self, x):
        return zext(x)

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


def _b1(c):
    return z3.If(c, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))


class _Z3Alg:
    """Builds Z3 QF_BV terms mirroring _EggAlg; widths 1/2 are BV8/BV16."""

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


# ---- the rule set: each entry is Z3-proven for each width before admission ------
def _r_add_comm(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.add(x, y, w), A.add(y, x, w)


def _r_add_assoc(A, w):
    x, y, z = A.tvar("x", w), A.tvar("y", w), A.tvar("z", w)
    return A.add(A.add(x, y, w), z, w), A.add(x, A.add(y, z, w), w)


def _r_add_fold(A, w):
    a, b = A.ivar("a", w), A.ivar("b", w)
    return A.add(A.num(a, w), A.num(b, w), w), A.num((a + b) & _mask(w), w)


def _r_add_zero(A, w):
    x = A.tvar("x", w)
    return A.add(x, A.num(0, w), w), x


def _r_sub_to_add(A, w):
    x, b = A.tvar("x", w), A.ivar("b", w)
    return A.sub(x, A.num(b, w), w), A.add(x, A.num((_mask(w) + 1 - b) & _mask(w), w), w)


def _r_and_comm(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.band(x, y, w), A.band(y, x, w)


def _r_and_fold(A, w):
    a, b = A.ivar("a", w), A.ivar("b", w)
    return A.band(A.num(a, w), A.num(b, w), w), A.num(a & b, w)


def _r_zext_num(A, w):
    del w
    a = A.ivar("a", 1)
    return A.zext(A.num(a, 1)), A.num(a, 2)


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


def _r_sub_eq0(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.eq(A.sub(x, y, w), A.num(0, w)), A.eq(x, y)


def _r_sub_ne0(A, w):
    x, y = A.tvar("x", w), A.tvar("y", w)
    return A.ne(A.sub(x, y, w), A.num(0, w)), A.ne(x, y)


def _r_carry_fuse(A, w):
    del w
    al, ah, bl, bh = (A.tvar(n, 1) for n in ("al", "ah", "bl", "bh"))
    hi = A.add(A.add(ah, bh, 1), A.carry(al, bl, 1), 1)
    split = A.bor(A.shl(A.zext(hi), A.num(8, 1), 2), A.zext(A.add(al, bl, 1)), 2)
    a16 = A.bor(A.shl(A.zext(ah), A.num(8, 1), 2), A.zext(al), 2)
    b16 = A.bor(A.shl(A.zext(bh), A.num(8, 1), 2), A.zext(bl), 2)
    return split, A.add(a16, b16, 2)


RULES = (
    ("add_comm", (1, 2), _r_add_comm),
    ("add_assoc", (1, 2), _r_add_assoc),
    ("add_fold", (1, 2), _r_add_fold),
    ("add_zero", (1, 2), _r_add_zero),
    ("sub_to_add", (1, 2), _r_sub_to_add),
    ("and_comm", (1, 2), _r_and_comm),
    ("and_fold", (1, 2), _r_and_fold),
    ("zext_num", (1,), _r_zext_num),
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
)


def verify_rules():
    """Z3-prove every rule instance equivalent over QF_BV; returns the list."""
    proved = []
    for name, widths, build in RULES:
        for w in widths:
            alg = _Z3Alg()
            lhs, rhs = build(alg, w)
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
                lhs, rhs = build(alg, w)
                rw = rewrite(lhs).to(rhs)
                if rw.decl in names:
                    continue
                rewrites.append(rw)
                names[rw.decl] = "%s/w%d" % (name, w)
        _RULESET, _RULE_NAMES = ruleset(*rewrites), names
    return _RULESET, _RULE_NAMES


# ---- tuple IR mirroring the constructors (parse target for extracted reprs) -----
_COSTS = {"num": 1, "cell": 1, "loc": 4, "load": 2, "bnot": 2, "carry": 12}

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


def _parse_call(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise ValueError("unexpected extracted syntax: %s" % ast.dump(node))
    out = [node.func.id]
    for a in node.args:
        out.append(_parse_call(a) if isinstance(a, ast.Call) else ast.literal_eval(a))
    return tuple(out)


def _parse_ir(text):
    return _parse_call(ast.parse(text, mode="eval").body)


def _cost(ir):
    c = _COSTS.get(ir[0], 1)
    for a in ir[1:]:
        c += _cost(a) if isinstance(a, tuple) else 1
    return c


def _leaves(ir, out):
    if ir[0] in ("num", "cell", "loc"):
        out.add(ir)
        return out
    for a in ir[1:]:
        if isinstance(a, tuple):
            _leaves(a, out)
    return out


def _contains(ir, leaf):
    if ir == leaf:
        return True
    return any(isinstance(a, tuple) and _contains(a, leaf) for a in ir[1:])


def _ir_width(ir, locw):
    k = ir[0]
    if k in ("num", "cell", "load"):
        return ir[2]
    if k == "loc":
        return locw.get(ir[1], 1)
    if k == "zext":
        return 2
    if k in _CMP_TAGS or k in ("bnot", "carry"):
        return 1
    return ir[-1]


# ---- SSA construction over the pass-1 statement list -----------------------------
class _Item:
    """One extractable expression site: original IR + its version environment."""

    __slots__ = ("ir", "locs", "cells", "heap", "def_leaf", "volatile", "chosen", "dropped")

    def __init__(self, ir, locs, cells, heap, volatile):
        self.ir = ir
        self.locs = locs
        self.cells = cells
        self.heap = heap
        self.def_leaf = None
        self.volatile = volatile
        self.chosen = ir
        self.dropped = False


_CALLISH = frozenset(("call", "pcall", "dcall", "callb", "swc", "swg", "opsw", "dbr", "dgoto"))
_NOFALL = frozenset(("ret", "goto", "cont", "brk", "unobs"))


def _falls(s):
    if s[0] in _NOFALL:
        return False
    if s[0] == "if":
        return _falls_list(s[3]) or _falls_list(s[4])
    return True


def _falls_list(stmts):
    return not stmts or _falls(stmts[-1])


def _writes(stmts, out):
    """Collect (written locals, const-store addresses, wild) under ``stmts``."""
    for s in stmts:
        k = s[0]
        if k in ("asg", "for"):
            out[0].add(s[1])
        elif k == "pcall":
            out[0].update(s[3])
        elif k == "st":
            if s[1][0] == "const":
                out[1].add(s[1][1])
            else:
                out[2] = True
        elif k in _CALLISH or k in ("label", "igoto"):
            out[2] = True
        for b in frameproc._stmt_bodies(s):
            _writes(b, out)
    return out


class _Ssa:
    """Version environment: unique versions per write, havoc at joins."""

    def __init__(self):
        self.tick = 0
        self.locs = {}
        self.cells = {}
        self.heap = 0
        self.locw = {}
        self.defs = []
        self.items = []
        self._vol = False

    def fresh(self):
        self.tick += 1
        return self.tick

    def conv(self, e):
        k = e[0]
        if k == "const":
            return ("num", e[1] & _mask(e[2]), e[2])
        if k == "loc":
            ver = self.locs.setdefault(e[1], self.fresh())
            return ("loc", "%s.%d" % (e[1], ver))
        if k == "mem":
            return self._conv_mem(e)
        if k == "op":
            return self._conv_op(e)
        raise ValueError("unencodable node %r" % (k,))

    def _conv_mem(self, e):
        addr, w = e[1], e[2]
        if addr[0] == "const":
            a = addr[1]
            if not sidprog._ld_safe(addr):
                self._vol = True
                return ("cell", a, w, self.fresh())
            return ("cell", a, w, self.cells.setdefault((a, w), self.fresh()))
        air = self.conv(addr)
        if sidprog._ld_safe(addr):
            return ("load", air, w, self.heap)
        self._vol = True
        return ("load", air, w, self.fresh())

    def _conv_op(self, e):
        mn, kids, w = e[1], e[2], e[3]
        if mn == "INT_ZEXT":
            return ("zext", self.conv(kids[0]))
        name = _OPS.get(mn)
        if name is None:
            raise ValueError("unencodable op %r" % (mn,))
        if name in _CMP_TAGS:
            return (name, self.conv(kids[0]), self.conv(kids[1]))
        if name == "carry":
            x = self.conv(kids[0])
            return ("carry", x, self.conv(kids[1]), _ir_width(x, self.locw))
        ir = self.conv(kids[0])
        for kid in kids[1:]:
            ir = (name, ir, self.conv(kid), w)
        return ir

    def item(self, e):
        self._vol = False
        ir = self.conv(e)
        it = _Item(ir, dict(self.locs), dict(self.cells), self.heap, self._vol)
        self.items.append(it)
        return it

    def define_loc(self, name, rhs_item):
        ver = self.fresh()
        self.locs[name] = ver
        leaf = ("loc", "%s.%d" % (name, ver))
        self.locw[leaf[1]] = _ir_width(rhs_item.ir, self.locw)
        rhs_item.def_leaf = leaf
        self.defs.append((leaf, rhs_item.ir))

    def define_cell(self, a, w, rhs_item):
        self.bump_cells_at(a, w)
        self.heap = self.fresh()
        ver = self.fresh()
        self.cells[(a, w)] = ver
        leaf = ("cell", a, w, ver)
        rhs_item.def_leaf = leaf
        self.defs.append((leaf, rhs_item.ir))

    def bump_cells_at(self, a, w):
        for a2, w2 in list(self.cells):
            if a2 < a + w and a < a2 + w2:
                self.cells[(a2, w2)] = self.fresh()

    def havoc_all(self):
        for n in list(self.locs):
            self.locs[n] = self.fresh()
        for key in list(self.cells):
            self.cells[key] = self.fresh()
        self.heap = self.fresh()

    def snap(self):
        return dict(self.locs), dict(self.cells), self.heap

    def restore(self, s):
        self.locs, self.cells, self.heap = dict(s[0]), dict(s[1]), s[2]

    def merge(self, a, b):
        locs = {}
        for n in set(a[0]) | set(b[0]):
            va, vb = a[0].get(n), b[0].get(n)
            locs[n] = va if va == vb else self.fresh()
        cells = {}
        for key in set(a[1]) | set(b[1]):
            va, vb = a[1].get(key), b[1].get(key)
            cells[key] = va if va == vb else self.fresh()
        self.locs, self.cells = locs, cells
        self.heap = a[2] if a[2] == b[2] else self.fresh()


class _Lift:
    """Builds the SSA skeleton, then saturates, extracts and sweeps."""

    def __init__(self, aliases=None):
        self.ssa = _Ssa()
        self.aliases = aliases or {}
        self.stats = {}
        self._groups = {}

    def build(self, stmts):
        return [self._stmt(s) for s in stmts]

    def _stmt(self, s):
        k = s[0]
        ssa = self.ssa
        if k == "asg":
            it = ssa.item(s[2])
            ssa.define_loc(s[1], it)
            return ("asg", s[1], it)
        if k == "st":
            return self._store(s)
        if k == "if":
            return self._if(s)
        if k == "loop":
            written = _writes(s[1], [set(), set(), False])
            self._loop_havoc(written)
            body = self.build(s[1])
            self._loop_havoc(written)
            return ("loop", body)
        if k == "label":
            ssa.havoc_all()
            return s
        if k in ("goto", "cont", "brk", "ret", "unobs"):
            return s
        if k in _CALLISH or k == "igoto":
            items = [ssa.item(x) for x in frameproc._stmt_exprs(s)]
            ssa.havoc_all()
            bodies = [self.build(b) for b in frameproc._stmt_bodies(s)]
            ssa.havoc_all()
            return ("opaque", s, items, bodies)
        raise ValueError("unexpected statement %r" % (k,))

    def _if(self, s):
        ssa = self.ssa
        it = ssa.item(s[2])
        if s[1] == "ifnot":
            it.ir = ("bnot", it.ir)
            it.chosen = it.ir
        pre = ssa.snap()
        then = self.build(s[3])
        mid = ssa.snap()
        ssa.restore(pre)
        els = self.build(s[4])
        tf, ef = _falls_list(s[3]), _falls_list(s[4])
        if tf and ef:
            ssa.merge(mid, ssa.snap())
        elif tf:
            ssa.restore(mid)
        return ("if", it, then, els)

    def _loop_havoc(self, written):
        ssa = self.ssa
        if written[2]:
            ssa.havoc_all()
            return
        for n in written[0]:
            ssa.locs[n] = ssa.fresh()
        for a in written[1]:
            ssa.bump_cells_at(a, 1)
            ssa.cells[(a, 1)] = ssa.fresh()
        if written[1]:
            ssa.heap = ssa.fresh()

    def _store(self, s):
        addr, e = s[1], s[2]
        it = self.ssa.item(e)
        if addr[0] == "const":
            w = _ir_width(it.ir, self.ssa.locw)
            self.ssa.define_cell(addr[1], w, it)
            return ("st", (addr[1], w), it)
        addr_it = self.ssa.item(addr)
        for key in list(self.ssa.cells):
            self.ssa.cells[key] = self.ssa.fresh()
        self.ssa.heap = self.ssa.fresh()
        return ("stx", addr_it, it)

    # ---- e-graph phase -----------------------------------------------------
    def saturate(self, iters=24, k=12):
        rs, names = admitted_rules()
        eg = EGraph()
        memo = {}
        for it in self.ssa.items:
            eg.register(_egg_of(it.ir, memo))
        for leaf, rhs in self.ssa.defs:
            eg.register(union(_egg_of(leaf, memo)).with_(_egg_of(rhs, memo)))
        t0 = time.monotonic()
        rep = eg.run(rs * iters)
        t1 = time.monotonic()
        fired = {}
        for key, n in rep.num_matches_per_rule.items():
            if n:
                name = names.get(key, str(key))
                fired[name] = fired.get(name, 0) + n
        self._group_leaves(eg, memo)
        for it in self.ssa.items:
            pool = [_parse_ir(str(x)) for x in eg.extract_multiple(_egg_of(it.ir, memo), k)]
            it.chosen = self._select(it, pool)
        t2 = time.monotonic()
        self.stats.update(
            items=len(self.ssa.items),
            defs=len(self.ssa.defs),
            fired=fired,
            matches=sum(fired.values()),
            saturate_s=round(t1 - t0, 3),
            extract_s=round(t2 - t1, 3),
        )

    def _group_leaves(self, eg, memo):
        """Leaf term -> its e-class mates, keyed by the class's extracted rep."""
        leaves = set()
        for it in self.ssa.items:
            _leaves(it.ir, leaves)
        for leaf, _rhs in self.ssa.defs:
            leaves.add(leaf)
        reps = {}
        for leaf in leaves:
            reps.setdefault(str(eg.extract(_egg_of(leaf, memo))), []).append(leaf)
        self._groups = {}
        for mates in reps.values():
            if len(mates) > 1:
                for leaf in mates:
                    self._groups[leaf] = sorted(mates, key=lambda t: (_cost(t), repr(t)))

    def _repair(self, ir, it, ok):
        """Site-valid equivalent of ``ir``: stale leaves swap for class mates."""
        if ir[0] in ("num", "cell", "loc"):
            if self._valid(ir, it, ok):
                return ir
            for alt in self._groups.get(ir, ()):
                if alt != it.def_leaf and self._valid(alt, it, ok):
                    return alt
            return None
        out = [ir[0]]
        for a in ir[1:]:
            if isinstance(a, tuple):
                a = self._repair(a, it, ok)
                if a is None:
                    return None
            out.append(a)
        fixed = tuple(out)
        return fixed if self._valid(fixed, it, ok) else None

    def _select(self, it, pool):
        ok = _leaves(it.ir, set())
        best, best_cost = it.ir, _cost(it.ir)
        for ir in pool:
            ir = self._repair(ir, it, ok)
            if ir is None or ir == it.ir:
                continue
            if it.def_leaf is not None and _contains(ir, it.def_leaf):
                continue
            c = _cost(ir)
            if c < best_cost or (c == best_cost and best != it.ir and repr(ir) < repr(best)):
                best, best_cost = ir, c
        return best

    def _valid(self, ir, it, ok):
        k = ir[0]
        if k in ("num", "cell", "loc"):
            if k == "num" or ir in ok:
                return True
            if k == "cell":
                return it.cells.get((ir[1], ir[2])) == ir[3]
            name, _sep, ver = ir[1].rpartition(".")
            return it.locs.get(name) == int(ver)
        if k == "load" and ir[3] != it.heap:
            return False
        return all(self._valid(a, it, ok) for a in ir[1:] if isinstance(a, tuple))

    def sweep(self, skeleton):
        """Drop dead non-register temp definitions after extraction."""
        dropped = 0
        while True:
            used = set()
            defs = self._collect(skeleton, used)
            dead = [
                (name, it)
                for name, it in defs
                if not it.dropped
                and not it.volatile
                and name not in frameproc._ALL_REG_LOCALS
                and it.def_leaf not in used
            ]
            if not dead:
                break
            for _name, it in dead:
                it.dropped = True
                dropped += 1
        self.stats["copies_dropped"] = dropped
        return skeleton

    def _collect(self, nodes, used):
        out = []
        for nd in nodes:
            k = nd[0]
            if k == "asg":
                if not nd[2].dropped:
                    _leaves(nd[2].chosen, used)
                    out.append((nd[1], nd[2]))
            elif k == "st":
                _leaves(nd[2].chosen, used)
            elif k == "stx":
                _leaves(nd[1].chosen, used)
                _leaves(nd[2].chosen, used)
            elif k == "if":
                _leaves(nd[1].chosen, used)
                out.extend(self._collect(nd[2], used))
                out.extend(self._collect(nd[3], used))
            elif k == "loop":
                out.extend(self._collect(nd[1], used))
            elif k == "opaque":
                for it in nd[2]:
                    _leaves(it.chosen, used)
                for b in nd[3]:
                    out.extend(self._collect(b, used))
        return out


# ---- printing --------------------------------------------------------------------
_CHAINS = {"band": "&", "bor": "|", "bxor": "^"}
_CMP_TEXT = {"eq": "==", "ne": "!=", "ult": "<", "ule": "<=", "slt": "<s", "sge": ">=s"}
_SHIFTS = {"shl": "<<", "shr": ">>"}


class _Printer:
    """Skeleton + chosen IR terms to frameprog-style text lines."""

    def __init__(self, aliases):
        self.aliases = aliases or {}
        self.out = []

    def name(self, a):
        return self.aliases.get(a) or sidprog._addr_name(a)

    def fmt(self, ir):
        k = ir[0]
        if k == "num":
            return sidprog._hex(ir[1], ir[2])
        if k == "loc":
            return ir[1].rpartition(".")[0]
        if k == "cell":
            return self.name(ir[1]) + sidprog._wsuf(ir[2])
        if k == "load":
            return self._loadref(ir)
        if k == "zext":
            return "zext2(%s)" % self.fmt(ir[1])
        if k == "carry":
            return "carry(%s, %s)" % (self.fmt(ir[1]), self.fmt(ir[2]))
        if k == "bnot":
            return "!%s" % self.fmt(ir[1])
        if k == "add":
            return self._addref(ir)
        if k == "sub":
            return "(%s - %s)%s" % (self.fmt(ir[1]), self.fmt(ir[2]), sidprog._wsuf(ir[3]))
        if k in _CHAINS:
            body = (" %s " % _CHAINS[k]).join(self.fmt(p) for p in self._chain(ir, k))
            return "(%s)%s" % (body, sidprog._wsuf(ir[3]))
        if k in _CMP_TEXT:
            return "(%s %s %s)" % (self.fmt(ir[1]), _CMP_TEXT[k], self.fmt(ir[2]))
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

    def _loadref(self, ir):
        addr, w = ir[1], ir[2]
        if w == 1 and addr[0] == "add" and addr[3] == 2:
            idx, base = addr[1], addr[2]
            if base[0] == "num" and base[1] >= 0x100 and idx[0] == "zext" and idx[1][0] == "loc":
                return "%s[%s]" % (self.name(base[1]), self.fmt(idx[1]))
        return "mem[%s]%s" % (self.fmt(addr), sidprog._wsuf(w))

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
        elif k == "opaque":
            self.line("opaque %s" % (nd[1][0],), d + 1)
            for b in nd[3]:
                self.seq(b, d + 1)
        else:
            raise ValueError("unprintable node %r" % (k,))


class LiftResult:
    """Lifted text plus the SSA/extraction record needed for verification."""

    def __init__(self, entry, lines, lifter, skeleton):
        self.entry = entry
        self.lines = lines
        self.text = "\n".join(lines) + "\n"
        self.lifter = lifter
        self.skeleton = skeleton
        self.stats = lifter.stats


def lift_stmts(stmts, aliases=None, entry=0, iters=24, k=12):
    """Equality-saturation lift of one pass-1 statement list."""
    lifter = _Lift(aliases)
    skeleton = lifter.build(stmts)
    lifter.saturate(iters=iters, k=k)
    lifter.sweep(skeleton)
    printer = _Printer(lifter.aliases)
    printer.line("sub_%04X {" % entry, 0)
    printer.seq(skeleton, 1)
    printer.line("}", 0)
    return LiftResult(entry, printer.out, lifter, skeleton)


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


def lift(model, entry=None, iters=24, k=12):
    """Lift one procedure of a committed model (default: the play procedure)."""
    stmts, aliases, entry = pass1(model, entry)
    return lift_stmts(stmts, aliases, entry, iters=iters, k=k)


# ---- end-to-end Z3 check: chosen terms equal originals under the SSA equations ---
_BV16 = z3.BitVecSort(16)


class _Z3Env:
    """Encodes IR terms over QF_BV with shared leaf constants per SSA version."""

    def __init__(self, locw):
        self.locw = locw
        self.alg = _Z3Alg()
        self.leaves = {}
        self.funcs = {}

    def enc(self, ir):
        k = ir[0]
        w = _ir_width(ir, self.locw)
        if k == "num":
            return z3.BitVecVal(ir[1], 8 * w)
        if k in ("cell", "loc"):
            key = (ir, w)
            if key not in self.leaves:
                self.leaves[key] = z3.BitVec("leaf_%d" % len(self.leaves), 8 * w)
            return self.leaves[key]
        if k == "load":
            sort = z3.BitVecSort(8 * w)
            f = self.funcs.setdefault(
                (ir[2], ir[3]), z3.Function("ld_%d_%d" % (ir[2], ir[3]), _BV16, sort)
            )
            return f(self.enc(ir[1]))
        return self._enc_op(ir, k, w)

    def _enc_op(self, ir, k, w):
        alg = self.alg
        if k == "zext":
            return alg.zext(self.enc(ir[1]))
        if k == "bnot":
            return alg.bnot(self.enc(ir[1]))
        x, y = self.enc(ir[1]), self.enc(ir[2])
        if k in _CMP_TAGS:
            return getattr(alg, k)(x, y)
        if k == "carry":
            return alg.carry(x, y, ir[3])
        return getattr(alg, k)(x, y, w)


def _loc_widths(defs):
    locw = {}
    for leaf, rhs in defs:
        if leaf[0] == "loc":
            locw[leaf[1]] = _ir_width(rhs, locw)
    return locw


def verify_lift(result, limit=None):
    """Z3-prove original == chosen for every rewritten site; returns the count."""
    defs = result.lifter.ssa.defs
    env = _Z3Env(_loc_widths(defs))
    s = z3.Solver()
    for leaf, rhs in defs:
        s.add(env.enc(leaf) == env.enc(rhs))
    proved = 0
    for it in result.lifter.ssa.items:
        if it.dropped or it.chosen == it.ir:
            continue
        s.push()
        s.add(env.enc(it.ir) != env.enc(it.chosen))
        r = s.check()
        s.pop()
        if r != z3.unsat:
            raise AssertionError("extracted term not equivalent: %r vs %r" % (it.ir, it.chosen))
        proved += 1
        if limit is not None and proved >= limit:
            break
    return proved
