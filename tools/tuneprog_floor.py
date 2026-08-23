#!/usr/bin/env python3
"""Measure the complexity floor of one certified tuneprog against its own tune.

Four markdown tables over a finished ``--out`` directory: ``bytes`` (the load band
by executed code / reached data / neither), ``mdl`` (``xz -9e`` of those, of the
print and of the SID write log), ``statements`` and ``pairs``.
"""

import argparse
import json
import lzma
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.tuneprog import datablock, ir, pipeline  # noqa: E402
from deity_informant.tuneprog.irwalk import addr_split, node_exprs, walk as ewalk  # noqa: E402
from deity_informant.tuneprog.lift import lift_trace  # noqa: E402
from deity_informant.tuneprog.live import needed, printable  # noqa: E402
from deity_informant.tuneprog.structure import Blk, For, hidden, walk  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402

KINDS = ("sid write", "16-bit half/carry", "index plumbing", "data", "control")


def xz(b):
    """``xz -9e`` of a byte string."""
    return len(lzma.compress(bytes(b), preset=9 | lzma.PRESET_EXTREME))


def table(head, rows):
    """A markdown table."""
    out = ["| %s |" % " | ".join(head), "|%s|" % "|".join("---" for _ in head)]
    return out + ["| %s |" % " | ".join(str(c) for c in r) for r in rows]


def band_split(trace, view):
    """``(band, code, data, neither)`` address sets of the load band.

    ``data`` is :func:`~.datablock.reach_bytes`, the reach the print's data section
    carries: the cells the S4's own accessor envelopes name, region by region.
    """
    lo, hi = trace.meta["load"]
    band = set(range(lo, hi + 1))
    lifted = lift_trace(trace)
    code = {a for k, l in lifted.items() for a in range(k[0], k[0] + l.length)} & band
    data = datablock.reach_bytes(view) & band
    return band, code, data, band - code - data


def bytes_table(trace, view):
    """The ``bytes`` table: what the load band is made of."""
    band, code, data, rest = band_split(trace, view)
    lo, hi = trace.meta["load"]

    def pct(s):
        return "%.1f" % (100.0 * len(s) / len(band))

    rows = [
        ("load band $%04X-$%04X" % (lo, hi), len(band), "100.0"),
        ("executed player code", len(code), pct(code)),
        ("data the trace reached", len(data), pct(data)),
        ("neither (other songs, sfx, dead code)", len(rest), pct(rest)),
    ]
    return table(("segment", "bytes", "%"), rows)


def writelog(trace):
    """``(reg, val)`` pairs of every play-time SID write, and the per-tick register image."""
    w = {k: np.asarray(v) for k, v in trace.wlog.items()}
    play = w["call"] != 0xFFFFFFFF
    addr = (w["addr"][play] - 0xD400).astype(np.uint8)
    val = w["val"][play].astype(np.uint8)
    call = w["call"][play]
    img = np.zeros((int(call.max()) + 1, 25), np.uint8)
    cur = np.zeros(25, np.uint8)
    j = 0
    for c in range(img.shape[0]):
        while j < len(addr) and call[j] == c:
            cur[addr[j]] = val[j]
            j += 1
        img[c] = cur
    return np.stack([addr, val], 1).tobytes(), img


def mdl_table(out, trace, view):
    """The ``mdl`` table: description lengths a decompilation sits between."""
    band, code, data, _rest = band_split(trace, view)
    img = trace.image_pre
    pairs, grid = writelog(trace)
    items = [
        ("the whole load band", bytes(img[a] for a in sorted(band))),
        ("executed player code only", bytes(img[a] for a in sorted(code))),
        ("data the trace reached", bytes(img[a] for a in sorted(data))),
        ("tuneprog.md (the print)", (out / "tuneprog.md").read_bytes()),
        ("tuneprog.py (the executable)", (out / "tuneprog.py").read_bytes()),
        ("SID write log, (reg,val) pairs", pairs),
        ("SID register image per tick, by register", grid.T.copy().tobytes()),
    ]
    return table(("artefact", "raw bytes", "xz -9e"), [(n, len(b), xz(b)) for n, b in items])


def cells(stmt):
    """``[(region, const base, index repr, reached span)]`` of each access in ``stmt``."""
    out = []
    for e in node_exprs(stmt):
        for x in ewalk(e):
            if type(x) is ir.Load:
                base, idx = addr_split(x.a)
                out.append((x.r, base, repr(idx), (x.lo, x.hi)))
            elif type(x) is ir.R16:
                out += _word(x)
    if type(stmt) is ir.Store:
        base, idx = addr_split(stmt.a)
        out.append((stmt.r, base, repr(idx), (stmt.lo, stmt.hi)))
    if type(stmt) is ir.W16:
        out += _word(stmt)
    return out


def _word(x):
    """The two halves of an already-folded 16-bit read or write."""
    idx = repr(addr_split(x.a)[1])
    return [(r, a, idx, (a, a)) for r, a in (x.lo, x.hi)]


def _cmp(e):
    return type(e) is ir.Bin and e.op in ("<=", "<", "==", "!=")


def _shift8(e):
    return type(e) is ir.Bin and e.op == "<<" and type(e.b) is ir.Const and e.b.v == 8


