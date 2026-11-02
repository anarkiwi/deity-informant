"""Compare the machine's SID register changes for interrupt-driven songs with sidplayfp's.

``python tools/sidcmp.py TUNE.sid[:SONG] ... [--seconds S] [--work DIR]`` runs each song booted
as ``wpcorpus.irq_boot`` does and on the ``anarkiwi/sidtrace`` image with the same ROMs (real for
an RSID); traces are cached in ``--work`` (default ``out/sidcmp``, bind-mountable).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import zstandard
from tqdm import tqdm
from vagrant_moses import memmap as mm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# pylint: disable=wrong-import-position
from deity_informant import c64  # noqa: E402
from deity_informant.smc.machine import MachineOracle  # noqa: E402
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-order

IMAGE = "anarkiwi/sidtrace"
ROM_FILES = {"kernal": mm.KERNAL_BASE, "basic": mm.BASIC_BASE, "chargen": mm.CHARGEN_BASE}


class _Log:
    """A SID at ``$D400`` keeping its writes; reads give the last value written."""

    def __init__(self):
        self.regs, self.writes = bytearray(32), []

    def read(self, _cycle, reg):
        return self.regs[reg]

    def write(self, cycle, reg, value):
        self.regs[reg] = value
        self.writes.append((cycle, reg, value))


def changes(writes):
    """The ``(cycle, reg, value)`` writes that change their register (from 0)."""
    regs, out = {}, []
    for cycle, reg, value in writes:
        if regs.get(reg, 0) != value:
            regs[reg] = value
            out.append((cycle, reg, value))
    return out


def song_row(data, song):
    """The row fields :func:`wpcorpus.irq_boot` takes for ``song`` (0: the start song)."""
    h = wpcorpus.header(data)
    song = song or h["start"] or 1
    speed = (h["speed_word"] >> min(song - 1, 31)) & 1 if h["kind"] == "PSID" else 0
    return h | {"song": song, "speed": speed, "init": h["init"] or wpcorpus.load_span(data)[0]}


def machine_changes(data, song, cycles):
    """The machine's SID register changes of ``song`` of ``data`` before cycle ``cycles``."""
    row = song_row(data, song)
    boot = wpcorpus.irq_boot(row, data)
    ntsc = row["clock"] == "NTSC"
    log = _Log()
    o = MachineOracle(boot.image, ntsc=ntsc, devices={mm.SID_BASE: log}, roms=boot.roms)
    frames = -(-cycles // mm.video(ntsc).cycles_per_frame)
    o.run([], frames=frames, **boot.run_kw())
    return [w for w in changes(log.writes) if w[0] < cycles]


def _roms(work):
    """A directory of the real ROMs named as sidplayfp's data directory holds them."""
    d = work / "roms"
    d.mkdir(parents=True, exist_ok=True)
    roms = c64.real_roms()
    for name, base in ROM_FILES.items():
        (d / name).write_bytes(roms[base])
    return d


def sidtrace_changes(path, song, seconds, work, roms=True):
    """sidplayfp's SID register changes of ``song`` over ``seconds`` on the real ``roms`` (else
    its ROM-less set), cached in ``work``."""
    tune = work / f"{Path(path).stem}.sid"
    out = work / f"{tune.stem}_{song}_{seconds}{'' if roms else '_romless'}.csv.zst"
    if not out.exists():
        shutil.copyfile(path, tune)
        mount = ["-v", f"{_roms(work).resolve()}:/root/.local/share/sidplayfp:ro"] if roms else []
        cmd = ["docker", "run", "--rm", "-v", f"{work.resolve()}:/work", *mount, IMAGE,
               out.name, tune.name, f"-o{song}", f"-t{seconds}"]  # fmt: skip
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    with zstandard.ZstdDecompressor().stream_reader(out.open("rb")) as f:
        rows = csv.DictReader(io.TextIOWrapper(f, "ascii"))
        return changes(
            (int(r["cycle"]), int(r["reg"]), int(r["value"])) for r in rows if r["chip"] == "0"
        )


def compare(path, song=0, seconds=10, work=ROOT / "out" / "sidcmp"):
    """Whether the machine and sidplayfp make the same SID register changes in ``seconds`` of
    ``song`` of the tune at ``path``, and the counts of the matched changes' cycle offsets."""
    work.mkdir(parents=True, exist_ok=True)
    data = Path(path).read_bytes()
    row = song_row(data, song)
    ref = sidtrace_changes(path, row["song"], seconds, work, row["kind"] == "RSID")
    got = machine_changes(data, row["song"], ref[-1][0] + 1 if ref else 0)
    n = next(
        (i for i, (a, b) in enumerate(zip(got, ref)) if a[1:] != b[1:]), min(len(got), len(ref))
    )
    dt = np.array([a[0] - b[0] for a, b in zip(got[:n], ref[:n])], dtype=np.int64)
    vals, counts = np.unique(dt, return_counts=True)
    return {
        "path": str(path),
        "song": row["song"],
        "clock": row["clock"],
        "changes": len(ref),
        "machine_changes": len(got),
        "matched": n,
        "same": n == len(ref) == len(got),
        "offsets": {int(v): int(c) for v, c in zip(vals, counts)},
    }


def main(argv=None):
    """Compare each ``TUNE.sid[:SONG]``, one JSON line per song; 1 when any differs."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("tunes", nargs="+", help="TUNE.sid or TUNE.sid:SONG (default: start song)")
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--work", type=Path, default=ROOT / "out" / "sidcmp")
    args = ap.parse_args(argv)
    bad = 0
    for spec in tqdm(args.tunes, unit="song", desc="sidcmp"):
        path, _, song = spec.partition(":")
        r = compare(path, int(song or 0), args.seconds, args.work)
        bad += not r["same"]
        tqdm.write(json.dumps(r))
    return int(bad > 0)


if __name__ == "__main__":
    sys.exit(main())
