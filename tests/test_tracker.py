"""tracker: the generator primitive, declared-table pitch recovery, and the law."""

from collections import Counter
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


def _instr(prog, ords):
    """``(pre, post, wholly refined registers)`` from ``_instr_streams``."""
    banks = T._banks(prog)
    pre, post, resid = T._instr_streams(prog, ords, T._tree_tables(prog, banks), banks)
    left = {w[0] for v in resid for ws in v for w in ws}
    return pre, post, {w[0] for seq in ords for ws in seq for w in ws} - left


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
    _gt, ords, _lww, _acc = T._observe(prog, {}, 4)
    pre, post, refined = _instr(prog, ords)
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
    pre, post, resid = T._refine_voice(seq, {}, banks, {5: {0}}, mem)
    assert not pre and sorted(s[1][0] for s in post) == ["LOOKUP", "SELECT"]
    (lk,) = [s for s in post if s[1][0] == "LOOKUP"]
    assert lk[1] == ("LOOKUP", (0,)) and lk[0] == (0, 1) and resid == [(), ()]
    _p, _q, left = T._refine_voice(seq, {}, banks, {}, mem)  # 0 is no program constant
    assert left == [(), ((5, 0x00),)]


def test_write_order_against_the_residual_is_checked():
    """ADSR ahead of ctrl places the streams before RAW; a straddled key is demoted."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44]})
    banks = T._banks(frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem))
    ahead = [[(5, 0x11, (0x2002,)), (4, 0x41, ())]]
    pre, post, resid = T._refine_voice(ahead, {}, banks, {}, mem)
    assert [s[2] for s in pre] == [5] and not post and resid == [((4, 0x41),)]
    mid = [[(4, 0x41, ()), (5, 0x11, (0x2002,)), (4, 0x40, ())]]
    assert T._refine_voice(mid, {}, banks, {}, mem)[2] == [((4, 0x41), (5, 0x11), (4, 0x40))]
    # mutation evidence for that demotion: neither placement of the key holds.
    frames = _frames([[(4, 0x41), (5, 0x11), (4, 0x40)]])
    lane, res = T.select((0x11,), (0,), ("event", 0), 5), T.raw([[(4, 0x41), (4, 0x40)]])
    for nodes in ([T.edge((1,)), lane, res], [T.edge((1,)), res, lane]):
        assert _diff(T.Graph(nodes), frames) is not None


def test_one_unexplained_write_no_longer_costs_the_whole_register():
    """The finer partition: the explained writes stream, the odd one out stays RAW."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44]})
    banks = T._banks(frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem))
    srcs = ((0x11, (0x2002,)), (0x22, (0x2006,)), (0x99, ()), (0x44, (0x200E,)))
    pre, post, resid = T._refine_voice([[(5, v, s)] for v, s in srcs], {}, banks, {}, mem)
    assert not pre and [s[1] for s in post] == [("SELECT", (0x11, 0x22, 0x33, 0x44), (0, 1, 3))]
    assert post[0][0] == (1, 1, 0, 1) and resid == [(), (), ((5, 0x99),), ()]


def test_a_voice_splits_across_both_sides_of_the_residual():
    """One residual ctrl write between two lanes puts ad in ``pre`` and sr in ``post``."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44], 3: [0x51, 0x52, 0x53, 0x54]})
    banks = T._banks(frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem))
    seq = [
        [(5, 0x11, (0x2002,)), (4, 0x41, ()), (6, 0x51, (0x2003,))],
        [(5, 0x22, (0x2006,)), (4, 0x40, ()), (6, 0x52, (0x2007,))],
    ]
    pre, post, resid = T._refine_voice(seq, {}, banks, {}, mem)
    assert [s[2] for s in pre] == [5] and [s[2] for s in post] == [6]
    assert resid == [((4, 0x41),), ((4, 0x40),)]


def test_the_rebuilt_section_refuses_a_bucket_order_that_does_not_hold(monkeypatch):
    """Mutation evidence for the check: a swapped bucket order refuses the voice whole."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44], 3: [0x51, 0x52, 0x53, 0x54]})
    banks = T._banks(frameprog.FrameProgram(0x1000, 0x0F00, decls=[decl], mem0=mem))
    seq = [[(5, 0x11, (0x2002,)), (6, 0x51, (0x2003,))]]
    good = T._buckets
    assert T._refine_voice(seq, {}, banks, {}, mem)[2] == [()]
    monkeypatch.setattr(T, "_buckets", lambda o, w: good(o, w)[:2] + (good(o, w)[2][::-1], set(w)))
    assert T._refine_voice(seq, {}, banks, {}, mem) is None


