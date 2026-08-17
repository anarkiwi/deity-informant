"""S6 -- 16-bit views: a carry chain across two cells is one word operation.

The low byte's add/sub produces a carry that only the high byte's add/sub of the
matching cell consumes, so the two statements are one assignment over a named
``lo|hi`` view; the residue (a carry a third byte reads) keeps the flag's name.
"""

from __future__ import annotations

from dataclasses import dataclass

from .idioms import fold
from .ir import Bin, Const, Let, Load, Store, Var, succs
from .irwalk import apply_stmt, apply_term, sub_expr

DEPTH = 8


@dataclass(frozen=True, slots=True)
class R16:
    """A 16-bit read of the ``lo``/``hi`` region pair addressed by ``a``."""

    lo: int
    hi: int
    a: object


@dataclass(slots=True)
class W16:
    """A 16-bit assignment of ``e`` to the ``lo``/``hi`` pair addressed by ``a``."""

    lo: int
    hi: int
    a: object
    e: object
    src: int = 0


def uses16(e, out):
    """Collect the names ``e`` reads, ``R16`` included."""
    t = type(e)
    if t is Var:
        out.add(e.n)
    elif t is Bin:
        uses16(e.a, out)
        uses16(e.b, out)
    elif t is Load or t is R16:
        uses16(e.a, out)
    return out


def _defs(proc):
    out = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            if type(s) is Let:
                out.setdefault(s.n, []).append((lbl, i, s.e))
    return {n: v[0] for n, v in out.items() if len(v) == 1}


def _expand(e, defs, depth=DEPTH):
    """``e`` with every single-definition name replaced by its value."""
    t = type(e)
    if t is Var and depth and e.n in defs:
        return _expand(defs[e.n][2], defs, depth - 1)
    if t is Bin:
        return Bin(e.op, _expand(e.a, defs, depth), _expand(e.b, defs, depth), e.w)
    if t is Load:
        return Load(e.cls, _expand(e.a, defs, depth), e.w, e.lo, e.hi, e.r)
    return e


def _nofold(e):
    """``carry(x, 0)`` is zero; then the ordinary algebraic folding."""
    if type(e) is Bin and e.op == "carry" and type(e.b) is Const and not e.b.v:
        return Const(0, 1)
    return fold(e)


def _norm(e):
    return sub_expr(e, _nofold)


def _same(a, b):
    """Structural equality of two expressions, ignoring the printed width."""
    if type(a) is not type(b):
        return False
    t = type(a)
    if t is Const:
        return a.v == b.v
    if t is Var:
        return a.n == b.n
    if t is Load:
        return a.cls == b.cls and a.r == b.r and _same(a.a, b.a)
    if t is Bin:
        return a.op == b.op and _same(a.a, b.a) and _same(a.b, b.b)
    return a == b


def _split(a):
    """``(base, index)`` of an address: a constant, or a constant plus an index."""
    if type(a) is Const:
        return a.v, None
    if type(a) is Bin and a.op == "+":
        for k, i in ((a.a, a.b), (a.b, a.a)):
            if type(k) is Const:
                return k.v, i
    return None, None


def _pairs(al, ah):
    """True when two addresses differ only in their constant base."""
    bl, il = _split(al)
    bh, ih = _split(ah)
    if bl is None or bh is None or bl == bh:
        return False
    return (il is None and ih is None) or (il is not None and ih is not None and _same(il, ih))


def _parses(e):
    """Every ``(op, X, Y, carry in)`` reading of an 8-bit add or subtract."""
    out = []
    if type(e) is not Bin:
        return out
    if e.op == "+":
        if type(e.a) is Bin and e.a.op == "+":
            out.append(("+", e.a.a, e.a.b, e.b))
        out.append(("+", e.a, e.b, Const(0)))
    if e.op == "-":
        if type(e.b) is Bin and e.b.op == "+":
            y, c = e.b.a, e.b.b
            if type(c) is Bin and c.op == "-" and type(c.a) is Const and c.a.v == 1:
                out.append(("-", e.a, y, c.b))
            elif type(c) is Const and c.v == 1:
                out.append(("-", e.a, y, Const(0)))
        out.append(("-", e.a, e.b, Const(1)))
    return out


