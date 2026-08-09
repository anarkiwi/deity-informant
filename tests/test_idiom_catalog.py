"""Hermetic tests for the stage-1 catalog (docs/register-model-lift-impl.md).

Every row is exercised on the shape it was derived from, ``cover`` accounts a
composed slice to the last node, a shape outside the catalog is reported rather
than absorbed, and ``obligations`` enumerates both slices off a built program."""

import pytest

from deity_informant import frameproc as P
from deity_informant import frameprog
from deity_informant import idioms

ROW = {r.id: r for r in idioms.ROWS}


def op(mn, kids, w=1):
    return ("op", mn, tuple(kids), w)


def c(v, w=1):
    return ("const", v, w)


def m(a, w=1):
    return ("mem", a, w)


def loc(name, w=1):
    return ("loc", name) if w == 1 else ("loc", name, w)


def zext(x):
    return op("INT_ZEXT", (x,), 2)


A, B = loc("a"), m(c(0x2000, 2))
IDX = loc("y")
ROWCELL = m(op("INT_ADD", (c(0x3000, 2), zext(IDX)), 2))
PTRCELL = m(c(0x00FB, 2), 2)
STACK = m(op("INT_OR", (zext(loc("sp")), c(0x100, 2)), 2))


def pack(lo, hi):
    return op("INT_OR", (op("INT_LEFT", (zext(hi), c(8)), 2), zext(lo)), 2)


def carry(x, y):
    return op("INT_OR", (op("INT_CARRY", (x, y)), op("INT_CARRY", (op("INT_ADD", (x, y)), A))))


def adc(x, y):
    return op("INT_ADD", (x, y, carry(x, y)))


def sbc(x, y, borrow):
    return op("INT_SUB", (x, op("INT_ADD", (y, op("INT_SUB", (c(1), borrow))))))


def shifts(mn, v, n):
    for _i in range(n):
        v = op(mn, (v, c(1)))
    return v


def deref(ptr, idx):
    return m(op("INT_ADD", (ptr, zext(idx)), 2))


CASES = [
    ("pair-row", pack(m(c(0x2000, 2)), m(c(0x2100, 2))), ()),
    ("pair-row", pack(ROWCELL, m(op("INT_ADD", (c(0x3100, 2), zext(IDX)), 2))), (IDX,)),
    ("word-pack", pack(A, op("INT_ADD", (A, B))), (A, op("INT_ADD", (A, B)))),
    (
        "lane-insert",
        op("INT_OR", (op("INT_AND", (PTRCELL, c(0xFF00, 2)), 2), zext(A)), 2),
        (PTRCELL, A),
    ),
    ("hi-byte", P.trunc_hi(PTRCELL), (PTRCELL,)),
    ("lo-byte", P.trunc_lo(PTRCELL), (PTRCELL,)),
    (
        "shift-pair",
        op(
            "INT_OR", (op("INT_RIGHT", (A, c(1))), op("INT_LEFT", (op("INT_AND", (B, c(1))), c(7))))
        ),
        (A, B),
    ),
    ("adc-chain", adc(A, B), (A, B)),
    ("adc-chain", op("INT_ADD", (A, B, loc("cflag"))), (A, B, loc("cflag"))),
    ("sbc-chain", sbc(A, B, loc("cflag")), (A, B)),
    ("shift-chain", shifts("INT_LEFT", A, 4), (A,)),
    (
        "flag-bit",
        op("INT_NOTEQUAL", (op("INT_AND", (op("INT_ADD", (A, B)), c(0x40))), c(0))),
        (op("INT_ADD", (A, B)),),
    ),
    ("table-row", ROWCELL, (IDX,)),
    ("cell-read", B, ()),
    ("stack-slot", STACK, (STACK[1],)),
    ("deref-row", deref(PTRCELL, IDX), (PTRCELL, IDX)),
    ("deref-row", m(PTRCELL), (PTRCELL,)),
    ("mask-const", op("INT_AND", (A, c(0x0F))), (A,)),
    ("set-const", op("INT_OR", (A, c(0x40))), (A,)),
    ("alu-op", op("INT_XOR", (A, B)), (A, B)),
    ("widen", zext(A), (A,)),
    ("carry-value", op("INT_CARRY", (A, B)), (A, B)),
    ("compare-value", op("INT_LESSEQUAL", (A, B)), (A, B)),
    ("const-literal", c(3), ()),
    ("local-read", A, ()),
]


