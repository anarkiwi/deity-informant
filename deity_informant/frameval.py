"""Reference evaluator for frameprog and Gate FP (docs/frameprog.md 1.4).

Statement trees compile once to a flat op array over one local environment and
the state image (spec 2: no code image, patched cells are state); SID writes
buffer per frame and flush through the single projection ``framelog.canonical``.
"""

from __future__ import annotations

from . import expr as E
from . import framefuse
from . import framelog
from . import frameproc
from . import frameprog
from . import grammar as G
from . import sidprog
from . import structured as C

_GUARD = 8_000_000
_REG_INIT = {frameproc._reg_local(i): (0xFF if i == 3 else 0) for i in range(16)}


class FrameFault(RuntimeError):
    """The frame program left its guarded envelope (fault, never improvise)."""


# ---- expressions: closures over (locals, state image, volatile reader) ----------
_width = frameproc.loc_width  # loc leaves carry their own width; every other node is E.width


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


_pure = frameproc.pure  # the ONE purity predicate (spec 1.4); rung (f) uses it too


def _byte_addr(a, j):
    """Address of byte ``j`` of a load at ``a`` (a word load reads two cells)."""
    if j == 0:
        return a
    if a[0] == "const":
        return ("const", (a[1] + j) & 0xFFFF, 2)
    return ("op", "INT_ADD", (a, ("const", j, 2)), 2)


def _addrs(n, pin=None):
    """Addresses of every byte a memory load inside ``n`` reads at a nameable address.

    Nameable is a pure address, or one ``pin`` carries: rung (f) proved that deref
    lies in a single declared block, so the proof supplies the base and only the row
    evaluates (spec 4.6). The pointer's own cells are that address, not the origin."""
    out = []
    if n[0] == "mem":
        a = pin.get(n[1]) if pin else None
        if a is not None:
            return [_byte_addr(a, j) for j in range(n[2])]
        if _pure(n[1]):
            out += [_byte_addr(n[1], j) for j in range(n[2])]
        out += _addrs(n[1], pin)
    elif n[0] == "op":
        for c in n[2]:
            out += _addrs(c, pin)
    return out


def _taint(n):
    """Locals whose VALUE ``n`` reads; one used only inside a mem address is not one."""
    if n[0] == "loc":
        return [n[1]]
    if n[0] == "op":
        out = []
        for c in n[2]:
            out += _taint(c)
        return out
    return []


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

    def __init__(self, prog, watch=(), pin=None):
        self.mem0 = prog.mem0
        self.pin = dict(getattr(prog, "pinned", ()) if pin is None else pin)
        self.watch = {id(s): i for i, s in enumerate(watch)}
        self.tagged = set()
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
        if len(self.tagged) != len(self.watch):
            raise FrameFault("watched statement outside the program")

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
            if s[0] == "igoto" and s[2] is None and nxt is not None:
                p = s[1]  # static vector: the image target's body follows inline
                self.mark(self.mem0[p] | (self.mem0[(p & 0xFF00) | ((p + 1) & 0xFF)] << 8))
            i += 1

    def stmt(self, s, ctx):
        k = s[0]
        fn = getattr(self, "_s_" + k, None)
        if fn is None:
            raise FrameFault("unimplemented statement %r" % (k,))
        fn(s, ctx)

    def _s_label(self, s, _ctx):
        self.mark(s[1])

    def tag(self, s):
        """Index of ``s`` in the caller's watch list, or None where it named none.

        Identity, not equality: two identical statements in different procedures
        are different sites, and a watch the program never compiles is a fault."""
        i = self.watch.get(id(s))
        if i is not None:
            self.tagged.add(i)
        return i

    def _s_asg(self, s, _ctx):
        self.emit(("asg", self.slot(s[1]), self.expr(s[2]), self.deriv(s[2]), self.tag(s)))

    def _s_st(self, s, _ctx):
        sz = G.store_width(s[2])
        if sz == 1:
            self.emit(("st", self.expr(s[1]), self.expr(s[2]), self.deriv(s[2]), self.tag(s)))
            return
        halves = framefuse.unpack(s[2]) or (s[2],) * sz
        derv = tuple(self.deriv(h) for h in halves)
        self.emit(("stw", self.expr(s[1]), self.expr(s[2]), derv, self.tag(s), sz))

    def deriv(self, val):
        """``(address closure, taint slots)``: where a stored byte may be copied from.

        The cells the value read, each evaluable without re-reading memory, plus the
        locals whose value it reads — a byte staged in a register arrives through the
        local, so the map would drop it where the tree shows no load at all."""
        fs = tuple(self.expr(a) for a in _addrs(val, self.pin))
        f = (lambda r, m, rd: tuple(g(r, m, rd) & 0xFFFF for g in fs)) if fs else None
        return f, tuple(self.slot(n) for n in _taint(val))

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
        self.emit(("asg", i, lambda r, m, rd, v=init: v, (None, ()), None))
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
        derv = tuple(self.deriv(a) for a in s[2])
        i = self.emit(("pcall", None, tuple(self.params[s[1]]), args, 0, derv))
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
def _cells(d, r, m, rd, ploc):
    """Cells a byte may be copied from: the value's read cells, then its locals' origins."""
    f, ts = d
    out = [] if f is None else list(f(r, m, rd))
    return out + [ploc[t] for t in ts if t in ploc]


def _bind(dst, key, cell):
    """Bind ``key`` to the cell its byte came from, or drop it where none does."""
    if cell is None:
        dst.pop(key, None)
    else:
        dst[key] = cell


def _copy(d, r, m, rd, prov, ploc):
    """The ONE cell a copied byte came from, or None where the byte is computed."""
    cs = _cells(d, r, m, rd, ploc)
    return prov.get(cs[0], cs[0]) if len(cs) == 1 else None


