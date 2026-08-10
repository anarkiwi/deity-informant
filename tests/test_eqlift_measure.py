"""Hermetic tests for the eqlift measurement rollup (stage 3b, re-based at stage 4).

The rollup is what the exemplar review reads, so its gate is the thing under test: a
fault, a refused proof, growth against the recorded baseline and an unproved change
must each be visible in the artifact rather than averaged away by the totals."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import eqlift_measure as M  # pylint: disable=wrong-import-position


def _row(tune, lines=100, stores=10, sha="a", **kw):
    return {
        "tune": tune,
        "name": tune.rsplit("/", 1)[-1],
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


def _base(*rows):
    return {r["tune"]: r for r in rows}


def test_rollup_diffs_the_baseline_and_totals_the_run():
    got = M.rollup(
        [_row("a/x", lines=98, stores=9, sha="b"), _row("a/y")],
        _base(_row("a/x"), _row("a/y")),
    )
    assert got["clean"] and got["tunes"] == 2 and got["identical"] == 1
    assert [r["d_lines"] for r in got["rows"]] == [-2, 0]
    assert got["totals"] == {
        "lines": 198,
        "stores": 19,
        "d_lines": -2,
        "d_stores": -1,
        "extracted": 100,
        "changed": 10,
        "proved": 100,
    }


def test_a_run_with_no_baseline_reports_zero_deltas_and_no_identity():
    got = M.rollup([_row("a/x")])
    assert got["clean"] and not got["baseline"] and got["identical"] == 0
    assert got["rows"][0]["d_lines"] == 0 and got["rows"][0]["d_stores"] == 0


def test_rollup_gates_growth_against_the_baseline():
    got = M.rollup([_row("a/x", lines=101)], _base(_row("a/x")))
    assert got["regressed"] == ["a/x"] and not got["clean"]


def test_rollup_gates_a_fault():
    got = M.rollup([_row("a/x"), _row("a/y", error="MemoryError: x")])
    assert got["faults"] == ["a/y"] and got["tunes"] == 1 and not got["clean"]


def test_rollup_gates_a_refusal_and_an_unproved_change():
    got = M.rollup([_row("a/x", refused="site environment is unsatisfiable")])
    assert got["refused"] == ["a/x"] and not got["clean"]
    got = M.rollup([_row("a/y", proved=0)])
    assert got["unproved"] == ["a/y"] and not got["clean"]


def test_rollup_reports_the_extraction_fallback():
    got = M.rollup([_row("a/x", fallback=7)])
    assert got["clean"] and got["fallback_tunes"] == ["a/x"]
    assert got["rows"][0]["fallback"] == 7


def test_render_names_every_tune_and_the_gate():
    base = _base(_row("a/x"), _row("a/y"))
    text = M.render(M.rollup([_row("a/x", lines=98, sha="b"), _row("a/y", lines=101)], base))
    assert "a/x" in text and "identical" not in text.split("a/x")[1].split("\n")[0]
    assert '"regressed": ["a/y"]' in text and '"clean": false' in text


def test_baseline_rows_skip_a_faulted_tune(tmp_path):
    art = tmp_path / "prev.json"
    art.write_text('{"rows": [{"tune": "a/x", "sha": "a"}, {"tune": "a/y", "error": "x"}]}')
    assert set(M.baseline_rows(str(art))) == {"a/x"}
    assert M.baseline_rows(None) == {}


def test_stores_counts_indexed_destinations_only():
    assert M.stores("a = $05\nm_1000[x] = a\nsid.v1.freq_lo = a\n") == 1
    assert M.model_name("MUSICIANS/D/Daf/Alioth") == "MUSICIANS~D~Daf~Alioth"
