"""Static header census over every HVSC .sid (no emulation): container kind,
play entry, speed bits, subtunes, clock/model flags, image size, SIDId family.

    python tools/survey/headers.py --hvsc C64Music --results results.csv --out headers.csv
"""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path


def header_row(path, data):
    magic = data[:4].decode("ascii", "replace")
    version, off, load, init, play, songs, start, speed = struct.unpack(">HHHHHHHI", data[4:22])
    flags = struct.unpack(">H", data[118:120])[0] if version >= 2 else 0
    body = data[off:]
    if load == 0 and len(body) >= 2:
        load = body[0] | (body[1] << 8)
        body = body[2:]
    speed_bits = bin(speed).count("1")
    return {
        "path": path,
        "magic": magic,
        "version": version,
        "play0": int(play == 0),
        "songs": songs,
        "speed_any_cia": int(speed != 0),
        "speed_bits": speed_bits,
        "clock": ("?", "PAL", "NTSC", "PAL+NTSC")[(flags >> 2) & 3],
        "model": ("?", "6581", "8580", "6581+8580")[(flags >> 4) & 3],
        "second_sid": int(version >= 3 and data[0x7A] != 0),
        "third_sid": int(version >= 4 and data[0x7B] != 0),
        "basic": int(bool(flags & 1) and magic == "RSID"),
        "load": load,
        "size": len(body),
        "init": init,
        "play": play,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hvsc", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    fam = {r["path"]: r["player"] for r in csv.DictReader(open(a.results, encoding="utf-8"))}
    root = Path(a.hvsc)
    with open(a.out, "w", newline="") as f:
        w = None
        for p in sorted(root.rglob("*.sid")):
            rel = str(p.relative_to(root))
            row = header_row(rel, p.read_bytes())
            row["family"] = fam.get(rel, "*Uncatalogued*")
            if w is None:
                w = csv.DictWriter(f, fieldnames=list(row))
                w.writeheader()
            w.writerow(row)


if __name__ == "__main__":
    main()
