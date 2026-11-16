"""Size and control-flow metrics of C files, as a Markdown table.

``python tools/cmetrics.py A.c [B.c ...]``: lines, statements (lines ending in ``;``), ``if``,
``goto``, ``switch``, loops (``while``, ``for`` and ``do`` loops) and ``int32_t`` variables,
the control variables ``lift-cf-to-scf`` introduces (lifted values are 8 and 16 bits).
``--labels``: instead, each ``goto`` whose label its function does not define, and exit 1
when there is one.
"""

import re
import sys
from pathlib import Path

GOTO = re.compile(r"\bgoto\s+(\w+)\s*;")
LABEL = re.compile(r"^\s*(?!default\b)(\w+)\s*:(?!:)", re.M)
COLUMNS = ("lines", "statements", "if", "goto", "switch", "loops", "int32_t variables")


def metrics(text):
    """The metrics of C ``text`` by column."""

    def count(pattern):
        return len(re.findall(pattern, text, flags=re.M))

    return {
        "lines": len(text.splitlines()),
        "statements": count(r";\s*$"),
        "if": count(r"\bif\s*\("),
        "goto": count(r"\bgoto\b"),
        "switch": count(r"\bswitch\s*\("),
        "loops": count(r"\bfor\s*\(") + count(r"\bdo\s*\{") + count(r"^\s*while\s*\("),
        "int32_t variables": count(r"\bint32_t\s+\w+\s*[;=]"),
    }


def unlabeled(text):
    """The ``goto`` targets of C ``text`` that the function holding the ``goto`` (the lines
    up to a ``}`` in column 0) does not define as labels, sorted."""
    out, gotos, labels = set(), set(), set()
    for line in [*text.splitlines(), "}"]:
        gotos.update(GOTO.findall(line))
        labels.update(LABEL.findall(line))
        if line.startswith("}"):
            out |= gotos - labels
            gotos, labels = set(), set()
    return sorted(out)


def table(paths):
    """The Markdown table of the files ``paths``."""
    rows = [f"| file | {' | '.join(COLUMNS)} |", f"|---|{'---|' * len(COLUMNS)}"]
    for path in paths:
        m = metrics(Path(path).read_text(encoding="utf-8"))
        rows.append(f"| {Path(path).name} | {' | '.join(str(m[c]) for c in COLUMNS)} |")
    return "\n".join(rows)


def main(argv=None):
    """Command line: print the table, or with ``--labels`` the unlabeled gotos."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--labels" not in args:
        print(table(args))
        return 0
    args.remove("--labels")
    bad = 0
    for path in args:
        for target in unlabeled(Path(path).read_text(encoding="utf-8")):
            print(f"{path}: goto {target} has no label")
            bad += 1
    return int(bad > 0)


if __name__ == "__main__":
    sys.exit(main())
