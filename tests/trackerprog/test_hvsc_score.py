"""T2 on the exemplars (marked ``hvsc``; short horizons): the cursor grammar's goldens."""

import json
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import lift  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.recover import Names  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402

from _hvsc import COMMANDO, EMOMYST, GULDKORN, LINUS, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc
_T2 = {}


def exemplar(rel, calls=1200):
    """The T2 document of one HVSC exemplar through ``pipeline.main``, once."""
    if rel not in _T2:
        out = Path(mkdtemp()) / "t2"
        assert pipeline.main([str(tune_file(rel)), "--out", str(out), "--calls", str(calls)]) == 0
        prog = Tuneprog.load(out / "tuneprog.S4.json")
        s6 = json.loads((out / "tuneprog.S6.json").read_text())
        cert = json.loads((out / "certificate.json").read_text())
        regions = json.loads((out / "regions.json").read_text())
        hist, ver = history(prog, Trace.load(out), s6, calls=calls, regions_doc=regions)
        assert ver.div is None
        _T2[rel] = lift.document(pipeline.present(prog)[0], Names.from_dict(s6), hist, cert)
    return _T2[rel]


def by_cursor(doc, kind):
    return {s["cursor"].split("@")[0]: s for s in doc[kind]}


def channels(doc, role):
    return [ch for v in doc["score"] for ch in v.get(role, ())]


def test_goattracker_s_score_is_a_pattern_pointer_table_over_an_orderlist():
    doc = exemplar(LINUS)
    assert doc["refusals"] == [] and doc["pitch"]["layout"] == "lo|hi"
    sel = by_cursor(doc, "selectors")
    assert len(sel["cursor_148C"]["columns"]) == 2  # the pattern pointers, lo and hi
    assert len(sel["cursor_1490"]["columns"]) == 9  # the instrument columns
    assert any("T16F9" in {c["table"] for c in s["columns"]} for s in doc["streams"])
    order, pattern = channels(doc, "order"), channels(doc, "pattern")
    assert len(order) == 3 and len(pattern) == 3
    # the orderlist's own end lies past a 1,200-tick horizon: its loop is untraced
    assert {ch["terminator"] for ch in order} <= {None, 0xFF}
    assert {ch["terminator"] for ch in pattern} == {0}
    assert all(ch["depth"] == 1 and ch["unresolved_ticks"] == 0 for ch in order + pattern)


def test_jch_s_score_walks_a_pointer_pair_into_patterns_ended_by_7f():
    doc = exemplar(GULDKORN)
    assert doc["refusals"] == [] and doc["pitch"]["layout"] == "u16le"
    ins = by_cursor(doc, "selectors")["b1014"]
    assert len(ins["columns"]) == 8 and ins["columns"][0]["stride"] == 8  # rec8
    order, pattern = channels(doc, "order"), channels(doc, "pattern")
    assert len(order) == 3 and {ch["terminator"] for ch in order} <= {None, 0xFF}
    assert len(pattern) == 3 and {ch["terminator"] for ch in pattern} == {0x7F}
    assert {ch["depth"] for ch in pattern} == {2} and all(ch["pointers"] for ch in pattern)
    assert len({ch["pointers"]["entries"] for ch in pattern}) == 1
    assert all(ch["unresolved_ticks"] == 0 for ch in order + pattern)


def test_hubbard_s_score_is_an_orderlist_of_pattern_pointers_and_a_record_table():
    doc = exemplar(COMMANDO)
    assert doc["refusals"] == [] and doc["pitch"]["layout"] == "u16le"
    rec = by_cursor(doc, "selectors")["cursor_54FE"]
    assert len(rec["columns"]) == 6 and rec["shift"] == 3
    order, pattern = channels(doc, "order"), channels(doc, "pattern")
    assert {ch["table"] for ch in order} == {"T576B"} and {ch["table"] for ch in pattern} == {
        "T5889"
    }
    assert {ch["terminator"] for ch in pattern} == {0xFF} and {ch["depth"] for ch in pattern} == {2}


def test_sid_wizard_s_erased_orderlist_load_is_a_named_refusal():
    doc = exemplar(EMOMYST)
    got = [r for r in doc["refusals"] if r["site"] == "p_17C8"]
    assert got and {r["why"] for r in got} == {"score not cursor-shaped"}
    assert all(r["cell"] for r in doc["refusals"])


def test_every_event_accounts_for_the_whole_horizon():
    for rel in (LINUS, GULDKORN, COMMANDO):
        doc = exemplar(rel)
        for ch in channels(doc, "order") + channels(doc, "pattern"):
            assert sum(e["ticks"] for e in ch["events"]) == doc["horizon"]["ticks"]
