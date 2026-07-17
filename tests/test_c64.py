"""C64 environment helpers: power-on RAM, handler discovery, KERNAL stub."""

from deity_informant import PcodeVM, c64


def test_poweron_ram_stripe_pattern():
    ram = c64.poweron_ram()
    assert len(ram) == 0x10000 and isinstance(ram, bytes)
    # First 16 KiB block base 0x00 with 0xFF 4-byte stripes every 8 from offset 2.
    assert ram[0x0000] == 0x00 and ram[0x0001] == 0x00
    assert ram[0x0002:0x0006] == b"\xff\xff\xff\xff"
    assert ram[0x0006:0x000A] == b"\x00\x00\x00\x00"
    # Second block (0x4000) inverts: base 0xFF with 0x00 stripes.
    assert ram[0x4000] == 0xFF
    assert ram[0x4002:0x4006] == b"\x00\x00\x00\x00"
    # Blocks alternate 0x00/0xFF across the 64 KiB.
    assert ram[0x8000] == 0x00 and ram[0xC000] == 0xFF


def test_read_vector_little_endian():
    mem = bytearray(0x10000)
    mem[0x0314], mem[0x0315] = 0x31, 0xEA
    assert c64.read_vector(mem, 0x0314) == 0xEA31


def test_installed_handler_prefers_written_cinv():
    mem = bytearray(0x10000)
    mem[0x0314], mem[0x0315] = 0x00, 0x20  # CINV -> $2000
    assert c64.installed_handler(mem, {0x0314}, (0x1000, 0x1100)) == (0x2000, True)


def test_installed_handler_written_nmi_and_hardware():
    mem = bytearray(0x10000)
    mem[0x0318], mem[0x0319] = 0x50, 0x30  # NMINV -> $3050
    assert c64.installed_handler(mem, {0x0319}, (0, 0)) == (0x3050, False)
    mem[0xFFFE], mem[0xFFFF] = 0x00, 0x40  # hardware -> $4000
    assert c64.installed_handler(mem, {0xFFFE}, (0, 0)) == (0x4000, False)


def test_installed_handler_lifted_from_image_when_unwritten():
    mem = bytearray(0x10000)
    mem[0x0314], mem[0x0315] = 0x00, 0x50  # CINV lives inside the load image
    assert c64.installed_handler(mem, set(), (0x0300, 0x0400)) == (0x5000, True)


def test_installed_handler_none_when_absent_or_zero():
    mem = bytearray(0x10000)
    assert c64.installed_handler(mem, set(), (0x1000, 0x1100)) is None  # not in image
    assert c64.installed_handler(mem, set(), (0x0300, 0x0400)) is None  # in image but $0000


def test_install_kernal_irq_stubs_writes_return_path():
    vm = PcodeVM(bytearray(0x10000))
    c64.install_kernal_irq_stubs(vm)
    assert vm.mem[0xEA31:0xEA34] == bytes((0x4C, 0x81, 0xEA))  # JMP $EA81
    assert vm.mem[0xEA81:0xEA87] == bytes((0x68, 0xA8, 0x68, 0xAA, 0x68, 0x40))  # PLA;TAY;...RTI
