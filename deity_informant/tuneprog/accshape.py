"""T1 -- the shapes an accumulator's update is made of: guards, arms, deltas.

A recurrence is not one statement: GoatTracker 2 stores its vibrato phase in
``p_109E`` from a value four arms of ``p_1082`` supply. :class:`Ctx` joins the
dominating guards and the call arguments; the rest is section 5's grammar.
"""

from __future__ import annotations

from collections import namedtuple

from .graph import EXIT, cfg, idoms, natural_loops, postdoms, preds_of
from .idioms import CMP, is_one
from .ir import Bin, Call, Const, If, Let, Load, R16, REGVAR, Store, Var, W16, succs
from .irwalk import addr_split, renamer, single_defs, sub_expr, walk
from .loops import _entry_value, repeats
from .provenance import stops

DEPTH = 3  # call frames a value is chased through before it is left as it stands
MAXARMS = 64
Arm = namedtuple("Arm", "guards value proc block")
Tgt = namedtuple("Tgt", "kind cells")
Site = namedtuple("Site", "proc block idx stmt")


def selfread(tgt):
    """The test for a value that reads the accumulator's own cell."""
    if tgt.kind == "pair":
        lo, hi = tgt.cells
        return lambda e: type(e) is R16 and (tuple(e.lo), tuple(e.hi)) == (lo, hi)
    key = tgt.cells[0]
    return lambda e: cellof(e) == key


def key_of(s):
    """The cell a store writes: one byte, or the two of a 16-bit assignment."""
    if type(s) is W16:
        return Tgt("pair", (tuple(s.lo), tuple(s.hi)))
    base = addr_split(s.a)[0]
    return None if base is None else Tgt("byte", ((s.r, base),))


def rank(prog, root="tick"):
    """``{(proc, block, index): position}`` in the order one tick first executes them."""
    out, n, done = {}, [0], set()

    def visit(name):
        p = prog.procs.get(name)
        if p is None or name in done:
            return
        done.add(name)
        for lbl in p.order():
            for i, s in enumerate(p.blocks[lbl].stmts):
                out[(name, lbl, i)] = n[0]
                n[0] += 1
                if type(s) is Call:
                    visit(s.proc)

    visit(root)
    for name in prog.procs:
        visit(name)
    return out


def sites(prog, facts, order):
    """``{target: [site]}`` for every store into a named region the tick reaches."""
    out = {}
    for name in sorted(facts.tick):
        p = prog.procs[name]
        for lbl, b in p.blocks.items():
            for i, s in enumerate(b.stmts):
                if type(s) is Store and (s.r < 0 or s.cls == "io"):
                    continue
                if type(s) is not Store and type(s) is not W16:
                    continue
                k = key_of(s)
                if k is not None:
                    out.setdefault(k, []).append(Site(name, lbl, order.get((name, lbl, i), 0), s))
    return {
        k: sorted(v, key=lambda x: (x.idx, x.stmt.src, x.block)) for k, v in sorted(out.items())
    }


# ---- dominating guards --------------------------------------------------------
def _domsets(idom, blocks):
    """``{label: its dominators, nearest first}``, from the immediate-dominator tree."""
    out = {}
    for lbl in blocks:
        chain, cur = [], lbl
        while cur is not None and cur not in chain:
            chain.append(cur)
            nxt = idom.get(cur)
            cur = None if nxt == cur else nxt
        out[lbl] = chain
    return out


def afterwrites(proc):
    """``{label: the regions the blocks a branch leads to write}``.

    What a condition read is what those writes had not yet changed, which is the
    only epoch :mod:`.history` keeps of a cell its own tick moves.
    """
    here = {
        lbl: frozenset(
            r
            for s in b.stmts
            for r in ((s.lo[0], s.hi[0]) if type(s) is W16 else (s.r,) if type(s) is Store else ())
            if r >= 0
        )
        for lbl, b in proc.blocks.items()
    }
    out = {lbl: here[lbl] for lbl in proc.blocks}
    for _ in range(len(proc.blocks)):
        moved = False
        for lbl, b in proc.blocks.items():
            got = out[lbl].union(*[out[s] for s in succs(b.term) if s in out] or [frozenset()])
            moved = moved or got != out[lbl]
            out[lbl] = got
        if not moved:
            break
    return {
        lbl: frozenset().union(*[out[s] for s in succs(b.term) if s in out] or [frozenset()])
        - here[lbl]
        for lbl, b in proc.blocks.items()
    }


