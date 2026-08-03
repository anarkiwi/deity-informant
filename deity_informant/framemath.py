"""framemath: rung (d2), 16-bit arithmetic lifting (docs/frameprog.md 4).

Two byte statements jointly update one 16-bit quantity iff the two values they
write, concatenated, are a width-2 function of the two the cells held. No idiom
and no operator is named: ``eqlift``'s Z3-proven rules say what that word is."""

from __future__ import annotations

from egglog import EGraph

from . import datadecl
from . import eqlift as EQ
from . import framefuse as FF
from . import frameproc
from . import sidprog
from .structured import Proof

_W = ("word",)  # the packed lanes, standing for the word local inside a form
_WNAME = "@w"
_FLAGS = frozenset(("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL", "INT_CARRY"))
_BITWISE = frozenset(("INT_OR", "INT_AND", "INT_XOR"))
_COUNT = {1: "INT_ADD", 0xFF: "INT_SUB"}  # the predicated step, times the predicate
_COMM = frozenset(("add", "band", "bor", "bxor"))
_ITERS, _STEP, _VARIANTS = 25, 1, 8
_NODES, _TERM = 30000, 4000  # e-graph and translated-term budgets


def _loc(name):
    return ("loc", name, 2)


def _trunc(n):
    return ("op", "COPY", (n,), 1)


def _hi_byte(n):
    return _trunc(("op", "INT_RIGHT", (n, ("const", 8, 1)), 2))


def _resolve(n, env, at):
    """Follow ``loc`` definitions, each read where it was written."""
    while n[0] == "loc":
        got = env.value(n[1], at)
        if got is None:
            return n
        at, n = got
    return n


def _byte_op(val, env, at):
    """``val`` computes a byte, so it can be the lo half of a word."""
    r = _resolve(val, env, at)
    return r[0] == "op" and r[3] == 1


_FUSED = {}


def _split(hi, lo):
    """``hi<<8 | lo`` over two byte terms: the word the two halves would make."""
    return ("bor", ("shl", ("zext", hi), ("num", 8, 1), 2), ("zext", lo), 2)


def _lanes(t):
    """``(hi, lo)`` byte-lane loads of a packed word term, else None.

    One byte is not two lanes: ``c<<8 | c`` wears the shape but names a single
    cell twice, which ``_rank`` would then rate best of all for having its bases
    no distance apart. ``_pairs`` refuses it on the same ground."""
    if t[0] != "bor" or t[3] != 2:
        return None
    for a, b in (t[1:3], t[2:0:-1]):
        if a[0] != "shl" or a[2] != ("num", 8, 1) or a[1][0] != "zext" or b[0] != "zext":
            continue
        hi, lo = a[1][1], b[1]
        if hi != lo and all(x[0] in ("cell", "load") and x[2] == 1 for x in (hi, lo)):
            return hi, lo
    return None


def _signed(t):
    """An add of a step past the halfway mark is the subtract it stands for."""
    if t[0] == "add" and t[3] == 2 and t[2][0] == "num" and t[2][1] >= 0x8000:
        return ("sub", t[1], ("num", 0x10000 - t[2][1], 2), 2)
    return t


def _mark(t, p):
    """``t`` with every occurrence of the pack ``p`` standing as the word."""
    if t == p:
        return _W
    return tuple(_mark(a, p) if isinstance(a, tuple) else a for a in t)


def _commute(t):
    """A commutative form spelled one way: the word first, whatever extraction gave."""
    if not isinstance(t, tuple) or t == _W or not _holds(t, _W):
        return t
    kids = tuple(_commute(a) for a in t)
    if kids[0] in _COMM and _holds(kids[2], _W) and not _holds(kids[1], _W):
        return (kids[0], kids[2], kids[1]) + kids[3:]
    return kids


def _packs(t):
    """Every packed subterm of ``t``, in a fixed order."""
    out, stack = [], [t]
    while stack:
        x = stack.pop()
        if x[0] == "bor" and x[3] == 2 and x not in out:
            out.append(x)
        stack.extend(a for a in x[1:] if isinstance(a, tuple))
    return sorted(out, key=repr)


def _word_form(t):
    """Every ``(hi lane, lo lane, form)`` making ``t`` one function of one word.

    No operator is read off: the lanes are whatever byte pair the term packs, and
    the form is what is left once every occurrence of that pack stands as the
    word. A pack the rest of the term still reads a lane outside is no form."""
    out = []
    for p in _packs(t):
        lanes = _lanes(p)
        if lanes is None:
            continue
        form = _mark(t, p)
        if not any(_holds(form, x) for x in lanes):
            out.append((lanes[0], lanes[1], _signed(_commute(form))))
    return out


