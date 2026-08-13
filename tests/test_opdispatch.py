"""``opdispatch``: the shape on a miniature, and the Follin table's discharge.

``TRANSCRIBED`` is ``follin_script._ARITY`` as it stood until stage 3d -- 20
lengths hand-copied from docs/follin-dispatch-study.md §3 -- kept as the
discharge witness the recovery must reproduce op for op on that family.
"""

from pathlib import Path

import pytest

from deity_informant import follin_script as fscript
from deity_informant import opdispatch as OD
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

TRANSCRIBED = {
    0x80: 3,
    0x81: 0,
    0x82: 1,
    0x83: 1,
    0x84: 1,
    0x86: 0,
    0x87: 2,
    0x88: 8,
    0x89: 0,
    0x8A: 2,
    0x8B: 0,
    0x8C: 1,
    0x8D: 1,
    0x8E: 4,
    0x8F: 4,
    0x90: 1,
    0x91: 3,
    0x92: 1,
    0x93: 0,
    0x94: 2,
}
REWRITES = {0x87, 0x8A}  # arms that rewrite the pointer: Y never counts the last byte

INIT, PLAY, SCRIPT = 0x0F00, 0x1000, 0x3000
_MINI = {
    INIT: [0xA9, 0x00, 0x85, 0x20, 0xA9, 0x30, 0x85, 0x21, 0x60],
    0x1000: [0x98, 0x18, 0x65, 0x20, 0x85, 0x20, 0x90, 0x02, 0xE6, 0x21],
    0x100A: [0xA0, 0x00, 0xB1, 0x20, 0x10, 0x11, 0xC8, 0xAA],
    0x1012: [0xBD, 0x80, 0x1F, 0x8D, 0x1F, 0x10, 0xBD, 0x84, 0x1F, 0x8D, 0x20, 0x10],
    0x101E: [0x4C, 0x30, 0x10],
    0x1021: [0x60],
    0x1030: [0xB1, 0x20, 0xC8, 0x85, 0x10, 0x4C, 0x00, 0x10],
    0x1038: [0xE6, 0x11, 0x4C, 0x00, 0x10],
    0x103D: [0xB1, 0x20, 0xAA, 0xC8, 0xB1, 0x20, 0x85, 0x21, 0x86, 0x20, 0x4C, 0x0A, 0x10],
    0x104A: [0xB1, 0x20, 0xAA, 0xC8, 0xB1, 0x20, 0xC8, 0x9D, 0x00, 0xD4]
    + [0xB1, 0x20, 0x10, 0xF4, 0xC8, 0x4C, 0x00, 0x10],
    0x2000: [0x30, 0x38, 0x3D, 0x4A],
    0x2004: [0x10, 0x10, 0x10, 0x10],
    SCRIPT: [0x80, 0x05, 0x81, 0x83, 0x04, 0x0F, 0x18, 0x0F, 0x80, 0x82, 0x0C, 0x30, 0x40, 0x08],
}


def _mini(hole=False):
    """A miniature script VM: one dispatch, split lo/hi tables, four arms.

    ``hole`` widens the tables by an unexecuted fifth slot pointing at an
    unwritten address, so the recovery meets an arm that does not lift."""
    mem = bytearray(0x10000)
    for a, bs in _MINI.items():
        mem[a : a + len(bs)] = bytes(bs)
    if hole:
        mem[0x1019] = 0x85  # hi table base $1F85: five slots per table
        mem[0x2000:0x200A] = bytes([0x30, 0x38, 0x3D, 0x4A, 0x60] + [0x10] * 5)
    return S.decompile(mem, INIT, PLAY, 4)[0]


