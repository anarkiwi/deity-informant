"""Rung (d0), destacking: the slot proven a temporary, the refusal and the law.

``PHA``/``PLA`` at a known ``sp`` is a store and a load at a constant stack-page
cell; where a store dominates every read with no control transfer between, the
slot is a synthetic local, and every control use of the stack refuses.
"""

import numpy as np
import pytest

from deity_informant import frameprog
from deity_informant import framestack
from deity_informant import frameval
import _fuzzgen as G

from test_frameprog import _fuzz_model

OUT = 0xD404  # sid.v1.ctrl/attack_decay: observable and not a SID lo/hi pair
LO, HI = G.TBL, G.TBL + 1
SUB = 0x1300
LOSLOT, HISLOT = 0x01FD, 0x01FC  # the two slots a two-byte push at reset ``sp`` takes


def _check(player):
    """``(model, program, text)`` for a player, gate, fixpoint and locals checked."""
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, 8, prog) is None
    assert frameprog.dumps(frameprog.loads(text)) == text
    frameprog.check_locals(prog.procs)
    return model, prog, text


def _build(name, asm, data=(), frames=1):
    return _check(
        G.Player(
            name,
            G.ORG,
            asm.assemble(),
            {OUT, OUT + 1},
            {"indexed"},
            data=dict(data),
            frames=frames,
        )
    )


def _stack(prog, status=None):
    return [p for p in prog.proofs if p.kind == "stack" and status in (None, p.status)]


# ---- the slot the premise proves a temporary --------------------------------------
def test_a_straight_line_spill_becomes_a_local():
    """One ``PHA`` and the ``PLA`` that reads it back: the slot is never memory."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", LO).i("CLC").i("ADC", "imm", 0x10).i("PHA")
    a.i("LDA", "abs", HI).i("STA", "abs", OUT + 1)
    a.i("PLA").i("STA", "abs", OUT).i("RTS")
    _m, prog, text = _build("spill", a, {LO: 0x22, HI: 0x33})
    (pr,) = _stack(prog)
    assert pr.status == "named" and pr.targets == (LOSLOT,)
    assert pr.lemma.endswith("1 store(s), 1 read(s); data temporary, local s0")
    assert "s0 = (m_1400 + $10)" in text and "sid.v1.ctrl = s0" in text
    assert "m_01FD" not in text  # not a state field, not a cell


def _two_arms():
    """Both arms push a lo/hi pair, a shared tail pops it (Commando $5262-$52AD)."""
    a = G.Asm(G.ORG)
    a.i("INC", "abs", G.CNT).i("LDA", "abs", G.CNT).i("AND", "imm", 0x01)
    a.i("BEQ", "rel", ("L", "down"))
    a.i("CLC").i("LDA", "abs", LO).i("ADC", "imm", 0x05).i("PHA")
    a.i("LDA", "abs", HI).i("ADC", "imm", 0x00).i("PHA").i("JMP", "abs", ("L", "tail"))
    a.label("down")
    a.i("SEC").i("LDA", "abs", LO).i("SBC", "imm", 0x05).i("PHA")
    a.i("LDA", "abs", HI).i("SBC", "imm", 0x00).i("PHA")
    a.label("tail")
    a.i("PLA").i("STA", "abs", OUT + 1).i("PLA").i("STA", "abs", OUT).i("RTS")
    return _build("twoarm", a, {LO: 0x40, HI: 0x02, G.CNT: 0}, frames=4)


def test_a_slot_written_in_both_arms_and_read_in_the_tail_is_one_local():
    """Two definitions and one use is an ordinary local, not two stack cells."""
    _m, prog, text = _two_arms()
    assert sorted(p.targets[0] for p in _stack(prog, "named")) == [HISLOT, LOSLOT]
    assert all(
        p.lemma.endswith("2 store(s), 1 read(s); data temporary, local s%d" % k)
        for k, p in enumerate(_stack(prog))
    )
    defs = [l.strip().split(" = ")[0] for l in text.splitlines() if " = " in l]
    assert defs.count("d0:2") == 2  # the arms keep only the +/- choice (repolish factoring)
    assert "sid.v1.ctrl = trunc1(q0:2)" in text  # the slot pair is one word local
    assert "sid.v1.attack_decay = trunc1((q0:2 >> $08):2)" in text
    assert "s0" not in text and "s1" not in text  # the slot locals inline clean away
    assert "m_01F" not in text


# ---- the control uses of the stack, refused --------------------------------------
def test_a_call_between_the_push_and_the_pull_refuses():
    """A ``JSR`` moves ``sp`` and returns through the stack: the slot is not private."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", LO).i("PHA").i("JSR", "abs", SUB)
    a.i("PLA").i("STA", "abs", OUT).i("RTS")
    sub = G.Asm(SUB).i("LDA", "abs", HI).i("STA", "abs", OUT + 1).i("RTS").assemble()
    data = {LO: 0x40, HI: 0x02}
    data.update({SUB + k: b for k, b in enumerate(sub)})
    _m, prog, text = _build("jsr_between", a, data)
    (pr,) = _stack(prog)
    assert pr.status == "refused"
    assert pr.lemma.endswith("a read is not dominated by a store of the slot")
    assert "m_01FD = m_1400" in text and "sid.v1.ctrl = m_01FD" in text  # untouched


