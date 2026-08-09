"""Which player families the cache holds, and which the exemplar set misses.

A family runs one player at many addresses, so the fingerprint must survive
relocation: the opcode sequence of the executed instructions -- no addresses, no
operands -- shingled, MinHashed, joined by containment, cut at a plateau.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from functools import partial
from pathlib import Path

import numpy as np

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/family_cluster.py                        # the whole cache, clusters ranked
  python tools/family_cluster.py --threshold 0.4        # override the plateau pick
  python tools/family_cluster.py --ngram 6 --frames 900 # a longer gram, a shorter listen"""

# docs/idiom-catalog.md's exemplar set: one tune per player family this run tests for gaps.
EXEMPLARS = (
    "MUSICIANS/H/Hubbard_Rob/Commando",
    "MUSICIANS/G/Galway_Martin/Comic_Bakery",
    "MUSICIANS/G/Galway_Martin/Athena",
    "MUSICIANS/G/Goto80/Automatas",
    "MUSICIANS/J/Jammer/Grid_Runner",
    "MUSICIANS/C/Cadaver/Aces_High",
    "MUSICIANS/C/Chabee/Angry_Birds",
    "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts",
    "MUSICIANS/F/Follin_Tim/Agent_X_II_The_Mad_Profs_Back",
    "MUSICIANS/D/Daf/Alioth",
    "MUSICIANS/C/Cleve/ABC_Music",
    "MUSICIANS/A/Alfatech/Galway-tune",
    "MUSICIANS/B/Beast/Discmonsters_Intro",
    "MUSICIANS/T/Tel_Kees/Before_I_Forget",
    "MUSICIANS/D/Deek/4_Tunes",
    "MUSICIANS/B/Buckley_Kevin/Down_Under",
)

SEED = 0x5109  # fixed: every worker must draw the same permutations
U64 = np.uint64
GRID = np.round(np.arange(0.05, 0.96, 0.01), 2)
_LEN = None


def op_lens():
    """``len[opcode]``, 0 where the byte decodes to nothing the lifter walks."""
    global _LEN  # pylint: disable=global-statement
    if _LEN is None:
        from deity_informant.lifter import MODE_LEN, OPS

        _LEN = np.zeros(256, np.int64)
        for op, (_mn, mode) in OPS.items():
            _LEN[op] = MODE_LEN[mode]
    return _LEN


def _ragged(counts):
    """``(row, offset)`` of the flat expansion of ``counts``: the ragged-repeat trick."""
    row = np.repeat(np.arange(counts.size), counts)
    off = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    return row, off


def shingles(pcs, n, cap):
    """The executed opcode ``n``-grams, one integer per gram, addresses discarded.

    A node is an executed ``(pc, opcode)`` -- a self-modified cell executes as several
    -- and its successors are the nodes at ``pc + the instruction's length``. Paths
    sharing an end node and a prefix fold each step, bounding the walk at ``cap``.
    """
    lens = op_lens()
    pairs = sorted((pc, op) for pc, ops in pcs.items() for op in ops if lens[op])
    if not pairs:
        return np.zeros(0, U64)
    pc = np.array([p for p, _ in pairs], np.int64)
    op = np.array([o for _, o in pairs], np.int64)
    uniq, first = np.unique(pc, return_index=True)
    cnt = np.diff(np.append(first, pc.size))
    fall = (pc + lens[op]) & 0xFFFF
    slot = np.searchsorted(uniq, fall)
    live = (slot < uniq.size) & (uniq[np.minimum(slot, uniq.size - 1)] == fall)
    slot = np.where(live, slot, 0)
    end, val = np.arange(pc.size), op.astype(U64)
    for _ in range(n - 1):
        keep = live[end]
        end, val = end[keep], val[keep]
        if not end.size:
            return np.zeros(0, U64)
        row, off = _ragged(cnt[slot[end]])
        end = first[slot[end]][row] + off
        val = val[row] * U64(256) + op[end].astype(U64)
        if end.size > cap:
            _k, sel = np.unique(val * U64(1 << 20) + end.astype(U64), return_index=True)
            end, val = end[sel[:cap]], val[sel[:cap]]
    return np.unique(val)


def minhash(grams, k):
    """MinHash signature: the least value of ``k`` independent 64-bit scramblings.

    ``a*x + b`` with odd ``a`` is a bijection mod 2^64 and the splitmix finalizer
    decorrelates the rows, so two sets' least elements agree at their Jaccard rate.
    """
    rng = np.random.default_rng(SEED)
    a = rng.integers(1, 1 << 63, k, dtype=np.uint64) | U64(1)
    b = rng.integers(0, 1 << 63, k, dtype=np.uint64)
    out = np.full(k, np.iinfo(np.uint64).max, U64)
    for i in range(0, grams.size, 4096):
        h = grams[i : i + 4096, None] * a + b
        h ^= h >> U64(29)
        h *= U64(0xBF58476D1CE4E5B9)
        h ^= h >> U64(32)
        np.minimum(out, h.min(0), out=out)
    return out


