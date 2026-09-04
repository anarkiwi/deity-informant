"""One synthetic tune in the S4 IR, carrying the idioms the whole pipeline meets.

A flush of the register file every write lands in; a run of unrolled sibling
blocks the first call runs, which the structuring rerolls; a voice loop with two
indices; a countdown clock whose row reloads it; a fetch that reads its row one
clock step ahead and decodes it byte by byte; a two-armed slide with a direction
cell and a bounce that turns it; a cursor over a wave table; and a tuning the
machine reads the voice's note out of.
"""

from _frag import C, V, sid, store
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Const,
    Goto,
    If,
    Let,
    Load,
    Proc,
    Return,
    Rgn,
    Tuneprog,
)
from deity_informant.tuneprog.recover import Names

FREQ, WAVE, ADSR, WTAB = 0x3000, 0x3100, 0x3110, 0x3120
ORD, PAT = 0x3200, 0x3240
NOTE, INS, TIMER, ORDPOS, CURSOR, ACC, DIR, WPOS, RPT = (
    0x3300,
    0x3303,
    0x3306,
    0x3309,
    0x330C,
    0x330F,
    0x3312,
    0x3315,
    0x3318,
)
GLOB, IMG, SID = 0x3400, 0x3440, 0xD400
NOTES, VOICES, CHIP, TICKS = 16, 3, 7, 300
PATTERNS = [0x05, 0x12, 0x27, 0x61, 0x80, 0x39, 0x11, 0x22, 0x80, 0x52, 0x03, 0x80] + [0] * 20


def ram(addr, r, idx=None, w=1, size=VOICES):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Load("ram", a, w, addr, addr + size - 1, r)


def regions():
    return [
        Rgn(1, "freq", FREQ, 2 * NOTES, "const"),
        Rgn(2, "wave", WAVE, 8, "const", stride=2),
        Rgn(3, "adsr", ADSR, 8, "const", stride=2),
        Rgn(4, "note", NOTE, VOICES, "state"),
        Rgn(5, "ins", INS, VOICES, "state"),
        Rgn(6, "timer", TIMER, VOICES, "state"),
        Rgn(7, "ordpos", ORDPOS, VOICES, "state"),
        Rgn(8, "cursor", CURSOR, VOICES, "state"),
        Rgn(9, "acc", ACC, VOICES, "state"),
        Rgn(10, "dir", DIR, VOICES, "state"),
        Rgn(11, "wpos", WPOS, VOICES, "state"),
        Rgn(16, "rpt", RPT, VOICES, "state"),
        Rgn(12, "scratch", GLOB, 1, "state"),
        Rgn(13, "orders", ORD, 8, "const"),
        Rgn(14, "patterns", PAT, 32, "const"),
        Rgn(15, "wtab", WTAB, 8, "const"),
        Rgn(20, "sid", SID, 25, "io", stride=CHIP),
        Rgn(30, "image", IMG, 25, "state", stride=CHIP),
    ]


def names():
    voice = [4, 5, 6, 7, 8, 9, 10, 11, 16]
    return Names(
        region={r.id: r.name for r in regions()},
        role={1: "freq_table"},
        groups={"voice": {"stride": 1, "n": VOICES, "members": voice}},
        view={
            4: ("voice", "note"),
            5: ("voice", "ins"),
            6: ("voice", "timer"),
            7: ("voice", "ordpos"),
            8: ("voice", "cursor"),
            9: ("voice", "acc"),
            10: ("voice", "dir"),
            11: ("voice", "wpos"),
            16: ("voice", "rpt"),
        },
    )


def image():
    m = bytearray(0x10000)
    for i in range(NOTES):
        w = 0x0400 + 61 * i
        m[FREQ + 2 * i], m[FREQ + 2 * i + 1] = w & 0xFF, w >> 8
    m[WAVE : WAVE + 8] = bytes([0x41, 0x0A, 0x21, 0x0B, 0x11, 0x0C, 0x81, 0x0D])
    m[ADSR : ADSR + 8] = bytes([0x09, 0x0A, 0x1A, 0x0B, 0x2B, 0x0C, 0x3C, 0x0D])
    m[WTAB : WTAB + 8] = bytes([0x10, 0x20, 0x40, 0x80, 0x80, 0x40, 0x20, 0x10])
    m[ORD : ORD + 8] = bytes([0x00, 0x05, 0x09, 0x00, 0, 0, 0, 0])
    m[PAT : PAT + len(PATTERNS)] = bytes(PATTERNS)
    m[GLOB] = 1
    for v in range(VOICES):
        m[TIMER + v], m[RPT + v] = 1, 2
    return m


