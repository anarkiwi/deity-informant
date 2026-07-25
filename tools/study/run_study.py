"""EXPERIMENTAL (musical-structure study): driver producing the doc's numbers.

Per tune: canonical frame log + loss stats, Gate F on the L0 event list,
freq-table location + note inversion rates, speed/row/pattern recovery,
sequencer-state (staircase) cells, and a recovered pattern excerpt.
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from framelog import HVSC, canonical_log, canonicalize, frame_trace, state_grid
from notes import locate_freq_table, note_name
from recover import best_block_len, phrase_cover, row_stream, tokens, voice_events
from verify import event_list, frame_reference, gate_f, render_events

TUNES = {
    "Commando": "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "Aces_High": "MUSICIANS/C/Cadaver/Aces_High.sid",
    "Consultant": "MUSICIANS/C/Cadaver/Consultant.sid",
}


def staircase_cells(sid_path, frames, speed):
    """Player cells whose values step monotonically on row boundaries."""
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid
    from deity_informant.lifter import lift
    from deity_informant.vm import PcodeVM

    data = Path(sid_path).read_bytes()
    mem, _l, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    ev = S.trace(bytearray(mem), init, play, min(frames, 2000), 0)
    cells = sorted(a for a in ev.written if not 0xD400 <= a <= 0xD41C and a < 0xD000)
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
    hist = {a: [(0, vm.mem[a])] for a in cells}
    for f in range(frames):
        run(play)
        for a in cells:
            v = vm.mem[a]
            if hist[a][-1][1] != v:
                hist[a].append((f, v))
    out = []
    for a, h in hist.items():
        ch = h[1:]
        if not 8 <= len(ch) <= 500:
            continue
        vals = [v for _f, v in h]
        resets = sum(1 for x, y in zip(vals, vals[1:]) if y < x)
        onrow = sum(1 for f, _v in ch if f % speed == 0) / len(ch)
        steps = sum(1 for x, y in zip(vals, vals[1:]) if y == x + 1)
        if resets <= 3 and onrow >= 0.95 and steps / len(ch) >= 0.9:
            out.append((a, len(ch), [f for f, _v in ch]))
    return out


def study(name, rel):
    sid = HVSC / rel
    slices, _prologue, mem0, meta = frame_trace(sid)
    frames = meta["frames"]
    log, loss = canonical_log(slices)
    ref = frame_reference(slices)
    ev0 = event_list(ref)
    ok, at = gate_f(ref, render_events(ev0))
    grid = state_grid(log, frames)
    canon = [canonicalize(s)[0] for s in slices]
    table = locate_freq_table(bytearray(mem0))
    tf = {}
    if table:
        for i, v in enumerate(table[2]):
            tf.setdefault(v, i)
    rep = {
        "tune": name,
        "frames": frames,
        "loss": loss,
        "gateF_L0": ok,
        "gateF_first_bad": at,
        "L0_events": sum(len(d) + sum(len(e) for e in es) for d, es in ev0),
        "table": table and {"kind": table[0], "addr": "$%04X" % table[1], "len": len(table[2])},
        "voices": [],
    }
    onsets_all = []
    for v in range(3):
        evs = voice_events(grid, canon, v)
        ons = [f for f, k, _ in evs if k == "on"]
        onsets_all += ons
        onf = [d[0] for f, k, d in evs if k == "on"]
        restarts = sum(1 for _s, es in ref if len(es[v]) >= 2)
        rep["voices"].append(
            {
                "events": dict(Counter(k for _f, k, _d in evs)),
                "table_exact_onsets": sum(1 for x in onf if x in tf),
                "hard_restart_frames": restarts,
            }
        )
    deltas = [b - a for a, b in zip(sorted(onsets_all), sorted(onsets_all)[1:]) if b > a]
    hist = Counter(deltas)
    speed = (
        max(range(2, 17), key=lambda s: (sum(1 for d in deltas if d % s == 0), s)) if deltas else 1
    )
    aligned = sum(1 for d in deltas if d % speed == 0) / len(deltas) if deltas else 0
    rep["speed"] = speed
    rep["delta_aligned"] = round(aligned, 3)
    rep["delta_hist"] = dict(hist.most_common(6))
    nrows = frames // speed
    for v in range(3):
        evs = voice_events(grid, canon, v)
        seq = tokens(row_stream(evs, speed, nrows, tf))
        cov, _uses = phrase_cover(seq, min_len=8)
        L, _ratio, order, _index = best_block_len(seq)
        ons = [f for f, k, _ in evs if k == "on"]
        rep["voices"][v].update(
            {
                "rows": len(seq),
                "distinct_tokens": len(set(seq)),
                "phrase_cover": round(cov, 3),
                "block_len": L,
                "blocks": len(order),
                "distinct_blocks": len(set(order)),
                "offgrid_onsets": sum(1 for f in ons if f % speed),
            }
        )
    stair = staircase_cells(sid, frames, speed)
    rep["staircase_cells"] = [{"cell": "$%04X" % a, "steps": n} for a, n, _f in stair]
    return rep, (grid, canon, tf, speed, stair)


def commando_excerpt(grid, canon, tf, speed, stair, frames):
    """Segment v1 by its order counter; print order list + one pattern."""
    counters = {a: fs for a, _n, fs in stair}
    v1 = counters.get(0x54EC)
    if not v1:
        return "order counter $54EC not found"
    bounds = v1 + [frames]
    evs = voice_events(grid, canon, 0)
    nrows = frames // speed
    seq = tokens(row_stream(evs, speed, nrows, tf))
    segs = []
    for a, b in zip(bounds, bounds[1:]):
        segs.append(seq[a // speed : b // speed])
    index = {}
    order = []
    for s in segs:
        key = tuple(s)
        if key not in index:
            index[key] = len(index)
        order.append(index[key])
    lines = [
        "v1 order list (%d slots, %d distinct patterns, lengths %s...):"
        % (len(order), len(index), [len(s) for s in segs[:8]]),
        "  " + " ".join("%02d" % p for p in order),
        "",
        "pattern 01 (rows, speed %d):" % speed,
    ]
    pat = segs[order.index(1)] if 1 in order else segs[0]
    for i, tok in enumerate(pat):
        if tok == ".":
            txt = "... .. ....."
        elif tok == "off":
            txt = "off"
        else:
            note, (wave, ad, sr) = tok
            nm = note_name(note) if note >= 0 else "?%04X" % -note
            txt = "%s %X %02X%02X" % (nm, wave, ad, sr)
        lines.append("  %02d | %s" % (i, txt))
    return "\n".join(lines)


def main():
    reports = []
    for name, rel in TUNES.items():
        rep, extra = study(name, rel)
        reports.append(rep)
        print(json.dumps(rep, indent=1))
        if name == "Commando":
            grid, canon, tf, speed, stair = extra
            print(commando_excerpt(grid, canon, tf, speed, stair, rep["frames"]))
    out = Path(__file__).with_name("study_report.json")
    out.write_text(json.dumps(reports, indent=1))


if __name__ == "__main__":
    main()
