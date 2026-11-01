"""Lift the play routine of many PSID tunes and tabulate what keeps their stack in memory.

``python tools/wpsurvey.py ROOT [--sample N] [--calls K] [--max-cycles C] [--timeout S] [--jobs J]
[--out F]``
walks ``ROOT`` for ``*.sid``, runs each tune's init on the reference, collects facts over
``K`` play calls from that state and lifts play. One JSON line per tune; a summary at the end.
A tune taking longer than ``S`` seconds is killed and reported in ``timeouts``.
"""

import argparse
import json
import os
import random
import struct
import sys
from collections import Counter
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import as_completed
from pathlib import Path

from pebble import ProcessPool
from tqdm import tqdm

from deity_informant import trace, wholeprog

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wplift  # noqa: E402 pylint: disable=wrong-import-position

SID = (0xD400, 0xD800)
STACK_OPS = {0x08: "PHP", 0x28: "PLP", 0x48: "PHA", 0x68: "PLA", 0x9A: "TXS", 0xBA: "TSX"}


def psid(data):
    """``(image, init, play, songs, start)`` of a PSID file, or None when not PSID-callable."""
    if data[:4] != b"PSID" or len(data) < 0x76:
        return None
    off, load, init, play, songs, start = struct.unpack(">HHHHHH", data[6:18])
    body = data[off:]
    if load == 0:
        load, body = body[0] | body[1] << 8, body[2:]
    if play == 0 or load + len(body) > 0x10000:
        return None
    img = bytearray(0x10000)
    img[load : load + len(body)] = body
    return img, init or load, play, songs, start


def survey(path, calls, max_cycles=1_000_000, check=0):
    """Stats of one tune's lifted play routine."""
    t = psid(Path(path).read_bytes())
    if t is None:
        return {"path": str(path), "skip": "not psid-callable"}
    img, init, play, _, start = t
    r = wholeprog.reference_call(
        img, init, a=max(start - 1, 0), volatile=[SID], max_cycles=50 * max_cycles
    )
    if r["halt"] != "return":
        return {"path": str(path), "skip": "init " + r["halt"]}
    facts = trace.collect(img, lambda s: [s.call(play) for _ in range(calls)], max_cycles)
    prog = wholeprog.lift(img, [play], [SID], facts)
    ops = Counter(STACK_OPS.get(img[p], "%02X" % img[p]) for p in facts.page1)
    insns = Counter(STACK_OPS[img[p]] for p in facts.starts if img[p] in STACK_OPS)
    row = {"path": str(path), **prog.stats, "page1_ops": ops, "stack_insns": insns}
    if check:
        bad = wplift.check(img, prog, [(play, 0)] * check, [SID], progress=False)
        row |= {"exact": bad is None, "mismatch": bad, "deopts": sum(prog.deopts.values())}
    return row


def _job(a):
    try:
        return survey(*a)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"path": str(a[0]), "error": repr(e)}


def summary(rows):
    """Counts of tunes per blocker and the opcodes behind page-1 sites."""
    ok = [r for r in rows if "ops" in r]
    s = {
        "tunes": len(rows),
        "lifted": len(ok),
        "skipped": Counter(r["skip"] for r in rows if "skip" in r),
        "errors": sum("error" in r for r in rows),
        "timeouts": [r["path"] for r in rows if r.get("error") == "timeout"],
        "stack_private": sum(r["stack_private"] for r in ok),
        "page1_sites": sum(r["page1_sites"] > 0 for r in ok),
        "kept_bytes": sum(r["kept_bytes"] > 0 for r in ok),
        "unknown_heights": sum(r["unknown_heights"] > 0 for r in ok),
        "recursive": sum(r["recursive"] > 0 for r in ok),
        "unreached": sum(r["unreached"] > 0 for r in ok),
        "checked": sum("exact" in r for r in ok),
        "inexact": [
            r["path"] for r in ok if r.get("exact") is False and r["mismatch"][1] != "no-return"
        ],
        "no_return": sum(
            r.get("mismatch") is not None and r["mismatch"][1] == "no-return" for r in ok
        ),
        "deopting": sum(r.get("deopts", 0) > 0 for r in ok),
    }
    s["page1_ops"] = sum((Counter(r["page1_ops"]) for r in ok), Counter()).most_common(12)
    s["blocked_stack_insns"] = sum(
        (Counter(r["stack_insns"]) for r in ok if not r["stack_private"]), Counter()
    ).most_common()
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--sample", type=int, default=0, help="random tunes (0: all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--calls", type=int, default=500)
    ap.add_argument("--max-cycles", type=int, default=1_000_000, help="per call")
    ap.add_argument("--check", type=int, default=0, help="calls checked against the reference")
    ap.add_argument("--timeout", type=float, default=60, help="seconds per tune")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    paths = sorted(Path(args.root).rglob("*.sid"))
    if args.sample:
        paths = random.Random(args.seed).sample(paths, min(args.sample, len(paths)))
    out = open(args.out, "w", encoding="utf-8") if args.out else None  # noqa: SIM115
    rows = []
    with ProcessPool(max_workers=args.jobs) as pool:
        futs = {
            pool.schedule(
                _job, args=((p, args.calls, args.max_cycles, args.check),), timeout=args.timeout
            ): p
            for p in paths
        }
        for f in tqdm(as_completed(futs), total=len(paths), desc="survey", unit="tune"):
            try:
                r = f.result()
            except FutureTimeout:
                r = {"path": str(futs[f]), "error": "timeout"}
            rows.append(r)
            if out:
                out.write(json.dumps(r) + "\n")
    if out:
        out.close()
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
