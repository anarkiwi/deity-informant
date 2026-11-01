"""deity_informant.c64: the bank map, and facts and records from the native C64 machine."""

import hashlib
import os
import socket
from urllib.parse import urlparse

import numpy as np
import pytest

pytest.importorskip("vagrant_moses")

# pylint: disable=wrong-import-position
from vagrant_moses import machine as vm  # noqa: E402
from vagrant_moses.bus import port_read  # noqa: E402
from vagrant_moses.c64 import poweron_ram  # noqa: E402
from vagrant_moses.roms import DEFAULT_ROM_MIRROR, ROMS, RomFetchError, rom_less  # noqa: E402

from deity_informant import c64, trace  # noqa: E402
from deity_informant.smc.machine import core  # noqa: E402

native = pytest.mark.skipif(not core(), reason="needs vagrant-moses' native machine")

PROGRAM = {
    0x1000: "a9358501ad00e08d00d448",  # LDA #$35; STA $01; LDA $E000; STA $D400; PHA
    0x100B: "a9378501688d800120201060",  # LDA #$37; STA $01; PLA; STA $0180; JSR $1020; RTS
    0x1020: "ad810160",  # LDA $0181; RTS
    0x1100: "a9018d00c060",  # LDA #1; STA $C000; RTS
    0x1200: "4c0012",  # JMP $1200
    0x1300: "02",  # JAM
    0x1400: "a9078d18d4ad18d4ae38d460",  # LDA #7; STA $D418; LDA $D418; LDX $D438; RTS
    0x1500: "ad00e08d00c060",  # LDA $E000; STA $C000; RTS
}


def image():
    img = bytearray(poweron_ram())
    for addr, code in PROGRAM.items():
        b = bytes.fromhex(code)
        img[addr : addr + len(b)] = b
    img[0xE000], img[0xD438] = 0x42, 0x99
    return img


@pytest.fixture(scope="session", name="real")
def fixture_real():
    """The verified real ROMs; skips only when their mirror is unreachable."""
    try:
        return c64.real_roms()
    except RomFetchError as e:
        host = urlparse(os.environ.get("C64_ROM_MIRROR", DEFAULT_ROM_MIRROR)).hostname
        try:
            socket.getaddrinfo(host, 443)
        except OSError:
            pytest.skip(f"real ROMs unavailable offline ({e})")
        raise


def test_port_read_model():
    d, v = (a.ravel() for a in np.meshgrid(np.arange(256), np.arange(256), indexing="ij"))
    want = np.array([port_read(int(p), int(q)) for p, q in zip(d, v)])
    assert np.array_equal(((v & d) | (~d & c64.PULL)) & 0xFF, want)
    assert c64.PULL == 0x1F


@native
def test_effective_bits_model():
    m = vm.Machine(False, sids={}, backend="native")
    bm = c64.bank_map()
    for d in range(256):
        m[0] = d
        for v in range(256):
            m[1] = v
            assert bm["lut"][(v | ~d) & bm["mask"]] == m.bits


def test_bank_map_configs():
    bm = c64.bank_map()
    assert (bm["dir"], bm["data"], bm["mask"], bm["lut"]) == (0, 1, 7, list(range(8)))
    assert all(len(c) == 256 for c in bm["configs"])
    ram, rom, io = c64.RAM, c64.ROM, c64.IO
    for b, (basic, char_io, kernal) in {
        7: (rom, io, rom), 6: (ram, io, rom), 5: (ram, io, ram), 4: (ram, ram, ram),
        3: (rom, rom, rom), 2: (ram, rom, rom), 1: (ram, rom, ram), 0: (ram, ram, ram),
    }.items():  # fmt: skip
        p = np.frombuffer(bm["configs"][b], np.uint8)
        assert set(p[0xA0:0xC0]) == {basic} and set(p[0xD0:0xE0]) == {char_io}
        assert set(p[0xE0:]) == {kernal} and set(p[:0xA0]) == set(p[0xC0:0xD0]) == {ram}
    for base, data in rom_less().items():
        assert bm["rom"][base : base + len(data)] == data


