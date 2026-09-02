"""Hermetic snippet tests for the forms a shadowed, stream-driven tune needs.

No tune and no HVSC: one hand-written trackerprog per form -- the flush, the
countdown clock and its funk alternation, a stream's hold, jump, op and trap, a
global channel, the fetch that runs ahead of its row, and three policies.
"""

import pytest

from deity_informant.trackerprog.universal import Player, render

FLO, FHI, PLO, PHI, CTRL, AD, SR = 0, 1, 2, 3, 4, 5, 6
CUT, RES, VOL = 22, 23, 24
GATED = {"and": [{"cell": "wave"}, {"cell": "gate"}]}
CELLS = ("rowclock", "tempo", "instr", "gate", "wave", "param", "vibtime", "vibdelay", "staged")
# a table's rows are its own, from the first: a cursor that is on no row says so
WAVE_ROWS = [
    {"sets": [["@wave", 0x21], ["!produced", 1]], "hold": 2, "op": {"pitch": 2}},
    {"sets": [["@wave", 0x11], ["!produced", 1]], "op": {"pitch": 1, "relative": True}},
    {"jump": 1},
]


def event(note=None, ins=None, arm=None, gate=None, dur=1, tie=False):
    return {
        "dur": dur,
        "sounds": note is not None,
        "tie": tie,
        "gate": gate,
        "note": note,
        "ins": ins,
        "arm": arm,
    }


def ins(points=(), prelude="hard_restart", accs=()):
    return {
        "adsr": [0x11, 0x22],
        "wave": 0x41,
        "sets": [],
        "on_note": [
            {
                "when": [["tie", "==", 0]],
                "sets": [["@wave", 0x41], ["@gate", 0xFF]],
                "point": list(points),
            }
        ],
        "prelude": prelude and {"stream": prelude},
        "accs": list(accs),
    }


def obj(events, streams=None, accs=None, instrument=None, tempo=2, early=1, cursors=(), **meta):
    """A one-voice trackerprog whose writes all pass through a flushed shadow."""
    st = {
        "note_on": {"rank": 0, "rows": [{"sets": [["ad", {"ins": "adsr.0"}]]}]},
        "hard_restart": {
            "rank": 0,
            "rows": [{"when": [[{"cell": "staged"}, "!=", 0]], "sets": [["ad", 0x0F]]}],
        },
        "exit": {"rank": 0, "rows": [{"sets": [["ctrl", GATED]]}]},
        "funktempo": {"rank": 0, "rows": [{"value": 2}, {"value": 4}]},
    }
    st.update(streams or {})
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": "hermetic",
            "family": "none",
            "song": 0,
            "voices": 1,
            "cycles_per_tick": 19656,
            "voice_order": [0],
            "commit_order": ["sr", "ad", "ctrl"],
            "shadow": {"registers": list(range(24, -1, -1))},
            "tempo": {
                "cell": "rowclock",
                "step": -1,
                "boundary": [[{"cell": "rowclock"}, "==", 0]],
                "early": [[{"cell": "rowclock"}, "==", early]],
                "reset": [
                    {
                        "when": [[{"cell": "rowclock"}, ">=", 0x80]],
                        "sets": [["@rowclock", {"cell": "tempo"}]],
                    }
                ],
            },
            "tick": ["row", "commit", "machine", "fetch", "prelude", {"stream": "exit"}],
            "row_consumes_tick": [["keys", "!=", 0]],
            "stage": [
                {"ins": True},
                {"sets": [["@gate", {"payload": "gate"}]], "when": [["gate_stmt", "!=", 0]]},
                {"hold": True},
                # whether the row the fetch read keys a note; a fetch tick that
                # stages none runs the same program over the empty facts, and says 0
                {"sets": [["@staged", {"payload": "keys"}]]},
            ],
            "row": [
                {"note": True, "when": [["sounds", "!=", 0]]},
                {"stream": "note_on", "when": [["keys", "!=", 0]]},
                {"commands": True},
            ],
            **meta,
        },
        "pitch": {"base": 0, "freq": [0x0100 * (k + 1) for k in range(8)]},
        "streams": st,
        "accs": accs or {},
        "instruments": {"1": instrument or ins()},
        "score": {
            "orders": [{"play": [{"pattern": 1, "transpose": 0}], "end": {"jump": 0}}],
            "patterns": {"1": {"events": events}},
            "commands": {
                "init": {"rows": [{"sets": [["@rowclock", 1], ["@tempo", tempo], ["@instr", 1]]}]}
            },
        },
        "globals": {"streams": [], "commit": [], "flags": {"produced": {"default": 0}}},
        "state0": {
            "shadow": [0] * 25,
            "cells": {k: [0] for k in CELLS},
            "ins": [1],
            "globals": {},
            "cursors": {k: [{"row": None, "hold": 0}] for k in cursors},
            "gcursors": {},
            "prologue": "init",  # the tune's init call, and the tick it spends
        },
    }


