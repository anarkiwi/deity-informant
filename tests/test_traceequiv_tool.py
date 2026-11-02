"""tools/traceequiv.py: both banked-run backends on a synthetic banked song."""

import json
import sys
from pathlib import Path

import pytest

from deity_informant import NATIVE, c64

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import traceequiv  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order
from test_wpcorpus_tool import BANKED, BUSY, sid  # noqa: E402 pylint: disable=C0411,C0413

pytestmark = pytest.mark.skipif(
    not (NATIVE and c64.TRACE_NATIVE), reason="needs the native extension and native trace"
)


def test_banked_song_backends_agree(tmp_path, capsys):
    (tmp_path / "banked.sid").write_bytes(sid(BANKED, 0x1001))
    song = {"path": "banked.sid", "init": 0x1000, "play": 0x1001, "song": 1, "calls": 40}
    rows = [song | {"mode": "banked", "init_halt": "return", "clock": "PAL"}, {"mode": "flat"}]
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"cut', encoding="utf-8")
    out = tmp_path / "eq.jsonl"
    args = [str(path), "--hvsc", str(tmp_path), "--jobs", "1", "--out", str(out)]
    assert traceequiv.main(args) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["compared"] == s["equal"] == s["records_equal"] == 1 and s["calls"] == 40
    (row,) = map(json.loads, out.read_text().splitlines())
    assert row["check"] is None and row["facts_equal"]
    assert traceequiv.summary([{"path": "x", "error": "e"}])["mismatches"] == ["x"]


def test_irq_song_backends_agree(tmp_path, capsys):
    (tmp_path / "busy.sid").write_bytes(sid(BUSY, 0, magic=b"RSID"))
    song = {"path": "busy.sid", "init": 0x1000, "song": 1, "calls": 20, "clock": "PAL"}
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(song | {"irq": {}}) + "\n" + json.dumps(song), encoding="utf-8")
    args = [str(path), "--hvsc", str(tmp_path), "--irq", "--jobs", "1", "--max-cycles", "5000"]
    assert traceequiv.main(args) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["compared"] == s["equal"] == s["scan_equal"] == 1 and s["calls"] == 20
