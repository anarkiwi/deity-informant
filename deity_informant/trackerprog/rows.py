"""B7 -- one segment of the certified tick read as guarded rows over named cells.

A block is one row.  Where a name the block reads is bound on more than one path
the row is split, one row per binding; where a store of the segment takes away
what a later guard reads, the row that guard carries stands before it; and where
a value was read one statement before a store moved the cell it reads, that
assignment stands before the store.  Nothing here is a lowering: every target is
a named cell, an instrument-scoped pair or a register.
"""

from __future__ import annotations

import itertools

from ..tuneprog.ir import Let, Load, Store, Var
from ..tuneprog.irwalk import addr_split, walk
from .read import Unlowerable
from .shape import _reads

MAXCOMBO = 24


def ambiguous(proc):
    """``{name: {block: value}}`` for every SSA name more than one block binds."""
    out = {}
    for lbl, b in proc.blocks.items():
        for s in b.stmts:
            if type(s) is Let:
                out.setdefault(s.n, {})[lbl] = s.e
    return {n: v for n, v in out.items() if len(v) > 1}


def _consistent(terms):
    """Whether guard terms can hold together: no condition under both truths."""
    seen = {}
    for _d, c, t in terms:
        if seen.setdefault(id(c), t) != t:
            return False
    return True


class Rows:
    """One segment read as guarded rows over the object's own cells.

    A block is one row.  Where a name the block reads is bound on more than one
    path the row is split -- one row per binding, under the guard of the block
    that bound it -- which is what keeps an SSA temp out of the object.
    """

    def __init__(self, low, amb):
        self.low, self.amb = low, amb

    def needs(self, lbl, drop=()):
        """The ambiguous names one block's own surviving stores read, after expansion."""
        low = self.low
        low.lbl, low.local, low.turn, low.pick = lbl, {}, None, {}
        out = []
        for s in low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.src in drop:
                continue
            for e in (s.v, s.a):
                for x in walk(low.expand(e)):
                    if type(x) is Var and x.n in self.amb and x.n not in low.v.vidx:
                        if x.n not in out:
                            out.append(x.n)
        return out

    def bindings(self, lbl, guard, drop=()):
        """``[(extra guard, {name: block})]``: the paths one block's row is read on."""
        names = self.needs(lbl, drop)
        if not names:
            return [((), {})]
        choice = [
            [(tuple(self.low.eff.get(d, ((), ()))[0]), d) for d in self.amb[n]] for n in names
        ]
        out = []
        for combo in itertools.islice(itertools.product(*choice), MAXCOMBO):
            gs = list(guard)
            for g, _d in combo:
                gs += [t for t in g if t not in gs]
            if not _consistent(gs):
                continue
            extra = tuple(t for t in gs if t not in guard)
            out.append((extra, {n: d for n, (_g, d) in zip(names, combo)}))
        return out

    def when(self, guard):
        """One row's guard: the block's own path terms, each read at its own site."""
        low, out = self.low, []
        for d, c, t in guard:
            if not low.onpath(d, c, t):
                continue
            low.lbl = d
            fact = low.v.terms.get(repr(c)) if low.v.payload else None
            got = [fact, "!=" if t else "==", 0] if fact is not None else low.term(low.expand(c), t)
            if got not in out:
                out.append(got)
        return out

    def sets(self, lbl, drop):
        """One block's stores, each as a ``sets`` assignment over the object's cells."""
        low, out = self.low, []
        for s in low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.src in drop:
                continue
            got = low.v.target(low, s)
            if got is None:
                continue
            name = "@" + got[1] if got[0] == "copy" else got[1]
            out.append([name, low.value(low.expand(s.v))])
        return out


def _epoch(stmts, got):
    """One block's stores in an order a row's own ``sets`` can be run in.

    A store whose value was *read* before a later store moved the cell it reads
    stands before that store: the IR names the read where it happened, and a row
    has no channel for a value read one statement earlier.
    """
    pos = {s.n: i for i, s in enumerate(stmts) if type(s) is Let}
    out = list(got)
    for _ in range(len(out) * len(out) + 1):
        for a, x in enumerate(out):
            i = x[0]
            b = next((k for k in range(a) if _before(stmts, i, out[k][0], pos)), None)
            if b is not None:
                out.insert(b, out.pop(a))
                break
        else:
            return out
    return out