def test_a_voice_whose_section_cannot_be_rebuilt_keeps_every_write(monkeypatch):
    """A refused voice falls all the way back to the RAW floor, so nothing is lost."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44]})
    prog = _rowprog(mem, decl, [(5, _sel(0x2000, 2, "i"))], step=4)
    _gt, ords, _lww, _acc = T._observe(prog, {}, 4)
    monkeypatch.setattr(T, "_refine_voice", lambda *a: None)
    pre, post, resid = T._instr_streams(prog, ords, {}, T._banks(prog))
    assert not pre and not post
    assert resid[0] == [((5, v),) for v in (0x11, 0x22, 0x33, 0x44)] and resid[1] == [()] * 4


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
    assert cov.classes["ctrl"] == {
        "lane": 4,
        "gate": 4,
        "imm": 0,
        "ramp": 0,
        "seed": 0,
        "mask": 0,
        "rel": 0,
    }
    _gt, ords, _lww, _acc = T._observe(prog, {}, 4)
    _pre, post, refined = _instr(prog, ords)
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
    # only the mutated row's pair falls out: the register is split, not forfeited.
    assert T.render(prog, {}, 4)[2].planes["ctrl"] == (6, 8) and T.gate(prog, {}, 4) is None


# ---- 3b. the declaration's mutable offsets, and the lww planes off the tree -------
def _muted(decl, offs):
    """The declaration with ``offs`` named as the offsets the play phase writes."""
    return {**decl, "mut": list(offs)}


def _at(base, idx):
    """``mem[base + idx]``: an indexed read whose index is itself an expression."""
    return ("mem", ("op", "INT_ADD", (("const", base, 2), ("op", "INT_ZEXT", (idx,), 2)), 2), 1)


def test_mut_offsets_are_lanes_when_strided_and_cells_when_flat():
    """``mut`` is per record offset: a lane modulo the stride, a cell in a flat region."""
    lane = [0x11, 0x22, 0x33, 0x44]
    mem, decl = _bank(0x2000, 4, 4, {2: lane})
    read = [(5, _sel(0x2000, 2, "i"))]
    assert T.render(_rowprog(mem, decl, read, step=4), {}, 4)[2].planes["ad"] == (4, 4)
    dirty = _rowprog(mem, _muted(decl, [2]), read, step=4)
    assert T.render(dirty, {}, 4)[2].planes["ad"] == (0, 4)  # the whole +2 lane, every row
    fmem, flat = _bank(0x3000, 4, 1, {0: lane})
    fread = [(0, _sel(0x3000, 0, "i"))]
    assert T.render(_rowprog(fmem, flat, fread), {}, 4)[2].planes["freq"] == (4, 4)
    cell = _rowprog(fmem, _muted(flat, [2]), fread)
    assert T.render(cell, {}, 4)[2].planes["freq"] == (3, 4)  # only that one cell
    assert T.gate(dirty, {}, 4) is None and T.gate(cell, {}, 4) is None


def test_freq_is_the_declared_table_the_store_names_at_a_recovered_row():
    """freq_lo/freq_hi are two lanes of one declaration read at one recovered row."""
    lo, hi = [0x10, 0x21, 0x32, 0x43], [0x01, 0x02, 0x03, 0x04]
    mem, decl = _bank(0x2000, 4, 2, {0: lo, 1: hi})
    stores = [(0, _sel(0x2000, 0, "i")), (1, _sel(0x2000, 1, "i"))]
    prog = _rowprog(mem, decl, stores, step=2)
    assert T.gate(prog, {}, 4) is None
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["freq"] == (8, 8) and cov.classes["freq"]["lane"] == 8
    _gt, _ords, lww, _acc = T._observe(prog, {}, 4)
    banks = T._banks(prog)
    streams, _e = T._lww_streams(lww, T._tree_tables(prog, banks), mem)
    got = {r: t for _c, t, r in streams}
    assert got[0] == ("SELECT", tuple(lo), (0, 1, 2, 3))  # the declared lane, by row
    assert got[1][1] == tuple(hi) and got[1][2] == got[0][2]


def test_the_lww_planes_read_the_table_the_store_names_not_the_one_it_indexes():
    """A cell the value only indexes through is no source, however its byte agrees."""
    mem, decl = _bank(0x2000, 4, 2, {0: [0x10, 0x21, 0x32, 0x43], 1: [0, 1, 2, 3]})
    mem[0x4000:0x4004] = bytes(range(4))  # undeclared: mem[$4000 + k] == k == the index byte
    stores = [(0, _at(0x4000, _sel(0x2000, 1, "i"))), (1, _sel(0x2000, 0, "i"))]
    prog = _rowprog(mem, decl, stores, step=2)
    assert T.gate(prog, {}, 4) is None
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["freq"] == (4, 8)  # freq_hi only; the indexed read names no declaration
    assert T._tree_tables(prog, T._banks(prog)) == {1: ((0x2000, 8, 2, frozenset()),)}


# ---- 3c. the pulse sweep: a RAMP whose step the origin map names ------------------
_ACC, _STAGE, _RAM = 0x0800, 0x0900, 0x0A00
_SWEEP_LANE = (0x16, 0x33)


def _sweepstmts(step, reg, extra=()):
    """Stage ``step`` in RAM, add it to `$0800`, and write the sum to a SID register."""
    acc = ("mem", ("const", _ACC, 2), 1)
    return [
        ("st", ("const", _STAGE, 2), step),
        ("asg", "t", ("op", "INT_ADD", (acc, ("mem", ("const", _STAGE, 2), 1)), 1)),
        ("st", ("const", _ACC, 2), ("loc", "t")),
        ("st", ("const", 0xD400 + reg, 2), ("loc", "t")),
        *extra,
        ("ret",),
    ]


def _sweepprog(src=0x2001, decl=None, reg=0x02, lane=_SWEEP_LANE):
    """``(image, program)`` whose step is staged in RAM from ``src``, then re-staged.

    No SID store reads the staging cell, so only the queried origin map names where the
    byte came from; the re-stage from row 1 after the step means a frame-end snapshot
    of that map names the wrong row."""
    mem, table = _bank(0x2000, len(lane), 4, {1: list(lane)})
    mem[_RAM] = lane[0]  # an undeclared cell holding the same byte
    back = ("st", ("const", _STAGE, 2), ("mem", ("const", 0x2005, 2), 1))
    stmts = _sweepstmts(("mem", ("const", src, 2), 1), reg, (back,))
    return mem, frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl or table], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def _rowsweepprog(lane, decl=None):
    """``(image, program)`` re-staging the step from a lane row that advances per frame."""
    mem, table = _bank(0x2000, len(lane), 4, {1: list(lane)})
    cell = ("mem", ("const", 0x0801, 2), 1)
    bump = ("st", ("const", 0x0801, 2), ("op", "INT_ADD", (cell, ("const", 4, 1)), 1))
    stmts = [("asg", "i", cell)] + _sweepstmts(_sel(0x2000, 1, "i"), 0x02, (bump,))
    return mem, frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl or table], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def _pw(prog, n=6, reg=2):
    """The bytes ``render`` emits into ``reg``, and the coverage it reports."""
    recs, _gt, cov, _lanes = T.render(prog, {}, n)
    return [w[1] for rec in recs for sec in rec for w in sec if w[0] == reg], cov


def _ramps(prog, n=6):
    """The RAMP transfers the rendered graph carries."""
    graph = T._graph(prog, None, *T._observe(prog, {}, n))[0]
    return [g.transfer for g in graph.nodes if g.transfer[0] == "RAMP"]


def test_pw_sweep_is_a_ramp_over_a_ram_staged_declared_step():
    """The step is a RAM cell; the origin map names the declared byte it was copied from."""
    _mem, prog = _sweepprog()
    vals, cov = _pw(prog)
    assert cov.planes["pw"] == (6, 6) and T.gate(prog, {}, 6) is None
    assert vals == [0x16, 0x2C, 0x42, 0x58, 0x6E, 0x84]
    assert cov.classes["pw"]["ramp"] == 5 and cov.classes["pw"]["seed"] == 1
    assert _ramps(prog) == [("RAMP", 0x16, 0x16, 0x100)]


def test_the_step_origin_is_read_per_execution_not_off_a_frame_snapshot():
    """The cell is re-staged after the step, so the last row written is not the one read."""
    mem, prog = _sweepprog()
    pools = T._observe(prog, {}, 6)[3][0]
    assert [sorted(p[_ACC]) for p in pools] == [sorted((_ACC, _STAGE, 0x2001))] * 6
    assert mem[0x2005] == 0x33 != mem[0x2001]  # what a frame-end snapshot would name
    assert T._accumulators(prog, T._acc_sites(prog)[2]) == {2: (_ACC, 1)}


def test_perturbing_the_declared_step_changes_the_generated_sweep():
    """The emitted stream is a function of the declared byte, not of the observation."""
    mem, prog = _sweepprog()
    mem[0x2001] = 0x05
    vals, cov = _pw(prog)
    assert cov.planes["pw"] == (6, 6) and vals == [0x05, 0x0A, 0x0F, 0x14, 0x19, 0x1E]
    assert _ramps(prog) == [("RAMP", 0x05, 0x05, 0x100)]  # seed and step are the declared byte


def test_a_sweep_whose_step_is_no_declared_byte_stays_residual():
    """A step the declarations do not hold is not a parameter: the run stays in RAW."""
    assert _pw(_sweepprog()[1])[1].planes["pw"] == (6, 6)  # the same sweep, step declared
    _mem, bare = _sweepprog(src=_RAM)  # the same stream, staged from an undeclared cell
    assert T._accumulators(bare, T._acc_sites(bare)[2]) == {2: (_ACC, 1)}
    assert _pw(bare)[0] == _pw(_sweepprog()[1])[0]
    assert _pw(bare)[1].planes["pw"] == (0, 6) and T.gate(bare, {}, 6) is None


def test_a_step_at_a_play_written_offset_is_not_a_parameter():
    """``mut`` names a play-written lane; a step staged from there is runtime state."""
    assert _pw(_sweepprog()[1])[1].planes["pw"] == (6, 6)  # the same sweep, step not `mut`
    _mem, dirty = _sweepprog(decl=_muted(_table(0x2000, 8, stride=4), [1]))
    assert _pw(dirty)[1].planes["pw"] == (0, 6) and T.gate(dirty, {}, 6) is None


def test_a_run_that_predicts_nothing_is_refused():
    """A declared step of zero and a run of one emit each generate no further byte."""
    _mem, flat = _sweepprog(lane=(0x00, 0x33))
    assert _pw(flat)[0] == [0] * 6 and _pw(flat)[1].planes["pw"] == (0, 6)
    _mem, prog = _sweepprog()
    assert _pw(prog, 1)[1].planes["pw"] == (0, 1) and T.gate(prog, {}, 1) is None


def test_a_re_staged_step_cuts_the_run_and_seeds_the_next_one():
    """A stream that stops regenerating ends its run; the next starts at its own seed."""
    _mem, prog = _rowsweepprog((0x10, 0x10, 0x33, 0x33))
    vals, cov = _pw(prog, 4)
    assert vals == [0x10, 0x20, 0x53, 0x86] and T.gate(prog, {}, 4) is None
    assert cov.planes["pw"] == (4, 4)
    assert cov.classes["pw"] == {
        "lane": 0,
        "gate": 0,
        "imm": 0,
        "ramp": 2,
        "seed": 2,
        "mask": 0,
        "rel": 0,
    }
    assert _ramps(prog, 4) == [("RAMP", 0x10, 0x10, 0x100), ("RAMP", 0x53, 0x33, 0x100)]


def test_a_run_the_ramp_cannot_regenerate_is_refused_whole():
    """One stepped emit whose origin is undeclared refuses the run, not just that emit."""
    _mem, prog = _rowsweepprog((0x10, 0x10, 0x10))
    assert _pw(prog, 3)[1].planes["pw"] == (3, 3)  # every step's origin is a declared byte
    _mem, cut = _rowsweepprog((0x10, 0x10, 0x10), decl=_table(0x2000, 8, stride=4))
    vals, cov = _pw(cut, 3)  # the same stream, the third row outside the declaration
    assert vals == [0x10, 0x20, 0x30] and cov.planes["pw"] == (0, 3)
    assert T.gate(cut, {}, 3) is None and not _ramps(cut, 3)


def test_the_cutoff_sweep_is_the_same_accumulator_as_the_pulse_sweep():
    """$16 is swept by an accumulator too, and takes the filter plane's own class."""
    _mem, prog = _sweepprog(reg=0x16)
    vals, cov = _pw(prog, reg=0x16)
    assert vals == [0x16, 0x2C, 0x42, 0x58, 0x6E, 0x84] and T.gate(prog, {}, 6) is None
    assert cov.planes["filter"] == (6, 6) and cov.classes["filter"]["ramp"] == 5
    assert T._accumulators(prog, T._acc_sites(prog)[2]) == {0x16: (_ACC, 1)}


