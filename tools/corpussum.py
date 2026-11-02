"""Summarise a ``wpcorpus`` rows file as compact tables.

``corpussum.py ROWS [--paths FILE] [--diff OTHER]``: counts by kind, skip, mode and outcome,
the non-exact rows, speedup and time percentiles, stats coverage and the interrupt breakdown.
``--paths`` keeps the tune paths listed in FILE (one per line, as ``wpcorpus --paths``);
``--diff OTHER`` prints how each song's outcome moved from ROWS to OTHER.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rowsdiff  # noqa: E402 pylint: disable=wrong-import-position,import-error


def load(path, keep=None):
    """Rows of a rows file (later rows of a song win), only the paths in ``keep`` if given."""
    seen = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if keep is None or r["path"] in keep:
                seen[r["path"], r.get("song")] = r
    return list(seen.values())


def get(row, *keys):
    """``row[k0][k1]...`` or None when any step is missing or null."""
    for k in keys:
        row = row.get(k) if isinstance(row, dict) else None
    return row


def pcts(values):
    """``(n, p10, median, p90)`` of the finite values, or None when there are none."""
    a = np.array([v for v in values if v is not None], dtype=float)
    a = a[np.isfinite(a)]
    return (len(a), *np.percentile(a, (10, 50, 90))) if len(a) else None


def ratios(rows, num, den):
    """``num / den`` of each row's timing where both are present and den is positive."""
    n = np.array([get(r, "timing", num) or np.nan for r in rows], dtype=float)
    d = np.array([get(r, "timing", den) or np.nan for r in rows], dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return n / d


def table(title, header, body):
    """Print ``body`` rows under ``header`` as an aligned table."""
    cells = [list(map(str, header))] + [list(map(str, b)) for b in body]
    w = [max(map(len, col)) for col in zip(*cells)]
    print(f"\n{title}")
    for i, row in enumerate(cells):
        print("  ".join(c.ljust(n) for c, n in zip(row, w)).rstrip())
        if i == 0:
            print("  ".join("-" * n for n in w))


def counts(title, counter, total=None):
    """Print a Counter as a table, most common first, with its share of ``total``."""
    total = total or sum(counter.values()) or 1
    table(
        title,
        ["what", "n", "%"],
        [(k, n, f"{100 * n / total:.2f}") for k, n in counter.most_common()],
    )


def stats_cover(rows):
    """Counter of the guard and stack properties of rows with ``stats``."""
    c = Counter()
    for r in rows:
        s = r.get("stats")
        if not s:
            continue
        c["lifted"] += 1
        c["stack_private"] += bool(s.get("stack_private"))
        c["recursive"] += bool(s.get("recursive"))
        cg, sg = (s.get("code_guards") or 0) > 0, (s.get("stack_guards") or 0) > 0
        c["code_guards>0"] += cg
        c["stack_guards>0"] += sg
        c["no guards"] += not (cg or sg)
    return c


def summarize(rows):
    """Print every table for ``rows``."""
    lifted = [r for r in rows if r.get("exact") is not None]
    print(f"rows {len(rows)}  tunes {len({r['path'] for r in rows})}")
    counts("kind", Counter(r.get("kind") for r in rows))
    counts("skip", Counter(r.get("skip") or r.get("error") for r in rows if "outcome" not in r))
    counts("mode / outcome", Counter((r.get("mode"), r.get("outcome")) for r in rows))
    ex = sum(bool(r.get("exact")) for r in lifted)
    print(f"\nexact {ex} / {len(lifted)} lifted = {100 * ex / max(len(lifted), 1):.3f}%")
    bad = [r for r in lifted if not r["exact"]]
    counts(
        "non-exact (mode, outcome, stop)",
        Counter((r["mode"], r["outcome"], (r.get("stop") or [None] * 3)[2]) for r in bad),
    )
    table(
        "non-exact songs",
        ["path", "song", "outcome", "detail"],
        [
            (r["path"], r["song"], r["outcome"], r.get("mismatch") or r.get("stop"))
            for r in bad
            if r["outcome"] in ("mismatch", "interrupt")
        ],
    )
    speed = []
    for mode in sorted({r["mode"] for r in lifted}):
        sel = [r for r in lifted if r["mode"] == mode]
        for name, num in (("reference", "reference_s"), ("jennings", "jennings_s")):
            p = pcts(ratios(sel, num, "lifted_s"))
            if p:
                speed.append((mode, f"{name}/lifted", p[0], *(f"{x:.2f}" for x in p[1:])))
    table("speedup", ["mode", "ratio", "n", "p10", "median", "p90"], speed)
    cost = []
    for key in ("compile_ms", "lift_ms"):
        p = pcts([r.get(key) for r in lifted])
        if p:
            cost.append((key, p[0], *(f"{x:.2f}" for x in p[1:])))
    table("cost", ["what", "n", "p10", "median", "p90"], cost)
    counts("stats coverage", stats_cover(lifted), len(lifted))
    irq = [r for r in rows if r.get("irq")]
    counts(
        "irq (path, idle, jammed)",
        Counter((r["irq"].get("path"), r["irq"].get("idle"), r["irq"].get("jammed")) for r in irq),
    )
    print(f"\nbasic {sum(bool(r.get('basic')) for r in rows)}")


def diff(before, after):
    """Print the count of each ``before -> after`` outcome over the songs of ``after``."""
    _, moves = rowsdiff.compare(
        *({(r["path"], r.get("song")): r for r in x} for x in (before, after))
    )
    table(
        "outcome moves",
        ["before", "after", "n"],
        [(b, a, n) for (b, a), n in moves.most_common()],
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rows")
    ap.add_argument("--paths", help="file of tune paths, one per line")
    ap.add_argument("--diff", help="second rows file: outcome moves from ROWS to it")
    args = ap.parse_args(argv)
    keep = set(Path(args.paths).read_text(encoding="utf-8").split()) if args.paths else None
    rows = load(args.rows, keep)
    if args.diff:
        diff(rows, load(args.diff, keep))
    else:
        summarize(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
