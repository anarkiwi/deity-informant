"""Complete 6502/6510 -> raw P-Code lifter + pure-Python P-Code interpreter.

First-class illegal-opcode support: every documented NMOS illegal is lifted to
genuine P-Code micro-ops (SLO=ASL+ORA, RLA=ROL+AND, SRE=LSR+EOR, RRA=ROR+ADC,
DCP=DEC+CMP, ISC=INC+SBC, plus SAX/LAX/ANC/ALR/ARR/SBX/LAS and the SH* family),
with correct cycle counts. JAM/KIL halt. There is NO stub for unknown opcodes --
an unimplemented opcode is a hard error, never a silent skip. Validated against
the sidplayfp oracle (py65 is NOT a valid reference: it stubs the RMW illegals).
"""
from __future__ import annotations

# ---- varnodes: [space, offset, size]; space c(const) r(reg) u(unique) --------
def C(v, sz=1):
    return ["c", v & ((1 << (8 * sz)) - 1), sz]

A = ["r", 0, 1]; X = ["r", 1, 1]; Y = ["r", 2, 1]; SP = ["r", 3, 1]
FC = ["r", 8, 1]; FZ = ["r", 9, 1]; FI = ["r", 10, 1]
FD = ["r", 11, 1]; FB = ["r", 12, 1]; FV = ["r", 13, 1]; FN = ["r", 14, 1]

XAA_MAGIC = 0xEE  # ANE/XAA analog constant (sidplayfp convention); rarely hit.


class Emit:
    __slots__ = ("ops", "_u")

    def __init__(self):
        self.ops = []; self._u = 0

    def tmp(self, sz=1):
        v = ["u", self._u, sz]; self._u += sz; return v

    def op(self, mn, out, *ins):
        self.ops.append([mn, out, list(ins)]); return out

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
    e.op("COPY", A, r); e.nz(A)


def _sbc(e, v):
    nc = e.op("INT_SUB", e.tmp(), C(1), FC)
    vb = e.op("INT_ADD", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), v),
              e.op("INT_ZEXT", e.tmp(2), nc))
    aext = e.op("INT_ZEXT", e.tmp(2), A)
    e.op("INT_LESSEQUAL", FC, vb, aext)
    r = e.op("INT_SUB", e.tmp(), A, e.op("INT_ADD", e.tmp(), v, nc))
    axv = e.op("INT_XOR", e.tmp(), A, v)
    axr = e.op("INT_XOR", e.tmp(), A, r)
    vv = e.op("INT_AND", e.tmp(), e.op("INT_AND", e.tmp(), axv, axr), C(0x80))
    e.op("INT_NOTEQUAL", FV, vv, C(0))
    e.op("COPY", A, r); e.nz(A)


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
    return e.op("INT_AND", e.tmp(),
                e.op("INT_OR", e.tmp(), e.op("INT_LEFT", e.tmp(), val, C(1)), oldc),
                C(0xFF))


def _ror(e, val):
    oldc = e.op("COPY", e.tmp(), FC)
    e.op("COPY", FC, e.op("INT_AND", e.tmp(), val, C(1)))
    return e.op("INT_OR", e.tmp(), e.op("INT_RIGHT", e.tmp(), val, C(1)),
                e.op("INT_LEFT", e.tmp(), oldc, C(7)))


# ---- addressing modes --------------------------------------------------------
MODE_LEN = {"imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "abs": 3, "absx": 3,
            "absy": 3, "indx": 2, "indy": 2, "acc": 1, "impl": 1, "rel": 2,
            "ind": 3}
MEM_MODES = {"zp", "zpx", "zpy", "abs", "absx", "absy", "indx", "indy"}


