"""Node-level diff of the recovered graph against the editor's own.

Both sides are `tracker.Graph`, so they compare node by node. Each node is keyed by
what it does — the writes, edges or rows it produces — and the two sets are matched
on that key; what is left on the editor's side is what the recovery never builds.
"""

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
HVSC = ROOT / ".oracle-cache" / "hvsc"


def _acts(graph, nframes):
    """``([node] -> signature, per-frame writes)``: what each node does.

    A plane node's signature is its writes, a fire node's its edges, an index node's
    its rows. Mirrors ``tracker._run``'s loop, which ``_faithful`` then checks."""
    from deity_informant import tracker

    nodes = graph.nodes
    counts = [0] * len(nodes)
    parts = tracker._masked(nodes)
    held = {reg: {} for reg in parts}
    prev, cur = {}, {}
    acts = [[] for _ in nodes]
    writes_by_frame = []
    for f in range(nframes):
        fires = tracker._fired(nodes, f)
        eaten = {
            g.route[4][1]
            for i, g in enumerate(nodes)
            if fires[i] and g.route[0] == "rel" and g.route[4][0] == "node"
        }
        last = {
            r: max((i for i in ns if fires[i] and i not in eaten), default=None)
            for r, ns in parts.items()
        }
        frame_writes = []
        for i, g in enumerate(nodes):
            if not fires[i]:
                continue
            if g.transfer[0] == "RAW":
                counts[i] += fires[i]
                rows = g.transfer[1]
                for reg, val in rows[f] if f < len(rows) else ():
                    acts[i].append((f, reg, val))
                    frame_writes.append((reg, val))
                    prev[reg] = val
            elif g.route == tracker.INDEX:
                for _t in range(fires[i]):
                    counts[i] += 1
                    cur[i] = v = tracker._emit(g, counts[i], cur)
                    acts[i].append((f, v))
            elif tracker._is_plane(g.route):
                reg = g.route[1]
                for _t in range(fires[i]):
                    counts[i] += 1
                    cur[i] = v = tracker._emit(g, counts[i], cur)
                    if g.route[0] == "rel":
                        v = tracker._combine(g.route, v, prev, cur)
                    if i in eaten:
                        continue
                    v = tracker._assemble(g, v, held.get(reg), i == last.get(reg))
                    if v is None:
                        continue
                    acts[i].append((f, reg, v & 0xFF))
                    prev[reg] = v & 0xFF
                    frame_writes.append((reg, v & 0xFF))
            else:
                counts[i] += fires[i]
                n = tracker._ticks(g, f) * fires[i]
                if n:
                    acts[i].append((f, n))
        writes_by_frame.append(frame_writes)
    return [tuple(a) for a in acts], writes_by_frame


def _rebase(sig, offset, win):
    """A signature moved into the oracle's aligned window, entries outside dropped."""
    return tuple((e[0] - offset,) + e[1:] for e in sig if offset <= e[0] < offset + win)


def _faithful(graph, nframes, writes):
    """The per-node attribution must rebuild the graph's own projection."""
    from deity_informant import framelog, tracker

    return framelog.canonical(writes) == tracker.eval_graph(graph, nframes)


def _role(g):
    """What a node is, musically: its transfer and where it sends it."""
    from deity_informant import tracker, trackertext

    if tracker._is_plane(g.route):
        where = trackertext._role(g.route[1], tracker._mask_of(g.route))
    elif g.route == tracker.INDEX:
        where = "a row index"
    elif g.route[0] == "fire":
        where = "triggers"
    else:
        where = "the residual floor"
    return "%s -> %s" % (g.transfer[0], where)


def _match(theirs, a_ours, a_theirs):
    """Pair nodes whose signature is identical, greedily and one to one."""
    pool = collections.defaultdict(list)
    for j, sig in enumerate(a_theirs):
        pool[sig].append(j)
    pairs, unmatched_ours = [], []
    for i, sig in enumerate(a_ours):
        if sig and pool.get(sig):
            pairs.append((i, pool[sig].pop(0)))
        else:
            unmatched_ours.append(i)
    taken = {j for _i, j in pairs}
    return pairs, unmatched_ours, [j for j in range(len(theirs.nodes)) if j not in taken]


def diff(rel, nframes):
    """The node-level diff for one GoatTracker-packed tune."""
    from deity_informant import gtoracle, tracker
    from deity_informant.c64 import psid_songs
    from gt_compare import _lifted, _ours

    path = HVSC / rel
    _songs, start = psid_songs(path.read_bytes())
    song, info, psub = gtoracle.gt_decompile(path, start - 1)
    prog, trace = _lifted(path, start - 1, nframes)
    records = tracker.oracle(prog, trace, nframes)
    native = gtoracle.gt_native(song, info, psub, nframes)
    offset = gtoracle.align(records, native)
    theirs, report = gtoracle.graph(records, native, offset)
    ours, _cov = _ours(prog, trace, nframes)
    win = report.frames

    a_ours, w_ours = _acts(ours, nframes)
    a_theirs, w_theirs = _acts(theirs, win)
    ok = _faithful(ours, nframes, w_ours) and _faithful(theirs, win, w_theirs)
    a_ours = [_rebase(sig, offset, win) for sig in a_ours]
    pairs, only_ours, only_theirs = _match(theirs, a_ours, a_theirs)

    produced = {e for sig in a_ours for e in sig if len(e) == 3}
    missing, emits, shaped, absent = (collections.Counter() for _ in range(4))
    for j in only_theirs:
        key = _role(theirs.nodes[j])
        missing[key] += 1
        writes = [e for e in a_theirs[j] if len(e) == 3]
        emits[key] += len(a_theirs[j])
        shaped[key] += sum(1 for e in writes if e in produced)
        absent[key] += sum(1 for e in writes if e not in produced)
    return {
        "tune": rel,
        "faithful": ok,
        "ours_nodes": len(ours.nodes),
        "their_nodes": len(theirs.nodes),
        "matched": len(pairs),
        "only_ours": len(only_ours),
        "only_theirs": len(only_theirs),
        "missing_kinds": missing.most_common(),
        "missing_emits": emits.most_common(),
        "same_write_wrong_shape": sum(shaped.values()),
        "write_never_produced": sum(absent.values()),
        "by_kind": [
            (k, shaped[k], absent[k]) for k, _n in missing.most_common() if shaped[k] or absent[k]
        ],
    }


def main(argv):
    """Diff the named tunes and rank what the recovery never builds."""
    nframes = int(argv[1]) if len(argv) > 1 else 600
    rels = argv[2:]
    total, tunes = collections.Counter(), 0
    for rel in rels:
        try:
            got = diff(rel, nframes)
        except Exception as exc:  # pylint: disable=broad-except
            print("%-46s FAILED %s: %s" % (rel, type(exc).__name__, exc))
            continue
        tunes += 1
        print(json.dumps(got, indent=1))
        for key, n in got["missing_emits"]:
            total[key] += n
    if tunes > 1:
        print("\n== generators the recovery never builds, by emits, over %d tunes ==" % tunes)
        for key, n in total.most_common(20):
            print("  %-52s %8d" % (key, n))


if __name__ == "__main__":
    main(sys.argv)
