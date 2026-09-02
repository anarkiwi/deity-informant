"""Hermetic snippet tests for an order program that is a program.

No tune and no HVSC: one hand-written trackerprog per form of section 3.6's
order grammar -- ``call``/``ret`` over a per-voice return stack, ``mark``/
``loop`` over one counted-loop register, ``jump``, and a ``stop`` that stops
one voice and not the tune -- plus the walk itself, a fetch that takes rows
until one of them carries a length.
"""

import pytest

from deity_informant.trackerprog.universal import Player, render


def obj(play, patterns, **meta):
    """A one-voice trackerprog whose row clock fires on every tick."""
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": "hermetic",
            "family": "none",
            "song": 0,
            "voices": 1,
            "cycles_per_tick": 19656,
            "voice_order": [0],
            "commit_order": ["ad", "sr", "ctrl"],
            "wide": [],
            "tempo": {
                "cell": "dur",
                "step": -1,
                "boundary": [[{"cell": "dur"}, "==", 0]],
            },
            "tick": ["row"],
            "row_consumes_tick": True,
            "row_command": "spent",
            "row": [
                {"commands": True},
                {"sets": [["ctrl", {"payload": "note"}]], "when": [["sounds", "!=", 0]]},
            ],
            **meta,
        },
        "pitch": {"base": 0, "freq": [0x0100 * (k + 1) for k in range(16)]},
        "streams": {},
        "accs": {},
        "instruments": {"0": {"on_note": [], "accs": []}},
        "score": {
            "orders": [{"play": play, "end": "stop"}],
            "patterns": patterns,
            "commands": {},
        },
        "globals": {"streams": [], "commit": [], "flags": {}},
        "state0": {"cells": {"dur": [1]}, "ins": [0], "cursors": {}, "gcursors": {}},
    }


def note(n, dur=1):
    return {
        "dur": dur,
        "sounds": True,
        "tie": False,
        "gate": None,
        "note": n,
        "ins": None,
        "arm": None,
    }


def cmd(sets):
    """A row that does not spend the tick: the walk takes it and keeps going."""
    return {
        "dur": 0,
        "sounds": False,
        "tie": False,
        "gate": None,
        "note": None,
        "ins": None,
        "arm": {"rows": [{"sets": sets}]},
    }


WALK = {"row_ends_fetch": [["dur", "!=", 0]]}


def played(o, ticks):
    """The ctrl value each tick left, which this snippet writes from the note."""
    return [w[0][1] if w else None for w in render(o, ticks)]


def test_a_step_with_no_op_falls_through_to_the_next():
    o = obj(
        [{"pattern": "0"}, {"pattern": "1", "op": {"jump": 0}}],
        {"0": {"events": [note(1)]}, "1": {"events": [note(2)]}},
    )
    assert played(o, 4) == [1, 2, 1, 2]


def test_a_call_runs_a_block_and_comes_back_to_the_step_it_names():
    """The stack is the score's: the call pushes where it returns, the ret pops it."""
    o = obj(
        [
            {"pattern": "0", "op": {"call": 2, "ret": 1}},
            {"pattern": "1", "op": {"jump": 0}},
            {"pattern": "2", "op": "ret"},
        ],
        {
            "0": {"events": [note(1)]},
            "1": {"events": [note(2)]},
            "2": {"events": [note(3)]},
        },
    )
    assert played(o, 6) == [1, 3, 2, 1, 3, 2]


def test_calls_nest_to_the_depth_the_score_uses():
    o = obj(
        [
            {"pattern": "0", "op": {"call": 2, "ret": 1}},
            {"pattern": "1", "op": {"jump": 0}},
            {"pattern": "2", "op": {"call": 3, "ret": 4}},
            {"pattern": "3", "op": "ret"},
            {"pattern": "4", "op": "ret"},
        ],
        {str(k): {"events": [note(k + 1)]} for k in range(5)},
    )
    p = Player(o)
    out = [w[0][1] for w in (p.tick() for _ in range(5))]
    assert out == [1, 3, 4, 5, 2] and p.callstack[0] == []


def test_a_counted_loop_runs_its_body_the_number_of_times_the_mark_carries():
    """One loop register per voice: the mark loads it, the loop spends it."""
    o = obj(
        [
            {"pattern": "0", "op": {"mark": 3, "next": 1}},
            {"pattern": "1", "op": {"loop": True, "next": 2}},
            {"pattern": "2", "op": {"jump": 0}},
        ],
        {"0": {"events": []}, "1": {"events": [note(7)]}, "2": {"events": [note(9)]}},
    )
    assert played(o, 8) == [7, 7, 7, 9, 7, 7, 7, 9]


def test_a_step_with_no_rows_is_a_step_of_the_program_and_no_row_of_the_score():
    o = obj(
        [
            {"pattern": "0", "op": {"jump": 1}},
            {"pattern": "1", "op": {"jump": 2}},
            {"pattern": "2", "op": {"jump": 0}},
        ],
        {"0": {"events": []}, "1": {"events": []}, "2": {"events": [note(5)]}},
    )
    assert played(o, 3) == [5, 5, 5]


def test_stop_stops_one_voice_and_not_the_tune():
    """The score stops each voice by itself, so the tick after it is not the end."""
    o = obj([{"pattern": "0", "op": "stop"}], {"0": {"events": [note(4)]}})
    o["meta"]["voices"] = 2
    o["meta"]["voice_order"] = [0, 1]
    o["state0"]["cells"]["dur"] = [1, 1]
    o["state0"]["ins"] = [0, 0]
    o["score"]["orders"].append({"play": [{"pattern": "1", "op": {"jump": 0}}], "end": "stop"})
    o["score"]["patterns"]["1"] = {"events": [note(6)]}
    p = Player(o)
    assert [tuple(w) for w in (p.tick() for _ in range(3))] == [
        ((4, 4), (11, 6)),
        ((11, 6),),
        ((11, 6),),
    ]
    assert p.stopped == [True, False] and p.entry is None


def test_a_voice_the_score_never_started_runs_no_clock():
    o = obj([], {})
    o["state0"]["stopped"] = [True]
    assert render(o, 3) == [[], [], []]


def test_the_fetch_walks_until_a_row_carries_a_length():
    """Every command on the way to the note is taken at the same boundary."""
    o = obj(
        [{"pattern": "0", "op": {"jump": 0}}],
        {"0": {"events": [cmd([["ad", {"const": 9}]]), cmd([["sr", {"const": 8}]]), note(1)]}},
        **WALK,
    )
    assert render(o, 2) == [[(5, 9), (6, 8), (4, 1)], [(5, 9), (6, 8), (4, 1)]]


def test_each_row_of_one_walk_is_its_own_group():
    """A walk that takes six rows is six acts, and a fetch that takes one is one."""
    o = obj(
        [{"pattern": "0", "op": {"jump": 0}}],
        {
            "0": {
                "events": [
                    cmd([["ctrl", {"const": 0x10}], ["freq", {"const": 0x0201}]]),
                    note(2),
                ]
            }
        },
        **WALK,
    )
    # the command's own group is sent before the note's, so its edge precedes
    # the producer the note leaves -- one act each, in the order the walk took
    assert render(o, 1) == [[(0, 1), (1, 2), (4, 0x10), (4, 2)]]


def test_a_walk_that_reaches_no_row_is_refused_by_the_render():
    o = obj(
        [{"pattern": "0", "op": {"jump": 0}}],
        {"0": {"events": [cmd([["ad", {"const": 1}]])]}},
        **WALK,
    )
    with pytest.raises(AssertionError, match="no row"):
        render(o, 1)
