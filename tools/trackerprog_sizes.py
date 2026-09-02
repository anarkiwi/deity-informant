#!/usr/bin/env python3
"""Section 9.1's table: every certified object against its tune's own load band.

The denominator is the binary that played the tune, not ``tuneprog.md``.  Builds
come from the poison registry, so the set of tunes and the set of certified
subtunes are the same ones every other measurement in the layer uses.
"""

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import trackerprog_poison as TP  # noqa: E402
from deity_informant.trackerprog import sizes  # noqa: E402
from deity_informant.tuneprog import tunes  # noqa: E402


def _one(args):
    name, cache = args
    return name, TP.build_object(name, cache)


def rows(names, cache):
    """One row per tune: its band, and every certified subtune's object."""
    with ProcessPoolExecutor() as ex:
        objs = dict(ex.map(_one, [(n, cache) for n in names]))
    by = defaultdict(list)
    for name in names:
        by[TP.BUILD[name].tune].append(objs[name])
    out = []
    for tune, group in by.items():
        path = tunes.resolve(tune)
        if path is None:
            raise SystemExit("%s unavailable (no HVSC tree, no cache, offline)" % tune)
        out.append(sizes.tune_row(Path(path).read_bytes(), group, tune))
    return sorted(out, key=lambda r: -r["ratio"])


def one_row(obj, tune):
    """One lifted object against its own tune's load band, off the registry."""
    path = tunes.resolve(tune)
    if path is None:
        raise SystemExit("%s unavailable (no HVSC tree, no cache, offline)" % tune)
    return sizes.tune_row(Path(path).read_bytes(), [obj], tune)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="trackerprog_sizes.py", description=__doc__.splitlines()[0])
    ap.add_argument("--builds", default="all", help="a build set (default: all thirty)")
    ap.add_argument("--cache", default=str(TP.DEFAULT_CACHE), help="object cache directory")
    ap.add_argument("--object", help="one object file, against its own tune's band")
    ap.add_argument("--tune", help="the tune the --object plays (default: its meta.tune)")
    ap.add_argument("--halves", action="store_true", help="the score half against the rest")
    ap.add_argument("--json", action="store_true", help="the rows as JSON")
    a = ap.parse_args(argv)
    if a.object:
        obj = json.loads(Path(a.object).read_bytes())
        got = one_row(obj, a.tune or obj["meta"]["tune"])
        print(json.dumps(got, indent=1) if a.json else sizes.line(got))
        return 0
    names = TP.resolve(a.builds)
    if a.halves:
        got = {n: sizes.halves(TP.build_object(n, a.cache)) for n in names}
        if a.json:
            print(json.dumps(got, indent=1))
            return 0
        print(
            "%-24s %9s %8s %9s %8s  %s" % ("build", "score", "score xz", "rest", "rest xz", "score")
        )
        for n, h in got.items():
            share = 100 * h["score_xz"] / (h["score_xz"] + h["rest_xz"])
            print(
                "%-24s %9d %8d %9d %8d  %4.0f%%"
                % (n, h["score_raw"], h["score_xz"], h["rest_raw"], h["rest_xz"], share)
            )
        return 0
    got = rows(names, a.cache)
    if a.json:
        print(json.dumps(got, indent=1))
        return 0
    print("%-32s %6s %8s %9s  %s" % ("tune", "cert", "band xz", "object xz", "ratio"))
    for r in got:
        print(sizes.line(r))
    whole = [r for r in got if r["songs"] == r["certified"]]
    print(
        "\n%d of %d tunes certified whole; ratios %.2fx-%.2fx"
        % (len(whole), len(got), min(r["ratio"] for r in whole), max(r["ratio"] for r in whole))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
