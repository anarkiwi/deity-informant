"""Unified value+memory e-graph: McCarthy select/store axioms + interval disjointness.

Memory becomes a first-class sort over eqlift's value algebra: store-to-load
forwarding, spill elimination, disjoint read-through and dead-store removal fall
out of one saturation + root extraction as Z3-proven rewrites.
"""

from __future__ import annotations

import collections
import os
import time

import z3
from egglog import EGraph, Expr, function, i64, rewrite, rule, ruleset, set_, union
from egglog import eq as egg_eq

from . import eqlift as E
from . import frameprog

ROOT_EXTRACT = os.environ.get("DI_EQLIFT_ROOT_EXTRACT", "") == "1"  # stage 3a flag

_WIDTHS = (1, 2)  # the widths the memory axioms and the interval rules are stated at
_TOP = (0, 0xFFFF)  # an address interval that says only "somewhere in the address space"
_ITERS = 30
BUDGET_S = float(os.environ.get("DI_EQLIFT_BUDGET_S", "5"))  # per-e-graph seconds
BUDGET_MB = float(os.environ.get("DI_EQLIFT_BUDGET_MB", "128"))  # resident growth per e-graph
EMIT_S = float(os.environ.get("DI_EQLIFT_EMIT_S", "60"))  # saturation across one artifact
_PAGE_MB = os.sysconf("SC_PAGE_SIZE") / (1 << 20)


def _rss_mb():
    with open("/proc/self/statm", encoding="ascii") as f:
        return int(f.read().split()[1]) * _PAGE_MB


def saturate(eg, rs, iters=_ITERS, budget=None):
    """Run ``rs`` a round at a time, to a fixpoint or to the resource bound.

    Adoption §5's mandatory bounded schedule, sound at any cutoff because every
    admitted rule is an equivalence: an e-graph grows super-linearly just before it
    blows up, so a round is refused when the last one's growth outruns the budget."""
    end = time.monotonic() + (BUDGET_S if budget is None else budget)
    base, last = _rss_mb(), 0.0
    for i in range(iters):
        t0 = time.monotonic()
        if not eg.run(rs).updated:
            return i + 1
        took = time.monotonic() - t0
        nxt = took * max(1.0, took / last) if last else took
        if time.monotonic() + nxt >= end or _rss_mb() - base >= BUDGET_MB:
            return i + 1
        last = took
    return iters


class Mem(Expr):
    """Memory (array) sort: a chain of stores over an opaque initial memory."""


@function
def mem0() -> Mem: ...


@function
def memk(n: i64) -> Mem: ...


@function
def store(m: Mem, a: E.T, v: E.T, w: i64) -> Mem: ...


