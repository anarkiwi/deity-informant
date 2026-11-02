"""The di-vtools image end to end: GoatTracker ground truth, sidreloc and SIDId agree on it.

Skips unless the image exists (``python tools/vtools.py build``), or ``DI_VTOOLS_BUILD=1`` lets
the session build it.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# pylint: disable=wrong-import-position
import gtruth  # noqa: E402
import sidid  # noqa: E402
import sidreloc  # noqa: E402
import vtools  # noqa: E402

SONGS = [f"{gtruth.EXAMPLES}/{s}.sng" for s in ("consultant", "dojo")]


@pytest.fixture(scope="module", name="fixtures")
def fixture_fixtures():
    """Truth records of two bundled songs in the base and relocated variants."""
    if not vtools.available() and os.environ.get("DI_VTOOLS_BUILD") == "1":
        vtools.build()
    if not vtools.available():
        pytest.skip("di-vtools image not built")
    out = vtools.workdir("test-vtools")
    try:
        summary = gtruth.produce(out, SONGS)
        yield out, summary
    finally:
        shutil.rmtree(out)


def _truth(out, variant, song):
    return gtruth.json.loads((out / variant / f"{song}.truth.json").read_text())


def test_listing_matches_image(fixtures):
    out, summary = fixtures
    assert len(summary) == 4 and not any(s["mismatches"] for s in summary.values())
    for s in summary.values():
        assert s["regions"]["addr_table"] == 2 and s["regions"]["records"] >= 4
    rec = _truth(out, "base", "consultant")
    rows = {tuple(r["labels"]): r for r in rec["regions"] if r["kind"] != "parallel"}
    song = rows[("mt_songtbllo",)]
    assert song["hi_labels"] == ["mt_songtblhi"] and song["count"] == 3
    assert all(rec["symbols"][f"mt_song{i}"]["value"] == t for i, t in enumerate(song["targets"]))


def test_relocated_player_same_layout(fixtures):
    out, _ = fixtures
    for song in ("consultant", "dojo"):
        a, b = _truth(out, "base", song), _truth(out, "w2000", song)
        d = b["load"] - a["load"]
        assert d == 0x1000 and a["size"] == b["size"]
        for x, y in zip(a["regions"], b["regions"], strict=True):
            assert (x["kind"], x["labels"], x["size"]) == (y["kind"], y["labels"], y["size"])
            assert y["addr"] - x["addr"] == d


def test_sidreloc_high_bytes_are_truth(fixtures):
    out, _ = fixtures
    rec = _truth(out, "base", "consultant")
    lo, hi = rec["load"] & 0xFF00, (rec["load"] + rec["size"] - 1) | 0xFF
    truth = {
        r["at"] + (r["role"] == "addr")
        for r in rec["refs"]
        if (r["role"] == "hi" or (r["role"] == "addr" and r["size"] == 2))
        and lo <= r["target"] <= hi
    }
    rep = sidreloc.relocate(out / "base" / "consultant.sid", pages=0x10, frames=3000)
    assert rep["error"] == "ok" and not rep["disagree"]
    found = set(rep["roles"]["hi"])
    assert found <= truth
    unused = set(rep["roles"]["unused"]) | set(rep["roles"]["undetermined"])
    assert truth - found <= unused


def test_sidid_names_goattracker(fixtures):
    out, _ = fixtures
    got = sidid.identify(sorted(out.glob("*/*.sid")), progress=False)
    assert len(got) == 4 and all(v[:1] == ["GoatTracker_V2.x"] for v in got.values())
