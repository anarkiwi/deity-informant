"""Adoption §8 step 4's cutover, pinned on the canonical example.

Step 4 carries the rung-built statements ``frameprog.program`` produces through
``eqlift_mem.render_proc``: since the switch that is what ``dumps`` renders, and the
control beside it is ``frameproc.render_lines``, the projection it replaced.
"""

import re
import sys
from pathlib import Path
from unittest import mock

import pytest

from deity_informant import eqlift
from deity_informant import eqlift_mem
from deity_informant import framelog
from deity_informant import frameprog
from deity_informant import frameproc
from deity_informant import frameval
from deity_informant import structured

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import splice_sweep

FRAMES = 24  # the count the witness e2e pin replays: far enough to cross the scripts


@pytest.fixture(scope="module", name="example")
def _example():
    """``(model, frames, prog)`` for the canonical example, read through its own pipeline."""
    sml = pytest.importorskip("examples.state_machine_lift")
    mem, _labels = sml.build_image()
    model, _ev = structured.decompile(bytearray(mem), sml.INIT, sml.PLAY, FRAMES)
    return model, FRAMES, frameprog.program(model)


def _copy_terms(node, out):
    """Every ``COPY`` term in a statement tree."""
    if isinstance(node, (list, tuple)):
        if len(node) == 4 and node[0] == "op" and node[1] == "COPY":
            out.append(node)
        for kid in node:
            _copy_terms(kid, out)
    return out


def test_the_replaced_projection_still_holds_the_three_properties(example):
    """The control: emission, the text fixpoint and the frame oracle, on the old emitter.

    The cutover gate below runs the same three assertions over the same program through
    the unified graph, so what it pins is the switch and not the example or the harness."""
    model, frames, prog = example
    with mock.patch.object(frameprog.FrameProgram, "lines", frameprog.render_lines):
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
    info, foot, _pairs, _derefs = eqlift_mem.render_ctx(prog)
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
    body = eqlift_mem.render_proc(stmts, prog.symbols, first, eqlift_mem.render_ctx(prog)[0])
    assert body and any("trunc1(" in ln for ln in body), "no narrowing read survived"


def test_the_signed_compare_the_emitter_spells_is_the_dialect_s(example):
    """#170's blocker, LANDED: the dialect carries the signed comparison the rules reach.

    ``sidprog.lark``'s ``op`` production spells ``<s`` and ``<=s``, and ``sge`` -- the one
    tag with no p-code mnemonic -- prints as the swapped ``<=s``."""
    model, _frames, prog = example
    text = frameprog.dumps(prog)
    assert "<s " in text and "<=s " in text, "the example stopped spelling the signed compare"
    assert ">=s" not in text, "a spelling the dialect has no production for"
    assert frameprog.loads(text) is not None


def test_the_dispatch_header_and_the_procedure_call_survive_the_splice(example):
    """#161's substrate facts, LANDED, on frameprog's own rung-built procedures.

    The arm table's header names its dispatch kind, and the example's one callee reaches
    only copyable blocks, so its body is duplicated at each of its three static sites."""
    _model, _frames, prog = example
    text = frameprog.dumps(prog)
    assert "switch goto {" in text and "switch {" not in text
    assert len(prog.procs) == 1 and "= sub_" not in text, "a callee stayed a procedure"
    assert text.count(" = m_14BD[") == 3, "the callee body is not at every site"


def test_the_unified_walk_refuses_a_statement_it_cannot_lift(example):
    """The silent drop the splice found is loud: an unknown kind raises, it does not vanish."""
    _model, _frames, prog = example
    _first, _params, _rets, stmts = prog.procs[0]
    with pytest.raises(ValueError, match="unliftable statement"):
        eqlift_mem.render_proc([("nosuchkind", 0)] + list(stmts), prog.symbols)


