"""tools/mutsweep.py: source mutations applied, checked and restored."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mutsweep  # noqa: E402 pylint: disable=wrong-import-position,import-error


def _spec(tmp_path, muts):
    src = tmp_path / "src.txt"
    src.write_text("keep = 1\ncheck = 2\n")
    spec = tmp_path / "muts.json"
    spec.write_text(json.dumps({n: [[str(src), o, x] for o, x in e] for n, e in muts.items()}))
    return src, spec


def test_caught_and_surviving_mutations_are_reported(tmp_path, capsys):
    src, spec = _spec(tmp_path, {"caught": [("check = 2", "check = 3")], "lives": [("keep", "kk")]})
    check = f"grep -q 'check = 2' {src}"
    assert mutsweep.main([str(spec), "--build", "true", "--check", check]) == 1
    out = capsys.readouterr().out
    assert "survived: ['lives']" in out and '"caught"' in out
    assert src.read_text() == "keep = 1\ncheck = 2\n"


def test_a_failing_build_is_recorded_and_the_source_restored(tmp_path, capsys):
    src, spec = _spec(tmp_path, {"broken": [("keep", "x")]})
    assert mutsweep.main([str(spec), "--build", "echo no >&2; false", "--check", "true"]) == 0
    assert '"build": [\n   "no"' in capsys.readouterr().out
    assert src.read_text().startswith("keep")


def test_an_edit_that_does_not_apply_restores_earlier_edits(tmp_path):
    src, _ = _spec(tmp_path, {})
    with pytest.raises(ValueError):
        mutsweep.apply([[str(src), "keep", "k2"], [str(src), "absent", "y"]])
    assert src.read_text().startswith("keep = 1")
    assert mutsweep.run("sleep 2", timeout=0.1) == (-1, ["timeout"])
