"""Unified value+memory e-graph: McCarthy select/store axioms + interval disjointness.

Memory becomes a first-class sort over eqlift's value algebra: store-to-load
forwarding, spill elimination, disjoint read-through and dead-store removal fall
out of one saturation + root extraction as Z3-proven rewrites.
"""

from __future__ import annotations

import collections
import functools
import os
import time

import z3
from egglog import EGraph, Expr, function, i64, rewrite, rule, ruleset, set_, union
from egglog import eq as egg_eq

from . import eqlift as E
from . import frameptr

_WIDTHS = (1, 2)  # the widths the memory axioms and the interval rules are stated at
_TOP = (0, 0xFFFF)  # an address interval that says only "somewhere in the address space"
_ITERS = 30
ROUNDS = int(os.environ.get("DI_EQLIFT_ROUNDS", "6"))  # measured: emitted size converges at 6
NODES = int(os.environ.get("DI_EQLIFT_NODES", "30000"))  # the e-graph bound rung (d2) runs under
EMIT_S = float(os.environ.get("DI_EQLIFT_EMIT_S", "60"))  # extraction across one artifact


def saturate(eg, rs, rounds=None):
    """Run ``rs`` a round at a time, to a fixpoint, the round cap or the node bound.

    Adoption §5's mandatory bounded schedule, sound at any cutoff because every
    admitted rule is an equivalence. Both bounds are functions of the program, so the
    artifact is one too (§10): the clock decides nothing, and a round past the cap
    reshuffles which representative extraction returns without changing the size."""
    rounds = ROUNDS if rounds is None else rounds
    for i in range(rounds):
        if not eg.run(rs).updated:
            return i + 1
        if sum(n for _f, n in eg.all_function_sizes()) > NODES:
            return i + 1
    return rounds


class Mem(Expr):
    """Memory (array) sort: a chain of stores over an opaque initial memory."""


@function
def mem0() -> Mem: ...


@function
def memk(n: i64) -> Mem: ...


@function
def store(m: Mem, a: E.T, v: E.T, w: i64) -> Mem: ...


_SEL_COST = 1 << 10  # the consumer's price: pick_ir spells a site from memory last of all


@function(cost=_SEL_COST)
def sel(m: Mem, a: E.T, w: i64) -> E.T: ...


@function(merge=lambda old, new: old.min(new))
def lo(x: E.T) -> i64: ...


@function(merge=lambda old, new: old.max(new))
def hi(x: E.T) -> i64: ...


@ruleset
def mem_rules(
    m: Mem,
    a: E.T,
    b: E.T,
    u: E.T,
    v: E.T,
    s: E.T,
    n: i64,
    p: i64,
    q: i64,
    r: i64,
    w: i64,
    wa: i64,
    wb: i64,
):
    """Address-interval analysis plus the Z3-proven McCarthy array axioms.

    ``add``/``shl`` carry the interval only where the result cannot wrap its own width: a
    wrapped sum is smaller than either operand. ``sel_pair`` is applied at the shape the
    catalog's ``pair-row`` spells -- two adjacent columns at one index -- which is the
    proved axiom instantiated at ``a = q + b``, never a second statement of it."""
    yield rule(egg_eq(a).to(E.num(n, w))).then(set_(lo(a)).to(n), set_(hi(a)).to(n))
    yield rule(egg_eq(a).to(E.zext(b))).then(set_(lo(a)).to(i64(0)), set_(hi(a)).to(i64(255)))
    yield rule(egg_eq(a).to(E.band(b, E.num(n, w), w))).then(
        set_(lo(a)).to(i64(0)), set_(hi(a)).to(n)
    )
    for width in _WIDTHS:
        cap = i64(E._mask(width))
        yield rule(
            egg_eq(s).to(E.add(a, b, i64(width))),
            egg_eq(lo(a)).to(n),
            egg_eq(lo(b)).to(q),
            egg_eq(hi(a)).to(p),
            egg_eq(hi(b)).to(r),
            p + r <= cap,
        ).then(set_(lo(s)).to(n + q), set_(hi(s)).to(p + r))
        yield rule(
            egg_eq(s).to(E.shl(a, E.num(n, i64(width)), i64(width))),
            egg_eq(lo(a)).to(p),
            egg_eq(hi(a)).to(q),
            (q << n) <= cap,
        ).then(set_(lo(s)).to(p << n), set_(hi(s)).to(q << n))
    yield rewrite(sel(store(m, a, v, w), a, w)).to(v)
    yield rule(
        egg_eq(s).to(sel(store(m, a, v, wa), b, wb)),
        egg_eq(hi(a)).to(p),
        egg_eq(lo(b)).to(q),
        p + wa <= q,
    ).then(union(s).with_(sel(m, b, wb)))
    yield rule(
        egg_eq(s).to(sel(store(m, a, v, wa), b, wb)),
        egg_eq(hi(b)).to(p),
        egg_eq(lo(a)).to(q),
        p + wb <= q,
    ).then(union(s).with_(sel(m, b, wb)))
    yield rule(
        egg_eq(s).to(
            E.bor(
                E.shl(E.zext(sel(m, E.add(E.num(p, 2), b, 2), 1)), E.num(8, 1), 2),
                E.zext(sel(m, E.add(E.num(q, 2), b, 2), 1)),
                2,
            )
        ),
        egg_eq(p).to(q + 1),
    ).then(union(s).with_(sel(m, E.add(E.num(q, 2), b, 2), 2)))
    yield rewrite(store(store(m, a, u, w), a, v, w)).to(store(m, a, v, w))
    yield rewrite(store(m, a, sel(m, a, w), w)).to(m)


_AX = z3.Array("m", z3.BitVecSort(16), z3.BitVecSort(8))
_A, _B = z3.BitVec("a", 16), z3.BitVec("b", 16)


def _store_w(m, a, v, w):
    for i in range(w):
        m = z3.Store(m, a + i, z3.Extract(8 * i + 7, 8 * i, v))
    return m


def _sel_w(m, a, w):
    parts = [z3.Select(m, a + i) for i in range(w)]
    return parts[0] if w == 1 else z3.Concat(list(reversed(parts)))


def _disjoint(a, wa, b, wb):
    return z3.And([a + i != b + j for i in range(wa) for j in range(wb)])


def _axioms():
    m, a, b = _AX, _A, _B
    out = []
    for w in (1, 2):
        v = z3.BitVec("v%d" % w, 8 * w)
        u = z3.BitVec("u%d" % w, 8 * w)
        out.append(("sel_store_same/w%d" % w, _sel_w(_store_w(m, a, v, w), a, w) == v))
        out.append(
            (
                "store_overwrite/w%d" % w,
                _store_w(_store_w(m, a, u, w), a, v, w) == _store_w(m, a, v, w),
            )
        )
        out.append(("store_redundant/w%d" % w, _store_w(m, a, _sel_w(m, a, w), w) == m))
    out.append(
        (
            "sel_pair",
            ((z3.ZeroExt(8, z3.Select(m, a + 1)) << 8) | z3.ZeroExt(8, z3.Select(m, a)))
            == _sel_w(m, a, 2),
        )
    )
    for wa in (1, 2):
        for wb in (1, 2):
            v = z3.BitVec("vd%d%d" % (wa, wb), 8 * wa)
            goal = z3.Implies(
                _disjoint(a, wa, b, wb), _sel_w(_store_w(m, a, v, wa), b, wb) == _sel_w(m, b, wb)
            )
            out.append(("sel_store_diff/w%d%d" % (wa, wb), goal))
    return out


def verify_axioms():
    """Z3-prove every (width-aware) memory axiom valid over the byte array; names."""
    proved = []
    for name, goal in _axioms():
        s = z3.Solver()
        s.add(z3.Not(goal))
        if s.check() != z3.unsat:
            raise AssertionError("memory axiom %s is not valid" % name)
        proved.append(name)
    return proved


_UNIFIED = None


def unified_rules():
    """(ruleset, names) for value + memory, after both admission gates pass."""
    global _UNIFIED  # pylint: disable=global-statement
    if _UNIFIED is None:
        verify_axioms()
        valrs, names = E.admitted_rules()
        _UNIFIED = (mem_rules | valrs, names)
    return _UNIFIED


def build(ops, w=1):
    """Fold ``ops`` [(addr_ir, val_ir), ...] into a ``w``-byte store chain."""
    m = mem0()
    for addr, val in ops:
        m = store(m, E._egg_of(addr, {}), E._egg_of(val, {}), w)
    return m


def extract(term, iters=30):
    """Saturate value+memory rules over ``term`` and return its extracted form."""
    rs, _names = unified_rules()
    eg = EGraph()
    h = eg.let("h", term)
    eg.run(rs * iters)
    return str(eg.extract(h))


def extract_load(ops, addr, w=1, iters=30):
    """Saturate value+memory rules and return the extracted term reading ``addr``."""
    return extract(sel(build(ops, w), E._egg_of(addr, {}), w), iters)


class _Defs:
    """``frameproc.DefsAt``'s reader over one walk's own SSA state.

    A local resolves to the pass-1 expression currently bound to it and to nothing
    where it was havoced, which is what ``addr_bits``/``addr_floor`` ask of an env."""

    __slots__ = ("src",)

    def __init__(self, src):
        self.src = src

    def defn(self, n):
        got = self.src.get(n[1])
        return None if got is None or got[0] == "loc" else got


_lattice = E.frameproc.lattice  # the ONE reading of the interval mem_rules derives


def addr_interval(e, defs=None):
    """The address interval of pass-1 expression ``e``, or None where only its width.

    Stage 3's interval bridge: ``addr_floor``'s must-set bits are a lower bound on
    every address the expression names and ``addr_bits``' may-set bits an upper one,
    so the two committed bit analyses read as the one interval the disjointness
    guard wants. Nothing is seeded where ``mem_rules`` already states a bound."""
    if _lattice(e) is not None:
        return None
    got = (E.frameproc.addr_floor(e, defs), E.frameproc.addr_bits(e, defs))
    return None if got == _TOP else got


def _sle(under, over):
    """``under <=s over`` is ``sge(over, under)``: the graph carries the pair as slt/sge."""
    return E.sge(over, under)


_OP = {
    "INT_ADD": E.add,
    "INT_SUB": E.sub,
    "INT_AND": E.band,
    "INT_OR": E.bor,
    "INT_XOR": E.bxor,
    "INT_LEFT": E.shl,
    "INT_RIGHT": E.shr,
    "INT_EQUAL": E.eq,
    "INT_NOTEQUAL": E.ne,
    "INT_LESS": E.ult,
    "INT_LESSEQUAL": E.ule,
    "INT_SLESS": E.slt,
    "INT_SLESSEQUAL": _sle,
    "INT_CARRY": E.carry,
}
_CMP = frozenset(
    ("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL", "INT_SLESS", "INT_SLESSEQUAL")
)
_REWIDTH = frozenset(("INT_ZEXT", "COPY"))


def _rewidth(mn, x, w):
    """The unary width changes: ``INT_ZEXT`` widens, rung (d2)'s ``COPY`` narrows."""
    if mn == "INT_ZEXT":
        return E.zext(x)
    if w != 1:
        raise KeyError("COPY at width %d" % w)
    return E.trunc(x)


