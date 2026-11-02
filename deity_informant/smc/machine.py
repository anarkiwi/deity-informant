"""The machine-level oracle: label a program from a cycle-exact vagrant-moses C64 run.

The native machine runs calls from a trapped sentinel. Backend ``python`` (the reference) diffs RAM
per instruction from a decode hook into :class:`Classifier`; ``native`` maps the events of
vagrant-moses' SMC recorder to the same cells, and is the default where the recorder exists.
"""

from __future__ import annotations


import weakref
from collections import deque
from itertools import groupby, repeat

import numpy as np
from vagrant_moses import machine as vm
from vagrant_moses import memmap as mm
from vagrant_moses.bus import port_read
from vagrant_moses.roms import rom_less

from .classify import Classifier, expected_writes, insn_reads, view
from .oracle import Run, writer_of
from .space import Cell

try:
    from vagrant_moses.smc import AVAILABLE as SMC_NATIVE
    from vagrant_moses.smc import Recorder
except ImportError:  # a vagrant-moses without the recorder
    SMC_NATIVE, Recorder = False, None
try:
    from vagrant_moses.smc import CALLS, Calls
except ImportError:  # a vagrant-moses without the call queue
    CALLS, Calls = False, None

__all__ = [
    "CALLS",
    "core",
    "MachineOracle",
    "run_machine",
    "SENTINEL",
    "SMC_NATIVE",
    "native_cells",
    "Queue",
    "weak",
]

SENTINEL = 0xCFF0
#: Cycles a run makes between its :meth:`MachineOracle._window` calls.
WINDOW = 1 << 22
#: A host I/O access in :meth:`MachineOracle.run`: a read; a write of the value last read.
READ, READ_BACK = None, -1
_ENTRY = {
    "nmi_entry": "nmi",
    "video_irq_entry": "irq",
    "cia_irq_entry": "irq",
    "reu_irq_entry": "irq",
    "brk_entry": "brk",
}
_PUSH = {"nmi": "nmi_push", "irq": "irq_push", "brk": "brk_push"}
_VEC = {"nmi": mm.CPU_NMI_VECTOR, "irq": mm.CPU_IRQ_VECTOR, "brk": mm.CPU_IRQ_VECTOR}
_PHASE = {"init": 1, "call": 2}
_BASES = {mm.BASIC_BASE: (0xA, 0xB), mm.CHARGEN_BASE: (0xD,), mm.KERNAL_BASE: (0xE, 0xF)}


def core():
    """Whether vagrant-moses' native machine (shipped in its wheels) is available."""
    return vm.NATIVE


def _rom_pages(roms):
    """``{addr >> 12: (base, bytes)}`` of the ROM set ``roms`` (base -> bytes)."""
    return {page: (base, data) for base, data in roms.items() for page in _BASES.get(base, ())}


def _machine(ntsc, sids, roms, on_io=None):
    """The native machine; ``roms`` (base -> bytes) replaces the ROM-less set, ``on_io``
    (when given) sees every I/O access as ``(addr, value, is_write)``."""
    kw = {} if on_io is None else {"on_io": on_io}
    return vm.Machine(ntsc, sids=sids, backend="native", roms=roms, **kw)


class Queue:
    """Calls in order, held as runs ``[call, times]`` of equal calls so that a long run of one
    call takes constant memory; ``runs`` adds such runs after ``calls``."""

    def __init__(self, calls=(), runs=()):
        self.runs, self.n = deque(), 0
        for call, group in groupby(calls):
            self.add(call, sum(1 for _ in group))
        for call, times in runs:
            self.add(call, times)

    def add(self, call, times=1):
        """Queue ``call`` ``times`` times."""
        if times > 0:
            if self.runs and self.runs[-1][0] == call:
                self.runs[-1][1] += times
            else:
                self.runs.append([call, times])
            self.n += times

    def __len__(self):
        return self.n

    def __iter__(self):
        return (c for call, times in self.runs for c in repeat(call, times))

    def __getitem__(self, k):
        for call, times in self.runs:
            if k < times:
                return call
            k -= times
        raise IndexError(k)

    def head(self, k):
        """The first ``k`` calls."""
        out = Queue()
        for call, times in self.runs:
            if k <= 0:
                break
            out.add(call, min(times, k))
            k -= times
        return out

    def drop(self, k):
        """Remove the first ``k`` calls."""
        self.n -= k
        while k:
            run = self.runs[0]
            if run[1] > k:
                run[1] -= k
                return
            k -= self.runs.popleft()[1]

    def pop(self):
        """Remove and return the first call."""
        call = self.runs[0][0]
        self.drop(1)
        return call


