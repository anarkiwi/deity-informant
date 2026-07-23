"""Complete 6502/6510 -> raw P-Code lifter (stand-in for Ghidra Instruction.getPcode()).

First-class illegal-opcode support: every documented NMOS illegal is lifted to
genuine P-Code micro-ops (SLO=ASL+ORA, RLA=ROL+AND, SRE=LSR+EOR, RRA=ROR+ADC,
DCP=DEC+CMP, ISC=INC+SBC, plus SAX/LAX/LXA/ANC/ALR/ARR/SBX/LAS/ANE and the SH*
family), with correct cycle counts. JAM/KIL halt. An unimplemented opcode is a
hard error, never a silent skip.

Semantics and cycle counts follow "No More Secrets - NMOS 6510 Unintended
Opcodes" (v0.91); see docs/illegal-opcodes.md for the per-opcode citations. The
static cycle tables below are frozen (no runtime py65 dependency) and were
cross-checked against the NMS reference chart.
"""

from __future__ import annotations

from jennings.opcodes import OPCODES as OPS, MODE_LEN, MEM_MODES, ILLEGAL_OPCODES

__all__ = [
    "OPS",
    "MODE_LEN",
    "MEM_MODES",
    "ILLEGAL_OPCODES",
    "MAGIC",
    "CYCLETIME",
    "EXTRACYCLES",
    "lift",
]

# Magic constant for the unstable "magic constant" group (ANE $8B, LXA $AB).
# CONST is chip-/temperature-dependent; NMS lists common values $00/$FF/$EE.
# $EE matches the sidplayfp oracle the interpreter was validated against.
MAGIC = 0xEE


# ---- varnodes: [space, offset, size]; space c(const) r(reg) u(unique) --------
def C(v, sz=1):
    return ["c", v & ((1 << (8 * sz)) - 1), sz]


A = ["r", 0, 1]
X = ["r", 1, 1]
Y = ["r", 2, 1]
SP = ["r", 3, 1]
FC = ["r", 8, 1]
FZ = ["r", 9, 1]
FI = ["r", 10, 1]
FD = ["r", 11, 1]
FB = ["r", 12, 1]
FV = ["r", 13, 1]
FN = ["r", 14, 1]

# P (status) register NV-BDIZC as (reg-file index, bit); shared by VM/recorder/lifter.
STATUS_BITS = ((8, 0), (9, 1), (10, 2), (11, 3), (13, 6), (14, 7))


class Emit:
    __slots__ = ("ops", "_u")

    def __init__(self):
        self.ops = []
        self._u = 0

    def tmp(self, sz=1):
        v = ["u", self._u, sz]
        self._u += sz
        return v

    def op(self, mn, out, *ins):
        self.ops.append([mn, out, list(ins)])
        return out

    def nz(self, val):
        self.op("INT_EQUAL", FZ, val, C(0))
        self.op("INT_NOTEQUAL", FN, self.op("INT_AND", self.tmp(), val, C(0x80)), C(0))


# ---- shared ALU micro-ops (reused by legal AND illegal opcodes) --------------
def _adc(e, v):
    s1 = e.op("INT_ADD", e.tmp(), A, v)
    c1 = e.op("INT_CARRY", e.tmp(), A, v)
    r = e.op("INT_ADD", e.tmp(), s1, FC)
    c2 = e.op("INT_CARRY", e.tmp(), s1, FC)
    axv = e.op("INT_XOR", e.tmp(), A, v)
    axr = e.op("INT_XOR", e.tmp(), A, r)
    nax = e.op("INT_XOR", e.tmp(), axv, C(0xFF))
    vv = e.op("INT_AND", e.tmp(), e.op("INT_AND", e.tmp(), nax, axr), C(0x80))
    e.op("INT_NOTEQUAL", FV, vv, C(0))
    e.op("INT_OR", FC, c1, c2)
    e.op("COPY", A, r)
    e.nz(A)