def render_block(stmts, aliases=None):
    """Render a straight-line block to text via the unified graph + eqlift's printer.

    Value locals stay named (SSA + def-equations, so simplification still fires);
    memory forwards through the store chain. Returns the printed lines."""
    env, defs, sk, ver = {}, [], [], [0]
    memref = [mem0()]

    def conv(e):
        k = e[0]
        if k == "const":
            return E.num(e[1] & E._mask(e[2]), e[2])
        if k == "loc":
            return env.get(e[1], E.loc(e[1] + ".0"))
        if k == "mem":
            return sel(memref[0], conv(e[1]), e[2])
        mn, kids, w = e[1], e[2], e[3]
        if mn in _REWIDTH:
            return _rewidth(mn, conv(kids[0]), w)
        fn = _OP[mn]
        if mn in _CMP:
            return fn(conv(kids[0]), conv(kids[1]))
        if mn == "INT_CARRY":
            w = E.carry_lane(kids[0])  # the lane the carry is out of, not its own bit
        r = conv(kids[0])
        for kid in kids[1:]:
            r = fn(r, conv(kid), w)
        return r

    avail = {n + ".0" for n in E.frameproc._ALL_REG_LOCALS}  # the block's own inputs
    cur = {}
    for s in stmts:
        if s[0] == "asg":
            rhs = conv(s[2])
            ver[0] += 1
            name = "%s.%d" % (s[1], ver[0])
            leaf = E.loc(name)
            defs.append((leaf, rhs))
            sk.append(("asg", s[1], rhs, ("loc", name), set(avail)))
            env[s[1]] = leaf
            avail.discard(cur.get(s[1], s[1] + ".0"))  # the base no longer holds it
            avail.add(name)
            cur[s[1]] = name
        elif s[0] == "st":
            v = conv(s[2])
            a = s[1]
            memref[0] = store(memref[0], E.num(a[1] & E._mask(a[2]), a[2]), v, _ew(s[2]))
            own = ("cell", a[1] & E._mask(a[2]), _ew(s[2]), 0)
            sk.append(("st", a, v, own, set(avail)))

    rs, _names = unified_rules()
    eg = EGraph()
    for leaf, rhs in defs:
        eg.register(union(leaf).with_(rhs))
    handles = [
        (kind, ref, own, av, eg.let("h%d" % i, t)) for i, (kind, ref, t, own, av) in enumerate(sk)
    ]
    saturate(eg, rs)
    pr = E._Printer(aliases or {})
    for kind, ref, own, av, h in handles:
        cands = [to_ir(str(x)) for x in eg.extract_multiple(h, 12)]
        cands = [c for c in cands if c != own and _defined_at(c, av)] or cands
        text = pr.fmt(min(cands, key=E._cost))
        dest = ref if kind == "asg" else pr.name(ref[1]) + E.sidprog._wsuf(ref[2])
        pr.line("%s = %s" % (dest, text), 0)
    return pr.out


def _defined_at(ir, avail):
    """True if every named-local leaf of ``ir`` is a version its base still holds.

    ``avail`` is the site's own ``live()``: the versions its bases denote there. A local
    renders as its base name, so a version the base no longer holds spells another value
    however available it once was, and one the base holds spells it whether or not an
    ``asg`` rendered the definition."""
    if not isinstance(ir, tuple):
        return True
    if ir[0] == "loc":
        return ir[1] in avail
    return all(_defined_at(a, avail) for a in ir[1:] if isinstance(a, tuple))


_DYN = frozenset(("dcall", "dbr", "dgoto", "igoto", "label"))  # transfers no map follows
_TOPFP = (frozenset(), True)  # the footprint that says nothing: a join forgets everything
_NOFP = (frozenset(), False)  # writes nothing; the call graph's fixpoint resolves it


def _wr_span(e, defs=None):
    """The interval a store address expression can reach, or None where it is ⊤.

    ``frameproc.addr_reach``'s tighter-of-the-two, refused where it says nothing. A join
    reads it with no env (a local's reaching definition at the join is not the one it
    carried inside the arm); a chain step reads it with the walk's own."""
    got = E.frameproc.addr_reach(e, defs)
    return None if got == _TOP or got[0] > got[1] else got


_INLINE = 4  # how far a read address is resolved through its reaching definitions


def _resolve(e, defs, depth=_INLINE):
    """``e`` with each local replaced by the definition reaching it.

    ``addr_bits`` already reads one level of this ("a local is the address its
    reaching definition spells"); an address built by an ``add`` needs the leaves,
    since the lattice states nothing about a bare local. A wall answers None and
    the local stays, so nothing is resolved that the definitions do not pin."""
    if not isinstance(e, tuple) or depth <= 0:
        return e
    if e[0] == "loc":
        got = None if defs is None else defs.defn(e)
        return e if got is None else _resolve(got, defs, depth - 1)
    if e[0] == "op":
        return (e[0], e[1], tuple(_resolve(k, defs, depth) for k in e[2]), e[3])
    return e


def _rd_span(a, defs=None, ext=None):
    """The interval a read address can reach, or None where it is ⊤.

    The bit analyses first, over the resolved address; where they state nothing and
    the address is a deref of a pointer web, 2b's observed extent bounds it -- the
    declared blocks the derefs landed in, consumed exactly as the join consumes
    ``addr_floor``/``addr_bits`` and extended nowhere."""
    if a[0] == "const":
        v = a[1] & E._mask(a[2])
        return v, v
    got = _wr_span(_resolve(a, defs), defs)
    if got is not None or not ext:
        return got
    web = frameptr.deref(a)
    return None if web is None else ext.get(web[0])


def _may_read_vol(a, defs=None, ext=None):
    """True where a load's run-time address may be a volatile cell (spec 1.3).

    A volatile read is not the last store's value -- ``$D012`` counts and ``$D019``
    reads zero -- so such a load may never be served from the store chain, and two of
    them may not be equated; the caller gives each one its own opaque memory."""
    if a[0] == "const":
        return a[1] & E._mask(a[2]) in E.sidprog._VOLS
    span = _rd_span(a, defs, ext)
    return span is None or any(span[0] <= v <= span[1] for v in E.sidprog._VOLS)


def _reads_of(e, out):
    """``(address, width)`` of every memory read an expression makes, nested included."""
    stack = [e]
    while stack:
        x = stack.pop()
        if not isinstance(x, tuple):
            continue
        if x[0] == "mem":
            out.append((x[1], x[2]))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def _cover(spans):
    """``spans`` merged into the fewest byte intervals covering every address named.

    A store answers to the whole reader set, so the set is asked as intervals and not
    cell by cell: merging only widens, and it turns thousands of proofs into a few."""
    got = []
    for a0, a1, w in spans:
        end = a1 + w - 1
        got.append((0, 0xFFFF) if end > 0xFFFF else (a0, end))
    out = []
    for a0, a1 in sorted(got):
        if out and a0 <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], a1))
        else:
            out.append((a0, a1))
    return tuple(out)


def _mem_reads(stmts, ext=None):
    """(read spans, wild) of a statement list: every address running it may read.

    ``_mem_writes``' dual, and the one a store answers to. Read artifact-wide, so a
    label and an enumerated dynamic transfer cost nothing -- entering anywhere reads
    no more than the union -- and only an address nothing bounds is ⊤."""
    spans, wild = set(), False
    for env, i, s in E.frameproc.envs(stmts):
        defs = E.frameproc.DefsAt(env, i)
        for e in E.frameproc._stmt_exprs(s):
            for a, w in _reads_of(e, []):
                got = _rd_span(a, defs, ext)
                if got is None:
                    wild = True
                else:
                    spans.add((got[0], got[1], w))
    return frozenset(spans), wild


def _mem_writes(stmts, enter=None, out_goto=False):
    """(write spans, wild) footprint of a statement list: what running it may store.

    A span is ``(lo, hi, width)`` — every address a store can name, at the width it
    writes; a const cell spans one address. ``enter(pc)`` is the footprint of the code an
    edge enters, and its absence, an unbounded address and an unfollowable transfer are ⊤."""
    spans, wild = set(), [False]

    def take(pc):
        got = _TOPFP if enter is None else enter(pc)
        spans.update(got[0])
        wild[0] = wild[0] or got[1]

    def rec(sl):
        for s in sl:
            k = s[0]
            if k == "st":
                a = s[1]
                lohi = (a[1] & E._mask(a[2]),) * 2 if a[0] == "const" else _wr_span(a)
                if lohi is None:
                    wild[0] = True
                else:
                    spans.add((lohi[0], lohi[1], _ew(s[2])))
            elif k in ("call", "callb", "pcall"):
                take(s[1])
            elif k == "swc":
                for lbl in s[1]:  # bare labels: entered with no inline body
                    take(int(lbl[1:], 16))
            elif k == "goto" and out_goto:
                take(s[1])
            elif k in _DYN:
                wild[0] = True
            for b in E.frameproc._stmt_bodies(s):
                rec(b)

    rec(stmts)
    return frozenset(spans), wild[0]


def _disjoint_of(a0, a1, sw, b0, b1, w):
    """Z3 (QF_BV): no byte a store in ``[a0,a1]`` of width ``sw`` writes is a byte a read
    in ``[b0,b1]`` of width ``w`` names. The admitted weakenings are proved, never matched."""
    x, y = z3.BitVec("x", 16), z3.BitVec("y", 16)
    within = [
        z3.ULE(z3.BitVecVal(a0, 16), x),
        z3.ULE(x, z3.BitVecVal(a1, 16)),
        z3.ULE(z3.BitVecVal(b0, 16), y),
        z3.ULE(y, z3.BitVecVal(b1, 16)),
    ]
    goal = z3.Implies(z3.And(within), z3.And([x + i != y + j for i in range(sw) for j in range(w)]))
    s = z3.Solver()
    s.add(z3.Not(goal))
    return s.check() == z3.unsat


_disjoint_spans = functools.lru_cache(maxsize=None)(_disjoint_of)


def _disjoint_span(a0, a1, sw, a, w):
    """The single-cell case the join asks for: ``a:w`` is one address, not an interval."""
    return _disjoint_spans(a0, a1, sw, a, a, w)


class _Chain:
    """The walk's own memory versions: what each step writes, so a read taken at an
    earlier version is proved to spell the same value at a later site.

    A step is a store span, a join's kept-cell set, or ⊤ (a havoc, which nothing crosses)."""

    __slots__ = ("steps", "memo")

    def __init__(self):
        self.steps, self.memo = {}, {}

    def step(self, ver, prev, wrote):
        self.steps[ver] = (prev, wrote)

    def _crosses(self, wrote, span):
        if isinstance(wrote, frozenset):  # a join carries exactly the cells it proved kept
            return span[0] == span[1] and any(c[0] == span[0] and c[2] == span[2] for c in wrote)
        return wrote is not None and _disjoint_spans(*wrote, span[0], span[1], span[2])

    def reach(self, ver, span):
        """The versions a read of ``span`` at ``ver`` may equally be taken at.

        An in-edge join records a version per cell instead of a kept set, because the
        version every edge agrees at is the cell's own; the walk lands there and stops,
        since only that one version is common to all the edges."""
        key = (ver, span)
        got = self.memo.get(key)
        if got is None:
            out, n = [ver], ver
            while True:
                st = self.steps.get(n)
                if st is None or st[0] is None:
                    break
                if isinstance(st[1], dict):
                    at = st[1].get((span[0], span[2])) if span[0] == span[1] else None
                    if at is None:
                        break
                    out.append(at)
                    break
                if not self._crosses(st[1], span):
                    break
                out.append(st[0])
                n = st[0]
            got = self.memo[key] = frozenset(out)
        return got

    def ok(self, ver, reads):
        """Depth of ``reads`` if every one spells at ``ver`` the value it holds, else None.

        Depth is how far back the chain the read may equally be taken — read off the site,
        so the rank does not depend on which representative extraction returned (§10)."""
        depth = 0
        for at, span, w in reads:
            if span is None:
                if at != ver:
                    return None
                continue
            got = self.reach(ver, (span[0], span[1], w))
            if at is None or at not in got:
                return None
            depth += len(got) - 1
        return depth


