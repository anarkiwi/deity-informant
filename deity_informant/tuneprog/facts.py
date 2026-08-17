"""S6 -- the facts the names are derived from: one pass over the IR, per cell.

What a store writes back into its own cell is a counter or a timer, what a load
indexes is a cursor, what flows into a SID register is that register's shadow.
:class:`Facts` gathers all of it once, per region and per cell.
"""

from __future__ import annotations

from functools import reduce
from math import gcd

from .ir import Bin, Call, Const, Let, Load, SID_REG_LO, SID_REG_HI, Store, Var
from .irwalk import addr_split, expand, loads, reachable, walk

VOICE_REG = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
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


def leaf_loads(e):
    """The loads of ``e`` that are values, not parts of an address."""
    ls = loads(e)
    inner = {id(y) for x in ls for y in walk(x.a)}
    return [x for x in ls if id(x) not in inner]


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
        self.cellindex = set()
        self.addr = set()
        self.reads = {}
        self.writes = {}
        self.wpc = {}
        self.tick = reachable(prog, prog.meta.get("tick_proc")) or set(prog.procs)
        for name, p in prog.procs.items():
            self.reads[name], self.writes[name] = set(), set()
            for b in p.blocks.values():
                self.block(name, b)

    def block(self, name, b):
        defs = {}
        for s in b.stmts:
            if type(s) is Let:
                if not s.n.startswith("$"):
                    defs[s.n] = s.e
                self.value(name, expand(s.e, defs, DEPTH))
            elif type(s) is Store:
                v, a = expand(s.v, defs, DEPTH), expand(s.a, defs, DEPTH)
                self.value(name, v)
                self.value(name, a)
                self.store(name, s, v, a)
            elif type(s) is Call:
                for a in s.args:
                    self.value(name, expand(a, defs, DEPTH))

    def value(self, name, e):
        """Record what an expression reads: regions, index uses, pointer uses."""
        for x in loads(e):
            self.reads[name].add(x.r)
            self.walks(x.r, x.a)
            for y in loads(x.a):
                self.index.setdefault(y.r, set()).add(x.r)
                if type(y.a) is Const:
                    self.cellindex.add((y.r, y.a.v))
                if y.w == 2:
                    self.addr.add(y.r)

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
        cell = (s.r, a.v if type(a) is Const else None)
        if same and ops(v) <= MAXOPS:
            self.updates.setdefault(s.r, set()).add(v)
            self.cellupd.setdefault(cell, set()).add(v)
        else:
            self.plain.add(s.r)
            self.cellplain.add(cell)


def sid_name(addr):
    """``(field name, voice)`` for a SID register address."""
    if addr in GLOBAL_REG:
        return GLOBAL_REG[addr], None
    v, k = divmod(addr - SID_REG_LO, 7)
    return VOICE_REG[k], v


def sid_image(facts):
    """``{region: (field name, {element: voice})}`` for the regions the SID image reads."""
    out = {}
    for addr, v in facts.sid:
        if ops(v) > MAXOPS:
            continue
        leaves = [x for x in leaf_loads(v) if x.r in facts.rgn]
        op = next((y.op for y in walk(v) if type(y) is Bin), "")
        for i, x in enumerate(leaves):
            name, voice = sid_name(addr)
            if i:
                name = "%s_%s" % (name, OPNAME.get(op, i))
            r = facts.rgn[x.r]
            hit = out.setdefault(x.r, [name, {}])
            if type(x.a) is Const:
                hit[1][(x.a.v - r.base) // max(r.stride, 1)] = voice
    return {k: (n, m) for k, (n, m) in out.items()}


def _scales(facts):
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


def image_copy(facts):
    """``{region: delta}`` for a region a loop copies byte-for-byte into the SID.

    ``sidw($D400 + i, load(R, base + i))`` with one index expression is a shadow
    of the register file: byte ``a`` of ``R`` is register ``a + delta``, so the
    flush loop prints as a copy and every other access to ``R`` by its register.
    """
    out = {}
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
