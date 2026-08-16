"""S6 -- machine texture removal over the presentation copy of the S4 IR.

Each pass rewrites the *view* only and is justified structurally: empty-block
threading, short-circuit conditions, stack slots as values, mirror cells, switch
merging and 16-bit carry chains (see each function).
"""

from __future__ import annotations

from .idioms import CMP, _neg, fold
from .ir import Bin, Call, Const, Goto, If, Let, Load, Return, Store, Switch, Var, succs
from .ssa import apply_stmt, apply_term, merge_chains, preds_of, prune, sub_expr, use_counts

STACK = (0x0100, 0x01FF)


def _exprs(node):
    t = type(node)
    if t is Let:
        return (node.e,)
    if t is Store:
        return (node.a, node.v)
    if t is Call:
        return node.args
    if t is If:
        return (node.c,)
    if t is Switch:
        return (node.e,)
    if t is Return:
        return node.vals
    return (node.e,) if hasattr(node, "e") else ()


def walk(e):
    """Every sub-expression of ``e``, itself first."""
    yield e
    t = type(e)
    if t is Bin:
        yield from walk(e.a)
        yield from walk(e.b)
    elif t is Load:
        yield from walk(e.a)


def loads_of(node):
    return [x for e in _exprs(node) for x in walk(e) if type(x) is Load and x.r >= 0]


# ---- empty-block threading ---------------------------------------------------
def thread_empty(proc):
    """Retarget every edge to a statement-free ``goto`` block at its destination."""
    changed, n = True, 0
    while changed:
        changed = False
        for lbl, b in list(proc.blocks.items()):
            if lbl == proc.entry or b.stmts or type(b.term) is not Goto or b.term.to == lbl:
                continue
            for other in proc.blocks.values():
                if lbl in succs(other.term):
                    other.term = _retarget(other.term, lbl, b.term.to)
            del proc.blocks[lbl]
            changed, n = True, n + 1
    prune(proc)
    return n


def _retarget(term, old, new):
    k = type(term)
    if k is Goto:
        return Goto(new)
    if k is If:
        return If(term.c, new if term.t == old else term.t, new if term.f == old else term.f)
    return Switch(
        term.e,
        tuple((v, new if l == old else l) for v, l in term.cases),
        new if term.default == old else term.default,
    )


# ---- short-circuit conditions ------------------------------------------------
def shortcircuit(proc, rounds=32):
    """``if a: X else: if b: X`` becomes ``if a or b: X`` (``and`` symmetrically).

    The merged test evaluates the second condition exactly where the CFG reached
    it, so the rewrite is the definition of the short-circuit operators.
    """
    n = 0
    for _ in range(rounds):
        preds, uses = preds_of(proc), use_counts(proc)
        hit = next(
            (
                h
                for lbl, b in proc.blocks.items()
                if type(b.term) is If
                for h in (_sc_pair(proc, lbl, b, preds, uses),)
                if h is not None
            ),
            None,
        )
        if hit is None:
            return n
        lbl, term, gone = hit
        proc.blocks[lbl].term = term
        del proc.blocks[gone]
        prune(proc)
        n += 1
    return n


def _sc_pair(proc, lbl, b, preds, uses):
    """``(label, merged terminator, dead block)`` when an arm is a shared test."""
    for arm, other, op in ((b.term.f, b.term.t, "or"), (b.term.t, b.term.f, "and")):
        c2 = _testonly(proc, arm, preds, uses) if arm != lbl and arm in proc.blocks else None
        if c2 is None:
            continue
        t2 = proc.blocks[arm].term
        for keep, drop, neg in ((t2.t, t2.f, False), (t2.f, t2.t, True)):
            if keep != other or drop in (arm, lbl):
                continue
            c = Bin(op, b.term.c, _not(c2) if neg else c2, 1)
            return lbl, (If(c, other, drop) if op == "or" else If(c, drop, other)), arm
    return None


def _testonly(proc, lbl, preds, uses):
    """The condition of a block that computes nothing but its own test."""
    b = proc.blocks[lbl]
    if type(b.term) is not If or len(preds[lbl]) != 1:
        return None
    vals = {}
    for s in b.stmts:
        if type(s) is not Let or not _movable(s.e) or uses[s.n] != 1:
            return None
        vals[s.n] = s.e
    return sub_expr(b.term.c, lambda e: vals.get(e.n, e) if type(e) is Var else e)


def _movable(e):
    """A value a condition may evaluate: pure, and never an input read."""
    t = type(e)
    if t is Load:
        return e.cls not in ("io", "chk") and _movable(e.a)
    return _movable(e.a) and _movable(e.b) if t is Bin else True


def _not(c):
    return _neg(c) if type(c) is Bin and c.op in CMP else Bin("==", c, Const(0), 1)


