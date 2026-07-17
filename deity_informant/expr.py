"""Entry-pure / evolved-state expression algebra for the symbolic recorder.

Nodes: ``("const", v, sz)``, ``("reg", i)``, ``("uni", n, sz)``,
``("mem", addr, sz)``, ``("cur", addr, sz, ver, fb)``, ``("op", MN, kids, sz)``.
Associative ops (``INT_ADD``/``INT_OR``/``INT_AND``/``INT_XOR``) are flat, N>=2
operands. See docs/symbolic-recorder.md for the full node/evaluation contract.
"""

from __future__ import annotations

MAX_DEPTH = 256


class ExprTooComplex(Exception):
    """A simplified expression exceeded ``MAX_DEPTH``.

    Flat associative folding keeps genuine playroutine arithmetic shallow, so
    this fires only on a runaway (e.g. a mis-driven interrupt executing RAM);
    the recorder surfaces it as a clean skip rather than an unbounded walk.
    """


def mask(sz):
    return (1 << (8 * sz)) - 1


def konst(v, sz=1):
    return ("const", v & mask(sz), sz)


def reg(i):
    return ("reg", i)


def uni(n, sz=1):
    return ("uni", n, sz)


def mem(addr, sz=1):
    return ("mem", addr, sz)


def cur(addr, sz, ver, fb):
    return ("cur", addr, sz, ver, fb)


def width(n):
    k = n[0]
    if k == "reg":
        return 1
    if k == "cur":
        return n[2]
    return n[-1]


def is_const(n):
    return n[0] == "const"


# ---- concrete op semantics: INT_ADD/OR/AND/XOR are associative, N>=2 operands --
_ASSOC = frozenset(("INT_ADD", "INT_OR", "INT_XOR", "INT_AND"))


def _fold(mn, vals, m):
    if mn == "INT_ADD":
        return sum(vals) & m
    if mn == "INT_OR":
        r = 0
        for v in vals:
            r |= v
        return r & m
    if mn == "INT_XOR":
        r = 0
        for v in vals:
            r ^= v
        return r & m
    r = m
    for v in vals:
        r &= v
    return r & m


def _apply(mn, vals, szs, out_sz):
    m = mask(out_sz)
    if mn in _ASSOC:
        return _fold(mn, vals, m)
    a = vals[0]
    b = vals[1] if len(vals) > 1 else 0
    if mn == "INT_SUB":
        return (a - b) & m
    if mn == "INT_LEFT":
        return (a << b) & m
    if mn == "INT_RIGHT":
        return (a >> b) & m
    if mn in ("INT_ZEXT", "COPY"):
        return a & m
    if mn == "INT_EQUAL":
        return 1 if a == b else 0
    if mn == "INT_NOTEQUAL":
        return 1 if a != b else 0
    if mn == "INT_LESS":
        return 1 if a < b else 0
    if mn == "INT_LESSEQUAL":
        return 1 if a <= b else 0
    if mn == "INT_CARRY":
        return 1 if (a + b) > mask(szs[0]) else 0
    raise NotImplementedError(mn)


# ---- simplification: width law + flat associative folding ---------------------
def _assoc(mn, kids, w):
    """Flat associative ``mn`` node over already-simplified ``kids``.

    Same-op same-width children are spliced in (so an accumulator is one flat
    node of bounded depth, never an N-deep chain); constants fold; the identity
    drops; a lone survivor collapses. Narrower children stay whole (width law).
    """
    m = mask(w)
    flat = []
    for c in kids:
        if c[0] == "op" and c[1] == mn and c[3] == w:
            flat.extend(c[2])
        else:
            flat.append(c)
    consts = [t[1] for t in flat if is_const(t)]
    terms = [t for t in flat if not is_const(t)]
    cc = _fold(mn, consts, m) if consts else (m if mn == "INT_AND" else 0)
    if mn == "INT_AND":
        if cc == 0:
            return konst(0, w)
        if cc != m:
            terms.append(konst(cc, w))
    elif cc != 0:
        terms.append(konst(cc, w))
    if not terms:
        return konst(cc, w)
    if len(terms) == 1:
        return terms[0]
    return ("op", mn, tuple(terms), w)


_SIMP_CACHE = {}
_DEPTH = {}


def clear_simplify_cache():
    """Drop the per-run ``simplify`` memo (call between recorder invocations)."""
    _SIMP_CACHE.clear()
    _DEPTH.clear()


def _children(n):
    k = n[0]
    if k == "op":
        return n[2]
    if k == "mem":
        return (n[1],)
    if k == "cur":
        return (n[1], n[4])
    return ()


def _dep(n):
    if n[0] in ("const", "reg", "uni"):
        return 1
    hit = _DEPTH.get(id(n))
    return hit[1] if hit is not None and hit[0] is n else 1


def _simplify(n):
    k = n[0]
    if k == "mem":
        return ("mem", simplify(n[1]), n[2])
    if k == "cur":
        return ("cur", simplify(n[1]), n[2], n[3], simplify(n[4]))
    mn, kids, sz = n[1], tuple(simplify(c) for c in n[2]), n[3]
    if all(is_const(c) for c in kids):
        return konst(_apply(mn, [c[1] for c in kids], [c[2] for c in kids], sz), sz)
    if mn in _ASSOC:
        return _assoc(mn, kids, sz)
    if mn == "INT_SUB" and is_const(kids[1]):
        return _assoc("INT_ADD", (kids[0], konst((-kids[1][1]) & mask(sz), sz)), sz)
    if mn == "INT_ZEXT" and kids[0][0] == "op" and kids[0][1] == "INT_ZEXT":
        return ("op", "INT_ZEXT", (kids[0][2][0],), sz)
    return ("op", mn, kids, sz)


