"""deity_informant.postinit: the state init leaves per song, the copies putting it in place,
and init's run with each byte's provenance and the pointer variables it leaves."""

import pytest

import wholeprog_progs as W
from deity_informant import NATIVE, postinit, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

TAGS = """
init:   LDA src
        PHA
        LDA #0
        PLA
        STA o_copy
        TAX
        INX
        STX o_inx
        LDA src
        ASL A
        STA o_asl
        LDA src
        AND #$0F
        STA o_and
        INC src2
        LDA src2
        STA o_inc
        SED
        CLC
        LDA src
        ADC #1
        CLD
        STA o_dec
        JSR sub
        LDA #lo(w)
        STA $FB
        LDA #hi(w)
        STA $FC
        LDY #0
        LDA ($FB),Y
        STA $FD
        INY
        LDA ($FB),Y
        STA $FE
        LDA ($FD),Y
        STA o_via
        SEC
        LDA w
        SBC #1
        STA o_lo
        LDA w+1
        SBC #0
        STA o_hi
        LDA $C000
        STA o_io
        RTS
sub:    LDA #5
        STA o_k
        RTS
src:    .byte 7
src2:   .byte 1
w:      .byte lo(tgt), hi(tgt)
tgt:    .byte 0, 42
o_copy: .byte 0
o_inx:  .byte 0
o_asl:  .byte 0
o_and:  .byte 0
o_inc:  .byte 0
o_dec:  .byte 0
o_k:    .byte 0
o_via:  .byte 0
o_lo:   .byte 0
o_hi:   .byte 0
o_io:   .byte 0
"""


def _run(src, a=0):
    img, labels = W.assemble(src)
    return img, labels, postinit.Run(img, [W.IO]).call(labels["init"], a)


def test_tags_follow_the_values():
    """A byte copied through the stack keeps its load's tag; arithmetic other than an addition
    chain, decimal mode and a device read are opaque; an immediate is a constant; a byte read
    through a pointer built from a word is named by that word; and the run leaves the memory
    the reference does."""
    img, lab, r = _run(TAGS)
    t = r.tags
    w = (None, lab["w"])
    assert t[lab["o_copy"]] == ("L", (None, lab["src"])) and t[lab["o_k"]] is None
    assert all(t[lab[n]] == "X" for n in ("o_inx", "o_asl", "o_and", "o_inc", "o_dec", "o_io"))
    assert t[lab["o_via"]] == ("L", (w, 1))
    ref = bytearray(img)
    wholeprog.reference_call(ref, lab["init"], volatile=[W.IO])
    assert all(r.mem[a] == ref[a] for a in range(0x200, 0x10000))


def test_init_that_never_returns_leaves_nothing():
    """An init that does not return within its budget has no tagged run and no state."""
    img, lab = W.assemble("init:   JMP init\n")
    with pytest.raises(RuntimeError, match="did not return"):
        postinit.Run(img, [W.IO]).call(lab["init"], max_steps=1000)
    with pytest.raises(RuntimeError, match="init at"):
        postinit.run(img, lab["init"], [0], [W.IO])


def test_runs_skip_the_values_init_never_returns_with():
    """With ``skip``, the runs are those of the values of ``A`` init returns with: below 2
    here, the byte it stores the same on each."""
    img, lab = W.assemble("init:   CMP #$02\nhang:   BCS hang\n        STA $FB\n        RTS\n")
    st = postinit.run(img, lab["init"], range(4), [W.IO], skip=True)
    assert st.songs == [0, 1] and st.free.tolist() == [0xFB] and not st.same().size
    with pytest.raises(RuntimeError, match="init at"):
        postinit.run(img, lab["init"], range(4), [W.IO])


