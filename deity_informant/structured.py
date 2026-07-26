"""Structured decompiler core (docs/decompiler-plan.md P1-P4).

Evidence trace -> per-pc blocks (opcode-SMC sites become per-byte variants) ->
compiled standalone walker. Every committed site set is the trace-observed set,
guarded at run time; static analysis only certifies guards dead, never widens.
"""

from __future__ import annotations

import hashlib
import itertools
from collections import namedtuple

from . import codec
from . import expr as E
from .lifter import MODE_LEN, OPS, lift
from .vm import PcodeVM

Proof = namedtuple("Proof", "site kind status targets lemma")
"""Per-site certification record. ``targets`` is always the serialized
(trace-observed) set. ``status`` is ``certified`` (the static set equals the
observed set: the runtime guard is provably dead; ``lemma`` carries the
derivation) or ``observed`` (guard live; ``lemma`` names why static did not
certify — a refusal diagnostic or a wider static set)."""

Closure = namedtuple("Closure", "recur first window cap diag note")
"""Run-to-recurrence result. ``recur``/``first``: the frame whose entry state
exactly matched an earlier frame's and that earlier frame (both None without
recurrence within ``cap`` total frames); ``window`` is the evidence window;
``diag`` lists still-changing cells ``(addr, at window, at cap)``."""

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
        self.reads = set()
        self.pc = 0

    def _rd(self, addr, sz):
        self.reads.add((self.pc, addr & 0xFFFF))
        if sz > 1:
            self.reads.add((self.pc, (addr + 1) & 0xFFFF))
        return super()._rd(addr, sz)

    def _wr(self, addr, val, sz):
        for i in range(sz):
            self.written.add((addr + i) & 0xFFFF)
        super()._wr(addr, val, sz)


class Evidence:
    """Play-phase executed identities, leaders, written cells, oracle log;
    init ran concretely into the ``mem0`` snapshot + SID-write ``prologue``."""

    def __init__(
        self,
        pcs,
        leaders,
        targets,
        written,
        wlog,
        end_mem,
        end_reg,
        prologue,
        mem0,
        reads=(),
        closure=None,
    ):
        self.pcs = pcs  # {pc: set(opcode bytes executed there)}
        self.leaders = leaders
        self.targets = targets  # {pc: set(taken successor pc)} at transfer sites
        self.written = written
        self.reads = frozenset(reads)  # play-phase (site pc, read address) pairs
        self.wlog = wlog
        self.end_mem = end_mem
        self.end_reg = end_reg
        self.prologue = prologue  # [(reg, val)] order-preserved, timing non-normative
        self.mem0 = mem0  # post-init snapshot: the play program's initial image
        self.closure = closure  # run-to-recurrence Closure record (None: scan off)


def _frame_digest(vm, cells):
    """16-byte digest of the frame-entry play state: the play-written cells,
    the stack page, and the registers (cycle counter excluded; the exclusion
    is justified in docs/soundness.md, closure certifier)."""
    h = hashlib.blake2b(digest_size=16)
    h.update(bytes(a & 0xFF for a in cells))
    h.update(bytes(a >> 8 for a in cells))
    h.update(bytes(map(vm.mem.__getitem__, cells)))
    h.update(vm.mem[0x100:0x200])
    h.update(bytes(v & 0xFF for v in vm.reg))
    return h.digest()


def trace(mem, init, play, frames, subtune=0, cap=0):
    """Run init concretely to the play boundary, then record play-phase
    evidence over ``frames`` play calls from the post-init snapshot.

    ``subtune`` (0-based) is passed to init in A. The cycle counter (and so
    the volatile-read model) is zeroed at the boundary. ``cap`` > 0 keeps
    playing past ``frames`` until the frame-entry state recurs (or ``cap``
    total frames), recording the run-to-recurrence ``Closure``.
    """
    vm = _EvidenceVM(mem)
    vm.wlog = []
    pcs = {}
    leaders = {init, play}
    targets = {}
    cache = {}
    reg = vm.reg

    def run_entry(entry, acc=0):
        start = reg[3]
        reg[0] = acc & 0xFF
        vm._push(0x00)
        vm._push(0x01)
        pc = entry
        n = 0
        while reg[3] < start:
            op = vm.mem[pc]
            pcs.setdefault(pc, set()).add(op)
            vm.pc = pc
            key = (pc, op, vm.mem[(pc + 1) & 0xFFFF], vm.mem[(pc + 2) & 0xFFFF])
            nxt = vm.step(pc, cache, lift)
            kind = cache[key]["ctrl"][0]
            fall = (pc + MODE_LEN[OPS[op][1]]) & 0xFFFF
            # a transfer landing on its own fallthrough is still an observed edge
            if kind != "next" and (kind != "br" or nxt != fall):
                leaders.add(nxt)
                targets.setdefault(pc, set()).add(nxt)
            pc = nxt
            n += 1
            if n > _GUARD:
                raise RuntimeError("runaway at %04X" % pc)

    run_entry(init, subtune)
    prologue = [(r, v) for _c, r, v in vm.wlog]
    mem0 = bytes(vm.mem)
    pcs.clear()
    targets.clear()
    leaders.clear()
    leaders.add(play)
    vm.written.clear()
    vm.reads.clear()
    vm.wlog = []
    vm.cycles = 0
    seen, cells = {}, []
    first = recur = None
    note, boundary, f = "", None, 0
    while f < frames or (cap and recur is None and not note and f < cap):
        if cap and recur is None and f < cap:
            if len(cells) != len(vm.written):
                cells = sorted(vm.written)
            prev = seen.setdefault(_frame_digest(vm, cells), f)
            if prev != f:
                first, recur = prev, f
        try:
            run_entry(play)
        except (KeyError, RuntimeError) as exc:
            if f < frames:
                raise
            note = "play faulted at frame %d: %s" % (f, exc)
        f += 1
        if f == frames:
            boundary = (len(vm.wlog), bytes(vm.mem), list(vm.reg))
    if boundary is None:
        boundary = (len(vm.wlog), bytes(vm.mem), list(vm.reg))
    closure = None
    if cap:
        diag = ()
        if recur is None:
            note = note or "no recurrence within %d frames" % cap
            diag = tuple(
                (a, boundary[1][a], vm.mem[a])
                for a in sorted(vm.written)
                if vm.mem[a] != boundary[1][a]
            )
        closure = Closure(recur, first, frames, cap, diag, note)
    wlen, end_mem, end_reg = boundary
    del vm.wlog[wlen:]
    return Evidence(
        pcs,
        leaders,
        targets,
        vm.written,
        vm.wlog,
        end_mem,
        end_reg,
        prologue,
        mem0,
        vm.reads,
        closure,
    )


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
            if pc in model.written or pc in model.cut or len(self.pcs) >= _BLOCK_CAP:
                self.term = ("goto", pc)  # dispatch boundary or another block's entry
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


