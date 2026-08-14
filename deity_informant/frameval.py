"""Reference evaluator for frameprog and Gate FP (docs/frameprog.md 1.4).

Statement trees compile once to a flat op array over one local environment and
the state image (spec 2: no code image, patched cells are state); SID writes
buffer per frame and flush through the single projection ``framelog.canonical``.
"""

from __future__ import annotations

from . import datadecl
from . import desmc
from . import expr as E
from . import framefuse
from . import framelog
from . import frameproc
from . import frameprog
from . import grammar as G
from . import ptrcert
from . import sidprog
from . import structured as C
from .render import name_addr

_GUARD = 8_000_000
_REG_INIT = {frameproc._reg_local(i): (0xFF if i == 3 else 0) for i in range(16)}

# ---- the protected regions: machine locations no frame program names -------------
_STACK = range(0x0100, 0x0200)
_CODE_REGION = "executable memory"  # de-SMC (docs/frameprog.md 2.1)
_STACK_REGION = "the stack page"  # the machine stack (docs/denotation-solve.md 8.4)


class FrameFault(RuntimeError):
    """The frame program left its guarded envelope (fault, never improvise)."""


class _Page:
    """Who owns a page-one cell at an access: the artifact's writes, then the frame.

    ``owned`` are the cells the artifact stored and no machine push has crossed since,
    ``stale`` the ones a push did cross. Ownership is asked before the frame rule, so a
    cell the program wrote and then moved ``sp`` below is still its own datum (8.4)."""

    __slots__ = ("spx", "owned", "stale")

    def __init__(self, spx, owned, stale):
        self.spx = spx
        self.owned = owned
        self.stale = stale

    def live(self, a, sz, r):
        """The first byte of a ``sz``-byte access at ``a`` the machine's frame owns.

        Page one above the stack top holds what ``run_frame`` and each surviving call
        pushed; below the top it is free space, and either way a cell in ``owned`` holds
        the byte the artifact put there."""
        top = r[self.spx]
        for j in range(sz):
            c = (a + j) & 0xFFFF
            if c in _STACK and (c & 0xFF) > top and c not in self.owned:
                return c
        return None

    def read(self, a, sz, r):
        """... that, or a cell a push crossed after the artifact wrote it (``stale``)."""
        got = self.live(a, sz, r)
        if got is not None:
            return got
        return next((c for c in ((a + j) & 0xFFFF for j in range(sz)) if c in self.stale), None)


def _protected(prog):
    """Cell -> the region it belongs to: executable memory no statement may name.

    Page one is not here: what the machine owns in it is decided at the access, by
    what the artifact wrote and where the stack pointer stands (``_Page``)."""
    return dict.fromkeys(desmc.executable(prog), _CODE_REGION)


def _off_stack(f, sz, page):
    """``f`` refusing an address any byte of which is the machine's, not the artifact's.

    Above the stack top an unowned cell holds a word the artifact never wrote; in
    ``stale`` a push has crossed the byte it left there. The address is concrete here."""

    def one(r, m, rd):
        a = f(r, m, rd)
        bad = page.read(a, sz, r)
        if bad is not None:
            raise FrameFault("load from %s $%04X" % (_STACK_REGION, bad))
        return a

    return one


# ---- the extent guard: an annotated web derefs inside its own blocks ------------
def _merge(spans):
    """The same bytes as ``spans``, as ascending disjoint half-open ranges."""
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _blocks(bases, regions):
    """The bytes an extent's blocks cover; a base the registry omits admits none."""
    out = []
    for b in bases:
        at = regions.at(b)
        if at is not None:
            out.append((at[0]["base"], at[0]["base"] + at[0]["size"]))
    return _merge(out)


def _inside(spans, c):
    return any(lo <= c < hi for lo, hi in spans)


def _outside(a, width, spans, who):
    """The fault an access raises, named at the first byte its extent does not cover."""
    first = next((c for c in range(a, a + width) if not _inside(spans, c)), a)
    return "extent $%04X outside %s" % (first, who)