def column(w, reg):
    """One register's value at every tick, as the flush hands it to the chip."""
    return [dict(t)[reg] for t in w]


def acc(name, cell, **kw):
    rec = {
        "rank": 0,
        "cell": cell,
        "target": "freq",
        "width": 16,
        "policy": "wrap",
        "scope": "voice",
        "produce": [],
        "bound": {"from": "projected", "interval": [0, 0xFFFF], "witness": "the 16-bit store"},
    }
    rec.update(kw)
    if "width" in kw and "bound" not in kw:  # the store's own projection at that width
        rec["bound"] = dict(rec["bound"], interval=[0, (1 << kw["width"]) - 1])
    return {name: rec}


def test_the_flush_emits_the_whole_shadow_high_to_low_every_tick():
    w = render(obj([event(note=1, ins=1)]), 8)
    assert [r for r, _ in w[0]] == list(range(24, -1, -1))
    assert all(len(t) == 25 for t in w)
    assert column(w, AD) == [0, 0, 0, 0, 0x0F, 0x11, 0x11, 0x0F]


def test_the_countdown_clock_reloads_from_its_tempo_cell():
    p = Player(obj([event(note=1, ins=1)], tempo=4))
    seen = [p.tick() and 0 or p.c["rowclock"][0] for _ in range(8)]
    assert seen == [1, 0, 4, 3, 2, 1, 0, 4]  # the row is tempo + 1 ticks long


def test_a_funk_tempo_alternates_the_two_lengths_its_stream_names():
    """A tempo over a stream is a reset clause, ahead of the plain reload's own."""
    o = obj([event(note=1, ins=1)], tempo=0)
    o["meta"]["tempo"]["reset"].insert(
        0,
        {
            "when": [[{"cell": "rowclock"}, ">=", 0x80], [{"cell": "tempo"}, "<", 2]],
            "sets": [
                ["@rowclock", {"tabcell": ["funktempo", {"cell": "tempo"}, "value"]}],
                ["@tempo", {"xor": [{"cell": "tempo"}, 1]}],
            ],
        },
    )
    p = Player(o)
    seen = [p.tick() and 0 or p.c["rowclock"][0] for _ in range(9)]
    assert seen[2:8] == [2, 1, 0, 4, 3, 2]  # the two funk rows' own countdowns, in turn


def test_a_stream_holds_its_row_then_takes_its_op_and_its_next():
    o = obj(
        [event(note=1, ins=1)],
        streams={"wave": {"rank": 0, "rows": WAVE_ROWS}},
        instrument=ins(points=[["wave", 0, False]], prelude=None),
        tempo=6,
        cursors=["wave"],
    )
    w = render(o, 14)
    assert column(w, CTRL)[8:14] == [0, 0x41, 0x41, 0x21, 0x11, 0x11]
    assert column(w, FHI)[8:14] == [0, 0, 0, 3, 3, 3]  # held two ticks, then its own pitch
    assert column(w, FLO)[11:14] == [0, 0, 0]


def test_a_step_that_produces_stands_the_armed_accumulators_down():
    """The row that produced leaves a flag, and the arm that stands down reads it."""
    a = acc("slide", "freq", delta={"const": 0x100}, produce=[["freq_hi", "hi"]])
    arm = {"acc": "slide", "when": [[{"flag": "produced"}, "==", 0]]}
    o = obj(
        [event(note=1, ins=1)],
        streams={"wave": {"rank": 0, "rows": WAVE_ROWS}},
        accs=a,
        instrument=ins(points=[["wave", 0, False]], prelude=None, accs=[arm]),
        tempo=6,
        cursors=["wave"],
    )
    w = render(o, 14)
    assert column(w, FHI)[11:14] == [3, 3, 3]  # the op wins the tick; the slide stands down
    del o["instruments"]["1"]["accs"][0]["when"]  # and without the guard it does not
    assert column(render(o, 14), FHI)[11:14] != [3, 3, 3]


