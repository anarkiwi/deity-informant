"""tools/fuzzsweep.py: seeded fuzz sweeps."""

import sys
from pathlib import Path

import pytest

import test_guard_fuzz
from deity_informant import NATIVE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fuzzsweep  # noqa: E402 pylint: disable=wrong-import-position,import-error,wrong-import-order

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")


@pytest.mark.parametrize("module", ["test_guard_fuzz", "test_ptr_fuzz"])
def test_a_passing_seed_reports_nothing(module):
    assert fuzzsweep.run(3, "interp", module) is None


def test_a_failing_seed_is_reported(monkeypatch):
    def fail(seed, cycles):
        raise AssertionError((seed, cycles))

    monkeypatch.setattr(test_guard_fuzz, "test_random_programs_are_exact", fail)
    assert fuzzsweep.run(7) == (7, False, "AssertionError((7, False))")


def test_main_sweeps_a_range(capsys):
    assert fuzzsweep.main(["--seeds", "0", "4", "--jobs", "2", "--backend", "interp"]) == 0
    assert capsys.readouterr().out.strip().endswith("failing seeds: []")
