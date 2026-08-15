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
    "dispatch statement scopes: two copies bind the pc twice and `_Code._link` "
    "resolves every reference to the first, so the fix is to fold the join away -- "
    "controlled node splitting copies the RC-set entry into its own predecessor's "
    "path, and the multi-entry loop that forced the label nests as a loop"
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
def test_a_pc_binding_region_copies_exactly_where_each_pc_is_bound_once(name):
    """The answer: a computed dispatch duplicates cleanly, at 2 sites, 3 and nested.

    A landing the arms share is folded into the procedure once and reached by the
    copies' own tables, so no pc the copies carry is bound twice."""
    stmts = D.built(name)[2].procs[0][3]
    bound = D.pcs_global(stmts)
    assert len(bound) == len(set(bound))
    assert D.gate(name) is None


def test_the_predicate_is_exactly_gate_fp_s_verdict():
    """Binary, not a percentage: the static class and the evaluated one are one set."""
    assert {n for n in D.DRIVERS if D.gate(n) is not None} == set(D.REFUSED)
    assert {n for n in D.DRIVERS if not all(D.copy_safe(D.built(n)[1]).values())} == set(D.REFUSED)


@pytest.mark.parametrize("name", sorted(n for n in SAFE if n.startswith("dispatch/")))
def test_an_arm_s_pc_is_the_dispatch_statement_s_own(name):
    """``case $PC:`` is scoped: ``_arms`` keys it in a table the statement owns.

    ``_callgen.pcs_bound`` counts the label the emitter restates at the head of an
    arm where it restates one; the copy is exact either way."""
    stmts = D.built(name)[2].procs[0][3]
    bound = D.pcs_global(stmts)
    assert len(bound) == len(set(bound))
    assert D.gate(name) is None


@pytest.mark.parametrize(
    "name,copies", [("dispatch/2-site", 2), ("dispatch/3-site", 3), ("dispatch/nested", 4)]
)
def test_each_copy_of_a_dispatch_gets_its_own_arm_table(name, copies):
    """Why the copy is exact: one table per statement, keyed the same, disjoint arms.

    The program-wide map binds each arm pc at the folded landing and at none of the
    copies -- and no dispatch ever consults it, which is the whole answer."""
    dup = D.built(name)[2]
    code = frameval._Code(dup)
    tables = [op[1] for op in code.ops if op[0] == "swd"]
    arms = sorted(tables[0])
    assert len(tables) == copies and arms
    assert [sorted(t) for t in tables] == [arms] * copies
    assert len({i for t in tables for i in t.values()}) == copies * len(arms)
    assert all(pc in code.pcmap for pc in arms)
    assert not {code.pcmap[pc] for pc in arms} & {i for t in tables for i in t.values()}


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


def test_the_goto_join_is_split_away_and_the_loop_nests():
    """The class that was the refusal, emptied at its source rather than named.

    The two-entry loop the structurer could not fold is copied into the second
    entry's own path, so the callee binds no pc and the copy needs no label."""
    base = D.built("irreducible")[1]
    assert [D.pcs_global(p[3]) for p in base.procs] == [[], []]
    assert "goto $" not in D.text("irreducible", "base")
    assert D.text("irreducible", "base").count("loop {") == 1
    assert D.gate("irreducible") is None and not D.violations("irreducible")
    assert "goto" in M_GOTO_JOIN and "label" in M_GOTO_JOIN


def test_a_sole_site_callee_is_spliced_and_both_copies_are_exact():
    """The splice: an inlined callee is the caller's own text, several returns and all.

    ``Carry`` now places the body at both sites itself, so the base is already the one
    call-free procedure and ``duplicate`` has nothing left to copy: each site carries its
    own scope of the early returns, and the copy is the base's own text."""
    base, dup = D.built("early-ret")[1:]
    assert not D.violations("early-ret", "base")
    assert [D.pcs_global(p[3]) for p in base.procs] == [[]]
    assert D.text("early-ret", "base").count("loop {") == 2  # one scope per spliced site
    assert D.text("early-ret") == D.text("early-ret", "base")
    assert [D.pcs_global(p[3]) for p in dup.procs] == [[]]
    assert not D.violations("early-ret") and D.gate("early-ret") is None


M_SHARED_TAIL = (
    "a region the one procedure binds a pc for: the RTS-trick landing folded in beside "
    "the play text, reached by a transfer that resolves through the program-wide map, so "
    "a copy of the procedure would bind the pc twice. The relocated SMC dispatch left "
    "this class when rung (d1) dropped the label no transfer names (9.1)"
)
_BINDS = ("rts-trick",)


def _copy_safe():
    """Every call-suite variant, pinned where its procedure binds a program-wide pc."""
    out = []
    for row, label in C.ALL:
        mark = [pytest.mark.xfail(strict=True, reason=M_SHARED_TAIL)] if row in _BINDS else []
        out.append(pytest.param(row, label, marks=mark, id="%s:%s" % (row, label)))
    return out


@pytest.mark.parametrize("row,label", _copy_safe())
def test_no_procedure_in_the_call_suite_binds_a_program_wide_pc(row, label):
    """The closure: every shape the call suite carries is copy-safe as it stands."""
    for entry, _p, _r, stmts in C.parsed(row, label).procs:
        assert D.pcs_global(stmts) == [], "sub_%04X" % entry
