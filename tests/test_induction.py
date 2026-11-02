"""Induction variables of loops: start, step and trip bound; trip bounds tighten pointer windows."""

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

LOOPS = """
entry:  LDA #$06
        STA $F0
        LDY #$00
        LDX #$08
copy:   LDA $1000,Y
        STA $1800,Y
        INY
        DEX
        BNE copy
count:  DEC $F0
        BNE count
        LDX #$10
down:   DEX
        CPX #$03
        BCS down
        LDY #$07
bpl:    DEY
        BPL bpl
        LDA #$00
add:    CLC
        ADC #$03
        CMP #$0F
        BNE add
        LDX $F1
free:   INX
        BNE free
        RTS
"""


def test_loops_have_their_induction_variables():
    img, labels = assemble(LOOPS)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, [(0, 0)]))
    got = {(i["head"], i["reg"] or i["addr"]): i for i in prog.induction}
    want = {
        ("copy", "x"): (8, 8, -1, 8),
        ("copy", "y"): (0, 0, 1, 8),
        ("count", 0xF0): (6, 6, -1, 6),
        ("down", "x"): (16, 16, -1, 14),
        ("bpl", "y"): (7, 7, -1, 8),
        ("add", "a"): (0, 0, 3, 5),
        ("free", "x"): (0, 255, 1, 256),
    }
    for (head, var), (lo, hi, step, trips) in want.items():
        i = got.pop((labels[head], var))
        assert (i["lo"], i["hi"], i["step"], i["trips"]) == (lo, hi, step, trips), (head, var)
    assert not got
    differential(img, prog, pcs, [(0, 0)])


# Y alone widens to any byte; the trip bound of X keeps $F0 + Y in page 0.
COUNTED = """
entry:  STA $FB
        LDA #$00
        STA $FC
        JSR sub
        RTS
sub:    LDY #$00
        LDX #$08
loop:   LDA ($FB),Y
        EOR ($FB),Y
        STA $1800,Y
        INY
        DEX
        BNE loop
        RTS
"""


def test_a_trip_bound_tightens_the_pointer_window():
    img, labels = assemble(COUNTED)
    pcs = [labels["entry"]]
    calls = [(0, 0x10), (0, 0xF0)]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls))
    trips = {(i["head"], i["reg"]): i["trips"] for i in prog.induction}
    assert trips[(labels["loop"], "y")] == 8
    assert prog.stats["stack_private"] and prog.stats["stack_guards"] == 0
    differential(img, prog, pcs, calls + [(0, 0xF8), (0, 0xF9), (0, 0x10)])
    assert sum(prog.deopts.values()) == 1
