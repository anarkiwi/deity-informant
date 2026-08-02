"""Every 6502 shape that writes one 16-bit quantity as two bytes, enumerated.

The axes are the machine's, not the pass's: lane addressing, adjacent or split
lanes, add or subtract, where the step comes from, how the carry crosses. Gate FP
is the oracle, so a case states that the record cannot move, not what was lifted.
"""

import pytest

from deity_informant import frameprog
from deity_informant import frameval
import _fuzzgen as G

from test_frameprog import _fuzz_model

OUT = 0xD404  # sid.v1.ctrl/attack_decay: observable, and not a lo/hi pair
ZP, ABS = 0x10, G.CNT + 0x20
STEP = G.TBL + 0x10
ZPSTEP = 0x60

_LANES = {  # mode -> (lo base, hi adjacent, hi split, index register)
    "zp": (ZP, ZP + 1, ZP + 0x40, None),
    "zpx": (ZP, ZP + 1, ZP + 0x40, "X"),
    "abs": (ABS, ABS + 1, ABS + 0x40, None),
    "absx": (ABS, ABS + 1, ABS + 0x40, "X"),
    "absy": (ABS, ABS + 1, ABS + 0x40, "Y"),
}
_INCABLE = ("zp", "zpx", "abs", "absx")  # INC/DEC have no ,Y form
_STEPS = ("imm", "zpstep", "abs", "absx", "absy")


def _mode(kind, reg):
    """The assembler mode for a lane or operand of ``kind`` indexed by ``reg``."""
    if reg is None:
        return "zp" if kind == "zp" else "abs"
    if kind == "zp":
        return "zpx"
    return "absx" if reg == "X" else "absy"


def _acc(a, mn, kind, base, reg):
    return a.i(mn, _mode(kind, reg), base)


def _step_op(a, mn, step):
    """The ADC/SBC that brings the step in, in whichever mode names it."""
    if step == "imm":
        return a.i(mn, "imm", 0x37)
    if step == "zpstep":
        return a.i(mn, "zp", ZPSTEP)
    return a.i(mn, step, STEP)


def _shape(lanes, split, op, step, carry):
    """The player for one shape, or None where the 6502 has no such instruction."""
    lo, hi_adj, hi_split, reg = _LANES[lanes]
    kind = "zp" if lanes.startswith("zp") else "abs"
    hi = hi_split if split else hi_adj
    if carry in ("branch", "rmw") and lanes not in _INCABLE:
        return None
    if op in ("shl", "shr", "inc", "dec") and (carry != "rmw" or step != "imm"):
        return None
    if op in ("and", "ora", "eor") and carry != "withzero":
        return None
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0).i("LDY", "imm", 0)
    if op in ("shl", "shr"):
        _rmw_shift(a, op, kind, lo, hi, reg)
    elif op in ("inc", "dec"):
        _rmw_count(a, op, kind, lo, hi, reg)
    elif op in ("and", "ora", "eor"):
        _bitwise(a, op, kind, lo, hi, reg, step)
    else:
        _carry_chain(a, op, kind, lo, hi, reg, step, carry)
    _acc(a, "LDA", kind, lo, reg)
    a.i("STA", "abs", OUT)
    _acc(a, "LDA", kind, hi, reg)
    a.i("STA", "abs", OUT + 1)
    return a.i("RTS")


def _carry_chain(a, op, kind, lo, hi, reg, step, carry):
    """``ADC``/``SBC`` on the lo lane, the carry crossing into the hi lane."""
    add = op == "adc"
    a.i("CLC" if add else "SEC")
    _acc(a, "LDA", kind, lo, reg)
    _step_op(a, "ADC" if add else "SBC", step)
    _acc(a, "STA", kind, lo, reg)
    if carry == "withzero":
        _acc(a, "LDA", kind, hi, reg)
        a.i("ADC" if add else "SBC", "imm", 0x00)
        _acc(a, "STA", kind, hi, reg)
    elif carry == "wordstep":
        _acc(a, "LDA", kind, hi, reg)
        a.i("ADC" if add else "SBC", "abs", STEP + 1)
        _acc(a, "STA", kind, hi, reg)
    else:
        a.i("BCC" if add else "BCS", "rel", ("L", "skip"))
        _acc(a, "INC" if add else "DEC", kind, hi, reg)
        a.label("skip")


