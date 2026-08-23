"""S6 region typing by accessor-shape partition, and its mirror, the merge."""

from types import SimpleNamespace

import pytest

from deity_informant.tuneprog import partition, pipeline, printer
from deity_informant.tuneprog.ir import Bin, Const, Rgn, Tuneprog, Var
from deity_informant.tuneprog.pseudocode import Printer
from deity_informant.tuneprog.recover import Names

from _asm import asm
from _prog import PLAY, printed, proc_body, tuneprog

BASE = 0x1000
R = Rgn(id=7, name="state_1000", base=BASE, size=16, kind="state", init=bytes(16), origin=BASE)


def _at(base, idx=True):
    """An address expression: ``base + i``, or the bare constant."""
    return Bin("+", Const(base, 2), Var("i", 1), 2) if idx else Const(base, 2)


# ---- the shapes --------------------------------------------------------------
def test_a_span_starting_at_its_own_operand_names_an_array():
    assert partition._cover(R, BASE + 4, BASE + 6, _at(BASE + 4)) == ("array", 4, 6)


def test_a_constant_address_names_a_scalar():
    assert partition._cover(R, BASE + 3, BASE + 3, _at(BASE + 3, idx=False)) == ("scalar", 3, 3)


def test_a_reach_that_starts_inside_the_region_claims_no_extent():
    assert partition._cover(R, BASE + 2, BASE + 9, _at(BASE)) == ("", 2, 9)
    assert partition._cover(R, BASE + 4, BASE + 4, _at(BASE + 4)) == ("", 4, 4)


def test_an_access_outside_the_region_is_no_cover():
    assert partition._cover(R, BASE - 1, BASE + 2, _at(BASE)) is None
    assert partition._cover(R, BASE, BASE + 99, _at(BASE)) is None


# ---- the partition -----------------------------------------------------------
def test_the_narrow_claim_wins_and_a_scalar_inside_an_array_is_its_element():
    covers = [("array", 0, 15), ("array", 4, 6), ("scalar", 5, 5), ("scalar", 9, 9)]
    assert partition._claims(covers) == [(4, 6), (9, 9)]


def test_equal_claims_are_one_extent():
    assert partition._claims([("array", 4, 6)] * 3 + [("scalar", 8, 8)]) == [(4, 6), (8, 8)]


def test_claims_of_one_width_at_one_spacing_are_a_record_not_a_fusion():
    assert partition._uniform([(0, 2), (3, 5), (6, 8)])
    assert partition._uniform([(0, 0), (1, 1), (2, 2)])
    assert not partition._uniform([(3, 3), (4, 6), (7, 9)])


def test_a_region_no_access_overruns_a_claim_of_is_not_partitioned():
    claims = [(4, 6), (9, 9)]
    inside = [(("array", 4, 6), False, True), (("scalar", 9, 9), False, True)]
    assert not partition._disagree(inside, claims)
    assert partition._disagree(inside + [(("", 0, 15), False, True)], claims)


def test_an_access_wholly_in_the_residue_disagrees_as_much_as_one_that_crosses():
    claims = [(4, 6), (9, 9)]
    assert partition._disagree([(("scalar", 12, 12), False, True)], claims)


# ---- the fold's cells --------------------------------------------------------
def _fold(slots, columns=None):
    """A copy fold shaped as :func:`~.copyview._folds` keys it: by each slot's first cell."""
    return SimpleNamespace(meta={"copyviews": [{"slots": slots, "columns": columns or {}}]})


def test_a_folds_cells_follow_a_merge_and_then_a_split():
    prog = _fold({(3, 0x1000): [(3, 0x1000), (3, 0x1004)]}, {(9, 0): (3, 0x1000)})
    f = prog.meta["copyviews"][0]
    partition._recell(prog, [(3, 0, 0xFFFF, 7)])  # a merge: region 3 is region 7 throughout
    assert f["slots"] == {(7, 0x1000): [(7, 0x1000), (7, 0x1004)]}
    assert f["columns"] == {(9, 0): (7, 0x1000)}
    partition._recell(prog, [(7, 0x1004, 0x1007, 8)])  # a split: the second copy moved out
    assert f["slots"] == {(7, 0x1000): [(7, 0x1000), (8, 0x1004)]}


def test_a_merge_that_would_collapse_two_slots_onto_one_cell_is_refused():
    prog = _fold({(3, 0x1000): [(3, 0x1000)], (4, 0x1000): [(4, 0x1000)]})
    with pytest.raises(ValueError):
        partition._recell(prog, [(4, 0, 0xFFFF, 3)])


