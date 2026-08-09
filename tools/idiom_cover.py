"""Stage 1's completeness gate: every node of every obligation, accounted to a row.

The unit is the node, not the slice: an unaccounted node names a missing catalog
row and is reported with the site behind it (tune, store base, pc), which is what
the claims discipline requires before anything is said about it.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import _sweep
import lift_residue
import storage_census

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/idiom_cover.py                                 # every exemplar, full song
  python tools/idiom_cover.py --tunes Hubbard_Rob/Commando --show 20
  python tools/idiom_cover.py --frames 1500 -o out/idiom_cover.json"""

EXEMPLARS = (  # docs/idiom-catalog.md's exemplar set: one per family, the plan's seven inside
    "Hubbard_Rob/Commando",
    "Galway_Martin/Comic_Bakery",
    "Galway_Martin/Athena",
    "Follin_Tim/Ghouls_n_Ghosts",
    "Follin_Tim/Agent_X_II_The_Mad_Profs_Back",
    "Jammer/Grid_Runner",
    "Cadaver/Aces_High",
    "Daf/Alioth",
    "Cleve/ABC_Music",
    "Alfatech/Galway-tune",
    "Beast/Discmonsters_Intro",
    "Tel_Kees/Before_I_Forget",
    "Deek/4_Tunes",
    "Chabee/Angry_Birds",
    "Buckley_Kevin/Down_Under",
    "Goto80/Automatas",
)


def accounting(prog):
    """``(counts, gaps, tally)``: the catalog's histogram and every unaccounted node.

    A gap carries the obligation it sits in, so ``disasm_tune`` can be pointed at
    the site; ``tally`` is the obligation count per kind plus the state rows."""
    from deity_informant import frameproc, idioms

    counts, gaps, raw = Counter(), [], 0
    sid, upd = idioms.obligations(prog)
    for ob in sid + upd:
        per, missed = idioms.cover(ob.value)
        counts.update(per)
        raw += idioms.size(ob.value)
        for n in missed:
            gaps.append(
                {
                    "shape": lift_residue._skeleton(n, 3),
                    "text": frameproc._fmt(n)[:96],
                    "kind": ob.kind,
                    "base": "$%04X" % ob.base if ob.base is not None else idioms.UNRESOLVED,
                    "field": ob.field,
                    "entry": "$%04X" % ob.entry,
                    "site": "$%04X" % ob.site,
                    "at": ob.at,
                }
            )
    cells = idioms.state_cells(prog)
    hit = {ob.base for ob in upd}
    tally = {
        "sid_stores": len(sid),
        "cell_updates": len(upd),
        "state_rows": len(prog.state),
        "state_cells": len(cells),
        "cells_updated": len(hit - {None}),
        "cells_quiet": sorted("$%04X" % c for c in cells if c not in hit),
        "unresolved": sum(1 for ob in sid + upd if ob.base is None),
        "nodes": sum(counts.values()) + len(gaps),
        "raw_nodes": raw,
    }
    return counts, gaps, tally


def one(entry, frames):
    """One tune's accounting row, or the exception that stopped it."""
    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        _model, prog, nframes = storage_census.build(entry, frames)
        counts, gaps, tally = accounting(prog)
        return {
            **_sweep.row_head(entry),
            "build_s": round(time.monotonic() - t0, 1),
            "frames": nframes,
            **tally,
            "rows": dict(counts),
            "unaccounted": gaps,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _merge(done):
    """``(row counts, tunes per row, gaps by shape)`` over the clean rows."""
    counts, tunes, shapes = Counter(), Counter(), defaultdict(list)
    for r in done:
        counts.update(r["rows"])
        tunes.update(r["rows"].keys())
        for g in r["unaccounted"]:
            shapes[g["shape"]].append({**g, "tune": r["tune"]})
    return counts, tunes, shapes


def _print(done, counts, tunes, shapes, show):
    """The per-tune obligations, the row histogram, then every gap with its site."""
    from deity_informant import idioms

    head = ("tune", "sid", "upd", "cells", "wrtn", "quiet", "unres", "nodes", "raw")
    print("\n%-46s %5s %5s %5s %5s %5s %5s %6s %6s" % head)
    for r in done:
        print(
            "%-46s %5d %5d %5d %5d %5d %5d %6d %6d"
            % (
                r["tune"],
                r["sid_stores"],
                r["cell_updates"],
                r["state_cells"],
                r["cells_updated"],
                len(r["cells_quiet"]),
                r["unresolved"],
                r["nodes"],
                r["raw_nodes"],
            )
        )
    print("\n%-16s %8s %6s  %s" % ("row", "nodes", "tunes", "normal form"))
    for rid, n in counts.most_common():
        print("%-16s %8d %6d  %s" % (rid, n, tunes[rid], idioms.FORMS.get(rid, "?")))
    unused = [r.id for r in idioms.ROWS if not counts[r.id]]
    if unused:
        print("\nrows no exemplar carries: %s" % ", ".join(unused))
    total = sum(len(v) for v in shapes.values())
    print("\nunaccounted nodes: %d over %d shape(s)" % (total, len(shapes)))
    for shape in sorted(shapes, key=lambda k: -len(shapes[k])):
        hits = shapes[shape]
        print("  %5d  %s" % (len(hits), shape))
        for g in hits[:show]:
            print(
                "         %s %s %s @ %s site %s [%d]  %s"
                % (g["tune"], g["kind"], g["base"], g["entry"], g["site"], g["at"], g["text"])
            )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the exemplars")
    ap.add_argument("--frames", default="full", help="frame cap, or 'full' (default full)")
    ap.add_argument("--show", type=int, default=4, help="sites listed per unaccounted shape")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "idiom_cover.json"))
    args = ap.parse_args()

    frames = None if args.frames == "full" else int(args.frames)
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else list(EXEMPLARS))
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(one, [(e, frames) for e in tunes]))
    done = sorted((r for r in rows if "error" not in r), key=lambda r: r["tune"])
    counts, tunes_per, shapes = _merge(done)
    gaps = sum(len(v) for v in shapes.values())
    out = {
        "tunes": len(done),
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        "frames": args.frames,
        "obligations": sum(r["sid_stores"] + r["cell_updates"] for r in done),
        "nodes": sum(r["nodes"] for r in done),
        "unaccounted": gaps,
        "unaccounted_shapes": {k: len(v) for k, v in shapes.items()},
        "row_counts": dict(counts),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("%d tunes, %d refused, %.1fs" % (len(done), len(out["refused"]), out["wall_s"]))
    _print(done, counts, tunes_per, shapes, args.show)
    sys.exit(1 if gaps or out["refused"] else 0)


if __name__ == "__main__":
    main()
