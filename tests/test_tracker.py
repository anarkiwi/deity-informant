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


def test_edge_stream_fires_downstream_selects_once_per_edge():
    """An EDGE tick emits one SELECT value; two ticks in a frame emit two, in order."""
    nodes = [T.edge((0, 2, 1)), T.select((0x11, 0x22, 0x33), (0, 1, 2), ("event", 0), 5)]
    recs = T.eval_graph(T.Graph(nodes), 3)
    got = [[w for sec in rec for w in sec] for rec in recs]
    assert got == [[], [(5, 0x11), (5, 0x22)], [(5, 0x33)]]


def test_mutation_wrong_select_row_is_detected():
    """The row indexes the table: a wrong row emits a wrong byte and the law fails."""
    frames = _frames([[(5, 0x22)]])
    ok = T.Graph([T.edge((1,)), T.select((0x11, 0x22), (1,), ("event", 0), 5)])
    assert _diff(ok, frames) is None
    d = _diff(T.Graph([T.edge((1,)), T.select((0x11, 0x22), (0,), ("event", 0), 5)]), frames)
    assert d is not None and d.got == (5, 0x11) and d.want == (5, 0x22)


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
    with pytest.raises(T.TrackerError, match="no edge emit"):
        T.eval_graph(T.Graph([T.Generator(("LOOKUP", (1,)), T.FRAME, ("fire",))]), 1)
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


# ---- 3. instrument lanes: ADSR from a declared bank ------------------------------
def _bank(base, rows, stride, lanes):
    """``(image, declaration)`` for a ``rows`` x ``stride`` instrument bank."""
    mem = bytearray(0x10000)
    for off, vals in lanes.items():
        for i, v in enumerate(vals[:rows]):
            mem[base + stride * i + off] = v
    return mem, _table(base, rows * stride, stride=stride)


def _sel(base, off, cell):
    """``mem[base + off + cell]``: an indexed lane read at a pure (local) index."""
    idx = ("op", "INT_ZEXT", (("loc", cell),), 2)
    return ("mem", ("op", "INT_ADD", (("const", base + off, 2), idx), 2), 1)


def _rowprog(mem, decl, stores, cell=0x0800, step=1):
    """Frame program whose play reads a row cell, writes ``stores``, then steps it."""
    load = ("asg", "i", ("mem", ("const", cell, 2), 1))
    bump = (
        "st",
        ("const", cell, 2),
        ("op", "INT_ADD", (("mem", ("const", cell, 2), 1), ("const", step, 1)), 1),
    )
    stmts = [load] + [("st", ("const", 0xD400 + r, 2), v) for r, v in stores] + [bump, ("ret",)]
    return frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def test_adsr_reads_a_declared_lane_at_a_recovered_row():
    """The emit is a declared lane byte; ad and sr share one recovered row stream."""
    ad, sr = [0x11, 0x22, 0x33, 0x44], [0x51, 0x52, 0x53, 0x54]
    mem, decl = _bank(0x2000, 4, 4, {2: ad, 3: sr})
    prog = _rowprog(mem, decl, [(5, _sel(0x2000, 2, "i")), (6, _sel(0x2000, 3, "i"))], step=4)
    assert T.gate(prog, {}, 4) is None
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["ad"] == (4, 4) and cov.planes["sr"] == (4, 4)
    _gt, ords = T._observe(prog, {}, 4)
    pre, post, refined = T._instr_streams(prog, ords)
    assert refined == {5, 6} and not pre
    got = {r: t for _c, t, r in post}
    assert got[5] == ("SELECT", tuple(ad), (0, 1, 2, 3))  # the declared lane, by row
    assert got[6][1] == tuple(sr) and got[6][2] == got[5][2]


def test_a_lane_cell_the_play_phase_wrote_is_not_read_as_constant():
    """Provenance is not enough: the byte must equal the declared image byte."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44]})
    clean = _rowprog(mem, decl, [(5, ("mem", ("const", 0x2002, 2), 1))])
    assert T.render(clean, {}, 4)[2].planes["ad"] == (4, 4)
    write = ("st", ("const", 0x2002, 2), ("const", 0x99, 1))
    stmts = [write] + list(clean.procs[0][3])
    dirty = frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )
    assert T.render(dirty, {}, 4)[2].planes["ad"] == (0, 4)  # the snapshot disagrees
    assert T.gate(dirty, {}, 4) is None


def test_immediate_writes_split_from_lane_writes_by_provenance():
    """A constant a store site writes is its own one-entry stream; nothing else is."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44]})
    prog = frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem)
    banks = T._banks(prog)
    seq = [[(5, 0x11, (0x2002,))], [(5, 0x00, ())]]
    rel, streams = T._refine_voice(seq, {5}, banks, {5: {0}}, mem)
    assert rel == "post" and [s[1][0] for s in streams] == ["SELECT", "LOOKUP"]
    assert streams[1][1] == ("LOOKUP", (0,)) and streams[1][0] == (0, 1)
    assert T._refine_voice(seq, {5}, banks, {}, mem) is None  # 0 is no program constant


