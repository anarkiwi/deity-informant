"""Def-use webs over settled statement trees: the unit a machine name is not.

A register is a location, so one spelling carries whatever was last put there.
These pin the web -- the definitions that reach a common read -- as the analysis
unit, and the three classes under which a web is refused rather than merged.
"""

from pathlib import Path

import pytest

from deity_informant import frameproc as F
from deity_informant import frameprog
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def L(n, w=None):
    return ("loc", n) if w is None else ("loc", n, w)


def C(v, w=1):
    return ("const", v, w)


def add(a, b, w=1):
    return ("op", "INT_ADD", (a, b), w)


def cell(a, w=1):
    return ("mem", C(a, 2), w)


def one(stmts, rets=()):
    return F.ProcWebs(0x1000, stmts, rets)


def test_two_definitions_reaching_one_read_are_one_web():
    """The whole point: an arm each side of a merge is one quantity, not two."""
    body = [
        ("if", "if", L("cflag"), [("asg", "a", C(1))], [("asg", "a", C(2))]),
        ("st", C(0x2000, 2), L("a")),
    ]
    pw = one(body)
    (web,) = pw.of_name("a")
    assert len(web.defs) == 2 and len(web.uses) == 1 and not web.refusals
    assert pw.at_def((0, 0, 0), "a") is web and pw.at_use((1,), "a") is web
    assert pw.at_use((1,), "y") is None and "a" in pw.names()
    assert repr(web).startswith("Web(a@") and repr(web).endswith("typed)")


def test_disjoint_arms_that_share_no_read_are_distinct_webs():
    """Exhibit A in miniature: one spelling, two quantities, two webs."""
    body = [
        ("if", "if", L("cflag"), [("asg", "y", C(1)), ("st", C(0x2000, 2), L("y"))], []),
        ("asg", "y", C(2)),
        ("st", C(0x2001, 2), L("y")),
    ]
    pw = one(body)
    got = pw.of_name("y")
    assert len(got) == 2 and [len(w.defs) for w in got] == [1, 1]
    assert pw.counts() == {"webs": 3, "refused": 1, "entry": 1, "opaque": 0, "width": 0}


def test_a_web_live_where_the_procedure_begins_is_refused():
    body = [("st", C(0x2000, 2), L("x")), ("asg", "x", C(3))]
    live, dead = one(body).of_name("x")
    assert live.refusals == {F.WEB_ENTRY} and live.root[0] == -1 and not live.defs
    assert not dead.refusals and not dead.uses, "a definition no read reaches is its own web"


def test_an_opaque_call_refuses_every_web_it_defines():
    """The ABI is not in the text, so a raw call defines and reads every register."""
    body = [("asg", "a", C(1)), ("call", 0x2000, 0x2003), ("st", C(0x2000, 2), L("a"))]
    pw = one(body)
    assert all(F.WEB_OPAQUE in w.refusals for w in pw.of_name("a") if len(w.defs) > 1)
    assert pw.counts()["opaque"] >= 1


def test_a_web_whose_sites_disagree_on_a_width_is_refused():
    body = [("asg", "q0", cell(0x2000, 2)), ("st", C(0x3000, 2), L("q0"))]
    (web,) = one(body).of_name("q0")
    assert web.widths == {1, 2} and web.refusals == {F.WEB_WIDTH}
    assert web.def_widths == {2}


def test_a_transfer_the_graph_cannot_enumerate_carries_the_registers_out():
    body = [("asg", "y", C(1)), ("goto", 0x9999)]
    (web,) = one(body).of_name("y")
    assert web.uses == [(1,)] and web.refusals == {F.WEB_OPAQUE}


def test_a_ret_reads_only_the_registers_its_header_returns():
    body = [
        ("if", "if", L("cflag"), [("asg", "a", C(1))], [("asg", "a", C(2))]),
        ("ret", 0),
    ]
    assert len(one(body, rets=("a",)).of_name("a")) == 1, "the header's return joins both arms"
    assert len(one(body).of_name("a")) == 2, "a header that returns nothing reads nothing"


def test_every_statement_shape_the_printer_spells_is_a_node():
    """One forest of every form: a shape the graph cannot place is a missing edge."""
    body = [
        ("label", 0x1100),
        ("asg", "a", C(1)),
        ("pcall", 0x2000, [L("a")], ["x"]),
        ("callb", 0x2100, 0x2103, [("asg", "y", C(2))]),
        ("for", "g4", 0, 2, [("st", C(0x2000, 2), L("g4")), ("cont",), ("brk",)]),
        ("loop", [("st", C(0x2001, 2), L("x")), ("brk",)]),
        ("opsw", C(0x30, 2), [("$1", [("asg", "a", C(3))]), ("$2", [("unobs", 0x1200)])]),
        ("dcall", L("a"), 0x2200),
        ("swc", ["$1104"], [("$1104", [("asg", "y", C(4))])]),
        ("dgoto", L("a")),
        ("swg", [("$1", [("goto", 0x1100)]), ("$2", [("igoto", 0x1300, None)])]),
        ("igoto", 0x1300, L("a")),
        ("dbr", "if", L("a"), L("x"), 0x1400),
        ("ret", 1),
    ]
    pw = one(body)
    assert pw.entry == 0x1000 and pw.webs and pw.counts()["refused"] > 0
    assert set(pw.names()) >= {"a", "x", "y", "g4"}
    assert F.web_counts([(0x1000, [], [], body)])["procs"] == 1


