"""SID Wizard (Hermit, anatomy 3.4) end to end on two Hermit tunes (``hvsc``).

Emomyst (SW 1.6) at 30 s and End of the World (SW 1.9) at 20 s: the init-time
relocation of table operands, the runtime blob base, the patched immediates, the
three dispatchers, the voice loop, the slowdown gate and the zero-page save.
"""

import re

import pytest

from deity_informant.tuneprog import ssa
from deity_informant.tuneprog.ir import Const, Store

from _hvsc import EMOMYST, EOTW, body, decompiled, load_addrs, switches

pytestmark = pytest.mark.hvsc

INDEXJ1, INDEXJ2, INDEXJP = 0x1952, 0x1A14, 0x19D0  # the three patched operands


def _switches(prog):
    """``{cell address: (width, arms)}`` for every computed-target switch."""
    return {a: (w, len(t.cases)) for a, w, t in switches(prog)}


def _tick_procs(prog):
    return set(prog.procs) - ssa.init_reachable(prog)


def test_emomyst_is_certified_over_thirty_seconds():
    run = decompiled(EMOMYST, seconds=30)
    trace, v, calls = run.trace, run.v, run.calls
    assert v.div is None and v.call == calls
    # the slowdown gate: the first play call returns before playing (anatomy 3.4.2)
    assert int((trace.wlog["call"] == 0).sum()) == 0
    assert int((trace.wlog["call"] == 1).sum()) > 0


def test_emomyst_folds_every_relocated_table_operand_outside_init():
    run = decompiled(EMOMYST, seconds=30)
    prog, trace = run.prog, run.trace
    init_only = trace.cells - trace.written_play
    assert len(init_only) >= 30  # 30 operands, most of them two bytes

    # every instruction whose operand init relocated, and which the tick runs
    reloc = [k for k in trace.sites if {k[0] + 1, k[0] + 2} & init_only]
    assert len([k for k in reloc if trace.sites[k]["phases"] & 2]) >= 25

    # constants in the tick; inside init the loads its own stores define (design S2)
    tick = _tick_procs(prog)
    assert not load_addrs(prog, tick) & init_only
    assert len(load_addrs(prog, ssa.init_reachable(prog)) & init_only) >= 8


def test_emomyst_prints_its_relocation_as_a_loop_over_the_pointer_tables():
    run = decompiled(EMOMYST, seconds=30)
    text, prog = run.text, run.prog
    lines = "\n".join(body(text, "init"))
    assert "while True:" in lines  # the DataPtr/PtrValu loop, not 30 unrolled stores
    assert re.search(r"ptr_\d\[ptr \+ [12]\] = ", lines), lines  # through the folded pointer
    stores = [
        s
        for b in prog.procs["init"].blocks.values()
        for s in b.stmts
        if type(s) is Store and type(s.a) is not Const and s.cls != "raw"
    ]
    assert 2 <= len(stores) <= 8  # the loop body's stores, once each


def test_emomyst_adds_the_blob_base_to_every_pointer_it_sets():
    run = decompiled(EMOMYST, seconds=30)
    text, names = run.text, run.names
    # LDA lo,Y; CLC; ADC SWP_OFFSET; STA zp; LDA hi,Y; ADC SWP_OFFSET+1; STA zp+1
    assert len(re.findall(r"ptr = \(T[0-9A-F]{4}\[.+\] \+ base\)", text)) >= 4, text
    assert "base" in names.u16.values() and "ptr" in names.u16.values()
    assert len([n for n in names.u16.values() if n.startswith("T")]) >= 2


def test_emomyst_keeps_its_patched_immediates_as_named_globals():
    run = decompiled(EMOMYST, seconds=30)
    text, names, prog, trace = run.text, run.names, run.prog, run.trace
    play = trace.cells & trace.written_play
    assert len(play) >= 20  # anatomy 3.4.1: 27 play-time immediates
    assert len(play & load_addrs(prog, _tick_procs(prog))) >= 20

    # the ones a role reaches are named by it (MAINVOL, FLTBAND, RESONIB, FSWITCH)
    named = {names.region.get(r.id) for r in prog.storage if r.base in play}
    assert {"res_route", "mode_vol", "cutoff_hi", "cutoff_lo"} <= named
    assert "sid.mode_vol = ($F | mode_vol)" in text
    assert re.search(r"\btimer \+= 1", text), text  # CWEPCNT, an `INC $15DD` in place


def test_emomyst_dispatches_through_two_branch_tables_and_one_jump_table():
    run = decompiled(EMOMYST, seconds=30)
    text, prog, trace = run.text, run.prog, run.trace
    sw = _switches(prog)
    assert {INDEXJ1, INDEXJ2, INDEXJP} <= set(sw)
    assert sw[INDEXJ1] == (1, 7)  # NOTEFXTBL: 8 entries, 7 distinct targets
    assert sw[INDEXJ2] == (1, 14)  # SMALLFXTBL: 14 branch offsets
    assert sw[INDEXJP] == (2, 25)  # BIGFXTABLE: 31 words, 25 distinct targets
    assert {INDEXJ1, INDEXJ2} <= trace.cells and INDEXJP in trace.cells

    # a patched offset on an always-taken branch is a switch over site + 2 + offset
    assert re.search(r"switch \(\$1\w{3} \+ sext\([^)]+\)\)", text), text
    assert "switch b19D0:" in text and text.count("trap 'unverified'") >= 25


