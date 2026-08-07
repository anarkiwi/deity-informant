"""The adversarial 16-bit shredder (docs/frameprog.md 7.9).

Every fixture shreds one 16-bit datum the way a 6502 must -- split, strided
and zero-page layouts; add, subtract and shift arithmetic; stack, spill and
illegal-opcode transport; fixed and per-voice indexing -- and the lift owes
back the whole word: gate-exact, round-tripping, and free of byte shadows."""

import re

import pytest

import _fuzzgen as G
from test_frameprog import _fuzz_model
from deity_informant import frameprog, frameval

SID = G.SID
T = G.TBL
VT = G.TBL + 0x30  # voice offsets 0/7/14
SPL = G.CNT + 0x10  # spill cells
ZL, ZH = 0x60, 0x61

_VALS = (0x0246, 0x1369, 0x2A8C)


def _data(layout):
    d = {VT: 0x00, VT + 1: 0x07, VT + 2: 0x0E}
    for k, v in enumerate(_VALS):
        if layout == "split3":
            d[T + k], d[T + 3 + k] = v & 0xFF, v >> 8
        elif layout == "split64":
            d[T + k], d[T + 0x40 + k] = v & 0xFF, v >> 8
        elif layout == "stride2":
            d[T + 2 * k], d[T + 2 * k + 1] = v & 0xFF, v >> 8
    if layout == "zp":
        d[ZL], d[ZH] = _VALS[0] & 0xFF, _VALS[0] >> 8
    return d


def _lo_hi(layout, mode):
    """(lo operand, hi operand, addressing mode) for entry loads."""
    if layout == "split3":
        return T, T + 3, mode
    if layout == "split64":
        return T, T + 0x40, mode
    if layout == "stride2":
        return T, T + 1, mode
    return ZL, ZH, "zp"


def _lift(name, a, data, outs=(SID, SID + 1)):
    player = G.Player(name, G.ORG, a.assemble(), set(outs), {"indexed"}, data=dict(data))
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, 8, prog) is None, "the frame oracle must hold"
    assert frameprog.dumps(frameprog.loads(text)) == text
    return text[text.index("sub_") :]


_SHADOWS = (
    ") << $08):2 | zext2(",
    "carry(",
    "trunc1(",
    "sid.reg[",
    "& $FF00",
    "& $00FF",
    "m_01",
)


def _canonical(body):
    bad = [ln.strip() for ln in body.splitlines() if any(sig in ln for sig in _SHADOWS)]
    bad += [
        ln.strip()
        for ln in body.splitlines()
        if re.search(r"sid\.v\d\.(freq|pw)_(lo|hi)", ln) and " = " in ln and ":2 = " not in ln
    ]
    bad += [ln.strip() for ln in body.splitlines() if re.search(r"\bsp\b", ln)]
    assert not bad, "byte shadows survive:\n" + "\n".join(dict.fromkeys(bad))
    assert ":2 = " in body, "no word store reached the SID"


def _fixed(layout, math):
    """Single-voice fixture: load entry 1, run the math, publish the pair."""
    lo, hi, _m = _lo_hi(layout, "abs")
    k = 0 if layout == "zp" else (2 if layout == "stride2" else 1)
    lo, hi = lo + k, hi + k
    zp = layout == "zp"
    md = "zp" if zp else "abs"
    a = G.Asm(G.ORG)
    if math == "add":
        a.i("CLC").i("LDA", md, lo).i("ADC", "imm", 0x37).i("STA", md, lo)
        a.i("LDA", md, hi).i("ADC", "imm", 0x01).i("STA", md, hi)
    elif math == "sub":
        a.i("SEC").i("LDA", md, lo).i("SBC", "imm", 0x37).i("STA", md, lo)
        a.i("LDA", md, hi).i("SBC", "imm", 0x00).i("STA", md, hi)
    elif math == "shl":
        a.i("ASL", md, lo).i("ROL", md, hi)
    a.i("LDA", md, lo).i("STA", "abs", SID)
    a.i("LDA", md, hi).i("STA", "abs", SID + 1)
    a.i("RTS")
    return a


@pytest.mark.parametrize("layout", ["split3", "split64", "stride2", "zp"])
@pytest.mark.parametrize("math", ["move", "add", "sub", "shl"])
def test_fixed_voice_shreds_lift_whole(layout, math):
    a = _fixed(layout, math)
    body = _lift("shred_%s_%s" % (layout, math), a, _data(layout))
    _canonical(body)


@pytest.mark.parametrize("layout", ["split3", "split64"])
@pytest.mark.parametrize("order", ["lofirst", "hifirst"])
def test_voice_loop_shreds_lift_whole(layout, order):
    """Per-voice loop: entry X, voice offset Y, both halves stored indexed."""
    lo, hi, _m = _lo_hi(layout, "absx")
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 2).label("lp")
    a.i("LDY", "absx", VT)
    first, second = ((lo, SID), (hi, SID + 1)) if order == "lofirst" else ((hi, SID + 1), (lo, SID))
    a.i("LDA", "absx", first[0]).i("STA", "absy", first[1])
    a.i("LDA", "absx", second[0]).i("STA", "absy", second[1])
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    outs = tuple(SID + k for k in range(0x19))
    body = _lift("shred_loop_%s_%s" % (layout, order), a, _data(layout), outs)
    _canonical(body)


@pytest.mark.parametrize("transport", ["stack", "spill", "lax"])
def test_transported_shreds_lift_whole(transport):
    """The datum rides the stack, a RAM spill, or an illegal load, and lands whole."""
    a = G.Asm(G.ORG)
    if transport == "stack":
        a.i("LDA", "abs", T).i("PHA")
        a.i("LDA", "abs", T + 3).i("PHA")
        a.i("PLA").i("STA", "abs", SID + 1)
        a.i("PLA").i("STA", "abs", SID)
    elif transport == "spill":
        a.i("LDA", "abs", T).i("STA", "abs", SPL)
        a.i("LDA", "abs", T + 3).i("STA", "abs", SPL + 1)
        a.i("LDA", "abs", SPL).i("STA", "abs", SID)
        a.i("LDA", "abs", SPL + 1).i("STA", "abs", SID + 1)
    else:
        a.i("LAX", "abs", T).i("STA", "abs", SID)
        a.i("LAX", "abs", T + 3).i("STA", "abs", SID + 1)
    a.i("RTS")
    body = _lift("shred_%s" % transport, a, _data("split3"))
    _canonical(body)
