"""tools/corpussum.py: corpus rows summary."""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import corpussum  # noqa: E402 pylint: disable=wrong-import-position,import-error


def lifted(path, mode, ref, low, jen=None, **kw):
    return {
        "path": path,
        "song": 1,
        "kind": "PSID",
        "mode": mode,
        "exact": True,
        "outcome": "exact",
        "stop": None,
        "compile_ms": 1.0,
        "lift_ms": 2.0,
        "timing": {"reference_s": ref, "lifted_s": low, "jennings_s": jen},
        "stats": kw.get("stats", {"stack_private": True, "code_guards": 0, "stack_guards": 0}),
    }


ROWS = [
    lifted("a", "flat", 2.0, 1.0, 4.0),
    lifted("b", "flat", 3.0, 1.0),
    lifted("c", "banked", 1.0, 2.0, stats={"recursive": 1, "code_guards": 2}),
    {
        "path": "d",
        "song": 1,
        "kind": "PSID",
        "mode": "flat",
        "exact": False,
        "outcome": "mismatch",
        "mismatch": [0, "log", 3],
        "stop": None,
    },
    {
        "path": "e",
        "song": 1,
        "kind": "RSID",
        "skip": "irq-driven",
        "basic": True,
        "irq": {"path": "kernal", "idle": True, "jammed": False},
    },
]


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def test_pcts_and_ratios():
    assert corpussum.pcts([None, np.nan, 1.0, 3.0, 2.0]) == (3, 1.2, 2.0, 2.8)
    assert corpussum.pcts([None]) is None
    r = corpussum.ratios(ROWS, "jennings_s", "lifted_s")
    assert r[0] == 4.0 and np.isnan(r[1:]).all()
    assert corpussum.get({"a": None}, "a", "b") is None


def test_stats_cover():
    c = corpussum.stats_cover(ROWS)
    assert (c["lifted"], c["stack_private"], c["recursive"], c["code_guards>0"]) == (3, 2, 1, 1)
    assert c["no guards"] == 2


def test_summary(tmp_path, capsys):
    rows = write(
        tmp_path / "r", ROWS + [ROWS[0] | {"song": 2, "exact": False, "outcome": "halted"}]
    )
    paths = tmp_path / "p"
    paths.write_text("a\nb\nc\nd\ne\n")
    assert corpussum.main([rows, "--paths", str(paths)]) == 0
    out = capsys.readouterr().out
    assert "rows 6  tunes 5" in out
    assert "exact 3 / 5 lifted = 60.000%" in out
    assert "d     1     mismatch  [0, 'log', 3]" in out
    assert "flat    reference/lifted  3  2.00  2.00    2.80" in out
    assert "('kernal', True, False)" in out and "basic 1" in out
    assert corpussum.main([rows, "--paths", str(tmp_path / "p")]) == 0


def test_filter_and_diff(tmp_path, capsys):
    before = write(tmp_path / "b", ROWS)
    after = write(
        tmp_path / "a",
        [ROWS[0], {k: v for k, v in ROWS[4].items() if k != "skip"} | {"exact": True}],
    )
    only = tmp_path / "only"
    only.write_text("e\n")
    assert corpussum.main([before, "--diff", after, "--paths", str(only)]) == 0
    out = capsys.readouterr().out
    assert "skip irq-driven  exact  1" in out and "stack_private" not in out