def _wide_ivals(ivals, aexpr):
    m = E.mask(E.width(aexpr))
    return any(lo <= 0 and hi >= m for lo, hi in ivals)


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
        self._opt = set()
        self._forced = set()
        self._pinned = {}  # cell -> affine-proven value set (not recomputed/widened)
        self._sidom = None  # cached leader-split immediate dominators (blocks are fixed)
        self._pair_memo = {}
        self._pair_active = set()
        self.pair_enabled = True
        self.derivations = {}  # site -> paired-index proof derivation (proven sites)
        self.pair_refusals = {}  # site -> paired-index refusal diagnostic

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
        ivals = self._ivals(aexpr, key)
        if _wide_ivals(ivals, aexpr):
            sid_ = (key, aexpr)
            if sid_ not in self._forced:
                self._opt.add(sid_)  # optimistic: defer until validated post-fixpoint
                return False
            return True
        return any(lo <= cell <= hi for lo, hi in ivals)

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
        ``p``'s branch condition where the taken edge constrains them."""
        blk = self.model.blocks[p]
        out = blk.regs[i]
        term = blk.term
        if term[0] == "br" and term[2] is not None:
            tgt, ft = term[2], term[3]
            pc = key[0]
            if (pc == tgt) != (pc == ft):
                want = term[1] if pc == tgt else 1 - term[1]
                got = self._edge_filter(p, term[4], out, want)
                if got is not None:
                    return got
        return self._resolve(out, p)

    def _edge_filter(self, p, flag, out, want):
        """``{out(v) : flag(v) == want}`` when both expressions are functions of
        one shared variable (a register or a block-local slot), else None."""
        varset = set()
        _leaf_vars(flag, varset)
        _leaf_vars(out, varset)
        if len(varset) != 1:
            return None
        var = next(iter(varset))
        try:
            if var[0] == "reg":
                dom = self._reg_set(p, var[1])
            else:
                dom = self._resolve(E.uni(var[1], 1), p)
        except DecompileError:
            dom = set(_ANYBYTE)  # the filter itself still bounds the result
        model = self.model
        try:
            return {_eval1(out, var, v, model) for v in dom if _eval1(flag, var, v, model) == want}
        except _NotPure:
            return None

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
        """Fixpoint value sets for ``seeds``: optimistic rounds (full-range
        stores deferred) each validated; stores that never narrow re-enter
        pessimistically and the fixpoint reruns."""
        for c in seeds:
            self.S.setdefault(c, set())
        while True:
            self._fixpoint()
            self._memo = {}
            self._imemo = {}
            self._pair_memo = {}
            if self._refine_affine():
                continue  # a monotone cell narrowed from TOP: re-close with it pinned
            pending = []
            for s in self._opt:
                if s in self._forced:
                    continue
                try:
                    wide = _wide_ivals(self._ivals(s[1], s[0]), s[1])
                except _Need as need:
                    self.S.setdefault(need.cell, set())
                    wide = True  # unclosed cell: treat as unresolved (pessimistic)
                if wide:
                    pending.append(s)
            if not pending:
                return self.S
            self._forced.update(pending)

    def _refine_affine(self):
        """Narrow TOP monotone-increment cells to their affine trip bound and pin
        them (§4 lemma 2). Returns whether any cell narrowed."""
        if not self.idom:
            return False
        changed = False
        for cell in list(self.S):
            if cell in self._pinned or self.S[cell] is not TOP:
                continue
            try:
                bound = self._affine_cell_bound(cell)
            except (DecompileError, _Need):
                bound = None
            if bound is None:
                continue
            saved = self.S[cell]  # tentatively pin, then discharge the alias premise
            self._pinned[cell] = set(bound)
            self.S[cell] = set(bound)
            self._memo = {}
            self._imemo = {}
            self._pair_memo = {}
            if self._cell_aliased(cell):  # a computed store may write it under the bound
                del self._pinned[cell]
                self.S[cell] = saved
                self._memo = {}
                self._imemo = {}
                self._pair_memo = {}
                continue
            self._widen.pop(cell, None)
            changed = True
        return changed

    def _cell_aliased(self, cell):
        for skey, rng, aexpr, _v in self._stores()[1]:
            if rng[0] <= cell <= rng[1] and self._store_may_hit(skey, aexpr, cell):
                return True
        return False

    def _fixpoint(self):
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
            self._pair_memo = {}
            const, dyn = self._stores()
            for cell in list(self.S):
                if cell in self._pinned:
                    continue  # affine-proven bound: not recomputed (would re-widen)
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
            sole = len(ptrs) == 1
            for ptr in sorted(ptrs):
                cells = (ptr, (ptr & 0xFF00) | ((ptr + 1) & 0xFF))
                if self.pair_enabled and any(c in self.model.written for c in cells):
                    try:
                        out |= self._paired_targets(blk, cells[0], cells[1], sole)
                        continue
                    except DecompileError:
                        pass  # refusal recorded per site; per-cell closure below
                los = self._cell_set(cells[0], key)
                his = self._cell_set(cells[1], key)
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
        """Paired-index lemma first (its refusals roll back cleanly), then
        per-cell resolution, then relational closure; refusals chain."""
        pexc = None
        cells = self._operand_cells(blk, ex) if self.pair_enabled else None
        if cells is not None:
            try:
                return self._paired_targets(blk, *cells)
            except DecompileError as exc:
                pexc = exc
        try:
            return set(self._resolve(ex, (blk.pc, blk.op0), budget=16384))
        except DecompileError:
            try:
                return self._relational_targets(blk, ex)  # sound joint-index closure
            except DecompileError as exc:
                if pexc is not None:
                    raise DecompileError("%s; %s" % (exc, pexc)) from exc
                raise

    # -- relational (joint-index) closure of self-modified vector targets ----
    def _relational_targets(self, blk, ex):
        """Prove a computed target set by substituting each self-modified operand
        cell's unique in-block store, then jointly enumerating the shared index
        registers over their value sets (correlated, not the Cartesian per-cell
        product). Sound: register sets over-approximate and the substituted table
        reads are immutable; raises a precise diagnostic where either fails."""
        key = (blk.pc, blk.op0)
        live = self._live_expr(blk, ex)
        regs = set()
        _regs_of(live, regs)
        doms = []
        for i in sorted(regs):
            if self.R.get((key, i)) is TOP:
                raise DecompileError("relational index reg r%d is TOP at $%04X" % (i, blk.pc))
            dom = self._reg_set(key, i)
            if not dom or len(dom) > 256:
                raise DecompileError(
                    "relational index reg r%d unbounded (%d) at $%04X" % (i, len(dom), blk.pc)
                )
            doms.append((i, sorted(dom)))
        total = 1
        for _, d in doms:
            total *= len(d)
        if total > 4096:
            raise DecompileError("relational domain too large (%d) at $%04X" % (total, blk.pc))
        idx = [i for i, _ in doms]
        out = set()
        for combo in itertools.product(*[d for _, d in doms]):
            out.add(self._eval_live(live, dict(zip(idx, combo))) & 0xFFFF)
        return out

    def _unique_store(self, blk, cell):
        model = self.model
        hits = [
            (bkey, ev[2])
            for bkey, b in model.blocks.items()
            for ev in b.events
            if ev[0] == "st" and E.is_const(ev[1]) and ev[1][1] == cell
        ]
        for skey, rng, aexpr, _v in self._stores()[1]:
            if rng[0] <= cell <= rng[1] and self._store_may_hit(skey, aexpr, cell):
                raise DecompileError("cell $%04X may be reached by a computed store" % cell)
        in_blk = [v for bkey, v in hits if bkey == (blk.pc, blk.op0)]
        if len(hits) != 1 or len(in_blk) != 1:
            raise DecompileError("cell $%04X is not a unique in-block store" % cell)
        return in_blk[0]

    def _store_may_hit(self, skey, aexpr, cell):
        """Whether a computed store at ``skey`` can reach ``cell`` (tight interval
        set; a bounded pointer that excludes the cell discharges the alias)."""
        for lo, hi in self._ivals(aexpr, skey):
            if lo <= cell <= hi:
                return True
        return False

    def _live_expr(self, blk, n, depth=0):
        if depth > 40:
            raise DecompileError("live expression too deep at $%04X" % blk.pc)
        k = n[0]
        if k in ("const", "reg"):
            return n
        if k == "uni":
            ld = next((e for e in blk.events if e[0] == "ld" and e[1] == n[1]), None)
            if ld is None:
                raise DecompileError("u%d has no load in block $%04X" % (n[1], blk.pc))
            addr = ld[2]
            if E.is_const(addr) and (addr[1] in _VOL or addr[1] in _VOL0):
                raise DecompileError("volatile load u%d in block $%04X" % (n[1], blk.pc))
            return ("mem", self._live_expr(blk, addr, depth + 1), n[2])
        if k == "mem":
            addr = n[1]
            if E.is_const(addr) and addr[1] in self.model.written:
                return self._live_expr(blk, self._unique_store(blk, addr[1]), depth + 1)
            return ("mem", self._live_expr(blk, addr, depth + 1), n[2])
        return ("op", n[1], tuple(self._live_expr(blk, c, depth + 1) for c in n[2]), n[3])

    def _eval_live(self, n, env):
        k = n[0]
        if k == "const":
            return n[1]
        if k == "reg":
            return env[n[1]]
        if k == "mem":
            a = self._eval_live(n[1], env) & 0xFFFF
            v = 0
            for j in range(n[2]):
                aj = (a + j) & 0xFFFF
                if aj in self.model.written or aj in _VOL or aj in _VOL0:
                    raise DecompileError("relational read hits mutable/IO cell $%04X" % aj)
                v |= self.model.mem0[aj] << (8 * j)
            return v
        vals = [self._eval_live(c, env) for c in n[2]]
        return E._apply(n[1], vals, [E.width(c) for c in n[2]], n[3])

    # -- paired-index closure: cross-block single-writer-pair lemma ----------
    def _operand_cells(self, blk, ex):
        """``(c_lo, c_hi)`` when ``ex`` is ``m[c_lo] | m[c_hi] << 8`` over
        constant operand cells, else ``None``."""
        if ex[0] != "op" or ex[1] != "INT_OR" or len(ex[2]) != 2:
            return None
        parts = []
        for kid in ex[2]:
            shift = 0
            if kid[0] == "op" and kid[1] == "INT_LEFT" and E.is_const(kid[2][1]):
                shift = kid[2][1][1]
                kid = kid[2][0]
            if kid[0] == "op" and kid[1] == "INT_ZEXT":
                kid = kid[2][0]
            cell = self._leaf_cell(blk, kid)
            if cell is None:
                return None
            parts.append((shift, cell))
        parts.sort()
        if [s for s, _c in parts] != [0, 8]:
            return None
        return parts[0][1], parts[1][1]

    def _leaf_cell(self, blk, n):
        """Constant address behind a byte leaf (uni load or mem), else None."""
        if n[0] == "uni":
            ld = next((e for e in blk.events if e[0] == "ld" and e[1] == n[1]), None)
            if ld is not None and E.is_const(ld[2]):
                return ld[2][1]
            return None
        if n[0] == "mem" and E.is_const(n[1]) and n[2] == 1:
            return n[1][1]
        return None

    @staticmethod
    def _uni_alias(blk):
        """uni -> canonical uni for repeated loads of one constant cell with no
        intervening store that may hit it (equal-value normalization)."""
        alias, first = {}, {}
        for ev in blk.events:
            if ev[0] == "st":
                if E.is_const(ev[1]):
                    first.pop(ev[1][1], None)
                else:
                    first.clear()
            elif ev[0] == "ld" and E.is_const(ev[2]):
                a = ev[2][1]
                if a in _VOL or a in _VOL0:
                    continue
                if a in first:
                    alias[ev[1]] = first[a]
                else:
                    first[a] = ev[1]
        return alias

    def _pair_live(self, blk, n, alias, unis, depth=0):
        """Writer-block live form: loads of written constant cells stay
        symbolic ``uni`` variables (``unis`` maps them to their source cell)."""
        if depth > 40:
            raise DecompileError("value expression too deep at writer $%04X" % blk.pc)
        k = n[0]
        if k in ("const", "reg"):
            return n
        if k == "uni":
            u = alias.get(n[1], n[1])
            ld = next((e for e in blk.events if e[0] == "ld" and e[1] == u), None)
            if ld is None:
                raise DecompileError("u%d has no load in writer $%04X" % (u, blk.pc))
            addr = ld[2]
            if E.is_const(addr):
                a = addr[1]
                if a in _VOL or a in _VOL0:
                    raise DecompileError("volatile load u%d in writer $%04X" % (u, blk.pc))
                if a in self.model.written:
                    unis[u] = a
                    return E.uni(u, n[2])
                return ("mem", addr, n[2])
            return ("mem", self._pair_live(blk, addr, alias, unis, depth + 1), n[2])
        if k == "mem":
            return ("mem", self._pair_live(blk, n[1], alias, unis, depth + 1), n[2])
        kids = tuple(self._pair_live(blk, c, alias, unis, depth + 1) for c in n[2])
        return ("op", n[1], kids, n[3])

    def _eval_pair(self, n, env):
        """Evaluate a writer live expression under ``env`` (reg/uni variables);
        refuses reads of mutable/IO cells (the table-immutability gate)."""
        k = n[0]
        if k == "const":
            return n[1]
        if k in ("reg", "uni"):
            return env[(k, n[1])]
        if k == "mem":
            a = self._eval_pair(n[1], env) & 0xFFFF
            v = 0
            for j in range(n[2]):
                aj = (a + j) & 0xFFFF
                if aj in self.model.written or aj in _VOL or aj in _VOL0:
                    raise DecompileError("table read hits mutable/IO cell $%04X" % aj)
                v |= self.model.mem0[aj] << (8 * j)
            return v
        vals = [self._eval_pair(c, env) for c in n[2]]
        return E._apply(n[1], vals, [E.width(c) for c in n[2]], n[3])

    def _paired_targets(self, blk, c_lo, c_hi, sole=True):
        """Memoized ``_paired_cell_targets``; refusals are recorded per site."""
        key = (blk.pc, blk.op0, c_lo, c_hi, sole)
        site = blk.pcs[-1]
        hit = self._pair_memo.get(key)
        if hit is not None:
            if isinstance(hit, DecompileError):
                raise hit
            return set(hit)
        if key in self._pair_active:  # pred-map recursion: refuse, don't record
            raise DecompileError("paired: recursive closure at $%04X" % blk.pc)
        self._pair_active.add(key)
        try:
            out = self._paired_cell_targets(blk, c_lo, c_hi, sole)
        except DecompileError as exc:
            self._pair_memo[key] = exc
            self.pair_refusals[site] = str(exc)
            self.derivations.get(site, {}).pop((c_lo, c_hi), None)
            raise
        finally:
            self._pair_active.discard(key)
        self._pair_memo[key] = frozenset(out)
        self.pair_refusals.pop(site, None)
        return out

    def _pair_writers(self, cells):
        """{writer block key: {cell: last stored expr}} over constant stores;
        refuses aliasing computed stores, stack cells, and unpaired writers."""
        model = self.model
        for c in cells:
            if 0x100 <= c <= 0x1FF:
                raise DecompileError("paired: operand cell $%04X is a stack cell" % c)
            if c in model.written and self._cell_aliased(c):
                raise DecompileError("paired: a computed store may reach cell $%04X" % c)
        writers = {}
        const, _dyn = self._stores()
        for c in cells:
            if c not in model.written:
                continue
            for key, vexpr in const.get(c, ()):  # event order: last store wins
                writers.setdefault(key, {})[c] = vexpr
        for key, pair in sorted(writers.items()):
            for c in cells:
                if c in model.written and c not in pair:
                    raise DecompileError(
                        "paired: writer block $%04X stores $%04X but not $%04X"
                        % (key[0], next(iter(pair)), c)
                    )
        return writers

    def _paired_cell_targets(self, blk, c_lo, c_hi, sole=True):
        """Targets of ``m[c_lo] | m[c_hi] << 8`` via the cross-block single-
        writer-pair lemma: each writer stores both cells in one state, so
        targets zip per writer over the shared index domain (never the
        per-cell Cartesian product), plus the post-init pair."""
        site = blk.pcs[-1]
        model = self.model
        writers = self._pair_writers((c_lo, c_hi))
        out = {model.mem0[c_lo] | (model.mem0[c_hi] << 8)}
        deriv = []
        for key, pair in sorted(writers.items()):
            wblk = model.blocks[key]
            alias = self._uni_alias(wblk)
            unis = {}
            lives = {
                c: (
                    E.konst(model.mem0[c], 1)
                    if pair.get(c) is None
                    else self._pair_live(wblk, pair[c], alias, unis)
                )
                for c in (c_lo, c_hi)
            }
            vlo, vhi = set(), set()
            _pair_vars(lives[c_lo], vlo)
            _pair_vars(lives[c_hi], vhi)
            if vlo and vhi and vlo != vhi:
                raise DecompileError("paired: writer $%04X indexes lo and hi differently" % key[0])
            vars_ = sorted(vlo | vhi)
            doms = self._pair_doms(key, vars_, unis)
            total = 1
            for d in doms:
                total *= len(d)
            if total > 4096:
                raise DecompileError(
                    "paired: index domain too large (%d) at writer $%04X" % (total, key[0])
                )
            try:
                for combo in itertools.product(*doms):
                    env = dict(zip(vars_, combo))
                    lo = self._eval_pair(lives[c_lo], env)
                    hi = self._eval_pair(lives[c_hi], env)
                    out.add((lo & 0xFF) | ((hi & 0xFF) << 8))
            except DecompileError as exc:
                raise DecompileError("paired: %s (writer $%04X)" % (exc, key[0])) from exc
            deriv.append("$%04X(|D|=%d)" % (key[0], total))
        obs = set(model.ev_targets.get(site, ()))
        if sole and not obs <= out:
            raise DecompileError(
                "paired: enumeration omits %d observed target(s) at $%04X" % (len(obs - out), site)
            )
        self.derivations.setdefault(site, {})[(c_lo, c_hi)] = (
            "paired-index: cells $%04X/$%04X; writers %s + post-init pair"
            % (c_lo, c_hi, " ".join(deriv) or "none")
        )
        return out

    def _pair_doms(self, key, vars_, unis):
        """Guard-refined value domains for a writer's index variables; refuses
        on TOP, empty, or over-wide domains (unswept registers fill on demand)."""
        doms = []
        for var in vars_:
            if var[0] == "reg":
                if self.R.get((key, var[1])) is TOP:
                    raise DecompileError(
                        "paired: index reg r%d is TOP at writer $%04X" % (var[1], key[0])
                    )
                dom = self._reg_set(key, var[1]) or self._demand_reg(key, var[1])
            else:
                dom = self._cell_set(unis[var[1]], key)
            if not dom or len(dom) > 256:
                raise DecompileError(
                    "paired: index %s%d unbounded (%d values) at writer $%04X"
                    % (var[0][0], var[1], len(dom), key[0])
                )
            doms.append(sorted(dom))
        return doms

    def _demand_reg(self, key, i):
        """Predecessor-contribution union for a register the demand-driven sweep
        has not visited yet; ``_Need`` propagates so the closure seeds the cells
        the sweep actually requires before the next retry."""
        vals = set()
        for p in self._pred_map().get(key, ()):
            if p == "cold":
                vals.add(0xFF if i == 3 else 0)
                continue
            try:
                vals |= self._pred_contrib(p, i, key)
            except DecompileError:
                return set()
        return vals if len(vals) <= 256 else set()

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

    # -- natural loops (lemma-2 foundation) ---------------------------------
    def _cfg_succ_keys(self, key):
        """Intraprocedural successor block keys (static control edges only)."""
        t = self.model.blocks[key].term
        if t[0] in ("goto", "jmp"):
            pcs = [t[1]]
        elif t[0] == "br":
            pcs = ([t[2]] if t[2] is not None else []) + ([t[3]] if t[3] is not None else [])
        elif t[0] == "jsr":
            pcs = [(t[2] + 1) & 0xFFFF]
        else:  # rts / jmpd / jmpind: continuation not a static intraprocedural edge
            pcs = []
        return [s for pc in pcs for s in self.model.variants(pc)]

    def _split_succ(self, key):
        """Successors on the leader-split CFG: a block that spans a later leader
        flows *into* that leader (instruction-level control), so an overlapping
        setup block cannot bypass an inner loop's header."""
        blk = self.model.blocks[key]
        for pc in blk.pcs[1:]:
            if self.model.variants(pc):
                return list(self.model.variants(pc))
        return self._cfg_succ_keys(key)

    def _split_idom(self):
        """Immediate dominators over the leader-split static CFG (Cooper-Harvey-
        Kennedy), rooted at every procedure; the basis for nested-loop back-edges.
        Cached: an ``Analysis`` sees a fixed block set."""
        if self._sidom is not None:
            return self._sidom
        model = self.model
        succ = {k: self._split_succ(k) for k in model.blocks}
        root = ("root", None)
        entries = [model.init, model.play]  # root every procedure so callees get dominators
        entries += [b.term[1] for b in model.blocks.values() if b.term[0] == "jsr" and b.term[1]]
        succ[root] = [s for e in entries for s in model.variants(e)]
        order, seen, stack = [], {root}, [(root, iter(succ[root]))]
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
        self._sidom = idom
        return idom

    @staticmethod
    def _dom(idom, a, b):
        n = b
        while n in idom:
            if n == a:
                return True
            p = idom[n]
            if p == n:
                return False
            n = p
        return False

    def natural_loops(self):
        """``{header_key: body_set}`` for each back-edge (an edge whose target
        dominates its source) over the leader-split CFG, so nested and
        overlapping-block loops are recovered. Sound; the trip-bound foundation."""
        return self._loops_from(self._split_idom())

    def _loops_from(self, idom):
        succ = {k: self._split_succ(k) for k in self.model.blocks}
        preds = {}
        for k, outs in succ.items():
            for s in outs:
                preds.setdefault(s, []).append(k)
        loops = {}
        for tail, outs in succ.items():
            for hdr in outs:
                if not self._dom(idom, hdr, tail):
                    continue
                body = loops.setdefault(hdr, {hdr})
                stack = [tail]
                while stack:
                    n = stack.pop()
                    if n not in body:
                        body.add(n)
                        stack.extend(preds.get(n, ()))
        return loops

    def _cfg_preds(self, hdr):
        return [k for k in self.model.blocks if hdr in self._split_succ(k)]

    # -- affine trip-bound of a monotone-increment cell (lemma 2) -----------
    @staticmethod
    def _reg_delta(expr, i):
        """Signed constant delta if ``expr`` is ``reg(i) (+|-) k``; 0 if identity."""
        if expr == E.reg(i):
            return 0
        if expr[0] == "op" and expr[1] == "INT_ADD" and len(expr[2]) == 2:
            a, b = expr[2]
            if a == E.reg(i) and E.is_const(b):
                return _signed8(b[1])
        return None

    @staticmethod
    def _incr_delta(vexpr, cell):
        """Positive constant ``d`` if ``vexpr`` is ``mem[cell] + d`` (1..127)."""
        if vexpr[0] != "op" or vexpr[1] != "INT_ADD":
            return None
        memkid, delta = None, 0
        for k in vexpr[2]:
            if k[0] == "mem" and E.is_const(k[1]) and k[1][1] == cell:
                if memkid is not None:
                    return None
                memkid = k
            elif E.is_const(k):
                delta += k[1]
            else:
                return None
        d = delta & 0xFF
        return d if memkid is not None and 1 <= d <= 127 else None

    def _cell_stores_of(self, cell):
        """``(const_values, [(block_key, d)])`` for stores to ``cell``; ``None`` if
        any store is neither a constant nor a ``mem[cell] + d`` increment."""
        consts, incrs = set(), []
        for key, blk in self.model.blocks.items():
            for ev in blk.events:
                if ev[0] != "st" or not E.is_const(ev[1]) or ev[1][1] != cell:
                    continue
                if E.is_const(ev[2]):
                    consts.add(ev[2][1])
                    continue
                d = self._incr_delta(ev[2], cell)
                if d is None:
                    return None
                incrs.append((key, d))
        return consts, incrs

    def _within_block_const(self, key, cell):
        """``cell``'s constant value at ``key``'s exit if a constant store to it is
        followed only by increments in the block, else ``None``."""
        val = None
        for ev in self.model.blocks[key].events:
            if ev[0] != "st" or not E.is_const(ev[1]) or ev[1][1] != cell:
                continue
            if E.is_const(ev[2]):
                val = ev[2][1]
            else:
                d = self._incr_delta(ev[2], cell)
                val = None if d is None or val is None else (val + d) & 0xFF
        return val

    def _bpl_counter(self, blk, body):
        """If ``blk`` is a loop-exit branch on a decremented counter's sign
        (``BPL``: continue while ``ctr - k >= 0``), return that register."""
        term = blk.term
        if term[0] != "br" or term[2] is None:
            return None
        cont, exit_ = (term[2], term[3]) if term[1] == 0 else (term[3], term[2])
        if any(k in body for k in self.model.variants(cont)) == (
            any(k in body for k in self.model.variants(exit_))
        ):
            return None  # need exactly one edge staying in the loop
        if not any(k in body for k in self.model.variants(cont)):
            return None
        flag = term[4]
        if flag[0] != "op" or flag[1] != "INT_NOTEQUAL" or not E.is_const(flag[2][1]):
            return None
        if flag[2][1][1] != 0 or flag[2][0][0] != "op" or flag[2][0][1] != "INT_AND":
            return None
        band = flag[2][0][2]
        if len(band) != 2 or not E.is_const(band[1]) or band[1][1] != 0x80:
            return None
        regs = set()
        _regs_of(band[0], regs)
        return next(iter(regs)) if len(regs) == 1 else None

    def _affine_cell_bound(self, cell):
        """A bounded value set for a monotone-increment ``cell`` proven via the
        loop invariant ``H + Y = K`` (H the cell, Y a co-decremented counter),
        or ``None`` when any premise is unproven (§4 lemma 2)."""
        got = self._cell_stores_of(cell)
        if got is None:
            return None
        consts, incrs = got
        if not consts or not incrs or len({d for _k, d in incrs}) != 1:
            return None
        d = incrs[0][1]
        incr_keys = {k for k, _ in incrs}
        model = self.model
        sidom = self._split_idom()
        for hdr, body in self._loops_from(sidom).items():
            body_incr = incr_keys & body
            if not body_incr:
                continue
            counters = None
            for key in body_incr:
                ys = {i for i in range(16) if self._reg_delta(model.blocks[key].regs[i], i) == -d}
                counters = ys if counters is None else counters & ys
            for y in sorted(counters or ()):
                bound = self._affine_bound_for(cell, hdr, body, y, d, consts, incr_keys, sidom)
                if bound is not None:
                    return bound
        return None

    def _affine_bound_for(self, cell, hdr, body, y, d, consts, incr_keys, sidom):
        model = self.model
        exits = [k for k in body if self._bpl_counter(model.blocks[k], body) == y]
        if not exits:
            return None  # no proven BPL exit on this counter -> unbounded
        for key in body:  # invariant H + Y = K: every body block cancels dH, dY
            blk = model.blocks[key]
            dh = 0
            for ev in blk.events:
                if ev[0] == "st" and E.is_const(ev[1]) and ev[1][1] == cell:
                    inc = self._incr_delta(ev[2], cell)
                    if inc is None:
                        return None
                    dh += inc
            dy = self._reg_delta(blk.regs[y], y)
            if dy is None or dh + dy != 0:
                return None
        pre = [p for p in self._cfg_preds(hdr) if p not in body]
        if not pre or not incr_keys <= (body | set(pre)):
            return None  # an increment outside the loop and its pre-header: unaccounted
        const_blocks = [
            k
            for k, blk in model.blocks.items()
            for ev in blk.events
            if ev[0] == "st" and E.is_const(ev[1]) and ev[1][1] == cell and E.is_const(ev[2])
        ]
        for k, blk in model.blocks.items():  # definite assignment: image byte is dead
            if any(_contains_memcell(e, cell) for e in _block_exprs(blk)):
                if not any(self._dom(sidom, cb, k) for cb in const_blocks):
                    return None
        h0 = {self._within_block_const(p, cell) for p in pre}
        if len(h0) != 1 or None in h0:
            return None
        h0 = next(iter(h0))
        ymax = 0
        for p in pre:
            try:
                vals = self._pred_contrib(p, y, hdr)
            except DecompileError:
                return None
            if not vals or max(vals) > 128:
                return None
            ymax = max([ymax, *vals])
        hi = h0 + (ymax + 1) * d
        if hi > 0xFF:  # byte cell wrap: not proven disjoint
            return None
        lo = min(consts)
        return frozenset(range(lo, hi + 1))


