"""tools/framediff.py: where a lifted interrupt frame differs from the machine's."""

import hashlib
import struct
import sys
from pathlib import Path

import pytest

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
    hdr = b"RSID" + struct.pack(">HHHHHHHI", 2, 0x7C, 0, 0x1000, 0, 1, 1, 0)
    hdr += bytes(0x60) + struct.pack(">HBBBB", 0x4, 0, 0, 0, 0)
    data = hdr + b"\x00\x10" + code
    (root / "DOCUMENTS").mkdir(parents=True)
    (root / "x.sid").write_bytes(data)
    md5 = hashlib.md5(data).hexdigest()
    (root / "DOCUMENTS" / "Songlengths.md5").write_text(f"[Database]\n; /x.sid\n{md5}=0:01\n")
    return root


def test_interrupts_inside_frames_are_exact(tmp_path, capsys):
    root = tree(tmp_path / "hvsc", INIT + IRQ + NMI)
    assert framediff.main([str(root), "x.sid", "--frames", "30"]) == 0
    assert "check: None" in capsys.readouterr().out


def test_reports_where_a_frame_differs(tmp_path, capsys, monkeypatch):
    root = tree(tmp_path / "hvsc", INIT + IRQ + NMI)
    monkeypatch.setattr(framediff.frames, "check", lambda *a, **k: [2, "log", 1])
    assert framediff.main([str(root), "x.sid", "--frames", "30"]) == 1
    out = capsys.readouterr().out
    assert "check: [2, 'log', 1]" in out and "reference vs machine: first log difference" in out
    assert "entry (" in out and "boundary lift" in out


def test_exact_song_prints_only_the_summary(tmp_path, capsys):
    root = tree(tmp_path / "hvsc", INIT + bytes.fromhex("ad0ddc 8d00d4 40"))
    assert framediff.main([str(root), "x.sid", "--frames", "10", "--raw"]) == 0
    assert "check: None" in capsys.readouterr().out


def test_bisect_finds_no_frame_the_reference_differs_in(tmp_path, capsys):
    root = tree(tmp_path / "hvsc", INIT + IRQ + NMI)
    assert framediff.main([str(root), "x.sid", "--frames", "30", "--bisect"]) == 0
    assert "every frame's memory is the machine's" in capsys.readouterr().out


def test_bisect_reports_the_first_frame_the_reference_differs_in(tmp_path, capsys, monkeypatch):
    root = tree(tmp_path / "hvsc", INIT + IRQ + NMI)
    real = framediff.frames._call  # pylint: disable=protected-access

    def corrupt(prog, host, k, rec, mem, bank=None):
        out = real(prog, host, k, rec, mem, bank)
        if k == 3:
            mem[0x1F0] ^= 0xFF
        return out

    monkeypatch.setattr(framediff.frames, "_call", corrupt)
    assert framediff.main([str(root), "x.sid", "--frames", "30", "--bisect"]) == 1
    out = capsys.readouterr().out
    assert "reference differs after frame 3" in out and "01f0 machine" in out
