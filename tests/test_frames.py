"""deity_informant.frames: interrupt frames recorded on the machine, lifted and checked."""

import struct
import sys
from pathlib import Path

import numpy as np
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


# the interrupted code counts in $0300 beside the KERNAL-path handler at $1020
BUSY = bytes.fromhex("78a9208d1403a9108d150358" "ee00034c0c10").ljust(0x20, b"\xea")
BUSY += bytes.fromhex("ad0ddc8d00d468a868aa6840")

# init: KERNAL out, IRQ ($FFFE) at $1040 and NMI ($FFFA) at $1060, CIA 2 timer A NMI every 100
# cycles, idle. IRQ handler: ack, 40 loops writing SID $D404; NMI handler: INC $0200 beside
# LDA $DD0D, STA $D418 (A saved and restored)
NESTED = bytes.fromhex(
    "78a935 8501 a940 8dfeff a910 8dffff a960 8dfaff a910 8dfbff"
    "a964 8d04dd a900 8d05dd a981 8d0ddd a901 8d0edd 58 4c1e10"
).ljust(0x40, b"\xea")
NESTED += bytes.fromhex("ad0ddc a228 8e04d4 ca d0fa 40").ljust(0x20, b"\xea")
NESTED += bytes.fromhex("48 ad0ddd ee0002 8d18d4 68 40")


# as NESTED, but the IRQ handler counts in $0300 twice between device accesses and the NMI
# handler writes $0300 to $D418: an NMI between the two INCs sees a state no device access marks
RACE = NESTED[:0x40] + bytes.fromhex("ad0ddc a228 ee0003 ee0003 8e04d4 ca d0f4 40").ljust(
    0x20, b"\xea"
)
RACE += bytes.fromhex("48 ad0ddd ad0003 8d18d4 68 40")


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
    assert len(host.stream) >= 40 and not host.foreign and not host.ninner
    assert set(host.frames["at"].tolist()) == {at} and set(host.frames["pc"].tolist()) <= pcs
    prog = frames.lift(host, boot.roms)
    assert prog.entries == [at] and frames.check(prog, host) is None
    assert host.stream.halt == "return" and prog.stats["stack_private"] and host.stop == "none"
    t_ref, t_lift, cycles, n, hit = frames.timed(prog, host, c64.bank_map(boot.roms))
    assert t_ref > 0 and t_lift > 0 and cycles > 0 and n >= 40 and hit == 0


def test_mismatch():
    boot, host = run(HARDWARE, 5)
    host.image = host.image[:0x1035] + b"\x03" + host.image[0x1036:]
    bad = frames.check(frames.lift(host, boot.roms), host)
    assert bad[0] == 0 and bad[1] == "memory"


def test_frame_budget_stops_the_run():
    boot = boot_of(HARDWARE)
    host = frames.FrameHost(boot.image, roms=boot.roms).drive_frames(boot, 40, 15)
    assert host.stop == "unit_cycles" and len(host.stream) < 5


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


def test_interrupted_code_that_works_is_dirty():
    assert not run(KERNAL)[1].dirty
    assert run(BUSY)[1].dirty


def test_nested_interrupt_taken_inside_frame():
    boot, host = run(NESTED, 30)
    assert len(host.stream) >= 30 and not host.foreign and not host.dirty
    k = int(host.inner["record"].max())
    ev = host.inside(k)
    assert host.sources(k) == ["nmi"] and set(ev["at"].tolist()) == {0x1060}
    assert ev["pos"].tolist() == sorted(ev["pos"]) and ev["icount"].tolist() == sorted(ev["icount"])
    prog = frames.lift(host, boot.roms)
    assert frames.check(prog, host) is None
    kw = {"ret": host.sentinel - 1, "irq": True, "log": True}
    entry = prog.entries.index(int(host.frames[k]["at"]))
    reg, reads = frames.regs(host.frames[k]), c64.read_entries(host.stream[k])
    with_nmi = prog.call(bytearray(host.image), entry, reg=reg, reads=reads, **kw,
                         nested=frames.nested(prog, host.inside(k)))  # fmt: skip
    without = prog.call(bytearray(host.image), entry, reg=reg, reads=reads, **kw)
    assert with_nmi["boundary"] is None
    assert [e[:2] for e in with_nmi["log"]] == [tuple(e[:2]) for e in host.stream[k]["log"]]
    assert len(without["log"]) == len(host.stream[k]["log"]) - 2 * len(ev)


def test_interrupt_between_two_device_accesses_is_exact():
    boot, host = run(RACE, 30)
    prog = frames.lift(host, boot.roms)
    assert host.ninner and frames.check(prog, host) is None
    other = bytearray(host.image)
    other[0x1060 + 4] = 0x01
    host.image, image = bytes(other), host.image
    changed = frames.lift(host, boot.roms)
    host.image = image
    assert c64.outcome(frames.check(changed, host)) == "mismatch"


