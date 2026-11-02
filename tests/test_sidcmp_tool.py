"""tools/sidcmp.py: machine SID register changes against a sidplayfp trace."""

import csv
import io
import sys
from pathlib import Path

import pytest
import zstandard

pytest.importorskip("vagrant_moses")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
from deity_informant.smc.machine import core  # noqa: E402
import sidcmp  # noqa: E402 pylint: disable=wrong-import-order
from test_wpcorpus_tool import sid  # noqa: E402 pylint: disable=wrong-import-order

pytestmark = pytest.mark.skipif(not core(), reason="needs vagrant-moses' native machine")

# init points $0314 at $1010, whose handler counts its IRQs into $D400
COUNT = bytes.fromhex("78a9108d1403a9108d150358 60eaeaea ad0ddcee8010ad80108d00d4 68a868aa6840")


def _trace(path, writes):
    """A sidtrace CSV of ``writes`` ``(cycle, reg, value)`` on chip 0, and one on chip 1."""
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(
        ["cycle", "cycle_since_nmi", "cycle_since_video_irq", "cycle_since_cia_irq"]
        + ["chip", "reg", "value"]
    )
    for cycle, reg, value in writes:
        w.writerow([cycle, "", "", "", 0, reg, value])
    w.writerow([writes[-1][0], "", "", "", 1, 0, 1])
    path.write_bytes(zstandard.ZstdCompressor().compress(text.getvalue().encode("ascii")))


def test_changes_keeps_register_changes():
    assert sidcmp.changes([(1, 0, 0), (2, 0, 5), (3, 0, 5), (4, 1, 5), (5, 0, 0)]) == [
        (2, 0, 5),
        (4, 1, 5),
        (5, 0, 0),
    ]


def test_compare_against_a_cached_trace(tmp_path, monkeypatch, capsys):
    tune = tmp_path / "count.sid"
    tune.write_bytes(sid(COUNT, 0, speed=1))
    row = sidcmp.song_row(tune.read_bytes(), 0)
    assert row["song"] == 1 and row["speed"] == 1 and row["init"] == 0x1000
    got = sidcmp.machine_changes(tune.read_bytes(), 1, 600_000)
    assert got[0][1:] == (0x18, 0x0F) and [v for _, r, v in got if r == 0] == list(
        range(1, len(got))
    )
    work = tmp_path / "work"
    calls = []

    def docker(cmd, **_kw):
        calls.append(cmd)
        _trace(work / cmd[-4], got[:-1] + [(got[-1][0] + 3, *got[-1][1:])])

    monkeypatch.setattr(sidcmp.subprocess, "run", docker)
    r = sidcmp.compare(tune, 0, 1, work)
    assert r["same"] and r["matched"] == len(got) and r["offsets"] == {0: len(got) - 1, -3: 1}
    assert len(calls) == 1 and not any("sidplayfp" in c for c in calls[0]), "PSIDs run ROM-less"
    _trace(work / "count_1_1_romless.csv.zst", got[:2] + [(got[2][0], 0, 99)])
    assert sidcmp.main([str(tune), "--seconds", "1", "--work", str(work)]) == 1
    assert '"same": false' in capsys.readouterr().out and len(calls) == 1


def test_rsid_trace_runs_on_the_real_roms(tmp_path, monkeypatch, real):
    tune = tmp_path / "count.sid"
    tune.write_bytes(sid(COUNT, 0, magic=b"RSID"))
    work, calls = tmp_path / "work", []

    def docker(cmd, **_kw):
        calls.append(cmd)
        _trace(work / cmd[-4], sidcmp.machine_changes(tune.read_bytes(), 1, 500_000))

    monkeypatch.setattr(sidcmp.subprocess, "run", docker)
    assert sidcmp.compare(tune, 1, 1, work)["same"]
    mount = next(c for c in calls[0] if c.endswith("sidplayfp:ro")).split(":")[0]
    assert (Path(mount) / "kernal").read_bytes() == real[0xE000]
