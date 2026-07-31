"""Census for the arrangement (docs/tracker.md 7.4): what rung (f) hands the tracker.

Per cached tune: the resolved deref sites, the shape of each site's row index and of
each pointer's reload index, and whether the cells behind them are ones the play code
only steps or reloads by the program text. Writes out/tracker_arrange.json.
"""

import json
import multiprocessing as mp
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HVSC = ROOT / ".oracle-cache" / "hvsc"
FRAMES = 200


def _shape(idx, walk, env, tracker):
    """How a deref index is written: a walked cell, some cell, or neither."""
    if idx is None:
        return "none"
    cell = tracker._read_base(idx, env)
    if not cell:
        return "computed"
    return "cell/walk" if cell in walk else "cell"


def one(rel):
    """The arrangement census for one cached tune."""
    # pylint: disable=import-outside-toplevel,too-many-locals
    from deity_informant import frameprog, frameptr, tracker
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid, psid_songs

    out = {"name": rel}
    try:
        data = (HVSC / rel).read_bytes()
        mem, _load, init, play = load_psid(data)
        mem[0xD418] = 0x0F
        _songs, start = psid_songs(data)
        model, _ev = S.decompile(mem, init, play, FRAMES, start - 1)
        prog = frameprog.program(model)
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
        return out
    walk, env = tracker._walked(prog), tracker._prog_env(prog)
    sites = frameptr.analyse(prog.mem0, prog.data_decls, prog.procs)
    tabs = frameptr._Tables(prog.data_decls)
    pos = {}
    for s in tracker._stmts(prog):
        if s[0] == "st" and s[1][0] == "const":
            ent = frameptr._entry(s[2])
            if ent is not None:
                pos.setdefault(s[1][1], []).append(_shape(ent[2], walk, env, tracker))
    recs = []
    for s in sites:
        if s.why is not None:
            continue
        blocks = sorted(s.ptr.targets)
        recs.append(
            {
                "cell": s.ptr.cell,
                "row": _shape(s.idx, walk, env, tracker),
                "pos": sorted(set(pos.get(s.ptr.cell, ()))),
                "blocks": len(blocks),
                "declared": sum(1 for b in blocks if tabs.at(b) is not None),
                "extent": [t[2] for t in s.ptr.tables],
            }
        )
    out["sites"] = recs
    out["walked"] = sorted(walk)
    return out


def tunes():
    """Every cached ``.sid``, by relpath."""
    return sorted(str(p.relative_to(HVSC)) for p in HVSC.rglob("*.sid"))


def summarize(res):
    """Corpus totals: the row-index shape histogram over the resolved sites."""
    rows, poss, both, tun, n = Counter(), Counter(), Counter(), 0, 0
    for r in res:
        n += len(r.get("sites", ()))
        tun += bool(r.get("sites"))
        for s in r.get("sites", ()):
            rows[s["row"]] += 1
            poss["+".join(s["pos"]) or "none"] += 1
            both[(s["row"], "+".join(s["pos"]), s["declared"] == s["blocks"])] += 1
    return {
        "tunes": len(res),
        "tunes_with_a_resolved_site": tun,
        "sites": n,
        "row_index": dict(rows),
        "orderlist_index": dict(poss),
        "both": {str(k): v for k, v in both.most_common()},
    }


def main(argv):
    """Census the cache and write the JSON report."""
    out = Path(argv[1]) if argv[1:] else ROOT / "out" / "tracker_arrange.json"
    workers = int(os.environ.get("DI_WORKERS") or min(mp.cpu_count(), 48))
    with mp.Pool(workers) as pool:
        res = list(pool.imap_unordered(one, tunes(), chunksize=1))
    res.sort(key=lambda r: r["name"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"totals": summarize(res), "tunes": res}, indent=1), encoding="utf-8")
    print(json.dumps(summarize(res), indent=1))


if __name__ == "__main__":
    main(sys.argv)
