"""Structured decompiler core (docs/decompiler-plan.md P1-P4).

Evidence trace -> per-pc blocks (opcode-SMC sites become per-byte variants,
closed by static value-set analysis) -> symbolic event summaries with
machine-order loads and cycle/penalty events -> compiled standalone walker.
"""

from __future__ import annotations

from . import expr as E
from .lifter import MODE_LEN, OPS, lift
from .vm import PcodeVM

_VOL = frozenset((0xD011, 0xD012, 0xD41B, 0xD41C))
_VOL0 = frozenset((0xD019, 0xDC0D))  # constant-0 sources under the per-frame driver
_GUARD = 8_000_000
_BLOCK_CAP = 64
SID_LO, SID_HI = 0xD400, 0xD418


class DecompileError(RuntimeError):
    """The analysis cannot model a site; decompilation fails (never degrades)."""


class WalkError(RuntimeError):
    """Standalone execution left the decompiled model (loud fault)."""


def volatile_read(m, a, c):
    """Cycle-derived volatile reads; formulas mirror ``PcodeVM._rd``."""
    if a == 0xD012:
        return (c // 63) % 312 & 0xFF
    if a == 0xD011:
        return (m[0xD011] & 0x7F) | ((((c // 63) % 312 >> 8) & 1) << 7)
    return (c >> 3) & 0xFF  # $D41B / $D41C


def _dyn_read(m, a, c):
    if a in _VOL:
        return volatile_read(m, a, c)
    if a in _VOL0:
        return 0
    return m[a]


# ---- P1: evidence (full-length concrete trace; doubles as the oracle run) ----
class _EvidenceVM(PcodeVM):
    def __init__(self, mem):
        super().__init__(mem)
        self.written = set()

    def _wr(self, addr, val, sz):
        for i in range(sz):
            self.written.add((addr + i) & 0xFFFF)
        super()._wr(addr, val, sz)


class Evidence:
    """Executed instruction identities, block leaders, written cells, oracle log."""

    def __init__(self, pcs, leaders, written, wlog, end_mem, end_reg):
        self.pcs = pcs  # {pc: set(opcode bytes executed there)}
        self.leaders = leaders
        self.written = written
        self.wlog = wlog
        self.end_mem = end_mem
        self.end_reg = end_reg


def trace(mem, init, play, frames):
    """Run init + ``frames`` play calls concretely, recording evidence."""
    vm = _EvidenceVM(mem)
    vm.wlog = []
    pcs = {}
    leaders = {init, play}
    cache = {}
    reg = vm.reg

    def run_entry(entry):
        start = reg[3]
        vm._push(0x00)
        vm._push(0x01)
        pc = entry
        n = 0
        while reg[3] < start:
            op = vm.mem[pc]
            pcs.setdefault(pc, set()).add(op)
            nxt = vm.step(pc, cache, lift)
            if nxt != (pc + MODE_LEN[OPS[op][1]]) & 0xFFFF:
                leaders.add(nxt)
            pc = nxt
            n += 1
            if n > _GUARD:
                raise RuntimeError("runaway at %04X" % pc)

    run_entry(init)
    for _ in range(frames):
        run_entry(play)
    return Evidence(pcs, leaders, vm.written, vm.wlog, bytes(vm.mem), list(vm.reg))


# ---- expression -> python source ----------------------------------------------
_CMP = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<", "INT_LESSEQUAL": "<="}


def _src(n):
    k = n[0]
    if k == "const":
        return hex(n[1])
    if k == "reg":
        return "r[%d]" % n[1]
    if k == "uni":
        return "t%d" % n[1]
    if k == "mem":
        return "m[%s]" % _src(n[1])
    mn, kids, sz = n[1], n[2], n[3]
    msk = hex(E.mask(sz))
    if mn == "INT_ADD":
        return "((%s) & %s)" % (" + ".join(_src(c) for c in kids), msk)
    if mn == "INT_SUB":
        return "((%s - %s) & %s)" % (_src(kids[0]), _src(kids[1]), msk)
    if mn == "INT_AND":
        return "(%s)" % " & ".join(_src(c) for c in kids)
    if mn == "INT_OR":
        return "(%s)" % " | ".join(_src(c) for c in kids)
    if mn == "INT_XOR":
        return "(%s)" % " ^ ".join(_src(c) for c in kids)
    if mn == "INT_LEFT":
        return "((%s << %s) & %s)" % (_src(kids[0]), _src(kids[1]), msk)
    if mn == "INT_RIGHT":
        return "(%s >> %s)" % (_src(kids[0]), _src(kids[1]))
    if mn == "INT_ZEXT":
        return _src(kids[0])
    if mn == "INT_CARRY":
        return "(1 if %s + %s > %s else 0)" % (
            _src(kids[0]),
            _src(kids[1]),
            hex(E.mask(E.width(kids[0]))),
        )
    if mn in _CMP:
        return "(1 if %s %s %s else 0)" % (_src(kids[0]), _CMP[mn], _src(kids[1]))
    raise DecompileError("no source form for %s" % mn)


# ---- blocks -------------------------------------------------------------------
class Block:
    """Events: ``("ld", n, addr)`` | ``("st", addr, val)`` | ``("cyc", k)`` |
    ``("pen", kind, aux, idx)``. ``term``: goto/br/jmp/jmpd/jmpind/jsr/rts
    tuples carrying any dynamic-target expressions; ``regs`` is 16 out-exprs."""

    __slots__ = ("pc", "op0", "pcs", "events", "term", "regs", "fn")

    def __init__(self, pc, op0, pcs, events, term, regs):
        self.pc = pc
        self.op0 = op0
        self.pcs = pcs
        self.events = list(events)
        self.term = term
        self.regs = list(regs)
        self.fn = None


class _BlockBuilder:
    """Symbolically executes instructions from ``entry`` to a terminator."""

    def __init__(self, model, entry, op0):
        self.model = model
        self.events = []
        self.nuni = 0
        self.sreg = [E.reg(i) for i in range(16)]
        self.suni = {}
        self.term = None
        self.pcs = []
        pc = entry
        scratch = bytearray(model.mem0)
        scratch[entry] = op0
        while True:
            self.pcs.append(pc)
            rec = lift(scratch, pc)
            self._insn(pc, rec)
            if self.term is not None:
                break
            pc = (pc + rec["len"]) & 0xFFFF
            if pc in model.written or len(self.pcs) >= _BLOCK_CAP:
                self.term = ("goto", pc)  # mutable opcode cell: dispatch boundary
                break
        self.block = Block(entry, op0, self.pcs, self.events, self.term, self.sreg)

    def _slot(self, addr_expr):
        n = self.nuni
        self.nuni += 1
        self.events.append(("ld", n, addr_expr))
        return E.uni(n, 1)

    def _rdbyte(self, addr):
        addr &= 0xFFFF
        if addr in self.model.written:
            return self._slot(E.konst(addr, 2))
        return ("mem", E.konst(addr, 2), 1)

    def _residual(self, srcs, fn, pc):
        if fn == "word":
            lo, hi = self._rdbyte(pc + srcs[0]), self._rdbyte(pc + srcs[1])
            return self._word(lo, hi)
        leaf = self._rdbyte(pc + srcs[0])
        if fn == "hi1":
            leaf = E.op("INT_ADD", [leaf, E.konst(1, 1)], 1)
        return leaf

    @staticmethod
    def _word(lo, hi):
        return E.op(
            "INT_OR",
            [
                E.op("INT_ZEXT", [lo], 2),
                E.op("INT_LEFT", [E.op("INT_ZEXT", [hi], 2), E.konst(8, 1)], 2),
            ],
            2,
        )

    def _sval(self, vn, i, j, pmap, pc):
        sp = vn[0]
        if sp == "r":
            return self.sreg[vn[1]]
        if sp == "u":
            return self.suni[vn[1]]
        p = pmap.get((i, j))
        if p is not None and any((pc + off) & 0xFFFF in self.model.written for off in p[0]):
            leaf = self._residual(p[0], p[1], pc)
            if vn[2] > 1 and E.width(leaf) == 1:
                return E.op("INT_ZEXT", [leaf], vn[2])
            return leaf
        return E.konst(vn[1], vn[2])

    def _insn(self, pc, rec):
        pmap = rec["prov"]["ops"]
        for i, (mn, out, ins) in enumerate(rec["ops"]):
            if mn == "STORE":
                assert ins[1][2] == 1, rec
                addr = self._sval(ins[0], i, 0, pmap, pc)
                val = self._sval(ins[1], i, 1, pmap, pc)
                self.events.append(("st", addr, val))
                continue
            if mn == "LOAD":
                assert out[2] == 1, rec
                self._set(out, self._slot(self._sval(ins[0], i, 0, pmap, pc)))
                continue
            svals = [self._sval(vn, i, j, pmap, pc) for j, vn in enumerate(ins)]
            self._set(out, svals[0] if mn == "COPY" else E.op(mn, svals, out[2]))
        self.events.append(("cyc", rec["cyc"]))
        self._pen(pc, rec)
        self._ctrl(pc, rec)

    def _set(self, out, ex):
        (self.sreg if out[0] == "r" else self.suni)[out[1]] = ex

    def _pen(self, pc, rec):
        pen = rec["pen"]
        if pen is None or pen[0] == "branch" or rec["ctrl"][0] == "br":
            return
        kind = pen[0]
        idx = self.sreg[1 if kind == "ax" else 2]
        aux = (
            self._operand_expr(pc, [1], "id")
            if kind == "iy"
            else self._operand_expr(pc, [1, 2], "word")
        )
        self.events.append(("pen", kind, aux, idx))

    def _operand_expr(self, pc, offs, fn):
        if any((pc + off) & 0xFFFF in self.model.written for off in offs):
            return self._residual(offs, fn, pc)
        m = self.model.mem0
        if fn == "word":
            return E.konst(m[(pc + 1) & 0xFFFF] | (m[(pc + 2) & 0xFFFF] << 8), 2)
        return E.konst(m[(pc + 1) & 0xFFFF], 1)

    def _ctrl_target_expr(self, pc, rec):
        """Dynamic control target from mutable instruction bytes (else None)."""
        ctrlp = rec["prov"]["ctrl"]
        if ctrlp is None:
            return None
        srcs, fn, _val = ctrlp
        if not any((pc + off) & 0xFFFF in self.model.written for off in srcs):
            return None
        if fn == "word":
            return self._residual(srcs, fn, pc)
        if fn == "rel":
            b = self._rdbyte(pc + srcs[0])
            msb = E.op("INT_AND", [E.op("INT_RIGHT", [b, E.konst(7, 1)], 1), E.konst(1, 1)], 1)
            corr = E.op("INT_LEFT", [E.op("INT_ZEXT", [msb], 2), E.konst(8, 1)], 2)
            base = E.op(
                "INT_ADD",
                [E.konst((pc + 2) & 0xFFFF, 2), E.op("INT_ZEXT", [b], 2)],
                2,
            )
            return E.op("INT_SUB", [base, corr], 2)
        raise DecompileError("ctrl provenance %r at %04X" % (fn, pc))

    def _ctrl(self, pc, rec):
        ctrl = rec["ctrl"]
        kind = ctrl[0]
        if kind == "next":
            return
        dyn = self._ctrl_target_expr(pc, rec)
        if kind == "br":
            flag = self.sreg[ctrl[1][1]]
            tgt = None if dyn is not None else ctrl[3]
            self.term = ("br", ctrl[2], tgt, ctrl[4], flag, dyn)
        elif kind == "jmp":
            self.term = ("jmpd", dyn) if dyn is not None else ("jmp", ctrl[1])
        elif kind == "jmpind":
            self.term = ("jmpind", None if dyn is not None else ctrl[1], dyn)
        elif kind == "jsr":
            ret = (pc + rec["len"] - 1) & 0xFFFF
            self.term = ("jsr", None if dyn is not None else ctrl[1], ret, dyn)
        elif kind == "rts":
            self.term = ("rts",)
        else:
            raise DecompileError("control %r at %04X not modeled" % (kind, pc))


# ---- compile a Block to a python closure --------------------------------------
def compile_block(block):
    lines = []
    for ev in block.events:
        k = ev[0]
        if k == "ld":
            n, ax = ev[1], ev[2]
            if E.is_const(ax):
                a = ax[1]
                if a in _VOL:
                    lines.append("t%d = vol(m, %s, c)" % (n, hex(a)))
                elif a in _VOL0:
                    lines.append("t%d = 0" % n)
                else:
                    lines.append("t%d = m[%s]" % (n, hex(a)))
            else:
                lines.append("t%d = dyn(m, %s, c)" % (n, _src(ax)))
        elif k == "st":
            ax, vx = ev[1], ev[2]
            if E.is_const(ax):
                a = ax[1]
                lines.append("m[%s] = %s" % (hex(a), _src(vx)))
                if SID_LO <= a <= SID_HI:
                    lines.append("w.append((c, %d, m[%s]))" % (a - SID_LO, hex(a)))
            else:
                lines.append("a = %s" % _src(ax))
                lines.append("v = %s" % _src(vx))
                lines.append("m[a] = v")
                lines.append("_sid(a, v, c, w)")
        elif k == "cyc":
            lines.append("c += %d" % ev[1])
        else:
            _, kind, aux, idx = ev
            if kind == "iy":
                if E.is_const(aux):
                    z = aux[1]
                    lines.append("b = m[%s] | (m[%s] << 8)" % (hex(z), hex((z + 1) & 0xFF)))
                else:
                    lines.append("z = %s" % _src(aux))
                    lines.append("b = m[z] | (m[(z + 1) & 0xFF] << 8)")
            else:
                lines.append("b = %s" % _src(aux))
            lines.append("c += (b & 0xFF00) != ((b + %s) & 0xFF00)" % _src(idx))
    term = block.term
    if term[0] == "br":
        lines.append("x = (%s, %s)" % (_src(term[4]), _src(term[5]) if term[5] else "None"))
    elif term[0] in ("jmpd",):
        lines.append("x = %s" % _src(term[1]))
    elif term[0] in ("jmpind", "jsr") and term[-1] is not None:
        lines.append("x = %s" % _src(term[-1]))
    else:
        lines.append("x = None")
    regs = "[%s]" % ", ".join(_src(ex) for ex in block.regs)
    src = "def _f(m, r, c, w, vol, dyn, _sid):\n    %s\n    return c, %s, x\n" % (
        "\n    ".join(lines or ["pass"]),
        regs,
    )
    ns = {}
    exec(src, ns)  # noqa: S102 - generated from the block summary, no user input
    return ns["_f"]


def _sid_log(a, v, c, w):
    if SID_LO <= a <= SID_HI:
        w.append((c, a - SID_LO, v))


# ---- P4: value-set closure, SP flow, dominators -------------------------------
def _addr_range(n):
    """Conservative [lo, hi] of an address expression."""
    if E.is_const(n):
        return (n[1], n[1])
    if n[0] == "op" and n[1] in ("INT_ADD", "INT_OR"):
        if n[1] == "INT_ADD":
            lo = hi = 0
            for c in n[2]:
                clo, chi = _addr_range(c)
                lo += clo
                hi += chi
        else:
            lo, hi = 0, 0
            for c in n[2]:
                clo, chi = _addr_range(c)
                lo = max(lo, clo)
                hi += chi
        return (min(lo, 0xFFFF), min(hi, 0xFFFF))
    if n[0] == "op" and n[1] == "INT_ZEXT":
        return _addr_range(n[2][0])
    return (0, E.mask(E.width(n)))


def _signed8(v):
    return v - 256 if v & 0x80 else v


class _Need(Exception):
    def __init__(self, cell):
        super().__init__(cell)
        self.cell = cell


TOP = "top"  # lattice top: value set unknown (contained; fatal only where proof is required)
_ANYBYTE = frozenset(range(256))


class Analysis:
    """Cell value-set closure + SP-constant flow + dominators (plan P4)."""

    def __init__(self, model):
        self.model = model
        self.S = {}
        self.R = {}  # (block key, reg) -> value set at block entry
        self.sp_in = {}
        self.idom = {}
        self._cell_stores = None
        self._virtual = {0x1FF: {0}, 0x1FE: {1}}  # driver sentinel pushes
        self.stack_rule = False
        self._r_grew = False
        self._widen = {}
        self._memo = {}
        self._imemo = {}
        self._rts_cache = {}

    # -- store indexing -----------------------------------------------------
    def _stores(self):
        if self._cell_stores is None:
            const, dyn = {}, []
            for key, blk in self.model.blocks.items():
                for ev in blk.events:
                    if ev[0] != "st":
                        continue
                    if E.is_const(ev[1]):
                        const.setdefault(ev[1][1], []).append((key, ev[2]))
                    else:
                        dyn.append((key, _addr_range(ev[1]), ev[1], ev[2]))
            self._cell_stores = (const, dyn)
        return self._cell_stores

    def _hits(self, key, rng, aexpr, cell):
        if not rng[0] <= cell <= rng[1]:
            return False
        return any(lo <= cell <= hi for lo, hi in self._ivals(aexpr, key))

    def _ivals(self, n, key, depth=0):
        """Sound interval set for an address expression (singletons when the
        value set resolves, wide bounds when it does not)."""
        wide = [(0, E.mask(E.width(n)))]
        if depth > 32:
            return wide
        hit = self._imemo.get((n, key))
        if hit is not None:
            return hit
        out = self._ivals_uncached(n, key, depth, wide)
        self._imemo[(n, key)] = out
        return out

    def _ivals_uncached(self, n, key, depth, wide):
        try:
            vs = self._resolve(n, key, depth + 1)
            if len(vs) <= 32:
                return [(v, v) for v in sorted(vs)]
            return [(min(vs), max(vs))]
        except DecompileError:
            pass
        if n[0] != "op":
            return wide
        mn, kids, sz = n[1], n[2], n[3]
        m = E.mask(sz)
        if mn == "INT_ZEXT":
            return self._ivals(kids[0], key, depth + 1)
        if mn in ("INT_ADD", "INT_OR"):  # sum bound is sound for OR of masked terms
            out = [(0, 0)]
            for c in kids:
                ci = self._ivals(c, key, depth + 1)
                out = [(lo + cl, hi + ch) for lo, hi in out for cl, ch in ci]
                if len(out) > 32:
                    out = [(min(lo for lo, _ in out), max(hi for _, hi in out))]
            if any(hi > m for _, hi in out):
                return [(0, m)]
            return out
        if mn == "INT_LEFT" and E.is_const(kids[1]):
            sh = kids[1][1]
            out = [(lo << sh, hi << sh) for lo, hi in self._ivals(kids[0], key, depth + 1)]
            return [(0, m)] if any(hi > m for _, hi in out) else out
        if mn == "INT_AND":
            consts = [c[1] for c in kids if E.is_const(c)]
            if consts:
                return [(0, min(consts))]
        return wide

    # -- value resolution ---------------------------------------------------
    def _cell_set(self, a, reader_key):
        if a in self.S:
            if self.S[a] is TOP:
                return set(_ANYBYTE)  # any byte: downstream masks recover precision
            return set(self.S[a])
        if a not in self.model.written:
            return {self.model.mem0[a]}
        if not self.stack_rule or not 0x100 <= a <= 0x1FF:
            if len(self.S) > 768:  # seed-universe cap: further cells are any byte
                return set(_ANYBYTE)
            raise _Need(a)
        return self._stack_cell(a, reader_key)

    def _stack_cell(self, a, reader_key):
        const, dyn = self._stores()
        vals = set(self._virtual.get(a, ()))
        dominated = False
        for key, vexpr in const.get(a, ()):
            vals |= self._resolve(vexpr, key)
            if reader_key is not None and self._dominates(key, reader_key):
                dominated = True
        for key, blk in self.model.blocks.items():
            term = blk.term
            if term[0] == "jsr" and term[1] is not None:
                sp = self.sp_in.get(key)
                spo = None if not isinstance(sp, int) else _sp_eval(blk.regs[3], sp)
                if spo is None:
                    continue
                if 0x100 + (spo & 0xFF) == a:
                    vals.add(term[2] >> 8)
                if 0x100 + ((spo - 1) & 0xFF) == a:
                    vals.add(term[2] & 0xFF)
        for key, rng, aexpr, _vexpr in dyn:
            if self._hits(key, rng, aexpr, a):
                raise DecompileError("computed store may reach stack cell $%04X" % a)
        if not dominated:
            vals.add(self.model.mem0[a])
        return vals

    # -- register value sets (interblock propagation) ------------------------
    def _reg_set(self, key, i):
        cur = self.R.get((key, i))
        if cur is None:
            self.R[(key, i)] = set()
            self._r_grew = True
            return set()
        if cur is TOP:
            return set(_ANYBYTE)  # any byte: downstream masks recover precision
        return set(cur)

    def _proc_rts(self, entry):
        """rts blocks reachable from ``entry`` without leaving the procedure
        (calls are stepped over via their return continuation)."""
        cached = self._rts_cache.get(entry)
        if cached is not None:
            return cached
        model = self.model
        out = set()
        seen = set()
        work = list(model.variants(entry))
        self._rts_cache[entry] = out  # pre-bind: recursion terminates on cycles
        while work:
            key = work.pop()
            if key in seen:
                continue
            seen.add(key)
            blk = model.blocks[key]
            if blk.term[0] == "rts":
                out.add(key)
                continue
            if blk.term[0] == "jsr":
                work.extend(model.variants((blk.term[2] + 1) & 0xFFFF))
                continue
            try:
                for pc in self.term_targets(blk):
                    work.extend(model.variants(pc))
            except (DecompileError, _Need):
                pass
        return out

    def _pred_map(self):
        model = self.model
        self._rts_cache = {}
        preds = {}
        for key, blk in model.blocks.items():
            term = blk.term
            if term[0] == "jsr":
                callees = [term[1]] if term[1] is not None else []
                if term[1] is None:
                    try:
                        callees = self.term_targets(blk)
                    except (DecompileError, _Need):
                        callees = []
                rets = set()
                for callee in callees:
                    for skey in model.variants(callee):
                        preds.setdefault(skey, set()).add(key)
                    rets |= self._proc_rts(callee)
                for skey in model.variants((term[2] + 1) & 0xFFFF):
                    preds.setdefault(skey, set()).update(rets)
                continue
            try:
                tgts = self.term_targets(blk)
            except (DecompileError, _Need):
                tgts = []
            for pc in tgts:
                for skey in model.variants(pc):
                    preds.setdefault(skey, set()).add(key)
        drivers = self._proc_rts(model.play) | self._proc_rts(model.init)
        for skey in model.variants(model.play):
            preds.setdefault(skey, set()).update(drivers)
        for skey in model.variants(model.init):
            preds.setdefault(skey, set()).add("cold")
        return preds

    _WIDEN = 8

    def _update(self, table, entry, new):
        cur = table[entry]
        if new == cur or cur is TOP and new is TOP:
            return False
        wide = self._widen
        wide[entry] = wide.get(entry, 0) + 1
        table[entry] = TOP if wide[entry] > self._WIDEN else new
        return table[entry] != cur

    def _sweep_regs(self, preds):
        changed = False
        for key, i in list(self.R):
            vals = set()
            top = False
            for p in preds.get(key, ()):
                if p == "cold":
                    vals.add(0xFF if i == 3 else 0)
                    continue
                try:
                    vals |= self._pred_contrib(p, i, key)
                except DecompileError:
                    top = True
                    break
            new = TOP if top or len(vals) > 256 else vals
            changed = self._update(self.R, (key, i), new) or changed
        return changed

    def _pred_contrib(self, p, i, key):
        """Values reg ``i`` can carry from pred ``p`` into ``key``, filtered by
        ``p``'s branch condition when both expressions are pure in reg ``i``."""
        blk = self.model.blocks[p]
        out = blk.regs[i]
        term = blk.term
        if term[0] == "br" and term[2] is not None:
            tgt, ft, flag = term[2], term[3], term[4]
            pc = key[0]
            if (pc == tgt) != (pc == ft):
                want = term[1] if pc == tgt else 1 - term[1]
                refs = set()
                _regs_of(flag, refs)
                orefs = set()
                _regs_of(out, orefs)
                if refs <= {i} and orefs <= {i} and _pure(flag) and _pure(out):
                    vals = set()
                    for v in self._reg_set(p, i):
                        if _pure_eval(flag, i, v) == want:
                            vals.add(_pure_eval(out, i, v))
                    return vals
        return self._resolve(out, p)

    def _resolve(self, n, key, depth=0, budget=4096):
        if depth > 32:
            raise DecompileError("value resolution too deep")
        if E.is_const(n):
            return {n[1]}
        mkey = (n, key, budget)
        hit = self._memo.get(mkey)
        if hit is not None:
            if isinstance(hit, DecompileError):
                raise hit
            return set(hit)
        try:
            out = self._resolve_uncached(n, key, depth, budget)
        except DecompileError as exc:
            self._memo[mkey] = exc
            raise
        self._memo[mkey] = frozenset(out)
        return out

    def _resolve_uncached(self, n, key, depth, budget):
        if n[0] == "reg":
            return self._reg_set(key, n[1])
        if n[0] == "mem" and E.is_const(n[1]):
            return self._cell_set(n[1][1], key)
        if n[0] == "uni":
            blk = self.model.blocks[key]
            ld = next((e for e in blk.events if e[0] == "ld" and e[1] == n[1]), None)
            if ld is None:
                raise DecompileError("unresolvable load in value at block $%04X" % key[0])
            if E.is_const(ld[2]):
                a = ld[2][1]
                if a not in _VOL and a not in _VOL0:
                    return self._cell_set(a, key)
                return {0} if a in _VOL0 else set(_ANYBYTE)  # volatile: any byte
            return self._table_load(ld[2], key, depth)
        if n[0] == "op":
            sets = [sorted(self._resolve(c, key, depth + 1, budget)) for c in n[2]]
            total = 1
            for s in sets:
                total *= len(s)
            if total > budget:
                if E.width(n) == 1:
                    return set(_ANYBYTE)  # any byte: sound for 1-byte results
                raise DecompileError("value-set product too large at block $%04X" % key[0])
            out = set()
            widths = [E.width(c) for c in n[2]]

            def rec(i, vals):
                if i == len(sets):
                    out.add(E._apply(n[1], vals, widths, n[3]))
                    return
                for v in sets[i]:
                    rec(i + 1, vals + [v])

            rec(0, [])
            return out
        raise DecompileError("unresolvable value form %r" % (n[0],))

    def _table_load(self, aexpr, key, depth):
        """A computed load resolves to table bytes: precise address set when
        provable, else the interval bound if small and wholly immutable."""
        model = self.model
        try:
            addrs = self._resolve(aexpr, key, depth + 1)
        except DecompileError:
            ivals = self._ivals(aexpr, key, depth + 1)
            if sum(hi - lo + 1 for lo, hi in ivals) > 1024:
                raise DecompileError(  # pylint: disable=raise-missing-from
                    "computed load range too large at block $%04X" % key[0]
                )
            addrs = [a for lo, hi in ivals for a in range(lo, hi + 1)]
        out = set()
        for a in addrs:
            a &= 0xFFFF
            if a in _VOL or a in _VOL0:
                raise DecompileError("computed load may hit IO at block $%04X" % key[0])
            if a in model.written:
                out |= self._cell_set(a, key)
            else:
                out.add(model.mem0[a])
        return out

    def close(self, seeds):
        """Fixpoint value sets for ``seeds`` (auto-extending to referenced cells
        and to the register/load sets they depend on)."""
        for c in seeds:
            self.S.setdefault(c, set())
        changed = True
        sweeps = 0
        while changed:
            sweeps += 1
            if sweeps > 40:  # widening cap: unsettled entries pin to TOP (sound)
                for entry, cnt in self._widen.items():
                    if cnt > 2:
                        table = self.R if isinstance(entry, tuple) and entry in self.R else self.S
                        if entry in table:
                            table[entry] = TOP
                break
            changed = False
            self._r_grew = False
            self._memo = {}
            self._imemo = {}
            const, dyn = self._stores()
            for cell in list(self.S):
                try:
                    vals = {self.model.mem0[cell]}
                    for key, vexpr in const.get(cell, ()):
                        vals |= self._resolve(vexpr, key)
                    for key, rng, aexpr, vexpr in dyn:
                        if self._hits(key, rng, aexpr, cell):
                            vals |= self._resolve(vexpr, key)
                except _Need as need:
                    self.S.setdefault(need.cell, set())
                    changed = True
                    continue
                except DecompileError:
                    vals = TOP
                if vals is not TOP and len(vals) > 256:
                    vals = TOP
                changed = self._update(self.S, cell, vals) or changed
            try:
                changed = self._sweep_regs(self._pred_map()) or changed
            except _Need as need:
                self.S.setdefault(need.cell, set())
                changed = True
            changed = changed or self._r_grew
        return self.S

    # -- proven successor sets ---------------------------------------------
    def term_targets(self, blk):
        """Proven successor pcs of a block (excluding rts continuations)."""
        term = blk.term
        t = term[0]
        if t in ("goto", "jmp"):
            return [term[1]]
        if t == "br":
            if term[2] is not None:
                return [term[2], term[3]]
            return sorted(self._dyn_targets(blk, term[5])) + [term[3]]
        if t == "jmpd":
            return sorted(self._dyn_targets(blk, term[1]))
        if t == "jmpind":
            key = (blk.pc, blk.op0)
            ptrs = {term[1]} if term[1] is not None else self._resolve(term[2], key, budget=16384)
            out = set()
            for ptr in ptrs:
                los = self._cell_set(ptr, key)
                his = self._cell_set((ptr & 0xFF00) | ((ptr + 1) & 0xFF), key)
                if len(out) + len(los) * len(his) > 1024:
                    raise DecompileError("jmpind vector set too large at $%04X" % blk.pc)
                out |= {lo | (hi << 8) for lo in los for hi in his}
            return sorted(out)
        if t == "jsr":
            if term[1] is None:
                return sorted(self._dyn_targets(blk, term[3]))
            return [term[1]]
        return []

    def _dyn_targets(self, blk, ex):
        try:
            return set(self._resolve(ex, (blk.pc, blk.op0), budget=16384))
        except _Need as need:
            raise DecompileError(
                "control target at $%04X depends on unclosed cell $%04X" % (blk.pc, need.cell)
            ) from need

    # -- SP-constant forward flow -------------------------------------------
    def sp_flow(self):
        model = self.model
        work = []

        def push(pc, sp):
            for key in model.variants(pc):
                cur = self.sp_in.get(key)
                new = sp if cur is None or cur == sp else "bot"
                if cur != new:
                    self.sp_in[key] = new
                    work.append(key)

        for entry in (model.init, model.play):
            push(entry, 0xFD)
        while work:
            key = work.pop()
            blk = model.blocks[key]
            sp = self.sp_in[key]
            spo = _sp_eval(blk.regs[3], sp) if isinstance(sp, int) else None
            out = "bot" if spo is None else spo & 0xFF
            term = blk.term
            try:
                tgts = self.term_targets(blk)
            except (DecompileError, _Need):
                tgts = []
            for pc in tgts:
                push(pc, (out - 2) & 0xFF if term[0] == "jsr" and isinstance(out, int) else out)
            if term[0] == "jsr":
                push((term[2] + 1) & 0xFFFF, out)  # balanced-call return edge
        return self.sp_in

    def concretize_stack(self):
        """Replace SP-pure event address expressions with constants where the
        entry SP is proven constant (makes push/pull cells first-class)."""
        for key, blk in self.model.blocks.items():
            sp = self.sp_in.get(key)
            if not isinstance(sp, int):
                continue
            changed = False
            for j, ev in enumerate(blk.events):
                if ev[0] == "ld" and not E.is_const(ev[2]):
                    a = _sp_eval(ev[2], sp)
                    if a is not None:
                        blk.events[j] = ("ld", ev[1], E.konst(a & 0xFFFF, 2))
                        changed = True
                elif ev[0] == "st" and not E.is_const(ev[1]):
                    a = _sp_eval(ev[1], sp)
                    if a is not None:
                        blk.events[j] = ("st", E.konst(a & 0xFFFF, 2), ev[2])
                        changed = True
            if changed:
                blk.fn = None
        self._cell_stores = None

    # -- dominators ----------------------------------------------------------
    def dominators(self):
        model = self.model
        succ = {}
        for key, blk in model.blocks.items():
            outs = set()
            try:
                for pc in self.term_targets(blk):
                    outs.update(model.variants(pc))
            except (DecompileError, _Need):
                outs = set(model.blocks)  # unknown targets: admit no false dominance
            if blk.term[0] == "jsr":
                outs.update(model.variants((blk.term[2] + 1) & 0xFFFF))
            succ[key] = outs
        root = ("root", None)
        succ[root] = set()
        for entry in (model.init, model.play):
            succ[root].update(model.variants(entry))
        order = []
        seen = {root}
        stack = [(root, iter(succ[root]))]
        while stack:
            node, it = stack[-1]
            adv = next(it, None)
            if adv is None:
                order.append(node)
                stack.pop()
            elif adv not in seen:
                seen.add(adv)
                stack.append((adv, iter(succ.get(adv, ()))))
        order.reverse()
        rpo = {n: i for i, n in enumerate(order)}
        preds = {n: [] for n in order}
        for n in order:
            for s in succ.get(n, ()):
                if s in preds:
                    preds[s].append(n)
        idom = {root: root}
        changed = True
        while changed:
            changed = False
            for n in order:
                if n == root:
                    continue
                cands = [p for p in preds[n] if p in idom]
                if not cands:
                    continue
                new = cands[0]
                for p in cands[1:]:
                    a, b = new, p
                    while a != b:
                        while rpo[a] > rpo[b]:
                            a = idom[a]
                        while rpo[b] > rpo[a]:
                            b = idom[b]
                    new = a
                if idom.get(n) != new:
                    idom[n] = new
                    changed = True
        self.idom = idom
        return idom

    def _dominates(self, a, b):
        idom = self.idom
        if a not in idom or b not in idom:
            return False
        n = b
        while True:
            if n == a:
                return True
            p = idom[n]
            if p == n:
                return False
            n = p


def _pure(n):
    """Whether ``n`` is a pure function of registers and constants."""
    k = n[0]
    if k in ("const", "reg"):
        return True
    if k == "op":
        return all(_pure(c) for c in n[2])
    return False


def _pure_eval(n, i, v):
    k = n[0]
    if k == "const":
        return n[1]
    if k == "reg":
        return v if n[1] == i else 0
    vals = [_pure_eval(c, i, v) for c in n[2]]
    return E._apply(n[1], vals, [E.width(c) for c in n[2]], n[3])


def _sp_eval(n, sp):
    """Evaluate an expression that depends only on the entry SP, else None."""
    k = n[0]
    if k == "const":
        return n[1]
    if k == "reg":
        return sp if n[1] == 3 else None
    if k != "op":
        return None
    vals = []
    for c in n[2]:
        v = _sp_eval(c, sp)
        if v is None:
            return None
        vals.append(v)
    return E._apply(n[1], vals, [E.width(c) for c in n[2]], n[3])


def _close_once(model):
    """One closure round: control cells, proven dyn targets, SP flow, stack
    concretization, dominators, opcode-cell sets + variant blocks."""
    ana = Analysis(model)
    seeds = set()
    dyn_blocks = []
    for blk in list(model.blocks.values()):
        term = blk.term
        ex = None
        if term[0] == "br":
            ex = term[5]
        elif term[0] in ("jmpd", "jmpind", "jsr"):
            ex = term[-1]
        if term[0] == "jmpind" and ex is None:
            ptr = term[1]
            if ptr in model.written or (ptr + 1) & 0xFFFF in model.written:
                seeds.update((ptr, (ptr & 0xFF00) | ((ptr + 1) & 0xFF)))
                dyn_blocks.append(blk)
        if isinstance(ex, tuple):
            dyn_blocks.append(blk)
            for ev in blk.events:
                if ev[0] == "ld" and E.is_const(ev[2]) and _uses(ex, ev[1]):
                    seeds.add(ev[2][1])
    if seeds:
        ana.close(seeds)
    model.unproven = []
    targets_map = {}
    pending = list(dyn_blocks)
    for _round in range(8):
        needs = set()
        still = []
        for blk in pending:
            try:
                targets_map[blk] = ana.term_targets(blk)
            except _Need as need:
                needs.add(need.cell)
                still.append(blk)
            except DecompileError as exc:
                model.unproven.append("control at $%04X: %s" % (blk.pc, exc))
                targets_map[blk] = []
        if not needs:
            break
        ana.close(needs)
        pending = still
    for blk in dyn_blocks:  # materialize liftable members of the proven target sets
        for pc in targets_map.get(blk, ()):
            if pc in model.written:
                continue  # dispatch boundary: its variants come from cell closure
            op0 = next(iter(model.pcs[pc])) if pc in model.pcs else model.mem0[pc]
            try:
                model.build(pc, op0)
            except (DecompileError, NotImplementedError):
                pass  # over-approximated member: walker faults loudly if reached
    ana.sp_flow()
    ana.concretize_stack()
    ana.dominators()
    ana.stack_rule = True
    closed = {}
    if model.dispatch_pcs:
        sets = ana.close(set(model.dispatch_pcs))
        for pc in sorted(model.dispatch_pcs):
            closed[pc] = sets[pc]
            if sets[pc] is TOP or len(sets[pc]) > 32:
                continue  # verified (and failed) once the fixpoint stabilizes
            for op0 in sorted(sets[pc]):
                try:
                    model.build(pc, op0)
                except (DecompileError, NotImplementedError):
                    pass  # junk member (e.g. pre-generation byte): loud fault if selected
    model.analysis = ana
    return closed


def close_dispatch(model):
    """P4 driver: iterate closure and block materialization to a fixpoint, then
    verify every observed opcode byte lies in its proven set (else fail)."""
    closed = {}
    while True:
        materialize(model)
        n = len(model.blocks)
        closed = _close_once(model)
        materialize(model)
        if len(model.blocks) == n:
            break
    if model.unproven:
        raise DecompileError("unproven control targets: " + "; ".join(model.unproven))
    for pc in sorted(model.dispatch_pcs):
        observed = model.pcs[pc]
        vals = closed.get(pc, set())
        if vals is TOP or len(vals) > 32 or not observed <= vals:
            raise DecompileError(
                "opcode cell $%04X: observed %s outside proven set %s"
                % (
                    pc,
                    sorted(observed),
                    "TOP" if vals is TOP or len(vals) > 32 else sorted(vals),
                )
            )
        for op0 in observed:
            if (pc, op0) not in model.blocks:
                raise DecompileError("observed variant $%02X at $%04X failed to build" % (op0, pc))
    return closed


# ---- passes: flag liveness + single-use slot inlining -------------------------
def _regs_of(n, out):
    k = n[0]
    if k == "reg":
        out.add(n[1])
    elif k == "mem":
        _regs_of(n[1], out)
    elif k == "op":
        for c in n[2]:
            _regs_of(c, out)


def _block_exprs(blk):
    for ev in blk.events:
        if ev[0] == "ld":
            yield ev[2]
        elif ev[0] == "st":
            yield ev[1]
            yield ev[2]
        elif ev[0] == "pen":
            yield ev[2]
            yield ev[3]
    term = blk.term
    if term[0] == "br":
        yield term[4]
        if term[5] is not None:
            yield term[5]
    elif term[0] == "jmpd":
        yield term[1]
    elif term[0] in ("jmpind", "jsr") and term[-1] is not None:
        yield term[-1]


def _successors(model, blk):
    term = blk.term
    if term[0] == "goto" or term[0] == "jmp":
        succs, unknown = [term[1]], False
    elif term[0] == "br":
        if term[2] is None:
            succs, unknown = [term[3]], True
        else:
            succs, unknown = [term[2], term[3]], False
    elif term[0] == "jsr":
        if term[1] is None:
            succs, unknown = [], True
        else:
            succs, unknown = [term[1]], False
    else:  # rts / jmpd / jmpind: continuation unknown -> all live
        succs, unknown = [], True
    unknown = unknown or any(not model.variants(pc) for pc in succs)
    return succs, unknown


def materialize(model):
    """Build every transitively reachable static-successor block, so the
    liveness graph and the emitted text cover the whole program."""
    work = list(model.blocks.values())
    while work:
        term = work.pop().term
        if term[0] in ("goto", "jmp"):
            targets = [term[1]]
        elif term[0] == "br":
            targets = [term[3]] + ([term[2]] if term[2] is not None else [])
        elif term[0] == "jsr":
            targets = [(term[2] + 1) & 0xFFFF] + ([term[1]] if term[1] is not None else [])
        else:
            continue
        for pc in targets:
            if pc in model.written or model.variants(pc):
                continue
            op0 = next(iter(model.pcs[pc])) if pc in model.pcs else model.mem0[pc]
            try:
                work.append(model.build(pc, op0))
            except (DecompileError, NotImplementedError):
                pass  # unreachable junk continuation: walker faults if reached


def prune_dead_flags(model):
    """Backward liveness over the block graph; dead register out-exprs become
    identity. Unknown continuations (rts, computed targets) keep all live."""
    blocks = model.blocks
    use = {}
    for key, blk in blocks.items():
        u = set()
        for ex in _block_exprs(blk):
            _regs_of(ex, u)
        use[key] = u

    def live_out(blk):
        succs, unknown = _successors(model, blk)
        out = set(range(16)) if unknown else set()
        for spc in succs:
            for skey in model.variants(spc):
                out |= live_in[skey]
        return out

    live_in = {key: set(use[key]) for key in blocks}
    changed = True
    while changed:
        changed = False
        for key, blk in blocks.items():
            need = set(use[key])
            for i in live_out(blk):
                _regs_of(blk.regs[i], need)
            if not need <= live_in[key]:
                live_in[key] |= need
                changed = True
    for blk in blocks.values():
        out = live_out(blk)
        blk.regs = [blk.regs[i] if i in out else E.reg(i) for i in range(16)]
        blk.fn = None


def _subst(n, uni_n, repl):
    k = n[0]
    if k == "uni" and n[1] == uni_n:
        return repl
    if k == "mem":
        return ("mem", _subst(n[1], uni_n, repl), n[2])
    if k == "op":
        return ("op", n[1], tuple(_subst(c, uni_n, repl) for c in n[2]), n[3])
    return n


def _uses(n, uni_n):
    k = n[0]
    if k == "uni":
        return 1 if n[1] == uni_n else 0
    if k == "mem":
        return _uses(n[1], uni_n)
    if k == "op":
        return sum(_uses(c, uni_n) for c in n[2])
    return 0


def _event_uses(e2, n):
    if e2[0] == "ld":
        return _uses(e2[2], n)
    if e2[0] == "st":
        return _uses(e2[1], n) + _uses(e2[2], n)
    if e2[0] == "pen":
        return _uses(e2[2], n) + _uses(e2[3], n)
    return 0


def _subst_events(blk, n, repl):
    for j, e2 in enumerate(blk.events):
        if e2[0] == "ld":
            blk.events[j] = (e2[0], e2[1], _subst(e2[2], n, repl))
        elif e2[0] == "st":
            blk.events[j] = (e2[0], _subst(e2[1], n, repl), _subst(e2[2], n, repl))
        elif e2[0] == "pen":
            blk.events[j] = (e2[0], e2[1], _subst(e2[2], n, repl), _subst(e2[3], n, repl))
    blk.term = tuple(_subst(x, n, repl) if isinstance(x, tuple) else x for x in blk.term)
    blk.regs = [_subst(x, n, repl) for x in blk.regs]


def inline_slots(blk):
    """Replace a const-address non-volatile slot with a direct read at its use
    sites when no store between the load and the last use can touch the cell."""
    changed = True
    while changed:
        changed = False
        for i, ev in enumerate(blk.events):
            if ev[0] != "ld" or not E.is_const(ev[2]):
                continue
            a = ev[2][1]
            if a in _VOL or a in _VOL0:
                continue
            n = ev[1]
            use_idx = [j for j, e2 in enumerate(blk.events) if j != i and _event_uses(e2, n)]
            tail = sum(_uses(x, n) for x in blk.term if isinstance(x, tuple)) + sum(
                _uses(x, n) for x in blk.regs
            )
            span_end = len(blk.events) if tail else (use_idx[-1] if use_idx else i)
            span = blk.events[i + 1 : span_end]
            if any(e2[0] == "st" and not (E.is_const(e2[1]) and e2[1][1] != a) for e2 in span):
                continue
            _subst_events(blk, n, ("mem", E.konst(a, 2), 1))
            del blk.events[i]
            blk.fn = None
            changed = True
            break


# ---- model + walker -----------------------------------------------------------
class Model:
    """Decompiled program: block variants over an initial image (standalone)."""

    def __init__(self, mem0, init, play, evidence):
        self.mem0 = bytes(mem0)
        self.init = init
        self.play = play
        # stack page always mutable: jsr/rts traffic bypasses _wr in PcodeVM.step
        self.written = frozenset(evidence.written) | frozenset(range(0x100, 0x200))
        self.pcs = evidence.pcs
        self.leaders = set(evidence.leaders)
        self.dispatch_pcs = {pc for pc in evidence.pcs if pc in evidence.written}
        self.dispatch_sets = {}
        self.blocks = {}
        self.analysis = None
        self.unproven = []
        self._by_pc = {}

    def build(self, pc, op0):
        key = (pc, op0)
        blk = self.blocks.get(key)
        if blk is None:
            blk = _BlockBuilder(self, pc, op0).block
            self.blocks[key] = blk
            self._by_pc.setdefault(pc, []).append(key)
        return blk

    def variants(self, pc):
        return self._by_pc.get(pc, ())

    def build_all(self):
        for pc in sorted(self.leaders | self.dispatch_pcs):
            for op0 in sorted(self.pcs.get(pc, ())):
                self.build(pc, op0)
        self.dispatch_sets = close_dispatch(self)
        prune_dead_flags(self)
        for blk in self.blocks.values():
            inline_slots(blk)
        return self

    def lookup(self, pc, m):
        if pc in self.written:
            key = (pc, m[pc])
            blk = self.blocks.get(key)
            if blk is None:
                raise WalkError("opcode $%02X at $%04X outside proven set" % (key[1], pc))
        else:
            ops = self.pcs.get(pc)
            key = (pc, next(iter(ops)) if ops is not None else self.mem0[pc])
            blk = self.blocks.get(key)
            if blk is None:
                blk = self.build(*key)  # static continuation: same static info
        if blk.fn is None:
            blk.fn = compile_block(blk)
        return blk


def decompile(mem, init, play, frames):
    """Full-length evidence trace + closed, passed model: ``(model, evidence)``."""
    ev = trace(bytearray(mem), init, play, frames)
    model = Model(mem, init, play, ev).build_all()
    return model, ev


class Walker:
    """Executes a model from its initial image alone, producing the
    cycle-stamped ``(cycle, reg, value)`` SID write log."""

    def __init__(self, model):
        self.model = model
        self.m = bytearray(model.mem0)
        self.r = [0] * 16
        self.r[3] = 0xFF
        self.c = 0
        self.wlog = []

    def _push(self, val):
        self.m[0x100 + self.r[3]] = val & 0xFF
        self.r[3] = (self.r[3] - 1) & 0xFF

    def _run_entry(self, entry):
        model = self.model
        m = self.m
        start = self.r[3]
        self._push(0x00)
        self._push(0x01)
        pc = entry
        n = 0
        while self.r[3] < start:
            blk = model.lookup(pc, m)
            self.c, self.r, x = blk.fn(
                m, self.r, self.c, self.wlog, volatile_read, _dyn_read, _sid_log
            )
            term = blk.term
            kind = term[0]
            if kind == "goto" or kind == "jmp":
                pc = term[1]
            elif kind == "br":
                _, pol, tgt, ft, _f, dynx = term
                flag, xtgt = x
                if dynx is not None:
                    tgt = xtgt
                if flag == pol:
                    self.c += 1 + ((ft & 0xFF00) != (tgt & 0xFF00))
                    pc = tgt
                else:
                    pc = ft
            elif kind == "jmpd":
                pc = x
            elif kind == "jmpind":
                ptr = term[1] if term[2] is None else x
                pc = m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)
            elif kind == "jsr":
                _, tgt, ret, dynx = term
                self._push(ret >> 8)
                self._push(ret & 0xFF)
                pc = tgt if dynx is None else x
            else:  # rts
                sp = (self.r[3] + 1) & 0xFF
                lo = m[0x100 + sp]
                sp = (sp + 1) & 0xFF
                hi = m[0x100 + sp]
                self.r[3] = sp
                pc = ((hi << 8) | lo) + 1 & 0xFFFF
            n += 1
            if n > _GUARD:
                raise WalkError("runaway at %04X" % pc)

    def run(self, frames):
        self._run_entry(self.model.init)
        for _ in range(frames):
            self._run_entry(self.model.play)
        return self.wlog
