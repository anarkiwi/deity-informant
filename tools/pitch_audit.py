"""Audit tracker pitch-table detection across the full corpus (resumable).

Decompiles each corpus tune at full Songlengths, runs production
`tracker.lift(...).pitch`, records hit/miss; misses carry triage data plus a
permissive DIAGNOSTIC ET search. Appends to out/pitch_audit.jsonl, skips done.
"""

import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "out" / "pitch_audit.jsonl"


def _diag_table(model):
    """Permissive DIAGNOSTIC ET search (interleaved / split-block), or None."""
    import numpy as np

    from deity_informant import eqlift_annotate as ann
    from deity_informant import tracker

    m0 = model.mem0
    bases = sorted(b for b in tracker._read_bases(model) if 0x100 <= b < 0xFEFF)
    best, best_oct = [None], [-1]

    def consider(base, kind, words):
        v = ann.et_check(np.asarray(words, dtype=float))
        if v["pitch_table"] and v["octaves"] > best_oct[0]:
            best_oct[0] = v["octaves"]
            best[0] = {
                "base": "$%04X" % base,
                "kind": kind,
                "octaves": v["octaves"],
                "n": len(words),
            }

    for b in bases:
        for endian in ("<", ">"):
            for n in (24, 48, 72, 96):
                if b + 2 * n <= len(m0):
                    consider(
                        b,
                        "interleave" + endian,
                        np.frombuffer(bytes(m0[b : b + 2 * n]), endian + "u2"),
                    )
    byt = {
        b: np.frombuffer(bytes(m0[b : b + 96]), "u1").astype(np.int64)
        for b in bases
        if b + 96 <= len(m0)
    }
    for lo, lob in byt.items():
        for hi, hib in byt.items():
            if hi != lo:
                for n in (36, 72):
                    consider(lo, "split", lob[:n] | (hib[:n] << 8))
    return best[0]


def audit_one(entry):
    """Audit one tune; dict with found=True/False/None plus triage on a miss."""
    from deity_informant import eqlift_annotate as ann
    from deity_informant import structured as S
    from deity_informant import tracker
    from deity_informant.c64 import load_psid

    path, sub, secs = entry
    stem, composer = path.stem, path.parent.name
    try:
        mem, _load, init, play = load_psid(path.read_bytes())
        mem[0xD418] = 0x0F
        model, _ev = S.decompile(mem, init, play, secs * 50, sub)
        p = tracker.lift(model).pitch
        if p is not None:
            mode = "shift" if p.shift else ("split" if p.endian == "split" else "direct")
            return {
                "tune": stem,
                "composer": composer,
                "found": True,
                "mode": mode,
                "base": "$%04X" % p.base,
                "octaves": int(p.octaves),
                "n": len(p.words),
            }
        tr = ann.aggregate(list(ann.model_procs(model)), model)
        roles = {"$%04X" % b: r for b, r in tr.roles.items() if any("freq" in x for x in r)}
        return {
            "tune": stem,
            "composer": composer,
            "found": False,
            "freq_roles": roles,
            "diag": _diag_table(model),
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {
            "tune": stem,
            "composer": composer,
            "found": None,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


def main():
    """Run the audit over not-yet-recorded corpus tunes, appending JSONL."""
    from _corpus import corpus_params  # pylint: disable=import-error

    done = set()
    if OUT.exists():
        done = {json.loads(l)["tune"] for l in OUT.read_text().splitlines() if l.strip()}
    entries = [e for e in corpus_params(ROOT / ".oracle-cache" / "hvsc") if e[0].stem not in done]
    print("corpus %d, done %d, running %d" % (len(done) + len(entries), len(done), len(entries)))
    with OUT.open("a", encoding="utf-8") as fh, mp.Pool(min(mp.cpu_count(), 12)) as pool:
        for i, res in enumerate(pool.imap_unordered(audit_one, entries), 1):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            print("%3d/%d %-28s %s" % (i, len(entries), res["tune"], res.get("found")))


if __name__ == "__main__":
    main()
