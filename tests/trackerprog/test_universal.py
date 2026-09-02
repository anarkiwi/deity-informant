"""Hermetic snippet tests for the universal player: one per section 5 mechanism.

No tune, no HVSC: a small hand-written trackerprog per form -- the tempo
divider, the note row, the keyoff, the tie, the prelude, and each accumulator
delta/policy pair -- so every branch of
:mod:`deity_informant.trackerprog.universal` is exercised by data alone.
"""

import pickle
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest, subsequences_agree  # noqa: E402
from deity_informant.trackerprog.universal import Player, render  # noqa: E402

import trackerprog_commando as TC  # noqa: E402

CTRL, AD, SR, FLO, FHI, PLO, PHI = 4, 5, 6, 0, 1, 2, 3


def event(dur, note=None, ins=None, slide=None, sounds=True, gate=None, tie=False):
    arm = (
        None
        if slide is None
        else {"arms": [{"acc": "slide", "delta": slide[0], "phase": slide[1]}]}
    )
    return {
        "dur": dur,
        "note": note,
        "ins": ins,
        "arm": arm,
        "sounds": sounds,
        "gate": gate,
        "tie": tie,
    }


def pat(events):
    return {"events": events}


def tuning(hi=20):
    """A pitch table: a base note and a contiguous run of frequencies."""
    return {"base": 1, "freq": [0x0101 * k for k in range(1, hi)]}


def obj(patterns, orders, instruments, pitch=None, rate=2, beyond=None, cells=None):
    """A one- or two-voice trackerprog over the same seven accumulator forms."""
    n = len(orders)
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": "hermetic",
            "family": "none",
            "song": 0,
            "voices": n,
            "cycles_per_tick": 19656,
            "voice_order": list(reversed(range(n))),
            "commit_order": ["ctrl", "ad", "sr"],
            "tempo": {
                "cell": "rowsleft",
                "step": -1,
                "rate": rate,
                "phase": 0,
                "boundary": [[{"cell": "rowsleft"}, ">=", 0x80]],
                "early": [[{"cell": "rowsleft"}, "==", 0], [{"cell": "tied"}, "==", 0]],
            },
            "tick": ["prelude", "commit", "row", "commit", "machine"],
            "row_consumes_tick": True,
            "row": [
                {"ins": True},
                {"note": True, "when": [["sounds", "!=", 0]]},
                {"sets": [["@wave", {"ins": "wave"}]]},
                {"stream": "note_on"},
                {"commands": True},
            ],
        },
        "globals": {
            "flags": {"C": {"default": {"bit": [{"cell": "ins"}, 5]}}},
            "stop_writes": [[4, 0], [24, 0x0F]],
        },
        "pitch": pitch or tuning(),
        "streams": {
            "note_on": {
                "rows": [
                    {
                        "sets": [
                            ["ctrl", {"and": [{"ins": "wave"}, "gate"]}],
                            ["pw_lo", {"cell": "pw_lo"}],
                            ["pw_hi", {"cell": "pw_hi"}],
                            ["ad", {"ins": "adsr.0"}],
                            ["sr", {"ins": "adsr.1"}],
                        ]
                    }
                ],
            },
            "note_off": {
                "rows": [
                    {
                        "sets": [
                            ["ctrl", {"and": [{"cell": "wave"}, 0xFE]}],
                            ["ad", {"const": 0}],
                            ["sr", {"const": 0}],
                        ]
                    }
                ],
            },
            "arp": {"rows": [0, 12], "kind": "pitch"},
        },
        "accs": _accs(beyond),
        "instruments": instruments,
        "score": {
            "patterns": {k: v if "events" in v else pat(v) for k, v in patterns.items()},
            "orders": orders,
        },
        "state0": {
            "ins": [0] * n,
            "wave": [0] * n,
            "cells": dict({"pwdir": [0] * n, "pwdelay": [0] * n}, **(cells or {})),
        },
    }


def _accs(beyond=None):
    a = TC.accs()
    if beyond:
        a["arpeggio"]["beyond"] = beyond
    return a


def ins(wave=0x41, ad=1, sr=2, pw=(0x10, 0x02), accs=(), pitch=None):
    rec = {
        "adsr": [ad, sr],
        "wave": wave,
        "pw": list(pw),
        "prelude": {"stream": "note_off", "early": 1},
        "on_note": [{"sets": [["freq", {"notefreq": None}]]}],
        "accs": list(accs),
    }
    if pitch:
        rec["pitch"] = pitch
    return rec


def beyond(words):
    """The arpeggio's own behaviour past the tuning."""
    return {"index": "how far past it the transposition went", "words": words}


def unpitched(value, **rest):
    """An instrument whose sound is no pitch: its own modulator, inline."""
    return dict({"value": value}, **rest)


