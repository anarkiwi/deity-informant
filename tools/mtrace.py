"""The first instruction of a banked play call where the reference departs from the machine.

``mtrace.py PATH SONG CALL [--hvsc ROOT]``: the machine's steps of call ``CALL`` (Python
decode hook) against the bank-aware reference interpreter replaying that call's device reads,
bisected on the reference's cycle budget; prints the steps around the departure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from py65.devices.mpu6502 import MPU
from py65.disassembler import Disassembler

sys.path.insert(0, str(Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from deity_informant import c64, wholeprog  # noqa: E402

import bdiff  # noqa: E402 pylint: disable=wrong-import-order
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-order


def stepper(call):
    """A ``c64._Host`` class (Python backend) keeping ``steps``, per instruction of
    play call ``call``: ``(pc, a, x, y, sp, p, cycle, bits)``."""

    class Steps(c64._Host):  # pylint: disable=protected-access
        """The machine run, its steps of one call."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **{**kw, "backend": "python"})
            self.steps, self.ncall = [], -1

        def _trap(self, mc):
            self.ncall += bool(self.queue) and self.queue[0][0] == "call"
            return super()._trap(mc)

        def _decode(self, mc):
            super()._decode(mc)
            r = mc.mpu
            if self.cur == "call" and self.ncall == call and r.pc != self.sentinel:
                self.steps.append((r.pc, r.a, r.x, r.y, r.sp, r.p, mc.cycle, mc.bits))

    return Steps


def departure(s, call):
    """``(step, steps, ref, mem)`` for session ``s`` (:func:`bdiff.session` over a
    :func:`stepper`): the first machine step of ``call`` the reference does not reach,
    ``ref(i)`` the reference run up to step ``i`` from RAM ``mem``; None when it follows all."""
    steps = s["host"].steps
    pre = s["records"][call - 1] if call else s["inits"][-1]
    mem = bytearray(pre["ram"])
    mem[1] = s["ports"]["call"]
    feed, c0 = c64.reads(s["records"][call]), steps[0][6]

    bank = wpcorpus._bank()  # pylint: disable=protected-access

    def ref(i):
        return wholeprog.reference_call(
            bytearray(mem), s["play"], reg=list(pre["reg"]), volatile=[c64.DEVICES],
            max_cycles=max(0, steps[i][6] - c0 - 1), bank=bank, reads=feed,
        )  # fmt: skip

    def same(i):
        o = ref(i)
        g = list(o["reg"][:4])
        return any(o["pc"] == t[0] and g == list(t[1:5]) for t in steps[max(0, i - 1) : i + 2])

    lo, hi = 0, len(steps) - 1
    if same(hi):
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (mid + 1, hi) if same(mid) else (lo, mid)
    return lo, steps, ref, mem


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="tune path relative to --hvsc")
    ap.add_argument("song", type=int)
    ap.add_argument("call", type=int)
    ap.add_argument("--hvsc", default=bdiff.HVSC)
    args = ap.parse_args(argv)
    data = (Path(args.hvsc) / args.path).read_bytes()
    s = bdiff.session(data, args.song, args.call + 1, stepper(args.call))
    d = departure(s, args.call)
    if d is None:
        print("no departure within the machine's steps")
        return 0
    lo, steps, ref, mem = d
    m = MPU()
    m.memory[:] = list(mem)
    dis = Disassembler(m)
    for i in range(max(0, lo - 4), min(len(steps), lo + 2)):
        st, o = steps[i], ref(i)
        print(
            i, "machine pc %04x a%02x x%02x y%02x s%02x p%02x bits%d" % (*st[:6], st[7]),
            "| reference pc %04x a%02x x%02x y%02x s%02x" % (o["pc"], *o["reg"][:4]),
            dis.instruction_at(st[0])[1],
        )  # fmt: skip
    return 1


if __name__ == "__main__":
    sys.exit(main())
