"""S4 stack elimination: pushes and pops as values, what stays residual, its horizon.

Every case runs both programs -- the one that keeps the machine stack and the one
that eliminated it -- against the trace on both executors, so a slot forwarded
wrongly is a divergence and not a printed-text difference.
"""

import json
import re

from deity_informant.tuneprog import frames, idioms, live, pipeline, stack, structure
from deity_informant.tuneprog.frame import frames as name_frames
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Const,
    Let,
    Load,
    Proc,
    Return,
    STACK_HI,
    STACK_LO,
    Store,
    Tuneprog,
    Var,
    enc,
)
from deity_informant.tuneprog.irwalk import defs_of, node_loads, stmt_uses, term_uses
from deity_informant.tuneprog.verify import verify

from _asm import asm, psid
from _prog import PLAY, printed as _text, stack_access as _accesses, tuneprog

SP = frames.SP
SPREG = frames.SPREG
PEEK = (
    "play: LDA cnt",
    "PHA",
    "JSR peek",
    "PLA",
    "STA $D400",
    "INC cnt",
    "RTS",
    "peek: TSX",
    "LDA $0103,X",
    "STA $D404",
    "RTS",
    "cnt: BRK",
)


def _sp(prog):
    return [n for n, p in prog.procs.items() if SPREG in p.params or SPREG in p.rets]


def both(code, calls=8, **kw):
    """``(trace, kept, eliminated)`` -- the same snippet with and without the stack."""
    trace, kept = tuneprog(code, calls=calls, s4=True, stack=False, **kw)
    _t, gone = tuneprog(code, calls=calls, s4=True, **kw)
    for prog in (kept, gone):
        v = verify(prog, trace, calls=calls, prefix=calls)
        assert v.div is None, (prog.meta.get("stack"), v.div)
    return trace, kept, gone


def eliminated(code, calls=8, **kw):
    """The eliminated program of a snippet, checked to have no machine stack left."""
    _t, _kept, gone = both(code, calls=calls, **kw)
    assert gone.meta["stack"] == "eliminated"
    assert not _accesses(gone) and not _sp(gone)
    assert not [r for r in gone.storage if STACK_LO <= r.base <= STACK_HI]
    return gone


def residual(code, calls=8, **kw):
    """The program of a snippet whose stack the analysis refuses to prove away."""
    _t, kept, gone = both(code, calls=calls, **kw)
    assert gone.meta["stack"]["procs"], gone.meta["stack"]
    assert _accesses(gone) or _sp(gone)
    for name, p in gone.procs.items():  # residual: nothing was rewritten
        assert enc(p) == enc(kept.procs[name])
    return gone


def _slots(prog):
    return {
        s.n
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Let and s.n.startswith(stack.SLOT)
    }


def test_a_push_and_its_pop_are_one_value_across_a_call():
    # the callee's frame lies below the caller's slot, so the call cannot touch it
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "JSR work",
        "PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "work: LDA #$07",
        "STA $D404",
        "RTS",
        "cnt: BRK",
    )
    eliminated(code)
    doc = _text(code)
    assert not re.search(r"\bsp\d*\b", doc), doc
    assert "saved = " in doc and "sid[0].freq_lo = saved" in doc


def test_a_call_three_deep_still_pops_what_it_pushed():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "JSR one",
        "PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "one: JSR two",
        "RTS",
        "two: JSR three",
        "RTS",
        "three: LDA #$07",
        "STA $D404",
        "RTS",
        "cnt: BRK",
    )
    prog = eliminated(code)
    assert len(prog.procs) == 5


def test_two_pushes_one_pop_reads_is_one_value():
    # the pop is a phi: both arms wrote the slot, so both name the same value
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "AND #$03",
        "BEQ zero",
        "LDA #$11",
        "PHA",
        "JMP join",
        "zero: LDA #$22",
        "PHA",
        "join: PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    prog = eliminated(code)
    assert len(_slots(prog)) == 1  # one name, two definitions
    doc = _text(code)
    assert not re.search(r"\bsp\d*\b", doc) and "sid[0].freq_lo = saved" in doc


