"""The def-use web census: the denotation plan's §5 unit, counted over the corpus.

A1 makes the web the analysis unit and emits no new byte, so this is the baseline
the solve is measured against: the webs the corpus carries, the refusals per class,
and the machine spellings carrying more than one web -- what a name cannot see.
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
  python tools/web_census.py                                   # the whole cache
  python tools/web_census.py --tunes MUSICIANS/H/Hubbard_Rob/Commando"""

TOTALS = ("procs", "webs", "refused", "entry", "opaque", "width", "names", "split", "arch_split")


def one(entry):
    """One tune's web counts, or the exception that stopped it."""
    from deity_informant import frameproc
    from deity_informant import frameprog
    from deity_informant.c64 import load_psid

    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        sid, sub, secs = entry
        mem, _load, init, play = load_psid(Path(sid).read_bytes())
        mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
        model, _ev = _sweep.decompile(mem, init, play, int(secs * 50), sub)
        row = dict.fromkeys(TOTALS, 0)
        widest = ["", 0]
        for pw in frameprog.program(model).webs().values():
            row["procs"] += 1
            for k, v in pw.counts().items():
                row[k] += v
            for name in pw.names():
                got = len(pw.of_name(name))
                row["names"] += 1
                row["split"] += got > 1
                row["arch_split"] += got > 1 and name in frameproc._ALL_REG_LOCALS
                widest = max(widest, [name, got], key=lambda w: w[1])
        row["widest"] = widest
        return {**_sweep.row_head(entry), "wall_s": round(time.monotonic() - t0, 1), **row}
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
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "web_census.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(one, tunes))
    errors = [r for r in rows if "error" in r]
    total = Counter()
    for r in rows:
        total.update({k: r[k] for k in TOTALS if k in r})
    widest = max((r.get("widest", ["", 0]) for r in rows), key=lambda w: w[1])
    out = {
        "tunes": len(rows) - len(errors),
        "errors": errors,
        "wall_s": round(time.monotonic() - t0, 1),
        "totals": dict(total),
        "widest": widest,
        "rows": sorted(rows, key=lambda r: r["tune"]),
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(
        "%d tunes, %d errors, %.1fs\n%d webs over %d procedures, %d refused"
        " (entry %d, opaque %d, width %d)\n%d locals, %d carry 2+ webs (%d machine names);"
        " widest %s at %d"
        % (
            out["tunes"],
            len(errors),
            out["wall_s"],
            total["webs"],
            total["procs"],
            total["refused"],
            total["entry"],
            total["opaque"],
            total["width"],
            total["names"],
            total["split"],
            total["arch_split"],
            widest[0],
            widest[1],
        )
    )
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
