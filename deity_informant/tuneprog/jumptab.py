"""S2 static closure -- a patched jump's domain from the tables its writers copy.

``LDA T,X; STA J+1`` before a ``JMP`` or an always-taken branch is a switch; the
table's other entries are targets too, and become arms that ``trap "unverified"``.
A table runs to the nearest instruction or foreign region, on its layout's step.
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


def _domain(sources, spans, cap):
    """Index values every table source covers, on the step its layout implies.

    Halves one byte apart are a table of words, so its entries are two apart;
    parallel columns (and a one-byte table) step by one.
    """
    tabs = [s for s in sources if s[0] == "table"]
    step = 2 if len(tabs) == 2 == len(sources) and abs(tabs[0][2] - tabs[1][2]) == 1 else 1
    lo, hi = None, None
    for _t, r, base, _i in tabs:
        a, b = spans.get(r.id, (r.base, r.base + r.size))
        lo = a - base if lo is None else max(lo, a - base)
        hi = b - base if hi is None else min(hi, b - base)
    lo = 0 if lo is None else max(lo, 0)  # the index register is an unsigned byte
    if not tabs or lo >= hi:
        return range(0)
    first = lo + ((tabs[0][1].base - tabs[0][2]) - lo) % step
    return range(first, hi, step) if (hi - first + step - 1) // step <= cap else range(0)


def _cell(term, defs):
    """``(cell address, width, branch base)`` of a computed target, or ``None``.

    A patched ``JMP`` reads its operand as one 16-bit value; a patched branch reads
    one byte and sign-extends it onto the address after the instruction.
    """
    e = _resolve(term.e, defs)
    if type(e) is Load and e.w == 2 and type(e.a) is Const:
        return e.a.v, 2, None
    if type(e) is not Bin or e.op != "-" or type(e.a) is not Bin or e.a.op != "+":
        return None
    sign, add = e.b, e.a
    base = next((x for x in (add.a, add.b) if type(x) is Const), None)
    cell = next((x for x in (add.a, add.b) if type(x) is not Const), None)
    if base is None or type(sign) is not Bin or sign.op != "<<":
        return None
    if type(sign.a) is not Bin or sign.a.op != "&" or sign.a.a != cell:
        return None
    hit = _resolve(cell, defs)
    ok = type(hit) is Load and hit.w == 1 and type(hit.a) is Const
    return (hit.a.v, 1, base.v) if ok else None


def _byte(src, x, image):
    return image[src[2] + x] if src[0] == "table" else src[1]


def _target(srcs, x, image, base):
    """The address arm ``x`` of the table jumps to."""
    if base is None:
        return _byte(srcs[0], x, image) | (_byte(srcs[1], x, image) << 8)
    b = _byte(srcs[0], x, image)
    return (base + b - ((b & 0x80) << 1)) & 0xFFFF


def owners(prog, code, addrs=None):
    """``{address: region}`` for every byte an access touched; an instruction is ``-1``."""
    out = {a: -1 for a in code}
    for r in prog.storage:
        if r.id < 0:
            continue
        for a in (addrs or {}).get(r.id) or range(r.base, r.base + r.size):
            out[a] = r.id
    return out


def span(r, own, addr_owner, band):
    """A table's static extent: out to the nearest instruction or foreign access.

    The columns of one table are not foreign to each other, which is what lets a
    table of words grow past the halves it interleaves with.
    """
    a, b = r.base, r.base + r.size
    while a > band[0] and addr_owner.get(a - 1, r.id) in own:
        a -= 1
    while b < band[1] and addr_owner.get(b, r.id) in own:
        b += 1
    return a, b


def enumerate_targets(prog, code=(), addrs=None, limit=64):
    """Add a patched jump's unobserved table targets as ``unverified`` arms; count them."""
    band = prog.meta.get("load", (0, 0x10000))
    image = prog.image()
    rgn = prog.by_id()
    addr_owner = owners(prog, code, addrs)
    added = 0
    for proc in prog.procs.values():
        defs = _defs(proc)
        writers = _writers(proc, defs)
        for b in list(proc.blocks.values()):
            hit = _cell(b.term, defs) if type(b.term) is Switch else None
            if hit is None:
                continue
            cell, width, base = hit
            srcs = [_source(cell + k, writers, rgn, image) for k in range(width)]
            if not all(srcs) or not any(s[0] == "table" for s in srcs):
                continue
            idx = {s[3] for s in srcs if s[0] == "table"}
            cols = {s[1].id for s in srcs if s[0] == "table"}
            ext = {i: span(rgn[i], cols, addr_owner, band) for i in cols}
            dom = _domain(srcs, ext, limit) if len(idx) == 1 else range(0)
            added += _arms(proc, b, srcs, dom, image, base, band, addr_owner)
    return added


def _arms(proc, b, srcs, dom, image, base, band, owner=()):
    """Give the block an arm for every table entry the trace never dispatched.

    An entry that addresses a byte some access reads is data, not a target.
    """
    seen = {v for v, _l in b.term.cases}
    extra = []
    for x in dom:
        t = _target(srcs, x, image, base)
        if t in seen or not band[0] <= t < band[1] or owner.get(t, -1) >= 0:
            continue
        seen.add(t)
        lbl = "U%04X_%04X" % (b.src, t)
        proc.blocks[lbl] = Block(lbl, [], Trap("unverified"), t)
        extra.append((t, lbl))
    if extra:
        b.term = Switch(b.term.e, tuple(b.term.cases) + tuple(extra), b.term.default)
    return len(extra)