def _sbc(e, v):
    nc = e.op("INT_SUB", e.tmp(), C(1), FC)
    vb = e.op("INT_ADD", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), v), e.op("INT_ZEXT", e.tmp(2), nc))
    aext = e.op("INT_ZEXT", e.tmp(2), A)
    e.op("INT_LESSEQUAL", FC, vb, aext)
    r = e.op("INT_SUB", e.tmp(), A, e.op("INT_ADD", e.tmp(), v, nc))
    axv = e.op("INT_XOR", e.tmp(), A, v)
    axr = e.op("INT_XOR", e.tmp(), A, r)
    vv = e.op("INT_AND", e.tmp(), e.op("INT_AND", e.tmp(), axv, axr), C(0x80))
    e.op("INT_NOTEQUAL", FV, vv, C(0))
    e.op("COPY", A, r)
    e.nz(A)


def _cmp(e, reg, v):
    e.op("INT_LESSEQUAL", FC, v, reg)
    e.nz(e.op("INT_SUB", e.tmp(), reg, v))


def _asl(e, val):
    e.op("INT_NOTEQUAL", FC, e.op("INT_AND", e.tmp(), val, C(0x80)), C(0))
    return e.op("INT_AND", e.tmp(), e.op("INT_LEFT", e.tmp(), val, C(1)), C(0xFF))


def _lsr(e, val):
    e.op("COPY", FC, e.op("INT_AND", e.tmp(), val, C(1)))
    return e.op("INT_RIGHT", e.tmp(), val, C(1))


def _rol(e, val):
    oldc = e.op("COPY", e.tmp(), FC)
    e.op("INT_NOTEQUAL", FC, e.op("INT_AND", e.tmp(), val, C(0x80)), C(0))
    return e.op(
        "INT_AND",
        e.tmp(),
        e.op("INT_OR", e.tmp(), e.op("INT_LEFT", e.tmp(), val, C(1)), oldc),
        C(0xFF),
    )


def _ror(e, val):
    oldc = e.op("COPY", e.tmp(), FC)
    e.op("COPY", FC, e.op("INT_AND", e.tmp(), val, C(1)))
    return e.op(
        "INT_OR",
        e.tmp(),
        e.op("INT_RIGHT", e.tmp(), val, C(1)),
        e.op("INT_LEFT", e.tmp(), oldc, C(7)),
    )


# ---- addressing modes (MODE_LEN / MEM_MODES canonical in jennings.opcodes) ----
def _ea(e, mem, pc, mode, pmap=None):
    """Emit P-Code computing the effective address; return its varnode.

    ``pmap`` (optional) collects ``{id(const): (srcs, fn)}`` recording which
    operand-derived address constants come from which instruction-byte offsets;
    purely additive (emitted ops and return value are identical without it).
    """
    lo = mem[(pc + 1) & 0xFFFF]
    hi = mem[(pc + 2) & 0xFFFF]
    word = lo | (hi << 8)

    def reg(vn, srcs, fn):
        if pmap is not None:
            pmap[id(vn)] = (srcs, fn)
        return vn

    if mode == "zp":
        return reg(C(lo, 2), [1], "id")
    if mode == "zpx":
        return e.op("INT_ZEXT", e.tmp(2), e.op("INT_ADD", e.tmp(), reg(C(lo), [1], "id"), X))
    if mode == "zpy":
        return e.op("INT_ZEXT", e.tmp(2), e.op("INT_ADD", e.tmp(), reg(C(lo), [1], "id"), Y))
    if mode == "abs":
        return reg(C(word, 2), [1, 2], "word")
    if mode == "absx":
        base = reg(C(word, 2), [1, 2], "word")
        return e.op("INT_ADD", e.tmp(2), base, e.op("INT_ZEXT", e.tmp(2), X))
    if mode == "absy":
        base = reg(C(word, 2), [1, 2], "word")
        return e.op("INT_ADD", e.tmp(2), base, e.op("INT_ZEXT", e.tmp(2), Y))
    if mode == "indy":
        plo = e.op("LOAD", e.tmp(), reg(C(lo, 2), [1], "id"))
        phi = e.op("LOAD", e.tmp(), reg(C((lo + 1) & 0xFF, 2), [1], "hi1"))
        base = e.op(
            "INT_OR",
            e.tmp(2),
            e.op("INT_LEFT", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), phi), C(8)),
            e.op("INT_ZEXT", e.tmp(2), plo),
        )
        return e.op("INT_ADD", e.tmp(2), base, e.op("INT_ZEXT", e.tmp(2), Y))
    if mode == "indx":
        pa = e.op("INT_ADD", e.tmp(), reg(C(lo), [1], "id"), X)
        pa1 = e.op("INT_ADD", e.tmp(), pa, C(1))
        plo = e.op("LOAD", e.tmp(), e.op("INT_ZEXT", e.tmp(2), pa))
        phi = e.op("LOAD", e.tmp(), e.op("INT_ZEXT", e.tmp(2), pa1))
        return e.op(
            "INT_OR",
            e.tmp(2),
            e.op("INT_LEFT", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), phi), C(8)),
            e.op("INT_ZEXT", e.tmp(2), plo),
        )
    raise ValueError(mode)


