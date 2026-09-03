"""One synthetic tune in the S4 IR, and the binding's own readers over it.

Regions, names, image and a ``tick`` whose voice loop carries a clock, a fetch at
a per-voice pattern pointer and a machine segment; the T0/T1/T2 planes a binding
reads beside it; and the object one lift of the whole emits.
"""

from deity_informant.trackerprog import bind, build, read, rows
from deity_informant.trackerprog.cells import Cells
from deity_informant.trackerprog.vocab import Vocab
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
    Store,
    Var,
)
from deity_informant.tuneprog.recover import Names

FREQ, WAVE, ADSR = 0x2000, 0x2100, 0x2101
ORD, PAT = 0x2200, 0x2300
NOTE, INSC, TIMER, ORDPOS = 0x2400, 0x2403, 0x2406, 0x2409
PTRL, PTRH, SWEEP = 0x240C, 0x240F, 0x2412
CMD, GATE = 0x2415, 0x2418
GLOB, STAGE, SID = 0x2500, 0x2501, 0xD400
NOTES, VOICES, TICKS = 16, 3, 24

# one row a pair: the note or $80 to end the pattern, then dur | ins << 4 | tie
PATTERNS = [
    0x05,
    0x12,
    0x07,
    0x61,
    0x80,
    0x11,
    0x00,
    0x00,
    0x09,
    0x13,
    0x11,
    0x22,
    0x80,
    0x52,
    0x00,
    0x00,
]


def C(v, w=1):
    return Const(v, w)


def V(n, w=1):
    return Var(n, w)


def ram(addr, r, idx=None, w=1, size=3):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Load("ram", a, w, addr, addr + size - 1, r)


def store(addr, r, val, idx=None, src=0, cls="ram", size=3):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Store(cls, a, val, 1, addr, addr + size - 1, r, src)


def regions():
    return [
        Rgn(1, "freq", FREQ, 2 * NOTES + 8, "const"),
        Rgn(2, "wave", WAVE, 8, "const", stride=2),
        Rgn(3, "adsr", ADSR, 8, "const", stride=2),
        Rgn(4, "note", NOTE, 3, "state"),
        Rgn(5, "ins", INSC, 3, "state"),
        Rgn(6, "timer", TIMER, 3, "state"),
        Rgn(7, "ordpos", ORDPOS, 3, "state"),
        Rgn(8, "ptrlo", PTRL, 3, "state"),
        Rgn(9, "ptrhi", PTRH, 3, "state"),
        Rgn(10, "sweep", SWEEP, 3, "state"),
        Rgn(14, "cmd", CMD, 3, "state"),
        Rgn(15, "gate", GATE, 3, "state"),
        Rgn(11, "scratch", GLOB, 1, "state"),
        Rgn(16, "stage", STAGE, 1, "state"),
        Rgn(12, "orders", ORD, 8, "const"),
        Rgn(13, "patterns", PAT, 32, "const"),
        Rgn(20, "sid", SID, 25, "io", stride=7),
    ]


def names():
    return Names(
        region={r.id: r.name for r in regions()},
        role={1: "freq_table"},
        groups={"voice": {"stride": 1, "n": VOICES, "members": [4, 5, 6, 7, 8, 9, 10, 14, 15]}},
        view={
            4: ("voice", "note"),
            5: ("voice", "ins"),
            6: ("voice", "timer"),
            7: ("voice", "ordpos"),
            8: ("voice", "ptrlo"),
            9: ("voice", "ptrhi"),
            10: ("voice", "sweep"),
            14: ("voice", "cmd"),
            15: ("voice", "gate"),
        },
    )


def image():
    m = bytearray(0x10000)
    for i in range(NOTES + 4):
        w = 0x0100 + 7 * i
        m[FREQ + 2 * i], m[FREQ + 2 * i + 1] = w & 0xFF, w >> 8
    m[WAVE : WAVE + 8] = bytes([0x41, 0x0A, 0x21, 0x0B, 0x11, 0x0C, 0x81, 0x0D])
    m[ORD : ORD + 8] = bytes([0x00, 0x08, 0x00, 0x08, 0, 0, 0, 0])
    m[PAT : PAT + len(PATTERNS)] = bytes(PATTERNS)
    for v in range(VOICES):
        m[PTRH + v], m[SWEEP + v] = PAT >> 8, v
    m[GLOB] = 3
    return m


