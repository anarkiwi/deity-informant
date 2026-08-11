"""frameprog dialect: annotation-free surface, state/inputs/data header,
opcode-cell switches on state variables, the M-FP2 reader (canonical fixpoint
``dumps(loads(t)) == t``) and the prototype Gate FP projection check."""

import re
from pathlib import Path

import numpy as np
import pytest

from deity_informant import expr as E
from deity_informant import framelog as F
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant.grammar import addr_name
from deity_informant import sidprog
from deity_informant import structured as S
from deity_informant.c64 import load_psid
from deity_informant.structured import Block

import _fuzzgen as G

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

_ANNOT = re.compile(r"@\d|@t\d|@x\(|@xi\(|code\[")
_Z = ("const", 0, 1)


# ---- a hermetic player whose song data declares and aliases ---------------------
_D_ORG, _D_TBL, _D_WTBL, _D_CNT, _D_PTR = 0x1000, 0x1400, 0x1480, 0x1440, 0x60
_D_PLO, _D_PHI, _D_PATA, _D_PATB = 0x1500, 0x1508, 0x1520, 0x1530


def _decl_player():
    """Counter-indexed tables (proven + record stride), a reloaded pointer
    pair walking command streams, and role-classified state cells."""
    a = G.Asm(_D_ORG)
    a.i("LDX", "abs", _D_CNT)
    a.i("LDA", "absx", _D_PLO).i("STA", "zp", _D_PTR)
    a.i("LDA", "absx", _D_PHI).i("STA", "zp", _D_PTR + 1)
    a.i("LDY", "abs", _D_CNT + 1)
    a.i("LDA", "indy", _D_PTR).i("CMP", "imm", 1).i("BNE", "rel", ("L", "n1"))
    a.i("LDA", "imm", 0x41)
    a.label("n1").i("STA", "abs", G.SID + 4)
    a.i("LDA", "abs", _D_CNT + 2).i("CLC").i("ADC", "imm", 1).i("STA", "abs", _D_CNT + 2)
    a.i("AND", "imm", 3).i("TAX")
    a.i("LDA", "absx", _D_TBL).i("STA", "abs", G.SID)
    a.i("LDA", "abs", _D_CNT + 2).i("AND", "imm", 3).i("ASL", "acc").i("TAX")
    a.i("LDA", "absx", _D_WTBL).i("STA", "abs", G.SID + 2)
    a.i("LDA", "absx", _D_WTBL + 1).i("STA", "abs", G.SID + 3)
    a.i("INC", "abs", _D_CNT + 1).i("LDA", "abs", _D_CNT + 1).i("CMP", "imm", 3)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", _D_CNT + 1)
    a.i("INC", "abs", _D_CNT).i("LDA", "abs", _D_CNT).i("CMP", "imm", 2)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", _D_CNT)
    a.label("out").i("RTS")
    data = {
        _D_PLO: _D_PATA & 0xFF,
        _D_PLO + 1: _D_PATB & 0xFF,
        _D_PHI: _D_PATA >> 8,
        _D_PHI + 1: _D_PATB >> 8,
    }
    data.update({_D_PATA + k: v for k, v in enumerate((1, 2, 1))})
    data.update({_D_PATB + k: 0x11 + k for k in range(3)})
    data.update({_D_TBL + k: 0x30 + k for k in range(4)})
    data.update({_D_WTBL + k: 0x50 + k for k in range(8)})
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS
    for k, b in enumerate(a.assemble()):
        mem[_D_ORG + k] = b
    for addr, v in data.items():
        mem[addr] = v
    return S.decompile(mem, 0x0F00, _D_ORG, 14)


def _regs():
    return [E.reg(i) for i in range(16)]


def _model(blocks, dispatch=None, mem0=None, play=0x1000):
    mem0 = mem0 if mem0 is not None else bytearray(0x10000)
    for pc, op0 in blocks:
        mem0[pc] = mem0[pc] or op0
    return sidprog.BlockModel(mem0, 0x0F00, play, blocks, dispatch or {})


def test_header_version_and_notes():
    m = _model({(0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], [], ("rts",), _regs())})
    text = frameprog.emit(m)
    assert text.startswith("frameprog %d\n" % frameprog.FRAMEPROG_VERSION)
    assert "fusion" in text and "unification" in text  # declared not applied
    assert "state {" in text


def test_cycle_and_penalty_annotations_stripped():
    events = [
        ("cyc", 2),
        ("ld", 0, ("const", 0x1500, 2)),
        ("cyc", 4),
        ("pen", "ax", ("const", 0x1500, 2), ("reg", 2)),
        ("st", ("const", 0xD400, 2), ("uni", 0, 1)),
    ]
    m = _model({(0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], events, ("rts",), _regs())})
    text = frameprog.emit(m)
    assert not _ANNOT.search(text)
    # pen-free single-use load inlines, into the u16 store a freq lane write is
    assert "sid.v1.freq_lo:2 = ((sid.v1.freq_lo:2 & $FF00):2 | zext2(m_1500)):2" in text
    assert " m_1500: u8" in text  # non-SID cell is state
    assert "sid.v1.freq_lo: " not in text  # SID cells are outputs, not state


