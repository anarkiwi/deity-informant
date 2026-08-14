"""The canonical call suite, asserted: one procedure, no call, return or stack access.

Two assertions per variant: the lift reproduces the SID write log today (a failure
there is a lifter bug, never a pin), and the docs/denotation-solve.md 8.4 invariant
holds. A shape it does not hold for is ``xfail(strict=True)`` naming its mechanism."""

import re

import pytest

import _callgen as G
from deity_informant import frameproc, frameprog, frameval
from deity_informant.lifter import OPS
from deity_informant.structured import DecompileError

XFAIL = {"strict": True}

_PIN = "call suite: "

M_BODY_LABEL = (
    "the inlined body binds a pc: some transfer may name it, and the ret the splice "
    "removes is what answers that transfer, so frameproc._Builder.splice keeps the "
    "`call $PC ret $PC { .. }` wrapper (bound_pcs); duplicating the shared tail per "
    "site is the removal"
)
M_TAIL_TRANSFER = (
    "the inlined body leaves by a goto, not by its own ret: it returns through the "
    "pushed word from wherever it landed, so the splice keeps the wrapper "
    "(frameproc.escapes) -- the tail's own text has to move to the site with it"
)
M_RET_WORD = (
    "the callee returns from a displacement it moved: it pulls the word the machine "
    "pushed and pushes one back, so frameproc.sp_balanced refuses the splice and the "
    "call, the interior ret and sp all stay (720_Degrees $C31D is the corpus shape)"
)
M_SELF_CALL = (
    "a recursive callee reaches a block whose terminator is a `jsr` -- its own call --"
    " so procpass.copies_ok refuses to place a copy per site and it stays a `sub` under"
    " a `pcall`; a tail self-call is a loop and a non-tail one a bounded unroll, and"
    " Plan has a form for neither"
)
M_SHARED_HANDLER = (
    "a computed call's arm a static JSR also names is a procedure of its own "
    "(render._dispatch_gates' static_subs, procpass's static_sites), so no site places "
    "its body: the arm stays bare and the site keeps `call (expr) ret $PC` plus "
    "`switch call { $PC .. }`; copying it at the static sites as well is the removal"
)
M_S_RELOCATED = (
    "a TXS from a computed value is an absolute write to sp, not a displacement, so "
    "_SpFlow cannot prove the frame stands where it entered (sp_unbalanced): sp stays "
    "a parameter and the page-one address it computed stays with it"
)
M_S_AS_VALUE = (
    "LAS reads sp as an ordinary operand, and rung (d0) destacks accesses, not reads "
    "of the register itself (sp_read): modelling S as a value is what removes this, "
    "and until then sp is a parameter of the procedure"
)
M_SP_LOOP_PUSH = (
    "a push inside a loop stands at no one displacement: the model's SP flow leaves "
    "the body's entry sp bot, so concretize_stack names no cell, rung (d0r) has no "
    "return slot to ask the value question of, and the pushes stay sp-relative stores"
)

MECHANISMS = (
    M_BODY_LABEL,
    M_TAIL_TRANSFER,
    M_RET_WORD,
    M_SELF_CALL,
    M_SHARED_HANDLER,
    M_S_RELOCATED,
    M_S_AS_VALUE,
    M_SP_LOOP_PUSH,
)

M_BRK_TERM = (
    "structured.Block has no terminator form for the lifter's `brk` control: the VM "
    "executes BRK (vm.py pushes pc+2 and P and vectors through $FFFE) but the block "
    "model raises DecompileError, so a BRK player has no lift to state an invariant of"
)

