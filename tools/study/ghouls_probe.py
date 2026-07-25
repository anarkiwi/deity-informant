"""EXPERIMENTAL (musical-structure study): Ghouls_n_Ghosts characterization.

Full-length loss stats, within-frame double edges, per-voice event density,
onset-delta histogram and staircase scan for the corpus's densest player.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from framelog import HVSC, canonical_log, canonicalize, frame_trace, state_grid
from recover import voice_events
from run_study import staircase_cells

SID = HVSC / "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid"


def main():
    slices, _p, _m, meta = frame_trace(SID)
    frames = meta["frames"]
    log, loss = canonical_log(slices)
    print("frames", frames, "loss", loss)
    state = [0] * 25
    dbl = Counter()
    seqs = Counter()
    for raw in slices:
        canon, _ = canonicalize(raw)
        per = {}
        for r, v in canon:
            if r < 21 and (r % 7) >= 4:
                if state[r] != v:
                    per.setdefault(r, []).append(v)
            state[r] = v
        for r, vs in per.items():
            if len(vs) >= 2:
                dbl[r % 7] += 1
                if r % 7 == 4:
                    seqs[tuple("%02X" % x for x in vs)] += 1
    print("same-reg double edges", dict(dbl), "top ctrl seqs", seqs.most_common(5))
    grid = state_grid(log, frames)
    canon = [canonicalize(s)[0] for s in slices]
    onsets = []
    for v in range(3):
        evs = voice_events(grid, canon, v)
        print("v%d" % (v + 1), dict(Counter(k for _f, k, _d in evs)))
        onsets += [f for f, k, _ in evs if k == "on"]
    deltas = [b - a for a, b in zip(sorted(onsets), sorted(onsets)[1:]) if b > a]
    print("onset deltas", Counter(deltas).most_common(10))
    stair = staircase_cells(SID, frames, 1)
    print("staircase cells", [("$%04X" % a, n) for a, n, _f in stair])


if __name__ == "__main__":
    main()