def test_if_header_without_taken_penalty():
    cond = ("op", "INT_EQUAL", (E.reg(0), ("const", 1, 1)), 1)
    blocks = {
        (0x1000, 0xD0): Block(
            0x1000, 0xD0, [0x1000], [], ("br", 1, 0x2000, 0x1002, cond, None), _regs()
        ),
        (0x1002, 0x60): Block(0x1002, 0x60, [0x1002], [], ("rts",), _regs()),
    }
    text = frameprog.emit(_model(blocks))
    assert "if (a == $01) unobserved $2000" in text
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert "sub_1000(a) {" in text  # the tested register is a live-in parameter
    assert "@t" not in text


def test_opcode_cell_renders_as_state_variable_switch():
    blocks = {
        (0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], [], ("rts",), _regs()),
        (0x1000, 0xEA): Block(0x1000, 0xEA, [0x1000], [], ("rts",), _regs()),
    }
    text = frameprog.emit(_model(blocks, dispatch={0x1000: {0xA9, 0xEA}}))
    assert "switch m_1000 {" in text and "code[" not in text
    assert "case $A9: {" in text and "case $EA: {" in text
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert " m_1000: u8 observed $A9 $EA" in text


def test_single_variant_opcode_cell_keeps_one_arm_switch():
    blocks = {(0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], [], ("rts",), _regs())}
    text = frameprog.emit(_model(blocks, dispatch={0x1000: {0xA9}}))
    assert "switch m_1000 {" in text and "case $A9: {" in text
    assert " m_1000: u8 observed $A9" in text


def test_volatile_read_declares_input():
    events = [("ld", 0, ("const", 0xD012, 2)), ("st", ("const", 0x00FB, 2), ("uni", 0, 1))]
    m = _model({(0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], events, ("rts",), _regs())})
    text = frameprog.emit(m)
    assert "inputs { raster }" in text
    assert " zp_FB: u8" in text and "m_D012" not in text.split("proc")[0].split("inputs")[0]


def test_indexed_state_arrays_and_sid_arrays_excluded():
    zx = ("op", "INT_ZEXT", (("reg", 1),), 2)
    arr = ("op", "INT_ADD", (zx, ("const", 0x1500, 2)), 2)
    sid = ("op", "INT_ADD", (zx, ("const", 0xD400, 2)), 2)
    events = [("st", arr, ("const", 1, 1)), ("st", sid, ("const", 2, 1))]
    m = _model({(0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], events, ("rts",), _regs())})
    text = frameprog.emit(m)
    assert " m_1500: u8[]" in text
    assert "sid.v1.freq_lo: " not in text


def test_declared_tables_and_aliases_carry_over():
    model, ev = _decl_player()
    text = frameprog.emit(model)
    assert "table m_1400[4]:" in text  # datadecl content reused verbatim
    assert "table m_1480[8] stride 2 +m_1481:" in text
    assert "alias ptr_0060 = zp_60" in text  # rung (d) fused the pair: one name
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert " ptr_0060: u16" in text and " zp_60: u8" not in text
    assert "ptr_0060:2 = " in text and "zp_60 = " not in text
    assert " m_1400" not in text.split("data {")[0]  # table cells are not state
    assert not _ANNOT.search(text)
    frames = F.frames_from_walker(S.Walker(model), 14)
    assert [(r, v) for f in frames for r, v in f] == [(r, v) for _c, r, v in ev.wlog]


def _fuzz_model(player):
    """Decompile a synthetic fuzz player into a committed model."""
    mem = bytearray(0x10000)
    for a, v in player.image_data().items():
        mem[a] = v
    init = player.init_org if player.init is not None else 0x0F00
    if player.init is None:
        mem[0x0F00] = 0x60
    model, _ev = S.decompile(mem, init, player.org, player.frames)
    return model


