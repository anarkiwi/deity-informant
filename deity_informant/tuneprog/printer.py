"""S7 text form: the structured tuneprog as anatomy-style pseudocode (``tuneprog.md``).

Prints the S5 node tree with the S6 names: ``meta``, ``state`` (regions, roles and
struct views), ``const`` tables, ``inputs``, then one procedure each. Machine
plumbing (stack frames, register copies nothing reads) is dropped for print only.
"""

from __future__ import annotations

from .idioms import CMP, fold
from .ir import REGVAR, Assert, Bin, Call, Const, Let, Load, Return, Store, Var
from .recover import GLOBAL_REG, VOICE_REG
from .ssa import stmt_uses, term_uses, uses_of
from .structure import Blk, Case, Cond, For, Jump, Loop
from .word import R16, W16, uses16

SID_LO, SID_HI = 0xD400, 0xD418
NEG = {"==": "!=", "!=": "==", "<": ">=", "<=": ">"}
MIRROR = {"==": "==", "!=": "!=", "<": ">", "<=": ">="}
COMM = ("+", "|", "&", "^")
IND = "    "


def _hex(v):
    return str(v) if v < 10 else "$%X" % v


# ---- what is worth printing --------------------------------------------------
def _order(prog):
    """Procedure names, callees before callers."""
    out, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for b in prog.procs[n].blocks.values():
            for s in b.stmts:
                if type(s) is Call:
                    visit(s.proc)
        out.append(n)

    for n in prog.procs:
        visit(n)
    return out


def _impure(e):
    t = type(e)
    if t is Load:
        return e.cls in ("io", "chk") or _impure(e.a)
    return _impure(e.a) or _impure(e.b) if t is Bin else False


def _stmt_roots(prog, s, defs, params, out):
    """Record a statement's definition and, when it has an effect, what it reads."""
    t = type(s)
    if t is Let:
        defs.setdefault(s.n, []).append(s.e)
        if _impure(s.e):
            uses_of(s.e, out)
    elif t is W16:
        uses16(s.a, out)
        uses16(s.e, out)
    elif t is Assert or (t is Store and s.cls != "raw"):
        stmt_uses(s, out)
    elif t is Call:
        for i, a in zip(prog.procs[s.proc].params, s.args):
            if i in params.get(s.proc, ()):
                uses_of(a, out)


def _roots(prog, name, params, rets):
    """The values a procedure must compute: stores, tests, inputs, wanted returns."""
    p = prog.procs[name]
    defs, out = {}, set()
    for b in p.blocks.values():
        for s in b.stmts:
            _stmt_roots(prog, s, defs, params, out)
        if type(b.term) is Return:
            for i, v in zip(p.rets, b.term.vals):
                if i in rets[name]:
                    uses_of(v, out)
        else:
            term_uses(b.term, out)
    work = list(out)
    while work:
        n = work.pop()
        for e in defs.pop(n, ()):
            add = uses_of(e, set())
            out |= add
            work += list(add)
    return out


def needed(prog, rounds=3):
    """``({proc: live names}, {proc: params it reads})`` -- the plumbing left out.

    A procedure's return values are live only where a caller reads them, which
    takes one pass per call-graph level to settle.
    """
    rets = {n: set() for n in prog.procs}
    used, params = {}, {}
    for _ in range(rounds):
        used, params = {}, {}
        for name in _order(prog):
            used[name] = _roots(prog, name, params, rets)
            params[name] = tuple(i for i in prog.procs[name].params if REGVAR[i] in used[name])
        want = {n: set() for n in prog.procs}
        for name, p in prog.procs.items():
            for s in [x for b in p.blocks.values() for x in b.stmts if type(x) is Call]:
                want[s.proc] |= {
                    i for i, r in zip(prog.procs[s.proc].rets, s.rets) if r in used[name]
                }
        if want == rets:
            break
        rets = want
    return used, params


