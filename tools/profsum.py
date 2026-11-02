"""Summarise a py-spy ``--format raw`` (collapsed stacks) profile.
``python tools/profsum.py PROFILE [--top N] [--match SUBSTR ...] [--within SUBSTR]
[--under SUBSTR]``: the top self-time frames
and, per ``--match``, the inclusive share of samples whose stack has a matching frame."""

import argparse
import re
import sys
from collections import Counter

_LINE = re.compile(r"^(.*) (\d+)$")


def load(path):
    """``[(frames, samples)]`` of a collapsed-stack file, each frame without its line number."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _LINE.match(line.rstrip("\n"))
            if m:
                frames = [re.sub(r":\d+\)$", ")", s) for s in m.group(1).split(";")]
                out.append((frames, int(m.group(2))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--match", nargs="*", default=())
    ap.add_argument("--within", help="only the samples whose stack has a matching frame")
    ap.add_argument("--under", help="also break down the frames called by the first matching one")
    args = ap.parse_args(argv)
    stacks = load(args.profile)
    if args.within:
        stacks = [(f, n) for f, n in stacks if any(args.within in s for s in f)]
    total = sum(n for _, n in stacks) or 1
    leaf = Counter()
    for frames, n in stacks:
        leaf[frames[-1]] += n
    print(f"samples {total}")
    for name, n in leaf.most_common(args.top):
        print(f"{100 * n / total:5.1f}% self  {name}")
    for pat in args.match:
        n = sum(k for frames, k in stacks if any(pat in s for s in frames))
        print(f"{100 * n / total:5.1f}% incl  {pat}")
    if args.under:
        child = Counter()
        for frames, n in stacks:
            i = next((i for i, s in enumerate(frames) if args.under in s), None)
            if i is not None:
                child[frames[i + 1] if i + 1 < len(frames) else "<self>"] += n
        for name, n in child.most_common(args.top):
            print(f"{100 * n / total:5.1f}% under {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
