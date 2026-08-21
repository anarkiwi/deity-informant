"""S6 equality saturation: rule soundness, the analysis that gates it, determinism.

Every gated rule is checked twice -- once where the IR proves its side condition
and once where it does not -- and the rewritten view is run against the trace, so
an unsound rule is a divergence and not a printed-text difference.
"""

import numpy as np
import pytest

from deity_informant.tuneprog import pipeline, printer
from deity_informant.tuneprog.eqrules import E, RULES
from deity_informant.tuneprog.eqsat import _diamonds, _Graph, saturate
from deity_informant.tuneprog.idioms import fold
from deity_informant.tuneprog.ir import Bin, Block, Const, Let, Load, Proc, Return, Store
from deity_informant.tuneprog.ir import Tuneprog, Var
from deity_informant.tuneprog.ranges import cell_ranges
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, tuneprog

SIGN = Const(0x80)


def _mem(cells=()):
    """Interval arrays over the image: every cell a whole byte but the ones named."""
    lo, hi = np.zeros(0x10000, np.int32), np.full(0x10000, 0xFF, np.int32)
    for a, (x, y) in dict(cells).items():
        lo[a], hi[a] = x, y
    return lo, hi


def _cell(a):
    return Load("ram", Const(a, 2), 1, a, a, 1)


def _sat(e, mem=None):
    """One expression through the e-graph, extracted back."""
    g = _Graph(set(), mem, {})
    i = g.root(e)
    g.run(RULES)
    return g.out(i)


def _sel(cond, arm_t, arm_f, mem=None):
    """One if-diamond through the e-graph: the converted value, or ``None``."""
    g = _Graph(set(), mem, {})
    i = g.root_of(E.sel(g.lower(cond), g.lower(arm_t), g.lower(arm_f)))
    g.run(RULES)
    return g.out(i)


def _play(*lines, calls=6, **kw):
    code = asm(PLAY, "init: LDA #$00", "STA cnt", "RTS", "play:", *lines, "RTS", "cnt: BRK", **kw)
    T, prog = tuneprog(code, calls=calls, s4=True)
    assert verify(prog, T, calls=calls, prefix=calls).div is None
    return T, prog


def _text(prog, eqsat_on):
    view, st, names = pipeline.present(prog, eqsat=eqsat_on)
    return printer.render(view, st, names, pcs=False)


# ---- the identities the bespoke passes encode --------------------------------
def test_fold_identities_hold_through_the_egraph():
    x = Var("a")
    assert _sat(Bin("+", Const(3), Const(4))) == Const(7, 1)
    assert _sat(Bin("&", Bin("&", x, Const(0x7F)), Const(0x80))) == Const(0, 1)
    assert _sat(Bin("|", x, Const(0))) == x
    assert _sat(Bin("&", x, Const(0xFF))) == x
    assert _sat(Bin("carry", x, Const(0))) == Const(0, 1)
    eq = Bin("==", x, Var("b"))
    assert _sat(Bin("==", eq, Const(0))) == Bin("!=", x, Var("b"), 1)
    assert _sat(Bin("==", Bin("-", x, Var("b"), 1), Const(0))) == Bin("==", x, Var("b"), 1)


def test_constant_comparisons_fold_to_the_right_answer():
    for op, pairs in (
        ("<", ((3, 4, 1), (4, 3, 0), (3, 3, 0))),
        ("<=", ((3, 4, 1), (4, 3, 0), (3, 3, 1))),
        ("==", ((3, 3, 1), (3, 4, 0))),
        ("!=", ((3, 3, 0), (3, 4, 1))),
        ("carry", ((200, 100, 1), (100, 100, 0))),
    ):
        for x, y, want in pairs:
            assert _sat(Bin(op, Const(x), Const(y))) == Const(want, 1), (op, x, y)


def test_if_conversion_reads_the_condition_as_a_borrow_only_when_it_is_a_bit():
    y, one = _cell(0x20), Const(1)
    bit = Bin("==", _cell(0x21), Const(0))
    assert _sel(bit, Bin("-", y, one), y) == Bin("-", y, bit, 1)
    byte = Bin("&", _cell(0x21), SIGN)  # a branch on bit 7 is not a 0-or-1 value
    assert _sel(byte, Bin("-", y, one), y) is None
    assert _sel(byte, y, Bin("-", y, one)) == Bin("-", y, Bin("==", byte, Const(0), 1), 1)


def test_saturation_reaches_what_one_fold_pass_leaves():
    x = Var("a")
    once = fold(Bin("-", Bin("+", x, Const(1)), Const(1)))
    assert once == Bin("+", x, Const(0, 1), 1)  # fold stops one rewrite short
    assert _sat(once) == x
    assert _sat(Bin("-", Bin("+", x, Const(1)), Const(1))) == x


# ---- the analysis-gated rules, proved and unproved ---------------------------
def test_a_mask_falls_away_only_where_the_interval_proves_it():
    e = Bin("&", _cell(0x20), Const(0x3F))
    assert _sat(e, _mem({0x20: (0, 0x3F)})) == _cell(0x20)
    assert _sat(e, _mem()) == e


def test_a_mask_clears_only_where_the_interval_proves_it():
    e = Bin("&", _cell(0x20), SIGN)
    assert _sat(e, _mem({0x20: (0, 0x7F)})) == Const(0, 1)
    assert _sat(e, _mem()) == e


