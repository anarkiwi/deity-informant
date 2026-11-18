"""Statement-tree rewrites of the printed C (csyntax): sections, returns and guards."""

from deity_informant.cgen.csyntax import CONTINUE, RETURN, flattened, guarded, returning, sections


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


def test_a_last_if_with_no_else_is_a_guard():
    """A function's last ``if`` with no ``else`` returns when its condition fails and holds
    its statements after it, to a fixpoint; one with an ``else`` stays."""
    inner = iff("q", [s("y();")], neg="!q")
    got = guarded([s("x();"), iff("p", [s("z();"), inner], neg="!p")])
    assert got == [
        s("x();"),
        ("if", "!p", frozenset(), [RETURN], [], "p"),
        s("z();"),
        ("if", "!q", frozenset(), [RETURN], [], "q"),
        s("y();"),
    ]
    kept = [iff("p", [s("x();")], [s("y();")])]
    assert guarded(kept) == kept


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
    """A loop body's last ``if`` with no ``else`` continues the loop when its condition
    fails, its statements after it."""
    loop = ("loop", "for", "", frozenset(), [s("x();"), iff("p", [s("y();")], neg="!p")])
    body = flattened([loop])[0][4]
    assert body == [s("x();"), ("if", "!p", frozenset(), [CONTINUE], [], "p"), s("y();")]
    switch = ("switch", "k", frozenset(), [((1,), [iff("p", [s("y();")], [RETURN], neg="!p")])])
    assert flattened([switch])[0][3][0][1][0][3] == [RETURN]
