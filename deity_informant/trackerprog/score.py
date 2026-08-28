"""T2 -- the score: a cursor nest through pointer bases, materialised over the horizon.

A pattern channel reads rows at ``ptrtab[selector] + cursor``; the selector is a
per-voice constant, a state cell, or a byte of another channel -- the order. Depth
counts the pointer bases a row address goes through, and a nest deeper than two
is not cursor-shaped (prototype section 6). Materialisation runs the resolved
address over the horizon: one event per change of ``(base, cursor)`` per voice.
"""

from __future__ import annotations

from collections import namedtuple

import numpy as np

from ..tuneprog.ir import Bin, Const, Load, R16, Store, Var, W16, overlaps
from ..tuneprog.irwalk import addr_split
from ..tuneprog.accshape import terms
from .cursors import TABLE, _halves, basekind, decompose, istable, selector
from .resolve import Sel, free, walkx
from .refuse import Refusal

MAXDEPTH = 2
MAXROW = 8
MAXLEAVES = 64
Channel = namedtuple("Channel", "table cursor accs kind depth")
Event = namedtuple("Event", "tick ticks base pos end")


def depth(base, rgn, seen=0, bound=frozenset()):
    """How many pointer bases a base goes through; ``None`` for a shape no nest has."""
    kind = basekind(base, rgn, bound)
    if kind == "const":
        return 0
    if kind == "pair":
        return 1
    if kind != "ptrtab" or seen > MAXDEPTH:
        return None
    sel = selector(base, rgn)
    got = []
    for x in leaf_tables(sel, rgn):
        d = decompose(x.a, rgn)
        got.append(None if d is None else depth(d[0], rgn, seen + 1, bound))
    if any(d is None for d in got):
        return None
    return 1 + max(got, default=0)


def leaf_tables(e, rgn):
    """The table reads of ``e`` that are values, not parts of another read's address."""
    if e is None:
        return []
    ls = [x for x in walkx(e) if istable(x, rgn)]
    inner = {id(y) for x in ls for y in walkx(x.a)}
    return [x for x in ls if id(x) not in inner]


def _key(a):
    if a.cursor is not None:
        return ("cell", a.cursor.region, a.cursor.addr)
    if type(a.base) is R16:
        return ("pair", tuple(a.base.lo), tuple(a.base.hi))
    halves = _halves(a.base) if a.base is not None else None
    if halves is not None and all(type(x) is Load for x in halves):
        return (
            "pair",
            (halves[0].r, addr_split(halves[0].a)[0]),
            (halves[1].r, addr_split(halves[1].a)[0]),
        )
    return None


def channels(accs, rgn, bound=frozenset()):
    """Every table a cursor reads through, with its base kind and depth.

    One channel per cursor and table -- where two table regions overlap (a 1-based
    and a 2-based view of the same bytes) they are one table, and ``table`` is the
    lowest-based of them.
    """
    by = {}
    for a in accs:
        k = _key(a)
        if k is None:
            continue
        by.setdefault(k, []).append(a)
    out = []
    for k, group in sorted(by.items(), key=lambda kv: str(kv[0])):
        for run in overlaps([rgn[t] for t in {a.table for a in group}]):
            ids = {r.id for r in run}
            accs_ = [a for a in group if a.table in ids]
            kinds = {basekind(a.base, rgn, bound) for a in accs_}
            kind = "other" if len(kinds) > 1 else kinds.pop()
            d = max(
                (depth(a.base, rgn, 0, bound) for a in accs_), key=lambda x: -1 if x is None else x
            )
            out.append(Channel(run[0].id, k, accs_, kind, d))
    return out


def run_length(base, cur):
    """``[(tick, ticks, base, cursor)]``: one run per unchanged ``(base, cursor)``."""
    if base.size == 0:
        return []
    key = base.astype(np.int64) * (1 << 24) + cur
    at = np.concatenate(([0], np.nonzero(key[1:] != key[:-1])[0] + 1, [key.size]))
    return [(int(s), int(e - s), int(base[s]), int(cur[s])) for s, e in zip(at[:-1], at[1:])]