SONGS = """
init:   TAX
        LDA plo,X
        STA $FB
        LDA phi,X
        STA $FC
        LDY #$07
copy:   LDA ($FB),Y
        STA buf,Y
        DEY
        BPL copy
        STX song
        LDA #$0F
        STA $C018
        CPX #$01
        BNE done
        STA $C017
done:   RTS
plo:    .byte lo(s0), lo(s1), lo(s2)
phi:    .byte hi(s0), hi(s1), hi(s2)
s0:     .byte 1, 2, 3, 4, 5, 6, 7, 8
s1:     .byte 9, 10, 11, 12, 13, 14, 15, 16
s2:     .byte 1, 2, 3, 4, 5, 6, 7, 99
song:   .byte 0
buf:    .byte 0, 0, 0, 0, 0, 0, 0, 0
"""


def test_runs_leave_each_songs_state():
    """Each song's run leaves its state (a block copied from the song's data, the song
    number, a pointer) over the bytes init stores outside the stack page and I/O, and the
    volatile writes are each song's, in order."""
    img, lab = W.assemble(SONGS)
    st = postinit.run(img, lab["init"], range(3), [W.IO])
    free = set(st.free.tolist())
    assert free == {0xFB, 0xFC, lab["song"], *range(lab["buf"], lab["buf"] + 8)}
    assert st.writes[0] == [(0xC018, 0x0F)] and st.writes[1] == [(0xC018, 0x0F), (0xC017, 0x0F)]
    assert st.same().tolist() == [0xFC]
    byk = [(k, img[lab[f"s{k}"] : lab[f"s{k}"] + 8]) for k in range(3)]
    assert all((st.states[k][lab["buf"] : lab["buf"] + 8] == want).all() for k, want in byk)
    assert [st.states[k][lab["song"]] for k in range(3)] == [0, 1, 2]


def test_runs_give_the_reads_with_the_writes():
    """The reference machine gives init's volatile reads and writes in order; a machine giving
    ``(addr, value)`` pairs gives writes only."""
    src = "init:   LDA $C001\n        STA $C000\n        RTS\n"
    img, lab = W.assemble(src)
    img[0xC001] = 7
    st = postinit.run(img, lab["init"], [0], [W.IO])
    assert st.io == [[(0xC001, 7, False), (0xC000, 7, True)]] and st.writes == [[(0xC000, 7)]]

    def pairs(mem, pc, a):
        return [(0xC000, 7)], postinit.reference([W.IO])(mem, pc, a)[1]

    st = postinit.run(img, lab["init"], [0], [W.IO], machine=pairs)
    assert st.io == [[(0xC000, 7, True)]] and st.writes == [[(0xC000, 7)]]


#: A pointer built from a word plus 2 by a carry chain, a byte pushed and pulled back, the
#: stack pointer stored, a byte less one.
CHAIN = """
init:   LDA w
        CLC
        ADC #$02
        STA $FB
        LDA w+1
        ADC #$00
        STA $FC
        LDY #$00
        LDA ($FB),Y
        STA o_ptr
        LDA src
        PHA
        LDA #$05
        PLA
        STA o_pla
        TSX
        STX o_tsx
        LDA src
        SEC
        SBC #$01
        STA o_sbc
        RTS
w:      .word tgt
tgt:    .byte 1, 2, 3, 4
src:    .byte 9
o_ptr:  .byte 0
o_pla:  .byte 0
o_tsx:  .byte 0
o_sbc:  .byte 0
"""


def test_carry_chains_name_the_word():
    """A pair set from a word plus a constant by a carry chain is that word's low and high
    byte of one chain, and a read through it is named by the word and the offset; a byte
    pulled back keeps the tag it was pushed with, the stack pointer is a constant, and a
    subtraction is a chain's low byte of its operand."""
    _, lab, r = _run(CHAIN)
    w, src = (None, lab["w"]), (None, lab["src"])
    lo, hi = r.tags[0xFB], r.tags[0xFC]
    assert lo[:3] == ("W", (w,), 0) and hi[:3] == ("W", w, 1) and lo[3] == hi[3]
    assert r.tags[lab["o_ptr"]] == ("L", (w, 2))
    assert r.tags[lab["o_pla"]] == ("L", src) and r.tags[lab["o_tsx"]] is None
    assert r.tags[lab["o_sbc"]][:3] == ("W", (src,), 0)