def _saturate(eg, rules):
    """Run the rules to saturation, or to a node budget, whichever comes first.

    The budget bounds the e-graph, not the question, since the allocator is what
    it protects: a Rust allocation failure aborts the process rather than raising.
    Extraction is sound at any cutoff, so a budget can cost a site, never buy one."""
    for _ in range(0, _ITERS, _STEP):
        if not eg.run(rules * _STEP).updated:
            return
        if sum(n for _f, n in eg.all_function_sizes()) > _NODES:
            return


def _holds(n, t):
    """``t`` occurs somewhere in the term ``n``."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x == t:
            return True
        stack.extend(a for a in x[1:] if isinstance(a, tuple))
    return False


def _unmask(hi):
    """``(hi without its constant AND, the word mask that stands for)``.

    ``mask_hoist`` says a masked hi byte is the whole word masked, so the query
    asks about the word the update makes and the mask rides on the answer."""
    if hi[0] == "band" and hi[3] == 1:
        for a, b in ((hi[1], hi[2]), (hi[2], hi[1])):
            if b[0] == "num":
                return a, (b[1] << 8) | 0xFF
    return hi, None


def _cheapest(eg, q):
    """One representative of an e-class, chosen by a total order and not by the pool.

    Which term extraction hands back is not contractual, and ``mask_hoist`` gives a
    masked pack two spellings of one cost; a step read off the pool is the seed
    dependence 7.1 settled for the word, arriving in the step instead."""
    return min(
        (EQ.canon(EQ._parse_ir(str(x))) for x in eg.extract_multiple(q, _VARIANTS)),
        key=lambda t: (EQ._cost(t), repr(t)),
    )


def _asked(eg, qs, lanes, mask):
    """The forms making ``lanes`` the lanes, each step read off its own e-class.

    ``pack(lanes) op step`` is the word by construction however ``step`` extracts,
    so a form is admitted only where the rules cancelled the pack back out: a step
    still reading a lane is the query answered with the question."""
    out = []
    for op, q in qs:
        step = _cheapest(eg, q)
        if not any(_holds(step, t) for t in lanes):
            form = _signed((op, _W, step, 2))
            if mask is not None:
                form = ("band", form, ("num", mask, 2), 2)
            out.append((lanes[0], lanes[1], form))
    return tuple(out)


def _ask(eg, memo, n, halves, lanes):
    """Let the pack of one ``(lo, hi)`` and a query per candidate grouping into ``eg``."""
    lo, hi = halves
    bare, mask = _unmask(hi)
    h = eg.let("h%d" % n, EQ._egg_of(_split(hi, lo), memo))
    w = h if mask is None else eg.let("w%d" % n, EQ._egg_of(_split(bare, lo), memo))
    qs = []
    for m, lane in enumerate(lanes):
        p = eg.let("p%d_%d" % (n, m), EQ._egg_of(_split(*lane), memo))
        fwd = ("add", eg.let("qa%d_%d" % (n, m), EQ.sub(w, p, 2)))
        qs.append((lane, (fwd, ("sub", eg.let("qs%d_%d" % (n, m), EQ.sub(p, w, 2))))))
    return (h, mask, qs)


def _fuse(halves, pairs):
    """Every 16-bit form each ``(lo, hi)`` saturates to, all of them in one e-graph.

    A rule pass costs the rule set whatever the e-graph holds, so every partner one
    statement may have is asked in one saturation: that is what keeps a query per
    pair affordable once program structure, not an idiom, bounds the pairs."""
    key = (halves, pairs)
    if key not in _FUSED:
        rules, _names = EQ.admitted_rules()
        eg, memo = EGraph(), {}
        asks = [_ask(eg, memo, n, h, p) for n, (h, p) in enumerate(zip(halves, pairs))]
        _saturate(eg, rules)
        out = []
        for h, mask, qs in asks:
            got = [f for lane, q in qs for f in _asked(eg, q, lane, mask)]
            forms = sorted(
                (EQ.canon(EQ._parse_ir(str(x))) for x in eg.extract_multiple(h, _VARIANTS)),
                key=lambda t: (EQ._cost(t), repr(t)),
            )
            got.extend(f for t in forms for f in _word_form(t))
            out.append(tuple(dict.fromkeys(got)))  # one term per grouping
        _FUSED[key] = tuple(out)
    return _FUSED[key]


def _emittable(n, env, at, i, j):
    """Every local ``n`` names holds at ``i`` the value it held at ``at``.

    A definition made inside the interval passes too, since ``settle`` inlines it
    -- but the inlining resolves a name to what it holds at ``j``, so any naming
    that means some other definition of it is refused."""
    for name in frameproc._locset(n):
        made = env.at(name, at)
        if made is not None and made[1] is not None and i < made[0] < j:
            if made != env.at(name, j):
                return False
            continue
        if made != env.at(name, i) or _rebound(env, name, i, j):
            return False
    return True


def _rebound(env, name, i, j):
    """The interval makes a definition of ``name``, which ``settle`` would inline."""
    got = env.at(name, j)
    return got is not None and i < got[0] < j


def _back(t, prov, ok):
    """The shallowest naming of a term that ``ok`` admits, else the term rebuilt.

    A read with no admissible naming is refused: the word assignment leads the
    interval, so a load rebuilt out of the graph is a read made where it was not."""
    if t == _W:
        return _loc(_WNAME)
    for ir, (_d, at) in sorted(prov.get(t, {}).items(), key=lambda kv: kv[1][0]):
        if ok(ir, at, t):
            return ir
    if t[0] in ("loc", "cell", "load"):
        return None
    kids = [_back(a, prov, ok) for a in t[1:] if isinstance(a, tuple)]
    return None if any(k is None for k in kids) else EQ.pass1_node(t, kids)


def _stale(t, at, lst, i, regions, env):
    """A store between the naming's point and the interval changed what it reads.

    ``to_egg`` keys a load by its address, so two reads of one cell either side of
    a store are one term and the shallowest naming of it may be the earlier read;
    that naming is not the value the word assignment leads with. ``_clobbers`` is
    the predicate ``_hoist`` holds the interval to, applied over the run up to it."""
    if at >= i:
        return False
    p1 = EQ.from_egg(t)
    if p1 is None:
        return True
    return any(_clobbers(_store(env, lst, k), (p1,), regions) for k in range(at, i))


def _lane_addr(t, prov, ok):
    """The address a lane term loads from."""
    return ("const", t[1], 2) if t[0] == "cell" else _back(t[1], prov, ok)


def _lane_ref(t, prov, ok, fallback):
    """A naming of the lane address the range rule reads, else ``fallback``.

    Every naming ``ok`` admits is the same address there, so preferring one the
    aliasing rule can read costs nothing; ``ok`` here forbids the interval's own
    definitions, since a row those pick out is not the row the lift emits."""
    if t[0] != "cell":
        for ir, (_d, at) in sorted(prov.get(t[1], {}).items(), key=lambda kv: kv[1][0]):
            if ok(ir, at, t[1]) and frameproc.addr_range(ir) is not None:
                return ir
    return fallback


def _inline(n, defs):
    """Substitute ``defs`` (definitions inside the interval) into ``n``."""
    if n[0] == "loc" and n[1] in defs:
        return _inline(defs[n[1]], {k: v for k, v in defs.items() if k != n[1]})
    if n[0] == "op":
        return (n[0], n[1], tuple(_inline(c, defs) for c in n[2]), n[3])
    if n[0] == "mem":
        return ("mem", _inline(n[1], defs), n[2])
    return n


def _hoist(lst, i, j, exprs, regions, env=None):
    """``exprs``, the interval's definitions inlined, and the statement blocking it.

    A read is carried up to ``i`` from where the interval made it, so a statement
    is held only against what is hoisted *past* it: one writing a cell later than
    the read it would spoil is no hazard, and its own definition is not one."""
    at = None
    for k in range(j - 1, i, -1):
        s = lst[k]
        if s[0] == "asg" and any(s[1] in frameproc._locset(x) for x in exprs):
            exprs = tuple(frameproc._subst_loc(x, s[1], s[2]) for x in exprs)
        elif _clobbers(_store(env, lst, k), exprs, regions):
            at = k
    return exprs, at


def _volatile(n):
    """True where ``n`` may load a volatile source, so its value cannot be dropped."""
    return any(FF._may_read(n, c) for c in sidprog._VOLS)


def _mem_refs(n):
    """``((base, index, modulus), width)`` of every memory reference under ``n``."""
    out, stack = [], [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            out.append((frameproc.addr_range(x[1], x[2]), x[2]))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


_span = frameproc.span  # the range rules live beside the definition-in-force query
_NOIDX = frameproc.NOIDX
_overlaps = frameproc.overlaps
_reach = frameproc.store_reach  # a store with no named base still has a bound


_OUTPUTS = ((0xD400, _NOIDX, 0x1F, 1, 0),) + tuple(
    (c, _NOIDX, 0, 1, 0) for c in sorted(sidprog._VOLS)
)


def _lane(base, idx, span, mod=0):
    """The one-byte range a lane occupies."""
    return (base, idx, span, 1, mod)


def _reads(exprs, at, regions):
    """True where evaluating ``exprs`` may load a cell of the range ``at``."""
    for ref, rw in (r for x in exprs for r in _mem_refs(x)):
        if ref is None:
            return True
        rb, ri, rm = ref
        if _overlaps(at, (rb, ri, _span(rb, ri, regions, rm), rw, rm)):
            return True
    return False


def _clobbers(stmt, exprs, regions):
    """True where ``stmt`` may change the value of any of ``exprs``."""
    if stmt[0] == "asg":
        return any(stmt[1] in frameproc._locset(x) for x in exprs)
    return True if stmt[0] != "st" else _disturbs(stmt, exprs, regions)


def _disturbs(stmt, exprs, regions):
    """The store may change a value ``exprs`` read.

    Index expressions may only be compared when the read is a direct load here;
    one captured earlier can be structurally equal yet hold a stale index."""
    return False if stmt[0] != "st" else _reads(exprs, _reach(stmt, regions), regions)


def _store(env, lst, k):
    """``lst[k]`` with its address spelled as the definition in force spells it.

    A local naming an address names the same address at ``k`` only where every
    local that naming reads still holds there what it held where it was written --
    the staleness ``_disturbs`` warns of, discharged rather than assumed."""
    s = lst[k]
    if s[0] != "st" or env is None:
        return s
    n, bound = s[1], k
    while n[0] == "loc":
        got = env.value(n[1], bound)
        if got is None or any(env.at(m, got[0]) != env.at(m, k) for m in frameproc._locset(got[1])):
            break
        n, bound = got[1], got[0]
    return ("st", n, s[2])


def _may_disturb(stmt, base, idx, regions, mod=0):
    """The store may write the lane at ``(base, idx)``; exact when the index matches."""
    if stmt[0] != "st":
        return False
    lane = _lane(base, idx, _span(base, idx, regions, mod), mod)
    return _overlaps(lane, _reach(stmt, regions))


def _writes(stmt, base, span, regions, mod=0):
    """True where ``stmt`` may store into the lane spanning ``base``."""
    if stmt[0] not in ("asg", "st"):
        return True
    if stmt[0] != "st":
        return False
    return _overlaps(_lane(base, _NOIDX, span, mod), _reach(stmt, regions))


def _hits(stmt, base, span, regions, mod=0):
    """True where ``stmt`` may read or write a cell of the lane at ``base``."""
    return _writes(stmt, base, span, regions, mod) or _reads(
        frameproc._stmt_exprs(stmt), _lane(base, _NOIDX, span, mod), regions
    )


class _Site:
    """One byte-wise 16-bit update: its lanes, the word it is, and its refusal."""

    __slots__ = (
        "lo hi form word direct merge src addr load idx hidx mod hmod why sid at lo_at hi_at"
    ).split()

    def __init__(self, form, addrs, refs, src, roles):
        self.form = form[2]
        self.lo_at, self.hi_at = roles
        self.lo, self.idx, self.mod = frameproc.addr_range(refs[0]) or (None, None, 0)
        self.hi, self.hidx, self.hmod = frameproc.addr_range(refs[1]) or (None, None, 0)
        self.word = (
            self.lo is not None
            and self.hi == self.lo + 1
            and self.hidx == self.idx
            and frameproc.addr_range(refs[0], 2) is not None
        )
        self.src, self.addr, self.load = src, addrs, addrs[0]
        self.direct = all(x[0] == "mem" for x in src[:2])
        self.merge = False
        self.sid = self.at = None
        self.why = None
        if self.lo is None or self.hi is None:
            self.why = "a lane address is not a const base plus index"
        elif self.idx is not None and self.hidx is not None and self.hidx != self.idx:
            self.why = "the two lanes are indexed differently"

    def settle(self, lst, i, j, regions, env=None):
        """Inline what the interval defines, since the word assignment leads it.

        Returns the statement blocking the hoist, if any. ``addr`` stays as
        written: it is matched against the two store addresses, which the lift
        does not move."""
        inner = {s[1]: s[2] for s in lst[i + 1 : j] if s[0] == "asg"}
        self.load = _inline(self.load, inner)
        self.idx = None if self.idx is None else _inline(self.idx, inner)
        self.src, at = _hoist(lst, i, j, self.src, regions, env)
        return at

    def name(self):
        """What the form does to the word, the mask the rules hoisted stripped off."""
        t = self.form
        if t[0] == "band" and t[2][0] == "num":
            t = t[1]
        return t[0]

    def proof(self):
        known = self.lo is not None and self.hi is not None
        body = "16-bit %s: lanes %s%s%s%s" % (
            self.name(),
            "$%04X/$%04X" % (self.lo, self.hi) if known else "unresolved",
            (", adjacent cells" if self.word else ", split tables") if known else "",
            "" if self.sid is None else ", SID pair $%04X" % self.sid,
            "" if not self.merge else ", one u16 store",
        )
        return Proof(
            self.lo or 0,
            "math",
            "refused" if self.why else "lifted",
            (self.lo, self.hi),
            "%s; %s" % (body, self.why or "carry chain"),
        )


def _value(s):
    """The value a store or assignment writes."""
    return s[2]


def _half_ref(s):
    """How later statements refer to the half this statement produced."""
    return ("loc", s[1]) if s[0] == "asg" else s[2]


def _rmw(lst, s, addrs):
    """The lanes are the two statements' own cells, so the update reads what it writes."""
    a, b = lst[s.lo_at], lst[s.hi_at]
    return (a[0], b[0]) == ("st", "st") and (a[1], b[1]) == addrs