@function
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

    ``add``/``shl`` carry the interval only where the result cannot wrap its own
    width: a wrapped sum is smaller than either operand, so an unguarded ``lo``
    would claim a floor the value drops below."""
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


def _lattice(e):
    """The interval ``mem_rules`` derives for ``e`` itself, or None where it states none.

    Mirrors those rules exactly, wrap guard included: the bridge seeds a fact only
    where the lattice has none, so no seeded bound can be widened by a derived one."""
    if e[0] == "const":
        v = e[1] & E._mask(e[2])
        return v, v
    if e[0] != "op":
        return None
    mn, kids, w = e[1], e[2], e[3]
    if mn == "INT_ZEXT":
        return 0, 255
    if mn == "INT_AND" and len(kids) == 2 and kids[1][0] == "const":
        return 0, kids[1][1] & E._mask(w)
    if mn == "INT_ADD" and len(kids) == 2:
        got = [_lattice(k) for k in kids]
        if all(got) and got[0][1] + got[1][1] <= E._mask(w):
            return got[0][0] + got[1][0], got[0][1] + got[1][1]
    if mn == "INT_LEFT" and len(kids) == 2 and kids[1][0] == "const":
        got, n = _lattice(kids[0]), kids[1][1]
        if got and (got[1] << n) <= E._mask(w):
            return got[0] << n, got[1] << n
    return None


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
    "INT_CARRY": E.carry,
}
_CMP = frozenset(("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"))


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
        if mn == "INT_ZEXT":
            return E.zext(conv(kids[0]))
        fn = _OP[mn]
        if mn in _CMP:
            return fn(conv(kids[0]), conv(kids[1]))
        r = conv(kids[0])
        for kid in kids[1:]:
            r = fn(r, conv(kid), w)
        return r

    avail = set()
    for s in stmts:
        if s[0] == "asg":
            rhs = conv(s[2])
            ver[0] += 1
            name = "%s.%d" % (s[1], ver[0])
            leaf = E.loc(name)
            defs.append((leaf, rhs))
            sk.append(("asg", s[1], rhs, ("loc", name), set(avail)))
            env[s[1]] = leaf
            avail.add(name)
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
    """True if every named-local leaf of ``ir`` is a block input or already defined."""
    if not isinstance(ir, tuple):
        return True
    if ir[0] == "loc":
        if ir[1].endswith(".0") and ir[1].split(".")[0] in E.frameproc._ALL_REG_LOCALS:
            return True
        return ir[1] in avail
    return all(_defined_at(a, avail) for a in ir[1:] if isinstance(a, tuple))


_WILD = frozenset(("call", "dcall", "dbr", "dgoto", "igoto", "label", "swc", "swg", "opsw"))


def _mem_writes(stmts):
    """(const (addr, addr_width, val_width) written, wild) footprint of a statement list."""
    addrs, wild = set(), [False]

    def rec(sl):
        for s in sl:
            if s[0] == "st":
                a = s[1]
                if a[0] == "const":
                    addrs.add((a[1] & E._mask(a[2]), a[2], _ew(s[2])))
                else:
                    wild[0] = True
            elif s[0] in _WILD:
                wild[0] = True
            for b in E.frameproc._stmt_bodies(s):
                rec(b)

    rec(stmts)
    return addrs, wild[0]


def _join_mem(pre_mem, stmts, fresh):
    """Memory after ``stmts`` may or may not have run: ``pre_mem`` with only the
    cells they can write rebound to a fresh opaque memory. Any unresolvable store
    address or wild statement kind havocs everything."""
    addrs, wild = _mem_writes(stmts)
    if wild:
        return memk(i64(fresh()))
    base = memk(i64(fresh()))  # opaque post-branch memory; written cells read from it
    m = pre_mem
    for a, aw, w in sorted(addrs):
        addr = E.num(a, aw)
        m = store(m, addr, sel(base, addr, w), w)
    return m


def _reg_bases(ir, out):
    if isinstance(ir, tuple):
        if ir[0] == "loc":
            base = ir[1].rpartition(".")[0]
            if base in E.frameproc._ALL_REG_LOCALS:
                out.add(base)
        else:
            for a in ir[1:]:
                _reg_bases(a, out)
    return out


def _child_bodies(nd):
    if nd[0] == "if":
        return (nd[2], nd[3])
    if nd[0] in ("loop", "callb"):
        return (nd[1 if nd[0] == "loop" else 3],)
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


def _share_once(tree, dead, chosen, terms):
    """``render_roots``' sharing rule over the render tree: a non-register subterm
    stays named only where more than one kept statement reads it; a name read once
    is inlined and its def drops. Site-validity is required, so no stale version."""
    reg = E.frameproc._ALL_REG_LOCALS
    by_name = {nd[3]: nd for nd in _all_nodes(tree) if nd[0] == "asg"}
    changed = True
    while changed:
        changed = False
        count, site = {}, {}

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
                for b in _child_bodies(nd):
                    scan(b)

        scan(tree)
        for name, nd in list(by_name.items()):
            if id(nd) in dead or nd[1] in reg or count.get(name) != 1:
                continue
            ti = site[name]
            defval = chosen[nd[2]]
            if _has_mem(defval) or not _defined_at(defval, terms[ti][1]):
                continue  # a cell/load value could be stale at the use -- keep it named
            chosen[ti] = _subst_loc(chosen[ti], name, defval)
            dead.add(id(nd))
            changed = True


def _temp_sweep(tree, dead, chosen):
    """Drop non-register asgs whose versioned local no consumer reads (all-local
    DCE by SSA name); iterate, since a drop can free another. Mutates ``dead``."""
    reg = E.frameproc._ALL_REG_LOCALS
    asgs = []

    def gather(nodes):
        for nd in nodes:
            if nd[0] == "asg":
                asgs.append(nd)
            for b in _child_bodies(nd):
                gather(b)

    gather(tree)
    while True:
        used = set()

        def collect(nodes):
            for nd in nodes:
                if nd[0] == "asg" and id(nd) in dead:
                    continue
                for ti in _node_terms(nd):
                    _loc_names(chosen[ti], used)
                for b in _child_bodies(nd):
                    collect(b)

        collect(tree)
        progress = False
        for nd in asgs:
            if nd[1] not in reg and id(nd) not in dead and nd[3] not in used:
                dead.add(id(nd))
                progress = True
        if not progress:
            return


def _liveness(tree, info, entry, chosen):
    """Backward register liveness over the render tree: id(node) -> live-out set.

    The interprocedural ``_Info`` supplies call/goto/dynamic/return live-out, so
    this is pass 2's boundary summary in the form root extraction reads."""
    reg = E.frameproc._ALL_REG_LOCALS
    labmap, liveout, brk, cont = {}, {}, [], []

    def uses(i):
        return _reg_bases(chosen[i], set())

    def node(nd, live):
        k = nd[0]
        if k == "asg":
            if nd[1] in reg:
                if nd[1] not in live:
                    return live
                live = set(live)
                live.discard(nd[1])
            return live | uses(nd[2])
        if k == "st":
            return live | uses(nd[2]) | (uses(nd[3]) if nd[3] is not None else set())
        if k == "if":
            return seq(nd[2], set(live)) | seq(nd[3], set(live)) | uses(nd[1])
        if k == "loop":
            head = set()
            for _i in range(24):
                h = loop(nd[1], live, head)
                if h <= head:
                    break
                head |= h
            loop(nd[1], live, head)
            return head
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
        if k == "callb":
            return seq(nd[3], set(live))
        if k == "ret":
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
                out |= seq(arm, set())
                brk.pop()
            return out | set(info.G)
        if k in ("dcall", "dgoto"):
            return set(info.G) | uses(nd[1])
        if k == "dbr":
            return set(info.G) | uses(nd[2]) | uses(nd[3])
        if k == "igoto":
            return set(info.G) | (uses(nd[2]) if nd[2] is not None else set())
        return set(info.G)

    def seq(nodes, live):
        for nd in reversed(nodes):
            liveout[id(nd)] = set(live)
            live = node(nd, live)
        return live

    def loop(body, brk_live, head):
        brk.append(set(brk_live))
        cont.append(set(head))
        out = seq(body, set(head))
        brk.pop()
        cont.pop()
        return out

    for _i in range(24):
        before = {p: set(v) for p, v in labmap.items()}
        liveout.clear()
        seq(tree, set(info.ret_live(entry)))
        if all(labmap.get(p) == before.get(p) for p in labmap):
            break
    return liveout


