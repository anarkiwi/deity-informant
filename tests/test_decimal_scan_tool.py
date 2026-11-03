"""tools/decimal_scan.py: songs that run arithmetic with the decimal flag set."""

import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import decimal_scan  # noqa: E402 pylint: disable=wrong-import-position,import-error

LOAD = 0x1000
PLAY = LOAD
SED_ADC = bytes([0xF8, 0x18, 0x69, 0x01, 0xD8, 0x60])
ADC_ONLY = bytes([0x18, 0x69, 0x01, 0xD8, 0x60])
SED_IDLE = bytes([0xF8, 0xD8, 0x60])
REG = [0, 0, 0, 0xFF] + [0] * 12


def psid(code):
    header = b"PSID" + struct.pack(">HHHHHHHI", 2, 0x7C, 0, LOAD, PLAY, 1, 1, 0) + bytes(0x7C - 22)
    return header + struct.pack("<H", LOAD) + code


def job(code, starts):
    record = {
        "path": "x.sid",
        "song": 1,
        "state": [],
        "reg": REG,
        "facts": {"starts": starts},
    }
    row = {"kind": "PSID", "play": PLAY, "calls_run": 3, "mode": "flat"}
    return json.dumps(record), row


@pytest.mark.parametrize(
    "code, starts, decimal",
    [
        (SED_ADC, [LOAD, LOAD + 1, LOAD + 2, LOAD + 4], True),
        (SED_IDLE, [LOAD, LOAD + 1], False),
        (ADC_ONLY, [LOAD, LOAD + 1], False),
    ],
)
def test_arithmetic_runs_with_the_decimal_flag_only_after_sed(tmp_path, code, starts, decimal):
    (tmp_path / "x.sid").write_bytes(psid(code))
    out = decimal_scan.scan(job(code, starts), str(tmp_path), 1000)
    assert out["decimal"] is decimal
    if decimal:
        assert (out["first_call"], out["hits"]) == (0, 1)
    assert out["sed"] == int(code[0] == 0xF8)


def test_an_interrupt_driven_song_is_judged_by_its_executed_instructions(tmp_path):
    (tmp_path / "x.sid").write_bytes(psid(SED_ADC))
    line, row = job(SED_ADC, [LOAD, LOAD + 1, LOAD + 2])
    out = decimal_scan.scan((line, row | {"play": 0}), str(tmp_path), 1000)
    assert out["decimal"] is None and out["arith"] == 1 and out["sed"] == 1


def test_a_run_over_its_time_budget_is_cut_short(tmp_path, monkeypatch):
    (tmp_path / "x.sid").write_bytes(psid(ADC_ONLY))
    monkeypatch.setattr(decimal_scan, "BUDGET_S", -1)
    img = bytearray(0x10000)
    img[LOAD : LOAD + len(ADC_ONLY)] = ADC_ONLY
    assert decimal_scan.traced_hits(img, REG, PLAY, 600, 1000) == (600, 0)
