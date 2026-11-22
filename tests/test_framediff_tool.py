"""tools/framediff.py: where a lifted interrupt frame differs from the machine's."""

import hashlib
import sys
from pathlib import Path

import pytest
from pysidtracker.header import write_psid

from deity_informant import NATIVE, c64
from deity_informant.smc.machine import core

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import framediff  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(
    not (NATIVE and core() and c64.TRACE_NATIVE), reason="needs the native machine and trace"
)

# test_frames.NESTED with a handler that also counts $0300 beside the IRQ handler counting it
INIT = bytes.fromhex(
    "78a935 8501 a940 8dfeff a910 8dffff a960 8dfaff a910 8dfbff"
    "a964 8d04dd a900 8d05dd a981 8d0ddd a901 8d0edd 58 4c1e10"
).ljust(0x40, b"\xea")
IRQ = bytes.fromhex("ad0ddc a228 ee0003 ee0003 8e04d4 ca d0f4 40").ljust(0x20, b"\xea")
NMI = bytes.fromhex("48 ad0ddd ad0003 8d18d4 68 40")


def tree(root, code):
    data = write_psid(
        kind="RSID", load=0x1000, init=0x1000, play=0, image=code, flags=0x4, embed_load=True
    )
    (root / "DOCUMENTS").mkdir(parents=True)
    (root / "x.sid").write_bytes(data)
    md5 = hashlib.md5(data).hexdigest()
    (root / "DOCUMENTS" / "Songlengths.md5").write_text(f"[Database]\n; /x.sid\n{md5}=0:01\n")
    return root


def run(tmp_path, capsys, *args, code=INIT + IRQ + NMI):
    """``framediff``'s exit status and output on a tree holding ``code`` as ``x.sid``."""
    rc = framediff.main([str(tree(tmp_path / "hvsc", code)), "x.sid", *args])
    return rc, capsys.readouterr().out


@pytest.mark.parametrize(
    "code,args,want",
    [
        (INIT + IRQ + NMI, ["--frames", "30"], "check: None"),
        (INIT + bytes.fromhex("ad0ddc 8d00d4 40"), ["--frames", "10", "--raw"], "check: None"),
        (INIT + IRQ + NMI, ["--frames", "30", "--bisect"], "every frame's memory is the machine's"),
    ],
    ids=["interrupts-inside-frames", "raw-summary-only", "bisect-finds-no-frame"],
)
def test_exact_songs_report_no_difference(tmp_path, capsys, code, args, want):
    """Interrupts inside frames are exact, an exact song with ``--raw`` prints only the
    summary, and ``--bisect`` finds no frame the reference differs in."""
    rc, out = run(tmp_path, capsys, *args, code=code)
    assert rc == 0 and want in out


def test_reports_where_a_frame_differs(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(framediff.frames, "check", lambda *a, **k: [2, "log", 1])
    rc, out = run(tmp_path, capsys, "--frames", "30")
    assert rc == 1
    assert "check: [2, 'log', 1]" in out and "reference vs machine: first log difference" in out
    assert "entry (" in out and "boundary lift" in out


def test_bisect_reports_the_first_frame_the_reference_differs_in(tmp_path, capsys, monkeypatch):
    real = framediff.frames._call  # pylint: disable=protected-access

    def corrupt(prog, host, k, rec, mem, bank=None):
        out = real(prog, host, k, rec, mem, bank)
        if k == 3:
            mem[0x1F0] ^= 0xFF
        return out

    monkeypatch.setattr(framediff.frames, "_call", corrupt)
    rc, out = run(tmp_path, capsys, "--frames", "30", "--bisect")
    assert rc == 1
    assert "reference differs after frame 3" in out and "01f0 machine" in out