def test_emomyst_prints_one_dotrack_over_the_three_voices():
    run = decompiled(EMOMYST, seconds=30)
    text = run.text
    # LDX #14; JSR DOTRACK; LDX #7; JSR; LDX #0; JSR
    loop = r"for v in 0, 1, 2:\n(\s+# \$\w+\n)?\s+row_apply\(x=\(\$E - \(v \* 7\)\)\)"
    assert re.search(loop, text), text
    assert text.count("row_apply(x=") == 1
    lines = "\n".join(body(text, "row_apply"))

    # the tempo test: SEC; SBC TEMPOTBL-1,Y; BEQ new row; BVC same row (the V flag)
    # SPDCNT is one field of VARIABLES, which init clears as one block and the
    # tick walks at stride 7: bunch 1 ($1024 + $15), voice x/7 (views.record_split)
    spdcnt = r"rec\[x/7 \+ 3\]\.\w+"
    assert re.search(r"if overflow\(%s - \w+\)" % spdcnt, lines), lines
    assert re.search(r"%s \+= 1" % spdcnt, lines), lines  # SPDCNT is post-incremented
    assert lines.count("if t4 == 0:") == 1 and "if t4 == 2:" in lines


def test_emomyst_passes_the_tick_number_to_the_hard_restart_as_a_bit_mask():
    run = decompiled(EMOMYST, seconds=30)
    text = run.text
    # HARDRST: A = 2 at tick 0, 1 at tick 1, ANDed with the instrument control byte
    assert re.search(r"\bsaved\d* = a\n(.*\n)?\s+if \(saved\d* & T\w+\[", text), text
    assert len(re.findall(r"a\d+ = 2\n\s+# \$1310", text)) == 1, text
    assert len(re.findall(r"a\d+ = 1\n\s+# \$1310", text)) == 1, text


def test_end_of_the_world_is_the_same_player_two_versions_on():
    run = decompiled(EOTW, seconds=20)
    text, v, calls = run.text, run.v, run.calls
    assert v.div is None and v.call == calls

    # ZEROPAGESAVE: the pushes around play and init, and no stack pointer left
    assert not re.search(r"\bsp\d*\b", text), text
    lines = body(text, "tick")
    assert re.search(r"saved\d* = ptr", "\n".join(lines[:3])), lines[:3]
    assert re.search(r"ptr = saved\d*", "\n".join(lines[-3:])), lines[-3:]
    assert re.search(r"for v in 0, 1, 2:\n\s+row_apply\(x=\(\$E - \(v \* 7\)\)\)", text)


def test_end_of_the_world_carries_the_subtune_and_the_1_9_write_order():
    run = decompiled(EOTW, seconds=20)
    text, names, prog, trace = run.text, run.names, run.prog, run.trace
    # init saves the subtune in an immediate, SETSTUNE patches the orderlist reads
    init = "\n".join(body(text, "init"))
    assert re.search(r"b2A05 = (a\b|0)", init), init
    assert "cursor_302E = (a << 3)" in text  # SUBTUNES[sub * 8]
    assert len(re.findall(r"p_30(1B|24)\(", text)) >= 3
    assert {0x2A05, 0x302E, 0x3045} <= trace.cells  # SWPdone, SUBTPOS, XSTORE

    # 1.9 writes SR before AD (1.6 the other way) and reads FREQTBH by pitch
    assert re.search(r"sid\[x/7\]\.sr = .*\n\s+sid\[x/7\]\.ad = ", text), text
    assert re.search(r"sid\[x/7\]\.freq_hi = FREQ_HI\[", text), text

    # MULPLY is assembled in but never called, so no procedure starts there
    assert not any(p.blocks[p.entry].src == 0x2AA2 for p in prog.procs.values())
    assert len(names.u16) >= 4  # the pointer, the blob base, two word tables


def test_the_branch_dispatchers_print_their_displacement_as_a_signed_byte():
    """``(base + T[i]) - ((T[i] & $80) << 1)`` is ``base + sext(T[i])``, an identity."""
    for rel, secs in ((EMOMYST, 30), (EOTW, 20)):
        text = decompiled(rel, seconds=secs).text
        assert len(re.findall(r"switch \(\$\w+ \+ sext\(", text)) == 2, text
        assert "& $80) << 1" not in text


def test_the_tempo_test_prints_as_one_overflow_instead_of_its_three_xors():
    """``BVC`` after ``SBC`` is the V flag; the flag algebra is not the program."""
    for rel, secs in ((EMOMYST, 30), (EOTW, 20)):
        text = decompiled(rel, seconds=secs).text
        assert re.search(r"if overflow\(rec\[x/7 \+ 3\]\.timer_2 - \w+\)", text), text
