"""Entry-pure / evolved-state expression algebra for the symbolic recorder.

Nodes: ``("const", v, sz)``, ``("reg", i)``, ``("uni", n, sz)``,
``("mem", addr, sz)``, ``("cur", addr, sz, ver, fb)``, ``("op", MN, kids, sz)``.
See docs/symbolic-recorder.md for the full node and evaluation contract.
"""

from __future__ import annotations

_ADD = ("INT_ADD", "INT_SUB")


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


# ---- concrete op semantics (mirrors vm._emit_line; drives folding + eval) -----
def _apply(mn, vals, szs, out_sz):
    a = vals[0]
    b = vals[1] if len(vals) > 1 else 0
    m = mask(out_sz)
    if mn == "INT_ADD":
        return (a + b) & m
    if mn == "INT_SUB":
        return (a - b) & m
    if mn == "INT_AND":
        return (a & b) & m
    if mn == "INT_OR":
        return (a | b) & m
    if mn == "INT_XOR":
        return (a ^ b) & m
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


# ---- simplification: width law + subtraction law + additive flattening -------
def _flatten(n, w):
    """Return ``(const_acc, terms)`` for an additive node of width ``w``.

    Only same-width ``INT_ADD``/``INT_SUB``-by-const absorb; narrower children
    stay whole so their own-width wrap is preserved (the width law).
    """
    if is_const(n):
        return n[1], []
    if n[0] == "op" and n[1] in _ADD and n[3] == w:
        c0, c1 = n[2]
        ac, terms = _flatten(c0, w) if width(c0) == w else (0, [c0])
        if n[1] == "INT_ADD":
            bc, bt = _flatten(c1, w) if width(c1) == w else (0, [c1])
            return (ac + bc) & mask(w), terms + bt
        if is_const(c1):
            return (ac - c1[1]) & mask(w), terms
        return 0, [n]
    return 0, [n]


def _rebuild(c, terms, w):
    terms = sorted(terms, key=repr)
    if not terms:
        return konst(c, w)
    node = terms[0]
    for t in terms[1:]:
        node = ("op", "INT_ADD", (node, t), w)
    if c & mask(w):
        node = ("op", "INT_ADD", (node, konst(c, w)), w)
    return node


def simplify(n):
    k = n[0]
    if k in ("const", "reg", "uni"):
        return n
    if k == "mem":
        return ("mem", simplify(n[1]), n[2])
    if k == "cur":
        return ("cur", simplify(n[1]), n[2], n[3], simplify(n[4]))
    mn, kids, sz = n[1], tuple(simplify(c) for c in n[2]), n[3]
    if all(is_const(c) for c in kids):
        return konst(_apply(mn, [c[1] for c in kids], [c[2] for c in kids], sz), sz)
    if mn in _ADD:
        c, terms = _flatten(("op", mn, kids, sz), sz)
        return _rebuild(c, terms, sz)
    if mn == "INT_ZEXT" and kids[0][0] == "op" and kids[0][1] == "INT_ZEXT":
        return ("op", "INT_ZEXT", (kids[0][2][0],), sz)
    return ("op", mn, kids, sz)


def op(mn, children, sz):
    return simplify(("op", mn, tuple(children), sz))


# ---- forms for the (entry, evolved) fact-identity pair -----------------------
def to_entry(n):
    """Entry-pure form: every ``cur`` leaf replaced by its fallback."""
    k = n[0]
    if k == "cur":
        return to_entry(n[4])
    if k == "mem":
        return ("mem", to_entry(n[1]), n[2])
    if k == "op":
        return ("op", n[1], tuple(to_entry(c) for c in n[2]), n[3])
    return n


def to_evolved(n, ver):
    """Evolved form: a ``cur`` leaf whose cell advanced past its load-version
    demotes to its entry-pure fallback."""
    k = n[0]
    if k == "cur":
        addr = n[1]
        a = addr[1] if is_const(addr) else None
        if a is None or ver.get(a, 0) != n[3]:
            return to_entry(n[4])
        return ("cur", to_evolved(n[1], ver), n[2], n[3], n[4])
    if k == "mem":
        return ("mem", to_evolved(n[1], ver), n[2])
    if k == "op":
        return ("op", n[1], tuple(to_evolved(c, ver) for c in n[2]), n[3])
    return n


# ---- reference re-evaluator (normative semantics) ----------------------------
def evaluate(n, entry_mem, entry_reg, image, uni_vals):
    k = n[0]
    if k == "const":
        return n[1]
    if k == "reg":
        return entry_reg[n[1]] & 0xFF
    if k == "uni":
        return uni_vals[n[1]] & mask(n[2])
    if k == "mem":
        return _load(entry_mem, evaluate(n[1], entry_mem, entry_reg, image, uni_vals), n[2])
    if k == "cur":
        return _load(image, evaluate(n[1], entry_mem, entry_reg, image, uni_vals), n[2])
    vals = [evaluate(c, entry_mem, entry_reg, image, uni_vals) for c in n[2]]
    return _apply(n[1], vals, [width(c) for c in n[2]], n[3])


def _load(buf, addr, sz):
    v = 0
    for i in range(sz):
        v |= buf[(addr + i) & 0xFFFF] << (8 * i)
    return v
