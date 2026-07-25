"""EXPERIMENTAL (Follin dispatch study): script-stream decoder + tick sim.

Decodes the per-voice byte streams of the Ghouls_n_Ghosts script VM using the
op grammar read off the interpreter, then validates it by simulating ticks and
comparing per-handler dispatch counts against the dynamic probe's counts.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from framelog import HVSC

from deity_informant.c64 import load_psid
from deity_informant.lifter import lift
from deity_informant.vm import PcodeVM, run_sub

OUT = ROOT / "out" / "study"
SID = "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid"
VOICES = {  # zp ptr pair, handler-table lo/hi base (indexed by op byte X>=0x80)
    1: (0x21, 0x6C37, 0x6C76),
    2: (0x23, 0x6C4C, 0x6C8B),
    3: (0x25, 0x6C61, 0x6CA0),
}
OPS = {
    0x80: ("slide", 3),
    0x81: ("loopend", None),
    0x82: ("loop", None),
    0x83: ("gatelen", 1),
    0x84: ("durmode", 1),
    0x85: ("rawsid", None),
    0x86: ("stop", None),
    0x87: ("jump", None),
    0x88: ("pulsecfg", 8),
    0x89: ("pulsesel", 0),
    0x8A: ("call", None),
    0x8B: ("ret", None),
    0x8C: ("transpose", 1),
    0x8D: ("wave", 1),
    0x8E: ("vibrato", 4),
    0x8F: ("noise", 4),
    0x90: ("gateoff", 1),
    0x91: ("detune", 3),
    0x92: ("porta", 1),
    0x93: ("tie", 0),
    0x94: ("slidedef", 2),
}


def post_init_mem(subtune=0):
    data = (HVSC / SID).read_bytes()
    mem, _l, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    vm = PcodeVM(mem)
    vm.reg[0] = subtune
    run_sub(vm, init, {}, lift)
    return vm.mem, play


class Stream:
    """Tick-level re-implementation of one voice's script interpreter."""

    def __init__(self, mem, voice):
        zp, lo, hi = VOICES[voice]
        self.m = mem
        self.tables = (lo, hi)
        self.ptr = mem[zp] | (mem[zp + 1] << 8)
        self.loop_start = 0
        self.loop_count = 0
        self.stack = []
        self.sticky = 0
        self.counter = 1
        self.stopped = False
        self.dispatches = Counter()
        self.trace = []

    def _handler(self, op):
        lo, hi = self.tables
        return self.m[lo + op] | (self.m[hi + op] << 8)

    def _log(self, at, nbytes, text):
        raw = " ".join("%02X" % self.m[at + i] for i in range(nbytes))
        self.trace.append("%04X: %-14s %s" % (at, raw, text))

    def tick(self):
        if self.stopped:
            return
        self.counter = (self.counter - 1) & 0xFF
        if self.counter:
            return
        while True:
            at = self.ptr
            b = self.m[at]
            if b < 0x80:
                n = 1 + (0 if self.sticky else 1)
                dur = self.sticky if self.sticky else self.m[at + 1]
                kind = "rest" if b == 0 else "note %02X" % b
                self._log(at, n, "%s dur=%d" % (kind, dur))
                self.counter = dur
                self.ptr = at + n
                return
            self.dispatches[self._handler(b)] += 1
            name, cnt = OPS[b]
            if cnt is not None:
                args = " ".join("%02X" % self.m[at + 1 + i] for i in range(cnt))
                self._log(at, 1 + cnt, "%s %s" % (name, args))
                if name == "durmode":
                    self.sticky = self.m[at + 1]
                self.ptr = at + 1 + cnt
                continue
            if name == "loop":
                self._log(at, 2, "loop count=%d" % self.m[at + 1])
                self.loop_count = self.m[at + 1]
                self.loop_start = at + 2
                self.ptr = at + 2
            elif name == "loopend":
                self.loop_count = (self.loop_count - 1) & 0xFF
                self._log(at, 1, "loopend (left=%d)" % self.loop_count)
                self.ptr = at + 1 if self.loop_count == 0 else self.loop_start
            elif name == "rawsid":
                i = at + 1
                pairs = []
                while self.m[i] < 0x80:
                    pairs.append("$D4%02X=%02X" % (self.m[i], self.m[i + 1]))
                    i += 2
                i += 1
                self._log(at, i - at, "rawsid " + " ".join(pairs))
                self.ptr = i
            elif name == "jump":
                tgt = self.m[at + 1] | (self.m[at + 2] << 8)
                self._log(at, 3, "jump %04X" % tgt)
                self.ptr = tgt
            elif name == "call":
                tgt = self.m[at + 1] | (self.m[at + 2] << 8)
                self._log(at, 3, "call %04X" % tgt)
                self.stack.append(at + 3)
                self.ptr = tgt
            elif name == "ret":
                self._log(at, 1, "ret")
                self.ptr = self.stack.pop()
            elif name == "stop":
                self._log(at, 1, "stop")
                self.stopped = True
                return


def main():
    mem, _play = post_init_mem(0)
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 12950
    probe = json.loads((OUT / "Ghouls_n_Ghosts.dispatch.json").read_text())
    streams = {v: Stream(mem, v) for v in VOICES}
    bases = {v: s.ptr for v, s in streams.items()}
    for _ in range(frames):
        for s in streams.values():
            s.tick()
    for v, s in streams.items():
        site = {1: "6374", 2: "6561", 3: "6750"}[v]
        obs = {int(k, 16): n for k, n in probe["jmp_multi"][site].items()}
        sim = dict(s.dispatches)
        print(
            "v%d base=%04X events=%d dispatches=%d match=%s"
            % (v, bases[v], len(s.trace), sum(sim.values()), "EXACT" if sim == obs else "DIFF")
        )
        if sim != obs:
            for h in sorted(set(sim) | set(obs)):
                if sim.get(h) != obs.get(h):
                    print("   %04X sim=%s obs=%s" % (h, sim.get(h), obs.get(h)))
    (OUT / "Ghouls_n_Ghosts.v1script.txt").write_text("\n".join(streams[1].trace) + "\n")
    print("v1 decoded stream ->", OUT / "Ghouls_n_Ghosts.v1script.txt")
    print("\n".join(streams[1].trace[:60]))


if __name__ == "__main__":
    main()
