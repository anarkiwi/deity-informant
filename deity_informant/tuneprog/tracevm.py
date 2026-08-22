"""S1 -- the tracing VM: per-P-Code-op access attribution, sites, edges, logs.

Its cache key is the site key, so :mod:`.tracesite` resolves each site once and
:meth:`TraceVM.step` fuses the base VM's fetch, execute, resolve and dispatch
with the accounting into one pass.
"""

from __future__ import annotations

from array import array

from ..vm import PcodeVM
from .ir import IO_HI, IO_LO, SID_HI, SID_LO
from .machine import CIA, CIA1_BASE, CIA2_BASE, Refusal, port_bank
from .traceflow import FlowRecorder
from .tracesite import (
    IDX_REG,
    ILEN,
    K_BR,
    K_BRK,
    K_JMP,
    K_JMPIND,
    K_JSR,
    K_NEXT,
    K_RTI,
    K_RTS,
    S_AUX,
    S_B0,
    S_B1,
    S_B2,
    S_CTRL,
    S_CYC,
    S_E0,
    S_E1,
    S_EK,
    S_F,
    S_KIND,
    S_N,
    S_NB,
    S_PEN,
    S_PH0,
    S_RD,
    S_RET,
    S_STABLE,
    S_WR,
    WROTE,
    build,
    recompile,
    stable,
)

PH_INIT, PH_PLAY = 1, 2
ACKS = (0xD019, 0xDC0D, 0xDD0D)
REG_IN = 0x10000  # synthetic input addresses for live-in A/X/Y
MAX_CELL_VALUES = 16

__all__ = ["TraceVM", "input_kind", "IDX_REG", "PH_INIT", "PH_PLAY", "REG_IN", "MAX_CELL_VALUES"]


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


