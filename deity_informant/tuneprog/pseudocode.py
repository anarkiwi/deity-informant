"""S7 -- the pseudocode itself: one expression, one statement, one structured node.

:class:`Printer` renders values and statements with the S6 names (struct views,
SID registers by voice, 16-bit pairs, compound assignment); :class:`Body` renders
the S5 node tree into indented lines. :mod:`.printer` assembles the document.
"""

from __future__ import annotations

from .idioms import CMP, fold, overflow_of, sext_of, width
from .ir import (
    Assert,
    Bin,
    Call,
    Const,
    COPYVAR,
    Let,
    Load,
    MASK,
    R16,
    REGVAR,
    SID_REG_HI,
    SID_REG_LO,
    Var,
    W16,
)
from .irwalk import addr_split, reads_region, unique_name, use_counts, walk
from .live import needed
from .facts import GLOBAL_REG, SID_VOICE, SID_VOICES, VOICE_REG

INDEX_MAX = 0x200  # how far a table's literal operand may sit from the region's base
CARRY = REGVAR[8]  # every version of it is the carry, which is the name it prints under
NEG = {"==": "!=", "!=": "==", "<": ">=", "<=": ">"}
MIRROR = {"==": "==", "!=": "!=", "<": ">", "<=": ">="}
COMM = ("+", "|", "&", "^")
IND = "    "


def hexlit(v):
    return str(v) if v < 10 else "$%X" % v


