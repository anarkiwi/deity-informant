"""S6 -- value inlining over the presentation copy: a let folded into its uses.

Regions are disjoint by construction, so a load moves past stores to other
regions and past ``sidw``/``iow``; a store to its own region, a call or an input
read stops it, and a use in another block needs that block to be the definer's.
"""

from __future__ import annotations

from .graph import cfg, idoms, natural_loops, preds_of
from .ir import Bin, Call, Const, Let, Load, Return, Store, Var, retval, succs
from .irwalk import (
    Uses,
    any_load,
    apply_stmt,
    apply_term,
    loadfree,
    node_exprs,
    renamer,
    stmt_uses,
    sub_expr,
    term_uses,
    use_counts,
    walk,
)


def _clobbers(s, e):
    """True when statement ``s`` can change the value of ``e``, or reorder its input.

    Regions are disjoint by construction, so a load of region R survives every
    store to another region and every ``sidw``/``iow``; a call may write anything
    and one input read never moves past another.
    """
    t = type(s)
    if t is Call:
        return not loadfree(e)
    if any_load(e, _input) and any(any_load(x, _input) for x in node_exprs(s)):
        return True
    if t is not Store:
        return False
    if s.cls == "io":
        return any_load(e, lambda x: x.cls == "io")
    if s.cls == "raw":  # a JSR frame is memory: it clobbers the slots it covers
        return any_load(e, lambda x: x.lo <= s.hi and x.hi >= s.lo)
    return any_load(e, lambda x: x.r < 0 or x.r == s.r or s.r < 0)


def _cost(e):
    """Operator count of an expression, address arithmetic included."""
    t = type(e)
    if t is Bin:
        return 1 + _cost(e.a) + _cost(e.b)
    return _cost(e.a) if t is Load else 0


def _loads(e, single, cache, seen=()):
    """Every load the value of ``e`` reads once its single-definition names expand."""
    out = []
    for x in walk(e):
        if type(x) is Load:
            out.append(x)
        elif type(x) is Var and x.n in single and x.n not in seen:
            key = x.n
            if key not in cache:
                cache[key] = _loads(single[key][2], single, cache, seen + (key,))
            out += cache[key]
    return out


def _kills(s, ls):
    return any(_clobbers(s, x) for x in ls)


def _own(proc, preds, lbl):
    """``{block: its predecessor}`` for the blocks only ``lbl`` can reach."""
    out, work = {}, [lbl]
    while work:
        cur = work.pop()
        for nxt in succs(proc.blocks[cur].term):
            if nxt in out or nxt == lbl or preds.get(nxt) != [cur]:
                continue
            out[nxt] = cur
            work.append(nxt)
    return out


def _blocked(proc, own, lbl, at, pos, ls):
    """True when a store, a call or a foreign block sits between the value and a use."""
    for ublk, uidx in pos:
        if ublk == lbl:
            if _kills_run(proc, lbl, at + 1, uidx, ls):
                return True
            continue
        if ublk not in own:
            return True
        if _kills_run(proc, ublk, 0, uidx, ls):
            return True
        cur = own[ublk]
        while cur != lbl:
            if _kills_run(proc, cur, 0, len(proc.blocks[cur].stmts), ls):
                return True
            cur = own[cur]
        if _kills_run(proc, lbl, at + 1, len(proc.blocks[lbl].stmts), ls):
            return True
    return False


def _kills_run(proc, lbl, lo, hi, ls):
    return any(_kills(s, ls) for s in proc.blocks[lbl].stmts[lo:hi])


def _positions(proc):
    """``{name: [(block, index)] once per use}``; the terminator counts last."""
    where = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(list(b.stmts) + [b.term]):
            t = Uses()
            (term_uses if i == len(b.stmts) else stmt_uses)(s, t)
            for u in t.hits:
                where.setdefault(u, []).append((lbl, i))
    return where


def values(proc, live=None, keep=()):
    """Fold a value into its uses, past statements that cannot alias it.

    A use may sit in another block when that block is reachable only through the
    definition's. ``keep`` names the return registers a reader wants: the host's,
    and the ones a caller reads.
    """
    if live is not None:
        want = {0} if retval(proc) is not None else set()
        slots = {proc.rets.index(i) for i in want | set(keep) if i in proc.rets}
        for b in proc.blocks.values():
            b.stmts = [s for s in b.stmts if type(s) is not Let or s.n in live]
            if type(b.term) is Return:
                # the machine's return plumbing goes; the values a reader wants stay
                b.term = Return(
                    ()
                    if not slots
                    else tuple(v if i in slots else Const(0) for i, v in enumerate(b.term.vals))
                )
    uses, where, preds = use_counts(proc), _positions(proc), preds_of(proc)
    latches = _latches(proc, preds)
    defs = {}
    for lbl, b in proc.blocks.items():
        for i, s in enumerate(b.stmts):
            if type(s) is Let:
                defs.setdefault(s.n, []).append((lbl, i, s.e))
    single = {n: v[0] for n, v in defs.items() if len(v) == 1}
    sub, cache, owns = {}, {}, {}
    for n, (lbl, at, e) in sorted(single.items()):
        pos = where.get(n, ())
        if not pos or len(pos) != uses[n] or type(e) is Var or _feeds_copy(proc, pos, latches):
            continue
        ls = _loads(e, single, cache)
        if len(pos) > 1 and (_cost(e) > 1 or any(_input(x) for x in ls)):
            continue
        own = owns.setdefault(lbl, _own(proc, preds, lbl))
        if not _blocked(proc, own, lbl, at, pos, ls):
            sub[n] = e
    return _apply_sub(proc, sub)


def _latches(proc, preds):
    """The blocks that close a loop: their copies are the induction variables."""
    g = cfg(proc)
    return {l for _b, ls in natural_loops(g, idoms(proc, g), preds).values() for l in ls}


def _feeds_copy(proc, pos, latches):
    """A value a loop-closing rename reads stays: it carries the induction variable."""
    for lbl, i in pos:
        stmts = proc.blocks[lbl].stmts
        if lbl in latches and i < len(stmts) and type(stmts[i]) is Let and type(stmts[i].e) is Var:
            return True
    return False


def _apply_sub(proc, sub):
    """Expand the chosen values into each other, then into every use."""
    for _ in range(len(sub)):
        nxt = {n: sub_expr(e, renamer(sub)) for n, e in sub.items()}
        if nxt == sub:
            break
        sub = nxt
    fn = renamer(sub)
    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
        apply_term(b.term, fn)
        b.stmts = [s for s in b.stmts if not (type(s) is Let and s.n in sub)]
    return proc


def _input(x):
    """A load whose value comes from the pinned input stream, not from a region."""
    return x.cls == "io" or (x.cls == "chk" and x.r < 0)