@pytest.mark.parametrize("p", G.players(3), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_fuzz_players_emit_annotation_free_and_project(p):
    model = _fuzz_model(p)
    text = frameprog.emit(model)
    assert text.startswith("frameprog 1\n") and not _ANNOT.search(text)
    assert frameprog.dumps(frameprog.loads(text)) == text  # M-FP2 canonical fixpoint
    frames = F.frames_from_walker(S.Walker(model), p.frames)
    assert F.loads(F.dumps(frames)) == F.canonical(frames)


def _round_trip(model):
    """``(text, program, model)`` rebuilt from the artifact text and nothing else."""
    text = frameprog.dumps(frameprog.program(model))
    rebuilt = frameprog.block_model(frameprog.loads(text))
    return text, frameprog.program(rebuilt), rebuilt


@pytest.mark.parametrize("p", G.players(2), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_artifact_rebuilds_the_program_it_was_emitted_from(p):
    """3a's pinning assert: the frameprog projection is total.

    Its absence is what let a projection silently produce a shorter, different
    program; equality of the re-emitted text, the walker log and the Gate FP
    verdict is the only thing that keeps that closed."""
    model = _fuzz_model(p)
    text, prog, rebuilt = _round_trip(model)
    assert frameprog.dumps(prog) == text
    assert S.Walker(rebuilt).run(p.frames) == S.Walker(model).run(p.frames)
    want = frameval.gate_fp(model, p.frames, frameprog.program(model))
    assert frameval.gate_fp(rebuilt, p.frames, prog) == want


def test_the_projection_is_total_on_the_jump_table_player():
    """3a's finding is discharged by supersession: there is no second projection.

    The sidprog projection that produced a silently shorter program is retired
    with its emit path (impl-plan housekeeping); what is left is the one
    projection, and the artifact it emits rebuilds it."""
    model = _fuzz_model(G.t_jump_table(np.random.default_rng(7)))
    text, prog, rebuilt = _round_trip(model)
    assert frameprog.dumps(prog) == text == frameprog.dumps(frameprog.program(model))
    assert S.Walker(rebuilt).run(8) == S.Walker(model).run(8)


def test_iota_pins_volatile_reads_and_matches_declared_inputs():
    """Every volatile read is pinned (frame, input, k); the set equals `inputs` (spec 4b)."""
    model = _fuzz_model(G.t_volatile(np.random.default_rng(7)))
    trace, frames = frameprog.iota(model, 12)
    assert trace, "the volatile player must read a modelled source"
    assert len(frames) == 12
    assert all(0 <= f < 12 for f, _n, _k in trace)
    for f, name in {(f, n) for f, n, _k in trace}:
        ks = sorted(k for g, m, k in trace if (g, m) == (f, name))
        assert ks == list(range(len(ks)))  # k is the read ordinal within the frame
    assert frameprog.declared_inputs(trace) == frameprog.program(model).inputs


def test_iota_is_empty_without_volatile_reads():
    """A player reading no volatile source declares none and records none."""
    model = _fuzz_model(G.t_table_index(np.random.default_rng(3)))
    trace, _frames = frameprog.iota(model, 8)
    assert trace == {} and frameprog.declared_inputs(trace) == []
    assert frameprog.program(model).inputs == []


def test_iota_run_reproduces_the_plain_walker_projection():
    """Pinning must not perturb execution: same frames as an unhooked walker."""
    model = _fuzz_model(G.t_volatile(np.random.default_rng(11)))
    _trace, frames = frameprog.iota(model, 10)
    assert F.canonical(frames) == F.canonical(F.frames_from_walker(S.Walker(model), 10))


def test_dynamic_flow_constructs_round_trip():
    """Computed jump/call surfaces: switch goto/call, bare targets, igoto."""
    a = E.reg(0)
    blocks = {
        (0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], [], ("jmpd", a), _regs()),
        (0x2000, 0x20): Block(0x2000, 0x20, [0x2000], [], ("jsr", None, 0x2002, a), _regs()),
        (0x2003, 0x4C): Block(0x2003, 0x4C, [0x2003], [], ("jmpind", 0x3000, None), _regs()),
        (0x2100, 0x60): Block(0x2100, 0x60, [0x2100], [], ("rts",), _regs()),
        (0x2200, 0x60): Block(0x2200, 0x60, [0x2200], [], ("rts",), _regs()),
    }
    mem0 = bytearray(0x10000)
    mem0[0x3000], mem0[0x3001] = 0x00, 0x21
    dyn = {0x1000: {0x2000}, 0x2000: {0x2100, 0x2200}}
    text = frameprog.emit(sidprog.BlockModel(mem0, 0x0F00, 0x1000, blocks, {}, dyn=dyn))
    for frag in ("goto (a)", "switch goto {", "switch call {", "\n    $2100\n", "igoto $3000"):
        assert frag in text, frag
    assert "call (a) ret $2002" in text
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_canonical_fixpoint_and_header_identity():
    """M-FP2: the text is readable and re-serialises byte-identically."""
    model, _ev = _decl_player()
    text = frameprog.emit(model)
    src, prog = frameprog.program(model), frameprog.loads(text)
    assert frameprog.dumps(prog) == text
    assert (prog.play, prog.init, prog.subtune) == (src.play, src.init, src.subtune)
    assert prog.prologue == src.prologue and prog.inputs == src.inputs
    assert prog.symbols == src.symbols and prog.state == src.state
    assert prog.data_decls == src.data_decls  # declarations round-trip exactly
    assert [(e, p, r) for e, p, r, _s in prog.procs] == [
        (e, p, r) for e, p, r, _s in src.procs
    ]  # entries, parameters and returns; the trees are the emitted (minimized) ones


def test_emission_deterministic():
    m1, _ev = _decl_player()
    m2, _ev = _decl_player()
    assert frameprog.emit(m1) == frameprog.emit(m1) == frameprog.emit(m2)


def test_registers_render_as_locals():
    model, _ev = _decl_player()
    text = frameprog.emit(model)
    body = text[text.index("sub_") :]
    assert not re.search(r"\n +[AXY] = ", body) and " u0 = " not in body
    frameprog.lint(text)


_LINT_DOC = "frameprog 1\nplay $1000\ninit $0F00\nsub_1000(%s) {\n  zp_10 = %s\n  ret\n}\n"


def test_lint_rejects_dangling_local():
    frameprog.lint(_LINT_DOC % ("a", "a"))
    with pytest.raises(ValueError, match="used before definition"):
        frameprog.lint(_LINT_DOC % ("", "(y + $01)"))


def _counter_loop_blocks(op0=0x60, tail=(), staged=None):
    """``x`` counting $02..$00 over ``m_1500``, then ``tail``; ``staged`` is ``a`` at entry."""
    dec = ("op", "INT_ADD", (E.reg(1), ("const", 0xFF, 1)), 1)
    sign = ("op", "INT_AND", (dec, ("const", 0x80, 1)), 1)
    cond = ("op", "INT_NOTEQUAL", (sign, ("const", 0, 1)), 1)
    arr = ("op", "INT_ADD", (("op", "INT_ZEXT", (E.reg(1),), 2), ("const", 0x1500, 2)), 2)
    init = _regs()
    init[1] = ("const", 2, 1)
    if staged is not None:
        init[0] = ("const", staged, 1)
    body = _regs()
    body[1] = dec
    return {
        (0x1000, 0xA2): Block(0x1000, 0xA2, [0x1000], [], ("goto", 0x1005), init),
        (0x1005, 0x99): Block(
            0x1005,
            0x99,
            [0x1005],
            [("st", arr, ("const", 1, 1))],
            ("br", 0, 0x1005, 0x100A, cond, None),
            body,
        ),
        (0x100A, op0): Block(0x100A, op0, [0x100A], list(tail), ("rts",), _regs()),
    }


def _counter_loop_model():
    return _model(_counter_loop_blocks())


def test_counter_loop_renders_as_for_range():
    text = frameprog.emit(_counter_loop_model())
    assert "for x in $02..$00 {" in text
    assert "m_1500" in text  # the row is state; no read names it, so its store retires
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_a_local_live_across_a_for_loop_is_not_pruned():
    """A for leaves by its own bottom, so its exit live set reaches its head that way.

    Where a loop leaves only by ``brk``, which carries the exit set for it, a for
    carried none: every name live after one and untouched inside it was pruned."""
    tail = (("st", ("const", 0xD404, 2), E.reg(0)), ("st", ("const", 0xD40B, 2), E.reg(0)))
    text = frameprog.emit(_model(_counter_loop_blocks(0x8D, tail, staged=0x07)))
    assert "for x in $02..$00 {" in text
    assert "sid.v1.ctrl = $07" in text and "sid.v2.ctrl = $07" in text
    frameprog.lint(text)


def test_parameter_and_return_inference():
    inc = ("op", "INT_ADD", (E.reg(0), ("const", 1, 1)), 1)
    callee = _regs()
    callee[0] = inc
    caller = _regs()
    caller[0] = ("const", 5, 1)
    blocks = {
        (0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], [], ("jsr", 0x2000, 0x1004, None), caller),
        (0x1005, 0x20): Block(0x1005, 0x20, [0x1005], [], ("jsr", 0x2000, 0x1007, None), _regs()),
        (0x1008, 0x85): Block(
            0x1008, 0x85, [0x1008], [("st", ("const", 0x00FB, 2), E.reg(0))], ("rts",), _regs()
        ),
        (0x2000, 0x69): Block(0x2000, 0x69, [0x2000], [], ("rts",), callee),
    }
    text = frameprog.emit(_model(blocks))
    assert "sub_2000(a) -> a {" in text
    assert "a = sub_2000($05)" in text and "a = sub_2000(a)" in text
    assert "zp_FB" in text  # declared state; no read names it, so its store retires
    frameprog.lint(text)
    assert frameprog.dumps(frameprog.loads(text)) == text


