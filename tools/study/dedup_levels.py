"""EXPERIMENTAL (musical-structure study): pattern dedup vs abstraction level.

Measures distinct Commando patterns (program-layer boundaries) at three
content levels: A full bitwise L1 slices; B rhythm/gating skeleton (freq
values dropped); C rows model (freq kept only at gate-on frames).
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sidsong_proto as sp
from framelog import HVSC, frame_trace
from run_study import staircase_cells
from verify import event_list, frame_reference

SID = HVSC / "MUSICIANS/H/Hubbard_Rob/Commando.sid"
GATE = 0x01


def abstract(seg, level, tf=None):
    out = []
    gated = set()
    for f, k, v in seg:
        if k == "e":
            out.append((f, k, v))
            if any(r % 7 == 4 and val & GATE for r, val in v):
                gated.add(f)
    base = None
    if level == "T":
        for f, k, v in seg:
            if k == "f" and f in gated and v in tf:
                base = tf[v]
                break
    for f, k, v in seg:
        if k == "e":
            continue
        if level == "B":
            out.append((f, k, None))
        elif level == "C":
            out.append((f, k, v if (k == "f" and f in gated) else None))
        elif level == "T":
            val = None
            if k == "f" and f in gated:
                val = tf[v] - base if (v in tf and base is not None) else v
            out.append((f, k, val))
        else:
            out.append((f, k, v))
    return tuple(sorted(out))


def main():
    slices, prologue, mem0, meta = frame_trace(SID)
    frames = meta["frames"]
    ist = sp.collapse_state(prologue)
    events = event_list(frame_reference(slices, ist), ist)
    voices, _glob = sp.build_l1(events, ist)
    mem0 = bytearray(mem0)
    tf = {}
    for i in range(96):
        tf.setdefault((mem0[0x5429 + 2 * i] << 8) | mem0[0x5428 + 2 * i], i)
    stair = staircase_cells(SID, frames, 1)
    stair.sort()
    for vi in range(3):
        bounds = [0] + stair[vi][2] + [frames]
        segs = []
        for a, b in zip(bounds, bounds[1:]):
            body = tuple((f - a, k, v) for f, k, v in voices[vi] if a <= f < b)
            segs.append((b - a, body))
        row = {"slots": len(segs)}
        for level in "ABCT":
            row[level] = len(Counter((n, abstract(s, level, tf)) for n, s in segs))
        print("v%d" % (vi + 1), row)


if __name__ == "__main__":
    main()
