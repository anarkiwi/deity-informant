"""S6 presentation: copy folding, outlining, machine texture (hermetic snippets)."""

import re

from deity_informant.tuneprog import texture, unroll
from deity_informant.tuneprog.ir import (
    Block,
    Const,
    Goto,
    If,
    Proc,
    Return,
    Rgn,
    Store,
    Var,
)

from _asm import asm
from _prog import PLAY, printed as _text, proc_body as _body, tuneprog


# ---- copy folding ------------------------------------------------------------
def _voices(third="$D412", copies=3):
    """Three unrolled copies of one write, over cells init fills through an index."""
    return asm(
        PLAY,
        "init: LDY #$17",
        "lp0: LDA #$00",
        "STA $D400,Y",
        "DEY",
        "BPL lp0",
        "LDX #$02",
        "lp: STA img,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play: LDA img",
        "STA $D404",
        "STA $D405",
        "STA $D406",
        "LDA img+1",
        "STA $D40B",
        "STA $D40C",
        "STA " + ("$D40D" if copies > 2 else third),
        *(["LDA img+2", "STA " + third, "STA $D413", "STA $D414"] if copies > 2 else []),
        "INC cnt",
        "RTS",
        "img: BRK",
        "BRK",
        "BRK",
        "cnt: BRK",
    )


def test_three_isomorphic_copies_fold_into_one_for_over_the_index():
    body = "\n".join(_body(_text(_voices()), "tick"))
    assert "for v in 0, 1, 2:" in body
    assert "sid[v].ctrl = ctrl[v]" in body
    assert body.count("sid[v].") == 3 and "sid[0]." not in body


def test_three_calls_of_one_procedure_with_stepping_arguments_fold():
    # GT2's voice loop: JSR, JSR, then the third call by falling into the routine.
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "LDX #$0E",
        "il: STA $1200,X",
        "DEX",
        "BPL il",
        "RTS",
        "play: INC cnt",
        "LDX #$00",
        "JSR chn",
        "LDX #$07",
        "JSR chn",
        "LDX #$0E",
        "chn: LDA cnt",
        "AND #$0F",
        "STA $1200,X",
        "LDA $1200,X",
        "STA $D404",
        "RTS",
        "cnt: BRK",
    )
    body = "\n".join(_body(_text(code, calls=8), "tick"))
    assert "for v in 0, 1, 2:" in body, body
    assert re.search(r"for v in 0, 1, 2:\n\s+\w+\(x=\(v \* 7\)\)", body), body


def test_a_copy_that_differs_in_one_operand_does_not_fold():
    body = "\n".join(_body(_text(_voices(third="$D40E", copies=2)), "tick"))
    assert "for v in" not in body
    assert "sid[0].ctrl = " in body and "sid[1].ctrl = " in body


def test_the_index_of_a_folded_copy_may_start_above_zero():
    code = asm(
        PLAY,
        "init: LDY #$17",
        "lp0: LDA #$00",
        "STA $D400,Y",
        "DEY",
        "BPL lp0",
        "LDX #$02",
        "lp: STA img,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play: LDA img",
        "STA $D404",
        "STA $D405",
        "LDA img+1",
        "STA $D40B",
        "STA $D40C",
        "STA $D40D",
        "LDA img+2",
        "STA $D412",
        "STA $D413",
        "STA $D414",
        "INC cnt",
        "RTS",
        "img: BRK",
        "BRK",
        "BRK",
        "cnt: BRK",
    )
    body = "\n".join(_body(_text(code), "tick"))
    assert "for v in 0, 1:" in body and "sid[v + 1].ctrl = " in body


# ---- outlining ---------------------------------------------------------------
SHARED = [
    "shared: LDA #$07",
    "STA $D404",
    "LDA #$08",
    "STA $D405",
    "LDA #$09",
    "STA $D406",
    "LDA #$0A",
    "STA $D407",
    "LDA #$0B",
    "STA $D408",
    "LDA #$0C",
    "STA $D409",
    "RTS",
]