def _join_mem(pre_mem, stmts, fresh, held=(), enter=None):
    """Memory after ``stmts`` may or may not have run, and the cells it still holds.

    The complemented encoding: a fresh opaque base carrying exactly the chain-held const
    cells Z3 proves disjoint from every span the statements can write — adoption §10's
    opaque reset, weakened only where the disjointness is proved."""
    spans, wild = _mem_writes(stmts, enter)
    base = memk(i64(fresh()))  # opaque post-branch memory
    if wild:
        return base, frozenset()
    kept = [
        c
        for c in sorted(held)
        if all(_disjoint_span(a0, a1, sw, c[0], c[2]) for a0, a1, sw in spans)
    ]
    m = base
    for a, aw, w in kept:
        addr = E.num(a, aw)
        m = store(m, addr, sel(pre_mem, addr, w), w)
    return m, frozenset(kept)


_ARMS = {"dgoto": "swg", "igoto": "swg", "dcall": "swc"}  # a transfer and the table that arms it


def _armed(s, nxt):
    """True where the arm table right after ``s`` enumerates its targets.

    ``frameval.seq``'s own pairing law: an arm table belongs to the computed transfer
    immediately before it and to no other, so the arms are that transfer's whole
    successor set and the memory they are entered with is the memory at the transfer."""
    return nxt is not None and nxt[0] == _ARMS.get(s[0])


def _collect(tgts):
    """An ``enter`` that records the pc and contributes nothing: the fixpoint resolves it."""

    def enter(pc):
        tgts.add(pc)
        return _NOFP

    return enter


def _targets(stmts, out=None):
    """``({pc: goto count}, other targets)``: the in-edges of a label, by kind.

    Only a ``goto`` carries a memory this walk can name; a call, an ``swc`` label and
    anything the map does not enumerate arrive with a memory it cannot."""
    goto, other = ({}, set()) if out is None else out
    for s in stmts:
        k = s[0]
        if k == "goto":
            goto[s[1]] = goto.get(s[1], 0) + 1
        elif k in ("call", "callb"):
            other.add(s[1])
        elif k == "swc":
            other.update(int(lbl[1:], 16) for lbl in s[1])
        for b in E.frameproc._stmt_bodies(s):
            _targets(b, (goto, other))
    return goto, other


_UNMAPPED = frozenset(("dcall", "dbr", "dgoto", "igoto", "callb"))


def _may_scan(stmts, e, info, out):
    """``(call targets, own definitions, unfollowable)``: one list, nested bodies included."""
    tgts, own, wild = out
    for s in stmts:
        k = s[0]
        if k in ("asg", "for"):
            own.add(s[1])
        elif k == "pcall":
            own.update(s[3])
            tgts.add(s[1])
        elif k == "call":
            tgts.add(s[1])
        elif k == "swc":
            tgts.update(int(lbl[1:], 16) for lbl in s[1])
        elif k == "goto":
            wild |= s[1] not in info.own_labels[e]
        elif k in _UNMAPPED:
            wild = True
        for b in E.frameproc._stmt_bodies(s):
            _t, _o, wild = _may_scan(b, e, info, (tgts, own, wild))
    return tgts, own, wild


def _wall_may(info):
    """``{entry: may-define set}`` for the entries whose ``may`` bounds their call tree.

    ``_Info._may`` reads a computed transfer and a ``callb``'s writes past its inlined body
    as ``G`` -- the register *read* set, no bound on a local no expression names -- and
    follows no ``goto`` leaving its procedure; the closure is re-proved, never assumed."""
    if info is None or info.open_flow:
        return {}
    edges, own, ok = {}, {}, set()
    for e in info.order:
        edges[e], own[e], wild = _may_scan(info.procs[e], e, info, (set(), set(), False))
        if not wild:
            ok.add(e)
    while True:
        drop = {
            e
            for e in ok
            if edges[e] - ok
            or own[e] - info.may[e]
            or any(info.may[t] - info.may[e] for t in edges[e])
        }
        if not drop:
            return {e: frozenset(info.may[e]) for e in ok}
        ok -= drop


def _labels_of(stmts, out=None):
    out = [] if out is None else out
    for s in stmts:
        if s[0] == "label":
            out.append(s[1])
        for b in E.frameproc._stmt_bodies(s):
            _labels_of(b, out)
    return out


class Footprints:
    """What entering at a pc may write, over the enumerated call/goto graph.

    ``of(pc)`` is ``(spans, wild)`` for the code entered there and everything it enters
    in turn; a pc no procedure owns is ⊤, and so is a procedure holding a transfer this
    map cannot follow. ``joins(pc)`` reads the same map's in-edges at a label, and
    ``unread`` the same traversal's read spans: what no reader in the artifact names."""

    __slots__ = ("own", "calls", "owner", "fp", "entered", "landings", "wall", "rd", "ext")

    def __init__(self, procs=(), open_flow=True, landings=(), extents=None):
        procs = list(procs)
        self.own, self.calls, self.owner, self.rd = {}, {}, {}, {}
        self.ext = dict(extents or {})
        self.wall, self.landings = bool(open_flow), frozenset(landings)
        for entry, stmts in procs:
            self.owner.update(dict.fromkeys(_labels_of(stmts), entry))
            self.owner[entry] = entry
        for entry, stmts in procs:
            tgts = set()
            self.own[entry] = _mem_writes(stmts, _collect(tgts), out_goto=True)
            self.calls[entry] = frozenset(tgts)
            self.rd[entry] = _mem_reads(stmts, extents)
        self.entered = frozenset().union(*self.calls.values()) if self.calls else frozenset()
        self.fp = self._close()

    def _close(self):
        """The call graph's least fixpoint: a caller writes what its callees write."""
        cur = dict(self.own)
        for _round in range(len(self.own) + 1):
            nxt = {}
            for e, (spans, wild) in cur.items():
                for t in self.calls[e]:
                    ts, tw = cur.get(self.owner.get(t), _TOPFP)
                    spans, wild = spans | ts, wild or tw
                nxt[e] = (spans, wild)
            if nxt == cur:
                return cur
            cur = nxt
        return cur

    def of(self, pc):
        """The footprint of entering at ``pc``; ⊤ where the map does not name it."""
        return self.fp.get(self.owner.get(pc), _TOPFP)

    def joins(self, pc):
        """Whether a label at ``pc`` is a join: an edge arrives that the walk does not
        carry. A transfer no map enumerates (``open_flow``) makes every label one, and
        so do an RTS-trick landing, a procedure entry and any enumerated goto or call."""
        return self.wall or pc in self.entered or pc in self.landings or pc in self.own

    def sources(self, pc):
        """The procedures whose enumerated transfers name ``pc``."""
        return frozenset(e for e, tgts in self.calls.items() if pc in tgts)

    def readers(self, skip=None):
        """``(byte intervals, wild)`` every reader but ``skip``'s own statements names.

        The reader set is artifact-wide because memory persists across the frame: a
        store answers to every read the program holds, wherever it sits. The rendering
        procedure is left out so its caller can offer the reads its *extraction* kept;
        every other procedure is read at its statements, which is the conservative
        side of the same question."""
        spans, wild = set(), self.wall
        for entry, (sp, wd) in self.rd.items():
            if entry == skip:
                continue
            spans |= sp
            wild = wild or wd
        return _cover(spans), wild


def _reg_bases(ir, out, regs_only=True):
    """The local bases an extracted term names -- the text's own reads.

    A local renders as its base name, so this is what the emitted line reads
    whatever version the term carries."""
    if isinstance(ir, tuple):
        if ir[0] == "loc":
            base = ir[1].rpartition(".")[0]
            if not regs_only or base in E.frameproc._ALL_REG_LOCALS:
                out.add(base)
        else:
            for a in ir[1:]:
                _reg_bases(a, out, regs_only)
    return out


def _child_bodies(nd):
    if nd[0] == "if":
        return (nd[2], nd[3])
    if nd[0] in ("loop", "callb", "for"):
        return (nd[{"loop": 1, "callb": 3, "for": 4}[nd[0]]],)
    if nd[0] == "switch":
        return [arm for _lbl, arm in nd[1]]
    return ()


def _loc_names(ir, out):
    if isinstance(ir, tuple):
        if ir[0] == "loc":
            out.add(ir[1])
        else:
            for a in ir[1:]:
                _loc_names(a, out)
    return out


def _node_terms(nd):
    """Every extracted-term index a node's rendered line names.

    A term missing here is invisible to rooting, sharing and the §6 proofs, so a
    spelling it alone reads would print a local no surviving def defines."""
    if nd[0] == "pcall":
        return tuple(nd[2])
    if nd[0] == "asg":
        return (nd[2],)
    if nd[0] == "st":
        return (nd[2],) if nd[3] is None else (nd[2], nd[3])
    if nd[0] in ("if", "dcall", "dgoto"):
        return (nd[1],)
    if nd[0] == "dbr":
        return (nd[2], nd[3])
    if nd[0] == "igoto":
        return () if nd[2] is None else (nd[2],)
    return ()


def _has_mem(ir):
    if not isinstance(ir, tuple):
        return False
    if ir[0] in ("cell", "load"):
        return True
    return any(_has_mem(a) for a in ir[1:])


def _ir_span(ir):
    """The address interval an extracted term names, or None where it is ⊤.

    ``_lattice``'s rules over the printer IR, wrap guards included; a value read out of
    memory is bounded by its own width, which is what makes a byte pointer page zero.
    A zero extension is the identity on the value, so it carries its operand's own
    interval where that interval is inside the byte this dialect extends -- the
    e-graph's ``lo``/``hi`` merge by join and cannot state it, but a reader interval
    only has to be sound, and this one is never wider than the width bound it replaces."""
    k = ir[0]
    if k == "num":
        v = ir[1] & E._mask(ir[2])
        return v, v
    if k == "zext":
        got = _ir_span(ir[1])
        return got if got is not None and got[1] <= 0xFF else (0, 0xFF)
    if k == "trunc":
        return 0, 0xFF
    if k == "band" and ir[2][0] == "num":
        return 0, ir[2][1] & E._mask(ir[3])
    if k == "add":
        a, b = _ir_span(ir[1]), _ir_span(ir[2])
        if a and b and a[1] + b[1] <= E._mask(ir[3]):
            return a[0] + b[0], a[1] + b[1]
        return None
    if k == "shl" and ir[2][0] == "num":
        a = _ir_span(ir[1])
        if a and (a[1] << ir[2][1]) <= E._mask(ir[3]):
            return a[0] << ir[2][1], a[1] << ir[2][1]
        return None
    if k in ("cell", "load"):
        return 0, E._mask(ir[2])
    return None


def _ir_ptr(ir):
    """The pointer cell an extracted word read names, else None."""
    if ir[0] == "cell" and ir[2] == 2:
        return ir[1]
    if ir[0] != "bor" or ir[1][0] != "shl" or ir[2][0] != "zext":
        return None
    top, bot = ir[1], ir[2][1]
    if top[2][0] != "num" or top[2][1] != 8 or top[1][0] != "zext":
        return None
    h = top[1][1]
    return bot[1] if h[0] == "cell" and bot[0] == "cell" and h[1] == bot[1] + 1 else None


def _ir_deref(ir):
    """The pointer cell an extracted address derefs, else None.

    ``frameptr.deref``'s shapes read over the printer IR, so the extent that bounds a
    deref in the statements bounds the same deref in the spelling extraction chose."""
    if ir[0] == "add":
        return next((c for c in map(_ir_ptr, (ir[1], ir[2])) if c is not None), None)
    return _ir_ptr(ir)


def _mem_ver(ir):
    """The memory version a ``sel``'s memory argument names, or None for a raw chain."""
    if ir[0] == "mem0":
        return 0
    return ir[1] if ir[0] == "memk" else None


def _sel_reads(raw, out):
    """``(version, address span, width)`` of every memory read of an extracted term."""
    if not isinstance(raw, tuple):
        return out
    if raw[0] == "sel":
        out.append((_mem_ver(raw[1]), _ir_span(_to_ir(raw[2])), raw[3]))
        return _sel_reads(raw[2], out)
    for a in raw[1:]:
        if isinstance(a, tuple):
            _sel_reads(a, out)
    return out


