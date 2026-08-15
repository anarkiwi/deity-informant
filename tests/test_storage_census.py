"""Hermetic tests for the Phase 0 instruments (docs/register-model-lift-impl.md).

Synthesized players exercise ``tools/storage_census.py`` and the extensions to
``tools/fuse_measure.py`` (wide-store shapes) and ``tools/lift_residue.py``
(``sid_readback``, dyn-control counts) without touching HVSC."""

import sys
from functools import lru_cache
from pathlib import Path

import pytest

import _fuzzgen as G
from test_frameprog import _fuzz_model
from deity_informant import frameprog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fuse_measure
import lift_residue
import storage_census

SID = G.SID
TMP = G.CNT + 0x20  # a RAM cell used only as per-frame scratch
CTR = G.CNT + 0x22  # a RAM counter carried across frames
PAT = G.TBL + 0x100  # sequence data block the zero-page pointer walks
BLK = G.TBL + 0x180  # RAM block a pointer stores through
FRAMES = 8


def _scratch():
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x11).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    a.i("LDA", "abs", TMP).i("ORA", "imm", 0x40).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}


def _lone_lane():
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x05).i("STA", "abs", CTR)
    a.i("STA", "abs", SID + 1)
    a.i("LDA", "imm", 0x21).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}


def _pointer_walk():
    a = G.Asm(G.ORG)
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x03).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("LDY", "imm", 0x01).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("RTS")
    data = {G.PTR: PAT & 0xFF, G.PTR + 1: PAT >> 8}
    data.update({PAT + k: (0x40 | (k & 0x1F)) for k in range(0x40)})
    return a, data


def _writethrough():
    a = G.Asm(G.ORG)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x41)
    a.i("LDY", "imm", 0x02).i("STA", "indy", G.PTR)
    a.i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("RTS")
    data = {CTR: 0, G.PTR: BLK & 0xFF, G.PTR + 1: BLK >> 8}
    data.update({G.TBL: BLK & 0xFF, G.TBL + 1: (BLK + 8) & 0xFF})
    data.update({G.TBL + 2: BLK >> 8, G.TBL + 3: (BLK + 8) >> 8})
    data.update({BLK + k: 0 for k in range(0x10)})
    return a, data


def _writethrough_open():
    """The same store with a play-written reload table, which rung (f) refuses.

    ``mut`` excludes the row from the const claim, so the pointer's word set is not
    the registry's and the store stays the top-wide access this census counts."""
    a = G.Asm(G.ORG)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x41)
    a.i("LDY", "imm", 0x02).i("STA", "indy", G.PTR)
    a.i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("LDA", "imm", (BLK + 8) & 0xFF).i("STA", "abs", G.TBL + 1)
    a.i("RTS")
    data = {CTR: 0, G.PTR: BLK & 0xFF, G.PTR + 1: BLK >> 8}
    data.update({G.TBL: BLK & 0xFF, G.TBL + 1: (BLK + 8) & 0xFF})
    data.update({G.TBL + 2: BLK >> 8, G.TBL + 3: (BLK + 8) >> 8})
    data.update({BLK + k: 0 for k in range(0x10)})
    return a, data


