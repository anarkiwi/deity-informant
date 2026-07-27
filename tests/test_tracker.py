"""Universal tracker (M-T1): engine lift, note-lane inversion, Gate T."""

from pathlib import Path

import numpy as np
import pytest

from deity_informant import eqlift_annotate as ann
from deity_informant import structured as S
from deity_informant import tracker
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _tune(stem, parent):
    return [
        pytest.param(path, sub, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _model(sid, subtune, frames=200):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, frames, subtune)
    return model


def test_sparse_et_gapped_semitone_table():
    """A semitone-indexed ET table with interior rest zeros validates; noise fails."""
    full = np.round(268 * tracker._SEMI ** np.arange(48)).astype(np.int64)
    gapped = full.copy()
    gapped[[3, 4, 5, 13, 20, 21, 31]] = 0  # unused notes are zero in the image
    et = tracker._et_words(gapped)
    assert et is not None and len(et) == 48
    assert tracker._sparse_et(np.arange(1, 49) * 7) is None  # linear, not exponential
    assert tracker._sparse_et(np.zeros(48, dtype=np.int64)) is None


def test_segmented_and_longest_run():
    """Per-octave segments (zero-separated) confirm; longest interior run trims noise."""
    octave = np.round(268 * tracker._SEMI ** np.arange(14)).astype(np.int64)
    seg = np.concatenate([octave, [0, 0], octave * 2, [0, 0], octave * 4])
    assert tracker._segmented_et(seg) is not None
    assert tracker._segmented_et(np.arange(1, 60)) is None  # linear runs are not ET
    window = np.concatenate([[999, 3, 7], octave, octave[-1:] * 5, [123, 45]])
    run = tracker._longest_run(window[: 3 + 14].astype(np.int64), minrun=12)
    assert run is not None and len(run) == 14  # the interior chromatic octave


def test_lattice_et_geometric_layouts():
    """Descending-period, diatonic, and sparse ET tables confirm; pattern data fails."""
    ref = 268
    period = np.round(60000 * tracker._SEMI ** -np.arange(48)).astype(np.int64)
    assert tracker._lattice_et(period) is not None  # slope -1 (period)
    diat = np.round(
        ref
        * tracker._SEMI
        ** np.array([i // 7 * 12 + [0, 2, 4, 5, 7, 9, 11][i % 7] for i in range(28)])
    ).astype(np.int64)
    assert tracker._lattice_et(diat) is not None  # major-scale subset, slope != 1
    sparse = np.zeros(80, dtype=np.int64)
    notes = [2, 4, 8, 16, 19, 24, 29, 34, 40, 47, 53, 62, 79]
    for n in notes:
        sparse[n] = round(ref * tracker._SEMI**n)
    assert tracker._lattice_et(sparse) is not None  # 13 notes over 77-semitone span
    pattern = np.array([8101, 7217, 6812, 7217, 8101, 7217, 6812] * 8, dtype=np.int64)
    assert tracker._lattice_et(pattern) is None  # non-monotone arpeggio stream
    assert (
        tracker._lattice_et(np.round(ref * tracker._SEMI ** np.arange(8)).astype(np.int64)) is None
    )


def _mem_interleave(base, words):
    """A 64K image with 16-bit ``words`` laid out interleaved little-endian."""
    mem = bytearray(0x10000)
    for i, w in enumerate(words):
        mem[base + 2 * i], mem[base + 2 * i + 1] = int(w) & 0xFF, (int(w) >> 8) & 0xFF
    return type("M", (), {"mem0": bytes(mem)})()


def test_interleaved_freq_role_tiny_decl(monkeypatch):
    """A freq-role interleaved table split into byte-sized decls recovers by window."""
    period = np.round(64814 * tracker._SEMI ** -np.arange(96)).astype(np.int64)
    base = 0x3946
    mem = _mem_interleave(base, period)
    monkeypatch.setattr(tracker, "_read_bases", lambda m: {base})
    monkeypatch.setattr(tracker, "_freq_bases", lambda m: {base})
    monkeypatch.setattr(
        tracker.ann, "_decls", lambda m: [{"base": base, "size": 1, "kind": "table"}]
    )
    p = tracker._interleaved_pitch(mem)
    assert p is not None and not p.shift and p.base == base and p.octaves >= 6
    monkeypatch.setattr(
        tracker.ann, "_decls", lambda m: [{"base": base, "size": 120, "kind": "table"}]
    )
    assert tracker._interleaved_pitch(mem).base == base  # a real decl still recovers


def _mem_split(lo, hi, words):
    """A 64K image with 16-bit ``words`` laid out as split lo/hi byte blocks."""
    mem = bytearray(0x10000)
    for i, w in enumerate(words):
        mem[lo + i], mem[hi + i] = int(w) & 0xFF, (int(w) >> 8) & 0xFF
    return type("M", (), {"mem0": bytes(mem)})()


def test_octave_split_pitch(monkeypatch):
    """A co-indexed 12-note ET octave in split lo/hi bytes inverts by octave shift."""
    octave = np.round(8000 * tracker._SEMI ** np.arange(12)).astype(np.int64)
    lo, hi = 0x2000, 0x200C
    assert tracker._split_octave(_mem_split(lo, hi, octave).mem0, lo, hi) is not None
    monkeypatch.setattr(tracker, "_coindexed_bases", lambda m: [{lo, hi}])
    p = tracker._octave_split_pitch(_mem_split(lo, hi, octave))
    assert p is not None and p.shift and p.base == lo and len(p.words) == 12
    noise = np.arange(1, 13, dtype=np.int64) * 111
    monkeypatch.setattr(tracker, "_coindexed_bases", lambda m: [{0x3000, 0x300C}])
    assert tracker._octave_split_pitch(_mem_split(0x3000, 0x300C, noise)) is None


def test_role_split_pitch(monkeypatch):
    """Freq-role lo/hi tables (not co-indexed) pair by role, gapped ET confirming."""
    words = np.round(560 * tracker._SEMI ** np.arange(48)).astype(np.int64)
    words[[4, 5, 17, 33]] = 0  # unused notes read zero in the image
    lo, hi = 0x4000, 0x4100
    mem = _mem_split(lo, hi, words)
    monkeypatch.setattr(tracker.ann, "model_procs", lambda m: [])
    monkeypatch.setattr(
        tracker.ann,
        "aggregate",
        lambda procs, m: ann.TableRoles({lo: ["freq_lo"], hi: ["freq_hi"]}, {}),
    )
    monkeypatch.setattr(
        tracker.ann,
        "_decls",
        lambda m: [
            {"base": lo, "size": 48, "kind": "table"},
            {"base": hi, "size": 48, "kind": "table"},
        ],
    )
    p = tracker._role_split_pitch(mem)
    assert p is not None and not p.shift and p.base == lo and p.octaves >= 3


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_notes_and_gate_t(sid, subtune):
    """Engine lifts, most freq-pairs invert to ET notes, render is bit-exact."""
    model = _model(sid, subtune)
    t = tracker.lift(model)
    assert t.pitch is not None and t.pitch.base == 0x5428
    assert t.tempo == 0x5596 and any(c.role == "lfo" for c in t.clocks)
    assert tracker.gate_t(model, 200) is None
    _r, _gt, cov, lanes = tracker.render(model, 200)
    assert cov.notes / cov.planes > 0.9
    assert all(lanes[v] for v in range(3)) and lanes[0][0][1].name


@pytest.mark.parametrize("sid,subtune", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_separate_block_pitch_notes(sid, subtune):
    """Follin separate lo/hi pitch blocks read correctly; inline-vibrato notes."""
    model = _model(sid, subtune)
    p = tracker.lift(model).pitch
    assert p is not None and p.base == 0x6D35 and not p.shift
    assert ann.et_check(p.words)["pitch_table"]  # the lo/hi pairing is ET, not garbage
    assert tracker.gate_t(model, 200) is None
    _r, _gt, cov, lanes = tracker.render(model, 200)
    assert cov.notes > 0 and any(lanes)


@pytest.mark.parametrize("sid,subtune", _tune("Automatas", "Goto80"))
def test_automatas_split_block_pitch(sid, subtune):
    """DefMON's split freq-lo/freq-hi blocks at distinct read bases are paired."""
    model = _model(sid, subtune)
    p = tracker.lift(model).pitch
    assert p is not None and p.base == 0x1578 and p.endian == "split"
    assert ann.et_check(p.words)["pitch_table"]
    assert tracker.gate_t(model, 200) is None
    _r, _gt, cov, _lanes = tracker.render(model, 200)
    assert cov.notes > 0


@pytest.mark.parametrize("sid,subtune", _tune("Athena", "Galway_Martin"))
def test_athena_split_wrap_pitch(sid, subtune):
    """Galway's split lo/hi table behind wrap-offset bases + scalar shadow is found."""
    model = _model(sid, subtune)
    p = tracker.lift(model).pitch
    assert p is not None and p.base == 0xC517 and p.endian == "split"
    assert ann.et_check(p.words)["pitch_table"] and p.octaves >= 3
    assert tracker.gate_t(model, 200) is None


@pytest.mark.parametrize("sid,subtune", _tune("Krakout", "Daglish_Ben"))
def test_krakout_octave_shift_notes(sid, subtune):
    """A one-octave big-endian ET table is read from the graph and notes recover."""
    model = _model(sid, subtune)
    p = tracker.lift(model).pitch
    assert p is not None and p.base == 0xE629 and p.endian == ">" and p.shift
    assert tracker.gate_t(model, 200) is None
    _r, _gt, cov, lanes = tracker.render(model, 200)
    assert cov.notes > 100
    detunes = {n.detune for lane in lanes for _f, n in lane}
    assert 0 in detunes and detunes & {30, -30}  # the ±30 vibrato triplets
    assert all(abs(n.detune) <= 30 for lane in lanes for _f, n in lane)  # continuity: no excursions