def machine(stmt, others):
    """True where the statement is 8-bit machinery: a carry, a borrow, or a high half.

    An access one byte above a sibling's at the same index is that pair's high
    half, which a 16-bit view of the pair deletes.
    """
    for e in node_exprs(stmt):
        for x in ewalk(e):
            if type(x) is not ir.Bin:
                continue
            if x.op == "carry" or (x.op in ("+", "-") and (_cmp(x.a) or _cmp(x.b))):
                return True
            if x.op == "|" and _shift8(x.a):
                return True
    return any((r, b - 1, i) in others for r, b, i, _s in cells(stmt) if b is not None)


def statements(view, structured):
    """``[(block src, statement)]`` in printed order, with the printer's own filter."""
    live, _p = needed(view)
    out = []
    for name, body in structured.items():
        hide = frozenset(
            n for x in walk(body) if type(x) is For and ir.copyval(x.var) for n in x.hide
        )
        for node in walk(body):
            if type(node) is not Blk:
                out.append((None, node))
                continue
            keep = [s for s in node.stmts if printable(s, live[name]) and not hidden(s, hide)]
            out += [(node.src, s) for s in keep]
    return out


def kind_of(src, stmt, siblings, ios):
    """Which of :data:`KINDS` a printed statement is."""
    if src is None or type(stmt) is ir.Call:
        return "control"
    if machine(stmt, siblings):
        return "16-bit half/carry"
    if type(stmt) is ir.Store and stmt.r in ios:
        return "sid write"
    return "data" if cells(stmt) else "index plumbing"


def _range(src, ranges):
    if src is None:
        return "(control nodes)"
    for lo, hi, name in ranges:
        if lo <= src <= hi:
            return name
    return "other"


def stmt_table(view, structured, regions, ranges):
    """The ``statements`` table: printed statements by code range and kind."""
    ios = {r["id"] for r in regions if r["kind"] == "io"}
    rows = statements(view, structured)
    bysrc = defaultdict(list)
    for src, s in rows:
        if src is not None:
            bysrc[src] += cells(s)
    counts = Counter()
    for src, s in rows:
        sib = {c[:3] for c in bysrc[src]}
        counts[(_range(src, ranges), kind_of(src, s, sib, ios))] += 1
    body = []
    for n in [x[2] for x in ranges] + ["other", "(control nodes)"]:
        r = [counts[(n, k)] for k in KINDS]
        if any(r):
            body.append((n,) + tuple(r) + (sum(r),))
    tot = tuple(sum(r[i + 1] for r in body) for i in range(len(KINDS)))
    return table(("code range",) + KINDS + ("all",), body + [("**total**",) + tot + (sum(tot),)])


def pair_check(view, structured, regions, trace):
    """The ``pairs`` table: which regions a block reads as ``(lo, hi)`` at one index.

    A pair is two accesses of one region in one block at the same index expression
    and adjacent constant bases; ``reaches`` is the address span they observed,
    which decides whether the pair is a const-table row or aliases live state.
    """
    byid = {r["id"]: r for r in regions}
    written = trace.written_play | trace.written_init
    blocks = defaultdict(list)
    for src, s in statements(view, structured):
        if src is not None:
            blocks[src] += cells(s)
    rows = []
    for src, cs in sorted(blocks.items()):
        bases = defaultdict(set)
        for r, b, i, sp in cs:
            if b is not None:
                bases[(r, i)].add(b)
        for (r, i), bs in sorted(bases.items()):
            rg = byid.get(r)
            if rg is None or not any(b + 1 in bs for b in bs):
                continue
            sp = [x for a, b, j, x in cs if a == r and j == i and b in bs]
            reach = range(min(x[0] for x in sp), max(x[1] for x in sp) + 1)
            hit = [a for a in reach if a in written]
            rows.append(
                (
                    "$%04X" % src,
                    "%s $%04X" % (rg["name"], rg["base"]),
                    rg["kind"],
                    ", ".join("$%04X+i" % b for b in sorted(bs)),
                    "$%04X-$%04X" % (reach[0], reach[-1]),
                    "-" if not hit else "$%04X (%d)" % (hit[0], len(hit)),
                    "yes" if not hit else "no",
                )
            )
    head = ("block", "region", "kind", "the two bases", "reaches", "written in reach", "const row")
    return table(head, rows)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tuneprog_floor.py", description=__doc__.splitlines()[0])
    ap.add_argument("out", help="a finished pipeline --out directory")
    ap.add_argument(
        "--code",
        action="append",
        default=[],
        metavar="LO-HI:NAME",
        help="a named hex code range (default: one range, 'player')",
    )
    args = ap.parse_args(argv)
    out = Path(args.out)
    trace = Trace.load(out)
    regions = json.loads((out / "regions.json").read_text())
    view, structured, _names = pipeline.present(ir.Tuneprog.load(out / "tuneprog.S4.json"))
    ranges = [
        (int(x.split("-")[0], 16), int(x.split("-")[1].split(":")[0], 16), x.split(":")[1])
        for x in args.code
    ] or [(0, 0xFFFF, "player")]
    for title, lines in (
        ("bytes", bytes_table(trace, view)),
        ("mdl", mdl_table(out, trace, view)),
        ("statements", stmt_table(view, structured, regions, ranges)),
        ("pairs", pair_check(view, structured, regions, trace)),
    ):
        print("### %s\n" % title)
        print("\n".join(lines))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
