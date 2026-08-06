"""The rung-(d2) refusals that remain, each as the 6502 shape that causes it.

Every case here is a synthetic player carrying one real driver's shape, pinned to
the diagnostic the pass gives it today. A gap that closes flips its case here in
under a second, which is what the corpus sweep is too slow to tell you.
"""

import pytest

from deity_informant import framemath
from deity_informant import frameprog
from deity_informant import frameval
import _fuzzgen as G

from test_frameprog import _fuzz_model

LO, HI = 0x10, 0x11
OUT = 0xD404
ROW = G.CNT + 0x20  # RAM lanes an indexed store can reach, clear of the counters


def _run(name, asm, data=(), outs=(OUT, OUT + 1)):
    """``(math proofs, text)`` for a player; Gate FP must hold whatever it refuses."""
    player = G.Player(name, G.ORG, asm.assemble(), set(outs), {"indexed"}, data=dict(data))
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None, "a refusal must still gate"
    return [p for p in prog.proofs if p.kind == "math"], frameprog.dumps(prog)


def _why(proofs, lo=None):
    """The refusal reasons, optionally for the site with lane ``lo``."""
    return sorted(
        p.lemma.rsplit(";", 1)[-1].strip()
        for p in proofs
        if p.status == "refused" and (lo is None or p.targets[0] == lo)
    )


# ---- a carry link is not two lanes ------------------------------------------------
def test_a_packed_term_naming_one_cell_twice_is_not_a_lane_pair():
    """Boo lifted `lanes $122E/$122E` until this held: one byte is not two lanes.

    Pinned on ``_lanes`` directly rather than through a driver, because the shape
    reaches it only when extraction happens to offer it -- which is precisely the
    seed-dependence that made it escape the corpus gate."""
    lo, hi = ("cell", 0x122E, 1, 0), ("cell", 0x12B4, 1, 0)
    assert framemath._lanes(framemath._split(hi, lo)) == (hi, lo)
    assert framemath._lanes(framemath._split(lo, lo)) is None
    load = ("load", ("add", ("zext", ("loc", "x")), ("num", 0x122E, 2), 2), 1, 0)
    assert framemath._lanes(framemath._split(load, load)) is None


# ---- the lo lane is defined above the branch --------------------------------------
def test_a_lo_lane_loaded_in_the_dominating_block_refuses():
    """Are_Friends_Electric $13DC: the lo lane's load sits above the branch.

    Inside the interval it is a free local, so the e-graph query cannot cancel the
    pack and only a mixed-index regrouping survives. Wants a cross-block
    definition query, not an aliasing input."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0).i("LDY", "imm", 0)
    a.i("LDA", "absx", ROW)  # the lo lane, loaded above the branch
    a.i("CMP", "imm", 0x80).i("BCS", "rel", ("L", "skip"))
    a.i("CLC").i("ADC", "absy", G.TBL)
    a.i("STA", "absx", ROW)
    a.i("LDA", "absx", ROW + 2).i("ADC", "imm", 0).i("STA", "absx", ROW + 2)
    a.label("skip")
    a.i("LDA", "absx", ROW).i("STA", "abs", OUT)
    a.i("LDA", "absx", ROW + 2).i("STA", "abs", OUT + 1).i("RTS")
    data = {G.TBL: 0x37, ROW: 0x70, ROW + 2: 0x01}
    proofs, _text = _run("above_branch", a, data)
    assert proofs, "the site must be found for the refusal to be the one measured"
    assert not [p for p in proofs if p.status == "lifted"]


# ---- a zero-page indexed store may wrap onto the hi lane --------------------------
def test_a_zero_page_indexed_store_bounded_only_by_a_counter_refuses():
    """Wizball $4981: `STA $E4,X` wraps over the whole zero page and covers the hi lane.

    The program bounds X by a loop counter; the pass has no value range for one,
    so the alias cannot be excluded."""
    a = G.Asm(G.ORG)
    a.i("LDX", "zp", 0x0D)
    a.i("LDA", "imm", 0x02).i("CLC").i("ADC", "zp", LO).i("STA", "zpx", 0xE4)
    a.i("LDA", "imm", 0x00).i("ADC", "zp", HI).i("STA", "zpx", 0xE9)
    a.i("LDA", "zp", LO).i("STA", "abs", OUT)
    a.i("LDA", "zp", HI).i("STA", "abs", OUT + 1).i("RTS")
    proofs, _text = _run("zp_wrap", a, {0x0D: 0x02, LO: 0xF0, HI: 0x01})
    assert "the lo destination may alias the hi lane" in _why(proofs, LO)


# ---- the step comes off the stack -------------------------------------------------
def test_a_step_pulled_from_the_stack_has_no_nameable_address():
    """Allt_under_himmelens_faeste $0974: the step is a PLA.

    The e-graph folds the pull's address ((sp + $FF) + 1) to sp while to_egg keyed
    provenance by the unfolded spelling, so _back cannot name it; the forms left
    make the stack byte a lane, whose address is no const base plus index."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0)
    a.i("LDA", "absx", G.TBL).i("PHA")
    a.i("PLA").i("CLC").i("ADC", "zp", LO).i("STA", "zp", LO)
    a.i("LDA", "zp", HI).i("ADC", "imm", 0).i("STA", "zp", HI)
    a.i("LDA", "zp", LO).i("STA", "abs", OUT)
    a.i("LDA", "zp", HI).i("STA", "abs", OUT + 1).i("RTS")
    proofs, _text = _run("stack_step", a, {G.TBL: 0x37, LO: 0xF0, HI: 0x01})
    assert proofs, "the site must still be found"


# ---- the widening residue: a definition no scope chain reads ----------------------
def test_a_lane_index_set_in_a_branch_arm_does_not_widen():
    """The largest widening residue: a valueless definition is in force.

    ``Defs`` walks a scope chain and gives up at a join, so an index an ``if`` arm
    sets is unknown at the store and the lane store stays byte-wide."""
    a = G.Asm(G.ORG)
    a.i("LDY", "imm", 0x00)
    a.i("LDA", "abs", G.TBL).i("BEQ", "rel", ("L", "keep"))
    a.i("LDY", "imm", 0x07)
    a.label("keep")
    a.i("LDA", "abs", G.TBL + 1).i("STA", "absy", G.SID).i("RTS")
    outs = tuple(G.SID + k for k in range(0x19))
    _proofs, text = _run("arm_index", a, {G.TBL: 0x01, G.TBL + 1: 0x42}, outs)
    assert "sid.reg00[y] = " in text  # byte-wide residue: the view, never a register name
    assert "sid.v1.freq_lo[y]" not in text


@pytest.mark.parametrize("k,widens", [(0x07, True), (0x01, False)])
def test_a_lane_index_from_a_constant_table_widens_only_when_lane_aligned(k, widens):
    """The rule that does hold: every value the index may take must land on a lo lane."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 1).label("lp")
    a.i("LDY", "absx", G.TBL)
    a.i("LDA", "absx", G.TBL + 4).i("STA", "absy", G.SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {G.TBL: 0x00, G.TBL + 1: k, G.TBL + 4: 0x30, G.TBL + 5: 0x31}
    outs = tuple(G.SID + n for n in range(0x19))
    _proofs, text = _run("tbl_index_%02X" % k, a, data, outs)
    assert ("sid.v1.freq_lo[y]:2" in text) is widens
