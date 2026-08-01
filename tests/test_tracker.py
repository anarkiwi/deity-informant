"""tracker: the generator primitive, declared-table pitch recovery, and the law."""

import json
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


_LONG = 1200  # frames enough for a tune to reach past its opening bars


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


def test_div_fires_a_downstream_table_on_its_edge():
    """A DIV clock is the root; the table advances only on its Fire edge."""
    nodes = [T.div(2), T.lookup([0x11, 0x22], ("event", 0), 0)]
    recs = T.eval_graph(T.Graph(nodes), 4)
    got = [[w for sec in rec for w in sec] for rec in recs]
    assert got == [[], [(0, 0x11)], [], [(0, 0x22)]]  # fires on frames 1 and 3


def test_ramp_wraps_at_its_bound():
    nodes = [T.ramp(0xFE, 1, 0x100, T.FRAME, 2)]
    recs = T.eval_graph(T.Graph(nodes), 4)
    vals = [w[1] for rec in recs for sec in rec for w in sec]
    assert vals == [0xFE, 0xFF, 0x00, 0x01]


def test_a_pair_routed_ramp_carries_into_its_high_register():
    """One 16-bit emit writes both halves; the carry is the word's own, mask applied."""
    nodes = [T.Generator(("RAMP", 0x00F0, 0x20, 0x10000, ()), T.FRAME, T.pair(2, 3, 0x7F))]
    recs = T.eval_graph(T.Graph(nodes), 3)
    got = [dict(w for sec in rec for w in sec) for rec in recs]
    assert got == [{2: 0xF0, 3: 0x00}, {2: 0x10, 3: 0x01}, {2: 0x30, 3: 0x01}]
    cov = T.coverage(T.Graph(nodes), 3)
    assert cov.interp == 6  # two register writes per tick


def test_mutation_a_wrong_pair_step_moves_both_halves():
    """The pair is generated: perturb the step and the whole stream moves."""
    good = T.Graph([T.Generator(("RAMP", 0x00F0, 0x20, 0x10000, ()), T.FRAME, T.pair(2, 3, 0xFF))])
    base = T.eval_graph(good, 3)
    bad = T.Graph([T.Generator(("RAMP", 0x00F0, 0x21, 0x10000, ()), T.FRAME, T.pair(2, 3, 0xFF))])
    assert F.diff(T.eval_graph(bad, 3), base) is not None


def test_a_pair_route_refuses_a_masked_owner_on_either_register():
    """A pair owns both whole bytes, so a masked generator on one is two owners of a bit."""
    nodes = [
        T.Generator(("RAMP", 0, 1, 0x10000, ()), T.FRAME, T.pair(2, 3, 0xFF)),
        T.lookup([0x0F], T.FRAME, 3, mask=0x0F),
    ]
    with pytest.raises(T.TrackerError):
        T.eval_graph(T.Graph(nodes), 2)


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


def test_a_table_read_straight_through_is_a_select_at_identity_rows():
    """`LOOKUP(seq)` was `SELECT(seq, identity)`: one transfer, not two (docs 2)."""
    seq = (3, 9, 4, 7)
    straight = T.Graph([T.lookup(seq, T.FRAME, 0)])
    rowed = T.Graph([T.select(seq, tuple(range(len(seq))), T.FRAME, 0)])
    assert straight.nodes[0].transfer == ("SELECT", seq, ())
    assert T.eval_graph(straight, 9) == T.eval_graph(rowed, 9)
    assert [T._emit(straight.nodes[0], k) for k in range(1, 10)] == [3, 9, 4, 7] * 2 + [3]


def test_mutation_the_evidence_split_is_the_class_not_the_transfer():
    """One transfer serves both, so `imm` vs `lane` must ride the stream's own class."""
    lane = ((1, 1), ("SELECT", (0x11, 0x22), (0, 1)), 5, "lane", None)
    imm = ((1, 1), ("SELECT", (0,), ()), 5, "imm", None)
    assert lane[1][0] == imm[1][0]  # the transfer no longer tells the two apart
    assert (
        T.select((0x11, 0x22), (0, 1), T.FRAME, 5).transfer[0] == T.lookup((0,), T.FRAME, 5)[0][0]
    )
    got = T._classes([lane, imm])["ad"]
    assert (got["lane"], got["imm"]) == (2, 2)
    swapped = T._classes([lane[:3] + ("imm", None), imm[:3] + ("lane", None)])["ad"]
    assert (swapped["lane"], swapped["imm"]) == (0, 2)


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
        T.eval_graph(T.Graph([T.Generator(("SELECT", (1,), ()), ("nope",), ("plane", 0))]), 1)
    with pytest.raises(T.TrackerError, match="unknown route"):
        T.eval_graph(T.Graph([T.Generator(("SELECT", (1,), ()), T.FRAME, ("nope",))]), 1)
    with pytest.raises(T.TrackerError, match="no value emit"):
        T.eval_graph(T.Graph([T.Generator(("DIV", 1), T.FRAME, ("plane", 0))]), 1)
    with pytest.raises(T.TrackerError, match="no edge emit"):
        T.eval_graph(T.Graph([T.Generator(("SELECT", (1,), ()), T.FRAME, ("fire",))]), 1)
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


def test_a_row_read_is_paired_with_the_edge_that_produced_it():
    """Three rows cut inside one frame read three rows, not the last one three times."""
    nodes = [
        T.edge((3,)),
        T.indexer(("SELECT", (2, 0, 1), ()), ("event", 0)),
        T.select((0x11, 0x22, 0x33), ("node", 1), ("event", 0), 5),
    ]
    got = [w[1] for rec in T.eval_graph(T.Graph(nodes), 1) for sec in rec for w in sec]
    assert got == [0x33, 0x11, 0x22]  # the index node's own first, second and third emit


def test_an_index_node_that_does_not_fire_holds_what_it_last_emitted():
    """A cell the machine did not rewrite still holds its byte, and so does an index node."""
    nodes = [
        T.edge((1, 0, 1)),
        T.edge((1, 1, 1)),
        T.indexer(("SELECT", (0, 2), ()), ("event", 0)),
        T.select((0x11, 0x22, 0x33), ("node", 2), ("event", 1), 5),
    ]
    got = [w[1] for rec in T.eval_graph(T.Graph(nodes), 3) for sec in rec for w in sec]
    assert got == [0x11, 0x11, 0x33]  # frame 1 reads the row frame 0 left


def test_a_divider_whose_divisor_is_another_generators_emit():
    """A period no constant names: the row's own duration field, reloaded at every tick."""
    nodes = [
        T.div(1),
        T.indexer(("SELECT", (2, 3, 1), ()), ("event", 2)),
        T.div(("node", 1), ("event", 0), phase=1),
        T.select((0x10, 0x20, 0x30), (), ("event", 2), 5),
    ]
    recs = T.eval_graph(T.Graph(nodes), 20)
    at = [f for f, r in enumerate(recs) for sec in r for _w in sec]
    assert [b - a for a, b in zip(at, at[1:])] == [2, 3, 1, 2, 3, 1, 2, 3, 1]


def test_a_generated_divisor_must_name_an_index_node():
    """The divisor is a value, so it comes from an index node exactly as a row does."""
    good = [T.indexer(("SELECT", (2,), ()), T.FRAME), T.div(("node", 0), T.FRAME)]
    assert T.eval_graph(T.Graph(good), 4) is not None
    for bad in (("node", 9), ("node", 1), ("prev",)):
        nodes = list(good)
        nodes[1] = nodes[1]._replace(transfer=("DIV", bad, 0))
        with pytest.raises(T.TrackerError):
            T.eval_graph(T.Graph(nodes), 4)


def test_a_turning_ramp_names_a_bound_to_turn_in():
    """`RAMP` carries a turn field; a turn with no wrap to turn in is not evaluable."""
    for bad in (("RAMP", 0, 1, 0x100), ("RAMP", 0, 1, 0, (1, 2)), ("RAMP", 0, 1, 0x100, (1,))):
        with pytest.raises(T.TrackerError):
            T.eval_graph(T.Graph([T.Generator(bad, T.FRAME, T.plane(0))]), 4)


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
    got = {r: t for _c, t, r, *_e in post}
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
    assert not pre and sorted(s[3] for s in post) == ["imm", "lane"]
    (imm,) = [s for s in post if s[3] == "imm"]
    assert imm[1] == ("SELECT", (0,), ()) and imm[0] == (0, 1) and resid == [(), ()]
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
        "arr": 0,
    }
    _gt, ords, _lww, _acc = T._observe(prog, {}, 4)
    _pre, post, refined = _instr(prog, ords)
    assert refined == {4}
    (t,) = [t for _c, t, r, *_e in post if r == 4]
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
    got = {r: t for _c, t, r, *_e in streams}
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


def _spillprog(beyond=0x2004):
    """``(image, program)``: one store's index runs past its table into a second one."""
    mem, decl = _bank(0x2000, 4, 1, {0: [0x10, 0x21, 0x32, 0x43]})
    mem[beyond : beyond + 4] = bytes([0xA0, 0xB0, 0xC0, 0xD0])
    prog = _rowprog(mem, decl, [(0, _sel(0x2000, 0, "i"))])
    prog.data_decls.append(_table(beyond, 4))
    return mem, prog


def test_a_row_past_the_declared_end_reads_the_declaration_it_lands_in():
    """The statement names the base and one index reaches 256 bytes: the row is still its."""
    _mem, prog = _spillprog()
    assert T.gate(prog, {}, 8) is None
    cov = T.render(prog, {}, 8)[2]
    assert cov.planes["freq"] == (8, 8) and cov.classes["freq"]["lane"] == 8


def test_a_declaration_no_named_base_reaches_is_no_source():
    """Past one index register the read is not that statement's own any more."""
    _mem, prog = _spillprog(beyond=0x2200)
    assert T.gate(prog, {}, 8) is None
    assert T.render(prog, {}, 8)[2].planes["freq"] == (4, 8)


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


def _byte_acc(cell, signs=(1,)):
    """A byte-wide accumulator over one cell: no high half, no turn, no step mask."""
    return T.Acc((cell,), (0x100,), (), frozenset({0xFF}), frozenset(signs))


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
    assert _ramps(prog) == [("RAMP", 0x16, 0x16, 0x100, ())]


def test_the_step_origin_is_read_per_execution_not_off_a_frame_snapshot():
    """The cell is re-staged after the step, so the last row written is not the one read."""
    mem, prog = _sweepprog()
    pools = T._observe(prog, {}, 6)[3][0]
    assert [sorted(p[_ACC]) for p in pools] == [sorted((_ACC, _STAGE, 0x2001))] * 6
    assert mem[0x2005] == 0x33 != mem[0x2001]  # what a frame-end snapshot would name
    assert T._accumulators(prog, T._acc_sites(prog)[2]) == {2: (_byte_acc(_ACC), 0)}