# the byte cursor a source packs a pattern into, kept by the row program itself:
# one byte for the row and one more where it sounds, starting over at the wrap
PATROW = [
    {"sets": [["@patrow", {"add": [{"cell": "patrow"}, {"add": [{"const": 1}, "sounds"]}]}]]},
    {"sets": [["@patrow", {"const": 0}]], "when": [["wraps", "!=", 0]]},
]


def test_the_note_row_and_the_tempo_divider():
    o = obj({"1": [event(3, note=2, ins=0)]}, [{"play": [1], "end": "jump"}], {"0": ins()})
    w = render(o, 3)
    assert w[0] == [
        (FLO, 0x02),
        (FHI, 0x02),
        (PLO, 0x10),
        (PHI, 0x02),
        (CTRL, 0x41),
        (AD, 1),
        (SR, 2),
    ]
    assert w[1] == []  # rate 2: no row boundary, and the instrument arms nothing
    assert w[2] == []  # the row lasts dur + 1 boundaries


def test_a_keyoff_row_re_emits_the_instrument_with_the_gate_cleared():
    o = obj(
        {"1": [event(0, note=2, ins=0), event(0, sounds=False)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins()},
    )
    w = render(o, 4)
    assert w[2][-3:] == [(CTRL, 0x40), (AD, 1), (SR, 2)]  # wave & $FE, pitch untouched
    assert not any(r == FHI for r, _ in w[2])


def test_the_prelude_fires_a_row_tick_early_and_the_tie_disarms_it():
    cut = [(CTRL, 0x40), (AD, 0), (SR, 0)]
    o = obj(
        {"1": [event(1, note=2, ins=0), event(1, note=3)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins()},
    )
    assert render(o, 6)[2][:3] == cut
    o["score"]["patterns"]["1"]["events"][0]["tie"] = True
    assert render(o, 6)[2][:3] != cut


def test_the_order_program_jumps_and_stops():
    o = obj({"1": [event(0, note=2, ins=0)]}, [{"play": [1], "end": "jump"}], {"0": ins()})
    assert len(render(o, 8)) == 8 and render(o, 8)[6]  # the jump keeps playing
    o["score"]["orders"][0]["end"] = "stop"
    w = render(o, 8)
    assert w[2] == []  # the terminator abandons the tick it was read on
    assert w[3] == [(4, 0), (24, 0x0F)] and w[4] == [] and w[5] == []


def test_the_free_slide_is_a_field_delta_with_a_phase_bit():
    up = obj(
        {"1": [event(9, note=2, ins=0, slide=(4, 0))]},
        [{"play": [1], "end": "jump"}],
        {"0": ins()},
    )
    w = render(up, 4)
    assert (FLO, 0x06) in w[1] and (FHI, 0x02) in w[1]  # 0x0202 + 4
    dn = obj(
        {"1": [event(9, note=2, ins=0, slide=(4, 1))]},
        [{"play": [1], "end": "jump"}],
        {"0": ins()},
    )
    assert (FLO, 0xFE) in render(dn, 2)[1]  # 0x0202 - 4


def test_the_pulse_run_adds_a_constant_and_the_flag_it_is_given():
    o = obj(
        {"1": [event(9, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "pulse_run", "delta": 3}])},
    )
    w = render(o, 4)
    assert [v for r, v in w[1] if r == PLO] == [0x13]
    assert [v for r, v in w[2] if r == PLO] == [0x16]


def test_the_pulse_bounce_reflects_inside_its_projected_bound():
    o = obj(
        {"1": [event(31, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(pw=(0xC0, 0x0D), accs=[{"acc": "pulse_bounce", "delta": 0x60, "rate": 2}])},
    )
    p = Player(o)
    seen = []
    for _ in range(400):
        for r, v in p.tick():
            if r == PHI:
                seen.append(v)
    assert seen and set(seen) <= set(range(8, 15))  # never outside $8xx..$Exx
    assert 0x0E in seen and 0x08 in seen  # and it reaches both ends


def test_the_vibrato_is_a_reload_plus_a_closed_repeat_and_it_leaves_a_carry():
    o = obj(
        {"1": [event(9, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "vibrato", "shift": 1}, {"acc": "pulse_run", "delta": 0}])},
    )
    w = render(o, 9)
    base, step = 0x0202, (0x0303 - 0x0202) >> 1
    for t in (1, 2, 3, 4):
        ph = t & 7
        ph = ph ^ 7 if ph >= 4 else ph
        assert (FLO, (base + ph * step) & 0xFF) in w[t]
    # the flag the repeat leaves: 1 where the loop does not run (the fold is 0)
    assert [v for t in w for r, v in t if r == PLO] == [0x10] * 7 + [0x11, 0x12]


def test_the_vibrato_delta_is_guarded_by_the_row_length():
    short = obj(
        {"1": [event(5, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "vibrato", "shift": 1}])},
    )
    assert all((FLO, 0x02) in t for t in render(short, 6)[1:6:1] if t)


def test_the_drum_emits_the_entry_value_and_picks_its_gate_row():
    o = obj(
        {"1": [event(9, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "drum"}])},
    )
    w = render(o, 12)
    assert (CTRL, 0x80) in w[1]  # the note's first row tick
    assert [v for r, v in w[1] if r == FHI] == [0x02]
    assert (CTRL, 0x40) in w[2] and (FHI, 0x02) in w[2]  # writes, then steps
    assert (FHI, 0x01) in w[3] and (FHI, 0x00) not in w[4]  # the guard freq_hi != 0


