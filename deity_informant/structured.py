"""Structured-decompiler prototype (docs/decompiler-plan.md P1-P3).

Evidence trace -> per-pc blocks (opcode-SMC sites become per-byte variants) ->
compiled symbolic summaries with machine-order loads and cycle/penalty events
-> standalone walker reproducing the cycle-stamped write log bit-exact.
"""

from __future__ import annotations

from . import expr as E
from .lifter import lift
from .vm import PcodeVM

_VOL = frozenset((0xD011, 0xD012, 0xD41B, 0xD41C))
_SIDE_EFFECT_VOL = frozenset((0xD019, 0xDC0D))
_GUARD = 8_000_000
_BLOCK_CAP = 64


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
    if a in _SIDE_EFFECT_VOL:
        raise WalkError("read of side-effecting IO $%04X not modeled" % a)
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
    """Executed instruction identities, written cells, and the oracle log."""

    def __init__(self, pcs, written, wlog, end_mem, end_reg):
        self.pcs = pcs  # {pc: set(opcode bytes executed there)}
        self.written = written
        self.wlog = wlog
        self.end_mem = end_mem
        self.end_reg = end_reg


def trace(mem, init, play, frames):
    """Run init + ``frames`` play calls concretely, recording evidence."""
    vm = _EvidenceVM(mem)
    vm.wlog = []
    pcs = {}
    cache = {}
    reg = vm.reg

    def run_entry(entry):
        start = reg[3]
        vm._push(0x00)
        vm._push(0x01)
        pc = entry
        n = 0
        while reg[3] < start:
            pcs.setdefault(pc, set()).add(vm.mem[pc])
            pc = vm.step(pc, cache, lift)
            n += 1
            if n > _GUARD:
                raise RuntimeError("runaway at %04X" % pc)

    run_entry(init)
    for _ in range(frames):
        run_entry(play)
    return Evidence(pcs, vm.written, vm.wlog, bytes(vm.mem), list(vm.reg))


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


# ---- P2+P3: block construction (symbolic summary emitted as python source) ----
class _BlockBuilder:
    """Symbolically executes instructions from ``entry`` to a terminator,
    emitting machine-order python source (loads/stores/cycle/penalty events)."""

    def __init__(self, model, entry, op0):
        self.model = model
        self.lines = []
        self.nuni = 0
        self.sreg = [E.reg(i) for i in range(16)]
        self.suni = {}
        self.term = None
        self.entry = entry
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
            if pc in model.dispatch_pcs or len(self.pcs) >= _BLOCK_CAP:
                self.term = ("goto", pc)
                break

    # -- machine-order slot allocation ------------------------------------
    def _slot(self, addr_expr):
        n = self.nuni
        self.nuni += 1
        if E.is_const(addr_expr):
            a = addr_expr[1]
            if a in _SIDE_EFFECT_VOL:
                raise DecompileError("read of $%04X (side-effecting IO)" % a)
            if a in _VOL:
                self.lines.append("t%d = vol(m, %s, c)" % (n, hex(a)))
            else:
                self.lines.append("t%d = m[%s]" % (n, hex(a)))
        else:
            self.lines.append("t%d = dyn(m, %s, c)" % (n, _src(addr_expr)))
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

    # -- one instruction ---------------------------------------------------
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
                if E.is_const(addr):
                    a = addr[1]
                    self.lines.append("m[%s] = %s" % (hex(a), _src(val)))
                    if 0xD400 <= a <= 0xD418:
                        self.lines.append("w.append((c, %d, m[%s]))" % (a - 0xD400, hex(a)))
                else:
                    self.lines.append("a = %s" % _src(addr))
                    self.lines.append("v = %s" % _src(val))
                    self.lines.append("m[a] = v")
                    self.lines.append("_sid(a, v, c, w)")
                continue
            if mn == "LOAD":
                assert out[2] == 1, rec
                self._set(out, self._slot(self._sval(ins[0], i, 0, pmap, pc)))
                continue
            svals = [self._sval(vn, i, j, pmap, pc) for j, vn in enumerate(ins)]
            self._set(out, svals[0] if mn == "COPY" else E.op(mn, svals, out[2]))
        self.lines.append("c += %d" % rec["cyc"])
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
        if kind == "iy":
            zp = self._operand_expr(pc, [1], "id")
            if E.is_const(zp):
                z = zp[1]
                self.lines.append("b = m[%s] | (m[%s] << 8)" % (hex(z), hex((z + 1) & 0xFF)))
            else:
                self.lines.append("z = %s" % _src(zp))
                self.lines.append("b = m[z] | (m[(z + 1) & 0xFF] << 8)")
        else:  # ax / ay: absolute base from the operand word
            base = self._operand_expr(pc, [1, 2], "word")
            self.lines.append("b = %s" % _src(base))
        self.lines.append("c += (b & 0xFF00) != ((b + %s) & 0xFF00)" % _src(idx))

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
            self.term = ("br", ctrl[2], ctrl[3], ctrl[4], dyn is not None)
            self.lines.append("x = (%s, %s)" % (_src(flag), _src(dyn) if dyn else "None"))
        elif kind == "jmp":
            self.term = ("jmp", ctrl[1], dyn is not None)
            if dyn is not None:
                self.lines.append("x = %s" % _src(dyn))
        elif kind == "jmpind":
            self.term = ("jmpind", ctrl[1], dyn is not None)
            if dyn is not None:
                self.lines.append("x = %s" % _src(dyn))
        elif kind == "jsr":
            self.term = ("jsr", ctrl[1], (pc + rec["len"] - 1) & 0xFFFF, dyn is not None)
            if dyn is not None:
                self.lines.append("x = %s" % _src(dyn))
        elif kind == "rts":
            self.term = ("rts",)
        else:
            raise DecompileError("control %r at %04X not modeled" % (kind, pc))

    def compile(self):
        body = list(self.lines) or ["pass"]
        if not any(l.startswith("x = ") for l in body):
            body.append("x = None")
        regs = "[%s]" % ", ".join(_src(ex) for ex in self.sreg)
        src = "def _f(m, r, c, w, vol, dyn, _sid):\n    %s\n    return c, %s, x\n" % (
            "\n    ".join(body),
            regs,
        )
        ns = {}
        exec(src, ns)  # noqa: S102 - generated from the block summary, no user input
        return ns["_f"], src


