"""The de-SMC invariant over the cached corpus: no store reaches executable memory.

The check is the evaluator's own (``frameval`` faults at the cell), so this sweep runs
the artifact and reports, per tune, whether it completed with zero such stores plus the
relocation refusals ``desmc`` named. A tune that faults names the cell it stored into.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/desmc_gate.py                       # the whole cache, full length
  python tools/desmc_gate.py --frames 300          # a shorter run
  python tools/desmc_gate.py --tunes Athena,Krakout -o out/desmc.json"""


def _one(entry, frames):
    from deity_informant import desmc, frameproc, framefuse, frameval
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    full = int(secs * 50)
    n = full if frames is None else min(frames, full)
    model, prog, _ev = _sweep.build(mem, init, play, full, sub)
    code = desmc.executable(prog)
    static = []
    for _e, _p, _r, stmts in prog.procs:
        for s in framefuse.stmts_of(stmts):
            if s[0] != "st":
                continue
            base, _idx = frameproc.addr_split(s[1])
            if base is not None and base in code:
                static.append(base)
    row = {**_sweep.row_head(entry), "frames": n, "code_bytes": len(code)}
    row["static_stores"] = sorted(set(static))
    row["gate"] = frameval.gate_fp(model, n, prog)
    return row


def one(entry, frames=None):
    """One tune's verdict: ``zero`` where no store of the artifact reached code."""
    try:
        signal.alarm(_sweep.CAP_S)
        row = _one(entry, frames)
        row["zero"] = not row["static_stores"] and row["gate"] is None
        return row
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
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "desmc_gate.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(one, [(e, args.frames) for e in tunes]))
    bad = [r for r in rows if not r.get("zero")]
    causes = Counter(r.get("error", "diverged").split(" $")[0] for r in bad)
    out = {
        "tunes": len(rows),
        "zero_writes": len(rows) - len(bad),
        "frames": args.frames,
        "causes": dict(causes),
        "failing": [{"tune": r["tune"], "why": r.get("error") or r.get("gate")} for r in bad],
        "wall_s": round(time.monotonic() - t0, 1),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows", "failing")}, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
