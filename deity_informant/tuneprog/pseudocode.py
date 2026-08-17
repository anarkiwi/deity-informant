"""S7 -- the pseudocode itself: one expression, one statement, one structured node.

:class:`Printer` renders values and statements with the S6 names (struct views,
SID registers by voice, 16-bit pairs, compound assignment); :class:`Body` renders
the S5 node tree into indented lines. :mod:`.printer` assembles the document.
"""

from __future__ import annotations

from .idioms import CMP, fold
from .ir import (
    Assert,
    Bin,
    Call,
    Const,
    Let,
    Load,
    R16,
    REGVAR,
    SID_REG_HI,
    SID_REG_LO,
    Var,
    W16,
)
from .irwalk import addr_split, loads
from .live import needed, printable
from .recover import GLOBAL_REG, VOICE_REG
from .structure import Blk, Case, Cond, For, Jump, Loop

INDEX_MAX = 0x200  # how far a table's literal operand may sit from the region's base
NEG = {"==": "!=", "!=": "==", "<": ">=", "<=": ">"}
MIRROR = {"==": "==", "!=": "!=", "<": ">", "<=": ">="}
COMM = ("+", "|", "&", "^")
IND = "    "


def _hex(v):
    return str(v) if v < 10 else "$%X" % v


# ---- expressions -------------------------------------------------------------
class Printer:
    """Renders one program: expressions, statements, structured nodes."""

    def __init__(self, prog, names, pcs=True):
        self.prog = prog
        self.names = names
        self.pcs = pcs
        self.rgn = {r.id: r for r in prog.storage}
        self.live, self.params = needed(prog)
        self.alias = {}
        self.tmp = {}
        self.mem = {}
        self.hide = frozenset()
        self.fors = 0
        self.proc = ""
        self.defs = {}
        self.inline = {}
        self.lastsrc = None

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
        out = base.lower() if base in REGVAR.values() else "t%d" % (len(self.tmp) + 1)
        while out in self.tmp.values() and not base in REGVAR.values():
            out = "t%d" % (len(self.tmp) + 1)
        self.tmp[n] = out = out + (n.split("#")[1] if base in REGVAR.values() and "#" in n else "")
        return out

    def pair(self, lo, hi, a):
        """A 16-bit view reference: the pair's name, indexed like its low half."""
        name = self.names.u16.get((lo, hi))
        if name is None:
            return "(%s | %s << 8)" % (self.load16(lo, a), self.load16(hi, a))
        return self.cell(lo, *self.addr_of(a, self.rgn.get(lo)), name=name)

    def load16(self, rid, a):
        return self.cell(rid, *self.addr_of(a, self.rgn.get(rid)))

    def cell(self, rid, addr, idx=None, name=None):
        """A storage reference: ``voice[v].field``, ``NAME[i]`` or a scalar's name."""
        r = self.rgn.get(rid)
        if r is None:
            return "mem[%s]" % (self.expr(idx) if idx is not None else _hex(addr))
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
            hit = self.alias.get(idx.a.n)
            return hit[0] if hit is not None and hit[1] == 1 and idx.b.v == step else None
        if idx.op == "+" and type(idx.a) is Const and not idx.a.v % step:
            inner = self.ivar(idx.b, stride)
            return None if inner is None else "%s + %s" % (inner, _hex(idx.a.v // step))
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
            v, k = divmod(addr - SID_REG_LO, 7)
            return "%s[%d].%s" % (name, v, VOICE_REG[k])
        i = self.voiced(idx) if addr - SID_REG_LO < 21 else None
        if i is None:
            off = addr - SID_REG_LO
            e = _bare(self.expr(idx, False))
            return "%s.reg[%s]" % (name, "%s + %s" % (_hex(off), e) if off else e)
        v, k = divmod(addr - SID_REG_LO, 7)
        return "%s[%s].%s" % (name, "%s + %s" % (i, _hex(v)) if v else i, VOICE_REG[k])

    def voiced(self, idx):
        """The index as a voice number, when something proves it steps by seven."""
        hit = self.ivar(idx, 7)
        if hit is not None:
            return hit
        n = idx.n if type(idx) is Var else None
        return "%s/7" % self.expr(idx, False) if self.names.scale.get(n) == 7 else None

    # ---- expressions -------------------------------------------------------
    def expr(self, e, top=True):
        e = fold(e)
        hit = self.mem.get(e)
        if hit is not None:
            return hit
        t = type(e)
        if t is Const:
            return _hex(e.v)
        if t is Var:
            return self.expr(self.inline[e.n], top) if e.n in self.inline else self.var(e.n)
        if t is Load:
            return self.load(e)
        if t is R16:
            return self.pair(e.lo, e.hi, e.a)
        a, b = e.a, e.b
        if e.op in ("==", "!=") and type(b) is Const:
            hit = self.mem.get(Bin("-", a, b, 1))
            if hit is not None:  # x == k is the cell that holds x - k against zero
                s = "%s %s 0" % (hit, e.op)
                return s if top else "(%s)" % s
        if e.op in ("==", "!=") and type(b) is Const and b.v == 0 and _signbit(a):
            s = "%s %s 0" % (self.expr(a.a, False), ">=" if e.op == "==" else "<")
            return s if top else "(%s)" % s
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
        r = self.rgn.get(e.r)
        addr, idx = self.addr_of(e.a, r)
        if r is None and addr is not None:
            return "mem[%s]" % _hex(addr)
        return self.cell(e.r, addr, idx)

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
        lhs, e = self.pair(s.lo, s.hi, s.a), s.e
        sides = ((e.a, e.b), (e.b, e.a)) if type(e) is Bin and e.op in COMM else ((e.a, e.b),)
        for x, y in sides if type(e) is Bin else ():
            if (
                type(x) is R16
                and (x.lo, x.hi) == (s.lo, s.hi)
                and _bare(self.expr(x, False)) == lhs
            ):
                return "%s %s= %s" % (lhs, e.op, self.expr(y, False))
        return "%s = %s" % (lhs, self.expr(e))

    def call(self, s):
        p = self.prog.procs[s.proc]
        lhs = [self.var(r) for i, r in zip(p.rets, s.rets) if r in self.live[self.proc]]
        args = [
            "%s=%s" % (REGVAR[i].lower(), self.expr(a))
            for i, a in zip(p.params, s.args)
            if i in self.params[s.proc]
        ]
        call = "%s(%s)" % (self.names.procs.get(s.proc, s.proc), ", ".join(args))
        return "%s = %s" % (", ".join(lhs), call) if lhs else call

    def store(self, s):
        r = self.rgn.get(s.r)
        addr, idx = self.addr_of(s.a, r)
        if s.cls == "io":
            base, i = addr_split(s.a)
            if base is not None and SID_REG_LO <= base <= SID_REG_HI:
                lhs = self.regcell("sid", base, i)
            else:
                lhs = "io[%s]" % (_hex(base) if base is not None else self.expr(s.a, False))
        else:
            lhs = self.cell(s.r, addr, idx)
        out = self.compound(lhs, s, addr)
        self.forget(s.r)
        if s.cls != "io" and type(s.v) is not Const:
            self.mem[fold(s.v)] = lhs
        return out

    def compound(self, lhs, s, addr):
        """``x += k`` when the stored value is the cell's own value plus a constant."""
        v = fold(s.v)
        if type(v) is Bin and v.op in ("+", "-", "&", "|", "^", "<<", ">>"):
            a = self.defs.get(v.a.n, v.a) if type(v.a) is Var else v.a
            if type(a) is Load and a.r == s.r and self.addr_of(a.a, self.rgn.get(s.r))[0] == addr:
                if type(v.b) is Const and v.op in ("+", "-") and v.b.v > 0xF0:
                    return "%s %s= %s" % (lhs, "-" if v.op == "+" else "+", _hex(0x100 - v.b.v))
                return "%s %s= %s" % (lhs, v.op, self.expr(v.b, False))
        return "%s = %s" % (lhs, self.expr(s.v))

    def forget(self, rid):
        self.mem = {k: v for k, v in self.mem.items() if not _reads(k, rid)}


def _bare(s):
    return s[1:-1] if s.startswith("(") and s.endswith(")") else s


def _unoffset(idx, d):
    """An index with the constant :meth:`Printer.addr_of` folded into it removed."""
    if d and type(idx) is Bin and idx.op == "+" and type(idx.a) is Const and idx.a.v == d:
        return idx.b
    return idx


def _signbit(e):
    return type(e) is Bin and e.op == "&" and type(e.b) is Const and e.b.v == 0x80


def _reads(e, rid):
    """True when the value of ``e`` loads from region ``rid``."""
    return any(x.r == rid for x in loads(e))


# ---- structured body ---------------------------------------------------------
class Body(Printer):
    """Renders structured nodes into indented pseudocode lines."""

    def render(self, name, body):
        self.tmp, self.mem, self.alias, self.proc = {}, {}, {}, name
        self.lastsrc = None
        p = self.prog.procs[name]
        args = ", ".join(REGVAR[i].lower() for i in self.params[name])
        head = "%s(%s):" % (self.names.procs.get(name, name), args)
        out = ["%-40s # $%04X, %s calls" % (head, p.blocks[p.entry].src, num(_calls(p)))]
        return out + self.nodes(body, name, 1)

    def nodes(self, body, proc, depth):
        out = []
        for n in body:
            out.extend(self.node(n, proc, depth))
        return out or [IND * depth + "pass"]

    def node(self, n, proc, depth):
        pad = IND * depth
        t = type(n)
        if t is Blk:
            return self.blk(n, proc, pad)
        if t is Cond:
            return self.cond(n, proc, depth)
        if t is Case:
            return self.case(n, proc, depth)
        if t is For:
            return self.forloop(n, proc, depth)
        if t is Loop:
            return self.loop(n, proc, depth)
        if t is Jump:
            return [pad + (n.kind if n.kind != "goto" else "goto %s" % n.label)]
        if n.kind != "return":
            return [pad + "trap %r" % n.why]
        return [pad + ("return %s" % self.expr(n.e) if n.e is not None else "return")]

    def blk(self, n, proc, pad):
        live = self.live[proc]
        stmts = [s for s in n.stmts if printable(s, live) and not _hidden(s, self.hide)]
        if not stmts:
            return []
        self.mem = {}
        self.defs = {s.n: s.e for s in stmts if type(s) is Let}
        head = ["%s# $%04X" % (pad, n.src)] if self.pcs and n.src != self.lastsrc else []
        self.lastsrc = n.src
        return head + [pad + self.stmt(s) for s in stmts]

    def cond(self, n, proc, depth):
        pad = IND * depth
        c, flip = self.expr(n.c), False
        neg = self.negate(n.c)
        both = self.arms([n.then, n.els], proc, depth + 1)
        then, els = both[0], both[1]
        if then == [IND * (depth + 1) + "pass"] and els != [IND * (depth + 1) + "pass"]:
            then, els, flip = els, ["%spass" % (IND * (depth + 1))], True
        if flip:
            c = neg
        if len(then) == 1 and len(els) == 1 and els[-1].endswith("pass"):
            return ["%sif %s: %s" % (pad, c, then[0].strip())]
        if len(then) == 1 and len(els) == 1:
            return ["%sif %s: %s else: %s" % (pad, c, then[0].strip(), els[0].strip())]
        out = ["%sif %s:" % (pad, c)] + then
        return out if els[-1].endswith("pass") and len(els) == 1 else out + ["%selse:" % pad] + els

    def arms(self, bodies, proc, depth):
        """Render sibling arms: each starts from the state the test saw, none survives."""
        saved, out = dict(self.mem), []
        for b in bodies:
            self.mem = dict(saved)
            out.append(self.nodes(b, proc, depth))
        self.mem = {}
        return out

    def negate(self, c):
        if type(c) is Bin and c.op in NEG:
            return self.expr(Bin(NEG[c.op], c.a, c.b, c.w))
        return "not %s" % self.expr(c)

    def case(self, n, proc, depth):
        pad = IND * depth
        out = ["%sswitch %s:" % (pad, self.expr(n.e))]
        arms = self.arms([b for _v, b in n.cases], proc, depth + 2)
        for (v, _b), body in zip(n.cases, arms):
            out.append("%s%scase %s:" % (pad, IND, _hex(v)))
            out.extend(body)
        return out

    def forloop(self, n, proc, depth):
        pad = IND * depth
        vals = tuple(v // n.scale for v in n.values)
        rng = _range(vals)
        alias, hide = dict(self.alias), set(self.hide)
        var = _ivar(self.fors)
        self.alias[n.var] = (var, n.scale)
        self.hide |= n.hide
        self.fors += 1
        body = self.arms([_strip(n.body, n.label, self.hide)], proc, depth + 1)[0]
        self.alias, self.hide, self.fors = alias, hide, self.fors - 1
        return ["%sfor %s in %s:%s" % (pad, var, rng, _times(n.count))] + body

    def loop(self, n, proc, depth):
        pad = IND * depth
        spin = self.spin(n)
        if spin is not None:
            return ["%swhile %s: pass%s" % (pad, spin, _times(n.count))]
        body = self.arms([n.body], proc, depth + 1)[0]
        return ["%swhile True:%s" % (pad, _times(n.count))] + body

    def spin(self, n):
        """A body that only reads and tests is a busy-wait: ``while cond: pass``."""
        conds = [x for x in n.body if type(x) is Cond]
        if any(type(x) not in (Blk, Cond, Jump) for x in n.body) or len(conds) != 1:
            return None
        c = conds[0]
        blks = [x for x in n.body + c.then + c.els if type(x) is Blk]
        if any(type(s) is not Let for b in blks for s in b.stmts):
            return None
        if any(type(x) not in (Blk, Jump) for x in c.then + c.els):
            return None
        jumps = ([x for x in c.then if type(x) is Jump], [x for x in c.els if type(x) is Jump])
        arms = [k for k, b in zip("tf", jumps) if any(x.kind == "continue" for x in b)]
        if len(arms) != 1:
            return None
        self.inline = {s.n: s.e for b in blks for s in b.stmts}
        out = self.expr(c.c) if arms[0] == "t" else self.negate(c.c)
        self.inline = {}
        return out


def _hidden(s, hide):
    return type(s) is Let and s.n in hide


def _strip(body, label, hide):
    """Drop the induction test and the back edge a ``for`` header already states."""
    out = []
    for n in body:
        if type(n) is Cond and _jumps_only(n.then + n.els, hide):
            continue
        if type(n) is Jump and n.label == label:
            continue
        out.append(n)
    return out


def _jumps_only(nodes, hide):
    """True when a branch arm only jumps (its blocks are empty or hidden)."""
    for n in nodes:
        if type(n) is Jump:
            continue
        if type(n) is not Blk or any(not _hidden(s, hide) for s in n.stmts):
            return False
    return True


def _ivar(n):
    return "vwxyz"[min(n, 4)]


def _range(vals):
    if len(vals) > 3 and vals == tuple(
        range(
            vals[0], vals[-1] + (1 if vals[0] < vals[-1] else -1), 1 if vals[0] < vals[-1] else -1
        )
    ):
        return "%d..%d" % (vals[0], vals[-1])
    return ", ".join(str(v) for v in vals)


def _times(n):
    return "" if not n else "   # x%s" % num(n)


def num(n):
    """A count with thousands separators."""
    return "{:,}".format(n)


def _calls(p):
    return p.blocks[p.entry].count
