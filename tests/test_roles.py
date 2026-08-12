"""Hermetic tests for the role reading (stage 2's precondition).

Each shape is exercised on the spelling the exemplars carry -- a ``DEC`` that
lifts to ``+ $FF``, a bound spelled as a mask, a lane of a wide step -- and an
unshaped update leaves its cell un-roled rather than absorbed.
"""

import sys
from pathlib import Path

import pytest

from deity_informant import frameproc as P
from deity_informant import frameprog
from deity_informant import roles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import role_census  # pylint: disable=wrong-import-position

CELL, PAIR, ARR, SID = 0x2000, 0x00FB, 0x3000, 0xD401


def op(mn, kids, w=1):
    return ("op", mn, tuple(kids), w)


def c(v, w=1):
    return ("const", v, w)


def m(a, w=1):
    return ("mem", ("const", a, 2), w)


def loc(name, w=1):
    return ("loc", name) if w == 1 else ("loc", name, w)


def zext(x):
    return op("INT_ZEXT", (x,), 2)


def row(base, idx):
    return ("mem", op("INT_ADD", (c(base, 2), zext(idx)), 2), 1)


def pack(lo, hi):
    return op("INT_OR", (op("INT_LEFT", (zext(hi), c(8)), 2), zext(lo)), 2)


SELF, WORD, ONE = m(CELL), m(CELL, 2), {CELL}
Y = loc("y")

SHAPES = [
    ("set", c(5)),
    ("set", row(ARR, Y)),
    ("set", ("mem", op("INT_ADD", (m(CELL, 2), zext(Y)), 2), 1)),
    ("set", SELF),
    ("set", row(CELL, Y)),
    ("step-up", op("INT_ADD", (SELF, c(1)))),
    ("step-up", op("INT_ADD", (SELF, m(0x2100)))),
    ("dec", op("INT_SUB", (SELF, c(1)))),
    ("dec", op("INT_ADD", (SELF, c(0xFF)))),
    ("step-down", op("INT_ADD", (SELF, c(0xFE)))),
    ("step-down", op("INT_SUB", (SELF, m(0x2100)))),
    ("step-up", op("INT_AND", (op("INT_ADD", (SELF, m(0x2100))), c(0x07)))),
    ("field", op("INT_AND", (SELF, c(0xBF)))),
    ("field", op("INT_OR", (SELF, c(0x10)))),
    ("field", op("INT_XOR", (SELF, c(0xFF)))),
    ("field", op("INT_XOR", (loc("a"), SELF))),
    ("field", op("INT_AND", (SELF, row(0xE713, loc("x"))))),
    ("field", op("INT_OR", (op("INT_AND", (SELF, c(0xF0))), m(0x2100)))),
    (None, op("INT_LEFT", (SELF, c(1)))),
    (None, op("INT_OR", (op("INT_RIGHT", (SELF, c(1))), op("INT_LEFT", (loc("a"), c(7)))))),
]


@pytest.mark.parametrize("want,node", SHAPES, ids=[f"{i}-{s[0]}" for i, s in enumerate(SHAPES)])
def test_each_update_shape_is_read_off_its_spelling(want, node):
    assert roles.shape(node, ONE) == want


def test_the_shape_and_role_vocabularies_are_closed():
    """Every shape the reading can return is named, and every named one is reachable."""
    got = {roles.shape(n, ONE) for _w, n in SHAPES}
    assert got == set(roles.SHAPES) | {None}
    assert set(roles.ROLES) == {roles.role(s, CELL, a, w) for _r, s, a, w in ORDER} - {None}


def test_a_decrement_lifts_as_a_modular_step_up_and_is_still_a_countdown():
    """``DEC`` lifts to ``x + $FF``, so the delta is only a countdown read signed."""
    assert roles._signed(0xFF, 1) == -1 and roles._signed(0xFFFF, 2) == -1
    assert roles._signed(0x7F, 1) == 0x7F and roles._signed(0x80, 1) == -128
    assert roles.shape(op("INT_ADD", (m(PAIR, 2), c(0xFFFF, 2)), 2), {PAIR}) == "dec"


def test_a_cell_read_inside_an_address_is_no_self_reference():
    """``s' = mem[s + y]`` walks a block through the cell; it does not step it."""
    walk = ("mem", op("INT_ADD", (m(PAIR, 2), zext(Y)), 2), 1)
    assert not roles.reads_self(walk, {PAIR})
    assert roles.reads_self(op("INT_ADD", (m(PAIR, 2), c(2, 2)), 2), {PAIR})


def test_the_high_lane_of_a_wide_step_carries_the_wide_update_s_shape():
    """A byte store of ``v >> 8`` is one lane of a word update, not a shape of its own."""
    wide = op("INT_ADD", (pack(SELF, m(CELL + 1)), c(0x0040, 2)), 2)
    assert roles.shape(P.trunc_hi(wide), {CELL}) == "step-up"
    assert roles.shape(P.trunc_lo(wide), {CELL}) == "step-up"


