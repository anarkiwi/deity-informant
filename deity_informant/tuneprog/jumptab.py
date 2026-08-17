"""S2 static closure -- a patched ``JMP``'s domain from the tables its writers copy.

``LDA T1,X; STA J+1; LDA T2,X; STA J+2; JMP`` is a switch whose observed arms are
the commands the trace met; the rest of the table is a known target set, added as
arms that ``trap "unverified"``. The domain is the tables' own observed extent,
interior gaps included (design S2/S6, anatomy 3.6.8).
"""

from __future__ import annotations

from .ir import Bin, Block, Const, Let, Load, Store, Switch, Trap, Var


def _defs(proc):
    return {s.n: s.e for b in proc.blocks.values() for s in b.stmts if type(s) is Let}


def _resolve(e, defs, seen=()):
    """Follow ``Var`` definitions to the expression that really produced a value."""
    while type(e) is Var and e.n in defs and e.n not in seen:
        seen, e = seen + (e.n,), defs[e.n]
    return e


def _writers(proc, defs):
    """``{address: value expression}`` for every byte address exactly one store writes."""
    out = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is Store and s.w == 1 and type(s.a) is Const:
                out.setdefault(s.a.v, []).append(_resolve(s.v, defs))
    return {a: v[0] for a, v in out.items() if len(v) == 1}


def _table(e, rgn):
    """``(region, literal base, index)`` of a ``LDA table,X`` of a const region."""
    if type(e) is not Load or e.w != 1 or type(e.a) is not Bin or e.a.op != "+":
        return None
    r = rgn.get(e.r)
    if r is None or r.kind != "const":
        return None
    for k, i in ((e.a.a, e.a.b), (e.a.b, e.a.a)):
        if type(k) is Const:
            return r, k.v, i
    return None


def _source(addr, writers, rgn, image):
    """``("table", r, base, idx)`` when a writer copies the byte, else ``("const", v)``."""
    w = writers.get(addr)
    if w is None:
        return ("const", image[addr])
    hit = _table(w, rgn)
    return hit and ("table",) + hit


def _domain(sources):
    """Index values every table source covers, in order."""
    lo, hi = None, None
    for s in sources:
        if s[0] != "table":
            continue
        a, b = s[1].base - s[2], s[1].base + s[1].size - s[2]
        lo, hi = (a if lo is None else max(lo, a)), (b if hi is None else min(hi, b))
    return range(lo, hi) if lo is not None and lo < hi else range(0)


def _byte(src, x, image):
    return image[src[2] + x] if src[0] == "table" else src[1]


def _cell(term, defs):
    """The constant address of a switch over a 16-bit load, or ``None``."""
    e = _resolve(term.e, defs)
    return e.a.v if type(e) is Load and e.w == 2 and type(e.a) is Const else None


def enumerate_targets(prog, limit=64):
    """Add a patched jump's unobserved table targets as ``unverified`` arms; count them."""
    lo, hi = prog.meta.get("load", (0, 0x10000))
    image = prog.image()
    rgn = {r.id: r for r in prog.storage}
    added = 0
    for proc in prog.procs.values():
        defs = _defs(proc)
        writers = _writers(proc, defs)
        for b in list(proc.blocks.values()):
            if type(b.term) is not Switch or (cell := _cell(b.term, defs)) is None:
                continue
            srcs = [_source(cell + k, writers, rgn, image) for k in (0, 1)]
            if not all(srcs) or not any(s[0] == "table" for s in srcs):
                continue
            idx = {s[3] for s in srcs if s[0] == "table"}
            dom = _domain(srcs) if len(idx) == 1 else range(0)
            seen = {v for v, _l in b.term.cases}
            extra = []
            for x in dom if len(dom) <= limit else ():
                t = _byte(srcs[0], x, image) | (_byte(srcs[1], x, image) << 8)
                if t in seen or not lo <= t < hi:
                    continue
                seen.add(t)
                lbl = "U%04X_%04X" % (cell, t)
                proc.blocks[lbl] = Block(lbl, [], Trap("unverified"), t)
                extra.append((t, lbl))
                added += 1
            if extra:
                b.term = Switch(b.term.e, tuple(b.term.cases) + tuple(extra), b.term.default)
    return added
