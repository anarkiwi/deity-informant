"""deity_informant.c64: the bank map, and facts and records from the native C64 machine."""

import hashlib

import numpy as np
import pytest

pytest.importorskip("vagrant_moses")

# pylint: disable=wrong-import-position
from vagrant_moses import machine as vm  # noqa: E402
from vagrant_moses.bus import port_read  # noqa: E402
from vagrant_moses.c64 import poweron_ram  # noqa: E402
from vagrant_moses.roms import ROMS, rom_less  # noqa: E402

from deity_informant import NATIVE, c64, trace, wholeprog  # noqa: E402
from deity_informant.smc.machine import CALLS, MachineOracle, core  # noqa: E402

import _common as H  # noqa: E402 pylint: disable=wrong-import-order

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
    # ($FB) = $0180: STA ($FB),Y; STA ($F9,X); LDA ($FB),Y; SAX $0190; STX $D020; INC $D021;
    # STY $F0,X; STX $F0,Y; LDA $01F0,Y; PHP; PLP; JMP ($0170) -> $1650: RTS
    0x1600: "a202a003a98085fba90185fca95591fb81f9b1fb8f90018e20d0ee21d094f096f0b9f001" "08286c7001",
    0x1650: "60",
    0x0170: "5016",
    0x1700: "ad05d41869018d05d460",  # LDA $D405; CLC; ADC #1; STA $D405; RTS
    0x1780: "a9408d05d460",  # LDA #$40; STA $D405; RTS
    0x17A0: "ee05d460",  # INC $D405; RTS
    0x1800: "a9558d00d4ad05d48d00c060",  # LDA #$55; STA $D400; LDA $D405; STA $C000; RTS
    0x1810: "ad1bd4ae1cd460",  # LDA $D41B; LDX $D41C; RTS
    0x1820: "ad12d0ae04dcac00de60",  # LDA $D012; LDX $DC04; LDY $DE00; RTS
    0x1830: "ad05d4ad06d460",  # LDA $D405; LDA $D406; RTS
    0x1840: "ad06d460",  # LDA $D406; RTS
    0x1850: "a9408d0fd4a9218d12d460",  # LDA #$40; STA $D40F; LDA #$21; STA $D412; RTS
    # CIA 1 timer A one-shot IRQ after 32 cycles, CLI, wait in a DEX loop, SEI; RTS
    0x1880: "a97f8d0ddca9208d04dca9008d05dca9818d0ddca9198d0edc58a220cad0fd7860",
    0x18C0: "ad0ddc40",  # LDA $DC0D; RTI
    0xFFFE: "c018",
}


def image():
    img = bytearray(poweron_ram())
    for addr, code in PROGRAM.items():
        b = bytes.fromhex(code)
        img[addr : addr + len(b)] = b
    img[0xE000], img[0xD438] = 0x42, 0x99
    return img


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
    assert r["log"] == [(0xD418, 7, True), (0xD418, 7, False), (0xD438, 7, False)]
    assert r["reg"][:2] == [7, 7]


@native
def test_machine_real_roms(real):
    img = image()
    (r,) = c64.machine_record(img, [(0x1500, 0)], ports={"call": 0x37}, roms=real)
    (s,) = c64.machine_record(img, [(0x1500, 0)], ports={"call": 0x37})
    assert r["ram"][0xC000] == real[0xE000][0] != s["ram"][0xC000] == rom_less()[0xE000][0]


def _backend_run(backend, calls, init, ports, mc):
    h = c64._Host(image(), backend=backend).drive(calls, init, mc, ports)  # pylint: disable=W0212
    facts, snaps = h.facts(), h.all_snaps()
    inits = [s for s in snaps if s[0] != "call"]
    bad = None
    if inits and inits[-1][1] == "return":
        prog = wholeprog.lift(
            inits[-1][4], [calls[0][0]], [c64.DEVICES], facts, bank=c64.bank_map()
        )
        records = [dict(zip(("halt", "reg", "log", "ram"), s[1:])) for s in snaps if s[0] == "call"]
        bad = c64.check(prog, inits[-1][4], inits[-1][2], records, (ports or {}).get("call"), mc)
        last, split, _ = h.split()
        assert last[-1]["ram"] == inits[-1][4]
        port = (ports or {}).get("call")
        assert c64.check(prog, inits[-1][4], inits[-1][2], split, port, mc) == bad
    return facts.to_dict(), snaps, bad, h.result.cells


