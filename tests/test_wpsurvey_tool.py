"""tools/wpsurvey.py: PSID loading, per-tune stats and the summary."""

import json
import struct
import sys
from pathlib import Path

import pytest

from deity_informant import NATIVE

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wpsurvey  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

# init $1000: RTS; play $1001: PHA, JSR $1008, PLA, RTS; $1008: STA $D400, RTS
CODE = bytes([0x60, 0x48, 0x20, 0x08, 0x10, 0x68, 0x60, 0xEA, 0x8D, 0x00, 0xD4, 0x60])


def psid(load=0x1000, init=0x1000, play=0x1001, magic=b"PSID", inline=False):
    hdr = magic + struct.pack(">HHHHHHHI", 2, 0x7C, 0 if inline else load, init, play, 1, 1, 0)
    hdr += bytes(0x7C - len(hdr))
    return hdr + (struct.pack("<H", load) if inline else b"") + CODE


def test_psid_parse():
    img, init, play, songs, start = wpsurvey.psid(psid(inline=True))
    assert (init, play, songs, start) == (0x1000, 0x1001, 1, 1)
    assert img[0x1000 : 0x1000 + len(CODE)] == CODE
    assert wpsurvey.psid(psid(magic=b"RSID")) is None
    assert wpsurvey.psid(psid(play=0)) is None
    assert wpsurvey.psid(psid(load=0xFFF8)) is None


def test_survey_main(tmp_path, capsys):
    (tmp_path / "a.sid").write_bytes(psid())
    (tmp_path / "b.sid").write_bytes(psid(magic=b"RSID"))
    (tmp_path / "c.sid").write_bytes(b"PSID")
    out = tmp_path / "s.jsonl"
    args = [str(tmp_path), "--calls", "3", "--check", "3", "--jobs", "1", "--out", str(out)]
    assert wpsurvey.main(args) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["tunes"] == 3 and s["lifted"] == 1 and s["stack_private"] == 1
    assert s["skipped"] == {"not psid-callable": 2}
    assert s["checked"] == 1 and not s["inexact"] and not s["deopting"]
    rows = {Path(r["path"]).name: r for r in map(json.loads, out.read_text().splitlines())}
    assert rows["a.sid"]["calls"] == 1 and rows["a.sid"]["stack_insns"] == {"PHA": 1, "PLA": 1}
    assert wpsurvey._job(("missing.sid", 1))["error"]  # pylint: disable=protected-access
