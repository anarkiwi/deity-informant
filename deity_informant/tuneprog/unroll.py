"""S6 -- copy folding: consecutive isomorphic siblings print once over an index.

A run of k segments differing only in constants that step per copy (a struct
stride, a voice's SID registers, a scaled argument) prints as ``for v in 0, 1, 2``
over copy 0; equal alpha-renamed skeletons and affine steps are the proof.
"""

from __future__ import annotations

from .ir import Assert, Bin, Call, Const, Let, Load, R16, Store, Var, W16
from .irwalk import defs_of, stmt_uses, uses_of
from .structure import Blk, Case, Cond, Exit, For, Loop, walk

MINSTMT = 6


class _Ctx:
    """Walks a segment, collecting its holes or substituting the loop index."""

    def __init__(self, defs, subs=None, var="", keep=None):
        self.defs = defs
        self.keep = keep or {}
        self.reg = ""
        self.holes = []
        self.subs = list(subs or ())
        self.sub = subs is not None
        self.var = var
        self.ren = {}
        self.bad = False

    def hole(self, kind, v, w=1):
        """Record (or replace) one constant or region id of the segment."""
        if not self.sub:
            self.holes.append((kind + self.reg if kind == "k" else kind, v))
            return None
        d = self.subs.pop(0)
        if kind != "k" or not d:
            return None
        step = Bin("*", Var(self.var), Const(abs(d), w), w)
        if not v:
            return step if d > 0 else Bin("-", Const(0, w), step, w)
        return Bin("+" if d > 0 else "-", Const(v, w), step, w)

    def name(self, n):
        return self.ren.setdefault(n, "$%d" % len(self.ren)) if n in self.defs else n


def _expr(e, c):
    t = type(e)
    if t is Const:
        return ("k",), (c.hole("k", e.v, e.w) or e)
    if t is Var:
        return ("v", c.name(e.n)), e
    if t is Load:
        c.hole("r", e.r)
        tok, a = _addr(e.a, c, e.r)
        return ("l", e.cls, e.w, tok), Load(e.cls, a, e.w, e.lo, e.hi, e.r)
    if t is R16:
        c.hole("r", e.lo)
        c.hole("r", e.hi)
        tok, a = _addr(e.a, c, e.lo)
        return ("r16", tok), R16(e.lo, e.hi, a)
    if t is Bin:
        ta, a = _expr(e.a, c)
        tb, b = _expr(e.b, c)
        return ("b", e.op, e.w, ta, tb), Bin(e.op, a, b, e.w)
    c.bad = True
    return ("?",), e


def _addr(e, c, rid):
    """Walk an address: its constants are that region's, so one stride serves it."""
    return _tagged(e, c, "@%d" % rid)


def _tagged(e, c, tag):
    keep, c.reg = c.reg, tag
    out = _expr(e, c)
    c.reg = keep
    return out


def _stmt(s, c):
    t = type(s)
    if t is Let:
        tok, e = _expr(s.e, c)
        return ("let", c.name(s.n), tok), Let(s.n, e)
    if t is Store and s.cls == "raw":
        return ("raw",), s
    if t is Store:
        c.hole("r", s.r)
        ta, a = _addr(s.a, c, s.r)
        tv, v = _expr(s.v, c)
        return ("st", s.cls, s.w, ta, tv), Store(s.cls, a, v, s.w, s.lo, s.hi, s.r, s.src)
    if t is W16:
        c.hole("r", s.lo)
        c.hole("r", s.hi)
        ta, a = _addr(s.a, c, s.lo)
        te, e = _expr(s.e, c)
        return ("w16", ta, te), W16(s.lo, s.hi, a, e, s.src)
    if t is Call:
        # Arguments the printer drops are machine plumbing: neither shape nor hole.
        keep = c.keep.get(s.proc)
        toks, args = [], []
        for i, a in enumerate(s.args):
            if keep is not None and not keep[i]:
                args.append(a)
                continue
            tok, x = _tagged(a, c, "!%s#%d" % (s.proc, i))
            toks.append(tok)
            args.append(x)
        return ("call", s.proc, tuple(toks)), Call(s.proc, tuple(args), s.rets)
    if t is Assert:
        tok, e = _expr(s.e, c)
        return ("assert", s.why, tok), Assert(e, s.why)
    c.bad = True
    return ("?",), s