def test_a_cursor_is_on_a_row_or_on_none_and_never_says_so_by_its_index():
    """Row 0 is a row like any other; a cursor that runs no stream carries ``None``."""
    o = obj(
        [event(note=1, ins=1)],
        streams={"wave": {"rank": 0, "rows": [{"trap": "the horizon never steps here"}, {}]}},
        cursors=["wave"],
    )
    p = Player(o)
    for row in (0, 1):
        p.cursor["wave"][0]["row"] = row
        assert [k for _, _, _, k in p.slots()] == ["wave"]
    p.cursor["wave"][0]["row"] = None
    assert p.slots() == []  # the cursor is off, and the object said so
    with pytest.raises(AssertionError, match="never steps here"):
        p.srow("wave", 0)


def test_a_step_that_goes_to_no_row_stops_its_own_stream():
    """``next: null`` and a jump of null are the same value the cursor is turned off by."""
    rows = [{"sets": [["@wave", 0x21]], "next": 1}, {"sets": [["@wave", 0x11]], "next": None}]
    o = obj(
        [event(note=1, ins=1)],
        streams={"wave": {"rank": 0, "rows": rows}},
        instrument=ins(points=[["wave", 0, False]], prelude=None),
        tempo=6,
        cursors=["wave"],
    )
    p = Player(o)
    w = [p.tick() for _ in range(12)]
    assert column(w, CTRL)[9:12] == [0x41, 0x21, 0x11]  # the note-on, then the two rows
    assert p.cursor["wave"][0]["row"] is None  # and the second row is the last
    assert column([p.tick() for _ in range(2)], CTRL) == [0x11, 0x11]  # nothing steps it


def test_a_global_channel_steps_its_own_stream_and_commits_its_own_registers():
    a = acc("cutoff_step", "#cutoff", width=8, target="cutoff", delta={"const": "delta"})
    a["cutoff_step"]["scope"] = "global"
    rows = [
        {"trap": "no row zero"},
        {"sets": [["#cutoff", 0x10]]},
        {"hold": 3, "run": [{"acc": "cutoff_step", "delta": 5}]},
        {"jump": 2},
    ]
    o = obj(
        [event(note=1, ins=1)],
        streams={"filter": {"rank": 0, "rows": rows}},
        accs=a,
    )
    o["globals"]["streams"] = ["filter"]
    o["globals"]["commit"] = [[CUT, {"global": "cutoff"}], [VOL, {"global": "vol"}]]
    o["state0"]["globals"] = {"cutoff": 0, "vol": 0x0F}
    o["state0"]["gcursors"] = {"filter": {"row": 1, "hold": 0}}
    w = render(o, 6)
    assert column(w, CUT) == [0, 0, 0x10, 0x15, 0x1A, 0x1F]
    assert column(w, VOL)[:3] == [0, 0, 0x0F]


def test_the_fetch_stages_the_row_early_and_a_tie_holds_the_prelude():
    tied = {"id": 3, "tie": True, "rows": [], "arms": []}
    w = render(obj([event(note=1, ins=1), event(note=2, arm=tied)], tempo=3), 16)
    assert column(w, AD)[4:7] == [0, 0x0F, 0x11]  # the cut, one tick before the row
    assert column(w, AD)[7:16] == [0x11] * 9  # and never again: the second row ties


def test_a_held_command_runs_at_every_row_and_may_set_every_voice():
    cmd = {"id": 15, "all": [["@tempo", 5]]}
    p = Player(obj([event(note=1, ins=1, arm=cmd), event(note=2)], tempo=2))
    for _ in range(6):
        p.tick()
    assert p.c["tempo"][0] == 5 and p.held[0] is cmd


def test_an_event_of_several_rows_spends_them_before_the_next_is_fetched():
    w = render(obj([event(dur=3), event(note=4, ins=1)], tempo=1), 14)
    assert column(w, AD) == [0] * 9 + [0x0F] + [0x11] * 4


