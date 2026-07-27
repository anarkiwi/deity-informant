"""Song-model recovery over eqlift graphs: cadence counters (decrement/reload)
and freq-driver classification, grounded on Rob Hubbard's Commando."""

import types
from pathlib import Path

import numpy as np
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


def _pitch_model():
    """A fake model carrying an interleaved ET pitch table at $2000 (36 words)."""
    mem = bytearray(0x10000)
    words = np.round(600 * (2 ** (1 / 12)) ** np.arange(36)).astype(int)
    for i, w in enumerate(words):
        mem[0x2000 + 2 * i] = int(w) & 0xFF
        mem[0x2000 + 2 * i + 1] = (int(w) >> 8) & 0xFF
    return types.SimpleNamespace(
        mem0=bytes(mem),
        data_decls=[{"base": 0x2000, "size": 72, "kind": "table"}],
        prologue=[(0x18, 0x0F), (0x15, 0x00), (0x00, 0x11)],
    )


def _mem(base, idx="i"):
    return ("mem", ("op", "INT_ADD", (("const", base, 2), ("loc", idx)), 2), 1)


def _recover_stmts():
    return [
        (
            "st",
            ("const", 0x0050, 2),
            (
                "op",
                "INT_ADD",
                (("mem", ("const", 0x0050, 2), 1), ("mem", ("const", 0x0051, 2), 1)),
                1,
            ),
        ),
        ("st", ("const", 0xD400, 2), _mem(0x2000)),  # freq_lo note lookup
        ("st", ("const", 0xD401, 2), _mem(0x2001)),  # freq_hi note lookup
        ("st", ("const", 0xD400, 2), ("mem", ("const", 0x0050, 2), 1)),  # freq slide
        (
            "st",
            ("const", 0x0060, 2),
            ("op", "INT_ADD", (("mem", ("const", 0x0060, 2), 1), ("const", 0xFF, 1)), 1),
        ),  # dec counter
        ("st", ("const", 0x0060, 2), ("mem", ("const", 0x0070, 2), 1)),  # reload
        (
            "st",
            ("const", 0xD402, 2),
            ("op", "INT_ADD", (("mem", ("const", 0x0050, 2), 1), ("const", 0x10, 1)), 1),
        ),  # pw_lo sweep
        (
            "if",
            "if",
            ("op", "INT_LESS", (("mem", ("const", 0x0060, 2), 1), ("const", 0x05, 1)), 1),
            [
                (
                    "st",
                    ("const", 0xD404, 2),
                    ("op", "INT_OR", (("mem", ("const", 0x0055, 2), 1), ("const", 0x01, 1)), 1),
                )
            ],
            [
                (
                    "st",
                    ("const", 0xD404, 2),
                    ("op", "INT_AND", (("mem", ("const", 0x0055, 2), 1), ("const", 0xFE, 1)), 1),
                )
            ],
        ),
        ("st", ("const", 0xD405, 2), ("const", 0x33, 1)),  # attack_decay
        ("st", ("const", 0xD406, 2), ("const", 0xF0, 1)),  # sustain_release
        (
            "st",
            ("const", 0xD415, 2),
            (
                "op",
                "INT_SUB",
                (("mem", ("const", 0x0080, 2), 1), ("mem", ("const", 0x0060, 2), 1)),
                1,
            ),
        ),  # cutoff_lo
    ]


def test_render_and_guard_forms():
    assert sm._render(5) == "5"
    assert sm._render(("const", 0x12, 1)) == "$12"
    assert sm._render(("loc", "x")) == "x"
    assert sm._render(("mem", ("const", 0x5523, 2), 1)) == "m_5523"
    assert sm._render(("mem", ("op", "INT_ADD", (("loc", "i"),), 2), 1)) == "mem[.]"
    assert sm._render(("op", "INT_AND", (("loc", "a"), ("const", 1, 1)), 1)) == "(a & $01)"
    assert sm._render(("op", "INT_MULT", (("loc", "a"),), 1)) == "?"
    assert sm._guard(("loc", "c"), True) == "c" and sm._guard(("loc", "c"), False) == "!c"


def test_guard_comparands_and_self_step_carry():
    gconds = [(("op", "INT_LESS", (("mem", ("const", 0x0060, 2), 1), ("const", 0xF0, 1)), 1), True)]
    cells, consts = sm._guard_comparands(gconds)
    assert cells == {0x0060} and 0xF0 in consts
    carry = (
        "op",
        "INT_ADD",
        (
            ("mem", ("const", 0x0050, 2), 1),
            ("op", "INT_CARRY", (("loc", "c"),), 1),
            ("mem", ("const", 0x0051, 2), 1),
        ),
        1,
    )
    d, st = sm._self_step(carry, 0x0050, {})
    assert d == "add" and st == ("mem", ("const", 0x0051, 2), 1)  # carry operand skipped


def test_transition_backtraces_source():
    val = ("op", "INT_OR", (("mem", ("const", 0x0055, 2), 1), ("const", 0x01, 1)), 1)
    tr = sm._transition("control", val, ("g",), {}, {})
    assert tr.role == "control" and tr.action == "gate_on" and tr.to == "on"
    ad = sm._transition("attack_decay", ("mem", ("const", 0x3000, 2), 1), (), {}, {})
    assert ad.action == "ad" and ad.to == "on" and ad.source == 0x3000
    assert sm._transition("sustain_release", ("const", 5, 1), (), {}, {}).action == "sr"


