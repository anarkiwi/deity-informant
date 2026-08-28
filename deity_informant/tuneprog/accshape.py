"""T1 -- the shapes an accumulator's update is made of: guards, arms, deltas.

A recurrence is not one statement: GoatTracker 2 stores its vibrato phase in
``p_109E`` from a value four arms of ``p_1082`` supply. :class:`Ctx` joins the
dominating guards and the call arguments; the rest is section 5's grammar.
"""

from __future__ import annotations

from collections import namedtuple

from .accguard import _domsets, cellof, guardpath, key_of, opened, propagate, reads, unpin
from .accguard import scratch, valnames, EMPTY, _inloop
from .graph import cfg, idoms, natural_loops, preds_of, rpo
from .idioms import CMP, is_one
from .ir import Bin, Call, Const, Let, Load, R16, REGIDX, REGVAR, Store, Var, W16
from .irwalk import addr_split, renamer, single_defs, sub_expr, walk
from .loops import _entry_value, _exit_tests, repeats
from .provenance import stops

DEPTH = 4  # call frames a value is chased through before it is left as it stands
MAXARMS = 256
Arm = namedtuple("Arm", "guards value addr proc block path exact")
Site = namedtuple("Site", "proc block idx stmt at")


def selfread(tgt):
    """The test for a value that reads the accumulator's own cell."""
    if tgt.kind == "pair":
        lo, hi = tgt.cells
        return lambda e: type(e) is R16 and (tuple(e.lo), tuple(e.hi)) == (lo, hi)
    key = tgt.cells[0]
    return lambda e: cellof(e) == key


def rank(prog, root="tick"):
    """``({key: position}, {proc: first call chain})``: where a statement sits in its procedure.

    Reverse postorder, not :meth:`~.ir.Proc.order`: a reload that guards a segment
    runs before the step that follows it in the same tick, and a preorder puts the
    step's join first. ``(proc, block)`` is the block's terminator, where a branch
    reads its condition. A tick position is the tuple of positions along a call
    chain (:meth:`Ctx.at`); ``first`` completes a chain no arm climbed, innermost
    hop first.
    """
    out, first = {}, {}
    for name, p in prog.procs.items():
        n = 0
        for lbl in rpo(p):
            for i in range(len(p.blocks[lbl].stmts)):
                out[(name, lbl, i)] = n
                n += 1
            out[(name, lbl)] = n
            n += 1
    todo, order = [(root, ())], [root]
    while todo:
        name, chain = todo.pop(0)
        p = prog.procs.get(name)
        if p is None or name in first:
            continue
        first[name] = chain
        for lbl in rpo(p):
            for i, s in enumerate(p.blocks[lbl].stmts):
                if type(s) is Call and s.proc not in first:
                    todo.append((s.proc, ((name, lbl, i),) + chain))
        order += [n for n, _c in todo if n not in order]
    for name in prog.procs:
        first.setdefault(name, ())
    return out, first


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
                    out.setdefault(k, []).append(Site(name, lbl, order.get((name, lbl, i), 0), s, i))
    return {
        k: sorted(v, key=lambda x: (x.idx, x.stmt.src, x.block)) for k, v in sorted(out.items())
    }


