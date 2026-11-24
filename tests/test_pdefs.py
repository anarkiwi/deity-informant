"""Reaching definitions (``docs/wholeprog.md``, *Pointer definitions*): a call of a procedure
that never writes a location keeps the caller's definitions of it, and the definitions of
``A``, ``X`` and ``Y`` as values reach every instruction."""

import test_cgen as T
from deity_informant import cgen

pytestmark = T.pytestmark

#: Two pointers set from two tables at an index play reads from the SID, each read after a
#: call of `sub`, which never writes them.
CALLS = """
init:   RTS
play:   LDA $D41B
        AND #1
        TAX
        LDA alo,X
s1:     STA $02
        LDA ahi,X
        STA $03
        JSR sub
r1:     LDA ($02),Y
        STA $C000
        LDA blo,X
s2:     STA $02
        LDA bhi,X
        STA $03
        JSR sub
r2:     LDA ($02),Y
        STA $C001
        RTS
sub:    LDY #1
        RTS
alo:    .byte lo(da), lo(da+1)
ahi:    .byte hi(da), hi(da+1)
blo:    .byte lo(db), lo(db+1)
bhi:    .byte hi(db), hi(db+1)
da:     .byte 1, 2, 3
db:     .byte 4, 5, 6
end:
"""

#: Values moved through registers into a cell.
VALUES = """
init:   RTS
play:
ld:     LDA #5
tx:     TAX
st:     STX cell
        RTS
cell:   .byte 0
end:
"""


def lifted(src, songs=1):
    """``(program, labels)`` of init and play of ``src`` under the last-entry rule with
    ``songs`` songs, the bytes past the program none of its own."""
    img, labels, loaded = T.assemble(src)
    img[labels["end"] : labels["end"] + 0x200] = bytes(range(0x80, 0x100)) * 4
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    kw = {"loaded": loaded, "init": 0, "songs": range(songs), "overrun": "last"}
    p, _ = cgen.lift(img, ents, [T.W.IO], **kw)
    return p, labels


def low_defs(p, pc):
    defs, at, _ = p.pdefs[0]
    return {defs[d][3] for d in at[pc] if defs[d][0] == "w" and defs[d][2] == 0}


def test_call_of_a_procedure_not_writing_a_pointer_keeps_its_definitions():
    p, labels = lifted(CALLS)
    assert low_defs(p, labels["r1"]) == {labels["s1"]}
    assert low_defs(p, labels["r2"]) == {labels["s2"]}


def test_register_values_reach_each_instruction():
    p, labels = lifted(VALUES)
    defs = p.pdefs[0][0]
    by = {(defs[d][0], defs[d][3]) for d in p.pvalues[labels["st"]] if defs[d][1] == 0xFFFFFFFF}
    assert ("x", labels["tx"]) in by
    assert ("a", labels["ld"]) in by
    assert all(defs[d][1] == 0xFFFFFFFF for d in p.pvalues[labels["tx"]])
