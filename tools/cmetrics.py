"""Size and control-flow metrics of C files, as a Markdown table.

``python tools/cmetrics.py A.c [B.c ...]``: lines, statements (lines ending in ``;``), ``if``,
``goto``, ``switch``, loops (``while``, ``for`` and ``do`` loops) and ``int32_t`` variables,
the control variables ``lift-cf-to-scf`` introduces (lifted values are 8 and 16 bits).
"""

import re
import sys
from pathlib import Path

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


def table(paths):
    """The Markdown table of the files ``paths``."""
    rows = [f"| file | {' | '.join(COLUMNS)} |", f"|---|{'---|' * len(COLUMNS)}"]
    for path in paths:
        m = metrics(Path(path).read_text(encoding="utf-8"))
        rows.append(f"| {Path(path).name} | {' | '.join(str(m[c]) for c in COLUMNS)} |")
    return "\n".join(rows)


def main(argv=None):
    """Command line: print the table."""
    print(table(argv if argv is not None else sys.argv[1:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