def visits(runs):
    """Consecutive runs of one base: ``[[run, ...], ...]``."""
    out = []
    for r in runs:
        if out and out[-1][-1][2] == r[2]:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def terminator(img, vs, byte):
    """``(byte, {visit index: offset})``: where each ended visit meets the terminator."""
    if byte is None:
        return None, {}
    offs = {}
    for i, v in enumerate(vs[:-1]):
        b, top = v[0][2], max(r[3] for r in v)
        hit = next((j for j in range(MAXROW + 1) if img[b + top + j] == byte), None)
        if hit is not None:
            offs[i] = hit
    return byte, offs


def leaves(e, gs=()):
    """``[(guards, value)]``: every alternative a resolved value can take.

    A selection inside an operator or an address distributes over it, so every
    leaf is one selection-free expression under the guards that pick it.
    """
    t = type(e)
    if t is Sel:
        out = []
        for g, x in e.alts:
            out += leaves(x, gs + tuple(g))
        return out
    if t is Bin:
        return [
            (ga + gb, Bin(e.op, a, b, e.w)) for ga, a in leaves(e.a) for gb, b in leaves(e.b, gs)
        ][:MAXLEAVES]
    if t is Load:
        return [(ga, Load(e.cls, a, e.w, e.lo, e.hi, e.r)) for ga, a in leaves(e.a, gs)]
    return [(gs, e)]


def terminators(chan, P, rgn):
    """The bytes the cursor's reset stores compare the channel's table against."""
    tables = {a.table for a in chan.accs}
    cells = {chan.cursor[1:]} if chan.cursor[0] == "cell" else set(chan.cursor[1:])
    out = set()
    for pn, lbl, i, s in stores(P.ctx.prog, cells):
        for gs, v in P.resolve(pn, lbl, i, s.e if type(s) is W16 else s.v):
            for lg, leaf in leaves(v, gs):
                if not _reset(leaf, cells, rgn):
                    continue
                for c, t, *_w in lg:
                    got = _compared(c, t, tables, rgn)
                    if got is not None:
                        out.add(got)
    return out


def _reset(leaf, cells, rgn):
    """A value that reloads a cursor: a constant or a table entry, not its own step."""
    if type(leaf) is Const:
        return True
    for _sign, t in terms(leaf):
        x = t.a if type(t) is Bin and t.op == "<<" else t
        if type(x) is Load and (x.r, addr_split(x.a)[0]) in cells:
            return False
    reads = [y for y in walkx(leaf) if type(y) is Load and y.r in rgn]
    return any(rgn[y.r].kind in TABLE for y in reads)


def stores(prog, cells):
    """``[(proc, label, index, statement)]`` writing any of ``cells`` (region, base address)."""
    out = []
    for pn, p in prog.procs.items():
        for lbl, b in p.blocks.items():
            for i, s in enumerate(b.stmts):
                if type(s) is Store and s.r >= 0 and (s.r, addr_split(s.a)[0]) in cells:
                    out.append((pn, lbl, i, s))
                elif type(s) is W16 and ({tuple(s.lo), tuple(s.hi)} & cells):
                    out.append((pn, lbl, i, s))
    return out


def stepping(P, rgn, cell):
    """True when some store moves a cursor cell from its own value, callers substituted."""
    rid, addr = cell
    for pn, lbl, i, s in stores(P.ctx.prog, {cell}):
        for _gs, v in P.resolve(pn, lbl, i, s.e if type(s) is W16 else s.v):
            for x in walkx(v):
                if type(x) is Load and x.r == rid and addr_split(x.a)[0] == addr:
                    return True
    return False


