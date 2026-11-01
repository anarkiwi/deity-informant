"""Check the machine oracle's two backends agree: native SMC recorder vs Python Classifier.

``python tools/smcequiv.py [--corpus] [--hvsc ROOT --sample N --seed S --frames F]`` labels the
corpus and/or seeded HVSC songs (PSID: F play calls; else F frame-indexed IRQ frames) with both,
compares cells, first-seen indices, unclassified counts and returns, and prints per-unit costs.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from pebble import ProcessPool
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# pylint: disable=wrong-import-position
from deity_informant.smc.asm import assemble  # noqa: E402
from deity_informant.smc.machine import MachineOracle  # noqa: E402
from deity_informant.smc.synthm import witnesses  # noqa: E402
from smclabel import iomap, sidfile  # noqa: E402 pylint: disable=wrong-import-order

BACKENDS = ("python", "native")


def corpus_cases(path=ROOT / "tests" / "smc_corpus.json"):
    """``(name, image, run kwargs, units)`` of every corpus program on the machine."""
    out = []
    wit = {"machine." + w.name: w for w in witnesses()}
    for rec in json.loads(Path(path).read_text(encoding="utf-8")):
        if rec["name"] in wit:
            w = wit[rec["name"]]
            img, labels = w.image()
            calls = [(labels[w.entry], c[-1] if len(c) > 1 else 0) for c in w.calls]
            kw = {"calls": calls, "frames": w.frames, "irq_setup": w.irq_setup}
            out.append((rec["name"], img, kw | {"index_frames": bool(w.frames)}, w.frames or 1))
            continue
        img, labels = assemble(rec["source"])
        pcs = [labels[e] for e in rec["entries"]]
        calls = [(pcs[e], a) for e, a in rec["calls"]]
        init = [(labels.get(p, p), a) for p, a in rec["init"]]
        out.append((rec["name"], img, {"calls": calls, "init": init}, len(calls)))
    return out


def song_case(path, frames, max_cycles):
    """``(name, image, run kwargs, units)`` of a PSID/RSID song's start song, or ``None``."""
    data = Path(path).read_bytes()
    tune = sidfile(data)
    if tune is None or (tune[0] == "RSID" and data[0x77] & 0x02):
        return None
    kind, img, init, play, songs = tune
    song = min(max(int.from_bytes(data[0x10:0x12], "big"), 1), songs) - 1
    kw = {"init": [(init, song)], "call_cycles": max_cycles}
    if kind == "PSID" and play:
        kw |= {"calls": [(play, song)] * frames}
        kw |= {"ports": {"init": iomap(init), "call": iomap(play)}}
    else:
        kw |= {"calls": [], "frames": frames, "irq_setup": True, "index_frames": True}
    return str(path), img, kw, frames


def label(img, kw, backend):
    """``(result, seconds)`` of one backend's run: first-seen per cell, unclassified count and
    whether the run returned."""
    t = time.perf_counter()
    run = MachineOracle(img, backend=backend).run(**kw)
    first = {c.sid: int(i) for c, i in run.first.items()}
    assert set(first) == {c.sid for c in run.cells}
    result = {"first": first, "unclassified": run.n_unclassified, "returned": run.returned}
    return result, time.perf_counter() - t


def compare(case):
    """Both backends on ``case``: a row with each result, cost and whether they agree."""
    name, img, kw, units = case
    row = {"name": name, "units": units}
    for b in BACKENDS:
        row[b], row[b + "_s"] = label(img, kw, b)
    row["cells_equal"] = row["python"]["first"].keys() == row["native"]["first"].keys()
    row["first_equal"] = row["python"]["first"] == row["native"]["first"]
    row["equal"] = row["python"] == row["native"]
    return row


def _job(args):
    case = song_case(*args)
    return {"name": str(args[0]), "skip": True} if case is None else compare(case)


def summary(rows):
    """Counts of agreeing rows and the per-unit labelling cost of each backend."""
    done = [r for r in rows if "equal" in r]
    units = sum(r["units"] for r in done) or 1
    out = {
        "rows": len(rows),
        "compared": len(done),
        "skipped": sum("skip" in r for r in rows),
        "errors": sum("error" in r for r in rows),
        "cells_equal": sum(r["cells_equal"] for r in done),
        "first_equal": sum(r["first_equal"] for r in done),
        "equal": sum(r["equal"] for r in done),
        "units": units,
        "distinct_cells": len({s for r in done for s in r["python"]["first"]}),
    }
    for b in BACKENDS:
        out[b + "_ms_per_unit"] = 1e3 * sum(r[b + "_s"] for r in done) / units
    out["speedup"] = out["python_ms_per_unit"] / max(out["native_ms_per_unit"], 1e-9)
    out["mismatches"] = [r["name"] for r in done if not r["equal"]][:20]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--hvsc")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--frames", type=int, default=1000)
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--timeout", type=float, default=3600.0, help="wall-clock seconds per song")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    rows = []
    if args.corpus:
        rows += [compare(c) for c in tqdm(corpus_cases(), desc="corpus", unit="prog")]
    if args.hvsc:
        paths = sorted(Path(args.hvsc).rglob("*.sid"))
        paths = random.Random(args.seed).sample(paths, min(args.sample, len(paths)))
        with ProcessPool(max_workers=args.jobs) as pool:
            jobs = [(p, args.frames, args.max_cycles) for p in paths]
            it = pool.map(_job, jobs, timeout=args.timeout).result()
            for path in tqdm(paths, desc="hvsc", unit="song"):
                try:
                    rows.append(next(it))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    rows.append({"name": str(path), "error": repr(exc)[:120]})
    if args.out:
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps(summary(rows), indent=1))
    return 0 if all(r.get("equal", True) for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
