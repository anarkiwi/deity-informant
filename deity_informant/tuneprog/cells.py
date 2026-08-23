"""S6 -- a storage cell that holds one value: mirrors, stack slots, its own update.

Three readings of one cell over the presentation view: two cells that provably hold
the same byte, a slot stored once and read where that store reaches, and the value a
read-modify-write leaves in the cell for the statements after it.
"""

from __future__ import annotations

from .frame import fresh
from .graph import idoms
from .ir import Assert, Call, Const, If, Let, Load, Return, STACK_HI, STACK_LO
from .ir import Store, Switch, Var, W16
from .irwalk import (
    apply_stmt,
    apply_term,
    node_loads,
    pure,
    reads_region,
    single_defs,
    sub_expr,
    walk,
)


def loads_of(node):
    """Every load of a real region one statement or terminator reads."""
    return [x for x in node_loads(node) if x.r >= 0]


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


def stack_temps(prog, make=None):
    """A cell stored once and read where that store reaches becomes a value.

    Regions are disjoint and this is the only writer, so every read the store
    reaches sees that value: inside one block by order, and for a stack slot
    (a PHA and the PLA a branch away) by dominance over one address expression.
    """
    st, ld = accesses(prog)
    ctx = _Slots(prog)
    make = make or fresh(prog)
    out = 0
    for rid, stores in sorted(st.items()):
        reads = ld.get(rid, [])
        if len(stores) != 1 or not reads or any(l[0] != stores[0][0] for l in reads):
            continue
        pname, lbl, si = stores[0]
        proc = prog.procs[pname]
        blk = proc.blocks[lbl]
        if not all(ctx.forwards(proc, rid, (lbl, si), l[1], l[2]) for l in reads):
            continue
        name = make()
        blk.stmts[si] = Let(name, blk.stmts[si].v)
        fn = _reader(rid, name)
        for b in proc.blocks.values():
            for s in b.stmts:
                apply_stmt(s, fn)
            apply_term(b.term, fn)
        prog.storage = [x for x in prog.storage if x.id != rid]
        out += 1
    return out


class _Slots:
    """Decides whether one store reaches one read of the same cell."""

    def __init__(self, prog):
        self.size = {r.id: r.size for r in prog.storage}
        self.frame = {r.id for r in prog.storage if STACK_LO <= r.base <= STACK_HI}
        self.wr = written(prog)
        self.doms = {}

    def forwards(self, proc, rid, store, llbl, li):
        lbl, si = store
        blk = proc.blocks[lbl]
        if lbl == llbl:
            calls = [s for s in blk.stmts[si:li] if type(s) is Call]
            if si >= li or any(rid in self.wr[s.proc] for s in calls):
                return False
        elif rid not in self.frame or not self._reaches(proc, lbl, llbl):
            return False
        if self.size.get(rid) == 1:
            return True
        return rid in self.frame and _same_addr(blk.stmts[si], proc.blocks[llbl], li, rid)

    def _reaches(self, proc, lbl, llbl):
        """True when block ``lbl`` lies on every path from the entry to ``llbl``."""
        if id(proc) not in self.doms:
            self.doms[id(proc)] = idoms(proc)
        idom, cur = self.doms[id(proc)], llbl
        while idom.get(cur, cur) != cur:
            cur = idom[cur]
            if cur == lbl:
                return True
        return False


def _same_addr(store, blk, li, rid):
    """True when the one load of the region reads the address the store wrote."""
    node = blk.stmts[li] if li < len(blk.stmts) else blk.term
    hit = [x for x in node_loads(node) if x.r == rid]
    return bool(hit) and all(x.a == store.a and pure(x.a) for x in hit)


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


# ---- what a read-modify-write leaves in its cell -----------------------------
def forward(prog):
    """The value a cell's own update stores is that cell: a later read of it is a load.

    Only an update -- a store whose value reads the cell it writes -- holds, so the
    substitution never introduces a dependency between two cells that the sibling
    fold reads as correspondence. It is what lets liveness see the pre-value
    temporary of a ``DEC``/``INC`` die.
    """
    n = 0
    for p in prog.procs.values():
        defs = single_defs(p)
        for b in p.blocks.values():
            n += _forward(b, defs)
    return n


def _forward(b, defs):
    """Forward within one block: the printer forgets a cell at every block head too."""
    held, n = {}, 0

    def fn(e):
        return held.get(e, e)

    for s in b.stmts:
        n += _rewrite(s, fn)
        _invalidate(held, s)
        if _holdable(s) and _updates(s, defs, s.v):
            held.setdefault(s.v, Load(s.cls, s.a, s.w, s.lo, s.hi, s.r))
    n += _rewrite(b.term, fn)
    return n


def _updates(s, defs, e, seen=()):
    """True when the value a store writes reads the very cell it writes."""
    for x in walk(e):
        if type(x) is Load and x.r == s.r and x.a == s.a:
            return True
        if type(x) is Var and x.n in defs and x.n not in seen:
            if _updates(s, defs, defs[x.n], seen + (x.n,)):
                return True
    return False


def _rewrite(node, fn):
    """Substitute in what a node reads for its value; an address is a place, not a value."""
    t, before = type(node), repr(node)
    if t is Store:
        node.v = sub_expr(node.v, fn)
    elif t is W16:
        node.e = sub_expr(node.e, fn)
    elif t in (If, Switch, Return):
        apply_term(node, fn)
    elif t in (Let, Assert, Call):
        apply_stmt(node, fn)
    return repr(node) != before


def _holdable(s):
    """True when a store makes a cell that later reads of its value can name."""
    return (
        type(s) is Store
        and s.cls not in ("io", "raw")
        and s.r >= 0
        and s.w == 1
        and type(s.v) is not Const
        and pure(s.a)
        and pure(s.v)
    )


def _invalidate(held, s):
    """Drop what one statement takes back: everything for a call, the region for a store."""
    if type(s) is Call or (type(s) is Store and (s.r < 0 or s.cls == "raw")):
        held.clear()
    elif type(s) is Store and s.cls != "io":
        for k, v in list(held.items()):
            if v.r == s.r or reads_region(k, (s.r,)) or reads_region(v.a, (s.r,)):
                del held[k]
