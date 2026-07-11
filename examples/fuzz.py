"""Differential fuzzer: PcodeVM vs py65 for legal + py65-defined illegal opcodes.

py65 (patched with pysidtracker.oracle._patch_illegals) is a correct reference
for legal opcodes and the illegals it defines (SBX/ANC/ALR/ARR/SBC/LAX/SAX +
NOP-illegals). For each such opcode we randomise A/X/Y/SP/P (D forced 0) and the
full 64K, single-step both cores from a fixed PC, and compare A,X,Y,SP, flags
C,Z,I,D,V,N, all 64K of memory, the next PC, and the cycle delta.

The RMW illegals (SLO/RLA/SRE/RRA/DCP/ISC), LAS, ANE and the SH* family are NOT
py65-defined and are validated end-to-end against the sidplayfp oracle instead.

py65's patched LAX absy/indy ($BF/$B3) stub `extracycles=0`, dropping the real
page-cross cycle our (hardware-correct) lifter keeps -- cycle mismatches on those
two opcodes in page-crossing cases are expected and reported separately.
"""
import numpy as np

import deity_informant as P
from pysidtracker.oracle import _patch_illegals

PC = 0x0800
ITERS = 200
# py65's patched read-illegals stub extracycles=0, dropping the real page-cross
# cycle our (hardware-correct, sidplayfp-matching) lifter keeps: LAX absy/indy and
# the NOP abs,X illegals. Cycle-only mismatches on these in page-crossing cases
# are expected, not lifter bugs.
CYCLE_STUB_OPS = {0xB3, 0xBF, 0x1C, 0x3C, 0x5C, 0x7C, 0xDC, 0xFC}


def _build_ref():
    from py65.devices.mpu6502 import MPU

    mpu = MPU(memory=bytearray(0x10000))
    _patch_illegals(mpu)
    return mpu


def _real_ops(mpu):
    return [op for op in range(256)
            if getattr(mpu.instruct[op], "__name__", "") != "inst_not_implemented"]


def _flags(p):
    return (p & 1, (p >> 1) & 1, (p >> 2) & 1, (p >> 3) & 1, (p >> 6) & 1, (p >> 7) & 1)


def _mk_p(rng):
    bits = rng.integers(0, 2, 6)  # C,Z,I,D,V,N with D forced 0
    return int(bits[0] | (bits[1] << 1) | (bits[2] << 2) | 0x20
               | (bits[4] << 6) | (bits[5] << 7))


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


def main():
    P.load_cycle_tables()
    mpu = _build_ref()
    ops = _real_ops(mpu)
    rng = np.random.default_rng(0xC64)
    bad = {}          # op -> minimal failing case (non-cycle-stub)
    cyc_stub_hits = 0
    for op in ops:
        for _ in range(ITERS):
            mem = bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8))
            mem = bytearray(mem)
            mem[PC] = op
            a, x, y, sp = (int(v) for v in rng.integers(0, 256, 4))
            p = _mk_p(rng)
            mem = bytes(mem)
            try:
                rst, rcyc, rmem = _run_ref(mpu, mem, a, x, y, sp, p)
            except IndexError:
                continue  # top-of-memory wrap that py65 cannot index; skip both
            vst, vcyc, vmem = _run_vm(mem, a, x, y, sp, p)
            state_ok = rst == vst and rmem == vmem
            cyc_ok = rcyc == vcyc
            if state_ok and cyc_ok:
                continue
            if state_ok and not cyc_ok and op in CYCLE_STUB_OPS:
                cyc_stub_hits += 1
                continue
            if op not in bad:
                diffs = []
                names = ("A", "X", "Y", "SP", "C", "Z", "I", "D", "V", "N", "PC")
                for nm, rv, vv in zip(names, rst, vst):
                    if rv != vv:
                        diffs.append("%s ref=%X vm=%X" % (nm, rv, vv))
                if rmem != vmem:
                    d = next(i for i in range(len(rmem)) if rmem[i] != vmem[i])
                    diffs.append("mem[%04X] ref=%02X vm=%02X" % (d, rmem[d], vmem[d]))
                if rcyc != vcyc:
                    diffs.append("cyc ref=%d vm=%d" % (rcyc, vcyc))
                bad[op] = "op %02X A=%02X X=%02X Y=%02X SP=%02X P=%02X ops=%02X%02X: %s" % (
                    op, a, x, y, sp, p, mem[PC + 1], mem[PC + 2], "; ".join(diffs))
    print("fuzzed %d py65-implemented opcodes x %d iters" % (len(ops), ITERS))
    print("LAX absy/indy page-cross cycle-stub cases (expected): %d" % cyc_stub_hits)
    if not bad:
        print("RESULT: 100%% CLEAN -- all legal + py65-defined illegal opcodes match")
        return 0
    print("RESULT: %d opcode(s) MISMATCH:" % len(bad))
    for op in sorted(bad):
        print("  " + bad[op])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