def simplify(n):
    """Canonicalise ``n``; memoised by identity so an already-simplified child
    (a prior result, cached as its own fixpoint) is not re-descended."""
    if n[0] in ("const", "reg", "uni"):
        return n
    hit = _SIMP_CACHE.get(id(n))
    if hit is not None and hit[0] is n:
        return hit[1]
    r = _simplify(n)
    if r[0] not in ("const", "reg", "uni") and id(r) not in _DEPTH:
        d = 1 + max((_dep(c) for c in _children(r)), default=0)
        if d > MAX_DEPTH:
            raise ExprTooComplex(f"expression depth {d} exceeds {MAX_DEPTH}")
        _DEPTH[id(r)] = (r, d)
    _SIMP_CACHE[id(n)] = (n, r)
    if r is not n:
        _SIMP_CACHE[id(r)] = (r, r)
    return r


def op(mn, children, sz):
    return simplify(("op", mn, tuple(children), sz))


# ---- forms for the (entry, evolved) fact-identity pair -----------------------
_HASCUR_CACHE = {}
_ENTRY_CACHE = {}


def clear_form_caches():
    """Drop the per-run ``_has_cur``/``to_entry`` memos."""
    _HASCUR_CACHE.clear()
    _ENTRY_CACHE.clear()


def _has_cur(n):
    """Whether ``n`` contains a ``cur`` leaf (identity-memoised)."""
    hit = _HASCUR_CACHE.get(id(n))
    if hit is not None and hit[0] is n:
        return hit[1]
    k = n[0]
    if k == "cur":
        r = True
    elif k == "mem":
        r = _has_cur(n[1])
    elif k == "op":
        r = any(_has_cur(c) for c in n[2])
    else:
        r = False
    _HASCUR_CACHE[id(n)] = (n, r)
    return r


def to_entry(n):
    """Entry-pure form: every ``cur`` leaf replaced by its fallback.

    An already-entry-pure subtree is returned unchanged, so a re-stored cell's
    fallback stays a shared DAG node instead of being re-materialised each store.
    """
    if not _has_cur(n):
        return n
    hit = _ENTRY_CACHE.get(id(n))
    if hit is not None and hit[0] is n:
        return hit[1]
    k = n[0]
    if k == "cur":
        r = to_entry(n[4])
    elif k == "mem":
        r = ("mem", to_entry(n[1]), n[2])
    else:
        r = ("op", n[1], tuple(to_entry(c) for c in n[2]), n[3])
    _ENTRY_CACHE[id(n)] = (n, r)
    return r


def to_evolved(n, ver, memo=None):
    """Evolved form: a ``cur`` leaf whose cell advanced past its load-version
    demotes to its entry-pure fallback. No-``cur`` subtrees are shared unchanged;
    ``memo`` shares each node once per call (``ver`` is fixed for its duration)."""
    if not _has_cur(n):
        return n
    if memo is None:
        memo = {}
    hit = memo.get(id(n))
    if hit is not None and hit[0] is n:
        return hit[1]
    k = n[0]
    if k == "cur":
        addr = n[1]
        a = addr[1] if is_const(addr) else None
        if a is None or ver.get(a, 0) != n[3]:
            r = to_entry(n[4])
        else:
            r = ("cur", to_evolved(n[1], ver, memo), n[2], n[3], n[4])
    elif k == "mem":
        r = ("mem", to_evolved(n[1], ver, memo), n[2])
    else:
        r = ("op", n[1], tuple(to_evolved(c, ver, memo) for c in n[2]), n[3])
    memo[id(n)] = (n, r)
    return r


# ---- reference re-evaluator (normative semantics) ----------------------------
def evaluate(n, entry_mem, entry_reg, image, uni_vals, memo=None):
    """Evaluate ``n``; ``memo`` shares subtree values within one call (the
    entry snapshot and ``image`` are fixed for its duration)."""
    if memo is None:
        memo = {}
    k = n[0]
    if k == "const":
        return n[1]
    if k == "reg":
        return entry_reg[n[1]] & 0xFF
    if k == "uni":
        return uni_vals[n[1]] & mask(n[2])
    key = id(n)
    hit = memo.get(key)
    if hit is not None and hit[0] is n:
        return hit[1]
    if k == "mem":
        r = _load(entry_mem, evaluate(n[1], entry_mem, entry_reg, image, uni_vals, memo), n[2])
    elif k == "cur":
        r = _load(image, evaluate(n[1], entry_mem, entry_reg, image, uni_vals, memo), n[2])
    else:
        vals = [evaluate(c, entry_mem, entry_reg, image, uni_vals, memo) for c in n[2]]
        r = _apply(n[1], vals, [width(c) for c in n[2]], n[3])
    memo[key] = (n, r)
    return r


def _load(buf, addr, sz):
    v = 0
    for i in range(sz):
        v |= buf[(addr + i) & 0xFFFF] << (8 * i)
    return v