def test_perturbing_the_declared_step_changes_the_generated_sweep():
    """The emitted stream is a function of the declared byte, not of the observation."""
    mem, prog = _sweepprog()
    mem[0x2001] = 0x05
    vals, cov = _pw(prog)
    assert cov.planes["pw"] == (6, 6) and vals == [0x05, 0x0A, 0x0F, 0x14, 0x19, 0x1E]
    assert _ramps(prog) == [("RAMP", 0x05, 0x05, 0x100, ())]  # seed and step are the declared byte


def test_a_sweep_whose_step_is_no_declared_byte_stays_residual():
    """A step the declarations do not hold is not a parameter: the run stays in RAW."""
    assert _pw(_sweepprog()[1])[1].planes["pw"] == (6, 6)  # the same sweep, step declared
    _mem, bare = _sweepprog(src=_RAM)  # the same stream, staged from an undeclared cell
    assert T._accumulators(bare, T._acc_sites(bare)[2]) == {2: (_byte_acc(_ACC), 0)}
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
        "arr": 0,
    }
    assert _ramps(prog, 4) == [("RAMP", 0x10, 0x10, 0x100, ()), ("RAMP", 0x53, 0x33, 0x100, ())]


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
    assert T._accumulators(prog, T._acc_sites(prog)[2]) == {0x16: (_byte_acc(_ACC), 1)}


def test_mutation_wrong_ramp_step_or_seed_is_detected():
    """The law fails on a wrong step and on a wrong seed: the sweep is generated."""
    _mem, prog = _sweepprog()
    assert _pw(prog)[1].planes["pw"] == (6, 6)
    graph = T._graph(prog, None, *T._observe(prog, {}, 6))[0]
    assert F.diff(T.eval_graph(graph, 6), T.oracle(prog, {}, 6)) is None
    i = next(i for i, g in enumerate(graph.nodes) if g.transfer[0] == "RAMP")
    for bad in (("RAMP", 0x16, 0x17, 0x100, ()), ("RAMP", 0x17, 0x16, 0x100, ())):
        nodes = list(graph.nodes)
        nodes[i] = nodes[i]._replace(transfer=bad)
        assert F.diff(T.eval_graph(T.Graph(nodes), 6), T.oracle(prog, {}, 6)) is not None


_PWL, _PWH, _DIR, _RATE, _SL, _SH = 0x0800, 0x0801, 0x0802, 0x0900, 0x0A00, 0x0A01


def _c(v, n=1):
    return ("const", v, n)


def _m(a):
    return ("mem", _c(a, 2), 1)


def _op(name, *kids):
    return ("op", name, tuple(kids), 1)


def _st(addr, val):
    return ("st", _c(addr, 2), val)


def _turnprog(pulse=0xE2, mask=0xE0, wide=0x0F, up=0x0E, down=0x08, decl=None, walk=True):
    """``(image, program)``: a 12-bit accumulator swept both ways between two bounds.

    The shape every 6502 pulse sweep has: a step masked out of a declared byte, a low byte
    and the byte above it that takes its carry, and a direction cell the text steps where
    the new high byte equals an immediate it compares against."""
    mem = bytearray(0x10000)
    mem[0x2001] = pulse
    carry = _op("INT_CARRY", _m(_RATE), _m(_PWL))
    borrow = _op(
        "INT_SUB",
        _c(1),
        _op(
            "INT_LESSEQUAL",
            ("op", "INT_ZEXT", (_m(_RATE),), 2),
            ("op", "INT_ZEXT", (_m(_PWL),), 2),
        ),
    )
    hi_up = _op("INT_AND", _op("INT_ADD", _m(_PWH), carry), _c(wide))
    hi_dn = _op("INT_AND", _op("INT_SUB", _m(_PWH), borrow), _c(wide))
    stmts = [
        _st(_RATE, _op("INT_AND", _m(0x2001), _c(mask))),
        (
            "if",
            "if",
            _op("INT_EQUAL", _m(_DIR), _c(0)),
            [
                _st(_SL, _op("INT_ADD", _m(_RATE), _m(_PWL))),
                _st(_SH, hi_up),
                (
                    "if",
                    "if",
                    _op("INT_EQUAL", hi_up, _c(up)),
                    [_st(_DIR, _op("INT_ADD", _m(_DIR), _c(1)) if walk else _c(1))],
                    [],
                ),
            ],
            [
                _st(_SL, _op("INT_SUB", _m(_PWL), _m(_RATE))),
                _st(_SH, hi_dn),
                (
                    "if",
                    "if",
                    _op("INT_EQUAL", hi_dn, _c(down)),
                    [_st(_DIR, _op("INT_SUB", _m(_DIR), _c(1)) if walk else _c(0))],
                    [],
                ),
            ],
        ),
        _st(_PWH, _m(_SH)),
        _st(0xD403, _m(_SH)),
        _st(_PWL, _m(_SL)),
        _st(0xD402, _m(_SL)),
        ("ret",),
    ]
    return mem, frameprog.FrameProgram(
        0x1000,
        0x0F00,
        decls=[decl or _table(0x2000, 8, stride=4)],
        mem0=mem,
        procs=[(0x1000, [], [], stmts)],
    )


_TURN_N = 40  # long enough that the sweep turns at both bounds


def test_a_carry_makes_two_cells_one_accumulator_and_the_sweep_a_pair_route():
    """The high byte takes the low byte's carry, so one emit writes both pw registers."""
    _mem, prog = _turnprog()
    acc = T._accumulators(prog, T._acc_sites(prog)[2])
    a = acc[2][0]
    assert a.cells == (_PWL, _PWH) and acc[3] == (a, 1)  # the plane's low register, its high
    assert a.wraps == (0x100, 0x10) and a.turn == (8, 14)  # the AND, and the two compares
    assert a.masks == frozenset({0xE0}) and a.signs == frozenset({1, -1})
    graph = T._graph(prog, None, *T._observe(prog, {}, _TURN_N))[0]
    got = [(g.transfer, g.route) for g in graph.nodes if g.transfer[0] == "RAMP"]
    assert got == [(("RAMP", 0xE0, 0xE0, 0x1000, (8, 14)), T.pair(2, 3, 0x0F))]
    cov = T.render(prog, {}, _TURN_N)[2]
    assert cov.planes["pw"] == (2 * _TURN_N, 2 * _TURN_N) and T.gate(prog, {}, _TURN_N) is None
    assert cov.classes["pw"]["ramp"] == 2 * _TURN_N - 2  # the two observed bytes are the seed


def test_the_sweep_turns_at_both_bounds_and_the_law_sees_it():
    """The emitted high byte reaches both compared immediates and reverses at each."""
    _mem, prog = _turnprog()
    recs = T.render(prog, {}, _TURN_N)[0]
    highs = [w[1] for rec in recs for sec in rec for w in sec if w[0] == 3]
    turns = [
        i
        for i in range(1, len(highs) - 1)
        if (highs[i] - highs[i - 1]) * (highs[i + 1] - highs[i]) < 0
    ]
    assert len(turns) >= 2 and highs[turns[0]] == 0x0E and highs[turns[1]] == 0x08
    assert max(highs) == 0x0E and min(highs[turns[0] :]) == 0x08  # neither bound overshot


def test_mutation_a_wrong_turn_bound_or_wrap_fails_the_law():
    """Both bounds and the high byte's own width are load-bearing, and the law says so."""
    _mem, prog = _turnprog()
    graph = T._graph(prog, None, *T._observe(prog, {}, _TURN_N))[0]
    assert F.diff(T.eval_graph(graph, _TURN_N), T.oracle(prog, {}, _TURN_N)) is None
    i = next(i for i, g in enumerate(graph.nodes) if g.transfer[0] == "RAMP")
    for bad in (
        ("RAMP", 0xE0, 0xE0, 0x1000, (8, 13)),  # turns down one high byte early
        ("RAMP", 0xE0, 0xE0, 0x1000, (9, 14)),  # turns up one high byte early
        ("RAMP", 0xE0, 0xE0, 0x1000, ()),  # a wrap where the text names a turn
        ("RAMP", 0xE0, 0xE0, 0x100, (8, 14)),  # the low byte's width, not the pair's
    ):
        nodes = list(graph.nodes)
        nodes[i] = nodes[i]._replace(transfer=bad)
        assert (
            F.diff(T.eval_graph(T.Graph(nodes), _TURN_N), T.oracle(prog, {}, _TURN_N)) is not None
        )


def test_a_bound_no_stepped_direction_cell_guards_is_not_a_turn():
    """A bound is the compare that guards the direction cell's own *step*, not any compare."""
    _mem, plain = _turnprog(walk=False)  # the same compares, setting the cell instead
    a = T._accumulators(plain, T._acc_sites(plain)[2])[2][0]
    assert a.cells == (_PWL, _PWH) and a.turn == ()  # still one 16-bit cell, with no turn
    assert T.gate(plain, {}, _TURN_N) is None


def test_the_step_is_the_declared_byte_under_the_mask_the_text_applies():
    """`prate = pulse & $e0`: the run is refused where no declared byte holds the step."""
    _mem, prog = _turnprog(pulse=0xE2)
    assert T.render(prog, {}, _TURN_N)[2].planes["pw"] == (2 * _TURN_N, 2 * _TURN_N)
    assert T._accumulators(prog, T._acc_sites(prog)[2])[2][0].masks == frozenset({0xE0})
    _bad, other = _turnprog(decl=_muted(_table(0x2000, 8, stride=4), [1]))
    assert T.render(other, {}, _TURN_N)[2].planes["pw"][0] == 0  # a play-written lane
    assert T.gate(other, {}, _TURN_N) is None


# ---- 3c2. the object: one accumulator, three registers, a declared first value ----
_OFFS3 = (0, 7, 14)


def _obj_graph(seed=0x0800, step=7, at=0):
    """A stepping object and one reader of it, both routed at an offset generator."""
    return [
        T.indexer(("SELECT", _OFFS3, (0, 1, 2)), T.FRAME),
        T.Generator(("RAMP", seed, step, 0x1000, ()), T.FRAME, T.pair(2, 3, 0x0F, ("node", 0))),
        T.indexer(("SELECT", _OFFS3, (1,)), T.FRAME),
        T.Generator(T.hold(1, seed, at), T.FRAME, T.pair(2, 3, 0x0F, ("node", 2))),
    ]


