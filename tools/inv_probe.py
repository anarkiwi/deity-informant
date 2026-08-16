"""Per-tune 8.4 invariant probe: procedures, call forms, sp, page-one fault.

Reads the same artifact the gate judges (``_sweep.build``), then applies the
canonical suite's own checker (``tests/_callgen.violations``) shape by shape.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

_CALLS = ("call", "callb", "dcall", "swc", "pcall")


def _counts(prog):
    from deity_informant import frameproc

    sp = frameproc._SP  # pylint: disable=protected-access
    out = {"procs": len(prog.procs), "calls": 0, "rets": 0, "sp": 0, "text": 0}

    def walk(stmts, last):
        for i, s in enumerate(stmts):
            if s[0] in _CALLS:
                out["calls"] += 1
            elif s[0] == "ret" and not (stmts is last and i == len(stmts) - 1):
                out["rets"] += 1
            if s[0] == "asg" and s[1] == sp:
                out["sp"] += 1
            for x in frameproc._stmt_exprs(s):  # pylint: disable=protected-access
                if sp in frameproc._locset(x):  # pylint: disable=protected-access
                    out["sp"] += 1
            for b in frameproc._stmt_bodies(s):  # pylint: disable=protected-access
                walk(b, last)

    for entry, params, rets, stmts in prog.procs:
        if sp in set(params) | set(rets):
            out["sp"] += 1
        walk(stmts, stmts)
        del entry, params, rets
    return out


def _one(entry, frames, fold=False):
    from deity_informant import frameprog, frameval
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    full = int(secs * 50)
    n = full if frames is None else min(frames, full)
    t0 = time.monotonic()
    model, prog, _ev = _sweep.build(mem, init, play, full, sub, fold=fold)
    row = {**_sweep.row_head(entry), "frames": n, "wall_s": round(time.monotonic() - t0, 1)}
    row.update(_counts(prog))
    row["state"] = len(prog.state)
    row["text"] = len(frameprog.dumps(prog))
    trace, _walker = frameprog.iota(model, n)
    try:
        got = frameval.gate_fp(model, n, prog)
        row["gate"] = None if got is None else list(got)
    except frameval.FrameFault as exc:
        row["fault"] = str(exc)
    del trace
    return row


def one(entry, frames=None, fold=False):
    """One tune's invariant row; ``fault`` where the evaluator refused the text."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, frames, fold)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


GRAPH_FRAMES = 200  # the fold survey's window: the channels saturate long before it


def _graph(entry, frames):
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    n = min(frames, int(secs * 50)) or 1
    t0 = time.monotonic()
    ev = S.trace(mem, init, play, n, sub, graph=True)
    pcs, g = len(ev.pcs), ev.graph
    viol = g.sp_violations()
    multi = odd = 0
    for node in g.nodes:
        if node[1][0] != 0x60 or not node[2]:  # RTS; an empty context returns the invocation
            continue
        succ = {s[0] for s in g.edges.get(node, ())}
        multi += len(succ) > 1
        odd += succ != {(node[2][-1] + 3) & 0xFFFF}
    return {
        **_sweep.row_head(entry),
        "frames": n,
        "pcs": pcs,
        "nodes": len(g.nodes),
        "ratio": round(len(g.nodes) / max(pcs, 1), 2),
        "depth": g.depth,
        "sp_viol": len(viol),
        "sp_examples": [[pc, list(ctx), sorted(g.sp_of[(pc, ctx)])] for pc, ctx in viol[:3]],
        "rts_multi": multi,
        "rts_odd": odd,
        "insns": g.insns,
        "rbw": len(g.rbw),
        "ld_sites": len(g.ld_sites),
        "st_sites": len(g.st_sites),
        "wall_s": round(time.monotonic() - t0, 1),
    }