def _cells(lst, i, j, env, prov):
    """The two statements' own byte cells as lane terms, else None.

    These are the lanes the *program* names, so a form built on them is the same
    form under any extraction order."""
    if (lst[i][0], lst[j][0]) != ("st", "st"):
        return None
    got = []
    for k in (i, j):
        a = lst[k][1]
        if a[0] == "const":
            got.append(("cell", a[1], 1, 0))
            continue
        t = EQ.to_egg(a, env.value, prov, k, _TERM)
        got.append(None if t is None else ("load", t, 1, 0))
    return None if any(t is None for t in got) else (got[1], got[0])


def _byte_reads(t):
    """The byte cells a value reads, an address's own reads excluded."""
    out, stack = [], [t]
    while stack:
        x = stack.pop()
        if x[0] in ("cell", "load") and x[2] == 1:
            if x not in out:
                out.append(x)
            continue
        stack.extend(a for a in x[1:] if isinstance(a, tuple))
    return out


def _rowbase(t):
    """``(row, base)``: the index a byte term is read at, and its constant part.

    A cell is a bare address, so it is all base and no row; a load of ``row + K``
    splits into the two, and one with no constant part is all row."""
    if t[0] == "cell":
        return None, t[1]
    inner = t[1][1] if t[1][0] == "zext" else t[1]
    if inner[0] == "add":
        for x, y in ((inner[1], inner[2]), (inner[2], inner[1])):
            if y[0] == "num":
                return x, y[1]
    return t[1], None


