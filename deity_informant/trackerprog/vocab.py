"""B7 -- what the object calls the thing one load or store names.

The leaf half of :mod:`.lower`: a register from T0, an instrument column, the
tuning through ``transpose``, the instrument's own pulse pair, or a named cell.
A leaf with no name here is a refusal, and the score supplies the score's bytes.
"""

from __future__ import annotations

from ..tuneprog.ir import Bin, Const, Load, Var
from ..tuneprog.irwalk import addr_split
from .lower import Unlowerable, masked
from .universal import REGNAME

COLUMN = "b"  # the one column a table materialised as a stream of its own bytes has
SIDBASE = 0xD400


def _shift(e):
    """``(term, 2**k)`` where an index is a left shift by a constant, else ``(e, 1)``."""
    if type(e) is Bin and e.op == "<<" and type(e.b) is Const:
        return e.a, 1 << e.b.v
    return e, 1


def _plus(e):
    """``(term, constant)`` of an expression written as a sum with one constant."""
    if type(e) is Bin and e.op == "+":
        if type(e.b) is Const:
            return e.a, e.b.v
        if type(e.a) is Const:
            return e.b, e.a.v
    return e, 0


class Vocab:
    """One tune's leaf vocabulary: the names its loads and stores resolve to."""

    def __init__(self, cells, img, regs, vidx):
        self.cells, self.img, self.regs, self.vidx = cells, img, regs, vidx
        self.supplied = set()
        self.pitch = ()  # the tuning: region ids, the halves' origins, step, entries
        self.notebase = None
        self.insbase = None
        self.inscol = {}  # region id -> column name
        self.inspw = {}  # region id -> "lo" | "hi"
        self.insstride = 8
        self.dropstores = set()
        self.subst = {}  # SSA name -> a node the schedule states outright
        self.tables = {}  # stream name -> (base, top): a const table read at a cell
        self.rowblocks = frozenset()  # the fetch's own blocks: their bytes are the score
        self.shadow = ()  # (base, size): the register file every write lands in
        # (score byte, mask) -> the section 3.6 event field that value is
        self.fields = {}

    # ---- loads -------------------------------------------------------------------
    def load(self, low, x):
        own, at = addr_split(x.a)
        base, idx = addr_split(low.expand(x.a))
        if base is None:
            base, idx = own, at
        if base is None:
            raise Unlowerable("computed address")
        got = self.imaged(base, x.w, idx is not None and low.isvoice(idx))
        if got is not None:
            return {"cell": got}
        if idx is None:
            return self.scalar(low, base, x)
        if low.isvoice(idx):
            return {"cell": self.cells.voicecell(base)}
        got = self.tuning(low, base, idx)
        if got is not None:
            return got
        raw = at if own == base else idx  # the index's own reading, not its folded value
        if x.r in self.inscol and self.isins(low, raw):
            return {"ins": self.inscol[x.r]}
        if x.r in self.inspw and self.isins(low, raw):
            return {"cell": "ins.pw." + self.inspw[x.r]}
        got = self.table(low, x, base, idx)
        return got if got is not None else self._no(x)

    def table(self, low, x, base, idx):
        """A table read at a cell: section 5's ``tabcell`` over the bytes it holds.

        Where the play never writes the region the image states its bytes once
        and for all, so the read is one row of a declared stream -- one row a
        byte, at the very index the read's own address has, and the rows are the
        extent the certified horizon reached and no more.

        The index is the address's **own** term and not the folded one: a fold
        substitutes a store's value where the store's own inputs may since have
        moved, and a cursor a row steps is exactly that.
        """
        top = x.hi
        if x.w != 1 or top < base or not low.frozen(base, top - base + 1):
            return None
        if low.lbl in self.rowblocks:  # the bytes a fetch read are the score's own
            return None
        idx = addr_split(x.a)[1] if addr_split(x.a)[0] == base else idx
        name = "T%04X" % base
        # one stream a table: every read of it, so the rows are the whole extent
        self.tables[name] = (base, max(top, self.tables.get(name, (0, 0))[1]))
        return {"tabcell": [name, low.value(idx), COLUMN]}

    @staticmethod
    def _no(x):
        raise Unlowerable("$%04X[..]" % x.lo)

    def scalar(self, low, base, x):
        got = self.cells.at(base)
        if got and got[0] == "pitch":
            return int(self.img[base])
        r = self.cells.region(base)
        if r is not None and self.cells.istable(r.id) and r.size == 1:
            return int(self.img[base])
        if got is None and r is None and low.frozen(base, x.w):
            return int.from_bytes(self.img[base : base + x.w], "little")
        if x.w == 2:
            got = self.word(low, base)
            if got is not None:
                return got
        name = self.named(low, base, x.w)
        if name.endswith(".lo"):
            return masked({"global": name[1:-3]}, 1)
        return {"global": name[1:]} if name.startswith("#") else {"cell": name}

    def word(self, low, base):
        """A 16-bit read of two bytes the object names apart: the halves, joined."""
        lo, hi = self.half(low, base), self.half(low, base + 1)
        if lo is None or hi is None or (isinstance(lo, int) and isinstance(hi, int)):
            return None
        return {"or": [lo, hi * 256 if isinstance(hi, int) else {"shl": [hi, 8]}]}

    def half(self, low, addr):
        """One byte of a word: the cell that holds it, or the byte the image holds."""
        name = self.cells.name(addr, True)
        if name is not None:
            return {"global": name[1:]} if name.startswith("#") else {"cell": name}
        return int(self.img[addr]) if low.frozen(addr, 1) else None

    def isins(self, low, idx):
        """Whether an index selects the record the voice is playing: ``stride * ins``.

        A byte the score supplied is no cell of the tune's own, however a fold
        makes the two values one.
        """
        if self.fromscore(low, idx):
            return False
        e, k = _shift(low.expand(idx))
        return k == self.insstride and self.sameread(low, e, self.insbase)

    def fromscore(self, low, e):
        """Whether an index is a byte the score supplied: no cell of the tune's own.

        A tune that writes the selecting cell from the byte it also indexes by
        folds the two into one value, and only the reading tells them apart.
        """
        for _ in range(8):
            if type(e) is not Var:
                return False
            if e.n in self.supplied:
                return True
            if e.n not in low.defs:
                return False
            e = low.defs[e.n]
        return False

    def cellread(self, low, base):
        """One per-voice cell as the lowering sees it, for comparing two readings."""
        v = sorted(self.vidx)[0]
        e = Load("ram", Bin("+", Const(base, 2), Var(v, 1), 2), 1, base, base + 2, -1)
        return low.expand(e)

    def sameread(self, low, term, base):
        """Whether an expression is the same reading of one per-voice cell as the object's."""
        got = self.cellread(low, base)
        if term == got:
            return True
        if type(term) is not Load or type(got) is not Load:
            return False
        a, ai = addr_split(term.a)
        b, bi = addr_split(got.a)
        return a == b and ai is not None and bi is not None and low.isvoice(ai)

    def tuning(self, low, base, idx):
        """A read of the tuning: the voice's own note moved by a constant (section 3.2).

        One word table is indexed by the note doubled and its halves are one byte
        apart; two byte tables are indexed by the note itself and are a half each.
        """
        if not self.pitch or self.notebase is None:
            return None
        obases, step, n = self.pitch[1], self.pitch[2], self.pitch[3]
        # the halves nearest origin first: two byte tables stand one after the
        # other, so an address of the second is no offset into the first
        for half, org in sorted(enumerate(obases), key=lambda x: -x[1]):
            off, rem = divmod(base - org, step)
            if rem or not 0 <= off < n + 32:
                continue
            e = low.expand(idx)
            if step == 2:
                if not (type(e) is Bin and e.op == "<<" and type(e.b) is Const and e.b.v == 1):
                    continue
                e = e.a
            term, k = _plus(low.expand(e))
            if self.sameread(low, term, self.notebase):
                word = {"transpose": k + off}
            else:
                delta = {"sub": [low.value(e), {"cell": "note"}]}
                word = {"transpose": delta if not off else {"add": [delta, off]}}
            return masked(word if not half else {"shr": [word, 8]}, 1)
        return None

    def named(self, low, base, w):
        """The cell one constant address is, declaring a byte no region names.

        An address that is another voice's *copy* is no cell of the committing
        voice's own row; a byte the play also reads as a word is that word's half.
        """
        got = self.cells.name(base, True)
        if got is not None:
            return got
        if (self.cells.at(base) or (None,))[0] == "voice":
            raise Unlowerable("$%04X" % base)
        name = self.cells.bytecell(base)
        if self.cells.widths.get(name, 1) != 2:
            return name
        low.wide.add(name[1:])
        return name if w == 2 else name + ".lo"

    def imaged(self, base, w, voiced):
        """The cell one byte of the register file is read as: section 3.1's shadow.

        A half of a pair section 5 reads as one cell is that cell's own half; a
        register the pair does not hold is written by name and read nowhere.
        """
        if not self.shadow:
            return None
        off = base - self.shadow[0]
        if not 0 <= off < self.shadow[1]:
            return None
        name = self.regs.get(off) if voiced else REGNAME.get(off, "").rpartition(".")[2]
        if name is None or not name.endswith(("_lo", "_hi")):
            return None
        return "shadow." + name[:-3] + ("" if w == 2 else "." + name[-2:])

    def imagedstore(self, base, w, voiced):
        """The target one store into the register file has: a pair's cell, or a register."""
        got = self.imaged(base, w, voiced)
        if got is not None:
            return ("cell", "@" + got)
        if not self.shadow:
            return None
        off = base - self.shadow[0]
        if not 0 <= off < self.shadow[1]:
            return None
        name = self.regs.get(off) if voiced else REGNAME.get(off)
        return None if name is None else ("reg", name)

    # ---- stores -------------------------------------------------------------------
    def target(self, low, s):
        """``(kind, name)`` one store writes, or ``None`` where the object drops it."""
        if s.cls == "io":
            # the register is the store's own address: a file written through one
            # indexed store has one region for all of it (section 3.1)
            base = addr_split(low.expand(s.a))[0]
            base = addr_split(s.a)[0] if base is None else base
            reg = None if base is None else self.regs.get(base - SIDBASE)
            if reg is None:
                raise Unlowerable("$%04X" % s.src)
            return ("reg", reg)
        if s.src in self.dropstores:
            return None
        own, at = addr_split(s.a)
        base, idx = addr_split(low.expand(s.a))
        if base is None:
            base, idx = own, at
        if base is None:
            raise Unlowerable("computed address")
        voiced = idx is not None and low.isvoice(idx)
        if self.shadow and (idx is None or voiced):
            got = self.imagedstore(base, s.w, voiced)
            if got is not None:
                return got
        if idx is None:
            got = self.cells.at(base)
            if got is not None and got[0] == "voice":
                self.cells.name(base, True)
                return ("copy", got[1])
            name = self.named(low, base, s.w)
            return ("cell", name if name.startswith("#") else "@" + name)
        if low.isvoice(idx):
            return ("cell", "@" + self.cells.voicecell(base))
        if s.r in self.inspw and self.isins(low, at if own == base else idx):
            return ("acc", "ins.pw." + self.inspw[s.r])
        raise Unlowerable("$%04X[..]" % s.lo)
