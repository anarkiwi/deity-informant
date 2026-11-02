"""Attribute lift differences between two ``wpcorpus`` runs to their facts or their states.

``factsequiv.py ROOT BEFORE AFTER [--list]``: per flat song lifted in both, lifts each pairing
of the runs' post-init states and facts and counts the songs whose lift statistics differ by
state, by facts, or not at all; ``--list`` prints those songs and their differing addresses.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from tqdm import tqdm

import corpusequiv
import wpcorpus
from deity_informant import trace, wholeprog


def state(img, rec):
    """The post-init memory of a facts record over the song's loaded image ``img``."""
    mem = bytearray(img)
    for addr, hexed in rec["state"]:
        data = bytes.fromhex(hexed)
        mem[addr : addr + len(data)] = data
    return mem


def attribute(img, row, before, after):
    """``(cause, differing state addresses)``: ``cause`` ``same`` when the lifts agree, else
    which of ``state``/``facts`` changes the lift statistics alone (``joint``: only both)."""
    mems = [state(img, r) for r in (before, after)]
    facts = [trace.Facts.from_dict(r["facts"]) for r in (before, after)]
    vol = wpcorpus.volatile_of(row)

    def stats(m, f):
        return wholeprog.lift(mems[m], [row["play"]], vol, facts[f]).stats

    base = stats(0, 0)
    by = [stats(1, 0) != base, stats(0, 1) != base]
    cause = "+".join(n for n, b in zip(("state", "facts"), by) if b) or (
        "same" if stats(1, 1) == base else "joint"
    )
    a, b = (np.frombuffer(bytes(m), np.uint8) for m in mems)
    return cause, np.flatnonzero(a != b).tolist()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    fb = corpusequiv.facts(Path(args.before) / "facts.jsonl.zst")
    fa = corpusequiv.facts(Path(args.after) / "facts.jsonl.zst")
    rows = corpusequiv.rows(Path(args.after) / "rows.jsonl")
    keys = [k for k in fa if k in fb and rows[k].get("mode") == "flat"]
    causes = Counter()
    for key in tqdm(keys, desc="lifts", unit="song"):
        img = wpcorpus.sidfile((Path(args.root) / key[0]).read_bytes())[1]
        cause, diff = attribute(img, rows[key], fb[key], fa[key])
        causes[cause] += 1
        if args.list and cause != "same":
            print(f"  {key[0]} #{key[1]}: {cause}; state {[hex(a) for a in diff[:12]]}")
    print(f"{len(keys)} flat songs: " + ", ".join(f"{c} {n}" for c, n in sorted(causes.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