def test_the_addresses_one_fold_names_as_one_field_are_a_group_per_region():
    prog = _fold({(3, 0x1000): [(3, 0x1000), (3, 0x1004), (5, 0x2000)]})
    assert partition._fields(prog) == {3: [{0x1000, 0x1004}], 5: [{0x2000}]}


def test_a_claim_that_cuts_a_fold_field_loses_and_the_rest_stands():
    claims = [(1, 1), (4, 6), (9, 9)]
    groups = [{BASE + 1, BASE + 2, BASE + 3}]
    assert partition._uncut(R, claims, groups) == [(4, 6), (9, 9)]
    assert partition._uncut(R, claims, [{BASE + 4, BASE + 5}]) == claims


def test_a_part_no_store_reaches_is_const_beside_a_state_neighbour():
    band = (BASE, BASE + 0x100)
    assert partition._kind(R, 4, 6, [(9, 9)], band) == "const"
    assert partition._kind(R, 4, 6, [(5, 5)], band) == "state"
    assert partition._kind(R, 4, 6, [], (0, 0)) == "image"


# ---- the print ---------------------------------------------------------------
FUSED = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDX #$02",
    "lp: INC v1,X",
    "LDA v1,X",
    "STA $D400",
    "DEX",
    "BPL lp",
    "LDA ofs",
    "STA $D404",
    "LDY cnt",
    "LDA tab,Y",
    "STA $D401",
    "INC cnt",
    "RTS",
    "cnt: BRK",
    "tab: BRK",
    *["BRK"] * 7,
    "ofs: BRK",
    "v1: BRK",
    "BRK",
    "BRK",
)
FUSED_DATA = {FUSED.labels["tab"] + i: i + 1 for i in range(8)}
FUSED_DATA[FUSED.labels["ofs"]] = 5


def _fused():
    return printed(FUSED, calls=12, data=FUSED_DATA)


def test_the_per_voice_array_splits_out_of_the_table_one_read_overran():
    doc = _fused()
    assert "freq_lo          $%04X 3 bytes" % FUSED.labels["v1"] in doc
    body = "\n".join(proc_body(doc, "tick"))
    assert "freq_lo[v] += 1" in body and "freq_lo[v + 9]" not in body


def test_the_overrunning_accessor_keeps_the_fused_extent_it_asserts():
    doc = _fused()
    assert "freq_hi          $%04X 12 bytes" % FUSED.labels["tab"] in doc
    assert "freq_hi[freq_hi_idx]" in "\n".join(proc_body(doc, "tick"))


def test_a_byte_no_store_writes_prints_const_beside_its_state_neighbours():
    data = _fused().split("## data")[1].split("```")[1]
    assert "also ctrl $%04X" % FUSED.labels["ofs"] in data
    row = next(l for l in data.splitlines() if l.startswith("  $%04X  " % FUSED.labels["tab"]))
    assert row.split()[1:] == ["%02X" % (i + 1) for i in range(8)] + ["05"]


def test_the_split_leaves_the_certified_region_ids_where_they_were():
    _T, prog = tuneprog(FUSED, calls=12, s4=True, data=FUSED_DATA)
    before = {r.id: (r.base, r.size, r.kind) for r in prog.storage}
    view, _st, _names = pipeline.present(prog)
    kept = {r.id: (r.base, r.size, r.kind) for r in view.storage if r.id in before}
    assert kept == {k: v for k, v in before.items() if k in kept}
    assert max(before) < min(r.id for r in view.storage if r.id not in before)


# ---- the mirror --------------------------------------------------------------
TWO = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDX cnt",
    "LDY ia,X",
    "LDA tab,Y",
    "STA $D418",
    "LDY ib,X",
    "LDA tab,Y",
    "STA $D417",
    "INC cnt",
    "LDA cnt",
    "CMP #$03",
    "BNE out",
    "LDA #$00",
    "STA cnt",
    "out: RTS",
    "cnt: BRK",
    "ia: BRK",
    "BRK",
    "BRK",
    "ib: BRK",
    "BRK",
    "BRK",
    "tab: BRK",
    *["BRK"] * 7,
)
TWO_DATA = {TWO.labels["tab"] + i: i + 1 for i in range(8)}
TWO_DATA.update(zip((TWO.labels["ia"] + i for i in range(3)), (0, 1, 3)))
TWO_DATA.update(zip((TWO.labels["ib"] + i for i in range(3)), (2, 4, 5)))