def test_a_run_two_procedures_share_prints_once_as_a_helper():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$01",
        "BNE odd",
        "JSR pa",
        "JMP done",
        "odd: JSR pb",
        "done: INC cnt",
        "RTS",
        "pa: LDA #$11",
        "STA $D400",
        "JMP shared",
        "pb: LDA #$22",
        "STA $D401",
        "JMP shared",
        *SHARED,
        "cnt: BRK",
    )
    doc = _text(code, calls=8)
    assert doc.count("writeout():") == 1
    assert doc.count("    writeout()") == 2
    assert doc.count("sid[0].sr = 9") == 1


def test_a_run_only_one_procedure_has_stays_where_it_is():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: JSR pa",
        "INC cnt",
        "RTS",
        "pa: LDA #$11",
        "STA $D400",
        "JMP shared",
        *SHARED,
        "cnt: BRK",
    )
    doc = _text(code)
    assert "writeout()" not in doc and "sid[0].sr = 9" in doc


# ---- shared tails ------------------------------------------------------------
def test_a_shared_tail_two_jumps_reach_becomes_a_procedure_instead_of_a_goto():
    # GT2's mt_loadregs: every voice path ends `JMP $140F`, one arm returns early.
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "STA wave",
        "RTS",
        "play: INC cnt",
        "LDA cnt",
        "AND #$03",
        "BEQ zero",
        "CMP #$01",
        "BEQ one",
        "LDX #$0E",
        "LDA #$99",
        "STA $D406,X",
        "RTS",
        "one: LDX #$00",
        "LDA #$21",
        "STA wave",
        "JMP loadregs",
        "zero: LDX #$07",
        "LDA #$41",
        "STA wave",
        "loadregs: LDA wave",
        "AND #$FE",
        "STA $D404,X",
        "LDA #$00",
        "STA $D405,X",
        "RTS",
        "wave: BRK",
        "cnt: BRK",
    )
    doc = _text(code, calls=8)
    assert "goto" not in doc, doc
    head = [l for l in doc.splitlines() if l.startswith("p_%04X(x):" % code.labels["loadregs"])]
    assert head, doc  # the tail is a procedure taking the voice index it reads
    assert doc.count("sid.reg[4 + x]") == 1 and doc.count("(x=") == 2  # one copy, two calls


def test_a_tail_promotion_that_would_not_pay_is_rolled_back():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: INC cnt",
        "LDA cnt",
        "AND #$01",
        "BNE odd",
        "LDA #$21",
        "JMP out",
        "odd: LDA #$41",
        "out: STA $D404",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code, calls=8)
    assert "goto" not in doc and "p_" not in doc  # an if/else needs no helper


# ---- the stack ---------------------------------------------------------------
def test_a_balanced_push_and_pop_is_a_temporary_and_hides_the_stack_pointer():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA flag",
        "STA cnt",
        "RTS",
        "play: LDA flag",
        "PHA",
        "JSR work",
        "PLA",
        "STA flag",
        "INC cnt",
        "RTS",
        "work: LDA #$07",
        "STA $D400",
        "RTS",
        "flag: BRK",
        "cnt: BRK",
    )
    doc = _text(code)
    assert "saved = b" in doc and "= saved" in doc
    assert "sp" not in doc


def test_a_push_and_a_pop_a_branch_apart_are_still_one_temporary():
    # GT2 holds a byte over a compare, so the PLA sits in another block than the PHA.
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA flag",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$07",
        "PHA",
        "CMP #$05",
        "BCC low",
        "LDA #$01",
        "STA $D404",
        "low: PLA",
        "STA flag",
        "INC cnt",
        "RTS",
        "flag: BRK",
        "cnt: BRK",
    )
    doc = _text(code, calls=8)
    assert "saved = " in doc and "= saved" in doc
    assert "sp" not in doc


