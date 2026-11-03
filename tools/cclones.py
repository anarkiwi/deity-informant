"""How much of a C file is repeated code: clones up to names and constants.

``python tools/cclones.py A.c [B.c ...] [-k K]``: a byte is cloned when a run of ``K`` lines
holding it recurs once identifiers ``vN``/``fN`` and numbers are normalized; also lists the
near-identical functions (``difflib`` ratio).
"""

import argparse
import difflib
import re
import sys
from collections import defaultdict
from pathlib import Path

_NAME = re.compile(r"\b[vf]\d+\b")
_NUM = re.compile(r"\b0x[0-9A-Fa-f]+u?\b|\b\d+u?\b")
_FUNC = re.compile(r"^(?:static |void )[^\n]*?\b(\w+)\(.*?^\}", re.S | re.M)


def normalize(line):
    """``line`` with names and numbers replaced and indentation dropped."""
    return _NUM.sub("K", _NAME.sub("V", line)).strip()


def cloned(text, k=4):
    """``(bytes, cloned bytes)`` of C ``text`` for runs of ``k`` lines."""
    lines = text.splitlines()
    norm = [normalize(ln) for ln in lines]
    seen = defaultdict(list)
    for i in range(len(lines) - k + 1):
        if all(len(x) > 1 for x in norm[i : i + k]):
            seen[tuple(norm[i : i + k])].append(i)
    hit = set()
    for starts in seen.values():
        if len(starts) > 1:
            for i in starts:
                hit.update(range(i, i + k))
    return len(text), sum(len(lines[i]) + 1 for i in hit)


def pairs(text, least=0.8):
    """``[(ratio, name, bytes, name, bytes)]`` of functions at least ``least`` alike."""
    funcs = {m.group(1): m.group(0) for m in _FUNC.finditer(text)}
    norm = {n: [normalize(x) for x in body.splitlines()] for n, body in funcs.items()}
    out = []
    names = sorted(funcs, key=lambda n: -len(funcs[n]))
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if min(len(funcs[a]), len(funcs[b])) < 200:
                continue
            r = difflib.SequenceMatcher(None, norm[a], norm[b], autojunk=False).ratio()
            if r >= least:
                out.append((round(r, 2), a, len(funcs[a]), b, len(funcs[b])))
    return sorted(out, reverse=True)


def main(argv=None):
    """Command line: the table and the alike functions."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("-k", type=int, default=4, help="lines per run")
    args = ap.parse_args(argv)
    print("| file | bytes | cloned bytes | cloned |\n|---|---|---|---|")
    alike = []
    for path in args.files:
        text = Path(path).read_text(encoding="utf-8")
        total, dup = cloned(text, args.k)
        print(f"| {Path(path).name} | {total} | {dup} | {100 * dup / max(total, 1):.0f}% |")
        alike += [(Path(path).name, *p) for p in pairs(text)]
    for name, r, a, na, b, nb in alike:
        print(f"{name}: {a} ({na} B) and {b} ({nb} B) {r:.0%} alike")
    return 0


if __name__ == "__main__":
    sys.exit(main())
