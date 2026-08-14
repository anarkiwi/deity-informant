"""The canonical idiom suite, asserted: one denotation per catalog row, invariant.

Each variant's named cell must denote its row's normal form, and every spelling of one
shape must denote the same thing -- the second property is the one that matters. A
known-broken shape is ``xfail(strict=True)`` whose reason names its mechanism.
"""

import pytest

import _idiomgen as G
from deity_informant import idioms

XFAIL = dict(strict=True)

_PIN = "idiom suite: "

M_SMC = (
    "smc-operand: de-SMC moves the operand pair out of the instruction stream and fuses "
    "it (docs/frameprog.md 2.1), but the declarations are carved before it, so "
    "datadecl._read_pcs skips a patched indexed read and no `via` anchor may sit on a "
    "code cell -- the reload tables take no lo/hi roles and the deref stays unlifted"
)
M_UNOBS_CARRY = (
    "unobserved carry: a 16-bit step whose carry arm the frame window never takes "
    "lifts as a lane insert with a guarded arm, so the same step denotes two things"
)
M_LANE_ORDER = (
    "lane-pack claims nothing (docs/denotation.md §4): the transient between the two "
    "lane stores is a value a deref may reach, and the ordering proof is not a lattice fact"
)
M_LANE_EXTRACT = (
    "no lane-extraction rule: a word's high byte is an operator no constructor covers, "
    "so the read is ⊤ unvocabularised"
)
M_LANE_NARROW = (
    "a narrowing COPY keeps the word's own denotation, so a pointer's low lane types "
    "as the pointer rather than as a byte of the pair's lo column"
)
M_SELF_VIA_LOCAL = (
    "the quantity reaches its own update through a local web, and _self_read matches "
    "only a direct read of the cell, so bit-field never fires: the shift reads the "
    "cell's own ⊤ back and the operator takes the blame for it"
)
M_MEM_SHIFT = (
    "a memory-RMW shift fires bit-field on the quantity reaching the top of the shift, "
    "so the cell is flags where the same shift through the accumulator keeps byte"
)
M_UNROLLED = "an unrolled table read declares no table, so each row is a bare cell"
M_ZP_ROW = (
    "no zp-row transfer rule: the address is zext(b ± k), which addr_split does not "
    "name, so the row the catalog says is table[i] is ⊤/addr"
)
M_READ_ONLY_CELL = (
    "a cell no play store writes gets no unknown, so staged-init and cell-read have "
    "nothing to fire on and every read of it is ⊤/cell"
)
M_STAGED_PTR = (
    "the same for a pointer: a word init stages and play never stores has no unknown, "
    "so _ev_mem's deref reads a cell nothing states, the byte it yields is ⊤/cell and "
    "the cell that keeps it is ⊤/mixed"
)
M_INDEX_LOCAL = (
    "an index read into a local before its use puts the index role on the local web, "
    "so the cell keeps only its step and demotes to acc"
)
M_CARRY_FOLDED = (
    "the carry a capture reads is folded into the add that set it, so the cell takes "
    "the fold's own operator and carry-value yields ⊤/op or the bit-field it landed in"
)
M_PRED_BRANCH = (
    "a flag spelled as control flow is not a pred: the arms' constants join across "
    "constructors and the cell is ⊤/mixed"
)

MECHANISMS = (
    M_SMC,
    M_UNOBS_CARRY,
    M_LANE_ORDER,
    M_LANE_EXTRACT,
    M_LANE_NARROW,
    M_SELF_VIA_LOCAL,
    M_MEM_SHIFT,
    M_UNROLLED,
    M_ZP_ROW,
    M_READ_ONLY_CELL,
    M_STAGED_PTR,
    M_INDEX_LOCAL,
    M_CARRY_FOLDED,
    M_PRED_BRANCH,
)

