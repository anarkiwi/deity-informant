"""Pointer cells and their regions (``docs/wholeprog.md``, *Pointer regions*): a player's
pointer is bounded by the block of song data its bases lie in, under the last-entry rule: the
lift's ``regions``, ``exact``, ``region_notes`` and ``ptr_cells`` facts.
"""

import test_cgen as T
from deity_informant import cgen

pytestmark = T.pytestmark

#: A row pointer in `rd`'s operand: zipped rows, a 16-bit add of `Y`, copied into `again`'s.
SMC = """
init:   TAX
        LDA rlo,X
        STA rd+1
        LDA rhi,X
        STA rd+2
        STX pos
        RTS
play:   LDY #0
rd:     LDA $FFFF,Y
        BNE adv
        INC pos
        LDX pos
        LDA rlo,X
        STA rd+1
        LDA rhi,X
        STA rd+2
        RTS
adv:    STA $C000
        LDA rd+1
        STA again+1
        LDA rd+2
        STA again+2
again:  LDA $FFFF
        STA $C001
        LDY #1
        TYA
        CLC
        ADC rd+1
        STA rd+1
        BCC done
        INC rd+2
done:   RTS
pos:    .byte 0
rlo:    .byte lo(r0), lo(r1), lo(r2)
rhi:    .byte hi(r0), hi(r1), hi(r2)
r0:     .byte 1, 2, 0
r1:     .byte 3, 0
r2:     .byte 4, 5, 6, 7
end:
"""

#: A zero-page row pointer stepped by `TYA; CLC; ADC lo; STA lo; BCC; INC hi`.
ZPADD = """
init:   TAX
        LDA rlo,X
        STA $FB
        LDA rhi,X
        STA $FC
        STX pos
        RTS
play:   LDY #0
rd:     LDA ($FB),Y
        BNE adv
        INC pos
        LDX pos
        LDA rlo,X
        STA $FB
        LDA rhi,X
        STA $FC
        RTS
adv:    STA $C000
        INY
nxt:    LDA ($FB),Y
        STA $C001
        TYA
        CLC
        ADC $FB
        STA $FB
        BCC done
        INC $FC
done:   RTS
pos:    .byte 0
rlo:    .byte lo(r0), lo(r1), lo(r2)
rhi:    .byte hi(r0), hi(r1), hi(r2)
r0:     .byte 1, 2, 3, 4, 0
r1:     .byte 5, 6, 0
r2:     .byte 7, 8, 9, 10, 11, 12
end:
"""

#: A song pointer reloaded from its data at a `$FF` jump, else stepped by `INC lo; BNE; INC hi`.
JUMP = """
init:   TAX
        LDA slo,X
        STA $FB
        LDA shi,X
        STA $FC
        RTS
play:   LDY #0
rd:     LDA ($FB),Y
        CMP #$FF
        BNE adv
        INY
jl:     LDA ($FB),Y
        TAX
        INY
jh:     LDA ($FB),Y
        STA $FC
        STX $FB
        RTS
adv:    STA $C000
        INC $FB
        BNE done
        INC $FC
done:   RTS
slo:    .byte lo(s0), lo(s1)
shi:    .byte hi(s0), hi(s1)
s0:     .byte 1, 2, $FF, lo(loop), hi(loop)
loop:   .byte 3, 4, $FF, lo(loop), hi(loop)
s1:     .byte 5, 6, 7
end:
"""

#: Per-voice copies of one pair, `($21,X)` and `STA $21,X`, reloaded from zipped rows.
VOICES = """
init:   RTS
play:   LDX #0
        JSR voice
        LDX #2
        JSR voice
        LDX #4
voice:  LDA ($21,X)
        BNE adv
        TXA
        LSR A
        TAY
        LDA vlo,Y
        STA $21,X
        LDA vhi,Y
        STA $22,X
        RTS
adv:    STA $C000,X
        INC $21,X
        BNE next
        INC $22,X
next:   RTS
vlo:    .byte lo(a0), lo(a1), lo(a2)
vhi:    .byte hi(a0), hi(a1), hi(a2)
a0:     .byte 1, 2, 0
a1:     .byte 3, 0
a2:     .byte 4, 5, 6
end:
"""

