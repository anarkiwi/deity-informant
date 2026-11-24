"""Reaching definitions (``docs/wholeprog.md``, *Pointer definitions*): the definitions of
``A``, ``X`` and ``Y`` as values reach every instruction."""

import test_cgen as T
from deity_informant import cgen

pytestmark = T.pytestmark

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


def lifted(src):
    img, labels, loaded = T.assemble(src)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    p, _ = cgen.lift(img, ents, [T.W.IO], loaded=loaded, init=0)
    return p, labels


def test_register_values_reach_each_instruction():
    p, labels = lifted(VALUES)
    defs = p.pdefs[0][0]
    by = {(defs[d][0], defs[d][3]) for d in p.pvalues[labels["st"]] if defs[d][1] == 0xFFFFFFFF}
    assert ("x", labels["tx"]) in by
    assert ("a", labels["ld"]) in by
    assert all(defs[d][1] == 0xFFFFFFFF for d in p.pvalues[labels["tx"]])
