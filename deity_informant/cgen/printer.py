"""Optimized MLIR (``cf``/``arith``/``memref``/``func``) structured and printed as compact C99.

Expressions carry their exact integer range under C's promotions, so a truncating cast is
printed only where a wider value would change a comparison, shift or subscript, and an operand
is made unsigned only where 16-bit ``int`` arithmetic could overflow.
"""

from __future__ import annotations

import operator
from collections import Counter
from dataclasses import dataclass, field, replace

import networkx as nx
import numpy as np

from .csyntax import declare, drop_unread, ends_in_jump, names_in, render
from .structure import Conds, Graph, Structurer, new_ids, outline

WIDTH = {"i1": 1, "i8": 8, "i16": 16, "i32": 8, "i64": 16, "index": 16}
CTYPE = {1: "uint8_t", 8: "uint8_t", 16: "uint16_t"}
CMP = {0: "==", 1: "!=", 2: "<", 3: "<=", 4: ">", 5: ">=", 6: "<", 7: "<=", 8: ">", 9: ">="}
FLIP = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", "<=": ">", ">": "<="}
BINOP = {
    "arith.addi": ("+", 12),
    "arith.subi": ("-", 12),
    "arith.muli": ("*", 13),
    "arith.andi": ("&", 8),
    "arith.ori": ("|", 6),
    "arith.xori": ("^", 7),
    "arith.shli": ("<<", 11),
    "arith.shrui": (">>", 11),
}
NOISY = {"<<", ">>", "&", "|", "^", "&&", "||"}
INT16 = (-0x8000, 0x7FFF)


@dataclass(frozen=True)
class E:  # pylint: disable=too-many-instance-attributes
    """A C expression: text, precedence, top operator, exact range, whether it is unsigned
    where ``int`` has 16 bits, whether it reads memory, the variables it reads and its
    operands ``(operator, left, right)`` when it is a comparison or negation."""

    text: str
    prec: int = 16
    op: str = ""
    lo: int = 0
    hi: int = 0
    uns: bool = False
    mem: bool = False
    refs: frozenset = field(default_factory=frozenset)
    parts: tuple | None = None


def lit(v, width):
    """A literal of ``width`` bits: decimal below 10, else hex."""
    v &= (1 << width) - 1
    text = str(v) if v < 10 else f"0x{v:X}"
    if 0x7F00 <= v <= 0x7FFF:
        text += "u"
    return E(text, lo=v, hi=v, uns=v > 0x7EFF)


def var(name, width):
    """A variable of ``width`` bits."""
    return E(name, lo=0, hi=(1 << width) - 1, uns=width == 16, refs=frozenset([name]))


def join(*es):
    """The ``mem`` and ``refs`` of expressions ``es`` combined."""
    return {"mem": any(e.mem for e in es), "refs": frozenset().union(*(e.refs for e in es))}


def par(e, prec, op=""):
    """``e`` as an operand of operator ``op`` of precedence ``prec``: parenthesized where
    precedence requires or where a compiler asks for clarity."""
    noisy = op in NOISY and e.prec < 15 and e.op != op
    noisy |= op in FLIP and (e.op == "!" or e.op in FLIP)
    return f"({e.text})" if e.prec < prec or noisy else e.text


def cast(e, ctype, width, signed=False):
    """``e`` converted to ``ctype``."""
    half = 1 << (width - 1)
    lo, hi = (-half, half - 1) if signed else (0, (1 << width) - 1)
    return E(f"({ctype}){par(e, 15)}", 15, "cast", lo, hi, width == 16 and not signed, **join(e))


def clean(e, width):
    """``e`` reduced to ``width`` bits by a cast when its range exceeds them."""
    if 0 <= e.lo and e.hi < (1 << width):
        return e
    return cast(e, CTYPE[width], width)


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
    top = min((1 << max(a.hi, b.hi).bit_length()) - 1, a.hi + b.hi)
    return (max(a.lo, b.lo) if sym == "|" else 0), top


def binary(sym, prec, a, b, width):
    """``a sym b`` with its exact range; an operand becomes unsigned where the result would
    overflow a 16-bit ``int``."""
    if sym == ">>" or (sym in ("&", "|", "^") and min(a.lo, b.lo) < 0):
        a, b = clean(a, width), clean(b, width)
    rng = _range(sym, a, b)
    uns = a.uns or (b.uns and sym not in ("<<", ">>"))
    if not uns and (rng[0] < INT16[0] or rng[1] > INT16[1]):
        if a.text[0].isdigit():
            a = replace(a, text=a.text.rstrip("u") + "u", uns=True)
        else:
            a = cast(a, "uint16_t", 16)
        uns = True
    text = f"{par(a, prec, sym)} {sym} {par(b, prec + 1, sym)}"
    return E(text, prec, sym, rng[0], rng[1], uns, **join(a, b))


def compare(sym, a, b):
    """``a sym b``."""
    prec = 9 if sym in ("==", "!=") else 10
    text = f"{par(a, prec, sym)} {sym} {par(b, prec + 1, sym)}"
    return E(text, prec, sym, 0, 1, parts=(sym, a, b), **join(a, b))


def negated(e):
    """The logical negation of truth value ``e``."""
    if e.op in FLIP:
        return compare(FLIP[e.op], e.parts[1], e.parts[2])
    if e.op == "!":
        return e.parts[1]
    return E(f"!{par(e, 15)}", 15, "!", 0, 1, parts=("!", e), **join(e))


def truth(e):
    """``e`` as a condition: ``x != 0`` as ``x``, ``x == 0`` as ``!x``."""
    if e.op in ("==", "!=") and e.parts[2].text == "0" and e.parts[1].lo >= 0:
        x = e.parts[1]
        x = truth(x) if x.hi == 1 else x
        return x if e.op == "!=" else negated(x)
    return e


