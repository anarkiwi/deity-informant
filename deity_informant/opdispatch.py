"""Recover a script-VM operator set from an SMC-operand dispatch's arms.

The subject is a shape and no driver family: a ``jmpd`` whose operand cells one
paired lo/hi handler table writes, guarded on a byte the stream pointer's own
fetch supplies. Stream, operator range, name, arity and writes are read off it.
"""

import heapq
from collections import namedtuple

from . import expr as E
from . import grammar as G
from . import structured as S

_CAP = 256  # blocks per arm: a `stop` arm runs out through the tick-end paths
_BYTES = range(0x100)
_SID = range(0xD400, 0xD419)

Site = namedtuple("Site", "pc stream tables entry_y ops floor other")
Arm = namedtuple("Arm", "op handler arity delta escape refusal")
Walk = namedtuple("Walk", "offsets deltas advances backs guards edges")
Escape = namedtuple("Escape", "first stride trailer cont")
Operator = namedtuple("Operator", "name arity repeat writes")


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


def _transparent(model, cache, blk, handler, stream):
    """Refuse unless the call ``blk`` makes cannot change what its caller consumes.

    A callee that neither fetches the arm's stream nor moves the counter leaves
    the arm's operand run exactly as it found it, so the arm is read through the
    call; one that might is refused rather than skipped."""
    target = blk.term[1]
    if target is None:
        raise Refused("arm $%04X calls a target it computes" % handler)
    seen, work = set(), [target]
    while work:
        pc = work.pop()
        if pc in seen:
            continue
        seen.add(pc)
        if len(seen) > _CAP:
            raise Refused("callee $%04X of arm $%04X exceeds %d blocks" % (target, handler, _CAP))
        sub = _block(model, cache, pc)
        if sub.regs[2] != ("reg", 2):
            raise Refused("callee $%04X of arm $%04X moves the counter" % (target, handler))
        if any(p == stream for p, _ye, _u in _fetches(model.analysis, sub)):
            raise Refused("callee $%04X of arm $%04X fetches the stream" % (target, handler))
        work.extend(_successors(sub))


def _arm_succs(model, cache, blk, handler, stream):
    """The arm's successors, reading through a call that is transparent to it."""
    if blk.term[0] == "jsr":
        _transparent(model, cache, blk, handler, stream)
        return [(blk.term[2] + 1) & 0xFFFF]
    return _successors(blk)


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
    """``(pair, floor, else-arm)``: the dispatch's stream, command floor and datum arm.

    The byte values whose guard edge lands on the dispatch are the operator index
    domain and must be an upper range of the byte; the rest are the stream's own
    datum form, which is that one other edge."""
    pred = _pred(model, dblk)
    if pred.term[0] != "br" or pred.term[2] is None:
        raise Refused("dispatch $%04X is not reached through a byte guard" % dblk.pc)
    hits = _fetches(ana, pred)
    if len(hits) != 1:
        raise Refused("dispatch $%04X fetches %d stream bytes" % (dblk.pc, len(hits)))
    pair, _ye, uni = hits[0]
    dom, rest = set(), set()
    for b in _BYTES:
        try:
            val = S._eval1(pred.term[4], ("uni", uni), b, model)
        except S._NotPure as exc:
            raise Refused("guard at $%04X is not a function of the byte" % pred.pc) from exc
        edge = _edge(pred.term, val)
        if edge == dblk.pc:
            dom.add(b)
        else:
            rest.add(edge)
    floor = min(dom, default=0x100)
    if dom != set(range(floor, 0x100)):
        raise Refused("guard at $%04X admits %d non-contiguous bytes" % (pred.pc, len(dom)))
    return pair, floor, next(iter(rest)) if len(rest) == 1 else None


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
    ``Y`` deltas where the arm stops counting, ``guards`` the fetches it branches on.
    ``advances`` are the cursor advances the arm's own stream stores make, read off
    the store rather than inferred from ``Y``: an arm whose add carries in moves the
    cursor one further than its counter went."""
    ana = model.analysis
    cache = {} if cache is None else cache
    seen, heap = {}, [(site.entry_y, handler)]
    offsets, deltas, backs, guards, edges, n = set(), set(), [], [], {}, 0
    advances = set()
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
        moved = [
            ev
            for ev in blk.events
            if ev[0] == "st" and E.is_const(ev[1]) and ev[1][1] in site.stream
        ]
        succs = [] if moved else _arm_succs(model, cache, blk, handler, site.stream)
        edges[pc] = succs
        if not succs:
            deltas.add(out - site.entry_y)
        advances.update(
            _advance(model, site, blk, ev, y) for ev in moved if ev[1][1] == site.stream[0]
        )
        for s in succs:
            heapq.heappush(heap, (out, s))
    return Walk(sorted(offsets), sorted(deltas), sorted(advances - {None}), backs, guards, edges)


_PROBES = (0x00, 0x10)  # two cursor values: an advance is the same move from either


def _advance(model, site, blk, ev, y):
    """How far the arm's own store moves the cursor's low lane, else None.

    The move is read off the store rather than inferred from ``Y``: an add that
    carries in advances the cursor one further than the counter went. It is an
    advance only where it is the same move from every cursor value, so the lane
    is bound to two and a value that depends on it is no reading."""
    ana = model.analysis
    try:
        alias, unis = ana._uni_alias(blk), {}
        live = ana._pair_live(blk, ev[2], alias, unis)
        got = set()
        for probe in _PROBES:
            env = {("reg", 2): y}
            for u, cell in unis.items():
                env[("uni", u)] = probe if cell == site.stream[0] else model.mem0[cell]
            got.add((ana._eval_pair(live, env) - probe) & 0xFF)
    except (S.DecompileError, KeyError):
        return None
    return got.pop() if len(got) == 1 else None


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
    leaves ``cont``, plus ``trailer`` -- and the trailer is what the arm's own
    cursor store adds past that byte, never the counter's net delta, which an
    add that carries in leaves one short."""
    heads = {
        pc
        for pc, _lo, _hi in wk.backs
        if any(s in _reaching(wk.edges, pc) for s in wk.edges.get(pc, ()))
    }
    if len(heads) != 1 or len(wk.advances) != 1:
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
    return Escape(first, min(strides), wk.advances[0] - first, cont)