def test_the_arpeggio_reads_a_pitch_stream_off_the_global_counter():
    o = obj(
        {"1": [event(9, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "arpeggio"}])},
    )
    w = render(o, 5)
    assert (FHI, 0x0E) in w[1] and (FLO, 0x0E) in w[1]  # note 2 + 12
    assert (FHI, 0x02) in w[2]


def test_a_trapped_arm_raises_where_it_is_taken():
    o = obj(
        {"1": [event(9, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "skydive"}])},
    )
    with pytest.raises(AssertionError):
        render(o, 4)


def test_an_unpitched_instrument_reads_the_cells_of_the_voices_it_names():
    """A sound whose frequency is no pitch: two cells, each of the voice it names."""
    p = unpitched(
        {"u16": [{"cell": ["wave", 0]}, {"cell": ["wave", 1]}]},
        octave={"u16": [{"const": 0x11}, {"const": 0x22}]},
    )
    o = obj(
        {"1": [event(9, ins=0)]},  # no note: the instrument sounds
        [{"play": [1], "end": "jump"}, {"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "arpeggio"}], pitch=p)},
    )
    pl = Player(o)
    w = [pl.tick() for _ in range(3)]
    assert pl.c["wave"] == [0x41, 0x41]  # the cells the word reads, on both voices
    assert (7 + FHI, 0x00) in w[0]  # at note on no voice has latched a wave yet
    assert (FHI, 0x22) in w[1] and (FLO, 0x11) in w[1]  # its own octave, for the arpeggio
    assert (FHI, 0x41) in w[2] and (FLO, 0x41) in w[2]  # and its own frequency on the other phase


def test_a_cell_read_names_the_voice_or_the_one_being_committed():
    """The two forms of one vocabulary, over the same cell of two voices."""
    p = unpitched({"u16": [{"cell": ["wave", 1]}, {"cell": "wave"}]})
    o = obj(
        {"1": [event(9, ins=0)], "2": [event(9, note=5, ins=1)]},
        [{"play": [1], "end": "jump"}, {"play": [2], "end": "jump"}],
        {"0": ins(pitch=p), "1": ins(wave=0x21)},
    )
    o["state0"]["wave"] = [0x55, 0]  # so the two halves cannot be the same read
    w = render(o, 2)
    # voice 0 reads voice 1's waveform for the low half and its own for the high
    assert (FLO, 0x21) in w[0] and (FHI, 0x55) in w[0]


def test_the_row_program_keeps_the_byte_cursor_the_score_no_longer_packs():
    """A packed cursor is a cell the row program counts, and the wrap starts it over."""
    o = obj(
        {"1": [event(0, note=2, ins=0), event(0, note=3), event(0, sounds=False)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins()},
        cells={"patrow": [0]},
    )
    o["meta"]["row"] += PATROW
    pl = Player(o)
    out = []
    for _ in range(8):
        pl.tick()
        out.append(pl.c["patrow"][0])
    # two bytes a sounding row, one the keyoff, and 0 where the cursor leaves the pattern
    assert out == [2, 2, 4, 4, 0, 0, 2, 2]


def test_the_arpeggio_owns_what_it_does_past_the_tuning():
    """The bound is the tuning; the behaviour at that bound is the modulator's own."""
    b = beyond([{"u16": [{"cell": ["wave", 0]}, {"const": 0x33}]}, {"trap": "the packed row byte"}])
    o = obj(
        {"1": [event(9, note=7, ins=0)]},  # 7 + 12 = 19, one past a tuning of 1..18
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "arpeggio"}])},
        pitch={"base": 1, "freq": [0x0101 * k for k in range(1, 19)]},
        beyond=b,
    )
    w = render(o, 2)
    assert (FHI, 0x33) in w[1] and (FLO, 0x41) in w[1]
    o["score"]["patterns"]["1"]["events"][0]["note"] = 8  # 8 + 12 = 20, the trapped one
    with pytest.raises(AssertionError, match="the packed row byte"):
        render(o, 2)
    o["score"]["patterns"]["1"]["events"][0]["note"] = 9  # past its own bound
    with pytest.raises(AssertionError, match="beyond its own bound"):
        render(o, 2)


