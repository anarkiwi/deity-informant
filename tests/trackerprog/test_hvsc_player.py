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
def test_the_trackers_and_hubbard_certify_on_the_universal_player(rel):
    doc, tp, refusals, numbers, out = exemplar(rel)
    assert refusals == [] and doc["emitted"] and doc["divergence"] is None
    assert doc["rendered"]["ticks_equal"] == doc["ticks"] == 1200
    assert (
        doc["compared"]
        and doc["dropped"]
        and doc["end"]["kind"] in ("loop", "fixed_point", "horizon")
    )
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
    assert (out / "trackerprog.json").exists() and (out / "trackerprog.md").exists()
    assert len(tp["score"]["voices"]) == 3 and tp["streams"] and tp["score"]["global"]


def test_a_refusal_keeps_the_certificate_and_withholds_the_object(tmp_path):
    doc, _tp, _refusals, _numbers, out = exemplar(EMOMYST)
    cert = json.loads((out / "trackerprog.certificate.json").read_text())
    assert cert["emitted"] and all(r["why"] in REASONS for r in cert["refusals"])
    assert doc["source"]["tune"] and tmp_path
