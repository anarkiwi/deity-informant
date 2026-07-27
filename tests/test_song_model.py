"""Song-model recovery over eqlift graphs: cadence counters (decrement/reload)
and freq-driver classification, grounded on Rob Hubbard's Commando."""

from pathlib import Path

import pytest

from deity_informant import eqlift as E
from deity_informant import eqlift_mem as M
from deity_informant import generators as G
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


def _walk_model(sid, subtune, secs):
    """Full-songlength model whose whole code path is observed and walkable."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
    return model


def test_step_and_cell_helpers():
    dec = ("op", "INT_ADD", (("mem", ("const", 0x5513, 2), 1), ("const", 0xFF, 1)), 1)
    inc = ("op", "INT_ADD", (("mem", ("const", 0x5525, 2), 1), ("const", 0x01, 1)), 1)
    assert sm._step(dec, {}) == (0x5513, "dec")
    assert sm._step(inc, {}) == (0x5525, "inc")
    assert sm._step(("op", "INT_AND", (("const", 0x1F, 1),), 1), {}) is None
    assert sm._cell(0x0027) and sm._cell(0x5513) and not sm._cell(0xD400) and not sm._cell(0x01)


def test_control_action_classifier():
    gate_off = ("op", "INT_AND", (("const", 0xFE, 1), ("mem", ("const", 0x54F8, 2), 1)), 1)
    gate_on = ("op", "INT_OR", (("mem", ("const", 0x5593, 2), 1), ("const", 0x01, 1)), 1)
    wave = ("op", "INT_AND", (("mem", ("const", 0x5593, 2), 1), ("loc", "ctr_5501")), 1)
    assert sm._control_action(gate_off) == "gate_off"
    assert sm._control_action(gate_on) == "gate_on"
    assert sm._control_action(wave) == "waveform"


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
    # control automaton: note-lifecycle states with guarded gate/waveform/AD-SR edges
    auto = m.control
    assert auto.states == ("off", "on")
    acts = {t.action for t in auto.transitions}
    assert {"gate_off", "waveform", "ad", "sr"} <= acts
    on = [t for t in auto.transitions if t.action == "waveform"]
    assert any("(m_5523 & $01)" in g for t in on for g in t.guards)  # flag-bit note trigger
    off = [t for t in auto.transitions if t.action == "gate_off"]
    assert off and all(t.to == "off" for t in off) and any(t.guards for t in off)


@pytest.mark.parametrize("sid,subtune,secs", _tune("Krakout", "Daglish_Ben"))
def test_krakout_freq_provenance_boundary(sid, subtune, secs):
    """Daglish flushes a SID shadow buffer behind a computed pitch base, so no
    freq store resolves to the pitch table, yet per-voice counters recover."""
    m = sm.analyze(_model(sid, subtune))
    assert m.counters and any(c.kind == "dec" for c in m.counters)
    assert m.freq and not any(d.kind == "note" for d in m.freq)


def test_self_step_and_cell_refs():
    add = ("op", "INT_ADD", (("mem", ("const", 0x5591, 2), 1), ("mem", ("const", 0x5507, 2), 1)), 1)
    d, st = sm._self_step(add, 0x5591, {})
    assert d == "add" and st == ("mem", ("const", 0x5507, 2), 1)
    sub = ("op", "INT_SUB", (("mem", ("const", 0x6F, 2), 1), ("mem", ("const", 0x73, 2), 1)), 1)
    assert sm._self_step(sub, 0x6F, {})[0] == "sub"
    assert sm._self_step(sub, 0x99, {}) is None  # no self read of $99
    assert sm._cell_refs(add, {}, set()) == {0x5591, 0x5507}


def test_verify_series_constant_accumulator():
    """A constant-step accumulator with mod-256 wrap regenerates every write past
    the two-sample seed, in a single run."""
    vals = [(128 + 22 * i) & 0xFF for i in range(20)]
    interp, total, runs = G.verify_series(vals)
    assert total == 20 and interp == 18 and runs == 2


def test_generator_canon_memory_forwarding():
    """Equality saturation forwards a PWM sweep's scratch-cell store chain, turning
    the opaque lifted load into the accumulator recurrence acc-step."""
    num = E.num
    m = M.mem0()
    m = M.store(m, num(0x5524, 1), E.band(E.cell(0x5507, 1, 0), num(0xE0, 1), 1), 1)
    step = M.sel(m, num(0x5524, 1), 1)
    m = M.store(m, num(0x01FD, 1), E.sub(E.cell(0x5591, 1, 0), step, 1), 1)
    pw = M.sel(m, num(0x01FD, 1), 1)
    pr = E._Printer({})
    assert pr.fmt(M.to_ir(str(pw))) == "m_01FD"
    assert pr.fmt(M.to_ir(M.extract(pw))) == "(m_5591 - (m_5507 & $E0))"


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_pwm_recovery(sid, subtune, secs):
    """PWM recovers as an additive accumulator m_5591 += idx_5507; forward
    evaluation regenerates the pw_lo plane within each note, filter static."""
    model = _walk_model(sid, subtune, secs)
    gens, cov = G.regenerate(model, int(secs * 50))
    pw = [a for a in gens.pw if a.plane == "pw_lo"]
    assert pw and pw[0].acc == 0x5591 and pw[0].direction == "add"
    assert pw[0].step[0][1] == "m_5507"
    v2 = [c for c in cov if c.plane == "pw_lo" and c.target == "v2"]
    assert v2 and v2[0].interpreted / v2[0].total >= 0.7
    assert gens.static.get(0x18) == 0x0F  # filter off, volume 15, static in sid-init


@pytest.mark.parametrize("sid,subtune,secs", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_filter_recovery(sid, subtune, secs):
    """Filter cutoff recovers as a downward accumulator zp_6F -= zp_73; forward
    evaluation regenerates cutoff_lo bit-exact over the full song, pw is a triangle."""
    model = _walk_model(sid, subtune, secs)
    gens, cov = G.regenerate(model, int(secs * 50))
    cut = [a for a in gens.filter if a.plane == "cutoff_lo"]
    assert cut and cut[0].acc == 0x6F and cut[0].direction == "sub"
    assert cut[0].step[0][1] == "zp_73"
    lo = [c for c in cov if c.plane == "cutoff_lo"]
    assert lo and lo[0].interpreted / lo[0].total >= 0.99
    pw = [a for a in gens.pw if a.plane == "pw_lo"]
    assert pw and pw[0].acc == 0x3F and "sub" in {d for d, _ in pw[0].step}