def test_a_stack_scratch_area_keeps_the_stack_pointer():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDX #$02",
        "lp: TXA",
        "PHA",
        "DEX",
        "BPL lp",
        "PLA",
        "STA $D400",
        "PLA",
        "PLA",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code)
    assert "sp" in doc


# ---- 16-bit chains -----------------------------------------------------------
def _chain(op, clc="CLC"):
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA lo",
        "STA hi",
        "STA cnt",
        "RTS",
        "play: " + clc,
        "LDA lo",
        op + " #$34",
        "STA lo",
        "LDA hi",
        op + " #$12",
        "STA hi",
        "STA $D400",
        "INC cnt",
        "RTS",
        "lo: BRK",
        "hi: BRK",
        "cnt: BRK",
    )


def test_an_add_chain_over_two_cells_prints_as_one_16_bit_statement():
    doc = _text(_chain("ADC"))
    assert "acc += $1234" in doc and "carry(" not in doc


def test_a_subtract_chain_over_two_cells_prints_as_one_16_bit_statement():
    doc = _text(_chain("SBC", clc="SEC"))
    assert "acc -= $1234" in doc and "carry(" not in doc


def test_a_table_pair_read_by_a_chain_prints_as_one_16_bit_table():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA lo",
        "STA hi",
        "STA cnt",
        "RTS",
        "play: LDX cnt",
        "CLC",
        "LDA lo",
        "ADC tlo,X",
        "STA lo",
        "LDA hi",
        "ADC thi,X",
        "STA hi",
        "STA $D400",
        "INC cnt",
        "RTS",
        "lo: BRK",
        "hi: BRK",
        "cnt: BRK",
        "tlo: BRK",
        "BRK",
        "BRK",
        "BRK",
        "thi: BRK",
        "BRK",
        "BRK",
        "BRK",
    )
    doc = _text(code, calls=4)
    line = [l for l in doc.splitlines() if "acc +=" in l]
    assert line and line[0].count("[") == 1, doc


# ---- value inlining ----------------------------------------------------------
def _pair(*lines):
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA p",
        "STA q",
        "STA w",
        "STA cnt",
        "RTS",
        "play: " + lines[0],
        *lines[1:],
        "INC cnt",
        "RTS",
        "work: LDA #$01",
        "STA w",
        "RTS",
        "p: BRK",
        "q: BRK",
        "w: BRK",
        "cnt: BRK",
    )


def test_a_load_moves_past_a_store_to_another_region():
    body = _body(_text(_pair("LDA p", "LDX #$07", "STX w", "STA $D400")), "tick")
    assert not any("t1 = " in l for l in body), body
    assert any(l.strip().startswith("sid[0].freq_lo = ") for l in body)


def test_a_load_does_not_move_past_a_store_to_its_own_region():
    body = _body(_text(_pair("LDA q", "LDX p", "STX q", "STA $D400")), "tick")
    assert any(l.strip().startswith("t1 = ") for l in body), body
    assert "    sid[0].freq_lo = t1" in body


def test_a_load_does_not_move_past_a_call():
    body = _body(_text(_pair("LDX w", "JSR work", "STX $D400")), "tick")
    assert any(l.strip().startswith("t1 = ") for l in body), body
    assert "    sid[0].freq_lo = t1" in body


def test_one_input_read_does_not_move_past_another():
    body = _body(_text(_pair("LDA $D012", "LDX $D012", "STX w", "STA $D400")), "tick")
    assert "    t1 = input($D012)" in body and "    sid[0].freq_lo = t1" in body


# ---- liveness and conditions -------------------------------------------------
def test_a_value_only_a_dead_return_reads_is_dropped():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: JSR work",
        "INC cnt",
        "RTS",
        "work: LDA #$07",
        "STA $D400",
        "LDA #$99",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code)
    assert "$99" not in doc


def test_two_tests_that_share_a_target_print_as_or():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$01",
        "BNE hit",
        "LDA cnt",
        "CMP #$05",
        "BCS skip",
        "hit: LDA #$07",
        "STA $D400",
        "skip: INC cnt",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code, calls=12)
    assert " or " in doc and "goto" not in doc


