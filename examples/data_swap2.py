"""Same lifted P-Code (GoatTracker MW1 player) vs a DIFFERENT tune's data (drum)."""
import sys; sys.path.insert(0, "/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad")
import deity_informant as P; P.load_cycle_tables()
from deity_informant import PcodeVM, lift, run_sub
from pysidtracker import registers as reg
from pysidtracker.image import SidImage
from pysidtracker.oracle import read_sidtrace, sidtrace_grid
SCR = "/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad"
ORACLE = "/scratch/anarkiwi/cbm/pysidtracker/.sidtrace_oracle"
PW = set(reg.PW_HI_REGS)
def mask(g): return [[(v & 0xF) if i in PW else v for i, v in enumerate(r)] for r in g]
def align(orc, my, rng=8):
    best = (0, -1, 0)
    for off in range(rng + 1):
        n = min(len(orc) - off, len(my))
        c = sum(1 for i in range(n) if orc[off + i] == my[i])
        if c > best[1]: best = (off, c, n)
    return best
def grid(img, cache, nframes):
    h = img.header
    vm = PcodeVM(bytes(img.mem)); vm.mem[0xD418] = 0x0F
    run_sub(vm, h.init_address, cache, lift)
    rows = []
    for _ in range(nframes):
        run_sub(vm, h.play_address, cache, lift)
        rows.append([vm.mem[0xD400 + i] for i in range(25)])
    return rows

# 1) lift the MW1 player P-Code (shared cache)
mw1 = SidImage.from_bytes(open(f"{SCR}/tunes/GoatTracker_MW1.sid", "rb").read())
cache = {}
mw1_grid = mask(grid(mw1, cache, 400))
n_lifted_after_mw1 = len(cache)

# 2) run the SAME cache against the drum tune's image (different song data)
drum = SidImage.from_bytes(open(f"{SCR}/tunes/GoatTracker_drum.sid", "rb").read())
orc = mask(sidtrace_grid(read_sidtrace(f"{ORACLE}/GoatTracker_drum.csv.zst")))
before = len(cache)
drum_grid = mask(grid(drum, cache, len(orc)))
relifts = len(cache) - before

off, c, n = align(orc, drum_grid[:len(orc)])
differs = drum_grid[:400] != mw1_grid[:400]
print("player-code identical MW1<->drum: 273/273 (verified earlier)")
print(f"MW1 player instructions lifted: {n_lifted_after_mw1}")
print(f"running drum on the SAME P-Code cache: re-lifts (new code paths) = {relifts}")
print(f"drum P-Code(MW1 cache) == drum sidplayfp oracle: lead={off} {c}/{n} "
      f"({'EXACT' if c == n else 'DIFF'})")
print(f"drum output differs from MW1 output: {differs}")