def _pair_vars(n, out):
    """Collect reg/uni variables of a writer live expression (into mem addrs)."""
    k = n[0]
    if k in ("reg", "uni"):
        out.add((k, n[1]))
    elif k == "mem":
        _pair_vars(n[1], out)
    elif k == "op":
        for c in n[2]:
            _pair_vars(c, out)


class _NotPure(Exception):
    pass


def _leaf_vars(n, out):
    """Collect register/slot variables (immutable mem leaves count as consts)."""
    k = n[0]
    if k == "reg":
        out.add(("reg", n[1]))
    elif k == "uni":
        out.add(("uni", n[1]))
    elif k == "op":
        for c in n[2]:
            _leaf_vars(c, out)


def _eval1(n, var, v, model):
    """Evaluate ``n`` with the single variable ``var`` bound to ``v``."""
    k = n[0]
    if k == "const":
        return n[1]
    if k in ("reg", "uni"):
        if var == (k, n[1]):
            return v
        raise _NotPure()
    if k == "mem":
        a = n[1]
        if E.is_const(a) and a[1] not in model.written:
            return model.mem0[a[1]]
        raise _NotPure()
    vals = [_eval1(c, var, v, model) for c in n[2]]
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


_TERM_KIND = {"jmpd": "jump", "jmpind": "vector", "br": "branch", "jsr": "call"}


