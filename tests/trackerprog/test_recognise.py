"""B7's recognition pass on hand-built rows and T1 records: no tune, no family."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from deity_informant.trackerprog import algebra as A  # noqa: E402
from deity_informant.trackerprog import build, recognise  # noqa: E402

G = [{"cell": "g"}, ">=", 1]
M8 = {"and": [None, 0xFF]}


class Region:
    def __init__(self, base):
        self.base = base


class View:
    def __init__(self, regions):
        self.regions = {k: Region(v) for k, v in regions.items()}

    def by_id(self):
        return self.regions


class Cells:
    """The address map the join asks for a name, and nothing else."""

    def __init__(self, names):
        self.names = names

    def name(self, addr, voice_indexed=True):
        del voice_indexed
        return self.names.get(addr)


class Phases:
    def __init__(self, streams, accs=None):
        self.streams, self.accs = streams, accs or {}


def row(when, sets, sites):
    return {"when": when, "sets": sets, build.SITES: sites}


def mask(e):
    return {"and": [e, 0xFF]}


def join(t1, t0, streams, cells, regions, accs=None):
    art = {"t0": {"writes": t0}, "t1": {"accs": t1}}
    ph = Phases(streams, accs)
    j = recognise.Join(art, View(regions), Cells(cells), ph)
    return j, j.run()


# ---- the algebra ---------------------------------------------------------------------
def test_the_evaluator_reaches_every_form_the_lowering_writes():
    env = {"a": 5, "#b": 0x104}
    assert A.ev({"add": [{"cell": "a"}, 1]}, env) == 6
    assert A.ev({"sub": [{"cell": "a"}, 1]}, env) == 4
    assert A.ev({"and": [{"global": "b"}, 0xFF]}, env) == 4
    assert A.ev({"or": [{"xor": [1, 2]}, 8]}, env) == 11
    assert A.ev({"shl": [{"shr": [{"global": "b"}, 2]}, 1]}, env) == 0x82
    assert A.ev({"carry_out": [{"global": "b"}, 8]}, env) == 1
    assert A.ev({"borrow_out": [{"global": "b"}, 8]}, env) == 0
    assert A.ev({"bit": [{"cell": "a"}, 0]}, env) == 1
    assert A.ev({"field": [{"global": "b"}, 0xF]}, env) == 4
    assert A.ev({"u16": [{"cell": "a"}, 1]}, env) == 0x105
    assert A.ev({"cell": ["a", 0]}, env) == 5


def test_a_leaf_the_arithmetic_does_not_reach_says_so():
    with pytest.raises(A.Opaque):
        A.ev({"ins": "col"}, {})
    with pytest.raises(A.Opaque):
        A.ev({"a": 1, "b": 2}, {})
    assert not A.evaluable({"ins": "col"})
    assert A.evaluable({"add": [{"cell": "a"}, 1]})


def test_the_free_cells_of_an_expression_carry_a_globals_own_hash():
    assert A.free({"add": [{"cell": "a"}, {"global": "b"}]}) == {"a", "#b"}
    assert A.free([{"cell": ["c", 1]}, 3]) == {"c"}


def test_a_constant_under_a_guard_is_enumerated_and_not_argued():
    e = {"borrow_out": [{"sub": [{"cell": "g"}, 1]}, 8]}
    assert A.constant_under(e, [G]) == 1
    assert A.constant_under(e, []) is None
    assert A.constant_under({"cell": "g"}, [G]) is None
    assert A.constant_under(e, [[{"ins": "x"}, "!=", 0], G]) == 1  # the term is dropped
    wide = {"add": [{"cell": "a"}, {"add": [{"cell": "b"}, {"cell": "c"}]}]}
    assert A.constant_under(wide, []) is None  # three cells: not enumerated


def test_the_shapes_a_record_is_read_out_of():
    assert A.target_of("x") == "@x" and A.target_of("#x") == "#x"
    assert A.read_of("x") == {"cell": "x"} and A.read_of("#x") == {"global": "x"}
    assert A.prefix([[1, 2, 3], [1, 2, 4]]) == [1, 2]
    assert A.prefix([[1], [2]]) == []
    assert A.extends([1, 2], [1]) and not A.extends([1], [1, 2])
    assert A.unsplit(mask({"cell": "r"}), mask({"shr": [{"cell": "r"}, 8]})) == {"cell": "r"}
    assert A.unsplit(mask({"cell": "r"}), mask({"cell": "q"})) is None
    assert A.unsplit({"cell": "r"}, {"cell": "q"}) is None


def test_an_accumulation_on_its_own_cell_peels_to_the_delta_it_applies():
    cell = {"cell": "a"}
    one = mask({"add": [{"cell": "t"}, {"cell": "d"}]})
    assert A.peel(one, cell, {"t": cell}) == {"cell": "d"}
    two = mask({"add": [one, {"cell": "c"}]})
    assert A.peel(two, cell, {"t": cell}) == {"add": [{"cell": "d"}, {"cell": "c"}]}
    assert A.peel(mask({"sub": [cell, 1]}), cell, {}) is None
    assert A.peel(mask({"add": [{"cell": "z"}, 1]}), cell, {}) is None
    assert A.peel(7, cell, {}) is None


def test_a_rewrite_replaces_the_reads_it_names_and_nothing_else():
    sub = {("cell", "c"): {"flag": "C"}}
    got = A.rewrite([{"add": [{"cell": "c"}, {"cell": "d"}]}], sub)
    assert got == [{"add": [{"flag": "C"}, {"cell": "d"}]}]
    assert A.rewrite({"cell": ["c", 1]}, sub) == {"cell": ["c", 1]}


# ---- the join ------------------------------------------------------------------------
def eight():
    """One 8-bit wrap accumulator: its store, its delta and its register."""
    t1 = [
        {
            "id": "a0",
            "width": 8,
            "cell": {"region": 1, "addr": "$0400", "name": "acc8"},
            "regions": [1],
            "sites": ["$1000"],
            "policy": "wrap",
            "delta": {"kind": "tabcell"},
            "bound": {"interval": [0, 255], "from": "projected", "witness": "width"},
            "target": {"register": "pw_lo"},
            "scope": "instrument",
            "phase": {"kind": "none"},
        }
    ]
    t0 = [{"register": "pw_lo", "site": {"pc": "$1004", "width": 1}, "cells": [{"region": 1}]}]
    streams = {
        "machine0": {
            "all": True,
            "rank": 0,
            "rows": [
                row([], [["@d", 3]], [None]),
                row(
                    [G],
                    [
                        ["@acc8", mask({"add": [{"cell": "acc8"}, {"cell": "d"}]})],
                        ["pw_lo", {"cell": "acc8"}],
                    ],
                    ["$1000", "$1004"],
                ),
                row([], [["@k", 1]], [None]),
            ],
        }
    }
    return t1, t0, streams, {0x400: "acc8"}, {1: 0x400}


def test_a_run_of_rows_whose_stores_are_t1s_own_becomes_one_section_5_record():
    j, report = join(*eight())
    assert [r["form"] for r in report] == ["acc"] and report[0]["why"] is None
    (rec,) = j.accs.values()
    assert rec["cell"] == "acc8" and rec["width"] == 8 and rec["policy"] == "wrap"
    assert rec["delta"] == {"cell": "d"} and rec["produce"] == [["pw_lo", "byte"]]
    assert rec["when"] == [G] and rec["bound"]["from"] == "projected"
    assert rec["target"] == "pw" and rec["scope"] == "instrument" and rec["rate"] == 1
    assert "flag" not in rec and "phase" not in rec


def test_the_rows_the_record_replaces_leave_the_stream_and_the_ranks_renumber():
    j, _report = join(*eight())
    assert set(j.streams) == {"machine0_0", "machine0_1"}
    assert [s["sets"] for s in j.streams["machine0_0"]["rows"]] == [[["@d", 3]], []]
    assert j.streams["machine0_1"]["rows"][0]["sets"] == [["@k", 1]]
    ranks = {j.streams["machine0_0"]["rank"], j.streams["machine0_1"]["rank"]}
    (rec,) = j.accs.values()
    assert ranks == {0, 2} and rec["rank"] == 1


def sixteen():
    """One 16-bit repeated addition: a reload, a loop, a carry and a word produced."""
    t1 = [
        {
            "id": "a0",
            "width": 16,
            "cell": {"region": 1, "addr": "$0500", "name": "acc16"},
            "regions": [1, 2],
            "sites": ["$2000"],
            "policy": "reload",
            "delta": {
                "kind": "repeat",
                "step": {"cell": {"addr": "$0600", "width": 2}},
                "n": {"addr": "$0602", "width": 1},
            },
            "bound": {"interval": [0, 65535], "from": "observed", "witness": "horizon"},
            "target": {"register": "freq"},
            "scope": "voice",
            "phase": {"kind": "bit", "cell": {"addr": "$0610", "width": 1}, "bit": 0},
        }
    ]
    t0 = [
        {
            "register": "freq",
            "site": {"pc": "$2010", "width": 2, "hifirst": False},
            "cells": [{"region": 1}],
        }
    ]
    seed = {"borrow_out": [{"sub": [{"cell": "g"}, 1]}, 8]}
    add = mask({"add": [{"cell": "lo16"}, {"global": "step_lo"}]})
    hiadd = mask({"add": [{"cell": "hi16"}, {"global": "step_hi"}]})
    streams = {
        "machine0": {
            "all": True,
            "rank": 4,
            "rows": [
                row([], [["@g", 3]], [None]),
                row(
                    [G],
                    [
                        ["@lo16", mask({"cell": "r"})],
                        ["@hi16", mask({"shr": [{"cell": "r"}, 8]})],
                        ["@cy", seed],
                    ],
                    ["$1FF0", "$1FF4", None],
                ),
                row(
                    [G, [{"cell": "g"}, "!=", 0]],
                    [["@lo16", add], ["@hi16", hiadd], ["@cy", {"cell": "c2"}]],
                    ["$2000", "$2004", None],
                ),
                row(
                    [G],
                    [
                        ["freq_lo", {"cell": "lo16"}],
                        ["freq_hi", {"cell": "hi16"}],
                        ["@z", {"cell": "cy"}],
                    ],
                    ["$2010", None, None],
                ),
                row([], [["@hi16", 7]], [None]),
            ],
        }
    }
    cells = {
        0x500: "lo16",
        0x510: "hi16",
        0x600: "#step_lo",
        0x601: "#step_hi",
        0x602: "#n",
        0x610: "b610",
    }
    return t1, t0, streams, cells, {1: 0x500, 2: 0x510}


def test_a_word_states_its_reload_its_repeat_and_the_carry_its_loop_leaves():
    j, report = join(*sixteen())
    assert [r["form"] for r in report] == ["acc"]
    (rec,) = j.accs.values()
    assert rec["width"] == 16 and rec["cell"] == "lo16"
    assert rec["policy"] == {"reload": {"cell": "r"}}
    assert rec["delta"] == {
        "repeat": [
            {"u16": [{"global": "step_lo"}, {"global": "step_hi"}]},
            {"global": "n"},
        ]
    }
    assert rec["delta_when"] == [[{"cell": "g"}, "!=", 0]]
    assert rec["flag"] == {"name": "cy", "seed": 1}
    assert rec["phase"] == {"bit": [{"cell": "b610"}, 0]}
    assert rec["produce"] == [["freq_lo", "lo"], ["freq_hi", "hi"]]


def test_the_carry_a_later_row_read_as_a_cell_it_reads_as_the_flag():
    j, _report = join(*sixteen())
    tail = [r for st in j.streams.values() for r in st["rows"]]
    assert [["@z", {"flag": "cy"}]] in [r["sets"] for r in tail]


def test_two_named_halves_become_one_wide_cell_with_section_5s_own_suffixes():
    j, _report = join(*sixteen())
    assert j.wide == ["lo16"] and j.merged == [("lo16", "hi16")]
    outside = [r["sets"] for st in j.streams.values() for r in st["rows"]]
    assert [["@lo16.hi", 7]] in outside


def test_a_stand_in_assignment_is_restated_as_the_record_it_stands_for():
    t1, t0, streams, cells, regions = eight()
    t1[0]["sites"] = ["$3000"]
    t0 = [{"register": "pw_lo", "site": {"pc": "$1004", "width": 1}, "cells": [{"region": 1}]}]
    streams = {
        "machine2": {
            "all": True,
            "rank": 2,
            "rows": [row([G], [["pw_lo", {"cell": "acc8"}]], ["$1004"])],
        }
    }
    accs = {
        "acc0": {
            "site": "$3000",
            "rank": 1,
            "cell": "acc8",
            "when": [G],
            "policy": {"reload": mask({"add": [{"cell": "acc8"}, {"cell": "d"}]})},
        }
    }
    cells = {0x400: "acc8"}
    j, report = join(t1, t0, streams, cells, regions, accs)
    assert [r["form"] for r in report] == ["acc"]
    rec = j.accs["acc0"]
    assert rec["policy"] == "wrap" and rec["delta"] == {"cell": "d"}
    assert rec["produce"] == [["pw_lo", "byte"]] and rec["bound"]["interval"] == [0, 255]
    assert j.streams["machine2"]["rows"][0]["sets"] == []


@pytest.mark.parametrize(
    "edit,why",
    (
        (lambda t1, t0, st, c: t1[0].update(width=11), "width 11 is not a section 5 width"),
        (lambda t1, t0, st, c: c.clear(), "no section 5 cell holds it"),
        (lambda t1, t0, st, c: t1[0].update(sites=["$9999"]), "no lowered row stores it"),
        (lambda t1, t0, st, c: t0.clear(), "T0 names no write of its own cells"),
        (lambda t1, t0, st, c: t1[0].update(policy="halt"), "policy 'halt' is no section 5 policy"),
        (
            lambda t1, t0, st, c: t1[0]["delta"].update(kind="const"),
            "the store is no accumulation on its own cell",
        ),
    ),
)
def test_what_the_join_cannot_settle_it_refuses_by_name(edit, why):
    t1, t0, streams, cells, regions = eight()
    streams["machine0"]["rows"][1]["sets"][0][1] = {"cell": "d"}  # no accumulation
    if "accumulation" not in why:
        streams["machine0"]["rows"][1]["sets"][0][1] = A.rewrite(
            mask({"add": [{"cell": "acc8"}, {"cell": "d"}]}), {}
        )
    edit(t1, t0, streams, cells)
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["form"] == "sets" and report[0]["why"] == why


def test_a_word_whose_second_region_t1_does_not_name_is_refused():
    t1, t0, streams, cells, regions = eight()
    t1[0].update(width=16)
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "T1 names no second region for the word"


def test_rows_outside_the_machines_rank_order_are_refused():
    t1, t0, streams, cells, regions = eight()
    del streams["machine0"]["rank"]
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "its rows stand outside the machine's rank order"


def test_stores_of_one_cell_in_two_streams_that_both_hold_a_site_are_refused():
    t1, t0, streams, cells, regions = eight()
    t1[0]["sites"] = ["$1000", "$1100"]
    streams["machine1"] = {
        "all": True,
        "rank": 1,
        "rows": [row([G], [["@acc8", mask({"add": [{"cell": "acc8"}, 1]})]], ["$1100"])],
    }
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "its own sites stand in 2 streams"


def test_the_seed_the_loop_enters_with_must_be_a_constant_on_the_guards_own_set():
    t1, t0, streams, cells, regions = sixteen()
    streams["machine0"]["rows"][1]["sets"][2][1] = {"cell": "g"}
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "the carry it enters the loop with is no constant"


def test_a_step_or_a_count_the_object_has_no_cell_for_is_refused():
    t1, t0, streams, cells, regions = sixteen()
    del cells[0x602]
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "no cell holds the step or the count"


def test_a_field_delta_reads_the_cell_t1_names_it_on():
    t1, t0, streams, cells, regions = eight()
    t1[0]["delta"] = {"kind": "field", "cell": {"addr": "$0410", "width": 1}, "mask": 255}
    cells[0x410] = "#dfield"
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["form"] == "acc"
    t1, t0, streams, cells, regions = eight()
    t1[0]["delta"] = {"kind": "field", "cell": {"addr": "$0999", "width": 1}, "mask": 255}
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "no cell holds the delta"


def test_a_word_delta_the_lowering_alone_states_is_refused():
    t1, t0, streams, cells, regions = sixteen()
    t1[0]["delta"] = {"kind": "tabcell"}
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "a tabcell delta on a word is no section 5 form"


def test_a_reload_that_is_not_one_store_of_its_own_cell_is_refused():
    t1, t0, streams, cells, regions = sixteen()
    streams["machine0"]["rows"][0]["sets"] = [["@lo16", 5]]
    streams["machine0"]["rows"][0][build.SITES] = ["$1AAA"]
    _j, report = join(t1, t0, streams, cells, regions)
    assert report[0]["why"] == "the reload is not one store under the record's own guard"


def test_the_state0_seed_of_a_merged_word_is_the_one_16_bit_cell_it_is():
    cellseed, globseed = build.widen(
        {"lo16": [1, 2, 3], "hi16": [4, 5, 6]}, {}, [("lo16", "hi16")], b"", None
    )
    assert cellseed == {"lo16": [0x401, 0x502, 0x603]} and globseed == {}


def test_state0_is_held_to_the_cells_the_object_reads_or_writes():
    obj = {
        "meta": {"wide": [], "tempo": {"cell": "timer"}},
        "accs": {"a": {"cell": "#g", "delta": {"cell": "live"}}},
        "streams": {"s": {"rows": [{"sets": [["@kept", {"cell": "live"}]]}]}},
        "score": {},
        "state0": {"cells": {"kept": [0], "live": [1], "dead": [2], "timer": [3]}, "globals": {}},
    }
    build.prune(obj)
    assert set(obj["state0"]["cells"]) == {"kept", "live", "timer"}