PINS = {
    ("pair-row", "TAX/lo/smc"): M_SMC,
    ("pair-row", "TAX/hi/smc"): M_SMC,
    ("word-pack", "via-cell/smc"): M_SMC,
    ("word-pack", "via-stack/smc"): M_SMC,
    ("lane-insert", "step-adc/indy"): M_UNOBS_CARRY,
    ("lane-insert", "step-adc/smc+cross"): M_SMC,
    ("lane-insert", "step-inc/smc+cross"): M_SMC,
    ("lane-insert", "step-pair/smc"): M_SMC,
    ("lane-insert", "lo-patch/indy"): M_LANE_ORDER,
    ("hi-byte", "hi/lda"): M_LANE_EXTRACT,
    ("hi-byte", "hi/ldx"): M_LANE_EXTRACT,
    ("hi-byte", "hi/ldy"): M_LANE_EXTRACT,
    ("hi-byte", "hi/lax"): M_LANE_EXTRACT,
    ("hi-byte", "hi/tay"): M_LANE_EXTRACT,
    ("hi-byte", "hi/pha"): M_LANE_EXTRACT,
    ("lo-byte", "lo/lda"): M_LANE_NARROW,
    ("lo-byte", "lo/ldx"): M_LANE_NARROW,
    ("lo-byte", "lo/ldy"): M_LANE_NARROW,
    ("lo-byte", "lo/lax"): M_LANE_NARROW,
    ("lo-byte", "lo/tay"): M_LANE_NARROW,
    ("lo-byte", "lo/pha"): M_LANE_NARROW,
    ("shift-chain", "lsr4-mem"): M_MEM_SHIFT,
    ("shift-chain", "asl2-mem"): M_MEM_SHIFT,
    ("flag-bit", "asl-adc"): M_SELF_VIA_LOCAL,
    ("flag-bit", "slo"): M_SELF_VIA_LOCAL,
    ("table-row", "unrolled/X"): M_UNROLLED,
    ("zp-row", "zpx"): M_ZP_ROW,
    ("zp-row", "zpx-tax"): M_ZP_ROW,
    ("zp-row", "zpy-ldx"): M_ZP_ROW,
    ("zp-row", "zpy-lax"): M_ZP_ROW,
    ("zp-row", "zpx-stride2"): M_ZP_ROW,
    ("cell-read", "zp-staged"): M_READ_ONLY_CELL,
    ("deref-row", "reload/smc"): M_SMC,
    ("deref-row", "staged/indy"): M_STAGED_PTR,
    ("deref-row", "staged/indx"): M_STAGED_PTR,
    ("deref-row", "staged/lax"): M_STAGED_PTR,
    ("deref-row", "staged/smc"): M_SMC,
    ("mask-const", "zp"): M_READ_ONLY_CELL,
    ("set-const", "zp"): M_READ_ONLY_CELL,
    ("alu-op", "zp"): M_READ_ONLY_CELL,
    ("widen", "two-columns"): M_INDEX_LOCAL,
    ("carry-value", "adc-capture"): M_CARRY_FOLDED,
    ("carry-value", "rol-acc"): M_CARRY_FOLDED,
    ("carry-value", "rol-mem"): M_CARRY_FOLDED,
    ("carry-value", "sbc-capture"): M_CARRY_FOLDED,
    ("carry-value", "branch"): M_PRED_BRANCH,
    ("compare-value", "cmp-branch"): M_PRED_BRANCH,
}

SPLITS = {
    "pair-row": (M_SMC,),
    "word-pack": (M_SMC,),
    "lane-insert": (M_SMC, M_UNOBS_CARRY, M_LANE_ORDER),
    "shift-chain": (M_MEM_SHIFT,),
    "flag-bit": (M_SELF_VIA_LOCAL,),
    "table-row": (M_UNROLLED,),
    "cell-read": (M_READ_ONLY_CELL,),
    "deref-row": (M_SMC, M_STAGED_PTR),
    "mask-const": (M_READ_ONLY_CELL,),
    "set-const": (M_READ_ONLY_CELL,),
    "alu-op": (M_READ_ONLY_CELL,),
    "widen": (M_INDEX_LOCAL,),
    "carry-value": (M_CARRY_FOLDED, M_PRED_BRANCH),
    "compare-value": (M_PRED_BRANCH,),
}


def _variants():
    """Every (row, variant), pinned where a named mechanism is known to break it."""
    out = []
    for row, label in G.ALL:
        got = PINS.get((row, label))
        marks = [pytest.mark.xfail(reason=_PIN + got, **XFAIL)] if got else []
        out.append(pytest.param(row, label, marks=marks, id="%s:%s" % (row, label)))
    return out


def _shapes():
    """Every row, pinned where its spellings are known to disagree."""
    out = []
    for s in G.SHAPES:
        got = SPLITS.get(s.row)
        marks = [pytest.mark.xfail(reason=_PIN + " | ".join(got), **XFAIL)] if got else []
        out.append(pytest.param(s.row, marks=marks, id=s.row))
    return out


@pytest.mark.parametrize("row,label", _variants())
def test_variant_denotes_the_rows_normal_form(row, label):
    """One spelling of one row: the named cell carries the form the catalog states."""
    assert G.denotation(row, label) == G.BY_ROW[row].want


@pytest.mark.parametrize("row", _shapes())
def test_shape_denotes_one_thing_under_every_spelling(row):
    """The property the suite exists for: meaning, not spelling, fixes the denotation."""
    got = {v.label: G.denotation(row, v.label) for v in G.BY_ROW[row].variants}
    assert len(set(got.values())) == 1, sorted(set(got.values()))


def test_the_shapes_are_the_catalog_rows_in_match_order():
    """Coverage is the catalog's own list: every row, in the order it is matched in."""
    assert [(s.row, s.doc) for s in G.SHAPES] == [(r.id, r.form) for r in idioms.ROWS]


def test_every_shape_carries_more_than_one_spelling():
    """A shape with one variant proves nothing about invariance, so none may have one."""
    assert not [s.row for s in G.SHAPES if len(s.variants) < 2]
    assert len(G.ALL) == len(set(G.ALL))


def test_the_suite_is_generated_not_sampled():
    """No randomness anywhere: one variant assembles to one image, every build."""
    for row, label in G.ALL[::17]:
        assert G.image(row, label)[0] == G.image(row, label)[0]
    assert all(len(s.variants) <= G.CAP for s in G.SHAPES)


def test_every_pin_names_a_live_mechanism():
    """The deferral discipline: a pin states what is broken, so its fix flips it."""
    named = set()
    for got in PINS.values():
        assert got in MECHANISMS
        named.add(got)
    for got in SPLITS.values():
        assert all(m in MECHANISMS for m in got)
        named.update(got)
    assert named == set(MECHANISMS), "a mechanism no pin names is dead text"
    assert set(PINS) <= set(G.ALL), "a pin naming a variant the generator does not carry"
    assert set(SPLITS) <= {s.row for s in G.SHAPES}
