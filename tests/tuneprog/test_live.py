"""S6 dead values: what nothing reads, and the cell a read-modify-write leaves behind."""

import re

from deity_informant.tuneprog.ir import Bin, Block, Const, Goto, If, Let, Load, Proc
from deity_informant.tuneprog.ir import Return, Store, Tuneprog, Var
from deity_informant.tuneprog.live import coalesce, dead
from deity_informant.tuneprog.cells import forward

from _asm import asm
from _prog import PLAY, printed

CELL = 0x20


def _load(cls="ram"):
    return Load(cls, Const(CELL, 2), 1, CELL, CELL, 0)


def _one(stmts, term=None):
    """A one-block program, so :func:`dead` and :func:`forward` see just these."""
    blocks = {"b": Block("b", list(stmts), term or Return(()))}
    return Tuneprog(procs={"p": Proc("p", blocks=blocks, entry="b")})


# ---- what nothing reads ------------------------------------------------------
def test_a_let_no_statement_reads_goes():
    prog = _one([Let("t", _load())])
    assert dead(prog) == 1
    assert not prog.procs["p"].blocks["b"].stmts


def test_a_let_a_terminator_reads_stays():
    prog = _one([Let("t", _load())], If(Var("t"), "b", "b"))
    assert dead(prog) == 0


def test_a_let_a_store_reads_stays():
    prog = _one([Let("t", _load()), Store("ram", Const(9, 2), Var("t"), 1, 9, 9, 1)])
    assert dead(prog) == 0


def test_a_let_reading_a_pinned_input_stays():
    prog = _one([Let("t", _load("io"))])
    assert dead(prog) == 0


def test_a_chain_of_dead_lets_goes_in_one_call():
    prog = _one([Let("t", _load()), Let("u", Bin("+", Var("t"), Const(1), 1))])
    assert dead(prog) == 2


# ---- the cell a read-modify-write leaves -------------------------------------
def _rmw(value, addr=CELL, rid=0):
    """``t = mem[CELL]``, a store of ``value`` to ``addr``, then a branch on ``value``."""
    store = Store("ram", Const(addr, 2), value, 1, addr, addr, rid)
    return _one([Let("t", _load()), store], If(value, "b", "b"))


def test_the_value_a_cell_update_stores_is_that_cell():
    v = Bin("-", Var("t"), Const(1), 1)
    prog = _rmw(v)
    assert forward(prog) == 1
    assert prog.procs["p"].blocks["b"].term.c == _load()


def test_a_store_to_another_cell_holds_nothing():
    v = Bin("-", Var("t"), Const(1), 1)
    prog = _rmw(v, addr=0x30, rid=1)
    assert forward(prog) == 0


def test_a_store_of_a_value_that_does_not_read_its_own_cell_holds_nothing():
    prog = _rmw(Bin("-", Load("ram", Const(0x30, 2), 1, 0x30, 0x30, 1), Const(1), 1))
    assert forward(prog) == 0


def test_a_call_between_the_store_and_the_read_ends_the_cell():
    v = Bin("-", Var("t"), Const(1), 1)
    prog = _rmw(v)
    p = prog.procs["p"]
    p.blocks["b"].term = Goto("c")
    p.blocks["c"] = Block("c", [], If(v, "c", "c"))
    assert forward(prog) == 0  # a block boundary is a barrier, as it is for the printer


# ---- the printed shape -------------------------------------------------------
def _dec(*after):
    """A play routine that decrements a cell through the accumulator, then branches."""
    return asm(
        PLAY,
        "init: LDA #$08",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "SEC",
        "SBC #$01",
        "STA cnt",
        "BPL over",
        *after,
        "over: RTS",
        "cnt: BRK",
    )


def test_a_decrements_pre_value_is_not_printed():
    doc = printed(_dec("LDA #$08", "STA cnt"))
    assert re.search(r"^\s+\w+ -= 1$", doc, re.M), doc
    assert not re.search(r"^\s+t\d+ = \w+$", doc, re.M), doc


def test_a_pre_value_a_later_branch_reads_survives():
    code = asm(
        PLAY,
        "init: LDA #$08",
        "STA cnt",
        "STA other",
        "RTS",
        "play: LDA cnt",
        "TAX",
        "SEC",
        "SBC #$01",
        "STA cnt",
        "CPX #$04",
        "BCC over",
        "LDA #$08",
        "STA other",
        "over: RTS",
        "cnt: BRK",
        "other: BRK",
    )
    doc = printed(code)
    assert re.search(r"^\s+\w+ -= 1$", doc, re.M), doc
    assert re.search(r"^\s+t\d+ = \w+$", doc, re.M), doc


# ---- the copies a join leaves ------------------------------------------------
def _join(tail):
    """``m = mem[CELL]``; two arms assign ``n``; the join runs ``tail(n, m)``."""
    blocks = {
        "h": Block("h", [Let("m", _load())], If(Var("m"), "t", "f")),
        "t": Block("t", [Let("n", Var("m"))], Goto("j")),
        "f": Block("f", [Let("n", Bin("+", Var("m"), Const(1), 1))], Goto("j")),
        "j": Block("j", [], Return(tail)),
    }
    return Tuneprog(procs={"p": Proc("p", blocks=blocks, entry="h", rets=[0])})


def test_a_join_copy_goes_when_the_two_ranges_do_not_meet():
    prog = _join((Var("n"),))
    assert coalesce(prog) == 1
    p = prog.procs["p"]
    assert not p.blocks["t"].stmts
    assert p.blocks["f"].stmts[0].n == "m"
    assert p.blocks["j"].term.vals == (Var("m"),)


def test_a_join_copy_stays_when_the_source_outlives_it():
    prog = _join((Var("n"), Var("m")))
    assert coalesce(prog) == 0
    assert prog.procs["p"].blocks["t"].stmts[0].n == "n"


def test_a_latch_copy_stays():
    blocks = {
        "h": Block("h", [Let("m", _load())], Goto("b")),
        "b": Block("b", [Let("k", Bin("-", Var("m"), Const(1), 1))], If(Var("k"), "l", "x")),
        "l": Block("l", [Let("m", Var("k"))], Goto("b")),
        "x": Block("x", [], Return(())),
    }
    prog = Tuneprog(procs={"p": Proc("p", blocks=blocks, entry="h")})
    assert coalesce(prog) == 0
