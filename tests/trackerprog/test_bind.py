"""B7's binding of one synthetic tune's planes to the player, whole and step by step."""

import pytest

from _bound import (
    C,
    CMD,
    GLOB,
    INSC,
    NOTE,
    NOTES,
    ORDPOS,
    PATTERNS,
    PTRH,
    PTRL,
    STAGE,
    SWEEP,
    TICKS,
    TIMER,
    V,
    VOICES,
    art,
    binder,
    bound,
    ram,
    store,
)
from deity_informant.trackerprog import bind
from deity_informant.trackerprog.refuse import Refused
from deity_informant.tuneprog.ir import Bin, Block, Goto, If, Return


# ---- the whole binding ----------------------------------------------------------
def test_the_binding_derives_the_schedule_the_certified_tick_carries():
    _obj, report = bound()
    sch = report["schedule"]
    assert sch["tick"] == ["prelude", "row", "machine"]
    assert sch["commit_order"] == ["ctrl", "ad", "sr"]
    assert sch["tempo.step"] == -1 and sch["tempo.rate"] == 1 and sch["tempo.phase"] == 0
    assert sch["tempo.boundary_terms"] == 1 and sch["tempo.resets"] == 0
    assert sch["segments"] == [("prelude", 1), ("row", 4), ("machine", 3)]
    assert not sch["row_consumes_tick"] and report["refusals"] == []


def test_the_object_binds_the_tuning_the_records_and_the_instruments():
    obj, _report = bound()
    assert obj["$trackerprog"] == 1 and obj["meta"]["family"] == "bound"
    assert obj["meta"]["voices"] == 3 and obj["meta"]["voice_order"] == [2, 1, 0]
    assert obj["meta"]["tempo"]["cell"] == "rowsleft"
    assert obj["pitch"]["base"] == 0 and len(obj["pitch"]["freq"]) == NOTES
    assert obj["accs"]["a0"] == {
        "rank": 0,
        "cell": "sweep",
        "target": "pw_lo",
        "width": 8,
        "delta": 1,
        "policy": "free",
        "bound": {"from": "projected", "interval": [0, 255], "witness": "the 8-bit store"},
        "rate": 1,
        "scope": "voice",
        "produce": [["pw_lo", "byte"]],
        "when": [],
    }
    assert sorted(obj["instruments"]) == ["0", "1", "2"]
    assert obj["instruments"]["1"] == {
        "wave": 0x21,
        "adsr": 0x0B,
        "pw": [0, 0],
        "accs": [{"acc": "a0"}],
    }


def test_the_machine_segment_is_one_stream_over_the_objects_own_names():
    obj, _report = bound()
    st = obj["streams"]["machine0"]
    assert st["all"] and st["rank"] == 1
    assert st["beyond"]["words"] == [{"u16": [0x70, 1]}]
    assert st["rows"][0]["sets"] == [
        ["freq_lo", {"and": [{"transpose": 0}, 0xFF]}],
        ["freq_hi", {"and": [{"shr": [{"transpose": 0}, 8]}, 0xFF]}],
        ["ctrl", {"ins": "wave"}],
        ["ad", {"ins": "adsr"}],
        ["sr", {"ins": "adsr"}],
        ["pw_hi", {"cell": "cmd"}],
        ["mode_vol", {"global": "scratch"}],
    ]


def test_a_block_outside_the_voice_loop_is_one_stream_of_the_objects_globals():
    obj, _report = bound()
    assert obj["globals"]["streams"] == ["global0"]
    assert obj["streams"]["global0"]["rows"] == [{"when": [], "sets": [["#scratch", 0x0F]]}]
    assert obj["state0"]["globals"] == {"scratch": 3}
    assert obj["state0"]["cells"]["sweep"] == [0, 1, 2]
    assert obj["state0"]["cells"]["rowsleft"] == [0, 0, 0]


def test_the_row_program_is_the_fetchs_own_steps_over_the_rows_facts():
    obj, _report = bound()
    assert obj["meta"]["row"] == [
        {"note": True, "when": [["wraps", "==", 0]]},
        {"ins": True},
        {"commands": True},
        {"stream": "note_on0"},
        {"sets": [["@cmd", 0], ["#scratch", 0x0E]], "when": [["wraps", "!=", 0]]},
    ]
    assert obj["streams"]["note_on0"]["rows"] == [
        {
            "when": [["wraps", "==", 0]],
            "sets": [["ctrl", {"and": [{"shr": [{"cell": "tc"}, 4]}, 3]}]],
        }
    ]
    assert obj["meta"]["row_command"] == "spent"