def _rmw_shift(a, op, kind, lo, hi, reg):
    """A 16-bit shift: the bit leaves one lane through the carry and enters the other."""
    if op == "shl":
        _acc(a, "ASL", kind, lo, reg)
        _acc(a, "ROL", kind, hi, reg)
    else:
        _acc(a, "LSR", kind, hi, reg)
        _acc(a, "ROR", kind, lo, reg)


def _rmw_count(a, op, kind, lo, hi, reg):
    """A 16-bit increment or decrement, the hi lane predicated on the lo lane's wrap."""
    if op == "inc":
        _acc(a, "INC", kind, lo, reg)
        a.i("BNE", "rel", ("L", "skip"))
        _acc(a, "INC", kind, hi, reg)
    else:
        _acc(a, "LDA", kind, lo, reg)
        a.i("BNE", "rel", ("L", "skip"))
        _acc(a, "DEC", kind, hi, reg)
        a.label("skip")
        _acc(a, "DEC", kind, lo, reg)
        return
    a.label("skip")


def _bitwise(a, op, kind, lo, hi, reg, step):
    """A 16-bit AND/ORA/EOR: no carry crosses, but both lanes take one word operand."""
    mn = {"and": "AND", "ora": "ORA", "eor": "EOR"}[op]
    _acc(a, "LDA", kind, lo, reg)
    _step_op(a, mn, step)
    _acc(a, "STA", kind, lo, reg)
    _acc(a, "LDA", kind, hi, reg)
    a.i(mn, "abs", STEP + 1)
    _acc(a, "STA", kind, hi, reg)


_OPS = ("adc", "sbc", "shl", "shr", "inc", "dec", "and", "ora", "eor")
_CARRY = {  # the carry forms each operation actually has on this machine
    "adc": ("withzero", "branch", "wordstep"),
    "sbc": ("withzero", "branch", "wordstep"),
    "shl": ("rmw",),
    "shr": ("rmw",),
    "inc": ("rmw",),
    "dec": ("rmw",),
    "and": ("withzero",),
    "ora": ("withzero",),
    "eor": ("withzero",),
}
_CASES = [
    (lanes, split, op, step, carry)
    for lanes in _LANES
    for split in (False, True)
    for op in _OPS
    for step in (_STEPS if op not in ("shl", "shr", "inc", "dec") else ("imm",))
    for carry in _CARRY[op]
]


def _ident(c):
    return "%s-%s-%s-%s-%s" % (c[0], "split" if c[1] else "adj", c[2], c[3], c[4])


def build(case):
    """``(model, program)`` for one enumerated shape, else None where it has none."""
    asm = _shape(*case)
    if asm is None:
        return None
    data = {
        STEP: 0x37,
        STEP + 1: 0x02,
        ZPSTEP: 0x05,
        ZP: 0xF0,
        ZP + 1: 0x01,
        ABS: 0xF0,
        ABS + 1: 0x01,
    }
    player = G.Player(
        _ident(case), G.ORG, asm.assemble(), {OUT, OUT + 1}, {"indexed"}, data=data, frames=8
    )
    model = _fuzz_model(player)
    return model, frameprog.program(model)


@pytest.mark.parametrize("case", _CASES, ids=_ident)
def test_every_two_byte_shape_projects(case):
    """Gate FP is the law: whatever rung (d2) makes of a shape, the record holds."""
    got = build(case)
    if got is None:
        pytest.skip("no such 6502 instruction for this lane mode")
    model, prog = got
    assert frameval.gate_fp(model, 8, prog) is None
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