def arm(model, site, op, cache=None):
    """Recover one operator: a constant arity, an escape, or a named refusal."""
    return _arm_at(model, site, op, site.ops[op], cache)


def default_arm(model, site, cache=None):
    """The arm every stream byte below the operator floor takes, else None.

    The bytes the dispatch's guard sends elsewhere are the stream's own datum
    form, and its arity is read off that arm exactly as an operator's is."""
    return None if site.other is None else _arm_at(model, site, None, site.other, cache)


def _arm_at(model, site, op, handler, cache=None):
    cache = {} if cache is None else cache
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
    displacement to the next, since the seats' tables tile one region."""
    ana = getattr(model, "analysis", None)  # a hand-built block model carries none
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
            stream, floor, other = _guarded_stream(model, ana, blk)
            entry_y = _at(blk.regs[2], min(ana._reg_set((blk.pc, blk.op0), 2), default=0), model)
        except Refused:
            continue
        raw.append((pc, var, live, bases, stream, floor, entry_y, other))
    marks = sorted({b for r in raw for b in r[3]})
    gaps = [b - a for a, b in zip(marks, marks[1:]) if b > a]
    if not gaps:
        return ()
    extent = min(gaps)
    out = []
    for pc, var, live, bases, stream, floor, entry_y, other in raw:
        try:
            ops = {b: _handler(ana, live, var, b) for b in range(floor, min(floor + extent, 0x100))}
        except Refused:
            continue
        out.append(Site(pc, stream, bases, entry_y, ops, floor, other))
    return tuple(out)


def _row_base(addr):
    """The constant base of an indexed store address, else None."""
    while addr[0] == "op" and addr[1] == "INT_ZEXT":
        addr = addr[2][0]
    if addr[0] == "op" and addr[1] == "INT_ADD" and len(addr[2]) == 2:
        got = [k[1] & 0xFFFF for k in addr[2] if E.is_const(k)]
        if len(got) == 1:
            return got[0]
    return None


def _store_cells(model, cache, wk, handler):
    """The cells an arm's own blocks assign, as the artifact names them.

    A store the arm computes the target of is not a cell, and a writes set that
    cannot name what it wrote is no reading of the arm, so it refuses instead."""
    out = set()
    for pc in wk.edges:
        for ev in _block(model, cache, pc).events:
            if ev[0] != "st":
                continue
            if E.is_const(ev[1]):
                out.add(G.addr_name(ev[1][1] & 0xFFFF))
                continue
            base = _row_base(ev[1])
            if base is None:
                raise Refused("arm $%04X stores to a computed address" % handler)
            out.add(G.VIEW if base in _SID else G.addr_name(base))
    return out


def _repeat(esc):
    """``(lo, hi, at, tail)`` of a decoded-length run, or None if it is unspellable.

    The run continues while the byte at ``at + k*stride`` stays in ``lo..hi``; a
    guard set that is not one range is no span, and the operator carries none."""
    lo, hi = min(esc.cont), max(esc.cont)
    if esc.cont != frozenset(range(lo, hi + 1)) or esc.trailer < 0:
        return None
    return (lo, hi, esc.first, esc.trailer)


def operator_set(model, names=None):
    """``{opcode: Operator}``: the operator set the model's dispatch sites declare.

    The name is the handler the paired tables select (``names`` gives the image's
    label for it, else its address), the arity the operand bytes the arm consumes
    and ``repeat`` the run a decoded-length arm's guard byte continues; the writes
    are the cells the arm's own blocks assign. Tiled copies of a dispatch are one
    set at several seats: the name is the first seat's, the writes their union."""
    try:
        arities, escapes, refusals = operators(model)
    except (Refused, S.DecompileError):
        return {}
    cache, seats, writes = {}, {}, {}
    for site in sites(model):
        for op in sorted(site.ops):
            if op in refusals:
                continue
            handler = site.ops[op]
            seats.setdefault(op, handler)
            try:
                got = _store_cells(model, cache, walk(model, site, handler, cache), handler)
            except (Refused, S.DecompileError) as exc:
                refusals[op] = str(exc)
                continue
            writes.setdefault(op, set()).update(got)
    out = {}
    for op, handler in sorted(seats.items()):
        rep = None
        if op in refusals:
            continue
        if op in escapes:
            rep = _repeat(escapes[op])
            if rep is None:
                continue
            arity = escapes[op].stride
        else:
            arity = arities[op]
        name = (names or {}).get(handler) or "h_%04X" % handler
        out[op] = Operator(name, arity, rep, tuple(sorted(writes.get(op, ()))))
    return out


def operators(model):
    """``(arities, escapes, refusals)`` over every dispatch site of the model.

    Tiled copies of one dispatch are one operator set at several seats; an
    operator the seats disagree on is a refusal, not a majority vote."""
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
            refusals[op] = "dispatch seats recover arities %s" % sorted(shapes)
        elif arms[0].arity is None:
            escs = {a.escape for a in arms}
            if len(escs) != 1:
                refusals[op] = "dispatch seats recover %d escapes" % len(escs)
            else:
                escapes[op] = arms[0].escape
        else:
            arities[op] = arms[0].arity
    return arities, escapes, refusals
