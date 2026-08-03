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
from deity_informant import sidprog
from deity_informant import structured as S
from deity_informant.c64 import load_psid
from deity_informant.structured import Block

import _fuzzgen as G

from _corpus import corpus_params
from test_sidprog import _decl_player

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

_ANNOT = re.compile(r"@\d|@t\d|@x\(|@xi\(|code\[")


def _regs():
    return [E.reg(i) for i in range(16)]


def _model(blocks, dispatch=None, mem0=None, play=0x1000):
    mem0 = mem0 if mem0 is not None else bytearray(0x10000)
    for pc, op0 in blocks:
        mem0[pc] = mem0[pc] or op0
    return sidprog.TextModel(mem0, 0x0F00, play, blocks, dispatch or {})


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
    assert text.startswith("frameprog 0\n") and not _ANNOT.search(text)
    assert frameprog.dumps(frameprog.loads(text)) == text  # M-FP2 canonical fixpoint
    frames = F.frames_from_walker(S.Walker(model), p.frames)
    assert F.loads(F.dumps(frames)) == F.canonical(frames)


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
    text = frameprog.emit(sidprog.TextModel(mem0, 0x0F00, 0x1000, blocks, {}, dyn=dyn))
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
    assert prog.procs == src.procs  # statement trees, parameters and returns too


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


_LINT_DOC = "frameprog 0\nplay $1000\ninit $0F00\nsub_1000(%s) {\n  zp_10 = %s\n  ret\n}\n"


def test_lint_rejects_dangling_local():
    frameprog.lint(_LINT_DOC % ("a", "a"))
    with pytest.raises(ValueError, match="used before definition"):
        frameprog.lint(_LINT_DOC % ("", "(y + $01)"))


def _counter_loop_model():
    dec = ("op", "INT_ADD", (E.reg(1), ("const", 0xFF, 1)), 1)
    sign = ("op", "INT_AND", (dec, ("const", 0x80, 1)), 1)
    cond = ("op", "INT_NOTEQUAL", (sign, ("const", 0, 1)), 1)
    arr = ("op", "INT_ADD", (("op", "INT_ZEXT", (E.reg(1),), 2), ("const", 0x1500, 2)), 2)
    init = _regs()
    init[1] = ("const", 2, 1)
    body = _regs()
    body[1] = dec
    blocks = {
        (0x1000, 0xA2): Block(0x1000, 0xA2, [0x1000], [], ("goto", 0x1005), init),
        (0x1005, 0x99): Block(
            0x1005,
            0x99,
            [0x1005],
            [("st", arr, ("const", 1, 1))],
            ("br", 0, 0x1005, 0x100A, cond, None),
            body,
        ),
        (0x100A, 0x60): Block(0x100A, 0x60, [0x100A], [], ("rts",), _regs()),
    }
    return _model(blocks)


def test_counter_loop_renders_as_for_range():
    text = frameprog.emit(_counter_loop_model())
    assert "for x in $02..$00 {" in text
    assert "m_1500[x] = $01" in text
    assert frameprog.dumps(frameprog.loads(text)) == text


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
    assert "zp_FB = a" in text
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
    assert text.startswith("frameprog 0\n") and not _ANNOT.search(text)
    assert "switch code[" not in text
    assert " ctr_5513: u8" in text
    assert "table pos_54EC[3] mut 0 1 2 observed:" in text  # a per-voice array, every entry written
    assert "table m_5428[192] stride 2 +m_5429 +m_542A +m_542B observed:" in text
    assert "for x in $02..$00 {" in text  # voice-state init counter loop
    # the note word: the hi lane rides a fused indexed u16 store (rung d, hi-first)
    assert re.search(r"sid\.v1\.freq_lo\[\w+\]:2 = \(\(zext2\(m_5429\[\w+\]\) << \$08\)", text)
    assert text.count("mem[") == 0  # rung (f) resolves every deref this tune has
    assert len(re.findall(r"\*ptr_\w+\[", text)) == 5  # the pointer-pair derefs, named
    assert "*ptr_005F[pos_54EF[x]]" in text  # a row index that is itself an indexed read
    assert not re.search(r"\n +[AXY] = ", text) and " u0 = " not in text
    assert frameprog.emit(model) == text  # emission is deterministic
    canon = F.canonical(frames)
    assert F.loads(F.dumps(frames)) == canon
    assert F.digi_frames(frames) == []


_IDX_DOC = (
    "frameprog 0\n"
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
        (("op", "INT_ADD", (("const", 0xD402, 2), ("loc", "v")), 2), "sid.v1.pw_lo[v]"),
        (("op", "INT_ADD", (("loc", "t5"), ("const", 0x00F0, 2)), 2), None),  # zero page: no base
    ],
)
def test_index_rendering_covers_the_computed_shapes(addr, want):
    assert frameproc._membody(addr) == want