def _close_once(model):
    """One certification round over the analysis workspace: control cells,
    dyn-target resolution, SP flow, stack concretization, dominators. Returns
    ``(closed, sites)``: static dispatch value sets and per-site ``(term kind,
    static targets, lemma)`` — certification input; committed sets are observed."""
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
    ana.dominators()  # loop structure for affine trip bounds during dispatch closure
    targets_map = {}
    lemmas = {}
    pending = list(dyn_blocks)
    for _round in range(8):
        needs = set()
        still = []
        for blk in pending:
            try:
                targets_map[blk] = ana.term_targets(blk)
                lemmas.pop(blk.pcs[-1], None)
            except _Need as need:
                needs.add(need.cell)
                still.append(blk)
                lemmas[blk.pcs[-1]] = "control target at $%04X depends on unclosed cell $%04X" % (
                    blk.pcs[-1],
                    need.cell,
                )
            except DecompileError as exc:
                targets_map[blk] = []  # static gave up: the guard stays live
                lemmas[blk.pcs[-1]] = str(exc)
        if not needs:
            break
        ana.close(needs)
        pending = still
    ana.pair_enabled = False  # rounds spent: pending sites resolve without pairing
    for blk in pending:
        if blk in targets_map:
            continue
        site = blk.pcs[-1]
        try:
            targets_map[blk] = ana.term_targets(blk)
            lemmas.pop(site, None)
        except _Need as need:
            lemmas[site] = "control target at $%04X depends on unclosed cell $%04X" % (
                site,
                need.cell,
            )
        except DecompileError as exc:
            targets_map[blk] = []
            lemmas[site] = str(exc)
    ana.pair_enabled = True
    sites = {}
    for blk in dyn_blocks:
        site = blk.pcs[-1]
        if blk not in targets_map:
            targets_map[blk] = []
            lemmas.setdefault(
                site, "control target at $%04X unresolved after closure rounds" % site
            )
        sites[site] = (blk.term[0], tuple(sorted(targets_map[blk])), lemmas.get(site))
    ana.sp_flow()
    ana.concretize_stack()
    ana.dominators()
    ana.stack_rule = True
    closed = {}
    if model.dispatch_pcs:
        sets = ana.close(set(model.dispatch_pcs))
        for pc in sorted(model.dispatch_pcs):
            closed[pc] = sets[pc]
    model.analysis = ana
    return closed, sites


