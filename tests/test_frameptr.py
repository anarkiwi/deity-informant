"""Rung (f), pointer resolution: the premise, the per-site refusal, the naming.

Covers docs/frameprog.md 4.4 — a deref whose every definition loads a declared
lo/hi partner table is row ``i`` of one of that table's blocks, the target set
comes from the declared extent, and each weakening of the premise flips a record.
"""

import numpy as np
import pytest

from deity_informant import framefuse
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameptr
from deity_informant import frameval
from deity_informant import grammar as G
import _fuzzgen as FG

from test_frameprog import _fuzz_model

PTR = FG.PTR


# ---- hand-built frame programs ---------------------------------------------------
def _decl(base, size, role=None, mut=(), kind="table"):
    return {
        "kind": kind,
        "base": base,
        "size": size,
        "stride": 1,
        "mut": list(mut),
        "cobases": [],
        "role": role,
        "via": None,
        "targets": None,
        "cmp": [],
        "dispatch": [],
        "observed": True,
        "data": b"",
    }


def _pair(lo, hi, size):
    return [_decl(lo, size, ("lo", hi)), _decl(hi, size, ("hi", lo))]


def _word(cell):
    return ("mem", ("const", cell, 2), 2)


def _zext2(n):
    return ("op", "INT_ZEXT", (n,), 2)


def _at(base, idx):
    return ("mem", ("op", "INT_ADD", (_zext2(idx), ("const", base, 2)), 2), 1)


def _load(lo, hi, idx):
    return framefuse._pack(_at(lo, idx), _at(hi, idx))


def _deref(cell, idx):
    return ("op", "INT_ADD", (_word(cell), _zext2(idx)), 2)


def _st(cell, val):
    return ("st", ("const", cell, 2), val)


def _y():
    return ("loc", "y")


def _mem0(cells):
    m = bytearray(0x10000)
    for a, v in cells.items():
        m[a] = v
    return m


def _run(stmts, decls=(), cells=None):
    """``(resolved addresses, proofs)`` of a one-procedure hand-built program."""
    procs = [(0x1000, [], [], list(stmts))]
    return frameptr.apply_rung(_mem0(cells or {}), list(decls), procs)


_TAB = {0x1500: 0x00, 0x1501: 0x40, 0x1502: 0x14, 0x1503: 0x14}  # blocks $1400, $1440


def _sequencer(extra=(), size=2, mut=()):
    """The orderlist idiom: reload the pair from $1500/$1502, deref at a row."""
    stmts = [
        ("asg", "y", ("mem", ("const", 0x1600, 2), 1)),
        _st(PTR, _load(0x1500, 0x1502, _y())),
        *extra,
        ("st", ("const", 0xD400, 2), ("mem", _deref(PTR, _y()), 1)),
    ]
    decls = [_decl(0x1500, size, ("lo", 0x1502), mut), _decl(0x1502, size, ("hi", 0x1500), mut)]
    return _run(stmts, decls, _TAB)


def _only(proofs):
    assert len(proofs) == 1
    return proofs[0]


# ---- the premise discharged ------------------------------------------------------
def test_a_reload_from_a_declared_partner_table_resolves_the_deref():
    """Both levels are named: which block (a table entry) and which row (the index)."""
    resolved, proofs = _sequencer()
    pr = _only(proofs)
    assert pr.status == "resolved" and pr.kind == "deref" and pr.site == PTR
    assert pr.targets == (0x0000, 0x1400, 0x1440)  # the two entries plus the init word
    assert "m_1500/m_1502[2]@y" in pr.lemma and "row index bound $FF" in pr.lemma
    assert set(resolved) == {_deref(PTR, _y())}


def test_the_target_set_is_the_declared_extent_not_the_image_run():
    """A declaration of one entry claims one block; widening it is the mutation."""
    assert _only(_sequencer(size=1)[1]).targets == (0x0000, 0x1400)
    assert _only(_sequencer(size=2)[1]).targets == (0x0000, 0x1400, 0x1440)


def test_two_sites_on_one_pointer_are_counted_once_per_address():
    """Resolution is per (pointer, index) by value: equal addresses are one record."""
    read = ("st", ("const", 0xD400, 2), ("mem", _deref(PTR, _y()), 1))
    _res, proofs = _sequencer(extra=[read])
    assert "at 2 site(s)" in _only(proofs).lemma


