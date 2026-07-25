"""EXPERIMENTAL (musical-structure study): program-side sequencer-state probe.

Watches player state cells across a full run and prints the change
trajectories of the per-voice pattern-pointer pairs and order-index cells
(Commando layout), to correlate with log-driven pattern boundaries.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from deity_informant.c64 import load_psid
from deity_informant.lifter import lift
from deity_informant.vm import PcodeVM

HVSC = Path("/scratch/anarkiwi/re/deity-informant/.oracle-cache/hvsc")


def watch(sid_path, frames, pairs, idx):
    data = Path(sid_path).read_bytes()
    mem, _l, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    vm = PcodeVM(bytearray(mem))
    vm.wlog = []
    cache = {}
    reg = vm.reg

    def run(entry, acc=0):
        start = reg[3]
        reg[0] = acc & 0xFF
        vm._push(0)
        vm._push(1)
        pc = entry
        while reg[3] < start:
            pc = vm.step(pc, cache, lift)

    run(init, 0)
    hist = {k: [] for k in pairs}
    ih = {a: [] for a in idx}
    for f in range(frames):
        run(play)
        for k, (lo, hi) in pairs.items():
            v = vm.mem[lo] | (vm.mem[hi] << 8)
            if not hist[k] or hist[k][-1][1] != v:
                hist[k].append((f, v))
        for a in idx:
            v = vm.mem[a]
            if not ih[a] or ih[a][-1][1] != v:
                ih[a].append((f, v))
    return hist, ih


if __name__ == "__main__":
    pairs = {"pa": (0x54F8, 0x54F9), "pb": (0x54FE, 0x54FF), "pc": (0x5504, 0x5505)}
    idx = (0x54EC, 0x54ED, 0x54EE)
    hist, ih = watch(HVSC / "MUSICIANS/H/Hubbard_Rob/Commando.sid", 11750, pairs, idx)
    for k, h in hist.items():
        print(k, [(f, hex(v)) for f, v in h[:12]], "... n=", len(h))
    for a, h in ih.items():
        print(hex(a), h[:24], "n=", len(h))
