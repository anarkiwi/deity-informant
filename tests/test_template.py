"""``lifter.template``: per-opcode P-Code with operand-bound constants, and the SH* family
against "No More Secrets" (NMOS 6510 Unintended Opcodes, 'unstable address high byte' group)."""

import numpy as np
import pytest

from deity_informant import PcodeVM, lift, lifter
from deity_informant.lifter import Template, template

import _common as H

PC = H.PC
BOUND = {
    ((1,), "id"): lambda b1, b2: b1,
    ((1, 2), "word"): lambda b1, b2: b1 | b2 << 8,
    ((1,), "hi1"): lambda b1, b2: (b1 + 1) & 0xFF,
    ((2,), "hi1"): lambda b1, b2: (b2 + 1) & 0xFF,
}


@pytest.fixture
def fresh_cache():
    template.cache_clear()
    yield
    template.cache_clear()


@pytest.mark.parametrize("op", range(256))
def test_bound_template_is_the_lift(op):
    """Binding ``prov`` from an instruction's own bytes reproduces its lift at any address."""
    t = template(op)
    rng = np.random.default_rng(op)
    for pc in [0xFFFE, 0xFFFF] + [int(p) for p in rng.integers(0, 0x10000, 8)]:
        mem = bytearray(rng.integers(0, 256, 0x10000, dtype=np.uint8).tobytes())
        mem[pc] = op
        rec = lift(mem, pc)
        b1, b2 = mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF]
        ops = [[mn, out, [list(v) for v in ins]] for mn, out, ins in t.ops]
        for (i, j), key in t.prov.items():
            vn = ops[i][2][j]
            vn[1] = BOUND[key](b1, b2) & ((1 << (8 * vn[2])) - 1)
        assert ops == rec["ops"]
        assert (t.len, t.cyc) == (rec["len"], rec["cyc"])
        assert t.ctrl[0] == rec["ctrl"][0] and (t.pen or (None,))[0] == (rec["pen"] or (None,))[0]


@pytest.mark.parametrize(
    "op,length,cyc,ctrl,pen",
    [
        (0xA9, 2, 2, ("next",), None),  # LDA #
        (0xBD, 3, 4, ("next",), ("ax",)),  # LDA abs,X
        (0xB9, 3, 4, ("next",), ("ay",)),  # LDA abs,Y
        (0x9D, 3, 5, ("next",), None),  # STA abs,X: always 5
        (0xF0, 2, 2, ("br", 9, 1), ("branch",)),  # BEQ: Z = 1
        (0x10, 2, 2, ("br", 14, 0), ("branch",)),  # BPL: N = 0
        (0x4C, 3, 3, ("jmp",), None),
        (0x6C, 3, 5, ("jmpind",), None),
        (0x20, 3, 6, ("jsr",), None),
        (0x60, 1, 6, ("rts",), None),
        (0x40, 1, 6, ("rti",), None),
        (0x00, 1, 7, ("brk",), None),
        (0x02, 1, 0, ("jam",), None),
        (0x93, 2, 6, ("next",), None),  # SHA (zp),Y
        (0x9F, 3, 5, ("next",), None),  # SHA abs,Y
        (0x9E, 3, 5, ("next",), None),  # SHX abs,Y
        (0x9C, 3, 5, ("next",), None),  # SHY abs,X
        (0x9B, 3, 5, ("next",), None),  # TAS abs,Y
    ],
)
def test_fields(op, length, cyc, ctrl, pen):
    t = template(op)
    assert isinstance(t, Template)
    assert (t.len, t.cyc, t.ctrl, t.pen) == (length, cyc, ctrl, pen)


def test_iy_base_holds_the_loaded_pointer():
    t = template(0xB1)  # LDA (zp),Y
    assert t.pen[0] == "iy"
    mem = H.image([0xB1, 0x50], PC, {0x50: 0x34, 0x51: 0x12})
    vm = PcodeVM(mem)
    vm.volatile = False
    vm.reg[2] = 0x07
    vm.step(PC, {}, lift)
    assert vm.uniq[t.pen[1]] == 0x1234


def test_sh_provenance():
    # H+1 is the operand high byte for abs,X / abs,Y, and the byte loaded from zp+1 for (zp),Y.
    for op in (0x9B, 0x9C, 0x9E, 0x9F):
        assert ((2,), "hi1") in template(op).prov.values()
    t = template(0x93)
    assert t.prov == {(0, 0): ((1,), "id"), (1, 0): ((1,), "hi1")}
    assert t.ops[1][0] == "LOAD" and t.ops[1][2][0][0] == "c"


def test_unbound_operand_constant_fails(monkeypatch, fresh_cache):
    real = lifter.lift

    def no_prov(mem, pc):
        rec = real(mem, pc)
        rec["prov"]["ops"] = {}
        return rec

    monkeypatch.setattr(lifter, "lift", no_prov)
    with pytest.raises(NotImplementedError, match="A9 op 0 input 0"):
        template(0xA9)
    assert not template(0xEA).prov


