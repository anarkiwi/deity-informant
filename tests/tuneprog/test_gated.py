"""S6 range-gated rewrites: every rule where the interval proves it and where it does not.

The intervals come from :func:`~.ranges.cell_ranges` over the certified IR, so each
rule is exercised twice -- once under a bound the program proves and once under the
whole byte, which is what an unproved side condition looks like.
"""

import numpy as np

from deity_informant.tuneprog import pipeline, printer
from deity_informant.tuneprog.gated import _rule, diamonds, ranged
from deity_informant.tuneprog.ir import Bin, Block, Const, Goto, If, Let, Load, Proc
from deity_informant.tuneprog.ir import Return, Store
from deity_informant.tuneprog.ir import Tuneprog, Var
from deity_informant.tuneprog.ranges import cell_ranges, expr_range
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


def _fold(e, cells=()):
    mem = _mem(cells)
    return _rule(e, lambda x: expr_range(x, mem, {}, frozenset()))


def _diamond(cond, arm_t, arm_f):
    """``if cond: a = arm_t else: a = arm_f``, the shape the borrow hides in."""
    blocks = {
        "h": Block("h", [], If(cond, "t", "f")),
        "t": Block("t", [Let("a", arm_t)], Goto("j")),
        "f": Block("f", [Let("a", arm_f)], Goto("j")),
        "j": Block("j", [], Return((Var("a"),))),
    }
    return Proc("p", blocks=blocks, entry="h")


# ---- the masks and comparisons the interval decides --------------------------
def test_a_mask_falls_away_only_where_the_interval_proves_it():
    e = Bin("&", _cell(0x20), Const(0x3F), 1)
    assert _fold(e, {0x20: (0, 0x3F)}) == _cell(0x20)
    assert _fold(e) is e


def test_a_mask_clears_only_where_the_interval_proves_it():
    e = Bin("&", _cell(0x20), SIGN, 1)
    assert _fold(e, {0x20: (0, 0x7F)}) == Const(0, 1)
    assert _fold(e) is e


def test_a_comparison_the_interval_decides():
    e = Bin("<", _cell(0x20), Const(0x10), 1)
    assert _fold(e, {0x20: (0, 0x0F)}) == Const(1, 1)
    assert _fold(e, {0x20: (0x10, 0xFF)}) == Const(0, 1)
    assert _fold(e) is e


def test_an_equality_the_interval_decides():
    e = Bin("==", _cell(0x20), Const(0x10), 1)
    assert _fold(e, {0x20: (0x11, 0xFF)}) == Const(0, 1)
    assert _fold(Bin("!=", _cell(0x20), Const(0x10), 1), {0x20: (0, 0x0F)}) == Const(1, 1)
    assert _fold(e) is e


# ---- the branch whose arms differ by one -------------------------------------
def test_a_branch_whose_arms_differ_by_one_is_the_borrow():
    y, bit = _cell(0x20), Bin("==", _cell(0x21), Const(0), 1)
    p = _diamond(bit, y, Bin("-", y, Const(1), 1))
    assert ranged(p, _mem()) == 1
    assert sorted(p.blocks) == ["h", "j"]
    assert p.blocks["h"].stmts[0].e == Bin("-", y, Bin("!=", _cell(0x21), Const(0), 1), 1)


def test_the_arm_order_that_reads_the_test_as_the_borrow_needs_the_interval():
    y, c = _cell(0x20), _cell(0x21)
    assert ranged(_diamond(c, Bin("-", y, Const(1), 1), y), _mem()) == 0
    p = _diamond(c, Bin("-", y, Const(1), 1), y)
    assert ranged(p, _mem({0x21: (0, 1)})) == 1
    assert p.blocks["h"].stmts[0].e == Bin("-", y, c, 1)


def test_arms_that_are_not_one_apart_stay_a_branch():
    y = _cell(0x20)
    p = _diamond(_cell(0x21), y, Bin("-", y, Const(2), 1))
    assert ranged(p, _mem({0x21: (0, 1)})) == 0
    assert sorted(p.blocks) == ["f", "h", "j", "t"]


# ---- what the certified IR proves about a cell -------------------------------
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


# ---- the pass over a real program --------------------------------------------
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
    view, st, names = pipeline.present(prog)
    assert not [h for p in view.procs.values() for h in diamonds(p)]
    assert "sid[0].freq_lo = (b101C - (counter < 0))" in printer.render(view, st, names, pcs=False)
