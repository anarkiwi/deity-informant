"""Statement-tree rewrites of the printed C (csyntax): sections, returns and guards."""

from deity_informant.cgen.csyntax import (
    CONTINUE,
    RETURN,
    cost,
    flattened,
    guarded,
    returning,
    sections,
)


def a(name, *reads):
    """An assignment of ``name`` reading ``reads``."""
    return ("a", name, " + ".join(reads) or "0", frozenset(reads))


def s(text, *reads):
    """A statement reading ``reads``."""
    return ("s", text, frozenset(reads))


def goto(name):
    """A jump to label ``name``."""
    return ("j", f"goto {name};", frozenset())


def label(name):
    """The label ``name``."""
    return ("l", f"{name}:;", frozenset())


def iff(cond, then, els=(), neg=None):
    """An ``if`` of ``cond`` (``neg`` its negation)."""
    return ("if", cond, frozenset(), list(then), list(els), neg or f"!({cond})")


def test_a_label_no_local_crosses_cuts_the_body():
    """What follows a label no local is used across and no goto crosses (but into it from
    before) is a section of its own."""
    body = [a("v1", "p"), iff("v1", [goto("L1")]), s("w(v1);", "v1"), label("L1"), a("v2", "p")]
    assert sections(body, {"v1", "v2"}) == [("L1", body[:3]), (None, body[4:])]


def test_a_local_used_across_a_label_keeps_one_section():
    """A local read after the label it is set before keeps the body whole."""
    body = [a("v1", "p"), label("L1"), s("w(v1);", "v1")]
    assert sections(body, {"v1"}) == [(None, body)]


def test_a_goto_past_the_label_keeps_one_section():
    """A goto from before the label to one after it, or back from after it, keeps the body
    whole."""
    past = [iff("p", [goto("L2")]), label("L1"), s("x();"), label("L2"), s("y();")]
    back = [label("L0"), s("x();"), label("L1"), iff("p", [goto("L0")])]
    assert len(sections(past, set())) == 2 and sections(past, set())[0][0] == "L2"
    assert len(sections(back, set())) == 1


def test_gotos_to_the_label_ending_a_section_return():
    """Each goto to the label ending a section, nested ones included, is a return."""
    got = returning([iff("p", [goto("L1")], [goto("L9")])], "L1")
    assert got[0][3] == [RETURN] and got[0][4] == [goto("L9")]


def test_a_last_if_is_a_guard_where_that_costs_less():
    """A function's last ``if`` returns first and holds its statements after it where that
    lowers the cognitive complexity (it holds nested control); of one with an ``else``, the
    arm with more nested control is after it; a last ``if`` holding none stays."""
    inner = iff("q", [s("y();")], neg="!q")
    got = guarded([s("x();"), iff("p", [s("z();"), inner], neg="!p")])
    assert got == [s("x();"), ("if", "!p", frozenset(), [RETURN], [], "p"), s("z();"), inner]
    both = [iff("p", [s("x();")], [iff("q", [s("y();"), inner], neg="!q")], neg="!p")]
    assert guarded(both) == [
        ("if", "p", frozenset(), [s("x();"), RETURN], [], "!p"),
        ("if", "!q", frozenset(), [RETURN], [], "q"),
        s("y();"),
        inner,
    ]
    flat = [iff("p", [s("x();")], neg="!p")]
    assert guarded(flat) == flat


def test_an_arm_leaving_first_is_the_lighter():
    """An ``if`` whose arm returns before the rest of a function holds more nested control
    than the rest leaves first by the rest, the arm after it."""
    heavy = [s("x();"), iff("q", [s("y();")], neg="!q"), RETURN]
    got = guarded([iff("p", heavy, neg="!p"), s("z();")])
    assert got == [("if", "!p", frozenset(), [s("z();"), RETURN], [], "p"), *heavy]


def test_costs_follow_the_cognitive_complexity_rules():
    """An ``if`` costs one plus its depth, an ``else if`` and an ``else`` one, a ``goto`` one."""
    chain = iff("p", [goto("L1")], [iff("q", [s("x();")], [s("y();")])])
    assert cost([chain]) == 1 + 1 + 1 + 1
    assert cost([iff("p", [iff("q", [])])], 2) == (1 + 2) + (1 + 3)


def test_an_else_ending_in_a_jump_is_tested_first():
    """An ``if`` whose ``else`` ends in a jump and whose ``then`` does not tests the other
    way, the jump first and the ``then`` after it, nested ones included; one whose ``then``
    jumps stays."""
    got = flattened(
        [iff("p", [s("x();"), iff("q", [s("y();")], [RETURN], neg="!q")], [RETURN], neg="!p")]
    )
    assert got == [
        ("if", "!p", frozenset(), [RETURN], [], "p"),
        s("x();"),
        ("if", "!q", frozenset(), [RETURN], [], "q"),
        s("y();"),
    ]
    kept = [iff("p", [RETURN], [RETURN])]
    assert flattened(kept) == kept


def test_a_loop_body_ending_in_an_if_continues():
    """A loop body's last ``if`` holding nested control continues the loop when its
    condition fails, its statements after it."""
    inner = iff("q", [s("w();")], neg="!q")
    loop = ("loop", "for", "", frozenset(), [s("x();"), iff("p", [s("y();"), inner], neg="!p")])
    body = flattened([loop])[0][4]
    assert body == [s("x();"), ("if", "!p", frozenset(), [CONTINUE], [], "p"), s("y();"), inner]
    switch = ("switch", "k", frozenset(), [((1,), [iff("p", [inner], [RETURN], neg="!p")])])
    assert flattened([switch])[0][3][0][1][0][3] == [RETURN]