# (code, zp pointer {addr: byte}, A, X, Y, SP) -> (address written, value, SP after), each
# derived by hand from NMS: the value is Value & {H+1}, H the high byte of the base before
# indexing (the pointer's for (zp),Y); without a page cross the address is the indexed one,
# with one its high byte is {H+1} & Value. A, X, Y and P are unchanged; TAS sets SP = A & X.
SH_CASES = [
    # SHA $7133,Y (NMS example): $F7 & $7F & $72
    ([0x9F, 0x33, 0x71], {}, 0xF7, 0x7F, 0x10, 0xFF, (0x7143, 0x72, 0xFF)),
    # SHA $71F0,Y crossing: $0F & $72 = $02, high byte $72 & $0F
    ([0x9F, 0xF0, 0x71], {}, 0x0F, 0xFF, 0x20, 0xFF, (0x0210, 0x02, 0xFF)),
    # SHA $FE00,Y is SAX $FE00,Y (NMS)
    ([0x9F, 0x00, 0xFE], {}, 0xC3, 0x5E, 0x07, 0xFF, (0xFE07, 0x42, 0xFF)),
    # SHA ($02),Y with ($02) = $FE00 is SAX ($02),Y (NMS)
    ([0x93, 0x02], {0x02: 0x00, 0x03: 0xFE}, 0xC3, 0x5E, 0x07, 0xFF, (0xFE07, 0x42, 0xFF)),
    # SHA ($50),Y, ($50) = $1234: H = $12 from the pointer, not the zp address
    ([0x93, 0x50], {0x50: 0x34, 0x51: 0x12}, 0xFF, 0xFF, 0x01, 0xFF, (0x1235, 0x13, 0xFF)),
    # SHA ($50),Y, ($50) = $70F0, crossing: $33 & $71 = $31, high byte $71 & $33
    ([0x93, 0x50], {0x50: 0xF0, 0x51: 0x70}, 0x33, 0xFF, 0x20, 0xFF, (0x3110, 0x31, 0xFF)),
    # SHA ($FF),Y: the pointer's high byte wraps to $00
    ([0x93, 0xFF], {0xFF: 0x10, 0x00: 0x20}, 0xFF, 0xFF, 0x02, 0xFF, (0x2012, 0x21, 0xFF)),
    # SHX $6430,Y (NMS example): X & $65
    ([0x9E, 0x30, 0x64], {}, 0x00, 0xFF, 0x05, 0xFF, (0x6435, 0x65, 0xFF)),
    # SHX $64F8,Y crossing: $A7 & $65 = $25, high byte $65 & $A7
    ([0x9E, 0xF8, 0x64], {}, 0x00, 0xA7, 0x10, 0xFF, (0x2508, 0x25, 0xFF)),
    # SHX $FFF0,Y: {H+1} = $00, crossing into page $00
    ([0x9E, 0xF0, 0xFF], {}, 0x00, 0xFF, 0x20, 0xFF, (0x0010, 0x00, 0xFF)),
    # SHY $7E00,X (NMS example): Y & $7F
    ([0x9C, 0x00, 0x7E], {}, 0x00, 0x03, 0xFF, 0xFF, (0x7E03, 0x7F, 0xFF)),
    # SHY $7EFF,X crossing: $EC & $7F = $6C, high byte $7F & $EC
    ([0x9C, 0xFF, 0x7E], {}, 0x00, 0x01, 0xEC, 0xFF, (0x6C00, 0x6C, 0xFF)),
    # TAS $4000,Y: SP = $F3 & $3F = $33, stores $33 & $41
    ([0x9B, 0x00, 0x40], {}, 0xF3, 0x3F, 0x02, 0xFF, (0x4002, 0x01, 0x33)),
    # TAS $4080,Y crossing: SP = $FE, $FE & $41 = $40, high byte $41 & $FE
    ([0x9B, 0x80, 0x40], {}, 0xFF, 0xFE, 0x90, 0x00, (0x4010, 0x40, 0xFE)),
]


@pytest.mark.parametrize("code,zp,a,x,y,sp,want", SH_CASES)
def test_sh_family_matches_nms(code, zp, a, x, y, sp, want):
    addr, val, sp_after = want
    mem = H.image(code, PC, zp)
    expect = bytearray(mem)
    expect[addr] = val
    vm = PcodeVM(mem)
    vm.volatile = False
    H.load_regs(vm, a, x, y, sp, 0xA5)
    before = list(vm.reg)
    vm.step(PC, {}, lift)
    assert vm.mem == expect
    assert vm.reg[:3] == [a, x, y] and vm.reg[3] == sp_after
    assert vm.reg[8:] == before[8:]