def _deps(stmts, i, pos):
    """``{address: the statement its value was read at}`` for one store's value."""
    out, seen, stack = {}, set(), [(stmts[i].v, i)]
    while stack:
        e, at = stack.pop()
        for x in walk(e):
            if type(x) is Load:
                b = addr_split(x.a)[0]
                if b is not None:
                    out[b] = min(out.get(b, at), at)
            elif type(x) is Var and x.n in pos and x.n not in seen:
                seen.add(x.n)
                stack.append((stmts[pos[x.n]].e, pos[x.n]))
    return out


def _before(stmts, i, j, pos):
    """Whether store ``i`` must stand before store ``j`` in one row's ``sets``."""
    if type(stmts[j]) is not Store or stmts[j].cls == "io":
        return False
    base = addr_split(stmts[j].a)[0]
    if base is None:
        return False
    got = _deps(stmts, i, pos).get(base)
    return got is not None and got < j


def _carried(low, c):
    """Whether a guard term reads a name more than one block of the tick binds."""
    low.lbl, low.local, low.pick = None, {}, {}
    return any(
        type(x) is Var and x.n not in low.defs and x.n not in low.v.vidx
        for x in walk(low.expand(c))
    )


def _copies(low, got):
    """Fold the copies of one per-voice cell a block writes at constant addresses.

    A value every copy takes is one write every voice makes (§3.6's ``all``); a
    copy that is neither the committing voice's nor one of a full set is no cell.
    """
    at = {}
    for k, x in enumerate(got):
        pair = x[2]
        if pair is not None and isinstance(pair[0], tuple):
            at.setdefault(pair[0][0], []).append(k)
    out, drop = list(got), set()
    for name, ks in at.items():
        vals = {got[k][2][0][1]: repr(got[k][2][1]) for k in ks}
        full = len(vals) == low.cells.voices and len(set(vals.values())) == 1
        for j, k in enumerate(ks):
            put = ("*" if full else "@") + name
            if full and j:
                drop.add(k)
                continue
            if not full and got[k][2][0][1]:
                drop.add(k)
                low.bad.add(name)
                continue
            sub = got[k][3]
            node = {"cell": name}
            out[k] = (
                got[k][0],
                got[k][1],
                [put, got[k][2][1]],
                None if sub is None else (sub[0], node),
            )
    return [x for k, x in enumerate(out) if k not in drop]


KEEP = (
    "rowsleft",
    "dur",
    "note",
    "ins",
    "freq",
    "orderpos",
    "tied",
    "phase",
    "counter",
    "voice_index",
    "lastnote",
    "wave",
)


def _staged(seq, order, facts):
    """One segment's rows in an order their own guards can be read in.

    A guard the tick decided before a store of the segment is read at the row it
    guards, so a row whose guard reads a cell an earlier row writes -- and whose
    guard was decided before that row's own block -- stands before it.
    """
    at = {l: i for i, l in enumerate(order)}
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        for j, step in enumerate(out):
            reads, dec = facts(step)
            if not reads or not dec:
                continue
            i = next(
                (
                    i
                    for i in range(j)
                    if out[i][2]
                    and reads & {x[0].lstrip("@#!*") for x in out[i][2]}
                    and out[i][3] not in dec
                    and max(at.get(d, 0) for d in dec) < at.get(out[i][3], 0)
                    and at.get(out[i][3], 0) <= at.get(step[3], 0)
                ),
                None,
            )
            if i is not None:
                out.insert(i, out.pop(j))
                break
        else:
            return out
    return out


def steps(b, lbl, drop, roles, guard, extra, split=False):
    """One block as ordered steps: its role stores, its cells, its registers.

    The order is the block's own, with one exception the schema has no other
    channel for: an assignment whose value was *read* before a later store
    moved the cell it reads stands before that store, since a row's ``sets``
    run in the order they are written.  A value the row has since stored is
    read as the cell it left it in, for the same reason.
    """
    low, out, keep = b.low, [], []
    stmts = low.proc.blocks[lbl].stmts
    for i, s in enumerate(stmts):
        if type(s) is not Store:
            continue
        role = roles.get(s.src)
        if role is not None:
            keep.append((i, role, None, None))
            continue
        if s.src in drop:
            continue
        tgt = low.v.target(low, s)
        if tgt is not None:
            keep.append((i, "reg" if tgt[0] == "reg" else "set", tgt, s))
    put = {
        (t[1][0] if t[0] == "copy" else str(t[1])).lstrip("@#!*")
        for _i, _k, t, _s in keep
        if t is not None
    }
    sub, got = {}, []
    for i, kind, tgt, s in _epoch(stmts, keep):
        if tgt is None:
            got.append((i, kind, None, None))
            continue
        low.sub = dict(sub)
        val = low.value(low.expand(s.v))
        low.sub = {}
        name = tgt[1] if tgt[0] != "acc" else "@" + str(tgt[1])
        hit = None
        if s.cls == "ram" and tgt[0] in ("cell", "acc"):
            nm = str(name).lstrip("@")
            node = {"global": nm[1:]} if nm[:1] == "#" else {"cell": nm}
            if _reads(val) & put:
                sub[repr(low.expand(s.v))] = node
                hit = (repr(low.expand(s.v)), node)
        got.append((i, kind, [name, val], hit))
    for _i, kind, pair, hit in _copies(low, got):
        key = kind if split or pair is None else "set"
        if out and out[-1][0] == key and key == "set" and pair is not None:
            out[-1][2].append(pair)
            out[-1][4].append(hit)
        else:
            out.append([key, guard + extra, None if pair is None else [pair], lbl, [hit]])
    return out