@pytest.mark.skipif(
    not (NATIVE and c64.TRACE_NATIVE), reason="needs the native extension and native trace"
)
@pytest.mark.parametrize(
    "calls,init,ports,mc",
    [
        ([(0x1000, 0)] * 2, [(0x1100, 0)], {"call": 0x36}, 2_000_000),
        ([(0x1600, 0)] * 3, [(0x1100, 0)], {"call": 0x35}, 2_000_000),
        ([(0x1400, 0)] * 2, [(0x1100, 0)], {"init": 0x37, "call": 0x35}, 2_000_000),
        ([(0x1000, 0), (0x1200, 0)], [(0x1100, 0)], None, 1000),
        ([(0x1300, 0), (0x1000, 0)], (), None, 2_000_000),
        ([(0x1880, 0)] * 2, [(0x1100, 0)], {"call": 0x35}, 2_000_000),
    ],
)
def test_trace_backends_agree(calls, init, ports, mc):
    py, nat = (_backend_run(b, calls, init, ports, mc) for b in ("python", "native"))
    assert py == nat


def _lift_check(entry, calls=2, init=0x1100):
    ports = {"init": 0x35, "call": 0x35}
    return c64.lift_check(image(), entry, [(entry, 0)], [(entry, 0)] * calls, [(init, 0)], ports)


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_sid_registers_carry_across_calls():
    res = _lift_check(0x1700, 3, 0x1780)
    assert res.bad is None and [r["log"][-1] for r in res.records] == [
        (0xD405, v, True) for v in (0x41, 0x42, 0x43)
    ]


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_unreadable_sid_register_reads_bus_latch():
    res = _lift_check(0x1800)
    assert res.bad is None and image()[0xD405] != 0x55
    assert res.records[0]["log"] == [(0xD400, 0x55, True), (0xD405, 0x55, False)]
    assert res.records[0]["ram"][0xC000] == 0x55


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_readable_and_other_device_reads_replay():
    for entry in (0x1810, 0x1820):
        res = _lift_check(entry, 3)
        assert res.bad is None and all(len(r["log"]) == 2 + (entry == 0x1820) for r in res.records)
    osc3 = [r["log"][0] for r in _lift_check(0x1810, 3, 0x1850).records]
    assert {a for a, _, _ in osc3} == {0xD41B} and len({v for _, v, _ in osc3}) > 1
    raster = [r["log"][0] for r in _lift_check(0x1820, 3).records]
    assert {a for a, _, _ in raster} == {0xD012} and len({v for _, v, _ in raster}) > 1


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_device_read_differences_are_mismatches():
    one, two, other = (_lift_check(e) for e in (0x1700, 0x1830, 0x1840))
    ram, reg = one.init["ram"], one.init["reg"]
    assert c64.check(two.prog, ram, reg, one.records, 0x35)[:3] == [0, "log", 1]
    assert c64.check(one.prog, ram, reg, two.records, 0x35)[:3] == [0, "log", 1]
    assert c64.check(other.prog, ram, reg, one.records, 0x35)[:3] == [0, "log", 0]


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_halts_and_interrupts_are_outcomes():
    irq = _lift_check(0x1880)
    assert irq.bad == [0, "interrupt", "cia_irq"] and c64.outcome(irq.bad) == "interrupt"
    ports = {"init": 0x35, "call": 0x35}
    for queue in (True, False):
        h = c64._Host(image())  # pylint: disable=protected-access
        h.native_queue = queue
        h.drive([(0x1100, 0), (0x1880, 0)], [(0x1100, 0)], 2_000_000, ports)
        assert [h.interrupts(k) for k in range(2)] == [[], ["cia_irq"]]
    assert _lift_check(0x1300).bad == [0, "halted", "jam"]
    ports = {"init": 0x35, "call": 0x35}
    spin = c64.lift_check(image(), 0x1200, [(0x1200, 0)], [(0x1200, 0)], [(0x1100, 0)], ports,
                          max_cycles=1000)  # fmt: skip
    assert spin.bad == [0, "halted", "cycles"] and c64.outcome(spin.bad) == "halted"
    assert c64.outcome(None) == "exact" and c64.outcome([0, "log", 1]) == "mismatch"


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_rmw_bus_writes_match_machine():
    ports = {"init": 0x35, "call": 0x35}
    res = c64.lift_check(image(), 0x17A0, [(0x17A0, 0)], [(0x17A0, 0)] * 2, [(0x1780, 0)], ports)
    assert res.bad is None and res.records[1]["log"] == [
        (0xD405, 0x41, False),
        (0xD405, 0x41, True),
        (0xD405, 0x42, True),
    ]


