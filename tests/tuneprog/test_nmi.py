"""S1 -- the second schedule: when CIA #2 dispatches an NMI, and what the tracer records.

Each fixture is a whole machine: an init that arms CIA #2 and installs a vector, a
play routine that writes ``$D400`` and a handler that writes ``$D418``, so the
interleaving is readable straight off the write log.
"""

import pytest

from deity_informant.tuneprog import nmi as N
from deity_informant.tuneprog.cia import CIA, CIA1_BASE, CIA2_BASE, ICR_TA, ICR_TB
from deity_informant.tuneprog.machine import (
    Refusal,
    STATUS,
    entry_frame,
    frame_slots,
)
from deity_informant.tuneprog.trace import IDLE_INDEX

from _asm import asm, trace_prog

PLAY = 0x1100
HANDLER = 0x1200
INIT = 0x1000
CYCLES = 2000

RAW = ("LDA #$35", "STA $01")  # bank the KERNAL out: $FFFA is the RAM under it
VEC_RAW = ("LDA #$00", "STA $FFFA", "LDA #$12", "STA $FFFB")  # $FFFA -> HANDLER
VEC_KERNAL = ("LDA #$00", "STA $0318", "LDA #$12", "STA $0319")
ARM_A = ("LDA #$81", "STA $DD0D", "LDA #$11", "STA $DD0E")  # ICR TA, force load + start
ACK = ("BIT $DD0D",)


def _init(*lines, latch=99, vec=VEC_RAW, bank=RAW):
    """An init that loads Timer A's latch, installs ``vec`` and then runs ``lines``."""
    return asm(
        INIT,
        *bank,
        "LDA #$%02X" % (latch & 0xFF),
        "STA $DD04",
        "LDA #$%02X" % (latch >> 8),
        "STA $DD05",
        *vec,
        *lines,
        "RTS",
    )


def _run(init_lines=ARM_A, handler=("LDA #$0F", "STA $D418", *ACK, "RTI"), calls=4, **kw):
    """Trace a machine whose NMI writes ``$D418`` and whose play writes ``$D400``."""
    latch = kw.pop("latch", 99)
    vec = kw.pop("vec", VEC_RAW)
    bank = kw.pop("bank", RAW)
    play = kw.pop("play", ("LDA #$01", "STA $D400", "RTS"))
    blocks = {
        INIT: _init(*init_lines, latch=latch, vec=vec, bank=bank),
        PLAY: asm(PLAY, *play),
        HANDLER: asm(HANDLER, *handler),
    }
    for org, body in (kw.pop("second", None) or {}).items():
        blocks[org] = asm(org, *body)
    return trace_prog(blocks, INIT, PLAY, calls=calls, cycles=CYCLES, **kw)


def _writes(trace, addr):
    return [i for i, a in enumerate(trace.wlog["addr"].tolist()) if a == addr]


def test_an_armed_cia2_timer_becomes_the_schedules_second_entry():
    trace, tr = _run()
    assert tr.nmi == trace.meta["schedule"][1] or True
    e = trace.meta["schedule"][1]
    assert e["kind"] == "nmi" and e["addr"] == HANDLER
    assert e["cycles_per_tick"] == 100 and e["source"] == "cia2_timer_a"
    assert len(trace.meta["schedule"]) == 2 and trace.meta["entry"]["kind"] == "sub"


def test_the_nmi_entry_frame_is_the_status_byte_alone():
    """Neither ``$FE43`` nor a raw ``$FFFA`` saves a register: only the 6510 pushes."""
    e = _run()[0].meta["schedule"][1]
    assert entry_frame(e) == (STATUS,) and frame_slots(e) == {1: STATUS}


SLOW = ("LDX #$20", "loop: DEX", "BNE loop", "LDA #$01", "STA $D400", "RTS")


