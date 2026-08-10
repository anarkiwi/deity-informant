"""Stage 4's round-trip witness, partial: every normal form run on the machine.

Each ``idioms.FORMS`` row is re-emitted as 6502 from the program
``test_vocabulary`` checks through the evaluator and replayed on ``PcodeVM``, so
the checked SID write is witnessed with no evaluator in the trust chain.
"""

import pytest

import test_vocabulary as tv
from deity_informant import framelog
from deity_informant import frameprog
from deity_informant import structured
from deity_informant import witness6502 as W
from deity_informant.asm6502 import Asm

SID, CELL, ALT, PTR, ARR = tv.SID, tv.CELL, tv.ALT, tv.PTR, tv.ARR
c, m, op, loc, zext = tv.c, tv.m, tv.op, tv.loc, tv.zext

WITNESSED = sorted(tv.CASES)  # the rows this witness claims: the catalog's own ids
E2E_FRAMES = 24  # the example replayed far enough to cross its script commands


def prog(stmts, procs=(), **kw):
    """A frame program over ``test_vocabulary``'s image, play at $1000."""
    return frameprog.FrameProgram(
        0x1000, 0x0F00, procs=[(0x1000, [], [], stmts)] + list(procs), mem0=tv.image(), **kw
    )


def store(value, at=SID, hi_first=False):
    s = ("st", c(at, 2), value)
    return (s + (True,)) if hi_first else s


def run(stmts, nframes=1, **kw):
    """Re-emit and replay: the per-frame SID write stream off the machine."""
    return W.emit(prog(stmts, **kw)).frames(nframes)


def test_the_witness_claims_are_enumerated_from_the_catalog_and_cannot_undercount():
    """The claim list is the stage-2 checklist itself, so it counts what it covers."""
    assert WITNESSED == tv.CHECKLIST
    assert len(WITNESSED) == 20


@pytest.mark.parametrize("rid", WITNESSED)
def test_every_normal_form_is_witnessed_on_the_machine(rid):
    """The form re-emitted as 6502 writes the row's own checked SID bytes."""
    value, writes, state, decls, resolved = tv.CASES[rid]
    witness = W.emit(tv._prog(value, state, decls, resolved))
    assert witness.frames(1) == [writes]


def test_the_re_emitted_image_is_the_program_s_own_bytes_plus_the_witness_code():
    """State and data stay where the program declares them: only free bytes are ours."""
    value, _w, state, decls, resolved = tv.CASES["pair-row"]
    p = tv._prog(value, state, decls, resolved)
    witness = W.emit(p)
    base, size = W.free_span(p)
    assert base <= witness.entry < base + size
    changed = {a for a in range(0x10000) if witness.mem[a] != p.mem0[a]}
    assert changed and all(base <= a < base + size for a in changed)


def test_a_state_cell_carries_across_frames_on_the_machine():
    """Cells are memory: the re-emitted frame reads back what the last one wrote."""
    stmts = [store(op("INT_ADD", (m(CELL), c(1))), CELL), store(m(CELL)), ("ret", False)]
    assert run(stmts, 3) == [[(0, 0x12)], [(0, 0x13)], [(0, 0x14)]]


def test_a_word_store_emits_its_own_byte_order():
    """``hi_first`` rides on the store, so the frame log is the store's order."""
    lo_first = run([store(zext(m(CELL)), SID), ("ret", False)])
    hi_first = run([store(zext(m(CELL)), SID, True), ("ret", False)])
    assert lo_first == [[(0, 0x11), (1, 0x00)]]
    assert hi_first == [[(1, 0x00), (0, 0x11)]]


def test_a_store_through_a_computed_address_reaches_the_boundary():
    """The address is patched into the instruction's own operand bytes."""
    addr = op("INT_ADD", (c(SID, 2), zext(loc("y"))), 2)
    assert run([("st", addr, c(0x2A)), ("ret", False)]) == [[(0, 0x2A)]]


def test_both_arms_of_a_guard_are_witnessed():
    """``if``/``ifnot`` over one condition take the two arms the evaluator takes."""
    cond = op("INT_NOTEQUAL", (m(CELL), c(0x11)))
    arms = ([store(c(0xAA))], [store(c(0xBB))])
    assert run([("if", "if", cond, *arms), ("ret", False)]) == [[(0, 0xBB)]]
    assert run([("if", "ifnot", cond, *arms), ("ret", False)]) == [[(0, 0xAA)]]


def test_a_loop_runs_its_break_and_its_continue():
    stmts = [
        ("asg", "n", c(0)),
        (
            "loop",
            [
                store(loc("n")),
                ("asg", "n", op("INT_ADD", (loc("n"), c(1)))),
                ("if", "if", op("INT_EQUAL", (loc("n"), c(3))), [("brk",)], []),
                ("cont",),
            ],
        ),
        ("ret", False),
    ]
    assert run(stmts) == [[(0, 0), (0, 1), (0, 2)]]