def test_a_route_may_take_its_register_from_a_generator():
    """`sta $d402,y`: one node writes whichever voice the offset generator names."""
    recs = T.eval_graph(T.Graph(_obj_graph()[:2]), 3)
    got = [sorted(w for sec in rec for w in sec) for rec in recs]
    assert got == [[(2, 0), (3, 8)], [(9, 7), (10, 8)], [(16, 14), (17, 8)]]


def test_an_offset_no_earlier_index_node_settles_is_refused():
    """The offset is a value, so it comes from an index node exactly as a row does."""
    nodes = _obj_graph()
    for bad in (("node", 9), ("node", 1), ("const", 7), ("node", 3)):
        broke = list(nodes)
        broke[1] = broke[1]._replace(route=T.pair(2, 3, 0x0F, bad))
        with pytest.raises(T.TrackerError):
            T.eval_graph(T.Graph(broke), 1)


def test_hold_emits_what_the_object_carries_from_a_declared_first_value():
    """A reader emits the object's value, and the declared seed before it has emitted."""
    got = dict(w for sec in T.eval_graph(T.Graph(_obj_graph(at=1)), 1)[0] for w in sec)
    assert got[2] == 0x00 and got[9] == 0x00  # the object's own emit, then the reader's
    nodes = _obj_graph()
    nodes[1] = nodes[1]._replace(trigger=("event", 4))
    nodes[3] = nodes[3]._replace(transfer=T.hold(1, 0x0999, 0))
    got = dict(w for sec in T.eval_graph(T.Graph(nodes + [T.edge([0, 1])]), 1)[0] for w in sec)
    assert got[9] == 0x99 and got[10] == 0x09  # nothing held yet: the declared value stands


def test_hold_reads_the_object_where_the_frame_order_puts_it():
    """``at`` is the machine's own order: before this frame's steps, or after the k-th."""
    base = [
        T.indexer(("SELECT", _OFFS3, (0, 1)), ("event", 2)),
        T.indexer(("SELECT", _OFFS3, (2,)), T.FRAME),
        T.edge([2]),
    ]
    step = T.Generator(("RAMP", 10, 5, 0x1000, ()), ("event", 2), T.pair(2, 3, 0xFF, ("node", 0)))
    for k, want in ((0, 0), (1, 10), (2, 15)):
        read = T.Generator(T.hold(3, 0, k), T.FRAME, T.pair(2, 3, 0xFF, ("node", 1)))
        got = dict(w for sec in T.eval_graph(T.Graph(base + [step, read]), 1)[0] for w in sec)
        assert got[16] == want  # before this frame's steps, after the first, after the second


# ---- 3m. the accumulator the note reloads ----------------------------------------
_SEED_LANE = [0x40, 0x50, 0x60, 0x70]
_OBJ, _ROW = 0x0810, 0x0800


def _noteprog(lane=None, extra=(), decl=None, reload_at=0):
    """``(image, program)``: a RAM cell a declared table reloads and the text steps down.

    The row cell walks 0..3 and the reload fires on one of them, so the object is seeded
    from a declaration and then walked by the text's own ``dec`` for three frames."""
    mem, table = _bank(0x2000, 4, 1, {0: list(lane or _SEED_LANE)})
    obj, row = ("mem", ("const", _OBJ, 2), 1), ("mem", ("const", _ROW, 2), 1)
    stmts = [
        ("asg", "i", ("mem", ("const", _ROW, 2), 1)),
        (
            "if",
            "if",
            ("op", "INT_EQUAL", (("loc", "i"), ("const", reload_at, 1)), 1),
            [("st", ("const", _OBJ, 2), _sel(0x2000, 0, "i"))],
            [("st", ("const", _OBJ, 2), ("op", "INT_SUB", (obj, ("const", 1, 1)), 1))],
        ),
        *extra,
        ("st", ("const", 0xD400, 2), obj),
        (
            "st",
            ("const", _ROW, 2),
            ("op", "INT_AND", (("op", "INT_ADD", (row, ("const", 1, 1)), 1), ("const", 3, 1)), 1),
        ),
        ("ret",),
    ]
    prog = frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[decl or table], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )
    return mem, prog


def _obj_vals(prog, nframes=8):
    """The freq_lo bytes the graph emits, and its coverage."""
    recs, _gt, cov, _lanes = T.render(prog, {}, nframes)
    return [dict(rec[0]).get(0) for rec in recs], cov


def _graph_of(prog, nframes):
    """The recovered graph, without the note reading."""
    return T._graph(prog, None, *T._observe(prog, {}, nframes))[0]


def test_an_object_a_declared_table_reloads_is_walked_by_the_text_own_step():
    """§4l's object one step out: the seed is a reload, not the post-init image."""
    _mem, prog = _noteprog()
    assert T.gate(prog, {}, 8) is None
    vals, cov = _obj_vals(prog)
    assert vals == [0x40, 0x3F, 0x3E, 0x3D, 0x40, 0x3F, 0x3E, 0x3D]
    assert cov.planes["freq"] == (8, 8) and cov.classes["freq"]["seed"] == 0
    assert [g.transfer[1:4] for g in _reloaded(_graph_of(prog, 8))] == [(("node", 6), 0xFF, 0x100)]


def _reloaded(graph):
    """The ramps whose seed another generator supplies: §4m's own objects."""
    return [g for g in graph.nodes if g.transfer[0] == "RAMP" and isinstance(g.transfer[1], tuple)]


def test_the_reload_seed_is_the_declared_byte_and_the_law_says_so():
    """Move the byte the seed node emits and every emit the object walks from it moves."""
    mem, prog = _noteprog()
    graph = _graph_of(prog, 8)
    gt = T.oracle(prog, {}, 8)
    assert F.diff(T.eval_graph(graph, 8), gt) is None
    at = _reloaded(graph)[0].transfer[1][1]
    nodes = list(graph.nodes)
    seed = nodes[at]
    lane = tuple(mem[0x2000:0x2004])  # the declared lane, read at the reload's own row
    assert seed.transfer[1] == lane and set(seed.transfer[2]) == {0}
    nodes[at] = seed._replace(transfer=("SELECT", (lane[0] ^ 0x55,) + lane[1:], seed.transfer[2]))
    assert F.diff(T.eval_graph(T.Graph(nodes), 8), gt) is not None


def test_a_writer_the_text_does_not_name_leaves_the_object_undefined():
    """A store neither the walk nor a declaration explains claims nothing after it."""
    dirt = [("st", ("const", _OBJ, 2), ("mem", ("const", 0x0A00, 2), 1))]
    _mem, prog = _noteprog(extra=dirt)
    assert T.gate(prog, {}, 8) is None
    assert _obj_vals(prog)[1].planes["freq"] == (0, 8)


def test_an_object_whose_reload_is_a_play_written_lane_is_refused():
    """``mut`` holds here too: a reload out of runtime state is no declared seed."""
    _mem, table = _bank(0x2000, 4, 1, {0: _SEED_LANE})
    _m, prog = _noteprog(decl=_muted(table, [0]))
    assert T.gate(prog, {}, 8) is None
    assert _obj_vals(prog)[1].planes["freq"] == (0, 8)


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
    got = {r: t for _c, t, r, *_e in streams}
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
    return _mutate_node(prog, lambda g: g.route == ("plane", reg), transfer, nframes)


def _mutate_node(prog, pick, transfer, nframes=4):
    """The law's verdict with the first node ``pick`` accepts given a new transfer."""
    nodes = list(T._graph(prog, None, *T._observe(prog, {}, nframes))[0].nodes)
    i = next(i for i, g in enumerate(nodes) if pick(g))
    nodes[i] = nodes[i]._replace(transfer=transfer(nodes[i].transfer))
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


def _dividerprog(n, reload_=None, decl=None, base=0x2000, nrows=4, seed=None):
    """``(image, program)`` ticking every ``n`` frames, reading an AD lane on the tick.

    The counter steps down from ``seed``, reloads with ``reload_`` (the immediate ``n``
    by default) and only the tick body writes the register, so the AD stream's edges
    are the divider's ticks."""
    mem, table = _bank(base, nrows, 4, {2: list(range(0x11, 0x11 + nrows))})
    mem[_COUNTER] = n if seed is None else seed
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


_COUNTER2 = 0x0802


def _cascadeprog(n1, n2, base=0x2000, nrows=8):
    """``(image, program)``: divider ``n1`` steps divider ``n2``, whose tick reads a lane.

    The inner counter's dec sits inside the outer's tick body, so the machine steps it
    exactly on the outer's ticks — the chain the cascade rule must see."""
    mem, table = _bank(base, nrows, 4, {2: list(range(0x11, 0x11 + nrows))})
    mem[_COUNTER] = n1
    mem[_COUNTER2] = n2
    dec1 = ("op", "INT_SUB", (("mem", ("const", _COUNTER, 2), 1), ("const", 1, 1)), 1)
    dec2 = ("op", "INT_SUB", (("mem", ("const", _COUNTER2, 2), 1), ("const", 1, 1)), 1)
    body2 = [
        ("st", ("const", _COUNTER2, 2), ("const", n2, 1)),
        ("asg", "i", ("mem", ("const", 0x0800, 2), 1)),
        ("st", ("const", 0xD405, 2), _sel(base, 2, "i")),
        (
            "st",
            ("const", 0x0800, 2),
            ("op", "INT_ADD", (("mem", ("const", 0x0800, 2), 1), ("const", 4, 1)), 1),
        ),
    ]
    tick = [
        ("st", ("const", _COUNTER, 2), ("const", n1, 1)),
        ("asg", "d", dec2),
        ("st", ("const", _COUNTER2, 2), ("loc", "d")),
        ("if", "if", ("op", "INT_EQUAL", (("loc", "d"), ("const", 0, 1)), 1), body2, []),
    ]
    stmts = [
        ("asg", "c", dec1),
        ("st", ("const", _COUNTER, 2), ("loc", "c")),
        ("if", "if", ("op", "INT_EQUAL", (("loc", "c"), ("const", 0, 1)), 1), tick, []),
        ("ret",),
    ]
    return mem, frameprog.FrameProgram(
        0x1000, 0x0F00, decls=[table], mem0=mem, procs=[(0x1000, [], [], stmts)]
    )


def test_a_div_clocked_by_a_div_divides_its_input_ticks():
    """The inner DIV counts the ticks it receives, not the frames — the cascade's law."""
    nodes = [
        T.div(2),
        T.Generator(("DIV", 3, 2), ("event", 0), ("fire",)),
        T.lookup([0x11, 0x22], ("event", 1), 5),
    ]
    recs = T.eval_graph(T.Graph(nodes), 12)
    got = [f for f, rec in enumerate(recs) if any(sec for sec in rec)]
    assert got == [5, 11]  # outer ticks at 1,3,5,7,9,11; inner on its 3rd and 6th input


