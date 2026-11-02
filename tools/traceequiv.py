"""Check the corpus machine runs' two backends agree: native trace/scan vs Python decode hooks.

``python tools/traceequiv.py ROWS --hvsc ROOT [--irq] [--sample N --seed S --jobs J --out F]``
runs sampled banked PSID songs (facts, records, the lift's check, cells) or, with ``--irq``,
interrupt-driven songs (the ``irq`` scan) of a ``tools/wpcorpus.py`` rows file at full length.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

# pylint: disable=wrong-import-position
import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from pebble import ProcessPool  # noqa: E402
from tqdm import tqdm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from deity_informant import c64, wholeprog  # noqa: E402
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-order
from smclabel import iomap, sidfile  # noqa: E402 pylint: disable=wrong-import-order

BACKENDS = ("python", "native")


def _digest(rec):
    h = hashlib.blake2b(digest_size=16)
    h.update(repr((rec["halt"], list(rec["reg"]), [tuple(e) for e in rec["log"]])).encode())
    h.update(bytes(rec["ram"]))
    return h.digest()


def run(img, song, backend, max_cycles=2_000_000):
    """One backend's banked run of ``song`` (``init``, ``play``, ``song``, ``calls``,
    ``clock``): ``(facts, records digest, check, cells, ...), seconds``."""
    init, play = song["init"], song["play"]
    ports = {"init": iomap(init), "call": iomap(play)}
    t = time.perf_counter()
    host = c64._Host(  # pylint: disable=protected-access
        img, ntsc=song["clock"] == "NTSC", backend=backend
    )
    host.drive([(play, 0)] * song["calls"], [(init, song["song"] - 1)], max_cycles, ports)
    inits, records, n = host.split()
    facts = host.facts()
    seconds = time.perf_counter() - t
    digest = hashlib.blake2b(b"".join(map(_digest, [*inits, *records])), digest_size=16)
    last, bad = inits[-1], None
    if last["halt"] == "return":
        bank = wpcorpus._bank()  # pylint: disable=protected-access
        prog = wholeprog.lift(last["ram"], [play], [c64.DEVICES], facts, bank=bank)
        bad = c64.check(prog, last["ram"], last["reg"], host.split()[1], ports["call"], max_cycles)
    return {
        "facts": facts.to_dict(),
        "records": digest.hexdigest(),
        "calls_run": n,
        "check": bad,
        "cells": sorted(c.sid for c in host.result.cells),
    }, seconds


def compare(img, song, max_cycles=2_000_000):
    """Both backends on ``song``: whether facts, records, check and cells agree, and costs."""
    row = {"path": song.get("path"), "song": song["song"]}
    res = {}
    for b in BACKENDS:
        res[b], row[b + "_s"] = run(img, song, b, max_cycles)
    py, nat = res["python"], res["native"]
    row |= {k + "_equal": py[k] == nat[k] for k in py}
    row |= {"calls_run": py["calls_run"], "check": py["check"], "equal": py == nat}
    return row


def compare_irq(data, song, max_cycles=2_000_000):
    """Both backends' ``_IrqScan`` of ``song`` of the file ``data`` over its ``calls`` frames,
    started as ``wpcorpus.irq_boot`` does, and costs."""
    boot = wpcorpus.irq_boot(song, data)
    row = {"path": song.get("path"), "song": song["song"], "calls_run": song["calls"]}
    res = {}
    for b in BACKENDS:
        t = time.perf_counter()
        o = wpcorpus._IrqScan(  # pylint: disable=protected-access
            boot.image, ntsc=song["clock"] == "NTSC", backend=b, roms=boot.roms
        )
        res[b] = o.scan(boot, song["calls"], max_cycles)
        row[b + "_s"] = time.perf_counter() - t
    row |= {"irq": res["python"], "equal": res["python"] == res["native"]}
    return row | {"scan_equal": row["equal"]}


def _job(args):
    root, song, max_cycles, irq = args
    data = (Path(root) / song["path"]).read_bytes()
    return (
        compare_irq(data, song, max_cycles) if irq else compare(sidfile(data)[1], song, max_cycles)
    )


def summary(rows):
    """Counts of agreeing rows and each backend's cost per call."""
    done = [r for r in rows if "equal" in r]
    calls = sum(r["calls_run"] for r in done) or 1
    out = {"rows": len(rows), "compared": len(done), "errors": len(rows) - len(done)}
    for k in sorted({k for r in done for k in r if k.endswith("_equal")}):
        out[k] = sum(r.get(k, False) for r in done)
    out |= {"equal": sum(r["equal"] for r in done), "calls": calls}
    for b in BACKENDS:
        out[b + "_ms_per_call"] = 1e3 * sum(r[b + "_s"] for r in done) / calls
    out["speedup"] = out["python_ms_per_call"] / max(out["native_ms_per_call"], 1e-9)
    out["mismatches"] = [r["path"] for r in rows if not r.get("equal")][:20]
    return out


def _banked(r):
    return r.get("mode") == "banked" and r.get("init_halt") == "return" and "error" not in r


def _irq(r):
    return "irq" in r and "error" not in r


def sample_rows(path, sample, seed, pick=_banked):
    """A seeded sample of a rows file's songs (read only) that ``pick`` takes: by default the
    banked songs whose init returned."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if pick(r):
                rows.append(r)
    return random.Random(seed).sample(rows, min(sample, len(rows)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rows")
    ap.add_argument("--hvsc", required=True)
    ap.add_argument("--irq", action="store_true", help="interrupt-driven songs' scans")
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--timeout", type=float, default=7200.0, help="wall-clock seconds per song")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    songs = sample_rows(args.rows, args.sample, args.seed, _irq if args.irq else _banked)
    rows = []
    with ProcessPool(max_workers=args.jobs) as pool:
        jobs = [(args.hvsc, s, args.max_cycles, args.irq) for s in songs]
        it = pool.map(_job, jobs, timeout=args.timeout).result()
        for s in tqdm(songs, desc="irq" if args.irq else "banked", unit="song"):
            try:
                rows.append(next(it))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                rows.append({"path": s["path"], "song": s["song"], "error": repr(exc)[:200]})
    if args.out:
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps(summary(rows), indent=1))
    return 0 if all(r.get("equal") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
