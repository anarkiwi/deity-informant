"""Follin command-script lane decode: grammar, certification, discrimination."""

import types
from pathlib import Path

import pytest

from deity_informant import follin_script as fscript
from deity_informant import opdispatch as OD
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

GRAM = fscript.Grammar(
    arity={0x83: 1, 0x84: 1, 0x86: 0, 0x87: 2, 0x8A: 2, 0x8B: 0, 0x8D: 1},
    escape={0x85: OD.Escape(3, 2, 1, frozenset(range(0x80)))},
)


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


def _put(mem, addr, *bs):
    for i, b in enumerate(bs):
        mem[addr + i] = b


def _script_model(mem, reads):
    return types.SimpleNamespace(mem0=bytes(mem), reads={(0, a) for a in reads})


def test_decode_seg_grammar_note_rawsid_call_ret():
    """A synthetic segment decodes note, gatelen, rawsid, call, ret with call targets."""
    mem = bytearray(0x10000)
    _put(mem, 0x1000, 0x30, 0x14)  # note 0x30 dur 0x14
    _put(mem, 0x1002, 0x83, 0x10)  # gatelen
    _put(mem, 0x1004, 0x85, 0x04, 0x0F, 0x18, 0x0F, 0x80)  # rawsid, 2 pairs, terminator
    _put(mem, 0x100A, 0x8A, 0x00, 0x20)  # call $2000
    _put(mem, 0x100D, 0x8B)  # ret
    reads = set(range(0x1000, 0x1010))
    ops, calls = fscript._decode_seg(bytes(mem), 0x1000, reads, set(), set(), GRAM)
    assert [o.name for o in ops] == ["note", "gatelen", "rawsid", "call", "ret"]
    assert ops[0].args == (0x30, 0x14)
    assert ops[2].args == ((0x04, 0x0F), (0x18, 0x0F))
    assert calls == [0x2000]


def test_decode_seg_sticky_duration_and_jump_and_bad():
    """durmode sets a sticky one-byte duration, jump follows target, unknown op is bad."""
    mem = bytearray(0x10000)
    _put(mem, 0x1000, 0x84, 0x08, 0x40, 0x86)  # durmode 8, note 0x40 (sticky), stop
    reads = set(range(0x1000, 0x1004))
    ops, _c = fscript._decode_seg(bytes(mem), 0x1000, reads, set(), set(), GRAM)
    assert [o.name for o in ops] == ["durmode", "note", "stop"]
    assert ops[1].args == (0x40, 8)  # dur came from the sticky durmode
    jm = bytearray(0x10000)
    _put(jm, 0x4000, 0x87, 0x10, 0x40)  # jump $4010
    _put(jm, 0x4010, 0x86)
    ops2, _ = fscript._decode_seg(
        bytes(jm), 0x4000, {0x4000, 0x4001, 0x4002, 0x4010}, set(), set(), GRAM
    )
    assert [o.name for o in ops2] == ["jump", "stop"]
    bad = bytearray(0x10000)
    bad[0x5000] = 0x95  # not in the grammar
    ops3, _ = fscript._decode_seg(bytes(bad), 0x5000, {0x5000}, set(), set(), GRAM)
    assert ops3[0].name == "bad" and ops3[0].args == (0x95,)


def test_decode_certifies_and_expands_patterns():
    """Top segment plus its call target decode; consumed bytes are all observed reads."""
    mem = bytearray(0x10000)
    _put(mem, 0x1000, 0x30, 0x14)
    _put(mem, 0x1002, 0x83, 0x10)
    _put(mem, 0x1004, 0x85, 0x04, 0x0F, 0x80)
    _put(mem, 0x1008, 0x8A, 0x00, 0x20)  # call $2000
    _put(mem, 0x100B, 0x8B)
    _put(mem, 0x2000, 0x50, 0x0A)
    _put(mem, 0x2002, 0x86)  # stop
    reads = set(range(0x1000, 0x1010)) | set(range(0x2000, 0x2004))
    model = _script_model(mem, reads)
    top, patterns, consumed, certified = fscript._decode(model, 0x1000, GRAM)
    assert certified and [o.name for o in top][:2] == ["note", "gatelen"]
    assert 0x2000 in patterns and [o.name for o in patterns[0x2000]] == ["note", "stop"]
    assert consumed <= reads
    partial = _script_model(mem, reads - {0x2000, 0x2001})  # target not observed fetched
    _t, pats, _c, _cert = fscript._decode(partial, 0x1000, GRAM)
    assert 0x2000 not in pats  # unreachable pattern is not decoded


