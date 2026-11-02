"""Pointer high bytes are guarded where they are defined, not at each access through them."""

import pytest

from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

PTR = 0xFB

READS = """
sub:    LDY #$05
        LDA ($FB),Y
        STA $1800
        INY
        LDA ($FB),Y
        STA $1801
        INY
        LDA ($FB),Y
        STA $1802
        RTS
"""

# The pointer high byte is read from a table: the facts see $20 and $21 only.
TABLE = (
    """
entry:  STA $F0
        AND #$03
        TAX
        LDA #$00
        STA $FB
        LDA hitab,X
        STA $FC
        JSR sub
        RTS
"""
    + READS
    + """
hitab:  .byte $20, $21, $01, $00
"""
)

# The high byte is incremented in place: the input chooses where it starts.
BUMP = """
entry:  STA $F0
        LDA #$00
        STA $FB
        LDA $F0
        STA $FC
        INC $FC
        JSR sub
        RTS
""" + READS

# The high byte is overwritten through an index the analysis cannot bound, with a byte the
# image holds at $F1.
INDEXED = (
    """
entry:  TAX
        LDA #$00
        STA $FB
        LDA #$20
        STA $FC
        LDA $F1
        STA $F0,X
        JSR sub
        RTS
"""
    + READS
    + """
.org $00F1
        .byte $01
"""
)

# The pointer is set by one entry and read by the next.
PERSIST = """
init:   STA $F0
        LDA #$00
        STA $FB
        LDA $F0
        STA $FC
        RTS
entry:  JSR sub
        RTS
""" + READS

# The pointer is set on one branch only.
BRANCH = """
entry:  STA $F0
        LDA #$00
        STA $FB
        LDA $F0
        AND #$01
        BEQ skip
        LDA #$20
        STA $FC
skip:   JSR sub
        RTS
""" + READS

# A callee sets the pointer on one path only, and a second callee reads it.
CALLS = """
entry:  STA $F0
        LDA #$00
        STA $FB
        JSR set
        JSR sub
        RTS
set:    LDA $F0
        AND #$01
        BEQ out
        LDA #$20
        STA $FC
out:    RTS
""" + READS

# The pointer is found through X.
INDEXED_X = """
entry:  STA $F0
        LDA #$00
        STA $FB
        LDA $F0
        STA $FC
        LDX #$00
        LDA ($FB,X)
        STA $1800
        LDA ($FB,X)
        STA $1801
        LDA ($FB,X)
        STA $1802
        RTS
"""


def lift(src, calls, cycles=False, facts_calls=None):
    img, labels = assemble(src)
    pcs = [labels["entry"]]
    facts = facts_for(img, pcs, calls if facts_calls is None else facts_calls)
    prog = wholeprog.lift(img, pcs, [IO], facts, cycles=cycles)
    return img, pcs, prog


@pytest.mark.parametrize("cycles", [False, True])
def test_defined_pointer_needs_no_access_guard(cycles):
    calls = [(0, 0), (0, 1)]
    img, pcs, prog = lift(TABLE, calls, cycles)
    st = prog.stats
    assert st["stack_private"] and st["stack_guards"] == 0 and st["ptr_guards"] > 0
    mem = bytearray(img)
    mem[PTR + 1] = 0x20
    differential(img, prog, pcs, calls, mem=mem)
    assert not prog.deopts


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize(
    "src,seen",
    [(TABLE, [0, 1]), (BUMP, [0x1F, 0x20]), (INDEXED, [0x20]), (INDEXED_X, [0x20, 0x21])],
)
def test_pointer_into_the_stack_deopts_exactly(src, seen, cycles):
    seen = [(0, a) for a in seen]
    img, pcs, prog = lift(src, seen, cycles)
    assert prog.stats["stack_guards"] == 0
    calls = seen + [(0, a) for a in (2, 3, 0xFF, 0xF0, 0xFB, 0xFC, 0x0C, 0x20, 0)]
    differential(img, prog, pcs, calls)
    assert prog.deopts


def test_a_pointer_left_below_page_2_is_checked_at_entry():
    img, labels = assemble(PERSIST)
    pcs = [labels["init"], labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, [(0, 0x20), (1, 0)]))
    assert prog.stats["stack_guards"] == 0 and prog.stats["ptr_guards"] > 0
    mem = differential(img, prog, pcs, [(0, 0x20), (1, 0)])
    assert not prog.deopts
    for hi in (0x21, 0x01, 0x00, 0x7F):
        mem[0xFC] = hi
        mem[0x1800:0x1803] = bytes([0x55] * 3)
        differential(img, prog, pcs, [(1, 0)], mem=mem)
    assert prog.deopts == {pcs[1]: 2}


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("src", [BRANCH, CALLS])
def test_a_pointer_set_on_one_path_only_is_checked_at_entry(src, cycles):
    calls = [(0, 1), (0, 0)]
    img, pcs, prog = lift(src, calls, cycles)
    assert prog.stats["stack_guards"] == 0 and prog.stats["ptr_guards"] > 0
    for hi in (0x20, 0x01):
        mem = bytearray(img)
        mem[0xFC] = hi
        differential(img, prog, pcs, [(0, 0), (0, 1), (0, 0)], mem=mem)
    assert len(prog.deopts) == 1


@pytest.mark.parametrize("cycles", [False, True])
def test_interrupts_disable_pointer_guards(cycles):
    img, labels = assemble(TABLE)
    pcs = [labels["entry"]]
    facts = facts_for(img, pcs, [(0, 0), (0, 1)])
    prog = wholeprog.lift(img, pcs, [IO], facts, cycles=cycles, interrupts=True)
    assert prog.stats["ptr_guards"] == 0 and prog.stats["stack_guards"] > 0
