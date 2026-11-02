"""The C64 host of bank-aware whole-program lifting, over vagrant-moses' native machine.

:func:`bank_map` describes the 6510 port and PLA configurations for the native lifter;
:func:`machine_facts` records a run's :class:`~deity_informant.trace.Facts` with banks and
entry ports; :func:`machine_session` records each call's halt, registers, I/O log and RAM.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import NamedTuple, Optional

import numpy as np
import pylibresidfp
from jennings.opcodes import MEM_MODES, OPCODES
import vagrant_moses as vm
from vagrant_moses import memmap as mm
from vagrant_moses.bus import banking, port_read
from vagrant_moses.c64 import poweron_ram
from vagrant_moses.roms import BASIC_RUN, RomFetchError, load_roms, poweron_writes, rom_less

from .smc.classify import expected_reads, expected_writes, view
from .smc.machine import READ, READ_BACK, SENTINEL, SMC_NATIVE, MachineOracle, _Mem
from .smc.oracle import writer_of
from . import wholeprog
from .trace import Facts

try:
    from vagrant_moses.smc import TRACE as _TRACE
    from vagrant_moses.smc import Tracer
except ImportError:  # a vagrant-moses without the trace
    _TRACE, Tracer = False, None

#: Whether the native trace collects facts and records (else the reference decode hook).
TRACE_NATIVE = bool(SMC_NATIVE and _TRACE)

__all__ = [
    "RAM",
    "ROM",
    "IO",
    "PULL",
    "ROM_NAMES",
    "SID",
    "DEVICES",
    "bank_map",
    "boot",
    "Boot",
    "driver_io",
    "iomap",
    "READ",
    "READ_BACK",
    "real_roms",
    "machine_facts",
    "machine_record",
    "machine_session",
    "mismatch",
    "check",
    "reads",
    "lift_check",
    "LiftCheck",
    "IDLE",
    "sentinel_for",
    "TRACE_NATIVE",
    "FORMS",
    "trace_table",
]

RAM, ROM, IO = 0, 1, 2
#: What the port's undriven (input) data bits read as.
PULL = port_read(0, 0)
ROM_NAMES = ("basic", "chargen", "kernal")
SID = (mm.SID_BASE, mm.SID_LAST + 1)
#: The I/O window the machine logs and banked lifts treat as volatile: VIC, SID, colour RAM,
#: CIAs and the expansion area.
DEVICES = (0xD000, 0xE000)
_MASK = mm.LORAM | mm.HIRAM | mm.CHAREN
_PUSHES = {"PHA": 1, "PHP": 1, "JSR": 2, "BRK": 3}
_STORED = {
    "STA": lambda a, x, y: a,
    "STX": lambda a, x, y: x,
    "STY": lambda a, x, y: y,
    "SAX": lambda a, x, y: a & x,
}
_FLAGS = ((8, 0), (9, 1), (10, 2), (11, 3), (13, 6), (14, 7))
#: The native trace's address-form codes (``vagrant_moses.smc.Tracer``).
FORMS = {
    m: i
    for i, m in enumerate(("zp", "zpx", "zpy", "abs", "absx", "absy", "indx", "indy", "ind"), 1)
}
_REG = {mn: i for i, mn in enumerate(_STORED, 1)}
_PHASE = {"init": 1, "call": 2}


@lru_cache(maxsize=None)
def trace_table():
    """The native trace's opcode table (``vagrant_moses.smc.Tracer``): per opcode, the
    address form, whether it reads or stores there, the register a plain store stores and
    the bytes it pushes, as :meth:`_Host._effects` derives them."""
    t = np.zeros(256, "<u2")
    for op in range(256):
        (mn, mode), w = OPCODES[op], writer_of(op)
        store = w is not None and w[0] == "cpu" and not w[2]
        read = mode in MEM_MODES and mn not in ("JSR", "JMP") and not store
        flags = read << 4 | (w is not None) << 5 | _REG.get(mn, 0) << 6 | _PUSHES.get(mn, 0) << 9
        t[op] = flags | FORMS.get(mode, 0) if flags or mode == "ind" else 0
    return t.tobytes()


def _config(bits):
    """One byte per 256-byte page: what the CPU sees there under effective port bits ``bits``."""
    basic, kernal, chargen, io = banking(bits)
    c = np.full(256, RAM, np.uint8)
    c[0xA0:0xC0] = ROM if basic else RAM
    c[0xD0:0xE0] = IO if io else ROM if chargen else RAM
    c[0xE0:] = ROM if kernal else RAM
    return c.tobytes()


def bank_map(roms=None):
    """The C64 bank map for the native lifter; ``roms`` (base -> bytes) overrides the ROM-less
    set per ROM. The configuration index is the effective port value
    ``(data | ~dir) & mask``; the data cell reads ``(data & dir) | (~dir & pull)``."""
    rom = bytearray(0x10000)
    for base, data in {**rom_less(), **(roms or {})}.items():
        rom[base : base + len(data)] = data
    return {
        "dir": mm.CPU_DDR,
        "data": mm.CPU_PORT,
        "mask": _MASK,
        "pull": PULL,
        "lut": list(range(_MASK + 1)),
        "configs": [_config(b) for b in range(_MASK + 1)],
        "rom": bytes(rom),
    }


def real_roms(**kwargs):
    """The real BASIC, CHARGEN and KERNAL by base, SHA-256 verified by vagrant-moses (from
    ``$C64_ROMS``, its cache or its mirror); ``kwargs`` go to its ``resolve_rom``.

    Raises:
      vagrant_moses.roms.RomFetchError: a ROM is unavailable or failed its checksum.
    """
    return load_roms(ROM_NAMES, **kwargs)


#: BASIC's NEWSTT (per statement), sidplayfp's subtune hook in free BASIC ROM, its stop-key test
#: and the SYS accumulator ``SAREG`` the hook loads with the song.
NEWSTT, SUBTUNE_HOOK, STOP_TEST, SAREG = 0xA7AE, 0xBF53, 0xA82C, 0x030C
#: The KERNAL IRQ's exit (acknowledge CIA 1, restore A/X/Y, RTI): the code of ``psiddrv.a65``'s
#: ``irqret``, where sidplayfp points ``$0314`` for an RSID without the BASIC flag or a PSID.
IRQ_EXIT = 0xEA7E
#: The ROM-less KERNAL's reset entry, a JAM (``KernalRomBank::set``): ``psiddrv.a65``'s
#: ``exception``, where sidplayfp points a PSID's BRK and NMI vectors.
JAM = 0xFCE2


class Boot(NamedTuple):
    """A machine start: RAM ``image``, ``roms`` by base, the ``init`` call ``(pc, a)``, the
    host's ``io`` accesses ``(cycle, addr, value)`` and the cycle init starts (from the reset),
    the ``port`` ($01) init runs with and the address it ``returns`` to."""

    image: bytearray
    roms: Optional[dict]
    init: tuple
    io: tuple
    start: int
    port: int
    returns: int

    def run_kw(self):
        """The :meth:`~deity_informant.smc.machine.MachineOracle.run` arguments of this start."""
        return {"init": [self.init], "ports": {"init": self.port}, "io": self.io,
                "start": self.start, "returns": self.returns}  # fmt: skip


def iomap(addr):
    """The $01 value sidplayfp banks in for PSID code at ``addr`` (``psiddrv::iomap``)."""
    if addr < 0xA000:
        return 0x37
    if addr < 0xD000:
        return 0x36
    return 0x35 if addr >= 0xE000 else 0x34


@lru_cache(maxsize=None)
def driver_io(ntsc, init_iomap=0, cia=True):
    """``(io, start)``: the I/O accesses of libsidplayfp's ``psiddrv.a65`` cold start, each in
    the cycle its instruction makes it, and the cycle init's first opcode is fetched (from the
    reset), for a tune without a play routine at the tune's clock. ``init_iomap`` is 0 for an
    RSID, else the PSID's; ``cia`` starts CIA 1 timer A with its IRQ, else enables the raster's."""
    latch = mm.video(ntsc).kernal_latch
    raster = (0x1B, 0x00) if init_iomap else (0x9B, 0x37)
    ops = [(2,), (2,), (4, mm.VIC_IRQ_ENABLE, 0), (4, mm.VIC_IRQ_STATUS, READ),
           (4, mm.VIC_IRQ_STATUS, READ_BACK), (2,), (4, mm.CIA1_ICR, 0x7F), (4, mm.CIA2_ICR, 0x7F),
           (4, mm.CIA1_ICR, READ), (4, mm.CIA2_ICR, READ), (2,), (4, mm.SID_BASE + 0x18, 0x0F),
           (4,)]  # fmt: skip
    ops += [(3,), (2,), (2,)] if ntsc else [(2,), (2,), (2,), (3,)]
    ops += [(4, mm.CIA1_TIMER_A_LO, latch & 0xFF), (4, mm.CIA1_TIMER_A_HI, latch >> 8)]
    ops += [(2,), (2,), (4,), (4,)] + ([(2,), (2,), (2,)] if init_iomap else [(3,)])
    ops += [(4, mm.VIC_CONTROL_1, raster[0]), (4, mm.VIC_RASTER, raster[1]), (4,)]
    ops += [(2,), (4,), (3,)] if init_iomap else [(3,)]
    ops += [(4,)]
    if cia:
        ops += [(3,), (2,), (2,), (4, mm.CIA1_ICR, 0x81), (4, mm.CIA1_CRA, 0x01)]
    else:
        ops += [(2,), (2,), (4, mm.VIC_IRQ_ENABLE, 0x81), (3,)]
    ops += [(4,)] + ([(3,)] if init_iomap else [(2,), (2,)])
    ops += [(3,), (4,), (3,), (4,), (4,), (6,), (5,)]
    io, t = [], 0
    for n, *access in ops:
        t += n
        if access:
            io.append((t - 1, *access))
    return tuple(io), t


def boot(image, load, end, init, song, ntsc=False, kind="RSID", cia=True, roms=None):
    """The :class:`Boot` of a song without a play routine as sidplayfp starts it (``psiddrv``,
    ``SidTuneBase::placeSidTuneInC64mem``, SID_file_format.txt; see docs/corpus.md): ``kind``
    ``RSID``, ``BASIC`` (an RSID with the C64 BASIC flag) or ``PSID`` (``cia`` its speed bit),
    its data at ``[load, end)`` of ``image``. An RSID's ``roms`` default to :func:`real_roms`.

    Raises:
      vagrant_moses.roms.RomFetchError: a ROM or the booted-KERNAL image is unavailable.
    """
    rsid = kind != "PSID"
    img = bytearray(image)
    img[:0x3FF] = bytes(0x3FF)
    if rsid:
        runs = poweron_writes()
        if runs is None:
            raise RomFetchError("poweron: unavailable (offline, not cached)")
        for addr, data in runs:
            img[addr : addr + len(data)] = data
        roms = dict(roms or real_roms())
    img[0x02A6] = 0 if ntsc else 1
    vectors = () if kind == "BASIC" else (IRQ_EXIT,) if rsid else (IRQ_EXIT, JAM, JAM)
    vec = b"".join(v.to_bytes(2, "little") for v in vectors)
    img[mm.RAM_IRQ_VECTOR_LO : mm.RAM_IRQ_VECTOR_LO + len(vec)] = vec
    for addr, v in ((0x2D, end), (0x2F, end), (0x31, end), (0xAC, load), (0xAE, end)):
        img[addr : addr + 2] = (v & 0xFFFF).to_bytes(2, "little")
    img[load:end] = image[load:end]
    if kind == "BASIC":
        basic = bytearray(roms[mm.BASIC_BASE])
        for addr, code in (
            (NEWSTT, b"\x4c" + SUBTUNE_HOOK.to_bytes(2, "little")),
            (SUBTUNE_HOOK, bytes([0xA9, song - 1, 0x8D]) + SAREG.to_bytes(2, "little") + b"\x20"
             + STOP_TEST.to_bytes(2, "little") + b"\x4c" + BASIC_RUN.to_bytes(2, "little")),
        ):  # fmt: skip
            basic[addr - mm.BASIC_BASE : addr - mm.BASIC_BASE + len(code)] = code
        roms[mm.BASIC_BASE] = bytes(basic)
        init = SUBTUNE_HOOK + 2
    port = 0 if rsid else iomap(init)
    io, start = driver_io(ntsc, port, cia or rsid)
    code, ret = _driver_tail(rsid, init)
    img[SENTINEL - len(code) : SENTINEL] = code
    if not rsid:
        init, start = SENTINEL - len(code), start - 5
    return Boot(img, roms, (init, song - 1), io, start, port or mm.BANK_DEFAULT, ret)


def _driver_tail(rsid, init):
    """``(code, returns)``: code before the sentinel standing in for ``psiddrv.a65``'s, cycle for
    cycle, and where init returns in it. An RSID's init returns to ``lda``/``beq idle``; a PSID's
    enters by ``sei``/``jmp init`` (``plp``'s I flag, ``jmp (initvec)``) and returns to
    ``lda``/``beq``/``lda``/``beq run``/``cli``; each ``lda`` reads a byte of the code."""
    s = SENTINEL
    if rsid:
        return bytes([0xAD, *(s - 1).to_bytes(2, "little"), 0xF0, 0x00]), s - 5
    tail = bytes([0xAD, *(s - 11).to_bytes(2, "little"), 0xF0, 0x00])
    tail += bytes([0xAD, *(s - 2).to_bytes(2, "little"), 0xF0, 0x00, 0x58])
    return bytes([0x78, 0x4C, *init.to_bytes(2, "little")]) + tail, s - len(tail)


def _reg(r):
    """The 16-entry register file of ``Program.call`` results from the machine's MPU."""
    reg = [r.a, r.x, r.y, r.sp] + [0] * 12
    for i, bit in _FLAGS:
        reg[i] = (r.p >> bit) & 1
    return reg


def sentinel_for(image):
    """Where a run of ``image`` puts the oracle's return trap: :data:`SENTINEL` unless
    ``image`` defines any of its 3 bytes, else the highest such run below $D000 above the
    stack that still holds power-on RAM."""
    free = (
        np.frombuffer(bytes(image), np.uint8)[:0xD000]
        == np.frombuffer(poweron_ram(), np.uint8)[:0xD000]
    )
    ok = np.lib.stride_tricks.sliding_window_view(free, 3).all(1)
    ok[:0x200] = False
    return SENTINEL if ok[SENTINEL] else int(np.flatnonzero(ok)[-1])


class _Chip:
    """The $D400 SID: vagrant-moses' bus model, a libresidfp chip answering $D419-$D41C."""

    def __init__(self, model):
        self.chip = pylibresidfp.SID(model, filter=False)
        self.sid = vm.NativeSid(model, self.read)

    def read(self, cycle, reg):
        """Register ``reg`` at machine cycle ``cycle``, after the writes logged since."""
        w = np.array(self.sid.drain_writes(), np.int64).reshape(-1, 3)
        return self.chip.serve(w, cycle, reg)


class _Host(MachineOracle):
    """A machine run recording, per queued call, facts (calls only) and a snapshot at its end.

    The SID at $D400 is a :class:`_Chip` of ``model`` (``6581``/``8580``); every CPU access
    to ``window`` is logged as the machine performed it. ``backend`` ``native`` collects
    facts and logs in vagrant-moses' trace beside its SMC recorder, ``python`` (the
    reference) from this decode hook and the machine's I/O hook; default ``native``."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments

    def __init__(
        self, image, window=DEVICES, roms=None, ntsc=False, backend=None, model="6581"
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.window = window
        backend = backend or ("native" if TRACE_NATIVE else "python")
        if backend == "native" and not TRACE_NATIVE:
            raise ValueError("backend 'native' is not available")
        sids = {mm.SID_BASE: _Chip(model).sid}
        hook = None if backend == "native" else self.access
        super().__init__(
            image,
            ntsc=ntsc,
            devices=sids,
            sentinel=sentinel_for(image),
            roms=roms,
            backend=backend,
            on_io=hook,
        )
        self.tr = (
            Tracer(self.rec, trace_table(), self.sentinel, window) if backend == "native" else None
        )
        self.cur = None
        self.log = []
        self.last = None
        self.sp0 = 0
        self.trace = []
        self.folded = (np.zeros(0, np.int64), np.zeros(0, np.int64))
        self.pending, self.npending = [], 0
        self.snaps = []
        self.writes = defaultdict(set)
        self.page1 = {}
        self.entry_sp = defaultdict(set)
        self.entry_port = defaultdict(set)
        self.result = None

    def access(self, addr, value, write):
        """An I/O access by the running instruction."""
        if self.cur is None or not self.window[0] <= addr < self.window[1]:
            return
        self.log.append((addr, value, write))
        if write and self.cur == "call":
            self.writes[addr].add(value)

    def _page1(self, pc, addrs):
        for a in addrs:
            if 0x100 <= a < 0x200:
                lo, hi = self.page1.get(pc, (a, a))
                self.page1[pc] = (min(lo, a), max(hi, a))

    def _effects(self, pc, fetched, a, x, y, sp, bits):
        """Record the stores and page-1 data accesses of the call instruction just executed,
        addressed over the RAM it started from. Values come from RAM after it; I/O stores
        outside the logged window from the register a plain store wrote."""
        mem = _Mem(pc, fetched, self.shadow)
        mn = OPCODES[fetched[0]][0]
        w = writer_of(fetched[0])
        store = w is not None and w[0] == "cpu" and not w[2]
        self._page1(pc, expected_reads(mem, pc, x, y, sp, store))
        if mn in _PUSHES:
            addrs = [0x100 | ((sp - k) & 0xFF) for k in range(_PUSHES[mn])]
        elif w is not None:
            addrs = expected_writes(mem, pc, x, y, sp)
            self._page1(pc, addrs)
        else:
            return
        lo, hi = self.window
        for addr in addrs:
            if view(bits, addr) != "io" or addr < 2:
                self.writes[addr].add(int(self.ram[addr]))
            elif not lo <= addr < hi and mn in _STORED:
                self.writes[addr].add(_STORED[mn](a, x, y))

    def _decode(self, mc):
        if self.last is not None:
            self._effects(*self.last)
            self.last = None
        super()._decode(mc)
        r = mc.mpu
        if self.cur == "call" and r.pc != self.sentinel and not self.timeout:
            self.trace.append((r.pc, r.sp - self.sp0, mc.bits))
            self.last = (r.pc, self.prev[1], r.a, r.x, r.y, r.sp, mc.bits)

    def _fold(self, merge=False):
        """Fold the current call's trace into the unique ``(pc, height, bits)`` keys and
        ``(pc, next)`` edges seen so far (sets, so memory saturates over long runs)."""
        if self.trace:
            t = np.array(self.trace, np.int64)
            self.trace = []
            pc = t[:, 0]
            self.npending += len(pc)
            self.pending.append(
                ((pc << 24) | ((t[:, 1] & 0xFFFF) << 8) | t[:, 2], (pc[:-1] << 16) | pc[1:])
            )
        if self.pending and (merge or self.npending > 1 << 18):
            keys, edges = zip(*self.pending)
            self.folded = tuple(
                np.unique(np.concatenate((old, *new)))
                for old, new in zip(self.folded, (keys, edges))
            )
            self.pending, self.npending = [], 0

    def _ram(self, _delta=None):
        """The RAM a call record keeps; ``_delta``: the native ``(idx, values)`` changed since
        the previous record."""
        return bytes(self.ram)

    def _snap(self, halt):
        if self.cur is None:
            return
        if self.tr is None:
            if self.cur == "call":
                self._fold()
            log, ram = self.log, self._ram()
        else:
            log, idx, vals = self.tr.snap()
            self.tr.phase(Tracer.NONE)
            ram = self._ram((np.frombuffer(idx, "<u2"), np.frombuffer(vals, np.uint8)))
        self.snaps.append((self.cur, halt, _reg(self.m.mpu), log, ram))
        self.cur, self.log = None, []

    def _trap(self, mc):
        self._snap("return")
        if self.queue:
            phase, pc, _ = self.queue[0]
            self.cur = phase
            if phase == "call":
                self.sp0 = (mc.mpu.sp - 2) & 0xFF
                self.entry_sp[pc].add(self.sp0)
            if self.tr is not None:
                self.tr.phase(_PHASE[phase], self.sp0)
        ok = super()._trap(mc)
        if self.cur == "call":
            self.entry_port[mc.mpu.pc].add((mc.port_dir, mc.port_data))
        return ok

    def drive(self, calls, init=(), max_cycles=2_000_000, ports=None):
        """Run ``init`` then ``calls``; a call that does not return ends the run. The run's
        SMC labelling is kept as :attr:`result`."""
        self.result = super().run(calls, init, call_cycles=max_cycles, ports=ports)
        if self.last is not None:
            self._effects(*self.last)
            self.last = None
        self._snap("jam" if self.m.jammed else "cycles")
        return self

    def facts(self):
        """The calls' :class:`~deity_informant.trace.Facts`."""
        entry = {
            "entry_sp": {a: sorted(v) for a, v in self.entry_sp.items()},
            "entry_port": {a: sorted(map(list, v)) for a, v in self.entry_port.items()},
        }
        if self.tr is not None:
            f = self.tr.facts()
            return Facts(
                starts=f.starts,
                edges=f.edges,
                writes=dict(f.writes),
                page1={p: [lo, hi] for p, lo, hi in f.page1},
                heights=dict(f.heights),
                banks=dict(f.banks),
                **entry,
            )
        self._fold(merge=True)
        keys, edges = self.folded
        pcs, hs, bs = keys >> 24, ((keys >> 8) & 0xFFFF) - ((keys >> 8) & 0x8000) * 2, keys & 0xFF

        def grouped(keys, vals):
            u = np.unique((keys << 16) | (vals & 0xFFFF))
            out = defaultdict(list)
            for k, v in zip((u >> 16).tolist(), (u & 0xFFFF).tolist()):
                out[k].append(v - 0x10000 if v & 0x8000 else v)
            return dict(out)

        return Facts(
            starts=np.unique(pcs).tolist(),
            edges=[(int(e >> 16), int(e & 0xFFFF)) for e in edges],
            writes={a: sorted(v) for a, v in sorted(self.writes.items())},
            page1={p: list(r) for p, r in sorted(self.page1.items())},
            heights=grouped(pcs, hs),
            banks=grouped(pcs, bs),
            **entry,
        )


def machine_facts(image, calls, init=(), ports=None, max_cycles=2_000_000, roms=None):
    """:class:`~deity_informant.trace.Facts` of ``calls`` (``[(pc, a)]``, after ``init``)
    over ``image`` (the whole address space) on the native machine; ``ports`` as
    ``MachineOracle.run``.
    ``banks`` holds effective port bits (:func:`bank_map` configuration indices)."""
    return _Host(image, roms=roms).drive(calls, init, max_cycles, ports).facts()


def machine_session(
    image, calls, init=(), ports=None, max_cycles=2_000_000, window=DEVICES, roms=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """``(init records, call records)`` of one run, ``{"halt", "reg", "log", "ram"}`` per call
    as it returned or stopped: ``halt`` ``"return"``/``"cycles"``/``"jam"``, ``reg`` the
    ``Program.call`` register file, ``log`` the ``(addr, value, is_write)`` I/O accesses to
    ``window`` in order, ``ram`` all of RAM with the port registers at $00/$01."""
    host = _Host(image, window, roms).drive(calls, init, max_cycles, ports)
    out = ([], [])
    for phase, halt, reg, log, ram in host.snaps:
        out[phase == "call"].append({"halt": halt, "reg": reg, "log": log, "ram": ram})
    return out


def machine_record(
    image, calls, init=(), ports=None, max_cycles=2_000_000, window=DEVICES, roms=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The call records of :func:`machine_session`."""
    return machine_session(image, calls, init, ports, max_cycles, window, roms)[1]


#: Register-file indices :func:`mismatch` compares: A X Y SP and the flags C Z I D V N.
REGS = (0, 1, 2, 3, 8, 9, 10, 11, 13, 14)


def mismatch(ref, out):
    """The first difference of a lifted call result ``out`` from the machine record ``ref``,
    or None: ``("no-return",)`` when both stop without returning, else ``(field, ...)``.
    The dead stack ``$0100-$0100+SP`` is not compared."""
    if ref["halt"] != "return" or out["halt"] != "return":
        return ("no-return",) if out["halt"] == ref["halt"] else ("halt", ref["halt"], out["halt"])
    log = [(a, v, w) for a, v, _, w in out["log"] or ()]
    want = [tuple(e) for e in ref["log"]]
    if log != want:
        return ("log", next((i for i, (p, q) in enumerate(zip(log, want)) if p != q),
                            min(len(log), len(want))))  # fmt: skip
    reg = [out["reg"][i] for i in REGS]
    if reg != [ref["reg"][i] for i in REGS]:
        return ("reg", [ref["reg"][i] for i in REGS], reg)
    diff = np.frombuffer(bytes(ref["ram"]), np.uint8) != np.frombuffer(bytes(out["ram"]), np.uint8)
    diff[0x100 : 0x101 + ref["reg"][3]] = False
    bad = np.flatnonzero(diff)
    return ("memory", int(bad[0])) if bad.size else None


def reads(rec):
    """The values a machine record's device reads returned, in order: what a check replays."""
    return bytes(v for _, v, w in rec["log"] or () if not w)


def check(
    prog, image, reg, records, port=None, max_cycles=2_000_000, accs=()
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Run ``prog``'s entry 0 from ``image`` and registers ``reg`` once per machine record,
    with A from ``accs`` (0 past its end) and ``port`` (when given) stored to $01 before each
    call, its volatile reads replaying the record's (:func:`reads`); ``[call, *mismatch]`` of
    the first, or None. A lift reading another address, or more or fewer times, than the
    machine differs in the log."""
    mem = bytearray(image)
    for n, ref in enumerate(records):
        if port is not None:
            mem[1] = port
        a = accs[n] if n < len(accs) else 0
        o = prog.call(mem, 0, a=a, reg=reg, log=True, max_cycles=max_cycles, reads=reads(ref))
        bad = mismatch(ref, {**o, "ram": mem})
        if bad is not None:
            return [n, *bad]
        reg = list(o["reg"])
    return None


#: An `RTS` beside the oracle's sentinel, run as the init call when none is given so the first
#: call starts from known registers.
IDLE = SENTINEL + 3


class LiftCheck(NamedTuple):
    """A :func:`lift_check`: the first mismatch (``[call, *mismatch]``) or None, the lifted
    program (None when init did not return), its facts, the machine's call records and its
    last init record."""

    bad: Optional[list]
    prog: object
    facts: Optional[Facts]
    records: list
    init: dict


def lift_check(
    image,
    entry,
    fact_calls,
    calls,
    init=None,
    ports=None,
    max_cycles=2_000_000,
    roms=None,
    cycles=False,
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Lift ``entry`` of ``image`` through :func:`bank_map` from machine facts over
    ``fact_calls`` and check ``calls`` (``[(pc, a)]``) against the machine, both after ``init``
    (default ``[(IDLE, 0)]``) with ``ports`` as ``MachineOracle.run``: a :class:`LiftCheck`."""
    image = bytearray(image)
    if init is None:
        image[IDLE], init = 0x60, [(IDLE, 0)]
    inits, records = machine_session(image, calls, init, ports, max_cycles, roms=roms)
    last = inits[-1]
    if last["halt"] != "return":
        return LiftCheck(None, None, None, records, last)
    facts = machine_facts(image, fact_calls, init, ports, max_cycles, roms)
    prog = wholeprog.lift(
        last["ram"], [entry], [DEVICES], facts, cycles=cycles, bank=bank_map(roms)
    )
    port = (ports or {}).get("call")
    accs = [a for _, a in calls]
    bad = check(prog, last["ram"], last["reg"], records, port, max_cycles, accs)
    return LiftCheck(bad, prog, facts, records, last)
