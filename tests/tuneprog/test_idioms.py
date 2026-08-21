"""S4 peepholes: the 6510's flag algebra folds back into relational and bit tests."""

import pytest

from deity_informant.tuneprog import ssa
from deity_informant.tuneprog.idioms import compound_hints, fold, inline, overflow_of, rewrite
from deity_informant.tuneprog.idioms import sext_of, width
from deity_informant.tuneprog.ir import Bin, Block, Const, If, Let, Load, Proc, Return, Store, Var
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, tuneprog


def _conds(prog, proc="tick"):
    return [b.term.c for b in prog.procs[proc].blocks.values() if type(b.term) is If]


def _play(*lines, calls=6, **kw):
    code = asm(PLAY, "init: LDA #$00", "STA cnt", "RTS", "play:", *lines, "RTS", "cnt: BRK", **kw)
    T, prog = tuneprog(code, calls=calls, s4=True)
    assert verify(prog, T, calls=calls).div is None
    return prog


def test_fold_constants_and_masks():
    assert fold(Bin("+", Const(3), Const(4))) == Const(7, 1)
    assert fold(Bin("&", Bin("&", Var("a"), Const(0x7F)), Const(0x80))) == Bin(
        "&", Var("a"), Const(0, 1), 1
    )
    assert fold(Bin("&", Var("a"), Const(0))) == Const(0, 1)
    assert fold(Bin("|", Var("a"), Const(0))) == Var("a")
    assert fold(Bin("+", Bin("+", Var("a"), Const(3)), Const(255))) == Bin(
        "+", Var("a"), Const(2, 1), 1
    )
    assert fold(Bin("&", Var("a"), Const(0xFF))) == Var("a")
    assert width(Bin("==", Var("a"), Var("b"))) == 1


def test_fold_boolean_and_compare_shapes():
    eq = Bin("==", Var("a"), Var("b"))
    assert fold(Bin("==", eq, Const(1))) == eq
    assert fold(Bin("==", eq, Const(0))) == Bin("!=", Var("a"), Var("b"), 1)
    assert fold(Bin("==", Bin("<", Var("a"), Var("b")), Const(0))) == Bin(
        "<=", Var("b"), Var("a"), 1
    )
    sub = Bin("-", Var("a"), Var("b"), 1)  # the 6510 compare shape
    assert fold(Bin("==", sub, Const(0))) == Bin("==", Var("a"), Var("b"), 1)
    assert fold(Bin("!=", sub, Const(0))) == Bin("!=", Var("a"), Var("b"), 1)


def test_compare_then_branch_becomes_a_relational_test():
    prog = _play("LDA cnt", "CMP #$05", "BEQ hit", "INC cnt", "RTS", "hit: STA $D400")
    c = [
        e
        for e in _conds(prog)
        if type(e) is Bin and e.op in ("==", "!=") and type(e.b) is Const and e.b.v == 5
    ]
    assert c, _conds(prog)


def test_carry_compare_becomes_an_unsigned_relation():
    prog = _play("LDA cnt", "CMP #$03", "BCC low", "LDA #$00", "STA cnt", "low: INC cnt")
    assert any(type(e) is Bin and e.op in ("<", "<=") for e in _conds(prog)), _conds(prog)


def test_dec_then_branch_is_a_sign_bit_test_on_the_decremented_value():
    prog = _play("DEC cnt", "BPL out", "LDA #$04", "STA cnt", "out: LDA cnt", "STA $D400")
    c = [e for e in _conds(prog) if type(e) is Bin and e.op in ("==", "!=")]
    assert any(
        type(e.a) is Bin and e.a.op == "&" and type(e.a.b) is Const and e.a.b.v == 0x80 for e in c
    ), _conds(prog)


def test_asl_bit_test_and_anc_constant_carry():
    # ASL A puts bit 7 in C: the branch tests that bit of the loaded byte.
    prog = _play("LDA cnt", "ASL A", "BCC skip", "STA $D400", "skip: INC cnt")
    assert any(
        type(e) is Bin and type(e.a) is Bin and e.a.op == "&" and e.a.b.v == 0x80
        for e in _conds(prog)
    ), _conds(prog)
    # ANC #$7F clears carry unconditionally: the branch it feeds disappears.
    prog = _play("LDA cnt", "ANC #$7F", "BCS never", "INC cnt", "RTS", "never: STA $D400")
    assert not _conds(prog)
    assert not any(
        type(s) is Let and s.n.split("#")[0] == "C"
        for b in prog.procs["tick"].blocks.values()
        for s in b.stmts
    )