@pytest.mark.parametrize("rid,node,want", CASES, ids=[f"{i}-{r[0]}" for i, r in enumerate(CASES)])
def test_each_row_consumes_its_shape(rid, node, want):
    got = ROW[rid].match(node)
    assert got is not None, rid
    if want is not None:
        assert tuple(got) == tuple(want)
    for other in idioms.ROWS:  # the first row that matches is the one that owns it
        if other.match(node) is not None:
            assert other.id == rid
            break


def test_rows_are_uniquely_named_and_carry_a_normal_form():
    ids = [r.id for r in idioms.ROWS]
    assert len(set(ids)) == len(ids)
    assert all(i == i.lower() and " " not in i and "_" not in i for i in ids)
    assert set(idioms.FORMS) == set(ids)


def test_a_lone_shift_is_no_chain_and_a_byte_copy_is_no_word_half():
    assert ROW["shift-chain"].match(op("INT_LEFT", (A, c(1)))) is None
    assert ROW["lo-byte"].match(P.trunc_lo(A)) is None
    assert ROW["hi-byte"].match(P.trunc_lo(PTRCELL)) is None


REFUSALS = [
    ("shift-pair", op("INT_OR", (op("INT_RIGHT", (A, c(1))), op("INT_LEFT", (B, c(7)))))),
    ("shift-pair", op("INT_OR", (A, B))),
    ("shift-pair", op("INT_OR", (op("INT_RIGHT", (A, c(1))), B))),
    ("flag-bit", op("INT_EQUAL", (A, B))),
    ("lane-insert", op("INT_OR", (zext(A), zext(B)), 2)),
    ("sbc-chain", op("INT_SUB", (A, B))),
    ("sbc-chain", op("INT_SUB", (A, op("INT_ADD", (A, B))))),
    ("deref-row", m(op("INT_ADD", (zext(A), zext(IDX)), 2))),
    ("deref-row", m(loc("w0"))),
    ("mask-const", op("INT_AND", (A, B))),
    ("set-const", op("INT_OR", (A, B))),
    ("alu-op", op("INT_SREM", (A, B))),
    ("adc-chain", op("INT_ADD", (A, B))),
    ("table-row", m(loc("w0", 2))),
    ("stack-slot", m(op("INT_ADD", (c(0x2000, 2), zext(IDX)), 2))),
]


@pytest.mark.parametrize("rid,node", REFUSALS, ids=[f"{i}-{r[0]}" for i, r in enumerate(REFUSALS)])
def test_a_row_refuses_what_only_wears_its_shape(rid, node):
    assert ROW[rid].match(node) is None


def test_flag_bit_refuses_a_plain_load_and_a_comparison():
    assert idioms.flag_bit(op("INT_NOTEQUAL", (op("INT_AND", (B, c(0x40))), c(0)))) is None
    cmpd = op("INT_LESS", (A, B))
    assert idioms.flag_bit(op("INT_NOTEQUAL", (op("INT_AND", (cmpd, c(0x40))), c(0)))) is None


def test_cover_accounts_a_composed_slice_to_the_last_node():
    slice_ = pack(op("INT_AND", (ROWCELL, c(0x0F))), adc(deref(PTRCELL, IDX), B))
    counts, gaps = idioms.cover(slice_)
    assert not gaps
    assert counts["word-pack"] == 1
    assert counts["adc-chain"] == 1
    assert counts["mask-const"] == counts["table-row"] == counts["deref-row"] == 1
    assert sum(counts.values()) < idioms.size(slice_)  # the rows absorbed their glue


def test_an_unknown_shape_is_reported_and_does_not_mask_its_children():
    node = op("INT_NEGATE", (op("INT_AND", (A, c(0x0F))),))
    counts, gaps = idioms.cover(node)
    assert [g[1] for g in gaps] == ["INT_NEGATE"]
    assert counts["mask-const"] == 1 and counts["local-read"] == 1