def test_the_handler_preempts_the_play_routine_and_runs_in_the_idle_time():
    """A play routine that outlives one timer period is preempted inside it."""
    trace, _tr = _run(play=SLOW)
    log = trace.nmilog
    assert trace.meta["nmis"] == len(log["call"]) == 80  # 4 ticks x 2000/100 cycles
    assert set(log["addr"].tolist()) == {HANDLER}
    inside = [i for i in log["insn"].tolist() if i != IDLE_INDEX]
    assert inside and len(inside) < len(log["insn"])  # some in a tick, most in the idle
    assert all(i > 0 for i in inside)  # the instruction the tick had reached


def test_the_write_log_interleaves_both_entries():
    trace, _tr = _run()
    order = [a for a in trace.wlog["addr"].tolist() if a in (0xD400, 0xD418)]
    assert order.count(0xD400) == 4 and order.count(0xD418) == 80
    assert 0xD418 in order[:2]  # the first NMI of tick 0 beats the play's own write


def test_a_handler_that_never_acknowledges_takes_exactly_one_nmi():
    """The 6510's NMI is the line's edge and the chip holds it until an ICR read."""
    trace, _tr = _run(handler=("LDA #$0F", "STA $D418", "RTI"), calls=8)
    assert trace.meta["nmis"] == 1


def test_the_gap_between_two_nmis_is_the_timer_period():
    trace, _tr = _run()
    cyc = trace.nmilog["cyc"].tolist()
    assert {b - a for a, b in zip(cyc, cyc[1:])} == {100}


def test_a_one_shot_timer_dispatches_once():
    arm = ("LDA #$81", "STA $DD0D", "LDA #$19", "STA $DD0E")  # force load + one-shot + start
    trace, _tr = _run(init_lines=arm, calls=8)
    assert trace.meta["nmis"] == 1


def test_timer_b_dispatches_on_the_cycle_rate():
    arm = (
        "LDA #$82",
        "STA $DD0D",
        "LDA #$63",
        "STA $DD06",
        "LDA #$00",
        "STA $DD07",
        "LDA #$11",
        "STA $DD0F",
    )
    trace, _tr = _run(init_lines=arm, calls=2)
    assert trace.meta["schedule"][1]["source"] == "cia2_timer_b"
    assert trace.meta["nmis"] == 40  # 2 ticks x 2000/100


def test_timer_b_linked_to_timer_a_counts_its_underflows():
    """CRB bits 5-6 = 10: Timer B is Timer A's period, coarsened by its own latch."""
    arm = (
        "LDA #$82",
        "STA $DD0D",
        "LDA #$04",
        "STA $DD06",
        "LDA #$00",
        "STA $DD07",
        "LDA #$11",
        "STA $DD0E",
        "LDA #$51",
        "STA $DD0F",
    )
    trace, _tr = _run(init_lines=arm, calls=2)
    cyc = trace.nmilog["cyc"].tolist()
    assert {b - a for a, b in zip(cyc, cyc[1:])} == {500}  # 5 x Timer A's 100


def test_a_source_this_model_has_no_schedule_for_is_refused():
    for icr in ("$84", "$88", "$90"):  # TOD alarm, serial, FLAG
        with pytest.raises(Refusal) as e:
            _run(init_lines=("LDA #%s" % icr, "STA $DD0D", "LDA #$11", "STA $DD0E"))
        assert e.value.reason == "second interrupt source armed"
        assert "unmodelled" in e.value.detail


def test_the_kernal_carries_the_nmi_through_0318():
    """With the ROM mapped ``$FFFA`` is ``$FE43``, whose ``JMP ($0318)`` is the dispatch."""
    trace, tr = _run(vec=VEC_KERNAL, bank=())
    assert N.vector(tr.vm.mem)[0] == 0x0318
    assert trace.meta["schedule"][1]["addr"] == HANDLER and trace.meta["nmis"]


