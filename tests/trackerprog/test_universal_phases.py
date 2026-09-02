"""Hermetic snippet tests for the forms a phased, unshadowed tick needs.

No tune and no HVSC: one hand-written trackerprog per form -- the counter clock
and the guarded reset that turns it, a stream a guard admits, one divider for a
stream and for an accumulator, a step whose counter is read at entry, an edge
written twice in one tick, a register written twice in one act, a flag one
producer leaves and another reads, a producer that moves no cell, the clamp's
edge, and a step past the top of the tuning.
"""

import pytest

from deity_informant.trackerprog import printer
from deity_informant.trackerprog.universal import Player, render

FLO, FHI, PLO, PHI, CTRL, AD, SR = 0, 1, 2, 3, 4, 5, 6
CELLS = ("spdcnt", "pending", "hrins", "arpscnt", "wave", "gate", "pw", "freq", "chordpos")
LIVE = [[{"or": [{"cell": "pending"}, {"cell": "ins"}]}, "!=", 0]]


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


def instrument(**kw):
    rec = {
        "ctrl": 0x03,
        "hr": [0x0F, 0x00],
        "adsr": [0x11, 0x22],
        "wave": 0x41,
        "transpose": 0,
        "sets": [],
        "on_note": [
            {
                "when": [["tie", "==", 0]],
                "sets": [["@wave", 0x41], ["@gate", 0xFF], ["ad", 0x11], ["sr", 0x22]],
            }
        ],
        "prelude": {"stream": "hard_restart"},
        "accs": [],
    }
    rec.update(kw)
    return rec


def obj(events, streams=None, accs=None, tempo=4, ins=None, **meta):
    """A one-voice trackerprog whose tick is three phases of a counted clock."""
    st = {
        "exit": {"rows": [{"when": LIVE, "sets": [["ctrl", {"cell": "wave"}]]}]},
        "gate_row": {"rows": [{"sets": [["@gate", {"payload": "gate"}]]}]},
        "pitch_row": {"rows": [{"sets": [["#seen", {"cell": "note"}]]}]},
        "hard_restart": {
            "rows": [
                {
                    "when": [
                        [{"cell": "pending"}, "!=", 0],
                        [{"cell": "phase"}, "==", 0],
                        [{"and": [{"insrec": ["hrins", "ctrl"]}, 2]}, "!=", 0],
                    ],
                    "sets": [["ad", {"insrec": ["hrins", "hr.0"]}], ["sr", 0x00]],
                }
            ],
        },
        "pitch_out": {
            "rank": 25,
            "all": True,
            "when": LIVE,
            "rows": [{"sets": [["pitch", {"cell": "freq"}]]}],
        },
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
            "commit_order": ["ad", "sr", "ctrl"],
            "wide": ["freq", "pw"],
            "tempo": {
                "cell": "spdcnt",
                "step": 1,
                "boundary": [[{"cell": "phase"}, "==", 2]],
                "fetch": [[{"cell": "phase"}, "==", 0]],
                "early": [[{"cell": "phase"}, "<", 2]],
                "reset": [
                    {
                        "when": [[{"cell": "spdcnt"}, ">=", tempo]],
                        "sets": [["@spdcnt", 0]],
                    }
                ],
            },
            "tick": [
                "fetch",
                "prelude",
                "commit",
                "row",
                "commit",
                "machine",
                {"stream": "exit"},
            ],
            "row_consumes_tick": [["sounds", "!=", 0]],
            "row_command": "spent",
            # the fetch's own row program: the instrument the row will play, staged
            # under the guard that says the fetch read a row at all, and whether
            # that row keys a note -- which a fetch that stages none says is 0
            "stage": [
                {"sets": [["@hrins", {"payload": "ins"}]], "when": [["dur", "!=", 0]]},
                {"sets": [["@pending", {"payload": "keys"}]]},
            ],
            "row": [
                {"sets": [["@pending", 0]]},
                {"ins": True},
                {"stream": "gate_row", "when": [["gate_stmt", "!=", 0]]},
                {"note": True, "when": [["sounds", "!=", 0]]},
                {"stream": "pitch_row", "when": [["sounds", "!=", 0]]},
                {"commands": True},
            ],
            "pitch_target": "@freq",
            **meta,
        },
        "pitch": {"base": 0, "freq": [0x0100 * (k + 1) for k in range(8)]},
        "streams": st,
        "accs": accs or {},
        "instruments": {"0": instrument(note_sets=[], accs=[]), "1": ins or instrument()},
        "score": {
            "orders": [{"play": [{"pattern": 1, "transpose": 0}], "end": {"jump": 0}}],
            "patterns": {"1": {"events": events}},
            "commands": {},
        },
        "globals": {
            "streams": [],
            "commit": [["mode_vol", {"global": "seen"}]],
            "flags": {"C": {"default": 0}},
        },
        "state0": {
            "cells": {k: [0] for k in CELLS},
            "ins": [0],
            "wave": [0],
            "globals": {"seen": 0},
            "cursors": {},
            "gcursors": {},
        },
    }