def _commando():
    return [
        pytest.param(path, sub, secs, id="Hubbard_Rob-Commando")
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == "Commando" and path.parent.name == "Hubbard_Rob"
    ]


@pytest.mark.parametrize("sid,subtune,secs", _commando())
def test_real_tune_frameprog_commando_gate(sid, subtune, secs):
    """Prototype Gate FP: the text is generated from the bit-exact verified
    model and the walker's projection is canonical and digi-clean; the full
    Gate FP law (independent frameprog evaluator) arrives with the M-FP2
    parser and is NOT faked here with a second interpreter."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    nframes = int(secs * 50)
    model, ev = S.decompile(mem, init, play, nframes, subtune)
    w = S.Walker(model)
    frames = F.frames_from_walker(w, nframes)
    assert w.wlog == ev.wlog  # model walker replay bit-exact vs the recorder
    text = frameprog.emit(model)
    assert text.startswith("frameprog 1\n") and not _ANNOT.search(text)
    assert "switch code[" not in text
    assert " ctr_5513: u8" in text
    assert "table pos_54EC[3] mut 0 1 2 observed:" in text  # a per-voice array, every entry written
    assert "table m_5428[192] stride 2 +m_5429 +m_542A +m_542B observed:" in text
    assert "for x in $02..$00 {" in text  # voice-state init counter loop
    # 16-clean (7.7) and canonical (7.9): the strided pitch table reads as u16 rows
    assert re.search(r"sid\.v1\.freq_lo\[\w+\]:2 = m_5428\[\w+\]:2", text)
    assert re.search(r"sid\.v1\.pw_lo\[\w+\]:2 = m_5591\[\w+\]:2", text)
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(";"))
    assert "sid.reg[" not in body
    assert not re.search(r"sid\.v1\.(freq|pw)_(lo|hi)\[\w+\] =", text)
    assert text.count("mem[") == 0  # rung (f) resolves every deref this tune has
    assert len(re.findall(r"\*ptr_\w+\[", text)) == 5  # the pointer-pair derefs, named
    assert "*ptr_005F[pos_54EF[x]]" in text  # a row index that is itself an indexed read
    assert not re.search(r"\n +[AXY] = ", text) and " u0 = " not in text
    assert frameprog.emit(model) == text  # emission is deterministic
    canon = F.canonical(frames)
    assert F.loads(F.dumps(frames)) == canon
    assert F.digi_frames(frames) == []


_IDX_DOC = (
    "frameprog 1\n"
    "play $1000\n"
    "init $0F00\n"
    "data {\n"
    " table m_1500[4] observed:\n"
    "  0A141E28\n"
    "}\n"
    "sub_1000() {\n"
    "  t0 = (m_1600 & $03)\n"
    "  sid.v1.freq_lo = m_1500[t0]\n"
    "  sid.v1.freq_hi = m_1500[(t0 + $01)]\n"
    "  sid.v1.pw_lo = m_1500[m_1600]\n"
    "  ret\n"
    "}\n"
)


def test_computed_table_read_is_an_indexed_access_not_a_raw_memref():
    """A computed read against a declaration names the table and its index."""
    prog = frameprog.loads(_IDX_DOC)
    text = frameprog.dumps(prog)
    assert "mem[" not in text
    assert "m_1500[(t0 + $01)]" in text and "m_1500[m_1600]" in text
    assert frameprog.dumps(frameprog.loads(text)) == text
    frames = frameval.eval_fp(prog, {}, 1)
    # mem0[$1500], [$1501], [$1500]; pw_hi comes along as the held lane of pw
    assert dict(frames[0][0]) == {0: 0x0A, 1: 0x14, 2: 0x0A, 3: 0x00}


@pytest.mark.parametrize(
    "addr,want",
    [
        (("op", "INT_ADD", (("loc", "t5"), ("const", 0x5429, 2)), 2), "m_5429[t5]"),
        (
            (
                "op",
                "INT_ADD",
                (
                    ("op", "INT_ZEXT", (("mem", ("const", 0x5518, 2), 1),), 2),
                    ("const", 0x5591, 2),
                ),
                2,
            ),
            "m_5591[m_5518]",
        ),
        (
            ("op", "INT_ADD", (("const", 0xD402, 2), ("loc", "v")), 2),
            "sid.reg[(zext2(v) + $0002):2]",
        ),
        (("op", "INT_ADD", (("loc", "t5"), ("const", 0x00F0, 2)), 2), None),  # zero page: no base
    ],
)
def test_index_rendering_covers_the_computed_shapes(addr, want):
    assert frameproc._membody(addr) == want


# ---- the inline that orphaned a use (docs/frameprog.md 7.10.14) -------------------
@pytest.mark.parametrize("loc", [("loc", "t16"), ("loc", "t16", 2)])
def test_a_local_is_a_use_at_every_width(loc):
    """A 16-bit local reads as ``("loc", name, 2)`` and is a use like any other.

    Counting only the bare 2-tuple missed exactly the words rung (d) mints, which
    is what let ``_find_use`` walk past a real use."""
    s = ("asg", "a6", ("mem", loc, 1))
    assert frameproc._use_count(s, "t16") == 1
    assert frameproc._locset(("mem", loc, 1)) == {"t16"}


# ---- the for counter the header binds (docs/frameprog.md 7.10.16) ----------------
def _rambo_note_loop():
    """Rambo ``$23C4``, ``rambload.asm`` ``NOTE1``/``n1sl2``, as statements.

    ``LDY #4 / LDA #0 / STA $D409,Y / LDA $2934,Y / STA $D409,Y / DEY / BPL`` with
    pass 3's rewrite already applied: init and step live in the header, so the body
    holds only the two stores. ``y = $00`` before it is the unrelated definition the
    escape used to reach."""
    idx = ("op", "INT_ZEXT", (("loc", "y"),), 2)
    dst = ("op", "INT_ADD", (idx, ("const", 0xD409, 2)), 2)
    src = ("mem", ("op", "INT_ADD", (idx, ("const", 0x2934, 2)), 2), 1)
    body = [("st", dst, ("const", 0, 1)), ("st", dst, src)]
    return dst, src, [("asg", "y", ("const", 0, 1)), ("for", "y", 4, 0, body)]


def test_a_for_counter_is_not_the_constant_in_force_before_the_loop():
    """``Defs._lookup`` must stop at the ``for`` header that binds the name.

    Reading past it folded ``$D409+y`` to ``$D409`` and ``$2934+y`` to ``$2934``:
    the corpus's one standing Gate FP divergence, and a written-to-the-wrong-cell
    store rather than a lost name."""
    _dst, _src, items = _rambo_note_loop()
    env = frameproc.Defs(items)
    body_env = frameproc.Defs(items[1][4], (env, 1), True)
    assert env._lookup("y", 1) is not None, "the outer definition is there to be found"
    assert body_env._lookup("y", 0) is None, "the header binds the counter over its body"


def test_canon_addrs_keeps_the_index_of_a_store_a_for_counter_indexes():
    """The one address-naming rule may not name an indexed cell by one seat of it.

    Both the SID store and the table read keep their index; before the fix each
    folded to the constant the pre-loop ``y = $00`` made of it."""
    dst, src, items = _rambo_note_loop()
    frameproc.canon_addrs(items)
    body = items[1][4]
    assert [s[1] for s in body] == [dst, dst]
    assert body[1][2] == src


def test_inline_does_not_orphan_a_width_2_use_by_folding_into_a_later_one():
    """``_find_use`` must not name the second use when the first is width-2.

    The shape delta-debugged out of ``Dribbling``: ``t16`` feeds a word-wide load
    and then a condition. Folding into the condition strands the load."""
    val = ("op", "INT_ZEXT", (("op", "INT_ADD", (("loc", "x"), ("const", 19, 1)), 1),), 2)
    items = [
        ("asg", "t16", val),
        ("asg", "a6", ("mem", ("loc", "t16", 2), 1)),  # width-2 use: scored 0 before the fix
        ("if", "if", ("loc", "t16"), [("asg", "a7", ("const", 1, 1))], []),
    ]
    info = frameproc._Info([(0x1000, items)], 0x1000)
    info.summarize()
    flow = frameproc._Flow(info, 0x1000)
    flow.liveout, flow.loop_head = {}, {}
    flow.run()
    ctx = frameproc._InlineCtx(info, 0x1000, flow.liveout, flow.loop_head)
    assert frameproc._find_use(items, 1, "t16", val, ctx) != 2, "folded past the width-2 use"


def test_a_factored_arm_rename_may_not_take_a_name_that_arm_binds():
    """Two arms that both assign one local: renaming into it merges two values.

    ``_factor_ifs`` unifies arm statements modulo a bijection over arm locals;
    with a rung-(d0) slot named in both arms the bijection could map the else
    arm's carry-in onto the slot, and the carry then read the sum it fed."""
    ctx = ({"s0", "t0"}, {"s0", "t1", "t2"}, {}, set())
    assert not frameproc._pair_names("s0", "t1", ctx)
    assert ctx[2] == {} and ctx[3] == set()
    assert frameproc._pair_names("t0", "t1", ctx)
    assert ctx[2] == {"t1": "t0"}