def test_trace_table_and_backend_choice():
    t = np.frombuffer(c64.trace_table(), "<u2")
    assert t[0x8D] == 4 | 1 << 5 | 1 << 6 and t[0x8F] == 4 | 1 << 5 | 4 << 6
    assert t[0xB1] == 8 | 1 << 4 and t[0xEE] == 4 | 3 << 4 and t[0x6C] == 9
    assert t[0x20] == 4 | 1 << 5 | 2 << 9 and t[0x00] == 1 << 5 | 3 << 9 and t[0xA9] == 0
    if not c64.TRACE_NATIVE:
        with pytest.raises(ValueError):
            c64._Host(image(), backend="native")  # pylint: disable=protected-access


@native
@pytest.mark.parametrize("song,ntsc", [(1, False), (3, True)])
def test_basic_boot_runs_the_program_with_the_song(real, song, ntsc):
    prog = H.SONG_POKE
    img = bytearray(poweron_ram())
    img[0x0801 : 0x0801 + len(prog)] = prog
    boot = c64.boot(img, 0x0801, 0x0801 + len(prog), 0, song, ntsc, "BASIC", roms=real)
    end = (0x0801 + len(prog)).to_bytes(2, "little")
    assert boot.image[0x2B:0x2D] == b"\x01\x08" and boot.image[0x2D:0x2F] == end
    assert boot.image[0x02A6] == (not ntsc) and boot.init == (0xBF55, song - 1)
    assert boot.roms[0xA000] != real[0xA000] and real[0xA000] == c64.real_roms()[0xA000]
    o = MachineOracle(boot.image, ntsc=ntsc, roms=boot.roms, backend="python")
    o.run([], frames=5, **boot.run_kw())
    assert o.ram[4096] == song and o.ram[c64.SAREG] == song - 1
    assert o.ram[0x39] | o.ram[0x3A] << 8 == 20 and not o.m.jammed


#: A ``psiddrv.a65`` cold start for a tune without a play address, at $0400, its data first.
DRIVER = """
playnum: .byte ${song:02X} ; speed: .byte ${speed:02X} ; initvec: .word $1000 ; playvec: .word 0
initiomap: .byte ${iomap:02X} ; playiomap: .byte 0 ; video: .byte ${video:02X}
clock: .byte ${video:02X} ; flags: .byte ${flags:02X} ; init: JMP ($0402)
cold: SEI ; LDA #$00 ; STA $D01A ; LDA $D019 ; STA $D019 ; LDA #$7F ; STA $DC0D ; STA $DD0D
LDA $DC0D ; LDA $DD0D ; LDA #$0F ; STA $D418 ; LDA video ; BEQ ntsc ; LDX #$25 ; LDY #$40
JMP timer ; ntsc: LDX #$95 ; LDY #$42 ; timer: STX $DC04 ; STY $DC05 ; LDX #$9B ; LDY #$37
EOR clock ; ORA initiomap ; BEQ vicinit ; LDX #$1B ; LDY #$00 ; vicinit: STX $D011
STY $D012 ; LDA initiomap ; BEQ irqinit ; LDA playiomap ; BEQ irqinit ; STX $0314
irqinit: LDA speed ; BNE ciainit ; LDA #$81 ; STA $D01A ; JMP setiomap
ciainit: LDA #$81 ; LDX #$01 ; STA $DC0D ; STX $DC0E ; setiomap: LDA initiomap ; BNE setbank
LDA #$37 ; setbank: STA $01 ; LDA flags ; PHA ; LDA playnum ; PLP ; JSR init
LDA initiomap ; BEQ idle ; LDA playiomap ; BEQ run ; LDA #$37 ; STA $01 ; run: CLI
idle: JMP idle
"""
#: Init: reports CIA 1 timer A, the raster, the VIC IRQ flags and SP (as reset, then sidplayfp's
#: ``jsr init``, leave it) to the SID, takes its IRQs (reporting the timer) and returns.
PROBE = (
    "ad04dc8d01d4ad05dc8d02d4ad12d08d03d4ad19d08d04d4ba8e05d4"
    "a9408d1403a9108d150360"  # $0314 = $1040; RTS
)
HANDLER = "ad04dc8d06d4a9ff8d19d0ad0ddc68a868aa6840"  # report, ack VIC and CIA, RTI


