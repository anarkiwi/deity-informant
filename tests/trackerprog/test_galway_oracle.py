"""Comic Bakery as a hand-written trackerprog: the family whose score is a program.

``tools/trackerprog_galway.py`` states Martin Galway's player in section 2's
vocabulary; this is its certificate over all fourteen subtunes, and the two
things the family says: a counted loop that nests, and a stop for a sequencer.
"""

import sys
from functools import lru_cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import Player, render  # noqa: E402

import trackerprog_galway as TG  # noqa: E402
from _hvsc import COMIC_BAKERY, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

JINGLES, EFFECTS = (4, 5, 6), tuple(range(7, 15))
TICKS = {  # every subtune's whole horizon (docs/certificates/galway-comic-bakery.json)
    1: 9450,
    2: 9450,
    3: 9450,
    4: 112,
    5: 138,
    6: 577,
    7: 26,
    8: 21,
    9: 101,
    10: 356,
    11: 31,
    12: 121,
    13: 31,
    14: 47,
}
TOTAL = 29911
TESTPULSE = [1, 0, 1]  # voice 1's copy names its own base where the others name base+2
MAIN = 1  # the main theme, and the only subtune whose loops nest
NEST_BY = 3200  # the tick by which the main theme opens a loop inside a loop


@lru_cache(maxsize=None)
def built(song):
    """One reading per worker: the object, and the oracle's writes beside it."""
    return TG.build(str(tune_file(COMIC_BAKERY)), song, TICKS[song])


@pytest.mark.parametrize("song", sorted(TICKS))
def test_every_subtune_certifies_over_its_whole_horizon(song):
    obj, writes = built(song)
    doc = attest(obj, writes)
    assert doc["divergence"] is None and doc["ticks"] == TICKS[song]
    assert doc["same_per_register_order"]  # never a value or a count, only an interleave
    assert doc["permuted_ticks"] + doc["identical_ticks"] == TICKS[song]


def test_the_horizons_are_the_whole_certified_set():
    assert sum(TICKS.values()) == TOTAL and sorted(TICKS) == list(TG.SONGS)


def deepest(obj, ticks):
    """How far the render's own loop stack goes over ``ticks``."""
    player, deep = Player(obj), 0
    for _ in range(ticks):
        player.tick()
        deep = max(deep, max(len(x) for x in player.loopstack))
    return deep


def test_the_counted_loops_nest_because_the_score_pushes_them():
    """The one form this family forced: ``mark``/``loop`` over a stack, not a cell."""
    obj = built(MAIN)[0]
    marks = [
        step
        for order in obj["score"]["orders"]
        for step in order["play"]
        if isinstance(step["op"], dict) and "mark" in step["op"]
    ]
    assert marks and deepest(obj, NEST_BY) > 1


def test_a_stop_ends_the_sequencer_and_not_the_voice():
    """The jingles end their tracks and their engines play the notes out."""
    obj, writes = built(JINGLES[0])
    assert obj["meta"]["stop"] == "sequencer"
    stops = [
        step for order in obj["score"]["orders"] for step in order["play"] if step["op"] == "stop"
    ]
    got = render(obj, TICKS[JINGLES[0]])
    assert stops and got[-1] == [tuple(x) for x in writes[-1]]


@pytest.mark.parametrize("song", EFFECTS)
def test_an_effect_subtune_is_the_engine_with_no_score_at_all(song):
    obj = built(song)[0]
    assert obj["state0"]["stopped"] == [True] * 3
    assert not any(o["play"] for o in obj["score"]["orders"])
    assert not obj["score"]["patterns"] and not obj["pitch"]["freq"][1:]


def test_the_test_bit_pulse_is_a_datum_because_one_copy_differs():
    """Voice 1's unrolled copy sends ``wave|8`` to its own ``pw_lo``, not its ``ctrl``."""
    obj = built(MAIN)[0]
    assert obj["state0"]["cells"]["testpulse"] == TESTPULSE
    pulse = [r for r in obj["streams"]["note_on"]["rows"] if "wave_test" in str(r["sets"])]
    assert [r["sets"][0][0] for r in pulse] == ["ctrl", "pw_lo"]


def test_the_loads_do_not_survive_the_score():
    """Section 6: a ``Moke`` builds the record a note copies, so it is the instrument."""
    obj = built(MAIN)[0]
    assert len(obj["instruments"]) > 1
    for name, cmd in obj["score"]["commands"].items():
        assert name.split(":")[0].split(".")[0] in obj["state0"]["cells"]
        assert len(cmd["rows"]) == 1 and "sets" in cmd["rows"][0]


def test_the_engine_residue_is_the_initial_state():
    """Init clears neither S nor D, so the image's own bytes are state0."""
    obj = built(MAIN)[0]
    cells = obj["state0"]["cells"]
    assert any(cells["vrc"]) or any(cells["fmc"]) or any(cells["fcurr"])
    zeroed = {k: ([0] * 3 if k != "testpulse" else v) for k, v in cells.items()}
    poisoned = dict(obj, state0=dict(obj["state0"], cells=zeroed))
    assert render(poisoned, 64) != render(obj, 64)


def test_the_silence_note_is_a_sound_with_no_pitch():
    """``$5E`` keys the instrument, and the tuning's own entry for it is its pitch."""
    obj = built(MAIN)[0]
    silence = {rec["pitch"]["value"]["const"] for rec in obj["instruments"].values()}
    assert silence == {0}  # entry $5E of the tune's own table, read and not assumed
    assert not [
        e
        for pat in obj["score"]["patterns"].values()
        for e in pat["events"]
        if e["sounds"] and e["note"] is None
    ]


def test_the_print_renders_every_subtune():
    for song in (MAIN, JINGLES[0], EFFECTS[0]):
        n = printer.numbers(printer.render(built(song)[0]))
        assert n["data_rows"] == n["statements"] > 0