def _ir_reads(ir, ver, out, ext=None):
    """The same over printer IR, where the version is the one the site was spelled at.

    ``ext`` bounds a deref by 2b's observed extent, as the statement side does; the
    chain walk passes none, so which representative it may cross does not move."""
    if not isinstance(ir, tuple):
        return out
    if ir[0] in ("cell", "load"):
        span = (ir[1], ir[1]) if ir[0] == "cell" else _ir_span(ir[1])
        if span is None and ext:
            span = ext.get(_ir_deref(ir[1]))
        out.append((ver, span, ir[2]))
        return _ir_reads(ir[1], ver, out, ext) if ir[0] == "load" else out
    for a in ir[1:]:
        if isinstance(a, tuple):
            _ir_reads(a, ver, out, ext)
    return out


def _count_locs(ir, out):
    if isinstance(ir, tuple):
        if ir[0] == "loc":
            out.append(ir[1])
        else:
            for a in ir[1:]:
                _count_locs(a, out)


def _subst_loc(ir, name, repl):
    if not isinstance(ir, tuple):
        return ir
    if ir[0] == "loc":
        return repl if ir[1] == name else ir
    return (ir[0],) + tuple(
        _subst_loc(a, name, repl) if isinstance(a, tuple) else a for a in ir[1:]
    )


def _share_once(tree, dead, chosen, terms, chain=None):
    """``render_roots``' sharing rule over the render tree: a non-register subterm
    stays named only where more than one kept statement reads it; a name read once
    is inlined and its def drops. Site-validity is required, so no stale version.

    A base some surviving read names at a version no def carries -- what a join or a
    havoc mints -- keeps every def it has: the text reads that base by name, and no
    spelling of the one versioned use accounts for the read."""
    reg = E.frameproc._ALL_REG_LOCALS
    by_name = {nd[3]: nd for nd in _all_nodes(tree) if nd[0] == "asg"}
    changed = True
    while changed:
        changed = False
        count, site, opaque = {}, {}, set()

        def scan(nodes):
            for nd in nodes:
                if nd[0] in ("asg", "st") and id(nd) in dead:
                    continue
                for ti in _node_terms(nd):
                    names = []
                    _count_locs(chosen[ti], names)
                    for nm in names:
                        count[nm] = count.get(nm, 0) + 1
                        site[nm] = ti
                        d = by_name.get(nm)
                        if d is None or id(d) in dead:
                            opaque.add(nm.rpartition(".")[0])
                for b in _child_bodies(nd):
                    scan(b)

        scan(tree)
        for name, nd in list(by_name.items()):
            if id(nd) in dead or nd[1] in reg or nd[1] in opaque or count.get(name) != 1:
                continue
            ti = site[name]
            defval = chosen[nd[2]]
            if not _defined_at(defval, terms[ti][1]):
                continue
            if _has_mem(defval) and (
                chain is None
                or chain.ok(terms[ti][3], _ir_reads(defval, terms[nd[2]][3], [])) is None
            ):
                continue  # a memory value moves to the use only where it reads the same
            chosen[ti] = _subst_loc(chosen[ti], name, defval)
            dead.add(id(nd))
            changed = True


def _dispatch(nd):
    """The frameprog statement kind a rendered ``switch`` node came from, or None."""
    return nd[2] if nd is not None and nd[0] == "switch" else None


def _switch_head(pr, nd):
    """The arm table's own header: the dialect spells one per dispatch kind."""
    if nd[2] == "swg":
        return "switch goto {"
    return "switch call {" if nd[2] == "swc" else "switch %s {" % pr.name(nd[3])


def _liveness(tree, info, entry, chosen, volatile=()):
    """Backward liveness over the render tree, in the text's own reads: id -> live-out.

    ``frameproc._Flow`` on the render tree, successor-aware cases included, and over
    every local rather than the registers alone: a value local a join renames is read
    by base name at a version no def carries, so only liveness sees that read. Its
    deletion role is gone (§5); what it answers now is which asgs ``roots`` roots."""
    labmap, liveout, brk, cont, armret = {}, {}, [], [], []

    def uses(i):
        return _reg_bases(chosen[i], set(), False)

    def node(nd, live, nxt=None):
        k = nd[0]
        if k == "asg":
            if nd[1] != E.frameproc._SP and nd[1] not in live and id(nd) not in volatile:
                return live  # faint: a dead target's operand reads are not uses
            live = set(live)
            live.discard(nd[1])
            return live | uses(nd[2])
        if k == "st":
            return live | uses(nd[2]) | (uses(nd[3]) if nd[3] is not None else set())
        if k == "if":
            return seq(nd[2], set(live)) | seq(nd[3], set(live)) | uses(nd[1])
        if k in ("loop", "for"):
            body = nd[1] if k == "loop" else nd[4]
            head = set()
            for _i in range(24):
                h = loop(body, live, head)
                if k == "for":
                    h.discard(nd[1])
                if h <= head:
                    break
                head |= h
            out = loop(body, live, head)
            if k == "for":
                out |= live  # a for leaves by its own bottom: a loop leaves only by brk
                out.discard(nd[1])  # the counter the for defines on entry is dead above
            return out | head
        if k == "label":
            labmap[nd[1]] = labmap.get(nd[1], set()) | live
            return live
        if k == "goto":
            if nd[1] in info.own_labels[entry]:
                return set(labmap.get(nd[1], set()))
            return set(info.livein[nd[1]]) if nd[1] in info.procs else set(info.G)
        if k == "call":
            t = nd[1]
            return (
                (live - info.must.get(t, set())) | info.livein[t]
                if t in info.procs
                else live | set(info.G)
            )
        if k == "pcall":
            args = set().union(*(uses(i) for i in nd[2])) if nd[2] else set()
            if nd[1] in info.procs:
                return (
                    (live - (set(nd[3]) | info.must.get(nd[1], set()))) | info.livein[nd[1]] | args
                )
            return live | set(info.G) | args
        if k == "callb":
            armret.append(set(live))  # the inlined callee's ret continues after the call
            out = seq(nd[3], set(live))
            armret.pop()
            return out
        if k == "ret":
            if nd[1]:
                return set(armret[-1]) if armret else set(info.G)
            return set(info.ret_live(entry))
        if k == "cont":
            return set(cont[-1]) if cont else set(info.G)
        if k == "brk":
            return set(brk[-1]) if brk else set(info.G)
        if k == "unobs":
            return set()
        if k == "switch":
            out = set()
            for _lbl, arm in nd[1]:
                brk.append(set(live))
                armret.append(set(live))  # a call arm's ret continues after the dispatch
                out |= seq(arm, set(live))  # an arm that falls off its end continues here
                armret.pop()
                brk.pop()
            return out if nd[2] in ("swg", "opsw") else out | set(info.G)
        if k in ("dcall", "dgoto"):
            want = "swc" if k == "dcall" else "swg"
            return (live if _dispatch(nxt) == want else set(info.G)) | uses(nd[1])
        if k == "dbr":
            return set(info.G) | uses(nd[2]) | uses(nd[3])
        if k == "igoto":
            used = uses(nd[2]) if nd[2] is not None else set()
            return (live if nd[2] is None or _dispatch(nxt) == "swg" else set(info.G)) | used
        return set(info.G)

    def call_body(nodes):
        """First index of an own-label some ``call`` enters, else None.

        ``frameproc._Prune.seq``'s rule: such a body returns to its call sites and may
        be entered again, so its exit carries the machine set -- textual fall-through
        under-approximates, and a live register there reads as dead."""
        labs = info.call_labels.get(entry)
        if not labs:
            return None
        for i, nd in enumerate(nodes):
            if nd[0] == "label" and nd[1] in labs:
                return i
        return None

    def seq(nodes, live):
        nxt, cb = None, call_body(nodes)
        for j in range(len(nodes) - 1, -1, -1):
            nd = nodes[j]
            if cb is not None and j >= cb:
                live = live | set(info.G)
            liveout[id(nd)] = set(live)
            live = node(nd, live, nxt)
            nxt = nd
        return live

    def loop(body, brk_live, head):
        brk.append(set(brk_live))
        cont.append(set(head))
        out = seq(body, set(head))
        brk.pop()
        cont.pop()
        return out

    labmap.update({p: set(v) for p, v in info.labmap.get(entry, {}).items()})  # _Flow's seed
    for _i in range(24):
        before = {p: set(v) for p, v in labmap.items()}
        liveout.clear()
        seq(tree, set(info.ret_live(entry)))
        if all(labmap.get(p) == before.get(p) for p in labmap):
            break
    return liveout


def _all_nodes(nodes):
    """Every render-tree node, outermost first."""
    for nd in nodes:
        yield nd
        for b in _child_bodies(nd):
            yield from _all_nodes(b)


Roots = collections.namedtuple("Roots", "ids sid regs")


_IO_LO = 0xD000  # the device window: a store that may reach it is an output, never scratch


def _unread(span, cover, wild):
    """No reader names a byte ``span`` writes, and no device sits inside it.

    Scratch demotion's whole test: a bounded store outside the device window that
    every reader interval is Z3-proved disjoint from reaches no root, so it is not
    one -- a store is observable only where a read of the program names it."""
    if wild or span is None or span[1] + span[2] - 1 >= _IO_LO:
        return False
    return all(_disjoint_spans(span[0], span[1], span[2], b0, b1, 1) for b0, b1 in cover)


def _own_reads(chosen, ext=None):
    """``(byte intervals, wild)`` the extracted spellings of one procedure read.

    Extraction is what retires a read: a pull the store forwards is spelled from the
    pushed value, so the slot has no reader left however the statement was written."""
    spans, wild = set(), False
    for ir in chosen:
        for _ver, span, w in _ir_reads(ir, None, [], ext):
            if span is None:
                wild = True
            else:
                spans.add((span[0], span[1], w))
    return _cover(spans), wild


def _scratch(wrspan, chosen, foot, entry):
    """The store node ids no reader in the artifact can observe.

    The reader set is every other procedure's statements plus this one's extracted
    spellings, since extraction is what retires a read; without the map there is no
    artifact to close over and nothing demotes."""
    if foot is None:
        return frozenset()
    cover, wild = foot.readers(entry)
    own, ownwild = _own_reads(chosen, foot.ext)
    cover, wild = _cover([(a, b, 1) for a, b in cover + own]), wild or ownwild
    return frozenset(i for i, span in wrspan.items() if _unread(span, cover, wild))


def _self_copies(tree, chosen, held):
    """Register asgs whose spelling is the version the register already holds.

    A local renders as its base name, so such a statement prints ``a = a`` and does
    nothing; 3b landing 2 left 11 of them live because the register is live, which is a
    fact about the register and not about the statement."""
    reg = E.frameproc._ALL_REG_LOCALS
    return frozenset(
        id(nd)
        for nd in _all_nodes(tree)
        if nd[0] == "asg" and nd[1] in reg and chosen[nd[2]] == ("loc", held.get(id(nd)))
    )


def roots(tree, info, entry, chosen, dead_stores=(), scratch=(), volatile=()):
    """The observable roots of one rendered procedure (adoption §2), as node ids.

    Sinks are the surviving memory stores (``sid`` names the write-only $D400-$D41C
    ones) and every control statement; the asgs rooted are those a surviving read
    names, register or value local. A store ``scratch`` names is observed by nobody,
    and an asg in ``volatile`` reads an input, whose order is observable."""
    live = _liveness(tree, info, entry, chosen, volatile)
    ids, sid = set(), set()
    for nd in _all_nodes(tree):
        k = nd[0]
        if k == "asg":
            # ``sp`` is machine state, never faint: a ret reads the stack through it
            out = live.get(id(nd))
            if nd[1] == E.frameproc._SP or id(nd) in volatile or out is None or nd[1] in out:
                ids.add(id(nd))
        elif k == "st":
            if id(nd) in dead_stores or id(nd) in scratch:
                continue
            ids.add(id(nd))
            a = nd[1]
            if a[0] == "const" and E._SID_LO <= (a[1] & 0xFFFF) <= E._SID_HI:
                sid.add(a[1] & 0xFFFF)
        else:
            ids.add(id(nd))
    return Roots(frozenset(ids), frozenset(sid), frozenset(info.ret_live(entry)))