def test_a_deref_with_no_index_resolves_at_row_zero():
    stmts = [_st(PTR, _load(0x1500, 0x1502, ("const", 0, 1))), ("st", ("const", 0xD400, 2), 1)]
    stmts[1] = ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1))
    resolved, proofs = _run(stmts, _pair(0x1500, 0x1502, 2), _TAB)
    assert _only(proofs).status == "resolved" and set(resolved) == {_word(PTR)}


def test_a_pointer_the_play_code_never_writes_is_the_image_word():
    """No definition means a compile-time constant: the init word is the whole set."""
    stmts = [("st", ("const", 0xD400, 2), ("mem", _deref(0x1500, _y()), 1))]
    pr = _only(_run(stmts, [], _TAB)[1])
    assert pr.status == "resolved" and pr.targets == (0x4000,) and "0 definition(s)" in pr.lemma


def test_a_lane_offset_into_a_declared_table_is_the_same_declaration():
    """``T+k`` is an offset in ``T``, not an undeclared base."""
    stmts = [_st(PTR, _load(0x1501, 0x1503, ("const", 0, 1))), ("st", 0, 0)]
    stmts[1] = ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1))
    pr = _only(_run(stmts, _pair(0x1500, 0x1502, 2), _TAB)[1])
    assert pr.status == "resolved" and pr.targets == (0x0000, 0x1440)


# ---- the refusals ------------------------------------------------------------------
@pytest.mark.parametrize(
    "extra,why",
    [
        (
            [_st(PTR, ("op", "INT_ADD", (_word(PTR), ("const", 8, 2)), 2))],
            "not a lo/hi partner-table entry read",
        ),
        (
            [("st", ("op", "INT_SUB", (_zext2(_y()), ("const", 1, 2)), 2), ("const", 0, 1))],
            "a store at an unproven address may write the pointer",
        ),
    ],
    ids=["advance", "wild-store"],
)
def test_an_unproven_writer_refuses_the_site(extra, why):
    """An advance and a store the analysis cannot place both refuse the pointer."""
    _res, proofs = _sequencer(extra=extra)
    pr = _only(proofs)
    assert pr.status == "refused" and pr.targets == () and why in pr.lemma


def test_a_store_whose_span_reaches_the_pointer_refuses():
    """A store's span bounds where it may land; overlap is a definition it cannot prove."""
    cell = 0x1700
    stmts = [
        ("asg", "y", ("mem", ("const", 0x1600, 2), 1)),
        _st(cell, _load(0x1500, 0x1502, _y())),
        ("st", ("op", "INT_ADD", (_zext2(_y()), ("const", 0x16F0, 2)), 2), ("const", 0, 1)),
        ("st", ("const", 0xD400, 2), ("mem", _deref(cell, _y()), 1)),
    ]
    pr = _only(_run(stmts, _pair(0x1500, 0x1502, 2), _TAB)[1])
    assert pr.status == "refused" and "another store may write the pointer" in pr.lemma


_STACK = ("op", "INT_OR", (_zext2(("loc", "sp")), ("const", 0x0100, 2)), 2)


def test_a_stack_push_is_a_proven_span_not_a_wild_store():
    """``sp | $0100`` lies in the stack page, so it is no writer of a zero-page pair."""
    push = ("st", _STACK, ("const", 0, 1))
    assert _only(_sequencer(extra=[push])[1]).status == "resolved"


@pytest.mark.parametrize(
    "addr,want",
    [
        (("const", 0x1500, 2), (0x1500, 0x1500)),
        (("op", "INT_ADD", (_zext2(_y()), ("const", 0x1500, 2)), 2), (0x1500, 0x15FF)),
        (_STACK, (0x0100, 0x01FF)),
        (("op", "INT_OR", (_zext2(_y()), _zext2(_y())), 2), None),
        (("loc", "t0"), (0x1500, 0x15FF)),
        (("loc", "t1"), None),
        (("loc", "t2"), None),
    ],
)
def test_a_store_span_is_proven_or_the_store_is_wild(addr, want):
    """A local store address is the union over its assignments; one hop, no guessing."""
    vals = {
        "t0": [("op", "INT_ADD", (_zext2(_y()), ("const", 0x1500, 2)), 2)],
        "t1": [("loc", "t0")],
    }
    assert frameptr._span(addr, set(), vals) == want


