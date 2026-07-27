"""Pitch-detection audit over the >=128-distinct-tracker corpus (resumable).

Reads out/tracker_corpus.json (tracker -> representative tune), decompiles each
at full Songlengths, runs production tracker.lift, and records hit/miss keyed by
tracker. Appends out/tracker_audit.jsonl; skips tunes already recorded.
"""

import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
HVSC = ROOT / ".oracle-cache" / "hvsc"
OUT = ROOT / "out" / "tracker_audit.jsonl"


def _entry(rel):
    """(path, subtune, secs) for a relpath, or None if unresolved/undated."""
    from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-error

    path = resolve_tune(rel, cache_dir=HVSC)
    if path is None:
        return None
    data = Path(path).read_bytes()
    _mem, _load, _init, play = load_psid(data)
    _songs, startsong = psid_songs(data)
    lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
    secs = song_seconds(data, lengths, startsong - 1)
    return (Path(path), startsong - 1, secs) if play and secs else None


def audit(item):
    """Audit one (tracker, relpath); reuse pitch_audit.audit_one, tag tracker."""
    from pitch_audit import audit_one

    tracker_key, rel = item
    ent = _entry(rel)
    if ent is None:
        return {"tracker": tracker_key, "rel": rel, "found": None, "error": "unresolved"}
    res = audit_one(ent)
    res["tracker"] = tracker_key
    res["rel"] = rel
    return res


def main():
    """Run the audit over the tracker manifest, appending JSONL."""
    manifest = json.loads((ROOT / "out" / "tracker_corpus.json").read_text())["trackers"]
    done = set()
    if OUT.exists():
        done = {json.loads(l)["tracker"] for l in OUT.read_text().splitlines() if l.strip()}
    items = [(k, r) for k, r in manifest.items() if k not in done]
    print("trackers %d, done %d, running %d" % (len(manifest), len(done), len(items)))
    with OUT.open("a", encoding="utf-8") as fh, mp.Pool(min(mp.cpu_count(), 12)) as pool:
        for i, res in enumerate(pool.imap_unordered(audit, items), 1):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            print(
                "%3d/%d %-34s %-20s %s"
                % (
                    i,
                    len(items),
                    res.get("tune", res["rel"])[:34],
                    res["tracker"][:20],
                    res.get("found"),
                )
            )


if __name__ == "__main__":
    main()
