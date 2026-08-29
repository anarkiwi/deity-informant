"""Hermetic snippet tests for the universal player: one per section 5 mechanism.

No tune, no HVSC: a small hand-written trackerprog per form -- the tempo
divider, the note row, the keyoff, the tie, the prelude, and each accumulator
delta/policy pair -- so every branch of
:mod:`deity_informant.trackerprog.universal` is exercised by data alone.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from deity_informant.trackerprog.attest import attest, subsequences_agree  # noqa: E402
from deity_informant.trackerprog.universal import Player, render  # noqa: E402

import trackerprog_commando as TC  # noqa: E402

CTRL, AD, SR, FLO, FHI, PLO, PHI = 4, 5, 6, 0, 1, 2, 3


def event(dur, note=None, ins=None, porta=None, gate="on", tie=False, nbytes=2):
    return {
        "dur": dur,
        "note": note,
        "ins": ins,
        "porta": porta,
        "gate": gate,
        "tie": tie,
        "bytes": nbytes,
    }


def obj(patterns, orders, instruments, pitch=None, rate=2):
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
            "tempo": {"rate": rate, "phase": 0},
            "row_consumes_tick": True,
            "note_row": "note_on",
            "score_acc": "slide",
        },
        "globals": {
            "mode_vol": 0x0F,
            "flags": {"C": {"default": {"bit": [{"cell": "ins"}, 5]}}},
            "init_writes": [],
            "stop_writes": [[4, 0], [24, 0x0F]],
        },
        "pitch": pitch or {str(k): {"const": 0x0100 * k + k} for k in range(1, 20)},
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
                "term": "halt",
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
                "term": "halt",
            },
            "arp": {"rows": [0, 12], "term": "jump", "kind": "pitch"},
        },
        "accs": TC.accs(),
        "instruments": instruments,
        "score": {"patterns": patterns, "orders": orders},
        "state0": {
            "ins": [0] * n,
            "wave": [0] * n,
            "pwdir": [0] * n,
            "dividers": {"pulse_bounce": [0] * n},
        },
    }


def ins(wave=0x41, ad=1, sr=2, pw=(0x10, 0x02), accs=()):
    return {
        "adsr": [ad, sr],
        "wave": wave,
        "pw": list(pw),
        "prelude": {"stream": "note_off", "early": 1},
        "accs": list(accs),
    }


def test_the_note_row_and_the_tempo_divider():
    o = obj({"1": [event(3, note=2, ins=0)]}, [{"play": [1], "end": "jump"}], {"0": ins()})
    w = render(o, 3)
    assert w[0] == [
        (FHI, 0x02),
        (FLO, 0x02),
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
        {"1": [event(0, note=2, ins=0), event(0, gate="off", nbytes=1)]},
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
    o["score"]["patterns"]["1"][0]["tie"] = True
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
        {"1": [event(9, note=2, ins=0, porta=0x84, nbytes=3)]},
        [{"play": [1], "end": "jump"}],
        {"0": ins()},
    )
    w = render(up, 4)
    assert (FLO, 0x06) in w[1] and (FHI, 0x02) in w[1]  # 0x0202 + 4
    dn = obj(
        {"1": [event(9, note=2, ins=0, porta=0x85, nbytes=3)]},
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


def test_a_pitch_entry_may_name_two_cells():
    pitch = {
        "2": {"cells": [{"cell": "wave", "voice": 0}, {"cell": "pwdir", "voice": 1}]},
        "3": {"const": 0x0303},
    }
    o = obj(
        {"1": [event(9, note=2, ins=0)]},
        [{"play": [1], "end": "jump"}, {"play": [1], "end": "jump"}],
        {"0": ins()},
        pitch=pitch,
    )
    w = render(o, 22)
    assert (FLO, 0x00) in w[0]  # tick 0: no wave has been latched yet
    assert (7 + FLO, 0x41) in w[20]  # the next fetch reads voice 0's live wave shadow


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