def test_a_play_written_reload_table_refuses():
    """``mut`` excludes the offset from the const claim, so the entry is not evidence."""
    assert "play-written offsets" in _only(_sequencer(mut=(0,))[1]).lemma


def test_an_undeclared_reload_table_refuses():
    assert (
        "is not declared"
        in _only(
            _run(
                [
                    _st(PTR, _load(0x1500, 0x1502, ("const", 0, 1))),
                    ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1)),
                ],
                [],
                _TAB,
            )[1]
        ).lemma
    )


def test_halves_that_are_not_a_declared_partner_pair_refuse():
    decls = [_decl(0x1500, 2, ("lo", 0x1502)), _decl(0x1502, 2, None)]
    assert (
        "partner pair"
        in _only(
            _run(
                [
                    _st(PTR, _load(0x1500, 0x1502, ("const", 0, 1))),
                    ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1)),
                ],
                decls,
                _TAB,
            )[1]
        ).lemma
    )


def test_halves_read_at_different_entries_refuse():
    val = framefuse._pack(_at(0x1500, _y()), _at(0x1502, ("const", 1, 1)))
    assert (
        "not a lo/hi partner-table entry read"
        in _only(
            _run(
                [_st(PTR, val), ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1))],
                _pair(0x1500, 0x1502, 2),
                _TAB,
            )[1]
        ).lemma
    )


def test_an_unbounded_row_index_refuses_the_site():
    """A local some assignment gives a word is not a byte row: the site stays raw."""
    wide = ("asg", "t0", ("mem", ("const", 0x1600, 2), 2))
    stmts = [
        wide,
        _st(PTR, _load(0x1500, 0x1502, ("const", 0, 1))),
        ("st", ("const", 0xD400, 2), ("mem", _deref(PTR, ("loc", "t0")), 1)),
    ]
    resolved, proofs = _run(stmts, _pair(0x1500, 0x1502, 2), _TAB)
    assert resolved == {}
    assert "row index bound $FFFF exceeds one row" in _only(proofs).lemma


def test_an_unfused_pair_refuses_and_names_rung_d():
    model = _fuzz_model(FG.t_lone_half(np.random.default_rng(7)))
    prog = frameprog.program(model)
    pr = next(p for p in prog.proofs if p.kind == "deref")
    assert pr.status == "refused" and "did not fuse (rung d)" in pr.lemma
    assert "mem[" in frameprog.dumps(prog)


# ---- shape recognisers --------------------------------------------------------------
@pytest.mark.parametrize(
    "addr,want",
    [
        (_deref(PTR, _y()), (PTR, _y(), True)),
        (("op", "INT_ADD", (_zext2(_y()), _word(PTR)), 2), (PTR, _y(), True)),
        (_word(PTR), (PTR, None, True)),
        (("op", "INT_ADD", (_zext2(_y()), ("const", 0x1500, 2)), 2), None),
        (("const", 0x1500, 2), None),
        (("mem", ("const", 0x1500, 2), 1), None),
        (("mem", ("op", "INT_ADD", (_zext2(_y()), ("const", 1, 2)), 2), 2), None),
    ],
)
def test_deref_recognises_only_the_base_less_pointer_shapes(addr, want):
    assert frameptr.deref(addr) == want


@pytest.mark.parametrize(
    "node,want",
    [
        (framefuse._pack(("mem", ("const", 0x02, 2), 1), ("mem", ("const", 0x03, 2), 1)), 0x02),
        (framefuse._pack(("mem", ("const", 0x02, 2), 1), ("mem", ("const", 0x04, 2), 1)), None),
        (framefuse._pack(_at(0x1500, _y()), ("mem", ("const", 0x03, 2), 1)), None),
        (("const", 0, 1), None),
    ],
)
def test_the_unfused_word_needs_two_adjacent_plain_halves(node, want):
    assert frameptr._split_cell(node) == want


@pytest.mark.parametrize(
    "val",
    [
        ("const", 0, 1),  # not a word shape at all
        framefuse._pack(("const", 1, 1), ("const", 2, 1)),  # halves are not reads
        framefuse._pack(_word(0x1500), _word(0x1502)),  # halves are word reads
        framefuse._pack(("mem", ("op", "INT_SUB", (_y(), _y()), 2), 1), _at(0x1502, _y())),
    ],
)
def test_a_definition_that_is_not_a_pair_entry_read_has_no_entry(val):
    assert frameptr._entry(val) is None


