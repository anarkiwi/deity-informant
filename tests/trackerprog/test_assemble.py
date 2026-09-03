"""B6/B7: the lift's own mechanisms, hermetically -- schedule, lowering, score."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import (
    assemble,
    build,
    flow,
    lower,
    record,
    report,
    schedule,
)  # noqa: E402
from deity_informant.trackerprog.cells import Cells, ident  # noqa: E402
from deity_informant.trackerprog.universal import Player  # noqa: E402
from deity_informant.trackerprog.vocab import Vocab, _plus  # noqa: E402
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
    Var,
)
from deity_informant.tuneprog.recover import Names  # noqa: E402

TUNE, PITCH, INS, VOICE, GLOB = 0x1000, 0x2000, 0x2200, 0x2300, 0x2310
NOTES = 8


def C(v, w=1):
    return Const(v, w)


def V(n, w=1):
    return Var(n, w)


def ram(addr, r, idx=None, w=1):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Load("ram", a, w, addr, addr + 2, r)


def store(addr, r, val, idx=None, src=0, cls="ram"):
    a = C(addr, 2) if idx is None else Bin("+", C(addr, 2), idx, 2)
    return Store(cls, a, val, 1, addr, addr + 2, r, src)


class View:
    """The presentation view a lift reads: its regions, its procs and its image."""

    def __init__(self, storage, procs, img):
        self.storage = storage
        self.procs = procs
        self.img = img

    def by_id(self):
        return {r.id: r for r in self.storage}

    def reads(self):
        return self.img


def regions():
    return [
        Rgn(1, "FREQ", PITCH, 2 * NOTES, "state"),
        Rgn(2, "wave", INS, 8, "const", stride=2),
        Rgn(3, "adsr", INS + 1, 8, "const", stride=2),
        Rgn(4, "note", VOICE, 3, "state"),
        Rgn(5, "ins", VOICE + 3, 3, "state"),
        Rgn(6, "timer", VOICE + 6, 3, "state"),
        Rgn(7, "scratch", GLOB, 1, "state"),
        Rgn(8, "r8", 0xD400, 15, "io", stride=7),
        Rgn(9, "r9", 0xD404, 15, "io", stride=7),
    ]


def names():
    return Names(
        region={r.id: r.name for r in regions()},
        role={1: "freq_table"},
        groups={"voice": {"stride": 1, "n": 3, "members": [4, 5, 6]}},
        view={4: ("voice", "note"), 5: ("voice", "ins"), 6: ("voice", "timer")},
    )


def image():
    m = bytearray(0x10000)
    for i in range(NOTES):
        m[PITCH + 2 * i] = (0x100 + 7 * i) & 0xFF
        m[PITCH + 2 * i + 1] = (0x100 + 7 * i) >> 8
    m[INS : INS + 8] = bytes([0x41, 0x0A, 0x21, 0x0B, 0x11, 0x0C, 0x81, 0x0D])
    return m


def proc():
    """One voice pass: a clock, a guarded write-out and a counted inner loop."""
    blocks = {
        "top": Block("top", [Let("x", C(2))], Goto("head"), src=0x1000),
        "head": Block(
            "head",
            [
                Let("t0", ram(VOICE + 6, 6, V("x"))),
                store(VOICE + 6, 6, Bin("-", V("t0"), C(1)), V("x"), src=0x1010),
            ],
            If(Bin("!=", Bin("&", Bin("-", V("t0"), C(1)), C(0x80)), C(0)), "fetch", "mach"),
            src=0x1010,
        ),
        "fetch": Block(
            "fetch",
            [
                Let("n", ram(0x3000, 10, V("y"))),
                store(VOICE, 4, V("n"), V("x"), src=0x1020),
                store(0xD400, 8, ram(PITCH, 1, Bin("<<", ram(VOICE, 4, V("x")), C(1))), src=0x1030),
            ],
            Goto("tail"),
            src=0x1020,
        ),
        "mach": Block(
            "mach",
            [Let("a", ram(INS, 2, Bin("<<", ram(VOICE + 3, 5, V("x")), C(1))))],
            If(Bin("==", V("a"), C(0)), "tail", "loop"),
            src=0x1040,
        ),
        "loop": Block(
            "loop",
            [
                Let("c", ram(GLOB, 7)),
                store(GLOB, 7, Bin("-", V("c"), C(1)), src=0x1050),
                store(0xD404, 9, V("a"), src=0x1054),
            ],
            If(Bin("==", Bin("&", Bin("-", V("c"), C(1)), C(0x80)), C(0)), "loop", "tail"),
            src=0x1050,
        ),
        "tail": Block(
            "tail", [Let("x2", Bin("-", V("x"), C(1)))], If(V("x2"), "back", "out"), src=0x1060
        ),
        "back": Block("back", [Let("x", V("x2"))], Goto("head"), src=0x1064),
        "out": Block("out", [], Return(vals=[]), src=0x1070),
    }
    return Proc("tick", blocks=blocks, entry="top")


class Prog:
    def __init__(self):
        self.procs = {"tick": proc()}
        self.meta = {"tick_proc": "tick"}
        self.storage = regions()
        self.img = image()

    def by_id(self):
        return {r.id: r for r in self.storage}

    def reads(self):
        return self.img


def vocab(low=None):
    cells = Cells(View(regions(), {"tick": proc()}, image()), names(), pitch=(1, PITCH, NOTES))
    voc = Vocab(cells, image(), build.registers(), frozenset({"x"}))
    voc.supplied = {"n"}
    voc.pitch, voc.notebase, voc.insbase = (1, PITCH, NOTES), VOICE, VOICE + 3
    voc.inscol, voc.insstride = {2: "wave", 3: "adsr"}, 2
    del low
    return voc


def lowered():
    voc = vocab()
    return lower.Lower(Prog(), "tick", voc.cells, voc), voc


# ---- the vocabulary ----------------------------------------------------------------
def test_a_name_the_object_carries_has_nothing_else_in_it():
    assert ident("rec2[].b5591#1") == "rec2__.b5591_1"


def test_cells_answers_voice_global_and_pitch_by_address():
    c = Cells(View(regions(), {}, image()), names(), pitch=(1, PITCH, NOTES))
    assert c.at(PITCH + 4) == ("pitch", (2, 0))
    assert c.at(VOICE + 1) == ("voice", ("note", 1))
    assert c.at(GLOB) == ("global", "scratch")
    assert c.at(0x9999) is None
    assert c.voicecell(VOICE) == "note" and c.voicecell(0x2400) == "c2400"
    assert c.name(GLOB, True) == "#scratch" and c.name(VOICE, False) is None
    cellseed, glob = c.seed(image())
    assert glob["scratch"] == 0 and cellseed["c2400"] == [0, 0, 0]


def test_a_record_split_one_copy_a_voice_names_each_field_a_voice_cell():
    n = names()
    n.groups["voice_2"] = {
        "stride": 1,
        "n": 3,
        "members": [],
        "split": 11,
        "fields": {"0": "f00", "3": "freq_lo"},
    }
    c = Cells(View(regions() + [Rgn(11, "voice_2", 0x2400, 6, "state")], {}, image()), n)
    assert c.at(0x2400) == ("voice", ("f00", 0)) and c.at(0x2402) == ("voice", ("f00", 2))
    # a name section 5 answers itself is not a tune's, and is qualified by its group
    assert c.at(0x2403) == ("voice", ("voice_2_freq_lo", 0))
    assert c.baseof("voice_2_freq_lo") == 0x2403


def test_a_scalar_the_tick_keeps_is_one_cell_every_voice_enters_with():
    m = image()
    m[GLOB] = 5
    c = Cells(View(regions(), {}, m), names())
    assert c.scalarcell(GLOB) == "scratch"
    assert c.seed(m)[0]["scratch"] == [5, 5, 5]
    assert c.scalarcell(GLOB, "phase") == "c%04X" % GLOB


def test_a_word_past_the_tuning_is_the_cell_that_holds_it():
    c = Cells(View(regions(), {}, image()), names(), pitch=(1, PITCH, NOTES))
    c.voicecell(PITCH + 2 * NOTES)
    words = build.beyond_words(c, PITCH, NOTES, 3)
    assert words[0]["u16"][0]["cell"][1] == 0
    assert "trap" in words[2]


def test_registers_are_the_chip_s_own_columns_by_their_offset():
    got = build.registers()
    assert got[0] == "freq_lo" and got[4] == "ctrl" and got[24] == "mode_vol"


def test_a_sum_with_one_constant_splits_into_term_and_offset():
    assert _plus(Bin("+", V("a"), C(3))) == (V("a"), 3)
    assert _plus(Bin("+", C(3), V("a"))) == (V("a"), 3)
    assert _plus(V("a")) == (V("a"), 0)


# ---- expressions and guards ---------------------------------------------------------
def test_a_left_shift_is_the_adds_the_object_has():
    assert lower._shl(4, 2, 1) == 16
    assert lower.masked({"cell": "a"}, 2) == {"and": [{"cell": "a"}, 0xFFFF]}


def test_a_comparison_in_a_value_position_is_the_chip_s_own_zero_test():
    low, _voc = lowered()
    low.lbl = "mach"
    got = low.value(Bin("!=", Bin("&", V("a"), C(4)), C(0)))
    assert got == {"bit": [{"cell": "ta"}, 2]}
    got = low.value(Bin("==", V("a"), C(3)))
    assert "borrow_out" in got
    assert low.value(Bin("<", V("a"), C(3)))["carry_out"][1] == 8
    assert low.value(Bin("<=", V("a"), C(3)))["borrow_out"][1] == 8
    assert low.value(Bin("carry", V("a"), C(3)))["carry_out"][1] == 8


def test_a_guard_term_is_a_comparison_of_the_object_s_own():
    low, _voc = lowered()
    low.lbl = "mach"
    assert low.term(Bin("==", V("a"), C(0)), True)[1] == "=="
    assert low.term(Bin("==", V("a"), C(0)), False)[1] == "!="
    assert low.term(Bin("<=", V("a"), C(0)), True)[1] == ">="
    assert low.term(V("a"), True)[1] == "!="


def test_a_leaf_the_vocabulary_has_no_name_for_is_refused():
    low, _voc = lowered()
    low.lbl = "fetch"
    with pytest.raises(lower.Unlowerable):
        low.value(ram(0x3000, 10, V("y")))


# ---- reaching definitions and the SSA ------------------------------------------------
def test_one_store_of_a_base_reaches_a_later_read():
    p = proc()
    got = flow.reaching(p, list(p.blocks), frozenset({"x"}))
    assert got["mach"][GLOB + 0] if GLOB in got["mach"] else True
    assert VOICE in got["tail"]


def test_a_read_of_the_note_cell_expands_to_the_store_that_filled_it():
    low, _voc = lowered()
    low.lbl = "mach"
    assert low.expand(ram(VOICE, 4, V("x"))) == V("n")
    assert low.isvoice(V("x")) and not low.isvoice(V("n"))


# ---- the tuning and the instrument ---------------------------------------------------
def test_the_pitch_read_is_the_voice_s_note_moved_by_a_constant():
    low, voc = lowered()
    low.lbl = "mach"
    idx = Bin("<<", ram(VOICE, 4, V("x")), C(1))
    got = voc.tuning(low, PITCH, idx)
    assert got == {"and": [{"transpose": 0}, 0xFF]}
    got = voc.tuning(low, PITCH + 1, idx)
    assert got == {"and": [{"shr": [{"transpose": 0}, 8]}, 0xFF]}
    assert voc.tuning(low, 0x9000, idx) is None


def test_the_instrument_column_is_the_record_the_voice_plays():
    low, voc = lowered()
    low.lbl = "mach"
    idx = Bin("<<", ram(VOICE + 3, 5, V("x")), C(1))
    assert voc.isins(low, idx)
    assert voc.load(low, ram(INS, 2, idx)) == {"ins": "wave"}
    assert not voc.isins(low, ram(VOICE, 4, V("x")))


def test_a_const_table_read_at_a_cell_is_a_stream_of_its_own_bytes():
    low, voc = lowered()
    low.lbl = "mach"
    got = voc.load(low, ram(INS, 2, V("c")))
    assert got == {"tabcell": ["T%04X" % INS, {"cell": "tc"}, "b"]}
    st = build.table_streams(voc, image())
    assert [r["b"] for r in st["T%04X" % INS]["rows"]] == list(image()[INS : INS + 3])
    low.lbl, voc.rowblocks = "fetch", frozenset({"fetch"})
    with pytest.raises(lower.Unlowerable):  # the bytes a fetch read are the score's own
        voc.load(low, ram(INS, 2, V("c")))


def test_the_pinned_reads_are_data_where_their_kind_is_never_external():
    prog, m = Prog(), image()
    m[0xFB] = 7
    prog.inputs = [
        [0x1000, 0xFB, "uninit_ram", 1, 2],
        [0x1004, 0xD012, "raster", 9, 2],
        [0x1008, 0x10000, "entry_reg", 1, 1],
    ]
    got, bad = build.pinned_inputs(prog, m)
    assert got == {0xFB: 7} and bad == [("$D012", "$1004", "raster")]


def test_the_instrument_records_are_named_by_what_the_cell_selecting_them_holds():
    art = {
        "t2": {
            "selectors": [
                {
                    "kind": "selector",
                    "cursor": "b1014@$101D",
                    "entries": 2,
                    "visited": [0, 8],
                    "columns": [{"table": "wave", "stride": 8}],
                }
            ],
            "streams": [],
        }
    }
    got = build.instrument_table(art, View(regions(), {}, image()), names())
    assert got[0] == 0x101D and got[3] == 2 and got[4] == [0, 8]


def test_a_store_names_a_register_a_cell_or_an_accumulator():
    low, voc = lowered()
    low.lbl = "fetch"
    assert voc.target(low, store(0xD400, 8, C(1), src=0x1030, cls="io")) == ("reg", "freq_lo")
    assert voc.target(low, store(VOICE, 4, C(1), V("x"))) == ("cell", "@note")
    assert voc.target(low, store(GLOB, 7, C(1))) == ("cell", "#scratch")
    assert voc.target(low, store(0, 0, C(1), cls="chk")) is None
    voc.inspw = {2: "lo"}
    idx = Bin("<<", ram(VOICE + 3, 5, V("x")), C(1))
    assert voc.target(low, store(INS, 2, C(1), idx)) == ("acc", "ins.pw.lo")


# ---- rows, loops and the linear order -------------------------------------------------
def test_the_lowering_makes_one_row_per_block_under_its_guard_path():
    low, _voc = lowered()
    low.scope = set(low.proc.blocks)
    when, parts = low.row("loop")
    assert when and parts[0][0]
    assert any(t == "#scratch" for t, _v, _s in parts[0][0])


def test_the_guard_a_phase_runs_under_is_the_schedule_s_and_not_the_row_s():
    low, _voc = lowered()
    got = low.guards["loop"]
    assert low.when("loop")
    low.gate = frozenset((id(c), t) for _d, c, t, _w in got)
    assert low.when("loop") == []


def test_a_divider_s_own_compare_is_the_rate_and_not_a_row_s_guard():
    low, _voc = lowered()
    d, c, t, _w = low.guards["loop"][0]
    low.stated = frozenset({id(c)})
    low.scope = {d, "loop"}
    assert low.onpath(d, c, t)
    low.scope = {"loop"}
    assert not low.onpath(d, c, t)


def test_an_inner_loop_is_unrolled_to_its_own_bound_and_traps_past_it():
    low, _voc = lowered()
    low.scope = set(low.proc.blocks)
    seq = low.sequence({"mach", "loop", "tail"}, {"loop": 3})
    assert [l for l, _e, _x, _g, _j in seq].count("loop") == 3
    assert [j for l, _e, _x, _g, j in seq if l == "loop"] == [0, 1, 2]
    assert any(l is None and e for l, e, _x, _g, _j in seq)
    items = build.stream_items(low, seq, {})
    rows = [r for k, v in items if k == "rows" for r in v]
    assert any("trap" in json.dumps(r["sets"]) for r in rows)


def test_a_name_an_unrolled_loop_binds_takes_one_cell_a_turn():
    """Section 2.4: the score supplies one constant a *turn*, not one a name."""
    low, voc = lowered()
    voc.supplied = {"c"}
    low.scope = set(low.proc.blocks)
    assert low.turnsof({"mach", "loop", "tail"}) == {"c": ("loop", True)}
    assert not low.turnsof({"loop"})  # a loop that is the whole segment is no inner one
    rows = [
        r
        for k, v in build.stream_items(low, low.sequence({"mach", "loop", "tail"}, {"loop": 3}), {})
        if k == "rows"
        for r in v
    ]
    got = [s[1]["cell"] for r in rows for s in r["sets"] if s[0] == "@tc"]
    assert got == ["tc__0", "tc__1", "tc__2"] and low.turns["c"] == got
    assert low.turncell("c") is None  # outside a turn the score writes the cell itself


def test_the_score_supplies_a_turn_s_own_byte_and_a_name_s_one_value():
    class Low:  # pylint: disable=too-few-public-methods
        temps, turns = {"a": "ta", "b": "tb"}, {"b": ["tb__0", "tb__1"]}

    got = {"temps": {"a": 7, "b": 9}, "turns": {("b", 0): 4, ("b", 1): 5, ("b", 2): 6}}
    assert record.bytes_of(Low(), "a", got) == [("ta", 7)]
    assert record.bytes_of(Low(), "b", got) == [("tb__0", 4), ("tb__1", 5)]
    assert record.bytes_of(Low(), "z", got) == []


def test_a_join_a_second_segment_reaches_is_raised_where_that_path_stands():
    """A join's own preds do not stop at a segment's edge (section 2.2)."""
    low, _voc = lowered()
    flags = low.planall([["fetch"], ["mach", "loop", "tail"]])
    assert flags == ["jtail"]
    assert ("jtail", ()) not in low.flagrows.get("fetch", ())
    assert [n for n, _c in low.flagrows["fetch"]] == ["jtail"]
    assert {q for q, v in low.flagrows.items() for n, _c in v if n == "jtail"} == {
        "fetch",
        "mach",
        "loop",
    }
    seq = low.sequence({"fetch"}, {}, flags)
    assert seq[0][0] == lower.RESET and seq[0][1] == ("jtail",)
    assert low.sequence({"out"}, {})[0][0] != lower.RESET  # a segment the plan does not hold


def test_the_dead_sets_are_dropped_and_the_live_ones_kept():
    st = [{"rows": [{"when": [], "sets": [["@dead", 1], ["@live", 2], ["freq_lo", 3]]}]}]
    build.dce(st, {"live"})
    assert [s[0] for s in st[0]["rows"][0]["sets"]] == ["@live", "freq_lo"]
    assert build._cellnames([{"cell": "a"}, {"cell": ["b", 1]}]) == {"a", "b"}


def test_a_join_several_paths_reach_is_one_cell_and_not_one_guard():
    low, _voc = lowered()
    eff, rows = low.plan({"head", "fetch", "mach", "loop", "tail"})
    assert eff["fetch"][1] == () and eff["fetch"][0]  # one path: its own guard path
    assert eff["tail"][0] == () and eff["tail"][1]  # a join: one term, over a cell
    name = eff["tail"][1][0][0]["cell"]
    assert set(rows) == {"fetch", "mach", "loop"}
    assert {n for v in rows.values() for n, _c in v} == {name}


def test_two_paths_that_differ_in_one_term_and_its_negation_are_the_one_path():
    c, d = Bin("==", V("a"), C(0)), Bin("==", V("b"), C(0))
    arm = lambda t, e=(): (((("head", c, t),) + e), ())
    assert flow.fold([arm(True), arm(False)]) == [((), ())]
    assert len(flow.fold([arm(True), (((("head", d, True),)), ())])) == 2


def test_the_flag_rows_raise_the_join_s_cell_where_each_path_already_stands():
    low, _voc = lowered()
    seq = low.sequence({"head", "fetch", "mach", "loop", "tail"}, {"loop": 1})
    assert seq[0][0] == lower.RESET
    rows = [r for k, v in build.stream_items(low, seq, {}) if k == "rows" for r in v]
    sets = [s for r in rows for s in r["sets"]]
    assert [s[1] for s in sets if s[0].startswith("@j")].count(0) == 1
    assert [s[1] for s in sets if s[0].startswith("@j")].count(1) == 3


# ---- the schedule (B6) -----------------------------------------------------------------
def test_the_voice_loop_is_the_one_the_fetch_blocks_are_in():
    p = proc()
    head, (body, latches) = schedule.voice_loop(Prog(), "tick", frozenset({"fetch"}))
    assert head == "head" and "fetch" in body and latches
    assert schedule.copies(p, schedule.induction(p, head, latches)) >= {"x"}


def test_the_segments_split_at_the_fetch_and_name_the_commits():
    order = ["a", "b", "fetch", "c", "d"]
    segs = schedule.segments(order, {"fetch"}, set())
    assert [n for n, _b in segs] == ["prelude", "row", "machine"]
    assert schedule.segments(["a"], set(), set()) == [("machine", ["a"])]


def test_the_row_clock_is_the_counter_a_guard_of_the_fetch_s_path_steps():
    p = proc()
    base, lbl, pre, store_, step, keep, div, spent = schedule.clock_of(p, frozenset({"x"}), "fetch")
    assert base == VOICE + 6 and lbl == "head" and step == -1
    assert pre.n == "t0" and store_.src == 0x1010
    assert len(keep) == 1 and keep[0][1] is True and not div and not spent


def test_a_counter_a_guard_compares_with_a_cell_is_the_divider_and_not_the_clock():
    p = proc()
    p.blocks["head"].term = If(Bin("!=", ram(GLOB, 7), ram(INS + 1, 3)), "fetch", "mach")
    p.blocks["fetch"].stmts.insert(0, Let("g", ram(GLOB, 7)))
    p.blocks["fetch"].stmts.insert(1, store(GLOB, 7, Bin("-", V("g"), C(1)), src=0x1024))
    got = schedule.clock_of(p, frozenset({"x"}), "fetch")
    assert got is None or GLOB in got[6]


def test_a_store_outside_the_voice_loop_is_the_clock_s_own_reset_clause():
    p = proc()
    p.blocks["top"].stmts.append(store(VOICE + 6, 6, ram(GLOB, 7), V("x"), src=0x1004))
    got = schedule.resets_of(
        p, VOICE + 6, None, {"head", "fetch", "mach", "loop", "tail"}, {"top": ()}
    )
    assert [s.src for s, _g in got] == [0x1004]


def test_the_divider_is_the_tick_level_counter_a_reload_gates():
    low, _voc = lowered()
    st = store(GLOB, 7, ram(INS + 1, 3), src=0x1080)
    assert build.divider_rate(st, low, image()) == image()[INS + 1] + 1
    assert build.divider_rate(store(GLOB, 7, C(2)), low, image()) == 3
    m = bytearray(0x10000)
    m[GLOB] = 0
    assert build.divider_phase(m, GLOB, 2, 3) == 0


def test_a_schedule_states_every_datum_b6_compares():
    sch = schedule.Schedule("tick", tick=["row"], voice_order=(2, 1, 0))
    assert set(sch.datums()) == {
        "voice_order",
        "commit_order",
        "tick",
        "row_consumes_tick",
        "tempo.rate",
        "tempo.phase",
        "tempo.step",
        "tempo.resets",
        "tempo.boundary_terms",
        "segments",
    }


def test_the_commit_order_is_the_order_the_write_sites_keep():
    t0 = {
        "writes": [
            {"register": "ctrl", "site": {"pc": "$1000"}},
            {"register": "ad", "site": {"pc": "$1004"}},
            {"register": "sr", "site": {"pc": "$1008"}},
        ]
    }
    assert schedule.commit_order(t0) == ("ctrl", "ad", "sr")


# ---- the score ---------------------------------------------------------------------------
def test_the_score_is_grouped_where_the_fetch_steps_the_order_cursor():
    low, _voc = lowered()
    low.temps = {"n": "tn"}
    rows = []
    for tick, note, ends in ((0, 12, False), (4, 14, True), (8, 12, False), (12, 14, True)):
        rows.append(
            {
                "env": {"x": 0},
                "seen": ["n"],
                "temps": {"n": note},
                "cmds": [["ram", VOICE + 6, 3, 1, 0]]
                + ([["ram", 0x2500, 1, 1, 0]] if ends else []),
                "tick": tick,
            }
        )
    orders, pats = record.score_of(rows, low, "x", set(), VOICE + 6, 1, 0x2500)
    assert len(pats) == 1 and orders[0]["play"] == [0, 0]
    got = record.patterns_of(pats)
    ev = got["0"]["events"][0]
    assert ev["dur"] == 3 and ev["arm"]["rows"][0]["sets"] == [["@tn", 12]]
    assert ev["sounds"] is False and ev["note"] is None


def test_an_instrument_scoped_store_becomes_a_reload_accumulator():
    name, rec = build.acc_of("acc0", "ins.pw.lo", {"const": 1}, [], 3, 0x1234)
    assert name == "acc0" and rec["policy"] == {"reload": {"const": 1}}
    assert rec["bound"]["interval"] == [0, 255] and rec["site"] == "$1234"


def test_the_coverage_counts_the_leaves_and_where_t1_s_accumulators_landed():
    low, _voc = lowered()
    streams = [{"rows": [{"when": [], "sets": [["freq_lo", {"cell": "a"}]]}]}]
    accs = {"acc0": {"site": "$1050", "policy": {"reload": 1}, "when": []}}
    t1got = [{"id": "a0", "cell": "c", "sites": ["$1050"], "form": "acc", "why": None}]
    cov = report.coverage(low, Prog(), "tick", {"machine": ["loop"]}, [], streams, accs, t1got)
    assert cov["t1_recognised"] == 1 and cov["leaves"]["cell"] == 1
    assert cov["store_sites"] == 2 and cov["t1_refused"] == []
    t1got[0].update(form="sets", why="no lowered row stores it")
    other = report.coverage(low, Prog(), "tick", {"machine": ["loop"]}, [], streams, accs, t1got)
    assert other["t1_recognised"] == 0
    assert other["t1_refused"] == [["a0", "c", "no lowered row stores it"]]


# ---- the whole lift ----------------------------------------------------------------------
def test_a_lift_that_reaches_no_fetch_region_refuses_and_emits_nothing():
    art = {"prog": Prog(), "view": View(regions(), {}, image()), "names": names(), "t0": {}}
    art["t2"] = {"score": [], "streams": [], "selectors": [], "horizon": {"ticks": 4}}
    art["t1"], art["cert"] = {}, {}
    with pytest.raises(assemble.Refused) as x:
        assemble.lift(art)
    assert x.value.refusals[0].why == "score not cursor-shaped"


def test_a_hint_is_written_where_the_schema_puts_it():
    obj = {"meta": {"commit_order": ["ad", "sr", "ctrl"]}}
    assemble._apply(obj, "meta.commit_order", ["ctrl", "ad", "sr"])
    assert obj["meta"]["commit_order"] == ["ctrl", "ad", "sr"]


def test_the_tick_puts_the_segment_before_the_row_at_its_own_position():
    got = assemble._tick(["prelude", "commit", "row"], [("stream", "p0"), ("acc", "a0")])
    assert got == [{"stream": "p0"}, "commit", "row"]


def test_the_recorder_names_each_inner_loop_s_own_test_and_its_reset():
    got = record.headers(Prog(), "tick", {"loop", "mach"})
    assert [k for _h, _n, k in got] == ["track"] + ["reset"] * (len(got) - 1)
    assert got[0][0] == "loop"


class TurnProg:
    """A tick whose inner loop binds one name a turn, run from its own image."""

    def __init__(self, turns):
        head = Block(
            "head",
            [Let("c", ram(GLOB, 7)), store(GLOB, 7, Bin("-", V("c"), C(1)), src=0x1010)],
            If(Bin("!=", V("c"), C(0)), "head", "out"),
            src=0x1010,
        )
        blocks = {
            "top": Block("top", [], Goto("head"), src=0x1000),
            "head": head,
            "out": Block("out", [], Return(vals=[]), src=0x1020),
        }
        self.procs = {
            "tick": Proc("tick", blocks=blocks, entry="top"),
            "init": Proc("init", blocks={"i": Block("i", [], Return(vals=[]))}, entry="i"),
        }
        self.storage = regions()
        self.img = image()
        self.img[GLOB] = turns - 1
        self.meta = {
            "tick_proc": "tick",
            "init_proc": "init",
            "entry": {"kind": "sub"},
            "load": (0x1000, 0x1100),
        }

    def image(self):
        return self.img


def test_the_recorder_keeps_the_value_each_turn_of_a_loop_bound_a_name():
    """Section 2.4: a name an unrolled loop binds is one constant a turn."""
    prog = TurnProg(3)
    defs = {s.n: s.e for b in prog.procs["tick"].blocks.values() for s in b.stmts if type(s) is Let}
    R, fetches, trap, _obs = record.run(
        prog,
        "tick",
        [("top", ["top", "head"], ["out"])],
        1,
        loops=record.headers(prog, "tick", {"head", "out"}),
        marks=[("c", defs["c"], ("head", True))],
    )
    assert trap is None and R.trips == {"head": 3}
    got = fetches[("tick", "top")][0]
    assert got["turns"] == {("c", 0): 2, ("c", 1): 1, ("c", 2): 0} and got["seen"] == ["c"]


def test_a_recorded_region_exports_every_temp_it_binds():
    F = record.fetch_of(Prog(), "tick", [("fetch", ["fetch"], ["tail"])])
    r = F.regions[("tick", "fetch")]
    assert r.liveout == ("n",) and r.exits == frozenset({"tail"})


def test_the_lowered_rows_render_on_the_universal_player():
    """A stream of lowered ``sets`` is what section 3.3 already admits (D3)."""
    obj = {
        "meta": {
            "voices": 1,
            "voice_order": [0],
            "commit_order": ["ctrl", "ad", "sr"],
            "tempo": {
                "cell": "timer",
                "step": -1,
                "rate": 1,
                "phase": 0,
                "boundary": [[{"cell": "timer"}, ">=", 0x80]],
            },
            "tick": [{"stream": "m0"}, "commit"],
            "row_consumes_tick": True,
            "row": [],
            "instrument": {},
        },
        "pitch": {"base": 0, "freq": [0x1234]},
        "streams": {
            "m0": {
                "all": True,
                "rows": [{"when": [], "sets": [["ctrl", {"and": [{"ins": "wave"}, 0xFE]}]]}],
            }
        },
        "accs": {},
        "instruments": {"0": {"wave": 0x41}},
        "score": {"patterns": {"0": {"events": []}}, "orders": [{"play": [0], "end": {"jump": 0}}]},
        "globals": {},
        "state0": {"cells": {"timer": [1]}},
    }
    assert Player(obj).tick() == [(4, 0x40)]
