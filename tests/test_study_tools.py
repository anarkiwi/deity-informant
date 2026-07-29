"""Study tools (tools/study): the universal-tracker prototype's pure functions.

Guards the u-code's behavioural detectors and the optimize pass. Corpus-driven
entry points (main/extract_*) need HVSC and stay out of hermetic CI.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "study"))

import uinventory  # noqa: E402  pylint: disable=wrong-import-position
import umap_svg  # noqa: E402  pylint: disable=wrong-import-position
import utune  # noqa: E402  pylint: disable=wrong-import-position


def _grid(rows):
    """25-register frames from {offset: value} dicts, persisting between frames."""
    st, out = [0] * 25, []
    for row in rows:
        for r, v in row.items():
            st[r] = v
        out.append(st.copy())
    return out


def _gated(n, freq, wave=0x40, ad=0x00, sr=0xF0, v=0):
    """n gate-on frames for voice v at a constant frequency."""
    b = 7 * v
    return [
        {b: freq & 0xFF, b + 1: freq >> 8, b + 4: wave | 1, b + 5: ad, b + 6: sr} for _ in range(n)
    ]


def test_note_token_rendering():
    """Semitone -> name; strings pass through; None is the rest token."""
    assert utune._nn(0) == "C-0" and utune._nn(13) == "C#1" and utune._nn(11) == "B-0"
    assert utune._nn(None) == ".." and utune._nn("===") == "==="
    assert utune._nn(-5).startswith("x")  # out-of-range renders as a raw marker
    assert utune._nn(200).startswith("x")


def test_wrap_groups_and_never_returns_empty():
    assert utune._wrap(["a", "b", "c"], 2) == ["a  b", "c"]
    assert utune._wrap([], 4) == ["(empty)"]


def test_optimize_dedups_programs_and_transpose_factors_patterns():
    """A pattern's identity is its notes; a pitch-shifted copy folds to one canonical."""
    tune = utune.Tune(
        stem="t",
        editor="test",
        pitch=[],
        orders=[],
        patterns={
            0: [(12, (1,)), (14, (1,)), (16, (2,))],
            1: [(24, (3,)), (26, (3,)), (28, (3,))],  # same shape, +12
            2: [(12, (1,)), (13, (1,)), (12, (1,))],  # a different shape
        },
        programs={1: (("ctrl=1",), "d"), 2: (("ctrl=1",), "d"), 3: (("ctrl=9",), "e")},
    )
    o = utune.optimize(tune)
    assert o.prog_map[1] == o.prog_map[2] != o.prog_map[3]  # identical signatures dedup
    assert o.exact == 3  # three distinct note-sequences before transposition
    assert len(o.canon_pat) == 2  # patterns 0 and 1 share one canonical shape
    assert o.inst[0][0] == o.inst[1][0]
    assert o.inst[0][1] == 12 and o.inst[1][1] == 24  # base note carries the transpose


def test_local_period_finds_the_tightest_confirmed_loop():
    """Three confirmed repeats are required, so noise does not register."""
    st = [(0x40, 0, 100), (0x40, 0, 200)] * 6
    assert utune._local_period(st, 8) == 2
    assert utune._local_period([(0x40, 0, i) for i in range(10)], 8) == 0


def test_onsets_are_gate_rising_edges_only():
    """Release (gate 1->0) is not a note; only 0->1 is."""
    grid = _grid(_gated(3, 1000) + [{4: 0x40}] + _gated(2, 1200))
    assert utune._onsets(grid, 0) == [4]  # frame 0 has no predecessor; 4 is the retrigger
    assert utune._onsets(_grid(_gated(5, 1000)), 0) == []  # continuous gate: one note, no edge


def test_program_phases_classify_cycle_and_ramp():
    """A repeating freq pattern is a CYCLE phase; a monotone pulse sweep is a RAMP."""
    st = [(0x40, 100 + 4 * i, 1000) for i in range(12)]
    disp, sig = utune._program(st, 0, 12)
    assert "pw ramp" in disp and sig[0][0] == "pw"
    cyc = [(0x40, 500, 1000), (0x40, 500, 2000)] * 6
    disp2, sig2 = utune._program(cyc, 0, 12)
    # the leading frames precede three confirmed repeats, so the cycle starts mid-run
    assert "cycle" in disp2 and any(s[0] == "cyc" for s in sig2)