def test_a_declaration_lookup_is_by_containment():
    tabs = frameptr._Tables([_decl(0x1500, 2), _decl(0x1600, 2)])
    assert tabs.at(0x1400) is None  # below every base
    assert tabs.at(0x1502) is None  # past the region's extent
    assert tabs.at(0x1501)[1] == 1


# ---- the emitted form and its fixpoint ----------------------------------------------
_DEREF_DOC = (
    "frameprog 0\n"
    "play $1000\n"
    "init $0F00\n"
    "data {\n"
    " table m_1500[2] lo m_1502 observed:\n"
    "  0040\n"
    " table m_1502[2] hi m_1500 observed:\n"
    "  1414\n"
    "}\n"
    "sub_1000() {\n"
    "  y = m_1600\n"
    "  zp_02:2 = (zext2(m_1500[y]) | (zext2(m_1502[y]) << $08):2):2\n"
    "  sid.v1.freq_lo = *zp_02[y]\n"
    "  *zp_02[y] = y\n"
    "  ret\n"
    "}\n"
)


def test_the_resolved_deref_round_trips_through_the_grammar():
    """``*ptr[i]`` reads and writes; the canonical fixpoint holds over it."""
    prog = frameprog.loads(_DEREF_DOC)
    text = frameprog.dumps(prog)
    assert "mem[" not in text
    assert "sid.v1.freq_lo = *zp_02[y]" in text and "*zp_02[y] = y" in text
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert set(prog.resolved) == {_deref(0x02, _y())}
    frameprog.lint(text)


def test_a_deref_the_rung_refuses_stays_a_raw_memref():
    """The text distinguishes proven from unproven: no proof, no name."""
    prog = frameprog.loads(_DEREF_DOC)
    prog.resolved = {}
    assert "mem[(zp_02:2 + zext2(y))" in frameprog.dumps(prog)


def test_the_deref_form_is_a_frameprog_form():
    with pytest.raises(ValueError, match="frameprog form"):
        G.parse_expression("*m_1500[X]", "sidprog")
    doc = "sidprog 1\nplay $1000\ninit $0F00\nproc $1000 {\n *m_1500[X] = A\n ret\n}\n"
    with pytest.raises(ValueError, match="frameprog form"):
        G.parse_document(doc, "sidprog")


def test_the_printer_names_a_resolved_address_only():
    addr = _deref(0x02, _y())
    assert frameproc._membody(addr) is None
    assert frameproc._membody(addr, {addr: (0x02, _y())}) == "*zp_02[y]"


_BARE_DOC = _DEREF_DOC.replace("*zp_02[y]", "*zp_02").replace("  *zp_02 = y\n", "")


def test_an_index_less_deref_keeps_its_own_form():
    """``*ptr`` is row 0 written as itself, so the tree survives the round trip."""
    prog = frameprog.loads(_BARE_DOC)
    text = frameprog.dumps(prog)
    assert "sid.v1.freq_lo = *zp_02\n" in text and "mem[" not in text
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert set(prog.resolved) == {("mem", ("const", 0x02, 2), 2)}


# ---- end to end, over the fuzz corpus ------------------------------------------------
def test_the_pointer_sequencer_resolves_and_still_gates():
    """The orderlist/pattern shape: a pointer table walked by a position counter."""
    model = _fuzz_model(FG.t_ptr_seq(np.random.default_rng(7)))
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    pr = next(p for p in prog.proofs if p.kind == "deref")
    assert pr.status == "resolved" and len(pr.targets) == 5  # 4 table blocks plus the init word
    assert "*ptr_0002[pos_1441]" in text and "mem[" not in text
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, 6, prog) is None


@pytest.mark.parametrize("p", FG.players(2), ids=lambda p: "%s-%d" % (p.name, p.seed[1]))
def test_resolution_never_moves_a_canonical_record(p):
    """Rung (f) is naming only: every fuzz player still passes Gate FP and the fixpoint."""
    model = _fuzz_model(p)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, max(p.frames, 8), prog) is None