# ---- expressions -------------------------------------------------------------
class Printer:
    """Renders one program: expressions, statements, structured nodes."""

    def __init__(self, prog, names, pcs=True):
        self.prog = prog
        self.names = names
        self.pcs = pcs
        self.rgn = prog.by_id()
        self.live, self.params = needed(prog)
        self.alias = {}
        self.tmp = {}
        self.mem = {}
        self.hide = frozenset()
        self.fors = 0
        self.fvars = {}
        self.proc = ""
        self.defs = {}
        self.inline = {}
        self.lastsrc = None
        self.flags = {}

    # ---- names -------------------------------------------------------------
    def var(self, n):
        """A printed variable name: registers keep their letter, uniques become tN."""
        if n in self.alias:
            a, s = self.alias[n]
            return a if s == 1 else "%s*%d" % (a, s)
        if n.startswith("$"):
            return n[1:]
        if n in self.tmp:
            return self.tmp[n]
        base = n.split("#")[0]
        if base == CARRY and "#" in n:
            self.tmp[n] = out = unique_name("carry", self.taken() | set(self.tmp.values()))
            return out
        out = base.lower() if base in REGVAR.values() else "t%d" % (len(self.tmp) + 1)
        while out in self.tmp.values() and not base in REGVAR.values():
            out = "t%d" % (len(self.tmp) + 1)
        self.tmp[n] = out = out + (n.split("#")[1] if base in REGVAR.values() and "#" in n else "")
        return out

    def taken(self):
        """``{"carry"}`` where the procedure already names a folded carry that."""
        if self.proc not in self.flags:
            p = self.prog.procs.get(self.proc)
            self.flags[self.proc] = set() if p is None else {"$carry"} & set(use_counts(p))
        return {n[1:] for n in self.flags[self.proc]}

    def pair(self, lo, hi, a):
        """A 16-bit view reference: the pair's own name, indexed like its low half.

        The pair is named by its two cells, so the name is the word's -- not the low
        half's, whose region a record view may already name by one of its fields.
        """
        name = self.names.u16.get((lo, hi))
        rid = lo[0]
        r = self.rgn.get(rid)
        addr, idx = self.addr_of(a, r)
        if name is None:
            return self.colref(rid, a) or self.cell(rid, addr, idx)
        if rid in self.names.view or rid in self.names.image:
            return self.cell(rid, addr, idx, name=name)
        return name if idx is None or r is None else "%s[%s]" % (name, self.index(r, addr, idx))

    def slot(self, hits, rid, addr, idx):
        """``voice[v].field`` for a cell a per-copy address table names, or ``None``.

        A cell two folds both name (the tick's voices, and the loop init copies
        them with) belongs to whichever of them is being printed; a local group
        names nothing outside the loop that proved it.
        """
        hit = next((h for h in hits if h[0] in self.fvars), hits[0])
        g, fname, j, local = hit
        if local and g not in self.fvars:
            return None
        # a family's cell is copy j's own address wherever it stands; only the
        # local fold, which substituted copy 0's constants, reads the loop index
        i = (self.fvars.get(g) or str(j)) if local else str(j)
        out = "%s[%s].%s" % (g, i, fname)
        r = self.rgn.get(rid)
        if idx is None:
            return out
        if r is None:
            return "%s[%s]" % (out, _bare(self.expr(idx, False)))
        inner = _unoffset(idx, addr - r.zero)
        if addr != r.zero and inner is idx:
            return None  # the index counts from the region, not from this cell
        return "%s[%s]" % (out, self.index(r, r.zero, inner))

    def field(self, rid, r, addr, idx, span=None):
        """``rec[i].field`` for a block the play-phase stride splits into records.

        An access whose index does not step by that stride is not one of its
        elements (a cursor reading the block as a table), and keeps its address.
        Under the transpose the index steps by one, so what says the same is the
        access's own envelope: it must stay inside the one field it names.
        """
        g, stride, fields, flip = self.names.split[rid]
        pos = addr - r.zero
        idx = _unoffset(idx, pos)
        if flip:
            if not self.one_field(r, stride, pos, span):
                return None
            off = pos - pos % stride
            i = self.ivar(idx, 1) or _bare(self.expr(idx)) if idx is not None else str(pos % stride)
            return "%s[%s].%s" % (g, i, fields.get(off, "f%02X" % off))
        off, elem = pos % stride, pos // stride
        i = str(elem)
        if idx is not None:
            hit = self.ivar(idx, stride) or self.scaled(idx, stride)
            if hit is None:
                return None
            i = hit if not elem else "%s + %d" % (hit, elem)
        return "%s[%s].%s" % (g, i, fields.get(off, "f%02X" % off))

    def scaled(self, idx, stride):
        """The index as an element number, when something proves it steps by ``stride``."""
        n = idx.n if type(idx) is Var else None
        return (
            "%s/%d" % (self.expr(idx, False), stride) if self.names.scale.get(n) == stride else None
        )

    def one_field(self, r, stride, pos, span):
        """True when this access's whole observed extent lies in the field ``pos`` names.

        An access with no envelope (a 16-bit view, whose halves name regions) proves
        nothing, so it keeps the address; the init loop that made the block one
        region reaches all of it, and so is not one of the fields play walks.
        """
        if span is None or pos < 0:
            return False
        lo, hi = span[0] - r.zero, span[1] - r.zero
        return lo // stride == hi // stride == pos // stride

    def cell(self, rid, addr, idx=None, name=None, span=None):
        """A storage reference: ``voice[v].field``, ``NAME[i]`` or a scalar's name."""
        hit = self.names.slots.get((rid, addr))
        hit = self.slot(hit, rid, addr, idx) if hit else None
        if hit is not None:
            return hit
        r = self.rgn.get(rid)
        if rid in self.names.split and r is not None and addr is not None:
            hit = self.field(rid, r, addr, idx, span)
            if hit is not None:
                return hit
        if r is None:
            return "mem[%s]" % (self.expr(idx) if idx is not None else hexlit(addr))
        name = name or self.names.region.get(rid, "r%d" % rid)
        if rid in self.names.image and addr is not None:
            return self.regcell(name, addr + self.names.image[rid], _unoffset(idx, addr - r.zero))
        view = self.names.view.get(rid)
        elem = self.names.elem.get(rid)
        field = name if name != self.names.region.get(rid) else view[1] if view else name
        if view is not None and elem is None:
            return "%s[%s].%s" % (view[0], self.index(r, addr, idx), field)
        if view is not None:
            return "%s[%d].%s%s" % (view[0], elem, field, self.offset(r, addr, idx))
        if r.size == 1 or (idx is None and addr == r.base and r.size <= 2):
            return name
        return "%s[%s]" % (name, self.index(r, addr, idx))

    def offset(self, r, addr, idx):
        """The byte offset inside one element of a struct view, or ``''``."""
        if idx is None:
            return "" if addr == r.base else "[%d]" % (addr - r.base)
        return "[%s]" % (self.ivar(idx, 1) or _bare(self.expr(idx, False)))

    def index(self, r, addr, idx):
        """The element index of an access: a constant, or the loop variable."""
        if idx is None:
            return str((addr - r.zero) // max(r.stride, 1))
        hit = self.ivar(idx, r.stride)
        if hit is not None:
            return hit
        e = self.expr(idx, False)
        return _bare(e) if r.stride == 1 else "%s/%d" % (e, r.stride)

    def ivar(self, idx, stride):
        """The loop variable, when the index is it scaled by the element size."""
        t, step = type(idx), max(stride, 1)
        if t is Var and idx.n in self.alias and self.alias[idx.n][1] == stride:
            return self.alias[idx.n][0]
        if t is not Bin:
            return None
        if idx.op == "*" and type(idx.b) is Const and type(idx.a) is Var:
            if idx.b.v != step:
                return None
            hit = self.alias.get(idx.a.n)
            return self.var(idx.a.n) if hit is None else (hit[0] if hit[1] == 1 else None)
        if idx.op == "+" and type(idx.a) is Const and not idx.a.v % step:
            inner = self.ivar(idx.b, stride)
            return None if inner is None else "%s + %s" % (inner, hexlit(idx.a.v // step))
        return None

    def addr_of(self, e, r):
        """``(constant address, index expression)`` of an access to region ``r``.

        The literal operand is not the table start (anatomy section 7: 1-based
        tables read as ``base-1,Y``, dispatch tables placed at ``base-$80``), so
        indices count from the region's recovered origin and the distance from it
        moves into the index: ``T[y]``, and a look-ahead sibling ``T[y + 1]``.
        """
        if type(e) is Const:
            return e.v, None
        if type(e) is Bin and e.op == "+" and r is not None:
            for k, i in ((e.a, e.b), (e.b, e.a)):
                if type(k) is not Const or abs(k.v - r.base) > INDEX_MAX:
                    continue
                if k.v < r.zero:
                    return k.v, Bin("-", i, Const(r.zero - k.v, 2), 2)
                d = k.v - r.zero
                return k.v, Bin("+", Const(d), i, 2) if d else i
        return None, e

    def regcell(self, name, addr, idx=None):
        """``sid[v].reg`` by voice; ``NAME.reg[i]`` when the register is data.

        An index that steps by the 7-byte voice block is a voice (a copy loop, a
        voice-offset table, a routine's voice argument); anything else selects the
        *register*, which a clear loop over the register file really does.
        """
        if idx is None:
            if addr in GLOBAL_REG:
                return "%s.%s" % (name, GLOBAL_REG[addr])
            v, k = divmod(addr - SID_REG_LO, SID_VOICE)
            return "%s[%d].%s" % (name, v, VOICE_REG[k])
        i = self.voiced(idx) if addr - SID_REG_LO < SID_VOICE * SID_VOICES else None
        if i is None:
            off = addr - SID_REG_LO
            e = _bare(self.expr(idx, False))
            return "%s.reg[%s]" % (name, "%s + %s" % (hexlit(off), e) if off else e)
        v, k = divmod(addr - SID_REG_LO, SID_VOICE)
        return "%s[%s].%s" % (name, "%s + %s" % (i, hexlit(v)) if v else i, VOICE_REG[k])

    def voiced(self, idx):
        """The index as a voice number: seven per voice, or an entry of the voice map."""
        return self.ivar(idx, SID_VOICE) or self.scaled(idx, SID_VOICE) or self.voicemap(idx)

    def voicemap(self, idx):
        """The element a read of the voice -> register-offset map selects, or ``None``.

        Entry ``i`` of such a table is ``7 * i`` (:func:`~.facts.voice_maps`), so the
        register the index reaches is voice ``i``'s, whatever the tune calls the table.
        """
        if type(idx) is not Load or idx.r not in self.names.voicemap:
            return None
        r = self.rgn.get(idx.r)
        addr, i = self.addr_of(idx.a, r)
        return None if r is None or addr is None else self.index(r, addr, i)

    # ---- expressions -------------------------------------------------------
    def expr(self, e, top=True):
        e = fold(e)
        hit = self.held(e)
        if hit is not None:
            return hit
        t = type(e)
        if t is Const:
            return hexlit(e.v)
        if t is Var:
            return self.expr(self.inline[e.n], top) if e.n in self.inline else self.var(e.n)
        if t is Load:
            return self.load(e)
        if t is R16:
            return self.pair(e.lo, e.hi, e.a)
        a, b = e.a, e.b
        if e.op == "^" and type(b) is Const and b.v == MASK[e.w] and width(a) <= e.w:
            return "~%s" % self.expr(a, False)
        if e.op in ("==", "!=") and type(b) is Const:
            hit = self.held(Bin("-", a, b, 1))
            if hit is not None:  # x == k is the cell that holds x - k against zero
                s = "%s %s 0" % (hit, e.op)
                return s if top else "(%s)" % s
        if e.op in ("==", "!=") and type(b) is Const and b.v == 0 and _signbit(a):
            v = overflow_of(a.a)
            if v is not None:
                s = "overflow(%s - %s)" % (self.expr(v[0], False), self.expr(v[1], False))
                if e.op == "!=":
                    return s
                return "not %s" % s if top else "(not %s)" % s
            s = "%s %s 0" % (self.expr(a.a, False), ">=" if e.op == "==" else "<")
            return s if top else "(%s)" % s
        hit = sext_of(e)
        if hit is not None:
            s = "sext(%s)" % self.expr(hit[1], False)
            s = s if hit[0] is None else "%s + %s" % (self.expr(hit[0], False), s)
            return "(%s)" % s
        if e.op in CMP and type(a) is Const and type(b) is not Const:
            s = "%s %s %s" % (self.expr(b, False), MIRROR[e.op], self.expr(a, False))
            return s if top else "(%s)" % s
        if e.op == "carry":
            return "carry(%s + %s)" % (self.expr(a, False), self.expr(b, False))
        s = "%s %s %s" % (self.expr(a, False), e.op, self.expr(b, False))
        return s if top and e.op in CMP else "(%s)" % s

    def load(self, e):
        if e.cls == "io":
            a = self.addr_of(e.a, None)[0]
            return "input($%04X)" % a if a is not None else "input(%s)" % self.expr(e.a, False)
        hit = self.colref(e.r, e.a)
        if hit is not None:
            return hit
        r = self.rgn.get(e.r)
        addr, idx = self.addr_of(e.a, r)
        if r is None and addr is not None:
            return "mem[%s]" % hexlit(addr)
        return self.cell(e.r, addr, idx, span=(e.lo, e.hi))

    def colref(self, rid, a):
        """``voice[v].field`` for an access through a per-copy column, or ``None``.

        The column read is the address, so the index is the copy the access
        itself names -- no constant of the merged body can be mistaken for it.
        """
        base, rest = (a.a, a.b) if type(a) is Bin and a.op == "+" else (a, None)
        for x, y in ((base, rest), (rest, base)) if rest is not None else ((base, None),):
            hit = self.names.column.get((x.r, x.lo)) if type(x) is Load else None
            j = _copyidx(x) if hit is not None else None
            if hit is None or hit[2] != rid or j is None:
                continue
            out = "%s[%s].%s" % (hit[0], self.expr(j, False), hit[1])
            return out if y is None else "%s[%s]" % (out, _bare(self.expr(y, False)))
        return None

    # ---- statements --------------------------------------------------------
    def stmt(self, s):
        t = type(s)
        if t is W16:
            return self.word(s)
        if t is Let:
            return "%s = %s" % (self.var(s.n), self.expr(s.e))
        if t is Assert:
            return "assert %s" % self.expr(s.e)
        if t is Call:
            return self.call(s)
        return self.store(s)

    def word(self, s):
        """A folded 16-bit assignment, compound when the pair is its own operand."""
        lhs, e, out = self.pair(s.lo, s.hi, s.a), s.e, None
        sides = (
            ()
            if type(e) is not Bin
            else (((e.a, e.b), (e.b, e.a)) if e.op in COMM else ((e.a, e.b),))
        )
        for x, y in sides:
            if (
                type(x) is R16
                and (x.lo, x.hi) == (s.lo, s.hi)
                and _bare(self.expr(x, False)) == lhs
            ):
                out = "%s %s= %s" % (lhs, e.op, self.expr(y, False))
                break
        if out is None:
            out = "%s = %s" % (lhs, self.expr(e))
        self.forget(s.lo[0], s.hi[0])
        return out

    def call(self, s):
        p = self.prog.procs[s.proc]
        lhs = [self.var(r) for i, r in zip(p.rets, s.rets) if r in self.live[self.proc]]
        args = [
            "%s=%s" % (REGVAR[i].lower(), self.expr(a))
            for i, a in zip(p.params, s.args)
            if i in self.params[s.proc]
        ]
        call = "%s(%s)" % (self.names.procs.get(s.proc, s.proc), ", ".join(args))
        self.mem = {}  # the callee writes what it likes: no cell still holds what it held
        return "%s = %s" % (", ".join(lhs), call) if lhs else call

    def store(self, s):
        r = self.rgn.get(s.r)
        addr, idx = self.addr_of(s.a, r)
        if s.cls == "io":
            base, i = addr_split(s.a)
            if base is not None and SID_REG_LO <= base <= SID_REG_HI:
                lhs = self.regcell("sid", base, i)
            else:
                lhs = "io[%s]" % (hexlit(base) if base is not None else self.expr(s.a, False))
        else:
            lhs = self.colref(s.r, s.a) or self.cell(s.r, addr, idx, span=(s.lo, s.hi))
        out = self.compound(lhs, s, (addr, idx))
        self.forget(s.r)
        if s.cls != "io" and type(s.v) is not Const:
            self.mem[fold(s.v)] = (lhs, s.r)
        return out

    def compound(self, lhs, s, split):
        """``x += k`` when the stored value is the cell's own value plus a constant."""
        v = fold(s.v)
        if type(v) is Bin and v.op in ("+", "-", "&", "|", "^", "<<", ">>"):
            a = self.defs.get(v.a.n, v.a) if type(v.a) is Var else v.a
            if type(a) is Load and a.r == s.r and self.same_cell(a.a, s.a, s.r, split):
                if type(v.b) is Const and v.op in ("+", "-") and v.b.v > 0xF0:
                    return "%s %s= %s" % (lhs, "-" if v.op == "+" else "+", hexlit(0x100 - v.b.v))
                return "%s %s= %s" % (lhs, v.op, self.expr(v.b, False))
        return "%s = %s" % (lhs, self.expr(s.v))

    def same_cell(self, load, store, rid, split):
        """True when a load names the very cell a store writes.

        A literal address is compared through the region's origin -- base *and*
        index, since one element on is a different cell; an address the program
        computes names the same cell only when it is the same expression.
        """
        if split[0] is None:
            return load == store
        return self.addr_of(load, self.rgn.get(rid)) == split

    def held(self, e):
        """The cell that holds the value of ``e``, or ``None``."""
        hit = self.mem.get(e)
        return hit[0] if hit is not None else None

    def forget(self, *rids):
        """Drop what a write to ``rids`` invalidates: their own cells, and values reading them."""
        keep = self.mem.items()
        self.mem = {k: v for k, v in keep if v[1] not in rids and not reads_region(k, rids)}


def _copyidx(e):
    """The copy a column read names: its index, or the one a pinned address selects."""
    hit = next((x for x in walk(e.a) if type(x) is Var and x.n.startswith(COPYVAR)), None)
    if hit is not None:
        return hit
    a = fold(e.a)
    return Const((a.v - e.lo) // max(e.w, 1)) if type(a) is Const else None


def _bare(s):
    return s[1:-1] if s.startswith("(") and s.endswith(")") else s


def _unoffset(idx, d):
    """An index with the constant :meth:`Printer.addr_of` folded into it removed."""
    if d and type(idx) is Bin and idx.op == "+" and type(idx.a) is Const and idx.a.v == d:
        return idx.b
    return idx


def _signbit(e):
    return type(e) is Bin and e.op == "&" and type(e.b) is Const and e.b.v == 0x80
