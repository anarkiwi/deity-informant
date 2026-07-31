"""Gate FP: the frameprog reference evaluator against the walker's projection.

Covers the law of docs/frameprog.md 1.4 over the synthetic corpus, the M-FP1
mutation evidence (dropped write, swapped ctrl order, wrong iota index) and the
guarded-envelope faults (unobserved arm, undeclared input, trace exhaustion).
"""

from pathlib import Path

import numpy as np
import pytest

from deity_informant import framelog as F
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import structured as S
from deity_informant.c64 import load_psid
from deity_informant.frameval import FrameFault
import _fuzzgen as G

from _corpus import corpus_params
from test_frameprog import _fuzz_model

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

_FP_GAP = frozenset()  # no recorded gaps: every player class passes Gate FP


def _sid_model(pairs):
    """Decompiled player writing ``(sid offset, value)`` in order, then RTS."""
    a = G.Asm(G.ORG)
    for r, v in pairs:
        a.i("LDA", "imm", v).i("STA", "abs", 0xD400 + r)
    a.i("RTS")
    outs = {0xD400 + r for r, _v in pairs}
    p = G.Player("mut", G.ORG, a.assemble(), outs, {"indexed"}, init=G._RTS, init_org=G._INIT_ORG)
    return _fuzz_model(p)


def _stmts(prog):
    return prog.procs[0][3]


def _st_at(stmts, reg):
    return [i for i, s in enumerate(stmts) if s[0] == "st" and s[1][1] == 0xD400 + reg]


def _progs(procs, inputs=(), mem0=None, play=0x1000):
    return frameprog.FrameProgram(play, 0x0F00, inputs=inputs, procs=procs, mem0=mem0)


def _prog(stmts, inputs=(), mem0=None, play=0x1000, entry=0x1000):
    return _progs([(entry, [], [], list(stmts))], inputs, mem0, play)


def _wr(reg, val):
    return ("st", ("const", 0xD400 + reg, 2), ("const", val, 1))


def _leaf(entry, reg, val, arm=False):
    return (entry, [], [], [_wr(reg, val), ("ret", arm)])


