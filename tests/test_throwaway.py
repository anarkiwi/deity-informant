"""Throw-away bus reads: lifts and both reference interpreters make the machine's."""

import pytest

from deity_informant import NATIVE, c64, wholeprog
from deity_informant.lifter import lift
from deity_informant.vm import PcodeVM, run_sub

pytest.importorskip("vagrant_moses")

# pylint: disable=wrong-import-position
from vagrant_moses.c64 import poweron_ram  # noqa: E402

needs_native = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

ENTRY = 0x1000
R, W = False, True
#: name: (code at ENTRY, {address: code}, the machine's I/O accesses of a call)
SNIPPETS = {
    # LDX #4; STA $D400,X
    "sta_absx_sid": ("a2049d00d460", {}, [(0xD404, R), (0xD404, W)]),
    # LDX #$10; STA $D4F8,X: a page-crossing store reads $D408 first
    "sta_absx_cross": ("a2109df8d460", {}, [(0xD408, R), (0xD508, W)]),
    # LDX #$0D; LDA #$7F; STA $DC00,X: ICR is read (acknowledged) before the write
    "sta_absx_icr": ("a20da97f9d00dc60", {}, [(0xDC0D, R), (0xDC0D, W)]),
    # LDX #$0F; LDA $DCFE,X: a page-crossing read reads CIA 1's ICR, then CIA 2's
    "lda_absx_cross_icr": ("a20fbdfedc60", {}, [(0xDC0D, R), (0xDD0D, R)]),
    # LDX #$0D; LDA $DC00,X
    "lda_absx_icr": ("a20dbd00dc60", {}, [(0xDC0D, R)]),
    # ($FB) = $DC00; LDY #$0D; LDA #$7F; STA ($FB),Y
    "sta_indy_icr": ("a90085fba9dc85fca00da97f91fb60", {}, [(0xDC0D, R), (0xDC0D, W)]),
    # ($FB) = $DCF0; LDY #$1D; LDA ($FB),Y
    "lda_indy_cross": ("a9f085fba9dc85fca01db1fb60", {}, [(0xDC0D, R), (0xDD0D, R)]),
    # LDX #1; INC $D01F,X
    "inc_absx": ("a201fe1fd060", {}, [(0xD020, R), (0xD020, R), (0xD020, W), (0xD020, W)]),
    # JSR $CFFF; $CFFF: RTS reads the byte after it
    "rts_before_io": ("20ffcf60", {0xCFFF: "60"}, [(0xD000, R)]),
    # push $DFFF; RTS reads it and continues at $E000: RTS
    "rts_pulled_io": ("a9df48a9ff4860", {0xE000: "60"}, [(0xDFFF, R)]),
    # LDX #2; JSR $CFF6; $CFF6: DEX; BEQ +3; JMP $CFFE; RTS; $CFFE: BNE $CFF6 (taken once)
    "branch_before_io": (
        "a20220f6cf60",
        {0xCFF6: "caf0034cfecf60", 0xCFFE: "d0f6"},
        [(0xD000, R), (0xD0F6, R)],
    ),
}


def image(code, more):
    img = bytearray(poweron_ram())
    for addr, hexcode in {ENTRY: code, **more}.items():
        b = bytes.fromhex(hexcode)
        img[addr : addr + len(b)] = b
    return img


@needs_native
@pytest.mark.parametrize("cycles", [False, True])
@pytest.mark.parametrize("name", sorted(SNIPPETS))
def test_lift_makes_the_machines_throw_away_reads(name, cycles):
    code, more, want = SNIPPETS[name]
    ports = {"init": 0x35, "call": 0x35}
    img = image(code, more)
    calls = [(ENTRY, 0)]
    res = c64.lift_check(img, ENTRY, calls, calls * 2, None, ports, cycles=cycles)
    assert all([(a, w) for a, _v, w in rec["log"]] == want for rec in res.records)
    for backend in ("interp", "jit"):
        res.prog.backend = backend
        ram, reg = res.init["ram"], res.init["reg"]
        assert c64.check(res.prog, ram, reg, res.records, 0x35) is None, backend


def _vm_accesses(img, pc):
    """``PcodeVM``'s ``(addr, is_write)`` accesses outside the stack page in ``run_sub(pc)``."""
    log = []

    class Logged(PcodeVM):
        """Logs every byte access."""

        def _rd(self, addr, sz):
            log.append((addr & 0xFFFF, R))
            return super()._rd(addr, sz)

        def _wr(self, addr, val, sz):
            log.append((addr & 0xFFFF, W))
            super()._wr(addr, val, sz)

    vm = Logged(img)
    vm.volatile = False
    vm.reg[3] = 0xFD
    run_sub(vm, pc, {}, lift)
    return [e for e in log if e[0] >> 8 != 1]