def test_mutation_wrong_ramp_step_or_seed_is_detected():
    """The law fails on a wrong step and on a wrong seed: the sweep is generated."""
    _mem, prog = _sweepprog()
    assert _pw(prog)[1].planes["pw"] == (6, 6)
    graph = T._graph(prog, None, *T._observe(prog, {}, 6))[0]
    assert F.diff(T.eval_graph(graph, 6), T.oracle(prog, {}, 6)) is None
    i = next(i for i, g in enumerate(graph.nodes) if g.transfer[0] == "RAMP")
    for bad in (("RAMP", 0x16, 0x17, 0x100), ("RAMP", 0x17, 0x16, 0x100)):
        nodes = list(graph.nodes)
        nodes[i] = nodes[i]._replace(transfer=bad)
        assert F.diff(T.eval_graph(T.Graph(nodes), 6), T.oracle(prog, {}, 6)) is not None


# ---- 3d. the filter plane: $15-$18 off the store statement, in its own class ------
_FILTER_LANES = {0: [0x10, 0x21, 0x32, 0x43], 1: [1, 2, 3, 4], 2: [0xF1, 0xF2, 0xF3, 0xF4]}


def _filterprog(lanes=None, decl=None, base=0x2000):
    """``(image, program)`` writing $15-$17 from the ``+0/+1/+2`` lanes of one bank."""
    lanes = _FILTER_LANES if lanes is None else lanes
    mem, table = _bank(base, 4, 4, lanes)
    stores = [(0x15 + off, _sel(base, off, "i")) for off in sorted(lanes)]
    return mem, _rowprog(mem, decl or table, stores, step=4)


def test_filter_is_the_declared_table_the_store_names_at_a_recovered_row():
    """$15-$18 are last-write-wins like freq/pw: the lane the store names, at its row."""
    mem, prog = _filterprog()
    assert T.gate(prog, {}, 4) is None
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["filter"] == (12, 12) and cov.classes["filter"]["lane"] == 12
    assert cov.classes["filter"]["imm"] == 0  # declared bytes only, nothing shallow
    _gt, _ords, lww, _acc = T._observe(prog, {}, 4)
    streams, _e = T._lww_streams(lww, T._tree_tables(prog, T._banks(prog)), mem)
    got = {r: t for _c, t, r in streams}
    assert got[0x15] == ("SELECT", tuple(_FILTER_LANES[0]), (0, 1, 2, 3))
    assert got[0x17][1] == tuple(_FILTER_LANES[2]) and got[0x17][2] == got[0x15][2]


def test_the_filter_registers_take_a_register_class_of_their_own():
    """``reg % 7`` aliases $15-$18 onto freq/pw; the filter is one global, not a voice."""
    assert [T._class_of(r) for r in (0x00, 0x02, 0x0E, 0x15, 0x16, 0x17, 0x18)] == [
        0,
        2,
        0,
        0x15,
        0x16,
        0x17,
        0x18,
    ]
    assert T._sid_class(("const", 0xD417, 2)) == 0x17 and T._sid_class(("const", 0xD419, 2)) is None


def test_a_table_a_pw_store_names_does_not_explain_a_filter_write():
    """$17 aliases pw_lo under ``reg % 7``, so a shared pool would take an indexed byte."""
    mem, decl = _bank(0x2000, 4, 4, {0: [0, 1, 2, 3]})
    mem[0x4000:0x4004] = bytes(range(4))  # undeclared: mem[$4000 + k] == k == the lane byte
    lane = _sel(0x2000, 0, "i")
    prog = _rowprog(mem, decl, [(0x02, lane), (0x17, _at(0x4000, lane))], step=4)
    assert T.gate(prog, {}, 4) is None
    tabs = T._tree_tables(prog, T._banks(prog))
    assert set(tabs) == {2}  # named for pw_lo by its store, for the filter by none
    assert T._lane_key((0x17, 0x02, (0x2008,)), tabs[2], mem) is not None  # the pool would take it
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["pw"] == (4, 4) and cov.planes["filter"] == (0, 4)


def test_a_filter_lane_the_play_phase_writes_is_not_const_data():
    """``mut`` holds here too: a play-written lane is runtime state, not a declaration."""
    lanes = {0: _FILTER_LANES[0]}
    assert T.render(_filterprog(lanes)[1], {}, 4)[2].planes["filter"] == (4, 4)
    _m, dirty = _filterprog(lanes, decl=_muted(_table(0x2000, 16, stride=4), [0]))
    assert T.render(dirty, {}, 4)[2].planes["filter"] == (0, 4) and T.gate(dirty, {}, 4) is None


def test_a_computed_filter_write_is_not_the_pulse_accumulator():
    """The sweep is a pw generator: $17 must not be swept because ``reg % 7`` says 2."""
    mem, sweep = _sweepprog()
    stmts = list(sweep.procs[0][3])
    stmts.insert(2, ("st", ("const", 0xD417, 2), ("loc", "t")))
    prog = frameprog.FrameProgram(
        0x1000, 0x0F00, decls=sweep.data_decls, mem0=mem, procs=[(0x1000, [], [], stmts)]
    )
    cov = T.render(prog, {}, 6)[2]
    assert cov.planes["pw"] == (6, 6) and cov.planes["filter"] == (0, 6)
    assert T.gate(prog, {}, 6) is None


def _mutated(prog, reg, transfer, nframes=4):
    """The law's verdict with the plane-``reg`` node's transfer replaced."""
    nodes = list(T._graph(prog, None, *T._observe(prog, {}, nframes))[0].nodes)
    i = next(i for i, g in enumerate(nodes) if g.route == ("plane", reg))
    old = nodes[i].transfer
    nodes[i] = nodes[i]._replace(transfer=transfer(old))
    return F.diff(T.eval_graph(T.Graph(nodes), nframes), T.oracle(prog, {}, nframes))


def test_mutation_a_wrong_filter_byte_or_row_is_detected():
    """The filter emit is a declared byte at a recovered row: perturb either and the law fails."""
    _mem, prog = _filterprog()
    assert _mutated(prog, 0x15, lambda t: t) is None
    wrong = _mutated(prog, 0x15, lambda t: ("SELECT", (t[1][0] ^ 0xFF,) + t[1][1:], t[2]))
    assert wrong is not None and wrong.section == "filter"
    row = _mutated(prog, 0x15, lambda t: ("SELECT", t[1], t[2][1:] + t[2][:1]))
    assert row is not None and row.section == "filter"


