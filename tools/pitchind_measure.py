"""Output-side note-lane recovery over the 24 no-static-table tail (resumable).

Decompiles each tracker_audit miss at full Songlengths, runs pitchind.recover_lanes,
and records per-voice lattice fit / coverage / recovered. A ``--static`` mode instead
spot-checks induced-vs-static agreement on hit tunes. Appends out/pitchind_tail.jsonl.
"""

import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
HVSC = ROOT / ".oracle-cache" / "hvsc"
OUT = ROOT / "out" / "pitchind_tail.jsonl"


def _decompile(rel):
    """(model, nframes) for a corpus relpath at full Songlengths."""
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-error

    data = Path(resolve_tune(rel, cache_dir=HVSC)).read_bytes()
    mem, _l, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    _s, ss = psid_songs(data)
    lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
    nframes = song_seconds(data, lengths, ss - 1) * 50
    model, _ev = S.decompile(mem, init, play, nframes, ss - 1)
    return model, nframes


def _tail(item):
    """Recover output-side note lanes for one tail miss."""
    from deity_informant import pitchind as P

    trk, rel = item
    try:
        model, nframes = _decompile(rel)
        lanes, kinds = P.recover_lanes(model, nframes)
        return {
            "tracker": trk,
            "tune": Path(rel).stem,
            "nframes": nframes,
            "freq_kinds": kinds,
            "voices": [
                {
                    "voice": ln.voice,
                    "fit": round(lat.fit, 3),
                    "coverage": round(ln.coverage, 3),
                    "frames": ln.frames,
                    "notes": len(ln.notes),
                    "distinct": ln.distinct,
                    "span": ln.span,
                    "residual": round(ln.residual, 4),
                    "recovered": rec,
                }
                for ln, lat, rec in lanes
            ],
            "recovered": any(rec for _l, _t, rec in lanes),
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {"tracker": trk, "tune": Path(rel).stem, "err": "%s: %s" % (type(exc).__name__, exc)}


def _static(item):
    """Induced-vs-static agreement for one hit tune."""
    from deity_informant import pitchind as P

    trk, rel = item
    try:
        model, nframes = _decompile(rel)
        return {"tracker": trk, "tune": Path(rel).stem, "agreement": P.agreement(model, nframes)}
    except Exception as exc:  # pylint: disable=broad-except
        return {"tracker": trk, "tune": Path(rel).stem, "err": "%s: %s" % (type(exc).__name__, exc)}


def main():
    """Run the tail (default) or --static spot-check pool, appending JSONL."""
    audit = [json.loads(l) for l in (OUT.parent / "tracker_audit.jsonl").read_text().splitlines()]
    static = "--static" in sys.argv[1:]
    items = [(r["tracker"], r["rel"]) for r in audit if r.get("found") is static]
    if static:
        items = items[:: max(1, len(items) // 8)][:8]
    fn = _static if static else _tail
    out = OUT.with_name("pitchind_static.jsonl") if static else OUT
    print("running %d (%s)" % (len(items), "static" if static else "tail"))
    with out.open("w", encoding="utf-8") as fh, mp.Pool(min(mp.cpu_count(), 12)) as pool:
        for i, res in enumerate(pool.imap_unordered(fn, items), 1):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            print("%3d/%d %-24s %s" % (i, len(items), res["tune"][:24], res.get("err", "ok")))


if __name__ == "__main__":
    main()
