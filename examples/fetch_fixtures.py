"""Fetch the prototype's HVSC tunes and render their sidplayfp oracle grids.

Tunes and oracle CSVs are derived fixtures -- fetched/rendered here, never
committed. Requires network (HVSC mirror) and Docker (sidtrace oracle image).

NOTE: the sidtrace Docker daemon must be able to bind-mount the oracle output
directory. If your Docker daemon cannot see the checkout path, set PCODE_ORACLE
to a directory the daemon can mount.
"""
import os
import shutil
from pathlib import Path

from pysidtracker.oracle import run_sidtrace
from pysidtracker.testing import fetch_tune

TUNES = {
    "A_Mind_Is_Born": None,  # fetched from linusakesson.net (see README), not HVSC
    "Automatas": "MUSICIANS/G/Goto80/Automatas.sid",
    "GoatTracker_MW1": "MUSICIANS/C/Cadaver/GoatTracker_example_MW1_title.sid",
    "GoatTracker_drum": "MUSICIANS/C/Cadaver/GoatTracker_drum_example.sid",
    "Stella_defMON": "MUSICIANS/E/Eeben_Aleksi/Stella_2600_by_Starlight.sid",
}
TUNE_DIR = Path(os.environ.get("PCODE_DIR", ".")) / "tunes"
ORACLE_DIR = Path(os.environ.get("PCODE_ORACLE", "oracle"))
CACHE = Path(os.environ.get("PCODE_CACHE", "hvsc_cache"))


def main():
    TUNE_DIR.mkdir(parents=True, exist_ok=True)
    ORACLE_DIR.mkdir(parents=True, exist_ok=True)
    for name, relpath in TUNES.items():
        dst = TUNE_DIR / f"{name}.sid"
        if relpath and not dst.exists():
            shutil.copy(fetch_tune(relpath, cache_dir=CACHE), dst)
        if not dst.exists():
            print(f"SKIP {name}: fetch it manually (see README)")
            continue
        csv = ORACLE_DIR / f"{name}.csv.zst"
        if not csv.exists():
            run_sidtrace(dst, csv, seconds=65)
        print(f"ready {name}: {dst}  oracle {csv}")


if __name__ == "__main__":
    main()
