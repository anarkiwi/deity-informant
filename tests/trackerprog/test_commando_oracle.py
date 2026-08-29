"""Commando as a hand-written trackerprog: the oracle reference tune.

``tools/trackerprog_commando.py`` states the certified Commando tuneprog in
prototype-trackerprog.md's own vocabulary -- pitch, instruments, streams,
bounded accumulators, a score of events -- and
:mod:`deity_informant.trackerprog.universal` renders it.  The claim these tests
make is the section 2 certificate: 0 divergences over each subtune's whole
horizon, against the tune's own player on the PcodeVM.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402

import trackerprog_commando as TC  # noqa: E402
from _hvsc import COMMANDO, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

HORIZON = 11780  # the horizon docs/certificates/commando-song1.json records
SHAPE = {0: (9, 31, 570), 1: (4, 10, 118), 2: (1, 4, 61)}


def built(song):
    return TC.build(str(tune_file(COMMANDO)), song)


@pytest.mark.parametrize("song", (0, 1, 2))
def test_every_commando_subtune_certifies_on_the_universal_player(song):
    obj = built(song)
    doc = attest(obj, TC.reference(str(tune_file(COMMANDO)), song, HORIZON))
    assert doc["divergence"] is None
    assert doc["ticks"] == HORIZON
    assert doc["compared"] and doc["dropped"]
    # stronger than section 2, and free: the two sides differ only by the
    # interleave of registers inside a tick, never by a value or a count
    assert doc["same_per_register_order"]


@pytest.mark.parametrize("song", (0, 1, 2))
def test_the_object_is_the_tune_and_no_more(song):
    obj = built(song)
    ins, pats, events = SHAPE[song]
    assert len(obj["instruments"]) == ins  # 13 in the file; the subtune reaches these
    assert len(obj["score"]["patterns"]) == pats
    assert sum(len(p) for p in obj["score"]["patterns"].values()) == events
    assert set(obj["accs"]) == {
        "vibrato",
        "pulse_run",
        "pulse_bounce",
        "slide",
        "drum",
        "skydive",
        "arpeggio",
    }
    assert obj["meta"]["commit_order"] == ["ctrl", "ad", "sr"]


def test_the_note_space_is_bounded_and_the_escapes_are_generators():
    """commando-floor section 5: the overrun is load-bearing, 25 notes' worth.

    The table never runs off its end.  Where a transposition would have left it,
    a column names a generator with its own private state.
    """
    obj = built(0)
    p = obj["pitch"]
    assert len(p["freq"]) == len(p["notes"]) == 70
    assert max(p["notes"]) == 104 and min(p["notes"]) == 16
    gens = set()

    def walk(x):
        if isinstance(x, dict):
            if "gen" in x:
                gens.add(x["gen"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk([p["freq"], p["step"], p["octave"]])
    assert gens == set(obj["generators"])
    assert len(gens) == 8
    for g in obj["generators"].values():
        assert set(g["value"]) == {"u16"}
        for sub in g["on"]:
            assert sub["event"] in ("sound", "note", "instrument", "order", "row", "wrap", "turn")
    # subtunes 2 and 3 never leave the table, so they carry no generator at all
    assert built(1)["generators"] == {} and built(2)["generators"] == {}


def test_no_expression_reads_another_voices_state():
    """The invariant a generator exists to keep."""
    obj = built(0)
    seen = []

    def walk(x):
        if isinstance(x, dict):
            if "cell" in x:
                seen.append(x["cell"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj["accs"])
    walk(obj["streams"])
    walk(obj["generators"])
    assert seen and all(isinstance(c, str) for c in seen)  # never [name, voice]


def test_the_inherited_carry_is_load_bearing():
    """Section 5's ``+ carry(site, flag)``: delete it and subtune 2 diverges."""
    obj = built(1)
    ref = TC.reference(str(tune_file(COMMANDO)), 1, 400)
    assert attest(obj, ref)["divergence"] is None
    obj["accs"]["pulse_run"]["delta"] = {"const": "delta"}
    assert attest(obj, ref)["divergence"] is not None


def test_the_skydive_arm_is_never_taken():
    """The print's ``trap 'untaken'`` carried into the object, and re-checked."""
    obj = built(0)
    assert obj["accs"]["skydive"]["trap"] is True
    assert any(a["acc"] == "skydive" for i in obj["instruments"].values() for a in i["accs"])
    render(obj, 2000)  # the trap raises where the arm is taken


@pytest.mark.parametrize("song", (0, 1, 2))
def test_the_print_is_flat_and_round_trips_the_object_by_eye(song):
    """The flattened form: one fact per line, every section, no JSON."""
    text = printer.render(built(song))
    assert "{" not in text and '"' not in text  # nothing of the serialisation shows
    n = printer.numbers(text)
    assert n["lines"] == n["header_rows"] + n["data_rows"]
    assert n["blocks"] == 8 - (0 if built(song)["generators"] else 1)
    assert n["xz"] < 4644  # the source tuneprog.md's own xz -9e
