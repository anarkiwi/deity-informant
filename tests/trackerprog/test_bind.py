"""B7's binding on one synthetic tune: the reader, the flow facts and the object.

Hermetic. The tune is built in the S4 IR -- regions, names, image and a ``tick``
whose voice loop carries a clock, a fetch at a per-voice pointer and a machine
segment -- and the whole of :mod:`~deity_informant.trackerprog.bind` runs on it.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from deity_informant.trackerprog import bind, build, read  # noqa: E402
from deity_informant.trackerprog.cells import Cells  # noqa: E402
from deity_informant.trackerprog.refuse import Refused  # noqa: E402
from deity_informant.trackerprog.vocab import Vocab  # noqa: E402
from deity_informant.tuneprog.ir import (  # noqa: E402
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
    Switch,
    Var,
)
from deity_informant.tuneprog.recover import Names  # noqa: E402

FREQ, WAVE, ADSR = 0x2000, 0x2100, 0x2101
ORD, PAT = 0x2200, 0x2300
NOTE, INSC, TIMER, ORDPOS = 0x2400, 0x2403, 0x2406, 0x2409
PTRL, PTRH, SWEEP = 0x240C, 0x240F, 0x2412
GLOB, SID = 0x2500, 0xD400
NOTES, VOICES, TICKS = 16, 3, 24

PATTERNS = [
    0x05, 0x12, 0x07, 0x21, 0x80, 0x11, 0x00, 0x00,
    0x09, 0x13, 0x0B, 0x22, 0x80, 0x12, 0x00, 0x00,
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
        Rgn(11, "scratch", GLOB, 1, "state"),
        Rgn(12, "orders", ORD, 8, "const"),
        Rgn(13, "patterns", PAT, 32, "const"),
        Rgn(20, "sid", SID, 25, "io", stride=7),
    ]


def names():
    return Names(
        region={r.id: r.name for r in regions()},
        role={1: "freq_table"},
        groups={"voice": {"stride": 1, "n": VOICES, "members": [4, 5, 6, 7, 8, 9, 10]}},
        view={
            4: ("voice", "note"),
            5: ("voice", "ins"),
            6: ("voice", "timer"),
            7: ("voice", "ordpos"),
            8: ("voice", "ptrlo"),
            9: ("voice", "ptrhi"),
            10: ("voice", "sweep"),
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
    return m


def tick():
    """One voice pass: the clock, the fetch at a pointer, and the machine's writes."""
    idx = V("x")
    blocks = {
        "top": Block("top", [Let("x", C(2))], Goto("head"), src=0x1000),
        "head": Block(
            "head",
            [
                Let("t0", ram(TIMER, 6, idx)),
                store(TIMER, 6, Bin("-", V("t0"), C(1)), idx, src=0x1010),
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
                Let("q1", Bin("+", V("q", 2), C(1, 2), 2)),
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
                Let("i", Bin(">>", V("c"), C(4))),
                store(INSC, 5, V("i"), idx, src=0x1034),
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
                "cell": {"addr": "$%04X" % SWEEP, "region": 10, "copies": VOICES,
                         "name": "sweep", "width": 8},
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


def diamond():
    """A join two paths reach that no fold makes one: the object states it as a cell."""
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block("a", [Let("y", C(1))], If(Bin("!=", V("y"), C(0)), "b", "c"), src=0x2000),
            "b": Block("b", [store(GLOB, 11, C(1), src=0x2004, size=1)], Goto("e"), src=0x2004),
            "c": Block("c", [], If(Bin("==", V("y"), C(1)), "d", "e"), src=0x2008),
            "d": Block("d", [store(GLOB, 11, C(2), src=0x200C, size=1)], Goto("g"), src=0x200C),
            "g": Block("g", [store(GLOB, 11, C(4), src=0x2018, size=1)], Goto("e"), src=0x2018),
            "e": Block("e", [store(GLOB, 11, C(3), src=0x2010, size=1)], Goto("f"), src=0x2010),
            "f": Block("f", [], Return(vals=[]), src=0x2014),
        },
    )


def dispatch():
    """A jump table whose edges decide a term, and one label two cases reach."""
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block(
                "a",
                [Let("y", C(1))],
                Switch(V("y"), ((0, "b"), (1, "c"), (2, "d"), (3, "d"))),
                src=0x3000,
            ),
            "b": Block("b", [store(GLOB, 11, C(1), src=0x3004, size=1)], Goto("e"), src=0x3004),
            "c": Block("c", [store(GLOB, 11, C(2), src=0x3008, size=1)], Goto("e"), src=0x3008),
            "d": Block("d", [store(GLOB, 11, C(3), src=0x300C, size=1)], Goto("e"), src=0x300C),
            "e": Block("e", [], Return(vals=[]), src=0x3010),
        },
    )


