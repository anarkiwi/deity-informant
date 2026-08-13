"""The denotation census: what the solve types, and what the ⊤ population is.

A2's deliverable and its kill criterion (docs/denotation-solve.md §4). Value sites
are local occurrences; deref sites are memory accesses at a non-constant address,
with the pointer-rooted subset -- the one §4.6 refuses today -- reported beside.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/denote_census.py                                # the whole cache
  python tools/denote_census.py --tunes MUSICIANS/H/Hubbard_Rob/Commando"""

KILL_VALUE = 0.60  # §4's kill criterion: the value-site rate it stops below
KILL_DEREF = 0.80  # ... and the deref-site rate


def one(entry):
    """One tune's denotation census, or the exception that stopped it."""
    from deity_informant import denote
    from deity_informant import frameproc
    from deity_informant import frameprog
    from deity_informant.c64 import load_psid

    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        sid, sub, secs = entry
        mem, _load, init, play = load_psid(Path(sid).read_bytes())
        mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
        model, _ev = _sweep.decompile(mem, init, play, int(secs * 50), sub)
        prog = frameprog.program(model)
        row = denote.solve(prog).census()
        row.update(frameproc.web_counts(prog.procs, prog.play))
        return {**_sweep.row_head(entry), "wall_s": round(time.monotonic() - t0, 1), **row}
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _pct(n, d):
    return 0.0 if not d else round(100.0 * n / d, 2)


def summary(total):
    """The two rates the kill criterion reads, plus the constructor breakdown."""
    from deity_informant import denote

    val = sum(total["v_" + k] for k in denote.KINDS)
    der = sum(total["d_" + k] for k in denote.KINDS)
    return {
        "value_sites": total["value_sites"],
        "value_typed": val,
        "value_pct": _pct(val, total["value_sites"]),
        "deref_sites": total["deref_sites"],
        "deref_typed": der,
        "deref_pct": _pct(der, total["deref_sites"]),
        "deref_ptr": total["deref_ptr"],
        "deref_ptr_typed": total["deref_ptr_typed"],
        "deref_ptr_pct": _pct(total["deref_ptr_typed"], total["deref_ptr"]),
        "value_by_kind": {k: total["v_" + k] for k in denote.KINDS},
        "deref_by_kind": {k: total["d_" + k] for k in denote.KINDS},
        "value_top_by_cause": {c: total["vtop_" + c] for c in denote.CAUSES},
        "deref_top_by_cause": {c: total["dtop_" + c] for c in denote.CAUSES},
        "webs_typed": total["utyped_w"],
        "webs_unknown": total["u_w"],
        "webs_typed_pct": _pct(total["utyped_w"], total["u_w"]),
        "cells_typed": "%d/%d" % (total["utyped_c"], total["u_c"]),
        "tables_typed": "%d/%d" % (total["utyped_t"], total["u_t"]),
        "by_rule": {r: total["rule_" + r] for r in denote.RULES},
        "webs": total["webs"],
        "web_refused": total["refused"],
        "web_entry": total["entry"],
        "web_opaque": total["opaque"],
        "web_width": total["width"],
    }


def verdict(sm):
    """PASS or STOP against §4's stated thresholds, which nothing here may move."""
    ok = sm["value_pct"] >= KILL_VALUE * 100 and sm["deref_pct"] >= KILL_DEREF * 100
    return "PASS" if ok else "STOP"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "denote_census.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(one, tunes))
    errors = [r for r in rows if "error" in r]
    total = Counter()
    for r in rows:
        total.update({k: v for k, v in r.items() if isinstance(v, int) and k != "wall_s"})
    sm = summary(total)
    out = {
        "tunes": len(rows) - len(errors),
        "errors": errors,
        "wall_s": round(time.monotonic() - t0, 1),
        "verdict": verdict(sm),
        "summary": sm,
        "totals": dict(total),
        "rows": sorted(rows, key=lambda r: r["tune"]),
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(
        "%d tunes, %d errors, %.1fs\nvalue %d/%d (%.2f%%)  deref %d/%d (%.2f%%)"
        "  deref via ptr %d/%d (%.2f%%)\nverdict %s"
        % (
            out["tunes"],
            len(errors),
            out["wall_s"],
            sm["value_typed"],
            sm["value_sites"],
            sm["value_pct"],
            sm["deref_typed"],
            sm["deref_sites"],
            sm["deref_pct"],
            sm["deref_ptr_typed"],
            sm["deref_ptr"],
            sm["deref_ptr_pct"],
            out["verdict"],
        )
    )
    print(
        "webs typed %d/%d (%.2f%%)  cells %s  tables %s"
        % (
            sm["webs_typed"],
            sm["webs_unknown"],
            sm["webs_typed_pct"],
            sm["cells_typed"],
            sm["tables_typed"],
        )
    )
    print("by rule            %s" % sm["by_rule"])
    print("value by kind      %s" % sm["value_by_kind"])
    print("value top by cause %s" % {k: v for k, v in sm["value_top_by_cause"].items() if v})
    print("deref by kind      %s" % sm["deref_by_kind"])
    print("deref top by cause %s" % {k: v for k, v in sm["deref_top_by_cause"].items() if v})
    print(
        "webs %d, refused %d (entry %d, opaque %d, width %d)"
        % (sm["webs"], sm["web_refused"], sm["web_entry"], sm["web_opaque"], sm["web_width"])
    )
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
