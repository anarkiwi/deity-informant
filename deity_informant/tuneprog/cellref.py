"""S6 -- how storage is spelled: a cell, a struct field, a copy's slot, a chip register.

One reference per shape, over the names :mod:`.recover` and :mod:`.views` recovered:
``voice[v].field`` for a per-copy address table, ``NAME[i]`` for a region an index
walks, ``sid[v].reg`` for the register file, and the pair's own name for a 16-bit
view. :class:`~.pseudocode.Printer` renders expressions and statements over these.
"""

from __future__ import annotations

from .facts import GLOBAL_REG, SID_VOICE, SID_VOICES, VOICE_REG
from .halves import register
from .idioms import fold
from .ir import Bin, Const, COPYVAR, Load, SID_REG_LO, Var
from .irwalk import addr_split, walk
from .live import needed

INDEX_MAX = 0x200  # how far a table's literal operand may sit from the region's base


def hexlit(v):
    return str(v) if v < 10 else "$%X" % v


class Cells:
    """The storage-reference vocabulary, and the state every reference reads.

    A subclass supplies the two spellings of a value -- :meth:`expr` for an index,
    :meth:`var` for a name -- which is the expression printer
    (:class:`~.pseudocode.Printer`).
    """

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
        self.sites = {}

    def expr(self, e, top=True):
        raise NotImplementedError

    def var(self, n):
        raise NotImplementedError

    def pair(self, lo, hi, a):
        """A 16-bit view reference: the pair's own name, indexed like its low half.

        The pair is named by its two cells, so the name is the word's -- not the low
        half's, whose region a record view may already name by one of its fields.
        """
        name = self.names.u16.get((lo, hi))
        rid = lo[0]
        r = self.rgn.get(rid)
        addr, idx = self.addr_of(a, r)
        reg = register((lo, hi)) if r is not None and r.kind == "io" else None
        if reg is not None:  # the chip's own register, addressed like any io store
            return self.regcell("sid", *addr_split(a), reg)
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

    def cell(self, rid, addr, idx=None, name=None, span=None, fld=None):
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
            off = _unoffset(idx, addr - r.zero)
            return self.regcell(name, addr + self.names.image[rid], off, fld)
        view = self.names.view.get(rid)
        elem = self.names.elem.get(rid)
        field = fld or (name if name != self.names.region.get(rid) else view[1] if view else name)
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

    def regcell(self, name, addr, idx=None, field=None):
        """``sid[v].reg`` by voice; ``NAME.reg[i]`` when the register is data.

        An index that steps by the 7-byte voice block is a voice (a copy loop, a
        voice-offset table, a routine's voice argument); anything else selects the
        *register*, which a clear loop over the register file really does. ``field``
        overrides the register the low address names, for a 16-bit pair.
        """
        if idx is None:
            if addr in GLOBAL_REG:
                return "%s.%s" % (name, field or GLOBAL_REG[addr])
            v, k = divmod(addr - SID_REG_LO, SID_VOICE)
            return "%s[%d].%s" % (name, v, field or VOICE_REG[k])
        i = self.voiced(idx) if addr - SID_REG_LO < SID_VOICE * SID_VOICES else None
        if i is None:
            off = addr - SID_REG_LO
            e = _bare(self.expr(idx, False))
            return "%s.reg[%s]" % (name, "%s + %s" % (hexlit(off), e) if off else e)
        v, k = divmod(addr - SID_REG_LO, SID_VOICE)
        return "%s[%s].%s" % (name, "%s + %s" % (i, hexlit(v)) if v else i, field or VOICE_REG[k])

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

    def site(self, rid, text, kind):
        """Record the printed form of one accessor, which :mod:`.datablock` states."""
        if rid >= 0:
            k, p = self.sites.setdefault((rid, text), (set(), set()))
            k.add(kind)
            p.add(self.names.procs.get(self.proc, self.proc))
        return text

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
