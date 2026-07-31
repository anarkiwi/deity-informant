"""Compare the native-editor oracle with the tracker's own recovery.

GoatTracker tunes are decompiled out of the cached .sids, mapped, checked against
the tracker's law and compared on three axes; SID-Wizard modules are mapped and
checked against that editor's own player. Writes out/gt_compare.json.
"""

import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HVSC = ROOT / ".oracle-cache" / "hvsc"
SWM = ROOT / ".oracle-cache" / "swm"
OUT = ROOT / "out" / "gt_compare.json"
FRAMES = 200
WORKERS = int(os.environ.get("WORKERS", "5"))


def _lifted(path, subtune, nframes):
    """``(prog, trace)``: the tracker's whole input for one cached tune."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    mem, _load, init, play = load_psid(path.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, nframes, subtune)
    trace, _walker = frameprog.iota(model, nframes)
    prog = frameprog.program(model)
    if os.environ.get("DI_DEREF_SRC") == "off":
        prog.pinned = {}
    elif os.environ.get("DI_DEREF_SRC") == "observed":
        prog.pinned = {a: a for a in prog.resolved}
    return prog, trace


def _fires(graph, node, nframes):
    """Per-frame fire counts of the node a plane-routed generator triggers on."""
    trig = node.trigger
    if trig[0] != "event":
        return [1] * nframes
    src = graph.nodes[trig[1]].transfer
    if src[0] == "EDGE":
        return list(src[1]) + [0] * (nframes - len(src[1]))
    return [1 if (f + 1) % max(1, src[1]) == 0 else 0 for f in range(nframes)]


def _rows(graph, nframes):
    """``{(frame, reg): row}`` for every SELECT emit of a graph."""
    out = {}
    for node in graph.nodes:
        if node.route[0] != "plane" or node.transfer[0] != "SELECT":
            continue
        rows, i = node.transfer[2], 0
        if len(rows) == 2 and rows[0] == "node":  # a generated row is not a recovered one
            continue
        for f, n in enumerate(_fires(graph, node, nframes)):
            for _k in range(n):
                if i < len(rows):
                    out[(f, node.route[1])] = rows[i]
                i += 1
    return out


def _bijection(pairs):
    """``(share consistent with the modal map, map size, is it onto)``."""
    per = {}
    for a, b in pairs:
        per.setdefault(a, {})[b] = per.setdefault(a, {}).get(b, 0) + 1
    votes = {a: max(hist, key=hist.get) for a, hist in per.items()}
    hit = sum(1 for a, b in pairs if votes.get(a) == b)
    return (hit / len(pairs) if pairs else 0.0), len(votes), len(set(votes.values())) == len(votes)


def _pitch_axis(ours, native, nframes):
    """``(share agreeing at the modal offset, that offset, emits)`` for the note lane."""
    diffs = [
        ours[(f, 7 * v)] - native.notes[f][v]
        for f in range(nframes)
        for v in range(3)
        if (f, 7 * v) in ours
    ]
    if not diffs:
        return 0.0, None, 0
    hist = {}
    for d in diffs:
        hist[d] = hist.get(d, 0) + 1
    best = max(hist, key=hist.get)
    return hist[best] / len(diffs), best, len(diffs)


def _instr_axis(ours, native, nframes):
    """``(consistency, distinct rows, is a bijection, emits)`` for the ad plane."""
    pairs = [
        (ours[(f, 7 * v + 5)], native.instrs[f][v])
        for f in range(nframes)
        for v in range(3)
        if (f, 7 * v + 5) in ours
    ]
    share, size, onto = _bijection(pairs)
    return share, size, onto, len(pairs)


def _index_census(graph, nframes, tag):
    """``index`` nodes, generated-row `SELECT`s and their emits, for either side's graph.

    The same predicate is applied to the oracle's graph and to the recovery's: a row a
    generator supplies is an arrangement, a row read off the observation is not."""
    from deity_informant import gtoracle  # pylint: disable=import-outside-toplevel

    nodes, _gen = gtoracle.index_nodes(graph)
    readers = [
        g
        for g in graph.nodes
        if g.route[0] == "plane"
        and g.transfer[0] == "SELECT"
        and len(g.transfer[2]) == 2
        and g.transfer[2][0] == "node"
    ]
    return {
        "%s_index_nodes" % tag: nodes,
        "%s_row_counters"
        % tag: sum(1 for g in graph.nodes if g.route == ("index",) and g.transfer[0] == "RAMP"),
        "%s_generated_selects" % tag: len(readers),
        "%s_arranged_emits" % tag: sum(sum(_fires(graph, g, nframes)) for g in readers),
    }


def _struct_axis(ours, native, nframes, oracle=None, report=None):
    """The arrangement axis, format side and recovery side reported apart.

    ``fmt_*`` is what the mapped graph expresses of the composer's own arrangement;
    ``our_*`` is what the tracker's recovery of the same tune expresses. Never summed."""
    fired = {i: _fires(ours, g, nframes) for i, g in enumerate(ours.nodes) if g.route[0] == "plane"}
    hit = 0
    for f, row in enumerate(native.onsets):
        for v, on in enumerate(row):
            if on and any(
                c[f]
                for i, c in fired.items()
                if ours.nodes[i].route[1] // 7 == v and ours.nodes[i].route[1] % 7 in (4, 5, 6)
            ):
                hit += 1
    out = {
        "native_patterns": native.structure["patterns"],
        "native_orderlist": native.structure["orderlist_entries"],
        "native_rows": native.structure["pattern_rows"],
        "native_onsets": sum(sum(fr) for fr in native.onsets),
        "our_nodes": len(ours.nodes),
        "our_edge_nodes": sum(1 for g in ours.nodes if g.transfer[0] in ("EDGE", "DIV")),
        "our_fires": sum(sum(c) for c in fired.values()),
        "onsets_on_a_fire": hit,
        **_index_census(ours, nframes, "our"),
        **_onset_periods(native, nframes),
    }
    if oracle is not None:
        out.update(_index_census(oracle, nframes, "fmt"))
    for k, v in (report.arrangement if report is not None else {}).items():
        out.setdefault("fmt_%s" % k, v)
    return out