def _root_keep(tree, rootids, chosen):
    """Node ids reachable from ``rootids``: a root, or a definition a kept node's
    extracted spelling names. One mechanism for dead flags, scratch and spills."""
    by_name = {nd[3]: nd for nd in _all_nodes(tree) if nd[0] == "asg"}
    keep = set(rootids)
    work = [nd for nd in _all_nodes(tree) if id(nd) in keep]
    while work:
        nd = work.pop()
        names = set()
        for ti in _node_terms(nd):
            _loc_names(chosen[ti], names)
        for nm in names:
            d = by_name.get(nm)
            if d is not None and id(d) not in keep:
                keep.add(id(d))
                work.append(d)
    return keep


def render_proc(
    stmts,
    aliases=None,
    entry=0,
    info=None,
    proofs=None,
    budget=None,
    stats=None,
    foot=None,
    rets=(),
    pairs=None,
    derefs=(),
    demoted=None,
    wall_may=None,
):
    """Render a whole procedure (asg/st/if/loop) via the unified graph + printer.

    Memory forwards intra-block; a join carries the chain-held cells proved disjoint from
    every span it can write. ``budget`` is the procedure's share of the artifact's emit
    seconds, which extraction spends; sites past it render own-term. ``rets``
    are the procedure's declared returns, which a valueless ``ret`` names, ``pairs``
    the declared lo/hi table registry the pack rendering reads, and ``derefs`` the
    pointer cells rung (f) resolved, which a deref address names. ``wall_may`` is
    ``_wall_may``'s map, which bounds a call's wall to the callee's may-define set."""
    deadline = None if budget is None else time.monotonic() + budget
    wall_may = wall_may or {}
    stt = {"env": {}, "mem": mem0(), "k": 0, "held": frozenset(), "memv": 0, "cyc": 0}
    defs, terms, locw, mempairs = [], [], {}, []
    src, seeds, memdefs, wrspan, held, volatile = {}, [], [], {}, {}, set()
    inedge, tgts = {}, _targets(stmts)
    dfs, ch = _Defs(src), _Chain()

    def seed(term, e):
        """Bridge ``e``'s address interval onto the term the address converts to."""
        got = addr_interval(e, dfs)
        if got is not None:
            seeds.append((term, got[0], got[1]))
        return term

    def fresh():
        """One counter for every fresh name: a def version and a havoc version drawn
        from two counters collide on ``<base>.<n>`` and equate unrelated values."""
        stt["k"] += 1
        return stt["k"]

    def remember(chain, wrote):
        """Name the memory version, as a def names a value: a store of a load embeds
        the chain twice, so an unnamed chain is exponential in the number of them."""
        n = fresh()
        memdefs.append((n, chain))
        ch.step(n, stt["memv"], wrote)
        stt["memv"] = n
        return memk(i64(n))

    def join_mem(pre_mem, s, held, pre_ver):
        """Rebind memory at a wall; the held cells the join carries are the new chain."""
        m, kept = _join_mem(pre_mem, [s], fresh, held, None if foot is None else foot.of)
        stt["held"], stt["memv"] = kept, pre_ver
        return remember(m, kept)

    def conv(e):
        k = e[0]
        if k == "const":
            return E.num(e[1] & E._mask(e[2]), e[2])
        if k == "loc":
            # a parameter is read at a width no ``asg`` in this procedure states
            locw.setdefault(e[1], e[2] if len(e) > 2 else 1)
            return E.loc(stt["env"].get(e[1], e[1] + ".0"))
        if k == "mem":
            vol = _may_read_vol(e[1], dfs, None if foot is None else foot.ext)
            m = memk(i64(fresh())) if vol else stt["mem"]
            return sel(m, seed(conv(e[1]), e[1]), e[2])
        mn, kids, w = e[1], e[2], e[3]
        if mn in _REWIDTH:
            return _rewidth(mn, conv(kids[0]), w)
        fn = _OP[mn]
        if mn in _CMP:
            return fn(conv(kids[0]), conv(kids[1]))
        if mn == "INT_CARRY":
            w = E.carry_lane(kids[0])  # the lane the carry is out of, not its own bit
        r = conv(kids[0])
        for kid in kids[1:]:
            r = fn(r, conv(kid), w)
        return r

    def add(t, own=None, ver=None):
        terms.append((t, live(), own, stt["memv"] if ver is None else ver))
        return len(terms) - 1

    def live():
        """The versions a site may spell: the ones its bases denote here.

        A local renders as its base name, so spellability is **denotation**, not
        definition: the base spells whatever version it holds, whether an ``asg``
        rendered it or a boundary produced it, and a version the base has been redefined
        over spells another value however available it once was. A register local the
        procedure has not assigned denotes its block input (``.0``)."""
        env = stt["env"]
        return set(env.values()) | {n + ".0" for n in E.frameproc._ALL_REG_LOCALS if n not in env}

    def havoc(names):
        for n in names:  # a boundary's value: no def renders it, the base still holds it
            stt["env"][n] = "%s.%d" % (n, fresh())
            src.pop(n, None)

    def havoc_locals():
        """Every local a boundary may rewrite: a register the procedure has not assigned
        yet is not in ``env``, and its block input would otherwise stay spellable."""
        havoc(set(stt["env"]) | E.frameproc._ALL_REG_LOCALS)

    def havoc_all():
        havoc_locals()
        n = fresh()
        ch.step(n, None, None)  # ⊤: no read crosses a havoc
        stt["mem"], stt["held"], stt["memv"] = memk(i64(n)), frozenset(), n

    def count(*names):
        """One counter per named statistic; the review reads the label roll-up off it."""
        for n in names:
            if stats is not None and n is not None:
                stats[n] = stats.get(n, 0) + 1

    def in_join(pc):
        """Join the in-edge memories at ``pc`` instead of resetting, or False.

        Every edge must be a ``goto`` this walk has already passed outside any cyclic
        body, so each arrives with a memory version the chain names; a cell every one
        of them reads at one common version keeps that value across the label."""
        got = inedge.get(pc, ())
        if foot is None or foot.wall or stt["cyc"] or not got:
            return False
        if pc in foot.landings or pc in foot.own or pc in tgts[1] or foot.sources(pc) - {entry}:
            return False
        if len(got) != tgts[0].get(pc, 0) or any(c for _v, _h, c in got):
            return False
        ins = [(stt["memv"], stt["held"])] + [(v, h) for v, h, _c in got]
        at = {}
        for c in sorted(frozenset.intersection(*[frozenset(h) for _v, h in ins])):
            common = frozenset.intersection(*[ch.reach(v, (c[0], c[0], c[2])) for v, _h in ins])
            if common:
                at[c] = max(common)
        m = memk(i64(fresh()))
        for a, aw, w in sorted(at):
            addr = E.num(a, aw)
            base = mem0() if at[(a, aw, w)] == 0 else memk(i64(at[(a, aw, w)]))
            m = store(m, addr, sel(base, addr, w), w)
        havoc(list(stt["env"]))
        stt["held"] = frozenset(at)
        stt["mem"] = remember(m, {(a, w): v for (a, _aw, w), v in at.items()})
        count("in_join")
        if stats is not None:
            stats["in_join_cells"] = stats.get("in_join_cells", 0) + len(at)
        return True

    def call_may(s):
        """The locals a call's callee may define, or None where the map cannot follow it.

        The wall retires what the boundary may write, and a call writes what its callee
        may define: a local outside that set holds its version across the call, so a
        value the caller could spell before it spells after. A callee ``_wall_may``
        cannot follow answers None -- the whole wall, unchanged."""
        got = None if s[0] not in ("call", "pcall") else wall_may.get(s[1])
        if got is None:
            return None
        return got | set(s[3]) if s[0] == "pcall" else got

    def wall(s):
        """A boundary the locals cannot cross: memory joins over what it can write."""
        pre_mem, pre_held, pre_ver = stt["mem"], stt["held"], stt["memv"]
        may = call_may(s)
        if may is None:
            havoc_locals()
        else:
            havoc((set(stt["env"]) | E.frameproc._ALL_REG_LOCALS) & may)
        stt["mem"] = join_mem(pre_mem, s, pre_held, pre_ver)

    def walk(sl):
        nodes = []
        for i, s in enumerate(sl):
            k = s[0]
            nxt = sl[i + 1] if i + 1 < len(sl) else None
            if k == "asg":
                rhs = conv(s[2])
                name = "%s.%d" % (s[1], fresh())
                defs.append((name, E.loc(name), rhs))
                w = locw.get(s[2][1], 1) if s[2][0] == "loc" else _ew(s[2])
                locw[s[1]] = w
                nd = ("asg", s[1], add(rhs, ("loc", name)), name, w)
                nodes.append(nd)
                if E.frameproc._reads_vol(s[2]):
                    volatile.add(id(nd))  # an input read's order is observable (iota)
                held[id(nd)] = stt["env"].get(s[1], s[1] + ".0")
                stt["env"][s[1]] = name
                src[s[1]] = s[2]
            elif k == "st":
                v = conv(s[2])
                a = s[1]
                addr = E.num(a[1] & E._mask(a[2]), a[2]) if a[0] == "const" else seed(conv(a), a)
                pre, pre_ver, w = stt["mem"], stt["memv"], _ew(s[2])
                cell = a[1] & E._mask(a[2]) if a[0] == "const" else None
                got = _wr_span(a, dfs) if cell is None else (cell, cell)
                ai = add(addr, ver=pre_ver) if cell is None else None
                own = None if cell is None else ("cell", cell, w, 0)
                nd = ("st", a, add(v, own, ver=pre_ver), ai, w, E.frameproc.hi_first(s))
                stt["mem"] = remember(store(pre, addr, v, w), None if got is None else got + (w,))
                if a[0] == "const":
                    stt["held"] |= {(cell, a[2], w)}
                nodes.append(nd)
                wrspan[id(nd)] = None if got is None else got + (w,)
                mempairs.append((nd, pre, stt["mem"]))
            elif k == "if":
                cond = conv(s[2])
                if s[1] == "ifnot":
                    cond = E.bnot(cond)
                ci = add(cond)
                pre_env, pre_mem = dict(stt["env"]), stt["mem"]
                pre_src, pre_held, pre_ver = dict(src), stt["held"], stt["memv"]
                then = walk(s[3])
                then_env = dict(stt["env"])
                stt["env"], stt["mem"], stt["held"] = dict(pre_env), pre_mem, pre_held
                stt["memv"] = pre_ver
                src.clear()
                src.update(pre_src)
                els = walk(s[4])
                els_env = dict(stt["env"])
                stt["env"], stt["mem"] = dict(pre_env), join_mem(pre_mem, s, pre_held, pre_ver)
                src.clear()
                src.update(pre_src)
                for n in set(pre_env) | set(then_env) | set(els_env):
                    c = pre_env.get(n)
                    if not (then_env.get(n) == c and els_env.get(n) == c):
                        stt["env"][n] = "%s.%d" % (n, fresh())
                        src.pop(n, None)
                nodes.append(("if", ci, then, els))
            elif k in ("loop", "for"):
                # a ``for``'s counter is written by the header, so it havocs with the body
                stmts_of, wr = (s[1], set()) if k == "loop" else (s[4], {s[1]})
                pre_mem, pre_held = stt["mem"], stt["held"]
                pre_ver = stt["memv"]
                havoc(_written(stmts_of) | wr)
                stt["mem"] = join_mem(pre_mem, s, pre_held, pre_ver)
                stt["cyc"] += 1
                body = walk(stmts_of)
                stt["cyc"] -= 1
                havoc(_written(stmts_of) | wr)
                stt["mem"] = join_mem(pre_mem, s, pre_held, pre_ver)
                nodes.append(("loop", body) if k == "loop" else ("for", s[1], s[2], s[3], body))
            elif k == "label":
                if foot is None or foot.joins(s[1]):
                    if not in_join(s[1]):
                        havoc_all()
                        count("label_reset", "label_forward" if inedge.get(s[1]) else None)
                nodes.append(("label", s[1]))
            elif k in ("goto", "cont", "brk", "ret", "unobs"):
                if k == "goto":
                    inedge.setdefault(s[1], []).append((stt["memv"], stt["held"], stt["cyc"]))
                nodes.append((k, s[1] if len(s) > 1 else None))
            elif k == "call":
                wall(s)
                nodes.append(("call", s[1], s[2]))
            elif k == "pcall":
                ixs = [add(conv(a)) for a in s[2]]  # arguments read before the callee runs
                wall(s)
                nodes.append(("pcall", s[1], ixs, list(s[3])))
            elif k == "callb":
                havoc([E.frameproc._SP])  # the machine call moved the stack pointer
                body = walk(s[3])
                wall(("call", s[1], s[2]))  # what the call writes beyond the inlined body
                nodes.append(("callb", s[1], s[2], body))
            elif k == "dcall":
                ix = add(conv(s[1]))
                if not _armed(s, nxt):
                    havoc_all()
                nodes.append(("dcall", ix, s[2]))
            elif k in ("swc", "opsw", "swg"):
                cases = s[1] if k == "swg" else s[2]
                pre_env, pre_mem = dict(stt["env"]), stt["mem"]
                pre_src, pre_held, arms = dict(src), stt["held"], []
                pre_ver = stt["memv"]
                for lbl, body in cases:
                    stt["env"], stt["mem"], stt["held"] = dict(pre_env), pre_mem, pre_held
                    stt["memv"] = pre_ver
                    if k == "swc":
                        havoc([E.frameproc._SP])  # a call arm is entered by a machine call
                    src.clear()
                    src.update(pre_src)
                    arms.append((lbl, walk(body)))
                stt["mem"], stt["held"], stt["memv"] = pre_mem, pre_held, pre_ver
                wall(s)
                nodes.append(("switch", arms, k, None if k == "swg" else s[1]))
            elif k == "dbr":
                pair = (add(conv(s[2])), add(conv(s[3])))
                havoc_all()
                nodes.append(("dbr", s[1], pair[0], pair[1], s[4]))
            elif k == "dgoto":
                ix = add(conv(s[1]))
                if not _armed(s, nxt):
                    havoc_all()
                nodes.append(("dgoto", ix))
            elif k == "igoto":
                ix = add(conv(s[2])) if s[2] is not None else None
                if not _armed(s, nxt):
                    havoc_all()
                nodes.append(("igoto", s[1], ix))
            else:
                raise ValueError("unliftable statement %r" % (k,))
        return nodes

    tree = walk(stmts)
    rs, _names = unified_rules()
    eg = EGraph()
    for _n, leaf, rhs in defs:
        eg.register(union(leaf).with_(rhs))
    for n, chain in memdefs:
        eg.register(union(memk(i64(n))).with_(chain))
    for t, l, h in seeds:  # the interval bridge: bit analyses read as e-class bounds
        eg.register(set_(lo(t)).to(i64(l)), set_(hi(t)).to(i64(h)))
    handles = [eg.let("h%d" % i, t) for i, (t, _av, _o, _v) in enumerate(terms)]
    memh = [
        (nd, eg.let("mp%d" % i, p), eg.let("mq%d" % i, q)) for i, (nd, p, q) in enumerate(mempairs)
    ]
    saturate(eg, rs)
    pr = E._Printer(aliases or {}, pairs, locw, derefs)

    def own_ir(i):
        """The site's own term: position-correct by construction, and what a spent
        extraction budget falls back to -- extraction is sound at any cutoff.

        Every leaf of it came from ``conv`` reading the site's own ``env``, so each is a
        version its base denotes there and the fallback needs no ``_defined_at``: what
        ``live()`` refuses a candidate for, the own term cannot carry."""
        raw = E._parse_ir(str(terms[i][0]))
        return _to_ir(raw), raw

    def price(c, ver):
        """Adoption §4's order as a price, not a filter: a memory spelling costs more
        than every value one, and among memory spellings the deepest read wins — the
        source rather than a copy of it, so the copies go unread. None refuses."""
        got = _sel_reads(c[1], [])
        if not got:
            return 0, 0, E._cost(c[0]), repr(c[0])
        depth = ch.ok(ver, got)
        return None if depth is None else (1, -depth, E._cost(c[0]), repr(c[0]))

    def pick_ir(i):
        _t, av, own, ver = terms[i]
        cands = [
            (_to_ir(r), r)
            for r in (E._parse_ir(str(x)) for x in eg.extract_multiple(handles[i], 12))
        ]
        own_name = own[1] if own is not None and own[0] == "loc" else None

        def ok(c):
            got = []
            _count_locs(c, got)
            return c != own and (own_name is None or own_name not in got)

        kept = [(price(c, ver), c) for c in cands if ok(c[0]) and _defined_at(c[0], av)]
        kept = [(p, c) for p, c in kept if p is not None]
        if not kept:
            raw = own_ir(i)  # the site's own term reads its own version: always in position
            if ok(raw[0]):
                kept = [(price(raw, ver), raw)]
        if not kept:
            return min(cands, key=lambda c: (E._cost(c[0]), repr(c[0])))
        return min(kept, key=lambda pc: pc[0])[1]

    spent = 0
    picked = []
    for i in range(len(terms)):
        if deadline is not None and time.monotonic() >= deadline:
            spent += 1
            picked.append(own_ir(i))
        else:
            picked.append(pick_ir(i))
    if stats is not None:
        stats["sites"] = stats.get("sites", 0) + len(terms)
        stats["extract_fallback"] = stats.get("extract_fallback", 0) + spent
    chosen = [c for c, _raw in picked]
    if info is None:
        info = E.frameproc._Info([(entry, stmts)], entry)
        info.summarize()
    gone = {id(nd) for nd, p, q in memh if eg.check_bool(egg_eq(p).to(q))}
    scratch = _scratch(wrspan, chosen, foot, entry)
    if demoted is not None:
        demoted.update(wrspan[i][:2] for i in scratch if wrspan[i] is not None)
    noop = _self_copies(tree, chosen, held)
    if stats is not None:
        stats["scratch"] = stats.get("scratch", 0) + len(scratch)
        stats["self_copy"] = stats.get("self_copy", 0) + len(noop)
    keep = _root_keep(tree, roots(tree, info, entry, chosen, gone, scratch, volatile).ids, chosen)
    dead = ({id(nd) for nd in _all_nodes(tree)} - keep) | noop
    _share_once(tree, dead, chosen, terms, ch)
    if proofs is not None:
        pairs = proofs.setdefault("pairs", [])
        for nd in _all_nodes(tree):
            if id(nd) not in dead:
                pairs.extend(
                    (E._parse_ir(str(terms[ti][0])), picked[ti][1]) for ti in _node_terms(nd)
                )
        proofs.setdefault("defs", {}).update(
            (name, E._parse_ir(str(rhs))) for name, _leaf, rhs in defs
        )
        proofs.setdefault("mems", {}).update((n, E._parse_ir(str(c))) for n, c in memdefs)
        proofs.setdefault("locw", {}).update(locw)

    def pick(i):
        return pr.fmt(chosen[i])

    def column(nd):
        """``(base, index term or None)`` a live byte store writes, else None."""
        if nd[0] != "st" or nd[4] != 1 or id(nd) in dead:
            return None
        a = nd[1]
        return (a[1] & 0xFFFF, None) if a[0] == "const" else pr._split(chosen[nd[3]])

    def pair_store(a, b):
        """``(lo base, index, word)`` where two byte stores write one declared pair.

        ``frameproc._pair_halves`` over the text the two lines would print: the halves
        are the two truncs of one word, the columns are the registry's, and the order
        is the declared one (lo then hi). The comparison is on the printed form because
        that is what the lines read -- a local renders as its base name."""
        if not pr.pairs:
            return None
        lcol, hcol = column(a), column(b)
        if lcol is None or hcol is None or (lcol[1] is None) != (hcol[1] is None):
            return None
        if lcol[1] is not None and pr.fmt(lcol[1]) != pr.fmt(hcol[1]):
            return None
        site = pr._pair_columns(lcol[0], hcol[0], lcol[1])
        if site is None:
            return None
        vl, vh = chosen[a[2]], chosen[b[2]]
        if vl[0] != "trunc" or vh[0] != "trunc":
            return None
        sh = vh[1]
        if sh[0] != "shr" or sh[2][0] != "num" or sh[2][1] != 8:
            return None
        return (site[0], site[1], vl[1]) if pr.fmt(sh[1]) == pr.fmt(vl[1]) else None

    def shown(nodes, i):
        """The next index at ``i`` or after whose node prints a line, else None."""
        while i < len(nodes):
            if nodes[i][0] not in ("asg", "st") or id(nodes[i]) not in dead:
                return i
            i += 1
        return None

    def render(nodes, d):
        i = 0
        while i < len(nodes):
            nd = nodes[i]
            i += 1
            j = shown(nodes, i)  # the pair's halves are adjacent in the TEXT
            got = None if j is None else pair_store(nd, nodes[j])
            if got is not None:
                pr.line("%s[%s]:2 = %s" % (pr.name(got[0]), pr.fmt(got[1]), pr.fmt(got[2])), d + 1)
                i = j + 1
                continue
            if nd[0] == "asg":
                if id(nd) in dead:
                    continue
                pr.line("%s = %s" % (nd[1] + E.sidprog._wsuf(nd[4]), pick(nd[2])), d + 1)
            elif nd[0] == "st":
                if id(nd) in dead:
                    continue
                a = nd[1]
                if a[0] == "const":
                    dest = pr.name(a[1]) + E.sidprog._wsuf(nd[4])
                else:
                    dest = pr._loadref(("load", chosen[nd[3]], nd[4], 0))
                order = "hi-first " if nd[5] else ""
                pr.line("%s%s = %s" % (order, dest, pick(nd[2])), d + 1)
            elif nd[0] == "if":
                cond = chosen[nd[1]]
                word, inner = ("ifnot", cond[1]) if cond[0] == "bnot" else ("if", cond)
                head = "%s %s" % (word, pr.fmt(inner))
                if len(nd[2]) == 1 and nd[2][0][0] == "unobs":
                    pr.line("%s unobserved $%04X" % (head, nd[2][0][1]), d)
                    render(nd[3], d)
                    continue
                pr.line(head + " {", d)
                render(nd[2], d + 1)
                if len(nd[3]) == 1 and nd[3][0][0] == "unobs":
                    pr.line("} else unobserved $%04X" % nd[3][0][1], d)
                    continue
                if any(n[0] not in ("asg", "st") or id(n) not in dead for n in nd[3]):
                    pr.line("} else {", d)
                    render(nd[3], d + 1)
                pr.line("}", d)
            elif nd[0] == "loop":
                pr.line("loop {", d)
                render(nd[1], d + 1)
                pr.line("}", d)
            elif nd[0] == "for":
                pr.line("for %s in $%02X..$%02X {" % (nd[1], nd[2], nd[3]), d)
                render(nd[4], d + 1)
                pr.line("}", d)
            elif nd[0] == "label":
                pr.line("$%04X:" % nd[1], d)
            elif nd[0] == "goto":
                pr.line("goto $%04X" % nd[1], d)
            elif nd[0] == "ret":
                named = rets and not nd[1]
                pr.line("ret %s" % ", ".join(rets) if named else "ret", d + 1)
            elif nd[0] in ("cont", "brk"):
                pr.line("continue" if nd[0] == "cont" else "break", d)
            elif nd[0] == "unobs":
                pr.line("unobserved $%04X" % nd[1], d)
            elif nd[0] == "call":
                pr.line("call $%04X ret $%04X" % (nd[1], nd[2]), d + 1)
            elif nd[0] == "callb":
                pr.line("call $%04X ret $%04X {" % (nd[1], nd[2]), d + 1)
                render(nd[3], d + 2)
                pr.line("}", d + 1)
            elif nd[0] == "dcall":
                pr.line("call (%s) ret $%04X" % (pick(nd[1]), nd[2]), d + 1)
            elif nd[0] == "pcall":
                text = "sub_%04X(%s)" % (nd[1], ", ".join(pick(i) for i in nd[2]))
                pr.line("%s = %s" % (", ".join(nd[3]), text) if nd[3] else text, d + 1)
            elif nd[0] == "switch":
                if nd[2] == "swc" and not nd[1]:
                    body = " ".join(nd[3])
                    pr.line("switch call { %s }" % body if body else "switch call { }", d)
                    continue
                pr.line(_switch_head(pr, nd), d)
                if nd[2] == "swc" and nd[3]:
                    pr.line(" ".join(nd[3]), d + 1)
                for lbl, arm in nd[1]:
                    pr.line("case %s: {" % lbl, d + 1)
                    render(arm, d + 2)
                    pr.line("}", d + 1)
                pr.line("}", d)
            elif nd[0] == "dbr":
                text = "%s %s goto (%s) else $%04X"
                pr.line(text % (nd[1], pick(nd[2]), pick(nd[3]), nd[4]), d + 1)
            elif nd[0] == "dgoto":
                pr.line("goto (%s)" % pick(nd[1]), d + 1)
            elif nd[0] == "igoto":
                ptr = "(%s)" % pick(nd[2]) if nd[2] is not None else "$%04X" % nd[1]
                pr.line("igoto %s" % ptr, d + 1)
            else:
                raise ValueError("unprintable node %r" % (nd[0],))

    render(tree, 0)
    return pr.out


