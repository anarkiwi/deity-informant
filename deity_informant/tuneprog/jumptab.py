"""S2 static closure -- a patched jump's domain from the tables its writers copy.

``LDA T,X; STA J+1`` before a ``JMP`` or an always-taken branch is a switch; the
table's other entries are targets too, and become arms that ``trap "unverified"``.
A table runs to the nearest instruction or foreign region, on its layout's step.
"""

from __future__ import annotations

from .ir import Bin, Block, Const, If, Let, Load, Store, Switch, Trap, Var, succs


def _defs(proc):
    return {s.n: s.e for b in proc.blocks.values() for s in b.stmts if type(s) is Let}


def _resolve(e, defs, seen=()):
    """Follow ``Var`` definitions to the expression that really produced a value."""
    while type(e) is Var and e.n in defs and e.n not in seen:
        seen, e = seen + (e.n,), defs[e.n]
    return e


def _column(e, defs, rgn):
    """The k values a per-copy column stands for, or ``None``.

    A column is a read-only table of one operand the copies disagree on
    (:mod:`.copymerge`), so copy *j*'s value of the expression is its *j*th entry.
    """
    e = _resolve(e, defs)
    if type(e) is not Load:
        return None
    r = rgn.get(e.r) if rgn else None
    if r is None or r.kind != "copymap":
        return None
    off = e.lo - r.base
    n = (e.hi + 1 - e.lo) // e.w
    return [int.from_bytes(r.init[off + j * e.w : off + (j + 1) * e.w], "little") for j in range(n)]


def _copy(e, j, defs, rgn):
    """``e`` as copy ``j`` names it: every column read is that copy's constant.

    A subexpression that holds no column is itself, name and all, which is what a
    range proof over the index needs.
    """
    vals = _column(e, defs, rgn)
    if vals is not None:
        return Const(vals[j], e.w) if j < len(vals) else None
    x = _resolve(e, defs)
    if type(x) is Bin:
        a, b = _copy(x.a, j, defs, rgn), _copy(x.b, j, defs, rgn)
        if a is None or b is None:
            return None
        return e if a is x.a and b is x.b else Bin(x.op, a, b, x.w)
    if type(x) is Load:
        a = _copy(x.a, j, defs, rgn)
        if a is None:
            return None
        return e if a is x.a else Load(x.cls, a, x.w, x.lo, x.hi, x.r)
    return e


def _writers(proc, defs, rgn):
    """``{address: value expression}`` for every byte address exactly one store writes.

    A store the copy index folded writes its cell per copy: the address is a column
    and the value is that copy's, which is what keeps a merged dispatch enumerable.
    """
    out = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is not Store or s.w != 1:
                continue
            plain = _resolve(s.v, defs)
            if type(s.a) is Const:
                out.setdefault(s.a.v, []).append((plain, (), plain))
                continue
            cells = _column(s.a, defs, rgn) or ()
            vals = [_copy(s.v, j, defs, rgn) for j in range(len(cells))]
            alts = tuple(v for v in vals if v is not None)
            for j, a in enumerate(cells):
                if vals[j] is not None:
                    out.setdefault(a, []).append((vals[j], alts, plain))
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


def _index(e, defs, rgn):
    """The index of a ``LDA table,X``, as the program itself names it.

    A merged writer reads the base through a column, so the index is what is left
    of the address once the base is taken away.
    """
    if type(e) is not Load or type(e.a) is not Bin or e.a.op != "+":
        return None
    for k, i in ((e.a.a, e.a.b), (e.a.b, e.a.a)):
        if type(k) is Const or _column(k, defs, rgn) is not None:
            return i
    return None


def _source(addr, writers, rgn, image, defs):
    """``("table", r, base, idx, stop)`` when a writer copies the byte, else ``("const", v)``.

    ``stop`` is how many entries a copy's table holds: a merged writer names one
    table per copy, they are k images of one table, and one index reads them all.
    """
    got = writers.get(addr)
    if got is None:
        return ("const", image[addr])
    val, alts, plain = got
    hit = _table(val, rgn)
    if hit is None:
        return None
    bases = sorted({t[1] for t in (_table(x, rgn) for x in alts) if t})
    gaps = [b - a for a, b in zip(bases, bases[1:])]
    return (
        "table",
        hit[0],
        hit[1],
        _index(plain, defs, rgn) or hit[2],
        min(gaps, default=0) or None,
    )


