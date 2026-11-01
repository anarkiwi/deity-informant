"""Check bank-aware lifts of the HVSC tunes that need banking against the C64 machine.

``python tools/wpbank.py [--rows out/smc500.jsonl] [--calls K] [--max-cycles C] [--jobs J]
[--timeout S] [--real-roms] [--out F]`` lifts each selected PSID's play from its post-init
RAM with machine facts and checks ``K`` lifted calls against the machine; one JSON line each.
"""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import as_completed
from pathlib import Path

from pebble import ProcessPool
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
from deity_informant import c64  # noqa: E402
from deity_informant.smc.expect import lift_reason, machine_cell  # noqa: E402
from smclabel import _parse, iomap, sidfile  # noqa: E402


def selected(row):
    """Whether an smclabel row is checked: a PSID needing banking, or any RSID whose cells are
    not all KERNAL-vector fetches."""
    cells = [_parse(s) for s in row.get("cells", ())]
    if row.get("kind") == "RSID":
        return any(c.role != "kernal_vector" for c in cells)
    return any(machine_cell(c) and lift_reason(c) is None for c in cells)


def bank_check(path, calls, max_cycles=2_000_000, roms=None):
    """The bank-aware lift check of one tune."""
    tune = sidfile(Path(path).read_bytes())
    if tune is None:
        return {"tune": str(path), "skip": "not sid-callable"}
    kind, img, init, play, _ = tune
    if kind == "RSID" or not play:
        return {"tune": str(path), "skip": "irq-driven"}
    ports = {"init": iomap(init), "call": iomap(play)}
    run = [(play, 0)] * calls
    res = c64.lift_check(img, play, run, run, [(init, 0)], ports, max_cycles, roms)
    if res.prog is None:
        return {"tune": str(path), "skip": "init " + res.init["halt"]}
    return {
        "tune": str(path),
        "exact": res.bad is None,
        "mismatch": res.bad,
        "deopts": sum(res.prog.deopts.values()),
        "stack_private": res.prog.stats["stack_private"],
        "configs": sorted({b for bs in res.facts.banks.values() for b in bs}),
        "returned": len(res.records) == calls and res.records[-1]["halt"] == "return",
    }


def _job(a):
    try:
        return bank_check(*a)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"tune": str(a[0]), "error": repr(e)[:200]}


def summary(rows):
    """Counts of checked, exact and failing tunes, and the inexact tunes."""
    ok = [r for r in rows if "exact" in r]
    return {
        "tunes": len(rows),
        "checked": len(ok),
        "exact": sum(r["exact"] for r in ok),
        "skipped": Counter(r["skip"] for r in rows if "skip" in r),
        "errors": sum("error" in r for r in rows),
        "timeouts": [r["tune"] for r in rows if r.get("error") == "timeout"],
        "inexact": [r["tune"] for r in ok if not r["exact"]],
        "mismatch_kinds": Counter(r["mismatch"][1] for r in ok if r["mismatch"]),
        "deopting": sum(r["deopts"] > 0 for r in ok),
        "stack_private": sum(bool(r["stack_private"]) for r in ok),
        "configs": Counter(b for r in ok for b in r["configs"]),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", default="out/smc500.jsonl", help="smclabel output")
    ap.add_argument("--calls", type=int, default=300)
    ap.add_argument("--max-cycles", type=int, default=2_000_000, help="per call")
    ap.add_argument("--timeout", type=float, default=60, help="seconds per tune")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--real-roms", action="store_true", help="the real ROMs, not ROM-less")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    lines = Path(args.rows).read_text(encoding="utf-8").splitlines()
    paths = [r["path"] for r in map(json.loads, filter(None, lines)) if selected(r)]
    roms = c64.real_roms() if args.real_roms else None
    out = open(args.out, "w", encoding="utf-8") if args.out else None  # noqa: SIM115
    rows = []
    with ProcessPool(max_workers=args.jobs) as pool:
        futs = {
            pool.schedule(
                _job, args=((p, args.calls, args.max_cycles, roms),), timeout=args.timeout
            ): p
            for p in paths
        }
        for f in tqdm(as_completed(futs), total=len(paths), desc="wpbank", unit="tune"):
            try:
                r = f.result()
            except FutureTimeout:
                r = {"tune": str(futs[f]), "error": "timeout"}
            rows.append(r)
            if out:
                out.write(json.dumps(r) + "\n")
    if out:
        out.close()
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