def test_a_stored_player_comes_back_with_its_compiled_form_rebuilt():
    """The resume the two chunked certifications use: state pickles, closures do not."""
    o = obj({"1": [event(3, note=2, ins=0)]}, [{"play": [1], "end": "jump"}], {"0": ins()})
    pl = Player(o)
    first = [pl.tick() for _ in range(3)]
    stored = Player(o)
    stored.tick()
    assert "code" not in stored.__getstate__()  # the compiled form is not state
    back = pickle.loads(pickle.dumps(stored))
    assert [back.tick() for _ in range(2)] == first[1:]


def test_the_tuning_is_asked_only_for_notes_it_has():
    o = obj({"1": [event(3, note=99, ins=0)]}, [{"play": [1], "end": "jump"}], {"0": ins()})
    with pytest.raises(AssertionError, match="note 99 is outside the tuning"):
        render(o, 2)


def test_the_flattened_print_carries_every_section_and_measures_itself():
    p = unpitched({"u16": [{"cell": ["wave", 0]}, {"cell": ["wave", 1]}]})
    o = obj(
        {"1": [event(9, note=2, ins=0, slide=(4, 1)), event(0, sounds=False)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "vibrato", "shift": 1}, {"acc": "drum"}], pitch=p)},
        beyond=beyond([{"trap": "a cell the tick recomputes"}]),
    )
    text = printer.render(o)
    for section in (
        "## meta",
        "## pitch",
        "## streams",
        "## accumulators",
        "## instruments",
        "## score",
        "## initial state",
    ):
        assert section in text
    assert "## pitch -- the tuning: notes 1..19, and nothing else" in text
    assert "beyond  past the tuning, by how far past it the transposition went" in text
    assert "0  trap: a cell the tick recomputes" in text
    assert "pitch   this instrument's sound is no pitch; it is its own" in text
    assert "value     u16(wave[0], wave[1])" in text
    assert "repeat(interval >> <shift>, fold(counter, 7))" in text  # an expression
    assert "(dur - 1) & $FF >= rowsleft" in text  # nested binaries parenthesised
    assert "emits   the value the tick came in with" in text
    assert "     dur  snd  tie  gate   ins  note  arm" in text
    assert "slide(delta 4 phase 1)" in text  # the arm the row carries, materialised
    n = printer.numbers(text)
    assert set(n) == {"lines", "tokens", "statements", "blocks", "header_rows", "data_rows", "xz"}
    assert n["blocks"] == 7 and n["data_rows"] == n["statements"] > 0
    assert n["lines"] == n["header_rows"] + n["data_rows"]


def test_the_attestation_names_what_it_compares_and_what_it_drops():
    o = obj({"1": [event(3, note=2, ins=0)]}, [{"play": [1], "end": "jump"}], {"0": ins()})
    ref = render(o, 6)
    d = attest(o, ref)
    assert d["divergence"] is None and d["ticks"] == 6
    assert d["compared"] and d["dropped"] and d["same_per_register_order"]
    assert d["identical_ticks"] == 6 and d["permuted_ticks"] == 0
    bent = [list(t) for t in ref]
    bent[0] = [(CTRL, 0x7F)] + bent[0][:-1]
    d = attest(o, bent)
    assert d["divergence"]["tick"] == 0 and "edges" in d["divergence"]
    assert not subsequences_agree(bent, ref)


def test_an_unpitched_sound_has_no_interval_above_it():
    """A vibrato over something that is not a pitch steps by nothing."""
    p = unpitched({"u16": [{"const": 0x34}, {"const": 0x12}]})
    o = obj(
        {"1": [event(9, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(accs=[{"acc": "vibrato", "shift": 1}], pitch=p)},
    )
    w = render(o, 6)
    for t in w[1:]:
        assert [v for r, v in t if r == FHI] in ([], [0x12])  # never stepped
        assert [v for r, v in t if r == FLO] in ([], [0x34])


def test_no_note_number_outside_the_tuning_exists_anywhere():
    """The rule this object keeps: a value that is not a pitch is never a note."""
    p = unpitched({"u16": [{"const": 0x34}, {"const": 0x12}]})
    o = obj(
        {"1": [event(9, note=2, ins=0), event(3, ins=0)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins(pitch=p)},
    )
    top = o["pitch"]["base"] + len(o["pitch"]["freq"]) - 1
    notes = [e["note"] for x in o["score"]["patterns"].values() for e in x["events"]]
    assert all(n is None or o["pitch"]["base"] <= n <= top for n in notes)
    assert "latches" not in p and "number" not in p and "interval" not in p