_CMPS = frozenset(("eq", "ne", "ult", "ule", "slt", "sge"))


class _Z3Env:
    """Z3 reading of an extracted term: values are BV16 masked to their own width,
    memory an array BV16 -> BV8 whose ``mem0``/``memk`` leaves are opaque."""

    def __init__(self, locw=None, mems=None):
        self.locw = locw or {}
        self.mems = mems or {}  # named memory versions; the unnamed ones stay opaque
        self.locs, self.arrs, self.constraints = {}, {}, []
        self.memo = {}  # extracted IR is a DAG; rebuilt as a tree it is exponential

    def _arr(self, key):
        a = self.arrs.get(key)
        if a is None:
            a = self.arrs[key] = z3.Array(key, z3.BitVecSort(16), z3.BitVecSort(8))
        return a

    def _loc(self, name):
        v = self.locs.get(name)
        if v is None:
            v = self.locs[name] = z3.BitVec("L_" + name.replace(".", "_"), 16)
        return v

    def width(self, ir):
        k = ir[0]
        if k == "num":
            return ir[2]
        if k == "sel":
            return ir[3]
        if k in ("cell", "load"):
            return ir[2]
        if k == "loc":
            base = ir[1].rpartition(".")[0]
            return 1 if base in E.frameproc._ALL_REG_LOCALS else self.locw.get(base, 2)
        if k == "zext":
            return 2
        if k == "trunc":
            return 1
        return 1 if k in _CMPS or k in ("bnot", "carry") else ir[-1]

    def close(self, defs):
        """Definitional equations for every reachable SSA local, plus the byte-range
        assumption on the free ones; call after the terms are read."""
        out, seen = [], set()
        while True:
            todo = [n for n in list(self.locs) if n not in seen and n in defs]
            if not todo:
                break
            for n in todo:
                seen.add(n)
                out.append(self._loc(n) == self.of(defs[n]))
        free = [n for n in self.locs if n not in defs]
        return out + [z3.ULE(self._loc(n), E._mask(self.width(("loc", n)))) for n in free]

    def memory(self, ir):
        got = self.memo.get(ir)
        if got is None:
            got = self.memo[ir] = self._memory(ir)
        return got

    def _memory(self, ir):
        k = ir[0]
        if k == "mem0":
            return self._arr("m0")
        if k == "memk":
            got = self.mems.get(ir[1])
            return self._arr("mk%d" % ir[1]) if got is None else self.memory(got)
        if k == "store":
            v = z3.Extract(8 * ir[4] - 1, 0, self.of(ir[3]))
            return _store_w(self.memory(ir[1]), self.of(ir[2]), v, ir[4])
        raise ValueError("unreadable memory %r" % (k,))

    def of(self, ir):
        got = self.memo.get(ir)
        if got is None:
            got = self.memo[ir] = self._of(ir)
        return got

    def _of(self, ir):
        k = ir[0]
        if k == "num":
            return z3.BitVecVal(ir[1] & E._mask(ir[2]), 16)
        if k == "loc":
            return self._loc(ir[1])
        if k == "sel":
            return z3.ZeroExt(16 - 8 * ir[3], _sel_w(self.memory(ir[1]), self.of(ir[2]), ir[3]))
        if k == "zext":
            return self.of(ir[1])
        if k == "trunc":
            return self.of(ir[1]) & 0xFF
        if k == "bnot":
            return _b16(self.of(ir[1]) == 0)
        if k in ("slt", "sge"):
            x, y = (self._signed(a) for a in ir[1:3])
            return _b16(x < y if k == "slt" else x >= y)
        if k in _CMPS:
            x, y = self.of(ir[1]), self.of(ir[2])
            return _b16({"eq": x == y, "ne": x != y, "ult": z3.ULT(x, y), "ule": z3.ULE(x, y)}[k])
        x, y, w = self.of(ir[1]), self.of(ir[2]), ir[-1]
        if k == "carry":
            wide = z3.ZeroExt(1, x) + z3.ZeroExt(1, y)
            return _b16(z3.UGT(wide, z3.BitVecVal(E._mask(w), 17)))
        if k == "shr":
            return z3.LShR(x & E._mask(w), y)
        v = {"add": x + y, "sub": x - y, "band": x & y, "bor": x | y, "bxor": x ^ y}.get(k)
        return (x << y if k == "shl" else v) & E._mask(w)

    def _signed(self, ir):
        w = self.width(ir)
        return (
            z3.SignExt(16 - 8 * w, z3.Extract(8 * w - 1, 0, self.of(ir))) if w < 2 else self.of(ir)
        )


