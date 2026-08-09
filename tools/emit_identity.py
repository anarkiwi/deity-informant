"""The corpus emission identity: one sha per tune, one aggregate over the cache.

The plan's standing byte-identity gate as a command. A stage that claims
capability with zero use is exactly the stage whose aggregate must not move, and
a stage that moves text on purpose reports the tunes that moved.
"""

import argparse
import hashlib
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
  python tools/emit_identity.py                                # the whole cache
  python tools/emit_identity.py --expect 99d4fdec...           # gate on the aggregate
  python tools/emit_identity.py --tunes Commando -o out/emit.json"""


def aggregate(rows):
    """Sha of ``"<tune> <sha>\\n"`` joined over the clean rows, sorted by identity."""
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: r["tune"]):
        if "error" not in r:
            h.update(("%s %s\n" % (r["tune"], r["sha"])).encode())
    return h.hexdigest()


def one(entry):
    """One tune's emitted-text sha at full Songlengths, or the exception that stopped it."""
    from deity_informant import frameprog
    from deity_informant.c64 import load_psid

    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        sid, sub, secs = entry
        mem, _load, init, play = load_psid(Path(sid).read_bytes())
        mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
        model, _ev = _sweep.decompile(mem, init, play, int(secs * 50), sub)
        text = frameprog.dumps(frameprog.program(model))
        return {
            **_sweep.row_head(entry),
            "wall_s": round(time.monotonic() - t0, 1),
            "bytes": len(text),
            "sha": hashlib.sha256(text.encode()).hexdigest(),
        }
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
    ap.add_argument("--expect", help="aggregate sha the run must reproduce")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "emit_identity.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(one, tunes))
    got = aggregate(rows)
    refused = [r for r in rows if "error" in r]
    out = {
        "tunes": len(rows) - len(refused),
        "refused": refused,
        "wall_s": round(time.monotonic() - t0, 1),
        "bytes": sum(r.get("bytes", 0) for r in rows),
        "aggregate": got,
        "rows": sorted(rows, key=lambda r: r["tune"]),
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(
        "%d tunes, %d refused, %d bytes, %.1fs\naggregate %s"
        % (out["tunes"], len(refused), out["bytes"], out["wall_s"], got)
    )
    if args.expect and args.expect != got:
        sys.exit("aggregate moved: expected %s" % args.expect)
    sys.exit(1 if refused else 0)


if __name__ == "__main__":
    main()
