"""Rank the 8.4 residue by the mechanism blocking it (docs/denotation-solve.md 8.4).

``inv_probe`` counts a tune's call lines and ``sp`` tokens; this asks what each is
waiting on. A call line is attributed to the copy refusal that keeps it (``Carry``,
or a pc the scoped-merge framework could not free), an ``sp`` token to its proof.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

_CALLS = ("call", "callb", "dcall", "swc", "pcall")


def _bodies_of(s):
    from deity_informant import frameproc

    return frameproc._stmt_bodies(s)  # pylint: disable=protected-access


def _bound_pcs(stmts, out=None):
    """The pcs a copy of ``stmts`` would bind twice: its ``label`` targets."""
    out = [] if out is None else out
    for s in stmts:
        if s[0] == "label":
            out.append(s[1])
        for b in _bodies_of(s):
            _bound_pcs(b, out)
    return sorted(set(out))


def _loops(entry, cfg):
    """``(idom, rpo, {header: body}, RC-set, pred)`` of one procedure CFG."""
    from deity_informant import render

    nodes, succ, pred = cfg
    idom, rpo = render._idoms(entry, succ, nodes)  # pylint: disable=protected-access
    nodeset = set(nodes)

    def dom(a, b):
        n = b
        while True:
            if n == a:
                return True
            p = idom.get(n)
            if p is None or p == n:
                return False
            n = p

    headers = {}
    for s in nodes:
        for h in succ.get(s, ()):
            if h in nodeset and dom(h, s):
                headers.setdefault(h, set())
    for h in headers:
        body = {h}
        stack = [s for s in nodes for t in succ.get(s, ()) if t == h and dom(h, s)]
        while stack:
            n = stack.pop()
            if n not in body:
                body.add(n)
                stack.extend(pred[n])
        headers[h] = body
    rc = {
        h
        for s in nodes
        for h in succ.get(s, ())
        if h in nodeset and rpo.get(h, 0) <= rpo.get(s, 0) and not dom(h, s)
    }
    return idom, rpo, headers, rc, pred


def _homed(view, pc):
    """``(entry, cfg)`` of the planned procedure whose CFG carries ``pc``."""
    from deity_informant import codec, render

    for entry in codec.procedures(view):
        cfg = render._proc_cfg(view, entry)  # pylint: disable=protected-access
        if pc in set(cfg[0]):
            return entry, cfg
    return None, None


def _label_class(pc, entry, cfg):
    """Why the scoped-merge framework left ``pc`` a label rather than a scope."""
    if entry is None:
        return "unhomed"
    idom, rpo, headers, rc, pred = _loops(entry, cfg)
    anchor = idom.get(pc)
    if anchor is None or anchor == pc:
        return "no-anchor"
    if pc in headers:
        return "loop-header"
    if len(pred.get(pc, ())) < 2:
        return "single-pred"
    if pc in rc:
        return "rc-set"
    lo, hi = rpo.get(anchor, 0), rpo.get(pc, 0)
    if any(lo < rpo.get(h, 0) < hi for h in headers):
        return "merge-straddles-loop"
    if any(anchor in body for body in headers.values()):
        return "merge-in-cycle"
    return "unclassified"


def _call_class(model, view, target, bodies):
    """The class keeping one call line: the copy refusal, named."""
    from deity_informant import procpass

    if target is None:
        return "computed-call"
    if not procpass.carry(model).body(target):
        return "carry-refused"
    stmts = bodies.get(target)
    if stmts is None:
        return "no-body"
    pcs = _bound_pcs(stmts)
    if not pcs:
        return "copyable-but-uncopied"
    return "label:" + ",".join(sorted({_label_class(p, *_homed(view, p)) for p in pcs}))


def _sp_classes(prog):
    """The refusal class of every stack proof the program carries, ``sp`` and slots."""
    out = []
    for p in prog.proofs:
        if p.status == "refused" and p.kind in ("sp", "spslot", "stack"):
            out.append("%s: %s" % (p.kind, p.lemma.split("; ")[-1].split(":", 1)[0]))
        elif p.kind == "spslot" and p.status == "held":
            out.append("spslot: held")
    return sorted(out)


def _walk(stmts, last, out):
    for i, s in enumerate(stmts):
        if s[0] in _CALLS:
            out["calls"].append((s[0], s[1] if isinstance(s[1], int) else None))
        elif s[0] == "ret" and not (stmts is last and i == len(stmts) - 1):
            out["rets"] += 1
        for b in _bodies_of(s):
            _walk(b, last, out)
    return out


def _one(entry, frames):
    from deity_informant import frameprog, frameval, sidprog
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    full = int(secs * 50)
    n = full if frames is None else min(frames, full)
    model, prog, _ev = _sweep.build(mem, init, play, full, sub)
    row = {**_sweep.row_head(entry), "procs": len(prog.procs)}
    got = _walk_procs(prog)
    row["rets"] = got["rets"]
    view = sidprog._SortedView(model)  # pylint: disable=protected-access
    bodies = {e: s for e, _p, _r, s in prog.procs}
    row["call_classes"] = sorted(
        Counter(_call_class(model, view, t, bodies) for _k, t in got["calls"]).items()
    )
    row["sp_classes"] = sorted(Counter(_sp_classes(frameprog.program(model))).items())
    try:
        frameval.gate_fp(model, n, prog)
    except frameval.FrameFault as exc:
        row["fault"] = str(exc).split("$", maxsplit=1)[0].strip()
    return row


def _walk_procs(prog):
    got = {"calls": [], "rets": 0}
    for _e, _pa, _r, stmts in prog.procs:
        _walk(stmts, stmts, got)
    return got


def one(entry, frames=None):
    """One tune's residue, attributed; ``error`` where the build refused."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, frames)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tunes")
    ap.add_argument("--frames", type=int)
    ap.add_argument("-j", "--procs", type=int, default=24)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "call_residue.json"))
    args = ap.parse_args()
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(one, [(e, args.frames) for e in tunes]))
    calls, sps, faults, tunes_by = Counter(), Counter(), Counter(), Counter()
    for r in rows:
        for k, v in r.get("call_classes", ()):
            calls[k] += v
            tunes_by[k] += 1
        for k, v in r.get("sp_classes", ()):
            sps[k] += v
        if "fault" in r:
            faults[r["fault"]] += 1
    out = {
        "wall_s": round(time.monotonic() - t0, 1),
        "call_sites": calls.most_common(),
        "call_tunes": tunes_by.most_common(),
        "sp_procs": sps.most_common(),
        "faults": faults.most_common(),
        "errors": len([r for r in rows if "error" in r]),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
