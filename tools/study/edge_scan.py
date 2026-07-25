"""EXPERIMENTAL (musical-structure study): within-frame same-register edges.

Counts frames where one ctrl/AD/SR register receives two value-changing
writes within a single frame -- the cases the ordered-sequence clause of the
frame semantics exists for.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from framelog import HVSC, canonicalize, frame_trace

TUNES = {
    "Commando": "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "Aces_High": "MUSICIANS/C/Cadaver/Aces_High.sid",
    "Consultant": "MUSICIANS/C/Cadaver/Consultant.sid",
    "Monty_on_the_Run": "MUSICIANS/H/Hubbard_Rob/Monty_on_the_Run.sid",
    "Wizball": "MUSICIANS/G/Galway_Martin/Wizball.sid",
    "Bionic_Commando": "MUSICIANS/F/Follin_Tim/Bionic_Commando.sid",
}


def main():
    for name, rel in TUNES.items():
        try:
            slices, _p, _m, meta = frame_trace(HVSC / rel)
        except FileNotFoundError:
            print(name, "not cached")
            continue
        state = [0] * 25
        multi = Counter()
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
                    multi[r % 7] += 1
                    if r % 7 == 4:
                        seqs[tuple("%02X" % x for x in vs)] += 1
        print(
            name,
            meta["frames"],
            "frames; same-reg double edges",
            dict(multi),
            "top ctrl seqs",
            seqs.most_common(3),
        )


if __name__ == "__main__":
    main()
