"""B7's shapes: the segments a binding assembles into, and the liveness that trims them."""

import pytest

from _bound import (
    C,
    INSC,
    NOTE,
    ORDPOS,
    TIMER,
    V,
    art,
    binder,
    image,
    names,
    other,
    reader,
    regions,
    store,
    View,
)
from _procs import trapping
from deity_informant.trackerprog import emit, region, schedule, sections, shape
from deity_informant.trackerprog.cells import Cells
from deity_informant.trackerprog.refuse import Refused


def test_a_block_the_program_cannot_leave_is_no_block_of_a_phase():
    low = other(trapping())
    assert shape._live(low.prog, "tick", ["a", "b", "t"]) == ["a", "b"]


def test_the_order_cursor_is_the_address_t2_names_or_the_region_it_names():
    a = art()
    assert shape._order_cursor(a, a["view"], a["names"]) == ORDPOS
    a["t2"]["score"][0]["order"] = [{"table": "orders", "cursor": "ordpos"}]
    assert shape._order_cursor(a, a["view"], a["names"]) == ORDPOS
    a["t2"]["score"][0]["order"] = [{"table": "orders", "cursor": "nosuchregion"}]
    assert shape._order_cursor(a, a["view"], a["names"]) is None
    a["t2"]["score"][0]["order"] = []
    assert shape._order_cursor(a, a["view"], a["names"]) is None


def test_the_row_is_the_regions_a_pattern_table_is_read_in_and_where_they_rejoin():
    b = binder()
    assert b.rowfb == {"fetch", "keyon", "wrap", "join"}
    a = art()
    got, _refused = region.fetch(a["prog"], emit.tables_of(a["t2"], a["view"], a["names"]))
    assert [r.entry for r in shape._channels(a["prog"], "tick", got, set())] == ["fetch"]
    assert shape._latches(a["prog"], "tick", b.sch) == {"back"}


def test_one_record_of_the_selector_is_its_columns_and_its_pulse_pair():
    a = art()
    got = shape._instruments(
        a, a["view"], a["names"], (INSC, {2: "wave"}, 2, 2, [0, 1]), {3: "hi"}, image(), {"a0": {}}
    )
    assert got == {
        "0": {"wave": 0x41, "pw": [0, 0x0A], "accs": [{"acc": "a0"}]},
        "1": {"wave": 0x21, "pw": [0, 0x0B], "accs": [{"acc": "a0"}]},
    }
    off = shape._instruments(
        a, a["view"], a["names"], (INSC, {2: "wave"}, 2, 2, [0, 2]), {}, image(), {}
    )
    assert sorted(off) == ["0", "2"] and off["2"]["wave"] == 0x21  # the offset it already is


def test_a_datum_the_binding_cannot_do_without_refuses_by_name():
    assert shape._need([1], "command residue", "x", "y") == [1]
    with pytest.raises(Refused):
        shape._need((), "command residue", "x", "y")


def test_the_slots_the_player_owns_are_bound_to_the_addresses_s6_and_t2_name():
    cells = Cells(View(regions(), {}, image()), names())
    shape._rename(cells, {"note": NOTE, "orderpos": None})
    assert cells.rename == {NOTE: "note"}


def test_a_word_s6_names_by_its_low_half_is_that_words_own_name():
    n = names()
    n.u16 = {((8, None), (9, None)): "pattern_ptr"}
    assert shape._u16name(n, 8) == "pattern_ptr"
    assert shape._u16name(n, 9) is None
    assert shape._u16name(names(), 8) is None


def test_the_carry_a_repeated_addition_leaves_is_the_one_flag_channel():
    assert shape._flags({"add": [1, {"flag": "C"}]}) == {"C"}
    assert shape._flags([{"cell": "a"}, {"flag": "V"}]) == {"V"}
    assert shape._flags(3) == set()


def test_a_cell_the_object_names_by_its_halves_is_seeded_as_the_word_it_is():
    obj = {"state0": {"cells": {"f.lo": [1, 2], "f.hi": [3, 4], "g.lo": [5, 6], "a": [7]}}}
    assert shape._merge_halves(obj)["state0"]["cells"] == {
        "a": [7],
        "f": [1 | (3 << 8), 2 | (4 << 8)],
        "g": [5, 6],
    }


def test_every_cell_one_expression_reads_is_named_and_a_register_is_not():
    got = shape._reads([{"cell": "a"}, {"cell": ["b", 1]}, {"global": "c"}, 3])
    assert got == {"a", "b", "c"}
    assert shape._needed("ctrl") and not shape._needed("@cell") and not shape._needed("#g")


def test_a_stream_is_named_once_and_carries_its_rank_where_it_has_one():
    out = shape._Out()
    assert out.stream("s0", [{"sets": []}]) == "s0"
    assert out.streams["s0"] == {"rows": [{"sets": []}], "all": True}
    out.stream("s1", [], rank=2)
    assert out.streams["s1"]["rank"] == 2


