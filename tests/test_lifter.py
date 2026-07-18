"""Differential fuzz: PcodeVM vs py65 across every py65-implemented (legal) opcode.

For each opcode py65 implements, randomise A/X/Y/SP/P (D forced 0) and the full
64 KiB, single-step both cores from a fixed PC, and compare A,X,Y,SP, flags
C,Z,I,D,V,N, all 64 KiB of memory, the next PC, and the cycle delta. The RMW
illegals are excluded here (py65 stubs them) and are covered by
``test_illegals.py`` (NMS legal-decomposition) and the sidplayfp oracle.
"""

import numpy as np
import pytest

import deity_informant as P

import _common as H

py65 = pytest.importorskip("py65.devices.mpu6502")

ITERS = 200


def _legal_ops():
    mpu = py65.MPU(memory=bytearray(0x10000))
    return [
        op
        for op in range(256)
        if getattr(mpu.instruct[op], "__name__", "") != "inst_not_implemented"
    ]


def _run_ref(mpu, mem, a, x, y, sp, p):
    mpu.memory[:] = mem
    mpu.a, mpu.x, mpu.y, mpu.sp, mpu.p, mpu.pc = a, x, y, sp, p, H.PC
    mpu.processorCycles = 0
    mpu.step()
    st = (mpu.a, mpu.x, mpu.y, mpu.sp) + H.flags(mpu.p) + (mpu.pc & 0xFFFF,)
    return st, mpu.processorCycles, bytes(mpu.memory)


def _run_vm(mem, a, x, y, sp, p):
    vm = P.PcodeVM(mem)
    vm.volatile = False
    H.load_regs(vm, a, x, y, sp, p)
    npc = vm.step(H.PC, {}, P.lift)
    return H.arch_state(vm) + (npc & 0xFFFF,), vm.cycles, bytes(vm.mem)


def test_legal_opcodes_match_py65():
    mpu = py65.MPU(memory=bytearray(0x10000))
    ops = _legal_ops()
    rng = np.random.default_rng(0xC64)
    bad = {}
    names = ("A", "X", "Y", "SP", "C", "Z", "I", "D", "V", "N", "PC")
    for op in ops:
        for _ in range(ITERS):
            mem = bytearray(bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8)))
            mem[H.PC] = op
            a, x, y, sp = (int(v) for v in rng.integers(0, 256, 4))
            p = H.mk_p(rng)
            mem = bytes(mem)
            try:
                rst, rcyc, rmem = _run_ref(mpu, mem, a, x, y, sp, p)
            except IndexError:
                continue  # top-of-memory wrap py65 cannot index; skip both
            vst, vcyc, vmem = _run_vm(mem, a, x, y, sp, p)
            if rst == vst and rmem == vmem and rcyc == vcyc:
                continue
            if op not in bad:
                diffs = [
                    "%s ref=%X vm=%X" % (nm, rv, vv)
                    for nm, rv, vv in zip(names, rst, vst)
                    if rv != vv
                ]
                if rmem != vmem:
                    d = next(i for i in range(len(rmem)) if rmem[i] != vmem[i])
                    diffs.append("mem[%04X] ref=%02X vm=%02X" % (d, rmem[d], vmem[d]))
                if rcyc != vcyc:
                    diffs.append("cyc ref=%d vm=%d" % (rcyc, vcyc))
                bad[op] = "op %02X ops=%02X%02X: %s" % (
                    op,
                    mem[H.PC + 1],
                    mem[H.PC + 2],
                    "; ".join(diffs),
                )
    assert not bad, "mismatches:\n" + "\n".join(bad[o] for o in sorted(bad))
