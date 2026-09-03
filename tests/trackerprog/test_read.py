"""B7's reader on one synthetic tune: its expressions, its guards and its flow facts.

Hermetic: no tune of the archive, and every claim is read off the S4 program in
``_bound.py``.
"""

import pytest

from _bound import (
    C,
    FREQ,
    NOTE,
    PAT,
    SWEEP,
    TIMER,
    V,
    other,
    ram,
    reader,
    tick,
)
from _procs import diamond, dispatch
from deity_informant.trackerprog import read
from deity_informant.tuneprog.ir import Bin, Load, Store


# ---- read.py: the leaves and the arithmetic ------------------------------------
def test_a_left_shift_is_the_adds_the_object_has():
    assert read._shl(4, 2, 1) == 16
    assert read._shl({"cell": "a"}, 0, 1) == {"cell": "a"}
    assert read._shl({"cell": "a"}, 1, 1) == {"and": [{"shl": [{"cell": "a"}, 1]}, 0xFF]}


def test_a_value_is_held_to_the_width_the_machine_gives_it():
    assert read.masked(0x1FF, 1) == 0xFF
    assert read.masked({"cell": "a"}, 2) == {"and": [{"cell": "a"}, 0xFFFF]}
    assert read.masked({"cell": "a"}, 4) == {"and": [{"cell": "a"}, 0xFFFF]}


def test_one_bit_of_a_mask_is_the_bit_and_a_wider_mask_is_the_zero_test():
    assert read._bitof(0x80) == 7 and read._bitof(0x81) is None and read._bitof(0) is None
    e = Bin("!=", Bin("&", V("n"), C(4)), C(0))
    assert read._truth({"and": [{"cell": "tn"}, 4]}, 0, "!=", 1, e) == {"bit": [{"cell": "tn"}, 2]}
    got = read._truth({"and": [{"cell": "tn"}, 4]}, 0, "==", 1, e)
    assert got == {"xor": [{"bit": [{"cell": "tn"}, 2]}, 1]}
    wide = Bin("!=", Bin("&", V("n"), C(6)), C(0))
    assert "carry_out" in read._truth({"cell": "tn"}, 0, "!=", 1, wide)


def test_a_comparison_in_a_value_position_is_the_chips_own_zero_test():
    low, _voc = reader()
    low.lbl = "mach"
    assert low.value(Bin("!=", Bin("&", V("n"), C(4)), C(0))) == {"bit": [{"cell": "tn"}, 2]}
    assert "borrow_out" in low.value(Bin("==", V("n"), C(3)))
    assert low.value(Bin("<", V("n"), C(3)))["carry_out"][1] == 8
    assert low.value(Bin("<=", V("n"), C(3)))["borrow_out"][1] == 8
    assert low.value(Bin("carry", V("n"), C(3)))["carry_out"][1] == 8
    assert low.value(Bin("|", V("n"), C(3))) == {"or": [{"cell": "tn"}, 3]}
    assert low.value(Bin("+", V("n"), C(3))) == {"and": [{"add": [{"cell": "tn"}, 3]}, 0xFF]}
    assert low.value(Bin(">>", V("n"), C(3))) == {"shr": [{"cell": "tn"}, 3]}


def test_a_name_is_the_cell_the_object_gives_it_or_no_name_at_all():
    low, voc = reader()
    low.lbl = "mach"
    low.local = {"z": 7}
    assert low.value(V("z")) == 7 and low.expand(V("z")) == C(7)
    low.local = {}
    voc.subst = {"t0": {"cell": "phase"}}
    assert low.value(V("t0")) == {"cell": "phase"}
    voc.subst = {}
    assert low.value(V("x")) == {"cell": "voice_index"}
    assert low.value(V("n")) == {"cell": "tn"} and low.temps["n"] == "tn"
    low.sub = {repr(V("n")): {"cell": "staged"}}
    assert low.value(V("n")) == {"cell": "staged"}
    low.sub = {}
    with pytest.raises(read.Unlowerable):
        low.value(V("nosuchname"))


def test_a_temp_is_one_cell_a_voice_and_a_scalar_is_the_tunes_own_global():
    low, _voc = reader()
    assert low.temp("n", 2) == "tn" and "tn" in low.wide
    low.scalars = frozenset({"g1"})
    assert low.temp("g1") == "#tg1"
    assert read.Reader.tref("#tg1") == {"global": "tg1"}
    assert read.Reader.tref("tn") == {"cell": "tn"}


def test_a_masked_score_byte_is_the_event_field_only_where_a_payload_stands():
    low, voc = reader()
    low.lbl = "keyon"
    voc.fields = {("c", 0x0F): "dur_fact"}
    assert low.field(V("c"), 0x0F) == "dur_fact"
    assert low.field(V("n"), 0x0F) is None
    assert low.value(Bin("&", V("c"), C(0x0F))) == "dur_fact"
    voc.payload = False
    assert low.value(Bin("&", V("c"), C(0x0F))) == {"and": [{"cell": "tc"}, 0x0F]}


def test_an_operator_the_object_has_no_form_for_is_refused():
    low, _voc = reader()
    low.lbl = "mach"
    with pytest.raises(read.Unlowerable):
        low.value(Bin("<<", V("n"), V("c")))
    with pytest.raises(read.Unlowerable):
        low.value(Bin("%", V("n"), C(3)))
    with pytest.raises(read.Unlowerable):
        low.value(object())
    low.lbl = "fetch"
    with pytest.raises(read.Unlowerable):
        low.value(Load("ram", V("q", 2), 1, PAT, PAT + 31, 13))


