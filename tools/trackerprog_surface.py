#!/usr/bin/env python3
"""The L0 to L6 surface over the certified exemplars: how far each tune gets, and why.

A tune stops at its first red level; each red cell carries a cause derived from
the level's own refusal, and tunes that share a cause close together.  Run as
``tools/trackerprog_surface.py --out out/surface/surface.json <output dirs>``.
"""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# pylint: disable=wrong-import-position
from trackerprog_passes import from_l0  # noqa: E402

LEVELS = ("L1", "L2", "L3", "L4", "L5", "L6")


def cause(got):
    """The cause one red level states: its own refusal, and the region it lies in."""
    why = got.get("why") or ""
    if got.get("pass") == "diverged":
        return "diverges at tick %s" % (why.partition("'tick': ")[2].partition(",")[0] or "?")
    where = Counter(g["region"] for g in got.get("unstatable") or ()).most_common(1)
    region = " (%s)" % where[0][0] if where else ""
    if "computed address" in why:
        return "computed address" + region
    if why.startswith("Unlowerable: "):
        return why[len("Unlowerable: ") :] + region
    return why.partition(": ")[2] or why or "?"


def one(out):
    """``(name, row)``: how far one tune's planes get, and the cause where they stop."""
    name = Path(out).name
    try:
        _levels, rep = from_l0(out)
    except Exception as x:  # pylint: disable=broad-except
        return name, {"ticks": None, "reached": "L0", "cause": str(x) or type(x).__name__}
    row = {"ticks": rep.get("ticks"), "levels": {}}
    for lvl in LEVELS:
        got = rep["levels"].get(lvl)
        if got is None:
            row["levels"][lvl] = None
        elif got.get("pass") == "ok":
            row["levels"][lvl] = "ok"
        else:
            row["levels"][lvl] = row["cause"] = cause(got)
            row["reached"] = lvl
            break
    row.setdefault("reached", "L6")
    return name, row


def _cell(row, lvl):
    """One cell of the table: green, the cause it stopped on, or never reached."""
    got = (row.get("levels") or {}).get(lvl)
    return "**ok**" if got == "ok" else ("—" if got is None else got)


def table(rows):
    """The surface as one markdown table, a row a tune and a column a level."""
    out = ["| tune | ticks | " + " | ".join(LEVELS) + " |", "| --- " * (len(LEVELS) + 2) + "|"]
    for name, r in rows:
        cells = (
            [r["cause"]] + ["—"] * (len(LEVELS) - 1)
            if not r.get("levels")
            else [_cell(r, lvl) for lvl in LEVELS]
        )
        out.append("| %s | %s | %s |" % (name, r.get("ticks") or "—", " | ".join(cells)))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="+", help="certified output directories")
    ap.add_argument("--out", help="write the surface as JSON here")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        rows = list(ex.map(one, args.dirs))
    print(table(rows))
    print("\n%d of %d tunes reach L6." % (sum(r["reached"] == "L6" for _n, r in rows), len(rows)))
    for why, n in Counter(r["cause"] for _n, r in rows if r["reached"] != "L6").most_common():
        print("  %2d x %s" % (n, why))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(dict(rows), indent=1, sort_keys=True) + "\n"
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