def _ea(e, mem, pc, mode):
    """Emit P-Code computing the effective address; return its varnode."""
    lo = mem[(pc + 1) & 0xFFFF]; hi = mem[(pc + 2) & 0xFFFF]; word = lo | (hi << 8)
    if mode == "zp":
        return C(lo, 2)
    if mode == "zpx":
        return e.op("INT_ZEXT", e.tmp(2), e.op("INT_ADD", e.tmp(), C(lo), X))
    if mode == "zpy":
        return e.op("INT_ZEXT", e.tmp(2), e.op("INT_ADD", e.tmp(), C(lo), Y))
    if mode == "abs":
        return C(word, 2)
    if mode == "absx":
        return e.op("INT_ADD", e.tmp(2), C(word, 2), e.op("INT_ZEXT", e.tmp(2), X))
    if mode == "absy":
        return e.op("INT_ADD", e.tmp(2), C(word, 2), e.op("INT_ZEXT", e.tmp(2), Y))
    if mode == "indy":
        plo = e.op("LOAD", e.tmp(), C(lo, 2))
        phi = e.op("LOAD", e.tmp(), C((lo + 1) & 0xFF, 2))
        base = e.op("INT_OR", e.tmp(2),
                    e.op("INT_LEFT", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), phi), C(8)),
                    e.op("INT_ZEXT", e.tmp(2), plo))
        return e.op("INT_ADD", e.tmp(2), base, e.op("INT_ZEXT", e.tmp(2), Y))
    if mode == "indx":
        pa = e.op("INT_ADD", e.tmp(), C(lo), X)
        pa1 = e.op("INT_ADD", e.tmp(), pa, C(1))
        plo = e.op("LOAD", e.tmp(), e.op("INT_ZEXT", e.tmp(2), pa))
        phi = e.op("LOAD", e.tmp(), e.op("INT_ZEXT", e.tmp(2), pa1))
        return e.op("INT_OR", e.tmp(2),
                    e.op("INT_LEFT", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), phi), C(8)),
                    e.op("INT_ZEXT", e.tmp(2), plo))
    raise ValueError(mode)