def test_two_unrelated_slots_do_not_share_a_name():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "PLA",
        "STA $D400",
        "LDA #$09",
        "PHA",
        "PLA",
        "STA $D404",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    prog = eliminated(code)
    doc = _text(code)
    assert "sid[0].freq_lo = freq_lo" in doc and "sid[0].ctrl = 9" in doc
    assert len(_slots(prog)) <= 1  # each pop reads the value its own push held


def test_a_push_and_a_pop_in_one_loop_iteration_are_one_value():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDX #$02",
        "lp: TXA",
        "PHA",
        "STA $D404",
        "PLA",
        "TAX",
        "DEX",
        "BPL lp",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    eliminated(code)
    assert not re.search(r"\bsp\d*\b", _text(code))


def test_a_push_inside_a_loop_keeps_the_stack_pointer():
    # a scratch *area*: the pointer is not a constant offset, so no slot is named
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
    prog = residual(code)
    assert prog.meta["stack"]["depth"] == "unknown"  # the pointer is not a slot here
    assert "sp" in _text(code)


def test_a_frame_another_procedure_reads_keeps_the_stack():
    # `TSX; LDA $0103,X` reads the caller's slot: no push in the program may go
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        *PEEK,
    )
    prog = residual(code)
    assert len(prog.meta["stack"]["procs"]) == 1  # the reader is the one that cannot
    doc = _text(code)
    assert "saved = counter" in doc and "sid[0].freq_lo = saved" in doc


def test_a_stack_pointer_read_as_data_keeps_the_stack():
    # `TSX; STX $D404` is the pointer itself, not a frame: it has to stay a value
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: TSX",
        "STX $D404",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    prog = residual(code)
    assert prog.meta["stack"]["depth"] == 0  # the pointer is data, but no frame is used


def test_a_php_plp_round_trip_leaves_the_carry_and_drops_the_flag_byte():
    # the anatomy's 11-bit cutoff idiom: PHP over an intervening AND, PLP for the ADC
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "STA hi",
        "RTS",
        "play: LDA cnt",
        "CLC",
        "ADC #$40",
        "PHP",
        "AND #$07",
        "STA $D415",
        "LDA hi",
        "PLP",
        "ADC #$00",
        "STA hi",
        "STA $D416",
        "INC cnt",
        "RTS",
        "hi: BRK",
        "cnt: BRK",
    )
    eliminated(code)
    doc = _text(code)
    assert not re.search(r"\bsp\d*\b", doc), doc
    assert not re.search(r"\b[icdvnz]\d*\b = ", doc), doc  # no flag byte, no flag copies
    assert re.search(r"\$?saved_b0 = carry\(", doc), doc
    assert "cutoff_hi += saved_b0" in doc, doc


def test_a_php_plp_pair_a_branch_apart_is_still_one_value():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "CLC",
        "ADC #$40",
        "PHP",
        "AND #$03",
        "BEQ skip",
        "STA $D404",
        "skip: PLP",
        "LDA #$00",
        "ADC #$00",
        "STA $D400",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    eliminated(code)
    assert not re.search(r"\bsp\d*\b", _text(code))


def test_the_rts_trick_switches_on_the_values_it_pushed():
    # `PHA PHA RTS` is a computed jump: the selector reads the pushed halves
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA #>go-1",
        "PHA",
        "LDA #<go-1",
        "PHA",
        "RTS",
        "go: LDA #$07",
        "STA $D404",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    prog = eliminated(code)
    sw = [b.term for p in prog.procs.values() for b in p.blocks.values() if hasattr(b.term, "e")]
    assert sw and not any(type(x) is Load for t in sw for x in node_loads(t))


def test_the_certificate_field_names_the_procedures_that_kept_a_stack():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: TSX",
        "STX $D404",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    _t, prog = tuneprog(code, calls=4, s4=True)
    assert sorted(prog.meta["stack"]) == ["depth", "procs"]
    assert prog.meta["stack"]["procs"] == ["tick"]


