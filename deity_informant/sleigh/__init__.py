"""The ``6510`` Ghidra/pypcode SLEIGH module: the ``languages/`` sources, the
:mod:`.smc` context constructors, and :mod:`.build` to compile and install them."""

from __future__ import annotations

import shutil
from pathlib import Path

from .build import LANGDIR, main as build_main

LANGUAGE = "6510:LE:16:default"


def pypcode_context(procs, magic=None):
    """Build the module into a pypcode processors tree at ``procs``, return its Context.

    The tree is pypcode's bundled processors plus ``6510``; ``pypcode.SPECFILES_DIR``
    is pointed at it. Raises ``SystemExit`` where the SLEIGH build fails.
    """
    import pypcode  # pylint: disable=import-outside-toplevel

    procs = Path(procs)
    langdir = procs / "6510" / "data" / "languages"
    build_main(["--install", str(langdir)] + (["--magic", magic] if magic else []))
    for p in (Path(pypcode.__file__).parent / "processors").iterdir():
        if p.is_dir():
            shutil.copytree(p, procs / p.name, dirs_exist_ok=True)
    pypcode.SPECFILES_DIR = str(procs)
    return pypcode.Context(LANGUAGE)