def test_a_statement_the_graph_does_not_know_is_an_error():
    with pytest.raises(ValueError, match="unexpected statement"):
        one([("nonesuch", 1)])


def test_the_width_law_is_the_conjunction_over_the_names_webs():
    """A width belongs to the site; the name promotes only where every web agrees."""
    words = [("asg", "q0", cell(0x2000, 2)), ("st", C(0x3000, 2), L("q0", 2))]
    F._norm_widths(words, ())
    assert words[0] == ("asg", "q0", cell(0x2000, 2)) and words[1][2] == L("q0", 2)
    mixed = list(words) + [("asg", "q0", C(1)), ("st", C(0x3001, 2), L("q0"))]
    F._norm_widths(mixed, ())
    assert mixed[3][2] == L("q0"), "a name one of whose webs is a byte does not promote"
    assert sorted(len(g) for g in F._width_webs(mixed).values()) == [2]


# ---- the two loop readings a loop-carried local reaches -------------------------
def _repolish(body):
    procs = [(0x1000, [], [], list(body))]
    F.repolish(procs, 0x1000, None, ())
    return procs[0][3]


def _acc(name):
    return ("op", "INT_XOR", (cell(0x1059), L(name)), 1)


def test_a_for_body_falls_out_of_its_loop_so_the_local_it_carries_lives():
    """A ``for`` leaves by its own bottom: the last trip's end is the loop's live-out.

    Read as the head alone, the update of an accumulator the body carries is dead at
    its own site and the sum outside reads the pre-loop constant."""
    body = [
        ("asg", "s1", C(0)),
        ("for", "y", 3, 0, [("asg", "s1", _acc("s1"))]),
        ("st", C(0xD404, 2), L("s1")),
    ]
    assert _repolish(body) == body


def test_a_levelled_exit_lands_on_the_loop_its_level_counts_out_to():
    """``continue 2`` re-reads the outer loop's head, so the definition before it
    stands and the inline context has to carry every enclosing loop, not one."""
    step = [("asg", "s1", ("op", "INT_ADD", (L("s1"), C(1)), 1))]
    inner = ("loop", step + [("if", "if", ("op", "INT_NOTEQUAL", (L("s1"), C(3)), 1), [], [])])
    inner[1][-1][3].append(("cont", 2))
    body = [("asg", "s1", C(0)), ("loop", [inner, ("brk", None)]), ("st", C(0xD404, 2), L("s1"))]
    assert _repolish(body) == body


def _tune(stem, parent):
    return [
        pytest.param(path, sub, secs, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _paths(stmts, out, path=()):
    for i, s in enumerate(stmts):
        out[path + (i,)] = s
        for bi, b in enumerate(F._stmt_bodies(s)):
            _paths(b, out, path + (i, bi))
    return out


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_y_carries_eight_webs(sid, subtune, secs):
    """§1's Exhibit A, measured: one machine name, eight unrelated quantities.

    The document reads five *denotations* off the emitted text; over the settled
    trees the voice displacement ``m_54EB`` is defined at four sites no read joins,
    so the analysis unit splits ``y`` eight ways where the name splits it none."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
    prog = frameprog.program(model)
    pw = prog.webs()[0x5012]
    assert prog.webs()[0x5012] is pw, "the analysis unit is computed once"
    at = _paths(prog.procs[0][3], {})
    got = sorted(tuple(sorted(F._fmt(at[p][2]) for p in w.defs)) for w in pw.of_name("y"))
    assert got == [
        ("(y + $01)", "m_54EF[x]"),  # the pattern row cursor
        ("(y - $01)", "m_550C"),  # the portamento repeat count
        ("(y1 << $01)",),  # a row of the pitch table
        ("m_54EB",),  # the SID voice displacement, at four sites no read joins
        ("m_54EB",),
        ("m_54EB",),
        ("m_54EB",),
        ("m_5518",),  # the pulse-width table row
    ]
    assert len(pw.of_name("x")) == 3 and len(pw.of_name("a")) == 9
    assert pw.counts() == {"webs": 47, "refused": 0, "entry": 0, "opaque": 0, "width": 0}
