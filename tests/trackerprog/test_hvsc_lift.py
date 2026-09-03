"""B6/B7 on the exemplars (marked ``hvsc``): the binding's own object, rendered."""

import json
import struct
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.lifter import lift as _lift  # noqa: E402
from deity_informant.trackerprog import bind, build  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.refuse import Refused  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

from _hvsc import COMMANDO, EMOMYST, GULDKORN, LINUS, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc
CALLS = 1200
_ART = {}


def artefacts(rel, calls=CALLS):
    """The certified artefacts of one exemplar, and its T0/T1/T2 planes."""
    if rel not in _ART:
        out = Path(mkdtemp()) / "lift"
        assert pipeline.main([str(tune_file(rel)), "--out", str(out), "--calls", str(calls)]) == 0
        prog = Tuneprog.load(out / "tuneprog.S4.json")
        cert = json.loads((out / "certificate.json").read_text())
        _ART[rel] = build.artefacts(prog, Trace.load(out), cert, calls=calls)
    return _ART[rel]


def reference(rel, song, ticks):
    """The oracle: the tune's own player on the PcodeVM."""
    d = Path(tune_file(rel)).read_bytes()
    off, org = struct.unpack(">H", d[6:8])[0], struct.unpack(">H", d[8:10])[0]
    body = d[off:]
    if org == 0:
        org, body = body[0] | body[1] << 8, body[2:]
    m = bytearray(0x10000)
    m[org : org + len(body)] = body
    init, play = struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]
    vm, cache = PcodeVM(m), {}
    vm.reg[0] = song
    run_sub(vm, init, cache, _lift)
    out = []
    for _ in range(ticks):
        vm.wlog = []
        run_sub(vm, play, cache, _lift)
        out.append([(r, v) for _c, r, v in vm.wlog])
        vm.cycles += 19656
    return out


def test_commando_binds_to_an_object_with_no_program_and_the_schedule_b6_derives():
    obj, report = bind.lift(artefacts(COMMANDO), ticks=CALLS)
    assert "program" not in obj and set(obj) >= {"meta", "pitch", "streams", "accs", "score"}
    sch = report["schedule"]
    assert sch["tick"] == ["prelude", "commit", "row", "commit", "machine"]
    assert sch["commit_order"] == ["ctrl", "ad", "sr"]
    assert sch["tempo.step"] == -1 and sch["row_consumes_tick"]
    assert obj["meta"]["tempo"]["cell"] == "rowsleft"


def test_the_bound_object_states_the_tuning_the_records_and_the_score_by_field():
    obj, _report = bind.lift(artefacts(COMMANDO), ticks=CALLS)
    assert obj["pitch"]["base"] == 16 and len(obj["pitch"]["freq"]) == 80
    assert [len(o["play"]) for o in obj["score"]["orders"]] == [5, 4, 13]
    events = [e for p in obj["score"]["patterns"].values() for e in p["events"]]
    # the fields of section 3.6, not the bytes a fetch read
    assert {e["dur"] for e in events} and any(e["note"] is not None for e in events)
    assert any(e["ins"] is not None for e in events) and any(e["sounds"] for e in events)
    assert all(set(e) == {"dur", "sounds", "note", "gate", "tie", "ins", "arm"} for e in events)


def test_the_row_program_is_the_fetchs_own_steps_over_the_rows_facts():
    obj, _report = bind.lift(artefacts(COMMANDO), ticks=CALLS)
    prog = obj["meta"]["row"]
    assert {"ins": True} in prog and any("note" in s for s in prog)
    facts = json.dumps([s.get("when") for s in prog])
    assert '"sounds"' in facts and '"wraps"' in facts


def test_the_binding_carries_no_row_whose_sets_target_a_cell_named_by_an_address():
    obj, _report = bind.lift(artefacts(COMMANDO), ticks=CALLS)
    for st in obj["streams"].values():
        for row in st["rows"]:
            for target, _value in row.get("sets", ()):
                assert not target.lstrip("@#!*").startswith("$")


def test_t1s_plane_is_the_bindings_input_and_this_prefix_states_none_of_it():
    """T1 verifies a recurrence over a horizon, and 1,200 calls is not one.

    The binding renders what the planes state, so a prefix T1 states no record
    over is a prefix the object does not render: the certificate over the whole
    horizon is ``tools/tuneprog_trackerprog.py``'s, and the mechanisms are
    exercised hermetically in ``test_bind.py``.
    """
    art = artefacts(COMMANDO)
    assert art["t1"]["accs"] == []
    assert {r["why"] for r in art["t1"]["refusals"]} == {"divergent recurrence"}
    _obj, report = bind.lift(art, ticks=CALLS)
    assert report["coverage"]["t1_accumulators"] == 0
    assert report["coverage"]["accs"] == 0


def test_guldkorn_binds_its_schedule_and_names_the_field_its_score_does_not_state():
    """The second family: B6 derives, and the score's own fields do not bind.

    JCH stages its row -- the fetch keeps the bytes and a later phase commits
    them into the cell that indexes the tuning -- so the value the fetch stored
    into that cell, which is what section 3.6's ``note`` is bound from, is not
    there and every event states none.
    """
    obj, report = bind.lift(artefacts(GULDKORN), ticks=CALLS)
    sch = report["schedule"]
    assert sch["commit_order"] == ["ad", "sr", "ctrl"]
    assert sch["tempo.step"] == -1 and sch["tempo.resets"] == 1
    assert obj["meta"]["tempo"]["reset"][0]["sets"][0][1] == 3  # the tune's own speed
    events = [e for p in obj["score"]["patterns"].values() for e in p["events"]]
    assert events and not any(e["sounds"] or e["note"] is not None for e in events)


def test_the_families_whose_tick_is_several_procedures_refuse_by_name():
    for rel in (LINUS, EMOMYST):
        with pytest.raises(Refused) as x:
            bind.lift(artefacts(rel), ticks=CALLS)
        assert [r.why for r in x.value.refusals] == ["score not cursor-shaped"]
        assert x.value.refusals[0].detail == "the tick reaches no fetch region"


def test_the_certificate_is_the_render_against_the_tunes_own_player():
    obj, _report = bind.lift(artefacts(COMMANDO), ticks=CALLS)
    doc = attest(obj, reference(COMMANDO, int(obj["meta"]["song"] or 0), 8))
    assert doc["ticks"] == 8 and doc["writes"] > 0
