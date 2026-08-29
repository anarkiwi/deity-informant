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
    assert sum(len(p["events"]) for p in obj["score"]["patterns"].values()) == events
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


def test_the_pitch_table_is_only_a_pitch_table():
    """Note numbers and frequencies, nothing else, and every entry a constant."""
    obj = built(0)
    p = obj["pitch"]
    assert set(p) == {"notes", "index", "freq"}
    assert len(p["freq"]) == len(p["notes"]) == 69
    assert all(isinstance(f, int) for f in p["freq"])
    assert max(p["notes"]) == 95 and min(p["notes"]) == 16  # 104 is no note


def test_the_modulators_keep_their_own_tables_over_the_tuning():
    """commando-floor section 5: the overrun is load-bearing, 25 notes' worth.

    It is not in the tuning.  The vibrato's interval and the arpeggio's octave
    are each that accumulator's own table, and where the tune's own arithmetic
    left the tuning the entry names a generator.
    """
    obj = built(0)
    n = len(obj["pitch"]["notes"])
    vib, arp = obj["accs"]["vibrato"], obj["accs"]["arpeggio"]
    assert len(vib["interval"]) == len(arp["octave"]) == n
    assert all(x is None or isinstance(x, int) for x in vib["interval"])  # all constants
    escapes = {
        obj["pitch"]["notes"][i]: e["gen"]
        for i, e in enumerate(arp["octave"])
        if isinstance(e, dict) and "gen" in e
    }
    assert set(escapes) == {85, 86, 88, 93, 95}
    assert set(escapes.values()) <= set(obj["generators"])
    assert built(1)["generators"] == {} and built(2)["generators"] == {}


def test_a_played_index_that_is_not_a_note_lives_on_its_instrument():
    """104 is the drum's seed, not a pitch: it is a record on instruments 4 and 7."""
    obj = built(0)
    seeded = {k: i["seed"] for k, i in obj["instruments"].items() if "seed" in i}
    assert set(seeded) == {"4", "7"}
    assert all(s["number"] == 104 for s in seeded.values())
    assert set(seeded["4"]) == {"number", "freq"}  # no vibrato, no arpeggio
    assert set(seeded["7"]) == {"number", "freq", "interval", "octave"}
    assert "104" not in obj["pitch"]["index"]
    events = [e for p in obj["score"]["patterns"].values() for e in p["events"]]
    # commando-floor section 5: "song 1 plays pitch 104 twenty-five times"
    assert sum(1 for e in events if e["note"] == "seed") == 25


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
