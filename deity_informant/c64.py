"""The C64 host of bank-aware whole-program lifting, over vagrant-moses' native machine.

:func:`bank_map` describes the 6510 port and PLA configurations for the native lifter;
:func:`machine_facts` records a run's :class:`~deity_informant.trace.Facts` with banks and
entry ports; :func:`machine_session` records each call's halt, registers, SID log and RAM.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import NamedTuple, Optional

import numpy as np
from jennings.opcodes import MEM_MODES, OPCODES
from vagrant_moses import memmap as mm
from vagrant_moses.bus import banking, port_read
from vagrant_moses.roms import load_roms, rom_less

from .smc.classify import expected_reads, expected_writes, view
from .smc.machine import SENTINEL, SMC_NATIVE, MachineOracle, _Mem
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
    "bank_map",
    "real_roms",
    "machine_facts",
    "machine_record",
    "machine_session",
    "mismatch",
    "check",
    "device_file",
    "lift_check",
    "LiftCheck",
    "IDLE",
    "TRACE_NATIVE",
    "FORMS",
    "trace_table",
]

RAM, ROM, IO = 0, 1, 2
#: What the port's undriven (input) data bits read as.
PULL = port_read(0, 0)
ROM_NAMES = ("basic", "chargen", "kernal")
SID = (mm.SID_BASE, mm.SID_LAST + 1)
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


def _reg(r):
    """The 16-entry register file of ``Program.call`` results from the machine's MPU."""
    reg = [r.a, r.x, r.y, r.sp] + [0] * 12
    for i, bit in _FLAGS:
        reg[i] = (r.p >> bit) & 1
    return reg


class _Sid:
    """A 32-byte SID window at ``base``: reads give the last value written there (else the
    image byte); every access goes to the host."""

    __slots__ = ("base", "regs", "host")

    def __init__(self, base, regs, host):
        self.base, self.regs, self.host = base, regs, host

    def read(self, _cycle, reg):
        v = self.regs[reg]
        self.host.access(self.base + reg, v, False)
        return v

    def write(self, _cycle, reg, value):
        self.regs[reg] = value
        self.host.access(self.base + reg, value, True)


class _Host(MachineOracle):
    """A machine run recording, per queued call, facts (calls only) and a snapshot at its end.

    ``backend`` ``native`` collects them in vagrant-moses' trace beside its SMC recorder,
    ``python`` (the reference) from this decode hook and Python SID devices; the default is
    ``native`` where the trace exists."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments

    def __init__(self, image, sid=SID, roms=None, ntsc=False, backend=None):
        self.sid = sid
        backend = backend or ("native" if TRACE_NATIVE else "python")
        if backend == "native":
            if not TRACE_NATIVE:
                raise ValueError("backend 'native' is not available")
            super().__init__(image, ntsc=ntsc, roms=roms, backend="native")
            self.tr = Tracer(self.rec, trace_table(), self.sentinel, sid, image)
        else:
            devices = {
                b: _Sid(b, bytearray(image[b : b + 0x20]), self)
                for b in range(sid[0], sid[1], 0x20)
            }
            super().__init__(image, ntsc=ntsc, devices=devices, roms=roms, backend="python")
            self.tr = None
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
        """A SID access by the running instruction."""
        if self.cur is None:
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
        outside the SID windows from the register a plain store wrote."""
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
        lo, hi = self.sid
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
    over ``image`` (64 KiB) on the native machine; ``ports`` as ``MachineOracle.run``.
    ``banks`` holds effective port bits (:func:`bank_map` configuration indices)."""
    return _Host(image, roms=roms).drive(calls, init, max_cycles, ports).facts()


def machine_session(
    image, calls, init=(), ports=None, max_cycles=2_000_000, sid=SID, roms=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """``(init records, call records)`` of one run, ``{"halt", "reg", "log", "ram"}`` per call
    as it returned or stopped: ``halt`` ``"return"``/``"cycles"``/``"jam"``, ``reg`` the
    ``Program.call`` register file, ``log`` the ``(addr, value, is_write)`` ``sid``-range
    accesses in order, ``ram`` the 64 KiB RAM with the port registers at $00/$01."""
    host = _Host(image, sid, roms).drive(calls, init, max_cycles, ports)
    out = ([], [])
    for phase, halt, reg, log, ram in host.snaps:
        out[phase == "call"].append({"halt": halt, "reg": reg, "log": log, "ram": ram})
    return out


def machine_record(
    image, calls, init=(), ports=None, max_cycles=2_000_000, sid=SID, roms=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The call records of :func:`machine_session`."""
    return machine_session(image, calls, init, ports, max_cycles, sid, roms)[1]


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


def device_file(image, ram, inits, sid=SID):
    """The device register file a banked lift's calls start from, as the machine holds it
    after the ``inits`` records: ``ram``, its ``sid`` window ``image``'s bytes with each
    logged write applied."""
    io = bytearray(ram)
    io[sid[0] : sid[1]] = image[sid[0] : sid[1]]
    for rec in inits:
        for a, v, w in rec["log"] or ():
            if w:
                io[a] = v
    return io


def check(
    prog, image, reg, records, port=None, max_cycles=2_000_000, accs=(), io=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Run ``prog``'s entry 0 from ``image`` and registers ``reg`` once per machine record,
    with A from ``accs`` (0 past its end) and ``port`` (when given) stored to $01 before each
    call, its devices on ``io`` (:func:`device_file`; a copy of ``image`` when absent) kept
    across the calls; ``[call, *mismatch]`` of the first, or None."""
    mem = bytearray(image)
    io = bytearray(image) if io is None else io
    for n, ref in enumerate(records):
        if port is not None:
            mem[1] = port
        a = accs[n] if n < len(accs) else 0
        o = prog.call(mem, 0, a=a, reg=reg, log=True, max_cycles=max_cycles, io=io)
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
    prog = wholeprog.lift(last["ram"], [entry], [SID], facts, cycles=cycles, bank=bank_map(roms))
    port = (ports or {}).get("call")
    accs = [a for _, a in calls]
    io = device_file(image, last["ram"], inits)
    bad = check(prog, last["ram"], last["reg"], records, port, max_cycles, accs, io)
    return LiftCheck(bad, prog, facts, records, last)
