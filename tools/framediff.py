"""Where a lifted interrupt frame differs from the machine's.
``python tools/framediff.py ROOT PATH [--song N] [--frames N] [--raw]``: records the
first ``--frames`` video frames of an interrupt-driven song, lifts them (``--raw``: unoptimized)
and prints, for the first frame the lift differs in, its entry state, the interrupts taken inside
it, the first difference of the lift's, the reference interpreter's and the machine's device
logs, and the addresses whose bytes differ."""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-position
from deity_informant import c64, frames  # noqa: E402 pylint: disable=wrong-import-position


def first_difference(a, b):
    """The index of the first entry two device logs differ at (a log entry's address, value and
    direction), or the length of the shorter."""
    key = lambda log: [(e[0], e[1], e[-1]) for e in log]  # noqa: E731
    a, b = key(a), key(b)
    return next((i for i, (p, q) in enumerate(zip(a, b)) if p != q), min(len(a), len(b)))


def show(name, log, k):
    print(f"  {name:9}", [(hex(e[0]), e[1], e[-1]) for e in log[max(0, k - 3) : k + 4]])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("path")
    ap.add_argument("--song", type=int, default=1)
    ap.add_argument("--frames", type=int, default=100)
    ap.add_argument("--raw", action="store_true", help="lift without optimizing")
    ap.add_argument("--max-cycles", type=int, default=2_000_000)
    args = ap.parse_args(argv)
    root = Path(args.root)
    wpcorpus._worker(str(root / "DOCUMENTS" / "Songlengths.md5"))  # pylint: disable=W0212
    row = wpcorpus.plan(str(root / args.path), args.path, 180.0, args.max_cycles)[args.song - 1]
    boot = wpcorpus.irq_boot(row, (root / args.path).read_bytes())
    host = frames.FrameHost(
        boot.image, roms=boot.roms, ntsc=row["clock"] == "NTSC", model=wpcorpus.sid_model(row)
    )
    host.drive_frames(boot, args.frames, args.max_cycles)
    prog = frames.lift(host, boot.roms, optimize=not args.raw)
    bank = c64.bank_map(boot.roms)
    bad = frames.check(prog, host, args.max_cycles)
    print(
        f"{len(host.stream)} frames, {len(host.inner)} nested; stopped: {host.stop}; check: {bad}"
    )
    print(f"foreign {sorted(host.foreign)} dirty {host.dirty} stats {prog.stats}")
    if bad is None:
        return 0
    n, mem, recs = bad[0], bytearray(host.image), iter(host.stream)
    for k in range(n):
        frames._call(prog, host, k, next(recs), mem)  # pylint: disable=protected-access
    rec, before = next(recs), bytes(mem)
    lift = frames._call(prog, host, n, rec, mem)  # pylint: disable=protected-access
    ref = frames._call(None, host, n, rec, bytearray(before), bank)  # pylint: disable=W0212
    print("entry", host.frames[n], "halt machine", rec["halt"], "lift", lift["halt"])
    print(
        "interrupts",
        host.inside(n).tolist(),
        "boundary lift",
        lift["boundary"],
        "ref",
        ref["boundary"],
    )
    for name, a, b in (("lift", lift, rec), ("reference", ref, rec), ("reference", ref, lift)):
        k = first_difference(a["log"], b["log"])
        other = "machine" if b is rec else "lift"
        print(f"{name} vs {other}: first log difference {k} of {len(a['log'])}/{len(b['log'])}")
        show(name, a["log"], k)
        show(other, b["log"], k)
    want = np.frombuffer(bytes(rec["ram"]), np.uint8)
    for a in np.flatnonzero(want != np.frombuffer(bytes(mem), np.uint8))[:40]:
        print(f"  {a:04x} machine {want[a]:02x} lift {mem[a]:02x}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
