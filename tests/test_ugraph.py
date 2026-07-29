"""Gate U0: the universal generator graph against the player's projection.

Covers the law of docs/tracker-unification.md 4, the RAW completeness floor,
refinement out of RAW, and mutation evidence that the gate can fail.
"""

import numpy as np
import pytest

from deity_informant import framelog as F
from deity_informant import structured as S
from deity_informant import ugraph as U
from deity_informant.c64 import load_psid

import _fuzzgen as G

from test_frameprog import _fuzz_model


def _frames(rows):
    """Per-frame ``(reg, val)`` write lists."""
    return [list(r) for r in rows]


def test_raw_floor_passes_gate_u0_by_construction():
    """An unrefined graph is complete: it replays every write in order."""
    frames = _frames([[(0, 1), (1, 2), (4, 0x41)], [(0, 3), (4, 0x40), (4, 0x41)]])
    g = U.from_frames(frames)
    assert U.gate_u0(g, frames) is None
    cov = U.coverage(g, len(frames))
    assert cov.interpreted == 0 and cov.raw == cov.total == 6


def test_div_fires_a_downstream_lookup_on_its_edge():
    """A DIV clock is the root; the LOOKUP advances only on its Fire edge."""
    nodes = [U.div(2), U.lookup([0x11, 0x22], ("event", 0), 0)]
    recs = U.eval_graph(U.Graph(nodes), 4)
    got = [[w for sec in rec for w in sec] for rec in recs]
    assert got == [[], [(0, 0x11)], [], [(0, 0x22)]]  # fires on frames 1 and 3


def test_ramp_wraps_at_its_bound():
    nodes = [U.ramp(0xFE, 1, 0x100, U.FRAME, 2)]
    recs = U.eval_graph(U.Graph(nodes), 4)
    vals = [w[1] for rec in recs for sec in rec for w in sec]
    assert vals == [0xFE, 0xFF, 0x00, 0x01]


def test_refinement_moves_coverage_out_of_raw_and_still_gates():
    """Pulling a last-write-wins plane out of RAW keeps Gate U0 green."""
    frames = _frames([[(0, 0x10), (4, 0x41)], [(0, 0x10), (4, 0x41)]])
    residual = _frames([[(4, 0x41)], [(4, 0x41)]])
    g = U.Graph([U.raw(residual), U.lookup([0x10], U.FRAME, 0)])
    assert U.gate_u0(g, frames) is None
    cov = U.coverage(g, 2)
    assert cov.interpreted == 2 and cov.raw == 2
    assert cov.planes["v0.lww"] == (2, 2)  # freq_lo fully interpreted


def test_mutation_wrong_lookup_value_is_detected():
    frames = _frames([[(0, 0x10)], [(0, 0x10)]])
    good = U.Graph([U.lookup([0x10], U.FRAME, 0)])
    assert U.gate_u0(good, frames) is None
    bad = U.Graph([U.lookup([0x11], U.FRAME, 0)])
    d = U.gate_u0(bad, frames)
    assert d is not None and d.got == (0, 0x11) and d.want == (0, 0x10)


def test_mutation_dropped_ordered_write_is_detected():
    """The order-preserved section does not collapse a duplicate ctrl write."""
    frames = _frames([[(4, 0x40), (4, 0x41)]])
    assert U.gate_u0(U.from_frames(frames), frames) is None
    d = U.gate_u0(U.from_frames(_frames([[(4, 0x41)]])), frames)
    assert d is not None and d.section == "v0.ord"


def test_mutation_swapped_ordered_writes_are_detected():
    frames = _frames([[(4, 0x40), (5, 0x11)]])
    d = U.gate_u0(U.from_frames(_frames([[(5, 0x11), (4, 0x40)]])), frames)
    assert d is not None and d.section == "v0.ord"


def test_dangling_and_unknown_forms_raise():
    with pytest.raises(U.UGraphError, match="dangling trigger"):
        U.eval_graph(U.Graph([U.lookup([1], ("event", 9), 0)]), 1)
    with pytest.raises(U.UGraphError, match="unknown trigger"):
        U.eval_graph(U.Graph([U.Generator(("LOOKUP", (1,)), ("nope",), ("plane", 0))]), 1)
    with pytest.raises(U.UGraphError, match="unknown route"):
        U.eval_graph(U.Graph([U.Generator(("LOOKUP", (1,)), U.FRAME, ("nope",))]), 1)
    with pytest.raises(U.UGraphError, match="no value emit"):
        U.eval_graph(U.Graph([U.Generator(("DIV", 1), U.FRAME, ("plane", 0))]), 1)


@pytest.mark.parametrize("p", G.players(2), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_gate_u0_raw_floor_on_fuzz_players(p):
    """The floor holds against real player output, not just hand-built frames."""
    model = _fuzz_model(p)
    frames = F.frames_from_walker(S.Walker(model), max(p.frames, 8))
    g = U.from_frames(frames)
    assert U.gate_u0(g, frames) is None
    cov = U.coverage(g, len(frames))
    assert cov.total == sum(len(fr) for fr in frames) and cov.interpreted == 0


def test_pitch_lookup_from_a_declared_table_gates():
    """A note-indexed LOOKUP over a declared ET table reproduces the freq plane."""
    words = np.round(268 * 2 ** (np.arange(12) / 12)).astype(int)
    notes = [3, 5, 7, 9]
    frames = _frames([[(0, int(words[n]) & 0xFF), (4, 0x41)] for n in notes])
    residual = _frames([[(4, 0x41)] for _ in notes])
    lo = U.lookup([int(words[n]) & 0xFF for n in notes], U.FRAME, 0)
    g = U.Graph([U.raw(residual), lo], freq_table=tuple(int(w) for w in words))
    assert U.gate_u0(g, frames) is None
    assert U.coverage(g, len(frames)).planes["v0.lww"] == (4, 4)
