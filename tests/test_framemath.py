"""Rung (d2), 16-bit arithmetic: the carry link lifted, the refusal and the law.

Covers docs/frameprog.md 4: a byte add/sub plus the carry (borrow) it propagates
into the next lane is one 16-bit update bound to a word local; a broken premise
refuses that site alone; a wrongly lifted site fails Gate FP.
"""

import pytest

from deity_informant import expr as E
from deity_informant import framefuse as FF
from deity_informant import framemath
from deity_informant import frameprog
from deity_informant import frameval
import _fuzzgen as G

from test_frameprog import _fuzz_model

LO, HI = 0x10, 0x11  # adjacent zero-page lanes, clear of the CPU port
SLO, SHI = G.TBL, G.TBL + 0x40  # the same index in two non-adjacent tables
OUT = 0xD404  # sid.v1.ctrl/attack_decay: observable and not a SID lo/hi pair
STEP = G.TBL + 0x80


def _build(name, asm, data=(), outs=(OUT, OUT + 1)):
    """``(model, program, text)`` for a synthetic player, gate and fixpoint checked."""
    player = G.Player(name, G.ORG, asm.assemble(), set(outs), {"indexed"}, data=dict(data))
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, 8, prog) is None
    assert frameprog.dumps(frameprog.loads(text)) == text
    return model, prog, text


def _math(prog, status=None):
    return [p for p in prog.proofs if p.kind == "math" and status in (None, p.status)]


def _publish(asm, lo, hi):
    """Make both lanes observable so the record separates a wrong word from a right one."""
    return asm.i("LDA", "zp", lo).i("STA", "abs", OUT).i("LDA", "zp", hi).i("STA", "abs", OUT + 1)


# ---- the carry chain is the evidence ---------------------------------------------
def _add_player():
    a = G.Asm(G.ORG)
    a.i("CLC")
    a.i("LDA", "zp", LO).i("ADC", "imm", 0x37).i("STA", "zp", LO)
    a.i("LDA", "zp", HI).i("ADC", "imm", 0x00).i("STA", "zp", HI)
    return _publish(a, LO, HI).i("RTS")


def test_the_classic_carry_chain_lifts_to_one_word_add():
    """CLC/ADC lo/ADC #0 hi over adjacent cells is one u16 add; the carry term goes."""
    _m, prog, text = _build("add16", _add_player())
    (pr,) = _math(prog)
    assert pr.status == "lifted" and pr.targets == (LO, HI)
    assert "16-bit add: lanes $0010/$0011, adjacent cells" in pr.lemma
    assert "d0:2 = (ctr_0010:2 + zext2($37)):2" in text
    assert "carry(" not in text


def test_the_borrow_chain_lifts_to_one_word_sub():
    """SEC/SBC lo/SBC #0 hi is the same site with the borrow as the link."""
    a = G.Asm(G.ORG)
    a.i("SEC")
    a.i("LDA", "zp", LO).i("SBC", "abs", STEP).i("STA", "zp", LO)
    a.i("LDA", "zp", HI).i("SBC", "imm", 0x00).i("STA", "zp", HI)
    _publish(a, LO, HI).i("RTS")
    _m, prog, text = _build("sub16", a, {STEP: 0x37, LO: 0x10, HI: 0x80})
    (pr,) = _math(prog)
    assert pr.status == "lifted" and pr.targets == (LO, HI)
    assert "16-bit sub: lanes $0010/$0011, adjacent cells" in pr.lemma
    assert "d0:2 = (zp_10:2 - zext2(m_1480)):2" in text
    assert "<=" not in text  # the borrow predicate is gone with the byte form


def test_the_borrow_chain_lifts_when_the_step_is_an_immediate():
    """An immediate step is a const:1 in the SBC and a const:2 in the borrow compare."""
    a = G.Asm(G.ORG)
    a.i("SEC")
    a.i("LDA", "zp", LO).i("SBC", "imm", 0x37).i("STA", "zp", LO)
    a.i("LDA", "zp", HI).i("SBC", "imm", 0x00).i("STA", "zp", HI)
    _publish(a, LO, HI).i("RTS")
    _m, prog, _text = _build("sub16imm", a, {LO: 0x10, HI: 0x80})
    assert _math(prog, "lifted")


