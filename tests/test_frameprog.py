"""frameprog emitter: annotation-free surface, state/inputs/data header,
opcode-cell switches on state variables, and the prototype Gate FP check
(projection of the verified model walker; the independent evaluator is M-FP2)."""

import re
from pathlib import Path

import pytest

from deity_informant import expr as E
from deity_informant import framelog as F
from deity_informant import frameprog
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
    assert "M-FP3" in text and "M-FP4" in text  # fusion/unification declared absent
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
    assert "sid.v1.freq_lo = m_1500" in text  # pen-free single-use load inlines
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
    assert "if (A == $01) unobserved $2000" in text
    assert "@t" not in text


def test_opcode_cell_renders_as_state_variable_switch():
    blocks = {
        (0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], [], ("rts",), _regs()),
        (0x1000, 0xEA): Block(0x1000, 0xEA, [0x1000], [], ("rts",), _regs()),
    }
    text = frameprog.emit(_model(blocks, dispatch={0x1000: {0xA9, 0xEA}}))
    assert "switch m_1000 {" in text and "code[" not in text
    assert "case $A9: {" in text and "case $EA: {" in text
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
    assert "alias ptr_0060_lo = zp_60" in text
    assert " ptr_0060_lo: u8" in text and " zp_60: u8" not in text
    assert "ptr_0060_lo = " in text and "zp_60 = " not in text
    assert " m_1400" not in text.split("data {")[0]  # table cells are not state
    assert not _ANNOT.search(text)
    frames = F.frames_from_walker(S.Walker(model), 14)
    assert [(r, v) for f in frames for r, v in f] == [(r, v) for _c, r, v in ev.wlog]


@pytest.mark.parametrize("p", G.players(3), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_fuzz_players_emit_annotation_free_and_project(p):
    mem = bytearray(0x10000)
    for a, v in p.image_data().items():
        mem[a] = v
    init = p.init_org if p.init is not None else 0x0F00
    if p.init is None:
        mem[0x0F00] = 0x60
    model, _ev = S.decompile(mem, init, p.org, p.frames)
    text = frameprog.emit(model)
    assert text.startswith("frameprog 0\n") and not _ANNOT.search(text)
    frames = F.frames_from_walker(S.Walker(model), p.frames)
    assert F.loads(F.dumps(frames)) == F.canonical(frames)


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
    assert " ctr_5513: u8" in text and " pos_54EC: u8[]" in text
    assert "table m_5428[192] stride 2 +m_5429 +m_542A +m_542B observed:" in text
    canon = F.canonical(frames)
    assert F.loads(F.dumps(frames)) == canon
    assert F.digi_frames(frames) == []
