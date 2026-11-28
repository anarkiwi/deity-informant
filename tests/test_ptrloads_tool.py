"""tools/ptrloads.py: the pointer loads of a PSID's lift and what bounds each."""

import json
import struct
import sys
from pathlib import Path

import pytest

import wholeprog_progs as W
import test_objects as TO
from deity_informant import NATIVE

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ptrloads  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")


def psid(src):
    """``(PSID bytes, labels)`` of ``src`` at ``$1000`` with entries ``init`` and ``play``,
    four songs."""
    img, labels = W.assemble(src)
    body = bytes(img[0x1000 : labels["end"]])
    hdr = b"PSID" + struct.pack(
        ">HHHHHHHI", 2, 0x7C, 0x1000, labels["init"], labels["play"], 4, 1, 0
    )
    return hdr + b"\0" * 0x60 + struct.pack(">HBBBB", 0, 0, 0, 0, 0) + body, labels


def test_rows_name_each_pointer_load(tmp_path, capsys):
    """The walk's load is listed with its pair, ``Y`` and a proven hull holding the runs
    (the song's pointer is song data: the hull is the data block's); ``--json`` writes the
    rows."""
    data, labels = psid(TO.SIGN)
    rows = ptrloads.survey(data)
    [row] = [r for r in rows if r["pc"] == labels["loop"]]
    assert row["mode"] == "(zp),Y" and row["zp"] == 0xFB and row["y"] == "0-255"
    assert row["proven"][0] <= labels["r0"] and labels["r3"] < row["proven"][1]
    f = tmp_path / "t.sid"
    f.write_bytes(data)
    out = tmp_path / "rows.json"
    assert ptrloads.main([str(f), "--json", str(out)]) == 0
    assert f"${labels['loop']:04X} (zp),Y $FB" in capsys.readouterr().out
    assert json.loads(out.read_text())[str(f)][0]["pc"] == rows[0]["pc"]
    assert ptrloads._span(b"") == "-" and ptrloads._hex(None) == "-"


def test_every_access_has_the_memory_maps_verdict(tmp_path, capsys):
    """``--all``: the cursors' loads, their bases in the program, read plain; the verdicts
    name why an access is held to the program or not."""
    data, labels = psid(TO.CURSOR)
    rows = {r["pc"]: r for r in ptrloads.survey(data, every=True)}
    assert all(rows[labels[n]]["verdict"].startswith("plain") for n in ("rd", "seg"))
    assert ptrloads.verdict((0x1000, 0x1000), (0x1000, 0x1001), True, (0x1000, 0x1010)) == (
        "plain, checked by the memory map"
    )
    held = (0x1000, labels["end"])
    assert ptrloads.verdict(None, None, False, held) == "no base"
    assert ptrloads.verdict((0, 0xFFFF), None, False, held) == "base unknown"
    assert ptrloads.verdict((0xD400, 0xD400), None, False, held) == "base in I/O"
    assert ptrloads.verdict((0x4000, 0x4000), None, False, held) == "base outside the program"
    assert ptrloads.verdict((0x1000, 0x1000), None, False, held).startswith("entry words")
    assert ptrloads.verdict((0x1000, 0x1000), (0x1000, 0x1001), False, held) == "plain"
    f = tmp_path / "t.sid"
    f.write_bytes(data)
    assert ptrloads.main([str(f), "--all"]) == 0
    assert f"${labels['rd']:04X} LDA indy" in capsys.readouterr().out
