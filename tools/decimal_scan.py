"""Find the songs of a corpus run that execute ADC, SBC, ARR, RRA or ISC with the decimal flag set.

``python tools/decimal_scan.py ROOT CORPUS_DIR [--out FILE] [--paths FILE] [--jobs J] [--limit N]``

Each song's facts record (``CORPUS_DIR/facts.jsonl.zst``) gives the instruction starts its run
executed and the RAM they ran from. Only a song that executes ``SED``, ``PLP`` or ``RTI`` can
have the decimal flag set at an arithmetic instruction; a PSID song with a play routine that
does is then run on the native core, call by call, tracing every instruction and its flags.
A banked song is run on flat RAM (``banked`` in its record). Interrupt-driven songs are reported by the static test alone. ``--paths`` writes the paths of
the songs that ran decimal arithmetic, one per line, for ``tools/wpcorpus.py --paths``.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

# pylint: disable=wrong-import-position
import argparse
import json
import sys
import time
from collections import Counter
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory
from jennings.opcodes import OPCODES
from smclabel import sidfile
from wpcorpus import _facts_lines

ARITH_MNEMONICS = ("ADC", "SBC", "ARR", "RRA", "ISC")
ARITH = np.array([OPCODES[o][0] in ARITH_MNEMONICS for o in range(256)])
SED = next(o for o, (m, _) in OPCODES.items() if m == "SED")
FLAG_LOADS = np.array([OPCODES[o][0] in ("PLP", "RTI") for o in range(256)])
DECIMAL = 8
BUDGET_S = 120


def traced_hits(img, reg, play, calls, mc):
    """``(first call, instructions)`` that ran decimal arithmetic in ``calls`` calls of ``play``
    from RAM ``img`` and registers ``reg`` (``PcodeVM`` order), or ``None``; ``(calls, 0)`` is
    a run cut short by the time budget."""
    mem = NativeMemory(bytes(img))
    mpu = NativeMPU(memory=mem)
    mpu.a, mpu.x, mpu.y, mpu.sp = reg[:4]
    mpu.p = sum(reg[i] << b for i, b in ((8, 0), (9, 1), (10, 2), (11, 3), (13, 6), (14, 7)))
    ram = np.frombuffer(memoryview(mem), np.uint8)
    t0 = time.perf_counter()
    for n in range(calls):
        before = mpu.p
        tr = mpu.run_to_rts_traced(play, 0, mc, True)
        if not len(tr):
            continue
        p = np.asarray(tr.p)
        decimal = np.concatenate(([before], p[:-1])) & DECIMAL != 0
        hits = int((ARITH[ram[np.asarray(tr.pc)]] & decimal).sum())
        if hits:
            return n, hits
        if not n & 255 and time.perf_counter() - t0 > BUDGET_S:
            return calls, 0
    return None


def scan(job, root, mc):
    """The record of one song: static counts of the executed arithmetic, decimal-flag setters,
    and, where the song can be run, its decimal arithmetic."""
    line, row = job
    rec = json.loads(line)
    out = {"path": rec["path"], "song": rec["song"], "kind": row["kind"]}
    img = sidfile((Path(root) / rec["path"]).read_bytes())[1]
    for addr, hexes in rec["state"]:
        img[addr : addr + len(hexes) // 2] = bytes.fromhex(hexes)
    ops = np.frombuffer(img, np.uint8)[np.asarray(rec["facts"]["starts"], np.int64)]
    out |= {
        "arith": int(ARITH[ops].sum()),
        "sed": int((ops == SED).sum()),
        "flag_loads": int(FLAG_LOADS[ops].sum()),
    }
    if not out["arith"] or not (out["sed"] or out["flag_loads"]):
        return out | {"decimal": False}
    if row["kind"] != "PSID" or not row["play"]:
        return out | {"decimal": None}
    found = traced_hits(img, rec["reg"], row["play"], row["calls_run"], mc)
    out |= {"banked": row["mode"] == "banked"}
    if found is None:
        return out | {"decimal": False}
    return out | {"decimal": True, "first_call": found[0], "hits": found[1]}


def jobs(corpus, limit):
    """``(facts line, row)`` of each song with facts, in the facts file's order."""
    rows = {}
    with open(Path(corpus) / "rows.jsonl", encoding="utf-8") as src:
        for line in src:
            r = json.loads(line)
            rows[(r["path"], r["song"])] = {
                k: r.get(k) for k in ("kind", "play", "calls_run", "mode")
            }
    for n, (_, line) in enumerate(_facts_lines(Path(corpus) / "facts.jsonl.zst")):
        if limit and n >= limit:
            return
        head = json.loads(line[: line.index(', "state"')] + "}")
        yield line, rows[(head["path"], head["song"])]


def main(argv=None):
    """Scan ``CORPUS_DIR`` and write one record per song to ``--out``."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("corpus")
    ap.add_argument("--out", default="out/decimal/scan.jsonl")
    ap.add_argument("--paths", default="out/decimal/paths.txt")
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    args = ap.parse_args(argv)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    count = Counter()
    with Pool(min(args.jobs, 24)) as pool, open(args.out, "w", encoding="utf-8") as out:
        work = pool.imap_unordered(
            partial(scan, root=args.root, mc=args.max_cycles), jobs(args.corpus, args.limit), 8
        )
        for r in tqdm(work, unit="song"):
            out.write(json.dumps(r) + "\n")
            count["songs"] += 1
            count["decimal"] += r["decimal"] is True
            count["candidates"] += r["decimal"] is None
    paths = sorted(
        {json.loads(l)["path"] for l in open(args.out, encoding="utf-8") if '"decimal": true' in l}
    )
    Path(args.paths).write_text("".join(p + "\n" for p in paths), encoding="utf-8")
    print(dict(count), "files:", len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
