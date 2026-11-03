"""Structured MLIR (``scf``/``arith``/``memref``/``func``) printed as compact C99.

Expressions carry their exact integer range under C's promotions, so a truncating cast is
printed only where a wider value would change a comparison, shift or subscript, and an operand
is made unsigned only where 16-bit ``int`` arithmetic could overflow.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field, replace

import numpy as np

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


TERMINATORS = {"scf.yield", "scf.condition", "func.return"}
CASTS = {"arith.extui", "arith.index_castui", "arith.index_cast", "arith.trunci"}
EFFECTS = {"memref.store", "func.call", "scf.if", "scf.while", "scf.index_switch"}


def _const_offset(text):
    sym, _, off = text.partition(" + ")
    return sym, int(off, 0) if off else 0


class Func:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One ``func.func`` of the structured module."""

    def __init__(self, op, mod):
        self.op, self.mod = op, mod
        self.name = op.prop("sym_name")
        self.defs, self.types, self.uses, self.user, self.where = {}, {}, {}, {}, {}
        self.names, self.inline, self.memf, self.dropped, self.ivs = {}, set(), {}, set(), {}
        self.globals, self.users, self.parents, self.coalesced = set(), {}, {}, set()
        self.addr_edges, self.arg_src = set(), {}
        self.index(op.regions[0])
        self.nvar = 0

    def index(self, region):
        """Record definitions, types, uses and positions under ``region``."""
        for blk in region:
            for n, t in blk.args:
                self.types[n] = t
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
                for reg in o.regions:
                    for b in reg:
                        self.parents[id(b)] = o
                    self.index(reg)
                if o.name == "scf.while":
                    before, after = o.regions[0][0], o.regions[1][0]
                    for k, (n, _) in enumerate(before.args):
                        self.arg_src[n] = [(o, k), (after.ops[-1], k)]
                    for k, (n, _) in enumerate(after.args):
                        self.arg_src[n] = [(before.ops[-1], k + 1)]

    def walk(self, region=None):
        """Every operation under ``region`` (the body by default), in order."""
        for blk in region if region is not None else self.op.regions[0]:
            for o in blk.ops:
                yield o
                for reg in o.regions:
                    yield from self.walk(reg)

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
        if o is None or o.name in ("scf.if", "scf.index_switch", "scf.while"):
            r = self.iv_joined(v)
        elif o.name in ("memref.load", "func.call"):
            r = full
        elif o.name == "arith.constant":
            c = o.int_prop("value") & top
            r = (c, c)
        elif o.name == "ub.poison":
            r = (0, 0)
        elif o.name in ("arith.extui", "arith.index_castui", "arith.index_cast", "arith.trunci"):
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
        """The interval of a block argument or ``scf`` result: the hull of what flows into
        it, where a loop-carried value that stays within its initial interval keeps it."""
        srcs = self.sources(v)
        full = (0, (1 << self.width(v)) - 1)
        if not srcs:
            return full
        first = self.iv(srcs[0][0].args[srcs[0][1]])
        if len(srcs) == 2 and srcs[0][0].name == "scf.while":
            exact = self.loop_iv(v, srcs[0][0], srcs[0][1], first)
            if exact is not None:
                return exact
            self.ivs[v] = first
            before = set(self.ivs)
            back = self.iv(srcs[1][0].args[srcs[1][1]])
            if first[0] <= back[0] and back[1] <= first[1]:
                return first
            for k in set(self.ivs) - before:
                del self.ivs[k]
            return full
        ivs = [self.iv(o.args[i]) for o, i in srcs]
        return min(lo for lo, _ in ivs), max(hi for _, hi in ivs)

    def loop_iv(self, v, loop, k, init):
        """The hull of the values loop-carried argument ``v`` (``k``-th of ``loop``) takes
        from ``init``, when the condition and the next value depend on ``v`` alone: the set
        of values is iterated to its fixpoint, every member evaluated at once."""
        size = 1 << self.width(v)
        cond, yld = loop.regions[0][0].ops[-1], loop.regions[1][0].ops[-1]
        after = loop.regions[1][0].args[k][0]
        seen = np.zeros(size, dtype=bool)
        seen[init[0] : init[1] + 1] = True
        new = np.flatnonzero(seen)
        try:
            for _ in range(LOOP_STEPS):
                memo = {}
                go = np.broadcast_to(self.evaluate(cond.args[0], {v: new}, memo), new.shape)
                fwd = np.broadcast_to(self.evaluate(cond.args[k + 1], {v: new}, memo), new.shape)
                back = self.evaluate(yld.args[k], {after: fwd[go != 0]}, {})
                back = np.unique(np.broadcast_to(back, fwd[go != 0].shape))
                new = back[~seen[back]]
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

    def drop(self):
        """Drop unused reads that cannot meet a read-sensitive address, then dead pure ops."""
        work = []
        for o in self.walk():
            if o.prop("callee") == "@io_read" and not self.uses.get(o.res[0]):
                lo, hi = self.iv(o.args[0])
                if not self.mod.mem.sensitive_in(lo, hi):
                    work.append(o)
        while work:
            o = work.pop()
            if id(o) in self.dropped:
                continue
            self.dropped.add(id(o))
            for a in o.args:
                self.uses[a] -= 1
                d = self.defs.get(a)
                if d is not None and not self.uses[a] and self.pure(d):
                    if not any(self.uses.get(r) for r in d.res):
                        work.append(d)

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
        if o.regions:
            return any(self.effect(x) for x in self.walk(o.regions[0])) or any(
                self.effect(x) for reg in o.regions[1:] for x in self.walk(reg)
            )
        return o.name in EFFECTS

    def plan(self):
        """Choose the values printed inline: constants, values used once and casts of or
        constant offsets from variables (the root of a shared one is made a variable)."""
        forced = set()
        for o in self.walk():
            if len(o.res) == 1 and (self.uses.get(o.res[0]) or 0) > 1:
                d = o
                while d is not None and self.offset_of(d) is not None:
                    root = self.offset_of(d)
                    forced.add(root)
                    d = self.defs.get(root)
        for o in self.walk():
            if len(o.res) != 1 or id(o) in self.dropped or not self.pure(o):
                continue
            if o.res[0] in forced and self.offset_of(o) is None and o.name != "arith.constant":
                self.memf[o.res[0]] = False
                continue
            v = o.res[0]
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
        self.mark_addresses()

    def offset_of(self, o):
        """The operand of a cast or constant offset ``o``, or None."""
        if o.name in CASTS:
            return o.args[0]
        if o.name in ("arith.addi", "arith.subi"):
            rest = [a for a in o.args if self.const_of(a) is None]
            return rest[0] if len(rest) == 1 else None
        return None

    def cheap(self, o):
        """Whether ``o`` is a cast of, or a constant offset from, a variable or constant."""
        if o.name in CASTS:
            args = o.args
        elif o.name in ("arith.addi", "arith.subi"):
            consts = [a for a in o.args if self.const_of(a) is not None]
            if not consts:
                return False
            args = [a for a in o.args if a not in consts] or consts[:1]
        else:
            return False
        for a in args:
            d = self.defs.get(a)
            if (
                a in self.inline
                and d is not None
                and not (d.name == "arith.constant" or self.cheap(d))
            ):
                return False
        return True

    def const_of(self, v):
        """The value of constant ``v``, or None."""
        d = self.defs.get(v)
        return d.int_prop("value") if d is not None and d.name == "arith.constant" else None

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
            return self.arg_sources(v)
        if d.name in CASTS or d.name in ("arith.addi", "arith.subi"):
            return [(d, i) for i in range(len(d.args))]
        if d.name == "arith.select":
            return [(d, 1), (d, 2)]
        if d.name in ("scf.if", "scf.index_switch", "scf.while"):
            k = d.res.index(v)
            if d.name == "scf.while":
                cond = d.regions[0][0].ops[-1]
                return [(cond, k + 1)]
            return [(reg[0].ops[-1], k) for reg in d.regions if reg and reg[0].ops]
        return []

    def arg_sources(self, v):
        """The edges feeding block argument ``v`` of an ``scf.while``."""
        return self.arg_src.get(v, [])

    def adjacent(self, d, u):
        """Whether use ``u`` of ``d``'s value is in ``d``'s block with no effect between."""
        blk, i = self.where[id(d)]
        ublk, j = self.where[id(u)]
        return ublk is blk and not any(self.effect(x) for x in blk.ops[i + 1 : j])

    def var_of(self, v):
        """The C variable of value ``v``, named on first use."""
        if v not in self.names:
            self.nvar += 1
            self.names[v] = f"v{self.nvar}"
        return self.names[v]

    def ex(self, v, edge=None):
        """The C expression of value ``v``, used as operand ``edge`` ``(op, index)``: a
        constant on an address edge relative to its region."""
        w = self.width(v)
        if edge is not None and w == 16 and (id(edge[0]), edge[1]) in self.addr_edges:
            c = self.const_of(v)
            if c is not None:
                return self.reloc(c & 0xFFFF)
        if v in self.inline:
            return self.build(self.defs[v])
        lo, hi = self.iv(v)
        return replace(var(self.var_of(v), w), lo=lo, hi=hi)

    def reloc(self, c):
        """The constant address ``c`` relative to the region holding it."""
        sym, off = self.mod.mem.base(c)
        if sym is None:
            return lit(c, 16)
        self.mod.used.add(sym)
        text = f"{sym} + {lit(off, 16).text}" if off else sym
        return E(text, 12 if off else 16, "+" if off else "", c, c, True)

    def split(self, a):
        """``(constant, rest)`` of a 16-bit address value: ``rest`` is None or a value."""
        d = self.defs.get(a)
        if d is not None and d.name == "arith.constant":
            return d.int_prop("value") & 0xFFFF, None
        if d is not None and d.name == "arith.addi":
            for x, y in (d.args, d.args[::-1]):
                dx = self.defs.get(x)
                if dx is not None and dx.name == "arith.constant":
                    return dx.int_prop("value") & 0xFFFF, y
        return 0, a

    def addr(self, a):
        """The address value ``a`` with its constant relocated."""
        c, rest = self.split(a)
        if rest is None:
            return self.reloc(c)
        if not c:
            return self.ex(rest)
        return binary("+", 12, self.reloc(c), self.ex(rest), 16)

    def target(self, o):
        """How access ``o`` reaches memory: ``("region", k)``, ``("io",)``, ``("code",)`` or
        ``("any",)``."""
        lo, hi = self.iv(self.address(o))
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
        """The region element ``Rk[...]`` that access ``o`` addresses."""
        org = self.mod.mem.regions[k][0]
        c, rest = self.split(self.address(o))
        if rest is None:
            sub = lit(c - org, 16)
        elif c == 0 and org:
            sub = binary("-", 12, self.ex(rest), E(f"R{k}_ORG", lo=org, hi=org, uns=True), 16)
        elif c > org:
            sub = binary("+", 12, lit(c - org, 16), self.ex(rest), 16)
        elif c < org:
            sub = binary("-", 12, self.ex(rest), lit(org - c, 16), 16)
        else:
            sub = self.ex(rest)
        sub = clean(sub, 16) if sub.lo < 0 else sub
        self.mod.regions_used.add(k)
        return E(f"R{k}[{sub.text}]", 16, "", 0, 255, False, True, sub.refs)

    def load(self, o):
        """The C expression of a memory load ``o``."""
        how = self.target(o)
        if how[0] == "region":
            return self.mem_ref(o, how[1])
        a = self.addr(self.address(o))
        fn = "oob_rd" if how[0] == "code" else "rd"
        self.mod.helpers.add(fn)
        return E(f"{fn}({a.text})", 16, "", 0, 255, False, True, a.refs)

    def build(self, o):  # pylint: disable=too-many-return-statements
        """The C expression of the single-result pure operation ``o``."""
        name, w = o.name, self.width(o.res[0])
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
            pred, w0 = o.int_prop("predicate"), self.width(o.args[0])
            d = self.defs.get(o.args[0])
            if (
                pred in (0, 1)
                and self.const_of(o.args[1]) == 0
                and o.args[0] in self.inline
                and (d.name in ("arith.subi", "arith.addi"))
            ):
                x, y = (self.ex(a, (d, i)) for i, a in enumerate(d.args))
                if d.name == "arith.addi":
                    k = self.const_of(d.args[1])
                    if k is None:
                        return self.cmpi(pred, *args, w0)
                    y = lit(-k, w0)
                return self.cmpi(pred, x, y, w0)
            return self.cmpi(pred, *args, w0)
        if name in ("arith.extui", "arith.index_castui", "arith.index_cast"):
            return clean(args[0], self.width(o.args[0]))
        if name == "arith.trunci":
            return args[0]
        if name == "arith.select":
            c, a, b = args
            text = f"{par(truth(c), 4)} ? {par(a, 4)} : {par(b, 3)}"
            return E(
                text, 3, "?:", min(a.lo, b.lo), max(a.hi, b.hi), a.uns or b.uns, **join(c, a, b)
            )
        raise NotImplementedError(name)

    def cmpi(self, pred, a, b, width):
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

    def fresh(self):
        """A new temporary name."""
        self.nvar += 1
        return f"v{self.nvar}"

    def stmts(self, blk):
        """``(statements, terminator)`` of block ``blk``, each statement a list of lines."""
        out, term = [], None
        for o in blk.ops:
            if id(o) in self.dropped:
                continue
            if o.name in TERMINATORS:
                term = o
            else:
                self.stmt(o, out)
        return out, term

    def declare(self, names_types, out):
        """Declare uninitialized variables ``[(name, width)]``."""
        by = {}
        for n, w in names_types:
            if n not in self.coalesced:
                by.setdefault(CTYPE[w], []).append(n)
        out += [[f"{t} {', '.join(ns)};"] for t, ns in by.items()]

    def define(self, v, e, out):
        """Assign or declare the variable of value ``v`` as ``e``."""
        name = self.var_of(v)
        if name in self.coalesced:
            out.append([f"{name} = {e.text};"])
        else:
            out.append([f"{CTYPE[self.width(v)]} {name} = {e.text};"])

    def stmt(self, o, out):  # pylint: disable=too-many-branches
        """Append the statements of operation ``o``."""
        name = o.name
        if name == "memref.get_global":
            return
        if (
            name == "func.call"
            and o.prop("callee") == "@codechk"
            or (name != "func.call" and self.pure(o))
        ):
            v = o.res[0]
            if v not in self.inline and self.uses.get(v):
                self.define(v, self.build(o), out)
            return
        if name == "memref.store":
            self.store(o, out)
        elif name == "func.call":
            self.call(o, out)
        elif name == "scf.if":
            self.s_if(o, out)
        elif name == "scf.while":
            self.s_while(o, out)
        elif name == "scf.index_switch":
            self.s_switch(o, out)
        else:
            raise NotImplementedError(name)

    def halves(self, o):
        """The value of a constant store ``o`` that, with a constant store to the other byte
        of a pointer pair in its block, writes an address: relative to its region."""
        c, a = self.const_of(o.args[0]), self.split(self.address(o))
        if c is None or a[1] is not None:
            return None
        pairs, a = self.mod.mem.pairs, a[0]
        cand = [(a, pairs[a])] if a in pairs else []
        cand += [(p, a) for p in self.mod.mem.pairs_to.get(a, ())]
        blk, i = self.where[id(o)]
        cells = {}
        for j, x in enumerate(blk.ops):
            if x.name == "memref.store" and x.args[-2] in self.globals:
                xa = self.split(self.address(x))
                if xa[1] is None:
                    cells.setdefault(xa[0], []).append((j, self.const_of(x.args[0])))
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
            out.append([f"{self.mem_ref(o, how[1]).text} = {v.text};"])
            return
        fn = "oob_wr" if how[0] == "code" else "wr"
        self.mod.helpers.add(fn)
        out.append([f"{fn}({self.addr(self.address(o)).text}, {v.text});"])

    def call(self, o, out):
        """A call of an external function."""
        callee = o.prop("callee")[1:]
        if callee == "trap":
            self.mod.traps.add(self.const_of(o.args[0]))
            out.append([f"trap({self.ex(o.args[0]).text});"])
            return
        if callee not in ("io_read", "io_write"):
            args = ", ".join(self.ex(a, (o, i)).text for i, a in enumerate(o.args))
            if any(self.uses.get(r) for r in o.res):
                t = self.fresh()
                out.append([f"struct cgen_regs {t} = {callee}({args});"])
                for j, r in enumerate(o.res):
                    self.names[r] = f"{t}.r{j}"
            else:
                out.append([f"{callee}({args});"])
            return
        how = self.target(o)
        if callee == "io_write":
            if how[0] == "io":
                v = self.ex(o.args[1])
                out.append([f"io_write({self.addr(o.args[0]).text}, {v.text});"])
            else:
                self.store(o, out)
            return
        e = E(f"io_read({self.addr(o.args[0]).text})", hi=255) if how[0] == "io" else None
        if self.uses.get(o.res[0]):
            self.define(o.res[0], e or self.load(o), out)
        elif how[0] in ("io", "any"):
            out.append([f"{(e or self.load(o)).text};"])

    def assign(self, targets, term, idx, out):
        """The parallel assignment of operands ``idx`` of ``term`` to ``[(name, width)]``."""
        pend = [(n, w, self.ex(term.args[i], (term, i))) for (n, w), i in zip(targets, idx)]
        pend = [(n, w, e) for n, w, e in pend if e.text != n]
        names = {n for n, _, _ in pend}
        for i, (n, w, e) in enumerate(pend):
            if e.refs & (names - {n}):
                t = self.fresh()
                out.append([f"{CTYPE[w]} {t} = {e.text};"])
                pend[i] = (n, w, var(t, w))
        out += [[f"{n} = {e.text};"] for n, _, e in pend]

    def s_if(self, o, out):
        """An ``scf.if``."""
        names = [(self.var_of(r), self.width(r)) for r in o.res if self.uses.get(r)]
        live = [i for i, r in enumerate(o.res) if self.uses.get(r)]
        self.declare(names, out)
        then, tt = self.stmts(o.regions[0][0])
        has_else = len(o.regions) > 1 and o.regions[1] and o.regions[1][0].ops
        els, et = self.stmts(o.regions[1][0]) if has_else else ([], None)
        if live:
            self.assign(names, tt, live, then)
            self.assign(names, et, live, els)
        c = truth(self.ex(o.args[0]))
        if not then:
            then, els, c = els, then, negated(c)
        if not then:
            return
        lines = [f"if ({c.text}) {{", *sum(then, [])]
        if len(els) == 1 and els[0][0].startswith("if ("):
            lines += ["} else " + els[0][0], *els[0][1:]]
        elif els:
            lines += ["} else {", *sum(els, []), "}"]
        else:
            lines.append("}")
        out.append(lines)

    def last_use(self, v, blk):
        """The index in ``blk`` of the last statement printing value ``v``."""
        last = -1
        for o in self.users_of(v):
            if o.res and o.res[0] in self.inline:
                last = max(last, self.last_use(o.res[0], blk))
                continue
            while o is not None and self.where[id(o)][0] is not blk:
                o = self.parent_op(o)
            if o is not None:
                last = max(last, self.where[id(o)][1])
        return last

    def declared(self, blk):
        """The variables declared by the statements of ``blk``."""
        return {
            self.names[r]
            for o in blk.ops
            for r in o.res
            if r in self.names and self.names[r] not in self.coalesced
        }

    def users_of(self, v):
        """The operations using value ``v``."""
        return self.users.get(v, [])

    def parent_op(self, o):
        """The operation whose region holds ``o``."""
        return self.parents.get(id(self.where[id(o)][0]))

    def coalesce(self, blk, carried, nexts):
        """Name each value of ``nexts`` defined in ``blk`` after the last use of its carried
        variable as that variable, so its definition assigns it in place."""
        for (cv, name), nv in zip(carried, nexts):
            d = self.defs.get(nv)
            if d is None or nv in self.names or self.where[id(d)][0] is not blk:
                continue
            if nv in self.inline and (self.uses.get(nv) == 1 or self.memf.get(nv)):
                continue
            was = nv in self.inline
            self.inline.discard(nv)
            if nexts.count(nv) > 1 or self.last_use(cv, blk) > self.where[id(d)][1]:
                if was:
                    self.inline.add(nv)
                continue
            self.names[nv] = name
            self.coalesced.add(name)

    def s_while(self, o, out):  # pylint: disable=too-many-locals
        """An ``scf.while`` as ``do``/``while``/``for``."""
        bb, ab = o.regions[0][0], o.regions[1][0]
        carried = [(n, self.var_of(n)) for n, _ in bb.args]
        widths = [WIDTH[t] for _, t in bb.args]
        for k, ((_, name), w) in enumerate(zip(carried, widths)):
            out.append([f"{CTYPE[w]} {name} = {self.ex(o.args[k], (o, k)).text};"])
        cond = bb.ops[-1]
        c, fwd = cond.args[0], cond.args[1:]
        aargs = [n for n, _ in ab.args]
        yld = ab.ops[-1]
        targets = [(name, w) for (_, name), w in zip(carried, widths)]
        if len(ab.ops) == 1 and yld.args == aargs and len(fwd) == len(carried):
            for r, a, (_, name) in zip(o.res, aargs, carried):
                self.names[r] = self.names[a] = name
            self.coalesce(bb, carried, fwd)
            body, _ = self.stmts(bb)
            ce = truth(self.ex(c))
            inner = ce.refs & self.declared(bb)
            if inner or ce.refs & {n for (_, n), f in zip(carried, fwd) if self.ex(f).text != n}:
                t = self.fresh()
                out.append([f"uint8_t {t};"])
                body.append([f"{t} = {ce.text};"])
                ce = var(t, 1)
            self.assign(targets, cond, range(1, len(cond.args)), body)
            out.append(["do {", *sum(body, []), f"}} while ({ce.text});"])
            return
        if len(bb.ops) == 1 and fwd == [n for n, _ in carried]:
            for r, a, (_, name) in zip(o.res, aargs, carried):
                self.names[r] = self.names[a] = name
            ce = truth(self.ex(c))
            self.coalesce(ab, [(a, n) for a, (_, n) in zip(aargs, carried)], yld.args)
            body, _ = self.stmts(ab)
            self.assign(targets, yld, range(len(yld.args)), body)
            out.append([f"while ({ce.text}) {{", *sum(body, []), "}"])
            return
        res = [(self.var_of(r), WIDTH[t]) for r, (_, t) in zip(o.res, ab.args)]
        for r, a in zip(o.res, aargs):
            self.names[a] = self.names[r]
        self.declare(res, out)
        body, _ = self.stmts(bb)
        self.assign(res, cond, range(1, len(cond.args)), body)
        body.append([f"if ({negated(truth(self.ex(c))).text}) {{", "break;", "}"])
        self.coalesce(ab, carried, yld.args)
        after, _ = self.stmts(ab)
        self.assign(targets, yld, range(len(yld.args)), after)
        out.append(["for (;;) {", *sum(body, []), *sum(after, []), "}"])

    def s_switch(self, o, out):
        """An ``scf.index_switch`` as ``if`` or ``switch``."""
        names = [(self.var_of(r), self.width(r)) for r in o.res if self.uses.get(r)]
        live = [i for i, r in enumerate(o.res) if self.uses.get(r)]
        self.declare(names, out)
        cases = [int(x) for x in o.prop("cases").split(":")[1].rstrip(">").split(",") if x.strip()]
        sel = self.ex(o.args[0])
        d = self.defs.get(o.args[0])
        is_addr = (
            d is not None and d.name.startswith("arith.index_cast") and self.width(d.args[0]) == 16
        )
        bodies = []
        for reg in o.regions:
            st, term = self.stmts(reg[0])
            if live:
                self.assign(names, term, live, st)
            bodies.append(sum(st, []))
        label = [self.reloc(k) if is_addr else lit(k, 16) for k in cases]
        if len(cases) == 1:
            c = compare("==", sel, label[0])
            if not bodies[1]:
                c, bodies[1], bodies[0] = negated(c), bodies[0], []
            lines = [f"if ({c.text}) {{", *bodies[1]]
            lines += ["} else {", *bodies[0], "}"] if bodies[0] else ["}"]
            out.append(lines)
            return
        lines = [f"switch ({sel.text}) {{"]
        for lab, body in zip(label, bodies[1:]):
            lines += [f"case {lab.text}: {{", *body, "break;", "}"]
        lines += ["default: {", *bodies[0], "break;", "}", "}"] if bodies[0] else ["}"]
        out.append(lines)

    @property
    def private(self):
        """Whether this is a lifted function left as a call (recursive), not an entry."""
        return self.op.prop("sym_visibility") == "private"

    def head(self):
        """The C declarator of the function."""
        if not self.private:
            return f"void {self.name}(uint8_t a)"
        params = ", ".join(f"{CTYPE[self.width(n)]} {n[1:]}" for n, _ in self.op.regions[0][0].args)
        return f"static struct cgen_regs {self.name}({params})"

    def text(self):
        """The C definition of the function."""
        blk = self.op.regions[0][0]
        for n, _ in blk.args:
            self.names[n] = "a" if not self.private else n[1:]
        self.plan()
        body, term = self.stmts(blk)
        lines = sum(body, [])
        if self.private:
            vals = ", ".join(self.ex(v, (term, i)).text for i, v in enumerate(term.args))
            lines.append(f"return (struct cgen_regs){{{vals}}};")
        params = [n for n, _ in blk.args]
        unused = [self.names[n] for n in params if not self.uses.get(n)]
        lines[:0] = [f"(void){n};" for n in unused]
        return "\n".join([f"{self.head()} {{", *lines, "}"])

    @staticmethod
    def ex_refs(line):
        """The identifiers of a line of C."""
        return set(re.findall(r"\b[A-Za-z_]\w*\b", line))