def _sid_log(a, v, c, w):
    if 0xD400 <= a <= 0xD418:
        w.append((c, a - 0xD400, v))


class Model:
    """Decompiled program: block variants over an initial image (standalone)."""

    def __init__(self, mem0, init, play, evidence):
        self.mem0 = bytes(mem0)
        self.init = init
        self.play = play
        # stack page always mutable: jsr/rts traffic bypasses _wr in PcodeVM.step
        self.written = frozenset(evidence.written) | frozenset(range(0x100, 0x200))
        self.pcs = evidence.pcs
        # a pc whose opcode cell is ever written dispatches on the live byte
        self.dispatch_pcs = {pc for pc in evidence.pcs if pc in evidence.written}
        self._blocks = {}

    def block(self, pc, op0):
        key = (pc, op0)
        b = self._blocks.get(key)
        if b is None:
            if pc not in self.pcs:
                raise WalkError("pc $%04X outside decompiled program" % pc)
            if op0 not in self.pcs[pc]:
                raise WalkError("opcode $%02X at $%04X outside evidence" % (op0, pc))
            builder = _BlockBuilder(self, pc, op0)
            fn, src = builder.compile()
            b = (fn, builder.term, src, builder.pcs)
            self._blocks[key] = b
        return b

    def build_all(self):
        for pc, ops in sorted(self.pcs.items()):
            for op0 in sorted(ops):
                self.block(pc, op0)
        return self._blocks


def decompile(mem, init, play, frames):
    """Full-length evidence trace + model; returns ``(model, evidence)``."""
    ev = trace(bytearray(mem), init, play, frames)
    return Model(mem, init, play, ev), ev


# ---- standalone walker --------------------------------------------------------
class Walker:
    """Executes a :class:`Model` from its initial image alone, producing the
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
            if pc in model.dispatch_pcs:
                op0 = m[pc]
            else:
                ops = model.pcs.get(pc)
                if ops is None:
                    raise WalkError("pc $%04X outside decompiled program" % pc)
                op0 = next(iter(ops))
            fn, term, _s, _p = model.block(pc, op0)
            self.c, self.r, x = fn(m, self.r, self.c, self.wlog, volatile_read, _dyn_read, _sid_log)
            kind = term[0]
            if kind == "goto":
                pc = term[1]
            elif kind == "br":
                _, pol, tgt, ft, dynt = term
                flag, xtgt = x
                if dynt:
                    tgt = xtgt
                if flag == pol:
                    self.c += 1 + ((ft & 0xFF00) != (tgt & 0xFF00))
                    pc = tgt
                else:
                    pc = ft
            elif kind == "jmp":
                pc = x if term[2] else term[1]
            elif kind == "jmpind":
                ptr = x if term[2] else term[1]
                pc = m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)
            elif kind == "jsr":
                _, tgt, ret, dynt = term
                self._push(ret >> 8)
                self._push(ret & 0xFF)
                pc = x if dynt else tgt
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


def dump(model):
    """Readable listing of all block summaries (P6 grows this into the full
    parseable language; the prototype emits an inspection listing)."""
    blocks = model.build_all()
    out = []
    for (pc, op0), (_fn, term, src, pcs) in sorted(blocks.items()):
        head = "block $%04X" % pc
        if pc in model.dispatch_pcs:
            head += " when code[$%04X] == $%02X" % (pc, op0)
        out.append("%s  ; %d insns -> %s" % (head, len(pcs), term))
        body = src.split("\n", 1)[1].rsplit("\n    return", 1)[0]
        out.append(body)
    return "\n".join(out) + "\n"