def pointers(chan, rgn, names):
    """The pointer tables a channel's base is read from, with their entry count."""
    base = chan.accs[0].base
    halves = _halves(base) if base is not None else None
    if halves is None or not all(type(x) is Load and x.r in rgn for x in halves):
        return None
    return {
        "tables": [names.of(x.r) for x in halves],
        "entries": min(-(-rgn[x.r].size // max(rgn[x.r].stride, 1)) for x in halves),
    }


def fed(chan, P, rgn):
    """The state cells a pattern channel's selector reads, each with what fills it.

    ``[(cell, ok, sites, tables)]``: ``ok`` where every store into the cell reads a
    table -- the order -- or steps the cell itself; not where some store gives it a
    value no table supplies (a call's return the fold erased), which is a score
    that is not cursor-shaped.
    """
    sel = selector(chan.accs[0].base, rgn)
    cells = set()
    for x in walkx(sel, False) if sel is not None else ():
        if type(x) is Load and x.r in rgn and rgn[x.r].kind == "state":
            base = addr_split(x.a)[0]
            if base is not None:
                cells.add((x.r, base))
    out = []
    for cell in sorted(cells):
        ok, sites, tables = True, [], set()
        for pn, lbl, i, s in stores(P.ctx.prog, {cell}):
            for _gs, v in P.resolve(pn, lbl, i, s.e if type(s) is W16 else s.v):
                for _lg, leaf in leaves(v):
                    reads = {y.r for y in walkx(leaf) if type(y) is Load and y.r in rgn}
                    own = any(
                        y.r == cell[0] and addr_split(y.a)[0] == cell[1]
                        for y in walkx(leaf)
                        if type(y) is Load
                    )
                    got = {r for r in reads if rgn[r].kind in TABLE}
                    tables |= got
                    # a name a call returned is a value the fold erased; a name a loop
                    # carries is one the resolver left, and says nothing either way
                    erased = any(x.n in P.of(pn).rets for x in walkx(leaf) if type(x) is Var)
                    opaque = bool(reads) and not got and not own and not free(leaf)
                    if erased or opaque:
                        ok, sites = False, sites + [pn]
        out.append((cell, ok, sorted(set(sites)), tables))
    return out


def _compared(c, t, tables, rgn):
    """The constant a condition holds a table read equal to, or ``None``.

    ``read == k`` taken, ``read != k`` not taken, and ``read < k`` not taken with
    ``k`` the byte's top value (GoatTracker 2's ``t3 < $FF``) all pin the byte.
    """
    if type(c) is not Bin or c.op not in ("==", "!=", "<"):
        return None
    if c.op == "<":
        if t or type(c.b) is not Const or c.b.v != 0xFF:
            return None
        return c.b.v if any(y.r in tables for y in leaf_tables(c.a, rgn)) else None
    if (c.op == "==") != bool(t):
        return None
    for x, k in ((c.a, c.b), (c.b, c.a)):
        if type(k) is Const and any(y.r in tables for y in leaf_tables(x, rgn)):
            return k.v
    return None


def _ffill(v, ran):
    """``v`` held from the last tick ``ran``; before the first, the first such value."""
    if not ran.any():
        return None
    idx = np.where(ran, np.arange(v.size), -1)
    idx = np.maximum.accumulate(idx)
    first = int(np.argmax(ran))
    return v[np.where(idx < 0, first, idx)]


def materialise(ev, cells, chan, env, shift_col=None, term=None):
    """One voice's events on one channel: ``(events, terminator byte, bad ticks)``.

    The cursor's samples are post-tick, with the post-init image as the sample
    before tick 0. Each change of ``(base, cursor)`` is a fetch: the row
    ``[pos, end)`` of the pattern at ``base`` the cursor stood on, sounding for
    ``ticks`` from ``tick``. A visit's top row ends at the terminator byte; a move
    back inside a pattern fetched nothing (``end == pos``).
    """
    a = chan.accs[0]
    ev.bad[:] = False
    base = ev.value(a.base, env) if a.base is not None else np.zeros(ev.ticks, np.int64)
    if base is not None:
        base = base + min(x.origin for x in chan.accs)  # the lowest column's own address
    if chan.cursor[0] == "cell":
        col = shift_col if shift_col is not None else 0
        cur = cells.col(chan.cursor[1], chan.cursor[2] + col)
        cur = None if cur is None else cur << a.shift
        if base is not None and cur is not None and any(type(x) is Sel for x in walkx(a.base)):
            # a base through a scratch alternative is what the fetch read: the value
            # on the ticks the cursor moved, held between them
            ran = np.concatenate(([True], cur[1:] != cur[:-1])) & ~ev.bad
            base = _ffill(base, ran)
            ev.bad &= ran
        if cur is not None:
            cur = np.concatenate(([int(cells.img[chan.cursor[2] + col]) << a.shift], cur))
    else:
        cur, base = base, np.zeros(ev.ticks, np.int64)
        if cur is not None:
            lo, hi = chan.cursor[1], chan.cursor[2]
            cur = np.concatenate(([int(cells.img[lo[1]]) | (int(cells.img[hi[1]]) << 8)], cur))
    if base is None or cur is None:
        return None, None, None
    base = np.concatenate(([base[0]], base))
    vs = visits(run_length(base, cur))
    byte, offs = terminator(cells.img, vs, term)
    runs = [r for v in vs for r in v]
    top = {i: max(r[3] for r in v) for i, v in enumerate(vs)}
    at = [i for i, v in enumerate(vs) for _r in v]  # each run's visit
    events = []
    if runs[0][1] > 1:  # the row init fetched, sounding until the first move
        events.append(Event(0, runs[0][1] - 1, runs[0][2], runs[0][3], runs[0][3]))
    for k, ((_t, _n, b, p), (t, n, b2, p2)) in enumerate(zip(runs, runs[1:])):
        i = at[k]
        if b2 == b and p2 > p:
            events.append(Event(t - 1, n, b, p, p2))
            continue
        # the visit's top row closes at the terminator; a move back fetched nothing
        end = p + offs[i] if i in offs and p == top[i] else p
        if b2 != b and p2 > 0:  # the new pattern's first row, fetched in the same tick
            if end > p:
                events.append(Event(t - 1, 0, b, p, end))
            events.append(Event(t - 1, n, b2, 0, p2))
        else:
            events.append(Event(t - 1, n, b, p, end))
    return events, byte, ev.bad.copy()


def classify(chans, rgn, names, P, skip=frozenset()):
    """``(order channels, pattern channels, refusals)`` among the score channels.

    A pattern channel steps its cursor through a pointer base an order picks: its
    order channels are the tables its selector reads, directly or through the
    state cells the selector's stores fill from them. A pointer-based channel no
    order feeds is a stream, one whose cursor never steps a selector; neither is
    the score. ``skip`` holds the pitch table's regions.
    """
    order, pattern, refused = [], [], []
    bytable = {}
    for c in chans:
        bytable.setdefault(c.table, []).append(c)
    for c in chans:
        if c.kind == "const" or c.table in skip or c.cursor[0] != "cell":
            continue
        if not stepping(P, rgn, c.cursor[1:]):
            continue
        feeds = feeders(c, P, rgn)
        if not feeds:
            continue
        if c.kind == "other" or c.depth is None or c.depth > MAXDEPTH:
            refused.append(
                Refusal(
                    "score not cursor-shaped",
                    _cellname(c, names),
                    c.accs[0].site[0],
                    "base %s, depth %s" % (c.kind, c.depth),
                )
            )
            continue
        pattern.append(c)
        for t in feeds:
            order += [o for o in bytable.get(t, ()) if o not in order]
    pattern = [c for c in pattern if c not in order]
    return order, pattern, refused


def feeders(chan, P, rgn):
    """The table regions a channel's selector reads, itself or through a state cell."""
    sel = selector(chan.accs[0].base, rgn)
    if sel is None:
        return set()
    out = {x.r for x in leaf_tables(sel, rgn)}
    for _cell, _ok, _sites, tables in fed(chan, P, rgn):
        out |= tables
    return out


def _cellname(c, names):
    if c.cursor[0] == "cell":
        return "%s@$%04X" % (names.of(c.cursor[1]), c.cursor[2])
    return "%s:%s" % (names.of(c.cursor[1][0]), names.of(c.cursor[2][0]))
