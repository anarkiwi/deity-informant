"""Recover a script-VM operator's arity from its dispatch arm.

Nothing here is transcribed: the stream is the pointer the dispatch's own
fetch uses, the operator range is the guard's floor plus the handler tables'
spacing, and an operator's arity is what its arm consumes of that stream.
"""

import heapq
from collections import namedtuple

from . import expr as E
from . import structured as S

_CAP = 256  # blocks per arm: a `stop` arm runs out through the tick-end paths
_BYTES = range(0x100)

Site = namedtuple("Site", "pc stream tables entry_y ops")
Arm = namedtuple("Arm", "op handler arity delta escape refusal")
Walk = namedtuple("Walk", "offsets deltas backs guards edges")
Escape = namedtuple("Escape", "first stride trailer cont")


class Refused(Exception):
    """The recovery declines: the reason names the site or arm it read."""


def _block(model, cache, pc):
    """The lifted block at ``pc``, built from the image like any continuation."""
    blk = cache.get(pc)
    if blk is None:
        ops = model.pcs.get(pc)
        if ops is not None and len(ops) > 1:
            raise Refused("$%04X is self-modified code" % pc)
        op0 = next(iter(ops)) if ops else model.mem0[pc]
        try:
            blk = S._BlockBuilder(model, pc, op0).block
        except (S.DecompileError, NotImplementedError) as exc:
            raise Refused("$%04X does not lift: %s" % (pc, exc)) from exc
        cache[pc] = blk
    return blk


def _fetch(ana, blk, addr):
    """``(pair, y_expr)`` if ``addr`` is a ``(zp),y`` effective address."""
    pair = ana._operand_cells(blk, addr)
    if pair is not None:
        return pair, E.konst(0, 1)  # a Y of zero folds the index away
    if addr[0] != "op" or addr[1] != "INT_ADD" or len(addr[2]) != 2:
        return None
    for i in (0, 1):
        base, ye = addr[2][i], addr[2][1 - i]
        pair = ana._operand_cells(blk, base)
        if pair is None:
            continue
        if ye[0] == "op" and ye[1] == "INT_ZEXT":
            ye = ye[2][0]
        seen = set()
        S._leaf_vars(ye, seen)
        if seen <= {("reg", 2)}:
            return pair, ye
    return None


def _fetches(ana, blk):
    """The block's ``(zp),y`` reads as ``(pair, y_expr, uni)``, in event order."""
    out = []
    for ev in blk.events:
        if ev[0] != "ld":
            continue
        hit = _fetch(ana, blk, ev[2])
        if hit is not None:
            out.append((hit[0], hit[1], ev[1]))
    return out


def _counts(ye):
    """True when a fetch's index is the arm's ``Y``, not a folded constant."""
    seen = set()
    S._leaf_vars(ye, seen)
    return ("reg", 2) in seen


def _at(expr, y, model):
    """``expr`` with the entry ``Y`` bound to ``y``; refuses any other input."""
    try:
        return S._eval1(expr, ("reg", 2), y, model)
    except S._NotPure as exc:
        raise Refused("value is not a function of the arm's entry Y") from exc


def _edge(term, cond_value):
    """The successor a ``br`` term takes when its condition has ``cond_value``."""
    return term[2] if cond_value == term[1] else term[3]


def _successors(blk):
    """Static successor pcs of an arm block; a dynamic terminator is refused."""
    term = blk.term
    if term[0] in ("goto", "jmp"):
        return [term[1]]
    if term[0] == "br" and term[2] is not None:
        return [term[2], term[3]]
    if term[0] == "rts":
        return []
    raise Refused("arm terminator %s at $%04X" % (term[0], blk.pc))


def _pred(model, dblk):
    """The sole static predecessor of the dispatch block -- its fetch and guard."""
    preds = {}
    for blk in model.blocks.values():
        term = blk.term
        tgts = ()
        if term[0] in ("goto", "jmp"):
            tgts = (term[1],)
        elif term[0] == "br":
            tgts = (term[2], term[3]) if term[2] is not None else (term[3],)
        if dblk.pc in tgts:
            preds[blk.pc] = blk
    if len(preds) != 1:
        raise Refused("dispatch $%04X has %d static predecessors" % (dblk.pc, len(preds)))
    return next(iter(preds.values()))


