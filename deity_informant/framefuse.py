"""framefuse: rung (d) of the lift ladder, 16-bit fusion (docs/frameprog.md 4).

A pair fuses on evidence the model already carries: ``datadecl``'s pointer pairs,
the paired-index zip closure of a dispatch word, or the SID lo/hi registers the
canonical section emits adjacent. One lone-half access refuses that pair alone.
"""

from __future__ import annotations

from bisect import bisect_right

from . import datadecl
from . import expr as E
from . import frameproc
from . import grammar as G
from . import streams as ST
from .structured import Proof

_SID_LO = 0xD400
_SID_HI = 0xD41C  # the last register the frame log records
_LOGGED = (_SID_LO, frameproc.NOIDX, _SID_HI - _SID_LO, 1, 0)


def _half(cell):
    return ("mem", ("const", cell, 2), 1)


def _word(cell):
    return ("mem", ("const", cell, 2), 2)


_w = frameproc.loc_width  # value width of a frameprog node, loc leaves included


def _zext2(n):
    return n if _w(n) == 2 else ("op", "INT_ZEXT", (n,), 2)


def _word_shape(n, lo, hi):
    """``n`` is exactly ``hi<<8 | lo`` over the pair's two byte cells."""
    if n[0] != "op" or n[1] != "INT_OR" or len(n[2]) != 2 or n[3] != 2:
        return False
    for a, b in (n[2], n[2][::-1]):
        if a[0] == "op" and a[1] == "INT_LEFT" and E.is_const(a[2][1]) and a[2][1][1] == 8:
            if ST._strip_zext(a[2][0]) == _half(hi) and ST._strip_zext(b) == _half(lo):
                return True
    return False


def _pack(vlo, vhi, hi_first=True):
    """``hi<<8 | lo``, the half written first left of the bar: evaluation order."""
    shl = ("op", "INT_LEFT", (_zext2(vhi), ("const", 8, 1)), 2)
    kids = (shl, _zext2(vlo)) if hi_first else (_zext2(vlo), shl)
    return ("op", "INT_OR", kids, 2)


def unpack(val):
    """``(lo value, hi value)`` of a packed word value, else None."""
    if val[0] != "op" or val[1] != "INT_OR" or len(val[2]) != 2 or val[3] != 2:
        return None
    for a, b in (val[2], val[2][::-1]):
        if a[0] == "op" and a[1] == "INT_LEFT" and E.is_const(a[2][1]) and a[2][1][1] == 8:
            return ST._strip_zext(b), ST._strip_zext(a[2][0])
    return None


def _rebase(addr, old, new):
    """The same address with its const base moved from ``old`` to ``new``."""
    if addr == ("const", old, 2):
        return ("const", new, 2)
    if addr[0] == "op":
        return (addr[0], addr[1], tuple(_rebase(a, old, new) for a in addr[2]), addr[3])
    return addr


def _consts(idx, env, at, regions, mem0, depth=8):
    """Every value ``idx`` may take where the model proves them all, else None.

    A constant *table* counts (Commando's ``LDY $14B5,X``), a written cell counts
    through the store in force (``Defs.cell``), and a definition a branch join
    leaves valueless forks (``_fork``): the union over the arms is the set."""
    while True:
        while idx[0] == "loc":
            got = env.lookup_joined(idx[1], at)
            if got is None:
                return None
            if got[2] is None:
                return _fork(idx, got[0], got[1], regions, mem0, depth)
            env, at, idx = got
        n = idx
        if n[0] == "op" and n[1] == "INT_ZEXT":
            n = n[2][0]
            if n[0] == "loc":
                idx = n
                continue
        if n[0] == "const":
            return frozenset((n[1] & 0xFF,))
        if n[0] != "mem" or n[2] != 1:
            return None
        base, row = _addr_split(n[1])
        if base is None:
            return None
        size = 1 if row is None else regions.avail(base)
        if size and all(regions.const_at(a) for a in range(base, base + size)):
            return frozenset(mem0[a] for a in range(base, base + size))
        got = None if row is not None else env.cell(base, at, regions)
        if got is None:
            return None
        env, at, idx = got


