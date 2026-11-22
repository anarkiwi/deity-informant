"""Lift the play routine of many PSID tunes and tabulate what keeps their stack in memory.

``python tools/wpsurvey.py ROOT [--sample N] [--calls K] [--max-cycles C] [--timeout S] [--jobs J]
[--out F]``
walks ``ROOT`` for ``*.sid``, runs each tune's init on the reference, collects facts over
``K`` play calls from that state and lifts play. One JSON line per tune; a summary at the end.
A tune taking longer than ``S`` seconds is killed and reported in ``timeouts``.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from pysidtracker.errors import SidError
from pysidtracker.image import SidImage

from deity_informant import trace, wholeprog

sys.path.insert(0, str(Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position,wrong-import-order
import wplift  # noqa: E402
from smclabel import failed_path, pool_rows, sweep_parser, tunes  # noqa: E402

SID = (0xD400, 0xD800)
STACK_OPS = {0x08: "PHP", 0x28: "PLP", 0x48: "PHA", 0x68: "PLA", 0x9A: "TXS", 0xBA: "TSX"}


def psid(data):
    """``(image, init, play, songs, start)`` of a PSID file, or None when not PSID-callable."""
    try:
        tune = SidImage.from_sid(data)
    except SidError:
        return None
    h = tune.header
    if not h.is_psid or not h.play_address:
        return None
    return tune.mem, h.init_address or tune.load, h.play_address, h.songs, h.start_song


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
    ap = sweep_parser(__doc__, 60)
    ap.add_argument("--out", help="the rows as JSON lines")
    ap.add_argument("--calls", type=int, default=500)
    ap.add_argument("--max-cycles", type=int, default=1_000_000, help="per call")
    ap.add_argument("--check", type=int, default=0, help="calls checked against the reference")
    args = ap.parse_args(argv)
    paths = tunes(args.root, args.sample, args.seed)
    jobs = [(p, args.calls, args.max_cycles, args.check) for p in paths]
    rows = pool_rows(_job, jobs, args.jobs, args.timeout, "survey", failed_path, args.out)
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
