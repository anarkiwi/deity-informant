"""Run the survey tracer over a stratified HVSC sample in parallel.

    python tools/survey/run.py --hvsc /path/C64Music --results results.csv \
        --cap 30 --seconds 60 --out survey.jsonl.gz [--jobs 64] [--all]

Sample: up to ``--cap`` tunes per SIDId family (seeded), or ``--all``. One JSON
line per tune; per-tune wall timeout via SIGALRM.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import random
import re
import signal
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tracer import trace_tune  # noqa: E402

_ARGS = None


def _songlengths(hvsc):
    """md5 -> [seconds per subtune] from DOCUMENTS/Songlengths.md5."""
    out = {}
    p = Path(hvsc) / "DOCUMENTS" / "Songlengths.md5"
    if not p.exists():
        return out
    for line in p.read_text(encoding="latin-1").splitlines():
        if "=" not in line or line.startswith("["):
            continue
        md5, rest = line.split("=", 1)
        secs = [int(m.group(1)) * 60 + int(m.group(2)) for m in re.finditer(r"(\d+):(\d+)", rest)]
        out[md5.strip()] = secs
    return out


def _timeout(signum, frame):
    raise TimeoutError("tune timeout")


def _init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _one(item):
    rel, family, secs = item
    path = Path(_ARGS.hvsc) / rel
    t0 = time.time()
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(_ARGS.timeout)
    try:
        data = path.read_bytes()
        r = trace_tune(data, seconds=_ARGS.seconds)
    except BaseException as e:  # noqa: BLE001 - survey: record, don't crash
        r = {"error": "trace:%s:%s" % (type(e).__name__, str(e)[:80])}
    finally:
        signal.alarm(0)
    r["path"] = rel
    r["family"] = family
    r["songlength"] = secs
    r["wall"] = round(time.time() - t0, 2)
    return r


def _sample(results, cap, seed, all_):
    by = defaultdict(list)
    for row in csv.DictReader(open(results, encoding="utf-8")):
        by[row["player"]].append(row["path"])
    rng = random.Random(seed)
    items = []
    for fam, paths in sorted(by.items()):
        paths = sorted(paths)
        if not all_ and len(paths) > cap:
            paths = rng.sample(paths, cap)
        items.extend((p, fam) for p in paths)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hvsc", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--jobs", type=int, default=max(1, os.cpu_count() - 4))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    global _ARGS  # noqa: PLW0603
    _ARGS = ap.parse_args()
    lengths = _songlengths(_ARGS.hvsc)
    items = _sample(_ARGS.results, _ARGS.cap, _ARGS.seed, _ARGS.all)
    if _ARGS.limit:
        items = items[: _ARGS.limit]
    todo = []
    for rel, fam in items:
        p = Path(_ARGS.hvsc) / rel
        if not p.exists():
            continue
        md5 = hashlib.md5(p.read_bytes()).hexdigest()
        todo.append((rel, fam, lengths.get(md5)))
    print("tunes:", len(todo), file=sys.stderr)
    t0 = time.time()
    with gzip.open(_ARGS.out, "wt") as f, Pool(_ARGS.jobs, initializer=_init_worker) as pool:
        for i, r in enumerate(pool.imap_unordered(_one, todo, chunksize=2)):
            f.write(json.dumps(r) + "\n")
            if i % 200 == 0:
                print("%d/%d %.0fs" % (i, len(todo), time.time() - t0), file=sys.stderr)
    print("done %.0fs" % (time.time() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