def _fork(n, env, k, regions, mem0, depth):
    """The union of the values the join at ``env.lst[k]`` may leave in ``n``.

    An ``if`` forks per arm (docs/frameprog.md 7.7 (3)): the arms are the exact
    paths control took, and an arm binding nothing falls through. A ``for`` binds
    its counter to the range's every value. A loop or a call stays a wall."""
    s = env.lst[k]
    if depth == 0:
        return None
    if s[0] == "for" and s[1] == n[1]:
        lo, hi = sorted((s[2], s[3]))
        return frozenset(range(lo, hi + 1))
    if s[0] != "if":
        return None
    out = set()
    for body in frameproc._stmt_bodies(s):
        sub = frameproc.Defs(body, (env, k), False)
        ks = _consts(n, sub, len(body), regions, mem0, depth - 1)
        if ks is None:
            return None
        out |= ks
    return frozenset(out)


def _lane_aligned(p, ks):
    """Every ``k`` puts the pair's lo on a register that is itself a pair's lo.

    ``mem[$D400 + y]`` is a store to register ``y``: widening it is right exactly
    where every reaching ``y`` lands the word on a 16-bit register, and wrong --
    it writes whatever cell follows -- where one does not."""
    return ks is not None and all(_sid_base(p.lo + k) == p.lo + k for k in ks)


def _widen(s, p):
    """A lone lane store as the u16 store it is, the other lane keeping its value.

    freq, pulse and cutoff are 16-bit registers: nothing narrower can be written
    to one, so a driver touching one lane still writes the whole word. An indexed
    lane widens only under ``_lane_aligned``."""
    base, _idx = _addr_split(s[1])
    addr = _rebase(s[1], base, p.lo)
    half = _zext2(s[2])
    if base == p.hi:
        half = ("op", "INT_LEFT", (half, ("const", 8, 1)), 2)
    keep = ("const", 0xFF00 if base == p.lo else 0x00FF, 2)
    kept = ("op", "INT_AND", (("mem", addr, 2), keep), 2)
    return ("st", addr, ("op", "INT_OR", (kept, half), 2))


_addr_split = frameproc.addr_split


def _may_read(n, cell):
    """True when evaluating ``n`` may load ``cell`` (the write-order hazard)."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            base, idx = _addr_split(x[1])
            if base is None:
                return True
            span = 0 if idx is None else E.mask(_w(idx))
            if base <= cell <= base + span + x[2] - 1:
                return True
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return False


def stmts_of(stmts):
    """Every statement of a statement list, nested bodies included."""
    stack = [list(stmts)]
    while stack:
        for s in stack.pop():
            yield s
            stack.extend(list(b) for b in frameproc._stmt_bodies(s))


class _Pair:
    """One candidate lo/hi pair and the evidence gathered against it."""

    __slots__ = (
        "lo",
        "hi",
        "kind",
        "evidence",
        "words",
        "lone",
        "stores",
        "unpaired",
        "hazard",
        "indexed",
        "unproven",
        "notaligned",
    )

    def __init__(self, lo, hi, kind, evidence):
        self.lo = lo
        self.hi = hi
        self.kind = kind
        self.evidence = evidence
        self.words = self.lone = self.stores = self.unpaired = self.hazard = 0
        self.indexed = self.unproven = self.notaligned = 0

    def refusal(self):
        """The premise's refusal diagnostic, or None where the pair fuses.

        A state pair is one tune-wide declaration, so any lone half refuses it. A
        SID pair never refuses: freq, pulse and cutoff are 16-bit registers, so
        the pair is one register whatever the driver's store sites look like."""
        if self.hi != self.lo + 1:
            return "halves are not adjacent"
        if self.kind == "sid":
            return None
        if self.hazard:
            return "%d store pair(s) whose second value may read the first cell" % self.hazard
        if self.lone:
            return "%d lone-half read(s)" % self.lone
        if self.unpaired:
            return "%d unpaired half store(s)" % self.unpaired
        if not (self.words or self.stores):
            return "no word access in the play code"
        return None

    def proof(self):
        """The rung-(d) proof record: evidence, premise counts, refusal."""
        why = self.refusal()
        body = "16-bit fusion: cells $%04X/$%04X; %s; %d word read(s), %d word store(s)" % (
            self.lo,
            self.hi,
            self.evidence,
            self.words,
            self.stores,
        )
        rest = "%d lone-half read(s), %d %s, %d hazard(s)" % (
            self.lone,
            self.unpaired,
            "widened lane store(s)" if self.kind == "sid" else "lone-half store(s)",
            self.hazard,
        )
        if self.kind == "sid":
            rest += ", %d lane-aligned indexed, %d index unproven, %d index proven off-lane" % (
                self.indexed,
                self.unproven,
                self.notaligned,
            )
        status = "refused" if why else ("fused" if not (self.lone or self.unpaired) else "partial")
        return Proof(self.lo, self.kind, status, (self.lo, self.hi), "%s; %s" % (body, why or rest))