# ---- cycle tables (frozen; NMS-verified; no runtime py65 dependency) ---------
# Base 6502 counts with the RMW/illegal overrides folded in; EXTRACYCLES is the
# page-cross / branch-taken penalty added by the interpreter when it applies.
CYCLETIME = (
    7,
    6,
    0,
    8,
    3,
    3,
    5,
    5,
    3,
    2,
    2,
    2,
    4,
    4,
    6,
    6,
    2,
    5,
    0,
    8,
    4,
    4,
    6,
    6,
    2,
    4,
    2,
    7,
    4,
    4,
    7,
    7,
    6,
    6,
    0,
    8,
    3,
    3,
    5,
    5,
    4,
    2,
    2,
    2,
    4,
    4,
    6,
    6,
    2,
    5,
    0,
    8,
    4,
    4,
    6,
    6,
    2,
    4,
    2,
    7,
    4,
    4,
    7,
    7,
    6,
    6,
    0,
    8,
    3,
    3,
    5,
    5,
    3,
    2,
    2,
    2,
    3,
    4,
    6,
    6,
    2,
    5,
    0,
    8,
    4,
    4,
    6,
    6,
    2,
    4,
    2,
    7,
    4,
    4,
    7,
    7,
    6,
    6,
    0,
    8,
    3,
    3,
    5,
    5,
    4,
    2,
    2,
    2,
    5,
    4,
    6,
    6,
    2,
    5,
    0,
    8,
    4,
    4,
    6,
    6,
    2,
    4,
    2,
    7,
    4,
    4,
    7,
    7,
    2,
    6,
    2,
    6,
    3,
    3,
    3,
    3,
    2,
    2,
    2,
    2,
    4,
    4,
    4,
    4,
    2,
    6,
    0,
    6,
    4,
    4,
    4,
    4,
    2,
    5,
    2,
    5,
    5,
    5,
    5,
    5,
    2,
    6,
    2,
    6,
    3,
    3,
    3,
    3,
    2,
    2,
    2,
    2,
    4,
    4,
    4,
    4,
    2,
    5,
    0,
    5,
    4,
    4,
    4,
    4,
    2,
    4,
    2,
    4,
    4,
    4,
    4,
    4,
    2,
    6,
    2,
    8,
    3,
    3,
    5,
    5,
    2,
    2,
    2,
    2,
    4,
    4,
    3,
    6,
    2,
    5,
    0,
    8,
    4,
    4,
    6,
    6,
    2,
    4,
    2,
    7,
    4,
    4,
    7,
    7,
    2,
    6,
    2,
    8,
    3,
    3,
    5,
    5,
    2,
    2,
    2,
    2,
    4,
    4,
    6,
    6,
    2,
    5,
    0,
    8,
    4,
    4,
    6,
    6,
    2,
    4,
    2,
    7,
    4,
    4,
    7,
    7,
)

EXTRACYCLES = (
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    1,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    1,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    1,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    1,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    1,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    1,
    1,
    1,
    1,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    1,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    1,
    1,
    0,
    0,
)


