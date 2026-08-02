"""framemath: rung (d2), 16-bit arithmetic lifting (docs/frameprog.md 4).

An 8-bit add/sub on the lo lane plus the carry (borrow) it propagates into the hi
lane is one 16-bit add/sub. Not a catalogue of 6502 idioms: the two byte results
are concatenated and ``eqlift``'s Z3-proven rules say what ``hi<<8 | lo`` is."""

from __future__ import annotations

from egglog import EGraph

from . import datadecl
from . import eqlift as EQ
from . import framefuse as FF
from . import frameproc
from . import sidprog
from .structured import Proof

_ADD, _SUB = "INT_ADD", "INT_SUB"
_OF = {"add": _ADD, "sub": _SUB}
_LINK = frozenset(("INT_CARRY", "INT_LESS", "INT_LESSEQUAL"))
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


def _links(n, env, at):
    """``n`` reads a carry or a borrow, without which no split can rejoin."""
    stack = [(n, at)]
    while stack:
        x, b = stack.pop()
        if x[0] == "op":
            if x[1] in _LINK:
                return True
            stack.extend((c, b) for c in x[2])
        elif x[0] == "mem":
            stack.append((x[1], b))
        elif x[0] == "loc":
            got = env.value(x[1], b)
            if got is not None:
                stack.append((got[1], got[0]))
    return False


_FUSED = {}


def _split(hi, lo):
    """``hi<<8 | lo`` over two byte terms: the word the two halves would make."""
    return ("bor", ("shl", ("zext", hi), ("num", 8, 1), 2), ("zext", lo), 2)


def _lanes(t):
    """``(hi, lo)`` byte-lane loads of a packed word term, else None."""
    if t[0] != "bor" or t[3] != 2:
        return None
    for a, b in (t[1:3], t[2:0:-1]):
        if a[0] != "shl" or a[2] != ("num", 8, 1) or a[1][0] != "zext" or b[0] != "zext":
            continue
        hi, lo = a[1][1], b[1]
        if all(x[0] in ("cell", "load") and x[2] == 1 for x in (hi, lo)):
            return hi, lo
    return None


def _signed(op, step):
    """An add of a step past the halfway mark is the subtract it stands for."""
    if op == "add" and step[0] == "num" and step[2] == 2 and step[1] >= 0x8000:
        return "sub", ("num", 0x10000 - step[1], 2)
    return op, step


def _word_form(t):
    """``(op, hi lane, lo lane, step, mask)`` of a 16-bit form of ``t``, else None."""
    mask = None
    if t[0] == "band" and t[3] == 2:
        for a, b in (t[1:3], t[2:0:-1]):
            if b[0] == "num":
                mask, t = b[1], a
                break
    if t[0] not in _OF or t[3] != 2:
        return None
    for w, step in ((t[1:3], t[2:0:-1]) if t[0] == "add" else (t[1:3],)):
        got = _lanes(w)
        if got is not None:
            op, step = _signed(t[0], step)
            return (op, got[0], got[1], step, mask)
    return None


def _saturate(eg, rules):
    """Run the rules to saturation, or to a node budget, whichever comes first.

    Associativity and commutativity grow an e-graph over a long expression
    without bound. Extraction is sound at any cutoff, so a budget can cost a
    site but can never buy a wrong one. The budget is checked every iteration:
    allocation happens inside ``run``, so a batched step is unbounded however
    small the node budget is."""
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


def _asked(eg, qs, lanes, mask):
    """The forms making ``lanes`` the lanes, each step read off its own e-class.

    ``pack(lanes) op step`` is the word by construction however ``step`` extracts,
    so a form is admitted only where the rules cancelled the pack back out: a step
    still reading a lane is the query answered with the question."""
    out = []
    for op, q in qs:
        step = EQ.canon(EQ._parse_ir(str(eg.extract(q))))
        if not any(_holds(step, t) for t in lanes):
            sop, sstep = _signed(op, step)
            out.append((sop, lanes[0], lanes[1], sstep, mask))
    return tuple(out)


