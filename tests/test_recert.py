"""The recert oracle table: what counts as a failure of an oracle run.

A green verdict has to mean the oracles ran. A certificate with no Ghidra export
and an emulator that errored are both failures of this table, not blank cells,
and only ``--known`` turns one into a recorded row.
"""

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# pylint: disable=wrong-import-position
import tuneprog_recert as R  # noqa: E402


def _gdir(tmp_path, name="cert"):
    """A Ghidra export directory that exists, with a ``stats.json`` in it."""
    g = tmp_path / name
    g.mkdir(parents=True)
    (g / "stats.json").write_text("{}")
    return g


def _doc(**emulate):
    return {
        "flags": [],
        "procs": [],
        "alignment": {"merged": []},
        "coverage": {"uncovered_sites": 0},
        "emulate": emulate,
    }


def test_a_certificate_with_no_export_is_a_failure(tmp_path):
    row = R.oracle_row("cert", tmp_path, tmp_path / "missing", 1.5)
    assert row["export"] is False and row["flags"] == ["export missing"]
    known = R.oracle_row("cert", tmp_path, tmp_path / "missing", 1.5, {"cert:export"})
    assert known["flags"] == [] and known["note"] == "no export"


def test_an_emulator_error_is_its_own_verdict_not_a_disagreement(tmp_path, monkeypatch):
    """``agree: false`` with no mismatches is an error: the note is its string."""
    monkeypatch.setattr(
        R.ghidra_compare,
        "compare",
        lambda *_a, **_k: _doc(
            agree=False, error="call 3 did not balance its frame", sid_mismatches=[]
        ),
    )
    row = R.oracle_row("cert", tmp_path, _gdir(tmp_path), 1.5)
    assert row["agree"] is None  # it made no comparison to agree or disagree with
    assert row["note"] == "call 3 did not balance its frame"
    assert row["flags"] == ["emulate call 3 did not balance its frame"]


def test_a_real_disagreement_keeps_its_first_difference(tmp_path, monkeypatch):
    mismatch = {"call": 0, "index": 2, "want": "D404=21", "got": "D404=3F"}
    monkeypatch.setattr(
        R.ghidra_compare, "compare", lambda *_a, **_k: _doc(agree=False, sid_mismatches=[mismatch])
    )
    row = R.oracle_row("cert", tmp_path, _gdir(tmp_path), 1.5)
    assert row["agree"] is False and row["flags"] == [] and "D404" in row["note"]


def test_the_summary_counts_the_exports_and_the_run_fails_without_them(tmp_path, capsys):
    (tmp_path / "gout").mkdir()
    args = types.SimpleNamespace(
        out=str(tmp_path), ghidra_dir=str(tmp_path / "gout"), tol=1.5, known=None
    )
    failed = R.oracles([("a", {}), ("b", {})], args)
    out = capsys.readouterr().out
    assert failed == 2 and "0/2 with a Ghidra export" in out
    assert "export missing" in out


def test_the_two_ghidra_options_are_the_two_steps(tmp_path, monkeypatch):
    """``--ghidra-facts`` exports as it replays; ``--ghidra-dir`` judges what came back."""
    monkeypatch.setattr(R.ghidra_facts, "export", lambda _out: None)
    certs = tmp_path / "certs"
    certs.mkdir()
    base = ["--certs", str(certs), "--out", str(tmp_path / "a")]
    assert R.main(base + ["--ghidra-facts"]) == 0
    assert R.main(base + ["--ghidra-dir", str(tmp_path / "g")]) == 0


def test_the_facts_export_does_not_wait_for_the_pipeline_to_print(tmp_path, monkeypatch):
    """A resumed certificate prints nothing, so the oracle exports its facts here."""
    calls = []
    monkeypatch.setattr(R.ghidra_facts, "export", calls.append)
    R.facts(tmp_path)  # no trace: nothing to export
    (tmp_path / "trace.npz").write_bytes(b"")
    R.facts(tmp_path)
    (tmp_path / "ghidra").mkdir()
    (tmp_path / "ghidra" / "ghidra_facts.json").write_text("{}")
    R.facts(tmp_path)
    assert calls == [tmp_path]


def test_more_is_not_the_usage_exit(tmp_path):
    """A caller's loop tells "invoke me again" from a bad command line by the code."""
    assert R.MORE == 3
    with pytest.raises(SystemExit) as e:
        R.main(["--no-such-option"])
    assert e.value.code == 2
    assert R.main(["--out", str(tmp_path), "--certs", str(tmp_path)]) == 0


def test_the_plan_is_the_run_the_certificate_records(tmp_path):
    doc = {
        "subtunes": [{"song": 1, "ticks": 40, "period": None, "first_repeat": None}],
        "sid_model": "8580",
        "stage": "S6",
    }
    argv = R.plan(doc, tmp_path, Path("t.sid"), 12.0)
    assert argv[:2] == ["t.sid", "--out"] and "--ghidra-facts" not in argv
    assert argv[-4:] == ["--calls", "40", "--sid-model", "8580"]


def test_the_table_reports_what_was_reproduced():
    doc = {"subtunes": [{"song": 0, "ticks": 4, "period": 2, "first_repeat": 1, "complete": True}]}
    doc["tune"] = "t.sid"
    text = R.table([("a", doc)], {"a": {"diff": [], "updated": [], "ticks": 4}})
    assert "1/1 reproduced, 0 mismatched, 0 pending" in text
    assert json.dumps(doc)  # the row does not mutate its certificate