def tick():
    """One voice pass: the clock, the fetch at a pointer, and the machine's writes."""
    idx = V("x")
    blocks = {
        "top": Block(
            "top",
            [Let("x", C(2)), store(GLOB, 11, C(0x0F), src=0x1004, size=1)],
            Goto("head"),
            src=0x1000,
        ),
        "head": Block(
            "head",
            [
                Let("t0", ram(TIMER, 6, idx)),
                store(TIMER, 6, Bin("-", V("t0"), C(1)), idx, src=0x1010),
                store(GATE, 15, C(0), idx, src=0x1014),
            ],
            If(Bin("!=", Bin("&", Bin("-", V("t0"), C(1)), C(0x80)), C(0)), "fetch", "join"),
            src=0x1010,
        ),
        "fetch": Block(
            "fetch",
            [
                Let("pl", ram(PTRL, 8, idx)),
                Let("ph", ram(PTRH, 9, idx)),
                Let("q", Bin("|", V("pl"), Bin("<<", V("ph"), C(8), 2), 2)),
                Let("n", Load("ram", V("q", 2), 1, PAT, PAT + 31, 13)),
                Let("q1", Bin("|", V("q", 2), C(1, 2), 2)),
                Let("c", Load("ram", V("q1", 2), 1, PAT, PAT + 31, 13)),
                store(TIMER, 6, Bin("&", V("c"), C(0x0F)), idx, src=0x1020),
                Let("q2", Bin("+", V("q", 2), C(2, 2), 2)),
                store(PTRL, 8, Bin("&", V("q2", 2), C(0xFF, 2)), idx, src=0x1024),
                store(PTRH, 9, Bin(">>", V("q2", 2), C(8, 2), 2), idx, src=0x1028),
            ],
            If(Bin("!=", Bin("&", V("n"), C(0x80)), C(0)), "wrap", "keyon"),
            src=0x1020,
        ),
        "keyon": Block(
            "keyon",
            [
                store(NOTE, 4, V("n"), idx, src=0x1030),
                Let("i", Bin("&", Bin(">>", V("c"), C(4)), C(3))),
                store(INSC, 5, V("i"), idx, src=0x1034),
                store(CMD, 14, V("n"), idx, src=0x1038),
                Let("tk", Bin("&", V("c"), C(0x40))),
                store(GATE, 15, V("tk"), idx, src=0x103C),
                store(SID + 4, 20, V("i"), src=0x103E, cls="io", size=25),
            ],
            Goto("join"),
            src=0x1030,
        ),
        "wrap": Block(
            "wrap",
            [
                Let("o", ram(ORDPOS, 7, idx)),
                Let("o2", Bin("&", Bin("+", V("o"), C(1)), C(3))),
                store(ORDPOS, 7, V("o2"), idx, src=0x1040),
                Let("b", ram(ORD, 12, V("o2"), size=8)),
                store(PTRL, 8, V("b"), idx, src=0x1044),
                store(PTRH, 9, C(PAT >> 8), idx, src=0x1048),
                store(CMD, 14, C(0), idx, src=0x104C),
                store(GLOB, 11, C(0x0E), src=0x104E, size=1),
            ],
            Goto("join"),
            src=0x1040,
        ),
        "join": Block("join", [], Goto("mach"), src=0x1050),
        "mach": Block(
            "mach",
            [
                Let("f", ram(FREQ, 1, Bin("<<", ram(NOTE, 4, idx), C(1)), size=40)),
                store(SID, 20, V("f"), src=0x1060, cls="io", size=25),
                Let("g", ram(FREQ + 1, 1, Bin("<<", ram(NOTE, 4, idx), C(1)), size=39)),
                store(SID + 1, 20, V("g"), src=0x1064, cls="io", size=25),
                Let("sw", ram(SWEEP, 10, idx)),
                Let("sw2", Bin("+", V("sw"), C(1))),
                store(SWEEP, 10, V("sw2"), idx, src=0x1068),
                store(SID + 2, 20, V("sw2"), src=0x106C, cls="io", size=25),
                Let("w1", ram(WAVE, 2, Bin("<<", ram(INSC, 5, idx), C(1)), size=8)),
                store(SID + 4, 20, V("w1"), src=0x1070, cls="io", size=25),
                Let("a1", ram(ADSR, 3, Bin("<<", ram(INSC, 5, idx), C(1)), size=8)),
                store(SID + 5, 20, V("a1"), src=0x1074, cls="io", size=25),
                store(SID + 6, 20, V("a1"), src=0x1078, cls="io", size=25),
                Let("pwh", ram(CMD, 14, idx)),
                store(SID + 3, 20, V("pwh"), src=0x107C, cls="io", size=25),
                store(STAGE, 16, ram(CMD, 14, idx), src=0x1098, size=1),
                Let("mv", ram(GLOB, 11, size=1)),
                store(SID + 24, 20, V("mv"), src=0x1090, cls="io", size=25),
            ],
            Goto("tail"),
            src=0x1060,
        ),
        "tail": Block("tail", [], If(Bin("==", V("x"), C(0)), "out", "back"), src=0x1080),
        "back": Block("back", [Let("x", Bin("-", V("x"), C(1)))], Goto("head"), src=0x1084),
        "out": Block("out", [], Return(vals=[]), src=0x1088),
    }
    return Proc("tick", blocks=blocks, entry="top")


