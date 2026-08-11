"""The 6502 re-emission witness over the cached corpus (stage 4, landing 5).

Gate FP asks whether the evaluator's frame program reproduces the walker; this
asks whether the machine does. Each tune's program is re-emitted as 6502 and
replayed under ``PcodeVM``, so a witnessed row has no evaluator in its chain.
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
  python tools/witness_sweep.py --exemplars                    # the 25-exemplar set
  python tools/witness_sweep.py --frames 300                   # a shorter run
  python tools/witness_sweep.py --tunes Commando -o out/w.json"""


def _one(entry, frames, extents):
    from deity_informant import framelog, frameprog, frameval
    from deity_informant import witness6502 as W
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    full = int(secs * 50)
    n = full if frames is None else min(frames, full)
    t0 = time.monotonic()
    model, prog, _ev = _sweep.build(mem, init, play, full, sub, extents)
    row = {**_sweep.row_head(entry), "frames": n}
    try:
        witness = W.emit(prog)
    except W.Refusal as exc:
        row["refused"] = str(exc).split(";", maxsplit=1)[0]
    else:
        _trace, walker = frameprog.iota(model, n)
        held0 = frameval.sid_held0(prog)
        got = framelog.diff(
            framelog.canonical(witness.frames(n), held0), framelog.canonical(walker, held0)
        )
        row["witness"] = None if got is None else list(got)
        row["bytes"] = len(witness.code)
    row["wall_s"] = round(time.monotonic() - t0, 1)
    return row


def one(entry, frames=None, extents=None):
    """One tune's verdict: ``witness`` is None where the machine reproduces the log."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, frames, extents)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def exemplar_names():
    """The 25 exemplars ``tools/exemplars.py`` declares, as cache identities."""
    sys.path.insert(0, str(ROOT / "tools"))
    import exemplars  # pylint: disable=import-outside-toplevel

    return list(exemplars.EXEMPLARS)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--exemplars", action="store_true", help="the exemplar set, one per family")
    ap.add_argument("--frames", type=int, help="cap the frames per tune; default the full length")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "witness_sweep.json"))
    args = ap.parse_args()

    names = exemplar_names() if args.exemplars else (args.tunes.split(",") if args.tunes else None)
    tunes = _sweep.entries(names)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(one, [(e, args.frames, None) for e in tunes]))
    built = [r for r in rows if "witness" in r]
    failed = sorted((r for r in built if r["witness"] is not None), key=lambda r: r["tune"])
    out = {
        "tunes": len(rows),
        "witnessed": len(built) - len(failed),
        "diverged": [{"tune": r["tune"], "witness": r["witness"]} for r in failed],
        "refused": [{"tune": r["tune"], "why": r["refused"]} for r in rows if "refused" in r],
        "errors": [r for r in rows if "error" in r],
        "frames": args.frames,
        "wall_s": round(time.monotonic() - t0, 1),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
    return 1 if failed or out["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