def test_bank_map_real(real):
    bm = c64.bank_map(real)
    for name in c64.ROM_NAMES:
        spec = ROMS[name]
        rom = bm["rom"][spec.base : spec.base + spec.size]
        assert hashlib.sha256(rom).hexdigest() == spec.sha256
    assert bm["rom"] != c64.bank_map()["rom"]


def test_facts_banks_roundtrip():
    f = trace.Facts(
        starts=[1], banks={0x1000: [5, 7]}, entry_port={0x1000: [[0x2F, 0x36], [0x2F, 0x37]]}
    )
    g = trace.Facts.loads(f.dumps())
    assert g == f and g.to_dict()["entry_port"] == {0x1000: [[0x2F, 0x36], [0x2F, 0x37]]}
    assert trace.Facts.from_dict({}).banks == {}


@native
def test_machine_facts():
    f = c64.machine_facts(image(), [(0x1000, 0)] * 2, init=[(0x1100, 0)], ports={"call": 0x36})
    starts = [0x1000, 0x1002, 0x1004, 0x1007, 0x100A, 0x100B, 0x100D, 0x100F, 0x1010, 0x1013]
    assert f.starts == starts + [0x1016, 0x1020, 0x1023]
    assert (0x1013, 0x1020) in f.edges and (0x1023, 0x1016) in f.edges
    assert len(f.edges) == len(f.starts) - 1
    assert f.entry_sp == {0x1000: [0xFD]} and f.entry_port == {0x1000: [[0x2F, 0x36]]}
    assert f.banks[0x1000] == [6] and f.banks[0x1004] == [5] and f.banks[0x100F] == [7]
    assert {p: h for p, h in f.heights.items() if h != [0]} == {
        0x100B: [-1], 0x100D: [-1], 0x100F: [-1], 0x1020: [-2], 0x1023: [-2]}  # fmt: skip
    assert f.page1 == {0x1010: [0x180, 0x180], 0x1020: [0x181, 0x181]}
    assert f.writes == {
        0x0001: [0x35, 0x37], 0x0180: [0x42], 0x01FC: [0x15], 0x01FD: [0x10, 0x42],
        0xD400: [0x42],
    }  # fmt: skip


@native
def test_machine_session():
    run = ([(0x1000, 0)] * 2, [(0x1100, 0)], {"init": 0x37, "call": 0x36})
    inits, calls = c64.machine_session(image(), *run)
    assert [r["halt"] for r in inits + calls] == ["return"] * 3
    assert inits[0]["log"] == [] and inits[0]["ram"][0xC000] == 1
    assert inits[0]["reg"][:4] == [1, 0, 0, 0xFF]
    for r in calls:
        assert r["log"] == [(0xD400, 0x42, True)]
        assert r["reg"][:4] == [0, 0, 0, 0xFF] and r["reg"][9] == 1 and len(r["reg"]) == 16
        assert r["ram"][:2] == bytes([0x2F, 0x37]) and r["ram"][0x180] == 0x42
    assert c64.machine_record(image(), *run) == calls


@native
def test_machine_record_halts_and_reads():
    img = image()
    assert c64.machine_record(img, [(0x1200, 0)], max_cycles=1000)[0]["halt"] == "cycles"
    assert [r["halt"] for r in c64.machine_record(img, [(0x1300, 0), (0x1000, 0)])] == ["jam"]
    (r,) = c64.machine_record(img, [(0x1400, 0)], ports={"call": 0x35})
    assert r["log"] == [(0xD418, 7, True), (0xD418, 7, False), (0xD438, 0x99, False)]
    assert r["reg"][:2] == [7, 0x99]


@native
def test_machine_real_roms(real):
    img = image()
    (r,) = c64.machine_record(img, [(0x1500, 0)], ports={"call": 0x37}, roms=real)
    (s,) = c64.machine_record(img, [(0x1500, 0)], ports={"call": 0x37})
    assert r["ram"][0xC000] == real[0xE000][0] != s["ram"][0xC000] == rom_less()[0xE000][0]