# ---- 3e. the trigger domain: a DIV whose divisor is a declared reload -------------
_COUNTER = 0x0801


def _dividerprog(n, reload_=None, decl=None, base=0x2000, nrows=4):
    """``(image, program)`` ticking every ``n`` frames, reading an AD lane on the tick.

    The counter steps down, reloads with ``reload_`` (the immediate ``n`` by default)
    and only the tick body writes the register, so the AD stream's edges are the
    divider's ticks."""
    mem, table = _bank(base, nrows, 4, {2: list(range(0x11, 0x11 + nrows))})
    mem[_COUNTER] = n
    dec = ("op", "INT_SUB", (("mem", ("const", _COUNTER, 2), 1), ("const", 1, 1)), 1)
    tick = [
        ("st", ("const", _COUNTER, 2), ("const", n, 1) if reload_ is None else reload_),
        ("asg", "i", ("mem", ("const", 0x0800, 2), 1)),
        ("st", ("const", 0xD405, 2), _sel(base, 2, "i")),
        (
            "st",
            ("const", 0x0800, 2),
            ("op", "INT_ADD", (("mem", ("const", 0x0800, 2), 1), ("const", 4, 1)), 1),
        ),
    ]
    stmts = [
        ("asg", "c", dec),
        ("st", ("const", _COUNTER, 2), ("loc", "c")),
        ("if", "if", ("op", "INT_EQUAL", (("loc", "c"), ("const", 0, 1)), 1), tick, []),
        ("ret",),
    ]
    return mem, frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl or table], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def _fires(prog, nframes=8):
    """``(DIV divisors, EDGE count streams, Coverage)`` for a rendered program."""
    graph = T._graph(prog, None, *T._observe(prog, {}, nframes))[0]
    return (
        [g.transfer[1] for g in graph.nodes if g.transfer[0] == "DIV"],
        [g.transfer[1] for g in graph.nodes if g.transfer[0] == "EDGE"],
        T.render(prog, {}, nframes)[2],
    )


def test_a_declared_reload_generates_the_edge_stream_as_a_div():
    """The divisor is the immediate the play code reloads: the EDGE floor is replaced."""
    _mem, prog = _dividerprog(2)
    assert T.lift(prog).divisors == (2,)
    divs, edges, cov = _fires(prog)
    assert divs == [2] and not edges
    assert cov.triggers == (4, 4) and T.gate(prog, {}, 8) is None
    assert cov.planes["ad"] == (4, 4)  # the value partition is the same either way


def test_the_trigger_partition_is_reported_apart_from_the_value_partition():
    """Two domains, two numbers: fires are never counted among the emits."""
    frames = _frames([[(0, 1)], [(0, 2)]])
    assert T.coverage(T.from_frames(frames), 2).triggers == (0, 0)  # RAW fires nothing
    g = T.Graph([T.edge((1, 1)), T.select((0x11, 0x22), (0, 1), ("event", 0), 5)])
    cov = T.coverage(g, 2)
    assert cov.triggers == (0, 2) and cov.interp == 2 and cov.total == 2
    assert T.coverage(T.Graph([T.div(2), T.lookup([9], ("event", 0), 5)]), 4).triggers == (2, 2)


def test_a_divisor_the_play_code_does_not_declare_is_refused():
    """Same edges, same period — but a divisor no recovered clock reloads is not one."""
    undeclared = ("mem", ("const", 0x0F00, 2), 1)  # holds 2, but no declaration covers it
    mem, prog = _dividerprog(2, reload_=undeclared)
    mem[0x0F00] = 2
    assert T.lift(prog).divisors == ()
    divs, edges, cov = _fires(prog)
    assert not divs and edges == [(0, 1, 0, 1, 0, 1, 0, 1)]  # the floor, unchanged
    assert cov.triggers == (0, 4) and cov.planes["ad"] == (4, 4)


def test_a_reload_at_a_play_written_offset_is_not_a_divisor():
    """``mut`` holds here too: a reload cell the play phase writes is runtime state."""
    cell = ("mem", ("const", 0x2001, 2), 1)
    mem, prog = _dividerprog(2, reload_=cell, decl=_muted(_table(0x2000, 16, stride=4), [1]))
    mem[0x2001] = 2
    assert T.lift(prog).divisors == () and _fires(prog)[0] == []
    clean, prog = _dividerprog(2, reload_=cell)
    clean[0x2001] = 2  # the same cell, at an offset the declaration does not name `mut`
    assert T.lift(prog).divisors == (2,) and _fires(prog)[0] == [2]


def test_a_divisor_of_one_divides_nothing_and_is_refused():
    """A stream firing every frame is the root cadence, not a recovered divider."""
    _mem, prog = _dividerprog(1, nrows=8)
    assert T.lift(prog).divisors == ()
    divs, edges, cov = _fires(prog)
    assert not divs and edges == [(1,) * 8] and cov.triggers == (0, 8)


def test_a_div_fires_where_the_divisor_says_and_nowhere_else():
    """The check is exact in both directions: a missing tick refuses as loudly as a spare."""
    assert T._generates((0, 1, 0, 1), 2) and not T._generates((0, 1, 1, 1), 2)
    assert not T._generates((0, 1, 0, 0), 2) and not T._generates((0, 1, 0, 1), 4)
    assert T._clock_node((0, 1, 0, 1), (2,)) == T.div(2)
    assert T._clock_node((0, 1, 0, 1), (3, 4)) == T.edge((0, 1, 0, 1))


def test_mutation_a_wrong_divisor_is_detected():
    """The law verifies the divisor: perturb it and the fires move off the observation."""
    _mem, prog = _dividerprog(2)
    graph = T._graph(prog, None, *T._observe(prog, {}, 8))[0]
    assert F.diff(T.eval_graph(graph, 8), T.oracle(prog, {}, 8)) is None
    i = next(i for i, g in enumerate(graph.nodes) if g.transfer[0] == "DIV")
    for wrong in (("DIV", 3), ("DIV", 1)):
        graph.nodes[i] = graph.nodes[i]._replace(transfer=wrong)
        assert F.diff(T.eval_graph(graph, 8), T.oracle(prog, {}, 8)) is not None


def test_an_lfo_phase_is_no_divider_so_its_reload_is_no_divisor():
    """``_clocks`` separates a free ``inc`` from a ``dec``; only the divider declares one."""
    inc = ("op", "INT_ADD", (("mem", ("const", 0x1001, 2), 1), ("const", 1, 1)), 1)
    stmts = [
        ("st", ("const", 0x1001, 2), inc),
        ("st", ("const", 0x1001, 2), ("const", 6, 1)),
    ]
    prog = frameprog.FrameProgram(0x1000, 0x0F00, procs=[(0x1000, (), (), stmts)])
    assert [c.role for c in T._clocks(prog)] == ["lfo"]
    assert not T._divisors(prog, T._banks(prog))


# ---- 3f. one plane, two generators: the bit partition the store statement names ---
_MODE = [0x10, 0x20, 0x30, 0x40]


def _or(*terms):
    """``t0 | t1 | ...`` as a frameprog expression."""
    return ("op", "INT_OR", tuple(terms), 1)


def _maskprog(lane=None, decl=None, vol=("const", 0x0F, 1), base=0x2000, reg=0x18):
    """``(image, program)``: a declared mode lane ORed with a second field into ``reg``."""
    mem, table = _bank(base, 4, 4, {0: lane or _MODE})
    return mem, _rowprog(mem, decl or table, [(reg, _or(_sel(base, 0, "i"), vol))], step=4)


def _mask_nodes(prog, reg=0x18, nframes=4):
    """The graph's masked generators for ``reg``, in node order."""
    nodes = T._graph(prog, None, *T._observe(prog, {}, nframes))[0].nodes
    return [g for g in nodes if g.route[:2] == ("plane", reg) and len(g.route) > 2]