def _carryof(op, x, y, cin):
    """The carry (or the not-borrow) the 6510 leaves after this byte."""
    if op == "+":
        return Bin("|", Bin("carry", x, y, 1), Bin("carry", Bin("+", x, y, 1), cin, 1), 1)
    return Bin("<=", Bin("+", y, Bin("-", Const(1), cin, 1), 1), x, 1)


def _operand(lo, hi):
    """The 16-bit value a pair of byte operands reads, or ``None``."""
    if type(lo) is Const and type(hi) is Const:
        return Const(lo.v | (hi.v << 8), 2)
    if type(lo) is Load and type(hi) is Load and lo.r >= 0 and hi.r >= 0 and _pairs(lo.a, hi.a):
        return R16(lo.r, hi.r, lo.a)
    return None


def _value(op, x, y, cin):
    """The 16-bit right-hand side of a folded chain."""
    if type(cin) is Const and cin.v == (0 if op == "+" else 1):
        return Bin(op, x, y, 2)
    if op == "+":
        return Bin("+", Bin("+", x, y, 2), cin, 2)
    return Bin("-", x, Bin("+", y, Bin("-", Const(1), cin, 1), 2), 2)


def _match(lo, hi, defs):
    """``(16-bit value, carry expression)`` when ``hi`` continues ``lo``'s chain."""
    for opl, xl, yl, cl in _parses(_expand(lo, defs)):
        want = _norm(_carryof(opl, xl, yl, cl))
        for oph, xh, yh, ch in _parses(_expand(hi, defs)):
            if oph != opl or not _same(_norm(ch), want):
                continue
            for a, b in ((xh, yh), (yh, xh)) if opl == "+" else ((xh, yh),):
                x16, y16 = _operand(xl, a), _operand(yl, b)
                if x16 is not None and y16 is not None:
                    return _value(opl, x16, y16, cl), _norm(_carryof(oph, xh, yh, ch))
    return None


def _sites(proc, defs, lbl, i, e, seen=()):
    """The definitions of a stored value, copies followed, as ``(block, index, value)``."""
    if type(e) is not Var or e.n in seen:
        return [(lbl, i, e)]
    out = []
    for l2, b in proc.blocks.items():
        for j, x in enumerate(b.stmts):
            if type(x) is Let and x.n == e.n:
                out += _sites(proc, defs, l2, j, x.e, seen + (e.n,))
    return out or [(lbl, i, _expand(e, defs))]


def _local(proc, defs, lbl, at):
    """``defs`` plus the values this block defines before ``at``."""
    out = dict(defs)
    for i, x in enumerate(proc.blocks[lbl].stmts[:at]):
        if type(x) is Let:
            out[x.n] = (lbl, i, x.e)
    return out


def _plan(proc, defs, lbl, i, s, taken):
    """``([(block, put, drop, W16, carry, value)], every definition folded)``."""
    out, whole = [], True
    for dlbl, didx, expr in _sites(proc, defs, lbl, i, s.v):
        local = _local(proc, defs, dlbl, didx)
        hit = None
        for j in range(didx - 1, -1, -1):
            lo = proc.blocks[dlbl].stmts[j]
            if type(lo) is not Store or lo.w != 1 or lo.r < 0 or (dlbl, j) in taken:
                continue
            if lo.a == s.a or not _pairs(lo.a, s.a) or _crosses(proc, dlbl, j, didx, lo.r, s.r):
                continue
            got = _match(lo.v, expr, local)
            if got is not None:
                hit = (dlbl, didx, j, W16(lo.r, s.r, lo.a, got[0], lo.src), got[1], expr)
                break
        if hit is None:
            whole = False
        else:
            out.append(hit)
    return (out, whole) if out else None


def _crosses(proc, lbl, i, j, rlo, rhi):
    """True when the two halves are separated by a call or an access to the pair."""
    for x in proc.blocks[lbl].stmts[i + 1 : j]:
        if type(x).__name__ == "Call":
            return True
        if type(x) is Store and (x.r in (rlo, rhi) or x.r < 0):
            return True
        if any(y.r == rlo for y in _walk16(x)):
            return True
    return False