# ---- candidate pairs: the evidence the spec names --------------------------------
def _pointer_pairs(model, decls):
    """State pairs the pointer classifier proves, named by their partner tables."""
    lanes = {d["base"] for d in decls if (d.get("role") or (None,))[0] == "lo"}
    out = {}
    for _cell, rec in sorted(ST.classify(model).items()):
        if rec["class"] != "pointer" or rec.get("role") != "lo":
            continue
        lts = [t for t in rec.get("reload_tables", ()) if t in lanes]
        note = " (datadecl lo/hi partner table %s)" % " ".join("$%04X" % t for t in lts)
        out[tuple(rec["pair"])] = ("pointer", "pointer pair" + (note if lts else ""))
    return out


def _dispatch_pairs(model):
    """Dispatch operand words the paired-index zip closure proved (study 4)."""
    ana = getattr(model, "analysis", None)
    out = {}
    for site, cells in sorted((getattr(ana, "derivations", None) or {}).items()):
        for (lo, hi), text in sorted(cells.items()):
            out[(lo, hi)] = ("dispatch", "%s (site $%04X)" % (text, site))
    return out


_sid_base = G.sid_base  # the lane rule lives with the grammar's names; the view uses it too


def _sid_pairs(procs):
    """SID lo/hi register pairs a store site addresses (freq, pulse, cutoff)."""
    bases = set()
    for _e, _p, _r, stmts in procs:
        for s in stmts_of(stmts):
            if s[0] == "st":
                b = _sid_base(_addr_split(s[1])[0] or 0)
                if b is not None:
                    bases.add(b)
    note = "SID register pair (the canonical section emits lo,hi adjacent)"
    return {(b, b + 1): ("sid", note) for b in sorted(bases)}


def candidates(model, decls, procs):
    """``{(lo, hi): (kind, evidence)}`` over every pair the model attests."""
    out = dict(_pointer_pairs(model, decls))
    out.update(_dispatch_pairs(model))
    out.update(_sid_pairs(procs))
    return out


# ---- the pass ---------------------------------------------------------------------
def _rewrite(n, p, count):
    """Fold word shapes to a word load; count lone-half reads on the way."""
    if _word_shape(n, p.lo, p.hi):
        p.words += count
        return _word(p.lo)
    k = n[0]
    if k == "mem":
        if n[1] in (("const", p.lo, 2), ("const", p.hi, 2)):
            p.lone += count
            return n
        return ("mem", _rewrite(n[1], p, count), n[2])
    if k == "op":
        return ("op", n[1], tuple(_rewrite(c, p, count) for c in n[2]), n[3])
    return n


def _store_half(s, p):
    """``(cell, index expression)`` when ``s`` stores one half, else None."""
    if s[0] != "st" or G.store_width(s[2]) != 1:
        return None
    base, idx = _addr_split(s[1])
    return (base, idx) if base in (p.lo, p.hi) else None