def _dce(tree, info, entry, chosen):
    """ids of dead register-local asgs (transitional; root extraction subsumes it)."""
    reg = E.frameproc._ALL_REG_LOCALS
    liveout = _liveness(tree, info, entry, chosen)
    return {
        id(nd)
        for nd in _all_nodes(tree)
        if nd[0] == "asg" and nd[1] in reg and nd[1] not in liveout.get(id(nd), reg)
    }


def _all_nodes(nodes):
    """Every render-tree node, outermost first."""
    for nd in nodes:
        yield nd
        for b in _child_bodies(nd):
            yield from _all_nodes(b)


Roots = collections.namedtuple("Roots", "ids sid regs")


def roots(tree, info, entry, chosen, dead_stores=()):
    """The observable roots of one rendered procedure (adoption §2), as node ids.

    Sinks are the surviving memory stores (``sid`` names the write-only
    $D400-$D41C ones) and every control statement; the register locals rooted are
    those pass 2's boundary summary says a consumer reads. Nothing else is a root."""
    reg = E.frameproc._ALL_REG_LOCALS
    live = _liveness(tree, info, entry, chosen)
    ids, sid = set(), set()
    for nd in _all_nodes(tree):
        k = nd[0]
        if k == "asg":
            if nd[1] in reg and nd[1] in live.get(id(nd), reg):
                ids.add(id(nd))
        elif k == "st":
            if id(nd) in dead_stores:
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
    root_extract=None,
    proofs=None,
    budget=None,
    stats=None,
):
    """Render a whole procedure (asg/st/if/loop) via the unified graph + printer.

    Memory forwards intra-block; at a join only the addresses written under the branch
    are havoced. ``budget`` is the procedure's share of the artifact's emit seconds, spent
    by saturation (capped at ``BUDGET_S``) then extraction; sites past it render own-term."""
    root_extract = ROOT_EXTRACT if root_extract is None else root_extract
    deadline = None if budget is None else time.monotonic() + budget
    stt = {"env": {}, "mem": mem0(), "k": 0}
    defs, terms, avail, locw, mempairs = [], [], set(), {}, []
    src, seeds = {}, []
    dfs = _Defs(src)

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

    def join_mem(pre_mem, s):
        return _join_mem(pre_mem, [s], fresh)

    def conv(e):
        k = e[0]
        if k == "const":
            return E.num(e[1] & E._mask(e[2]), e[2])
        if k == "loc":
            return stt["env"].get(e[1], E.loc(e[1] + ".0"))
        if k == "mem":
            return sel(stt["mem"], seed(conv(e[1]), e[1]), e[2])
        mn, kids, w = e[1], e[2], e[3]
        if mn == "INT_ZEXT":
            return E.zext(conv(kids[0]))
        fn = _OP[mn]
        if mn in _CMP:
            return fn(conv(kids[0]), conv(kids[1]))
        r = conv(kids[0])
        for kid in kids[1:]:
            r = fn(r, conv(kid), w)
        return r

    def add(t, own=None):
        terms.append((t, set(avail), own))
        return len(terms) - 1

    def havoc(names):
        for n in names:  # havoc names have no rendered def: never available to spell
            stt["env"][n] = E.loc("%s.%d" % (n, fresh()))
            src.pop(n, None)

    def havoc_all():
        havoc(list(stt["env"]))
        stt["mem"] = memk(i64(fresh()))

    def walk(sl):
        nodes = []
        for s in sl:
            k = s[0]
            if k == "asg":
                rhs = conv(s[2])
                name = "%s.%d" % (s[1], fresh())
                defs.append((name, E.loc(name), rhs))
                locw[s[1]] = locw.get(s[2][1], 1) if s[2][0] == "loc" else _ew(s[2])
                nodes.append(("asg", s[1], add(rhs, ("loc", name)), name))
                stt["env"][s[1]] = E.loc(name)
                src[s[1]] = s[2]
                avail.add(name)
            elif k == "st":
                v = conv(s[2])
                a = s[1]
                addr = E.num(a[1] & E._mask(a[2]), a[2]) if a[0] == "const" else seed(conv(a), a)
                pre = stt["mem"]
                stt["mem"] = store(pre, addr, v, _ew(s[2]))
                ai = None if a[0] == "const" else add(addr)
                own = ("cell", a[1] & E._mask(a[2]), _ew(s[2]), 0) if a[0] == "const" else None
                nd = ("st", a, add(v, own), ai, _ew(s[2]))
                nodes.append(nd)
                if root_extract:
                    mempairs.append((nd, pre, stt["mem"]))
            elif k == "if":
                cond = conv(s[2])
                if s[1] == "ifnot":
                    cond = E.bnot(cond)
                ci = add(cond)
                pre_env, pre_mem, pre_av = dict(stt["env"]), stt["mem"], set(avail)
                pre_src = dict(src)
                then = walk(s[3])
                then_env = dict(stt["env"])
                stt["env"], stt["mem"] = dict(pre_env), pre_mem
                src.clear()
                src.update(pre_src)
                avail.intersection_update(pre_av)
                els = walk(s[4])
                els_env = dict(stt["env"])
                stt["env"], stt["mem"] = dict(pre_env), join_mem(pre_mem, s)
                src.clear()
                src.update(pre_src)
                avail.intersection_update(pre_av)
                for n in set(pre_env) | set(then_env) | set(els_env):
                    c = pre_env.get(n)
                    if not (then_env.get(n) is c and els_env.get(n) is c):
                        stt["env"][n] = E.loc("%s.%d" % (n, fresh()))
                        src.pop(n, None)
                nodes.append(("if", ci, then, els))
            elif k == "loop":
                pre_mem, pre_av = stt["mem"], set(avail)
                havoc(_written(s[1]))
                stt["mem"] = join_mem(pre_mem, s)
                body = walk(s[1])
                avail.intersection_update(pre_av)
                havoc(_written(s[1]))
                stt["mem"] = join_mem(pre_mem, s)
                nodes.append(("loop", body))
            elif k == "label":
                avail.clear()
                havoc_all()
                nodes.append(("label", s[1]))
            elif k in ("goto", "cont", "brk", "ret", "unobs"):
                nodes.append((k, s[1] if len(s) > 1 else None))
            elif k == "call":
                havoc_all()
                nodes.append(("call", s[1], s[2]))
            elif k == "callb":
                body = walk(s[3])
                havoc_all()
                nodes.append(("callb", s[1], s[2], body))
            elif k == "dcall":
                ix = add(conv(s[1]))
                havoc_all()
                nodes.append(("dcall", ix, s[2]))
            elif k in ("swc", "opsw", "swg"):
                cases = s[1] if k == "swg" else s[2]
                pre_env, pre_mem, pre_av = dict(stt["env"]), stt["mem"], set(avail)
                pre_src, arms = dict(src), []
                for lbl, body in cases:
                    stt["env"], stt["mem"] = dict(pre_env), pre_mem
                    src.clear()
                    src.update(pre_src)
                    avail.intersection_update(pre_av)
                    arms.append((lbl, walk(body)))
                avail.intersection_update(pre_av)
                havoc_all()
                nodes.append(("switch", arms))
            elif k == "dbr":
                pair = (add(conv(s[2])), add(conv(s[3])))
                havoc_all()
                nodes.append(("dbr", s[1], pair[0], pair[1], s[4]))
            elif k == "dgoto":
                ix = add(conv(s[1]))
                havoc_all()
                nodes.append(("dgoto", ix))
            elif k == "igoto":
                ix = add(conv(s[2])) if s[2] is not None else None
                havoc_all()
                nodes.append(("igoto", s[1], ix))
        return nodes

    tree = walk(stmts)
    rs, _names = unified_rules()
    eg = EGraph()
    for _n, leaf, rhs in defs:
        eg.register(union(leaf).with_(rhs))
    for t, l, h in seeds:  # the interval bridge: bit analyses read as e-class bounds
        eg.register(set_(lo(t)).to(i64(l)), set_(hi(t)).to(i64(h)))
    handles = [eg.let("h%d" % i, t) for i, (t, _av, _o) in enumerate(terms)]
    memh = [
        (nd, eg.let("mp%d" % i, p), eg.let("mq%d" % i, q)) for i, (nd, p, q) in enumerate(mempairs)
    ]
    saturate(eg, rs, budget=None if budget is None else min(BUDGET_S, budget))
    pr = E._Printer(aliases or {})

    def own_ir(i):
        """The site's own term: position-correct by construction, and what a spent
        extraction budget falls back to -- extraction is sound at any cutoff."""
        raw = E._parse_ir(str(terms[i][0]))
        return _to_ir(raw), raw

    def pick_ir(i):
        _t, av, own = terms[i]
        cands = [
            (_to_ir(r), r)
            for r in (E._parse_ir(str(x)) for x in eg.extract_multiple(handles[i], 12))
        ]
        own_name = own[1] if own is not None and own[0] == "loc" else None

        def ok(c):
            got = []
            _count_locs(c, got)
            return c != own and (own_name is None or own_name not in got)

        kept = [c for c in cands if ok(c[0]) and not _has_mem(c[0]) and _defined_at(c[0], av)]
        if not kept:
            raw = own_ir(i)
            if ok(raw[0]):
                kept = [raw]
        return min(kept or cands, key=lambda c: (E._cost(c[0]), repr(c[0])))

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
    if root_extract:
        gone = {id(nd) for nd, p, q in memh if eg.check_bool(egg_eq(p).to(q))}
        keep = _root_keep(tree, roots(tree, info, entry, chosen, gone).ids, chosen)
        dead = {id(nd) for nd in _all_nodes(tree)} - keep
        _share_once(tree, dead, chosen, terms)
    else:
        dead = _dce(tree, info, entry, chosen)
        _share_once(tree, dead, chosen, terms)
        _temp_sweep(tree, dead, chosen)
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
        proofs.setdefault("locw", {}).update(locw)

    def pick(i):
        return pr.fmt(chosen[i])

    def render(nodes, d):
        for nd in nodes:
            if nd[0] == "asg":
                if id(nd) in dead:
                    continue
                pr.line("%s = %s" % (nd[1], pick(nd[2])), d)
            elif nd[0] == "st":
                if id(nd) in dead:
                    continue
                a = nd[1]
                if a[0] == "const":
                    dest = pr.name(a[1]) + E.sidprog._wsuf(nd[4])
                else:
                    dest = pr._loadref(("load", chosen[nd[3]], nd[4], 0))
                pr.line("%s = %s" % (dest, pick(nd[2])), d)
            elif nd[0] == "if":
                pr.line("if %s {" % pick(nd[1]), d)
                render(nd[2], d + 1)
                if nd[3]:
                    pr.line("} else {", d)
                    render(nd[3], d + 1)
                pr.line("}", d)
            elif nd[0] == "loop":
                pr.line("loop {", d)
                render(nd[1], d + 1)
                pr.line("}", d)
            elif nd[0] == "label":
                pr.line("$%04X:" % nd[1], d)
            elif nd[0] == "goto":
                pr.line("goto $%04X" % nd[1], d)
            elif nd[0] in ("ret", "cont", "brk"):
                pr.line({"ret": "ret", "cont": "continue", "brk": "break"}[nd[0]], d)
            elif nd[0] == "unobs":
                pr.line("unobserved $%04X" % nd[1], d)
            elif nd[0] == "call":
                pr.line("call $%04X ret $%04X" % (nd[1], nd[2]), d)
            elif nd[0] == "callb":
                pr.line("call $%04X ret $%04X {" % (nd[1], nd[2]), d)
                render(nd[3], d + 1)
                pr.line("}", d)
            elif nd[0] == "dcall":
                pr.line("call (%s) ret $%04X" % (pick(nd[1]), nd[2]), d)
            elif nd[0] == "switch":
                pr.line("switch {", d)
                for lbl, arm in nd[1]:
                    pr.line("case %s: {" % lbl, d + 1)
                    render(arm, d + 2)
                    pr.line("}", d + 1)
                pr.line("}", d)
            elif nd[0] == "dbr":
                pr.line("%s %s goto (%s) else $%04X" % (nd[1], pick(nd[2]), pick(nd[3]), nd[4]), d)
            elif nd[0] == "dgoto":
                pr.line("goto (%s)" % pick(nd[1]), d)
            elif nd[0] == "igoto":
                ptr = "(%s)" % pick(nd[2]) if nd[2] is not None else "$%04X" % nd[1]
                pr.line("igoto %s" % ptr, d)

    render(tree, 0)
    return pr.out


