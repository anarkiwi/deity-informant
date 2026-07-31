"""Whole-cache tracker measurement: the value/trigger partition and every gate.

Runs the tracker over every cached HVSC ``.sid`` at its PSID start subtune in a
process pool, recording per tune the coverage partition, the evidence classes, the
refusal histogram and the gates. Writes out/tracker_measure.json.
"""

import json
import multiprocessing as mp
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HVSC = ROOT / ".oracle-cache" / "hvsc"
FRAMES = 200


def one(rel):
    """Coverage, refusals and gates for one cached tune, or the error that stopped it."""
    # pylint: disable=import-outside-toplevel
    from deity_informant import framelog, frameprog, frameval, tracker
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid, psid_songs

    out = {"name": rel}
    try:
        data = (HVSC / rel).read_bytes()
        mem, _load, init, play = load_psid(data)
        mem[0xD418] = 0x0F
        _songs, start = psid_songs(data)
        model, _ev = S.decompile(mem, init, play, FRAMES, start - 1)
        prog = frameprog.program(model)
        trace, walker = frameprog.iota(model, FRAMES)
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
        return out
    try:
        text = frameprog.dumps(prog)
        out["fixpoint"] = frameprog.dumps(frameprog.parse(text)) == text
        out["gate_fp"] = (
            framelog.diff(frameval.eval_fp(prog, trace, FRAMES), framelog.canonical(walker)) is None
        )
        diag = Counter()
        rendered, gt, cov, _lanes = tracker.render(prog, trace, FRAMES, diag)
        out.update(
            law=framelog.diff(rendered, gt) is None,
            interp=cov.interp,
            total=cov.total,
            planes={k: list(v) for k, v in cov.planes.items()},
            classes=cov.classes,
            triggers=list(cov.triggers),
            refusals=dict(diag),
        )
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
    return out


def tunes():
    """Every cached ``.sid``, by relpath."""
    return sorted(str(p.relative_to(HVSC)) for p in HVSC.rglob("*.sid"))


def summarize(res):
    """The corpus totals: the partition per plane, the classes, the gates, refusals."""
    ok = [r for r in res if "law" in r]
    planes, classes, refus = {}, {}, {}
    for r in ok:
        for p, (i, t) in r["planes"].items():
            a, b = planes.get(p, (0, 0))
            planes[p] = (a + i, b + t)
        for cls in (r["classes"] or {}).values():
            for k, n in cls.items():
                classes[k] = classes.get(k, 0) + n
        for k, n in (r.get("refusals") or {}).items():
            refus[k] = refus.get(k, 0) + n
    return {
        "tunes": len(res),
        "decompile": len(ok),
        "interp": sum(r["interp"] for r in ok),
        "total": sum(r["total"] for r in ok),
        "planes": planes,
        "classes": classes,
        "refusals": refus,
        "triggers": [sum(r["triggers"][i] for r in ok) for i in (0, 1)],
        "gate_fp": sum(1 for r in ok if r["gate_fp"]),
        "law": sum(1 for r in ok if r["law"]),
        "fixpoint": sum(1 for r in ok if r["fixpoint"]),
    }


def main(argv):
    """Measure the cache and write the JSON report."""
    out = Path(argv[1]) if argv[1:] else ROOT / "out" / "tracker_measure.json"
    workers = int(os.environ.get("DI_WORKERS") or min(mp.cpu_count(), 48))
    with mp.Pool(workers) as pool:
        res = list(pool.imap_unordered(one, tunes(), chunksize=1))
    res.sort(key=lambda r: r["name"])
    tot = summarize(res)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"totals": tot, "tunes": res}, indent=1), encoding="utf-8")
    print(json.dumps(tot, indent=1))


if __name__ == "__main__":
    main(sys.argv)
