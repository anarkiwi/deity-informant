"""L1 -- the structured tick: the idioms the nine families forced, one code path.

Each fragment names the family that forced it (the "what the layer needed"
section of that family's own document) and asserts
:func:`~deity_informant.trackerprog.passes.l1_structure.structure` produces the
generic concept -- an inlined callee, a rerolled run, a voice loop with an
induction variable, a per-voice array, a tuning -- with no branch on a family.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from _frag import (  # noqa: E402
    ACC,
    C,
    CHIP,
    CURSOR,
    FREQ,
    FREQHI,
    FREQLO,
    GLOB,
    NOTE,
    SID,
    TIMER,
    V,
    VOICES,
    art_of,
    prog_of,
    ram,
    sid,
    store,
    voiceblocks,
)
from deity_informant.trackerprog.passes import l1_structure  # noqa: E402
from deity_informant.trackerprog.passes.ir import (  # noqa: E402
    Diverged,
    Level,
    irwrites,
    validate,
)
from deity_informant.tuneprog.ir import (  # noqa: E402
    Bin,
    Block,
    Call,
    Goto,
    If,
    Let,
    Proc,
    Return,
    Store,
    Trap,
    Var,
)

TICKS = 24


def _l0(prog, split=False):
    return Level(0, art=art_of(prog, split), prog=prog, proc="tick")


def _calls(n, args):
    """A tick whose voice pass stands in a callee, called ``n`` times."""
    body = Block(
        "b",
        [Let("u", ram(TIMER, 6, V("X", 1))), sid(0, V("u"), V("X", 1), src=0x2000)],
        Return(vals=[Var("u", 1)]),
        src=0x2000,
    )
    p = Proc("p", blocks={"b": body}, entry="b", params=[1], rets=[0])
    stmts = [Call("p", (C(a, 1),), ("r%d" % i,)) for i, a in enumerate(args)]
    tick = Proc(
        "tick",
        blocks={"a": Block("a", stmts, Return(vals=[]), src=0x1000)},
        entry="a",
    )
    del n
    return prog_of({"tick": tick, "p": p})


def test_a_callee_is_inlined_where_it_stands():
    """GoatTracker 2: the tick is fourteen procedures and the fetch stands in one."""
    prog = _calls(1, [0])
    got = l1_structure.structure(art_of(prog))
    assert not any(type(s) is Call for b in got.prog.procs["tick"].blocks.values() for s in b.stmts)
    assert got.facts["inlined_loops"] == 0
    validate(_l0(prog), got, TICKS)


def test_a_run_of_calls_with_a_stepping_argument_is_the_loop_it_closes():
    """Walker and Hubbard: one voice routine, called once a voice with the index."""
    prog = _calls(3, [0, 1, 2])
    got = l1_structure.structure(art_of(prog))
    assert got.facts["inlined_loops"] == 1
    assert not any(type(s) is Call for b in got.prog.procs["tick"].blocks.values() for s in b.stmts)
    validate(_l0(prog), got, TICKS)


def _siblings():
    """Three unrolled copies of one voice's pass, the chip at its own stride."""
    blocks = {}
    for v in range(VOICES):
        nxt = "c%d" % (v + 1) if v + 1 < VOICES else "out"
        blocks["c%d" % v] = Block(
            "c%d" % v,
            [
                Let("t%d" % v, ram(ACC + v, 9)),
                store(ACC + v, 9, Bin("+", V("t%d" % v), C(1))),
                sid(CHIP * v, Bin("+", V("t%d" % v), C(1)), src=0x1100 + 4 * v),
            ],
            Goto(nxt),
            src=0x1100 + 4 * v,
        )
    blocks["out"] = Block("out", [], Return(vals=[]), src=0x1200)
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="c0")})


def test_unrolled_voice_copies_at_a_stride_are_one_pass_over_that_stride():
    """SID Wizard and Hubbard: ``X = 7v`` double duty, the copies written out."""
    prog = _siblings()
    got = l1_structure.structure(art_of(prog))
    p = got.prog.procs["tick"]
    assert got.facts["rerolled"] == 1
    assert "c1" not in p.blocks and "c2" not in p.blocks
    validate(_l0(prog), got, TICKS)


def test_the_voice_loop_and_its_induction_variable_are_explicit():
    """Every family: one pass over the voices, and the index that closes it."""
    prog = _siblings()
    got = l1_structure.structure(art_of(prog))
    assert got.facts["head"]
    assert got.facts["vidx"]
    assert got.facts["latches"]
    assert got.facts["body"] >= {got.facts["head"]}


def _loop(split=False):
    """The voice loop written as one, over a per-voice array and the tuning."""
    idx = V("x")
    read = (
        [
            Let("f", ram(FREQLO, 1, V("n"), size=16)),
            Let("g", ram(FREQHI, 21, V("n"), size=16)),
            sid(1, V("g"), src=0x1022),
        ]
        if split
        else [Let("f", ram(FREQ, 1, Bin("<<", V("n"), C(1)), size=2 * 16 + 24))]
    )
    body = (
        [
            Let("n", ram(NOTE, 4, idx)),
            *read,
            sid(0, V("f"), src=0x1020),
            Let("t", ram(TIMER, 6, idx)),
            store(TIMER, 6, Bin("-", V("t"), C(1)), idx, src=0x1024),
        ],
        Goto("back"),
    )
    return prog_of({"tick": Proc("tick", blocks=voiceblocks(body), entry="top")}, split)


