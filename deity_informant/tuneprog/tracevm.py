"""S1 -- the tracing VM: per-P-Code-op access attribution, sites, edges, logs.

:class:`TraceVM` is a :class:`~deity_informant.vm.PcodeVM` whose generated
straight-line P-Code calls ``rd(a, sz, i)`` / ``wr(a, v, sz, i)`` with the index
``i`` of the P-Code op making the access, so a ``(zp),Y`` pointer fetch and the
stream load it feeds are attributed separately. The base VM is untouched.

The CIA model of :mod:`.machine` answers every ``$DCxx``/``$DDxx`` timer and ICR
read, which supersedes the base VM's ``ciaicr`` flag: a ``TraceVM`` driven by
``run_irq_driven`` would not see that driver's raised CIA source.
"""

from __future__ import annotations

from array import array
from collections import Counter, defaultdict

from ..lifter import OPS, MODE_LEN, STATUS_BITS
from ..vm import PcodeVM, _emit_line, _rd_expr, _lhs
from .ir import IO_HI, IO_LO, SID_HI, SID_LO
from .machine import CIA, CIA1_BASE, CIA2_BASE, Refusal, port_bank

PH_INIT, PH_PLAY = 1, 2
IDX_REG = {"absx": 1, "zpx": 1, "indx": 1, "absy": 2, "zpy": 2, "indy": 2}
ACKS = (0xD019, 0xDC0D, 0xDD0D)
REG_IN = 0x10000  # synthetic input addresses for live-in A/X/Y
MAX_CELL_VALUES = 16
_KIND = {"jmp": "jmp", "jmpind": "jmpind", "jsr": "jsr", "brk": "brk"}
_FLAGS = sum(1 << i for i, _b in STATUS_BITS)


def input_kind(addr):
    """Input class of ``addr`` (design section 4 ``Input.kind``)."""
    if addr >= REG_IN:
        return "entry_reg"
    if addr in ACKS:
        return "ack"
    if addr == 0xD011 or addr == 0xD012:
        return "raster"
    if SID_LO <= addr <= SID_HI:
        return "sid_readback"
    if 0xDC00 <= addr <= 0xDDFF:
        return "cia"
    if IO_LO <= addr <= IO_HI:
        return "io"
    return "uninit_ram"


def _emit_attr(mn, out, ins, i):
    """One P-Code op as Python, with the op index threaded into rd/wr."""
    if mn == "STORE":
        return "wr(%s, %s, %d, %d)" % (_rd_expr(ins[0]), _rd_expr(ins[1]), ins[1][2], i)
    if mn == "LOAD":
        return "%s = rd(%s, %d, %d)" % (_lhs(out), _rd_expr(ins[0]), out[2], i)
    return _emit_line(mn, out, ins)


def _reg_masks(rec):
    """``(read-before-write, written)`` register-file bitmasks of one instruction."""
    rd = wr = 0
    for _mn, out, ins in rec["ops"]:
        for vn in ins:
            if vn[0] == "r":
                b = 1 << vn[1]
                if not wr & b:
                    rd |= b
        if out is not None and out[0] == "r":
            wr |= 1 << out[1]
    stk = rec["stk"]
    if stk is not None:
        wr |= 1 << 3
        if stk in ("rts", "rti"):
            rd |= 1 << 3
        if stk == "rti":  # step() pops the status byte into the flag registers
            wr |= _FLAGS
        elif stk == "brk":  # step() pushes the status byte and sets I
            rd |= _FLAGS & ~wr
            wr |= _FLAGS | (1 << 10)
    return rd, wr


