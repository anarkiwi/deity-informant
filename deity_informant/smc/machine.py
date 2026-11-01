"""The machine-level oracle: label a program from a cycle-exact vagrant-moses C64 run.

The native :class:`vagrant_moses._core.Machine` (6510 on jennings-core, $01-banked PLA, VIC-II
and CIAs, ROM-less ROM set) runs calls from a trapped sentinel. A decode hook diffs RAM per
instruction to attribute writes, follows interrupt entries/RTI as contexts, and classifies fetches.
"""

from __future__ import annotations

import numpy as np
from vagrant_moses import machine as vm
from vagrant_moses import memmap as mm
from vagrant_moses.bus import port_read
from vagrant_moses.c64 import poweron_ram
from vagrant_moses.roms import rom_less

from .classify import Classifier, expected_writes, insn_reads, view
from .oracle import Run, writer_of

__all__ = ["core", "MachineOracle", "run_machine", "SENTINEL"]

SENTINEL = 0xCFF0
_ENTRY = {"nmi_entry": "nmi", "video_irq_entry": "irq", "cia_irq_entry": "irq", "brk_entry": "brk"}
_PUSH = {"nmi": "nmi_push", "irq": "irq_push", "brk": "brk_push"}
_VEC = {"nmi": mm.CPU_NMI_VECTOR, "irq": mm.CPU_IRQ_VECTOR, "brk": mm.CPU_IRQ_VECTOR}
_BASES = {mm.BASIC_BASE: (0xA, 0xB), mm.CHARGEN_BASE: (0xD,), mm.KERNAL_BASE: (0xE, 0xF)}


def core():
    """Whether vagrant-moses' native machine (shipped in its wheels) is available."""
    return vm.NATIVE


def _rom_pages(roms):
    """``{4 KiB page: (base, bytes)}`` of the ROM set ``roms`` (base -> bytes)."""
    return {page: (base, data) for base, data in roms.items() for page in _BASES.get(base, ())}


def _machine(ntsc, sids, roms):
    """The native machine; ``roms`` (base -> bytes, every base) replaces the ROM-less set."""
    if roms is None:
        return vm.Machine(ntsc, sids=sids, backend="native")
    # pylint: disable=protected-access,c-extension-no-member
    vm._jennings()
    images = []
    for base in (mm.BASIC_BASE, mm.CHARGEN_BASE, mm.KERNAL_BASE):
        image = bytearray(0x10000)
        image[base : base + len(roms[base])] = roms[base]
        images.append(bytes(image))
    lines, cycles_per_line = mm.video(ntsc).geometry
    return vm._core.Machine(
        ntsc, sids, False, None, vm._native_layouts(), bytes(poweron_ram()), tuple(images),
        lines, cycles_per_line,
    )  # fmt: skip


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


class MachineOracle:
    """One machine run over ``image`` (64 KiB RAM at power-on), driven call by call.

    ``roms`` (base -> bytes) overrides vagrant-moses' ROM-less ROM set per ROM.
    """

    def __init__(self, image, ntsc=False, devices=None, sentinel=SENTINEL, roms=None):
        if not core():
            raise ImportError("vagrant-moses native machine is not available")
        img = bytearray(image)
        img[sentinel : sentinel + 3] = bytes([0x4C, sentinel & 0xFF, sentinel >> 8])
        full = {**rom_less(), **roms} if roms else None
        self.rom_page = _rom_pages(full or rom_less())
        self.m = _machine(ntsc, dict(devices or {}), full)
        self.ram = np.frombuffer(self.m.ram, dtype=np.uint8)
        self.ram[:] = np.frombuffer(bytes(img), dtype=np.uint8)
        self.shadow = self.ram.copy()
        self.cls = Classifier(img, banked=True)
        self.sentinel = sentinel
        self.queue = []
        self.prev = None
        self.bits = None
        self.stack = []
        self.decodes = 0
        self.dma = []
        self.irq_driven = False
        self.ports = {}
        self.timeout = False
        self.call_start = 0
        self.call_cycles = 0
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
        self._sweep()
        if not self.queue:
            if self.irq_driven:
                mc.watch = set()
            mc.stop = True
            return False
        phase, pc, a = self.queue.pop(0)
        port = self.ports.get(phase)
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
        sp, ret = r.sp, self.sentinel - 1
        for k, v in ((0, ret >> 8), (1, ret & 0xFF)):
            addr = 0x100 | ((sp - k) & 0xFF)
            self.ram[addr] = self.shadow[addr] = v
            self.cls.write(addr, "ctl", "", False, pc)
        r.sp, r.a, r.pc = (sp - 2) & 0xFF, a, pc
        self.call_start = mc.cycle
        self._decode(mc)
        return True

    def run(self, calls, init=(), frames=0, call_cycles=200_000, irq_setup=False, ports=None):
        """Run ``init`` then ``calls`` (``[(pc, a)]``); with ``frames``, interrupts drive play.

        ``ports`` maps ``"init"``/``"call"`` to the $01 value the host sets before each such call.
        """
        self.ports = dict(ports or {})
        self.queue = [("init", pc, a) for pc, a in init] + [("call", pc, a) for pc, a in calls]
        self.irq_driven = frames > 0
        self.call_cycles = call_cycles
        m = self.m
        m.set_decode_hook(self._decode)  # pylint: disable=no-member
        m.set_trap(self._trap, {self.sentinel})  # pylint: disable=no-member
        t = self.call_start = m.reset(self.sentinel)
        self.shadow[:] = self.ram
        if irq_setup:
            latch = mm.video(m.ntsc).kernal_latch
            for reg, v in ((mm.CIA1_TIMER_A_LO, latch & 0xFF), (mm.CIA1_TIMER_A_HI, latch >> 8),
                           (mm.CIA1_ICR, 0x81), (mm.CIA1_CRA, 0x11)):  # fmt: skip
                m.io_write(reg, v)
        frame_cycles = frames * mm.video(m.ntsc).cycles_per_frame
        if self.irq_driven:
            self.call_cycles = min(call_cycles, frame_cycles)
        m.run(t + self.call_cycles * (len(self.queue) + 1))
        if self.irq_driven:
            if self.cls.call < 0:
                self.cls.start_calls(m.bits)
            self.call_cycles = 0
            m.stop = False
            m.run(m.cycle + frame_cycles)
        m.set_decode_hook(None)  # pylint: disable=no-member
        if self.prev is not None:
            self._attribute([], m.mpu.sp)
        self._sweep()
        done = not m.jammed and (self.irq_driven or not (self.queue or self.timeout))
        return Run(self.cls.cells, self.cls.unclassified, done, self.cls.first)


def run_machine(image, calls, init=(), **kw):
    """One :class:`MachineOracle` run of ``calls`` over ``image``; the :class:`Run`."""
    return MachineOracle(image).run(calls, init, **kw)
