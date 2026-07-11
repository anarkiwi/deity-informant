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

py65 = pytest.importorskip("py65.devices.mpu6502")

PC = 0x0800
ITERS = 200


def _flags(p):
    return (p & 1, (p >> 1) & 1, (p >> 2) & 1, (p >> 3) & 1, (p >> 6) & 1, (p >> 7) & 1)


def _legal_ops():
    mpu = py65.MPU(memory=bytearray(0x10000))
    return [
        op
        for op in range(256)
        if getattr(mpu.instruct[op], "__name__", "") != "inst_not_implemented"
    ]


def _mk_p(rng):
    b = rng.integers(0, 2, 6)  # C,Z,I,D,V,N with D forced 0
    return int(b[0] | (b[1] << 1) | (b[2] << 2) | 0x20 | (b[4] << 6) | (b[5] << 7))


def _run_ref(mpu, mem, a, x, y, sp, p):
    mpu.memory[:] = mem
    mpu.a, mpu.x, mpu.y, mpu.sp, mpu.p, mpu.pc = a, x, y, sp, p, PC
    mpu.processorCycles = 0
    mpu.step()
    st = (mpu.a, mpu.x, mpu.y, mpu.sp) + _flags(mpu.p) + (mpu.pc & 0xFFFF,)
    return st, mpu.processorCycles, bytes(mpu.memory)


def _run_vm(mem, a, x, y, sp, p):
    vm = P.PcodeVM(mem)
    vm.volatile = False
    r = vm.reg
    r[0], r[1], r[2], r[3] = a, x, y, sp
    r[8], r[9], r[10], r[11], r[13], r[14] = _flags(p)
    npc = vm.step(PC, {}, P.lift)
    st = (r[0], r[1], r[2], r[3], r[8], r[9], r[10], r[11], r[13], r[14], npc & 0xFFFF)
    return st, vm.cycles, bytes(vm.mem)


def test_legal_opcodes_match_py65():
    mpu = py65.MPU(memory=bytearray(0x10000))
    ops = _legal_ops()
    rng = np.random.default_rng(0xC64)
    bad = {}
    names = ("A", "X", "Y", "SP", "C", "Z", "I", "D", "V", "N", "PC")
    for op in ops:
        for _ in range(ITERS):
            mem = bytearray(bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8)))
            mem[PC] = op
            a, x, y, sp = (int(v) for v in rng.integers(0, 256, 4))
            p = _mk_p(rng)
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
                    mem[PC + 1],
                    mem[PC + 2],
                    "; ".join(diffs),
                )
    assert not bad, "mismatches:\n" + "\n".join(bad[o] for o in sorted(bad))