def _b16(cond):
    return z3.If(cond, z3.BitVecVal(1, 16), z3.BitVecVal(0, 16))


def verify_sites(sites):
    """Z3-prove every recorded site equal to what extraction chose; count proved.

    Adoption §6's all-rewritten-sites law, under the SSA/memory definitional
    equations: both sides are raw extracted forms, so a forwarded load is proven
    against the array encoding of its own store chain, not replayed."""
    defs, env = sites.get("defs", {}), _Z3Env(sites.get("locw"), sites.get("mems"))
    goals = [
        (site, got, env.of(site) != env.of(got))
        for site, got in sites.get("pairs", ())
        if site != got
    ]
    if not goals:
        return len(sites.get("pairs", ()))
    s = z3.Solver()
    s.add(*env.close(defs))
    if s.check() != z3.sat:
        raise AssertionError("site environment is unsatisfiable: proofs would be vacuous")
    for site, got, goal in goals:
        s.push()
        s.add(goal)
        ok = s.check() == z3.unsat
        s.pop()
        if not ok:
            raise AssertionError("site %r is not equivalent to %r" % (site, got))
    return len(sites["pairs"])


def render_roots(rootterms, aliases=None):
    """Print a forest of root terms with common subterms shared as named temps.

    ``rootterms`` is [(dest, term)]. Subterms used more than once become ``t<k>``
    locals (let-binding); a subterm in no root never prints -- dead code drops out."""
    rs, _names = unified_rules()
    eg = EGraph()
    handles = [(dest, eg.let("r%d" % i, t)) for i, (dest, t) in enumerate(rootterms)]
    saturate(eg, rs)
    irs = [(dest, to_ir(str(eg.extract(h)))) for dest, h in handles]
    counts = {}
    for _dest, ir in irs:
        _count(ir, counts)
    shared = {}
    for k, ir in enumerate(
        sorted((t for t, c in counts.items() if c >= 2 and _nameable(t)), key=_size)
    ):
        shared[ir] = "t%d" % k
    pr = E._Printer(aliases or {})
    for sub in sorted(shared, key=_size):
        pr.line("%s = %s" % (shared[sub], pr.fmt(_share(sub, shared, True))), 0)
    for dest, ir in irs:
        pr.line("%s = %s" % (dest, pr.fmt(_share(ir, shared, False))), 0)
    return pr.out


def _nameable(ir):
    return isinstance(ir, tuple) and ir[0] not in ("num", "loc", "cell")


def _count(ir, counts):
    if isinstance(ir, tuple):
        counts[ir] = counts.get(ir, 0) + 1
        for a in ir[1:]:
            _count(a, counts)


def _size(ir):
    return 1 + sum(_size(a) for a in ir[1:] if isinstance(a, tuple)) if isinstance(ir, tuple) else 1


def _share(ir, shared, top):
    if not top and ir in shared:
        return ("loc", shared[ir] + ".0")
    if not isinstance(ir, tuple) or ir[0] in ("num", "loc", "cell"):
        return ir
    return (ir[0],) + tuple(_share(a, shared, False) if isinstance(a, tuple) else a for a in ir[1:])


def to_ir(egg_str):
    """Translate an extracted unified-graph term into eqlift printer IR.

    A forwarded value maps straight across; an unresolved ``sel(mem, addr, w)``
    becomes a ``load`` (the opaque memory is the heap), and bare locals get a
    ``.0`` version so the printer's name logic applies."""
    return _to_ir(E._parse_ir(egg_str))


def _to_ir(ir):
    if not isinstance(ir, tuple):
        return ir
    if ir[0] == "sel":
        addr = _to_ir(ir[2])
        if addr[0] == "num":  # a constant-address load prints as the named cell
            return ("cell", addr[1], ir[3], 0)
        return ("load", addr, ir[3], 0)
    if ir[0] == "loc" and "." not in ir[1]:
        return ("loc", ir[1] + ".0")
    return tuple(_to_ir(a) if isinstance(a, tuple) else a for a in ir)


def _ew(e):
    """Byte width of a pass-1 expression value (``grammar.store_width``'s rule).

    A local states its own width where the tree carries one, which is what a word
    local assigned into a byte-spelled destination would otherwise lose."""
    k = e[0]
    if k in ("const", "mem"):
        return e[2]
    if k == "loc":
        return e[2] if len(e) > 2 else 1
    if k == "op":
        mn = e[1]
        if mn == "INT_ZEXT":
            return 2
        return 1 if mn in _CMP or mn == "INT_CARRY" else e[3]
    return 1