def weak(method):
    """``method`` called through a weak reference to its object, for a hook the machine keeps:
    the object's RAM array (a buffer the GC does not traverse) would otherwise close an
    uncollectable cycle through the machine."""
    ref = weakref.WeakMethod(method)

    def call(*args):
        return ref()(*args)

    return call


def _frames(events, sp):
    """``(kind, frame_sp)`` per interrupt entry since the last decode, oldest first.

    Back-to-back entries stack their 3-byte frames, so the earlier one sits higher.
    """
    kinds = [_ENTRY[n] for n in events if n in _ENTRY]
    n = len(kinds)
    return [(k, (sp + 3 * (n - 1 - i)) & 0xFF) for i, k in enumerate(kinds)]


class _Mem:
    """Memory as an instruction saw it: its own fetched bytes, RAM for the rest."""

    __slots__ = ("pc", "fetched", "ram")

    def __init__(self, pc, fetched, ram):
        self.pc, self.fetched, self.ram = pc, fetched, ram

    def __getitem__(self, addr):
        k = (addr - self.pc) & 0xFFFF
        return self.fetched[k] if k < len(self.fetched) else int(self.ram[addr])


def _writer(writer, op):
    """``(writer, channel, rmw)`` of a native event's writer; ``ctl`` for a call frame's
    return address, ``None`` when the writer has no channel."""
    if writer == "cpu":
        return writer_of(op)
    if writer == "cpu_port":
        if op is None:
            return ("cpu_port", "reset", False)
        w = writer_of(op)
        return None if w is None or w[0] != "cpu" else ("cpu_port", w[1], w[2])
    return (writer, "dma" if writer == "reu_dma" else "sp_rel", False)


def native_cells(events):
    """``(cells, first, unclassified)`` of the recorder's events, ``first`` keeping the
    earliest index of the events a cell merges."""
    cells, first, bad = set(), {}, []
    for writer, op, role, timing, mapping, index in events:
        w = _writer(writer, op)
        if w is None:
            bad.append(("write-form", op, role))
        elif w[0] != "ctl":
            cell = Cell(*w, role, timing, mapping)
            cells.add(cell)
            first.setdefault(cell, index)
    return cells, first, bad