PINS = {
    ("shared-tail", "tone/X"): (M_BODY_LABEL, M_TAIL_TRANSFER),
    ("shared-tail", "tone/Y"): (M_BODY_LABEL, M_TAIL_TRANSFER),
    ("shared-tail", "alt/X"): (M_BODY_LABEL, M_TAIL_TRANSFER),
    ("shared-tail", "alt/Y"): (M_BODY_LABEL, M_TAIL_TRANSFER),
    ("arg-pass", "stack-pla"): (M_RET_WORD,),
    ("rts-trick", "loop"): (M_SP_LOOP_PUSH,),
    ("mixed-handler", "head/vt0"): (M_SHARED_HANDLER,),
    ("mixed-handler", "head/vt1"): (M_SHARED_HANDLER,),
    ("mixed-handler", "tail/vt0"): (M_SHARED_HANDLER,),
    ("mixed-handler", "tail/vt1"): (M_SHARED_HANDLER,),
    ("tail-recursion", "x/const"): (M_SELF_CALL,),
    ("tail-recursion", "x/row"): (M_SELF_CALL,),
    ("tail-recursion", "cell/const"): (M_SELF_CALL,),
    ("tail-recursion", "cell/row"): (M_SELF_CALL,),
    ("deep-recursion", "const/after"): (M_SELF_CALL,),
    ("deep-recursion", "const/around"): (M_SELF_CALL,),
    ("deep-recursion", "row/after"): (M_SELF_CALL,),
    ("deep-recursion", "row/around"): (M_SELF_CALL,),
    ("stack-move", "row/push"): (M_S_RELOCATED,),
    ("s-illegal", "las/page1"): (M_S_AS_VALUE,),
    ("s-illegal", "las/far"): (M_S_AS_VALUE,),
}

# what already holds, and must keep holding
CLEAN = (
    ("leaf-call", "head/sid"),
    ("leaf-call", "head/cell"),
    ("leaf-call", "mid/sid"),
    ("leaf-call", "mid/cell"),
    ("leaf-call", "tail/sid"),
    ("leaf-call", "tail/cell"),
    ("arm-liveout", "eor1"),
    ("arm-liveout", "eor3"),
    ("arm-liveout", "eor5"),
    ("multi-site", "2-site/X"),
    ("multi-site", "2-site/Y"),
    ("multi-site", "2-site/no-arg"),
    ("multi-site", "3-site/X"),
    ("multi-site", "3-site/Y"),
    ("multi-site", "3-site/no-arg"),
    ("two-callers", "X/sid"),
    ("two-callers", "X/cell"),
    ("two-callers", "Y/sid"),
    ("two-callers", "Y/cell"),
    ("two-callers", "cell/sid"),
    ("two-callers", "cell/cell"),
    ("branchy-callee", "cc/dey"),
    ("branchy-callee", "cc/iny"),
    ("branchy-callee", "cs/dey"),
    ("branchy-callee", "cs/iny"),
    ("nested", "X/tail"),
    ("nested", "X/post"),
    ("nested", "Y/tail"),
    ("nested", "Y/post"),
    ("arg-pass", "a"),
    ("arg-pass", "x"),
    ("arg-pass", "y"),
    ("arg-pass", "cell"),
    ("arg-pass", "stack-tsx"),
    ("ret-value", "a"),
    ("ret-value", "x"),
    ("ret-value", "y"),
    ("ret-value", "carry"),
    ("ret-value", "cell"),
    ("tail-call", "only"),
    ("tail-call", "shared"),
    ("tail-call", "cond"),
    ("vector-call", "jmpind"),
    ("vector-call", "smc-jmp"),
    ("vector-call", "smc-jsr"),
    ("dispatch-share", "same/tight"),
    ("dispatch-share", "same/gap"),
    ("dispatch-share", "skew/tight"),
    ("dispatch-share", "skew/gap"),
    ("flag-record", "plp/tight"),
    ("flag-record", "plp/across"),
    ("flag-record", "pla/tight"),
    ("flag-record", "pla/across"),
    ("stack-move", "const/push"),
    ("stack-move", "const/cell"),
    ("stack-move", "row/cell"),
    ("s-illegal", "tas/page1"),
    ("s-illegal", "tas/far"),
    ("page-one-cell", "abs/same"),
    ("page-one-cell", "abs/cross"),
    ("page-one-cell", "absx/same"),
    ("page-one-cell", "absx/cross"),
    ("page-one-cell", "absy/same"),
    ("page-one-cell", "absy/cross"),
    ("page-one-cell", "indy/same"),
    ("page-one-cell", "indy/cross"),
    ("rts-trick", "const"),
    ("rts-trick", "arith"),
    ("rts-trick", "two-arm"),
    ("rts-trick", "table"),
    ("rts-trick", "ptr"),
    ("rts-trick", "open"),
    ("irq-frame", "hw"),
    ("irq-frame", "cinv"),
)

