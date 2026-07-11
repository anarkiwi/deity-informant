"""Illegal-opcode correctness via NMS-documented legal decomposition.

py65 is not a valid reference for the RMW illegals (it stubs them), but it *is*
correct for the legal instructions each illegal is composed of. So we run py65
on the documented legal-equivalent sequence (e.g. SLO == ASL;ORA) and assert the
architectural state matches the VM executing the single illegal. The unstable /
magic-constant opcodes have no legal equivalent and are checked against
hand-computed NMS semantics.
"""

import numpy as np
import pytest

import deity_informant as P

py65 = pytest.importorskip("py65.devices.mpu6502")

PC = 0x0800
ZP = 0x50  # a zero-page cell clear of I/O


def _flags(p):
    return (p & 1, (p >> 1) & 1, (p >> 2) & 1, (p >> 3) & 1, (p >> 6) & 1, (p >> 7) & 1)


def _mk_p(rng):
    b = rng.integers(0, 2, 6)  # C,Z,I,D,V,N with D forced 0 (no decimal on C64)
    return int(b[0] | (b[1] << 1) | (b[2] << 2) | 0x20 | (b[4] << 6) | (b[5] << 7))


def _vm_state(vm):
    r = vm.reg
    return (r[0], r[1], r[2], r[3], r[8], r[9], r[10], r[11], r[13], r[14])


def _run_vm(mem, a, x, y, sp, p):
    vm = P.PcodeVM(mem)
    vm.volatile = False
    r = vm.reg
    r[0], r[1], r[2], r[3] = a, x, y, sp
    r[8], r[9], r[10], r[11], r[13], r[14] = _flags(p)
    vm.step(PC, {}, P.lift)
    return _vm_state(vm), bytes(vm.mem)


def _run_py65(mem, a, x, y, sp, p, nsteps):
    """Run ``nsteps`` from PC; ``mem`` is a full 64K image with code at PC."""
    mpu = py65.MPU(memory=bytearray(mem))
    mpu.a, mpu.x, mpu.y, mpu.sp, mpu.p, mpu.pc = a, x, y, sp, p, PC
    for _ in range(nsteps):
        mpu.step()
    st = (mpu.a, mpu.x, mpu.y, mpu.sp) + _flags(mpu.p)
    return st, bytes(mpu.memory)


def _img(seq):
    """A zeroed 64K image with ``seq`` bytes placed at PC (for immediate ops)."""
    m = bytearray(0x10000)
    m[PC : PC + len(seq)] = seq
    return bytes(m)


# illegal zp opcode -> its two-instruction legal-equivalent zp opcodes (NMS).
_RMW_DECOMP = {
    0x07: (0x06, 0x05),  # SLO == ASL ; ORA
    0x27: (0x26, 0x25),  # RLA == ROL ; AND
    0x47: (0x46, 0x45),  # SRE == LSR ; EOR
    0x67: (0x66, 0x65),  # RRA == ROR ; ADC
    0xC7: (0xC6, 0xC5),  # DCP == DEC ; CMP
    0xE7: (0xE6, 0xE5),  # ISC == INC ; SBC
    0xA7: (0xA5, 0xA6),  # LAX == LDA ; LDX
}


@pytest.mark.parametrize("ill,legal", sorted(_RMW_DECOMP.items()))
def test_rmw_matches_legal_decomposition(ill, legal):
    rng = np.random.default_rng(0xC64 ^ ill)
    for _ in range(300):
        mem = bytearray(bytes(rng.integers(0, 256, 0x10000, dtype=np.uint8)))
        a, x, y, sp = (int(v) for v in rng.integers(0, 256, 4))
        p = _mk_p(rng)
        ill_mem = bytearray(mem)
        ill_mem[PC] = ill
        ill_mem[PC + 1] = ZP
        seq_mem = bytearray(mem)  # same random RAM, legal 2-insn equivalent at PC
        seq_mem[PC : PC + 4] = bytes([legal[0], ZP, legal[1], ZP])
        vst, vmem = _run_vm(bytes(ill_mem), a, x, y, sp, p)
        rst, rmem = _run_py65(bytes(seq_mem), a, x, y, sp, p, 2)
        # compare architectural state and the touched zp cell (ignore the code bytes)
        assert vst == rst, "%02X state: vm=%s py65=%s (A=%02X X=%02X mem[%02X]=%02X p=%02X)" % (
            ill,
            vst,
            rst,
            a,
            x,
            ZP,
            mem[ZP],
            p,
        )
        assert vmem[ZP] == rmem[ZP], "%02X mem[%02X]: vm=%02X py65=%02X" % (
            ill,
            ZP,
            vmem[ZP],
            rmem[ZP],
        )


def test_alr_matches_and_then_lsr():
    rng = np.random.default_rng(0xA18)
    for _ in range(300):
        imm = int(rng.integers(0, 256))
        a, x, y, sp = (int(v) for v in rng.integers(0, 256, 4))
        p = _mk_p(rng)
        mem = bytearray(0x10000)
        mem[PC] = 0x4B
        mem[PC + 1] = imm  # ALR #imm
        vst, _ = _run_vm(bytes(mem), a, x, y, sp, p)
        rst, _ = _run_py65(_img(bytes([0x29, imm, 0x4A])), a, x, y, sp, p, 2)
        assert vst == rst, "ALR #%02X: vm=%s py65=%s" % (imm, vst, rst)


