"""Translate a program with ``deity_informant.cgen`` and play the C against the reference.

``python tools/cgenplay.py FILE (--psid | --org ORG --entry PC:NAME ...) --volatile LO-HI
[--calls NAME:A*N,...] [-o DIR]`` compiles the ``.c`` with ``tools/cgenplay.c`` and checks
each call's volatile writes (and reads of ``--sensitive`` addresses) against the reference.
"""

import argparse
import ctypes
import subprocess
import sys
import time
from pathlib import Path

from tqdm import tqdm

import cmetrics
from deity_informant import cgen, wholeprog

HARNESS = Path(__file__).with_name("cgenplay.c")
CFLAGS = ("-std=c99", "-DCGEN_OOB_EXACT", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC")


def build(src, out, flags=CFLAGS):
    """Compile the generated ``src`` with the harness to the shared library ``out``."""
    subprocess.run(["gcc", *flags, "-o", str(out), str(src), str(HARNESS)], check=True)
    return ctypes.CDLL(str(out))


def entry_regs(a):
    """The register file an entry starts from: ``A``, ``SP`` $FF before the return address."""
    reg = [0] * 16
    reg[0], reg[3] = a, 0xFF
    return reg


def replay(lib, image, pcs, calls, volatile, sensitive=(), progress=False):
    """Play ``calls`` ``[(name, a)]`` of the library on its own memory and the reference at
    ``pcs[name]`` from ``image``; AssertionError at the first call whose log differs."""
    ctypes.memmove(ctypes.addressof(ctypes.c_uint8.in_dll(lib, "IO")), bytes(image), 0x10000)
    logn = ctypes.c_uint32.in_dll(lib, "logn")
    loga = (ctypes.c_uint16 * (1 << 20)).in_dll(lib, "loga")
    logv = (ctypes.c_uint8 * (1 << 20)).in_dll(lib, "logv")
    logw = (ctypes.c_uint8 * (1 << 20)).in_dll(lib, "logw")
    sens = (ctypes.c_uint16 * 64).in_dll(lib, "sensitive")
    for i, a in enumerate(sensitive):
        sens[i] = a
    ctypes.c_int.in_dll(lib, "nsensitive").value = len(sensitive)
    lib.call.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
    fns = {name: ctypes.cast(getattr(lib, name), ctypes.c_void_p) for name in pcs}
    ref = bytearray(image)
    keep = set(sensitive)
    for n, (name, a) in enumerate(tqdm(calls, disable=not progress, desc="calls", unit="call")):
        logn.value = 0
        k = lib.call(fns[name], a)
        got = [(loga[i], logv[i], logw[i]) for i in range(logn.value)]
        r = wholeprog.reference_call(
            ref, pcs[name], a=a, reg=entry_regs(a), volatile=volatile, log=True
        )
        want = [(addr, v, int(w)) for addr, v, _c, w in r["log"] if w or addr in keep]
        assert k < 0, (n, name, "trap", k)
        i = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w), None)
        i = min(len(got), len(want)) if i is None and got != want else i
        assert i is None, (n, name, i, got[i : i + 4], want[i : i + 4])
    return len(calls)


def psid(data):
    """``(load, init, play, start song, body)`` of a PSID/RSID file."""

    def word(i):
        return int.from_bytes(data[i : i + 2], "big")

    body = data[word(6) :]
    load = word(8)
    if not load:
        load, body = body[0] | body[1] << 8, body[2:]
    return load, word(10), word(12), word(16), body


def parse_calls(text, entries):
    """``NAME:A*N,...`` as ``[(name, a)]``; an entry name alone is called with ``A`` = 0."""
    out = []
    for item in filter(None, text.split(",")):
        name, _, rest = item.partition(":")
        a, _, n = (rest or "0").partition("*")
        assert name in entries, name
        out += [(name, int(a, 0))] * int(n or 1)
    return out


def main(argv=None):  # pylint: disable=too-many-locals
    """Command line: translate, compile, replay and report."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--psid", action="store_true", help="FILE is a PSID: init and play")
    ap.add_argument("--org", type=lambda s: int(s, 0), help="load address of a raw FILE")
    ap.add_argument("--loaded", help="LO-HI (HI exclusive): the program's bytes in a raw FILE")
    ap.add_argument("--entry", action="append", default=[], help="PC:NAME")
    ap.add_argument("--volatile", action="append", default=[], help="LO-HI (HI exclusive)")
    ap.add_argument("--sensitive", action="append", default=[], type=lambda s: int(s, 0))
    ap.add_argument("--calls", default="", help="NAME:A*N,... (default init then 3000 plays)")
    ap.add_argument("--init", help="NAME of the entry called once, first (--psid: init)")
    ap.add_argument("-n", type=int, default=3000, help="play calls after init with --psid")
    ap.add_argument("-o", "--out", default=".", help="directory for the .c, .h and library")
    args = ap.parse_args(argv)
    data = Path(args.file).read_bytes()
    if args.psid:
        load, init, play, start, body = psid(data)
        entries = [(init, "init"), (play, "play")]
        calls = args.calls or f"init:{max(start - 1, 0)},play:0*{args.n}"
        args.init = args.init or "init"
    else:
        load, body = args.org, data
        entries = [(int(pc, 0), name) for pc, name in (e.split(":") for e in args.entry)]
        calls = args.calls
    image = bytearray(0x10000)
    image[load : load + len(body)] = body
    vol = [tuple(int(x, 0) for x in v.split("-")) for v in args.volatile]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / Path(args.file).stem
    loaded = (load, load + len(body))
    if args.loaded:
        loaded = tuple(int(x, 0) for x in args.loaded.split("-"))
    t = time.perf_counter()
    c, h, info = cgen.translate(
        image,
        entries,
        vol,
        args.sensitive,
        loaded=loaded,
        header=stem.with_suffix(".h").name,
        init=[n for _, n in entries].index(args.init) if args.init else None,
    )
    wall = time.perf_counter() - t
    stem.with_suffix(".c").write_text(c, encoding="utf-8")
    stem.with_suffix(".h").write_text(h, encoding="utf-8")
    print(cmetrics.table([str(stem.with_suffix(".c"))]))
    print(f"\n.c bytes {len(c)}, .h bytes {len(h)}, regions {len(info['regions'])}")
    jumps = {f"0x{pc:04X}": [f"0x{t:04X}" for t in ts] for pc, ts in sorted(info["jumps"].items())}
    traps = {k: (key[0], f"0x{key[1]:04X}") for k, key in sorted(info["traps"].items())}
    print(f"jumps from the code {jumps}; traps {traps}")
    print(f"structure {info['structure']}")
    stages = ", ".join(f"{k} {v:.3f} s" for k, v in info["times"].items())
    print(f"translate {wall:.3f} s ({stages})", flush=True)
    lib = build(stem.with_suffix(".c"), stem.with_suffix(".so"))
    pcs = {name: pc for pc, name in entries}
    n = replay(lib, image, pcs, parse_calls(calls, pcs), vol, args.sensitive, progress=True)
    print(f"{n} calls identical to the reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
