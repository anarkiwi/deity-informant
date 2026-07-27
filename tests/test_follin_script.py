"""Follin command-script lane decode: grammar, certification, discrimination."""

from pathlib import Path

import pytest

from deity_informant import follin_script as fscript
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


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
