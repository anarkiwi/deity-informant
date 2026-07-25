"""EXPERIMENTAL (Follin dispatch study): dynamic RTS-dispatch probe.

Instrumented full-length play run: classifies every rts by popped-byte
provenance (jsr return vs explicit push), records dispatch events with push
sites, source instructions and index registers, and censuses (zp),y fetches.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from framelog import HVSC

from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds
from deity_informant.lifter import lift
from deity_informant.vm import PcodeVM

OUT = ROOT / "out" / "study"
GUARD = 8_000_000
EVENT_CAP = 400_000


class ProbeVM(PcodeVM):
    """Tracks stack-byte provenance, rts classification and (zp),y fetches."""

    def __init__(self, mem):
        super().__init__(mem)
        self.sprov = [None] * 256
        self.cur = 0
        self.prevpc = 0
        self.frame = -1
        self.rts_kinds = Counter()
        self.events = []
        self.b1_count = Counter()
        self.zp_writers = {}
        self.jmp_targets = {}
        self.jmpind_events = Counter()

    def _wr(self, addr, val, sz):
        if 0x0100 <= addr <= 0x01FF and sz == 1:
            r = self.reg
            self.sprov[addr & 0xFF] = (self.cur, self.prevpc, r[1], r[2], val & 0xFF)
        elif addr < 0x100:
            self.zp_writers.setdefault(addr, Counter())[self.cur] += 1
        super()._wr(addr, val, sz)

    def step(self, pc, cache, lifter):
        self.cur = pc
        op = self.mem[pc]
        sp0 = self.reg[3]
        if op == 0xB1:
            self.b1_count[(pc, self.mem[(pc + 1) & 0xFFFF])] += 1
        elif op == 0x4C:
            tgt = self.mem[(pc + 1) & 0xFFFF] | (self.mem[(pc + 2) & 0xFFFF] << 8)
            self.jmp_targets.setdefault(pc, Counter())[tgt] += 1
        elif op == 0x6C:
            ptr = self.mem[(pc + 1) & 0xFFFF] | (self.mem[(pc + 2) & 0xFFFF] << 8)
            tgt = self.mem[ptr] | (self.mem[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)
            self.jmpind_events[(pc, ptr, tgt)] += 1
        nxt = super().step(pc, cache, lifter)
        if op == 0x20:
            self.sprov[sp0] = "J"
            self.sprov[(sp0 - 1) & 0xFF] = "J"
        elif op == 0x60:
            lo = self.sprov[(sp0 + 1) & 0xFF]
            hi = self.sprov[(sp0 + 2) & 0xFF]
            if lo == "J" or hi == "J":
                self.rts_kinds["jsr_return"] += 1
            elif lo is None or hi is None:
                self.rts_kinds["sentinel"] += 1
            else:
                self.rts_kinds["explicit_push"] += 1
                if len(self.events) < EVENT_CAP:
                    self.events.append((self.frame, pc, nxt, lo, hi))
        self.prevpc = pc
        return nxt


def run(sid_path, frames=None, subtune=None):
    data = Path(sid_path).read_bytes()
    mem, _load, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    _songs, start = psid_songs(data)
    if subtune is None:
        subtune = start - 1
    if frames is None:
        sl = song_lengths((HVSC / "Songlengths.md5").read_text("latin-1"))
        frames = song_seconds(data, sl, subtune) * 50
    vm = ProbeVM(bytearray(mem))
    cache = {}
    reg = vm.reg

    def run_entry(entry, acc=0):
        startsp = reg[3]
        reg[0] = acc & 0xFF
        vm._push(0x00)
        vm._push(0x01)
        vm.sprov[(startsp - 1) & 0xFF] = None
        vm.sprov[startsp & 0xFF] = None
        pc = entry
        n = 0
        while reg[3] < startsp:
            pc = vm.step(pc, cache, lift)
            n += 1
            if n > GUARD:
                raise RuntimeError("runaway at %04X" % pc)

    run_entry(init, subtune)
    vm.rts_kinds.clear()
    vm.events.clear()
    vm.b1_count.clear()
    vm.zp_writers.clear()
    zp_frames = []
    for f in range(frames):
        vm.frame = f
        run_entry(play)
        zp_frames.append(bytes(vm.mem[0:256]))
    return vm, zp_frames, frames


def _insn(mem, pc):
    op = mem[pc]
    o1 = mem[(pc + 1) & 0xFFFF]
    o2 = mem[(pc + 2) & 0xFFFF]
    return (op, o1, o2)


def summarize(vm):
    sites = {}
    for _f, pc, tgt, lo, hi in vm.events:
        s = sites.setdefault(
            pc, {"n": 0, "targets": Counter(), "push_pairs": Counter(), "src": Counter()}
        )
        s["n"] += 1
        s["targets"][tgt] += 1
        s["push_pairs"][(hi[0], lo[0])] += 1
        for info in (hi, lo):
            _ppc, prev, _x, _y, _v = info
            s["src"][(_ppc, prev, _insn(vm.mem, prev))] += 1
    return sites


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid"
    vm, zp_frames, frames = run(HVSC / name)
    print("frames", frames, "rts kinds", dict(vm.rts_kinds), "events", len(vm.events))
    sites = summarize(vm)
    for pc in sorted(sites):
        s = sites[pc]
        print(
            "site %04X: n=%d targets=%d push_pairs=%s"
            % (pc, s["n"], len(s["targets"]), [("%04X %04X" % p) for p in s["push_pairs"]][:4])
        )
        for (ppc, prev, ins), n in s["src"].most_common(6):
            print(
                "   push@%04X prev=%04X [%02X %02X %02X] x%d"
                % (ppc, prev, ins[0], ins[1], ins[2], n)
            )
        top = ", ".join("%04X:%d" % (t, n) for t, n in s["targets"].most_common(40))
        print("   targets:", top)
    print("top (zp),y fetch sites:")
    for (pc, zp), n in vm.b1_count.most_common(20):
        print("   %04X (zp=%02X) x%d" % (pc, zp, n))
    print("multi-target jmp sites:")
    for pc in sorted(vm.jmp_targets):
        tc = vm.jmp_targets[pc]
        if len(tc) > 1:
            top = ", ".join("%04X:%d" % (t, n) for t, n in tc.most_common(24))
            print("   jmp@%04X n=%d targets=%d: %s" % (pc, sum(tc.values()), len(tc), top))
    for (pc, ptr, tgt), n in vm.jmpind_events.most_common(12):
        print("   jmpind@%04X ptr=%04X -> %04X x%d" % (pc, ptr, tgt, n))
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / (Path(name).stem + ".dispatch.json")
    rep = {
        "frames": frames,
        "rts_kinds": dict(vm.rts_kinds),
        "sites": {
            "%04X"
            % pc: {
                "n": s["n"],
                "targets": {"%04X" % t: n for t, n in s["targets"].items()},
                "push_pairs": {"%04X %04X" % p: n for p, n in s["push_pairs"].items()},
                "src": {
                    "%04X<-%04X %02X%02X%02X" % (a, b, *i): n for (a, b, i), n in s["src"].items()
                },
            }
            for pc, s in sites.items()
        },
        "b1_fetch": {"%04X zp%02X" % k: n for k, n in vm.b1_count.most_common(64)},
        "jmp_multi": {
            "%04X" % pc: {"%04X" % t: n for t, n in tc.items()}
            for pc, tc in vm.jmp_targets.items()
            if len(tc) > 1
        },
        "jmpind": {"%04X ptr%04X -> %04X" % k: n for k, n in vm.jmpind_events.items()},
        "zp_writers": {
            "%02X" % a: {"%04X" % w: n for w, n in c.most_common(8)}
            for a, c in sorted(vm.zp_writers.items())
        },
    }
    out.write_text(json.dumps(rep, indent=1))
    events_out = OUT / (Path(name).stem + ".dispatch.events.json")
    events_out.write_text(json.dumps(vm.events[:EVENT_CAP]))
    zp_out = OUT / (Path(name).stem + ".zpframes.bin")
    zp_out.write_bytes(b"".join(zp_frames))
    print("->", out, events_out, zp_out)


if __name__ == "__main__":
    main()
