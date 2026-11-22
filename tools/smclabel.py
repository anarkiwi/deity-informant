"""Label real HVSC tunes with the SMC machine oracle to prioritise which gaps to fix.

``python tools/smclabel.py ROOT [--sample N --seed S --jobs J --frames F --timeout T]`` runs
each PSID/RSID tune's init then its play (PSID: called per frame; RSID: driven by the tune's own
IRQ) on the vagrant-moses machine and records the feature cells it exercises. The summary counts,
per cell and per lift-gap reason, how many tunes need it.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import sys
from collections import Counter
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import as_completed
from pathlib import Path

from pebble import ProcessPool
from tqdm import tqdm
from pysidtracker.errors import SidError
from pysidtracker.header import parse_sid_header
from pysidtracker.image import SidImage
from pysidtracker.oracle import PAL_FLAG
from pysidtracker.poweron import ram_pattern
from vagrant_moses.c64 import psid_iomap

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.smc.expect import lift_reason  # noqa: E402
from deity_informant.smc.machine import MachineOracle, core  # noqa: E402
from deity_informant.smc.outcomes import parse_cell  # noqa: E402


def sidfile(data):
    """``(kind, image, init, play, songs)`` for a PSID/RSID file, or ``None``: the tune over the
    power-on RAM, ``$02A6`` the video flag of its clock (NTSC only for an NTSC-only header)."""
    try:
        tune = SidImage.from_sid(data)
    except SidError:
        return None
    h = tune.header
    if not tune.load:
        return None
    img = ram_pattern()
    img[PAL_FLAG] = int(not h.is_ntsc)
    img[tune.load : tune.end] = tune.image
    return h.magic.decode(), img, h.init_address or tune.load, h.play_address, max(h.songs, 1)


def sweep_parser(doc, timeout, timeout_help="wall-clock seconds per tune"):
    """The arguments of a sweep over the tunes under ``root``: ``--sample``, ``--seed``,
    ``--jobs`` and ``--timeout`` (default ``timeout``)."""
    ap = argparse.ArgumentParser(description=doc.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--sample", type=int, default=0, help="random tunes (0: all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--timeout", type=float, default=timeout, help=timeout_help)
    return ap


def tunes(root, sample=0, seed=0):
    """The ``.sid`` files under ``root``, sorted, or a ``seed``-ed random ``sample`` of them."""
    paths = sorted(Path(root).rglob("*.sid"))
    return random.Random(seed).sample(paths, min(sample, len(paths))) if sample else paths


def pool_rows(fn, jobs, workers, timeout, desc, failed, out=None):
    """The rows ``fn(job)`` of ``jobs`` in a pool of ``workers`` processes, each job at most
    ``timeout`` seconds, in completion order with progress, each also written to the JSON
    lines file ``out`` when given; a job that times out or raises gives
    ``failed(job, "timeout" or the exception's repr)``."""
    rows = []
    with contextlib.ExitStack() as stack, ProcessPool(max_workers=workers) as pool:
        f = stack.enter_context(open(out, "w", encoding="utf-8")) if out else None
        futs = {pool.schedule(fn, (j,), timeout=timeout): j for j in jobs}
        for fut in tqdm(as_completed(futs), total=len(futs), desc=desc, unit="tune"):
            try:
                row = fut.result()
            except FutureTimeout:
                row = failed(futs[fut], "timeout")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                row = failed(futs[fut], repr(exc)[:200])
            rows.append(row)
            if f:
                f.write(json.dumps(row) + "\n")
    return rows


def failed_path(job, error):
    """The :func:`pool_rows` row of a failed job on the tune ``job[0]``."""
    return {"path": str(job[0]), "error": error}


def label_tune(path, frames, max_cycles):
    """The feature-cell sids a tune exercises on the machine oracle."""
    data = Path(path).read_bytes()
    tune = sidfile(data)
    if tune is None:
        return {"path": str(path), "skip": "not sid-callable"}
    kind, img, init, play, _ = tune
    if kind == "RSID" and parse_sid_header(data).flags & 0x02:
        return {"path": str(path), "skip": "needs the C64 BASIC ROM"}
    oracle = MachineOracle(img)
    song = 0
    try:
        if kind == "PSID" and play:
            ports = {"init": psid_iomap(init), "call": psid_iomap(play)}
            run = oracle.run(
                [(play, song)] * frames, init=[(init, song)], call_cycles=max_cycles, ports=ports
            )
        else:
            run = oracle.run([], init=[(init, song)], frames=frames, irq_setup=True,
                             call_cycles=max_cycles)  # fmt: skip
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"path": str(path), "error": repr(exc)[:120]}
    return {
        "path": str(path),
        "kind": kind,
        "cells": sorted(c.sid for c in run.cells),
        "unclassified": run.n_unclassified,
        "returned": run.returned,
    }


def _job(args):
    try:
        return label_tune(*args)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"path": str(args[0]), "error": repr(exc)[:120]}


def summary(rows):
    """Per-cell and per-lift-gap-reason tune counts across the labelled tunes."""
    ok = [r for r in rows if "cells" in r]
    per_cell = Counter()
    per_reason = Counter()
    for r in ok:
        reasons = set()
        for sid in r["cells"]:
            per_cell[sid] += 1
            reasons.add(lift_reason(parse_cell(sid)) or "lift-exact")
        for reason in reasons:
            per_reason[reason] += 1
    return {
        "tunes": len(rows),
        "labelled": len(ok),
        "skipped": sum("skip" in r for r in rows),
        "errors": sum("error" in r for r in rows),
        "distinct_cells": len(per_cell),
        "per_reason": dict(per_reason.most_common()),
        "top_cells": per_cell.most_common(25),
    }


def main(argv=None):
    ap = sweep_parser(__doc__, 20.0)
    ap.add_argument("--out", help="the rows as JSON lines")
    ap.add_argument("--frames", type=int, default=50)
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    args = ap.parse_args(argv)
    if not core():
        print("vagrant-moses native machine not available", file=sys.stderr)
        return 2
    jobs = [(p, args.frames, args.max_cycles) for p in tunes(args.root, args.sample, args.seed)]
    rows = pool_rows(_job, jobs, args.jobs, args.timeout, "label", failed_path, args.out)
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