def test_a_gap_starts_a_new_run_and_only_the_overlapping_extents_merge():
    def rgn(i, base, size):
        return Rgn(
            id=i,
            name="state_%04X" % base,
            base=base,
            size=size,
            kind="state",
            init=bytes(size),
            origin=BASE,
        )

    prog = SimpleNamespace(
        storage=[rgn(1, BASE, 2), rgn(2, BASE + 1, 2), rgn(3, BASE + 0x10, 2)], procs={}, meta={}
    )
    assert partition._merge(prog, set()) == {2: 1}
    assert [(r.id, r.base, r.size) for r in prog.storage] == [(1, BASE, 3), (3, BASE + 0x10, 2)]


def test_two_extents_of_one_table_at_one_origin_are_one_region():
    doc = printed(TWO, calls=12, data=TWO_DATA)
    assert "mode_vol         $%04X 6 bytes" % TWO.labels["tab"] in doc
    assert "res_route        $" not in doc
    body = "\n".join(proc_body(doc, "tick"))
    assert body.count("mode_vol[") == 2


COLUMNS = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDA cnt",
    "ASL",
    "TAY",
    "LDA tab,Y",
    "STA $D418",
    "INY",
    "LDA tab,Y",
    "STA $D417",
    "INC cnt",
    "LDA cnt",
    "CMP #$05",
    "BNE out",
    "LDA #$00",
    "STA cnt",
    "out: RTS",
    "cnt: BRK",
    "tab: BRK",
    *["BRK"] * 11,
)


def test_the_parallel_columns_of_one_record_are_not_one_extent():
    doc = printed(COLUMNS, calls=12, data={COLUMNS.labels["tab"] + i: i + 1 for i in range(12)})
    assert "rec1[5]  stride 2, 2 fields" in doc


# ---- the word view the split unblocks ----------------------------------------
def _chain(op, fix):
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA lo",
        "STA hi",
        "STA cnt",
        "RTS",
        "play: LDA lo",
        fix,
        "%s step" % op,
        "STA lo",
        "STA $D400",
        "LDA hi",
        "%s #$00" % op,
        "STA hi",
        "STA $D401",
        "INC cnt",
        "RTS",
        "cnt: BRK",
        "step: BRK",
        "lo: BRK",
        "hi: BRK",
    )


def test_a_byte_added_to_a_word_is_one_16_bit_statement():
    code = _chain("ADC", "CLC")
    body = "\n".join(proc_body(printed(code, calls=6, data={code.labels["step"]: 0x40}), "tick"))
    assert "freq += $40" in body and "carry(" not in body


def test_a_byte_subtracted_from_a_word_is_one_16_bit_statement():
    code = _chain("SBC", "SEC")
    body = "\n".join(proc_body(printed(code, calls=6, data={code.labels["step"]: 0x40}), "tick"))
    # one class, not io beside ram; the folded borrow is short enough for the high
    # half to read as the shadow of $D401, so the pair takes the register's name
    assert "freq -= $40" in body and "wD400 = " not in body


def _pairprint(rs, pair, recorded=()):
    """``pair(lo, hi, a)`` over a word named by its two cells.

    ``recorded`` lists the cells a copy fold already names by their own field.
    """
    names = Names(region={r.id: r.name for r in rs}, u16={pair: "freq"})
    names.slots.update({c: [("voice", "f%d" % c[0], 0, False)] for c in recorded})
    return Printer(Tuneprog(storage=rs), names).pair(pair[0], pair[1], Const(pair[0][1], 2))


def _bytes():
    return [
        Rgn(id=i, name=n, base=BASE + i - 1, size=1, kind="state", init=bytes(1), origin=BASE)
        for i, n in ((1, "lo"), (2, "hi"))
    ]


def test_a_word_over_two_one_byte_regions_prints_by_its_name():
    assert _pairprint(_bytes(), ((1, BASE), (2, BASE + 1))) == "freq"


def test_a_word_whose_half_a_record_names_still_prints_by_its_own_name():
    got = _pairprint(_bytes(), ((1, BASE), (2, BASE + 1)), recorded=((1, BASE),))
    assert got == "freq"


def test_two_cells_of_one_region_are_one_word():
    rs = [Rgn(id=1, name="zp", base=BASE, size=16, kind="state", init=bytes(16), origin=BASE)]
    assert _pairprint(rs, ((1, BASE + 4), (1, BASE + 9))) == "freq"


def test_the_present_pass_is_stable_over_two_runs():
    _T, prog = tuneprog(FUSED, calls=12, s4=True, data=FUSED_DATA)
    one = printer.render(*pipeline.present(prog), pcs=False)
    assert one == printer.render(*pipeline.present(prog), pcs=False)
