"""T2 -- streams and selectors: the const-based tables a cursor walks or picks in.

One cursor cell reads the columns of one table (a record's fields, or parallel
arrays); it is a *stream* where a store steps it from its own value and a
*selector* where only the score or another table sets it.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from .cursors import successors


def group(chans):
    """``{cursor key: [channels]}`` for the const-based cell cursors."""
    out = {}
    for c in chans:
        if c.cursor[0] == "cell":
            out.setdefault(c.cursor, []).append(c)
    return out


def table(cells, key, chans, rgn, names, shifts, stepped):
    """One cursor's table: its columns, entries, visited rows, jumps, holds, terminator."""
    shift = chans[0].accs[0].shift
    per = []
    for d in shifts:
        h = cells.col(key[1], key[2] + d)
        if h is not None:
            per.append(successors(h << shift))
    if not per:
        return None
    cols = sorted({(a.table, a.origin - rgn[a.table].base) for c in chans for a in c.accs})
    jumps, holds = Counter(), Counter()
    for e in per:
        jumps.update(e.jumps)
        holds.update(e.holds)
    entries = max(-(-rgn[t].size // max(rgn[t].stride, 1)) for t, _o in cols)
    img = cells.img
    first = min((rgn[t].base + o for t, o in cols if o >= 0), default=None)
    termvals = {int(img[first + f]) for f, _t in jumps} if first is not None else set()
    moved = _moves(per)
    return {
        "cursor": "%s@$%04X" % (names.of(key[1]), key[2]),
        "kind": "stream" if stepped else "selector",
        "moves": moved,
        "shift": shift,
        "step": (
            Counter(e.step for e in per if e.step is not None).most_common(1)[0][0]
            if any(e.step is not None for e in per)
            else None
        ),
        "columns": [{"table": names.of(t), "origin": o, "stride": rgn[t].stride} for t, o in cols],
        "entries": entries,
        "visited": sorted({v for e in per for v in e.visited}),
        "jumps": sorted([list(k), n] for k, n in jumps.items()),
        "terminator": termvals.pop() if len(termvals) == 1 else None,
        "holds": sorted(holds.items()),
        "copies": len(per),
    }


def _moves(per):
    """Every transition a cursor's copies made."""
    return sum(len(e.holds) - 1 for e in per)


def bytes_at(img, base, lo, hi):
    """The table bytes ``[lo, hi)`` from ``base``, as a list."""
    return [int(x) for x in np.asarray(img[base + lo : base + hi])]
