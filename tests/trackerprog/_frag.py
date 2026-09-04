"""The idiom fragments the pass proof is written over: hand-built S4 IR.

One tiny tune the interpreter renders -- a tuning, per-voice state, a pattern
table and the chip at its own stride -- and the constructors the six idiom test
files build their fragments with.
"""

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
    Tuneprog,
    Var,
)
from deity_informant.tuneprog.recover import Names

FREQ, FREQLO, FREQHI = 0x2000, 0x2600, 0x2700
NOTE, INS, TIMER, ORDPOS, CURSOR, ACC, DIR = (
    0x2400,
    0x2403,
    0x2406,
    0x2409,
    0x240C,
    0x240F,
    0x2412,
)
ORD, PAT, GLOB, SID, IMG = 0x2200, 0x2300, 0x2500, 0xD400, 0x2540
NOTES, VOICES, STRIDE = 16, 3, 1
CHIP = 7


def C(v, w=1):
    return Const(v, w)


def V(n, w=1):
    return Var(n, w)


def ram(addr, r, idx=None, w=1, size=VOICES):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Load("ram", a, w, addr, addr + size - 1, r)


def store(addr, r, val, idx=None, src=0, cls="ram", size=VOICES, w=1):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Store(cls, a, val, w, addr, addr + size - 1, r, src)


def sid(off, val, idx=None, src=0):
    """One write to the chip: the register of the voice the index names.

    The index is the voice's own base in the register file -- the ``7v`` every
    family of the nine holds in a register -- and not the voice number.
    """
    return store(SID + off, 20, val, idx, src=src, cls="io", size=25)


def regions(split=False):
    """The tune's storage; ``split`` gives the tuning as two byte tables."""
    pit = (
        [Rgn(1, "freqlo", FREQLO, NOTES, "const"), Rgn(21, "freqhi", FREQHI, NOTES, "const")]
        if split
        else [Rgn(1, "freq", FREQ, 2 * NOTES + 24, "const")]
    )
    return pit + [
        Rgn(4, "note", NOTE, VOICES, "state"),
        Rgn(5, "ins", INS, VOICES, "state"),
        Rgn(6, "timer", TIMER, VOICES, "state"),
        Rgn(7, "ordpos", ORDPOS, VOICES, "state"),
        Rgn(8, "cursor", CURSOR, VOICES, "state"),
        Rgn(9, "acc", ACC, VOICES, "state"),
        Rgn(10, "dir", DIR, VOICES, "state"),
        Rgn(11, "scratch", GLOB, 1, "state"),
        Rgn(12, "orders", ORD, 8, "const"),
        Rgn(13, "patterns", PAT, 32, "const"),
        Rgn(20, "sid", SID, 25, "io", stride=CHIP),
        Rgn(30, "image", IMG, 25, "state", stride=CHIP),
    ]


def names(split=False):
    voice = [4, 5, 6, 7, 8, 9, 10]
    return Names(
        region={r.id: r.name for r in regions(split)},
        role={1: "freq_table"} if not split else {1: "freq_table", 21: "freq_table"},
        groups={"voice": {"stride": STRIDE, "n": VOICES, "members": voice}},
        view={
            4: ("voice", "note"),
            5: ("voice", "ins"),
            6: ("voice", "timer"),
            7: ("voice", "ordpos"),
            8: ("voice", "cursor"),
            9: ("voice", "acc"),
            10: ("voice", "dir"),
        },
    )


PATTERNS = [0x05, 0x12, 0x07, 0x61, 0x80, 0x11, 0x09, 0x13, 0x11, 0x22, 0x80, 0x52] + [0] * 20


def image(split=False):
    m = bytearray(0x10000)
    for i in range(NOTES + 12):
        w = 0x0100 + 7 * i
        if split:
            if i < NOTES:
                m[FREQLO + i], m[FREQHI + i] = w & 0xFF, w >> 8
        else:
            m[FREQ + 2 * i], m[FREQ + 2 * i + 1] = w & 0xFF, w >> 8
    m[ORD : ORD + 8] = bytes([0x00, 0x06, 0x00, 0x06, 0, 0, 0, 0])
    m[PAT : PAT + len(PATTERNS)] = bytes(PATTERNS)
    for v in range(VOICES):
        m[TIMER + v], m[CURSOR + v], m[ACC + v] = 1, 0, 0
    m[GLOB] = 0x0F
    for v in range(VOICES):
        m[IMG + CHIP * v + 4] = 0x10
    return m


