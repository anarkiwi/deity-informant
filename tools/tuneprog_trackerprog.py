#!/usr/bin/env python3
"""T3: the trackerprog of a decompiled tune, lifted from its data, rendered and certified.

Reads the certified program and its T0/T1/T2 documents from the output directory,
lifts the trackerprog from the program's tables and its fetch regions, replays it
on the universal player and compares the section 2 observable against the
verifier's over the whole horizon. Writes ``trackerprog.json`` and
``trackerprog.md`` only when emitted; ``trackerprog.certificate.json`` always.
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
from deity_informant.trackerprog import certify, emit, lift  # noqa: E402
from deity_informant.trackerprog.refuse import Refusal  # noqa: E402
from deity_informant.tuneprog import accum, pipeline, provenance  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.recover import Names  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402


def documents(out, prog, view, st, names, hist, ver, cert):
    """T0, T1 and T2, read beside S6 or lifted now."""
    t0p, t1p = out / "tuneprog.T0.json", out / "tuneprog.T1.json"
    t0 = json.loads(t0p.read_text()) if t0p.exists() else provenance.document(view, st, names)
    if t1p.exists():
        t1 = json.loads(t1p.read_text())
    else:
        t1 = accum.document(view, names, t0, hist, cert, obs=ver.obs)
    t2 = lift.document(view, names, hist, cert)
    (out / "tuneprog.T2.json").write_text(json.dumps(t2, indent=1))
    return t0, t1, t2


def run(out, calls=None):
    """``(certificate, trackerprog, refusals, numbers, seconds)`` of one directory."""
    t0s = time.process_time()
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    s6 = json.loads((out / "tuneprog.S6.json").read_text())
    regions = json.loads((out / "regions.json").read_text())
    cert = json.loads((out / "certificate.json").read_text())
    trace = Trace.load(out)
    hist, ver = history(prog, trace, s6, calls=calls, regions_doc=regions, obs=True)
    view, st, _n = pipeline.present(prog)
    names = Names.from_dict(s6)
    t0, t1, t2 = documents(out, prog, view, st, names, hist, ver, cert)
    tp, refusals, _rec = emit.lift(prog, view, names, t0, t1, t2, cert, trace.inputs)
    got, trap, rendered = emit.replay(tp, len(ver.obs))
    doc = certify.certificate(
        prog.meta.get("name"), cert, ver.obs, got, refusals, tp["score"]["end"], trap, tp, rendered
    )
    refusals = [Refusal(**r) for r in doc["refusals"]]
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
        doc, tp, refusals, n, secs = run(out, args.calls)
        for r in doc["refusals"]:
            print("  refusal %-22s %-40s %s" % (r["why"], r["cell"][:40], r["site"]))
        ins = tp["instruments"]
        print(
            "%s: %d ticks, emitted %s, divergence %s, %d refusals, instruments %s, "
            "accs %d, producers %d, %s, %.1f s"
            % (
                out.name,
                doc["ticks"],
                doc["emitted"],
                doc["divergence"],
                len(refusals),
                ins and ins["entries"],
                len(tp["accs"]),
                len(emit.stores(tp)),
                {k: v for k, v in n.items() if k != "tuneprog"},
                secs,
            )
        )
        rc |= 0 if doc["emitted"] else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
