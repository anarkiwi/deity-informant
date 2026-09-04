#!/usr/bin/env python3
"""The pipeline's later passes over one bound object: L4 to L5 to L6, certified.

The binding of #352 is L3 with one L4 shape -- the score materialised -- so this
takes its object through selection and canonicalisation, renders the result
against the tune's own player and prints one line per level.

Usage::

    tools/trackerprog_passes.py --out out/lift-b6/commando-song1 \
        --sid $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid --certify
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# pylint: disable=wrong-import-position
from deity_informant.trackerprog import sizes  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.passes import l5_select, l6_canon  # noqa: E402
from deity_informant.trackerprog.passes.ir import Level  # noqa: E402

from tuneprog_trackerprog import reference  # noqa: E402


def measure(obj):
    """What one level's object is: its size, its rows, its records and its cells."""
    return {
        "xz": sizes.xz(sizes.compact(obj)),
        "streams": len(obj["streams"]),
        "rows": sum(len(s.get("rows", ())) for s in obj["streams"].values()),
        "accs": len(obj["accs"]),
        "cells": len(obj["state0"]["cells"]) + len(obj["state0"].get("globals", {})),
    }


def run(out, sid=None, certify=False, ticks=None):
    """``(levels, report)``: the bound object taken to L6, beside the level it was."""
    t0 = time.process_time()
    obj = json.loads((Path(out) / "trackerprog.lift.json").read_text())
    n = ticks or obj["meta"]["horizon"]
    l4 = Level(4, obj=obj)
    l5 = l5_select.select_level(l4)
    l6 = l6_canon.canonicalise(l5)
    rep = {
        "levels": {"L%d" % lv.n: measure(lv.obj) for lv in (l4, l5, l6)},
        "covered": sorted(l5.facts["covered"]),
        "selected": sorted(l5.facts["selected"]),
        "covering_xz": l5.facts["xz"],
        "canon": {k: l6.facts[k] for k in ("merged", "propagated", "dropped", "renamed")},
        "accs": sorted(l6.obj["accs"]),
        "seconds": round(time.process_time() - t0, 1),
    }
    (Path(out) / "trackerprog.passes.json").write_text(json.dumps(l6.obj, indent=1))
    if certify and sid:
        want = reference(sid, int(obj["meta"]["song"] or 0), n)
        rep["certificate"] = {
            "L%d"
            % lv.n: {
                k: v
                for k, v in attest(lv.obj, want, n).items()
                if k in ("ticks", "writes", "divergence", "identical_ticks")
            }
            for lv in (l4, l5, l6)
        }
    (Path(out) / "trackerprog.passes.report.json").write_text(json.dumps(rep, indent=1))
    return (l4, l5, l6), rep


def main(argv=None):
    ap = argparse.ArgumentParser(prog="trackerprog_passes.py", description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, action="append", help="a pipeline output directory")
    ap.add_argument("--sid", help="the tune, for the PcodeVM oracle")
    ap.add_argument("--ticks", type=int, help="ticks to render (default: the object's horizon)")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    rc = 0
    for name in a.out:
        _levels, rep = run(Path(name), a.sid, a.certify, a.ticks)
        cert = rep.get("certificate") or {}
        for key in ("L4", "L5", "L6"):
            m, c = rep["levels"][key], cert.get(key, {})
            print(
                "%s %s: %d streams, %d rows, %d accs, %d cells, xz %d, divergence %s"
                % (
                    Path(name).name,
                    key,
                    m["streams"],
                    m["rows"],
                    m["accs"],
                    m["cells"],
                    m["xz"],
                    c.get("divergence", "not certified"),
                )
            )
            rc |= 1 if c.get("divergence") else 0
        print(
            "%s: covered %s, selected %s, covering xz %s, merged %d, propagated %s, %.1f s"
            % (
                Path(name).name,
                rep["covered"] or "none",
                rep["selected"] or "none",
                rep["covering_xz"],
                rep["canon"]["merged"],
                list(rep["canon"]["propagated"]) or "none",
                rep["seconds"],
            )
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