def test_a_register_two_generators_share_is_split_by_the_masks_the_text_names():
    """$18 is a declared mode nibble ORed with the store's own volume constant."""
    _mem, prog = _maskprog()
    assert T.gate(prog, {}, 4) is None
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["filter"] == (4, 4) and cov.classes["filter"]["mask"] == 4
    assert cov.classes["filter"]["lane"] == 0  # a masked write is its own class
    got = [(g.route[2], g.transfer) for g in _mask_nodes(prog)]
    assert [m for m, _t in got] == [0x0F, 0xF0]  # disjoint fields, the whole byte covered
    assert dict(got)[0x0F] == ("LOOKUP", (0x0F,))
    assert dict(got)[0xF0] == ("SELECT", tuple(_MODE), (0, 1, 2, 3))


def test_two_generators_of_one_register_must_own_disjoint_bits():
    """Overlapping masks are two owners of one bit, which no node order resolves."""
    ok = [
        T.lookup((0x0F,), FRAME := T.FRAME, 0x18, mask=0x0F),
        T.lookup((0x10,), FRAME, 0x18, 0xF0),
    ]
    assert T.eval_graph(T.Graph(ok), 1) == F.canonical([[(0x18, 0x1F)]])
    for bad in ((0x0F, 0x18), (0x0F, 0xFF), (0xF0, 0)):
        nodes = [T.lookup((0,), T.FRAME, 0x18, mask=m) for m in bad]
        with pytest.raises(T.TrackerError):
            T.eval_graph(T.Graph(nodes), 1)
    same = [T.lookup((1,), T.FRAME, 0x18, mask=0x0F) for _k in range(2)]
    assert T.eval_graph(T.Graph(same), 1) is not None  # equal masks are one owner over time


def test_a_masked_write_is_one_emit_at_the_last_field_holders_position():
    """The order-preserved section takes one write per group, where its last field fires."""
    order = [
        T.select((0x41,), (0,), T.FRAME, 4, mask=0xFE),
        T.lookup((0x00, 0x01), T.FRAME, 4, mask=0x01),
        T.lookup((0x11, 0x22), T.FRAME, 5),
    ]
    assert _diff(T.Graph(order), _frames([[(4, 0x40), (5, 0x11)], [(4, 0x41), (5, 0x22)]])) is None
    swapped = T.Graph([order[1], order[0], order[2]])  # the ctrl write moves ahead of nothing
    assert _diff(swapped, _frames([[(4, 0x40), (5, 0x11)], [(4, 0x41), (5, 0x22)]])) is None
    assert (
        _diff(T.Graph(order), _frames([[(5, 0x11), (4, 0x40)], [(5, 0x22), (4, 0x41)]])) is not None
    )


def test_the_masks_a_store_statement_names_and_the_ones_it_does_not():
    """A constant owns its bits, an AND-immediate its mask, a shift moves it."""
    lane = ("mem", ("const", 0x2000, 2), 1)
    shift = (
        "op",
        "INT_LEFT",
        (("op", "INT_AND", (lane, ("const", 0x0F, 1)), 1), ("const", 4, 1)),
        1,
    )
    assert T._partition(_or(shift, ("const", 0x0F, 1)), {}) == (
        (False, 0xF0, None),
        (True, 0x0F, 0x0F),
    )
    assert T._partition(_or(("op", "INT_AND", (lane, ("const", 0xF0, 1)), 1), lane), {}) == (
        (False, 0xF0, None),
        (False, 0x0F, None),
    )
    for bad in (
        _or(lane, lane),  # two terms, neither masked
        _or(("const", 0x0F, 1), ("const", 0x18, 1)),  # overlapping bits
        _or(("op", "INT_AND", (lane, ("const", 0xF0, 1)), 1), ("const", 0x0C, 1)),  # $03 unowned
        _or(lane, ("const", 0xFF, 1)),  # the constant leaves the other term nothing
        lane,  # not an OR at all
    ):
        assert T._partition(bad, {}) is None


def test_a_mask_the_program_text_does_not_name_is_refused():
    """Two variable terms name no partition: the write stays whole in RAW."""
    mem, table = _bank(0x2000, 4, 4, {0: _MODE, 1: [0x0F] * 4})
    both = _rowprog(mem, table, [(0x18, _or(_sel(0x2000, 0, "i"), _sel(0x2000, 1, "i")))], step=4)
    assert T.render(both, {}, 4)[2].planes["filter"] == (0, 4) and T.gate(both, {}, 4) is None
    assert not T._partitions(both)
    _m, named = _maskprog()  # the same bytes, with the second field a program constant
    assert T._partitions(named) == {0x18: [((False, 0xF0, None), (True, 0x0F, 0x0F))]}
    assert T.render(named, {}, 4)[2].planes["filter"] == (4, 4)


def test_a_field_no_declaration_holds_leaves_the_register_residual():
    """Every field must be sourced, over exactly the bits the text gives it."""
    _mem, moved = _maskprog(decl=_muted(_table(0x2000, 16, stride=4), [0]))
    assert T.render(moved, {}, 4)[2].planes["filter"] == (0, 4)  # a `mut` lane is not const data
    _m, spill = _maskprog(lane=[0x11, 0x22, 0x33, 0x44])  # the lane sets bits the constant owns
    assert T.render(spill, {}, 4)[2].planes["filter"] == (0, 4) and T.gate(spill, {}, 4) is None


def test_mutation_a_wrong_mask_or_a_wrong_field_byte_is_detected():
    """The law verifies the partition: move a mask or a field's byte and it fails."""
    _mem, prog = _maskprog()
    graph = T._graph(prog, None, *T._observe(prog, {}, 4))[0]
    assert F.diff(T.eval_graph(graph, 4), T.oracle(prog, {}, 4)) is None
    for i, g in enumerate(graph.nodes):
        if g.route[:2] != ("plane", 0x18) or len(g.route) < 3:
            continue
        mask = g.route[2]
        bit, t = mask & -mask, g.transfer  # the lowest bit this field owns
        wrong = (
            g._replace(route=("plane", 0x18, mask & ~bit)),
            g._replace(transfer=(t[0], tuple(b ^ bit for b in t[1])) + t[2:]),
        )
        for bad in wrong:
            nodes = list(graph.nodes)
            nodes[i] = bad
            assert F.diff(T.eval_graph(T.Graph(nodes), 4), T.oracle(prog, {}, 4)) is not None


def test_the_order_preserved_section_does_not_take_a_masked_write():
    """ctrl writes are a sequence, not a partition of one byte: the group is refused there."""
    mem, prog = _maskprog(reg=0x04)
    assert T._partitions(prog) == {4: [((False, 0xF0, None), (True, 0x0F, 0x0F))]}
    cov = T.render(prog, {}, 4)[2]
    assert cov.planes["ctrl"] == (0, 4) and T.gate(prog, {}, 4) is None
    assert mem[0x2000] == 0x10  # the field is declared; the section is what refuses it


# ---- 3g. the relative route: a declared delta over a base the statement names -----
_DELTA = [0x01, 0x02, 0x03, 0x04]
_BASE_LANE = [0x40, 0x50, 0x60, 0x70]
_MIRROR = 0x0900
_RES = 0x17  # a filter register no other rule claims: not an accumulator, not a partition


def _bin(a, b, op="INT_ADD"):
    """``a op b`` as a frameprog expression."""
    return ("op", op, (a, b), 1)


def _relprog(value, mem=None, decl=None, reg=_RES, lanes=None):
    """``(image, program)``: one store of a binary-op value into ``reg``, per row."""
    if mem is None:
        mem, table = _bank(0x2000, 4, 4, lanes or {0: _DELTA, 1: _BASE_LANE})
        decl = decl or table
    return mem, _rowprog(mem, decl, [(reg, value)], step=4)