class View:
    """The presentation view the passes read: the regions, the procs and the image."""

    def __init__(self, storage, procs, img):
        self.storage, self.procs, self.img = storage, procs, img

    def by_id(self):
        return {r.id: r for r in self.storage}

    def reads(self):
        return self.img


def prog_of(procs, split=False, img=None):
    """One tune the interpreter renders: the procedures, the storage and the image."""
    meta = {
        "tick_proc": "tick",
        "init_proc": "init",
        "name": "fragment",
        "song": 0,
        "entry": {"kind": "sub", "cycles_per_tick": 19656},
        "load": (0x1000, 0x2800),
    }
    got = dict(procs)
    got.setdefault("init", Proc("init", blocks={"i": Block("i", [], Return(vals=[]))}, entry="i"))
    m = image(split) if img is None else img
    st = regions(split)
    for r in st:
        if r.kind != "io":
            r.init = bytes(m[r.base : r.base + r.size])
    return Tuneprog(meta, st, [], got)


def art_of(prog, split=False, t0=None, t1=None, t2=None):
    """The planes a fragment carries: what the levels read beside the program."""
    return {
        "prog": prog,
        "view": View(list(prog.storage), prog.procs, prog.image()),
        "names": names(split),
        "t0": t0 or {"writes": []},
        "t1": t1 or {"accs": [], "refusals": []},
        "t2": t2 or t2_of(split),
        "cert": {},
    }


def t2_of(split=False):
    ent = [0x0100 + 7 * i for i in range(NOTES)]
    return {
        "pitch": {
            "layout": "lo|hi" if split else "u16le",
            "entries": ent,
            **({"regions": [1, 21]} if split else {}),
        },
        "selectors": [
            {
                "kind": "selector",
                "cursor": "ins@$%04X" % INS,
                "entries": 2,
                "visited": [0, 1],
                "columns": [],
            }
        ],
        "streams": [],
        "score": [
            {
                "order": [{"table": "orders", "cursor": "ordpos@$%04X" % ORDPOS}],
                "pattern": [{"table": "patterns", "cursor": "cursor@$%04X" % CURSOR}],
            }
        ],
        "horizon": {"ticks": 24},
    }


VIDX, VIDX7 = "x", "x7"


def voiceblocks(body, head="head", tail="out", src=0x1000):
    """One voice loop over ``body``: the two indices, the pass and the latch.

    A voice has two: its own number, which its cells stand at, and its base in
    the register file, which the chip's own stride puts them at.
    """
    return {
        "top": Block(
            "top",
            [Let(VIDX, C(VOICES - 1)), Let(VIDX7, C(CHIP * (VOICES - 1), 2))],
            Goto(head),
            src=src,
        ),
        head: Block(head, body[0], body[1], src=src + 0x10),
        "back": Block(
            "back",
            [
                Let(VIDX, Bin("-", V(VIDX), C(1))),
                Let(VIDX7, Bin("-", V(VIDX7, 2), C(CHIP, 2), 2)),
            ],
            If(Bin("!=", V(VIDX), C(0xFF), 1), head, tail),
            src=src + 0x80,
        ),
        tail: Block(tail, [], Return(vals=[]), src=src + 0x90),
    }


def flushblocks(nxt, src=0x1F00):
    """The tick's own first act: the image emptied into the chip, register by register."""
    return {
        "fl": Block("fl", [Let("i", C(0, 2))], Goto("fb"), src=src),
        "fb": Block(
            "fb",
            [
                Store(
                    "io",
                    Bin("+", C(SID, 2), V("i", 2), 2),
                    Load("ram", Bin("+", C(IMG, 2), V("i", 2), 2), 1, IMG, IMG + 24, 30),
                    1,
                    SID,
                    SID + 24,
                    20,
                    src,
                ),
                Let("i", Bin("+", V("i", 2), C(1, 2), 2)),
            ],
            If(Bin("!=", V("i", 2), C(25, 2), 1), "fb", nxt),
            src=src + 4,
        ),
    }


def flusht0(regs, pc=0x1F00):
    """T0 for a shadowed tune: every write lands in the image, and one site empties it."""
    img = {"region": 30, "delta": (SID - IMG) & 0xFFFF, "flush_pc": "$%04X" % pc}
    return {
        "writes": [
            {"register": r, "site": {"pc": "$%04X" % s, "block": ""}, "image": img} for r, s in regs
        ]
    }