def _node(n, c):
    t = type(n)
    if t is Blk:
        toks, stmts = _many(n.stmts, c, _stmt)
        return ("blk", toks), Blk(n.label, stmts, n.src, n.count)
    if t is Cond:
        tok, e = _expr(n.c, c)
        tt, then = _many(n.then, c, _node)
        tf, els = _many(n.els, c, _node)
        return ("cond", tok, tt, tf), Cond(e, then, els)
    if t is Case:
        tok, e = _expr(n.e, c)
        arms = [(v, _many(b, c, _node)) for v, b in n.cases]
        toks = tuple((v, t) for v, (t, _b) in arms)
        return ("case", tok, toks), Case(e, tuple((v, b) for v, (_t, b) in arms), n.src)
    if t is For:
        tb, body = _many(n.body, c, _node)
        node = For(n.var, n.values, body, n.scale, n.hide, n.label, n.count)
        return ("for", c.name(n.var), n.values, n.scale, tb), node
    if t is Loop:
        tb, body = _many(n.body, c, _node)
        return ("loop", tb), Loop(body, n.label, n.count)
    if t is Exit:
        if n.e is None:
            return ("exit", n.kind, n.why), n
        tok, e = _expr(n.e, c)
        return ("exit", n.kind, n.why, tok), Exit(n.kind, n.why, e)
    c.bad = True
    return ("?",), n


def _many(items, c, fn):
    out = [fn(x, c) for x in items]
    return tuple(t for t, _x in out), [x for _t, x in out]


def units_of(body):
    """Sibling nodes, with every block's statements as units of their own.

    A JSR frame push is not a unit: the printer never shows it, so it is not part
    of a shape -- which is what lets a tail call join the run of ordinary calls.
    """
    out = []
    for n in body:
        if type(n) is Blk:
            out += [("s", s, n) for s in n.stmts if type(s) is not Store or s.cls != "raw"]
        else:
            out.append(("n", n, None))
    return out


def _rebuild(units):
    """Units back into nodes, consecutive statements of one block regrouped."""
    out, run, src = [], [], None
    for kind, obj, blk in units:
        if kind == "s" and (src is None or src is blk):
            run, src = run + [obj], blk
            continue
        if run:
            out.append(Blk(src.label, run, src.src, src.count))
            run, src = [], None
        if kind == "s":
            run, src = [obj], blk
        else:
            out.append(obj)
    if run:
        out.append(Blk(src.label, run, src.src, src.count))
    return out


def _skeleton(units, defs, keep=None):
    c = _Ctx(defs, keep=keep)
    toks = [(_stmt if k == "s" else _node)(o, c)[0] for k, o, _b in units]
    return (None if c.bad else tuple(toks)), c.holes


def _defined(units):
    """The names a run defines: only those may be renamed between copies."""
    out = set()
    for kind, obj, _b in units:
        if kind == "s":
            out.update(defs_of(obj))
        else:
            _node_defs(obj, out)
    return out


def _node_defs(n, out):
    """Every name a structured node and its children define."""
    for x in walk([n]):
        if type(x) is Blk:
            for s in x.stmts:
                out.update(defs_of(s))
        elif type(x) is For:
            out.add(x.var)


def steps(runs):
    """The per-hole step when copy i is copy 0 plus i times it, else ``None``.

    One region is walked with one stride: the constants of its addresses must
    agree, and at least one of them must move, or the copies index nothing.
    """
    out, by = [], {}
    for j, (kind, v0) in enumerate(runs[0]):
        vals = [r[j] for r in runs]
        if any(k != kind for k, _v in vals):
            return None
        if kind == "r":
            if any(v != v0 for _k, v in vals):
                return None
            out.append(0)
            continue
        d = vals[1][1] - v0
        if any(v != v0 + i * d for i, (_k, v) in enumerate(vals)):
            return None
        if kind != "k" and d:
            by.setdefault(kind, set()).add(d)
        out.append(d)
    return None if not by or any(len(v) > 1 for v in by.values()) else out