class TraceVM(PcodeVM):
    """PcodeVM with per-P-Code-op access attribution, sites, edges and logs."""

    def __init__(self, mem, image, policy="record", inputs=None, override=None):
        super().__init__(mem)
        self.image = image
        self.policy = policy
        self.override = override or {}
        self.replay = iter(inputs or ())
        self.phase = PH_INIT
        self.call = -1
        self.bank = port_bank(mem)
        self.cia = (CIA(CIA1_BASE), CIA(CIA2_BASE))
        self.known = bytearray(0x10000)
        self.known[image.lo : image.hi] = b"\1" * (image.hi - image.lo)
        self.known[0x100:0x200] = b"\1" * 0x100
        self.code = bytearray(0x10000)
        self.inband = bytearray(0x10000)
        self.inband[image.lo : image.hi] = b"\1" * (image.hi - image.lo)
        self.count = Counter()
        self.sitephase = {}
        self.reads = defaultdict(dict)
        self.writes = defaultdict(dict)
        self.idx = defaultdict(set)
        self.edges = {}
        self.calls = {}
        self.rets = {}
        self.summaries = {}
        self.shadow = []
        self.unmatched_rts = 0
        self.max_depth = 0
        self.insns = 0
        self.written_init = set()
        self.written_play = set()
        self.chip_ops = set()
        self.wr_values = defaultdict(set)
        self.init_writes = []
        self.inputs = []
        self.input_sites = {}
        # column arrays (call, addr, val, cycle); the base VM's own wlog hook is
        # unused because _wr is fully overridden.
        self.sidlog = tuple(array(t) for t in "IHBI")
        self.iolog = tuple(array(t) for t in "IHBI")
        self.tick_rd = self.tick_wr = 0
        self._rs = {}
        self._ws = {}
        self._pc = self._op = 0
        self._r0 = (0, 0, 0)

    # ---- per-op attributed memory ------------------------------------------
    def compile_record(self, rec):
        f = rec.get("_f")
        if f is None:
            lines = [_emit_attr(mn, out, ins, i) for i, (mn, out, ins) in enumerate(rec["ops"])]
            src = "def _f(r,u,rd,wr):\n    " + ("\n    ".join(lines) or "pass") + "\n"
            ns = {}
            exec(src, ns)  # noqa: S102 - generated straight-line P-Code
            f = rec["_f"] = ns["_f"]
        return f

    def _rd(self, addr, sz, i=0):
        s = self._rs.get(i)
        if s is None:
            s = self._rs[i] = set()
        if sz == 1:
            s.add(addr)
            return self._rd1(addr, i)
        v = 0
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            s.add(a)
            v |= self._rd1(a, i) << (8 * k)
        return v

    def _rd1(self, a, i):
        if IO_LO <= a <= IO_HI:
            if self.bank != "io":
                return self.mem[a]
            v = self.override.get(a)
            if v is None:
                v = self.cia[0].read(a, self.cycles)
            if v is None:
                v = self.cia[1].read(a, self.cycles)
            if v is None:
                v = PcodeVM._rd(self, a, 1)
            self.chip_ops.add((self._pc, i))
            return self._input(a, v, i, input_kind(a))
        v = self.mem[a]
        if not self.known[a]:
            v = self._input(a, v, i, "uninit_ram")
        return v

    def _input(self, addr, value, i, kind):
        site = self._pc
        rec = self.input_sites.get((site, addr))
        if rec is None:
            rec = self.input_sites[(site, addr)] = {"kind": kind, "count": 0, "phase": 0}
        rec["count"] += 1
        rec["phase"] |= self.phase
        if self.policy == "replay":
            nxt = next(self.replay, None)
            if nxt is None or nxt[3] != addr or nxt[1] != site:
                raise Refusal("input replay mismatch", "at $%04X call %d" % (addr, self.call))
            return nxt[4]
        self.inputs.append((self.call, site, i, addr, value))
        return value

    def _wr(self, addr, val, sz, i=0):
        s = self._ws.get(i)
        if s is None:
            s = self._ws[i] = set()
        mem = self.mem
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            b = (val >> (8 * k)) & 0xFF
            s.add(a)
            if IO_LO <= a <= IO_HI and self.bank == "io":
                self.chip_ops.add((self._pc, i))
                self._io_write(a, b)
            else:
                self.known[a] = 1
                (self.written_init if self.phase == PH_INIT else self.written_play).add(a)
                if self.inband[a]:
                    vs = self.wr_values[a]
                    if len(vs) < MAX_CELL_VALUES:
                        vs.add(b)
            mem[a] = b
        if addr <= 1:
            self.bank = port_bank(mem)

    def _io_write(self, a, b):
        log = self.sidlog if SID_LO <= a <= SID_HI else self.iolog
        log[0].append(self.call & 0xFFFFFFFF)
        log[1].append(a)
        log[2].append(b)
        log[3].append(self.cycles & 0xFFFFFFFF)
        if self.phase == PH_INIT:
            self.init_writes.append((a, b, self.cycles))
        if a == 0xD019:
            self.vicirq &= ~b & 0x7F
        elif 0xDC00 <= a <= 0xDDFF:
            self.cia[(a >> 8) & 1].write(a, b, self.cycles)

    # ---- sites, edges, frames ----------------------------------------------
    def push_frame(self, site, ret, target):
        """Push a shadow frame (``site`` is ``None`` for a driver's dummy return)."""
        self.shadow.append([site, ret, target, 0, 0])
        self.max_depth = max(self.max_depth, len(self.shadow))

    def step(self, pc, cache, lifter):
        mem = self.mem
        b0 = mem[pc]
        key = (pc, b0, mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF])
        mode = OPS[b0][1]
        bb = key[1 : 1 + MODE_LEN[mode]]
        sk = (pc, bb)
        self._pc = pc
        self._op = b0
        n = self.count[sk]
        self.count[sk] = n + 1
        if not n:  # first execution of these exact bytes: they are instruction bytes
            for k in range(len(bb)):
                self.code[(pc + k) & 0xFFFF] = 1
        self.sitephase[sk] = self.sitephase.get(sk, 0) | self.phase
        r = IDX_REG.get(mode)
        if r is not None:
            self.idx[sk].add(self.reg[r])
        self._rs = self.reads[sk]
        self._ws = self.writes[sk]
        if self.tick_rd & 7 != 7:
            self._r0 = (self.reg[0], self.reg[1], self.reg[2])
        self.insns += 1
        nxt = super().step(pc, cache, lifter)
        rec = cache[key]
        rw = rec.get("_rw")
        if rw is None:
            rw = rec["_rw"] = _reg_masks(rec)
        self._account(pc, rec, nxt, rw)
        return nxt

    def _account(self, pc, rec, nxt, rw):
        rd, wr = rw
        live = rd & ~self.tick_wr
        if live & 7:
            for j in range(3):
                if live & (1 << j) and not self.tick_rd & (1 << j):
                    self._input(REG_IN + j, self._r0[j], 0, "entry_reg")
        self.tick_rd |= rd
        self.tick_wr |= wr
        if self.shadow:
            f = self.shadow[-1]
            f[3] |= rd & ~f[4]
            f[4] |= wr
        sk = (pc, self._op)
        kind = rec["ctrl"][0]
        if kind == "next":
            self._edge(sk, nxt, "fall")
        elif kind == "br":
            self._edge(sk, nxt, "br_taken" if nxt == rec["ctrl"][3] else "br_not")
        elif kind == "jsr":
            self._edge(sk, nxt, "jsr")
            ret = (pc + rec["len"]) & 0xFFFF
            c = self.calls.get(sk)
            if c is None:
                c = self.calls[sk] = {"targets": Counter(), "ret_pc": ret, "count": 0}
            c["targets"][nxt] += 1
            c["count"] += 1
            self.push_frame(pc, ret, nxt)
        elif kind in ("rts", "rti"):
            self._return(sk, nxt)
        else:
            self._edge(sk, nxt, _KIND[kind])

    def _edge(self, sk, t, kind):
        e = self.edges.get((sk[0], sk[1], t))
        if e is None:
            self.edges[(sk[0], sk[1], t)] = [kind, 1]
        else:
            e[1] += 1

    def _return(self, sk, nxt):
        r = self.rets.get(sk)
        if r is None:
            r = self.rets[sk] = {
                "matched": Counter(),
                "unmatched": 0,
                "targets": Counter(),
                "loose": Counter(),
            }
        r["targets"][nxt] += 1
        if self.shadow and self.shadow[-1][1] == nxt:
            site, _ret, target, frd, fwr = self.shadow.pop()
            r["matched"][site if site is not None else -1] += 1
            if self.shadow:
                p = self.shadow[-1]
                p[3] |= frd & ~p[4]
                p[4] |= fwr
            s = self.summaries.get(target)
            if s is None:
                s = self.summaries[target] = {"rd": 0, "wr": 0, "count": 0}
            s["rd"] |= frd
            s["wr"] |= fwr
            s["count"] += 1
        else:
            r["unmatched"] += 1
            r["loose"][nxt] += 1
            self.unmatched_rts += 1