def _tune(stem, parent):
    return [
        pytest.param(path, sub, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _model(sid, subtune):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    return S.decompile(mem, init, play, 200, subtune)[0]


def test_mini_site_is_read_off_the_dispatch_not_told():
    """Stream, entry Y, tables and operator range all come from the model."""
    (site,) = OD.sites(_mini())
    assert site.pc == 0x101E and site.stream == (0x20, 0x21)
    assert site.tables == (0x1F80, 0x1F84) and site.entry_y == 1
    assert site.ops == {0x80: 0x1030, 0x81: 0x1038, 0x82: 0x103D, 0x83: 0x104A}


def test_mini_arities_escape_and_the_control_arm():
    """Constant arities, the pointer-rewriting arm, and the decoded-length escape."""
    model = _mini()
    (site,) = OD.sites(model)
    arms = {op: OD.arm(model, site, op) for op in site.ops}
    assert {op: a.arity for op, a in arms.items()} == {0x80: 1, 0x81: 0, 0x82: 2, 0x83: None}
    assert arms[0x82].delta == 1, "the rewriting arm's Y delta is one short of its arity"
    assert arms[0x80].delta == 1 and arms[0x81].delta == 0
    esc = arms[0x83].escape
    assert (esc.first, esc.stride, esc.trailer) == (3, 2, 1)
    assert esc.cont == frozenset(range(0x80))
    assert OD.operators(model) == ({0x80: 1, 0x81: 0, 0x82: 2}, {0x83: esc}, {})


def test_mini_script_decodes_through_the_recovered_grammar():
    """The decoder consumes what the arms consume, escape included."""
    model = _mini()
    gram = fscript.grammar(model)
    ops, _calls = fscript._decode_seg(
        model.mem0, SCRIPT, set(range(SCRIPT, SCRIPT + 14)), set(), set(), gram
    )
    assert [(o.addr, o.args) for o in ops] == [
        (0x3000, (0x05,)),
        (0x3002, ()),
        (0x3003, ((0x04, 0x0F), (0x18, 0x0F))),
        (0x3009, (0x0C, 0x30)),
        (0x300C, (0x40, 0x08)),
    ]


def test_mini_unliftable_arm_is_a_named_refusal():
    """A table entry that does not lift refuses by name; the others still recover."""
    model = _mini(hole=True)
    (site,) = OD.sites(model)
    assert sorted(site.ops) == [0x80, 0x81, 0x82, 0x83, 0x84]
    arities, escapes, refusals = OD.operators(model)
    assert arities == {0x80: 1, 0x81: 0, 0x82: 2} and list(escapes) == [0x83]
    assert list(refusals) == [0x84] and "$1060" in refusals[0x84]


def test_no_dispatch_recovers_nothing():
    """A program with no SMC-operand dispatch yields no sites and no grammar."""
    mem = bytearray(0x10000)
    mem[INIT], mem[PLAY] = 0x60, 0x60
    model = S.decompile(mem, INIT, PLAY, 2)[0]
    assert OD.sites(model) == () and OD.operators(model) == ({}, {}, {})
    assert fscript.decode(model) == []


@pytest.mark.parametrize("sid,subtune", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_recovery_equals_the_transcription(sid, subtune):
    """The discharge: 20 arities recovered equal the hand table, $85 escapes."""
    arities, escapes, refusals = OD.operators(_model(sid, subtune))
    assert refusals == {}
    assert arities == TRANSCRIBED
    assert list(escapes) == [0x85]
    esc = escapes[0x85]
    assert (esc.first, esc.stride, esc.trailer) == (3, 2, 1)
    assert esc.cont == frozenset(range(0x80)), "the rawsid run ends at the first command byte"


@pytest.mark.parametrize("sid,subtune", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_sites_are_three_voice_copies(sid, subtune):
    """Three dispatches, one per voice stream, over the same 21-slot operator range."""
    sites = OD.sites(_model(sid, subtune))
    assert [s.pc for s in sites] == [0x6374, 0x6561, 0x6750]
    assert [s.stream for s in sites] == [(0x21, 0x22), (0x23, 0x24), (0x25, 0x26)]
    assert [s.tables for s in sites] == [(0x6C37, 0x6C76), (0x6C4C, 0x6C8B), (0x6C61, 0x6CA0)]
    for s in sites:
        assert sorted(s.ops) == list(range(0x80, 0x95)) and s.entry_y == 1


@pytest.mark.parametrize("sid,subtune", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_net_y_delta_falls_one_short_on_the_rewriting_arms(sid, subtune):
    """The catalog's net-Y-delta definition holds except where the arm rewrites."""
    model = _model(sid, subtune)
    site = OD.sites(model)[0]
    short = set()
    for op in sorted(TRANSCRIBED):
        got = OD.arm(model, site, op)
        assert got.arity == TRANSCRIBED[op], "$%02X" % op
        if got.delta != got.arity:
            short.add(op)
            assert got.delta == got.arity - 1
    assert short == REWRITES


@pytest.mark.parametrize("sid,subtune", _tune("Agent_X_II_The_Mad_Profs_Back", "Follin_Tim"))
def test_agent_x_ii_is_a_second_build_with_its_own_operator_set(sid, subtune):
    """Why the table was debt: the family's other exemplar has other lengths."""
    model = _model(sid, subtune)
    arities, escapes, refusals = OD.operators(model)
    sites = OD.sites(model)
    assert [s.pc for s in sites] == [0x69E7, 0x6CD4, 0x6FC3]
    assert all(sorted(s.ops) == list(range(0x80, 0x91)) for s in sites)
    assert arities[0x84] == 0 and TRANSCRIBED[0x84] == 1
    assert arities[0x88] == 4 and TRANSCRIBED[0x88] == 8
    assert list(refusals) == [0x87] and "not constant" in refusals[0x87]
    assert escapes[0x85].cont == frozenset(range(0x100)) - {0xFF}
