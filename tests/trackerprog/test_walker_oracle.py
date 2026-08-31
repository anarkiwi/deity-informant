"""Chameleon as a hand-written trackerprog: the family that is four modulators.

``tools/trackerprog_walker.py`` states Martin Walker's Chameleon player in
prototype-trackerprog.md's vocabulary; the claim is section 2's certificate,
over the whole 8,052-tick horizon of the tune's own certificate
(docs/prototype-walker-trackerprog.md).  The tune's score is the C64 keyboard
the author typed it on and its engine is one modulator template unrolled four
times, so what this suite checks beside the render is that neither survives as
a family construct: the keys are semitones and the four copies are one record.
"""

import lzma
import sys
from functools import lru_cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402

import trackerprog_walker as TW  # noqa: E402
from _hvsc import CHAMELEON, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

BLOCKS, STEPS = 11, 32  # the blocks the song plays, and the steps that play them
ROWS = 1134  # 2,592 played rows, stated once per block instead of once per step
TAIL = 30  # the rows the clock still turns after the song has parked itself
TUNING = (16, 56)  # the semitone span the horizon asks the tuning for
SOURCE_XZ = 11176  # ``xz -9e`` of the source tuneprog.md the object was read from
RANDOM_VOICE = 2  # the residue leaves one modulator's period at $FF, on voice 3
MODS = ("mod1", "mod2", "mod3", "filter")


@lru_cache(maxsize=None)
def built():
    """One reading per worker: the object, and the oracle's writes beside it."""
    return TW.build(str(tune_file(CHAMELEON)))


def test_the_build_certifies_over_its_whole_horizon():
    obj, writes = built()
    doc = attest(obj, writes)
    assert doc["divergence"] is None and doc["ticks"] == TW.TICKS
    # the tick runs its sequencer over all three voices and then its engine, so
    # the writes are permuted between voices and never inside one
    assert (
        doc["same_per_register_order"]
        and doc["permuted_ticks"] + doc["identical_ticks"] == TW.TICKS
    )


def test_the_keyboard_does_not_survive_the_score():
    """Section 6: the token grammar is table membership, so the table is storage."""
    obj = built()[0]
    assert "keys" not in obj["streams"] and "keyboard" not in obj["streams"]
    for name, pat in obj["score"]["patterns"].items():
        assert {e["dur"] for e in pat["events"]} == {1}, name
        for e in pat["events"]:
            drum = e["ins"] is not None and e["ins"] >= TW.DRUM_BASE
            assert (e["note"] is None) == (not e["sounds"] or drum), name


def test_one_pattern_per_block_carries_every_step_that_plays_it():
    """A block header re-arms all three voices, so a block's rows never differ."""
    obj = built()[0]
    named = {k for k in obj["score"]["patterns"] if not k.startswith("tail")}
    assert len({k.split(".")[0] for k in named}) == BLOCKS
    play = [x["pattern"] for x in obj["score"]["orders"][0]["play"]]
    assert len(play) == STEPS + 1 and play[-1] == "tail.0"
    assert len(set(play)) < len(play)  # the order plays blocks more than once
    assert sum(len(p["events"]) for p in obj["score"]["patterns"].values()) == ROWS
    assert len(obj["score"]["patterns"]["tail.0"]["events"]) == TAIL


def test_the_four_modulators_are_one_record_with_four_sets_of_operands():
    obj = built()[0]
    shapes = {m: sorted(obj["accs"][m]) for m in MODS}
    assert shapes["mod1"] == shapes["mod2"]
    assert shapes["mod3"] == shapes["filter"]  # the two that also carry a halt
    assert set(shapes["mod3"]) - set(shapes["mod1"]) == {"delta_when"}
    deltas = [obj["accs"][m]["delta"]["const"] for m in MODS]
    assert deltas == [0x0A, 0x10, 0x50, 0x02]  # the four bytes of RAM at $AD73


def test_the_turn_is_counted_because_two_modulators_share_one_cell():
    """A bound on the value cannot turn a triangle the value is not only its own."""
    obj = built()[0]
    assert obj["accs"]["mod1"]["cell"] == obj["accs"]["mod3"]["cell"] == "freqoff"
    for m in MODS:
        am = obj["accs"][m]["amplitude"]
        assert "interval" not in am and am["count"] and am["cell"]
        assert obj["accs"][m]["policy"] == "reflect"


def test_the_filter_is_the_fourth_copy_on_the_global_channel():
    obj = built()[0]
    assert obj["globals"]["after"] == ["filterclock", "filtermod"]
    f = obj["accs"]["filter"]
    assert f["scope"] == "global" and f["cell"][0] == "#"
    assert obj["state0"]["gcursors"]["filtermod"]["row"] == 1


def test_the_engine_residue_is_the_initial_state():
    """Init clears page 2 and not the engine, so the image's bytes are state0."""
    obj = built()[0]
    cells = obj["state0"]["cells"]
    assert cells["m3period"][RANDOM_VOICE] == 0xFF  # the one volatile arm the tune takes
    assert cells["m4rate"][RANDOM_VOICE] and not any(cells["m4rate"][:RANDOM_VOICE])
    assert any(cells["freqbase"]) and any(cells["freqoff"])
    zeroed = {k: [0] * 3 for k in cells}
    zeroed["cnt"] = cells["cnt"]
    poisoned = dict(obj, state0=dict(obj["state0"], cells=zeroed))
    assert render(poisoned, 8) != render(obj, 8)


def test_the_one_pinned_input_is_eight_rows_and_no_more():
    """``$D41B`` is the family's single stated boundary, and it lands in an offset."""
    obj, _ = built()
    rows = obj["streams"]["noise"]["rows"]
    assert 0 < len(rows) <= 8
    assert all(r["word"] >> 8 == r["word"] & 0xFF for r in rows)  # both halves, one byte


def test_the_tuning_is_the_span_the_horizon_asks_for():
    obj = built()[0]
    assert (obj["pitch"]["base"], len(obj["pitch"]["freq"])) == TUNING


def test_the_print_compresses_below_the_program_that_played_it():
    text = printer.render(built()[0])
    n = printer.numbers(text)
    assert n["data_rows"] == n["statements"] > 0
    assert len(lzma.compress(text.encode(), preset=9 | lzma.PRESET_EXTREME)) < SOURCE_XZ
