"""The CPU-level oracle: label a program from a jennings native-core run.

Each call runs on :class:`jennings.devices.mpu6502.NativeMPU` with a write subscription and a
traced pc stream. Memory is replayed from the observed writes, so every instruction's fetched
bytes and control reads are checked against the lift-time image by the shared classifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory
from jennings.opcodes import OPCODES

from .classify import Classifier, expected_writes, insn_reads
from .space import store_channel

__all__ = ["Run", "run_cpu", "label", "writer_of"]

_HWVEC = {"BRK": 0xFFFE}


@lru_cache(maxsize=None)
def writer_of(op):
    """``(writer, channel, rmw)`` for a byte stored by opcode ``op``, or ``None``."""
    mn = OPCODES[op][0]
    if mn == "JSR":
        return ("ctl", "", False)
    if mn == "BRK":
        return ("brk_push", "sp_rel", False)
    sc = store_channel(op)
    return None if sc is None else ("cpu", sc[0], sc[1])


@dataclass
class Run:
    """An oracle run: observed cells, unclassified events and whether every call returned."""

    cells: set = field(default_factory=set)
    unclassified: list = field(default_factory=list)
    returned: bool = True


def _call(mpu, mem, shadow, cls, writes, pc, a, max_cycles):
    sp0 = mpu.sp
    for k, v in ((0, 0x00), (1, 0x01)):
        addr = 0x100 | ((sp0 - k) & 0xFF)
        mem.write(addr, bytes([v]))
        shadow[addr] = v
        cls.write(addr, "ctl", "", False, pc)
    mpu.sp, mpu.pc, mpu.a = (sp0 - 2) & 0xFF, pc, a
    writes.clear()
    s0 = mpu.steps
    x, y, sp = mpu.x, mpu.y, mpu.sp
    reason, tr = mpu.run_until(max_cycles=max_cycles, sp_above=(sp0 - 2) & 0xFF, regs=True)
    wi = 0
    for i in range(len(tr)):
        pc_i = tr.pc[i]
        for addr, role in insn_reads(shadow, pc_i, x, sp):
            cls.fetch(addr, shadow[addr], role, pc_i)
        mn = OPCODES[shadow[pc_i]][0]
        if mn in _HWVEC:
            for va in (_HWVEC[mn], _HWVEC[mn] + 1):
                cls.fetch(va, shadow[va], "hw_vector", pc_i)
        expect = None
        while wi < len(writes) and writes[wi][0] - s0 <= i:
            _, addr, v = writes[wi]
            wi += 1
            w = writer_of(shadow[pc_i])
            if expect is None:
                expect = expected_writes(shadow, pc_i, x, y, sp)
            if w is None or addr not in expect:
                cls.unclassified.append(("write-form", shadow[pc_i], addr, pc_i))
            else:
                cls.write(addr, *w, pc_i)
            shadow[addr] = v
        x, y, sp = tr.x[i], tr.y[i], tr.sp[i]
    return reason == "sp"


def run_cpu(image, entries, calls, init=(), max_cycles=200_000):
    """Run ``init`` then ``calls`` (``[(entry_index, a)]``) on jennings; the :class:`Run`."""
    mem = NativeMemory(bytes(image))
    mpu = NativeMPU(memory=mem)
    mpu.sp = 0xFF
    shadow = bytearray(image)
    cls = Classifier(image)
    writes = []
    mem.subscribe_to_write(range(0x10000), lambda ad, v: writes.append((mpu.steps, ad, v)))
    ok = True
    for pc, a in init:
        ok &= _call(mpu, mem, shadow, cls, writes, pc, a, max_cycles)
    cls.start_calls()
    for n, (e, a) in enumerate(calls):
        if n:
            cls.next_call()
        ok &= _call(mpu, mem, shadow, cls, writes, entries[e], a, max_cycles)
    return Run(cls.cells, cls.unclassified, ok)


def label(image, entries, calls, init=()):
    """The cells a program exhibits on the CPU oracle."""
    return run_cpu(image, entries, calls, init).cells