# ---- the state field's block extent (register-model-lift 2b) ----------------------
_STATE_DOC = "frameprog 1\nplay $1000\ninit $0F00\nstate {\n %s\n}\n"


def _extent_prog(state, extents, symbols=None):
    return frameprog.FrameProgram(0x1000, 0x0F00, state=state, symbols=symbols, extents=extents)


def test_block_extent_round_trips_as_an_int_keyed_side_map():
    """The extent rides beside the 4-tuple fields: cell address -> block bases."""
    state = [("zp_21", 2, False, []), ("zp_02", 1, False, [])]
    text = frameprog.dumps(_extent_prog(state, {0x21: (0x7338, 0x7401)}))
    assert " zp_21: u16 in m_7338, m_7401\n" in text and " zp_02: u8\n" in text
    prog = frameprog.loads(text)
    assert prog.extents == {0x21: (0x7338, 0x7401)} and prog.state == state
    assert frameprog.dumps(prog) == text  # M-FP2 over the new production


def test_block_extent_emits_ascending_and_before_the_observed_values():
    """Ascending block order and the clause's seat are the canonical form."""
    state = [("zp_21", 2, False, [0x01])]
    text = frameprog.dumps(_extent_prog(state, {0x21: (0x7401, 0x1000, 0x7338)}))
    assert " zp_21: u16 in m_1000, m_7338, m_7401 observed $01\n" in text
    prog = frameprog.loads(text)
    assert prog.extents == {0x21: (0x1000, 0x7338, 0x7401)}
    assert frameprog.dumps(prog) == text