def other(proc):
    """A reader over one hand-built procedure, with the tune's own regions under it."""
    prog = Prog()
    prog.procs["tick"] = proc
    view = View(regions(), {"tick": proc}, image())
    cells = Cells(view, names())
    voc = Vocab(cells, image(), build.registers(), frozenset())
    return read.Reader(prog, "tick", cells, voc)


# ---- read.py: the leaves and the arithmetic ------------------------------------
def test_a_left_shift_is_the_adds_the_object_has():
    assert read._shl(4, 2, 1) == 16
    assert read._shl({"cell": "a"}, 0, 1) == {"cell": "a"}
    assert read._shl({"cell": "a"}, 1, 1) == {"and": [{"shl": [{"cell": "a"}, 1]}, 0xFF]}


def test_a_value_is_held_to_the_width_the_machine_gives_it():
    assert read.masked(0x1FF, 1) == 0xFF
    assert read.masked({"cell": "a"}, 2) == {"and": [{"cell": "a"}, 0xFFFF]}
    assert read.masked({"cell": "a"}, 4) == {"and": [{"cell": "a"}, 0xFFFF]}


def test_one_bit_of_a_mask_is_the_bit_and_a_wider_mask_is_the_zero_test():
    assert read._bitof(0x80) == 7 and read._bitof(0x81) is None and read._bitof(0) is None
    e = Bin("!=", Bin("&", V("n"), C(4)), C(0))
    assert read._truth({"and": [{"cell": "tn"}, 4]}, 0, "!=", 1, e) == {"bit": [{"cell": "tn"}, 2]}
    got = read._truth({"and": [{"cell": "tn"}, 4]}, 0, "==", 1, e)
    assert got == {"xor": [{"bit": [{"cell": "tn"}, 2]}, 1]}
    wide = Bin("!=", Bin("&", V("n"), C(6)), C(0))
    assert "carry_out" in read._truth({"cell": "tn"}, 0, "!=", 1, wide)


def test_a_comparison_in_a_value_position_is_the_chips_own_zero_test():
    low, _voc = reader()
    low.lbl = "mach"
    assert low.value(Bin("!=", Bin("&", V("n"), C(4)), C(0))) == {"bit": [{"cell": "tn"}, 2]}
    assert "borrow_out" in low.value(Bin("==", V("n"), C(3)))
    assert low.value(Bin("<", V("n"), C(3)))["carry_out"][1] == 8
    assert low.value(Bin("<=", V("n"), C(3)))["borrow_out"][1] == 8
    assert low.value(Bin("carry", V("n"), C(3)))["carry_out"][1] == 8
    assert low.value(Bin("|", V("n"), C(3))) == {"or": [{"cell": "tn"}, 3]}
    assert low.value(Bin("+", V("n"), C(3))) == {"and": [{"add": [{"cell": "tn"}, 3]}, 0xFF]}
    assert low.value(Bin(">>", V("n"), C(3))) == {"shr": [{"cell": "tn"}, 3]}