class Module:  # pylint: disable=too-few-public-methods
    """The functions of a structured module and the memory they share."""

    def __init__(self, top, mem):
        self.mem, self.used, self.helpers, self.regions_used = mem, set(), set(), set()
        self.traps = set()
        self.funcs = []
        for o in top.regions[0][0].ops:
            if o.name == "func.func" and o.regions[0] and o.regions[0][0].ops:
                self.funcs.append(Func(o, self))

    def partition(self):
        """Partition memory by the accesses of every function."""
        ranges, stores = [], set()
        for f in self.funcs:
            f.drop()
            for o, (lo, hi) in f.accesses():
                if id(o) not in f.dropped:
                    ranges.append((lo, hi))
                    if o.name == "memref.store" and lo == hi:
                        stores.add(lo)
        self.mem.partition(ranges, sorted(stores))

    def functions(self):
        """The C text of every function, after the register struct and prototypes the
        functions left as calls need."""
        out = []
        private = [f for f in self.funcs if f.private]
        if private:
            types = [WIDTH[t] for t in self.regtypes(private[0])]
            fields = " ".join(f"{CTYPE[w]} r{j};" for j, w in enumerate(types))
            out.append(f"struct cgen_regs {{ {fields} }};")
            out += [f"{f.head()};" for f in private]
        return out + [f.text() for f in self.funcs]

    @staticmethod
    def regtypes(f):
        """The result types of private function ``f``."""
        ft = f.op.prop("function_type")
        return [t.strip() for t in ft.split("->")[1].strip(" ()").split(",")]
