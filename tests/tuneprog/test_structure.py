"""S5 structuring: loops, if/else, switch, counted loops, the phase, the goto residue."""

import pytest

from deity_informant.tuneprog import structure as S
from deity_informant.tuneprog.interp import Interp, Machine
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Const,
    Goto,
    If,
    Let,
    Load,
    Proc,
    Return,
    Store,
    Switch,
    Trap,
    Tuneprog,
    Var,
)
from deity_informant.tuneprog.verify import Reference, Verifier

from _asm import asm
from _prog import PLAY, counter, tuneprog


def kinds(body):
    return [type(n).__name__ for n in S.walk(body)]


def _fors(body):
    return [n for n in S.walk(body) if type(n) is S.For]


def _proc(blocks, entry="b0"):
    return Proc("f", (), (), {b.label: b for b in blocks}, entry)


def test_if_else_joins_at_the_post_dominator():
    body = S.structure_proc(
        _proc(
            [
                Block("b0", [], If(Var("A"), "t", "e")),
                Block("t", [Let("x", Const(1))], Goto("j")),
                Block("e", [Let("x", Const(2))], Goto("j")),
                Block("j", [], Return()),
            ]
        )
    )
    assert kinds(body) == ["Blk", "Cond", "Blk", "Blk", "Blk", "Exit"]
    cond = body[1]
    assert [n.label for n in cond.then] == ["t"] and [n.label for n in cond.els] == ["e"]


def test_nested_ifs_and_a_switch():
    body = S.structure_proc(
        _proc(
            [
                Block("b0", [], Switch(Var("A"), ((1, "one"), (2, "two")), "")),
                Block("one", [], If(Var("X"), "a", "b")),
                Block("a", [], Goto("j")),
                Block("b", [], Goto("j")),
                Block("two", [], Goto("j")),
                Block("j", [], Return()),
            ]
        )
    )
    case = next(n for n in S.walk(body) if type(n) is S.Case)
    assert [v for v, _b in case.cases] == [1, 2]
    assert any(type(n) is S.Cond for n in S.walk(case.cases[0][1]))


def test_a_loop_the_trace_cannot_count_stays_a_while_with_break_and_continue():
    body = S.structure_proc(
        _proc(
            [
                Block("b0", [], Goto("h")),
                Block("h", [Let("A", Load("chk", Const(0x1000)))], If(Var("A"), "h", "x")),
                Block("x", [], Return()),
            ]
        )
    )
    loop = next(n for n in S.walk(body) if type(n) is S.Loop)
    assert loop.label == "h"
    assert {n.kind for n in S.walk(loop.body) if type(n) is S.Jump} == {"continue", "break"}


def test_goto_is_the_residue_of_a_shared_tail():
    body = S.structure_proc(
        _proc(
            [
                Block("b0", [], If(Var("A"), "t", "e")),
                Block("t", [], Goto("mid")),
                Block("e", [], If(Var("X"), "mid", "other")),
                Block("mid", [], Goto("end")),
                Block("other", [], Goto("end")),
                Block("end", [], Return()),
            ]
        )
    )
    assert any(type(n) is S.Jump and n.kind == "goto" for n in S.walk(body))


def test_the_dex_loop_becomes_a_for_over_its_observed_domain():
    code = counter("LDX #$02", "lp: TXA", "STA $D404", "DEX", "BPL lp")
    _T, prog = tuneprog(code, calls=3, s4=True)
    body = S.structure_proc(S.view(prog).procs["tick"])
    hit = _fors(body)
    assert len(hit) == 1 and hit[0].values == (2, 1, 0) and hit[0].scale == 1


def test_a_strided_loop_reports_the_stride_as_the_for_scale():
    code = counter("LDX #$62", "lp: TXA", "SBX #$31", "STA $D404", "BPL lp")
    _T, prog = tuneprog(code, calls=2, s4=True)
    hit = _fors(S.structure_proc(S.view(prog).procs["tick"]))
    assert len(hit) == 1 and hit[0].values == (0x62, 0x31, 0) and hit[0].scale == 0x31


def test_an_index_carried_through_memory_is_still_an_induction_variable():
    code = counter(
        "LDX #$02",
        "lp: STX save",
        "LDA #$07",
        "LDX #$00",
        "STA $D404",
        "LDX save",
        "DEX",
        "BPL lp",
        "RTS",
        "save: BRK",
    )
    _T, prog = tuneprog(code, calls=2, s4=True)
    hit = _fors(S.structure_proc(S.view(prog).procs["tick"]))
    assert len(hit) == 1 and hit[0].values == (2, 1, 0)