def test_a_word_lane_write_reads_off_the_lane_and_not_the_read_back():
    """``(w & $FF00) | zext(v)`` writes one lane; the lane's value is the shape."""
    lane = op("INT_OR", (op("INT_AND", (WORD, c(0xFF00, 2)), 2), zext(m(0x2100))), 2)
    assert roles.shape(lane, ONE) == "set"


def test_a_bound_spelled_as_a_mask_does_not_hide_the_step_under_it():
    assert roles._mask_bound(op("INT_AND", (SELF, c(7)))) == (SELF, 7)
    assert roles._mask_bound(op("INT_AND", (SELF, m(0x2100)))) is None


ORDER = [
    ("vm", {"set"}, {CELL}, {CELL}),
    ("cursor", {"set"}, {CELL}, set()),
    ("cursor", {"dec"}, {CELL}, set()),
    ("counter", {"dec", "field"}, set(), set()),
    ("accumulator", {"step-up", "field"}, set(), set()),
    ("flags", {"field", "set"}, set(), set()),
    ("parameter", {"set"}, set(), set()),
    (None, {"step-up", None}, {CELL}, {CELL}),
]


@pytest.mark.parametrize("want,shapes,addr,sw", ORDER, ids=[str(r[0]) for r in ORDER])
def test_the_role_order_is_by_strength_of_evidence(want, shapes, addr, sw):
    assert roles.role(shapes, CELL, addr, sw) == want


def _prog(stmts, state, symbols=None):
    return frameprog.FrameProgram(
        0x1000, 0x0FFD, state=state, symbols=symbols or {}, procs=[(0x1000, [], [], stmts)]
    )


def test_the_census_reads_a_program_s_cells_and_names_its_residue():
    prog = _prog(
        [
            ("st", c(CELL, 2), op("INT_ADD", (SELF, c(0xFF)))),
            ("st", c(0x2100, 2), op("INT_LEFT", (m(0x2100), c(1)))),
            ("st", c(ARR, 2), row(0x3100, Y)),
            ("st", c(SID, 2), ("mem", op("INT_ADD", (c(ARR, 2), zext(m(0x2200))), 2), 1)),
            ("st", c(0x2200, 2), c(3)),
        ],
        state=tuple((n, 1, False, []) for n in ("m_2000", "m_2100", "m_3000", "m_2200")),
    )
    got, shapes, residue, _bounds = roles.census(prog)
    assert got == {CELL: "counter", 0x2100: None, ARR: "parameter", 0x2200: "cursor"}
    assert shapes[CELL] == {"dec"} and shapes[0x2100] == {None}
    assert [(u.base, u.shape) for u in residue] == [(0x2100, None)]


def test_the_dispatch_subject_is_a_vm_register_whatever_wrote_it():
    prog = _prog(
        [("st", c(CELL, 2), c(3)), ("opsw", CELL, [("$1010", [("ret", False)])])],
        state=(("m_2000", 1, False, []),),
    )
    assert roles.census(prog)[0] == {CELL: "vm"}
    assert roles.read_sites(prog) == (set(), {CELL})


def _census_prog():
    return _prog(
        [
            ("st", c(CELL, 2), op("INT_ADD", (SELF, c(0xFF)))),
            ("st", c(0x2100, 2), op("INT_LEFT", (m(0x2100), c(1)))),
            ("st", c(ARR, 2), op("INT_OR", (m(ARR), c(0x40)))),
        ],
        state=tuple((n, 1, False, []) for n in ("m_2000", "m_2100", "m_3000")),
    )


def test_the_census_row_counts_roles_and_names_every_residual_site():
    """``tools/role_census.py`` reports what ``roles.census`` read, per tune."""
    roled, shapes, residue = role_census.reading(_census_prog())
    assert dict(roled) == {"counter": 1, "flags": 1, "residue": 1}
    assert dict(shapes) == {"dec": 1, "field": 1}
    assert [(r["field"], r["base"], r["shape"]) for r in residue] == [
        ("m_2100", "$2100", "INT_LEFT1(m:1,$1)")
    ]


def test_the_census_merges_its_rows_by_role_and_by_residual_shape():
    rows = [
        {"tune": "a", "roles": {"cursor": 2}, "shapes": {"set": 2}, "residue": [{"shape": "s"}]},
        {"tune": "b", "roles": {"cursor": 1, "vm": 1}, "shapes": {"set": 1}, "residue": []},
    ]
    roled, shapes, byshape = role_census._merge(rows)
    assert dict(roled) == {"cursor": 3, "vm": 1} and dict(shapes) == {"set": 3}
    assert byshape == {"s": [{"shape": "s", "tune": "a"}]}
