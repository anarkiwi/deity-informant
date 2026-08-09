"""Name the player a tune runs, from the SIDId signature database.

A fingerprint clusters tunes but does not name the cluster; SIDId's byte
signatures do. A name owns alternatives, each a sequence of literal bytes and
``??`` wildcards, optionally ``AND``-joined parts that must all occur.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / ".oracle-cache" / "sidid.cfg"
WILD = -1

USAGE = """\
  python tools/player_id.py                              # name every cached tune
  python tools/player_id.py --tunes Commando,Aces_High
  python tools/player_id.py --tally                      # players by tune count"""


def parse(path=CONFIG):
    """``{name: [[part, ...], ...]}``: alternatives of AND-joined byte parts.

    A part is an int array with ``WILD`` at each ``??``. A hex line ending in
    ``END`` is one alternative; any other non-empty line names what follows."""
    out, name = {}, None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        toks = line.split()
        if toks[-1] != "END":
            name = line
            continue
        parts, cur = [], []
        for tok in toks[:-1]:
            if tok == "AND":
                parts.append(cur)
                cur = []
            else:
                cur.append(WILD if tok == "??" else int(tok, 16))
        parts.append(cur)
        if name and all(parts):
            out.setdefault(name, []).append([np.array(p, np.int16) for p in parts])
    return out


def _hit(img, part):
    """Whether ``part`` occurs in ``img``; the first literal byte anchors the scan."""
    lit = np.flatnonzero(part != WILD)
    if not lit.size:
        return True
    at = np.flatnonzero(img == part[lit[0]]) - lit[0]
    at = at[(at >= 0) & (at + part.size <= img.size)]
    for k in lit[1:]:
        if not at.size:
            return False
        at = at[img[at + k] == part[k]]
    return bool(at.size)


def identify(mem, sigs):
    """Every player name whose alternative matches the image."""
    img = np.frombuffer(bytes(mem), np.uint8).astype(np.int16)
    return sorted(n for n, alts in sigs.items() if any(all(_hit(img, p) for p in a) for a in alts))


def one(entry, sigs):
    """One tune's names, or the exception that stopped it."""
    from deity_informant.c64 import load_psid

    try:
        mem, _load, _init, _play = load_psid(Path(entry[0]).read_bytes())
        return {**_sweep.row_head(entry), "players": identify(mem, sigs)}
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--tally", action="store_true", help="rank players by tune count")
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "player_id.json"))
    args = ap.parse_args()

    sigs = parse()
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    rows = [one(e, sigs) for e in tunes]
    named = [r for r in rows if r.get("players")]
    if args.tally:
        for player, count in Counter(p for r in named for p in r["players"]).most_common():
            print("%5d  %s" % (count, player))
    else:
        for row in rows:
            print("%-52s %s" % (row["tune"], ", ".join(row.get("players", [])) or "-"))
    print(
        "\n%d signatures, %d tunes, %d named (%.1f%%)"
        % (len(sigs), len(rows), len(named), 100.0 * len(named) / max(len(rows), 1))
    )
    Path(args.out).write_text(json.dumps({"rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