def _analyze(model):
    """ANALYSIS phase: iterate closure and speculative block materialization over
    the workspace to a fixpoint. Returns the final round's ``(closed, sites)``;
    no final tables or blocks are constructed here."""
    while True:
        materialize(model)
        n = len(model.blocks)
        closed, sites = _close_once(model)
        materialize(model)
        if len(model.blocks) == n:
            return closed, sites


def _static_lemma(tgts, lemma, obs):
    """Guard-live reason: the static refusal, or the static/observed mismatch."""
    if lemma is not None:
        return lemma
    return "static set %d target(s) != %d observed" % (len(tgts), len(obs))


def _commit_tables(model, closed, sites):
    """Observed-primary site tables: every committed set is exactly the
    trace-observed set; static results only certify a guard dead (static set
    equals observed) and are otherwise recorded as the guard-live reason."""
    proofs, dyn_targets, dispatch_sets = {}, {}, {}
    ana = model.analysis
    derivs = ana.derivations if ana is not None else {}
    clo = model.closure
    horizon = None
    if clo is not None and clo.recur is not None:
        horizon = "closure: frame %d entry state == frame %d (window %d); observed set complete" % (
            clo.recur,
            clo.first,
            clo.window,
        )
    for site in sorted(sites):
        term0, tgts, lemma = sites[site]
        obs = tuple(sorted(model.ev_targets.get(site, ())))
        kind = _TERM_KIND.get(term0, term0)
        if lemma is None and tuple(sorted(tgts)) == obs:
            deriv = "; ".join(txt for _cells, txt in sorted(derivs.get(site, {}).items()))
            proofs[site] = Proof(site, kind, "certified", obs, deriv)
        elif horizon is not None:
            proofs[site] = Proof(site, kind, "certified", obs, horizon)
        else:
            proofs[site] = Proof(site, kind, "observed", obs, _static_lemma(tgts, lemma, obs))
        dyn_targets[site] = list(obs)
    for pc in sorted(model.dispatch_pcs):
        obs = tuple(sorted(model.pcs[pc]))
        vals = closed.get(pc, set())
        if vals is not TOP and tuple(sorted(vals)) == obs:
            proofs[pc] = Proof(pc, "opcode", "certified", obs, "")
        elif horizon is not None:
            proofs[pc] = Proof(pc, "opcode", "certified", obs, horizon)
        else:
            lemma = (
                "opcode-cell value set TOP (aliasing computed store)"
                if vals is TOP
                else "static set %d value(s) != %d observed" % (len(vals), len(obs))
            )
            proofs[pc] = Proof(pc, "opcode", "observed", obs, lemma)
        dispatch_sets[pc] = set(model.pcs[pc])
    return proofs, dyn_targets, dispatch_sets


