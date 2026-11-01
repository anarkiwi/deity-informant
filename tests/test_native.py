"""Differential tests: the native IR and interpreter against ``lift`` and ``PcodeVM``."""

from pathlib import Path

import numpy as np
import pytest

from deity_informant import OPS, PcodeVM, lift
from deity_informant.hello_world import EXPECTED, ORG, PROGRAM

native = pytest.importorskip("deity_informant._native")

JAM = np.array([OPS[o][0] == "JAM" for o in range(256)])
FLAGS = (8, 9, 10, 11, 12, 13, 14)
BSORT = Path(__file__).resolve().parent / "fixtures" / "bsort.hex"


def rand_image(rng, jam_free=False):
    a = rng.integers(0, 256, 0x10000, dtype=np.uint8)
    if jam_free:
        a[JAM[a]] = 0xEA
    return bytearray(a.tobytes())


def rand_regs(rng):
    r = [int(v) for v in rng.integers(0, 256, 16)]
    for i in FLAGS:
        r[i] &= 1
    return r


def hex_image(path):
    mem = bytearray(0x10000)
    for line in path.read_text().splitlines():
        addr, sep, data = line.split(";")[0].partition(":")
        if sep:
            b = bytes.fromhex(data)
            a = int(addr, 16)
            mem[a : a + len(b)] = b
    return mem


def pair(mem, pc, reg=None, cycles=0):
    vm = PcodeVM(mem)
    vm.volatile = False
    if reg is not None:
        vm.reg = list(reg)
    vm.cycles = cycles
    return vm, native.Cpu(vm.reg, pc, cycles), bytearray(mem)


def vm_step(vm, pc, cache):
    try:
        return vm.step(pc, cache, lift), None
    except RuntimeError as e:
        return pc, str(e)


def cpu_step(cpu, mem):
    try:
        return cpu.step(mem), None
    except RuntimeError as e:
        return cpu.pc, str(e)


def lockstep(mem, pc, steps, reg=None, until=None):
    """Step both interpreters, comparing full state after every instruction (JAMs skipped),
    for ``steps`` instructions or until PC reaches ``until``."""
    vm, cpu, cmem = pair(mem, pc, reg)
    cache = {}
    for i in range(steps):
        got = vm_step(vm, pc, cache)
        assert cpu_step(cpu, cmem) == got, i
        assert (cpu.reg, cpu.cycles) == (vm.reg, vm.cycles), i
        assert cmem == vm.mem, i
        pc = got[0]
        if pc == until:
            break
        if got[1]:
            pc = cpu.pc = (pc + 1) & 0xFFFF
    return vm, cpu


@pytest.mark.parametrize("op", range(256))
def test_instantiate_matches_lift(op):
    rng = np.random.default_rng(op)
    for pc in [0xFFFE, 0xFFFF] + [int(p) for p in rng.integers(0, 0x10000, 24)]:
        mem = rand_image(rng)
        mem[pc] = op
        assert native.lift(mem, pc) == lift(mem, pc)


@pytest.mark.parametrize("op", range(256))
def test_interpreter_matches_pcodevm(op):
    rng = np.random.default_rng(0x100 + op)
    for _ in range(24):
        mem = rand_image(rng)
        pc = int(rng.integers(0, 0x10000))
        mem[pc] = op
        vm, cpu, cmem = pair(mem, pc, rand_regs(rng), int(rng.integers(0, 1 << 40)))
        assert cpu_step(cpu, cmem) == vm_step(vm, pc, {})
        assert (cpu.reg, cpu.cycles, cmem) == (vm.reg, vm.cycles, vm.mem)


@pytest.mark.parametrize("seed", range(3))
def test_random_image_trajectory(seed):
    rng = np.random.default_rng(seed)
    lockstep(rand_image(rng, jam_free=True), int(rng.integers(0, 0x10000)), 12_000, rand_regs(rng))


def test_bubble_sort_trajectory():
    vm, _ = lockstep(hex_image(BSORT), 0x1040, 30_000)
    assert vm.cycles > 30_000


def test_hello_world_trajectory():
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(PROGRAM)] = PROGRAM
    vm, cpu = lockstep(mem, ORG, 1000, until=0x0001)
    assert cpu.pc == 0x0001
    assert bytes(vm.mem[0x400 : 0x400 + len(EXPECTED)]) == EXPECTED


def test_cpu_api():
    cpu = native.Cpu()
    assert (cpu.reg[3], cpu.pc, cpu.cycles) == (0xFF, 0, 0)
    cpu.reg = list(range(16))
    cpu.pc, cpu.cycles = 0x1234, 7
    assert (cpu.reg, cpu.pc, cpu.cycles) == (list(range(16)), 0x1234, 7)
    assert "pc=0x1234" in repr(cpu)
    cpu.reg = [0] * 8 + [1, 0, 1, 0, 0, 0, 1, 0]
    assert cpu.status() == 0xA5
    mem = bytearray(0x10000)
    mem[0x1234:0x1237] = bytes([0xEA, 0xEA, 0x02])
    assert cpu.run(mem, 10) == (3, True)
    assert (cpu.pc, cpu.cycles) == (0x1236, 11)
    with pytest.raises(RuntimeError, match="JAM at 1236"):
        cpu.step(mem)
    cpu.pc = 0
    assert cpu.run(mem, 2) == (2, False)
    with pytest.raises(ValueError):
        cpu.step(bytearray(16))
    with pytest.raises(TypeError):
        cpu.step(bytes(0x10000))


def test_lift_accepts_any_indexable():
    prog = [0x20, 0x34, 0x12]
    want = lift(prog + [0] * 0xFFFD, 0)
    assert native.lift(prog + [0] * 0xFFFD, 0) == want
    assert native.lift(bytes(prog), 0) == lift(bytes(prog), 0) == want
    with pytest.raises(IndexError):
        native.lift(bytes(prog[:1]), 0)
    assert native.UNIQUE_SIZE == max(
        vn[1] + vn[2]
        for op in range(256)
        for _mn, out, ins in lift(bytes([op, 0, 0]) + bytes(0xFFFD), 0)["ops"]
        for vn in (out, *ins)
        if vn is not None and vn[0] == "u"
    )
