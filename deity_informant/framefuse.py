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


def _pack(vlo, vhi, hi_first=True):
    """``hi<<8 | lo``, the half written first left of the bar: evaluation order.

    Two constant halves do NOT fold to one constant here: ``grammar.store_width``
    reads every ``const`` store value as one byte, so a folded word would store
    truncated. The pack keeps the width the store protocol can see."""
    shl = ("op", "INT_LEFT", (_zext2(vhi), frameproc.SHIFT8), 2)
    kids = (shl, _zext2(vlo)) if hi_first else (_zext2(vlo), shl)
    return ("op", "INT_OR", kids, 2)


def unpack(val):
    """``(lo value, hi value)`` of a packed word value, else None.

    The ONE reader of the pack shape here: rung (d) packs computed halves as
    readily as cells, so unlike ``frameproc._le_bytes`` neither side has to be a
    leaf -- what ``_pack`` writes is exactly what this reads back."""
    if not frameproc.is_op(val, "INT_OR", 2, 2):
        return None
    for a, b in frameproc.commuted(val[2]):
        if frameproc.is_op(a, "INT_LEFT") and E.is_const(a[2][1]) and a[2][1][1] == 8:
            return ST._strip_zext(b), ST._strip_zext(a[2][0])
    return None


def _word_shape(n, lo, hi):
    """``n`` is exactly ``hi<<8 | lo`` over the pair's two byte cells."""
    return unpack(n) == (_half(lo), _half(hi))


def _rebase(addr, old, new):
    """The same address with its const base moved from ``old`` to ``new``."""
    if addr == ("const", old, 2):
        return ("const", new, 2)
    if addr[0] == "op":
        return (addr[0], addr[1], tuple(_rebase(a, old, new) for a in addr[2]), addr[3])
    return addr


def _consts(idx, env, at, ctx, depth=8):
    """Every value ``idx`` may take where the model proves them all, else None.

    ``ctx`` is ``(regions, mem0, params, entry, foreign, writes)``. A constant
    *table* counts, a written cell counts through the store in force
    (``Defs.cell``), a join forks (``_fork``), an entry unions the call sites."""
    regions, mem0, params, entry = ctx[0], ctx[1], ctx[2], ctx[3]
    while True:
        while idx[0] == "loc":
            got = env.lookup_joined(idx[1], at)
            if got is frameproc.ENTRY:
                return None if params is None else params.of(entry, idx[1])
            if got is None:
                return None
            if got[2] is None:
                return _fork(idx, got[0], got[1], ctx, depth)
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
        got = None if row is not None else env.cell(base, at, regions, ctx[5])
        if got is None:
            return None
        env, at, idx = got


def _fork(n, env, k, ctx, depth):
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
        ks = _consts(n, sub, len(body), ctx, depth - 1)
        if ks is None:
            return None
        out |= ks
    return frozenset(out)


class _MayWrite:
    """Whether a procedure, or any procedure it calls, may store into one cell.

    A ``pcall`` on a cell walk is a wall only where the callee may reach the
    cell; a raw call binds unseen and stays one. A call cycle answers True."""

    __slots__ = ("procs", "regions", "mem0", "memo", "depth")

    def __init__(self, procs, regions, mem0):
        self.procs = {e: stmts for e, _pa, _r, stmts in procs}
        self.regions = regions
        self.mem0 = mem0
        self.memo = {}
        self.depth = 0

    def may_write(self, entry, cell):
        key = (entry, cell)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = True  # a cycle may write until the scan proves otherwise
        self.memo[key] = got = self._scan(self.procs.get(entry), cell)
        return got

    def _scan(self, stmts, cell):
        if stmts is None:
            return True
        at = (cell, frameproc.NOIDX, 0, 1, 0)
        for s in stmts_of(stmts):
            k = s[0]
            if k == "st" and frameproc.overlaps(at, frameproc.store_reach(s, self.regions)):
                return True
            if k == "pcall" and self.may_write(s[1], cell):
                return True
            if k in ("call", "dcall", "swc"):
                return True
        return False

    def store_misses(self, env, k, s, cell):
        """The indexed byte store at ``env.lst[k]`` provably never lands on ``cell``.

        Its own index resolved as any store's would be: the span rule bounds an
        undeclared base at the full index width, but the values the index takes
        can rule the cell out exactly. Re-entrant walks give up rather than spin."""
        base, idx = _addr_split(s[1])
        if base is None or idx is None or G.store_width(s[2]) != 1 or self.depth > 8:
            return False
        self.depth += 1
        try:
            ks = _consts(idx, env, k, (self.regions, self.mem0, None, None, None, self), 4)
        finally:
            self.depth -= 1
        return ks is not None and all(base + v != cell for v in ks)