class Extent:
    """The extent guard at the two address seats (register-model-lift 2b).

    Every byte of an access through an annotated web must land in that web's blocks;
    attribution is static and per site (``ptrcert.root_cells``, as ``ptrextent.Probe``
    charges by), so an unspelled site keeps its own closure. The check is at the use."""

    __slots__ = ("spans", "sites")

    def __init__(self, prog):
        regions = datadecl.Regions(prog.data_decls)
        self.spans = {
            c: _blocks(bs, regions) for c, bs in (getattr(prog, "extents", None) or {}).items()
        }
        self.sites = 0

    def __call__(self, addr, f, width):
        """``f`` guarded by the extent this site's own roots name, else ``f`` itself.

        Every byte of the access is checked, so a word at a block's last cell reads one
        past it and faults; the union over roots never over-faults, an address two
        annotated webs both spelled being inside where either one declares it."""
        roots = sorted({c for c in ptrcert.root_cells(addr) if c in self.spans})
        if not roots:
            return f
        self.sites += 1
        spans = _merge(s for c in roots for s in self.spans[c])
        who = ", ".join(name_addr(c) for c in roots)
        if len(spans) == 1:
            lo, top = spans[0][0], spans[0][1] - width + 1

            def one(r, m, rd):
                a = f(r, m, rd)
                if lo <= a < top:
                    return a
                raise FrameFault(_outside(a, width, spans, who))

            return one

        def many(r, m, rd):
            a = f(r, m, rd)
            for c in range(a, a + width):
                if not _inside(spans, c):
                    raise FrameFault(_outside(a, width, spans, who))
            return a

        return many


class Relocated:
    """The de-SMC address seat: ``relocated`` moves the instruction stream, not the state.

    A run's declaration names its **code** bytes at the destination; every other byte of
    it is still itself, so an access that lands on a play-written cell of the run reads
    and writes that cell where the rest of the program does (docs/frameprog.md 2)."""

    __slots__ = ("state", "runs")

    def __init__(self, prog):
        code = desmc.executable(prog)
        data = set(prog.evidence.get("written", ()) or ()) - code
        self.runs = [(dst, dst + hi - lo, dst - lo) for lo, hi, dst in prog.relocated]
        self.state = {c for lo, hi, _d in prog.relocated for c in data if lo <= c <= hi}

    def __call__(self, addr, f, width):
        """``f`` translated where the address is based in a moved run, else ``f`` itself."""
        base, _idx = frameproc.addr_split(addr)
        got = [d for lo, hi, d in self.runs if lo <= base <= hi] if base is not None else []
        if not got or not self.state:
            return f
        disp, state = got[0], self.state

        def one(r, m, rd):
            a = f(r, m, rd)
            src = (a - disp) & 0xFFFF
            return src if any((src + j) & 0xFFFF in state for j in range(width)) else a

        return one


def _guard(prog):
    """The guard a program's own annotations ask for: none where it declares none."""
    got = Extent(prog) if getattr(prog, "extents", None) else None
    if not getattr(prog, "relocated", None):
        return got
    return _seat(Relocated(prog), got)


def _seat(probe, check):
    """The one address wrapper a seat carries, b0's observer ahead of 2b's guard.

    The probe sees the address first, so a census run under both instruments records
    what it faulted on: an address observed and then refused is what names the gap."""
    if probe is None:
        return check
    if check is None:
        return probe
    return lambda n, f, w: check(n, probe(n, f, w), w)


# ---- expressions: closures over (locals, state image, volatile reader) ----------
_width = frameproc.loc_width  # loc leaves carry their own width; every other node is E.width


def _load(n, slot, probe, page):
    addr, sz = n[1], n[2]
    if addr[0] == "const":
        cells = [(addr[1] + j) & 0xFFFF for j in range(sz)]
        if sidprog._ld_safe(addr) and not any(c in _STACK for c in cells):
            if sz == 1:
                a = cells[0]
                return lambda r, m, rd: m[a]
            return lambda r, m, rd: sum(m[c] << (8 * j) for j, c in enumerate(cells))
    fa = _expr(addr, slot, probe, page)
    if probe is not None:
        fa = probe(addr, fa, sz)
    fa = _off_stack(fa, sz, page)
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


def _expr(n, slot, probe, page):
    """Closure ``(r, m, rd) -> value`` for one frameprog expression node.

    ``probe`` is the address-seat wrapper -- b0's read-only observer, 2b's extent
    guard, or both: it sees each computed address node once, at compile time, and
    hands back the address closure it wants called. ``None`` builds today's."""
    k = n[0]
    if k == "const":
        v = n[1]
        return lambda r, m, rd: v
    if k == "loc":
        i = slot(n[1])
        return lambda r, m, rd: r[i]
    if k == "mem":
        return _load(n, slot, probe, page)
    if k != "op":
        raise FrameFault("unexpected expression node %r" % (k,))
    mn, sz = n[1], n[3]
    fs = tuple(_expr(c, slot, probe, page) for c in n[2])
    szs = [_width(c) for c in n[2]]
    return lambda r, m, rd: E._apply(mn, [f(r, m, rd) for f in fs], szs, sz)