def test_the_view_still_names_the_frames_of_a_residual_program():
    """:mod:`~deity_informant.tuneprog.frame` names what the elimination left behind."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        *PEEK,
    )
    _t, prog = tuneprog(code, calls=6, s4=True)
    view = structure.view(prog, live.needed(prog)[0])
    assert name_frames(view, frames.deltas(prog)) == 1  # the pair is named, the push stays
    assert [s for s in _accesses(view) if type(s) is Store]


def test_the_copy_fold_terminates_on_a_cycle_of_copies():
    """``A = B`` and ``B = A``, each the only definition, must not chase forever."""
    blk = Block("b0", [Let("A#1", Var("B#1")), Let("B#1", Var("A#1"))], Return((Var("A#1"),)))
    proc = Proc("p", (), (0,), {"b0": blk}, "b0")
    stack._copies(proc)
    assert [type(s.e) for s in proc.blocks["b0"].stmts] == [Var, Var]


def test_a_bit_of_a_packed_value_folds_to_the_value_that_packed_it():
    defs = {"p": [_pack()]}
    assert idioms.bit(Var("p"), 2, defs) == Var("I")
    assert idioms.bit(Var("p"), 0, defs) == Var("C")
    assert idioms.bit(Var("p"), 5, defs).v == 1  # the unused bit PHP always sets
    assert idioms.bit(Var("x"), 0, {}) is None


def _pack():
    """The byte ``PHP`` pushes: ``$30 | C | Z<<1 | I<<2 | D<<3 | V<<6 | N<<7``."""
    from deity_informant.tuneprog.ir import Bin, Const

    out = Const(0x30, 1)
    for name, sh in (("C", 0), ("Z", 1), ("I", 2), ("D", 3), ("V", 6), ("N", 7)):
        f = Var(name, 1)
        out = Bin("|", out, f if not sh else Bin("<<", f, Const(sh, 1), 1), 1)
    return out


def test_a_residual_program_claims_periodicity_on_the_whole_footprint():
    """The scratch byte is the only state here: leaving the page out would say period 1."""
    code = asm(
        PLAY,
        "init: RTS",
        "play: TSX",
        "LDA $0100,X",
        "CLC",
        "ADC #$01",
        "STA $0100,X",
        "STA $D400",
        "RTS",
    )
    trace, prog = tuneprog(code, calls=600, s4=True)
    assert prog.meta["stack"]["procs"] == ["tick"]
    assert (trace.meta["period_free"], trace.meta["first_repeat_free"]) == (1, 1)
    assert (trace.meta["period"], trace.meta["first_repeat"]) == (256, 256)
    v = verify(prog, trace, calls=600)
    sub = v.subtune()
    assert v.div is None and not v.free
    assert (sub["period"], sub["trace_period"], sub["complete"]) == (256, 256, True)


def test_an_eliminated_program_claims_periodicity_without_the_page():
    """Its pushes are values, so the page it no longer writes is out of the footprint."""
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "cnt: BRK",
    )
    trace, prog = tuneprog(code, calls=600, s4=True)
    assert prog.meta["stack"] == "eliminated"
    assert [a for a in trace.written_play if STACK_LO <= a <= STACK_HI]  # the trace pushed
    v = verify(prog, trace, calls=600)
    sub = v.subtune()
    assert v.div is None and v.free
    assert sub["trace_period"] == trace.meta["period_free"] == 256
    assert (sub["period"], sub["complete"]) == (256, True)


def test_an_eliminated_program_keeps_no_stack_pointer_anywhere():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "PHA",
        "JSR work",
        "PLA",
        "STA $D400",
        "INC cnt",
        "RTS",
        "work: LDA #$07",
        "STA $D404",
        "RTS",
        "cnt: BRK",
    )
    prog = eliminated(code)
    names = set()
    for p in prog.procs.values():
        for b in p.blocks.values():
            for st in b.stmts:
                stmt_uses(st, names)
                names.update(defs_of(st))
            term_uses(b.term, names)
    assert not [n for n in names if n.split("#")[0] == frames.SP]


# ---- the interrupt frame the machine pushes: the tick's contract -------------
def _handler(*lines):
    """A tick entered as an installed IRQ handler: the machine's frame, then ``RTI``."""
    return asm(PLAY, "init: LDA #$00", "STA cnt", "RTS", "play:", *lines, "RTI", "cnt: BRK")