def test_an_interrupt_the_lift_would_move_is_taken_at_the_machines_boundary():
    # IRQ: 40 times LDA $0300; STX $D404; CMP $0300; STX $D405 only when it changed. NMI: INC $0300
    irq = bytes.fromhex("ad0ddc a228 ad0003 8e04d4 cd0003 f003 8e05d4 ca d0ef 40")
    nmi = bytes.fromhex("48 ad0ddd ee0003 68 40")
    boot, host = run((NESTED[:0x40] + irq).ljust(0x60, b"\xea") + nmi, 30)
    assert host.ninner and frames.check(frames.lift(host, boot.roms), host) is None


def test_a_boundary_the_machine_did_not_have_is_reported():
    boot, host = run(NESTED, 30)
    prog = frames.lift(host, boot.roms)
    moved = []
    for chunk in host.nested_log:
        ev = np.frombuffer(chunk, c64.EVENT).copy()
        ev["icount"] += 1
        moved.append(ev.tobytes())
    host.nested_log = c64.Spill()
    for chunk in moved:
        host.nested_log.append(chunk)
    bad = frames.check(prog, host)
    assert bad is not None and bad[1] in ("boundary", "log", "memory", "reg")


def test_timed_frames_with_interrupts():
    boot, host = run(NESTED, 30)
    prog = frames.lift(host, boot.roms)
    *_, n, hit = frames.timed(prog, host, c64.bank_map(boot.roms))
    assert n > 100 and hit > 0


def test_nested_interrupts_agree_across_backends_and_reference():
    boot, host = run(NESTED, 30)
    prog = frames.lift(host, boot.roms)
    k = int(host.inner["record"].max())
    rec, e = host.stream[k], host.frames[k]
    kw = {"a": int(e["a"]), "reg": frames.regs(e), "log": True, "reads": c64.read_entries(rec)}
    kw |= {"irq": True, "ret": host.sentinel - 1}
    entry = prog.entries.index(int(e["at"]))
    outs = []
    for backend in ("jit", "interp"):
        mem = bytearray(host.image)
        outs.append(
            prog.call(mem, entry, backend=backend, nested=frames.nested(prog, host.inside(k)), **kw)
        )
        outs[-1]["ram"] = mem
    mem = bytearray(host.image)
    ref = wholeprog.reference_call(mem, int(e["at"]), volatile=[c64.DEVICES], nested=frames.nested(None, host.inside(k)),
                                   bank=c64.bank_map(boot.roms), **kw)  # fmt: skip
    ref["ram"] = mem
    assert all(o["halt"] == "return" and o["boundary"] is None for o in outs + [ref])
    assert outs[0]["log"] == outs[1]["log"] and outs[0]["ram"] == outs[1]["ram"] == ref["ram"]
    assert [x[:2] for x in outs[0]["log"]] == [x[:2] for x in ref["log"]]
    assert len(outs[0]["log"]) == len(rec["log"])


def test_a_handler_returning_to_the_call_with_its_frame_on_the_stack_ends_the_call():
    img = bytearray(0x10000)
    # INC $0201; LDA #$12; PHA; LDA #$33; PHA; RTS: returns to $1234 below the entry's frame
    img[0x1000:0x100A] = bytes.fromhex("ee0102 a912 48 a933 48 60")
    img[0x1010] = 0x40  # the interrupt after the INC: RTI
    prog = wholeprog.lift(bytes(img), [0x1000, 0x1010], [])
    reg = [5, 6, 7, 0xF0] + [0] * 12
    reg[8] = 1
    inner = [*reg[:3], 0xED] + reg[4:]
    inner[10] = 1
    kw = {"a": 5, "reg": reg, "ret": 0x1233, "irq": True, "nested": [(1, 0, 1, 0, inner, 0)]}
    outs = []
    for backend in ("jit", "interp"):
        mem = bytearray(img)
        outs.append(prog.call(mem, 0, backend=backend, **kw) | {"ram": mem})
    kw["nested"] = [(1, 0, 0x1010, 0, inner, 0)]
    ref = bytearray(img)
    outs.append(wholeprog.reference_call(ref, 0x1000, **kw) | {"ram": ref})
    for o in outs:
        assert o["halt"] == "return" and o["pc"] == 0x1234 and o["boundary"] is None
        assert o["ram"][0x201] == 1


def test_recorded_frames_spill_beside_the_stream_and_the_machine_forgets_its_events():
    boot = boot_of(NESTED)
    whole = frames.FrameHost(boot.image, roms=boot.roms).drive_frames(boot, 30)
    host = frames.FrameHost(boot.image, roms=boot.roms, spill=1).drive_frames(boot, 30)
    for log in (host.entry_log, host.nested_log, host.stream.spill):
        assert log.frames and not log.held
    assert not list(host.m.events)
    assert host.ninner == whole.ninner > 0
    assert (host.frames == whole.frames).all() and (host.inner == whole.inner).all()
    assert frames.check(frames.lift(host, boot.roms), host) is None
    assert sum(len(ev) for _, _, _, ev in host.chunks()) == host.ninner