NUMPY = {
    "arith.addi": operator.add,
    "arith.subi": operator.sub,
    "arith.muli": operator.mul,
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


def _const_offset(text):
    sym, _, off = text.partition(" + ")
    return sym, int(off, 0) if off else 0


def logic(sym, es):
    """``es`` joined by ``&&`` or ``||`` (none: the operator's identity)."""
    if not es:
        return lit(int(sym == "&&"), 1)
    if len(es) == 1:
        return es[0]
    prec = 5 if sym == "&&" else 4
    text = f" {sym} ".join(par(e, prec + 1, sym) for e in es)
    return E(text, prec, sym, 0, 1, **join(*es))


class Func:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One ``func.func`` of the optimized module: its values, their ranges and uses."""

    def __init__(self, op, mod):
        self.op, self.mod = op, mod
        self.name = op.prop("sym_name")
        self.blocks = op.regions[0]
        self.block_of = {b.name: b for b in self.blocks}
        self.defs, self.types, self.uses, self.user, self.where = {}, {}, {}, {}, {}
        self.users, self.ivs, self.dropped, self.globals = {}, {}, set(), set()
        self.addr_edges, self.arg_src, self.arg_block, self.dead = set(), {}, {}, set()
        self.flags = set()
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
        for blk in self.blocks:
            for n, t in blk.args:
                self.types[n], self.arg_block[n] = t, blk
            for i, o in enumerate(blk.ops):
                self.where[id(o)] = (blk, i)
                if o.name == "memref.get_global":
                    self.globals.add(o.res[0])
                for j, r in enumerate(o.res):
                    self.defs[r] = o
                    self.types[r] = o.types[1][j]
                for a in o.args:
                    self.uses[a] = self.uses.get(a, 0) + 1
                    self.user[a] = o
                    self.users.setdefault(a, []).append(o)
                if o.name == "cf.switch":
                    self.flags.add(o.args[0])
                for succ, idx, case in o.edges():
                    for (a, _), i in zip(self.block_of[succ].args, idx):
                        self.arg_src.setdefault(a, []).append((o, i, case))

    def dominators(self):
        """``{block name: immediate dominator name}``."""
        g = nx.DiGraph()
        g.add_node(self.blocks[0].name)
        for b in self.blocks:
            g.add_edges_from((b.name, s) for s in b.ops[-1].succs)
        return nx.immediate_dominators(g, self.blocks[0].name)

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
        elif o.name in ("memref.load", "func.call"):
            r = full
        elif o.name == "arith.constant":
            c = o.int_prop("value") & top
            r = (c, c)
        elif o.name == "ub.poison":
            r = (0, 0)
        elif o.name in CASTS:
            r = self.iv(o.args[0])
            r = r if r[1] <= top else full
        elif o.name == "arith.select":
            a, b = self.iv(o.args[1]), self.iv(o.args[2])
            r = (min(a[0], b[0]), max(a[1], b[1]))
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
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
            r = (0, min(a[1], b[1]))
        elif o.name in ("arith.ori", "arith.xori"):
            a, b = self.iv(o.args[0]), self.iv(o.args[1])
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
        self.ivs[v] = full
        ivs = [f.iv(o.args[names.index(v)]) for f, o in sites]
        return min(lo for lo, _ in ivs), max(hi for _, hi in ivs)

    def loop_iv(self, v, back, init):
        """The hull of the values block argument ``v`` takes from ``init`` around the loop
        edges ``back``, when their conditions and values depend on ``v`` alone: the set of
        values is iterated to its fixpoint, every member evaluated at once."""
        seen = np.zeros(1 << self.width(v), dtype=bool)
        seen[init[0] : init[1] + 1] = True
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
                    vals = np.flatnonzero(seen)
                    return int(vals[0]), int(vals[-1])
                seen[new] = True
        except Unknown:
            return None
        return None

    def evaluate(self, v, env, memo):  # pylint: disable=too-many-branches
        """The values of ``v`` (an array or scalar) for the block arguments ``env``."""
        if v in env:
            return env[v]
        if v in memo:
            return memo[v]
        d = self.defs.get(v)
        if d is None or not d.name.startswith(("arith.", "ub.")) or len(d.res) != 1:
            raise Unknown(v)
        top = (1 << self.width(v)) - 1
        name = d.name
        if name == "arith.constant":
            r = np.int64(d.int_prop("value") & top)
        elif name == "ub.poison":
            r = np.int64(0)
        elif name == "arith.select":
            c, a, b = (self.evaluate(x, env, memo) for x in d.args)
            r = np.where(c != 0, a, b)
        else:
            args = [np.asarray(self.evaluate(x, env, memo), dtype=np.int64) for x in d.args]
            if name in CASTS:
                r = args[0] & top
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

    def address(self, o):
        """The 16-bit address value of a memory access or I/O call ``o``."""
        if o.name == "func.call":
            return o.args[0]
        idx = o.args[-1]
        d = self.defs.get(idx)
        return d.args[0] if d is not None and d.name == "arith.index_castui" else idx

    def accesses(self):
        """``[(op, (lo, hi))]`` of the memory accesses and I/O calls."""
        out = []
        for o in self.walk():
            if o.name in ("memref.load", "memref.store") and o.args[-2] in self.globals:
                out.append((o, self.iv(self.address(o))))
            elif o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write"):
                out.append((o, self.iv(o.args[0])))
        return out

    def root(self, o):
        """Whether operation ``o`` is observable by itself: a store, an I/O write, a trap,
        or a read that may meet a read-sensitive address."""
        if o.name == "memref.store":
            return True
        callee = o.prop("callee") if o.name == "func.call" else None
        if callee in ("@io_write", "@trap"):
            return True
        return callee == "@io_read" and self.mod.mem.sensitive_in(*self.iv(o.args[0]))

    def settle(self, live, funcs):
        """Drop what ``live`` does not hold: pure operations and reads, the arguments of
        unused parameters, unused returned values and the edges into unused block
        arguments; count the uses left."""
        self.uses = {}
        for o in self.walk():
            callee = o.prop("callee")[1:] if o.name == "func.call" else None
            if callee in funcs:
                keep = set(funcs[callee].live_args)
            elif o.name == "func.return":
                keep = set(self.live_res)
            elif o.name.startswith("cf."):
                keep = {
                    i
                    for s, idx, _ in o.edges()
                    for (a, _), i in zip(self.block_of[s].args, idx)
                    if a in live
                } | ({0} if o.name != "cf.br" else set())
            elif callee in funcs or not o.res or any(r in live for r in o.res) or self.root(o):
                keep = set(range(len(o.args)))
            else:
                self.dropped.add(id(o))
                continue
            for i, a in enumerate(o.args):
                if i in keep:
                    self.uses[a] = self.uses.get(a, 0) + 1
                else:
                    self.dead.add((id(o), i))

    def pure(self, o):
        """Whether ``o`` computes a value with no effect."""
        if o.name in ("memref.load",) or o.name.startswith("arith.") or o.name == "ub.poison":
            return True
        return o.name == "func.call" and o.prop("callee") == "@codechk"

    def effect(self, o):
        """Whether ``o`` may write memory or call out."""
        if id(o) in self.dropped:
            return False
        if o.name == "func.call":
            return o.prop("callee") != "@codechk"
        return o.name == "memref.store"

    def mark_addresses(self):
        """Mark the operand edges ``(op, index)`` through which a value reaches the address
        of a memory access, so constants on them print relative to their region."""
        work = []
        for o in self.walk():
            if o.name in ("memref.load", "memref.store") and o.args[-2] in self.globals:
                work.append((o, len(o.args) - 1))
            elif o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write"):
                work.append((o, 0))
        while work:
            o, i = work.pop()
            if (id(o), i) in self.addr_edges:
                continue
            self.addr_edges.add((id(o), i))
            work += self.sources(o.args[i])

    def sources(self, v):
        """The operand edges a value ``v`` is computed from, as far as addresses flow."""
        d = self.defs.get(v)
        if d is None:
            return [(o, i) for o, i, _ in self.arg_src.get(v, [])]
        if d.name in CASTS or d.name in ("arith.addi", "arith.subi"):
            return [(d, i) for i in range(len(d.args))]
        if d.name == "arith.select":
            return [(d, 1), (d, 2)]
        return []

    def adjacent(self, d, u):
        """Whether use ``u`` of ``d``'s value is in ``d``'s block with no effect between."""
        blk, i = self.where[id(d)]
        ublk, j = self.where[id(u)]
        return ublk is blk and not any(self.effect(x) for x in blk.ops[i + 1 : j])

    def const_of(self, v):
        """The value of constant ``v``, or None."""
        d = self.defs.get(v)
        return d.int_prop("value") if d is not None and d.name == "arith.constant" else None

    def remat(self, v):
        """Whether ``v`` is a constant every function can print itself."""
        d = self.defs.get(v)
        return d is not None and d.name in REMAT

    def returns_only(self, b):
        """Whether block ``b`` holds nothing but a ``func.return``."""
        return len(b.ops) == 1 and b.ops[0].name == "func.return"

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

    def free(self, t):
        """``(block arguments, other values)`` a tail graph ``t`` takes, in order of use."""
        order = t.dfs()[0]
        blocks = [it[1] for n in order for it in t.items[n] if it[0] == "ops"]
        defined = {a for b in blocks for a, _ in b.args} | {
            r for b in blocks for o in b.ops for r in o.res
        }
        head = t.items[t.entry][0][1]
        args = [a for a, _ in head.args if self.uses.get(a)]
        params, seen = [], set()
        for v in self.refs(t, order):
            if v not in defined and v not in seen and not self.remat(v):
                seen.add(v)
                params.append(v)
        return args, params

    def refs(self, t, order):
        """The values the nodes ``order`` of graph ``t`` read, in order, with repeats."""
        for n in order:
            for it in t.items[n]:
                if it[0] == "ops":
                    for o in it[1].ops[:-1]:
                        if id(o) not in self.dropped and self.printed(o):
                            yield from self.reads(o)
                elif it[0] == "copy":
                    yield from (v for _, v, _ in it[1])
                elif it[0] in ("return", "call"):
                    yield from (v if isinstance(v, str) else v[0] for v in it[-1])
            for _, c in t.succ[n]:
                if c is not None:
                    for name in sorted(c.support, key=self.conds.bdd.level_of_var):
                        v = self.lit_value(self.conds.keys[name])
                        if v is not None:
                            yield v

    def quiet(self, items):
        """Whether statements ``items`` are a block that prints nothing: its operations are
        pure and only it reads them, other than as block arguments."""
        if len(items) != 1 or items[0][0] != "ops":
            return False
        blk = items[0][1]
        term = blk.ops[-1]
        for o in blk.ops[:-1]:
            if not (self.pure(o) or id(o) in self.dropped) and o.name != "memref.get_global":
                return False
            for r in o.res:
                for u in self.users.get(r, ()):
                    if u.parent is not blk or (
                        u is term
                        and term.args.index(r)
                        and (id(u), term.args.index(r)) not in self.dead
                    ):
                        return False
        return True

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
        addr = None
        if o.name in ("memref.load", "memref.store") and o.args[-2] in self.globals:
            addr = len(o.args) - 1
        elif o.name == "func.call" and o.prop("callee") in ("@io_read", "@io_write", "@codechk"):
            addr = 0
        out = []
        for i, a in enumerate(o.args):
            if (id(o), i) in self.dead or a in self.globals:
                continue
            if i == addr:
                a = self.split(self.address(o) if o.name != "func.call" else a)[1]
                if a is None:
                    continue
            out.append(a)
        return out

    def printed(self, o):
        """Whether operation ``o`` prints: an effect, or a value something uses."""
        if o.name == "memref.get_global":
            return False
        return not self.pure(o) or any(self.uses.get(r) for r in o.res)


def _types(text):
    """The types of a result list ``(i8, i16)`` or ``i8``."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return [t.strip() for t in text.split(",") if t.strip()]


class CFunc:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One C function: a lifted function or entry, or a tail outlined from one."""

    def __init__(self, func, graph, tail=False):
        self.f, self.g, self.tail, self.mod = func, graph, tail, func.mod
        self.name = None
        self.owned = {it[1].name for its in graph.items.values() for it in its if it[0] == "ops"}
        self.names, self.nvar, self.inline, self.memf, self.types = {}, 0, set(), {}, {}
        self.uses, self.user, self.covers = {}, {}, {}
        self.body, self.head_text = None, ""

    def structure(self):
        """Structure the graph into statements."""
        s = Structurer(self.f.conds, self.mod.made, self.f.quiet)
        self.body = s.finish(s.structure(self.g))

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

        for v, adj in self.item_refs(self.body, None):
            use(v, ("adj" if adj else "far",))
        for b in self.f.blocks:
            if b.name not in self.owned:
                continue
            for o in b.ops[:-1]:
                if id(o) not in self.f.dropped and self.f.printed(o) and not self.f.pure(o):
                    for a in self.f.reads(o):
                        use(a, o)
        while work:
            d = self.f.defs.get(work.pop())
            if d is None or id(d) in seen or not self.f.pure(d) or id(d) in self.f.dropped:
                continue
            if self.f.where[id(d)][0].name not in self.owned:
                continue
            seen.add(id(d))
            for a in self.f.reads(d):
                use(a, d)

    def item_refs(self, items, after):
        """``(value, adjacent)`` read by ``items``; ``after`` the block they directly follow."""
        for k, it in enumerate(items):
            prev = items[k - 1][1] if k and items[k - 1][0] == "ops" else after if not k else None
            kind = it[0]
            if kind == "copy":
                yield from ((v, self.def_in(v, prev)) for _, v, _ in it[1])
            elif kind in ("return", "call"):
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
                    yield from self.item_refs(body, None)
            elif kind == "loop":
                yield from self.item_refs(it[1], None)

    def def_in(self, v, blk):
        """Whether value ``v`` is defined by an operation of block ``blk``."""
        v = v[0] if isinstance(v, tuple) else v
        d = self.f.defs.get(v)
        return blk is not None and d is not None and self.f.where[id(d)][0] is blk

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
        """Choose the values printed inline: constants, values used once and casts of or
        constant offsets from variables (the root of a shared one is made a variable)."""
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
            mem = o.name == "memref.load" or any(
                self.memf.get(a) for a in o.args if a in self.inline
            )
            self.memf[v] = mem
            if o.name in ("arith.constant", "ub.poison"):
                self.inline.add(v)
                continue
            if self.uses.get(v) != 1:
                if not mem and self.cheap(o):
                    self.inline.add(v)
                continue
            if mem and not self.adjacent(o, self.user[v]):
                continue
            self.inline.add(v)

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
        """The C variable of value ``v`` (or structuring variable), named on first use."""
        if v not in self.names:
            self.nvar += 1
            self.names[v] = f"v{self.nvar}"
            self.types[self.names[v]] = CTYPE[width]
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
        base = name.split(".")[0]
        return E(name, lo=lo, hi=hi, uns=w == 16, refs=frozenset([base]))

    def reloc(self, c):
        """The constant address ``c`` relative to the region holding it."""
        sym, off = self.mod.mem.base(c)
        if sym is None:
            return lit(c, 16)
        self.mod.used.add(sym)
        text = f"{sym} + {lit(off, 16).text}" if off else sym
        return E(text, 12 if off else 16, "+" if off else "", c, c, True)

    def split(self, a):
        """``(constant, rest)`` of an address value: ``rest`` is None or a value."""
        return self.f.split(a)

    def addr(self, a):
        """The address value ``a`` with its constant relocated (a zero-page one as is)."""
        c, rest = self.split(a)
        if self.f.width(a) < 16 and rest is not None:
            r = self.ex(rest) if c == 0 else binary("+", 12, lit(c, 8), self.ex(rest), 8)
            return clean(r, 8)
        if self.f.width(a) < 16:
            return lit(c, 8)
        if rest is None:
            return self.reloc(c)
        if not c:
            return self.ex(rest)
        return binary("+", 12, self.reloc(c), self.ex(rest), 16)

    def target(self, o):
        """How access ``o`` reaches memory: ``("region", k)``, ``("io",)``, ``("code",)`` or
        ``("any",)``."""
        lo, hi = self.f.iv(self.f.address(o))
        mem = self.mod.mem
        if o.name == "func.call" and mem.is_volatile(lo, hi):
            return ("io",)
        k = mem.region(lo, hi)
        if k is not None:
            return ("region", k)
        if mem.is_code(lo, hi):
            return ("code",)
        return ("any",)

    def mem_ref(self, o, k):
        """The region element ``Rk[...]`` that access ``o`` addresses: the address modulo
        its width (16 bits, or 8 for a zero-page one), as the 6502 wraps it, less the origin."""
        org = self.mod.mem.regions[k][0]
        a = self.f.address(o)
        c, rest = self.split(a)
        top = (1 << self.f.width(a)) - 1
        c &= top
        if rest is None:
            sub = lit(c - org, 16)
        elif c == 0 and org:
            r = self.ex(rest)
            sub = binary("-", 12, r, E(f"R{k}_ORG", lo=org, hi=org, uns=True), 16)
        else:
            r = clean(self.ex(rest), self.f.width(rest))
            lo, hi = self.f.iv(rest)
            if lo + c <= top < hi + c:
                r, c = clean(binary("+", 12, lit(c, 16), r, 16), self.f.width(a)), 0
            elif lo + c > top:
                c -= top + 1
            off = c - org
            if off > 0:
                sub = binary("+", 12, lit(off, 16), r, 16)
            elif off < 0:
                sub = binary("-", 12, r, lit(-off, 16), 16)
            else:
                sub = r
        sub = clean(sub, 16) if sub.lo < 0 else sub
        self.mod.regions_used.add(k)
        return E(f"R{k}[{sub.text}]", 16, "", 0, 255, False, True, sub.refs)

    def load(self, o):
        """The C expression of a memory load ``o``."""
        how = self.target(o)
        if how[0] == "region":
            return self.mem_ref(o, how[1])
        a = self.addr(self.f.address(o))
        fn = "oob_rd" if how[0] == "code" else "rd"
        self.mod.helpers.add(fn)
        return E(f"{fn}({a.text})", 16, "", 0, 255, False, True, a.refs)

    def build(self, o):  # pylint: disable=too-many-return-statements
        """The C expression of the single-result pure operation ``o``."""
        name, w = o.name, self.f.width(o.res[0])
        if name == "arith.constant":
            return lit(o.int_prop("value"), w)
        if name == "ub.poison":
            return lit(0, w)
        if name == "memref.load":
            return self.load(o)
        if name == "func.call":
            a, v = self.addr(o.args[0]), self.ex(o.args[1])
            self.mod.helpers.add("codechk")
            return E(f"codechk({a.text}, {v.text})", 16, "", 0, 1, **join(a, v))
        args = [self.ex(a, (o, i)) for i, a in enumerate(o.args)]
        if name in BINOP:
            sym, prec = BINOP[name]
            a, b = args
            if sym == "+" and b.lo == b.hi and (1 << w) - b.lo < min(0x100, (1 << w) // 2):
                return binary("-", 12, a, lit((1 << w) - b.lo, w), w)
            if sym in ("&", "|", "^") and w == 1:
                if sym == "^" and b.text == "1":
                    return negated(truth(a))
                sym, prec = {"&": ("&&", 5), "|": ("||", 4)}.get(sym, (sym, prec))
                return (
                    binary(sym, prec, truth(a), truth(b), 1)
                    if sym != "^"
                    else binary(sym, prec, a, b, 1)
                )
            return binary(sym, prec, a, b, w)
        if name == "arith.cmpi":
            pred, w0 = o.int_prop("predicate"), self.f.width(o.args[0])
            d = self.f.defs.get(o.args[0])
            if (
                pred in (0, 1)
                and self.f.const_of(o.args[1]) == 0
                and o.args[0] in self.inline
                and (d.name in ("arith.subi", "arith.addi"))
            ):
                x, y = (self.ex(a, (d, i)) for i, a in enumerate(d.args))
                if d.name == "arith.addi":
                    k = self.f.const_of(d.args[1])
                    if k is None:
                        return self.cmpi(pred, *args, w0)
                    y = lit(-k, w0)
                return self.cmpi(pred, x, y, w0)
            return self.cmpi(pred, *args, w0)
        if name in ("arith.extui", "arith.index_castui", "arith.index_cast"):
            return clean(args[0], self.f.width(o.args[0]))
        if name == "arith.trunci":
            return args[0]
        if name == "arith.select":
            c, a, b = args
            text = f"{par(truth(c), 4)} ? {par(a, 4)} : {par(b, 3)}"
            return E(
                text, 3, "?:", min(a.lo, b.lo), max(a.hi, b.hi), a.uns or b.uns, **join(c, a, b)
            )
        raise NotImplementedError(name)

    @staticmethod
    def cmpi(pred, a, b, width):
        """An ``arith.cmpi`` of ``width``-bit operands."""
        a, b = clean(a, width), clean(b, width)
        sym = CMP[pred]
        if pred in (2, 3, 4, 5):
            half = 1 << (width - 1)
            top, k = (1 << width) - 1, b.lo if b.lo == b.hi else None
            neg = (pred == 2 and k == 0) or (pred == 3 and k == top)
            pos = (pred == 5 and k == 0) or (pred == 4 and k == top)
            if neg or pos:
                return compare(">=" if neg else "<", a, lit(half, width))
            st = f"int{width}_t"
            a, b = cast(a, st, width, True), cast(b, st, width, True)
        if b.lo == b.hi and b.lo in (0, 1) and 0 <= a.lo and a.hi <= 1 and sym in ("==", "!="):
            return truth(a) if (sym == "==") == (b.lo == 1) else negated(truth(a))
        return compare(sym, a, b)

    def literal(self, key, pol):
        """The C condition of literal ``key`` with polarity ``pol``."""
        if key[0] == "c":
            e = truth(self.ex(key[1]))
        elif isinstance(key[1], str):
            x = key[1]
            e = self.cmpi(0, self.ex(x), self.label(x, key[2]), self.f.width(x))
        elif len(self.f.conds.families[key[1]]) == 2:
            e = truth(self.ex_syn(key[1]))
            e = e if key[2] else negated(e)
        else:
            e = compare("==", self.ex_syn(key[1]), lit(key[2], 8))
        return e if pol else negated(e)

    def label(self, x, k):
        """Case value ``k`` of selector ``x``: an address when ``x`` is a computed jump's."""
        return self.reloc(k) if x in self.f.flags else lit(k, self.f.width(x))

    def ex_syn(self, fam):
        """The variable of structuring variable family ``fam``."""
        return var(self.var_of(fam), 1 if len(self.f.conds.families[fam]) == 2 else 8)

    def cond(self, f, ctx):
        """The C condition of ``f`` where ``ctx`` holds."""
        cubes = self.cover(f, ctx)
        return logic("||", [logic("&&", [self.literal(k, p) for k, p in c]) for c in cubes])

    # Statements.

    def define(self, v, e, out):
        """Assign the variable of value ``v`` as ``e``."""
        out.append(("a", self.var_of(v, self.f.width(v)), e.text, e.refs))

    def stmt(self, o, out):
        """Append the statements of operation ``o``."""
        name, f = o.name, self.f
        if name == "memref.get_global" or id(o) in f.dropped:
            return
        if (
            name == "func.call"
            and o.prop("callee") == "@codechk"
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

    def halves(self, o):
        """The value of a constant store ``o`` that, with a constant store to the other byte
        of a pointer pair in its block, writes an address: relative to its region."""
        f = self.f
        c, a = f.const_of(o.args[0]), self.split(f.address(o))
        if c is None or a[1] is not None:
            return None
        pairs, a = self.mod.mem.pairs, a[0]
        cand = [(a, pairs[a])] if a in pairs else []
        cand += [(p, a) for p in self.mod.mem.pairs_to.get(a, ())]
        blk, i = f.where[id(o)]
        cells = {}
        for j, x in enumerate(blk.ops):
            if x.name == "memref.store" and x.args[-2] in f.globals:
                xa = self.split(f.address(x))
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
        e = self.reloc(word)
        if a == lo:
            return E(f"(uint8_t)({e.text})", 15, "cast", 0, 255)
        return E(f"({e.text}) >> 8", 11, ">>", 0, 255)

    def store(self, o, out):
        """A memory store (or an ``io_write`` that cannot reach I/O)."""
        val = o.args[1] if o.name == "func.call" else o.args[0]
        how, v = self.target(o), (o.name != "func.call" and self.halves(o)) or self.ex(val)
        if how[0] == "region":
            ref = self.mem_ref(o, how[1])
            out.append(("s", f"{ref.text} = {v.text};", ref.refs | v.refs))
            return
        fn = "oob_wr" if how[0] == "code" else "wr"
        self.mod.helpers.add(fn)
        a = self.addr(self.f.address(o))
        out.append(("s", f"{fn}({a.text}, {v.text});", a.refs | v.refs))

    def call(self, o, out):
        """A call of an external or lifted function."""
        f = self.f
        callee = o.prop("callee")[1:]
        if callee == "trap":
            self.mod.traps.add(f.const_of(o.args[0]))
            out.append(("s", f"trap({self.ex(o.args[0]).text});", frozenset()))
            return
        if callee not in ("io_read", "io_write"):
            target = self.mod.cfunc_of(callee)
            args = [self.ex(a, (o, i)) for i, a in enumerate(o.args) if (id(o), i) not in f.dead]
            text = f"{self.mod.name_of(target, self)}({', '.join(a.text for a in args)})"
            refs = frozenset().union(*(a.refs for a in args))
            res = [o.res[j] for j in target.f.live_res]
            used = [r for r in res if self.uses.get(r)]
            if len(res) == 1 and used:
                out.append(("a", self.var_of(res[0], f.width(res[0])), text, refs))
            elif len(res) > 1 and used:
                t = self.fresh(8)
                self.types[t] = self.mod.ret_type(target.f)
                out.append(("a", t, text, refs))
                for j, r in enumerate(res):
                    self.names[r] = f"{t}.r{j}"
            else:
                out.append(("s", f"{text};", refs))
            return
        how = self.target(o)
        if callee == "io_write":
            if how[0] == "io":
                a, v = self.addr(o.args[0]), self.ex(o.args[1])
                out.append(("s", f"io_write({a.text}, {v.text});", a.refs | v.refs))
            else:
                self.store(o, out)
            return
        e = None
        if how[0] == "io":
            a = self.addr(o.args[0])
            e = E(f"io_read({a.text})", hi=255, refs=a.refs)
        if self.uses.get(o.res[0]):
            self.define(o.res[0], e or self.load(o), out)
        elif how[0] in ("io", "any"):
            e = e or self.load(o)
            out.append(("s", f"{e.text};", e.refs))

    def assign(self, pairs, out):
        """The parallel assignment of block arguments ``[(arg, value, edge)]``."""
        pend = []
        for a, v, edge in pairs:
            name, e = self.var_of(a, self.f.width(a)), self.ex(v, edge)
            if e.text != name:
                pend.append((name, self.f.width(a), e))
        names = {n for n, _, _ in pend}
        for i, (n, w, e) in enumerate(pend):
            if e.refs & (names - {n}):
                t = self.fresh(w)
                out.append(("a", t, e.text, e.refs))
                pend[i] = (n, w, var(t, w))
        out += [("a", n, e.text, e.refs) for n, _, e in pend]

    def jump(self, vals):
        """The statements returning ``[(value, edge)]``."""
        if not vals:
            return [("j", "return;", frozenset())]
        es = [self.ex(v, edge) for v, edge in vals]
        refs = frozenset().union(*(e.refs for e in es))
        if len(es) == 1:
            return [("j", f"return {es[0].text};", refs)]
        text = ", ".join(e.text for e in es)
        return [("j", f"return ({self.mod.ret_type(self.f)}){{{text}}};", refs)]

    def convert(self, items):  # pylint: disable=too-many-branches
        """The C statements of structured ``items``."""
        out = []
        for it in items:
            kind = it[0]
            if kind == "ops":
                for o in it[1].ops[:-1]:
                    self.stmt(o, out)
            elif kind == "copy":
                self.assign(it[1], out)
            elif kind == "set":
                out.append(("a", self.var_of(it[1]), str(it[2]), frozenset()))
            elif kind == "return":
                out += self.jump(it[1])
            elif kind == "call":
                out += self.tail_call(it[1], it[2])
            elif kind in ("break", "continue"):
                out.append(("j", f"{kind};", frozenset()))
            elif kind == "if":
                out += self.convert_if(it)
            elif kind == "switch":
                out.append(self.convert_switch(it))
            else:
                out.append(self.convert_loop(self.convert(it[1])))
        return out

    def tail_call(self, t, vals):
        """A call of tail ``t`` and the return."""
        target = self.mod.tails[id(t)]
        es = [self.ex(v, e) for v, e in vals]
        refs = frozenset().union(*(e.refs for e in es))
        text = f"{self.mod.name_of(target, self)}({', '.join(e.text for e in es)})"
        if self.f.live_res:
            return [("j", f"return {text};", refs)]
        return [("s", f"{text};", refs), ("j", "return;", frozenset())]

    def convert_if(self, it):
        """An ``if``; one whose then-branch jumps continues with its else-branch after it."""
        then, els, f = self.convert(it[3]), self.convert(it[4]), it[1]
        if not then and not els:
            return []
        if not then:
            then, els, f = els, then, ~f
        c, n = self.cond(f, it[2]), self.cond(~f, it[2])
        if els and ends_in_jump(then):
            return [("if", c.text, c.refs, then, [], n.text), *els]
        return [("if", c.text, c.refs, then, els, n.text)]

    def convert_switch(self, it):
        """A ``switch`` over a selector family."""
        fam = it[1]
        if isinstance(fam, str):
            sel = clean(self.ex(fam), self.f.width(fam))
            arms = [
                ([None if k is None else self.label(fam, k).text for k in ks], self.convert(b))
                for ks, b in it[2]
            ]
        else:
            sel = self.ex_syn(fam)
            arms = [([str(k) for k in ks], self.convert(b)) for ks, b in it[2]]
        return ("switch", sel.text, sel.refs, arms)

    @staticmethod
    def convert_loop(body):
        """A loop as ``while`` (an exit test first), ``do``/``while`` (an exit test last and
        no other ``continue``) or ``for (;;)``."""
        body = _strip_continue(body)
        brk, cont = [("j", "break;", frozenset())], [("j", "continue;", frozenset())]
        if body and body[0][0] == "if" and body[0][3] == brk and not body[0][4]:
            return ("loop", "while", body[0][5], body[0][2], body[1:])
        if body and body[-1][0] == "if" and body[-1][3] == brk and not body[-1][4]:
            if not _continues(body[:-1]):
                return ("loop", "do", body[-1][5], body[-1][2], body[:-1])
        if len(body) > 1 and body[-1] == brk and body[-2][0] == "if" and body[-2][3] == cont:
            if not body[-2][4] and not _continues(body[:-2]):
                return ("loop", "do", body[-2][1], body[-2][2], body[:-2])
        return ("loop", "for", "", frozenset(), body)

    def head(self):
        """The C declarator, naming the parameters."""
        f = self.f
        if not f.private and not self.tail:
            self.names[f.blocks[0].args[0][0]] = "a"
            return f"void {self.name}(uint8_t a)", ["a"]
        if self.tail:
            params = self.g.args + self.g.params
        else:
            params = [f.blocks[0].args[i][0] for i in f.live_args]
        names = [self.var_of(p, f.width(p)) for p in params]
        decl = ", ".join(f"{self.types.pop(n)} {n}" for n in names) or "void"
        return f"static {self.mod.ret_type(f)} {self.name}({decl})", names

    def text(self):
        """The C definition of the function."""
        self.count()
        self.plan()
        head, params = self.head()
        body = self.convert(self.body)
        if not self.f.live_res or not (self.f.private or self.tail):
            while body and body[-1] == ("j", "return;", frozenset()):
                body.pop()
        body = drop_unread(body, {n for k, n in self.names.items() if isinstance(k, tuple)})
        declare(body, self.types)
        read = names_in(body)
        lines = [f"(void){n};" for n in params if n not in read] + render(body)
        self.head_text = head
        return "\n".join([f"{head} {{", *lines, "}"])


def _recall(items, t, keep):
    """``items`` with each call of tail ``t`` passing only the arguments ``keep``."""
    out = []
    for it in items:
        if it[0] == "call" and it[1] is t:
            it = ("call", t, [it[2][i] for i in keep])
        elif it[0] == "if":
            it = (*it[:3], _recall(it[3], t, keep), _recall(it[4], t, keep))
        elif it[0] == "switch":
            it = ("switch", it[1], [(c, _recall(b, t, keep)) for c, b in it[2]])
        elif it[0] == "loop":
            it = ("loop", _recall(it[1], t, keep))
        out.append(it)
    return out


def _strip_continue(body):
    """``body`` without ``continue`` statements in its tail position."""
    if not body:
        return body
    last = body[-1]
    if last == ("j", "continue;", frozenset()):
        return _strip_continue(body[:-1])
    if last[0] == "if":
        return body[:-1] + [
            (*last[:3], _strip_continue(last[3]), _strip_continue(last[4]), *last[5:])
        ]
    return body


def _continues(items):
    """Whether ``items`` hold a ``continue`` of the enclosing loop."""
    for st in items:
        if st[0] == "j" and st[1] == "continue;":
            return True
        if st[0] == "if" and (_continues(st[3]) or _continues(st[4])):
            return True
        if st[0] == "switch" and any(_continues(b) for _, b in st[3]):
            return True
    return False


class Module:  # pylint: disable=too-many-instance-attributes
    """The functions of the optimized module and the memory they share."""

    def __init__(self, top, mem):
        self.mem, self.used, self.helpers, self.regions_used = mem, set(), set(), set()
        self.traps, self.funcs, self.tails, self.mains, self.structs = set(), [], {}, {}, {}
        self.order, self.edges, self.ids, self.entries = [], {}, new_ids(), []
        self.sites, self.made = {}, Counter()
        for o in top.regions[0][0].ops:
            if o.name == "func.func" and o.regions[0] and o.regions[0][0].ops:
                self.funcs.append(Func(o, self))
        self.by_name = {f.name: f for f in self.funcs}

    def prune(self):
        """Keep the values something observable needs, over the whole module: a store, an
        I/O access, a trap or a branch needs its operands, a used result of a lifted function
        its returned operands, a used parameter its call sites' arguments. Calls pass only the
        parameters used, functions return only the results used, the rest is dropped."""
        self.sites = sites = {f.name: [] for f in self.funcs}
        for f in self.funcs:
            for o in f.walk():
                if o.name == "func.call" and o.prop("callee")[1:] in sites:
                    sites[o.prop("callee")[1:]].append((f, o))
        live, work = {f.name: set() for f in self.funcs}, []

        def mark(f, v):
            if v not in live[f.name]:
                live[f.name].add(v)
                work.append((f, v))

        for f in self.funcs:
            f.live_args, f.live_res = [], []
            for o in f.walk():
                if o.name in ("cf.cond_br", "cf.switch") or f.root(o):
                    for a in o.args[:1] if o.name.startswith("cf.") else o.args:
                        mark(f, a)
        while work:
            f, v = work.pop()
            d = f.defs.get(v)
            if d is None and v in f.arg_src:
                for o, i, _ in f.arg_src[v]:
                    mark(f, o.args[i])
            elif d is None:
                i = [a for a, _ in f.blocks[0].args].index(v)
                f.live_args.append(i)
                for g, o in sites.get(f.name, ()):
                    mark(g, o.args[i])
            elif d.name == "func.call" and d.prop("callee")[1:] in sites:
                h, j = self.by_name[d.prop("callee")[1:]], d.res.index(v)
                if j not in h.live_res:
                    h.live_res.append(j)
                    for ret in (o for o in h.walk() if o.name == "func.return"):
                        mark(h, ret.args[j])
            else:
                for a in d.args:
                    mark(f, a)
        for f in self.funcs:
            f.live_args.sort()
            f.live_res = sorted(f.live_res) if f.private else []
            f.settle(live[f.name], self.by_name)

    def partition(self):
        """Partition memory by the accesses of every function."""
        ranges, stores = [], set()
        for f in self.funcs:
            for o, (lo, hi) in f.accesses():
                if id(o) not in f.dropped:
                    ranges.append((lo, hi))
                    if o.name == "memref.store" and lo == hi:
                        stores.add(lo)
        self.mem.partition(ranges, sorted(stores))

    def ret_type(self, f):
        """The C return type of function ``f``'s live results."""
        ws = [WIDTH[t] for j, t in enumerate(f.results()) if j in f.live_res]
        if not ws:
            return "void"
        if len(ws) == 1:
            return CTYPE[ws[0]]
        name = f"struct r{'_'.join(str(w) for w in ws)}"
        self.structs[name] = ws
        return name

    def cfunc_of(self, name):
        """The C function of lifted function ``name``."""
        return self.mains[name]

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
        """Outline the shared tails of every function and structure each C function."""
        for f in self.funcs:
            g = f.graph(self.ids)
            tails = outline(g, f.free)
            self.mains[f.name] = CFunc(f, g)
            for t in tails:
                self.tails[id(t)] = CFunc(f, t, tail=True)
            self.made["tails"] += len(tails)
            f.mark_addresses()
        for c in [*self.mains.values(), *self.tails.values()]:
            c.structure()
        for c in self.tails.values():
            c.count()
            ins = c.g.args + c.g.params
            keep = [i for i, v in enumerate(ins) if c.uses.get(v)]
            if len(keep) < len(ins):
                c.g.args = [v for v in c.g.args if c.uses.get(v)]
                c.g.params = [v for v in c.g.params if c.uses.get(v)]
                for other in [*self.mains.values(), *self.tails.values()]:
                    if other.f is c.f:
                        other.body = _recall(other.body, c.g, keep)

    def functions(self):
        """The C text of every function: struct types and the prototypes recursion needs,
        then the definitions, callees first."""
        self.entries[:] = [self.mains[f.name] for f in self.funcs if not f.private]
        for c in self.entries:
            c.name = c.f.name
        self.order = list(self.entries)
        texts, k = {}, 0
        while k < len(self.order):
            c = self.order[k]
            texts[id(c)] = c.text()
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
        for c in [*self.mains.values(), *self.tails.values()]:
            c.body, c.covers, c.g = None, {}, None
        for f in self.funcs:
            f.conds.close()
        out = [
            f"{name} {{ {' '.join(f'{CTYPE[w]} r{j};' for j, w in enumerate(ws))} }};"
            for name, ws in sorted(self.structs.items())
        ]
        out += [f"{c.head_text};" for c in protos]
        self.made["functions"] = len(post)
        return out + [texts[id(c)] for c in post]
