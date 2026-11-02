"""Lift a program image whole and check the lift against the reference interpreter.

``python tools/wplift.py IMAGE --org 0x1000 --entry 0x1003 --calls 0x1003:0*3000
--volatile 0xC000-0xC100 [--init PC[:A]] [--facts F.json] [--save-facts F.json] [--dump OUT.txt]
[--cycles] [--check]``
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deity_informant import trace, wholeprog  # noqa: E402


def num(s):
    return int(s, 0)


def calls_spec(s):
    """``pc:a*n,...`` as ``[(pc, a)]`` in order."""
    out = []
    for part in s.split(","):
        head, _, n = part.partition("*")
        pc, _, a = head.partition(":")
        out += [(num(pc), num(a or "0"))] * num(n or "1")
    return out


def image_of(path, org):
    data = Path(path).read_bytes()
    if len(data) == 0x10000:
        return bytearray(data)
    img = bytearray(0x10000)
    img[org : org + len(data)] = data
    return img


def check(img, prog, calls, volatile, progress=True):
    """Run ``calls`` on the reference and the lift; the first mismatch or None. Without cycle
    counting a call the reference does not return from ends the check: the lift must stop
    with the same halt (``no-return``), at a later instruction boundary."""
    entries = prog.entries
    m1, m2 = bytearray(img), bytearray(img)
    s1 = s2 = {"reg": None, "cycles": 0}
    keys = ("log", "reg", "pc", "halt") + (("cycles",) if prog.cycles else ())
    for n, (pc, a) in enumerate(tqdm(calls, desc="check", unit="call", disable=not progress)):
        kw = {"log": True, "synth": True}
        r = wholeprog.reference_call(
            m1, pc, a=a, volatile=volatile, clocked=prog.cycles, **kw, **s1
        )
        o = prog.call(m2, entries.index(pc), a=a, **kw, **s2)
        if r["halt"] != "return" and not prog.cycles:
            return (n, "no-return") if o["halt"] == r["halt"] else (n, "halt", r["halt"], o["halt"])
        for k in keys:
            if r[k] != o[k]:
                return n, k, r[k] if k == "reg" else None, o[k] if k == "reg" else None
        diff = np.frombuffer(m1, np.uint8) != np.frombuffer(m2, np.uint8)
        diff[0x100 : 0x101 + r["reg"][3]] = False
        if diff.any():
            return n, "memory@%04x" % int(np.argmax(diff))
        s1 = {"reg": list(r["reg"]), "cycles": r["cycles"]}
        s2 = {"reg": list(o["reg"]), "cycles": o["cycles"] if prog.cycles else r["cycles"]}
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image")
    ap.add_argument("--org", type=num, default=0)
    ap.add_argument("--entry", type=num, action="append", required=True)
    ap.add_argument("--calls", type=calls_spec, default=None)
    ap.add_argument("--volatile", action="append", default=[])
    ap.add_argument("--init", type=calls_spec, help="run PC[:A] on the reference first")
    ap.add_argument("--facts")
    ap.add_argument("--save-facts")
    ap.add_argument("--dump")
    ap.add_argument("--raw", action="store_true", help="skip optimization")
    ap.add_argument("--cycles", action="store_true", help="count cycles")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backend", choices=("jit", "interp"), default="jit")
    args = ap.parse_args(argv)
    img = image_of(args.image, args.org)
    vol = [tuple(num(x) for x in v.split("-")) for v in args.volatile]
    for pc, a in args.init or ():
        wholeprog.reference_call(img, pc, a=a, volatile=vol)
    calls = args.calls or [(e, 0) for e in args.entry]
    if args.facts:
        facts = trace.Facts.loads(Path(args.facts).read_text(encoding="utf-8"))
    else:
        facts = trace.collect(
            img, lambda s: [s.call(pc, a) for pc, a in calls], progress=True, total=len(calls)
        )
    if args.save_facts:
        Path(args.save_facts).write_text(facts.dumps(), encoding="utf-8")
    t = time.perf_counter()
    prog = wholeprog.lift(
        img, args.entry, vol, facts, optimize=not args.raw, cycles=args.cycles, backend=args.backend
    )
    lift_ms = round(1e3 * (time.perf_counter() - t), 2)
    compile_s = prog.compile() if args.backend == "jit" else None
    compile_ms = None if compile_s is None else round(1e3 * compile_s, 2)
    print(json.dumps({"lift_ms": lift_ms, "compile_ms": compile_ms, **prog.stats}))
    if args.dump:
        Path(args.dump).write_text(str(prog), encoding="utf-8")
    if args.check:
        bad = check(img, prog, calls, vol)
        print(json.dumps({"exact": bad is None, "mismatch": bad, "deopts": prog.deopts}))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
