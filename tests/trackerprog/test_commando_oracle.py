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


def test_the_pitch_tail_names_cells_the_player_already_holds():
    """commando-floor section 5: the overrun is load-bearing, 25 notes' worth."""
    obj = built(0)
    cells = {n: e["cells"] for n, e in obj["pitch"].items() if "cells" in e}
    assert set(cells) == {"97", "98", "100", "104", "105", "107", "116"}
    assert obj["pitch"]["96"] == {"const": 0x0700}  # the last const pair of the fusion
    named = {r["cell"] for pair in cells.values() for r in pair if "cell" in r}
    assert named <= {"orderpos", "patrow", "wave", "note", "ins", "pwdir", "voice_base"}


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
