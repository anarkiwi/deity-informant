"""Init-phase copy origins: the address a RAM cell's staged byte was loaded from.

``decompile`` keeps only init's flat image, so a parameter staged out of a const
table at init reaches the play phase undeclared (docs/frameprog.md 4.5). The origin
is a record's static copy transfer over its own addresses, never a value match.
"""

from __future__ import annotations

from collections import Counter

_TRACKED = (0, 1, 2)  # A, X, Y: the registers a staged byte passes through
_STK_LO, _STK_HI = 0x0100, 0x01FF


def _src(env, vn):
    """The byte source of a varnode: its binding, its entry origin, or None."""
    if vn[2] != 1 or vn[0] == "c":
        return None
    key = (vn[0], vn[1])
    if key in env:
        return env[key]
    return ("in", vn[1]) if vn[0] == "r" else None


def transfer(rec):
    """``((register, source)..., per-store source)`` for a lifted record, cached on it.

    A source is ``("ld", k)`` -- the k-th ``LOAD`` -- or ``("in", r)``, the entry
    origin of register ``r``; ``None`` is a byte the record computed, and ``False``
    a record moving no byte at all. Only a value-preserving move chain propagates.
    """
    ct = rec.get("_ct")
    if ct is not None:
        return ct
    env, stores, nld = {}, [], 0
    for mn, out, ins in rec["ops"]:
        if mn == "STORE":
            stores.append(_src(env, ins[1]))
            continue
        key = (out[0], out[1])
        if mn == "LOAD":
            env[key] = ("ld", nld) if out[2] == 1 else None
            nld += 1
        elif mn == "COPY" and out[2] == 1:
            env[key] = _src(env, ins[0])
        else:
            env[key] = None
    regs = tuple((i, env[("r", i)]) for i in _TRACKED if ("r", i) in env)
    ct = rec["_ct"] = (regs, tuple(stores)) if regs or stores else False
    return ct


def _origin(tok, regs, cells, la):
    """The cell a token's byte came from, path-compressed through the map."""
    if tok is None:
        return None
    if tok[0] == "in":
        return regs[tok[1]]
    a = la[tok[1]]
    return cells.get(a, a)


class Tracer:
    """Cell -> origin over one init run, with the per-site and per-cell census.

    Last write wins: the origin kept is the one the machine wrote last, which is the
    byte ``mem0`` holds. A computed value drops the cell, and a stack cell is never
    an origin -- ``jsr``/``rts`` traffic bypasses the P-Code store.
    """

    __slots__ = ("cells", "sites", "regs", "written", "refused", "conflict", "stores")

    def __init__(self):
        self.cells = {}  # cell -> origin address, proven copies only
        self.sites = {}  # cell -> pc of the store that proved it
        self.regs = [None] * 16
        self.written = set()
        self.refused = Counter()  # pc -> executed stores that proved no origin
        self.conflict = set()  # cells init staged twice from different origins
        self.stores = 0

    def step(self, rec, pc, la, sa):
        """Apply one executed record; ``la``/``sa`` are its load/store addresses."""
        ct = rec.get("_ct")
        if ct is None:
            ct = transfer(rec)
        if not ct:
            return
        regs, stores = ct
        cells, r = self.cells, self.regs
        if stores:
            self.stores += len(stores)
            for i, tok in enumerate(stores):
                self.bind(sa[i], _origin(tok, r, cells, la), pc)
        if regs:
            for i, v in [(i, _origin(t, r, cells, la)) for i, t in regs]:
                r[i] = v

    def bind(self, cell, org, pc):
        """Bind ``cell`` to the origin of the byte just stored, or refuse it."""
        self.written.add(cell)
        cells = self.cells
        if org is None or _STK_LO <= org <= _STK_HI or _STK_LO <= cell <= _STK_HI:
            if cells.pop(cell, None) is not None:
                del self.sites[cell]
            self.refused[pc] += 1
            return
        if cells.get(cell, org) != org:
            self.conflict.add(cell)
        cells[cell] = org
        self.sites[cell] = pc


class Reduced:
    """A recorded ``reduce`` result, replayed instead of the tracer it came from.

    Both the declaration index and the projection are in the artifact while the
    tracer is not, and the projection is two orders smaller (Ghouls_n_Ghosts:
    7,159 traced cells against 40 surviving records)."""

    __slots__ = ("origins", "sites", "census")

    def __init__(self, origins, sites, census):
        self.origins = dict(origins)
        self.sites = dict(sites)
        self.census = dict(census)


def reduce(tracer, is_const, played=frozenset()):
    """``(cell -> origin, per-site verdicts, census)``: the copies a declaration names.

    The map seeds ``frameval``'s play-phase map, so a play-phase store rebinds or
    drops a staged cell by the same one-contributor rule. ``is_const`` is the caller's
    declaration index (``datadecl.Regions.const_at``); a ``Reduced`` consults neither."""
    if isinstance(tracer, Reduced):
        return tracer.origins, tracer.sites, tracer.census
    cens = Counter(cells=len(tracer.written), stores=tracer.stores, conflict=len(tracer.conflict))
    cens["stack"] = sum(1 for c in tracer.written if _STK_LO <= c <= _STK_HI)
    cens["computed"] = len(tracer.written) - len(tracer.cells) - cens["stack"]
    out, staged, dropped = {}, {}, Counter()
    for cell, org in sorted(tracer.cells.items()):
        pc = tracer.sites[cell]
        if not is_const(org):
            cens["undeclared"] += 1
            dropped[pc] += 1
            continue
        out[cell] = org
        staged.setdefault(pc, []).append(cell)
        if cell in played:
            cens["play_written"] += 1
    cens["origins"] = len(out)
    sites = {
        pc: (tuple(staged.get(pc, ())), dropped[pc], tracer.refused[pc])
        for pc in sorted(set(staged) | set(dropped) | set(tracer.refused))
    }
    return out, sites, dict(cens)
