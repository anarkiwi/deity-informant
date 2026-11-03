"""Play a C lift against the reference interpreter and compare what it does.

``python tools/cplay.py SRC.c --state IMAGE.bin --meta META.json [-n CALLS]`` compiles ``SRC.c``
with ``tools/cplay.c`` and plays it from ``A = X = Y = 0``, ``SP = $FD`` per call: the bus writes
(and with ``--ram`` the RAM outside volatile ranges and page 1) must equal the reference's.
"""

import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tqdm import tqdm

from deity_informant import wholeprog

HARNESS = Path(__file__).with_name("cplay.c")


def build(src, out):
    """Compile ``src`` with the harness to the shared library ``out``; the loaded library."""
    cmd = [
        "gcc",
        "-std=c11",
        "-O2",
        "-shared",
        "-fPIC",
        "-Werror=implicit-function-declaration",
        "-o",
        str(out),
        str(src),
    ]
    subprocess.run([*cmd, str(HARNESS)], check=True)
    return ctypes.CDLL(str(out))


def replay(lib, image, pc, inputs, volatile, fn="play", progress=False, ram=True):
    """Play ``fn`` once per ``A`` in ``inputs`` from ``image`` and against the reference at ``pc``.

    AssertionError at the first call whose bus writes, deopts or (with ``ram``) RAM differ."""
    mem = (ctypes.c_uint8 * 65536).in_dll(lib, "M")
    mem[:] = list(image)
    ref = bytearray(image)
    logn = ctypes.c_uint32.in_dll(lib, "logn")
    loga = (ctypes.c_uint16 * (1 << 20)).in_dll(lib, "loga")
    logv = (ctypes.c_uint8 * (1 << 20)).in_dll(lib, "logv")
    deopted = ctypes.c_int.in_dll(lib, "deopted")
    start = {"A": None, "X": 0, "Y": 0, "SP": 0xFD}
    regs = {}
    for r in start:
        try:
            regs[r] = ctypes.c_uint8.in_dll(lib, r)
        except ValueError:
            pass
    keep = bytearray(b"\x01" * 0x10000)
    keep[0x100:0x200] = bytes(0x100)
    for lo, hi in volatile:
        keep[lo:hi] = bytes(hi - lo)
    play = getattr(lib, fn)

    def inside(addr):
        return any(lo <= addr < hi for lo, hi in volatile)

    for n, a in enumerate(tqdm(inputs, disable=not progress, desc="calls", unit="call")):
        logn.value = deopted.value = 0
        for r, reg in regs.items():
            reg.value = a if r == "A" else start[r]
        play()
        got = [(loga[i], logv[i]) for i in range(logn.value) if inside(loga[i])]
        r = wholeprog.reference_call(ref, pc, a=a, volatile=volatile, log=True)
        want = [(addr, v) for addr, v, _cycle, w in r["log"] if w]
        assert not deopted.value, (n, hex(deopted.value))
        assert got == want, (n, got[:8], want[:8])
        if not ram:
            continue
        got_mem = bytes(mem)
        bad = [i for i in range(0x10000) if keep[i] and got_mem[i] != ref[i]]
        assert not bad, (n, [hex(i) for i in bad[:8]])


def main(argv=None):
    """Command line: replay a lift of a tune."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("--state", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("-n", "--calls", type=int, default=3000)
    ap.add_argument("--ram", action="store_true", help="also compare RAM after every call")
    args = ap.parse_args(argv)
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        lib = build(args.src, Path(tmp) / "play.so")
        vol = [tuple(v) for v in meta["vol"]]
        image = Path(args.state).read_bytes()
        replay(lib, image, meta["play"], [0] * args.calls, vol, progress=True, ram=args.ram)
    print(f"{args.src}: {args.calls} calls, bus writes identical to the reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
