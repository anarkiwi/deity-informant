"""Redundant data-move pass: SID-shadow detection and provenance lift."""

from pathlib import Path

import pytest

from deity_informant import movefwd
from deity_informant import song_model as sm
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


def _freq_kinds(procs, model):
    kinds = set()
    for stmts in procs:
        kinds.update(d.kind for d in sm.recover(stmts, model).freq)
    return kinds


@pytest.mark.parametrize("sid,subtune", _tune("Krakout", "Daglish_Ben"))
def test_krakout_shadow_lifts_provenance(sid, subtune):
    """The $E686 shadow flush is detected and freq provenance leaves `other`."""
    model = _model(sid, subtune)
    lifted, shadows = movefwd.lifted_procs(model)
    assert shadows == {0xE686: 0xD400}
    base = _freq_kinds(movefwd.list_procs(model), model)
    assert base == {"other"} and "slide" in _freq_kinds(lifted, model)


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_has_no_shadow(sid, subtune):
    """Read-only pitch tables and scalar per-voice cells are not shadows."""
    model = _model(sid, subtune)
    lifted, shadows = movefwd.lifted_procs(model)
    assert shadows == {}
    assert "note" in _freq_kinds(lifted, model)
