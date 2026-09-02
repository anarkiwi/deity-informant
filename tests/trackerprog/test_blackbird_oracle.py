"""Quintessence as a hand-written trackerprog: the family whose score is compressed.

``tools/trackerprog_blackbird.py`` states lft's Blackbird in
prototype-trackerprog.md's vocabulary; the claim is section 2's certificate,
over the whole 10,426-tick horizon of the tune's own certificate
(docs/prototype-blackbird-trackerprog.md).  The tune ships one LZ stream and
three ring buffers, so what this suite checks beside the render is that none of
that survives: one event per row, no decompressor, no packed delay.
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

import trackerprog_blackbird as TB  # noqa: E402
from _hvsc import QUINTESSENCE, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

ROWS = 2085  # the horizon's row frames: 10,426 ticks of five frames, one over
TUNING = (36, 269)  # the quarter-semitone span the horizon asks the tuning for
TABLES = ((143, 111), (72, 59))  # (pitch, wave): bytes in the table, rows reached
SOURCE_XZ = 7956  # ``xz -9e`` of the source tuneprog.md the object was read from
RESTART = 2  # this build's hard-restart threshold, its three compare immediates agreeing


@lru_cache(maxsize=None)
def built():
    """One reading per worker: the object, and the oracle's writes beside it."""
    return TB.build(str(tune_file(QUINTESSENCE)))


def test_the_build_certifies_over_its_whole_horizon():
    obj, writes = built()
    doc = attest(obj, writes)
    assert doc["divergence"] is None and doc["ticks"] == TB.TICKS
    # the tick's passes run over all three voices before its audio engine does,
    # so the writes are permuted between voices and never inside one
    assert (
        doc["same_per_register_order"]
        and doc["permuted_ticks"] + doc["identical_ticks"] == TB.TICKS
    )


def test_the_compressed_stream_and_the_ring_buffers_do_not_survive():
    """Section 6: storage is materialised away, so every row is one row."""
    obj = built()[0]
    for v, pat in obj["score"]["patterns"].items():
        assert len(pat["events"]) == ROWS, v
        assert {e["dur"] for e in pat["events"]} == {1}
        assert not any(e["tie"] for e in pat["events"])
    assert [o["end"] for o in obj["score"]["orders"]] == ["horizon"] * 3


def test_the_hard_restart_is_the_pipeline_and_not_a_schedule():
    """Two frames early is where the second tokenizer pass falls, so it is a prelude."""
    obj = built()[0]
    assert obj["meta"]["tempo"]["early"][0] == [{"cell": "phase"}, "==", TB.EARLY_PHASE]
    assert obj["meta"]["tempo"]["fetch"] == [[{"cell": "phase"}, "==", TB.FETCH_PHASE]]
    for name, ins in obj["instruments"].items():
        assert ("prelude" in ins) == (int(name) >= RESTART)
    assert obj["streams"]["hard_restart"]["rows"][0]["sets"][0][0] == "sr"


def test_one_compare_sorts_the_instrument_table():
    """The exporter sorts the table so a threshold replaces a per-instrument flag."""
    obj = built()[0]
    restart = [int(k) for k, i in obj["instruments"].items() if i["restart"]]
    assert min(restart) == RESTART == TB.restart(TB.load(str(tune_file(QUINTESSENCE))))
    assert len(obj["instruments"]) == 15


def test_the_tuning_is_quarter_semitones_and_every_note_is_one():
    """Storage is an idiom: two overlapped byte arrays lift to one u16 row per quarter."""
    obj = built()[0]
    assert (obj["pitch"]["base"], len(obj["pitch"]["freq"])) == TUNING
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            assert e["note"] is None or e["note"] % TB.QUARTER == 0


def test_a_backward_jump_is_folded_into_the_row_that_lands_on_it():
    """A byte at or above $C0 is no control byte, so it never occupies a row."""
    obj = built()[0]
    for row in obj["streams"]["wave"]["rows"]:
        if "trap" in row:
            continue
        assert row["sets"][0][1]["and"][0]["const"] < 0xC0


def test_a_row_the_horizon_never_steps_on_is_a_trap_and_not_a_row():
    obj = built()[0]
    for name, (size, reached) in zip(("pitch", "wave"), TABLES):
        rows = obj["streams"][name]["rows"]
        assert len(rows) == size  # a byte offset is its own row, from the first
        assert sum(1 for r in rows if "trap" not in r) == reached


def test_the_print_compresses_below_the_program_that_played_it():
    text = printer.render(built()[0])
    n = printer.numbers(text)
    assert n["data_rows"] == n["statements"] > 0
    assert len(lzma.compress(text.encode(), preset=9 | lzma.PRESET_EXTREME)) < SOURCE_XZ
