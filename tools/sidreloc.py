#!/usr/bin/env python3
"""Relocate a PSID by whole pages with Linus Akesson's sidreloc and report its byte roles.

``python tools/sidreloc.py TUNE.sid... [--pages N] [--zp LO-HI] [--frames F] [--out DIR]``
writes ``DIR/<name>.reloc.json``: the program map's role per loaded byte (``ROLES``) and the
bytes the relocated image changed, each checked against its role (``disagree``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import shutil
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import vtools  # noqa: E402

ROW = re.compile(r"^([0-9a-f]{4}), ([0-9a-f]{4}):  (.*)$")
ZP = re.compile(r"^(Old|New) zero-page addresses:\s*(.*)$")
ROLES = {"R": "hi", "Z": "zp", "=": "static", "?": "undetermined", ".": "unused"}
EXIT = [
    "ok",
    "bad header",
    "bad RSID",
    "MUS unsupported",
    "BASIC unsupported",
    "PSID extensions unsupported",
    "bad parameters",
    "I/O error",
    "no solution",
    "zero page full",
    "verification failed",
    "no play address",
    "invalid range",
    "cycles exhausted",
]
WARN = {32: "writes outside load range", 64: "pitch/pulse mismatches"}


def status(code):
    """``(error, [warnings])`` of a sidreloc exit status."""
    err = EXIT[code & 31] if (code & 31) < len(EXIT) else f"exit {code}"
    return err, [w for bit, w in WARN.items() if code & bit]


def parse_map(text):
    """``{address: role}`` over the loaded bytes of sidreloc ``-v``'s program map."""
    out = {}
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            base = int(m.group(1), 16)
            for i, c in enumerate(m.group(3)):
                if c in ROLES:
                    out[base + i] = ROLES[c]
    return out


def parse_zp(text):
    """``{old: new}`` zero-page addresses of the tune (equal when kept)."""
    rows = {}
    for m in filter(None, map(ZP.match, text.splitlines())):
        rows[m.group(1)] = [int(v, 16) for v in m.group(2).split()]
    return dict(zip(rows.get("Old", []), rows.get("New", [])))


def diff(old, new):
    """``{address: (old, new)}`` for each loaded byte the relocation changed."""
    load, o0, a = vtools.sid_body(old)
    _, o1, b = vtools.sid_body(new)
    if o0 != o1 or len(a) != len(b):
        raise ValueError("relocated image differs in shape")
    return {load + i: (x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y}


def report(old, new, stderr, pages):
    """The relocation record: roles by address, changed bytes, and their disagreements."""
    load, _, body = vtools.sid_body(old)
    roles = parse_map(stderr)
    zp = parse_zp(stderr)
    changed = diff(old, new) if new is not None else {}
    bad = set()
    for a, (x, y) in changed.items():
        r = roles.get(a)
        if not ((r == "hi" and (y - x) & 0xFF == pages & 0xFF) or (r == "zp" and zp.get(x) == y)):
            bad.add(a)
    if new is not None and pages & 0xFF:
        bad |= {a for a, r in roles.items() if r == "hi" and a not in changed}
    return {
        "load": load,
        "size": len(body),
        "pages": pages,
        "roles": {r: sorted(a for a, v in roles.items() if v == r) for r in ROLES.values()},
        "zp": {str(k): v for k, v in zp.items()},
        "changed": {str(a): list(v) for a, v in sorted(changed.items())},
        "disagree": sorted(bad),
    }


def relocate(sid, pages=1, zp=None, frames=None, image=vtools.IMAGE, timeout=900):
    """Relocate ``sid`` by ``pages`` (zero page kept unless ``zp`` is ``"lo-hi"``); the
    report, with ``error`` and ``warnings`` from the exit status."""
    sid = Path(sid).resolve()
    old = sid.read_bytes()
    load, _, _ = vtools.sid_body(old)
    page = (load >> 8) + pages
    if not 1 <= page <= 0xFF:
        raise ValueError(f"page ${page:x} out of range")
    args = ["sidreloc", "-v", "-p", f"{page:02x}"] + (["-z", zp] if zp else ["-k"])
    args += ["--frames", str(frames)] if frames else []
    tmp = vtools.workdir("sidreloc")
    try:
        mounts = [(sid.parent, "/in", True), (tmp, "/out", False)]
        r = vtools.run(
            args + [f"/in/{sid.name}", "/out/r.sid"], mounts, image=image, timeout=timeout
        )
        out = tmp / "r.sid"
        new = out.read_bytes() if out.exists() else None
    finally:
        shutil.rmtree(tmp)
    rep = report(old, new, r.stderr, pages)
    rep["error"], rep["warnings"] = status(r.returncode)
    rep["sid"] = str(sid)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tunes", nargs="+", type=Path)
    ap.add_argument("--pages", type=int, default=1, help="page offset (may be negative)")
    ap.add_argument("--zp", help="relocate zero page into LO-HI (hex), e.g. 80-ff")
    ap.add_argument("--frames", type=int, help="play calls per subtune")
    ap.add_argument("--out", type=Path, default=vtools.ROOT / "out" / "sidreloc")
    ap.add_argument("--image", default=vtools.IMAGE)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for t in tqdm(args.tunes, desc="sidreloc", unit="tune"):
        rep = relocate(t, args.pages, args.zp, args.frames, args.image)
        (args.out / f"{t.stem}.reloc.json").write_text(json.dumps(rep))
        tqdm.write(
            f"{t.name}: {rep['error']} hi={len(rep['roles']['hi'])} zp={len(rep['roles']['zp'])}"
            f" changed={len(rep['changed'])} disagree={len(rep['disagree'])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