# ---- the context every reader of one program shares ---------------------------
class Ctx:
    """One program's dominator guards, call sites and name-stopping expansion."""

    def __init__(self, prog, names):
        self.prog = prog
        self.names = names
        self.rgn = prog.by_id()
        self.keep = stops(names)
        self.rank, self.first = rank(prog)
        self.scratch = scratch(prog)
        self.prop = propagate(prog, self.scratch)
        self.cache = {}
        self.callers = {}
        for n, p in prog.procs.items():
            for lbl, b in p.blocks.items():
                for i, s in enumerate(b.stmts):
                    if type(s) is Call:
                        self.callers.setdefault(s.proc, []).append((n, lbl, i, s))

    def _memo(self, key, make):
        if key not in self.cache:
            self.cache[key] = make()
        return self.cache[key]

    def defs(self, proc):
        """``{name: expression}`` over the whole procedure, not one block."""
        return self._memo(("defs", proc), lambda: single_defs(self.prog.procs[proc]))

    def guardsites(self, proc):
        """:func:`guardpath` of one procedure, each guard with its deciding block first."""
        return self._memo(("guardsites", proc), lambda: guardpath(self.prog.procs[proc], True))

    def guards(self, proc):
        """:func:`guardpath` of one procedure."""
        return self._memo(
            ("guards", proc),
            lambda: {l: tuple(g[1:] for g in gs) for l, gs in self.guardsites(proc).items()},
        )

    def deciders(self, proc, block):
        """``(proc, block, index)`` where each guard of a block is read: its decider's end."""
        p = self.prog.procs[proc]
        return tuple(
            (proc, d, len(p.blocks[d].stmts)) for d, *_g in self.guardsites(proc).get(block, ())
        )

    def chain(self, proc, path):
        """A call chain to the root, innermost hop first: the arm's, completed by the first."""
        top = path[-1][0] if path else proc
        return tuple(path) + self.first.get(top, ())

    def at(self, chain, key):
        """The tick position of ``key`` (a statement or a terminator) on a call chain."""
        return tuple(self.rank[h] for h in reversed(chain)) + (self.rank[key],)

    def ranked(self, proc, block, idx, arms_):
        """``[(arm, its rank, ((rank, where), ...) per guard)]`` on each arm's own chain.

        Each guard is ranked at its deciding block's end in the procedure of its own
        level of the chain; a chain completed past what the arm climbed carries no
        guards from those levels.
        """
        out = []
        for a in arms_:
            chain = self.chain(proc, a.path)
            levels = [(proc, block)] + [(h[0], h[1]) for h in a.path]
            at = []
            for j, (name, lbl) in reversed(list(enumerate(levels))):
                rest = chain[j:]
                for _p, d, i in self.deciders(name, lbl):
                    at.append((self.at(rest, (name, d)), (name, d, i, rest)))
            out.append((a, self.at(chain, (proc, block, idx)), tuple(at)))
        return out

    def dom(self, proc):
        """``{label: its dominators, nearest first}`` of one procedure."""
        p = self.prog.procs[proc]
        return self._memo(("dom", proc), lambda: _domsets(idoms(p), p.blocks))

    def defs_at(self, proc, label):
        """:meth:`defs`, plus each many-times-defined name's one dominating value.

        SSA leaves a value one name and several ``Let`` s where the arms of a
        branch each supply it; at a block only one of them can have run -- unless
        the block is inside a loop that carries one of them, where the preheader's
        value dominates every iteration and is right for none but the first.
        """
        return self.placed(proc, label)[0]

    def placed(self, proc, label):
        """``(defs, {name: its definition's site})`` of :meth:`defs_at`."""

        def make():
            hits = {}
            for lbl, b in self.prog.procs[proc].blocks.items():
                for i, s in enumerate(b.stmts):
                    if type(s) is Let:
                        hits.setdefault(s.n, []).append((lbl, i, s.e))
            return hits

        hits = self._memo(("lets", proc), make)
        out = dict(self.defs(proc))
        sites = {n: (proc, v[0][0], v[0][1]) for n, v in hits.items() if n in out}
        dom, here = self.dom(proc).get(label, ()), self.inloop(proc).get(label, EMPTY)
        for n, v in hits.items():
            if len(v) < 2 or any(here & self.inloop(proc).get(lbl, EMPTY) for lbl, _i, _e in v):
                continue
            got = [(dom.index(lbl), lbl, i, e) for lbl, i, e in v if lbl in dom]
            near = [x for x in got if x[0] == min(d for d, *_r in got)] if got else []
            if len(near) == 1:
                out[n], sites[n] = near[0][3], (proc, near[0][1], near[0][2])
        return out, sites

    def inloop(self, proc):
        """``{label: the loop headers whose body holds it}``."""
        return self._memo(("inloop", proc), lambda: _inloop(self.loops(proc)))

    def parked(self, proc):
        """``{name: the cell it advanced}``: a successor read is the cell it lands in.

        ``t = T[cursor]; cursor = t`` names one value twice, and opening the name
        back to its read puts the *previous* cursor in every value that follows it
        this tick. The cell's own history is that value, at the epoch the tick left
        it -- so a name a store parks in the cell its own definition *indexed* is
        that cell. A name whose definition reads the cell as a term is the cell's
        own recurrence, and opening it is the whole of what T1 reads.
        """

        def make():
            defs, hits = self.defs(proc), {}
            for b in self.prog.procs[proc].blocks.values():
                for s in b.stmts:
                    if type(s) is Store and s.r >= 0 and type(s.v) is Var:
                        hits.setdefault(s.v.n, []).append(s)
            out = {}
            for n, ss in hits.items():
                key = key_of(ss[0])
                if len(ss) != 1 or key is None or n not in defs:
                    continue
                inner = set(walk(defs[n])) - set(reads(defs[n]))
                if any(cellof(x) == key.cells[0] for x in inner):
                    out[n] = Load("ram", ss[0].a, ss[0].w, ss[0].lo, ss[0].hi, ss[0].r)
            return out

        return self._memo(("parked", proc), make)

    def loops(self, proc):
        """``{header: (body, latches)}`` of one procedure's natural loops."""

        def make():
            p = self.prog.procs[proc]
            g = cfg(p)
            return natural_loops(g, idoms(p, g), preds_of(p))

        return self._memo(("loops", proc), make)


