"""Optimized MLIR (``cf``/``arith``/``memref``/``func``) structured and printed as compact C99.

Expressions carry their exact integer range under C's promotions, so a truncating cast is
printed only where a wider value would change a comparison, shift or subscript, and an operand
is made unsigned only where 16-bit ``int`` arithmetic could overflow.
"""

from __future__ import annotations

import bisect
import copy
import re
import math
import operator
from collections import Counter
from dataclasses import dataclass, field, replace

import networkx as nx
import numpy as np
from pycparser import c_ast
from wayward_eagle.c import prototypes, reduce_function
from wayward_eagle.c import stmt as cs

from .. import masks
from ..lifter import MODE_LEN, OPS
from . import llvmopt, mlir
from .mlir import Op, _types
from . import cnodes as cn, joins, marks, recompute, regions, ssa
from .state import INDEXED, INIT, POINTER, UNKNOWN, State, classify
from .structure import Conds, Graph, Structurer, new_ids

WIDTH = {"i1": 1, "i8": 8, "i16": 16, "i32": 8, "i64": 16, "index": 16}
CTYPE = {1: "uint8_t", 8: "uint8_t", 16: "uint16_t"}
CMP = {0: "==", 1: "!=", 2: "<", 3: "<=", 4: ">", 5: ">=", 6: "<", 7: "<=", 8: ">", 9: ">="}
FLIP = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", "<=": ">", ">": "<="}
BINOP = {
    "arith.addi": "+",
    "arith.subi": "-",
    "arith.muli": "*",
    "arith.divui": "/",
    "arith.remui": "%",
    "arith.andi": "&",
    "arith.ori": "|",
    "arith.xori": "^",
    "arith.shli": "<<",
    "arith.shrui": ">>",
}
#: Operations on signed operands, printed on ``intN_t`` casts.
SIGNED = {"arith.shrsi": ">>", "arith.divsi": "/", "arith.remsi": "%"}
#: ``min``/``max``: the predicate under which each picks its first operand.
MINMAX = {"arith.maxui": 8, "arith.minui": 6, "arith.maxsi": 4, "arith.minsi": 2}
INT16 = (-0x8000, 0x7FFF)
#: The lift's checks: calls computing a value with no effect.
CHECKS = ("@codechk", "@objchk")


@dataclass(frozen=True)
class E:  # pylint: disable=too-many-instance-attributes
    """A C expression: its ``c_ast`` node, exact range, whether it is unsigned where ``int``
    has 16 bits, whether it reads memory, the variables it reads and its operands
    ``(operator, left, right)`` when it is a comparison or negation."""

    node: c_ast.Node
    lo: int = 0
    hi: int = 0
    uns: bool = False
    mem: bool = False
    refs: frozenset = field(default_factory=frozenset)
    parts: tuple | None = None

    @property
    def op(self):
        """The top operator: a binary or unary operator, ``"cast"``, ``"?:"`` or none."""
        n = self.node
        if isinstance(n, (c_ast.BinaryOp, c_ast.UnaryOp)):
            return n.op
        return (
            "cast" if isinstance(n, c_ast.Cast) else "?:" if isinstance(n, c_ast.TernaryOp) else ""
        )


#: The register each load mnemonic sets to the byte it reads (``LAX`` sets ``A`` too).
LOADS = {"LDA": 0, "LDX": 1, "LDY": 2, "LAX": 1}


def meet(a, b):
    """The intersection of intervals ``a`` and ``b`` (``a`` where they do not meet)."""
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else a


def lit(v, width):
    """A literal of ``width`` bits: decimal below 10, else hex, unsigned near ``INT16_MAX``."""
    v &= (1 << width) - 1
    return E(cn.const(v, 0x7F00 <= v <= 0x7FFF), lo=v, hi=v, uns=v > 0x7EFF)


def is_lit(e, v=None):
    """Whether ``e`` is an integer literal (of value ``v``)."""
    return isinstance(e.node, c_ast.Constant) and (v is None or e.lo == e.hi == v)


def var(name, width):
    """A variable of ``width`` bits."""
    return E(cn.ident(name), lo=0, hi=(1 << width) - 1, uns=width == 16, refs=frozenset([name]))


def named(name, lo=0, hi=255, uns=False, mem=False, refs=frozenset()):
    """The expression named ``name`` (a macro, enumerator, array element or register)."""
    return E(cn.ident(name), lo, hi, uns, mem, refs)


def join(*es):
    """The ``mem`` and ``refs`` of expressions ``es`` combined."""
    return {"mem": any(e.mem for e in es), "refs": frozenset().union(*(e.refs for e in es))}


def plus(*es):
    """The 16-bit sum of expressions ``es``, left to right."""
    out = es[0]
    for e in es[1:]:
        out = binary("+", out, e, 16)
    return out


def cast(e, ctype, width, signed=False):
    """``e`` converted to ``ctype``: a conversion to an integer type at least as wide that
    it applies first is subsumed, as the outer one reduces modulo its width."""
    half = 1 << (width - 1)
    lo, hi = (-half, half - 1) if signed else (0, (1 << width) - 1)
    inner = e.node
    while (t := cn.cast_type(inner)) is not None and cn.BITS[t] >= width:
        inner = inner.expr
    return E(cn.cast(ctype, inner), lo, hi, width == 16 and not signed, **join(e))


def clean(e, width):
    """``e`` reduced to ``width`` bits, by a cast (a mask below a byte) when its range exceeds
    them."""
    if 0 <= e.lo and e.hi < (1 << width):
        return e
    if width < 8:
        return binary("&", e, lit((1 << width) - 1, width), 8)
    return cast(e, CTYPE[width], width)


def stored(e, width):
    """``e`` as the value of an assignment to a ``width``-bit unsigned object, which reduces
    it modulo its width: an outer integer cast at least as wide is subsumed, but on a
    constant, whose truncation it states."""
    t = cn.cast_type(e.node)
    if t is not None and t[0] == "u" and cn.BITS[t] >= max(width, 8) and (e.refs or e.mem):
        return replace(e, node=e.node.expr)
    return e


def _range(sym, a, b):
    if sym == "<<":
        return a.lo << b.lo, a.hi << b.hi
    if sym == ">>":
        return a.lo >> b.hi, a.hi >> b.lo
    if sym == "+":
        return a.lo + b.lo, a.hi + b.hi
    if sym == "-":
        return a.lo - b.hi, a.hi - b.lo
    if sym == "*":
        ps = [x * y for x in (a.lo, a.hi) for y in (b.lo, b.hi)]
        return min(ps), max(ps)
    if sym == "&":
        return 0, min(a.hi, b.hi)
    if sym == "%":
        return 0, max(0, min(a.hi, b.hi - 1))
    top = min((1 << max(a.hi, b.hi).bit_length()) - 1, a.hi + b.hi)
    return (max(a.lo, b.lo) if sym == "|" else 0), top


def binary(sym, a, b, width):
    """``a sym b`` with its exact range; an operand becomes unsigned where the result would
    overflow a 16-bit ``int``."""
    if sym == ">>" or (sym in ("&", "|", "^") and min(a.lo, b.lo) < 0):
        a, b = clean(a, width), clean(b, width)
    rng = _range(sym, a, b)
    uns = a.uns or (b.uns and sym not in ("<<", ">>"))
    if not uns and (rng[0] < INT16[0] or rng[1] > INT16[1]):
        if is_lit(a):
            a = replace(a, node=cn.const(a.lo, True), uns=True)
        else:
            a = cast(a, "uint16_t", 16)
        uns = True
    return E(cn.binop(sym, a.node, b.node), rng[0], rng[1], uns, **join(a, b))


def compare(sym, a, b):
    """``a sym b``."""
    return E(cn.binop(sym, a.node, b.node), 0, 1, parts=(sym, a, b), **join(a, b))


def negated(e):
    """The logical negation of truth value ``e``."""
    if e.lo == e.hi and not e.mem and not e.refs:
        return lit(int(not e.lo), 1)
    if e.parts and e.parts[0] in FLIP:
        return compare(FLIP[e.parts[0]], e.parts[1], e.parts[2])
    if e.parts and e.parts[0] == "!":
        return e.parts[1]
    return E(c_ast.UnaryOp("!", e.node), 0, 1, parts=("!", e), **join(e))


def truth(e):
    """``e`` as a condition: ``x != 0`` as ``x``, ``x == 0`` as ``!x``."""
    if e.parts and e.parts[0] in ("==", "!=") and is_lit(e.parts[2], 0) and e.parts[1].lo >= 0:
        x = e.parts[1]
        x = truth(x) if x.hi == 1 else x
        return x if e.parts[0] == "!=" else negated(x)
    return e


def scaled(c, a, b):
    """``c ? a : b`` with one arm zero and the other a literal ``k`` as a product, ``c * k``
    (``!c * k``), which decides nothing; else None."""
    for t, k, z in ((c, a, b), (negated(c), b, a)):
        if is_lit(z, 0) and is_lit(k) and 0 <= t.lo <= t.hi <= 1:
            return t if k.lo == 1 else binary("*", t, k, 16)
    return None


def ternary(c, a, b, **kw):
    """``c ? a : b``."""
    rng = {"lo": min(a.lo, b.lo), "hi": max(a.hi, b.hi), "uns": a.uns or b.uns}
    rng.update(kw)
    return E(c_ast.TernaryOp(c.node, a.node, b.node), **rng, **join(c, a, b))


NUMPY = {
    "arith.addi": operator.add,
    "arith.subi": operator.sub,
    "arith.muli": operator.mul,
    "arith.divui": operator.floordiv,
    "arith.remui": operator.mod,
    "arith.andi": operator.and_,
    "arith.ori": operator.or_,
    "arith.xori": operator.xor,
}
NUMPY_CMP = {
    0: operator.eq,
    1: operator.ne,
    2: operator.lt,
    3: operator.le,
    4: operator.gt,
    5: operator.ge,
    6: operator.lt,
    7: operator.le,
    8: operator.gt,
    9: operator.ge,
}
LOOP_STEPS = 256
#: Most values :meth:`Func.points` follows.
POINTS = 16


class Unknown(Exception):
    """A value depends on something other than the given block arguments."""


TERMINATORS = {"cf.br", "cf.cond_br", "cf.switch", "func.return"}
CASTS = {"arith.extui", "arith.index_castui", "arith.index_cast", "arith.trunci"}
REMAT = {"arith.constant", "ub.poison", "memref.get_global"}
#: The value ``x`` takes when ``op(x, c)`` is ``k``, for the invertible operations.
OFFSETS = {
    "arith.addi": lambda k, c: k - c,
    "arith.subi": lambda k, c: k + c,
    "arith.xori": lambda k, c: k ^ c,
}
#: The pure operations whose intervals :meth:`Func.iv` derives from their operands'.
DERIVED = CASTS | set(BINOP) | set(SIGNED) | set(MINMAX) | {"arith.select", "arith.extsi"}
#: Most combinations of inputs a flag is evaluated over (:meth:`Func.flag_value`).
FLAG_INPUTS = 1 << 16
#: A ``cmpi`` predicate with its operands swapped, and negated.
SWAPPED = {0: 0, 1: 1, 2: 4, 3: 5, 4: 2, 5: 3, 6: 8, 7: 9, 8: 6, 9: 7}
NEGATED = {0: 1, 1: 0, 2: 5, 3: 4, 4: 3, 5: 2, 6: 9, 7: 8, 8: 7, 9: 6}


def twos(v, width):
    """``v`` of ``width`` bits read as two's complement."""
    half = 1 << (width - 1)
    return (v ^ half) - half


def solution(pred, n, width):
    """The interval ``(lo, hi)`` of the ``width``-bit ``x`` for which ``x pred n`` holds, or
    None where it is no interval (``!=``, or a signed range across the sign bit)."""
    top, half = (1 << width) - 1, 1 << (width - 1)
    if pred in (2, 3, 4, 5):
        k = twos(n, width)
        lo, hi = {2: (-half, k - 1), 3: (-half, k), 4: (k + 1, half - 1), 5: (k, half - 1)}[pred]
        return (lo & top, hi & top) if lo <= hi and (lo < 0) == (hi < 0) else None
    return {0: (n, n), 6: (0, n - 1), 7: (0, n), 8: (n + 1, top), 9: (n, top)}.get(pred)


def line(pts, m):
    """``(a, s)`` with the points ``(k, v)`` on ``v = a + s * k`` modulo ``m + 1``, if any."""
    (k0, v0), rest = pts[0], [p for p in pts if p[1] != pts[0][1]]
    s = 0
    if rest:
        k1, v1 = rest[0]
        dv, dk = (v1 - v0) & m, k1 - k0
        if dv % dk:
            return None
        s = dv // dk
    a = (v0 - s * k0) & m
    return (a, s) if all((a + s * k) & m == v for k, v in pts) else None


def _resolve(sub, v):
    """``v`` through the substitution ``sub`` to a value it does not replace."""
    while v in sub:
        v = sub[v]
    return v


def is_write(o):
    """Whether ``o`` writes memory: a store or an ``io_write`` call."""
    return o.name == "memref.store" or o.prop("callee") == "@io_write"


def _add16(name, pc, x, y):
    """``(operations, low byte, high byte)`` of the 16-bit sum of the byte pairs ``x`` and
    ``y`` (``(high, low)`` values), its results named from ``name``."""
    ops = [Op("arith.constant", [f"{name}.c"], [], "value = 8 : i16", [], ((), ("i16",)))]

    def op(kind, args, t):
        ops.append(Op(kind, [f"{name}.{len(ops)}"], list(args), "", [], ((), (t,)), pc=pc))
        return ops[-1].res[0]

    def word(h, l):
        hz, lz = op("arith.extui", [h], "i16"), op("arith.extui", [l], "i16")
        return op("arith.ori", [op("arith.shli", [hz, f"{name}.c"], "i16"), lz], "i16")

    w = op("arith.addi", [word(*x), word(*y)], "i16")
    low = op("arith.trunci", [w], "i8")
    return ops, low, op("arith.trunci", [op("arith.shrui", [w, f"{name}.c"], "i16")], "i8")


def enum_lit(names, v):
    """Enumerator ``names[v]`` as a constant expression."""
    return named(names[int(v)], int(v), int(v))


def logic(sym, es):
    """``es`` joined by ``&&`` or ``||`` (none: the operator's identity)."""
    if not es:
        return lit(int(sym == "&&"), 1)
    if len(es) == 1:
        return es[0]
    node = es[0].node
    for e in es[1:]:
        node = cn.binop(sym, node, e.node)
    return E(node, 0, 1, **join(*es))


def enum_names(cell, vals):
    """``{value: name}`` of an opcode cell named ``cell`` holding ``vals``: each value by the
    mnemonic of the instruction it makes, with its mode where the mnemonic repeats and its
    value where both do."""
    ops = [OPS[v] for v in vals]

    def name(v, mn, mode):
        if sum(m == mn for m, _ in ops) == 1:
            return mn
        return (
            f"{mn}_{mode.upper()}" if ops.count((mn, mode)) == 1 else f"{mn}_{mode.upper()}_{v:02X}"
        )

    return {v: f"{cell}_{name(v, *op)}" for v, op in zip(vals, ops)}


