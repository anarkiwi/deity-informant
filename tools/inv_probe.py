"""Per-tune 8.4 invariant probe: procedures, call forms, sp, page-one fault.

Reads the same artifact the gate judges (``_sweep.build``), then applies the
canonical suite's own checker (``tests/_callgen.violations``) shape by shape.
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
sys.path.insert(0, str(ROOT / "tests"))

_CALLS = ("call", "callb", "dcall", "swc", "pcall")


def _counts(prog):
    from deity_informant import frameproc

    sp = frameproc._SP  # pylint: disable=protected-access
    out = {"procs": len(prog.procs), "calls": 0, "rets": 0, "sp": 0, "text": 0}

    def walk(stmts, last):
        for i, s in enumerate(stmts):
            if s[0] in _CALLS:
                out["calls"] += 1
            elif s[0] == "ret" and not (stmts is last and i == len(stmts) - 1):
                out["rets"] += 1
            if s[0] == "asg" and s[1] == sp:
                out["sp"] += 1
            for x in frameproc._stmt_exprs(s):  # pylint: disable=protected-access
                if sp in frameproc._locset(x):  # pylint: disable=protected-access
                    out["sp"] += 1
            for b in frameproc._stmt_bodies(s):  # pylint: disable=protected-access
                walk(b, last)

    for entry, params, rets, stmts in prog.procs:
        if sp in set(params) | set(rets):
            out["sp"] += 1
        walk(stmts, stmts)
        del entry, params, rets
    return out


def _one(entry, frames):
    from deity_informant import frameprog, frameval
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    full = int(secs * 50)
    n = full if frames is None else min(frames, full)
    t0 = time.monotonic()
    model, prog, _ev = _sweep.build(mem, init, play, full, sub)
    row = {**_sweep.row_head(entry), "frames": n, "wall_s": round(time.monotonic() - t0, 1)}
    row.update(_counts(prog))
    row["text"] = len(frameprog.dumps(prog))
    trace, _walker = frameprog.iota(model, n)
    try:
        got = frameval.gate_fp(model, n, prog)
        row["gate"] = None if got is None else list(got)
    except frameval.FrameFault as exc:
        row["fault"] = str(exc)
    del trace
    return row


def one(entry, frames=None):
    """One tune's invariant row; ``fault`` where the evaluator refused the text."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, frames)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def klass(row):
    """The movement class of one row: clean, or the reason it is not."""
    if "error" in row:
        return "error"
    if "fault" in row:
        return "fault:" + row["fault"].split("$")[0].strip()
    if row.get("gate") is not None:
        return "diverged"
    if row["procs"] == 1 and not row["calls"] and not row["rets"] and not row["sp"]:
        return "clean"
    return "residue"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tunes")
    ap.add_argument("--frames", type=int)
    ap.add_argument("-j", "--procs", type=int, default=24)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "inv_probe.json"))
    args = ap.parse_args()
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = pool.starmap(one, [(e, args.frames) for e in tunes])
    tally = {}
    for r in rows:
        k = klass(r)
        tally[k] = tally.get(k, 0) + 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"tally": tally, "rows": rows}, indent=1), "utf-8")
    for k in sorted(tally, key=lambda x: -tally[x]):
        print("%6d  %s" % (tally[k], k))


if __name__ == "__main__":
    main()
