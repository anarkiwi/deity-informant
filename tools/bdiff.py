"""Diff a banked song's lift against the machine at its first mismatching call.

``bdiff.py PATH SONG [--calls N] [--hvsc ROOT]``: a ``wpcorpus``-style run whose calls replay
the machine's device reads; prints the machine's, lift's and reference's logs around the
first difference, their registers and the RAM the lift and the reference leave differently.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from deity_informant import c64, wholeprog  # noqa: E402

import wpcorpus  # noqa: E402 pylint: disable=wrong-import-order

HVSC = "/scratch/datasets/hvsc/C64Music"
MC = 2_000_000


def session(data, song, calls, host=None):
    """A banked machine run of ``song`` of the SID file ``data`` over ``calls`` play calls on
    ``host`` (a ``wpcorpus._Records`` class): ``{"img", "play", "ports", "inits", "records",
    "prog", "host"}``."""
    _, img, init, play, _ = wpcorpus.sidfile(data)
    hdr = wpcorpus.header(data)
    ports = {"init": wpcorpus.iomap(init), "call": wpcorpus.iomap(play)}
    model = wpcorpus.sid_model({"sid_models": [(hdr["flags"] >> 4) & 3]})
    h = (host or wpcorpus._Records)(  # pylint: disable=protected-access
        img, ntsc=hdr["clock"] == "NTSC", model=model
    )
    h.drive([(play, 0)] * calls, [(init, song - 1)], MC, ports)
    inits, records, _ = h.split()
    records = [dict(r, ram=bytes(r["ram"])) for r in records]
    bank = wpcorpus._bank()  # pylint: disable=protected-access
    prog = wholeprog.lift(inits[-1]["ram"], [play], [c64.DEVICES], h.facts(), bank=bank)
    return {
        "img": img,
        "play": play,
        "ports": ports,
        "inits": inits,
        "records": records,
        "prog": prog,
        "host": h,
    }


def _entries(log):
    return [(e[0], e[1], bool(e[-1])) for e in log or ()]


def diff(s, width=3):
    """The first call of session ``s`` whose lift differs from the machine: None when all
    match, else ``{"call", "mismatch", "at", "machine", "lift", "reference", "reg", "ram"}``
    with the three logs ``width`` entries either side of the first difference ``at``."""
    play, port, prog = s["play"], s["ports"]["call"], s["prog"]
    last = s["inits"][-1]
    mem, reg = bytearray(last["ram"]), list(last["reg"])
    for n, ref in enumerate(tqdm(s["records"], desc="calls", leave=False)):
        mem[1] = port
        pre, feed = bytes(mem), c64.reads(ref)
        o = prog.call(mem, 0, reg=reg, log=True, max_cycles=MC, reads=feed)
        bad = c64.mismatch(ref, {**o, "ram": mem})
        if bad is None:
            reg = list(o["reg"])
            continue
        back = bytearray(pre)
        r = wholeprog.reference_call(
            back, play, reg=reg, volatile=[c64.DEVICES], log=True, max_cycles=MC,
            bank=wpcorpus._bank(), clocked=False, reads=feed,  # pylint: disable=W0212
        )  # fmt: skip
        logs = (_entries(ref["log"]), _entries(o["log"]), _entries(r["log"]))
        at = bad[1] if bad[0] == "log" else 0
        lo = max(0, at - width)
        a, b = np.frombuffer(bytes(back), np.uint8), np.frombuffer(bytes(mem), np.uint8)
        idx = np.flatnonzero(a != b)
        names = ("machine", "lift", "reference")
        return {
            "call": n,
            "mismatch": bad,
            "at": at,
            **{k: lg[lo : at + width + 1] for k, lg in zip(names, logs)},
            "reg": dict(zip(names, (list(x["reg"][:4]) for x in (ref, o, r)))),
            "ram": [(int(i), int(a[i]), int(b[i])) for i in idx[:16]],
        }
    return None


def _fmt(e):
    return "%04x=%02x%s" % (e[0], e[1], "w" if e[2] else "r")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="tune path relative to --hvsc")
    ap.add_argument("song", type=int)
    ap.add_argument("--calls", type=int, default=100)
    ap.add_argument("--hvsc", default=HVSC)
    args = ap.parse_args(argv)
    s = session((Path(args.hvsc) / args.path).read_bytes(), args.song, args.calls)
    d = diff(s)
    if d is None:
        print(f"exact over {len(s['records'])} calls")
        return 0
    print("call", d["call"], "mismatch", d["mismatch"], "first difference at", d["at"])
    for k in ("machine", "lift", "reference"):
        print(f"{k:>9}", " ".join(map(_fmt, d[k])), "reg", d["reg"][k])
    print("reference/lift RAM", " ".join("%04x:%02x/%02x" % x for x in d["ram"]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