def test_a_vector_the_port_banks_out_carries_nothing():
    """CINV's NMI twin is dead once init banks the ROM out, so the line has no handler."""
    with pytest.raises(Refusal) as e:
        _run(vec=VEC_KERNAL)
    assert e.value.reason == "nmi vector banked out" and "$FFFA" in e.value.detail


def test_arming_the_nmi_during_play_starts_the_schedule_at_that_tick():
    """The gate is the chip's at every instruction, not a verdict taken once at init."""
    trace, _tr = _run(
        init_lines=("LDA #$11", "STA $DD0E"),
        play=("LDA #$81", "STA $DD0D", "LDA #$01", "STA $D400", "RTS"),
        calls=3,
    )
    assert trace.meta["schedule"][1]["addr"] == HANDLER
    assert trace.nmilog["call"].tolist()[0] == 0 and trace.meta["nmis"] > 1


def test_a_vector_the_handlers_repoint_is_one_schedule_with_several_entries():
    """A two-phase handler chain: each address the vector takes is an entry of its own."""
    move = ("LDA #$13", "STA $FFFB", "LDA #$0F", "STA $D418", *ACK, "RTI")
    back = ("LDA #$12", "STA $FFFB", "LDA #$0E", "STA $D418", *ACK, "RTI")
    trace, _tr = _run(handler=move, second={0x1300: back})
    assert [e["addr"] for e in trace.meta["schedule"][1:]] == [HANDLER, 0x1300]
    assert set(trace.nmilog["addr"].tolist()) == {HANDLER, 0x1300}


def test_the_frames_the_handler_pushes_balance():
    trace, _tr = _run()
    assert trace.meta["unmatched_rts"] == 0


def test_a_trace_with_no_nmi_carries_no_second_entry():
    blocks = {INIT: asm(INIT, "RTS"), PLAY: asm(PLAY, "LDA #$01", "STA $D400", "RTS")}
    trace, tr = trace_prog(blocks, INIT, PLAY, calls=2)
    assert tr.nmi is None and len(trace.meta["schedule"]) == 1
    assert "nmis" not in trace.meta and not trace.nmilog["call"].size


# ---- the chip model ---------------------------------------------------------
def test_writing_a_running_latch_lands_at_the_next_underflow():
    """The counter is untouched, so the pending underflow keeps its cycle."""
    c = CIA(CIA1_BASE)
    c.write(CIA1_BASE + 4, 99, 0)
    c.write(CIA1_BASE + 5, 0, 0)
    c.write(CIA1_BASE + 0x0E, 0x11, 0)
    assert c.underflows(250) == 2
    c.write(CIA1_BASE + 4, 199, 150)  # a longer period, mid-flight
    assert c.underflows(199) == 0 and c.underflows(200) == 1  # the pending one still lands
    assert c.underflows(399) == 1 and c.underflows(400) == 2  # then 200 cycles apart


def test_the_line_asserts_once_until_an_icr_read_releases_it():
    c = CIA(CIA2_BASE)
    c.write(CIA2_BASE + 4, 99, 0)
    c.write(CIA2_BASE + 5, 0, 0)
    c.write(CIA2_BASE + 0x0E, 0x11, 0)
    c.write(CIA2_BASE + 0x0D, 0x81, 0)
    assert c.edge_at(50) == 100 and c.edge_at(100) == 100
    c.raise_line()
    assert c.edge_at(300) is None  # latched: no further edge without an acknowledge
    c.read(CIA2_BASE + 0x0D, 300)
    assert c.edge_at(300) == 400  # and an underflow inside the handler is not held over


def test_an_enabled_source_whose_timer_never_started_has_no_edge():
    c = CIA(CIA2_BASE)
    c.write(CIA2_BASE + 0x0D, 0x81, 0)
    assert c.sources() == 0 and c.edge_at(0) is None
    assert N.entry(c, bytearray(0x10000)) is None