SPLITS = {
    "arg-pass": (M_RET_WORD,),
    "rts-trick": (M_SP_LOOP_PUSH,),
    "stack-move": (M_S_RELOCATED,),
    "s-illegal": (M_S_AS_VALUE,),
}

# the protected-region fault a variant's text raises, at the cell it concretely reaches
FAULTS = {
    ("arg-pass", "stack-pla"): "store into the stack page $01FC",
}

BINDS_PC = (  # a dispatch arm, an RTS-trick landing, and a tail two inlined bodies share
    ("vector-call", "jmpind"),
    ("vector-call", "smc-jmp"),
    ("arm-liveout", "eor1"),
    ("arm-liveout", "eor3"),
    ("arm-liveout", "eor5"),
    ("shared-tail", "tone/X"),
    ("shared-tail", "tone/Y"),
    ("shared-tail", "alt/X"),
    ("shared-tail", "alt/Y"),
) + tuple(("rts-trick", v.label) for v in G.BY_ROW["rts-trick"].variants)


def _variants():
    """Every (row, variant), pinned where a named mechanism is known to break it."""
    out = []
    for row, label in G.ALL:
        got = PINS.get((row, label))
        marks = [pytest.mark.xfail(reason=_PIN + " | ".join(got), **XFAIL)] if got else []
        out.append(pytest.param(row, label, marks=marks, id="%s:%s" % (row, label)))
    return out


def _shapes():
    """Every shape, pinned where its spellings are known to break differently."""
    out = []
    for s in G.SHAPES:
        if not s.lifts:
            continue
        got = SPLITS.get(s.row)
        marks = [pytest.mark.xfail(reason=_PIN + " | ".join(got), **XFAIL)] if got else []
        out.append(pytest.param(s.row, marks=marks, id=s.row))
    return out


@pytest.mark.parametrize("row,label", [pytest.param(r, l, id="%s:%s" % (r, l)) for r, l in G.ALL])
def test_variant_reproduces_the_write_log(row, label):
    """The lift is correct today: no pin may hide a divergence here (spec 1.4).

    A variant whose text names a protected region has no write log to reproduce: it
    faults, and the fault is asserted at the cell rather than silenced."""
    model, prog = G.built(row, label)
    want = FAULTS.get((row, label))
    if want is None:
        assert frameval.gate_fp(model, G.FRAMES, prog) is None
        return
    with pytest.raises(frameval.FrameFault, match=re.escape(want)):
        frameval.gate_fp(model, G.FRAMES, prog)


@pytest.mark.parametrize("label", [v.label for v in G.BY_ROW["irq-frame"].variants])
def test_the_artifact_carries_the_convention_it_was_entered_over(label):
    """3a for a handler frame: the entry convention is a header fact, not lifted text.

    Nothing in the image says how many bytes were pushed below the handler, so the
    text states it and the rebuild re-derives the same program from the text alone."""
    text = G.text("irq-frame", label)
    prog = frameprog.loads(text)
    assert prog.entry_frame == (6 if label == "cinv" else 3)
    rebuilt = frameprog.block_model(prog)
    assert frameprog.dumps(frameprog.program(rebuilt)) == text