def _sites(stmts, p):
    """``{(cell, index): [position]}`` over the list's half stores, in list order."""
    out = {}
    for k, s in enumerate(stmts):
        h = _store_half(s, p)
        if h is not None:
            out.setdefault(h, []).append(k)
    return out


def _lww(p, idx, env, at, ctx):
    """Both cells an indexed store pair writes are last-write-wins registers.

    ``framelog`` keys the record by register and keeps write order only inside the
    ctrl/AD/SR and $19-$1C sections, so two writes to one lo/hi pair commute. An
    index the model cannot resolve may land the pair inside one of those."""
    if idx is None:
        return True
    return ctx is not None and _lane_aligned(p, _consts(idx, env, at, *ctx))


def _undisturbed(stmts, i, j, half, regions, env):
    """The interval leaves the moved lane and every logged register alone.

    A read of the lane between the two sites would see the moved write, and a
    write to any register the frame log records may share an order-preserved
    section with it, so crossing one may reverse two entries."""
    cell, idx = half
    at = frameproc.lane(cell, idx, frameproc.span(cell, idx, regions))
    for k in range(i + 1, j):
        s = frameproc.as_written(env, stmts, k)
        if s[0] == "st" and frameproc.overlaps(_LOGGED, frameproc.store_reach(s, regions)):
            return False
        if frameproc.reads(frameproc._stmt_exprs(s), at, regions):
            return False
    return True


def _bring(stmts, i, j, p, regions, env):
    """``(seat, value at i, value at j)`` where the two half stores may meet, else None.

    Either end may move. Hoisting the later store inlines what the interval
    defines, since the merged store then leads it; sinking the leading store
    admits no inlining, since it keeps the expression it was written with. Both
    hold the interval to disturbing neither value nor the lane that moved."""
    a, b = stmts[i], stmts[j]
    ha, hb = _store_half(a, p), _store_half(b, p)
    up, blocked = frameproc.hoist(stmts, i, j, (b[1], b[2]), regions, env)
    if blocked is None and up[0] == b[1] and _undisturbed(stmts, i, j, hb, regions, env):
        return i, a[2], up[1]
    down, blocked = frameproc.hoist(stmts, i, j, (a[1], a[2]), regions, env)
    if blocked is None and down == (a[1], a[2]) and _undisturbed(stmts, i, j, ha, regions, env):
        return j, a[2], b[2]
    return None


def _pair_at(stmts, i, p, sites, regions=None, env=None, ctx=None):
    """The merge ``stmts[i]`` leads: ``(partner, seat, statement, its value, cell)``.

    The partner is the nearest later store of the pair's other lane at the same
    symbolic index, adjacent or not. A merge writes exactly the two cells the
    program wrote, so it owes no proof about the index -- only that bringing the
    two together moves no value and no record entry. ``stw`` logs lo then hi, so
    a hi-first pair reverses two writes: free where both cells are proven
    last-write-wins registers, and refused where the index leaves that open."""
    ha = _store_half(stmts[i], p)
    if ha is None:
        return None
    ks = sites.get((p.hi if ha[0] == p.lo else p.lo, ha[1])) or ()
    k = bisect_right(ks, i)
    if k == len(ks):
        return None
    j = ks[k]
    lofirst = _store_half(stmts[j], p)[0] == p.hi
    if not lofirst and (p.kind != "sid" or not _lww(p, ha[1], env, i, ctx)):
        return None
    got = _bring(stmts, i, j, p, regions, env)
    if got is None:
        return None
    seat, va, vb = got
    lo = stmts[i] if lofirst else stmts[j]
    vlo, vhi = (va, vb) if lofirst else (vb, va)
    return j, seat, ("st", lo[1], _pack(vlo, vhi, not lofirst)), vb, ha[0]