def test_a_loop_whose_count_the_trace_contradicts_is_not_printed_as_a_for():
    proc = _proc(
        [
            Block("b0", [Let("X", Const(2))], Goto("h"), 0, 1),
            Block("h", [], If(Bin("<", Var("X"), Const(9)), "body", "x"), 0, 99),
            Block("body", [Let("X", Bin("+", Var("X"), Const(1)))], Goto("h"), 0, 99),
            Block("x", [], Return(), 0, 1),
        ]
    )
    assert not _fors(S.structure_proc(proc))


def test_phase_is_the_tick_s_first_test_of_one_state_scalar():
    code = counter("LDA cnt", "AND #$07", "BNE skip", "LDA #$0F", "STA $D418", "skip: INC cnt")
    _T, prog = tuneprog(code, calls=9, s4=True)
    view = S.view(prog)
    body = S.structure_proc(view.procs["tick"])
    hit = S.phase(body, view.storage)
    assert hit is not None
    rid, cond, _t, _f = hit
    assert next(r for r in view.storage if r.id == rid).kind == "state"
    assert type(cond) is Bin


def test_phase_declines_when_the_first_test_is_not_on_one_scalar():
    code = counter("LDA $D012", "CMP cnt", "BNE skip", "LDA #$0F", "STA $D418", "skip: INC cnt")
    _T, prog = tuneprog(code, calls=3, s4=True)
    view = S.view(prog)
    assert S.phase(S.structure_proc(view.procs["tick"]), view.storage) is None


SNIPPETS = (
    ("straight", ("play: LDA #$07", "STA $D400", "LDX #$0F", "STX $D418")),
    ("loop", ("play: LDX #$02", "lp: TXA", "STA $D404", "DEX", "BPL lp")),
    (
        "call",
        (
            "play: JSR sub",
            "LDA #$01",
            "STA $D401",
            "JMP done",
            "sub: STA $D400",
            "RTS",
            "done: NOP",
        ),
    ),
    ("indexed", ("play: LDY cnt", "LDA tab,Y", "STA $D400", "INC cnt", "LDA cnt", "AND #$03")),
)


@pytest.mark.parametrize("name,lines", SNIPPETS)
def test_the_reading_view_runs_exactly_like_the_certified_program(name, lines):
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        *lines,
        "RTS",
        "cnt: BRK",
        "tab: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    T, prog = tuneprog(code, calls=6, s4=True)
    ref = Reference(T, 6)
    a, b = Verifier(prog, ref, backend="interp"), Verifier(S.view(prog), ref, backend="interp")
    a.run(6)
    b.run(6)
    assert a.div is None and b.div is None, (name, a.div, b.div)
    assert bytes(a.M.m) == bytes(b.M.m) and a.M.hash() == b.M.hash()


def test_the_view_of_a_trap_only_procedure_has_no_post_dominator():
    prog = Tuneprog(procs={"f": Proc("f", (), (), {"b": Block("b", [], Trap("x"))}, "b")})
    body = S.structure_proc(S.view(prog).procs["f"])
    assert [type(n).__name__ for n in body] == ["Blk", "Exit"]


def test_structure_covers_every_procedure_of_a_program():
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=2, s4=True)
    view = S.view(prog)
    assert set(S.structure(view)) == set(view.procs)


def test_a_store_ends_a_load_s_life_so_inlining_never_crosses_it():
    proc = Proc(
        "f",
        (),
        (),
        {
            "b": Block(
                "b",
                [
                    Let("t", Load("chk", Const(0x2000))),
                    Store("chk", Const(0x2000), Const(9), 1, 0x2000, 0x2000, 0),
                    Store("chk", Const(0x2001), Var("t"), 1, 0x2001, 0x2001, 1),
                ],
                Return(),
            )
        },
        "b",
    )
    view = S.view(Tuneprog(procs={"f": proc}))
    m = Machine(bytes(0x10000))
    m.m[0x2000] = 5
    m.k[0x2000] = m.k[0x2001] = 1
    Interp(view, m).run("f")
    assert m.m[0x2001] == 5
