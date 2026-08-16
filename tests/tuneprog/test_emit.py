"""S7 code generation: the emitted Python must agree with the reference interpreter."""

import json

import pytest

from deity_informant.tuneprog import emit
from deity_informant.tuneprog.ir import (
    Assert,
    Block,
    Const,
    Goto,
    If,
    Let,
    Load,
    Machine,
    Proc,
    Return,
    Store,
    Switch,
    Trap,
    TrapError,
    Tuneprog,
    Var,
)
from deity_informant.tuneprog.verify import Reference, Verifier

from _asm import asm
from _prog import PLAY, counter, tuneprog

SNIPPETS = {
    "straight": ("play: LDA #$07", "STA $D400", "LDX #$0F", "STX $D418"),
    "loop": ("play: LDX #$02", "lp: TXA", "STA $D404", "DEX", "BPL lp"),
    "call": (
        "play: JSR sub",
        "LDA #$01",
        "STA $D401",
        "JMP done",
        "sub: STA $D400",
        "RTS",
        "done: NOP",
    ),
    "indexed": (
        "play: LDY cnt",
        "LDA tab,Y",
        "STA $D400",
        "INC cnt",
        "LDA cnt",
        "AND #$03",
        "STA cnt",
    ),
    "zp_pointer": (
        "play: LDA #<tab",
        "STA $FB",
        "LDA #>tab",
        "STA $FC",
        "LDY #$01",
        "LAX ($FB),Y",
        "SAX $D400",
    ),
    "io_and_schedule": ("play: LDA cnt", "STA $D020", "STA $DC04", "STA $D400", "INC cnt"),
}


def _code(lines):
    return asm(
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


@pytest.mark.parametrize("name", sorted(SNIPPETS))
@pytest.mark.parametrize("s4", [False, True])
def test_generated_python_matches_the_interpreter(name, s4):
    T, prog = tuneprog(_code(SNIPPETS[name]), calls=6, s4=s4)
    ref = Reference(T, 6)
    py, it = Verifier(prog, ref), Verifier(prog, ref, backend="interp")
    py.run(6)
    it.run(6)
    assert py.div is None and it.div is None, (py.div, it.div)
    assert py.call == it.call == 6
    assert bytes(py.M.m) == bytes(it.M.m)
    assert py.M.hash() == it.M.hash()


def test_emitted_module_is_a_plain_python_module():
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=2, s4=True)
    src = emit.emit_python(prog)
    ns = emit.compile_prog(src)
    assert set(ns["PROCS"]) == set(prog.procs)
    assert ns["PARAMS"]["tick"] == prog.procs["tick"].params
    assert ns["RETS"]["tick"] == prog.procs["tick"].rets
    assert "def p_tick(S, m" in src and "while True:" in src


def _run(prog, machine=None, args=()):
    m = machine or Machine(bytes(0x10000))
    return emit.PyProgram(prog, m).run("f", args), m


def test_generated_traps_are_the_interpreters_traps():
    trap = Tuneprog(procs={"f": Proc("f", (), (), {"b": Block("b", [], Trap("unverified"))}, "b")})
    with pytest.raises(TrapError, match="unverified"):
        _run(trap)
    sw = Tuneprog(
        procs={
            "f": Proc(
                "f",
                (),
                (),
                {"b": Block("b", [Let("t", Const(9))], Switch(Var("t"), ((1, "b"),), ""))},
                "b",
            )
        }
    )
    with pytest.raises(TrapError, match="switch"):
        _run(sw)
    env = Tuneprog(
        procs={
            "f": Proc(
                "f",
                (1,),
                (),
                {
                    "b": Block(
                        "b",
                        [Let("t", Load("ram", Var("X"), 1, 0x10, 0x1F, 0))],
                        Return(()),
                    )
                },
                "b",
            )
        }
    )
    with pytest.raises(TrapError, match="envelope"):
        _run(env, args=[0x40])


def test_switch_case_that_falls_through_still_traps_other_values():
    proc = Proc(
        "f",
        (0,),
        (0,),
        {
            "b": Block("b", [], Switch(Var("A"), ((1, "one"), (2, "two")), "")),
            "one": Block("one", [Let("A", Const(11))], Return((Var("A"),))),
            "two": Block("two", [Let("A", Const(22))], Return((Var("A"),))),
        },
        "b",
    )
    prog = Tuneprog(procs={"f": proc})
    assert _run(prog, args=[1])[0] == (11,)
    assert _run(prog, args=[2])[0] == (22,)
    with pytest.raises(TrapError, match="switch"):
        _run(prog, args=[3])


