"""tools/liftsweep.py: exact, deopting, non-returning, timed-out and unreadable files."""

import fcntl
import json
import sys
from pathlib import Path

import pytest
from pysidtracker.header import write_psid

from deity_informant import NATIVE
from deity_informant.cgen import mlir

pytest.importorskip("pebble")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import liftsweep  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

INIT = bytes.fromhex("a9058d0020 60")  # $1000: LDA #5; STA $2000; RTS
EXACT = bytes.fromhex("ad0020 8d00d4 ee0020 60")  # $1006: LDA $2000; STA $D400; INC; RTS
# $1006: X from I/O (0-1), A from I/O; `STA $1010,X` over its own operand bytes.
DEOPT = bytes.fromhex("ad00d0 2901 aa ad01d0 9d1010 60")
SPIN = bytes.fromhex("a900 10fe")  # $1006: LDA #0; BPL *


def sid(code, run=0x1006, init=0x1000, load=0x1000):
    """A PSID of ``code`` at ``load`` with entries ``init`` and ``run``."""
    return write_psid(load=load, init=init, play=run, image=code)


@pytest.fixture(name="mlir_ready", scope="module")
def fixture_mlir_ready(tmp_path_factory):
    """``mlir-opt`` on PATH or its Docker image; skips without either."""
    with open(tmp_path_factory.getbasetemp().parent / "docker.lock", "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        if mlir.runner(build=True) is None:
            pytest.skip("needs mlir-opt or Docker")


def test_sweep_rows(tmp_path, capsys, mlir_ready):  # pylint: disable=unused-argument
    files = {"exact": INIT + EXACT, "deopt": INIT + DEOPT, "spin": INIT + SPIN, "bad": b"x"}
    for name, code in files.items():
        (tmp_path / f"{name}.sid").write_bytes(code if name == "bad" else sid(code))
    ram = bytearray(0x10000)
    ram[0xD000:0xD002] = (1, 1)  # the I/O reads: X 1, then A 1 into the high operand byte
    (tmp_path / "ram.bin").write_bytes(ram)
    out = tmp_path / "rows.json"
    paths = [str(tmp_path / f"{n}.sid") for n in files]
    args = [*paths, "--json", str(out), "-n", "8", "--jobs", "2"]
    args += ["--image", str(tmp_path / "ram.bin")]
    assert liftsweep.main(args) == 1
    rows = {Path(r["file"]).stem: r for r in json.loads(out.read_text())}
    assert (rows["exact"]["result"], rows["exact"]["detail"]) == ("exact", "")
    assert rows["deopt"]["result"] == "deopt" and rows["deopt"]["traps"]
    assert rows["spin"]["detail"] == "the reference returns from 1 of 9"
    assert rows["bad"]["result"] == "error" and rows["bad"]["seconds"] is None
    assert "exact.sid" in capsys.readouterr().out
    assert liftsweep.main([paths[0], "--timeout", "0.001", "--volatile", "0xD000-0xE000"]) == 1
    assert "timeout\ttranslate" in capsys.readouterr().out


def test_translate_and_replay_in_process(mlir_ready):  # pylint: disable=unused-argument
    data, vol = sid(INIT + DEOPT), [(0xD000, 0xE000)]
    ram = bytearray(0x10000)
    ram[0xD000:0xD002] = (1, 1)
    secs, c, h, traps = liftsweep.translate(data, vol, bytes(ram))
    assert secs > 0 and traps and "void run(" in c
    assert liftsweep.replay(data, c, h, vol, 4, bytes(ram)) == ("deopt", "call 2")
    _, c, h, _ = liftsweep.translate(sid(INIT + SPIN), vol)
    assert liftsweep.replay(sid(INIT + SPIN), c, h, vol, 3) == (
        "exact",
        "the reference returns from 1 of 4",
    )
