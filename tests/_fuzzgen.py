"""Seeded synthetic 6510-player generator for the byte-exactness differential fuzzer.

A two-pass label assembler (built on ``deity_informant``'s opcode table) plus
tagged idiom templates; ``players(per)`` yields a reproducible corpus spanning
every recorder-stressing idiom class. Imported as ``import _fuzzgen as G``.
"""

from __future__ import annotations

import numpy as np

from deity_informant.lifter import OPS, MODE_LEN, ILLEGAL_OPCODES

SID = 0xD400  # $D400..$D418 SID register file (the observable output set)

# ---- opcode encoder: invert deity's own (opcode -> (mn, mode)) table ---------
_ENC = {}
for _op, (_mn, _md) in OPS.items():
    if _op in ILLEGAL_OPCODES:
        continue
    _ENC[(_mn, _md)] = _op
for _mn, _pairs in (
    ("LAX", (("zp", 0xA7), ("abs", 0xAF), ("absy", 0xBF), ("indy", 0xB3), ("indx", 0xA3))),
    ("SAX", (("zp", 0x87), ("abs", 0x8F), ("indx", 0x83))),
    ("DCP", (("zp", 0xC7), ("abs", 0xCF), ("absx", 0xDF))),
    ("ISC", (("zp", 0xE7), ("abs", 0xEF), ("absx", 0xFF))),
    ("SLO", (("zp", 0x07), ("abs", 0x0F))),
):  # whitelisted illegals real players use (unambiguous byte per (mn,mode))
    for _md, _o in _pairs:
        _ENC[(_mn, _md)] = _o

_ONE = {"imm", "zp", "zpx", "zpy", "indx", "indy", "rel"}


class Asm:
    """Two-pass label assembler emitting 6510 machine code at ``org``."""

    def __init__(self, org):
        self.org = org
        self.items = []
        self.labels = {}
        self.end = org

    def i(self, mn, mode="impl", operand=None):
        self.items.append(("i", mn, mode, operand))
        return self

    def label(self, name):
        self.items.append(("label", name))
        return self

    def byte(self, val):
        self.items.append(("byte", val & 0xFF))
        return self

    def _pass_addrs(self):
        pc = self.org
        for it in self.items:
            if it[0] == "label":
                self.labels[it[1]] = pc
            elif it[0] == "byte":
                pc += 1
            else:
                pc += MODE_LEN[it[2]]
        self.end = pc

    def _resolve(self, operand):
        if operand is None:
            return 0
        if isinstance(operand, int):
            return operand
        kind = operand[0]
        off = operand[2] if len(operand) > 2 else 0
        base = self.labels[operand[1]] + off
        if kind == "L":
            return base & 0xFFFF
        if kind == "LOL":
            return base & 0xFF
        if kind == "HIL":
            return (base >> 8) & 0xFF
        raise ValueError(operand)

    def assemble(self):
        self._pass_addrs()
        out = bytearray()
        pc = self.org
        for it in self.items:
            if it[0] == "label":
                continue
            if it[0] == "byte":
                out.append(it[1])
                pc += 1
                continue
            _, mn, mode, operand = it
            out.append(_ENC[(mn, mode)])
            pc += MODE_LEN[mode]
            if mode in ("impl", "acc"):
                continue
            val = self._resolve(operand)
            if mode == "rel":
                out.append((val - pc) & 0xFF)
            elif mode in _ONE:
                out.append(val & 0xFF)
            else:
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out)


class Player:
    """A generated playroutine: code, seed data, output set, and idiom tags."""

    def __init__(
        self,
        name,
        org,
        prog,
        outputs,
        classes,
        *,
        data=None,
        frames=1,
        volatile=False,
        init=None,
        init_org=None,
        load=None,
    ):
        self.name = name
        self.org = org
        self.prog = prog
        self.outputs = set(outputs)
        self.classes = set(classes)
        self.data = dict(data or {})
        self.frames = frames
        self.volatile = volatile
        self.init = init
        self.init_org = init_org
        self.load = load if load is not None else org
        self.seed = None

    def image_data(self):
        """``{addr: byte}`` of code + seed cells (for the in-process image)."""
        cells = dict(self.data)
        for k, b in enumerate(self.prog):
            cells[self.org + k] = b
        if self.init is not None:
            for k, b in enumerate(self.init):
                cells[self.init_org + k] = b
        return cells


ORG = 0x1000
TBL = 0x1400  # constant data tables (inside the load image)
VEC = 0x1500  # target-pointer tables
PTR = 0x02  # zero-page pointer pair $02/$03 (clear of the CPU port $00/$01)
CNT = 0x1440  # RAM counter cells
_INIT_ORG = 0x0F00
_RTS = bytes([0x60])