def test_a_comparison_the_interval_decides():
    e = Bin("<", _cell(0x20), Const(0x10))
    assert _sat(e, _mem({0x20: (0, 0x0F)})) == Const(1, 1)
    assert _sat(e, _mem({0x20: (0x10, 0xFF)})) == Const(0, 1)
    assert _sat(e, _mem()) == e


def test_an_equality_the_interval_decides():
    e = Bin("==", _cell(0x20), Const(0x10))
    assert _sat(e, _mem({0x20: (0x11, 0xFF)})) == Const(0, 1)
    assert _sat(e, _mem()) == e


def _ovf(a, b):
    return Bin("&", Bin("^", a, b), Bin("^", a, Bin("-", a, b, 1)), 1)


def test_overflow_reduces_only_when_both_operand_signs_are_known():
    a, b = _cell(0x20), _cell(0x21)
    e = Bin("&", _ovf(a, b), SIGN)
    assert _sat(e, _mem({0x20: (0, 0x7F), 0x21: (0, 0x7F)})) == Const(0, 1)
    assert _sat(e, _mem({0x20: (0x80, 0xFF), 0x21: (0x80, 0xFF)})) == Const(0, 1)
    assert _sat(e, _mem({0x20: (0, 0x7F), 0x21: (0x80, 0xFF)})) == Bin("&", Bin("-", a, b, 1), SIGN)
    assert _sat(e, _mem({0x20: (0, 0x7F)})) == e  # one operand's sign is not enough
    assert _sat(e, _mem()) == e


# ---- what the IR proves about a cell -----------------------------------------
def test_cell_ranges_bound_a_masked_cell():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$0F",
        "STA mask",
        "LDA mask",
        "STA $D400",
        "INC cnt",
        "RTS",
        "cnt: BRK",
        "mask: BRK",
    )
    T, prog = tuneprog(code, calls=6, s4=True)
    assert verify(prog, T, calls=6).div is None
    lo, hi = cell_ranges(prog)
    at = code.labels["mask"]
    assert (int(lo[at]), int(hi[at])) == (0, 0x0F)


def test_cell_ranges_keep_each_procedure_s_own_names():
    procs = {}
    for name, v, at in (("a", 0x0F, 0x20), ("b", 0xF0, 0x21)):
        body = [Let("t", Const(v)), Store("ram", Const(at, 2), Var("t"), 1, at, at, 0)]
        procs[name] = Proc(name, blocks={"e": Block("e", body, Return())}, entry="e")
    lo, hi = cell_ranges(Tuneprog(procs=procs))
    assert (int(hi[0x20]), int(hi[0x21])) == (0x0F, 0xF0)
    assert (int(lo[0x20]), int(lo[0x21])) == (0, 0)


# ---- the pass over real programs ---------------------------------------------
BORROW = (
    "init: LDA #$7E",
    "STA cnt",
    "LDA #$10",
    "STA hi",
    "RTS",
    "play: LDX hi",
    "LDA cnt",
    "BPL pos",
    "DEX",
    "pos: STX $D400",
    "INC cnt",
    "RTS",
    "cnt: BRK",
    "hi: BRK",
)


def test_the_branch_carried_borrow_becomes_one_statement():
    T, prog = tuneprog(asm(PLAY, *BORROW), calls=6, s4=True)
    assert verify(prog, T, calls=6, prefix=6).div is None
    view, _st, _n = pipeline.present(prog, eqsat=True)
    assert not [h for p in view.procs.values() for h in _diamonds(p)]
    plain = _text(prog, False).count("\n")
    assert _text(prog, True).count("\n") < plain


@pytest.mark.parametrize("gated", (True, False))
def test_the_rewritten_program_still_matches_the_trace(gated):
    for lines in (
        ("LDA cnt", "CLC", "ADC #$01", "AND #$7F", "STA $D400"),
        ("LDA cnt", "CMP #$05", "BCC low", "LDA #$00", "low: STA $D404"),
    ):
        T, prog = _play(*lines)
        before = prog.to_json()
        copy = Tuneprog.from_json(before)
        assert saturate(copy, gated=gated) >= 0
        assert prog.to_json() == before  # the certified program is untouched
        assert verify(copy, T, calls=6, prefix=6).div is None


def test_the_flag_off_leaves_the_print_alone_and_on_never_grows_it():
    T, prog = _play("LDA cnt", "CLC", "ADC #$01", "AND #$7F", "STA $D400")
    del T
    off = _text(prog, False)
    assert off == _text(prog, False)
    assert len(_text(prog, True)) <= len(off)


def test_extraction_is_byte_stable():
    _T, prog = _play("LDA cnt", "CLC", "ADC #$01", "AND #$7F", "STA $D400")
    assert _text(prog, True) == _text(prog, True)
    one, two = Tuneprog.from_json(prog.to_json()), Tuneprog.from_json(prog.to_json())
    saturate(one)
    saturate(two)
    assert one.to_json() == two.to_json()


def test_saturation_is_asserted():
    g = _Graph(set())
    g.root(Bin("+", Var("a"), Const(0)))
    g.run(RULES)
    assert g.out(0) == Var("a")
