"""B6/B7 on the exemplars (marked ``hvsc``): the lift's own object, rendered and certified."""

import json
import struct
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.lifter import lift as _lift  # noqa: E402
from deity_informant.trackerprog import assemble, build  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

from _hvsc import COMMANDO, EMOMYST, GULDKORN, LINUS, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc
CALLS = 1200
GT2_LOOPS = 2  # the two runs of calls the tick's own voice loop is
GT2_REFUSED = 11  # the SMC patch sites, the reset's own copies and the entry carry
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


def test_commando_lifts_to_an_object_the_universal_player_renders_with_no_divergence():
    obj, report = assemble.lift(artefacts(COMMANDO), ticks=CALLS)
    assert "program" not in obj and set(obj) >= {"meta", "pitch", "streams", "accs", "score"}
    doc = attest(obj, reference(COMMANDO, int(obj["meta"]["song"] or 0), CALLS))
    assert doc["divergence"] is None and doc["ticks"] == CALLS
    assert doc["same_per_register_order"]
    assert report["schedule"]["tick"] == ["prelude", "commit", "row", "commit", "machine"]
    assert report["schedule"]["voice_order"] == [2, 1, 0]
    assert report["schedule"]["commit_order"] == ["ctrl", "ad", "sr"]
    assert report["schedule"]["tempo.rate"] == 3 and report["schedule"]["row_consumes_tick"]


def test_the_lifted_object_states_the_tuning_and_the_score_the_hand_reading_does():
    obj, _report = assemble.lift(artefacts(COMMANDO), ticks=CALLS)
    assert obj["pitch"]["base"] == 16 and len(obj["pitch"]["freq"]) == 80
    assert len(obj["instruments"]) == 13
    assert [len(o["play"]) for o in obj["score"]["orders"]] == [5, 4, 13]
    assert all(e["arm"]["rows"] for p in obj["score"]["patterns"].values() for e in p["events"])


def test_the_coverage_names_what_the_lowering_did_not_recognise():
    _obj, report = assemble.lift(artefacts(COMMANDO), ticks=CALLS)
    cov = report["coverage"]
    assert cov["store_sites"] == 76 and cov["accs"] == 3
    assert cov["refused"] == ["$5023", "$502C"]
    assert {"cell", "ins", "pitch", "global"} <= set(cov["leaves"])


def test_t1s_plane_is_the_joins_input_and_this_prefix_states_none_of_it():
    """T1 verifies a recurrence over a horizon, and 1,200 calls is not one.

    Over this prefix T1 refuses both, so the join has nothing to join: the
    recognition runs hermetically in ``test_recognise.py`` and over the whole
    horizon under ``tools/tuneprog_trackerprog.py``.
    """
    art = artefacts(COMMANDO)
    assert art["t1"]["accs"] == []
    assert {r["why"] for r in art["t1"]["refusals"]} == {"divergent recurrence"}
    _obj, report = assemble.lift(art, ticks=CALLS)
    assert report["coverage"]["t1_accumulators"] == []
    assert report["coverage"]["t1_recognised"] == 0 and report["coverage"]["t1_refused"] == []


def test_jch_derives_the_row_clock_of_section_3_6_and_renders_it_with_no_divergence():
    """The second family (prototype-lifter.md section 2.1).

    B6's schedule is derived and agrees with the hand tool's datum for datum but
    four; the clock is section 3.6's general counter -- one the tick steps outside
    the voice loop, with a boundary of three terms and a reset clause -- and the
    fetch's own byte loop supplies one constant a turn, which is what the object
    renders with no divergence.
    """
    obj, report = assemble.lift(artefacts(GULDKORN), ticks=CALLS)
    sch = report["schedule"]
    assert sch["voice_order"] == [2, 1, 0] and sch["commit_order"] == ["ad", "sr", "ctrl"]
    assert sch["tempo.step"] == -1 and sch["tempo.rate"] == 1
    assert sch["tempo.resets"] == 1 and sch["tempo.boundary_terms"] == 3
    assert obj["meta"]["tempo"]["reset"][0]["sets"][0][1] == 3  # the tune's own speed
    assert report["coverage"]["refused"] == ["V#1"]
    # the fetch's loop turns up to three times a visit and each turn reads its own cell
    assert report["trips"]["L113D_BC"] == 3
    sets = {
        s[0]
        for p in obj["score"]["patterns"].values()
        for e in p["events"]
        for s in e["arm"]["rows"][0]["sets"]
    }
    assert {"@t_saved8__0", "@t_saved8__1", "@t_saved8__2"} <= sets
    doc = attest(obj, reference(GULDKORN, int(obj["meta"]["song"] or 0), CALLS))
    assert doc["divergence"] is None and doc["ticks"] == CALLS


def test_gt2_inlines_the_ticks_callees_and_writes_through_the_register_file_it_flushes():
    """The third family (prototype-lifter.md section 2.5).

    Its tick calls one procedure once a voice, so the voice loop is that run of
    calls rerolled; every write lands in a register file the tick's first act
    flushes, and the first call runs the reset and spends its own tick.
    """
    obj, report = assemble.lift(artefacts(LINUS), ticks=CALLS)
    assert report["inlined"] == GT2_LOOPS
    sch = report["schedule"]
    assert sch["voice_order"] == [0, 1, 2] and sch["commit_order"] == ["ad", "sr", "ctrl"]
    assert sch["tempo.step"] == -1 and sch["tempo.boundary_terms"] == 3
    regs = obj["meta"]["shadow"]["registers"]
    assert len(regs) == 25 and regs[0] == "mode_vol" and regs[-1] == "v0.freq_lo"
    assert obj["state0"]["prologue"]["rows"] and len(obj["state0"]["shadow"]) == 25
    assert len(report["coverage"]["refused"]) == GT2_REFUSED


def test_the_fourth_family_refuses_with_a_named_datum_rather_than_approximating():
    with pytest.raises(assemble.Refused) as x:
        assemble.lift(artefacts(EMOMYST), ticks=CALLS)
    assert x.value.refusals and all(r.why and r.detail for r in x.value.refusals)