def _split_player():
    """The Commando slide shape: one index, two lanes in non-adjacent tables."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 3).label("lp").i("CLC")
    a.i("LDA", "absx", SLO).i("ADC", "imm", 0x37).i("STA", "absx", SLO)
    a.i("LDA", "absx", SHI).i("ADC", "imm", 0x00).i("STA", "absx", SHI)
    a.i("LDA", "absx", SLO).i("STA", "absx", OUT)
    a.i("LDA", "absx", SHI).i("STA", "absx", OUT + 4)
    return a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")


_SPLIT_DATA = {SLO + k: 0x80 + k for k in range(4)} | {SHI + k: 0x10 + k for k in range(4)}
_SPLIT_OUTS = tuple(OUT + k for k in range(8))


def _split():
    return _build("split16", _split_player(), _SPLIT_DATA, _SPLIT_OUTS)


def test_lanes_in_two_tables_lift_without_merging_the_stores():
    """Split lanes: the word is packed from the two rows and truncated back per lane."""
    _m, prog, text = _split()
    (pr,) = _math(prog)
    assert pr.status == "lifted" and pr.targets == (SLO, SHI)
    assert "16-bit add: lanes $1400/$1440, split tables" in pr.lemma
    assert "one u16 store" not in pr.lemma
    assert "(zext2(m_1440[x]) << $08):2 | zext2(" in text
    assert "ctr_1400[x] = trunc1(d0:2)" in text
    assert "m_1440[x] = trunc1((d0:2 >> $08):2)" in text


def test_the_sid_pair_fuses_off_the_lifted_word():
    """The two half results also reach $D400/$D401: one u16 SID store off the word."""
    a = G.Asm(G.ORG)
    a.i("CLC")
    a.i("LDA", "zp", LO).i("ADC", "imm", 0x37).i("STA", "zp", LO).i("STA", "abs", G.SID)
    a.i("LDA", "zp", HI).i("ADC", "imm", 0x00).i("STA", "zp", HI).i("STA", "abs", G.SID + 1)
    a.i("RTS")
    _m, prog, text = _build("sidpair16", a, {}, (G.SID, G.SID + 1))
    (pr,) = _math(prog)
    assert pr.status == "lifted" and "SID pair $D400" in pr.lemma
    assert [ln for ln in text.splitlines() if "sid.v1.freq" in ln] == ["  sid.v1.freq_lo:2 = d0:2"]


# ---- the refusal is per site -----------------------------------------------------
def test_an_intervening_write_to_the_hi_lane_refuses_the_site():
    """The lift moves the hi load up; a store into that lane first forbids it."""
    a = G.Asm(G.ORG)
    a.i("LDX", "zp", HI).i("CLC")
    a.i("LDA", "zp", LO).i("ADC", "imm", 0x37).i("STA", "zp", LO)
    a.i("LDA", "imm", 0x05).i("STA", "zp", HI)
    a.i("TXA").i("ADC", "imm", 0x00).i("STA", "zp", HI)
    _publish(a, LO, HI).i("RTS")
    _m, prog, text = _build("hilane", a)
    (pr,) = _math(prog)
    assert pr.status == "refused" and pr.targets == (LO, HI)
    assert "an intervening statement writes the hi lane" in pr.lemma
    assert ":2 = " not in text and "carry(" in text  # the site is left as two byte updates


@pytest.mark.parametrize("push", [True, False])
def test_the_c64_world_cybertracker_high_half_is_not_a_lane(push):
    """`LDA $14/CLC/ADC #4/STA $14/LDA $15/ADC #0/PHA` reads $15 but never stores it.

    The two cells are not one 16-bit variable: fusing them computes the right
    value through the wrong cell, so the source cell must be the destination cell.
    """
    a = G.Asm(G.ORG)
    a.i("LDA", "zp", 0x14).i("CLC").i("ADC", "imm", 0x04).i("STA", "zp", 0x14)
    a.i("LDA", "zp", 0x15).i("ADC", "imm", 0x00)
    a.i(*(("PHA",) if push else ("STA", "zp", 0x16)))
    a.i("LDA", "zp", 0x14).i("STA", "abs", OUT)
    a.i(*(("PLA",) if push else ("LDA", "zp", 0x16))).i("STA", "abs", OUT + 1)
    a.i("RTS")
    _m, prog, text = _build("c64world", a, {0x14: 0xF0, 0x15: 0x20, 0x16: 0x00})
    assert not _math(prog, "lifted")
    assert "carry(" in text and ":2 = " not in text


# ---- mutation evidence: a wrongly lifted site moves the record --------------------
_D0 = ("loc", "d0", 2)


def _find(prog, pred):
    """``(statement list, index)`` of the first statement satisfying ``pred``."""
    for lst in framemath._bodies(prog.procs[0][3]):
        for k, s in enumerate(lst):
            if pred(s):
                return lst, k
    raise AssertionError("no such statement")


def test_swapping_the_packed_halves_of_a_lifted_word_moves_the_record():
    """The proof is load-bearing: pack the word hi/lo the wrong way and Gate FP fails."""
    model, prog, _text = _split()
    trace, _walker = frameprog.iota(model, 8)
    good = frameval.eval_fp(prog, trace, 8)
    lst, i = _find(prog, lambda s: s[0] == "asg" and s[1] == "d0")
    word = lst[i][2]
    lo, hi = FF.unpack(word[2][0])
    lst[i] = ("asg", "d0", (word[0], word[1], (FF._pack(hi, lo), word[2][1]), word[3]))
    assert frameval.eval_fp(prog, trace, 8) != good
    assert frameval.gate_fp(model, 8, prog) is not None


def test_dropping_the_truncation_off_the_hi_lane_store_moves_the_record():
    """The hi lane takes the word's high byte: store the word itself and the byte changes."""
    model, prog, _text = _split()
    trace, _walker = frameprog.iota(model, 8)
    good = frameval.eval_fp(prog, trace, 8)
    lst, i = _find(prog, lambda s: s[0] == "st" and s[2] == framemath._hi_byte(_D0))
    lst[i] = ("st", lst[i][1], _D0)
    assert frameval.eval_fp(prog, trace, 8) != good