def _prevprog(delta=(0x2000, 0), reg=_RES, lanes=None, cell=_MIRROR):
    """A store adding a declared byte to a cell the text stores that same value into.

    The cell is then the plane's mirror, so reading it *is* the previous emit."""
    mem, decl = _bank(0x2000, 4, 4, lanes or {0: _DELTA})
    mem[cell] = 0x20  # the plane starts where no declared byte stands, so §4b declines it
    val = _bin(("mem", ("const", cell, 2), 1), _sel(*delta, "i"))
    step = ("op", "INT_ADD", (("mem", ("const", 0x0800, 2), 1), ("const", 4, 1)), 1)
    stmts = [
        ("asg", "i", ("mem", ("const", 0x0800, 2), 1)),
        ("st", ("const", 0xD400 + reg, 2), val),  # the register takes the value first
        ("st", ("const", cell, 2), val),
        ("st", ("const", 0x0800, 2), step),
        ("ret",),
    ]
    return mem, frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def _rel_nodes(prog, nframes=4):
    """The graph's relatively-routed generators, in node order."""
    nodes = T._graph(prog, None, *T._observe(prog, {}, nframes))[0].nodes
    return [g for g in nodes if g.route[0] == "rel"]


def _diag(prog, nframes=4):
    """``(Coverage, refusal histogram)`` for a hermetic program."""
    diag = Counter()
    return T.render(prog, {}, nframes, diag)[2], diag


def test_a_declared_delta_over_a_program_constant_base():
    """`lane[i] + $10` is a relative route: the delta is declared, the base is the text's."""
    _mem, prog = _relprog(_bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1)))
    assert T.gate(prog, {}, 4) is None
    cov = _diag(prog)[0]
    assert cov.planes["filter"] == (4, 4) and cov.classes["filter"]["rel"] == 4
    assert cov.classes["filter"]["lane"] == 0  # a relative emit is neither lane nor imm
    got = _rel_nodes(prog)
    assert [g.route[3:] for g in got] == [("ADD", ("const", 0x10))]
    assert got[0].transfer == ("SELECT", tuple(_DELTA), (0, 1, 2, 3))


def test_a_subtracted_constant_base_is_the_same_route_with_the_base_negated():
    """`lane[i] - $10` and `lane[i] + $F0` are one byte-wide route, not two."""
    _mem, prog = _relprog(_bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1), "INT_SUB"))
    assert T.gate(prog, {}, 4) is None
    assert _diag(prog)[0].classes["filter"]["rel"] == 4
    assert [g.route[3:] for g in _rel_nodes(prog)] == [("ADD", ("const", 0xF0))]


def _genprog():
    """``base[i] + delta[i]`` over two separate declarations, so the two lanes are distinct."""
    mem, delta = _bank(0x2000, 4, 4, {0: _DELTA})
    for i, v in enumerate(_BASE_LANE):
        mem[0x3000 + 4 * i] = v
    val = _bin(_sel(0x3000, 0, "i"), _sel(0x2000, 0, "i"))
    stmts = [
        ("asg", "i", ("mem", ("const", 0x0800, 2), 1)),
        ("st", ("const", 0xD400 + _RES, 2), val),
        ("st", ("const", 0x0800, 2), _bin(("mem", ("const", 0x0800, 2), 1), ("const", 4, 1))),
        ("ret",),
    ]
    return mem, frameprog.FrameProgram(
        0x1000,
        0x0F00,
        decls=[delta, _table(0x3000, 16, stride=4)],
        mem0=mem,
        procs=[(0x1000, [], [], stmts)],
    )


def test_a_declared_delta_over_another_generators_value():
    """`base[i] + delta[i]`: the base generator supplies a value and does not write."""
    _mem, prog = _genprog()
    assert T.gate(prog, {}, 4) is None
    cov = _diag(prog)[0]
    assert cov.planes["filter"] == (4, 4)  # four writes, not eight: the base is consumed
    assert cov.classes["filter"]["rel"] == 4 and cov.classes["filter"]["lane"] == 0
    nodes = T._graph(prog, None, *T._observe(prog, {}, 4))[0].nodes
    rel = [i for i, g in enumerate(nodes) if g.route[0] == "rel"]
    assert len(rel) == 1 and nodes[rel[0]].route[3] == "ADD"
    base = nodes[rel[0]].route[4]
    assert base[0] == "node" and base[1] < rel[0]  # absolutes settle, relatives follow
    assert nodes[base[1]].transfer == ("SELECT", tuple(_BASE_LANE), (0, 1, 2, 3))


def test_a_declared_delta_over_the_planes_own_previous_value():
    """A cell the text stores the register's own value into is that plane's mirror."""
    _mem, prog = _prevprog()
    assert T.gate(prog, {}, 4) is None
    cov, diag = _diag(prog)
    assert T._mirrors(prog)[_MIRROR] == {_RES}
    assert cov.planes["filter"] == (3, 4)  # frame 0 has no previous value to combine with
    assert cov.classes["filter"]["rel"] == 3 and diag["rel_no_base"] == 1
    assert [g.route[3:] for g in _rel_nodes(prog)] == [("ADD", ("prev",))]


def _rammed(delta=0x0A00, cell=_MIRROR, reg=_RES):
    """`_prevprog`'s stream again, with the delta staged in a cell no declaration names."""
    mem, decl = _bank(0x2000, 4, 4, {0: _DELTA})
    mem[cell] = 0x20
    for i, v in enumerate(_DELTA):
        mem[delta + 4 * i] = v
    val = _bin(("mem", ("const", cell, 2), 1), _sel(delta, 0, "i"))
    step = ("op", "INT_ADD", (("mem", ("const", 0x0800, 2), 1), ("const", 4, 1)), 1)
    stmts = [
        ("asg", "i", ("mem", ("const", 0x0800, 2), 1)),
        ("st", ("const", 0xD400 + reg, 2), val),
        ("st", ("const", cell, 2), val),
        ("st", ("const", 0x0800, 2), step),
        ("ret",),
    ]
    return frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def test_a_delta_read_back_off_the_output_is_refused():
    """The same emitted stream, staged from a cell no declaration names, stays residual."""
    got = [w[1] for r in T.oracle(_prevprog()[1], {}, 4) for w in r[6]]
    fitted = _rammed()
    assert [w[1] for r in T.oracle(fitted, {}, 4) for w in r[6]] == got  # the identical stream
    assert T.gate(fitted, {}, 4) is None
    cov, diag = _diag(fitted)
    assert cov.planes["filter"] == (0, 4)  # the bytes agree; no declaration names them
    assert diag["rel_site_unnamed_base"] + diag["rel_site_no_declared_term"] == 1
    assert not diag["rel_fitted"]  # the site is refused, so there is nothing left to price


def test_what_a_fitted_delta_would_have_taken_is_counted_where_a_site_exists():
    """A named site whose declared byte predicts nothing prices the refusal it makes."""
    _mem, moved = _prevprog(lanes={0: [0x01, 0x02, 0x03, 0x04], 1: [9] * 4})
    cov, diag = _diag(moved)
    assert cov.planes["filter"] == (3, 4) and not diag["rel_fitted"]
    _m, zero = _relprog(_bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1)), lanes={0: [0] * 4})
    cov2, diag2 = _diag(zero)
    assert cov2.planes["filter"] == (0, 4)  # a delta of zero predicts nothing
    assert diag2["rel_zero_delta"] == 4 and T.gate(zero, {}, 4) is None


def test_a_base_the_program_text_does_not_name_is_refused():
    """A RAM cell the text never mirrors this register into names no base."""
    mem, decl = _bank(0x2000, 4, 4, {0: _DELTA})
    mem[0x0A00] = 0x10
    val = _bin(("mem", ("const", 0x0A00, 2), 1), _sel(0x2000, 0, "i"))
    _m, prog = _relprog(val, mem=mem, decl=decl)
    cov, diag = _diag(prog)
    assert cov.planes["filter"] == (0, 4) and diag["rel_site_unnamed_base"] == 1
    assert T.gate(prog, {}, 4) is None