def _commit(model, closed, sites):
    """COMMIT phase: construct the final site tables and final block set in one
    step from the analysis results (resplit at final entries, keep only blocks
    final-reachable from play, then the intra-block passes). Blocks are immutable
    afterwards; only ``lookup`` may lazily add static continuations."""
    model.proofs, model.dyn_targets, model.dispatch_sets = _commit_tables(model, closed, sites)
    live = sorted(s for s, p in model.proofs.items() if p.status != "certified")
    if model.sound and live:
        raise DecompileError(
            "sound mode: %d guard-live site(s): %s"
            % (len(live), "; ".join("$%04X (%s)" % (s, model.proofs[s].lemma) for s in live))
        )
    model._resplit()
    collect_unreachable(model)
    prune_dead_flags(model)
    for blk in model.blocks.values():
        inline_slots(blk)


def _dyn_site(model, blk):
    """Whether ``blk``'s terminator is a dynamic-dispatch site (proof required)."""
    term = blk.term
    if term[0] == "jmpd" or (term[0] in ("br", "jmpind", "jsr") and term[-1] is not None):
        return True
    if term[0] == "jmpind":
        ptr = term[1]
        return ptr in model.written or (ptr + 1) & 0xFFFF in model.written
    return False


def check_commit(model):
    """Commit-boundary checker, one rule for every site class: every committed
    set IS the observed set, every kept block is final-reachable from play,
    every observed variant is built, and site records exist only for kept
    sites. Violations are fatal."""
    errs = []
    live = {blk.pcs[-1] for blk in model.blocks.values()} | {key[0] for key in model.blocks}
    seen = set()
    work = [model.play]
    while work:
        pc = work.pop()
        if pc in seen:
            continue
        seen.add(pc)
        for key in model.variants(pc):
            work.extend(_walk_edges(model, model.blocks[key]))
    for key, blk in sorted(model.blocks.items()):
        if key[0] not in seen:
            errs.append("block $%04X/$%02X not reachable from play" % key)
        site = blk.pcs[-1]
        if _dyn_site(model, blk) and site not in model.proofs:
            errs.append("dyn site $%04X has no proof record" % site)
    for site, pr in sorted(model.proofs.items()):
        obs = model.pcs.get(site, ()) if pr.kind == "opcode" else model.ev_targets.get(site, ())
        committed = (
            model.dispatch_sets.get(site) if pr.kind == "opcode" else (model.dyn_targets.get(site))
        )
        if set(pr.targets) != set(obs) or set(committed or ()) != set(obs):
            errs.append("site $%04X (%s): committed set is not the observed set" % (site, pr.kind))
    for pc in sorted(model.dispatch_pcs):
        if pc not in model.proofs or pc not in model.dispatch_sets:
            errs.append("dispatch cell $%04X has no committed set" % pc)
            continue
        for op0 in sorted(model.pcs[pc]):
            if (pc, op0) not in model.blocks:
                errs.append("observed variant $%02X at $%04X failed to build" % (op0, pc))
    for name, table in (("proof", model.proofs), ("dyn-target", model.dyn_targets)):
        errs.extend(
            "%s record for nonexistent site $%04X" % (name, site)
            for site in sorted(table)
            if site not in live
        )
    if errs:
        raise DecompileError("commit check: " + "; ".join(errs))


