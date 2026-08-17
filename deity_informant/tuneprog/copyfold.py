"""S6 -- fold a chain of sibling copies into one loop over the copy index.

The k copies must be one program modulo a renaming: equal alpha-renamed
skeletons, every differing address consistent with one per-copy mapping, every
other differing constant affine in the index. What differs becomes a group view.
"""

from __future__ import annotations

from .ir import (
    Assert,
    Bin,
    Block,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    R16,
    Return,
    Store,
    Switch,
    Trap,
    SID_REG_HI,
    SID_REG_LO,
    Var,
    W16,
    retarget,
    succs,
)
from .irwalk import defs_of
from .facts import MAXROLE
from .siblings import Copies
from .ssa import prune

LATCH = "$fold%d"
SID_VOICE = 7  # the SID's per-voice register block


class _Ctx:
    """Collects a copy's holes, or substitutes the loop index into copy 0's."""

    def __init__(self, proc, order, nxt, defs, subs=None, var="", keep=None, retnext=False):
        self.keep = keep or {}
        self.retnext = retnext
        self.proc = proc
        self.pos = {l: i for i, l in enumerate(order)}
        self.nxt = nxt
        self.defs = defs
        self.subs = None if subs is None else list(subs)
        self.var = var
        self.holes = []
        self.ren = {}
        self.reg = ""
        self.bad = False

    def istrap(self, lbl):
        """True when the successor is a bare trap: a shape the copies may differ in."""
        b = self.proc.blocks.get(lbl)
        return b is not None and not b.stmts and type(b.term) is Trap

    def edge(self, lbl):
        """A successor as a token: a trap, a block of the copy, the next copy, or out."""
        if self.istrap(lbl):
            return "trap", self.proc.blocks[lbl].term.why
        if lbl in self.pos:
            return "b", self.pos[lbl]
        return ("next",) if lbl == self.nxt else ("out", lbl)

    def hole(self, kind, v, w=1):
        """Record one constant or region id; in substitution mode, replace it."""
        if self.subs is None:
            self.holes.append((kind + self.reg if kind == "k" else kind, v))
            return None
        how = self.subs.pop(0)
        if how[0] != "affine":
            return None
        step = Bin("*", Var(self.var), Const(abs(how[1]), w), w)
        if not v:
            return step if how[1] > 0 else Bin("-", Const(0, w), step, w)
        return Bin("+" if how[1] > 0 else "-", Const(v, w), step, w)

    def name(self, n):
        return self.ren.setdefault(n, "$%d" % len(self.ren)) if n in self.defs else n

    def region(self, r):
        """Record a region id; its address constants are tagged with its hole."""
        self.hole("r", r)
        return "@%d" % (len(self.holes) - 1 if self.subs is None else 0)


def _expr(e, c, tag=""):
    keep, c.reg = c.reg, tag or c.reg
    out = _expr1(e, c)
    c.reg = keep
    return out


def _expr1(e, c):
    t = type(e)
    if t is Const:
        return ("k",), (c.hole("k", e.v, e.w) or e)
    if t is Var:
        return ("v", c.name(e.n)), e
    if t is Load:
        tag = c.region(e.r)
        tok, a = _expr(e.a, c, tag)
        return ("l", e.cls, e.w, tok), Load(e.cls, a, e.w, e.lo, e.hi, e.r)
    if t is R16:
        tag = c.region(e.lo)
        c.region(e.hi)
        tok, a = _expr(e.a, c, tag)
        return ("r16", tok), R16(e.lo, e.hi, a)
    if t is Bin:
        ta, a = _expr1(e.a, c)
        tb, b = _expr1(e.b, c)
        return ("b", e.op, e.w, ta, tb), Bin(e.op, a, b, e.w)
    c.bad = True
    return ("?",), e


