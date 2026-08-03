"""framelog: canonical projection, digi rule, diff, text round-trip, adapter."""

from pathlib import Path

import pytest

from deity_informant import framelog as F

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def test_absolute_and_offset_regs_normalize_identically():
    assert F.canonical([[(0xD400, 1), (0xD418, 0x0F)]]) == F.canonical([[(0x00, 1), (0x18, 0x0F)]])


@pytest.mark.parametrize("reg", [-1, 0x1D, 0xD3FF, 0xD41D, 0x10000])
def test_non_sid_register_rejected(reg):
    with pytest.raises(ValueError):
        F.canonical([[(reg, 0)]])


@pytest.mark.parametrize("val", [-1, 0x100])
def test_non_byte_value_rejected(val):
    with pytest.raises(ValueError):
        F.canonical([[(0x00, val)]])


def test_freq_pw_last_write_wins_and_the_other_lane_comes_with_it():
    """Freq/PW are 16-bit: a lane write reports the word, the other lane as held."""
    (rec,) = F.canonical([[(0x00, 0x11), (0x00, 0x22), (0x03, 0x08), (0x08, 0x40)]])
    assert rec[0] == ((0x00, 0x22), (0x01, 0x00), (0x02, 0x00), (0x03, 0x08))
    assert rec[2] == ((0x07, 0x00), (0x08, 0x40))  # v1 freq: hi written, lo held
    assert rec[4] == ()  # v2 untouched: still elided, neither lane written


def test_a_held_lane_is_the_value_the_seed_and_earlier_frames_left():
    """The unwritten lane reports what it holds, not zero, once anything set it."""
    a, b = F.canonical([[(0x00, 0x11)], [(0x01, 0x02)]], {0x01: 0x09})
    assert a[0] == ((0x00, 0x11), (0x01, 0x09))  # the seed supplies the hi lane
    assert b[0] == ((0x00, 0x11), (0x01, 0x02))  # frame 0's lo lane carries forward


def test_ctrl_ad_sr_original_order_preserved_incl_double_ctrl():
    writes = [(0x05, 0x0F), (0x04, 0x41), (0x06, 0xF0), (0x04, 0x40), (0x04, 0x41)]
    (rec,) = F.canonical([writes])
    assert rec[1] == tuple(writes)  # hard-restart double ctrl kept verbatim


def test_voice_sections_do_not_interleave():
    writes = [(0x0B, 0x41), (0x04, 0x11), (0x12, 0x81), (0x0B, 0x40)]
    (rec,) = F.canonical([writes])
    assert rec[1] == ((0x04, 0x11),)
    assert rec[3] == ((0x0B, 0x41), (0x0B, 0x40))
    assert rec[5] == ((0x12, 0x81),)


def test_filter_tail_last_write_wins():
    """Cutoff is the tail's only 16-bit register; $17/$18 stay byte-wide."""
    (rec,) = F.canonical([[(0x18, 0x0F), (0x15, 0x03), (0x18, 0x1F), (0x17, 0xF1)]])
    assert rec[6] == ((0x15, 0x03), (0x16, 0x00), (0x17, 0xF1), (0x18, 0x1F))


def test_readonly_residual_class_ordered():
    (rec,) = F.canonical([[(0x1B, 0x01), (0x19, 0x02), (0x1B, 0x03)]])
    assert rec[7] == ((0x1B, 0x01), (0x19, 0x02), (0x1B, 0x03))


def test_dumps_loads_round_trip():
    frames = [
        [(0xD400, 0x1A), (0x04, 0x41), (0x05, 0x00), (0x04, 0x40), (0x18, 0x0F)],
        [],
        [(0x0E, 0xFF), (0x0F, 0x10), (0x16, 0x80), (0x1C, 0x7F)],
    ]
    text = F.dumps(frames)
    assert len(text.splitlines()) == 3
    assert F.loads(text) == F.canonical(frames)
    assert F.dumps([]) == "" and F.loads("") == []


def test_loads_rejects_malformed_line():
    with pytest.raises(ValueError):
        F.loads("00=1A|04=41")


def test_digi_rule_clean_and_borderline_and_flagged():
    clean = [[(0x18, 0x0F)], [(0x18, 0x0F), (0x18, 0x0F), (0x18, 0xFF)]]
    assert F.digi_frames(clean) == []  # dup nibbles collapse (high nibble ignored)
    two_step = [[(0x18, 0x0F), (0x18, 0x00)]]
    assert F.digi_frames(two_step) == []  # exactly-2-step borderline stays clean
    three_step = [[(0x18, 0x0F)], [(0x18, 0x0F), (0x18, 0x00), (0x18, 0x0F)]]
    assert F.digi_frames(three_step) == [(1, (0x0F, 0x00, 0x0F))]


def test_diff_none_on_equal_and_first_divergence():
    a = [[(0x00, 1), (0x04, 0x41), (0x04, 0x40)], [(0x18, 0x0F)]]
    b = [[(0x00, 1), (0x04, 0x41), (0x04, 0x44)], [(0x18, 0x0E)]]
    ca, cb = F.canonical(a), F.canonical(b)
    assert F.diff(ca, ca) is None
    assert F.diff(ca, cb) == F.Divergence(0, "v0.ord", 1, (0x04, 0x40), (0x04, 0x44))
    assert F.diff(ca, cb[:1]) == F.Divergence(0, "v0.ord", 1, (0x04, 0x40), (0x04, 0x44))
    assert F.diff(ca[:1], ca) == F.Divergence(1, None, None, None, ca[1])
    short = F.canonical([[(0x00, 1), (0x04, 0x41)]])
    assert F.diff(short, ca[:1]) == F.Divergence(0, "v0.ord", 1, None, (0x04, 0x40))


def test_frames_from_walker_duck_typed():
    class Fake:
        def __init__(self):
            self.wlog = []
            self._i = 0

        def run(self, frames):
            for _ in range(frames):
                self.wlog.append((self._i * 100, 0x04, self._i))
                self._i += 1

    frames = F.frames_from_walker(Fake(), 2)
    assert frames == [[(0x04, 0)], [(0x04, 1)]]


def _tunes():
    want = {"Commando", "Ghouls_n_Ghosts"}
    return [
        pytest.param(path, sub, secs, id="%s-%s" % (path.parent.name, path.stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem in want
    ]


@pytest.mark.parametrize("sid,subtune,secs", _tunes())
def test_real_tune_projection_deterministic_fp_clean(sid, subtune, secs):
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    nframes = int(secs * 50)
    model, _ev = S.decompile(mem, init, play, nframes, subtune)
    a = F.frames_from_walker(S.Walker(model), nframes)
    b = F.frames_from_walker(S.Walker(model), nframes)
    ca, cb = F.canonical(a), F.canonical(b)
    assert F.diff(ca, cb) is None and ca == cb
    assert F.loads(F.dumps(a)) == ca
    assert F.digi_frames(a) == []