def external(e):
    """True when a value reaches outside the tick: an entry register or an ``io`` read.

    The section 8 rule for a live bit: a carry another block of the tick defines is
    section 5's ``+ carry(site)``; one the tick is *given* is an external input, and
    the plane refuses rather than guess a bit it never saw.
    """
    if any(type(x) is Load and x.cls == "io" for x in walk(e)):
        return True
    return type(e) is Var and e.n in REGIDX


def arms(ctx, proc, block, value, addr, skip=frozenset(), depth=DEPTH):
    """Every ``(guards, value)`` the callers of one store's procedure supply it.

    A value and the address it lands at, whose free names are the procedure's own
    parameters, are not yet a value and an address:
    each call site substitutes its arguments and prepends its own guard path. The
    store's own copy index is not one of them -- every copy shares the value, so
    ``skip`` is left standing in the value and in the guards alike, and the replay
    binds it per copy. A rerolled loop is the case: its index is one ``Let`` per
    iteration, and the nearest dominating one is the first iteration's constant.
    Every caller is climbed: which of a procedure's visits ran is its callers' guards.
    """
    return _arms(ctx, proc, block, value, value, addr, (), (), skip, depth)


def _arms(ctx, proc, block, value, exact, addr, extra, path, skip, depth):
    """``exact`` is the value with no :meth:`Ctx.parked` name read as its cell: what
    an epoch-exact reader (:mod:`.accstep`) evaluates, since a parked successor read
    stands for the cell's own next value and is a no-op as a store of it."""
    p = ctx.prog.procs[proc]
    plain, sites = ctx.placed(proc, block)
    plain = {n: e for n, e in plain.items() if n not in skip}
    defs = {**plain, **{n: e for n, e in ctx.parked(proc).items() if n not in skip}}
    value, addr = opened(value, defs, DEPTH, ctx.prop), opened(addr, defs, DEPTH, ctx.prop)
    exact = opened(exact, plain, DEPTH, ctx.prop, sites)
    gs = tuple(
        (opened(c, defs, DEPTH, ctx.prop), t, w)
        for c, t, w in tuple(ctx.guards(proc).get(block, ())) + tuple(extra)
    )
    here = [Arm(gs, value, addr, proc, block, path, exact)]
    calls = ctx.callers.get(proc) or ()
    if not depth or not calls:
        return here
    out = []
    for cproc, clbl, ci, call in calls:
        fn = renamer(dict(zip([REGVAR[i] for i in p.params], call.args)))
        out += _arms(
            ctx,
            cproc,
            clbl,
            sub_expr(value, fn),
            sub_expr(exact, fn),
            sub_expr(addr, fn),
            tuple((sub_expr(c, fn), t, w) for c, t, w in gs),
            tuple(path) + ((cproc, clbl, ci),),
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
    for header, body, latches in enclosing(ctx, proc, block):
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


def enclosing(ctx, proc, block):
    """The loops whose body holds ``block``, innermost first.

    A block of a nested loop belongs to every loop around it, and the counted one
    is the nearest: the smallest body that holds it, which is also the one order a
    dictionary of headers does not fix.
    """
    got = sorted((len(b), h, b, l) for h, (b, l) in ctx.loops(proc).items() if block in b)
    return [(h, b, l) for _n, h, b, l in got]


def onepass(ctx, proc, block, guards):
    """True when a counted loop runs its bound's own value of passes, not one more.

    :func:`~.loops.repeats` reads a loop tested after its body, which runs
    ``bound + 1`` times whatever the start is. A loop whose exit test *precedes*
    the body has already dropped the last pass, and what says so is that test
    standing in the body's own guards -- where a back edge would have taken it out.
    """
    p = ctx.prog.procs[proc]
    got = enclosing(ctx, proc, block)
    tests = {repr(canon(c)) for c, _t, _at in _exit_tests(p, got[0][1])} if got else set()
    return any(repr(canon(g)) in tests for g, _t, _w in guards)


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
