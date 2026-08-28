#!/usr/bin/env python3
"""T2: the cursors, streams, pitch table and materialised score of a decompiled tune.

Reads the certified program from its output directory, replays it for its cell
histories (:mod:`deity_informant.tuneprog.history`) and writes ``tuneprog.T2.json``
beside S6. Not a pipeline stage: no tuneprog artefact moves, and what the cursor
grammar cannot express is a stated refusal, never an approximation.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.trackerprog import lift  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.recover import Names  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402


def load(out, calls=None):
    """``(document, seconds)`` of one output directory."""
    t0 = time.process_time()
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    s6 = json.loads((out / "tuneprog.S6.json").read_text())
    regions = json.loads((out / "regions.json").read_text())
    cert = out / "certificate.json"
    doc = json.loads(cert.read_text()) if cert.exists() else None
    hist, _ver = history(prog, Trace.load(out), s6, calls=calls, regions_doc=regions)
    view = pipeline.present(prog)[0]
    return lift.document(view, Names.from_dict(s6), hist, doc), time.process_time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tuneprog_score.py", description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, action="append", help="a pipeline output directory")
    ap.add_argument("--calls", type=int, help="ticks to replay (default: the trace's)")
    args = ap.parse_args(argv)
    rc = 0
    for name in args.out:
        out = Path(name)
        got, secs = load(out, args.calls)
        (out / "tuneprog.T2.json").write_text(json.dumps(got, indent=1))
        print(lift.summary(got))
        for r in got["refusals"]:
            print("  refusal %-24s %-22s %s" % (r["cell"], r["why"], r["site"]))
        rc |= 1 if got["refusals"] else 0
        print("%s: %d ticks, %.1f s" % (out.name, got["horizon"]["ticks"], secs))
    return rc


if __name__ == "__main__":
    sys.exit(main())
