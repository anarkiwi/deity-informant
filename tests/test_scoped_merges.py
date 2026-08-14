"""The reducible label class: a scope and a levelled exit where a ``goto`` used to be.

A merge a structurer cannot nest is placed as a single-trip ``loop`` ending at it,
and every edge into it becomes a levelled ``break`` (Yakdan et al., NDSS 2015;
Peterson, Kasami & Tokura, CACM 16(8) 1973). One driver per canonical shape.
"""

import re

import pytest

import _dupgen as D
from deity_informant import frameprog

_GOTO = re.compile(r"(?<![a-z])goto \$")
CLOSED = tuple(n for n in D.SHAPES if n not in D.PINNED)


@pytest.mark.parametrize("name", sorted(D.SHAPES))
def test_the_shape_lifts_and_its_program_is_exact(name):
    """No pin hides a lift bug: every shape reproduces its own write log."""
    assert D.gate(name, "base") is None


@pytest.mark.parametrize("name", sorted(D.SHAPES))
def test_the_dialect_stays_total_over_the_shape(name):
    """A levelled exit round-trips and the artifact still rebuilds its own model."""
    text = D.text(name, "base")
    prog = frameprog.loads(text)
    assert frameprog.dumps(prog) == text
    assert frameprog.block_model(prog).play == prog.play


@pytest.mark.parametrize("name", sorted(CLOSED))
def test_the_merge_needs_no_label(name):
    """The answer: a ladder, a state machine's arm merge and a two-level exit close."""
    text = D.text(name, "base")
    assert [D.pcs_global(p[3]) for p in D.built(name)[1].procs] == [[]]
    assert not _GOTO.search(text), text


def test_a_two_level_exit_names_its_own_loop():
    """The dialect's whole change: the level, and it is the innermost that stays bare."""
    text = D.text("two-level", "base")
    assert re.search(r"^\s*continue 2$", text, re.M), text
    assert re.search(r"^\s*continue$", text, re.M), text


def test_a_ladder_becomes_one_scope_and_no_copy():
    """The merge is placed once: a scope, not a duplicated tail (no growth by copy).

    The second scope is the procedure's own exit (8.4), which ``one_exit`` opens where
    the first of its several returns stands."""
    text = D.text("ladder", "base")
    assert text.count("loop {") == 2
    assert text.count("sid.v1.ctrl") == 1


def test_the_state_machine_arm_merge_breaks_out_of_the_switch():
    """A ``switch goto`` arm reaches the tail by leaving the scope that ends there."""
    text = D.text("arm-merge", "base")
    assert "switch goto {" in text
    assert text.count("break") == 2  # one per arm


@pytest.mark.xfail(strict=True, reason=D.M_LOOP_MERGE)
def test_a_merge_inside_a_loop_is_scoped_too():
    """Pinned: the ladder's own join, one cycle in, still binds its pc."""
    assert [D.pcs_global(p[3]) for p in D.built("loop-merge")[1].procs] == [[]]
