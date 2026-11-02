"""Where a lifted PSID play call differs from the machine's.
``python tools/calldiff.py ROOT PATH [--song N] [--calls N]``: records the first ``--calls`` calls
and prints, for the first the lift differs in, where the lift's, the reference interpreter's and
the machine's device logs differ and the addresses whose bytes differ."""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-position
from framediff import first_difference, show  # noqa: E402 pylint: disable=C0413
from deity_informant import (
    c64,
    frames,
    wholeprog,
)  # noqa: E402 pylint: disable=wrong-import-position
from deity_informant.smc.machine import Queue  # noqa: E402 pylint: disable=C0413


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("path")
    ap.add_argument("--song", type=int, default=1)
    ap.add_argument("--calls", type=int, default=100)
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    args = ap.parse_args(argv)
    root, mc = Path(args.root), args.max_cycles
    wpcorpus._worker(str(root / "DOCUMENTS" / "Songlengths.md5"))  # pylint: disable=W0212
    row = wpcorpus.plan(str(root / args.path), args.path, 180.0, mc)[args.song - 1]
    img = wpcorpus.sidfile((root / args.path).read_bytes())[1]
    init, play, a = row["init"], row["play"], row["song"] - 1
    ports = {"init": wpcorpus.iomap(init), "call": wpcorpus.iomap(play)}
    host = c64._Host(img, ntsc=row["clock"] == "NTSC", model=wpcorpus.sid_model(row))
    host.drive(Queue(runs=[((play, 0), args.calls)]), [(init, a)], mc, ports)
    inits, records, n = host.split()
    last, records = inits[-1], list(records)
    bank, ret = c64.bank_map(), host.sentinel - 1
    prog = wholeprog.lift(
        last["ram"], c64.entries_of(play, host.facts()), [c64.DEVICES], host.facts(), bank=bank
    )
    bad = c64.check(prog, last["ram"], last["reg"], host.stream, ports["call"], mc, ret=ret)
    print(f"{n} calls, init {last['halt']}, check: {bad}")
    if bad is None:
        return 0
    parts, base = [], 0
    for chunk in host.stream.chunks():
        e = np.frombuffer(chunk.packed.events, c64.EVENT)
        e = e.astype(
            [
                *c64.EVENT.descr[:0],
                *[(n, "<i8") if n == "record" else (n, e.dtype[n]) for n in e.dtype.names],
            ]
        )
        e["record"] = e["record"] - chunk.first + base
        parts.append(e)
        base += len(chunk)
    ev = np.concatenate(parts)

    def nested(k, entry):
        rows = ev[ev["record"] == k]
        return [(int(e["icount"]), int(e["pos"]), entry(int(e["at"])), int(e["a"]),
                 frames.regs(e), int(e["depth"])) for e in rows]  # fmt: skip

    at = {pc: i for i, pc in enumerate(prog.entries)}
    mem, reg, k = bytearray(last["ram"]), list(last["reg"]), bad[0]
    for j, rec in enumerate(records[:k]):
        mem[1] = ports["call"]
        reg = prog.call(mem, 0, reg=reg, reads=c64.read_entries(rec), ret=ret,
                        nested=nested(j, at.get))["reg"]  # fmt: skip
    rec, before = records[k], bytes(mem)
    mem[1] = ports["call"]
    kw = {"reg": reg, "reads": c64.read_entries(rec), "log": True, "ret": ret}
    print(
        "machine halt",
        rec["halt"],
        "log",
        len(rec["log"]),
        "lift halt reached",
    )
    print("interrupts", ev[ev["record"] == k].tolist())
    lift = prog.call(mem, 0, nested=nested(k, at.get), **kw)
    ref_mem = bytearray(before)
    ref_mem[1] = ports["call"]
    ref = wholeprog.reference_call(ref_mem, play, volatile=[c64.DEVICES], bank=bank,
                                   nested=nested(k, int), **kw)  # fmt: skip
    print("boundary lift", lift["boundary"], "reference", ref["boundary"], "reg", reg)
    for name, x in (("lift", lift), ("reference", ref)):
        i = first_difference(x["log"], rec["log"])
        print(f"{name} vs machine: first log difference {i} of {len(x['log'])}/{len(rec['log'])}")
        show(name, x["log"], i)
        show("machine", rec["log"], i)
    want = np.frombuffer(bytes(rec["ram"]), np.uint8)
    for x in np.flatnonzero(want != np.frombuffer(bytes(mem), np.uint8))[:40]:
        print(f"  {x:04x} machine {want[x]:02x} lift {mem[x]:02x}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