def _guarded_stream(model, ana, dblk):
    """``(pair, floor)``: the stream the dispatch fetches, and its command floor.

    The byte values whose guard edge lands on the dispatch are the operator
    index domain, and they must be an upper range of the byte."""
    pred = _pred(model, dblk)
    if pred.term[0] != "br" or pred.term[2] is None:
        raise Refused("dispatch $%04X is not reached through a byte guard" % dblk.pc)
    hits = _fetches(ana, pred)
    if len(hits) != 1:
        raise Refused("dispatch $%04X fetches %d stream bytes" % (dblk.pc, len(hits)))
    pair, _ye, uni = hits[0]
    dom = set()
    for b in _BYTES:
        try:
            val = S._eval1(pred.term[4], ("uni", uni), b, model)
        except S._NotPure as exc:
            raise Refused("guard at $%04X is not a function of the byte" % pred.pc) from exc
        if _edge(pred.term, val) == dblk.pc:
            dom.add(b)
    floor = min(dom, default=0x100)
    if dom != set(range(floor, 0x100)):
        raise Refused("guard at $%04X admits %d non-contiguous bytes" % (pred.pc, len(dom)))
    return pair, floor


def _table(model, ana, dblk):
    """``(index var, live lo/hi table reads, table base addresses)``."""
    cells = ana._operand_cells(dblk, dblk.term[1])
    if cells is None:
        raise Refused("dispatch $%04X has no constant operand cells" % dblk.pc)
    writers = ana._pair_writers(cells)
    if len(writers) != 1:
        raise Refused("dispatch $%04X operand has %d writers" % (dblk.pc, len(writers)))
    key, pair = next(iter(writers.items()))
    wblk = model.blocks[key]
    alias, unis = ana._uni_alias(wblk), {}
    live = [
        (
            E.konst(model.mem0[c], 1)
            if pair.get(c) is None
            else ana._pair_live(wblk, pair[c], alias, unis)
        )
        for c in cells
    ]
    seen = set()
    for n in live:
        S._pair_vars(n, seen)
    if len(seen) != 1:
        raise Refused("dispatch $%04X indexes its tables by %d variables" % (dblk.pc, len(seen)))
    var = next(iter(seen))
    for n in live:
        if n[0] != "mem":
            raise Refused("dispatch $%04X reads an operand that is not a table" % dblk.pc)
    return var, live, tuple(ana._eval_pair(n[1], {var: 0}) for n in live)


def _handler(ana, live, var, index):
    """The arm the paired table selects for ``index``."""
    try:
        lo = ana._eval_pair(live[0], {var: index})
        hi = ana._eval_pair(live[1], {var: index})
    except S.DecompileError as exc:
        raise Refused("table read at index $%02X: %s" % (index, exc)) from exc
    return (lo & 0xFF) | ((hi & 0xFF) << 8)


def walk(model, site, handler, cache=None):
    """Walk one arm in stream-pointer terms, each block at its least ``Y``.

    Least-``Y`` order makes the reading independent of where the lifter cut the
    blocks: a block re-entered higher is a back edge, ``deltas`` are the net
    ``Y`` deltas where the arm stops counting, ``guards`` the fetches it branches on."""
    ana = model.analysis
    cache = {} if cache is None else cache
    seen, heap = {}, [(site.entry_y, handler)]
    offsets, deltas, backs, guards, edges, n = set(), set(), [], [], {}, 0
    while heap:
        y, pc = heapq.heappop(heap)
        if pc in seen:
            if seen[pc] != y:
                backs.append((pc, seen[pc], y))
            continue
        seen[pc] = y
        n += 1
        if n > _CAP:
            raise Refused("arm $%04X exceeds %d blocks" % (handler, _CAP))
        blk = _block(model, cache, pc)
        live = set()
        S._leaf_vars(blk.regs[2], live)
        here = [(p, _at(ye, y, model), u) for p, ye, u in _fetches(ana, blk) if _counts(ye)]
        foreign = [p for p, _o, _u in here if p != site.stream]
        if foreign:
            raise Refused("arm $%04X fetches stream $%02X" % (handler, foreign[0][0]))
        if ("reg", 2) not in live:
            if here:
                raise Refused("arm $%04X fetches where Y is not the counter" % handler)
            deltas.add(y - site.entry_y)
            continue
        offsets.update(o for _p, o, _u in here)
        if blk.term[0] == "br" and blk.term[2] is not None:
            guards += [(o, pc, u) for _p, o, u in here if S._uses(blk.term[4], u)]
        out = _at(blk.regs[2], y, model)
        moved = any(
            ev[0] == "st" and E.is_const(ev[1]) and ev[1][1] in site.stream for ev in blk.events
        )
        succs = [] if moved else _successors(blk)
        edges[pc] = succs
        if not succs:
            deltas.add(out - site.entry_y)
        for s in succs:
            heapq.heappush(heap, (out, s))
    return Walk(sorted(offsets), sorted(deltas), backs, guards, edges)