def col(w, reg):
    """One register's writes each tick, in order, empty where it was not written."""
    return [[v for r, v in t if r == reg] for t in w]


def test_the_counter_clock_runs_three_phases_and_turns_on_its_own_guard():
    """spdcnt 0, 1, 2 are the fetch, the prelude and the row; the reset turns it."""
    w = render(obj([event(note=1, ins=1), event(note=2, ins=1)]), 9)
    p = Player(obj([event(note=1, ins=1)]))
    seen = [(p.tick(), p.tickphase)[1] for _ in range(9)]
    assert seen == [0, 1, 2, 3, 0, 1, 2, 3, 0]
    assert col(w, AD)[0] == [0x0F]  # the prelude, at the fetch
    assert col(w, AD)[2] == [0x11]  # the note's own, at the row


def quiet_obj(streams, cursors, events=None):
    """A voice whose rows sound nothing, so its tables run on every tick."""
    o = obj(events or [event(), event()], streams=streams)
    o["state0"]["ins"] = [1]
    o["state0"]["cursors"] = cursors
    return o


def test_a_stream_a_guard_does_not_admit_does_not_run():
    """A stream carries its own guards, and the voice's tick reads them."""
    rows = [{"trap": "no stream"}, {"sets": [["@wave", 0x21]], "next": 1}]
    cur = {"wave": [{"row": 1, "hold": 0}]}
    quiet = quiet_obj({"wave": {"rank": 5, "when": [[0, "!=", 0]], "rows": rows}}, cur)
    loud = quiet_obj({"wave": {"rank": 5, "rows": rows}}, cur)
    assert col(render(quiet, 6), CTRL) == [[0]] * 6
    assert col(render(loud, 6), CTRL) == [[0x21]] * 6


def test_a_divider_kept_in_a_cell_holds_the_stream():
    """Section 3.3's rate, in a cell the score can set: one row every k + 1 ticks."""
    rows = [
        {"trap": "no stream"},
        {"sets": [["@wave", 0x21]], "next": 2},
        {"sets": [["@wave", 0x41]], "next": 1},
    ]
    o = quiet_obj(
        {
            "wave": {
                "rank": 5,
                "rate": {"cell": "arpscnt", "reload": 2},
                "rows": rows,
            }
        },
        {"wave": [{"row": 1, "hold": 0}]},
    )
    assert col(render(o, 9), CTRL) == [[0x21]] * 3 + [[0x41]] * 3 + [[0x21]] * 3


def _sweep(epoch, acc=()):
    rows = [
        {"trap": "no stream"},
        {"hold": 3, "run": [{"acc": "step", "delta": 1}], "sets": [["@wave", 0x11]], "next": 1},
    ]
    o = quiet_obj(
        {"pulse": dict({"rank": 5, "rows": rows}, **epoch)},
        {"pulse": [{"row": 1, "hold": 0}]},
    )
    o["accs"] = {
        "step": dict(
            {
                "id": "step",
                "rank": 0,
                "cell": "pw",
                "target": "pw",
                "width": 16,
                "delta": {"const": "delta"},
                "policy": "wrap",
                "scope": "voice",
                "produce": [["pw_lo", "lo"]],
                "bound": {"from": "projected", "interval": [0, 0xFFFF], "witness": "16-bit"},
            },
            **dict(acc),
        )
    }
    return [x[0] for x in col(render(o, 9), PLO) if x]


def test_one_divider_for_a_stream_and_for_an_accumulator():
    """§3.3's ``rate`` is one form and one procedure wherever it is a divider."""
    assert _sweep({}, {"rate": {"cell": "arpscnt", "reload": 2}}) == [1, 2, 3]
    with pytest.raises(AssertionError):  # a bare k names no counter, so it is no divider
        _sweep({}, {"rate": 3})


def test_a_step_whose_counter_is_read_at_entry_does_not_run_on_the_tick_it_ends():
    """#297's epochs: the consuming tick runs the step, or it does not, and the object says."""
    assert _sweep({}) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert _sweep({"epoch": "entry"}) == [1, 2, 3, 4, 5, 6]


def test_an_edge_written_twice_in_one_tick_is_two_writes():
    """Section 2 rule 1: the tick is a sequence of acts, each in the commit order."""
    cmd = {"rows": [{"sets": [["sr", 0x77], ["ad", 0x88]]}]}
    o = obj([event(note=1, ins=1, arm="louder")])
    o["score"]["commands"]["louder"] = cmd
    w = render(o, 4)
    assert [(r, v) for r, v in w[2] if r in (AD, SR)] == [
        (AD, 0x11),
        (SR, 0x22),
        (AD, 0x88),
        (SR, 0x77),
    ]


