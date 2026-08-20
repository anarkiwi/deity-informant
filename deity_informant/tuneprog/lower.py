"""Residualised P-Code -> statements, the access typing, and the machine's frames.

``ops_to_stmts`` is the one-for-one lowering :mod:`.build` lays into blocks;
:class:`Storage` types every access from the trace's access relation, and the
push/pop helpers write the frames :mod:`.stack` later proves away.

Public API: :func:`ops_to_stmts`, :func:`straightline`, :class:`Storage`.
"""

from __future__ import annotations

from ..lifter import STATUS_BITS
from .ir import Bin, Block, Const, IO_HI, IO_LO, Let, Load, Proc, REGVAR, Return, Store, Var
from .ir import STACK_LO, STACK_HI
from .regions import index_regions

BINOP = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
    "INT_CARRY": "carry",
}
PH_INIT = 1
SP = REGVAR[3]


def column(fam, c, w):
    """``T_c[v]``: the value copy ``v`` names where the copies name different ones."""
    lo = fam.base + fam.offset(c)
    idx = Var(fam.var) if w == 1 else Bin("<<", Var(fam.var), Const(1), 2)
    return Load("ram", Bin("+", Const(lo, 2), idx, 2), w, lo, lo + w * fam.k - 1, fam.rid)


def _vn(v, blk, fam=None):
    """A varnode as an IR expression (registers by name, uniques per block)."""
    if v[0] == "c":
        return Const(v[1], v[2])
    if v[0] == "r":
        return Var(REGVAR[v[1]], v[2])
    if v[0] == "h":
        return Var(fam.col(v[1]), v[2]) if fam.hoist else column(fam, v[1], v[2])
    return Var("u%d_%s" % (v[1], blk), v[2])


def _name(v, blk):
    return REGVAR[v[1]] if v[0] == "r" else "u%d_%s" % (v[1], blk)


def ops_to_stmts(ops, resolve=None, blk="0", src=0, src_map=None, fam=None):
    """Residualised P-Code ``ops`` as IR statements.

    ``resolve(op index, size, is_store) -> (cls, lo, hi, region id)`` types each
    memory access; the default is untyped RAM over the whole address space. ``fam``
    reads a copy hole (:mod:`.copymerge`) as its per-copy column.
    """
    out = []
    for i, (mn, res, ins) in enumerate(ops):
        j = src_map[i] if src_map is not None else i
        if mn == "STORE":
            cls, lo, hi, rid = resolve(j, ins[1][2], True) if resolve else ("ram", 0, 0xFFFF, -1)
            a, v = _vn(ins[0], blk, fam), _vn(ins[1], blk, fam)
            out.append(Store(cls, a, v, ins[1][2], lo, hi, rid, src))
        elif mn == "LOAD":
            cls, lo, hi, rid = resolve(j, res[2], False) if resolve else ("ram", 0, 0xFFFF, -1)
            out.append(Let(_name(res, blk), Load(cls, _vn(ins[0], blk, fam), res[2], lo, hi, rid)))
        elif mn in ("COPY", "INT_ZEXT"):
            out.append(Let(_name(res, blk), _vn(ins[0], blk, fam)))
        else:
            w = ins[0][2] if mn == "INT_CARRY" else res[2]
            a, b = _vn(ins[0], blk, fam), _vn(ins[1], blk, fam)
            out.append(Let(_name(res, blk), Bin(BINOP[mn], a, b, w)))
    return out


def straightline(ops, name="f", resolve=None):
    """A one-block :class:`~.ir.Proc` over every register (the fuzz-test shape)."""
    regs = tuple(range(16))
    blocks = {
        "b0": Block("b0", ops_to_stmts(ops, resolve), Return(tuple(Var(REGVAR[i]) for i in regs)))
    }
    return Proc(name, regs, regs, blocks, "b0", "sub")


