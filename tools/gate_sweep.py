"""Gate FP over the whole cached corpus (docs/frameprog.md 7.10.9).

Every other sweep counts shapes in the emitted text; this one asks whether the
text is right. A tune reports the divergence it gives rather than a boolean, so a
regression names the section and frame it moved.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/gate_sweep.py                                   # the whole cache
  python tools/gate_sweep.py --frames 300                      # a shorter run
  python tools/gate_sweep.py --tunes Comic_Bakery,Krakout -o out/gate.json"""


def _one(entry, frames, extents):
    from deity_informant import frameprog
    from deity_informant import frameval
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    full = int(secs * 50)
    n = full if frames is None else min(frames, full)
    t0 = time.monotonic()
    model, _ev = S.decompile(mem, init, play, full, sub)
    prog = frameprog.program(model, extents)
    got = frameval.gate_fp(model, n, prog)
    row = {**_sweep.row_head(entry), "frames": n, "wall_s": round(time.monotonic() - t0, 1)}
    row["gate"] = None if got is None else list(got)
    return row


def one(entry, frames=None, extents=None):
    """One tune's verdict: ``gate`` is None where the program reproduces the log."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, frames, extents)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--frames", type=int, help="cap the frames per tune; default the full length")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "gate_sweep.json"))
    ap.add_argument(
        "--extents",
        help="Phase 2b (b0) artifact: rung (g) reads it, and a run that outruns a"
        " recorded horizon fails",
    )
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    art = json.loads(Path(args.extents).read_text(encoding="utf-8")) if args.extents else None
    lifts = _records(art)
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        jobs = [(e, args.frames, lifts.get(_sweep.tune_id(e[0]))) for e in tunes]
        rows = _sweep.check_rows(pool.starmap(one, jobs))
    built = [r for r in rows if "error" not in r]
    failed = sorted((r for r in built if r["gate"] is not None), key=lambda r: r["tune"])
    out = {
        "tunes": len(built),
        "clean": len(built) - len(failed),
        "diverged": [{"tune": r["tune"], "gate": r["gate"]} for r in failed],
        "refused": [r for r in rows if "error" in r],
        "frames": args.frames,
        "wall_s": round(time.monotonic() - t0, 1),
        "rows": rows,
    }
    if art is not None:
        out["outran_horizon"] = _horizon(art, built)
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    brief = {k: v for k, v in out.items() if k != "rows"}
    brief["refused"] = len(out["refused"])
    print(json.dumps(brief, indent=1))
    return 1 if failed or out["refused"] or out.get("outran_horizon") else 0


def _records(art):
    """``{tune: [web record]}`` rung (g) reads off the artifact, empty without one."""
    from deity_informant import ptrextent

    return {} if art is None else ptrextent.records(art["rows"])


def _horizon(art, built):
    """Phase 2b (b0)'s MUST: no gate run may outrun the extent artifact's horizon.

    Inside it an ``extent`` fault is an instrument defect and stops the line; past it
    the fault is the claim boundary, so a run this artifact does not cover is refused
    before its verdicts are read."""
    from deity_informant import ptrextent

    return ptrextent.outran(
        ptrextent.horizons(art["rows"]), {r["tune"]: r["frames"] for r in built}
    )


if __name__ == "__main__":
    sys.exit(main())
