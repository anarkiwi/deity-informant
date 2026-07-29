"""Reference evaluator for frameprog and Gate FP (docs/frameprog.md 1.4).

Statement trees compile once to a flat op array over one local environment and
the state image (spec 2: no code image, patched cells are state); SID writes
buffer per frame and flush through the single projection ``framelog.canonical``.
"""

from __future__ import annotations

from . import expr as E
from . import framelog
from . import frameproc
from . import frameprog
from . import sidprog
from . import structured as C

_GUARD = 8_000_000
_REG_INIT = {frameproc._reg_local(i): (0xFF if i == 3 else 0) for i in range(16)}


class FrameFault(RuntimeError):
    """The frame program left its guarded envelope (fault, never improvise)."""


# ---- expressions: closures over (locals, state image, volatile reader) ----------
def _width(n):
    return 1 if n[0] == "loc" else E.width(n)


def _load(n, slot):
    addr, sz = n[1], n[2]
    if addr[0] == "const" and sidprog._ld_safe(addr):
        cells = [(addr[1] + j) & 0xFFFF for j in range(sz)]
        if sz == 1:
            a = cells[0]
            return lambda r, m, rd: m[a]
        return lambda r, m, rd: sum(m[c] << (8 * j) for j, c in enumerate(cells))
    fa = _expr(addr, slot)
    if sz == 1:
        return lambda r, m, rd: rd(fa(r, m, rd))
    return lambda r, m, rd: sum(rd((fa(r, m, rd) + j) & 0xFFFF) << (8 * j) for j in range(sz))


def _expr(n, slot):
    """Closure ``(r, m, rd) -> value`` for one frameprog expression node."""
    k = n[0]
    if k == "const":
        v = n[1]
        return lambda r, m, rd: v
    if k == "loc":
        i = slot(n[1])
        return lambda r, m, rd: r[i]
    if k == "mem":
        return _load(n, slot)
    if k != "op":
        raise FrameFault("unexpected expression node %r" % (k,))
    mn, sz = n[1], n[3]
    fs = tuple(_expr(c, slot) for c in n[2])
    szs = [_width(c) for c in n[2]]
    return lambda r, m, rd: E._apply(mn, [f(r, m, rd) for f in fs], szs, sz)


# ---- compilation: statement trees to one flat op array -------------------------
def _pc(lbl):
    return int(lbl[1:], 16)


