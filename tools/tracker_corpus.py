"""Assemble a >=128-distinct-tracker HVSC corpus via SIDId identification.

Fetches tunes round-robin across composers (Songlengths.md5), identifies each
with SIDId (custom players fall back to a play-opcode key), dedups to >=128
distinct. Writes out/tracker_corpus.json; resumable via out/tracker_scan.jsonl.
"""

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
HVSC = ROOT / ".oracle-cache" / "hvsc"
CFG = ROOT / ".oracle-cache" / "sidid.cfg"
CFG_URL = "https://raw.githubusercontent.com/cadaver/sidid/master/sidid.cfg"
SCAN = ROOT / "out" / "tracker_scan.jsonl"
TARGET = 128
_BYTE = re.compile(r"^([0-9A-Fa-f]{2}|\?\?|AND|END)$")


def load_sigs():
    """Download/cache sidid.cfg and compile to [(name, [regex, ...])]."""
    if not CFG.exists():
        CFG.write_bytes(urllib.request.urlopen(CFG_URL, timeout=30).read())
    txt = CFG.read_text(encoding="latin-1")
    ents, name, toks = [], None, []
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if all(_BYTE.match(p) for p in s.split()):
            toks += s.split()
        else:
            if name and toks:
                ents.append((name, toks))
            name, toks = s, []
    if name and toks:
        ents.append((name, toks))
    out = []
    for nm, ts in ents:
        subs = [[]]
        for t in ts:
            if t == "END":
                break
            if t == "AND":
                subs.append([])
            else:
                subs[-1].append(t)
        rxs = [
            re.compile(
                b"".join(b"." if x == "??" else re.escape(bytes([int(x, 16)])) for x in sub),
                re.DOTALL,
            )
            for sub in subs
            if sub
        ]
        if rxs:
            out.append((nm, rxs))
    return out


def composers_index():
    """{'A/Name': [relpath, ...]} from the cached Songlengths.md5."""
    pat = re.compile(r"^; (/MUSICIANS/([A-Z]/[^/]+)/.+\.sid)$")
    idx = {}
    for line in (HVSC / "Songlengths.md5").read_text(encoding="latin-1").splitlines():
        m = pat.match(line)
        if m:
            idx.setdefault(m.group(2), []).append(m.group(1).lstrip("/"))
    return idx


def tracker_key(sigs, data):
    """(kind, key): SIDId player name, else a play-opcode signature."""
    from deity_informant.c64 import load_psid
    from deity_informant.lifter import MODE_LEN, OPS

    mem, _load, _init, play = load_psid(data)
    b = bytes(mem)
    hits = [nm for nm, rxs in sigs if all(r.search(b) for r in rxs)]
    if hits:
        return "sidid", hits[0]
    pc, ops = play, bytearray()
    for _ in range(160):
        ops.append(mem[pc])
        pc = (pc + MODE_LEN[OPS[mem[pc]][1]]) & 0xFFFF
    return "opcode", hashlib.md5(bytes(ops)).hexdigest()[:12]


def _roundrobin(idx):
    order = sorted(idx)
    i = 0
    while any(i < len(idx[c]) for c in order):
        for c in order:
            if i < len(idx[c]):
                yield idx[c][i]
        i += 1


def main():
    """Scan HVSC round-robin, dedup by tracker, write the manifest."""
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-error

    sigs = load_sigs()
    idx = composers_index()
    done = {}
    if SCAN.exists():
        for line in SCAN.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["rel"]] = r
    distinct = {}
    for r in done.values():
        if r.get("key"):
            distinct.setdefault(r["key"], r)

    def named():
        return sum(1 for k in distinct if k.startswith("sidid:"))

    with SCAN.open("a", encoding="utf-8") as fh:
        for rel in _roundrobin(idx):
            if named() >= TARGET:
                break
            if rel in done:
                continue
            try:
                path = resolve_tune(rel, cache_dir=HVSC)
                if path is None:
                    rec = {"rel": rel, "key": None, "err": "unreachable"}
                else:
                    kind, key = tracker_key(sigs, Path(path).read_bytes())
                    rec = {"rel": rel, "kind": kind, "key": "%s:%s" % (kind, key)}
            except Exception as exc:  # pylint: disable=broad-except
                rec = {"rel": rel, "key": None, "err": "%s" % type(exc).__name__}
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            done[rel] = rec
            if rec.get("key"):
                distinct.setdefault(rec["key"], rec)
                if len(distinct) % 16 == 0:
                    print("distinct=%d (sidid=%d) scanned=%d" % (len(distinct), named(), len(done)))
    (ROOT / "out" / "tracker_corpus.json").write_text(
        json.dumps(
            {
                "distinct": len(distinct),
                "sidid": named(),
                "trackers": {k: r["rel"] for k, r in distinct.items()},
            },
            indent=1,
        )
    )
    print("DONE distinct=%d sidid=%d scanned=%d" % (len(distinct), named(), len(done)))


if __name__ == "__main__":
    main()
