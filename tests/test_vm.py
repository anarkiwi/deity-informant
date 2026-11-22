"""PcodeVM control-flow, drivers, self-modification, and remaining illegals."""

import pytest

import deity_informant as P
from deity_informant import PcodeVM, run_sub, run_irq, run_irq_driven, lift, vector

import _common as H


def _vm(prog, org=0x1000):
    return PcodeVM(H.image(prog, org))


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


def _chain_vm():
    # A: INC $0400; vector <- B; RTI.  B: INC $0401; vector <- A; RTI.
    def handler(counter, nxt_hi):
        return bytes(
            [0xEE, counter, 0x04, 0xA9, 0, 0x8D, 0xFE, 0xFF, 0xA9, nxt_hi, 0x8D, 0xFF, 0xFF, 0x40]
        )

    vm = _vm(handler(0x00, 0x31), org=0x3000)
    vm.mem[0x3100:0x310E] = handler(0x01, 0x30)
    vm.mem[0xFFFE], vm.mem[0xFFFF] = 0x00, 0x30
    return vm


@pytest.mark.parametrize(
    "handler,counts",
    [(vector(0xFFFE), (3, 2)), (0x3000, (5, 0))],
    ids=["follows-rewritten-vector", "fixed-handler-ignores-vector"],
)
def test_run_irq_driven(handler, counts):
    """A vector handler follows the chain's rewrites of $FFFE, alternating the two handlers;
    a fixed handler address runs the first handler every time."""
    vm = _chain_vm()
    sources = [{"period": 20000, "next": 100, "enter": lambda m: None}]
    run_irq_driven(vm, handler, 100_000, sources, {}, lift)
    assert (vm.mem[0x0400], vm.mem[0x0401]) == counts


def test_run_irq_resolves_vector_at_entry():
    vm = _chain_vm()
    for _ in range(3):
        run_irq(vm, vector(0xFFFE), {}, lift)
    assert (vm.mem[0x0400], vm.mem[0x0401]) == (2, 1)
    assert vector(0xFFFE)(vm) == 0x3100


# ---- remaining illegal semantics (lifter branch coverage) --------------------
def _run1(prog, org=0x1000, **regs):
    return H.step(H.image(prog, org), org, **regs)


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


@pytest.mark.parametrize(
    "op,base,regs,at,value,sp",
    [
        (0x9C, 0x7000, {"x": 0x03, "y": 0xFF}, 0x03, 0xFF, None),
        (0x9B, 0x8000, {"a": 0xF0, "x": 0x3C, "y": 0x02}, 0x02, 0xF0 & 0x3C, 0xF0 & 0x3C),
    ],
    ids=["shy-abs-x", "tas-sets-sp-and-stores"],
)
def test_store_and_high_plus_one(op, base, regs, at, value, sp):
    """SHY abs,X stores ``Y & (high(base)+1)`` at ``base+X``; TAS abs,Y sets ``SP = A & X``
    and stores ``A & X & (high(base)+1)`` at ``base+Y``."""
    vm = _run1(bytes([op, base & 0xFF, base >> 8]), **regs)
    assert sp is None or vm.reg[3] == sp
    assert vm.mem[base + at] == (value & (((base >> 8) + 1) & 0xFF))


def test_every_opcode_lifts():
    # the table is dense (all 256 NMOS bytes defined); lifting any byte must
    # produce a record, never a silent skip. Unknown bytes are a hard error.
    assert len(P.lifter.OPCODES) == 256
    mem = bytearray(0x10000)
    for op in range(256):
        mem[0x1000] = op
        rec = lift(mem, 0x1000)
        assert rec["len"] in (1, 2, 3) and "ctrl" in rec


class _Bus(PcodeVM):
    """A VM that logs its memory accesses as ``(address, is_write)``."""

    __slots__ = ("log",)

    def _rd(self, addr, sz):
        self.log.append((addr, False))
        return PcodeVM._rd(self, addr, sz)

    def _wr(self, addr, val, sz):
        self.log.append((addr, True))
        PcodeVM._wr(self, addr, val, sz)


# code, X, Y, the accesses with the dummy ones, the accesses without
R_, W_ = False, True
BUS = {
    "lda_absx_cross": (
        [0xBD, 0xFF, 0x10],
        1,
        0,
        [(0x1000, R_), (0x1100, R_)],
        [(0x1100, R_)],
    ),
    "lda_absx_same_page": ([0xBD, 0x00, 0x10], 1, 0, [(0x1001, R_)], [(0x1001, R_)]),
    "sta_absx_same_page": ([0x9D, 0x00, 0x10], 1, 0, [(0x1001, R_), (0x1001, W_)], [(0x1001, W_)]),
    "inc_absx_cross": (
        [0xFE, 0xFF, 0x10],
        1,
        0,
        [(0x1000, R_), (0x1100, R_), (0x1100, W_), (0x1100, W_)],
        [(0x1100, R_), (0x1100, W_)],
    ),
    "inc_zp": ([0xE6, 0x10], 0, 0, [(0x10, R_), (0x10, W_), (0x10, W_)], [(0x10, R_), (0x10, W_)]),
    "nop": ([0xEA], 0, 0, [(0x1001, R_)], []),
    "bne_taken_cross": ([0xD0, 0x80], 0, 0, [(0x1002, R_), (0x1082, R_)], []),
}


@pytest.mark.parametrize("dummies", [True, False])
@pytest.mark.parametrize("name", sorted(BUS))
def test_dummy_accesses_are_the_vms_to_make(name, dummies):
    code, x, y, with_dummies, without = BUS[name]
    vm = _Bus(H.image(bytes(code)))
    vm.volatile, vm.dummies, vm.log = False, dummies, []
    vm.reg[1], vm.reg[2] = x, y
    vm.step(0x1000, {}, lift)
    assert vm.log == (with_dummies if dummies else without)