def test_the_rts_trick_is_the_goto_it_always_was():
    """``PHA``/``PHA``/``RTS`` dispatch: the push pair and the displacement lift.

    A constant trick is one dispatch -- control lands at the pushed word plus
    one -- so the stores, the ``sp`` update and the ret become ``goto ($1320)``,
    the procedure balances, and rung (d0') drops ``sp`` outright."""
    _m, prog, text = _check(G.t_rts_trick(np.random.default_rng(5)))
    assert not _stack(prog, "named")
    assert "goto ($1320)" in text
    assert "m_01FD = " not in text and "m_01FC = " not in text and "sp = " not in text
    (rts,) = [p for p in prog.proofs if p.kind == "rts"]
    assert rts.status == "resolved" and "goto ($1320)" in rts.lemma
    (sp,) = [p for p in prog.proofs if p.kind == "sp"]
    assert sp.status == "resolved" and "no reader" in sp.lemma


def test_a_tsx_save_txs_restore_bracket_dissolves():
    """The context-save idiom: sp spilled, moved, restored -- all of it fabric.

    The bracket cell is saved once and read only to restore, the symbolic walk
    returns to the entry state whatever ran between, and the datum that rode
    the stack arrives as the one word it was (docs/frameprog.md 7.9)."""
    a = G.Asm(G.ORG)
    a.i("TSX").i("STX", "abs", G.CNT + 0x10)
    a.i("LDA", "abs", LO).i("PHA")
    a.i("LDA", "abs", HI).i("PHA")
    a.i("PLA").i("STA", "abs", OUT + 1)
    a.i("PLA").i("STA", "abs", OUT)
    a.i("LDX", "abs", G.CNT + 0x10).i("TXS").i("RTS")
    _m, prog, text = _build("spsave", a, {LO: 0x40, HI: 0x02})
    body = text[text.index("sub_") :]
    assert "sp" not in body and "m_01" not in body
    assert "sid.v1.ctrl = s1" in body and "sid.v1.attack_decay = m_1401" in body
    (sp,) = [p for p in prog.proofs if p.kind == "sp"]
    assert sp.status == "resolved"


def test_a_txs_stack_switch_with_restore_dissolves():
    """A constant TXS opens a new stack; restored before ret, it is still fabric."""
    a = G.Asm(G.ORG)
    a.i("TSX").i("STX", "abs", G.CNT + 0x10)
    a.i("LDX", "imm", 0x80).i("TXS")
    a.i("LDA", "abs", LO).i("PHA")
    a.i("LDA", "abs", HI).i("PHA")
    a.i("PLA").i("STA", "abs", OUT + 1)
    a.i("PLA").i("STA", "abs", OUT)
    a.i("LDX", "abs", G.CNT + 0x10).i("TXS").i("RTS")
    _m, prog, text = _build("spswitch", a, {LO: 0x41, HI: 0x03})
    body = text[text.index("sub_") :]
    assert "sp" not in body and "m_01" not in body and "m_0080" not in body
    assert "sid.v1.ctrl = s1" in body and "sid.v1.attack_decay = m_1401" in body
    (sp,) = [p for p in prog.proofs if p.kind == "sp"]
    assert sp.status == "resolved"


# ---- the premise, stated ----------------------------------------------------------
CELL = 0x01FD


def _store(val=("const", 7, 1)):
    return ("st", ("const", CELL, 2), val)


def _read(name="w0"):
    return ("asg", name, ("mem", ("const", CELL, 2), 1))


def _indexed(base, name="x"):
    return ("op", "INT_ADD", (("const", base, 2), ("op", "INT_ZEXT", (("loc", name),), 2)), 2)


def _clean():
    return [_store(), _read()]


def _read_first():
    return [_read(), _store()]


def _control_between():
    return [_store(), ("ret", False), _read()]


def _one_arm_only():
    return [("if", 0, ("loc", "cflag"), [_store()], []), _read()]


def _stack_peek():
    return [_store(), ("st", _indexed(0x0100), ("const", 0, 1)), _read()]


def _blind_store():
    return [_store(), ("st", ("loc", "t0", 2), ("const", 0, 1)), _read()]


def _blind_store_before():
    return [("st", ("loc", "t0", 2), ("const", 0, 1))] + _clean()


def _word_store():
    return [("st", ("const", CELL, 2), ("mem", ("const", 0x1400, 2), 2)), _read()]


@pytest.mark.parametrize(
    "build,want",
    [
        (_clean, None),
        (_read_first, "a read is not dominated by a store of the slot"),
        (_control_between, "a read is not dominated by a store of the slot"),
        (_one_arm_only, "a read is not dominated by a store of the slot"),
        (_stack_peek, "another resolvable access may touch the slot"),
        (_blind_store, "an unresolvable address may alias the live slot"),
        (_blind_store_before, None),
        (_word_store, "another resolvable access may touch the slot"),
    ],
)
def test_the_refusal_diagnostic_names_the_premise_that_failed(build, want):
    slot = framestack._Slot(CELL).run(build(), set())
    assert slot.why == want
    pr = slot.proof("s0")
    assert pr.kind == "stack" and pr.status == ("named" if want is None else "refused")
    assert pr.targets == (CELL,) and pr.lemma.endswith(want or "data temporary, local s0")


def test_a_slot_a_second_procedure_may_touch_is_not_private():
    """The rewrite is per procedure, so a slot two of them address stays memory."""
    slot = framestack._Slot(CELL).run(_clean(), {CELL})
    assert slot.why == "another procedure may touch the slot"


def test_the_synthetic_names_collide_with_nothing_the_procedures_bind():
    """``s0`` is taken by the player, so the slot takes the next free ``s<k>``."""
    procs = [(0x1000, [], [], [("asg", "s0", ("const", 1, 1))] + _clean())]
    (pr,) = framestack.apply_rung(procs)
    assert pr.status == "named" and pr.lemma.endswith("local s1")
    assert procs[0][3][1:] == [("asg", "s1", ("const", 7, 1)), ("asg", "w0", ("loc", "s1"))]
