"""Guards the lifter proves redundant are not emitted; the rest stay and deopt exactly."""

import pytest

from test_cgen import VOICEBYTE
from test_wholeprog import differential, exact
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


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("src", [COUNT_UP, COUNT_DOWN, VOICES])
def test_bounded_indexed_stores_need_no_code_guard(src, cycles):
    prog, _ = exact(src, [(0, 0)] * 3, [(0, 0)], cycles=cycles)
    assert prog.stats["code_guards"] == 0 and not prog.deopts


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
@pytest.mark.parametrize(
    "src,a,guards",
    [(INPUT_INDEX, 0x41, 1), (DEVICE_JUMP, 1, None)],
    ids=["input-dependent-index-keeps-its-guard", "code-rewritten-to-jump-into-a-device"],
)
def test_a_store_the_first_call_does_not_make_deopts(src, a, guards, cycles):
    """A call with ``A = a``, unlike the first call the facts see, stores outside what was
    proven: an index from the input keeps its code guard, and code rewritten into a jump to a
    device leaves the lift; both deopt."""
    prog, _ = exact(src, [(0, 0), (0, a), (0, 0)], [(0, 0)], cycles=cycles)
    assert guards is None or prog.stats["code_guards"] == guards
    assert sum(prog.deopts.values()) > 0


def test_left_out_guard_keeps_the_bound_it_relies_on():
    """An index guard the bound of the per-voice bytes proves is left out of the static lift,
    so the program matches memory only while those bytes stay inside their bound."""
    img, labels = assemble(VOICEBYTE)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], entry_sp=0xFD, loaded=(0x1000, labels["end"]))
    differential(img, prog, pcs, [(0, 0)] * 3)
    mem = bytearray(img)
    assert prog.matches(mem)
    mem[labels["state"] + 7] = 0x10
    assert not prog.matches(mem)
