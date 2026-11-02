"""Check two ``wpcorpus`` runs agree apart from timing, and compare their per-song cost.

``corpusequiv.py BEFORE AFTER``: the songs of AFTER also in BEFORE whose rows differ in any
field but the timing ones (``*_ms``, ``timing.*_s``) or whose facts records differ, and the
distribution of BEFORE/AFTER ``song_ms`` per row mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import zstandard


def untimed(row):
    """``row`` without its timing fields."""
    out = {k: v for k, v in row.items() if not k.endswith("_ms")}
    if isinstance(out.get("timing"), dict):
        out["timing"] = {k: v for k, v in out["timing"].items() if not k.endswith("_s")}
    return out


def rows(path):
    """``{(path, song): row}`` of a rows file (the last row of a song wins)."""
    with open(path, encoding="utf-8") as f:
        return {(r["path"], r["song"]): r for r in map(json.loads, f)}


def facts(path):
    """``{(path, song): record}`` of a compressed facts file."""
    out = {}
    if not Path(path).exists():
        return out
    with open(path, "rb") as f:
        text = zstandard.ZstdDecompressor().stream_reader(f).read().decode()
    for line in text.splitlines():
        r = json.loads(line)
        out[(r["path"], r["song"])] = r
    return out


def mode(row):
    return row.get("mode") or ("irq" if "irq" in row else "other")


def compare(before, after):
    """``(differing keys with their fields, {mode: [speedups]}, compared count)``."""
    rb, ra = rows(Path(before) / "rows.jsonl"), rows(Path(after) / "rows.jsonl")
    fb, fa = facts(Path(before) / "facts.jsonl.zst"), facts(Path(after) / "facts.jsonl.zst")
    bad, speed, n = [], defaultdict(list), 0
    for key, a in ra.items():
        b = rb.get(key)
        if b is None:
            continue
        n += 1
        ub, ua = untimed(b), untimed(a)
        fields = sorted(k for k in set(ub) | set(ua) if ub.get(k) != ua.get(k))
        if fb.get(key) != fa.get(key):
            fields.append("facts")
        if fields:
            bad.append((key, fields))
        if b.get("song_ms") and a.get("song_ms"):
            speed[mode(a)].append(b["song_ms"] / a["song_ms"])
    return bad, speed, n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before")
    ap.add_argument("after")
    args = ap.parse_args(argv)
    bad, speed, n = compare(args.before, args.after)
    print(f"compared {n} songs, {len(bad)} differ")
    for key, fields in bad[:50]:
        print(f"  {key[0]} #{key[1]}: {', '.join(fields)}")
    for m, xs in sorted(speed.items()):
        q = np.percentile(xs, [10, 50, 90])
        print(f"{m}: n={len(xs)} speedup p10 {q[0]:.2f} median {q[1]:.2f} p90 {q[2]:.2f}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