def test_write_order_against_the_residual_is_checked():
    """ADSR ahead of ctrl places the streams before RAW; interleaved is refused."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44]})
    banks = T._banks(frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem))
    ahead = [[(5, 0x11, (0x2002,)), (4, 0x41, ())]]
    assert T._refine_voice(ahead, {5}, banks, {}, mem)[0] == "pre"
    mid = [[(4, 0x41, ()), (5, 0x11, (0x2002,)), (4, 0x40, ())]]
    assert T._refine_voice(mid, {5}, banks, {}, mem) is None


def test_immediates_are_read_off_the_program_text_per_register_class():
    """A voice-generic store site fixes the constant for the register class."""
    stmts = [
        ("st", ("const", 0xD405, 2), ("const", 0x00, 1)),
        ("st", ("const", 0xD40D, 2), ("const", 0xF0, 1)),
        ("st", ("const", 0x0800, 2), ("const", 0x11, 1)),
    ]
    prog = frameprog.FrameProgram(0x1000, 0x0F00, procs=[(0x1000, (), (), stmts)])
    assert T._immediates(prog) == {5: {0x00}, 6: {0xF0}}


def test_a_constant_reaches_a_ctrl_store_through_a_local():
    """A hard-restart byte the play code loads in a branch is still a program constant."""
    stmts = [
        ("if", "if", ("loc", "c"), [("asg", "a", ("const", 0x80, 1))], []),
        ("st", ("const", 0xD404, 2), ("loc", "a")),
        ("asg", "b", ("mem", ("const", 0x0800, 2), 1)),
        ("st", ("const", 0xD40B, 2), ("loc", "b")),
    ]
    prog = frameprog.FrameProgram(0x1000, 0x0F00, procs=[(0x1000, (), (), stmts)])
    assert T._immediates(prog) == {4: {0x80}}  # the loaded byte contributes nothing


# ---- 3b. ctrl: the waveform lane, and the gate bit over the held row --------------
_SHADOW = 0x0900


def _ctrlprog(mem, decl, arms, cell=0x0800, step=4):
    """Frame program whose play reads a row cell, runs ``arms``, then steps it."""
    load = ("asg", "i", ("mem", ("const", cell, 2), 1))
    bump = (
        "st",
        ("const", cell, 2),
        ("op", "INT_ADD", (("mem", ("const", cell, 2), 1), ("const", step, 1)), 1),
    )
    stmts = [load] + arms + [bump, ("ret",)]
    return frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def _ctrl_arms(base=0x2000, off=2):
    """Note-on writes the lane byte and shadows it; the gate-off writes it back cleared."""
    lane = _sel(base, off, "i")
    cleared = ("op", "INT_AND", (("mem", ("const", _SHADOW, 2), 1), ("const", 0xFE, 1)), 1)
    return [
        ("st", ("const", _SHADOW, 2), lane),
        ("st", ("const", 0xD404, 2), lane),
        ("st", ("const", 0xD404, 2), cleared),
    ]


def test_ctrl_is_the_declared_waveform_lane_and_its_gate_image():
    """Gate-on reads the declared lane; gate-off is the same row with bit 0 cleared."""
    wave = [0x41, 0x15, 0x81, 0x43]
    mem, decl = _bank(0x2000, 4, 4, {2: wave})
    prog = _ctrlprog(mem, decl, _ctrl_arms())
    assert T.gate(prog, {}, 4) is None
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["ctrl"] == (8, 8)
    assert cov.classes["ctrl"] == {"lane": 4, "gate": 4, "imm": 0}
    _gt, ords = T._observe(prog, {}, 4)
    _pre, post, refined = T._instr_streams(prog, ords)
    assert refined == {4}
    (t,) = [t for _c, t, r in post if r == 4]
    w = tuple(wave)
    gated, on = tuple(b & 0xFE for b in wave), tuple(b | 1 for b in wave)
    assert t == ("SELECT", w + w + gated + on, (0, 8, 1, 9, 2, 10, 3, 11))


def test_a_gate_write_without_a_recovered_row_stays_residual():
    """The gate bit rides a recovered waveform: with no lane read there is nothing to ride."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x41, 0x15, 0x81, 0x43]})
    shadow, on, cleared = _ctrl_arms()
    assert T.render(_ctrlprog(mem, decl, [shadow, on, cleared]), {}, 4)[2].planes["ctrl"] == (8, 8)
    bare = _ctrlprog(mem, decl, [shadow, cleared])  # the gate-off write, with no note-on
    assert T.render(bare, {}, 4)[2].planes["ctrl"] == (0, 4) and T.gate(bare, {}, 4) is None