def test_a_for_range_runs_its_own_bounds():
    """The range is the evaluator's: the body runs on ``last`` and the test ends it."""
    assert run([("for", "i", 0, 3, [store(loc("i"))]), ("ret", False)]) == [
        [(0, 0), (0, 1), (0, 2), (0, 3)]
    ]
    assert run([("for", "i", 2, 0, [store(loc("i"))]), ("ret", False)]) == [
        [(0, 2), (0, 1), (0, 0)]
    ]


def test_a_goto_reaches_its_label_and_skips_what_lies_between():
    stmts = [("goto", 0x1010), store(c(0xEE)), ("label", 0x1010), store(c(0x77)), ("ret", False)]
    assert run(stmts) == [[(0, 0x77)]]


def test_a_goto_reaches_a_procedure_entry():
    callee = [(0x1200, [], [], [store(c(0x5A)), ("ret", False)])]
    assert run([("goto", 0x1200)], procs=callee) == [[(0, 0x5A)]]


def test_a_wide_operation_widens_its_narrow_operand():
    """A byte term under a word operation is zero-extended, as the evaluator reads it."""
    assert run([store(op("INT_ADD", (m(CELL), m(CELL, 2)), 2)), ("ret", False)]) == [
        [(0, 0x22), (1, 0x22)]
    ]


def test_a_wide_equality_compares_every_byte():
    same = op("INT_NOTEQUAL", (m(CELL, 2), c(0x2211, 2)))
    other = op("INT_NOTEQUAL", (m(CELL, 2), c(0x2212, 2)))
    assert run([store(same), store(other), ("ret", False)]) == [[(0, 0), (0, 1)]]


def test_a_wide_guard_tests_every_byte():
    """A word condition is false only where every one of its bytes is."""
    arms = ([store(c(0x01))], [store(c(0x02))])
    assert run([("if", "if", m(CELL, 2), *arms), ("ret", False)]) == [[(0, 0x01)]]
    assert run([("if", "if", m(0x2400, 2), *arms), ("ret", False)]) == [[(0, 0x02)]]


def test_a_for_range_counts_past_a_byte():
    body = [store(op("COPY", (loc("i", 2),)))]
    assert run([("for", "i", 0xFE, 0x101, body), ("ret", False)]) == [
        [(0, 0xFE), (0, 0xFF), (0, 0x00), (0, 0x01)]
    ]


def test_a_procedure_call_passes_its_argument_and_returns():
    """Locals are program-wide slots, so the ABI is the parameter binding itself."""
    callee = [(0x1200, ["p"], [], [store(op("INT_ADD", (loc("p"), c(1)))), ("ret", False)])]
    stmts = [("pcall", 0x1200, [c(0x40)], []), store(c(0x09)), ("ret", False)]
    assert run(stmts, procs=callee) == [[(0, 0x41), (0, 0x09)]]


def test_an_unobserved_statement_faults_the_machine_instead_of_running_on():
    with pytest.raises(W.WitnessFault, match="unobserved"):
        run([("unobs", 0x1004), ("ret", False)])


REFUSALS = {
    "opsw": (lambda: [("opsw", 0x1000, [("$1000", [])])], "SMC dispatch switch"),
    "swg": (lambda: [("swg", [("$1000", [])])], "SMC dispatch switch"),
    "swc": (lambda: [("swc", ["$1000"], [("$1000", [])])], "SMC dispatch switch"),
    "dgoto": (lambda: [("dgoto", c(0x1000, 2))], "computed transfer"),
    "igoto": (lambda: [("igoto", 0x1000, None)], "computed transfer"),
    "dcall": (lambda: [("dcall", c(0x1000, 2), 0x1005)], "computed transfer"),
    "dbr": (lambda: [("dbr", "if", c(1), c(0x1000, 2))], "computed transfer"),
    "call": (lambda: [("call", 0x1200, 0x1005)], "raw machine call"),
    "callb": (lambda: [("callb", 0x1005, 0x1005, [])], "raw machine call"),
    "unknown": (lambda: [("frobnicate", 0x1000)], "no witnessed statement form"),
    "signed": (lambda: [store(op("INT_SLESS", (m(CELL), c(1))))], "signed operator INT_SLESS"),
    "unknown-op": (lambda: [store(op("FLOAT_ADD", (m(CELL),)))], "operator FLOAT_ADD"),
    "variable-shift": (lambda: [store(op("INT_LEFT", (m(CELL), m(ALT))))], "variable shift"),
    "carry-width": (
        lambda: [store(op("INT_CARRY", (m(CELL), m(ALT, 2))))],
        "carry over operands of unequal width",
    ),
    "volatile-read": (lambda: [store(m(0xD012))], r"volatile input read at \$D012"),
    "goto-nowhere": (lambda: [("goto", 0x9999)], r"transfer to \$9999"),
    "depth": (lambda: [store(op("INT_ADD", (m(CELL),) * 600))], "expression depth"),
    "bogus-node": (lambda: [store(("elsewhere", 0, 1))], "expression node"),
}


