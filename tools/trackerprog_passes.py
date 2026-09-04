#!/usr/bin/env python3
"""The pipeline over one tune: L0 to L6 with a validation at every level.

``--l0`` takes the certified planes of an output directory through all six
passes; without it the binding of #352 -- which is L3 with one L4 shape -- is
taken through selection and canonicalisation.  Either way every level is
rendered and compared with the level before it, and one line a level is printed.

Usage::

    tools/trackerprog_passes.py --l0 --out out/lift-b6/commando-song1 \
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
from deity_informant.trackerprog import build, emit, record, region, sizes  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.passes import (  # noqa: E402
    ir,
    l1_structure,
    l2_phases,
    l3_roles,
    l4_specialise,
    l5_select,
    l6_canon,
    rir,
)
from deity_informant.trackerprog.passes.ir import Level  # noqa: E402
from deity_informant.trackerprog.shape import _channels, _rowblocks  # noqa: E402

from tuneprog_trackerprog import reference  # noqa: E402  # pylint: disable=wrong-import-order


def measure(obj):
    """What one level's object is: its size, its rows, its records and its cells."""
    return {
        "xz": sizes.xz(sizes.compact(obj)),
        "streams": len(obj["streams"]),
        "rows": sum(len(s.get("rows", ())) for s in obj["streams"].values()),
        "accs": len(obj["accs"]),
        "cells": len(obj["state0"]["cells"]) + len(obj["state0"].get("globals", {})),
    }


def fetchblocks(art):
    """The blocks of the tune's own fetch region: where the voice's pass is cut."""
    prog, proc = art["prog"], art["prog"].meta["tick_proc"]
    fetch, _ref = region.fetch(prog, emit.tables_of(art["t2"], art["view"], art["names"]))
    pat = emit.tables_of(art["t2"], art["view"], art["names"], ("pattern",))
    return _rowblocks(prog, proc, _channels(prog, proc, fetch, pat))


def planes(out, ticks=None):
    """The tune's certified planes, with the inputs the tick reads pinned to the image."""
    art = build.read(Path(out))
    img = record.interp.Player(art["prog"], region.Fetch()).run_init().m
    art["inputs"], _bad = build.pinned_inputs(art["prog"], img)
    return art, ticks or art["t2"]["horizon"]["ticks"]


def _decisions(art):
    """How many two-way decisions the structured tick makes at all."""
    p = art["prog"].procs[art["prog"].meta["tick_proc"]]
    return sum(
        1 for b in p.blocks.values() if type(b.term).__name__ == "If" and b.term.t != b.term.f
    )


def from_l0(out, ticks=None):
    """``(levels, report)``: the planes taken L0 to L6, each level validated."""
    art, n = planes(out, ticks)
    fb = fetchblocks(art)
    levels = [Level(0, art=art, prog=art["prog"], proc=art["prog"].meta["tick_proc"])]
    steps = (
        ("L1", lambda: l1_structure.structure(art, ticks=3)),
        ("L2", lambda: l2_phases.phases(levels[-1], fb, ticks=n)),
        ("L3", lambda: l3_roles.roles(levels[-1])),
        ("L4", lambda: l4_specialise.specialise(levels[-1], n)),
        ("L5", lambda: l5_select.select_level(levels[-1])),
        ("L6", lambda: l6_canon.canonicalise(levels[-1])),
    )
    rep = {"ticks": n, "levels": {}}
    for name, step in steps:
        t0 = time.process_time()
        try:
            levels.append(step())
        except Exception as x:  # pylint: disable=broad-except
            got = {"pass": "failed", "why": "%s: %s" % (type(x).__name__, x)}
            if name == "L2":
                got["unstatable"] = l2_phases.unstatable(levels[-1], fb)
                got["decisions"] = _decisions(art)
            rep["levels"][name] = got
            return levels, rep
        got = {"seconds": round(time.process_time() - t0, 1)}
        try:
            got.update(
                {
                    k: v
                    for k, v in ir.validate(levels[-2], levels[-1], n).items()
                    if k in ("ticks", "writes", "identical", "divergence")
                }
            )
            got["pass"] = "ok"
        except ir.Diverged as x:
            got.update({"pass": "diverged", "why": str(x)[:400]})
        if levels[-1].obj is not None:
            got.update(measure(levels[-1].obj))
        rep["levels"][name] = got
        if got["pass"] != "ok":
            return levels, rep
    return levels, rep


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
    ap.add_argument("--l0", action="store_true", help="run the whole pipeline from the planes")
    a = ap.parse_args(argv)
    if a.l0:
        return _l0(a)
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


def _l0(a):
    """The whole pipeline over each output directory, one line a level."""
    rc = 0
    for name in a.out:
        levels, rep = from_l0(Path(name), a.ticks)
        if a.certify and a.sid and levels[-1].obj is not None:
            want = reference(a.sid, int(levels[-1].obj["meta"]["song"] or 0), rep["ticks"])
            got = attest(levels[-1].obj, want, rep["ticks"], rir.render)
            rep["certificate"] = {k: got[k] for k in ("ticks", "writes", "divergence")}
        (Path(name) / "trackerprog.l0.report.json").write_text(json.dumps(rep, indent=1))
        for key, m in rep["levels"].items():
            print(
                "%s %s: %s%s"
                % (
                    Path(name).name,
                    key,
                    m["pass"],
                    (
                        ", %d ticks, %d writes%s"
                        % (
                            m["ticks"],
                            m["writes"],
                            (
                                ", %d streams, %d accs, xz %d" % (m["streams"], m["accs"], m["xz"])
                                if "streams" in m
                                else ""
                            ),
                        )
                        if m["pass"] == "ok"
                        else ", " + m.get("why", "")
                    ),
                )
            )
            rc |= 0 if m["pass"] == "ok" else 1
        cert = rep.get("certificate")
        if cert:
            print(
                "%s: divergence %s over %d ticks"
                % (Path(name).name, cert["divergence"], cert["ticks"])
            )
    return rc


if __name__ == "__main__":
    sys.exit(main())