@pytest.mark.parametrize("label", [v.label for v in G.BY_ROW["irq-frame"].variants])
def test_the_frame_the_artifact_cuts_is_the_machine_s_own(label):
    """Where a frame starts moved; which writes fall in it did not (spec 1.4).

    The raw 6510 takes one interrupt per frame and the artifact runs one procedure
    per frame: the two write lists agree frame by frame, so no write crossed into a
    neighbour. The driver writes either side of its cursor bump, so a shift shows."""
    want = G.machine_frames("irq-frame", label)
    trace, _walker = frameprog.iota(G.built("irq-frame", label)[0], G.FRAMES)
    got = frameval.Evaluator(G.parsed("irq-frame", label), trace).frames(G.FRAMES)
    assert got == want
    assert len({tuple(f) for f in want}) > 1, "a constant log proves no alignment"


def test_the_faulting_variants_are_exactly_the_ones_the_protection_names():
    """The fault table is read off the evaluator, so a shape may not fault unnamed."""
    got = {v: G.fault(*v)[0].split(": ", 1)[1] for v in G.ALL if G.fault(*v)}
    assert got == FAULTS
    assert set(FAULTS) <= set(PINS), "a faulting variant with no mechanism named"


@pytest.mark.parametrize("row,label", [pytest.param(r, l, id="%s:%s" % (r, l)) for r, l in G.ALL])
def test_the_gate_judges_the_artifact_and_nothing_else(row, label):
    """One object: the program the gate evaluates is the text, read back and canonical."""
    assert frameprog.dumps(G.parsed(row, label)) == G.text(row, label)


@pytest.mark.parametrize("row,label", _variants())
def test_variant_is_one_call_free_procedure(row, label):
    """The removal's invariant, machine-checked off the parsed text (8.4)."""
    assert not G.violations(row, label)


@pytest.mark.parametrize("row", _shapes())
def test_shape_breaks_one_way_under_every_spelling(row):
    """The property the suite exists for: the mechanism is the shape's, not the spelling's."""
    got = {v.label: G.kinds(row, v.label) for v in G.BY_ROW[row].variants}
    assert len(set(got.values())) == 1, sorted(set(got.values()))


@pytest.mark.parametrize("label", [v.label for v in G.BY_ROW["arm-liveout"].variants])
def test_a_dispatch_arm_is_no_break_level_and_keeps_what_the_merge_reads(label):
    """A `switch goto` arm's `break` names the enclosing loop, not the switch.

    ``frameval._arms`` hands the arms the context it was given, so a liveness pushing
    a level per arm reads the arm's own continuation for the loop's exit and prunes
    the definition the text after the merge reads."""
    arms = [b for _l, b in _swg_arms(G.parsed("arm-liveout", label).procs[0][3])]
    assert len(arms) == 2
    assert all(any(s[0] == "asg" and s[1] == "x" for s in b) for b in arms), arms


def _swg_arms(stmts, out=None):
    """Every ``switch goto`` case body a statement list carries."""
    out = [] if out is None else out
    for s in stmts:
        if s[0] == "swg":
            out += list(s[1])
        for b in frameproc._stmt_bodies(s):
            _swg_arms(b, out)
    return out


def test_the_invariant_holds_exactly_where_no_pin_names_a_mechanism():
    """Binary, not a percentage: the pins and the clean set partition the suite."""
    held = {v for v in G.ALL if not G.violations(*v)}
    assert held == set(CLEAN)
    assert set(G.ALL) - set(PINS) == set(CLEAN)


def test_no_called_body_binds_a_pc_a_copy_would_collide_on():
    """Label identity is no obstacle to duplication: a callee's own text binds no pc."""
    bad = {}
    for row, label in G.ALL:
        for entry, _p, _r, stmts in G.parsed(row, label).procs[1:]:
            if G.pcs_bound(stmts):
                bad["%s:%s" % (row, label)] = (entry, G.pcs_bound(stmts))
    assert not bad