class View:
    """The presentation view a binding reads: its regions, its procs and its image."""

    def __init__(self, storage, procs, img):
        self.storage, self.procs, self.img = storage, procs, img

    def by_id(self):
        return {r.id: r for r in self.storage}

    def reads(self):
        return self.img


class Prog:
    """One certified tune as the binding reads it: the tick, its regions, its image."""

    def __init__(self):
        self.procs = {
            "tick": tick(),
            "init": Proc("init", blocks={"i": Block("i", [], Return(vals=[]))}, entry="i"),
        }
        self.storage = regions()
        self.img = image()
        self.inputs = []
        self.meta = {
            "tick_proc": "tick",
            "init_proc": "init",
            "name": "synth",
            "song": 0,
            "entry": {"kind": "sub", "cycles_per_tick": 19656},
            "load": (0x1000, 0x2600),
        }

    def by_id(self):
        return {r.id: r for r in self.storage}

    def reads(self):
        return self.img

    def image(self):
        return self.img


def t0():
    def w(reg, pc, rid):
        return {"register": reg, "site": {"pc": pc, "block": "mach"}, "cells": [{"region": rid}]}

    return {
        "writes": [
            w("freq_lo", "$1060", 4),
            w("freq_hi", "$1064", 4),
            w("pw_lo", "$106C", 10),
            w("ctrl", "$1070", 5),
            w("ad", "$1074", 5),
            w("sr", "$1078", 5),
        ]
    }


def t1():
    return {
        "accs": [
            {
                "id": "a0",
                "cell": {
                    "addr": "$%04X" % SWEEP,
                    "region": 10,
                    "copies": VOICES,
                    "name": "sweep",
                    "width": 8,
                },
                "regions": [10],
                "width": 8,
                "target": {"register": "pw_lo"},
                "policy": "free",
                "scope": "voice",
                "sites": ["$1068"],
                "delta": {"kind": "const", "value": 1},
            }
        ],
        "refusals": [],
    }


def t2():
    return {
        "pitch": {"layout": "u16le", "entries": [0x0100 + 7 * i for i in range(NOTES)]},
        "selectors": [
            {
                "kind": "selector",
                "cursor": "ins@$%04X" % INSC,
                "entries": 4,
                "visited": [0, 1, 2, 3],
                "columns": [{"table": "wave", "stride": 2}, {"table": "adsr", "stride": 2}],
            }
        ],
        "streams": [],
        "score": [
            {
                "order": [{"table": "orders", "cursor": "ordpos@$%04X" % ORDPOS}],
                "pattern": [{"table": "patterns", "cursor": "ptrlo@$%04X" % PTRL}],
            }
        ],
        "horizon": {"ticks": TICKS},
    }


def art():
    prog = Prog()
    return {
        "prog": prog,
        "view": View(regions(), prog.procs, prog.img),
        "names": names(),
        "t0": t0(),
        "t1": t1(),
        "t2": t2(),
        "cert": {},
    }


def reader():
    """A :class:`~.read.Reader` over the tune, with the vocabulary a binding gives it."""
    view = View(regions(), {"tick": tick()}, image())
    cells = Cells(view, names(), pitch=((1,), (FREQ, FREQ + 1), 2, NOTES))
    voc = Vocab(cells, image(), build.registers(), frozenset({"x"}))
    voc.supplied = {"n", "c"}
    voc.pitch = ((1,), (FREQ, FREQ + 1), 2, NOTES)
    voc.notebase, voc.insbase = NOTE, INSC
    voc.inscol, voc.insstride = {2: "wave", 3: "adsr"}, 2
    return read.Reader(Prog(), "tick", cells, voc), voc


def other(proc):
    """A reader over one hand-built procedure, with the tune's own regions under it."""
    prog = Prog()
    prog.procs["tick"] = proc
    view = View(regions(), {"tick": proc}, image())
    cells = Cells(view, names())
    voc = Vocab(cells, image(), build.registers(), frozenset())
    return read.Reader(prog, "tick", cells, voc)


_BOUND = []


def bound():
    """The object and the report the binding emits for the tune, computed once."""
    if not _BOUND:
        _BOUND.append(bind.lift(art(), ticks=TICKS))
    return _BOUND[0]


def binder():
    """A binder with its roles bound, its horizon recorded and its fields named."""
    b = bind.Binder(art(), ticks=TICKS)
    b.roles()
    b.supplied()
    b.bind_fields(b.visits())
    b.amb = rows.ambiguous(b.p)
    b.plan(b.low.rpo)
    return b