def test_the_score_is_the_visits_the_horizon_made_grouped_by_the_order_cursor():
    obj, _report = bound()
    assert [o["play"] for o in obj["score"]["orders"]] == [[0, 1, 0]] * VOICES
    got = obj["score"]["patterns"]["0"]["events"]
    assert [e["dur"] for e in got] == [2, 1, 1]
    assert [e["note"] for e in got] == [5, 7, None]
    assert [e["ins"] for e in got] == [1, 2, None]
    assert [e["sounds"] for e in got] == [True, True, False]
    assert [e["tie"] for e in got] == [False, True, False]
    assert got[0]["arm"] == {"rows": [{"sets": [["@cmd", 5]]}]} and got[2]["arm"] is None
    assert [e["dur"] for e in obj["score"]["patterns"]["1"]["events"]] == [3, 2, 2]


def test_the_coverage_counts_what_each_plane_supplied():
    obj, report = bound()
    assert report["coverage"] == {
        "store_sites": 26,
        "streams": 3,
        "rows": 8,
        "sets": 11,
        "accs": 1,
        "t1_accumulators": 1,
        "t1_recognised": 1,
        "cells": 5,
        "patterns": 2,
        "events": 6,
        "instruments": 3,
        "refused": [],
    }
    assert report["rows"] == 3 and report["accs"] == 1 and report["patterns"] == 2
    assert obj["meta"]["horizon"] == TICKS


def test_a_hint_is_written_where_the_schema_puts_it():
    obj, _report = bind.lift(art(), ticks=4, hints={"meta.commit_order": ["ad", "sr", "ctrl"]})
    assert obj["meta"]["commit_order"] == ["ad", "sr", "ctrl"]


# ---- the binder, step by step ----------------------------------------------------
def test_the_binder_names_the_players_slots_from_s6_t1_and_t2():
    b = bind.Binder(art(), ticks=TICKS)
    assert b.freqpair() == (None, None)  # no 16-bit frequency record
    assert b.copied(CMD) == CMD  # two blocks store it: no copy of one cell
    assert b.copied(STAGE) == CMD  # one store, of the per-voice cell it copies
    assert b.copied(NOTE) == NOTE  # one store, of no cell the voice index names
    b.roles()
    assert b.slots == {"note": NOTE, "ins": INSC, "rowsleft": TIMER, "orderpos": ORDPOS}
    assert b.clockcell == "rowsleft" and b.orderbase == ORDPOS
    assert b.cells.rename[NOTE] == "note" and b.cells.rename[ORDPOS] == "orderpos"
    assert b.voc.subst == {"t0": {"cell": "phase"}}
    assert 0x1010 in b.voc.dropstores and 0x1020 in b.voc.dropstores
    assert b.segs["row"] == ["fetch", "keyon", "wrap", "join"]


def test_the_supplied_names_are_the_bytes_no_cell_of_the_tune_holds():
    b = bind.Binder(art(), ticks=TICKS)
    b.roles()
    assert b.supplied() == {"n", "c"}
    assert b.low.v.supplied == {"n", "c"}


def test_the_horizon_is_recorded_over_the_fetch_region_one_visit_a_row():
    b = bind.Binder(art(), ticks=TICKS)
    b.roles()
    b.supplied()
    recs = b.visits()
    assert b.vvar == "x" and b.trap is None and not b.badinputs
    assert len(recs) == 3 * len(set(r["env"]["x"] for r in recs)) * 3
    assert {r["env"]["x"] for r in recs} == {0, 1, 2}
    assert all(r["temps"]["c"] in PATTERNS for r in recs)


def test_the_fields_of_a_score_byte_are_what_the_horizons_own_visits_say():
    b = bind.Binder(art(), ticks=TICKS)
    b.roles()
    b.supplied()
    got = b.bind_fields(b.visits())
    assert got == {
        ("c", 0x0F): {"cell": "dur"},
        ("n", None): {"cell": "note"},
        ("n", 0x80): {"xor": ["sounds", 1]},
        ("c", 0x40): {"cell": "tied"},
    }
    assert b.tiemask == ("c", 0x40) and b.packed == {"c"} and b.left == []
    assert list(b.voc.terms.values()) == ["wraps"]
    assert b.sc == {0x1038: ("@cmd", CMD, "n")} and b.armcells == b.sc
    assert b.tie({"temps": {"c": 0x40}}) and not b.tie({"temps": {"c": 0x11}})
    b.tiemask = None
    assert not b.tie({"temps": {"c": 0x40}})


def test_a_plan_over_the_segments_folds_every_join_this_tick_has():
    b = binder()
    assert not b.plan(b.low.rpo) and not b.refusals
    assert b.low.eff["join"] == ((), ())
    assert b.amb == {"x": {"top": C(2), "back": Bin("-", V("x"), C(1))}}


def test_a_tick_the_planes_do_not_state_refuses_and_emits_nothing():
    no_score = art()
    no_score["t2"]["score"] = []
    with pytest.raises(Refused) as x:
        bind.lift(no_score)
    assert x.value.refusals[0].why == "score not cursor-shaped"
    no_pitch = art()
    no_pitch["t2"]["pitch"] = {"entries": []}
    with pytest.raises(Refused) as x:
        bind.lift(no_pitch)
    assert x.value.refusals[0].why == "unclassified update"
    no_ins = art()
    no_ins["t2"]["selectors"] = []
    with pytest.raises(Refused) as x:
        bind.lift(no_ins)
    assert x.value.refusals[0].why == "command residue"