def test_a_cascade_of_two_declared_dividers_is_recovered_and_generates():
    """A period only the product of two reloads declares becomes DIV -> DIV, law green."""
    _mem, prog = _cascadeprog(2, 3)
    graph = T._graph(prog, None, *T._observe(prog, {}, 24))[0]
    divs = [(i, g) for i, g in enumerate(graph.nodes) if g.transfer[0] == "DIV"]
    assert [g.transfer[1] for _i, g in divs] == [2, 3]
    assert divs[1][1].trigger == ("event", divs[0][0])
    assert T.gate(prog, {}, 24) is None
    cov = T.render(prog, {}, 24)[2]
    assert cov.triggers[0] == cov.triggers[1] > 0  # every fire generated, none EDGE


def test_mutation_a_wrong_cascade_divisor_is_detected():
    """Perturbing either divisor of a cascade moves the emits and fails the law."""
    _mem, prog = _cascadeprog(2, 3)
    graph = T._graph(prog, None, *T._observe(prog, {}, 24))[0]
    assert F.diff(T.eval_graph(graph, 24), T.oracle(prog, {}, 24)) is None
    for which, wrong in ((2, ("DIV", 4, 2)), (3, ("DIV", 2, 1))):
        idx = next(i for i, g in enumerate(graph.nodes) if g.transfer[:2] == ("DIV", which))
        was = graph.nodes[idx]
        graph.nodes[idx] = was._replace(transfer=wrong)
        assert F.diff(T.eval_graph(graph, 24), T.oracle(prog, {}, 24)) is not None
        graph.nodes[idx] = was


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
    assert T._generates((0, 1, 0, 1), 2, 1) and not T._generates((0, 1, 1, 1), 2, 1)
    assert not T._generates((0, 1, 0, 0), 2, 1) and not T._generates((0, 1, 0, 1), 4, 3)
    assert T._clock_node((0, 1, 0, 1), T.Seq(((2, 1),), {}, {})) == [T.div(2)]
    assert T._clock_node((0, 1, 0, 1), T.Seq(((3, 2), (4, 3)), {}, {})) == [T.edge((0, 1, 0, 1))]


def test_mutation_a_wrong_divisor_is_detected():
    """The law verifies the divisor: perturb it and the fires move off the observation."""
    _mem, prog = _dividerprog(2)
    graph = T._graph(prog, None, *T._observe(prog, {}, 8))[0]
    assert F.diff(T.eval_graph(graph, 8), T.oracle(prog, {}, 8)) is None
    i = next(i for i, g in enumerate(graph.nodes) if g.transfer[0] == "DIV")
    for wrong in (("DIV", 3), ("DIV", 1)):
        graph.nodes[i] = graph.nodes[i]._replace(transfer=wrong)
        assert F.diff(T.eval_graph(graph, 8), T.oracle(prog, {}, 8)) is not None


def test_the_phase_is_the_counter_byte_the_post_init_image_declares():
    """DIV's phase belongs to the arrangement: the counter's own seed says which frame ticks."""
    _mem, prog = _dividerprog(3, seed=1)
    assert T._sequencer(prog, T._banks(prog)).ticks == ((3, 0),)
    divs, edges, cov = _fires(prog, 9)
    assert divs == [3] and not edges and cov.triggers == (3, 3)
    assert T.gate(prog, {}, 9) is None


def test_a_counter_seeded_at_its_own_reload_takes_the_dividers_own_phase():
    """``n-1`` is not a default but the seed ``n``'s reading, and an unstaged 0 gives it too."""
    _mem, prog = _dividerprog(2)
    assert T._sequencer(prog, T._banks(prog)).ticks == ((2, 1),) == (T.div(2).transfer[1:] + (),)
    assert _fires(prog)[0] == [2] and T.gate(prog, {}, 8) is None
    _m2, zeroed = _dividerprog(2, seed=0)
    assert T._sequencer(zeroed, T._banks(zeroed)).ticks == ((2, 1),)


def test_mutation_a_wrong_phase_moves_every_tick_and_fails_the_law():
    """The phase is checked by the law exactly as the divisor is: shift it and it fails."""
    _mem, prog = _dividerprog(3, seed=1)
    pick = (
        lambda g: g.transfer[0] == "DIV"
    )  # noqa: E731  pylint: disable=unnecessary-lambda-assignment
    assert _mutate_node(prog, pick, lambda t: t, 9) is None
    for phase in (1, 2):
        assert _mutate_node(prog, pick, lambda t: ("DIV", 3, phase), 9) is not None


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
    assert dict(got)[0x0F] == ("SELECT", (0x0F,), ())
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


def _value_rel(op, base, mask=0xFF):
    """A relative *value* route: node 0 settles no field, node 1 settles the one it rides."""
    return [
        T.raw([[]]),
        T.lookup((0x40,), T.FRAME, 0),
        T.Generator(("SELECT", (3,), ()), T.FRAME, T.relative(0, op, base, mask)),
    ]


def _index_rel(op, base):
    """The same object one domain over: node 0 settles no row, node 1 supplies the delta."""
    return [
        T.lookup((5,), T.FRAME, 4),
        T.indexer(("SELECT", (1,), ()), T.FRAME),
        T.select((7, 8, 9), ("rel", op, ("node", 1), base), T.FRAME, 1),
    ]


# one relative concept, one validator, one vocabulary of refusals -- in both domains
_REL_BAD = [
    ("unknown relative operation", "NAND", ("const", 1)),
    ("is not a byte", "ADD", ("const", 0x100)),
    ("is not an earlier node", "ADD", ("node", 9)),
    ("drives another field", "ADD", ("node", 0)),
]


@pytest.mark.parametrize("why,op,base", _REL_BAD)
@pytest.mark.parametrize("build", (_value_rel, _index_rel), ids=("value", "index"))
def test_one_relative_rule_refuses_the_same_way_in_both_domains(build, why, op, base):
    """A delta combines with a named base; the base rule and its refusals are shared."""
    with pytest.raises(T.TrackerError, match=why):
        T.eval_graph(T.Graph(build(op, base)), 1)


def test_prev_is_the_value_domain_s_base_and_a_row_index_has_none():
    """A row is not a plane, so it has no previous value; nor has a plane nothing writes."""
    for nodes in (_index_rel("ADD", ("prev",)), _value_rel("ADD", ("prev",))[2:]):
        with pytest.raises(T.TrackerError, match="has no previous value"):
            T.eval_graph(T.Graph(nodes), 1)
    T.eval_graph(T.Graph(_value_rel("ADD", ("prev",))), 1)  # the RAW floor is a writer