def test_block_extent_is_spelled_through_the_symbol_table():
    """Both ends of the clause are cell names, so an alias stands for either."""
    state = [("ptr_0021", 2, False, [])]
    text = frameprog.dumps(
        _extent_prog(state, {0x21: (0x7338,)}, {0x21: "ptr_0021", 0x7338: "song"})
    )
    assert " ptr_0021: u16 in song\n" in text and "alias song = m_7338" in text
    prog = frameprog.loads(text)
    assert prog.extents == {0x21: (0x7338,)}
    assert frameprog.dumps(prog) == text


def test_a_field_without_an_extent_emits_exactly_what_it_did():
    """The clause and its note are the whole delta: a program with none is unmoved."""
    state = [("zp_21", 2, False, [])]
    plain = frameprog.dumps(_extent_prog(state, {}))
    assert " zp_21: u16\n" in plain and frameprog._EXTENT_NOTE[0] not in plain
    note = "\n".join(frameprog._EXTENT_NOTE) + "\n"
    got = frameprog.dumps(_extent_prog(state, {0x21: (0x7338,)}))
    assert got.replace(note, "").replace(" in m_7338", "") == plain


def test_the_in_keyword_serves_both_the_extent_and_the_for_range():
    """One contextual-lexer keyword: `in` opens an extent and a for-range alike."""
    text = _STATE_DOC % "zp_21: u16 in m_7338" + (
        "sub_1000() {\n  for x in $02..$00 {\n    zp_02 = x\n  }\n  ret\n}\n"
    )
    prog = frameprog.loads(text)
    assert prog.extents == {0x21: (0x7338,)}
    assert "for x in $02..$00 {" in frameprog.dumps(prog)