def test_the_spliced_program_reproduces_the_walker_s_projection(example):
    """The cutover's semantic half, LANDED: Gate FP on the spliced text is clean."""
    model, frames, prog = example
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, frames, frameprog.loads(text)) is None


def test_the_three_memory_spellings_the_cutover_owed_are_in_the_text(example):
    """The last blocker, LANDED: each of the three spellings, in the artifact itself.

    An index *expression* against a declared base, the ``sid.reg`` view of the register
    file, and the declared lo/hi pair read as one word column rather than as the OR."""
    model, _frames, prog = example
    text = frameprog.dumps(prog)
    assert "m_14D3[(ctr_0043 & zp_46)]" in text, "the index expression stayed mem[...]"
    assert "sid.reg[a]" in text, "the register file kept a register name for a byte index"
    assert "m_148F[t3:2]:2" in text, "the declared pair stayed an OR-pack"
    assert "<< $08" not in text, "a byte column pack survived the declarations"


def test_the_printer_breadth_and_the_registry_are_separate_mechanisms(example):
    """The separation #172 measured, now on the widened printer itself.

    ``_loadref`` gives the indexed and ``sid.reg`` forms with no registry at all; only
    the pack asks for ``pairs``, and without it prints the OR it started as."""
    del example
    idx = ("band", ("loc", "ctr_0043.0"), ("loc", "zp_46.0"), 1)
    table = ("load", ("add", ("zext", idx), ("num", 0x14D3, 2), 2), 1, 0)
    reg = ("load", ("add", ("zext", ("loc", "a.0")), ("num", 0xD400, 2), 2), 1, 0)

    def col(b):
        return ("zext", ("load", ("add", ("loc", "t3.0"), ("num", b, 2), 2), 1, 0))

    pack = ("bor", ("shl", col(0x1493), ("num", 8, 1), 2), col(0x148F), 2)
    bare = eqlift._Printer({})
    assert bare.fmt(table) == "m_14D3[(ctr_0043 & zp_46)]"
    assert bare.fmt(reg) == "sid.reg[a]"
    assert bare.fmt(pack) == "((zext2(m_1493[t3]) << $08):2 | zext2(m_148F[t3])):2"
    assert eqlift._Printer({}, {0x148F: (0x1493, 4)}).fmt(pack) == "m_148F[t3]:2"


def test_the_spliced_emitter_carries_the_rung_built_statements(example):
    """The cutover gate: the unified renderer emits the example's artifact, and it holds.

    Emission succeeds, the text is a ``dumps``/``loads`` fixpoint, and the program it
    parses back to reproduces the walker's per-frame projection under Gate FP."""
    model, frames, prog = example
    text = frameprog.dumps(prog)
    assert text.startswith("frameprog ")
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, frames, frameprog.loads(text)) is None


def test_the_headline_metric_counts_every_register_a_version_hides(example):
    """``arch``/``temps``: a copied register is still the register, a width type is not.

    The predicate is read off ``frameproc``'s own allocator, so a register the emitter
    gains cannot go uncounted, and the two residues stay apart."""
    assert splice_sweep._arch_re().pattern.count("|") == len(frameproc._ALL_REG_LOCALS) - 1
    arch, temps = splice_sweep.arch_shapes(
        "a = cflag0\niflag = g4\nzp_00: parameter u8 = $00\nt3 = zext2(y1) ; a a a\n"
    )
    assert (arch, temps) == (5, 1)  # a cflag0 iflag g4 y1 | t3; u8/zext2 are the grammar's
    _model, _frames, prog = example
    text = splice_sweep._NOISE.sub("", frameprog.dumps(prog))
    old = frozenset(("a", "x", "y", "sp", "cflag", "nflag", "zflag", "vflag"))
    arch, temps = splice_sweep.arch_shapes(text)
    assert arch >= sum(1 for w in re.findall(r"[A-Za-z_]\w*", text) if w in old)
    assert temps, "the second residue reads zero on an artifact full of ``t``/``ptr`` locals"