def _stmt(s, c):
    t = type(s)
    if t is Let:
        tok, e = _expr(s.e, c, "")
        return ("let", c.name(s.n), tok), Let(s.n, e)
    if t is Store:
        tag = c.region(s.r)
        ta, a = _expr(s.a, c, tag)
        tv, v = _expr(s.v, c, "")
        return ("st", s.cls, s.w, ta, tv), Store(s.cls, a, v, s.w, s.lo, s.hi, s.r, s.src)
    if t is W16:
        tag = c.region(s.lo)
        c.region(s.hi)
        ta, a = _expr(s.a, c, tag)
        te, e = _expr(s.e, c, "")
        return ("w16", ta, te), W16(s.lo, s.hi, a, e, s.src)
    if t is Call:
        # an argument the printer drops is machine plumbing: neither shape nor hole
        keep = c.keep.get(s.proc)
        toks, args = [], []
        for i, a in enumerate(s.args):
            if keep is not None and not keep[i]:
                args.append(a)
                continue
            tok, x = _expr(a, c, "")
            toks.append(tok)
            args.append(x)
        return ("call", s.proc, tuple(toks)), Call(s.proc, tuple(args), s.rets)
    if t is Assert:
        tok, e = _expr(s.e, c, "")
        return ("assert", s.why, tok), Assert(e, s.why)
    c.bad = True
    return ("?",), s


def _term(t, c):
    k = type(t)
    if k is If:
        tok, e = _expr(t.c, c, "")
        return ("if", tok, c.edge(t.t), c.edge(t.f)), If(e, t.t, t.f)
    if k is Switch:
        tok, e = _expr(t.e, c, "")
        # an arm no copy ran is a trap the copies need not agree on: a table whose
        # extent rule over-reached gives one copy arms the others have not
        arms = tuple(c.edge(l) for _v, l in t.cases if not c.istrap(l))
        return ("sw", tok, arms), Switch(e, t.cases, t.default)
    if k is Goto:
        return ("goto", c.edge(t.to)), t
    if k is Return:
        if c.retnext:
            return ("goto", ("next",)), t
        out = [_expr(v, c, "") for v in t.vals]
        return ("ret", tuple(t for t, _v in out)), Return(tuple(v for _t, v in out))
    return (type(t).__name__, getattr(t, "why", "")), t


def _order(proc, blocks, entry):
    """The copy's blocks in a deterministic traversal from its entry."""
    out, work = [], [entry]
    while work:
        lbl = work.pop()
        if lbl in out or lbl not in blocks:
            continue
        out.append(lbl)
        work.extend(reversed(succs(proc.blocks[lbl].term)))
    return out + sorted(blocks - set(out))


def _shape(proc, blocks, entry, nxt, subs=None, var="", keep=None, retnext=False, skip=0):
    """``(tokens, holes, order, blocks)`` of one copy; tokens are ``None`` if unwalkable.

    ``skip`` leaves a prologue out of the entry block: the copy's first
    instruction is not a block start when S4 merged it into what runs before.
    """
    order = _order(proc, blocks, entry)
    defs = {n for l in blocks for s in proc.blocks[l].stmts for n in defs_of(s)}
    c = _Ctx(proc, order, nxt, defs, subs, var, keep, retnext)
    toks, out = [], []
    for lbl in order:
        b = proc.blocks[lbl]
        stmts = []
        for s in b.stmts[skip if lbl == entry else 0 :]:
            if type(s) is Store and s.cls == "raw":
                stmts.append(s)
                continue
            tok, x = _stmt(s, c)
            toks.append(tok)
            stmts.append(x)
        tok, term = _term(b.term, c)
        toks.append(tok)
        head = b.stmts[:skip] if lbl == entry else []
        out.append(Block(lbl, head + stmts, term, b.src, b.count))
    return (None if c.bad else tuple(toks)), c.holes, order, out