def test_a_register_written_twice_in_one_act_keeps_the_last_write():
    """An act has one slot per register; two guarded rows are two acts and two writes."""
    twice = obj([event(note=1, ins=1, arm="twice")])
    twice["score"]["commands"]["twice"] = {"rows": [{"sets": [["sr", 0x33], ["sr", 0x44]]}]}
    assert [x for x in render(twice, 4)[2] if x[0] == SR] == [(SR, 0x22), (SR, 0x44)]
    split = obj([event(note=1, ins=1, arm="twice")])
    split["score"]["commands"]["twice"] = {
        "rows": [{"sets": [["sr", 0x33]]}, {"sets": [["sr", 0x44]]}]
    }
    assert [x for x in render(split, 4)[2] if x[0] == SR] == [(SR, 0x22), (SR, 0x33), (SR, 0x44)]


def test_a_flag_one_producer_leaves_and_another_reads():
    """A carry another act of the tick left is a named flag, not an expression."""
    rows = [{"trap": "no stream"}, {"sets": [["!C", 1]], "next": 1}]
    o = obj(
        [event(note=1, ins=1)],
        streams={
            "wave": {"rank": 5, "rows": rows},
            "pitch_out": {
                "rank": 25,
                "all": True,
                "when": LIVE,
                "rows": [{"sets": [["pitch", {"add": [{"cell": "freq"}, {"flag": "C"}]}]]}],
            },
        },
    )
    o["state0"]["cursors"] = {"wave": [{"row": 1, "hold": 0}]}
    assert col(render(o, 5), FLO)[3] == [0x01]  # pitch[1] is $0200, plus the flag


def test_a_producer_may_write_the_chip_without_moving_the_cell():
    """``pitch`` emits the pair; the cell a step took is not the value it writes."""
    rows = [{"trap": "no stream"}, {"op": {"pitch": 2}, "next": 1}]
    o = obj(
        [event(note=1, ins=1)],
        streams={
            "wave": {"rank": 5, "rows": rows},
            "pitch_out": {
                "rank": 25,
                "all": True,
                "when": LIVE,
                "rows": [{"sets": [["pitch", {"add": [{"cell": "freq"}, 5]}]]}],
            },
        },
    )
    o["state0"]["cursors"] = {"wave": [{"row": 1, "hold": 0}]}
    p = Player(o)
    w = [p.tick() for _ in range(5)]
    assert p.c["freq"][0] == 0x0300  # the step took note 2 into the cell
    assert col(w, FLO)[3] == [0x05]  # and the producer wrote five past it


def test_a_clamp_takes_its_target_at_the_edge_the_object_names():
    """The step that lands exactly on the target either reaches it or does not."""

    def run(edge):
        o = obj(
            [event(note=3, ins=1)],
            accs={
                "porta": {
                    "id": "porta",
                    "rank": 0,
                    "cell": "freq",
                    "target": "freq",
                    "width": 16,
                    "delta": {"const": 0x100},
                    "policy": {"clamp": {"notefreq": None}, "edge": edge},
                    "scope": "voice",
                    "produce": [],
                    "bound": {"from": "proved", "interval": [0, 0xFFFF], "witness": "the target"},
                }
            },
            ins=instrument(accs=[{"acc": "porta"}], note_sets=[]),
        )
        o["state0"]["cells"]["freq"] = [0x0300]
        return [x[0] for x in col(render(o, 6), FLO) if x]

    assert run(0) == [0, 0, 0, 0, 0]  # the step reaching the target takes it
    assert run(1) == [0, 0, 1, 0, 0]  # the step reaching it goes one past, then snaps


def test_a_step_past_the_top_of_the_tuning_is_the_stream_s_own():
    """A note column of seven bits wraps; what it plays there is not a pitch."""
    beyond = {"id": "wave.pitch", "words": [{"u16": [0x34, 0x12]}]}
    rows = [
        {"trap": "no stream"},
        {"sets": [["@wave", 0x21]], "op": {"pitch": 7, "relative": True, "wrap": 0x7F}, "next": 1},
    ]
    o = obj(
        [event(note=1, ins=1)],
        streams={"wave": {"rank": 5, "rows": rows, "beyond": beyond}},
    )
    o["state0"]["cursors"] = {"wave": [{"row": 1, "hold": 0}]}
    w = render(o, 5)
    assert col(w, FLO)[3] == [0x34] and col(w, FHI)[3] == [0x12]


def test_the_global_channel_commits_once_the_voices_have_run():
    """A channel whose value the voices move must be committed after them."""
    w = render(obj([event(note=3, ins=1)]), 4)
    assert col(w, 24) == [[0], [0], [3], [3]]  # the row's note, seen on its own tick


def test_the_print_carries_the_phased_forms():
    """The print walks what is there: the counter, the acts, and the guarded rows."""
    text = printer.render(obj([event(note=1, ins=1, gate=None)]))
    for line in (
        "tempo spdcnt +1, row at phase == 2, fetched where phase == 0, early where phase < 2",
        "row 2      stream gate_row when <gate_stmt> != 0",
        "row 3      the sound the row keys when <sounds> != 0",
        "row 5      the row's own commands",
        "staged 1   sets @pending := keys",
    ):
        assert line in text, line
    assert printer.numbers(text)["blocks"] >= 5
