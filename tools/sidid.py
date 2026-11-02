#!/usr/bin/env python3
"""Player names of SID files by Cadaver's SIDId (``sidid.cfg`` signatures), on ``di-vtools``.

``python tools/sidid.py PATH... [--json OUT]``: each PATH is a file or a directory searched
for ``*.sid``; prints ``path<TAB>player[,player...]`` (empty when unidentified).  The files'
common parent is mounted read-only, so it must be a host path the Docker proxy allows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import vtools  # noqa: E402

NAME = re.compile(r"^(\d{6})\.sid\s+(\S+)\s*$")
MORE = re.compile(r"^\s+(\S+)\s*$")
UNKNOWN = "*Unidentified*"


def parse(lines):
    """``{index: [players]}`` from ``sidid -m -u`` output over the ``NNNNNN.sid`` staging names;
    a match line without a name continues the previous file."""
    out, cur = {}, None
    for line in lines:
        if line.startswith(("Detected players:", "Statistics:")):
            break
        m = NAME.match(line)
        if m:
            cur = int(m.group(1))
            out.setdefault(cur, [])
            if m.group(2) != UNKNOWN:
                out[cur].append(m.group(2))
            continue
        m = MORE.match(line)
        if m and cur is not None and m.group(1) != UNKNOWN:
            out[cur].append(m.group(1))
    return out


def expand(paths):
    """The ``.sid`` files named by ``paths`` (directories searched recursively), sorted."""
    out = []
    for p in map(Path, paths):
        out += sorted(p.rglob("*.sid")) if p.is_dir() else [p]
    return [p.resolve() for p in out]


def identify(paths, image=vtools.IMAGE, timeout=3600, progress=True):
    """``{path: [players]}`` for ``paths``, all matching players (first is SIDId's own pick)."""
    paths = [Path(p).resolve() for p in paths]
    if not paths:
        return {}
    root = Path(os.path.commonpath([p.parent for p in paths]))
    cmd = vtools.docker_cmd(["sidid-list"], [(root, "/in", True)], image=image, interactive=True)
    stdin = "".join(f"{p.relative_to(root)}\n" for p in paths)
    lines = []
    with (
        subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="latin-1"
        ) as proc,
        tqdm(total=len(paths), desc="sidid", unit="file", disable=not progress) as pbar,
    ):
        proc.stdin.write(stdin)
        proc.stdin.close()
        for line in proc.stdout:
            lines.append(line)
            pbar.update(bool(NAME.match(line)))
        if proc.wait(timeout=timeout):
            raise RuntimeError(f"sidid-list exited {proc.returncode}")
    found = parse(lines)
    return {p: found.get(i, []) for i, p in enumerate(paths)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help=".sid files or directories")
    ap.add_argument("--image", default=vtools.IMAGE)
    ap.add_argument("--json", type=Path, help="also write {path: [players]} here")
    args = ap.parse_args(argv)
    res = identify(expand(args.paths), args.image)
    for p, names in res.items():
        print(f"{p}\t{','.join(names)}")
    if args.json:
        args.json.write_text(json.dumps({str(k): v for k, v in res.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
