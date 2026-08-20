"""S2b static closure: the untaken branch directions the post-init image states."""

import pytest

from deity_informant.tuneprog import closure, pipeline, printer
from deity_informant.tuneprog.cfg import build_procs
from deity_informant.tuneprog.ir import Load, Trap
from deity_informant.tuneprog.irwalk import node_exprs, walk
from deity_informant.tuneprog.lift import lift_trace
from deity_informant.tuneprog.regions import build_regions
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, front, proc_body as body

CALLS = 4
ARM = ("BEQ dead", "LDA #$07", "STA $D400", "join: RTS")


def guarded(*lines):
    """A tune whose branch tests a cell the tick writes, so S4 cannot fold the arm away.

    ``st`` counts up from zero, so ``BEQ dead`` never fires and ``dead`` is the
    direction the trace never took.
    """
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA st",
        "RTS",
        "play: INC st",
        "LDA st",
        *lines,
        "st: BRK",
    )


def built(code, static=True, calls=CALLS, **kw):
    """``(Trace, Tuneprog)`` of a snippet through the certified pipeline."""
    trace = front(code, calls=calls, **kw)[0]
    return trace, pipeline.build(trace, "snippet", static=static)[0]


def traps(prog, why):
    return [
        b
        for p in prog.procs.values()
        for b in p.blocks.values()
        if type(b.term) is Trap and b.term.why == why
    ]


def joining():
    """The untaken arm is real code that rejoins the executed path at ``join``."""
    return guarded(*ARM, "dead: LDA #$09", "STA $D401", "JMP join")


def test_an_untaken_direction_closes_into_code_that_joins_the_executed_path():
    trace, prog = built(joining())
    rep = closure.report(prog)
    assert rep["arms"] == 1 and rep["closed"] == 1 and rep["instructions"] == 3
    assert rep["untaken"] == 0 and rep["frontier"] == 0 and rep["stops"] == {}
    assert rep["blocks"] and rep["statements"] and rep["verified_statements"]
    assert verify(prog, trace, calls=CALLS).div is None


def test_without_the_closure_the_same_arm_is_still_a_trap():
    """The default product is trace-closed, and says nothing about a walk it never ran."""
    trace, prog = built(joining(), static=False)
    assert len(traps(prog, "untaken")) == 1
    assert closure.report(prog) == {}  # no closure block in the certificate
    assert verify(prog, trace, calls=CALLS).div is None


STOPS = {
    # the arm's head is the operand byte the tick patches: a writer states it, not the image
    "smc_cell": ("BEQ alive+1", "alive: LDA #$60", "STA $D400", "STA alive+1", "RTS"),
    "jmpind": (*ARM, "dead: JMP (vec)", "vec: BRK", "BRK"),
    "foreign_jsr": (*ARM, "dead: JSR sub", "JMP join", "sub: RTS"),
    "stack_push": (*ARM, "dead: PHA", "JMP join"),
    # a pointer dereference spans the address space, and the stack page is in it
    "stack_pointer": (*ARM, "dead: LDA ($FB),Y", "JMP join"),
    "brk": (*ARM, "dead: BRK"),
    "jam": (*ARM, "dead: JAM"),
}


@pytest.mark.parametrize("case", sorted(STOPS))
def test_the_walk_stops_where_the_image_does_not_state_the_answer(case):
    trace, prog = built(guarded(*STOPS[case]))
    rep = closure.report(prog)
    why = "stack" if case.startswith("stack") else case
    assert rep["stops"] == {why: 1}, rep
    assert rep["closed"] == 0 and rep["blocks"] == 0
    assert len(traps(prog, "untaken")) == 1
    assert verify(prog, trace, calls=CALLS).div is None


def test_a_stop_inside_a_closed_arm_leaves_an_unstated_frontier():
    code = guarded(*ARM, "dead: LDA #$09", "STA $D401", "PHA", "JMP join")
    trace, prog = built(code)
    rep = closure.report(prog)
    assert rep["closed"] == 1 and rep["stops"] == {"stack": 1}
    assert rep["untaken"] == 0 and rep["frontier"] == 1
    assert verify(prog, trace, calls=CALLS).div is None


def test_a_closed_arm_may_call_a_procedure_the_trace_already_entered():
    code = guarded(
        "BEQ dead",
        "JSR beep",
        "join: RTS",
        "dead: JSR beep",
        "JMP join",
        "beep: LDA #$07",
        "STA $D400",
        "RTS",
    )
    trace, prog = built(code)
    rep = closure.report(prog)
    assert rep["closed"] == 1 and rep["untaken"] == 0 and not rep["stops"]
    assert verify(prog, trace, calls=CALLS).div is None


def test_a_closed_block_never_claims_an_execution():
    """A closed mark on executed code would be a false unverified claim."""
    _trace, prog = built(joining())
    shut = 0
    for p in prog.procs.values():
        closed = closure.closed_blocks(p)
        shut += len(closed)
        for lbl, b in p.blocks.items():
            assert not (b.closed and (b.count or any(b.cover)))
            assert lbl not in closed or not b.count
    assert shut


def test_the_printed_text_marks_every_closed_statement():
    _trace, prog = built(joining())
    view, st, names = pipeline.present(prog)
    text = printer.render(view, st, names, pcs=False)
    lines = [ln for ln in body(text, "tick") if ln.strip()]
    mark = "# unverified (static closure)"
    marked = [ln for ln in lines if mark in ln]
    assert marked and len(marked) < len(lines), text
    assert all("$D400" not in ln for ln in marked), marked  # the executed store is not marked


def test_closing_changes_no_executed_behaviour():
    runs = [built(joining(), static=s) for s in (False, True)]
    for trace, prog in runs:
        v = verify(prog, trace, calls=CALLS, prefix=CALLS)
        assert v.div is None and v.call == CALLS
    assert runs[0][1].meta["stack"] == runs[1][1].meta["stack"] == "eliminated"


def test_a_closed_access_carries_the_envelope_the_image_states():
    """``chk`` says the trace never saw it; the envelope still comes from the image."""
    code = guarded(*ARM, "dead: LDA tab,X", "STA $D401", "JMP join", "tab: BRK")
    _trace, prog = built(code)
    tab = code.labels["tab"]
    envs = {
        (x.cls, x.lo, x.hi)
        for p in prog.procs.values()
        for lbl in closure.closed_blocks(p)
        for s in p.blocks[lbl].stmts
        for e in node_exprs(s)
        for x in walk(e)
        if type(x) is Load
    }
    assert ("chk", tab, tab + 0xFF) in envs, envs


def test_only_a_synthesised_site_carries_the_closed_stamp():
    """A zero-count site is closed code only where the closure wrote it."""
    trace, _prog = built(joining())
    stamped = {k for k, s in trace.sites.items() if s.get("closed")}
    assert stamped and all(not trace.sites[k]["count"] for k in stamped)
    for s in trace.sites.values():
        s["count"] = 0  # a site nothing ran is still not the image's statement
    lifted = lift_trace(trace)
    procs = build_procs(trace, lifted, build_regions(trace, lifted))
    shut = {n["pc"] for p in procs.values() for n in p.nodes.values() if n["closed"]}
    assert shut == {trace.sites[k]["pc"] for k in stamped}