def _walk16(s):
    for e in (getattr(s, "e", None), getattr(s, "a", None), getattr(s, "v", None)):
        if e is not None:
            for x in _walk(e):
                if type(x) is Load:
                    yield x


def _carry_defs(proc, carries):
    """``(names defined only by these carries, the positions of every such def)``."""
    defs, seen, where = _defs(proc), {}, []
    for lbl, b in proc.blocks.items():
        local = _local(proc, defs, lbl, len(b.stmts))
        for j, x in enumerate(b.stmts):
            if type(x) is not Let:
                continue
            hit = any(_same(_norm(_expand(x.e, local)), c) for c in carries)
            a, n = seen.get(x.n, (0, 0))
            seen[x.n] = (a + hit, n + 1)
            if hit:
                where.append((lbl, j, x.n))
    return {n for n, (a, t) in seen.items() if a == t}, where


def _before(proc, sites):
    """The blocks the entry reaches without passing a fold site."""
    out, work = set(), [proc.entry]
    while work:
        cur = work.pop()
        if cur in out or cur not in proc.blocks:
            continue
        out.add(cur)
        if cur not in sites:
            work.extend(succs(proc.blocks[cur].term))
    return out - sites


def _rewrite(proc, sites, fn):
    """Apply ``fn`` to every expression a fold site dominates."""
    pre = _before(proc, set(sites))
    for lbl, b in proc.blocks.items():
        if lbl in pre:
            continue
        start = sites.get(lbl, 0)
        for s in b.stmts[start:]:
            apply_stmt(s, fn)
        apply_term(b.term, fn)


def _subst(hi, carries, vals, ren):
    """Uses of the stored byte read the cell; the carry keeps the flag's name."""
    load = Load(hi.cls, hi.a, 1, hi.lo, hi.hi, hi.r)

    def fn(e):
        if type(e) is Var and e.n in ren:
            return Var(ren[e.n])
        if any(_same(e, c) for c in carries):
            return Var("$carry")
        return load if any(_same(e, v) for v in vals) else e

    return fn


def fold16(prog, names=None):
    """Fold every 8-bit carry chain into a 16-bit statement; returns the pairs."""
    pairs = []
    for proc in prog.procs.values():
        taken, drop, put = set(), {}, {}
        stores = [
            (lbl, i)
            for lbl, b in proc.blocks.items()
            for i, s in enumerate(b.stmts)
            if type(s) is Store and s.w == 1 and s.r >= 0
        ]
        for lbl, i in stores:
            s = proc.blocks[lbl].stmts[i]
            got = None if (lbl, i) in taken else _plan(proc, _defs(proc), lbl, i, s, taken)
            if got is None:
                continue
            plan, whole = got
            taken |= {(lbl, i)} | {(l, k) for l, _p, k, _w, _c, _v in plan}
            taken |= {(l, p) for l, p, _k, _w, _c, _v in plan}
            for l, p, k, w, _c, _v in plan:
                put[(l, p)] = [w] if whole else [w, proc.blocks[l].stmts[p]]
                drop.setdefault(l, set()).add(k)
            if whole and (lbl, i) not in put:
                drop.setdefault(lbl, set()).add(i)
            _finish(proc, s, plan, drop, taken, whole)
            pairs += [(w.lo, w.hi, w.e) for _l, _p, _k, w, _c, _v in plan]
        for lbl, b in proc.blocks.items():
            gone = drop.get(lbl, ())
            out = []
            for j, x in enumerate(b.stmts):
                if j not in gone:
                    out += put.get((lbl, j), [x])
            b.stmts[:] = out
    if names is not None:
        _name(prog, names, pairs)
    return pairs


def _finish(proc, s, plan, drop, taken, whole=True):
    """Rename what the fold subsumes: the stored byte and its carry out."""
    sites = {l: p for l, p, _k, _w, _c, _v in plan}
    carries = [c for _l, _p, _k, _w, c, _v in plan]
    vals = [v for _l, _p, _k, _w, _c, v in plan] + ([s.v] if whole else [])
    whole, where = _carry_defs(proc, carries)
    ren = {n: "$carry" for n in whole}
    _rewrite(proc, sites, _subst(s, carries, vals, ren))
    for lbl, j, n in where:
        if n in ren:
            if (lbl, j) not in taken:
                drop.setdefault(lbl, set()).add(j)
        else:
            proc.blocks[lbl].stmts[j].e = Var("$carry")


