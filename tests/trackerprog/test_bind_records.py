"""B7's records: T1's accumulators rendered into section 5's, over the object's cells."""

from _bound import C, FREQ, GLOB, INSC, NOTE, SWEEP, V, VOICES, WAVE, binder, other, reader
from _procs import halver
from deity_informant.trackerprog import records
from deity_informant.tuneprog.ir import Load


def acc(**kw):
    """One T1 record over the tune's own sweep cell, with the fields a test varies."""
    got = {
        "id": "a0",
        "cell": {
            "addr": "$%04X" % SWEEP,
            "region": 10,
            "copies": VOICES,
            "name": "sweep",
            "width": 8,
        },
        "regions": [10],
        "width": 8,
        "target": {"register": "pw_lo"},
        "policy": "free",
        "scope": "voice",
        "sites": ["$1068"],
        "delta": {"kind": "const", "value": 1},
    }
    for k, v in kw.items():
        got[k] = v
    return got


def accs():
    """The record reader over the tune, with the plan its guards are read against."""
    b = binder()
    return records.Accs(b.low, b.art, b.names, b.view), b


def test_a_record_names_the_region_the_sites_and_the_guard_they_all_stand_under():
    A, _b = accs()
    a = acc()
    assert A.base_of("sweep") == SWEEP and A.base_of("nosuchregion") is None
    assert A.siteblocks(a) == ["mach"]
    assert A.siteblocks(acc(sites=[])) == []
    assert not A.when(a) and not A.when(acc(sites=[]))
    # two sites, and the record's guard is the terms both of them stand under
    assert len(A.when(acc(sites=["$1030", "$1044"]))) == 1
    assert A.under("mach", ()) and not A.under("nosuchblock", ())
    assert not A.under("keyon", (("head", C(1), True),))


def test_the_cell_a_record_moves_is_a_voices_own_a_global_or_the_instruments_pair():
    A, b = accs()
    assert A.cellname(acc(), SWEEP) == "sweep"
    one = acc()
    one["cell"] = dict(one["cell"], copies=1)
    assert A.cellname(one, SWEEP) == "#sweep"
    assert b.low.cells.widths["#sweep"] == 1
    b.low.cells.inspw = {2: "lo"}
    assert A.cellname(acc(), WAVE) == "ins.pw.lo"


def test_a_records_produce_is_the_t0_writes_its_own_cells_reach():
    A, _b = accs()
    assert A.produce(acc(), ()) == ([["pw_lo", "byte"]], {0x106C}, ["mach"])
    assert A.produce(acc(regions=[7]), ()) == ([], set(), [])  # no write reaches its cells
    A.t0 = A.t0 + [{"site": {"block": "mach"}, "cells": [{"region": 10}]}]
    assert A.produce(acc(), ())[0] == [["pw_lo", "byte"]]  # a write of no register at all
    wide = acc(
        width=16,
        regions=[10, 11],
        cell={
            "addr": "$%04X" % SWEEP,
            "region": 10,
            "copies": VOICES,
            "name": "sweep",
            "width": 16,
        },
    )
    assert A.produce(wide, ())[0] == [["pw_lo", "lo"]]
    A.t0 = [
        {"register": "freq", "site": {"pc": "$1060", "block": "mach"}, "cells": [{"region": 10}]}
    ]
    assert A.produce(wide, ())[0] == [["freq_lo", "lo"], ["freq_hi", "hi"]]
    assert sorted(A.regsites("mach", "freq")) == [0x1060, 0x1064]
    assert A.regsites("mach", "pw_lo") == {0x106C}
    assert A.regsites("nosuchblock", "ad") == set()


def test_a_delta_is_one_of_section_fives_four_forms_or_no_record_at_all():
    A, _b = accs()
    assert A.delta(acc(), "mach") == 1
    field = acc(delta={"kind": "field", "cell": {"addr": "$%04X" % GLOB, "width": 1}, "mask": 0x0F})
    assert A.delta(field, "mach") == {"and": [{"global": "scratch"}, 0x0F]}
    # a fold that leaves a score byte is no reading of the record's own input
    assert A.cellvalue(INSC, 1) == {"cell": "ins"}
    assert A.delta(acc(delta={"kind": "tabcell", "cell": {"region": 2}}), "mach") == {"ins": "wave"}
    assert A.delta(acc(delta={"kind": "tabcell", "cell": {"region": 99}}), "mach") is None
    assert A.delta(acc(delta={"kind": "elsewhere"}), "mach") is None
    assert A.delta(acc(delta=None), "mach") is None
    carry = acc(delta={"kind": "const", "value": 2, "carry": {"flag": "C!"}})
    assert A.delta(carry, "mach") == {"add": [2, {"flag": "C"}]}
    assert A.cellvalue(GLOB, 1) == {"global": "scratch"}


def test_a_repeat_is_a_table_difference_shifted_down_by_the_loops_own_count():
    A, _b = accs()
    step = {"index": {"addr": "$%04X" % NOTE}, "span": 2, "cell": {"addr": "$%04X" % FREQ}}
    assert A.tablestep(step, "mach") == {"interval": None}
    assert A.tablestep(dict(step, span=1), "mach") is None
    assert A.tablestep(dict(step, index=None), "mach") is None
    assert (
        A.delta(
            acc(
                delta={"kind": "repeat", "step": dict(step, span=1), "n": {"addr": "$%04X" % GLOB}}
            ),
            "mach",
        )
        is None
    )
    got = A.delta(
        acc(delta={"kind": "repeat", "step": step, "n": {"addr": "$%04X" % GLOB}}), "mach"
    )
    assert got == {"repeat": [{"interval": None}, {"global": "scratch"}]}


