"""Hermetic snippet tests for the forms a fetch that spends the row needs.

No tune and no HVSC: one hand-written trackerprog per form -- a flush whose
entries carry the guard the image writes them under, the row's pitch and the
order's transpose staged with the row the clock runs ahead of, the row's own
commands spent at the fetch rather than held for the boundary, and a register of
the tune's one global channel written by the voice whose write-out sends it.
"""

from deity_informant.trackerprog.universal import Player, render

FLUSH = list(range(7))  # a one-voice image: the registers the write-out runs


def obj(streams=None, events=None, commands=None, shadow=None, **meta):
    """A one-voice trackerprog whose clock fetches one row ahead of its boundary."""
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
                "form": "countdown",
                "cell": "phase",
                "boundary": 0,
                "reload": "speed",
                "early": 2,
            },
            "tick": ["fetch", "row", "machine"],
            "row_consumes_tick": False,
            "row_command": "spent",
            "row": [{"note": True, "when": [["sounds", "!=", 0]]}],
            "player": "hermetic",
            **({} if shadow is None else {"shadow": {"registers": shadow}}),
            **meta,
        },
        "pitch": {"base": 0, "freq": [0x0100 * (k + 1) for k in range(16)]},
        "streams": streams or {},
        "accs": {},
        "instruments": {"0": {"on_note": [], "accs": []}},
        "score": {
            "orders": [{"play": [{"pattern": 0, "transpose": 3}], "end": {"jump": 0}}],
            "patterns": {"0": {"events": events or [event(1), event(2)]}},
            "commands": commands or {},
        },
        "globals": {"streams": [], "commit": [], "flags": {}, "stop_writes": []},
        "state0": {
            "shadow": [0] * 25,
            "cells": {
                "phase": [3],
                "speed": [3],
                "staged": [0],
                "xpose": [0],
                "level": [0],
                "delay": [0],
            },
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


def cells(p):
    return {k: v[0] for k, v in p.c.items()}


def test_the_fetch_stages_the_row_s_own_pitch_where_the_boundary_takes_it():
    """``note`` staged early is the pitch the row carries, held until the boundary."""
    o = obj(stage=[{"sets": [["@staged", {"payload": "note"}]], "when": [["sounds", "!=", 0]]}])
    p = Player(o)
    seen = []
    for _ in range(9):
        p.tick()
        seen.append((p.c["phase"][0], p.c["staged"][0]))
    # the clock counts 2, 1, 0 and the fetch at 2 stages the row the boundary takes
    assert seen[:4] == [(2, 1), (1, 1), (0, 1), (3, 1)]
    assert seen[4] == (2, 2)  # the next row, staged one clock step ahead of its own


def test_the_fetch_stages_the_order_s_transpose_with_the_row_it_plays():
    """The order's own column reaches a cell, which is where a modulator can read it."""
    p = Player(obj(stage=[{"sets": [["@xpose", {"payload": "transpose"}]]}]))
    p.tick()
    assert p.c["xpose"][0] == 3  # the play step's column, not the pattern's own byte
    assert cells(p)["note"] in (0, 4)


def test_a_row_s_commands_are_spent_at_the_fetch_and_not_held_for_the_boundary():
    """``cmds`` runs the row's own commands where the fetch reads them."""
    cmd = {"rows": [{"when": [], "sets": [["@level", 9]]}]}
    o = obj(
        stage=[{"commands": True}],
        events=[event(1, arm=["bump"]), event(2)],
        commands={"bump": cmd},
    )
    p = Player(o)
    p.tick()  # the fetch is the first tick of the clock's own count
    assert p.c["level"][0] == 9
    held = Player(
        obj(
            events=[event(1, arm=["bump"]), event(2)],
            commands={"bump": cmd},
            row=[{"commands": True}, {"note": True, "when": [["sounds", "!=", 0]]}],
        )
    )
    held.tick()
    assert held.c["level"][0] == 0  # with no staged step the boundary spends it
    for _ in range(2):
        held.tick()
    assert held.c["level"][0] == 9


def test_a_flush_entry_states_the_guard_the_image_writes_it_under():
    """The same registers in either direction, and a cell of the tune picks which."""
    up = [[r, [[{"cell": "delay"}, "==", 0]]] for r in FLUSH]
    down = [[r, [[{"cell": "delay"}, "!=", 0]]] for r in reversed(FLUSH)]
    st = {
        "turn": {
            "rank": 0,
            "all": True,
            "rows": [{"when": [], "sets": [["@delay", {"xor": [{"cell": "delay"}, 1]}]]}],
        }
    }
    w = render(obj(shadow=up + down, streams=st), 4)
    assert [r for r, _ in w[0]] == FLUSH  # the frame carries no delay
    assert [r for r, _ in w[1]] == list(reversed(FLUSH))  # and the next one does
    assert [r for r, _ in w[2]] == FLUSH


def test_a_flush_entry_with_no_guard_is_the_entry_every_frame_writes():
    """A bare register is the degenerate entry: no guard, so every flush carries it."""
    w = render(obj(shadow=FLUSH), 2)
    assert [r for r, _ in w[0]] == FLUSH
    assert [r for r, _ in w[1]] == FLUSH


def test_a_voice_writes_a_register_of_the_one_global_channel():
    """``reg.N`` is the channel's own register, sent by the voice whose write-out runs."""
    st = {
        "out": {
            "rank": 0,
            "all": True,
            "rows": [{"when": [], "sets": [["reg.22", {"global": "level"}], ["ctrl", 0x41]]}],
        }
    }
    w = render(obj(streams=st), 1)
    assert w[0] == [(22, 0x40), (4, 0x41)]  # the producer first, then the voice's edge
    image = render(obj(streams=st, shadow=FLUSH + [22]), 2)
    assert dict(image[1])[22] == 0x40  # through an image that holds it, the tick after
