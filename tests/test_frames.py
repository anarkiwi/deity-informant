"""deity_informant.frames: interrupt frames recorded on the machine, lifted and checked."""

import struct
import sys
from pathlib import Path

import pytest

from deity_informant import NATIVE, c64, frames, wholeprog
from deity_informant.smc.machine import core

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(
    not (NATIVE and core() and c64.TRACE_NATIVE), reason="needs the native machine and trace"
)

# init points $0314 at $1010: LDA $DC0D; STA $D400; PLA; TAY; PLA; TAX; PLA; RTI
KERNAL = bytes.fromhex("78a9108d1403a9108d150358" "60eaeaea" "ad0ddc8d00d468a868aa6840")
# init banks the KERNAL out, points RAM $FFFE at $1030 and idles; handler: LDA $DC0D; INC $0200
HARDWARE = bytes.fromhex("78a9358501a9308dfeffa9108dffff58" "4c0f10").ljust(0x30, b"\xea")
HARDWARE += bytes.fromhex("ad0ddcee000240")


def boot_of(code):
    hdr = b"PSID" + struct.pack(">HHHHHHHI", 2, 0x7C, 0x1000, 0x1000, 0, 1, 1, 1)
    hdr += bytes(0x60) + struct.pack(">HBBBB", 0x14, 0, 0, 0, 0)
    img = wpcorpus.sidfile(hdr + code)[1]
    return c64.boot(img, 0x1000, 0x1000 + len(code), 0x1000, 1, kind="PSID")


def run(code, n=40):
    boot = boot_of(code)
    return boot, frames.FrameHost(boot.image, roms=boot.roms).drive_frames(boot, n)


@pytest.mark.parametrize(
    "code,at,pcs", [(KERNAL, 0xFF48, {c64.SENTINEL}), (HARDWARE, 0x1030, {0x100F, 0x1010})]
)
def test_frames_lift_exact(code, at, pcs):
    boot, host = run(code)
    assert len(host.stream) >= 40 and not host.foreign and not host.nested
    assert set(host.frames["at"].tolist()) == {at} and set(host.frames["pc"].tolist()) <= pcs
    prog = frames.lift(host, boot.roms)
    assert prog.entries == [at] and frames.check(prog, host) is None
    assert host.stream.halt == "return" and prog.stats["stack_private"]
    t_ref, t_lift, cycles = frames.timed(prog, host, c64.bank_map(boot.roms))
    assert t_ref > 0 and t_lift > 0 and cycles > 0


def test_mismatch_and_nested_interrupt():
    boot, host = run(HARDWARE, 5)
    host.image = host.image[:0x1035] + b"\x03" + host.image[0x1036:]
    prog = frames.lift(host, boot.roms)
    bad = frames.check(prog, host)
    assert bad[0] == 0 and bad[1] == "memory"
    host.nested[0] = ["nmi"]
    assert frames.check(prog, host) == [0, "interrupt", "nmi"]


def test_irq_call_matches_reference():
    code = bytes.fromhex("ee000240")  # INC $0200; RTI
    img = bytearray(0x10000)
    img[0x1000:0x1004] = code
    prog = wholeprog.lift(bytes(img), [0x1000], [])
    reg = [5, 6, 7, 0xF0] + [0] * 12
    reg[8] = reg[14] = 1
    for backend in ("jit", "interp"):
        mem, ref = bytearray(img), bytearray(img)
        kw = {"a": 5, "reg": reg, "ret": 0x1233, "irq": True}
        o = prog.call(mem, 0, backend=backend, **kw)
        r = wholeprog.reference_call(ref, 0x1000, **kw)
        assert o["halt"] == r["halt"] == "return" and o["pc"] == r["pc"] == 0x1234
        assert o["reg"] == r["reg"] and mem == ref and mem[0x200] == 1
        assert bytes(mem[0x1EE:0x1F1]) == bytes([0xA1, 0x34, 0x12])