def test_the_composition_order_of_an_absolute_and_a_relative_route_is_checked():
    """A relative route names a base an earlier generator settles, or the graph is refused."""
    ok = _value_rel("ADD", ("node", 1))
    assert T.eval_graph(T.Graph(ok), 1) == F.canonical([[(0, 0x43)]])
    bad = [
        ok[2:],  # the base generator is gone with the floor
        [ok[1], ok[2]._replace(route=T.relative(0, "ADD", ("node", 0), 0x0F))],
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
    sel = {r: t for _c, t, r, *_e in post if t[2]}
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
    sel = {r: t for _c, t, r, *_e in T._lww_streams(lww, tabs, prog.mem0)[0]}
    for v in range(3):
        lo, hi = sel[7 * v], sel[7 * v + 1]
        assert lo[1] == tuple(prog.mem0[0x5428 + 2 * i] for i in range(96))
        assert hi[1] == tuple(prog.mem0[0x5429 + 2 * i] for i in range(96))
        assert lo[2] == hi[2]  # one row stream per voice: the row is the note index
    cov = T.render(prog, trace, nf)[2]
    assert cov.classes["freq"]["lane"] > 600 and cov.classes["freq"]["imm"] == 0


# ---- 3k. universality: what the graph *produces*, ratcheted so it cannot slip back ----
# The law passes at zero generation — `from_frames` is exactly that floor — so law-PASS is
# a soundness check and never a universality one. These are the figures that measure the
# goal: what is replayed rather than produced, what reaches the output shallowly, how many
# fires no generator makes, and how much is read at a row the arrangement generates.
# Measured at _LONG frames, which is where Commando's turning pulse sweep and its song
# chain both run; the 200-frame tests above reach neither. It does NOT cover the whole
# tune's figures, which docs/tracker.md §6 reports.
_COMMANDO = {"total": 10489, "residual": 122, "shallow": 938, "generated_fires": 1499, "arr": 598}
_LEDGER = json.loads((Path(__file__).parent / "universality.json").read_text(encoding="utf-8"))


def _ledger(frames, stem="Commando"):
    """The universality.json entry for one tune at one frame count: bounds live there."""
    return next(
        t for t in _LEDGER["tunes"] if t["frames"] == frames and Path(t["rel"]).stem == stem
    )


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_universality_does_not_regress(sid, subtune):
    """Residual, shallow emits and observed fires may only fall; generated rows may only rise.

    ``total`` is asserted by **equality**, not by a bound: it is the tune's own write count
    at a fixed frame count, so it is a constant of the ground truth and not a figure the
    recovery is entitled to move. The trigger domain is floored by the *generated* fire
    count: an observed-fire ceiling falls to any value the layer newly explains (§0)."""
    prog, trace, nf = _lifted(sid, subtune, frames=_LONG)
    cov = T.render(prog, trace, nf)[2]
    shallow = sum(c["imm"] + c["seed"] for c in cov.classes.values())
    assert cov.total == _COMMANDO["total"]  # ground truth, not ours to move
    assert cov.interp + cov.residual == cov.total  # every write is on one side or the other
    assert cov.residual <= _COMMANDO["residual"]  # bytes replayed, not produced
    assert shallow <= _COMMANDO["shallow"]  # a constant, or a byte a sweep starts from
    assert cov.triggers[0] >= _COMMANDO["generated_fires"]  # fires a divider makes, a floor
    assert sum(c["arr"] for c in cov.classes.values()) >= _COMMANDO["arr"]
    assert T.gate(prog, trace, nf) is None


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_the_rendered_header_keeps_the_same_books_as_the_recovery(sid, subtune):
    """The artifact's own replay must agree with ``_run``: same denominator, no negative class.

    ``trackertext._scan`` is a *second* evaluator, so it can silently disagree — and it did:
    building ``_Fires`` without a value view left a generated divisor with no divisor, and
    every stream it triggers fell out of the artifact's books entirely."""
    from deity_informant import trackertext as X  # pylint: disable=import-outside-toplevel

    prog, trace, nf = _lifted(sid, subtune, frames=_LONG)
    got = T._observe(prog, trace, nf)
    graph = T._graph(prog, T._pitch(prog, T._freq_words(got[0])), *got)[0]
    cov = T.coverage(graph, nf)  # the recovery's own books, off `_run`
    _keys, scan = X._scanned(graph, nf, prog)  # the artifact's, off its own replay
    assert (scan.cov.total, scan.cov.interp) == (cov.total, cov.interp)
    assert scan.cov.planes == cov.planes and scan.cov.triggers == cov.triggers
    for plane in scan.cov.planes:
        assert min(X._classes(graph, scan.cov, plane).values()) >= 0  # a count is never negative


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_ablating_raw_leaves_the_generators_reproducing_the_tune(sid, subtune):
    """Deleting the ``RAW`` floor is what measures universality: what the graph *produces*."""
    prog, trace, nf = _lifted(sid, subtune, frames=_LONG)
    got = T._observe(prog, trace, nf)
    graph = T._graph(prog, T._pitch(prog, T._freq_words(got[0])), *got)[0]
    nodes = list(graph.nodes)  # emptied in place: node indices carry rows and divisors
    nodes[graph.raw_index()] = T.raw([])
    got, want = T.coverage(T.Graph(nodes), nf), T.coverage(graph, nf)
    assert got.residual == 0 and got.total == want.interp  # nothing is carried any more
    assert got.total / want.total >= _ledger(_LONG)["ablated_share"]  # a ledger line, not code


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_pw_lanes_are_one_object_the_graph_carries(sid, subtune):
    """$5591's +0/+1 lanes are `mut`, and that is what makes them an object rather than data.

    A const read of a play-written cell is refused, and rightly; the object is the same
    cell read as *state* — its first value the declared image, every later one a step."""
    prog, trace, nf = _lifted(sid, subtune)
    decl = next(d for d in prog.data_decls if d["base"] == 0x5591)
    assert decl["stride"] == 8 and decl["mut"] == [0]
    got = T._observe(prog, trace, nf)
    graph = T._graph(prog, None, *got)[0]
    lanes = [g for g in graph.nodes if T._is_plane(g.route) and g.route[1] in (2, 3)]
    assert lanes and all(T._at_of(g.route) is not None for g in lanes)  # every one an object's
    assert {g.transfer[0] for g in lanes} == {"RAMP", "HOLD", "SELECT"}
    cov = T.render(prog, trace, nf)[2]
    assert cov.planes["pw"] == (245, 245)  # every pw write, and not one observed seed
    pw = cov.classes["pw"]
    assert pw["seed"] == 0 and pw["imm"] == 0 and pw["arr"] == 0
    assert pw["lane"] + pw["ramp"] == cov.planes["pw"][0]


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_object_seed_is_the_declared_image_and_the_law_says_so(sid, subtune):
    """Perturbing the post-init byte at an object's own row moves every emit of it.

    The seed is read from the declaration, never from the stream: with the byte moved the
    projection no longer matches, which is what an observed seed could never fail at."""
    prog, trace, nf = _lifted(sid, subtune)
    graph = T._graph(prog, None, *T._observe(prog, trace, nf))[0]
    pw = [g for g in graph.nodes if g.transfer[0] == "HOLD" and T._plane_of(g.route[1]) == "pw"]
    holds = {g.transfer[2] for g in pw}
    assert holds  # the note-on reads of an object are what carry its declared first value
    want = {prog.mem0[0x5591 + 8 * r] | (prog.mem0[0x5592 + 8 * r] << 8) for r in range(13)}
    assert holds <= want | {w & 0xFF for w in want}  # a declared word, or a byte object's half
    gt = T.oracle(prog, trace, nf)
    assert F.diff(T.eval_graph(graph, nf), gt) is None
    for i, g in enumerate(graph.nodes):  # every declared first value is load-bearing
        if g not in pw:
            continue
        nodes = list(graph.nodes)
        nodes[i] = g._replace(transfer=("HOLD", g.transfer[1], g.transfer[2] ^ 0x55, g.transfer[3]))
        assert F.diff(T.eval_graph(T.Graph(nodes), nf), gt) is not None


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_pw_sweep_is_generated_from_the_declared_step_lane(sid, subtune):
    """The +6 lane of the `$5591` bank is the step; the sweep is a RAMP over it."""
    prog, trace, nf = _lifted(sid, subtune)
    cov = T.render(prog, trace, nf)[2]
    assert cov.planes["pw"][0] > 150  # 53 declared-lane emits before the sweep is generated
    assert T._accumulators(prog, T._acc_sites(prog)[2]) == {2: (_byte_acc(0x5591), 0)}
    assert cov.classes["pw"]["ramp"] > 100 and cov.classes["pw"]["seed"] < 20
    graph = T._graph(prog, None, *T._observe(prog, trace, nf))[0]
    ramps = [  # a §4m ramp's seed is a node, and its step is the walk rule, not this lane
        g.transfer
        for g in graph.nodes
        if g.transfer[0] == "RAMP" and not isinstance(g.transfer[1], tuple)
    ]
    assert ramps and all(t[2] == prog.mem0[0x55A7] for t in ramps)  # the declared step byte
    lane = {prog.mem0[0x5591 + 8 * r + 6] for r in range(263 // 8)}
    assert {t[2] for t in ramps} <= lane  # every step is a byte of the declared +6 lane


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_pulse_sweep_turns_where_the_player_compares(sid, subtune):
    """The `pulseup` sweep only plays past the opening, so it is measured where it runs."""
    prog, trace, nf = _lifted(sid, subtune, frames=_LONG)
    acc = T._accumulators(prog, T._acc_sites(prog)[2])
    a = acc[2][0]
    assert a.cells == (0x5591, 0x5592) and acc[3] == (a, 1)  # ins_pwl:ins_pwh, one 16-bit cell
    assert a.wraps == (0x100, 0x10) and a.turn == (8, 14)  # and #$0f, cmp #8, cmp #$0e
    assert a.masks == frozenset({0xFF, 0xE0}) and a.signs == frozenset({1, -1})
    graph = T._graph(prog, None, *T._observe(prog, trace, nf))[0]
    turns = [g for g in graph.nodes if g.transfer[0] == "RAMP" and g.transfer[4]]
    assert turns and all(g.route[:4] == T.pair(2, 3, 0x0F)[:4] for g in turns)  # the pw pair
    assert all(T._at_of(g.route)[0] == "node" for g in turns)  # at the voice the text names
    assert {g.transfer[3] for g in turns} == {0x1000} and {g.transfer[4] for g in turns} == {
        (8, 14)
    }


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_drum_is_the_pitch_word_the_note_reloads_walked_down(sid, subtune):
    """`ctr_551A` is plain RAM the note-on reloads from the declared pitch table (§4m)."""
    prog, trace, nf = _lifted(sid, subtune, frames=_LONG)
    walks = T._reload_walks(prog, T._banks(prog))
    assert walks[0x551A][0][0][1] == ("step", 0xFF, 0x100)  # dec ctr_551A,x: the text's own step
    assert {T._base(s[1]) for s in walks[0x551A][1]} == {0x551A}  # and its one reload
    graph = T._graph(prog, None, *T._observe(prog, trace, nf))[0]
    objs = [g for g in graph.nodes if g.transfer[0] == "RAMP" and isinstance(g.transfer[1], tuple)]
    assert objs and {g.transfer[2:4] for g in objs} == {(0xFF, 0x100)}
    seeds = {graph.nodes[g.transfer[1][1]].transfer[1] for g in objs}
    lane = tuple(prog.mem0[0x5429 + 2 * r] for r in range(96))
    assert seeds == {lane}  # the high lane of the declared `$5428` pitch table, at the note's row
    cov = T.render(prog, trace, nf)[2]
    assert cov.classes["freq"]["ramp"] > 0 and cov.classes["freq"]["seed"] == 0


@pytest.mark.parametrize("sid,subtune", _tune("Artura", "Daglish_Ben"))
def test_artura_adsr_through_the_sid_register_mirror(sid, subtune):
    """ADSR staged in a per-voice SID mirror still reads as the declared bank."""
    prog, trace, nf = _lifted(sid, subtune)
    assert 0xEFC1 in T.lift(prog).instruments  # the store site reads the mirror cell
    _gt, ords, _lww, _acc = T._observe(prog, trace, nf)
    pre, post, refined = _instr(prog, ords)
    assert refined >= {5, 6, 12, 13, 19, 20} and not pre
    bank = tuple(prog.mem0[0xEF52 + i] for i in range(46))
    sel = {r: t for _c, t, r, *_e in post if t[2]}
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
        "arr": 0,
    }
    _gt, _ords, lww, _acc = T._observe(prog, trace, nf)
    streams = T._lww_streams(lww, T._tree_tables(prog, T._banks(prog)), prog.mem0)[0]
    got = {r: t for _c, t, r, *_e in streams if r > T._VOICE_HI}
    assert set(got) == {0x16, 0x17}  # $15 and $18 name no declaration and stay residual
    cells = [0x19C5 + t[2][0] for t in (got[0x16], got[0x17])]
    assert cells == [0x1A08, 0x1A07]  # two cells of one declared table, recovered per register
    assert [prog.mem0[c] for c in cells] == [0x06, 0xF7]


def _arrangement(orderlist, patterns, rows_per_pattern, nframes, phase=None):
    """An orderlist indexing a pattern table, the shape §7.4 names.

    n0 the row clock, n1 the orderlist (an index route), n2 the pattern it selects."""
    beat = T.div(rows_per_pattern, phase=phase)
    order = T.indexer(("SELECT", tuple(orderlist), ()), ("event", 0))
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


def test_the_arrangement_supplies_the_phase_the_divider_lacks():
    """A song that started its clock elsewhere ticks elsewhere: entry 0 from frame 0 on."""
    got = _emitted(*_arrangement([3, 5, 1], tuple(range(8)), 4, 16, phase=0))
    assert got[:4] == [3, 3, 3, 3] and got[4:8] == [5] * 4
    assert _emitted(*_arrangement([3, 5, 1], tuple(range(8)), 4, 16, phase=2))[:2] == [None] * 2


def test_index_source_must_precede_its_reader():
    """A row source later than its reader would need a value the frame has not made."""
    pat = T.select((1, 2, 3), ("node", 1), T.FRAME, 0x18)
    order = T.indexer(("SELECT", (0, 1), ()), T.FRAME)
    with pytest.raises(T.TrackerError, match="not an earlier node"):
        T.eval_graph(T.Graph([pat, order]), 4)


def test_row_source_must_route_to_an_index():
    """A plane generator's write is a byte, not a row: it cannot be an index source."""
    other = T.lookup((0, 1), T.FRAME, 0x04)
    pat = T.select((1, 2, 3), ("node", 0), T.FRAME, 0x18)
    with pytest.raises(T.TrackerError, match="drives another field"):
        T.eval_graph(T.Graph([other, pat]), 4)


def test_index_route_without_a_reader_is_dead():
    """A generator that neither writes a plane nor is read explains nothing."""
    order = T.indexer(("SELECT", (0, 1), ()), T.FRAME)
    with pytest.raises(T.TrackerError, match="has no reader"):
        T.eval_graph(T.Graph([order]), 4)


def test_generated_row_out_of_range_drops_the_write():
    """An index past the table emits nothing, so the law fails rather than wrapping."""
    order = T.indexer(("SELECT", (9,), ()), T.FRAME)
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
    shift = T.indexer(("SELECT", tuple(trans), ()), ("event", 0))
    note = T.indexer(("SELECT", tuple(notes), ()), T.FRAME)
    pitch = T.select(table, ("rel", "ADD", ("node", 2), ("node", 1)), T.FRAME, 0x01)
    return T.Graph([beat, shift, note, pitch]), nframes


def test_a_relative_row_carries_a_transpose():
    """A transpose shifts the row a pitch table is read at, not the byte it yields."""
    got = _emitted(*_transposed([0, 12], [1, 3, 5, 7], tuple(range(100, 140)), 4, 10))
    assert got[3:7] == [107, 101, 103, 105]  # transpose 0: the note column itself
    assert got[7:10] == [119, 113, 115]  # transpose 12: every row shifted by an octave


def test_a_relative_row_may_shift_by_a_declared_constant():
    """A fixed transpose needs no generator of its own."""
    note = T.indexer(("SELECT", (0, 1), ()), T.FRAME)
    pitch = T.select(tuple(range(20)), ("rel", "ADD", ("node", 0), ("const", 5)), T.FRAME, 0x01)
    assert _emitted(T.Graph([note, pitch]), 4) == [5, 6, 5, 6]


def test_a_relative_row_refuses_an_unknown_operation():
    """The operation comes from the store's own operator, not an invented one."""
    note = T.indexer(("SELECT", (0,), ()), T.FRAME)
    pitch = T.select((1, 2), ("rel", "MUL", ("node", 0), ("const", 0)), T.FRAME, 0x01)
    with pytest.raises(T.TrackerError, match="unknown relative operation"):
        T.eval_graph(T.Graph([note, pitch]), 2)


def test_both_sources_of_a_relative_row_must_be_earlier_index_nodes():
    """Either half arriving late would need a value the frame has not made."""
    note = T.indexer(("SELECT", (0,), ()), T.FRAME)
    pitch = T.select(tuple(range(20)), ("rel", "ADD", ("node", 0), ("node", 2)), T.FRAME, 0x01)
    shift = T.indexer(("SELECT", (1,), ()), T.FRAME)
    with pytest.raises(T.TrackerError, match="not an earlier node"):
        T.eval_graph(T.Graph([note, pitch, shift]), 2)


def test_a_transposed_row_past_the_table_drops_the_write():
    """A shift off the end of the pitch table emits nothing rather than wrapping."""
    note = T.indexer(("SELECT", (1,), ()), T.FRAME)
    pitch = T.select((7, 8), ("rel", "ADD", ("node", 0), ("const", 40)), T.FRAME, 0x01)
    assert _emitted(T.Graph([note, pitch]), 2) == [None, None]


def test_mutation_a_wrong_transpose_changes_the_projection():
    """Mutation evidence: the shift must be the declared one."""
    good, n = _transposed([0, 12], [1, 3], tuple(range(100, 140)), 4, 12)
    bad, _n = _transposed([0, 11], [1, 3], tuple(range(100, 140)), 4, 12)
    assert T.eval_graph(good, n) != T.eval_graph(bad, n)


# ---- 4g. the arrangement: a declared pattern at a row the program text walks ------
_PAT, _LO, _HI = 0x2000, 0x2100, 0x2104  # pattern region, and the pointer table's lanes
_POS, _ROW = 0x0800, 0x0801  # the orderlist position and the pattern row, in RAM


def _cell(a):
    return ("mem", ("const", a, 2), 1)


def _lane(base, name):
    """``base[name]``: one byte of a declared table at a machine-register index."""
    idx = ("op", "INT_ZEXT", (("loc", name),), 2)
    return ("mem", ("op", "INT_ADD", (("const", base, 2), idx), 2), 1)


def _bump(cell, name, wrap):
    """``cell = (cell + 1) & (wrap - 1)``: the counter walk a 6502 driver writes."""
    step = ("op", "INT_ADD", (("loc", name), ("const", 1, 1)), 1)
    return ("st", ("const", cell, 2), ("op", "INT_AND", (step, ("const", wrap - 1, 1)), 1))


def _arrprog(nblocks=1, wrap=8, patlen=8, reg=0xD400, mut=(), blocks=4, lockstep=False):
    """``(image, program)``: a pointer reloaded from a declared table, deref'd at a walk.

    The orderlist position selects the block and the row walks inside it, both by cells
    whose every writer the program text names — rung (f)'s shape, hermetically."""
    mem = bytearray(0x10000)
    for k in range(blocks):
        blk = _PAT + 8 * k
        mem[_LO + k], mem[_HI + k] = blk & 0xFF, blk >> 8
    for i in range(32):
        mem[_PAT + i] = (0x11 * (i + 1)) & 0xFF
    word = (
        "op",
        "INT_OR",
        (
            ("op", "INT_ZEXT", (_lane(_LO, "x"),), 2),
            ("op", "INT_LEFT", (("op", "INT_ZEXT", (_lane(_HI, "x"),), 2), ("const", 8, 1)), 2),
        ),
        2,
    )
    deref = ("op", "INT_ADD", (("mem", ("const", 0x02, 2), 2), _zext("y")), 2)
    stmts = [
        ("asg", "x", _cell(_POS)),
        ("st", ("const", 0x02, 2), word),
        ("asg", "y", _cell(_ROW)),
        ("st", ("const", reg, 2), ("mem", deref, 1)),
        ("asg", "r", _cell(_ROW)),
        _bump(_ROW, "r", wrap),
    ]
    if nblocks > 1:  # the orderlist advances where the row walk wrapped
        arm = [("asg", "p", _cell(_POS)), _bump(_POS, "p", nblocks)]
        cond = ("op", "INT_EQUAL", (_cell(_ROW), ("const", 0, 1)), 1)
        stmts += arm if lockstep else [("if", "if", cond, arm, [])]
    decls = [
        dict(_table(_PAT, patlen), mut=list(mut), role=None),
        dict(_table(_LO, blocks), mut=[], role=("lo", _HI)),
        dict(_table(_HI, blocks), mut=[], role=("hi", _LO)),
    ]
    return mem, frameprog.FrameProgram(
        0x1000, 0x0F00, decls=decls, mem0=mem, procs=[(0x1000, [], [], stmts + [("ret",)])]
    )


def _zext(name):
    return ("op", "INT_ZEXT", (("loc", name),), 2)


def _arr(prog, n=10):
    """``(emitted bytes, Coverage, refusals, nodes)`` for one arrangement program."""
    diag = Counter()
    recs, gt, cov, _lanes = T.render(prog, {}, n, diag)
    assert F.diff(recs, gt) is None
    nodes = T._graph(prog, None, *T._observe(prog, {}, n))[0].nodes
    return [w[1] for rec in recs for sec in rec for w in sec], cov, diag, nodes


def test_the_pattern_is_a_declared_block_at_a_row_the_text_walks():
    """The row is generated from the post-init byte and the text's own step, not observed."""
    _mem, prog = _arrprog()
    vals, cov, _diag, nodes = _arr(prog)
    assert vals == [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x11, 0x22]
    assert cov.planes["freq"] == (10, 10) and cov.classes["freq"]["arr"] == 10
    assert [g.transfer for g in nodes if g.route == T.INDEX] == [("RAMP", 0, 1, 8, ())]
    assert [g.transfer[2] for g in nodes if g.transfer[0] == "SELECT"] == [("node", 2)]


def test_the_pattern_loop_is_the_walk_modulus_and_needs_no_back_edge():
    """`_emit` wraps, so the row RAMP's own bound is the pattern's loop: frame 8 is row 0."""
    _mem, prog = _arrprog(wrap=4, patlen=4)
    vals, _cov, _diag, _nodes = _arr(prog)
    assert vals == [0x11, 0x22, 0x33, 0x44] * 2 + [0x11, 0x22]


def test_an_orderlist_step_shares_the_pattern_node_it_revisits():
    """Two blocks alternating every four rows: one node per block, refired on the revisit."""
    _mem, prog = _arrprog(nblocks=2, wrap=4, patlen=32)
    vals, cov, _diag, nodes = _arr(prog)
    assert vals == [0x11, 0x22, 0x33, 0x44, 0x99, 0xAA, 0xBB, 0xCC, 0x11, 0x22]
    assert cov.classes["freq"]["arr"] == 10
    sel = [g.transfer[1] for g in nodes if g.transfer[0] == "SELECT"]
    assert sel == [(0x11, 0x22, 0x33, 0x44), (0x99, 0xAA, 0xBB, 0xCC)]


def test_a_row_the_program_text_does_not_walk_is_refused():
    """The row cell is written from a byte the text does not name: no walk, no pattern."""
    _mem, prog = _arrprog()
    stmts = list(prog.procs[0][3])
    stmts[5] = ("st", ("const", _ROW, 2), ("mem", ("const", 0x0900, 2), 1))
    bad = frameprog.FrameProgram(
        0x1000, 0x0F00, decls=prog.data_decls, mem0=prog.mem0, procs=[(0x1000, [], [], stmts)]
    )
    vals, cov, diag, _nodes = _arr(bad)
    assert cov.classes == {} and diag["arrange_row_not_walked"] == 1 and len(vals) == 10


def test_a_block_outside_every_declaration_is_refused():
    """A pointer into undeclared memory is not a pattern, whatever byte it holds."""
    _mem, prog = _arrprog()
    prog.data_decls = [d for d in prog.data_decls if d["base"] != _PAT]
    vals, cov, diag, _nodes = _arr(prog)
    assert cov.classes == {} and diag["arrange_block_undeclared"] == 10 and len(vals) == 10


def test_a_pattern_row_the_declaration_names_mut_is_refused():
    """A play-written offset is not const data, so the block it sits in holds no pattern."""
    _mem, prog = _arrprog(mut=(3,))
    _vals, cov, diag, _nodes = _arr(prog)
    assert cov.classes == {} and diag["arrange_block_undeclared"] == 10


def test_a_walk_that_stands_still_predicts_no_row_and_is_refused():
    """A modulus of one holds the row: it explains no index, as DIV(1) explains no tick."""
    _mem, prog = _arrprog(wrap=1)
    _vals, cov, diag, _nodes = _arr(prog)
    assert cov.classes == {} and diag["arrange_walk_stands_still"] == 1


def test_a_block_visited_for_one_row_is_refused():
    """A run of one row predicts no second one, so the block it names is not a pattern."""
    _mem, prog = _arrprog(nblocks=4, wrap=8, patlen=32, lockstep=True)
    _vals, cov, diag, _nodes = _arr(prog)
    assert cov.classes == {} and diag["arrange_short_run"] == 10


def test_the_order_preserved_section_takes_no_pattern_and_it_is_priced():
    """ctrl/AD/SR is a sequence of whole-byte writes, so a pattern generator is refused."""
    _mem, prog = _arrprog(reg=0xD404)
    _vals, cov, diag, _nodes = _arr(prog)
    assert cov.classes == {} and diag["arrange_ord_section"] == 10


def test_mutation_a_pattern_boundary_moved_by_one_row_fails_the_law():
    """The wrap is the pattern's length: shorten it and the generated stream diverges."""
    _mem, prog = _arrprog()
    graph = T._graph(prog, None, *T._observe(prog, {}, 10))[0]
    nodes = list(graph.nodes)
    for i, g in enumerate(nodes):
        if g.route == T.INDEX:
            nodes[i] = T.indexer(("RAMP", 0, 1, 7, ()), g.trigger)
    assert F.diff(T.eval_graph(T.Graph(nodes), 10), T.oracle(prog, {}, 10)) is not None


def test_mutation_a_wrong_orderlist_entry_fails_the_law():
    """The block comes off the machine's own address bus; point it elsewhere and it fails."""
    _mem, prog = _arrprog(nblocks=2, wrap=4, patlen=32)
    graph = T._graph(prog, None, *T._observe(prog, {}, 10))[0]
    assert F.diff(T.eval_graph(graph, 10), T.oracle(prog, {}, 10)) is None
    nodes, sel = list(graph.nodes), [
        i for i, g in enumerate(graph.nodes) if g.transfer[0] == "SELECT"
    ]
    a, b = (nodes[i] for i in sel)  # the two song steps name each other's block
    nodes[sel[0]], nodes[sel[1]] = a._replace(transfer=b.transfer[:2] + a.transfer[2:]), b._replace(
        transfer=a.transfer[:2] + b.transfer[2:]
    )
    assert F.diff(T.eval_graph(T.Graph(nodes), 10), T.oracle(prog, {}, 10)) is not None


# ---- 4h. the node identity: a declared region at a cursor the program text names --
_CUR = (0x0800, 0x0801)


def _pairprog(mem, decls, reads, cells=_CUR, steps=(1, 1), extra=()):
    """``FrameProgram`` reading one region per cursor cell, then stepping every cursor."""
    stmts = [("asg", "i%d" % k, ("mem", ("const", c, 2), 1)) for k, c in enumerate(cells)]
    stmts += [("st", ("const", 0xD400 + reg, 2), val) for reg, val in reads]
    stmts += [
        (
            "st",
            ("const", c, 2),
            ("op", "INT_ADD", (("mem", ("const", c, 2), 1), ("const", s, 1)), 1),
        )
        for c, s in zip(cells, steps)
    ]
    return frameprog.FrameProgram(
        0x1000,
        0x0F00,
        decls=decls,
        mem0=mem,
        procs=[(0x1000, [], [], list(extra) + stmts + [("ret",)])],
    )


def _twoobj(decl=None, base=0x2000, size=16, half=8):
    """``(image, program)``: one flat declaration holding two objects, a cursor each."""
    mem = bytearray(0x10000)
    for i in range(size):
        mem[base + i] = 0x10 + i
    reads = [(0x15, _sel(base, 0, "i0")), (0x16, _sel(base + half, 0, "i1"))]
    return mem, _pairprog(mem, [decl or _table(base, size)], reads)


def _regions(prog, diag=None):
    """The regions the program text names, off the declarations."""
    return T._objects(prog, T._banks(prog), Counter() if diag is None else diag)


def _lww_keys(prog, nframes=4, objs=None):
    """``{register: transfer}`` for the lww streams, with or without the pair regions."""
    _gt, _ords, lww, acc = T._observe(prog, {}, nframes)
    objs = acc[4][0] if objs is None else objs
    streams, _e = T._lww_streams(lww, T._tree_tables(prog, T._banks(prog)), prog.mem0, objs)
    return {reg: t for _c, t, reg, *_e in streams}


def test_a_region_is_the_load_base_the_text_names_not_the_whole_declaration():
    """One declaration tiles the block; the bases the text indexes resolve the objects."""
    mem, prog = _twoobj()
    assert _regions(prog) == [T.Region(0x2000, 8, 1, (0x0800,)), T.Region(0x2008, 8, 1, (0x0801,))]
    assert T.gate(prog, {}, 4) is None
    got = _lww_keys(prog)
    assert got[0x15] == ("SELECT", tuple(mem[0x2000:0x2008]), (0, 1, 2, 3))
    assert got[0x16] == ("SELECT", tuple(mem[0x2008:0x2010]), (0, 1, 2, 3))
    whole = _lww_keys(prog, objs=())  # the declaration alone reads one block at two offsets
    assert whole[0x15] == ("SELECT", tuple(mem[0x2000:0x2010]), (0, 1, 2, 3))
    assert whole[0x16] == ("SELECT", tuple(mem[0x2000:0x2010]), (8, 9, 10, 11))


def test_the_lanes_of_a_record_array_are_regions_of_their_own():
    """A strided declaration is a record array, so a named base is one lane of it."""
    mem, decl = _bank(0x2000, 4, 4, {2: [0x11, 0x22, 0x33, 0x44], 3: [0x51, 0x52, 0x53, 0x54]})
    prog = _rowprog(mem, decl, [(5, _sel(0x2000, 2, "i")), (6, _sel(0x2000, 3, "i"))], step=4)
    assert _regions(prog) == [
        T.Region(0x2002, 14, 4, (0x0800,)),
        T.Region(0x2003, 13, 4, (0x0800,)),
    ]
    assert T.gate(prog, {}, 4) is None and T.render(prog, {}, 4)[2].planes["ad"] == (4, 4)


def test_a_base_the_program_text_names_no_cursor_for_is_refused():
    """The pair has no identity without a cursor cell, so the whole declaration keys it."""
    mem = bytearray(0x10000)
    mem[0x2000:0x2010] = bytes(range(0x10, 0x20))
    const = ("asg", "j", ("const", 2, 1))
    prog = _pairprog(mem, [_table(0x2000, 16)], [(0x15, _sel(0x2000, 0, "j"))], extra=[const])
    diag = Counter()
    assert _regions(prog, diag) == [] and diag["pair_no_cursor"] == 1
    assert T.gate(prog, {}, 4) is None
    assert _lww_keys(prog)[0x15] == ("SELECT", tuple(mem[0x2000:0x2010]), (2, 2, 2, 2))


def test_a_load_base_outside_every_declaration_is_refused():
    """A base no declaration covers holds no const data, whatever byte it agrees with."""
    mem = bytearray(0x10000)
    mem[0x3000:0x3010] = bytes(range(0x10, 0x20))
    prog = _pairprog(mem, [_table(0x2000, 16)], [(0x15, _sel(0x3000, 0, "i0"))])
    diag = Counter()
    assert _regions(prog, diag) == [] and diag["pair_base_undeclared"] == 1
    assert T.gate(prog, {}, 4) is None


def test_a_region_stops_where_the_declaration_names_a_play_written_offset():
    """``mut`` bounds the object as it bounds the lane: const data stops at the first one."""
    mem, prog = _twoobj(decl=_muted(_table(0x2000, 16), [4]))
    assert _regions(prog)[0] == T.Region(0x2000, 4, 1, (0x0800,))
    diag = Counter()
    _mem2, whole = _twoobj(decl=_muted(_table(0x2000, 16), [8]))
    assert [r.base for r in _regions(whole, diag)] == [0x2000] and diag["pair_region_mut"] == 1
    assert T.gate(prog, {}, 4) is None and T.gate(whole, {}, 4) is None


def test_the_cursor_value_is_counted_but_does_not_key_the_node():
    """The cursor's own value is watched and priced; keying the node on it costs matches."""
    _mem, prog = _twoobj()
    diag = Counter()
    T.render(prog, {}, 4, diag)
    assert diag["pair_cursor_verified"] == 8 and not diag["pair_cursor_unverified"]
    opaque = ("st", ("const", 0x0800, 2), ("mem", ("const", 0x0900, 2), 1))
    mem, blind = _twoobj()
    blind = _pairprog(
        mem,
        blind.data_decls,
        [(0x15, _sel(0x2000, 0, "i0")), (0x16, _sel(0x2008, 0, "i1"))],
        extra=[opaque],
    )
    diag2 = Counter()
    T.render(blind, {}, 4, diag2)
    assert diag2["pair_cursor_unverified"] == 3 and diag2["pair_cursor_verified"] == 5
    assert len(_lww_keys(blind)) == 2  # unverified or not, the region is one node


def test_mutation_a_region_read_at_another_regions_base_fails_the_law():
    """The row is the index the machine read off the text's own base: move the base and it fails."""
    mem, prog = _twoobj()
    assert _mutated(prog, 0x15, lambda t: t) is None
    moved = _mutated(prog, 0x15, lambda t: ("SELECT", tuple(mem[0x2008:0x2010]), t[2]))
    assert moved is not None and moved.section == "filter"
    seed = _mutate_node(prog, lambda g: g.route == T.INDEX, lambda t: (t[0], t[1] + 1) + t[2:])
    assert seed is not None and seed.section == "filter"


def test_what_a_row_fitted_to_the_byte_would_have_taken_is_counted():
    """A row searched over the region for the byte is refused, and §6 prices the search."""
    mem = bytearray(0x10000)
    mem[0x2000:0x2010] = bytes(range(0x10, 0x20))
    reads = [(0, _sel(0x2000, 0, "i0")), (7, ("const", 0x1F, 1))]
    prog = _pairprog(mem, [_table(0x2000, 16)], reads)
    diag = Counter()
    T._graph(prog, None, *T._observe(prog, {}, 4), diag)
    assert diag["pair_fitted"] == 4 and diag["pair_emits"] == 4


# ---- 4i. the sequencer: a tick clock, a row cursor, and the table it rows ---------
def _chain_nodes(prog, nframes=4):
    """``(cursor RAMPs, SELECTs read at a generated row)`` of a program's graph."""
    nodes = T._graph(prog, None, *T._observe(prog, {}, nframes))[0].nodes
    rows = [g for g in nodes if g.route == T.INDEX]
    read = [g for g in nodes if g.transfer[0] == "SELECT" and T._generated(g.transfer[2])]
    return rows, read


def test_a_lane_is_read_at_the_row_its_own_cursor_walks():
    """Link 2: the row stops being the observed run and becomes the cursor's own RAMP."""
    mem, prog = _twoobj()
    rows, read = _chain_nodes(prog)
    assert [g.transfer for g in rows] == [("RAMP", 0, 1, 256, ())] * 2
    assert [g.transfer[1] for g in read] == [tuple(mem[0x2000:0x2008]), tuple(mem[0x2008:0x2010])]
    assert [g.transfer[2] for g in read] == [("node", 1), ("node", 2)]
    assert T.gate(prog, {}, 4) is None


def test_the_cursor_is_beaten_by_its_own_step_statement_not_by_the_read():
    """The RAMP steps where the text stepped the cell, so a read is what the cursor holds."""
    mem, prog = _twoobj()
    _gt, _ords, _lww, acc = T._observe(prog, {}, 4)
    assert acc[4][2] == {0x0800: (1, 1, 1, 1), 0x0801: (1, 1, 1, 1)}
    rows, _read = _chain_nodes(prog)
    assert all(g.trigger == ("event", 0) for g in rows)  # the beat stream, not the lane's
    assert T._rows_at((0, 1, 256), (1, 1, 1, 1), (1, 1, 1, 1)) == [0, 1, 2, 3]
    assert T._rows_at((0, 1, 256), (0, 1, 1, 1), (1, 1, 1, 1)) is None  # a read before a beat
    assert mem[0x2000] == 0x10


def test_a_cursor_some_writer_reloads_is_refused_and_keeps_its_run():
    """A ``RAMP`` walks and never resets, so a cell a ``set`` rule writes is not a cursor."""
    mem = bytearray(0x10000)
    mem[0x2000:0x2010] = bytes(range(0x10, 0x20))
    reset = ("st", ("const", 0x0800, 2), ("const", 0, 1))
    prog = _pairprog(mem, [_table(0x2000, 16)], [(0x15, _sel(0x2000, 0, "i0"))], extra=[reset])
    diag = Counter()
    T._graph(prog, None, *T._observe(prog, {}, 4), diag)
    assert diag["chain_cursor_reset"] == 1 and not diag["chain_rows_generated"]
    assert not _chain_nodes(prog)[0] and T.gate(prog, {}, 4) is None


def test_a_row_stream_the_walk_does_not_reproduce_keeps_its_recovered_run():
    """The rows are predicted, never solved for: one the cursor's walk misses is refused."""
    mem = bytearray(0x10000)
    mem[0x2000:0x2010] = bytes(range(0x10, 0x20))
    plain = _pairprog(mem, [_table(0x2000, 16)], [(0x15, _sel(0x2000, 0, "i0"))], cells=(0x0800,))
    diag = Counter()
    T._graph(plain, None, *T._observe(plain, {}, 8), diag)
    assert diag["chain_rows_generated"] == 8 and T.gate(plain, {}, 8) is None
    narrowed = ("op", "INT_AND", (("loc", "i0"), ("const", 3, 1)), 1)
    read = (
        "mem",
        ("op", "INT_ADD", (("const", 0x2000, 2), ("op", "INT_ZEXT", (narrowed,), 2)), 2),
        1,
    )
    masked = _pairprog(mem, [_table(0x2000, 16)], [(0x15, read)], cells=(0x0800,))
    diag2 = Counter()
    T._graph(masked, None, *T._observe(masked, {}, 8), diag2)
    assert diag2["chain_rows_unwalked"] == 8 and not diag2["chain_rows_generated"]
    assert not _chain_nodes(masked, 8)[0] and T.gate(masked, {}, 8) is None


def test_mutation_a_wrong_wrap_or_step_on_the_cursor_fails_the_law():
    """The modulus and the step are program text, and the law checks both."""
    _mem, prog = _twoobj()
    pick = lambda g: g.route == T.INDEX  # noqa: E731  pylint: disable=unnecessary-lambda-assignment
    assert _mutate_node(prog, pick, lambda t: t) is None
    assert _mutate_node(prog, pick, lambda t: t[:3] + (2, ())) is not None  # a wrong wrap
    assert _mutate_node(prog, pick, lambda t: t[:2] + (2,) + t[3:]) is not None  # wrong step


# ---- 4j. the song: terminator-bounded regions at cursors the program text steps ---
_SONG, _SLO, _SHI, _CUR_ROW, _PARM = 0x3000, 0x3200, 0x3204, 0x0810, 0x0811


def _deref(idx):
    """``*ptr[idx]``: the base-less deref rung (f) proves."""
    return ("mem", ("op", "INT_ADD", (("mem", ("const", 0x04, 2), 2), idx), 2), 1)


def _step1(cell):
    """``cell = cell + 1``: one walked increment of a cursor."""
    add = ("op", "INT_ADD", (("mem", ("const", cell, 2), 1), ("const", 1, 1)), 1)
    return ("st", ("const", cell, 2), add)


def _flag(name, mask):
    return (
        "op",
        "INT_NOTEQUAL",
        (("op", "INT_AND", (("loc", name), ("const", mask, 1)), 1), ("const", 0, 1)),
        1,
    )


def _songprog(blocks=(0x3000, 0x3010), data=None, term=0xFF, size=4, extra=()):
    """``(image, program)``: a pointer over terminator-bounded regions a cursor walks.

    Row 0 is the row byte; bit 7 takes a parameter byte and steps the cursor once more;
    the region ends where the text compares the byte at the cursor against ``term``."""
    mem = bytearray(0x10000)
    for k, b in enumerate(blocks):
        mem[_SLO + k], mem[_SHI + k] = b & 0xFF, b >> 8
    for b in blocks:
        for i, v in enumerate(data or (0x81, 0x22, 0x03, term)):
            mem[b + i] = v
    word = (
        "op",
        "INT_OR",
        (
            (
                "op",
                "INT_ZEXT",
                (("mem", ("op", "INT_ADD", (("const", _SLO, 2), _zext("k")), 2), 1),),
                2,
            ),
            (
                "op",
                "INT_LEFT",
                (
                    (
                        "op",
                        "INT_ZEXT",
                        (("mem", ("op", "INT_ADD", (("const", _SHI, 2), _zext("k")), 2), 1),),
                        2,
                    ),
                    ("const", 8, 1),
                ),
                2,
            ),
        ),
        2,
    )
    live = ("op", "INT_ZEXT", (("mem", ("const", _CUR_ROW, 2), 1),), 2)
    stmts = [
        ("asg", "k", ("const", 0, 1)),
        ("st", ("const", 0x04, 2), word),
        ("asg", "y", ("mem", ("const", _CUR_ROW, 2), 1)),
        ("asg", "b", _deref(_zext("y"))),
        ("st", ("const", 0xD400, 2), ("loc", "b")),
        (
            "if",
            "if",
            _flag("b", 0x80),
            [
                _step1(_CUR_ROW),
                ("asg", "p", _deref(live)),
                ("st", ("const", _PARM, 2), ("loc", "p")),
            ],
            [],
        ),
        _step1(_CUR_ROW),
        ("asg", "t", _deref(live)),
        (
            "if",
            "if",
            ("op", "INT_EQUAL", (("loc", "t"), ("const", term, 1)), 1),
            [("st", ("const", _CUR_ROW, 2), ("const", 0, 1))],
            [],
        ),
    ]
    decls = [
        dict(_table(_SONG, size), mut=[], role=None),
        dict(_table(_SLO, len(blocks)), mut=[], role=("lo", _SHI)),
        dict(_table(_SHI, len(blocks)), mut=[], role=("hi", _SLO)),
    ]
    prog = frameprog.FrameProgram(
        0x1000,
        0x0F00,
        decls=decls,
        mem0=mem,
        procs=[(0x1000, [], [], list(extra) + stmts + [("ret",)])],
    )
    return mem, prog


def _charts(prog):
    diag = Counter()
    return T._charts(prog, T._banks(prog), None, diag), diag


def test_a_region_runs_to_the_byte_the_program_text_compares_for():
    """The declaration floors the extent; the terminator compare is what ends it."""
    _mem, prog = _songprog()
    charts, diag = _charts(prog)
    assert len(charts) == 1 and charts[0].terms == (0xFF,)
    assert [b.size for b in charts[0].blocks] == [4, 4] and diag["song_regions"] == 2
    assert charts[0].cursor == _CUR_ROW


def test_a_terminator_past_the_declared_extent_extends_the_region():
    """The declared size is a floor: the region runs on to the terminator the text names."""
    _mem, prog = _songprog(data=(0x01, 0x02, 0x01, 0x04, 0x01, 0x06, 0xFF), size=4)
    charts, _diag = _charts(prog)
    assert [b.size for b in charts[0].blocks] == [7, 7]
    assert [len(b.rows) for b in charts[0].blocks] == [6, 6]


def test_the_row_cursor_steps_once_per_walked_increment():
    """A row byte whose parameter bit is set takes three bytes, one per guarded step."""
    _mem, prog = _songprog(data=(0x81, 0x22, 0x03, 0x05, 0x06, 0xFF), size=6)
    rows = _charts(prog)[0][0].blocks[0].rows
    assert [off for off, _f in rows] == [0, 2, 3, 4]
    assert [[v for _o, v, _c in f] for _off, f in rows] == [[0x81, 0x22], [0x03], [0x05], [0x06]]


def test_a_regions_row_fields_are_the_masks_the_text_tests_on_its_own_byte():
    """The mask whose arm steps the cursor further is what takes a parameter byte."""
    _mem, prog = _songprog()
    assert _charts(prog)[0][0].roles[1] == {0x80: "parameter"}


def test_a_region_the_text_compares_no_byte_of_is_refused():
    """Without a walked comparison there is no terminator, so there is no region."""
    _mem, prog = _songprog()
    stmts = [s for s in prog.procs[0][3] if not (s[0] == "if" and s[2][1] == "INT_EQUAL")]
    bad = frameprog.FrameProgram(
        0x1000,
        0x0F00,
        decls=prog.data_decls,
        mem0=prog.mem0,
        procs=[(0x1000, [], [], stmts)],
    )
    charts, diag = _charts(bad)
    assert charts == [] and diag["song_no_terminator"] == 1


def test_the_song_rides_the_graph_and_carries_no_observation():
    """``Graph.charts`` is declared data at program-text offsets: no frame is read."""
    _mem, prog = _songprog()
    graph = T._graph(prog, None, *T._observe(prog, {}, 8))[0]
    assert len(graph.charts) == 1
    assert graph.charts[0].blocks[0].data == (0x81, 0x22, 0x03, 0xFF)