# ---- opcode table: opcode -> (mnemonic, mode) --------------------------------
def _build_ops():
    t = {}
    L = [
        (0x00, "BRK", "impl"), (0x08, "PHP", "impl"), (0x28, "PLP", "impl"),
        (0x48, "PHA", "impl"), (0x68, "PLA", "impl"), (0x40, "RTI", "impl"),
        (0x60, "RTS", "impl"), (0x20, "JSR", "abs"), (0x4C, "JMP", "abs"),
        (0x6C, "JMP", "ind"), (0xEA, "NOP", "impl"),
        (0x18, "CLC", "impl"), (0x38, "SEC", "impl"), (0x58, "CLI", "impl"),
        (0x78, "SEI", "impl"), (0xB8, "CLV", "impl"), (0xD8, "CLD", "impl"),
        (0xF8, "SED", "impl"),
        (0xAA, "TAX", "impl"), (0xA8, "TAY", "impl"), (0x8A, "TXA", "impl"),
        (0x98, "TYA", "impl"), (0xBA, "TSX", "impl"), (0x9A, "TXS", "impl"),
        (0xCA, "DEX", "impl"), (0x88, "DEY", "impl"), (0xE8, "INX", "impl"),
        (0xC8, "INY", "impl"),
    ]
    for op, mn, md in L:
        t[op] = (mn, md)
    for op, md in ((0x10, "BPL"), (0x30, "BMI"), (0x50, "BVC"), (0x70, "BVS"),
                   (0x90, "BCC"), (0xB0, "BCS"), (0xD0, "BNE"), (0xF0, "BEQ")):
        t[op] = (op, md) if False else (md, "rel")
    # group opcodes by (base, mode) low-bits for the ALU/load/store families
    def add(mn, entries):
        for op, md in entries:
            t[op] = (mn, md)
    add("ORA", [(0x09, "imm"), (0x05, "zp"), (0x15, "zpx"), (0x0D, "abs"),
                (0x1D, "absx"), (0x19, "absy"), (0x01, "indx"), (0x11, "indy")])
    add("AND", [(0x29, "imm"), (0x25, "zp"), (0x35, "zpx"), (0x2D, "abs"),
                (0x3D, "absx"), (0x39, "absy"), (0x21, "indx"), (0x31, "indy")])
    add("EOR", [(0x49, "imm"), (0x45, "zp"), (0x55, "zpx"), (0x4D, "abs"),
                (0x5D, "absx"), (0x59, "absy"), (0x41, "indx"), (0x51, "indy")])
    add("ADC", [(0x69, "imm"), (0x65, "zp"), (0x75, "zpx"), (0x6D, "abs"),
                (0x7D, "absx"), (0x79, "absy"), (0x61, "indx"), (0x71, "indy")])
    add("SBC", [(0xE9, "imm"), (0xE5, "zp"), (0xF5, "zpx"), (0xED, "abs"),
                (0xFD, "absx"), (0xF9, "absy"), (0xE1, "indx"), (0xF1, "indy")])
    add("CMP", [(0xC9, "imm"), (0xC5, "zp"), (0xD5, "zpx"), (0xCD, "abs"),
                (0xDD, "absx"), (0xD9, "absy"), (0xC1, "indx"), (0xD1, "indy")])
    add("LDA", [(0xA9, "imm"), (0xA5, "zp"), (0xB5, "zpx"), (0xAD, "abs"),
                (0xBD, "absx"), (0xB9, "absy"), (0xA1, "indx"), (0xB1, "indy")])
    add("STA", [(0x85, "zp"), (0x95, "zpx"), (0x8D, "abs"), (0x9D, "absx"),
                (0x99, "absy"), (0x81, "indx"), (0x91, "indy")])
    add("LDX", [(0xA2, "imm"), (0xA6, "zp"), (0xB6, "zpy"), (0xAE, "abs"),
                (0xBE, "absy")])
    add("LDY", [(0xA0, "imm"), (0xA4, "zp"), (0xB4, "zpx"), (0xAC, "abs"),
                (0xBC, "absx")])
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
    # ---- illegals ----
    rmw = {0x00: "SLO", 0x20: "RLA", 0x40: "SRE", 0x60: "RRA", 0xC0: "DCP", 0xE0: "ISC"}
    for base, mn in rmw.items():
        for lowoff, md in ((0x03, "indx"), (0x07, "zp"), (0x0F, "abs"),
                           (0x13, "indy"), (0x17, "zpx"), (0x1B, "absy"), (0x1F, "absx")):
            t[base + lowoff] = (mn, md)
    add("SAX", [(0x83, "indx"), (0x87, "zp"), (0x8F, "abs"), (0x97, "zpy")])
    add("LAX", [(0xA3, "indx"), (0xA7, "zp"), (0xAF, "abs"), (0xB3, "indy"),
                (0xB7, "zpy"), (0xBF, "absy"), (0xAB, "imm")])
    add("ANC", [(0x0B, "imm"), (0x2B, "imm")])
    add("ALR", [(0x4B, "imm")]); add("ARR", [(0x6B, "imm")])
    add("SBX", [(0xCB, "imm")]); add("SBC", [(0xEB, "imm")])
    add("ANE", [(0x8B, "imm")]); add("LAS", [(0xBB, "absy")])
    add("SHA", [(0x9F, "absy"), (0x93, "indy")]); add("SHX", [(0x9E, "absy")])
    add("SHY", [(0x9C, "absx")]); add("TAS", [(0x9B, "absy")])
    # NOP illegals (byte-skip)
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

# ---- cycle tables (correct for legal AND illegal; page-cross via EXTRA) ------
def _cycle_tables():
    from py65.devices.mpu6502 import MPU  # py65 base is correct for LEGAL ops
    m = MPU(memory=bytearray(0x10000))
    ct, ex = list(m.cycletime), list(m.extracycles)
    rmw_cyc = {"indx": 8, "zp": 5, "abs": 6, "indy": 8, "zpx": 6, "absy": 7, "absx": 7}
    for base in (0x00, 0x20, 0x40, 0x60, 0xC0, 0xE0):
        for lowoff, md in ((0x03, "indx"), (0x07, "zp"), (0x0F, "abs"),
                           (0x13, "indy"), (0x17, "zpx"), (0x1B, "absy"), (0x1F, "absx")):
            ct[base + lowoff] = rmw_cyc[md]; ex[base + lowoff] = 0
    illc = {0x83: 6, 0x87: 3, 0x8F: 4, 0x97: 4,          # SAX
            0xA3: 6, 0xA7: 3, 0xAF: 4, 0xB3: 5, 0xB7: 4, 0xBF: 4, 0xAB: 2,  # LAX
            0x0B: 2, 0x2B: 2, 0x4B: 2, 0x6B: 2, 0xCB: 2, 0xEB: 2, 0x8B: 2,
            0xBB: 4, 0x9F: 5, 0x93: 6, 0x9E: 5, 0x9C: 5, 0x9B: 5}
    for op, c in illc.items():
        ct[op] = c; ex[op] = 0
    for op in (0xBF, 0xB3, 0xBB):  # read illegals that page-cross
        ex[op] = 1
    # NOP illegals
    for op in (0x1A, 0x3A, 0x5A, 0x7A, 0xDA, 0xFA):
        ct[op] = 2
    for op in (0x80, 0x82, 0x89, 0xC2, 0xE2):
        ct[op] = 2
    for op in (0x04, 0x44, 0x64):
        ct[op] = 3
    for op in (0x14, 0x34, 0x54, 0x74, 0xD4, 0xF4):
        ct[op] = 4; ex[op] = 0
    ct[0x0C] = 4
    for op in (0x1C, 0x3C, 0x5C, 0x7C, 0xDC, 0xFC):
        ct[op] = 4; ex[op] = 1  # NOP absx page-crosses (+1), like a read
    return ct, ex