def _adjacent(h, l):
    """Two byte terms one apart in one row, which is the shape a 16-bit quantity has."""
    (rh, bh), (rl, bl) = _rowbase(h), _rowbase(l)
    return rh == rl and None not in (bh, bl) and bh == bl + 1


def _pairs(lo, hi, cells):
    """Every ``(hi, lo)`` lane pair the program itself names, in a fixed order.

    A byte the lo value reads against a byte the hi value reads is a grouping the
    e-graph can be asked about; which of them it happens to extract is not, so the
    set is closed here rather than left to the pool. Two lanes of one quantity are
    one row of one datum, so a cross pair of differing rows is asked about only
    where no same-row pair is on offer at all."""
    got = [] if cells is None else [cells]
    cross = [(h, l) for h in _byte_reads(hi) for l in _byte_reads(lo) if l != h]
    row = [p for p in cross if _rowbase(p[0])[0] == _rowbase(p[1])[0]]
    got.extend(p for p in (row or cross) if p not in got)
    return tuple(sorted(got, key=repr))


def _rank(lst, s, form):
    """Order over the offered groupings, decided by the program and not by extraction.

    The statements' own cells outrank a resolved pair, which outranks one sharing a
    row, then adjacency and the nearer bases: a step table sits away from the lanes
    it steps. The form's cost and spelling settle the rest, so a tie is one form."""
    known = s.lo is not None and s.hi is not None
    return (
        not _rmw(lst, s, s.addr),
        not known,
        s.idx != s.hidx,
        not s.word,
        abs(s.hi - s.lo) if known else 0,
        s.lo if known else 0,
        s.lo_at > s.hi_at,
        EQ._cost(form[2]),
        repr(form),
    )


