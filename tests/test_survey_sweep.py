"""Hermetic checks of the sweep instruments: row extraction, fault classes, weighting."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "survey"))

sweep = pytest.importorskip("tuneprog_sweep")
rep = pytest.importorskip("tuneprog_report")

CERT = {
    "cost": {"sites": 9, "regions": 2, "ir_statements": 7, "verify_cpu_seconds": 0.5},
    "entry": {"kind": "irq", "source": "cia_timer", "kernal": True, "cycles_per_tick": 100},
    "stack": {"depth": 4, "procs": ["tick"]},
    "subtunes": [{"ticks": 12, "complete": False, "period": None, "song": 1}],
    "copies": {"families": [{"proc": "tick"}], "statements": 3, "unverified": 1, "refused": []},
    "divergence": {"trap": "unreached", "detail": "X1", "tick": 5},
}
TRACE = {
    "sites": [
        [0x1000, 0x60, [], 1, [], [[0x60]], {}, [], []],
        [0x1000, 0xA9, [1], 1, [], [[0xA9, 1]], {}, [[0, [0xD400]]], []],
        [0x1005, 0xAD, [0, 0xD4], 1, [], [[0xAD, 0, 0xD4]], {}, [[0, [0xD41B]]], []],
    ],
    "cells": [0x1000],
    "written_play": [0x1000, 0x2000],
    "code": [0x1000, 0x1005],
    "chip_ops": [[0x1005, 0]],
    "rets": [[0x1100, 0, 2, [], [], []]],
}


def test_stack_field():
    assert sweep._stack("eliminated") == {"stack": "eliminated"}
    assert sweep._stack({"depth": 3, "procs": ["a"]})["stack"] == "residual"
    assert sweep._stack(None)["stack"] is None


def test_fault_classes():
    from deity_informant.tuneprog.machine import Refusal

    for exc, kind in (
        (Refusal("no entry", "play=0"), "refused"),
        (TimeoutError("x"), "timeout"),
        (MemoryError(), "oom"),
        (KeyError("L1"), "crashed"),
    ):
        try:
            raise exc
        except BaseException as e:  # pylint: disable=broad-exception-caught
            assert sweep._fault(e)[0] == kind


def test_certificate_and_smc(tmp_path):
    (tmp_path / "certificate.json").write_text(json.dumps(CERT))
    (tmp_path / "state.json").write_text(json.dumps({"stage": "done", "procs": 2, "stmts": 7}))
    (tmp_path / "trace.json").write_text(json.dumps(TRACE))
    row = {}
    assert sweep._certificate(tmp_path, row)
    assert row["outcome"] == "diverged"
    assert (row["stack"], row["depth"], row["held"]) == ("residual", 4, ["tick"])
    assert (row["entry"], row["kernal"], row["copy_families"]) == ("irq", True, 1)
    sweep._smc(tmp_path, row)
    assert row["opcode_cells"] == 1  # $1000 ran as both RTS and LDA
    assert row["opcode_cells_non_rts"] == 0
    assert row["smc_cells"] == 1 and row["smc_play"] == 1
    assert row["two_plane_bytes"] == 0 and row["io_ram_bytes"] == 1
    assert row["rts_unmatched"] == 2


def test_certificate_absent(tmp_path):
    assert not sweep._certificate(tmp_path, {})


def test_weighting_matches_family_size():
    rows = [
        {"path": "a", "family": "F", "outcome": "certified"},
        {"path": "b", "family": "F", "outcome": "refused"},
        {"path": "c", "family": "G", "outcome": "certified"},
    ]
    R = rep.Rates(rows, {"F": 1000, "G": 1})
    n, d, raw, weighted = R.rate(lambda r: r["outcome"] == "certified")
    assert (n, d) == (2, 3)
    assert round(raw) == 67
    assert round(weighted, 1) == round(100.0 * 500 / 1001, 1)


def test_divclass():
    assert "unreached" in rep.divclass({"divergence": {"trap": "unreached", "detail": "X1"}})
    assert "sid" in rep.divclass({"divergence": {"compared": "sid", "index": 0}})