def proof_report(model):
    """Serialisable per-site certification report: proofs sorted by site plus a
    status tally. ``observed`` entries are live guards with the static reason."""
    proofs = [model.proofs[s] for s in sorted(model.proofs)]
    tally = {}
    for p in proofs:
        tally[p.status] = tally.get(p.status, 0) + 1
    clo = model.closure
    closure = None
    if clo is not None:
        closure = {
            "recur": clo.recur,
            "first": clo.first,
            "window": clo.window,
            "cap": clo.cap,
            "note": clo.note,
            "diag": ["$%04X %02X->%02X" % d for d in clo.diag],
        }
    return {
        "tally": tally,
        "closure": closure,
        "sites": [
            {
                "site": "$%04X" % p.site,
                "kind": p.kind,
                "status": p.status,
                "targets": ["$%04X" % t for t in p.targets],
                "lemma": p.lemma,
            }
            for p in proofs
        ],
    }


def format_report(model):
    """Human-readable proof report for the CLI (``--report``)."""
    rep = proof_report(model)
    tally = rep["tally"]
    lines = [
        "proof report: "
        + ", ".join("%s=%d" % (k, tally[k]) for k in sorted(tally))
        + (" [SOUND]" if not tally.get("observed") else "")
    ]
    clo = rep["closure"]
    if clo is not None:
        if clo["recur"] is not None:
            lines.append(
                "  closure: recurrence at frame %d == frame %d (window %d, cap %d)"
                % (clo["recur"], clo["first"], clo["window"], clo["cap"])
            )
        else:
            lines.append("  closure: %s; guards stay live" % clo["note"])
            for d in clo["diag"][:8]:
                lines.append("    still changing: " + d)
    for s in rep["sites"]:
        line = "  %s %-7s %-8s %d target(s)" % (
            s["site"],
            s["kind"],
            s["status"],
            len(s["targets"]),
        )
        if s["lemma"]:
            line += " :: " + s["lemma"]
        lines.append(line)
    return "\n".join(lines) + "\n"


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


