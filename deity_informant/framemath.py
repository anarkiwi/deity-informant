"""framemath: rung (d2), 16-bit arithmetic lifting (docs/frameprog.md 4).

An 8-bit add/sub on the lo lane plus the carry (borrow) it propagates into the hi
lane is one 16-bit add/sub. The carry link is the evidence, and it is local: it
exists only where the two lanes are halves of one quantity.
"""

from __future__ import annotations

from . import datadecl
from . import expr as E
from . import framefuse as FF
from . import frameproc
from . import grammar as G
from . import sidprog
from . import streams as ST
from .structured import Proof

_ADD, _SUB = "INT_ADD", "INT_SUB"


def _loc(name):
    return ("loc", name, 2)


def _trunc(n):
    return ("op", "COPY", (n,), 1)


def _hi_byte(n):
    return _trunc(("op", "INT_RIGHT", (n, ("const", 8, 1)), 2))


def _resolve(n, env, seen=None):
    """Follow ``loc`` definitions to the first non-``loc`` expression."""
    seen = seen or set()
    while n[0] == "loc" and n[1] in env and n[1] not in seen:
        seen.add(n[1])
        n = env[n[1]]
    return n


def _same(a, b, env):
    """Same value, whatever width it was widened to (the borrow compare zero-extends)."""
    if a == b:
        return True
    a, b = _resolve(a, env), _resolve(b, env)
    if E.is_const(a) and E.is_const(b):
        return a[1] == b[1]
    return a == b


def _update(val, env):
    """``(lane value, step, op)`` when ``val`` is a byte add/sub, else None."""
    r = _resolve(val, env)
    if r[0] != "op" or r[3] != 1:
        return None
    if r[1] == _ADD and len(r[2]) == 2:
        return (r[2][0], r[2][1], _ADD)
    if r[1] == _SUB:
        return (r[2][0], r[2][1], _SUB)
    return None


def _carry_over(n, env):
    """``(a, b)`` a carry term is over; ``carry(x, $00)`` is 0 and drops out."""
    r = _resolve(n, env)
    if r[0] != "op":
        return None
    if r[1] == "INT_CARRY":
        return None if E.is_const(r[2][1]) and r[2][1][1] == 0 else (r[2][0], r[2][1])
    if r[1] == "INT_OR":
        live = [g for g in (_carry_over(c, env) for c in r[2]) if g is not None]
        return live[0] if len(live) == 1 else None
    return None


def _borrow_over(n, env):
    """``(a, b)`` a borrow term is over: ``$01 - (zext(b) <= zext(a))`` or ``a < b``."""
    r = _resolve(n, env)
    if r[0] != "op":
        return None
    if r[1] == "INT_LESS":
        return (r[2][0], r[2][1])
    if r[1] == _SUB and r[2][0] == ("const", 1, 1):
        c = _resolve(r[2][1], env)
        if c[0] == "op" and c[1] == "INT_LESSEQUAL":
            return (ST._strip_zext(c[2][1]), ST._strip_zext(c[2][0]))
    return None


def _inline(n, defs):
    """Substitute ``defs`` (definitions inside the interval) into ``n``."""
    if n[0] == "loc" and n[1] in defs:
        return _inline(defs[n[1]], {k: v for k, v in defs.items() if k != n[1]})
    if n[0] == "op":
        return (n[0], n[1], tuple(_inline(c, defs) for c in n[2]), n[3])
    if n[0] == "mem":
        return ("mem", _inline(n[1], defs), n[2])
    return n


def _volatile(n):
    """True where ``n`` may load a volatile source, so its value cannot be dropped."""
    return any(FF._may_read(n, c) for c in sidprog._VOLS)