CYCLETIME, EXTRACYCLES = None, None

def load_cycle_tables():
    global CYCLETIME, EXTRACYCLES
    CYCLETIME, EXTRACYCLES = _cycle_tables()


# ---- the lifter --------------------------------------------------------------
def lift(mem, pc):
    op = mem[pc]
    if op not in OPS:
        raise NotImplementedError(f"opcode {op:02X} at {pc:04X}")
    mn, mode = OPS[op]
    length = MODE_LEN[mode]
    e = Emit()
    ctrl = ("next",)
    eav = _ea(e, mem, pc, mode) if mode in MEM_MODES else None
    imm = mem[(pc + 1) & 0xFFFF]

    def rd():
        if mode == "imm":
            return C(imm)
        if mode == "acc":
            return A
        return e.op("LOAD", e.tmp(), eav)

    if mn in ("LDA", "LDX", "LDY"):
        r = {"LDA": A, "LDX": X, "LDY": Y}[mn]; e.op("COPY", r, rd()); e.nz(r)
    elif mn in ("STA", "STX", "STY"):
        e.op("STORE", None, eav, {"STA": A, "STX": X, "STY": Y}[mn])
    elif mn == "ORA":
        e.op("INT_OR", A, A, rd()); e.nz(A)
    elif mn == "AND":
        e.op("INT_AND", A, A, rd()); e.nz(A)
    elif mn == "EOR":
        e.op("INT_XOR", A, A, rd()); e.nz(A)
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
            r = fn(e, A); e.op("COPY", A, r); e.nz(A)
        else:
            r = fn(e, e.op("LOAD", e.tmp(), eav)); e.op("STORE", None, eav, r); e.nz(r)
    elif mn in ("INC", "DEC"):
        cur = e.op("LOAD", e.tmp(), eav)
        r = e.op("INT_ADD" if mn == "INC" else "INT_SUB", e.tmp(), cur, C(1))
        e.op("STORE", None, eav, r); e.nz(r)
    elif mn in ("INX", "DEX", "INY", "DEY"):
        r = {"INX": X, "DEX": X, "INY": Y, "DEY": Y}[mn]
        e.op("INT_ADD" if mn in ("INX", "INY") else "INT_SUB", r, r, C(1)); e.nz(r)
    elif mn in ("TAX", "TAY", "TXA", "TYA", "TSX"):
        src, dst = {"TAX": (A, X), "TAY": (A, Y), "TXA": (X, A), "TYA": (Y, A),
                    "TSX": (SP, X)}[mn]
        e.op("COPY", dst, src); e.nz(dst)
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
        pass
    elif mn in ("PHA", "PHP"):
        if mn == "PHP":
            val = e.op("INT_OR", e.tmp(), C(0x30), FC)
            for f, sh in ((FZ, 1), (FI, 2), (FD, 3), (FV, 6), (FN, 7)):
                val = e.op("INT_OR", e.tmp(), val, e.op("INT_LEFT", e.tmp(), f, C(sh)))
        else:
            val = A
        addr = e.op("INT_OR", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), SP), C(0x100, 2))
        e.op("STORE", None, addr, val); e.op("INT_SUB", SP, SP, C(1))
    elif mn in ("PLA", "PLP"):
        e.op("INT_ADD", SP, SP, C(1))
        addr = e.op("INT_OR", e.tmp(2), e.op("INT_ZEXT", e.tmp(2), SP), C(0x100, 2))
        val = e.op("LOAD", e.tmp(), addr)
        if mn == "PLA":
            e.op("COPY", A, val); e.nz(A)
        else:
            e.op("INT_AND", FC, val, C(1))
            for f, sh in ((FZ, 1), (FI, 2), (FD, 3), (FV, 6), (FN, 7)):
                e.op("INT_AND", f, e.op("INT_RIGHT", e.tmp(), val, C(sh)), C(1))
    elif mn in ("BPL", "BMI", "BVC", "BVS", "BCC", "BCS", "BNE", "BEQ"):
        flag, pol = {"BPL": (FN, 0), "BMI": (FN, 1), "BVC": (FV, 0), "BVS": (FV, 1),
                     "BCC": (FC, 0), "BCS": (FC, 1), "BNE": (FZ, 0), "BEQ": (FZ, 1)}[mn]
        tgt = (pc + 2 + (imm - 256 if imm & 0x80 else imm)) & 0xFFFF
        ctrl = ("br", flag, pol, tgt, (pc + 2) & 0xFFFF)
    elif mn == "JMP":
        ctrl = ("jmpind", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8)) \
            if mode == "ind" else ("jmp", mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8))
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
        r = shift(e, e.op("LOAD", e.tmp(), eav)); e.op("STORE", None, eav, r)
        if mn == "SLO":
            e.op("INT_OR", A, A, r); e.nz(A)
        elif mn == "RLA":
            e.op("INT_AND", A, A, r); e.nz(A)
        elif mn == "SRE":
            e.op("INT_XOR", A, A, r); e.nz(A)
        else:  # RRA = ROR then ADC
            _adc(e, r)
    elif mn == "DCP":
        r = e.op("INT_SUB", e.tmp(), e.op("LOAD", e.tmp(), eav), C(1))
        e.op("STORE", None, eav, r); _cmp(e, A, r)
    elif mn == "ISC":
        r = e.op("INT_ADD", e.tmp(), e.op("LOAD", e.tmp(), eav), C(1))
        e.op("STORE", None, eav, r); _sbc(e, r)
    elif mn == "SAX":
        e.op("STORE", None, eav, e.op("INT_AND", e.tmp(), A, X))
    elif mn == "LAX":
        v = rd(); e.op("COPY", A, v); e.op("COPY", X, v); e.nz(A)
    elif mn == "ANC":
        e.op("INT_AND", A, A, C(imm)); e.nz(A)
        e.op("INT_NOTEQUAL", FC, e.op("INT_AND", e.tmp(), A, C(0x80)), C(0))
    elif mn == "ALR":
        e.op("INT_AND", A, A, C(imm))
        e.op("COPY", FC, e.op("INT_AND", e.tmp(), A, C(1)))
        e.op("INT_RIGHT", A, A, C(1)); e.nz(A)
    elif mn == "ARR":
        e.op("INT_AND", A, A, C(imm))
        r = e.op("INT_OR", e.tmp(), e.op("INT_RIGHT", e.tmp(), A, C(1)),
                 e.op("INT_LEFT", e.tmp(), FC, C(7)))
        e.op("COPY", A, r); e.nz(A)
        b6 = e.op("INT_AND", e.tmp(), e.op("INT_RIGHT", e.tmp(), r, C(6)), C(1))
        b5 = e.op("INT_AND", e.tmp(), e.op("INT_RIGHT", e.tmp(), r, C(5)), C(1))
        e.op("COPY", FC, b6); e.op("INT_XOR", FV, b6, b5)
    elif mn == "SBX":
        ax = e.op("INT_AND", e.tmp(), A, X)
        e.op("INT_LESSEQUAL", FC, C(imm), ax)
        e.op("INT_SUB", X, ax, C(imm)); e.nz(X)
    elif mn == "LAS":
        v = e.op("INT_AND", e.tmp(), e.op("LOAD", e.tmp(), eav), SP)
        e.op("COPY", A, v); e.op("COPY", X, v); e.op("COPY", SP, v); e.nz(v)
    elif mn == "ANE":  # unstable A = (A | magic) & X & imm
        e.op("INT_AND", A, e.op("INT_AND", e.tmp(),
             e.op("INT_OR", e.tmp(), A, C(XAA_MAGIC)), X), C(imm)); e.nz(A)
    elif mn in ("SHA", "SHX", "SHY", "TAS"):
        # store reg & (high(base)+1); the address-high AND quirk.
        base = mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8) \
            if mode == "absy" or mode == "absx" else mem[(pc + 1) & 0xFFFF]
        h1 = C(((base >> 8) + 1) & 0xFF)
        if mn == "SHA":
            val = e.op("INT_AND", e.tmp(), e.op("INT_AND", e.tmp(), A, X), h1)
        elif mn == "SHX":
            val = e.op("INT_AND", e.tmp(), X, h1)
        elif mn == "SHY":
            val = e.op("INT_AND", e.tmp(), Y, h1)
        else:  # TAS
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
    return {"ops": e.ops, "len": length, "cyc": CYCLETIME[op], "pen": pen, "ctrl": ctrl}


