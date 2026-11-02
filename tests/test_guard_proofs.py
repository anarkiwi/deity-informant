"""Guards the lifter proves redundant are not emitted; the rest stay and deopt exactly."""

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

SUB = """
.org $1120
sub:    LDA #$07
        STA $C000
        RTS
"""

COUNT_UP = """
entry:  LDX #$00
loop:   TXA
        STA $10E0,X
        INX
        CPX #$20
        BNE loop
        JSR sub
        RTS
""" + SUB

COUNT_DOWN = """
entry:  LDX #$07
loop:   STA $10E0,X
        DEX
        BPL loop
        JSR sub
        RTS
""" + SUB

VOICES = """
entry:  LDX #$00
        JSR voice
        INX
        JSR voice
        INX
        JSR voice
        RTS
voice:  LDA #$05
        STA $10E0,X
        JSR note
        STA $10E8,X
        RTS
.org $1140
note:   LDA #$09
        RTS
"""

INPUT_INDEX = """
entry:  TAX
        LDA #$EA
        STA $10E0,X
        JSR sub
        RTS
""" + SUB


def lift(src, cycles, calls):
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls[:1]), cycles=cycles)
    return img, pcs, prog


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("src", [COUNT_UP, COUNT_DOWN, VOICES])
def test_bounded_indexed_stores_need_no_code_guard(src, cycles):
    calls = [(0, 0)] * 3
    img, pcs, prog = lift(src, cycles, calls)
    assert prog.stats["code_guards"] == 0
    differential(img, prog, pcs, calls)
    assert not prog.deopts


@pytest.mark.parametrize("cycles", [False, True])
def test_input_dependent_index_keeps_its_guard(cycles):
    calls = [(0, 0), (0, 0x41), (0, 0)]
    img, pcs, prog = lift(INPUT_INDEX, cycles, calls)
    assert prog.stats["code_guards"] == 1
    differential(img, prog, pcs, calls)
    assert sum(prog.deopts.values()) > 0


DEVICE_JUMP = (
    """
entry:  STA $F0
        LDA $F0
        BEQ skip
        LDA #$4C
        STA tgt
skip:   JSR sub
        RTS
"""
    + """
sub:    LDA #$01
tgt:    STA $C000
        RTS
"""
)


@pytest.mark.parametrize("cycles", [False, True])
def test_code_rewritten_to_jump_into_a_device_deopts(cycles):
    calls = [(0, 0), (0, 1), (0, 0)]
    img, pcs, prog = lift(DEVICE_JUMP, cycles, calls)
    differential(img, prog, pcs, calls)
    assert sum(prog.deopts.values()) > 0
