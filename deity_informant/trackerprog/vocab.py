"""B7 -- what the object calls the thing one load or store names.

The leaf half of :mod:`.lower`: a register from T0, an instrument column, the
tuning through ``transpose``, the instrument's own pulse pair, or a named cell.
A leaf with no name here is a refusal, and the score supplies the score's bytes.
"""

from __future__ import annotations

from ..tuneprog.ir import Bin, Const, Load, Var
from ..tuneprog.irwalk import addr_split
from .lower import Unlowerable, masked


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
        self.pitch = ()  # (region id, base address, entries)
        self.notebase = None
        self.insbase = None
        self.inscol = {}  # region id -> column name
        self.inspw = {}  # region id -> "lo" | "hi"
        self.insstride = 8
        self.dropstores = set()
        self.dropguards = set()
        self.subst = {}  # SSA name -> a node the schedule states outright

    # ---- loads -------------------------------------------------------------------
    def load(self, low, x):
        base, idx = addr_split(low.expand(x.a))
        if base is None:
            base, idx = addr_split(x.a)
        if base is None:
            raise Unlowerable("computed address")
        if idx is None:
            return self.scalar(low, base, x)
        if low.isvoice(idx):
            return {"cell": self.cells.voicecell(base)}
        got = self.tuning(low, base, idx)
        if got is not None:
            return got
        if x.r in self.inscol:
            return {"ins": self.inscol[x.r]} if self.isins(low, idx) else self._no(x)
        if x.r in self.inspw:
            return {"cell": "ins.pw." + self.inspw[x.r]} if self.isins(low, idx) else self._no(x)
        return self._no(x)

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
        if x.cls == "chk":
            raise Unlowerable("$%02X" % base)
        name = self.cells.name(base, True)
        if name is None:
            raise Unlowerable("$%04X" % base)
        del low
        return {"global": name[1:]} if name.startswith("#") else {"cell": name}

    def isins(self, low, idx):
        """Whether an index selects the record the voice is playing: ``stride * ins``."""
        e = low.expand(idx)
        if type(e) is Bin and e.op == "<<" and type(e.b) is Const:
            e, k = e.a, 1 << e.b.v
        else:
            k = 1
        return k == self.insstride and self.sameread(low, e, self.insbase)

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
        """A read of the tuning: the voice's own note moved by a constant (section 3.2)."""
        if not self.pitch:
            return None
        pbase, n = self.pitch[1], self.pitch[2]
        off = base - pbase
        if not 0 <= off < 2 * n + 64:
            return None
        e = low.expand(idx)
        if not (type(e) is Bin and e.op == "<<" and type(e.b) is Const and e.b.v == 1):
            return None
        term, k = _plus(low.expand(e.a))
        if self.notebase is None:
            return None
        if self.sameread(low, term, self.notebase):
            word = {"transpose": k + off // 2}
        else:
            delta = {"sub": [low.value(e.a), {"cell": "note"}]}
            word = {"transpose": delta if not off else {"add": [delta, off // 2]}}
        return masked(word if off % 2 == 0 else {"shr": [word, 8]}, 1)

    # ---- stores -------------------------------------------------------------------
    def target(self, low, s):
        """``(kind, name)`` one store writes, or ``None`` where the object drops it."""
        if s.cls == "io":
            reg = self.regs.get(s.r)
            if reg is None:
                raise Unlowerable("$%04X" % s.src)
            return ("reg", reg)
        if s.cls == "chk" or s.src in self.dropstores:
            return None
        base, idx = addr_split(low.expand(s.a))
        if base is None:
            base, idx = addr_split(s.a)
        if base is None:
            raise Unlowerable("computed address")
        if idx is None:
            name = self.cells.name(base, True)
            if name is None:
                raise Unlowerable("$%04X" % base)
            return ("cell", name if name.startswith("#") else "@" + name)
        if low.isvoice(idx):
            return ("cell", "@" + self.cells.voicecell(base))
        if s.r in self.inspw and self.isins(low, idx):
            return ("acc", "ins.pw." + self.inspw[s.r])
        raise Unlowerable("$%04X[..]" % s.lo)

    def drop_guard(self, low, c, truth):
        del low, truth
        return id(c) in self.dropguards

    def value_subst(self, name):
        return self.subst.get(name)
