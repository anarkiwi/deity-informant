"""deity_informant.postinit: init's run with each byte's provenance, and the pointer variables
it leaves."""

import pytest

import wholeprog_progs as W
from test_cgen import WORDREL
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
    return img, labels, postinit.run(img, labels["init"], a, [W.IO])


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
    ps = postinit.pointers(r)
    tgt = lab["tgt"]
    assert ps[0xFD] == (w, tgt) and ps[lab["o_lo"]] == (w, tgt - 1)
    ref = bytearray(img)
    wholeprog.reference_call(ref, lab["init"], volatile=[W.IO])
    assert all(r.mem[a] == ref[a] for a in range(0x200, 0x10000))


def test_pairs_set_from_one_word_group():
    """The relocator's operand pairs set from one header word are one group, at the offset
    its table gives; the pair from another word stands alone."""
    _, lab, r = _run(WORDREL)
    o0, o1, o2 = (lab[n] + 1 for n in ("o0", "o1", "o2"))
    ps = postinit.pointers(r)
    assert ps[o0][0] == ps[o1][0] == (None, lab["hdr"]) and ps[o2][0] == (None, lab["hdr"] + 2)
    assert postinit.groups([r, r], [o0, o1, o2]) == {o1: (o0, 1)}


def test_group_needs_one_offset_in_every_run():
    """A pair whose offset from its group's differs between runs is no alias."""
    _, lab, r = _run(WORDREL)
    _, _, s = _run(WORDREL.replace("offs:   .byte 0, 0, 1", "offs:   .byte 0, 0, 2"))
    o0, o1 = lab["o0"] + 1, lab["o1"] + 1
    assert not postinit.groups([r, s], [o0, o1])


def test_init_that_never_returns_leaves_nothing():
    """An init that does not return on the reference within its budget has no run."""
    img, lab = W.assemble("init:   JMP init\n")
    assert postinit.run(img, lab["init"]) is None
