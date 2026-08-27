"""S6 -- the facts the names are derived from: one pass over the IR, per cell.

What a store writes back into its own cell is a counter or a timer, what a load
indexes is a cursor, what flows into a SID register is that register's shadow.
:class:`Facts` gathers all of it once, per region and per cell.
"""

from __future__ import annotations

from functools import reduce
from math import gcd

from .ir import Bin, Call, Const, Let, Load, R16, SID_REG_LO, SID_REG_HI, Store, Var, W16
from .irwalk import addr_split, expand, loads, reachable, single_defs, walk

VOICE_REG = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
SID_VOICE, SID_VOICES = len(VOICE_REG), 3  # the per-voice register block, and the voices
GLOBAL_REG = {0xD415: "cutoff_lo", 0xD416: "cutoff_hi", 0xD417: "res_route", 0xD418: "mode_vol"}
OPNAME = {"^": "eor", "|": "or", "&": "and", "+": "add", "-": "sub", "<<": "shl", ">>": "shr"}
DEPTH, MAXOPS, MAXPAIRS = 4, 2, 24
MAXROLE = 8  # elements: above this a region is a block, not a variable


# ---- expression facts --------------------------------------------------------
def value_walk(e):
    """The operators of a value, address arithmetic excluded."""
    yield e
    if type(e) is Bin:
        yield from value_walk(e.a)
        yield from value_walk(e.b)


def ops(e):
    """How many operators a value applies; the arithmetic of its addresses is not one.

    An index a fold made into ``base + v * stride`` is one cell of a struct, not
    two more operators on the value it holds.
    """
    return sum(1 for x in value_walk(e) if type(x) is Bin)


def reach(e):
    """How far a value is from a cell, its addressing included.

    A register's shadow is a byte or a table entry away; a stream a pointer pair
    walks is further, and is not a shadow of anything.
    """
    return sum(1 for x in walk(e) if type(x) is Bin)


def leaf_loads(e):
    """The loads of ``e`` that are values, not parts of an address."""
    ls = loads(e)
    inner = {id(y) for x in ls for y in walk(x.a)}
    return [x for x in ls if id(x) not in inner]


def leaf_reads(e):
    """The regions ``e`` reads for its value: addresses excluded, 16-bit pairs included."""
    xs = [x for x in walk(e) if type(x) is Load or type(x) is R16]
    inner = {id(y) for x in xs for y in walk(x.a)}
    out = set()
    for x in (x for x in xs if id(x) not in inner):
        out.update((x.lo[0], x.hi[0]) if type(x) is R16 else (x.r,))
    return {r for r in out if r >= 0}


def same_cell(a, b):
    """True when two address expressions name the same cell."""
    return a.v == b.v if type(a) is Const and type(b) is Const else a == b