def test_a_delta_at_a_mut_offset_is_refused():
    """A play-written offset is not const data, so its byte is not a declared delta."""
    mem, table = _bank(0x2000, 4, 4, {0: _DELTA})
    val = _bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1))
    _m, prog = _relprog(val, mem=mem, decl=_muted(table, [0]))
    assert T.render(prog, {}, 4)[2].planes["filter"] == (0, 4)
    assert T.gate(prog, {}, 4) is None


def test_the_order_preserved_section_takes_no_relative_route():
    """ctrl/AD/SR is a sequence of whole-byte writes, so a relative emit is refused there."""
    _mem, prog = _relprog(_bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1)), reg=4)
    cov, diag = _diag(prog)
    assert cov.planes["ctrl"] == (0, 4) and diag["rel_ord_section"] == 4
    assert T.gate(prog, {}, 4) is None


def test_the_composition_order_of_an_absolute_and_a_relative_route_is_checked():
    """A relative route names a base an earlier generator settles, or the graph is refused."""
    ok = [
        T.raw([[(0, 5)]]),
        T.lookup(
            (3,),
            T.FRAME,
            0,
        ),
    ]
    ok = [
        T.raw([[]]),
        T.lookup((0x40,), T.FRAME, 0),
        T.Generator(("LOOKUP", (3,)), T.FRAME, T.relative(0, "ADD", ("node", 1))),
    ]
    assert T.eval_graph(T.Graph(ok), 1) == F.canonical([[(0, 0x43)]])
    bad = [
        [T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(0, "ADD", ("prev",)))],
        [T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(0, "NAND", ("const", 1)))],
        [T.raw([[]]), T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(0, "ADD", ("node", 1)))],
        [ok[1], T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(1, "ADD", ("node", 0)))],
        [ok[1], T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(0, "ADD", ("node", 0), 0x0F))],
        [T.Generator(("LOOKUP", (1,)), T.FRAME, T.relative(0, "ADD", ("const", 0x100)))],
    ]
    for nodes in bad:
        with pytest.raises(T.TrackerError):
            T.eval_graph(T.Graph(nodes), 1)


def test_mutation_a_delta_on_the_wrong_base_or_a_wrong_delta_is_detected():
    """The law verifies the combination: move the base or the delta and it fails."""
    for _mem, prog in (
        _relprog(_bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1))),
        _relprog(_bin(_sel(0x2000, 1, "i"), _sel(0x2000, 0, "i"))),
        _prevprog(),
    ):
        graph = T._graph(prog, None, *T._observe(prog, {}, 4))[0]
        assert F.diff(T.eval_graph(graph, 4), T.oracle(prog, {}, 4)) is None
        i = next(k for k, g in enumerate(graph.nodes) if g.route[0] == "rel")
        g, t = graph.nodes[i], graph.nodes[i].transfer
        wrong = [
            g._replace(transfer=(t[0], tuple(b ^ 1 for b in t[1])) + t[2:]),
            g._replace(route=g.route[:4] + (("const", 0x7F),)),
        ]
        for bad in wrong:
            nodes = list(graph.nodes)
            nodes[i] = bad
            assert F.diff(T.eval_graph(T.Graph(nodes), 4), T.oracle(prog, {}, 4)) is not None