def _onset_periods(native, nframes):
    """How many note-on streams a `DIV` could generate, and how many it misses on phase."""
    out = {"periodic_voices": 0, "periodic_onsets": 0, "at_div_phase": 0, "voices": 0}
    for v in range(3):
        at = [f for f in range(nframes) if native.onsets[f][v]]
        if len(at) < 3:
            continue
        out["voices"] += 1
        gaps = {b - a for a, b in zip(at, at[1:])}
        if len(gaps) != 1:
            continue
        n = gaps.pop()
        out["periodic_voices"] += 1
        out["periodic_onsets"] += len(at)
        out["at_div_phase"] += int(n > 1 and all((f + 1) % n == 0 for f in at))
    return out


def _cov(cov):
    """A `Coverage` as plain JSON."""
    return {
        "interp": cov.interp,
        "total": cov.total,
        "planes": {k: list(v) for k, v in cov.planes.items()},
        "classes": cov.classes,
        "triggers": list(cov.triggers),
    }


def _ours(prog, trace, nframes):
    """``(graph, Coverage)`` from the tracker's own recovery of the same tune."""
    from deity_informant import tracker

    gt, ords, lww, acc = tracker._observe(prog, trace, nframes)  # pylint: disable=protected-access
    pitch = tracker._pitch(prog, tracker._freq_words(gt))  # pylint: disable=protected-access
    graph, _lanes = tracker._graph(
        prog, pitch, gt, ords, lww, acc
    )  # pylint: disable=protected-access
    return graph, tracker.coverage(graph, nframes)


