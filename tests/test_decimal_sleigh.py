"""The 6510 SLEIGH module's ADC, SBC, ARR, RRA and ISC against Bruce Clark's reference."""

import re

import pytest

import decimal_ref as ref
from pcode_eval import Machine
from deity_informant.sleigh import build

# opcode, reference, zero-page operand
CASES = {
    "ADC": (0x69, ref.adc, False),
    "ADC zp": (0x65, ref.adc, True),
    "SBC": (0xE9, ref.sbc, False),
    "SBC alias": (0xEB, ref.sbc, False),
    "SBC zp": (0xE5, ref.sbc, True),
    "ARR": (0x6B, ref.arr, False),
    "RRA": (0x67, ref.rra, True),
    "ISC": (0xE7, ref.isc, True),
}


def instruction(ctx6510, op, m, zero_page):
    return ctx6510.translate(bytes([op, 0x10 if zero_page else m]), 0x1000, 0, 0, 0).ops


@pytest.mark.parametrize("d", (0, 1))
@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("quarter", range(4))
def test_sleigh_matches_the_reference(ctx6510, case, quarter, d):
    op, fn, zero_page = CASES[case]
    reg = {k: ctx6510.registers[k].offset for k in "ANVZCD"}
    for m in range(256):
        ops = instruction(ctx6510, op, m, zero_page)
        for a in range(quarter * 64, quarter * 64 + 64):
            for c in (0, 1):
                vm = Machine()
                regs = vm.spaces["register"]
                regs[reg["A"]], regs[reg["C"]], regs[reg["D"]] = a, c, d
                vm.spaces["RAM"][0x10] = m
                vm.run(ops)
                want = fn(a, m, c, d)
                got = tuple(regs[reg[k]] for k in "ANVZC")
                assert got == tuple(want[:5]), (case, a, m, c, d)
                if case in ("RRA", "ISC"):
                    assert vm.spaces["RAM"][0x10] == want[5], (case, a, m, c, d)


def test_install_brings_every_included_source_along(tmp_path):
    """Ghidra compiles the installed ``.slaspec`` itself, so each ``@include`` must be there."""
    pytest.importorskip("pypcode")
    dst = tmp_path / "Processors" / "6510" / "data" / "languages"
    assert build.main(["--install", str(dst)]) == 0
    sources = [*dst.glob("*.slaspec"), *dst.glob("*.sinc")]
    included = [n for f in sources for n in re.findall(r'@include "([^"]+)"', f.read_text())]
    assert "6510_alu.sinc" in included
    assert all((dst / name).is_file() for name in included)
    assert (dst.parents[1] / "Module.manifest").is_file()
