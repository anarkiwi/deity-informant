"""Scan cached HVSC for GoatTracker-packed .sids and write out/gt_scan.json.

`tools/gt_compare.py` reads that file and nothing in the tree produced it. Fetches
round-robin across composers when the cache is short, decompiles each candidate with
`pygoattracker` and records the verdict; resumable through the same JSON.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HVSC = ROOT / ".oracle-cache" / "hvsc"
OUT = ROOT / "out" / "gt_scan.json"


def _cached():
    """Relpaths of every .sid already in the cache."""
    return sorted(str(p.relative_to(HVSC)) for p in HVSC.rglob("*.sid"))


def _index():
    """``{'A/Name': [relpath]}`` from the cached Songlengths.md5."""
    pat = re.compile(r"^; (/MUSICIANS/([A-Z]/[^/]+)/.+\.sid)$")
    idx = {}
    for line in (HVSC / "Songlengths.md5").read_text(encoding="latin-1").splitlines():
        m = pat.match(line)
        if m:
            idx.setdefault(m.group(2), []).append(m.group(1).lstrip("/"))
    return idx


def _roundrobin(idx):
    order = sorted(idx)
    i = 0
    while any(i < len(idx[c]) for c in order):
        for c in order:
            if i < len(idx[c]):
                yield idx[c][i]
        i += 1


def verdict(rel):
    """``{'rel', 'ok', 'err'}`` for one cached tune: does it decompile as GoatTracker?"""
    from deity_informant import gtoracle
    from deity_informant.c64 import psid_songs

    try:
        _songs, start = psid_songs((HVSC / rel).read_bytes())
        gtoracle.gt_decompile(HVSC / rel, start - 1)
        return {"rel": rel, "ok": True}
    except Exception as exc:  # pylint: disable=broad-except
        return {"rel": rel, "ok": False, "err": "%s: %s" % (type(exc).__name__, str(exc)[:60])}


def main(argv):
    """Fetch up to ``argv[1]`` more tunes, then rescan the whole cache."""
    want = int(argv[1]) if len(argv) > 1 else 0
    if want:
        from pysidtracker.testing import resolve_tune  # pylint: disable=import-error

        have = set(_cached())
        for rel in _roundrobin(_index()):
            if not want:
                break
            if rel in have:
                continue
            try:
                if resolve_tune(rel, cache_dir=HVSC):
                    want -= 1
            except Exception:  # pylint: disable=broad-except
                pass
    res = [verdict(rel) for rel in _cached()]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    print("scanned %d, goattracker %d" % (len(res), sum(1 for r in res if r["ok"])))


if __name__ == "__main__":
    main(sys.argv)
