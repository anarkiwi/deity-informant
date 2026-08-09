"""The canonical end-to-end example, gated (docs/register-model-lift-impl.md).

The plan's stages MUST keep this pipeline green: playroutine -> decompile ->
e-graph minimize -> Z3-proved u16 folds -> role-typed state machine ->
frame-oracle equality."""

import pytest

from examples.state_machine_lift import (
    FRAMES,
    change_stream,
    classify_roles,
    framelog,
    grids_from_writes,
    pipeline,
    render,
    sidtrace_stream,
    to_psid,
)


@pytest.fixture(scope="module", name="art")
def _art():
    return pipeline()


def test_folds_all_proved(art):
    kinds = {p.split("(")[0] for p in art["proofs"]}
    assert kinds == {"pair_store", "pair_set", "advance"}
    assert "advance(ptr_00FB,+2,wide)" in art["proofs"]
    assert "advance(ptr_00FB,+2,nocarry)" in art["proofs"]


def test_minimized_matches_vm_frame_projection(art):
    assert framelog.canonical(art["min_frames"]) == framelog.canonical(art["orig_frames"])


def test_minimized_grid_matches_vm(art):
    assert grids_from_writes(art["init_writes"], art["min_frames"]) == art["orig_grids"]


def test_roles_are_the_four_the_plan_names(art):
    roles = classify_roles(art["folded"])
    assert roles["ptr_00FB"] == "cursor"
    assert roles["ctr_00FA"] == "counter"
    assert roles["ctr_00F9"] == "accumulator"
    assert {roles[n] for n in ("zp_F5", "zp_F6", "zp_F7", "zp_F8")} == {"parameter"}
    text = render(art["folded"], roles)
    assert "song_pos:u16 += 2" in text and "sid.v1.freq:u16" in text


def test_independent_engine_grid(art):
    oracle_mod = pytest.importorskip("pysidtracker.oracle")
    psid = to_psid(art["mem"], art["labels"]["script"] + 0x100)
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
