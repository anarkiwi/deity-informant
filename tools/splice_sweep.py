"""Adoption §8 step 4's emitter, measured over the whole cached corpus.

Per tune: the text parses, it is a ``dumps``/``loads`` fixpoint, every local it reads
has a definition, and the parsed program reproduces the walker's projection under Gate
FP. ``--baseline`` runs the same checks over ``frameproc.render_lines``' own text.
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import signal
import sys
import time
from pathlib import Path
from unittest import mock

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/splice_sweep.py                                 # the whole cache
  python tools/splice_sweep.py --baseline -o out/base.json     # the control
  python tools/splice_sweep.py --tunes Commando --frames 600"""

CHECKS = ("error", "parse", "lint", "fixpoint", "gate")


def one(entry, frames, baseline):
    """One tune's verdict on each check, or the exception that stopped it."""
    from deity_informant import frameprog
    from deity_informant import frameproc
    from deity_informant import frameval
    from deity_informant.c64 import load_psid

    row = dict(_sweep.row_head(entry))
    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        sid, sub, secs = entry
        mem, _load, init, play = load_psid(Path(sid).read_bytes())
        mem[0xD418] = 0x0F
        model, _ev = _sweep.decompile(mem, init, play, int(secs * 50), sub)
        prog = frameprog.program(model)
        row["base_lines"] = len(frameprog.dumps(prog).splitlines())
        if baseline:
            text = frameprog.dumps(prog)
        else:
            lines = frameprog.unified_lines(model, prog)
            with mock.patch.object(frameproc, "render_lines", lambda *_a, **_k: lines):
                text = frameprog.dumps(prog)
        row["lines"] = len(text.splitlines())
        row["sha"] = hashlib.sha256(text.encode()).hexdigest()
        try:
            frameprog.lint(text)
        except Exception as exc:  # pylint: disable=broad-except
            row["lint"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            back = frameprog.loads(text)
        except Exception as exc:  # pylint: disable=broad-except
            row["parse"] = "%s: %s" % (type(exc).__name__, exc)
            return row
        again = frameprog.dumps(back)
        if again != text:
            a, b = text.splitlines(), again.splitlines()
            diff = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
            row["fixpoint"] = [list(d) for d in diff[:4]] or ["length %d/%d" % (len(a), len(b))]
        n = int(secs * 50) if frames is None else min(frames, int(secs * 50))
        got = frameval.gate_fp(model, n, back)
        if got is not None:
            row["gate"] = list(got)
        row["wall_s"] = round(time.monotonic() - t0, 1)
        return row
    except Exception as exc:  # pylint: disable=broad-except
        row["error"] = "%s: %s" % (type(exc).__name__, exc)
        return row
    finally:
        signal.alarm(0)


def _job(args):
    return one(*args)


def rollup(rows):
    """Per-check failure lists plus the emitted-size delta against ``render_lines``."""
    got = {k: [(r["tune"], r[k]) for r in rows if k in r] for k in CHECKS}
    sized = [r for r in rows if "lines" in r]
    got["tunes"] = len(rows)
    got["bad"] = sorted({t for k in CHECKS for t, _v in got[k]})
    got["d_lines"] = sum(r["lines"] - r["base_lines"] for r in sized)
    got["larger"] = [r["tune"] for r in sized if r["lines"] > r["base_lines"]]
    got["smaller"] = sum(1 for r in sized if r["lines"] < r["base_lines"])
    return got


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--frames", type=int, help="cap the Gate FP frames (default full length)")
    ap.add_argument("--baseline", action="store_true", help="measure render_lines' own text")
    ap.add_argument("--against", help="a recorded artifact; a tune bad here and clean there fails")
    ap.add_argument("-j", "--procs", type=int, default=24)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "splice_sweep.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    jobs = [(t, args.frames, args.baseline) for t in tunes]
    with mp.Pool(min(len(jobs), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(_job, jobs))
    got = rollup(rows)
    got["wall_s"] = round(time.monotonic() - t0, 1)
    if args.against:
        prior = json.loads(Path(args.against).read_text(encoding="utf-8"))["rollup"]["bad"]
        got["new"] = sorted(set(got["bad"]) - set(prior))
        got["fixed"] = sorted(set(prior) - set(got["bad"]))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"rollup": got, "rows": rows}, indent=1), encoding="utf-8")
    show = {k: len(v) if isinstance(v, list) and k != "new" else v for k, v in got.items()}
    print(json.dumps(show, indent=1))
    return 1 if got.get("new") or got["larger"] else 0


if __name__ == "__main__":
    sys.exit(main())