def _visit(stmts, p, mutate, ctx=None, outer=None, cyclic=False):
    """One statement list: fuse paired stores, fold word reads, count refusals.

    The list is rewritten as it is scanned, so the environment and the half-store
    index are rebuilt whenever a statement moved: a stale one would answer for
    the wrong statement. ``taken`` is the partner a merge already accounted for."""
    count = 0 if mutate else 1
    regions = None if ctx is None else ctx[0]
    env, sites, taken, stale = None, None, set(), True
    i = 0
    while i < len(stmts):
        if stale:
            env, sites, stale = frameproc.Defs(stmts, outer, cyclic), _sites(stmts, p), False
            taken.clear()
        if i in taken:
            i += 1
            continue
        s = stmts[i]
        for body in frameproc._stmt_bodies(s):
            _visit(body, p, mutate, ctx, (env, i), s[0] in frameproc._CYCLIC)
        at = _pair_at(stmts, i, p, sites, regions, env, ctx)
        if at is not None and _may_read(at[3], at[4]):
            p.hazard += count
            if p.kind != "sid":
                taken.add(at[0])
                i += 1
                continue
            at = None  # the halves cannot pack, but each lane still widens
        if at is not None:
            j, seat, merged = at[:3]
            p.stores += count
            if mutate:
                stmts[seat] = merged
                del stmts[j if seat == i else i]
                stale = True
            else:
                taken.add(j)
            i += 1 if seat == i or not mutate else 0
            continue
        if s[0] == "st" and _addr_split(s[1])[0] == p.lo and G.store_width(s[2]) == 2:
            p.stores += count  # already one word store: rung (d2) fused this pair
            i += 1
            continue
        half = _store_half(s, p)
        widen = half is not None and p.kind == "sid"
        if widen and half[1] is not None:
            ks = _consts(half[1], env, i, *ctx) if ctx is not None else None
            widen = _lane_aligned(p, ks)  # an unproven index is work, an off-lane one is not
            p.indexed += count if widen else 0
            p.unproven += count if ks is None else 0
            p.notaligned += count if ks is not None and not widen else 0
        if half is not None:
            p.unpaired += count
        new = frameproc._map_exprs(s, lambda x: _rewrite(x, p, count))
        if mutate:
            stmts[i] = _widen(new, p) if widen else new
            stale = stale or stmts[i] is not s  # a widened store moved its own range
        i += 1


def _merge_alias(symbols, p):
    """Name the fused word after the pair, dropping the hi half's alias."""
    alias = symbols.get(p.lo)
    if alias is not None and alias.endswith("_lo"):
        merged = alias[:-3]
        if merged not in set(symbols.values()):
            symbols[p.lo] = merged
    symbols.pop(p.hi, None)


def _fuse_state(state, symbols, pairs, name_of):
    """Drop each fused pair's hi field and widen its lo field to ``u16``."""
    drop, rename = set(), {}
    for p in pairs:
        drop.add(symbols.get(p.hi) or name_of(p.hi))
        old = symbols.get(p.lo) or name_of(p.lo)
        _merge_alias(symbols, p)
        rename[old] = symbols.get(p.lo) or name_of(p.lo)
    out = []
    for name, width, array, obs in state:
        if name in rename:
            out.append((rename[name], 2, array, obs))
        elif name not in drop:
            out.append((name, width, array, obs))
    return out


def apply_rung(model, decls, procs, state, symbols, name_of):
    """Rung (d) in place over ``procs``; returns ``(state fields, proofs)``.

    Per pair, never per tune: a pair whose premise fails keeps its two byte
    halves and every other pair still fuses. The SID register pairs — freq,
    pulse and cutoff — fuse on the same footing, per store site (spec 4d)."""
    proofs, fused = [], []
    ctx = (datadecl.Regions(decls), model.mem0)
    for (lo, hi), (kind, evidence) in sorted(candidates(model, decls, procs).items()):
        p = _Pair(lo, hi, kind, evidence)
        if hi == lo + 1:
            for _e, _pa, _r, stmts in procs:
                _visit(stmts, p, False, ctx)
        proofs.append(p.proof())
        if p.refusal() is not None:
            continue
        fused.append(p)
        for _e, _pa, _r, stmts in procs:
            _visit(stmts, p, True, ctx)
    state = _fuse_state(state, symbols, [p for p in fused if p.kind != "sid"], name_of)
    return state, proofs