@pytest.mark.parametrize(
    "line,msg",
    [
        ("zp_21: u8 in m_7338", "a block extent is a u16 field's"),
        ("zp_21: u16[] in m_7338", "a block extent is a u16 field's"),
        ("zp_21: u16 in nope", "not a canonical cell name"),
    ],
)
def test_a_block_extent_is_a_scalar_u16_field_naming_canonical_cells(line, msg):
    """An extent is a pointer's: a byte field, an array or an unnamed block refuses."""
    with pytest.raises(ValueError, match=msg):
        frameprog.loads(_STATE_DOC % line)


# ---- ported from the retired sidprog emit path (the laws still bind) -----------
def _one_block(events, term=("rts",), regs=None, blocks=None, mem0=None):
    mem0 = mem0 if mem0 is not None else bytearray(0x10000)
    mem0[0x1000] = mem0[0x1000] or 0xA9
    blk = Block(0x1000, mem0[0x1000], [0x1000], events, term, regs or _regs())
    blocks = dict(blocks or {})
    blocks[(0x1000, mem0[0x1000])] = blk
    return frameprog.emit(sidprog.BlockModel(mem0, 0x0F00, 0x1000, blocks, {}))


def _indexed(base, reg):
    zx = ("op", "INT_ZEXT", (("reg", reg),), 2)
    return ("op", "INT_ADD", (zx, ("const", base, 2)), 2)


def test_single_use_load_inlines_at_its_use_site():
    """``_stmt_view``: the load line vanishes into the consumer that reads it."""
    text = _one_block(
        [
            ("cyc", 2),
            ("ld", 0, _indexed(0x5591, 2)),
            ("cyc", 4),
            ("pen", "ax", ("const", 0x5591, 2), ("reg", 2)),
            ("cyc", 4),
            ("st", ("const", 0x01FD, 2), ("op", "INT_SUB", (("uni", 0, 1), ("reg", 0)), 1)),
        ]
    )
    assert "  m_01FD = (m_5591[y] - a)" in text
    assert not re.search(r"\bw\d = ", text)


@pytest.mark.parametrize(
    "addr,line",
    [
        (("const", 0xD012, 2), "w0 = m_D012"),  # a volatile cell
        (_indexed(0xD400, 1), "w0 = sid.reg[x]"),  # index window reaches $D41B/$D41C
    ],
)
def test_a_volatile_or_near_volatile_load_keeps_its_line(addr, line):
    """``_ld_safe``: a read that may hit a volatile cell may not move."""
    text = _one_block([("ld", 0, addr), ("st", ("const", 0x00FB, 2), ("uni", 0, 1))])
    assert "  %s" % line in text


_FB = ("mem", ("const", 0xFB, 2), 1)


@pytest.mark.parametrize(
    "pol,cond,line",
    [
        (
            1,
            ("op", "INT_EQUAL", (("op", "INT_SUB", (_FB, ("reg", 0)), 1), _Z), 1),
            "if (zp_FB == a) goto ($1000) else $1005",
        ),
        (
            0,
            (
                "op",
                "INT_NOTEQUAL",
                (("op", "INT_ADD", (("reg", 0), ("const", 0xF8, 1)), 1), _Z),
                1,
            ),
            "ifnot (a != $08) goto ($1000) else $1005",
        ),
    ],
)
def test_a_zero_compare_canonicalizes_to_a_direct_compare(pol, cond, line):
    """``_canon_cond``: sub/add compare-to-zero becomes the direct compare."""
    text = _one_block([], term=("br", pol, None, 0x1005, cond, ("const", 0x1000, 2)))
    assert "  %s" % line in text


def test_the_canonicalization_is_width_guarded():
    """A compare whose operands are wider than the zero it tests stays as written."""
    wide = ("op", "INT_SUB", ((("uni", 0, 2)), ("uni", 1, 2)), 2)
    cond = ("op", "INT_EQUAL", (wide, _Z), 1)
    assert sidprog._canon_cond(cond) is cond
    narrow = ("op", "INT_EQUAL", (("op", "INT_SUB", (_FB, ("reg", 0)), 1), _Z), 1)
    assert sidprog._canon_cond(narrow) == ("op", "INT_EQUAL", (_FB, ("reg", 0)), 1)


@pytest.mark.parametrize(
    "term,line",
    [
        ((1, 0x2000, 0x1002), " if (a == $01) unobserved $2000"),
        ((1, 0x1002, 0x2000), " if (a != $01) unobserved $2000"),  # the closer collapses
    ],
)
def test_a_never_serialized_branch_side_emits_the_frontier_marker(term, line):
    """No goto and no label: an unobserved edge is a fault, not a destination."""
    cond = ("op", "INT_EQUAL", (E.reg(0), ("const", 1, 1)), 1)
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xD0
    text = _one_block(
        [],
        term=("br", term[0], term[1], term[2], cond, None),
        blocks={(0x1002, 0x60): Block(0x1002, 0x60, [0x1002], [], ("rts",), _regs())},
        mem0=mem0,
    )
    assert line in text
    assert "goto $2000" not in text and "$2000:" not in text
    assert frameprog.dumps(frameprog.loads(text)) == text


def _flow_blk(pc, term):
    return Block(pc, 0, [pc], [], term, _regs())