def _domain(sources, spans, cap, idx=None):
    """Index values every table source covers, on the step its layout implies.

    Halves one byte apart are a table of words, so its entries are two apart;
    parallel columns (and a one-byte table) step by one. ``idx`` is the range the
    index is proven to hold, which only ever tightens the extent.
    """
    tabs = [s for s in sources if s[0] == "table"]
    step = 2 if len(tabs) == 2 == len(sources) and abs(tabs[0][2] - tabs[1][2]) == 1 else 1
    lo, hi = None, None
    for _t, r, base, _i, _stop in tabs:
        a, b = spans.get(r.id, (r.base, r.base + r.size))
        lo = a - base if lo is None else max(lo, a - base)
        hi = b - base if hi is None else min(hi, b - base)
    lo = 0 if lo is None else max(lo, 0)  # the index register is an unsigned byte
    if idx is not None and hi is not None:
        lo, hi = max(lo, idx[0]), min(hi, idx[1])
    # one index reads every copy's table, so a copy's ends where the next begins
    ahead = [s[4] for s in tabs if s[4] is not None]
    hi = hi if not ahead or hi is None else min(hi, lo + min(ahead))
    if not tabs or lo >= hi:
        return range(0)
    first = lo + ((tabs[0][1].base - tabs[0][2]) - lo) % step
    return range(first, hi, step) if (hi - first + step - 1) // step <= cap else range(0)


def _preds(proc):
    """``{label: [block with an edge into it]}``."""
    out = {}
    for b in proc.blocks.values():
        for t in succs(b.term):
            out.setdefault(t, []).append(b)
    return out


def _split(c, w):
    """``(name, lo, hi)`` the condition ``c`` proves when it holds, or ``None``.

    The three tests a 6502 leaves after SSA: the sign bit, an equality, and the
    compare a borrow reports.
    """
    if type(c) is not Bin or c.op not in ("==", "<"):
        return None
    a, b = c.a, c.b
    if c.op == "<" and type(a) is Var and type(b) is Const:
        return (a.n, 0, b.v)
    if c.op != "==" or type(b) is not Const:
        return None
    if type(a) is Var:
        return (a.n, b.v, b.v + 1)
    sign = 1 << (8 * w - 1)
    ok = type(a) is Bin and a.op == "&" and type(a.a) is Var and type(a.b) is Const
    return (a.a.n, 0, sign) if ok and a.b.v == sign and b.v == 0 else None


def _edge(term, to, w):
    """``(name, lo, hi)`` an edge into ``to`` proves; a negated split keeps its half."""
    if type(term) is not If:
        return None
    got = _split(term.c, w)
    if got is None or to not in (term.t, term.f) or term.t == term.f:
        return None
    n, lo, hi = got
    if to == term.t:
        return got
    return (n, hi, 1 << (8 * w)) if lo == 0 else None


def _range(label, e, preds):
    """The range the branches on the one path into ``label`` prove for ``e``.

    Only a value a branch tested is bounded; where nothing is proven the caller's
    extent rule stands.
    """
    if type(e) is not Var:
        return None
    lo, hi, seen = 0, 1 << (8 * e.w), set()
    while label not in seen:
        seen.add(label)
        ps = preds.get(label) or []
        if len(ps) != 1:
            break
        got = _edge(ps[0].term, label, e.w)
        if got is not None and got[0] == e.n:
            lo, hi = max(lo, got[1]), min(hi, got[2])
        label = ps[0].label
    return (lo, hi) if (lo, hi) != (0, 1 << (8 * e.w)) else None


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


def dispatch(proc, rgn, image, band):
    """``{block entry: {table index: arm target}}`` of every patched jump a table writes.

    An arm's index in its own table is what pairs it with the arm a parallel copy
    dispatches; an arm two indices reach names no one index, and pairs with nothing.
    """
    defs = _defs(proc)
    writers = _writers(proc, defs, rgn)
    out = {}
    for b in proc.blocks.values():
        hit = _cell(b.term, defs) if type(b.term) is Switch else None
        if hit is None:
            continue
        cell, width, base = hit
        srcs = [_source(cell + k, writers, rgn, image, defs) for k in range(width)]
        tabs = [s for s in srcs if s and s[0] == "table"]
        if not all(srcs) or not tabs or len({t[3] for t in tabs}) != 1:
            continue
        cases = {v for v, _l in b.term.cases}
        hits = {}
        for x in range(0x100):  # the index register is an unsigned byte
            if any(not band[0] <= t[2] + x < band[1] for t in tabs):
                continue
            t = _target(srcs, x, image, base)
            if t in cases:
                hits.setdefault(t, []).append(x)
        out.setdefault(b.src, {}).update({x[0]: t for t, x in hits.items() if len(x) == 1})
    return out


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
        writers = _writers(proc, defs, rgn)
        preds = _preds(proc)
        for b in list(proc.blocks.values()):
            hit = _cell(b.term, defs) if type(b.term) is Switch else None
            if hit is None:
                continue
            cell, width, base = hit
            srcs = [_source(cell + k, writers, rgn, image, defs) for k in range(width)]
            if not all(srcs) or not any(s[0] == "table" for s in srcs):
                continue
            idx = {s[3] for s in srcs if s[0] == "table"}
            cols = {s[1].id for s in srcs if s[0] == "table"}
            ext = {i: span(rgn[i], cols, addr_owner, band) for i in cols}
            one = next(iter(idx)) if len(idx) == 1 else None
            got = None if one is None else _range(b.label, one, preds)
            dom = _domain(srcs, ext, limit, got) if one is not None else range(0)
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
