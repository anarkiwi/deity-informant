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
import struct
import sys
from collections import Counter
from pathlib import Path

from pebble import ProcessPool
from tqdm import tqdm
from vagrant_moses.c64 import poweron_ram

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.smc.expect import lift_reason  # noqa: E402
from deity_informant.smc.machine import MachineOracle, core  # noqa: E402
from deity_informant.smc.space import Cell  # noqa: E402


def sidfile(data):
    """``(kind, image, init, play, songs)`` for a PSID/RSID file, or ``None``."""
    if data[:4] not in (b"PSID", b"RSID") or len(data) < 0x7C:
        return None
    off, load, init, play, songs = struct.unpack(">HHHHH", data[6:16])
    body = data[off:]
    if load == 0:
        load, body = body[0] | body[1] << 8, body[2:]
    if load + len(body) > 0x10000 or load == 0:
        return None
    img = bytearray(poweron_ram())
    img[load : load + len(body)] = body
    return data[:4].decode(), img, init or load, play, max(songs, 1)


def iomap(addr):
    """The $01 value a PSID host banks in for code at ``addr`` (sidplay's I/O map)."""
    if addr < 0xA000:
        return 0x37
    if addr < 0xD000:
        return 0x36
    return 0x35 if addr >= 0xE000 else 0x34


def _parse(sid):
    writer, channel, role, timing, mapping = sid.split(".")
    rmw = channel.endswith("_rmw")
    return Cell(writer, channel[:-4] if rmw else channel, rmw, role, timing, mapping)


def label_tune(path, frames, max_cycles):
    """The feature-cell sids a tune exercises on the machine oracle."""
    tune = sidfile(Path(path).read_bytes())
    if tune is None:
        return {"path": str(path), "skip": "not sid-callable"}
    kind, img, init, play, _ = tune
    if kind == "RSID" and int.from_bytes(Path(path).read_bytes()[0x76:0x78], "big") & 0x02:
        return {"path": str(path), "skip": "needs the C64 BASIC ROM"}
    oracle = MachineOracle(img)
    song = 0
    try:
        if kind == "PSID" and play:
            ports = {"init": iomap(init), "call": iomap(play)}
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
            reasons.add(lift_reason(_parse(sid)) or "lift-exact")
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--sample", type=int, default=0, help="random tunes (0: all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--frames", type=int, default=50)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--timeout", type=float, default=20.0, help="wall-clock seconds per tune")
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if not core():
        print("vagrant-moses native machine not available", file=sys.stderr)
        return 2
    paths = sorted(Path(args.root).rglob("*.sid"))
    if args.sample:
        paths = random.Random(args.seed).sample(paths, min(args.sample, len(paths)))
    rows = []
    with contextlib.ExitStack() as stack, ProcessPool(max_workers=args.jobs) as pool:
        out = stack.enter_context(open(args.out, "w", encoding="utf-8")) if args.out else None
        fut = pool.map(
            _job, [(p, args.frames, args.max_cycles) for p in paths], timeout=args.timeout
        )
        it = fut.result()
        for path in tqdm(paths, desc="label", unit="tune"):
            try:
                row = next(it)
            except StopIteration:
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                row = {"path": str(path), "error": repr(exc)[:80]}
            rows.append(row)
            if out:
                out.write(json.dumps(row) + "\n")
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
