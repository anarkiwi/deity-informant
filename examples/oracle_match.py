"""Task 4 -- per-frame SID register grid from PcodeVM vs the sidplayfp oracle.

Runs a tune's real init + play (or IRQ handler) on the pure-Python P-Code VM,
snapshots $D400..$D418 per frame, and compares byte-exactly to the sidplayfp
oracle grid over the full 65s render. Reports the startup lead, matched/total,
and every illegal opcode the lifter actually executed.

Usage: oracle_match.py <tune> [<tune> ...]
"""
import sys

import deity_informant as P
from deity_informant import PcodeVM, run_sub, run_irq, lift
from pysidtracker import registers as reg
from pysidtracker.image import SidImage
from pysidtracker.oracle import read_sidtrace, sidtrace_grid
from pysidtracker.trace import trace_init

SCR = "/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad"
ORACLE = "/scratch/anarkiwi/cbm/pysidtracker/.sidtrace_oracle"
PW = set(reg.PW_HI_REGS)

# Illegal opcode bytes (everything the NMOS 6502 runs that is not a documented
# legal instruction), for instrumenting what each driver executes.
_LEGAL = set()
for _o, (_mn, _md) in P.OPS.items():
    _LEGAL.add(_o)
_ILLEGAL_MN = {"SLO", "RLA", "SRE", "RRA", "DCP", "ISC", "SAX", "LAX", "ANC",
               "ALR", "ARR", "SBX", "ANE", "LAS", "SHA", "SHX", "SHY", "TAS"}
_ILLEGAL_NOP = {0x1A, 0x3A, 0x5A, 0x7A, 0xDA, 0xFA, 0x80, 0x82, 0x89, 0xC2,
                0xE2, 0x04, 0x44, 0x64, 0x14, 0x34, 0x54, 0x74, 0xD4, 0xF4,
                0x0C, 0x1C, 0x3C, 0x5C, 0x7C, 0xDC, 0xFC, 0xEB}


def _mask(grid):
    return [[(v & 0xF) if i in PW else v for i, v in enumerate(r)] for r in grid]


def _snapshot(vm):
    return [vm.mem[0xD400 + i] for i in range(reg.SID_REG_COUNT)]


def _illegals(cache):
    seen = {}
    for k in cache:
        op = k[1]
        mn = P.OPS.get(op, (None,))[0]
        if mn in _ILLEGAL_MN or op in _ILLEGAL_NOP:
            seen.setdefault(mn if mn != "NOP" else "NOP*", set()).add("%02X" % op)
    return {m: sorted(v) for m, v in seen.items()}


def _grid_sub(mem0, init, play, nframes, cache):
    vm = PcodeVM(mem0)
    vm.mem[0xD418] = 0x0F
    run_sub(vm, init, cache, lift)
    rows = []
    for _ in range(nframes):
        run_sub(vm, play, cache, lift)
        rows.append(_snapshot(vm))
    return rows


def _grid_irq(mem0, init, handler, nframes, cache):
    vm = PcodeVM(mem0)
    vm.mem[0xD418] = 0x0F
    run_sub(vm, init, cache, lift)
    rows = []
    for _ in range(nframes):
        run_irq(vm, handler, cache, lift)
        rows.append(_snapshot(vm))
    return rows


def _align(orc, my, rng=8):
    m = len(orc)
    best = (0, -1)
    for off in range(rng + 1):
        n = min(m - off, len(my))
        c = sum(1 for i in range(n) if orc[off + i] == my[i])
        if c > best[1]:
            best = (off, c, n)
    return best


def run_tune(name):
    P.load_cycle_tables()
    data = open("%s/tunes/%s.sid" % (SCR, name), "rb").read()
    img = SidImage.from_bytes(data)
    h = img.header
    mem0 = bytes(img.mem)
    orc = _mask(sidtrace_grid(read_sidtrace("%s/%s.csv.zst" % (ORACLE, name))))
    cache = {}
    if h.play_address:
        init = h.init_address
        play = h.play_address
        my = _mask(_grid_sub(mem0, init, play, len(orc), cache))
        mode = "play $%04X" % play
    else:
        tr = trace_init(img, play_calls=0)
        handler = tr.irq_vector or tr.hw_irq_vector
        my = _mask(_grid_irq(mem0, h.init_address, handler, len(orc), cache))
        mode = "IRQ $%04X (cia1_latch=%s)" % (handler, tr.cia1_timer_latch)
    off, c, n = _align(orc, my)
    verdict = "EXACT" if c == n else "DIFF"
    print("== %s == init $%04X %s | oracle_frames=%d" % (name, h.init_address, mode, len(orc)))
    print("   startup_lead=%d  P-Code_VM==sidplayfp: %d/%d  (%s)" % (off, c, n, verdict))
    print("   illegal opcodes executed: %s" % (_illegals(cache) or "none"))
    if verdict == "DIFF":
        for i in range(n):
            if orc[off + i] != my[i]:
                d = [(hex(0xD400 + j), orc[off + i][j], my[i][j])
                     for j in range(25) if orc[off + i][j] != my[i][j]]
                print("   first diff at frame %d (oracle idx %d): %s" % (i, off + i, d))
                break
    return c == n


def main():
    names = sys.argv[1:] or ["A_Mind_Is_Born", "GoatTracker_MW1"]
    results = [run_tune(n) for n in names]  # run/print every tune (no short-circuit)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
