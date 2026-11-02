"""Recover the memory layout of a PSID tune's play routine (see ``docs/layout.md``).

``python tools/layout.py SIDFILE [--song N] [--calls N] [--json OUT]``: init on the reference,
``N`` play calls traced with effective-address evidence, the lift, and the layout map.
"""

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

# pylint: disable=wrong-import-position
import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deity_informant import layout, trace, wholeprog  # noqa: E402
from smclabel import sidfile  # noqa: E402 pylint: disable=wrong-import-order
from wpcorpus import flat_init, header, volatile_of  # noqa: E402 pylint: disable=wrong-import-order


def song_layout(data, song=1, calls=1000, max_cycles=2_000_000, progress=True):
    """The layout map (``layout.build``) of a PSID file's play routine for ``song``, with the
    ``tune`` header fields, the init's ``init_halt``, and the play calls' executed ``starts`` and
    ``accessed`` data addresses."""
    hdr, tune = header(data), sidfile(data)
    if hdr is None or tune is None or hdr["kind"] != "PSID" or not hdr["play"]:
        raise ValueError("not a PSID tune with a play routine")
    row = hdr | {"song": song, "init": tune[2]}
    state, r = flat_init(tune[1], row, max_cycles)
    vol, play = volatile_of(row), hdr["play"]
    facts = trace.collect(
        state,
        lambda s: [s.call(play) for _ in range(calls)],
        max_cycles,
        progress=progress,
        total=calls,
        ea=True,
    )
    prog = wholeprog.lift(state, [play], volatile=vol, facts=facts)
    tune = {k: hdr[k] for k in ("init", "play", "songs")} | {"song": song, "calls": calls}
    m = layout.build(prog, facts, state, vol)
    return m | {
        "tune": tune,
        "init_halt": r["halt"],
        "starts": sorted(facts.starts),
        "accessed": accessed(facts.ea),
    }


def accessed(ea):
    """Maximal ``[lo, hi]`` runs of the data addresses in effective-address evidence (an
    interval whose members were dropped counts whole)."""
    pts = set()
    for ops in ea.values():
        for rec in ops.values():
            a = trace.EA.strided(rec, "addr")
            pts.update(a.values() or range(a.lo, a.hi + 1))
    runs = []
    for v in sorted(pts):
        if runs and runs[-1][1] == v - 1:
            runs[-1][1] = v
        else:
            runs.append([v, v])
    return runs


def summary(m):
    """Region counts by kind (devices apart) and the conflict count."""
    kinds = Counter(r["kind"] + (" (io)" if r.get("io") else "") for r in m["regions"])
    return dict(sorted(kinds.items())) | {"conflicts": len(m["conflicts"])}


def describe(r):
    """One line for a region."""
    out = "%-14s $%04X" % (r["kind"] + ("*" if r.get("io") else ""), r["base"])
    if "extent" in r:
        out += "-$%04X" % r["extent"][1]
    for k in ("stride", "count", "index"):
        if k in r:
            out += " %s=%s" % (k, r[k])
    out += " fields=" + ",".join("%d:%d" % (f["offset"], f["width"]) for f in r["fields"])
    if "words" in r:
        out += " words=%s" % r["words"]
    if r.get("targets"):
        t = r["targets"]
        out += " targets=%s%s" % (",".join("$%04X" % v for v in t[:4]), "..." if len(t) > 4 else "")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sidfile")
    ap.add_argument("--song", type=int, default=1)
    ap.add_argument("--calls", type=int, default=1000)
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    ap.add_argument("--json", help="write the layout map here")
    args = ap.parse_args(argv)
    m = song_layout(Path(args.sidfile).read_bytes(), args.song, args.calls, args.max_cycles)
    for r in m["regions"]:
        print(describe(r))
    for c in m["conflicts"]:
        print("conflict", json.dumps(c))
    print(json.dumps(summary(m)))
    if args.json:
        Path(args.json).write_text(json.dumps(m), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
