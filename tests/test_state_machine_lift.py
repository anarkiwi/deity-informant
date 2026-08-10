"""The canonical end-to-end example, gated (docs/register-model-lift-impl.md).

The plan's stages MUST keep this pipeline green: playroutine -> decompile ->
e-graph minimize -> Z3-proved u16 folds -> role-typed state machine ->
frame-oracle equality."""

import re

import pytest

from deity_informant import roles
from examples.state_machine_lift import (
    FRAMES,
    SHADOW,
    TEST_BIT,
    VOICES,
    WAVEF,
    ZPV,
    adsr_before_gate,
    change_stream,
    classify_roles,
    framelog,
    grids_from_writes,
    minimized_wav,
    note_starts,
    oscillator_reset_frames,
    pipeline,
    pretty,
    render,
    restart_shape,
    sidplayfp_wav,
    sidtrace_stream,
    to_psid,
    image_end,
)


@pytest.fixture(scope="module", name="art")
def _art():
    return pipeline()


@pytest.fixture(scope="module", name="min_grids")
def _min_grids(art):
    return grids_from_writes(art["init_writes"], art["min_frames"])


def test_folds_all_proved(art):
    kinds = {p.split("(")[0] for p in art["proofs"]}
    assert kinds == {"forward_shadow", "pair_store", "pair_set", "advance"}
    got = set(art["proofs"])
    for b in ZPV:  # the three voices fold per voice, on their own cursor and note pair
        assert "pair_set(ptr_%04X)" % b in got
        assert "pair_store(zp_%02X,zp_%02X)" % (b + 4, b + 5) in got
        assert "advance(ptr_%04X,+2,nocarry)" % b in got
    assert any(p.endswith(",wide)") for p in got), "no observed page cross to fold"


def test_shadow_forwards_off_the_sid_path(art):
    """The RAM SID shadow is looked through: no sink reads it back."""
    fwd = [p for p in art["proofs"] if p.startswith("forward_shadow")]
    assert len(fwd) == 3 * VOICES  # ad, sr and ctrl per voice
    shadow = re.compile(r"m_0[0-9A-F]{3}")
    text = render(art["folded"], classify_roles(art["folded"]))
    assert not [n for n in shadow.findall(text) if SHADOW <= int(n[2:], 16) < SHADOW + 7 * VOICES]
    for v in range(1, VOICES + 1):
        assert "sid.v%d.ctrl = v%d_ctl" % (v, v) in text
        assert "sid.v%d.attack_decay = v%d_ad" % (v, v) in text
    assert "m_034" in art["eqlift_text"], "the emitter's own text keeps the read-back"


def test_minimized_matches_vm_frame_projection(art):
    assert framelog.canonical(art["min_frames"]) == framelog.canonical(art["orig_frames"])


def test_minimized_grid_matches_vm(art, min_grids):
    assert min_grids == art["orig_grids"]


def test_hard_restart_survives_minimization(art, min_grids):
    """Both orderings hold on both sides: ADSR before the gate in a frame, and
    ADSR-zero then TEST across the two frames before each attack."""
    assert adsr_before_gate(art["orig_frames"]) and adsr_before_gate(art["min_frames"])
    for v in range(VOICES):
        want = ((0, 0, WAVEF[v]), (0, 0, WAVEF[v] | TEST_BIT))
        shapes = restart_shape(art["orig_grids"], v)
        assert len(shapes) > 8 and all(s == want for s in shapes)
        assert restart_shape(min_grids, v) == shapes
        attacks, b = note_starts(art["orig_grids"], v), 7 * v
        reset = oscillator_reset_frames(art["orig_grids"], v)
        assert reset and all(f - 1 in reset for f in attacks if f)
        for i in reset:  # TEST is held for exactly one frame, over a zeroed envelope
            g = art["orig_grids"][i]
            assert (g[b + 5], g[b + 6], g[b + 4]) == (0, 0, WAVEF[v] | TEST_BIT)
            assert i + 1 == FRAMES or not art["orig_grids"][i + 1][b + 4] & TEST_BIT
        assert oscillator_reset_frames(min_grids, v) == reset
        assert note_starts(min_grids, v) == attacks


def test_roles_are_the_plan_s_own_and_the_field_line_is_the_dialect_s(art):
    """The example's roles are ``roles.ROLES`` and it spells them as the grammar does."""
    got = classify_roles(art["folded"])
    assert set(got.values()) <= set(roles.ROLES)
    text = render(art["folded"], got)
    by_voice = {pretty(n): r for n, r in got.items()}
    for v in range(1, VOICES + 1):
        assert by_voice["v%d_pos" % v] == "cursor"
        assert by_voice["v%d_dur" % v] == "counter"
        assert by_voice["v%d_phase" % v] == "accumulator"
        fields = ("note_lo", "note_hi", "vib", "ctl", "ad", "sr")
        assert {by_voice["v%d_%s" % (v, f)] for f in fields} == {"parameter"}
        assert "v%d_pos: cursor u16" % v in text and "v%d_dur: counter u8" % v in text
        assert "v%d_pos:u16 += 2" % v in text and "sid.v%d.freq:u16" % v in text


def test_independent_engine_grid(art):
    oracle_mod = pytest.importorskip("pysidtracker.oracle")
    psid = to_psid(art["mem"], image_end(art["labels"]))
    oracle = [g[:25] for g in oracle_mod.register_grid(psid, FRAMES)]
    assert oracle == art["orig_grids"]


@pytest.mark.oracle
def test_sidplayfp_sidtrace_oracle(art):
    pytest.importorskip("pysidtracker")
    try:
        stream = sidtrace_stream(art["mem"], art["labels"])
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip("sidtrace oracle unavailable: %s" % exc)
    if stream and stream[0] == (24, 0x0F):
        stream = stream[1:]
    mine = change_stream(art["init_writes"], art["min_frames"], volume=0x0F)
    n = min(len(stream), len(mine))
    assert n and mine[:n] == stream[:n]


@pytest.mark.oracle
def test_wav_renders(art, tmp_path):
    """The tune is audible: the minimized write stream and sidplayfp both render."""
    wave = pytest.importorskip("wave")
    pytest.importorskip("pysidtracker.audio")

    def seconds(path):
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()

    mine = minimized_wav(art, tmp_path / "minimized.wav")
    assert seconds(mine) > 10
    try:
        theirs = sidplayfp_wav(art["mem"], art["labels"], tmp_path / "tune.wav", seconds=12)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip("sidplayfp unavailable: %s" % exc)
    assert seconds(theirs) > 10
