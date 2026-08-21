"""S0: image build, 6510 port banking, CIA timer/ICR, entry discovery, init runner."""

import pytest

from deity_informant import PcodeVM, lift
from deity_informant.tuneprog.machine import (
    CIA,
    CIA1_BASE,
    Entry,
    MachineImage,
    Refusal,
    find_entries,
    init_runner,
    is_idle,
    port_bank,
)

from _asm import asm, psid, sid_image


def _mem(ddr, data):
    m = bytearray(0x10000)
    m[0], m[1] = ddr, data
    return m


def test_port_bank_default_maps_io():
    assert port_bank(_mem(0x2F, 0x37)) == "io"
    assert port_bank(_mem(0x2F, 0x35)) == "io"


def test_port_bank_input_bits_read_as_pullups():
    # a player that clears the direction register to use $00/$01 as a pointer
    # leaves I/O mapped: undriven bits read 1.
    assert port_bank(_mem(0x00, 0x00)) == "io"
    assert port_bank(_mem(0x00, 0x03)) == "io"


def test_port_bank_ram_and_charrom():
    assert port_bank(_mem(0x2F, 0x30)) == "ram"  # LORAM+HIRAM low -> RAM under I/O
    assert port_bank(_mem(0x2F, 0x33)) == "charrom"  # CHAREN low -> character ROM


def test_cia_timer_counts_down_and_flags_underflow():
    c = CIA(CIA1_BASE)
    c.write(CIA1_BASE + 4, 0x98, 0)
    c.write(CIA1_BASE + 5, 0x09, 0)
    c.write(CIA1_BASE + 0x0E, 0x11, 0)  # force load + start
    assert c.latch == 0x0998
    assert c.read(CIA1_BASE + 4, 0) == 0x98
    assert c.read(CIA1_BASE + 4, 0x100) == (0x0998 - 0x100) & 0xFF
    assert c.read(CIA1_BASE + 0x0D, 0x100) == 0  # no underflow yet
    v = c.read(CIA1_BASE + 0x0D, 0x0999)
    assert v & 0x81 == 0x81  # underflowed: flag + IR bit
    assert c.read(CIA1_BASE + 0x0D, 0x0999) == 0  # read-cleared
    assert c.read(CIA1_BASE + 3, 0) is None  # unmodelled register


def test_machine_image_overlays_load_band_on_poweron_ram():
    code = asm(0x1000, "LDA #$01", "RTS")
    img = MachineImage.from_sid(psid({0x1000: code}, 0x1000, 0x1000))
    assert img.mem[0x1000 : 0x1000 + len(code)] == code
    assert (img.lo, img.hi) == (0x1000, 0x1000 + len(code))
    assert img.in_band(0x1000) and not img.in_band(0x0FFF)
    assert img.mem[0x0FFA] == 0xFF  # power-on stripe outside the band
    assert img.meta()["init"] == 0x1000


def test_find_entries_header_play():
    data = psid({0x1000: asm(0x1000, "RTS"), 0x1010: asm(0x1010, "RTS")}, 0x1000, 0x1010)
    img, sched = find_entries(data)
    assert img.play == 0x1010
    assert [e.kind for e in sched] == ["sub"]
    assert sched[0].addr == 0x1010 and sched[0].cycles_per_tick > 0


def test_find_entries_refuses_second_interrupt_source():
    pytest.importorskip("pysidtracker")
    init = asm(0x1000, "LDA #$34", "STA $DD04", "LDA #$12", "STA $DD05", "RTS")
    data = psid({0x1000: init, 0x1020: asm(0x1020, "RTS")}, 0x1000, 0x1020)
    with pytest.raises(Refusal) as e:
        find_entries(data)
    assert e.value.reason == "second interrupt source armed"


def test_find_entries_refuses_when_no_entry():
    pytest.importorskip("pysidtracker")
    data = psid({0x1000: asm(0x1000, "RTS")}, 0x1000, 0x0000)
    with pytest.raises(Refusal) as e:
        find_entries(data)
    assert e.value.reason == "no entry"


def test_find_entries_installed_handler_fallback():
    # play = 0 and the handler discovered from the caller's own post-init write set.
    mem = bytearray(0x10000)
    mem[0x0314], mem[0x0315] = 0x00, 0x20
    img, sched = find_entries(
        psid({0x1000: asm(0x1000, "RTS")}, 0x1000, 0x0000),
        mem=mem,
        written={0x0314, 0x0315},
    )
    assert img.play == 0
    assert sched[0] == Entry("irq", 0x2000, sched[0].cycles_per_tick, sched[0].source, True)
    assert sched[0].to_dict()["kernal"] is True  # CINV: the KERNAL dispatches it


def test_find_entries_hardware_vector_is_not_a_kernal_entry():
    mem = bytearray(0x10000)
    mem[0xFFFE], mem[0xFFFF] = 0x00, 0x20
    _img, sched = find_entries(
        psid({0x1000: asm(0x1000, "RTS")}, 0x1000, 0x0000),
        mem=mem,
        written={0xFFFE, 0xFFFF},
    )
    assert sched[0].kernal is False


def test_init_runner_returns_on_rts_and_detects_idle():
    img = sid_image({0x1000: asm(0x1000, "LDA #$05", "STA $D400", "RTS")}, 0x1000, 0x1000)
    vm = PcodeVM(img.mem)
    assert init_runner(vm, 0x1000, {}, lift) is None
    assert vm.mem[0xD400] == 0x05

    idle = sid_image({0x1000: asm(0x1000, "LDA #$06", "STA $D400", "JMP $1005")}, 0x1000, 0x1000)
    vm2 = PcodeVM(idle.mem)
    assert init_runner(vm2, 0x1000, {}, lift) == 0x1005
    assert is_idle(vm2.mem, 0x1005)


def test_init_runner_refuses_runaway():
    img = sid_image({0x1000: asm(0x1000, "NOP", "JMP $1000")}, 0x1000, 0x1000)
    vm = PcodeVM(img.mem)
    with pytest.raises(Refusal) as e:
        init_runner(vm, 0x1000, {}, lift, budget=500)
    assert e.value.reason == "init runaway"


def test_cia_registers_mirror_every_sixteen_bytes():
    c = CIA(CIA1_BASE)
    c.write(CIA1_BASE + 0x14, 0x40, 0)  # $DC14 mirrors $DC04
    c.write(CIA1_BASE + 0x15, 0x00, 0)
    c.write(CIA1_BASE + 0x1E, 0x11, 0)  # $DC1E mirrors $DC0E
    assert c.latch == 0x0040 and c.running
    assert c.read(CIA1_BASE + 0x14, 0x10) == c.read(CIA1_BASE + 4, 0x10) == 0x30
    assert c.read(CIA1_BASE + 0x100, 0) is None


def test_cia_latch_rewrite_keeps_underflows_monotone():
    c = CIA(CIA1_BASE)
    c.write(CIA1_BASE + 4, 0x10, 0)
    c.write(CIA1_BASE + 5, 0x00, 0)
    c.write(CIA1_BASE + 0x0E, 0x11, 0)
    assert c.read(CIA1_BASE + 0x0D, 0x100) & 1  # underflowed
    c.write(CIA1_BASE + 5, 0x10, 0x100)  # a much longer period, mid-flight
    assert c.read(CIA1_BASE + 0x0D, 0x120) == 0  # not due yet, and not wedged
    assert c.read(CIA1_BASE + 0x0D, 0x1200) & 1  # still fires later