_CMPS = frozenset(("eq", "ne", "ult", "ule", "slt", "sge"))


class _Z3Env:
    """Z3 reading of an extracted term: values are BV16 masked to their own width,
    memory an array BV16 -> BV8 whose ``mem0``/``memk`` leaves are opaque."""

    def __init__(self, locw=None):
        self.locw = locw or {}
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
            return self._arr("mk%d" % ir[1])
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
    defs, env = sites.get("defs", {}), _Z3Env(sites.get("locw"))
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
    """Byte width of a pass-1 expression value (locals default to 1)."""
    k = e[0]
    if k in ("const", "mem"):
        return e[2]
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
        if mn == "INT_ZEXT":
            return E.zext(self._conv(kids[0]))
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
    and everything reset at labels, calls and dynamic control. Memory joins through
    ``_join_mem``, so cells no branch can write keep forwarding. Records site terms."""

    def __init__(self):
        super().__init__()
        self.sites = []
        self._k = 0

    def _fresh(self):
        self._k += 1
        return self._k

    def _havoc_locs(self, names):
        for n in names:
            self.env[n] = E.loc("%s@%d" % (n, self._fresh()))

    def _havoc_all(self):
        self._havoc_locs(list(self.env))
        self.mem = memk(i64(self._fresh()))

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
            v = self._conv(s[2])
            self.mem = store(self.mem, self._addr(s[1]), v, _ew(s[2]))
            self.sites.append(("st", s[1], v))
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
            self._havoc_all()
        elif k in ("swc", "opsw"):
            self._branch([b for _l, b in s[2]], s)
        elif k == "swg":
            self._branch([b for _l, b in s[1]], s)
        elif k not in _NOEFFECT:
            for i in _READS.get(k, ()):
                if s[i] is not None:
                    self.sites.append((k, self._conv(s[i])))
            self._havoc_all()

    def _branch(self, bodies, stmt):
        pre_env, pre_mem = dict(self.env), self.mem
        ends = []
        for b in bodies:
            self.env, self.mem = dict(pre_env), pre_mem
            self.run(b)
            ends.append((self.env, self.mem))
        self.env = dict(pre_env)
        self.mem = _join_mem(pre_mem, [stmt], self._fresh)
        names = set(pre_env).union(*(e for e, _m in ends))
        for n in names:
            terms = [e.get(n) for e, _m in ends] + [pre_env.get(n)]
            if any(t is not terms[0] for t in terms):
                self.env[n] = E.loc("%s@%d" % (n, self._fresh()))

    def _loop(self, stmt):
        body, pre_mem = stmt[1], self.mem
        w = _written(body)
        self._havoc_locs(w)
        self.mem = _join_mem(pre_mem, [stmt], self._fresh)
        self.run(body)
        self._havoc_locs(w)
        self.mem = _join_mem(pre_mem, [stmt], self._fresh)


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


def emit_mem(model, root_extract=None, proofs=None, stats=None):
    """Whole-artifact text with procedure bodies rendered via ``render_proc``.

    ``proofs`` is a list one §6 site record per procedure is appended to -- SSA names
    are per procedure, so one merged record would prove the wrong equalities. ``EMIT_S``
    is divided over the procedures still to render, so slack from one funds the next."""
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
    state, inputs = frameprog._state_lines(view, decls, model.dispatch_sets)
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
    for i, (entry, stmts) in enumerate(procs):
        proc_lines.append("sub_%04X {" % entry)
        rec = None if proofs is None else {}
        share = max(0.0, end - time.monotonic()) / (len(procs) - i)
        body = render_proc(stmts, aliases, entry, info, root_extract, rec, share, stats)
        if rec is not None:
            proofs.append(rec)
        proc_lines.extend(" " + ln for ln in body)
        proc_lines.append("}")
    from . import eqlift_annotate  # pylint: disable=import-outside-toplevel

    tr = eqlift_annotate.aggregate([s for _e, s in procs], model)
    header_body = eqlift_annotate.annotate_lines(header_body, tr, model, aliases)
    lines = head + header_body + E.sidprog._symbol_lines(aliases) + proc_lines
    return "\n".join(lines) + "\n"


def emit(model, root_extract=None, proofs=None, stats=None):
    """Whole-artifact eqlift text via the unified value+memory e-graph lifter.

    Thin wrapper over ``emit_mem``; returns ``(text, None)`` so callers that still
    unpack a second value keep working. Soundness is the rule/axiom admission gate
    (``verify_rules`` + ``verify_axioms``) run once inside ``unified_rules``."""
    return emit_mem(model, root_extract, proofs, stats), None