class Storage:
    """Resolves an access to (class, envelope, region) from the trace's access relation."""

    def __init__(self, trace, regions):
        self.trace = trace
        self.by_addr = index_regions(regions)
        self.acc = {}
        for r in regions:
            for a in r.accessors:
                self.acc.setdefault((tuple(a["site"]), a["op"]), (a["extent"], r))
        lo, hi = trace.meta["load"]
        self.k0 = bytearray(0x10000)
        self.k0[lo:hi] = b"\1" * (hi - lo)
        self.k0[STACK_LO : STACK_HI + 1] = b"\1" * 0x100
        self.k0[IO_LO : IO_HI + 1] = b"\1" * (IO_HI + 1 - IO_LO)
        self.k1 = bytearray(self.k0)
        for a in trace.written_init:
            self.k1[a] = 1

    def chip(self, keys, i):
        """True where this access reached a chip at $D000-$DFFF rather than the RAM under it."""
        return any(self.trace.is_chip(k[0], i) for k in keys if k is not None)

    def cls(self, lo, hi, kind, init_phase):
        if kind == "io":
            return "io"
        k = self.k0 if init_phase else self.k1
        return "ram" if all(k[a] for a in range(lo, hi + 1)) else "chk"

    def at(self, addr, size, init_phase):
        """Type an access at a known constant address (control cells, stack)."""
        r = self.by_addr.get(addr)
        lo, hi = (addr, addr + size - 1) if r is None else (r.base, r.base + r.size - 1)
        kind = "io" if IO_LO <= addr <= IO_HI else (r.kind if r else "state")
        return self.cls(addr, addr + size - 1, kind, init_phase), lo, hi, (r.id if r else -1)

    def resolver(self, key, init_phase):
        return self.resolver_many((key,), init_phase)

    def resolver_many(self, keys, init_phase):
        """Type an access the copies made one: its envelope is the union over ``v``."""

        def resolve(i, size, _store):
            hits = [self.acc.get((k, i)) for k in keys if k is not None]
            hits = [h for h in hits if h is not None]
            if not hits:
                return "chk", 0, 0xFFFF, -1
            lo = min(h[0][0] for h in hits)
            hi = max(h[0][1] for h in hits)
            r = hits[0][1]
            cls = self.cls(lo, hi, r.kind, init_phase)
            if cls != "io" and lo <= IO_HI and hi >= IO_LO and self.chip(keys, i):
                cls = "io"  # the RAM under I/O is a region; this access reached the chip
            return cls, lo, max(hi, lo + size - 1), r.id

        return resolve


SP = REGVAR[3]


def spaddr(out, blk, tag):
    """``$0100 | SP`` as an expression (the stack pointer's current byte)."""
    n = "sp%s_%s" % (tag, blk)
    out.append(Let(n, Bin("|", Const(0x100, 2), Var(SP), 2)))
    return Var(n, 2)


def add_sp(out, delta):
    out.append(Let(SP, Bin("+" if delta > 0 else "-", Var(SP), Const(abs(delta)), 1)))


def push16(out, blk, val, src):
    """The JSR frame: return address high byte then low, SP down by two."""
    for tag, e in (("h", Const((val >> 8) & 0xFF)), ("l", Const(val & 0xFF))):
        a = spaddr(out, blk, tag)
        out.append(Store("raw", a, e, 1, 0x100, 0x1FF, -1, src))
        add_sp(out, -1)


def pop_status(out, blk):
    """The RTI frame: status byte back into the six flag registers, then the pc."""
    add_sp(out, 1)
    a = spaddr(out, blk, "p")
    out.append(Let("pstat_%s" % blk, Load("ram", a, 1, 0x100, 0x1FF, -1)))
    for idx, sh in STATUS_BITS:
        src = Var("pstat_%s" % blk, 1)
        out.append(
            Let(
                REGVAR[idx],
                Bin("&", src if not sh else Bin(">>", src, Const(sh), 1), Const(1), 1),
            )
        )


def tgt(store, addr, size, init_phase, blk, out):
    """Emit the load of a computed-control cell; returns its expression."""
    cls, lo, hi, rid = store.at(addr, size, init_phase)
    n = "t_%s" % blk
    out.append(Let(n, Load(cls, Const(addr, 2), size, lo, hi, rid)))
    return Var(n, size)


def ctrl_expr(node, ls, store, pc, init_phase, blk, out):
    """The switch expression of a computed jump/branch/return, with its loads."""
    ex = node["switch"]["expr"]
    if ex["kind"] == "stack":
        for half in ("lo", "hi"):
            add_sp(out, 1)
            a = spaddr(out, blk, half)
            out.append(Let("p_%s_%s" % (half, blk), Load("ram", a, 1, 0x100, 0x1FF, -1)))
        w = Bin("|", Var("p_lo_%s" % blk, 1), Bin("<<", Var("p_hi_%s" % blk, 1), Const(8), 2), 2)
        return Bin("+", w, Const(1, 2), 2)
    if ex["kind"] == "jmpind":
        ptr = ex["ptr"]
        lo8 = tgt(store, ptr, 1, init_phase, blk + "l", out)
        hi8 = tgt(store, (ptr & 0xFF00) | ((ptr + 1) & 0xFF), 1, init_phase, blk + "h", out)
        return Bin("|", lo8, Bin("<<", hi8, Const(8), 2), 2)
    cell = tgt(store, ex["addr"], ex["size"], init_phase, blk, out)
    if ex["size"] == 2 or ls is None or ls.ctrl[0] != "br":
        return cell
    base = Bin("+", Const((pc + 2) & 0xFFFF, 2), cell, 2)
    return Bin("-", base, Bin("<<", Bin("&", cell, Const(0x80), 1), Const(1), 2), 2)
