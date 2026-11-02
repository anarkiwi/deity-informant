"""tools/rowsdiff.py: song outcomes across two corpus runs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rowsdiff  # noqa: E402 pylint: disable=wrong-import-position,import-error


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def test_compare(tmp_path, capsys):
    before = write(
        tmp_path / "b",
        [
            {"path": "a", "song": 1, "exact": False, "mismatch": [3, "log", 1]},
            {"path": "a", "song": 2, "exact": True, "mismatch": None},
            {"path": "c", "song": 1, "skip": "init cycles"},
        ],
    )
    after = write(
        tmp_path / "a",
        [
            {"path": "a", "song": 1, "exact": True, "mismatch": None},
            {"path": "a", "song": 2, "exact": False, "mismatch": [0, "memory", 5]},
            {"path": "c", "song": 1, "error": "boom"},
            {"path": "d", "song": 1, "exact": True},
        ],
    )
    assert rowsdiff.main([before, after]) == 0
    out = capsys.readouterr().out
    assert "| a | 1 | mismatch log | exact | None |" in out
    assert "| a | 2 | exact | mismatch memory | [0, 'memory', 5] |" in out
    assert "| c | 1 | skip init cycles | error | None |" in out and "1  - -> exact" in out