def test_sweep_refs_and_merge():
    updates = {0x50: [("add", ("const", 3, 1), ())]}
    gconds = [(("op", "INT_LESS", (("mem", ("const", 0x50, 2), 1), ("const", 0xF0, 1)), 1), True)]
    w = sm._sweep("pw_lo", {0x50, 0x60}, gconds, {0x50, 0x60}, updates, {0x60})
    assert w.acc == 0x50 and w.rate == 0x60 and 0xF0 in w.bounds
    assert sm._sweep("pw_lo", {0x99}, [], set(), {}, set()) is None  # no accumulator
    assert sm._refs_cell(("mem", ("const", 0x50, 2), 1), 0x50)
    assert not sm._refs_cell(("const", 1, 1), 0x50)
    a = sm.Sweep("pw_lo", 0x50, (("add", 1),), 0x60, (0x10,))
    b = sm.Sweep("pw_lo", 0x50, (("sub", 2),), None, (0x20,))
    merged = sm._merge_sweeps([a, b])
    assert len(merged) == 1 and merged[0].bounds == (0x10, 0x20)
    assert len(merged[0].updates) == 2


def test_probe_and_label_and_static_filter():
    model = _pitch_model()
    spans = [(0x2000, 0x2048)]
    probe = sm._probe("freq_lo", _mem(0x2000), {}, {}, spans)
    assert sm._in_pitch({0x2000}, spans) and probe[2]  # pitch provenance true
    note = sm._label(probe, set())
    assert note.kind == "note" and note.pitch
    slide = sm._label(("freq_lo", 0x50, False, True, False), {0x50})
    assert slide.kind == "slide"
    other = sm._label(("freq_lo", 0, False, False, False), set())
    assert other.kind == "other"
    assert sm._static_filter(model) == {0x18: 0x0F, 0x15: 0x00}  # only filter regs


def test_recover_full_model():
    model = _pitch_model()
    r = sm.recover(_recover_stmts(), model)
    assert {c.base: (c.kind, c.reload) for c in r.counters} == {0x0060: ("dec", 0x0070)}
    kinds = {(d.role, d.kind) for d in r.freq}
    assert ("freq_lo", "note") in kinds and ("freq_lo", "slide") in kinds
    assert any(d.pitch for d in r.freq)
    assert r.control.states == ("off", "on")
    assert {t.action for t in r.control.transitions} == {"gate_on", "gate_off", "ad", "sr"}
    assert r.pw and r.pw[0].plane == "pw_lo" and r.pw[0].acc == 0x50
    assert r.filter.cutoff and r.filter.cutoff[0].plane == "cutoff_lo"


def test_analyze_unions_over_procs(monkeypatch):
    model = _pitch_model()
    monkeypatch.setattr(sm.ann, "model_procs", lambda m: [_recover_stmts()])
    m = sm.analyze(model)
    assert m.counters and m.freq and m.pw
    assert m.filter.static == {0x18: 0x0F, 0x15: 0x00}
    assert {"gate_on", "gate_off", "ad", "sr"} <= {t.action for t in m.control.transitions}


def test_clock_classification():
    div = G._clock(sm.Counter(0x5513, "dec", 0x5517))
    lfo = G._clock(sm.Counter(0x5525, "inc", None))
    bare = G._clock(sm.Counter(0x54F2, "dec", None))
    assert div.role == "tick_divider" and lfo.role == "lfo_phase" and bare.role == "counter"


def test_accumulator_direction_and_canon_steps():
    tri = sm.Sweep("pw_lo", 0x5591, (("add", ("const", 5, 1)), ("sub", ("const", 5, 1))), 0x50, ())
    up = sm.Sweep("pw_lo", 0x5591, (("add", ("mem", ("const", 0x5507, 2), 1)),), None, (0x10,))
    assert G._accumulator(tri).direction == "triangle"
    acc = G._accumulator(up)
    assert acc.direction == "add" and acc.step == (("add", "m_5507"),)
    assert G._accumulator(sm.Sweep("pw_lo", 0x00, (), None, ())).direction == "?"


def test_triggers_from_control_automaton():
    trans = (
        sm.Transition("control", "gate_on", "on", 0x40, ("(m_5523 & $01)",)),
        sm.Transition("control", "gate_off", "off", 0, ("!c",)),
        sm.Transition("control", "waveform", "on", 0x50, ("(m_10 & $02)",)),
        sm.Transition("control", "waveform", "on", 0x50, ("plain",)),
    )
    kinds = [t.kind for t in G._triggers(sm.Automaton(("off", "on"), trans))]
    assert kinds == ["note_on", "gate_off", "flag_bit"]  # plain waveform edge yields no trigger


def test_to_egg_and_canon_cover_forms():
    assert G.canon(None) is None and G.raw(None) is None
    assert G.canon(("const", 0x12, 1)) == "$12"
    assert G.canon(("mem", ("const", 0x5507, 2), 1)) == "m_5507"
    load = ("mem", ("op", "INT_ADD", (("const", 0x5000, 2), ("loc", "i")), 2), 1)
    assert isinstance(G.raw(load), str)  # computed-address load
    zext = ("op", "INT_ZEXT", (("loc", "x"),), 2)
    cmp_ir = ("op", "INT_LESS", (("loc", "x"), ("const", 3, 1)), 1)
    assert isinstance(G._to_egg(zext), tuple) and G._to_egg(cmp_ir)[0] == "ult"


def test_series_extracts_register_column():
    canonical = [([(2, 0x10)],), ([(2, 0x20)],), ([(9, 0x00)],)]
    assert G._series(canonical, 0, 2) == [0x10, 0x20]


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