class Func:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One ``func.func`` of the optimized module: its values, their ranges and uses."""

    def __init__(self, op, mod):
        self.op, self.mod = op, mod
        self.name = op.prop("sym_name")
        self.blocks = op.regions[0]
        self.block_of = {b.name: b for b in self.blocks}
        self.defs, self.types, self.uses, self.where = {}, {}, {}, {}
        self.users, self.dropped, self.globals = {}, set(), set()
        self.ivs = self.ivs0 = {}
        self.unread = set()
        #: The loads printed as their base and index (a block's checked read).
        self.split_reads = set()
        self.addr_edges, self.arg_src, self.arg_block, self.dead = set(), {}, {}, set()
        self.flags = set()
        self.kparam, self.kvals, self.ktabs, self.kmemo, self.kiv = None, None, {}, {}, {}
        self.kptrs, self.wide, self.promoted, self.aware = {}, {}, {}, set()
        self.steps, self.elements, self.knowns, self.symbols = {}, {}, {}, None
        #: The 16-bit values printed as one read of a split table (:meth:`CFunc.word_reads`).
        self.wordreads = {}
        self.index()
        self.idom = self.dominators()
        self.live_args = list(range(len(self.blocks[0].args)))
        self.live_res = list(range(len(self.results())))
        self.conds = Conds()

    @property
    def private(self):
        """Whether this is a lifted function kept as a call, not an entry."""
        return self.op.prop("sym_visibility") == "private"

    def results(self):
        """The result types of the function."""
        ft = self.op.prop("function_type")
        return _types(ft.split("->", 1)[1])

    def index(self):
        """Record definitions, types, uses and positions, and the edges into block arguments."""
        self.contexts, self.preds = {}, None
        for blk in self.blocks:
            for n, t in blk.args:
                self.types[n], self.arg_block[n] = t, blk
            for i, o in enumerate(blk.ops):
                self.where[id(o)] = (blk, i)
                if o.name == "memref.get_global" and o.prop("name") == "@G":
                    self.symbols = o.res[0]
                elif o.name == "memref.get_global":
                    self.globals.add(o.res[0])
                for j, r in enumerate(o.res):
                    self.defs[r] = o
                    self.types[r] = o.types[1][j]
                for a in o.args:
                    self.uses[a] = self.uses.get(a, 0) + 1
                    self.users.setdefault(a, []).append(o)
                if o.name == "cf.switch" and o.types[0][0] == "i16":
                    self.flags.add(o.args[0])
                for succ, idx, case in o.edges():
                    for (a, _), i in zip(self.block_of[succ].args, idx):
                        self.arg_src.setdefault(a, []).append((o, i, case))

    def reindex(self):
        """Record definitions, types and uses again after operations were rewritten: every
        interval of the module is found again (:meth:`Module.forget`)."""
        self.defs, self.types, self.uses, self.where = {}, {}, {}, {}
        self.users, self.globals, self.arg_src = {}, set(), {}
        self.arg_block, self.flags, self.kmemo = {}, set(), {}
        self.addr_edges = set()
        self.index()
        self.mod.forget()

    def forget(self):
        """Drop the intervals found, per value, per instance index, per block and the
        constants loads read by them."""
        self.ivs = self.ivs0 = {}
        self.kiv, self.contexts, self.knowns = {}, {}, {}

    def iv0(self, v):
        """The interval of value ``v`` in no context (no instance index, no block): what
        another function's interval or a cached fact may read, whichever context this
        function's intervals are switched to (:meth:`kivs`, :meth:`iv_at`)."""
        if self.ivs is self.ivs0:
            return self.iv(v)
        saved, self.ivs = self.ivs, self.ivs0
        try:
            return self.iv(v)
        finally:
            self.ivs = saved

    def instance(self, kpos):
        """Take block argument ``kpos`` as the instance index when every call site passes
        a constant for it: its values are ``kvals``."""
        sites = self.mod.sites.get(self.name) or ()
        vals = [f.const_of(o.args[kpos]) for f, o in sites]
        if vals and None not in vals:
            self.kparam = self.blocks[0].args[kpos][0]
            self.kvals = np.array(sorted({v & 0xFF for v in vals}), dtype=np.int64)

    def kvec(self, v):
        """The value of ``v`` for each instance index (``kvals``) when it depends on the
        index alone, else None."""
        if self.kparam is None:
            return None
        try:
            r = self.evaluate(v, {self.kparam: self.kvals}, self.kmemo)
        except Unknown:
            return None
        return np.broadcast_to(r, self.kvals.shape)

    def kivs(self, v):
        """The interval of ``v`` for each instance index, or None outside an instance."""
        if self.kparam is None:
            return None
        saved, out = self.ivs, []
        try:
            for k in self.kvals.tolist():
                self.ivs = self.kiv.setdefault(k, {self.kparam: (k, k)})
                out.append(self.iv(v))
        finally:
            self.ivs = saved
        return out

    def carry16(self, live, funcs):
        """Rewrite each 8-bit add whose carry only feeds the add of the byte above it as one
        16-bit add: ``lo = a + b`` and ``hi = d + e + (lo < a)`` become the bytes of
        ``(d:a) + (e:b)``, the 6502's own carry chain, read where something needs it
        (:meth:`live_users`). The new operations go before the high add; ``lo``'s uses after
        it read the new low byte, and the stores of ``lo`` before it move to it
        (:meth:`sunk`). Whether any chain was found."""
        found, used = [], set()
        for o in self.walk():
            pat = self.carry_chain(o, live, funcs)
            if pat is not None and not {pat[0].res[0], pat[5]} & used:
                found.append(pat)
                used |= {pat[0].res[0], pat[5]}
        if not found:
            return False
        hi_of, lo_of, moves = {}, {}, []
        for k, (top, a, b, hd, he, lo) in enumerate(found):
            ops, low, high = _add16(f"%w{k}", top.pc, (hd, a), (he, b))
            lo_of[lo] = (top, low)
            hi_of[top.res[0]] = high
            blk, at = self.where[id(top)]
            blk.ops[at:at] = ops
            for x in ops:
                x.parent = blk
            self.reindex()
            moves += [(u, top, low, lo) for u in self.live_users(lo, live, funcs)]
        for u, top, low, lo in moves:
            if u.name == "memref.store" and u.args[0] == lo and not self.after(top, u):
                if self.sink(u, top, low):
                    self.reindex()
        for o in self.walk():
            for i, x in enumerate(o.args):
                if x in hi_of:
                    o.args[i] = hi_of[x]
                elif x in lo_of and self.after(lo_of[x][0], o):
                    o.args[i] = lo_of[x][1]
        self.reindex()
        return True

    def sink(self, st, top, low):
        """Move store ``st`` to just before ``top`` (writing ``low``) when nothing between
        reads or writes what it writes and the way is a run of blocks each entered only from
        the one before, left otherwise by a ``trap``, which does not return (:meth:`sunk`)."""
        if self.sunk(st, top) is None:
            return False
        blk, i = self.where[id(st)]
        del blk.ops[i]
        end = self.where[id(top)][0]
        end.ops.insert(next(j for j, x in enumerate(end.ops) if x is top), st)
        st.parent, st.args[0] = end, low
        return True

    def between(self, a, b):
        """The operations between operation ``a`` and the later ``b`` when ``a`` runs only
        before ``b`` along one run of blocks, each entered only from the one before and left
        for the next or a ``trap``, which does not return; else None."""
        blk, i = self.where[id(a)]
        end, at = self.where[id(b)]
        if blk is end:
            return blk.ops[i + 1 : at] if i < at else None
        out, seen = blk.ops[i + 1 : -1], {blk.name}
        while blk is not end:
            term = blk.ops[-1]
            nxt = [s for s in term.succs if not self.traps(self.block_of[s])]
            if term.name not in ("cf.br", "cf.cond_br") or len(nxt) != 1:
                return None
            blk = self.block_of[nxt[0]]
            if self.pred_counts()[blk.name] != 1 or blk.name in seen:
                return None
            seen.add(blk.name)
            out = out + (blk.ops[:at] if blk is end else blk.ops[:-1])
        return out

    def sunk(self, st, top):
        """The blocks from the one holding store ``st`` to the one holding ``top`` when ``st``
        may move to ``top``: no operation between (:meth:`between`) reads or writes the bytes
        ``st`` writes, or calls out; else None."""
        ops = self.between(st, top)
        if ops is None:
            return None
        lo, hi = self.addr_iv(st)
        for x in ops:
            if x.name in ("memref.store", "memref.load") and x.args[-2] in self.globals:
                a, b = self.addr_iv(x)
                if a <= hi and lo <= b:
                    return None
            elif self.effect(x):
                return None
        return ops

    def word_of(self, v):
        """``(w, 0)`` or ``(w, 8)`` when byte ``v`` is the low or high byte of the 16-bit
        value ``w`` (``trunci w``, ``trunci (w >> 8)``), else None."""
        d = self.defs.get(v)
        if d is None or d.name != "arith.trunci" or self.width(d.args[0]) != 16:
            return None
        e = self.defs.get(d.args[0])
        if e is not None and e.name == "arith.shrui" and self.const_of(e.args[1]) == 8:
            return e.args[0], 8
        return d.args[0], 0

    def joined(self, o):
        """``(high, low, chain)`` when ``o`` is ``(high << 8) | low`` of two byte values, the
        operations of ``chain`` the shift and the two zero extensions."""
        if o.name != "arith.ori" or self.width(o.res[0]) != 16:
            return None
        for a, b in (o.args, o.args[::-1]):
            hi, lo = self.defs.get(a), self.defs.get(b)
            if (
                hi is None
                or lo is None
                or hi.name != "arith.shli"
                or self.const_of(hi.args[1]) != 8
            ):
                continue
            up = self.defs.get(hi.args[0])
            if up is not None and (up.name, lo.name) == ("arith.extui", "arith.extui"):
                if self.width(up.args[0]) == self.width(lo.args[0]) == 8:
                    return up.args[0], lo.args[0], (hi, up, lo)
        return None

    def reaching(self, k):
        """``{id(load): store}`` of the loads of byte ``k`` (a constant address) that one store
        of ``k`` reaches along every path, with no operation that may write ``k`` after it
        (a store whose address interval holds it, a call out)."""
        mem = self.globals
        preds = {b.name: [] for b in self.blocks}
        for b in self.blocks:
            for s in b.ops[-1].succs:
                preds[s].append(b.name)

        def exact(x):
            return x.name == "memref.store" and self.const_of(self.address(x)) == k

        def writes(x):
            if id(x) in self.dropped:
                return False
            if x.name == "memref.store" and x.args[-2] in mem:
                lo, hi = self.addr_iv(x)
                return lo <= k <= hi
            if x.name != "func.call":
                return False
            return any(a <= k <= b for a, b in self.mod.written(x.prop("callee")[1:]))

        def run(b, state, found=None):
            for x in b.ops:
                if found is not None and x.name == "memref.load" and x.args[-2] in mem:
                    if self.const_of(self.address(x)) == k:
                        found[id(x)] = state
                elif writes(x):
                    state = x if exact(x) else ""
            return state

        out, work = {}, list(self.blocks)
        while work:
            b = work.pop(0)
            ins = [out[p] for p in preds[b.name] if p in out]
            state = "" if b is self.blocks[0] else None
            for x in ins:
                state = x if state is None or state is x else ""
            if state is None:
                continue
            new = run(b, state)
            if out.get(b.name, 0) is not new:
                out[b.name] = new
                work += [self.block_of[s] for s in b.ops[-1].succs]
        found = {}
        for b in self.blocks:
            ins = [out[p] for p in preds[b.name] if p in out]
            state = "" if b is self.blocks[0] else None
            for x in ins:
                state = x if state is None or state is x else ""
            if state is not None:
                run(b, state, found)
        return {i: st for i, st in found.items() if st}

    def source(self, v, reach):
        """``(w, 0|8)`` of byte ``v`` (:meth:`word_of`), or of the byte a load of a constant
        address reads where one store reaches it (``reach``, a cache of :meth:`reaching`)."""
        d = self.defs.get(v)
        if d is None or d.name != "memref.load" or d.args[-2] not in self.globals:
            return self.word_of(v)
        k = self.const_of(self.address(d))
        if k is None or self.named(d) is not None:
            return None
        if k not in reach:
            reach[k] = self.reaching(k)
        st = reach[k].get(id(d))
        return None if st is None else self.word_of(st.args[0])

    def pairs(self):
        """Replace each ``(high << 8) | low`` whose bytes are the halves of one 16-bit value
        ``w`` (:meth:`source`: its ``trunci`` halves, or loads of the two bytes ``w`` was stored
        to) by ``w``. Whether any was."""
        sub, reach = {}, {}
        for o in self.walk():
            hl = None if id(o) in self.dropped else self.joined(o)
            if hl is None:
                continue
            hi, lo = (self.source(x, reach) for x in hl[:2])
            if hi is not None and lo is not None and hi == (lo[0], 8) and lo[1] == 0:
                sub[o.res[0]] = lo[0]
        if sub:
            self.substitute(sub)
            self.reindex()
        return bool(sub)

    def fold_flags(self):
        """Read each truth value (:meth:`is_flag`) an arithmetic operation consumes as the
        constant it is (:meth:`flag_value`), for LLVM's passes to fold forward."""
        new, memo = {}, {}
        for o in list(self.walk()):
            if not o.name.startswith("arith.") or o.name == "arith.constant":
                continue
            for i, c in enumerate(o.args):
                if (c in self.defs or c in self.arg_block) and self.types.get(c) in WIDTH:
                    if c not in memo:
                        memo[c] = self.flag_value(c)
                    if memo[c] is not None:
                        o.args[i] = self.constant(new, self.types[c], memo[c], o.pc, "%fc")
        if new:
            self.prepend(new)
            self.reindex()

    def flag_value(self, c):
        """The one value truth value ``c`` (:meth:`is_flag`) takes, or None: its values over
        every combination (at most :data:`FLAG_INPUTS`) of the values it is computed from,
        each over every value of its type (an instance index over its copies). No interval,
        so no byte of memory, decides it: the data does not shape the C."""
        if self.iv(c) != (0, 1) or not self.is_flag(c, set()):
            return None
        used = sorted(self.free_values(c, set()))
        doms = [
            np.asarray(self.kvals) if x == self.kparam else np.arange(1 << self.width(x))
            for x in used
        ]
        if math.prod(len(d) for d in doms) > FLAG_INPUTS:
            return None
        grid = np.meshgrid(*doms, indexing="ij")
        env = {x: g.ravel() for x, g in zip(used, grid)}
        try:
            vals = np.unique(self.evaluate(c, env, {}))
        except (Unknown, IndexError):
            return None
        return int(vals[0]) if len(vals) == 1 else None

    def is_flag(self, v, seen):
        """Whether value ``v`` is a truth value: an ``i1``, its zero extension, or a block
        argument every edge gives one."""
        if v in seen:
            return True
        seen.add(v)
        if self.types.get(v) == "i1":
            return True
        d = self.defs.get(v)
        if d is not None:
            return d.name == "arith.extui" and self.types.get(d.args[0]) == "i1"
        srcs = self.arg_src.get(v)
        return bool(srcs) and all(self.is_flag(o.args[i], seen) for o, i, _ in srcs)

    def carry(self, v):
        """Whether value ``v`` is a truth value (:meth:`is_flag`) an add or subtract reads as
        data: a carry or borrow the code leaves for an ``ADC`` or ``SBC``, its own variable."""
        if self.types.get(v) != "i1" and v not in self.arg_block:
            return False
        if self.iv(v) != (0, 1) or not self.is_flag(v, set()):
            return False
        work, seen = [v], set()
        while work:
            x = work.pop()
            for u in self.users.get(x, ()):
                if u.name in ("arith.addi", "arith.subi"):
                    return True
                if u.name in ("arith.extui", "arith.xori") and u.res[0] not in seen:
                    seen.add(u.res[0])
                    work.append(u.res[0])
        return False

    def free_values(self, v, seen):
        """The values ``v`` is computed from through arithmetic: block arguments, loads and
        results of other operations."""
        if v in seen:
            return set()
        seen.add(v)
        d = self.defs.get(v)
        if d is not None and d.name == "arith.constant":
            return set()
        if d is None or not d.name.startswith("arith."):
            return {v}
        return set().union(*(self.free_values(a, seen) for a in d.args))

    def constant(self, new, t, v, pc, prefix):
        """The value of a constant ``v`` of type ``t`` among the new operations ``new``, its
        definition recorded at once."""
        k = self.kop(new, "arith.constant", [], t, pc, f"value = {v} : {t}", prefix=prefix)
        self.defs[k] = next(x for x in new.values() if x.res[0] == k)
        return k

    def after(self, a, b):
        """Whether operation ``b`` runs after ``a`` on every path: later in its block, or in a
        block ``a``'s block dominates."""
        (ba, ia), (bb, ib) = self.where[id(a)], self.where[id(b)]
        return ib > ia if ba is bb else self.dominates(ba.name, bb.name)

    def carry_chain(self, o, live, funcs):
        """``(high add, a, b, d, e, lo)`` when ``o`` is ``lo < a`` (unsigned, ``lo = a + b`` in 8
        bits) and its value only feeds, zero-extended, ``hi = (d + e) + carry``, no other
        operation needing a value of the chain but ``lo``."""
        if o.name != "arith.cmpi" or o.int_prop("predicate") != 6:
            return None
        lo, a = o.args
        d = self.defs.get(lo)
        if d is None or d.name != "arith.addi" or a not in d.args or self.types[lo] != "i8":
            return None
        b = d.args[1] if d.args[0] == a else d.args[0]
        users = self.live_users(o.res[0], live, funcs)
        if len(users) != 1 or users[0].name != "arith.extui":
            return None
        z = users[0].res[0]
        zu = self.live_users(z, live, funcs)
        if len(zu) != 1 or zu[0].name != "arith.addi" or self.types[z] != "i8":
            return None
        top = zu[0]
        x = top.args[1] if top.args[0] == z else top.args[0]
        dx = self.defs.get(x)
        if dx is None or dx.name != "arith.addi" or len(self.live_users(x, live, funcs)) != 1:
            return None
        return (top, a, b, dx.args[0], dx.args[1], lo)

    def kfold(self):
        """Give each value computed from the instance index alone (not a condition: a
        condition of the index alone tests such a value) one definition at the entry per
        distinct set of per-index values: the least value plus a ``arith.kfam`` of the index,
        8 bits wide where the offsets fit, which prints as ``base + k`` where it steps by one
        with the index, else as a constant table."""
        if self.kparam is None:
            return
        self.kmerge()
        new = {}
        self.mark_addresses()
        for o in list(self.walk()):
            if (
                len(o.res) == 1
                and self.types[o.res[0]] not in ("index", "i1")
                and self.kvec(o.res[0]) is not None
            ):
                continue
            for i, a in enumerate(o.args):
                t = self.types.get(a)
                if a == self.kparam or t not in ("i8", "i16"):
                    continue
                vec = self.kvec(a)
                if vec is None or (vec == self.kvals).all() or self.const_of(a) is not None:
                    continue
                o.args[i] = self.kdefine(t, vec, new, o.pc, (id(o), i) in self.addr_edges)
        if new:
            self.prepend(new)
            self.reindex()

    def kedges(self, blk):
        """Per instance index the edge ``(terminator id, case)`` into block ``blk`` that the
        index takes from ``blk``'s immediate dominator (None where it does not reach
        ``blk``), when the index alone decides every branch on the way; else None."""
        top = self.idom.get(blk)
        if top is None or top == blk:
            return None
        out = []
        for k in self.kvals.tolist():
            env, memo, b, seen, edge = {self.kparam: np.int64(k)}, {}, top, set(), None
            while b not in seen:
                seen.add(b)
                term = self.block_of[b].ops[-1]
                es = term.edges()
                if not es:
                    break
                try:
                    c = int(self.evaluate(term.args[0], env, memo)) if len(es) > 1 else None
                except Unknown:
                    return None
                if term.name == "cf.cond_br":
                    e = es[0] if c else es[1]
                elif term.name == "cf.switch":
                    e = next((x for x in es[1:] if x[2] == c), es[0])
                else:
                    e = es[0]
                if e[0] == blk:
                    edge = (id(term), e[2])
                    break
                b = e[0]
            else:
                return None
            out.append(edge)
        return out

    def kshape(self, v, blk, memo):
        """The form of value ``v`` as seen from block ``blk``: ``("k", v)`` where it depends on
        the instance index alone, ``("op", op, args)`` of a pure operation's operands' forms
        (an equality of a difference with zero read as the equality of its operands), else
        ``("v", v)`` where it is defined where ``blk`` is dominated; None otherwise (``memo``
        holds the forms made)."""
        if v not in memo:
            memo[v] = self.kform(v, blk, memo)
        return memo[v]

    def kform(self, v, blk, memo):
        """:meth:`kshape` of ``v``, made."""
        if self.types.get(v) in ("i8", "i16") and self.kvec(v) is not None:
            return ("k", v)
        d = self.defs.get(v)
        if d is None or len(d.res) != 1 or not d.name.startswith("arith."):
            home = self.arg_block[v].name if d is None else self.where[id(d)][0].name
            return ("v", v) if self.dominates(home, blk) else None
        args = d.args
        if d.name == "arith.cmpi" and d.int_prop("predicate") in (0, 1):
            x = self.defs.get(args[0])
            if x is not None and x.name == "arith.subi" and self.const_of(args[1]) == 0:
                args = x.args
        forms = [self.kshape(a, blk, memo) for a in args]
        return None if None in forms else ("op", d, forms)

    def kunify(self, forms, blk, entry, made):
        """The value of the per-index ``forms`` (``[(index position, form)]``) alike up to
        their per-index leaves: a value they share, the per-index leaves' values chosen by the
        index (a constant where they agree, else an ``arith.kfam`` among the entry's new
        operations ``entry``) or the operation of those, appended to ``made`` (``blk``'s new
        operations); None where they differ."""
        kinds = {f[0] for _, f in forms}
        if len(kinds) != 1:
            return None
        kind = kinds.pop()
        if kind != "op" and len({f[1] for _, f in forms}) == 1:
            return forms[0][1][1]
        if kind == "v":
            return None
        if kind == "k":
            t = self.types[forms[0][1][1]]
            if any(self.types[f[1]] != t for _, f in forms):
                return None
            vec = np.zeros(len(self.kvals), dtype=np.int64)
            vec[:] = self.kvec(forms[0][1][1])[forms[0][0]]
            for i, f in forms:
                vec[i] = self.kvec(f[1])[i]
            if (vec == vec[0]).all():
                props = f"value = {vec[0]} : {t}"
                return self.kop(entry, "arith.constant", [], t, None, props, prefix="%m")
            offs = tuple(vec.tolist())
            return self.kop(entry, "arith.kfam", [self.kparam], t, None, offs=offs, prefix="%m")
        d, args = forms[0][1][1], []
        if all(f[1] is d for _, f in forms) and self.dominates(self.where[id(d)][0].name, blk):
            return d.res[0]
        if len({(f[1].name, f[1].props, len(f[2])) for _, f in forms}) != 1:
            return None
        for j in range(len(forms[0][1][2])):
            a = self.kunify([(i, f[2][j]) for i, f in forms], blk, entry, made)
            if a is None:
                return None
            args.append(a)
        res = f"%m{len(self.defs)}.{len(made)}"
        made.append(Op(d.name, [res], args, d.props, [], d.types, pc=d.pc))
        self.types[res] = d.types[1][0]
        return res

    def kmerge(self):
        """Read each block argument the instance index alone chooses (every edge into its
        block decided by the index from the block's immediate dominator, none a back edge)
        whose values on those edges are alike up to per-index values, as the operation of
        the values the index chooses: a compare some copies have (``CMP #k``) and the others'
        zero test are then one compare with a per-copy value, ``v == k`` or a constant
        table, as each copy's code compares."""
        sub, entry = {}, {}
        for blk in self.blocks[1:]:
            srcs = [self.arg_src.get(v, []) for v, _ in blk.args]
            if not blk.args or any(
                self.dominates(blk.name, o.parent.name) for ss in srcs for o, _, _ in ss
            ):
                continue
            edges = self.kedges(blk.name)
            if edges is None:
                continue
            made, memo = [], {}
            for (v, _), ss in zip(blk.args, srcs):
                by = {(id(o), case): o.args[i] for o, i, case in ss}
                if len(by) < 2:
                    continue
                forms = [
                    (i, self.kshape(by[e], blk.name, memo)) for i, e in enumerate(edges) if e in by
                ]
                if not forms or any(f is None for _, f in forms):
                    continue
                n = len(made)
                r = self.kunify(forms, blk.name, entry, made)
                if r is None or r == v:
                    del made[n:]
                else:
                    sub[v] = r
            blk.ops[0:0] = made
            for o in made:
                o.parent = blk
        if not sub:
            return
        self.prepend(entry)
        self.substitute(sub)
        self.reindex()

    def kdefine(self, t, vec, out, pc, address):
        """The value ``vec`` (per instance index) of type ``t`` as new operations ``out``: the
        least value split off a 16-bit one or an ``address`` (it prints relative to its
        region), the rest from the index."""
        lo = int(vec.min()) if address or t == "i16" else 0
        offs = vec - lo
        kt = "i8" if offs.max() <= 0xFF else t
        if (offs == self.kvals).all():
            kt, name = self.types[self.kparam], self.kparam
        else:
            name = self.kop(out, "arith.kfam", [self.kparam], kt, pc, offs=tuple(offs.tolist()))
        if kt != t:
            name = self.kop(out, "arith.extui", [name], t, pc)
        if lo:
            c = self.kop(out, "arith.constant", [], t, pc, props=f"value = {lo} : {t}")
            name = self.kop(out, "arith.addi", [c, name], t, pc)
        return name

    def kop(self, out, name, args, t, pc, props="", offs=None, prefix="%k"):
        """The result of operation ``name`` of ``args`` (type ``t``) among the new operations
        ``out`` (``{key: op}``), added unless there (``offs``: an ``arith.kfam``'s values),
        named ``prefix`` and its number."""
        key = (name, tuple(args), t, props, offs)
        if key not in out:
            res = f"{prefix}{len(out)}"
            if offs is not None:
                self.ktabs[res] = tab = np.zeros(int(self.kvals.max()) + 1, dtype=np.int64)
                tab[self.kvals] = offs
            self.types[res] = t
            types = (tuple(self.types[a] for a in args), (t,))
            out[key] = Op(name, [res], list(args), props, [], types, pc=pc)
        return out[key].res[0]

    def kpointers(self):
        """Address the per-copy state of an instance whose per-copy offsets do not fit 8 bits
        through per-copy pointers: the accesses at a 16-bit table's offsets from constants, in
        one region or held span for each copy, are grouped by the table and the array; each
        group's constants are covered by the fewest windows of 256 bytes, each window a
        ``arith.kfam`` of the copies' addresses of its least constant (:attr:`kptrs`, a table of
        pointers into the array) and every access in it that pointer plus a byte, as the
        6502's ``(zp),Y`` reads a field at a base held per copy."""
        if self.kparam is None:
            return
        groups = {}
        for o in self.walk():
            if o.name not in ("memref.load", "memref.store") or id(o) in self.dropped:
                continue
            if o.args[-2] not in self.globals or id(o) in self.promoted:
                continue
            c, rest = self.split(self.address(o))
            d = self.defs.get(rest)
            if d is None or d.name != "arith.kfam" or self.width(rest) != 16:
                continue
            place = self.place(o)
            if place is not None:
                groups.setdefault((rest, place), []).append((c, o))
        new = {}
        for (rest, place), accs in groups.items():
            starts = []
            for c in sorted({c for c, _ in accs}):
                if not starts or c - starts[-1] > 0xFF:
                    starts.append(c)
            per = self.ktabs[rest][self.kvals]
            for c, o in accs:
                s = starts[bisect.bisect_right(starts, c) - 1]
                offs = tuple(int(x) for x in (s + per) & 0xFFFF)
                ptr = self.kop(
                    new, "arith.kfam", [self.kparam], "i16", o.pc, offs=offs, prefix="%p"
                )
                self.kptrs[ptr] = place
                a = ptr
                if c != s:
                    k = self.kop(
                        new,
                        "arith.constant",
                        [],
                        "i16",
                        o.pc,
                        f"value = {c - s} : i16",
                        prefix="%p",
                    )
                    a = self.kop(new, "arith.addi", [ptr, k], "i16", o.pc, prefix="%p")
                idx = self.defs.get(o.args[-1])
                if idx is not None and idx.name == "arith.index_castui":
                    a = self.kop(new, "arith.index_castui", [a], "index", o.pc, prefix="%p")
                self.uses[o.args[-1]] -= 1
                o.args[-1] = a
                self.uses[a] = self.uses.get(a, 0) + 1
        self.prepend(new)

    def substitute(self, sub):
        """Replace every operand ``a`` in ``sub`` by what it resolves to."""
        for o in self.walk():
            o.args = [_resolve(sub, a) for a in o.args]

    def pred_counts(self):
        """Each block's number of predecessor edges, kept until the next :meth:`index`."""
        if self.preds is None:
            self.preds = Counter(s for b in self.blocks for s in b.ops[-1].succs)
        return self.preds

    def prepend(self, new):
        """Put the new operations ``new`` (``{key: op}``) first in the entry block."""
        self.insert_first(self.blocks[0], list(new.values()))

    def insert_first(self, blk, ops):
        """Put the new single-result operations ``ops`` first in block ``blk``, recording
        their definitions, uses and the block's positions."""
        blk.ops[0:0] = ops
        for i, o in enumerate(blk.ops):
            self.where[id(o)] = (blk, i)
        for o in ops:
            o.parent = blk
            self.defs[o.res[0]] = o
            for a in o.args:
                self.uses[a] = self.uses.get(a, 0) + 1
                self.users.setdefault(a, []).append(o)

    def param_of(self, v, through, seen):
        """The index of the parameter value ``v`` always equals, or None: the parameter, a
        block argument every edge gives one, or a call's result its callee returns from the
        parameter (``through``) the call passes one."""
        params = [a for a, _ in self.blocks[0].args]
        if v in params:
            return params.index(v)
        if v in seen:
            return None
        seen.add(v)
        d = self.defs.get(v)
        if d is None:
            srcs = self.arg_src.get(v)
            ps = {self.param_of(o.args[i], through, seen) for o, i, _ in srcs or ()}
            return ps.pop() if len(ps) == 1 else None
        if d.name == "func.call":
            p = through.get(d.prop("callee")[1:], {}).get(d.res.index(v))
            return None if p is None else self.param_of(d.args[p], through, seen)
        return None

    def dominators(self):
        """``{block name: immediate dominator name}``."""
        g = nx.DiGraph()
        g.add_node(self.blocks[0].name)
        for b in self.blocks:
            g.add_edges_from((b.name, s) for s in b.ops[-1].succs)
        return nx.immediate_dominators(g, self.blocks[0].name)

    def context(self, blk):
        """``(ivs, values)`` wherever block ``blk`` runs: ``ivs`` the intervals the branches
        every path to it takes imply (into each block of its dominator chain entered by that
        edge alone) and the other operands' of ``values``, the pure values computed from those
        bounded, whose intervals :meth:`iv` derives from ``ivs``."""
        if blk in self.contexts:
            return self.contexts[blk]
        if self.ivs is not self.ivs0:
            saved, self.ivs = self.ivs, self.ivs0
            try:
                return self.context(blk)
            finally:
                self.ivs = saved
        preds, facts, b = self.pred_counts(), {}, blk
        while (d := self.idom.get(b, b)) != b:
            term = self.block_of[d].ops[-1]
            if preds[b] == 1 and term.name in ("cf.cond_br", "cf.switch"):
                case = next(c for s, _, c in term.edges() if s == b)
                if term.name == "cf.cond_br":
                    self.implied(term.args[0], case, facts)
                elif case is not None:
                    k = case & (1 << self.width(term.args[0])) - 1
                    self.narrow(term.args[0], (k, k), facts)
            b = d
        ivs = {v: meet(self.iv(v), r) for v, r in facts.items()}
        values, work = set(ivs), list(ivs)
        while work:
            for o in self.users.get(work.pop(), ()):
                if o.name in DERIVED and len(o.res) == 1 and o.res[0] not in values:
                    values.add(o.res[0])
                    work.append(o.res[0])
        for v in values - set(ivs):
            for a in self.defs[v].args:
                if a not in values:
                    ivs[a] = self.iv(a)
        self.contexts[blk] = ivs, values
        return ivs, values

    def implied(self, c, holds, facts):
        """Meet into ``facts`` the intervals truth value ``c`` being ``holds`` implies: of the
        operand of a compare with a constant, through its negation and the operands of an
        ``||`` false or an ``&&`` true."""
        d = self.defs.get(c)
        if d is None:
            return
        k = [self.const_of(a) for a in d.args]
        if d.name == "arith.xori" and k[1] is not None and k[1] & 1:
            self.implied(d.args[0], not holds, facts)
        elif d.name == ("arith.andi" if holds else "arith.ori") and self.width(c) == 1:
            for a in d.args:
                self.implied(a, holds, facts)
        elif d.name == "arith.cmpi" and None in k and k != [None, None]:
            pred, x = d.int_prop("predicate"), d.args[k.index(None)]
            pred = pred if k[1] is not None else SWAPPED.get(pred)
            pred = pred if holds else NEGATED.get(pred)
            top = (1 << self.width(x)) - 1
            n = next(v for v in k if v is not None) & top
            r = solution(pred, n, self.width(x))
            if pred == 1:
                lo, hi = self.iv(x)
                r = (lo + 1, hi) if n == lo else (lo, hi - 1) if n == hi else None
            if r is not None and r[0] <= r[1]:
                self.narrow(x, r, facts)

    def narrow(self, v, r, facts):
        """Meet interval ``r`` of value ``v`` into ``facts``, and the interval it gives the value
        ``v`` extends or offsets by a constant (where that does not wrap)."""
        while True:
            facts[v] = meet(facts.get(v, r), r)
            d = self.defs.get(v)
            if d is None:
                return
            if d.name in ("arith.extui", "arith.index_castui"):
                top = (1 << self.width(d.args[0])) - 1
                if r[0] > top:
                    return
                v, r = d.args[0], (r[0], min(r[1], top))
                continue
            k = [self.const_of(a) for a in d.args]
            if d.name not in ("arith.addi", "arith.subi") or k.count(None) != 1:
                return
            j = k.index(None)
            if d.name == "arith.subi" and j:
                return
            off = k[1 - j] if d.name == "arith.addi" else -k[1]
            top = (1 << self.width(v)) - 1
            lo, hi = (r[0] - off) & top, (r[1] - off) & top
            if hi - lo != r[1] - r[0]:
                return
            v, r = d.args[j], (lo, hi)

    def code_iv(self, o):
        """The interval of load ``o`` of an opcode cell (:attr:`Module.opcodes`): the hull of
        the opcodes the code stores in it; else None. The lift's other value sets are of the
        program's data, which the ``.c`` does not depend on."""
        vals = self.mod.opcodes.get(self.const_of(self.address(o)))
        return (vals[0], vals[-1]) if vals else None

    def iv_at(self, v, blk):
        """The interval of value ``v`` wherever block ``blk`` runs (:meth:`context`)."""
        ivs, values = self.context(blk)
        if v not in values:
            return self.iv(v)
        saved, self.ivs = self.ivs, ivs
        try:
            return self.iv(v)
        finally:
            self.ivs = saved

    def addr_iv(self, o):
        """The interval of access ``o``'s address where it runs."""
        return self.iv_at(self.address(o), self.where[id(o)][0].name)

    def dominates(self, a, b):
        """Whether block ``a`` dominates block ``b`` (names)."""
        while True:
            if a == b:
                return True
            if self.idom.get(b, b) == b:
                return False
            b = self.idom[b]

    def walk(self):
        """Every operation, in order."""
        for blk in self.blocks:
            yield from blk.ops

    def width(self, v):
        """The bit width of value ``v``."""
        return WIDTH[self.types[v]]

    def iv(self, v):  # pylint: disable=too-many-return-statements,too-many-branches
        """The interval ``(lo, hi)`` of value ``v`` modulo its width."""
        if v in self.ivs:
            return self.ivs[v]
        w = self.width(v)
        top = (1 << w) - 1
        o = self.defs.get(v)
        full = (0, top)
        if o is None:
            r = self.iv_joined(v)
        elif o.name == "memref.load" and (k := self.named(o)) is not None and self.exact(o):
            r = (k, k)
        elif o.name == "memref.load" and w == 8:
            a = self.address(o)
            pts = self.points(a)
            r = self.mod.fx(self.name).sets.get(self.const_of(a)) or (
                self.mod.mem.hull(*self.iv(a)) if pts is None else self.mod.mem.hull_at(pts)
            )
            r = meet(r, self.mod.loaded_iv(self, o) or r)
        elif o.name == "func.call":
            r = self.mod.result_iv(o.prop("callee")[1:], o.res.index(v)) or full
        elif o.name == "memref.load":
            r = full
        elif o.name == "arith.constant":
            c = o.int_prop("value") & top
            r = (c, c)
        elif o.name == "ub.poison":
            r = (0, 0)
        elif o.name == "arith.kfam":
            lo, hi = self.iv(o.args[0])
            ks = self.kvals[(self.kvals >= lo) & (self.kvals <= hi)]
            vals = self.ktabs[v][ks]
            r = (int(vals.min()), int(vals.max())) if len(vals) else full
        elif o.name in CASTS:
            r = self.iv(o.args[0])
            r = r if r[1] <= top else full
        elif o.name == "arith.select" or o.name in MINMAX:
            a, b = self.iv(o.args[-2]), self.iv(o.args[-1])
            r = (min(a[0], b[0]), max(a[1], b[1]))
            if o.name in ("arith.maxui", "arith.minui"):
                pick = max if o.name == "arith.maxui" else min
                r = (pick(a[0], b[0]), pick(a[1], b[1]))
        elif o.name == "arith.extsi":
            r, w0 = self.iv(o.args[0]), self.width(o.args[0])
            if r[0] >> (w0 - 1) == r[1] >> (w0 - 1):
                r = (twos(r[0], w0) & top, twos(r[1], w0) & top)
            else:
                r = full
        elif o.name == "arith.shrsi" and self.iv(o.args[0])[1] < (top + 1) // 2:
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            r = (a[0] >> b[1], a[1] >> b[0])
        elif o.name in ("arith.addi", "arith.subi"):
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            lo, hi = (
                (a[0] + b[0], a[1] + b[1]) if o.name == "arith.addi" else (a[0] - b[1], a[1] - b[0])
            )
            if hi - lo > top:
                r = full
            else:
                lo2, hi2 = lo & top, hi & top
                r = (lo2, hi2) if lo2 <= hi2 else full
        elif o.name == "arith.andi":
            a, b = sorted((self.iv(o.args[0]), self.iv(o.args[1])), key=lambda x: x[0] != x[1])
            m = a[0]
            if a[0] == a[1] and not m & (m + 1) and b[0] & ~m == b[1] & ~m:
                r = (b[0] & m, b[1] & m)
            elif a[0] == a[1]:
                p = (b[0] ^ b[1]).bit_length()
                kept = (b[0] >> p << p) & m
                r = meet((kept, kept | m & ((1 << p) - 1)), (0, min(a[1], b[1])))
            else:
                r = (0, min(a[1], b[1]))
        elif o.name == "arith.muli":
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            r = (a[0] * b[0], a[1] * b[1]) if a[1] * b[1] <= top else full
        elif o.name == "arith.divui":
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            r = (a[0] // b[1], a[1] // b[0]) if b[0] else full
        elif o.name == "arith.remui":
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            r = (0, min(a[1], b[1] - 1)) if b[0] else full
        elif o.name in ("arith.ori", "arith.xori"):
            a, b = sorted((self.iv(o.args[0]), self.iv(o.args[1])), key=lambda x: x[0] != x[1])
            c = a[0]
            if a[0] == a[1] and b[0] == b[1]:
                r = ((c | b[0]) if o.name == "arith.ori" else (c ^ b[0]),) * 2
            elif a[0] == a[1] and c and b[1] < c & -c:
                r = (c + b[0], c + b[1])
            else:
                hi = min((1 << max(a[1], b[1]).bit_length()) - 1, a[1] + b[1])
                r = (max(a[0], b[0]) if o.name == "arith.ori" else 0, hi)
        elif o.name in ("arith.shli", "arith.shrui"):
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            if b[0] != b[1]:
                r = full
            elif o.name == "arith.shrui":
                r = (a[0] >> b[0], a[1] >> b[0])
            else:
                r = (a[0] << b[0], a[1] << b[0]) if a[1] << b[0] <= top else full
        else:
            r = full
        self.ivs[v] = r
        return r

    def points(self, v):
        """The values ``v`` takes, ascending, when they are at most :data:`POINTS` that its
        constants decide (a constant, a select or a cast of such, an operation of two such),
        else None: what an access at such an address reads is the bytes at them, not their
        hull."""
        d, top = self.defs.get(v), (1 << self.width(v)) - 1
        if d is None:
            return None
        if d.name == "arith.constant":
            return np.array([d.int_prop("value") & top])
        if d.name in CASTS:
            x = self.points(d.args[0])
            return None if x is None else np.unique(x & top)
        if d.name == "arith.select" or d.name in NUMPY:
            xs = [self.points(a) for a in d.args[-2:]]
            if any(x is None for x in xs):
                return None
            vals = (
                np.union1d(*xs)
                if d.name == "arith.select"
                else np.unique(NUMPY[d.name](xs[0][:, None], xs[1][None, :]) & top)
            )
            return vals if len(vals) <= POINTS else None
        return None

    def iv_joined(self, v):
        """The interval of a block argument: the hull of what flows into it, where a value
        carried around a loop that stays within its entering interval keeps it."""
        srcs = self.arg_src.get(v, [])
        full = (0, (1 << self.width(v)) - 1)
        if not srcs:
            return self.param_iv(v, full)
        self.ivs[v] = full
        home = self.arg_block[v].name
        back = [s for s in srcs if self.dominates(home, s[0].parent.name)]
        fwd = [s for s in srcs if s not in back]
        ivs = [self.iv(o.args[i]) for o, i, _ in fwd]
        first = (min(lo for lo, _ in ivs), max(hi for _, hi in ivs)) if ivs else full
        if not back:
            return first
        exact = self.loop_iv(v, back, first)
        if exact is not None:
            return exact
        self.ivs[v] = first
        before = set(self.ivs)
        for o, i, _ in back:
            lo, hi = self.iv(o.args[i])
            if lo < first[0] or hi > first[1]:
                for k in set(self.ivs) - before:
                    del self.ivs[k]
                return full
        return first

    def param_iv(self, v, full):
        """The hull of the arguments a lifted function's call sites pass for parameter
        ``v`` (``full`` for an entry's, or while recursion reaches it)."""
        names = [a for a, _ in self.blocks[0].args]
        sites = self.mod.sites.get(self.name) if self.private and v in names else None
        if not sites:
            return full
        self.ivs[v] = lift = self.mod.entry_iv(self.name, names.index(v)) or full
        ivs = [f.iv0(o.args[names.index(v)]) for f, o in sites]
        return meet((min(lo for lo, _ in ivs), max(hi for _, hi in ivs)), lift)

    def loop_iv(self, v, back, init):
        """The hull of the values block argument ``v`` takes from ``init`` around the loop
        edges ``back`` (:meth:`closure`)."""
        seen = np.zeros(1 << self.width(v), dtype=bool)
        seen[init[0] : init[1] + 1] = True
        vals = self.closure(v, back, seen)
        return None if vals is None else (int(vals[0]), int(vals[-1]))

    def closure(self, v, back, seen):
        """The values block argument ``v`` takes from those ``seen`` (a mask) around the loop
        edges ``back``, when their conditions and values depend on ``v`` alone: the set of
        values is iterated to its fixpoint, every member evaluated at once; else None."""
        new = np.flatnonzero(seen)
        try:
            for _ in range(LOOP_STEPS):
                memo, outs = {}, []
                for o, i, case in back:
                    nxt = np.broadcast_to(self.evaluate(o.args[i], {v: new}, memo), new.shape)
                    if o.name == "cf.cond_br":
                        go = np.broadcast_to(self.evaluate(o.args[0], {v: new}, memo), new.shape)
                        nxt = nxt[(go != 0) == case]
                    outs.append(nxt)
                vals = np.unique(np.concatenate(outs))
                new = vals[~seen[vals]]
                if not new.size:
                    return np.flatnonzero(seen)
                seen[new] = True
        except Unknown:
            return None
        return None

    def stepped(self, v):
        """``(values, step, steps)`` when byte block argument ``v`` takes the ``values`` of an
        arithmetic progression of ``step``, each edge into its block (no ``switch``) passing a
        constant (``steps[(op id, case)] = ("c", element)``) or ``v`` plus a multiple ``j`` of
        the step (``("d", j)``) that cannot wrap, so the element number steps by ``j``."""
        if v not in self.steps:
            self.steps[v] = self.stepped_of(v)
        return self.steps[v]

    def stepped_of(self, v):
        """:meth:`stepped`, made."""
        srcs = self.arg_src.get(v)
        if not srcs or self.types.get(v) != "i8" or any(o.name == "cf.switch" for o, _, _ in srcs):
            return None
        home = self.arg_block[v].name
        back = [x for x in srcs if self.dominates(home, x[0].parent.name)]
        loops = {(id(o), c) for o, _, c in back}
        init = [self.const_of(o.args[i]) for o, i, c in srcs if (id(o), c) not in loops]
        if None in init or not init:
            return None
        seen = np.zeros(0x100, dtype=bool)
        seen[np.array(init) & 0xFF] = True
        vals = self.closure(v, back, seen) if back else np.flatnonzero(seen)
        if vals is None or len(vals) < 2 or len(set(np.diff(vals).tolist())) != 1:
            return None
        step, span, steps = int(vals[1] - vals[0]), int(vals[-1] - vals[0]), {}
        for o, i, case in srcs:
            u, d = o.args[i], 0
            if (id(o), case) not in loops:
                steps[id(o), case] = ("c", ((self.const_of(u) & 0xFF) - int(vals[0])) // step)
                continue
            if u != v:
                du = self.defs.get(u)
                ok = du is not None and du.name in ("arith.addi", "arith.subi")
                k = self.const_of(du.args[1]) if ok and du.args[0] == v else None
                if k is None:
                    return None
                k = k if du.name == "arith.addi" else -k
                d = (k & 0xFF) - (0x100 if k & 0x80 else 0)
            if d % step or span >= 0x100 - abs(d):
                return None
            steps[id(o), case] = ("d", d // step)
        return vals, step, steps

    def element(self, v):
        """The block argument added beside ``v`` (:meth:`stepped`) holding its element number:
        ``(v - values[0]) / step``, the same on every edge."""
        if v in self.elements:
            return self.elements[v]
        _, _, steps = self.stepped(v)
        blk = self.arg_block[v]
        e = f"%e{len(self.elements)}"
        self.elements[v] = e
        new, made = {}, []
        for o, _, case in list(self.arg_src[v]):
            kind, j = steps[id(o), case]
            if kind == "c" or j:
                c = self.kop(
                    new,
                    "arith.constant",
                    [],
                    "i8",
                    None,
                    f"value = {j & 0xFF} : i8",
                    prefix=f"%ec{len(self.defs)}.",
                )
            if kind == "d" and j:
                res = f"%ed{len(self.elements)}.{len(made)}"
                made.append(Op("arith.addi", [res], [e, c], "", [], (("i8", "i8"), ("i8",))))
                c = res
            elif kind == "d":
                c = e
            self.pass_arg(o, case, c)
        self.prepend(new)
        self.insert_first(blk, made)
        blk.args.append((e, "i8"))
        self.types[e], self.arg_block[e] = "i8", blk
        self.types.update((x.res[0], "i8") for x in made)
        self.arg_src[e] = [
            (o, idx[-1], case)
            for o in {id(o): o for o, _, _ in self.arg_src[v]}.values()
            for succ, idx, case in o.edges()
            if succ == blk.name
        ]
        return e

    def plus(self, e, k):
        """``e + k`` (bytes) for an element block argument ``e``, at the start of its block."""
        if not k:
            return e
        blk, new = self.arg_block[e], {}
        c = self.kop(
            new,
            "arith.constant",
            [],
            "i8",
            None,
            f"value = {k & 0xFF} : i8",
            prefix=f"%ec{len(self.defs)}.",
        )
        self.prepend(new)
        res = f"%ep{len(self.defs)}"
        self.insert_first(blk, [Op("arith.addi", [res], [e, c], "", [], (("i8", "i8"), ("i8",)))])
        self.types[res] = "i8"
        return res

    def pass_arg(self, o, case, val):
        """Append ``val`` to the operands terminator ``o`` passes on its edge ``case``,
        shifting the operand indices recorded past it."""
        if o.name == "cf.br":
            at = len(o.args)
        else:
            _, n, m = o.ints("operandSegmentSizes")
            at = 1 + n if case else len(o.args)
            n, m = (n + 1, m) if case else (n, m + 1)
            o.props = re.sub(
                r"operandSegmentSizes = array<i32: [^>]*>",
                f"operandSegmentSizes = array<i32: 1, {n}, {m}>",
                o.props,
            )
        o.args.insert(at, val)
        o.types = (tuple(o.types[0][:at]) + ("i8",) + tuple(o.types[0][at:]), o.types[1])
        self.dead = {(x, i + (x == id(o) and i >= at)) for x, i in self.dead}
        for srcs in self.arg_src.values():
            srcs[:] = [(x, i + (x is o and i >= at), c) for x, i, c in srcs]
        self.uses[val] = self.uses.get(val, 0) + 1
        self.users.setdefault(val, []).append(o)

    def byte_index(self, o):
        """``(c, w)`` of access ``o`` at a constant ``c`` plus the zero-extended byte ``w``,
        else None."""
        c, rest = self.split(self.address(o))
        d = self.defs.get(rest)
        if rest is None or d is None or d.name != "arith.extui" or self.width(d.args[0]) != 8:
            return None
        return c, d.args[0]

    def stepped_access(self, o):
        """``(addresses, v, offset)`` of access ``o`` at a constant plus a byte ``v + offset``,
        ``v`` a block argument taking an arithmetic progression (:meth:`stepped`): its
        addresses for each of ``v``'s values, else None."""
        if (cw := self.byte_index(o)) is None:
            return None
        (c, w), off = cw, 0
        dw = self.defs.get(w)
        if dw is not None and dw.name == "arith.addi" and self.const_of(dw.args[1]) is not None:
            w, off = dw.args[0], self.const_of(dw.args[1])
        if self.defs.get(w) is not None or w not in self.arg_src or self.stepped(w) is None:
            return None
        vals = self.stepped(w)[0]
        top = (1 << self.width(self.address(o))) - 1
        return ((c + ((vals + off) & 0xFF)) & top).tolist(), w, off

    def strided_access(self, o):
        """``(addresses, w, base, k)`` of load or store ``o`` at a constant ``c`` plus a byte
        ``w`` whose address set from the lift is ``n >= 2`` addresses at a stride ``k >= 2``
        from ``c + base``, none wrapping past ``c + 255``: the records of an object ``w``
        indexes, element ``(w - base) / k``; else None."""
        if o.name not in ("memref.load", "memref.store") or o.pc in self.mod.copy_pcs.get(
            self.name, {}
        ):
            return None
        runs = self.mod.fx(self.name).addr_sets.get((o.pc, o.name == "memref.store"))
        addrs = sorted({a for lo, hi in runs or () for a in range(lo, hi)})
        steps = set(np.diff(addrs).tolist())
        if len(addrs) < 2 or len(steps) != 1 or min(steps) < 2:
            return None
        if (cw := self.byte_index(o)) is None:
            return None
        c, w = cw
        base, top = addrs[0] - c, (1 << self.width(self.address(o))) - 1
        if not 0 <= base <= addrs[-1] - c <= 0xFF or addrs[-1] > top:
            return None
        return addrs, w, base, min(steps)

    def divided(self, o, w, base, k, first):
        """The element number ``first + (w - base) / k`` (:meth:`strided_access`), computed
        just before access ``o``."""
        new = {}
        consts = [
            self.kop(new, "arith.constant", [], "i8", None, f"value = {x} : i8", prefix="%dc")
            for x in (base, k, first)
        ]
        self.prepend(new)
        blk, at = self.where[id(o)]
        made, x = [], w
        for name, c, used in (
            ("arith.subi", consts[0], base),
            ("arith.divui", consts[1], k),
            ("arith.addi", consts[2], first),
        ):
            if name == "arith.divui" or used:
                res = f"%dv{len(self.defs)}.{len(made)}"
                y = Op(name, [res], [x, c], "", [], (("i8", "i8"), ("i8",)), parent=blk, pc=o.pc)
                made.append(y)
                self.defs[res], self.types[res] = y, "i8"
                for a in (x, c):
                    self.uses[a] = self.uses.get(a, 0) + 1
                    self.users.setdefault(a, []).append(y)
                x = res
        blk.ops[at:at] = made
        for j, y in enumerate(blk.ops):
            self.where[id(y)] = (blk, j)
        return x

    def evaluate(self, v, env, memo, known=False):  # pylint: disable=too-many-branches
        """The values of ``v`` (an array or scalar) for the block arguments ``env``; with
        ``known`` a byte load reads the bytes holding one value whenever this function runs
        (:meth:`Module.known`)."""
        if v in env:
            return env[v]
        if v in memo:
            return memo[v]
        d = self.defs.get(v)
        if known and d is not None and d.name == "memref.load" and d.args[-2] in self.globals:
            a = np.asarray(self.evaluate(self.address(d), env, memo, known), dtype=np.int64)
            got = [self.mod.known(self.name, int(x) & 0xFFFF) for x in a.ravel()]
            if None in got:
                raise Unknown(v)
            memo[v] = r = np.array(got, dtype=np.int64).reshape(a.shape)
            return r
        if d is None or not d.name.startswith(("arith.", "ub.")) or len(d.res) != 1:
            raise Unknown(v)
        top = (1 << self.width(v)) - 1
        name = d.name
        if name == "arith.constant":
            r = np.int64(d.int_prop("value") & top)
        elif name == "ub.poison":
            r = np.int64(0)
        elif name == "arith.select":
            c, a, b = (self.evaluate(x, env, memo, known) for x in d.args)
            r = np.where(c != 0, a, b)
        else:
            args = [np.asarray(self.evaluate(x, env, memo, known), dtype=np.int64) for x in d.args]
            if name == "arith.kfam":
                r = self.ktabs[v][args[0]]
            elif name in CASTS:
                r = args[0] & top
            elif name == "arith.extsi":
                r = twos(args[0], self.width(d.args[0])) & top
            elif name in MINMAX or name in SIGNED:
                a, b, w = *args, self.width(d.args[0])
                if name in MINMAX:
                    a, b = (twos(a, w), twos(b, w)) if MINMAX[name] in (2, 4) else (a, b)
                    r = np.where(NUMPY_CMP[MINMAX[name]](a, b), a, b) & top
                elif name == "arith.shrsi":
                    r = np.where(b < w, twos(a, w) >> np.minimum(b, 63), -(a >> (w - 1))) & top
                else:
                    a, b = twos(a, w), twos(b, w)
                    q = np.trunc(a / np.where(b == 0, 1, b)).astype(np.int64)
                    r = (q if name == "arith.divsi" else a - q * b) & top
            elif name in NUMPY:
                r = NUMPY[name](args[0], args[1]) & top
            elif name in ("arith.shli", "arith.shrui"):
                a, b = args
                sh = np.minimum(b, 63)
                r = np.where(b < self.width(v), (a << sh if name == "arith.shli" else a >> sh), 0)
                r = r & top
            elif name == "arith.cmpi":
                pred, w = d.int_prop("predicate"), self.width(d.args[0])
                a, b = args
                if pred in (2, 3, 4, 5):
                    half = 1 << (w - 1)
                    a, b = (a ^ half) - half, (b ^ half) - half
                r = NUMPY_CMP[pred](a, b).astype(np.int64)
            else:
                raise Unknown(v)
        memo[v] = r
        return r

    def free_args(self, v, seen=None):
        """The block arguments value ``v`` is computed from, through operations and the
        addresses of the loads among them."""
        seen = set() if seen is None else seen
        if v in seen:
            return set()
        seen.add(v)
        d = self.defs.get(v)
        if d is None:
            return {v}
        args = [self.address(d)] if d.name == "memref.load" else d.args
        return set().union(*(self.free_args(a, seen) for a in args)) if args else set()

    def fold_known_addresses(self):
        """Rewrite the 16-bit address of each memory access computed from one byte index (a
        block argument of an arithmetic progression, or the instance index) through loads of
        bytes holding one value whenever this function runs (a pointer pair init leaves,
        ``Module.known``) as the constant plus the index it equals for every value of the
        index: the access indexes what the pointers address, and the pointers are not read."""
        changed = False
        for blk in self.blocks:
            for o in list(blk.ops):
                if o.name not in ("memref.load", "memref.store") or o.args[-2] not in self.globals:
                    continue
                a = self.address(o)
                if self.width(a) != 16 or self.split(a)[1] is None:
                    continue
                free = self.free_args(a)
                v = free.pop() if len(free) == 1 else None
                if v is None or self.types.get(v) != "i8":
                    continue
                vals = self.index_values(v, a)
                if vals is None:
                    continue
                try:
                    self.evaluate(a, {v: vals}, {})
                    continue
                except Unknown:
                    pass
                try:
                    got = self.evaluate(a, {v: vals}, {}, True)
                except Unknown:
                    continue
                c = set(((np.broadcast_to(got, vals.shape) - vals) & 0xFFFF).tolist())
                if len(c) != 1:
                    continue
                self.rebase(blk, blk.ops.index(o), o, c.pop(), v)
                changed = True
        if changed:
            self.reindex()

    def index_values(self, v, a):
        """The values byte ``v`` takes where address ``a`` is computed from it: the instance
        index's, a block argument's arithmetic progression, or the values the lift finds ``X``
        holding at a ``(zp,X)`` instruction one of whose pointer loads ``a`` reads at ``v``
        plus its operand (every copy's); else None."""
        if v == self.kparam:
            return self.kvals
        step = self.stepped(v)
        if step is not None:
            return step[0]
        mod, regs = self.mod, self.mod.fx(self.name).regs
        for d in self.loads_in(a):
            b = self.address(d)
            c, rest = self.split(b)
            pcs = mod.copy_pcs.get(self.name, {}).get(d.pc, [d.pc])
            if rest != v or None in pcs or self.width(b) != 8:
                continue
            ok = all(
                mod.fixed[q]
                and OPS.get(mod.mem.image[q], ("", ""))[1] == "indx"
                and mod.mem.image[(q + 1) & 0xFFFF] == c & 0xFF
                and q in regs
                for q in pcs
            )
            if ok:
                vals = set().union(*(regs[q][1] for q in pcs))
                return np.array(sorted(vals), dtype=np.int64) if vals else None
        return None

    def loads_in(self, v, seen=None):
        """The loads value ``v`` is computed from."""
        seen = set() if seen is None else seen
        d = self.defs.get(v)
        if v in seen or d is None:
            return []
        seen.add(v)
        args = [self.address(d)] if d.name == "memref.load" else d.args
        out = [d] if d.name == "memref.load" else []
        return out + [x for a in args for x in self.loads_in(a, seen)]

    def rebase(self, blk, i, o, c, v):
        """Make access ``o`` (at ``i`` in ``blk``) address constant ``c`` plus byte ``v``."""
        n = f"%rb{len(self.defs)}.{i}"
        ops = [
            Op("arith.constant", [n + "c"], [], f"value = {c} : i16", [], ((), ("i16",))),
            Op("arith.extui", [n + "e"], [v], "", [], (("i8",), ("i16",))),
            Op("arith.addi", [n + "a"], [n + "c", n + "e"], "", [], (("i16", "i16"), ("i16",))),
            Op("arith.index_castui", [n + "i"], [n + "a"], "", [], (("i16",), ("index",))),
        ]
        for x in ops:
            x.parent, x.pc, x.loc, x.where = blk, o.pc, o.loc, o.where
            self.defs[x.res[0]] = x
        blk.ops[i:i] = ops
        o.args[-1] = n + "i"

    def address(self, o):
        """The 16-bit address value of a memory access or I/O call ``o``."""
        if o.name == "func.call":
            return o.args[0]
        idx = o.args[-1]
        d = self.defs.get(idx)
        return d.args[0] if d is not None and d.name == "arith.index_castui" else idx

    def proven_iv(self, o):
        """The interval of load or store ``o``'s address met with the hull the lift proves of
        its instruction (every copy's: the bounds of the bytes its index comes from, or the
        bytes the memory map checks it into), or None."""
        lo, hi = self.addr_iv(o)
        direct = o.name in ("memref.load", "memref.store")
        h = self.mod.reach_of(self, o, "proven") if direct else None
        if h is None or hi - lo >= regions.BOUNDED or h[0] > hi or lo > h[1]:
            return None
        return max(lo, h[0]), min(hi, h[1])

    def place(self, o):
        """``("region", k)`` or ``("held", j)``: the one region (or, for a load or store, held
        span) access ``o`` lies in, over all its addresses or, in an instance, for each copy's;
        else None."""
        mem, a = self.mod.mem, self.address(o)
        lo, hi = self.addr_iv(o)
        direct = o.name in ("memref.load", "memref.store")
        store = o.name != "memref.load"
        k = mem.region(lo, hi)
        if k is not None:
            return ("region", k)
        j = mem.held_span(lo, hi, store)
        if j is not None and direct:
            return ("held", j)
        per = self.kivs(a)
        if per:
            ks = {mem.region(x, y) for x, y in per}
            js = {mem.held_span(x, y, store) for x, y in per}
            if len(ks) == 1 and None not in ks:
                return ("region", ks.pop())
            if len(js) == 1 and None not in js and direct:
                return ("held", js.pop())
        return None

    def named(self, o):
        """The value load ``o`` reads when it is of one byte holding one value whenever this
        function runs (:meth:`Module.known`) that the ``.h`` names (:meth:`Module.konst`),
        else None: no access of memory."""
        if o.name == "memref.load" and o.args[0] == self.symbols:
            return self.mod.symbols[self.const_of(o.args[1])][1]
        if o.name != "memref.load" or o.args[-2] not in self.globals:
            return None
        if id(o) not in self.knowns:
            lo, hi = self.iv0(self.address(o))
            self.knowns[id(o)] = self.mod.known(self.name, lo) if lo == hi else None
        return self.knowns[id(o)]

    def exact(self, o):
        """Whether named load ``o`` (:meth:`named`) stands for a constant of the code: a
        symbol, or an instruction's operand byte the lift reads as data init leaves constant
        (``Module.folded``), as the code would hold it."""
        return o.args[0] == self.symbols or self.mod.folded[2][self.iv0(self.address(o))[0]]

    def accesses(self):
        """``[(op, (lo, hi), per)]`` of the memory accesses and I/O calls: the interval of
        the address and, in an instance, ``per`` index (else None); a load the ``.h`` names
        (:meth:`named`) is none."""
        out = []
        for o in self.walk():
            if o.name in ("memref.load", "memref.store") and o.args[-2] in self.globals:
                if self.named(o) is not None:
                    continue
                a = self.address(o)
            elif o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write"):
                a = o.args[0]
            else:
                continue
            out.append((o, self.addr_iv(o), self.kivs(a)))
        return out

    def root(self, o):
        """Whether operation ``o`` is observable by itself: a store some load may read back
        (:meth:`Module.unread_stores`), an I/O write, a trap, or a read that may meet a
        read-sensitive address."""
        if o.name == "memref.store":
            return id(o) not in self.unread
        callee = o.prop("callee") if o.name == "func.call" else None
        if callee in ("@io_write", "@trap"):
            return True
        return callee == "@io_read" and self.mod.mem.sensitive_in(*self.iv(o.args[0]))

    def kept(self, o, live, funcs):
        """The indices of the operands of ``o`` that something reads, given the values
        ``live`` and the functions ``funcs`` by name, or None when nothing needs ``o``."""
        callee = o.prop("callee")[1:] if o.name == "func.call" else None
        if callee in self.mod.inert and not any(r in live for r in o.res) or id(o) in self.unread:
            return None
        if callee in funcs:
            return set(funcs[callee].live_args)
        if o.name == "func.return":
            return set(self.live_res)
        if o.name.startswith("cf."):
            return {
                i
                for s, idx, _ in o.edges()
                for (a, _), i in zip(self.block_of[s].args, idx)
                if a in live
            } | ({0} if o.name != "cf.br" else set())
        if not o.res or any(r in live for r in o.res) or self.root(o):
            return set(range(len(o.args)))
        return None

    def live_users(self, v, live, funcs):
        """The operations that read value ``v`` where something needs them (:meth:`kept`)."""
        out = []
        for u in self.users.get(v, ()):
            keep = self.kept(u, live, funcs)
            if keep is not None and any(a == v and i in keep for i, a in enumerate(u.args)):
                out.append(u)
        return out

    def settle(self, live, funcs):
        """Drop what ``live`` does not hold: pure operations and reads, the arguments of
        unused parameters, unused returned values and the edges into unused block
        arguments; count the uses left."""
        self.uses = {}
        for o in self.walk():
            keep = self.kept(o, live, funcs)
            if keep is None:
                self.dropped.add(id(o))
                continue
            for i, a in enumerate(o.args):
                if i in keep:
                    self.uses[a] = self.uses.get(a, 0) + 1
                else:
                    self.dead.add((id(o), i))

    def inert(self):
        """Whether this function has no effect: no loop, and nothing kept but pure operations,
        branches, returns and calls of inert functions."""
        if any(self.dominates(s, b.name) for b in self.blocks for s in b.ops[-1].succs):
            return False
        return all(
            id(o) in self.dropped
            or self.pure(o)
            or o.name in TERMINATORS
            or o.name == "memref.get_global"
            for o in self.walk()
        )

    def pure(self, o):
        """Whether ``o`` computes a value with no effect."""
        if o.name in ("memref.load",) or o.name.startswith("arith.") or o.name == "ub.poison":
            return True
        return o.name == "func.call" and o.prop("callee") in CHECKS

    def effect(self, o):
        """Whether ``o`` may write memory or call out."""
        if id(o) in self.dropped:
            return False
        if o.name == "func.call":
            return o.prop("callee") not in CHECKS
        return o.name == "memref.store"

    def mark_addresses(self):
        """Mark the operand edges ``(op, index)`` through which a value reaches the address
        of a memory access or a computed jump's target, so constants on them print relative
        to their region (a jump's or-ed in high byte too)."""
        work, jumps = [], []
        for o in self.walk():
            if o.name in ("memref.load", "memref.store") and o.args[-2] in self.globals:
                work.append((o, len(o.args) - 1))
            elif o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write"):
                work.append((o, 0))
            elif o.name == "cf.switch":
                jumps.append((o, 0))
        for work, ors in ((work, False), (jumps, True)):
            while work:
                o, i = work.pop()
                if (id(o), i) in self.addr_edges:
                    continue
                self.addr_edges.add((id(o), i))
                work += self.sources(o.args[i], ors)

    def sources(self, v, ors=False):
        """The operand edges a value ``v`` is computed from, as far as addresses flow (with
        ``ors`` through ``|`` too)."""
        d = self.defs.get(v)
        if d is None:
            return [(o, i) for o, i, _ in self.arg_src.get(v, [])]
        if (
            d.name in CASTS
            or d.name in ("arith.addi", "arith.subi")
            or ors
            and d.name == "arith.ori"
        ):
            return [(d, i) for i in range(len(d.args))]
        if d.name == "arith.select":
            return [(d, 1), (d, 2)]
        return []

    def adjacent(self, d, u):
        """Whether use ``u`` of ``d``'s value follows ``d`` with no effect between: in its block,
        or past blocks that only check for a ``trap`` (:meth:`between`)."""
        ops = self.between(d, u)
        return ops is not None and not any(self.effect(x) for x in ops)

    def const_of(self, v):
        """The value of constant ``v``, or None."""
        d = self.defs.get(v)
        return d.int_prop("value") if d is not None and d.name == "arith.constant" else None

    def remat(self, v):
        """Whether ``v`` is a constant every function can print itself."""
        d = self.defs.get(v)
        return d is not None and d.name in REMAT

    def returns_only(self, b):
        """Whether block ``b`` does nothing but a ``func.return``: its other operations are
        dropped."""
        return b.ops[-1].name == "func.return" and all(id(o) in self.dropped for o in b.ops[:-1])

    def ret_vals(self, ret, sub):
        """The live values ``[(value, edge)]`` returned by ``ret``, its block's arguments
        replaced through ``sub``."""
        return [sub.get(ret.args[j], (ret.args[j], (ret, j))) for j in self.live_res]

    def lit_key(self, c):
        """``(key, polarity)`` of branch condition ``c``: an atom ``x == k`` of a comparison
        with a constant (``x`` with its zero extensions stripped; one of an ``i1`` is that
        value), else the value."""
        d = self.defs.get(c)
        if d is not None and d.name == "arith.xori" and self.const_of(d.args[1]) in (1, -1):
            key, pol = self.lit_key(d.args[0])
            return key, not pol
        if d is not None and d.name == "arith.cmpi" and d.int_prop("predicate") in (0, 1):
            x, k = d.args
            if self.const_of(k) is not None and self.const_of(x) is None:
                k = self.const_of(k) & (1 << self.width(x)) - 1
                while (dx := self.defs.get(x)) is not None:
                    c = self.const_of(dx.args[1]) if dx.name in OFFSETS else None
                    if dx.name in ("arith.extui", "arith.index_castui"):
                        if k >> self.width(dx.args[0]):
                            break
                    elif c is None:
                        break
                    else:
                        k = OFFSETS[dx.name](k, c) & (1 << self.width(x)) - 1
                    x = dx.args[0]
                eq = d.int_prop("predicate") == 0
                if self.width(x) == 1:
                    key, pol = self.lit_key(x)
                    return key, pol == (eq == (k == 1))
                return ("e", x, k), eq
        return ("c", c), True

    def graph(self, ids):  # pylint: disable=too-many-locals,too-many-branches
        """The blocks as a :class:`structure.Graph`: a branch's edges carrying block
        arguments get a node of copies, an edge to a block that only returns a return."""
        g = Graph(self.conds, ids)
        node = {}
        for b in self.blocks:
            if b is self.blocks[0] or not self.returns_only(b):
                node[b.name] = g.add([("ops", b)])
        g.entry = node[self.blocks[0].name]
        for b in self.blocks:
            if b.name not in node:
                continue
            n, term = node[b.name], b.ops[-1]
            if self.traps(b):
                g.items[n].append(("halt",))
                continue
            if term.name == "func.return":
                g.items[n].append(("return", self.ret_vals(term, {})))
                continue
            for (succ, idx, _), key in zip(term.edges(), self.edge_keys(term)):
                tb = self.block_of[succ]
                if tb.name not in node:
                    sub = {a: (term.args[i], (term, i)) for (a, _), i in zip(tb.args, idx)}
                    stmt = ("return", self.ret_vals(tb.ops[-1], sub))
                    if term.name == "cf.br":
                        g.items[n].append(stmt)
                    else:
                        g.succ[n].append([g.add([stmt]), key])
                    continue
                pairs = [
                    (a, term.args[i], (term, i))
                    for (a, _), i in zip(tb.args, idx)
                    if self.uses.get(a)
                ]
                if pairs and term.name == "cf.br":
                    g.items[n].append(("copy", pairs))
                    g.succ[n].append([node[tb.name], None])
                elif pairs:
                    g.succ[n].append([g.add([("copy", pairs)], [[node[tb.name], None]]), key])
                else:
                    g.succ[n].append([node[tb.name], key])
        pre = g.dfs()[0]
        g.remove(set(g.items) - set(pre))
        for n in pre:
            merged = {}
            for t, key in g.succ[n]:
                c = self.bdd_of(key)
                merged[t] = c if t not in merged else (None if c is None else merged[t] | c)
            g.succ[n] = [[t, c] for t, c in merged.items()]
        return g

    def edge_keys(self, term):
        """The symbolic conditions of a terminator's edges."""
        if term.name == "cf.br":
            return [None]
        if term.name == "cf.cond_br":
            key, pol = self.lit_key(term.args[0])
            return [("lit", key, pol), ("lit", key, not pol)]
        x = term.args[0]
        mask = (1 << self.width(x)) - 1
        cases = [("e", x, v & mask) for _, _, v in term.edges()[1:]]
        return [("none", cases)] + [("lit", k, True) for k in cases]

    def bdd_of(self, key):
        """The BDD of a symbolic edge condition."""
        if key is None:
            return None
        if key[0] == "lit":
            v = self.conds.var(key[1])
            return v if key[2] else ~v
        out = self.conds.bdd.true
        for k in key[1]:
            out &= ~self.conds.var(k)
        return out

    @staticmethod
    def lit_value(key):
        """The value literal ``key`` reads: the condition, or an atom's selector (which
        dominates every comparison of it); None for a structuring variable."""
        return key[1] if isinstance(key[1], str) else None

    def quiet(self, items):
        """Whether statements ``items`` are a block that prints nothing: its operations are
        pure and only its own operations and branch condition read them."""
        if len(items) != 1 or items[0][0] != "ops":
            return False
        blk = items[0][1]
        term = blk.ops[-1]
        passed = {
            a
            for i, a in enumerate(term.args)
            if (i or term.name == "cf.br") and (id(term), i) not in self.dead
        }
        for o in blk.ops[:-1]:
            if not (self.pure(o) or id(o) in self.dropped) and o.name != "memref.get_global":
                return False
            for r in o.res:
                if r in passed or any(u.parent is not blk for u in self.users.get(r, ())):
                    return False
        return True

    def hoistable(self, items):
        """Whether statements ``items`` are a block whose operations have no effect and read
        no memory but data regions and code, so they may run before the branch leading to
        them."""
        if len(items) != 1 or items[0][0] != "ops":
            return False
        mem = self.mod.mem
        for o in items[0][1].ops[:-1]:
            if id(o) in self.dropped or o.name == "memref.get_global":
                continue
            if o.name == "func.call" or not self.pure(o):
                return False
            if o.name == "memref.load" and id(o) not in self.promoted and self.named(o) is None:
                lo, hi = self.iv(self.address(o))
                if mem.region(lo, hi) is None and not mem.is_code(lo, hi):
                    return False
        return True

    def latchable(self, items):
        """Whether statements ``items`` are one block with no effect."""
        if len(items) != 1 or items[0][0] != "ops":
            return False
        return not any(
            self.effect(o) or o.name == "func.call"
            for o in items[0][1].ops[:-1]
            if id(o) not in self.dropped
        )

    def exprable(self, items):
        """Whether statements ``items`` are one block whose effects are stores and I/O
        accesses and whose values only it reads: C expressions with no declaration."""
        if len(items) != 1 or items[0][0] != "ops":
            return False
        blk = items[0][1]
        for o in blk.ops[:-1]:
            if id(o) in self.dropped or o.name == "memref.get_global":
                continue
            io = o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write")
            if not (self.pure(o) or o.name == "memref.store" or io):
                return False
            if any(u.parent is not blk for r in o.res for u in self.users.get(r, ())):
                return False
        return True

    def tail(self, items):
        """Whether statements ``items`` are copies and blocks whose only effects are calls of
        lifted functions, at least one: a tail call's code."""
        calls = 0
        for it in items:
            if it[0] in ("copy", "return"):
                continue
            if it[0] != "ops":
                return False
            for o in it[1].ops[:-1]:
                if id(o) in self.dropped or self.pure(o) or o.name == "memref.get_global":
                    continue
                if o.name != "func.call" or o.prop("callee")[1:] not in self.mod.by_name:
                    return False
                calls += 1
        return calls > 0

    def traps(self, b):
        """Whether block ``b`` calls ``trap``, which does not return."""
        return any(o.name == "func.call" and o.prop("callee") == "@trap" for o in b.ops)

    def stack_byte(self, a):
        """``(value, offset)`` of a page-1 address ``(value + offset) | $100``, or None."""
        d = self.defs.get(a)
        if d is None or d.name != "arith.ori":
            return None
        x, k = d.args if self.const_of(d.args[1]) is not None else d.args[::-1]
        if self.const_of(k) != 0x100:
            return None
        while self.defs.get(x) is not None and self.defs[x].name in CASTS:
            x = self.defs[x].args[0]
        d = self.defs.get(x)
        if d is not None and d.name in ("arith.addi", "arith.subi"):
            c = self.const_of(d.args[1])
            if c is not None:
                return d.args[0], (c if d.name == "arith.addi" else -c) & 0xFF
        return x, 0

    def split(self, a):
        """``(constant, rest)`` of an address value: ``rest`` is None or a value."""
        d, top = self.defs.get(a), (1 << self.width(a)) - 1
        if d is not None and d.name == "arith.constant":
            return d.int_prop("value") & top, None
        if d is not None and d.name == "arith.addi":
            for x, y in (d.args, d.args[::-1]):
                if self.const_of(x) is not None:
                    return self.const_of(x) & top, y
        return 0, a

    def reads(self, o):
        """The values printing operation ``o`` reads: an access reads its address through
        :meth:`address` less a constant offset, not the ``index`` value."""
        if id(o) in self.wordreads:
            return list(self.wordreads[id(o)][2:5])
        addr = None
        if o.name in ("memref.load", "memref.store") and o.args[-2] in self.globals:
            addr = len(o.args) - 1
        elif o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write", "@codechk"):
            addr = 0
        out = []
        for i, a in enumerate(o.args):
            if (id(o), i) in self.dead or a in self.globals:
                continue
            if i == addr and id(o) in self.promoted:
                e = self.promoted[id(o)][-1]
                out.extend([e] if isinstance(e, str) else [])
                continue
            split = id(o) in self.wide or id(o) in self.split_reads
            if i == addr and split and self.base_index(o) is not None:
                out.extend(self.base_index(o))
                continue
            if i == addr:
                a = self.split(self.address(o) if o.name != "func.call" else a)[1]
                if a is None:
                    continue
            out.append(a)
        return out

    def base_index(self, o):
        """``(base, index)`` values of the address of access ``o`` when it is a 16-bit value
        plus a zero-extended byte, else None."""
        d = self.defs.get(self.address(o))
        if d is None or d.name != "arith.addi":
            return None
        for b, i in (d.args, d.args[::-1]):
            e = self.defs.get(i)
            if e is not None and e.name == "arith.extui" and self.width(e.args[0]) == 8:
                return b, e.args[0]
        return None

    def printed(self, o):
        """Whether operation ``o`` prints: an effect, or a value something uses."""
        if o.name == "memref.get_global":
            return False
        return not self.pure(o) or any(self.uses.get(r) for r in o.res)


def _read_only(st, found, seen):
    """:class:`State` ``st`` less its cells no load of ``found`` (``(func, op, addresses,
    _)``) nor pointer load reaching them (``seen``, a mask) reads, which it lists as
    ``unread``: what they hold is never observed."""
    read = {a if p is None else (p[0], p[2]) for a, p in st.at.items() if seen[a]}
    for _, o, one, _ in found:
        if o.name == "memref.load" and len(set(one)) == 1 and one[0] in st.at:
            p = st.at[one[0]]
            read.add(one[0] if p is None else (p[0], p[2]))
        elif o.name == "memref.load" and (p := st.field_of(one)) is not None:
            read.add(p[:2])
    structs = [(b, [d for d in fs if (s, d) in read]) for s, (b, fs) in enumerate(st.structs)]
    gone = [a for a, p in st.at.items() if (a if p is None else (p[0], p[2])) not in read]
    return State(
        [a for a in st.scalars if a in read], [x for x in structs if x[1]], st.why, sorted(gone)
    )


class CFunc:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One C function: a lifted function or entry."""

    def __init__(self, func, graph):
        self.f, self.g, self.mod = func, graph, func.mod
        self.name = None
        #: The blocks the graph holds, and those only returning that it returns in place of.
        self.owned = {it[1].name for its in graph.items.values() for it in its if it[0] == "ops"}
        self.owned |= {
            s
            for b in list(self.owned)
            for s in func.block_of[b].ops[-1].succs
            if func.returns_only(func.block_of[s])
        }
        self.names, self.nvar, self.inline, self.memf, self.types = {}, 0, set(), {}, {}
        #: Each SSA value's congruence class representative (:class:`ssa.Coalescer`).
        self.classes = {}
        #: The C declaration of the function, once printed.
        self.head_decl = None
        self.uses, self.user, self.covers, self.copies = {}, {}, {}, {}
        self.body, self.head_text, self.labels = None, "", {}
        self.prov = {"gotos": [], "reduced": [], "loops": [], "conditions": [], "switches": []}
        self.edges = {}
        self._fused = None
        #: The operations the statement being printed prints, and the block visit it is in.
        self.printing, self.visit = None, (None, 0)
        #: The branch whose condition is being printed (its node's last operation).
        self.term = None

    def structure(self):
        """Structure the graph into statements."""
        s = Structurer(
            self.f.conds,
            self.f.quiet,
            self.f.hoistable,
            self.falls,
            lambda n: self.first_pc(n) or 0,
            self.f.tail,
            self.exprable,
            self.f.latchable,
            lambda n: self.first_pc(n) in self.mod.shared,
        )
        self.body = s.structure(self.g)

    def exprable(self, items):
        """Whether statements ``items`` are C expressions (:meth:`Func.exprable`) whose stores
        print as assignments: none tested against the bounds of the array or the regions it
        may write."""
        return self.f.exprable(items) and not any(
            o.name == "memref.store" and self.target(o)[0] in ("bound", "set", "block")
            for o in items[0][1].ops[:-1]
            if id(o) not in self.f.dropped
        )

    # Values used and printed inline.

    def count(self):
        """Count the uses of each value by what prints, and mark the uses that directly
        follow their block."""
        work, seen = [], set()
        self.uses, self.user = {}, {}

        def use(v, who):
            self.uses[v] = self.uses.get(v, 0) + 1
            self.user[v] = who
            if self.uses[v] == 1:
                work.append(v)

        fused = self.fusion()
        for v, adj in self.item_refs(self.body, ()):
            use(v, ("adj" if adj else "far",))
        for b in self.f.blocks:
            if b.name not in self.owned:
                continue
            for o in b.ops[:-1]:
                if id(o) in fused and o.name == "func.call":
                    continue
                if id(o) not in self.f.dropped and self.f.printed(o) and not self.f.pure(o):
                    for a in self.f.reads(o):
                        use(a, o)
        while work:
            d = self.f.defs.get(work.pop())
            if d is None or id(d) in seen or not self.f.pure(d) or id(d) in self.f.dropped:
                continue
            # A check its load makes reads nothing of its own (:meth:`fusion`).
            if id(d) in fused and d.name == "func.call":
                continue
            if self.f.where[id(d)][0].name not in self.owned:
                continue
            seen.add(id(d))
            for a in self.f.reads(d) * (1 + (d.name in MINMAX)):
                use(a, d)

    def item_refs(self, items, after):
        """``(value, adjacent)`` read by ``items``; ``after`` the blocks they directly follow
        (a block, then blocks with no effect)."""
        run = after
        for it in items:
            prev = run
            if it[0] != "ops":
                run = ()
            elif any(self.f.effect(o) for o in it[1].ops[:-1]):
                run = (it[1],)
            else:
                run = (*run, it[1])
            kind = it[0]
            if kind == "copy":
                yield from ((v, self.def_in(v, prev)) for _, v, _ in it[1])
            elif kind == "set":
                yield from ((v, self.def_in(v, prev)) for v in self.lit_values(it[2]))
            elif kind == "return":
                yield from (
                    (v[0] if isinstance(v, tuple) else v, self.def_in(v, prev)) for v in it[-1]
                )
            elif kind == "if":
                for v in self.cond_refs(it):
                    yield v, self.def_in(v, prev)
                yield from self.item_refs(it[3], prev)
                yield from self.item_refs(it[4], prev)
            elif kind == "switch":
                if isinstance(it[1], str):
                    yield it[1], self.def_in(it[1], prev)
                for _, body in it[2]:
                    yield from self.item_refs(body, ())
            elif kind == "loop":
                yield from self.item_refs(it[1], ())
                if len(it) > 3:
                    f, copies = self.latch_edge(it[3], it[2])
                    yield from ((v, False) for v in self.lit_values(f))
                    yield from self.item_refs(copies, ())

    def def_in(self, v, blks):
        """Whether value ``v`` is defined by an operation of one of the blocks ``blks``."""
        v = v[0] if isinstance(v, tuple) else v
        d = self.f.defs.get(v)
        return d is not None and any(self.f.where[id(d)][0] is b for b in blks)

    def lit_values(self, f):
        """The values the literals of ``f``'s cover read."""
        cubes = self.cover(f, self.f.conds.care)
        vals = (self.f.lit_value(key) for cube in cubes for key, _ in cube)
        return [v for v in vals if v is not None]

    def cond_refs(self, it):
        """The values the condition of ``if`` statement ``it`` reads."""
        out = []
        for cube in self.cover(it[1], it[2]):
            for key, _ in cube:
                v = self.f.lit_value(key)
                if v is not None:
                    out.append(v)
        return out

    def cover(self, f, ctx):
        """The cover of ``f`` where ``ctx`` holds, cached."""
        if (f, ctx) not in self.covers:
            self.covers[(f, ctx)] = self.f.conds.cover(f, ctx)
        return self.covers[(f, ctx)]

    def plan(self):
        """Choose the values printed inline: constants, values used once, casts of or
        constant offsets from variables (the root of a shared one is made a variable) and
        loads of a cell no write reaches before a use (:mod:`.recompute`)."""
        f, forced = self.f, set()
        ops = [o for b in f.blocks if b.name in self.owned for o in b.ops[:-1]]
        for o in ops:
            if len(o.res) == 1 and (self.uses.get(o.res[0]) or 0) > 1:
                d = o
                while d is not None and self.offset_of(d) is not None:
                    root = self.offset_of(d)
                    forced.add(root)
                    d = f.defs.get(root)
        for o in ops:
            if len(o.res) != 1 or id(o) in f.dropped or not f.pure(o):
                continue
            v = o.res[0]
            if v in forced and self.offset_of(o) is None and o.name != "arith.constant":
                self.memf[v] = False
                continue
            mem = o.name == "memref.load" and f.named(o) is None
            mem = mem or any(self.memf.get(a) for a in o.args if a in self.inline)
            self.memf[v] = mem
            if f.carry(v):
                continue
            if o.name in ("arith.constant", "ub.poison") or f.named(o) is not None:
                self.inline.add(v)
                continue
            if self.uses.get(v) != 1:
                if not mem and self.cheap(o):
                    self.inline.add(v)
                continue
            if mem and not self.adjacent(o, self.user[v]):
                continue
            self.inline.add(v)
        self.inline |= recompute.Recompute(self).loads()

    def adjacent(self, d, who):
        """Whether the single use ``who`` of ``d``'s value sees memory as ``d`` does."""
        if isinstance(who, tuple):
            blk, i = self.f.where[id(d)]
            return who[0] == "adj" and not any(self.f.effect(x) for x in blk.ops[i + 1 :])
        return self.f.adjacent(d, who)

    def offset_of(self, o):
        """The operand of a cast or constant offset ``o``, or None."""
        if o.name in CASTS:
            return o.args[0]
        if o.name in ("arith.addi", "arith.subi"):
            rest = [a for a in o.args if self.f.const_of(a) is None]
            return rest[0] if len(rest) == 1 else None
        return None

    def cheap(self, o):
        """Whether ``o`` is a cast of, or a constant offset from, a variable or constant."""
        if o.name in CASTS:
            args = o.args
        elif o.name in ("arith.addi", "arith.subi"):
            consts = [a for a in o.args if self.f.const_of(a) is not None]
            if not consts:
                return False
            args = [a for a in o.args if a not in consts] or consts[:1]
        else:
            return False
        for a in args:
            d = self.f.defs.get(a)
            if (
                a in self.inline
                and d is not None
                and not (d.name == "arith.constant" or self.cheap(d))
            ):
                return False
        return True

    # Expressions.

    def var_of(self, v, width=8):
        """The C variable of value ``v`` (or structuring variable), named on first use: one
        per congruence class (:mod:`.ssa`)."""
        v = self.classes.get(v, v)
        if v not in self.names:
            self.nvar += 1
            self.names[v] = f"{'c' if self.f.carry(v) else 'v'}{self.nvar}"
            self.types[self.names[v]] = "uint8_t *" if v in self.f.kptrs else CTYPE[width]
        return self.names[v]

    def fresh(self, width):
        """A new temporary of ``width`` bits."""
        self.nvar += 1
        name = f"v{self.nvar}"
        self.types[name] = CTYPE[width]
        return name

    def ex(self, v, edge=None):
        """The C expression of value ``v``, used as operand ``edge`` ``(op, index)``: a
        constant on an address edge relative to its region."""
        f = self.f
        w = f.width(v)
        if edge is not None and w == 16 and (id(edge[0]), edge[1]) in f.addr_edges:
            c = f.const_of(v)
            if c is not None:
                return self.reloc(c & 0xFFFF)
        if v in self.inline or (f.remat(v) and v not in self.names):
            return self.build(f.defs[v])
        lo, hi = f.iv(v)
        name = self.var_of(v, w)
        return E(cn.ident(name), lo=lo, hi=hi, uns=w == 16, refs=frozenset([name]))

    def reloc(self, c):
        """The constant address ``c`` relative to the region holding it."""
        sym, off = self.mod.mem.base(c)
        if sym is None:
            return lit(c, 16)
        self.mod.used.add(sym)
        node = cn.binop("+", cn.ident(sym), lit(off, 16).node) if off else cn.ident(sym)
        return E(node, c, c, True)

    def indexed(self, o):
        """``(base, index)`` expressions of the address of access ``o`` when it is a 16-bit
        value plus a zero-extended byte, else None."""
        bi = self.f.base_index(o)
        return None if bi is None else (self.addr(bi[0]).node, self.ex(bi[1]).node)

    def base_tests(self, o, how):
        """Per choice of a pointer access ``o`` over regions and spans (``how``), a test of its
        base alone, when the base determines the choice: the bases that reach each choice's
        part of the address set (its addresses less the index's interval) are disjoint
        intervals, so the test holds for every index the base is used with. An access into
        the data block tests its address: what bases reach each part is the song's data."""
        bi = self.f.base_index(o)
        runs = self.mod.addr_set(self.f.name, o)
        if bi is None or not runs or self.mod.block(self.f, o):
            return None
        ylo, yhi = self.f.iv(bi[1])
        idx = np.concatenate([np.arange(lo, hi) for lo, hi in runs])
        mem, parts = self.mod.mem, []
        for k in how[1]:
            parts.append((f"R{k}", mem.regions[k][0], idx[mem.owner[idx] == k]))
        for j in how[2]:
            a, b = mem.held[j]
            inside = idx[(idx >= a) & (idx < b) & (mem.owner[idx] < 0)]
            parts.append((f"C{j}", a, inside))
        spans = [(int(p.min()) - yhi, int(p.max()) - ylo, n, org) for n, org, p in parts if p.size]
        if len(spans) != len(parts):
            return None
        order = sorted(spans)
        if any(x[1] >= y[0] for x, y in zip(order, order[1:])):
            return None
        base = self.addr(bi[0]).node
        tests = []
        for lo, hi, n, org in spans:
            d, ref = lo - org, cn.ident(n + "_ORG")
            if d:
                ref = cn.binop("+" if d > 0 else "-", ref, lit(abs(d), 16).node)
            off = cn.cast("uint16_t", cn.binop("-", base, ref))
            tests.append(cn.binop("<", off, lit(hi - lo + 1, 16).node))
        return tests

    def addr(self, a):
        """The address value ``a`` with its constant relocated (a zero-page one as is)."""
        c, rest = self.f.split(a)
        if self.f.width(a) < 16 and rest is not None:
            r = self.ex(rest) if c == 0 else binary("+", lit(c, 8), self.ex(rest), 8)
            return clean(r, 8)
        if self.f.width(a) < 16:
            return lit(c, 8)
        if rest is None:
            return self.reloc(c)
        if not c:
            return self.ex(rest)
        return binary("+", self.reloc(c), self.ex(rest), 16)

    def target(self, o):
        """How access ``o`` reaches memory: ``("region", k)``, ``("held", j)``, ``("io",)``,
        ``("code",)``, ``("set", regions, spans)`` (a pointer access with an address set over
        several, or into one region of the data block), ``("block", regions, spans)`` (into a
        data block of several, through its reader or writer) or ``("any",)``
        (``rd``/``wr``)."""
        if id(o) in self.f.promoted:
            return ("var",)
        lo, hi = self.f.addr_iv(o)
        mem = self.mod.mem
        if o.name == "func.call" and mem.is_volatile(lo, hi):
            return ("io",)
        blk = self.mod.block(self.f, o)
        if blk and len(blk) > 1:
            # An access of a data block of several runs indexes the array holding its hull.
            b = mem.block_of(blk[0][0], blk[-1][1] - 1)
            if b is not None:
                return ("barray", b)
        found = blk and self.mod.owners(blk, not is_write(o), False)
        if found and len(found[0]) + len(found[1]) == 1:
            return ("set", *found)
        if found:
            return ("block", *found)
        run = self.f.wide.get(id(o))
        if run is not None:
            j = mem.held_span(*run, wide=True)
            if j is not None:
                return ("checked", "held", j)
        # An access the lift proves inside one region or held span reaches it unchecked.
        sure = self.f.proven_iv(o)
        if sure is not None and (k := mem.region(*sure)) is not None:
            return ("region", k)
        if sure is not None and not is_write(o) and (j := mem.held_span(*sure)) is not None:
            return ("held", j)
        bound = self.bound_region(o, lo, hi)
        if bound is not None:
            return bound
        place = self.f.place(o)
        if place is not None:
            return place
        per = self.f.kivs(self.f.address(o))
        if o.name == "func.call" and per and all(mem.is_volatile(a, b) for a, b in per):
            return ("io",)
        if mem.is_code(lo, hi):
            return ("code",)
        store = is_write(o)
        found = self.mod.owners(self.mod.addr_set(self.f.name, o), not store)
        if found:
            regs, spans = found
            if len(regs) + len(spans) == 1:
                return ("region", regs[0]) if regs else ("held", spans[0])
            if self.pointer(o):
                return ("set", regs, spans)
        found = None if store else mem.bounded(lo, hi)
        return ("any",) if found is None else ("bounded", *found)

    def bound_region(self, o, lo, hi):
        """``("region", k)`` for a store the lift bounds to the array holding its base
        (:meth:`Module.run_of`) whose addresses lie in region ``k``, ``("bound", k)`` when
        they leave it (a write past it is out of bounds), else None."""
        run = self.mod.run_of(self.f, o)
        if run is None:
            return None
        mem, per = self.mod.mem, self.f.kivs(self.f.address(o)) or [(lo, hi)]
        own = [mem.owner[max(a, run[0]) : min(b, run[1]) + 1] for a, b in per]
        ks = set(np.unique(np.concatenate(own)).tolist()) - {-1} if own else set()
        if len(ks) != 1:
            return None
        k = ks.pop()
        org, n = mem.regions[k]
        inside = all(org <= a and b < org + n for a, b in per)
        return ("region", k) if inside else ("bound", k)

    def pointer(self, o):
        """Whether access ``o``'s address is a constant plus a value no byte extends: a
        pointer, not an indexed constant base."""
        rest = self.f.split(self.f.address(o))[1]
        d = self.f.defs.get(rest)
        byte = d is not None and d.name == "arith.extui" and self.f.width(d.args[0]) == 8
        return rest is not None and self.f.width(rest) == 16 and not byte

    def terms(self, a, top=True):
        """``(constant, [values])`` summing to the 16-bit address value ``a``: its additions
        that print inline (all of ``a``'s own, as :meth:`split` reads them) taken apart."""
        f, d = self.f, self.f.defs.get(a)
        k = f.const_of(a)
        if k is not None:
            return k, []
        inline = top or a in self.inline or (f.remat(a) and a not in self.names)
        if d is None or d.name != "arith.addi" or not inline or f.width(a) != 16:
            return 0, [a]
        c, out = 0, []
        for x in d.args:
            cx, tx = self.terms(x, False)
            c, out = c + cx, out + tx
        return c & 0xFFFF, out

    def mem_ref(self, o, k, held=False):
        """The region element ``Rk[...]`` (with ``held`` the held constants' ``CODE[...]``)
        that access ``o`` addresses: the address modulo its width (16 bits, or 8 for a
        zero-page one), as the 6502 wraps it, less the origin."""
        name = f"C{k}" if held else f"R{k}"
        c, rest = self.f.split(self.f.address(o))
        if rest in self.f.kptrs:
            p = self.ex(rest)
            return E(cn.index(p.node, lit(c, 8).node), 0, 255, False, True, p.refs)
        sub = self.sub(o, self.f.address(o), k, held)
        sub = clean(sub, 16) if sub.lo < 0 else sub
        if held:
            self.mod.used.add("CODE")
            self.mod.spans_used.add(k)
        else:
            self.mod.regions_used.add(k)
        return E(cn.index(name, sub.node), 0, 255, False, True, sub.refs)

    def block_ref(self, o, b):
        """The element ``Bb[...]`` of block array ``b`` access ``o`` addresses."""
        self.mod.mem.blocks_used.add(b)
        a = self.addr(self.f.address(o))
        off = cn.cast("uint16_t", cn.binop("-", a.node, cn.ident(f"B{b}_ORG")))
        return E(cn.index(f"B{b}", off), 0, 255, False, True, a.refs)

    def sub(self, o, a, k, held):
        """The offset of address value ``a`` of access ``o`` into region ``k`` (or held span
        ``k``): a constant inside it, wherever the additions place it, relative to its origin
        (a ``.h`` name where the layout decides the distance), plus the rest."""
        name = f"C{k}" if held else f"R{k}"
        mem, f = self.mod.mem, self.f
        org, n = mem.held[k] if held else mem.regions[k]
        n = n - org if held else n
        sym = named(f"{name}_ORG", org, org, True)
        c, rest = self.f.split(a)
        top = (1 << f.width(a)) - 1
        c &= top
        deep, parts = self.terms(a) if top == 0xFFFF else (c, [])
        rs, lo, hi = [], 0, 0
        if deep != c and 0 <= deep - org < n and parts:
            c, rs = deep, [self.ex(t) for t in parts]
            lo, hi = (sum(x) for x in zip(*(f.iv(t) for t in parts)))
        elif rest is not None:
            rs, (lo, hi) = [clean(self.ex(rest), f.width(rest))], f.iv(rest)
        # A fixed address in a region the image's relocation moves is offset by its origin.
        moved = not held and mem.moves(org, True) != mem.moves(c)
        if rest is None and moved:
            sub = binary("-", lit(c, 16), sym, 16)
        elif rest is None:
            sub = self.mod.offset(name, o.pc, c, org) if held else lit(c - org, 16)
        elif c == 0 and org:
            sub = binary("-", self.ex(rest), sym, 16)
        else:
            if lo + c <= top < hi + c:
                rs, c = [clean(plus(lit(c, 16), *rs), f.width(a))], 0
            elif lo + c > top:
                c -= top + 1
            off = c - org
            if held and c and off >= 0 and self.mod.placed(c, org):
                sub = plus(self.mod.offset(name, o.pc, c, org), *rs)
            elif off < -0x100 or moved:
                # A constant that is no address near the region (a pointer plus one): its
                # origin stays symbolic, as relocation moves it.
                sub = binary("-", plus(*([lit(c, 16)] if c else []), *rs), sym, 16)
            elif off > 0:
                sub = plus(lit(off, 16), *rs)
            elif off < 0:
                sub = binary("-", plus(*rs), lit(-off, 16), 16)
            else:
                sub = plus(*rs)
        return sub

    def var(self, o):
        """The promoted cell access ``o`` reaches (:meth:`Module.promote`): a scalar, or a
        struct's field of the element its copy index chooses."""
        p = self.f.promoted[id(o)]
        if p[0] == "s":
            return named(self.mod.var_name(p[1]), mem=True)
        _, s, d, e = p
        el = lit(e, 8) if isinstance(e, int) else self.ex(e)
        return E(cn.index(f"V{s}_f{d:02X}", el.node), 0, 255, False, True, el.refs)

    def load(self, o):
        """The C expression of a memory load ``o``: of a constant byte at a fixed address, its
        name in the ``.h`` (:meth:`Module.konst`)."""
        if o.args[0] == self.f.symbols:
            return named(self.mod.symbol(self.f.const_of(o.args[1])))
        if self.f.named(o) is not None:
            return named(self.mod.konst(self.f.iv(self.f.address(o))[0]))
        if id(o) in self.f.promoted:
            return self.var(o)
        how = self.target(o)
        if id(o) in self.f.aware and self.mod.var_at and how[0] != "checked":
            how = ("any",)
        if how[0] in ("region", "held"):
            fused = self.fusion().get(id(o)) if how[0] == "region" else None
            if fused is not None:
                return self.guarded_read(o, how[1], fused)
            return self.mem_ref(o, how[-1], how[0] == "held")
        if how[0] == "barray":
            # The block's reader, checking the address first where a check guarding the load
            # folds into it.
            fused = self.fusion().get(id(o))
            self.mod.mem.blocks_used.add(how[1])
            bi = self.f.base_index(o)
            if bi is not None:
                at, idx = bi[0], self.ex(bi[1])
            else:
                at, idx = self.f.address(o), lit(0, 8)
            p = self.addr(at)
            refs = p.refs | idx.refs
            if fused is None:
                self.mod.block_readers.add(how[1])
                return E(cn.call(f"B{how[1]}r", p.node, idx.node), 0, 255, False, True, refs)
            self.mod.traps.add(fused[1])
            name = self.mod.checked_fn(f"B{how[1]}", fused[0])
            node = cn.call(name, p.node, idx.node, cn.const(fused[1]))
            return E(self.check("objchk", o, node, fused[1]), 0, 255, False, True, refs)
        a = self.addr(self.f.address(o))
        if how[0] == "checked":
            return self.checked(o, how[2], a)
        if how[0] == "bounded":
            # A constant base plus a byte inside one held span: its bounds-checked reader.
            if not how[1] and self.f.base_index(o) is not None:
                return self.checked(o, how[2][0], a)
            ((test, el),) = self.mod.choices(how[1], how[2], a.node, self.indexed(o))
            self.mod.helpers.add("oob_rd")
            test = self.check("bounded", o, test)
            node = c_ast.TernaryOp(test, el, self.arm(cn.call("oob_rd", a.node)))
            return E(node, 0, 255, False, True, a.refs)
        if how[0] == "set":
            fused = self.fusion().get(id(o))
            if fused is not None and len(how[1]) + len(how[2]) == 1:
                return self.guarded_set(o, how, fused)
            choices = self.mod.choices(
                how[1], how[2], a.node, self.indexed(o), self.base_tests(o, how)
            )
            node = choices[-1][1]
            for t, r in reversed(choices[:-1]):
                node = c_ast.TernaryOp(t, r, node)
            return E(node, 0, 255, False, True, a.refs)
        if how[0] == "block":
            name = self.mod.block_fn("rd", how[1], how[2])
            fused = self.fusion().get(id(o))
            if fused is not None:
                self.mod.traps.add(fused[1])
                name = self.mod.checked_fn(name, fused[0])
                node = self.check("objchk", o, cn.call(name, a.node, cn.const(fused[1])), fused[1])
                return E(node, 0, 255, False, True, a.refs)
            return E(cn.call(name, a.node), 0, 255, False, True, a.refs)
        fn = "oob_rd" if how[0] == "code" else "rd"
        self.mod.helpers.add(fn)
        if id(o) in self.f.aware and self.mod.var_at:
            self.mod.helpers.add("cgen_var")
        node = self.check("oob" if how[0] == "code" else "rd", o, cn.call(fn, a.node))
        return E(node, 0, 255, False, True, a.refs)

    def guarded_read(self, o, k, fused):
        """The load ``o`` of region ``k`` through the reader that checks its address into
        object ``fused[0]`` first and leaves by trap ``fused[1]`` (:meth:`Module.guarded_fn`)."""
        a = self.addr(self.f.address(o))
        j, trap = fused
        self.mod.traps.add(trap)
        self.mod.regions_used.add(k)
        node = cn.call(self.mod.guarded_fn(k, j), a.node, cn.const(trap))
        return E(self.check("objchk", o, node, trap), 0, 255, False, True, a.refs)

    def plain_region(self, x, cont):
        """Whether region load ``x`` of block ``cont`` addresses its region by a 16-bit value
        with no constant offset, as its guarded reader (:meth:`guarded_read`) does, after
        nothing in the block that has an effect: the check the reader makes comes where the
        branch to the trap was."""
        f = self.f
        a = f.address(x)
        c, rest = f.split(a)
        plain = c == 0 and rest is not None and f.width(a) == 16 and rest not in f.kptrs
        return plain and self.unmapped(x, cont)

    def unmapped(self, x, cont):
        """Whether load ``x`` of block ``cont`` is not of a mapped cell, not the one value a
        branch tests (a call that may trap is not printed in a condition) and follows nothing in its
        block that has an effect."""
        f = self.f
        tested = any(
            u.name in ("arith.cmpi", "cf.switch")
            and id(u) not in f.dropped
            and (u.name == "cf.switch" or f.uses.get(u.res[0]))
            for u in f.users.get(x.res[0], ())
        )
        if tested and f.uses.get(x.res[0]) == 1:
            return False
        before = cont.ops[: next(i for i, y in enumerate(cont.ops) if y is x)]
        mapped = id(x) in f.aware and bool(self.mod.var_at)
        return not mapped and not any(f.effect(y) for y in before)

    def plain_set(self, x, cont):
        """Whether load ``x`` of block ``cont`` is of one region or span whose address set
        the lift gives (:meth:`target`) and reads it by its address or a base and an index
        (:meth:`guarded_set`)."""
        how = self.target(x)
        return how[0] == "set" and len(how[1]) + len(how[2]) == 1 and self.unmapped(x, cont)

    def guarded_set(self, o, how, fused):
        """The load ``o`` of the one region or span ``how`` names through the reader that
        checks its address into object ``fused[0]`` first and leaves by trap ``fused[1]``:
        ``<array>c<object>(address, k)``, or ``<array>x<object>(base, index, k)`` where the
        address is a base plus a byte, as ``(zp),Y`` forms it."""
        a = self.addr(self.f.address(o))
        indexed = self.indexed(o)
        j, trap = fused
        self.mod.traps.add(trap)
        ((_, _),) = self.mod.choices(how[1], how[2], a.node, indexed)
        name = f"R{how[1][0]}" if how[1] else f"C{how[2][0]}"
        args = [*indexed, cn.const(trap)] if indexed else [a.node, cn.const(trap)]
        node = cn.call(self.mod.set_fn(name, j, bool(indexed)), *args)
        return E(self.check("objchk", o, node, trap), 0, 255, False, True, a.refs)

    def fusion(self):
        """``{id(op): ...}`` of the object checks a load through the data block's reader
        makes itself: the check's (the load) and the load's (``(j, k)``: the object, and the
        trap the check leaves by). A check of the address a load in the block it guards
        reads, its other edge a trap, is the load's own (:meth:`Module.checked_fn`)."""
        if self._fused is not None:
            return self._fused
        f, out = self.f, {}
        for b in f.blocks:
            term = b.ops[-1] if b.ops else None
            if term is None or term.name != "cf.cond_br":
                continue
            succ = [f.block_of.get(x) for x in term.succs[:2]]
            traps = [
                x is not None
                and bool(x.ops)
                and x.ops[0].name == "func.call"
                and x.ops[0].prop("callee") == "@trap"
                for x in succ
            ]
            if traps.count(True) != 1 or None in succ:
                continue
            k = f.const_of(succ[traps.index(True)].ops[0].args[0])
            cont = succ[traps.index(False)]
            # The checks the condition is computed from through arithmetic.
            parts, todo = [], [term.args[0]]
            while todo:
                d = f.defs.get(todo.pop())
                if d is not None and d.name.startswith("arith."):
                    todo += d.args
                elif d is not None:
                    parts.append(d)
            for c in parts:
                x = self.fused_load(c, cont) if k is not None else None
                if x is not None and id(x) not in out:
                    out[id(c)], out[id(x)] = x, (f.defs[c.args[1]].int_prop("value"), k)
        # Loads printed as a base indexed by a byte read the base and the index.
        aware = self.mod.var_at and f.aware
        for x in (x for b in f.blocks for x in b.ops if x.name == "memref.load"):
            how = self.target(x)[0]
            sure = how == "checked" and id(x) in f.sure and not (aware and id(x) in f.aware)
            if how == "barray" or sure:
                f.split_reads.add(id(x))

        self._fused = out
        self.word_reads(out)
        return out

    def word_reads(self, fused):
        """Record in ``f.wordreads`` each 16-bit value ``(high << 8) | low`` of two guarded
        reads of one region through the same object at one index from two bases (a table split
        into its low and high bytes), nothing else reading the bytes, the high read directly
        after the low and no effect between them or before the value: a call of one reader
        (:meth:`word_read`) checks the low read, reads it, then the high one."""
        f = self.f
        for blk in f.blocks:
            for o in blk.ops if blk.name in self.owned else ():
                got = None if id(o) in f.dropped else f.joined(o)
                if got is None:
                    continue
                hi, lo = (f.defs.get(v) for v in got[:2])
                if not all(x is not None and isinstance(fused.get(id(x)), tuple) for x in (hi, lo)):
                    continue
                if any(f.uses.get(r) != 1 for r in (hi.res[0], lo.res[0])):
                    continue
                if any(f.uses.get(x.res[0]) != 1 for x in got[2]):
                    continue
                (bh, ih), (bl, il) = f.base_index(hi), f.base_index(lo)
                same = fused[id(hi)][0] == fused[id(lo)][0] and ih == il
                mid = f.between(lo, hi)
                if not same or mid is None or any(f.effect(x) for x in mid):
                    continue
                if self.target(hi)[:2] != self.target(lo)[:2] or not f.adjacent(hi, o):
                    continue
                f.wordreads[id(o)] = (lo, hi, bl, bh, ih)

    def word_read(self, o):
        """The call of the reader of the table split into bytes that the 16-bit value of
        operation ``o`` is (:meth:`word_reads`)."""
        lo, hi, bl, bh, idx = self.f.wordreads[id(o)]
        fused = self.fusion()
        (j, klo), (_, khi) = fused[id(lo)], fused[id(hi)]
        a, b, i = self.addr(bl), self.addr(bh), self.ex(idx)
        k = self.target(lo)[1]
        self.mod.traps.update((klo, khi))
        self.mod.regions_used.add(k)
        node = cn.call(self.mod.word_fn(k, j), a.node, b.node, i.node, cn.const(klo), cn.const(khi))
        return E(self.check("objchk", lo, node, klo), 0, 0xFFFF, True, True, join(a, b, i)["refs"])

    def fused_load(self, c, cont):
        """The load of block ``cont`` through a data block's reader or array whose address
        object check ``c`` tests (not a check after a pointer's definition), else None."""
        f = self.f
        if c.name != "func.call" or c.prop("callee") != "@objchk":
            return None
        x = next(
            (x for x in cont.ops if x.name == "memref.load" and f.address(x) == c.args[0]), None
        )
        how = None if x is None else self.target(x)[0]
        plain = (
            (how == "region" and self.plain_region(x, cont))
            or (how == "set" and self.plain_set(x, cont))
            or (how == "checked" and id(x) in f.sure and self.unmapped(x, cont))
        )
        if how not in ("block", "barray") and not plain:
            return None
        if how == "barray":
            f.split_reads.add(id(x))
        return x

    def objchk(self, o):
        """The test ``objchk(p, j)`` of call ``o``: whether pointer ``p`` lies outside object
        ``j``, whose bounds the ``.h`` names; none where the load it guards makes it
        (:meth:`fusion`)."""
        if id(o) in self.fusion():
            return lit(0, 1)
        p, j = self.ex(o.args[0]), self.f.defs[o.args[1]].int_prop("value")
        node = self.check("objchk", o, self.mod.outside(j, p), self.trap_after(o))
        return E(node, 0, 1, **join(p))

    def check(self, kind, o, node, trap=None):
        """Expression ``node`` marked as a check of access (or guard) ``o`` (*Translation
        cost*), ``trap`` the ``k`` its failure takes."""
        return cn.marked(node, self.mod.marks.add(("check", kind, o, self.f.name, trap)))

    def arm(self, node):
        """Expression ``node`` marked as the failure arm of the check last marked."""
        return cn.marked(node, self.mod.marks.add(("arm", len(self.mod.marks.recs) - 1)))

    def trap_after(self, o):
        """The ``k`` of the ``trap(k)`` a branch on the value of call ``o`` leads to directly,
        through pure operations on it; None for no such branch or no result."""
        f = self.f
        work, seen = list(o.res), set()
        by_name = {b.name: b for b in f.blocks}
        while work:
            v = work.pop()
            for x in (x for b in f.blocks for x in b.ops if v in x.args):
                if x.name == "cf.cond_br":
                    traps = [
                        t
                        for b in (by_name[s] for s in x.succs if s in by_name)
                        for t in b.ops
                        if t.name == "func.call" and t.prop("callee") == "@trap"
                    ]
                    if traps:
                        return f.const_of(traps[0].args[0])
                elif f.pure(x) and x.res and x.res[0] not in seen:
                    seen.add(x.res[0])
                    work.append(x.res[0])
        return None

    def checked(self, o, j, a):
        """Pointer load ``o`` of address ``a`` (an expression) through held span ``j``'s
        bounds-checked reader ``rdCj(base, index)``: the address as a 16-bit base plus a byte
        (an index value, or a constant below 256), as ``(zp),Y`` forms it; the span's element
        there where the lift proves the address inside it."""
        self.mod.used.add("CODE")
        self.mod.spans_used.add(j)
        mapped = id(o) in self.f.aware and bool(self.mod.var_at)
        sure = id(o) in self.f.sure and not mapped
        if mapped:
            self.mod.helpers.add("cgen_var")
            self.mod.var_spans.add(j)
        if not sure:
            self.mod.checked.add(j)
        bi = self.f.base_index(o)
        c, rest = self.f.split(self.f.address(o))
        if bi is not None:
            at, idx = bi[0], self.ex(bi[1])
        elif rest is not None and self.f.width(rest) == 16 and 0 < c < 0x100:
            at, idx = rest, lit(c, 8)
        else:
            at, idx = self.f.address(o), lit(0, 8)
        if sure:
            self.mod.used.add("CGEN_IX")
            off = self.sub(o, at, j, True)
            fused = self.fusion().get(id(o))
            if fused is not None:
                self.mod.traps.add(fused[1])
                name = self.mod.set_fn(f"C{j}", fused[0], "g")
                node = cn.call(name, off.node, clean(idx, 8).node, cn.const(fused[1]))
                node = self.check("objchk", o, node, fused[1])
                return E(node, 0, 255, False, True, join(off, idx)["refs"])
            node = cn.call("CGEN_IX", cn.ident(f"C{j}"), off.node, clean(idx, 8).node)
            return E(node, 0, 255, False, True, join(off, idx)["refs"])
        base = self.addr(at) if bi is not None else a if at == self.f.address(o) else self.ex(at)
        refs = join(base, idx)["refs"]
        node = self.check("rdC", o, cn.call(f"rdC{j}", base.node, idx.node))
        return E(node, 0, 255, False, True, refs)

    def build(self, o):  # pylint: disable=too-many-return-statements
        """The C expression of the single-result pure operation ``o``."""
        if self.printing is not None:
            self.printing.append(o)
        name, w = o.name, self.f.width(o.res[0])
        if name == "arith.constant":
            return lit(o.int_prop("value"), w)
        if name == "ub.poison":
            return lit(0, w)
        if name == "memref.load":
            return self.load(o)
        if id(o) in self.f.wordreads:
            return self.word_read(o)
        if name == "arith.kfam":
            return self.kfam(o)
        if name == "func.call" and o.prop("callee") == "@objchk":
            return self.objchk(o)
        if name == "func.call":
            a, v = self.addr(o.args[0]), self.ex(o.args[1])
            self.mod.helpers.add("codechk")
            node = self.check("codechk", o, cn.call("codechk", a.node, v.node), self.trap_after(o))
            return E(node, 0, 1, **join(a, v))
        args = [self.ex(a, (o, i)) for i, a in enumerate(o.args)]
        if name in BINOP:
            sym = BINOP[name]
            a, b = args
            if sym == "+" and b.lo == b.hi and (1 << w) - b.lo < min(0x100, (1 << w) // 2):
                return binary("-", a, lit((1 << w) - b.lo, w), w)
            if sym in ("&", "|", "^") and w == 1:
                if sym == "^" and is_lit(b, 1):
                    return negated(truth(a))
                if sym == "^":
                    return binary(sym, a, b, 1)
                return binary({"&": "&&", "|": "||"}[sym], truth(a), truth(b), 1)
            return binary(sym, a, b, w)
        if name == "arith.cmpi" and (st := self.over_state(o.res[0])) is not None:
            return self.member(st[0], st[1], st[2] != 0)
        if name == "arith.cmpi":
            pred, w0 = o.int_prop("predicate"), self.f.width(o.args[0])
            return self.cmpi(pred, *args, w0)
        if name in MINMAX:
            return ternary(self.cmpi(MINMAX[name], *args, w), *args)
        if name in SIGNED:
            sym = SIGNED[name]
            a, b = (cast(x, f"int{w}_t", w, True) for x in args)
            return cast(E(cn.binop(sym, a.node, b.node), **join(a, b)), CTYPE[w], w)
        if name == "arith.extsi":
            w0 = self.f.width(o.args[0])
            if w0 == 1:
                neg = E(c_ast.UnaryOp("-", args[0].node), -1, 0, **join(args[0]))
                return cast(neg, CTYPE[w], w)
            return cast(cast(args[0], f"int{w0}_t", w0, True), CTYPE[w], w)
        if name in ("arith.extui", "arith.index_castui", "arith.index_cast"):
            return clean(args[0], self.f.width(o.args[0]))
        if name == "arith.trunci":
            return clean(args[0], w)
        if name == "arith.select":
            return scaled(*args) or ternary(truth(args[0]), args[1], args[2])
        raise NotImplementedError(name)

    def kfam(self, o):
        """The C expression of an ``arith.kfam``: ``a + k`` or ``a - k`` where its values step
        by one with the instance index ``k``, else an element of a constant table indexed by
        ``k`` (one indexed load, where a stride would be a multiplication)."""
        f, w = self.f, self.f.width(o.res[0])
        tab, k = f.ktabs[o.res[0]], self.ex(o.args[0])
        if o.res[0] in f.kptrs:
            name = self.mod.pointers(f.kptrs[o.res[0]], tab[f.kvals])
            return E(cn.index(name, k.node), 0, 0xFFFF, True, False, k.refs)
        m = (1 << w) - 1
        fit = line([(int(x), int(tab[x])) for x in f.kvals], m)
        if fit is None or fit[1] not in (1, m):
            name = self.mod.table(w, tab)
            lo, hi = int(tab[f.kvals].min()), int(tab[f.kvals].max())
            return E(cn.index(name, k.node), lo, hi, w == 16, False, k.refs)
        a, st = fit
        if st == m:
            return binary("-", lit(a, w), k, w)
        return binary("+", lit(a, w), k, w) if a else k

    @staticmethod
    def cmpi(pred, a, b, width):
        """An ``arith.cmpi`` of ``width``-bit operands: a signed one on ``intN_t`` casts, which
        reduce modulo ``N`` themselves."""
        sym = CMP[pred]
        if pred in (2, 3, 4, 5):
            k = b.lo if b.lo == b.hi else None
            st = f"int{width}_t"
            a = cast(a, st, width, True)
            if k is None:
                b = cast(b, st, width, True)
            else:
                k = twos(k, width)
                node = cn.const(k) if k >= 0 else c_ast.UnaryOp("-", cn.const(-k))
                b = E(node, lo=k, hi=k)
        else:
            a, b = clean(a, width), clean(b, width)
        if b.lo == b.hi and b.lo in (0, 1) and 0 <= a.lo and a.hi <= 1 and sym in ("==", "!="):
            return truth(a) if (sym == "==") == (b.lo == 1) else negated(truth(a))
        return compare(sym, a, b)

    def state_of(self, v):
        """The opcode cell (:meth:`Module.enum_of`) whose load value ``v`` is, or None."""
        d = self.f.defs.get(v)
        p = self.f.promoted.get(id(d)) if d is not None and d.name == "memref.load" else None
        return p[1] if p is not None and p[0] == "s" and self.mod.enum_of(p[1]) else None

    def over_state(self, v):
        """``(u, values, results)`` where value ``v`` is computed by arithmetic from the load
        ``u`` of one opcode cell alone: the cell's values and ``v``'s for each; else None."""
        if not self.mod.opcodes:
            return None
        leaves, work, seen = set(), [v], set()
        while work:
            x = work.pop()
            if x not in seen:
                seen.add(x)
                d = self.f.defs.get(x)
                if d is not None and d.name in REMAT:
                    continue
                if d is not None and d.name.startswith("arith.") and d.name != "arith.kfam":
                    work.extend(d.args)
                else:
                    leaves.add(x)
        u = leaves.pop() if len(leaves) == 1 else None
        a = self.state_of(u)
        if a is None:
            return None
        vals = np.array(self.mod.opcodes[a], dtype=np.int64)
        try:
            r = self.f.evaluate(v, {u: vals}, {})
        except Unknown:
            return None
        return u, vals, np.broadcast_to(r, vals.shape)

    def member(self, u, vals, hits):
        """The test that the opcode cell load ``u`` holds one of ``vals[hits]``, by name."""
        names, x = self.mod.enum_of(self.state_of(u)), self.ex(u)
        if hits.sum() * 2 <= len(vals):
            return logic("||", [compare("==", x, enum_lit(names, t)) for t in vals[hits]])
        return logic("&&", [compare("!=", x, enum_lit(names, t)) for t in vals[~hits]])

    def state_value(self, v, names):
        """The value ``v`` stored into an opcode cell of enumerators ``names``, as one of
        them: by the state the cell load it is computed from holds; None where ``v`` may
        leave them or is that state."""
        c = self.f.const_of(v)
        if c is not None:
            c &= 0xFF
            return enum_lit(names, c) if c in names else None
        st = self.over_state(v)
        if st is None or (st[2] == st[1]).all() or not set(st[2].tolist()) <= set(names):
            return None
        u, vals, r = st
        outs = sorted(set(r.tolist()), key=lambda y: (-(r == y).sum(), y))
        e = enum_lit(names, outs[0])
        for y in outs[1:]:
            e = ternary(self.member(u, vals, r == y), enum_lit(names, y), e, lo=0, hi=255)
        return e

    def literal(self, key, pol):
        """The C condition of literal ``key`` with polarity ``pol``."""
        st = self.over_state(key[1]) if key[0] == "x" else None
        if key[0] == "v":
            e = var(self.selector(key[1]), 1)
        elif key[0] == "c":
            e = truth(self.ex(key[1]))
        elif st is not None:
            e = self.member(st[0], st[1], st[2] == key[2])
        else:
            x = key[1]
            e = self.cmpi(0, self.ex(x), self.label(x, key[2]), self.f.width(x))
        return e if pol else negated(e)

    def label(self, x, k):
        """Case value ``k`` of selector ``x``: an address when ``x`` is a computed jump's, a
        state's name when an opcode cell's."""
        if x in self.f.flags:
            return self.reloc(k)
        names = self.mod.enum_of(self.state_of(x))
        return enum_lit(names, k) if names and k in names else lit(k, self.f.width(x))

    def cond(self, f, ctx, fx=None):
        """The C condition of ``f`` where ``ctx`` holds; ``fx`` ``[(items, key, _)]``:
        statements evaluated with literal ``key``, as a comma expression before it."""
        cubes = self.cover(f, ctx)
        effects = {key: items for items, key, _ in fx or ()}

        def lit_of(k, p):
            e = self.literal(k, p)
            if k not in effects:
                return e
            parts = _expressions(self.convert(effects[k]))
            refs = e.refs | frozenset().union(*(cs.names_in([x]) for x in parts))
            return E(c_ast.ExprList([*parts, e.node]), e.lo, e.hi, refs=refs)

        saved, self.printing, visit, term = self.printing, [], self.visit, self.term
        e = logic("||", [logic("&&", [lit_of(k, p) for k, p in c]) for c in cubes])
        got, self.printing, self.visit = self.printing, saved, visit
        if saved is not None:
            saved += got
            return e
        got = [o for o in (term,) if o is not None] + got
        if not any(o.pc for o in got):
            return e
        return replace(
            e, node=cn.marked(e.node, self.mod.marks.add(("pc", self.f.name, *self.visit, got)))
        )

    def branch_of(self, n):
        """The last operation of graph node ``n``: the branch its condition prints."""
        blocks = self.blocks(n)
        return blocks[-1].ops[-1] if blocks and blocks[-1].ops else None

    # Statements.

    def define(self, v, e, out):
        """Assign the variable of value ``v`` as ``e``: an assignment of a pure value
        (:meth:`Func.pure`) nothing printed reads is dropped, as an unread copy is."""
        w = self.f.width(v)
        d = self.f.defs.get(v)
        out.append(
            cn.assign(self.var_of(v, w), stored(e, w).node, d is not None and self.f.pure(d))
        )

    def stmt(self, o, out):
        """Append the statements of operation ``o``."""
        name, f = o.name, self.f
        if name == "memref.get_global" or id(o) in f.dropped:
            return
        if (
            name == "func.call"
            and o.prop("callee") in CHECKS
            or (name != "func.call" and f.pure(o))
        ):
            v = o.res[0]
            if v not in self.inline and self.uses.get(v):
                self.define(v, self.build(o), out)
            return
        if name == "memref.store":
            self.store(o, out)
        elif name == "func.call":
            self.call(o, out)
        else:
            raise NotImplementedError(name)

    def stack_halves(self, o, c):
        """The value ``c`` of a store ``o`` to a stack byte that, with a constant store to the
        byte below in its block, writes a return address the program holds: relative to it."""
        f = self.f
        me = f.stack_byte(f.address(o))
        if me is None:
            return None
        blk, _ = f.where[id(o)]
        for x in blk.ops:
            if x is o or x.name != "memref.store" or x.args[-2] not in f.globals:
                continue
            other, v = f.stack_byte(f.address(x)), f.const_of(x.args[0])
            if other is None or v is None or other[0] != me[0]:
                continue
            if (other[1] + 1) & 0xFF == me[1]:
                word, hi = (c & 0xFF) << 8 | v & 0xFF, True
            elif (me[1] + 1) & 0xFF == other[1]:
                word, hi = (v & 0xFF) << 8 | c & 0xFF, False
            else:
                continue
            if self.mod.mem.base(word)[0] is None:
                return None
            return self.half(self.reloc(word), hi)
        return None

    @staticmethod
    def half(e, hi):
        """The high (``hi``) or low byte of address ``e``, as a byte store writes it."""
        if hi:
            return E(cn.binop(">>", e.node, cn.const(8)), 0, 255)
        return E(cn.cast("uint8_t", e.node), 0, 255)

    def halves(self, o):
        """The value of a constant store ``o`` that, with a constant store to the other byte
        of a pointer pair in its block, writes an address: relative to its region."""
        f = self.f
        c, a = f.const_of(o.args[0]), self.f.split(f.address(o))
        if c is not None and a[1] is not None:
            return self.stack_halves(o, c)
        if c is None or a[1] is not None:
            return None
        pairs, a = self.mod.mem.pairs, a[0]
        cand = [(a, pairs[a])] if a in pairs else []
        cand += [(p, a) for p in self.mod.mem.pairs_to.get(a, ())]
        blk, i = f.where[id(o)]
        cells = {}
        for j, x in enumerate(blk.ops):
            if x.name == "memref.store" and x.args[-2] in f.globals:
                xa = self.f.split(f.address(x))
                if xa[1] is None:
                    cells.setdefault(xa[0], []).append((j, f.const_of(x.args[0])))
        found = []
        for lo, hi in cand:
            other = hi if a == lo else lo
            for j, v in cells.get(other, ()):
                between = any(
                    min(i, j) < k < max(i, j) for b in (lo, hi) for k, _ in cells.get(b, ())
                )
                if v is not None and not between:
                    found.append((abs(j - i), lo, hi, v))
        if not found:
            return None
        _, lo, hi, v = min(found)
        found = {a: c, (hi if a == lo else lo): v}
        word = found[lo] & 0xFF | (found[hi] & 0xFF) << 8
        sym, _ = self.mod.mem.base(word)
        if sym is None:
            return None
        return self.half(self.reloc(word), a != lo)

    def store(self, o, out):
        """A memory store (or an ``io_write`` that cannot reach I/O)."""
        val = o.args[1] if o.name == "func.call" else o.args[0]
        how, v = self.target(o), (o.name != "func.call" and self.halves(o)) or self.ex(val)
        v = stored(v, 8).node
        if id(o) in self.f.aware and self.mod.var_at:
            how = ("any",)
            self.mod.helpers.add("cgen_var")
        if how[0] == "var":
            ref, p = self.var(o), self.f.promoted[id(o)]
            names = self.mod.enum_of(p[1]) if p[0] == "s" else None
            got = self.state_value(val, names) if names else None
            out.append(cn.assign(ref.node, got.node if got else v))
            return
        if how[0] in ("region", "held", "barray"):
            ref = self.block_ref(o, how[1]) if how[0] == "barray" else None
            ref = ref or self.mem_ref(o, how[1], how[0] == "held")
            out.append(cn.assign(ref.node, v))
            return
        a = self.addr(self.f.address(o))
        if how[0] == "block":
            out.append(cn.call(self.mod.block_fn("wr", how[1], how[2]), a.node, v))
            return
        if how[0] == "set":
            choices = self.mod.choices(
                how[1], how[2], a.node, self.indexed(o), self.base_tests(o, how)
            )
            st = cn.assign(choices[-1][1], v)
            for t, r in reversed(choices[:-1]):
                st = c_ast.If(t, cn.assign(r, v), st)
            out.append(st)
            return
        if how[0] == "bound":
            # A store inside the array holding its base, out of bounds past it.
            ((test, el),) = self.mod.choices((how[1],), (), a.node, self.indexed(o))
            self.mod.helpers.add("oob_wr")
            test = self.check("bounded", o, test)
            out.append(c_ast.If(test, cn.assign(el, v), self.arm(cn.call("oob_wr", a.node, v))))
            return
        fn = "oob_wr" if how[0] == "code" else "wr"
        self.mod.helpers.add(fn)
        out.append(self.check("oob" if how[0] == "code" else "wr", o, cn.call(fn, a.node, v)))

    def call(self, o, out):
        """A call of an external or lifted function."""
        f = self.f
        callee = o.prop("callee")[1:]
        if callee == "trap":
            k = f.const_of(o.args[0])
            self.mod.traps.add(k)
            st = cn.call("trap", self.ex(o.args[0]).node)
            cn.prov(st).rec = self.mod.marks.add(("trap", k, f.name))
            out.append(st)
            return
        if callee not in ("io_read", "io_write"):
            target = self.mod.mains[callee]
            args = [self.ex(a, (o, i)) for i, a in enumerate(o.args) if (id(o), i) not in f.dead]
            node = cn.call(self.mod.name_of(target, self), *(a.node for a in args))
            res = [o.res[j] for j in target.f.live_res]
            used = [r for r in res if self.uses.get(r)]
            if len(res) == 1 and used:
                out.append(cn.assign(self.var_of(res[0], f.width(res[0])), node))
            elif len(res) > 1 and used:
                out.append(node)
                for j, r in zip(target.f.live_res, res):
                    if self.uses.get(r):
                        rv = self.mod.reg_var(target.f, j)
                        self.define(r, named(rv, hi=(1 << f.width(r)) - 1), out)
            else:
                out.append(node)
            return
        how = self.target(o)
        if callee == "io_write":
            if how[0] == "io":
                a, v = self.addr(o.args[0]), self.ex(o.args[1])
                out.append(cn.call("io_write", a.node, stored(v, 8).node))
            else:
                self.store(o, out)
            return
        e = None
        if how[0] == "io":
            a = self.addr(o.args[0])
            e = E(cn.call("io_read", a.node), hi=255, refs=a.refs)
        if self.uses.get(o.res[0]):
            self.define(o.res[0], e or self.load(o), out)
        elif how[0] in ("io", "any"):
            out.append((e or self.load(o)).node)

    def assign(self, pairs, out):
        """The parallel assignment of block arguments ``[(arg, value, edge)]``, sequenced:
        each copy once no other pending copy reads its destination, a cycle broken by saving
        one destination in a temporary the copies reading it read instead."""
        pend = []
        for a, v, edge in pairs:
            name, e = self.var_of(a, self.f.width(a)), self.ex(v, edge)
            if not (isinstance(e.node, c_ast.ID) and e.node.name == name):
                pend.append((name, self.f.width(a), stored(e, self.f.width(a))))
        while pend:
            read = [frozenset().union(*(e.refs for m, _, e in pend if m != n)) for n, _, _ in pend]
            free = [i for i, (n, _, _) in enumerate(pend) if n not in read[i]]
            if free:
                n, _, e = pend.pop(free[0])
                out.append(cn.assign(n, e.node, True))
                continue
            n, w, _ = pend[0]
            t = self.fresh(w)
            out.append(cn.assign(t, cn.ident(n), True))
            pend = [pend[0]] + [
                (m, x, replace(e, node=cn.substitute(e.node, {n: t}), refs=(e.refs - {n}) | {t}))
                for m, x, e in pend[1:]
            ]

    def jump(self, vals):
        """The statements returning ``[(value, edge)]``."""
        if not vals:
            return [cn.ret()]
        es = [self.ex(v, edge) for v, edge in vals]
        if len(es) == 1:
            return [cn.ret(stored(es[0], self.f.width(vals[0][0])).node)]
        regs = [self.mod.reg_var(self.f, j) for j in self.f.live_res]
        sets = [
            cn.assign(r, e.node)
            for r, e in zip(regs, es)
            if not (isinstance(e.node, c_ast.ID) and e.node.name == r)
        ]
        return sets + [cn.ret()]

    def convert(self, items):  # pylint: disable=too-many-branches
        """The C statements of structured ``items``."""
        out = []
        for it in items:
            kind = it[0]
            if kind == "ops":
                self.visit = (it[1].name, self.mod.marks.visit())
                for o in it[1].ops[:-1]:
                    self.marked(out, lambda o=o: self.stmt(o, out), [o])
            elif kind == "copy":
                self.marked(out, lambda it=it: self.assign(it[1], out), [], "copy")
            elif kind == "return":
                self.marked(out, lambda it=it: out.extend(self.jump(it[1])), [], "copy")
            elif kind == "set":
                f, bdd = it[2], self.f.conds.bdd
                e = self.cond(f, self.f.conds.care) if f not in (bdd.true, bdd.false) else None
                st = cn.assign(self.selector(it[1]), e.node if e else cn.const(int(f == bdd.true)))
                out.append(self.mark(st, [], "copy"))
            elif kind == "halt":
                out.append(self.halt())
            elif kind == "site":
                self.tail_site(it[1], it[2])
            elif kind == "fall":
                out.append(cn.fallthrough())
            elif kind in ("break", "continue"):
                out.append(c_ast.Break() if kind == "break" else c_ast.Continue())
            elif kind == "goto":
                q, img = self.first_pc(it[1]), self.mod.mem.image
                out.append(c_ast.Goto(self.goto_label(it[1])))
                cn.prov(out[-1]).edges = [
                    regions.edge(img, p, q) if p else [p, q] for p in self.source_pcs(it[2], it[1])
                ]
            elif kind == "label":
                out.append(c_ast.Label(self.goto_label(it[1]), c_ast.EmptyStatement()))
            elif kind == "if":
                out += self.convert_if(it)
            elif kind == "switch":
                out += self.convert_switch(it)
                self.prov["switches"].append(self.source_pc(it[3]))
            elif len(it) > 3:
                out.append(self.convert_latched(it))
                self.prov["loops"].append(self.head_pc(it[2]))
            else:
                out.append(self.convert_loop(self.convert(it[1])))
                self.prov["loops"].append(self.head_pc(it[2]))
        return _merge_tails(out)

    def marked(self, out, emit, ops, file=None):
        """Run ``emit``, which appends statements to ``out``, and mark each it appends as
        printing ``ops`` and the operations it builds, else as artifact ``file``."""
        saved, self.printing, n = self.printing, list(ops), len(out)
        emit()
        got, self.printing = self.printing, saved
        for i in range(n, len(out)):
            out[i] = self.mark(out[i], got, file)

    def mark(self, st, ops, file=None):
        """Statement ``st`` marked as printing ``ops`` in the current visit, or as ``file``
        when it prints no instruction's; a jump, a statement already marked and one of
        neither as it is."""
        jump = isinstance(st, JUMPS) and not (isinstance(st, c_ast.Return) and st.expr)
        if cn.rec_of(st) is not None or jump:
            return st
        if any(o.pc for o in ops):
            rec = ("pc", self.f.name, *self.visit, ops)
        elif file and not isinstance(st, c_ast.Return):
            rec = ("tag", file)
        else:
            return st
        cn.prov(st).rec = self.mod.marks.add(rec)
        return st

    def latch_edge(self, u, h):
        """``(condition, copy items)`` of latch ``u``'s back edge to loop head ``h``."""
        for t, c in self.g.succ[u]:
            if t == h or [x for x, _ in self.g.succ.get(t, ())] == [h]:
                return self.g.cond(c), (
                    [i for i in self.g.items[t] if i[0] == "copy"] if t != h else []
                )
        raise AssertionError(u)

    def convert_latched(self, it):
        """A loop whose latch ``it[3]`` is its ``do``/``while`` condition: the latch's
        statements, its back edge's test and copies as one expression."""
        body = _strip_continue(self.convert(it[1]))
        f, copies = self.latch_edge(it[3], it[2])
        pre = _expressions(self.convert([i for i in self.g.items[it[3]] if i[0] == "ops"]))
        post = _expressions(self.convert(copies))
        self.term = self.branch_of(it[3])
        c = self.cond(f, self.f.conds.care).node
        self.term = None
        if pre:
            c = c_ast.ExprList([*pre, c])
        if post:
            c = cn.binop("&&", c, c_ast.ExprList([*post, cn.const(1)]))
        return c_ast.DoWhile(c, cs.braced(body))

    def selector(self, head):
        """The C variable choosing the entry of the cycle selector ``head`` enters."""
        if ("sel", head) not in self.names:
            self.names[("sel", head)] = self.fresh(8)
        return self.names[("sel", head)]

    def halt(self):
        """The return after a ``trap``, which does not return: zero results."""
        rt = self.mod.ret_type(self.f) if self.f.private else "void"
        return cn.ret() if rt == "void" else cn.ret(cn.const(0))

    def tail_site(self, p, t):
        """Record the tail call graph node ``p`` makes into node ``t``: the instruction taking
        the edge into the callee of ``t``'s first call."""
        src = self.source_pc(p, t)
        for o in (o for b in self.blocks(t) for o in b.ops if o.name == "func.call"):
            callee = o.prop("callee")[1:]
            if callee in self.mod.lifted and src:
                entry = self.mod.lifted[callee][0]
                self.prov.setdefault("calls", []).append(
                    [regions.edge(self.mod.mem.image, src, entry)[0], callee]
                )
                return

    def goto_label(self, node):
        """The C label of graph node ``node``, numbered on first use."""
        return self.labels.setdefault(node, f"L{len(self.labels) + 1}")

    def blocks(self, n):
        """The blocks graph node ``n`` holds, those of its condition's effects last."""
        items = self.g.items[n] + [it for fx in self.g.effects.get(n, ()) for it in fx[0]]
        return [it[1] for it in items if it[0] == "ops"]

    def first_pc(self, n):
        """The address of the first instruction graph node ``n`` (or the node its copies
        lead to) runs."""
        while not self.blocks(n) and (len(self.g.succ.get(n, ())) == 1 or n in self.g.selectors):
            n = self.g.selectors[n][0] if n in self.g.selectors else self.g.succ[n][0][0]
        for b in self.blocks(n):
            for o in b.ops:
                if o.pc:
                    return o.pc
        return self.source_pc(n)

    def falls(self, p, y):
        """Whether the edge from graph node ``p`` to ``y`` is the machine code's fall-through
        into the next instruction."""
        img, a = self.mod.mem.image, self.source_pc(p, y)
        if not a:
            return False
        a, b = regions.edge(img, a, self.first_pc(y))
        return b == regions.successors(img, a)[0] and OPS.get(img[a]) != ("JMP", "abs")

    def head_pc(self, h):
        """The address of the instruction loop head ``h`` starts at: its back edge's
        target."""
        q = self.first_pc(h)
        for u, v in sorted(self.g.back):
            p = self.source_pc(u, h) if v == h else None
            if p:
                img = self.mod.mem.image
                return regions.through(img, regions.edge(img, p, q)[1])
        return q

    def source_pcs(self, x, t):
        """The addresses of the instructions branching from graph node ``x`` to node ``t``:
        each branch composed into ``x`` (a short-circuit condition) with an edge toward it."""
        if not self.blocks(x):
            preds = [p for p, es in self.g.succ.items() if any(u == x for u, _ in es)]
            if len(preds) == 1 and preds[0] in self.g.composed:
                x = preds[0]
        if x not in self.g.composed:
            return [self.source_pc(x, t)]
        while not self.blocks(t) and len(self.g.succ.get(t, ())) == 1:
            t = self.g.succ[t][0][0]
        names = {b.name for b in self.blocks(t)}
        pcs = [b.ops[-1].pc for b in self.blocks(x) if names & set(b.ops[-1].succs)]
        return [p for p in pcs if p] or [self.source_pc(x, t)]

    def source_pc(self, x, t=None):
        """The address of the instruction branching from graph node ``x`` (to node ``t``):
        the last of its terminators with an edge to ``t``'s block, else its last."""
        terms = [b.ops[-1] for b in self.blocks(x)]
        terms += [e[0] for it in self.g.items[x] if it[0] in ("copy", "return") for *_, e in it[1]]
        while t is not None and not self.blocks(t) and len(self.g.succ.get(t, ())) == 1:
            t = self.g.succ[t][0][0]
        name = self.blocks(t)[0].name if t is not None and self.blocks(t) else None
        hit = [o for o in terms if name in o.succs] or terms
        for o in reversed(hit[-1].parent.ops if hit else ()):
            if o.pc:
                return o.pc
        return None

    def convert_if(self, it):
        """An ``if``; one whose then-branch jumps continues with its else-branch after it."""
        x = it[5]
        if x in self.g.composed and x not in self.prov.setdefault("composed", set()):
            self.prov["composed"].add(x)
            self.prov["conditions"].append([b.ops[-1].pc for b in self.blocks(x)])
        visit = self.visit
        (then, els), tail = _shared_jump([self.convert(it[3]), self.convert(it[4])])
        self.visit = visit
        f = it[1]
        if not then and not els and x not in self.g.effects:
            return tail
        if not then and els:
            then, els, f = els, then, ~f
        fx = self.g.effects.get(x)
        self.term = self.branch_of(x)
        c, n = self.cond(f, it[2], fx), self.cond(~f, it[2], fx)
        self.term = None
        if c.lo == c.hi and not fx:
            # A condition of one value (a check its load makes, :meth:`fusion`): its branch.
            return [*(then if c.lo else els), *tail]
        if not then and not els:
            return [cn.void(c.node), *tail]
        if els and cs.ends_in_jump(then):
            return [_if(c.node, then, [], n.node), *els, *tail]
        return [_if(c.node, then, els, n.node), *tail]

    def convert_switch(self, it):
        """A ``switch`` over a selector family, each arm leaving it by its own jump."""
        fam = it[1]
        saved, self.printing = self.printing, []
        sel = clean(self.ex(fam), self.f.width(fam)).node
        got, self.printing = self.printing, saved
        got = [o for o in (self.branch_of(it[3]),) if o is not None] + got
        if any(o.pc for o in got):
            sel = cn.marked(sel, self.mod.marks.add(("pc", self.f.name, *self.visit, got)))
        labels = [[None if k is None else self.label(fam, k).node for k in ks] for ks, _ in it[2]]
        bodies, tail = [self.convert(b) for _, b in it[2]], []
        if any(None in ks for ks in labels):
            bodies, tail = _shared_jump(bodies)
        cases = []
        for ks, body in zip(labels, bodies):
            body = body if cs.ends_in_jump(body) else [*body, c_ast.Break()]
            *first, last = ks
            cases += [c_ast.Case(k, []) if k is not None else c_ast.Default([]) for k in first]
            arm = [cs.braced(body)]
            cases.append(c_ast.Case(last, arm) if last is not None else c_ast.Default(arm))
        return [c_ast.Switch(sel, cs.braced(cases)), *tail]

    @staticmethod
    def convert_loop(body):
        """A loop as ``while`` (an exit test first), ``do``/``while`` (an exit test last and
        no other ``continue``) or ``for (;;)``."""
        body = _strip_continue(body)

        def exits(st, jump):
            if not isinstance(st, c_ast.If) or cs.block(st.iffalse):
                return False
            then = cs.block(st.iftrue)
            return len(then) == 1 and isinstance(then[0], jump)

        if body and exits(body[0], c_ast.Break):
            return c_ast.While(_negation(body[0]), cs.braced(body[1:]))
        if body and exits(body[-1], c_ast.Break) and not _continues(body[:-1]):
            return c_ast.DoWhile(_negation(body[-1]), cs.braced(body[:-1]))
        if len(body) > 1 and isinstance(body[-1], c_ast.Break) and exits(body[-2], c_ast.Continue):
            if not _continues(body[:-2]):
                return c_ast.DoWhile(body[-2].cond, cs.braced(body[:-2]))
        return c_ast.For(None, None, None, cs.braced(body))

    def head(self):
        """The C declaration of the function and its parameters' names."""
        f = self.f
        if not f.private:
            arg = f.blocks[0].args[0][0]
            self.names[self.classes.get(arg, arg)] = "a"
            return cn.function(self.name, "void", [("a", "uint8_t")]), ["a"]
        params = [f.blocks[0].args[i][0] for i in f.live_args]
        names = [self.var_of(p, f.width(p)) for p in params]
        ps = [(n, self.types.pop(n)) for n in names]
        return cn.function(self.name, self.mod.ret_type(f), ps, ("static",)), names

    def definition(self):
        """The C definitions of the function: wayward-eagle's pieces of it, their
        prototypes first."""
        self.count()
        self.plan()
        self.classes = ssa.Coalescer(self).classes()
        head, _ = self.head()
        body = self.convert(self.body)
        if len(self.f.live_res) != 1 or not self.f.private:
            while body and isinstance(body[-1], c_ast.Return) and body[-1].expr is None:
                body.pop()
        body = _drop_unread(body)
        structured = _goto_edges(body)
        locals_ = [cn.decl(v, self.types[v]) for v in sorted(self.types, key=cs.order)]
        self.head_decl = head
        got = reduce_function(cn.define(head, [*locals_, *body]), NORETURN, cn.type_decl("uint8_t"))
        self.prov["pieces"] = [x.decl.name for x in got if x.decl.name != self.name]
        self.prov["gotos"] = _goto_edges(got)
        left = Counter(map(tuple, self.prov["gotos"]))
        for e in structured:
            if left[tuple(e)]:
                left[tuple(e)] -= 1
            else:
                self.prov["reduced"].append(e)
        for fd in got:
            _zeroed(fd.body.block_items)
        if not self.f.private:
            got[-1].body.block_items[:0] = self.mod.guards.get(self.name, [])
        tag = self.mod.marks.add
        for fd in got:
            cn.prov(fd).rec, cn.prov(fd.body).rec = tag(("tag", "frame")), tag(("tag", "frame"))
        return [*prototypes(got), *got]


#: The jumps a statement list may end with.
JUMPS = (c_ast.Return, c_ast.Break, c_ast.Continue, c_ast.Goto, c_ast.AttributedStatement)
#: The functions that never return.
NORETURN = frozenset({"trap"})


def _if(cond, then, els, neg):
    """``if (cond) { then } else { els }``, ``neg`` its condition negated."""
    st = cs.iff(cond, then, els)
    cn.prov(st).neg = neg
    return st


def _negation(st):
    """The negation of ``if`` statement ``st``'s condition."""
    neg = st.coord.neg if isinstance(st.coord, cn.Prov) else None
    return neg if neg is not None else cs.negate(st.cond)


def _expressions(items):
    """Statements ``items`` (assignments and calls) as the expressions of a comma list."""
    for st in items:
        assert isinstance(st, (c_ast.Assignment, c_ast.FuncCall)), st
    return list(items)


def _zeroed(items):
    """Initialize to zero, in place, each declaration of statement list ``items`` and those
    nested in it without an initializer whose variable may be read before it is set."""
    for i, st in enumerate(items):
        if (
            isinstance(st, c_ast.Decl)
            and st.init is None
            and not cs.assigned_first(items[i + 1 :], st.name)
        ):
            st.init = cn.const(0)
        for sub in cs.children(st):
            _zeroed(sub)


def _goto_edges(nodes):
    """The machine-code edges of the ``goto`` statements of ``nodes``."""
    tops = nodes if isinstance(nodes, list) else [nodes]
    return [
        e
        for top in tops
        for n in cn.walk(top)
        if isinstance(n, c_ast.Goto) and isinstance(n.coord, cn.Prov) and n.coord.edges
        for e in n.coord.edges
    ]


def _drop_unread(items):
    """``items`` without the pure assignments (:class:`cnodes.Prov`) to variables nothing
    reads, nor ``if`` statements left empty, until none is left."""
    while True:
        read = cs.reads_in(items)
        out = _drop(items, read)
        if cn.key(out) == cn.key(items):
            return out
        items = out


def _drop(items, read):
    out = []
    for st in items:
        if isinstance(st, c_ast.Assignment) and isinstance(st.coord, cn.Prov) and st.coord.pure:
            if st.lvalue.name not in read:
                continue
        if isinstance(st, c_ast.If):
            then, els = _drop(cs.block(st.iftrue), read), _drop(cs.block(st.iffalse), read)
            if not then and not els:
                continue
            st = cs.rebuild(st, [then, els])
        elif cs.children(st):
            st = cs.rebuild(st, [_drop(sub, read) for sub in cs.children(st)])
        out.append(st)
    return out


def _shared_jump(arms):
    """``(arms, [jump])`` without the jump every arm of a statement ends with, which then
    follows the statement once; else ``(arms, [])``."""
    last = arms[0][-1] if arms[0] else None
    if not isinstance(last, JUMPS) or any(not a or cn.key(a[-1]) != cn.key(last) for a in arms):
        return arms, []
    return [a[:-1] for a in arms], [last]


def _merge_tails(items):
    """``items`` where an ``if`` without ``else`` whose branch ends with the statements that
    follow it (which jump away) ends before them."""
    out = []
    for st in reversed(items):
        n = len(out)
        if isinstance(st, c_ast.If) and not cs.block(st.iffalse) and n:
            then = cs.block(st.iftrue)
            if cn.key(then[-n:]) == cn.key(out[::-1]):
                if len(then) == n:
                    continue
                st = cs.rebuild(st, [then[:-n], []])
        out.append(st)
    return out[::-1]


def _strip_continue(body):
    """``body`` without ``continue`` statements in its tail position."""
    if not body:
        return body
    last = body[-1]
    if isinstance(last, c_ast.Continue):
        return _strip_continue(body[:-1])
    if isinstance(last, c_ast.If):
        arms = [_strip_continue(cs.block(last.iftrue)), _strip_continue(cs.block(last.iffalse))]
        return body[:-1] + [cs.rebuild(last, arms)]
    return body


def _continues(items):
    """Whether ``items`` hold a ``continue`` of the enclosing loop."""
    for st in items:
        if isinstance(st, c_ast.Continue):
            return True
        if isinstance(st, (c_ast.If, c_ast.Switch)) and any(map(_continues, cs.children(st))):
            return True
    return False


def fold(defs):
    """``([[node]], {name: final name})``: ``defs`` ``[(name, [node])]`` with each set of
    functions whose definitions are the same up to their own name and the functions they call
    (merged so far) kept once, until none is left, then renumbered ``f1, f2, ...``."""
    nodes = dict(defs)
    rep = {n: n for n in nodes}
    while True:
        groups = {}
        for n in (n for n, _ in defs if rep[n] == n):
            names = {m: rep[m] for m in rep if _numbered(m)}
            names.update({n: "f0"} if _numbered(n) else {})
            groups.setdefault(cn.key(nodes[n], names), []).append(n)
        merged = [(v[0], x) for v in groups.values() for x in v[1:]]
        for keep, x in merged:
            rep[x] = keep
        if not merged:
            break
    kept = sorted((n for n in nodes if rep[n] == n and _numbered(n)), key=lambda n: int(n[1:]))
    name = {n: f"f{i}" for i, n in enumerate(kept, 1)}
    name.update({n: name.get(rep[n], rep[n]) for n in nodes})
    keep = [xs for n, xs in defs if rep[n] == n]
    cn.rename([x for xs in keep for x in xs], name)
    return keep, name


def _outside(j, off, k):
    """Whether offset ``off`` into object ``j`` lies outside it, or ``k`` bytes past it do."""
    lim = cn.ident(f"CGEN_OBJ{j}_LIM")
    past = cn.binop(">", off, lim)
    if isinstance(k, int) and not k:
        return past
    k = cn.const(k) if isinstance(k, int) else k
    return cn.binop("||", past, cn.binop("<", cn.binop("-", lim, off), k))


def _count(array, ctype, body):
    """``for (ctype i = 0; i < sizeof array / sizeof *array; ++i) { body }``."""
    n = cn.binop(
        "/",
        c_ast.UnaryOp("sizeof", cn.ident(array)),
        c_ast.UnaryOp("sizeof", c_ast.UnaryOp("*", cn.ident(array))),
    )
    init = c_ast.DeclList([cn.decl("i", ctype, cn.const(0))])
    return c_ast.For(
        init, cn.binop("<", cn.ident("i"), n), c_ast.UnaryOp("++", cn.ident("i")), cs.braced(body)
    )


def _element(name, a):
    """``name[(uint16_t)(a - name_ORG)]``."""
    return cn.index(name, cn.cast("uint16_t", cn.binop("-", a, cn.ident(name + "_ORG"))))


def _numbered(n):
    """Whether ``n`` is a lifted function's name, ``f`` and its number."""
    return n[:1] == "f" and n[1:].isdigit()


def _copies(items, pcs):
    """Provenance ``items`` (addresses, or lists of them) of an instance function, with each
    copy's instructions for those ``pcs`` (``{pc: [pc per copy]}``) maps."""
    if not pcs:
        return list(items)

    def at(x, k):
        if isinstance(x, list):
            got = [at(y, k) for y in x]
            return None if None in got else got
        row = pcs.get(x)
        return x if row is None else (row[k] or None)

    n = len(next(iter(pcs.values())))
    out = []
    for k in range(n):
        out += [y for y in (at(x, k) for x in items) if y is not None and y not in out]
    return out


class Module:  # pylint: disable=too-many-instance-attributes
    """The functions of the optimized module and the memory they share."""

    #: The facts the intervals read besides the operations: setting one forgets them.
    FACTS = frozenset(
        "mem sites free folded after scope sets regs reach proven bounded addr_sets data "
        "copy_pcs fixed lifted regfile symbols by_name".split()
    )

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in Module.FACTS:
            self.forget()

    def forget(self):
        """Drop every interval found (the functions' and their results'): each is found
        again from the operations and facts as they are."""
        self.results_ivs = {}
        for f in self.__dict__.get("funcs", ()):
            f.forget()

    def __init__(self, top, mem):
        self.results_ivs = {}
        self.mem, self.used, self.helpers, self.regions_used = mem, set(), set(), set()
        self.addr_sets, self.data, self.spans_used = {}, {}, set()
        #: The readers and writers of data blocks of several regions, by kind and places.
        self.block_fns = {}
        self.traps, self.funcs, self.mains, self.structs = set(), [], {}, {}
        self.marks = marks.Marks()
        self.order, self.edges, self.ids, self.entries = [], {}, new_ids(), []
        self.sites, self.made, self.lifted, self.prov = {}, Counter(), {}, {}
        self.copy_pcs, self.sets, self.kpos, self.tables = {}, {}, None, {}
        self.shared = set()
        #: The functions the cognitive-complexity reduction cut from the printed ones.
        self.pieces = set()
        #: The bytes init stores (``postinit.Init.mask``), and those no store may write
        #: (:meth:`unwritten`, None until found or when a store of unknown address may).
        self.init_stored, self.free = np.zeros(0x10000, dtype=bool), None
        self.reach, self.proven, self.checked, self.konsts = {}, {}, set(), {}
        #: The pointer objects ``[(lo, hi)]`` the lift checks, and those the ``.c`` names.
        self.ptr_objects, self.objs_used, self.ptr_parts = [], set(), {}
        #: Per object of a pointer's check after its definition, ``(block, lo, k)``.
        self.ptr_slack = {}
        #: The block readers checking their address into an object (:meth:`checked_fn`).
        self.checked_fns = set()
        #: The ``(region, object)`` pairs of the region readers checking their address first.
        self.guarded, self.writes, self.words = set(), {}, set()
        self.setfns = set()
        #: The block arrays read through their unchecked reader ``Bbr(base, index)``.
        self.block_readers = set()
        #: The held spans whose bounds-checked readers find promoted cells' variables.
        self.var_spans, self.var_at = set(), []
        self.offsets, self.insn = {}, np.zeros(0x10000, dtype=bool)
        self.inert, self.regs, self.regfile = set(), {}, []
        self.bounded, self.cells, self.state = {}, None, State()
        #: Per opcode cell whose values the lift bounds, its values, ascending.
        self.opcodes = {}
        #: Per entry name, the C lines that start it (the host's check of its arguments).
        self.guards = {}
        #: Runs ``(lo, hi)`` (inclusive) held in regions whatever reads them (init's sources).
        self.held_runs = []
        #: Per function name, the lift whose facts (:meth:`fx`) it reads, when not this one's.
        self.scope = {}
        #: The instruction bytes each lift decodes as constant (opcodes intervals are read by).
        self.fixed = mem.code
        #: ``(mask, image, operands)``: the bytes init leaves constant (``Program.fixed``),
        #: the values it leaves in them, and those of them in the instructions planned.
        none = np.zeros(0x10000, dtype=bool)
        self.folded = (none, mem.image, none)
        #: The functions of the other entries' lift, which read them as constants, when a
        #: translated init's are others (None: every function).
        self.after = None
        #: ``[(pc, value)]``: the constants of the tests of the instructions at ``pc`` the
        #: ``.h`` names, from bytes init leaves constant (``@G``'s elements).
        self.symbols = []
        self.load(top)

    def load(self, top):
        """Take the functions of module ``top``."""
        self.top = top
        self.funcs = [
            Func(o, self)
            for o in top.regions[0][0].ops
            if o.name == "func.func" and o.regions[0] and o.regions[0][0].ops
        ]
        self.by_name = {f.name: f for f in self.funcs}

    def calls(self):
        """Find each lifted function's call sites (:attr:`sites`) and a copy's instance index
        (:meth:`Func.instance`)."""
        self.sites = {f.name: [] for f in self.funcs}
        for f in self.funcs:
            for o in f.walk():
                if o.name == "func.call" and o.prop("callee")[1:] in self.sites:
                    self.sites[o.prop("callee")[1:]].append((f, o))
        for f in self.funcs if self.kpos is not None else ():
            if self.lifted.get(f.name, (0, ()))[1]:
                f.instance(self.kpos)

    def combine(self, prefix):
        """Take the functions after LLVM's passes (:func:`llvmopt.combined`, its tools under
        ``prefix``), the results they forward read as the arguments first (:meth:`forward`)
        and the truth values constant (:meth:`Func.fold_flags`), the intervals of loads of
        opcode cells (:meth:`Func.code_iv`) stated; then the block arguments their blocks
        compute again (:func:`joins.rematerialise`)."""
        self.forward()
        self.calls()
        for f in self.funcs:
            f.fold_flags()
            f.fold_known_addresses()
        ranges = {
            f.name: [f.code_iv(o) for o in f.walk() if o.name == "memref.load"] for f in self.funcs
        }
        self.load(mlir.parse(llvmopt.combined(mlir.generic(self.top), prefix, ranges)))
        self.writes = {}
        self.calls()
        live = self.mark()
        done = [joins.rematerialise(f, live[f.name]) for f in self.funcs]
        if any(done):
            self.load(mlir.parse(mlir.run(prefix, list(llvmopt.TIDY), mlir.generic(self.top))))
        self.writes = {}

    def copies_of(self, name, pc):
        """The instruction PCs of every copy of instruction ``pc`` of function ``name``."""
        return [q for q in self.copy_pcs.get(name, {}).get(pc, [pc]) if q is not None]

    def addr_set(self, name, o, calls=True):
        """The address set of access ``o`` of function ``name`` (a load's, else a write's,
        :func:`is_write`; None for an I/O call unless ``calls``)."""
        if not calls and o.name == "func.call":
            return None
        return self.fx(name).addr_sets.get((o.pc, is_write(o)))

    def block(self, f, o):
        """The runs ``[(lo, hi)]`` (``hi`` exclusive) of the data block access ``o`` of function
        ``f`` may reach (a pointer from the program's data the memory map checks into it: its
        runs inside the interval of its address), else None."""
        if o.name not in ("memref.load", "memref.store"):
            return None
        runs = self.fx(f.name).data.get((o.pc, is_write(o)))
        if not runs:
            return None
        lo, hi = f.addr_iv(o)
        return [(max(a, lo), min(b, hi + 1)) for a, b in runs if a <= hi and lo < b] or None

    def fx(self, name):
        """The facts of the lift function ``name`` comes from: ``sets``, ``regs``, ``reach``,
        ``proven``, ``bounded`` and ``addr_sets``."""
        return self.scope.get(name, self)

    def unread_stores(self):
        """Mark the stores no load reads back (``f.unread``): every byte they may write lies
        outside the constant bytes of the code span (a store there is a code check) and
        outside the addresses any load of the module may read, over every call, and the
        sources of init's copies. What they hold is never observed: a return address a callee
        pulls into a pointer whose reads are forwarded, a vector the lift resolved its
        transfers through, the operand cells of a computed jump that switches on its index."""
        read = masks.mask(self.held_runs, True)
        stores = []
        for f in self.funcs:
            for o, (lo, hi), per in f.accesses():
                if o.name == "memref.load":
                    masks.mask(per or [(lo, hi)], True, out=read)
                elif o.name == "memref.store":
                    stores.append((f, o, per or [(lo, hi)]))
        lo, hi = self.mem.code_span
        read[lo:hi] |= True if self.cells is None else ~self.cells[lo:hi]
        for f, o, ivs in stores:
            if not any(read[x : y + 1].any() for x, y in ivs):
                f.unread.add(id(o))

    def mark(self):
        """``{function name: values something needs}``: the roots' operands, and what each
        needed value is computed from through block arguments, parameters (the call sites'
        arguments) and results (the callee's returned operands); sets each function's used
        parameters and results."""
        sites = self.sites
        live, work = {f.name: set() for f in self.funcs}, []

        def need(f, v):
            if v not in live[f.name]:
                live[f.name].add(v)
                work.append((f, v))

        for f in self.funcs:
            f.live_args, f.live_res = [], []
            for o in f.walk():
                if o.name in ("cf.cond_br", "cf.switch") or f.root(o):
                    for a in o.args[:1] if o.name.startswith("cf.") else o.args:
                        need(f, a)
        while work:
            f, v = work.pop()
            d = f.defs.get(v)
            if d is None and v in f.arg_src:
                for o, i, _ in f.arg_src[v]:
                    need(f, o.args[i])
            elif d is None:
                i = [a for a, _ in f.blocks[0].args].index(v)
                f.live_args.append(i)
                for g, o in sites.get(f.name, ()):
                    need(g, o.args[i])
            elif d.name == "func.call" and d.prop("callee")[1:] in sites:
                h, j = self.by_name[d.prop("callee")[1:]], d.res.index(v)
                if j not in h.live_res:
                    h.live_res.append(j)
                    for ret in (o for o in h.walk() if o.name == "func.return"):
                        need(h, ret.args[j])
            else:
                for a in d.args:
                    need(f, a)
        for f in self.funcs:
            f.live_args.sort()
            f.live_res = sorted(f.live_res) if f.private else []
        return live

    def prune(self):
        """Keep the values something observable needs, over the whole module: a store, an
        I/O access, a trap or a branch needs its operands, a used result of a lifted function
        its returned operands, a used parameter its call sites' arguments. Calls pass only the
        parameters used, functions return only the results used, the rest is dropped. The
        bytes no store writes (:meth:`unwritten`) are found first."""
        self.unwritten()
        self.calls()
        for f in self.funcs:
            f.kfold()
        self.unread_stores()
        live = self.mark()
        carried = [f.carry16(live[f.name], self.by_name) for f in self.funcs]
        if any(carried):
            live = self.mark()
        self.settle(live)
        self.writes = {}
        paired = [f.pairs() for f in self.funcs]
        if any(paired):
            self.settle(self.mark())

    def written(self, name):
        """``[(lo, hi)]``: the addresses the stores of function ``name`` and of what it calls
        may write; none for a function that is not one of the module's."""
        if name in self.writes:
            return self.writes[name]
        seen, work, out = set(), [name], []
        while work:
            f = self.by_name.get(work.pop())
            if f is None or f.name in seen:
                continue
            seen.add(f.name)
            for o in f.walk():
                if id(o) in f.dropped:
                    continue
                if o.name == "memref.store" and o.args[-2] in f.globals:
                    out.append(f.addr_iv(o))
                elif o.name == "func.call":
                    work.append(o.prop("callee")[1:])
        self.writes[name] = out
        return out

    def settle(self, live):
        """Drop what ``live`` does not hold in every function (:meth:`Func.settle`), again
        while that leaves another function with no effect."""
        while True:
            for f in self.funcs:
                f.settle(live[f.name], self.by_name)
            inert = {f.name for f in self.funcs if f.private and f.inert()}
            if inert <= self.inert:
                break
            self.inert |= inert

    def forward(self):
        """Read each result a lifted function returns unchanged from a parameter (on every
        return, through block arguments and the calls that forward theirs) as the argument
        its call passes: the result is then dead, as a register the callee leaves alone."""
        through = {}
        changed = True
        while changed:
            changed = False
            for f in self.funcs:
                if not f.private:
                    continue
                rets = [o for o in f.walk() if o.name == "func.return"]
                params = [a for a, _ in f.blocks[0].args]
                have = through.setdefault(f.name, {})
                for j, t in enumerate(f.results()):
                    ps = {f.param_of(r.args[j], through, set()) for r in rets}
                    p = ps.pop() if len(ps) == 1 else None
                    if p is not None and j not in have and f.types[params[p]] == t:
                        have[j] = p
                        changed = True
        for f in self.funcs:
            sub = {}
            for o in f.walk():
                callee = o.prop("callee")[1:] if o.name == "func.call" else None
                for j, p in through.get(callee, {}).items():
                    sub[o.res[j]] = o.args[p]
            if not sub:
                continue
            f.substitute(sub)
            f.reindex()

    def reg_iv(self, name, pcs, j):
        """The hull of the values register-file position ``j`` (``A``, ``X`` or ``Y``) holds at
        every instruction start of ``pcs`` on every run of function ``name``'s lift
        (:attr:`regs`), or None."""
        r = self.regfile[j] if j < len(self.regfile) else None
        regs = self.fx(name).regs
        sets = [regs.get(pc) for pc in pcs] if r in (0, 1, 2) else [None]
        if not sets or None in sets or not all(x[r] for x in sets):
            return None
        return min(x[r][0] for x in sets), max(x[r][-1] for x in sets)

    def loaded_iv(self, f, o):
        """The interval of the byte load ``o`` of function ``f`` reads where its instruction
        (every copy's, constant code) loads it into a register (``LDA``, ``LDX``, ``LDY``,
        ``LAX``): that register's values at the next instruction start (:meth:`reg_iv`), else
        None."""
        pcs = self.copies_of(f.name, o.pc)
        ops = {OPS.get(self.mem.image[q], ("", "imm")) for q in pcs}
        if len(ops) != 1 or not all(self.fixed[q] for q in pcs):
            return None
        mn, mode = ops.pop()
        r, n = LOADS.get(mn) if mode != "imm" else None, MODE_LEN[mode]
        if r not in self.regfile:
            return None
        return self.reg_iv(f.name, [(q + n) & 0xFFFF for q in pcs], self.regfile.index(r))

    def entry_iv(self, name, j):
        """The interval of register-file position ``j`` at the entry of lifted function
        ``name`` (every copy's), from the lift's register sets, or None."""
        pc, copies = self.lifted.get(name, (None, ()))
        return None if pc is None else self.reg_iv(name, [pc, *copies], j)

    def result_iv(self, name, j):
        """The interval of result ``j`` of lifted function ``name``: the hull of what each of
        its returns gives, the lift's register set where it returns at an ``RTS``, else the
        returned value's interval."""
        f = self.by_name.get(name)
        if f is None or not f.private:
            return None
        key = (name, j)
        if key in self.results_ivs:
            return self.results_ivs[key]
        self.results_ivs[key] = None
        out = []
        for o in (o for o in f.walk() if o.name == "func.return"):
            pcs = self.copies_of(name, o.pc)
            rts = pcs and all(self.fixed[q] and self.mem.image[q] in (0x40, 0x60) for q in pcs)
            r = self.reg_iv(name, pcs, j) if rts else None
            d = f.defs.get(o.args[j])
            out.append(r if r or d is not None and d.name == "ub.poison" else f.iv0(o.args[j]))
        out = [r for r in out if r is not None]
        self.results_ivs[key] = r = (
            (min(lo for lo, _ in out), max(hi for _, hi in out)) if out else None
        )
        return r

    def run_of(self, f, o):
        """The run ``(lo, hi)`` (inclusive) of data bytes store ``o`` of function ``f`` writes
        when the lift bounds it to the array holding its base (every copy's), else None."""
        pcs = self.copies_of(f.name, o.pc)
        runs = [self.fx(f.name).bounded.get(q) for q in pcs]
        if o.name != "memref.store" or not runs or None in runs:
            return None
        return min(r[0] for r in runs), max(r[1] for r in runs) - 1

    def reach_of(self, f, o, hulls="reach"):
        """The lift's address hull of pointer load ``o`` of function ``f`` and its base's,
        over every copy's instruction (from ``hulls`` of its lift, :attr:`reach` or :attr:`proven`),
        or None."""
        pcs = [q for q in self.copy_pcs.get(f.name, {}).get(o.pc, [o.pc]) if q]
        hulls = [getattr(self.fx(f.name), hulls).get(q) for q in pcs]
        if not hulls or None in hulls:
            return None
        return tuple(m(h[i] for h in hulls) for i, m in enumerate((min, max, min, max)))

    def touched(self, f, o, lo, hi, per):
        """``([(lo, hi)], why)``: the addresses access ``o`` of function ``f`` (address interval
        ``(lo, hi)``, per copy ``per``) may touch, and the :data:`state.WHY` code of its kind."""
        blk = self.block(f, o)
        if blk:
            return [(a, b - 1) for a, b in blk], POINTER
        run = self.run_of(f, o)
        if run is not None:
            return [(max(lo, run[0]), min(hi, run[1]))], INDEXED
        lo, hi = f.proven_iv(o) or (lo, hi)
        if per and all(y - x < regions.BOUNDED for x, y in per):
            return per, INDEXED
        if hi - lo < regions.BOUNDED:
            return [(lo, hi)], INDEXED
        aset = self.addr_set(f.name, o)
        if aset:
            return [(a, b - 1) for a, b in aset], POINTER
        hull = self.reach_of(f, o) if o.name == "memref.load" else None
        return ([hull[:2]], POINTER) if hull is not None else ([(0, 0xFFFF)], UNKNOWN)

    def mapped(self, f, o, runs, kind):
        """Whether access ``o`` of function ``f`` (touching ``runs``, of :data:`state.WHY`
        kind ``kind``) leaves the cells it may reach to promotion: a load through a pointer the
        lift bounds by a hull, or a load or store of unknown address, of code span cells only,
        goes through a reader or ``wr`` that finds a promoted cell's variable."""
        hull = (
            kind == POINTER
            and o.name == "memref.load"
            and not self.addr_set(f.name, o)
            and self.reach_of(f, o) is not None
        )
        if o.name == "func.call" or not (hull or kind == UNKNOWN):
            return False
        lo, hi = self.mem.code_span
        hit = np.concatenate([np.flatnonzero(self.cells[x : y + 1]) + x for x, y in runs])
        return bool(hit.size) and lo <= hit.min() and hit.max() < hi

    def promote(self):
        """Classify the code cells (:attr:`cells`) by the address sets of every access
        (:func:`state.classify`) and mark each access of a promoted cell (``f.promoted``): a
        scalar's address, or a field's struct, field and element (a value of the copy index)."""
        why, found = np.zeros(0x10000, dtype=np.int8), []
        seen = np.zeros(0x10000, dtype=bool)
        for lo, hi in self.held_runs:
            why[lo : hi + 1] = INIT
        cells = self.objects()
        for f in self.funcs:
            for o, (lo, hi), per in f.accesses():
                if id(o) in f.dropped or o.name == "func.call" and self.mem.is_volatile(lo, hi):
                    continue
                one = [x for x, y in per if x == y] if per else [lo] if lo == hi else []
                if o.name != "func.call" and one and len(one) == len(per or one):
                    if cells[one].all():
                        found.append((f, o, one, None))
                        continue
                step = None if per or o.name == "func.call" else f.stepped_access(o)
                if step is None and not per and (sa := f.strided_access(o)) is not None:
                    step = (sa[0], ("div", *sa[1:]))
                if step is not None and cells[step[0]].all():
                    found.append((f, o, step[0], step[1]))
                    continue
                if step is not None:
                    sel = why[step[0]]
                    why[step[0]] = np.where(sel == 0, INDEXED, sel)
                    continue
                runs, kind = self.touched(f, o, lo, hi, per)
                if self.mapped(f, o, runs, kind):
                    f.aware.add(id(o))
                    if o.name == "memref.load":
                        masks.mask(runs, True, out=seen)
                    continue
                for x, y in runs:
                    sel = why[x : y + 1]
                    sel[sel == 0] = kind
        stepped = [a for _, _, a, v in found if v is not None]
        st = classify(cells, [a for _, _, a, _ in found], why, stepped)
        self.state = st = _read_only(st, found, seen)
        self.var_at = self.var_cases()
        made, gone = {}, set(st.unread)
        for f, o, one, v in found:
            if len(set(one)) == 1 and one[0] in st.at:
                p = st.at[one[0]]
                f.promoted[id(o)] = ("s", one[0]) if p is None else ("v", p[0], p[2], p[1])
                continue
            place = st.field_of(one) if len(set(one)) > 1 else None
            if place is None:
                if gone.issuperset(one):
                    f.dropped.add(id(o))
                continue
            s, d, els = place
            key = (f.name, v, int(els[0])) if v is not None else (f.name, tuple(els.tolist()))
            if isinstance(v, tuple):
                e = f.divided(o, *v[1:], int(els[0]))
            elif key in made:
                e = made[key]
            elif v is not None:
                e = f.plus(f.element(v), int(els[0]))
            elif (els == f.kvals).all():
                e = f.kparam
            else:
                new, offs = {}, tuple(els.tolist())
                pre = f"%q{len(f.defs)}."
                e = f.kop(new, "arith.kfam", [f.kparam], "i8", o.pc, offs=offs, prefix=pre)
                f.prepend(new)
            made[key] = e
            f.promoted[id(o)] = ("v", s, d, e)

    def objects(self):
        """The promotion candidates: the code cells and the other variable bytes of the code
        span (:attr:`cells`), and the data bytes outside it (not volatile, the stack page or a
        table) an access reaches at a stride of at least 2 for each value of its index
        (:meth:`CFunc.stepped_access`, :meth:`CFunc.strided_access`): records of objects."""
        out, (lo, hi) = self.cells.copy(), self.mem.code_span
        for f in self.funcs:
            for o, _, per in f.accesses():
                if per or o.name == "func.call" or id(o) in f.dropped:
                    continue
                step = f.stepped_access(o) or f.strided_access(o)
                addrs = np.array(sorted(set(step[0])) if step else [], dtype=np.int64)
                if len(addrs) < 2 or np.diff(addrs).min() < 2:
                    continue
                addrs = addrs[(addrs < lo) | (addrs >= hi)]
                keep = ~self.mem.vol[addrs] & ~self.mem.constant[addrs]
                keep &= (addrs < 0x100) | (addrs >= 0x200)
                out[addrs[keep]] = True
        return out

    def var_name(self, a):
        """The name of scalar ``a``: by its offset from the code span's origin (or the loaded
        image's, else its address), so relocating the program keeps it."""
        lo, hi = self.mem.code_span
        if lo <= a < hi:
            return f"S_{a - lo:X}"
        if self.mem.loaded[0] <= a < self.mem.loaded[1]:
            return f"S_I{a - self.mem.loaded[0]:X}"
        return f"S_A{a:04X}"

    def var_cases(self):
        """``[(offset, lvalue)]`` of the promoted cells of the code span, by offset from its
        origin: what ``cgen_var`` finds for an access :meth:`mapped` leaves to them."""
        lo, hi = self.mem.code_span
        return [(a - lo, self.var_ref(a)) for a in sorted(self.state.at) if lo <= a < hi]

    def var_ref(self, a):
        """The C lvalue of promoted cell ``a``, or None."""
        if a not in self.state.at:
            return None
        p = self.state.at[a]
        return self.var_name(a) if p is None else f"V{p[0]}_f{p[2]:02X}[{p[1]}]"

    def enum_of(self, a):
        """``{value: name}`` (:func:`enum_names`) of opcode cell ``a`` when promoted to a
        scalar and the lift bounds its values (:attr:`opcodes`), else None."""
        if a is None or a not in self.opcodes or self.state.at.get(a, 0) is not None:
            return None
        return enum_names(self.var_name(a), self.opcodes[a])

    def state_c(self):
        """The ``.c``'s definitions of the promoted state, initialized from the ``.h``: each
        field of a struct is an array over its copies; an opcode cell's states are named."""
        out = []
        for names in filter(None, map(self.enum_of, self.state.scalars)):
            vals = [c_ast.Enumerator(n, cn.const(v)) for v, n in names.items()]
            out.append(
                c_ast.Decl(
                    None,
                    [],
                    None,
                    [],
                    [],
                    c_ast.Enum(None, c_ast.EnumeratorList(vals), None),
                    None,
                    None,
                )
            )
        out += [
            cn.decl(n, "uint8_t", cn.ident(n + "_INIT"))
            for n in map(self.var_name, self.state.scalars)
        ]
        for s, (bases, fields) in enumerate(self.state.structs):
            out += [
                cn.decl(
                    f"V{s}_f{d:02X}",
                    "uint8_t",
                    cn.inits(f"V{s}_f{d:02X}_INIT"),
                    dim=cn.const(len(bases)),
                )
                for d in fields
            ]
        return out

    def state_h(self):
        """The ``.h``'s initial values of the promoted state."""
        img = self.mem.init
        out = [f"#define {self.var_name(a)}_INIT 0x{img[a]:02X}" for a in self.state.scalars]
        for s, (bases, fields) in enumerate(self.state.structs):
            out += [
                f"#define V{s}_f{d:02X}_INIT {cn.hexes(img[(int(b) + d) & 0xFFFF] for b in bases)}"
                for d in fields
            ]
        return out

    def partition(self):
        """Partition memory by the accesses of every function but those of promoted cells
        (:meth:`promote`): a pointer load of unknown address whose hull the lift bounds reads
        the run :meth:`Memory.pointer_run` gives, unchecked where the hull the lift proves
        lies in it (``f.sure``)."""
        if self.cells is not None:
            self.promote()
        ranges, stores, unknown, joined, ptrs, writes = [], set(), False, [], [], []
        bound = []
        for f in self.funcs:
            f.wide, f.sure = {}, set()
            for o, (lo, hi), per in f.accesses():
                if id(o) in f.dropped or id(o) in f.promoted:
                    continue
                # An access into the data block touches its runs, none of the code between.
                blk = self.block(f, o)
                if blk:
                    step = regions.BOUNDED
                    rs = [(x, min(x + step, b) - 1) for a, b in blk for x in range(a, b, step)]
                    ranges.extend(rs)
                    if is_write(o):
                        writes.extend(rs)
                    continue
                run = self.run_of(f, o)
                if run is not None:
                    lo, hi = max(lo, run[0]), min(hi, run[1])
                # An access the lift proves inside a hull touches that hull alone.
                lo, hi = f.proven_iv(o) or (lo, hi)
                # An access with an address set reaches its addresses (a load's where they lie
                # in the loaded program or reach code, which no pointer run holds): inside
                # it, through one span holding them, as the image holds the bytes between; a
                # run of the set as wide as an access of unknown address touches its bytes.
                store = o.name == "memref.store"
                aset = self.addr_set(f.name, o, calls=False)
                a0, a1 = self.mem.loaded
                inside = all(a0 <= a < b <= a1 for a, b in aset or ())
                if not store and not inside and not any(self.mem.code[a:b].any() for a, b in aset):
                    aset = None
                if aset and hi - lo >= regions.BOUNDED:
                    step = regions.BOUNDED
                    ranges.extend(
                        (x, min(x + step, b) - 1) for a, b in aset for x in range(a, b, step)
                    )
                    if self.mem.loaded[0] <= aset[0][0] and aset[-1][1] <= self.mem.loaded[1]:
                        joined.append((aset[0][0], aset[-1][1] - 1))
                    continue
                # A load the lift bounds reads the run its hull gives.
                hull = self.reach_of(f, o) if o.name == "memref.load" else None
                if hull is not None and hi - lo >= regions.BOUNDED:
                    ptrs.append((f, o, hull, (lo, hi)))
                    continue
                # A store the lift bounds to an array writes it, checked, whatever its width.
                if run is not None and hi - lo >= regions.BOUNDED:
                    bound.append((lo, hi))
                    continue
                ranges.append((lo, hi))
                if o.name == "memref.store" and lo == hi:
                    stores.add(lo)
                if per and len(set(per)) > 1 and hi - lo < regions.BOUNDED:
                    joined.append((lo, hi))
                write = is_write(o)
                unknown |= write and hi - lo >= regions.BOUNDED
                if write and hi - lo < regions.BOUNDED:
                    writes.append((lo, hi))
        wide = []
        for f, o, hull, r in ptrs:
            sure = self.reach_of(f, o, "proven")
            run = self.mem.pointer_run(hull)
            if sure is not None and self.mem.pointer_run(sure, True) is not None:
                run = sure[:2]
                f.sure.add(id(o))
            if run is None:
                ranges.append(r)
                continue
            f.wide[id(o)] = run
            wide.append(run)
        self.mem.partition(
            ranges,
            sorted(stores),
            unknown,
            joined + wide,
            wide + bound,
            writes + bound + self.held_runs,
            self.state.mask(),
            self.held_runs,
            sorted({r for f in self.funcs for r in self.blocks(f)}),
            sorted({h for f in self.funcs for h in self.block_hulls(f)}),
        )

    def block_hulls(self, f):
        """The hulls ``(lo, hi)`` (``hi`` exclusive) of the data blocks of several runs the
        loads of function ``f`` lie in: each held as one array (:meth:`regions.Memory.partition`).
        """
        out = set()
        for o, _, _ in f.accesses():
            runs = self.block(f, o) if o.name == "memref.load" else None
            if runs and len(runs) > 1:
                out.add((runs[0][0], runs[-1][1]))
        return out

    def blocks(self, f):
        """The runs of the data blocks the accesses of function ``f`` lie in."""
        return {tuple(r) for o, _, _ in f.accesses() for r in self.block(f, o) or ()}

    def owners(self, runs, held, whole=True):
        """``(regions, held spans)`` the address ``runs`` (``[(lo, hi)]``, ``hi`` exclusive)
        lie in (spans only when ``held``: a load; with ``whole``, the one span holding them
        all where there is one), or None when some address is in none."""
        if not runs:
            return None
        mem = self.mem
        idx = np.concatenate([np.arange(lo, hi) for lo, hi in runs]) & 0xFFFF
        reg = mem.owner[idx]
        whole = whole and (held or bool((reg >= 0).all()))
        if whole and (j := mem.holding(idx.min(), idx.max())) is not None:
            return (), (j,)
        span = np.where(held & mem.constant[idx], mem.spans_at(idx), -1)
        if ((reg < 0) & (span < 0)).any():
            return None
        return tuple(int(k) for k in np.unique(reg[reg >= 0])), tuple(
            int(j) for j in np.unique(span[(reg < 0) & (span >= 0)])
        )

    def block_fn(self, kind, regs, spans):
        """The name of the ``kind`` (``rd``, ``wr``) function of the data block over the
        regions ``regs`` and held spans ``spans`` (an address in one of them): its element
        there, chosen by the address."""
        key = (kind, tuple(regs), tuple(spans))
        self.regions_used.update(regs)
        self.spans_used.update(spans)
        if spans:
            self.used.add("CODE")
        if key not in self.block_fns:
            self.block_fns[key] = f"{kind}B{sum(k[0] == kind for k in self.block_fns)}"
        return self.block_fns[key]

    def outside(self, j, p):
        """The test of address ``p`` (an expression) lying outside object ``j``; of a slack
        object (``ptr_slack``), of ``p`` plus ``lo`` outside its block with ``k`` bytes past it."""
        j, lo, k = self.ptr_slack.get(j, (j, 0, 0))
        self.objs_used.add(j)
        at = cn.cast("uint16_t", cn.binop("+", p.node, cn.const(lo))) if lo else p.node
        if j in self.ptr_parts:
            return cn.call(f"cgen_out{j}", at, cn.const(k))
        return _outside(j, cn.cast("uint16_t", cn.binop("-", at, cn.ident(f"CGEN_OBJ{j}_ORG"))), k)

    def checked_fn(self, name, j):
        """The name of block reader ``name``'s (or block array's) variant checking its address
        into object ``j`` first, leaving by the trap its caller passes."""
        self.checked_fns.add((name, j))
        self.objs_used.add(self.ptr_slack.get(j, (j,))[0])
        return f"{name}c{j}"

    def guarded_fn(self, k, j):
        """The name of the reader of region ``k`` that checks its address into object ``j``
        first, leaving by the trap its caller passes."""
        self.guarded.add((k, j))
        self.objs_used.add(self.ptr_slack.get(j, (j,))[0])
        return f"R{k}c{j}"

    def set_fn(self, name, j, indexed):
        """The name of the reader of array ``name`` that checks its address into object ``j``
        first (``indexed`` True: given a base and a byte, not the address; ``"g"``: given the
        offset into the span the address is and a byte, the span's ``CGEN_IX`` read)."""
        mode = {True: "x", False: "c"}.get(indexed, indexed)
        self.setfns.add((name, j, mode))
        self.objs_used.add(self.ptr_slack.get(j, (j,))[0])
        return f"{name}{mode}{j}"

    def word_fn(self, k, j):
        """The name of the reader of a 16-bit table split into low and high bytes in region
        ``k``, each read checked into object ``j`` (:meth:`guarded_fn`)."""
        self.guarded.add((k, j))
        self.words.add((k, j))
        self.objs_used.add(self.ptr_slack.get(j, (j,))[0])
        return f"W{k}c{j}"

    def block_c(self):  # pylint: disable=too-many-locals
        """The definitions of the data blocks' readers and writers (:meth:`block_fn`) and of
        the tests of a pointer outside an object of several runs (the ``.h``'s
        ``CGEN_OBJj_OUT``)."""
        out, noinline = [], ("noinline",)
        for j in sorted(self.objs_used & set(self.ptr_parts)):
            lo, lim = f"cgen_lo{j}", f"cgen_lim{j}"
            out += [
                cn.decl(lo, "const uint16_t", cn.inits(f"CGEN_OBJ{j}_LO"), ("static",), dim=True),
                cn.decl(lim, "const uint16_t", cn.inits(f"CGEN_OBJ{j}_LIM"), ("static",), dim=True),
            ]
            el = cn.index(lim, cn.ident("i"))
            hit = cn.binop(
                "&&",
                cn.binop("<=", cn.ident("d"), el),
                cn.binop(
                    "<=", cn.ident("k"), cn.cast("uint16_t", cn.binop("-", el, cn.ident("d")))
                ),
            )
            body = [
                cn.decl("d", "uint16_t", cn.binop("-", cn.ident("p"), cn.index(lo, cn.ident("i")))),
                cn.when(hit, [cn.ret(cn.const(0))]),
            ]
            head = cn.function(
                f"cgen_out{j}",
                "uint8_t",
                [("p", "uint16_t"), ("k", "uint8_t")],
                ("static",),
                noinline,
            )
            out.append(cn.define(head, [_count(lo, "uint16_t", body), cn.ret(cn.const(1))]))
        for (kind, regs, spans), name in sorted(self.block_fns.items(), key=lambda kv: kv[1]):
            names = [f"R{k}" for k in regs] + [f"C{j}" for j in spans]
            self.choices(regs, spans, cn.ident("a"))
            ptr = "const uint8_t *const" if kind == "rd" else "uint8_t *const"
            out += [
                cn.decl(
                    f"{name}_org",
                    "const uint16_t",
                    cn.inits(*(x + "_ORG" for x in names)),
                    ("static",),
                    dim=True,
                ),
                cn.decl(
                    f"{name}_len",
                    "const uint16_t",
                    cn.inits(*(x + "_LEN" for x in names)),
                    ("static",),
                    dim=True,
                ),
                cn.decl(f"{name}_at", ptr, cn.inits(*names), ("static",), dim=True),
            ]
            off = cn.cast(
                "uint16_t", cn.binop("-", cn.ident("a"), cn.index(f"{name}_org", cn.ident("i")))
            )
            more = cn.binop(
                "&&",
                cn.binop("<", cn.ident("i"), cn.const(len(names) - 1)),
                cn.binop(">=", off, cn.index(f"{name}_len", cn.ident("i"))),
            )
            find = [
                cn.decl("i", "uint16_t", cn.const(0)),
                c_ast.While(more, cs.braced([c_ast.UnaryOp("++", cn.ident("i"))])),
            ]
            el = cn.index(cn.index(f"{name}_at", cn.ident("i")), off)
            if kind == "rd":
                head = cn.function(name, "uint8_t", [("a", "uint16_t")], ("static",), noinline)
                out.append(cn.define(head, [*find, cn.ret(el)]))
            else:
                head = cn.function(
                    name, "void", [("a", "uint16_t"), ("v", "uint8_t")], ("static",), noinline
                )
                out.append(cn.define(head, [*find, cn.assign(el, cn.ident("v"))]))
        for b in sorted(self.block_readers):
            head = cn.function(
                f"B{b}r", "uint8_t", [("p", "uint16_t"), ("y", "uint8_t")], ("static",), noinline
            )
            off = cn.cast(
                "uint16_t",
                cn.binop("-", cn.binop("+", cn.ident("p"), cn.ident("y")), cn.ident(f"B{b}_ORG")),
            )
            out.append(cn.define(head, [cn.ret(cn.index(f"B{b}", off))]))
        kt = "uint8_t" if max(self.traps, default=0) < 0x100 else "uint16_t"
        # A block array's readers checking a window (slack objects) and the others of their
        # object: each its window, from the address plus ``lo`` to ``k`` bytes past it,
        # handed to one reader of the block checking any window of the object.
        win = {
            (name, self.ptr_slack[j][0])
            for name, j in self.checked_fns
            if name[0] == "B" and j in self.ptr_slack
        }
        pyk = [("p", "uint16_t"), ("y", "uint8_t")]
        for name, b in sorted(win):
            args = [*pyk, ("t", kt), ("lo", "uint16_t"), ("k", "uint8_t")]
            e = cn.binop("+", cn.ident("a"), cn.ident("lo"))
            if b in self.ptr_parts:
                test = cn.call(f"cgen_out{b}", cn.ident("e"), cn.ident("k"))
            else:
                e = cn.binop("-", e, cn.ident(f"CGEN_OBJ{b}_ORG"))
                test = _outside(b, cn.ident("e"), cn.ident("k"))
            body = [
                cn.decl("a", "uint16_t", cn.binop("+", cn.ident("p"), cn.ident("y"))),
                cn.decl("e", "uint16_t", e),
                cn.when(test, [cn.call("trap", cn.ident("t"))]),
                cn.ret(_element(name, cn.ident("a"))),
            ]
            out.append(
                cn.define(cn.function(f"{name}w{b}", "uint8_t", args, ("static",), noinline), body)
            )
        for name, j in sorted(self.checked_fns):
            b, lo, k = self.ptr_slack.get(j, (j, 0, 0))
            if (name, b) in win:
                head = cn.function(
                    f"{name}c{j}", "uint8_t", [*pyk, ("t", kt)], ("static",), noinline
                )
                fwd = cn.call(
                    f"{name}w{b}",
                    cn.ident("p"),
                    cn.ident("y"),
                    cn.ident("t"),
                    lit(lo, 16).node,
                    cn.const(k),
                )
                out.append(cn.define(head, [cn.ret(fwd)]))
                continue
            trap = cn.call("trap", cn.ident("k"))
            if name[0] == "B":
                head = cn.function(
                    f"{name}c{j}", "uint8_t", [*pyk, ("k", kt)], ("static",), noinline
                )
                test = self.outside(j, E(cn.ident("a"), 0, 0xFFFF))
                body = [
                    cn.decl("a", "uint16_t", cn.binop("+", cn.ident("p"), cn.ident("y"))),
                    cn.when(test, [trap]),
                ]
                out.append(cn.define(head, [*body, cn.ret(_element(name, cn.ident("a")))]))
                continue
            head = cn.function(
                f"{name}c{j}", "uint8_t", [("a", "uint16_t"), ("k", kt)], ("static",), noinline
            )
            if j in self.ptr_parts:
                test = cn.call(f"cgen_out{j}", cn.ident("a"), cn.const(0))
            else:
                off = cn.cast(
                    "uint16_t", cn.binop("-", cn.ident("a"), cn.ident(f"CGEN_OBJ{j}_ORG"))
                )
                test = cn.binop(">", off, cn.ident(f"CGEN_OBJ{j}_LIM"))
            out.append(
                cn.define(head, [cn.when(test, [trap]), cn.ret(cn.call(name, cn.ident("a")))])
            )
        for k, j in sorted(self.guarded):
            head = cn.function(f"R{k}c{j}", "uint8_t", [("a", "uint16_t"), ("k", kt)], ("static",))
            test = self.outside(j, E(cn.ident("a"), 0, 0xFFFF))
            elem = cn.index(f"R{k}", cn.binop("-", cn.ident("a"), cn.ident(f"R{k}_ORG")))
            out.append(
                cn.define(head, [cn.when(test, [cn.call("trap", cn.ident("k"))]), cn.ret(elem)])
            )
        for name, j, mode in sorted(self.setfns):
            org, k = cn.ident(f"{name}_ORG"), cn.ident("k")
            at = a = cn.ident("a")
            if mode == "c":
                params = [("a", "uint16_t"), ("k", kt)]
                decls = []
                el = cn.index(name, cn.cast("uint16_t", cn.binop("-", a, org)))
            else:
                p = cn.ident("p")
                params = [("p", "uint16_t"), ("y", "uint8_t"), ("k", kt)]
                if mode == "x":
                    decls = [cn.decl("a", "uint16_t", cn.binop("+", p, cn.ident("y")))]
                    el = cn.call("CGEN_IX", cn.ident(name), cn.binop("-", p, org), cn.ident("y"))
                else:
                    decls = [
                        cn.decl(
                            "a", "uint16_t", cn.binop("+", cn.binop("+", p, org), cn.ident("y"))
                        )
                    ]
                    el = cn.call("CGEN_IX", cn.ident(name), p, cn.ident("y"))
            head = cn.function(f"{name}{mode}{j}", "uint8_t", params, ("static",))
            test = self.outside(j, E(at, 0, 0xFFFF))
            body = [*decls, cn.when(test, [cn.call("trap", k)]), cn.ret(el)]
            out.append(cn.define(head, body))
        for k, j in sorted(self.words):
            params = [("lo", "uint16_t"), ("hi", "uint16_t"), ("i", "uint8_t")]
            head = cn.function(
                f"W{k}c{j}", "uint16_t", [*params, ("klo", kt), ("khi", kt)], ("static",)
            )
            read = f"R{k}c{j}"
            low = cn.decl(
                "l",
                "uint8_t",
                cn.call(read, cn.binop("+", cn.ident("lo"), cn.ident("i")), cn.ident("klo")),
            )
            high = cn.call(read, cn.binop("+", cn.ident("hi"), cn.ident("i")), cn.ident("khi"))
            word = cn.binop(
                "|", cn.binop("<<", cn.cast("uint16_t", high), cn.const(8)), cn.ident("l")
            )
            out.append(cn.define(head, [low, cn.ret(word)]))
        return out

    def choices(self, regs, spans, addr, indexed=None, tests=None):
        """``[(test, element)]`` of address ``addr`` (an expression) in each of the regions
        ``regs`` and held ``spans`` its address set lies in: the last needs no test. With
        ``indexed`` (``(base, index)``, the address a 16-bit base plus an 8-bit index) the
        element is the base's place indexed, as the 6502's ``(zp),Y`` is; ``tests`` replace
        the address's tests."""
        names = [f"R{k}" for k in regs] + [f"C{j}" for j in spans]
        self.regions_used.update(regs)
        if spans:
            self.used.add("CODE")
        out = []
        for n in names:
            org = cn.ident(n + "_ORG")
            off = cn.cast("uint16_t", cn.binop("-", addr, org))
            if indexed:
                el = cn.call("CGEN_IX", cn.ident(n), cn.binop("-", indexed[0], org), indexed[1])
                self.used.add("CGEN_IX")
            else:
                el = cn.index(n, off)
            out.append((cn.binop("<", off, cn.ident(n + "_LEN")), el))
        return out if tests is None else [(t, e) for t, (_, e) in zip(tests, out)]

    def known(self, name, a):
        """The value byte ``a`` holds whenever function ``name`` runs, or None: for the other
        entries' lift (:attr:`after`) one init leaves constant (:attr:`folded`), else a
        constant byte's (code, or a table the lift read as constant) or the entry value of a
        machine byte outside ``loaded`` no store may write (:meth:`unwritten`); not a volatile
        one."""
        if self.mem.vol[a]:
            return None
        if self.folded[0][a] and (self.after is None or name in self.after):
            return self.folded[1][a]
        free = self.free is not None and self.free[a] and not self.mem.moves(a)
        return self.mem.image[a] if self.mem.constant[a] or free else None

    def unwritten(self):
        """The bytes no store of any function may write (:meth:`stored`), no store with an
        address set (``Memory.written``) or init (:attr:`init_stored`) writes, outside the
        stack page and the volatile ranges; None when a store of unknown address may write any
        byte. While they are found no byte is known unwritten, and the functions' intervals
        are found again after."""
        self.free = None
        may = self.stored()
        if may is not None:
            may |= self.mem.vol | self.init_stored
            masks.mask([(0x100, 0x200), *self.mem.written], out=may)
        self.free = None if may is None else ~may
        return self.free

    def stored(self):
        """The bytes some store of any function may write: its address set, else its address
        interval (each copy's in an instance) met with the run the lift bounds it to; None
        when one of unknown address may write any byte."""
        may = np.zeros(0x10000, dtype=bool)
        for f in self.funcs:
            for o, iv, per in f.accesses():
                if not is_write(o):
                    continue
                aset = self.addr_set(f.name, o, calls=False)
                masks.mask(aset or (), out=may)
                if aset:
                    continue
                run = self.run_of(f, o)
                for lo, hi in per or [iv]:
                    if run is not None:
                        lo, hi = max(lo, run[0]), min(hi, run[1])
                    elif hi - lo >= regions.BOUNDED:
                        return None
                    may[lo : hi + 1] = True
        return may

    def symbol(self, j):
        """The ``.h`` name of symbol ``j`` (:attr:`symbols`): ``G_`` and the offset of its
        instruction as :meth:`var_name` gives it, numbered from its second."""
        pc, v = self.symbols[j]
        n = sum(q == pc for q, _ in self.symbols[:j])
        name = "G_" + self.var_name(pc)[2:] + (f"_{n}" if n else "")
        self.konsts[name] = v
        return name

    def konst(self, addr):
        """The ``.h`` name of the constant byte at ``addr``, relative to the origin it is
        placed by (so the ``.c`` holds neither its value nor its address), a byte of the image
        relative to it, a fixed machine address's ``MEM_K`` and the address; of a byte init
        leaves constant (:attr:`folded`) ``K_`` and the offset its variable would be named by
        (:meth:`var_name`)."""
        if self.folded[0][addr]:
            name = "K_" + self.var_name(addr)[2:]
            self.konsts[name] = self.folded[1][addr]
            return name
        sym, off = self.mem.base(addr)
        if sym is None and self.mem.moves(addr):
            sym, off = "IMG_ORG", addr - self.mem.loaded[0]
        name = f"MEM_K{addr:04X}" if sym is None else f"{sym[:-4]}_K{off:X}"
        self.konsts[name] = self.mem.image[addr]
        if sym == "CODE_ORG":
            self.used.add("CODE_ORG")
        elif sym == "IMG_ORG":
            self.used.add("IMG_ORG")
        return name

    def placed(self, addr, org):
        """Whether the distance of ``addr`` from a span's origin ``org`` below it is the
        layout's: a byte from one to the other is no instruction's, so where the program's
        data lies decides it."""
        return not self.insn[org : addr + 1].all()

    def offset(self, span, pc, addr, org):
        """The offset of ``addr`` in held span ``span`` (origin ``org``) as the access of the
        instruction at ``pc`` names it: by a ``.h`` name where the layout places it
        (:meth:`placed`), as where a song's tables lie is song data; else as a literal."""
        at = self.mem.base(pc)[1] if pc is not None else None
        if at is None or not self.placed(addr, org):
            return lit(addr - org, 16)
        if (span, addr) not in self.offsets:
            stem = f"{span}_AT{at:X}"
            n = sum(v.split("_")[:2] == stem.split("_") for v in self.offsets.values())
            self.offsets[span, addr] = stem + (f"_{n}" if n else "")
            self.konsts[self.offsets[span, addr]] = addr - org
        return named(self.offsets[span, addr], addr - org, addr - org, True)

    def table(self, width, vals):
        """The name of the constant table of ``vals`` (``width`` bits), named on first use."""
        key = (width, tuple(x if width == "ptr" else int(x) for x in vals))
        if key not in self.tables:
            self.tables[key] = f"K{len(self.tables)}"
        return self.tables[key]

    def pointers(self, place, addrs):
        """The name of the constant table of pointers to ``addrs`` in the region or held span
        ``place``, named on first use."""
        kind, j = place
        if kind == "held":
            self.used.add("CODE")
            self.spans_used.add(j)
            name, org = f"C{j}", self.mem.held[j][0]
        else:
            self.regions_used.add(j)
            name, org = f"R{j}", self.mem.regions[j][0]
        offs = [int(a) - org for a in addrs]
        return self.table("ptr", [(name, d) for d in offs])

    def ret_type(self, f):
        """The C return type of function ``f``'s live results: none, its one result, or with
        several none, the results being left in the register variables (:meth:`reg_var`)."""
        ws = [WIDTH[t] for j, t in enumerate(f.results()) if j in f.live_res]
        return CTYPE[ws[0]] if len(ws) == 1 else "void"

    def reg_var(self, f, j):
        """The static variable result ``j`` of function ``f`` (a register of the register
        file) is left in when ``f`` returns several, as the 6502 leaves its registers."""
        self.structs[j] = WIDTH[f.results()[j]]
        return f"rv{j}"

    def name_of(self, target, caller):
        """The name of C function ``target``, named on its first call."""
        if target.name is None:
            target.name = f"f{len(self.order) - len(self.entries) + 1}"
            self.order.append(target)
        self.edges.setdefault(id(caller), [])
        if target not in self.edges[id(caller)]:
            self.edges[id(caller)].append(target)
        return target.name

    def structure(self):
        """Structure each function whole; ``shared``: the instructions two or more functions
        hold."""
        held = Counter(pc for f in self.funcs for pc in {o.pc for b in f.blocks for o in b.ops})
        self.shared = {pc for pc, n in held.items() if pc and n > 1}
        for f in self.funcs:
            f.kpointers()
            f.mark_addresses()
            self.mains[f.name] = CFunc(f, f.graph(self.ids))
            self.mains[f.name].structure()

    def functions(self):
        """The C text of every function: struct types and the prototypes recursion needs,
        then the definitions, callees first."""
        self.entries[:] = [self.mains[f.name] for f in self.funcs if not f.private]
        for c in self.entries:
            c.name = c.f.name
        self.order = list(self.entries)
        defs, k = {}, 0
        while k < len(self.order):
            c = self.order[k]
            defs[id(c)] = c.definition()
            k += 1
        post, state, protos = [], {}, []

        def visit(c):
            state[id(c)] = 1
            for t in self.edges.get(id(c), []):
                if id(t) not in state:
                    visit(t)
                elif state[id(t)] == 1 and t not in protos:
                    protos.append(t)
            state[id(c)] = 2
            post.append(c)

        for c in self.entries:
            if id(c) not in state:
                visit(c)
        for c in self.mains.values():
            c.body, c.covers, c.g = None, {}, None
        for f in self.funcs:
            f.conds.close()
        out = [
            cn.decl(f"rv{j}", CTYPE[w], storage=("static",))
            for j, w in sorted(self.structs.items())
        ]
        for (w, vals), n in self.tables.items():
            if w == "ptr":
                items = [
                    cn.binop("+", cn.ident(a), lit(d, 16).node) if d else cn.ident(a)
                    for a, d in vals
                ]
                out.append(
                    cn.decl(n, "uint8_t *const", c_ast.InitList(items), ("static",), dim=True)
                )
            else:
                items = c_ast.InitList([lit(x, w).node for x in vals])
                out.append(cn.decl(n, f"const {CTYPE[w]}", items, ("static",), dim=True))
        heads = [copy.deepcopy(c.head_decl) for c in protos]
        kept, names = fold([(c.name, defs[id(c)]) for c in post])
        cn.rename(heads, names)
        heads = [h for i, h in enumerate(heads) if cn.key(h) not in map(cn.key, heads[:i])]
        nodes = [x for xs in kept for x in xs]
        self.made["functions"], self.made["folded"] = len(kept), len(post) - len(kept)
        self.made["gotos"] = sum(isinstance(n, c_ast.Goto) for x in nodes for n in cn.walk(x))
        self.pieces = {x for c in post for x in c.prov.get("pieces", ())}
        self.provenance(post, names)
        return out + heads + nodes

    def provenance(self, cfuncs, names):
        """:attr:`prov`: where each printed construct comes from in the machine code, the C
        functions by their final names, each with the sites calling it (one kept for several
        identical ones lists theirs as copies)."""
        funcs, sites = {}, {}
        prov = {"gotos": [], "reduced": [], "loops": [], "conditions": [], "switches": []}
        for c in cfuncs:
            for b in c.f.blocks:
                for o in b.ops if b.name in c.owned else ():
                    callee = o.prop("callee")[1:] if o.name == "func.call" else None
                    site = o.pc or (callee and o.prop("site") and o.int_prop("site"))
                    if site and callee in self.lifted:
                        sites.setdefault(callee, []).append([site, self.lifted[callee][0]])
            for site, callee in c.prov.get("calls", ()):
                if [site, self.lifted[callee][0]] not in sites.setdefault(callee, []):
                    sites[callee].append([site, self.lifted[callee][0]])
        for c in cfuncs:
            name = names.get(c.name, c.name)
            pc, copies = self.lifted[c.f.name]
            calls = sites.get(c.f.name, [])
            each = self.copy_pcs.get(c.f.name, {})
            pcs = {
                q
                for b in c.f.blocks
                if b.name in c.owned
                for o in b.ops
                if o.pc is not None
                for q in each.get(o.pc, [o.pc])
                if q is not None
            }
            if name in funcs:
                funcs[name]["copies"] += [pc, *copies]
                funcs[name]["calls"] += calls
                funcs[name]["pcs"] = sorted(set(funcs[name]["pcs"]) | pcs)
                funcs[name]["pieces"] = sorted(
                    set(funcs[name]["pieces"]) | set(c.prov.get("pieces", ()))
                )
                continue
            funcs[name] = {
                "entry": pc,
                "index": bool(copies),
                "calls": calls,
                "copies": list(copies),
                "pcs": sorted(pcs),
                "pieces": list(c.prov.get("pieces", ())),
            }
            for k, v in prov.items():
                v += _copies(c.prov[k], self.copy_pcs.get(c.f.name, {}))
        self.prov = {"functions": funcs, **prov}