class _Params:
    """The values each procedure parameter may hold, unioned over its call sites.

    Filled before rung (d) moves a statement: a call site is a position in a list
    this pass rewrites, so the table is loaded while the positions still hold and
    only read back afterwards."""

    __slots__ = ("calls", "regions", "mem0", "writes", "table", "busy", "filling")

    def __init__(self, calls, regions, mem0, writes=None):
        self.calls, self.regions, self.mem0, self.writes = calls, regions, mem0, writes
        self.table, self.busy, self.filling = {}, set(), False

    def fill(self, procs):
        """Solve every parameter of a procedure that stores to a lane through an index."""
        self.filling = True
        for e, params, _r, stmts in procs:
            if any(_lane_index(s) for s in stmts_of(stmts)):
                for name in params:
                    self.of(e, name)
        self.filling = False

    def of(self, entry, name):
        """The values the parameter may hold; an unsolved one reads as unknown."""
        key = (entry, name)
        if key in self.table or not self.filling:
            return self.table.get(key)
        if key in self.busy:
            return None  # a call cycle: the union is not well founded
        sites = self.calls.args(entry, name)
        if sites is None:
            self.table[key] = None
            return None
        self.busy.add(key)
        out = set()
        for caller, env, at, arg in sites:
            ctx = (self.regions, self.mem0, self, caller, None, self.writes)
            got = _consts(arg, env, at, ctx)
            if got is None:
                out = None
                break
            out |= got
        self.busy.discard(key)
        self.table[key] = out = None if out is None else frozenset(out)
        return out


def _lane_index(s):
    """True where ``s`` stores to a SID lo/hi register through an index."""
    if s[0] != "st":
        return False
    base, idx = _addr_split(s[1])
    return idx is not None and base is not None and _sid_base(base) is not None


def _lane_aligned(p, ks):
    """Every ``k`` puts the pair's lo on a register that is itself a pair's lo.

    ``mem[$D400 + y]`` is a store to register ``y``: widening it is right exactly
    where every reaching ``y`` lands the word on a 16-bit register, and wrong --
    it writes whatever cell follows -- where one does not."""
    return ks is not None and all(_sid_base(p.lo + k) == p.lo + k for k in ks)


_LEAVES = frozenset(("ret", "goto", "dgoto", "dbr", "igoto", "swg", "unobs"))


def _escapes(body, own=True):
    """Control may leave the loop before its counter has taken every value.

    ``brk`` and ``cont`` belong to the nearest enclosing cycle, so they count only
    outside a nested one; a return or a jump leaves whatever it is nested in."""
    for s in body:
        if s[0] in _LEAVES or (own and s[0] in ("brk", "cont")):
            return True
        inner = own and s[0] not in frameproc._CYCLIC
        if any(_escapes(b, inner) for b in frameproc._stmt_bodies(s)):
            return True
    return False