def test_two_tests_that_guard_one_arm_merge_into_one_condition():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$01",
        "BNE skip",
        "LDA cnt",
        "CMP #$05",
        "BCC skip",
        "LDA #$07",
        "STA $D400",
        "skip: INC cnt",
        "RTS",
        "cnt: BRK",
    )
    doc = _text(code, calls=12)
    assert "((call_counter & 1) != 0) or (call_counter < 5)" in doc
    assert "goto" not in doc


def test_short_circuit_recovers_and_over_the_control_flow_graph():
    proc = Proc(
        "f",
        (),
        (),
        {
            b.label: b
            for b in (
                Block("b0", [], If(Var("p"), "b1", "out")),
                Block("b1", [], If(Var("q"), "hit", "out")),
                Block("hit", [Store("ram", Const(1), Const(7), 1, 1, 1, 0)], Goto("out")),
                Block("out", [], Return()),
            )
        },
        "b0",
    )
    assert texture.shortcircuit(proc) == 1
    term = proc.blocks["b0"].term
    assert term.c.op == "and" and term.t == "hit" and "b1" not in proc.blocks


# ---- mirror cells and the switch they share ----------------------------------
def test_two_cells_written_together_print_as_one():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA m1",
        "STA m2",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "STA m1",
        "STA m2",
        "LDA m1",
        "STA $D400",
        "LDA m2",
        "STA $D401",
        "INC cnt",
        "RTS",
        "m1: BRK",
        "m2: BRK",
        "cnt: BRK",
    )
    body = _body(_text(code), "tick")
    rhs = [l.split(" = ")[1] for l in body if l.strip().startswith("sid[0].")]
    assert len(rhs) == 2 and rhs[0] == rhs[1], body


def test_a_patched_opcode_pair_becomes_one_switch_over_a_16_bit_step():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA lo",
        "STA hi",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$01",
        "BEQ plus",
        "LDA #$E9",
        "BNE set",
        "plus: LDA #$69",
        "set: STA op1",
        "STA op2",
        "CLC",
        "LDA lo",
        "op1: SBC #$34",
        "STA lo",
        "LDA hi",
        "op2: SBC #$12",
        "STA hi",
        "STA $D400",
        "INC cnt",
        "RTS",
        "lo: BRK",
        "hi: BRK",
        "cnt: BRK",
    )
    body = "\n".join(_body(_text(code, calls=8), "tick"))
    assert body.count("switch ") == 1, body
    assert "acc += $1234" in body and "acc -= " in body
    assert "carry(" not in body


def _run(shared=None):
    """Two copies of a run that relocates two cells, plus a constant both share."""
    a = [("r", 1), ("k@0", 0x551A), ("k@0", 0x5520)]
    b = [("r", 1), ("k@0", 0x561A), ("k@0", 0x5620)]
    if shared is not None:
        a, b = a + [("k@0", shared)], b + [("k@0", shared)]
    return [a, b]


CELL = {1: Rgn(1, "cells", 0x551A, 1, "state", 1, b"\0", ())}


def test_a_run_that_relocates_two_cells_is_one_mapping():
    deltas, slots = unroll.steps(_run(), CELL)
    assert deltas == [0, 0, 0] and len(slots) == 2
    assert sorted(slots) == [(1, 0x551A), (1, 0x5520)]


def test_a_constant_every_copy_shares_does_not_step_with_the_index():
    deltas, slots = unroll.steps(_run(shared=0x5600), CELL)
    assert deltas == [0, 0, 0, 0] and len(slots) == 2  # the constant keeps its literal


def test_a_shared_constant_equal_to_a_slot_refuses_the_fold():
    """Copy 0's constant is all the folded body holds: slot and cell look alike."""
    assert unroll.steps(_run(shared=0x551A), CELL) == (None, None)
