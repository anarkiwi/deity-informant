"""Duplicating a pc-binding region: which bindings a copy collides on, and which not.

The rule the call-graph removal was left with was "copy freely where the region
binds no pc; qualify per copy or refuse otherwise". These drivers settle which:
``pcs_global`` names the class and Gate FP is what the naming is checked against.
"""

import pytest

import _callgen as C
import _dupgen as D
from deity_informant import frameprog, frameval

SAFE = tuple(n for n in D.DRIVERS if n not in D.REFUSED)

M_GOTO_JOIN = (
    "a `goto $PC` reaching a `label $PC` in the same region is the one binding no "
    "dispatch statement scopes: `frameproc._Ser.splice` mints it at an inlined "
    "callee's continuation pc, two copies bind it twice and `_Code._link` resolves "
    "every reference to the first -- folding the join away is the fix, not naming it"
)


@pytest.mark.parametrize("name", sorted(D.DRIVERS))
def test_the_driver_lifts_and_its_base_program_is_exact(name):
    """No pin may hide a lift bug: every driver reproduces its own write log."""
    assert D.gate(name, "base") is None
    assert frameprog.dumps(frameprog.loads(D.text(name, "base"))) == D.text(name, "base")


@pytest.mark.parametrize("name", sorted(D.DRIVERS))
def test_the_duplicated_text_round_trips_whatever_it_denotes(name):
    """The corruption is invisible to the text: a refused copy is canonical too."""
    assert frameprog.dumps(frameprog.loads(D.text(name))) == D.text(name)
    assert len(D.built(name)[2].procs) == 1


@pytest.mark.parametrize("name", sorted(SAFE))
def test_a_pc_binding_region_copies_exactly_where_no_pc_is_program_wide(name):
    """The answer: a computed dispatch duplicates cleanly, at 2 sites, 3 and nested."""
    assert D.pcs_global(D.built(name)[2].procs[0][3]) == []
    assert D.gate(name) is None


def test_the_predicate_is_exactly_gate_fp_s_verdict():
    """Binary, not a percentage: the static class and the evaluated one are one set."""
    assert {n for n in D.DRIVERS if D.gate(n) is not None} == set(D.REFUSED)
    assert {n for n in D.DRIVERS if not all(D.copy_safe(D.built(n)[1]).values())} == set(D.REFUSED)


@pytest.mark.parametrize("name", sorted(n for n in SAFE if n.startswith("dispatch/")))
def test_an_arm_s_pc_is_the_dispatch_statement_s_and_pcs_bound_over_reports_it(name):
    """``case $PC:`` is scoped: ``_arms`` keys it in a table the statement owns.

    ``_callgen.pcs_bound`` counts the label the emitter restates at the head of the
    arm, which is why the class looked non-empty; the copy is exact regardless."""
    stmts = D.built(name)[2].procs[0][3]
    assert C.pcs_bound(stmts), "the driver no longer carries a dispatch arm"
    assert D.pcs_global(stmts) == []
    assert D.gate(name) is None


@pytest.mark.parametrize(
    "name,copies", [("dispatch/2-site", 2), ("dispatch/3-site", 3), ("dispatch/nested", 4)]
)
def test_each_copy_of_a_dispatch_gets_its_own_arm_table(name, copies):
    """Why the copy is exact: one table per statement, keyed the same, disjoint arms.

    The program-wide map holds one binding per arm pc and cannot tell the copies
    apart -- and no dispatch ever consults it, which is the whole answer."""
    dup = D.built(name)[2]
    code = frameval._Code(dup)
    tables = [op[1] for op in code.ops if op[0] == "swd"]
    arms = C.pcs_bound(dup.procs[0][3])
    assert len(tables) == copies
    assert [sorted(t) for t in tables] == [arms] * copies
    assert len({i for t in tables for i in t.values()}) == copies * len(arms)
    assert all(code.pcmap[pc] == min(t[pc] for t in tables) for pc in arms)


def test_a_static_vector_s_landing_is_bound_to_its_own_igoto():
    """The implementation: the one binding inside a region that was program-wide.

    Two copies compile to two one-entry tables, so each ``igoto`` lands in its own
    copy; the program-wide bind stays, so a transfer from outside is unchanged."""
    _model, _base, dup = D.built("static-vector")
    code = frameval._Code(dup)
    tables = [op[1] for op in code.ops if op[0] == "swd"]
    assert len(tables) == 2
    assert [sorted(t) for t in tables] == [sorted(tables[0])] * 2
    assert tables[0] != tables[1]
    assert all(land in code.pcmap for t in tables for land in t)


def test_the_static_vector_driver_is_one_call_free_procedure():
    """The full 8.4 invariant on a duplicated region, not a weakened one."""
    assert not D.violations("static-vector")


@pytest.mark.parametrize("name", sorted(SAFE))
def test_duplication_removes_violation_classes_and_adds_none(name):
    """What the copy costs: the call and the extra procedure go, nothing arrives."""
    assert D.kinds(name) <= D.kinds(name, "base") - {"pcall", "procedures"}


def test_the_refusal_class_is_the_goto_join_alone():
    """Named, counted and visible: one driver, one mechanism, one bound pc."""
    assert D.pcs_global(D.built("goto-join")[1].procs[1][3]) == [0x1035]
    assert D.gate("goto-join") == "fault: runaway frame program"
    assert "goto" in M_GOTO_JOIN and "splice" in M_GOTO_JOIN


@pytest.mark.parametrize("row,label", [pytest.param(r, l, id="%s:%s" % (r, l)) for r, l in C.ALL])
def test_no_procedure_in_the_call_suite_binds_a_program_wide_pc(row, label):
    """The closure: every shape the call suite carries is copy-safe as it stands."""
    for entry, _p, _r, stmts in C.parsed(row, label).procs:
        assert D.pcs_global(stmts) == [], "sub_%04X" % entry
