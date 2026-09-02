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
    assert cov["refused"] == ["$5023", "$5026", "$5029", "$502C"]
    assert all(a["form"] in ("acc", "sets") for a in cov["t1_accumulators"])
    assert {"cell", "ins", "pitch", "global"} <= set(cov["leaves"])


@pytest.mark.parametrize("rel", (LINUS, GULDKORN, EMOMYST))
def test_the_other_families_refuse_with_a_named_datum_rather_than_approximating(rel):
    with pytest.raises(assemble.Refused) as x:
        assemble.lift(artefacts(rel), ticks=CALLS)
    assert x.value.refusals and all(r.why and r.detail for r in x.value.refusals)
