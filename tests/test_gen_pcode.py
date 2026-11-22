"""``tools/gen_pcode.py``: the checked-in templates are current."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_pcode  # noqa: E402  pylint: disable=wrong-import-position


def test_checked_in_templates_are_current():
    assert gen_pcode.main(["--check"]) == 0


def test_check_reports_stale_and_write_refreshes(tmp_path, monkeypatch):
    out = tmp_path / "templates_gen.rs"
    monkeypatch.setattr(gen_pcode, "OUT", out)
    assert gen_pcode.main(["--check"]) == 1
    out.write_text("stale")
    assert gen_pcode.main(["--check"]) == 1
    assert gen_pcode.main([]) == 0
    assert gen_pcode.main(["--check"]) == 0