def test_a_loop_that_halves_a_word_once_a_turn_shifts_it_by_its_counters_value():
    low = other(halver())
    assert records._halving(low.proc.blocks["h"].stmts[0], {SWEEP, SWEEP + 1})
    assert not records._halving(low.proc.blocks["a"].stmts[0], {SWEEP})
    assert records.shift_of(low, GLOB, {SWEEP, SWEEP + 1}) == {"add": [3, 1]}
    assert records.shift_of(low, None, {SWEEP}) == 0
    assert records.shift_of(low, GLOB, {0x9999}) == 0
    assert records.shift_of(other(halver(False)), GLOB, {SWEEP, SWEEP + 1}) == 3


def test_a_reload_is_read_where_the_records_own_reload_stands():
    A, _b = accs()
    assert A.policy(acc(), "mach") == "free"
    assert not A.reloads(acc())
    got = A.policy(acc(policy="reload"), "mach")
    assert got == {"reload": {"and": [{"add": [{"cell": "sweep"}, 1]}, 0xFF]}}
    wide = acc(policy="reload", width=16, regions=[10, 6])
    assert "or" in A.policy(wide, "mach")["reload"]
    a = acc(policy="reload", sites=["$1004"])  # a site outside the block that steps it
    assert A.reloads(a) == {SWEEP: ("mach", V("sw2"))}
    assert A.policy(a, "mach") == {"reload": {"and": [{"add": [{"cell": "sweep"}, 1]}, 0xFF]}}
    half = acc(policy="reload", sites=["$1004"], width=16, regions=[10, 6])
    assert "or" in A.policy(half, "mach")["reload"]  # one half reloaded, one not


def test_a_phase_is_one_bit_of_a_live_cell_or_no_phase_at_all():
    A, _b = accs()
    assert A.phase(acc(), "mach") is None
    assert A.phase(acc(phase={"kind": "range"}), "mach") is None
    got = A.phase(acc(phase={"kind": "bit", "cell": {"addr": "$%04X" % GLOB}, "bit": 2}), "mach")
    assert got == {"bit": [{"global": "scratch"}, 2]}


def test_one_record_is_section_fives_own_and_the_stores_it_states():
    A, b = accs()
    rec, drop, why = A.record(acc(), 3)
    assert why is None and drop == {0x1068, 0x106C}
    assert rec["rank"] == 3 and rec["cell"] == "sweep" and rec["delta"] == 1
    assert rec["produce"] == [["pw_lo", "byte"]] and rec["when"] == []
    assert "phase" not in rec and "delta_when" not in rec
    ph = acc(phase={"kind": "bit", "cell": {"addr": "$%04X" % GLOB}, "bit": 0})
    assert "phase" in A.record(ph, 0)[0]
    assert A.record(acc(delta={"kind": "elsewhere"}), 0) == (
        None,
        set(),
        "T1's delta is no section 5 form",
    )
    assert [x["id"] for x in A.order(b.low.rpo)] == ["a0"]


def test_a_word_stated_once_is_no_pair_of_masked_halves():
    lo, hi = {"and": [{"cell": "r"}, 0xFF]}, {"and": [{"shr": [{"cell": "r"}, 8]}, 0xFF]}
    assert records._unsplit(lo, hi) == {"cell": "r"}
    assert records._unsplit(lo, {"and": [{"cell": "q"}, 0xFF]}) is None
    assert records._unsplit({"cell": "r"}, hi) is None
    assert records._unsplit(lo, {"cell": "q"}) is None
    assert records._flagname("C!x") == "Cx" and records._flagname(None) == "C"
    assert records._addr(None) is None and records._addr({"addr": "$1234"}) == 0x1234


def test_a_cell_the_object_has_no_name_of_its_own_for_is_read_as_the_cell():
    low, _voc = reader()
    low.lbl = "mach"
    assert records._load(low, GLOB) == Load("ram", C(GLOB, 2), 1, GLOB, GLOB, -1)
    assert records._clean(low, {"cell": "sweep"})
    low.temp("n")
    assert not records._clean(low, {"cell": "tn"})


def test_a_term_the_step_stands_under_and_the_produce_does_not_is_the_deltas_own():
    A, _b = accs()
    a = acc(
        sites=["$1030"],
        regions=[4],
        cell={"addr": "$%04X" % NOTE, "region": 4, "copies": VOICES, "name": "note"},
    )
    assert len(A.when(a)) == 2  # the fetch's own path, which the machine's is not
    rec, drop, why = A.record(a, 0)
    assert why is None and rec["when"] == []
    assert rec["delta_when"] == [
        [{"and": [{"and": [{"sub": [{"cell": "phase"}, 1]}, 0xFF]}, 0x80]}, "!=", 0],
        ["wraps", "==", 0],
    ]
    assert 0x1030 in drop and 0x1060 in drop