def _derived(d, r, m, rd, prov, ploc):
    """Cells a stored byte derives from: each read cell, its origin, then its locals'."""
    out = []
    for c in _cells(d, r, m, rd, ploc):
        o = prov.get(c)
        if o is not None and o not in out:
            out.append(o)
        if c not in out:
            out.append(c)
    return tuple(out)


class Evaluator:
    """Executes a ``FrameProgram`` frame by frame against a pinned ``iota``."""

    def __init__(self, prog, trace, state0=None, sources=False, watch=(), pin=None):
        self.code = _Code(prog, watch, pin)
        self.srcs = [] if sources else None
        self.watched = [] if sources else None
        self.prov = dict(getattr(prog, "prov0", ())) if sources else None
        self.ploc = {} if sources else None
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
        """Volatile-aware state read: declared inputs resolve to iota(f, name, k).

        The one volatile model is the walker's: its cycle-derived reads are the
        pinned inputs, its constant-0 sources (``structured._VOL0``) read 0 here
        exactly as they do there, whatever byte the state image holds."""
        name = frameprog._INPUTS.get(a)
        if name is None:
            return 0 if a in C._VOL0 else self.m[a]
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
        rmap, prov, ploc = self.code.rmap, self.prov, self.ploc

        def push(ret):
            p = r[s] & 0xFF
            m[0x100 + p] = (ret >> 8) & 0xFF
            q = (p - 1) & 0xFF
            m[0x100 + q] = ret & 0xFF
            r[s] = (q - 1) & 0xFF
            if prov is not None:
                prov.pop(0x100 + p, None)
                prov.pop(0x100 + q, None)

        self.k.clear()
        if self.acc is not None:
            r[self.acc] = 0
            if ploc is not None:
                ploc.pop(self.acc, None)
        srcs = wat = None
        if self.srcs is not None:
            srcs, wat = [], []
            self.srcs.append(srcs)
            self.watched.append(wat)
        start = r[s] & 0xFF
        push(0x0001)
        buf, stack, dyn, pc, n = [], [], 0, self.play, 0
        while True:
            op = ops[pc]
            k = op[0]
            pc += 1
            if k == "asg":
                r[op[1]] = op[2](r, m, rd)
                if prov is not None:
                    if op[4] is not None:
                        wat.append((op[4], None, _derived(op[3], r, m, rd, prov, ploc)))
                    _bind(ploc, op[1], _copy(op[3], r, m, rd, prov, ploc))
            elif k == "st":
                a = op[1](r, m, rd)
                m[a] = op[2](r, m, rd)
                if C.SID_LO <= a <= C.SID_HI:
                    buf.append((a - C.SID_LO, m[a]))
                    if prov is not None:
                        srcs.append(_derived(op[3], r, m, rd, prov, ploc))
                        if op[4] is not None:  # a watched store reports its own SID address too
                            wat.append((op[4], a, srcs[-1]))
                elif prov is not None:  # a one-cell value carries that cell's origin on
                    if op[4] is not None:
                        wat.append((op[4], a, _derived(op[3], r, m, rd, prov, ploc)))
                    _bind(prov, a, _copy(op[3], r, m, rd, prov, ploc))
            elif k == "stw":
                a, v = op[1](r, m, rd), op[2](r, m, rd)
                for j in range(op[5]):
                    c = (a + j) & 0xFFFF
                    m[c] = (v >> (8 * j)) & 0xFF
                    if C.SID_LO <= c <= C.SID_HI:
                        buf.append((c - C.SID_LO, m[c]))
                        if prov is not None:
                            srcs.append(_derived(op[3][j], r, m, rd, prov, ploc))
                    elif prov is not None:
                        if op[4] is not None:
                            wat.append((op[4], c, _derived(op[3][j], r, m, rd, prov, ploc)))
                        _bind(prov, c, _copy(op[3][j], r, m, rd, prov, ploc))
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
                if prov is not None:
                    ploc.pop(op[1], None)
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
                orgs = None if prov is None else [_copy(d, r, m, rd, prov, ploc) for d in op[5]]
                for i, v in zip(op[2], vals):
                    r[i] = v
                for i, o in zip(op[2], orgs or ()):
                    _bind(ploc, i, o)
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


def eval_src(prog, trace, nframes, state0=None, pin=None):
    """``(per-frame writes, per-frame source cells)``: ``eval_fp`` before projection.

    ``srcs[f][k]`` is the tuple of cells the k-th SID write of frame f derives its byte
    from — each cell the value read, ahead of it that byte's origin (spec 1.4), then the
    origins of the locals it read. ``pin`` replaces ``prog.pinned`` (measurement only)."""
    ev = Evaluator(prog, trace, state0, sources=True, pin=pin)
    return ev.frames(nframes), ev.srcs


def eval_watch(prog, trace, nframes, watch, state0=None, pin=None):
    """``(writes, source cells, watched stores)``: ``eval_src`` plus named non-SID stores.

    ``watch`` is a sequence of ``asg``/``st`` statements; ``watched[f]`` lists
    ``(index into watch, cell or None, source cells)`` per execution, in order —
    a snapshot names a re-staged cell's last row, not each read's (spec 1.4)."""
    ev = Evaluator(prog, trace, state0, sources=True, watch=watch, pin=pin)
    return ev.frames(nframes), ev.srcs, ev.watched


def gate_fp(model, nframes, prog=None):
    """Gate FP verdict: None if the frame program reproduces the walker projection.

    Both sides consume one ``iota`` run, so the law is well defined (spec 1.3)."""
    trace, walker = frameprog.iota(model, nframes)
    if prog is None:
        prog = frameprog.program(model)
    return framelog.diff(eval_fp(prog, trace, nframes), framelog.canonical(walker))