def _counter_range(idx, env, at):
    """The range ``idx`` steps through where the store rides every value of it.

    ``_consts`` unions the definitions reaching a store, so its set is what the
    index *may* hold; a sweep needs each value to occur, which only a ``for``
    counter binding the body the store sits directly in, with no early exit, gives."""
    while frameproc.is_op(idx, "INT_ZEXT"):
        idx = idx[2][0]
    if idx[0] != "loc" or env.outer is None:
        return None
    got = env.lookup_joined(idx[1], at)
    if got is None or got is frameproc.ENTRY or got[2] is not None or got[:2] != env.outer:
        return None
    s = env.outer[0].lst[env.outer[1]]
    if s[0] != "for" or s[1] != idx[1] or _escapes(s[4]):
        return None
    lo, hi = sorted((s[2], s[3]))
    return frozenset(range(lo, hi + 1))


def _covering(cell, ks):
    """The reached registers hold both halves of every pair they touch.

    A run of the register file writes each 16-bit register it reaches entire, so
    no half is left stale and there is no word to complete around the store."""
    hit = frozenset(cell + k for k in ks)
    return all(_sid_base(a) is None or {_sid_base(a), _sid_base(a) + 1} <= hit for a in hit)


def _lane_sweep(cell, idx, env, at):
    """The indexed lane store is a covering sweep, so it is no lane half at all.

    ``_lane_aligned``'s premise is that the store is a lone half needing the word
    completed around it; a sweep writes the word entire, so nothing is owed and
    nothing widens -- the rule is stated over the reached set and names no loop."""
    ks = _counter_range(idx, env, at)
    return ks is not None and _covering(cell, ks)


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
        "swept",
    )

    def __init__(self, lo, hi, kind, evidence):
        self.lo = lo
        self.hi = hi
        self.kind = kind
        self.evidence = evidence
        self.words = self.lone = self.stores = self.unpaired = self.hazard = 0
        self.indexed = self.unproven = self.notaligned = self.swept = 0

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
            rest += (
                ", %d lane-aligned indexed, %d index unproven, %d index proven off-lane"
                ", %d covering sweep(s)"
                % (self.indexed, self.unproven, self.notaligned, self.swept)
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
    """Fold word shapes to a word load; count lone-half reads on the way.

    A word read already folded (``repolish`` runs the little-endian fold before
    this rung) is the same evidence the shape is: one access of the whole pair."""
    if _word_shape(n, p.lo, p.hi) or n == _word(p.lo):
        p.words += count
        return _word(p.lo)
    k = n[0]
    if k == "mem":
        if n[2] == 1 and n[1] in (("const", p.lo, 2), ("const", p.hi, 2)):
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


def _pair_at(stmts, i, p, sites, regions=None, env=None):
    """The merge ``stmts[i]`` leads: ``(partner, seat, statement, its value, cell)``.

    The partner is the nearest later store of the pair's other lane at the same
    symbolic index, adjacent or not. A merge writes exactly the two cells the
    program wrote, so it owes no proof about the index -- only that bringing the
    two together moves no value and no record entry. A hi-first pair merges to a
    store that says so (``frameproc.hi_first``): ``stw`` then emits hi then lo,
    which is the sequence the program wrote for *any* index, so the record is
    reproduced wherever the index lands and no lane fact is owed (§7.10.4)."""
    ha = _store_half(stmts[i], p)
    if ha is None:
        return None
    ks = sites.get((p.hi if ha[0] == p.lo else p.lo, ha[1])) or ()
    k = bisect_right(ks, i)
    if k == len(ks):
        return None
    j = ks[k]
    lofirst = _store_half(stmts[j], p)[0] == p.hi
    got = _bring(stmts, i, j, p, regions, env)
    if got is None:
        return None
    seat, va, vb = got
    lo = stmts[i] if lofirst else stmts[j]
    vlo, vhi = (va, vb) if lofirst else (vb, va)
    st = ("st", lo[1], _pack(vlo, vhi, not lofirst)) + (() if lofirst else (True,))
    return j, seat, st, vb, ha[0]


def _visit(stmts, p, mutate, ctx=None, outer=None, cyclic=False):
    """One statement list: fuse paired stores, fold word reads, count refusals.

    The list is rewritten as it is scanned, so the environment and the half-store
    index are rebuilt whenever a statement moved: a stale one would answer for
    the wrong statement. ``taken`` is the partner a merge already accounted for."""
    count = 0 if mutate else 1
    regions = None if ctx is None else ctx[0]
    foreign = ctx[4] if ctx is not None and outer is None else None
    env, sites, taken, stale = None, None, set(), True
    i = 0
    while i < len(stmts):
        if stale:
            env = frameproc.Defs(stmts, outer, cyclic, foreign)
            sites, stale = _sites(stmts, p), False
            taken.clear()
        if i in taken:
            i += 1
            continue
        s = stmts[i]
        for body in frameproc._stmt_bodies(s):
            _visit(body, p, mutate, ctx, (env, i), s[0] in frameproc._CYCLIC)
        at = _pair_at(stmts, i, p, sites, regions, env)
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
            ks = _consts(half[1], env, i, ctx) if ctx is not None else None
            widen = _lane_aligned(p, ks)  # an unproven index is work, an off-lane one is not
            swept = not widen and _lane_sweep(half[0], half[1], env, i)
            p.indexed += count if widen else 0
            p.swept += count if swept else 0
            p.unproven += count if ks is None and not swept else 0
            p.notaligned += count if not (widen or swept or ks is None) else 0
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


def _landings(model):
    """RTS-trick landings: what an RTS reaches that no JSR's return explains.

    ``ev_targets`` also records every JSR's callee and every normal return, so
    only the RTS-terminated blocks are consulted, as ``procpass._graph`` does."""
    ev = getattr(model, "ev_targets", None) or {}
    blocks = getattr(model, "blocks", None) or {}
    rets = {(b.term[2] + 1) & 0xFFFF for b in blocks.values() if b.term[0] == "jsr"}
    out = set()
    for b in blocks.values():
        if b.term[0] == "rts":
            out |= set(ev.get(b.pcs[-1], ())) - rets
    return out


def contexts(model, decls, procs):
    """Per-procedure ``(regions, mem0, params, entry, foreign)``, call sites solved.

    ``foreign`` is the goto targets every *other* procedure carries: a label among
    them is an entry no local walk saw, refusing its join, and a procedure so
    entered is opaque to the union, with the RTS landings (7.7 (4))."""
    regions = datadecl.Regions(decls)
    targets = {}
    for e, _pa, _r, stmts in procs:
        targets[e] = {s[1] for s in stmts_of(stmts) if s[0] == "goto"}
    foreign = {
        e: frozenset(t for e2, ts in targets.items() if e2 != e for t in ts) for e in targets
    }
    entered = {
        e
        for e, _pa, _r, stmts in procs
        if any(s[0] == "label" and s[1] in foreign[e] for s in stmts_of(stmts))
    }
    calls = frameproc.Calls(procs, model.play, _landings(model) | entered)
    writes = _MayWrite(procs, regions, model.mem0)
    params = _Params(calls, regions, model.mem0, writes)
    params.fill(procs)
    return {e: (regions, model.mem0, params, e, foreign[e], writes) for e, _pa, _r, _s in procs}


def apply_rung(model, decls, procs, state, symbols, name_of):
    """Rung (d) in place over ``procs``; returns ``(state fields, proofs)``.

    Per pair, never per tune: a pair whose premise fails keeps its two byte
    halves and every other pair still fuses. The SID register pairs — freq,
    pulse and cutoff — fuse on the same footing, per store site (spec 4d)."""
    proofs, fused = [], []
    ctx = contexts(model, decls, procs)
    for (lo, hi), (kind, evidence) in sorted(candidates(model, decls, procs).items()):
        p = _Pair(lo, hi, kind, evidence)
        if hi == lo + 1:
            for e, _pa, _r, stmts in procs:
                _visit(stmts, p, False, ctx[e])
        proofs.append(p.proof())
        if p.refusal() is not None:
            continue
        fused.append(p)
        for e, _pa, _r, stmts in procs:
            _visit(stmts, p, True, ctx[e])
    state = _fuse_state(state, symbols, [p for p in fused if p.kind != "sid"], name_of)
    return state, proofs