@pytest.mark.parametrize("p", G.players(3), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_gate_fp_fuzz_players(p):
    """Gate FP over the synthetic corpus: eval_fp == canonical(walker frames)."""
    d = frameval.gate_fp(_fuzz_model(p), max(p.frames, 8))
    if p.name in _FP_GAP:
        assert d is not None, "recorded gap closed: drop %s from _FP_GAP" % p.name
    else:
        assert d is None, d


@pytest.mark.parametrize("kernal", [True, False], ids=["cinv", "hw"])
def test_gate_fp_handler_driven_entry(kernal):
    """Gate FP holds for a ``play == 0`` tune: the frame program reproduces the
    walker across the dispatch stub, the vector goto and the handler's RTI."""
    mem, init = G.irq_image(0x0314 if kernal else 0xFFFE, kernal)
    model, _ev = S.decompile(mem, init, 0, 8, img=G.IRQ_IMAGE)
    prog = frameprog.loads(frameprog.emit(model))
    assert frameval.gate_fp(model, 8) is None
    assert frameval.gate_fp(model, 8, prog) is None


def test_gate_fp_runs_on_the_parsed_artifact():
    """The law holds against the text artifact, not just the in-memory trees."""
    model = _fuzz_model(G.t_table_index(np.random.default_rng(3)))
    assert frameval.gate_fp(model, 8, frameprog.loads(frameprog.emit(model))) is None


def test_frame_buffer_flushes_one_canonical_record():
    """Spec 1.1/1.4: writes buffer per frame, the single projection collapses."""
    prog = frameprog.program(_sid_model([(4, 0x10), (0, 0x11), (0, 0x22), (4, 0x13)]))
    raw = frameval.Evaluator(prog, {}).frames(2)
    assert raw[0] == [(4, 0x10), (0, 0x11), (0, 0x22), (4, 0x13)] == raw[1]
    rec = F.canonical(raw)[0]
    assert rec[0] == ((0, 0x22),)  # freq_lo collapses, ctrl keeps both in order
    assert rec[1] == ((4, 0x10), (4, 0x13))


def test_eval_src_records_the_cell_each_write_loaded_from():
    """Provenance: every byte load at a pure address reports its cell, in order."""
    mem0 = bytearray(0x10000)
    mem0[0x0803] = 0x5A
    idx = ("op", "INT_ZEXT", (("loc", "i"),), 2)
    load = ("mem", ("op", "INT_ADD", (("const", 0x0800, 2), idx), 2), 1)
    stmts = [
        ("asg", "i", ("const", 3, 1)),
        ("st", ("const", 0xD405, 2), load),
        ("st", ("const", 0xD406, 2), ("op", "INT_AND", (load, ("const", 0x0F, 1)), 1)),
        _wr(4, 0x41),
        ("ret", False),
    ]
    prog = _prog(stmts, mem0=mem0)
    frames, srcs = frameval.eval_src(prog, {}, 2)
    assert frames[0] == [(5, 0x5A), (6, 0x0A), (4, 0x41)]
    assert srcs == [[(0x0803,), (0x0803,), ()]] * 2  # the masked load keeps its cell
    assert F.diff(F.canonical(frames), frameval.eval_fp(prog, {}, 2)) is None
    assert frameval._pure(idx) and not frameval._pure(load)


def _staged(cell, val_of):
    return ("st", ("const", cell, 2), val_of)


def _cell(addr):
    return ("mem", ("const", addr, 2), 1)


def test_eval_src_chases_a_staged_byte_back_to_the_table_it_came_from():
    """A byte staged in RAM and flushed reports its origin ahead of the cell read.

    A cell the play phase recomputed from more than one cell starts a new origin."""
    mem0 = bytearray(0x10000)
    mem0[0x0803], mem0[0x0804] = 0x5A, 0x60
    idx = ("op", "INT_ZEXT", (("loc", "i"),), 2)

    def load(k):
        return ("mem", ("op", "INT_ADD", (("const", 0x0800 + k, 2), idx), 2), 1)

    stmts = [
        ("asg", "i", ("const", 3, 1)),
        _staged(0x00C0, load(0)),
        _staged(0x00C2, _cell(0x00C0)),
        _staged(0x00C1, load(1)),
        _staged(0x00C1, ("op", "INT_OR", (_cell(0x00C1), _cell(0x00C0)), 1)),
        ("st", ("const", 0xD405, 2), _cell(0x00C2)),
        ("st", ("const", 0xD406, 2), _cell(0x00C1)),
        ("ret", False),
    ]
    frames, srcs = frameval.eval_src(_prog(stmts, mem0=mem0), {}, 2)
    assert frames[0] == [(5, 0x5A), (6, 0x7A)]
    assert srcs == [[(0x0803, 0x00C2), (0x00C1,)]] * 2  # $C2 chases, $C1 has no origin


def test_eval_src_carries_an_origin_through_the_local_that_staged_it():
    """A byte staged in a register reaches the SID as the table cell it was loaded from.

    A local holding a computed byte carries no origin, so its staged cell stands alone."""
    mem0 = bytearray(0x10000)
    mem0[0x0803], mem0[0x0804] = 0x5A, 0x60
    idx = ("op", "INT_ZEXT", (("loc", "i"),), 2)

    def load(k):
        return ("mem", ("op", "INT_ADD", (("const", 0x0800 + k, 2), idx), 2), 1)

    stmts = [
        ("asg", "i", ("const", 3, 1)),
        ("asg", "a", load(0)),
        _staged(0x00C0, ("loc", "a")),
        ("asg", "y", ("op", "INT_OR", (load(0), load(1)), 1)),
        _staged(0x00C1, ("loc", "y")),
        ("st", ("const", 0xD405, 2), _cell(0x00C0)),
        ("st", ("const", 0xD406, 2), _cell(0x00C1)),
        ("st", ("const", 0xD407, 2), ("loc", "a")),
        ("ret", False),
    ]
    frames, srcs = frameval.eval_src(_prog(stmts, mem0=mem0), {}, 2)
    assert frames[0] == [(5, 0x5A), (6, 0x7A), (7, 0x5A)]
    # the register hop chases, straight to the SID as well as through the staging cell
    assert srcs == [[(0x0803, 0x00C0), (0x00C1,), (0x0803,)]] * 2


def test_eval_src_forgets_a_cell_the_return_address_overwrote():
    """A pushed return byte is not the table byte that stood in the cell before it."""
    mem0 = bytearray(0x10000)
    mem0[0x0803] = 0x5A
    idx = ("op", "INT_ZEXT", (("loc", "i"),), 2)
    load = ("mem", ("op", "INT_ADD", (("const", 0x0800, 2), idx), 2), 1)
    stmts = [
        ("asg", "i", ("const", 3, 1)),
        _staged(0x01FC, load),
        _staged(0x00C0, load),
        ("call", 0x1100, 0x1005),
        ("st", ("const", 0xD405, 2), _cell(0x01FC)),
        ("st", ("const", 0xD406, 2), _cell(0x00C0)),
        ("ret", False),
    ]
    prog = _progs([(0x1000, [], [], stmts), (0x1100, [], [], [("ret", False)])], mem0=mem0)
    frames, srcs = frameval.eval_src(prog, {}, 1)
    assert frames[0] == [(5, 0x05), (6, 0x5A)]
    assert srcs == [[(0x01FC,), (0x0803, 0x00C0)]]


_MUT = [(4, 0x10), (4, 0x11), (0, 0x22), (1, 0x33)]


def test_mutation_dropped_write_is_detected():
    model = _sid_model(_MUT)
    assert frameval.gate_fp(model, 4) is None
    prog = frameprog.program(model)
    del _stmts(prog)[_st_at(_stmts(prog), 0)[0]]
    d = frameval.gate_fp(model, 4, prog)
    assert d is not None and d.section == "v0.lww" and d.want == (0, 0x22) != d.got


def test_mutation_swapped_ctrl_order_is_detected():
    model = _sid_model(_MUT)
    prog = frameprog.program(model)
    stmts = _stmts(prog)
    i, j = _st_at(stmts, 4)
    stmts[i], stmts[j] = stmts[j], stmts[i]
    d = frameval.gate_fp(model, 4, prog)
    assert d is not None and d.section == "v0.ord"
    assert (d.got, d.want) == ((4, 0x11), (4, 0x10))


def test_mutation_wrong_iota_index_is_detected():
    """Spec 1.3: resolving the wrong k-th read of a frame breaks the law."""
    model = _fuzz_model(G.t_volatile(np.random.default_rng(1)))
    nf = 8
    trace, walker = frameprog.iota(model, nf)
    prog = frameprog.program(model)
    want = F.canonical(walker)
    assert F.diff(frameval.eval_fp(prog, trace, nf), want) is None
    assert {k for _f, _n, k in trace} == {0, 1}
    name = frameprog.declared_inputs(trace)[0]
    assert trace[(0, name, 0)] != trace[(0, name, 1)]
    swapped = {(f, n, k ^ 1): v for (f, n, k), v in trace.items()}
    assert F.diff(frameval.eval_fp(prog, swapped, nf), want) is not None


def test_call_and_dispatch_forms_execute_in_order():
    """Every transfer form: static/inlined/parameterized call, computed goto/call."""
    mem0 = bytearray(0x10000)
    mem0[0x0300], mem0[0x0301] = 0x00, 0x60
    body = [_wr(2, 0x02), ("ret", True)]
    main = [
        ("call", 0x2000, 0x1002),
        ("callb", 0x3000, 0x1005, body),
        ("pcall", 0x2000, [], []),
        ("dcall", ("const", 0x2000, 2), 0x1008),
        ("swc", ["$2000"], [("$5000", [_wr(4, 0x04), ("ret", True)])]),
        ("igoto", 0x0300, None),
    ]
    tail = [("dgoto", ("const", 0x7000, 2)), ("swg", [("$7000", [_wr(7, 0x07), ("ret", False)])])]
    procs = [(0x1000, [], [], main), _leaf(0x2000, 0, 0x01), (0x6000, [], [], tail)]
    raw = frameval.Evaluator(_progs(procs, mem0=mem0), {}).frames(1)
    assert raw[0] == [(0, 1), (2, 2), (0, 1), (0, 1), (7, 7)]


def test_dangling_switch_statements_fault():
    with pytest.raises(FrameFault, match="switch goto without"):
        frameval.eval_fp(_prog([("swg", [])]), {}, 1)
    with pytest.raises(FrameFault, match="switch call without"):
        frameval.eval_fp(_prog([("swc", [], [])]), {}, 1)
    with pytest.raises(FrameFault, match="unimplemented statement"):
        frameval.eval_fp(_prog([("nope",)]), {}, 1)


def test_switch_goto_arm_may_not_run_on():
    procs = [
        (0x1000, [], [], [("dgoto", ("const", 0x2000, 2)), ("swg", [("$2000", [_wr(0, 1)])])]),
    ]
    with pytest.raises(FrameFault, match=r"case \$2000 ran on"):
        frameval.eval_fp(_progs(procs), {}, 1)


def test_undeclared_and_exhausted_inputs_fault():
    read = ("st", ("const", 0xD400, 2), ("mem", ("const", 0xD012, 2), 1))
    with pytest.raises(FrameFault, match="undeclared volatile input raster"):
        frameval.eval_fp(_prog([read, ("ret", False)]), {}, 1)
    with pytest.raises(FrameFault, match=r"iota\(0, raster, 0\) past the pinned trace"):
        frameval.eval_fp(_prog([read, ("ret", False)], inputs=["raster"]), {}, 1)


def test_switch_arm_outside_the_observed_set_faults():
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xEA
    arm = [("st", ("const", 0xD404, 2), ("const", 1, 1)), ("ret", False)]
    prog = _prog([("opsw", 0x1000, [("$A9", arm)])], mem0=mem0)
    with pytest.raises(FrameFault, match=r"switch \$1000 target \$00EA"):
        frameval.eval_fp(prog, {}, 1)


def test_unobserved_region_and_fallthrough_fault():
    with pytest.raises(FrameFault, match=r"unobserved \$2000 reached"):
        frameval.eval_fp(_prog([("unobs", 0x2000)]), {}, 1)
    with pytest.raises(FrameFault, match="fell through"):
        frameval.eval_fp(_prog([("asg", "a", ("const", 1, 1))]), {}, 1)


def test_unlinkable_targets_fault_at_compile_time():
    with pytest.raises(FrameFault, match=r"target \$9999 outside the program"):
        frameval.eval_fp(_prog([("goto", 0x9999)]), {}, 1)
    with pytest.raises(FrameFault, match=r"play \$2000 is not a serialized procedure"):
        frameval.eval_fp(_prog([("ret", False)], play=0x2000), {}, 1)


def _goto_into_later_arm():
    """A procedure whose forward goto reaches a label consuming a live local.

    The sweep walks the then-arm before the else-arm, so the goto is seen before
    its target: without a fixpoint the label's live-set reads empty and ``y``
    looks dead, and the inliner deletes the update the label still consumes."""
    y = ("loc", "y")
    inc = ("op", "INT_ADD", (y, ("const", 2, 1)), 1)
    addr = ("op", "INT_ADD", (("op", "INT_ZEXT", (y,), 2), ("const", 0x0F95, 2)), 2)
    stmts = [
        ("asg", "y", inc),
        (
            "if",
            "ifnot",
            ("op", "INT_LESSEQUAL", (("const", 1, 1), ("const", 2, 1)), 1),
            [("asg", "z", ("const", 9, 1))],
            [("goto", 0x0C63)],
        ),
        ("label", 0x0C63),
        ("st", ("const", 0xD402, 2), ("mem", addr, 1)),
    ]
    info = frameproc._Info([(0x900, stmts)], 0x900)
    info.summarize()
    return info


def test_goto_target_is_a_live_consumer_and_its_update_survives():
    """An own-procedure goto consumes whatever its target consumes.

    Deleting the update makes the label index the wrong cell, so the local must
    survive inlining; ``_use_count`` sees no textual use, hence ``_invis_name``."""
    info = _goto_into_later_arm()
    assert 0x0C63 in info.own_labels[0x900]
    assert "y" in info.labmap[0x900][0x0C63]
    assert frameproc._invis_name(("goto", 0x0C63), "y", info, 0x900)
    for _ in range(4):
        frameproc._inline(info)
    assert any(s[0] == "asg" and s[1] == "y" for s in info.procs[0x900])


def test_call_entered_body_keeps_its_updates_live():
    """A label a ``call`` enters returns to its call sites and may re-enter.

    Its exit therefore carries the machine set; treating it as textual
    fall-through let ``_prune`` delete an update the next entry consumes."""
    y = ("loc", "y")
    addr = ("op", "INT_ADD", (("op", "INT_ZEXT", (y,), 2), ("const", 0x1300, 2)), 2)
    stmts = [
        ("call", 0x1100, 0x1005),
        ("call", 0x1100, 0x1009),
        ("label", 0x1100),
        ("st", addr, ("const", 5, 1)),
        ("asg", "y", ("op", "INT_ADD", (y, ("const", 1, 1)), 1)),
        ("ret", False),
    ]
    info = frameproc._Info([(0x1000, stmts)], 0x1000)
    info.summarize()
    assert info.call_labels[0x1000] == {0x1100}
    for _ in range(4):
        frameproc._prune(info)
        frameproc._inline(info)
    assert any(s[0] == "asg" and s[1] == "y" for s in info.procs[0x1000])


def test_stack_pointer_updates_are_never_eliminated():
    """``sp`` is machine state: call/ret move it and pushed bytes ride on it."""
    sp = ("loc", "sp")
    stmts = [
        ("asg", "sp", ("op", "INT_ADD", (sp, ("const", 0xFF, 1)), 1)),
        ("call", 0x2000, 0x1005),
        ("asg", "sp", ("op", "INT_ADD", (sp, ("const", 1, 1)), 1)),
        ("ret", False),
    ]
    info = frameproc._Info([(0x1000, stmts), (0x2000, [("ret", False)])], 0x1000)
    info.summarize()
    for _ in range(4):
        frameproc._prune(info)
        frameproc._inline(info)
    kept = [s for s in info.procs[0x1000] if s[0] == "asg" and s[1] == "sp"]
    assert len(kept) == 2, info.procs[0x1000]


def _lot_of_coke():
    return [
        pytest.param(path, sub, id="Slaygon-A_Lot_of_Coke_part_8")
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == "A_Lot_of_Coke_part_8"
    ]


@pytest.mark.oracle
@pytest.mark.parametrize("sid,subtune", _lot_of_coke())
def test_gate_fp_goto_liveness_regression(sid, subtune):
    """The tune whose voice-2 pulse width diverged by one accumulator step."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, 300, subtune)
    assert frameval.gate_fp(model, 300) is None


def _commando():
    return [
        pytest.param(path, sub, secs, id="Hubbard_Rob-Commando")
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == "Commando" and path.parent.name == "Hubbard_Rob"
    ]


@pytest.mark.oracle
@pytest.mark.parametrize("sid,subtune,secs", _commando())
def test_gate_fp_commando_full_length(sid, subtune, secs):
    """Corpus-scale Gate FP at Songlengths duration on the reference tune."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    nframes = int(secs * 50)
    model, _ev = S.decompile(mem, init, play, nframes, subtune)
    assert frameval.gate_fp(model, nframes) is None
