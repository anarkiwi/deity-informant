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
    """``(resolved, rung-(f) proofs, pinned addresses, provenance proofs)``."""
    procs = [(0x1000, [], [], list(stmts))]
    resolved, _blocked, pinned, proofs = frameptr.apply_rung(_mem0(cells or {}), list(decls), procs)
    kinds = {k: [p for p in proofs if p.kind == k] for k in ("deref", "deref-src")}
    return resolved, kinds["deref"], pinned, kinds["deref-src"]


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
    resolved, proofs = _sequencer()[:2]
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
    _res, proofs = _sequencer(extra=[read])[:2]
    assert "at 2 site(s)" in _only(proofs).lemma


def test_a_deref_with_no_index_resolves_at_row_zero():
    stmts = [_st(PTR, _load(0x1500, 0x1502, ("const", 0, 1))), ("st", ("const", 0xD400, 2), 1)]
    stmts[1] = ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1))
    resolved, proofs = _run(stmts, _pair(0x1500, 0x1502, 2), _TAB)[:2]
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
            [_st(PTR, ("mem", ("op", "INT_ADD", (_zext2(_y()), ("const", 0x1700, 2)), 2), 2))],
            "not a lo/hi partner-table entry read",
        ),
        (
            [("st", ("op", "INT_SUB", (_zext2(_y()), ("const", 1, 2)), 2), ("const", 0, 1))],
            "a store at an unproven address may write the pointer",
        ),
    ],
    ids=["foreign-row", "wild-store"],
)
def test_an_unproven_writer_refuses_the_site(extra, why):
    """A row from outside the web and an unplaceable store both refuse the pointer."""
    _res, proofs = _sequencer(extra=extra)[:2]
    pr = _only(proofs)
    assert pr.status == "refused" and pr.targets == () and why in pr.lemma


def test_an_advance_is_the_webs_own_maintenance_and_opens_the_target_set():
    """The rung takes an advance and stops claiming a block set for it.

    Every read of ``P = P + 8`` is the pair's own word, so the store is no third
    writer; the price is the target set, which opens and supplies no address."""
    advance = [_st(PTR, ("op", "INT_ADD", (_word(PTR), ("const", 8, 2)), 2))]
    resolved, proofs, pinned, src = _sequencer(extra=advance)
    pr = _only(proofs)
    assert pr.status == "resolved" and pr.targets == ()
    assert "1 maintenance definition(s), target set open" in pr.lemma
    assert set(resolved) == {_deref(PTR, _y())} and pinned == {}
    assert _only(src).status == "refused" and "target set open" in _only(src).lemma


def test_a_lane_reload_is_the_webs_own_row_and_opens_the_target_set():
    """Rung (d) spells a lone half store as a lane update, so the pair never packs.

    The surviving lane is the web's and the replacement is a declared const row, so
    premise 1 takes it: the site names and the target set opens with it."""
    lane = ("op", "INT_AND", (_word(PTR), ("const", 0xFF00, 2)), 2)
    half = ("op", "INT_ZEXT", (_at(0x1500, _y()),), 2)
    reload_ = [_st(PTR, ("op", "INT_OR", (lane, half), 2))]
    resolved, proofs, pinned, _src = _sequencer(extra=reload_)
    pr = _only(proofs)
    assert pr.status == "resolved" and pr.targets == ()
    assert "1 maintenance definition(s), target set open" in pr.lemma
    assert set(resolved) == {_deref(PTR, _y())} and pinned == {}


def test_a_lane_reload_from_a_play_written_table_refuses():
    """``mut`` is the play-written lane the const claim excludes, lane or pair."""
    lane = ("op", "INT_AND", (_word(PTR), ("const", 0xFF00, 2)), 2)
    half = ("op", "INT_ZEXT", (_at(0x1500, _y()),), 2)
    reload_ = [_st(PTR, ("op", "INT_OR", (lane, half), 2))]
    pr = _only(_sequencer(extra=reload_, mut=(0,))[1])
    assert pr.status == "refused" and "play-written offsets" in pr.lemma


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
    assert frameptr._span(addr, set(), vals, {}) == want


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
    resolved, proofs = _run(stmts, _pair(0x1500, 0x1502, 2), _TAB)[:2]
    assert resolved == {}
    assert "row index bound $FFFF exceeds one row" in _only(proofs).lemma


def test_a_lane_advanced_pair_lifts_on_its_own_maintenance():
    """However the word folds spell the deref, a lane-advanced pair is still the web's.

    Rung (d) makes the ``INC`` a lane update of the fused word, so the definition
    reads the pair's own cells: the site names, and the target set opens with it."""
    model = _fuzz_model(FG.t_lone_half(np.random.default_rng(7)))
    prog = frameprog.program(model)
    pr = next(p for p in prog.proofs if p.kind == "deref")
    assert pr.status == "resolved"
    assert "1 maintenance definition(s), target set open" in pr.lemma
    assert "mem[" not in frameprog.dumps(prog)


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