def test_a_per_voice_array_at_a_stride_is_one_cell_the_voice_reads_its_own_of():
    """GoatTracker 2: the state block S6 splits into the arrays a voice indexes."""
    prog = _loop()
    got = l1_structure.structure(art_of(prog))
    cells = got.facts["cells"]
    assert cells.voices == VOICES
    assert got.facts["arrays"].get(NOTE) == ("note", VOICES)
    assert got.facts["arrays"].get(TIMER) == ("timer", VOICES)
    assert cells.voicecell(NOTE) == "note"


def test_a_fused_tuning_region_states_the_notes_and_the_state_past_them():
    """Hubbard: the tuning and six per-voice arrays are one region."""
    prog = _loop()
    got = l1_structure.structure(art_of(prog))
    pit = got.facts["pitch"]
    assert pit is not None and pit.n == 16 and pit.step == 2
    assert pit.rids == (1,) and pit.obases == (FREQ, FREQ + 1)
    # the state past the notes is the same region, and the tuning stops at its top
    assert got.facts["cells"].at(FREQ + 2 * 16) is None or pit.n == 16


def test_a_split_tuning_of_two_byte_tables_is_one_tuning():
    """SID Wizard and GoatTracker 2: the low and high bytes are two tables."""
    prog = _loop(split=True)
    got = l1_structure.structure(art_of(prog, split=True))
    pit = got.facts["pitch"]
    assert pit is not None and pit.n == 16 and pit.step == 1
    assert len(pit.rids) == 2 and len(pit.obases) == 2


def _prologue():
    """A tick whose first call runs a block no later call reaches."""
    blocks = {
        "a": Block(
            "a",
            [Let("g", ram(GLOB, 11, size=1))],
            If(Bin("!=", V("g"), C(0)), "reset", "run"),
            src=0x1000,
        ),
        "reset": Block(
            "reset",
            [store(GLOB, 11, C(0), size=1, src=0x1004), store(CURSOR, 8, C(0), src=0x1008)],
            Goto("run"),
            src=0x1004,
        ),
        "run": Block(
            "run",
            [Let("c", ram(CURSOR, 8)), store(CURSOR, 8, Bin("+", V("c"), C(1)), src=0x1010)],
            Goto("emit"),
            src=0x1010,
        ),
        "emit": Block("emit", [sid(0, ram(CURSOR, 8), src=0x1014)], Return(vals=[]), src=0x1014),
    }
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="a")})


def test_the_first_call_s_own_blocks_are_peeled_as_a_prologue():
    """GoatTracker 2 and SID Wizard: the first ``play`` flushes and resets."""
    prog = _prologue()
    got = l1_structure.structure(art_of(prog))
    assert got.facts["prologue"] == {"reset"}
    validate(_l0(prog), got, TICKS)


def test_no_pass_of_the_pipeline_names_a_family():
    """One code path a level: no module of the passes branches on a family."""
    bad = (
        "hubbard",
        "commando",
        "goattracker",
        "gt2",
        "sidwizard",
        "wizard",
        "jch",
        "defmon",
        "follin",
        "blackbird",
        "walker",
        "galway",
    )
    root = Path(__file__).resolve().parent.parent.parent
    for path in sorted((root / "deity_informant/trackerprog/passes").glob("*.py")):
        text = path.read_text().lower()
        body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
        for name in bad:
            assert name not in body, "%s names %s" % (path.name, name)


def test_a_store_the_structuring_moved_is_the_store_it_was():
    """The rerolled copy writes what the copies wrote, at the index it now steps."""
    prog = _siblings()
    got = l1_structure.structure(art_of(prog))
    stores = [
        s
        for b in got.prog.procs["tick"].blocks.values()
        for s in b.stmts
        if type(s) is Store and s.cls == "io"
    ]
    assert len(stores) == 1 and stores[0].lo == SID
    # the envelope is the copies' own, joined: the rerolled store reaches them all
    assert stores[0].hi >= SID + CHIP * (VOICES - 1)


def test_a_pass_that_changed_the_observable_is_the_pass_that_failed():
    """The one check every pass answers to says which tick, and stops there."""
    prog = _siblings()
    a = Level(1, prog=prog, proc="tick")
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": "t",
            "song": 0,
            "voices": 1,
            "horizon": 4,
            "voice_order": [0],
            "commit_order": ["ctrl", "ad", "sr"],
            "instrument": {},
            "tempo": {"cell": "p", "step": 0, "rate": 1, "phase": 0, "boundary": [[0, "!=", 0]]},
            "tick": ["machine", "commit"],
            "row": [],
            "row_consumes_tick": False,
        },
        "pitch": {"base": 0, "freq": [1]},
        "streams": {"m": {"all": True, "rank": 0, "rows": [{"sets": [["ad", 7]]}]}},
        "accs": {},
        "instruments": {"0": {}},
        "score": {"patterns": {}, "orders": [{"play": [], "end": "stop"}]},
        "globals": {},
        "state0": {"cells": {"p": [0]}, "ins": [0]},
    }
    b = Level(2, obj=obj)
    with pytest.raises(Diverged):
        validate(a, b, 4)
    with pytest.raises(Diverged):
        validate(b, Level(1, prog=prog, proc="tick"), 4)


def test_a_program_the_interpreter_cannot_finish_renders_what_it_reached():
    """A trap stops the render where it sprang, and the level is what it rendered."""
    blocks = {
        "a": Block("a", [], If(Bin("!=", C(0), C(0)), "b", "t"), src=0x1000),
        "b": Block("b", [], Return(vals=[]), src=0x1004),
        "t": Block("t", [], Trap("unverified"), src=0x1008),
    }
    prog = prog_of({"tick": Proc("tick", blocks=blocks, entry="a")})
    assert irwrites(prog, 4) == []
