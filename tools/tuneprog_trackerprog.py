#!/usr/bin/env python3
"""T3: the trackerprog of a decompiled tune, rendered on the universal player and certified.

Reads the certified program and its T0/T1 documents from the output directory,
lifts T2 and T3, renders the trackerprog tick for tick and compares the section 2
observable against the verifier's over the whole horizon. Writes
``trackerprog.json`` and ``trackerprog.md`` only with no refusal;
``trackerprog.certificate.json`` always, its refusals each naming a cell.
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
from deity_informant.trackerprog import certify, emit, lift, player  # noqa: E402
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.recover import Names  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402


def run(out, calls=None):
    """``(certificate, trackerprog, refusals, numbers, seconds)`` of one output directory."""
    t0s = time.process_time()
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    s6 = json.loads((out / "tuneprog.S6.json").read_text())
    regions = json.loads((out / "regions.json").read_text())
    cert = json.loads((out / "certificate.json").read_text())
    t0 = json.loads((out / "tuneprog.T0.json").read_text())
    t1p = out / "tuneprog.T1.json"
    t1 = json.loads(t1p.read_text()) if t1p.exists() else None
    hist, ver = history(prog, Trace.load(out), s6, calls=calls, regions_doc=regions, obs=True)
    view = pipeline.present(prog)[0]
    names = Names.from_dict(s6)
    t2 = lift.document(view, names, hist, cert)
    (out / "tuneprog.T2.json").write_text(json.dumps(t2, indent=1))
    tp, refusals = emit.document(view, names, t0, t1, t2, hist, cert, ver.obs)
    refusals += list(t2["refusals"]) + [
        {"why": "unclassified update", "cell": r["cell"], "site": r["site"], "detail": r["clause"]}
        for r in (t1 or {}).get("refusals", ())
    ]
    got = player.Player(tp).render(len(ver.obs))
    doc = certify.certificate(
        prog.meta.get("name"), cert, ver.obs, got, refusals, tp["score"]["end"]
    )
    md = emit.render(tp)
    n = emit.numbers(tp, md)
    src = out / "tuneprog.md"
    if src.exists():
        n["tuneprog"] = emit.numbers_tuneprog(src.read_text(), view)
    doc["numbers"] = n
    (out / "trackerprog.certificate.json").write_text(json.dumps(doc, indent=1))
    if doc["emitted"]:
        (out / "trackerprog.json").write_text(json.dumps(emit.to_json(tp)))
        (out / "trackerprog.md").write_text(md)
    return doc, tp, refusals, n, time.process_time() - t0s


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tuneprog_trackerprog.py", description=__doc__.splitlines()[0]
    )
    ap.add_argument("--out", required=True, action="append", help="a pipeline output directory")
    ap.add_argument("--calls", type=int, help="ticks to replay (default: the trace's)")
    args = ap.parse_args(argv)
    rc = 0
    for name in args.out:
        out = Path(name)
        doc, _tp, refusals, n, secs = run(out, args.calls)
        for r in doc["refusals"]:
            print("  refusal %-22s %-40s %s" % (r["why"], r["cell"][:40], r["site"]))
        print(
            "%s: %d ticks, emitted %s, rendered equal %d/%d, %d refusals, %s, %.1f s"
            % (
                out.name,
                doc["ticks"],
                doc["emitted"],
                doc["rendered"]["ticks_equal"],
                doc["ticks"],
                len(refusals),
                {k: v for k, v in n.items() if k != "tuneprog"},
                secs,
            )
        )
        rc |= 1 if refusals or doc["divergence"] else 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
