"""tools/wpbank.py: row selection, the call comparison, the check loop and the tune runner."""

import json
import struct
import sys
from pathlib import Path

import pytest

pytest.importorskip("vagrant_moses")
pytest.importorskip("pebble")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
# pylint: disable=wrong-import-position,wrong-import-order
import wpbank  # noqa: E402

from deity_informant import NATIVE, c64  # noqa: E402
from deity_informant.smc.machine import core  # noqa: E402

BANKED = NATIVE and core()

# init $1000: RTS; play $1001: LDA #$35; STA $01; LDA $E000; STA $D400; INC $E000; RTS
CODE = bytes.fromhex("60a9358501ad00e08d00d4ee00e060")


def sid(magic=b"PSID", play=0x1001):
    hdr = magic + struct.pack(">HHHHHHHI", 2, 0x7C, 0x1000, 0x1000, play, 1, 1, 0)
    return hdr + bytes(0x7C - len(hdr)) + CODE


def record(halt="return", log=(), reg=None, ram=None):
    return {
        "halt": halt,
        "log": list(log),
        "reg": reg or [1, 2, 3, 0xFD] + [0] * 4 + [1, 0, 1, 0, 0, 0, 1, 0],
        "ram": ram or bytes(0x10000),
    }


def test_selected():
    def row(kind, *cells):
        return {"kind": kind, "cells": list(cells)}

    bank = "cpu_port.abs.bank_reg.same_call_earlier.flat"
    flat = "cpu.zp.ptr_indirect.same_call_earlier.flat"
    kvec = "cpu.abs.kernal_vector.pre_lift.flat"
    assert wpbank.selected(row("PSID", flat, bank)) and not wpbank.selected(row("PSID", flat))
    assert wpbank.selected(row("RSID", kvec, flat)) and not wpbank.selected(row("RSID", kvec))
    assert not wpbank.selected({"path": "x", "error": "e"})


def test_mismatch():
    ref = record(log=[(0xD400, 1, True)])
    out = {**record(), "log": [(0xD400, 1, 77, True)]}
    assert c64.mismatch(ref, out) is None
    assert c64.mismatch(ref, {**out, "log": [(0xD400, 2, 0, True)]}) == ("log", 0)
    assert c64.mismatch(ref, {**out, "log": []}) == ("log", 0)
    assert c64.mismatch(ref, {**out, "reg": [9] + out["reg"][1:]})[0] == "reg"
    flags = list(out["reg"])
    flags[12] = flags[15] = 1
    assert c64.mismatch(ref, {**out, "reg": flags}) is None
    dead = bytearray(0x10000)
    dead[0x1FD] = dead[0x100] = 5
    assert c64.mismatch(ref, {**out, "ram": dead}) is None
    dead[0x1FE] = 5
    assert c64.mismatch(ref, {**out, "ram": dead}) == ("memory", 0x1FE)
    assert c64.mismatch(record("cycles"), {**out, "halt": "cycles"}) == ("no-return",)
    assert c64.mismatch(record("jam"), out) == ("halt", "jam", "return")


class FakeProg:
    """A lifted program that increments $C000 and A, logging the port it was called under."""

    def __init__(self):
        self.ports = []

    def call(self, mem, entry, a, reg, log, max_cycles, reads):  # pylint: disable=R0913,R0917
        assert entry == 0 and a == 0 and log and max_cycles and reads == b""
        self.ports.append(mem[1])
        mem[0xC000] += 1
        reg = list(reg)
        reg[0] += 1
        return {"halt": "return", "reg": reg, "log": [(0xD400, mem[0xC000], 0, True)]}


def test_check():
    img = bytes(0x10000)
    reg0 = record()["reg"]
    refs = []
    for n in range(1, 4):
        ram = bytearray(img)
        ram[1], ram[0xC000] = 0x35, n
        reg = list(reg0)
        reg[0] += n
        refs.append(record(log=[(0xD400, n, True)], reg=reg, ram=bytes(ram)))
    prog = FakeProg()
    assert c64.check(prog, img, reg0, refs, 0x35) is None and prog.ports == [0x35] * 3
    assert c64.check(FakeProg(), img, reg0, refs, 0x36) == [0, "memory", 1]
    refs[2]["reg"][1] = 7
    assert c64.check(FakeProg(), img, reg0, refs, 0x35)[:2] == [2, "reg"]


def test_bank_check_skips(tmp_path):
    (tmp_path / "r.sid").write_bytes(sid(b"RSID"))
    (tmp_path / "n.sid").write_bytes(b"nope")
    assert wpbank.bank_check(tmp_path / "r.sid", 3)["skip"] == "irq-driven"
    assert wpbank.bank_check(tmp_path / "n.sid", 3)["skip"] == "not sid-callable"
    assert "error" in wpbank._job((tmp_path / "missing.sid", 3))  # pylint: disable=W0212


def test_main(tmp_path, capsys):
    rows = tmp_path / "rows.jsonl"
    cell = "cpu_port.abs.bank_reg.same_call_earlier.flat"
    (tmp_path / "r.sid").write_bytes(sid(b"RSID"))
    lines = [{"path": str(tmp_path / n), "kind": "RSID", "cells": [cell]} for n in ("r.sid",)]
    lines.append({"path": str(tmp_path / "f.sid"), "kind": "PSID", "cells": []})
    rows.write_text("\n".join(map(json.dumps, lines)) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    assert wpbank.main(["--rows", str(rows), "--jobs", "1", "--out", str(out)]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["tunes"] == 1 and s["skipped"] == {"irq-driven": 1} and s["checked"] == 0
    assert json.loads(out.read_text(encoding="utf-8"))["skip"] == "irq-driven"


def test_summary():
    rows = [
        {"tune": "a", "exact": True, "mismatch": None, "deopts": 0, "stack_private": 1,
         "configs": [5, 7]},
        {"tune": "b", "exact": False, "mismatch": [3, "log", 0], "deopts": 2,
         "stack_private": 0, "configs": [7]},
        {"tune": "c", "skip": "irq-driven"},
        {"tune": "d", "error": "timeout"},
    ]  # fmt: skip
    s = wpbank.summary(rows)
    assert (s["tunes"], s["checked"], s["exact"], s["errors"]) == (4, 2, 1, 1)
    assert s["inexact"] == ["b"] and s["timeouts"] == ["d"] and s["mismatch_kinds"] == {"log": 1}
    assert s["configs"] == {7: 2, 5: 1} and s["deopting"] == 1 and s["stack_private"] == 1


@pytest.mark.skipif(not BANKED, reason="needs the native bank-aware lifter")
def test_bank_check_lifts(tmp_path):
    (tmp_path / "p.sid").write_bytes(sid())
    r = wpbank.bank_check(tmp_path / "p.sid", 20)
    assert r["exact"], r
    assert r["configs"] == [5, 7]
