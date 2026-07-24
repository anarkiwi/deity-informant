"""Acceptance tests for the cross-frame state-machine lift (``deity_informant.kernel``).

Over the synthetic-player corpus (``_fuzzgen``): the closed-loop model reproduces
the recorder's write stream byte-exact (Tier A), guards self-drive it (Tier B), and
the memory partition / template dedup match each generator's known idiom.
"""

import pytest

from deity_informant import lift_kernel, record, run_sub

from examples.hello_world import EXPECTED, ORG, PROGRAM

import _fuzzgen as G

_PLAYERS = G.players(8)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]


def _image(cells):
    m = bytearray(0x10000)
    for a, v in cells.items():
        m[a] = v
    return m


def _kernel(p):
    rec = record(_image(p.image_data()), run_sub, p.org, p.outputs, p.frames)
    return lift_kernel(rec)


def _by_name(name):
    p = next(pl for pl in _PLAYERS if pl.name == name)
    return p, _kernel(p)


# ---- closed-loop soundness over the whole corpus -----------------------------
@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_tier_a_closed_loop_byte_exact(p):
    """Re-iterating from S0 with the recorded templates is byte-exact + closed."""
    assert _kernel(p).verify() == (True, None), (p.name, p.seed)


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_tier_b_self_driving(p):
    """Guards alone select each frame's variant and reproduce the writes."""
    assert _kernel(p).verify(self_driving=True) == (True, None), (p.name, p.seed)


# ---- memory partition --------------------------------------------------------
def test_partition_tables_vs_state():
    _, k = _by_name("table_index")
    assert k.state == set() and k.tables  # pure constant-table read, no state
    _, k = _by_name("smc_operand")
    assert k.state and not any(a in k.tables for a in k.state)  # disjoint T / S


def test_state_is_the_self_modified_cell():
    p, k = _by_name("smc_operand")
    assert len(k.state) == 1 and (p.org + 3) in k.state  # patched STA operand-low byte


def test_counter_cell_is_state():
    _, k = _by_name("illegal_dcp_isc")
    assert G.CNT in k.state


# ---- template dedup / compactness --------------------------------------------
def test_smc_operand_collapses_to_one_path():
    p, k = _by_name("smc_operand")
    assert len(k.variants) == 1 and k.variants[0].frames == list(range(p.frames))


def test_dec_timer_has_two_control_paths():
    _, k = _by_name("dec_timer")
    assert len(k.variants) == 2
    obs = {g[3] for v in k.variants for g in v.guard}
    assert obs == {0, 1}  # the two branch outcomes of the down-counter


def test_variant_count_never_exceeds_frames():
    for p in _PLAYERS:
        k = _kernel(p)
        assert 1 <= len(k.variants) <= p.frames


# ---- closed-form transition function -----------------------------------------
def test_smc_operand_transition_is_increment():
    p, k = _by_name("smc_operand")
    cell = p.org + 3
    expr = k.variants[0].transition[cell]  # S'[cell] = (S[cell] + 1) & 0xFF
    assert expr[0] == "op" and expr[1] == "INT_ADD"
    kids = expr[2]
    assert ("mem", ("const", cell, 2), 1) in kids and ("const", 1, 1) in kids


def test_output_address_is_state_dependent():
    _, k = _by_name("smc_operand")
    outs = [e for e in k.variants[0].sslog if e[3]]  # is_output stores
    assert outs and outs[0][0][0] == "op"  # SID register written is a function of state


# ---- hello_world -------------------------------------------------------------
def test_hello_world_state_machine():
    mem = _image({ORG + i: b for i, b in enumerate(PROGRAM)})
    outs = range(0x0400, 0x0400 + len(EXPECTED))
    k = lift_kernel(record(mem, run_sub, ORG, outs, 1))
    assert k.verify() == (True, None)
    assert k.verify(self_driving=True) == (True, None)
    assert (ORG + 0x0A) in k.state  # the ISC-patched STA operand byte
    n_out = sum(1 for e in k.variants[0].sslog if e[3])
    assert n_out == len(EXPECTED)  # one output write per screen code


# ---- determinism & rendering -------------------------------------------------
def test_determinism():
    p = _PLAYERS[0]
    assert _kernel(p).pretty() == _kernel(p).pretty()


def test_pretty_renders_every_class():
    for p in _PLAYERS:
        text = _kernel(p).pretty()
        assert "tables:" in text and "variant 0" in text


def test_pretty_leaf_kinds():
    _, k = _by_name("smc_operand")
    assert k._s(("reg", 0)) == "A"
    assert k._s(("uni", 3, 1)) == "U3"
    assert k._s(("op", "INT_SUB", (("const", 5, 1), ("const", 2, 1)), 1)) == "($5 - $2)"
    assert k._s(("mem", ("const", 0x9999, 2), 1)) == "M[$9999]"  # non-partitioned const


# ---- divergence reporting ----------------------------------------------------
def test_closure_divergence_detected():
    p, k = _by_name("smc_operand")
    bad = bytearray(k.rec.entry[1][0])
    bad[p.org + 3] ^= 0xFF  # break frame-1 entry so it no longer equals frame-0 end
    k.rec.entry[1] = (bytes(bad), k.rec.entry[1][1])
    assert k.verify() == (False, ("closure", 1))


def test_no_variant_when_guards_undecidable():
    _, k = _by_name("dec_timer")
    for v in k.variants:
        v.guard = []  # strip the discriminating branch fact
    ok, div = k.verify(self_driving=True)
    assert not ok and div[0] == "no-variant"


def test_empty_recording():
    _, k = _by_name("smc_operand")
    k.rec.slog = []
    assert k.verify() == (True, None)