def test_the_bound_pcs_are_the_dispatch_arms_and_the_shared_tail():
    """Where context-qualified pcs would be needed: a fold the structurer could not do."""
    got = {v for v in G.ALL if any(G.pcs_bound(s) for _e, _p, _r, s in G.parsed(*v).procs)}
    assert got == set(BINDS_PC)


def test_every_pin_names_a_live_mechanism():
    """The deferral discipline: a pin states what is broken, so its fix flips it."""
    named = set()
    for got in list(PINS.values()) + list(SPLITS.values()):
        assert all(m in MECHANISMS for m in got)
        named.update(got)
    assert named == set(MECHANISMS), "a mechanism no pin names is dead text"
    assert set(PINS) <= set(G.ALL), "a pin naming a variant the generator does not carry"
    assert set(SPLITS) <= {s.row for s in G.SHAPES}


def test_every_shape_carries_more_than_one_spelling():
    """A shape with one variant proves nothing about invariance, so none may have one."""
    assert not [s.row for s in G.SHAPES if len(s.variants) < 2]
    assert len(G.ALL) == len(set(G.ALL))


def test_the_suite_is_generated_not_sampled():
    """No randomness anywhere: one variant assembles to one image, every build."""
    for row, label in G.EVERY[::7]:
        assert G.image(row, label)[0] == G.image(row, label)[0]
    assert all(len(s.variants) <= G.CAP for s in G.SHAPES)


def test_every_opcode_that_touches_the_stack_has_a_driver():
    """The closure the suite exists to keep: the opcode table names the obligation.

    A new stack-touching opcode upstream fails here until a driver encodes it, which
    is why the covered set is read off the assembled bytes and never annotated."""
    missing = {"%s $%02X" % (OPS[op][0], op) for op in G.STACK_OPCODES - G.COVERED}
    assert not missing


def test_the_stack_set_is_the_lift_s_own_and_is_named_per_mnemonic():
    """The predicate tracks the lifter: a finding is sp, a page-one access or stk.

    It is also mnemonic-closed -- every encoding of a stack mnemonic touches the
    stack -- so naming the set by mnemonic loses nothing the table says."""
    assert G.STACK_OPCODES == {op for op, (mn, _md) in OPS.items() if mn in G.STACK_MNEMONICS}
    assert all(G.touches(op) for op in G.STACK_OPCODES)
    assert not any(G.touches(op) for op in set(OPS) - G.STACK_OPCODES)
    plain = {op: G.touches(op) - {"page1"} for op in OPS}
    assert plain == {op: G.touches(op, 0x0100) - {"page1"} for op in OPS}


def test_every_mode_that_can_address_page_one_has_a_driver_that_does():
    """The rest of the stack: page one reached by an operand, with no stack opcode.

    An indirect mode's base is a LOAD the lift leaves symbolic, so no operand makes
    such an access provably page one -- that case is pinned as blind, not covered."""
    assert G.PAGE_ONE_MODES <= G.PAGE_ONE_COVERED
    assert G.PAGE_ONE_MODES == {"abs", "absx", "absy"}
    assert not G.PAGE_ONE_MODES & {"indx", "indy", "zp", "zpx", "zpy"}


@pytest.mark.parametrize("label", [v.label for v in G.BY_ROW["brk-vector"].variants])
def test_brk_is_the_one_stack_opcode_no_lift_expresses(label):
    """Pinned, not faked: the driver assembles and runs, and the model refuses it."""
    assert 0x00 in {op for op, _w in G.instructions("brk-vector", label)}
    with pytest.raises(DecompileError, match="control 'brk'"):
        G.built("brk-vector", label)


def test_the_unliftable_shapes_are_exactly_the_ones_a_mechanism_names():
    """A shape kept out of the invariant suite states why, so its fix puts it back."""
    assert {s.row for s in G.SHAPES if not s.lifts} == {"brk-vector"}
    assert "brk" in M_BRK_TERM
