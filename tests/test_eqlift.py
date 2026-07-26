"""Equality-saturation lift PoC: Z3-verified rules, synthetic exhibits, and the
solver-produced Commando/Krakout procedures (no targeted per-tune code)."""

from pathlib import Path

import pytest

pytest.importorskip("egglog")
pytest.importorskip("z3")

import z3

from deity_informant import eqlift
from deity_informant import frameprog
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


def _loc(n):
    return ("loc", n)


def test_sign_bit_exhibit_synthetic():
    dec = ("op", "INT_ADD", (_loc("ctr0"), ("const", 0xFF, 1)), 1)
    cond = (
        "op",
        "INT_NOTEQUAL",
        (("op", "INT_AND", (dec, ("const", 0x80, 1)), 1), ("const", 0, 1)),
        1,
    )
    stmts = [
        ("asg", "ctr0", ("mem", ("const", 0x5513, 2), 1)),
        ("st", ("const", 0x5513, 2), dec),
        ("asg", "x", ("const", 2, 1)),
        ("if", "ifnot", cond, [], [("st", ("const", 0x5513, 2), ("mem", ("const", 0x5517, 2), 1))]),
    ]
    res = eqlift.lift_stmts(stmts)
    text = res.text
    assert "m_5513 = (m_5513 - $01)" in text
    assert "if (m_5513 >=s $00) {" in text
    assert "ctr0" not in text  # the copy is dead once extraction reads the cell
    assert eqlift.verify_lift(res) >= 2


def test_carry_chain_fuses_to_16bit_add():
    lo = ("op", "INT_ADD", (_loc("al"), _loc("bl")), 1)
    c = ("op", "INT_CARRY", (_loc("al"), _loc("bl")), 1)
    hi = ("op", "INT_ADD", (("op", "INT_ADD", (_loc("ah"), _loc("bh")), 1), c), 1)
    shifted = ("op", "INT_LEFT", (("op", "INT_ZEXT", (hi,), 2), ("const", 8, 1)), 2)
    split = ("op", "INT_OR", (shifted, ("op", "INT_ZEXT", (lo,), 2)), 2)
    res = eqlift.lift_stmts([("asg", "a", split)])
    (line,) = [ln for ln in res.lines if ln.strip().startswith("a = ")]
    assert "carry(" not in line and " + " in line  # extraction prefers the fused add
    assert res.stats["fired"].get("carry_fuse/w2", 0) >= 1
    assert eqlift.verify_lift(res) == 1


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_play_exhibit_end_to_end(sid, subtune, secs):
    """The exhibit must fall out of generic rules: no rule mentions any cell."""
    model = _model(sid, subtune, secs)
    before = frameprog.emit(model)
    res = eqlift.lift(model)
    assert "ctr_5513 = (ctr_5513 - $01)" in res.text
    assert "if (ctr_5513 >=s $00) {" in res.text
    assert "ctr0" not in res.text
    assert res.stats["copies_dropped"] >= 1
    assert eqlift.verify_lift(res) >= 50  # every rewritten site is Z3-proven
    assert frameprog.emit(model) == before  # the committed model is untouched


@pytest.mark.parametrize("sid,subtune,secs", _tune("Krakout", "Daglish_Ben"))
def test_krakout_whole_artifact_emission(sid, subtune, secs):
    """emit() covers calls/switches; deterministic; every proc's sites proven."""
    model = _model(sid, subtune, secs)
    text, results = eqlift.emit(model)
    assert text.startswith("eqlift 0\n") and "state {" in text
    assert "sub_E001 {" in text and "sub_E536 {" in text and "sub_E578 {" in text
    assert "switch call {" in text and "call $" in text
    assert "if (x0 != $01) {" in text  # ifnot(eq) lifts to a direct compare
    assert "vflag = (((a1 ^ $7F) & (a1 ^ t0)) <s $00)" in text
    assert eqlift.emit(model)[0] == text  # emission is deterministic
    assert sum(eqlift.verify_lift(r) for r in results) >= 50


def _regs():
    return [("reg", i) for i in range(16)]


def test_emit_synthetic_model_headers_and_proc():
    from deity_informant import sidprog
    from deity_informant.structured import Block

    events = [("ld", 0, ("const", 0x1500, 2)), ("st", ("const", 0x00FB, 2), ("uni", 0, 1))]
    blocks = {(0x1000, 0xA9): Block(0x1000, 0xA9, [0x1000], events, ("rts",), _regs())}
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xA9
    model = sidprog.TextModel(mem0, 0x0F00, 0x1000, blocks, {})
    text, results = eqlift.emit(model)
    assert text.startswith("eqlift 0\n")
    assert " m_1500: u8" in text and "sub_1000 {" in text
    assert "zp_FB = m_1500" in text  # the load's local dies into the store
    assert len(results) == 1 and eqlift.emit(model)[0] == text