def zerocarry(prog):
    """``carry(x, 0)`` is zero: a byte value plus nothing never carries."""
    n = [0]

    def fn(e):
        if type(e) is Bin and e.op == "carry" and type(e.b) is Const and not e.b.v:
            n[0] += 1
            return Const(0, 1)
        return fold(e)

    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                apply_stmt(s, fn)
            apply_term(b.term, fn)
    return n[0]


# ---- single-definition values ------------------------------------------------
def propagate(proc, rounds=8):
    """Forward a name that has one definition in the procedure (copy/constant).

    Phi elimination leaves copies behind that S4's propagation, which runs before
    it, never sees; a single definition dominates every use, so forwarding it is
    the same rewrite one stage later.
    """
    n = 0
    for _ in range(rounds):
        defs = {}
        for b in proc.blocks.values():
            for s in b.stmts:
                for name in _defines(s):
                    defs.setdefault(name, []).append(s.e if type(s) is Let else None)
        one = {k: v[0] for k, v in defs.items() if len(v) == 1 and v[0] is not None}
        sub = {
            k: e
            for k, e in one.items()
            if type(e) is Const or (type(e) is Var and e.n in one and type(one[e.n]) is not Load)
        }
        if not sub:
            return n
        fn = _subber(sub)
        hit = 0
        for b in proc.blocks.values():
            for s in b.stmts:
                before = repr(s)
                apply_stmt(s, fn)
                hit += repr(s) != before
            apply_term(b.term, fn)
        n += hit
        if not hit:
            return n
    return n


def _subber(sub):
    def fn(e):
        return sub.get(e.n, e) if type(e) is Var else e

    return fn


def _defines(s):
    t = type(s)
    if t is Let:
        return (s.n,)
    return s.rets if t is Call else ()


# ---- pinned addresses --------------------------------------------------------
def pin(prog):
    """An access whose observed envelope is exactly its width addresses one cell.

    The certified program asserts ``lo <= a`` and ``a + w - 1 <= hi`` on every
    access, so a ``w``-wide envelope pins the address to ``lo`` -- which is what
    takes the stack pointer out of a push and a pop.
    """
    n = [0]

    def fn(e):
        if type(e) is Load and type(e.a) is not Const and _pinned(e):
            n[0] += 1
            return Load(e.cls, Const(e.lo, 2), e.w, e.lo, e.hi, e.r)
        return e

    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Store and type(s.a) is not Const and _pinned(s):
                    s.a = Const(s.lo, 2)
                    n[0] += 1
                apply_stmt(s, fn)
            apply_term(b.term, fn)
    return n[0]


def _pinned(x):
    """A stack access whose envelope is one slot: the frame pointer is not data."""
    return x.hi - x.lo + 1 == x.w and STACK[0] <= x.lo <= STACK[1]


# ---- stack slots as values ---------------------------------------------------
def accesses(prog):
    """``({region: [(proc, block, index)] stores}, {region: [...] loads})``."""
    st, ld = {}, {}
    for name, p in prog.procs.items():
        for lbl, b in p.blocks.items():
            for i, s in enumerate(b.stmts):
                if type(s) is Store and s.cls != "raw" and s.r >= 0:
                    st.setdefault(s.r, []).append((name, lbl, i))
                for x in loads_of(s):
                    ld.setdefault(x.r, []).append((name, lbl, i))
            for x in loads_of(b.term):
                ld.setdefault(x.r, []).append((name, lbl, len(b.stmts)))
    return st, ld


def written(prog):
    """``{proc: regions it or anything it calls stores to}``."""
    out = {n: set() for n in prog.procs}
    callees = {n: set() for n in prog.procs}
    for n, p in prog.procs.items():
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Store and s.r >= 0:
                    out[n].add(s.r)
                elif type(s) is Call:
                    callees[n].add(s.proc)
    for _ in range(len(prog.procs)):
        for n, cs in callees.items():
            out[n] |= {r for c in cs if c in out for r in out[c]}
    return out


def stack_temps(prog):
    """A one-byte cell stored once and loaded once in one block becomes a value.

    The store is the program's only writer of the region and regions are disjoint,
    so the load reads exactly that value and nothing else can observe the cell.
    """
    st, ld = accesses(prog)
    size = {r.id: r.size for r in prog.storage}
    wr = written(prog)
    out = 0
    for rid, stores in sorted(st.items()):
        loads = ld.get(rid, [])
        if len(stores) != 1 or len(loads) != 1 or stores[0][:2] != loads[0][:2]:
            continue
        (pname, lbl, si), li = stores[0], loads[0][2]
        blk = prog.procs[pname].blocks[lbl]
        calls = [s for s in blk.stmts[si:li] if type(s) is Call]
        if size.get(rid) != 1 or li <= si or any(rid in wr[s.proc] for s in calls):
            continue
        name = "$saved" if not out else "$saved%d" % (out + 1)
        blk.stmts[si] = Let(name, blk.stmts[si].v)
        fn = _reader(rid, name)
        for s in blk.stmts[si + 1 :]:
            apply_stmt(s, fn)
        apply_term(blk.term, fn)
        prog.storage = [x for x in prog.storage if x.id != rid]
        out += 1
    return out


