"""T3: the universal player renders a lifted tune tick for tick, and certifies it."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import certify, emit, lift, player  # noqa: E402
from deity_informant.trackerprog.refuse import Refusal  # noqa: E402
from deity_informant.tuneprog import pipeline, provenance  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402

from _prog import tuneprog  # noqa: E402
from test_score import CERT, TUNE, blocks  # noqa: E402


def t3(code=TUNE, calls=64, cert=CERT):
    trace, prog = tuneprog(code, calls=calls, s4=True, blocks=blocks())
    view, st, names = pipeline.present(prog)
    t0 = provenance.document(view, st, names)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls, obs=True)
    t2 = lift.document(view, names, hist, cert)
    tp, refusals = emit.document(view, names, t0, None, t2, hist, cert, ver.obs)
    return tp, refusals, ver, view


def test_the_snippet_lifts_with_no_residue_and_renders_its_observable_exactly():
    tp, refusals, ver, _view = t3()
    assert refusals == []
    (voice,) = tp["score"]["voices"]
    assert voice["order"] and all(r["dur"] >= 1 for p in voice["patterns"].values() for r in p)
    notes = [r["note"] for p in voice["patterns"].values() for r in p]
    assert {n for n in notes if n is not None} <= {12, 14, 16, 24}
    got = player.Player(tp).render(len(ver.obs))
    assert certify.divergence(ver.obs, got) is None


def test_the_certificate_binds_the_source_and_states_both_halves():
    tp, refusals, ver, _view = t3()
    got = player.Player(tp).render(len(ver.obs))
    doc = certify.certificate("snippet", CERT, ver.obs, got, refusals, tp["score"]["end"])
    assert doc["divergence"] is None and doc["emitted"] and doc["ticks"] == 64
    assert doc["compared"] and doc["dropped"] and doc["loop"]["period"] == 40
    assert doc["end"]["kind"] == "loop"
    assert json.loads(json.dumps(doc)) == doc


def test_a_refusal_means_no_emit_but_a_stated_render():
    tp, _refusals, ver, _view = t3()
    got = player.Player(tp).render(len(ver.obs))
    bad = [Refusal("command residue", "sid[0].ctrl = x", "$1000")]
    doc = certify.certificate("snippet", CERT, ver.obs, got, bad, tp["score"]["end"])
    assert not doc["emitted"] and doc["divergence"] is None and doc["refusals"][0]["cell"]
    assert doc["rendered"]["ticks_equal"] == 64


def test_a_divergence_names_its_tick_and_register():
    tp, _refusals, ver, _view = t3()
    got = player.Player(tp).render(len(ver.obs))
    wrong = list(got)
    wrong[5] = got[5]._replace(values=tuple(v if v is None else v + 1 for v in got[5].values))
    d = certify.divergence(ver.obs, wrong)
    assert d["tick"] == 5 and d["register"].startswith("value")
    short = certify.divergence(ver.obs, got[:10])
    assert short["register"] == "horizon" and short["tick"] == 10


def test_the_print_measures_and_round_trips():
    tp, _refusals, _ver, _view = t3()
    md = emit.render(tp)
    n = emit.numbers(tp, md)
    assert set(n) >= {"tokens", "lines", "statements", "blocks", "header_rows", "data_rows", "xz"}
    assert n["statements"] > 0 and n["tokens"] > n["lines"] > 0 and n["xz"] > 0
    assert emit.from_json(json.loads(json.dumps(emit.to_json(tp)))) == tp


def test_the_player_s_accumulator_policies_stay_bounded():
    a = player.Acc("freq", 8, 3, 0, 7, "reflect")
    seen = [a.step() or a.value for _ in range(12)]
    assert max(seen) <= 7 and min(seen) >= 0 and seen[:4] == [3, 6, 3, 0]
    c = player.Acc("freq", 8, 5, 0, 12, "clamp")
    for _ in range(5):
        c.step()
    assert c.value == 12
    h = player.Acc("freq", 8, 5, 0, 12, "halt")
    for _ in range(5):
        h.step()
    assert h.value == 10
    w = player.Acc("freq", 8, 200, 0, 255, "wrap", rate=2)
    for _ in range(4):
        w.step()
    assert w.value == (400 & 0xFF)
