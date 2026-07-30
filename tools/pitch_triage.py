"""Triage remaining pitch-detection misses with graph facts (resumable).

Decompiles each miss in out/tracker_audit.jsonl at full Songlengths and records
graph-grounded facts (freq-role tables, decl stride/cobases, shadow-zeros,
freq kinds) and a representation label. Appends pitch_triage.jsonl.
"""

import json
import multiprocessing as mp
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
HVSC = ROOT / ".oracle-cache" / "hvsc"
OUT = ROOT / "out" / "pitch_triage.jsonl"


def _label(info, kinds):
    """Representation label from the graph facts."""
    if not info:
        return "no-freq-role (arithmetic or deep-shadow)"
    if any(r["stride"] and r["stride"] > 1 and r["cobases"] for r in info):
        return "record-block (freq is one field of a per-note record)"
    if any(r["zeros"] for r in info):
        return "scalar-shadow (freq-role cell reads zeros)"
    if kinds.get("slide") and not kinds.get("note"):
        return "glide/accumulator (freq is a slide; target behind acc)"
    return "table-unrecognized-layout (freq-role base, not confirmed ET)"


def _facts(item):
    from deity_informant import eqlift_annotate as ann
    from deity_informant import song_model as sm
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-error

    trk, rel = item
    try:
        path = resolve_tune(rel, cache_dir=HVSC)
        data = Path(path).read_bytes()
        mem, _load, init, play = load_psid(data)
        mem[0xD418] = 0x0F
        _s, ss = psid_songs(data)
        lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
        model, _ev = S.decompile(mem, init, play, song_seconds(data, lengths, ss - 1) * 50, ss - 1)
        m0 = model.mem0
        tr = ann.aggregate(list(ann.model_procs(model)), model)
        decls = {d["base"]: d for d in ann._decls(model)}
        info = []
        for b in sorted(b for b, rs in tr.roles.items() if any("freq" in x for x in rs)):
            d = decls.get(b, {})
            info.append(
                {
                    "base": "$%04X" % b,
                    "size": d.get("size"),
                    "stride": d.get("stride"),
                    "cobases": len(d.get("cobases", [])),
                    "zeros": all(x == 0 for x in m0[b : b + 12]),
                }
            )
        kinds = Counter(d.kind for d in sm.analyze(model).freq)
        return {
            "tracker": trk,
            "tune": Path(rel).stem,
            "label": _label(info, kinds),
            "freq_kinds": dict(kinds),
            "roles": info,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {
            "tracker": trk,
            "tune": Path(rel).stem,
            "label": "error",
            "err": "%s: %s" % (type(exc).__name__, exc),
        }


def main():
    """Triage every miss, appending JSONL."""
    audit = [
        json.loads(l)
        for l in (OUT.parent / "tracker_audit.jsonl").read_text().splitlines()
        if l.strip()
    ]
    miss = [(r["tracker"], r["rel"]) for r in audit if r["found"] is False]
    done = set()
    if OUT.exists():
        done = {json.loads(l)["tracker"] for l in OUT.read_text().splitlines() if l.strip()}
    items = [m for m in miss if m[0] not in done]
    print("misses %d, done %d, running %d" % (len(miss), len(done), len(items)))
    with OUT.open("a", encoding="utf-8") as fh, mp.Pool(min(mp.cpu_count(), 12)) as pool:
        for i, res in enumerate(pool.imap_unordered(_facts, items), 1):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            print("%3d/%d %-26s %s" % (i, len(items), res["tracker"][:26], res["label"]))


if __name__ == "__main__":
    main()