def test_a_name_is_the_cell_the_object_gives_it_or_no_name_at_all():
    low, voc = reader()
    low.lbl = "mach"
    low.local = {"z": 7}
    assert low.value(V("z")) == 7 and low.expand(V("z")) == C(7)
    low.local = {}
    voc.subst = {"t0": {"cell": "phase"}}
    assert low.value(V("t0")) == {"cell": "phase"}
    voc.subst = {}
    assert low.value(V("x")) == {"cell": "voice_index"}
    assert low.value(V("n")) == {"cell": "tn"} and low.temps["n"] == "tn"
    low.sub = {repr(V("n")): {"cell": "staged"}}
    assert low.value(V("n")) == {"cell": "staged"}
    low.sub = {}
    with pytest.raises(read.Unlowerable):
        low.value(V("nosuchname"))


def test_a_temp_is_one_cell_a_voice_and_a_scalar_is_the_tunes_own_global():
    low, _voc = reader()
    assert low.temp("n", 2) == "tn" and "tn" in low.wide
    low.scalars = frozenset({"g1"})
    assert low.temp("g1") == "#tg1"
    assert read.Reader.tref("#tg1") == {"global": "tg1"}
    assert read.Reader.tref("tn") == {"cell": "tn"}


def test_a_masked_score_byte_is_the_event_field_only_where_a_payload_stands():
    low, voc = reader()
    low.lbl = "keyon"
    voc.fields = {("c", 0x0F): "dur_fact"}
    assert low.field(V("c"), 0x0F) == "dur_fact"
    assert low.field(V("n"), 0x0F) is None
    assert low.value(Bin("&", V("c"), C(0x0F))) == "dur_fact"
    voc.payload = False
    assert low.value(Bin("&", V("c"), C(0x0F))) == {"and": [{"cell": "tc"}, 0x0F]}


def test_an_operator_the_object_has_no_form_for_is_refused():
    low, _voc = reader()
    low.lbl = "mach"
    with pytest.raises(read.Unlowerable):
        low.value(Bin("<<", V("n"), V("c")))
    with pytest.raises(read.Unlowerable):
        low.value(Bin("%", V("n"), C(3)))
    with pytest.raises(read.Unlowerable):
        low.value(object())
    low.lbl = "fetch"
    with pytest.raises(read.Unlowerable):
        low.value(Load("ram", V("q", 2), 1, PAT, PAT + 31, 13))


# ---- read.py: expansion, the image and the flow facts ---------------------------
def test_a_word_the_play_never_writes_is_the_byte_the_image_states():
    low, _voc = reader()
    assert low.frozen(PAT, 32) and low.frozen(FREQ, 2)
    assert not low.frozen(TIMER, 1) and not low.frozen(SWEEP, 3)
    low.lbl = "mach"
    assert low.expand(Load("ram", C(FREQ + 2, 2), 1, FREQ, FREQ, 1)) == C(0x07)
    assert low.expand(Bin("+", C(2), C(3))) == C(5)


def test_a_read_of_a_cell_one_store_reaches_is_the_value_that_store_left():
    low, _voc = reader()
    low.lbl = "mach"
    assert low.expand(ram(NOTE, 4, V("x"))) == V("n")
    assert low.isvoice(V("x")) and not low.isvoice(V("n"))
    assert low.chase(V("q")).op == "|"


def test_a_store_whose_value_reads_its_own_cell_is_a_counter_and_no_copy():
    low, _voc = reader()
    p = tick()
    clock = [s for s in p.blocks["head"].stmts if type(s) is Store][0]
    keyed = [s for s in p.blocks["keyon"].stmts if type(s) is Store][0]
    assert low.selfread(clock.v, TIMER)
    assert not low.selfread(keyed.v, NOTE)


def test_one_store_of_a_base_reaches_the_blocks_the_edges_lead_to():
    p = tick()
    got = read.reaching(p, list(p.blocks), frozenset({"x"}))
    assert NOTE in got["mach"] and TIMER in got["fetch"]
    assert not got["top"]