# ---- the premise, stated ----------------------------------------------------------
def _cell(addr):
    return ("mem", ("const", addr, 2), 1)


def _byte_op(name, args):
    return ("op", name, tuple(args), 1)


def _at(base, name):
    return ("op", "INT_ADD", (("const", base, 2), ("op", "INT_ZEXT", (("loc", name),), 2)), 2)


def _chain(lo_addr, hi_addr, lo_val, step, hi_val=None):
    """The two lane stores of one carry chain, addressed as given."""
    hi_val = hi_val or ("mem", hi_addr, 1)
    return [
        ("st", lo_addr, _byte_op("INT_ADD", (lo_val, step))),
        ("st", hi_addr, _byte_op("INT_ADD", (hi_val, _byte_op("INT_CARRY", (lo_val, step))))),
    ]


def _diagnose(lst, env=None):
    """The pass's own matcher and premise over the site ``lst[0]`` opens."""
    j, site, parts = framemath._match(lst, 0, dict(env or {}))
    inner = {s[1]: s[2] for s in lst[1:j] if s[0] == "asg"}
    parts = tuple(None if p is None else framemath._inline(p, inner) for p in parts)
    span = 0 if parts[3] is None else E.mask(FF._w(parts[3]))
    site.why = site.why or framemath._premise(lst, 0, j, parts, site, span)
    return site


def _unresolved_lane():
    addr = ("loc", "p", 2)
    return _chain(addr, ("const", HI, 2), ("mem", addr, 1), ("const", 0x37, 1)), {}


def _mismatched_index():
    lo, hi = _at(SLO, "x"), _at(SHI, "y")
    return _chain(lo, hi, ("mem", lo, 1), ("const", 0x37, 1)), {}


def _operand_clobbered():
    step = _cell(STEP)
    lst = _chain(("const", LO, 2), ("const", HI, 2), _cell(LO), step)
    lst.insert(1, ("st", ("const", STEP, 2), ("const", 9, 1)))
    return lst, {}


def _hi_lane_written():
    lst = _chain(("const", LO, 2), ("const", HI, 2), _cell(LO), ("const", 0x37, 1), ("loc", "w"))
    lst.insert(1, ("st", ("const", HI, 2), ("const", 5, 1)))
    return lst, {"w": _cell(HI)}


def _clean():
    return _chain(("const", LO, 2), ("const", HI, 2), _cell(LO), ("const", 0x37, 1)), {}


@pytest.mark.parametrize(
    "build,want",
    [
        (_unresolved_lane, "a lane address is not a const base plus index"),
        (_mismatched_index, "the two lanes are indexed differently"),
        (_operand_clobbered, "an intervening statement changes an operand"),
        (_hi_lane_written, "an intervening statement writes the hi lane"),
        (_clean, None),
    ],
)
def test_the_refusal_diagnostic_names_the_premise_that_failed(build, want):
    lst, env = build()
    site = _diagnose(lst, env)
    assert site.why is None if want is None else site.why == want
    pr = site.proof()
    assert pr.kind == "math" and pr.status == ("lifted" if want is None else "refused")
    assert pr.lemma.endswith(want or "carry chain")
    assert pr.targets == (site.lo, site.hi)


def test_an_unresolved_lane_prints_as_unresolved_and_sites_at_zero():
    site = _diagnose(*_unresolved_lane())
    assert "lanes unresolved" in site.proof().lemma and site.proof().site == 0


def test_a_zero_carry_term_is_not_a_carry_link():
    """`carry(x, $00)` is identically 0 and cannot stand as the chain's evidence."""
    assert framemath._carry_over(_byte_op("INT_CARRY", (_cell(LO), ("const", 0, 1))), {}) is None
    assert framemath._carry_over(_byte_op("INT_CARRY", (_cell(LO), ("const", 1, 1))), {}) == (
        _cell(LO),
        ("const", 1, 1),
    )
