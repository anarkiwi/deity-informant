"""Lifted whole programs run ADC, SBC, RRA and ISC exactly: with the decimal flag proven clear,
declared set or possibly set at the entry, set in the code, and set by a caller that did not
declare it (the entry guard hands over to the interpreter)."""

import random

import pytest

import decimal_ref as ref
from test_wholeprog import differential, facts_for
from wholeprog_progs import IO, assemble
from deity_informant import NATIVE, wholeprog

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

MNEMONICS = {"ADC": ref.adc, "SBC": ref.sbc, "RRA": ref.rra, "ISC": ref.isc}
OPERAND, RESULT = 0x80, 0x81
D, C = 11, 8

# how the program is entered: entry spec, the decimal flag the caller passes, and whether the
# code sets it itself
MODES = {
    "clear": (0, {}, 0, False),
    "undeclared set": (0, {}, 1, False),
    "declared set": (0, {"d": [1]}, 1, False),
    "declared either": (0, {"d": [0, 1]}, 1, False),
    "declared either clear": (0, {"d": [0, 1]}, 0, False),
    "in code": (0, {}, 0, True),
}


def program(instruction, set_in_code):
    return assemble(
        "entry: %s\n %s\n STA $%02X\n CLD\n RTS"
        % ("SED" if set_in_code else "NOP", instruction, RESULT)
    )


def lifted(mnemonic, regs, set_in_code):
    img, labels = program("%s $%02X" % (mnemonic, OPERAND), set_in_code)
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, [(pcs[0], regs)], [IO], facts_for(img, pcs, [(0, 0)]))
    return img, prog


def run(prog, img, a, m, c, d):
    mem = bytearray(img)
    mem[OPERAND] = m
    reg = [0] * 16
    reg[3], reg[C], reg[D] = 0xFD, c, d
    out = prog.call(mem, 0, a=a, reg=reg)
    return out["reg"], mem


@pytest.mark.parametrize("mnemonic", MNEMONICS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("quarter", range(4))
def test_lifted_program_matches_the_reference(mnemonic, mode, quarter):
    _, regs, d, in_code = MODES[mode]
    img, prog = lifted(mnemonic, regs, in_code)
    fn = MNEMONICS[mnemonic]
    decimal = d or in_code
    for a in range(quarter * 64, quarter * 64 + 64):
        for m in range(256):
            for c in (0, 1):
                reg, mem = run(prog, img, a, m, c, d)
                want = fn(a, m, c, int(bool(decimal)))
                assert reg[0] == want[0], (a, m, c)
                assert (reg[14], reg[13], reg[9], reg[8]) == tuple(want[1:5]), (a, m, c)
                assert reg[D] == 0
                assert mem[RESULT] == want[0]
                if mnemonic in ("RRA", "ISC"):
                    assert mem[OPERAND] == want[5], (a, m, c)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("eighth", range(8))
def test_lifted_arr_matches_the_reference(mode, eighth):
    """ARR takes its operand from the code, so each operand is a program of its own."""
    _, regs, d, in_code = MODES[mode]
    img0, labels = program("ARR #$00", in_code)
    entry = labels["entry"]
    decimal = int(bool(d or in_code))
    for m in range(eighth * 32, eighth * 32 + 32):
        img = bytearray(img0)
        img[entry + 2] = m
        prog = wholeprog.lift(img, [(entry, regs)], [IO], facts_for(img, [entry], [(0, 0)]))
        for a in range(256):
            for c in (0, 1):
                reg, _ = run(prog, img, a, 0, c, d)
                want = ref.arr(a, m, c, decimal)
                assert reg[0] == want[0], (a, m, c)
                assert (reg[14], reg[13], reg[9], reg[8]) == tuple(want[1:5]), (a, m, c)


ADD16 = """
entry:  %s
        CLC
        LDA $80
        ADC $82
        STA $84
        LDA $81
        ADC $83
        STA $85
        CLD
        RTS
"""


@pytest.mark.parametrize("decimal", (False, True))
def test_a_carry_chain_is_fused_only_where_the_decimal_flag_is_clear(decimal):
    img, labels = assemble(ADD16 % ("SED" if decimal else "NOP"))
    pcs = [labels["entry"]]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, [(0, 0)]))
    assert prog.stats["fused_chains"] == (0 if decimal else 1)
    for lo, hi, lo2, hi2 in [
        (0x99, 0x09, 0x01, 0x00),
        (0x45, 0x67, 0x55, 0x43),
        (0xFA, 0x99, 0x0F, 0x01),
    ]:
        mem = bytearray(img)
        mem[0x80:0x84] = bytes([lo, hi, lo2, hi2])
        prog.call(mem, 0)
        low = ref.adc(lo, lo2, 0, decimal)
        high = ref.adc(hi, hi2, low[4], decimal)
        assert (mem[0x84], mem[0x85]) == (low[0], high[0])


def _patch(opcode, original, timing):
    """A flag instruction at ``cell`` that a store replaces by ``opcode``, then arithmetic."""
    tail = [
        "cell:   %s" % original,
        "        LDA $80",
        "        ADC $82",
        "        STA $84",
        "        LDA $81",
        "        SBC $83",
        "        STA $85",
        "        CLD",
        "        RTS",
    ]
    patch = ["        LDA #$%02X" % opcode, "        STA cell"]
    if timing == "same_call_earlier":
        body = ["entry:  CMP #$00", "        BEQ go"] + patch + ["go:     CLC"]
        return "\n".join(body + tail), ["entry"], [(0, 0), (0, 1), (0, 0), (0, 1)]
    body = ["entry:  JMP cell", "ini:"] + patch + ["        RTS"]
    calls = [(0, 0), (1, 0), (0, 0), (0, 0)]
    return "\n".join(body + tail), ["entry", "ini"], calls


@pytest.mark.parametrize("timing", ["same_call_earlier", "earlier_call"])
@pytest.mark.parametrize("opcode, original", [(0xF8, "NOP"), (0xD8, "SED")])
@pytest.mark.parametrize("seed", range(8))
def test_code_that_sets_or_clears_the_decimal_flag_is_exact(timing, opcode, original, seed):
    rng = random.Random(seed)
    src, names, calls = _patch(opcode, original, timing)
    img, labels = assemble(src)
    pcs = [labels[n] for n in names]
    prog = wholeprog.lift(img, pcs, [IO], facts_for(img, pcs, calls[:1]))
    for _ in range(3):
        mem = bytearray(img)
        mem[0x80:0x84] = bytes(rng.randrange(256) for _ in range(4))
        differential(img, prog, pcs, calls, mem=mem)
