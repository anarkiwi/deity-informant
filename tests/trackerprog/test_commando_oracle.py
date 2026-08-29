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
    """A base note and a contiguous run of frequencies: the tune's whole tuning."""
    for song in (0, 1, 2):
        p = built(song)["pitch"]
        assert set(p) == {"base", "freq"}
        assert p["base"] == 16 and len(p["freq"]) == 80  # notes 16..95, every subtune
        assert all(isinstance(f, int) for f in p["freq"])


def test_the_modulators_are_expressions_not_tables():
    """The vibrato's interval and the arpeggio's octave are read, not tabulated."""
    obj = built(0)
    vib, arp = obj["accs"]["vibrato"], obj["accs"]["arpeggio"]
    assert "interval" not in vib and "octave" not in arp
    assert vib["delta"]["repeat"][0]["shr"][0]["sub"] == [
        {"noteword": {"add": [{"cell": "note"}, 1]}},
        {"noteword": {"cell": "note"}},
    ]
    assert arp["policy"]["reload"]["noteword"]["add"][0] == {"cell": "note"}
    assert obj["streams"]["arp"]["rows"] == [0, 12]  # semitone offsets, not note rows
    # no instrument carries a per-note record of any kind
    assert all(
        set(i) == {"adsr", "wave", "pw", "prelude", "accs"} for i in obj["instruments"].values()
    )


def test_one_source_covers_every_index_past_the_tuning():
    """commando-floor section 5: the overrun is load-bearing, 25 notes' worth.

    It is one generator indexed by position -- not by note -- so the same source
    serves every subtune and a different melody reads it unchanged.
    """
    for song in (0, 1, 2):
        obj = built(song)
        assert set(obj["generators"]) == {"past_tuning"}
        g = obj["generators"]["past_tuning"]
        assert g["base"] == 96 and len(g["words"]) == 21  # indices 96..116
        assert all("u16" in w or "trap" in w for w in g["words"])
        assert all(w["trap"] for w in g["words"] if "trap" in w)  # every trap says why
        assert {s["event"] for s in g["on"]} <= {
            "sound",
            "note",
            "instrument",
            "order",
            "row",
            "turn",
        }
        assert all("set" in s for s in g["on"])  # a source mirrors; it never counts
    # every subtune carries the same source: it is the tune's, not the melody's
    assert built(0)["generators"] == built(1)["generators"] == built(2)["generators"]


def test_the_score_carries_note_numbers_and_the_drum_index_is_one_of_them():
    """104 is no pitch, and the tuning does not pretend otherwise: it stops at 95."""
    obj = built(0)
    events = [e for p in obj["score"]["patterns"].values() for e in p["events"]]
    notes = {e["note"] for e in events if e["note"] is not None}
    assert max(notes) == 104 and 104 > obj["pitch"]["base"] + len(obj["pitch"]["freq"]) - 1
    # commando-floor section 5: "song 1 plays pitch 104 twenty-five times"
    assert sum(1 for e in events if e["note"] == 104) == 25


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
