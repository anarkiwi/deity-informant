"""Shared fixtures for the deity_informant test suite.

``ctx6510`` compiles+installs the 6510 SLEIGH module into a scratch pypcode
processors tree and returns a loaded ``pypcode.Context`` -- the same libsla
engine Ghidra's GUI uses. It skips cleanly where pypcode or the SLEIGH build is
unavailable.
"""

import pytest

from deity_informant.sleigh import pypcode_context


@pytest.fixture(scope="session")
def ctx6510(tmp_path_factory):
    """Compile+install the 6510 module into a scratch pypcode tree, return its Context."""
    pytest.importorskip("pypcode")
    try:
        return pypcode_context(tmp_path_factory.mktemp("procs"))
    except SystemExit as e:  # pragma: no cover - environment dependent
        pytest.skip("6510 build failed: %s" % e)