def _call_model(callers):
    blocks = {(0x2000, 0): _flow_blk(0x2000, ("rts",))}
    for i, pc in enumerate(callers):
        blocks[(pc, 0)] = _flow_blk(pc, ("jsr", 0x2000, pc + 2, None))
        nxt = callers[i + 1] if i + 1 < len(callers) else None
        blocks[(pc + 3, 0)] = _flow_blk(pc + 3, ("goto", nxt) if nxt is not None else ("rts",))
    return sidprog.BlockModel(bytearray(0x10000), 0x0F00, callers[0], blocks, {})


def test_a_sole_static_call_site_owns_the_callee_body():
    """``_model_trees``: one caller inlines the callee, two keep it a procedure."""
    one = frameprog.emit(_call_model([0x1000]))
    assert "  call $2000 ret $1002 {" in one and "sub_2000(" not in one
    assert frameprog.dumps(frameprog.loads(one)) == one
    two = frameprog.emit(_call_model([0x1000, 0x1100]))
    assert "sub_2000() {" in two and two.count("  sub_2000()\n") == 2
    assert frameprog.dumps(frameprog.loads(two)) == two


_DECL_TRUTH = {  # the declaration studies, as the frame program spells them
    "Hubbard_Rob-Commando": [
        "table m_5428[192] stride 2 +m_5429 +m_542A +m_542B observed:",
        "table m_56F9[3] lo m_56FC -> $576B..$57EC observed:",
        "table m_56FC[3] hi m_56F9 -> $576B..$57EC observed:",
        " stride 8 ",  # instrument records at m_5591
        "stream m_576B[",
        "via ptr_005D cmp $FE $FF observed:",
        "alias ptr_005D = zp_5D",  # rung (d) fused this pair: one name
        "alias pos_54EC = m_54EC",
        "alias pos_54ED = m_54ED",
        "alias pos_54EE = m_54EE",
    ],
    "Cadaver-Aces_High": [
        "table m_155C[52] lo m_1590",
        "stream m_15C4[",
        "via ptr_00FB_lo cmp $00 $FE $FF observed:",  # unfused: the lo half names it
        "alias ptr_00FB_lo = zp_FB",
        "alias ptr_00FB_hi = zp_FC",
    ],
    "Follin_Tim-Ghouls_n_Ghosts": [
        "stream m_7338[",
        "stream m_75F7[",
        "stream m_77A8[",
        "via ptr_0021_lo ",
        "via ptr_0023_lo ",
        "via ptr_0025_lo ",
        "alias ptr_0021_lo = zp_21",
    ],
}


def _decl_tunes():
    """The three studied tunes; the stem comparisons are what pins them."""
    out = []
    for path, sub, secs in corpus_params(HVSC):
        named = (
            path.stem == "Commando" or path.stem == "Aces_High" or path.stem == "Ghouls_n_Ghosts"
        )
        tid = "%s-%s" % (path.parent.name, path.stem)
        if named and tid in _DECL_TRUTH:
            out.append(pytest.param(path, sub, secs, id=tid))
    return out


@pytest.mark.parametrize("sid,subtune,secs", _decl_tunes())
def test_declarations_are_ground_truth_on_the_studied_tunes(sid, subtune, secs):
    """The declaration study, held on the artifact that carries it."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
    text = frameprog.emit(model)
    for frag in _DECL_TRUTH["%s-%s" % (sid.parent.name, sid.stem)]:
        assert frag in text, frag
    assert frameprog.dumps(frameprog.loads(text)) == text


def _pair_tune():
    """`Agent_X_II`: 3a's own finding, one cell declared in ``state`` and in ``data``."""
    return [
        pytest.param(path, sub, secs, id="%s-%s" % (path.parent.name, path.stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.parent.name == "Follin_Tim" and path.stem.startswith("Agent_X_II")
    ]


@pytest.mark.parametrize("sid,subtune,secs", _pair_tune())
def test_no_cell_is_declared_in_both_state_and_data(sid, subtune, secs):
    """3a's finding, discharged: the loose pair the rung carves leaves ``state { }``.

    ``_state_fields`` refuses a cell inside a declared span; ``_pair_tables`` carves
    new ones after it ran, so `$6923`/`$6925` were declared twice."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert "table m_6923[1] mut 0 lo m_6925:" in text  # the pair is declared, once
    assert "\n m_6923: u8\n" not in text and "\n m_6925: u8\n" not in text
    covered = {
        prog.symbols.get(a) or addr_name(a)
        for d in prog.data_decls
        for a in range(d["base"], d["base"] + d["size"])
    }
    assert not covered & {f[0] for f in prog.state}
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_drop_declared_takes_only_the_cells_a_declaration_covers():
    """The rule is the span, not the pair: a name no declaration covers stays."""
    decls = [{"base": 0x1000, "size": 2}, {"base": 0x6923, "size": 1}]
    state = [("m_1000", 1, False, []), ("m_1001", 1, False, []), ("m_6923", 1, False, [])]
    state.append(("zp_40", 1, False, []))
    assert sidprog._drop_declared(state, decls, {}) == [("zp_40", 1, False, [])]
    assert sidprog._drop_declared(state, [], {}) == state
    assert sidprog._drop_declared(state, decls, {0x1000: "voice"})[0][0] == "m_1000"