def _flush(nxt):
    """The tick's own first act: the image emptied into the chip, one store."""
    return {
        "fl": Block("fl", [Let("i", C(0, 2))], Goto("fb"), src=0x1F00),
        "fb": Block(
            "fb",
            [
                store(
                    SID,
                    20,
                    ram(IMG, 30, V("i", 2), size=25),
                    V("i", 2),
                    src=0x1F00,
                    cls="io",
                    size=25,
                ),
                Let("i", Bin("+", V("i", 2), C(1, 2), 2)),
            ],
            If(Bin("!=", V("i", 2), C(25, 2), 1), "fb", nxt),
            src=0x1F04,
        ),
    }


def _reset():
    """Three unrolled sibling copies the first call runs: one pass over the voices."""
    out = {}
    for v in range(VOICES):
        nxt = "r%d" % (v + 1) if v + 1 < VOICES else "top"
        out["r%d" % v] = Block(
            "r%d" % v,
            [
                store(CURSOR + v, 8, C(0), src=0x1800 + 8 * v),
                store(DIR + v, 10, C(0), src=0x1802 + 8 * v),
                store(WPOS + v, 11, C(0), src=0x1804 + 8 * v),
            ],
            Goto(nxt),
            src=0x1800 + 8 * v,
        )
    return out


def tick():  # noqa: C901 - one clause per block of the tune
    """The tick: the flush, the prologue, the voice loop, its fetch and its machine."""
    idx, at = V("x"), V("x7", 2)
    blocks = {
        "pre": Block(
            "pre",
            [Let("g", ram(GLOB, 12, size=1)), store(GLOB, 12, C(0), size=1, src=0x1810)],
            If(Bin("!=", V("g"), C(0)), "r0", "top"),
            src=0x1810,
        ),
        "top": Block(
            "top",
            [Let("x", C(VOICES - 1)), Let("x7", C(CHIP * (VOICES - 1), 2))],
            Goto("head"),
            src=0x1000,
        ),
        "head": Block(
            "head",
            [
                Let("t", ram(TIMER, 6, idx)),
                store(TIMER, 6, Bin("-", V("t"), C(1)), idx, src=0x1010),
            ],
            If(Bin("!=", V("t"), C(1)), "mach", "fetch"),
            src=0x1010,
        ),
        "fetch": Block(
            "fetch",
            [
                Let("c", ram(CURSOR, 8, idx)),
                Let("n", Load("ram", Bin("+", C(PAT, 2), V("c"), 2), 1, PAT, PAT + 31, 14)),
            ],
            If(Bin("!=", Bin("&", V("n"), C(0x80)), C(0)), "wrap", "key"),
            src=0x1020,
        ),
        "wrap": Block(
            "wrap",
            [
                Let("o", Bin("&", Bin("+", ram(ORDPOS, 7, idx), C(1)), C(3))),
                store(ORDPOS, 7, V("o"), idx, src=0x1030),
                store(CURSOR, 8, ram(ORD, 13, V("o"), size=8), idx, src=0x1032),
                store(TIMER, 6, C(6), idx, src=0x1034),
            ],
            Goto("mach"),
            src=0x1030,
        ),
        "key": Block(
            "key",
            [
                store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
                store(INS, 5, Bin("&", Bin(">>", V("n"), C(4)), C(3)), idx, src=0x1042),
                store(CURSOR, 8, Bin("&", Bin("+", V("c"), C(1)), C(0x0F)), idx, src=0x1044),
                store(TIMER, 6, C(6), idx, src=0x1046),
            ],
            Goto("mach"),
            src=0x1040,
        ),
        "mach": Block(
            "mach",
            [Let("d", ram(DIR, 10, idx)), Let("a", ram(ACC, 9, idx))],
            If(Bin("!=", Bin("&", V("d"), C(1)), C(0)), "down", "up"),
            src=0x1050,
        ),
        "down": Block(
            "down",
            [Let("a2", Bin("-", V("a"), C(3))), store(ACC, 9, V("a2"), idx, src=0x1054)],
            If(Bin("<", V("a2"), C(0x20)), "flip", "rseed"),
            src=0x1054,
        ),
        "up": Block(
            "up",
            [Let("a2", Bin("+", V("a"), C(3))), store(ACC, 9, V("a2"), idx, src=0x1058)],
            If(Bin("<", C(0xE0), V("a2")), "flip", "rseed"),
            src=0x1058,
        ),
        "flip": Block(
            "flip",
            [store(DIR, 10, Bin("^", ram(DIR, 10, idx), C(1)), idx, src=0x105C)],
            Goto("rseed"),
            src=0x105C,
        ),
        "rseed": Block(
            "rseed",
            [store(RPT, 16, C(2), idx, src=0x1070)],
            Goto("rloop"),
            src=0x1070,
        ),
        "rloop": Block(
            "rloop",
            [
                Let("ra", ram(ACC, 9, idx)),
                store(ACC, 9, Bin("+", V("ra"), C(1)), idx, src=0x1074),
                Let("rc", ram(RPT, 16, idx)),
                store(RPT, 16, Bin("-", V("rc"), C(1)), idx, src=0x1076),
            ],
            If(Bin("!=", ram(RPT, 16, idx), C(0)), "rloop", "join"),
            src=0x1074,
        ),
        "join": Block(
            "join",
            [
                Let("p", ram(WPOS, 11, idx)),
                store(WPOS, 11, Bin("&", Bin("+", V("p"), C(1)), C(7)), idx, src=0x1060),
                store(IMG + 2, 30, ram(WTAB, 15, V("p"), size=8), at, src=0x1062, size=23),
                Let("k", Bin("<<", ram(NOTE, 4, idx), C(1))),
                Let("flo", ram(FREQ, 1, V("k"), size=2 * NOTES)),
                Let("fhi", ram(FREQ + 1, 1, V("k"), size=2 * NOTES - 1)),
                store(IMG, 30, V("flo"), at, src=0x1064, size=25),
                store(IMG + 1, 30, V("fhi"), at, src=0x1066, size=24),
                Let("j", Bin("<<", ram(INS, 5, idx), C(1))),
                Let("wv", ram(WAVE, 2, V("j"), size=8)),
                Let("av", ram(ADSR, 3, V("j"), size=8)),
                store(IMG + 4, 30, V("wv"), at, src=0x1068, size=21),
                store(IMG + 5, 30, V("av"), at, src=0x106A, size=20),
                store(IMG + 6, 30, ram(ACC, 9, idx), at, src=0x106C, size=19),
            ],
            Goto("back"),
            src=0x1060,
        ),
        "back": Block(
            "back",
            [
                Let("x", Bin("-", V("x"), C(1))),
                Let("x7", Bin("-", V("x7", 2), C(CHIP, 2), 2)),
            ],
            If(Bin("!=", V("x"), C(0xFF), 1), "head", "out"),
            src=0x1080,
        ),
        "out": Block("out", [], Return(vals=[]), src=0x1090),
    }
    blocks.update(_reset())
    blocks.update(_flush("pre"))
    return Proc("tick", blocks=blocks, entry="fl")