def gt_one(rel):
    """Oracle, recovery and the three axes for one cached GT-packed .sid."""
    from deity_informant import gtoracle, tracker
    from deity_informant.c64 import psid_songs

    path, out = HVSC / rel, {"editor": "goattracker", "name": rel}
    try:
        _songs, start = psid_songs(path.read_bytes())
        song, info, psub = gtoracle.gt_decompile(path, start - 1)
        prog, trace = _lifted(path, start - 1, FRAMES)
        records = tracker.oracle(prog, trace, FRAMES)
        native = gtoracle.gt_native(song, info, psub, FRAMES)
        offset = gtoracle.align(records, native)
        _g, admitted = gtoracle.graph(records, native, offset)
        _g2, strict = gtoracle.strict(native, records, offset)
        ours_graph, ours_cov = _ours(prog, trace, FRAMES)
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:70])
        return out
    rows = {(f - offset, r): v for (f, r), v in _rows(ours_graph, FRAMES).items() if f >= offset}
    n = admitted.frames
    share, off, emits = _pitch_axis(rows, native, n)
    isha, isize, onto, iemits = _instr_axis(rows, native, n)
    out.update(
        {
            "frames": n,
            "offset": offset,
            "admitted_law": admitted.divergence is None,
            "admitted": _cov(admitted.coverage),
            "strict_matched": strict.matched,
            "strict_law": strict.divergence is None,
            "strict_divergence": None if strict.divergence is None else str(strict.divergence),
            "raw_kinds": {str(k): v for k, v in strict.raw_kinds.items()},
            "ours": _cov(ours_cov),
            "pitch": {"share": share, "offset": off, "emits": emits},
            "instruments": {"share": isha, "rows": isize, "bijection": onto, "emits": iemits},
            "structure": _struct_axis(ours_graph, native, n, _g, admitted),
        }
    )
    return out


def sw_one(name):
    """Oracle for one cached SID-Wizard .swm, against that editor's own player."""
    from deity_informant import gtoracle
    from pysidwizard import read_swm  # pylint: disable=import-error

    out = {"editor": "sidwizard", "name": name}
    try:
        swm = read_swm(str(SWM / name))
        native, records = gtoracle.sw_native(swm, FRAMES)
        ograph, admitted = gtoracle.graph(records, native, 0)
        _g2, strict = gtoracle.strict(native, records, 0)
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:70])
        return out
    out.update(
        {
            "frames": admitted.frames,
            "admitted_law": admitted.divergence is None,
            "admitted": _cov(admitted.coverage),
            "strict_matched": strict.matched,
            "strict_law": strict.divergence is None,
            "strict_divergence": None if strict.divergence is None else str(strict.divergence),
            "raw_kinds": {str(k): v for k, v in strict.raw_kinds.items()},
            "structure": dict(
                {"native_%s" % k: v for k, v in native.structure.items()},
                **_index_census(ograph, admitted.frames, "fmt"),
                **{"fmt_%s" % k: v for k, v in admitted.arrangement.items()},
            ),
        }
    )
    return out


def _run(item):
    """Dispatch one work item by editor."""
    kind, name = item
    return gt_one(name) if kind == "gt" else sw_one(name)


def main(argv):
    """Run both oracles over the caches and write the JSON report."""
    items = []
    if argv[1:2] != ["sw"]:
        scan = json.loads((ROOT / "out" / "gt_scan.json").read_text())
        items += [("gt", r["rel"]) for r in scan if r.get("ok")]
    if argv[1:2] != ["gt"]:
        items += [("sw", p.name) for p in sorted(SWM.glob("*.swm"))]
    with mp.Pool(WORKERS) as pool:
        res = list(pool.imap_unordered(_run, items, chunksize=1))
    OUT.write_text(json.dumps(res, indent=1))
    ok = [r for r in res if "error" not in r]
    print("ran %d, ok %d" % (len(res), len(ok)))
    for r in sorted(ok, key=lambda x: (x["editor"], -x["strict_matched"])):
        cov = r["admitted"]
        print(
            "%-10s %-50s law %-5s strict %3d/%-3d interp %5d/%-5d %5.1f%%"
            % (
                r["editor"],
                r["name"][-50:],
                r["admitted_law"],
                r["strict_matched"],
                r["frames"],
                cov["interp"],
                cov["total"],
                100.0 * cov["interp"] / max(1, cov["total"]),
            )
        )


if __name__ == "__main__":
    main(sys.argv)
