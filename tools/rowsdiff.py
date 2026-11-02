"""Compare two ``wpcorpus`` rows files song by song.

``rowsdiff.py BEFORE AFTER``: for every song of AFTER also in BEFORE, its outcome in each
(``exact``, the mismatch kind, ``skip``/``error``), as a markdown table of the changed songs
and a count of each ``before -> after`` transition.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter


def outcome(row):
    """A row's outcome: ``exact``, ``mismatch <kind>``, ``skip <why>``, ``error`` or ``-``."""
    if row is None:
        return "-"
    if "error" in row:
        return "error"
    if "skip" in row:
        return "skip " + row["skip"]
    if row.get("exact"):
        return "exact"
    if row.get("mismatch"):
        return "mismatch " + str(row["mismatch"][1])
    return "-"


def rows(path):
    """``{(path, song): row}`` of a rows file, later rows winning."""
    with open(path, encoding="utf-8") as f:
        return {(r["path"], r["song"]): r for r in map(json.loads, f)}


def compare(before, after):
    """``(changed, transitions)``: ``[(path, song, before, after, after mismatch)]`` for songs
    whose outcome or mismatch changed, and the count of each ``(before, after)`` outcome."""
    changed, moves = [], Counter()
    for key, r in sorted(after.items()):
        b = before.get(key)
        ob, oa = outcome(b), outcome(r)
        moves[ob, oa] += 1
        if ob != oa or (b or {}).get("mismatch") != r.get("mismatch"):
            changed.append((*key, ob, oa, r.get("mismatch")))
    return changed, moves


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before")
    ap.add_argument("after")
    args = ap.parse_args(argv)
    changed, moves = compare(rows(args.before), rows(args.after))
    print("| path | song | before | after | mismatch |\n|---|---|---|---|---|")
    for p, s, b, a, m in changed:
        print(f"| {p} | {s} | {b} | {a} | {m} |")
    print()
    for (b, a), n in moves.most_common():
        print(f"{n:6d}  {b} -> {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
