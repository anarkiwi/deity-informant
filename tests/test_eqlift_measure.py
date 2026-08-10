"""Hermetic tests for the eqlift ON/OFF measurement rollup (stage 3b).

The rollup is what the exemplar review reads, so its gate is the thing under test:
a fault, a refused proof, a regression and an unproved change each have to be
visible in the artifact rather than averaged away by the totals."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import eqlift_measure as M  # pylint: disable=wrong-import-position


def _row(tune, mode, lines=100, stores=10, sha="a", **kw):
    return {
        "tune": tune,
        "name": tune.rsplit("/", 1)[-1],
        "mode": mode,
        "lines": lines,
        "stores": stores,
        "sha": sha,
        "wall_s": 1.0,
        "sites": 50,
        "changed": 5,
        "proved": 50,
        "fallback": 0,
        **kw,
    }


def _pair(tune, **on):
    return [_row(tune, False), _row(tune, True, **on)]


def test_rollup_pairs_the_two_paths_and_totals_them():
    got = M.rollup(_pair("a/x", lines=98, stores=9, sha="b") + _pair("a/y"))
    assert got["clean"] and got["tunes"] == 2 and got["identical"] == 1
    assert [r["d_lines"] for r in got["rows"]] == [-2, 0]
    assert got["totals"] == {
        "off_lines": 200,
        "on_lines": 198,
        "d_lines": -2,
        "d_stores": -1,
        "extracted": 100,
        "changed": 10,
        "proved": 100,
    }


def test_rollup_gates_a_regression():
    got = M.rollup(_pair("a/x", lines=101))
    assert got["regressed"] == ["a/x"] and not got["clean"]


def test_rollup_gates_a_fault_and_a_missing_path():
    rows = _pair("a/x") + [_row("a/y", False, error="MemoryError: x"), _row("a/y", True)]
    got = M.rollup(rows + [_row("a/z", True)])
    assert got["faults"] == ["a/y", "a/z"] and got["tunes"] == 1 and not got["clean"]


def test_rollup_gates_a_refusal_and_an_unproved_change():
    got = M.rollup(_pair("a/x", refused="site environment is unsatisfiable"))
    assert got["refused"] == ["a/x"] and not got["clean"]
    got = M.rollup(_pair("a/y", proved=0))
    assert got["unproved"] == ["a/y"] and not got["clean"]


def test_rollup_reports_the_extraction_fallback():
    got = M.rollup(_pair("a/x", fallback=7))
    assert got["clean"] and got["fallback_tunes"] == ["a/x"]
    assert got["rows"][0]["on_fallback"] == 7 and got["rows"][0]["off_fallback"] == 0


def test_rollup_refuses_two_rows_for_one_path():
    with pytest.raises(ValueError):
        M.rollup(_pair("a/x") + [_row("a/x", True)])


def test_render_names_every_tune_and_the_gate():
    text = M.render(M.rollup(_pair("a/x", lines=98, sha="b") + _pair("a/y", lines=101)))
    assert "a/x" in text and "identical" not in text.split("a/x")[1].split("\n")[0]
    assert '"regressed": ["a/y"]' in text and '"clean": false' in text


def test_stores_counts_indexed_destinations_only():
    assert M.stores("a = $05\nm_1000[x] = a\nsid.v1.freq_lo = a\n") == 1
    assert M.model_name("MUSICIANS/D/Daf/Alioth") == "MUSICIANS~D~Daf~Alioth"