def _fuse(lo, hi, pairs=()):
    """Every 16-bit form the two byte halves saturate to, the asked-for ones first.

    ``hi<<8`` has a zero low byte, so ``|`` is ``+`` and ``(hi<<8 | step) + lo``
    equals ``(hi<<8 | lo) + step``: lane *shape* does not make a lane, and the
    wanted grouping need not be extracted at all, so ``pairs`` are asked for."""
    key = (lo, hi, pairs)
    if key not in _FUSED:
        rules, _names = EQ.admitted_rules()
        eg, memo, asked = EGraph(), {}, []
        h = eg.let("h", EQ._egg_of(_split(hi, lo), memo))
        bare, mask = _unmask(hi)
        w = h if mask is None else eg.let("w", EQ._egg_of(_split(bare, lo), memo))
        qs = []
        for n, lanes in enumerate(pairs):
            p = eg.let("p%d" % n, EQ._egg_of(_split(*lanes), memo))
            fwd = ("add", eg.let("qa%d" % n, EQ.sub(w, p, 2)))
            qs.append((lanes, (fwd, ("sub", eg.let("qs%d" % n, EQ.sub(p, w, 2))))))
        _saturate(eg, rules)
        for lanes, q in qs:
            asked.extend(_asked(eg, q, lanes, mask))
        forms = sorted(
            (EQ.canon(EQ._parse_ir(str(x))) for x in eg.extract_multiple(h, _VARIANTS)),
            key=lambda t: (EQ._cost(t), repr(t)),
        )
        got = [f for f in map(_word_form, forms) if f is not None]
        _FUSED[key] = tuple(dict.fromkeys(asked + got))  # one term per grouping
    return _FUSED[key]


def _emittable(n, env, at, i, j):
    """Every local ``n`` names holds at ``i`` the value it held at ``at``.

    A definition made inside the interval passes too, since ``settle`` inlines it
    -- but only one this list wrote, which is all ``settle`` collects."""
    for name in frameproc._locset(n):
        made = env.at(name, at)
        if made == env.at(name, i):
            continue
        if made is None or made[1] is None or not i < made[0] < j:
            return False
    return True


def _back(t, prov, ok):
    """The shallowest naming of a term that ``ok`` admits, else the term rebuilt.

    A read with no admissible naming is refused: the word assignment leads the
    interval, so a load rebuilt out of the graph is a read made where it was not."""
    for ir, (_d, at) in sorted(prov.get(t, {}).items(), key=lambda kv: kv[1][0]):
        if ok(ir, at):
            return ir
    if t[0] in ("loc", "cell", "load"):
        return None
    kids = [_back(a, prov, ok) for a in t[1:] if isinstance(a, tuple)]
    return None if any(k is None for k in kids) else EQ.pass1_node(t, kids)


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
            if ok(ir, at) and frameproc.addr_range(ir) is not None:
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


def _writes(stmt, base, span, regions, idx=_NOIDX, mod=0):
    """True where ``stmt`` may store into the lane spanning ``base``."""
    if stmt[0] not in ("asg", "st"):
        return True
    if stmt[0] != "st":
        return False
    return _overlaps(_lane(base, idx, span, mod), _reach(stmt, regions))


def _hits(stmt, base, span, regions, mod=0):
    """True where ``stmt`` may read or write a cell of the lane at ``base``."""
    return _writes(stmt, base, span, regions, _NOIDX, mod) or _reads(
        frameproc._stmt_exprs(stmt), _lane(base, _NOIDX, span, mod), regions
    )