def one(args, entry):
    """One tune's signature, or the exception that stopped it."""
    try:
        signal.alarm(args.cap_s)
        return _one(args, entry)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _one(args, entry):
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    t0 = time.monotonic()
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    ev = S.trace(mem, init, play, min(args.frames, int(secs * 50)) or 1, sub)
    grams = shingles(ev.pcs, args.ngram, args.path_cap)
    return {
        **_sweep.row_head(entry),
        "trace_s": round(time.monotonic() - t0, 1),
        "pcs": len(ev.pcs),
        "grams": int(grams.size),
        "sig": minhash(grams, args.sigs).tolist(),
    }


def similarity(sigs, block=64):
    """``(N, N)`` Jaccard estimates: the fraction of signature rows two tunes agree on."""
    n, k = sigs.shape
    out = np.empty((n, n), np.float32)
    for i in range(0, n, block):
        out[i : i + block] = (sigs[i : i + block, None, :] == sigs[None, :, :]).sum(2) / k
    return out


def containment(jac, sizes):
    """``|A&B| / min(|A|,|B|)`` from the Jaccard estimate and the exact gram counts.

    Two songs on one driver execute overlapping but differently sized instruction
    sets, and Jaccard scores that size gap as difference; the question here is
    whether the smaller is inside the larger. ``|A&B| = J(|A|+|B|)/(1+J)`` inverts
    the estimate, so this costs no second pass."""
    tot = sizes[:, None] + sizes[None, :]
    inter = jac * tot / (1.0 + jac)
    return (inter / np.maximum(np.minimum(sizes[:, None], sizes[None, :]), 1)).astype(np.float32)


def components(sim, thresh):
    """Connected components of the graph joining tunes at or above ``thresh``."""
    parent = np.arange(sim.shape[0])

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in zip(*np.nonzero(np.triu(sim >= thresh, 1))):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    labels = np.array([find(i) for i in range(sim.shape[0])])
    return np.unique(labels, return_inverse=True)[1]


