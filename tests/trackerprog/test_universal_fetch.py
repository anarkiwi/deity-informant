"""Hermetic snippet tests for the forms a fetch that spends the row needs.

No tune and no HVSC: one hand-written trackerprog per form -- a flush whose
entries carry the guard the image writes them under, the row's pitch and the
order's transpose staged with the row the clock runs ahead of, the row's own
commands spent at the fetch rather than held for the boundary, and a register of
the tune's one global channel written by the voice whose write-out sends it.
"""

from deity_informant.trackerprog.universal import REG, Player, render

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
                "cell": "rowclock",
                "step": -1,
                "boundary": [[{"cell": "rowclock"}, "==", 0]],
                "early": [[{"cell": "rowclock"}, "==", 2]],
                "reset": [
                    {
                        "when": [[{"cell": "rowclock"}, ">=", 0x80]],
                        "sets": [["@rowclock", {"cell": "speed"}]],
                    }
                ],
            },
            "tick": ["fetch", "row", "machine"],
            "row_consumes_tick": False,
            "row_command": "spent",
            "row": [{"note": True, "when": [["sounds", "!=", 0]]}],
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
                "rowclock": [3],
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
        seen.append((p.c["rowclock"][0], p.c["staged"][0]))
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


# ---- the order program, under a clock that prefetches -------------------------
# *When* the row is read and *what shape the sequencer is* are two properties.
# They were coupled: ``advance``, the prefetch path's cursor, stepped
# ``orderpos`` by one of its own instead of calling ``order_step``, so a
# prefetching family with a called or counted score walked past ``call``,
# ``mark`` and ``loop`` as though each were ``play``.  It stayed invisible
# because the three prefetching families have flat orders and the two with order
# programs do not prefetch -- no exemplar separated them.

ORDER_ROW = [
    {"note": True, "when": [["sounds", "!=", 0]]},
    {"sets": [["ctrl", {"payload": "note"}]], "when": [["sounds", "!=", 0]]},
]


class Coupled(Player):
    """``advance`` before the fix: its own increment, ignoring ``play_of``'s ``op``."""

    def advance(self, v):
        self.evrow[v] += 1
        if self.evrow[v] == len(self.pattern_of(v)["events"]):
            self.evrow[v] = 0
            self.c["orderpos"][v] += 1
            self.publish("wrap", v)
            self.publish("order", v, {"pos": self.c["orderpos"][v]})


def ordered(play):
    """A prefetching object whose score is an order *program*; one row per step."""
    o = obj(events=[event(1)], row=ORDER_ROW)
    o["score"]["orders"] = [{"play": play, "end": {"jump": 0}}]
    o["score"]["patterns"] = {k: {"events": [event(n)]} for k, n in (("0", 1), ("1", 7), ("2", 9))}
    return o


def notes(cls, o, ticks):
    """The note each tick keyed, off the ``ctrl`` byte this snippet writes it to."""
    p, out = cls(o), []
    for _ in range(ticks):
        out += [v for r, v in p.tick() if r == REG["ctrl"]]
    return out


def test_a_counted_loop_runs_its_body_where_the_clock_prefetches():
    """``mark``/``loop`` under a ``fetch`` phase: the body three times, not once."""
    o = ordered(
        [
            {"pattern": "0", "op": {"mark": 3, "next": 1}},
            {"pattern": "1", "op": {"loop": True, "next": 2}},
            {"pattern": "2", "op": {"jump": 0}},
        ]
    )
    assert notes(Player, o, 16) == [1, 7, 7, 7]
    assert notes(Coupled, o, 16) == [1, 7, 9, 1]  # the mark and the loop taken as play


def test_a_call_returns_where_it_says_where_the_clock_prefetches():
    """``call``/``ret`` under a ``fetch`` phase: the stack is the score's either way."""
    o = ordered(
        [
            {"pattern": "0", "op": {"call": 2, "ret": 1}},
            {"pattern": "1", "op": {"jump": 0}},
            {"pattern": "2", "op": "ret"},
        ]
    )
    assert notes(Player, o, 16) == [1, 9, 7, 1]
    assert notes(Coupled, o, 16) == [1, 7, 9, 1]  # walked straight past the call


def test_an_empty_pattern_is_no_row_where_the_fetch_reads_it():
    """A pattern with no events stages nothing; the fetch does not index past its end."""
    o = obj()
    o["score"]["patterns"]["0"] = {"events": []}
    p = Player(o)
    for _ in range(8):
        assert p.tick() == []
    assert p.staged == [None] and p.c["orderpos"] == [0]


def test_a_play_list_that_stops_at_its_end_stops_where_the_fetch_reads_it():
    """The terminator is the order's own datum, answered the same way in both positions."""
    o = ordered([{"pattern": "0"}])
    o["score"]["orders"] = [{"play": [{"pattern": "0"}], "end": "stop"}]
    p = Player(o)
    for _ in range(12):
        p.tick()
    assert p.stopping == 2 and notes(Player, o, 12) == [1]
    o["score"]["orders"] = [{"play": [{"pattern": "0"}], "end": {"jump": 0}}]
    assert notes(Player, o, 12) == [1, 1, 1]  # the same list, wrapped instead of stopped


def test_a_stop_in_a_prefetched_score_stops_the_voice():
    """``stop`` reaches the voice through the fetch's wrap, and halts its clock."""
    o = ordered([{"pattern": "0", "op": "stop"}, {"pattern": "1", "op": {"jump": 0}}])
    p = Player(o)
    for _ in range(12):
        p.tick()
    assert p.stopped == [True] and p.stopping == 0
    q = Coupled(o)  # the coupled body walks past the stop and never halts
    for _ in range(12):
        q.tick()
    assert q.stopped == [False]