def test_the_relative_stream_is_generated_from_the_declared_byte():
    """Perturb the declaration and the whole emitted stream moves with it."""
    val = _bin(_sel(0x2000, 0, "i"), ("const", 0x10, 1))
    mem, prog = _relprog(val)
    assert [g.transfer[1] for g in _rel_nodes(prog)] == [tuple(_DELTA)]
    mem[0x2004] = 0x7E  # row 1 of the declared delta lane
    moved = _rowprog(mem, _table(0x2000, 16, stride=4), [(_RES, val)], step=4)
    assert [g.transfer[1] for g in _rel_nodes(moved)] == [(0x01, 0x7E, 0x03, 0x04)]
    assert T.gate(moved, {}, 4) is None


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
    _gt, ords, _lww, _acc = T._observe(prog, trace, nf)
    pre, post, refined = _instr(prog, ords)
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


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_freq_is_the_declared_pitch_table_at_a_recovered_row(sid, subtune):
    """`freq_hi = m_5429[t5]`: the store names $5428, so the row is the semitone index."""
    prog, trace, nf = _lifted(sid, subtune)
    tabs = T._tree_tables(prog, T._banks(prog))
    assert [b[:3] for b in tabs[0]] == [b[:3] for b in tabs[1]] == [(0x5428, 192, 2)]
    _gt, _ords, lww, _acc = T._observe(prog, trace, nf)
    sel = {r: t for _c, t, r in T._lww_streams(lww, tabs, prog.mem0)[0]}
    for v in range(3):
        lo, hi = sel[7 * v], sel[7 * v + 1]
        assert lo[1] == tuple(prog.mem0[0x5428 + 2 * i] for i in range(96))
        assert hi[1] == tuple(prog.mem0[0x5429 + 2 * i] for i in range(96))
        assert lo[2] == hi[2]  # one row stream per voice: the row is the note index
    cov = T.render(prog, trace, nf)[2]
    assert cov.classes["freq"]["lane"] > 600 and cov.classes["freq"]["imm"] == 0


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_pw_lo_is_refused_because_the_play_phase_writes_that_lane(sid, subtune):
    """$5591's +0 lane is `mut`: the play code writes it back, so it is not const data."""
    prog, trace, nf = _lifted(sid, subtune)
    decl = next(d for d in prog.data_decls if d["base"] == 0x5591)
    assert decl["stride"] == 8 and decl["mut"] == [0]
    _gt, _ords, lww, _acc = T._observe(prog, trace, nf)
    streams = T._lww_streams(lww, T._tree_tables(prog, T._banks(prog)), prog.mem0)[0]
    assert {r % 7 for _c, _t, r in streams if r % 7 in (2, 3)} == {3}  # pw_hi at +1 only
    cov = T.render(prog, trace, nf)[2]
    assert cov.planes["pw"] == (205, 245)  # 53 lane reads at +1, the rest the swept +0
    pw = cov.classes["pw"]
    assert pw["lane"] == 53 and pw["imm"] == 0  # nothing reads the +0 lane as const
    assert pw["lane"] + pw["ramp"] + pw["seed"] == cov.planes["pw"][0]


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_pw_sweep_is_generated_from_the_declared_step_lane(sid, subtune):
    """The +6 lane of the `$5591` bank is the step; the sweep is a RAMP over it."""
    prog, trace, nf = _lifted(sid, subtune)
    cov = T.render(prog, trace, nf)[2]
    assert cov.planes["pw"][0] > 150  # 53 declared-lane emits before the sweep is generated
    assert T._accumulators(prog, T._acc_sites(prog)[2]) == {2: (0x5591, 1)}
    assert cov.classes["pw"]["ramp"] > 100 and cov.classes["pw"]["seed"] < 20
    graph = T._graph(prog, None, *T._observe(prog, trace, nf))[0]
    ramps = [g.transfer for g in graph.nodes if g.transfer[0] == "RAMP"]
    assert ramps and all(t[2] == prog.mem0[0x55A7] for t in ramps)  # the declared step byte
    lane = {prog.mem0[0x5591 + 8 * r + 6] for r in range(263 // 8)}
    assert {t[2] for t in ramps} <= lane  # every step is a byte of the declared +6 lane


@pytest.mark.parametrize("sid,subtune", _tune("Artura", "Daglish_Ben"))
def test_artura_adsr_through_the_sid_register_mirror(sid, subtune):
    """ADSR staged in a per-voice SID mirror still reads as the declared bank."""
    prog, trace, nf = _lifted(sid, subtune)
    assert 0xEFC1 in T.lift(prog).instruments  # the store site reads the mirror cell
    _gt, ords, _lww, _acc = T._observe(prog, trace, nf)
    pre, post, refined = _instr(prog, ords)
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


@pytest.mark.parametrize("sid,subtune", _tune("64_Forever", "Linus"))
def test_64_forever_filter_registers_read_declared_cells(sid, subtune):
    """A real filter tail off the store statement: cutoff hi and resonance, both declared."""
    prog, trace, nf = _lifted(sid, subtune)
    assert T.gate(prog, trace, nf) is None
    cov = T.render(prog, trace, nf)[2]
    assert cov.planes["filter"] == (384, 598)
    assert cov.classes["filter"] == {
        "lane": 384,
        "gate": 0,
        "imm": 0,
        "ramp": 0,
        "seed": 0,
        "mask": 0,
        "rel": 0,
    }
    _gt, _ords, lww, _acc = T._observe(prog, trace, nf)
    streams = T._lww_streams(lww, T._tree_tables(prog, T._banks(prog)), prog.mem0)[0]
    got = {r: t for _c, t, r in streams if r > T._VOICE_HI}
    assert set(got) == {0x16, 0x17}  # $15 and $18 name no declaration and stay residual
    cells = [0x19C5 + t[2][0] for t in (got[0x16], got[0x17])]
    assert cells == [0x1A08, 0x1A07]  # two cells of one declared table, recovered per register
    assert [prog.mem0[c] for c in cells] == [0x06, 0xF7]


def _arrangement(orderlist, patterns, rows_per_pattern, nframes):
    """An orderlist indexing a pattern table, the shape §7.4 names.

    n0 the row clock, n1 the orderlist (an index route), n2 the pattern it selects."""
    beat = T.Generator(("DIV", rows_per_pattern), T.FRAME, ("fire",))
    order = T.indexer(("LOOKUP", tuple(orderlist)), ("event", 0))
    pat = T.select(patterns, ("node", 1), T.FRAME, 0x18)
    return T.Graph([beat, order, pat]), nframes


def _emitted(graph, nframes):
    """Per-frame value written by an arrangement graph, None where none is."""
    out = []
    for fr in T.eval_graph(graph, nframes):
        vals = [v for slot in fr for (_reg, v) in slot]
        out.append(vals[0] if vals else None)
    return out


def test_index_route_expresses_an_orderlist():
    """A pattern's row is the orderlist's emit, and the loop is the modulo wrap."""
    got = _emitted(*_arrangement([3, 5, 1], tuple(range(8)), 4, 16))
    assert got[3:7] == [3, 3, 3, 3]  # the orderlist holds while rows advance
    assert got[7:11] == [5, 5, 5, 5] and got[11:15] == [1, 1, 1, 1]
    assert got[15] == 3  # wrapped by _emit's modulo: no back-edge machinery needed


def test_nothing_is_written_before_the_orderlist_speaks():
    """DIV(n) fires at n-1, so the phase is the arrangement's (docs 8).

    The graph emits nothing rather than inventing entry 0, which is why a phase
    field belongs to this layer and not to the divider."""
    assert _emitted(*_arrangement([3, 5, 1], tuple(range(8)), 4, 16))[:3] == [None] * 3


def test_index_source_must_precede_its_reader():
    """A row source later than its reader would need a value the frame has not made."""
    pat = T.select((1, 2, 3), ("node", 1), T.FRAME, 0x18)
    order = T.indexer(("LOOKUP", (0, 1)), T.FRAME)
    with pytest.raises(T.TrackerError, match="not an earlier node"):
        T.eval_graph(T.Graph([pat, order]), 4)


def test_row_source_must_route_to_an_index():
    """A plane generator's write is a byte, not a row: it cannot be an index source."""
    other = T.lookup((0, 1), T.FRAME, 0x04)
    pat = T.select((1, 2, 3), ("node", 0), T.FRAME, 0x18)
    with pytest.raises(T.TrackerError, match="does not route to an index"):
        T.eval_graph(T.Graph([other, pat]), 4)


def test_index_route_without_a_reader_is_dead():
    """A generator that neither writes a plane nor is read explains nothing."""
    order = T.indexer(("LOOKUP", (0, 1)), T.FRAME)
    with pytest.raises(T.TrackerError, match="has no reader"):
        T.eval_graph(T.Graph([order]), 4)


def test_generated_row_out_of_range_drops_the_write():
    """An index past the table emits nothing, so the law fails rather than wrapping."""
    order = T.indexer(("LOOKUP", (9,)), T.FRAME)
    pat = T.select((1, 2), ("node", 0), T.FRAME, 0x18)
    assert T.eval_graph(T.Graph([order, pat]), 3) == F.canonical([[], [], []])


def test_mutation_a_moved_orderlist_entry_changes_the_projection():
    """Mutation evidence: one wrong orderlist entry must move the record."""
    good, n = _arrangement([3, 5, 1], tuple(range(8)), 4, 16)
    bad, _n = _arrangement([3, 4, 1], tuple(range(8)), 4, 16)
    assert T.eval_graph(good, n) != T.eval_graph(bad, n)


def _transposed(trans, notes, table, rows_per, nframes):
    """A pattern note column read at a declared transpose: the index-domain relative."""
    beat = T.Generator(("DIV", rows_per), T.FRAME, ("fire",))
    shift = T.indexer(("LOOKUP", tuple(trans)), ("event", 0))
    note = T.indexer(("LOOKUP", tuple(notes)), T.FRAME)
    pitch = T.select(table, ("rel", "ADD", 2, ("node", 1)), T.FRAME, 0x01)
    return T.Graph([beat, shift, note, pitch]), nframes


def test_a_relative_row_carries_a_transpose():
    """A transpose shifts the row a pitch table is read at, not the byte it yields."""
    got = _emitted(*_transposed([0, 12], [1, 3, 5, 7], tuple(range(100, 140)), 4, 10))
    assert got[3:7] == [107, 101, 103, 105]  # transpose 0: the note column itself
    assert got[7:10] == [119, 113, 115]  # transpose 12: every row shifted by an octave


def test_a_relative_row_may_shift_by_a_declared_constant():
    """A fixed transpose needs no generator of its own."""
    note = T.indexer(("LOOKUP", (0, 1)), T.FRAME)
    pitch = T.select(tuple(range(20)), ("rel", "ADD", 0, ("const", 5)), T.FRAME, 0x01)
    assert _emitted(T.Graph([note, pitch]), 4) == [5, 6, 5, 6]


def test_a_relative_row_refuses_an_unknown_operation():
    """The operation comes from the store's own operator, not an invented one."""
    note = T.indexer(("LOOKUP", (0,)), T.FRAME)
    pitch = T.select((1, 2), ("rel", "MUL", 0, ("const", 0)), T.FRAME, 0x01)
    with pytest.raises(T.TrackerError, match="unknown relative row operation"):
        T.eval_graph(T.Graph([note, pitch]), 2)


def test_both_sources_of_a_relative_row_must_be_earlier_index_nodes():
    """Either half arriving late would need a value the frame has not made."""
    note = T.indexer(("LOOKUP", (0,)), T.FRAME)
    pitch = T.select(tuple(range(20)), ("rel", "ADD", 0, ("node", 2)), T.FRAME, 0x01)
    shift = T.indexer(("LOOKUP", (1,)), T.FRAME)
    with pytest.raises(T.TrackerError, match="not an earlier node"):
        T.eval_graph(T.Graph([note, pitch, shift]), 2)


def test_a_transposed_row_past_the_table_drops_the_write():
    """A shift off the end of the pitch table emits nothing rather than wrapping."""
    note = T.indexer(("LOOKUP", (1,)), T.FRAME)
    pitch = T.select((7, 8), ("rel", "ADD", 0, ("const", 40)), T.FRAME, 0x01)
    assert _emitted(T.Graph([note, pitch]), 2) == [None, None]


def test_mutation_a_wrong_transpose_changes_the_projection():
    """Mutation evidence: the shift must be the declared one."""
    good, n = _transposed([0, 12], [1, 3], tuple(range(100, 140)), 4, 12)
    bad, _n = _transposed([0, 11], [1, 3], tuple(range(100, 140)), 4, 12)
    assert T.eval_graph(good, n) != T.eval_graph(bad, n)
