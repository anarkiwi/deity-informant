"""Hermetic snippets for the compiler: one per §5 form it folds, reads or refuses.

No tune and no HVSC: the smallest object the player will build, and expressions
read through the closures they compile to, so every branch of
:mod:`deity_informant.trackerprog.compiler` is exercised by data alone.
"""

import pytest

from deity_informant.trackerprog.universal import Player

X = {"cell": "x"}  # the one cell these snippets read: 5, on the one voice


def acc(**over):
    """The smallest accumulator §5 admits: a cell, a width, a bound and a delta."""
    return dict(
        {
            "rank": 0,
            "cell": "param",
            "target": "none",
            "scope": "voice",
            "width": 8,
            "produce": [],
            "policy": {"wrap": True},
            "rate": 1,
            "delta": 1,
            "bound": {"from": "hermetic", "interval": [0, 0xFF]},
        },
        **over,
    )


def player(streams=None, accs=None, arms=()):
    """A one-voice trackerprog that plays nothing: the compiler is what is read."""
    return Player(
        {
            "$trackerprog": 1,
            "meta": {
                "tune": "hermetic",
                "family": "none",
                "song": 0,
                "voices": 1,
                "cycles_per_tick": 19656,
                "voice_order": [0],
                "commit_order": ["ctrl", "ad", "sr"],
                "tempo": {
                    "cell": "rowsleft",
                    "step": -1,
                    "boundary": [[{"cell": "rowsleft"}, "==", 0]],
                },
                "tick": ["row", "machine"],
                "row_consumes_tick": True,
                "row": [{"ins": True}],
            },
            "pitch": {"base": 1, "freq": [0x100 * k for k in range(1, 8)]},
            "streams": streams or {},
            "accs": accs or {},
            "instruments": {"0": {"adsr": [1, 2], "wave": 0x41, "on_note": [], "accs": list(arms)}},
            "score": {
                "patterns": {"1": {"events": []}},
                "orders": [{"play": [1], "end": "jump"}],
                "commands": {},
            },
            "state0": {"cells": {"param": [0], "x": [5]}},
        }
    )


@pytest.mark.parametrize(
    "node,want",
    [
        ({"field": [X, 0x3]}, 1),
        ({"bit": [X, 0]}, 1),
        ({"carry_out": [X, 2]}, 1),
        ({"borrow_out": [X, 2]}, 0),
        ({"u16": [X, 2]}, 0x205),
        ({"fold": [X, 0x7]}, 2),  # 5 is past half of 7, so the triangle folds it back
        ({"interval": X}, 0x100),  # the step from note 5 to note 6 of this tuning
        ({"interval": None}, 0x100),  # the same step, of the note the voice sounds
    ],
)
def test_every_expression_form_reads_what_section_5_says_it_does(node, want):
    p = player()
    p.c["note"] = [5]
    assert p.ev(node) == want


def test_an_expression_form_the_grammar_does_not_have_is_refused():
    with pytest.raises(KeyError, match="fortissimo"):
        player().ev({"fortissimo": 1})


def test_a_constant_the_payload_binds_is_a_number_or_an_expression():
    """``const`` names what an arm or a command states beside the record it arms."""
    p = player()
    assert p.ev({"const": "k"}, {"k": 3}) == 3
    assert p.ev({"const": "k"}, {"k": X}) == 5  # the name binds an expression, not a number
    # where the object fixes the payload, both are spent at compile time
    assert p.code_of({"const": "k"}, {"k": 3})(None) == 3
    assert p.code_of({"const": "k"}, {"k": X})(None) == 5
    assert p.code_of({"const": "k"}, {})({"k": 4}) == 4  # no such name: the tick's own


def test_a_guard_list_of_any_length_is_its_terms_and_nothing_else():
    """One, two, three and a chain: the same conjunction, and the same answer."""
    p = player()
    hi = [X, "<", 9]
    lo = [3, "<", X]  # the operand the object states outright is either side
    for n in range(1, 6):
        assert p.guardcode([hi, lo][: n % 2 + 1] * n)(None) is True
    assert p.guardcode([hi] * 3 + [[X, "==", 4]])(None) is False


def test_an_accumulator_runs_under_its_own_guard_and_its_arm_s(recwarn):
    """§5's ``when`` and the arm's are one predicate, and either one refuses."""
    p = player(accs={"v": acc(when=[[X, "==", 5]])})
    assert p.armof({"acc": "v", "when": [[X, "<", 9]]}).when(None) is True
    assert p.armof({"acc": "v", "when": [[X, ">", 9]]}).when(None) is False
    assert p.armof({"acc": "v"}).when(None) is True
    p = player(accs={"v": acc()})
    assert p.armof({"acc": "v", "when": [[X, ">", 9]]}).when(None) is False


def test_a_stream_column_a_row_does_not_carry_is_read_out_or_refused():
    """The compiled column is one closure per row; the rest is the row's own."""
    rows = [{"delta": 7}, {"trap": "no row here"}, {"width": 1}]
    p = player(streams={"speed": {"rows": rows}})
    read = {"tabcell": ["speed", X, "delta"]}
    p.c["x"] = [0]
    assert p.ev(read) == 7
    p.c["x"] = [2]  # a row without the column: read out of the row itself
    with pytest.raises(KeyError, match="delta"):
        p.ev(read)
    p.c["x"] = [1]  # a row the object marks as no row at all
    with pytest.raises(AssertionError, match="no row here"):
        p.ev(read)
