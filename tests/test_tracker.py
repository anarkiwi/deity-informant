"""tracker: the generator primitive, declared-table pitch recovery, and the law."""

from pathlib import Path

import numpy as np
import pytest

from deity_informant import eqlift_annotate as ann
from deity_informant import framelog as F
from deity_informant import frameprog
from deity_informant import structured as S
from deity_informant import tracker as T
from deity_informant.c64 import load_psid

import _fuzzgen as G

from _corpus import corpus_params
from test_frameprog import _fuzz_model

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _tune(stem, parent):
    return [
        pytest.param(path, sub, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _lifted(sid, subtune, frames=200):
    """``(prog, trace, nframes)`` for a corpus tune: the tracker's whole input."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, frames, subtune)
    prog = frameprog.program(model)
    trace, _walker = frameprog.iota(model, frames)
    return prog, trace, frames


# ---- 1. the one primitive --------------------------------------------------------
def _frames(rows):
    """Per-frame ``(reg, val)`` write lists."""
    return [list(r) for r in rows]


def _diff(graph, frames):
    """The law, applied to a hand-built projection instead of a frame program."""
    return F.diff(T.eval_graph(graph, len(frames)), F.canonical(frames))


def test_raw_floor_passes_by_construction():
    """An unrefined graph is complete: it replays every write in order."""
    frames = _frames([[(0, 1), (1, 2), (4, 0x41)], [(0, 3), (4, 0x40), (4, 0x41)]])
    g = T.from_frames(frames)
    assert _diff(g, frames) is None
    cov = T.coverage(g, len(frames))
    assert cov.interp == 0 and cov.residual == cov.total == 6


def test_div_fires_a_downstream_lookup_on_its_edge():
    """A DIV clock is the root; the LOOKUP advances only on its Fire edge."""
    nodes = [T.div(2), T.lookup([0x11, 0x22], ("event", 0), 0)]
    recs = T.eval_graph(T.Graph(nodes), 4)
    got = [[w for sec in rec for w in sec] for rec in recs]
    assert got == [[], [(0, 0x11)], [], [(0, 0x22)]]  # fires on frames 1 and 3


def test_ramp_wraps_at_its_bound():
    nodes = [T.ramp(0xFE, 1, 0x100, T.FRAME, 2)]
    recs = T.eval_graph(T.Graph(nodes), 4)
    vals = [w[1] for rec in recs for sec in rec for w in sec]
    assert vals == [0xFE, 0xFF, 0x00, 0x01]


def test_refinement_moves_coverage_out_of_raw_and_still_gates():
    """Pulling a last-write-wins plane out of RAW keeps the law green."""
    frames = _frames([[(0, 0x10), (4, 0x41)], [(0, 0x10), (4, 0x41)]])
    residual = _frames([[(4, 0x41)], [(4, 0x41)]])
    g = T.Graph([T.raw(residual), T.lookup([0x10], T.FRAME, 0)])
    assert _diff(g, frames) is None
    cov = T.coverage(g, 2)
    assert cov.interp == 2 and cov.residual == 2
    assert cov.planes["freq"] == (2, 2) and cov.planes["ctrl"] == (0, 2)


def test_mutation_wrong_lookup_value_is_detected():
    frames = _frames([[(0, 0x10)], [(0, 0x10)]])
    assert _diff(T.Graph([T.lookup([0x10], T.FRAME, 0)]), frames) is None
    d = _diff(T.Graph([T.lookup([0x11], T.FRAME, 0)]), frames)
    assert d is not None and d.got == (0, 0x11) and d.want == (0, 0x10)


def test_mutation_dropped_ordered_write_is_detected():
    """The order-preserved section does not collapse a duplicate ctrl write."""
    frames = _frames([[(4, 0x40), (4, 0x41)]])
    assert _diff(T.from_frames(frames), frames) is None
    d = _diff(T.from_frames(_frames([[(4, 0x41)]])), frames)
    assert d is not None and d.section == "v0.ord"


def test_mutation_swapped_ordered_writes_are_detected():
    frames = _frames([[(4, 0x40), (5, 0x11)]])
    d = _diff(T.from_frames(_frames([[(5, 0x11), (4, 0x40)]])), frames)
    assert d is not None and d.section == "v0.ord"


def test_dangling_and_unknown_forms_raise():
    with pytest.raises(T.TrackerError, match="dangling trigger"):
        T.eval_graph(T.Graph([T.lookup([1], ("event", 9), 0)]), 1)
    with pytest.raises(T.TrackerError, match="unknown trigger"):
        T.eval_graph(T.Graph([T.Generator(("LOOKUP", (1,)), ("nope",), ("plane", 0))]), 1)
    with pytest.raises(T.TrackerError, match="unknown route"):
        T.eval_graph(T.Graph([T.Generator(("LOOKUP", (1,)), T.FRAME, ("nope",))]), 1)
    with pytest.raises(T.TrackerError, match="no value emit"):
        T.eval_graph(T.Graph([T.Generator(("DIV", 1), T.FRAME, ("plane", 0))]), 1)
    assert T.Graph([T.raw([])]).raw_index() == 0 and T.Graph([T.div(2)]).raw_index() is None


@pytest.mark.parametrize("p", G.players(2), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_raw_floor_reproduces_frameprog_on_fuzz_players(p):
    """The floor holds against the real law: RAW vs the frame program's projection."""
    model = _fuzz_model(p)
    nframes = max(p.frames, 8)
    prog = frameprog.program(model)
    trace, frames = frameprog.iota(model, nframes)
    g = T.from_frames(frames)
    assert F.diff(T.eval_graph(g, nframes), T.oracle(prog, trace, nframes)) is None
    cov = T.coverage(g, nframes)
    assert cov.total == sum(len(fr) for fr in frames) and cov.interp == 0


# ---- 2. pitch from the declarations ---------------------------------------------
def _prog(decls, mem0):
    """A frame program carrying only declarations and an image."""
    return frameprog.FrameProgram(0x1000, 0x0F00, decls=decls, mem0=mem0)


def _table(base, size, stride=1, cobases=()):
    return {"kind": "table", "base": base, "size": size, "stride": stride, "cobases": list(cobases)}


def _interleaved(base, words, endian="<"):
    mem = bytearray(0x10000)
    mem[base : base + 2 * len(words)] = np.asarray(words, dtype=endian + "u2").tobytes()
    return mem


def _split(lo, hi, words):
    mem = bytearray(0x10000)
    for i, w in enumerate(words):
        mem[lo + i], mem[hi + i] = int(w) & 0xFF, (int(w) >> 8) & 0xFF
    return mem


def _et(n, ref=268.0, step=1):
    return np.round(ref * T._SEMI ** (step * np.arange(n))).astype(np.int64)


def test_pitch_is_read_from_a_declared_interleaved_table():
    """The declared extent is the window; no byte outside a declaration is read."""
    words = _et(96)
    prog = _prog([_table(0x2000, 192, stride=2)], _interleaved(0x2000, words))
    p = T._pitch(prog, [int(words[10])])
    assert p is not None and p.base == 0x2000 and not p.shift and len(p.words) == 96
    bare = _prog([], _interleaved(0x2000, words))
    assert T._pitch(bare, [int(words[10])]) is None  # undeclared bytes are not scanned


def test_pitch_pairs_two_declared_split_blocks():
    """A lo/hi pair is two declarations; the pairing is the declared extent."""
    words = _et(96, ref=560.0)
    prog = _prog([_table(0x4000, 96), _table(0x4100, 96)], _split(0x4000, 0x4100, words))
    p = T._pitch(prog, [int(words[5])])
    assert p is not None and (p.base, p.hi, p.endian) == (0x4000, 0x4100, "split")
    assert len(p.words) == 96 and p.octaves == 8


def test_pitch_reads_a_one_octave_table_by_octave_shift():
    words = _et(12, ref=8000.0)
    prog = _prog([_table(0x3000, 24, stride=2)], _interleaved(0x3000, words, ">"))
    p = T._pitch(prog, [int(words[5]) >> 2])
    assert p is not None and p.shift and p.endian == ">" and len(p.words) == 12


def test_pitch_prefers_the_table_that_explains_the_observed_freqs():
    """A decoy ET window that holds none of the written words loses, however long."""
    real, decoy = _et(48, ref=268.0), _et(96, ref=61000.0, step=-1)
    mem = _interleaved(0x2000, real)
    mem[0x5000 : 0x5000 + 2 * len(decoy)] = np.asarray(decoy, dtype="<u2").tobytes()
    prog = _prog([_table(0x2000, 96, stride=2), _table(0x5000, 192, stride=2)], mem)
    assert T._pitch(prog, [int(w) for w in real[:24]]).base == 0x2000
    assert T._pitch(prog, [int(w) for w in decoy[:24]]).base == 0x5000


def test_median_and_run_validators_read_gapped_and_noisy_windows():
    """Every ET reading of a window is a candidate: the strongest tier ranks first."""
    gapped = _et(48).copy()
    gapped[[3, 4, 5, 13, 20, 21, 31]] = 0  # unused notes are zero in the image
    assert [len(w) for _t, w in T._et_words(gapped)][0] == 48
    assert T._sparse_et(np.arange(1, 49) * 7) is None  # linear, not exponential
    assert T._median_et(np.arange(1, 49) * 7) is None
    octave = _et(14)
    seg = np.concatenate([octave, [0, 0], octave * 2, [0, 0], octave * 4])
    assert T._segmented_et(seg) is not None
    assert T._segmented_et(np.arange(1, 60)) is None
    window = np.concatenate([[999, 3, 7], octave]).astype(np.int64)
    assert len(T._longest_run(window, minrun=12)) == 14  # the interior chromatic octave


def test_lattice_et_geometric_layouts():
    """Descending-period, diatonic, and sparse ET tables confirm; pattern data fails."""
    assert T._lattice_et(_et(48, ref=60000.0, step=-1)) is not None
    diat = np.round(
        268 * T._SEMI ** np.array([i // 7 * 12 + [0, 2, 4, 5, 7, 9, 11][i % 7] for i in range(28)])
    ).astype(np.int64)
    assert T._lattice_et(diat) is not None  # major-scale subset, slope != 1
    sparse = np.zeros(80, dtype=np.int64)
    for n in (2, 4, 8, 16, 19, 24, 29, 34, 40, 47, 53, 62, 79):
        sparse[n] = round(268 * T._SEMI**n)
    assert T._lattice_et(sparse) is not None  # 13 notes over a 77-semitone span
    pattern = np.array([8101, 7217, 6812, 7217, 8101, 7217, 6812] * 8, dtype=np.int64)
    assert T._lattice_et(pattern) is None  # non-monotone arpeggio stream
    assert T._lattice_et(_et(8)) is None


def test_avail_spans_adjacent_declarations():
    """Adjacent declarations are one const run; a gap ends it."""
    prog = _prog([_table(0x2000, 8, cobases=[0x2004]), _table(0x2008, 8), _table(0x3000, 4)], b"")
    avail = T._avail(prog)
    assert avail[0x2000] == 16 and avail[0x2004] == 12 and avail[0x2008] == 8
    assert avail[0x3000] == 4


def test_note_direct_and_shift_inversion():
    """Multi-octave table matches by nearest word, one-octave table by octave shift."""
    words = _et(36)
    p = T.Pitch(0x2000, words, 3, 268, "<", False)
    assert T._note_direct(p, int(words[10])) == T.Note(10, int(words[10]), "A#0", 0)
    assert T._note_direct(p, int(words[-1]) * 3) is None  # far above the table
    octave = _et(12, ref=8000.0)
    ps = T.Pitch(0x3000, octave, 1, 8000, ">", True)
    n = T._note_shift(ps, int(octave[5]) >> 2)
    assert n is not None and n.detune == 0 and n.name == "F6"
    assert T._note_shift(ps, int(octave.max()) * 20) is None  # no octave resolves
    assert T._note_of(p, int(words[10])).index == 10
    assert T._note_of(ps, int(octave[5])).index == 5


# ---- 3. the engine and the law over real tunes -----------------------------------
def test_clocks_and_tempo_come_off_the_frameprog_procedures():
    """A dec+reload cell is a divider (its reload is the tempo), a free inc an LFO."""
    dec = (
        "st",
        ("const", 0x1000, 2),
        ("op", "INT_SUB", (("mem", ("const", 0x1000, 2), 1), ("const", 1, 1)), 1),
    )
    reload_ = ("st", ("const", 0x1000, 2), ("mem", ("const", 0x2000, 2), 1))
    inc = (
        "st",
        ("const", 0x1001, 2),
        ("op", "INT_ADD", (("mem", ("const", 0x1001, 2), 1), ("const", 1, 1)), 1),
    )
    prog = frameprog.FrameProgram(0x1000, 0x0F00, procs=[(0x1000, (), (), [dec, reload_, inc])])
    clocks = T._clocks(prog)
    assert [(c.base, c.kind, c.reload, c.role) for c in clocks] == [
        (0x1000, "dec", 0x2000, "divider"),
        (0x1001, "inc", None, "lfo"),
    ]
    assert T._tempo(clocks) == 0x2000 and T._tempo(clocks[1:]) is None


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_notes_and_the_law(sid, subtune):
    """Engine lifts, most freq-pairs invert to ET notes, the graph is bit-exact."""
    prog, trace, nf = _lifted(sid, subtune)
    t = T.lift(prog, T.oracle(prog, trace, nf))
    assert t.pitch is not None and t.pitch.base == 0x5428
    assert t.tempo == 0x5596 and any(c.role == "lfo" for c in t.clocks)
    assert t.instruments and T.gate(prog, trace, nf) is None
    _r, _gt, cov, lanes = T.render(prog, trace, nf)
    fi, ft = cov.planes["freq"]
    assert fi / ft > 0.9  # freq plane mostly interpreted
    assert cov.interp + cov.residual == cov.total  # the partition is complete
    assert all(lanes[v] for v in range(3)) and lanes[0][0][1].name


@pytest.mark.parametrize("sid,subtune", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_separate_block_pitch_notes(sid, subtune):
    """Follin separate lo/hi pitch blocks are two declarations that pair up."""
    prog, trace, nf = _lifted(sid, subtune)
    p = T.lift(prog, T.oracle(prog, trace, nf)).pitch
    assert p is not None and p.base == 0x6D35 and p.endian == "split"
    assert ann.et_check(p.words)["pitch_table"]  # the lo/hi pairing is ET, not garbage
    assert T.gate(prog, trace, nf) is None
    _r, _gt, cov, lanes = T.render(prog, trace, nf)
    assert cov.planes["freq"][0] > 0 and any(lanes)


@pytest.mark.parametrize("sid,subtune", _tune("Automatas", "Goto80"))
def test_automatas_split_block_pitch(sid, subtune):
    """DefMON's split freq-lo/freq-hi blocks are declared at their true extent."""
    prog, trace, nf = _lifted(sid, subtune)
    p = T.lift(prog, T.oracle(prog, trace, nf)).pitch
    assert p is not None and p.base == 0x1578 and p.endian == "split"
    assert ann.et_check(p.words)["pitch_table"]
    assert len(p.words) == 120 and p.words[-1] == 65535  # the declared 120-note run
    assert T.gate(prog, trace, nf) is None
    assert T.render(prog, trace, nf)[2].planes["freq"][0] > 0


@pytest.mark.parametrize("sid,subtune", _tune("Athena", "Galway_Martin"))
def test_athena_split_wrap_pitch(sid, subtune):
    """Galway's split lo/hi table behind wrap-offset bases is declared and paired."""
    prog, trace, nf = _lifted(sid, subtune)
    p = T.lift(prog, T.oracle(prog, trace, nf)).pitch
    assert p is not None and p.base == 0xC517 and p.endian == "split"
    assert ann.et_check(p.words)["pitch_table"] and p.octaves >= 3
    assert T.gate(prog, trace, nf) is None


@pytest.mark.parametrize("sid,subtune", _tune("Krakout", "Daglish_Ben"))
def test_krakout_octave_shift_notes(sid, subtune):
    """A one-octave big-endian ET table is declared, and notes recover by shift."""
    prog, trace, nf = _lifted(sid, subtune)
    p = T.lift(prog, T.oracle(prog, trace, nf)).pitch
    assert p is not None and p.base == 0xE629 and p.endian == ">" and p.shift
    assert T.gate(prog, trace, nf) is None
    _r, _gt, cov, lanes = T.render(prog, trace, nf)
    assert cov.planes["freq"][0] > 100
    detunes = {n.detune for lane in lanes for _f, n in lane}
    assert 0 in detunes and detunes & {30, -30}  # the +-30 vibrato triplets
    assert all(abs(n.detune) <= 30 for lane in lanes for _f, n in lane)  # no excursions