def test_decode_and_script_bases(monkeypatch):
    """A zp pointer pair resolves to a base and decode returns a certified Script."""
    mem = bytearray(0x10000)
    mem[0xFB], mem[0xFC] = 0x00, 0x10  # pointer -> $1000
    _put(mem, 0x1000, 0x30, 0x14)
    _put(mem, 0x1002, 0x83, 0x10)
    _put(mem, 0x1004, 0x8D, 0x41)  # wave
    _put(mem, 0x1006, 0x86)  # stop
    reads = set(range(0x1000, 0x1008))
    model = _script_model(mem, reads)
    monkeypatch.setattr(
        fscript.streams, "streams", lambda m: [{"kind": "pointer", "base": ((0xFB,), (0xFC,))}]
    )
    monkeypatch.setattr(fscript, "grammar", lambda m: GRAM)
    assert fscript.script_bases(model) == {(0xFB, 0xFC): 0x1000}
    scripts = fscript.decode(model)
    assert len(scripts) == 1 and scripts[0].base == 0x1000 and scripts[0].certified


def test_escape_that_runs_away_or_is_not_pairs_reads_bad():
    """A decoded length with no terminator, or a non-pair stride, is not decoded."""
    mem = bytearray(0x10000)
    mem[0x1000], mem[0x1002] = 0x85, 0x80
    reads = set(range(0x1000, 0x1100))
    endless = fscript.Grammar({}, {0x85: OD.Escape(3, 2, 1, frozenset(range(0x100)))})
    ops, _c = fscript._decode_seg(bytes(mem), 0x1000, reads, set(), set(), endless)
    assert [o.name for o in ops] == ["bad"]
    wide = fscript.Grammar({}, {0x85: OD.Escape(2, 3, 1, frozenset(range(0x80)))})
    ops2, _c = fscript._decode_seg(bytes(mem), 0x1000, reads, set(), set(), wide)
    assert [o.name for o in ops2] == ["bad"]


def test_operator_outside_the_named_set_still_decodes_by_its_arity():
    """Names are labels; a recovered length decodes an op the name table lacks."""
    mem = bytearray(0x10000)
    _put(mem, 0x1000, 0x95, 0x07, 0x86)
    ops, _c = fscript._decode_seg(
        bytes(mem), 0x1000, set(range(0x1000, 0x1010)), set(), set(), fscript.Grammar({0x95: 1}, {})
    )
    assert (ops[0].name, ops[0].args) == ("op95", (0x07,))


def test_fmt_op_and_render():
    ops = [
        fscript.Op(0x1000, "note", (0x30, 20)),
        fscript.Op(0x1004, "rawsid", ((0x04, 0x0F), (0x18, 0x0F))),
        fscript.Op(0x100A, "call", (0x00, 0x20)),
        fscript.Op(0x100D, "ret", ()),
        fscript.Op(0x100E, "gatelen", (0x10,)),
    ]
    assert fscript._fmt_op(ops[0]) == "note 30 dur=20"
    assert fscript._fmt_op(ops[1]) == "rawsid $D404=0F $D418=0F"
    assert fscript._fmt_op(ops[2]) == "call $2000"
    assert fscript._fmt_op(ops[3]) == "ret"
    assert fscript._fmt_op(ops[4]) == "gatelen 10"
    script = fscript.Script((0xFB, 0xFC), 0x1000, ops, {0x2000: [ops[3]]}, set(), True)
    text = fscript.render(script)
    assert "base $1000" in text and "1000: note 30 dur=20" in text
    assert "pattern $2000:" in text and "  100D: ret" in text


@pytest.mark.parametrize("sid,subtune", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_three_certified_scripts(sid, subtune):
    """Three per-voice scripts decode, all certified, matching the known grammar."""
    scripts = fscript.decode(_model(sid, subtune))
    assert len(scripts) == 3 and all(s.certified for s in scripts)
    v1 = next(s for s in scripts if s.base == 0x7338)
    assert [o.name for o in v1.ops[:4]] == ["gatelen", "gateoff", "rawsid", "wave"]
    note = next(o for o in v1.ops if o.name == "note")
    assert note.args == (0x5F, 42)  # study §6: note 5F dur=42


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_not_a_script_player(sid, subtune):
    """A counter/row driver yields no certified Follin script (the discriminator)."""
    assert fscript.decode(_model(sid, subtune)) == []


@pytest.mark.parametrize("sid,subtune", _tune("Krakout", "Daglish_Ben"))
def test_krakout_not_a_script_player(sid, subtune):
    """A shadow-buffer driver yields no certified Follin script."""
    assert fscript.decode(_model(sid, subtune)) == []
