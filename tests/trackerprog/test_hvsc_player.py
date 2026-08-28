"""T3 on the exemplars (marked ``hvsc``; short horizons): certificates that name their residue."""

import json
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog.refuse import REASONS  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402

import tuneprog_trackerprog as T3  # noqa: E402
from _hvsc import COMMANDO, EMOMYST, GULDKORN, LINUS, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc
_T3 = {}


def exemplar(rel, calls=1200):
    if rel not in _T3:
        out = Path(mkdtemp()) / "t3"
        assert pipeline.main([str(tune_file(rel)), "--out", str(out), "--calls", str(calls)]) == 0
        doc, tp, refusals, numbers, _secs = T3.run(out, calls)
        _T3[rel] = doc, tp, refusals, numbers, out
    return _T3[rel]


@pytest.mark.parametrize("rel", (LINUS, GULDKORN, COMMANDO, EMOMYST))
def test_every_residue_is_a_named_refusal_and_nothing_partial_is_emitted(rel):
    doc, _tp, refusals, numbers, out = exemplar(rel)
    assert refusals and not doc["emitted"] and doc["divergence"] is None
    for r in doc["refusals"]:
        assert r["why"] in REASONS and r["cell"]
    assert doc["compared"] and doc["dropped"] and doc["ticks"] == 1200
    assert 0 <= doc["rendered"]["ticks_equal"] <= doc["ticks"]
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
    assert (
        not (out / "trackerprog.json").exists() and (out / "trackerprog.certificate.json").exists()
    )
    assert json.loads((out / "trackerprog.certificate.json").read_text())["refusals"]


def test_sid_wizard_s_score_refusal_reaches_the_certificate():
    doc, _tp, _refusals, _numbers, _out = exemplar(EMOMYST)
    assert any(
        r["why"] == "score not cursor-shaped" and r["site"] == "p_17C8" for r in doc["refusals"]
    )


def test_the_residue_of_a_tracker_is_command_residue_on_its_own_write_sites():
    doc, _tp, _refusals, _numbers, _out = exemplar(GULDKORN)
    got = [r for r in doc["refusals"] if r["why"] == "command residue"]
    assert got and all(r["site"].startswith("$") for r in got)
