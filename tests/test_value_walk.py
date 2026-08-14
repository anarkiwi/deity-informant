"""Hermetic tests for the Phase 2.5 instrument (docs/register-model-lift-impl.md).

The domain's rules are checked against the evaluator's own semantics by exhaustion
over small intervals, and the walk against synthesized players -- including the
divergence guard, which is the soundness check that matters."""

import sys
from functools import lru_cache
from itertools import combinations_with_replacement, product
from pathlib import Path

import pytest

import _fuzzgen as G
from test_frameprog import _fuzz_model
from deity_informant import expr as E
from deity_informant import frameprog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import value_walk as W

SID = G.SID
CTR = G.CNT + 0x22  # a RAM counter carried across frames
ZPX = 0x40  # the zero-page block a modular store walks
FRAMES = 8
TMP = G.CNT + 0x20  # per-frame scratch the dispatch handlers read
HND0 = 0x13C0  # dispatch handler stubs: the SMC-operand jmp's observed targets
HND1 = 0x13E0


def _intervals(hi):
    return list(combinations_with_replacement(range(hi + 1), 2))


def _members(v):
    return [x for x in range(v.lo, v.hi + 1) if not (x - v.lo) % v.stride]


BIN = ("INT_ADD", "INT_SUB", "INT_AND", "INT_OR", "INT_XOR")


@pytest.mark.parametrize("mn", BIN)
def test_every_binary_rule_contains_the_evaluators_own_answer(mn):
    """Soundness by exhaustion: the rule's set holds every value the evaluator gives."""
    for (a, b), (c, d) in product(_intervals(7), repeat=2):
        got = W.apply_op(mn, [W.V(a, b, 1, 1), W.V(c, d, 1, 1)], 1)
        for x, y in product(range(a, b + 1), range(c, d + 1)):
            want = E._apply(mn, [x, y], [1, 1], 1)
            assert got.lo <= want <= got.hi, (mn, a, b, c, d, x, y, got)


@pytest.mark.parametrize("mn", ("INT_LEFT", "INT_RIGHT"))
def test_shift_rules_contain_the_evaluators_own_answer(mn):
    for (a, b), k in product(_intervals(15), range(4)):
        got = W.apply_op(mn, [W.V(a, b, 1, 1), W.exact(k, 1)], 1)
        for x in range(a, b + 1):
            want = E._apply(mn, [x, k], [1, 1], 1)
            assert got.lo <= want <= got.hi, (mn, a, b, k, x, got)


def test_a_stride_survives_addition_and_the_congruence_is_kept():
    got = W.apply_op("INT_ADD", [W.V(0, 12, 4, 1), W.exact(1, 1)], 1)
    assert (got.lo, got.hi, got.stride) == (1, 13, 4)
    assert _members(got) == [1, 5, 9, 13]


def test_a_wrap_the_width_cannot_hold_saturates_and_says_so():
    got = W.apply_op("INT_ADD", [W.V(0, 255, 1, 1), W.exact(3, 1)], 1)
    assert (got.lo, got.hi) == (0, 255) and got.top() == "top_width"


def test_a_wrap_wholly_inside_one_period_shifts_exactly():
    got = W.apply_op("INT_ADD", [W.V(252, 255, 1, 1), W.exact(4, 1)], 1)
    assert (got.lo, got.hi) == (0, 3) and got.top() == "top_width"


def test_the_stack_push_shape_floors_at_page_one():
    """``zext2(sp) | $0100``: ``addr_bits`` gives $01FF and ``addr_floor`` $0100; one rule."""
    push = W.apply_op("INT_OR", [W.cast(W.V(0, 255, 1, 1), 2), W.exact(0x0100, 2)], 2)
    assert (push.lo, push.hi) == (0x0100, 0x01FF)
    assert W.verdict(push, 0, W.STACK_TOP) == "bounded"


def test_a_zext_byte_plus_a_small_base_stays_in_the_stack():
    """G2's whole claim, with no fact about the byte at all."""
    got = W.apply_op("INT_ADD", [W.cast(W.full(1, ("memory",)), 2), W.exact(0xA5, 2)], 2)
    assert (got.lo, got.hi) == (0xA5, 0x1A4)
    assert W.verdict(got, 0, W.STACK_TOP) == "bounded"


def test_a_top_names_the_hardest_premise_that_widened_it():
    v = W.full(2, ("memory", "loop", "width"))
    assert v.top() == "top_edge" and v.premises() == "loop|memory|width"
    assert W.full(2, ("memory",)).top() == "top_memory"


def _g2_store():
    """G2 (7.10.3): ``(zext2(y) + $NN)`` stores under $01FF; ``g2_store``'s own shape."""
    a = G.Asm(G.ORG)
    a.i("LDY", "abs", CTR)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x40).i("STA", "absy", 0x00A5)
    a.i("LDA", "zp", 0xA8).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", 0x07).i("STA", "abs", CTR)
    a.i("RTS")
    data = {CTR: 0}
    data.update({0x00A5 + k: 0 for k in range(8)})
    return a, data