class Facts:
    """Everything the roles are derived from, gathered in one pass over the IR."""

    def __init__(self, prog):
        self.prog = prog
        self.rgn = prog.by_id()
        self.sid = []
        self.copies = []
        self.idxvar = {}
        self.updates = {}
        self.cellupd = {}
        self.cellplain = set()
        self.plain = set()
        self.index = {}
        self.idxbase = {}
        self.cellsrc = {}
        self.sididx = set()
        self.cellindex = set()
        self.addr = set()
        self.reads = {}
        self.writes = {}
        self.wpc = {}
        self.tick = reachable(prog, prog.meta.get("tick_proc")) or set(prog.procs)
        for name, p in prog.procs.items():
            self.reads[name], self.writes[name] = set(), set()
            whole = {n: e for n, e in single_defs(p).items() if not n.startswith("$")}
            for b in p.blocks.values():
                self.block(name, b, whole)

    def block(self, name, b, whole):
        """One block's facts, over the procedure's definitions and the block's own.

        What a value *reads* is a property of the value, so which block the
        structuring left its definition in must not decide it; whether a *store* is
        a simple self-update is a property of the statement, which stays local.
        """
        defs, seen = {}, dict(whole)
        for s in b.stmts:
            if type(s) is Let:
                if not s.n.startswith("$"):
                    defs[s.n] = seen[s.n] = s.e
                self.value(name, expand(s.e, seen, DEPTH))
            elif type(s) is Store:
                addr = expand(s.a, seen, DEPTH)
                self.value(name, expand(s.v, seen, DEPTH))
                self.value(name, addr)
                if s.cls == "io":
                    self.sidaddr(addr)
                self.store(name, s, expand(s.v, defs, DEPTH), expand(s.a, defs, DEPTH))
            elif type(s) is W16:
                self.source((s.lo, s.hi), expand(s.e, seen, DEPTH))
            elif type(s) is Call:
                for a in s.args:
                    self.value(name, expand(a, seen, DEPTH))

    def value(self, name, e):
        """Record what an expression reads: regions, index uses, pointer uses."""
        for x in loads(e):
            self.reads[name].add(x.r)
            self.walks(x.r, x.a)
            base = index_base(x.a)
            for y in loads(x.a):
                self.index.setdefault(y.r, set()).add(x.r)
                cell = self.cell(y.r, addr_split(y.a)[0])
                self.idxbase.setdefault(cell, set()).add((x.r,) + base)
                if cell[1] is not None:
                    self.cellindex.add(cell)
                if y.w == 2:
                    self.addr.add(y.r)

    def cell(self, rid, a):
        """``(region, address)`` for an access: the address, or ``None`` where it is not one.

        A constant the address arithmetic leaves that the region does not contain
        is a displacement, not the cell an index was loaded from.
        """
        r = self.rgn.get(rid)
        return (rid, a if a is not None and r is not None and r.extent(a, a) else None)

    def source(self, cells, v):
        """Record the regions whose load supplies the value written to ``cells``."""
        rids = leaf_reads(v)
        for c in cells:
            self.cellsrc.setdefault(c, set()).update(rids)

    def sidaddr(self, a):
        """Record the regions a SID-register access takes its index from."""
        base, idx = addr_split(a)
        if base is not None and SID_REG_LO <= base <= SID_REG_HI and idx is not None:
            self.sididx.update(x.r for x in loads(idx))

    def walks(self, rid, a):
        """Record that a bare index variable walks the elements of region ``rid``."""
        base, i = addr_split(a)
        if base is not None and type(i) is Var:
            self.idxvar.setdefault(i.n, set()).add(rid)

    def store(self, name, s, v, a=None):
        """Record a store: the SID image, and whether it updates its own region."""
        if s.cls == "io":
            base, idx = addr_split(a if a is not None else s.a)
            if base is not None and SID_REG_LO <= base <= SID_REG_HI:
                (self.sid if idx is None else self.copies).append(
                    (base, v) if idx is None else (base, idx, v)
                )
        if s.r < 0:
            return
        self.walks(s.r, a if a is not None else s.a)
        self.writes[name].add(s.r)
        self.wpc.setdefault(s.r, set()).add(s.src)
        if name not in self.tick:
            return
        same = [x for x in leaf_loads(v) if x.r == s.r and same_cell(x.a, s.a)]
        cell = (s.r, addr_split(a)[0])
        if same and ops(v) <= MAXOPS:
            self.updates.setdefault(s.r, set()).add(v)
            self.cellupd.setdefault(cell, set()).add(v)
        else:
            self.plain.add(s.r)
            self.cellplain.add(cell)
            self.source((cell,), v)


def index_base(a):
    """What an indexed address adds its index to: a constant, a 16-bit pair, or neither.

    The pair is the two cells of a :class:`~.ir.R16`, so a table walked through a
    loaded pointer names both the pointer and the cursor that walks it.
    """
    if addr_split(a)[0] is not None:
        return ("const", None, None)
    p = next((x for x in walk(a) if type(x) is R16), None)
    return ("ptr", p.lo, p.hi) if p is not None else ("other", None, None)


def cursor_cells(facts, cells):
    """``cursor`` when the cells of one variable or field index a block.

    The mirror of the scalar rule, which asks the region *holding* the index to
    be a variable and not a block (:data:`MAXROLE`): a field is one byte per
    record, so what it must show instead is a block on the other side -- a table
    of more than :data:`MAXROLE` elements it walks. An index that only selects
    one of a handful of elements is the element selector a view already names.
    """
    tgt = {t for c in cells if c in facts.cellindex for t, *_ in facts.idxbase.get(c, ())}
    walks = any(elem_count(facts.rgn[t]) > MAXROLE for t in tgt if t in facts.rgn)
    return "cursor" if walks else ""


def sid_name(addr):
    """``(field name, voice)`` for a SID register address."""
    if addr in GLOBAL_REG:
        return GLOBAL_REG[addr], None
    v, k = divmod(addr - SID_REG_LO, SID_VOICE)
    return VOICE_REG[k], v