def test_layout_follows_the_hottest_successor():
    blocks = {
        "a": Block("a", [], If(Const(1), "cold", "hot"), 0, 10),
        "cold": Block("cold", [], Goto("end"), 0, 1),
        "hot": Block("hot", [], Goto("end"), 0, 9),
        "end": Block("end", [], Return(()), 0, 10),
    }
    order = emit.layout(Proc("f", (), (), blocks, "a"))
    assert order[:2] == ["a", "hot"]
    assert set(order) == set(blocks)


def test_certificate_document_and_writer(tmp_path):
    _T, prog = tuneprog(counter("LDA #$07", "STA $D400"), calls=2, s4=True)
    doc = emit.certificate(prog, [{"song": 1, "ticks": 2}], {"cpu": 0.1}, stage="S4")
    p = emit.write_certificate(tmp_path / "certificate.json", doc)
    back = json.loads(p.read_text())
    assert back["stage"] == "S4" and back["divergence"] is None
    assert back["compared"][0] == "init writes"
    assert back["oracle"].startswith("deity_informant.PcodeVM@")
    assert back["subtunes"][0]["ticks"] == 2


def test_two_byte_store_and_dynamic_address_round_trip():
    proc = Proc(
        "f",
        (0,),
        (),
        {
            "b": Block(
                "b",
                [
                    Let("t", Const(0x2000, 2)),
                    Store("ram", Var("t", 2), Const(0xBEEF, 2), 2, 0x2000, 0x2001, 0),
                    Let("u", Load("ram", Var("t", 2), 2, 0x2000, 0x2001, 0)),
                    Store("ram", Const(0x2002, 2), Var("u", 2), 2, 0x2002, 0x2003, 0),
                ],
                Return(()),
            )
        },
        "b",
    )
    m = Machine(bytes(0x10000))
    _run(Tuneprog(procs={"f": proc}), m, args=[0])
    assert m.m[0x2000:0x2004] == bytearray([0xEF, 0xBE, 0xEF, 0xBE])
    assert {0x2000, 0x2001, 0x2002, 0x2003} <= m.W


def test_carry_io_and_bank_shapes_reach_the_generated_code():
    lines = (
        "play: LDA cnt",
        "CLC",
        "ADC #$40",  # carry expression
        "STA $D400",
        "LDA $D012",  # an I/O read: a pinned input
        "STA $D401",
        "LDA #$37",
        "STA $01",  # the 6510 port: the bank may change
        "INC cnt",
    )
    T, prog = tuneprog(_code(lines), calls=4, s4=True)
    src = emit.emit_python(prog)
    assert "S.ioload(" in src and "S.setbank()" in src
    assert "1 if" in src  # the carry of the ADC
    v = Verifier(prog, Reference(T, 4))
    v.run(4)
    assert v.div is None


def test_assert_and_plain_condition_and_far_branches_compile():
    proc = Proc(
        "f",
        (0,),
        (0,),
        {
            "b": Block("b", [Assert(Var("A"), "nonzero")], If(Var("A"), "t", "f")),
            "t": Block("t", [Let("A", Const(1))], Return((Var("A"),))),
            "f": Block("f", [Let("A", Const(2))], Return((Var("A"),))),
        },
        "b",
    )
    prog = Tuneprog(procs={"f": proc})
    src = emit.emit_python(prog)
    assert "S.trap('nonzero'" in src
    assert _run(prog, args=[9])[0] == (1,)
    with pytest.raises(TrapError, match="nonzero"):
        _run(prog, args=[0])


def test_a_phi_in_the_emitted_program_is_refused():
    from deity_informant.tuneprog.ir import Phi

    proc = Proc("f", (), (), {"b": Block("b", [Phi("A", {})], Return(()))}, "b")
    with pytest.raises(ValueError, match="phi"):
        emit.emit_python(Tuneprog(procs={"f": proc}))


def test_layout_reaches_blocks_no_chain_leads_to():
    blocks = {
        "a": Block("a", [], Return(()), 0, 5),
        "orphan": Block("orphan", [], Return(()), 0, 1),
    }
    proc = Proc("f", (), (), blocks, "a")
    proc.blocks["a"].term = Switch(Const(0), ((0, "orphan"),), "")
    assert emit.layout(proc) == ["a", "orphan"]