# ---- P-Code -> Python source (per-op line; exec'd into a closure) ------------
_BINOP = {"INT_ADD": "+", "INT_SUB": "-", "INT_AND": "&", "INT_OR": "|",
          "INT_XOR": "^", "INT_LEFT": "<<"}
_CMPOP = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<",
          "INT_LESSEQUAL": "<="}


def _rd_expr(vn):
    sp, off, _sz = vn
    if sp == "c":
        return str(off)
    return ("r[%d]" if sp == "r" else "u[%d]") % off


def _lhs(vn):
    sp, off, _sz = vn
    return ("r[%d]" if sp == "r" else "u[%d]") % off


def _emit_line(mn, out, ins):
    if mn == "STORE":
        return "wr(%s, %s, %d)" % (_rd_expr(ins[0]), _rd_expr(ins[1]), ins[1][2])
    if mn == "LOAD":
        return "%s = rd(%s, %d)" % (_lhs(out), _rd_expr(ins[0]), out[2])
    lhs = _lhs(out)
    if mn in ("COPY", "INT_ZEXT"):
        return "%s = %s" % (lhs, _rd_expr(ins[0]))
    if mn == "INT_RIGHT":
        return "%s = %s >> %s" % (lhs, _rd_expr(ins[0]), _rd_expr(ins[1]))
    if mn in _BINOP:
        mask = (1 << (8 * out[2])) - 1
        return "%s = (%s %s %s) & %d" % (
            lhs, _rd_expr(ins[0]), _BINOP[mn], _rd_expr(ins[1]), mask)
    if mn in _CMPOP:
        return "%s = 1 if %s %s %s else 0" % (
            lhs, _rd_expr(ins[0]), _CMPOP[mn], _rd_expr(ins[1]))
    if mn == "INT_CARRY":
        mask0 = (1 << (8 * ins[0][2])) - 1
        return "%s = 1 if (%s + %s) > %d else 0" % (
            lhs, _rd_expr(ins[0]), _rd_expr(ins[1]), mask0)
    raise NotImplementedError(mn)