def _reader(rid, name):
    def fn(e):
        return Var(name) if type(e) is Load and e.r == rid else e

    return fn


# ---- mirror cells ------------------------------------------------------------
def mirrors(prog):
    """``{region: representative}`` for cells that provably hold the same value.

    Equal initial bytes and every store paired with an equal-valued store to the
    other cell in the same block, with no read of either between them.
    """
    st, _ld = accesses(prog)
    rgn = {r.id: r for r in prog.storage if r.size == 1 and len(r.init) == 1}
    out = {}
    for a in sorted(rgn):
        for b in sorted(rgn):
            if b <= a or a in out or b in out or rgn[a].init != rgn[b].init:
                continue
            if _paired(prog, st.get(a, []), st.get(b, []), a, b):
                out[b] = a
    if out:
        fn = _mirror_fn(out, rgn)
        for p in prog.procs.values():
            for blk in p.blocks.values():
                for s in blk.stmts:
                    apply_stmt(s, fn)
                apply_term(blk.term, fn)
    return out


def _mirror_fn(out, rgn):
    def fn(e):
        if type(e) is Load and e.r in out and type(e.a) is Const:
            r = rgn[out[e.r]]
            return Load(e.cls, Const(r.base, 2), e.w, r.base, r.base, r.id)
        return e

    return fn


def _paired(prog, sa, sb, ra, rb):
    if not sa or len(sa) != len(sb):
        return False
    for (pa, la, ia), (pb, lb, ib) in zip(sa, sb):
        if (pa, la) != (pb, lb):
            return False
        stmts = prog.procs[pa].blocks[la].stmts
        if stmts[ia].v != stmts[ib].v:
            return False
        for s in stmts[min(ia, ib) + 1 : max(ia, ib)]:
            if any(x.r in (ra, rb) for x in loads_of(s)):
                return False
            if type(s) is Store and s.r in (ra, rb):
                return False
    return True


# ---- switch merging ----------------------------------------------------------
def merge_switches(proc):
    """Two switches on one value with a straight line between them become one.

    The line is copied into each arm, which is the case-split of a sequence when
    no arm and no copied statement writes the selector.
    """
    n = 0
    for lbl in list(proc.blocks):
        b = proc.blocks.get(lbl)
        if b is None or type(b.term) is not Switch:
            continue
        mid = _joint(proc, b.term)
        if mid is None or not _stable(proc, b.term, mid):
            continue
        pairs = dict(proc.blocks[mid].term.cases)
        for v, arm in b.term.cases:
            proc.blocks[arm].stmts.extend(_copy(s) for s in proc.blocks[mid].stmts)
            proc.blocks[arm].term = Goto(pairs[v])
        del proc.blocks[mid]
        prune(proc)
        merge_chains(proc)
        n += 1
    return n


def _joint(proc, term):
    """The switch block every arm of ``term`` flows straight into."""
    outs = set()
    for _v, arm in term.cases:
        b = proc.blocks.get(arm)
        if b is None or type(b.term) is not Goto:
            return None
        outs.add(b.term.to)
    if len(outs) != 1 or term.default:
        return None
    mid = outs.pop()
    m = proc.blocks.get(mid)
    if m is None or type(m.term) is not Switch or m.term.default:
        return None
    if {v for v, _ in term.cases} != {v for v, _ in m.term.cases}:
        return None
    return mid if set(preds_of(proc)[mid]) == {a for _v, a in term.cases} else None


def _stable(proc, term, mid):
    """The two selectors are the same value and nothing between them writes it."""
    if proc.blocks[mid].term.e != term.e:
        return False
    rs = {x.r for x in walk(term.e) if type(x) is Load}
    for name in [a for _v, a in term.cases] + [mid]:
        for s in proc.blocks[name].stmts:
            if type(s) is Call or (type(s) is Store and (s.r in rs or s.r < 0)):
                return False
    return True


def _copy(s):
    t = type(s)
    if t is Let:
        return Let(s.n, s.e)
    if t is Store:
        return Store(s.cls, s.a, s.v, s.w, s.lo, s.hi, s.r, s.src)
    return Call(s.proc, s.args, s.rets) if t is Call else s


def tidy(prog):
    """Drop the jump blocks the later passes emptied."""
    for p in prog.procs.values():
        thread_empty(p)
    return prog


def clean(prog):
    """Every texture pass over a presentation copy of ``prog``."""
    zerocarry(prog)
    pin(prog)
    mirrors(prog)
    stack_temps(prog)
    for p in prog.procs.values():
        propagate(p)
        thread_empty(p)
        merge_switches(p)
        shortcircuit(p)
        merge_chains(p)
        thread_empty(p)
    return prog