def _recurrent():
    """2b (b3) --close: the whole state is one toggling cell, so frame 2 repeats frame 0."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("ORA", "imm", 0x40).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}


_PLAYERS = {
    "scratch": _scratch,
    "recurrent": _recurrent,
    "lone_lane": _lone_lane,
    "pointer_walk": _pointer_walk,
    "writethrough": _writethrough,
    "writethrough_open": _writethrough_open,
}


@lru_cache(maxsize=None)
def _built(name):
    a, data = _PLAYERS[name]()
    player = G.Player(name, G.ORG, a.assemble(), {SID + 4}, {"census"}, data=data, frames=FRAMES)
    model = _fuzz_model(player)
    return model, frameprog.program(model)


def _row(name):
    model, prog = _built(name)
    return storage_census.census(model, prog, FRAMES)


def test_scratch_cell_classes_and_verdicts():
    """The scratch cell is no longer declared: the artifact demotes what nothing reads."""
    row = _row("scratch")
    assert row["frames"] == FRAMES
    assert "m_%04X" % TMP not in row["decl_verdict"]
    assert row["decl_verdict"]["ctr_%04X" % CTR] == "persistent"
    assert row["field_verdicts"]["persistent"] >= 1
    assert row["scratch"] == 0 and row["persistent"] == 1


def test_scratch_has_no_top_sites_and_no_readback():
    row = _row("scratch")
    assert row["top_loads"] == row["top_stores"] == 0
    assert row["readback_sites"] == 0 and row["readback"] == []
    assert row["dyn_stmts"] == row["switch_gotos"] == 0


def test_write_echo_is_netted_out():
    """A byte store to a byte register owes zero net reads: echo-free counting."""
    row = _row("scratch")
    assert row["sid_gross_reads"] == FRAMES
    assert row["sid_net_reads"] == 0 and row["sid_net_regs"] == 0


def test_lone_lane_readback_is_counted_net_and_static():
    """The census counts a read-back of a write-only sink, and there is none to count.

    Rung (d)'s widening guard is what took it to zero: a lone half inside
    $D400-$D416 stays a byte store, so no word is completed around it."""
    row = _row("lone_lane")
    assert row["readback_sites"] == 0
    assert row["sid_net_reads"] == 0
    _model, prog = _built("lone_lane")
    sites, _edges = lift_residue.census(prog)
    assert sum(s["sig"] == "sid_readback" for s in sites) == 0


def test_pointer_walk_top_loads_carry_their_root():
    row = _row("pointer_walk")
    assert row["top_loads"] >= 1 and row["top_stores"] == 0
    assert row["ptr_roots"] == ["$%04X" % G.PTR]


def test_writethrough_top_store_is_classified():
    """The store roots at the pointer it writes through, not at the table behind it.

    A destination is an address, so it keeps the pointer word (``_fold_stmt``); where
    rung (f) proves the word set the store leaves the census, and where the table is
    play-written it stays, rooted at the pair."""
    assert _row("writethrough")["top_stores"] == 0, "rung (f) proved the const table's set"
    row = _row("writethrough_open")
    assert row["top_stores"] == 1
    assert row["wide_classes"] == {"ptr_writethrough": 1}
    (site,) = row["top_store_sites"]
    assert site["roots"] == ["$%04X" % G.PTR]
    assert row["ptr_roots"] == ["$%04X" % G.PTR]


def test_writethrough_aliased_cell_stays_consistent_with_dump():
    _model, prog = _built("writethrough")
    text = frameprog.dumps(prog)
    decl = storage_census.rendered_fields(text)
    assert all(text.count(" %s:" % n) for n in decl)


def test_wide_class_names_the_three_shapes():
    zext_y = ("op", "INT_ZEXT", (("loc", "y"),), 2)
    g2 = ("op", "INT_ADD", (zext_y, ("const", 0x00A3, 2)), 2)
    assert fuse_measure.wide_class(g2) == "g2_boundable"
    far = ("op", "INT_ADD", (zext_y, ("const", 0x0101, 2)), 2)
    assert fuse_measure.wide_class(far) == "other"
    root = ("mem", ("const", 0x0021, 2), 2)
    walk = ("op", "INT_ADD", (root, zext_y), 2)
    assert fuse_measure.wide_class(walk) == "ptr_writethrough"
    assert fuse_measure.wide_class(("loc", "t0", 2)) == "loc_unresolved"
    assert fuse_measure.wide_class(
        ("op", "INT_ADD", (("loc", "t1", 2), ("const", 0x19, 2)), 2)
    ) == ("other")
    assert fuse_measure.root_cells(walk) == [0x0021]


def test_dyn_counts_pairs_and_walls():
    tgt = ("loc", "t0", 2)
    proc = lambda stmts: [(0x1000, (), (), stmts)]
    assert lift_residue.dyn_counts(proc([("dgoto", tgt), ("swg", [])])) == (0, 1)
    assert lift_residue.dyn_counts(proc([("igoto", 0x0314, tgt), ("swg", [])])) == (0, 1)
    assert lift_residue.dyn_counts(proc([("dgoto", tgt)])) == (1, 0)
    assert lift_residue.dyn_counts(proc([("igoto", 0x0314, None)])) == (0, 0)
    assert lift_residue.dyn_counts(proc([("igoto", 0x0314, tgt)])) == (1, 0)
    assert lift_residue.dyn_counts(proc([("dbr", "if", tgt, tgt, 0)])) == (1, 0)
    assert lift_residue.dyn_counts(proc([("dcall", tgt, 0x1234), ("swc", [], [])])) == (0, 0)
    assert lift_residue.dyn_counts(proc([("swc", [], [])])) == (1, 0)


def test_rendered_fields_drops_unaddressed_names():
    text = "\n".join(
        (
            "frameprog 1",
            "state {",
            " m_1234: u8",
            " ptr_00FB: u16",
            " cflag: u8",
            " m_5678: u8[]",
            " wave: u8 observed $01",
            "}",
        )
    )
    got = storage_census.rendered_fields(text)
    assert got == {"m_1234": (0x1234, 1), "ptr_00FB": (0x00FB, 2), "m_5678": (0x5678, 1)}


def test_field_cells_reads_the_canonical_name():
    assert list(storage_census.field_cells("ptr_00FB_hi", 1)) == [0xFC]
    assert list(storage_census.field_cells("m_1234", 2)) == [0x1234, 0x1235]
    assert storage_census.field_cells("cflag", 1) is None


def test_field_verdict_prefers_persistence_and_spells_mixes():
    cls = {10: "persistent", 11: "framelocal", 12: "data"}
    assert storage_census.field_verdict(cls, [10, 11]) == "persistent"
    assert storage_census.field_verdict(cls, [11, 12]) == "framelocal"
    assert storage_census.field_verdict(cls, [12, 13]) == "data/untouched"
    assert storage_census.field_verdict(cls, [13]) == "untouched"


def test_totals_merge_the_gate_columns():
    rows = [_row("scratch"), _row("writethrough_open")]
    for row in rows:
        row["tune"] = row.get("tune", "t/%d" % id(row))
    got = storage_census._totals(rows)
    assert got["scratch_total"] == sum(r["scratch"] for r in rows)
    assert got["wide_class_total"] == {"ptr_writethrough": 1}
    assert got["eval_faults"] == []


def test_census_matches_the_dynamic_oracle_of_the_evaluator():
    """The image sees every state cell the program declares as touched or not.

    Rung (d1) took the scratch cell out of the address space (9.1), so the oracle
    records no traffic at it at all: it is a wire, and the persistent cell is what
    the image still carries across the frame boundary."""
    model, prog = _built("scratch")
    image, ran, fault = storage_census.evaluate(model, prog, FRAMES)
    assert (ran, fault) == (FRAMES, None)
    cls = storage_census.cell_classes(image)  # the recorders are defaultdicts: read first
    assert not image.writes.get(TMP) and not image.reads.get(TMP)
    assert not image.first.get(TMP)
    assert image.writes[CTR] == FRAMES and image.reads[CTR] == FRAMES
    assert TMP not in cls and cls[CTR] == "persistent"
    assert not any(SID <= a <= SID + 0x1C for a in cls)


def test_a_recurring_run_closes_and_a_counting_one_does_not():
    """2b (b3) ``--close``: recurrence is what lets a finite run stand for the infinite one.

    The toggle's state image repeats at a frame boundary and no input drives it, so the
    observed extent is the whole extent; the counter never repeats inside the horizon."""
    model, prog = _built("recurrent")
    image, ran, fault = storage_census.evaluate(model, prog, FRAMES, close=True)
    assert image.recurred == 0 and not image.driven
    assert storage_census.closed_run(image, ran, fault)
    model, prog = _built("scratch")
    image, ran, fault = storage_census.evaluate(model, prog, FRAMES, close=True)
    assert image.recurred is None and not storage_census.closed_run(image, ran, fault)


def test_closure_is_off_unless_it_is_asked_for():
    """Invariant: the recurrence test costs a hash per frame, so it is opt in."""
    model, prog = _built("recurrent")
    image, ran, fault = storage_census.evaluate(model, prog, FRAMES)
    assert image.recurred is None and not storage_census.closed_run(image, ran, fault)


@pytest.mark.parametrize("name", sorted(_PLAYERS))
def test_players_build_and_their_rows_are_json_shaped(name):
    row = _row(name)
    assert row["state_fields"] >= row["persistent"]
    assert isinstance(row["by_class_region"], dict)
