"""``python -m deity_informant.cgen IMAGE --org ORG --entry PC:NAME ... -o OUT.c --header OUT.h``.

Translates a raw program image loaded at ``ORG`` (the rest of memory zero) to C; ``--facts``
(``trace.Facts`` JSON) supplies computed-jump targets only; ``--init NAME`` names the entry the
host calls once, before any other.
"""

import argparse
import json
import sys
from pathlib import Path

from . import translate


def _int(text):
    return int(text, 0)


def _range(text):
    lo, hi = (int(x, 0) for x in text.split("-"))
    return lo, hi


def main(argv=None):
    """Command line: write the ``.c`` and ``.h`` and report the stages."""
    ap = argparse.ArgumentParser(prog="python -m deity_informant.cgen", description=__doc__)
    ap.add_argument("image", help="raw program bytes")
    ap.add_argument("--org", type=_int, required=True, help="load address")
    ap.add_argument("--entry", action="append", required=True, help="PC:NAME, A the argument")
    ap.add_argument("--volatile", action="append", type=_range, default=[], help="LO-HI, HI excl.")
    ap.add_argument("--sensitive", action="append", type=_int, default=[], help="kept I/O read")
    ap.add_argument("--facts", help="trace.Facts JSON for computed-jump targets")
    ap.add_argument("--init", help="NAME of the entry called once, before any other")
    ap.add_argument("-o", "--out", required=True, help="the .c")
    ap.add_argument("--header", help="the .h (default: the .c's name with .h)")
    args = ap.parse_args(argv)
    data = Path(args.image).read_bytes()
    image = bytearray(0x10000)
    image[args.org : args.org + len(data)] = data
    entries = [(int(pc, 0), name) for pc, name in (e.split(":") for e in args.entry)]
    facts = json.loads(Path(args.facts).read_text(encoding="utf-8")) if args.facts else None
    header = Path(args.header) if args.header else Path(args.out).with_suffix(".h")
    c, h, info = translate(
        image,
        entries,
        args.volatile,
        args.sensitive,
        facts,
        loaded=(args.org, args.org + len(data)),
        header=header.name,
        init=[n for _, n in entries].index(args.init) if args.init else None,
    )
    Path(args.out).write_text(c, encoding="utf-8")
    header.write_text(h, encoding="utf-8")
    stages = ", ".join(f"{k} {v:.3f} s" for k, v in info["times"].items())
    print(
        f"{args.out}: {len(c)} bytes, {len(info['regions'])} regions, "
        f"{len(info['traps'])} traps, {len(info['facts_edges'])} fact edges ({stages})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
