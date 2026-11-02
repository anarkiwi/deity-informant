"""Run one HVSC song through the corpus pipeline in this process and print its row.
``python tools/wpsong.py ROOT PATH [--song N] [--calls N] [--out ROW.json]``: ``PATH`` relative
to ``ROOT``; ``--calls`` caps the planned calls (for profiling a long song)."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-position

_PHASES = ("facts_ms", "lift_ms", "compile_ms", "check_ms", "label_ms")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("path")
    ap.add_argument("--song", type=int, default=1)
    ap.add_argument("--calls", type=int, default=0, help="cap on planned calls (0: none)")
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    ap.add_argument("--label-calls", type=int, default=1000)
    ap.add_argument("--out", help="write the full row as JSON here")
    args = ap.parse_args(argv)
    root = Path(args.root)
    wpcorpus._worker(
        str(root / "DOCUMENTS" / "Songlengths.md5")
    )  # pylint: disable=protected-access
    row = wpcorpus.plan(str(root / args.path), args.path, 180.0, args.max_cycles)[args.song - 1]
    if args.calls:
        row["calls"] = min(row["calls"], args.calls)
    t = time.perf_counter()
    row, _ = wpcorpus.run_song(str(root / args.path), row, args.max_cycles, args.label_calls)
    wall = time.perf_counter() - t
    if args.out:
        Path(args.out).write_text(json.dumps(row), encoding="utf-8")
    phases = {k: round(row[k] or 0) for k in _PHASES if k in row}
    timing = row.get("timing") or {}
    timing = {k: round(v or 0, 3) for k, v in timing.items() if k.endswith("_s")}
    n = row.get("calls_run") or 1
    out = {"mode": row.get("mode", "irq" if "irq" in row else None), "calls": n,
           "wall_s": round(wall, 3), "us_per_call": round(1e6 * wall / n, 1)}  # fmt: skip
    print(json.dumps(out | phases | timing | {"error": row.get("error"), "skip": row.get("skip")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