def _feeds(prog, want=range(0xD415, 0xD419)):
    """The regions whose value reaches one of the filter registers."""
    out = set()
    for proc in prog.procs.values():
        defs = {}
        for b in proc.blocks.values():
            for x in b.stmts:
                if type(x) is Let:
                    defs.setdefault(x.n, []).append(x.e)
        for b in proc.blocks.values():
            for x in b.stmts:
                if type(x) is Store and x.cls == "io" and type(x.a) is Const and x.a.v in want:
                    _sources(x.v, defs, out, set())
    return out


def _sources(e, defs, out, seen, depth=DEPTH):
    for x in _walk(e):
        if type(x) is Load:
            out.add(x.r)
        elif type(x) is Var and x.n not in seen and depth:
            seen.add(x.n)
            for d in defs.get(x.n, ()):
                _sources(d, defs, out, seen, depth - 1)
    return out


def _walk(e):
    yield e
    t = type(e)
    if t is Bin:
        yield from _walk(e.a)
        yield from _walk(e.b)
    elif t is Load or t is R16:
        yield from _walk(e.a)


def _name(prog, names, pairs):
    """Name every folded pair, its halves, and the group its data flows to."""
    rgn = {r.id: r for r in prog.storage}
    filt, seen, kind = _feeds(prog), {}, {}
    for lo, hi, e in pairs:
        ops = [(x.lo, x.hi) for x in _r16s(e)]
        for p in [(lo, hi)] + ops:
            seen.setdefault(p, (lo, hi))
            kind.setdefault(p, "operand" if p != (lo, hi) else "")
        if (lo, hi) in ops:
            kind[(lo, hi)] = "acc"
    for p, dest in sorted(seen.items()):
        if p == dest and (p[0] in filt or p[1] in filt) and not set(p) & set(names.view):
            names.u16group[p] = "filter"
    for (lo, hi), dest in sorted(seen.items()):
        if (lo, hi) in names.u16 or lo not in rgn or hi not in rgn:
            continue
        base = _basename(names, rgn, lo, hi, kind.get((lo, hi), ""))
        group = names.u16group.get(dest, "")
        want = base if not group else "%s.%s" % (group, base)
        names.u16[(lo, hi)] = _uniq(names, want, (lo, hi))
        names.u16group[(lo, hi)] = group
        for rid, half in ((lo, "lo"), (hi, "hi")) if lo != hi else ():
            cur = names.region.get(rid, "")
            if rid not in names.view and cur[:-2].lower() != base.lower() + "_":
                names.region[rid] = "%s_%s" % (names.u16[(lo, hi)], half)


def _r16s(e):
    if type(e) is R16:
        return [e]
    return _r16s(e.a) + _r16s(e.b) if type(e) is Bin else []


def _basename(names, rgn, lo, hi, kind):
    """``freq`` from ``freq_lo``/``freq_hi``, else the role, else the address."""
    a, b = names.region.get(lo, ""), names.region.get(hi, "")
    if lo == hi and a:
        return a
    low = (a[:-2].lower(), b[:-2].lower(), a[-2:].lower(), b[-2:].lower())
    if a and b and low[0] == low[1] and low[2:] == ("lo", "hi"):
        return a[:-3] if a[-3] == "_" else a[:-2]
    if kind == "acc" or names.role.get(lo) == "acc":
        return "acc"
    if kind == "operand" and rgn[lo].kind == "const":
        return a or "T%04X" % rgn[lo].base
    if kind == "operand" and rgn[lo].kind == "init_constant":
        return "base"
    return "step" if kind == "operand" else "w%04X" % rgn[lo].base


def _uniq(names, want, own=()):
    taken = (set(names.region.values()) | set(names.u16.values())) - {
        names.region.get(r) for r in own
    }
    out, i = want, 2
    while out in taken:
        out, i = "%s_%d" % (want, i), i + 1
    return out