def _mem_refs(n):
    """``((base, index), width)`` of every memory reference under ``n``."""
    out, stack = [], [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            out.append((FF._addr_split(x[1]), x[2]))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def _span(base, idx, regions):
    """Tightest sound span for an indexed address at ``base``: the ONE span rule.

    An index reaches no further than the declaration holding its base, since the
    lifted program indexes that datum; with no declaration the register width is
    all that bounds it."""
    if idx is None:
        return 0
    full = E.mask(FF._w(idx))
    avail = 0 if base is None or regions is None else regions.avail(base)
    return min(full, avail - 1) if avail > 0 else full


def _reads(exprs, base, span, regions):
    """True where evaluating ``exprs`` may load a cell of ``[base, base + span]``."""
    for (rb, ri), rw in (r for x in exprs for r in _mem_refs(x)):
        if rb is None or not (rb + _span(rb, ri, regions) + rw - 1 < base or rb > base + span):
            return True
    return False


def _clobbers(stmt, exprs, regions):
    """True where ``stmt`` may change the value of any of ``exprs``."""
    if stmt[0] == "asg":
        return any(stmt[1] in frameproc._locset(x) for x in exprs)
    if stmt[0] != "st":
        return True
    base, idx = FF._addr_split(stmt[1])
    if base is None:
        return True
    return _reads(exprs, base, _span(base, idx, regions), regions)


def _disturbs(stmt, exprs, regions):
    """The store may change a value ``exprs`` read.

    Index expressions may only be compared when the read is a direct load here;
    one captured earlier can be structurally equal yet hold a stale index."""
    if stmt[0] != "st":
        return False
    b, bi = FF._addr_split(stmt[1])
    if b is None:
        return True
    bw = G.store_width(stmt[2])
    for (rb, ri), rw in (r for x in exprs for r in _mem_refs(x)):
        if rb is None:
            return True
        if ri == bi:
            if not (b + bw - 1 < rb or b > rb + rw - 1):
                return True
        else:
            sb, sr = _span(b, bi, regions), _span(rb, ri, regions)
            if not (b + sb + bw - 1 < rb or b > rb + sr + rw - 1):
                return True
    return False


def _may_disturb(stmt, base, idx, regions):
    """The store may write the lane at ``(base, idx)``; exact when the index matches."""
    if stmt[0] != "st":
        return False
    b, i2 = FF._addr_split(stmt[1])
    if b is None:
        return True
    if i2 == idx:
        return b == base
    return not (b + _span(b, i2, regions) < base or b > base + _span(base, idx, regions))


def _writes(stmt, base, span, regions):
    """True where ``stmt`` may store into the lane spanning ``base``."""
    if stmt[0] not in ("asg", "st"):
        return True
    if stmt[0] != "st":
        return False
    b, idx = FF._addr_split(stmt[1])
    if b is None:
        return True
    return not (b + _span(b, idx, regions) < base or b > base + span)


def _hits(stmt, base, span, regions):
    """True where ``stmt`` may read or write a cell of the lane at ``base``."""
    return _writes(stmt, base, span, regions) or _reads(
        frameproc._stmt_exprs(stmt), base, span, regions
    )


class _Site:
    """One byte-wise 16-bit update, its premise counts and its refusal."""

    __slots__ = ("lo", "hi", "op", "word", "direct", "merge", "mask", "why", "sid", "at")

    def __init__(self, lo, hi, op):
        self.lo, self.hi, self.op = lo, hi, op
        self.word = lo is not None and hi == lo + 1
        self.direct = False
        self.merge = False
        self.mask = None
        self.why = None
        self.sid = None
        self.at = None

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


def _load_addr(n, env):
    """The address ``n`` loads one byte from, else None."""
    r = _resolve(n, env)
    return r[1] if r[0] == "mem" and r[2] == 1 else None


def _peel_mask(val, env):
    """``(update, byte mask)`` where the hi half is masked (a 12-bit register)."""
    r = _resolve(val, env)
    if r[0] == "op" and r[1] == "INT_AND" and len(r[2]) == 2 and r[3] == 1:
        for a, b in (r[2], r[2][::-1]):
            if E.is_const(b) and _update(a, env) is not None:
                return a, b[1]
    return val, None


def _mergeable(dst_lo, dst_hi, src_lo, src_hi):
    """Both halves go straight back to the lanes they came from.

    Adjacency is the caller's ``word`` test: two lanes three bytes apart are one
    quantity but not one word store."""
    return dst_lo is not None and dst_lo == src_lo and dst_hi == src_hi


def _lo_agrees(lo, op, step, env):
    """The lo lane runs the same update the hi lane carries from.

    ``expr`` folds ``SBC #k`` to an add of ``-k``, so a borrow's lo half may be an
    add of the byte complement; that is still the 16-bit subtract of ``k``."""
    if lo[2] == op:
        return _same(lo[1], step, env)
    if lo[2] == _ADD and op == _SUB and E.is_const(lo[1]) and E.is_const(step):
        return (lo[1][1] + step[1]) % 0x100 == 0
    return False


def _half_ref(s):
    """How later statements refer to the half this statement produced."""
    return ("loc", s[1]) if s[0] == "asg" else s[2]


def _match(lst, i, env, kinds=("st", "asg"), regions=None):
    """``(j, site, parts)`` for the update ``lst[i]`` opens, else None.

    The **sources** decide the lift: two byte lanes linked by a carry are one
    16-bit quantity wherever their halves are then written. The destinations
    decide only whether the two writes collapse into one ``u16`` store."""
    s = lst[i]
    if s[0] not in kinds:
        return None
    lo = _update(_value(s), env)
    if lo is None:
        return None
    a_lo = _load_addr(lo[0], env)
    if a_lo is None:
        return None
    env = {**env, s[1]: s[2]} if s[0] == "asg" else env
    for j in range(i + 1, len(lst)):
        t = lst[j]
        if t[0] not in ("st", "asg"):
            return None
        if t[0] not in kinds:
            env = {**env, t[1]: t[2]} if t[0] == "asg" else env
            continue
        val, mask = _peel_mask(_value(t), env)
        hi = _update(val, env)
        over = None if hi is None else (_carry_over if hi[2] == _ADD else _borrow_over)(hi[1], env)
        a_hi = None if hi is None else _load_addr(hi[0], env)
        if over is None or a_hi is None or not _lo_agrees(lo, hi[2], over[1], env):
            if t[0] == "asg":
                env = {**env, t[1]: t[2]}
            continue
        if not _same(over[0], lo[0], env):
            if t[0] == "asg":
                env = {**env, t[1]: t[2]}
            continue
        lo_base, lo_idx = FF._addr_split(a_lo)
        hi_base, hi_idx = FF._addr_split(a_hi)
        site = _Site(lo_base, hi_base, hi[2])
        site.mask = mask
        site.direct = lo[0][0] == "mem" and hi[0][0] == "mem"
        site.merge = site.word and _mergeable(
            s[1] if s[0] == "st" else None, t[1] if t[0] == "st" else None, a_lo, a_hi
        )
        if (
            lo_base is not None
            and hi_base is not None
            and _may_disturb(s, hi_base, hi_idx, regions)
        ):
            site.why = "the lo destination may alias the hi lane"
        elif lo_base is None or hi_base is None:
            site.why = "a lane address is not a const base plus index"
        elif hi_idx != lo_idx:
            site.why = "the two lanes are indexed differently"
        return j, site, (lo[0], hi[0], over[1], lo_idx, a_lo)
    return None


def _premise(lst, i, j, parts, site, span, regions=None):
    """The refusal diagnostic for lifting ``lst[i:j+1]``, or None.

    The hi lane's load moves to the word assignment, so no intervening statement
    may write it; merging the two lane stores also moves the hi store, so that
    needs no intervening read either."""
    watch = [x for x in parts[:3] if x is not None]
    later = [x for x in parts[1:3] if x is not None]  # the lo store precedes them in the original
    if _disturbs(lst[i], later, regions):
        return "the lo destination may disturb the hi lane or the step"
    for k in range(i + 1, j):
        if _clobbers(lst[k], watch, regions):
            return "an intervening statement changes an operand"
        if _writes(lst[k], site.hi, span, regions):
            return "an intervening statement writes the hi lane"
    site.merge = site.merge and not any(
        _hits(lst[k], site.hi, span, regions) for k in range(i + 1, j)
    )
    return None


def _sid_pair(lst, i, vlo, vhi, span, regions=None):
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
    if any(_hits(lst[k], at["lo"][1], span + 1, regions) for k in range(a + 1, b)):
        return None
    return a, b, at["lo"][2], at["lo"][1]


def _lift(lst, i, j, parts, site, name):
    """Rewrite ``lst`` in place: one word assignment, the halves truncated off it."""
    lo_st, hi_st, lv = lst[i], lst[j], _loc(name)
    word_load = site.word and (site.direct or site.merge)
    src = ("mem", parts[4], 2) if word_load else FF._pack(parts[0], parts[1])
    word = ("op", site.op, (src, FF._zext2(parts[2])), 2)
    if site.mask is not None:
        word = ("op", "INT_AND", (word, ("const", (site.mask << 8) | 0xFF, 2)), 2)
    half = {i: _trunc(lv), j: _hi_byte(lv)}
    out = []
    for k, s in enumerate(lst):
        if k == i:
            out.append(("asg", name, word))
        if k in (i, j):
            if k == j and site.merge:
                continue
            src_s = lo_st if k == i else hi_st
            if src_s[0] == "asg":
                out.append(("asg", src_s[1], half[k]))
                continue
            out.append(("st", src_s[1], lv if (k == i and site.merge) else half[k]))
            continue
        if site.at is not None and k in site.at[:2]:
            if k == site.at[0]:
                out.append(("st", site.at[2], lv))
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
                i = 0
                while i < len(lst):
                    env = {s[1]: s[2] for s in lst[:i] if s[0] == "asg"}
                    got = _match(lst, i, env, kinds, regions)
                    if got is None:
                        i += 1
                        continue
                    j, site, parts = got
                    if (site.lo, site.hi) in done:
                        i += 1
                        continue
                    done.add((site.lo, site.hi))
                    inner = {s[1]: s[2] for s in lst[i + 1 : j] if s[0] == "asg"}
                    parts = tuple(_inline(p, inner) if p is not None else None for p in parts)
                    span = _span(site.hi, parts[3], regions)
                    site.why = site.why or _premise(lst, i, j, parts, site, span, regions)
                    site.at = (
                        None
                        if site.why
                        else _sid_pair(lst, i, _half_ref(lst[i]), _half_ref(lst[j]), span, regions)
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
                    _lift(lst, i, j, parts, site, name)
                    lifted = True
                    i += 2
        if lifted:
            _drop_dead(_e, params, rets, stmts)
    return proofs
