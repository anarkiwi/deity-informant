"""Hermetic snippet tests for the forms a partial register image needs.

No tune and no HVSC: one hand-written trackerprog per form -- a flush that names
its own registers in its own order, a global commit to a register the image does
not hold, the section 5 cell vocabulary read and written as a ``sets`` target,
``xor``, a row that never spends its tick, a global ``all`` stream, and the gate
reporting the decision the step made rather than a re-reading of the cell.
"""

from deity_informant.trackerprog.universal import Player, render

FLUSH = [2, 3, 0, 1, 6, 5, 4, 23, 24]  # the image, in the order the write-out runs
CUT = 22  # written where it is made: the image has no byte for it


def obj(streams=None, accs=None, arms=(), commit=(), gstreams=(), cells=None, **meta):
    """A one-voice trackerprog whose image is a proper subset of the register file."""
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
            "shadow": {"registers": list(FLUSH)},
            "wide": ["level"],
            "tempo": {"form": "divider", "rate": 1, "phase": 0},
            "tick": ["row", "machine"],
            "row_consumes_tick": False,
            "row_command": "spent",
            "row": [{"commands": True}, {"note": True, "when": [["sounds", "!=", 0]]}],
            "player": "hermetic",
            **meta,
        },
        "pitch": {"base": 0, "freq": [0x0100 * (k + 1) for k in range(8)]},
        "streams": streams or {},
        "accs": accs or {},
        "instruments": {"0": {"on_note": [], "accs": list(arms)}},
        "score": {
            "orders": [{"play": [{"pattern": 0}], "end": {"jump": 0}}],
            "patterns": {"0": {"events": [event(), event()]}},
            "commands": {},
        },
        "globals": {
            "streams": list(gstreams),
            "commit": list(commit),
            "flags": {},
            "stop_writes": [],
        },
        "state0": {
            "shadow": [0] * 25,
            "cells": dict({"tick_no": [0]}, **(cells or {})),
            "ins": [0],
            "globals": {"level": 0x40},
            "cursors": {},
            "gcursors": {},
        },
    }


def event(note=None, arm=None, dur=1):
    return {
        "dur": dur,
        "sounds": note is not None,
        "tie": False,
        "gate": None,
        "note": note,
        "ins": None,
        "arm": arm,
    }


def test_the_flush_writes_the_registers_the_image_names_in_its_own_order():
    """The image is the registers the flush lists; the file's others are not its."""
    w = render(obj(), 2)
    assert [r for r, _ in w[0]] == FLUSH
    assert 21 not in {r for t in w for r, _ in t}  # no byte, so no write


def test_a_global_commit_outside_the_image_reaches_the_chip_on_its_own_tick():
    """A register the flush does not carry is written where it is made, not deferred."""
    step = {
        "all": True,
        "rows": [{"when": [], "sets": [["#level", {"add": [{"global": "level"}, 1]}]]}],
    }
    w = render(
        obj(
            streams={"chan": step},
            gstreams=["chan"],
            commit=[[CUT, {"global": "level"}], [24, {"global": "level"}]],
        ),
        3,
    )
    # the channel steps before the voices, and its commit lands this tick at $16
    assert [dict(t)[CUT] for t in w] == [0x41, 0x42, 0x43]
    # the same value through the image is the tick after, because the flush is
    assert [dict(t)[24] for t in w] == [0x00, 0x41, 0x42]


def test_the_cell_vocabulary_is_one_for_a_read_a_write_and_an_accumulator():
    """``shadow.pw`` names the image half a set writes, a guard reads and an acc moves."""
    st = {
        "poke": {
            "rank": 0,
            "all": True,
            "rows": [
                {
                    "when": [[{"cell": "shadow.pw.hi"}, "==", 0]],
                    "sets": [["shadow.pw.hi", 3], ["shadow.pw.lo", 0x10]],
                },
            ],
        }
    }
    a = {
        "sweep": {
            "rank": 1,
            "cell": "shadow.pw",
            "target": "pw",
            "width": 16,
            "delta": {"const": 1},
            "policy": "wrap",
            "scope": "voice",
            "produce": [],
            "when": [[{"cell": "shadow.pw"}, ">=", 0x300]],
        }
    }
    w = render(obj(streams=st, accs=a, arms=[{"acc": "sweep"}]), 4)
    assert [dict(t)[2] for t in w] == [0x00, 0x11, 0x12, 0x13]
    assert [dict(t)[3] for t in w] == [0x00, 0x03, 0x03, 0x03]


def test_xor_is_an_expression_like_and_and_or():
    st = {
        "poke": {
            "rank": 0,
            "all": True,
            "rows": [{"when": [], "sets": [["ctrl", {"xor": [{"cell": "mask"}, 0x0F]}]]}],
        }
    }
    w = render(obj(streams=st, cells={"mask": [0x41]}), 2)
    assert [dict(t)[4] for t in w] == [0x00, 0x4E]


def test_a_row_that_never_spends_its_tick_still_runs_the_machine():
    """``row_consumes_tick`` false is never, not the empty guard list, which is always."""
    st = {
        "poke": {
            "rank": 0,
            "all": True,
            "rows": [
                {
                    "when": [],
                    "sets": [
                        ["sr", {"add": [{"cell": "sr_c"}, 1]}],
                        ["@sr_c", {"add": [{"cell": "sr_c"}, 1]}],
                    ],
                }
            ],
        }
    }
    w = render(obj(streams=st, cells={"sr_c": [0]}), 3)
    assert [dict(t)[6] for t in w] == [0x00, 0x01, 0x02]


def test_the_gate_reports_the_decision_the_step_made_not_the_cell_it_left():
    """The step decides once, before it moves: a gate re-reading the cell would flip."""
    a = {
        "bounce": {
            "rank": 0,
            "cell": "level",
            "target": "pw",
            "width": 8,
            "delta": {"const": 4},
            "policy": "wrap",
            "scope": "voice",
            "produce": [],
            "step_when": [[{"cell": "level"}, "<", 8]],
            "gate": {"true": [["ad", 0x11]], "false": [["ad", 0x22]]},
        }
    }
    w = render(obj(accs=a, arms=[{"acc": "bounce"}], cells={"level": [0]}), 4)
    # level 0 -> 4 -> 8: the step that took it to 8 still reports that it stepped
    assert [dict(t)[5] for t in w] == [0x00, 0x11, 0x11, 0x22]


def test_the_shadow_survives_a_replay_of_the_same_object():
    """Two players over one object see the same image: nothing is shared."""
    o = obj()
    assert render(o, 3) == [Player(o).tick() for _ in range(3)]
