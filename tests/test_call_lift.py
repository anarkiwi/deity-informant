"""The canonical call suite, asserted: one procedure, no call, return or stack access.

Two assertions per variant: the lift reproduces the SID write log today (a failure
there is a lifter bug, never a pin), and the docs/denotation-solve.md 8.4 invariant
holds. A shape it does not hold for is ``xfail(strict=True)`` naming its mechanism."""

import re

import pytest

import _callgen as G
from deity_informant import frameprog, frameval
from deity_informant.lifter import OPS
from deity_informant.structured import DecompileError

XFAIL = {"strict": True}

_PIN = "call suite: "

M_CALLB = (
    "sole-site inline keeps its wrapper: procpass.Plan.inline proves the callee "
    "dominated by its one static site and render nests the body there, but the form "
    "emitted is still `call $PC ret $PC { .. }` -- the call statement and the callee's "
    "own ret both survive inside the one procedure"
)
M_SHARED_SUB = (
    "two or more static sites are not inlinable as the plan is written: Plan.inline is "
    "a {callee: sole site} map and cannot name two, so the callee stays a `sub` and "
    "every site stays a `pcall`; duplicating the body is the removal, and it is exact "
    "where the body binds no pc"
)
M_SELF_CALL = (
    "a self-call is one of its own callee's static sites, so the callee has two and "
    "stays a sub; a tail self-call is a loop and a non-tail one a bounded unroll, and "
    "Plan has a form for neither"
)
M_MULTI_EXIT = (
    "the machine's several returns are several ret statements: a tail transfer's "
    "target block and a dispatch arm each keep their own rts, and nothing joins them "
    "into the procedure's single exit"
)
M_LANDING_PROC = (
    "a resolved computed goto's landing is nobody's call target, so procpass never "
    "sees it as an inline candidate: it becomes its own proc entry and the emitted "
    "`goto` crosses procedures"
)
M_SWITCH_CALL = (
    "a computed call whose target set the trace closed emits `call (expr) ret $PC` "
    "plus `switch call { case .. }`: the arms are already nested at the site, but the "
    "dcall statement and the arms' own rets stay"
)
M_SP_LINKED = (
    "framestack.drop_sp refuses while a raw call keeps the machine stack alive "
    "(sp_linked): the pushed argument is named through sp, so the parameter and the "
    "updates survive the drop"
)
M_SP_UNBALANCED = (
    "the pull/push return-address dance leaves _SpFlow unable to prove the procedure "
    "stands where it entered (sp_unbalanced), so sp stays and the re-push survives as "
    "a page-one word copy"
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
M_PAGE_ONE_CELL = (
    "a page-one cell no forwarding removes is an ordinary memory access: it survives "
    "wherever the store and the read are not in one frame, and no rung relocates a "
    "$0100..$01FF address off the page"
)
M_PAGE_ONE_BLIND = (
    "an `(zp),Y` address is a LOAD-derived base the lift leaves symbolic, so the "
    "store-load pair a single frame writes and reads back is not forwarded away: the "
    "access survives into the text, and the evaluator lands it in page one where the "
    "same frame spelled `abs` keeps no access at all"
)
M_SP_LOOP_PUSH = (
    "a push inside a loop stands at no one displacement: the model's SP flow leaves "
    "the body's entry sp bot, so concretize_stack names no cell, rung (d0r) has no "
    "return slot to ask the value question of, and the pushes stay sp-relative stores"
)

MECHANISMS = (
    M_CALLB,
    M_SHARED_SUB,
    M_SELF_CALL,
    M_MULTI_EXIT,
    M_LANDING_PROC,
    M_SWITCH_CALL,
    M_SP_LINKED,
    M_SP_UNBALANCED,
    M_S_RELOCATED,
    M_S_AS_VALUE,
    M_PAGE_ONE_CELL,
    M_PAGE_ONE_BLIND,
    M_SP_LOOP_PUSH,
)

M_BRK_TERM = (
    "structured.Block has no terminator form for the lifter's `brk` control: the VM "
    "executes BRK (vm.py pushes pc+2 and P and vectors through $FFFE) but the block "
    "model raises DecompileError, so a BRK player has no lift to state an invariant of"
)

PINS = {
    ("leaf-call", "head/sid"): (M_CALLB,),
    ("leaf-call", "head/cell"): (M_CALLB,),
    ("leaf-call", "mid/sid"): (M_CALLB,),
    ("leaf-call", "mid/cell"): (M_CALLB,),
    ("leaf-call", "tail/sid"): (M_CALLB,),
    ("leaf-call", "tail/cell"): (M_CALLB,),
    ("multi-site", "2-site/X"): (M_SHARED_SUB,),
    ("multi-site", "2-site/Y"): (M_SHARED_SUB,),
    ("multi-site", "2-site/no-arg"): (M_SHARED_SUB,),
    ("multi-site", "3-site/X"): (M_SHARED_SUB,),
    ("multi-site", "3-site/Y"): (M_SHARED_SUB,),
    ("multi-site", "3-site/no-arg"): (M_SHARED_SUB,),
    ("nested", "X/tail"): (M_CALLB,),
    ("nested", "X/post"): (M_CALLB,),
    ("nested", "Y/tail"): (M_CALLB,),
    ("nested", "Y/post"): (M_CALLB,),
    ("arg-pass", "a"): (M_CALLB,),
    ("arg-pass", "x"): (M_CALLB,),
    ("arg-pass", "y"): (M_CALLB,),
    ("arg-pass", "cell"): (M_CALLB,),
    ("arg-pass", "stack-tsx"): (M_CALLB, M_SP_LINKED),
    ("arg-pass", "stack-pla"): (M_CALLB, M_SP_UNBALANCED),
    ("ret-value", "a"): (M_CALLB,),
    ("ret-value", "x"): (M_CALLB,),
    ("ret-value", "y"): (M_CALLB,),
    ("ret-value", "carry"): (M_CALLB,),
    ("ret-value", "cell"): (M_CALLB,),
    ("tail-call", "shared"): (M_CALLB,),
    ("tail-call", "cond"): (M_MULTI_EXIT,),
    ("rts-trick", "const"): (M_LANDING_PROC,),
    ("rts-trick", "arith"): (M_LANDING_PROC,),
    ("rts-trick", "two-arm"): (M_LANDING_PROC,),
    ("rts-trick", "loop"): (M_LANDING_PROC, M_SP_LOOP_PUSH),
    ("rts-trick", "table"): (M_LANDING_PROC,),
    ("rts-trick", "ptr"): (M_LANDING_PROC,),
    ("rts-trick", "open"): (M_LANDING_PROC,),
    ("vector-call", "jmpind"): (M_MULTI_EXIT,),
    ("vector-call", "smc-jsr"): (M_SWITCH_CALL,),
    ("vector-call", "smc-jmp"): (M_MULTI_EXIT,),
    ("tail-recursion", "x/const"): (M_SELF_CALL,),
    ("tail-recursion", "x/row"): (M_SELF_CALL,),
    ("tail-recursion", "cell/const"): (M_SELF_CALL,),
    ("tail-recursion", "cell/row"): (M_SELF_CALL,),
    ("deep-recursion", "const/after"): (M_SELF_CALL,),
    ("deep-recursion", "const/around"): (M_SELF_CALL,),
    ("deep-recursion", "row/after"): (M_SELF_CALL,),
    ("deep-recursion", "row/around"): (M_SELF_CALL,),
    ("two-callers", "X/sid"): (M_CALLB, M_SHARED_SUB),
    ("two-callers", "X/cell"): (M_CALLB, M_SHARED_SUB),
    ("two-callers", "Y/sid"): (M_CALLB, M_SHARED_SUB),
    ("two-callers", "Y/cell"): (M_CALLB, M_SHARED_SUB),
    ("two-callers", "cell/sid"): (M_CALLB, M_SHARED_SUB),
    ("two-callers", "cell/cell"): (M_CALLB, M_SHARED_SUB),
    ("branchy-callee", "cc/dey"): (M_SHARED_SUB,),
    ("branchy-callee", "cc/iny"): (M_SHARED_SUB,),
    ("branchy-callee", "cs/dey"): (M_SHARED_SUB,),
    ("branchy-callee", "cs/iny"): (M_SHARED_SUB,),
    ("stack-move", "row/push"): (M_S_RELOCATED,),
    ("s-illegal", "las/page1"): (M_S_AS_VALUE, M_PAGE_ONE_CELL),
    ("s-illegal", "las/far"): (M_S_AS_VALUE,),
    ("page-one-cell", "abs/cross"): (M_PAGE_ONE_CELL,),
    ("page-one-cell", "absx/cross"): (M_PAGE_ONE_CELL,),
    ("page-one-cell", "absy/cross"): (M_PAGE_ONE_CELL,),
    ("page-one-cell", "indy/same"): (M_PAGE_ONE_BLIND,),
    ("page-one-cell", "indy/cross"): (M_PAGE_ONE_CELL,),
}

# what already holds, and must keep holding
CLEAN = (
    ("tail-call", "only"),
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
    ("page-one-cell", "absx/same"),
    ("page-one-cell", "absy/same"),
    ("irq-frame", "hw"),
    ("irq-frame", "cinv"),
)

SPLITS = {
    "arg-pass": (M_SP_LINKED, M_SP_UNBALANCED),
    "tail-call": (M_CALLB, M_MULTI_EXIT),
    "rts-trick": (M_SP_LOOP_PUSH,),
    "vector-call": (M_SWITCH_CALL, M_MULTI_EXIT),
    "stack-move": (M_S_RELOCATED,),
    "s-illegal": (M_S_AS_VALUE, M_PAGE_ONE_CELL),
    "page-one-cell": (M_PAGE_ONE_CELL, M_PAGE_ONE_BLIND),
}

# the protected-region fault a variant's text raises, at the cell it concretely reaches
FAULTS = {
    ("arg-pass", "stack-pla"): "store into the stack page $01FD",
    ("arg-pass", "stack-tsx"): "store into the stack page $01FD",
    ("page-one-cell", "abs/cross"): "load from the stack page $0108",
    ("page-one-cell", "absx/cross"): "load from the stack page $0100",
    ("page-one-cell", "absy/cross"): "load from the stack page $0100",
    ("page-one-cell", "indy/cross"): "load from the stack page $0100",
    ("page-one-cell", "indy/same"): "store into the stack page $0100",
    ("rts-trick", "loop"): "store into the stack page $01FD",
    ("s-illegal", "las/page1"): "load from the stack page $0100",
    ("stack-move", "row/push"): "store into the stack page $0140",
}

DISPATCH_ARMS = (
    ("vector-call", "jmpind"),
    ("vector-call", "smc-jmp"),
)


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


def test_the_bound_pcs_are_the_computed_dispatch_arms_alone():
    """Where context-qualified pcs would be needed: an arm the structurer could not fold."""
    got = {v for v in G.ALL if any(G.pcs_bound(s) for _e, _p, _r, s in G.parsed(*v).procs)}
    assert got == set(DISPATCH_ARMS)


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
