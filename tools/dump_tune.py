"""Dump one cached tune's emitted frame program text (development probe)."""

import argparse
import sys
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tune")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    from deity_informant import frameprog
    from deity_informant.c64 import load_psid

    sid, sub, secs = _sweep.entries([args.tune])[0]
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    _model, prog, _ev = _sweep.build(mem, init, play, int(secs * 50), sub)
    Path(args.out).write_text(frameprog.dumps(prog), encoding="utf-8")


if __name__ == "__main__":
    main()