@needs_native
@pytest.mark.parametrize("name", sorted(SNIPPETS))
def test_reference_interpreters_agree(name):
    code, more, _ = SNIPPETS[name]
    img = image(code, more)
    r = wholeprog.reference_call(bytearray(img), ENTRY, volatile=[(0, 0x10000)], log=True)
    assert [(a, w) for a, _v, _c, w in r["log"] if a >> 8 != 1] == _vm_accesses(img, ENTRY)


def _dummies(code):
    mem = bytearray(0x10000)
    mem[0x1000:0x1003] = bytes.fromhex(code)
    return [o[2] for o in lift(mem, 0x1000)["ops"] if o[0] == "LOAD" and o[1] is None]


def test_lifter_throw_away_reads():
    """Indexed writes always read the unfixed address, indexed reads only on a page cross."""
    assert [len(i) for i in _dummies("bd00d4")] == [2]  # LDA $D400,X: predicated
    assert [len(i) for i in _dummies("9d00d4")] == [1]  # STA $D400,X
    assert [len(i) for i in _dummies("1efed4")] == [1]  # ASL $D4FE,X
    for code in ("ad00d4", "b500", "a1fb", "8d00d4", "60"):
        assert not _dummies(code)


@pytest.mark.parametrize(
    "code,x,y,want",
    [
        ("9ef0c3", 0x35, 0x00, 0xC3F0),  # SHX $C3F0,Y: no cross
        ("9ef0c3", 0x35, 0x20, 0x0410),  # cross: high byte X & ($C3 + 1)
        ("9cf0c3", 0x20, 0x35, 0x0410),  # SHY $C3F0,X
        ("9bf0c3", 0xFF, 0x20, 0xC410),  # TAS $C3F0,Y with A & X = $FF
    ],
)
def test_sh_page_cross_address(code, x, y, want):
    """libsidplayfp ``sh_instr``: on a page cross the stored value is the address's high byte."""
    mem = bytearray(0x10000)
    mem[0x1000:0x1004] = bytes.fromhex(code + "60")
    writes = []

    class Logged(PcodeVM):
        """Logs writes."""

        def _wr(self, addr, val, sz):
            writes.append((addr, val))

    vm = Logged(mem)
    vm.volatile = False
    vm.reg[0], vm.reg[1], vm.reg[2], vm.reg[3] = 0xFF, x, y, 0xFD
    vm.step(0x1000, {}, lift)
    val = (vm.reg[2] if code.startswith("9c") else vm.reg[1]) & 0xC4
    assert writes == [(want, val)]


@needs_native
def test_ram_throw_away_reads_cost_nothing():
    """Indexed accesses, code and the entry's return away from volatile ranges add no ops."""
    code = "a2101df0c19df0c1fe00c1cad0f460"  # ORA/STA/INC $C1xx,X; DEX; BNE; RTS
    img = bytearray(0x10000)
    img[ENTRY : ENTRY + len(code) // 2] = bytes.fromhex(code)
    quiet = wholeprog.lift(img, [ENTRY], [(0xD000, 0xE000)])
    ops = [o for f in quiet.functions for b in f["blocks"] for o in b["ops"]]
    assert not [o for o in ops if o[0] == "LOAD" and o[1] is None]
    assert quiet.stats["ops"] == wholeprog.lift(img, [ENTRY], []).stats["ops"]


@needs_native
@pytest.mark.parametrize("base,folds", [(0xD400, True), (0xD404, False)])
def test_unfixed_address_folds_without_a_page_cross(base, folds):
    """``STA base,X`` reads the store's own address when no X can cross a page."""
    img = bytearray(0x10000)
    img[ENTRY : ENTRY + 4] = bytes([0x9D, base & 0xFF, base >> 8, 0x60])
    prog = wholeprog.lift(img, [ENTRY], [(0xD000, 0xE000)])
    ops = [o for f in prog.functions for b in f["blocks"] for o in b["ops"]]
    (dummy,) = [o for o in ops if o[0] == "LOAD" and o[1] is None]
    (store,) = [o for o in ops if o[0] == "STORE"]
    assert (dummy[2][0] == store[2][0]) == folds