def _assemble(src, base):
    """``(code, labels)`` of ``src`` (``;``-separated, ``label:`` prefixes, ``.byte``/``.word``)
    at ``base``, in two passes."""
    from py65.assembler import Assembler  # pylint: disable=import-outside-toplevel
    from py65.devices.mpu6502 import MPU  # pylint: disable=import-outside-toplevel
    from py65.utils.addressing import AddressParser  # pylint: disable=import-outside-toplevel

    stmts = [x.strip() for x in src.replace("\n", ";").split(";") if x.strip()]
    names = [x.split(":")[0] for x in stmts if ":" in x]
    parser, labels = AddressParser(), {}
    asm = Assembler(MPU(), parser)
    for final in (False, True):
        pc, out = base, bytearray()
        for st in stmts:
            label, _, op = st.rpartition(":")
            if label:
                labels[label] = pc
            parser.labels = labels if final else dict.fromkeys(names, pc)
            directive, _, arg = op.strip().partition(" ")
            if directive in (".byte", ".word"):
                code = parser.number(arg).to_bytes(1 + (directive == ".word"), "little")
            else:
                code = bytes(asm.assemble(op.strip(), pc))
            out += code
            pc += len(code)
    return bytes(out), labels


class _SidLog:
    def __init__(self):
        self.writes = []

    def read(self, _cycle, _reg):
        return 0

    def write(self, cycle, reg, value):
        self.writes.append((cycle, reg, value))


def _sid_log(img, ntsc, roms, frames=3, **kw):
    log = _SidLog()
    o = MachineOracle(img, ntsc=ntsc, devices={0xD400: log}, roms=roms)
    o.run([], frames=frames, **kw)
    return log.writes


@native
@pytest.mark.parametrize(
    "kind,ntsc,cia", [("RSID", False, 1), ("RSID", True, 1), ("PSID", False, 0), ("PSID", True, 1)]
)
def test_boot_matches_the_driver_cycle_for_cycle(request, kind, ntsc, cia):
    """A :func:`c64.boot` start makes the I/O, the timer and raster phase, the interrupts and the
    SID writes that running sidplayfp's cold start on the machine does."""
    roms = request.getfixturevalue("real") if kind == "RSID" else None
    img = bytearray(poweron_ram())
    img[0x1000 : 0x1000 + len(bytes.fromhex(PROBE))] = bytes.fromhex(PROBE)
    img[0x1040 : 0x1040 + len(bytes.fromhex(HANDLER))] = bytes.fromhex(HANDLER)
    boot = c64.boot(img, 0x1000, 0x1060, 0x1000, 2, ntsc, kind, cia, roms)
    iomap = 0 if kind == "RSID" else c64.iomap(0x1000)
    src = DRIVER.format(
        song=1, speed=cia, iomap=iomap, video=int(not ntsc), flags=4 * (kind == "PSID")
    )
    drv, labels = _assemble(src, 0x0400)
    ref = bytearray(boot.image)
    ref[0x0400 : 0x0400 + len(drv)] = drv
    want = _sid_log(ref, ntsc, boot.roms, init=[(labels["cold"], 0)])
    got = _sid_log(boot.image, ntsc, boot.roms, **boot.run_kw())
    sp = [w for w in got if w[1] == 5]
    assert sp[0][2] == 0xFD and [w for w in want if w[1] == 5][0][2] == 0xFB  # the call to cold
    end = min(got[-1][0], want[-1][0])
    got, want = ([w for w in log if w[1] != 5 and w[0] <= end] for log in (got, want))
    assert got == want and len(got) > 6 and got[0][1:] == (0x18, 0x0F)