def _linked(lo, hi, pairs, near):
    """Program structure pairing two statements to ask about, with no operator named.

    Proximity in the list, one value reading a byte the other reads -- how a carry,
    a shift bit or a wrap flag crosses -- or a pair the program names being two
    adjacent cells of one row. Cost is what this bounds, and only cost."""
    if near or set(_byte_reads(lo)) & set(_byte_reads(hi)):
        return True
    return any(_adjacent(h, l) for h, l in pairs)


def _halves(lst, i, j, env, vals):
    """Both orientations of the pair ``(i, j)``: either statement may hold the hi lane.

    ``LSR hi / ROR lo`` writes the hi lane first, so which statement holds which is
    a question, not a convention."""
    cells = _cells(lst, i, j, env, None)
    out = []
    for roles in ((i, j), (j, i)):
        fwd = roles[0] == i
        lo, hi = (vals[i], vals[j]) if fwd else (vals[j], vals[i])
        own = cells if cells is None or fwd else (cells[1], cells[0])
        pairs = _pairs(lo, hi, own)
        if _linked(lo, hi, pairs, j == i + 1):
            out.append((j, roles, (lo, hi), pairs))
    return out


def _offer(lst, i, js, env):
    """``{j: [(roles, forms)]}``: every form on offer for ``lst[i]``, from one e-graph."""
    vals = {k: EQ.to_egg(_value(lst[k]), env.value, None, k, _TERM) for k in (i,) + tuple(js)}
    if vals[i] is None:
        return {}
    asks = [a for j in js if vals[j] is not None for a in _halves(lst, i, j, env, vals)]
    out = {}
    for ask, forms in zip(asks, _fuse(tuple(a[2] for a in asks), tuple(a[3] for a in asks))):
        if forms:
            out.setdefault(ask[0], []).append((ask[1], forms))
    return out


