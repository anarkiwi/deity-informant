"""Zero-page pointers are guarded where they are defined, not at each access through them."""

import pytest

from test_wholeprog import differential, lift_entry
from wholeprog_progs import IO
from deity_informant import NATIVE

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


# The pointer's high byte comes from a table (the facts see $20 only), after calls that have
# returned: a call that points it at page 1 reads their return addresses.
DEAD = (
    """
entry:  STA $F0
        AND #$03
        TAX
        JSR outer
        LDA #$F5
        STA $FB
        LDA hitab,X
        STA $FC
        JSR sub
        RTS
outer:  JSR inner
        RTS
inner:  RTS
"""
    + READS
    + """
hitab:  .byte $20, $20, $01, $00
"""
)


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize(
    "vol", [(IO,), (IO, (0xF0, PTR))], ids=["io", "volatile-range-ends-before-its-end-address"]
)
def test_defined_pointer_needs_no_access_guard(vol, cycles):
    """A pointer defined where it is set needs no guard at its accesses, also beside a volatile
    range ending at its address (which the range leaves out)."""
    calls = [(0, 0), (0, 1)]
    img, pcs, prog, _ = lift_entry(TABLE, calls, volatile=vol, cycles=cycles)
    st = prog.stats
    assert st["stack_private"] and st["stack_guards"] == 0 and st["ptr_guards"] > 0
    mem = bytearray(img)
    mem[PTR + 1] = 0x20
    differential(img, prog, pcs, calls, mem=mem, volatile=vol)
    assert not prog.deopts


@pytest.mark.parametrize("cycles", [False, True])
def test_a_load_that_may_read_page_1_reads_the_returned_frames_exactly(cycles):
    img, pcs, prog, _ = lift_entry(DEAD, [(0, 0), (0, 1)], cycles=cycles)
    mem = differential(img, prog, pcs, [(0, 0), (0, 1), (0, 2), (0, 3), (0, 2)])
    assert mem[0x1800:0x1803] != bytes(3)


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize(
    "src,seen",
    [(TABLE, [0, 1]), (BUMP, [0x1F, 0x20]), (INDEXED, [0x20]), (INDEXED_X, [0x20, 0x21])],
)
def test_pointer_into_the_stack_deopts_exactly(src, seen, cycles):
    seen = [(0, a) for a in seen]
    img, pcs, prog, _ = lift_entry(src, seen, cycles=cycles)
    assert prog.stats["stack_guards"] == 0
    calls = seen + [(0, a) for a in (2, 3, 0xFF, 0xF0, 0xFB, 0xFC, 0x0C, 0x20, 0)]
    differential(img, prog, pcs, calls)
    assert prog.deopts


@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("src", [BRANCH, CALLS])
def test_a_pointer_set_on_one_path_only_is_checked_at_entry(src, cycles):
    calls = [(0, 1), (0, 0)]
    img, pcs, prog, _ = lift_entry(src, calls, cycles=cycles)
    assert prog.stats["stack_guards"] == 0 and prog.stats["ptr_guards"] > 0
    for hi in (0x20, 0x01):
        mem = bytearray(img)
        mem[0xFC] = hi
        differential(img, prog, pcs, [(0, 0), (0, 1), (0, 0)], mem=mem)
    assert len(prog.deopts) == 1


# The pair is defined from tables by the input: zero-page targets ($10 + Y and $E0 + Y stay in
# page 0), targets at $20F8 and $20FC and two that reach the stack page (hi 0 with lo + Y past
# $FF, hi 1). Each store pair is checked once, after the high byte.
PAIRS = (
    """
entry:  AND #$07
        TAX
        LDA lotab,X
        STA $FB
        LDA hitab,X
        STA $FC
        JSR sub
        RTS
"""
    + READS
    + """
lotab:  .byte $10, $E0, $F8, $FC, $F8, $FC, $E0, $10
hitab:  .byte $00, $00, $20, $00, $01, $20, $00, $00
"""
)


def test_zero_page_pointers_need_no_access_guard():
    calls = [(0, 0), (0, 1), (0, 2)]
    img, pcs, prog, _ = lift_entry(PAIRS, calls)
    st = prog.stats
    assert st["stack_private"] and st["stack_guards"] == 0 and st["ptr_guards"] == 1
    differential(img, prog, pcs, calls + [(0, 5), (0, 6), (0, 7), (0, 0)])
    assert not prog.deopts