def test_an_rti_ticks_entry_frame_is_the_value_its_rti_consumes():
    code = _handler("PHA", "TXA", "PHA", "INC cnt", "LDA cnt", "STA $D400", "PLA", "TAX", "PLA")
    prog = eliminated(code, calls=6, kind="irq")
    assert prog.meta["stack"] == "eliminated"
    doc = _text(code, calls=6, kind="irq")
    assert not re.search(r"\bsp\d*\b", doc), doc
    assert not re.search(r"\b[icdvnz]\d*\b = ", doc), doc


def test_a_tick_that_reads_its_entry_frame_through_tsx_keeps_the_stack():
    # `TSX; LDA $0101,X` is the pushed status by another route: no slot, no value
    code = _handler("TSX", "LDA $0101,X", "AND #$01", "STA $D400", "INC cnt")
    prog = residual(code, calls=6, kind="irq")
    assert prog.meta["stack"] == {"depth": "unknown", "procs": ["tick"]}


def test_a_tick_that_pops_past_its_entry_status_keeps_the_stack():
    """The pushed return address is named by nothing, so reading it is no value.

    This tick puts the frame it took apart back together, and is residual anyway.
    """
    code = _handler("PLA", "TAY", "PLA", "PHA", "TYA", "PHA", "INC cnt", "LDA cnt", "STA $D400")
    prog = residual(code, calls=6, kind="irq")
    assert prog.meta["stack"]["procs"] == ["tick"]


def _rti_proc(slot, kind="irq"):
    """A one-block tick whose ``RTI`` reads the entry frame at ``slot``."""
    a = Bin("|", Const(STACK_LO, 2), Bin("+", Var(SP), Const(slot), 1), 2)
    blk = Block("b0", [Let("p", Load("ram", a, 1, STACK_LO, STACK_HI, -1))], Return())
    proc = Proc("tick", (SPREG,), (), {"b0": blk}, "b0", "tick")
    meta = {"tick_proc": "tick", "entry": {"kind": kind}}
    return Tuneprog(meta=meta, storage=[], inputs=[], procs={"tick": proc})


def test_the_entry_contract_covers_the_status_slot_and_nothing_else():
    """Only the byte the machine pushed as ``P`` is a value: the pc bytes are not."""
    assert sorted(frames.contract(_rti_proc(1), "tick")) == [frames.STATUS_SLOT]
    assert frames.contract(_rti_proc(1, kind="sub"), "tick") == {}
    assert frames.contract(_rti_proc(1), "init") == {}
    ok = frames.analyse(_rti_proc(1))["tick"]
    assert not ok.opaque and len(ok.plan) == 1 and not ok.foreign
    for slot in (0, 2, 3):  # a pop at another depth, and the pushed return address
        assert frames.analyse(_rti_proc(slot))["tick"].opaque, slot


SCRATCH = (
    "init: RTS",
    "play: TSX",
    "LDA $0100,X",
    "CLC",
    "ADC #$01",
    "STA $0100,X",
    "STA $D400",
    "RTS",
)


def test_until_period_traces_a_residual_program_to_the_page_inclusive_repeat(tmp_path):
    """S4 decides the footprint, so the horizon it stopped on may be the wrong one.

    Page-free this tune repeats every tick; its scratch byte repeats every 256.
    """
    out = tmp_path / "o"
    sid = tmp_path / "scratch.sid"
    code = asm(PLAY, *SCRATCH)
    sid.write_bytes(psid({PLAY: code}, init=code.labels["init"], play=code.labels["play"]))
    argv = [str(sid), "--out", str(out), "--until-period", "--max-calls", "1000"]
    assert pipeline.main(argv + ["--chunk", "8", "--no-text", "--prefix", "0"]) == 0
    doc = json.loads((out / "certificate.json").read_text())
    sub = doc["subtunes"][0]
    assert doc["stack"]["procs"] == ["tick"]
    assert (sub["period"], sub["trace_period"], sub["first_repeat"]) == (256, 256, 256)
    assert sub["ticks"] == 257 and sub["complete"] and sub["divergences"] == 0
    assert json.loads((out / "state.json").read_text())["stack"] == "residual"