def _zpx_masked():
    """A modular ``zp,X`` store whose index is masked: bounded well inside the wrap."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x03).i("TAX")
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("STA", "zpx", ZPX)
    a.i("LDA", "zpx", ZPX).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("RTS")
    data = {CTR: 0}
    data.update({ZPX + k: 0 for k in range(0x10)})
    return a, data


def _zpx_open():
    """The same store with the index read out of RAM: ⊤ under memory, by construction."""
    a = G.Asm(G.ORG)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("STA", "zpx", ZPX)
    a.i("LDA", "zpx", ZPX).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("RTS")
    data = {CTR: 0}
    data.update({ZPX + k: 0 for k in range(0x10)})
    return a, data


def _rts_trick():
    """ASL/04's three per-voice passes: ``JSR`` into a label of the calling list itself.

    The in-edge a map built from ``goto`` alone misses, and the guard's own finding:
    the index at the store is entered from two call sites as well as by fall-through.
    The pass keeps its own call line, which is what makes the edges raw: its body
    carries one, so no site may take the copy the flown-callee removal would place."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0x00)
    a.i("JSR", "abs", ("L", "voice")).i("JSR", "abs", ("L", "step"))
    a.label("step").i("INX")
    a.label("voice")
    a.i("JSR", "abs", ("L", "tick"))
    a.i("STA", "zpx", ZPX)
    a.i("LDA", "zpx", ZPX).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("RTS")
    a.label("tick")
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR).i("RTS")
    data = {CTR: 0}
    data.update({ZPX + k: 0 for k in range(0x10)})
    return a, data


def _dispatch():
    """R8's join: an SMC-operand dispatch emits ``switch goto``, which is no wall."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x11).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x01).i("TAX")
    a.i("LDA", "absx", G.TBL).i("STA", "abs", ("L", "site", 1))
    a.i("LDA", "absx", G.TBL + 2).i("STA", "abs", ("L", "site", 2))
    a.label("site").i("JMP", "abs", 0x0000)
    data = {CTR: 0, TMP: 0}
    data.update({G.TBL: HND0 & 0xFF, G.TBL + 1: HND1 & 0xFF})
    data.update({G.TBL + 2: HND0 >> 8, G.TBL + 3: HND1 >> 8})
    for base, orv in ((HND0, 0x20), (HND1, 0x40)):
        h = G.Asm(base)
        h.i("LDA", "abs", TMP).i("ORA", "imm", orv).i("STA", "abs", SID + 4).i("RTS")
        data.update({base + k: b for k, b in enumerate(h.assemble())})
    return a, data


_PLAYERS = {
    "g2_store": _g2_store,
    "zpx_masked": _zpx_masked,
    "zpx_open": _zpx_open,
    "rts_trick": _rts_trick,
    "dispatch": _dispatch,
}


@lru_cache(maxsize=None)
def _built(name):
    a, data = _PLAYERS[name]()
    player = G.Player(name, G.ORG, a.assemble(), {SID + 4}, {"valuewalk"}, data=data, frames=FRAMES)
    model = _fuzz_model(player)
    return model, frameprog.program(model)


@lru_cache(maxsize=None)
def _row(name):
    model, prog = _built(name)
    return W.row(model, prog, (), FRAMES)


@pytest.mark.parametrize("name", sorted(_PLAYERS))
def test_the_guard_finds_no_value_outside_a_static_bound(name):
    """§3's differential guard: dynamic contradicting static is an analysis bug."""
    got = _row(name)["guard"]
    assert got["frames"] == FRAMES
    assert got["contradictions"] == [], got["contradictions"]


def test_the_g2_store_is_bounded_inside_the_stack():
    assert _row("g2_store")["wide"] == {"g2_boundable": {"bounded": 1}}


def test_a_masked_zero_page_index_bounds_its_modular_store():
    got = _row("zpx_masked")
    assert got["mod_addr"]["store"] == {"bounded": 1}
    assert got["guard"]["checked"] > 0


def test_an_index_read_out_of_ram_is_top_memory_by_construction():
    got = _row("zpx_open")
    assert got["mod_addr"]["store"] == {"top_memory": 1}
    assert set(got["premises"]["mod_addr"]) == {"memory|width"}


def test_a_raw_call_into_a_label_is_an_in_edge_the_map_carries():
    """The ASL/04 shape: without the call edges the walker claims one index of three."""
    model, prog = _built("rts_trick")
    edges = W.InEdges(prog, model)
    assert edges.report()["call_edges"] >= 2
    assert edges.wall is None
    for pc, srcs in edges.srcs.items():
        assert srcs, pc


def test_a_switch_goto_is_a_join_and_closes_the_map():
    got = _row("dispatch")["edges"]
    assert got["arms"] > 0 and got["wall"] is None and got["map_closes"]
    assert got["closed"] == got["labels"]


def test_an_unbuilt_extent_artifact_prices_no_web(tmp_path):
    assert W._extent_rows(tmp_path / "absent.json") == {}
    art = tmp_path / "a.json"
    art.write_text('{"rows": [{"tune": "t", "extents": {"records": [{"root": "$00FA"}]}}]}')
    assert W._extent_rows(art) == {"t": [{"root": "$00FA"}]}


def test_totals_sum_the_rows_and_keep_the_verdict_classes():
    rows = [_row(n) for n in sorted(_PLAYERS)]
    got = W.totals(rows)
    assert got["tunes"] == len(rows)
    assert got["wide_stores"] == {"bounded": 1}
    assert got["guard"]["contradictions"] == 0
    assert got["in_edges"]["labels"] >= got["in_edges"]["closed"] > 0
    assert set(got["mod_addr"]) == {"store", "load"}


def test_the_verdict_order_is_the_class_map_and_nothing_else():
    assert set(W.CLASS) == set(W.ORDER)
    assert set(W.CLASS.values()) | {"bounded"} == set(W.CLASSES)
    assert W._harder("top_dyn", "top_memory") == "top_dyn"
    assert W._harder("bounded", "top_memory") == "top_memory"