def elems(r):
    """How many elements a region's stride divides it into."""
    return -(-r.size // max(r.stride, 1))


def indexed(rgn, rids, vals, k):
    """How a run's differing addresses print: as an ``index``, as a ``table``, or ``no``.

    One index serves one region a stride view already walks; addresses in
    different regions need the per-copy table; the SID's own register file is
    indexed by voice and by nothing else.
    """
    if rids is None:
        return "index"
    d = vals[1] - vals[0]
    if all(SID_REG_LO <= v <= SID_REG_HI for v in vals):
        return "index" if not d % SID_VOICE else "no"
    if len(set(rids)) > 1:
        return "table"
    r = rgn.get(rids[0])
    if r is None or r.kind == "io":
        return "index"
    return "index" if not d % max(r.stride, 1) and k <= elems(r) <= MAXROLE else "table"


def plan(holes, rgn=None):
    """The per-hole substitution plan and the group slots, or ``(None, None)``.

    A region id and an address may differ when one mapping explains every use of
    them; an address a stride view already indexes, and any other constant, must
    step affinely with the copy index instead.
    """
    kinds = [h[0] for h in holes[0]]
    if any([h[0] for h in x] != kinds for x in holes[1:]):
        return None, None
    out, slots, rmap = [], {}, {}
    for i, kind in enumerate(kinds):
        vals = [x[i][1] for x in holes]
        if kind == "r":
            if rmap.setdefault(vals[0], tuple(vals)) != tuple(vals):
                return None, None
            out.append(("keep",))
            continue
        if len(set(vals)) == 1:
            out.append(("keep",))
            continue
        rids = [x[int(kind[2:])][1] for x in holes] if kind.startswith("k@") else None
        how = indexed(rgn or {}, rids, vals, len(holes))
        if how == "no":
            return None, None
        if how == "table":
            key = (rids[0], vals[0])
            if slots.setdefault(key, tuple(zip(rids, vals))) != tuple(zip(rids, vals)):
                return None, None
            out.append(("keep",))
            continue
        d = vals[1] - vals[0]
        if any(v != vals[0] + i * d for i, v in enumerate(vals)):
            return None, None
        out.append(("affine", d))
    return out, slots


def _copies(proc, fam):
    """``[block labels]`` per copy, or ``None`` when the copies are not separable.

    A bare trap block belongs to no copy: it is the shape a copy has where a
    sibling has code, compared as its reason and never walked.
    """
    spans = fam.spans()
    live = {l for l, b in proc.blocks.items() if b.stmts or type(b.term) is not Trap}
    out = [{l for l in live if proc.blocks[l].src in s} for s in spans]
    if any(not s for s in out) or len(set().union(*out)) != sum(len(s) for s in out):
        return None
    return out


def _preds(proc):
    out = {}
    for lbl, b in proc.blocks.items():
        for s in succs(b.term):
            out.setdefault(s, set()).add(lbl)
    return out


def entries(proc, fam, sets):
    """The block each copy is entered at, or ``None`` when it is not one block.

    A copy whose first instruction S4 merged into the block before it is entered
    at that block, which then carries a prologue the fold leaves outside the loop.
    """
    preds = _preds(proc)
    out = []
    for j, base in enumerate(fam.bases):
        hit = [
            l
            for l in sets[j]
            if proc.blocks[l].src == base and (l == proc.entry or preds.get(l, set()) - sets[j])
        ]
        if not hit and not j:
            hit = sorted({l for x in sets[j] for l in preds.get(x, ()) if l not in sets[j]})
            if len(hit) == 1:
                sets[j].add(hit[0])
        if len(hit) != 1:
            return None
        out.append(hit[0])
    return out


def _exits(proc, blocks, inside):
    """The labels a copy leaves to that are neither its own nor a bare trap."""
    out = set()
    for lbl in blocks:
        b = proc.blocks[lbl]
        for s in succs(b.term):
            if s in blocks or (not proc.blocks[s].stmts and type(proc.blocks[s].term) is Trap):
                continue
            out.add(s)
    return out


def foldable(proc, fam):
    """``(sets, entries, after)`` when the copies are a separable chain, else ``None``.

    Every copy must leave only into the next one, the last only into one common
    block (or by returning, when the run ends the procedure), and nothing outside
    may enter a copy other than the first.
    """
    sets = _copies(proc, fam)
    if sets is None:
        return None
    ents = entries(proc, fam, sets)
    if ents is None:
        return None
    inside = set().union(*sets)
    after = _exits(proc, sets[-1], inside)
    rets = any(type(proc.blocks[l].term) is Return for l in sets[-1])
    if len(after) > 1 or (not after and not rets):
        return None
    after = after.pop() if after else None
    preds = _preds(proc)
    for j, blocks in enumerate(sets):
        want = {ents[j + 1]} if j + 1 < len(ents) else set([after]) - {None}
        if _exits(proc, blocks, inside) - want:
            return None
        if j and preds.get(ents[j], set()) - sets[j] - sets[j - 1]:
            return None
    return sets, ents, after


def check(proc, ents, sets, after, keep=None, rgn=None, base=None):
    """``(plan, slots, orders, retnext, skip)`` when the copies are one program.

    The copies after the first fix the shape; the first may carry a prologue (S4
    merged its opening instruction into the block before it), and ``skip`` is how
    many statements of that prologue the loop leaves outside itself.
    """
    shapes, retnext = [], False
    for j, blocks in list(enumerate(sets))[1:]:
        last = j + 1 == len(sets)
        nxt = after if last else ents[j + 1]
        toks, holes, order, _b = _shape(proc, blocks, ents[j], nxt, keep=keep)
        if last and shapes and toks != shapes[0][0]:
            # the last copy leaves the run by returning, where the others jump on
            retnext = True
            toks, holes, order, _b = _shape(proc, blocks, ents[j], nxt, keep=keep, retnext=True)
        if toks is None or (shapes and toks != shapes[0][0]):
            return None
        shapes.append((toks, holes, order))
    top = 0 if base is None or proc.blocks[ents[0]].src == base else len(proc.blocks[ents[0]].stmts)
    for skip in range(top + 1):
        toks, holes, order, _b = _shape(proc, sets[0], ents[0], ents[1], keep=keep, skip=skip)
        if toks is not None and toks == shapes[0][0]:
            p, slots = plan([holes] + [s[1] for s in shapes], rgn)
            if p is None:
                return None
            return p, slots, [order] + [s[2] for s in shapes], retnext, skip
    return None


def fold(proc, fam, var, latch, keep=None, rgn=None):
    """Fold ``fam``'s copies in ``proc`` into a loop over ``var``; returns its slots.

    Copy 0 keeps the code with the index substituted, the chain edge becomes the
    back edge, and the block counts are the whole run's.
    """
    hit = foldable(proc, fam)
    if hit is None:
        return None
    sets, ents, after = hit
    got = check(proc, ents, sets, after, keep, rgn, fam.bases[0])
    if got is None:
        return None
    subs, slots, orders, _ret, skip = got
    _t, _h, order, blocks = _shape(proc, sets[0], ents[0], ents[1], subs, var, keep, skip=skip)
    calls, k = proc.blocks[ents[0]].count, len(sets)
    for i, b in enumerate(blocks):
        b.count = sum(proc.blocks[orders[j][i]].count for j in range(k))
        proc.blocks[b.label] = b
    for lbl in set().union(*sets[1:]):
        del proc.blocks[lbl]
    src = proc.blocks[ents[0]].src
    # the loop runs k times per call, so the prologue block keeps the entry's own
    # count -- and its label, which every edge from outside the run already names
    header, e = "$hdr" + var, proc.blocks[ents[0]]
    proc.blocks[header] = Block(header, e.stmts[skip:], e.term, e.src, e.count)
    proc.blocks[ents[0]] = Block(ents[0], e.stmts[:skip], Goto(header), e.src, calls)
    for lbl in sets[0] - {ents[0]}:
        proc.blocks[lbl].term = retarget(proc.blocks[lbl].term, ents[0], header)
    if after is None:
        after = latch + "$ret"
        proc.blocks[after] = Block(after, [], Return(), src)
    proc.blocks[latch] = Block(latch, [], If(Bin("<", Var(var), Const(k - 1)), header, after), src)
    for lbl in sets[0] | {header}:
        proc.blocks[lbl].term = retarget(proc.blocks[lbl].term, ents[1], latch)
    prune(proc)
    return {"var": var, "n": k, "header": header, "latch": latch, "slots": slots, "order": order}


def parts(fam, p):
    """``fam`` cut into runs of ``p`` consecutive copies."""
    return [
        Copies(fam.bases[i : i + p], tuple(r[i : i + p] for r in fam.rows), fam.proc)
        for i in range(0, fam.k, p)
    ]


def _runs(fam):
    """The family, then the equal runs of it to try when the whole does not fold.

    Six copies that are three voices of two cascades are one program in two
    dimensions, not in one: the runs of three fold, the whole six does not.
    """
    out = [[fam]]
    out += [parts(fam, p) for p in range(fam.k - 1, 1, -1) if not fam.k % p]
    return out


def apply(prog, fams, keep=None):
    """Fold every foldable family of ``prog``; records the loops in ``prog.meta``.

    Presentation only: the argument is a view, and what the fold removes is k-1
    copies of code the remaining one now stands for.
    """
    rgn = prog.by_id()
    out = prog.meta.setdefault("folds", {})
    n = sum(len(v) for v in out.values())
    for fam in fams:
        proc = prog.procs.get(fam.proc)
        if proc is None:
            continue
        for run in _runs(fam):
            got = [
                fold(proc, f, "$fv%d" % (n + i), "$fold%d" % (n + i), keep, rgn)
                for i, f in enumerate(run)
            ]
            got = [g for g in got if g is not None]
            for g in got:
                g["group"] = ("voice" if g["n"] == 3 else "copy%d" % n) if g["slots"] else ""
                g["proc"] = fam.proc
                out[g["header"]] = g
                n += 1
            if got:
                break
    return out