#: A pair zipped from the rows at the song, read at a `Y` each call steps past its row's end.
EXACT = """
init:   TAX
        LDA rlo,X
        STA $FB
        LDA rhi,X
        STA $FC
        LDA #0
        STA $FD
        RTS
play:   LDY $FD
rd:     LDA ($FB),Y
        STA $C000
        INC $FD
        RTS
rlo:    .byte lo(r0), lo(r1)
rhi:    .byte hi(r0), hi(r1)
r0:     .byte 1, 2, 3
r1:     .byte 4, 5, 6
end:
"""

#: A pointer zipped from tables whose words lie in two blocks a variable splits.
SPLIT = """
init:   RTS
play:   LDX pos
        INX
        STX pos
        LDA plo,X
        STA $FB
        LDA phi,X
        STA $FC
        LDY #0
rd:     LDA ($FB),Y
        STA $C000
        RTS
plo:    .byte lo(b0), lo(b1)
phi:    .byte hi(b0), hi(b1)
b0:     .byte 1, 2
pos:    .byte 0
b1:     .byte 3, 4
end:
"""


#: A pair chosen from a table of pointers by a position play steps, read at `Y` and `Y + 1`.
TABLE = """
init:   LDA #0
        STA pos
        STA $FD
        RTS
play:   LDX pos
        LDA rlo,X
        STA $FB
        LDA rhi,X
        STA $FC
        LDY $FD
rd:     LDA ($FB),Y
        STA $C000
        INY
        LDA ($FB),Y
        STA $C001
        INC $FD
        LDA $FD
        AND #3
        BNE done
        STA $FD
        INX
        TXA
        AND #1
        STA pos
done:   RTS
pos:    .byte 0
rlo:    .byte lo(r0), lo(r1)
rhi:    .byte hi(r0), hi(r1)
r0:     .byte 1, 2, 3, 4, 5
r1:     .byte 6, 7, 8, 9, 10
end:
"""

#: A pair play stores, read by the subroutine it calls with `Y` set.
CALL = """
init:   LDA #0
        STA pos
        RTS
play:   LDX pos
        LDA rlo,X
        STA $FB
        LDA rhi,X
        STA $FC
        LDY #2
        JSR emit
        LDY #0
        JSR emit
        INX
        TXA
        AND #1
        STA pos
        RTS
emit:   LDA ($FB),Y
        STA $C000
        RTS
pos:    .byte 0
rlo:    .byte lo(r0), lo(r1)
rhi:    .byte hi(r0), hi(r1)
r0:     .byte 1, 2, 3
r1:     .byte 4, 5, 6
end:
"""

#: One pair in two webs: zipped words read at a constant `Y` (exact), then a table holding a
#: word naming no data (`$0400`, the entry a song's init never selects) read at a position.
MIXED = """
init:   CMP #7
        BNE keep
        LDA #1
        STA sel
keep:   LDA #0
        STA pos
        RTS
play:   LDX pos
        LDA alo,X
        STA $FB
        LDA ahi,X
        STA $FC
        LDY #0
ra:     LDA ($FB),Y
        STA $C000
        INX
        TXA
        AND #1
        STA pos
        LDX sel
        LDA blo,X
        STA $FB
        LDA bhi,X
        STA $FC
        LDY pos
rb:     LDA ($FB),Y
        STA $C001
        RTS
pos:    .byte 0
sel:    .byte 0
alo:    .byte lo(a0), lo(a1)
ahi:    .byte hi(a0), hi(a1)
blo:    .byte lo(b0), $00
bhi:    .byte hi(b0), $04
a0:     .byte 1, 2
a1:     .byte 3, 4
b0:     .byte 5, 6
end:
"""


#: A pair play stores one of two constant words into, by a flag it toggles.
CONST = """
init:   LDA #0
        STA pos
        RTS
play:   LDA pos
        BNE two
        LDA #lo(r0)
        STA $FB
        LDA #hi(r0)
        STA $FC
        JMP go
two:    LDA #lo(r1)
        STA $FB
        LDA #hi(r1)
        STA $FC
go:     LDY #1
rd:     LDA ($FB),Y
        STA $C000
        LDA pos
        EOR #1
        STA pos
        RTS
pos:    .byte 0
r0:     .byte 1, 2, 3
r1:     .byte 4, 5, 6
end:
"""


