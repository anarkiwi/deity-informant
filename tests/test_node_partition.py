"""The (declared region, cursor) correspondence measurement, on hand-built input.

Covers docs/node-partition.md: the cursor is read off the program text and the
editor's object is matched by address containment, so both rules are checked here
without HVSC — a silently loosened match would otherwise pass unnoticed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import node_partition as NP  # pylint: disable=wrong-import-position

Obj = NP.Obj


def _decl(base, size, mut=(), kind="table"):
    return {"kind": kind, "base": base, "size": size, "stride": 1, "mut": list(mut)}


def _const(v, sz=2):
    return ("const", v, sz)


def _mem(addr, sz=1):
    return ("mem", addr, sz)


def _idx(base, index):
    return ("op", "INT_ADD", (_const(base), index), 2)


DECLS = [_decl(0x1000, 0x40), _decl(0x1100, 8, mut=(0, 1, 2))]


def test_kind_is_read_off_the_declarations():
    assert NP._kind(DECLS, 0x1010) == "row"
    assert NP._kind(DECLS, 0x1102) == "state"
    assert NP._kind(DECLS, 0x0040) == "cell"


def test_cursor_follows_a_local_and_a_table_read():
    env = {"x": _mem(_const(0x0300, 2))}
    assert NP._cursors(("loc", "x"), env) == {0x0300}
    assert NP._cursors(_mem(_idx(0x1000, ("loc", "x"))), env) == {0x1000, 0x0300}
    assert NP._cursors(_const(4), env) == set()


def test_cursor_stops_where_the_local_is_not_in_scope():
    assert NP._cursors(("loc", "x"), {}) == set()


def test_walk_resolves_against_the_locals_defined_above_the_load():
    stmts = [
        ("st", _const(0xD400), _mem(_idx(0x1000, ("loc", "y")))),
        ("asg", "y", _mem(_const(0x0300, 2))),
        ("st", _const(0xD401), _mem(_idx(0x1000, ("loc", "y")))),
    ]
    out = {}
    NP._walk(stmts, {}, out)
    assert out == {0x1000: {0x0300}}


def test_walk_descends_into_a_nested_body_without_leaking_its_locals():
    inner = [
        ("asg", "z", _mem(_const(0x0301, 2))),
        ("st", _const(0xD400), _mem(_idx(0x1000, ("loc", "z")))),
    ]
    stmts = [("loop", inner), ("st", _const(0xD401), _mem(_idx(0x1040, ("loc", "z"))))]
    out = {}
    NP._walk(stmts, {}, out)
    assert out == {0x1000: {0x0301}, 0x1040: set()}


OBJS = [Obj("tbl", 0x1010, 0x10), Obj("far", 0x2000, 0x10)]


def _per(objs, pairs):
    return NP._pairs_on(objs, pairs, DECLS, *NP._on(objs, pairs, DECLS))


def _unmatched(objs, pairs):
    return NP._unmatched(pairs, NP._on(objs, pairs, DECLS)[1])


def test_an_object_is_paired_through_the_declaration_that_covers_it():
    got = _per(OBJS, {0x1000: {0x1102}})
    assert got["tbl"] == (True, True, False, ["state"])  # the load is below the object's own base
    assert got["far"] == (False, False, False, [])


def test_an_undeclared_object_is_paired_only_by_a_load_inside_its_own_span():
    assert _per(OBJS, {0x2004: {0x40}})["far"] == (False, True, True, ["cell"])


def test_a_pair_on_no_object_is_counted_unmatched():
    assert _unmatched(OBJS, {0x1000: {0x40}, 0x1100: {0x41, 0x42}}) == (2, 1)


def test_a_base_with_no_cursor_is_not_a_pair():
    got = _per(OBJS, {0x1000: set()})
    assert got["tbl"] == (True, True, False, [])
    assert _unmatched(OBJS, {0x1100: set()}) == (0, 1)


@pytest.mark.parametrize(
    "key,lanes,want",
    [
        ((0, "select", ("pitch", "lo"), 0, 0xFF, None), {}, ("pitch.lo", None)),
        ((0, "imm", 0x80, 0, 0xFF, None), {}, ("player-imm", None)),
        ((0, "select", ("hr", "ad"), 0, 0xFF, None), {}, ("player-const", None)),
        (
            (2, "ramp", 0, 1, 0xFF, None),
            {(2, "ramp", 0, 1, 0xFF, None): ("ptbl", "right")},
            ("ptbl.right", None),
        ),
        ((0, "rel", (("stbl", "right"), None), 1, 0xFF, None), {}, ("stbl.right", None)),
    ],
)
def test_a_stream_key_names_the_object_it_reads(key, lanes, want):
    assert NP._key_object(key, lanes) == want


def test_an_arranged_key_reports_its_cursor_lane():
    key = (0, "select", ("pitch", "lo"), 0, 0xFF, (("patt", "note"), (0, 1)))
    assert NP._key_object(key, {}) == ("pitch.lo", ("patt", "note"))


def _sol(**kw):
    base = {
        "arrays": {"ad": [0] * 4, "sr": [0] * 4, "waveptr": [0] * 4},
        "tstart": 0x2000,
        "wlen": 8,
        "plen": 4,
        "flen": 0,
        "slen": 2,
        "zeros": 0,
    }
    base.update(kw)
    return base


def test_the_object_map_tiles_the_region_the_layout_solved():
    objs = NP._gt_objects(
        (0x1000, 0, 16), (0x1100, 1, 0x1120), 4, [0x1200] * 3, 0x1300, 0x1380, 0x1F00, _sol(), 0
    )
    at = {o.name: (o.base, o.size) for o in objs}
    assert at["pitch.lo"] == (0x1000, 16) and at["pitch.hi"] == (0x1010, 16)
    assert at["ins.ad"] == (0x1F00, 4) and at["ins.waveptr"] == (0x1F08, 4)
    assert at["wtbl.left"] == (0x2000, 8) and at["wtbl.right"] == (0x2008, 8)
    assert at["ptbl.left"] == (0x2010, 4) and at["ptbl.right"] == (0x2014, 4)
    assert at["stbl.left"] == (0x2018, 2) and at["stbl.right"] == (0x201A, 2)
    assert "ftbl.left" not in at  # a table the layout sized at zero is not an object
    assert at["patterns"] == (0x1300, 0x80)


def test_only_the_played_subtune_s_orderlists_are_objects():
    order = [0x1200, 0x1210, 0x1220, 0x1230, 0x1240, 0x1250]
    objs = NP._gt_objects(
        (0x1000, 0, 16), (0x1100, 2, 0x1120), 4, order, 0x1300, 0x1380, 0x1F00, _sol(), 1
    )
    assert [o.base for o in objs if o.name.startswith("orderlist")] == [0x1230, 0x1240, 0x1250]