# ---- pure-Python P-Code interpreter (no py65) --------------------------------
class PcodeVM:
    __slots__ = ("mem", "reg", "uniq", "cycles", "volatile", "vicirq", "ciaicr",
                 "wlog")

    def __init__(self, mem_bytes):
        self.mem = bytearray(mem_bytes)
        self.reg = [0] * 16
        self.reg[3] = 0xFF
        self.uniq = {}
        self.cycles = 0
        self.volatile = True  # apply the SID-replay volatile-read model
        # Interrupt-source flags an IRQ handler polls to find who fired: $D019
        # (VIC, write-acked) and $DC0D (CIA, read-cleared). run_irq_driven raises
        # them. wlog, when a list, records (cycle, reg, val) SID writes.
        self.vicirq = 0
        self.ciaicr = 0
        self.wlog = None

    def _rd(self, addr, sz):
        mem = self.mem
        if sz == 1:
            if self.volatile and addr == 0xD019:
                return self.vicirq
            if self.volatile and addr == 0xDC0D:
                v = self.ciaicr; self.ciaicr = 0; return v
            if not (self.volatile and 0xD011 <= addr <= 0xD41C):
                return mem[addr]
        val = 0
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if self.volatile and a == 0xD012:
                b = (self.cycles // 63) % 312 & 0xFF
            elif self.volatile and a == 0xD011:
                b = (mem[0xD011] & 0x7F) | ((((self.cycles // 63) % 312 >> 8) & 1) << 7)
            elif self.volatile and (a == 0xD41B or a == 0xD41C):
                b = (self.cycles >> 3) & 0xFF
            else:
                b = mem[a]
            val |= b << (8 * i)
        return val

    def _wr(self, addr, val, sz):
        mem = self.mem
        if self.volatile and addr == 0xD019:  # writing 1s to $D019 acks VIC IRQ
            self.vicirq &= ~val & 0x7F
        if self.wlog is not None and 0xD400 <= addr <= 0xD418:
            self.wlog.append((self.cycles, addr - 0xD400, val & 0xFF))
        for i in range(sz):
            mem[(addr + i) & 0xFFFF] = (val >> (8 * i)) & 0xFF

    def compile_record(self, rec):
        f = rec.get("_f")
        if f is not None:
            return f
        lines = [_emit_line(mn, out, ins) for mn, out, ins in rec["ops"]]
        src = "def _f(r,u,rd,wr):\n    " + ("\n    ".join(lines) or "pass") + "\n"
        ns = {}
        exec(src, ns)  # noqa: S102 - generated straight-line P-Code, no user input
        f = ns["_f"]
        rec["_f"] = f
        return f

    def run_record(self, rec, pc):
        (rec.get("_f") or self.compile_record(rec))(
            self.reg, self.uniq, self._rd, self._wr)
        cyc = rec["cyc"]
        ctrl = rec["ctrl"]
        nxt = None
        if ctrl[0] == "br":
            _k, flag, pol, tgt, ft = ctrl
            if self.reg[flag[1]] == pol:
                cyc += 1
                if (ft & 0xFF00) != (tgt & 0xFF00):
                    cyc += 1
                nxt = tgt
            else:
                nxt = ft
        else:
            pen = rec["pen"]
            if pen is not None:
                k = pen[0]
                base = pen[1]
                if k == "iy":
                    base = self.mem[base] | (self.mem[(base + 1) & 0xFF] << 8)
                    idx = self.reg[2]
                elif k == "ax":
                    idx = self.reg[1]
                else:  # ay
                    idx = self.reg[2]
                if k != "branch" and (base & 0xFF00) != ((base + idx) & 0xFF00):
                    cyc += 1
        self.cycles += cyc
        return ctrl, nxt

    # ---- one 6502 instruction: run its P-Code and resolve the next PC --------
    def step(self, pc, cache, lifter):
        mem = self.mem
        k = (pc, mem[pc], mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF])
        rec = cache.get(k)
        if rec is None:
            if lifter is None:
                raise KeyError("cache miss at %04X (SMC needs a lifter)" % pc)
            rec = lifter(mem, pc)
            cache[k] = rec
        ctrl, nxt = self.run_record(rec, pc)
        t = ctrl[0]
        reg = self.reg
        if t == "next":
            return (pc + rec["len"]) & 0xFFFF
        if t == "br":
            return nxt
        if t == "jmp":
            return ctrl[1]
        if t == "jmpind":
            ptr = ctrl[1]
            lo = mem[ptr]
            hi = mem[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)]
            return lo | (hi << 8)
        if t == "jsr":
            ret = (pc + rec["len"] - 1) & 0xFFFF
            mem[0x100 + reg[3]] = ret >> 8
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = ret & 0xFF
            reg[3] = (reg[3] - 1) & 0xFF
            return ctrl[1]
        if t == "rts":
            reg[3] = (reg[3] + 1) & 0xFF
            lo = mem[0x100 + reg[3]]
            reg[3] = (reg[3] + 1) & 0xFF
            hi = mem[0x100 + reg[3]]
            return ((hi << 8) | lo) + 1 & 0xFFFF
        if t == "rti":
            reg[3] = (reg[3] + 1) & 0xFF
            self._set_flags(mem[0x100 + reg[3]])
            reg[3] = (reg[3] + 1) & 0xFF
            lo = mem[0x100 + reg[3]]
            reg[3] = (reg[3] + 1) & 0xFF
            hi = mem[0x100 + reg[3]]
            return (hi << 8) | lo
        if t == "brk":
            ret = (pc + 2) & 0xFFFF
            mem[0x100 + reg[3]] = ret >> 8
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = ret & 0xFF
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = self._status(brk=1)
            reg[3] = (reg[3] - 1) & 0xFF
            reg[10] = 1
            return mem[0xFFFE] | (mem[0xFFFF] << 8)
        raise RuntimeError("JAM at %04X" % pc)

    def _status(self, brk=0):
        r = self.reg
        return (r[8] | (r[9] << 1) | (r[10] << 2) | (r[11] << 3)
                | 0x20 | ((brk or 0) << 4) | (r[13] << 6) | (r[14] << 7))

    def _set_flags(self, p):
        r = self.reg
        r[8] = p & 1
        r[9] = (p >> 1) & 1
        r[10] = (p >> 2) & 1
        r[11] = (p >> 3) & 1
        r[13] = (p >> 6) & 1
        r[14] = (p >> 7) & 1


# ---- control-flow drivers ----------------------------------------------------
_GUARD = 8_000_000


def run_sub(vm, pc, cache, lifter):
    """Run a subroutine to its balancing RTS (dummy-return convention)."""
    reg = vm.reg
    start = reg[3]
    vm.mem[0x100 + reg[3]] = 0x00
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = 0x01
    reg[3] = (reg[3] - 1) & 0xFF
    n = 0
    while reg[3] < start:
        pc = vm.step(pc, cache, lifter)
        n += 1
        if n > _GUARD:
            raise RuntimeError("runaway subroutine at %04X" % pc)
    return pc


def run_irq(vm, handler, cache, lifter):
    """Enter ``handler`` like a hardware IRQ; run until its RTI unwinds the frame.

    Pushes the CPU interrupt frame (return address then status), sets I, and runs
    from ``handler`` until the balancing RTI climbs the stack back.
    """
    reg = vm.reg
    start = reg[3]
    vm.mem[0x100 + reg[3]] = 0x00  # sentinel return hi
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = 0x00  # sentinel return lo
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = vm._status()  # pushed status
    reg[3] = (reg[3] - 1) & 0xFF
    reg[10] = 1  # I set on IRQ entry
    pc = handler
    n = 0
    while reg[3] < start:
        pc = vm.step(pc, cache, lifter)
        n += 1
        if n > _GUARD:
            raise RuntimeError("runaway IRQ at %04X" % pc)
    return pc


def _take_irq(vm, handler, ret_pc, enter):
    reg = vm.reg
    vm.mem[0x100 + reg[3]] = (ret_pc >> 8) & 0xFF; reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = ret_pc & 0xFF; reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = vm._status(); reg[3] = (reg[3] - 1) & 0xFF
    reg[10] = 1  # I set on IRQ entry
    enter(vm)
    return handler


def run_irq_driven(vm, handler, total, sources, cache, lifter, idle_pc=0):
    """Interrupt-driven executor: multiple sources, hardware-faithful nesting.

    ``sources`` is a list of dicts ``{"period", "next", "enter"}``; ``enter(vm)``
    raises that source's flag ($D019/$DC0D). Between handlers the CPU idles (skip
    to the next fire). While a handler runs with I clear, a due source nests --
    modelling defMON's ``CLI`` mid-play, so a raster split delays the CIA play's
    later SID writes exactly as on hardware. Advances until ``total`` cycles.
    """
    reg = vm.reg
    while True:
        nxt = min(s["next"] for s in sources)
        if nxt >= total:
            return
        if vm.cycles < nxt:
            vm.cycles = nxt
        idle_sp = reg[3]
        due = min((s for s in sources if s["next"] <= vm.cycles), key=lambda s: s["next"])
        pc = _take_irq(vm, handler, idle_pc, due["enter"]); due["next"] += due["period"]
        n = 0
        while reg[3] < idle_sp:
            if reg[10] == 0:
                ready = [s for s in sources if s["next"] <= vm.cycles]
                if ready:
                    d = min(ready, key=lambda s: s["next"])
                    pc = _take_irq(vm, handler, pc, d["enter"]); d["next"] += d["period"]
                    continue
            pc = vm.step(pc, cache, lifter)
            n += 1
            if n > _GUARD:
                raise RuntimeError("runaway IRQ-driven at %04X" % pc)