@pytest.mark.parametrize("rid", sorted(REFUSALS))
def test_a_construct_outside_the_partial_witness_refuses_by_name(rid):
    """The executable refusal: an exception naming the mechanism, never wrong code."""
    build, needle = REFUSALS[rid]
    with pytest.raises(W.Refusal, match=needle) as got:
        W.emit(prog(build() + [("ret", False)]))
    assert "stage 4 owns the completion" in str(got.value)


def test_the_refusal_table_names_every_statement_form_the_witness_declines():
    """The module's own table is the checklist: a form is witnessed or it is named."""
    assert set(W._STMT_REFUSALS) <= set(REFUSALS)


def test_a_declared_volatile_input_refuses_the_whole_program():
    """iota is the evaluator's pinning; the machine reads the raster itself."""
    with pytest.raises(W.Refusal, match="declared volatile inputs"):
        W.emit(prog([("ret", False)], inputs=["raster"]))


def test_an_extent_guarded_deref_refuses_the_whole_program():
    with pytest.raises(W.Refusal, match="extent-guarded deref"):
        W.emit(prog([("ret", False)], extents={PTR: (ARR,)}, decls=[tv.decl(ARR, 16)]))


def test_a_narrowing_local_read_refuses_by_name():
    """The evaluator reads a local unmasked; a memory slot reads it by width."""
    stmts = [("asg", "t", zext(m(CELL))), store(loc("t")), ("ret", False)]
    with pytest.raises(W.Refusal, match="narrowing read of local 't'"):
        W.emit(prog(stmts))


def test_the_stack_pointer_local_refuses_by_name():
    with pytest.raises(W.Refusal, match="stack-pointer local"):
        W.emit(prog([("asg", "sp", c(0)), ("ret", False)]))


def test_a_call_whose_arguments_are_not_the_parameters_refuses():
    callee = [(0x1200, [], [], [("ret", False)])]
    with pytest.raises(W.Refusal, match="call to sub_1200"):
        W.emit(prog([("pcall", 0x1200, [c(1)], []), ("ret", False)], procs=callee))


def test_an_image_with_no_room_refuses_rather_than_overwriting_the_program():
    stmts = [store(m(CELL)), ("ret", False)]
    with pytest.raises(W.Refusal, match="no free span holds the witness data block"):
        W.emit(prog(stmts), org=0xFF00)
    with pytest.raises(W.Refusal, match="free span is shorter than the emitted code"):
        W.emit(prog(stmts), org=0x10000 - (W.CTRL_BYTES + W.TEMP_BYTES + 8))


def test_the_assembler_binds_labels_in_both_passes():
    """Data bytes take label low/high halves, so a forward reference resolves."""
    p = Asm(0x1000)
    p.i("LDA", "imm", 0x01).i("JMP", "abs", ("L", "tab"))
    p.label("tab").byte(("LOL", "tab"), ("HIL", "tab"), 0x7F)
    assert p.assemble() == bytes((0xA9, 0x01, 0x4C, 0x05, 0x10, 0x05, 0x10, 0x7F))
    assert p.end == 0x1008


def test_the_assembler_refuses_a_duplicate_label_and_a_long_branch():
    with pytest.raises(ValueError, match="duplicate label"):
        Asm(0x1000).label("x").label("x")
    p = Asm(0x1000).i("BNE", "rel", ("L", "far"))
    p.byte(*([0] * 200)).label("far")
    with pytest.raises(ValueError, match="branch out of range"):
        p.assemble()


@pytest.fixture(scope="module", name="example")
def _example():
    """The canonical example's own image, VM frames and frame program."""
    sml = pytest.importorskip("examples.state_machine_lift")
    mem, _labels = sml.build_image()
    frames = sml.run_vm(mem, E2E_FRAMES)[2]
    model, _ev = structured.decompile(bytearray(mem), sml.INIT, sml.PLAY, E2E_FRAMES)
    return frameprog.program(model), frames


def test_the_example_artifact_refuses_only_on_its_dispatch(example):
    """The pin's observed failure mode, pinned: the coverage gap, not a divergence."""
    with pytest.raises(W.Refusal, match="computed transfer"):
        W.emit(example[0])


@pytest.mark.xfail(
    reason="register-model-lift stage 4: the witness covers dispatch and the full artifact",
    strict=True,
)
def test_the_example_artifact_replays_frame_for_frame(example):
    """The end-to-end gate: re-emitted 6502 against the original routine's projection."""
    p, frames = example
    assert framelog.canonical(W.emit(p).frames(len(frames))) == framelog.canonical(frames)