def test_two_paths_that_differ_in_one_term_and_its_negation_are_the_one_path():
    c, d = Bin("==", V("a"), C(0)), Bin("==", V("b"), C(0))
    arm = lambda t, e=(): ((("h", c, t),) + e, ())
    assert read.fold([arm(True), arm(False)]) == [((), ())]
    assert len(read.fold([arm(True), ((("h", d, True),), ())])) == 2
    assert read.pair((("h", c, True),), (("h", c, False),))
    assert not read.pair((("h", c, True),), (("h", d, False),))


def test_the_guard_the_schedule_states_is_read_over_the_cells_and_not_the_temps():
    low, _voc = reader()
    d, c, t, _w = low.guards["fetch"][0]
    assert low.guard(c, t) == [
        {"and": [{"and": [{"sub": [{"cell": "timer"}, 1]}, 0xFF]}, 0x80]},
        "!=",
        0,
    ]
    assert low.guard_value(ram(TIMER, 6, V("x"))) == {"cell": "timer"}
    assert low.onpath(d, c, t)
    low.stated, low.scope = frozenset({id(c)}), {"fetch"}
    assert not low.onpath(d, c, t)
    low.scope = {"head", "fetch"}
    assert low.onpath(d, c, t)
    low.gate = frozenset({(id(c), t)})
    assert not low.onpath(d, c, t)


def test_a_guard_term_is_a_comparison_of_the_objects_own():
    low, _voc = reader()
    low.lbl = "mach"
    assert low.term(Bin("==", V("n"), C(0)), True)[1] == "=="
    assert low.term(Bin("==", V("n"), C(0)), False)[1] == "!="
    assert low.term(Bin("<=", V("n"), C(0)), True) == [0, ">=", {"cell": "tn"}]
    assert low.term(Bin("<=", V("n"), C(0)), False)[1] == "<"
    assert low.term(V("n"), True) == [{"cell": "tn"}, "!=", 0]


# ---- read.py: the join plan and the jump table ----------------------------------
def test_a_diamond_folds_to_the_one_path_the_terms_it_states_do_not_decide():
    low, _voc = reader()
    eff, rows = low.plan(set(low.proc.blocks))
    assert eff["join"] == ((), ()) and not rows  # every path folds: no cell
    assert [t for _d, _c, t in eff["keyon"][0]] == [True, False]
    other_low = other(diamond())
    eff, rows = other_low.plan(set(other_low.proc.blocks))
    assert eff["e"] == ((), ()) and not rows
    assert len(eff["d"][0]) == 2


def test_a_joins_own_preds_do_not_stop_at_a_segments_edge():
    low = other(diamond())
    assert low.planall([["a", "b", "c", "e"], ["d", "g", "f"]]) == ["je"]
    assert low.eff["e"][1] == (({"cell": "je"}, "!=", 0),) and low.eff["e"][0] == ()
    assert {q for q, v in low.flagrows.items() for n, _c in v if n == "je"} == {"b", "c", "g"}
    assert low.planned == frozenset(low.eff)
    assert low.planall([["a", "b"], ["c", "d", "e", "f", "g"]]) == []


def test_a_block_one_case_of_a_jump_table_alone_reaches_stands_under_that_term():
    low = other(dispatch())
    term = low.proc.blocks["a"].term
    assert read._cases(term) == [("b", 0), ("c", 1)]
    assert read.edge(term, "b") == (Bin("==", V("y"), C(0, 2), 1),)
    assert read.edge(term, "d") == ()
    assert low._edge("a", "c") == (("a", Bin("==", V("y"), C(1, 2), 1), True),)
    assert low._edge("a", "e") == ()
    assert [d for d, _c, _t, _w in low.guards["b"]] == ["a"]
    assert low.guards["d"] == ()  # two cases reach it: no case is its term
    assert low._own("c") == (("a", Bin("==", V("y"), C(1, 2), 1), True),)


def test_the_reader_names_every_address_the_play_writes_and_refuses_nothing_yet():
    low, _voc = reader()
    assert (TIMER, TIMER + 3) in low.written
    assert low.refusals() == []
    low.bad.add("mach: $D400")
    assert low.refusals() == ["mach: $D400"]