class MachineOracle:
    """One machine run over ``image`` (all of RAM at power-on), driven call by call.

    ``roms`` (base -> bytes) overrides vagrant-moses' ROM-less ROM set per ROM. ``backend``
    (``native``/``python``, default: ``native`` when the recorder is available, the class keeps
    this decode hook and no device reports DMA) picks who labels the run.
    """

    # pylint: disable=too-many-arguments,too-many-positional-arguments

    #: Whether a native run of calls hands them to vagrant-moses' call queue where it exists.
    native_queue = True

    def __init__(
        self,
        image,
        ntsc=False,
        devices=None,
        sentinel=SENTINEL,
        roms=None,
        backend=None,
        on_io=None,
    ):
        if not core():
            raise ImportError("vagrant-moses native machine is not available")
        dma = any(hasattr(d, "attach") for d in (devices or {}).values())
        if backend is None:
            plain = type(self)._decode is MachineOracle._decode
            backend = "native" if SMC_NATIVE and plain and not dma else "python"
        if backend not in ("native", "python") or (backend == "native" and not SMC_NATIVE):
            raise ValueError(f"backend {backend!r} is not available")
        self.native = backend == "native"
        img = bytearray(image)
        img[sentinel : sentinel + 3] = bytes([0x4C, sentinel & 0xFF, sentinel >> 8])
        full = {**rom_less(), **roms} if roms else None
        self.rom_page = _rom_pages(full or rom_less())
        self.m = _machine(ntsc, dict(devices or {}), full, on_io)
        self.ram = np.frombuffer(self.m.ram, dtype=np.uint8)
        self.ram[:] = np.frombuffer(bytes(img), dtype=np.uint8)
        self.shadow = self.ram.copy()
        self.cls = Classifier(img, banked=True)
        self.sentinel = sentinel
        self.queue = Queue()
        self.prev = None
        self.bits = None
        self.stack = []
        self.decodes = 0
        self.dma = []
        self.irq_driven = False
        self.ports = {}
        self.returns = sentinel
        self.timeout = False
        self.call_start = 0
        self.call_cycles = 0
        self.t0, self.cpf = None, mm.video(ntsc).cycles_per_frame
        self.rec = Recorder(self.m, img, banked=True) if self.native else None
        self.calls = None
        for dev in (devices or {}).values():
            if hasattr(dev, "attach"):
                dev.attach(self)

    def note_dma(self, lo, hi):
        """A device's DMA is writing C64 RAM ``[lo, hi)``: attribute those changes to it."""
        self.dma.append((lo, hi))

    def _read(self, bits, addr):
        v = view(bits, addr)
        if addr < 2:
            m = self.m
            return (port_read(m.port_dir, m.port_data) if addr else m.port_dir), v
        if v == "rom":
            base, data = self.rom_page[addr >> 12]
            return data[addr - base], v
        return int(self.ram[addr]), v

    def _sweep(self):
        """Full RAM diff: changes no instruction, entry push or DMA explains are unclassified."""
        ram, shadow = self.ram, self.shadow
        neq = np.flatnonzero(ram.view(np.uint64) != shadow.view(np.uint64))
        if neq.size:
            idx = (neq[:, None] * 8 + np.arange(8)).ravel()
            idx = idx[ram[idx] != shadow[idx]]
            self.cls.unclassified += [("unattributed", int(a)) for a in idx]
            shadow[idx] = ram[idx]

    def _attribute(self, events, sp):
        """Attribute the previous instruction's writes, entry pushes and DMA by address form."""
        ram, shadow = self.ram, self.shadow
        pc, fetched, x, y, psp, bits = self.prev
        writes = {a: None for a in expected_writes(_Mem(pc, fetched, shadow), pc, x, y, psp)}
        for kind, fsp in _frames(events, sp):
            writes.update({0x100 | ((fsp + k) & 0xFF): _PUSH[kind] for k in (1, 2, 3)})
        for lo, hi in self.dma:
            writes.update(dict.fromkeys(range(lo, hi), "reu_dma"))
        self.dma.clear()
        w = writer_of(fetched[0])
        for addr, who in writes.items():
            v = ram[addr]
            if v == shadow[addr]:
                continue
            shadow[addr] = v
            wview = view(bits, addr)
            if who == "reu_dma":
                self.cls.write(addr, who, "dma", False, pc, wview)
            elif who is not None:
                self.cls.write(addr, who, "sp_rel", False, pc, wview)
            elif w is None:
                self.cls.unclassified.append(("write-form", fetched[0], addr, pc))
            else:
                self.cls.write(addr, *w, pc, wview)
        self.decodes += 1
        if self.decodes & 0xFFF == 0:
            self._sweep()

    def _decode(self, mc):
        r = mc.mpu
        pc, x, y, sp, bits = r.pc, r.x, r.y, r.sp, mc.bits
        events = [name for _, name in mc.events]
        if events:
            mc.clear_events()
        if self.t0 is not None:
            self.t0 = mc.cycle if self.t0 < 0 else self.t0
            self.cls.index = (mc.cycle - self.t0) // self.cpf
        if self.prev is not None and pc == self.sentinel == self.prev[0] and not events:
            return
        if self.prev is not None:
            self._attribute(events, sp)
        cls = self.cls
        if self.bits is not None and bits != self.bits and self.prev is not None:
            w = writer_of(self.prev[1][0])
            if w is not None and w[0] == "cpu":
                cls.port_change(w[1], w[2], self.prev[0])
            else:
                cls.unclassified.append(("port-write", self.prev[1][0], self.prev[0]))
        self.bits = bits
        stack = self.stack
        while stack and sp > stack[-1][1]:
            stack.pop()
        for kind, fsp in _frames(events, sp):
            ctx = kind if kind in ("irq", "nmi") else (stack[-1][0] if stack else "main")
            stack.append((ctx, fsp))
            va = _VEC[kind]
            for a in (va, va + 1):
                v, fv = self._read(bits, a)
                cls.fetch(a, v, "hw_vector", pc, fv)
        cls.ctx = stack[-1][0] if stack else "main"
        fetched = [self._read(bits, (pc + k) & 0xFFFF)[0] for k in range(3)]
        mem = _Mem(pc, fetched, self.ram)
        from_rom = view(bits, pc) == "rom"
        for addr, role in insn_reads(mem, pc, x, sp):
            v, fv = (fetched[(addr - pc) & 0xFFFF], view(bits, addr)) if role in (
                "opcode", "operand") else self._read(bits, addr)  # fmt: skip
            cls.fetch(addr, v, role, pc, fv, from_rom)
        cls.bank(bits, pc)
        self.prev = (pc, fetched, x, y, sp, bits)
        if self.call_cycles and mc.cycle - self.call_start > self.call_cycles:
            self.timeout = True
            mc.stop = True

    def _trap(self, mc):
        if not self.native:
            self._sweep()
        if not self.queue:
            if self.irq_driven:
                mc.watch = set()
            mc.stop = True
            return False
        phase, pc, a = self.queue.pop()
        port = self.ports.get(phase)
        if self.native:
            return self._native_call(mc, phase, pc, a, port)
        if port is not None and port != mc.port_data:
            mc[mm.CPU_PORT] = port
            self.shadow[mm.CPU_PORT] = self.ram[mm.CPU_PORT]
            self.bits = mc.bits
            self.cls.port = ("reset", False, -1, "main", None)
        if phase == "call":
            if self.cls.call < 0:
                self.cls.start_calls(mc.bits)
            else:
                self.cls.next_call()
        r = mc.mpu
        sp, ret = r.sp, (self.returns if phase == "init" else self.sentinel) - 1
        for k, v in ((0, ret >> 8), (1, ret & 0xFF)):
            addr = 0x100 | ((sp - k) & 0xFF)
            self.ram[addr] = self.shadow[addr] = v
            self.cls.write(addr, "ctl", "", False, pc)
        r.sp, r.a, r.pc = (sp - 2) & 0xFF, a, pc
        self.call_start = mc.cycle
        self._decode(mc)
        return True

    def _native_call(self, mc, phase, pc, a, port):
        """The trap's call set-up, its host writes and call boundary told to the recorder."""
        rec = self.rec
        mc.clear_events()
        if port is not None and port != mc.port_data:
            rec.host_write(mm.CPU_PORT, port)
        if phase == "call":
            if rec.call < 0:
                rec.begin()
            else:
                rec.next_call()
        r = mc.mpu
        sp, ret = r.sp, (self.returns if phase == "init" else self.sentinel) - 1
        for k, v in ((0, ret >> 8), (1, ret & 0xFF)):
            rec.host_write(0x100 | ((sp - k) & 0xFF), v)
        r.sp, r.a, r.pc = (sp - 2) & 0xFF, a, pc
        rec.deadline = mc.cycle + self.call_cycles if self.call_cycles else None
        return True

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def run(
        self,
        calls,
        init=(),
        frames=0,
        call_cycles=200_000,
        irq_setup=False,
        ports=None,
        index_frames=False,
        io=(),
        start=0,
        returns=None,
    ):
        """Run ``init`` then ``calls`` (``[(pc, a)]``); with ``frames``, interrupts drive play.

        ``ports`` maps ``"init"``/``"call"`` to the $01 value the host sets before each such call.
        ``index_frames``: a cell's first-seen index is the frame of the interrupt-driven phase
        (cycles since its first decode over cycles per frame) instead of the call. ``io``:
        ``(cycle, addr, value)`` host I/O accesses as a CPU access in that cycle after the reset
        would make them (``value`` :data:`READ` reads, :data:`READ_BACK` writes back the value
        last read); init starts ``start`` cycles after the reset, then ``irq_setup``'s CIA setup.
        Init returns to ``returns`` (default: the sentinel), code that runs on to the sentinel.
        """
        self.ports = dict(ports or {})
        self.returns = returns or self.sentinel
        calls = calls if isinstance(calls, Queue) else Queue(calls)
        self.queue = Queue(("init", pc, a) for pc, a in init)
        for (pc, a), times in calls.runs:
            self.queue.add(("call", pc, a), times)
        self.irq_driven = frames > 0
        self.call_cycles = call_cycles
        m = self.m
        t = m.reset(self.sentinel)
        self._host_io(t, io)
        if not self.native:
            m.set_decode_hook(weak(self._decode))  # pylint: disable=no-member
        m.set_trap(weak(self._trap), {self.sentinel})  # pylint: disable=no-member
        t = self.call_start = m.reset(self.sentinel, t + start)
        self.shadow[:] = self.ram
        if irq_setup:
            latch = mm.video(m.ntsc).kernal_latch
            for reg, v in ((mm.CIA1_TIMER_A_LO, latch & 0xFF), (mm.CIA1_TIMER_A_HI, latch >> 8),
                           (mm.CIA1_ICR, 0x81), (mm.CIA1_CRA, 0x11)):  # fmt: skip
                m.io_write(reg, v)
        frame_cycles = frames * mm.video(m.ntsc).cycles_per_frame
        if self.irq_driven:
            self.call_cycles = min(call_cycles, frame_cycles)
        limit = t + self.call_cycles * (len(self.queue) + 1)
        queue = self.native_queue and not self.irq_driven and self.returns == self.sentinel
        if self.native and CALLS and queue:
            self._queue_native()
            self._run(limit)
            self.queue.drop(len(self.queue) - self.calls.left)
        else:
            self._run(limit)
        if self.irq_driven:
            self._frames_phase(index_frames)
            m.stop = False
            self._run(m.cycle + frame_cycles)
        if self.native:
            return self._native_run()
        m.set_decode_hook(None)  # pylint: disable=no-member
        if self.prev is not None:
            self._attribute([], m.mpu.sp)
        self._sweep()
        done = not m.jammed and (self.irq_driven or not (self.queue or self.timeout))
        return Run(self.cls.cells, self.cls.unclassified, done, self.cls.first)

    def _run(self, limit):
        """Run the machine to cycle ``limit`` or until it stops, :data:`WINDOW` cycles at a
        time, :meth:`_window` after each."""
        m = self.m
        while True:
            m.run(min(limit, m.cycle + WINDOW))
            self._window()
            if m.stop or m.jammed or m.cycle >= limit:
                return

    def _window(self):
        """Between two windows of a run: what the run holds may be handed on here."""

    def _host_io(self, t0, io):
        """Make ``io`` (:meth:`run`): each access from a trap at the CPU's first fetch after a
        reset to its cycle, where the chips' clock is that cycle's as for a CPU access."""
        pending, last = list(io)[::-1], 0

        def access(mc):
            nonlocal last
            _, reg, v = pending.pop()
            if v is READ:
                last = mc.io_read(reg)
            else:
                mc.io_write(reg, last if v == READ_BACK else v)
            mc.stop = True
            return False

        m = self.m
        m.set_trap(access, {self.sentinel})  # pylint: disable=no-member
        for at, _, _ in io:
            m.reset(self.sentinel, t0 + at)
            m.run(t0 + at + 1)

    def _queue_native(self):
        """Hand the queue to vagrant-moses: its sentinel trap starts each call natively."""
        runs = list(self.queue.runs)
        q = np.array([(_PHASE[ph], pc, a) for (ph, pc, a), _ in runs], np.int64).reshape(-1, 3)
        packed = np.stack([q[:, 0], q[:, 1] & 0xFF, q[:, 1] >> 8, q[:, 2] & 0xFF], 1)
        self.calls = Calls(self.rec, packed.astype(np.uint8).tobytes(), self.sentinel, self.ports,
                           self.call_cycles, [t for _, t in runs])  # fmt: skip

    def _frames_phase(self, index_frames):
        """Calls start (if init did not) and the per-call budget ends: interrupts drive play."""
        self.call_cycles = 0
        if self.native:
            if self.rec.call < 0:
                self.rec.begin()
            self.timeout = self.rec.timed_out
            self.rec.deadline = None
            if index_frames:
                self.rec.frame_clock(self.cpf)
            return
        if self.cls.call < 0:
            self.cls.start_calls(self.m.bits)
        if index_frames:
            self.t0 = -1

    def _native_run(self):
        m, rec = self.m, self.rec
        m.clear_events()  # pylint: disable=no-member
        rec.finish()
        self.timeout |= rec.timed_out
        cells, first, bad = native_cells(rec.events())
        count, sample = rec.unclassified()
        done = not m.jammed and (self.irq_driven or not (self.queue or self.timeout))
        return Run(cells, bad + sample, done, first, count + len(bad))


def run_machine(image, calls, init=(), **kw):
    """One :class:`MachineOracle` run of ``calls`` over ``image``; the :class:`Run`."""
    return MachineOracle(image).run(calls, init, **kw)