def guardpath(proc):
    """``{label: ((condition, its truth here, the regions written after it), ...)}``.

    Control dependence, not dominance: a block a join carries is reached either
    way, however the join itself is dominated. Outermost condition first.
    """
    g = cfg(proc)
    after = afterwrites(proc)
    ipd = postdoms(g, proc, EXIT)
    pd = _domsets(ipd, [n for n in ipd if n in proc.blocks])
    dom, out = _domsets(idoms(proc, g), proc.blocks), {lbl: [] for lbl in proc.blocks}
    for d, b in proc.blocks.items():
        t = b.term
        if type(t) is not If or t.t == t.f or t.t not in pd or t.f not in pd:
            continue
        for lbl in proc.blocks:
            if lbl in pd.get(d, ()):
                continue
            if lbl in pd[t.t]:
                out[lbl].append((d, t.c, True))
            elif lbl in pd[t.f]:
                out[lbl].append((d, t.c, False))
    for _ in range(len(proc.blocks)):
        moved = False
        for lbl, gs in out.items():
            got = list(gs) + [x for d, _c, _v in gs for x in out[d] if x not in gs]
            moved = moved or len(got) != len(gs)
            out[lbl] = list(dict.fromkeys(got))
        if not moved:
            break
    depth = {lbl: len(dom[lbl]) for lbl in proc.blocks}
    return {
        lbl: tuple((c, v, after[d]) for d, c, v in sorted(gs, key=lambda x: depth.get(x[0], 0)))
        for lbl, gs in out.items()
    }


# ---- the context every reader of one program shares ---------------------------
class Ctx:
    """One program's dominator guards, call sites and name-stopping expansion."""

    def __init__(self, prog, names):
        self.prog = prog
        self.names = names
        self.rgn = prog.by_id()
        self.keep = stops(names)
        self.cache = {}
        self.callers = {}
        for n, p in prog.procs.items():
            for lbl, b in p.blocks.items():
                for s in b.stmts:
                    if type(s) is Call:
                        self.callers.setdefault(s.proc, []).append((n, lbl, s))

    def _memo(self, key, make):
        if key not in self.cache:
            self.cache[key] = make()
        return self.cache[key]

    def defs(self, proc):
        """``{name: expression}`` over the whole procedure, not one block."""
        return self._memo(("defs", proc), lambda: single_defs(self.prog.procs[proc]))

    def guards(self, proc):
        """:func:`guardpath` of one procedure."""
        return self._memo(("guards", proc), lambda: guardpath(self.prog.procs[proc]))

    def dom(self, proc):
        """``{label: its dominators, nearest first}`` of one procedure."""
        p = self.prog.procs[proc]
        return self._memo(("dom", proc), lambda: _domsets(idoms(p), p.blocks))

    def defs_at(self, proc, label):
        """:meth:`defs`, plus each many-times-defined name's one dominating value.

        SSA leaves a value one name and several ``Let`` s where the arms of a
        branch each supply it; at a block only one of them can have run.
        """
        out = dict(self.defs(proc))

        def make():
            hits = {}
            for lbl, b in self.prog.procs[proc].blocks.items():
                for s in b.stmts:
                    if type(s) is Let:
                        hits.setdefault(s.n, []).append((lbl, s.e))
            return {n: v for n, v in hits.items() if len(v) > 1}

        dom = self.dom(proc).get(label, ())
        for n, hits in self._memo(("multi", proc), make).items():
            got = [(dom.index(lbl), e) for lbl, e in hits if lbl in dom]
            near = [e for d, e in got if d == min(d for d, _e in got)] if got else []
            if len(near) == 1:
                out[n] = near[0]
        return out

    def loops(self, proc):
        """``{header: (body, latches)}`` of one procedure's natural loops."""

        def make():
            p = self.prog.procs[proc]
            g = cfg(p)
            return natural_loops(g, idoms(p, g), preds_of(p))

        return self._memo(("loops", proc), make)


