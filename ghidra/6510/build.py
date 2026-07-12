#!/usr/bin/env python3
"""Compile the 6510 SLEIGH module (stock 6502 legal set + our illegal .sinc).

Resolves the stock ``6502.slaspec`` from a Ghidra install (``$GHIDRA_INSTALL_DIR``)
or a pypcode install, copies it beside ``6510.slaspec`` (it is Ghidra's,
Apache-2.0, and deliberately not committed here), then runs the SLEIGH compiler
to emit ``6510.sla``.

Usage:
    python build.py [--sleigh PATH] [--magic 0xEE] [--install DIR]

``--install DIR`` also copies the finished module (``6510.*`` + ``6510.sla``)
into ``DIR`` so Ghidra or pypcode can discover the ``6510:LE:16:default``
language (e.g. ``$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages`` or
``<pypcode>/processors/6510/data/languages``).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANGDIR = HERE / "data" / "languages"


def find_base_slaspec() -> Path:
    """Locate the stock 6502.slaspec from a Ghidra or pypcode install."""
    ghidra = os.environ.get("GHIDRA_INSTALL_DIR")
    if ghidra:
        p = Path(ghidra) / "Ghidra/Processors/6502/data/languages/6502.slaspec"
        if p.is_file():
            return p
    try:
        import pypcode  # noqa: PLC0415

        p = Path(pypcode.__file__).parent / "processors/6502/data/languages/6502.slaspec"
        if p.is_file():
            return p
    except ImportError:
        pass
    raise SystemExit(
        "cannot find stock 6502.slaspec: set GHIDRA_INSTALL_DIR or `pip install pypcode`"
    )


def find_sleigh() -> str:
    """Locate the SLEIGH compiler (Ghidra's or pypcode's bundled binary)."""
    ghidra = os.environ.get("GHIDRA_INSTALL_DIR")
    if ghidra:
        p = Path(ghidra) / "support/sleigh"
        if p.is_file():
            return str(p)
    try:
        import pypcode  # noqa: PLC0415

        p = Path(pypcode.__file__).parent / "bin/sleigh"
        if p.is_file():
            return str(p)
    except ImportError:
        pass
    found = shutil.which("sleigh")
    if found:
        return found
    raise SystemExit("cannot find the SLEIGH compiler: set GHIDRA_INSTALL_DIR")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sleigh", help="path to the SLEIGH compiler")
    ap.add_argument("--magic", help="override the ANE/LXA magic constant, e.g. 0x00")
    ap.add_argument("--install", help="also copy the built module into this languages dir")
    args = ap.parse_args(argv)

    base = find_base_slaspec()
    sleigh = args.sleigh or find_sleigh()
    shutil.copy(base, LANGDIR / "6502.slaspec")  # build artifact; not committed

    cmd = [sleigh]
    if args.magic:
        cmd.append(f"-D MAGIC={args.magic}")
    cmd += ["6510.slaspec", "6510.sla"]
    print("compiling:", " ".join(cmd))
    r = subprocess.run(cmd, cwd=LANGDIR, capture_output=True, text=True, check=False)
    errs = [ln for ln in (r.stdout + r.stderr).splitlines() if "ERROR" in ln]
    if r.returncode != 0 or errs:
        sys.stderr.write("\n".join(errs) or (r.stdout + r.stderr))
        raise SystemExit(f"\nSLEIGH compile failed (exit {r.returncode})")
    sla = LANGDIR / "6510.sla"
    print(f"built {sla} ({sla.stat().st_size} bytes)")

    if args.install:
        dst = Path(args.install)
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("6510.sla", "6510.ldefs", "6510.pspec", "6510.cspec"):
            shutil.copy(LANGDIR / name, dst / name)
        # Ghidra (unlike pypcode) compiles the .slaspec at load time and validates
        # the .sla against it, so the SLEIGH sources must be installed too -- ours
        # plus the stock 6502 @include sources. Copy .slaspec/.sinc only, never the
        # stock .ldefs (that would register a duplicate 6502 language).
        for name in ("6510.slaspec", "6510_illegal.sinc"):
            shutil.copy(LANGDIR / name, dst / name)
        for src in list(base.parent.glob("*.slaspec")) + list(base.parent.glob("*.sinc")):
            shutil.copy(src, dst / src.name)
        # Ghidra discovers a Processor module only via a Module.manifest at the
        # module root (`.../<name>/data/languages` -> root is two levels up); without
        # it analyzeHeadless reports "Unsupported language". pypcode ignores it.
        if dst.parts[-2:] == ("data", "languages"):
            (dst.parents[1] / "Module.manifest").touch()
        print(f"installed 6510:LE:16:default into {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
