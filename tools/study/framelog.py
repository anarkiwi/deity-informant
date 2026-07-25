"""EXPERIMENTAL (musical-structure study): canonical frame log.

Derives the per-frame canonical SID write log from a concrete init+play run,
per the normative frame semantics (see docs/musical-structure-study.md):
freq/pw/filter/volume collapse last-write-wins, ctrl/AD/SR order preserved.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HVSC = Path("/scratch/anarkiwi/re/deity-informant/.oracle-cache/hvsc")

from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds
from deity_informant.lifter import lift
from deity_informant.vm import PcodeVM

GUARD = 8_000_000
V_BASE = (0, 7, 14)
FREQ_LO, FREQ_HI, PW_LO, PW_HI, CTRL, AD, SR = range(7)
F_CLO, F_CHI, F_RES, F_VOL = 21, 22, 23, 24


def frame_trace(sid_path, frames=None, subtune=None):
    """Concrete init+play run.

    Returns (frame_slices, prologue, mem0, meta); frame_slices holds one raw
    ordered (reg, val) SID write list per play call.
    """
    data = Path(sid_path).read_bytes()
    mem, _load, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    _songs, startsong = psid_songs(data)
    if subtune is None:
        subtune = startsong - 1
    if frames is None:
        sl = song_lengths((HVSC / "Songlengths.md5").read_text("latin-1"))
        frames = song_seconds(data, sl, subtune) * 50
    vm = PcodeVM(bytearray(mem))
    vm.wlog = []
    cache = {}
    reg = vm.reg

    def run_entry(entry, acc=0):
        start = reg[3]
        reg[0] = acc & 0xFF
        vm._push(0x00)
        vm._push(0x01)
        pc = entry
        n = 0
        while reg[3] < start:
            pc = vm.step(pc, cache, lift)
            n += 1
            if n > GUARD:
                raise RuntimeError("runaway at %04X" % pc)

    run_entry(init, subtune)
    prologue = [(r, v) for _c, r, v in vm.wlog]
    mem0 = bytes(vm.mem)
    vm.wlog = []
    vm.cycles = 0
    slices = []
    for _ in range(frames):
        mark = len(vm.wlog)
        run_entry(play)
        slices.append([(r, v) for _c, r, v in vm.wlog[mark:]])
    return slices, prologue, mem0, {"init": init, "play": play, "frames": frames}


def canonicalize(raw_frame):
    """One frame's raw (reg,val) writes -> (canonical list, collapsed count).

    ctrl/AD/SR keep their per-voice relative order in full; every other
    register collapses last-write-wins (the collapsed count is the loss).
    """
    latch = {}
    seq = {0: [], 1: [], 2: []}
    loss = 0
    for r, v in raw_frame:
        if r < 21 and (r % 7) >= CTRL:
            seq[r // 7].append((r, v))
        else:
            if r in latch:
                loss += 1
            latch[r] = v
    canon = []
    for vi in range(3):
        b = V_BASE[vi]
        for off in (FREQ_LO, FREQ_HI, PW_LO, PW_HI):
            if b + off in latch:
                canon.append((b + off, latch[b + off]))
        canon.extend(seq[vi])
    for r in (F_CLO, F_CHI, F_RES, F_VOL):
        if r in latch:
            canon.append((r, latch[r]))
    return canon, loss


def canonical_log(slices):
    """[(frame, reg, val)] canonical log plus per-tune loss stats."""
    log = []
    total_loss = 0
    lossy_frames = 0
    vol_multi = 0
    for f, raw in enumerate(slices):
        canon, loss = canonicalize(raw)
        total_loss += loss
        if loss:
            lossy_frames += 1
        if sum(1 for r, _ in raw if r == F_VOL) > 1:
            vol_multi += 1
        log.extend((f, r, v) for r, v in canon)
    return log, {
        "writes_raw": sum(len(s) for s in slices),
        "writes_canon": len(log),
        "collapsed_writes": total_loss,
        "lossy_frames": lossy_frames,
        "d418_multiwrite_frames": vol_multi,
    }


def state_grid(log, frames):
    """Per-frame 25-register carried state (last-write-wins across frames)."""
    state = [0] * 25
    grid = []
    it = iter(log)
    cur = next(it, None)
    for f in range(frames):
        while cur is not None and cur[0] == f:
            state[cur[1]] = cur[2]
            cur = next(it, None)
        grid.append(tuple(state))
    return grid
