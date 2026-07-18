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


# ---- addressing modes --------------------------------------------------------
MODE_LEN = {
    "imm": 2,
    "zp": 2,
    "zpx": 2,
    "zpy": 2,
    "abs": 3,
    "absx": 3,
    "absy": 3,
    "indx": 2,
    "indy": 2,
    "acc": 1,
    "impl": 1,
    "rel": 2,
    "ind": 3,
}
MEM_MODES = {"zp", "zpx", "zpy", "abs", "absx", "absy", "indx", "indy"}


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


# ---- opcode table: opcode -> (mnemonic, mode) --------------------------------
def _build_ops():
    t = {}
    L = [
        (0x00, "BRK", "impl"),
        (0x08, "PHP", "impl"),
        (0x28, "PLP", "impl"),
        (0x48, "PHA", "impl"),
        (0x68, "PLA", "impl"),
        (0x40, "RTI", "impl"),
        (0x60, "RTS", "impl"),
        (0x20, "JSR", "abs"),
        (0x4C, "JMP", "abs"),
        (0x6C, "JMP", "ind"),
        (0xEA, "NOP", "impl"),
        (0x18, "CLC", "impl"),
        (0x38, "SEC", "impl"),
        (0x58, "CLI", "impl"),
        (0x78, "SEI", "impl"),
        (0xB8, "CLV", "impl"),
        (0xD8, "CLD", "impl"),
        (0xF8, "SED", "impl"),
        (0xAA, "TAX", "impl"),
        (0xA8, "TAY", "impl"),
        (0x8A, "TXA", "impl"),
        (0x98, "TYA", "impl"),
        (0xBA, "TSX", "impl"),
        (0x9A, "TXS", "impl"),
        (0xCA, "DEX", "impl"),
        (0x88, "DEY", "impl"),
        (0xE8, "INX", "impl"),
        (0xC8, "INY", "impl"),
    ]
    for op, mn, md in L:
        t[op] = (mn, md)
    for op, md in (
        (0x10, "BPL"),
        (0x30, "BMI"),
        (0x50, "BVC"),
        (0x70, "BVS"),
        (0x90, "BCC"),
        (0xB0, "BCS"),
        (0xD0, "BNE"),
        (0xF0, "BEQ"),
    ):
        t[op] = (md, "rel")
    # group opcodes by (base, mode) low-bits for the ALU/load/store families

    def add(mn, entries):
        for op, md in entries:
            t[op] = (mn, md)

    add(
        "ORA",
        [
            (0x09, "imm"),
            (0x05, "zp"),
            (0x15, "zpx"),
            (0x0D, "abs"),
            (0x1D, "absx"),
            (0x19, "absy"),
            (0x01, "indx"),
            (0x11, "indy"),
        ],
    )
    add(
        "AND",
        [
            (0x29, "imm"),
            (0x25, "zp"),
            (0x35, "zpx"),
            (0x2D, "abs"),
            (0x3D, "absx"),
            (0x39, "absy"),
            (0x21, "indx"),
            (0x31, "indy"),
        ],
    )
    add(
        "EOR",
        [
            (0x49, "imm"),
            (0x45, "zp"),
            (0x55, "zpx"),
            (0x4D, "abs"),
            (0x5D, "absx"),
            (0x59, "absy"),
            (0x41, "indx"),
            (0x51, "indy"),
        ],
    )
    add(
        "ADC",
        [
            (0x69, "imm"),
            (0x65, "zp"),
            (0x75, "zpx"),
            (0x6D, "abs"),
            (0x7D, "absx"),
            (0x79, "absy"),
            (0x61, "indx"),
            (0x71, "indy"),
        ],
    )
    add(
        "SBC",
        [
            (0xE9, "imm"),
            (0xE5, "zp"),
            (0xF5, "zpx"),
            (0xED, "abs"),
            (0xFD, "absx"),
            (0xF9, "absy"),
            (0xE1, "indx"),
            (0xF1, "indy"),
        ],
    )
    add(
        "CMP",
        [
            (0xC9, "imm"),
            (0xC5, "zp"),
            (0xD5, "zpx"),
            (0xCD, "abs"),
            (0xDD, "absx"),
            (0xD9, "absy"),
            (0xC1, "indx"),
            (0xD1, "indy"),
        ],
    )
    add(
        "LDA",
        [
            (0xA9, "imm"),
            (0xA5, "zp"),
            (0xB5, "zpx"),
            (0xAD, "abs"),
            (0xBD, "absx"),
            (0xB9, "absy"),
            (0xA1, "indx"),
            (0xB1, "indy"),
        ],
    )
    add(
        "STA",
        [
            (0x85, "zp"),
            (0x95, "zpx"),
            (0x8D, "abs"),
            (0x9D, "absx"),
            (0x99, "absy"),
            (0x81, "indx"),
            (0x91, "indy"),
        ],
    )
    add("LDX", [(0xA2, "imm"), (0xA6, "zp"), (0xB6, "zpy"), (0xAE, "abs"), (0xBE, "absy")])
    add("LDY", [(0xA0, "imm"), (0xA4, "zp"), (0xB4, "zpx"), (0xAC, "abs"), (0xBC, "absx")])
    add("STX", [(0x86, "zp"), (0x96, "zpy"), (0x8E, "abs")])
    add("STY", [(0x84, "zp"), (0x94, "zpx"), (0x8C, "abs")])
    add("CPX", [(0xE0, "imm"), (0xE4, "zp"), (0xEC, "abs")])
    add("CPY", [(0xC0, "imm"), (0xC4, "zp"), (0xCC, "abs")])
    add("BIT", [(0x24, "zp"), (0x2C, "abs")])
    add("ASL", [(0x0A, "acc"), (0x06, "zp"), (0x16, "zpx"), (0x0E, "abs"), (0x1E, "absx")])
    add("LSR", [(0x4A, "acc"), (0x46, "zp"), (0x56, "zpx"), (0x4E, "abs"), (0x5E, "absx")])
    add("ROL", [(0x2A, "acc"), (0x26, "zp"), (0x36, "zpx"), (0x2E, "abs"), (0x3E, "absx")])
    add("ROR", [(0x6A, "acc"), (0x66, "zp"), (0x76, "zpx"), (0x6E, "abs"), (0x7E, "absx")])
    add("INC", [(0xE6, "zp"), (0xF6, "zpx"), (0xEE, "abs"), (0xFE, "absx")])
    add("DEC", [(0xC6, "zp"), (0xD6, "zpx"), (0xCE, "abs"), (0xDE, "absx")])
    # ---- illegals (NMS-sourced) ----
    rmw = {0x00: "SLO", 0x20: "RLA", 0x40: "SRE", 0x60: "RRA", 0xC0: "DCP", 0xE0: "ISC"}
    for base, mn in rmw.items():
        for lowoff, md in (
            (0x03, "indx"),
            (0x07, "zp"),
            (0x0F, "abs"),
            (0x13, "indy"),
            (0x17, "zpx"),
            (0x1B, "absy"),
            (0x1F, "absx"),
        ):
            t[base + lowoff] = (mn, md)
    add("SAX", [(0x83, "indx"), (0x87, "zp"), (0x8F, "abs"), (0x97, "zpy")])
    add(
        "LAX",
        [
            (0xA3, "indx"),
            (0xA7, "zp"),
            (0xAF, "abs"),
            (0xB3, "indy"),
            (0xB7, "zpy"),
            (0xBF, "absy"),
        ],
    )
    add("LXA", [(0xAB, "imm")])  # $AB magic-constant variant (NMS p.53), != memory LAX
    add("ANC", [(0x0B, "imm"), (0x2B, "imm")])
    add("ALR", [(0x4B, "imm")])
    add("ARR", [(0x6B, "imm")])
    add("SBX", [(0xCB, "imm")])
    add("SBC", [(0xEB, "imm")])
    add("ANE", [(0x8B, "imm")])
    add("LAS", [(0xBB, "absy")])
    add("SHA", [(0x9F, "absy"), (0x93, "indy")])
    add("SHX", [(0x9E, "absy")])
    add("SHY", [(0x9C, "absx")])
    add("TAS", [(0x9B, "absy")])
    # NOP illegals (byte-skip; memory modes still perform a read -- NMS p.40)
    for op in (0x1A, 0x3A, 0x5A, 0x7A, 0xDA, 0xFA):
        t[op] = ("NOP", "impl")
    for op in (0x80, 0x82, 0x89, 0xC2, 0xE2):
        t[op] = ("NOP", "imm")
    for op in (0x04, 0x44, 0x64):
        t[op] = ("NOP", "zp")
    for op in (0x14, 0x34, 0x54, 0x74, 0xD4, 0xF4):
        t[op] = ("NOP", "zpx")
    t[0x0C] = ("NOP", "abs")
    for op in (0x1C, 0x3C, 0x5C, 0x7C, 0xDC, 0xFC):
        t[op] = ("NOP", "absx")
    for op in (0x02, 0x12, 0x22, 0x32, 0x42, 0x52, 0x62, 0x72, 0x92, 0xB2, 0xD2, 0xF2):
        t[op] = ("JAM", "impl")
    return t


OPS = _build_ops()

# The set of opcode bytes that are undocumented (illegal) NMOS instructions.
_ILLEGAL_COMBO = {
    "SLO",
    "RLA",
    "SRE",
    "RRA",
    "DCP",
    "ISC",
    "SAX",
    "LAX",
    "LXA",
    "ANC",
    "ALR",
    "ARR",
    "SBX",
    "ANE",
    "LAS",
    "SHA",
    "SHX",
    "SHY",
    "TAS",
}


def _is_illegal(op):
    """True if opcode byte ``op`` is an undocumented NMOS instruction."""
    mn, _ = OPS[op]
    if mn in _ILLEGAL_COMBO or mn == "JAM":
        return True
    if mn == "SBC" and op == 0xEB:  # $EB is the illegal SBC alias
        return True
    if mn == "NOP" and op != 0xEA:  # every NOP but $EA is illegal
        return True
    return False


ILLEGAL_OPCODES = frozenset(o for o in OPS if _is_illegal(o))


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