# ---- the lifter --------------------------------------------------------------
def lift(mem, pc):
    op = mem[pc]
    if op not in OPS:
        raise NotImplementedError(f"opcode {op:02X} at {pc:04X}")
    mn, mode = OPS[op]
    length = MODE_LEN[mode]
    e = Emit()
    ctrl = ("next",)
    pmap = {}
    eav = _ea(e, mem, pc, mode, pmap) if mode in MEM_MODES else None
    imm = mem[(pc + 1) & 0xFFFF]

    def rd():
        if mode == "imm":
            vn = C(imm)
            pmap[id(vn)] = ([1], "id")
            return vn
        if mode == "acc":
            return A
        return e.op("LOAD", e.tmp(), eav)

    if mn in ("LDA", "LDX", "LDY"):
        r = {"LDA": A, "LDX": X, "LDY": Y}[mn]
        e.op("COPY", r, rd())
        e.nz(r)
    elif mn in ("STA", "STX", "STY"):
        e.op("STORE", None, eav, {"STA": A, "STX": X, "STY": Y}[mn])
    elif mn == "ORA":
        e.op("INT_OR", A, A, rd())
        e.nz(A)
    elif mn == "AND":
        e.op("INT_AND", A, A, rd())
        e.nz(A)
    elif mn == "EOR":
        e.op("INT_XOR", A, A, rd())
        e.nz(A)
    elif mn == "ADC":
        _adc(e, rd())
    elif mn == "SBC":
        _sbc(e, rd())
    elif mn == "CMP":
        _cmp(e, A, rd())
    elif mn == "CPX":
        _cmp(e, X, rd())
    elif mn == "CPY":
        _cmp(e, Y, rd())
    elif mn == "BIT":
        m = rd()
        e.op("INT_EQUAL", FZ, e.op("INT_AND", e.tmp(), A, m), C(0))
        e.op("INT_NOTEQUAL", FN, e.op("INT_AND", e.tmp(), m, C(0x80)), C(0))
        e.op("INT_NOTEQUAL", FV, e.op("INT_AND", e.tmp(), m, C(0x40)), C(0))
    elif mn in ("ASL", "LSR", "ROL", "ROR"):
        fn = {"ASL": _asl, "LSR": _lsr, "ROL": _rol, "ROR": _ror}[mn]
        if mode == "acc":
            r = fn(e, A)
            e.op("COPY", A, r)
            e.nz(A)
        else:
            r = fn(e, e.op("LOAD", e.tmp(), eav))
            e.op("STORE", None, eav, r)
            e.nz(r)
    elif mn in ("INC", "DEC"):
        cur = e.op("LOAD", e.tmp(), eav)
        r = e.op("INT_ADD" if mn == "INC" else "INT_SUB", e.tmp(), cur, C(1))
        e.op("STORE", None, eav, r)
        e.nz(r)
    elif mn in ("INX", "DEX", "INY", "DEY"):
        r = {"INX": X, "DEX": X, "INY": Y, "DEY": Y}[mn]
        e.op("INT_ADD" if mn in ("INX", "INY") else "INT_SUB", r, r, C(1))
        e.nz(r)
    elif mn in ("TAX", "TAY", "TXA", "TYA", "TSX"):
        src, dst = {"TAX": (A, X), "TAY": (A, Y), "TXA": (X, A), "TYA": (Y, A), "TSX": (SP, X)}[mn]
        e.op("COPY", dst, src)
        e.nz(dst)
    elif mn == "TXS":
        e.op("COPY", SP, X)
    elif mn in ("CLC", "SEC"):
        e.op("COPY", FC, C(1 if mn == "SEC" else 0))
    elif mn in ("CLI", "SEI"):
        e.op("COPY", FI, C(1 if mn == "SEI" else 0))
    elif mn in ("CLD", "SED"):
        e.op("COPY", FD, C(1 if mn == "SED" else 0))
    elif mn == "CLV":
        e.op("COPY", FV, C(0))
    elif mn == "NOP":
        if mode in MEM_MODES:
            e.op("LOAD", e.tmp(), eav)  # NMS p.40: NOP zp/abs still reads memory
    elif mn in ("PHA", "PHP"):
        if mn == "PHP":
            val = C(0x30)  # B + unused bits set on a pushed status byte
            for idx, sh in STATUS_BITS:
                f = ["r", idx, 1]
                val = e.op(
                    "INT_OR", e.tmp(), val, f if sh == 0 else e.op("INT_LEFT", e.tmp(), f, C(sh))
                )
        else:
            val = A
        addr = e.op("INT_OR", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), SP), C(0x100, 2))
        e.op("STORE", None, addr, val)
        e.op("INT_SUB", SP, SP, C(1))
    elif mn in ("PLA", "PLP"):
        e.op("INT_ADD", SP, SP, C(1))
        addr = e.op("INT_OR", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), SP), C(0x100, 2))
        val = e.op("LOAD", e.tmp(), addr)
        if mn == "PLA":
            e.op("COPY", A, val)
            e.nz(A)
        else:
            for idx, sh in STATUS_BITS:
                src = val if sh == 0 else e.op("INT_RIGHT", e.tmp(), val, C(sh))
                e.op("INT_AND", ["r", idx, 1], src, C(1))
    elif mn in ("BPL", "BMI", "BVC", "BVS", "BCC", "BCS", "BNE", "BEQ"):
        flag, pol = {
            "BPL": (FN, 0),
            "BMI": (FN, 1),
            "BVC": (FV, 0),
            "BVS": (FV, 1),
            "BCC": (FC, 0),
            "BCS": (FC, 1),
            "BNE": (FZ, 0),
            "BEQ": (FZ, 1),
        }[mn]
        tgt = (pc + 2 + (imm - 256 if imm & 0x80 else imm)) & 0xFFFF
        ctrl = ("br", flag, pol, tgt, (pc + 2) & 0xFFFF)
    elif mn == "JMP":
        ctrl = (
            ("jmpind", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8))
            if mode == "ind"
            else ("jmp", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8))
        )
    elif mn == "JSR":
        ctrl = ("jsr", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8))
    elif mn == "RTS":
        ctrl = ("rts",)
    elif mn == "RTI":
        ctrl = ("rti",)
    elif mn == "BRK":
        ctrl = ("brk",)
    elif mn == "JAM":
        ctrl = ("jam",)
    # ---- illegal combos ----
    elif mn in ("SLO", "RLA", "SRE", "RRA"):
        shift = {"SLO": _asl, "RLA": _rol, "SRE": _lsr, "RRA": _ror}[mn]
        r = shift(e, e.op("LOAD", e.tmp(), eav))
        e.op("STORE", None, eav, r)
        if mn == "SLO":
            e.op("INT_OR", A, A, r)
            e.nz(A)
        elif mn == "RLA":
            e.op("INT_AND", A, A, r)
            e.nz(A)
        elif mn == "SRE":
            e.op("INT_XOR", A, A, r)
            e.nz(A)
        else:  # RRA = ROR then ADC
            _adc(e, r)
    elif mn == "DCP":
        r = e.op("INT_SUB", e.tmp(), e.op("LOAD", e.tmp(), eav), C(1))
        e.op("STORE", None, eav, r)
        _cmp(e, A, r)
    elif mn == "ISC":
        r = e.op("INT_ADD", e.tmp(), e.op("LOAD", e.tmp(), eav), C(1))
        e.op("STORE", None, eav, r)
        _sbc(e, r)
    elif mn == "SAX":
        e.op("STORE", None, eav, e.op("INT_AND", e.tmp(), A, X))
    elif mn == "LAX":
        v = rd()
        e.op("COPY", A, v)
        e.op("COPY", X, v)
        e.nz(A)
    elif mn == "LXA":  # $AB: A,X = (A | CONST) & imm  (NMS p.53, magic constant)
        v = e.op("INT_AND", e.tmp(), e.op("INT_OR", e.tmp(), A, C(MAGIC)), C(imm))
        e.op("COPY", A, v)
        e.op("COPY", X, v)
        e.nz(A)
    elif mn == "ANC":
        e.op("INT_AND", A, A, C(imm))
        e.nz(A)
        e.op("INT_NOTEQUAL", FC, e.op("INT_AND", e.tmp(), A, C(0x80)), C(0))
    elif mn == "ALR":
        e.op("INT_AND", A, A, C(imm))
        e.op("COPY", FC, e.op("INT_AND", e.tmp(), A, C(1)))
        e.op("INT_RIGHT", A, A, C(1))
        e.nz(A)
    elif mn == "ARR":
        e.op("INT_AND", A, A, C(imm))
        r = e.op(
            "INT_OR",
            e.tmp(),
            e.op("INT_RIGHT", e.tmp(), A, C(1)),
            e.op("INT_LEFT", e.tmp(), FC, C(7)),
        )
        e.op("COPY", A, r)
        e.nz(A)
        b6 = e.op("INT_AND", e.tmp(), e.op("INT_RIGHT", e.tmp(), r, C(6)), C(1))
        b5 = e.op("INT_AND", e.tmp(), e.op("INT_RIGHT", e.tmp(), r, C(5)), C(1))
        e.op("COPY", FC, b6)
        e.op("INT_XOR", FV, b6, b5)
    elif mn == "SBX":
        ax = e.op("INT_AND", e.tmp(), A, X)
        e.op("INT_LESSEQUAL", FC, C(imm), ax)
        e.op("INT_SUB", X, ax, C(imm))
        e.nz(X)
    elif mn == "LAS":
        v = e.op("INT_AND", e.tmp(), e.op("LOAD", e.tmp(), eav), SP)
        e.op("COPY", A, v)
        e.op("COPY", X, v)
        e.op("COPY", SP, v)
        e.nz(v)
    elif mn == "ANE":  # $8B: A = (A | CONST) & X & imm  (NMS p.51, magic constant)
        e.op(
            "INT_AND", A, e.op("INT_AND", e.tmp(), e.op("INT_OR", e.tmp(), A, C(MAGIC)), X), C(imm)
        )
        e.nz(A)
    elif mn in ("SHA", "SHX", "SHY", "TAS"):
        # Stable form: store reg & (high(base)+1) -- the address-high AND quirk
        # (NMS pp.43-50). Page-cross / RDY-drop-off instabilities are
        # non-deterministic and intentionally not modelled.
        if mode in ("absy", "absx"):
            base = mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8)
        else:  # indy ($93 SHA): base is the 16-bit pointer read from zero page
            zp = mem[(pc + 1) & 0xFFFF]
            base = mem[zp] | (mem[(zp + 1) & 0xFF] << 8)
        h1 = C(((base >> 8) + 1) & 0xFF)
        if mode in ("absy", "absx"):
            pmap[id(h1)] = ([2], "hi1")
        if mn == "SHA":
            val = e.op("INT_AND", e.tmp(), e.op("INT_AND", e.tmp(), A, X), h1)
        elif mn == "SHX":
            val = e.op("INT_AND", e.tmp(), X, h1)
        elif mn == "SHY":
            val = e.op("INT_AND", e.tmp(), Y, h1)
        else:  # TAS: SP = A & X, then store SP & (H+1)
            e.op("INT_AND", SP, A, X)
            val = e.op("INT_AND", e.tmp(), SP, h1)
        e.op("STORE", None, eav, val)
    else:
        raise NotImplementedError(mn)

    pen = None
    ex = EXTRACYCLES[op]
    if mode == "rel":
        pen = ("branch",)
    elif ex and mode == "absx":
        pen = ("ax", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8))
    elif ex and mode == "absy":
        pen = ("ay", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8))
    elif ex and mode == "indy":
        pen = ("iy", mem[(pc + 1) & 0xFFFF])
    prov = _provenance(e.ops, pmap, ctrl, op)
    stk = ctrl[0] if ctrl[0] in ("jsr", "brk", "rts", "rti") else None
    return {
        "ops": e.ops,
        "len": length,
        "cyc": CYCLETIME[op],
        "pen": pen,
        "ctrl": ctrl,
        "prov": prov,
        "stk": stk,
    }


def _provenance(ops, pmap, ctrl, op):
    """Byte-provenance metadata for the recorder (inert for other users).

    ``op0`` is the opcode byte (record-identity fold). ``ops`` links op-list
    const varnodes to instruction-byte offsets ``{(i, arg): (srcs, fn)}`` for
    residualization. ``ctrl`` describes an operand-derived control target
    ``(srcs, fn, value)`` for jmp/jsr/jmpind/branch when applicable.
    """
    opmap = {}
    for i, (_mn, _out, ins) in enumerate(ops):
        for j, vn in enumerate(ins):
            p = pmap.get(id(vn))
            if p is not None:
                opmap[(i, j)] = p
    ctrlp = None
    kind = ctrl[0]
    if kind in ("jmp", "jsr", "jmpind"):
        ctrlp = ([1, 2], "word", ctrl[1])
    elif kind == "br":
        ctrlp = ([1], "rel", ctrl[3])
    return {"op0": op, "ops": opmap, "ctrl": ctrlp}