def voice_maps(prog):
    """Regions holding the SID's voice -> register-offset map, ``0, 7, 14``.

    The same hardware fact as :data:`VOICE_REG`: a register file of three
    identical blocks. An index read from such a table selects the *voice*, so
    ``$D405 + map[v]`` is voice ``v``'s ``ad`` however the tune reaches it.
    """
    want = [SID_VOICE * i for i in range(SID_VOICES)]
    out = set()
    for r in prog.storage:
        if r.id < 0 or r.kind not in ("const", "image") or elem_count(r) != SID_VOICES:
            continue
        top = (SID_VOICES - 1) * r.stride
        if top < len(r.init) and [r.init[i * r.stride] for i in range(SID_VOICES)] == want:
            out.add(r.id)
    return out


def elem_count(r):
    """How many elements a region's stride divides it into."""
    return -(-r.size // max(r.stride, 1))


def sid_stores(facts):
    """Every SID store as ``(base register, value)``, the per-voice ones included.

    A merged access unites the copies' registers behind one index, which does not
    take the store's own base away: it still names the register the evidence is about.
    """
    return list(facts.sid) + [(base, v) for base, _idx, v in facts.copies]


def sid_image(facts):
    """``{region: (field name, {element: voice})}`` for the regions the SID image reads."""
    out = {}
    for addr, v in sid_stores(facts):
        if reach(v) > MAXOPS:
            continue
        leaves = [x for x in leaf_loads(v) if x.r in facts.rgn]
        op = next((y.op for y in walk(v) if type(y) is Bin), "")
        for i, x in enumerate(leaves):
            name, voice = sid_name(addr)
            if i:
                name = "%s_%s" % (name, OPNAME.get(op, i))
            r = facts.rgn[x.r]
            hit = out.setdefault(x.r, [name, {}])
            for e in touched(r, x.lo, x.hi):
                hit[1][e] = voice
    return {k: (n, m) for k, (n, m) in out.items()}


def touched(r, lo, hi):
    """The elements of ``r`` an access observably reached, from its envelope."""
    s = max(r.stride, 1)
    lo, hi = max(lo, r.base), min(hi, r.base + r.size - 1)
    return range((lo - r.base) // s, (hi - r.base) // s + 1)


def scales(facts):
    """``{index name: stride}`` for a value that walks a record wider than a byte.

    An index the program uses to reach a 7-byte record is a voice wherever else it
    appears -- which is what makes ``$14CE,X`` voice ``x/7``'s control register.
    """
    out = {}
    for n, rids in facts.idxvar.items():
        strides = [facts.rgn[r].stride for r in rids if r in facts.rgn]
        s = reduce(gcd, [x for x in strides if x > 1], 0)
        if s > 1:
            out[n] = s
    return out


def per_region(facts, per_index):
    """``{region: {value}}`` for every region an index of ``per_index`` walks.

    One reading of :attr:`Facts.idxvar`: the record stride that reaches a region
    (:func:`scales`), or how many elements of a view an index selects.
    """
    out = {}
    for n, rids in facts.idxvar.items():
        for rid in rids if (per_index.get(n) or 1) > 1 else ():
            out.setdefault(rid, set()).add(per_index[n])
    return out


def unclaimed(r, taken, kinds):
    """True when a region of one of ``kinds`` is not already some view's own."""
    return r.id >= 0 and r.kind in kinds and r.id not in taken


def image_copy(facts):
    """``{region: delta}`` for a region a loop copies byte-for-byte into the SID.

    ``sidw($D400 + i, load(R, base + i))`` with one index expression is a shadow
    of the register file: byte ``a`` of ``R`` is register ``a + delta``, so the
    flush loop prints as a copy and every other access to ``R`` by its register.
    The RAM under the register file is one by aliasing, at delta 0.
    """
    out = {
        rid: 0
        for rid, r in facts.rgn.items()
        if r.kind == "state" and SID_REG_LO <= r.base and r.base + r.size - 1 <= SID_REG_HI
    }
    for base, idx, v in facts.copies:
        if type(v) is not Load or v.r not in facts.rgn:
            continue
        rbase, ridx = addr_split(v.a)
        if rbase is None or ridx != idx:
            continue
        r = facts.rgn[v.r]
        if r.kind != "state" or rbase != r.base:  # a table is read, never a shadow
            continue
        out[v.r] = base - rbase
    return out


def update_role(updates, plain, rid):
    """``counter``/``timer``/``acc`` from the shape of a cell's own updates."""
    steps, arith = set(), False
    for e in updates:
        for x in value_walk(e):
            if type(x) is not Bin or x.op not in ("+", "-"):
                continue
            arith = True
            if type(x.b) is Const and type(x.a) is Load and x.a.r == rid:
                steps.add(x.b.v if x.op == "+" else -x.b.v)
    if not steps:
        return "acc" if arith else ""
    if steps <= {1, -1, 255, -255}:
        return "timer" if plain else "counter"
    return "acc"