# ---- compilation: statement trees to one flat op array -------------------------
def _pc(lbl):
    return int(lbl[1:], 16)


class _Code:
    """Flat op array with label/entry fixups; one environment for all locals.

    ``call``/``goto`` cross procedures as machine transfers, so locals are
    program-wide (registers are shared, temporaries never outlive a block)."""

    def __init__(self, prog, watch=(), pin=None, probe=None):
        self.mem0 = prog.mem0
        self.protect = _protected(prog)  # the memory no store of the program reaches
        self.probe = _seat(probe, _guard(prog))  # both seats carry the same wrapper
        self.pin = dict(getattr(prog, "pinned", ()) if pin is None else pin)
        self.watch = {id(s): i for i, s in enumerate(watch)}
        self.tagged = set()
        self.ops = []
        self.idx = {}
        self.spx = self.slot("sp")  # the register the machine's live frame is read off
        self.owned = set()  # page-one cells the artifact wrote and no push has crossed
        self.stale = set()  # ... and the ones a push did cross: reading one reads the word
        self.page = _Page(self.spx, self.owned, self.stale)
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
        """Bind a serialized pc to an op index (first wins)."""
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
        return _expr(n, self.slot, self.probe, self.page)

    def addr(self, n, sz):
        """A store's address closure, wrapped: a write-through deref is one too."""
        f = self.expr(n)
        return f if self.probe is None else self.probe(n, f, sz)

    def store_addr(self, n, sz):
        """A store address that provably leaves the protected regions (``_protected``).

        A const code byte is refused here -- a program that stores into its own
        instruction stream must not be emitted; page one and a computed address fault
        at the cell, so each invariant is machine-checked and never a claim."""
        cells, page, owned, stale = self.protect, self.page, self.owned, self.stale
        span = [(n[1] + j) & 0xFFFF for j in range(sz)] if n[0] == "const" else []
        bad = [c for c in span if c in cells]
        if bad:
            raise FrameFault("store into %s $%04X" % (cells[bad[0]], bad[0]))
        f = self.addr(n, sz)
        if span and not any(c in _STACK for c in span):
            return f

        def checked(r, m, rd):
            a = f(r, m, rd)
            for j in range(sz):
                who = cells.get((a + j) & 0xFFFF)
                if who is not None:
                    raise FrameFault("store into %s $%04X" % (who, (a + j) & 0xFFFF))
            hit = page.live(a, sz, r)
            if hit is not None:
                raise FrameFault("store into %s $%04X" % (_STACK_REGION, hit))
            for c in ((a + j) & 0xFFFF for j in range(sz)):
                if c in _STACK:
                    owned.add(c)
                    stale.discard(c)
            return a

        return checked

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
            if s[0] == "igoto" and s[2] is None and nxt is not None:
                self.vecgoto(s)
                i += 1
                continue
            self.stmt(s, ctx)
            i += 1

    def vecgoto(self, s):
        """A static vector's goto, whose landing body follows it inline.

        The landing is bound to this statement as well as program-wide: a region
        copied to several sites carries one such binding per copy, and only the
        statement's own table tells them apart (the arms of a ``switch goto`` are
        scoped the same way). The program-wide bind stays, so a transfer from
        outside still resolves exactly as it did."""
        self.dyn(s)
        d = self.emit(("swd", None))
        p = s[1]
        land = self.mem0[p] | (self.mem0[(p & 0xFF00) | ((p + 1) & 0xFF)] << 8)
        self.patch(d, 1, {land: len(self.ops)})
        self.mark(land)

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
            self.emit(
                ("st", self.store_addr(s[1], 1), self.expr(s[2]), self.deriv(s[2]), self.tag(s))
            )
            return
        halves = framefuse.unpack(s[2]) or (s[2],) * sz
        derv = tuple(self.deriv(h) for h in halves)
        # The byte order is the store's own: ascending unless it says otherwise.
        order = tuple(range(sz))[:: -1 if frameproc.hi_first(s) else 1]
        self.emit(("stw", self.store_addr(s[1], sz), self.expr(s[2]), derv, self.tag(s), order))

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

    def _exit(self, s, ctx, half):
        n = frameproc.exit_level(s)
        if not ctx or len(ctx) < n:
            raise FrameFault("%s %d outside %d enclosing regions" % (s[0], n, len(ctx or ())))
        ctx[-n][half].append(self.emit(("jmp", None)))

    def _s_cont(self, s, ctx):
        self._exit(s, ctx, 0)

    def _s_brk(self, s, ctx):
        self._exit(s, ctx, 1)

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

    def _s_loop(self, s, ctx):
        head = len(self.ops)
        conts, brks = [], []
        self.seq(s[1], (ctx or ()) + ((conts, brks),))
        self.emit(("jmp", head))
        for i in conts:
            self.patch(i, 1, head)
        for i in brks:
            self.patch(i, 1)

    def _s_for(self, s, ctx):
        _k, name, init, last, body = s
        i = self.slot(name)
        self.emit(("asg", i, lambda r, m, rd, v=init: v, (None, ()), None))
        head = len(self.ops)
        conts, brks = [], []
        self.seq(body, (ctx or ()) + ((conts, brks),))
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

    def __init__(self, prog, trace, state0=None, sources=False, watch=(), pin=None, probe=None):
        self.code = _Code(prog, watch, pin, probe)
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
        name = sidprog._INPUTS.get(a)
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

        ``sp`` and the pushed return bytes are machine-faithful: call/ret move the
        shared stack register, and a ``ret`` continues at the slot the callee left.
        A push over a cell the artifact wrote is the one way the evaluator changes
        state behind the program's back: the cell goes stale and reading it faults."""
        ops, r, m, rd, s = self.code.ops, self.r, self.m, self._rd, self.sp
        rmap, prov, ploc = self.code.rmap, self.prov, self.ploc
        owned, stale = self.code.owned, self.code.stale

        def push(ret):
            p = r[s] & 0xFF
            q = (p - 1) & 0xFF
            for c in (0x100 + p, 0x100 + q):
                if c in owned:  # the artifact's byte is gone: reading it back is the fault
                    owned.discard(c)
                    stale.add(c)
            m[0x100 + p] = (ret >> 8) & 0xFF
            m[0x100 + q] = ret & 0xFF
            r[s] = (q - 1) & 0xFF

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
                for j in op[5]:  # the store's own byte-emission order
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
                w = m[0x100 + ((p + 1) & 0xFF)] | (m[0x100 + q] << 8)
                if stack and stack[-1][1] == p and stack[-1][2] == w:
                    pc = stack.pop()[0]
                elif q >= start:
                    break
                else:  # the slot the callee wrote, or the program's own sp move
                    pc = self._resolve(self.code.rmap, (w + 1) & 0xFFFF, "ret")
            elif k == "sw":
                pc = self._resolve(op[2], m[op[1]], "switch $%04X" % op[1])
            elif k == "call":
                push(op[2])
                stack.append((pc, r[s], op[2]))
                pc = op[1]
            elif k == "pcall":
                vals = [f(r, m, rd) for f in op[3]]
                orgs = None if prov is None else [_copy(d, r, m, rd, prov, ploc) for d in op[5]]
                for i, v in zip(op[2], vals):
                    r[i] = v
                for i, o in zip(op[2], orgs or ()):
                    _bind(ploc, i, o)
                push(op[4])
                stack.append((pc, r[s], op[4]))
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
                stack.append((pc, r[s], op[2]))
                pc = op[1].get(dyn) or self._resolve(rmap, dyn, "switch call")
            elif k == "calld":
                push(op[1])
                stack.append((pc, r[s], op[1]))
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


def eval_fp(prog, trace, nframes, state0=None, held0=None):
    """Canonical per-frame records of ``prog`` under the pinned trace (spec 1.4).

    Output semantics: buffer the frame's SID writes, flush one canonical
    record per frame through the single projection."""
    return framelog.canonical(Evaluator(prog, trace, state0).frames(nframes), held0)


def sid_held0(prog):
    """Post-init value of every SID register: the lane a frame leaves untouched."""
    return {r: prog.mem0[framelog._ABS + r] for r in range(framelog._NREG)}


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
    held0 = sid_held0(prog)
    return framelog.diff(
        eval_fp(prog, trace, nframes, held0=held0), framelog.canonical(walker, held0)
    )
