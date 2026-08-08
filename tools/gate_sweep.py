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


def _one(entry, frames):
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
    prog = frameprog.program(model)
    got = frameval.gate_fp(model, n, prog)
    row = {**_sweep.row_head(entry), "frames": n, "wall_s": round(time.monotonic() - t0, 1)}
    row["gate"] = None if got is None else list(got)
    return row


def one(entry, frames=None):
    """One tune's verdict: ``gate`` is None where the program reproduces the log."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, frames)
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
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(one, [(e, args.frames) for e in tunes]))
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
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    brief = {k: v for k, v in out.items() if k != "rows"}
    brief["refused"] = len(out["refused"])
    print(json.dumps(brief, indent=1))
    return 1 if failed or out["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
