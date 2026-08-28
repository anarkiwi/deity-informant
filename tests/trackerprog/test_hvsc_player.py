"""T3 on the exemplars (marked ``hvsc``; short horizons): lifted from their data, certified."""

import json
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import certify, emit  # noqa: E402
from deity_informant.trackerprog.refuse import REASONS  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402

import tuneprog_trackerprog as T3  # noqa: E402
from _hvsc import COMMANDO, EMOMYST, GULDKORN, LINUS, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc
_T3 = {}
INSTRUMENTS = {LINUS: 30, GULDKORN: 19, COMMANDO: 13, EMOMYST: 11}


def exemplar(rel, calls=1200):
    if rel not in _T3:
        out = Path(mkdtemp()) / "t3"
        assert pipeline.main([str(tune_file(rel)), "--out", str(out), "--calls", str(calls)]) == 0
        doc, tp, refusals, numbers, _secs, snd = T3.run(out, calls)
        _T3[rel] = doc, tp, refusals, numbers, out, snd
    return _T3[rel][:5]


@pytest.mark.parametrize("rel", (LINUS, GULDKORN, COMMANDO, EMOMYST))
def test_a_derived_fetch_renders_exactly_and_a_walked_one_refuses_by_name(rel):
    doc, tp, refusals, numbers, out = exemplar(rel)
    walked = [r for r in refusals if r.why == "fetch not in IR" and "#" not in r.cell]
    if walked:
        assert doc["trap"]["trap"] == "fetch not in IR" and doc["divergence"]
    else:
        assert doc["divergence"] is None and doc["trap"] is None
        assert doc["rendered"]["ticks_equal"] == doc["ticks"] == 1200
    # the sound half still names SSA temps and addresses: refused by name, not emitted
    residue = [r for r in refusals if r.why == "program residue"]
    assert residue and all(r.cell.split("/")[0] in ("producers", "accs") for r in residue)
    assert not doc["emitted"] and not (out / "trackerprog.json").exists()
    assert doc["compared"] and doc["dropped"]
    assert doc["end"]["kind"] in ("loop", "fixed_point", "horizon")
    assert {"tokens", "lines", "statements", "blocks", "header_rows", "data_rows", "xz"} <= set(
        numbers
    )
    assert set(numbers["tuneprog"]) >= {
        "tokens",
        "lines",
        "statements",
        "blocks",
        "data_rows",
        "xz",
    }
    assert tp["score"]["regions"] and (walked or len(tp["score"]["voices"]) == 3)
    assert all(v["order"] and v["patterns"] for v in tp["score"]["voices"])


@pytest.mark.parametrize("rel", (LINUS, GULDKORN, COMMANDO, EMOMYST))
def test_the_instruments_are_the_program_s_table_and_the_rows_are_bytes(rel):
    _doc, tp, _refusals, _numbers, _out = exemplar(rel)
    ins = tp["instruments"]
    # a 1,200-tick trace reaches part of the table; the full horizons reach all of it
    assert 0 < ins["used"] <= ins["entries"] <= INSTRUMENTS[rel]
    assert len(ins["rows"]) == ins["entries"]
    assert all(any(r["bytes"] for r in v["rows"]) for v in tp["score"]["voices"])
    assert all(set(r) == {"dur", "bytes", "at"} for v in tp["score"]["voices"] for r in v["rows"])
    assert tp["streams"] and tp["producers"] and tp["score"]["fetch"]
    assert all(p["register"] or p["kind"] == "file" for p in tp["producers"])


def test_hubbard_s_fetch_derives_whole_and_its_patterns_are_reused():
    _doc, tp, _refusals, _numbers, _out = exemplar(COMMANDO)
    prints = [p["print"] for f in tp["score"]["fetch"] for p in f["producers"]]
    assert (
        any(p.endswith("= (byte[0] & $1F)") for p in prints) and "ptr_2 = T5712[T576B[0]]" in prints
    )
    assert not any(f["refusals"] for f in tp["score"]["fetch"])
    for v in tp["score"]["voices"]:
        assert 1 < len(v["patterns"]) < len(v["order"]) < len(v["rows"])
    assert "   12 13 | 83 07 58 03" in emit.render(tp)


def test_accumulators_annotate_the_producers_that_step_them():
    _doc, tp, _refusals, _numbers, _out = exemplar(GULDKORN)
    assert tp["accs"] and any(p["accs"] for p in tp["producers"])


def test_the_certificate_names_its_refusals_by_reason(tmp_path):
    doc, _tp, _refusals, _numbers, out = exemplar(EMOMYST)
    cert = json.loads((out / "trackerprog.certificate.json").read_text())
    assert not cert["emitted"] and all(r["why"] in REASONS for r in cert["refusals"])
    assert all(r["cell"] and r["detail"] for r in cert["refusals"])
    assert doc["source"]["tune"] and tmp_path


def test_the_emitted_object_carries_no_program_and_the_universal_player_matches_the_oracle():
    _doc, tp, _refusals, _numbers, out = exemplar(COMMANDO)
    snd = _T3[COMMANDO][5]
    assert "program" not in tp and "sound" not in tp and snd["items"]
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    want, _trap = emit.oracle(prog, tp, 300)
    got, trap = emit.replay(tp, snd, 300)
    assert trap is None and certify.divergence(want, got) is None
