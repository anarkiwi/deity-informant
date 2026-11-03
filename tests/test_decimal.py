"""ADC, SBC, ARR, RRA and ISC in binary and decimal mode, exhaustively, on every engine.

The reference is :mod:`decimal_ref` (Bruce Clark's tutorial): every accumulator, operand and
carry with the decimal flag set (invalid BCD included), and the binary forms with it clear.
"""

import pytest

from deity_informant import PcodeVM, lift

import decimal_ref as ref

native = pytest.importorskip("deity_informant._native")

ORG, ZP, STEP = 0x1000, 0, 3
# opcode, reference, operand in the zero page (else inline)
MODES = {
    "ADC": (0x65, ref.adc, True),
    "SBC": (0xE5, ref.sbc, True),
    "ARR": (0x6B, ref.arr, False),
    "RRA": (0x67, ref.rra, True),
    "ISC": (0xE7, ref.isc, True),
}
FLAGS = {"N": 14, "V": 13, "Z": 9, "C": 8}


def image(op, zero_page):
    """256 copies of ``op`` at ``ORG + 3 * m`` taking operand ``m``, in the zero page or inline."""
    mem = bytearray(0x10000)
    for m in range(256):
        pc = ORG + STEP * m
        mem[pc : pc + 2] = bytes([op, ZP + m if zero_page else m])
        mem[ZP + m] = m
    return mem


def expected(name, a, m, c, d):
    out = MODES[name][1](a, m, c, d)
    return out[:5], (out[5] if len(out) > 5 else None)


def observed(name, regs, mem, m):
    flags = tuple(regs[i] for i in FLAGS.values())
    return (regs[0], flags[0], flags[1], flags[2], flags[3]), (
        mem[ZP + m] if name in ("RRA", "ISC") else None
    )


def python_run(name, a, c, d, ms, mem, cache):
    for m in ms:
        vm = PcodeVM(bytearray(mem))
        vm.volatile = False
        vm.reg[0], vm.reg[8], vm.reg[11] = a, c, d
        vm.step(ORG + STEP * m, cache, lift)
        yield m, vm.reg, vm.mem


def native_run(a, c, d, ms, mem):
    for m in ms:
        buf = bytearray(mem)
        cpu = native.Cpu([a, 0, 0, 0xFF, 0, 0, 0, 0, c, 0, 0, d, 0, 0, 0, 0], ORG + STEP * m, 0)
        cpu.step(buf)
        yield m, cpu.reg, buf


def check(name, runner, a, c, d):
    ms = range(256)
    for m, regs, mem in runner(name, a, c, d, ms):
        want, wmem = expected(name, a, m, c, d)
        got, gmem = observed(name, regs, mem, m)
        assert (got, gmem) == (want, wmem), (name, hex(a), hex(m), c, d)
        assert regs[11] == d and regs[10] == 0 and regs[12] == 0


@pytest.mark.parametrize("d", (0, 1))
@pytest.mark.parametrize("name", MODES)
@pytest.mark.parametrize("half", range(8))
def test_python_vm_matches_the_reference(name, half, d):
    op, _, zero_page = MODES[name]
    mem, cache = image(op, zero_page), {}

    def runner(_name, a, c, dd, ms):
        return python_run(name, a, c, dd, ms, mem, cache)

    for a in range(half * 32, half * 32 + 32):
        for c in (0, 1):
            check(name, runner, a, c, d)


@pytest.mark.parametrize("d", (0, 1))
@pytest.mark.parametrize("name", MODES)
@pytest.mark.parametrize("half", range(8))
def test_native_interpreter_matches_the_reference(name, half, d):
    op, _, zero_page = MODES[name]
    mem = image(op, zero_page)

    def runner(_name, a, c, dd, ms):
        return native_run(a, c, dd, ms, mem)

    for a in range(half * 32, half * 32 + 32):
        for c in (0, 1):
            check(name, runner, a, c, d)