def test_a_machine_leaf_the_catalog_never_names_is_unaccounted():
    counts, gaps = idioms.cover(op("INT_ADD", (("reg", 0), c(1))))
    assert gaps == [("reg", 0)]
    assert counts["alu-op"] == 1


SID, CELL, ARR = 0xD401, 0x2000, 0x3000


def _prog(stmts, state=(("m_2000", 1, False, []),), symbols=None):
    return frameprog.FrameProgram(
        0x1000, 0x0FFD, state=state, symbols=symbols or {}, procs=[(0x1000, [], [], stmts)]
    )


def test_obligations_enumerate_sid_stores_and_cell_updates_only():
    prog = _prog(
        [
            ("label", 0x1010),
            ("asg", "t0", m(c(CELL, 2))),
            ("st", c(SID, 2), loc("t0")),
            ("st", c(CELL, 2), c(5)),
            ("st", c(0x4000, 2), c(7)),
        ]
    )
    sid, upd = idioms.obligations(prog)
    assert [o.base for o in sid] == [SID] and [o.base for o in upd] == [CELL]
    assert sid[0].value == m(c(CELL, 2))  # the slice is the value with its locals resolved
    assert sid[0].site == 0x1010 and sid[0].entry == 0x1000 and sid[0].at == 2
    assert upd[0].field == "m_2000"


def test_a_slice_reads_the_definition_in_force_at_the_store():
    prog = _prog(
        [
            ("asg", "t0", c(1)),
            ("st", c(SID, 2), loc("t0")),
            ("asg", "t0", c(2)),
            ("st", c(SID, 2), loc("t0")),
        ]
    )
    sid, _upd = idioms.obligations(prog)
    assert [o.value for o in sid] == [c(1), c(2)]


def test_a_store_whose_address_does_not_resolve_is_still_enumerated():
    prog = _prog([("st", loc("w0", 2), c(1)), ("st", loc("b0"), c(2))])
    sid, upd = idioms.obligations(prog)
    assert [(o.base, o.field) for o in sid] == [(None, idioms.UNRESOLVED)]
    assert [(o.base, o.field) for o in upd] == []
    prog = _prog([("st", loc("b0"), c(2))], state=(("zp_FB", 1, False, []),))
    _sid, upd = idioms.obligations(prog)
    assert [(o.base, o.field) for o in upd] == [(None, idioms.UNRESOLVED)]


def test_a_store_in_a_nested_body_is_enumerated_at_the_site_enclosing_it():
    inner = [("st", c(SID, 2), c(3))]
    prog = _prog([("label", 0x1020), ("if", "if", c(1), inner, [])])
    sid, _upd = idioms.obligations(prog)
    assert [(o.site, o.value) for o in sid] == [(0x1020, c(3))]


def test_a_state_row_that_names_no_cell_is_no_obligation():
    assert not idioms.state_cells(_prog([], state=(("cflag", 1, False, []),)))


def test_state_cells_name_every_byte_of_a_field_and_a_table_only_by_its_base():
    prog = _prog(
        [], state=(("ptr", 2, False, []), ("m_3000", 1, True, [])), symbols={0x00FB: "ptr"}
    )
    cells = idioms.state_cells(prog)
    assert cells == {0x00FB: "ptr", 0x00FC: "ptr", ARR: "m_3000"}


def test_an_indexed_store_is_an_update_of_the_table_it_names():
    prog = _prog(
        [("st", op("INT_ADD", (c(ARR, 2), zext(IDX)), 2), c(9))],
        state=(("m_3000", 1, True, []),),
    )
    _sid, upd = idioms.obligations(prog)
    assert [(o.base, o.field) for o in upd] == [(ARR, "m_3000")]


def test_every_obligation_of_a_built_program_is_fully_accounted():
    prog = _prog(
        [
            ("asg", "t0", pack(ROWCELL, adc(m(c(CELL, 2)), c(1)))),
            ("st", c(SID, 2), P.trunc_lo(loc("t0", 2))),
            ("st", c(CELL, 2), op("INT_AND", (deref(PTRCELL, IDX), c(0x0F)))),
        ]
    )
    sid, upd = idioms.obligations(prog)
    for ob in sid + upd:
        counts, gaps = idioms.cover(ob.value)
        assert not gaps and counts