def t_table_index(rng):
    """Indexed absolute read of a constant table -> STA $D400,X (DEX/BPL loop)."""
    n = int(rng.integers(4, 0x19))
    tbl = [int(v) for v in rng.integers(0, 256, n)]
    a = Asm(ORG)
    a.i("LDX", "imm", n - 1).label("lp")
    a.i("LDA", "absx", TBL).i("STA", "absx", SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {TBL + k: tbl[k] for k in range(n)}
    outs = {SID + r for r in range(n)}
    return Player(
        "table_index",
        ORG,
        a.assemble(),
        outs,
        {"indexed"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_smc_operand(rng):
    """Self-modify a STA absolute operand across frames (operand patching)."""
    base = int(rng.integers(0, 0x10))
    val = int(rng.integers(0, 256))
    frames = int(rng.integers(3, 7))
    a = Asm(ORG)
    a.i("LDA", "imm", val).label("st")
    a.i("STA", "abs", SID + base)  # operand low byte at st+1
    a.i("INC", "abs", ("L", "st", 1)).i("RTS")  # advance target next frame
    outs = {SID + ((base + f) & 0xFF) for f in range(frames)}
    return Player(
        "smc_operand",
        ORG,
        a.assemble(),
        outs,
        {"smc"},
        frames=frames,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_smc_opcode(rng):
    """Self-modify an opcode byte selected by a seed cell (opcode patching)."""
    seed = int(rng.choice([0xE8, 0xCA]))  # INX vs DEX
    reg = int(rng.integers(0, 0x19))
    a = Asm(ORG)
    a.i("LDA", "abs", 0x1030).label("patch")
    a.i("STA", "abs", ("L", "site")).i("LDX", "imm", 0x05)
    a.label("site").i("NOP")  # opcode overwritten to INX/DEX
    a.i("STX", "abs", SID + reg).i("RTS")
    data = {0x1030: seed}
    return Player(
        "smc_opcode",
        ORG,
        a.assemble(),
        {SID + reg},
        {"smc"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_smc_branch(rng):
    """Self-modify a branch displacement operand (branch patching)."""
    reg = int(rng.integers(0, 0x19))
    a = Asm(ORG)
    a.i("LDA", "abs", 0x1030).label("bp")
    a.i("STA", "abs", ("L", "br", 1)).i("LDX", "imm", 0x00)
    a.label("loop").i("INX").label("br")
    a.i("BNE", "rel", ("L", "loop"))  # displacement overwritten from seed
    a.i("STX", "abs", SID + reg).i("RTS")
    data = {0x1030: 0xFD}  # -3: loop back to INX
    return Player(
        "smc_branch",
        ORG,
        a.assemble(),
        {SID + reg},
        {"smc"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def _stub(org, reg, val):
    return Asm(org).i("LDA", "imm", val).i("STA", "abs", SID + reg).i("RTS").assemble()


def t_jmp_indirect(rng):
    """Write a vector cell then JMP (ind) to one of two stubs (computed dispatch)."""
    sel = int(rng.integers(0, 2))
    reg = int(rng.integers(0, 0x19))
    v0, v1 = int(rng.integers(0, 256)), int(rng.integers(0, 256))
    s0, s1 = 0x1300, 0x1320
    tgt = s0 if sel == 0 else s1
    a = Asm(ORG)
    a.i("LDA", "imm", tgt & 0xFF).i("STA", "abs", VEC)
    a.i("LDA", "imm", tgt >> 8).i("STA", "abs", VEC + 1).i("JMP", "ind", VEC)
    data = {}
    for base, v in ((s0, v0), (s1, v1)):
        for k, b in enumerate(_stub(base, reg, v)):
            data[base + k] = b
    return Player(
        "jmp_indirect",
        ORG,
        a.assemble(),
        {SID + reg},
        {"dispatch"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_rts_trick(rng):
    """Push (target-1) hi/lo then RTS to a stub (RTS-trick computed dispatch)."""
    sel = int(rng.integers(0, 2))
    reg = int(rng.integers(0, 0x19))
    v0, v1 = int(rng.integers(0, 256)), int(rng.integers(0, 256))
    s0, s1 = 0x1300, 0x1320
    tgt = (s0 if sel == 0 else s1) - 1
    a = Asm(ORG)
    a.i("LDA", "imm", (tgt >> 8) & 0xFF).i("PHA")
    a.i("LDA", "imm", tgt & 0xFF).i("PHA").i("RTS")  # returns to tgt+1 = stub
    data = {}
    for base, v in ((s0, v0), (s1, v1)):
        for k, b in enumerate(_stub(base, reg, v)):
            data[base + k] = b
    return Player(
        "rts_trick",
        ORG,
        a.assemble(),
        {SID + reg},
        {"dispatch"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_jump_table(rng):
    """Index a table of target addresses into a zp vector, then JMP (ind)."""
    idx = int(rng.integers(0, 3))
    reg = int(rng.integers(0, 0x19))
    stubs = [0x1300, 0x1320, 0x1340]
    vals = [int(rng.integers(0, 256)) for _ in stubs]
    a = Asm(ORG)
    a.i("LDX", "imm", idx * 2)
    a.i("LDA", "absx", VEC).i("STA", "zp", PTR)
    a.i("LDA", "absx", VEC + 1).i("STA", "zp", PTR + 1).i("JMP", "ind", PTR)
    data = {}
    for k, base in enumerate(stubs):
        data[VEC + 2 * k] = base & 0xFF
        data[VEC + 2 * k + 1] = base >> 8
        for j, b in enumerate(_stub(base, reg, vals[k])):
            data[base + j] = b
    return Player("jump_table", ORG, a.assemble(), {SID + reg}, {"dispatch", "indexed"}, data=data)


def t_indy(rng):
    """(zp),Y indexed-indirect read with 8-bit index inside a 16-bit base."""
    y = int(rng.choice([0x00, 0x05, 0xFF, 0x80]))  # incl. page-cross cases
    reg = int(rng.integers(0, 0x19))
    base = 0x14F0
    a = Asm(ORG)
    a.i("LDY", "imm", y).i("LDA", "indy", PTR).i("STA", "abs", SID + reg).i("RTS")
    data = {(base + y) & 0xFFFF: int(rng.integers(0, 256)), PTR: base & 0xFF, PTR + 1: base >> 8}
    init = Asm(_INIT_ORG)
    init.i("LDA", "imm", base & 0xFF).i("STA", "zp", PTR)
    init.i("LDA", "imm", base >> 8).i("STA", "zp", PTR + 1).i("RTS")
    return Player(
        "indy",
        ORG,
        a.assemble(),
        {SID + reg},
        {"indexed"},
        data=data,
        init=init.assemble(),
        init_org=_INIT_ORG,
    )


def t_indx(rng):
    """(zp,X) indexed-indirect read with 8-bit pointer-address wrap."""
    x = int(rng.choice([0x00, 0x02, 0xFE]))
    reg = int(rng.integers(0, 0x19))
    tgt = 0x1490
    a = Asm(ORG)
    a.i("LDX", "imm", x).i("LDA", "indx", PTR).i("STA", "abs", SID + reg).i("RTS")
    data = {
        tgt: int(rng.integers(0, 256)),
        (PTR + x) & 0xFF: tgt & 0xFF,
        (PTR + x + 1) & 0xFF: tgt >> 8,
    }
    return Player(
        "indx",
        ORG,
        a.assemble(),
        {SID + reg},
        {"indexed"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_illegal_lax_sax(rng):
    """LAX (A=X=mem) then SAX (store A&X): undocumented load/store pair."""
    reg0 = int(rng.integers(0, 0x0C))
    reg1 = reg0 + int(rng.integers(1, 0x0C))
    a = Asm(ORG)
    a.i("LAX", "abs", TBL).i("STA", "abs", SID + reg0)
    a.i("SAX", "abs", SID + reg1).i("RTS")
    data = {TBL: int(rng.integers(0, 256))}
    return Player(
        "illegal_lax_sax",
        ORG,
        a.assemble(),
        {SID + reg0, SID + reg1},
        {"illegal"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_illegal_dcp_isc(rng):
    """DCP/ISC RMW on RAM counters, gating a SID write (illegal + counter)."""
    reg = int(rng.integers(0, 0x19))
    which = int(rng.integers(0, 2))
    a = Asm(ORG)
    a.i("LDA", "imm", 0x80)
    a.i("DCP" if which == 0 else "ISC", "abs", CNT)
    a.i("LDA", "abs", CNT).i("STA", "abs", SID + reg).i("RTS")
    data = {CNT: int(rng.integers(0, 256))}
    return Player(
        "illegal_dcp_isc",
        ORG,
        a.assemble(),
        {SID + reg},
        {"illegal", "dec_timer"},
        data=data,
        frames=int(rng.integers(2, 5)),
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_dec_timer(rng):
    """DEC reload counter: on underflow, reload and advance a row pointer mod n."""
    reg = int(rng.integers(0, 0x19))
    period = int(rng.integers(2, 5))
    n = 4
    tbl = [int(v) for v in rng.integers(0, 256, n)]
    a = Asm(ORG)
    a.i("DEC", "abs", CNT).i("BNE", "rel", ("L", "done"))
    a.i("LDA", "imm", period).i("STA", "abs", CNT)
    a.i("LDX", "abs", CNT + 1).i("INX").i("TXA")
    a.i("AND", "imm", n - 1).i("STA", "abs", CNT + 1)
    a.label("done").i("LDX", "abs", CNT + 1)
    a.i("LDA", "absx", TBL).i("STA", "abs", SID + reg).i("RTS")
    data = {CNT: 0x01, CNT + 1: 0x00}
    data.update({TBL + k: tbl[k] for k in range(n)})
    return Player(
        "dec_timer",
        ORG,
        a.assemble(),
        {SID + reg},
        {"dec_timer", "variable_row"},
        data=data,
        frames=int(rng.integers(6, 12)),
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_varlen_row(rng):
    """Ctrl-byte gated variable-length row decode: 1..3 payload bytes per command."""
    reg = int(rng.integers(0, 0x16))
    cmds = [int(rng.integers(0, 3)) for _ in range(int(rng.integers(2, 5)))]
    payload = [[int(rng.integers(0, 256)) for _ in range(c + 1)] for c in cmds]
    a = Asm(ORG)
    a.i("LDX", "abs", CNT + 2)  # stream cursor
    a.i("LDA", "absx", TBL).i("TAY").i("INX")  # Y = count-1
    a.label("emit").i("LDA", "absx", TBL).i("STA", "abs", SID + reg)
    a.i("INX").i("DEY").i("BPL", "rel", ("L", "emit"))
    a.i("STX", "abs", CNT + 2).i("RTS")
    stream = []
    for c, pl in zip(cmds, payload):
        stream.append(c)
        stream.extend(pl)
    data = {TBL + k: stream[k] for k in range(len(stream))}
    data[CNT + 2] = 0x00
    return Player(
        "varlen_row",
        ORG,
        a.assemble(),
        {SID + reg},
        {"variable_row"},
        data=data,
        frames=len(cmds),
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_multispeed(rng):
    """Inner DEC-counted loop writing SID K times per call (multispeed cadence)."""
    reg = int(rng.integers(0, 0x19))
    k = int(rng.integers(2, 5))
    a = Asm(ORG)
    a.i("LDX", "imm", k).label("pass").i("TXA").i("STA", "abs", SID + reg)
    a.i("DEX").i("BNE", "rel", ("L", "pass")).i("RTS")
    return Player(
        "multispeed",
        ORG,
        a.assemble(),
        {SID + reg},
        {"multispeed", "dec_timer"},
        init=_RTS,
        init_org=_INIT_ORG,
    )


def t_volatile(rng):
    """Read a modelled volatile source ($D41B/$D41C/$D012) -> SID (cycle-derived)."""
    src = int(rng.choice([0xD41B, 0xD41C, 0xD012]))
    reg = int(rng.integers(0, 0x16))
    a = Asm(ORG)
    a.i("LDA", "abs", src).i("STA", "abs", SID + reg)
    a.i("LDA", "abs", src).i("STA", "abs", SID + reg + 1).i("RTS")
    return Player(
        "volatile",
        ORG,
        a.assemble(),
        {SID + reg, SID + reg + 1},
        {"volatile"},
        frames=int(rng.integers(2, 5)),
        volatile=True,
    )


def t_ram_output(rng):
    """Indexed copy to a couple of RAM output cells (non-SID observable set)."""
    n = 3
    tbl = [int(v) for v in rng.integers(0, 256, n)]
    a = Asm(ORG)
    a.i("LDX", "imm", n - 1).label("lp")
    a.i("LDA", "absx", TBL).i("STA", "absx", 0x0400)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {TBL + k: tbl[k] for k in range(n)}
    return Player(
        "ram_output",
        ORG,
        a.assemble(),
        {0x0400, 0x0401, 0x0402},
        {"indexed"},
        data=data,
        init=_RTS,
        init_org=_INIT_ORG,
    )


TEMPLATES = (
    t_table_index,
    t_smc_operand,
    t_smc_opcode,
    t_smc_branch,
    t_jmp_indirect,
    t_rts_trick,
    t_jump_table,
    t_indy,
    t_indx,
    t_illegal_lax_sax,
    t_illegal_dcp_isc,
    t_dec_timer,
    t_varlen_row,
    t_multispeed,
    t_volatile,
    t_ram_output,
)

REQUIRED_CLASSES = frozenset(
    {"smc", "dispatch", "indexed", "illegal", "dec_timer", "variable_row", "multispeed", "volatile"}
)

# Byte-defined for sidplayfp (no volatile, no magic-constant illegal): oracle-eligible.
ORACLE_SAFE = frozenset({"table_index", "smc_operand", "illegal_lax_sax", "indy", "ram_output"})


def players(per=6):
    """Deterministic corpus: ``per`` seeded instances of every template."""
    out = []
    for ti, tmpl in enumerate(TEMPLATES):
        for s in range(per):
            rng = np.random.default_rng((ti << 16) ^ (s * 0x9E3779B1) ^ 0xC0FFEE)
            p = tmpl(rng)
            p.seed = (ti, s)
            out.append(p)
    return out