def graph_one(entry, frames=GRAPH_FRAMES):
    """One tune's fold-channel row: nodes, context depth and the F-ctx violations."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _graph(entry, frames)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def graph_klass(row):
    """The fold class of one graph row: bounded, or the channel that is not."""
    if "error" in row:
        return "error"
    if row["sp_viol"]:
        return "sp-violation"
    if row["rts_multi"] or row["rts_odd"]:
        return "rts-dispatch"
    return "bounded"


def _pick(vals, q):
    return vals[min(len(vals) - 1, int(q * len(vals)))]


_ROW = (
    "%-44s f%-5d pcs %-5d nodes %-6d ratio %-5.2f depth %d viol %d rts %d/%d"
    " insns %-9d rbw %-5d ld %-5d st %-5d %.1fs"
)


def graph_report(rows):
    """Print the survey: per-tune channels, then inflation, depth and what is not clean."""
    ok = [r for r in rows if "error" not in r]
    for r in sorted(rows, key=lambda x: x["tune"]):
        if "error" in r:
            print("%-44s error %s" % (r["tune"], r["error"]))
            continue
        print(
            _ROW
            % tuple(
                r[k]
                for k in (
                    "tune",
                    "frames",
                    "pcs",
                    "nodes",
                    "ratio",
                    "depth",
                    "sp_viol",
                    "rts_multi",
                    "rts_odd",
                    "insns",
                    "rbw",
                    "ld_sites",
                    "st_sites",
                    "wall_s",
                )
            )
        )
        for pc, ctx, sps in r["sp_examples"]:
            print("    sp_viol $%04X ctx %s sp %s" % (pc, [hex(c) for c in ctx], sps))
    print("%6d tunes, %d traced" % (len(rows), len(ok)))
    if ok:
        ratios = sorted(r["ratio"] for r in ok)
        print(
            "ratio median %.2f p90 %.2f max %.2f"
            % (_pick(ratios, 0.5), _pick(ratios, 0.9), ratios[-1])
        )
        hist = {}
        for r in ok:
            hist[r["depth"]] = hist.get(r["depth"], 0) + 1
        print(
            "depth max %d  %s" % (max(hist), " ".join("%d:%d" % kv for kv in sorted(hist.items())))
        )
    for name, key in (("sp_viol", "sp_viol"), ("rts_multi", "rts_multi"), ("rts_odd", "rts_odd")):
        hit = [r for r in ok if r[key]]
        print("%s: %s" % (name, ", ".join("%s(%d)" % (r["tune"], r[key]) for r in hit) or "none"))
    errs = {}
    for r in rows:
        if "error" in r:
            k = r["error"].split(":")[0]
            errs[k] = errs.get(k, 0) + 1
    for k in sorted(errs, key=lambda x: -errs[x]):
        print("%6d  error %s" % (errs[k], k))


def klass(row):
    """The movement class of one row: clean, or the reason it is not."""
    if "error" in row:
        return "error"
    if "fault" in row:
        return "fault:" + row["fault"].split("$")[0].strip()
    if row.get("gate") is not None:
        return "diverged"
    if row["procs"] == 1 and not row["calls"] and not row["rets"] and not row["sp"]:
        return "clean"
    return "residue"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tunes")
    ap.add_argument("--frames", type=int)
    ap.add_argument("-j", "--procs", type=int, default=24)
    ap.add_argument("-o", "--out")
    ap.add_argument("--fold", action="store_true", help="emit through framepath (11)")
    ap.add_argument(
        "--graph", action="store_true", help="trace only: the fold's channels (fold doc 9.5)"
    )
    args = ap.parse_args()
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    out = args.out or str(ROOT / "out" / ("inv_graph.json" if args.graph else "inv_probe.json"))
    frames = args.frames if args.frames is not None else (GRAPH_FRAMES if args.graph else None)
    fn, work = (
        (graph_one, [(e, frames) for e in tunes])
        if args.graph
        else (
            one,
            [(e, frames, args.fold) for e in tunes],
        )
    )
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = pool.starmap(fn, work)
    kf = graph_klass if args.graph else klass
    tally = {}
    for r in rows:
        k = kf(r)
        tally[k] = tally.get(k, 0) + 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"tally": tally, "rows": rows}, indent=1), "utf-8")
    for k in sorted(tally, key=lambda x: -tally[x]):
        print("%6d  %s" % (tally[k], k))
    if args.graph:
        graph_report(rows)


if __name__ == "__main__":
    main()
