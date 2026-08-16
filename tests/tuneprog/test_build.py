"""Front end -> IR: the control shapes (variants, computed targets, frames, traps)."""

import pytest

from deity_informant.tuneprog.ir import Call, Switch, Trap
from deity_informant.tuneprog.verify import Reference, Verifier, verify

from _asm import asm
from _prog import PLAY, front, tuneprog
from deity_informant.tuneprog.build import build_ir


def _terms(prog, kind):
    return [
        (p.name, b.label, b.term)
        for p in prog.procs.values()
        for b in p.blocks.values()
        if type(b.term) is kind
    ]


def _traps(prog):
    return _terms(prog, Trap)


def test_jmp_indirect_becomes_a_switch_that_verifies():
    def source(mask):
        return (
            PLAY,
            "init: RTS",
            "play: LDA vec",
            "EOR #$%02X" % mask,
            "STA vec",
            "ind: JMP (vec)",
            "one: LDA #$01",
            "STA $D400",
            "RTS",
            "two: LDA #$02",
            "STA $D401",
            "RTS",
            "vec: BRK",
            "BRK",
        )

    probe = asm(*source(0))
    code = asm(*source(probe.labels["one"] ^ probe.labels["two"]))
    data = {code.labels["vec"]: code.labels["one"] & 0xFF, code.labels["vec"] + 1: PLAY >> 8}
    T, prog = tuneprog(code, calls=6, data=data, s4=True)
    sw = [t for _p, _l, t in _terms(prog, Switch) if len(t.cases) == 2]
    assert sw, "the indirect jump must switch over both observed targets"
    assert verify(prog, T, calls=6).div is None


def test_patched_jsr_operand_becomes_a_computed_call():
    def source(mask):
        return (
            PLAY,
            "init: RTS",
            "play: LDA jsr1+1",
            "EOR #$%02X" % mask,
            "STA jsr1+1",
            "jsr1: JSR one",
            "RTS",
            "one: LDA #$01",
            "STA $D400",
            "RTS",
            "two: LDA #$02",
            "STA $D401",
            "RTS",
        )

    probe = asm(*source(0))
    code = asm(*source(probe.labels["one"] ^ probe.labels["two"]))
    T, prog = tuneprog(code, calls=6, s4=True)
    calls = {
        s.proc
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Call
    }
    assert len(calls) >= 2 and _terms(prog, Switch)
    assert verify(prog, T, calls=6).div is None


def test_patched_branch_offset_becomes_a_computed_target():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA br+1",
        "STA br+1",  # the offset byte is a play-written cell
        "LDX #$00",
        "br: BEQ hit",
        "LDA #$02",
        "STA $D401",
        "RTS",
        "hit: LDA #$01",
        "STA $D400",
        "RTS",
    )
    T, prog = tuneprog(code, calls=4, s4=True)
    assert _terms(prog, Switch)
    assert verify(prog, T, calls=4).div is None


def test_writer_derived_variant_is_a_trap_arm():
    code = asm(
        PLAY,
        "init: RTS",
        "play: JSR sub",
        "LDA #$60",
        "STA gate",
        "LDA #$A9",
        "STA gate",
        "RTS",
        "sub: gate: LDA #$00",
        "STA $D400",
        "RTS",
    )
    T, prog = tuneprog(code, calls=4, s4=True)
    arms = [t for _p, _l, t in _terms(prog, Switch)]
    assert arms and any(len(t.cases) == 2 for t in arms)
    assert any(t.why == "unverified" for _p, _l, t in _traps(prog))
    assert verify(prog, T, calls=4).div is None


def test_untaken_branch_direction_is_a_trap():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDX #$01",
        "BEQ dead",
        "LDA #$07",
        "STA $D400",
        "RTS",
        "dead: STA $D401",
        "RTS",
    )
    T, prog = tuneprog(code, calls=3)
    assert any(t.why == "untaken" for _p, _l, t in _traps(prog))
    assert verify(prog, T, calls=3).div is None
    # S4 knows the test is constant here, so the arm and its trap fold away
    T, s4 = tuneprog(code, calls=3, s4=True)
    assert not any(t.why == "untaken" for _p, _l, t in _traps(s4))
    assert verify(s4, T, calls=3).div is None


def test_an_irq_entry_pops_its_frame_and_restores_the_flags():
    handler = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: PHA",
        "TXA",
        "PHA",
        "INC cnt",
        "LDA cnt",
        "STA $D400",
        "PLA",
        "TAX",
        "PLA",
        "RTI",
        "cnt: BRK",
    )
    T, prog = tuneprog(handler, calls=5, kind="irq", s4=True)
    v = verify(prog, T, calls=5)
    assert v.div is None and v.call == 5
    assert v.M.regs[3] == 0xFF  # the RTI frame is balanced across every call


def test_brk_is_refused_as_an_unmodelled_frame():
    code = asm(PLAY, "init: RTS", "play: LDA #$07", "STA $D400", "BRK", "RTS")
    T, _tr, L, R, P = front(code, calls=1, data={0xFFFE: 0x00, 0xFFFF: 0x20})
    prog = build_ir(T, L, R, P)
    assert any(t.why == "brk" for _p, _l, t in _traps(prog))
    v = Verifier(prog, Reference(T, 1))
    v.run(1)
    assert v.div["trap"] == "brk"


def test_procedure_parameters_cover_every_register_a_callee_returns():
    code = asm(
        PLAY,
        "init: RTS",
        "play: JSR sub",
        "STA $D400",
        "RTS",
        "sub: LDA #$07",
        "RTS",
    )
    _T, prog = tuneprog(code, calls=2)
    sub = [p for p in prog.procs.values() if p.kind == "sub"][0]
    assert set(sub.rets) <= set(sub.params)
    assert 0 in sub.rets  # A is defined by the callee and returned to the caller


@pytest.mark.parametrize("s4", [False, True])
def test_stack_scratch_through_pha_pla_verifies(s4):
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA #$07",
        "PHA",
        "LDA #$00",
        "PLA",
        "STA $D400",
        "RTS",
    )
    T, prog = tuneprog(code, calls=3, s4=s4)
    assert verify(prog, T, calls=3).div is None