def lifted(src, songs=1):
    """``(program, labels)`` of init and play of ``src`` under the last-entry rule with
    ``songs`` songs, the bytes past the program none of its own."""
    img, labels, loaded = T.assemble(src)
    img[labels["end"] : labels["end"] + 0x200] = bytes(range(0x80, 0x100)) * 4
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    p, _ = cgen.lift(img, ents, [T.W.IO], loaded=loaded, init=0, songs=range(songs), overrun="last")
    return p, labels


def regions(p):
    """``{pc: (lo, hi)}`` of ``p.regions``."""
    return {pc: (lo, hi) for pc, lo, hi in p.regions}


def test_self_modified_operand_reads_its_region():
    """The row pointer in `rd`'s operand and its copy in `again`'s read the rows' block, their
    bases the zipped words init and the reload past the tables' extent take."""
    p, labels = lifted(SMC)
    got = regions(p)
    assert got[labels["rd"]] == got[labels["again"]] == (labels["rlo"], labels["end"] - 1)


def test_zero_page_pointer_added_with_carry():
    """A pair stepped by an add whose carry goes into its high byte keeps its bases' block."""
    p, labels = lifted(ZPADD)
    got = regions(p)
    assert got[labels["rd"]] == got[labels["nxt"]] == (labels["rlo"], labels["end"] - 1)


def test_pointer_reloaded_from_data():
    """A pair whose bytes are read through itself points into what it points into."""
    p, labels = lifted(JUMP)
    got = regions(p)
    assert {got[labels[k]] for k in ("rd", "jl", "jh")} == {(labels["slo"], labels["end"] - 1)}


def test_per_voice_copies():
    """Per-voice pairs `($21,X)` stored by `STA $21,X` from the zipped rows at the voice read
    the rows' block (init's null pointers name none)."""
    p, labels = lifted(VOICES)
    assert regions(p)[labels["voice"]] == (labels["vlo"], labels["end"] - 1)


def test_bases_in_two_blocks_have_no_region():
    """Words in two blocks a variable splits give the pointer no region: it is reported."""
    p, labels = lifted(SPLIT)
    assert labels["rd"] not in regions(p)
    assert "2 blocks" in dict(p.region_notes)[labels["rd"]]


def test_exact_pointer_holds_its_bases():
    """A pair only ever holding the zipped rows' words is exact at its read, whose bases are
    the least and greatest row."""
    p, labels = lifted(EXACT, songs=2)
    assert regions(p)[labels["rd"]] == (labels["rlo"], labels["end"] - 1)
    got = {pc: (lo, hi) for pc, lo, hi in p.exact}
    assert got[labels["rd"]] == (labels["r0"], labels["r1"])


def test_stepped_pointer_is_not_exact():
    """A pair adding to itself may hold any word past its bases: its accesses are not exact."""
    p, labels = lifted(ZPADD)
    assert labels["rd"] in regions(p)
    assert not {labels["rd"], labels["nxt"]} & {e[0] for e in p.exact}


def test_pointer_cells_list_their_stores_and_accesses():
    """The pair play stores from zipped tables is one cell: its low byte, its region, the
    store of its high byte (a zip of the two tables, mated with the low byte's) and the two
    reads through it, each exact with the stores' definitions."""
    p, labels = lifted(TABLE)
    (cell,) = [c for c in p.ptr_cells if c[0] == 0xFB]
    _, region, highs, lows, accs, _, _ = cell
    assert region == (labels["rlo"], labels["end"] - 1)
    (high,) = highs
    (low,) = lows
    assert high[1] == low[0] and low[1] == high[0] and high[2] == "zip"
    assert [(a[0], a[1], a[2], a[3]) for a in accs] == [
        (pc, True, [low[0]], [high[0]]) for pc in (labels["rd"], labels["rd"] + 6)
    ]
