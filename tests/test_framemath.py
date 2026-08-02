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
    assert "d0:2 = (ctr_0010:2 + $0037):2" in text  # the widened byte step is a word const
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
def test_the_c64_world_cybertracker_half_goes_elsewhere(push):
    """`LDA $14/CLC/ADC #4/STA $14/LDA $15/ADC #0/PHA` reads $15 but stores elsewhere.

    The sources ARE one 16-bit quantity, so the arithmetic lifts; the destinations
    are not the lanes, so the two writes must stay apart (no u16 store).
    """
    a = G.Asm(G.ORG)
    a.i("LDA", "zp", 0x14).i("CLC").i("ADC", "imm", 0x04).i("STA", "zp", 0x14)
    a.i("LDA", "zp", 0x15).i("ADC", "imm", 0x00)
    a.i(*(("PHA",) if push else ("STA", "zp", 0x16)))
    a.i("LDA", "zp", 0x14).i("STA", "abs", OUT)
    a.i(*(("PLA",) if push else ("LDA", "zp", 0x16))).i("STA", "abs", OUT + 1)
    a.i("RTS")
    model, prog, text = _build("c64world", a, {0x14: 0xF0, 0x15: 0x20, 0x16: 0x00})
    (pr,) = _math(prog, "lifted")
    assert "carry(" not in text and "):2 + $0004):2" in text
    assert "one u16 store" not in pr.lemma  # the hi half never reaches $15
    assert "ctr_0014 = trunc1(d0:2)" in text
    dest = "sid.v1.attack_decay" if push else "zp_16"  # destacked, the slot is not a cell
    assert "%s = trunc1((d0:2 >> $08):2)" % dest in text
    assert frameval.gate_fp(model, 8, prog) is None


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


# ---- the premise, refused from real code ------------------------------------------
def _zp_indexed_lanes():
    """`zp,X` wraps inside the byte, so the add is under the index, not over a base."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 2).label("lp").i("CLC")
    a.i("LDA", "zpx", 0x20).i("ADC", "imm", 0x37).i("STA", "zpx", 0x20)
    a.i("LDA", "zpx", 0x21).i("ADC", "imm", 0x00).i("STA", "zpx", 0x21)
    a.i("LDA", "zpx", 0x20).i("STA", "abs", OUT)
    a.i("LDA", "zpx", 0x21).i("STA", "abs", OUT + 1)
    a.i("DEX").i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    return _build("zpx", a, {0x20 + k: 0xF0 + k for k in range(6)})


def _split_index_lanes():
    """One lane walked by X, the other by Y: two rows, not two halves of one row."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 1).i("LDY", "imm", 3).label("lp").i("CLC")
    a.i("LDA", "absx", SLO).i("ADC", "imm", 0x37).i("STA", "absx", SLO)
    a.i("LDA", "absy", SHI).i("ADC", "imm", 0x00).i("STA", "absy", SHI)
    a.i("LDA", "absx", SLO).i("STA", "absx", OUT)
    a.i("LDA", "absy", SHI).i("STA", "abs", OUT + 1)
    a.i("DEY").i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {SLO + k: 0x80 + k for k in range(4)} | {SHI + k: 0x10 + k for k in range(4)}
    return _build("xyidx", a, data, tuple(OUT + k for k in range(4)))


def _step_overwritten():
    """A 16-bit step whose hi byte is rewritten between the lanes: the read cannot move up."""
    a = G.Asm(G.ORG)
    a.i("CLC")
    a.i("LDA", "zp", LO).i("ADC", "abs", STEP).i("STA", "zp", LO)
    a.i("LDA", "imm", 0x09).i("STA", "abs", STEP + 1)
    a.i("LDA", "zp", HI).i("ADC", "abs", STEP + 1).i("STA", "zp", HI)
    _publish(a, LO, HI).i("RTS")
    return _build("clobber", a, {STEP: 0x37, STEP + 1: 0x02, LO: 0x10, HI: 0x80})


@pytest.mark.parametrize(
    "build,want",
    [
        (_zp_indexed_lanes, "a lane address is not a const base plus index"),
        (_split_index_lanes, "the two lanes are indexed differently"),
        (_step_overwritten, "an intervening statement changes an operand"),
    ],
)
def test_the_refusal_diagnostic_names_the_premise_that_failed(build, want):
    _m, prog, text = build()
    (pr,) = _math(prog)
    assert pr.status == "refused" and pr.lemma.endswith(want)
    assert "carry(" in text and "d0:2" not in text  # left as the two byte updates


