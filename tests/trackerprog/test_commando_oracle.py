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
    # Stronger than section 2, and free where it holds: the two sides differ only
    # by the interleave of registers inside a tick.  Subtune 1 is the exception --
    # see test_the_only_intermediate_writes_that_differ_are_superseded.
    assert doc["same_per_register_order"] == (song != 0)


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


def test_no_note_number_outside_the_tuning_exists_anywhere():
    """The rule: a value that is not in the pitch table is not a pitch, so not a note."""
    for song in (0, 1, 2):
        obj = built(song)
        top = obj["pitch"]["base"] + len(obj["pitch"]["freq"]) - 1
        notes = [e["note"] for p in obj["score"]["patterns"].values() for e in p["events"]]
        assert all(n is None or obj["pitch"]["base"] <= n <= top for n in notes)
        for i in obj["instruments"].values():
            assert set(i.get("pitch", {})) <= {"state", "on", "value", "octave"}
        assert "generators" not in obj and "residue" not in obj


def test_the_modulators_are_expressions_and_own_what_they_do_past_the_tuning():
    """commando-floor section 5: the overrun is load-bearing, 25 notes' worth.

    It is not in the tuning and not a note.  The arpeggio's bound is the tuning
    and its behaviour there is its own, indexed by how far past it went.
    """
    obj = built(0)
    vib, arp = obj["accs"]["vibrato"], obj["accs"]["arpeggio"]
    assert vib["delta"]["repeat"][0] == {"shr": [{"interval": None}, "shift"]}
    assert arp["policy"]["reload"]["transpose"] == {
        "stream": ["arp", {"and": [{"cell": "counter"}, 1]}]
    }
    assert obj["streams"]["arp"]["rows"] == [0, 12]  # semitone offsets
    assert "beyond" not in vib  # the vibrato never asks for one; measured, not assumed
    b = arp["beyond"]
    assert len(b["words"]) == 12 and all("u16" in w or "trap" in w for w in b["words"])
    assert all(w["trap"] for w in b["words"] if "trap" in w)  # every trap says why
    assert {x["event"] for x in b["on"]} <= {
        "sound",
        "note",
        "instrument",
        "order",
        "row",
        "wrap",
        "turn",
    }
    # it mirrors what it is told, and counts for itself what the tune counts
    assert all(set(x) <= {"event", "voice", "acc", "set", "add"} for x in b["on"])
    assert {k for p in obj["score"]["patterns"].values() for k in p} == {"events"}
    assert built(0)["accs"]["arpeggio"]["beyond"] == built(1)["accs"]["arpeggio"]["beyond"]


def test_an_unpitched_instrument_carries_its_own_pitch_modulator():
    """104 was never a note: the drum's frequency is its instrument's, privately."""
    obj = built(0)
    drums = {k: i["pitch"] for k, i in obj["instruments"].items() if "pitch" in i}
    assert set(drums) == {"4", "7"}
    assert set(drums["4"]) == {"state", "on", "value"}  # no arpeggio, so no octave
    assert set(drums["7"]) == {"state", "on", "value", "octave"}
    assert all("interval" not in d for d in drums.values())  # no pitch, no semitone above
    events = [e for p in obj["score"]["patterns"].values() for e in p["events"]]
    # commando-floor section 5: "song 1 plays pitch 104 twenty-five times"
    assert sum(1 for e in events if e["note"] is None and e["sounds"]) == 25


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
    walk(obj["instruments"])
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
    assert n["blocks"] == 7
    assert n["xz"] < 4644  # the source tuneprog.md's own xz -9e


def test_the_only_intermediate_writes_that_differ_are_superseded():
    """What dropping the fabricated note costs, measured rather than asserted.

    An unpitched sound has no semitone above it, so a vibrato over it steps by
    nothing.  Hubbard's routine steps by whatever lies past his tuning instead,
    and writes it -- then the same instrument's arpeggio overwrites it in the
    same tick.  Section 2 drops that intermediate by design; so does the object.
    """
    ref = TC.reference(str(tune_file(COMMANDO)), 0, HORIZON)
    got = render(built(0), HORIZON)
    bad = []
    for want, mine in zip(ref, got):
        want = [tuple(x) for x in want]
        mine = [tuple(x) for x in mine]
        for r in {q for q, _ in want} | {q for q, _ in mine}:
            a = [v for q, v in want if q == r]
            b = [v for q, v in mine if q == r]
            if a != b:
                bad.append((r, a, b))
                break
    assert len(bad) == 105
    assert {r % 7 for r, _, _ in bad} == {0}  # freq_lo alone
    for _, a, b in bad:
        assert a[-1] == b[-1]  # the value the tick leaves is identical
        assert a[1:] == b[1:] and len(a) > 1  # only a write another write supersedes


def _canonical(events):
    """Section 3.6's event, as the layer states it after the note column is spent."""
    for e in events:
        assert set(e) == {"dur", "sounds", "tie", "gate", "note", "ins", "arm"}
        assert isinstance(e["sounds"], bool)
        if e["note"] is not None:  # a pitch is a pitch: a row with one sounds
            assert e["sounds"]
        if e["gate"] is not None:  # a gate statement is its own row, never a note's
            assert not e["sounds"] and e["gate"] in ("on", "off")


@pytest.mark.parametrize("song", (0, 1, 2))
def test_the_event_is_the_canonical_one(song):
    """The same shape GoatTracker 2 uses: a gate token this family does not have."""
    obj = built(song)
    events = [e for p in obj["score"]["patterns"].values() for e in p["events"]]
    _canonical(events)
    assert all(e["gate"] is None for e in events)  # the row byte's bit 6 is `sounds`
