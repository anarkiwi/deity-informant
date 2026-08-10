"""Adoption §8 step 4's cutover, pinned on the canonical example.

Step 4 carries the rung-built statements ``frameprog.program`` produces through
``eqlift_mem.render_proc``, in place of ``frameproc.render_lines``. The goal is a
strict xfail; the observed first blocker sits beside it un-pinned.
"""

from unittest import mock

import pytest

from deity_informant import eqlift_mem
from deity_informant import framefuse
from deity_informant import framelog
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import structured

FRAMES = 24  # the count the witness e2e pin replays: far enough to cross the scripts


@pytest.fixture(scope="module", name="example")
def _example():
    """``(model, frames, prog)`` for the canonical example, read through its own pipeline."""
    sml = pytest.importorskip("examples.state_machine_lift")
    mem, _labels = sml.build_image()
    model, _ev = structured.decompile(bytearray(mem), sml.INIT, sml.PLAY, FRAMES)
    return model, FRAMES, frameprog.program(model)


def _render_ctx(model, prog):
    """``emit_mem``'s per-artifact renderer context, built over the rung-built procedures."""
    flat = [(entry, stmts) for entry, _p, _r, stmts in prog.procs]
    info = frameproc._Info(flat, prog.play)
    info.summarize()
    for _round in range(3):
        before = ({e: list(v) for e, v in info.params.items()}, dict(info.rets))
        info.summarize()
        if before == (info.params, info.rets):
            break
    foot = eqlift_mem.Footprints(
        flat,
        info.open_flow,
        framefuse._landings(model),
        eqlift_mem._extent_spans(prog.extents, prog.data_decls),
    )
    return info, foot


def _unified_lines(model, prog):
    """``render_lines``' replacement: the same procedure headers, bodies from the graph."""
    info, foot = _render_ctx(model, prog)
    out = []
    for entry, params, rets, stmts in prog.procs:
        sig = "sub_%04X(%s)" % (entry, ", ".join(params))
        if rets:
            sig += " -> %s" % ", ".join(rets)
        out.append(sig + " {")
        body = eqlift_mem.render_proc(stmts, prog.symbols, entry, info, foot=foot)
        out.extend("  " + ln for ln in body)
        out.append("}")
    return out


def _spliced_text(model, prog):
    """The step-4 artifact: frameprog's own text with the unified renderer spliced in."""
    with mock.patch.object(
        frameproc, "render_lines", lambda *_a, **_k: _unified_lines(model, prog)
    ):
        return frameprog.dumps(prog)


def _copy_terms(node, out):
    """Every ``COPY`` term in a statement tree."""
    if isinstance(node, (list, tuple)):
        if len(node) == 4 and node[0] == "op" and node[1] == "COPY":
            out.append(node)
        for kid in node:
            _copy_terms(kid, out)
    return out


def test_the_example_s_own_emitter_holds_the_three_properties(example):
    """The control: emission, the text fixpoint and the frame oracle, on frameprog today.

    The strict xfail below runs the same three assertions over the same program, so what
    it pins is the splice and not the example or the harness."""
    model, frames, prog = example
    text = frameprog.dumps(prog)
    assert text.startswith("frameprog ")
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, frames, frameprog.loads(text)) is None


def test_the_walker_projection_the_splice_must_reproduce_is_the_machine_s(example):
    """The oracle behind Gate FP is the example's own VM run, not a second reading."""
    sml = pytest.importorskip("examples.state_machine_lift")
    model, frames, _prog = example
    mem, _labels = sml.build_image()
    _trace, walker = frameprog.iota(model, frames)
    assert framelog.canonical(walker) == framelog.canonical(sml.run_vm(mem, frames)[2])


def test_the_splice_plumbing_is_not_the_blocker(example):
    """Everything the splice needs before a statement converts builds over the rungs.

    Call summaries, footprints, landings and extents all come up on frameprog's own
    procedures, so the blocker below is a statement form and not an API mismatch."""
    model, _frames, prog = example
    info, foot = _render_ctx(model, prog)
    assert set(info.procs) == {entry for entry, _p, _r, _s in prog.procs}
    assert all(foot.of(entry) is not None for entry, _p, _r, _s in prog.procs)


def test_the_rung_minted_narrowing_copy_is_a_term(example):
    """#164's first blocker, LANDED: the narrowing ``COPY`` converts and renders.

    Rung (d2) mints a width-one ``COPY`` of a fused u16 local in the phase accumulator's
    carry chain; ``eqlift.trunc`` is the dual of ``zext`` and ``_rewidth`` routes it, so
    the converter no longer faults before any rule fires."""
    model, _frames, prog = example
    terms = _copy_terms([s for _e, _p, _r, s in prog.procs], [])
    assert terms, "the example minted no COPY at all"
    assert all(t[3] == 1 for t in terms), "a COPY that is not narrowing"
    assert any(
        kid[0] == "loc" and kid[2] == 2 for t in terms for kid in t[2]
    ), "no width-one COPY reads a fused u16 local"
    first, _params, _rets, stmts = prog.procs[0]
    body = eqlift_mem.render_proc(stmts, prog.symbols, first, _render_ctx(model, prog)[0])
    assert body and any("trunc1(" in ln for ln in body), "no narrowing read survived"


def test_the_splice_now_blocks_on_the_signed_compare_the_dialect_cannot_spell(example):
    """The next blocker, un-pinned: the unified emitter spells an operator frameprog has not.

    ``eqlift``'s ``slt``/``sge`` print ``<s``/``>=s`` -- 3d landing 4 measured the smaller
    graph reaching ``(a <s $00)`` for ``((a & $80) != $00)`` -- and ``sidprog.lark``'s ``op``
    production has no signed comparison, so ``loads`` refuses the spliced text."""
    model, _frames, prog = example
    text = _spliced_text(model, prog)
    assert "<s " in text, "the example stopped spelling the signed compare"
    with pytest.raises(ValueError, match="Unexpected token"):
        frameprog.loads(text)


@pytest.mark.xfail(
    reason="register-model-lift stage 4 (§8 step 4): the spliced emitter carries "
    "rung-minted statements",
    strict=True,
)
def test_the_spliced_emitter_carries_the_rung_built_statements(example):
    """The cutover gate: the unified renderer emits the example's artifact, and it holds.

    Emission succeeds, the text is a ``dumps``/``loads`` fixpoint, and the program it
    parses back to reproduces the walker's per-frame projection under Gate FP."""
    model, frames, prog = example
    text = _spliced_text(model, prog)
    assert text.startswith("frameprog ")
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, frames, frameprog.loads(text)) is None