class _Code:
    """Flat op array with label/entry fixups; one environment for all locals.

    ``call``/``goto`` cross procedures as machine transfers, so locals are
    program-wide (registers are shared, temporaries never outlive a block)."""

    def __init__(self, prog):
        self.ops = []
        self.idx = {}
        self.pcmap = {}
        self.entries = {}
        self.fix = []
        self.barefix = []
        self.conts = {}
        self.params = {e: [self.slot(p) for p in ps] for e, ps, _r, _s in prog.procs}
        for entry, _params, _rets, stmts in prog.procs:
            self.entries.setdefault(entry, len(self.ops))
            self.mark(entry)
            self.seq(stmts, None)
            self.emit(("fault", "sub_%04X fell through" % entry))
        for i, field, pc in self.fix:
            self.patch(i, field, self._link(pc))
        for i, pc in self.barefix:
            self.ops[i][1][pc] = self._link(pc)
        self.rmap = {**self.conts, **self.pcmap}

    def mark(self, pc, i=None):
        """Bind a serialized pc to an op index (sidprog's pcmap, first wins)."""
        self.pcmap.setdefault(pc, len(self.ops) if i is None else i)

    def cont(self, i, ret):
        """Bind a JSR continuation ``ret + 1`` to the op after the call (contmap)."""
        self.conts.setdefault((ret + 1) & 0xFFFF, i + 1)

    def _link(self, pc):
        tgt = self.pcmap.get(pc)
        if tgt is None:
            raise FrameFault("target $%04X outside the program" % pc)
        return tgt

    def slot(self, name):
        i = self.idx.get(name)
        if i is None:
            i = self.idx[name] = len(self.idx)
        return i

    def emit(self, op):
        self.ops.append(op)
        return len(self.ops) - 1

    def patch(self, i, field, tgt=None):
        op = list(self.ops[i])
        op[field] = len(self.ops) if tgt is None else tgt
        self.ops[i] = tuple(op)

    def ref(self, pc, field=1):
        """Emitted-op field patched to the op index of ``pc`` after linking."""
        self.fix.append((len(self.ops) - 1, field, pc))

    def expr(self, n):
        return _expr(n, self.slot)

    # -- statements ---------------------------------------------------------------
    def seq(self, stmts, ctx):
        i = 0
        while i < len(stmts):
            s = stmts[i]
            nxt = stmts[i + 1] if i + 1 < len(stmts) else None
            if nxt is not None and nxt[0] in ("swg", "swc") and s[0] in ("dgoto", "igoto", "dcall"):
                self.dyn(s)
                if nxt[0] == "swg":
                    self.swg(nxt, ctx)
                else:
                    self.swc(nxt, s[2])
                i += 2
                continue
            self.stmt(s, ctx)
            i += 1

    def stmt(self, s, ctx):
        k = s[0]
        fn = getattr(self, "_s_" + k, None)
        if fn is None:
            raise FrameFault("unimplemented statement %r" % (k,))
        fn(s, ctx)

    def _s_label(self, s, _ctx):
        self.mark(s[1])

    def _s_asg(self, s, _ctx):
        self.emit(("asg", self.slot(s[1]), self.expr(s[2])))

    def _s_st(self, s, _ctx):
        self.emit(("st", self.expr(s[1]), self.expr(s[2])))

    def _s_ret(self, _s, _ctx):
        self.emit(("ret",))

    def _s_unobs(self, s, _ctx):
        self.emit(("fault", "unobserved $%04X reached" % s[1]))

    def _s_goto(self, s, _ctx):
        self.emit(("jmp", None))
        self.ref(s[1])

    def _s_cont(self, _s, ctx):
        ctx[0].append(self.emit(("jmp", None)))

    def _s_brk(self, _s, ctx):
        ctx[1].append(self.emit(("jmp", None)))

    def _s_if(self, s, ctx):
        _k, word, cond, then, els = s
        j = self.emit(("br", self.expr(cond), word == "ifnot", None))
        self.seq(then, ctx)
        if els:
            e = self.emit(("jmp", None))
            self.patch(j, 3)
            self.seq(els, ctx)
            self.patch(e, 1)
        else:
            self.patch(j, 3)

    def _s_loop(self, s, _ctx):
        head = len(self.ops)
        conts, brks = [], []
        self.seq(s[1], (conts, brks))
        self.emit(("jmp", head))
        for i in conts:
            self.patch(i, 1, head)
        for i in brks:
            self.patch(i, 1)

    def _s_for(self, s, _ctx):
        _k, name, init, last, body = s
        i = self.slot(name)
        self.emit(("asg", i, lambda r, m, rd, v=init: v))
        head = len(self.ops)
        conts, brks = [], []
        self.seq(body, (conts, brks))
        test = self.emit(("fortest", i, last, None))
        self.emit(("forstep", i, 1 if last >= init else -1))
        self.emit(("jmp", head))
        for j in conts:
            self.patch(j, 1, test)
        for j in brks:
            self.patch(j, 1)
        self.patch(test, 3)

    def _arms(self, cases, ctx, follow):
        """Case bodies laid out after the dispatch op; ``follow`` = fall through."""
        table, ends = {}, []
        for lbl, body in cases:
            table[_pc(lbl)] = len(self.ops)
            self.seq(body, ctx)
            ends.append(self.emit(("jmp", None) if follow else ("fault", "case %s ran on" % lbl)))
        if follow:
            for i in ends:
                self.patch(i, 1)
        return table

    def _s_opsw(self, s, ctx):
        d = self.emit(("sw", s[1], None))
        self.mark(s[1], d)
        self.patch(d, 2, self._arms(s[2], ctx, True))

    def _s_call(self, s, _ctx):
        self.cont(self.emit(("call", None, s[2])), s[2])
        self.ref(s[1])

    def _s_pcall(self, s, _ctx):
        args = tuple(self.expr(a) for a in s[2])
        i = self.emit(("pcall", None, tuple(self.params[s[1]]), args, 0))
        self.patch(i, 4, self.synth(i))
        self.ref(s[1])

    def synth(self, i):
        """Stand-in return address for a ``pcall`` (the surface drops ``ret $R``)."""
        r = 0xFFFE
        while (r + 1) & 0xFFFF in self.conts:
            r -= 1
        self.cont(i, r)
        return r

    def _s_callb(self, s, _ctx):
        c = self.emit(("call", None, s[2]))
        self.cont(c, s[2])
        skip = self.emit(("jmp", None))
        self.mark(s[1])
        self.patch(c, 1)
        self.seq(s[3], None)
        self.emit(("ret",))
        self.patch(skip, 1)

    def _s_dbr(self, s, _ctx):
        j = self.emit(("br", self.expr(s[2]), s[1] == "if", None))
        e = self.emit(("jmp", None))
        self.patch(j, 3)
        self.emit(("dyn", self.expr(s[3])))
        self.emit(("gdyn",))
        self.patch(e, 1)

    def _s_dgoto(self, s, _ctx):
        self.dyn(s)
        self.emit(("gdyn",))

    _s_igoto = _s_dgoto

    def _s_dcall(self, s, _ctx):
        self.dyn(s)
        self.cont(self.emit(("calld", s[2])), s[2])

    def dyn(self, s):
        """Land the pending dynamic target of a dgoto/igoto/dcall in ``dyn``."""
        if s[0] == "igoto":
            if s[2] is not None:
                self.emit(("dyn", self.expr(s[2])))
            self.emit(("vec", None if s[2] is not None else s[1]))
        else:
            self.emit(("dyn", self.expr(s[1])))

    def _s_swg(self, _s, _ctx):
        raise FrameFault("switch goto without a computed-jump statement")

    def _s_swc(self, _s, _ctx):
        raise FrameFault("switch call without a computed-call statement")

    def swg(self, s, ctx):
        d = self.emit(("swd", None))
        self.patch(d, 1, self._arms(s[1], ctx, False))

    def swc(self, s, ret):
        d = self.emit(("cd", None, ret))
        self.cont(d, ret)
        skip = self.emit(("jmp", None))
        table = {}
        for lbl, body in s[2]:
            table[_pc(lbl)] = len(self.ops)
            self.mark(_pc(lbl))
            self.seq(body, None)
            self.emit(("ret",))
        self.patch(skip, 1)
        self.patch(d, 1, table)
        self.barefix.extend((d, _pc(lbl)) for lbl in s[1])


