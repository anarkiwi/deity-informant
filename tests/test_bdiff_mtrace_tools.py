"""tools/bdiff.py and tools/mtrace.py on synthetic banked songs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
# pylint: disable=wrong-import-position,import-error
from deity_informant import NATIVE, c64, wholeprog  # noqa: E402
from test_wpcorpus_tool import sid  # noqa: E402

import bdiff  # noqa: E402 pylint: disable=wrong-import-order
import mtrace  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (NATIVE and c64.TRACE_NATIVE), reason="needs the native extension and native trace"
)
# init: RTS; play $1001: LDA #$35; STA $01; LDA $D41B; BNE $100D; INC $D400; LDA #$37; STA $01
SONG = bytes.fromhex("60" "a9358501" "ad1bd4" "d003" "ee00d4" "a9378501" "60")


@pytest.fixture(name="tune")
def fixture_tune(tmp_path):
    (tmp_path / "t.sid").write_bytes(sid(SONG, 0x1001))
    return tmp_path


def test_bdiff_exact_and_mismatch(tune, capsys):
    assert bdiff.main(["t.sid", "1", "--calls", "3", "--hvsc", str(tune)]) == 0
    assert "exact over 3 calls" in capsys.readouterr().out
    s = bdiff.session((tune / "t.sid").read_bytes(), 1, 3)
    ram = s["inits"][-1]["ram"]
    extra = bytearray(ram)
    extra[0x100A:0x100D] = bytes.fromhex("ad05d4")  # the INC becomes LDA $D405
    s["inits"][-1]["ram"] = bytes(extra)
    s["prog"] = wholeprog.lift(extra, [0x1001], [c64.DEVICES], bank=c64.bank_map())
    d = bdiff.diff(s)
    assert d["call"] == 0 and d["mismatch"][:2] == ("log", 1)
    assert d["machine"][1] == (0xD400, 0, False) and d["lift"][1] == (0xD405, 0, False)


def test_mtrace_departure(tune, capsys):
    assert mtrace.main(["t.sid", "1", "1", "--hvsc", str(tune)]) == 0
    assert "no departure" in capsys.readouterr().out
    s = bdiff.session((tune / "t.sid").read_bytes(), 1, 2, mtrace.stepper(1))
    s["records"][1]["log"] = [(0xD41B, 1, False)]
    lo, steps, ref, _ = mtrace.departure(s, 1)
    assert steps[lo][0] == 0x1008 and ref(lo + 1)["pc"] == 0x100D