def _site(lst, i, j, env, offered, regions):
    """The site the two statements' values are one 16-bit update of, else None.

    Lane *shape* is not lane *identity*, so the lanes the program names are asked
    for and every offered form is then weighed."""
    prov = {}
    for k in (i, j):
        EQ.to_egg(_value(lst[k]), env.value, prov, k, _TERM)
    _cells(lst, i, j, env, prov)

    def ok(ir, at, t):
        return _emittable(ir, env, at, i, j) and not _stale(t, at, lst, i, regions, env)

    def fixed(ir, at, t):
        return _emittable(ir, env, at, i, i) and not _stale(t, at, lst, i, regions, env)

    cands = []
    for roles, forms in offered:
        for form in forms:
            src = tuple(_back(t, prov, ok) for t in (form[1], form[0], form[2]))
            addrs = tuple(_lane_addr(t, prov, ok) for t in (form[1], form[0]))
            if any(x is None for x in src + addrs):
                continue
            refs = tuple(_lane_ref(t, prov, fixed, a) for t, a in zip((form[1], form[0]), addrs))
            cand = _Site(form, addrs, refs, src, roles)
            cands.append((_rank(lst, cand, form), cand))
    return min(cands, key=lambda kv: kv[0])[1] if cands else None


def _roles(site, i):
    """The lane statement ``i`` writes and the other one, named for a diagnostic."""
    return ("lo", "hi") if site.lo_at == i else ("hi", "lo")


def _other(site, i):
    """The lane statement ``i`` does not write, as a ``(base, index, modulus)``."""
    if site.lo_at == i:
        return site.hi, site.hidx, site.hmod
    return site.lo, site.idx, site.mod


def _match(lst, i, env, kinds=("st", "asg"), regions=None):
    """``(j, site)`` the update at ``lst[i]`` opens: the first that lifts, else refused.

    Program structure alone bounds the pairs asked about -- the run of statements
    that write a value -- so a carry crossing as control flow, as a shift bit or as
    a predicated count is as visible as one written into the other lane's value."""
    s = lst[i]
    if s[0] not in kinds or not _byte_op(_value(s), env, i):
        return None
    js = []
    for j in range(i + 1, len(lst)):
        if lst[j][0] not in ("st", "asg"):
            break
        if lst[j][0] in kinds:
            js.append(j)
    first = None
    for j, offered in sorted(_offer(lst, i, tuple(js), env).items()):
        site = _site(lst, i, j, env, offered, regions)
        if site is None:
            continue
        _decide(lst, i, j, site, regions, env)
        if site.why is None:
            return j, site
        if first is None:
            first = (j, site)
    return first


def _decide(lst, i, j, site, regions, env):
    """Settle the site's reads, then give it its refusal or the SID pair it writes."""
    base, idx, mod = _other(site, i)
    if site.why is None and _may_disturb(_store(env, lst, i), base, idx, regions, mod):
        site.why = "the %s destination may alias the %s lane" % _roles(site, i)
    blocked = site.settle(lst, i, j, regions, env)
    span = _span(site.hi, site.hidx, regions, site.hmod)
    site.why = site.why or _premise(lst, i, j, site, span, blocked, regions, env)
    site.at = None if site.why else _sid_pair(lst, i, site, span, regions, env)
    site.sid = None if site.at is None else site.at[3]