def _reaching(edges, head):
    """The blocks from which ``head`` is reachable, ``head`` included."""
    back = {}
    for pc, succs in edges.items():
        for s in succs:
            back.setdefault(s, []).append(pc)
    seen, work = {head}, [head]
    while work:
        for pc in back.get(work.pop(), ()):
            if pc not in seen:
                seen.add(pc)
                work.append(pc)
    return seen


def _escape(model, site, wk, cache):
    """The decoded length of a single counted-loop arm, else ``None``.

    The length is the first byte of the progression ``first + k*stride`` that
    leaves ``cont``, plus ``trailer``."""
    heads = {
        pc
        for pc, _lo, _hi in wk.backs
        if any(s in _reaching(wk.edges, pc) for s in wk.edges.get(pc, ()))
    }
    if len(heads) != 1 or len(wk.deltas) != 1:
        return None
    head = next(iter(heads))
    strides = {hi - lo for pc, lo, hi in wk.backs if pc == head}
    reach = _reaching(wk.edges, head)
    cands = sorted(g for g in wk.guards if any(s in reach for s in wk.edges.get(g[1], ())))
    if len(strides) != 1 or min(strides) <= 0 or not cands:
        return None
    first, pc, uni = cands[0]
    term = _block(model, cache, pc).term
    cont = frozenset(
        b for b in _BYTES if _edge(term, S._eval1(term[4], ("uni", uni), b, model)) in reach
    )
    if not cont:
        return None
    return Escape(first, min(strides), site.entry_y + wk.deltas[0] - first, cont)


def arm(model, site, op, cache=None):
    """Recover one operator: a constant arity, an escape, or a named refusal."""
    cache = {} if cache is None else cache
    handler = site.ops[op]
    try:
        wk = walk(model, site, handler, cache)
        if wk.backs:
            esc = _escape(model, site, wk, cache)
            if esc is None:
                return Arm(op, handler, None, None, None, "arm $%04X loops unboundedly" % handler)
            return Arm(op, handler, None, None, esc, None)
    except Refused as exc:
        return Arm(op, handler, None, None, None, str(exc))
    if len(wk.deltas) != 1:
        return Arm(op, handler, None, None, None, "net Y delta %s is not constant" % (wk.deltas,))
    n = max(wk.offsets, default=0)
    if wk.offsets != list(range(1, n + 1)):
        return Arm(
            op, handler, None, None, None, "fetches %s are not an operand run" % (wk.offsets,)
        )
    return Arm(op, handler, n, wk.deltas[0], None, None)


def sites(model):
    """Every SMC-operand dispatch site of the model, its operator table recovered.

    The sites are recovered together: the extent of one handler table is the
    displacement to the next, since the voice copies tile one region."""
    ana = model.analysis
    if ana is None:
        return ()
    found = {}
    for blk in model.blocks.values():
        if blk.term[0] == "jmpd" and blk.pcs[-1] not in found:
            found[blk.pcs[-1]] = blk
    raw = []
    for pc, blk in sorted(found.items()):
        try:
            var, live, bases = _table(model, ana, blk)
            stream, floor = _guarded_stream(model, ana, blk)
            entry_y = _at(blk.regs[2], min(ana._reg_set((blk.pc, blk.op0), 2), default=0), model)
        except Refused:
            continue
        raw.append((pc, var, live, bases, stream, floor, entry_y))
    marks = sorted({b for r in raw for b in r[3]})
    gaps = [b - a for a, b in zip(marks, marks[1:]) if b > a]
    if not gaps:
        return ()
    extent = min(gaps)
    out = []
    for pc, var, live, bases, stream, floor, entry_y in raw:
        try:
            ops = {b: _handler(ana, live, var, b) for b in range(floor, min(floor + extent, 0x100))}
        except Refused:
            continue
        out.append(Site(pc, stream, bases, entry_y, ops))
    return tuple(out)


def operators(model):
    """``(arities, escapes, refusals)`` over every dispatch site of the model.

    A family's voice copies are one operator set at three seats; an operator
    the copies disagree on is a refusal, not a majority vote."""
    cache, per = {}, {}
    for site in sites(model):
        for op in sorted(site.ops):
            per.setdefault(op, []).append(arm(model, site, op, cache))
    arities, escapes, refusals = {}, {}, {}
    for op, arms in sorted(per.items()):
        bad = [a.refusal for a in arms if a.refusal]
        shapes = {a.arity for a in arms}
        if bad:
            refusals[op] = bad[0]
        elif len(shapes) != 1:
            refusals[op] = "voice copies recover arities %s" % sorted(shapes)
        elif arms[0].arity is None:
            escs = {a.escape for a in arms}
            if len(escs) != 1:
                refusals[op] = "voice copies recover %d escapes" % len(escs)
            else:
                escapes[op] = arms[0].escape
        else:
            arities[op] = arms[0].arity
    return arities, escapes, refusals