def test_program_bank_keys_instruments_by_note_on_identity():
    """Two notes with the same (waveform, AD, SR) are one instrument, not two."""
    rows = _gated(6, 1000, ad=0x11, sr=0x22) + [{4: 0x40}] + _gated(6, 2000, ad=0x11, sr=0x22)
    rows += [{4: 0x40}] + _gated(6, 3000, ad=0x33, sr=0x44)
    key_id, progs = utune._program_bank(_grid(rows))
    assert len(key_id) == 2 and len(progs) == 2


def test_sync_reports_locked_interval_binds():
    """A voice held a fixed interval above another is reported as a bind."""
    rows = []
    for _ in range(40):
        rows.append({0: 0xE8, 1: 0x03, 4: 0x41, 7: 0xDC, 8: 0x05, 11: 0x41})  # 1000 and ~1498
    trig, binds = utune._sync(_grid(rows))
    assert any("fifth" in b for b in binds), binds  # 7 semitones, not "fourth +1oct"
    assert not any("oct" in b for b in binds)  # a fifth is within the octave
    assert isinstance(trig, list)


def test_uinventory_periodicity_and_monotone_detectors():
    """The arp/ramp detectors separate repetition from monotone motion."""
    assert uinventory._periodic([1, 2, 3, 1, 2, 3, 1, 2, 3])
    assert not uinventory._periodic([1, 1, 1, 1])  # constant: no distinct values
    assert not uinventory._periodic([1, 2])  # too short
    assert uinventory._monotone_run(list(range(0, 800, 100)), need=6, span=64)
    assert not uinventory._monotone_run([5, 5, 5, 5, 5, 5, 5], need=6, span=64)


def test_uinventory_pitch_lattice_needs_coverage_and_distinct_notes():
    """Gated freqs on an ET lattice count; too few distinct notes do not."""
    semis = [0, 2, 4, 5, 7, 9, 11, 12]
    rows = []
    for s in semis:
        rows += _gated(6, int(round(268 * 2 ** (s / 12))))
    assert uinventory._pitch_notes(_grid(rows)) >= 5
    assert uinventory._pitch_notes(_grid(_gated(40, 1000))) == 0  # one note only


def test_uinventory_detect_and_render_cover_the_archetype_matrix():
    """detect() fills the fixed archetype keys; both renderers emit every row."""
    rows = []
    for s in (0, 4, 7, 12, 16, 19):
        rows += _gated(8, int(round(268 * 2 ** (s / 12))))
    ev = uinventory.detect(_grid(rows))
    assert set(ev) == set(uinventory.KEY)
    assert ev[("DIV", "root frame", "tick")] == 1
    cols = [("synth", ev)]
    for text in (uinventory.render(cols), uinventory.render_counts(cols)):
        assert "synth" in text
        for xfer, trig, route, _meaning in uinventory.ARCHETYPES:
            assert xfer in text and trig in text and route in text


def test_umap_svg_builds_a_self_contained_document():
    """Every editor column and archetype row appears in the emitted SVG."""
    svg = umap_svg.build()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    for col in umap_svg.COLS:
        assert col in svg
    assert "xlink:href" not in svg and "<image" not in svg  # self-contained, no fetches


@pytest.mark.parametrize("bad", [np.zeros(4, dtype=np.int64), np.arange(1, 5, dtype=np.int64)])
def test_et_extent_rejects_non_chromatic_memory(bad):
    """The ET extension stops immediately when memory is not a chromatic run."""
    mem = bytearray(0x10000)
    for i, w in enumerate(bad):
        mem[0x1000 + i], mem[0x1100 + i] = int(w) & 0xFF, (int(w) >> 8) & 0xFF
    ext, words = utune._et_extent(bytes(mem), 0x1000, 0x1100, 2)
    assert ext == 2 and len(words) == 2