def opened(e, defs, depth=DEPTH):
    """``e`` with every name ``defs`` maps substituted, addresses included.

    Not :func:`~.provenance.expand`, which stops at a named cell and so leaves a
    table read's own index a register: T1 evaluates that index over the horizon.
    """
    t = type(e)
    if t is Var and depth > 0 and e.n in defs:
        return opened(defs[e.n], defs, depth - 1)
    if t is Bin:
        return Bin(e.op, opened(e.a, defs, depth), opened(e.b, defs, depth), e.w)
    if t is Load:
        return Load(e.cls, opened(e.a, defs, depth), e.w, e.lo, e.hi, e.r)
    return R16(e.lo, e.hi, opened(e.a, defs, depth)) if t is R16 else e


def valnames(e):
    """Every name an expression reads, its addresses included."""
    return {x.n for x in walk(e) if type(x) is Var}


def arms(ctx, proc, block, value, skip=frozenset(), depth=DEPTH):
    """Every ``(guards, value)`` the callers of one store's procedure supply it.

    A value whose free names are its procedure's parameters is not yet a value:
    each call site substitutes its arguments and prepends its own guard path. The
    store's own copy index is not one of them -- every copy shares the value.
    """
    return _arms(ctx, proc, block, value, (), skip, depth)


def _arms(ctx, proc, block, value, extra, skip, depth):
    p = ctx.prog.procs[proc]
    defs = ctx.defs_at(proc, block)
    value = opened(value, defs)
    gs = tuple(
        (opened(c, defs), t, w) for c, t, w in tuple(ctx.guards(proc).get(block, ())) + tuple(extra)
    )
    here = [Arm(gs, value, proc, block)]
    free = ({REGVAR[i] for i in p.params} & valnames(value)) - skip
    calls = ctx.callers.get(proc) or ()
    if not free or not depth or not calls:
        return here
    out = []
    for cproc, clbl, call in calls:
        fn = renamer(dict(zip([REGVAR[i] for i in p.params], call.args)))
        out += _arms(
            ctx,
            cproc,
            clbl,
            sub_expr(value, fn),
            tuple((sub_expr(c, fn), t, w) for c, t, w in gs),
            skip,
            depth - 1,
        )
        if len(out) > MAXARMS:
            return here
    return out or here


# ---- the additive spine -------------------------------------------------------
def terms(e, sign=1):
    """``[(sign, term)]`` over an expression's ``+``/``-`` spine."""
    if type(e) is Bin and e.op in ("+", "-"):
        return terms(e.a, sign) + terms(e.b, sign if e.op == "+" else -sign)
    return [(sign, e)]


def flagish(e):
    """True for a value that can only be a carry or a borrow: one bit, or a name."""
    t = type(e)
    if t is Const:
        return e.v in (0, 1)
    if t is Bin:
        if e.op == "&" and is_one(e.b):
            return True
        return e.op in CMP or e.op == "carry" or (e.op == "|" and flagish(e.a) and flagish(e.b))
    return t is Var


def _unborrow(e):
    """``(D, C)`` of ``D + (1 - C)``, the 6510's subtract-with-borrow adjustment."""
    if type(e) is not Bin or e.op != "+":
        return e, None
    for x, y in ((e.a, e.b), (e.b, e.a)):
        if type(x) is Bin and x.op == "-" and is_one(x.a) and flagish(x.b):
            return y, x.b
    return e, None


def step(e, isself):
    """``(sign, delta, carry, borrow)`` for a self-referential add or subtract.

    The three 6510 spellings: ``(X + D) + C``, ``X - (D + (1 - C))``, ``X ± D``.
    ``borrow`` says the bit came off a subtract, whose increment is ``C - 1``.
    """
    carry = None
    if type(e) is Bin and e.op == "+" and flagish(e.b) and not isself(e.b):
        if not isself(e.a) or type(e.b) is not Const:  # ``X + k`` is a delta, not a carry
            carry, e = e.b, e.a
    if isself(e) and carry is not None:
        return 1, Const(0, 1), carry, False
    if type(e) is not Bin or e.op not in ("+", "-"):
        return None
    if e.op == "-":
        if not isself(e.a):
            return None
        d, borrow = _unborrow(e.b)
        if borrow is None and flagish(d) and type(d) is not Const:
            d, borrow = Const(0, e.w), d
        return (-1, d, borrow, True) if borrow is not None else (-1, d, carry, False)
    for x, y in ((e.a, e.b), (e.b, e.a)):
        if isself(x) and not isself(y):
            return 1, y, carry, False
    return None