# ---- the machine ----------------------------------------------------------------
class Evaluator:
    """Executes a ``FrameProgram`` frame by frame against a pinned ``iota``."""

    def __init__(self, prog, trace, state0=None):
        self.code = _Code(prog)
        self.m = bytearray(prog.mem0 if state0 is None else state0)
        self.sp = self.code.slot("sp")
        self.r = [0] * len(self.code.idx)
        for name, i in self.code.idx.items():
            self.r[i] = _REG_INIT.get(name, 0)
        self.acc = self.code.idx.get("a")
        self.trace = trace
        self.inputs = frozenset(prog.inputs)
        self.frame = 0
        self.k = {}
        if prog.play not in self.code.entries:
            raise FrameFault("play $%04X is not a serialized procedure" % prog.play)
        self.play = self.code.entries[prog.play]

    def _rd(self, a):
        """Volatile-aware state read: declared inputs resolve to iota(f, name, k)."""
        name = frameprog._INPUTS.get(a)
        if name is None:
            return 0 if a == 0xD019 else self.m[a]
        if name not in self.inputs:
            raise FrameFault("undeclared volatile input %s" % name)
        k = self.k.get(name, 0)
        self.k[name] = k + 1
        v = self.trace.get((self.frame, name, k))
        if v is None:
            raise FrameFault("iota(%d, %s, %d) past the pinned trace" % (self.frame, name, k))
        return v

    def _resolve(self, table, pc, what):
        tgt = table.get(pc)
        if tgt is None:
            raise FrameFault("%s target $%04X outside the observed set" % (what, pc))
        return tgt

    def run_frame(self):
        """One play invocation; the frame's buffered ``(reg, val)`` SID writes.

        ``sp`` and the pushed return bytes are machine-faithful: call/ret move
        the shared stack register the program itself reads back (TSX/TXS)."""
        ops, r, m, rd, s = self.code.ops, self.r, self.m, self._rd, self.sp
        rmap = self.code.rmap

        def push(ret):
            p = r[s] & 0xFF
            m[0x100 + p] = (ret >> 8) & 0xFF
            p = (p - 1) & 0xFF
            m[0x100 + p] = ret & 0xFF
            r[s] = (p - 1) & 0xFF

        self.k.clear()
        if self.acc is not None:
            r[self.acc] = 0
        start = r[s] & 0xFF
        push(0x0001)
        buf, stack, dyn, pc, n = [], [], 0, self.play, 0
        while True:
            op = ops[pc]
            k = op[0]
            pc += 1
            if k == "asg":
                r[op[1]] = op[2](r, m, rd)
            elif k == "st":
                a = op[1](r, m, rd)
                m[a] = op[2](r, m, rd)
                if C.SID_LO <= a <= C.SID_HI:
                    buf.append((a - C.SID_LO, m[a]))
            elif k == "br":
                if bool(op[1](r, m, rd)) is op[2]:
                    pc = op[3]
            elif k == "jmp":
                pc = op[1]
            elif k == "fortest":
                if r[op[1]] == op[2]:
                    pc = op[3]
            elif k == "forstep":
                r[op[1]] = (r[op[1]] + op[2]) & 0xFF
            elif k == "ret":
                p = r[s] & 0xFF
                while stack and stack[-1][1] < p:
                    stack.pop()
                r[s] = q = (p + 2) & 0xFF
                if stack and stack[-1][1] == p:
                    pc = stack.pop()[0]
                elif q >= start:
                    break
                else:  # the program moved sp (TXS/PHA): return through the real stack
                    w = m[0x100 + ((p + 1) & 0xFF)] | (m[0x100 + q] << 8)
                    pc = self._resolve(self.code.rmap, (w + 1) & 0xFFFF, "ret")
            elif k == "sw":
                pc = self._resolve(op[2], m[op[1]], "switch $%04X" % op[1])
            elif k == "call":
                push(op[2])
                stack.append((pc, r[s]))
                pc = op[1]
            elif k == "pcall":
                vals = [f(r, m, rd) for f in op[3]]
                for i, v in zip(op[2], vals):
                    r[i] = v
                push(op[4])
                stack.append((pc, r[s]))
                pc = op[1]
            elif k == "dyn":
                dyn = op[1](r, m, rd) & 0xFFFF
            elif k == "vec":
                p = dyn if op[1] is None else op[1]
                dyn = m[p] | (m[(p & 0xFF00) | ((p + 1) & 0xFF)] << 8)
            elif k == "gdyn":
                pc = self._resolve(rmap, dyn, "goto")
            elif k == "swd":
                pc = op[1].get(dyn) or self._resolve(rmap, dyn, "switch goto")
            elif k == "cd":
                push(op[2])
                stack.append((pc, r[s]))
                pc = op[1].get(dyn) or self._resolve(rmap, dyn, "switch call")
            elif k == "calld":
                push(op[1])
                stack.append((pc, r[s]))
                pc = self._resolve(rmap, dyn, "call")
            else:
                raise FrameFault(op[1])
            n += 1
            if n > _GUARD:
                raise FrameFault("runaway frame program")
        return buf

    def frames(self, nframes):
        """Buffered per-frame write lists for ``nframes`` play invocations."""
        out = []
        for f in range(nframes):
            self.frame = f
            out.append(self.run_frame())
        return out


def eval_fp(prog, trace, nframes, state0=None):
    """Canonical per-frame records of ``prog`` under the pinned trace (spec 1.4).

    Output semantics: buffer the frame's SID writes, flush one canonical
    record per frame through the single projection."""
    return framelog.canonical(Evaluator(prog, trace, state0).frames(nframes))


def gate_fp(model, nframes, prog=None):
    """Gate FP verdict: None if the frame program reproduces the walker projection.

    Both sides consume one ``iota`` run, so the law is well defined (spec 1.3)."""
    trace, walker = frameprog.iota(model, nframes)
    if prog is None:
        prog = frameprog.program(model)
    return framelog.diff(eval_fp(prog, trace, nframes), framelog.canonical(walker))
