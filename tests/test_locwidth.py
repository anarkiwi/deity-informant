"""Word-typed locals and the truncation form.

``("loc", name, 2)`` is a 16-bit local (the 2-tuple stays one byte) and
``("op", "COPY", (x,), w)`` prints as ``trunc<w>``; both round trip through the
grammar at every use site.
"""

import pytest

from deity_informant import expr as E
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import grammar as G

_DOC = "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n%s\n  ret\n}\n"


def _round_trip(lines):
    text = frameprog.dumps(frameprog.loads(_DOC % "\n".join(lines)))
    assert frameprog.dumps(frameprog.loads(text)) == text
    return text.splitlines()


def test_a_bare_loc_is_a_byte_and_a_three_tuple_carries_its_width():
    assert frameproc.loc_width(("loc", "w")) == 1
    assert frameproc.loc_width(("loc", "w", 2)) == 2
    assert G.store_width(("loc", "w")) == 1
    assert G.store_width(("loc", "w", 2)) == 2


def test_a_word_local_round_trips_through_the_text():
    lines = ["  w:2 = (m_1400:2 + zext2(m_1500)):2", "  m_1600:2 = w:2"]
    assert set(lines) <= set(_round_trip(lines))


def test_a_word_local_parses_as_a_three_tuple_and_a_byte_local_does_not():
    stmts = frameprog.loads(_DOC % "  w:2 = m_1400:2\n  a = m_1500\n  m_1600:2 = w:2").procs[0][3]
    assert stmts[0] == ("asg", "w", ("mem", ("const", 0x1400, 2), 2))
    assert stmts[1] == ("asg", "a", ("mem", ("const", 0x1500, 2), 1))
    assert stmts[2] == ("st", ("const", 0x1600, 2), ("loc", "w", 2))


@pytest.mark.parametrize(
    "line",
    [
        "  a = trunc1(m_1400:2)",
        "  w:2 = trunc2((m_1400:2 + $0001):2)",
        "  m_1600 = trunc1(w:2)",
    ],
)
def test_the_truncation_form_round_trips(line):
    assert line in _round_trip(["  w:2 = m_1400:2", line])


def test_a_truncation_masks_to_its_output_width():
    val = ("op", "COPY", (("const", 0x1234, 2),), 1)
    assert E.evaluate(val, bytearray(0x10000), [0] * 16, bytearray(0x10000), {}) == 0x34


def _run(stmts, cells=()):
    mem0 = bytearray(0x10000)
    for a, v in cells:
        mem0[a] = v
    procs = [(0x1000, [], [], list(stmts) + [("ret", False)])]
    prog = frameprog.FrameProgram(0x1000, 0x0F00, procs=procs, mem0=mem0)
    ev = frameval.Evaluator(prog, {})
    ev.run_frame()
    return ev.m


def test_a_word_local_store_takes_the_two_byte_path_low_byte_first():
    m = _run(
        [
            ("asg", "w", ("mem", ("const", 0x1400, 2), 2)),
            ("st", ("const", 0x1600, 2), ("loc", "w", 2)),
        ],
        cells=((0x1400, 0x34), (0x1401, 0x12)),
    )
    assert (m[0x1600], m[0x1601]) == (0x34, 0x12)


def test_a_truncated_word_stores_one_cell():
    m = _run(
        [
            ("asg", "a", ("op", "COPY", (("mem", ("const", 0x1400, 2), 2),), 1)),
            ("st", ("const", 0x1600, 2), ("loc", "a")),
        ],
        cells=((0x1400, 0x34), (0x1401, 0x12)),
    )
    assert (m[0x1600], m[0x1601]) == (0x34, 0x00)