@pytest.mark.parametrize("cycles", [False, True])
def test_pointers_into_the_stack_page_deopt_after_the_pair(cycles):
    calls = [(0, 0), (0, 1), (0, 2)]
    img, pcs, prog, _ = lift_entry(PAIRS, calls, cycles=cycles)
    assert prog.stats["stack_guards"] == 0
    differential(img, prog, pcs, [(0, 3), (0, 0), (0, 4), (0, 2)])
    assert sum(prog.deopts.values()) == 2


def test_a_low_byte_past_the_window_keeps_the_access_guard():
    calls = [(0, 0), (0, 5)]
    img, pcs, prog, _ = lift_entry(PAIRS, calls)
    assert prog.stats["stack_private"] and prog.stats["stack_guards"] > 0
    differential(img, prog, pcs, calls + [(0, 3), (0, 1), (0, 4)])
    assert sum(prog.deopts.values()) == 2


# The second store of the pair waits for the first: the pointer between them ($F8 with high byte
# 0) would reach page 1, though neither the old pointer nor the new one does.
TRANSIENT = """
entry:  AND #$01
        TAX
        LDA lotab,X
        STA $FB
        LDA hitab,X
        STA $FC
        JSR sub
        RTS
lotab:  .byte $10, $F8
hitab:  .byte $00, $20
""" + READS


def test_the_transient_pointer_between_the_stores_is_not_checked():
    calls = [(0, 0), (0, 1)]
    img, pcs, prog, _ = lift_entry(TRANSIENT, calls)
    assert prog.stats["stack_guards"] == 0
    differential(img, prog, pcs, [(0, 0), (0, 1), (0, 0), (0, 1)])
    assert not prog.deopts


ZP_PERSIST = """
init:   STA $FB
        LDA #$00
        STA $FC
        RTS
entry:  JSR sub
        RTS
""" + READS


@pytest.mark.parametrize(
    "src,seen,calls,pokes,deopts",
    [
        (
            PERSIST,
            [(0, 0x20), (1, 0)],
            [(0, 0x20), (1, 0)],
            [{0xFC: hi, 0x1800: 0x55, 0x1801: 0x55, 0x1802: 0x55} for hi in (0x21, 1, 0, 0x7F)],
            2,
        ),
        (
            ZP_PERSIST,
            [(0, 0x40), (1, 0)],
            [(0, 0x40), (1, 0), (0, 0xF0), (1, 0)],
            [
                {0xFB: lo, 0xFC: hi}
                for lo, hi in ((0xF8, 0), (0x40, 1), (0xFA, 0), (0xF9, 0), (0, 2))
            ],
            3,
        ),
    ],
    ids=["left-below-page-2", "zero-page"],
)
def test_a_pointer_init_leaves_is_checked_at_entry(src, seen, calls, pokes, deopts):
    """A pointer ``init`` leaves for ``entry`` is checked at ``entry``: ``calls`` replay
    without a deopt, and of the pointers then poked between calls of ``entry`` (one left below
    page 2, or one in the zero page) those outside the checked window deopt there."""
    img, pcs, prog, _ = lift_entry(src, seen, ("init", "entry"))
    assert prog.stats["stack_guards"] == 0 and prog.stats["ptr_guards"] > 0
    mem = differential(img, prog, pcs, calls)
    assert not prog.deopts
    for poke in pokes:
        for a, v in poke.items():
            mem[a] = v
        differential(img, prog, pcs, [(1, 0)], mem=mem)
    assert prog.deopts == {pcs[1]: deopts}


# The pointer at $FF has its high byte at $00.
WRAP = """
entry:  STA $FF
        LDA #$00
        STA $00
        JSR sub
        RTS
sub:    LDY #$03
        LDA ($FF),Y
        STA $1800
        DEY
        LDA ($FF),Y
        STA $1801
        RTS
"""


def test_a_pointer_at_ff_wraps_to_its_high_byte_at_00():
    calls = [(0, 0x10), (0, 0x80)]
    img, pcs, prog, _ = lift_entry(WRAP, calls)
    assert prog.stats["stack_guards"] == 0
    differential(img, prog, pcs, calls + [(0, 0xFE), (0, 0x20)])
    assert sum(prog.deopts.values()) == 1