def test_consecutive_steps_of_one_kind_are_the_rows_of_one_stream():
    steps = [
        ("a", "set", [], [["@x", 1]], frozenset()),
        ("a", "note", [], [], frozenset()),
        ("b", "reg", [[1, "!=", 0]], [["ctrl", 2]], frozenset()),
    ]
    assert shape._rows_of(steps, ("set", "reg")) == [
        {"when": [], "sets": [["@x", 1]]},
        {"when": [[1, "!=", 0]], "sets": [["ctrl", 2]]},
    ]


def test_a_dead_assignment_is_dropped_and_the_streams_nothing_names_go_with_it():
    obj = {
        "streams": {
            "s0": {
                "rank": 0,
                "rows": [
                    {
                        "when": [],
                        "sets": [["@dead", 1], ["@live", 2], ["ctrl", {"cell": "live"}]],
                    }
                ],
            },
            "s1": {"rows": [{"when": [], "sets": [["@dead", 4]]}]},
            "t0": {"rows": [{"b": 1}]},
        },
        "accs": {"a0": {"cell": "#kept", "delta": {"tabcell": ["t0", 0, "b"]}}},
        "meta": {
            "row": [{"sets": [["@live", 5]]}, {"stream": "s1"}],
            "tick": ["row"],
            "tempo": {"cell": "rowsleft"},
        },
        "score": {},
        "globals": {"streams": ["s1"]},
        "instruments": {},
    }
    shape._dce(obj)
    assert [s[0] for s in obj["streams"]["s0"]["rows"][0]["sets"]] == ["@live", "ctrl"]
    assert sorted(obj["streams"]) == ["s0", "t0"]  # s1 wrote nothing anything reads
    assert obj["meta"]["row"] == [{"sets": [["@live", 5]]}]
    assert "streams" not in obj["globals"]
    assert shape._tables(obj) == {"t0"}


def test_how_far_past_the_tuning_a_transposition_of_the_objects_own_reaches():
    streams = {
        "s0": {"rows": [{"when": [[{"transpose": 2}, "!=", 0]], "sets": [["a", 1]]}]},
        "s1": {"rows": [{"sets": [["b", {"and": [{"transpose": 5}, 0xFF]}]]}]},
    }
    assert shape._transposed(streams) == 5
    assert shape._offsets({"transpose": None}, [0]) == [0]


def test_the_clock_the_tick_refills_at_its_end_is_the_clocks_own_reset_clause():
    low, _voc = reader()
    st = store(TIMER, 6, C(4), V("x"), src=0x1010)
    c = low.proc.blocks["tail"].term.c
    sch = schedule.Schedule("tick", resets=((st, ((c, True),)),))
    got = shape._resets(low, "rowsleft", sch)
    assert got == {
        "reset": [{"when": [[{"cell": "voice_index"}, "==", 0]], "sets": [["@rowsleft", 4]]}]
    }
    assert not shape._resets(low, "rowsleft", schedule.Schedule("tick"))


# ---- sections.py: the parts of the object each plane supplied --------------------
def test_the_records_the_object_reads_as_sixteen_bits_and_the_flags_they_leave():
    b = binder()
    b.accs = {
        "a0": {"cell": "#freqword", "width": 16, "delta": {"repeat": [1, 2]}},
        "a1": {"cell": "ins.pw.lo", "width": 16, "delta": {"add": [1, {"flag": "C"}]}},
    }
    assert sections.wide(b) == {"freqword"}
    assert sections.flags(b) == {"flags": {"C": {"default": {"const": 0}}}}
    assert b.accs["a0"]["flag"] == {"name": "C", "seed": 0}
    b.accs = {}
    assert not sections.flags(b)


def test_an_instrument_the_words_past_the_tuning_do_not_reach_states_no_pitch():
    b = binder()
    got = {"2": {"wave": 0x11}}
    sections.pitched(b, got, [{"u16": [0, 0]}])
    assert "pitch" not in got["2"]  # the word one past the tuning is not stated
    sections.pitched(b, got, [{"u16": [0, 0]}, {"trap": "no cell holds 1 past"}])
    assert "pitch" not in got["2"]
    sections.pitched(b, {"9": {}}, [{"u16": [0, 0]}] * 2)  # no record the score selects
    sections.pitched(b, got, [{"u16": [0, 0]}] * 2)
    assert got["2"]["pitch"] == {"value": {"u16": [0, 0]}}


def test_a_word_past_the_tuning_the_score_keeps_as_a_field_is_a_trap():
    b = binder()
    words = [{"u16": [1, 2]}, {"u16": [{"cell": ["cmd", 0]}, 2]}]
    assert sections.trapped(b, words) == words  # the cell is no packed row byte
    b.packed = {"n"}
    got = sections.trapped(b, words)
    assert got[0] == words[0] and "trap" in got[1]
