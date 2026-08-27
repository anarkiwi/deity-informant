"""T0 on the tracker exemplars (marked ``hvsc``; short horizons).

Every SID write site of GT2, JCH V20 and SID Wizard is one record: a named
register or a stated refusal, and a ``print`` that is a line of the tune's own
``tuneprog.md``. The pipeline writes the document beside S6.
"""

import json
from collections import Counter

import pytest

from deity_informant import cli
from deity_informant.tuneprog import pipeline, printer, provenance
from deity_informant.tuneprog.facts import GLOBAL_REG, VOICE_REG

from _hvsc import EMOMYST, GULDKORN, LINUS, decompiled, tune

pytestmark = pytest.mark.hvsc

REGS = set(VOICE_REG) | set(GLOBAL_REG.values()) | {"freq", "pw", "cutoff"}
_T0 = {}


def t0(relpath, seconds=30):
    """``(the T0 document, the rendered tuneprog)`` of one exemplar, once."""
    if relpath not in _T0:
        run = decompiled(relpath, seconds=seconds, text=False)
        view, st, names = pipeline.present(run.prog)
        _T0[relpath] = provenance.document(view, st, names), printer.render(view, st, names)
    return _T0[relpath]


def counts(doc):
    """``(sites, direct, image, refusals by reason)`` of one document."""
    w = doc["writes"]
    return (
        len(w),
        sum(r["direct"] for r in w),
        sum(not r["direct"] for r in w),
        Counter(r["refusal"]["why"] for r in w if r["refusal"]),
    )


@pytest.mark.parametrize("rel", (LINUS, GULDKORN, EMOMYST))
def test_every_write_site_is_a_named_register_or_a_stated_refusal(rel):
    doc, text = t0(rel)
    lines = {l.strip() for l in text.splitlines()}
    sites, direct, image, refused = counts(doc)
    assert sites and direct + image == sites
    for r in doc["writes"]:
        assert r["print"] in lines, r["print"]  # the record re-renders to its own line
        named = r["register"] in REGS or r["kind"] == "file"
        assert named or r["refusal"] is not None, r
        assert set(r["voices"]) <= {0, 1, 2} and r["envelope"][0] >= "$D400"
        assert r["site"]["pc"] and r["site"]["proc"] and r["site"]["width"] in (1, 2)
    assert set(refused) <= set(provenance.REFUSALS)


def test_the_ghost_image_carries_goattracker_s_provenance():
    doc, _text = t0(LINUS)
    flush = [r for r in doc["writes"] if r["kind"] == "file"]
    assert len(flush) == 1 and flush[0]["direct"] and flush[0]["register"] is None
    image = [r for r in doc["writes"] if not r["direct"]]
    assert len(doc["image"]) == 1 and len(image) > 15
    assert all(r["image"]["flush_pc"] == flush[0]["site"]["pc"] for r in image)
    # the registers the ghost's own cells are, and the recurrences T1 inherits
    assert {r["register"] for r in image} >= {"freq", "ctrl", "ad", "sr", "pw_lo", "pw_hi"}
    assert sum(r["self_update"] for r in image) >= 4


def test_the_direct_players_write_every_voice_register_by_voice():
    for rel in (GULDKORN, EMOMYST):
        doc, _text = t0(rel)
        by = {r["register"]: r for r in doc["writes"] if r["direct"]}
        assert {"ad", "sr", "ctrl"} <= set(by) and REGS >= set(by) - {None}
        voiced = [r for r in doc["writes"] if r["voices"] == [0, 1, 2]]
        assert len(voiced) >= 5 and not [r for r in doc["writes"] if not r["direct"]]
    # a 16-bit register the fold made one statement is one record, at its pair's name
    assert {"freq", "pw"} <= {r["register"] for r in t0(GULDKORN)[0]["writes"]}


def test_the_pipeline_writes_the_document_beside_s6(tmp_path):
    sid = tmp_path / "Guldkornekspressen_Intro.sid"
    sid.write_bytes(tune(GULDKORN))
    out = tmp_path / "out"
    assert cli.main(["tuneprog", str(sid), "--out", str(out), "--seconds", "5"]) == 0
    doc = json.loads((out / "tuneprog.T0.json").read_text())
    lines = {l.strip() for l in (out / "tuneprog.md").read_text().splitlines()}
    assert doc["plane"] == "S6-view" and doc["voice_map"] and doc["writes"]
    assert all(r["print"] in lines for r in doc["writes"])
    assert {r["register"] for r in doc["writes"]} - {None} <= REGS