def plateau(sim, grid):
    """``(threshold, curve)``: components per threshold, picked mid widest plateau.

    A threshold inside a long run of thresholds all yielding one clustering is one the
    answer does not depend on; the degenerate runs -- one blob, all singletons -- are
    excluded, being plateaus of the grid's ends rather than of the data.
    """
    curve = [int(components(sim, t).max() + 1) for t in grid]
    n = sim.shape[0]
    best, run = None, []
    for i, ct in enumerate(curve):
        run = run + [i] if run and curve[run[0]] == ct else [i]
        if 1 < ct < n and (best is None or len(run) > len(best)):
            best = list(run)
    pick = grid[best[len(best) // 2]] if best else grid[len(grid) // 2]
    return float(pick), list(zip(grid.tolist(), curve))


def cluster_rows(rows, sim, labels):
    """Clusters as records ranked by size: exemplars, cohesion, representatives."""
    ids = [r["tune"] for r in rows]
    where = {e: i for i, e in enumerate(ids) if e in EXEMPLARS}
    groups = sorted((np.nonzero(labels == c)[0] for c in range(labels.max() + 1)), key=len)
    out = []
    for g in reversed(groups):
        mem = set(g.tolist())
        order = g[np.argsort([-rows[i]["grams"] for i in g])]
        out.append(
            {
                "size": int(g.size),
                "exemplars": sorted(e for e, i in where.items() if i in mem),
                "min_inner_sim": round(float(sim[np.ix_(g, g)].min()), 3),
                "reps": [ids[i] for i in order[:5]],
                "members": [ids[i] for i in g],
            }
        )
    return out, where


def report(clusters, where, total, thresh, curve, show):
    """Print the ranked clusters and the headline: the families no exemplar reaches."""
    covered = sum(c["size"] for c in clusters if c["exemplars"])
    orphan = [c for c in clusters if not c["exemplars"]]
    print(
        "\n%d tunes, %d clusters at sim >= %.2f; exemplars fall in %d, covering %d tunes (%.1f%%)"
        % (
            total,
            len(clusters),
            thresh,
            sum(1 for c in clusters if c["exemplars"]),
            covered,
            100.0 * covered / max(total, 1),
        )
    )
    print("\ncomponents vs threshold:")
    print("  " + "  ".join("%.2f:%d" % (t, c) for t, c in curve[:: max(len(curve) // 18, 1)]))
    print("\n%5s %6s  %-32s %s" % ("size", "inner", "exemplar", "representatives"))
    for c in clusters[:show]:
        print(
            "%5d %6.2f  %-32s %s"
            % (
                c["size"],
                c["min_inner_sim"],
                ",".join(_sweep.tune_name(e) for e in c["exemplars"]) or "-",
                ", ".join(c["reps"][:2]),
            )
        )
    print(
        "\nUNREPRESENTED: %d clusters / %d tunes carry no exemplar"
        % (len(orphan), sum(c["size"] for c in orphan))
    )
    for c in orphan[:show]:
        print("%5d  %s" % (c["size"], ", ".join(c["reps"][:3])))
    missing = sorted(set(EXEMPLARS) - set(where))
    if missing:
        print("\nexemplars absent from the cache: %s" % ", ".join(missing))
    return orphan


def pair_stats(rows, sim):
    """Where the similarity mass sits: same-composer pairs against the rest."""
    iu = np.triu_indices(len(rows), 1)
    who = np.array([r["tune"].rsplit("/", 1)[0] for r in rows])
    same, vals = who[iu[0]] == who[iu[1]], sim[iu]
    qs = [1, 25, 50, 75, 95, 99]
    out = {
        "hist_20": [int(x) for x in np.histogram(vals, bins=20, range=(0, 1))[0]],
        "same_dir_pct": dict(zip(map(str, qs), np.percentile(vals[same], qs).round(3).tolist())),
        "cross_dir_pct": dict(zip(map(str, qs), np.percentile(vals[~same], qs).round(3).tolist())),
        "pairs_at_or_above": {"%.1f" % t: int((vals >= t).sum()) for t in np.arange(0.1, 1.0, 0.1)},
    }
    print("\npair similarity over %d pairs, 20 bins on [0,1]:" % vals.size)
    print("  " + " ".join(map(str, out["hist_20"])))
    print("  same-composer  p%s: %s" % (qs, list(out["same_dir_pct"].values())))
    print("  cross-composer p%s: %s" % (qs, list(out["cross_dir_pct"].values())))
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--frames", type=int, default=1500, help="play frames per tune, capped")
    ap.add_argument("--ngram", type=int, default=5, help="opcodes per shingle")
    ap.add_argument("--sigs", type=int, default=512, help="MinHash rows; stderr ~ 1/sqrt(rows)")
    ap.add_argument("--path-cap", type=int, default=1 << 17, help="live paths per gram step")
    ap.add_argument("--threshold", type=float, help="override the plateau-picked threshold")
    ap.add_argument(
        "--measure",
        choices=("containment", "jaccard"),
        default="containment",
        help="containment asks whether the smaller executed set sits inside the larger",
    )
    ap.add_argument("--show", type=int, default=40, help="clusters to list")
    ap.add_argument("--cap-s", type=int, default=min(60, _sweep.CAP_S), help="wall cap per tune")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "family_cluster.json"))
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(partial(one, args), tunes))
    done = [r for r in rows if "error" not in r and r["grams"]]
    sim = similarity(np.array([r["sig"] for r in done], np.uint64))
    if args.measure == "containment":
        sim = containment(sim, np.array([r["grams"] for r in done], np.float64))
    pick, curve = plateau(sim, GRID)
    thresh = args.threshold if args.threshold is not None else pick
    clusters, where = cluster_rows(done, sim, components(sim, thresh))
    stats = pair_stats(done, sim)
    orphan = report(clusters, where, len(done), thresh, curve, args.show)
    ex = sorted(where)
    out = {
        "tunes": len(done),
        "wall_s": round(time.monotonic() - t0, 1),
        "frames": args.frames,
        "ngram": args.ngram,
        "sig_rows": args.sigs,
        "threshold": thresh,
        "threshold_plateau_pick": pick,
        "component_curve": curve,
        "pair_stats": stats,
        "exemplar_sim": {a: {b: round(float(sim[where[a], where[b]]), 3) for b in ex} for a in ex},
        "clusters": clusters,
        "unrepresented": orphan,
        "skipped": [r for r in rows if "error" in r or not r.get("grams")],
        "rows": [{k: v for k, v in r.items() if k != "sig"} for r in rows],
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(
        "\n%d tunes, %d skipped, %.1fs -> %s"
        % (len(done), len(out["skipped"]), out["wall_s"], args.out)
    )


if __name__ == "__main__":
    main()
