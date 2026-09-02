"""Ghouls'n'Ghosts as a hand-written trackerprog: three builds of one player.

``tools/trackerprog_follin.py`` states each subtune's data in
prototype-trackerprog.md's vocabulary; the claim is section 2's certificate.
The three builds are the family's three shapes -- a song that stops, a song
that loops, and a sound effect that starts one voice of three -- and each
certifies over its whole horizon under the tool itself
(docs/prototype-follin-trackerprog.md section 3).  This suite renders the
prefix named in ``CLAIMS`` so the hvsc budget stays where it is.
"""

import sys
from functools import lru_cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import CHIP  # noqa: E402

import trackerprog_follin as TF  # noqa: E402
from _hvsc import GNG, tune_file  # noqa: E402

pytestmark = pytest.mark.hvsc

CERTS = Path(__file__).resolve().parent.parent.parent / "docs" / "certificates"
# subtune -> its certificate, the prefix this suite renders, and
#            (blocks, events, tuning, stream rows, cells)
CLAIMS = {
    0: ("ghouls-song01", 2000, (81, 993, 97, 55, 36)),
    6: ("ghouls-song07", 2000, (123, 883, 97, 55, 36)),
    20: ("ghouls-song21", 2000, (4, 46, 97, 55, 36)),
}
STREAMS = ["blip", "filter", "gate", "noteon", "pitchmod", "pulse", "rest", "vibrato"]
COMMANDS = tuple(range(0x80, 0x95))


def claim(song):
    """What the committed tuneprog certificate says: the horizon and how it ends."""
    return TF.claim(str(CERTS / (CLAIMS[song][0] + ".json")), song)


@lru_cache(maxsize=None)
def built(song):
    """One reading per subtune per worker: the object is a pure function of the image."""
    return TF.build(str(tune_file(GNG)), song)


@pytest.mark.parametrize("song", sorted(CLAIMS))
def test_each_build_certifies_on_the_universal_player(song):
    loop, ticks, _, end = claim(song)
    obj = built(song)
    prefix = CLAIMS[song][1] or ticks
    doc = attest(obj, TF.reference(str(tune_file(GNG)), song, prefix))
    assert doc["divergence"] is None and doc["ticks"] == prefix
    assert doc["identical_ticks"] == prefix  # write for write, not merely reduced
    assert (
        len(obj["score"]["patterns"]),
        sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
        len(obj["pitch"]["freq"]),
        sum(len(s["rows"]) for s in obj["streams"].values()),
        len(obj["state0"]["cells"]),
    ) == CLAIMS[song][2]
    if loop:
        assert TF.loop_holds(obj, loop)
    if end == "fixed_point" and prefix == ticks:
        assert TF.fixed_point(obj, ticks)


def test_the_score_is_a_program_and_not_an_orderlist_and_a_pattern():
    """One byte stream per voice is both, so its steps carry the control flow."""
    ops = [s["op"] for song in (0, 6) for o in built(song)["score"]["orders"] for s in o["play"]]
    kinds = {o if isinstance(o, str) else next(iter(o)) for o in ops}
    assert kinds == {"call", "ret", "mark", "loop", "jump", "stop"}
    assert all(o["ret"] != o["call"] for o in ops if not isinstance(o, str) and "call" in o)


def test_a_call_comes_back_to_the_step_after_the_bytes_it_read():
    """A ``$8A`` pushes the byte after its own operand, so its return is a step of its own."""
    for song in CLAIMS:
        for order in built(song)["score"]["orders"]:
            for i, s in enumerate(order["play"]):
                op = s["op"]
                if not isinstance(op, str) and "call" in op:
                    assert op.get("ret", i + 1) < len(order["play"])


def test_a_row_that_carries_a_command_does_not_spend_the_voice_s_tick():
    """The fetch is a walk: only a row with a length ends it (``row_ends_fetch``)."""
    obj = built(0)
    assert obj["meta"]["row_ends_fetch"] == [["dur", "!=", 0]]
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            assert set(e) == {"dur", "sounds", "tie", "gate", "note", "ins", "arm"}
            assert (e["dur"] == 0) == (e["arm"] is not None)
            assert (e["note"] is not None) == e["sounds"]
            assert e["dur"] == 0 or e["arm"] is None


def test_a_sound_effect_starts_the_voices_it_names_and_no_others():
    """A stopped voice has no program at all, not an empty one it keeps stepping."""
    obj = built(20)
    assert obj["state0"]["stopped"] == [True, False, False]
    assert [len(o["play"]) for o in obj["score"]["orders"]][0] == 0
    assert built(0)["state0"]["stopped"] == [False] * 3


def test_the_instrument_is_the_run_of_commands_before_a_note():
    """The family has no instrument table, so the object has one instrument and no accs."""
    obj = built(0)
    assert list(obj["instruments"]) == ["0"] and obj["instruments"]["0"] == {"accs": []}
    assert obj["accs"] == {} and obj["score"]["commands"] == {}
    assert sorted(obj["streams"]) == STREAMS


def test_a_raw_register_list_is_a_command_that_writes_the_chip_by_number():
    """``$85`` names its registers outright, across voices, and the object keeps them."""
    regs = set()
    for pat in built(0)["score"]["patterns"].values():
        for e in pat["events"]:
            for row in (e["arm"] or {}).get("rows", ()):
                regs |= {t for t, _ in row["sets"] if t in CHIP}
    assert regs and {r.split(".")[0] for r in regs} > {"v0", "v1", "v2"} - {"res_route"}


def test_no_number_outside_the_tuning_is_a_note():
    """97 entries is the tuning; the byte index reads past it, and that is not pitch."""
    obj = built(0)
    p = obj["pitch"]
    assert p["base"] == 0 and len(p["freq"]) == TF.NOTES
    for pat in obj["score"]["patterns"].values():
        for e in pat["events"]:
            if e["note"] is not None:
                assert 0 <= e["note"] < TF.NOTES
    for name in ("pitchmod", "noteon"):
        words = obj["streams"][name]["beyond"]["words"]
        assert len(words) == 0x100 - TF.NOTES
        assert all(list(w) == ["const"] for w in words)


def test_a_byte_the_grammar_has_no_command_for_is_refused():
    """Fail closed: nothing is emitted, and the byte and its address are named."""
    m = bytearray(0x10000)
    m[0x1000] = 0x95
    with pytest.raises(TF.Refused, match=r"\$95 at \$1000"):
        TF.blocks(m, 0x1000)


def test_the_print_carries_the_order_program_and_measures_itself():
    text = printer.render(built(0))
    assert "order 0 -- 34 steps, stop" in text
    for step in ("call ", "back at ", "mark ", "loop, else ", "ret", "stop"):
        assert step in text, step
    n = printer.numbers(text)
    assert n["data_rows"] == n["statements"] > 0
