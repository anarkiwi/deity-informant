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
from .irwalk import addr_split, reads_region, unique_name, use_counts
from .halves import register
from .cellref import Cells, _bare, hexlit

CARRY = REGVAR[8]  # every version of it is the carry, which is the name it prints under
NEG = {"==": "!=", "!=": "==", "<": ">=", "<=": ">"}
MIRROR = {"==": "==", "!=": "!=", "<": ">", "<=": ">="}
COMM = ("+", "|", "&", "^")
IND = "    "


# ---- expressions -------------------------------------------------------------
class Printer(Cells):
    """Renders one program: expressions, statements, structured nodes."""

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
            return self.site(e.r, hit, "read")
        r = self.rgn.get(e.r)
        addr, idx = self.addr_of(e.a, r)
        if r is None and addr is not None:
            return "mem[%s]" % hexlit(addr)
        return self.site(e.r, self.cell(e.r, addr, idx, span=(e.lo, e.hi)), "read")

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
        return self.wordassign(s) + self.against(s)

    def against(self, s):
        """The mark a SID pair written against the header's convention carries."""
        conv = self.names.sidwrite
        if conv is None or register((s.lo, s.hi)) is None or s.hifirst == (conv[0] == "hi"):
            return ""
        return "  # %s then %s" % (("hi", "lo") if s.hifirst else ("lo", "hi"))

    def wordassign(self, s):
        """The assignment itself, ``op=`` when the pair is its own operand."""
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
            self.site(s.r, lhs, "written")
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


def _signbit(e):
    return type(e) is Bin and e.op == "&" and type(e.b) is Const and e.b.v == 0x80
