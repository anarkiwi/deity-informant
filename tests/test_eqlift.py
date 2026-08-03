"""Equality-saturation lift: Z3 rule-admission gate, plus the whole-artifact
emit over the Commando/Krakout tunes (no targeted per-tune code)."""

from pathlib import Path

import pytest

pytest.importorskip("egglog")
pytest.importorskip("z3")

import z3

from deity_informant import eqlift
from deity_informant import eqlift_mem
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


def _model(sid, subtune, secs):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
    return model


def test_all_rules_z3_verified():
    proved = eqlift.verify_rules()
    assert len(proved) == sum(len(widths) for _n, widths, _b in eqlift.RULES)
    assert ("sign_ne", 1) in proved and ("carry_fuse", 2) in proved


def test_verification_rejects_a_wrong_rule():
    alg = eqlift._Z3Alg()
    x = alg.tvar("x", 1)
    s = z3.Solver()
    s.add(alg.add(x, alg.num(1, 1), 1) != x)  # x + 1 == x is not an equivalence
    assert s.check() != z3.unsat


def test_admitted_ruleset_builds_and_dedups():
    rs, names = eqlift.admitted_rules()
    assert rs is not None
    assert len(set(names.values())) == len(names)


def _saturate(term, iters=25):
    from egglog import EGraph

    rs, _names = eqlift.admitted_rules()
    eg = EGraph()
    h = eg.let("h", eqlift._egg_of(term, {}))
    eg.run(rs * iters)
    return {eqlift._parse_ir(str(x)) for x in eg.extract_multiple(h, 8)}


def test_a_guarded_rule_fires_only_inside_its_width():
    """``num_narrow`` is what lets the SBC borrow's widened constant meet its byte."""
    assert ("zext", ("num", 55, 1)) in _saturate(("num", 55, 2))
    assert not any(f[0] == "zext" for f in _saturate(("num", 0x1234, 2)))


def test_a_zero_carry_term_is_identically_zero():
    """``carry(x, $00)`` cannot stand as a chain's evidence: the rules erase it."""
    x = ("cell", 0x10, 1, 0)
    assert ("num", 0, 1) in _saturate(("carry", x, ("num", 0, 1), 1))
    assert ("num", 0, 1) not in _saturate(("carry", x, ("num", 1, 1), 1))


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_emit_end_to_end(sid, subtune, secs):
    """The whole-artifact emit lifts Commando: header, play sub, a representative
    cell-forward line, deterministically -- from generic rules, no per-tune code."""
    model = _model(sid, subtune, secs)
    text, extra = eqlift_mem.emit(model)
    assert extra is None
    assert text.startswith("eqlift 0\n") and "state {" in text
    assert ("sub_%04X {" % model.play) in text
    assert "ctr_5525 = (ctr_5525 + $01)" in text
    assert eqlift_mem.emit(model)[0] == text  # emission is deterministic


@pytest.mark.parametrize("sid,subtune,secs", _tune("Krakout", "Daglish_Ben"))
def test_krakout_emit_whole_artifact(sid, subtune, secs):
    """emit covers calls/switches over the whole model; deterministic."""
    model = _model(sid, subtune, secs)
    text, _ = eqlift_mem.emit(model)
    assert text.startswith("eqlift 0\n") and "state {" in text
    assert "sub_E001 {" in text and "sub_E536 {" in text and "sub_E578 {" in text
    assert "switch {" in text and "call $" in text
    assert eqlift_mem.emit(model)[0] == text  # emission is deterministic