def test_an_unresolved_lane_prints_as_unresolved_and_sites_at_zero():
    """With neither lane named, the record has nowhere to sit but the bottom."""
    (pr,) = _math(_zp_indexed_lanes()[1])
    assert "lanes unresolved" in pr.lemma and pr.site == 0 and pr.targets == (None, None)


def test_a_sixteen_bit_step_lifts_as_one_word_add():
    """Both addends 16 bits: the word the rules name need not be a widened byte."""
    a = G.Asm(G.ORG)
    a.i("CLC")
    a.i("LDA", "zp", LO).i("ADC", "abs", STEP).i("STA", "zp", LO)
    a.i("LDA", "zp", HI).i("ADC", "abs", STEP + 1).i("STA", "zp", HI)
    _publish(a, LO, HI).i("RTS")
    _m, prog, text = _build("word_step", a, {STEP: 0x37, STEP + 1: 0x02, LO: 0x10, HI: 0x80})
    (pr,) = _math(prog)
    assert pr.status == "lifted" and pr.targets == (LO, HI)
    assert "carry(" not in text
    assert "(zext2(m_1481) << $08):2 | zext2(m_1480)" in text


def test_lanes_three_bytes_apart_lift_but_never_merge():
    """For_Link's shape: a stride-3 lane pair is one quantity, not one word store.

    Merging `$1750,X`/`$1753,X` into a u16 store would write `$1750/$1751` — the
    wrong cells — so the arithmetic lifts and the two byte stores stay.
    """
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 2).label("lp")
    a.i("LDA", "absx", SLO).i("CLC").i("ADC", "imm", 0x37).i("STA", "absx", SLO)
    a.i("LDA", "absx", SLO + 3).i("ADC", "imm", 0x00).i("STA", "absx", SLO + 3)
    a.i("LDA", "absx", SLO).i("STA", "absx", OUT)
    a.i("LDA", "absx", SLO + 3).i("STA", "absx", OUT + 4)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {SLO + k: 0x80 + k for k in range(8)}
    _m, prog, text = _build("stride3", a, data, tuple(OUT + k for k in range(8)))
    (pr,) = _math(prog)
    assert pr.status == "lifted" and pr.targets == (SLO, SLO + 3)
    assert "one u16 store" not in pr.lemma
    assert "trunc1(d0:2)" in text and "trunc1((d0:2 >> $08):2)" in text
    assert ":2 = d0:2" not in text  # no merged word store across a 3-byte stride


# ---- a definition the statement list does not make ---------------------------------
def test_a_definition_in_a_branch_arm_is_not_the_value_before_the_branch():
    """Andy_Capp's ptr step: ``DEY`` under a ``BCS`` sign-extends the byte added.

    Reading Y as the ``LDY #$00`` that precedes the branch folds the extension away
    and adds $0100 too much, so the definition inside the arm must end the reign of
    the one before it."""
    a = G.Asm(G.ORG)
    a.i("SEC").i("LDA", "abs", STEP).i("SBC", "abs", STEP + 1)
    a.i("LDY", "imm", 0x00).i("BCS", "rel", ("L", "pos")).i("DEY").label("pos")
    a.i("CLC").i("ADC", "zp", LO).i("STA", "zp", LO)
    a.i("TYA").i("ADC", "zp", HI).i("STA", "zp", HI)
    _publish(a, LO, HI).i("RTS")
    _m, prog, text = _build("sext16", a, {STEP: 0x10, STEP + 1: 0x40, LO: 0x00, HI: 0x80})
    (pr,) = _math(prog)
    assert pr.status == "lifted" and pr.targets == (LO, HI)
    assert "((zext2(y) << $08):2 | zext2(a)):2" in text  # the step is a word, Y its hi byte


def test_a_sid_pair_naming_the_lifted_store_writes_the_whole_word_there():
    """Beat_the_System's shape: the pair's lo store is the lifted lo statement itself.

    Emitting the lane half there and dropping the pair's hi store leaves the hi SID
    register holding the previous frame's byte."""
    a = G.Asm(G.ORG)
    a.i("CLC")
    a.i("LDA", "zp", LO).i("ADC", "imm", 0x37).i("STA", "abs", G.SID).i("STA", "zp", LO)
    a.i("LDA", "zp", HI).i("ADC", "imm", 0x00).i("STA", "zp", HI).i("STA", "abs", G.SID + 1)
    a.i("RTS")
    _m, prog, text = _build("sidfirst16", a, {LO: 0xF0, HI: 0x01}, (G.SID, G.SID + 1))
    (pr,) = _math(prog)
    assert pr.status == "lifted" and "SID pair $D400" in pr.lemma
    assert [ln for ln in text.splitlines() if "sid.v1.freq" in ln] == ["  sid.v1.freq_lo:2 = d0:2"]