def test_psid_boot_environment():
    img = bytearray(poweron_ram())
    img[0x0200:0x0210] = bytes(range(1, 17))
    img[0xFFF0:] = b"\xaa" * 16
    boot = c64.boot(img, 0xFFF0, 0x10000, 0xFFF0, 3, ntsc=True, kind="PSID", cia=False)
    assert boot.image[0x2D:0x33] == bytes(6) and boot.image[0xAC:0xB0] == b"\xf0\xff\x00\x00"
    assert boot.image[0x0200:0x0210] == bytes(16) and boot.image[0x02A6] == 0 and not boot.roms
    assert boot.image[0x0314:0x031A] == bytes.fromhex("7eeae2fce2fc") and boot.port == 0x35
    assert boot.init == (c64.SENTINEL - 15, 2) and boot.image[0xFFF0:] == b"\xaa" * 16
    assert boot.image[c64.SENTINEL - 15 : c64.SENTINEL - 11] == bytes.fromhex("78 4c f0 ff")
    io, start = c64.driver_io(True, 0x35, False)
    assert boot.io == io and boot.start == start - 5 and io[-1][1:] == (0xD01A, 0x81)


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_sentinel_moves_off_tune_code():
    img = image()
    assert c64.sentinel_for(img) == c64.SENTINEL
    code = bytes.fromhex("ee00c060")  # INC $C000; RTS
    img[c64.SENTINEL : c64.SENTINEL + len(code)] = code
    s = c64.sentinel_for(img)
    assert s + 3 <= 0xD000 and not set(range(s, s + 3)) & set(range(0xCFF0, 0xCFF4))
    (rec,) = c64.machine_record(img, [(c64.SENTINEL, 0)])
    assert rec["halt"] == "return" and rec["ram"][0xC000] == (img[0xC000] + 1) & 0xFF


def _tampered(packed, field, k, value):
    b = bytearray(getattr(packed, field))
    b[k] = value
    return c64.CallRecords(packed._replace(**{field: bytes(b)}))


@pytest.mark.skipif(
    not (NATIVE and c64.TRACE_NATIVE and CALLS), reason="needs the native trace and call queue"
)
def test_native_check_reports_as_the_python_check():
    h = c64._Host(image()).drive(  # pylint: disable=protected-access
        [(0x1600, 0)] * 3, [(0x1100, 0)], ports={"call": 0x35}
    )
    inits, recs, n = h.split()
    assert isinstance(recs, c64.CallRecords) and n == len(recs) == 3
    assert recs[-1] == list(recs)[-1] and recs[0]["halt"] == h.last_halt == "return"
    last = inits[-1]
    prog = wholeprog.lift(last["ram"], [0x1600], [c64.DEVICES], h.facts(), bank=c64.bank_map())
    p = recs.packed
    m1 = 7 * (recs.first + 1)
    cases = [
        recs,
        _tampered(p, "meta", m1 + 1, 1),
        _tampered(p, "meta", m1 + 2, recs.meta[recs.first + 1, 2] ^ 1),
    ]
    d0 = int(recs.delta_end[recs.first - 1])
    i = d0 + int(np.flatnonzero(recs.idx[d0:] >> 8 != 1)[0])
    cases.append(_tampered(p, "vals", i, p.vals[i] ^ 1))
    if p.log:
        cases.append(_tampered(p, "log", len(p.log) - 2, p.log[-2] ^ 1))
    if len(p.log_end) > 1:
        ends = np.frombuffer(p.log_end, "<u8").copy()
        ends[recs.first :] = ends[recs.first - 1]
        log = p.log[: 4 * int(ends[-1])]
        cases.append(c64.CallRecords(p._replace(log_end=ends.tobytes(), log=log)))
    got = set()
    for r in cases:
        fast = c64.check(prog, last["ram"], last["reg"], r, 0x35)
        assert fast == c64.check(prog, last["ram"], last["reg"], list(r), 0x35)
        got.add(None if fast is None else fast[1])
    assert {None, "halt", "reg", "memory"} <= got
    with pytest.raises(ValueError):
        prog.check(bytearray(last["ram"]), last["reg"], p._replace(vals=b""), recs.first)


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_reference_calls_are_reference_calls_in_a_loop():
    img, bank = image(), c64.bank_map()
    for kw in ({}, {"bank": bank, "port": 0x36}):
        mem, reg, cycles = bytearray(img), None, 0
        for _ in range(3):
            if "port" in kw:
                mem[1] = kw["port"]
            o = wholeprog.reference_call(mem, 0x1000, reg=reg, bank=kw.get("bank"))
            reg, cycles = o["reg"], cycles + o["cycles"]
        out = bytearray(img)
        assert wholeprog.reference_calls(out, 0x1000, 3, **kw) == (3, cycles, "return")
        assert out == mem
    assert wholeprog.reference_calls(bytearray(img), 0x1000, 2, max_cycles=1)[::2] == (1, "cycles")
