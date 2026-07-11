"""PcodeVM control-flow, drivers, self-modification, and remaining illegals."""

import pytest

import deity_informant as P
from deity_informant import PcodeVM, run_sub, run_irq, run_irq_driven, lift


def _vm(prog, org=0x1000):
    mem = bytearray(0x10000)
    mem[org : org + len(prog)] = prog
    return PcodeVM(mem)


def test_jsr_rts_roundtrip():
    # JSR $1010 ; (sub: LDA #$07; STA $D400; RTS) ; then STA $D401
    mem = bytearray(0x10000)
    mem[0x1000:0x1006] = bytes([0x20, 0x10, 0x10, 0x8D, 0x01, 0xD4])
    mem[0x1010:0x1016] = bytes([0xA9, 0x07, 0x8D, 0x00, 0xD4, 0x60])
    vm = PcodeVM(mem)
    run_sub(vm, 0x1000, {}, lift)
    assert vm.mem[0xD400] == 0x07


def test_jmp_indirect():
    # JMP ($00F0) with pointer -> $1020 (LDA #$01; STA $D400; RTS)
    vm = _vm(bytes([0x6C, 0xF0, 0x00]))
    vm.mem[0x00F0] = 0x20
    vm.mem[0x00F1] = 0x10
    vm.mem[0x1020:0x1026] = bytes([0xA9, 0x01, 0x8D, 0x00, 0xD4, 0x60])
    run_sub(vm, 0x1000, {}, lift)
    assert vm.mem[0xD400] == 0x01


def test_brk_vectors_and_pushes_frame():
    # BRK sets I, pushes return+status, and jumps through the $FFFE vector.
    vm = _vm(bytes([0x00]))  # BRK at $1000
    vm.mem[0xFFFE] = 0x30
    vm.mem[0xFFFF] = 0x10
    sp0 = vm.reg[3]
    nxt = vm.step(0x1000, {}, lift)
    assert nxt == 0x1030  # vectored through $FFFE
    assert vm.reg[10] == 1  # I set
    assert vm.reg[3] == (sp0 - 3) & 0xFF  # 2 return bytes + status pushed


def test_rti_restores_status_and_returns():
    # RTI pops status then the return address.
    vm = _vm(bytes([0x40]), org=0x1030)  # RTI
    # push status (all flags clear) then return address $1002
    vm.mem[0x1FF] = 0x10
    vm.mem[0x1FE] = 0x02
    vm.mem[0x1FD] = 0x20  # status: only the unused bit set -> C/Z/I/... = 0
    vm.reg[3] = 0xFC
    vm.reg[10] = 1  # I currently set
    nxt = vm.step(0x1030, {}, lift)
    assert nxt == 0x1002
    assert vm.reg[10] == 0  # I restored from the popped status


def test_jam_halts():
    vm = _vm(bytes([0x02]))
    with pytest.raises(RuntimeError, match="JAM"):
        vm.step(0x1000, {}, lift)


def test_self_modifying_code_relifts():
    # cache is keyed by (pc, bytes); rewriting the operand must re-lift.
    vm = _vm(bytes([0xA9, 0x11]))  # LDA #$11
    cache = {}
    vm.step(0x1000, cache, lift)
    assert vm.reg[0] == 0x11
    vm.mem[0x1001] = 0x22  # self-modify the immediate operand
    vm.step(0x1000, cache, lift)
    assert vm.reg[0] == 0x22
    assert len(cache) == 2  # two distinct (pc, bytes) records


def test_run_irq_handler_unwinds_on_rti():
    # handler: LDA #$05; STA $D404; RTI  -- run_irq must return after the RTI
    vm = _vm(bytes([0xA9, 0x05, 0x8D, 0x04, 0xD4, 0x40]), org=0x2000)
    run_irq(vm, 0x2000, {}, lift)
    assert vm.mem[0xD404] == 0x05  # handler ran and stored to SID


def test_run_irq_driven_fires_source():
    # one CIA-like source; handler acks $DC0D (read-clear) and stores, then RTI.
    handler = 0x3000
    vm = _vm(
        bytes([0xAD, 0x0D, 0xDC, 0x8D, 0x05, 0xD4, 0x40]), org=handler
    )  # LDA $DC0D;STA $D405;RTI

    def enter(machine):
        machine.ciaicr = 0x81  # raise CIA interrupt-source flag

    sources = [{"period": 20000, "next": 100, "enter": enter}]
    run_irq_driven(vm, handler, 5000_0, sources, {}, lift)
    assert vm.mem[0xD405] == 0x81  # handler observed the raised source


# ---- remaining illegal semantics (lifter branch coverage) --------------------
def _run1(prog, a=0, x=0, y=0, sp=0xFF, p=0x20, org=0x1000):
    vm = _vm(prog, org)
    vm.volatile = False
    vm.reg[0], vm.reg[1], vm.reg[2], vm.reg[3] = a, x, y, sp
    vm.step(org, {}, lift)
    return vm


def test_arr_ror_after_and():
    # ARR #imm: A = ror(A & imm); C = bit6, V = bit6 ^ bit5
    a, imm = 0xFF, 0xFF
    vm = _run1(bytes([0x6B, imm]), a=a, p=0x20)  # C=0
    r = (a & imm) >> 1  # carry-in 0
    assert vm.reg[0] == r
    assert vm.reg[8] == ((r >> 6) & 1)
    assert vm.reg[13] == (((r >> 6) & 1) ^ ((r >> 5) & 1))


def test_sha_abs_y_and_indirect_y():
    # SHA abs,y ($9F): mem[base+Y] = A & X & (high(base)+1)
    base, y, a, x = 0x6300, 0x04, 0xFF, 0xFF
    vm = _run1(bytes([0x9F, base & 0xFF, base >> 8]), a=a, x=x, y=y)
    assert vm.mem[base + y] == (a & x & (((base >> 8) + 1) & 0xFF))
    # SHA (zp),y ($93): pointer from zero page
    vm2 = _vm(bytes([0x93, 0x80]))
    vm2.volatile = False
    vm2.mem[0x80] = 0x00
    vm2.mem[0x81] = 0x62  # pointer -> $6200
    vm2.reg[0], vm2.reg[1], vm2.reg[2] = 0xFF, 0xFF, 0x04
    vm2.step(0x1000, {}, lift)
    assert vm2.mem[0x6204] == (0xFF & 0xFF & ((0x62 + 1) & 0xFF))


def test_shy_abs_x():
    base, x, y = 0x7000, 0x03, 0xFF
    vm = _run1(bytes([0x9C, base & 0xFF, base >> 8]), x=x, y=y)
    assert vm.mem[base + x] == (y & (((base >> 8) + 1) & 0xFF))


def test_tas_sets_sp_and_stores():
    base, y, a, x = 0x8000, 0x02, 0xF0, 0x3C
    vm = _run1(bytes([0x9B, base & 0xFF, base >> 8]), a=a, x=x, y=y)
    assert vm.reg[3] == (a & x)  # SP = A & X
    assert vm.mem[base + y] == ((a & x) & (((base >> 8) + 1) & 0xFF))


def test_every_opcode_lifts():
    # the table is dense (all 256 NMOS bytes defined); lifting any byte must
    # produce a record, never a silent skip. Unknown bytes are a hard error.
    assert len(P.OPS) == 256
    mem = bytearray(0x10000)
    for op in range(256):
        mem[0x1000] = op
        rec = lift(mem, 0x1000)
        assert rec["len"] in (1, 2, 3) and "ctrl" in rec
