"""Stage 2's role precondition: the witnessed cell updates, classified by role.

The five role keywords come from the plan's read-forward argument, not from the
stage-1 catalog, which accounted dataflow shapes and never update shapes. A
common residual shape names a missing role before the grammar freezes them.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import _sweep
import lift_residue
import storage_census
from exemplars import EXEMPLARS

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/role_census.py                                 # every exemplar, full song
  python tools/role_census.py --tunes Hubbard_Rob/Commando --show 20
  python tools/role_census.py --frames 1500 -o out/role_census.json"""


def reading(prog):
    """``(role counts, shape counts, residue records)`` over one frame program."""
    from deity_informant import frameproc, roles

    got, shapes, residue, _bounds = roles.census(prog)
    out = [
        {
            "shape": lift_residue._skeleton(u.value, 3),
            "text": frameproc._fmt(u.value)[:96],
            "field": u.field,
            "base": "$%04X" % u.base,
            "entry": "$%04X" % u.entry,
            "site": "$%04X" % u.site,
        }
        for u in residue
    ]
    roled = Counter(r or "residue" for r in got.values())
    return roled, Counter(s for ss in shapes.values() for s in ss if s is not None), out


def one(entry, frames):
    """One tune's role reading, or the exception that stopped it."""
    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        _model, prog, nframes = storage_census.build(entry, frames)
        roled, shapes, residue = reading(prog)
        return {
            **_sweep.row_head(entry),
            "build_s": round(time.monotonic() - t0, 1),
            "frames": nframes,
            "cells": sum(roled.values()),
            "roles": dict(roled),
            "shapes": dict(shapes),
            "residue": residue,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _merge(done):
    """``(role counts, shape counts, residue by shape)`` over the clean rows."""
    roled, shapes, byshape = Counter(), Counter(), defaultdict(list)
    for r in done:
        roled.update(r["roles"])
        shapes.update(r["shapes"])
        for g in r["residue"]:
            byshape[g["shape"]].append({**g, "tune": r["tune"]})
    return roled, shapes, byshape


def _print(done, roled, shapes, byshape, show):
    """Per-tune cells and residue, the role histogram, then every residual shape."""
    from deity_informant import roles

    print("\n%-46s %6s %8s %9s" % ("tune", "cells", "residue", "un-roled"))
    for r in done:
        print(
            "%-46s %6d %8d %9d"
            % (r["tune"], r["cells"], len(r["residue"]), r["roles"].get("residue", 0))
        )
    total = sum(roled.values())
    print("\n%-12s %8s %7s" % ("role", "cells", "share"))
    for name in roles.ROLES + ("residue",):
        print("%-12s %8d %6.1f%%" % (name, roled[name], 100.0 * roled[name] / max(total, 1)))
    print("\n%-12s %8s" % ("shape", "cells"))
    for name, n in shapes.most_common():
        print("%-12s %8d" % (name, n))
    hits = sum(len(v) for v in byshape.values())
    print("\nresidual updates: %d over %d shape(s)" % (hits, len(byshape)))
    for shape in sorted(byshape, key=lambda k: -len(byshape[k])):
        rows = byshape[shape]
        print("  %5d  %s" % (len(rows), shape))
        for g in rows[:show]:
            print(
                "         %-30s %-12s %s @ %s  %s"
                % (g["tune"], g["field"], g["base"], g["site"], g["text"])
            )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the exemplars")
    ap.add_argument("--frames", default="full", help="frame cap, or 'full' (default full)")
    ap.add_argument("--show", type=int, default=4, help="sites listed per residual shape")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "role_census.json"))
    args = ap.parse_args()

    frames = None if args.frames == "full" else int(args.frames)
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else list(EXEMPLARS))
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(one, [(e, frames) for e in tunes]))
    done = sorted((r for r in rows if "error" not in r), key=lambda r: r["tune"])
    roled, shapes, byshape = _merge(done)
    out = {
        "tunes": len(done),
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        "frames": args.frames,
        "cells": sum(roled.values()),
        "role_counts": dict(roled),
        "shape_counts": dict(shapes),
        "residue": sum(len(v) for v in byshape.values()),
        "residue_shapes": {k: len(v) for k, v in byshape.items()},
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("%d tunes, %d refused, %.1fs" % (len(done), len(out["refused"]), out["wall_s"]))
    _print(done, roled, shapes, byshape, args.show)
    sys.exit(1 if out["refused"] else 0)


if __name__ == "__main__":
    main()
