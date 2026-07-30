"""Audit tracker pitch-table detection across the full corpus (resumable).

Decompiles each corpus tune at full Songlengths, runs production
`tracker.lift(...).pitch` over its frame program, and records hit/miss; a miss
carries its declared-table inventory. Appends out/pitch_audit.jsonl, skips done.
"""

import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "out" / "pitch_audit.jsonl"


def _inventory(prog):
    """Declared const tables the pitch search had to choose from, on a miss."""
    from deity_informant import tracker

    avail = tracker._avail(prog)
    return ["$%04X[%d]" % (b, n) for b, n in sorted(avail.items()) if n >= 24][:32]


def audit_one(entry):
    """Audit one tune; dict with found=True/False/None plus triage on a miss."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant import tracker
    from deity_informant.c64 import load_psid

    path, sub, secs = entry
    stem, composer = path.stem, path.parent.name
    try:
        mem, _load, init, play = load_psid(path.read_bytes())
        mem[0xD418] = 0x0F
        nframes = secs * 50
        model, _ev = S.decompile(mem, init, play, nframes, sub)
        prog = frameprog.program(model)
        trace, _walker = frameprog.iota(model, nframes)
        p = tracker.lift(prog, tracker.oracle(prog, trace, nframes)).pitch
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
        return {
            "tune": stem,
            "composer": composer,
            "found": False,
            "declared": _inventory(prog),
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