def complemented(e, isself):
    """True when a value is the target's own cell under ``~X``, which is ``X ^ $FF``."""
    if type(e) is Bin and e.op == "^" and type(e.b) is Const and e.b.v == 0xFF:
        return isself(e.a)
    return False


# ---- masks, cells, sign extension ---------------------------------------------
def canon(e):
    """``e`` with every access's observed envelope dropped: two sites, one value.

    The same table read at two sites carries two envelopes, which structural
    equality would keep apart; the value it reads is the same.
    """
    t = type(e)
    if t is Load:
        return Load(e.cls, canon(e.a), e.w, 0, 0xFFFF, e.r)
    if t is Bin:
        return Bin(e.op, canon(e.a), canon(e.b), e.w)
    return R16(e.lo, e.hi, canon(e.a)) if t is R16 else e


def maskof(e):
    """``(value, mask)`` of ``v & m``, or ``(e, None)``."""
    if type(e) is Bin and e.op == "&" and type(e.b) is Const:
        return e.a, e.b.v
    return e, None


def cellof(e):
    """``(region, constant address)`` of a byte read, or ``None``."""
    if type(e) is Load and e.w == 1:
        base = addr_split(e.a)[0]
        return None if base is None else (e.r, base)
    return None


def lowbits(m):
    """The width of a low-bit mask, or ``None`` when ``m`` is not one."""
    return m.bit_length() if m and not m & (m + 1) else None


def _shifted(e, k):
    """The value ``e >> k`` reads, or ``None``."""
    if type(e) is Bin and e.op == ">>" and type(e.b) is Const and e.b.v == k:
        return e.a
    return None


def _notted(e):
    """The value under ``~e``."""
    return e.a if type(e) is Bin and e.op == "^" and type(e.b) is Const and e.b.v == 0xFF else None


def sext_split(lo, hi, k):
    """The one byte a ``k``-low-bit half and a signed high half both read, or ``None``.

    SID Wizard's filter step spells its positive arm ``(t & 7, t >> 3)`` and its
    negative one ``(t | $F8, ~(~t >> 3))``: one byte, sign-extended into 3 + 8.
    """
    m = (1 << k) - 1
    v, mask = maskof(lo)
    if mask != m:
        if type(lo) is not Bin or lo.op != "|" or type(lo.b) is not Const or lo.b.v != 0xFF - m:
            return None
        v = lo.a
    up = _shifted(hi, k)
    if up is None:
        inner = _notted(hi)
        up = None if inner is None else _shifted(inner, k)
        up = None if up is None else _notted(up)
    return v if up is not None and canon(up) == canon(v) else None


# ---- the variable-shift loop --------------------------------------------------
def shift_loop(ctx, proc, block):
    """The cell a right-shift loop containing ``block`` takes its count from.

    :func:`~.loops.repeats` refuses GoatTracker 2's (an equality exit) and
    Hubbard's (a second recurrence), so the count is read off the decrement.
    """
    p = ctx.prog.procs[proc]
    for header, (body, latches) in ctx.loops(proc).items():
        if block not in body:
            continue
        got = repeats(p, header, body, latches)
        if got is not None:
            return got[1]
        defs = ctx.defs(proc)
        for lbl in sorted(body):
            for s in p.blocks[lbl].stmts:
                d = _decrement(s, defs)
                if d is not None:
                    return d
                if type(s) is Let and type(s.e) is Var and _steps_down(defs.get(s.e.n), s.n):
                    v = _entry_value(p, header, body, preds_of(p), s.n)
                    if v is not None:
                        return opened(v, defs)
    return None


def _steps_down(e, name):
    """True when ``e`` is ``name - 1``: a loop index stepped by one."""
    return type(e) is Bin and e.op == "-" and type(e.a) is Var and e.a.n == name and is_one(e.b)


def _decrement(s, defs):
    """The cell a statement steps down by one, as the value it reads."""
    if type(s) is not Store or s.r < 0:
        return None
    v = opened(s.v, defs)
    if type(v) is not Bin or v.op != "-" or not is_one(v.b):
        return None
    return v.a if cellof(v.a) == (s.r, addr_split(s.a)[0]) else None


def reads(e):
    """Every byte or pair a value reads, addresses excluded."""
    xs = [x for x in walk(e) if type(x) is Load or type(x) is R16]
    inner = {id(y) for x in xs for y in walk(x.a)}
    return [x for x in xs if id(x) not in inner]