class _Site:
    """One byte-wise 16-bit update: its lanes, the word it is, and its refusal."""

    __slots__ = "lo hi op mask word direct merge src addr load idx hidx mod hmod why sid at".split()

    def __init__(self, form, addrs, refs, src):
        self.op, self.mask = _OF[form[0]], form[4]
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

    def proof(self):
        known = self.lo is not None and self.hi is not None
        body = "16-bit %s: lanes %s%s%s%s" % (
            "add" if self.op == _ADD else "sub",
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


def _rmw(lst, i, j, addrs):
    """The lanes are the two statements' own cells, so the update reads what it writes."""
    return (lst[i][0], lst[j][0]) == ("st", "st") and (lst[i][1], lst[j][1]) == addrs


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


def _pairs(lo, hi, cells):
    """Every ``(hi, lo)`` lane pair the program itself names, in a fixed order.

    A byte the lo value reads against a byte the hi value reads is a grouping the
    e-graph can be asked about; which of them it happens to extract is not, so the
    set is closed here rather than left to the pool."""
    got = [] if cells is None else [cells]
    for h in _byte_reads(hi):
        for lane in _byte_reads(lo):
            if lane != h and (h, lane) not in got:
                got.append((h, lane))
    return tuple(sorted(got, key=repr))


def _rank(lst, i, j, s, form):
    """Order over the offered groupings, decided by the program and not by extraction.

    The statements' own cells outrank a resolved pair, which outranks one sharing a
    row, then adjacency and the nearer bases: a step table sits away from the lanes
    it steps. The step's cost and spelling settle the rest, so a tie is one form."""
    known = s.lo is not None and s.hi is not None
    return (
        not _rmw(lst, i, j, s.addr),
        not known,
        s.idx != s.hidx,
        not s.word,
        abs(s.hi - s.lo) if known else 0,
        s.lo if known else 0,
        EQ._cost(form[3]),
        repr(form),
    )


def _site(lst, i, j, env):
    """The site the two statements' values are one 16-bit update of, else None.

    Lane *shape* is not lane *identity*: a step table wears it too, so ``_fuse``
    can offer a grouping pairing the hi lane with the step (Antitrack_01). The
    lanes the program names are asked for, and every offered form is then weighed."""
    prov = {}
    lo, hi = (EQ.to_egg(_value(lst[k]), env.value, prov, k, _TERM) for k in (i, j))
    if lo is None or hi is None:
        return None

    def ok(ir, at):
        return _emittable(ir, env, at, i, j)

    def fixed(ir, at):
        return _emittable(ir, env, at, i, i)

    cands = []
    for form in _fuse(lo, hi, _pairs(lo, hi, _cells(lst, i, j, env, prov))):
        src = tuple(_back(t, prov, ok) for t in (form[2], form[1], form[3]))
        addrs = tuple(_lane_addr(t, prov, ok) for t in (form[2], form[1]))
        if any(x is None for x in src + addrs):
            continue
        refs = tuple(_lane_ref(t, prov, fixed, a) for t, a in zip((form[2], form[1]), addrs))
        cand = _Site(form, addrs, refs, src)
        cands.append((_rank(lst, i, j, cand, form), cand))
    return min(cands, key=lambda kv: kv[0])[1] if cands else None


def _match(lst, i, env, kinds=("st", "asg"), regions=None):
    """``(j, site)`` for the update ``lst[i]`` opens, else None.

    The **sources** decide the lift: two byte lanes linked by a carry are one
    16-bit quantity wherever their halves are then written. The destinations
    decide only whether the two writes collapse into one ``u16`` store."""
    s = lst[i]
    if s[0] not in kinds or not _byte_op(_value(s), env, i):
        return None
    for j in range(i + 1, len(lst)):
        t = lst[j]
        if t[0] not in ("st", "asg"):
            return None
        if t[0] not in kinds or not _links(_value(t), env, j):
            continue
        site = _site(lst, i, j, env)
        if site is not None:
            if site.why is None and _may_disturb(
                _store(env, lst, i), site.hi, site.hidx, regions, site.hmod
            ):
                site.why = "the lo destination may alias the hi lane"
            return j, site
    return None


def _premise(lst, i, j, site, span, blocked=None, regions=None, env=None):
    """The refusal diagnostic for lifting ``lst[i:j+1]``, or None.

    The hi lane's load moves to the word assignment, so no intervening statement
    may write it; merging the two lane stores also moves the hi store, so that
    needs no intervening read either. ``blocked`` is ``settle``'s verdict."""
    later = list(site.src[1:])  # the lo store precedes the hi lane and the step
    if _disturbs(_store(env, lst, i), later, regions):
        return "the lo destination may disturb the hi lane or the step"
    for k in range(i + 1, j):
        if k == blocked:
            return "an intervening statement changes an operand"
        if _writes(_store(env, lst, k), site.hi, span, regions, site.hidx, site.hmod):
            return "an intervening statement writes the hi lane"
    site.merge = (
        site.word
        and _rmw(lst, i, j, site.addr)
        and not any(
            _hits(_store(env, lst, k), site.hi, span, regions, site.hmod) for k in range(i + 1, j)
        )
    )
    return None


def _sid_pair(lst, i, vlo, vhi, span, regions=None, env=None):
    """``(first, second, lo address, base)`` of a SID pair store over the halves.

    Freq/pulse/cutoff are last-write-wins, so the projection keys them by register
    and the two writes may be brought together (framelog.canonical)."""
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


def _lift(lst, i, j, site, name):
    """Rewrite ``lst`` in place: one word assignment, the halves truncated off it.

    The SID pair is settled before the halves are: it may name a lane store as
    one of its two, and that store then writes the whole word once rather than a
    half here and a half it never reaches."""
    lo_st, hi_st, lv = lst[i], lst[j], _loc(name)
    word_load = site.word and (site.direct or site.merge)
    src = ("mem", site.load, 2) if word_load else FF._pack(site.src[0], site.src[1])
    word = ("op", site.op, (src, FF._zext2(site.src[2])), 2)
    if site.mask is not None:
        word = ("op", "INT_AND", (word, ("const", site.mask, 2)), 2)
    half = {i: _trunc(lv), j: _hi_byte(lv)}
    pair = () if site.at is None else site.at[:2]
    out = []
    for k, s in enumerate(lst):
        if k == i:
            out.append(("asg", name, word))
        if k in pair:
            if k == site.at[0]:
                out.append(("st", site.at[2], lv))
            continue
        if k in (i, j):
            if k == j and site.merge:
                continue
            src_s = lo_st if k == i else hi_st
            if src_s[0] == "asg":
                out.append(("asg", src_s[1], half[k]))
                continue
            out.append(("st", src_s[1], lv if (k == i and site.merge) else half[k]))
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


def apply_rung(procs, decls=()):
    """Rung (d2) in place over ``procs``; returns the per-site proofs.

    Two sweeps: cell destinations first, so a half written through a register
    still collapses into the lane store, then the local-destination form. ``decls``
    bound the index spans an aliasing test has to assume (``_span``)."""
    regions = datadecl.Regions(decls)
    used, proofs, n = _names(procs), [], 0
    for _e, params, rets, stmts in procs:
        lifted = False
        for lst in _bodies(stmts):
            done = set()
            for kinds in (("st",), ("asg",)):
                i, env = 0, frameproc.Defs(lst)
                while i < len(lst):
                    got = _match(lst, i, env, kinds, regions)
                    if got is None:
                        i += 1
                        continue
                    j, site = got
                    if (site.lo, site.hi) in done:
                        i += 1
                        continue
                    done.add((site.lo, site.hi))
                    blocked = site.settle(lst, i, j, regions, env)
                    span = _span(site.hi, site.idx, regions, site.hmod)
                    site.why = site.why or _premise(lst, i, j, site, span, blocked, regions, env)
                    site.at = (
                        None
                        if site.why
                        else _sid_pair(
                            lst, i, _half_ref(lst[i]), _half_ref(lst[j]), span, regions, env
                        )
                    )
                    site.sid = None if site.at is None else site.at[3]
                    proofs.append(site.proof())
                    if site.why:
                        i += 1
                        continue
                    while "d%d" % n in used:
                        n += 1
                    name = "d%d" % n
                    used.add(name)
                    _lift(lst, i, j, site, name)
                    env, lifted = frameproc.Defs(lst), True
                    i += 2
        if lifted:
            _drop_dead(_e, params, rets, stmts)
    return proofs