def _premise(lst, i, j, site, span, blocked=None, regions=None, env=None):
    """The refusal diagnostic for lifting ``lst[i:j+1]``, or None.

    Every read the word assignment leads with is held against the statements it is
    hoisted past, which is ``blocked``, ``settle``'s verdict; merging the two lane
    stores also moves the second one, so that needs no intervening read either."""
    kept = 1 if site.lo_at == i else 0
    later = [site.src[kept], site.src[2]]  # the leading store precedes both of these
    if _disturbs(_store(env, lst, i), later, regions):
        return "the %s destination may disturb the %s lane or the step" % _roles(site, i)
    if blocked is not None:
        return "an intervening statement changes an operand"
    site.merge = (
        site.word
        and _rmw(lst, site, site.addr)
        and not any(
            _hits(_store(env, lst, k), site.hi, span, regions, site.hmod) for k in range(i + 1, j)
        )
    )
    return None


def _sid_pair(lst, i, site, span, regions=None, env=None):
    """``(first, second, lo address, base)`` of a SID pair store over the halves.

    Freq/pulse/cutoff are last-write-wins, so the projection keys them by register
    and the two writes may be brought together (framelog.canonical)."""
    vlo, vhi = (_half_ref(lst[k]) for k in (site.lo_at, site.hi_at))
    at = {}
    for k in range(i, len(lst)):
        s = lst[k]
        if s[0] != "st":
            continue
        base, _idx = FF._addr_split(s[1])
        if base is None or FF._sid_base(base) is None:
            continue
        if s[2] == vlo and "lo" not in at:
            at["lo"] = (k, base, s[1])
        elif s[2] == vhi and "hi" not in at:
            at["hi"] = (k, base, s[1])
    if len(at) != 2 or at["hi"][1] != at["lo"][1] + 1:
        return None
    a, b = sorted((at["lo"][0], at["hi"][0]))
    if any(_hits(_store(env, lst, k), at["lo"][1], span + 1, regions) for k in range(a + 1, b)):
        return None
    return a, b, at["lo"][2], at["lo"][1]


def _lift(lst, site, name):
    """Rewrite ``lst`` in place: one word assignment, the halves truncated off it.

    The SID pair is settled before the halves are: it may name a lane store as
    one of its two, and that store then writes the whole word once rather than a
    half here and a half it never reaches."""
    i, j = site.lo_at, site.hi_at
    lv = _loc(name)
    word_load = site.word and (site.direct or site.merge)
    src = ("mem", site.load, 2) if word_load else FF._pack(site.src[0], site.src[1])
    word = frameproc._subst_loc(site.src[2], _WNAME, src)
    half = {i: _trunc(lv), j: _hi_byte(lv)}
    pair = () if site.at is None else site.at[:2]
    out = []
    for k, s in enumerate(lst):
        if k == min(i, j):
            out.append(("asg", name, word))
        if k in pair:
            if k == site.at[0]:
                out.append(("st", site.at[2], lv))
            continue
        if k in half:
            if k == j and site.merge:
                continue
            if s[0] == "asg":
                out.append(("asg", s[1], half[k]))
                continue
            out.append(("st", s[1], lv if (k == i and site.merge) else half[k]))
            continue
        out.append(s)
    lst[:] = out


def _read_names(stmts):
    """Local names any statement reads (a procedure-wide use set)."""
    out = set()
    for s in FF.stmts_of(stmts):
        for x in frameproc._stmt_exprs(s):
            out |= frameproc._locset(x)
    return out


def _bodies(stmts):
    """``stmts`` and every nested statement list under it."""
    yield stmts
    for s in stmts:
        for b in frameproc._stmt_bodies(s):
            yield from _bodies(b)


def _drop_dead(entry, params, rets, stmts):
    """Drop assignments unread anywhere in the procedure (pure, non-volatile)."""
    del entry
    fixed = set(params) | set(rets) | frameproc._ALL_REG_LOCALS
    while True:
        read = _read_names(stmts)
        gone = False
        for lst in _bodies(stmts):
            keep = [
                s
                for s in lst
                if not (
                    s[0] == "asg" and s[1] not in read and s[1] not in fixed and not _volatile(s[2])
                )
            ]
            if len(keep) != len(lst):
                lst[:] = keep
                gone = True
        if not gone:
            return


def _names(procs):
    """Every local name any procedure binds or reads."""
    used = set()
    for _e, params, rets, stmts in procs:
        used.update(params, rets)
        for s in FF.stmts_of(stmts):
            if s[0] in ("asg", "for"):
                used.add(s[1])
            if s[0] == "pcall":
                used.update(s[3])
            for x in frameproc._stmt_exprs(s):
                used |= frameproc._locset(x)
    return used