def test_a_tick_no_counter_steps_has_no_row_clock_and_no_object():
    a = art()
    head = a["prog"].procs["tick"].blocks["head"]
    head.stmts[1] = store(TIMER, 6, C(1), V("x"), src=0x1010)
    with pytest.raises(Refused) as x:
        bind.lift(a)
    assert x.value.refusals[0].why == "unclassified update"
    assert x.value.refusals[0].detail == "no row clock steps the voice loop"


def test_an_instrument_whose_sound_the_tuning_has_no_note_for_states_its_own_pitch():
    obj, _report = bound()
    got = obj["score"]["patterns"]["1"]["events"]
    assert got[1]["sounds"] and got[1]["note"] is None  # one past the tuning's last entry
    assert obj["instruments"]["2"]["pitch"] == {
        "value": {"u16": [0x77, 1]},
        "octave": {"u16": [0, 0]},
    }
    assert "pitch" not in obj["instruments"]["1"]


# ---- bind.py: the planes a variant of the tune states differently ----------------
def test_a_sixteen_bit_record_of_the_frequency_pair_is_the_players_own_freq():
    a = art()
    a["t1"]["accs"] = [
        {
            "id": "f0",
            "cell": {"addr": "$%04X" % PTRL, "region": 8, "copies": VOICES, "name": "ptrlo"},
            "regions": [8, 9],
            "width": 16,
            "target": {"register": "freq"},
            "policy": "free",
            "scope": "voice",
            "sites": ["$1024"],
            "delta": {"kind": "const", "value": 1},
        }
    ]
    assert bind.Binder(a, ticks=4).freqpair() == (PTRL, PTRH)
    a["t1"]["accs"][0]["regions"] = [8]
    assert bind.Binder(a, ticks=4).freqpair() == (None, None)
    a["t1"]["accs"][0]["target"] = {"register": "ctrl"}
    assert bind.Binder(a, ticks=4).freqpair() == (None, None)


def test_a_clock_no_row_of_the_tune_reloads_is_that_voices_own_cell_and_no_rowsleft():
    a = art()
    fetch = a["prog"].procs["tick"].blocks["fetch"]
    fetch.stmts = [s for s in fetch.stmts if getattr(s, "src", 0) != 0x1020]
    b = bind.Binder(a, ticks=4)
    b.roles()
    assert b.clockbase is None and b.clockcell == "timer"
    assert "rowsleft" not in b.slots


def test_a_record_no_section_five_form_states_refuses_and_names_its_own_cell():
    a = art()
    a["t1"]["accs"].append(
        {
            "id": "a1",
            "cell": {"addr": "$3000", "region": 10, "copies": VOICES, "name": "elsewhere"},
            "regions": [10],
            "width": 8,
            "target": {"register": "pw_lo"},
            "policy": "free",
            "scope": "voice",
            "sites": ["$1068"],
            "delta": {"kind": "const", "value": 2},
        }
    )
    a["t1"]["accs"].append(
        {
            "id": "a2",
            "cell": {"addr": "$%04X" % SWEEP, "region": 10, "copies": VOICES, "name": "sweep"},
            "regions": [10],
            "width": 8,
            "target": {"register": "pw_lo"},
            "policy": "free",
            "scope": "voice",
            "sites": ["$1068"],
            "delta": {"kind": "elsewhere"},
        }
    )
    obj, report = bind.lift(a, ticks=TICKS)
    assert obj["accs"]["a1"]["cell"] == "a1"  # no region names the address it moves
    assert "a2" not in obj["accs"]
    assert [r["cell"] for r in report["refusals"]] == ["sweep"]
    assert report["refusals"][0]["detail"] == "T1's delta is no section 5 form"


def test_a_join_no_path_folds_is_a_cell_of_the_object_and_a_refusal():
    a = art()
    blocks = a["prog"].procs["tick"].blocks
    scratch = ram(GLOB, 11, size=1)
    blocks["out"] = Block("out", [], If(Bin("!=", scratch, C(0)), "ob", "oc"), src=0x1088)
    blocks["ob"] = Block("ob", [], Goto("oe"), src=0x108C)
    blocks["oc"] = Block("oc", [], If(Bin("==", scratch, C(1)), "od", "oe"), src=0x1090)
    blocks["od"] = Block("od", [], Goto("of"), src=0x1094)
    blocks["oe"] = Block("oe", [store(GLOB, 11, C(1), src=0x10A0, size=1)], Goto("of"), src=0x1098)
    blocks["of"] = Block("of", [], Return(vals=[]), src=0x109C)
    _obj, report = bind.lift(a, ticks=4)
    assert [r["why"] for r in report["refusals"]] == ["unclassified update"]
    assert report["refusals"][0]["cell"] == "joe,jof"
    assert report["refusals"][0]["detail"] == "a join no path folds"