def test_a_move_outside_the_declared_bound_stops_the_render():
    """Section 5's bound is the invariant: the renderer asserts it, and says which acc."""
    a = acc("run", "vibtime", width=8, delta={"const": 40})
    a["run"]["bound"] = {"from": "proved", "interval": [0, 100], "witness": "a guard"}
    o = obj([event(note=1, ins=1)], accs=a, tempo=2, rest_arm=[{"acc": "run"}])
    with pytest.raises(AssertionError) as e:
        render(o, 16)
    assert str(e.value) == "run left its proved bound [0, 100] at 120"


def test_an_accumulator_with_no_bound_is_refused_where_it_moves():
    """Bounded is what an accumulator is; a record with no interval renders nothing."""
    a = acc("run", "vibtime", width=8, delta={"const": 1})
    del a["run"]["bound"]
    o = obj([event(note=1, ins=1)], accs=a, tempo=2, rest_arm=[{"acc": "run"}])
    with pytest.raises(AssertionError, match="run stores with no bound"):
        render(o, 8)


def test_the_amplitude_a_triangle_folds_at_is_not_the_bound_it_keeps():
    """Section 5 correction 1: the complement arm leaves the amplitude, by design."""
    a = acc(
        "phase",
        "vibtime",
        width=8,
        target="note",
        delta={"const": 2},
        policy="reflect-complement",
        amplitude={"interval": [0, 4]},
    )
    o = obj([event(note=1, ins=1)], accs=a, tempo=2, rest_arm=[{"acc": "phase"}])
    p = Player(o)
    got = [p.tick() and 0 or p.c["vibtime"][0] for _ in range(16)]
    # the cell swings against 4 and keeps the byte: two intervals, and only one is asserted
    assert max(got) == 255 and a["phase"]["bound"]["interval"] == [0, 0xFF]


def test_reflect_complement_folds_the_phase_at_its_bound():
    a = acc(
        "phase",
        "vibtime",
        width=8,
        target="note",
        delta={"const": 2},
        policy="reflect-complement",
        amplitude={"interval": [0, 4]},
    )
    o = obj([event(note=1, ins=1)], accs=a, tempo=2, rest_arm=[{"acc": "phase"}])
    p = Player(o)
    seen = [p.tick() and 0 or p.c["vibtime"][0] for _ in range(16)]
    assert seen[5:16] == [2, 4, 4, 6, 251, 251, 253, 255, 255, 1, 3]


def test_clamp_takes_its_target_where_the_step_would_pass_it():
    a = acc(
        "slide",
        "freq",
        delta={"const": 0x180},
        policy={"clamp": {"notefreq": None}},
        produce=[["freq_lo", "lo"], ["freq_hi", "hi"]],
    )
    o = obj([event(note=5, ins=1)], accs=a, tempo=2, rest_arm=[{"acc": "slide"}], pitch_links=[])
    p = Player(o)
    seen = []
    for i in range(12):
        p.tick()
        if i == 4:
            p.c["freq"][0] = 0x0100
        seen.append(p.c["freq"][0])
    assert seen[5:10] == [0x280, 0x400, 0x400, 0x580, 0x600]  # taken, never passed
    assert p.c["freq"][0] == 0x0600 and p.c["lastnote"][0] == 5


def test_take_is_the_degenerate_clamp_and_reaches_its_target_at_once():
    a = acc("snap", "freq", policy="take")
    o = obj([event(note=3, ins=1)], accs=a, tempo=2, rest_arm=[{"acc": "snap"}], pitch_links=[])
    p = Player(o)
    for _ in range(6):
        p.tick()
    assert p.c["freq"][0] == 0x0400
    assert p.c["freq"][0] == 0x0400 and p.c["lastnote"][0] == 3


def test_a_tabcell_reads_the_column_of_a_stream_row_a_cell_selects():
    rows = [{"delta": {"trap": "no speed"}}, {"delta": 7}, {"delta": 9}]
    p = Player(obj([event()], streams={"speed": {"rank": 0, "rows": rows}}))
    p.c["param"][0] = 2
    assert p.ev({"tabcell": ["speed", {"cell": "param"}, "delta"]}) == 9
    p.c["param"][0] = 0
    with pytest.raises(AssertionError, match="no speed"):
        p.ev({"tabcell": ["speed", {"cell": "param"}, "delta"]})