def _contains_memcell(n, cell):
    """Whether expression ``n`` reads ``mem[cell]`` (a constant-address load)."""
    if not isinstance(n, tuple):
        return False
    if n[0] == "mem":
        return (E.is_const(n[1]) and n[1][1] == cell) or _contains_memcell(n[1], cell)
    if n[0] == "op":
        return any(_contains_memcell(c, cell) for c in n[2])
    return False


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


def _walk_edges(model, blk):
    """Successor pcs the walker can traverse from ``blk`` under the final
    closure: static terms, call returns, and final dyn/observed target sets."""
    term = blk.term
    t = term[0]
    out = []
    if t in ("goto", "jmp"):
        out.append(term[1])
    elif t == "br":
        if term[2] is not None:
            out.append(term[2])
        out.append(term[3])
    elif t == "jsr":
        if term[1] is not None:
            out.append(term[1])
        out.append((term[2] + 1) & 0xFFFF)
    elif t == "jmpind" and term[2] is None:
        m, ptr = model.mem0, term[1]
        out.append(m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8))
    site = blk.pcs[-1]
    if t in ("br", "jmpd", "jmpind", "jsr"):
        out.extend(model.dyn_targets.get(site, ()))
    elif t == "rts":
        out.extend(model.ev_targets.get(site, ()))  # RTS-trick landings
    return out


def collect_unreachable(model):
    """Drop blocks unreachable from play under the final closure (transient
    fixpoint-round materialization residue), plus their stale site records.
    ``cut`` is preserved so lazily rebuilt continuations keep their shape."""
    seen = set()
    work = [model.play]
    while work:
        pc = work.pop()
        if pc in seen:
            continue
        seen.add(pc)
        for key in model.variants(pc):
            work.extend(_walk_edges(model, model.blocks[key]))
    if all(key[0] in seen for key in model.blocks):
        return
    model.blocks = {key: blk for key, blk in model.blocks.items() if key[0] in seen}
    by_pc = {}
    for key in model.blocks:
        by_pc.setdefault(key[0], []).append(key)
    model._by_pc = by_pc
    model.__dict__.pop("_static_preds", None)  # graph memos: now stale
    model.__dict__.pop("_proc_plan", None)
    live = {blk.pcs[-1] for blk in model.blocks.values()} | {key[0] for key in model.blocks}
    for table in (model.dyn_targets, model.proofs):
        for site in [s for s in table if s not in live]:
            del table[site]


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

    def __init__(self, mem0, init, play, evidence, subtune=0, sound=False):
        self.mem0 = bytes(mem0)
        self.init = init
        self.play = play
        self.subtune = subtune
        self.prologue = list(evidence.prologue)
        self.sound = sound  # strict mode: any guard-live (uncertified) site fails the build
        # stack page always mutable: jsr/rts traffic bypasses _wr in PcodeVM.step
        self.written = frozenset(evidence.written) | frozenset(range(0x100, 0x200))
        self.reads = evidence.reads  # play-phase (site pc, read address) pairs
        self.pcs = evidence.pcs
        self.leaders = set(evidence.leaders)
        self.cut = set(evidence.leaders)  # block boundaries: no suffix duplication
        self.ev_targets = evidence.targets
        self.closure = evidence.closure  # run-to-recurrence record (None: scan off)
        self.dispatch_pcs = {pc for pc in evidence.pcs if pc in evidence.written}
        self.dispatch_sets = {}
        self.blocks = {}
        self.analysis = None
        self.dyn_targets = {}  # transfer-site pc -> observed successor pcs
        self.proofs = {}  # site pc -> Proof (certification record)
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
        """ANALYSIS over the workspace, then one immutable COMMIT, then the
        boundary coverage check."""
        for pc in sorted(self.leaders | self.dispatch_pcs):
            for op0 in sorted(self.pcs.get(pc, ())):
                self.build(pc, op0)
        closed, sites = _analyze(self)
        _commit(self, closed, sites)
        check_commit(self)
        return self

    def _resplit(self):
        """Cut every block at any other block's entry: closure-materialized
        entries arrive after the first build, so shared suffixes re-lift short."""
        self.cut = {pc for pc, _op in self.blocks}
        for key, blk in list(self.blocks.items()):
            if any(p in self.cut for p in blk.pcs[1:]):
                self.blocks[key] = _BlockBuilder(self, key[0], key[1]).block

    def lookup(self, pc, m):
        if pc in self.written:
            key = (pc, m[pc])
            blk = self.blocks.get(key)
            if blk is None:
                raise WalkError("opcode $%02X at $%04X outside observed set" % (key[1], pc))
        else:
            ops = self.pcs.get(pc)
            key = (pc, next(iter(ops)) if ops is not None else self.mem0[pc])
            blk = self.blocks.get(key)
            if blk is None:
                blk = self.build(*key)  # static continuation: same static info
        if blk.fn is None:
            blk.fn = compile_block(blk)
        return blk


def decompile(mem, init, play, frames, subtune=0, sound=False, close=False, close_cap=None):
    """Full-length evidence trace + committed, passed model: ``(model, evidence)``.

    ``subtune`` selects the tune, ``sound=True`` requires every guard certified.
    ``close=True`` plays on past ``frames`` until the play state recurs (cap
    ``close_cap``, default ``max(4 * frames, 60000)``); recurrence certifies.
    """
    cap = (close_cap or max(4 * frames, 60000)) if close else 0
    ev = trace(bytearray(mem), init, play, frames, subtune, cap)
    model = Model(ev.mem0, init, play, ev, subtune, sound).build_all()
    try:
        codec.verify(model)  # flatten(structure(model)) == CFG, per procedure
    except codec.CodecError as e:
        raise DecompileError("structurer codec: %s" % e) from e
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

    def _guard(self, blk, pc):
        """Fault when a dynamic transfer leaves its committed (observed) set."""
        tg = self.model.dyn_targets.get(blk.pcs[-1])
        if tg is not None and pc not in tg:
            raise WalkError("target $%04X at $%04X outside observed set" % (pc, blk.pcs[-1]))
        return pc

    def _run_entry(self, entry, acc=0):
        model = self.model
        m = self.m
        start = self.r[3]
        self.r[0] = acc & 0xFF
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
                    if dynx is not None and tgt != ft:  # branch onto ft: static edge
                        self._guard(blk, tgt)
                    self.c += 1 + ((ft & 0xFF00) != (tgt & 0xFF00))
                    pc = tgt
                else:
                    pc = ft
            elif kind == "jmpd":
                pc = self._guard(blk, x)
            elif kind == "jmpind":
                ptr = term[1] if term[2] is None else x
                pc = self._guard(blk, m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8))
            elif kind == "jsr":
                _, tgt, ret, dynx = term
                self._push(ret >> 8)
                self._push(ret & 0xFF)
                pc = tgt if dynx is None else self._guard(blk, x)
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
        for _ in range(frames):
            self._run_entry(self.model.play)
        return self.wlog