class TraceVM(FlowRecorder, PcodeVM):
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
        self.io = self.bank == "io"
        self.cia = (CIA(CIA1_BASE), CIA(CIA2_BASE))
        self.known = bytearray(0x10000)
        self.known[image.lo : image.hi] = b"\1" * (image.hi - image.lo)
        self.known[0x100:0x200] = b"\1" * 0x100
        self.code = bytearray(0x10000)
        self.inband = bytearray(0x10000)
        self.inband[image.lo : image.hi] = b"\1" * (image.hi - image.lo)
        self.blocks = {}
        self.at = [None] * 0x10000
        self.vol = bytearray(0x10000)
        self.stack_code = False
        self.init_flow()
        self.written_init = set()
        self.written_play = set()
        self.wset = self.written_init
        self.chip_ops = set()
        self.wr_values = {}
        self.init_writes = []
        self.inputs = []
        self.input_sites = {}
        # column arrays (call, addr, val, cycle); _wr fully overrides the base wlog hook
        self.sidlog = tuple(array(t) for t in "IHBI")
        self.iolog = tuple(array(t) for t in "IHBI")
        self.tick_rd = self.tick_wr = 0
        self.pinned = False

    # ---- per-op attributed memory ------------------------------------------
    def read(self, addr, sz, pci, s):
        if sz == 1:
            s.add(addr)
            if IO_LO <= addr <= IO_HI:
                return self._chip_rd(addr, pci)
            v = self.mem[addr]
            if self.known[addr]:
                return v
            return self._input(addr, v, pci, "uninit_ram")
        v = 0
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            s.add(a)
            v |= self._read1(a, pci) << (8 * k)
        return v

    def _read1(self, a, pci):
        if IO_LO <= a <= IO_HI:
            return self._chip_rd(a, pci)
        v = self.mem[a]
        if self.known[a]:
            return v
        return self._input(a, v, pci, "uninit_ram")

    def _chip_rd(self, a, pci):
        """A ``$D000-$DFFF`` read: the chip when the port maps it, else the RAM under it.

        The CIA model of :mod:`.machine` answers every ``$DCxx``/``$DDxx`` timer
        and ICR read, superseding the base VM's ``ciaicr`` flag: a ``TraceVM``
        driven by ``run_irq_driven`` would not see that driver's raised source.
        """
        if not self.io:
            return self.mem[a]
        v = self.override.get(a)
        if v is None:
            v = self.cia[0].read(a, self.cycles)
        if v is None:
            v = self.cia[1].read(a, self.cycles)
        if v is None:
            v = PcodeVM._rd(self, a, 1)
        self.chip_ops.add(pci)
        return self._input(a, v, pci, input_kind(a))

    def _input(self, addr, value, pci, kind):
        site = pci[0]
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
        self.inputs.append((self.call, site, pci[1], addr, value))
        return value

    def write(self, addr, val, sz, pci, s):
        """One store, attributed to ``pci`` and ``s``.

        Every 6510 store is one byte; the wide case is a residualised 16-bit cell
        write, and keeping the two apart is worth 3-6 % of the whole trace.
        """
        mem = self.mem
        if sz == 1:
            a = addr & 0xFFFF
            s.add(a)
            b = val & 0xFF
            if IO_LO <= a <= IO_HI and self.io:
                self.chip_ops.add(pci)
                if self.code[a]:
                    self._drop(a)
                self._io_write(a, b)
            else:
                if self.known[a] != WROTE:
                    self._mark_written(a)
                self.wset.add(a)
                if self.inband[a]:
                    self._cell_value(a, b)
            mem[a] = b
        else:
            for k in range(sz):
                a = (addr + k) & 0xFFFF
                b = (val >> (8 * k)) & 0xFF
                s.add(a)
                if IO_LO <= a <= IO_HI and self.io:
                    self.chip_ops.add(pci)
                    if self.code[a]:
                        self._drop(a)
                    self._io_write(a, b)
                else:
                    if self.known[a] != WROTE:
                        self._mark_written(a)
                    self.wset.add(a)
                    if self.inband[a]:
                        self._cell_value(a, b)
                mem[a] = b
        if addr <= 1:
            self.bank = port_bank(mem)
            self.io = self.bank == "io"

    def _mark_written(self, a):
        """First write to ``a``: an executed instruction covering it is no longer stable."""
        self.known[a] = WROTE
        if self.code[a]:
            self._drop(a)

    def _drop(self, a):
        """``a`` moved: every pc whose instruction bytes cover it re-reads them from now on."""
        at, vol = self.at, self.vol
        for b in (a, (a - 1) & 0xFFFF, (a - 2) & 0xFFFF):
            at[b] = None
            vol[b] = 1

    def invalidate(self):
        """Drop the whole inline cache: memory changed behind the tracer's back."""
        self.at = [None] * 0x10000

    def _cell_value(self, a, b):
        """Record one byte written to an in-band address, up to the per-cell cap."""
        vs = self.wr_values.get(a)
        if vs is None:
            vs = self.wr_values[a] = set()
        if len(vs) < MAX_CELL_VALUES:
            vs.add(b)

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

    # ---- recorded structures -----------------------------------------------
    def insn_count(self):
        """Instructions executed, in every phase: the sum of the site counts."""
        return sum(t[S_N] for t in self.blocks.values())

    def enter_play(self):
        """Leave init: an init site executed again from here also carries the play phase."""
        self.phase = PH_PLAY
        self.wset = self.written_play
        for t in self.blocks.values():
            t[S_PH0] = t[S_N]

    def begin_tick(self, call):
        """Start one tick: the entry registers are live-in again."""
        self.call = call
        self.tick_rd = self.tick_wr = 0
        self.pinned = False

    def _push(self, val):
        """Push one byte; a driver frame over executed code makes those pcs re-read."""
        a = 0x100 + self.reg[3]
        if self.stack_code:
            self._drop(a)
        self.mem[a] = val & 0xFF
        self.reg[3] = (self.reg[3] - 1) & 0xFF

    # ---- one instruction ---------------------------------------------------
    def step(self, pc, cache, lifter):
        """One instruction: fetch, execute, resolve, dispatch and account, in one pass.

        The per-pc inline cache answers directly for a site no store has touched;
        a volatile pc re-reads its bytes and re-keys.
        """
        t = self.at[pc]
        if t is None:
            t = self._fetch(pc, self.mem[pc], cache, lifter)
        elif not t[S_STABLE]:
            mem = self.mem
            b0 = mem[pc]
            if t[S_B0] != b0:
                t = self._fetch(pc, b0, cache, lifter)
            else:
                nb = t[S_NB]
                if nb and (
                    t[S_B1] != mem[(pc + 1) & 0xFFFF]
                    or (nb == 2 and t[S_B2] != mem[(pc + 2) & 0xFFFF])
                ):
                    t = self._fetch(pc, b0, cache, lifter)
        t[S_N] += 1
        reg = self.reg
        pinned = self.pinned
        if not pinned:
            r0 = (reg[0], reg[1], reg[2])
        t[S_F]()
        rd = t[S_RD]
        wr = t[S_WR]
        if not pinned:
            live = rd & ~self.tick_wr & 7
            if live:
                for j in (0, 1, 2):
                    if live & (1 << j) and not self.tick_rd & (1 << j):
                        self._input(REG_IN + j, r0[j], (pc, 0), "entry_reg")
            tr = self.tick_rd | rd
            tw = self.tick_wr | wr
            self.tick_rd = tr
            self.tick_wr = tw
            if (tr | tw) & 7 == 7:
                self.pinned = True
        f = self.top
        if f is not None:
            f[3] |= rd & ~f[4]
            f[4] |= wr
        k = t[S_KIND]
        if k == K_NEXT:
            cyc = t[S_CYC]
            p = t[S_PEN]
            if p is not None:  # index slot, indirect, base
                b = p[2]
                if p[1]:
                    mem = self.mem
                    b = mem[b] | (mem[(b + 1) & 0xFF] << 8)
                if (b & 0xFF00) != ((b + reg[p[0]]) & 0xFF00):
                    cyc += 1
            self.cycles += cyc
            t[S_E0][1] += 1
            return t[S_RET]
        if k == K_BR:
            ctrl = t[S_CTRL]
            if reg[ctrl[1][1]] == ctrl[2]:
                nxt = ctrl[3]
                self.cycles += t[S_CYC] + (2 if (ctrl[4] ^ nxt) & 0xFF00 else 1)
                e = t[S_E0]
                if e is None:
                    e = t[S_E0] = self.edge_slot(t[S_EK], nxt, "br_taken")
            else:
                nxt = ctrl[4]
                self.cycles += t[S_CYC]
                e = t[S_E1]
                if e is None:
                    # a zero displacement makes both directions the same target,
                    # and the taken label is the one that names it
                    kind = "br_taken" if nxt == ctrl[3] else "br_not"
                    e = t[S_E1] = self.edge_slot(t[S_EK], nxt, kind)
            e[1] += 1
            return nxt
        return self._control(t, pc, k)

    def _fetch(self, pc, b0, cache, lifter):
        """The block for the bytes now at ``pc``, and the per-pc inline cache entry."""
        n = ILEN[b0]
        if n == 3:
            key = (pc, b0, self.mem[(pc + 1) & 0xFFFF], self.mem[(pc + 2) & 0xFFFF])
        elif n == 2:
            key = (pc, b0, self.mem[(pc + 1) & 0xFFFF])
        else:
            key = (pc, b0)
        t = cache.get(key)
        if t is None:
            t = self._site(key, cache, lifter, pc, b0, n)
        t[S_STABLE] = not self.vol[pc]
        self.at[pc] = t
        return t

    def _site(self, key, cache, lifter, pc, b0, n):
        """The site record of ``key``: built on first sight, rebound after a resume."""
        if lifter is None:
            raise KeyError("cache miss at %04X (SMC needs a lifter)" % pc)
        t = self.blocks.get(key)
        rec = lifter(self.mem, pc)
        if t is None:
            t = self.blocks[key] = build(self, pc, key, rec)
            code = self.code
            for k in range(n):
                code[(pc + k) & 0xFFFF] = 1
            if 0x100 <= pc <= 0x1FF:
                self.stack_code = True
            if not stable(self.known, pc, n):
                self.vol[pc] = 1
        else:
            recompile(self, pc, b0, rec, t)
        cache[key] = t
        return t

    def _control(self, t, pc, k):
        """Next pc for every control kind but fall-through and branch, and its edge."""
        mem = self.mem
        reg = self.reg
        self.cycles += t[S_CYC]
        ctrl = t[S_CTRL]
        if k == K_JSR:
            ret = (t[S_RET] - 1) & 0xFFFF
            if self.stack_code:
                self._drop(0x100 + reg[3])
                self._drop(0x100 + ((reg[3] - 1) & 0xFF))
            mem[0x100 + reg[3]] = ret >> 8
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = ret & 0xFF
            reg[3] = (reg[3] - 1) & 0xFF
            nxt = ctrl[1]
            t[S_E0][1] += 1
            c = t[S_AUX]
            c["targets"][nxt] += 1
            c["count"] += 1
            self.push_frame(pc, t[S_RET], nxt)
            return nxt
        if k == K_RTS:
            reg[3] = (reg[3] + 1) & 0xFF
            lo = mem[0x100 + reg[3]]
            reg[3] = (reg[3] + 1) & 0xFF
            nxt = ((mem[0x100 + reg[3]] << 8) | lo) + 1 & 0xFFFF
            self._return(t[S_AUX], nxt)
            return nxt
        if k == K_RTI:
            reg[3] = (reg[3] + 1) & 0xFF
            self._set_flags(mem[0x100 + reg[3]])
            reg[3] = (reg[3] + 1) & 0xFF
            lo = mem[0x100 + reg[3]]
            reg[3] = (reg[3] + 1) & 0xFF
            nxt = (mem[0x100 + reg[3]] << 8) | lo
            self._return(t[S_AUX], nxt)
            return nxt
        if k == K_JMP:
            t[S_E0][1] += 1
            return ctrl[1]
        if k == K_JMPIND:
            ptr = ctrl[1]
            nxt = mem[ptr] | (mem[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)
            self._varying_edge(t, nxt, "jmpind")
            return nxt
        if k == K_BRK:
            ret = (pc + 2) & 0xFFFF
            if self.stack_code:
                for d in (0, 1, 2):
                    self._drop(0x100 + ((reg[3] - d) & 0xFF))
            mem[0x100 + reg[3]] = ret >> 8
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = ret & 0xFF
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = self._status(brk=1)
            reg[3] = (reg[3] - 1) & 0xFF
            reg[10] = 1
            nxt = mem[0xFFFE] | (mem[0xFFFF] << 8)
            self._varying_edge(t, nxt, "brk")
            return nxt
        raise RuntimeError("JAM at %04X" % pc)

    def __getstate__(self):
        """Pickle without the compiled closures; :meth:`_site` rebinds them on resume."""
        st = super().__getstate__()
        d, slots = st if isinstance(st, tuple) else (st, None)
        d = dict(d or ())
        d["blocks"] = {k: [None] + t[1:] for k, t in self.blocks.items()}
        d.pop("at", None)
        return (d, slots) if slots is not None else d

    def __setstate__(self, st):
        d, slots = st if isinstance(st, tuple) else (st, None)
        self.__dict__.update(d)
        for k, v in (slots or {}).items():
            setattr(self, k, v)
        self.at = [None] * 0x10000