def blockrows(b, blocks, order, drop, roles, split=False):
    """One segment as ordered steps in program order, split where a path binds."""
    low, R, out = b.low, Rows(b.low, b.amb), []
    for lbl in [l for l in order if l in blocks]:
        if not any(type(s) is Store for s in low.proc.blocks[lbl].stmts):
            continue
        guard = tuple(low.eff.get(lbl, ((), ()))[0])
        for extra, pick in R.bindings(lbl, guard, drop):
            low.pick = {n: b.amb[n][d] for n, d in pick.items()}
            low.lbl, low.local, low.turn, low.sub = lbl, {}, None, {}
            try:
                out += steps(b, lbl, drop, roles, guard, extra, split)
            except Unlowerable as x:
                low.bad.add("%s: %s" % (lbl, x))
    low.pick = {}
    return out


def guards(b, got, order):
    """Each step's guard, read where the staged order puts the row that carries it.

    A value an earlier row of the segment has since stored is that cell where
    the guard reads it, which is what keeps a row's own guard exact without a
    cell for the epoch.
    """
    low, out, sub = b.low, [], {}
    for kind, guard, pairs, lbl, subs in _staged(got, order, lambda step: guardfacts(b, step)):
        low.sub = {}
        when, dec = [], set()
        for d, c, t in guard:
            if not low.onpath(d, c, t):
                continue
            low.lbl, low.sub = d, dict(sub, **stored(b, d))
            fact = low.v.terms.get(repr(c))
            term = (
                [fact, "!=" if t else "==", 0] if fact is not None else low.term(low.expand(c), t)
            )
            if term not in when:
                when.append(term)
                dec.add(d)
        out.append((lbl, kind, when, pairs, frozenset(dec)))
        # a value the row itself takes the inputs of away: the cell it left it
        # in is where a later guard reads it, and no other value moved
        put = {x[0].lstrip("@#!*") for x in (pairs or ())}
        for x, pair in zip(subs, pairs or []):
            if x is not None and _reads(pair[1]) & put:
                sub[x[0]] = x[1]
    low.sub = {}
    return out


def stored(b, lbl):
    """``{a value the block stored: the cell it left it in}``, as the row reads it."""
    low, out = b.low, {}
    if lbl is None or lbl not in low.proc.blocks:
        return out
    low.lbl, low.sub = lbl, {}
    for s in low.proc.blocks[lbl].stmts:
        if type(s) is not Store or s.cls != "ram" or s.src in low.v.dropstores:
            continue
        base = addr_split(s.a)[0]
        # a counter alone: a value that is its own cell's is what a later row
        # has no older epoch of, and a copy is readable where it was copied from
        if base is None or not low.selfread(s.v, base):
            continue
        try:
            tgt = low.v.target(low, s)
        except Unlowerable:
            continue
        if tgt is None or tgt[0] != "cell":
            continue
        name = tgt[1]
        node = {"global": name[1:]} if name[:1] == "#" else {"cell": name.lstrip("@")}
        out[repr(low.expand(s.v))] = node
    return out


def guardfacts(b, step):
    """``(the cells one step's guard reads, the blocks that decide it)``."""
    low, reads, dec = b.low, set(), set()
    for d, c, t in step[1]:
        if not low.onpath(d, c, t):
            continue
        low.lbl, low.sub = d, stored(b, d)
        try:
            reads |= _reads(low.term(low.expand(c), t))
        except Unlowerable:
            pass
        dec.add(d)
    low.sub = {}
    return reads, frozenset(dec)