def _bit(n):
    """True where ``n`` computes 0 or 1, so a predicate is its own value."""
    if n[0] != "op":
        return False
    if n[1] in _FLAGS:
        return True
    return n[1] in _BITWISE and all(_bit(c) for c in n[2])


def _counts(addr, val):
    """The word op a store of ``val`` to ``addr`` makes of that cell, else None."""
    if val[0] != "op" or val[1] != "INT_ADD" or len(val[2]) != 2 or val[3] != 1:
        return None
    a, b = val[2]
    if a != ("mem", addr, 1) or b[0] != "const":
        return None
    return _COUNT.get(b[1])


def _flag(cond, taken):
    """The condition as the value the arm runs on: ``c``, or ``1 - c`` for the other."""
    return cond if taken else ("op", "INT_SUB", (("const", 1, 1), cond), 1)


def _arm_store(arm):
    """The one store an arm makes, its own definitions inlined, else None."""
    if not arm or arm[-1][0] != "st" or any(s[0] != "asg" for s in arm[:-1]):
        return None
    defs = {s[1]: s[2] for s in arm[:-1]}
    return ("st", _inline(arm[-1][1], defs), _inline(arm[-1][2], defs))


def _observable(t, regions):
    """The store reads an input or writes an output, so running it always is an event."""
    return any(
        _overlaps(_reach(t, regions), r) or _reads((t[1], t[2]), r, regions) for r in _OUTPUTS
    )


def _escapes(stmts, arm):
    """A name the arm binds that outlives it: a register, or one read outside it."""
    bound = {s[1] for s in arm if s[0] == "asg"}
    if bound & frameproc._ALL_REG_LOCALS:
        return True
    for s in FF.stmts_of(stmts):
        if any(s is a for a in arm):
            continue
        for x in frameproc._stmt_exprs(s):
            if bound & frameproc._locset(x):
                return True
    return False


def _predicated(s, stmts, regions):
    """``if c { X = X +- 1 }`` as the value ``X +- c`` it writes, else None.

    The condition is a flag, so it is 0 or 1 and the step is it times the count;
    a volatile or output store is left alone, since reading or writing one back
    unconditionally is not the same event."""
    if s[0] != "if" or not _bit(s[2]):
        return None
    arms = (s[3], s[4]) if s[1] == "if" else (s[4], s[3])
    for n, arm in enumerate(arms):
        if arms[1 - n]:
            continue
        t = _arm_store(arm)
        mn = None if t is None else _counts(t[1], t[2])
        if mn is None or _escapes(stmts, arm) or _observable(t, regions):
            return None
        return ("st", t[1], ("op", mn, (("mem", t[1], 1), _flag(s[2], n == 0)), 1))
    return None


def _predicate_values(stmts, lst, regions, kept):
    """Rewrite ``lst``'s predicated updates as values; ``kept`` names the way back."""
    for k, s in enumerate(lst):
        got = _predicated(s, stmts, regions)
        if got is not None:
            kept[got] = s
            lst[k] = got


def _restore(lists, kept):
    """Put back every predicated update no lift consumed."""
    for lst in kept and lists:
        lst[:] = [kept.get(s, s) if s[0] == "st" else s for s in lst]


def apply_rung(procs, decls=()):
    """Rung (d2) in place over ``procs``; returns the per-site proofs.

    Two sweeps: cell destinations first, so a half written through a register
    still collapses into the lane store, then the local-destination form. ``decls``
    bound the index spans an aliasing test has to assume (``_span``)."""
    regions = datadecl.Regions(decls)
    used, proofs, n = _names(procs), [], 0
    for _e, params, rets, stmts in procs:
        lifted, kept, lists = False, {}, list(_bodies(stmts))
        for lst in lists:
            done = set()
            _predicate_values(stmts, lst, regions, kept)
            for kinds in (("st",), ("asg",)):
                i, env = 0, frameproc.Defs(lst)
                while i < len(lst):
                    got = _match(lst, i, env, kinds, regions)
                    if got is None:
                        i += 1
                        continue
                    _j, site = got
                    if (site.lo, site.hi) in done:
                        i += 1
                        continue
                    done.add((site.lo, site.hi))
                    proofs.append(site.proof())
                    if site.why:
                        i += 1
                        continue
                    while "d%d" % n in used:
                        n += 1
                    name = "d%d" % n
                    used.add(name)
                    _lift(lst, site, name)
                    env, lifted = frameproc.Defs(lst), True
                    i += 2
        _restore(lists, kept)
        if lifted:
            _drop_dead(_e, params, rets, stmts)
    return proofs
