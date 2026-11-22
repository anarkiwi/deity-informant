#!/usr/bin/env python3
"""Player names of SID files by Cadaver's SIDId signatures (``pysidtracker.sidid``).

``python tools/sidid.py PATH... [--json OUT] [--cfg CFG] [--jobs J]``: each PATH is a file or a
directory searched for ``*.sid``; prints ``path<TAB>player[,player...]`` (empty when
unidentified), every matching player in ``sidid.cfg`` order, SIDId's own pick first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pysidtracker import sidid
from tqdm import tqdm


def expand(paths):
    """The ``.sid`` files named by ``paths`` (directories searched recursively), resolved."""
    return [p.resolve() for p in sidid.sid_paths(paths)]


def identify(paths, cfg=None, jobs=None, progress=True):
    """``{path: [players]}`` for ``paths``, all matching players (first is SIDId's own pick)."""
    paths = [Path(p).resolve() for p in paths]
    hits = sidid.scan(paths, jobs or os.cpu_count(), cfg, multi=True)
    return dict(tqdm(hits, total=len(paths), desc="sidid", unit="file", disable=not progress))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help=".sid files or directories")
    ap.add_argument("--cfg", help="sidid.cfg (default: pysidtracker's bundled one)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--json", type=Path, help="also write {path: [players]} here")
    args = ap.parse_args(argv)
    res = identify(expand(args.paths), args.cfg, args.jobs)
    for p, names in res.items():
        print(f"{p}\t{','.join(names)}")
    if args.json:
        args.json.write_text(json.dumps({str(k): v for k, v in res.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