def test_illegal_opcode_shapes_survive_the_passes():
    prog = _play("LAX cnt", "SAX $D400", "SBX #$01", "STX $D401", calls=4)
    st = [
        s
        for b in prog.procs["tick"].blocks.values()
        for s in b.stmts
        if type(s) is Store and s.cls == "io"
    ]
    assert len(st) == 2
    # SAX stores A & X, both of which came from the same LAX load
    v = st[0].v
    assert type(v) is Bin and v.op == "&"


def test_inline_folds_a_single_use_into_its_use():
    blocks = {
        "b": Block(
            "b",
            [Let("t", Bin("+", Var("A"), Const(1))), Let("X", Bin("&", Var("t"), Const(0x0F)))],
            Return((Var("X"),)),
        )
    }
    proc = Proc("f", (0,), (1,), blocks, "b")
    assert inline(proc) == 2  # t into X, then X into the return
    ssa.dce(proc)
    s = proc.blocks["b"].stmts
    assert s == [] and type(proc.blocks["b"].term.vals[0].a) is Bin


def test_inline_leaves_loads_where_they_are():
    blocks = {
        "b": Block(
            "b",
            [Let("t", Load("ram", Const(0x1000, 2), 1)), Let("A", Bin("+", Var("t"), Const(1)))],
            Return((Var("A"),)),
        )
    }
    proc = Proc("f", (), (0,), blocks, "b")
    rewrite(proc)
    ssa.dce(proc)
    kept = proc.blocks["b"].stmts
    assert len(kept) == 1 and type(kept[0].e) is Load  # the load stays where it was
    assert proc.blocks["b"].term.vals[0].a == Var("t")


def test_compound_hints_spot_load_modify_store():
    prog = _play("INC cnt", "LDA cnt", "STA $D400")
    hints = compound_hints(prog.procs["tick"])
    assert hints and all(len(h) == 3 for h in hints)


@pytest.mark.parametrize("prog_src", [("LDA #$07", "STA $D400"), ("LDX cnt", "STX $D404")])
def test_rewrites_keep_the_program_verifiable(prog_src):
    _play(*prog_src)


def _cell(addr):
    return Load("ram", Const(addr, 2), 1)


def test_a_byte_minus_twice_its_sign_bit_is_that_byte_signed():
    """``(A + T) - ((T & $80) << 1)`` is ``A + sext(T)``: an identity over eight bits."""
    t = _cell(0x1934)
    e = Bin("-", Bin("+", Const(0x1953, 2), t, 2), Bin("<<", Bin("&", t, Const(0x80)), Const(1)), 2)
    assert sext_of(e) == (Const(0x1953, 2), t)
    assert sext_of(Bin("-", t, Bin("<<", Bin("&", t, Const(0x80)), Const(1)), 2)) == (None, t)


def test_a_sign_bit_taken_off_some_other_byte_is_not_a_sign_extension():
    t, u = _cell(0x1934), _cell(0x1935)
    e = Bin("-", Bin("+", Const(1, 2), t, 2), Bin("<<", Bin("&", u, Const(0x80)), Const(1)), 2)
    assert sext_of(e) is None
    half = Bin("-", Bin("+", Const(1, 2), t, 2), Bin("&", t, Const(0x80)), 2)
    assert sext_of(half) is None  # $80, not $100: not the sign extension


def test_the_v_flag_of_a_subtract_is_recovered_from_its_three_xors():
    a, b = _cell(0x20), _cell(0x21)
    e = Bin("&", Bin("^", a, b), Bin("^", a, Bin("-", a, b, 1)), 1)
    assert overflow_of(e) == (a, b)
    assert overflow_of(Bin("&", Bin("^", a, b), Bin("^", b, Bin("-", a, b, 1)), 1)) is None
    assert overflow_of(Bin("&", Bin("^", a, b), Bin("^", a, Bin("+", a, b, 1)), 1)) is None
