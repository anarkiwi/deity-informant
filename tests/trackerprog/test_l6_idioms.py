"""L6 -- the canonical object: streams merged, cells spent, guards and names settled.

Scalar optimisation.  Nothing here moves a value, and the property the whole
level answers to is stated over the thirty hand objects: the certificate is the
one it was and the object is no bigger.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from deity_informant.trackerprog import poison, sizes  # noqa: E402
from deity_informant.trackerprog.passes import l6_canon  # noqa: E402
from deity_informant.trackerprog.passes.ir import Level, validate  # noqa: E402

import trackerprog_poison as TP  # noqa: E402

CACHE = str(ROOT / ".oracle-cache-poison")
TICKS = 24


def _obj(tick, streams, cells, tempo=None, row=None):
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": "canon",
            "song": 0,
            "voices": 1,
            "horizon": TICKS,
            "cycles_per_tick": 19656,
            "voice_order": [0],
            "commit_order": ["ctrl", "ad", "sr"],
            "instrument": {},
            "tempo": tempo
            or {"cell": "$p", "step": 0, "rate": 1, "phase": 0, "boundary": [[0, "!=", 0]]},
            "tick": tick,
            "row": row or [],
            "row_consumes_tick": False,
        },
        "pitch": {"base": 0, "freq": [0x0100 + 7 * i for i in range(16)]},
        "streams": streams,
        "accs": {},
        "instruments": {"0": {}},
        "score": {"patterns": {}, "orders": [{"play": [], "end": "stop"}]},
        "globals": {},
        "state0": {"cells": {**cells, "$p": [0]}, "ins": [0]},
    }


def _canon(obj, do=("merge", "propagate", "implied", "names")):
    l5 = Level(5, obj=obj)
    l6 = l6_canon.canonicalise(l5, do)
    validate(l5, l6, TICKS)
    return l6


def test_adjacent_streams_of_one_tick_are_one_stream():
    """A phase list of two guarded row lists with no phase between them."""
    obj = _obj(
        [{"stream": "a"}, {"stream": "b"}, "commit"],
        {
            "a": {"all": True, "rows": [{"sets": [["ad", {"const": 3}]]}]},
            "b": {"all": True, "rows": [{"sets": [["sr", {"const": 4}]]}]},
        },
        {},
    )
    got = _canon(obj)
    assert got.facts["merged"] == 1
    assert len(got.obj["streams"]) == 1
    assert len(next(iter(got.obj["streams"].values()))["rows"]) == 2


def test_a_cell_whose_reads_are_an_expression_over_state_no_row_moves_is_spent():
    """The predicate if-conversion raised: propagated, and its write dead."""
    obj = _obj(
        [{"stream": "a"}, "commit"],
        {
            "a": {
                "all": True,
                "rows": [
                    {"sets": [["@t", {"add": [{"cell": "counter"}, 1]}]]},
                    {"sets": [["ad", {"and": [{"cell": "t"}, 15]}]]},
                ],
            }
        },
        {"t": [0]},
    )
    got = _canon(obj)
    assert got.facts["propagated"] == ("t",)
    assert "t" not in got.obj["state0"]["cells"]
    rows = got.obj["streams"]["a"]["rows"]
    assert len(rows) == 1 and rows[0]["sets"][0][1] == {
        "and": [{"add": [{"cell": "counter"}, 1]}, 15]
    }


def test_a_guard_term_the_clock_s_boundary_implies_is_dropped():
    """The row phase runs where the boundary holds, so the row states it once."""
    tempo = {
        "cell": "clk",
        "step": -1,
        "rate": 1,
        "phase": 0,
        "boundary": [[{"cell": "phase"}, "==", 1]],
    }
    obj = _obj(
        ["row", "commit"],
        {
            "n": {
                "all": True,
                "rows": [{"when": [[{"cell": "phase"}, "==", 1]], "sets": [["ad", 3]]}],
            }
        },
        {"clk": [1]},
        tempo=tempo,
        row=[{"stream": "n"}],
    )
    got = _canon(obj, do=("implied",))
    assert got.facts["dropped"] == 1
    assert got.obj["streams"]["n"]["rows"][0]["when"] == []


def test_a_guard_term_over_two_constants_is_worth_what_it_is():
    """A term the object states outright is no term at all, or the row is dead."""
    obj = _obj(
        [{"stream": "a"}, "commit"],
        {"a": {"all": True, "rows": [{"when": [[1, "!=", 0], [0, "!=", 0]], "sets": [["ad", 3]]}]}},
        {},
    )
    got = _canon(obj, do=("implied",))
    assert got.facts["dropped"] == 2
    # the true term is no term and the false one makes the row dead, so the
    # stream the row was the whole of is gone with it
    assert "a" not in got.obj["streams"]


def test_a_cell_is_named_by_the_register_its_sole_reader_writes():
    """One naming: the slot the cell is, or the register the one reader sends it to."""
    obj = _obj(
        [{"stream": "a"}, "commit"],
        {
            "a": {
                "all": True,
                "rows": [
                    {"sets": [["@c0", {"add": [{"cell": "c0"}, 1]}]]},
                    {"sets": [["ad", {"and": [{"cell": "c0"}, 15]}]]},
                ],
            }
        },
        {"c0": [0]},
    )
    got = _canon(obj, do=("names",))
    assert got.facts["renamed"] == {"c0": "ad"}
    assert "ad" in got.obj["state0"]["cells"]


@pytest.mark.hvsc
def test_the_level_leaves_every_hand_object_no_bigger_and_certified():
    """The property, over the thirty builds: the size falls or holds, the render is one."""
    grew, diverged = [], []
    for name, b in TP.BUILD.items():
        obj = TP.build_object(name, CACHE)
        ticks = min(TP.horizon(b.cert, b.cert_song), 400)
        got = l6_canon.canonicalise(Level(5, obj=obj))
        if sizes.xz(sizes.compact(got.obj)) > sizes.xz(sizes.compact(obj)):
            grew.append(name)
        if not (poison.digests(obj, ticks) == poison.digests(got.obj, ticks)).all():
            diverged.append(name)
    assert grew == [] and diverged == []
