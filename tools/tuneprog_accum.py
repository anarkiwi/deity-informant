#!/usr/bin/env python3
"""T1: the bounded accumulators of a decompiled tune, from its output directory.

Reads the certified program, replays it for its cell histories
(:mod:`deity_informant.tuneprog.history`) and writes ``tuneprog.T1.json`` beside
S6 -- one section 5 ``Acc`` record per producer, each with the interval it claims
and the recurrence replayed tick for tick. Not a pipeline stage: no artefact of
the tuneprog moves, and a residue is a stated refusal, never an approximation.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # keep process_time() a measure of this work only
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.tuneprog import accum, pipeline  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.recover import Names  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402

COLS = "  %-6s %-9s %-24s %-10s %-9s %-18s %-10s %s"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tuneprog_accum.py", description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, action="append", help="a pipeline output directory")
    ap.add_argument("--calls", type=int, help="ticks to replay (default: the trace's)")
    args = ap.parse_args(argv)
    rc = 0
    for name in args.out:
        out = Path(name)
        t0 = time.process_time()
        prog = Tuneprog.load(out / "tuneprog.S4.json")
        s6 = json.loads((out / "tuneprog.S6.json").read_text())
        regions = json.loads((out / "regions.json").read_text())
        t0doc = json.loads((out / "tuneprog.T0.json").read_text())
        cert = out / "certificate.json"
        doc = json.loads(cert.read_text()) if cert.exists() else None
        hist = history(prog, Trace.load(out), s6, calls=args.calls, regions_doc=regions)[0]
        view = pipeline.present(prog)[0]
        got = accum.document(view, Names.from_dict(s6), t0doc, hist, doc)
        secs = time.process_time() - t0
        (out / "tuneprog.T1.json").write_text(json.dumps(got, indent=1))
        for a in got["accs"]:
            print(
                COLS
                % (
                    a["id"],
                    a["target"]["register"],
                    a["cell"]["name"],
                    a["delta"]["kind"],
                    a["bound"]["from"],
                    a["policy"],
                    a["scope"],
                    "replay %d/%d" % (a["verify"]["divergences"], a["verify"]["ticks"]),
                )
            )
        for r in got["refusals"]:
            print("  refusal %-24s %-9s %s" % (r["cell"], r["clause"], r["site"]))
        kinds = Counter("%s/%s" % (a["policy"], a["bound"]["from"]) for a in got["accs"])
        bad = sum(a["verify"]["divergences"] for a in got["accs"])
        rc |= 1 if bad else 0
        print(
            "%s: %d ticks, %d accs %s, %d refusals, %d divergences, %.1f s"
            % (
                out.name,
                got["horizon"]["ticks"],
                len(got["accs"]),
                dict(sorted(kinds.items())),
                len(got["refusals"]),
                bad,
                secs,
            )
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
