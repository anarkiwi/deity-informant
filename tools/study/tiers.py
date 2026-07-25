"""EXPERIMENTAL (musical-structure study): emit + verify tiered texts.

Full-Songlengths runs for Commando and Ghouls_n_Ghosts: L0/L1(/L2) texts to
out/study/, Gate F verified per tier from the PARSED text, and a
simplification table per tune (study answers "does it simplify").
"""

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sidsong_proto as sp
from framelog import HVSC, ROOT, canonical_log, frame_trace
from run_study import staircase_cells
from verify import event_list, frame_reference, gate_f, render_events

TUNES = {
    "Commando": ("MUSICIANS/H/Hubbard_Rob/Commando.sid", True),
    "Ghouls_n_Ghosts": ("MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid", False),
}
SIDPROG_BYTES = {"Commando": 26168, "Ghouls_n_Ghosts": 452000}
OUT = ROOT / "out" / "study"


def header(name, tier, frames, ist):
    init = " ".join("%02X=%02X" % (r, v) for r, v in enumerate(ist) if v)
    return (
        "sidsong-proto 0 tier %s" % tier,
        'tune "%s" frames %d' % (name, frames),
        "init %s" % init,
    )


def one(item):
    name, (rel, try_l2) = item
    t0 = time.monotonic()
    slices, prologue, _mem0, meta = frame_trace(HVSC / rel)
    frames = meta["frames"]
    ist = sp.collapse_state(prologue)
    ref = frame_reference(slices, ist)
    events = event_list(ref, ist)
    log, loss = canonical_log(slices)
    raw_dump = "".join("%d %d %d\n" % e for e in log)
    (OUT / ("%s.framelog.txt" % name)).write_text(raw_dump)
    rep = {
        "tune": name,
        "frames": frames,
        "raw_play_writes": loss["writes_raw"],
        "canonical_writes": loss["writes_canon"],
        "framelog_dump_bytes": len(raw_dump),
        "sidprog_bytes": SIDPROG_BYTES[name],
        "tiers": {},
    }

    def record(tier, text, got_events):
        (OUT / ("%s.%s.txt" % (name, tier.lower()))).write_text(text)
        ok, at = gate_f(ref, render_events(got_events, ist))
        rep["tiers"][tier] = {"bytes": len(text), "gate_f": ok, "first_bad": at}
        return ok

    hdr0 = header(name, "L0", frames, ist)
    l0_text = "\n".join(hdr0) + "\n" + sp.emit_l0(events)
    record("L0", l0_text, sp.parse_l0(l0_text, frames))
    rep["tiers"]["L0"]["events"] = sum(len(d) + sum(len(e) for e in es) for d, es in events)

    voices, glob = sp.build_l1(events, ist)
    l1_text = sp.emit_l1(voices, glob, header(name, "L1", frames, ist))
    pv, pg = sp.parse_l1(l1_text)
    record("L1", l1_text, sp.l1_to_events(pv, pg, frames))
    rep["tiers"]["L1"]["elements"] = sum(len(sp.rle(v)) for v in voices) + len(sp.rle(glob))

    if try_l2:
        stair = staircase_cells(HVSC / rel, frames, 1)
        stair.sort()
        bounds = [fs for _a, _n, fs in stair[:3]]
        while len(bounds) < 3:
            bounds.append([])
        orders, patterns = sp.build_l2(voices, bounds, frames)
        l2_text = sp.emit_l2(orders, patterns, glob, header(name, "L2", frames, ist))
        po, pp, pg2 = sp.parse_l2(l2_text)
        v2 = sp.l2_to_l1(po, pp)
        record("L2", l2_text, sp.l1_to_events(v2, pg2, frames))
        rep["tiers"]["L2"].update(
            {
                "order_slots": [len(o) for o in orders],
                "patterns": [len(p) for p in patterns],
                "pattern_frames": [sum(l for l, _ in p) for p in patterns],
                "global_overlay_elements": len(sp.rle(glob)),
                "counter_cells": ["$%04X" % a for a, _n, _f in stair[:3]],
            }
        )
    rep["cpu_s"] = round(time.monotonic() - t0, 1)
    return rep


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with mp.Pool(2) as pool:
        reps = pool.map(one, list(TUNES.items()))
    (OUT / "tiers.json").write_text(json.dumps(reps, indent=1))
    for r in reps:
        print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