class Straight:
    """Straight-line lift over the unified graph: a load reads the current store
    chain (McCarthy forwarding does the aliasing), a store extends it. No SSA
    versioning -- memory position in the chain is the only state."""

    def __init__(self):
        self.env = {}
        self.mem = mem0()

    def run(self, stmts):
        for s in stmts:
            if s[0] == "asg":
                self.env[s[1]] = self._conv(s[2])
            elif s[0] == "st":
                self.mem = store(self.mem, self._addr(s[1]), self._conv(s[2]), _ew(s[2]))
            else:
                break
        return self

    def _addr(self, a):
        return E.num(a[1] & E._mask(a[2]), a[2]) if a[0] == "const" else self._conv(a)

    def _conv(self, e):
        k = e[0]
        if k == "const":
            return E.num(e[1] & E._mask(e[2]), e[2])
        if k == "loc":
            return self.env.get(e[1], E.loc(e[1]))
        if k == "mem":
            return sel(self.mem, self._conv(e[1]), e[2])
        if k != "op":
            raise ValueError("unencodable %r" % (k,))
        mn, kids, w = e[1], e[2], e[3]
        if mn in _REWIDTH:
            return _rewidth(mn, self._conv(kids[0]), w)
        fn = _OP[mn]
        if mn in _CMP:
            return fn(self._conv(kids[0]), self._conv(kids[1]))
        r = self._conv(kids[0])
        for kid in kids[1:]:
            r = fn(r, self._conv(kid), w)
        return r


_READS = {"dcall": (1,), "dbr": (2, 3), "dgoto": (1,), "igoto": (2,)}
_NOEFFECT = frozenset(("goto", "cont", "brk", "ret", "unobs"))


class Proc(Straight):
    """Whole-procedure lift: intra-block store-chain forwarding, with written locals
    reset to fresh opaque terms (the algebraic havoc) at branch joins and loop heads,
    and everything reset at labels and dynamic control. Memory joins through
    ``_join_mem``, so cells no reachable store can name keep forwarding."""

    def __init__(self, foot=None):
        super().__init__()
        self.sites = []
        self.held = frozenset()
        self.foot = foot
        self._k = 0

    def _fresh(self):
        self._k += 1
        return self._k

    def _havoc_locs(self, names):
        for n in names:
            self.env[n] = E.loc("%s@%d" % (n, self._fresh()))

    def _havoc_all(self):
        self._havoc_locs(list(self.env))
        self.mem, self.held = memk(i64(self._fresh())), frozenset()

    def _join(self, pre_mem, stmts, held):
        enter = None if self.foot is None else self.foot.of
        self.mem, self.held = _join_mem(pre_mem, stmts, self._fresh, held, enter)

    def run(self, stmts):
        for s in stmts:
            self._stmt(s)
        return self

    def _stmt(self, s):
        k = s[0]
        if k == "asg":
            t = self._conv(s[2])
            self.env[s[1]] = t
            self.sites.append(("asg", s[1], t))
        elif k == "st":
            v, a = self._conv(s[2]), s[1]
            self.mem = store(self.mem, self._addr(a), v, _ew(s[2]))
            if a[0] == "const":
                self.held |= {(a[1] & E._mask(a[2]), a[2], _ew(s[2]))}
            self.sites.append(("st", a, v))
        elif k == "if":
            cond = self._conv(s[2])
            if s[1] == "ifnot":
                cond = E.bnot(cond)
            self.sites.append(("if", cond))
            self._branch([s[3], s[4]], s)
        elif k == "loop":
            self._loop(s)
        elif k == "callb":
            self.run(s[3])
            pre_mem, pre_held = self.mem, self.held
            self._havoc_locs(list(self.env))
            self._join(pre_mem, [("call", s[1], s[2])], pre_held)
        elif k in ("swc", "opsw"):
            self._branch([b for _l, b in s[2]], s)
        elif k == "swg":
            self._branch([b for _l, b in s[1]], s)
        elif k == "call":
            pre_mem, pre_held = self.mem, self.held
            self._havoc_locs(list(self.env))
            self._join(pre_mem, [s], pre_held)
        elif k not in _NOEFFECT:
            for i in _READS.get(k, ()):
                if s[i] is not None:
                    self.sites.append((k, self._conv(s[i])))
            self._havoc_all()

    def _branch(self, bodies, stmt):
        pre_env, pre_mem, pre_held = dict(self.env), self.mem, self.held
        ends = []
        for b in bodies:
            self.env, self.mem, self.held = dict(pre_env), pre_mem, pre_held
            self.run(b)
            ends.append((self.env, self.mem))
        self.env = dict(pre_env)
        self._join(pre_mem, [stmt], pre_held)
        names = set(pre_env).union(*(e for e, _m in ends))
        for n in names:
            terms = [e.get(n) for e, _m in ends] + [pre_env.get(n)]
            if any(t is not terms[0] for t in terms):
                self.env[n] = E.loc("%s@%d" % (n, self._fresh()))

    def _loop(self, stmt):
        body, pre_mem, pre_held = stmt[1], self.mem, self.held
        w = _written(body)
        self._havoc_locs(w)
        self._join(pre_mem, [stmt], pre_held)
        self.run(body)
        self._havoc_locs(w)
        self._join(pre_mem, [stmt], pre_held)


def _written(stmts):
    out = set()
    for s in stmts:
        if s[0] in ("asg", "for"):
            out.add(s[1])
        elif s[0] == "pcall":
            out.update(s[3])
        for b in E.frameproc._stmt_bodies(s):
            out |= _written(b)
    return out


def render_ctx(prog):
    """``(call summaries, footprints, pairs, derefs)`` the unified renderer reads.

    Every part is read off the analysed program: the summary ``repolish`` computed, the
    landings and extents the memory join consumes, the ONE lo/hi registry and rung (f)'s
    resolved pointer cells. ``landings`` is the one model fact, carried since ``program``."""
    flat = [(entry, stmts) for entry, _p, _r, stmts in prog.procs]
    info = E.frameproc._Info(flat, prog.play)
    info.summarize()
    for _round in range(3):
        before = ({e: list(v) for e, v in info.params.items()}, dict(info.rets))
        info.summarize()
        if before == (info.params, info.rets):
            break
    foot = Footprints(
        flat,
        info.open_flow,
        prog.landings or (),
        _extent_spans(prog.extents, prog.data_decls),
    )
    return (
        info,
        foot,
        E.datadecl.decl_pairs(prog.data_decls),
        {c for c, _i in prog.resolved.values()},
    )


def artifact_lines(prog, proofs=None, demoted=None):
    """The artifact's procedure lines: every body from the unified graph (§8 step 4).

    The headers are the program's own; a body is one saturation and one root extraction.
    ``proofs`` collects §6's per-procedure site record -- SSA names are per procedure, so
    one merged record would prove the wrong equalities."""
    info, foot, pairs, derefs = render_ctx(prog)
    wall_may, out = _wall_may(info), []
    for entry, params, rets, stmts in prog.procs:
        sig = "sub_%04X(%s)" % (entry, ", ".join(params))
        if rets:
            sig += " -> %s" % ", ".join(rets)
        out.append(sig + " {")
        rec = None if proofs is None else {}
        body = render_proc(
            stmts,
            prog.symbols,
            entry,
            info,
            proofs=rec,
            foot=foot,
            rets=rets,
            pairs=pairs,
            derefs=derefs,
            demoted=demoted,
            wall_may=wall_may,
        )
        if rec is not None:
            proofs.append(rec)
        out.extend(" " + ln for ln in body)
        out.append("}")
    return out


def _extent_spans(extents, decls):
    """``{pointer cell: (lo, hi)}``: the interval 2b observed a web's derefs inside.

    The declared blocks the extent names, read as one interval over the registry that
    declared them -- the record is an observation and this is its only reading."""
    size = {d["base"]: d["size"] for d in decls}
    out = {}
    for cell, bases in (extents or {}).items():
        got = [(b, b + size[b] - 1) for b in bases if size.get(b)]
        if got:
            out[cell] = (min(a for a, _b in got), max(b for _a, b in got))
    return out


def _work(stmts):
    """A procedure's statement-node count: what its share of the emit budget buys.

    Dividing by the procedure COUNT gives the largest procedure -- index 0 in almost
    every multi-procedure tune -- the same seconds as a one-site trailer, so the head
    degrades while the tail returns its share unspent."""
    n = 0
    for s in stmts:
        n += 1
        for b in E.frameproc._stmt_bodies(s):
            n += _work(b)
    return n


def emit_mem(model, proofs=None, stats=None, extents=None):
    """The prototype's pre-rung substrate: ``eqlift`` text over raw ``_Builder`` procedures.

    NOT the artifact and never a second projection of it -- since §8 step 4's switch the
    artifact is ``frameprog.dumps``. Its one consumer is
    ``examples/state_machine_lift.py``, whose fold layer is stated over the byte lanes
    rung (d) fuses, and it retires with that layer at landing 4. ``proofs`` takes one §6
    site record per procedure and ``EMIT_S`` is split over them by work."""
    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = E.datadecl.declarations(model)
    trees, labels, view = E.sidprog._model_trees(model)
    head = ["eqlift 0"]
    head.extend(E._EMIT_NOTES)
    head.append("play $%04X" % model.play)
    head.append("init $%04X" % model.init)
    if getattr(model, "subtune", 0):
        head.append("subtune %d" % model.subtune)
    prologue = getattr(model, "prologue", ())
    if prologue:
        head.append("sid-init {")
        head.extend("  $%02X = $%02X" % (r, v) for r, v in prologue)
        head.append("}")
    state, inputs = E.sidprog._state_lines(view, decls, model.dispatch_sets)
    if inputs:
        head.append("inputs { %s }" % " ".join(inputs))
    header_body, _cov = E.sidprog._data_lines(decls, model.mem0)
    header_body = state + header_body
    to_alias = E.sidprog._alias_sub(aliases)
    if to_alias is not None:
        header_body = list(map(to_alias, header_body))
    procs = []
    for entry, root in trees:
        conv = E.frameproc._Conv(E.frameproc._Names(aliases))
        builder = E.frameproc._Builder(labels, set(model.dispatch_sets), view, conv)
        procs.append((entry, builder.proc(root)))
    info = E.frameproc._Info(procs, model.play)
    info.summarize()
    for _round in range(3):
        before = ({e: list(v) for e, v in info.params.items()}, dict(info.rets))
        info.summarize()
        if before == (info.params, info.rets):
            break
    proc_lines, end = [], time.monotonic() + EMIT_S
    from . import framefuse  # pylint: disable=import-outside-toplevel

    foot = Footprints(
        procs, info.open_flow, framefuse._landings(model), _extent_spans(extents, decls)
    )
    weights, wall = [_work(stmts) for _e, stmts in procs], _wall_may(info)
    left = sum(weights)
    for i, (entry, stmts) in enumerate(procs):
        proc_lines.append("sub_%04X {" % entry)
        rec = None if proofs is None else {}
        share = max(0.0, end - time.monotonic()) * (weights[i] / left if left else 1.0)
        left -= weights[i]
        body = render_proc(stmts, aliases, entry, info, rec, share, stats, foot, wall_may=wall)
        if rec is not None:
            proofs.append(rec)
        proc_lines.extend(" " + ln for ln in body)
        proc_lines.append("}")
    from . import eqlift_annotate  # pylint: disable=import-outside-toplevel

    tr = eqlift_annotate.aggregate([s for _e, s in procs], model)
    header_body = eqlift_annotate.annotate_lines(header_body, tr, model, aliases)
    lines = head + header_body + E.sidprog._symbol_lines(aliases) + proc_lines
    return "\n".join(lines) + "\n"


def emit(model, proofs=None, stats=None, extents=None):
    """Whole-artifact eqlift text via the unified value+memory e-graph lifter.

    Thin wrapper over ``emit_mem``; returns ``(text, None)`` so callers that still
    unpack a second value keep working. Soundness is the rule/axiom admission gate
    (``verify_rules`` + ``verify_axioms``) run once inside ``unified_rules``."""
    return emit_mem(model, proofs, stats, extents), None
