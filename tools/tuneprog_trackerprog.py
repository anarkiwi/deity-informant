#!/usr/bin/env python3
"""B6/B7: the trackerprog lifted from a tune's certified artefacts, rendered and certified.

Reads ``tuneprog.S4/S6/T0/T1/T2.json`` and ``certificate.json`` from a pipeline
output directory, derives the schedule (B6), lowers the tick outside the fetch
regions (B7) and writes ``trackerprog.json``/``.md`` -- an object with no
``program`` key that ``deity_informant/trackerprog/universal.py`` renders.

Usage::

    tools/tuneprog_trackerprog.py --out out/recert-main/commando-song1 \
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.trackerprog import assemble, build, printer, sizes  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402


def hints(path):
    """One datum a line, ``meta.commit_order = [...]``: what the lift cannot derive."""
    out = {}
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, _eq, val = line.partition("=")
        out[key.strip()] = json.loads(val)
    return out


def reference(sid, song, ticks):
    """The oracle: the tune's own player on the PcodeVM (section 2's second link)."""
    import struct

    from deity_informant.lifter import lift as _lift
    from deity_informant.vm import PcodeVM, run_sub

    d = Path(sid).read_bytes()
    off, org = struct.unpack(">H", d[6:8])[0], struct.unpack(">H", d[8:10])[0]
    body = d[off:]
    if org == 0:
        org, body = body[0] | body[1] << 8, body[2:]
    m = bytearray(0x10000)
    m[org : org + len(body)] = body
    init, play = struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]
    vm, cache = PcodeVM(m), {}
    vm.reg[0] = song
    run_sub(vm, init, cache, _lift)
    out = []
    for _ in range(ticks):
        vm.wlog = []
        run_sub(vm, play, cache, _lift)
        out.append([(r, v) for _c, r, v in vm.wlog])
        vm.cycles += 19656
    return out


def run(out, sid=None, ticks=None, hint=None, certify=False):
    """``(object, report)`` for one output directory, written beside its artefacts."""
    t0 = time.process_time()
    art = build.read(out)
    try:
        obj, report = assemble.lift(art, ticks=ticks, hints=hint)
    except assemble.Refused as x:
        doc = {"emitted": False, "refusals": [r.to_dict() for r in x.refusals]}
        (out / "trackerprog.lift.report.json").write_text(json.dumps(doc, indent=1))
        return None, doc
    md = printer.render(obj)
    report["seconds"] = round(time.process_time() - t0, 1)
    report["print"] = printer.numbers(md)
    report["sizes"] = {"xz": sizes.xz(sizes.compact(obj)), **sizes.halves(obj)}
    (out / "trackerprog.lift.json").write_text(json.dumps(obj, indent=1))
    (out / "trackerprog.lift.md").write_text(md)
    if certify and sid:
        cert = attest(obj, reference(sid, int(obj["meta"]["song"] or 0), obj["meta"]["horizon"]))
        cert["source"] = {"tune": obj["meta"]["tune"], "song": obj["meta"]["song"]}
        cert["refusals"] = report["refusals"]
        report["certificate"] = cert
        (out / "trackerprog.lift.certificate.json").write_text(json.dumps(cert, indent=1))
    (out / "trackerprog.lift.report.json").write_text(json.dumps(report, indent=1))
    return obj, report


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tuneprog_trackerprog.py", description=__doc__.splitlines()[0]
    )
    ap.add_argument("--out", required=True, action="append", help="a pipeline output directory")
    ap.add_argument("--sid", help="the tune, for the PcodeVM oracle")
    ap.add_argument("--ticks", type=int, help="ticks to lift (default: the certified horizon)")
    ap.add_argument("--hints", help="a hints file, one named datum a line")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    hint = hints(a.hints) if a.hints else None
    rc = 0
    for name in a.out:
        obj, rep = run(Path(name), a.sid, a.ticks, hint, a.certify)
        if obj is None:
            for r in rep["refusals"]:
                print("  refusal %-24s %-22s %s" % (r["why"], r["cell"], r["detail"]))
            print("%s: refused, no object emitted" % Path(name).name)
            rc |= 1
            continue
        cert = rep.get("certificate") or {}
        print(
            "%s: %d ticks, %d rows, %d accs, %d patterns, %d events, %d hints, %d refusals, "
            "xz %d, divergence %s, %.1f s"
            % (
                Path(name).name,
                obj["meta"]["horizon"],
                rep["rows"],
                rep["accs"],
                len(obj["score"]["patterns"]),
                sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
                len(hint or {}),
                len(rep["refusals"]),
                rep["sizes"]["xz"],
                cert.get("divergence", "not certified"),
                rep["seconds"],
            )
        )
        rc |= 1 if cert.get("divergence") else 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
