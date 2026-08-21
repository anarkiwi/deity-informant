"""S0: image build, 6510 port banking, CIA timer/ICR, entry discovery, init runner."""

import pytest

from deity_informant import PcodeVM, lift
from deity_informant.tuneprog import machine
from deity_informant.tuneprog.machine import (
    CIA,
    CIA1_BASE,
    Entry,
    MachineImage,
    Refusal,
    find_entries,
    frame_slots,
    init_runner,
    is_idle,
    kernal_mapped,
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


def _gate(written, port=0x37, vectors=()):
    """``find_entries`` over a post-init machine with ``written`` vectors and ``$01`` port."""
    mem = bytearray(0x10000)
    mem[0], mem[1] = 0x2F, port
    for addr, val in vectors:
        mem[addr], mem[addr + 1] = val & 0xFF, val >> 8
    return find_entries(
        psid({0x1000: asm(0x1000, "RTS")}, 0x1000, 0x0000), mem=mem, written=set(written)
    )


def test_a_raw_vector_needs_the_kernal_banked_out():
    """With the ROM mapped the 6510 reads the KERNAL's own ``$FFFE``, not the tune's."""
    _img, sched = _gate({0xFFFE, 0xFFFF}, port=0x35, vectors=[(0xFFFE, 0x2000)])
    assert sched[0].addr == 0x2000 and sched[0].kernal is False
    assert frame_slots(sched[0].to_dict()) == {1: machine.STATUS}
    with pytest.raises(Refusal) as e:
        _gate({0xFFFE, 0xFFFF}, vectors=[(0xFFFE, 0x2000)])
    assert e.value.reason == "vector banked out"


def test_both_vectors_written_are_decided_by_the_port():
    """Only one of them is live: the dead one's write went under the ROM, or over it."""
    both = {0x0314, 0x0315, 0xFFFE, 0xFFFF}
    vectors = [(0x0314, 0x2000), (0xFFFE, 0x3000)]
    _img, mapped = _gate(both, vectors=vectors)
    assert (mapped[0].addr, mapped[0].kernal) == (0x2000, True)
    _img, out = _gate(both, port=0x35, vectors=vectors)
    assert (out[0].addr, out[0].kernal) == (0x3000, False)


def test_cinv_with_the_kernal_banked_out_refuses():
    """Fail closed: the port forbids the only dispatch the tune armed."""
    with pytest.raises(Refusal) as e:
        _gate({0x0314, 0x0315}, port=0x35, vectors=[(0x0314, 0x2000)])
    assert e.value.reason == "vector banked out" and "$FFFE" in e.value.detail


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


def test_kernal_mapped_is_the_ports_hiram_line():
    """HIRAM alone decides ``$E000-$FFFF``; a line held as input reads as 1."""
    mem = bytearray(2)
    mem[0], mem[1] = 0x2F, 0x37
    assert kernal_mapped(mem) and port_bank(mem) == "io"
    mem[1] = 0x35  # HIRAM clear, LORAM and CHAREN set: I/O mapped, no KERNAL
    assert not kernal_mapped(mem) and port_bank(mem) == "io"
    mem[0] = 0x00  # every line an input: the port's pull-ups
    assert kernal_mapped(mem)


PAL_HOST, NTSC_HOST = 0x4025 + 1, 0x4295 + 1


def _cadence(speed=0, songs=1, song=None, init=("RTS",), magic=b"PSID", clock=1):
    """``(cycles_per_tick, source)`` of a synthetic tune's play entry."""
    pytest.importorskip("pysidtracker")
    data = psid(
        {0x1000: asm(0x1000, *init), 0x1100: asm(0x1100, "RTS")},
        0x1000,
        0x1100,
        speed=speed,
        songs=songs,
        magic=magic,
        clock=clock,
    )
    e = find_entries(data, song=song)[1][0]
    return e.cycles_per_tick, e.source


def test_the_speed_bit_selects_the_hosts_own_cia():
    """A tune with no timer of its own is driven by the host, and the bit says which."""
    assert _cadence() == (machine.PAL_FRAME, "pal_video")
    assert _cadence(speed=1) == (PAL_HOST, "pal_host_cia")
    assert _cadence(speed=1, clock=2) == (NTSC_HOST, "ntsc_host_cia")


def test_a_timer_of_its_own_outranks_the_speed_bit():
    """The traced machine decides: an armed latch is the cadence whatever the header says."""
    latch = ("LDA #$00", "STA $DC04", "LDA #$20", "STA $DC05", "RTS")
    assert _cadence(speed=1, init=latch) == (0x2000 + 1, "cia_timer")


def test_the_speed_word_is_a_bitfield_over_subtunes():
    """Bit *n* is subtune *n*; subtunes past the 32nd share bit 31."""
    assert _cadence(speed=0b10, songs=2, song=0) == (machine.PAL_FRAME, "pal_video")
    assert _cadence(speed=0b10, songs=2, song=1) == (PAL_HOST, "pal_host_cia")
    assert _cadence(speed=1 << 31, songs=40, song=39) == (PAL_HOST, "pal_host_cia")
    assert _cadence(speed=1 << 31, songs=40, song=30) == (machine.PAL_FRAME, "pal_video")


def test_an_rsid_runs_the_kernals_cia_unless_it_armed_a_raster():
    """RSID carries no speed word: its host is the KERNAL, whose default IRQ is that CIA."""
    assert _cadence(magic=b"RSID") == (PAL_HOST, "pal_host_cia")
    raster = ("LDA #$32", "STA $D012", "RTS")
    assert _cadence(magic=b"RSID", init=raster) == (machine.PAL_FRAME, "pal_video")
