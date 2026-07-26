"""Song-model recovery over eqlift graphs: cadence counters (decrement/reload)
and freq-driver classification, grounded on Rob Hubbard's Commando."""

from pathlib import Path

import pytest

from deity_informant import song_model as sm
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _tune(stem, parent):
    return [
        pytest.param(path, sub, secs, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _model(sid, subtune):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, 200, subtune)
    return model


def test_step_and_cell_helpers():
    dec = ("op", "INT_ADD", (("mem", ("const", 0x5513, 2), 1), ("const", 0xFF, 1)), 1)
    inc = ("op", "INT_ADD", (("mem", ("const", 0x5525, 2), 1), ("const", 0x01, 1)), 1)
    assert sm._step(dec, {}) == (0x5513, "dec")
    assert sm._step(inc, {}) == (0x5525, "inc")
    assert sm._step(("op", "INT_AND", (("const", 0x1F, 1),), 1), {}) is None
    assert sm._cell(0x0027) and sm._cell(0x5513) and not sm._cell(0xD400) and not sm._cell(0x01)


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_cadence_and_freq_drivers(sid, subtune, secs):
    """The global tick divider reloads from the speed cell, note-duration and the
    free-running phase counter are recovered, and freq stores split note/slide."""
    model = _model(sid, subtune)
    m = sm.analyze(model)
    counters = {c.base: c for c in m.counters}
    assert counters[0x5513].kind == "dec" and counters[0x5513].reload == 0x5517
    assert counters[0x54F2].kind == "dec"
    assert counters[0x5525].kind == "inc"
    kinds = {d.kind for d in m.freq}
    assert "note" in kinds and "slide" in kinds
    assert any(d.pitch for d in m.freq)


@pytest.mark.parametrize("sid,subtune,secs", _tune("Krakout", "Daglish_Ben"))
def test_krakout_freq_provenance_boundary(sid, subtune, secs):
    """Daglish flushes a SID shadow buffer behind a computed pitch base, so no
    freq store resolves to the pitch table, yet per-voice counters recover."""
    m = sm.analyze(_model(sid, subtune))
    assert m.counters and any(c.kind == "dec" for c in m.counters)
    assert m.freq and not any(d.kind == "note" for d in m.freq)
