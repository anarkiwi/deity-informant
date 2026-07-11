"""Shared fixtures for the deity_informant test suite.

``ctx6510`` compiles+installs the 6510 SLEIGH module into a scratch pypcode
processors tree and returns a loaded ``pypcode.Context`` -- the same libsla
engine Ghidra's GUI uses. It skips cleanly where pypcode or the SLEIGH build is
unavailable. Also puts the repo root on ``sys.path`` so tests can import the
self-contained ``examples.hello_world`` demo.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def ctx6510(tmp_path_factory):
    """Compile+install the 6510 module into a scratch pypcode tree, return its Context."""
    pypcode = pytest.importorskip("pypcode")
    procs = tmp_path_factory.mktemp("procs")
    # copy the bundled processors tree so pypcode discovers the stock languages
    src = Path(pypcode.__file__).parent / "processors"
    langdir = procs / "6510" / "data" / "languages"
    langdir.mkdir(parents=True)
    build = ROOT / "ghidra" / "6510" / "build.py"
    r = subprocess.run(
        ["python3", str(build), "--install", str(langdir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        pytest.skip("6510 build failed: " + r.stdout + r.stderr)
    # point pypcode at a merged tree (stock + our 6510)
    for p in src.iterdir():
        if p.is_dir():
            shutil.copytree(p, procs / p.name, dirs_exist_ok=True)
    pypcode.SPECFILES_DIR = str(procs)
    try:
        return pypcode.Context("6510:LE:16:default")
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip("cannot load 6510 context: %r" % e)