def prog():
    """The tune as the levels read it: its procedures, its storage and its image."""
    meta = {
        "tick_proc": "tick",
        "init_proc": "init",
        "name": "pipeline",
        "song": 0,
        "entry": {"kind": "sub", "cycles_per_tick": 19656},
        "load": (0x1000, 0x3500),
    }
    m, st = image(), regions()
    for r in st:
        if r.kind != "io":
            r.init = bytes(m[r.base : r.base + r.size])
    procs = {
        "tick": tick(),
        "init": Proc("init", blocks={"i": Block("i", [], Return(vals=[]))}, entry="i"),
    }
    return Tuneprog(meta, st, [], procs)


class View:
    def __init__(self, storage, procs, img):
        self.storage, self.procs, self.img = storage, procs, img

    def by_id(self):
        return {r.id: r for r in self.storage}

    def reads(self):
        return self.img


def t0():
    img = {"region": 30, "delta": (SID - IMG) & 0xFFFF, "flush_pc": "$1F00"}
    regs = [("pw_lo", 0x1062), ("freq_lo", 0x1064), ("freq_hi", 0x1066)]
    regs += [("ctrl", 0x1068), ("ad", 0x106A), ("sr", 0x106C)]
    return {
        "writes": [
            {"register": r, "site": {"pc": "$%04X" % s, "block": "join"}, "image": img}
            for r, s in regs
        ]
    }


def t2():
    return {
        "pitch": {"layout": "u16le", "entries": [0x0400 + 61 * i for i in range(NOTES)]},
        "selectors": [
            {
                "kind": "selector",
                "cursor": "ins@$%04X" % INS,
                "entries": 4,
                "visited": [0, 1, 2, 3],
                "columns": [{"table": "wave", "stride": 2}, {"table": "adsr", "stride": 2}],
            }
        ],
        "streams": [],
        "score": [
            {
                "order": [{"table": "orders", "cursor": "ordpos@$%04X" % ORDPOS}],
                "pattern": [{"table": "patterns", "cursor": "cursor@$%04X" % CURSOR}],
            }
        ],
        "horizon": {"ticks": TICKS},
    }


def art():
    """The planes the pipeline reads beside the program."""
    p = prog()
    return {
        "prog": p,
        "view": View(list(p.storage), p.procs, p.image()),
        "names": names(),
        "t0": t0(),
        "t1": {"accs": [], "refusals": []},
        "t2": t2(),
        "cert": {},
    }


del Const, sid