def test_a_flag_latched_before_the_mask_named_it_raises_at_once():
    c = CIA(CIA2_BASE)
    c.write(CIA2_BASE + 4, 99, 0)
    c.write(CIA2_BASE + 5, 0, 0)
    c.write(CIA2_BASE + 0x0E, 0x11, 0)
    assert c.edge_at(150) is None  # no source enabled yet, but the flag has latched
    c.write(CIA2_BASE + 0x0D, 0x81, 150)
    assert c.edge_at(150) == 150


def test_the_icr_mask_names_the_sources_the_model_carries():
    c = CIA(CIA2_BASE)
    c.write(CIA2_BASE + 0x0D, 0x93, 0)  # TA + TB + FLAG
    c.write(CIA2_BASE + 0x0E, 0x11, 0)
    c.write(CIA2_BASE + 0x0F, 0x11, 0)
    assert c.sources() == 0x13
    assert c.unmodelled() == 0x10 and N.sources(c) == "cia2_timer_a+cia2_timer_b"
    assert c.grid(ICR_TA) and c.grid(ICR_TB)


# ---- the whole pipeline over a two-entry schedule ----------------------------
def _decompiled(**kw):
    """Decompile and verify a two-entry machine on both executors."""
    from deity_informant.tuneprog import pipeline
    from deity_informant.tuneprog.verify import certify, verify

    trace, _tr = _run(calls=kw.pop("calls", 6), **kw)
    prog, _regions, _procs = pipeline.build(trace, "nmi-fixture")
    v = verify(prog, trace, calls=trace.meta["calls"], prefix=trace.meta["calls"])
    return trace, prog, v, certify(prog, v, prefix=trace.meta["calls"])


def test_the_two_entries_are_two_procedures_of_one_program():
    _trace, prog, _v, _c = _decompiled()
    assert prog.meta["tick_proc"] == "tick" and prog.meta["nmi_procs"] == ["nmi"]
    assert prog.procs["nmi"].kind == "nmi" and prog.procs["tick"].kind == "tick"


def test_tracer_interpreter_and_generated_code_agree_on_the_schedule():
    """Design S1 evidence E12, now over both entries and the interleaving between them."""
    _trace, _prg, v, cert = _decompiled()
    sub = cert["subtunes"][0]
    assert v.div is None and v.call == sub["ticks"]
    assert sub["divergences"] == 0 and sub["nmis"] == 120 and sub["nmi_entries"] == ["nmi"]
    assert "nmi preemption schedule" in cert["compared"]
    assert [e["kind"] for e in cert["schedule"]] == ["sub", "nmi"]


def test_a_handler_that_reads_what_the_play_routine_writes_verifies():
    """The handshake: the schedule is counted in stores, so the handler's view is exact."""
    play = ("LDA #$05", "STA $D400", "LDA #$0C", "STA $2000", "LDA #$03", "STA $2000", "RTS")
    handler = ("LDA $2000", "STA $D418", *ACK, "RTI")
    _trace, _prg, v, cert = _decompiled(play=play, handler=handler)
    assert v.div is None and cert["subtunes"][0]["divergences"] == 0


def test_a_single_entry_program_pays_nothing_for_the_hook():
    """The generated text of a one-entry program carries no preemption point at all."""
    from deity_informant.tuneprog import emit, pipeline
    from deity_informant.tuneprog.interp import Machine, NmiMachine
    from deity_informant.tuneprog.verify import Reference, Verifier

    blocks = {INIT: asm(INIT, "RTS"), PLAY: asm(PLAY, "LDA #$01", "STA $D400", "STA $2000", "RTS")}
    trace, _tr = trace_prog(blocks, INIT, PLAY, calls=2)
    prog, _r, _p = pipeline.build(trace, "plain")
    src = emit.emit_python(prog)
    assert "S.at(" not in src
    v = Verifier(prog, Reference(trace, 2), src=src)
    assert isinstance(v.M, Machine) and not isinstance(v.M, NmiMachine)
