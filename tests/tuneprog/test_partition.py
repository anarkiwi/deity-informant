"""S6 region typing by accessor-shape partition, and its mirror, the merge."""

from deity_informant.tuneprog import partition, pipeline, printer
from deity_informant.tuneprog.ir import Bin, Const, Rgn, Var

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
    doc = _fused()
    const = doc.split("## const")[1].split("```")[1]
    assert "ctrl             $%04X 1 bytes" % FUSED.labels["ofs"] in const


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
    assert "acc -= $40" in body and "wD400 = " not in body  # one class, not io beside ram


def test_the_present_pass_is_stable_over_two_runs():
    _T, prog = tuneprog(FUSED, calls=12, s4=True, data=FUSED_DATA)
    one = printer.render(*pipeline.present(prog), pcs=False)
    assert one == printer.render(*pipeline.present(prog), pcs=False)