# ---- the emitted form and its fixpoint ----------------------------------------------
_DEREF_DOC = (
    "frameprog 1\n"
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


# ---- the provenance rule: the address the proof supplies (spec 4.6) -------------------
_PIN_TAB = {**_TAB, 0x1500: 0x00, 0x1501: 0x00, PTR: 0x00, PTR + 1: 0x14}  # one block


def _pinned(extra=(), idx=None, tab=None):
    """The sequencer whose every reload entry, and whose image word, is one block."""
    stmts = [
        ("asg", "y", ("mem", ("const", 0x1600, 2), 1)),
        _st(PTR, _load(0x1500, 0x1502, _y())),
        *extra,
        ("st", ("const", 0xD400, 2), ("mem", _deref(PTR, idx or _y()), 1)),
    ]
    return _run(stmts, _pair(0x1500, 0x1502, 2), tab or _PIN_TAB)


def test_one_target_block_is_one_address_the_proof_supplies():
    """The base is the proof's constant and the row is pure, so nothing re-evaluates."""
    _res, _pr, pinned, src = _pinned()
    assert (
        _only(src).status == "resolved" and "block $1400, address $1400..$14FF" in _only(src).lemma
    )
    assert pinned == {_deref(PTR, _y()): ("op", "INT_ADD", (("const", 0x1400, 2), _zext2(_y())), 2)}
    assert frameproc.pure(pinned[_deref(PTR, _y())])


def test_a_bare_deref_pins_to_the_block_itself():
    stmts = [
        _st(PTR, _load(0x1500, 0x1502, _y())),
        ("st", ("const", 0xD400, 2), ("mem", _word(PTR), 1)),
    ]
    _res, _pr, pinned, src = _run(
        [("asg", "y", ("const", 0, 1))] + stmts, _pair(0x1500, 0x1502, 2), _PIN_TAB
    )
    assert pinned == {_word(PTR): ("const", 0x1400, 2)}
    assert "address $1400..$1400" in _only(src).lemma


def test_two_target_blocks_are_an_address_space_and_supply_no_address():
    """``k`` is live state: the proof names where the address may be, not where it is."""
    _res, pr, pinned, src = _sequencer()
    assert pr[0].status == "resolved" and pinned == {}
    assert "the proof names 3 target blocks, not one address" in _only(src).lemma


def test_the_pointers_own_image_word_is_part_of_the_target_set():
    """A single declared block still refuses where the image word is another block."""
    tab = {**_PIN_TAB, PTR: 0x30}  # the pointer starts at $1430, not $1400
    _res, _pr, pinned, src = _pinned(tab=tab)
    assert pinned == {} and "names 2 target blocks" in _only(src).lemma


def test_an_impure_row_index_supplies_no_address():
    """Substituting the base does not make a memory-reading row pure."""
    idx = ("mem", ("op", "INT_ADD", (_zext2(_y()), ("const", 0x1600, 2)), 2), 1)
    _res, pr, pinned, src = _pinned(idx=idx)
    assert pr[0].status == "resolved" and pinned == {}
    assert "the row index reads memory" in _only(src).lemma


def test_a_site_the_rung_refuses_keeps_the_rungs_own_diagnostic():
    foreign = [_st(PTR, ("mem", ("op", "INT_ADD", (_zext2(_y()), ("const", 0x1700, 2)), 2), 2))]
    _res, pr, pinned, src = _pinned(extra=foreign)
    assert pinned == {} and _only(pr).status == "refused"
    assert (
        _only(src).status == "refused"
        and "not a lo/hi partner-table entry read" in _only(src).lemma
    )


def _srcs(model, prog, nframes, pin=None):
    """The per-frame SID source tuples of one run under a given address map."""
    trace, _walker = frameprog.iota(model, nframes)
    return frameval.eval_src(prog, trace, nframes, pin=pin)[1]


def test_a_pinned_deref_reports_the_declared_cell_the_direct_read_reports():
    """The same block, read twice: through the pointer and at a const base."""
    model = _fuzz_model(FG.t_ptr_pin(np.random.default_rng(7)))
    prog = frameprog.program(model)
    assert len(prog.pinned) == 1 and frameval.gate_fp(model, 6, prog) is None
    with_pin = _srcs(model, prog, 6)
    without = _srcs(model, prog, 6, pin={})
    deref, direct = zip(*[(f[0], f[1]) for f in with_pin])
    assert all(d == (c[0],) for d, c in zip(deref, direct))  # the deref names the read cell
    assert all(f[0] == () for f in without)  # and named nothing before


def test_mutation_an_address_from_observation_moves_the_record():
    """Re-evaluating the impure address reports where the run went, not what is proved."""
    model = _fuzz_model(FG.t_ptr_seq(np.random.default_rng(7)))
    prog = frameprog.program(model)
    assert prog.pinned == {} and len(prog.resolved) == 1
    proved = _srcs(model, prog, 6)
    watched = _srcs(model, prog, 6, pin={a: a for a in prog.resolved})
    assert all(f[0] == () for f in proved) and len({f[0] for f in watched}) > 1


def test_mutation_an_unresolved_site_given_an_address_moves_the_record():
    """One block named for a pointer that ranges over four reports the wrong row."""
    model = _fuzz_model(FG.t_ptr_seq(np.random.default_rng(7)))
    prog = frameprog.program(model)
    addr = next(iter(prog.resolved))
    block = min(next(p for p in prog.proofs if p.kind == "deref").targets[1:])
    forced = _srcs(model, prog, 6, pin={addr: frameptr._sub(addr, block)})
    watched = _srcs(model, prog, 6, pin={addr: addr})
    assert forced != watched
    assert all(block <= c <= block + 0xFF for f in forced for w in f for c in w)


def test_mutation_dropping_the_row_bound_claims_the_address_space():
    """A row is one block wide; without the bound the claim is every address."""
    wide = ("asg", "t0", ("mem", ("const", 0x1600, 2), 2))
    _res, _pr, pinned, src = _pinned(extra=[wide], idx=("loc", "t0"))
    assert pinned == {} and "row index bound $FFFF exceeds one row" in _only(src).lemma
    saved = frameptr._ROW
    try:
        frameptr._ROW = 0xFFFF
        _res, _pr, pinned, src = _pinned(extra=[wide], idx=("loc", "t0"))
    finally:
        frameptr._ROW = saved
    assert len(pinned) == 1 and "address $1400..$13FF" in _only(src).lemma
