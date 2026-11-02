"""Shared fixtures for the deity_informant test suite.

``ctx6510`` compiles+installs the 6510 SLEIGH module into a scratch pypcode
processors tree and returns a loaded ``pypcode.Context`` -- the same libsla
engine Ghidra's GUI uses. It skips cleanly where pypcode or the SLEIGH build is
unavailable.
"""

import os
import socket
from urllib.parse import urlparse

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


@pytest.fixture(scope="session", name="real")
def fixture_real():
    """The verified real ROMs and booted-KERNAL image; skips only when their mirror is
    unreachable."""
    roms = pytest.importorskip("vagrant_moses.roms")
    from deity_informant import c64  # pylint: disable=import-outside-toplevel

    try:
        if roms.poweron_writes() is None:
            raise roms.RomFetchError("poweron: unavailable")
        return c64.real_roms()
    except roms.RomFetchError as e:
        host = urlparse(os.environ.get("C64_ROM_MIRROR", roms.DEFAULT_ROM_MIRROR)).hostname
        try:
            socket.getaddrinfo(host, 443)
        except OSError:
            pytest.skip(f"real ROMs unavailable offline ({e})")
        raise