def _run(units, i, minunits, keep=None):
    """``(length, copies, steps)`` of the best isomorphic run starting at ``i``."""
    best, hit = None, None
    for length in range(1, (len(units) - i) // 2 + 1):
        sk, holes = _skeleton(units[i : i + length], _defined(units[i : i + length]), keep)
        if sk is None:
            continue
        runs, k = [holes], 1
        while i + (k + 1) * length <= len(units):
            seg = units[i + k * length : i + (k + 1) * length]
            sk2, h2 = _skeleton(seg, _defined(seg), keep)
            if sk2 != sk or len(h2) != len(holes):
                break
            runs.append(h2)
            k += 1
        size = _size(units[i : i + length])
        for copies in range(k, 1, -1):
            step = steps(runs[:copies])
            if step is None or copies * size < minunits:
                continue
            score = (copies * length, length)
            if best is None or score > best:
                best, hit = score, (length, copies, step)
            break
    return hit


def _size(units):
    """The statements a run of units prints; a call weighs the procedure it stands for."""
    return sum(_stmts(o) if k == "n" else MINSTMT if type(o) is Call else 1 for k, o, _b in units)


def _stmts(n):
    """The statements a structured node prints, its children included."""
    return sum(len(x.stmts) for x in walk([n]) if type(x) is Blk)


def _abstract(units, step, var, keep=None):
    """Copy 0 with each stepping constant replaced by the loop index."""
    c = _Ctx(set(), step, var, keep)
    return [(k, (_stmt if k == "s" else _node)(o, c)[1], b) for k, o, b in units]


def unroll(structured, live=None, keep=None, minunits=MINSTMT):
    """Fold every isomorphic sibling run into a ``for`` over the copy index."""
    n = [0]
    for name, body in structured.items():
        structured[name] = _body(body, minunits, n, (live or {}).get(name, set()), keep)
    return n[0]


def _body(body, minunits, n, live, keep):
    body = [_recurse(x, minunits, n, live, keep) for x in body]
    units, out, i = units_of(body), [], 0
    while i < len(units):
        hit = _run(units, i, minunits, keep)
        if hit is None or _escapes(units, i, hit[0] * hit[1], live):
            out.append(units[i])
            i += 1
            continue
        length, k, step = hit
        var = "$i%d" % n[0]
        n[0] += 1
        seg = _abstract(units[i : i + length], step, var, keep)
        out.append(("n", For(var, tuple(range(k)), _rebuild(seg), 1, frozenset(), "", 0), None))
        i += length * k
    return _rebuild(out)


def _escapes(units, i, span, live):
    """True when the run defines a live name a statement outside it reads."""
    inside = _defined(units[i : i + span])
    after = set()
    for kind, obj, _b in units[:i] + units[i + span :]:
        (stmt_uses if kind == "s" else _node_uses)(obj, after)
    return bool(inside & after & live)


def _node_uses(n, out):
    """Every name a structured node and its children read."""
    for x in walk([n]):
        t = type(x)
        if t is Blk:
            for s in x.stmts:
                stmt_uses(s, out)
        elif t is Cond:
            uses_of(x.c, out)
        elif t is Case:
            uses_of(x.e, out)


def _recurse(n, minunits, count, live, keep):
    t = type(n)
    if t is Cond:
        n.then = _body(n.then, minunits, count, live, keep)
        n.els = _body(n.els, minunits, count, live, keep)
    elif t is Case:
        n.cases = tuple((v, _body(b, minunits, count, live, keep)) for v, b in n.cases)
    elif t is For or t is Loop:
        n.body = _body(n.body, minunits, count, live, keep)
    return n