def test_a_play_written_waveform_lane_takes_the_gate_writes_down_with_it():
    """#61 for ctrl: a mutated lane cell is no constant, so its gate image is none either."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x41, 0x15, 0x81, 0x43]})
    assert T.render(_ctrlprog(mem, decl, _ctrl_arms()), {}, 4)[2].planes["ctrl"] == (8, 8)
    dirty = [("st", ("const", 0x2002, 2), ("const", 0x99, 1))] + _ctrl_arms()
    prog = _ctrlprog(mem, decl, dirty)
    assert T.render(prog, {}, 4)[2].planes["ctrl"] == (0, 8) and T.gate(prog, {}, 4) is None


# ---- 4. the engine and the law over real tunes -----------------------------------
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
    assert cov.planes["ad"][0] == cov.planes["ad"][1] > 0  # ADSR wholly interpreted
    assert cov.planes["sr"][0] == cov.planes["sr"][1] > 0


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_adsr_is_the_declared_instrument_bank(sid, subtune):
    """Every ctrl/ADSR emit is a byte of the declared $5591 bank at a recovered row."""
    prog, trace, nf = _lifted(sid, subtune)
    _gt, ords = T._observe(prog, trace, nf)
    pre, post, refined = T._instr_streams(prog, ords)
    assert refined == {4, 5, 6, 11, 12, 13, 18, 19, 20} and not pre
    sel = {r: t for _c, t, r in post if t[0] == "SELECT"}
    assert len(sel) == 9 and set(T.lift(prog).instruments) >= {0x5594, 0x5595}
    for off, regs in ((3, (5, 12, 19)), (4, (6, 13, 20))):
        lane = tuple(prog.mem0[0x5591 + off + 8 * i] for i in range(33))
        assert all(sel[r][1] == lane for r in regs)  # the AD and SR lanes, as declared
    wave = tuple(prog.mem0[0x5593 + 8 * i] for i in range(33))
    held = tuple(b & 0xFE for b in wave) + tuple(b | 1 for b in wave)
    for r in (4, 11, 18):  # the waveform lane at +2, then the three held readings
        assert sel[r][1] == wave + wave + held
        assert set(sel[r][2]) <= set(range(66)) | set(range(66, 99))  # no gate-set row
    for v in range(3):
        assert sel[7 * v + 5][2] == sel[7 * v + 6][2]  # one row stream per voice
        assert set(sel[7 * v + 5][2]) <= set(range(13))  # rows are instrument numbers


@pytest.mark.parametrize("sid,subtune", _tune("Artura", "Daglish_Ben"))
def test_artura_adsr_through_the_sid_register_mirror(sid, subtune):
    """ADSR staged in a per-voice SID mirror still reads as the declared bank."""
    prog, trace, nf = _lifted(sid, subtune)
    assert 0xEFC1 in T.lift(prog).instruments  # the store site reads the mirror cell
    _gt, ords = T._observe(prog, trace, nf)
    pre, post, refined = T._instr_streams(prog, ords)
    assert refined >= {5, 6, 12, 13, 19, 20} and not pre
    bank = tuple(prog.mem0[0xEF52 + i] for i in range(46))
    sel = {r: t for _c, t, r in post if t[0] == "SELECT"}
    assert all(sel[r][1] == bank for r in (5, 6, 12, 13, 19, 20))  # the $EF52 declaration
    assert T.gate(prog, trace, nf) is None
    cov = T.render(prog, trace, nf)[2]
    assert cov.planes["ad"][0] == cov.planes["ad"][1] > 0
    assert cov.planes["sr"][0] == cov.planes["sr"][1] > 0


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
