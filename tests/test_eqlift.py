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
    assert {("lane_lo", 2), ("lane_hi", 2)} <= set(proved), "rung (d)'s lane law is unproved"


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


def _pk(h, l):
    return ("bor", ("shl", ("zext", h), ("num", 8, 1), 2), ("zext", l), 2)


def test_the_branch_borrow_chain_is_one_word_compare():
    """rung (d2)'s law over a predicate: what the chain tests, not what it computes."""
    al, ah, bl, bh = (("cell", a, 1, 0) for a in (0x10, 0x11, 0x20, 0x21))
    lo = ("ule", ("zext", bl), ("zext", al))
    chain = ("ule", ("add", ("zext", bh), ("zext", ("sub", ("num", 1, 1), lo, 1)), 2), ("zext", ah))
    got = _saturate(chain)
    assert ("ule", _pk(bh, bl), _pk(ah, al)) in got


def test_a_branch_on_a_flag_makes_it_a_constant_in_its_arms():
    """The path condition's own law: a guard that is its own truth value is 0/1 below it."""
    cmps = [("op", mn, (("loc", "x"), ("loc", "y")), 1) for mn in sorted(eqlift.BIT_OPS)]
    assert all(eqlift.bit_valued(e) for e in cmps)
    assert eqlift.bit_valued(("op", "INT_AND", tuple(cmps[:2]), 1))
    assert not eqlift.bit_valued(("op", "INT_ADD", tuple(cmps[:2]), 1))
    assert not eqlift.bit_valued(("loc", "cflag"))  # with no defs nothing is known
    assert eqlift.bit_valued(("loc", "cflag"), {"cflag": cmps[0]})
    assert not eqlift.bit_valued(("loc", "a"), {"a": ("loc", "b"), "b": ("loc", "a")})


def test_every_flag_op_really_computes_zero_or_one():
    """The obligation the path condition rides on, discharged in QF_BV rather than asserted."""
    alg = eqlift._Z3Alg()
    x, y = alg.tvar("x", 1), alg.tvar("y", 1)
    ones = [fn(x, y) for fn in (alg.eq, alg.ne, alg.ult, alg.ule, alg.slt, alg.sge)]
    for got in ones + [alg.carry(x, y, 1), alg.bnot(x)]:
        s = z3.Solver()
        s.add(z3.UGT(got, 1))
        assert s.check() == z3.unsat, got


def test_the_narrowing_copy_is_a_term_in_both_algebras():
    """``trunc`` is ``zext``'s dual, so rung (d2)'s width-one ``COPY`` converts (step 4).

    The egg side round trips through the printer IR and back to pass 1; the Z3 side is
    checked to be the low byte, which is what a rule stated over it would be proved on."""
    ir = ("trunc", ("loc", "w0.1"))
    assert eqlift._parse_ir(str(eqlift._egg_of(ir, {}))) == ir
    assert eqlift.pass1_node(ir, [("loc", "w0")]) == ("op", "COPY", (("loc", "w0"),), 1)
    assert eqlift._ir_width(ir, {}) == 1
    assert eqlift._Printer({}).fmt(ir) == "trunc1(w0)"
    alg = eqlift._Z3Alg()
    x = alg.tvar("x", 1)
    s = z3.Solver()
    s.add(alg.trunc(alg.zext(x)) != x)
    assert s.check() == z3.unsat


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_emit_end_to_end(sid, subtune, secs):
    """The prototype's substrate lifts Commando: header, play sub, a representative
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
    """The substrate covers calls/switches over the whole model; deterministic."""
    model = _model(sid, subtune, secs)
    text, _ = eqlift_mem.emit(model)
    assert text.startswith("eqlift 0\n") and "state {" in text
    assert "sub_E001 {" in text and "sub_E536 {" in text and "sub_E578 {" in text
    assert "switch call {" in text and "call $" in text
    assert eqlift_mem.emit(model)[0] == text  # emission is deterministic