def test_illegal_sbc_matches_legal_sbc():
    rng = np.random.default_rng(0x5BC)
    for _ in range(300):
        imm = int(rng.integers(0, 256))
        a, x, y, sp = (int(v) for v in rng.integers(0, 256, 4))
        p = _mk_p(rng)
        mem = bytearray(0x10000)
        mem[PC] = 0xEB
        mem[PC + 1] = imm  # illegal SBC #imm
        vst, _ = _run_vm(bytes(mem), a, x, y, sp, p)
        rst, _ = _run_py65(_img(bytes([0xE9, imm])), a, x, y, sp, p, 1)  # legal SBC #imm
        assert vst == rst, "SBC(illegal) #%02X: vm=%s py65=%s" % (imm, vst, rst)


def test_anc_and_carry_from_bit7():
    rng = np.random.default_rng(0xA0C)
    for _ in range(300):
        imm = int(rng.integers(0, 256))
        a = int(rng.integers(0, 256))
        p = _mk_p(rng)
        mem = bytearray(0x10000)
        mem[PC] = 0x0B
        mem[PC + 1] = imm  # ANC #imm
        vst, _ = _run_vm(bytes(mem), a, 0, 0, 0xFF, p)
        # AND part matches py65 AND #imm; ANC additionally sets C = bit7(result)
        rst, _ = _run_py65(_img(bytes([0x29, imm])), a, 0, 0, 0xFF, p, 1)
        res = a & imm
        assert vst[0] == res
        assert vst[5] == rst[5] and vst[9] == rst[9]  # Z, N match AND
        assert vst[4] == (res >> 7)  # C == bit7


def test_lxa_magic_constant():
    # $AB: A,X = (A | CONST) & imm  (NMS p.53) -- the fix over the flat prototype.
    for a in (0x00, 0x55, 0xFF, 0x80):
        for imm in (0x00, 0x0F, 0xF0, 0xFF, 0xEE):
            mem = bytearray(0x10000)
            mem[PC] = 0xAB
            mem[PC + 1] = imm
            vst, _ = _run_vm(bytes(mem), a, 0x00, 0, 0xFF, 0x20)
            exp = (a | P.MAGIC) & imm
            assert (
                vst[0] == exp and vst[1] == exp
            ), "LXA a=%02X imm=%02X -> A=%02X X=%02X exp=%02X" % (a, imm, vst[0], vst[1], exp)


def test_ane_magic_constant():
    # $8B: A = (A | CONST) & X & imm  (NMS p.51)
    for a in (0x00, 0x3C, 0xFF):
        for x in (0x0F, 0xF0, 0xFF):
            for imm in (0x00, 0x77, 0xFF):
                mem = bytearray(0x10000)
                mem[PC] = 0x8B
                mem[PC + 1] = imm
                vst, _ = _run_vm(bytes(mem), a, x, 0, 0xFF, 0x20)
                exp = (a | P.MAGIC) & x & imm
                assert vst[0] == exp, "ANE a=%02X x=%02X imm=%02X -> %02X exp %02X" % (
                    a,
                    x,
                    imm,
                    vst[0],
                    exp,
                )


def test_sax_stores_a_and_x_no_flags():
    rng = np.random.default_rng(0x5A4)
    for _ in range(200):
        a, x = int(rng.integers(0, 256)), int(rng.integers(0, 256))
        p = _mk_p(rng)
        mem = bytearray(0x10000)
        mem[PC] = 0x87
        mem[PC + 1] = ZP  # SAX $ZP
        vst, vmem = _run_vm(bytes(mem), a, x, 0, 0xFF, p)
        assert vmem[ZP] == (a & x)
        assert vst[:4] == (a, x, 0, 0xFF)  # A/X/Y/SP unchanged
        assert vst[4:] == _flags(p)  # flags unchanged


def test_sbx_x_equals_ax_minus_imm():
    rng = np.random.default_rng(0x58B)
    for _ in range(300):
        a, x = int(rng.integers(0, 256)), int(rng.integers(0, 256))
        imm = int(rng.integers(0, 256))
        mem = bytearray(0x10000)
        mem[PC] = 0xCB
        mem[PC + 1] = imm  # SBX #imm
        vst, _ = _run_vm(bytes(mem), a, x, 0, 0xFF, 0x20)
        ax = a & x
        assert vst[1] == ((ax - imm) & 0xFF)  # X
        assert vst[4] == (1 if ax >= imm else 0)  # C from the compare


def test_shx_stores_x_and_high_plus_one():
    # SHX $9E abs,y: mem[base+Y] = X & (high(base)+1) (stable form).
    base, y, x = 0x6400, 0x05, 0xFF
    mem = bytearray(0x10000)
    mem[PC] = 0x9E
    mem[PC + 1] = base & 0xFF
    mem[PC + 2] = base >> 8
    _, vmem = _run_vm(bytes(mem), 0, x, y, 0xFF, 0x20)
    assert vmem[base + y] == (x & (((base >> 8) + 1) & 0xFF))


def test_las_loads_a_x_sp_from_mem_and_sp():
    base, y, sp = 0x2000, 0x03, 0xC0
    mem = bytearray(0x10000)
    mem[base + y] = 0xAA
    mem[PC] = 0xBB
    mem[PC + 1] = base & 0xFF
    mem[PC + 2] = base >> 8  # LAS abs,y
    vst, _ = _run_vm(bytes(mem), 0x00, 0x00, y, sp, 0x20)
    v = 0xAA & sp
    assert vst[0] == v and vst[1] == v and vst[3] == v  # A, X, SP all = mem & SP