def _keep(s, live):
    t = type(s)
    if t is Let:
        return s.n in live or _impure(s.e)
    return t is not Store or s.cls != "raw"


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

        Indices count from the region's origin, so a 1-based table read at
        ``base-1,Y`` prints ``T[y]`` and its look-ahead sibling ``T[y + 1]``.
        """
        if type(e) is Const:
            return e.v, None
        if type(e) is Bin and e.op == "+" and r is not None:
            for k, i in ((e.a, e.b), (e.b, e.a)):
                if type(k) is Const and r.zero <= k.v <= r.base + r.size:
                    d = k.v - r.zero
                    return k.v, Bin("+", Const(d), i, 2) if d else i
        return None, e

    def regcell(self, name, addr, idx=None):
        """``sid[v].reg`` / ``ghost[r]``: a register file cell, by voice or by index.

        A voice-scaled index (the loop variable of a per-voice loop, or an index
        the region's stride says is one) names the register; an index that walks
        the registers themselves (a flush loop) leaves the register to the index.
        """
        if idx is None and addr in GLOBAL_REG:
            return "%s.%s" % (name, GLOBAL_REG[addr])
        v, k = divmod(addr - SID_LO, 7)
        if idx is None:
            return "%s[%d].%s" % (name, v, VOICE_REG[k])
        i = self.ivar(idx, 7)
        if i is None:
            flat = self.ivar(idx, 1)
            if flat is not None:
                off = addr - SID_LO
                return "%s[%s]" % (name, "%s + %s" % (flat, _hex(off)) if off else flat)
            i = "%s/7" % self.expr(idx, False)
        return "%s[%s].%s" % (name, "%s + %s" % (i, _hex(v)) if v else i, VOICE_REG[k])

    def sid(self, addr, idx=None):
        return self.regcell("sid", addr, idx)

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
        if e.op == "&" and type(b) is Const and b.v == 0x80:
            return self.expr(a, False)
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
            base, i = _split(s.a)
            if base is not None and SID_LO <= base <= SID_HI:
                lhs = self.sid(base, i)
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


def _split(e):
    """``(base, index)`` of an address: a constant, or a constant plus an index."""
    if type(e) is Const:
        return e.v, None
    if type(e) is Bin and e.op == "+":
        for k, i in ((e.a, e.b), (e.b, e.a)):
            if type(k) is Const:
                return k.v, i
    return None, e


def _signbit(e):
    return type(e) is Bin and e.op == "&" and type(e.b) is Const and e.b.v == 0x80


def _reads(e, rid):
    t = type(e)
    if t is Load:
        return e.r == rid or _reads(e.a, rid)
    return _reads(e.a, rid) or _reads(e.b, rid) if t is Bin else False


# ---- structured body ---------------------------------------------------------
class Body(Printer):
    """Renders structured nodes into indented pseudocode lines."""

    def render(self, name, body):
        self.tmp, self.mem, self.alias, self.proc = {}, {}, {}, name
        self.lastsrc = None
        p = self.prog.procs[name]
        args = ", ".join(REGVAR[i].lower() for i in self.params[name])
        head = "%s(%s):" % (self.names.procs.get(name, name), args)
        out = ["%-40s # $%04X, %s calls" % (head, p.blocks[p.entry].src, _num(_calls(p)))]
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
        return [pad + ("return" if n.kind == "return" else "trap %r" % n.why)]

    def blk(self, n, proc, pad):
        live = self.live[proc]
        stmts = [s for s in n.stmts if _keep(s, live) and not _hidden(s, self.hide)]
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
        then, els = self.nodes(n.then, proc, depth + 1), self.nodes(n.els, proc, depth + 1)
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

    def negate(self, c):
        if type(c) is Bin and c.op in NEG:
            return self.expr(Bin(NEG[c.op], c.a, c.b, c.w))
        return "not %s" % self.expr(c)

    def case(self, n, proc, depth):
        pad = IND * depth
        out = ["%sswitch %s:" % (pad, self.expr(n.e))]
        for v, b in n.cases:  # pylint: disable=redefined-argument-from-local
            out.append("%s%scase %s:" % (pad, IND, _hex(v)))
            out.extend(self.nodes(b, proc, depth + 2))
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
        body = self.nodes(_strip(n.body, n.label, self.hide), proc, depth + 1)
        self.alias, self.hide, self.fors = alias, hide, self.fors - 1
        return ["%sfor %s in %s:%s" % (pad, var, rng, _times(n.count))] + body

    def loop(self, n, proc, depth):
        pad = IND * depth
        spin = self.spin(n)
        if spin is not None:
            return ["%swhile %s: pass%s" % (pad, spin, _times(n.count))]
        return ["%swhile True:%s" % (pad, _times(n.count))] + self.nodes(n.body, proc, depth + 1)

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
    return "" if not n else "   # x%s" % _num(n)


def _num(n):
    return "{:,}".format(n)


def _calls(p):
    return p.blocks[p.entry].count


# ---- the document ------------------------------------------------------------
FRAME = 19656
PHASES = {1: "init", 2: "tick", 3: "init+tick"}


def _meta(prog, names, cert):
    """The ``meta`` block: entry, cadence, subtunes, model, certificate."""
    m = prog.meta
    e = m.get("entry", {})
    rate = FRAME / e.get("cycles_per_tick", FRAME)
    out = [
        "entry     %s $%04X every %d cycles (%.1f calls/frame, %s)"
        % (e.get("kind"), e.get("addr", 0), e.get("cycles_per_tick", 0), rate, e.get("source")),
        "subtunes  %d, playing song %d; sid model %s"
        % (m.get("songs", 1), (m.get("song") or 0) + 1, m.get("sid_model") or "as traced"),
        "program   %d procedures, %d blocks, %d statements, %d regions"
        % (
            len(prog.procs),
            sum(len(p.blocks) for p in prog.procs.values()),
            sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
            len([r for r in prog.storage if r.id >= 0]),
        ),
    ]
    if names.phase is not None:
        out.append("phase     %s selects the rate" % names.region.get(names.phase[0], "?"))
    if cert:
        s = cert["subtunes"][0]
        out.append(
            "certified %s calls, %s divergences, period %s, first repeat at call %s (%s), stage %s"
            % (
                _num(s["ticks"]),
                s["divergences"],
                _num(s["period"]) if s["period"] else "none",
                _num(s["first_repeat"]) if s["first_repeat"] is not None else "-",
                "complete" if s["complete"] else "horizon",
                cert.get("stage"),
            )
        )
    return out


def _row(r, names, extra=""):
    return "%-16s $%04X %-14s %-10s %s" % (
        names.region.get(r.id, "r%d" % r.id),
        r.base,
        "%d bytes%s" % (r.size, "" if r.stride < 2 else " stride %d" % r.stride),
        names.role.get(r.id, ""),
        extra or names.notes.get(r.id, ""),
    )


def _state(prog, names):
    """The ``state`` block: struct views first, then the scalars."""
    out = []
    for g, d in sorted(names.groups.items()):
        fields = {}
        for rid in sorted(set(d["members"]), key=lambda i: _base(prog, i)):
            fields.setdefault(names.view[rid][1], []).append(rid)
        out.append("%s[%d]  stride %d, %d fields" % (g, d["n"], d["stride"], len(fields)))
        for fname, rids in fields.items():
            out.append(
                "  .%-14s %-24s %-10s %s"
                % (
                    fname,
                    " ".join("$%04X" % _base(prog, i) for i in rids),
                    names.role.get(rids[0], ""),
                    names.notes.get(rids[0], ""),
                )
            )
    half = {r for p in names.u16 for r in p}
    for (lo, hi), name in sorted(names.u16.items(), key=lambda kv: _base(prog, kv[0][0])):
        if lo in names.view:
            continue
        out.append(
            "%-16s $%04X %-14s %-10s %s"
            % (
                name,
                _base(prog, lo),
                "u16",
                names.role.get(lo, ""),
                "lo|hi $%04X" % _base(prog, hi),
            )
        )
    for r in sorted(prog.storage, key=lambda x: x.base):
        if r.id < 0 or r.id in names.view or r.kind not in ("state", "init_constant"):
            continue
        if r.id not in half:
            out.append(_row(r, names, "init-only" if r.kind == "init_constant" else ""))
    return out


def _const(prog, names):
    return [
        _row(r, names)
        for r in sorted(prog.storage, key=lambda x: x.base)
        if r.id >= 0 and r.kind in ("const", "image") and r.id not in names.view
    ]


def _inputs(prog):
    return [
        "$%04X %-14s at $%04X, %d reads (%s)" % (addr, kind, pc, count, PHASES.get(phase, phase))
        for pc, addr, kind, count, phase in prog.inputs
    ]


def _rgn(prog, rid):
    return next(r for r in prog.storage if r.id == rid)


def _base(prog, rid):
    return _rgn(prog, rid).base


def render(prog, structured, names, cert=None, pcs=True):
    """``tuneprog.md``: meta, state, const, inputs, then every procedure."""
    body = Body(prog, names, pcs)
    out = ["# tuneprog: %s" % prog.meta.get("name", "?"), ""]
    for title, lines in (
        ("meta", _meta(prog, names, cert)),
        ("state", _state(prog, names)),
        ("const", _const(prog, names)),
        ("inputs", _inputs(prog)),
    ):
        out += ["## %s" % title, "", "```"] + (lines or ["(none)"]) + ["```", ""]
    out += ["## program", ""]
    for name in _procs_order(prog):
        out += ["```"] + body.render(name, structured[name]) + ["```", ""]
    return "\n".join(out) + "\n"


def _forwarder(p):
    """A procedure that is nothing but a call to another one."""
    stmts = [s for b in p.blocks.values() for s in b.stmts]
    return len(p.blocks) == 1 and len(stmts) == 1 and type(stmts[0]) is Call


def _procs_order(prog):
    """The tick first, then what it calls, then init and the rest; forwarders elided."""
    tick, init = prog.meta.get("tick_proc"), prog.meta.get("init_proc")
    hot = [n for n in reversed(_order(prog)) if n != init]
    order = [n for n in (tick,) if n in hot] + [n for n in hot if n != tick]
    order += [n for n in prog.procs if n not in order]
    return [n for n in order if not _forwarder(prog.procs[n])]