# ---- read.py: expansion, the image and the flow facts ---------------------------
def test_a_word_the_play_never_writes_is_the_byte_the_image_states():
    low, _voc = reader()
    assert low.frozen(PAT, 32) and low.frozen(FREQ, 2)
    assert not low.frozen(TIMER, 1) and not low.frozen(SWEEP, 3)
    low.lbl = "mach"
    assert low.expand(Load("ram", C(FREQ + 2, 2), 1, FREQ, FREQ, 1)) == C(0x07)
    assert low.expand(Bin("+", C(2), C(3))) == C(5)


def test_a_read_of_a_cell_one_store_reaches_is_the_value_that_store_left():
    low, _voc = reader()
    low.lbl = "mach"
    assert low.expand(ram(NOTE, 4, V("x"))) == V("n")
    assert low.isvoice(V("x")) and not low.isvoice(V("n"))
    assert low.chase(V("q")).op == "|"


def test_a_store_whose_value_reads_its_own_cell_is_a_counter_and_no_copy():
    low, _voc = reader()
    p = tick()
    clock = [s for s in p.blocks["head"].stmts if type(s) is Store][0]
    keyed = [s for s in p.blocks["keyon"].stmts if type(s) is Store][0]
    assert low.selfread(clock.v, TIMER)
    assert not low.selfread(keyed.v, NOTE)


def test_one_store_of_a_base_reaches_the_blocks_the_edges_lead_to():
    p = tick()
    got = read.reaching(p, list(p.blocks), frozenset({"x"}))
    assert NOTE in got["mach"] and TIMER in got["fetch"]
    assert not got["top"]


def test_two_paths_that_differ_in_one_term_and_its_negation_are_the_one_path():
    c, d = Bin("==", V("a"), C(0)), Bin("==", V("b"), C(0))

    def arm(t):
        return ((("h", c, t),), ())

    assert read.fold([arm(True), arm(False)]) == [((), ())]
    assert len(read.fold([arm(True), ((("h", d, True),), ())])) == 2
    assert read.pair((("h", c, True),), (("h", c, False),))
    assert not read.pair((("h", c, True),), (("h", d, False),))


def test_the_guard_the_schedule_states_is_read_over_the_cells_and_not_the_temps():
    low, _voc = reader()
    d, c, t, _w = low.guards["fetch"][0]
    assert low.guard(c, t) == [
        {"and": [{"and": [{"sub": [{"cell": "timer"}, 1]}, 0xFF]}, 0x80]},
        "!=",
        0,
    ]
    assert low.guard_value(ram(TIMER, 6, V("x"))) == {"cell": "timer"}
    assert low.onpath(d, c, t)
    low.stated, low.scope = frozenset({id(c)}), {"fetch"}
    assert not low.onpath(d, c, t)
    low.scope = {"head", "fetch"}
    assert low.onpath(d, c, t)
    low.gate = frozenset({(id(c), t)})
    assert not low.onpath(d, c, t)


def test_a_guard_term_is_a_comparison_of_the_objects_own():
    low, _voc = reader()
    low.lbl = "mach"
    assert low.term(Bin("==", V("n"), C(0)), True)[1] == "=="
    assert low.term(Bin("==", V("n"), C(0)), False)[1] == "!="
    assert low.term(Bin("<=", V("n"), C(0)), True) == [0, ">=", {"cell": "tn"}]
    assert low.term(Bin("<=", V("n"), C(0)), False)[1] == "<"
    assert low.term(V("n"), True) == [{"cell": "tn"}, "!=", 0]


# ---- read.py: the join plan and the jump table ----------------------------------
def test_a_diamond_folds_to_the_one_path_the_terms_it_states_do_not_decide():
    low, _voc = reader()
    eff, flagged = low.plan(set(low.proc.blocks))
    assert eff["join"] == ((), ()) and not flagged  # every path folds: no cell
    assert [t for _d, _c, t in eff["keyon"][0]] == [True, False]
    other_low = other(diamond())
    eff, flagged = other_low.plan(set(other_low.proc.blocks))
    assert eff["e"] == ((), ()) and not flagged
    assert len(eff["d"][0]) == 2


def test_a_joins_own_preds_do_not_stop_at_a_segments_edge():
    low = other(diamond())
    assert low.planall([["a", "b", "c", "e"], ["d", "g", "f"]]) == ["je"]
    assert low.eff["e"][1] == (({"cell": "je"}, "!=", 0),) and low.eff["e"][0] == ()
    assert {q for q, v in low.flagrows.items() for n, _c in v if n == "je"} == {"b", "c", "g"}
    assert low.planned == frozenset(low.eff)
    assert low.planall([["a", "b"], ["c", "d", "e", "f", "g"]]) == []


def test_a_block_one_case_of_a_jump_table_alone_reaches_stands_under_that_term():
    low = other(dispatch())
    term = low.proc.blocks["a"].term
    assert read._cases(term) == [("b", 0), ("c", 1)]
    assert read.edge(term, "b") == (Bin("==", V("y"), C(0, 2), 1),)
    assert not read.edge(term, "d")
    assert low._edge("a", "c") == (("a", Bin("==", V("y"), C(1, 2), 1), True),)
    assert not low._edge("a", "e")
    assert [d for d, _c, _t, _w in low.guards["b"]] == ["a"]
    assert low.guards["d"] == ()  # two cases reach it: no case is its term
    assert low._own("c") == (("a", Bin("==", V("y"), C(1, 2), 1), True),)


def test_the_reader_names_every_address_the_play_writes_and_refuses_nothing_yet():
    low, _voc = reader()
    assert (TIMER, TIMER + 3) in low.written
    assert low.refusals() == []
    low.bad.add("mach: $D400")
    assert low.refusals() == ["mach: $D400"]
